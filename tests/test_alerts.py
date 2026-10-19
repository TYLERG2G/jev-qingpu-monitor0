"""通知規則（門檻、新增／修改／刪除）與匯出。"""

import pytest
from conftest import ROOT, validate

from jev import notify, registry
from jev.api import App
from jev.lifecycle import import_csv, record

H = {"x-api-key": "k"}
BASE = {"source_code": "SRC-591", "platform_community_ref": "591:9488", "community_text": "聯上世界",
        "address_text": "桃園市大園區高鐵南路一段", "parking_included": "INCLUDED", "building_ping": 43.92, "parking_ping": 7.81,
        "floor_text": "9樓/14樓", "rooms": 2}


@pytest.fixture
def app(tmp_path, monkeypatch):
    for k in ("JEV_TELEGRAM_BOT_TOKEN", "JEV_TELEGRAM_CHAT_ID", "JEV_SMTP_HOST"):
        monkeypatch.delenv(k, raising=False)
    a = App(tmp_path / "a.db", api_key="k", outbox=str(tmp_path / "ob"))
    registry.load(a.conn)
    import_csv(a.conn, ROOT / "data" / "seed" / "lianshang-listings-2026-10-18.csv", "INITIAL")
    return a


def test_rule_thresholds_and_crud(app, tmp_path):
    rules = app.handle("GET", "/api/v1/alert-rules", H, {})[1]
    for r in rules:  # 停用預設規則，只測自訂規則
        app.handle("PATCH", f"/api/v1/alert-rules/{r['id']}", {**H, "if-match": f'"v{r["version"]}"'}, {"enabled": False})
    body = {"name": "兩房降價 5% 以上", "event_types": ["LISTING_PRICE_CHANGED"], "channel": "TELEGRAM", "min_price_drop_pct": 5,
            "min_rooms": 2, "max_total_price": 22000000}
    validate("AlertRuleCreate", body)
    status, rule, _ = app.handle("POST", "/api/v1/alert-rules", H, body)
    assert status == 201
    validate("AlertRule", rule)
    status, err, _ = app.handle("POST", "/api/v1/alert-rules", H, {"name": "x", "event_types": ["PROPERTY_MERGED"], "channel": "EMAIL"})
    assert status == 422  # 內部事件不能拿來通知
    c = app.conn
    record(c, {**BASE, "external_id": "N1", "total_price": 21000000}, "INCREMENTAL")
    record(c, {**BASE, "external_id": "N1", "total_price": 20600000}, "INCREMENTAL")  # 降 1.9%：不通知
    assert [n for n in notify.process(c, tmp_path / "ob") if n["rule"] == body["name"]] == []
    record(c, {**BASE, "external_id": "N1", "total_price": 19000000}, "INCREMENTAL")  # 再降 7.8%：通知
    sent = [n for n in notify.process(c, tmp_path / "ob") if n["rule"] == body["name"]]
    assert len(sent) == 1 and sent[0]["status"] == "PENDING"  # 沒設定 Telegram → 寫入 outbox
    record(c, {**BASE, "external_id": "N2", "rooms": 1, "total_price": 21000000}, "INCREMENTAL")
    record(c, {**BASE, "external_id": "N2", "rooms": 1, "total_price": 19000000}, "INCREMENTAL")  # 一房：不符 min_rooms
    assert [n for n in notify.process(c, tmp_path / "ob") if n["rule"] == body["name"]] == []
    status, err, _ = app.handle("PATCH", f"/api/v1/alert-rules/{rule['id']}", H, {"min_rooms": 3})
    assert status == 428
    status, patched, _ = app.handle("PATCH", f"/api/v1/alert-rules/{rule['id']}", {**H, "if-match": '"v1"'}, {"min_rooms": None, "name": "降價"})
    assert status == 200 and patched["min_rooms"] is None and patched["version"] == 2
    status, err, _ = app.handle("PATCH", f"/api/v1/alert-rules/{rule['id']}", {**H, "if-match": '"v2"'}, {"name": None})
    assert status == 422
    status, _, _ = app.handle("DELETE", f"/api/v1/alert-rules/{rule['id']}", {**H, "if-match": '"v2"'}, {})
    assert status == 204
    assert rule["id"] not in [r["id"] for r in app.handle("GET", "/api/v1/alert-rules", H, {})[1]]


def test_export_csv(app):
    status, text, hdr = app.handle("GET", "/api/v1/properties:export", H, {})
    assert status == 200 and hdr["Content-Type"].startswith("text/csv") and text.startswith("﻿")
    lines = text.lstrip("﻿").strip().splitlines()
    assert lines[0].startswith("property_id") and len(lines) == 14
    assert "103號9樓" in text  # 20790495 的兩個候選
    assert "591" in text and "吉富" not in text  # 不含仲介資料
