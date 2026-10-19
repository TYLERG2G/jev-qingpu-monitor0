"""本機 API：回應依 api/openapi.yaml 驗證；冪等、版本檢查、錯誤格式（對應 T-API-*、T-REV-01）。"""

import pytest
from conftest import ROOT, validate

from jev import registry
from jev.api import App
from jev.lifecycle import import_csv

H = {"x-api-key": "k"}


@pytest.fixture
def app(tmp_path):
    a = App(tmp_path / "api.db", api_key="k")
    registry.load(a.conn)
    import_csv(a.conn, ROOT / "data" / "seed" / "lianshang-listings-2026-10-18.csv", "INITIAL")
    return a


def test_health_and_auth(app):
    status, body, _ = app.handle("GET", "/api/v1/health", {}, {})
    assert status == 200 and {s["code"]: s["last_run_status"] for s in body["sources"]}["SRC-591"] == "SUCCEEDED"
    validate("Health", body)
    status, body, _ = app.handle("GET", "/api/v1/listings", {}, {})
    assert status == 401
    validate("Problem", body)


def test_listing_shapes(app):
    status, page, _ = app.handle("GET", "/api/v1/listings", H, {})
    assert status == 200 and len(page["items"]) == 13
    for item in page["items"]:
        validate("Listing", item)
    status, detail, headers = app.handle("GET", "/api/v1/listings/1", H, {})
    validate("ListingDetail", detail)
    assert headers["ETag"].startswith('"v')
    status, cands, _ = app.handle("GET", "/api/v1/listings/1/candidates", H, {})
    for c in cands:
        validate("ScoredCandidate", c)
    status, ev, _ = app.handle("GET", "/api/v1/events", H, {})
    for e in ev["items"]:
        validate("Event", e)
    validate("Community", app.handle("GET", "/api/v1/community", H, {})[1])


def test_review_tasks_and_stale_decision(app):  # T-API-02、04、T-REV-01
    status, tasks, _ = app.handle("GET", "/api/v1/review-tasks", H, {})
    assert tasks["items"]
    for t in tasks["items"]:
        validate("ReviewTask", t)
    tid = tasks["items"][0]["id"]
    status, body, _ = app.handle("POST", f"/api/v1/review-tasks/{tid}/decisions", H, {"decision": "ABSTAIN", "rationale": "測試缺少冪等鍵"})
    assert status == 400 and body["code"] == "IDEMPOTENCY_KEY_REQUIRED"
    hdr = {**H, "idempotency-key": "key-0001", "if-match": '"v99"'}
    status, body, _ = app.handle("POST", f"/api/v1/review-tasks/{tid}/decisions", hdr, {"decision": "ABSTAIN", "rationale": "版本過期測試"})
    assert status == 412 and body["code"] == "STALE_VERSION" and "current_etag" in body
    validate("Problem", body)
    hdr = {**H, "idempotency-key": "key-0002", "if-match": f'"v{tasks["items"][0]["version"]}"'}
    status, review_body, _ = app.handle("POST", f"/api/v1/review-tasks/{tid}/decisions", hdr, {"decision": "ABSTAIN", "rationale": "兩戶照片無法區分"})
    assert status == 201
    validate("Review", review_body)
    status, again, extra = app.handle("POST", f"/api/v1/review-tasks/{tid}/decisions", hdr, {"decision": "ABSTAIN", "rationale": "兩戶照片無法區分"})
    assert status == 201 and extra.get("Idempotent-Replayed") == "true" and again == review_body
    status, body, _ = app.handle("POST", f"/api/v1/review-tasks/{tid}/decisions", hdr, {"decision": "ABSTAIN", "rationale": "內容不同的重送"})
    assert status == 422 and body["code"] == "IDEMPOTENCY_KEY_REUSED"


def test_create_listing_rules(app):  # T-API-01、03、06
    body = {"source_code": "SRC-LEJU", "snapshot": {"total_price": 15800000}}
    status, err, _ = app.handle("POST", "/api/v1/listings", H, body)
    assert status == 422
    validate("Problem", err)
    manual = {"source_code": "SRC-MANUAL", "evidence_origin": {"origin_source_code": "SRC-591"},
              "snapshot": {"total_price": 15800000, "building_ping": 37.8, "floor_text": "8樓/14樓"}}
    status, created, _ = app.handle("POST", "/api/v1/listings", H, manual)
    assert status == 201 and created["external_id"].startswith("man_") and created["external_id_origin"] == "SYSTEM_GENERATED"
    validate("ListingDetail", created)
    status, err, _ = app.handle("POST", "/api/v1/listings", H, {"source_code": "SRC-591", "external_id": "20790495", "snapshot": {"total_price": 1}})
    assert status == 409 and err["code"] == "LISTING_EXISTS"


def _post(app, path, body, key, etag=None):
    hdr = {**H, "idempotency-key": key}
    if etag:
        hdr["if-match"] = etag
    return app.handle("POST", path, hdr, body)


CAP = {"captured_by": "user", "captured_at": "2026-11-01T06:00:00Z", "method": "PHOTO"}


def test_evidence_create_retract_and_reads(app):  # T-EVD-01、T-EVD-11
    cands = app.handle("GET", "/api/v1/listings/1/candidates", H, {})[1]
    body = {"listing_id": 1, "field": "layout", "value": {"sales_unit": "D5", "match": True}, "description": "照片鏡像方向符合 D5",
            "observed_at": "2026-11-01T06:00:00Z", "evidence_role": "FEATURE_OBSERVATION", "derivation_type": "ORIGINAL", "capture": CAP,
            "links": [{"target_type": "CANDIDATE", "target_id": cands[0]["candidate_id"], "target_field": "sales_unit", "stance": "SUPPORTS"}]}
    validate("EvidenceCreate", body)
    status, ev, _ = app.handle("POST", "/api/v1/evidence", H, body)
    assert status == 201 and ev["links"][0]["stance"] == "SUPPORTS"
    validate("Evidence", ev)
    for e in app.handle("GET", "/api/v1/listings/1/evidence", H, {})[1]:
        validate("Evidence", e)
    for a in app.handle("GET", "/api/v1/listings/1/assessments", H, {})[1]:
        validate("Assessment", a)
    validate("Snapshot", app.handle("GET", "/api/v1/listings/1/snapshots", H, {})[1]["items"][0])
    status, err, _ = _post(app, f"/api/v1/evidence/{ev['id']}:retract", {"rationale": "照片是別戶"}, "r-1")
    assert status == 428
    status, res, _ = _post(app, f"/api/v1/evidence/{ev['id']}:retract", {"rationale": "照片是別戶"}, "r-2", f'"v{ev["version"]}"')
    assert status == 200 and res["new_assessment_ids"]
    validate("ReassessResult", res)
    assert app.handle("GET", f"/api/v1/evidence/{ev['id']}", H, {})[1]["status"] == "RETRACTED"


def test_merge_split_revert_via_api(app):  # T-IDN-02、04、05
    status, merged, _ = _post(app, "/api/v1/properties:merge", {"property_ids": [1, 2], "rationale": "沒有佐證的合併"}, "m-0")
    assert status == 422
    ev = app.handle("POST", "/api/v1/evidence", H, {
        "listing_id": 1, "field": "listing_identity", "value": {"same_photos": True}, "description": "兩則刊登使用同一組室內照片",
        "observed_at": "2026-11-01T06:00:00Z", "evidence_role": "OTHER", "derivation_type": "ORIGINAL", "capture": CAP,
        "links": [{"target_type": "IDENTITY_MATCH", "target_id": 1, "target_field": "same_dwelling", "stance": "SUPPORTS"}]})[1]
    status, op, _ = _post(app, "/api/v1/properties:merge", {"property_ids": [1, 2], "rationale": "同一組室內照片", "evidence_ids": [ev["id"]]}, "m-1")
    assert status == 201 and op["evidence_ids"] == [ev["id"]]
    validate("MergeOperation", op)
    status, prop, hdr = app.handle("GET", "/api/v1/properties/1", H, {})
    assert prop["listing_ids"] == [1, 2]
    validate("PropertyDetail", prop)
    for p in app.handle("GET", "/api/v1/properties", H, {})[1]["items"]:
        validate("Property", p)
    status, err, _ = _post(app, "/api/v1/properties/1:split", {"groups": [[1]], "rationale": "缺少群組測試"}, "s-0", hdr["ETag"])
    assert status == 422
    status, err, _ = _post(app, "/api/v1/properties/1:split", {"groups": [[1], [3]], "rationale": "群組不符測試"}, "s-1", hdr["ETag"])
    assert status == 422 and err["code"] == "SPLIT_GROUPS_MISMATCH"
    status, rev, _ = _post(app, f"/api/v1/merge-operations/{op['id']}:revert", {"rationale": "合併錯誤，撤回"}, "v-1")
    assert status == 201 and rev["op_type"] == "REVERT" and rev["reverts_operation_id"] == op["id"]
    validate("MergeOperation", app.handle("GET", f"/api/v1/merge-operations/{rev['id']}", H, {})[1])
    status, err, _ = _post(app, f"/api/v1/merge-operations/{op['id']}:revert", {"rationale": "重複撤回測試"}, "v-2")
    assert status == 409


def test_delist_relation_and_lists(app):
    detail = app.handle("GET", "/api/v1/listings/3", H, {})
    status, out, _ = _post(app, "/api/v1/listings/3:mark-delisted", {"reason": "MARKED_SOLD", "rationale": "平台顯示已成交"}, "d-1",
                           detail[2]["ETag"])
    assert status == 200 and out["status"] == "DELISTED"
    validate("ListingDetail", out)
    status, err, _ = _post(app, "/api/v1/listings/3:mark-delisted", {"reason": "MANUAL", "rationale": "版本過期測試"}, "d-2",
                           detail[2]["ETag"])
    assert status == 412
    status, err, _ = _post(app, "/api/v1/origin-relations", {"origin_a_id": 1, "origin_b_id": 2, "relation": "INDEPENDENT",
                                                             "rationale": "沒有佐證的獨立判定"}, "o-1")
    assert status == 422
    app.conn.execute("INSERT INTO source_origin (origin_type, asserting_party_type, description) VALUES ('PLATFORM','AGENT','測試 A')")
    app.conn.execute("INSERT INTO source_origin (origin_type, asserting_party_type, description) VALUES ('PLATFORM','AGENT','測試 B')")
    a, b = [r[0] for r in app.conn.execute("SELECT id FROM source_origin ORDER BY id DESC LIMIT 2")]
    status, rel, _ = _post(app, "/api/v1/origin-relations", {"origin_a_id": a, "origin_b_id": b, "relation": "DEPENDENT",
                                                             "rationale": "同一家仲介轉貼"}, "o-2")
    assert status == 201 and rel["origin_a_id"] < rel["origin_b_id"]
    validate("OriginRelation", rel)
    for s in app.handle("GET", "/api/v1/sources", H, {})[1]:
        validate("Source", s)
    for r in app.handle("GET", "/api/v1/alert-rules", H, {})[1]:
        validate("AlertRule", r)
    for n in app.handle("GET", "/api/v1/notifications", H, {})[1]["items"]:
        validate("Notification", n)
    status, mt, _ = app.handle("GET", "/api/v1/identity-matches/999", H, {})
    assert status == 404


def test_real_http_server(app):  # 只連 127.0.0.1：中文 UTF-8、錯誤編碼、未實作方法都回 problem+json
    import json as _json
    import threading
    import urllib.error
    import urllib.request
    from http.server import ThreadingHTTPServer

    from jev.api import make_handler
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(app))
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{httpd.server_address[1]}/api/v1"

    def call(method, path, data=None, headers=None):
        req = urllib.request.Request(base + path, data=data, method=method, headers={"X-API-Key": "k", **(headers or {})})
        try:
            with urllib.request.urlopen(req, timeout=5) as r:
                return r.status, r.headers, _json.loads(r.read())
        except urllib.error.HTTPError as e:
            return e.code, e.headers, _json.loads(e.read())

    try:
        etag = call("GET", "/listings/5")[1]["ETag"]
        body = _json.dumps({"reason": "MARKED_SOLD", "rationale": "平台顯示已成交"}, ensure_ascii=False).encode("utf-8")
        status, _, out = call("POST", "/listings/5:mark-delisted", body, {"Idempotency-Key": "h-1", "If-Match": etag,
                                                                          "Content-Type": "application/json"})
        assert status == 200 and out["status"] == "DELISTED"
        assert call("GET", "/properties/5")[2]["status"] == "INACTIVE"
        status, hdr, err = call("POST", "/listings/6:mark-delisted", "{\"rationale\": \"平台\"}".encode("cp950"), {"Idempotency-Key": "h-2"})
        assert status == 400 and hdr["Content-Type"].startswith("application/problem+json")
        status, _, err = call("PATCH", "/listings/1", b"{}")
        assert status == 405 and err["code"] == "METHOD_NOT_ALLOWED"
    finally:
        httpd.shutdown()


def test_robust_errors_and_host_check(app, monkeypatch):  # 軟工 P0：任何錯誤都回 problem，不會讓伺服器斷線
    status, body, _ = app.handle("POST", "/api/v1/listings", H, [1, 2])
    assert status == 400 and body["code"] == "VALIDATION_ERROR"
    status, body, _ = app.handle("POST", "/api/v1/listings", H, {"source_code": "SRC-591", "external_id": "bad1",
                                                                  "snapshot": {"total_price": 1000, "building_ping": "abc"}})
    assert status == 422
    validate("Problem", body)
    assert app.conn.execute("SELECT COUNT(*) FROM listing WHERE external_id='bad1'").fetchone()[0] == 0
    status, body, _ = app.handle("GET", "/api/v1/listings", {**H, "host": "evil.example:8787"}, {})
    assert status == 403
    assert app.handle("GET", "/api/v1/listings", {**H, "host": "localhost:8787"}, {})[0] == 200
    monkeypatch.setattr(app, "community", lambda: 1 / 0)
    status, body, _ = app.handle("GET", "/api/v1/community", H, {})
    assert status == 500 and body["code"] == "INTERNAL_ERROR" and "ZeroDivision" in body["detail"]


def test_failed_write_leaves_nothing_behind(app, monkeypatch):  # 一個操作一個交易
    from jev import identity
    n = app.conn.execute("SELECT COUNT(*) FROM evidence").fetchone()[0]
    monkeypatch.setattr(identity, "merge", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("合併中途失敗")))
    body = {"property_ids": [1, 2], "rationale": "同一組照片", "new_evidence": [
        {"field": "x", "value": 1, "description": "兩則刊登照片相同", "observed_at": "2026-11-01T00:00:00Z", "evidence_role": "OTHER",
         "derivation_type": "ORIGINAL", "capture": {"captured_by": "user", "captured_at": "2026-11-01T00:00:00Z", "method": "PHOTO"}}]}
    status, _, _ = app.handle("POST", "/api/v1/properties:merge", {**H, "idempotency-key": "tx-1"}, body)
    assert status == 500
    assert app.conn.execute("SELECT COUNT(*) FROM evidence").fetchone()[0] == n  # 先建立的佐證也被還原


def test_parallel_reads_are_consistent(app):  # 前端一次發出多個請求：共用連線也不能讀錯
    from concurrent.futures import ThreadPoolExecutor
    ids = [i["id"] for i in app.handle("GET", "/api/v1/listings", H, {})[1]["items"]] * 8
    with ThreadPoolExecutor(max_workers=16) as ex:
        results = list(ex.map(lambda i: app.handle("GET", f"/api/v1/listings/{i}", H, {})[0], ids))
    assert set(results) == {200}
