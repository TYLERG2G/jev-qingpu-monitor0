"""SQLite 資料層。表名與欄位依 docs/05-data-model.md（MVP 子集）。"""

import json
import re
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

# 2：merge_operation／registry 的 evidence_ids、review_task 的名冊欄位、evidence_link（v0.6 期間新增）
SCHEMA_VERSION = 5  # 3：lvr_transaction（官方實價登錄成交）；4：decision_call（決策模型呼叫紀錄）；5：alert_rule.deleted_at

DDL = """
CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT);

CREATE TABLE IF NOT EXISTS community (
  id INTEGER PRIMARY KEY, code TEXT UNIQUE NOT NULL, name TEXT NOT NULL, city TEXT, district TEXT,
  area_name TEXT, aliases TEXT NOT NULL DEFAULT '[]'
);
CREATE TABLE IF NOT EXISTS registry_version (
  id INTEGER PRIMARY KEY, label TEXT NOT NULL, parent_id INTEGER, created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS unit_type (
  id INTEGER PRIMARY KEY, community_id INTEGER NOT NULL, code TEXT NOT NULL,
  area_ping_min REAL, area_ping_max REAL, parking_basis TEXT NOT NULL DEFAULT 'EXCLUDED',
  rooms INTEGER, halls INTEGER, baths INTEGER, layout_fingerprint TEXT, record_status TEXT NOT NULL,
  UNIQUE (community_id, code)
);
CREATE TABLE IF NOT EXISTS sales_unit (
  id INTEGER PRIMARY KEY, community_id INTEGER NOT NULL, building_label TEXT, unit_type_id INTEGER,
  code TEXT NOT NULL, block_position TEXT, facades TEXT NOT NULL DEFAULT '[]', record_status TEXT NOT NULL,
  UNIQUE (community_id, code)
);
CREATE TABLE IF NOT EXISTS address_unit (
  id INTEGER PRIMARY KEY, community_id INTEGER NOT NULL, addr_key TEXT NOT NULL, raw_address TEXT NOT NULL,
  road TEXT, section INTEGER, number INTEGER, number_sub INTEGER, floor TEXT, precision TEXT NOT NULL,
  origin TEXT NOT NULL, record_status TEXT NOT NULL, UNIQUE (community_id, addr_key)
);
CREATE TABLE IF NOT EXISTS registry_mapping (
  id INTEGER PRIMARY KEY, registry_version_id INTEGER NOT NULL, mapping_type TEXT NOT NULL,
  from_type TEXT NOT NULL, from_id INTEGER NOT NULL, to_type TEXT NOT NULL, to_id INTEGER NOT NULL,
  record_status TEXT NOT NULL, version INTEGER NOT NULL DEFAULT 1, evidence_ids TEXT NOT NULL DEFAULT '[]'
);
CREATE TABLE IF NOT EXISTS address_derivation_rule (
  id INTEGER PRIMARY KEY, community_id INTEGER NOT NULL, registry_version_id INTEGER NOT NULL, code TEXT UNIQUE,
  scope_type TEXT NOT NULL, scope_ids TEXT NOT NULL DEFAULT '[]', floor_min INTEGER NOT NULL, floor_max INTEGER NOT NULL,
  floors TEXT, rule_type TEXT NOT NULL, floor_label_format TEXT, exceptions TEXT NOT NULL DEFAULT '[]',
  record_status TEXT NOT NULL, supersedes_rule_id INTEGER, version INTEGER NOT NULL DEFAULT 1, created_at TEXT NOT NULL,
  evidence_ids TEXT NOT NULL DEFAULT '[]', CHECK (floor_min <= floor_max)
);
CREATE TABLE IF NOT EXISTS dwelling (
  id INTEGER PRIMARY KEY, sales_unit_id INTEGER NOT NULL, floor INTEGER NOT NULL, record_status TEXT NOT NULL,
  UNIQUE (sales_unit_id, floor)
);
CREATE TABLE IF NOT EXISTS source (
  id INTEGER PRIMARY KEY, code TEXT UNIQUE NOT NULL, name TEXT NOT NULL, priority TEXT NOT NULL,
  access_method TEXT NOT NULL, rights TEXT NOT NULL, auto_fetch_enabled INTEGER NOT NULL DEFAULT 0,
  min_interval_sec INTEGER NOT NULL DEFAULT 10, canonical_scope_fingerprint TEXT, baseline_completed_at TEXT,
  compliance TEXT NOT NULL DEFAULT '{}', area_includes_parking INTEGER, version INTEGER NOT NULL DEFAULT 1
);
CREATE TABLE IF NOT EXISTS fetch_run (
  id INTEGER PRIMARY KEY, source_id INTEGER NOT NULL, run_type TEXT NOT NULL, status TEXT NOT NULL,
  coverage TEXT NOT NULL, coverage_reasons TEXT NOT NULL DEFAULT '[]', import_mode TEXT,
  query_fingerprint TEXT, pages_fetched INTEGER NOT NULL DEFAULT 0, page_cap_hit INTEGER NOT NULL DEFAULT 0,
  pagination_exhausted INTEGER NOT NULL DEFAULT 0, reported_total INTEGER, enumerated_count INTEGER NOT NULL DEFAULT 0,
  list_parse_failures INTEGER NOT NULL DEFAULT 0, detail_failures INTEGER NOT NULL DEFAULT 0,
  started_at TEXT, finished_at TEXT, error_summary TEXT
);
CREATE TABLE IF NOT EXISTS listing (
  id INTEGER PRIMARY KEY, source_id INTEGER NOT NULL, external_id TEXT NOT NULL, external_id_origin TEXT NOT NULL,
  url TEXT, deal_type TEXT NOT NULL DEFAULT 'SALE', status TEXT NOT NULL DEFAULT 'ACTIVE',
  current_cycle_no INTEGER NOT NULL DEFAULT 1, consecutive_missing_complete_runs INTEGER NOT NULL DEFAULT 0,
  first_observed_at TEXT NOT NULL, first_observed_context TEXT NOT NULL, new_listing_status TEXT NOT NULL,
  last_seen_at TEXT NOT NULL, lister_type TEXT NOT NULL DEFAULT 'UNKNOWN', lister_company TEXT,
  property_id INTEGER, version INTEGER NOT NULL DEFAULT 1, UNIQUE (source_id, external_id)
);
CREATE TABLE IF NOT EXISTS listing_cycle (
  id INTEGER PRIMARY KEY, listing_id INTEGER NOT NULL, cycle_no INTEGER NOT NULL, started_at TEXT NOT NULL,
  start_reason TEXT NOT NULL, ended_at TEXT, end_reason TEXT, UNIQUE (listing_id, cycle_no)
);
CREATE TABLE IF NOT EXISTS listing_snapshot (
  id INTEGER PRIMARY KEY, listing_id INTEGER NOT NULL, fetch_run_id INTEGER, source_document_id INTEGER,
  observed_at TEXT NOT NULL, content_hash TEXT NOT NULL, total_price INTEGER NOT NULL, parking_price INTEGER,
  parking_included TEXT NOT NULL DEFAULT 'UNKNOWN', building_ping REAL, main_ping REAL, parking_ping REAL,
  floor_text TEXT, floor_min INTEGER, floor_max INTEGER, floor_exact INTEGER NOT NULL DEFAULT 0, total_floors INTEGER,
  rooms INTEGER, halls INTEGER, baths INTEGER, orientation TEXT, age_years REAL, address_text TEXT,
  address_precision TEXT NOT NULL DEFAULT 'NONE', community_text TEXT, platform_community_ref TEXT,
  sales_unit_text TEXT, units_per_floor INTEGER, map_address TEXT, title TEXT, posted_at TEXT, parser_version TEXT
);
CREATE TABLE IF NOT EXISTS source_origin (
  id INTEGER PRIMARY KEY, origin_type TEXT NOT NULL, asserting_party_type TEXT NOT NULL, asserting_party_key TEXT,
  captured_by TEXT, captured_at TEXT, root_content_hash TEXT, description TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS evidence (
  id INTEGER PRIMARY KEY, kind TEXT NOT NULL, origin TEXT NOT NULL, listing_id INTEGER, snapshot_id INTEGER,
  source_document_id INTEGER, review_id INTEGER, field TEXT NOT NULL, value TEXT NOT NULL, locator TEXT NOT NULL,
  excerpt TEXT, excerpt_hash TEXT, observed_at TEXT NOT NULL, evidence_role TEXT NOT NULL DEFAULT 'OTHER',
  derivation_type TEXT NOT NULL DEFAULT 'ORIGINAL', origin_ids TEXT NOT NULL DEFAULT '[]', capture TEXT,
  link_method TEXT, content_hash TEXT, extractor_version TEXT, status TEXT NOT NULL DEFAULT 'ACTIVE',
  status_reason TEXT, superseded_by_id INTEGER, version INTEGER NOT NULL DEFAULT 1
);
CREATE TABLE IF NOT EXISTS evidence_link (
  id INTEGER PRIMARY KEY, evidence_id INTEGER NOT NULL, target_type TEXT NOT NULL, target_id INTEGER NOT NULL,
  target_field TEXT NOT NULL, stance TEXT NOT NULL, rule_code TEXT
);
CREATE TABLE IF NOT EXISTS origin_relation (
  id INTEGER PRIMARY KEY, origin_a_id INTEGER NOT NULL, origin_b_id INTEGER NOT NULL, relation TEXT NOT NULL,
  basis TEXT NOT NULL, rationale TEXT NOT NULL, evidence_ids TEXT NOT NULL DEFAULT '[]', status TEXT NOT NULL DEFAULT 'ACTIVE',
  created_at TEXT NOT NULL, CHECK (origin_a_id < origin_b_id)
);
CREATE TABLE IF NOT EXISTS evidence_derivation (
  evidence_id INTEGER NOT NULL, ref_type TEXT NOT NULL, ref_id INTEGER NOT NULL, role TEXT NOT NULL,
  PRIMARY KEY (evidence_id, ref_type, ref_id)
);
CREATE INDEX IF NOT EXISTS ix_evidence_derivation_ref ON evidence_derivation (ref_type, ref_id);
CREATE TABLE IF NOT EXISTS candidate (
  id INTEGER PRIMARY KEY, listing_id INTEGER NOT NULL, dwelling_id INTEGER, address_unit_id INTEGER,
  generated_by TEXT NOT NULL, CHECK (dwelling_id IS NOT NULL OR address_unit_id IS NOT NULL)
);
CREATE TABLE IF NOT EXISTS assessment (
  id INTEGER PRIMARY KEY, subject_type TEXT NOT NULL, listing_id INTEGER, identity_match_id INTEGER, snapshot_id INTEGER,
  ruleset_version TEXT NOT NULL, registry_version_id INTEGER NOT NULL, evidence_set_hash TEXT NOT NULL,
  outcome TEXT NOT NULL, rule_score REAL, basis TEXT NOT NULL, resolved_fields TEXT, registry_items_used TEXT NOT NULL DEFAULT '[]',
  flags TEXT NOT NULL DEFAULT '[]', rule_hits TEXT NOT NULL DEFAULT '[]', candidate_count INTEGER, summary TEXT,
  trigger TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'CURRENT', superseded_by_id INTEGER, created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS assessment_candidate (
  assessment_id INTEGER NOT NULL, candidate_id INTEGER NOT NULL, rule_score REAL NOT NULL, excluded INTEGER NOT NULL,
  excluded_by_rule TEXT, rule_hits TEXT NOT NULL DEFAULT '[]', PRIMARY KEY (assessment_id, candidate_id)
);
CREATE TABLE IF NOT EXISTS assessment_evidence (
  assessment_id INTEGER NOT NULL, evidence_id INTEGER NOT NULL, evidence_version INTEGER NOT NULL,
  PRIMARY KEY (assessment_id, evidence_id)
);
CREATE TABLE IF NOT EXISTS review_task (
  id INTEGER PRIMARY KEY, task_type TEXT NOT NULL, listing_id INTEGER, identity_match_id INTEGER, registry_mapping_id INTEGER, derivation_rule_id INTEGER,
  base_assessment_id INTEGER, status TEXT NOT NULL DEFAULT 'OPEN', priority INTEGER NOT NULL DEFAULT 3,
  version INTEGER NOT NULL DEFAULT 1, created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS review (
  id INTEGER PRIMARY KEY, task_id INTEGER NOT NULL, decision TEXT NOT NULL, rationale TEXT NOT NULL,
  actor TEXT NOT NULL DEFAULT 'USER', base_assessment_id INTEGER, result_assessment_id INTEGER, candidate_id INTEGER,
  address_unit_id INTEGER, support_assessment TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'ACTIVE',
  supersedes_review_id INTEGER, retraction_rationale TEXT, created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS review_evidence (
  review_id INTEGER NOT NULL, evidence_id INTEGER NOT NULL, role TEXT NOT NULL, PRIMARY KEY (review_id, evidence_id)
);
CREATE TABLE IF NOT EXISTS property (
  id INTEGER PRIMARY KEY, community_id INTEGER NOT NULL, status TEXT NOT NULL DEFAULT 'ACTIVE',
  resolved_dwelling_id INTEGER, current_min_price INTEGER, active_listing_count INTEGER NOT NULL DEFAULT 0,
  first_seen_at TEXT NOT NULL, version INTEGER NOT NULL DEFAULT 1
);
CREATE TABLE IF NOT EXISTS property_membership (
  id INTEGER PRIMARY KEY, property_id INTEGER NOT NULL, listing_id INTEGER NOT NULL, valid_from TEXT NOT NULL,
  valid_to TEXT, operation_id INTEGER
);
CREATE TABLE IF NOT EXISTS identity_match (
  id INTEGER PRIMARY KEY, listing_a_id INTEGER NOT NULL, listing_b_id INTEGER NOT NULL, status TEXT NOT NULL,
  current_assessment_id INTEGER, conflicts TEXT NOT NULL DEFAULT '[]', version INTEGER NOT NULL DEFAULT 1,
  UNIQUE (listing_a_id, listing_b_id), CHECK (listing_a_id < listing_b_id)
);
CREATE TABLE IF NOT EXISTS merge_operation (
  id INTEGER PRIMARY KEY, op_type TEXT NOT NULL, actor TEXT NOT NULL, rationale TEXT NOT NULL, ruleset_version TEXT NOT NULL,
  identity_match_id INTEGER, reverts_operation_id INTEGER, created_at TEXT NOT NULL, evidence_ids TEXT NOT NULL DEFAULT '[]'
);
CREATE TABLE IF NOT EXISTS merge_operation_listing (
  operation_id INTEGER NOT NULL, listing_id INTEGER NOT NULL, from_property_id INTEGER NOT NULL, to_property_id INTEGER NOT NULL,
  PRIMARY KEY (operation_id, listing_id)
);
CREATE TABLE IF NOT EXISTS listing_event (
  id INTEGER PRIMARY KEY, event_id TEXT UNIQUE NOT NULL, event_type TEXT NOT NULL, schema_version INTEGER NOT NULL,
  idempotency_key TEXT UNIQUE NOT NULL, listing_id INTEGER, property_id INTEGER, source_id INTEGER,
  caused_by TEXT NOT NULL, payload TEXT NOT NULL, notify_eligible INTEGER NOT NULL, suppression_reason TEXT,
  occurred_at TEXT NOT NULL, recorded_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS alert_rule (
  id INTEGER PRIMARY KEY, name TEXT NOT NULL, enabled INTEGER NOT NULL DEFAULT 1, event_types TEXT NOT NULL,
  max_total_price INTEGER, min_price_drop_pct REAL, min_rooms INTEGER, min_building_ping REAL,
  membership_statuses TEXT NOT NULL DEFAULT '["MEMBER","LIKELY_MEMBER"]', channel TEXT NOT NULL,
  notify_corrections INTEGER NOT NULL DEFAULT 1, version INTEGER NOT NULL DEFAULT 1, deleted_at TEXT
);
CREATE TABLE IF NOT EXISTS notification (
  id INTEGER PRIMARY KEY, notification_kind TEXT NOT NULL, alert_rule_id INTEGER NOT NULL, event_id TEXT NOT NULL,
  channel TEXT NOT NULL, rendered TEXT NOT NULL, status TEXT NOT NULL, attempts INTEGER NOT NULL DEFAULT 0,
  last_error TEXT, sent_at TEXT, corrects_notification_id INTEGER
);
CREATE UNIQUE INDEX IF NOT EXISTS ux_notification_event ON notification (alert_rule_id, event_id) WHERE notification_kind = 'EVENT';
CREATE TABLE IF NOT EXISTS decision_call (
  id INTEGER PRIMARY KEY, purpose TEXT NOT NULL, listing_id INTEGER, identity_match_id INTEGER, provider TEXT NOT NULL, model TEXT,
  endpoint TEXT, wire_format TEXT, questions TEXT NOT NULL DEFAULT '[]', request_hash TEXT, image_count INTEGER NOT NULL DEFAULT 0,
  status TEXT NOT NULL, http_status INTEGER, error TEXT, input_tokens INTEGER, cost_usd REAL, latency_ms INTEGER, created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS lvr_transaction (
  id INTEGER PRIMARY KEY, serial TEXT UNIQUE NOT NULL, season TEXT, number INTEGER NOT NULL, floor INTEGER, trade_date TEXT,
  rooms TEXT, ping_ex_parking REAL, main_ping REAL, total_price INTEGER, parking_price INTEGER, source_file TEXT, imported_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS idempotency_record (
  api_key_id TEXT NOT NULL, method TEXT NOT NULL, route TEXT NOT NULL, key TEXT NOT NULL, request_hash TEXT NOT NULL,
  response_status INTEGER NOT NULL, response_body TEXT NOT NULL, created_at TEXT NOT NULL, expires_at TEXT NOT NULL,
  PRIMARY KEY (api_key_id, method, route, key)
);
"""


def now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


class JevConnection(sqlite3.Connection):
    """在 transaction() 之內，函式各自呼叫的 commit() 都延到最外層才真正提交；出錯就整個還原（一個操作一個交易）。"""

    _depth = 0

    def commit(self):
        if self._depth == 0:
            super().commit()

    @contextmanager
    def transaction(self):
        self._depth += 1
        try:
            yield self
        except BaseException:
            self._depth -= 1
            if self._depth == 0:
                self.rollback()
            raise
        self._depth -= 1
        if self._depth == 0:
            super().commit()


def _missing_columns(conn):
    """與 DDL 比對：找出既有資料表缺少的欄位（只處理新增欄位這種相容變更）。"""
    ref = sqlite3.connect(":memory:")
    ref.executescript(DDL)
    missing = []
    for (table,) in ref.execute("SELECT name FROM sqlite_master WHERE type='table'"):
        have = {r[1] for r in conn.execute(f"PRAGMA table_info({table})")}
        if not have:
            continue  # 新資料表由 CREATE TABLE IF NOT EXISTS 建立
        for cid, name, ctype, notnull, default, pk in ref.execute(f"PRAGMA table_info({table})"):
            if name not in have:
                missing.append((table, name, ctype, notnull, default))
    ref.close()
    return missing


def migrate(conn, path=None):
    """依 schema_version 升級：先備份，再補上缺少的欄位。回傳做了哪些變更。"""
    row = conn.execute("SELECT value FROM meta WHERE key='schema_version'").fetchone()         if conn.execute("SELECT 1 FROM sqlite_master WHERE name='meta'").fetchone() else None
    current = int(row[0]) if row else None
    missing = _missing_columns(conn)
    changes = []
    if missing and path and path != ":memory:" and Path(path).exists():
        changes.append(f"已備份到 {backup(conn, path, reason='migrate')}")
    for table, name, ctype, notnull, default in missing:
        if notnull and default is None:
            raise RuntimeError(f"無法自動升級：{table}.{name} 是 NOT NULL 且沒有預設值，需要手寫遷移")
        clause = f" NOT NULL DEFAULT {default}" if notnull else (f" DEFAULT {default}" if default is not None else "")
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {name} {ctype}{clause}")
        changes.append(f"新增欄位 {table}.{name}")
    conn.executescript(DDL)
    conn.execute("INSERT OR REPLACE INTO meta (key, value) VALUES ('schema_version', ?)", (str(SCHEMA_VERSION),))
    if current is not None and current != SCHEMA_VERSION:
        changes.append(f"schema_version {current} → {SCHEMA_VERSION}")
    conn.commit()
    return changes


def backup(conn, path, reason="manual", keep=20):
    """用 SQLite 線上備份 API 複製資料庫到 data/backups/（保留最近 keep 份）。"""
    src = Path(path)
    dest_dir = src.parent / "backups"
    dest_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    dest = dest_dir / f"{src.stem}-{stamp}-{re.sub(r'[^a-z0-9-]', '', reason)}.db"
    target = sqlite3.connect(str(dest))
    with target:
        conn.backup(target)
    target.close()
    olds = sorted(dest_dir.glob(f"{src.stem}-*.db"))
    for old in olds[:-keep]:
        old.unlink()
    return dest


def connect(path) -> JevConnection:
    path = str(path)
    if path != ":memory:":
        Path(path).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path, check_same_thread=False, factory=JevConnection)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    if path != ":memory:":
        conn.execute("PRAGMA journal_mode = WAL")
    conn.migration_log = migrate(conn, path)
    return conn


def j(value) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True)


def unj(text, default=None):
    if text is None:
        return default
    return json.loads(text)
