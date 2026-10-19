"""刊登生命週期：人工匯入、快照、上架週期、事件（docs/07、docs/12）。

人工匯入的輪次 coverage 固定為 NOT_APPLICABLE，不會讓其他刊登增加未出現次數或下架。
"""

import csv
import hashlib

from .db import j, now
from .events import emit
from .normalize import normalize_address, parse_floor

SNAPSHOT_FIELDS = ["total_price", "parking_price", "parking_included", "building_ping", "main_ping", "parking_ping",
                   "floor_text", "rooms", "halls", "baths", "orientation", "age_years", "address_text", "community_text",
                   "platform_community_ref", "sales_unit_text", "units_per_floor", "map_address", "title", "posted_at"]
INT_FIELDS = {"total_price", "parking_price", "rooms", "halls", "baths", "units_per_floor"}
FLOAT_FIELDS = {"building_ping", "main_ping", "parking_ping", "age_years"}


def _clean(rec):
    out = {}
    for k in SNAPSHOT_FIELDS:
        v = rec.get(k)
        if v in ("", None):
            out[k] = None
        elif k in INT_FIELDS:
            out[k] = int(float(v))
        elif k in FLOAT_FIELDS:
            out[k] = float(v)
        else:
            out[k] = str(v)
    out["parking_included"] = out["parking_included"] or "UNKNOWN"
    if out["total_price"] is None or out["total_price"] < 1:
        raise ValueError("total_price 必須 ≥ 1")
    return out


def _hash(fields):
    return "sha256:" + hashlib.sha256(j(fields).encode()).hexdigest()[:24]


def source_id(conn, code):
    row = conn.execute("SELECT id FROM source WHERE code=?", (code,)).fetchone()
    if not row:
        raise ValueError(f"未知的來源 {code}")
    return row["id"]


def start_run(conn, src_id, import_mode):
    return conn.execute(
        "INSERT INTO fetch_run (source_id, run_type, status, coverage, coverage_reasons, import_mode, started_at)"
        " VALUES (?, 'MANUAL_IMPORT', 'RUNNING', 'NOT_APPLICABLE', ?, ?, ?)",
        (src_id, j(["MANUAL_IMPORT"]), import_mode, now())).lastrowid


def finish_run(conn, run_id, count):
    conn.execute("UPDATE fetch_run SET status='SUCCEEDED', enumerated_count=?, finished_at=? WHERE id=?", (count, now(), run_id))


def record(conn, rec, import_mode="INCREMENTAL", run_id=None, observed_at=None):
    """匯入一筆人工記錄：新刊登建立 listing 與 property；既有刊登在內容改變時新增快照並產生事件。"""
    code = rec.get("source_code") or "SRC-MANUAL"
    sid = source_id(conn, code)
    ext, origin = rec.get("external_id") or None, "PLATFORM"
    if not ext and rec.get("url"):
        ext, origin = "url_" + hashlib.sha256(rec["url"].encode()).hexdigest()[:16], "URL_HASH"
    if not ext:
        if code != "SRC-MANUAL":
            raise ValueError("平台來源必須有 external_id 或 url")
        from .events import ulid
        ext, origin = "man_" + ulid(), "SYSTEM_GENERATED"
    fields = _clean(rec)
    fl = parse_floor(fields["floor_text"])
    addr = normalize_address(fields["address_text"])
    ts = observed_at or rec.get("observed_at") or now()
    content = _hash(fields)
    lst = conn.execute("SELECT * FROM listing WHERE source_id=? AND external_id=?", (sid, ext)).fetchone()
    created = lst is None
    if created:
        context = "MANUAL_INITIAL" if import_mode == "INITIAL" else "MANUAL_INCREMENTAL"
        is_new = rec.get("is_new_listing") in (True, "true", "1", "TRUE")
        new_status = "NOT_NEW" if context == "MANUAL_INITIAL" else ("CONFIRMED_NEW" if is_new else "UNCONFIRMED")
        cid = conn.execute("SELECT id FROM community WHERE code='lianshang-world'").fetchone()["id"]
        pid = conn.execute("INSERT INTO property (community_id, first_seen_at) VALUES (?,?)", (cid, ts)).lastrowid
        lid = conn.execute(
            "INSERT INTO listing (source_id, external_id, external_id_origin, url, first_observed_at, first_observed_context,"
            " new_listing_status, last_seen_at, lister_type, lister_company, property_id) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            (sid, ext, origin, rec.get("url") or None, ts, context, new_status, ts, rec.get("lister_type") or "UNKNOWN",
             rec.get("lister_company") or None, pid)).lastrowid
        conn.execute("INSERT INTO property_membership (property_id, listing_id, valid_from) VALUES (?,?,?)", (pid, lid, ts))
        conn.execute("INSERT INTO listing_cycle (listing_id, cycle_no, started_at, start_reason) VALUES (?,1,?, 'FIRST_OBSERVED')", (lid, ts))
        lst = conn.execute("SELECT * FROM listing WHERE id=?", (lid,)).fetchone()
    prev = conn.execute("SELECT * FROM listing_snapshot WHERE listing_id=? ORDER BY id DESC LIMIT 1", (lst["id"],)).fetchone()
    if prev and prev["content_hash"] == content:
        conn.execute("UPDATE listing SET last_seen_at=? WHERE id=?", (ts, lst["id"]))
        return {"listing_id": lst["id"], "created": False, "snapshot_id": prev["id"], "changed": False}
    snap_id = conn.execute(
        "INSERT INTO listing_snapshot (listing_id, fetch_run_id, observed_at, content_hash, " + ", ".join(SNAPSHOT_FIELDS) +
        ", floor_min, floor_max, floor_exact, total_floors, address_precision) VALUES (" + ",".join("?" * (len(SNAPSHOT_FIELDS) + 9)) + ")",
        [lst["id"], run_id, ts, content] + [fields[k] for k in SNAPSHOT_FIELDS] +
        [fl.floor_min, fl.floor_max, int(fl.floor_exact), fl.total_floors, addr.precision]).lastrowid
    conn.execute("UPDATE listing SET last_seen_at=?, version=version+1 WHERE id=?", (ts, lst["id"]))
    subject = {"listing_id": lst["id"], "property_id": lst["property_id"], "source_id": sid}
    cause = {"fetch_run_id": run_id, "snapshot_id": snap_id}
    if created:
        reason = {"MANUAL_INITIAL": "MANUAL_INITIAL_CONTEXT", "BASELINE": "BASELINE_CONTEXT"}.get(lst["first_observed_context"], "INTERNAL_EVENT")
        emit(conn, "LISTING_FIRST_OBSERVED", f"first_observed:{lst['id']}", subject,
             {"context": lst["first_observed_context"], "new_listing_status": lst["new_listing_status"], "cycle_no": 1,
              "snapshot_id": snap_id, "total_price": fields["total_price"]}, cause, suppression_reason=reason)
        if lst["new_listing_status"] == "CONFIRMED_NEW":
            emit(conn, "LISTING_NEW_CONFIRMED", f"new_confirmed:{lst['id']}", subject,
                 {"cycle_no": 1, "snapshot_id": snap_id, "confirmation_basis": "USER_FLAG", "late_confirmation": False,
                  "total_price": fields["total_price"]}, cause)
    elif prev:
        if prev["total_price"] != fields["total_price"]:
            diff = fields["total_price"] - prev["total_price"]
            emit(conn, "LISTING_PRICE_CHANGED", f"price:{lst['id']}:{snap_id}", subject,
                 {"cycle_no": lst["current_cycle_no"], "from_snapshot_id": prev["id"], "to_snapshot_id": snap_id,
                  "old_price": prev["total_price"], "new_price": fields["total_price"], "diff": diff,
                  "pct": round(diff / prev["total_price"], 4), "direction": "DOWN" if diff < 0 else "UP"}, cause)
        changed = [{"field": k, "old": prev[k], "new": fields[k]} for k in SNAPSHOT_FIELDS
                   if k not in ("total_price", "posted_at") and prev[k] != fields[k]]
        if changed:
            emit(conn, "LISTING_FIELDS_CHANGED", f"fields:{lst['id']}:{snap_id}", subject,
                 {"cycle_no": lst["current_cycle_no"], "from_snapshot_id": prev["id"], "to_snapshot_id": snap_id,
                  "changed_fields": changed}, cause)
    _recompute(conn, lst["id"])
    return {"listing_id": lst["id"], "created": created, "snapshot_id": snap_id, "changed": True}


def _recompute(conn, listing_id):
    """property 的最低價、上架中刊登數與狀態跟著刊登變動（13 §2）。"""
    from .identity import recompute_property
    recompute_property(conn, conn.execute("SELECT property_id FROM listing WHERE id=?", (listing_id,)).fetchone()[0])


def mark_delisted(conn, listing_id, reason, rationale):
    lst = conn.execute("SELECT * FROM listing WHERE id=?", (listing_id,)).fetchone()
    if lst["status"] == "DELISTED":
        raise ValueError("已經是 DELISTED")
    ts = now()
    last = conn.execute("SELECT id FROM listing_snapshot WHERE listing_id=? ORDER BY id DESC LIMIT 1", (listing_id,)).fetchone()
    conn.execute("UPDATE listing SET status='DELISTED', version=version+1 WHERE id=?", (listing_id,))
    conn.execute("UPDATE listing_cycle SET ended_at=?, end_reason=? WHERE listing_id=? AND cycle_no=?",
                 (ts, reason, listing_id, lst["current_cycle_no"]))
    emit(conn, "LISTING_DELISTED", f"delisted:{listing_id}:{lst['current_cycle_no']}",
         {"listing_id": listing_id, "property_id": lst["property_id"], "source_id": lst["source_id"]},
         {"cycle_no": lst["current_cycle_no"], "reason": reason, "last_snapshot_id": last["id"] if last else None,
          "last_seen_at": lst["last_seen_at"], "missing_complete_run_ids": [], "evidence_document_id": None, "days_listed": None})
    _recompute(conn, listing_id)
    conn.commit()


def relist_if_needed(conn, listing_id):
    lst = conn.execute("SELECT * FROM listing WHERE id=?", (listing_id,)).fetchone()
    if lst["status"] != "DELISTED":
        return None
    cyc = conn.execute("SELECT * FROM listing_cycle WHERE listing_id=? AND cycle_no=?", (listing_id, lst["current_cycle_no"])).fetchone()
    new_no = lst["current_cycle_no"] + 1
    ts = now()
    conn.execute("UPDATE listing SET status='ACTIVE', current_cycle_no=?, version=version+1 WHERE id=?", (new_no, listing_id))
    conn.execute("INSERT INTO listing_cycle (listing_id, cycle_no, started_at, start_reason) VALUES (?,?,?, 'RELISTED')", (listing_id, new_no, ts))
    _recompute(conn, listing_id)
    return emit(conn, "LISTING_RELISTED", f"relisted:{listing_id}:{new_no}",
                {"listing_id": listing_id, "property_id": lst["property_id"], "source_id": lst["source_id"]},
                {"previous_cycle_no": lst["current_cycle_no"], "cycle_no": new_no, "delisted_at": cyc["ended_at"], "snapshot_id": None})


def import_csv(conn, path, import_mode):
    """匯入人工記錄 CSV。先檢查全部列，有錯就整個檔案不寫入；每個來源一個 MANUAL_IMPORT 輪次。"""
    from .assess import assess_listing
    rows = list(csv.DictReader(open(path, encoding="utf-8-sig")))
    for i, rec in enumerate(rows, start=2):
        try:
            code = rec.get("source_code") or "SRC-MANUAL"
            source_id(conn, code)
            _clean(rec)
            if code != "SRC-MANUAL" and not (rec.get("external_id") or rec.get("url")):
                raise ValueError("平台來源必須有 external_id 或 url")
        except (ValueError, TypeError) as e:
            raise ValueError(f"第 {i} 列（{rec.get('source_code')} {rec.get('external_id') or ''}）：{e}；整個檔案都沒有匯入") from e
    runs, results = {}, []
    for rec in rows:
        sid = source_id(conn, rec.get("source_code") or "SRC-MANUAL")
        if sid not in runs:
            runs[sid] = start_run(conn, sid, import_mode)
        lst_before = conn.execute("SELECT status FROM listing WHERE source_id=? AND external_id=?", (sid, rec.get("external_id"))).fetchone()
        res = record(conn, rec, import_mode, runs[sid])
        if lst_before and lst_before["status"] == "DELISTED":
            relist_if_needed(conn, res["listing_id"])
        if res["changed"]:
            assess_listing(conn, res["listing_id"])
        results.append(res)
    for sid, rid in runs.items():
        finish_run(conn, rid, sum(1 for r in rows if source_id(conn, r.get("source_code") or "SRC-MANUAL") == sid))
    conn.commit()
    return results
