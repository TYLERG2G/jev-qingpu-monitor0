"""同屋辨識與合併治理（docs/13、ADR-004、ADR-007）。

相似特徵只產生配對候選；自動合併需要 A1–A4（實務上要兩邊都 CONFIRMED 而且來源獨立）。
"""

from itertools import combinations

from . import RULESET_VERSION
from .db import j, now, unj
from .events import emit


def _current(conn, subject, listing_id):
    return conn.execute("SELECT * FROM assessment WHERE subject_type=? AND listing_id=? AND status='CURRENT'",
                        (subject, listing_id)).fetchone()


def _cand_set(conn, assessment_id):
    rows = conn.execute(
        "SELECT su.code, d.floor FROM assessment_candidate ac JOIN candidate c ON c.id=ac.candidate_id"
        " JOIN dwelling d ON d.id=c.dwelling_id JOIN sales_unit su ON su.id=d.sales_unit_id"
        " WHERE ac.assessment_id=? AND ac.excluded=0", (assessment_id,)).fetchall()
    return {(r["code"], r["floor"]) for r in rows}


def _snap(conn, listing_id):
    return conn.execute("SELECT * FROM listing_snapshot WHERE listing_id=? ORDER BY id DESC LIMIT 1", (listing_id,)).fetchone()


def score_pair(conn, a, b):
    """回傳 (rule_score, rule_hits, conflicts, intersection)。"""
    sa, sb = _snap(conn, a), _snap(conn, b)
    aa, ab = _current(conn, "ADDRESS_RESOLUTION", a), _current(conn, "ADDRESS_RESOLUTION", b)
    inter = _cand_set(conn, aa["id"]) & _cand_set(conn, ab["id"])
    hits, conflicts = [], []
    if len(inter) == 1:
        hits.append({"rule_code": "P1", "effect": 0.4, "evidence_ids": []})
    if sa["building_ping"] and sb["building_ping"]:
        d = abs(sa["building_ping"] - sb["building_ping"])
        if d <= 0.1:
            hits.append({"rule_code": "P2", "effect": 0.2, "evidence_ids": []})
        elif d <= 0.5:
            hits.append({"rule_code": "P2", "effect": 0.1, "evidence_ids": []})
        elif (sa["parking_ping"] or 0) == (sb["parking_ping"] or 0):
            conflicts.append("F2")
    if sa["floor_exact"] and sb["floor_exact"]:
        if sa["floor_min"] == sb["floor_min"]:
            hits.append({"rule_code": "P3", "effect": 0.2, "evidence_ids": []})
        else:
            conflicts.append("F1")
    if sa["rooms"] and sb["rooms"]:
        if sa["rooms"] == sb["rooms"]:
            hits.append({"rule_code": "P4", "effect": 0.1, "evidence_ids": []})
        else:
            conflicts.append("F3")
    if sa["orientation"] and sb["orientation"]:
        if sa["orientation"] == sb["orientation"]:
            hits.append({"rule_code": "P5", "effect": 0.1, "evidence_ids": []})
        else:
            conflicts.append("F4")
    if sa["sales_unit_text"] and sb["sales_unit_text"] and sa["sales_unit_text"] != sb["sales_unit_text"]:
        conflicts.append("F5")
    if "CONFLICTING" in (aa["outcome"], ab["outcome"]):
        conflicts.append("F6")
    lo, hi = sorted((a, b))
    ev = conn.execute("SELECT id, value FROM evidence WHERE listing_id=? AND kind='INFERENCE' AND status='ACTIVE' AND field='identity.same'"
                      " ORDER BY id DESC", (lo,)).fetchall()
    for row in ev:
        v = unj(row["value"], {})
        if v.get("other_listing_id") == hi and isinstance(v.get("answer"), (int, float)):
            p = float(v["answer"])
            hits.append({"rule_code": "P9m", "effect": round(0.2 * (2 * p - 1), 3), "evidence_ids": [row["id"]],
                         "detail": f"決策模型：同一戶的機率 {p:.0%}"})
            break
    la = conn.execute("SELECT lister_company FROM listing WHERE id=?", (a,)).fetchone()["lister_company"]
    lb = conn.execute("SELECT lister_company FROM listing WHERE id=?", (b,)).fetchone()["lister_company"]
    if la and la == lb:
        hits.append({"rule_code": "P7", "effect": 0.2, "evidence_ids": []})
    score = round(max(0.0, min(1.0, sum(h["effect"] for h in hits))), 3)
    return score, hits, sorted(set(conflicts)), inter


def auto_merge_eligible(conn, a, b, conflicts):
    """A1–A4：兩邊 MEMBER、兩邊 CONFIRMED 指向同一住宅、來源 INDEPENDENT、沒有衝突。"""
    if conflicts:
        return False
    for lid in (a, b):
        if _current(conn, "COMMUNITY_MEMBERSHIP", lid)["outcome"] != "MEMBER":
            return False
        if _current(conn, "ADDRESS_RESOLUTION", lid)["outcome"] != "CONFIRMED":
            return False
    fa = unj(_current(conn, "ADDRESS_RESOLUTION", a)["resolved_fields"])["full_address"]
    fb = unj(_current(conn, "ADDRESS_RESOLUTION", b)["resolved_fields"])["full_address"]
    if fa["value"] != fb["value"]:
        return False
    return origins_independent(conn, fa["evidence_ids"], fb["evidence_ids"])


def _origins(conn, evidence_ids):
    out = set()
    for eid in evidence_ids:
        row = conn.execute("SELECT origin_ids FROM evidence WHERE id=?", (eid,)).fetchone()
        out |= set(unj(row["origin_ids"], [])) if row else set()
    return out


def origins_independent(conn, ev_a, ev_b):
    """05 §5.4：共享根來源 → 不獨立；否則每一對根來源都要有 ACTIVE 的 INDEPENDENT 關係紀錄（D-011：沒有正面佐證就是 UNKNOWN）。"""
    oa, ob = _origins(conn, ev_a), _origins(conn, ev_b)
    if not oa or not ob or oa & ob:
        return False
    for x in oa:
        for y in ob:
            lo, hi = sorted((x, y))
            rel = conn.execute("SELECT relation FROM origin_relation WHERE origin_a_id=? AND origin_b_id=? AND status='ACTIVE'",
                               (lo, hi)).fetchone()
            if not rel or rel["relation"] != "INDEPENDENT":
                return False
    return True


def evaluate_pairs(conn):
    members = [r["listing_id"] for r in conn.execute(
        "SELECT listing_id FROM assessment WHERE subject_type='COMMUNITY_MEMBERSHIP' AND status='CURRENT'"
        " AND outcome IN ('MEMBER','LIKELY_MEMBER') ORDER BY listing_id")]
    out = []
    for a, b in combinations(members, 2):
        pa = conn.execute("SELECT property_id FROM listing WHERE id=?", (a,)).fetchone()[0]
        pb = conn.execute("SELECT property_id FROM listing WHERE id=?", (b,)).fetchone()[0]
        if pa == pb:
            continue
        score, hits, conflicts, inter = score_pair(conn, a, b)
        if not inter:
            continue
        prev = conn.execute("SELECT * FROM identity_match WHERE listing_a_id=? AND listing_b_id=?", (a, b)).fetchone()
        if prev and prev["status"] in ("REJECTED",):
            conflicts = sorted(set(conflicts) | {"F7"})
        if auto_merge_eligible(conn, a, b, conflicts):
            outcome = "AUTO_MERGE_ELIGIBLE"
        elif conflicts:
            outcome = "CONFLICTING"
        elif score >= 0.5:
            outcome = "REVIEW_REQUIRED"
        else:
            outcome = "CANDIDATE_ONLY"
        if prev:
            mid = prev["id"]
            if prev["conflicts"] != j(conflicts):
                conn.execute("UPDATE identity_match SET conflicts=?, version=version+1 WHERE id=?", (j(conflicts), mid))
        else:
            mid = conn.execute("INSERT INTO identity_match (listing_a_id, listing_b_id, status, conflicts) VALUES (?,?, 'CANDIDATE', ?)",
                               (a, b, j(conflicts))).lastrowid
        cur = conn.execute("SELECT * FROM assessment WHERE subject_type='LISTING_MATCH' AND identity_match_id=? AND status='CURRENT'",
                           (mid,)).fetchone()
        same = cur is not None and (cur["outcome"], cur["rule_score"], cur["flags"], cur["rule_hits"]) == (outcome, score, j(conflicts), j(hits))
        if same:  # 結果沒變：沿用目前的評估，不新增紀錄
            aid = cur["id"]
        else:
            aid = conn.execute(
                "INSERT INTO assessment (subject_type, identity_match_id, ruleset_version, registry_version_id, evidence_set_hash, outcome,"
                " rule_score, basis, flags, rule_hits, summary, trigger, created_at) VALUES ('LISTING_MATCH',?,?,?,?,?,?, 'SYSTEM_RULES', ?, ?, ?, 'MANUAL', ?)",
                (mid, RULESET_VERSION, 1, "sha256:-", outcome, score, j(conflicts), j(hits), j({"intersection": len(inter)}), now())).lastrowid
            conn.execute("UPDATE assessment SET status='SUPERSEDED', superseded_by_id=? WHERE subject_type='LISTING_MATCH'"
                         " AND identity_match_id=? AND status='CURRENT' AND id<>?", (aid, mid, aid))
            conn.execute("UPDATE identity_match SET current_assessment_id=? WHERE id=?", (aid, mid))
        last = conn.execute("SELECT a.outcome FROM review_task t JOIN assessment a ON a.id=t.base_assessment_id"
                            " WHERE t.identity_match_id=? AND t.status='RESOLVED' ORDER BY t.id DESC LIMIT 1", (mid,)).fetchone()
        decided = (prev is not None and prev["status"] == "REJECTED") or (last is not None and last["outcome"] == outcome)
        if not decided and (outcome == "REVIEW_REQUIRED" or (outcome == "CONFLICTING" and score >= 0.5)):
            if not conn.execute("SELECT 1 FROM review_task WHERE task_type='LISTING_MATCH' AND identity_match_id=? AND status='OPEN'", (mid,)).fetchone():
                conn.execute("INSERT INTO review_task (task_type, identity_match_id, base_assessment_id, created_at) VALUES ('LISTING_MATCH',?,?,?)",
                             (mid, aid, now()))
        out.append({"identity_match_id": mid, "listing_a_id": a, "listing_b_id": b, "score": score, "outcome": outcome,
                    "conflicts": conflicts, "intersection": len(inter)})
    conn.commit()
    return out


def _members(conn, pid):
    return [r["listing_id"] for r in conn.execute(
        "SELECT listing_id FROM property_membership WHERE property_id=? AND valid_to IS NULL ORDER BY listing_id", (pid,))]


def recompute_property(conn, pid):
    rows = conn.execute(
        "SELECT l.status, l.first_observed_at, s.total_price FROM listing l JOIN property_membership pm ON pm.listing_id=l.id"
        " LEFT JOIN listing_snapshot s ON s.id=(SELECT MAX(id) FROM listing_snapshot WHERE listing_id=l.id)"
        " WHERE pm.property_id=? AND pm.valid_to IS NULL", (pid,)).fetchall()
    active = [r for r in rows if r["status"] == "ACTIVE"]
    conn.execute("UPDATE property SET status=?, current_min_price=?, active_listing_count=?, first_seen_at=COALESCE(?, first_seen_at),"
                 " version=version+1 WHERE id=?",
                 ("ACTIVE" if active else "INACTIVE", min((r["total_price"] for r in active), default=None), len(active),
                  min((r["first_observed_at"] for r in rows), default=None), pid))


def merge(conn, property_ids, rationale, evidence_ids, op_type="MANUAL_MERGE", identity_match_id=None):
    """合併 property。PROPERTY_MERGED 只保存、不通知；只有真正新增的刊登發 DWELLING_NEW_LISTING（每則刊登最多一次）。"""
    if op_type == "MANUAL_MERGE" and not evidence_ids:
        raise ValueError("人工合併必須附佐證")
    if len(rationale or "") < 5:
        raise ValueError("rationale 至少 5 個字")
    into = min(property_ids)
    ts = now()
    op = conn.execute("INSERT INTO merge_operation (op_type, actor, rationale, ruleset_version, identity_match_id, created_at)"
                      " VALUES (?,?,?,?,?,?)", (op_type, "USER" if op_type == "MANUAL_MERGE" else "SYSTEM", rationale,
                                                RULESET_VERSION, identity_match_id, ts)).lastrowid
    conn.execute("UPDATE merge_operation SET evidence_ids=? WHERE id=?", (j(sorted(evidence_ids or [])), op))
    moved = []
    for pid in property_ids:
        if pid == into:
            continue
        for lid in _members(conn, pid):
            conn.execute("UPDATE property_membership SET valid_to=? WHERE listing_id=? AND valid_to IS NULL", (ts, lid))
            conn.execute("INSERT INTO property_membership (property_id, listing_id, valid_from, operation_id) VALUES (?,?,?,?)", (into, lid, ts, op))
            conn.execute("UPDATE listing SET property_id=? WHERE id=?", (into, lid))
            conn.execute("INSERT INTO merge_operation_listing VALUES (?,?,?,?)", (op, lid, pid, into))
            moved.append(lid)
        recompute_property(conn, pid)
    recompute_property(conn, into)
    members = _members(conn, into)
    info = {r["id"]: r for r in conn.execute(
        f"SELECT * FROM listing WHERE id IN ({','.join('?' * len(members))})", members)}
    initial = [lid for lid in members if info[lid]["first_observed_context"] in ("BASELINE", "MANUAL_INITIAL")]
    new_ids = []
    for lid in members:
        l = info[lid]
        if l["new_listing_status"] != "CONFIRMED_NEW":
            continue
        earlier = [o for o in members if o != lid and (info[o]["first_observed_at"], o) < (l["first_observed_at"], lid)]
        if not earlier:
            continue
        anchor = min(earlier, key=lambda o: (info[o]["first_observed_at"], o))
        price = conn.execute("SELECT total_price FROM listing_snapshot WHERE listing_id=? ORDER BY id DESC LIMIT 1", (lid,)).fetchone()
        if emit(conn, "DWELLING_NEW_LISTING", f"dwelling_new_listing:{lid}",
                {"listing_id": lid, "property_id": into, "source_id": l["source_id"]},
                {"anchor_listing_id": anchor, "other_listing_ids": [o for o in members if o != lid],
                 "joining_listing_context": l["first_observed_context"],
                 "new_listing_status": "CONFIRMED_NEW", "trigger": "MERGE", "trigger_operation_id": op,
                 "property_was_inactive": False, "total_price": price["total_price"] if price else None},
                caused_by={"operation_id": op}):
            new_ids.append(lid)
    emit(conn, "PROPERTY_MERGED", f"property_merged:{op}", {"property_id": into},
         {"operation_id": op, "op_type": op_type, "into_property_id": into,
          "merged_property_ids": [p for p in property_ids if p != into], "listing_ids": members,
          "initial_context_listing_ids": initial, "new_listing_ids": new_ids, "identity_match_id": identity_match_id},
         caused_by={"operation_id": op})
    if identity_match_id:
        conn.execute("UPDATE identity_match SET status=?, version=version+1 WHERE id=?",
                     ("AUTO_MERGED" if op_type == "AUTO_MERGE" else "CONFIRMED", identity_match_id))
    from .notify import corrections_for
    corrections_for(conn, members, f"property_merged:{op}")
    conn.commit()
    return op


def split(conn, property_id, groups, rationale, evidence_ids=None):
    if len(groups) < 2 or any(not g for g in groups):
        raise ValueError("拆分至少要兩組，每組至少一則刊登")
    flat = [lid for g in groups for lid in g]
    if len(flat) != len(set(flat)) or sorted(flat) != sorted(_members(conn, property_id)):
        raise ValueError("SPLIT_GROUPS_MISMATCH：各組必須剛好涵蓋這個 property 的所有刊登")
    if len(rationale or "") < 5:
        raise ValueError("rationale 至少 5 個字")
    ts = now()
    op = conn.execute("INSERT INTO merge_operation (op_type, actor, rationale, ruleset_version, created_at, evidence_ids)"
                      " VALUES ('SPLIT','USER',?,?,?,?)", (rationale, RULESET_VERSION, ts, j(sorted(evidence_ids or [])))).lastrowid
    cid = conn.execute("SELECT community_id FROM property WHERE id=?", (property_id,)).fetchone()[0]
    out_groups = [{"property_id": property_id, "listing_ids": groups[0]}]
    for g in groups[1:]:
        pid = conn.execute("INSERT INTO property (community_id, first_seen_at) VALUES (?,?)", (cid, ts)).lastrowid
        for lid in g:
            conn.execute("UPDATE property_membership SET valid_to=? WHERE listing_id=? AND valid_to IS NULL", (ts, lid))
            conn.execute("INSERT INTO property_membership (property_id, listing_id, valid_from, operation_id) VALUES (?,?,?,?)", (pid, lid, ts, op))
            conn.execute("UPDATE listing SET property_id=? WHERE id=?", (pid, lid))
            conn.execute("INSERT INTO merge_operation_listing VALUES (?,?,?,?)", (op, lid, property_id, pid))
        recompute_property(conn, pid)
        out_groups.append({"property_id": pid, "listing_ids": g})
    recompute_property(conn, property_id)
    for g1, g2 in combinations(groups, 2):
        for a in g1:
            for b in g2:
                lo, hi = sorted((a, b))
                conn.execute("INSERT OR IGNORE INTO identity_match (listing_a_id, listing_b_id, status, conflicts) VALUES (?,?, 'REJECTED', '[\"F7\"]')",
                             (lo, hi))
                conn.execute("UPDATE identity_match SET status='REJECTED', version=version+1 WHERE listing_a_id=? AND listing_b_id=?", (lo, hi))
    emit(conn, "PROPERTY_SPLIT", f"property_split:{op}", {"property_id": property_id}, {"operation_id": op, "groups": out_groups},
         caused_by={"operation_id": op})
    from .notify import corrections_for
    corrections_for(conn, [lid for g in groups for lid in g], f"property_split:{op}")
    conn.commit()
    return op


def revert(conn, operation_id, rationale):
    """撤回合併：還原受影響刊登的成員關係；之後有依賴這次操作的操作時拒絕（DEPENDENT_OPERATIONS）。"""
    op = conn.execute("SELECT * FROM merge_operation WHERE id=?", (operation_id,)).fetchone()
    if not op or op["op_type"] not in ("MANUAL_MERGE", "AUTO_MERGE"):
        raise ValueError("只能撤回合併操作")
    if conn.execute("SELECT 1 FROM merge_operation WHERE reverts_operation_id=?", (operation_id,)).fetchone():
        raise ValueError("ALREADY_DONE")
    moved = conn.execute("SELECT * FROM merge_operation_listing WHERE operation_id=?", (operation_id,)).fetchall()
    props = sorted({m["to_property_id"] for m in moved} | {m["from_property_id"] for m in moved})
    later = conn.execute(
        f"SELECT DISTINCT operation_id FROM merge_operation_listing WHERE operation_id > ? AND (listing_id IN ({','.join('?' * len(moved))})"
        f" OR to_property_id IN ({','.join('?' * len(props))}) OR from_property_id IN ({','.join('?' * len(props))}))"
        " AND operation_id NOT IN (SELECT id FROM merge_operation WHERE op_type='REVERT' AND reverts_operation_id=?)",
        [operation_id] + [m["listing_id"] for m in moved] + props + props + [operation_id]).fetchall()
    if later:
        raise ValueError("DEPENDENT_OPERATIONS:" + ",".join(str(r[0]) for r in later))
    ts = now()
    rop = conn.execute("INSERT INTO merge_operation (op_type, actor, rationale, ruleset_version, reverts_operation_id, created_at)"
                       " VALUES ('REVERT','USER',?,?,?,?)", (rationale, RULESET_VERSION, operation_id, ts)).lastrowid
    restored = []
    for m in moved:
        lid, back = m["listing_id"], m["from_property_id"]
        conn.execute("UPDATE property_membership SET valid_to=? WHERE listing_id=? AND valid_to IS NULL", (ts, lid))
        conn.execute("INSERT INTO property_membership (property_id, listing_id, valid_from, operation_id) VALUES (?,?,?,?)", (back, lid, ts, rop))
        conn.execute("UPDATE listing SET property_id=? WHERE id=?", (back, lid))
        conn.execute("INSERT INTO merge_operation_listing VALUES (?,?,?,?)", (rop, lid, m["to_property_id"], back))
        recompute_property(conn, back)
        restored.append({"listing_id": lid, "property_id": back})
    recompute_property(conn, moved[0]["to_property_id"])
    if op["identity_match_id"]:
        conn.execute("UPDATE identity_match SET status='REVERTED', version=version+1 WHERE id=?", (op["identity_match_id"],))
    emit(conn, "MERGE_REVERTED", f"merge_reverted:{rop}", {"property_id": moved[0]["to_property_id"]},
         {"operation_id": rop, "reverted_operation_id": operation_id, "restored": restored}, caused_by={"operation_id": rop})
    from .notify import corrections_for
    corrections_for(conn, [m["listing_id"] for m in moved], f"merge_reverted:{rop}")
    conn.commit()
    return rop


def add_origin_relation(conn, origin_a_id, origin_b_id, relation, rationale, evidence_ids=None):
    """人工判定兩個根來源的關係（D-011）。INDEPENDENT 必須附佐證；同一對只保留一筆 ACTIVE。"""
    if relation not in ("SAME", "DEPENDENT", "INDEPENDENT"):
        raise ValueError("relation 必須是 SAME、DEPENDENT 或 INDEPENDENT")
    if relation == "INDEPENDENT" and not evidence_ids:
        raise ValueError("判定 INDEPENDENT 必須附佐證")
    if len(rationale or "") < 5:
        raise ValueError("rationale 至少 5 個字")
    lo, hi = sorted((origin_a_id, origin_b_id))
    if lo == hi:
        raise ValueError("兩個根來源不能相同")
    for oid in (lo, hi):
        if not conn.execute("SELECT 1 FROM source_origin WHERE id=?", (oid,)).fetchone():
            raise LookupError(f"找不到根來源 {oid}")
    conn.execute("UPDATE origin_relation SET status='RETRACTED' WHERE origin_a_id=? AND origin_b_id=? AND status='ACTIVE'", (lo, hi))
    rid = conn.execute("INSERT INTO origin_relation (origin_a_id, origin_b_id, relation, basis, rationale, evidence_ids, created_at)"
                       " VALUES (?,?,?,'HUMAN_REVIEW',?,?,?)", (lo, hi, relation, rationale, j(sorted(evidence_ids or [])), now())).lastrowid
    conn.commit()
    return rid


def retract_origin_relation(conn, relation_id):
    if not conn.execute("SELECT 1 FROM origin_relation WHERE id=?", (relation_id,)).fetchone():
        raise LookupError("找不到來源關係")
    conn.execute("UPDATE origin_relation SET status='RETRACTED' WHERE id=?", (relation_id,))
    conn.commit()
