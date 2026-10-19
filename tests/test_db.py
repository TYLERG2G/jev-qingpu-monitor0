"""資料層：一個操作一個交易、schema 自動升級、備份。"""

import pytest

from jev import registry
from jev.db import SCHEMA_VERSION, backup, connect


def test_transaction_rolls_back_inner_commits(tmp_path):
    conn = connect(tmp_path / "t.db")
    registry.load(conn)
    before = conn.execute("SELECT COUNT(*) FROM listing_event").fetchone()[0]
    with pytest.raises(RuntimeError):
        with conn.transaction():
            conn.execute("INSERT INTO meta (key, value) VALUES ('x', '1')")
            conn.commit()  # 函式內部的 commit 不會真的提交
            raise RuntimeError("中途失敗")
    assert conn.execute("SELECT value FROM meta WHERE key='x'").fetchone() is None
    assert conn.execute("SELECT COUNT(*) FROM listing_event").fetchone()[0] == before
    with conn.transaction():
        conn.execute("INSERT INTO meta (key, value) VALUES ('y', '1')")
    assert conn.execute("SELECT value FROM meta WHERE key='y'").fetchone()[0] == "1"


def test_old_database_is_backed_up_and_upgraded(tmp_path):
    db = tmp_path / "old.db"
    conn = connect(db)
    registry.load(conn)
    conn.execute("ALTER TABLE merge_operation DROP COLUMN evidence_ids")  # 模擬 v0.6 之前建立的資料庫
    conn.execute("UPDATE meta SET value='1' WHERE key='schema_version'")
    conn.commit()
    conn.close()
    conn = connect(db)
    assert "evidence_ids" in {r[1] for r in conn.execute("PRAGMA table_info(merge_operation)")}
    assert conn.execute("SELECT value FROM meta WHERE key='schema_version'").fetchone()[0] == str(SCHEMA_VERSION)
    assert any("備份" in c for c in conn.migration_log) and any("merge_operation.evidence_ids" in c for c in conn.migration_log)
    assert list((tmp_path / "backups").glob("old-*-migrate.db"))
    assert connect(db).migration_log == []  # 已是最新版：不再變更


def test_backup_keeps_recent_copies(tmp_path):
    db = tmp_path / "b.db"
    conn = connect(db)
    paths = {backup(conn, db, reason=f"r{i}", keep=3) for i in range(5)}
    assert len(list((tmp_path / "backups").glob("b-*.db"))) <= 3 and paths
