"""社區歸屬與完整門牌推斷（docs/06 §3–5、docs/16）。

規則分數不是機率；calibrated_probability 一律不提供。
"""

import hashlib
import re

from . import RULESET_VERSION
from . import registry as reg
from .db import j, now, unj
from .events import emit
from .normalize import area_readings, normalize_address, parse_orientation

NAME_RX = re.compile(r"聯上世界")
NEAR_RX = re.compile(r"(近|鄰|旁)聯上世界|聯上世界(旁|附近|週邊|周邊)")
AREA_TOL, AREA_TIGHT = 1.0, 0.3
MAX_ENUM = 40
BUILDING_UNITS_PER_FLOOR = {"A": 4, "B": 4, "C": 4, "D": 5}  # 平面圖直接觀察（9/11/13F）


def _hit(code, effect, detail, decisive=False):
    return {"rule_code": code, "effect": effect, "decisive": decisive, "detail": detail, "evidence_ids": []}


# ---------------------------------------------------------------- 社區歸屬（06 §3）
def membership(conn, snap):
    hits, flags = [], []
    numbers = {u["number"]: u for u in reg.units(conn)}
    addr = normalize_address(snap["address_text"])
    same_road = addr.road == reg.ROAD and addr.section in (reg.SECTION, None)
    dec_support = dec_refute = False
    if addr.precision in ("NUMBER", "FLOOR"):
        if not same_road and addr.road:
            hits.append(_hit("N1", None, f"明寫門牌路段為 {addr.road}", True))
            dec_refute = True
        elif addr.number in numbers:
            verified = conn.execute(
                "SELECT m.record_status FROM registry_mapping m JOIN address_unit au ON au.id=m.to_id WHERE au.number=?",
                (addr.number,)).fetchone()["record_status"] == "VERIFIED"
            if verified:
                hits.append(_hit("M1", None, f"{addr.number}號在名冊（mapping VERIFIED）", True))
                dec_support = True
            else:
                hits.append(_hit("M1u", 0.6, f"{addr.number}號在名冊（mapping 未驗證）"))
        elif same_road:
            flags.append("REGISTRY_GAP")  # N5：涵蓋度不是 COMPLETE，不扣分
    elif addr.precision in ("ROAD", "LANE") and same_road:
        hits.append(_hit("M4", 0.2, "路段為高鐵南路一段"))
    if snap["platform_community_ref"]:
        hits.append(_hit("M2", 0.5, f"平台社區標記 {snap['platform_community_ref']}"))
    text = " ".join(x for x in (snap["title"], snap["community_text"]) if x)
    if NEAR_RX.search(text):
        hits.append(_hit("N4", -0.3, "「近聯上世界」等字樣"))
    elif NAME_RX.search(text):
        hits.append(_hit("M3", 0.2, "標題或社區欄位出現聯上世界"))
    if snap["map_address"]:
        flags.append("MAP_ADDRESS_IGNORED")  # Q-029 建議預設：地圖定位欄不觸發 N1
    mem_ev = conn.execute("SELECT id, value FROM evidence WHERE listing_id=? AND kind='INFERENCE' AND status='ACTIVE'"
                          " AND field='membership.text' ORDER BY id DESC LIMIT 1", (snap["listing_id"],)).fetchone()
    if mem_ev and isinstance(unj(mem_ev["value"], {}).get("answer"), (int, float)):
        p = float(unj(mem_ev["value"])["answer"])
        hits.append({"rule_code": "M9m", "effect": round(0.2 * (2 * p - 1), 3), "decisive": False, "evidence_ids": [mem_ev["id"]],
                     "detail": f"決策模型：文字描述就是聯上世界的機率 {p:.0%}"})
    score = max(0.0, min(1.0, sum(h["effect"] or 0 for h in hits)))
    if dec_support and dec_refute:
        outcome = "CONFLICTING"
    elif dec_refute:
        outcome = "NON_MEMBER"
    elif dec_support:
        outcome = "MEMBER"
    elif score >= 0.8:
        outcome = "MEMBER"
    elif score >= 0.5:
        outcome = "LIKELY_MEMBER"
    else:
        outcome = "UNDETERMINED"
    return {"outcome": outcome, "rule_score": round(score, 3), "rule_hits": hits, "flags": flags}


# ---------------------------------------------------------------- 完整門牌（06 §4–5、16）
def _area_shortlist(units, snap, source_incl):
    """雙口徑試算：回傳 (每個戶別的最佳距離與口徑, flags)。"""
    parking_included = snap["parking_included"]
    if parking_included == "UNKNOWN" and source_incl is not None:
        parking_included = "INCLUDED" if source_incl else "EXCLUDED"
    readings = area_readings(snap["building_ping"], snap["parking_ping"], parking_included)
    per_reading = []
    for basis, ping, inferred in readings:
        matched = {}
        for u in units:
            if u["area_ping_min"] is None:
                matched[u["id"]] = (None, basis, ping, inferred)  # 坪數未知：不能用坪數排除
                continue
            d = max(0.0, u["area_ping_min"] - ping, ping - u["area_ping_max"])
            if d <= AREA_TOL:
                matched[u["id"]] = (round(d, 2), basis, ping, inferred)
        per_reading.append((basis, ping, inferred, matched))
    hit_readings = [r for r in per_reading if any(v[0] is not None for v in r[3].values())]
    flags = []
    if len(hit_readings) > 1:
        flags.append("AREA_BASIS_AMBIGUOUS")
    elif len(hit_readings) == 1 and (hit_readings[0][2] or len(readings) > 1):
        flags.append("AREA_BASIS_INFERRED")
    best = {}
    for _, _, _, matched in hit_readings:
        for uid, v in matched.items():
            if uid not in best or (v[0] is not None and (best[uid][0] is None or v[0] < best[uid][0])):
                best[uid] = v
    return best, flags, readings


def resolve_address(conn, snap, member_outcome, human=None):
    units = reg.units(conn)
    flags, registry_items = [], []
    if member_outcome not in ("MEMBER", "LIKELY_MEMBER"):
        return _result("NOT_EVALUATED", [], flags, _fields_unknown(), registry_items, 0, {})
    src = conn.execute(
        "SELECT s.area_includes_parking FROM source s JOIN listing l ON l.source_id=s.id WHERE l.id=?", (snap["listing_id"],)
    ).fetchone()
    source_incl = src["area_includes_parking"] if src else None
    addr = normalize_address(snap["address_text"])
    orient = parse_orientation(snap["orientation"])
    has_floor = snap["floor_min"] is not None
    addr_floor = int(addr.floor) if addr.precision == "FLOOR" and str(addr.floor).isdigit() else None  # G2：明寫門牌的樓層
    area_best, area_flags, readings = _area_shortlist(units, snap, source_incl)
    flags += area_flags
    narrowing = has_floor or readings or snap["sales_unit_text"] or addr.precision in ("NUMBER", "FLOOR") or snap["units_per_floor"]
    if not narrowing:
        return _result("INSUFFICIENT_INFO", [], flags, _fields_unknown(), registry_items, 0, {"reason": "NO_NARROWING_FIELDS"})
    shortlist = set(area_best) if area_best else None
    from .decide import model_hits
    from .decide import candidate_hits
    mhits, mflags = model_hits(conn, snap["listing_id"])
    chits = candidate_hits(conn, snap["listing_id"])
    if chits and "MODEL_INFERENCE_USED" not in mflags:
        mflags.append("MODEL_INFERENCE_USED")
    mhits = mhits + chits
    flags += mflags
    cands = []
    for u in units:
        floors_by_rule = reg.unit_floors(conn, u["id"])
        if snap["floor_exact"]:
            floors = [snap["floor_min"]]
        elif not has_floor and addr_floor is not None:
            floors = [addr_floor]
        elif has_floor:
            floors = list(range(snap["floor_min"], snap["floor_max"] + 1))
        else:
            floors = list(floors_by_rule)
        for f in floors:
            hits, score, excluded, by = [], 0.0, False, None
            if snap["sales_unit_text"] and snap["sales_unit_text"].upper() != u["code"]:
                excluded, by = True, "X4"
            if not excluded and (u["code"], f) in (human or {}).get("rejected", set()):
                excluded, by = True, "HUMAN_REJECT"
            if not excluded and addr.precision == "FLOOR":
                same = addr.number == u["number"] and addr.floor == str(f)
                if not same:
                    if reg.derivation_verified(conn, u["id"], f):
                        excluded, by = True, "X5"
                    else:
                        hits.append(_hit("X5s", -0.3, "明寫門牌不同（推導未驗證，軟反駁）"))
                else:
                    hits.append(_hit("S1", 0.6, "明寫門牌一致"))
            if not excluded and f not in floors_by_rule:
                hits.append(_hit("X7s", -0.3, f"{f}F 沒有任何推導規則涵蓋此戶別"))
            if not excluded and shortlist is not None and u["id"] not in shortlist:
                excluded, by = True, "G5"
            if not excluded:
                if u["id"] in area_best and area_best[u["id"]][0] is not None:
                    d, basis, ping, inferred = area_best[u["id"]]
                    eff = 0.15 if d <= AREA_TIGHT else 0.05
                    if "AREA_BASIS_INFERRED" in flags or "AREA_BASIS_AMBIGUOUS" in flags:
                        eff /= 2
                    hits.append(_hit("S3", eff, f"坪數 {ping}（{basis}）與坪型距離 {d}"))
                if snap["sales_unit_text"]:
                    hits.append(_hit("S2", 0.4, "明寫戶別一致"))
                if snap["rooms"] and u["ut_rooms"]:
                    hits.append(_hit("S4", 0.1, "房數一致") if snap["rooms"] == u["ut_rooms"] else _hit("S4", -0.2, "房數不一致"))
                facades = unj(u["facades"], [])
                if orient and facades:
                    hits.append(_hit("S5", 0.1, f"落地窗 {orient} 在此戶立面") if orient in facades
                                else _hit("S5", -0.2, f"落地窗 {orient} 不在此戶立面 {facades}"))
                if snap["units_per_floor"]:
                    exp = BUILDING_UNITS_PER_FLOOR.get(u["building_label"])
                    hits.append(_hit("S12", 0.1, "該層戶數符合棟別") if exp == snap["units_per_floor"]
                                else _hit("S12", -0.2, f"該層戶數 {snap['units_per_floor']} ≠ {exp}"))
                if snap["floor_exact"]:
                    hits.append(_hit("S7", 0.1, "樓層一致"))
                for ev in (human or {}).get("features", []):
                    if ev.get("sales_unit") == u["code"]:
                        hits.append(_hit("S9" if ev.get("match") else "S10", 0.1 if ev.get("match") else -0.3, "照片格局特徵"))
                for fn in mhits:  # 決策模型（推論）：只加減分，不排除
                    h = fn(u["code"], f)
                    if h:
                        hits.append(h)
                score = max(0.0, min(1.0, sum(h["effect"] or 0 for h in hits)))
            cands.append({"sales_unit": u["code"], "sales_unit_id": u["id"], "number": u["number"], "floor": f,
                          "score": round(score, 3), "excluded": excluded, "excluded_by_rule": by, "rule_hits": hits})
            registry_items.append({"ref_type": "REGISTRY_MAPPING", "id": u["id"], "version": 1})
    live = [c for c in cands if not c["excluded"]]
    live.sort(key=lambda c: (-c["score"], c["sales_unit"], c["floor"]))
    fields = _fields(live)
    if human and human.get("sufficient"):
        fields = _apply_human(fields, human)
    else:
        _system_confirm(conn, fields, live, snap, set(flags))
    extra = {"area_readings": [{"basis": b, "ping": p, "inferred": i} for b, p, i in readings],
             "shortlist_units": sorted({c["sales_unit"] for c in live})}
    if fields["full_address"]["status"] == "CONFIRMED":
        outcome = "CONFIRMED"
    elif len(live) > MAX_ENUM:
        outcome = "INSUFFICIENT_INFO"
    elif not live:
        outcome = "NO_CANDIDATE"
    elif len(live) == 1:
        outcome = "SINGLE_CANDIDATE"
    else:
        outcome = "MULTIPLE_CANDIDATES"
    if human and human.get("abstain") and outcome != "CONFIRMED":
        outcome = "ABSTAINED"
    return _result(outcome, cands, flags, fields, registry_items, len(live), extra)


def _fields_unknown():
    return {k: {"status": "UNKNOWN", "value": None, "basis": None, "evidence_ids": []}
            for k in ("building", "sales_unit", "floor", "address_number", "full_address")}


def _fields(live):
    f = _fields_unknown()
    if not live:
        return f
    def agg(values):
        vals = sorted(set(values), key=lambda v: (isinstance(v, str), v))
        return {"status": "NARROWED", "value": vals[0], "basis": None, "evidence_ids": []} if len(vals) == 1 else \
               {"status": "AMBIGUOUS", "value": None, "basis": None, "evidence_ids": [], "candidates": vals[:20]}
    f["building"] = agg(c["sales_unit"][0] for c in live)
    f["sales_unit"] = agg(c["sales_unit"] for c in live)
    f["floor"] = agg(c["floor"] for c in live)
    if all(c["number"] is not None for c in live):  # mapping 被撤回的戶別沒有號碼 → 門牌欄位維持 UNKNOWN
        f["address_number"] = agg(f"高鐵南路一段{c['number']}號" for c in live)
        f["full_address"] = agg(f"高鐵南路一段{c['number']}號{c['floor']}樓" for c in live)
    return f


def _system_confirm(conn, fields, live, snap, flags):
    """06 §5.1 K1–K5：只有單一候選、推導已驗證、此刊登自己的決定性事實、沒有反駁時，系統才確認完整門牌。"""
    if len(live) != 1 or flags & {"MAPPING_DISPUTE", "AREA_BASIS_AMBIGUOUS"}:
        return False
    c = live[0]
    if c["number"] is None or not reg.derivation_verified(conn, c["sales_unit_id"], c["floor"]):  # K2
        return False
    codes = {h["rule_code"] for h in c["rule_hits"]}
    decisive = "S1" in codes or ("S2" in codes and snap["floor_exact"])  # K3
    if not decisive or any((h["effect"] or 0) < 0 for h in c["rule_hits"]):  # K4
        return False
    full = f"高鐵南路一段{c['number']}號{c['floor']}樓"
    for key, value in (("full_address", full), ("address_number", f"高鐵南路一段{c['number']}號"), ("floor", c["floor"])):
        fields[key] = {"status": "CONFIRMED", "value": value, "basis": "SYSTEM_RULES", "evidence_ids": []}
    return True


def _apply_human(fields, human):
    addr = human["full_address"]
    ev = human["evidence_ids"]
    fields["full_address"] = {"status": "CONFIRMED", "value": addr, "basis": "HUMAN_REVIEW", "evidence_ids": ev}
    m = re.search(r"(\d+)號(\d+)樓", addr)
    if m:
        fields["address_number"] = {"status": "CONFIRMED", "value": f"高鐵南路一段{m.group(1)}號", "basis": "HUMAN_REVIEW", "evidence_ids": ev}
        fields["floor"] = {"status": "CONFIRMED", "value": int(m.group(2)), "basis": "HUMAN_REVIEW", "evidence_ids": ev}
    return fields  # sales_unit、building 不因門牌確認而變成 CONFIRMED（06 §5.2）


def _result(outcome, cands, flags, fields, registry_items, count, extra):
    return {"outcome": outcome, "candidates": cands, "flags": sorted(set(flags)), "resolved_fields": fields,
            "registry_items_used": registry_items[:50], "candidate_count": count, "extra": extra}


# ---------------------------------------------------------------- 寫入評估
def _store(conn, subject_type, listing_id, snapshot_id, outcome, rule_score, basis, fields, flags, hits, count,
           summary, trigger, registry_items, evidence_ids, candidates=None):
    """寫入評估。輸入與結果都和目前的評估相同時，沿用目前的評估（不新增紀錄、不發事件）。回傳 (id, prev, changed, reused)。"""
    rv = reg.current_version(conn)
    ev_hash = "sha256:" + hashlib.sha256(j(sorted(evidence_ids)).encode()).hexdigest()[:16]
    prev = conn.execute(
        "SELECT * FROM assessment WHERE subject_type=? AND listing_id=? AND status='CURRENT'", (subject_type, listing_id)
    ).fetchone()
    cand_sig = [(c["sales_unit"], c["floor"], c["score"], c["excluded"], c["excluded_by_rule"], c["rule_hits"]) for c in candidates or []]
    sig = "sha256:" + hashlib.sha256(j([snapshot_id, RULESET_VERSION, rv, ev_hash, outcome, rule_score, basis, fields, flags, hits,
                                        count, cand_sig]).encode()).hexdigest()[:16]
    if prev and (unj(prev["summary"], {}) or {}).get("_sig") == sig:
        return prev["id"], prev, False, True
    summary = {**(summary or {}), "_sig": sig}
    aid = conn.execute(
        "INSERT INTO assessment (subject_type, listing_id, snapshot_id, ruleset_version, registry_version_id, evidence_set_hash,"
        " outcome, rule_score, basis, resolved_fields, registry_items_used, flags, rule_hits, candidate_count, summary, trigger,"
        " created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (subject_type, listing_id, snapshot_id, RULESET_VERSION, rv, ev_hash, outcome, rule_score, basis,
         j(fields) if fields else None, j(registry_items), j(flags), j(hits), count, j(summary), trigger, now()),
    ).lastrowid
    for eid in evidence_ids:
        ver = conn.execute("SELECT version FROM evidence WHERE id=?", (eid,)).fetchone()
        conn.execute("INSERT OR IGNORE INTO assessment_evidence VALUES (?,?,?)", (aid, eid, ver["version"] if ver else 1))
    changed = True
    if prev:
        conn.execute("UPDATE assessment SET status='SUPERSEDED', superseded_by_id=? WHERE id=?", (aid, prev["id"]))
        changed = (prev["outcome"] != outcome or prev["resolved_fields"] != (j(fields) if fields else None))
    return aid, prev, changed, False


def assess_listing(conn, listing_id, trigger="NEW_SNAPSHOT"):
    from . import review as rv_mod
    snap = conn.execute(
        "SELECT * FROM listing_snapshot WHERE listing_id=? ORDER BY id DESC LIMIT 1", (listing_id,)
    ).fetchone()
    lst = conn.execute("SELECT * FROM listing WHERE id=?", (listing_id,)).fetchone()
    ev_ids = [r["id"] for r in conn.execute("SELECT id FROM evidence WHERE listing_id=? AND status='ACTIVE'", (listing_id,))]
    mem = membership(conn, snap)
    decided = rv_mod.membership_decision(conn, listing_id)
    if decided:
        mem["rule_hits"].append({"rule_code": "HUMAN_REVIEW", "effect": None, "decisive": True, "evidence_ids": decided["evidence_ids"],
                                 "detail": f"人工覆核 #{decided['review_id']}：{decided['outcome']}"})
        mem["outcome"] = decided["outcome"]
    m_basis = "HUMAN_REVIEW" if decided else "SYSTEM_RULES"
    m_id, m_prev, m_changed, _ = _store(conn, "COMMUNITY_MEMBERSHIP", listing_id, snap["id"], mem["outcome"], mem["rule_score"],
                                     m_basis, None, mem["flags"], mem["rule_hits"], None, {}, trigger, [], ev_ids)
    if m_changed:
        emit(conn, "COMMUNITY_MEMBERSHIP_CHANGED", f"community_membership_changed:{m_id}",
             {"listing_id": listing_id, "property_id": lst["property_id"], "source_id": lst["source_id"]},
             {"assessment_id": m_id, "previous_assessment_id": m_prev["id"] if m_prev else None,
              "old_status": m_prev["outcome"] if m_prev else None, "new_status": mem["outcome"],
              "rule_score": mem["rule_score"], "basis": m_basis},
             caused_by={"snapshot_id": snap["id"], "assessment_id": m_id})
    human = rv_mod.human_support(conn, listing_id)
    addr = resolve_address(conn, snap, mem["outcome"], human)
    basis = addr["resolved_fields"]["full_address"]["basis"] or "SYSTEM_RULES" if addr["outcome"] == "CONFIRMED" else "SYSTEM_RULES"
    a_id, a_prev, a_changed, a_reused = _store(conn, "ADDRESS_RESOLUTION", listing_id, snap["id"], addr["outcome"], None, basis,
                                               addr["resolved_fields"], addr["flags"], [], addr["candidate_count"], addr["extra"],
                                               trigger, addr["registry_items_used"], ev_ids, addr["candidates"])
    for c in ([] if a_reused else addr["candidates"]):
        did = reg.ensure_dwelling(conn, c["sales_unit_id"], c["floor"])
        cid = conn.execute("SELECT id FROM candidate WHERE listing_id=? AND dwelling_id=?", (listing_id, did)).fetchone()
        cid = cid["id"] if cid else conn.execute(
            "INSERT INTO candidate (listing_id, dwelling_id, generated_by) VALUES (?,?,'G2')", (listing_id, did)).lastrowid
        if not c["excluded"] or c["excluded_by_rule"] != "G5":
            conn.execute("INSERT INTO assessment_candidate VALUES (?,?,?,?,?,?)",
                         (a_id, cid, c["score"], int(c["excluded"]), c["excluded_by_rule"], j(c["rule_hits"])))
    if a_changed:
        emit(conn, "ADDRESS_ASSESSMENT_CHANGED", f"address_assessment_changed:{a_id}",
             {"listing_id": listing_id, "property_id": lst["property_id"], "source_id": lst["source_id"]},
             {"assessment_id": a_id, "previous_assessment_id": a_prev["id"] if a_prev else None,
              "old_status": a_prev["outcome"] if a_prev else None, "new_status": addr["outcome"],
              "full_address_status": addr["resolved_fields"]["full_address"]["status"],
              "candidate_count": addr["candidate_count"], "top_candidate_id": None, "basis": basis, "trigger": trigger},
             caused_by={"snapshot_id": snap["id"], "assessment_id": a_id})
    rv_mod.route_tasks(conn, listing_id, mem, addr, m_id, a_id)
    conn.commit()
    return {"membership": mem, "address": addr, "membership_assessment_id": m_id, "address_assessment_id": a_id}
