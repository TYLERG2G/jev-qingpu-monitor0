"""本機 REST API（標準函式庫 http.server；只綁 127.0.0.1）。契約見 api/openapi.yaml（MVP 子集）。"""

import hashlib
import json
import logging
import os
import re
import secrets
import threading
import time
import traceback
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from . import __version__, decide, identity, lifecycle, overview, review, serialize  # noqa: F401
from .assess import assess_listing
from .db import connect, j, now, unj
from .events import to_api as event_to_api

BASE = "/api/v1"
LOG = logging.getLogger("jev.api")
ALLOWED_HOSTS = ("127.0.0.1", "localhost", "[::1]")
UI_PAGE = (Path(__file__).parent / "ui" / "index.html").read_text(encoding="utf-8") if (Path(__file__).parent / "ui" / "index.html").exists() else ""
LOCK = threading.Lock()


def _page(items):
    return {"items": items, "next_cursor": None}


class App:
    def __init__(self, db_path, api_key=None, outbox=None):
        self.db_path = str(db_path)
        self.conn = connect(db_path)
        self.api_key = api_key or os.environ.get("JEV_API_KEY") or local_api_key(Path(self.db_path).parent)
        self.outbox = outbox or str(Path(self.db_path).parent / "outbox")

    def _notify(self):
        """寫入成功後處理通知（未設定 SMTP 時寫入 outbox）；通知失敗不影響已完成的寫入。"""
        from . import notify
        try:
            notify.process(self.conn, self.outbox)
        except OSError:
            pass

    # ---- 路由
    def handle(self, method, path, headers, body):
        """路由入口：所有例外都轉成 problem 回應，寫入操作一律在一個交易裡完成。"""
        host = (headers.get("host") or "").rsplit(":", 1)[0] if not (headers.get("host") or "").startswith("[") else headers["host"].split("]")[0] + "]"
        if headers.get("host") and host not in ALLOWED_HOSTS:
            return 403, serialize.problem(403, "FORBIDDEN_HOST", "只接受 127.0.0.1 或 localhost 的請求"), {}
        if method in ("POST", "PATCH", "DELETE") and not isinstance(body, dict):
            return 400, serialize.problem(400, "VALIDATION_ERROR", "請求本體必須是 JSON 物件"), {}
        try:
            if method == "GET":  # 所有請求共用一個 SQLite 連線：讀取也要排隊，否則並行請求會互相干擾
                with LOCK:
                    return self._route(method, path, headers, body)
            with LOCK, self.conn.transaction():
                return self._route(method, path, headers, body)
        except review.ReviewError as e:
            return e.status, serialize.problem(e.status, e.code, str(e)), {}
        except decide.DecisionError as e:
            return e.status, serialize.problem(e.status, e.code, str(e)), {}
        except (ValueError, TypeError, KeyError) as e:
            LOG.warning("422 %s %s：%s", method, path, e)
            return 422, serialize.problem(422, "VALIDATION_ERROR", f"欄位格式錯誤：{e}"), {}
        except Exception as e:  # 非預期錯誤：記錄完整追蹤，回應不洩漏內部細節
            LOG.error("500 %s %s\n%s", method, path, traceback.format_exc())
            return 500, serialize.problem(500, "INTERNAL_ERROR", f"伺服器內部錯誤（{type(e).__name__}），詳情見 data/logs/jev.log"), {}

    def _route(self, method, path, headers, body):
        if path in ("/", "/index.html") and method == "GET":
            return 200, UI_PAGE.replace("__JEV_LOCAL_KEY__", self.api_key), {"Content-Type": "text/html; charset=utf-8"}
        if path == "/tools" and method == "GET":
            return 200, tools_page(headers.get("host") or "127.0.0.1:8787"), {"Content-Type": "text/html; charset=utf-8"}
        if path == f"{BASE}/health" and method == "GET":
            return 200, self.health(), {}
        if headers.get("x-api-key") != self.api_key:
            return 401, serialize.problem(401, "UNAUTHORIZED", "缺少或錯誤的 API key"), {}
        c = self.conn
        routes = [
            ("GET", r"/community", lambda m: (200, self.community(), {})),
            ("GET", r"/community/overview", lambda m: (200, overview.build(c), {})),
            ("GET", r"/decisions/status", lambda m: (200, decide.status(c), {})),
            ("GET", r"/decisions/calls", lambda m: (200, _page([serialize.decision_call(r) for r in c.execute(
                "SELECT * FROM decision_call ORDER BY id DESC LIMIT 100")]), {})),
            ("GET", r"/listings/(\d+)/decisions", lambda m: self.listing_decisions(int(m.group(1)))),
            ("POST", r"/listings/(\d+)/decisions", lambda m: self.ask_listing(int(m.group(1)), body)),
            ("POST", r"/identity-matches/(\d+)/decisions", lambda m: self.ask_pair(int(m.group(1)), body)),
            ("GET", r"/listings", lambda m: (200, _page([serialize.listing(c, r) for r in c.execute("SELECT * FROM listing ORDER BY id")]), {})),
            ("POST", r"/listings", lambda m: self.create_listing(headers, body)),
            ("GET", r"/listings/(\d+)", lambda m: self.listing_detail(int(m.group(1)))),
            ("GET", r"/listings/(\d+)/candidates", lambda m: self.candidates(int(m.group(1)))),
            ("GET", r"/events", lambda m: (200, _page([event_to_api(r) for r in c.execute("SELECT * FROM listing_event ORDER BY id")]), {})),
            ("GET", r"/identity-matches", lambda m: (200, _page([serialize.identity_match(r) for r in c.execute("SELECT * FROM identity_match ORDER BY id")]), {})),
            ("GET", r"/review-tasks", lambda m: (200, _page([serialize.review_task(c, r) for r in c.execute("SELECT * FROM review_task WHERE status='OPEN' ORDER BY priority, id")]), {})),
            ("GET", r"/review-tasks/(\d+)", lambda m: self.task(int(m.group(1)))),
            ("POST", r"/review-tasks/(\d+)/decisions", lambda m: self.decide(int(m.group(1)), headers, body)),
            ("POST", r"/reviews/(\d+):retract", lambda m: self.write(headers, body, m, self.retract_review)),
            ("GET", r"/listings/(\d+)/snapshots", lambda m: self.rows("listing_snapshot", m, serialize.snapshot, page=True)),
            ("GET", r"/listings/(\d+)/evidence", lambda m: self.rows("evidence", m, lambda r: serialize.evidence(c, r))),
            ("GET", r"/listings/(\d+)/assessments", lambda m: self.rows("assessment", m, lambda r: serialize.assessment(c, r))),
            ("POST", r"/listings/(\d+)/snapshots", lambda m: self.write(headers, body, m, self.add_snapshot, idem_required=False)),
            ("POST", r"/listings/(\d+):mark-delisted", lambda m: self.write(headers, body, m, self.mark_delisted)),
            ("GET", r"/assessments/(\d+)", lambda m: self.one("assessment", m, lambda r: serialize.assessment(c, r))),
            ("POST", r"/evidence", lambda m: self.write(headers, body, m, self.create_evidence, idem_required=False)),
            ("GET", r"/evidence/(\d+)", lambda m: self.one("evidence", m, lambda r: serialize.evidence(c, r), etag=True)),
            ("POST", r"/evidence/(\d+):retract", lambda m: self.write(headers, body, m, self.retract_evidence)),
            ("POST", r"/origin-relations", lambda m: self.write(headers, body, m, self.create_relation)),
            ("POST", r"/origin-relations/(\d+):retract", lambda m: self.write(headers, body, m, self.retract_relation)),
            ("GET", r"/properties", lambda m: (200, _page([serialize.property_(c, r) for r in c.execute(
                "SELECT * FROM property WHERE id IN (SELECT property_id FROM listing) ORDER BY id")]), {})),
            ("GET", r"/properties/(\d+)", lambda m: self.one("property", m, lambda r: serialize.property_detail(c, r), etag=True)),
            ("POST", r"/properties:merge", lambda m: self.write(headers, body, m, self.merge)),
            ("POST", r"/properties/(\d+):split", lambda m: self.write(headers, body, m, self.split)),
            ("GET", r"/identity-matches/(\d+)", lambda m: self.one("identity_match", m, serialize.identity_match, etag=True)),
            ("GET", r"/merge-operations/(\d+)", lambda m: self.one("merge_operation", m, lambda r: serialize.merge_operation(c, r))),
            ("POST", r"/merge-operations/(\d+):revert", lambda m: self.write(headers, body, m, self.revert)),
            ("GET", r"/notifications", lambda m: (200, _page([serialize.notification(r) for r in c.execute("SELECT * FROM notification ORDER BY id")]), {})),
            ("GET", r"/alert-rules", lambda m: (200, [serialize.alert_rule(r) for r in c.execute(
                "SELECT * FROM alert_rule WHERE deleted_at IS NULL ORDER BY id")], {})),
            ("POST", r"/alert-rules", lambda m: self.write(headers, body, m, self.create_rule, idem_required=False)),
            ("PATCH", r"/alert-rules/(\d+)", lambda m: self.write(headers, body, m, self.patch_rule, idem_required=False)),
            ("DELETE", r"/alert-rules/(\d+)", lambda m: self.write(headers, body, m, self.delete_rule, idem_required=False)),
            ("GET", r"/properties:export", lambda m: (200, export_csv(c), {"Content-Type": "text/csv; charset=utf-8",
                                                                            "Content-Disposition": 'attachment; filename="jev-properties.csv"'})),
            ("GET", r"/registry/sales-units", lambda m: (200, _page([serialize.sales_unit(r) for r in c.execute(
                "SELECT * FROM sales_unit ORDER BY code")]), {})),
            ("GET", r"/registry/dwellings", lambda m: (200, _page([serialize.dwelling(r) for r in c.execute(
                "SELECT * FROM dwelling ORDER BY sales_unit_id, floor")]), {})),
            ("GET", r"/registry/address-units", lambda m: (200, _page([serialize.address_unit(r) for r in c.execute(
                "SELECT * FROM address_unit ORDER BY id")]), {})),
            ("POST", r"/addresses:normalize", lambda m: self.normalize(body)),
            ("GET", r"/registry/mappings", lambda m: (200, _page([serialize.registry_mapping(r) for r in c.execute(
                "SELECT * FROM registry_mapping ORDER BY id")]), {})),
            ("GET", r"/registry/derivation-rules", lambda m: (200, [serialize.derivation_rule(r) for r in c.execute(
                "SELECT * FROM address_derivation_rule ORDER BY id")], {})),
            ("GET", r"/registry/derivation-rules/(\d+)", lambda m: self.one("address_derivation_rule", m, serialize.derivation_rule, etag=True)),
            ("POST", r"/registry/derivation-rules/(\d+):retract", lambda m: self.write(headers, body, m, self.retract_rule)),
            ("GET", r"/sources", lambda m: (200, [serialize.source(r) for r in c.execute("SELECT * FROM source ORDER BY id")], {})),
        ]
        sub = path[len(BASE):] if path.startswith(BASE) else None
        for meth, pattern, fn in routes:
            mt = re.fullmatch(pattern, sub or "")
            if mt and meth == method:
                return fn(mt)
        if any(re.fullmatch(p, sub or "") for _, p, _ in routes):
            return 405, serialize.problem(405, "METHOD_NOT_ALLOWED", f"{method} {path} 尚未實作（見 docs/17 §3）"), {}
        return 404, serialize.problem(404, "NOT_FOUND", f"{method} {path} 不存在"), {}

    # ---- 決策模型（D-020）
    @staticmethod
    def _images(body):
        imgs = body.get("images") or []
        if not isinstance(imgs, list) or not all(isinstance(u, str) and u.startswith("data:image/") for u in imgs):
            raise review.ReviewError("VALIDATION_ERROR", "images 必須是 data:image/... 的 base64 字串陣列")
        if len(imgs) > 4 or sum(len(u) for u in imgs) > 16_000_000:
            raise review.ReviewError("VALIDATION_ERROR", "最多 4 張、合計約 12 MB 以內（請先縮小照片）")
        return imgs

    def _decision_problem(self, e):
        return e.status, serialize.problem(e.status, e.code, str(e)), {}

    def listing_decisions(self, lid):
        if not self.conn.execute("SELECT 1 FROM listing WHERE id=?", (lid,)).fetchone():
            return 404, serialize.problem(404, "NOT_FOUND", "找不到刊登"), {}
        evs = [serialize.evidence(self.conn, r) for r in self.conn.execute(
            "SELECT * FROM evidence WHERE listing_id=? AND kind='INFERENCE' ORDER BY id DESC", (lid,))]
        return 200, {"suggestions": decide.suggest(self.conn, lid), "answers": evs, "status": decide.status(self.conn)}, {}

    def ask_listing(self, lid, body):
        try:
            if not self.conn.execute("SELECT 1 FROM listing WHERE id=?", (lid,)).fetchone():
                raise decide.DecisionError("NOT_FOUND", "找不到刊登", 404)
            if body.get("consent") is not True:
                raise decide.DecisionError("CONSENT_REQUIRED", "送出前必須確認同意把資料傳給外部模型（consent: true）", 422)
            images = self._images(body)
            urls = body.get("photo_urls") or []
            if urls:
                if not decide.decision_settings().enabled:  # 未啟用時連照片都不下載
                    raise decide.DecisionError("DECISIONS_DISABLED", "決策模型未啟用", 409)
                images = images + decide.fetch_photos(urls)
            out = decide.ask_listing(self.conn, lid, body.get("purposes") or [], images)
        except decide.DecisionError as e:
            return self._decision_problem(e)
        return 201, out, {}

    def ask_pair(self, mid, body):
        try:
            if body.get("consent") is not True:
                raise decide.DecisionError("CONSENT_REQUIRED", "送出前必須確認同意把資料傳給外部模型（consent: true）", 422)
            out = decide.ask_pair(self.conn, mid, self._images(body))
        except decide.DecisionError as e:
            return self._decision_problem(e)
        return 201, out, {}

    def normalize(self, body):
        text = body.get("address_text")
        if not isinstance(text, str) or not text:
            return 422, serialize.problem(422, "VALIDATION_ERROR", "需要 address_text"), {}
        from .normalize import normalize_address
        return 200, normalize_address(text).to_dict(), {}

    def health(self):
        try:
            self.conn.execute("SELECT 1")
            db = "ok"
        except Exception:
            db = "error"
        sources = [{"code": r["code"], "auto_fetch_enabled": bool(r["auto_fetch_enabled"]), "last_run_status": r["status"],
                    "last_run_coverage": r["coverage"], "consecutive_failures": 0}
                   for r in self.conn.execute("SELECT s.code, s.auto_fetch_enabled, fr.status, fr.coverage FROM source s LEFT JOIN fetch_run fr"
                                              " ON fr.id=(SELECT MAX(id) FROM fetch_run WHERE source_id=s.id) ORDER BY s.id")]
        return {"status": "ok" if db == "ok" else "down", "db": db, "sources": sources}

    def community(self):
        r = self.conn.execute("SELECT * FROM community WHERE code='lianshang-world'").fetchone()
        rv = self.conn.execute("SELECT MAX(id) FROM registry_version").fetchone()[0]
        return {"id": r["id"], "code": r["code"], "name": r["name"], "city": r["city"], "district": r["district"],
                "area_name": r["area_name"], "aliases": json.loads(r["aliases"]), "current_registry_version_id": rv}

    def listing_detail(self, lid):
        row = self.conn.execute("SELECT * FROM listing WHERE id=?", (lid,)).fetchone()
        if not row:
            return 404, serialize.problem(404, "NOT_FOUND", "找不到刊登"), {}
        return 200, serialize.listing_detail(self.conn, row), {"ETag": f'"v{row["version"]}"'}

    def candidates(self, lid):
        a = self.conn.execute("SELECT id FROM assessment WHERE subject_type='ADDRESS_RESOLUTION' AND listing_id=? AND status='CURRENT'", (lid,)).fetchone()
        if not a:
            return 404, serialize.problem(404, "NOT_FOUND", "找不到評估"), {}
        return 200, serialize.candidates(self.conn, a["id"]), {}

    # ---- 通用讀取
    def one(self, table, m, fn, etag=False):
        row = self.conn.execute(f"SELECT * FROM {table} WHERE id=?", (int(m.group(1)),)).fetchone()
        if not row:
            return 404, serialize.problem(404, "NOT_FOUND", f"找不到 {table} {m.group(1)}"), {}
        return 200, fn(row), ({"ETag": f'"v{row["version"]}"'} if etag else {})

    def rows(self, table, m, fn, page=False):
        lid = int(m.group(1))
        if not self.conn.execute("SELECT 1 FROM listing WHERE id=?", (lid,)).fetchone():
            return 404, serialize.problem(404, "NOT_FOUND", "找不到刊登"), {}
        items = [fn(r) for r in self.conn.execute(f"SELECT * FROM {table} WHERE listing_id=? ORDER BY id", (lid,))]
        return 200, (_page(items) if page else items), {}

    # ---- 通用寫入：冪等、If-Match、錯誤對應（08 §4–§5）
    def write(self, headers, body, m, fn, idem_required=True):
        route = m.string
        token, early = self._idem(headers, route, body, required=idem_required)
        if early:
            return early
        mt = re.fullmatch(r'"v(\d+)"', headers.get("if-match", ""))
        try:
            status, out = fn(int(m.group(1)) if m.groups() else None, body, int(mt.group(1)) if mt else None)
        except review.ReviewError as e:
            self._abort()
            return e.status, serialize.problem(e.status, e.code, str(e)), {}
        except LookupError as e:
            self._abort()
            return 404, serialize.problem(404, "NOT_FOUND", str(e)), {}
        except ValueError as e:
            self._abort()
            msg = str(e)
            if msg.startswith("DEPENDENT_OPERATIONS"):
                return 409, serialize.problem(409, "DEPENDENT_OPERATIONS", msg), {}
            if msg == "ALREADY_DONE":
                return 409, serialize.problem(409, "ALREADY_DONE", "操作已經執行過"), {}
            if msg.startswith("SPLIT_GROUPS_MISMATCH"):
                return 422, serialize.problem(422, "SPLIT_GROUPS_MISMATCH", msg), {}
            return 422, serialize.problem(422, "VALIDATION_ERROR", msg), {}
        self.conn.commit()
        self._notify()
        self._remember(token, route, status, out)
        return status, (out if out is not None else ""), {}

    def _abort(self):
        """寫入失敗：還原這個交易已做的變更（之後回傳錯誤回應）。"""
        self.conn.rollback()

    def _check_version(self, table, rid, expected):
        row = self.conn.execute(f"SELECT version FROM {table} WHERE id=?", (rid,)).fetchone()
        if not row:
            raise LookupError(f"找不到 {table} {rid}")
        if expected is None:
            raise review.ReviewError("PRECONDITION_REQUIRED", "缺少 If-Match", 428)
        if expected != row["version"]:
            raise review.ReviewError("STALE_VERSION", f'目前版本為 "v{row["version"]}"', 412)

    @staticmethod
    def _rationale(body):
        r = body.get("rationale")
        if not isinstance(r, str) or len(r) < 5:
            raise review.ReviewError("VALIDATION_ERROR", "rationale 至少 5 個字")
        return r

    def _reassess(self, listing_ids, trigger):
        """重新評估並回傳 ReassessResult。"""
        c = self.conn
        last_event = c.execute("SELECT COALESCE(MAX(id), 0) FROM listing_event").fetchone()[0]
        out = {"new_assessment_ids": [], "superseded_assessment_ids": [], "changed_listing_ids": [], "event_ids": []}
        for lid in sorted(set(listing_ids)):
            before = {r["id"] for r in c.execute("SELECT id FROM assessment WHERE listing_id=? AND status='CURRENT'", (lid,))}
            assess_listing(c, lid, trigger=trigger)
            after = {r["id"] for r in c.execute("SELECT id FROM assessment WHERE listing_id=? AND status='CURRENT'", (lid,))}
            out["new_assessment_ids"] += sorted(after - before)
            out["superseded_assessment_ids"] += sorted(before - after)
            if after != before:
                out["changed_listing_ids"].append(lid)
        out["event_ids"] = [r[0] for r in c.execute("SELECT event_id FROM listing_event WHERE id>? ORDER BY id", (last_event,))]
        return out

    # ---- 寫入操作
    def create_evidence(self, _, body, __):
        lid = body.get("listing_id")
        if lid is not None and not self.conn.execute("SELECT 1 FROM listing WHERE id=?", (lid,)).fetchone():
            raise LookupError("找不到刊登")
        if not body.get("links"):
            raise review.ReviewError("VALIDATION_ERROR", "links 至少一筆")
        if len(body.get("description") or "") < 5:
            raise review.ReviewError("VALIDATION_ERROR", "description 至少 5 個字")
        eid = review.add_user_evidence(self.conn, lid, body)
        if lid is not None:
            self._reassess([lid], "EVIDENCE_CHANGED")
        return 201, serialize.evidence(self.conn, self.conn.execute("SELECT * FROM evidence WHERE id=?", (eid,)).fetchone())

    def retract_evidence(self, eid, body, expected):
        self._check_version("evidence", eid, expected)
        self._rationale(body)
        if self.conn.execute("SELECT status FROM evidence WHERE id=?", (eid,)).fetchone()["status"] == "RETRACTED":
            raise ValueError("ALREADY_DONE")
        ids = review.retract_evidence_cascade(self.conn, [eid])
        lids = [r[0] for r in self.conn.execute(
            f"SELECT DISTINCT listing_id FROM evidence WHERE id IN ({','.join('?' * len(ids))}) AND listing_id IS NOT NULL", ids)]
        return 200, self._reassess(lids, "EVIDENCE_RETRACTED")

    def retract_review(self, rid, body, _):
        return 200, review.retract_review(self.conn, rid, self._rationale(body))

    def add_snapshot(self, lid, body, _):
        """既有刊登的新觀測（08；人工記錄與書籤匯入共用）：內容沒變回 200，有變回 201 並產生事件。"""
        lst = self.conn.execute("SELECT l.*, s.code FROM listing l JOIN source s ON s.id=l.source_id WHERE l.id=?", (lid,)).fetchone()
        if not lst:
            raise LookupError("找不到刊登")
        unknown = set(body) - set(lifecycle.SNAPSHOT_FIELDS)
        if unknown:
            raise review.ReviewError("VALIDATION_ERROR", f"不接受的欄位：{', '.join(sorted(unknown))}")
        if not isinstance(body.get("total_price"), int) or body["total_price"] < 1:
            raise review.ReviewError("VALIDATION_ERROR", "需要 total_price（≥ 1 的整數）")
        was_delisted = lst["status"] == "DELISTED"
        res = lifecycle.record(self.conn, {**body, "source_code": lst["code"], "external_id": lst["external_id"]}, "INCREMENTAL")
        if was_delisted:
            lifecycle.relist_if_needed(self.conn, lid)
        if res["changed"]:
            assess_listing(self.conn, lid)
            identity.evaluate_pairs(self.conn)
        snap = self.conn.execute("SELECT * FROM listing_snapshot WHERE id=?", (res["snapshot_id"],)).fetchone()
        return (201 if res["changed"] else 200), serialize.snapshot(snap)

    def mark_delisted(self, lid, body, expected):
        self._check_version("listing", lid, expected)
        if body.get("reason") not in ("MARKED_SOLD", "MARKED_REMOVED", "MANUAL"):
            raise review.ReviewError("VALIDATION_ERROR", "reason 必須是 MARKED_SOLD、MARKED_REMOVED 或 MANUAL")
        lifecycle.mark_delisted(self.conn, lid, body["reason"], self._rationale(body))
        return 200, serialize.listing_detail(self.conn, self.conn.execute("SELECT * FROM listing WHERE id=?", (lid,)).fetchone())

    def create_relation(self, _, body, __):
        rid = identity.add_origin_relation(self.conn, body.get("origin_a_id"), body.get("origin_b_id"), body.get("relation"),
                                           self._rationale(body), body.get("evidence_ids"))
        return 201, serialize.origin_relation(self.conn.execute("SELECT * FROM origin_relation WHERE id=?", (rid,)).fetchone())

    def retract_relation(self, rid, body, _):
        self._rationale(body)
        identity.retract_origin_relation(self.conn, rid)
        return 200, serialize.origin_relation(self.conn.execute("SELECT * FROM origin_relation WHERE id=?", (rid,)).fetchone())

    def retract_rule(self, rid, body, expected):
        from . import registry
        self._check_version("address_derivation_rule", rid, expected)
        listings = [r[0] for r in self.conn.execute("SELECT id FROM listing")]
        before = {lid: {r["id"] for r in self.conn.execute("SELECT id FROM assessment WHERE listing_id=? AND status='CURRENT'", (lid,))}
                  for lid in listings}
        last_event = self.conn.execute("SELECT COALESCE(MAX(id), 0) FROM listing_event").fetchone()[0]
        registry.set_status(self.conn, "DERIVATION_RULE", rid, "RETRACTED", self._rationale(body), [])
        out = {"new_assessment_ids": [], "superseded_assessment_ids": [], "changed_listing_ids": [], "event_ids": []}
        for lid in listings:
            after = {r["id"] for r in self.conn.execute("SELECT id FROM assessment WHERE listing_id=? AND status='CURRENT'", (lid,))}
            out["new_assessment_ids"] += sorted(after - before[lid])
            out["superseded_assessment_ids"] += sorted(before[lid] - after)
            out["changed_listing_ids"].append(lid)
        out["event_ids"] = [r[0] for r in self.conn.execute("SELECT event_id FROM listing_event WHERE id>? ORDER BY id", (last_event,))]
        return 200, out

    RULE_FIELDS = {"name": str, "enabled": bool, "event_types": list, "max_total_price": int, "min_price_drop_pct": (int, float),
                   "min_rooms": int, "min_building_ping": (int, float), "membership_statuses": list, "channel": str, "notify_corrections": bool}
    NOT_NULL = {"name", "enabled", "event_types", "channel", "notify_corrections", "membership_statuses"}

    def _rule_values(self, body, partial):
        from .events import INTERNAL, NOTIFIABLE
        unknown = set(body) - set(self.RULE_FIELDS)
        if unknown:
            raise review.ReviewError("VALIDATION_ERROR", f"不接受的欄位：{', '.join(sorted(unknown))}")
        if partial and not body:
            raise review.ReviewError("VALIDATION_ERROR", "至少要改一個欄位")
        for k, v in body.items():
            if v is None:
                if k in self.NOT_NULL:
                    raise review.ReviewError("VALIDATION_ERROR", f"{k} 不可為 null")
                continue
            if isinstance(v, bool) and self.RULE_FIELDS[k] is not bool or not isinstance(v, self.RULE_FIELDS[k]):
                raise review.ReviewError("VALIDATION_ERROR", f"{k} 型別錯誤")
        if "channel" in body and body["channel"] not in ("EMAIL", "TELEGRAM"):
            raise review.ReviewError("VALIDATION_ERROR", "channel 只能是 EMAIL 或 TELEGRAM")
        if "event_types" in body:
            bad = [t for t in body["event_types"] if t not in NOTIFIABLE]
            if not body["event_types"] or bad:
                raise review.ReviewError("VALIDATION_ERROR", f"event_types 至少一個，且只能是可通知的事件（不接受：{', '.join(bad) or '空陣列'}）")
        if "name" in body and not body["name"].strip():
            raise review.ReviewError("VALIDATION_ERROR", "name 不可空白")
        cols = {k: (j(v) if isinstance(v, list) else (int(v) if isinstance(v, bool) else v)) for k, v in body.items()}
        return cols

    def create_rule(self, _, body, __):
        for k in ("name", "event_types", "channel"):
            if k not in body:
                raise review.ReviewError("VALIDATION_ERROR", f"缺少 {k}")
        cols = self._rule_values(body, partial=False)
        rid = self.conn.execute(f"INSERT INTO alert_rule ({', '.join(cols)}) VALUES ({', '.join('?' * len(cols))})", list(cols.values())).lastrowid
        return 201, serialize.alert_rule(self.conn.execute("SELECT * FROM alert_rule WHERE id=?", (rid,)).fetchone())

    def patch_rule(self, rid, body, expected):
        self._check_version("alert_rule", rid, expected)
        cols = self._rule_values(body, partial=True)
        self.conn.execute(f"UPDATE alert_rule SET {', '.join(f'{k}=?' for k in cols)}, version=version+1 WHERE id=? AND deleted_at IS NULL",
                          [*cols.values(), rid])
        return 200, serialize.alert_rule(self.conn.execute("SELECT * FROM alert_rule WHERE id=?", (rid,)).fetchone())

    def delete_rule(self, rid, body, expected):
        self._check_version("alert_rule", rid, expected)
        from .db import now
        self.conn.execute("UPDATE alert_rule SET deleted_at=?, enabled=0, version=version+1 WHERE id=?", (now(), rid))
        return 204, None

    def _op(self, op):
        return serialize.merge_operation(self.conn, self.conn.execute("SELECT * FROM merge_operation WHERE id=?", (op,)).fetchone())

    def merge(self, _, body, __):
        pids = body.get("property_ids") or []
        if len(set(pids)) < 2 or len(set(pids)) != len(pids):
            raise review.ReviewError("VALIDATION_ERROR", "property_ids 至少兩個且不重複")
        for pid in pids:
            row = self.conn.execute("SELECT version FROM property WHERE id=?", (pid,)).fetchone()
            if not row:
                raise LookupError(f"找不到 property {pid}")
            want = (body.get("expected_versions") or {}).get(str(pid))
            if want is not None and want != row["version"]:
                raise review.ReviewError("STALE_VERSION", f"property {pid} 目前版本為 v{row['version']}", 412)
        rationale = self._rationale(body)
        ev = list(body.get("evidence_ids") or [])
        ev += [review.add_user_evidence(self.conn, None, item) for item in body.get("new_evidence") or []]
        if not ev:
            raise review.ReviewError("VALIDATION_ERROR", "人工合併必須附佐證（evidence_ids 或 new_evidence）")
        return 201, self._op(identity.merge(self.conn, pids, rationale, ev))

    def split(self, pid, body, expected):
        self._check_version("property", pid, expected)
        return 201, self._op(identity.split(self.conn, pid, body.get("groups") or [], self._rationale(body), body.get("evidence_ids")))

    def revert(self, opid, body, _):
        if not self.conn.execute("SELECT 1 FROM merge_operation WHERE id=?", (opid,)).fetchone():
            raise LookupError("找不到合併操作")
        return 201, self._op(identity.revert(self.conn, opid, self._rationale(body)))

    def task(self, tid):
        row = self.conn.execute("SELECT * FROM review_task WHERE id=?", (tid,)).fetchone()
        if not row:
            return 404, serialize.problem(404, "NOT_FOUND", "找不到覆核任務"), {}
        return 200, serialize.review_task(self.conn, row), {"ETag": f'"v{row["version"]}"'}

    # ---- 冪等（08 §4）
    def _idem(self, headers, route, body, required):
        key = headers.get("idempotency-key")
        if not key:
            if required:
                return None, (400, serialize.problem(400, "IDEMPOTENCY_KEY_REQUIRED", "這個操作必須帶 Idempotency-Key"), {})
            return None, None
        h = hashlib.sha256(json.dumps(body, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
        row = self.conn.execute("SELECT * FROM idempotency_record WHERE api_key_id='local' AND method='POST' AND route=? AND key=?",
                                (route, key)).fetchone()
        if row and row["expires_at"] > now():
            if row["request_hash"] != h:
                return None, (422, serialize.problem(422, "IDEMPOTENCY_KEY_REUSED", "同一個 key 已用於不同內容"), {})
            return None, (row["response_status"], json.loads(row["response_body"]), {"Idempotent-Replayed": "true"})
        return (key, h), None

    def _remember(self, token, route, status, body):
        if not token:
            return
        key, h = token
        exp = (datetime.now(timezone.utc) + timedelta(hours=24)).replace(microsecond=0).isoformat().replace("+00:00", "Z")
        self.conn.execute("INSERT OR REPLACE INTO idempotency_record VALUES ('local','POST',?,?,?,?,?,?,?)",
                          (route, key, h, status, j(body), now(), exp))
        self.conn.commit()

    def create_listing(self, headers, body):
        token, early = self._idem(headers, "/listings", body, required=False)
        if early:
            return early
        snap = body.get("snapshot") or {}
        code = body.get("source_code")
        if not code or "total_price" not in snap or not isinstance(snap.get("total_price"), int) or snap["total_price"] < 1:
            return 422, serialize.problem(422, "VALIDATION_ERROR", "需要 source_code 與 snapshot.total_price（≥ 1）"), {}
        if code != "SRC-MANUAL" and not (body.get("external_id") or body.get("url")):
            return 422, serialize.problem(422, "VALIDATION_ERROR", "平台來源必須有 external_id 或 url",
                                          errors=[{"field": "external_id", "message": "external_id 或 url 擇一必填"}]), {}
        if code == "SRC-MANUAL" and not body.get("evidence_origin"):
            return 422, serialize.problem(422, "VALIDATION_ERROR", "SRC-MANUAL 必須附 evidence_origin"), {}
        try:
            sid = lifecycle.source_id(self.conn, code)
        except ValueError as e:
            return 422, serialize.problem(422, "VALIDATION_ERROR", str(e)), {}
        if body.get("external_id") and self.conn.execute("SELECT 1 FROM listing WHERE source_id=? AND external_id=?",
                                                         (sid, body["external_id"])).fetchone():
            lid = self.conn.execute("SELECT id FROM listing WHERE source_id=? AND external_id=?", (sid, body["external_id"])).fetchone()[0]
            return 409, serialize.problem(409, "LISTING_EXISTS", "刊登已存在", existing_listing_id=lid), {}
        rec = {**snap, "source_code": code, "external_id": body.get("external_id"), "url": body.get("url"),
               "lister_type": body.get("lister_type"), "lister_company": body.get("lister_company"),
               "is_new_listing": body.get("is_new_listing")}
        res = lifecycle.record(self.conn, rec, body.get("import_mode", "INCREMENTAL"))
        assess_listing(self.conn, res["listing_id"])
        identity.evaluate_pairs(self.conn)
        row = self.conn.execute("SELECT * FROM listing WHERE id=?", (res["listing_id"],)).fetchone()
        out = serialize.listing_detail(self.conn, row)
        self._notify()
        self._remember(token, "/listings", 201, out)
        return 201, out, {"ETag": f'"v{row["version"]}"'}

    def decide(self, tid, headers, body):
        token, early = self._idem(headers, f"/review-tasks/{tid}/decisions", body, required=True)
        if early:
            return early
        m = re.fullmatch(r'"v(\d+)"', headers.get("if-match", ""))
        try:
            out = review.submit_decision(self.conn, tid, int(m.group(1)) if m else None, body.get("decision"), body.get("rationale"),
                                         body.get("new_evidence"), body.get("evidence_ids"), body.get("candidate_id"),
                                         body.get("address_unit_id"))
        except review.ReviewError as e:
            extra = {}
            if e.code == "STALE_VERSION":
                v = self.conn.execute("SELECT version FROM review_task WHERE id=?", (tid,)).fetchone()[0]
                extra["current_etag"] = f'"v{v}"'
            return e.status, serialize.problem(e.status, e.code, str(e), **extra), {}
        self._notify()
        self._remember(token, f"/review-tasks/{tid}/decisions", 201, out)
        return 201, out, {}


def export_csv(conn):
    """同屋群組（property）匯出：UTF-8 BOM，只含房屋欄位與判斷結果（不含照片、標題與仲介資料）。"""
    import csv as _csv
    import io
    rows = conn.execute(
        "SELECT p.id, p.status, p.current_min_price, p.active_listing_count, p.first_seen_at,"
        " GROUP_CONCAT(s.code || ' ' || l.external_id, '；') AS listings FROM property p JOIN listing l ON l.property_id=p.id"
        " JOIN source s ON s.id=l.source_id GROUP BY p.id ORDER BY p.id").fetchall()
    buf = io.StringIO()
    w = _csv.writer(buf)
    w.writerow(["property_id", "狀態", "上架刊登數", "最低價", "首次出現", "刊登", "坪數", "樓層", "戶別", "門牌狀態", "門牌候選"])
    for r in rows:
        lid = conn.execute("SELECT id FROM listing WHERE property_id=? ORDER BY id LIMIT 1", (r["id"],)).fetchone()[0]
        sn = conn.execute("SELECT building_ping, floor_text FROM listing_snapshot WHERE listing_id=? ORDER BY id DESC LIMIT 1", (lid,)).fetchone()
        a = conn.execute("SELECT id, outcome, resolved_fields, candidate_count FROM assessment WHERE listing_id=? AND subject_type='ADDRESS_RESOLUTION'"
                         " AND status='CURRENT'", (lid,)).fetchone()
        f = unj(a["resolved_fields"], {}) if a else {}
        su = f.get("sales_unit", {})
        units = su.get("value") or "／".join(map(str, su.get("candidates", [])))
        tops = "" if not a or (a["candidate_count"] or 0) > 4 else "、".join(
            f"{t['number']}號{t['floor']}樓" for t in conn.execute(
                "SELECT au.number, d.floor FROM assessment_candidate ac JOIN candidate c ON c.id=ac.candidate_id JOIN dwelling d ON d.id=c.dwelling_id"
                " JOIN registry_mapping rm ON rm.from_id=d.sales_unit_id AND rm.mapping_type='SALES_UNIT_TO_NUMBER' JOIN address_unit au ON au.id=rm.to_id"
                " WHERE ac.assessment_id=? AND ac.excluded=0 ORDER BY ac.rule_score DESC", (a["id"],)))
        w.writerow([r["id"], r["status"], r["active_listing_count"], r["current_min_price"], r["first_seen_at"], r["listings"],
                    sn["building_ping"] if sn else "", (sn["floor_text"] or "") if sn else "", units, a["outcome"] if a else "", tops])
    return "\ufeff" + buf.getvalue()


def bookmarklet(host):
    """把 parse591.js 包成書籤：只讀目前分頁畫面上的內容，打開本機 JEV 的匯入表單（不會自動寫入）。"""
    import urllib.parse
    src = (Path(__file__).parent / "ui" / "parse591.js").read_text(encoding="utf-8")
    lines = []
    for line in src.splitlines():
        if line.strip().startswith("//") or line.startswith("if (typeof module"):
            continue
        lines.append(re.sub(r"\s+// .*$", "", line))
    collect = ("const ld=[...document.querySelectorAll('script[type=\"application/ld+json\"]')].map(s=>{try{return JSON.parse(s.textContent)}catch(e){return {}}});"
               "const links=[...document.querySelectorAll('a[href*=\"market.591.com.tw\"]')].map(a=>({href:a.href,text:a.textContent,"
               "recommended:!!a.closest('[class*=\"hot\"],[class*=\"recommend\"],[class*=\"similar\"],[class*=\"like\"]')}));"
               "const r=parse591({href:location.href,title:document.title,text:document.body.innerText,ld,links});"
               f"window.open('http://{host}/#import='+encodeURIComponent(JSON.stringify(r)),'jev');")
    code = "(()=>{\n" + "\n".join(lines) + "\n" + collect + "})();"
    return "javascript:" + urllib.parse.quote(code, safe="(){}[];,=+-*/!?:'.<>&|$`_~@^")


def tools_page(host):
    import html as _html
    link = _html.escape(bookmarklet(host), quote=True)
    return f"""<!doctype html><html lang="zh-Hant"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>JEV 匯入工具</title><style>body{{font-family:"Noto Sans TC","Microsoft JhengHei",system-ui,sans-serif;max-width:760px;margin:0 auto;padding:24px 16px;line-height:1.7;background:#f6f5f1;color:#1d2321}}
a.bm{{display:inline-block;padding:10px 18px;border-radius:999px;background:#1f6f5c;color:#fff;text-decoration:none;font-weight:600}}
li{{margin:6px 0}} .note{{color:#66706c;font-size:13px}} code{{background:#ebe9e1;padding:1px 5px;border-radius:4px}}</style></head><body>
<h1>591 → JEV 一鍵匯入</h1>
<p><a class="bm" href="{link}">📥 匯入 JEV</a></p>
<ol>
<li>把上面的綠色按鈕<b>拖曳到瀏覽器的書籤列</b>（看不到書籤列：按 <code>Ctrl+Shift+B</code>）。</li>
<li>用瀏覽器正常打開一則 591 的聯上世界刊登頁，等頁面載入完成。</li>
<li>按書籤列上的「📥 匯入 JEV」，會打開 JEV 的匯入表單，欄位已經預填。</li>
<li><b>核對後才按「寫入 JEV」</b>；已存在的刊登會記成新的觀測（例如降價）。</li>
</ol>
<p class="note">只讀你畫面上已經顯示的文字（價格、坪數、樓層、格局、社區），一次一頁；不讀照片、不存原始頁面，也不擷取仲介姓名或電話。資料只送到你本機的 JEV（{_html.escape(host)}），不會送到其他地方。JEV 伺服器要先開著（<code>python -m jev serve</code>）。</p>
<p class="note">591 改版後如果有欄位讀不到，表單會提示；直接手動補上即可。</p>
<p><a href="/">← 回到 JEV</a></p></body></html>"""


def make_handler(app):
    class Handler(BaseHTTPRequestHandler):
        def _go(self, method):
            n = int(self.headers.get("Content-Length") or 0)
            try:
                body = json.loads(self.rfile.read(n) or b"{}") if n else {}
            except (json.JSONDecodeError, UnicodeDecodeError):
                return self._send(400, serialize.problem(400, "VALIDATION_ERROR", "請求內容必須是 UTF-8 編碼的 JSON"), {})
            headers = {k.lower(): v for k, v in self.headers.items()}
            t0 = time.perf_counter()
            status, payload, extra = app.handle(method, self.path.split("?")[0], headers, body)
            self._send(status, payload, extra)
            LOG.info("%s %s %s %.0fms", method, self.path.split("?")[0], status, (time.perf_counter() - t0) * 1000)

        def _send(self, status, payload, extra):
            extra = dict(extra)
            if isinstance(payload, str):
                data, ctype_header = payload.encode("utf-8"), extra.pop("Content-Type", "text/plain; charset=utf-8")
            else:
                data = json.dumps(payload, ensure_ascii=False).encode()
                ctype = "application/problem+json" if isinstance(payload, dict) and "code" in payload and status >= 400 else "application/json"
                ctype_header = f"{ctype}; charset=utf-8"
            self.send_response(status)
            self.send_header("Content-Type", ctype_header)
            self.send_header("Content-Length", str(len(data)))
            for k, v in extra.items():
                self.send_header(k, v)
            self.end_headers()
            self.wfile.write(data)

        def do_GET(self):
            self._go("GET")

        def do_POST(self):
            self._go("POST")

        def do_PATCH(self):
            self._go("PATCH")

        def do_DELETE(self):
            self._go("DELETE")

        def log_message(self, *args):
            pass
    return Handler


def local_api_key(data_dir):
    """沒有設定 JEV_API_KEY 時：第一次啟動產生隨機金鑰，存在 data/ 底下（已被 git 排除）。"""
    f = Path(data_dir) / "api_key.txt"
    if f.exists() and f.read_text(encoding="utf-8").strip():
        return f.read_text(encoding="utf-8").strip()
    f.parent.mkdir(parents=True, exist_ok=True)
    key = secrets.token_urlsafe(24)
    f.write_text(key, encoding="utf-8")
    return key


def setup_logging(data_dir):
    log_dir = Path(data_dir) / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    from logging.handlers import RotatingFileHandler
    h = RotatingFileHandler(log_dir / "jev.log", maxBytes=2_000_000, backupCount=5, encoding="utf-8")
    h.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s %(message)s"))
    root = logging.getLogger("jev")
    root.setLevel(logging.INFO)
    if not any(isinstance(x, RotatingFileHandler) for x in root.handlers):
        root.addHandler(h)


def serve(db_path, port=8787):
    from .db import backup
    data_dir = Path(str(db_path)).parent
    setup_logging(data_dir)
    app = App(db_path)
    for change in getattr(app.conn, "migration_log", []):
        print("資料庫升級：" + change)
    print(f"啟動前備份：{backup(app.conn, db_path, reason='serve')}")
    httpd = ThreadingHTTPServer(("127.0.0.1", port), make_handler(app))
    LOG.info("serve %s on 127.0.0.1:%s", db_path, port)
    print(f"JEV {__version__}：介面 http://127.0.0.1:{port}/ ；API http://127.0.0.1:{port}{BASE}"
          f"（API key：{'環境變數 JEV_API_KEY' if os.environ.get('JEV_API_KEY') else data_dir / 'api_key.txt'}；日誌 {data_dir / 'logs' / 'jev.log'}）")
    httpd.serve_forever()
