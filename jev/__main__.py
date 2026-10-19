"""JEV CLI：python -m jev <指令>。資料庫預設在 data/jev.db（--db 或 .env 的 JEV_DB 可改）。"""

import argparse
import json
import sys

from . import __version__, assess, identity, lifecycle, notify, registry, report, review
from .db import connect


def _backup(conn, path, reason):
    """匯入前先備份（只對檔案資料庫；資料庫還沒有任何刊登時略過）。"""
    if path != ":memory:" and conn.execute("SELECT 1 FROM listing LIMIT 1").fetchone():
        from .db import backup
        print(f"匯入前備份：{backup(conn, path, reason=reason)}")


def main(argv=None):
    sys.stdout.reconfigure(encoding="utf-8")
    import os
    from . import config
    env_keys = config.load_env()
    p = argparse.ArgumentParser(prog="jev", description="JEV：聯上世界房源監控與門牌推斷（MVP）")
    p.add_argument("--db", default=None, help="資料庫路徑（預設 JEV_DB 或 data/jev.db）")
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("init", help="建立資料庫並載入名冊")
    imp = sub.add_parser("import", help="匯入人工記錄 CSV")
    imp.add_argument("csv")
    imp.add_argument("--mode", choices=["INITIAL", "INCREMENTAL"], default="INCREMENTAL")
    sub.add_parser("assess", help="重新評估全部刊登")
    sub.add_parser("match", help="計算同屋配對候選")
    rep = sub.add_parser("report", help="輸出候選報告（Markdown）")
    rep.add_argument("--out")
    nt = sub.add_parser("notify", help="處理通知（未設定 SMTP 時寫入 outbox）")
    nt.add_argument("--outbox", default=None, help="預設：資料庫所在資料夾的 outbox")
    cyc = sub.add_parser("cycle", help="一次完成：匯入新的人工記錄 → 重算同屋配對 → 報告 → 通知")
    cyc.add_argument("csv")
    cyc.add_argument("--mode", choices=["INITIAL", "INCREMENTAL"], default="INCREMENTAL")
    cyc.add_argument("--report", default="data/report.md")
    cyc.add_argument("--outbox", default=None, help="預設：資料庫所在資料夾的 outbox")
    sh = sub.add_parser("show", help="顯示一則刊登的判定與候選")
    sh.add_argument("listing_id", type=int)
    tk = sub.add_parser("tasks", help="列出待覆核任務（預設不列名冊任務）")
    tk.add_argument("--all", action="store_true", help="也列出名冊 mapping 與推導規則的覆核任務（P5）")
    ev = sub.add_parser("evidence", help="新增使用者佐證（JSON 檔，格式同 API 的 EvidenceCreate）")
    ev.add_argument("evidence_json")
    dl = sub.add_parser("delist", help="標記刊登已下架")
    dl.add_argument("listing_id", type=int)
    dl.add_argument("--reason", choices=["MARKED_SOLD", "MARKED_REMOVED", "MANUAL"], default="MANUAL")
    dl.add_argument("--rationale", required=True)
    mg = sub.add_parser("merge", help="人工合併同屋（property id）")
    mg.add_argument("property_ids", type=int, nargs="+")
    mg.add_argument("--evidence", type=int, nargs="+", required=True, help="佐證 id")
    mg.add_argument("--rationale", required=True)
    sp = sub.add_parser("split", help="拆分 property：每組以逗號分隔刊登 id，例如 1,2 3")
    sp.add_argument("property_id", type=int)
    sp.add_argument("groups", nargs="+")
    sp.add_argument("--rationale", required=True)
    rv = sub.add_parser("revert", help="撤回一次合併")
    rv.add_argument("operation_id", type=int)
    rv.add_argument("--rationale", required=True)
    dec = sub.add_parser("decide", help="送出覆核決定（JSON 檔）")
    dec.add_argument("task_id", type=int)
    dec.add_argument("decision_json")
    lv = sub.add_parser("lvr", help="篩選本機的官方實價登錄檔案（zip 或 csv），不連網；--import 存進資料庫")
    lv.add_argument("path", nargs="+")
    lv.add_argument("--import", dest="do_import", action="store_true")
    sub.add_parser("overview", help="社區總覽：名冊、刊登、行情、缺口與下一步")
    srv = sub.add_parser("serve", help="啟動本機 API（127.0.0.1）")
    srv.add_argument("--port", type=int, default=8787)
    dm = sub.add_parser("demo", help="建立展示用資料庫（真實人工記錄＋標示為 DEMO 的模擬刊登），然後可用 serve 開啟")
    dm.add_argument("--outbox", default="data/demo-outbox")
    sub.add_parser("config", help="顯示目前設定（.env 與環境變數；金鑰遮蔽，不連網）")
    ak = sub.add_parser("ask", help="請 JEV 決策模型判斷一則刊登（會送資料到外部模型，需加 --yes）")
    ak.add_argument("listing_id", type=int)
    ak.add_argument("--purpose", action="append", choices=["candidate_photo", "layout_front_row", "layout_kitchen_left", "floor_band", "membership_text"],
                    help="題目，可重複；省略時使用系統建議的題目")
    ak.add_argument("--image", action="append", default=[], help="照片路徑，可重複（最多 4 張）")
    ak.add_argument("--yes", action="store_true", help="確認同意把房屋欄位與照片送到設定的外部模型")
    dc = sub.add_parser("decisions", help="決策模型狀態、本月花費與最近的呼叫紀錄")
    dc.add_argument("--limit", type=int, default=20)
    ev2 = sub.add_parser("eval", help="用有正確答案的題目評估決策模型（--dry-run 不連網）")
    ev2.add_argument("set", help="題目檔（JSONL；範本 templates/eval-template.jsonl）")
    ev2.add_argument("--dry-run", action="store_true", help="只產生請求內容，不連網、不花錢")
    ev2.add_argument("--out", help="報告輸出位置（預設 data/eval/report-時間.md）")
    sub.add_parser("version")
    a = p.parse_args(argv)
    explicit_db = a.db is not None
    a.db = a.db or os.environ.get("JEV_DB") or "data/jev.db"
    if a.cmd == "config":
        print(config.report(env_keys=env_keys))
        return 0
    if a.cmd == "version":
        print(__version__)
        return 0
    if a.cmd == "demo":
        from pathlib import Path
        db = Path(a.db if explicit_db else "data/demo.db")
        if db.exists():
            print(f"{db} 已存在；要重建請先自行刪除或改用 --db 指定新檔名。開啟：python -m jev --db {db} serve")
            return 1
        db.parent.mkdir(parents=True, exist_ok=True)
        from .demo import build
        out = build(connect(str(db)), a.outbox)
        print(f"已建立 {db}：刊登 {out['listings']} 則、通知 {out['notifications']} 則、待覆核 {out['tasks']} 件")
        print(f"開啟 DEMO：python -m jev --db {db} serve，然後瀏覽 http://127.0.0.1:8787/")
        return 0
    conn = connect(a.db)
    from pathlib import Path as _P
    if getattr(a, "outbox", "x") is None:
        a.outbox = str(_P(a.db).parent / "outbox")
    for change in getattr(conn, "migration_log", []):
        print("資料庫升級：" + change)
    if a.cmd == "init":
        rv = registry.load(conn)
        print(f"名冊已載入（registry_version={rv}），戶別 {len(registry.units(conn))} 個")
    elif a.cmd == "import":
        registry.load(conn)
        _backup(conn, a.db, "import")
        try:
            res = lifecycle.import_csv(conn, a.csv, a.mode)
        except ValueError as e:
            print(f"匯入失敗：{e}")
            return 1
        print(f"匯入 {len(res)} 筆：新增 {sum(r['created'] for r in res)}，有變更 {sum(r['changed'] for r in res)}")
    elif a.cmd == "assess":
        for (lid,) in conn.execute("SELECT id FROM listing ORDER BY id").fetchall():
            assess.assess_listing(conn, lid, trigger="MANUAL")
        print("已重新評估")
    elif a.cmd == "match":
        res = identity.evaluate_pairs(conn)
        for m in sorted(res, key=lambda x: -x["score"])[:20]:
            print(f"{m['listing_a_id']}↔{m['listing_b_id']} score={m['score']:.2f} {m['outcome']} conflicts={m['conflicts']}")
        print(f"配對候選 {len(res)} 組")
    elif a.cmd == "report":
        md = report.markdown(conn)
        if a.out:
            open(a.out, "w", encoding="utf-8").write(md)
            print(f"已寫入 {a.out}")
        else:
            print(md)
    elif a.cmd == "notify":
        res = notify.process(conn, a.outbox)
        print(f"通知 {len(res)} 則：" + ", ".join(f"{r['rule']}/{r['status']}" for r in res))
    elif a.cmd == "cycle":
        registry.load(conn)
        _backup(conn, a.db, "cycle")
        try:
            res = lifecycle.import_csv(conn, a.csv, a.mode)
        except ValueError as e:
            print(f"匯入失敗：{e}")
            return 1
        pairs = identity.evaluate_pairs(conn)
        md = report.markdown(conn)
        open(a.report, "w", encoding="utf-8").write(md)
        sent = notify.process(conn, a.outbox)
        tasks = conn.execute("SELECT COUNT(*) FROM review_task WHERE status='OPEN' AND priority < 5").fetchone()[0]
        print(f"匯入 {len(res)} 筆（新增 {sum(r['created'] for r in res)}、內容有變 {sum(r['changed'] for r in res)}）；"
              f"同屋配對候選 {len(pairs)} 組；待覆核 {tasks} 件；通知 {len(sent)} 則；報告 {a.report}")
    elif a.cmd == "show":
        row = conn.execute("SELECT * FROM listing WHERE id=?", (a.listing_id,)).fetchone()
        if not row:
            print("找不到刊登")
            return 1
        from .serialize import listing_detail
        d = listing_detail(conn, row)
        snap, mem, addr = d["latest_snapshot"], d["membership_assessment"], d["address_assessment"]
        print(f"#{d['id']} {d['source_code']} {d['external_id']} {d['status']} property={d['property_id']} {d['url'] or ''}")
        print(f"  {snap['total_price']:,} 元｜{snap['building_ping']} 坪｜{snap['floor_text'] or '樓層未揭露'}｜{snap['rooms'] or '?'} 房")
        print(f"  社區歸屬 {mem['outcome']}（rule_score {mem['rule_score']}）旗標 {mem['flags']}")
        print(f"  門牌 {addr['outcome']}（候選 {addr['candidate_count']}）旗標 {addr['flags']}")
        for k, v in addr["resolved_fields"].items():
            print(f"    {k}: {v['status']} {v['value'] if v['value'] is not None else ''}")
        for t in report.top_candidates(conn, addr["id"], 8):
            print(f"    候選 {t['code']} → 高鐵南路一段{t['number']}號{t['floor']}樓  rule_score {t['rule_score']:.2f}")
    elif a.cmd == "tasks":
        rows = conn.execute(
            "SELECT t.*, l.external_id, s.code, su.code AS su_code, r.code AS rule_code, m.listing_a_id, m.listing_b_id"
            " FROM review_task t LEFT JOIN listing l ON l.id=t.listing_id LEFT JOIN source s ON s.id=l.source_id"
            " LEFT JOIN registry_mapping rm ON rm.id=t.registry_mapping_id LEFT JOIN sales_unit su ON su.id=rm.from_id"
            " LEFT JOIN address_derivation_rule r ON r.id=t.derivation_rule_id LEFT JOIN identity_match m ON m.id=t.identity_match_id"
            " WHERE t.status='OPEN' AND (? OR t.priority < 5) ORDER BY t.priority, t.id", (int(a.all),)).fetchall()
        for t in rows:
            if t["task_type"] == "ADDRESS_CANDIDATE":
                tops = report.top_candidates(conn, t["base_assessment_id"], 3)
                what = f"刊登#{t['listing_id']} {t['code']} {t['external_id']} " + "、".join(
                    f"{c['code']}={c['number']}號{c['floor']}樓" for c in tops)
            elif t["task_type"] == "COMMUNITY_MEMBERSHIP":
                what = f"刊登#{t['listing_id']} {t['code']} {t['external_id']} 是否屬於聯上世界"
            elif t["task_type"] == "LISTING_MATCH":
                what = f"刊登#{t['listing_a_id']} ↔ #{t['listing_b_id']} 是否同一戶"
            elif t["task_type"] == "REGISTRY_MAPPING":
                what = f"戶別 {t['su_code']} → 門牌號是否正確"
            else:
                what = f"推導規則 {t['rule_code']} 是否正確"
            print(f"#{t['id']} v{t['version']} P{t['priority']} {t['task_type']} {what}")
        hidden = conn.execute("SELECT COUNT(*) FROM review_task WHERE status='OPEN' AND priority=5").fetchone()[0]
        if not a.all and hidden:
            print(f"（另有 {hidden} 件名冊覆核任務，用 tasks --all 顯示）")
    elif a.cmd == "evidence":
        d = json.load(open(a.evidence_json, encoding="utf-8"))
        try:
            eid = review.add_user_evidence(conn, d.get("listing_id"), d)
        except review.ReviewError as e:
            print(f"{e.status} {e.code}: {e}")
            return 1
        if d.get("listing_id"):
            assess.assess_listing(conn, d["listing_id"], trigger="EVIDENCE_CHANGED")
        conn.commit()
        print(f"已新增佐證 #{eid}")
    elif a.cmd in ("delist", "merge", "split", "revert"):
        try:
            if a.cmd == "delist":
                lifecycle.mark_delisted(conn, a.listing_id, a.reason, a.rationale)
                print(f"刊登 #{a.listing_id} 已標記下架")
            elif a.cmd == "merge":
                print(f"合併完成，操作 #{identity.merge(conn, a.property_ids, a.rationale, a.evidence)}")
            elif a.cmd == "split":
                groups = [[int(x) for x in g.split(",")] for g in a.groups]
                print(f"拆分完成，操作 #{identity.split(conn, a.property_id, groups, a.rationale)}")
            else:
                print(f"撤回完成，操作 #{identity.revert(conn, a.operation_id, a.rationale)}")
        except ValueError as e:
            print(f"失敗：{e}")
            return 1
    elif a.cmd == "decide":
        d = json.load(open(a.decision_json, encoding="utf-8"))
        try:
            out = review.submit_decision(conn, a.task_id, d.get("expected_version"), d["decision"], d.get("rationale"),
                                         d.get("new_evidence"), d.get("evidence_ids"), d.get("candidate_id"), d.get("address_unit_id"))
            print(json.dumps(out, ensure_ascii=False, indent=2))
        except review.ReviewError as e:
            print(f"{e.status} {e.code}: {e}")
            return 1
    elif a.cmd == "lvr":
        from .lvr import filter_community, import_transactions
        for path in a.path:
            if a.do_import:
                added, dup = import_transactions(conn, path)
                print(f"{path}：新增 {added} 筆、已存在 {dup} 筆")
            else:
                for r in filter_community(path):
                    print(r)
    elif a.cmd in ("ask", "decisions", "eval"):
        from . import decide
        if a.cmd == "decisions":
            st = decide.status(conn)
            print(f"決策模型：{'已啟用' if st['enabled'] else '未啟用'}｜{st['provider']} · {st['model'] or '—'}｜本月 US${st['month_spend_usd']:.4f}"
                  + (f" / {st['monthly_budget_usd']}" if st["monthly_budget_usd"] is not None else ""))
            for prob in st["problems"]:
                print(f"  ⚠ {prob}")
            for r in conn.execute("SELECT * FROM decision_call ORDER BY id DESC LIMIT ?", (a.limit,)):
                print(f"  #{r['id']} {r['created_at']} {r['purpose']} 刊登#{r['listing_id'] or '—'} {r['status']} {r['latency_ms'] or '—'}ms "
                      f"US${(r['cost_usd'] or 0):.5f} 圖{r['image_count']} {r['error'] or ''}")
        elif a.cmd == "ask":
            purposes = a.purpose or [x["question"] for x in decide.suggest(conn, a.listing_id) if x["question"]]
            purposes = [p for p in purposes if a.image or not (p == "candidate_photo" or decide.QUESTIONS[p]["needs_images"])]
            if not purposes:
                print("沒有可以問的題目（需要照片的題目請加 --image）")
                return 1
            st = decide.status(conn)
            print(f"將送到 {st['provider']} · {st['model']}：題目 {', '.join(purposes)}；照片 {len(a.image)} 張")
            if not a.yes:
                print("沒有加 --yes：不送出。確認同意後再執行一次並加上 --yes")
                return 1
            from .evaluate import data_url
            try:
                with conn.transaction():
                    out = decide.ask_listing(conn, a.listing_id, purposes, [data_url(x) for x in a.image[:4]])
            except decide.DecisionError as e:
                print(f"{e.status} {e.code}：{e}")
                return 1
            for k, v in out["answers"].items():
                print(f"  {decide.label(k)}：{v['value']}  {v.get('probabilities') or ''}")
            print(f"門牌判定：{out['address_outcome']}；社區歸屬：{out['membership_outcome']}（用 show {a.listing_id} 看新的排序）")
        else:
            from datetime import datetime
            from pathlib import Path
            from . import evaluate
            items = evaluate.load(a.set)
            try:
                res = evaluate.run(conn, items, dry_run=a.dry_run)
            except decide.DecisionError as e:
                print(f"{e.status} {e.code}：{e}")
                return 1
            if a.dry_run:
                print(json.dumps(res, ensure_ascii=False, indent=2))
                return 0
            out = Path(a.out or f"data/eval/report-{datetime.now().strftime('%Y%m%d-%H%M%S')}.md")
            out.parent.mkdir(parents=True, exist_ok=True)
            out.write_text(evaluate.report_markdown(res, Path(a.set).name), encoding="utf-8")
            o = res["overall"]
            print(f"{o.get('answered', 0)}／{o['n']} 題有答案；答對率 {o.get('accuracy', '—')}；Brier {o.get('brier', '—')}；ECE {o.get('ece', '—')}；報告 {out}")
    elif a.cmd == "overview":
        from .overview import build
        o = build(conn)
        s = o["summary"]
        print(f"聯上世界：{s['units']} 個戶別（推估 {s['dwellings_estimate']} 戶住宅），門牌對照已驗證 {s['mapping_verified']}／{s['units']}")
        print(f"刊登 {s['listings']} 則（上架 {s['active']}、屬於社區 {s['member']}）；有樓層 {s['floor_known']}、戶別唯一 {s['unit_unique']}、"
              f"門牌 ≤ 2 個候選 {s['within_two']}、已確認 {s['confirmed']}；同屋群組 {s['groups']}；待覆核 {s['open_tasks']}")
        print("\n開價行情（依坪型）：")
        for m in o["market"]:
            print(f"  {m['group']:<14} {m['count']} 則  {m['min_price']/1e4:,.0f}–{m['max_price']/1e4:,.0f} 萬  單價中位數 {m['median_unit_price_wan'] or '—'} 萬/坪（含車位價、扣除車位坪）")
        print(f"\n官方實價登錄成交：{o['transactions']['total']} 筆")
        print("\n缺口與下一步：")
        for g in o["gaps"]:
            print(f"  {g['priority']}. {g['title']} — {g['action']}")
    elif a.cmd == "serve":
        from .api import serve
        serve(a.db, a.port)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
