"""名冊：社區、戶別、坪型、門牌、推導規則、來源（docs/05 §1、§5.5；docs/research）。

狀態一律照查證結果：戶別→號是 TRANSCRIBED_UNVERIFIED，坪型是 INFERRED，推導規則沒有 VERIFIED。
"""

import csv
import re
from pathlib import Path

from .db import j, now, unj

ROOT = Path(__file__).resolve().parent.parent
MAPPING_CSV = ROOT / "docs" / "research" / "lianshang-world-address-mapping.csv"
AREA_CSV = ROOT / "docs" / "research" / "unit-area-reference.csv"

ROAD, SECTION = "高鐵南路", 1
FRONT_ROW_2F_ABSENT = ["A1", "A2", "B1", "B2", "C1", "C2"]  # 2F 平面圖：這 6 戶在 2F 不存在
# 依樂居門牌示意圖的指北針推論的立面方位（docs/research/leju-layout-analysis.md §2.2）
FACADES = {"TL": ["NW", "SW"], "TR": ["NW", "NE"], "BL": ["SE", "SW"], "BR": ["SE", "NE"], "ML": ["SW"], "MR": ["NE"]}

SOURCES = [
    # code, name, priority, access_method, rights, area_includes_parking
    ("SRC-591", "591", "PRIMARY", "MANUAL_ONLY", {"acquire": "RESTRICTED", "store": "UNKNOWN", "image_use": "UNKNOWN", "republish": "UNKNOWN"}, 1),
    ("SRC-LEJU", "樂居", "PRIMARY", "MANUAL_ONLY", {"acquire": "UNKNOWN", "store": "UNKNOWN", "image_use": "UNKNOWN", "republish": "UNKNOWN"}, None),
    ("SRC-SINYI", "信義房屋", "SECONDARY", "MANUAL_ONLY", {"acquire": "RESTRICTED", "store": "RESTRICTED", "image_use": "PROHIBITED", "republish": "PROHIBITED"}, 1),
    ("SRC-YUNGCHING", "永慶房屋", "SECONDARY", "MANUAL_ONLY", {"acquire": "UNKNOWN", "store": "UNKNOWN", "image_use": "UNKNOWN", "republish": "PROHIBITED"}, None),
    ("SRC-MANUAL", "使用者人工記錄", "PRIMARY", "MANUAL_ONLY", {"acquire": "ALLOWED", "store": "ALLOWED", "image_use": "UNKNOWN", "republish": "UNKNOWN"}, None),
    ("GOV-LVR", "內政部實價登錄", "SECONDARY", "PUBLIC_WEB", {"acquire": "ALLOWED", "store": "ALLOWED", "image_use": "UNKNOWN", "republish": "ALLOWED"}, 0),
]


def _floats(text):
    return [float(x) for x in re.findall(r"\d+(?:\.\d+)?", text or "")]


def _rooms(text):
    vals = sorted({int(x) for x in re.findall(r"(\d+)房", text or "")})
    return vals[0] if len(vals) == 1 else None


def load(conn, mapping_csv=MAPPING_CSV, area_csv=AREA_CSV):
    """建立名冊第一版（registry_version 1）。重複執行不會重建。"""
    if conn.execute("SELECT 1 FROM community WHERE code = 'lianshang-world'").fetchone():
        return current_version(conn)
    ts = now()
    cid = conn.execute(
        "INSERT INTO community (code, name, city, district, area_name, aliases) VALUES (?,?,?,?,?,?)",
        ("lianshang-world", "聯上世界", "桃園市", "大園區", "青埔", j(["聯上世界"])),
    ).lastrowid
    rv = conn.execute("INSERT INTO registry_version (label, created_at) VALUES (?, ?)", ("v1 M0 查證結果", ts)).lastrowid
    for code, name, prio, access, rights, incl in SOURCES:
        conn.execute(
            "INSERT INTO source (code, name, priority, access_method, rights, area_includes_parking) VALUES (?,?,?,?,?,?)",
            (code, name, prio, access, j(rights), incl),
        )
    areas = {r["sales_unit"]: r for r in csv.DictReader(open(area_csv, encoding="utf-8"))}
    for row in csv.DictReader(open(mapping_csv, encoding="utf-8")):
        code = row["sales_unit"]
        a = areas.get(code, {})
        vals = _floats(a.get("ping_ex_parking_observed", ""))
        area_source = "OBSERVED" if vals else None
        if not vals:  # 沒有坪數時，借用鏡像配對戶的坪數（推論，docs/research/leju-layout-analysis.md §3）
            for pair in re.findall(r"[A-D]\d", a.get("mirror_pair", "")):
                vals += _floats(areas.get(pair, {}).get("ping_ex_parking_observed", ""))
            area_source = "MIRROR_PAIR" if vals else None
        ut_status = "INFERRED" if vals else "UNKNOWN"
        ut = conn.execute(
            "INSERT INTO unit_type (community_id, code, area_ping_min, area_ping_max, parking_basis, rooms, record_status, layout_fingerprint)"
            " VALUES (?,?,?,?,?,?,?,?)",
            (cid, f"T-{code}", min(vals) if vals else None, max(vals) if vals else None, "EXCLUDED",
             _rooms(a.get("rooms_observed")), ut_status,
             j({"block_position": a.get("block_position") or None, "mirror_pair": a.get("mirror_pair") or "", "area_source": area_source})),
        ).lastrowid
        pos = a.get("block_position") or None
        su = conn.execute(
            "INSERT INTO sales_unit (community_id, building_label, unit_type_id, code, block_position, facades, record_status)"
            " VALUES (?,?,?,?,?,?,?)",
            (cid, code[0], ut, code, pos, j(FACADES.get(pos, [])), "TRANSCRIBED_UNVERIFIED"),
        ).lastrowid
        num = int(row["number"])
        au = conn.execute(
            "INSERT INTO address_unit (community_id, addr_key, raw_address, road, section, number, precision, origin, record_status)"
            " VALUES (?,?,?,?,?,?,?,?,?)",
            (cid, f"{ROAD}|{SECTION}|||{num}|||", f"{ROAD}一段{num}號", ROAD, SECTION, num, "NUMBER",
             "PRIOR_TRANSCRIPTION", "TRANSCRIBED_UNVERIFIED"),
        ).lastrowid
        conn.execute(
            "INSERT INTO registry_mapping (registry_version_id, mapping_type, from_type, from_id, to_type, to_id, record_status)"
            " VALUES (?,?,?,?,?,?,?)",
            (rv, "SALES_UNIT_TO_NUMBER", "SALES_UNIT", su, "ADDRESS_UNIT", au, "TRANSCRIBED_UNVERIFIED"),
        )
    ids = {r["code"]: r["id"] for r in conn.execute("SELECT id, code FROM sales_unit")}
    two_f = [ids[c] for c in ids if c not in FRONT_ROW_2F_ABSENT]
    rules = [
        ("R-2F", "SALES_UNIT", two_f, 2, 2, None, "TRANSCRIBED_UNVERIFIED"),
        ("R-ODD-VISIBLE", "COMMUNITY", [], 9, 13, [9, 11, 13], "TRANSCRIBED_UNVERIFIED"),
        ("R-3TO14", "COMMUNITY", [], 3, 14, None, "HYPOTHETICAL"),
    ]
    for code, scope, scope_ids, lo, hi, floors, status in rules:
        conn.execute(
            "INSERT INTO address_derivation_rule (community_id, registry_version_id, code, scope_type, scope_ids, floor_min, floor_max,"
            " floors, rule_type, floor_label_format, exceptions, record_status, created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (cid, rv, code, scope, j(scope_ids), lo, hi, j(floors) if floors else None, "SAME_NUMBER_ALL_FLOORS",
             "{floor}樓", "[]", status, ts),
        )
    # 06 §7：未驗證的 mapping 與推導規則都要有覆核任務（優先度最低，不打擾日常覆核）
    for (mid,) in conn.execute("SELECT id FROM registry_mapping WHERE record_status<>'VERIFIED'").fetchall():
        conn.execute("INSERT INTO review_task (task_type, registry_mapping_id, priority, created_at) VALUES ('REGISTRY_MAPPING',?,5,?)", (mid, ts))
    for (rid,) in conn.execute("SELECT id FROM address_derivation_rule WHERE record_status<>'VERIFIED'").fetchall():
        conn.execute("INSERT INTO review_task (task_type, derivation_rule_id, priority, created_at) VALUES ('DERIVATION_RULE',?,5,?)", (rid, ts))
    conn.commit()
    return rv


def set_status(conn, item_type, item_id, new_status, rationale, evidence_ids, review_id=None):
    """名冊 mapping 或推導規則的狀態變更（06 §4.1a、§8）：新 registry_version → REGISTRY_UPDATED → 全部刊登重新評估。"""
    table = {"REGISTRY_MAPPING": "registry_mapping", "DERIVATION_RULE": "address_derivation_rule"}[item_type]
    if new_status not in ("VERIFIED", "RETRACTED"):
        raise ValueError("只能改成 VERIFIED 或 RETRACTED")
    if new_status == "VERIFIED" and not evidence_ids:
        raise ValueError("驗證名冊項目必須附佐證")
    row = conn.execute(f"SELECT * FROM {table} WHERE id=?", (item_id,)).fetchone()
    if not row:
        raise LookupError(f"找不到 {item_type} {item_id}")
    prev = current_version(conn)
    rv = conn.execute("INSERT INTO registry_version (label, parent_id, created_at) VALUES (?,?,?)",
                      (f"{item_type} {item_id} → {new_status}：{rationale}"[:200], prev, now())).lastrowid
    conn.execute(f"UPDATE {table} SET record_status=?, evidence_ids=?, version=version+1 WHERE id=?",
                 (new_status, j(sorted(evidence_ids or [])), item_id))
    from .assess import assess_listing
    from .events import emit
    listings = [r[0] for r in conn.execute("SELECT id FROM listing ORDER BY id")]
    emit(conn, "REGISTRY_UPDATED", f"registry_updated:{rv}", {},
         {"registry_version_id": rv, "previous_registry_version_id": prev,
          "changes": [{"item_type": item_type, "item_id": item_id, "old_status": row["record_status"], "new_status": new_status}],
          "affected_listing_ids": listings}, caused_by={"review_id": review_id})
    for lid in listings:
        assess_listing(conn, lid, trigger="REGISTRY_UPDATED")
    conn.commit()
    return rv


def current_version(conn):
    return conn.execute("SELECT MAX(id) FROM registry_version").fetchone()[0]


def community_id(conn):
    return conn.execute("SELECT id FROM community WHERE code = 'lianshang-world'").fetchone()[0]


def rule_floors(rule):
    floors = unj(rule["floors"])
    return floors if floors else list(range(rule["floor_min"], rule["floor_max"] + 1))


def rule_applies(rule, sales_unit_id):
    if rule["record_status"] == "RETRACTED":
        return False
    return rule["scope_type"] == "COMMUNITY" or sales_unit_id in unj(rule["scope_ids"], [])


def unit_floors(conn, sales_unit_id):
    """依非 RETRACTED 的推導規則，回傳此戶別可能存在的樓層與支持的規則（不依總樓高推定）。"""
    floors = {}
    for rule in conn.execute("SELECT * FROM address_derivation_rule"):
        if not rule_applies(rule, sales_unit_id):
            continue
        for f in rule_floors(rule):
            floors.setdefault(f, []).append(rule)
    # 2F 只有 R-2F 範圍內的戶別（前排 6 戶不存在）；社區範圍規則不涵蓋 2F
    return dict(sorted(floors.items()))


def derivation_verified(conn, sales_unit_id, floor):
    """06 §4.1a (b)：戶別→號 VERIFIED，且有 VERIFIED 規則適用於該樓層。"""
    m = conn.execute(
        "SELECT record_status FROM registry_mapping WHERE mapping_type='SALES_UNIT_TO_NUMBER' AND from_id=?", (sales_unit_id,)
    ).fetchone()
    if not m or m["record_status"] != "VERIFIED":
        return False
    for rule in conn.execute("SELECT * FROM address_derivation_rule WHERE record_status='VERIFIED'"):
        if rule_applies(rule, sales_unit_id) and floor in rule_floors(rule):
            return True
    return False


def ensure_dwelling(conn, sales_unit_id, floor):
    row = conn.execute("SELECT id FROM dwelling WHERE sales_unit_id=? AND floor=?", (sales_unit_id, floor)).fetchone()
    if row:
        return row["id"]
    return conn.execute(
        "INSERT INTO dwelling (sales_unit_id, floor, record_status) VALUES (?,?, 'HYPOTHETICAL')", (sales_unit_id, floor)
    ).lastrowid


def units(conn):
    return conn.execute(
        "SELECT su.*, ut.area_ping_min, ut.area_ping_max, ut.rooms AS ut_rooms, ut.record_status AS ut_status,"
        " CASE WHEN m.record_status='RETRACTED' THEN NULL ELSE au.number END AS number, m.record_status AS mapping_status"
        " FROM sales_unit su JOIN unit_type ut ON ut.id = su.unit_type_id"
        " JOIN registry_mapping m ON m.from_id = su.id AND m.mapping_type='SALES_UNIT_TO_NUMBER'"
        " JOIN address_unit au ON au.id = m.to_id ORDER BY su.code"
    ).fetchall()
