"""覆核：分流、送出決定（含 If-Match 版本檢查）、佐證充分性 H1–H6、撤回（docs/06 §5.2–§7）。"""

import hashlib

from .db import j, now, unj
from .normalize import normalize_address

DIRECT_DERIVATIONS = {"ORIGINAL", "TRANSCRIPTION", "OCR"}
STRONG_LINKS = {"AGENT_VIEWING_WITH_LISTING_REF", "DOCUMENT_CITES_LISTING", "INTERIOR_FEATURE_MATCH"}
CONFIRM_DECISIONS = {"CONFIRM_CANDIDATE", "SET_UNIT", "CONFIRM_MEMBER", "CONFIRM_NON_MEMBER", "CONFIRM_SAME", "VERIFY_MAPPING", "VERIFY_RULE"}
ALLOWED = {
    "COMMUNITY_MEMBERSHIP": {"CONFIRM_MEMBER", "CONFIRM_NON_MEMBER", "ABSTAIN"},
    "ADDRESS_CANDIDATE": {"CONFIRM_CANDIDATE", "REJECT_CANDIDATE", "SET_UNIT", "MARK_INSUFFICIENT", "ABSTAIN"},
    "LISTING_MATCH": {"CONFIRM_SAME", "REJECT_SAME", "ABSTAIN"},
    "REGISTRY_MAPPING": {"VERIFY_MAPPING", "REJECT_MAPPING", "ABSTAIN"},
    "DERIVATION_RULE": {"VERIFY_RULE", "REJECT_RULE", "ABSTAIN"},
}


class ReviewError(Exception):
    def __init__(self, code, message, status=422):
        super().__init__(message)
        self.code, self.status = code, status


def _system_origin(conn):
    row = conn.execute("SELECT id FROM source_origin WHERE origin_type='SYSTEM'").fetchone()
    if row:
        return row["id"]
    return conn.execute("INSERT INTO source_origin (origin_type, asserting_party_type, description) VALUES"
                        " ('SYSTEM','SYSTEM','系統推論')").lastrowid


def _origins_of(conn, refs):
    out = set()
    for ref in refs:
        if ref["ref_type"] == "EVIDENCE":
            row = conn.execute("SELECT origin_ids FROM evidence WHERE id=?", (ref["id"],)).fetchone()
            if row:
                out |= set(unj(row["origin_ids"], []))
        elif ref["ref_type"] in ("ASSESSMENT", "CANDIDATE", "IDENTITY_MATCH"):
            out.add(_system_origin(conn))
    return out


def add_user_evidence(conn, listing_id, item, review_id=None):
    """建立使用者佐證（HUMAN_ASSERTION）。ORIGINAL 必須有 capture；其他衍生方式必須有具型別 derived_from。"""
    derivation = item.get("derivation_type", "ORIGINAL")
    derived = item.get("derived_from") or []
    if derivation == "ORIGINAL":
        if not item.get("capture"):
            raise ReviewError("VALIDATION_ERROR", "ORIGINAL 證據必須有 capture")
        cap = item["capture"]
        party = item.get("asserting_party") or {"type": "USER", "key": "user"}
        origin_ids = [conn.execute(
            "INSERT INTO source_origin (origin_type, asserting_party_type, asserting_party_key, captured_by, captured_at,"
            " root_content_hash, description) VALUES (?,?,?,?,?,?,?)",
            ("USER_SITE_VISIT", party["type"], party.get("key"), cap["captured_by"], cap["captured_at"],
             item.get("content_hash"), item["description"])).lastrowid]
    else:
        if not derived or any(not isinstance(d, dict) or "ref_type" not in d for d in derived):
            raise ReviewError("VALIDATION_ERROR", "非 ORIGINAL 證據必須有具型別的 derived_from")
        origin_ids = sorted(_origins_of(conn, derived))
    if item.get("evidence_role") == "LISTING_LINKAGE" and not item.get("link_method"):
        raise ReviewError("VALIDATION_ERROR", "LISTING_LINKAGE 必須有 link_method")
    eid = conn.execute(
        "INSERT INTO evidence (kind, origin, listing_id, review_id, field, value, locator, observed_at, evidence_role,"
        " derivation_type, origin_ids, capture, link_method, content_hash) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        ("HUMAN_ASSERTION", "USER_OBSERVATION", listing_id, review_id, item["field"], j(item.get("value")),
         j({"type": "FREE_TEXT", "value": item["description"]}), item.get("observed_at") or now(),
         item.get("evidence_role", "OTHER"), derivation, j(origin_ids), j(item.get("capture")),
         item.get("link_method"), item.get("content_hash"))).lastrowid
    for d in derived:
        conn.execute("INSERT OR IGNORE INTO evidence_derivation VALUES (?,?,?,?)", (eid, d["ref_type"], d["id"], d.get("role", "CITES")))
    for link in item.get("links") or []:
        conn.execute("INSERT INTO evidence_link (evidence_id, target_type, target_id, target_field, stance) VALUES (?,?,?,?,?)",
                     (eid, link["target_type"], link["target_id"], link["target_field"], link["stance"]))
    return eid


def human_support(conn, listing_id):
    """依 H1–H6 判定使用者佐證是否足以確認完整門牌；另外整理照片格局比對（S9、S10）。"""
    rows = conn.execute("SELECT * FROM evidence WHERE listing_id=? AND status='ACTIVE' AND kind='HUMAN_ASSERTION'",
                        (listing_id,)).fetchall()
    system = conn.execute("SELECT id FROM source_origin WHERE origin_type='SYSTEM'").fetchone()
    sys_id = system["id"] if system else None
    snap = conn.execute("SELECT * FROM listing_snapshot WHERE listing_id=? ORDER BY id DESC LIMIT 1", (listing_id,)).fetchone()
    features = [unj(r["value"]) for r in rows if r["evidence_role"] == "FEATURE_OBSERVATION" and isinstance(unj(r["value"]), dict)]
    abstain = conn.execute(
        "SELECT 1 FROM review r JOIN review_task t ON t.id=r.task_id WHERE t.listing_id=? AND t.task_type='ADDRESS_CANDIDATE'"
        " AND r.status='ACTIVE' AND r.decision='ABSTAIN'", (listing_id,)).fetchone() is not None
    unmet, direct, link = [], None, None
    for r in rows:
        clean = sys_id not in unj(r["origin_ids"], [])
        if r["evidence_role"] == "DIRECT_ADDRESS" and r["derivation_type"] in DIRECT_DERIVATIONS and clean:
            direct = direct or r
        if r["evidence_role"] == "LISTING_LINKAGE" and r["link_method"] in STRONG_LINKS and clean:
            link = link or r
    if not direct:
        unmet.append("H1")
    if not link:
        unmet.append("H2")
    for r in (direct, link):
        if r is not None and not r["capture"] and r["derivation_type"] == "ORIGINAL":
            unmet.append("H3")
    if any(r["evidence_role"] in ("DIRECT_ADDRESS", "LISTING_LINKAGE") and sys_id in unj(r["origin_ids"], []) for r in rows):
        unmet.append("H4")
    full = None
    if direct:
        value = unj(direct["value"]) or {}
        addr = normalize_address(value.get("address_text") if isinstance(value, dict) else value)
        if addr.precision != "FLOOR":
            unmet.append("H5")
        else:
            full = f"高鐵南路一段{addr.number}號{addr.floor}樓"
            if snap and snap["floor_exact"] and str(snap["floor_min"]) != addr.floor:
                unmet += ["H5", "H6"]
    unmet = sorted(set(unmet))
    rejected = {(r["code"], r["floor"]) for r in conn.execute(
        "SELECT su.code, d.floor FROM review r JOIN review_task t ON t.id=r.task_id JOIN candidate c ON c.id=r.candidate_id"
        " JOIN dwelling d ON d.id=c.dwelling_id JOIN sales_unit su ON su.id=d.sales_unit_id"
        " WHERE t.listing_id=? AND t.task_type='ADDRESS_CANDIDATE' AND r.decision='REJECT_CANDIDATE'"
        " AND r.status IN ('ACTIVE','SUPERSEDED')", (listing_id,))}
    return {"sufficient": not unmet and full is not None, "unmet": unmet, "full_address": full,
            "evidence_ids": [r["id"] for r in (direct, link) if r is not None], "features": features, "abstain": abstain,
            "rejected": rejected}


def membership_decision(conn, listing_id):
    """有效的人工社區歸屬決定（CONFIRM_MEMBER／CONFIRM_NON_MEMBER）與其佐證；沒有則回傳 None。"""
    r = conn.execute(
        "SELECT r.* FROM review r JOIN review_task t ON t.id=r.task_id WHERE t.listing_id=? AND t.task_type='COMMUNITY_MEMBERSHIP'"
        " AND r.status='ACTIVE' AND r.decision IN ('CONFIRM_MEMBER','CONFIRM_NON_MEMBER') ORDER BY r.id DESC LIMIT 1",
        (listing_id,)).fetchone()
    if not r:
        return None
    ev = [e["evidence_id"] for e in conn.execute(
        "SELECT re.evidence_id FROM review_evidence re JOIN evidence e ON e.id=re.evidence_id WHERE re.review_id=? AND e.status='ACTIVE'",
        (r["id"],))]
    if not ev:
        return None  # 佐證全部被撤回 → 退回系統規則
    return {"outcome": "MEMBER" if r["decision"] == "CONFIRM_MEMBER" else "NON_MEMBER", "review_id": r["id"], "evidence_ids": ev}


def _open_task(conn, task_type, listing_id, base_assessment_id, priority=3):
    row = conn.execute("SELECT * FROM review_task WHERE task_type=? AND listing_id=? AND status='OPEN'",
                       (task_type, listing_id)).fetchone()
    if row:
        if row["base_assessment_id"] != base_assessment_id:
            conn.execute("UPDATE review_task SET base_assessment_id=?, version=version+1 WHERE id=?", (base_assessment_id, row["id"]))
        return row["id"]
    last = conn.execute("SELECT a.outcome, a.resolved_fields, a.candidate_count FROM review_task t JOIN assessment a ON a.id=t.base_assessment_id"
                        " WHERE t.task_type=? AND t.listing_id=? AND t.status='RESOLVED' ORDER BY t.id DESC LIMIT 1",
                        (task_type, listing_id)).fetchone()
    now_a = conn.execute("SELECT outcome, resolved_fields, candidate_count FROM assessment WHERE id=?", (base_assessment_id,)).fetchone()
    if last and now_a and tuple(last) == tuple(now_a):
        return None  # 使用者已處理過內容相同的判定：不重複詢問
    return conn.execute("INSERT INTO review_task (task_type, listing_id, base_assessment_id, priority, created_at) VALUES (?,?,?,?,?)",
                        (task_type, listing_id, base_assessment_id, priority, now())).lastrowid


def route_tasks(conn, listing_id, mem, addr, mem_aid, addr_aid):
    """06 §7 的覆核分流。"""
    if mem["outcome"] in ("LIKELY_MEMBER", "CONFLICTING"):
        _open_task(conn, "COMMUNITY_MEMBERSHIP", listing_id, mem_aid)
    live = sorted((c for c in addr["candidates"] if not c["excluded"]), key=lambda c: -c["score"])
    lead = live[0]["score"] - live[1]["score"] if len(live) > 1 else None
    route = addr["outcome"] in ("SINGLE_CANDIDATE", "CONFLICTING", "NO_CANDIDATE") or (
        addr["outcome"] == "MULTIPLE_CANDIDATES" and (len(live) <= 5 or (lead is not None and lead >= 0.3)))
    if route:
        _open_task(conn, "ADDRESS_CANDIDATE", listing_id, addr_aid, priority=2 if len(live) <= 2 else 3)
    else:
        conn.execute("UPDATE review_task SET status='OBSOLETE', version=version+1 WHERE task_type='ADDRESS_CANDIDATE'"
                     " AND listing_id=? AND status='OPEN'", (listing_id,))


def submit_decision(conn, task_id, expected_version, decision, rationale, new_evidence=None, evidence_ids=None,
                    candidate_id=None, address_unit_id=None):
    task = conn.execute("SELECT * FROM review_task WHERE id=?", (task_id,)).fetchone()
    if not task:
        raise ReviewError("NOT_FOUND", "找不到覆核任務", 404)
    if expected_version is None:
        raise ReviewError("PRECONDITION_REQUIRED", "缺少 If-Match", 428)
    if int(expected_version) != task["version"]:
        raise ReviewError("STALE_VERSION", f'任務目前版本為 "v{task["version"]}"', 412)
    if decision not in ALLOWED.get(task["task_type"], set()):
        raise ReviewError("DECISION_NOT_ALLOWED", f"{decision} 不適用於 {task['task_type']}")
    if not rationale or len(rationale) < 5:
        raise ReviewError("VALIDATION_ERROR", "rationale 至少 5 個字")
    if decision == "SET_UNIT" and not address_unit_id:
        raise ReviewError("VALIDATION_ERROR", "SET_UNIT 必須有 address_unit_id")
    if decision in ("CONFIRM_CANDIDATE", "REJECT_CANDIDATE"):
        if not candidate_id or not conn.execute("SELECT 1 FROM candidate WHERE id=? AND listing_id=?", (candidate_id, task["listing_id"])).fetchone():
            raise ReviewError("VALIDATION_ERROR", f"{decision} 必須指定這則刊登的 candidate_id")
    if decision in CONFIRM_DECISIONS and not (new_evidence or evidence_ids):
        raise ReviewError("VALIDATION_ERROR", "確認類決定必須附佐證")
    prev = conn.execute("SELECT id FROM review WHERE task_id=? AND status='ACTIVE' ORDER BY id DESC LIMIT 1", (task_id,)).fetchone()
    rid = conn.execute(
        "INSERT INTO review (task_id, decision, rationale, base_assessment_id, candidate_id, address_unit_id, support_assessment,"
        " supersedes_review_id, created_at) VALUES (?,?,?,?,?,?,?,?,?)",
        (task_id, decision, rationale, task["base_assessment_id"], candidate_id, address_unit_id, "{}",
         prev["id"] if prev else None, now())).lastrowid
    if prev:
        conn.execute("UPDATE review SET status='SUPERSEDED' WHERE id=?", (prev["id"],))
    match = conn.execute("SELECT * FROM identity_match WHERE id=?", (task["identity_match_id"],)).fetchone() \
        if task["task_type"] == "LISTING_MATCH" else None
    ev_listing = task["listing_id"] if match is None else match["listing_a_id"]
    created = [add_user_evidence(conn, ev_listing, item, rid) for item in (new_evidence or [])]
    for eid in created:
        conn.execute("INSERT INTO review_evidence VALUES (?,?, 'CREATED')", (rid, eid))
    for eid in evidence_ids or []:
        conn.execute("INSERT OR IGNORE INTO review_evidence VALUES (?,?, 'CITED')", (rid, eid))
    if match is not None:
        return _decide_match(conn, task, match, rid, decision, rationale, created + list(evidence_ids or []))
    if task["task_type"] in ("REGISTRY_MAPPING", "DERIVATION_RULE"):
        return _decide_registry(conn, task, rid, decision, rationale, created + list(evidence_ids or []))
    from .assess import assess_listing
    result = assess_listing(conn, task["listing_id"], trigger="REVIEW")
    support = {"result": "NOT_APPLICABLE", "unmet_conditions": [], "confirmed_fields": []}
    if decision in ("SET_UNIT", "CONFIRM_CANDIDATE"):
        hs = human_support(conn, task["listing_id"])
        if hs["sufficient"]:
            support = {"result": "SUFFICIENT", "unmet_conditions": [],
                       "confirmed_fields": [k for k, v in result["address"]["resolved_fields"].items() if v["status"] == "CONFIRMED"]}
        else:
            support = {"result": "INSUFFICIENT", "unmet_conditions": hs["unmet"] or ["H1"], "confirmed_fields": []}
    conn.execute("UPDATE review SET support_assessment=?, result_assessment_id=? WHERE id=?",
                 (j(support), result["address_assessment_id"], rid))
    if support["result"] != "INSUFFICIENT":
        conn.execute("UPDATE review_task SET status='RESOLVED', version=version+1 WHERE id=?", (task_id,))
    conn.commit()
    return review_to_api(conn, rid)


def _decide_match(conn, task, match, rid, decision, rationale, evidence_ids):
    """同屋覆核（13 §5）：CONFIRM_SAME → 人工合併；REJECT_SAME → 配對 REJECTED；ABSTAIN → 只結案。"""
    from . import identity
    if decision == "CONFIRM_SAME":
        pa, pb = [conn.execute("SELECT property_id FROM listing WHERE id=?", (lid,)).fetchone()[0]
                  for lid in (match["listing_a_id"], match["listing_b_id"])]
        if pa != pb:
            identity.merge(conn, [pa, pb], rationale, evidence_ids, identity_match_id=match["id"])
        else:
            conn.execute("UPDATE identity_match SET status='CONFIRMED', version=version+1 WHERE id=?", (match["id"],))
    elif decision == "REJECT_SAME":
        conn.execute("UPDATE identity_match SET status='REJECTED', version=version+1 WHERE id=?", (match["id"],))
    support = {"result": "NOT_APPLICABLE", "unmet_conditions": [], "confirmed_fields": []}
    conn.execute("UPDATE review SET support_assessment=?, result_assessment_id=? WHERE id=?",
                 (j(support), match["current_assessment_id"], rid))
    conn.execute("UPDATE review_task SET status='RESOLVED', version=version+1 WHERE id=?", (task["id"],))
    conn.commit()
    return review_to_api(conn, rid)


def _decide_registry(conn, task, rid, decision, rationale, evidence_ids):
    from . import registry
    item_id = task["registry_mapping_id"] if task["task_type"] == "REGISTRY_MAPPING" else task["derivation_rule_id"]
    target = "REGISTRY_MAPPING" if task["task_type"] == "REGISTRY_MAPPING" else "DERIVATION_RULE"
    for eid in evidence_ids:
        conn.execute("INSERT INTO evidence_link (evidence_id, target_type, target_id, target_field, stance) VALUES (?,?,?,?,?)",
                     (eid, target, item_id, "record_status", "REFUTES" if decision.startswith("REJECT") else "SUPPORTS"))
    if decision != "ABSTAIN":
        status = "VERIFIED" if decision.startswith("VERIFY") else "RETRACTED"
        registry.set_status(conn, task["task_type"], item_id, status, rationale, evidence_ids, review_id=rid)
    conn.execute("UPDATE review SET support_assessment=? WHERE id=?",
                 (j({"result": "NOT_APPLICABLE", "unmet_conditions": [], "confirmed_fields": []}), rid))
    conn.execute("UPDATE review_task SET status='RESOLVED', version=version+1 WHERE id=?", (task["id"],))
    conn.commit()
    return review_to_api(conn, rid)


def retract_review(conn, review_id, rationale):
    rv = conn.execute("SELECT * FROM review WHERE id=?", (review_id,)).fetchone()
    if not rv:
        raise ReviewError("NOT_FOUND", "找不到覆核", 404)
    if rv["status"] == "RETRACTED":
        raise ReviewError("ALREADY_DONE", "已經撤回", 409)
    conn.execute("UPDATE review SET status='RETRACTED', retraction_rationale=? WHERE id=?", (rationale, review_id))
    created = [r["evidence_id"] for r in conn.execute("SELECT evidence_id FROM review_evidence WHERE review_id=? AND role='CREATED'", (review_id,))]
    retract_evidence_cascade(conn, created)
    task = conn.execute("SELECT * FROM review_task WHERE id=?", (rv["task_id"],)).fetchone()
    if task["listing_id"] is not None:
        from .assess import assess_listing
        assess_listing(conn, task["listing_id"], trigger="REVIEW_RETRACTED")
    conn.commit()
    return review_to_api(conn, review_id)


def retract_evidence_cascade(conn, evidence_ids, reason="RETRACTED"):
    """撤回證據，並沿 evidence_derivation 連鎖撤回所有依賴者（05 §5.4）。"""
    queue, seen = list(evidence_ids), set()
    while queue:
        eid = queue.pop()
        if eid in seen:
            continue
        seen.add(eid)
        conn.execute("UPDATE evidence SET status='RETRACTED', status_reason=?, version=version+1 WHERE id=?",
                     (reason if eid in evidence_ids else "UPSTREAM_RETRACTED", eid))
        queue += [r["evidence_id"] for r in conn.execute(
            "SELECT evidence_id FROM evidence_derivation WHERE ref_type='EVIDENCE' AND ref_id=?", (eid,))]
    return sorted(seen)


def review_to_api(conn, review_id):
    r = conn.execute("SELECT * FROM review WHERE id=?", (review_id,)).fetchone()
    ev = conn.execute("SELECT evidence_id, role FROM review_evidence WHERE review_id=?", (review_id,)).fetchall()
    return {
        "id": r["id"], "task_id": r["task_id"], "decision": r["decision"], "rationale": r["rationale"], "actor": "USER",
        "base_assessment_id": r["base_assessment_id"], "result_assessment_id": r["result_assessment_id"],
        "candidate_id": r["candidate_id"], "address_unit_id": r["address_unit_id"],
        "cited_evidence_ids": [e["evidence_id"] for e in ev if e["role"] == "CITED"],
        "created_evidence_ids": [e["evidence_id"] for e in ev if e["role"] == "CREATED"],
        "support_assessment": unj(r["support_assessment"]), "status": r["status"],
        "supersedes_review_id": r["supersedes_review_id"], "retraction_rationale": r["retraction_rationale"],
        "created_at": r["created_at"],
    }


def content_hash(text):
    return "sha256:" + hashlib.sha256(text.encode()).hexdigest()[:16]
