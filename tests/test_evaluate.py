"""決策模型評估：題目檔、dry-run 不連網、答對率與校準指標。"""

import json

import pytest
from conftest import ROOT

from jev import evaluate
from test_decide import ENV, fake


def _set(tmp_path, lines):
    f = tmp_path / "set.jsonl"
    f.write_text("# 註解\n" + "\n".join(json.dumps(x, ensure_ascii=False) for x in lines) + "\n", encoding="utf-8")
    return f


def test_template_parses():
    items = evaluate.load(ROOT / "templates" / "eval-template.jsonl")
    assert {i["question"] for i in items} >= {"layout_front_row", "floor_band", "membership_text"}


def test_bad_lines_are_rejected(tmp_path):
    with pytest.raises(ValueError, match="question"):
        evaluate.load(_set(tmp_path, [{"question": "nope", "gold": 1}]))
    with pytest.raises(ValueError, match="gold"):
        evaluate.load(_set(tmp_path, [{"question": "membership_text"}]))


def test_dry_run_sends_nothing_and_hides_images(conn, tmp_path):
    img = tmp_path / "p.jpg"
    img.write_bytes(b"\xff\xd8 fake jpeg")
    items = evaluate.load(_set(tmp_path, [{"id": "a", "question": "layout_front_row", "images": [str(img)], "gold": True}]))
    called = []
    res = evaluate.run(conn, items, dry_run=True, environ={}, transport=lambda *x: called.append(x))
    assert res["dry_run"] and not called
    body = json.dumps(res["requests"][0]["payload"])
    assert "base64" not in body and "sha256:" in body
    assert conn.execute("SELECT COUNT(*) FROM decision_call").fetchone()[0] == 0


def test_metrics(conn, tmp_path):
    items = evaluate.load(_set(tmp_path, [
        {"id": "t1", "question": "membership_text", "state": "聯上世界 三房", "gold": True},
        {"id": "t2", "question": "membership_text", "state": "近聯上世界", "gold": False},
        {"id": "t3", "question": "membership_text", "state": "聯上世界", "gold": True}]))
    answers = iter([0.9, 0.8, 0.6])  # 第 2 題答錯（80% 說是）

    def transport(provider, payload, timeout):
        return 200, {"answers": {"membership_text": {"type": "noul", "noul": next(answers)}}, "usage": {"input_tokens": 100, "cost": 0.00001}}
    res = evaluate.run(conn, items, environ=ENV, transport=transport)
    o = res["overall"]
    assert o["answered"] == 3 and o["accuracy"] == pytest.approx(2 / 3, abs=0.001)
    assert o["brier"] == pytest.approx(((1 - 0.9) ** 2 + (1 - 0.2) ** 2 + (1 - 0.6) ** 2) / 3, abs=1e-4)
    md = evaluate.report_markdown(res, "set.jsonl")
    assert "答對率" in md and "| t2 |" in md
    assert conn.execute("SELECT COUNT(*) FROM decision_call WHERE purpose='eval'").fetchone()[0] == 3


def test_score_question_metrics(conn, tmp_path):
    img = tmp_path / "f.jpg"
    img.write_bytes(b"\xff\xd8 fake")
    items = evaluate.load(_set(tmp_path, [{"id": "f1", "question": "floor_band", "images": [str(img)], "gold": 2},
                                          {"id": "f2", "question": "floor_band", "images": [], "gold": 0}]))
    res = evaluate.run(conn, items, environ=ENV, transport=fake(
        {"floor_band": {"type": "score", "score": 1.8, "probabilities": {"0": 0.1, "1": 0.1, "2": 0.8}}}))
    rows = {r["id"]: r for r in res["rows"]}
    assert rows["f1"]["correct"] is True and rows["f1"]["p_gold"] == 0.8
    assert rows["f2"]["correct"] is None and rows["f2"]["error"].startswith("IMAGES_REQUIRED")  # 缺照片：記錄錯誤、繼續下一題
    assert res["overall"]["answered"] == 1
