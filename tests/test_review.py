"""人工確認與覆核（docs/06 §5.2–5.3、§7；對應 T-ADDR-09*、T-ADDR-11*、T-REV-*、T-EVD-11）。"""

import pytest

from jev import review
from jev.assess import assess_listing

CAP = {"captured_by": "user", "captured_at": "2026-11-01T06:00:00Z", "method": "PHOTO"}
DIRECT = {"field": "full_address", "value": {"address_text": "高鐵南路一段79號12樓"}, "description": "現場門牌照片（只存描述）",
          "evidence_role": "DIRECT_ADDRESS", "derivation_type": "ORIGINAL", "capture": CAP}
LINK = {"field": "listing_identity", "value": {"listing_ref_seen": "591#T0001"}, "description": "帶看仲介出示刊登編號",
        "evidence_role": "LISTING_LINKAGE", "derivation_type": "ORIGINAL", "link_method": "AGENT_VIEWING_WITH_LISTING_REF",
        "capture": {**CAP, "method": "SITE_VISIT_NOTE"}}


@pytest.fixture
def a2(conn, add):
    lid, out = add(sales_unit_text="A2", floor_text="12F")
    task = conn.execute("SELECT * FROM review_task WHERE listing_id=? AND task_type='ADDRESS_CANDIDATE'", (lid,)).fetchone()
    au = conn.execute("INSERT INTO address_unit (community_id, addr_key, raw_address, road, section, number, floor, precision, origin,"
                      " record_status) VALUES (1,'高鐵南路|1|||79||12|','高鐵南路一段79號12樓','高鐵南路',1,79,'12','FLOOR','USER_OBSERVATION','INFERRED')").lastrowid
    return lid, task, au


def test_sufficient_direct_and_linkage_confirms_full_address_only(conn, a2):  # T-ADDR-09a、09g
    lid, task, au = a2
    out = review.submit_decision(conn, task["id"], task["version"], "SET_UNIT", "帶看時現場門牌 79 號 12 樓", [DIRECT, LINK], address_unit_id=au)
    assert out["support_assessment"]["result"] == "SUFFICIENT"
    a = assess_listing(conn, lid, trigger="REVIEW")["address"]
    f = a["resolved_fields"]
    assert a["outcome"] == "CONFIRMED" and f["full_address"]["basis"] == "HUMAN_REVIEW"
    assert f["floor"]["status"] == "CONFIRMED"
    assert f["sales_unit"]["status"] == "NARROWED"  # 不因門牌確認而宣稱戶別 mapping 已驗證


def test_address_text_only_is_insufficient(conn, a2):  # T-ADDR-09b
    lid, task, au = a2
    item = {**DIRECT, "evidence_role": "OTHER", "description": "使用者輸入的地址文字"}
    out = review.submit_decision(conn, task["id"], task["version"], "SET_UNIT", "應該是 79 號 12 樓", [item], address_unit_id=au)
    assert out["support_assessment"]["result"] == "INSUFFICIENT"
    assert set(out["support_assessment"]["unmet_conditions"]) >= {"H1", "H2"}
    assert conn.execute("SELECT status FROM review_task WHERE id=?", (task["id"],)).fetchone()[0] == "OPEN"
    assert assess_listing(conn, lid)["address"]["outcome"] == "SINGLE_CANDIDATE"


def test_photo_without_linkage(conn, a2):  # T-ADDR-09c
    lid, task, au = a2
    out = review.submit_decision(conn, task["id"], task["version"], "SET_UNIT", "只有門牌照片", [DIRECT], address_unit_id=au)
    assert out["support_assessment"]["unmet_conditions"] == ["H2"]


def test_stale_version_rejected(conn, a2):  # T-REV-01
    _, task, au = a2
    with pytest.raises(review.ReviewError) as e:
        review.submit_decision(conn, task["id"], task["version"] + 5, "ABSTAIN", "版本過期測試")
    assert e.value.status == 412 and e.value.code == "STALE_VERSION"


def test_set_unit_requires_address_unit_and_support(conn, a2):  # T-REV-02、03、05
    _, task, _ = a2
    with pytest.raises(review.ReviewError) as e:
        review.submit_decision(conn, task["id"], task["version"], "SET_UNIT", "缺少 address_unit_id", [DIRECT])
    assert e.value.code == "VALIDATION_ERROR"
    with pytest.raises(review.ReviewError):
        review.submit_decision(conn, task["id"], task["version"], "CONFIRM_CANDIDATE", "沒有佐證的確認", candidate_id=1)
    with pytest.raises(review.ReviewError) as e:
        review.submit_decision(conn, task["id"], task["version"], "CONFIRM_SAME", "不適用的決定")
    assert e.value.code == "DECISION_NOT_ALLOWED"


def test_retract_falls_back_to_rules(conn, a2):  # T-ADDR-11b、T-EVD-11
    lid, task, au = a2
    out = review.submit_decision(conn, task["id"], task["version"], "SET_UNIT", "帶看時現場門牌 79 號 12 樓", [DIRECT, LINK], address_unit_id=au)
    cited = review.add_user_evidence(conn, lid, {**DIRECT, "derivation_type": "REVIEW_CITATION", "capture": None,
                                                  "derived_from": [{"ref_type": "EVIDENCE", "id": out["created_evidence_ids"][0], "role": "CITES"}],
                                                  "description": "第二次覆核引用同一張照片"})
    origins = conn.execute("SELECT origin_ids FROM evidence WHERE id IN (?,?)", (out["created_evidence_ids"][0], cited)).fetchall()
    assert origins[0][0] == origins[1][0]  # T-EVD-06：再次引用不增加根來源
    review.retract_review(conn, out["id"], "照片拍錯戶，撤回")
    status = conn.execute("SELECT status, status_reason FROM evidence WHERE id=?", (cited,)).fetchone()
    assert tuple(status) == ("RETRACTED", "UPSTREAM_RETRACTED")
    assert assess_listing(conn, lid)["address"]["outcome"] == "SINGLE_CANDIDATE"


def test_abstain(conn, add):  # T-ADDR-10
    lid, _ = add(building_ping=43.92, parking_ping=7.81, floor_text="12F")
    task = conn.execute("SELECT * FROM review_task WHERE listing_id=? AND task_type='ADDRESS_CANDIDATE'", (lid,)).fetchone()
    review.submit_decision(conn, task["id"], task["version"], "MARK_INSUFFICIENT", "照片太少，先不判斷")
    assess_listing(conn, lid)
    assert _task(conn, "ADDRESS_CANDIDATE", listing_id=lid) is None  # 同樣內容不重複開任務
    lid2, _ = add(building_ping=43.92, parking_ping=7.81, floor_text="12F")
    task = _task(conn, "ADDRESS_CANDIDATE", listing_id=lid2)
    review.submit_decision(conn, task["id"], task["version"], "ABSTAIN", "D2 與 D5 照片無法區分")
    assert assess_listing(conn, lid2)["address"]["outcome"] == "ABSTAINED"


def _task(conn, task_type, **where):
    q = "SELECT * FROM review_task WHERE task_type=? AND status='OPEN'" + "".join(f" AND {k}=?" for k in where)
    return conn.execute(q, (task_type, *where.values())).fetchone()


def test_reject_candidate_excludes_only_that_candidate(conn, add):  # T-REV-04
    lid, out = add(building_ping=43.92, parking_ping=7.81, floor_text="12樓/14樓")
    task = _task(conn, "ADDRESS_CANDIDATE", listing_id=lid)
    d2 = conn.execute("SELECT c.id FROM candidate c JOIN dwelling d ON d.id=c.dwelling_id JOIN sales_unit su ON su.id=d.sales_unit_id"
                      " WHERE c.listing_id=? AND su.code='D2' AND d.floor=12", (lid,)).fetchone()[0]
    with pytest.raises(review.ReviewError):
        review.submit_decision(conn, task["id"], task["version"], "REJECT_CANDIDATE", "缺少 candidate_id")
    review.submit_decision(conn, task["id"], task["version"], "REJECT_CANDIDATE", "照片是 D5 的鏡像方向", candidate_id=d2)
    a = assess_listing(conn, lid)["address"]
    rejected = [c for c in a["candidates"] if c["sales_unit"] == "D2" and c["floor"] == 12][0]
    assert rejected["excluded_by_rule"] == "HUMAN_REJECT"
    assert a["outcome"] == "SINGLE_CANDIDATE"
    assert a["resolved_fields"]["full_address"]["status"] == "NARROWED"  # 排除不是確認


def test_membership_confirmation_and_fallback(conn, add):  # T-MEM-05
    lid, out = add(address_text="高鐵南路一段111號", building_ping=37.8)
    assert out["membership"]["outcome"] == "LIKELY_MEMBER"
    task = _task(conn, "COMMUNITY_MEMBERSHIP", listing_id=lid)
    rv = review.submit_decision(conn, task["id"], task["version"], "CONFIRM_MEMBER", "現場確認 111 號屬於聯上世界管委會",
                                [{**DIRECT, "field": "community", "value": {"community": "聯上世界"}, "evidence_role": "OTHER",
                                  "description": "社區公告欄照片"}])
    row = conn.execute("SELECT outcome, basis FROM assessment WHERE listing_id=? AND subject_type='COMMUNITY_MEMBERSHIP' AND status='CURRENT'",
                       (lid,)).fetchone()
    assert tuple(row) == ("MEMBER", "HUMAN_REVIEW")
    review.retract_review(conn, rv["id"], "照片其實是隔壁社區")
    assert assess_listing(conn, lid)["membership"]["outcome"] == "LIKELY_MEMBER"


def test_match_decisions(conn, add):  # T-IDN-02、T-REV-06
    from jev import identity
    a, _ = add(building_ping=43.92, parking_ping=7.81, floor_text="9樓/14樓", rooms=2)
    b, _ = add(mode="INCREMENTAL", building_ping=43.92, parking_ping=7.81, floor_text="9樓/14樓", rooms=2, is_new_listing=True)
    c, _ = add(building_ping=43.92, parking_ping=7.81, floor_text="9樓/14樓", rooms=2)
    identity.evaluate_pairs(conn)
    t_ab = conn.execute("SELECT t.* FROM review_task t JOIN identity_match m ON m.id=t.identity_match_id WHERE m.listing_a_id=? AND m.listing_b_id=?",
                        (a, b)).fetchone()
    t_ac = conn.execute("SELECT t.* FROM review_task t JOIN identity_match m ON m.id=t.identity_match_id WHERE m.listing_a_id=? AND m.listing_b_id=?",
                        (a, c)).fetchone()
    with pytest.raises(review.ReviewError):
        review.submit_decision(conn, t_ab["id"], t_ab["version"], "CONFIRM_SAME", "沒有佐證的確認")
    photo = {**DIRECT, "field": "listing_identity", "value": {"same_photos": True}, "evidence_role": "OTHER", "description": "同一組室內照片"}
    review.submit_decision(conn, t_ab["id"], t_ab["version"], "CONFIRM_SAME", "兩則刊登使用同一組室內照片", [photo])
    pa, pb = [conn.execute("SELECT property_id FROM listing WHERE id=?", (x,)).fetchone()[0] for x in (a, b)]
    assert pa == pb
    assert conn.execute("SELECT COUNT(*) FROM listing_event WHERE event_type='DWELLING_NEW_LISTING'").fetchone()[0] == 1
    review.submit_decision(conn, t_ac["id"], t_ac["version"], "REJECT_SAME", "一則是 D2、一則是 D5")
    assert conn.execute("SELECT status FROM identity_match WHERE id=?", (t_ac["identity_match_id"],)).fetchone()[0] == "REJECTED"
    identity.evaluate_pairs(conn)
    identity.evaluate_pairs(conn)
    assert conn.execute("SELECT COUNT(*) FROM review_task WHERE task_type='LISTING_MATCH' AND identity_match_id=? AND status='OPEN'",
                        (t_ac["identity_match_id"],)).fetchone()[0] == 0  # 已拒絕的配對不會每次重問
