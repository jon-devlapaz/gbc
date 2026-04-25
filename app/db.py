import sqlite3
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS scans (
  id INTEGER PRIMARY KEY,
  started_at TEXT NOT NULL,
  finished_at TEXT
);

CREATE TABLE IF NOT EXISTS entries (
  id INTEGER PRIMARY KEY,
  scan_id INTEGER NOT NULL REFERENCES scans(id),
  path TEXT NOT NULL,
  kind TEXT NOT NULL,
  inode INTEGER NOT NULL,
  size_bytes INTEGER,
  mtime TEXT,
  file_count INTEGER,
  sample_files TEXT,
  status TEXT NOT NULL,
  reason TEXT,
  purpose TEXT,
  user_decision TEXT
);

CREATE UNIQUE INDEX IF NOT EXISTS entries_scan_path ON entries(scan_id, path);

CREATE TABLE IF NOT EXISTS actions (
  id INTEGER PRIMARY KEY,
  scan_id INTEGER NOT NULL REFERENCES scans(id),
  entry_id INTEGER REFERENCES entries(id),
  ts TEXT NOT NULL,
  action TEXT NOT NULL,
  path TEXT NOT NULL,
  archive_path TEXT,
  state TEXT NOT NULL,
  error_detail TEXT
);

CREATE TABLE IF NOT EXISTS sessions (
  session_id TEXT PRIMARY KEY,
  family TEXT,
  cwd TEXT,
  started_at TEXT,
  ended_at TEXT,
  message_count INTEGER NOT NULL DEFAULT 0,
  prompt_count INTEGER NOT NULL DEFAULT 0,
  first_prompt TEXT,
  jsonl_path TEXT NOT NULL UNIQUE,
  jsonl_mtime REAL NOT NULL,
  indexed_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS sessions_family     ON sessions(family);
CREATE INDEX IF NOT EXISTS sessions_started_at ON sessions(started_at DESC);

CREATE VIRTUAL TABLE IF NOT EXISTS prompts_fts USING fts5(
  session_id UNINDEXED,
  timestamp UNINDEXED,
  content,
  tokenize = "unicode61"
);

CREATE TABLE IF NOT EXISTS families (
  name TEXT PRIMARY KEY,
  path_prefix TEXT NOT NULL,
  is_override INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS index_runs (
  id INTEGER PRIMARY KEY,
  started_at TEXT NOT NULL,
  finished_at TEXT,
  files_seen INTEGER,
  files_updated INTEGER,
  error_count INTEGER
);

CREATE TABLE IF NOT EXISTS cost_events (
  id INTEGER PRIMARY KEY,
  message_uuid TEXT NOT NULL UNIQUE,
  session_id TEXT NOT NULL,
  parent_session_id TEXT,
  jsonl_path TEXT NOT NULL,
  ts TEXT NOT NULL,
  model TEXT NOT NULL,
  service_tier TEXT,
  input_tokens INTEGER NOT NULL DEFAULT 0,
  output_tokens INTEGER NOT NULL DEFAULT 0,
  cache_creation_5m_tokens INTEGER NOT NULL DEFAULT 0,
  cache_creation_1h_tokens INTEGER NOT NULL DEFAULT 0,
  cache_read_tokens INTEGER NOT NULL DEFAULT 0,
  input_rate REAL NOT NULL,
  output_rate REAL NOT NULL,
  cache_write_5m_rate REAL NOT NULL,
  cache_write_1h_rate REAL NOT NULL,
  cache_read_rate REAL NOT NULL,
  cost_usd REAL NOT NULL,
  unknown_pricing INTEGER NOT NULL DEFAULT 0
);

CREATE INDEX IF NOT EXISTS cost_events_ts      ON cost_events(ts DESC);
CREATE INDEX IF NOT EXISTS cost_events_session ON cost_events(session_id);
CREATE INDEX IF NOT EXISTS cost_events_parent  ON cost_events(parent_session_id);
CREATE INDEX IF NOT EXISTS cost_events_model   ON cost_events(model);
"""


def init_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(SCHEMA)
    conn.commit()


def connect(db_path: Path) -> sqlite3.Connection:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    init_schema(conn)
    return conn
