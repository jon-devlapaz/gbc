import sqlite3
import pytest
from app.fts import escape_query, search


@pytest.fixture
def fts_db(tmp_path):
    conn = sqlite3.connect(tmp_path / "t.db")
    conn.row_factory = sqlite3.Row
    conn.execute("""
      CREATE TABLE sessions (
        session_id TEXT PRIMARY KEY, family TEXT, cwd TEXT,
        started_at TEXT, jsonl_path TEXT)
    """)
    conn.execute("""
      CREATE VIRTUAL TABLE prompts_fts USING fts5(
        session_id UNINDEXED, timestamp UNINDEXED, content,
        tokenize = "unicode61")
    """)
    conn.executemany(
        "INSERT INTO sessions VALUES (?, ?, ?, ?, ?)",
        [("s1", "socratink", "/x", "2026-01-01", "/a.jsonl"),
         ("s2", "other",     "/y", "2026-01-02", "/b.jsonl")],
    )
    conn.executemany(
        "INSERT INTO prompts_fts(session_id, timestamp, content) VALUES (?, ?, ?)",
        [("s1", "t1", "fix the auth bug in login"),
         ("s1", "t2", "add stripe payment flow"),
         ("s2", "t3", "the auth module broke again")],
    )
    conn.commit()
    return conn


@pytest.mark.parametrize("raw", [
    "-hello",
    "NEAR(a b)",
    "/Users/jondev/foo",
    "a\"b",
    "* wildcard",
    "plain words",
    "",
    "   ",
])
def test_escape_does_not_raise_on_match(fts_db, raw):
    q = escape_query(raw)
    fts_db.execute("SELECT * FROM prompts_fts WHERE prompts_fts MATCH ?", (q,)).fetchall() if q else None


def test_search_returns_hits(fts_db):
    hits = search(fts_db, "auth", family=None, limit=10)
    assert len(hits) == 2
    assert {h["session_id"] for h in hits} == {"s1", "s2"}


def test_search_family_filter(fts_db):
    hits = search(fts_db, "auth", family="socratink", limit=10)
    assert len(hits) == 1
    assert hits[0]["session_id"] == "s1"


def test_search_empty_query_returns_empty(fts_db):
    assert search(fts_db, "", family=None, limit=10) == []


def test_search_snippet_present(fts_db):
    hits = search(fts_db, "stripe", family=None, limit=10)
    assert "stripe" in hits[0]["snippet"].lower()
