"""決策模型評估：用有正確答案的題目測答對率與校準（docs/research/decision-models-analysis.md §6）。

題目檔是 JSONL，每行一題：
  {"id": "d2-view-01", "question": "layout_front_row", "images": ["data/eval/photos/a.jpg"], "gold": true,
   "state": {"權狀坪數": 43.92, "樓層": "9樓/14樓"}, "note": "現場確認是 103 號（D2）"}
- question：decide.QUESTIONS 的名稱（layout_front_row、floor_band、membership_text、same_dwelling）
- gold：noul 題是 true／false；score 題是等級索引（floor_band：0 低、1 中、2 高）
- images：本機照片路徑（只在記憶體中轉成 base64，不會複製或保存）
--dry-run 只產生請求內容（圖片以雜湊代替），不連網、不花錢。
"""

import base64
import hashlib
import json
import math
import mimetypes
from datetime import datetime
from pathlib import Path

from . import decide
from .config import decision_settings


def load(path):
    items = []
    for n, line in enumerate(Path(path).read_text(encoding="utf-8-sig").splitlines(), start=1):
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        it = json.loads(line)
        if it.get("question") not in decide.QUESTIONS:
            raise ValueError(f"第 {n} 行：question 必須是 {', '.join(decide.QUESTIONS)}")
        if "gold" not in it:
            raise ValueError(f"第 {n} 行：缺少 gold（正確答案）")
        it.setdefault("id", f"line-{n}")
        items.append(it)
    return items


def data_url(path):
    p = Path(path)
    mime = mimetypes.guess_type(p.name)[0] or "image/jpeg"
    return f"data:{mime};base64," + base64.b64encode(p.read_bytes()).decode()


def _probability_of_gold(q, answer, gold):
    """回傳（預測正確?, 模型給正確答案的機率, 預測的機率分布）。"""
    if answer is None or answer.get("value") is None:
        return None, None, None
    if q["type"] == "noul":
        p = float(answer["value"])
        return (p >= 0.5) == bool(gold), (p if gold else 1 - p), {"true": p, "false": 1 - p}
    probs = {int(k): float(v) for k, v in (answer.get("probabilities") or {}).items() if str(k).lstrip("-").isdigit() and v is not None}
    if not probs:
        return None, None, None
    pred = max(probs, key=probs.get)
    return pred == int(gold), probs.get(int(gold), 0.0), probs


def metrics(rows):
    """答對率、Brier（正確答案機率的平方誤差）、log loss、ECE（5 個區間的校準誤差）。"""
    scored = [r for r in rows if r["correct"] is not None]
    if not scored:
        return {"n": len(rows), "answered": 0}
    acc = sum(r["correct"] for r in scored) / len(scored)
    brier = sum((1 - r["p_gold"]) ** 2 for r in scored) / len(scored)
    logloss = -sum(math.log(max(r["p_gold"], 1e-6)) for r in scored) / len(scored)
    bins = [[] for _ in range(5)]
    for r in scored:
        conf = max(r["dist"].values())
        bins[min(4, int(conf * 5))].append((conf, r["correct"]))
    ece, table = 0.0, []
    for i, b in enumerate(bins):
        if not b:
            continue
        avg_conf = sum(c for c, _ in b) / len(b)
        avg_acc = sum(1 for _, ok in b if ok) / len(b)
        ece += len(b) / len(scored) * abs(avg_conf - avg_acc)
        table.append({"bin": f"{i * 20}–{i * 20 + 20}%", "n": len(b), "confidence": round(avg_conf, 3), "accuracy": round(avg_acc, 3)})
    return {"n": len(rows), "answered": len(scored), "accuracy": round(acc, 3), "brier": round(brier, 4),
            "log_loss": round(logloss, 4), "ece": round(ece, 4), "calibration": table}


def run(conn, items, dry_run=False, environ=None, transport=None):
    s = decision_settings(environ)
    provider = s.provider or decision_settings({"JEV_DECISION_PROVIDER": "openrouter", "OPENROUTER_API_KEY": "dry-run",
                                                "OPENROUTER_DECISION_MODEL": "typesafe/jev-1.14"}).provider
    rows, payloads = [], []
    for it in items:
        q = decide.QUESTIONS[it["question"]]
        images = [data_url(p) for p in it.get("images", [])]
        state = it.get("state") or ""
        if dry_run:
            body = json.dumps(decide.build_request(provider, {it["question"]: q}, state, images), ensure_ascii=False)
            for u in images:
                body = body.replace(u, "sha256:" + hashlib.sha256(u.encode()).hexdigest()[:16])
            payloads.append({"id": it["id"], "endpoint": provider.endpoint, "payload": json.loads(body)})
            continue
        try:
            call_id, ans = decide.ask(conn, "eval", [it["question"]], state, images, environ=environ, transport=transport)
            conn.commit()
            answer, err = ans.get(it["question"]), None
        except decide.DecisionError as e:
            if e.code in ("BUDGET_EXCEEDED", "DECISIONS_DISABLED"):
                raise
            call_id, answer, err = None, None, f"{e.code}：{e}"
        correct, p_gold, dist = _probability_of_gold(q, answer, it["gold"])
        rows.append({"id": it["id"], "question": it["question"], "gold": it["gold"], "answer": answer and answer.get("value"),
                     "correct": correct, "p_gold": p_gold, "dist": dist, "call_id": call_id, "error": err, "note": it.get("note")})
    if dry_run:
        return {"dry_run": True, "provider": provider.name, "model": provider.model, "requests": payloads}
    by_q = {}
    for r in rows:
        by_q.setdefault(r["question"], []).append(r)
    return {"dry_run": False, "provider": provider.name, "model": provider.model, "overall": metrics(rows),
            "by_question": {k: metrics(v) for k, v in by_q.items()}, "rows": rows}


def report_markdown(result, set_name):
    lines = [f"# JEV 決策模型評估：{set_name}", "",
             f"- 時間：{datetime.now().strftime('%Y-%m-%d %H:%M')}", f"- 模型：{result['provider']} · {result['model']}", ""]
    o = result["overall"]
    if not o.get("answered"):
        return "\n".join(lines + ["沒有任何題目得到答案。"]) + "\n"
    lines += [f"整體：{o['answered']}／{o['n']} 題有答案；答對率 **{o['accuracy']:.0%}**；Brier {o['brier']}；log loss {o['log_loss']}；"
              f"校準誤差（ECE）{o['ece']}", "", "| 題目 | 題數 | 答對率 | Brier | ECE |", "|------|------|--------|-------|-----|"]
    for k, m in result["by_question"].items():
        if m.get("answered"):
            lines.append(f"| {decide.QUESTIONS[k]['label']} | {m['answered']} | {m['accuracy']:.0%} | {m['brier']} | {m['ece']} |")
    lines += ["", "## 校準（模型說的信心 vs. 實際答對率）", "", "| 信心區間 | 題數 | 平均信心 | 實際答對率 |", "|---|---|---|---|"]
    for b in o["calibration"]:
        lines.append(f"| {b['bin']} | {b['n']} | {b['confidence']:.0%} | {b['accuracy']:.0%} |")
    lines += ["", "## 逐題", "", "| id | 題目 | 正確答案 | 模型答案 | 對？ | 正確答案的機率 | 備註 |", "|---|---|---|---|---|---|---|"]
    for r in result["rows"]:
        ok = "—" if r["correct"] is None else ("✓" if r["correct"] else "✗")
        p = "—" if r["p_gold"] is None else f"{r['p_gold']:.0%}"
        lines.append(f"| {r['id']} | {r['question']} | {r['gold']} | {r['answer'] if r['answer'] is not None else (r['error'] or '—')} | {ok} | {p} | {r['note'] or ''} |")
    lines += ["", "> 題數少時，答對率與校準都不穩定；至少 20–30 題再下結論。答案正確的機率低於 50% 的題目，代表模型在這類照片上不可靠。"]
    return "\n".join(lines) + "\n"
