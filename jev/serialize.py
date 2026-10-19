"""資料表 → OpenAPI 物件（欄位依 api/openapi.yaml）。"""

from .db import unj

SNAP_API_FIELDS = ["total_price", "parking_price", "parking_included", "building_ping", "main_ping", "parking_ping", "floor_text",
                   "total_floors", "rooms", "halls", "baths", "orientation", "age_years", "address_text", "community_text",
                   "platform_community_ref", "sales_unit_text", "units_per_floor", "map_address", "title", "posted_at"]


def snapshot(row):
    d = {k: row[k] for k in SNAP_API_FIELDS}
    d.update({"id": row["id"], "listing_id": row["listing_id"], "observed_at": row["observed_at"], "content_hash": row["content_hash"],
              "address_precision": row["address_precision"], "fetch_run_id": row["fetch_run_id"],
              "source_document_id": row["source_document_id"], "raw_available": False, "parser_version": row["parser_version"],
              "floor_min": row["floor_min"], "floor_max": row["floor_max"], "floor_exact": bool(row["floor_exact"])})
    return d


def listing(conn, row):
    src = conn.execute("SELECT code FROM source WHERE id=?", (row["source_id"],)).fetchone()["code"]
    def outcome(subject):
        a = conn.execute("SELECT outcome FROM assessment WHERE subject_type=? AND listing_id=? AND status='CURRENT'",
                         (subject, row["id"])).fetchone()
        return a["outcome"] if a else None
    return {"id": row["id"], "source_code": src, "external_id": row["external_id"], "external_id_origin": row["external_id_origin"],
            "url": row["url"], "deal_type": row["deal_type"], "status": row["status"], "current_cycle_no": row["current_cycle_no"],
            "consecutive_missing_complete_runs": row["consecutive_missing_complete_runs"], "first_observed_at": row["first_observed_at"],
            "first_observed_context": row["first_observed_context"], "new_listing_status": row["new_listing_status"],
            "last_seen_at": row["last_seen_at"], "lister_type": row["lister_type"], "lister_company": row["lister_company"],
            "property_id": row["property_id"], "membership_status": outcome("COMMUNITY_MEMBERSHIP"),
            "address_status": outcome("ADDRESS_RESOLUTION"), "version": row["version"]}


def _hits(hits):
    return [{"rule_code": h["rule_code"], "effect": h.get("effect"), "decisive": bool(h.get("decisive")),
             "evidence_ids": h.get("evidence_ids", []), "detail": h.get("detail")} for h in hits or []]


def candidates(conn, assessment_id, include_excluded=True):
    rows = conn.execute(
        "SELECT ac.*, c.dwelling_id, c.address_unit_id, c.generated_by FROM assessment_candidate ac JOIN candidate c ON c.id=ac.candidate_id"
        " WHERE ac.assessment_id=? ORDER BY ac.excluded, ac.rule_score DESC, c.id", (assessment_id,)).fetchall()
    return [{"candidate_id": r["candidate_id"], "dwelling_id": r["dwelling_id"], "address_unit_id": r["address_unit_id"],
             "generated_by": r["generated_by"], "rule_score": r["rule_score"], "excluded": bool(r["excluded"]),
             "excluded_by_rule": r["excluded_by_rule"], "rule_hits": _hits(unj(r["rule_hits"], []))}
            for r in rows if include_excluded or not r["excluded"]]


def assessment(conn, row, max_candidates=50):
    evid = [r["evidence_id"] for r in conn.execute("SELECT evidence_id FROM assessment_evidence WHERE assessment_id=?", (row["id"],))]
    base = {"id": row["id"], "subject_type": row["subject_type"], "listing_id": row["listing_id"],
            "identity_match_id": row["identity_match_id"], "snapshot_id": row["snapshot_id"], "ruleset_version": row["ruleset_version"],
            "registry_version_id": row["registry_version_id"], "evidence_set_hash": row["evidence_set_hash"], "evidence_ids": evid,
            "registry_items_used": unj(row["registry_items_used"], []), "rule_score": row["rule_score"],
            "calibrated_probability": None, "calibration_version": None, "basis": row["basis"], "flags": unj(row["flags"], []),
            "trigger": row["trigger"], "status": row["status"], "superseded_by_id": row["superseded_by_id"], "created_at": row["created_at"],
            "outcome": row["outcome"]}
    if row["subject_type"] == "COMMUNITY_MEMBERSHIP":
        base["rule_hits"] = _hits(unj(row["rule_hits"], []))
    elif row["subject_type"] == "ADDRESS_RESOLUTION":
        fields = unj(row["resolved_fields"], {})
        base["resolved_fields"] = {k: {"status": v["status"], "value": v.get("value"), "basis": v.get("basis"),
                                       "evidence_ids": v.get("evidence_ids", [])} for k, v in fields.items()}
        base["candidate_count"] = row["candidate_count"] or 0
        base["candidates"] = candidates(conn, row["id"], include_excluded=False)[:max_candidates]
    else:
        base["conflicts"] = unj(row["flags"], [])
        base["flags"] = []
        base["rule_hits"] = _hits(unj(row["rule_hits"], []))
    return base


def listing_detail(conn, row):
    d = listing(conn, row)
    snap = conn.execute("SELECT * FROM listing_snapshot WHERE listing_id=? ORDER BY id DESC LIMIT 1", (row["id"],)).fetchone()
    d["latest_snapshot"] = snapshot(snap) if snap else None
    d["cycles"] = [{"cycle_no": c["cycle_no"], "started_at": c["started_at"], "start_reason": c["start_reason"],
                    "ended_at": c["ended_at"], "end_reason": c["end_reason"]}
                   for c in conn.execute("SELECT * FROM listing_cycle WHERE listing_id=? ORDER BY cycle_no", (row["id"],))]
    for key, subject in (("membership_assessment", "COMMUNITY_MEMBERSHIP"), ("address_assessment", "ADDRESS_RESOLUTION")):
        a = conn.execute("SELECT * FROM assessment WHERE subject_type=? AND listing_id=? AND status='CURRENT'", (subject, row["id"])).fetchone()
        d[key] = assessment(conn, a) if a else None
    return d


def identity_match(row):
    return {"id": row["id"], "listing_a_id": row["listing_a_id"], "listing_b_id": row["listing_b_id"], "status": row["status"],
            "current_assessment_id": row["current_assessment_id"], "conflicts": unj(row["conflicts"], []), "version": row["version"]}


def review_task(conn, row):
    d = {"id": row["id"], "task_type": row["task_type"], "status": row["status"], "priority": row["priority"],
         "base_assessment_id": row["base_assessment_id"], "version": row["version"], "created_at": row["created_at"]}
    a = conn.execute("SELECT * FROM assessment WHERE id=?", (row["base_assessment_id"],)).fetchone()
    if row["task_type"] in ("COMMUNITY_MEMBERSHIP", "ADDRESS_CANDIDATE"):
        d["listing_id"] = row["listing_id"]
        d["assessment"] = assessment(conn, a)
    elif row["task_type"] == "REGISTRY_MAPPING":
        d["mapping"] = registry_mapping(conn.execute("SELECT * FROM registry_mapping WHERE id=?", (row["registry_mapping_id"],)).fetchone())
    elif row["task_type"] == "DERIVATION_RULE":
        d["rule"] = derivation_rule(conn.execute("SELECT * FROM address_derivation_rule WHERE id=?", (row["derivation_rule_id"],)).fetchone())
    elif row["task_type"] == "LISTING_MATCH":
        d["identity_match"] = identity_match(conn.execute("SELECT * FROM identity_match WHERE id=?", (row["identity_match_id"],)).fetchone())
        d["assessment"] = assessment(conn, a)
    return d


def evidence(conn, row):
    derived = [{"ref_type": d["ref_type"], "id": d["ref_id"], "role": d["role"]}
               for d in conn.execute("SELECT * FROM evidence_derivation WHERE evidence_id=? ORDER BY ref_type, ref_id", (row["id"],))]
    links = [{"target_type": l["target_type"], "target_id": l["target_id"], "target_field": l["target_field"], "stance": l["stance"],
              "rule_code": l["rule_code"]} for l in conn.execute("SELECT * FROM evidence_link WHERE evidence_id=? ORDER BY id", (row["id"],))]
    return {"id": row["id"], "kind": row["kind"], "origin": row["origin"], "listing_id": row["listing_id"], "snapshot_id": row["snapshot_id"],
            "source_document_id": row["source_document_id"], "review_id": row["review_id"], "field": row["field"],
            "value": unj(row["value"]), "locator": unj(row["locator"]), "excerpt": row["excerpt"], "excerpt_hash": row["excerpt_hash"],
            "observed_at": row["observed_at"], "evidence_role": row["evidence_role"], "derivation_type": row["derivation_type"],
            "derived_from": derived, "origin_ids": unj(row["origin_ids"], []), "capture": unj(row["capture"]),
            "link_method": row["link_method"], "content_hash": row["content_hash"], "extractor_version": row["extractor_version"],
            "status": row["status"], "status_reason": row["status_reason"], "superseded_by_id": row["superseded_by_id"],
            "raw_available": False, "links": links, "version": row["version"]}


def property_(conn, row):
    ids = [r[0] for r in conn.execute("SELECT id FROM listing WHERE property_id=? ORDER BY id", (row["id"],))]
    return {"id": row["id"], "status": row["status"], "resolved_dwelling_id": row["resolved_dwelling_id"],
            "current_min_price": row["current_min_price"], "active_listing_count": row["active_listing_count"],
            "listing_ids": ids, "first_seen_at": row["first_seen_at"], "version": row["version"]}


def property_detail(conn, row):
    d = property_(conn, row)
    d["listings"] = [listing(conn, l) for l in conn.execute("SELECT * FROM listing WHERE property_id=? ORDER BY id", (row["id"],))]
    hist, current = [], {}
    snaps = conn.execute(
        f"SELECT listing_id, observed_at, total_price FROM listing_snapshot WHERE listing_id IN ({','.join('?' * len(d['listing_ids']))})"
        " ORDER BY observed_at, id", d["listing_ids"]).fetchall()
    for sn in snaps:
        current[sn["listing_id"]] = sn["total_price"]
        low = min(current.values())
        if not hist or hist[-1]["min_total_price"] != low:
            hist.append({"observed_at": sn["observed_at"], "min_total_price": low})
    d["price_history"] = hist
    return d


def merge_operation(conn, row):
    affected = [{"listing_id": m["listing_id"], "from_property_id": m["from_property_id"], "to_property_id": m["to_property_id"]}
                for m in conn.execute("SELECT * FROM merge_operation_listing WHERE operation_id=? ORDER BY listing_id", (row["id"],))]
    return {"id": row["id"], "op_type": row["op_type"], "actor": row["actor"], "rationale": row["rationale"],
            "ruleset_version": row["ruleset_version"], "identity_match_id": row["identity_match_id"],
            "reverts_operation_id": row["reverts_operation_id"], "affected": affected,
            "evidence_ids": unj(row["evidence_ids"], []), "created_at": row["created_at"]}


def notification(row):
    return {"id": row["id"], "notification_kind": row["notification_kind"], "alert_rule_id": row["alert_rule_id"],
            "event_id": row["event_id"], "channel": row["channel"], "rendered": unj(row["rendered"], {}), "status": row["status"],
            "attempts": row["attempts"], "last_error": row["last_error"], "sent_at": row["sent_at"],
            "corrects_notification_id": row["corrects_notification_id"]}


def source(row):
    return {"id": row["id"], "code": row["code"], "name": row["name"], "priority": row["priority"], "access_method": row["access_method"],
            "rights": unj(row["rights"]), "auto_fetch_enabled": bool(row["auto_fetch_enabled"]), "min_interval_sec": row["min_interval_sec"],
            "canonical_scope_fingerprint": row["canonical_scope_fingerprint"], "baseline_completed_at": row["baseline_completed_at"],
            "compliance": unj(row["compliance"], {}), "version": row["version"]}


def alert_rule(row):
    return {"id": row["id"], "name": row["name"], "enabled": bool(row["enabled"]), "event_types": unj(row["event_types"], []),
            "max_total_price": row["max_total_price"], "min_price_drop_pct": row["min_price_drop_pct"], "min_rooms": row["min_rooms"],
            "min_building_ping": row["min_building_ping"], "membership_statuses": unj(row["membership_statuses"], []),
            "channel": row["channel"], "notify_corrections": bool(row["notify_corrections"]), "version": row["version"]}


def origin_relation(row):
    return {"id": row["id"], "origin_a_id": row["origin_a_id"], "origin_b_id": row["origin_b_id"], "relation": row["relation"],
            "basis": row["basis"], "rationale": row["rationale"], "evidence_ids": unj(row["evidence_ids"], []), "status": row["status"],
            "created_at": row["created_at"]}


def decision_call(row):
    return {"id": row["id"], "purpose": row["purpose"], "listing_id": row["listing_id"], "identity_match_id": row["identity_match_id"],
            "provider": row["provider"], "model": row["model"], "wire_format": row["wire_format"], "questions": unj(row["questions"], []),
            "image_count": row["image_count"], "status": row["status"], "http_status": row["http_status"], "error": row["error"],
            "input_tokens": row["input_tokens"], "cost_usd": row["cost_usd"], "latency_ms": row["latency_ms"], "created_at": row["created_at"]}


def sales_unit(row):
    return {"id": row["id"], "code": row["code"], "building_id": None, "unit_type_id": row["unit_type_id"],
            "orientation": "、".join(unj(row["facades"], [])) or None, "record_status": row["record_status"]}


def dwelling(row):
    return {"id": row["id"], "sales_unit_id": row["sales_unit_id"], "floor": row["floor"], "record_status": row["record_status"]}


def address_unit(row):
    from .normalize import normalize_address
    norm = normalize_address(row["raw_address"]).to_dict()
    return {"id": row["id"], "raw_address": row["raw_address"], "normalized": norm, "origin": row["origin"],
            "record_status": row["record_status"]}


def registry_mapping(row):
    return {"id": row["id"], "registry_version_id": row["registry_version_id"], "mapping_type": row["mapping_type"],
            "from_type": row["from_type"], "from_id": row["from_id"], "to_type": row["to_type"], "to_id": row["to_id"],
            "record_status": row["record_status"], "version": row["version"], "evidence_ids": unj(row["evidence_ids"], [])}


def derivation_rule(row):
    return {"id": row["id"], "registry_version_id": row["registry_version_id"], "scope_type": row["scope_type"],
            "scope_ids": unj(row["scope_ids"], []), "floor_min": row["floor_min"], "floor_max": row["floor_max"],
            "rule_type": row["rule_type"], "floor_label_format": row["floor_label_format"], "exceptions": unj(row["exceptions"], []),
            "record_status": row["record_status"], "supersedes_rule_id": row["supersedes_rule_id"],
            "evidence_ids": unj(row["evidence_ids"], []), "version": row["version"], "created_at": row["created_at"]}


def problem(status, code, detail, **extra):
    return {"type": f"https://jev.local/problems/{code.lower().replace('_', '-')}", "title": code.replace("_", " ").title(),
            "status": status, "code": code, "detail": detail, **extra}
