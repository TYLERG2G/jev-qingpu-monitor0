"""事件寫入（docs/12）：一律保存；以 idempotency_key 去重；notify_eligible 決定是否交給通知。"""

import os
import time

from .db import j, now

CROCKFORD = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"
NO_CAUSE = {"fetch_run_id": None, "snapshot_id": None, "operation_id": None, "review_id": None, "assessment_id": None}

# 依 12 §2：保存但不通知的事件，以及固定的抑制原因
INTERNAL = {"PROPERTY_MERGED", "PROPERTY_SPLIT", "MERGE_REVERTED", "COMMUNITY_MEMBERSHIP_CHANGED",
            "ADDRESS_ASSESSMENT_CHANGED", "REGISTRY_UPDATED"}
NOTIFIABLE = {"LISTING_NEW_CONFIRMED", "DWELLING_NEW_LISTING", "LISTING_RELISTED", "LISTING_PRICE_CHANGED",
              "LISTING_FIELDS_CHANGED", "LISTING_DELISTED", "SOURCE_HEALTH_CHANGED"}


def ulid() -> str:
    ms = int(time.time() * 1000)
    rnd = int.from_bytes(os.urandom(10), "big")
    value = (ms << 80) | rnd
    out = []
    for _ in range(26):
        out.append(CROCKFORD[value & 31])
        value >>= 5
    return "".join(reversed(out))


def emit(conn, event_type, idempotency_key, subject, payload, caused_by=None, suppression_reason=None, occurred_at=None):
    """寫入事件；如果冪等鍵已存在就不寫，回傳 None。"""
    if event_type in INTERNAL:
        notify, reason = False, "INTERNAL_EVENT"
    elif event_type == "LISTING_FIRST_OBSERVED":
        notify, reason = False, suppression_reason or "INTERNAL_EVENT"
    else:
        notify, reason = event_type in NOTIFIABLE, None
    subj = {"listing_id": None, "property_id": None, "source_id": None, **subject}
    cause = {**NO_CAUSE, **(caused_by or {})}
    ts = now()
    event_id = ulid()
    cur = conn.execute(
        "INSERT OR IGNORE INTO listing_event (event_id, event_type, schema_version, idempotency_key, listing_id, property_id,"
        " source_id, caused_by, payload, notify_eligible, suppression_reason, occurred_at, recorded_at)"
        " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (event_id, event_type, 1, idempotency_key, subj["listing_id"], subj["property_id"], subj["source_id"],
         j(cause), j(payload), int(notify), reason, occurred_at or ts, ts),
    )
    return event_id if cur.rowcount else None


def to_api(row) -> dict:
    """listing_event 列 → OpenAPI Event 物件。"""
    import json
    return {
        "event_id": row["event_id"], "event_type": row["event_type"], "schema_version": row["schema_version"],
        "idempotency_key": row["idempotency_key"], "occurred_at": row["occurred_at"], "recorded_at": row["recorded_at"],
        "subject": {"listing_id": row["listing_id"], "property_id": row["property_id"], "source_id": row["source_id"]},
        "caused_by": json.loads(row["caused_by"]), "notify_eligible": bool(row["notify_eligible"]),
        "suppression_reason": row["suppression_reason"], "payload": json.loads(row["payload"]),
    }
