"""社區總覽：把 JEV 對一個社區知道的事（名冊、刊登、行情、缺口）彙整成一份，並算出下一步該做什麼。

只讀資料庫，不寫入、不連網。數字都是規則與資料的彙整，不是機率。
"""

import re
from statistics import median

from . import registry as reg
from .db import unj


def _units(conn):
    rows = conn.execute(
        "SELECT su.id, su.code, su.building_label, su.block_position, su.facades, ut.area_ping_min, ut.area_ping_max, ut.rooms,"
        " ut.record_status AS area_status, ut.layout_fingerprint, au.number, m.record_status AS mapping_status"
        " FROM sales_unit su JOIN unit_type ut ON ut.id=su.unit_type_id"
        " JOIN registry_mapping m ON m.from_id=su.id AND m.mapping_type='SALES_UNIT_TO_NUMBER' JOIN address_unit au ON au.id=m.to_id"
        " ORDER BY su.building_label, su.code").fetchall()
    out = []
    for r in rows:
        fp = unj(r["layout_fingerprint"], {}) or {}
        out.append({"id": r["id"], "code": r["code"], "building": r["building_label"], "block_position": r["block_position"],
                    "facades": unj(r["facades"], []), "number": r["number"], "mapping_status": r["mapping_status"],
                    "area_min": r["area_ping_min"], "area_max": r["area_ping_max"], "rooms": r["rooms"],
                    "area_source": fp.get("area_source"), "mirror_pair": fp.get("mirror_pair") or "",
                    "floors": sorted(reg.unit_floors(conn, r["id"])), "listings": []})
    return out


def _listings(conn):
    rows = conn.execute(
        "SELECT l.id, l.external_id, l.status, l.property_id, l.first_observed_at, s.code AS source, sn.total_price, sn.building_ping,"
        " sn.parking_ping, sn.parking_included, sn.floor_text, sn.floor_min, sn.floor_exact, sn.rooms, sn.title,"
        " m.outcome AS membership, a.outcome AS address, a.candidate_count, a.resolved_fields, a.id AS aid"
        " FROM listing l JOIN source s ON s.id=l.source_id"
        " JOIN listing_snapshot sn ON sn.id=(SELECT MAX(id) FROM listing_snapshot WHERE listing_id=l.id)"
        " LEFT JOIN assessment m ON m.listing_id=l.id AND m.subject_type='COMMUNITY_MEMBERSHIP' AND m.status='CURRENT'"
        " LEFT JOIN assessment a ON a.listing_id=l.id AND a.subject_type='ADDRESS_RESOLUTION' AND a.status='CURRENT'"
        " ORDER BY l.id").fetchall()
    out = []
    for r in rows:
        f = unj(r["resolved_fields"], {}) or {}
        su = f.get("sales_unit", {})
        units = [su["value"]] if su.get("status") in ("NARROWED", "CONFIRMED") else list(su.get("candidates", []))
        ping_ex = None
        if r["building_ping"]:
            ping_ex = r["building_ping"] - (r["parking_ping"] or 0) if r["parking_included"] == "INCLUDED" else r["building_ping"]
        tops = conn.execute(
            "SELECT su.code, au.number, d.floor, ac.rule_score FROM assessment_candidate ac JOIN candidate c ON c.id=ac.candidate_id"
            " JOIN dwelling d ON d.id=c.dwelling_id JOIN sales_unit su ON su.id=d.sales_unit_id"
            " JOIN registry_mapping rm ON rm.from_id=su.id AND rm.mapping_type='SALES_UNIT_TO_NUMBER' JOIN address_unit au ON au.id=rm.to_id"
            " WHERE ac.assessment_id=? AND ac.excluded=0 ORDER BY ac.rule_score DESC LIMIT 2", (r["aid"],)).fetchall() if r["aid"] else []
        out.append({"id": r["id"], "source": r["source"], "external_id": r["external_id"], "status": r["status"],
                    "property_id": r["property_id"], "total_price": r["total_price"], "building_ping": r["building_ping"],
                    "ping_ex_parking": round(ping_ex, 2) if ping_ex else None, "floor_text": r["floor_text"],
                    "floor_known": r["floor_min"] is not None, "membership": r["membership"], "address": r["address"],
                    "candidate_count": r["candidate_count"] or 0, "units": units,
                    "unit_status": su.get("status"), "full_address_status": f.get("full_address", {}).get("status"),
                    "top_candidates": [{"unit": t["code"], "number": t["number"], "floor": t["floor"], "rule_score": t["rule_score"]} for t in tops],
                    "title": r["title"]})
    return out


def _market(listings, units):
    """依坪型分組的開價行情（只算上架中、屬於社區的刊登；同屋群組只算一次最低價）。"""
    groups = {u["code"]: "／".join(sorted({u["code"], *re.findall(r"[A-D]\d", u["mirror_pair"])})) for u in units}
    by_group, seen = {}, {}
    for l in listings:
        if l["status"] != "ACTIVE" or l["membership"] not in ("MEMBER", "LIKELY_MEMBER") or not l["units"] or not l["total_price"]:
            continue
        g = groups.get(l["units"][0], l["units"][0])
        prev = seen.get((g, l["property_id"]))
        if prev and prev["total_price"] <= l["total_price"]:
            continue
        seen[(g, l["property_id"])] = l
    for (g, _), l in seen.items():
        by_group.setdefault(g, []).append(l)
    out = []
    for g, ls in sorted(by_group.items()):
        prices = [l["total_price"] for l in ls]
        unit_prices = [l["total_price"] / l["ping_ex_parking"] / 10000 for l in ls if l["ping_ex_parking"]]
        out.append({"group": g, "count": len(ls), "min_price": min(prices), "max_price": max(prices), "median_price": median(prices),
                    "median_unit_price_wan": round(median(unit_prices), 1) if unit_prices else None,
                    "listing_ids": [l["id"] for l in ls]})
    return out


def _transactions(conn, units):
    rows = conn.execute("SELECT * FROM lvr_transaction ORDER BY trade_date DESC").fetchall()
    by_number = {}
    for r in rows:
        by_number.setdefault(r["number"], []).append(r)
    out = []
    for u in units:
        ts = by_number.get(u["number"], [])
        pings = sorted({t["ping_ex_parking"] for t in ts if t["ping_ex_parking"]})
        out.append({"unit": u["code"], "number": u["number"], "count": len(ts), "floors": sorted({t["floor"] for t in ts if t["floor"]}),
                    "pings": pings, "same_area_across_floors": (max(pings) - min(pings) <= 0.5) if len(pings) > 1 else None,
                    "latest": ({"date": ts[0]["trade_date"], "floor": ts[0]["floor"], "total_price": ts[0]["total_price"]} if ts else None)})
    return {"total": len(rows), "by_unit": out}


def build(conn):
    units = _units(conn)
    listings = _listings(conn)
    by_code = {u["code"]: u for u in units}
    for l in listings:
        for code in l["units"]:
            if code in by_code:
                by_code[code]["listings"].append({"id": l["id"], "external_id": l["external_id"], "status": l["status"],
                                                   "total_price": l["total_price"], "floor_text": l["floor_text"],
                                                   "shared_with": [c for c in l["units"] if c != code]})
    for u in units:
        act = [x for x in u["listings"] if x["status"] == "ACTIVE"]
        u["active_count"] = len(act)
        u["exclusive_count"] = sum(1 for x in act if not x["shared_with"])
        u["min_price"] = min((x["total_price"] for x in act), default=None)
    member = [l for l in listings if l["membership"] in ("MEMBER", "LIKELY_MEMBER")]
    active = [l for l in member if l["status"] == "ACTIVE"]
    groups = conn.execute("SELECT COUNT(*) FROM (SELECT property_id FROM listing GROUP BY property_id HAVING COUNT(*) > 1)").fetchone()[0]
    tasks = {r[0]: r[1] for r in conn.execute("SELECT task_type, COUNT(*) FROM review_task WHERE status='OPEN' GROUP BY task_type")}
    rules = [{"code": r["code"], "floors": f"{r['floor_min']}–{r['floor_max']}", "status": r["record_status"]}
             for r in conn.execute("SELECT * FROM address_derivation_rule ORDER BY id")]
    summary = {
        "listings": len(listings), "active": sum(1 for l in listings if l["status"] == "ACTIVE"), "member": len(member),
        "floor_known": sum(1 for l in member if l["floor_known"]),
        "unit_unique": sum(1 for l in member if len(l["units"]) == 1),
        "within_two": sum(1 for l in member if 0 < l["candidate_count"] <= 2),
        "confirmed": sum(1 for l in member if l["full_address_status"] == "CONFIRMED"),
        "groups": groups, "open_tasks": sum(v for k, v in tasks.items() if k not in ("REGISTRY_MAPPING", "DERIVATION_RULE")),
        "mapping_verified": sum(1 for u in units if u["mapping_status"] == "VERIFIED"), "units": len(units),
        "dwellings_estimate": sum(len(u["floors"]) for u in units),
    }
    tx = _transactions(conn, units)
    return {"summary": summary, "units": units, "listings": listings, "market": _market(listings, units), "rules": rules,
            "transactions": tx, "gaps": _gaps(summary, units, listings, rules, tx, tasks), "tasks": tasks}


def _gaps(summary, units, listings, rules, tx, tasks):
    """缺口與下一步：依「對門牌準確度的幫助」排序。每一項都附上可以直接做的動作。"""
    out = []
    no_floor = [l for l in listings if l["membership"] in ("MEMBER", "LIKELY_MEMBER") and l["status"] == "ACTIVE" and not l["floor_known"]]
    if no_floor:
        out.append({"priority": 1, "title": f"{len(no_floor)} 則刊登沒有樓層",
                    "why": "坪數只能決定戶型；有樓層才能縮小到 1–2 個完整門牌",
                    "action": "用「591 → JEV」書籤重新匯入這些刊登；頁面有樓層就會自動縮小",
                    "listing_ids": [l["id"] for l in no_floor]})
    mirror = [l for l in listings if l["status"] == "ACTIVE" and len(l["units"]) == 2 and l["floor_known"]]
    if mirror:
        out.append({"priority": 2, "title": f"{len(mirror)} 則刊登卡在鏡像戶（只剩 2 個門牌）",
                    "why": "鏡像戶坪數相同，只能靠照片判斷格局方向或窗景",
                    "action": "在刊登頁按「請 JEV 判斷」上傳室內照片（需啟用決策模型），或自己比對照片後在覆核頁排除其中一戶",
                    "listing_ids": [l["id"] for l in mirror]})
    unverified = [u["code"] for u in units if u["mapping_status"] != "VERIFIED"]
    if unverified:
        out.append({"priority": 3, "title": f"戶別→門牌號對照 {len(unverified)}／{len(units)} 組未驗證",
                    "why": "對照表只來自 591／樂居示意圖（疑似同源）；未驗證時系統不會自行確認門牌",
                    "action": "取得管委會門牌對照表或現場門牌照片後，到覆核頁（名冊任務）驗證"})
    hyp = [r["code"] for r in rules if r["status"] in ("HYPOTHETICAL", "TRANSCRIBED_UNVERIFIED")]
    if hyp:
        out.append({"priority": 4, "title": f"門牌推導規則未驗證：{'、'.join(hyp)}",
                    "why": "「同一戶別各樓層共用同一個號」目前只是推論",
                    "action": ("匯入多季官方實價登錄（python -m jev lvr 檔案 --import），檢查同號各樓層坪數是否一致"
                               if tx["total"] < 10 else "實價登錄已顯示同號跨樓層坪數一致的戶別，可據此在覆核頁驗證規則")})
    unknown_area = [u["code"] for u in units if u["area_source"] != "OBSERVED"]
    if unknown_area:
        out.append({"priority": 5, "title": f"坪數未觀測的戶別：{'、'.join(unknown_area)}",
                    "why": "這些戶別借用鏡像戶或沒有坪數，比對可能不準",
                    "action": "從實價登錄或樂居銷控表補上坪數"})
    undetermined = [l for l in listings if l["membership"] in ("UNDETERMINED", "CONFLICTING")]
    if undetermined:
        out.append({"priority": 6, "title": f"{len(undetermined)} 則刊登無法判定是否屬於聯上世界",
                    "why": "只有社區名稱與路段，缺少平台社區編號",
                    "action": "重新匯入並補上 591 社區編號（591:9488），或在覆核頁確認",
                    "listing_ids": [l["id"] for l in undetermined]})
    if tasks.get("ADDRESS_CANDIDATE") or tasks.get("LISTING_MATCH"):
        out.append({"priority": 7, "title": f"待覆核：門牌 {tasks.get('ADDRESS_CANDIDATE', 0)} 件、同屋 {tasks.get('LISTING_MATCH', 0)} 件",
                    "why": "系統已縮小範圍，剩下的需要你的判斷或佐證", "action": "到「覆核」分頁處理"})
    return out
