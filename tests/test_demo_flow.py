"""DEMO 流程：以 jev.demo 建庫，再用與 jev/ui/index.html 相同的請求內容操作 API。"""

import pytest
from conftest import validate

from jev.api import App
from jev.db import connect
from jev.demo import build

H = {"x-api-key": "k"}
CAP = {"captured_by": "user", "captured_at": "2026-11-02T03:00:00Z", "method": "PHOTO"}


@pytest.fixture
def app(tmp_path):
    db = tmp_path / "demo.db"
    out = build(connect(str(db)), tmp_path / "ob")
    assert out["listings"] == 15
    return App(db, api_key="k", outbox=str(tmp_path / "ob2"))


def _post(app, path, body, version, key):
    return app.handle("POST", f"/api/v1{path}", {**H, "idempotency-key": key, "if-match": f'"v{version}"'}, body)


def _task(app, pred):
    return next(t for t in app.handle("GET", "/api/v1/review-tasks", H, {})[1]["items"] if pred(t))


def test_page_and_registry_endpoints(app):
    status, page, hdr = app.handle("GET", "/", {}, {})
    assert status == 200 and hdr["Content-Type"].startswith("text/html") and "JEV" in page
    for path, schema in (("sales-units", "SalesUnit"), ("dwellings", "Dwelling"), ("address-units", "AddressUnit"), ("mappings", "RegistryMapping")):
        items = app.handle("GET", f"/api/v1/registry/{path}", H, {})[1]["items"]
        assert items
        for it in items:
            validate(schema, it)
    for r in app.handle("GET", "/api/v1/registry/derivation-rules", H, {})[1]:
        validate("DerivationRule", r)
    validate("NormalizedAddress", app.handle("POST", "/api/v1/addresses:normalize", H, {"address_text": "桃園市大園區高鐵南路一段７９號十二樓"})[1])
    for t in app.handle("GET", "/api/v1/review-tasks", H, {})[1]["items"]:
        validate("ReviewTask", t)


def test_demo_flow_confirm_address_and_same_dwelling(app):
    ext = {l["id"]: l["external_id"] for l in app.handle("GET", "/api/v1/listings", H, {})[1]["items"]}
    lid = next(k for k, v in ext.items() if v == "8749CJ")
    t = _task(app, lambda t: t["task_type"] == "ADDRESS_CANDIDATE" and t["listing_id"] == lid)
    top = max(t["assessment"]["candidates"], key=lambda c: c["rule_score"])
    body = {"decision": "CONFIRM_CANDIDATE", "candidate_id": top["candidate_id"], "rationale": "帶看時看到門牌", "new_evidence": [
        {"field": "full_address", "value": {"address_text": "高鐵南路一段83號12樓"}, "description": "帶看時拍到門牌 83 號 12 樓",
         "observed_at": CAP["captured_at"], "evidence_role": "DIRECT_ADDRESS", "derivation_type": "ORIGINAL", "capture": CAP},
        {"field": "listing_identity", "value": {"listing_ref_seen": "現場"}, "description": "與此刊登的連結：帶看仲介出示此刊登編號",
         "observed_at": CAP["captured_at"], "evidence_role": "LISTING_LINKAGE", "derivation_type": "ORIGINAL",
         "link_method": "AGENT_VIEWING_WITH_LISTING_REF", "capture": {**CAP, "method": "SITE_VISIT_NOTE"}, "matched_features": ["窗景", "格局"]}]}
    status, rv, _ = _post(app, f"/review-tasks/{t['id']}/decisions", body, t["version"], "demo-1")
    assert status == 201 and rv["support_assessment"]["result"] == "SUFFICIENT"
    detail = app.handle("GET", f"/api/v1/listings/{lid}", H, {})[1]
    assert detail["address_status"] == "CONFIRMED"
    assert detail["address_assessment"]["resolved_fields"]["full_address"]["value"] == "高鐵南路一段83號12樓"

    m = _task(app, lambda t: t["task_type"] == "LISTING_MATCH")
    pair = {ext[m["identity_match"]["listing_a_id"]], ext[m["identity_match"]["listing_b_id"]]}
    assert pair == {"20790495", "DEMO-001"}
    body = {"decision": "CONFIRM_SAME", "rationale": "兩則刊登室內照片相同", "new_evidence": [
        {"field": "review_support", "value": {"note": "同一組室內照片"}, "description": "兩則刊登使用同一組室內照片",
         "observed_at": CAP["captured_at"], "evidence_role": "OTHER", "derivation_type": "ORIGINAL", "capture": {**CAP, "method": "OTHER"}}]}
    status, _, _ = _post(app, f"/review-tasks/{m['id']}/decisions", body, m["version"], "demo-2")
    assert status == 201
    notes = app.handle("GET", "/api/v1/notifications", H, {})[1]["items"]
    assert [n["rendered"]["event_type"] for n in notes if n["notification_kind"] == "EVENT"][-1] == "DWELLING_NEW_LISTING"
    groups = [p for p in app.handle("GET", "/api/v1/properties", H, {})[1]["items"] if len(p["listing_ids"]) > 1]
    assert len(groups) == 1
