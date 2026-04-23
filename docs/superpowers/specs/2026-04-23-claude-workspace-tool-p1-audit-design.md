# Claude Workspace Tool — P1: Workspace Audit + Cleanup

**Date:** 2026-04-23
**Status:** Design approved, pending spec review
**Owner:** Jon
**Scope:** Phase 1 of a three-phase umbrella project

## Umbrella Project Context

The umbrella goal is to make Jon a more productive Claude Code power user by giving him persistent, local tooling for three recurring pains:

- **P1 — Workspace audit + cleanup (this spec).** Map `~/.claude/`, kill dead dirs, produce a canonical taxonomy.
- **P2 — Session index + search (future).** Build an index over `sessions/` and `projects/` transcripts so past work is findable and resumable.
- **P3 — Insight extraction (future).** Extract durable lessons, patterns, and TODOs from sessions into a queryable knowledge store.

All three phases share a single long-lived localhost app. P1 establishes the stack, schema, and UI shell the later phases extend. Each phase ships independently, with its own spec and implementation plan.

## Goals (P1)

1. Produce a complete, human-readable taxonomy of every top-level dir under `~/.claude/` — what it is, who owns it (harness vs user), whether it's still active.
2. Archive and delete dead dirs safely. Never touch harness-owned paths.
3. Leave Jon with a localhost web UI that is the durable home for P2 and P3.

## Non-Goals (P1)

- Search, index, or insight extraction. Those are P2/P3.
- Auditing anything outside `~/.claude/` (e.g., `~/dev/`).
- Auto-scheduled re-runs. Manual trigger only.
- Multi-user. Single-user local tool.
- Cloud deployment.

## Architecture

**App location:** `~/dev/claude-workspace-tool/` (own repo, git-tracked, outside `.claude/`).

**Stack:**
- Backend: FastAPI, Python 3.11+, stdlib + `pydantic`.
- Frontend: Jinja2 server-rendered templates + HTMX for interactivity + one small CSS file.
- Persistence: SQLite at `~/dev/claude-workspace-tool/data/workspace.db`. Plain markdown export for human reads at `~/dev/claude-workspace-tool/data/taxonomy.md`.
- LLM: Anthropic SDK, `claude-haiku-4-5` for reasoner calls. API key from env.

**Run:** `uvicorn app:main --port 7878` → browser at `http://localhost:7878`. No build step.

**Style:** Zen of Python. Explicit over implicit, flat over nested, simple over clever. Each module has one clear purpose.

## Components

Five modules, each in its own file, each testable alone.

### 1. `scanner.py`

Walks `~/.claude/` deep. Emits one `Entry(path, kind, size_bytes, mtime, file_count, sample_files)` per top-level entry, where `kind ∈ {dir, file}`. Top-level files (e.g., `history.jsonl`, `settings.json`, `RTK.md`) are included with `file_count=1` and empty `sample_files`. Dirs are walked deep to compute `size_bytes`, `file_count`, and up to 5 representative filenames.

Pure read, no side effects.

Behavior:
- Permission-denied subdir → log, skip, continue. Never crash.
- Symlinks → resolve once, detect cycles via visited-set, do not follow links leaving `~/.claude/`.
- Samples skip `.DS_Store`, `*.lock`.

### 2. `classifier.py`

Rule engine. Input `Entry`, output `Verdict(status, reason)` where `status ∈ {harness_protected, likely_dead, likely_active, needs_review}`.

Rules (first match wins):
- `harness_protected`: name match against hardcoded allowlist covering both files and dirs — `sessions/`, `projects/`, `history.jsonl`, `settings.json`, `settings.local.json`, `hooks/`, `ide/`, `shell-snapshots/`, `session-env/`, `mcp.json`, `statusline-command.sh`, `cache/`, `cowork_plugins/`, `cowork_settings.json`, `telemetry/`, `usage-data/`, `plugins/`, `commands/`, `agents/`, `skills/`.
- `likely_dead`: mtime > 30 days AND name matches `*-archive/`, `*-backups/`, `paste-cache/`, `debug/`, `downloads/`.
- `likely_active`: mtime < 7 days.
- `needs_review`: everything else. Unknown name + mtime > 90 days + < 10 files → still `needs_review`, never auto-dead. Err conservative.

Pure function. No I/O.

### 3. `reasoner.py`

For entries flagged `needs_review` or `likely_dead`: reads up to 5 sample files (first 50 lines each), sends a small prompt to `claude-haiku-4-5`, returns `(purpose, recommendation)`.

- Purpose: 1-2 sentence guess at what the dir is for.
- Recommendation: `keep` or `kill` with a reason.

Cache: keyed by `sha256(path + mtime_iso)`. Repeated scans with unchanged dirs skip the LLM call.

Failure modes:
- Network or rate-limit error → cache miss returns `purpose="unknown"`, `recommendation="manual review"`. UI surfaces yellow; user decides.
- Cost cap: max 50 live reasoner calls per scan. Excess rows left `purpose=NULL` and deferred to next scan.

### 4. `executor.py`

Given an approved kill-list:

1. `tar czf ~/.claude-archive-YYYYMMDD-HHMM.tar.gz <paths>`.
2. Integrity check: `tar tzf <archive>` must succeed. On failure, abort — no delete happens.
3. Per-path guards before `rm -rf`:
   - `os.path.realpath(p)` must start with `os.path.expanduser("~/.claude/")`. Else refuse.
   - Path must not be in harness allowlist. Refuse even if user approved. Safety over autonomy.
4. Each delete writes an `actions` row *before* the `rm`. Paper trail is complete even if `rm` crashes.
5. Dry-run mode default-on in UI. User explicitly toggles "armed" before the execute button enables.

### 5. `taxonomy.py`

After cleanup finalizes: reads the most recent scan's surviving rows, writes a markdown doc with one section per dir (name, purpose, owner, status, last mtime, approx size).

Atomic write: `taxonomy.md.tmp` → `rename` to `taxonomy.md`. No half-written file visible.

### Web layer

FastAPI routes wrap the five modules. HTMX templates:

- `GET /` — home. Shows last scan summary + "New scan" button.
- `POST /scan` — triggers scanner, streams progress via HTMX SSE.
- `POST /classify` — runs classifier on latest scan rows.
- `POST /reason` — runs reasoner on `needs_review` + `likely_dead` rows.
- `GET /review` — grouped table: harness_protected (collapsed), likely_active (collapsed), needs_review (expanded with reasoning), likely_dead (expanded, pre-checked).
- `POST /execute` — accepts kill-list, runs executor, returns archive path and action log.
- `POST /finalize` — runs taxonomy writer.

Concurrent scans blocked at the DB layer via `scans.finished_at IS NULL` check.

## Data Flow

1. User clicks **Scan** → `POST /scan` → `scanner.walk()` → rows inserted into `entries`.
2. `POST /classify` → `classifier.verdict()` per row → update `status`, `reason`.
3. `POST /reason` → `reasoner.analyze()` per flagged row → update `purpose`, `recommendation`.
4. User reviews grouped table, toggles checkboxes → `POST /execute` with kill-list → `executor.archive_and_delete()` → `actions` rows written, archive path returned.
5. `POST /finalize` → `taxonomy.write()` → markdown emitted, scan row marked done.

## Schema (SQLite)

```sql
CREATE TABLE scans (
  id INTEGER PRIMARY KEY,
  started_at TEXT NOT NULL,
  finished_at TEXT
);

CREATE TABLE entries (
  id INTEGER PRIMARY KEY,
  scan_id INTEGER NOT NULL REFERENCES scans(id),
  path TEXT NOT NULL,
  kind TEXT NOT NULL,      -- dir | file
  size_bytes INTEGER,
  mtime TEXT,
  file_count INTEGER,
  sample_files TEXT,       -- JSON array
  status TEXT,             -- harness_protected | likely_dead | likely_active | needs_review
  reason TEXT,             -- rule that fired
  purpose TEXT,            -- LLM guess
  recommendation TEXT,     -- LLM keep/kill + reason
  user_decision TEXT       -- keep | kill | NULL
);

CREATE TABLE actions (
  id INTEGER PRIMARY KEY,
  ts TEXT NOT NULL,
  action TEXT NOT NULL,    -- archive | delete
  path TEXT NOT NULL,
  archive_path TEXT,
  status TEXT              -- success | failed
);

CREATE TABLE reasoner_cache (
  path_mtime_hash TEXT PRIMARY KEY,
  purpose TEXT,
  recommendation TEXT,
  created_at TEXT
);
```

## Error Handling + Edge Cases

See per-module "Behavior" and "Failure modes" above. Cross-cutting:

- All filesystem operations use `pathlib.Path`, never string concat.
- All destructive paths pass through the realpath + allowlist guard in `executor.py`. Hardcoded, not config-driven.
- Long scans stream via HTMX SSE so the user sees progress on `file-history/` and similar large dirs.
- Atomic writes for `taxonomy.md`.
- Actions row written before each destructive call.

## Testing

Stack: `pytest` + `pytest-asyncio`. `tmp_path` fixture for filesystem tests. No filesystem mocks — real temp dirs.

Per module:
- **scanner** — fixture builds fake `.claude/`-like tree in `tmp_path` with symlinks, permission-denied dir, nested dirs. Assert entries found, no crash on perm error, no link-cycle following.
- **classifier** — pure function, parametrized ~15 cases covering all rules and edges (unknown name recent, known harness name, stale archive pattern).
- **reasoner** — mock Anthropic client at SDK boundary. Assert cache hit skips the call; assert failure path returns `"unknown"`.
- **executor** — real tarball in `tmp_path`. Assert: corrupt-tar aborts delete, realpath guard refuses `/etc/passwd`, allowlist guard refuses `sessions/` even when user-approved, actions row written before delete.
- **taxonomy** — snapshot test: fixed DB state → generated markdown matches fixture.

Integration: one end-to-end scan → classify → approve → execute → verify archive exists, paths gone, actions logged, `taxonomy.md` written.

Web: FastAPI `TestClient`. `POST /scan` returns 200 + scan id. `POST /execute` in dry-run returns planned actions without touching disk.

Not tested:
- HTMX rendering (manual visual check).
- Anthropic API contract.

Coverage target: ~80% on `executor.py` (destructive). ~60% elsewhere. No blind coverage chase.

## Open Questions

None at spec time. If encountered during implementation, they surface in the plan, not here.

## What This Spec Is Not

- Not an implementation plan. That comes next via the writing-plans skill.
- Not a commitment to build P2 or P3. Those are separate decisions after P1 ships.
