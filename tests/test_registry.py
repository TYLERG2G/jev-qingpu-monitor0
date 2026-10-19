"""名冊驗證與系統確認（06 §4.1a、§5.1 K1–K5、§8；對應 T-REG-02、03、T-RULE-04、06、T-ADDR-01）。"""

import pytest
from conftest import live, validate

from jev import review
from jev.assess import assess_listing
from jev.events import to_api

CAP = {"captured_by": "user", "captured_at": "2026-11-01T06:00:00Z", "method": "DOCUMENT"}
DOC = {"field": "registry", "value": {"note": "管委會提供的戶別門牌對照表"}, "description": "管委會門牌對照表（只存描述）",
       "evidence_role": "OTHER", "derivation_type": "ORIGINAL", "capture": CAP}


def _task(conn, task_type, code):
    col, q = ("registry_mapping_id", "SELECT m.id FROM registry_mapping m JOIN sales_unit su ON su.id=m.from_id WHERE su.code=?") \
        if task_type == "REGISTRY_MAPPING" else ("derivation_rule_id", "SELECT id FROM address_derivation_rule WHERE code=?")
    item = conn.execute(q, (code,)).fetchone()[0]
    return conn.execute(f"SELECT * FROM review_task WHERE task_type=? AND {col}=? AND status='OPEN'", (task_type, item)).fetchone()


def _verify(conn, task_type, code, decision):
    t = _task(conn, task_type, code)
    return review.submit_decision(conn, t["id"], t["version"], decision, "依管委會門牌對照表核對", [DOC])


def test_registry_tasks_exist_and_match_contract(conn):
    rows = conn.execute("SELECT * FROM review_task WHERE task_type IN ('REGISTRY_MAPPING','DERIVATION_RULE')").fetchall()
    assert len(rows) == 17 + 3 and all(r["priority"] == 5 for r in rows)
    from jev.serialize import review_task
    for r in rows:
        validate("ReviewTask", review_task(conn, r))


def test_verify_needs_evidence(conn):
    t = _task(conn, "REGISTRY_MAPPING", "A2")
    with pytest.raises(review.ReviewError):
        review.submit_decision(conn, t["id"], t["version"], "VERIFY_MAPPING", "沒有佐證的驗證")


def test_verified_derivation_allows_system_confirmation_and_retract_falls_back(conn, add):  # T-REG-02、T-RULE-06、T-ADDR-01、T-RULE-04
    lid, out = add(address_text="桃園市大園區高鐵南路一段79號12樓")
    assert out["address"]["outcome"] == "MULTIPLE_CANDIDATES"
    _verify(conn, "REGISTRY_MAPPING", "A2", "VERIFY_MAPPING")
    _verify(conn, "DERIVATION_RULE", "R-3TO14", "VERIFY_RULE")
    a = assess_listing(conn, lid)["address"]
    assert a["outcome"] == "MULTIPLE_CANDIDATES"  # 只有 A2 驗證：其他戶的推導未驗證 → X5 仍是軟反駁 → K1 不成立
    for code in [r[0] for r in conn.execute("SELECT code FROM sales_unit WHERE code<>'A2' ORDER BY code")]:
        _verify(conn, "REGISTRY_MAPPING", code, "VERIFY_MAPPING")
    ev = conn.execute("SELECT * FROM listing_event WHERE event_type='REGISTRY_UPDATED' ORDER BY id").fetchall()
    assert len(ev) == 18
    for e in ev[:3]:
        validate("Event", to_api(e))
    a = assess_listing(conn, lid)["address"]
    assert a["outcome"] == "CONFIRMED" and a["resolved_fields"]["full_address"]["basis"] == "SYSTEM_RULES"
    assert a["resolved_fields"]["full_address"]["value"] == "高鐵南路一段79號12樓"
    assert len(live({"address": a})) == 1 and a["resolved_fields"]["sales_unit"]["status"] == "NARROWED"
    from jev import registry
    rule_id = conn.execute("SELECT id FROM address_derivation_rule WHERE code='R-3TO14'").fetchone()[0]
    registry.set_status(conn, "DERIVATION_RULE", rule_id, "RETRACTED", "規則有例外，先撤回", [])
    assert assess_listing(conn, lid)["address"]["outcome"] != "CONFIRMED"


def test_reject_mapping_removes_number(conn, add):  # T-REG-03
    lid, _ = add(sales_unit_text="A2", floor_text="12F")
    _verify(conn, "REGISTRY_MAPPING", "A2", "REJECT_MAPPING")
    a = assess_listing(conn, lid)["address"]
    assert a["outcome"] == "SINGLE_CANDIDATE"
    assert a["resolved_fields"]["sales_unit"]["value"] == "A2"
    assert a["resolved_fields"]["full_address"]["status"] == "UNKNOWN"  # 戶別→號被撤回，不能再推出門牌
