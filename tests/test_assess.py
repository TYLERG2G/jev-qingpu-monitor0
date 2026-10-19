"""社區歸屬與完整門牌（docs/06、16；對應 docs/10 的 T-MEM、T-ADDR、T-RULE、T-PHOTO）。"""

from conftest import live

from jev.review import add_user_evidence


def test_member_platform_name_road(add):  # T-MEM-01
    _, out = add(building_ping=37.8)
    assert out["membership"]["outcome"] == "MEMBER" and out["membership"]["rule_score"] == 0.9


def test_name_and_road_only_undetermined(add):  # T-MEM-02
    _, out = add(platform_community_ref=None, building_ping=37.8)
    assert out["membership"]["outcome"] == "UNDETERMINED"
    assert out["address"]["outcome"] == "NOT_EVALUATED"


def test_near_phrase_refutes(add):  # T-MEM-03
    _, out = add(community_text=None, title="近聯上世界兩房", building_ping=37.8)
    assert out["membership"]["rule_score"] == 0.4 and out["membership"]["outcome"] == "UNDETERMINED"


def test_number_not_in_registry_is_gap_not_refute(add):  # T-MEM-04
    _, out = add(address_text="高鐵南路一段111號", building_ping=37.8)
    assert out["membership"]["outcome"] == "LIKELY_MEMBER"
    assert "REGISTRY_GAP" in out["membership"]["flags"]


def test_other_road_full_address_is_non_member(add):  # N1
    _, out = add(address_text="桃園市大園區領航南路二段366號5樓")
    assert out["membership"]["outcome"] == "NON_MEMBER"


def test_map_address_does_not_trigger_n1(add):  # Q-029 建議預設
    _, out = add(map_address="桃園市中壢區領航南路二段366號", building_ping=79.05, parking_ping=15.08, floor_text="12樓/14樓")
    assert out["membership"]["outcome"] == "MEMBER" and "MAP_ADDRESS_IGNORED" in out["membership"]["flags"]


def test_unique_area_single_candidate_not_confirmed(add):  # T-PHOTO-01
    _, out = add(building_ping=37.775, parking_ping=7.807, floor_text="12F/14F", rooms=2)
    c = live(out)
    assert out["address"]["outcome"] == "SINGLE_CANDIDATE"
    assert (c[0]["sales_unit"], c[0]["number"], c[0]["floor"]) == ("C3", 97, 12)
    assert out["address"]["resolved_fields"]["full_address"]["status"] == "NARROWED"  # 推導未驗證 → 不是 CONFIRMED


def test_mirror_pair_needs_more_than_area(add):  # 鏡像配對
    _, out = add(building_ping=43.92, parking_ping=7.81, floor_text="12樓/14樓")
    assert out["address"]["outcome"] == "MULTIPLE_CANDIDATES"
    assert {(c["sales_unit"], c["floor"]) for c in live(out)} == {("D2", 12), ("D5", 12)}


def test_photo_feature_ranks_but_never_excludes(conn, add):  # T-PHOTO-02
    lid, _ = add(building_ping=43.92, parking_ping=7.81, floor_text="12樓/14樓")
    add_user_evidence(conn, lid, {"field": "layout", "value": {"sales_unit": "D5", "match": True}, "description": "照片鏡像方向符合 D5",
                                  "evidence_role": "FEATURE_OBSERVATION", "derivation_type": "ORIGINAL",
                                  "capture": {"captured_by": "user", "captured_at": "2026-10-18T00:00:00Z", "method": "PHOTO"}})
    add_user_evidence(conn, lid, {"field": "layout", "value": {"sales_unit": "D2", "match": False}, "description": "照片鏡像方向與 D2 相反",
                                  "evidence_role": "FEATURE_OBSERVATION", "derivation_type": "ORIGINAL",
                                  "capture": {"captured_by": "user", "captured_at": "2026-10-18T00:00:00Z", "method": "PHOTO"}})
    from jev.assess import assess_listing
    out = assess_listing(conn, lid, trigger="EVIDENCE_CHANGED")
    c = live(out)
    assert c[0]["sales_unit"] == "D5" and {x["sales_unit"] for x in c} == {"D2", "D5"}  # D2 只降分
    assert out["address"]["outcome"] == "MULTIPLE_CANDIDATES"


def test_floor_unknown_identifies_unit_only(add):  # T-PHOTO-04
    _, out = add(building_ping=37.775, parking_ping=7.807)
    f = out["address"]["resolved_fields"]
    assert f["sales_unit"] == {"status": "NARROWED", "value": "C3", "basis": None, "evidence_ids": []}
    assert f["floor"]["status"] == "AMBIGUOUS"
    assert out["address"]["candidate_count"] == 13  # 2F + 3F–14F（推導規則都未驗證）


def test_no_narrowing_fields(add):  # T-RULE-01
    _, out = add(floor_text="中樓層")
    assert out["address"]["outcome"] == "INSUFFICIENT_INFO"


def test_sales_unit_and_floor_single_candidate(add):  # T-ADDR-07
    _, out = add(sales_unit_text="A2", floor_text="12F")
    c = live(out)
    assert out["address"]["outcome"] == "SINGLE_CANDIDATE" and (c[0]["number"], c[0]["floor"]) == (79, 12)


def test_dual_area_basis_ambiguous(add):  # 16 §3
    _, out = add(source_code="SRC-MANUAL", parking_included="UNKNOWN", building_ping=43.56, floor_text="5樓/14樓")
    assert "AREA_BASIS_AMBIGUOUS" in out["address"]["flags"]
    assert {c["sales_unit"] for c in live(out)} >= {"D2", "D5", "C1", "C2"}


def test_orientation_and_units_per_floor(add):  # 信義 8749CJ
    _, out = add(source_code="SRC-SINYI", building_ping=79.05, parking_ping=15.08, floor_text="12樓/14樓", rooms=4,
                 orientation="落地窗東南", units_per_floor=4)
    c = live(out)
    assert (c[0]["sales_unit"], c[0]["number"], c[0]["floor"]) == ("A5", 83, 12)
    assert all(x["score"] < c[0]["score"] for x in c[1:])


def test_front_row_absent_on_2f(add):  # 2F 平面圖：前排 6 戶不存在
    _, out = add(building_ping=51.37, parking_ping=7.81, floor_text="2樓/14樓", rooms=3)
    hits = {h["rule_code"] for c in live(out) for h in c["rule_hits"]}
    assert "X7s" in hits  # C1/C2 在 2F 沒有規則涵蓋 → 軟反駁


def test_unchanged_reassessment_writes_nothing(conn, add):  # 軟工 P1：資料沒變就不新增評估紀錄
    from jev import identity
    from jev.assess import assess_listing
    lid, _ = add(building_ping=43.92, parking_ping=7.81, floor_text="9樓/14樓")
    add(building_ping=43.92, parking_ping=7.81, floor_text="9樓/14樓")
    identity.evaluate_pairs(conn)
    count = lambda: tuple(conn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0] for t in ("assessment", "assessment_candidate", "listing_event"))
    before = count()
    for _ in range(3):
        assess_listing(conn, lid, trigger="MANUAL")
        identity.evaluate_pairs(conn)
    assert count() == before
