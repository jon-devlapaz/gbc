# Claude Workspace Tool — P1: Workspace Audit + Cleanup

**Date:** 2026-04-23
**Status:** Design approved, pending spec review
**Owner:** Jon
**Scope:** Phase 1 of a three-phase umbrella project

## Umbrella Project Context

The umbrella goal is to make Jon a more productive Claude Code power user by giving him persistent, local tooling for three recurring pains:

- **P1 — Workspace audit + cleanup (this spec).** Map `~/.claude/`, kill dead entries, produce a canonical taxonomy.
- **P2 — Session index + search (future).** Build an index over `sessions/` and `projects/` transcripts so past work is findable and resumable.
- **P3 — Insight extraction (future).** Extract durable lessons, patterns, and TODOs from sessions into a queryable knowledge store.

All three phases share a single long-lived localhost app. P1 establishes the stack, schema, and UI shell the later phases extend. Each phase ships independently, with its own spec and implementation plan.

## Goals (P1)

1. Produce a complete, human-readable taxonomy of every top-level entry under `~/.claude/` — what it is, who owns it (harness vs user), whether it's still active.
2. Archive and delete dead entries safely. Default-refuse anything not on an explicit user-curated kill-candidate list.
3. Leave Jon with a localhost web UI that is the durable home for P2 and P3.

## Non-Goals (P1)

- Search, index, or insight extraction. Those are P2/P3.
- Auditing anything outside `~/.claude/` (e.g., `~/dev/`).
- Auto-scheduled re-runs. Manual trigger only.
- Multi-user. Single-user local tool.
- Cloud deployment.
- Reading user file contents for classification. Classification is metadata-only (see reasoner).

## Architecture

**App location:** `~/dev/claude-workspace-tool/` (own repo, git-tracked, outside `.claude/`).

**Stack:**
- Backend: FastAPI, Python 3.11+, stdlib + `pydantic`.
- Frontend: Jinja2 server-rendered templates + HTMX for interactivity + one small CSS file.
- Persistence: SQLite at `~/dev/claude-workspace-tool/data/workspace.db`. Plain markdown export for human reads at `~/dev/claude-workspace-tool/data/taxonomy.md`.
- LLM: Anthropic SDK, `claude-haiku-4-5`. API key from env. Metadata-only prompts.

**Run:** `uvicorn app:main --port 7878` → browser at `http://localhost:7878`. No build step.

**Style:** Zen of Python. Explicit over implicit, flat over nested, simple over clever. Each module has one clear purpose.

## Safety Model

P1 is a destructive tool on a directory the harness actively uses. Three safety principles hold everywhere:

1. **Deny-by-default for deletion.** Nothing is deletable unless it appears on a hardcoded kill-candidate allowlist of entry names. Unknown entries (including any new upstream harness additions) classify as `unknown` and cannot be deleted — only surveyed.
2. **TOCTOU-safe execution.** Between scan and delete, each entry's inode + mtime is re-verified. A mismatch aborts that entry's delete.
3. **No shell interpolation.** All subprocess calls use list args with `shell=False` and `--` separator before user-derived paths.

These are invariants, not toggles.

## Components

Five modules, each in its own file, each testable alone.

### 1. `scanner.py`

Walks `~/.claude/` deep. Emits one `Entry(path, kind, inode, size_bytes, mtime, file_count, sample_files)` per top-level entry, where `kind ∈ {dir, file}`.
- Top-level files (e.g., `history.jsonl`, `settings.json`, `RTK.md`) are included with `file_count=1` and empty `sample_files`.
- Dirs are walked deep to compute `size_bytes`, `file_count`, and up to 5 representative filenames (names only, never contents).
- `inode` captured via `os.stat().st_ino` for TOCTOU re-verification later.

Pure read, no side effects.

Behavior:
- Permission-denied subdir → log, skip, continue. Never crash.
- Symlinks → resolve once, detect cycles via visited-set, do not follow links leaving `~/.claude/`.
- Sample filenames skip `.DS_Store`, `*.lock`.

### 2. `classifier.py`

Rule engine. Input `Entry`, output `Verdict(status, reason)` where `status ∈ {harness_protected, kill_candidate, active, unknown}`.

Rules (first match wins, name-based):
- `harness_protected`: entry name is in the hardcoded harness allowlist — `sessions/`, `projects/`, `history.jsonl`, `settings.json`, `settings.local.json`, `hooks/`, `ide/`, `shell-snapshots/`, `session-env/`, `mcp.json`, `statusline-command.sh`, `cache/`, `cowork_plugins/`, `cowork_settings.json`, `telemetry/`, `usage-data/`, `plugins/`, `commands/`, `agents/`, `skills/`. Never deletable.
- `kill_candidate`: entry name matches one of a hardcoded kill-candidate allowlist — today: `paste-cache/`, `.window-cleaner-backups/`, `backups/`, `skills-archive/`, `debug/`, `downloads/`, `file-history/`, `.DS_Store`, `stats-cache.json`, `RTK.md`. Proposed for delete in UI; user still approves per entry.
- `active`: entry name not in either list AND mtime < 7 days → keep, annotate as "recently touched."
- `unknown`: everything else. Default status. Shown in UI but **not deletable**. To delete, user must first add the name to the kill-candidate allowlist in code. Intentional friction.

Pure function. No I/O.

### 3. `reasoner.py` (metadata-only)

For every entry, regardless of status, generates a short human-readable `purpose` line used by the taxonomy doc and the review UI.

**Input sent to LLM:** entry name, kind (dir/file), size bucket (e.g., "< 1 MB", "1-100 MB"), age bucket ("< 7 days", "1-6 months", "> 1 year"), up to 5 sample filenames (names only), and the rule that fired. **Never file contents.**

**Output:** 1-2 sentence purpose guess. No keep/kill recommendation — that's the classifier's job.

Cache: in-memory dict keyed by `(path, mtime_iso)`. Lifetime = current scan. No persistent cache table (P1 scope trim).

Failure modes:
- Network or rate-limit error → `purpose="(reasoner unavailable)"`. Entry still shown.
- Cost cap: max 50 live calls per scan; excess entries get `purpose="(not reasoned)"`.

Risk of exfil is reduced to: entry names and sample filenames within `~/.claude/`. If any filename itself is sensitive, the user can set `CLAUDE_TOOL_DISABLE_REASONER=1` to skip reasoner entirely; taxonomy falls back to names + sizes only.

### 4. `executor.py`

Given an approved kill-list (list of `entry.id` from the current scan):

1. **Pre-flight gauntlet per entry — ALL must pass or that entry is skipped and logged:**
   - Entry's `scan_id` matches the current scan.
   - Entry's classifier status is `kill_candidate`. Never `harness_protected`, `active`, or `unknown`. Refuse even if user-approved.
   - `os.path.realpath(entry.path)` starts with `os.path.expanduser("~/.claude/")` with a trailing slash.
   - Re-stat now: `os.stat(entry.path).st_ino == entry.inode` AND `st_mtime` matches recorded mtime within 1 second. TOCTOU check.
2. **Archive:** `subprocess.run(["tar", "czf", archive_path, "--", *paths], shell=False, check=True)` where `archive_path = ~/.claude-archive-YYYYMMDD-HHMM.tar.gz`.
3. **Integrity:** `subprocess.run(["tar", "tzf", archive_path], shell=False, check=True)`. On failure, abort entire run — no deletes.
4. **Per-path delete:** `subprocess.run(["rm", "-rf", "--", path], shell=False, check=True)`. No shell. No glob expansion.
5. **Logging:** `actions` row with `planned`→`executed`/`failed` transitions. Written BEFORE the `rm` call (planned), updated AFTER (executed/failed + error_detail).
6. **Dry-run default ON.** User toggles "armed" in UI. Execute button disabled until armed.
7. **Concurrency:** `fcntl.flock` exclusive on `data/.scan.lock` at start of run. Released in `finally`. `scans.finished_at IS NULL` is NOT a lock; it is only a UI hint.

### 5. `taxonomy.py`

After execute finalizes: reads the current scan's surviving rows, writes a markdown doc with one section per entry (name, kind, purpose, owner, status, last mtime, approx size, sample filenames).

Atomic write: `taxonomy.md.tmp` → `rename` to `taxonomy.md`. No half-written file visible.

### Web layer

Collapsed from five endpoints to three. FastAPI routes wrap the modules, HTMX templates render:

- `GET /` — home. Last scan summary + "New scan" button.
- `POST /scan` — acquires scan lock, runs scanner → classifier → reasoner inline, stores rows, returns review view. No SSE in P1 (sync, scan is fast enough; add streaming only if measured latency hurts).
- `GET /review/{scan_id}` — grouped table: harness_protected (collapsed), active (collapsed), kill_candidate (expanded, checkboxes per entry), unknown (expanded, read-only with "learn more" that shows rationale).
- `POST /execute/{scan_id}` — body = kill-list of entry ids + `armed: bool`. Runs executor. Returns archive path + action log. If `armed=false`, returns planned actions only (dry-run), nothing touched.
- `POST /explain/{entry_id}` — on-demand reasoner re-run for one entry. For when purpose is `(not reasoned)` or the user wants a second pass.

Finalization (taxonomy write) is automatic after a successful armed execute. No separate endpoint.

## Data Flow

1. User clicks **Scan** → `POST /scan` → lock acquired → `scanner.walk()` → `classifier.verdict()` → `reasoner.purpose()` per entry → rows inserted → lock released → review view returned.
2. User reviews grouped table, checks entries in `kill_candidate` group → submits → `POST /execute/{scan_id}` with `armed=false`. UI shows planned actions.
3. User toggles "armed" → resubmits with `armed=true` → executor runs per-entry gauntlet → archive + delete → `actions` rows written → taxonomy regenerated.

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
  kind TEXT NOT NULL,              -- dir | file
  inode INTEGER NOT NULL,
  size_bytes INTEGER,
  mtime TEXT,                      -- ISO8601 with second precision
  file_count INTEGER,
  sample_files TEXT,               -- JSON array
  status TEXT NOT NULL,            -- harness_protected | kill_candidate | active | unknown
  reason TEXT,                     -- rule that fired
  purpose TEXT,                    -- LLM metadata guess, or sentinel
  user_decision TEXT               -- keep | kill | NULL; unique per (scan_id, path)
);

CREATE UNIQUE INDEX entries_scan_path ON entries(scan_id, path);

CREATE TABLE actions (
  id INTEGER PRIMARY KEY,
  scan_id INTEGER NOT NULL REFERENCES scans(id),
  entry_id INTEGER REFERENCES entries(id),  -- NULL for run-level rows like archive-create
  ts TEXT NOT NULL,
  action TEXT NOT NULL,            -- archive | delete
  path TEXT NOT NULL,
  archive_path TEXT,
  state TEXT NOT NULL,             -- planned | executed | failed | skipped
  error_detail TEXT
);
```

No `reasoner_cache` table — in-memory for P1.

## Error Handling + Edge Cases

Cross-cutting:

- All filesystem operations use `pathlib.Path`, never string concat.
- All subprocess calls use list args, `shell=False`, `--` separator before user-derived paths.
- All destructive paths pass through the full gauntlet in `executor.py`. Hardcoded, not config-driven.
- `user_decision` is scoped per scan (unique index on `scan_id, path`), so decisions from old scans never leak into a new run.
- Atomic write for `taxonomy.md`.
- Actions row written in `planned` state before each destructive call; updated to `executed`/`failed`/`skipped` after.
- File-specific handling: top-level files archive and delete identically to dirs (same tar, same `rm -rf --`). UI shows `kind` column so files aren't visually lumped as dirs.

## Testing

Stack: `pytest` + `pytest-asyncio`. `tmp_path` fixture for filesystem tests. No filesystem mocks — real temp dirs.

Per module:
- **scanner** — fixture builds fake `.claude/`-like tree in `tmp_path` with symlinks, permission-denied dir, nested dirs, top-level files. Assert: entries found, inode captured, no crash on perm error, no link-cycle following, no reading of file contents (enforced via unreadable files present).
- **classifier** — pure function, parametrized ~20 cases covering all rules, unknown-status default, file vs dir names, new-upstream simulation (unknown name).
- **reasoner** — mock Anthropic client. Assert: prompt payload contains only metadata (no file contents), cache hit skips call, failure path returns sentinel, `CLAUDE_TOOL_DISABLE_REASONER=1` skips client entirely.
- **executor** — real tarball in `tmp_path`. Assert each gauntlet check in isolation: wrong scan_id refused, `unknown` status refused, non-`~/.claude/` realpath refused, inode mismatch refused, mtime skew refused, harness_protected name refused, corrupt-tar aborts run, file-lock prevents concurrent run, subprocess calls list-form (no shell). Actions row in `planned` state written before `rm`.
- **taxonomy** — snapshot test: fixed DB state → generated markdown matches fixture.

Integration: end-to-end scan → review → dry-run execute (asserts disk untouched) → armed execute → verify archive exists, paths gone, actions logged, taxonomy.md written.

Web: FastAPI `TestClient`. `POST /scan` returns 200 + review HTML fragment. `POST /execute` with `armed=false` returns planned actions, disk untouched. `armed=true` requires explicit flag.

Not tested:
- HTMX rendering (manual visual check).
- Anthropic API contract.

Coverage target: ~90% on `executor.py` (destructive, safety-critical). ~60% elsewhere. No blind coverage chase.

## Open Questions

None at spec time. If encountered during implementation, they surface in the plan, not here.

## What This Spec Is Not

- Not an implementation plan. That comes next via the writing-plans skill.
- Not a commitment to build P2 or P3. Those are separate decisions after P1 ships.
