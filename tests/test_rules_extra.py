"""10 §3 其餘適用於人工記錄模式的案例；10 的虛構名冊（T45／T38／R-ALL）改用實際的聯上世界名冊表達同一條規則。"""

import json

import pytest
from conftest import live

from jev import identity, notify, review
from jev.assess import assess_listing
from jev.lifecycle import record
from jev.normalize import parse_floor

CAP = {"captured_by": "user", "captured_at": "2026-11-01T06:00:00Z", "method": "PHOTO"}


def _addr_task(conn, lid):
    return conn.execute("SELECT * FROM review_task WHERE listing_id=? AND task_type='ADDRESS_CANDIDATE' AND status='OPEN'", (lid,)).fetchone()


def _photo(text="高鐵南路一段79號12樓"):
    return {"field": "full_address", "value": {"address_text": text}, "description": "現場門牌照片（只存描述）",
            "evidence_role": "DIRECT_ADDRESS", "derivation_type": "ORIGINAL", "capture": CAP}


def _link(method="AGENT_VIEWING_WITH_LISTING_REF"):
    return {"field": "listing_identity", "value": {"listing_ref_seen": "591#T0001"}, "description": "帶看時看到的刊登編號",
            "evidence_role": "LISTING_LINKAGE", "derivation_type": "ORIGINAL", "link_method": method,
            "capture": {**CAP, "method": "SITE_VISIT_NOTE"}}


def _au(conn, number=79, floor=12):
    return conn.execute("INSERT INTO address_unit (community_id, addr_key, raw_address, road, section, number, floor, precision, origin,"
                        " record_status) VALUES (1,?,?,'高鐵南路',1,?,?,'FLOOR','USER_OBSERVATION','INFERRED')",
                        (f"高鐵南路|1|||{number}||{floor}|", f"高鐵南路一段{number}號{floor}樓", number, str(floor))).lastrowid


def test_registry_loads_as_unverified(conn):  # T-REG-01
    rows = conn.execute("SELECT DISTINCT record_status FROM registry_mapping").fetchall()
    assert {r[0] for r in rows} == {"TRANSCRIBED_UNVERIFIED"}
    rules = {r["code"]: r["record_status"] for r in conn.execute("SELECT code, record_status FROM address_derivation_rule")}
    assert rules["R-3TO14"] == "HYPOTHETICAL" and rules["R-2F"] == "TRANSCRIBED_UNVERIFIED"


def test_floor_range_parsing():  # T-NORM-09、T-NORM-10
    f = parse_floor("10~15樓")
    assert (f.floor_min, f.floor_max, f.floor_exact) == (10, 15, False)
    top = parse_floor("頂樓/14樓")
    assert top.floor_exact is False  # 頂樓換算只能當推論，不是精確樓層


def test_explicit_address_unverified_is_soft(add):  # T-ADDR-02、T-RULE-02（mapping 未驗證 → X5 軟反駁）
    _, out = add(address_text="桃園市大園區高鐵南路一段79號12樓")
    c = live(out)
    assert out["membership"]["outcome"] == "MEMBER"  # T-MEM-07：M1u 0.6 + M2 + M3 → 以分數判定
    assert (c[0]["sales_unit"], c[0]["floor"]) == ("A2", 12)
    assert out["address"]["outcome"] == "MULTIPLE_CANDIDATES"  # 其他戶別只被軟反駁、沒有被排除
    assert c[0]["score"] - c[1]["score"] >= 0.3
    assert any(h["rule_code"] == "X5s" for x in c[1:] for h in x["rule_hits"])


def test_floor_range_candidates(add):  # T-ADDR-05
    _, out = add(building_ping=37.775, parking_ping=7.807, floor_text="10~12樓")
    assert out["address"]["outcome"] == "MULTIPLE_CANDIDATES" and out["address"]["candidate_count"] == 3
    assert out["address"]["resolved_fields"]["floor"]["status"] == "AMBIGUOUS"


def test_unknown_sales_unit_no_candidate(add):  # T-ADDR-08
    _, out = add(sales_unit_text="A4", floor_text="12F")
    assert out["address"]["outcome"] == "NO_CANDIDATE"
    assert out["membership"]["outcome"] == "MEMBER"  # 歸屬不受影響


@pytest.mark.parametrize("items, unmet", [
    ([_photo(), _link("OTHER")], ["H2"]),  # T-ADDR-09d：「感覺很像」不是強連結
    ([_photo("高鐵南路一段79號15樓"), _link()], ["H5", "H6"]),  # T-ADDR-09f：與刊登的 12F 矛盾
])
def test_insufficient_support(conn, add, items, unmet):
    lid, _ = add(sales_unit_text="A2", floor_text="12F")
    task = _addr_task(conn, lid)
    out = review.submit_decision(conn, task["id"], task["version"], "SET_UNIT", "依佐證設定門牌", items, address_unit_id=_au(conn))
    assert out["support_assessment"]["result"] == "INSUFFICIENT" and out["support_assessment"]["unmet_conditions"] == unmet


def test_repackaged_system_inference_is_not_evidence(conn, add):  # T-ADDR-09e、T-EVD-12
    lid, out = add(sales_unit_text="A2", floor_text="12F")
    task = _addr_task(conn, lid)
    cand = conn.execute("SELECT candidate_id FROM assessment_candidate WHERE assessment_id=? AND excluded=0",
                        (out["address_assessment_id"],)).fetchone()[0]
    repack = {**_photo(), "derivation_type": "REVIEW_CITATION", "capture": None, "description": "引用系統候選清單",
              "derived_from": [{"ref_type": "CANDIDATE", "id": cand, "role": "CITES"}]}
    res = review.submit_decision(conn, task["id"], task["version"], "CONFIRM_CANDIDATE", "系統說是這戶", [repack], candidate_id=cand)
    assert res["support_assessment"]["result"] == "INSUFFICIENT"
    assert {"H1", "H2", "H4"} <= set(res["support_assessment"]["unmet_conditions"])


def test_photo_layout_only_cannot_confirm(conn, add):  # T-PHOTO-05
    lid, out = add(building_ping=37.775, parking_ping=7.807, floor_text="12F/14F", rooms=2)
    task = _addr_task(conn, lid)
    cand = conn.execute("SELECT candidate_id FROM assessment_candidate WHERE assessment_id=? AND excluded=0",
                        (out["address_assessment_id"],)).fetchone()[0]
    feat = {"field": "layout", "value": {"sales_unit": "C3", "match": True}, "description": "照片格局與 C3 一致",
            "evidence_role": "FEATURE_OBSERVATION", "derivation_type": "ORIGINAL", "capture": CAP}
    res = review.submit_decision(conn, task["id"], task["version"], "CONFIRM_CANDIDATE", "照片格局一致", [feat], candidate_id=cand)
    assert res["support_assessment"]["result"] == "INSUFFICIENT"
    assert assess_listing(conn, lid)["address"]["resolved_fields"]["sales_unit"]["status"] == "NARROWED"


def test_retract_only_linkage(conn, add):  # T-ADDR-11c
    lid, _ = add(sales_unit_text="A2", floor_text="12F")
    task = _addr_task(conn, lid)
    out = review.submit_decision(conn, task["id"], task["version"], "SET_UNIT", "帶看時現場門牌", [_photo(), _link()], address_unit_id=_au(conn))
    link_id = [e for e in out["created_evidence_ids"]
               if conn.execute("SELECT evidence_role FROM evidence WHERE id=?", (e,)).fetchone()[0] == "LISTING_LINKAGE"][0]
    review.retract_evidence_cascade(conn, [link_id])
    a = assess_listing(conn, lid, trigger="EVIDENCE_RETRACTED")["address"]
    assert a["outcome"] == "SINGLE_CANDIDATE"
    assert review.human_support(conn, lid)["unmet"] == ["H2"]


def test_rerun_same_import_has_no_new_events(conn, add):  # T-EVT-01
    add(building_ping=37.8)
    n = conn.execute("SELECT COUNT(*) FROM listing_event").fetchone()[0]
    rec = {"source_code": "SRC-591", "external_id": "T0001", "total_price": 20000000, "building_ping": 37.8,
           "platform_community_ref": "591:9488", "community_text": "聯上世界", "address_text": "桃園市大園區高鐵南路一段",
           "parking_included": "INCLUDED"}
    assert record(conn, rec)["changed"] is False
    assert conn.execute("SELECT COUNT(*) FROM listing_event").fetchone()[0] == n


def test_new_listing_after_initial_notifies(conn, add, tmp_path):  # T-LIFE-19
    add(building_ping=37.8)
    add(mode="INCREMENTAL", building_ping=51.37, parking_ping=7.81, is_new_listing=True)
    sent = notify.process(conn, tmp_path)
    assert len(sent) == 1
    ev = conn.execute("SELECT * FROM listing_event WHERE event_type='LISTING_NEW_CONFIRMED'").fetchone()
    assert ev["notify_eligible"] == 1


def test_split_correction_points_to_original_and_no_new_task(conn, add, tmp_path):  # T-NTF-02、T-IDN-07
    a, _ = add(building_ping=43.92, parking_ping=7.81, floor_text="9樓/14樓", rooms=2)
    b, _ = add(mode="INCREMENTAL", building_ping=43.92, parking_ping=7.81, floor_text="9樓/14樓", rooms=2, is_new_listing=True)
    pa, pb = [conn.execute("SELECT property_id FROM listing WHERE id=?", (x,)).fetchone()[0] for x in (a, b)]
    identity.merge(conn, [pa, pb], "同一組室內照片", [1])
    notify.process(conn, tmp_path)
    original = conn.execute("SELECT n.* FROM notification n JOIN listing_event e ON e.event_id=n.event_id"
                            " WHERE e.event_type='DWELLING_NEW_LISTING'").fetchone()
    identity.split(conn, pa, [[a], [b]], "其實是 D2 與 D5 兩戶")
    corr = conn.execute("SELECT * FROM notification WHERE notification_kind='CORRECTION'").fetchone()
    assert corr["corrects_notification_id"] == original["id"]
    assert conn.execute("SELECT rendered FROM notification WHERE id=?", (original["id"],)).fetchone()[0] == original["rendered"]
    identity.evaluate_pairs(conn)
    m = conn.execute("SELECT id, status, conflicts FROM identity_match WHERE listing_a_id=? AND listing_b_id=?", (a, b)).fetchone()
    assert m["status"] == "REJECTED" and "F7" in json.loads(m["conflicts"])
    assert conn.execute("SELECT COUNT(*) FROM review_task WHERE identity_match_id=? AND status='OPEN'", (m["id"],)).fetchone()[0] == 0


def _origin(conn, desc):
    return conn.execute("INSERT INTO source_origin (origin_type, asserting_party_type, asserting_party_key, description)"
                        " VALUES ('PLATFORM','AGENT',?,?)", (desc, desc)).lastrowid


def _ev(conn, lid, origin):
    return conn.execute("INSERT INTO evidence (kind, origin, listing_id, field, value, locator, observed_at, origin_ids)"
                        " VALUES ('LISTING_FIELD','LISTING_SNAPSHOT',?,'floor','12','{\"type\":\"FREE_TEXT\",\"value\":\"x\"}',?,?)",
                        (lid, "2026-10-18T00:00:00Z", json.dumps([origin]))).lastrowid


def test_independence_needs_positive_record(conn, add):  # T-EVD-09、T-EVD-10
    a, _ = add(building_ping=37.8)
    x, y = _origin(conn, "仲介 X"), _origin(conn, "仲介 Y")
    ea, eb = _ev(conn, a, x), _ev(conn, a, y)
    assert identity.origins_independent(conn, [ea], [eb]) is False  # UNKNOWN 不算獨立（D-011）
    with pytest.raises(ValueError):
        identity.add_origin_relation(conn, x, y, "INDEPENDENT", "兩家仲介各自帶看")  # 沒有佐證
    rid = identity.add_origin_relation(conn, y, x, "INDEPENDENT", "兩家仲介各自帶看，照片不同", [ea])
    assert identity.origins_independent(conn, [ea], [eb]) is True
    identity.retract_origin_relation(conn, rid)
    assert identity.origins_independent(conn, [ea], [eb]) is False
    ec = _ev(conn, a, x)
    assert identity.origins_independent(conn, [ea], [ec]) is False  # 同一根來源（T-EVD-08 的原則）


def test_confirmation_does_not_spread_to_group(conn, add):  # T-IDN-06
    a, _ = add(sales_unit_text="A2", floor_text="12F")
    b, _ = add(sales_unit_text="A2", floor_text="12F")
    task = _addr_task(conn, a)
    review.submit_decision(conn, task["id"], task["version"], "SET_UNIT", "帶看時現場門牌", [_photo(), _link()], address_unit_id=_au(conn))
    pa, pb = [conn.execute("SELECT property_id FROM listing WHERE id=?", (x,)).fetchone()[0] for x in (a, b)]
    identity.merge(conn, [pa, pb], "同一組室內照片", [1])
    assert assess_listing(conn, a)["address"]["outcome"] == "CONFIRMED"
    assert assess_listing(conn, b)["address"]["outcome"] != "CONFIRMED"  # 只得到 S8 等級的支持，不是 K3


def test_later_listing_is_not_anchor_for_earlier_new(conn, add):  # T-LIFE-18
    l22, _ = add(mode="INCREMENTAL", building_ping=37.8, is_new_listing=True, observed_at="2026-10-20T00:00:00Z")
    l23, _ = add(mode="INCREMENTAL", building_ping=37.8, observed_at="2026-10-22T00:00:00Z")
    p22, p23 = [conn.execute("SELECT property_id FROM listing WHERE id=?", (x,)).fetchone()[0] for x in (l22, l23)]
    identity.merge(conn, [p22, p23], "同一組室內照片", [1])
    assert conn.execute("SELECT COUNT(*) FROM listing_event WHERE event_type='DWELLING_NEW_LISTING'").fetchone()[0] == 0
