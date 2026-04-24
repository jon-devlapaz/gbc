# Claude Workspace Tool — P2: Session Index + Search + Dashboard

**Date:** 2026-04-24
**Status:** Design approved, pending spec review
**Owner:** Jon
**Scope:** Phase 2 of the three-phase umbrella project (P1 shipped; P3 pending)

## Umbrella Context

P2 is the **memory** layer. Sessions evaporate today: 374 jsonl files totalling ~190 MB live under `~/.claude/projects/`, but there is no search, no timeline, no project-family view. P2 adds a searchable, browsable index so any prior session (prompts, transcripts, tool use) is findable and resumable.

P1 shipped the **inventory** layer (workspace audit + skill editor). P2 adds memory. P3 (future) will add **evolution** (insight extraction).

## Goals (P2)

1. Global dashboard widget on the home page showing: total sessions, recent sessions across all projects, top project families by activity, last index run status.
2. Per-family drill-down with scoped timeline + stats.
3. Full-text search over **user prompts only** across all 374 jsonl files.
4. Per-session detail view that streams the jsonl off disk and renders a readable transcript (filtered by event kind).
5. Lazy incremental reindex — no daemon, no fsevents; page loads trigger reindex of changed files.
6. Family auto-detection with user override UI.

## Non-Goals (P2)

- Insight extraction, pattern mining, skill authoring. Those are P3.
- Editing session content. P2 is **read-only** over the session corpus.
- Resume into a live Claude Code session. Out of scope; future if valuable.
- Indexing assistant replies or tool outputs (FTS is user prompts only — scope trim).
- Analytics dashboards beyond counts (no charts, no graphs — YAGNI).

## Safety Model (new P2 invariants)

P2 intentionally pierces the P1 read-blocklist for `~/.claude/projects/` and `~/.claude/sessions/`. Three new invariants:

1. **Read-only corpus.** P2 never writes to, moves, or deletes any file under `~/.claude/projects/` or `~/.claude/sessions/`. All ingest is parse + project into SQLite.
2. **No prompt content leaves the local DB.** Indexed prompt text is never sent to an LLM (no reasoner call on prompts). Dashboard/UI renders snippets locally.
3. **User-visible privacy surface.** UI banner on first /sessions visit: "Session prompts may contain pasted secrets. Index stays local in `data/workspace.db`." A `POST /sessions/redact` button deletes matching FTS rows on demand (SQL `DELETE … WHERE content LIKE ?`).

## Architecture

**App location:** existing repo `~/dev/claude-workspace-tool/`. No new repo.

**Stack additions:** none. FastAPI + Jinja2 + HTMX + SQLite (FTS5) — already in place. Reuses `workspace.db`.

**Run:** same `gbc` shell function.

**Style:** Zen of Python. Explicit > implicit. Each module one purpose. Defense at boundaries.

## Components

Six modules, each one file, each testable alone.

### 1. `app/events.py`

JSONL parser.

`parse_file(path: Path) -> ParseResult`

```python
@dataclass
class ParseResult:
    session: SessionMeta
    prompts: list[PromptRow]
    errors: list[ParseError]

@dataclass
class SessionMeta:
    session_id: str
    cwd: str | None
    started_at: str | None
    ended_at: str | None
    message_count: int
    prompt_count: int
    first_prompt: str | None   # truncated to 200 chars

@dataclass
class PromptRow:
    session_id: str
    timestamp: str
    content: str               # clamped to MAX_PROMPT_BYTES = 256_000

@dataclass
class ParseError:
    line_number: int
    reason: str
```

**Classification per line:**

A line is a **prompt** iff:
- `type == "user"` AND
- `isMeta != True` AND
- `message.role == "user"` AND
- `message.content` is a string, OR an array of objects where no element is `{"type":"tool_result",...}`.

Everything else (tool_result, tool_use, assistant, permission-mode, attachment, file-history-snapshot, last-prompt, queue-operation, progress, pr-link, worktree-state, summary) is treated as metadata. Counted for `message_count`, not indexed.

**Malformed lines:** append to `errors`, continue. Never raise.

Pure read.

### 2. `app/families.py`

Project-family detection.

`detect(cwds: Iterable[str], overrides: list[FamilyOverride]) -> dict[str, str]`

- Worktree collapse: any path containing `/.claude/worktrees/<anything>` or `/.worktrees/<anything>` collapses to the parent directory. So `/Users/jondev/dev/socratink/prod/socratink-app/.claude/worktrees/determined-bhaskara` → `/Users/jondev/dev/socratink/prod/socratink-app`.
- Segment-aware prefix clustering: collect collapsed cwds, for each find the longest common path (≥3 segments) shared by ≥2 others; that becomes a family name (last segment as human name).
- Overrides beat auto: longest matching `path_prefix` wins.
- Stragglers (no shared prefix) → family `"unsorted"`.

Pure function, no I/O.

### 3. `app/session_index.py`

Orchestrator.

`reindex(db, claude_root)` — called lazily from `/` and `/sessions`; also from `POST /reindex`.

Steps:
1. Acquire `fcntl.flock(LOCK_EX)` on `data/.index.lock`.
2. `INSERT INTO index_runs (started_at) VALUES (...)` → get `run_id`.
3. Walk `<claude_root>/projects/**/*.jsonl`.
4. For each file, compare `st.st_mtime` vs `sessions.jsonl_mtime` (subsecond float). If ≤, skip.
5. If changed: `events.parse_file()` → `BEGIN IMMEDIATE` transaction → `DELETE FROM sessions WHERE jsonl_path=?` (cascades via separate `DELETE FROM prompts_fts WHERE session_id=?`) → `INSERT INTO sessions` + bulk `INSERT INTO prompts_fts`. Commit.
6. If parse returned errors, increment `index_runs.error_count`; store first 5 errors in a `index_errors` detail table (NOT per-file, global per run) — skip for MVP if schema too heavy.
7. Files in DB but missing on disk: `DELETE` the row (keeps index in sync with deletions).
8. `UPDATE index_runs SET finished_at, files_seen, files_updated, error_count WHERE id=run_id`.
9. Release lock.

Idempotent. Safe to run concurrently at the DB level (advisory lock ensures one at a time).

### 4. `app/fts.py`

FTS5 setup + safe query builder.

`escape_query(user_input: str) -> str` — quotes every token, handles double-quoted phrases user typed, strips `NEAR(`, `*`, `"` (un-paired), leading/trailing `-`. Returns a string safe for `MATCH`.

`search(db, query: str, family: str | None, limit: int = 50) -> list[SearchHit]` — runs the safe query against `prompts_fts` joined to `sessions` (optional family filter). Returns session_id, timestamp, snippet (clamped to 160 chars via `snippet()`).

### 5. `app/session_reader.py`

`stream(jsonl_path: Path, offset: int = 0, limit: int = 200) -> list[EventView]` — opens file, reads lines `offset..offset+limit`, parses each, returns filtered views with `kind`, `timestamp`, `body_preview`. Never loads whole file. Used by `/sessions/{id}` detail.

### 6. Web layer

New routes on existing FastAPI app:

- `GET /` — existing home gains a **dashboard** panel below the Scan button: total sessions indexed, last index run timestamp, 3 most-recent sessions across all families, top 5 families by session count (each a link to `/sessions?family=NAME`).
- `GET /sessions` — filterable searchable timeline. Query params: `family`, `q` (FTS search), `limit`, `offset`. Calls `reindex()` lazily first. Returns HTML with list of session rows, each a link to `/sessions/{id}`.
- `GET /sessions/{session_id}` — detail view. Calls `session_reader.stream()`. Paginates 200 events/page.
- `GET /families` — list families, inline form per row to rename or override `path_prefix`.
- `POST /families` — form `{name, path_prefix}` → `INSERT OR REPLACE` with `is_override=1`.
- `POST /reindex` — force full reindex (bypasses mtime skip).
- `POST /sessions/redact` — form `{pattern}` → `DELETE FROM prompts_fts WHERE content LIKE ?`.

## Data Flow

**Reindex:** page hit → `session_index.reindex(db)` → lock → walk fs → per-changed-file parse + commit → unlock.

**Search:** user types `q` → `fts.escape_query(q)` → SQL `SELECT ... MATCH ?` → render list of `(session_id, timestamp, snippet)`.

**Detail:** `/sessions/{id}` → `SELECT jsonl_path FROM sessions WHERE session_id=?` → `session_reader.stream(path)` → render paginated.

## Schema (SQLite additions)

```sql
CREATE TABLE sessions (
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
CREATE INDEX sessions_family     ON sessions(family);
CREATE INDEX sessions_started_at ON sessions(started_at DESC);

CREATE VIRTUAL TABLE prompts_fts USING fts5(
  session_id UNINDEXED,
  timestamp UNINDEXED,
  content,
  tokenize = "unicode61"
);

CREATE TABLE families (
  name TEXT PRIMARY KEY,
  path_prefix TEXT NOT NULL,
  is_override INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE index_runs (
  id INTEGER PRIMARY KEY,
  started_at TEXT NOT NULL,
  finished_at TEXT,
  files_seen INTEGER,
  files_updated INTEGER,
  error_count INTEGER
);
```

No foreign keys between sessions and prompts_fts (FTS5 virtual tables don't participate in FKs); deletion by `session_id` is a second statement.

## Error Handling + Edge Cases

Cross-cutting (see per-module sections for local):

- Parse errors → logged, line skipped, run continues.
- Missing `cwd` across all events → `cwd = NULL`, family = `unsorted`.
- Zero prompts → session row created, no FTS rows. UI renders "(no user prompts)".
- File disappears mid-walk → caught at parse time, removed from DB on next pass.
- Prompt > 256 KB → truncated with `[…truncated]` suffix.
- FTS query is empty / malformed → returns 0 results, no error.
- Concurrent reindex request while one running → blocks on flock, returns when done.
- Prompts may contain secrets → UI banner + `POST /sessions/redact` for targeted purge; full-wipe via `POST /reindex` with `wipe=true` query.

## Testing

Stack: existing `pytest` + `pytest-asyncio`. `tmp_path` fixtures.

Per module:

- **events** — fixtures with: happy jsonl, pure tool_result jsonl, missing-field jsonl, malformed-line jsonl, string-content prompt, array-content prompt, isMeta=true prompt. Assert prompt count, field extraction, error capture.
- **families** — parametrized: worktree paths (socratink-app + its `.claude/worktrees/*`), sibling repos (`socratink-app` vs `socratink-landing`), non-project paths. Assert worktree collapse, longest-override-wins, unsorted fallback.
- **session_index.reindex** — real tmp fs tree mimicking `~/.claude/projects/`. Happy first run, no-op second run (mtimes match), mtime-bump triggers re-ingest, deleted file removed, concurrent flock blocks.
- **fts.escape_query** — parametrize: `-hello`, `"foo bar"`, `NEAR(a b)`, `/Users/jondev/x`, UUIDs, emojis. Round-trip: escaped string passes FTS5 `MATCH` without error, returns expected rows.
- **session_reader** — fixture jsonl with 1000 events, assert `stream(offset=500, limit=10)` returns events 500-509, memory doesn't load whole file.
- **Web routes** — `/`, `/sessions`, `/sessions/{id}`, `/reindex`, `/families`, `/sessions/redact`. Dry-run tests that reindex into a tmp claude_root, search returns expected hits.

Integration: end-to-end — write 3 fake jsonls into `tmp_path/.claude/projects/...` → hit `/` → assert dashboard shows 3 sessions → search for known prompt text → click session → assert transcript streams.

Coverage target: ~85% on `events.py` + `fts.py` (safety + correctness critical). ~60% elsewhere.

## Open Questions

None at spec time.

## What This Spec Is Not

- Not an implementation plan. That comes next via the writing-plans skill.
- Not a commitment to P3 (insight extraction). Separate decision after P2 ships.
