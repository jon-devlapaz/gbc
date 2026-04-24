import sqlite3
import pytest


def test_schema_has_expected_tables(db: sqlite3.Connection):
    names = {r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert {"scans", "entries", "actions"}.issubset(names)


def test_entries_unique_index(db: sqlite3.Connection):
    db.execute("INSERT INTO scans(started_at) VALUES ('2026-01-01')")
    db.execute(
        "INSERT INTO entries(scan_id,path,kind,inode,status) VALUES (1,'/x','dir',1,'unknown')"
    )
    with pytest.raises(sqlite3.IntegrityError):
        db.execute(
            "INSERT INTO entries(scan_id,path,kind,inode,status) VALUES (1,'/x','dir',2,'unknown')"
        )


def test_actions_state_column_exists(db: sqlite3.Connection):
    cols = [r[1] for r in db.execute("PRAGMA table_info(actions)")]
    assert "state" in cols and "error_detail" in cols and "entry_id" in cols


def test_sessions_table_exists(db):
    cols = [r[1] for r in db.execute("PRAGMA table_info(sessions)")]
    for c in ["session_id", "family", "cwd", "started_at", "ended_at",
             "message_count", "prompt_count", "first_prompt",
             "jsonl_path", "jsonl_mtime", "indexed_at"]:
        assert c in cols, f"missing column {c}"


def test_prompts_fts_virtual_table_exists(db):
    rows = db.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='prompts_fts'"
    ).fetchall()
    assert rows, "prompts_fts virtual table missing"


def test_families_table_exists(db):
    cols = [r[1] for r in db.execute("PRAGMA table_info(families)")]
    for c in ["name", "path_prefix", "is_override"]:
        assert c in cols


def test_index_runs_table_exists(db):
    cols = [r[1] for r in db.execute("PRAGMA table_info(index_runs)")]
    for c in ["id", "started_at", "finished_at", "files_seen",
             "files_updated", "error_count"]:
        assert c in cols
