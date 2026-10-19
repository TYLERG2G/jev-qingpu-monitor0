"""決策模型模組（D-020）：用假的傳輸層測試，不連網。"""

import base64
import json

import pytest
from conftest import live

from jev import decide, identity, review
from jev.config import decision_settings
from jev.assess import assess_listing

PNG = "data:image/png;base64," + base64.b64encode(b"\x89PNG fake listing photo bytes").decode()
ENV = {"JEV_DECISION_PROVIDER": "openrouter", "OPENROUTER_API_KEY": "sk-or-test-1234567890", "OPENROUTER_DECISION_MODEL": "typesafe/jev-1.14",
       "JEV_DECISION_ALLOW_TEXT": "true", "JEV_DECISION_ALLOW_IMAGES": "true", "JEV_DECISION_MONTHLY_BUDGET_USD": "1"}


KIND = {"photo_kind": {"type": "choice", "choice": "interior_view", "confidence": 0.9, "probabilities": {"interior_view": 0.95}}}


def fake(answers, cost=0.0001, status=200, sent=None, kind=KIND):
    """假的傳輸層：照片分類預設回答「室內有窗景」，其他題目依 answers。"""
    def transport(provider, payload, timeout):
        if sent is not None and "photo_kind" not in payload["questions"]:
            sent.append(payload)
        if status != 200:
            return status, {"error": "boom"}
        merged = {**kind, **answers}
        return 200, {"answers": {k: v for k, v in merged.items() if k in payload["questions"]}, "usage": {"input_tokens": 1200, "cost": cost}}
    return transport


def test_disabled_by_default(conn, add):
    lid, _ = add(building_ping=43.92, parking_ping=7.81, floor_text="9樓/14樓")
    with pytest.raises(decide.DecisionError) as e:
        decide.ask_listing(conn, lid, ["layout_front_row"], [PNG], environ={})
    assert e.value.code == "DECISIONS_DISABLED"
    assert conn.execute("SELECT COUNT(*) FROM decision_call").fetchone()[0] == 0


def test_request_formats():
    q = {"front": decide.QUESTIONS["layout_front_row"], "band": decide.QUESTIONS["floor_band"]}
    o = decision_settings(ENV).provider
    body = decide.build_request(o, q, {"坪數": 43.92}, [PNG])
    assert body["model"] == "typesafe/jev-1.14" and body["state"][1]["image_url"]["url"] == PNG
    assert set(body["questions"]["front"]["criteria"]) == {"true", "false"}  # OpenRouter 的 noul 要 criteria
    t = decision_settings({"JEV_DECISION_PROVIDER": "typesafe", "TYPESAFE_API_KEY": "ts-1234567890", "JEV_DECISION_ALLOW_TEXT": "1"}).provider
    assert "criteria" not in decide.build_request(t, q, "x")["questions"]["front"]  # TypeSafe 直連：noul 只要 instructions
    a = decision_settings({"JEV_DECISION_PROVIDER": "openai", "OPENAI_API_KEY": "sk-1234567890", "JEV_DECISION_ALLOW_IMAGES": "1"}).provider
    body = decide.build_request(a, q, {"坪數": 43.92}, [PNG])
    assert [x["type"] for x in body["questions"]] == ["predicate", "score"] and body["input"][0]["content"][1]["type"] == "input_image"


def test_parse_both_formats():
    q = {"front": decide.QUESTIONS["layout_front_row"], "band": decide.QUESTIONS["floor_band"]}
    s1, _ = decide.parse_response({"answers": {"front": {"type": "noul", "noul": 0.8},
                                               "band": {"type": "score", "score": 1.7, "probabilities": {"0": 0.1, "1": 0.1, "2": 0.8}}}}, q)
    s2, _ = decide.parse_response({"answers": [{"name": "front", "type": "predicate", "probability": 0.8},
                                               {"name": "band", "type": "score", "score": 1.7,
                                                "probabilities": [{"value": 0, "probability": 0.1}, {"value": 1, "probability": 0.1},
                                                                  {"value": 2, "probability": 0.8}]}]}, q)
    assert s1 == s2 and s1["front"]["value"] == 0.8 and s1["band"]["probabilities"]["2"] == 0.8


def test_front_row_reorders_mirror_pair_but_never_excludes_or_confirms(conn, add):
    lid, out = add(building_ping=43.92, parking_ping=7.81, floor_text="9樓/14樓")
    assert {c["sales_unit"] for c in live(out)} == {"D2", "D5"}
    assert [x["question"] for x in decide.suggest(conn, lid)][:2] == ["candidate_photo", "layout_front_row"]
    sent = []
    res = decide.ask_listing(conn, lid, ["layout_front_row"], [PNG], environ=ENV,
                             transport=fake({"layout_front_row": {"type": "noul", "noul": 0.9}}, sent=sent))
    a = assess_listing(conn, lid)["address"]
    c = live({"address": a})
    assert [x["sales_unit"] for x in c] == ["D2", "D5"] and len(c) == 2  # D2 靠路側：排第一，但 D5 仍保留
    assert a["outcome"] == "MULTIPLE_CANDIDATES" and a["resolved_fields"]["full_address"]["status"] != "CONFIRMED"
    assert any(h["rule_code"] == "S13" and h["effect"] < 0 for h in c[1]["rule_hits"])
    ev = conn.execute("SELECT * FROM evidence WHERE id=?", (res["evidence_ids"][0],)).fetchone()
    assert ev["kind"] == "INFERENCE" and json.loads(ev["value"])["answer"] == 0.9
    sys_origin = conn.execute("SELECT id FROM source_origin WHERE origin_type='SYSTEM'").fetchone()[0]
    assert sys_origin in json.loads(ev["origin_ids"])  # 根來源含 SYSTEM → 不能當成決定性證據
    photo = conn.execute("SELECT value FROM evidence WHERE field='listing_photo'").fetchone()[0]
    assert "base64" not in photo and "sha256" in photo  # 照片只存雜湊
    call = conn.execute("SELECT * FROM decision_call").fetchone()
    assert call["status"] == "OK" and call["image_count"] == 1 and call["cost_usd"] == 0.0001
    assert sent[0]["state"][1]["image_url"]["url"] == PNG
    review.retract_evidence_cascade(conn, res["evidence_ids"])  # 撤回模型答案 → 回到純規則的分數
    c2 = live(assess_listing(conn, lid))
    assert not any(h["rule_code"] == "S13" for x in c2 for h in x["rule_hits"])


def test_floor_band_for_unknown_floor(conn, add):
    lid, out = add(building_ping=37.775, parking_ping=7.807, rooms=2)
    assert out["address"]["candidate_count"] == 13
    assert any(s["question"] == "floor_band" for s in decide.suggest(conn, lid))
    decide.ask_listing(conn, lid, ["floor_band"], [PNG], environ=ENV,
                       transport=fake({"floor_band": {"type": "score", "score": 1.9, "probabilities": {"0": 0.02, "1": 0.08, "2": 0.9}}}))
    c = live(assess_listing(conn, lid))
    assert len(c) == 13 and c[0]["floor"] >= 11  # 高樓層排前面，其他樓層仍保留


def test_membership_text_is_soft(conn, add):
    lid, out = add(platform_community_ref=None, building_ping=37.8)
    assert out["membership"]["outcome"] == "UNDETERMINED"
    decide.ask_listing(conn, lid, ["membership_text"], [], environ=ENV,
                       transport=fake({"membership_text": {"type": "noul", "noul": 0.95}}))
    m = assess_listing(conn, lid)["membership"]
    assert m["outcome"] == "LIKELY_MEMBER" and any(h["rule_code"] == "M9m" for h in m["rule_hits"])  # 不會直接變成 MEMBER


def test_pair_question_never_auto_merges(conn, add):
    a, _ = add(building_ping=43.92, parking_ping=7.81, floor_text="9樓/14樓", rooms=2)
    b, _ = add(building_ping=43.92, parking_ping=7.81, floor_text="9樓/14樓", rooms=2)
    mid = next(m["identity_match_id"] for m in identity.evaluate_pairs(conn) if {m["listing_a_id"], m["listing_b_id"]} == {a, b})
    res = decide.ask_pair(conn, mid, environ=ENV, transport=fake({"same_dwelling": {"type": "noul", "noul": 0.99}}))
    assert res["match_outcome"] != "AUTO_MERGE_ELIGIBLE"
    score, hits, *_ = identity.score_pair(conn, a, b)
    assert any(h["rule_code"] == "P9m" for h in hits)
    pa, pb = [conn.execute("SELECT property_id FROM listing WHERE id=?", (x,)).fetchone()[0] for x in (a, b)]
    assert pa != pb


def test_budget_and_errors_are_logged(conn, add):
    lid, _ = add(building_ping=43.92, parking_ping=7.81, floor_text="9樓/14樓")
    env = {**ENV, "JEV_DECISION_MONTHLY_BUDGET_USD": "0.001"}
    with pytest.raises(decide.DecisionError) as e:
        decide.ask_listing(conn, lid, ["layout_front_row"], [PNG], environ=env, transport=fake({}, status=502))
    assert e.value.code == "PROVIDER_ERROR"
    assert conn.execute("SELECT status, http_status FROM decision_call").fetchone()[:] == ("ERROR", 502)
    decide.ask_listing(conn, lid, ["layout_front_row"], [PNG], environ=env, screen=False,
                       transport=fake({"layout_front_row": {"type": "noul", "noul": 0.6}}, cost=0.002))
    with pytest.raises(decide.DecisionError) as e:
        decide.ask_listing(conn, lid, ["layout_front_row"], [PNG], environ=env, transport=fake({}))
    assert e.value.code == "BUDGET_EXCEEDED" and e.value.status == 402
    with pytest.raises(decide.DecisionError) as e:
        decide.ask_listing(conn, lid, ["layout_front_row"], [], environ=env, transport=fake({}))
    assert e.value.code == "IMAGES_REQUIRED"


def test_api_flow(tmp_path, monkeypatch):
    from conftest import ROOT, validate
    from jev import registry
    from jev.api import App
    from jev.lifecycle import import_csv
    app = App(tmp_path / "d.db", api_key="k", outbox=str(tmp_path / "ob"))
    registry.load(app.conn)
    import_csv(app.conn, ROOT / "data" / "seed" / "lianshang-listings-2026-10-18.csv", "INITIAL")
    H = {"x-api-key": "k"}
    for k in list(ENV) + ["JEV_DECISION_PROVIDER"]:
        monkeypatch.delenv(k, raising=False)
    st = app.handle("GET", "/api/v1/decisions/status", H, {})[1]
    validate("DecisionStatus", st)
    assert st["enabled"] is False and "api_key" not in str(st).lower()
    status, err, _ = app.handle("POST", "/api/v1/listings/3/decisions", H, {"purposes": ["layout_front_row"], "images": [PNG], "consent": True})
    assert status == 409 and err["code"] == "DECISIONS_DISABLED"
    for k, v in ENV.items():
        monkeypatch.setenv(k, v)
    monkeypatch.setattr(decide, "_http", fake({"layout_front_row": {"type": "noul", "noul": 0.2}}))
    status, err, _ = app.handle("POST", "/api/v1/listings/3/decisions", H, {"purposes": ["layout_front_row"], "images": [PNG]})
    assert status == 422 and err["code"] == "CONSENT_REQUIRED"
    status, err, _ = app.handle("POST", "/api/v1/listings/3/decisions", H, {"purposes": ["layout_front_row"], "images": ["http://x/y.jpg"], "consent": True})
    assert status == 422
    body = {"purposes": ["layout_front_row"], "images": [PNG], "consent": True}
    validate("DecisionAskRequest", body)
    status, res, _ = app.handle("POST", "/api/v1/listings/3/decisions", H, body)
    assert status == 201
    validate("DecisionAskResult", res)
    d = app.handle("GET", "/api/v1/listings/3/decisions", H, {})[1]
    assert d["answers"] and d["answers"][0]["kind"] == "INFERENCE"
    for e in d["answers"]:
        validate("Evidence", e)
    validate("ListingDetail", app.handle("GET", "/api/v1/listings/3", H, {})[1])  # 新旗標也要符合契約
    cands = app.handle("GET", "/api/v1/listings/3/candidates", H, {})[1]
    assert sum(1 for c in cands if not c["excluded"]) == 2  # 只重排，不排除
    calls = app.handle("GET", "/api/v1/decisions/calls", H, {})[1]["items"]
    for c in calls:
        validate("DecisionCall", c)
    assert calls[0]["status"] == "OK" and calls[0]["image_count"] == 1


def test_reused_photo_is_not_scored(conn, add):  # 16 §3：PHOTO_REUSE → 照片推論不計分
    a, _ = add(building_ping=43.92, parking_ping=7.81, floor_text="9樓/14樓")
    b, _ = add(building_ping=43.92, parking_ping=7.81, floor_text="4樓/14樓")
    t = fake({"layout_front_row": {"type": "noul", "noul": 0.9}})
    decide.ask_listing(conn, a, ["layout_front_row"], [PNG], environ=ENV, transport=t)
    decide.ask_listing(conn, b, ["layout_front_row"], [PNG], environ=ENV, transport=t)  # 同一張照片用在另一則刊登
    out = assess_listing(conn, a)["address"]
    assert "PHOTO_REUSE" in out["flags"]
    assert not any(h["rule_code"] == "S13" for c in live({"address": out}) for h in c["rule_hits"])


def test_kitchen_side_scores_only_mapped_units(conn, add, monkeypatch):  # S15：名冊有廚房方向才計分
    lid, out = add(building_ping=51.37, parking_ping=7.81, floor_text="6樓/14樓", rooms=3)
    assert {c["sales_unit"] for c in live(out)} == {"C1", "C2"}
    assert "layout_kitchen_left" not in [x["question"] for x in decide.suggest(conn, lid)]  # 方向未知：不建議問
    monkeypatch.setattr(decide, "kitchen_side", lambda code: {"C1": "left", "C2": "right"}.get(code))
    assert any(x["question"] == "layout_kitchen_left" for x in decide.suggest(conn, lid))
    decide.ask_listing(conn, lid, ["layout_kitchen_left"], [PNG], environ=ENV,
                       transport=fake({"layout_kitchen_left": {"type": "noul", "noul": 0.85}}))
    c = live(assess_listing(conn, lid))
    assert [x["sales_unit"] for x in c][0] == "C1" and len(c) == 2
    assert any(h["rule_code"] == "S15" for h in c[0]["rule_hits"])


def test_photo_screening_drops_lobby_photos(conn, add):  # 大廳、外觀照片會誤導窗景與樓層判斷
    lid, _ = add(building_ping=43.92, parking_ping=7.81, floor_text="9樓/14樓")
    lobby = "data:image/jpeg;base64," + base64.b64encode(b"lobby").decode()
    kinds = iter(["common", "interior_view"])
    sent = []

    def transport(provider, payload, timeout):
        if "photo_kind" in payload["questions"]:
            k = next(kinds)
            return 200, {"answers": {"photo_kind": {"type": "choice", "choice": k, "probabilities": {k: 0.9}}}, "usage": {"cost": 0.00001}}
        sent.append(payload)
        return 200, {"answers": {"layout_front_row": {"type": "noul", "noul": 0.8}}, "usage": {"cost": 0.0001}}
    res = decide.ask_listing(conn, lid, ["layout_front_row"], [lobby, PNG], environ=ENV, transport=transport)
    assert [x["kind"] for x in res["screened"]] == ["common", "interior_view"] and res["photos_used"] == 1
    assert len(sent[0]["state"]) == 2 and sent[0]["state"][1]["image_url"]["url"] == PNG  # 只送室內有窗景的那張
    with pytest.raises(decide.DecisionError) as e:
        decide.ask_listing(conn, lid, ["layout_front_row"], [lobby], environ=ENV, transport=fake({}, kind={
            "photo_kind": {"type": "choice", "choice": "common", "probabilities": {"common": 0.9}}}))
    assert e.value.code == "NO_USABLE_PHOTOS"


def test_candidate_photo_choice_reorders_units(conn, add):  # S16：候選窮舉＋照片
    lid, out = add(building_ping=43.92, parking_ping=7.81, floor_text="9樓/14樓")
    sent = []
    answer = {"candidate_photo": {"type": "choice", "choice": "D5", "confidence": 0.6, "probabilities": {"D2": 0.2, "D5": 0.8}}}
    res = decide.ask_listing(conn, lid, ["candidate_photo"], [PNG], environ=ENV, transport=fake(answer, sent=sent))
    assert set(sent[0]["questions"]["candidate_photo"]["criteria"]) == {"D2", "D5"}
    c = live(assess_listing(conn, lid))
    assert [x["sales_unit"] for x in c] == ["D5", "D2"] and len(c) == 2  # 只重排，不排除
    s16 = [h for h in c[0]["rule_hits"] if h["rule_code"] == "S16"][0]
    assert s16["effect"] == pytest.approx(0.18) and res["answers"]["candidate_photo"]["value"] == "D5"
    one, _ = add(building_ping=37.775, parking_ping=7.807, floor_text="9樓/14樓")
    with pytest.raises(decide.DecisionError) as e:
        decide.ask_listing(conn, one, ["candidate_photo"], [PNG], environ=ENV, transport=fake({}))
    assert e.value.code == "NOT_APPLICABLE"  # C3 只有一個戶別


def test_fetch_photos_allowlist_and_limits():
    class Resp:
        def __init__(self, ctype, data):
            self.headers, self.data = {"Content-Type": ctype}, data

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def read(self, n):
            return self.data[:n]
    ok = decide.fetch_photos(["https://img1.591.com.tw/house/a.jpg"], opener=lambda req, timeout: Resp("image/jpeg", b"\xff\xd8x"))
    assert ok[0].startswith("data:image/jpeg;base64,")
    for bad in (["https://evil.example/a.jpg"], ["http://img1.591.com.tw/a.jpg"], ["https://img1.591.com.tw.evil.com/a.jpg"]):
        with pytest.raises(decide.DecisionError):
            decide.fetch_photos(bad, opener=lambda req, timeout: Resp("image/jpeg", b"x"))
    with pytest.raises(decide.DecisionError):
        decide.fetch_photos(["https://img1.591.com.tw/a.jpg"], opener=lambda req, timeout: Resp("text/html", b"<html>"))
    with pytest.raises(decide.DecisionError):
        decide.fetch_photos(["https://img1.591.com.tw/a.jpg"] * 7)


def test_api_does_not_download_when_disabled(tmp_path, monkeypatch):
    from jev import registry
    from jev.api import App
    app = App(tmp_path / "x.db", api_key="k")
    registry.load(app.conn)
    lid = app.handle("POST", "/api/v1/listings", {"x-api-key": "k"}, {"source_code": "SRC-591", "external_id": "P1", "snapshot": {
        "total_price": 21000000, "building_ping": 43.92, "parking_ping": 7.81, "floor_text": "9樓/14樓", "platform_community_ref": "591:9488",
        "community_text": "聯上世界", "address_text": "桃園市大園區高鐵南路一段", "parking_included": "INCLUDED"}})[1]["id"]
    monkeypatch.setenv("JEV_DECISION_PROVIDER", "none")
    called = []
    monkeypatch.setattr(decide, "fetch_photos", lambda urls: called.append(urls) or [])
    status, err, _ = app.handle("POST", f"/api/v1/listings/{lid}/decisions", {"x-api-key": "k"},
                                {"purposes": ["candidate_photo"], "photo_urls": ["https://img1.591.com.tw/a.jpg"], "consent": True})
    assert status == 409 and not called



def test_floor_band_ignored_when_floor_known(conn, add):
    lid, _ = add(building_ping=43.92, parking_ping=7.81, floor_text="9樓/14樓")
    decide.ask_listing(conn, lid, ["floor_band"], [PNG], environ=ENV,
                       transport=fake({"floor_band": {"type": "score", "score": 1, "probabilities": {"0": 0.05, "1": 0.9, "2": 0.05}}}))
    assert not any(h["rule_code"] == "S14" for c in live(assess_listing(conn, lid)) for h in c["rule_hits"])
