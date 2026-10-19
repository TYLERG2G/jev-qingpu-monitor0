"""生命週期、事件、通知與同屋（docs/07、12、13；對應 T-LIFE、T-EVT、T-NTF、T-IDN）。"""

import json

from conftest import validate

from jev import identity, notify
from jev.events import to_api
from jev.lifecycle import mark_delisted, record, relist_if_needed


def events(conn, t=None):
    q = "SELECT * FROM listing_event" + (" WHERE event_type=?" if t else "") + " ORDER BY id"
    return conn.execute(q, (t,) if t else ()).fetchall()


def test_initial_import_never_notifies(conn, add, tmp_path):  # T-LIFE-12、T-LIFE-15
    add(building_ping=37.8)
    fo = events(conn, "LISTING_FIRST_OBSERVED")[0]
    assert fo["suppression_reason"] == "MANUAL_INITIAL_CONTEXT" and not fo["notify_eligible"]
    assert notify.process(conn, tmp_path) == []


def test_all_events_match_contract(conn, add):  # T-EVT-02
    lid, _ = add(building_ping=37.8)
    record(conn, {"source_code": "SRC-591", "external_id": "T0001", "total_price": 19000000, "building_ping": 37.8,
                  "platform_community_ref": "591:9488", "community_text": "聯上世界", "address_text": "桃園市大園區高鐵南路一段",
                  "parking_included": "INCLUDED"})
    for ev in events(conn):
        validate("Event", to_api(ev))


def test_price_drop_notifies_once(conn, add, tmp_path):  # T-LIFE-08、T-NTF-01
    lid, _ = add(building_ping=37.8, total_price=20000000)
    rec = {"source_code": "SRC-591", "external_id": "T0001", "total_price": 19500000, "building_ping": 37.8,
           "platform_community_ref": "591:9488", "community_text": "聯上世界", "address_text": "桃園市大園區高鐵南路一段",
           "parking_included": "INCLUDED"}
    record(conn, rec)
    ev = events(conn, "LISTING_PRICE_CHANGED")[0]
    assert json.loads(ev["payload"])["direction"] == "DOWN" and ev["notify_eligible"]
    assert len(notify.process(conn, tmp_path)) == 1
    assert notify.process(conn, tmp_path) == []  # 同一事件不重複通知
    assert record(conn, rec)["changed"] is False  # 內容相同：不新增快照、不發事件


def test_delist_relist_cycles(conn, add):  # T-LIFE-07
    lid, _ = add(building_ping=37.8)
    prop = lambda: conn.execute("SELECT p.status, p.active_listing_count, p.current_min_price FROM property p JOIN listing l"
                                " ON l.property_id=p.id WHERE l.id=?", (lid,)).fetchone()
    assert tuple(prop()) == ("ACTIVE", 1, 20000000)
    mark_delisted(conn, lid, "MARKED_SOLD", "平台顯示已下架")
    assert tuple(prop()) == ("INACTIVE", 0, None)
    relist_if_needed(conn, lid)
    mark_delisted(conn, lid, "MANUAL", "再次下架")
    relist_if_needed(conn, lid)
    keys = [e["idempotency_key"] for e in events(conn) if e["event_type"] in ("LISTING_DELISTED", "LISTING_RELISTED")]
    assert keys == [f"delisted:{lid}:1", f"relisted:{lid}:2", f"delisted:{lid}:2", f"relisted:{lid}:3"]


def _props(conn, *lids):
    return [conn.execute("SELECT property_id FROM listing WHERE id=?", (l,)).fetchone()[0] for l in lids]


def test_merge_initial_listings_does_not_notify(conn, add, tmp_path):  # T-LIFE-13
    a, _ = add(building_ping=37.8)
    b, _ = add(building_ping=37.8)
    identity.merge(conn, _props(conn, a, b), "兩則初始化刊登是同一戶", [1])
    assert events(conn, "DWELLING_NEW_LISTING") == []
    merged = json.loads(events(conn, "PROPERTY_MERGED")[0]["payload"])
    assert sorted(merged["initial_context_listing_ids"]) == sorted([a, b]) and merged["new_listing_ids"] == []
    assert notify.process(conn, tmp_path) == []


def test_new_listing_joins_once_even_after_revert_and_split(conn, add, tmp_path):  # T-LIFE-14、16、20
    a, _ = add(building_ping=37.8)
    b, _ = add(mode="INCREMENTAL", building_ping=37.8, is_new_listing=True)
    op = identity.merge(conn, _props(conn, a, b), "延遲合併的真正新增刊登", [1])
    dnl = events(conn, "DWELLING_NEW_LISTING")
    assert len(dnl) == 1 and dnl[0]["idempotency_key"] == f"dwelling_new_listing:{b}"
    sent = notify.process(conn, tmp_path)
    assert any(s["rule"] == "新刊登與同屋新刊登" for s in sent)
    identity.revert(conn, op, "合併錯誤，撤回")
    identity.merge(conn, _props(conn, a, b), "確認後再合併", [1])
    assert len(events(conn, "DWELLING_NEW_LISTING")) == 1  # 不重發新案
    kinds = [r[0] for r in conn.execute("SELECT notification_kind FROM notification ORDER BY id")]
    assert kinds.count("CORRECTION") == 2  # 撤回與重新合併各一則更正通知
    sent = notify.process(conn, tmp_path)
    assert [s.get("kind") for s in sent] == ["CORRECTION", "CORRECTION"]
    assert len(list(tmp_path.glob("*correction*.json"))) == 2
    assert notify.process(conn, tmp_path) == []
    for n in conn.execute("SELECT * FROM notification"):
        validate("Notification", {"id": n["id"], "notification_kind": n["notification_kind"], "alert_rule_id": n["alert_rule_id"],
                                  "event_id": n["event_id"], "channel": n["channel"], "rendered": json.loads(n["rendered"]),
                                  "status": n["status"], "attempts": n["attempts"], "last_error": n["last_error"],
                                  "sent_at": n["sent_at"], "corrects_notification_id": n["corrects_notification_id"]})


def test_revert_refused_when_later_operation_depends(conn, add):  # T-IDN-05
    a, _ = add(building_ping=37.8)
    b, _ = add(building_ping=37.8)
    c, _ = add(building_ping=37.8)
    op1 = identity.merge(conn, _props(conn, a, b), "第一次合併測試", [1])
    identity.merge(conn, _props(conn, b, c), "第二次合併測試", [1])
    try:
        identity.revert(conn, op1, "嘗試撤回第一次")
        raise AssertionError("應該被拒絕")
    except ValueError as e:
        assert str(e).startswith("DEPENDENT_OPERATIONS")


def test_pair_conflicts_and_no_auto_merge(conn, add):  # T-IDN-01、T-IDN-03
    a, _ = add(building_ping=43.92, parking_ping=7.81, floor_text="9樓/14樓", rooms=2)
    b, _ = add(building_ping=43.92, parking_ping=7.81, floor_text="4樓/14樓", rooms=2)
    c, _ = add(building_ping=43.92, parking_ping=7.81, floor_text="9樓/14樓", rooms=2)
    res = {(m["listing_a_id"], m["listing_b_id"]): m for m in identity.evaluate_pairs(conn)}
    assert (a, b) not in res  # 9F 與 4F 沒有共同候選，不進入 blocking
    assert res[(a, c)]["outcome"] == "REVIEW_REQUIRED"  # 坪數、樓層、房數都相同，但仍是 D2／D5 兩戶 → 只能覆核
    assert all(m["outcome"] != "AUTO_MERGE_ELIGIBLE" for m in res.values())


def test_split_rejects_pairs(conn, add):  # T-IDN-04
    a, _ = add(building_ping=37.8)
    b, _ = add(building_ping=37.8)
    identity.evaluate_pairs(conn)
    identity.merge(conn, _props(conn, a, b), "合併測試資料", [1])
    pid = _props(conn, a)[0]
    identity.split(conn, pid, [[a], [b]], "照片不同，拆開")
    assert _props(conn, a) != _props(conn, b)
    assert conn.execute("SELECT status FROM identity_match WHERE listing_a_id=? AND listing_b_id=?", (a, b)).fetchone()[0] == "REJECTED"
    validate("Event", to_api(events(conn, "PROPERTY_SPLIT")[0]))


def test_bad_csv_row_rejects_whole_file(conn, tmp_path):
    import pytest
    from jev.lifecycle import import_csv
    f = tmp_path / "bad.csv"
    f.write_text("source_code,external_id,total_price,building_ping\nSRC-591,A1,15000000,37.8\nSRC-591,A2,,37.8\n", encoding="utf-8")
    with pytest.raises(ValueError, match="第 3 列"):
        import_csv(conn, f, "INCREMENTAL")
    assert conn.execute("SELECT COUNT(*) FROM listing").fetchone()[0] == 0
