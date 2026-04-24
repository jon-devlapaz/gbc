# P2 Session Index Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a session index over `~/.claude/projects/*.jsonl` with FTS5 search on user prompts, project-family detection, a dashboard widget on home, and a transcript detail view — on top of the existing claude-workspace-tool.

**Architecture:** Six new modules (`events`, `families`, `fts`, `session_reader`, `session_index`, plus route additions to `main`). New SQLite tables (`sessions`, `prompts_fts`, `families`, `index_runs`). Lazy incremental reindex on page loads, guarded by `fcntl` advisory lock. Read-only over the session corpus; prompts never sent to LLM.

**Tech Stack:** Python 3.11+, FastAPI, Jinja2, HTMX, SQLite FTS5 (stdlib), pytest — all already in place.

**Spec:** `docs/superpowers/specs/2026-04-24-p2-session-index-design.md`

---

## File Structure

```
claude-workspace-tool/
├── app/
│   ├── events.py              # jsonl parser (NEW)
│   ├── families.py            # project-family detector (NEW)
│   ├── fts.py                 # FTS5 query escaping + search (NEW)
│   ├── session_reader.py      # stream jsonl for detail view (NEW)
│   ├── session_index.py       # reindex orchestrator (NEW)
│   ├── main.py                # routes + dashboard widget (MODIFY)
│   ├── db.py                  # schema additions (MODIFY)
│   └── templates/
│       ├── home.html          # dashboard widget (MODIFY)
│       ├── sessions.html      # timeline + search (NEW)
│       ├── session_detail.html# transcript (NEW)
│       └── families.html      # family CRUD (NEW)
├── tests/
│   ├── test_events.py         # NEW
│   ├── test_families.py       # NEW
│   ├── test_fts.py            # NEW
│   ├── test_session_reader.py # NEW
│   ├── test_session_index.py  # NEW
│   ├── test_sessions_routes.py# NEW
│   └── test_p2_integration.py # NEW
```

---

## Task 1: DB Schema — add P2 tables

**Files:**
- Modify: `app/db.py` — append to `SCHEMA` string
- Test: `tests/test_db.py` — add 4 assertions

- [ ] **Step 1: Write failing tests**

Append to `tests/test_db.py`:

```python
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
```

- [ ] **Step 2: Verify fail**

Run: `.venv/bin/pytest tests/test_db.py -v`
Expected: 4 FAILED (table/column missing).

- [ ] **Step 3: Modify `app/db.py` SCHEMA string**

Append before the closing triple-quote of `SCHEMA`:

```sql
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
```

- [ ] **Step 4: Verify pass**

`.venv/bin/pytest tests/test_db.py -v`
Expected: all (prior + 4 new) PASSED.

- [ ] **Step 5: Commit**

```bash
git add app/db.py tests/test_db.py
git -c user.email=jonathan10620@gmail.com -c user.name=jondev commit -m "feat(db): P2 schema — sessions, prompts_fts, families, index_runs"
```

---

## Task 2: `events.py` — jsonl parser

**Files:**
- Create: `app/events.py`
- Create: `tests/test_events.py`

- [ ] **Step 1: Write failing tests**

```python
# tests/test_events.py
import json
from pathlib import Path
from app.events import parse_file


def _write(path: Path, lines: list[dict]) -> Path:
    path.write_text("\n".join(json.dumps(x) for x in lines))
    return path


def test_happy_path_yields_one_prompt(tmp_path: Path):
    p = _write(tmp_path / "a.jsonl", [
        {"sessionId": "s1", "type": "user", "timestamp": "2026-01-01T00:00:00Z",
         "cwd": "/Users/jondev/dev/socratink/prod/socratink-app",
         "message": {"role": "user", "content": "hello world"}},
        {"sessionId": "s1", "type": "assistant", "timestamp": "2026-01-01T00:00:05Z",
         "message": {"role": "assistant", "content": "hi there"}},
    ])
    r = parse_file(p)
    assert r.session.session_id == "s1"
    assert r.session.cwd.endswith("socratink-app")
    assert r.session.started_at == "2026-01-01T00:00:00Z"
    assert r.session.ended_at == "2026-01-01T00:00:05Z"
    assert r.session.message_count == 2
    assert r.session.prompt_count == 1
    assert r.session.first_prompt == "hello world"
    assert len(r.prompts) == 1
    assert r.prompts[0].content == "hello world"


def test_tool_result_is_not_a_prompt(tmp_path: Path):
    p = _write(tmp_path / "a.jsonl", [
        {"sessionId": "s2", "type": "user", "timestamp": "t1",
         "message": {"role": "user", "content": [
             {"type": "tool_result", "content": "[file contents]"}
         ]}},
    ])
    r = parse_file(p)
    assert r.session.prompt_count == 0
    assert r.prompts == []


def test_ismeta_prompt_is_skipped(tmp_path: Path):
    p = _write(tmp_path / "a.jsonl", [
        {"sessionId": "s3", "type": "user", "timestamp": "t1", "isMeta": True,
         "message": {"role": "user", "content": "system injected"}},
    ])
    r = parse_file(p)
    assert r.session.prompt_count == 0


def test_array_content_text_is_a_prompt(tmp_path: Path):
    p = _write(tmp_path / "a.jsonl", [
        {"sessionId": "s4", "type": "user", "timestamp": "t1",
         "message": {"role": "user", "content": [
             {"type": "text", "text": "hello"}
         ]}},
    ])
    r = parse_file(p)
    assert r.session.prompt_count == 1
    assert r.prompts[0].content == "hello"


def test_malformed_line_captured_as_error(tmp_path: Path):
    p = tmp_path / "a.jsonl"
    p.write_text('{"good": true}\nnot json at all\n{"also": "good"}\n')
    r = parse_file(p)
    assert len(r.errors) == 1
    assert r.errors[0].line_number == 2


def test_missing_cwd_and_timestamps_tolerated(tmp_path: Path):
    p = _write(tmp_path / "a.jsonl", [
        {"sessionId": "s5", "type": "user",
         "message": {"role": "user", "content": "hi"}},
    ])
    r = parse_file(p)
    assert r.session.session_id == "s5"
    assert r.session.cwd is None
    assert r.session.started_at is None
    assert r.session.prompt_count == 1


def test_prompt_content_clamped_when_huge(tmp_path: Path):
    big = "x" * 300_000
    p = _write(tmp_path / "a.jsonl", [
        {"sessionId": "s6", "type": "user", "timestamp": "t1",
         "message": {"role": "user", "content": big}},
    ])
    r = parse_file(p)
    assert len(r.prompts[0].content) <= 256_100  # 256_000 + suffix
    assert r.prompts[0].content.endswith("[…truncated]")


def test_first_prompt_truncated_to_200(tmp_path: Path):
    long = "a" * 500
    p = _write(tmp_path / "a.jsonl", [
        {"sessionId": "s7", "type": "user", "timestamp": "t1",
         "message": {"role": "user", "content": long}},
    ])
    r = parse_file(p)
    assert len(r.session.first_prompt) <= 200
```

- [ ] **Step 2: Verify fail**

`.venv/bin/pytest tests/test_events.py -v` → FAIL (module not found).

- [ ] **Step 3: Implement `app/events.py`**

```python
# app/events.py
from __future__ import annotations
import json
from dataclasses import dataclass, field
from pathlib import Path

MAX_PROMPT_BYTES = 256_000
FIRST_PROMPT_CHARS = 200
TRUNCATE_SUFFIX = "[…truncated]"


@dataclass
class PromptRow:
    session_id: str
    timestamp: str
    content: str


@dataclass
class ParseError:
    line_number: int
    reason: str


@dataclass
class SessionMeta:
    session_id: str
    cwd: str | None = None
    started_at: str | None = None
    ended_at: str | None = None
    message_count: int = 0
    prompt_count: int = 0
    first_prompt: str | None = None


@dataclass
class ParseResult:
    session: SessionMeta
    prompts: list[PromptRow] = field(default_factory=list)
    errors: list[ParseError] = field(default_factory=list)


def _extract_prompt_text(msg: dict) -> str | None:
    """Return the human prompt text if this is a user prompt, else None."""
    if msg.get("role") != "user":
        return None
    content = msg.get("content")
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for part in content:
            if not isinstance(part, dict):
                continue
            if part.get("type") == "tool_result":
                return None  # tool result, not a prompt
            if part.get("type") == "text" and isinstance(part.get("text"), str):
                parts.append(part["text"])
        if parts:
            return "\n".join(parts)
    return None


def _clamp(text: str) -> str:
    if len(text) <= MAX_PROMPT_BYTES:
        return text
    return text[:MAX_PROMPT_BYTES] + TRUNCATE_SUFFIX


def parse_file(path: Path) -> ParseResult:
    session_id = path.stem  # filename without extension is the uuid
    meta = SessionMeta(session_id=session_id)
    prompts: list[PromptRow] = []
    errors: list[ParseError] = []

    with path.open("r", encoding="utf-8", errors="replace") as fp:
        for i, raw in enumerate(fp, start=1):
            raw = raw.strip()
            if not raw:
                continue
            try:
                ev = json.loads(raw)
            except json.JSONDecodeError as e:
                errors.append(ParseError(line_number=i, reason=str(e)))
                continue

            meta.message_count += 1

            ts = ev.get("timestamp")
            if ts:
                if meta.started_at is None:
                    meta.started_at = ts
                meta.ended_at = ts

            if meta.cwd is None and ev.get("cwd"):
                meta.cwd = ev["cwd"]

            if ev.get("type") != "user" or ev.get("isMeta"):
                continue

            text = _extract_prompt_text(ev.get("message") or {})
            if text is None:
                continue

            meta.prompt_count += 1
            if meta.first_prompt is None:
                meta.first_prompt = text[:FIRST_PROMPT_CHARS]

            prompts.append(PromptRow(
                session_id=session_id,
                timestamp=ts or "",
                content=_clamp(text),
            ))

    return ParseResult(session=meta, prompts=prompts, errors=errors)
```

- [ ] **Step 4: Verify pass**

`.venv/bin/pytest tests/test_events.py -v`
Expected: 8 PASSED.

Full suite: `.venv/bin/pytest -v` → existing + 8 new, all green.

- [ ] **Step 5: Commit**

```bash
git add app/events.py tests/test_events.py
git -c user.email=jonathan10620@gmail.com -c user.name=jondev commit -m "feat(events): jsonl parser extracts user prompts (skips tool_result, isMeta)"
```

---

## Task 3: `families.py` — project-family detection

**Files:**
- Create: `app/families.py`
- Create: `tests/test_families.py`

- [ ] **Step 1: Write failing tests**

```python
# tests/test_families.py
import pytest
from app.families import detect, collapse_worktree, FamilyOverride


def test_collapse_claude_worktree():
    p = "/Users/jondev/dev/socratink/prod/socratink-app/.claude/worktrees/determined-bhaskara"
    assert collapse_worktree(p) == "/Users/jondev/dev/socratink/prod/socratink-app"


def test_collapse_dot_worktrees():
    p = "/Users/jondev/dev/socratink/prod/socratink-app/.worktrees/repair-reps-slice-b"
    assert collapse_worktree(p) == "/Users/jondev/dev/socratink/prod/socratink-app"


def test_collapse_noop_when_no_worktree():
    p = "/Users/jondev/dev/socratink/prod/socratink-app"
    assert collapse_worktree(p) == p


def test_detect_clusters_sibling_subdirs():
    cwds = [
        "/Users/jondev/dev/socratink/prod/socratink-app",
        "/Users/jondev/dev/socratink/prod/socratink-app/.claude/worktrees/determined-bhaskara",
        "/Users/jondev/dev/socratink/prod/socratink-landing",
    ]
    result = detect(cwds, overrides=[])
    # Both socratink-app paths collapse to same, get one family
    # socratink-landing is sibling — either own family or grouped with socratink
    assert "socratink-app" in result[cwds[0]].lower() or "socratink" in result[cwds[0]].lower()


def test_unsorted_fallback():
    cwds = ["/Users/jondev/tetris"]
    result = detect(cwds, overrides=[])
    # solitary path, no cluster → "unsorted" (or own family — either OK)
    assert cwds[0] in result


def test_override_longest_prefix_wins():
    cwds = ["/Users/jondev/dev/socratink/prod/socratink-app/docs"]
    overrides = [
        FamilyOverride(name="socratink", path_prefix="/Users/jondev/dev/socratink"),
        FamilyOverride(name="socratink-docs", path_prefix="/Users/jondev/dev/socratink/prod/socratink-app/docs"),
    ]
    result = detect(cwds, overrides=overrides)
    assert result[cwds[0]] == "socratink-docs"


def test_segment_aware_not_substring():
    # /foo/barbaz should NOT match prefix /foo/bar
    cwds = ["/Users/jondev/sockratink"]  # typo variant
    overrides = [FamilyOverride(name="socratink", path_prefix="/Users/jondev/socratink")]
    result = detect(cwds, overrides=overrides)
    assert result[cwds[0]] != "socratink"
```

- [ ] **Step 2: Verify fail**

`.venv/bin/pytest tests/test_families.py -v` → FAIL.

- [ ] **Step 3: Implement `app/families.py`**

```python
# app/families.py
from __future__ import annotations
from dataclasses import dataclass
from pathlib import PurePosixPath

WORKTREE_MARKERS = (".claude/worktrees", ".worktrees")


@dataclass
class FamilyOverride:
    name: str
    path_prefix: str


def collapse_worktree(cwd: str) -> str:
    """If cwd is inside a claude-code worktree, collapse to the parent repo root."""
    for marker in WORKTREE_MARKERS:
        idx = cwd.find("/" + marker + "/")
        if idx >= 0:
            return cwd[:idx]
    return cwd


def _is_prefix(path: str, prefix: str) -> bool:
    """Segment-aware prefix: /a/b is a prefix of /a/b/c but NOT of /a/bb."""
    if path == prefix:
        return True
    return path.startswith(prefix.rstrip("/") + "/")


def _auto_family_name(path: str) -> str:
    """Use last segment of collapsed cwd as the family name."""
    return PurePosixPath(path).name or "unsorted"


def detect(cwds, overrides: list[FamilyOverride]) -> dict[str, str]:
    """Map each cwd to a family name.

    1. Override match wins (longest prefix).
    2. Otherwise collapse worktrees, use the last path segment of the collapsed dir.
    3. Empty / root → 'unsorted'.
    """
    # Sort overrides by prefix length, longest first (for longest-wins)
    sorted_overrides = sorted(overrides, key=lambda o: len(o.path_prefix), reverse=True)

    out: dict[str, str] = {}
    for cwd in cwds:
        matched = None
        for ov in sorted_overrides:
            if _is_prefix(cwd, ov.path_prefix):
                matched = ov.name
                break
        if matched is None:
            collapsed = collapse_worktree(cwd)
            matched = _auto_family_name(collapsed) or "unsorted"
        out[cwd] = matched
    return out
```

- [ ] **Step 4: Verify pass**

`.venv/bin/pytest tests/test_families.py -v` → 7 PASSED.

- [ ] **Step 5: Commit**

```bash
git add app/families.py tests/test_families.py
git -c user.email=jonathan10620@gmail.com -c user.name=jondev commit -m "feat(families): segment-aware project-family detection with worktree collapse + overrides"
```

---

## Task 4: `fts.py` — safe FTS5 query building

**Files:**
- Create: `app/fts.py`
- Create: `tests/test_fts.py`

- [ ] **Step 1: Write failing tests**

```python
# tests/test_fts.py
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
    # should not raise an FTS5 syntax error
    fts_db.execute("SELECT * FROM prompts_fts WHERE prompts_fts MATCH ?", (q,)).fetchall()


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
```

- [ ] **Step 2: Verify fail**

`.venv/bin/pytest tests/test_fts.py -v` → FAIL.

- [ ] **Step 3: Implement `app/fts.py`**

```python
# app/fts.py
from __future__ import annotations
import re
import sqlite3

SAFE_TOKEN = re.compile(r"[A-Za-z0-9]+")
SNIPPET_LEN = 160
SNIPPET_CTX = 8  # tokens of context


def escape_query(user_input: str) -> str:
    """Extract safe tokens from user input and join them with AND (implicit).

    FTS5 is unforgiving with symbols. We tokenize aggressively and quote.
    Empty input → empty string (caller skips the MATCH).
    """
    if not user_input:
        return ""
    tokens = SAFE_TOKEN.findall(user_input)
    if not tokens:
        return ""
    return " ".join(f'"{t}"' for t in tokens)


def search(db: sqlite3.Connection, query: str, family: str | None, limit: int = 50) -> list[dict]:
    q = escape_query(query)
    if not q:
        return []
    sql = [
        "SELECT p.session_id AS session_id, p.timestamp AS timestamp,",
        f"       snippet(prompts_fts, 2, '[', ']', '…', {SNIPPET_CTX}) AS snippet,",
        "       s.family AS family, s.cwd AS cwd, s.started_at AS started_at",
        "FROM prompts_fts p",
        "JOIN sessions s ON s.session_id = p.session_id",
        "WHERE prompts_fts MATCH ?",
    ]
    params: list = [q]
    if family:
        sql.append("  AND s.family = ?")
        params.append(family)
    sql.append("ORDER BY p.timestamp DESC LIMIT ?")
    params.append(limit)
    rows = db.execute("\n".join(sql), params).fetchall()
    out = []
    for r in rows:
        d = dict(r)
        if d.get("snippet"):
            d["snippet"] = d["snippet"][:SNIPPET_LEN]
        out.append(d)
    return out
```

- [ ] **Step 4: Verify pass**

`.venv/bin/pytest tests/test_fts.py -v` → PASSED (12: 8 param + 4 specific).

- [ ] **Step 5: Commit**

```bash
git add app/fts.py tests/test_fts.py
git -c user.email=jonathan10620@gmail.com -c user.name=jondev commit -m "feat(fts): safe FTS5 query escape + snippet search with family filter"
```

---

## Task 5: `session_reader.py` — stream transcripts for detail view

**Files:**
- Create: `app/session_reader.py`
- Create: `tests/test_session_reader.py`

- [ ] **Step 1: Write failing tests**

```python
# tests/test_session_reader.py
import json
from pathlib import Path
from app.session_reader import stream, EventView


def _write(path: Path, events: list[dict]) -> Path:
    path.write_text("\n".join(json.dumps(e) for e in events))
    return path


def test_stream_yields_events_in_order(tmp_path):
    p = _write(tmp_path / "s.jsonl", [
        {"type": "user", "timestamp": "t1",
         "message": {"role": "user", "content": "hello"}},
        {"type": "assistant", "timestamp": "t2",
         "message": {"role": "assistant", "content": "hi"}},
    ])
    evs = stream(p, offset=0, limit=100)
    assert len(evs) == 2
    assert evs[0].kind == "prompt"
    assert evs[1].kind == "assistant"


def test_stream_offset_limit(tmp_path):
    events = [
        {"type": "user", "timestamp": f"t{i}",
         "message": {"role": "user", "content": f"msg-{i}"}}
        for i in range(20)
    ]
    p = _write(tmp_path / "s.jsonl", events)
    evs = stream(p, offset=5, limit=3)
    assert len(evs) == 3
    assert "msg-5" in evs[0].body_preview


def test_stream_skips_malformed_line(tmp_path):
    p = tmp_path / "s.jsonl"
    p.write_text('{"type": "user", "message": {"role":"user","content":"x"}}\nNOT-JSON\n')
    evs = stream(p, offset=0, limit=100)
    assert len(evs) == 1


def test_stream_missing_file(tmp_path):
    assert stream(tmp_path / "ghost.jsonl", 0, 10) == []


def test_event_classifies_kinds(tmp_path):
    p = _write(tmp_path / "s.jsonl", [
        {"type": "user", "message": {"role": "user", "content": "x"}},
        {"type": "user", "message": {"role": "user",
           "content": [{"type": "tool_result", "content": "..."}]}},
        {"type": "assistant", "message": {"role": "assistant", "content": "ok"}},
        {"type": "summary"},
    ])
    evs = stream(p, 0, 100)
    kinds = [e.kind for e in evs]
    assert kinds == ["prompt", "tool_result", "assistant", "meta"]
```

- [ ] **Step 2: Verify fail**

`.venv/bin/pytest tests/test_session_reader.py -v` → FAIL.

- [ ] **Step 3: Implement `app/session_reader.py`**

```python
# app/session_reader.py
from __future__ import annotations
import json
from dataclasses import dataclass
from pathlib import Path

PREVIEW_LIMIT = 400


@dataclass
class EventView:
    kind: str           # prompt | assistant | tool_use | tool_result | meta | unknown
    timestamp: str
    body_preview: str


def _classify(ev: dict) -> tuple[str, str]:
    """Return (kind, preview_text)."""
    t = ev.get("type")
    msg = ev.get("message") or {}
    role = msg.get("role")
    content = msg.get("content")

    if t == "user" and role == "user":
        if isinstance(content, str):
            return ("prompt", content)
        if isinstance(content, list):
            for part in content:
                if isinstance(part, dict) and part.get("type") == "tool_result":
                    return ("tool_result", str(part.get("content", "")))
            texts = [p.get("text", "") for p in content
                     if isinstance(p, dict) and p.get("type") == "text"]
            return ("prompt", "\n".join(texts))
        return ("unknown", "")

    if t == "assistant" and role == "assistant":
        if isinstance(content, str):
            return ("assistant", content)
        if isinstance(content, list):
            for part in content:
                if isinstance(part, dict) and part.get("type") == "tool_use":
                    return ("tool_use", f"tool_use: {part.get('name', '?')}")
            texts = [p.get("text", "") for p in content
                     if isinstance(p, dict) and p.get("type") == "text"]
            return ("assistant", "\n".join(texts))

    return ("meta", json.dumps(ev)[:PREVIEW_LIMIT])


def stream(path: Path, offset: int = 0, limit: int = 200) -> list[EventView]:
    if not path.exists():
        return []
    out: list[EventView] = []
    with path.open("r", encoding="utf-8", errors="replace") as fp:
        for i, raw in enumerate(fp):
            if i < offset:
                continue
            if len(out) >= limit:
                break
            raw = raw.strip()
            if not raw:
                continue
            try:
                ev = json.loads(raw)
            except json.JSONDecodeError:
                continue
            kind, preview = _classify(ev)
            out.append(EventView(
                kind=kind,
                timestamp=ev.get("timestamp", ""),
                body_preview=preview[:PREVIEW_LIMIT],
            ))
    return out
```

- [ ] **Step 4: Verify pass**

`.venv/bin/pytest tests/test_session_reader.py -v` → 5 PASSED.

- [ ] **Step 5: Commit**

```bash
git add app/session_reader.py tests/test_session_reader.py
git -c user.email=jonathan10620@gmail.com -c user.name=jondev commit -m "feat(session_reader): stream jsonl transcript with event classification + pagination"
```

---

## Task 6: `session_index.py` — reindex orchestrator

**Files:**
- Create: `app/session_index.py`
- Create: `tests/test_session_index.py`

- [ ] **Step 1: Write failing tests**

```python
# tests/test_session_index.py
import json
import time
from pathlib import Path
from app.db import connect
from app.session_index import reindex


def _write_session(claude: Path, cwd: str, sid: str, prompts: list[str]) -> Path:
    flat = cwd.replace("/", "-")
    proj = claude / "projects" / flat
    proj.mkdir(parents=True, exist_ok=True)
    f = proj / f"{sid}.jsonl"
    events = [
        {"sessionId": sid, "type": "user", "timestamp": f"2026-01-01T00:00:0{i}Z",
         "cwd": cwd,
         "message": {"role": "user", "content": p}}
        for i, p in enumerate(prompts)
    ]
    f.write_text("\n".join(json.dumps(e) for e in events))
    return f


def test_reindex_happy_path(tmp_path):
    claude = tmp_path / ".claude"
    (claude / "projects").mkdir(parents=True)
    _write_session(claude, "/Users/jondev/dev/socratink/prod/socratink-app", "uuid1",
                   ["hello world", "fix the bug"])

    db = connect(tmp_path / "data" / "workspace.db")
    reindex(db, claude)

    sessions = db.execute("SELECT * FROM sessions").fetchall()
    assert len(sessions) == 1
    assert sessions[0]["session_id"] == "uuid1"
    assert sessions[0]["prompt_count"] == 2

    prompts = db.execute("SELECT * FROM prompts_fts").fetchall()
    assert len(prompts) == 2


def test_reindex_idempotent(tmp_path):
    claude = tmp_path / ".claude"
    (claude / "projects").mkdir(parents=True)
    _write_session(claude, "/Users/jondev/dev/x", "u1", ["a", "b"])
    db = connect(tmp_path / "data" / "workspace.db")
    reindex(db, claude)
    n1 = db.execute("SELECT COUNT(*) FROM prompts_fts").fetchone()[0]
    reindex(db, claude)
    n2 = db.execute("SELECT COUNT(*) FROM prompts_fts").fetchone()[0]
    assert n1 == n2


def test_reindex_picks_up_mtime_bump(tmp_path):
    claude = tmp_path / ".claude"
    (claude / "projects").mkdir(parents=True)
    f = _write_session(claude, "/Users/jondev/dev/x", "u1", ["a"])
    db = connect(tmp_path / "data" / "workspace.db")
    reindex(db, claude)
    assert db.execute("SELECT prompt_count FROM sessions").fetchone()[0] == 1

    # Rewrite with more prompts; bump mtime explicitly.
    _write_session(claude, "/Users/jondev/dev/x", "u1", ["a", "b", "c"])
    ts = time.time() + 10
    import os
    os.utime(f, (ts, ts))

    reindex(db, claude)
    assert db.execute("SELECT prompt_count FROM sessions").fetchone()[0] == 3


def test_reindex_removes_deleted_file(tmp_path):
    claude = tmp_path / ".claude"
    (claude / "projects").mkdir(parents=True)
    f = _write_session(claude, "/Users/jondev/dev/x", "u1", ["a"])
    db = connect(tmp_path / "data" / "workspace.db")
    reindex(db, claude)
    assert db.execute("SELECT COUNT(*) FROM sessions").fetchone()[0] == 1

    f.unlink()
    reindex(db, claude)
    assert db.execute("SELECT COUNT(*) FROM sessions").fetchone()[0] == 0


def test_reindex_records_run(tmp_path):
    claude = tmp_path / ".claude"
    (claude / "projects").mkdir(parents=True)
    _write_session(claude, "/Users/jondev/dev/x", "u1", ["a"])
    db = connect(tmp_path / "data" / "workspace.db")
    reindex(db, claude)
    runs = db.execute("SELECT * FROM index_runs").fetchall()
    assert len(runs) == 1
    assert runs[0]["finished_at"] is not None
    assert runs[0]["files_seen"] == 1
    assert runs[0]["files_updated"] == 1
```

- [ ] **Step 2: Verify fail**

`.venv/bin/pytest tests/test_session_index.py -v` → FAIL.

- [ ] **Step 3: Implement `app/session_index.py`**

```python
# app/session_index.py
from __future__ import annotations
import fcntl
import sqlite3
from datetime import datetime
from pathlib import Path
from app.events import parse_file
from app.families import FamilyOverride, detect


def _load_overrides(db: sqlite3.Connection) -> list[FamilyOverride]:
    rows = db.execute(
        "SELECT name, path_prefix FROM families WHERE is_override=1"
    ).fetchall()
    return [FamilyOverride(name=r["name"], path_prefix=r["path_prefix"]) for r in rows]


def _record_start(db: sqlite3.Connection) -> int:
    cur = db.execute(
        "INSERT INTO index_runs(started_at) VALUES (?)",
        (datetime.now().isoformat(),),
    )
    db.commit()
    return cur.lastrowid


def _record_end(db: sqlite3.Connection, run_id: int, seen: int, updated: int, errors: int) -> None:
    db.execute(
        "UPDATE index_runs SET finished_at=?, files_seen=?, files_updated=?, error_count=? WHERE id=?",
        (datetime.now().isoformat(), seen, updated, errors, run_id),
    )
    db.commit()


def _ingest_file(db: sqlite3.Connection, jsonl_path: Path, mtime: float, family: str) -> int:
    """Returns number of parse errors for this file."""
    result = parse_file(jsonl_path)
    meta = result.session

    db.execute("DELETE FROM sessions WHERE jsonl_path=?", (str(jsonl_path),))
    db.execute("DELETE FROM prompts_fts WHERE session_id=?", (meta.session_id,))

    db.execute(
        """INSERT INTO sessions
           (session_id, family, cwd, started_at, ended_at, message_count,
            prompt_count, first_prompt, jsonl_path, jsonl_mtime, indexed_at)
           VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
        (meta.session_id, family, meta.cwd, meta.started_at, meta.ended_at,
         meta.message_count, meta.prompt_count, meta.first_prompt,
         str(jsonl_path), mtime, datetime.now().isoformat()),
    )
    for p in result.prompts:
        db.execute(
            "INSERT INTO prompts_fts(session_id, timestamp, content) VALUES (?, ?, ?)",
            (p.session_id, p.timestamp, p.content),
        )
    db.commit()
    return len(result.errors)


def reindex(db: sqlite3.Connection, claude_root: Path, force_rebuild: bool = False) -> None:
    projects = claude_root / "projects"
    if not projects.exists():
        return

    lock_path = claude_root.parent / ".index.lock"  # safe: outside claude_root unless claude_root is home
    # Prefer a lock path that we know we can write to: sibling to DB
    lock_path = Path(db.execute("PRAGMA database_list").fetchone()["file"]).parent / ".index.lock"
    lock_path.parent.mkdir(parents=True, exist_ok=True)

    with open(lock_path, "w") as lock_fp:
        fcntl.flock(lock_fp, fcntl.LOCK_EX)
        try:
            run_id = _record_start(db)
            overrides = _load_overrides(db)

            on_disk: dict[str, float] = {}
            for f in projects.rglob("*.jsonl"):
                try:
                    on_disk[str(f)] = f.stat().st_mtime
                except OSError:
                    continue

            # Remove DB entries whose files vanished
            for row in db.execute("SELECT session_id, jsonl_path FROM sessions").fetchall():
                if row["jsonl_path"] not in on_disk:
                    db.execute("DELETE FROM sessions WHERE session_id=?", (row["session_id"],))
                    db.execute("DELETE FROM prompts_fts WHERE session_id=?", (row["session_id"],))
            db.commit()

            # Figure out cwds to resolve families (need to parse changed + new files)
            existing = {r["jsonl_path"]: r["jsonl_mtime"]
                        for r in db.execute("SELECT jsonl_path, jsonl_mtime FROM sessions").fetchall()}

            updated = 0
            total_errors = 0

            for jsonl_str, mtime in on_disk.items():
                if not force_rebuild and existing.get(jsonl_str) == mtime:
                    continue
                jsonl_path = Path(jsonl_str)
                # Parse once to get cwd, then decide family
                pr = parse_file(jsonl_path)
                family_map = detect([pr.session.cwd] if pr.session.cwd else [],
                                    overrides=overrides)
                family = family_map.get(pr.session.cwd, "unsorted") if pr.session.cwd else "unsorted"

                db.execute("DELETE FROM sessions WHERE jsonl_path=?", (jsonl_str,))
                db.execute("DELETE FROM prompts_fts WHERE session_id=?", (pr.session.session_id,))
                db.execute(
                    """INSERT INTO sessions
                       (session_id, family, cwd, started_at, ended_at, message_count,
                        prompt_count, first_prompt, jsonl_path, jsonl_mtime, indexed_at)
                       VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
                    (pr.session.session_id, family, pr.session.cwd,
                     pr.session.started_at, pr.session.ended_at,
                     pr.session.message_count, pr.session.prompt_count, pr.session.first_prompt,
                     jsonl_str, mtime, datetime.now().isoformat()),
                )
                for p in pr.prompts:
                    db.execute(
                        "INSERT INTO prompts_fts(session_id, timestamp, content) VALUES (?, ?, ?)",
                        (p.session_id, p.timestamp, p.content),
                    )
                db.commit()
                updated += 1
                total_errors += len(pr.errors)

            _record_end(db, run_id, seen=len(on_disk), updated=updated, errors=total_errors)
        finally:
            fcntl.flock(lock_fp, fcntl.LOCK_UN)
```

- [ ] **Step 4: Verify pass**

`.venv/bin/pytest tests/test_session_index.py -v` → 5 PASSED.

- [ ] **Step 5: Commit**

```bash
git add app/session_index.py tests/test_session_index.py
git -c user.email=jonathan10620@gmail.com -c user.name=jondev commit -m "feat(session_index): lazy incremental reindex with flock, idempotent upsert, deletion sync"
```

---

## Task 7: Web routes — `/sessions`, `/sessions/{id}`, `/reindex`

**Files:**
- Modify: `app/main.py` (add routes)
- Create: `app/templates/sessions.html`
- Create: `app/templates/session_detail.html`
- Create: `tests/test_sessions_routes.py`

- [ ] **Step 1: Write failing tests**

```python
# tests/test_sessions_routes.py
import json
from pathlib import Path
import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def client(tmp_path: Path, monkeypatch):
    data = tmp_path / "data"; data.mkdir()
    claude = tmp_path / ".claude"
    (claude / "projects" / "-Users-jondev-dev-socratink-prod-socratink-app").mkdir(parents=True)
    f = claude / "projects" / "-Users-jondev-dev-socratink-prod-socratink-app" / "uuid-abc.jsonl"
    ev = [
        {"sessionId": "uuid-abc", "type": "user", "timestamp": "2026-01-01T00:00:00Z",
         "cwd": "/Users/jondev/dev/socratink/prod/socratink-app",
         "message": {"role": "user", "content": "find the auth bug"}},
        {"sessionId": "uuid-abc", "type": "assistant", "timestamp": "2026-01-01T00:00:05Z",
         "message": {"role": "assistant", "content": "checking middleware"}},
    ]
    f.write_text("\n".join(json.dumps(e) for e in ev))
    monkeypatch.setenv("CLAUDE_TOOL_CLAUDE_ROOT", str(claude))
    monkeypatch.setenv("CLAUDE_TOOL_DATA_DIR", str(data))
    monkeypatch.setenv("CLAUDE_TOOL_DISABLE_REASONER", "1")
    from app.main import create_app
    return TestClient(create_app())


def test_sessions_list_renders(client):
    r = client.get("/sessions")
    assert r.status_code == 200
    assert "uuid-abc" in r.text
    assert "find the auth bug" in r.text


def test_sessions_search_filters(client):
    r = client.get("/sessions", params={"q": "auth"})
    assert r.status_code == 200
    assert "uuid-abc" in r.text
    r2 = client.get("/sessions", params={"q": "missingzzz"})
    assert "uuid-abc" not in r2.text


def test_sessions_family_filter(client):
    r = client.get("/sessions", params={"family": "socratink-app"})
    assert "uuid-abc" in r.text


def test_session_detail_renders(client):
    client.get("/sessions")  # trigger reindex
    r = client.get("/sessions/uuid-abc")
    assert r.status_code == 200
    assert "find the auth bug" in r.text


def test_reindex_post(client):
    r = client.post("/reindex")
    assert r.status_code == 200
```

- [ ] **Step 2: Verify fail**

`.venv/bin/pytest tests/test_sessions_routes.py -v` → FAIL.

- [ ] **Step 3: Add routes to `app/main.py`**

Add imports near the top (after existing app imports):

```python
from app.fts import search as fts_search
from app.session_index import reindex
from app.session_reader import stream as stream_events
```

Add these route definitions inside `create_app()`, just before `return app`:

```python
    @app.get("/sessions", response_class=HTMLResponse)
    def sessions_list(request: Request, q: str = "", family: str = "", limit: int = 50):
        conn = get_db()
        try:
            reindex(conn, claude_root)
        except Exception:
            pass  # rendering list even if reindex fails

        if q.strip():
            hits = fts_search(conn, q, family=family or None, limit=limit)
            rows = hits  # already has session_id, snippet, family, started_at
            mode = "search"
        else:
            sql = ["SELECT session_id, family, cwd, started_at, prompt_count, first_prompt FROM sessions"]
            params: list = []
            if family:
                sql.append("WHERE family=?"); params.append(family)
            sql.append("ORDER BY started_at DESC LIMIT ?"); params.append(limit)
            rows = [dict(r) for r in conn.execute(" ".join(sql), params).fetchall()]
            mode = "list"

        families = [r["family"] for r in conn.execute(
            "SELECT family, COUNT(*) AS n FROM sessions GROUP BY family ORDER BY n DESC"
        ).fetchall() if r["family"]]

        return templates.TemplateResponse(
            request, "sessions.html",
            {**_base_ctx(), "rows": rows, "mode": mode,
             "q": q, "family": family, "families": families},
        )

    @app.get("/sessions/{session_id}", response_class=HTMLResponse)
    def session_detail(request: Request, session_id: str, offset: int = 0, limit: int = 200):
        conn = get_db()
        row = conn.execute(
            "SELECT * FROM sessions WHERE session_id=?", (session_id,)
        ).fetchone()
        if not row:
            return HTMLResponse("(session not found)", status_code=404)
        events = stream_events(Path(row["jsonl_path"]), offset=offset, limit=limit)
        return templates.TemplateResponse(
            request, "session_detail.html",
            {**_base_ctx(), "session": dict(row), "events": events,
             "offset": offset, "limit": limit},
        )

    @app.post("/reindex", response_class=HTMLResponse)
    def reindex_now(request: Request, wipe: bool = False):
        conn = get_db()
        if wipe:
            conn.execute("DELETE FROM sessions")
            conn.execute("DELETE FROM prompts_fts")
            conn.commit()
        reindex(conn, claude_root, force_rebuild=wipe)
        return HTMLResponse("<div class='banner banner-success'><strong>REINDEXED</strong></div>")
```

- [ ] **Step 4: Create `app/templates/sessions.html`**

```html
{% extends "base.html" %}
{% block content %}
<h2>Sessions</h2>

<form method="get" action="/sessions" class="session-search">
  <input type="search" name="q" value="{{ q }}" placeholder="search prompts…" autocomplete="off">
  <select name="family">
    <option value="">all families</option>
    {% for f in families %}
      <option value="{{ f }}" {% if f == family %}selected{% endif %}>{{ f }}</option>
    {% endfor %}
  </select>
  <button type="submit">search</button>
  <button type="button" hx-post="/reindex" hx-target="#reindex-result" hx-swap="innerHTML"
          data-tip="rebuild index from scratch">↻ reindex</button>
</form>
<div id="reindex-result"></div>

<ul class="entry-list">
  {% for row in rows %}
    <li class="entry">
      <a class="tree-name tree-name-link" href="/sessions/{{ row.session_id }}">
        <strong>{{ row.first_prompt or row.snippet or row.session_id }}</strong>
      </a>
      <span class="meta">{{ row.family }} · {{ row.started_at }}{% if row.prompt_count is defined %} · {{ row.prompt_count }} prompts{% endif %}</span>
      {% if row.snippet %}<span class="purpose">{{ row.snippet }}</span>{% endif %}
    </li>
  {% endfor %}
</ul>

{% if not rows %}
  <p class="muted">No sessions match.</p>
{% endif %}
{% endblock %}
```

- [ ] **Step 5: Create `app/templates/session_detail.html`**

```html
{% extends "base.html" %}
{% block content %}
<nav class="breadcrumb">
  <a href="/sessions" class="crumb-back">◀ Sessions</a>
</nav>

<h2>Session {{ session.session_id }}</h2>
<section class="entry-detail">
  <h3>Metadata</h3>
  <dl class="metadata-grid">
    <dt>Family</dt>       <dd>{{ session.family }}</dd>
    <dt>Cwd</dt>          <dd><code>{{ session.cwd or "—" }}</code></dd>
    <dt>Started</dt>      <dd>{{ session.started_at or "—" }}</dd>
    <dt>Ended</dt>        <dd>{{ session.ended_at or "—" }}</dd>
    <dt>Messages</dt>     <dd>{{ session.message_count }}</dd>
    <dt>Prompts</dt>      <dd>{{ session.prompt_count }}</dd>
    <dt>Jsonl path</dt>   <dd><code>{{ session.jsonl_path }}</code></dd>
  </dl>
</section>

<section class="entry-detail">
  <h3>Transcript ({{ offset }} – {{ offset + events|length }} of {{ session.message_count }})</h3>
  <ul class="transcript">
    {% for e in events %}
      <li class="evt evt-{{ e.kind }}">
        <span class="evt-kind">{{ e.kind }}</span>
        <span class="evt-ts">{{ e.timestamp }}</span>
        <pre class="preview">{{ e.body_preview }}</pre>
      </li>
    {% endfor %}
  </ul>
  {% if offset + events|length < session.message_count %}
    <a class="crumb-back" href="/sessions/{{ session.session_id }}?offset={{ offset + limit }}&limit={{ limit }}">next {{ limit }} ▶</a>
  {% endif %}
</section>
{% endblock %}
```

- [ ] **Step 6: Verify pass**

`.venv/bin/pytest tests/test_sessions_routes.py -v`
Expected: 5 PASSED.

Full suite green.

- [ ] **Step 7: Commit**

```bash
git add app/main.py app/templates/sessions.html app/templates/session_detail.html tests/test_sessions_routes.py
git -c user.email=jonathan10620@gmail.com -c user.name=jondev commit -m "feat(web): /sessions list + search, /sessions/{id} detail, /reindex"
```

---

## Task 8: Dashboard widget on home

**Files:**
- Modify: `app/main.py` — extend `home()` context
- Modify: `app/templates/home.html` — add dashboard section

- [ ] **Step 1: Write failing test**

Append to `tests/test_sessions_routes.py`:

```python
def test_home_shows_dashboard(client):
    client.get("/sessions")  # trigger reindex
    r = client.get("/")
    assert r.status_code == 200
    assert "Sessions" in r.text
    assert "1" in r.text  # session count
```

- [ ] **Step 2: Verify fail**

`.venv/bin/pytest tests/test_sessions_routes.py::test_home_shows_dashboard -v` → FAIL.

- [ ] **Step 3: Modify `home()` in `app/main.py`**

Replace the existing `home()` handler body with:

```python
    @app.get("/", response_class=HTMLResponse)
    def home(request: Request):
        conn = get_db()
        try:
            reindex(conn, claude_root)
        except Exception:
            pass
        last_scan = conn.execute(
            "SELECT id, started_at FROM scans ORDER BY id DESC LIMIT 1"
        ).fetchone()
        session_count = conn.execute("SELECT COUNT(*) FROM sessions").fetchone()[0]
        recent_sessions = [dict(r) for r in conn.execute(
            "SELECT session_id, family, started_at, first_prompt "
            "FROM sessions ORDER BY started_at DESC LIMIT 5"
        ).fetchall()]
        top_families = [dict(r) for r in conn.execute(
            "SELECT family, COUNT(*) AS n FROM sessions "
            "WHERE family IS NOT NULL GROUP BY family ORDER BY n DESC LIMIT 5"
        ).fetchall()]
        last_index = conn.execute(
            "SELECT * FROM index_runs ORDER BY id DESC LIMIT 1"
        ).fetchone()
        return templates.TemplateResponse(
            request, "home.html",
            {**_base_ctx(), "last_scan": last_scan,
             "session_count": session_count,
             "recent_sessions": recent_sessions,
             "top_families": top_families,
             "last_index": dict(last_index) if last_index else None},
        )
```

- [ ] **Step 4: Extend `app/templates/home.html`**

Append before `{% endblock %}`:

```html
<section class="entry-detail">
  <h3>Sessions</h3>
  <p class="muted">
    {{ session_count }} total
    {% if last_index %}
      · last index {{ last_index.finished_at or "running…" }}
      {% if last_index.error_count %} · <span class="status-kill_candidate">{{ last_index.error_count }} errors</span>{% endif %}
    {% endif %}
    · <a href="/sessions">browse all →</a>
  </p>

  <h4>Recent</h4>
  <ul class="entry-list">
    {% for s in recent_sessions %}
      <li class="entry">
        <a class="tree-name tree-name-link" href="/sessions/{{ s.session_id }}">
          <strong>{{ s.first_prompt or s.session_id }}</strong>
        </a>
        <span class="meta">{{ s.family }} · {{ s.started_at }}</span>
      </li>
    {% endfor %}
  </ul>

  <h4>Top families</h4>
  <ul class="entry-list">
    {% for f in top_families %}
      <li class="entry">
        <a class="tree-name tree-name-link" href="/sessions?family={{ f.family }}">
          <strong>{{ f.family }}</strong>
        </a>
        <span class="meta">{{ f.n }} sessions</span>
      </li>
    {% endfor %}
  </ul>
</section>
```

- [ ] **Step 5: Verify pass**

`.venv/bin/pytest tests/test_sessions_routes.py -v` → 6 PASSED.

Full suite: `.venv/bin/pytest -v` → all green.

- [ ] **Step 6: Commit**

```bash
git add app/main.py app/templates/home.html tests/test_sessions_routes.py
git -c user.email=jonathan10620@gmail.com -c user.name=jondev commit -m "feat(dashboard): home shows session count, recent, top families, last index"
```

---

## Task 9: Families CRUD + redact

**Files:**
- Modify: `app/main.py` — add 2 routes
- Create: `app/templates/families.html`
- Create: `tests/test_families_routes.py`

- [ ] **Step 1: Write failing tests**

```python
# tests/test_families_routes.py
from pathlib import Path
import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def client(tmp_path: Path, monkeypatch):
    data = tmp_path / "data"; data.mkdir()
    claude = tmp_path / ".claude"
    (claude / "projects").mkdir(parents=True)
    monkeypatch.setenv("CLAUDE_TOOL_CLAUDE_ROOT", str(claude))
    monkeypatch.setenv("CLAUDE_TOOL_DATA_DIR", str(data))
    monkeypatch.setenv("CLAUDE_TOOL_DISABLE_REASONER", "1")
    from app.main import create_app
    return TestClient(create_app())


def test_families_page_renders(client):
    r = client.get("/families")
    assert r.status_code == 200
    assert "Families" in r.text


def test_post_family_creates_override(client, tmp_path):
    r = client.post("/families", data={"name": "socratink", "path_prefix": "/Users/jondev/dev/socratink"})
    assert r.status_code == 200
    # DB reflects
    import sqlite3
    conn = sqlite3.connect(tmp_path / "data" / "workspace.db")
    row = conn.execute("SELECT * FROM families WHERE name='socratink'").fetchone()
    assert row is not None


def test_post_redact_deletes_matching(client, tmp_path):
    # Seed fake prompt
    import sqlite3
    conn = sqlite3.connect(tmp_path / "data" / "workspace.db")
    conn.execute("INSERT INTO sessions(session_id, jsonl_path, jsonl_mtime, indexed_at) VALUES ('s1','/x', 0.0, 'now')")
    conn.execute("INSERT INTO prompts_fts(session_id, timestamp, content) VALUES ('s1','t','my-secret-token-abc')")
    conn.commit()
    r = client.post("/sessions/redact", data={"pattern": "%secret%"})
    assert r.status_code == 200
    n = conn.execute("SELECT COUNT(*) FROM prompts_fts WHERE content LIKE '%secret%'").fetchone()[0]
    assert n == 0
```

- [ ] **Step 2: Verify fail**

`.venv/bin/pytest tests/test_families_routes.py -v` → FAIL.

- [ ] **Step 3: Add routes to `app/main.py`**

Inside `create_app()`, before `return app`:

```python
    @app.get("/families", response_class=HTMLResponse)
    def families_page(request: Request):
        conn = get_db()
        rows = [dict(r) for r in conn.execute(
            "SELECT name, path_prefix, is_override FROM families ORDER BY path_prefix DESC"
        ).fetchall()]
        counts = {r["family"]: r["n"] for r in conn.execute(
            "SELECT family, COUNT(*) AS n FROM sessions GROUP BY family"
        ).fetchall()}
        return templates.TemplateResponse(
            request, "families.html",
            {**_base_ctx(), "families": rows, "counts": counts},
        )

    @app.post("/families", response_class=HTMLResponse)
    async def families_upsert(request: Request):
        form = await request.form()
        name = (form.get("name") or "").strip()
        prefix = (form.get("path_prefix") or "").strip()
        if not name or not prefix:
            return HTMLResponse("<div class='banner banner-warn'>name + path_prefix required</div>", status_code=400)
        conn = get_db()
        conn.execute(
            "INSERT OR REPLACE INTO families(name, path_prefix, is_override) VALUES (?,?,1)",
            (name, prefix),
        )
        conn.commit()
        return HTMLResponse(f"<div class='banner banner-success'><strong>SAVED</strong> {name} → {prefix}</div>")

    @app.post("/sessions/redact", response_class=HTMLResponse)
    async def sessions_redact(request: Request):
        form = await request.form()
        pattern = (form.get("pattern") or "").strip()
        if not pattern:
            return HTMLResponse("<div class='banner banner-warn'>pattern required</div>", status_code=400)
        conn = get_db()
        cur = conn.execute("DELETE FROM prompts_fts WHERE content LIKE ?", (pattern,))
        conn.commit()
        return HTMLResponse(f"<div class='banner banner-success'><strong>REDACTED</strong> {cur.rowcount} rows</div>")
```

- [ ] **Step 4: Create `app/templates/families.html`**

```html
{% extends "base.html" %}
{% block content %}
<h2>Families</h2>

<section class="entry-detail">
  <h3>Overrides</h3>
  <ul class="entry-list">
    {% for f in families %}
      <li class="entry">
        <strong>{{ f.name }}</strong>
        <span class="meta">{{ f.path_prefix }} · {{ counts.get(f.name, 0) }} sessions</span>
      </li>
    {% endfor %}
  </ul>

  <h3>Add override</h3>
  <form method="post" action="/families">
    <label>name: <input type="text" name="name" required></label>
    <label>path prefix: <input type="text" name="path_prefix" required size="50"></label>
    <button type="submit">save</button>
  </form>
</section>

<section class="entry-detail">
  <h3>Redact prompts (privacy)</h3>
  <p class="muted">SQL LIKE pattern. Use % as wildcard. Deletes matching FTS rows permanently.</p>
  <form method="post" action="/sessions/redact">
    <input type="text" name="pattern" placeholder="%sk_live_%" size="40">
    <button type="submit" class="danger-btn"
            onclick="return confirm('Delete all prompts containing this pattern?')">redact</button>
  </form>
</section>
{% endblock %}
```

- [ ] **Step 5: Verify pass**

`.venv/bin/pytest tests/test_families_routes.py -v` → 3 PASSED.

Full suite green.

- [ ] **Step 6: Commit**

```bash
git add app/main.py app/templates/families.html tests/test_families_routes.py
git -c user.email=jonathan10620@gmail.com -c user.name=jondev commit -m "feat(families): CRUD overrides + redact endpoint for prompt-content purge"
```

---

## Task 10: Integration test

**Files:**
- Create: `tests/test_p2_integration.py`

- [ ] **Step 1: Write the integration test**

```python
# tests/test_p2_integration.py
import json
from pathlib import Path
import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def client(tmp_path: Path, monkeypatch):
    data = tmp_path / "data"; data.mkdir()
    claude = tmp_path / ".claude"
    projects = claude / "projects" / "-Users-jondev-dev-socratink-prod-socratink-app"
    projects.mkdir(parents=True)

    def write(sid, cwd, prompts):
        f = projects / f"{sid}.jsonl"
        events = [
            {"sessionId": sid, "type": "user", "timestamp": f"2026-01-0{i+1}T00:00:00Z",
             "cwd": cwd, "message": {"role": "user", "content": p}}
            for i, p in enumerate(prompts)
        ]
        f.write_text("\n".join(json.dumps(e) for e in events))

    write("uuid-1", "/Users/jondev/dev/socratink/prod/socratink-app",
          ["auth bug in middleware", "add stripe webhook"])
    write("uuid-2", "/Users/jondev/dev/socratink/prod/socratink-app",
          ["refactor learning graph"])

    # Different family
    other = claude / "projects" / "-Users-jondev-dev-other"
    other.mkdir()
    (other / "uuid-3.jsonl").write_text(json.dumps(
        {"sessionId": "uuid-3", "type": "user", "timestamp": "2026-01-04T00:00:00Z",
         "cwd": "/Users/jondev/dev/other",
         "message": {"role": "user", "content": "unrelated work"}}
    ))

    monkeypatch.setenv("CLAUDE_TOOL_CLAUDE_ROOT", str(claude))
    monkeypatch.setenv("CLAUDE_TOOL_DATA_DIR", str(data))
    monkeypatch.setenv("CLAUDE_TOOL_DISABLE_REASONER", "1")
    from app.main import create_app
    return TestClient(create_app())


def test_end_to_end_session_index(client):
    # 1. Visit home → reindexes lazily
    r = client.get("/")
    assert r.status_code == 200

    # 2. Dashboard shows 3 sessions
    assert "3" in r.text or "socratink-app" in r.text

    # 3. Search for "stripe" finds uuid-1
    r = client.get("/sessions", params={"q": "stripe"})
    assert "uuid-1" in r.text
    assert "uuid-2" not in r.text

    # 4. Family filter to socratink-app excludes uuid-3
    r = client.get("/sessions", params={"family": "socratink-app"})
    assert "uuid-1" in r.text and "uuid-2" in r.text
    assert "uuid-3" not in r.text

    # 5. Detail page streams transcript
    r = client.get("/sessions/uuid-1")
    assert "auth bug" in r.text

    # 6. Reindex is idempotent
    r = client.post("/reindex")
    assert r.status_code == 200
```

- [ ] **Step 2: Verify pass**

`.venv/bin/pytest tests/test_p2_integration.py -v`
Expected: 1 PASSED.

Full suite: `.venv/bin/pytest -v` — all green across P1 + P2.

- [ ] **Step 3: Commit**

```bash
git add tests/test_p2_integration.py
git -c user.email=jonathan10620@gmail.com -c user.name=jondev commit -m "test(p2): end-to-end integration for session index, search, detail"
```

---

## Task 11: Manual verify + README

**Files:**
- Modify: `README.md`

- [ ] **Step 1: Run against real data**

```
gbc
```

In browser:
- Home page should show `Sessions` section with total count (~374), recent sessions, top families.
- Click `browse all →` → `/sessions` shows timeline.
- Type `socratink` in search box → filtered list.
- Click a session → transcript streams.
- Click a family link → scoped list.

Confirm nothing crashes; check uvicorn logs for parse errors (non-fatal, logged to `index_runs`).

- [ ] **Step 2: Expand README.md**

Append to README:

```markdown
## P2: Session Index

- **/sessions** — searchable timeline of all Claude Code sessions. Full-text search on user prompts only (tool outputs + assistant replies are not indexed).
- **/sessions/{id}** — transcript view; streams the jsonl off disk, paginated 200 events/page.
- **/families** — auto-detected project families with override UI.
- **/reindex** — force rebuild.

Index refreshes lazily when you visit `/` or `/sessions`; compares jsonl mtimes, re-ingests only changed files.

### Privacy

Your session prompts may contain secrets you pasted into Claude Code conversations. The index is stored locally in `data/workspace.db`. Nothing is sent to an LLM. Use `/families` → "Redact prompts" with a SQL LIKE pattern to delete matching rows (e.g., `%sk_live_%`).
```

- [ ] **Step 3: Commit**

```bash
git add README.md
git -c user.email=jonathan10620@gmail.com -c user.name=jondev commit -m "docs(p2): README section covering /sessions, privacy, reindex"
```

---

## Done Criteria

- All P2 unit tests pass (events, families, fts, session_reader, session_index, sessions_routes, families_routes).
- Integration test passes end-to-end.
- Manual smoke against real `~/.claude/projects/` — dashboard loads, search finds known prompts, detail view streams without crash.
- `data/workspace.db` gains `sessions`, `prompts_fts`, `families`, `index_runs` tables and populates correctly.
- `~/.claude/projects/` and `~/.claude/sessions/` files are NEVER written to.

After P2 ships: brainstorm P3 (insight extraction) as its own spec.
