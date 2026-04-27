# CLAUDE.md — game-boy-claude (gbc)

Project-scoped guidance for any Claude agent working in this repo. Read this first.

## What this is

A local FastAPI + SQLite + HTMX webapp that gives Jon visibility into his Claude Code sessions:

1. **Workspace audit** (P1) — scans `~/.claude/`, classifies entries, allows safe deletion of cruft
2. **Session memory** (P2) — FTS5 search over JSONL session files at `~/.claude/projects/`, family auto-detection, transcript browsing
3. **Cost watcher** — Node ccsniff sidecar tails JSONL → POSTs token usage to `/ingest/usage` → `/costs` page renders live USD totals, model breakdown, day-grouped session cards, Gemini Q&A box

UI is Game Boy DMG-themed (4-shade LCD green, scanlines, "Press Start 2P" + "VT323"). Repo lives at `~/dev/claude-workspace-tool`. GitHub: https://github.com/jon-devlapaz/gbc.

## Run / develop

```bash
gbc           # FastAPI on http://127.0.0.1:7878 with --reload
gbc-watch     # Node ccsniff sidecar (separate terminal)
pytest        # Python tests (from repo root)
cd watcher && npm test   # Node tests
```

Both `gbc` and `gbc-watch` are zsh functions in `~/.zshrc`. Order doesn't matter — the watcher's retry queue absorbs `ECONNREFUSED` while FastAPI starts.

## Architecture

```
~/.claude/projects/*.jsonl     ← Claude Code writes these
        │
        ▼ (Node sidecar)
   watcher/index.js  ──ccsniff──►  watcher/parser.js  ──HTTP──►  /ingest/usage
                                                                       │
                                                                       ▼
                                                            data/workspace.db
                                                                       ▲
                                                                       │
                                                            FastAPI routes
                                                            /costs, /sessions, /
```

Two processes, decoupled by HTTP. Pricing lives only in `app/pricing.py`. Idempotency via `cost_events.message_uuid UNIQUE`. Subagent attribution via `parent_session_id` (path-derived: `<projects>/<dir>/<parent>/subagents/agent-*.jsonl`).

## Codebase map

```
app/
  main.py              FastAPI factory; all route definitions
  db.py                SQLite schema + idempotent migrations
  pricing.py           RATES dict ($/M tokens) + resolve(model, tier)
  cost_ingest.py       POST /ingest/usage; UsageEvent pydantic model
  cost_query.py        Aggregations: today_total, range_total, by_model,
                       by_session, by_cwd, by_day (subagent rollup)
  cost_recompute.py    `python -m app.cost_recompute` backfills unknown_pricing rows
  cost_qa.py           build_snapshot() + ask() — Gemini-powered Q&A over snapshot
  sessions_query.py    by_day() for session list day-grouping
  session_index.py     Reindex JSONL → sessions table + prompts_fts
  session_reader.py    Stream JSONL events for transcript view
  fts.py               FTS5 search over user prompts
  llm.py               select_provider() → (name, call_fn). Anthropic + Gemini.
  reasoner.py          Per-entry purpose-guess workflow
  classifier.py        Entry classification (kill_candidate, keep, unknown, etc.)
  scanner.py           Walk ~/.claude/ for the audit
  inspector.py         Live dir-tree inspector
  files.py             Safe file read/write/duplicate with whitelist
  executor.py          Armed-only delete executor
  formatting.py        Jinja filters: size, age, local_time, local_datetime
  templates/           Jinja templates extending base.html
static/style.css       DMG palette + all styling

watcher/               Node ESM sidecar
  index.js             Orchestrator — backfill + ccsniff-driven live loop
  parser.js            JSONL → usage records (with cwd + path-based parent)
  poster.js            Batched POST + retry queue + queueCap
  state.js             Per-file byte offsets at data/.watcher-state.json

tests/                 pytest; 33 test files
docs/superpowers/      specs/ + plans/ (design docs)
data/workspace.db      Live SQLite (gitignored)
```

## Conventions (don't drift from these)

### Templates / UI
- Game Boy DMG palette in `:root` of `static/style.css`. Use these tokens — never hardcode hex except as a fallback in `var(--name, #fallback)`.
- Variables: `--lcd-0` (#0f380f darkest "ink"), `--lcd-1`, `--lcd-2`, `--lcd-3` (#9bbc0f screen bg), `--bezel` (#2a3028), `--shell` (beige), `--led-red` (danger).
- Fonts: `--font-pixel` ('Press Start 2P', for headings/labels) and `--font-lcd` ('VT323', for body / numbers).
- `<section>` elements containing a `<form>` get a global `:has(form)` red treatment ("kill_candidate"). If you add a form to a non-destructive section, override with explicit `border-color`/`box-shadow`.
- Cache-busting: `base.html` references `style.css?v={{ static_version }}` (mtime-based). No action needed unless you serve other static assets.

### Templates / FastAPI
- Routes use `templates.TemplateResponse(request, "name.html", ctx)` — modern Starlette signature, NOT the legacy `(name, ctx)`.
- All routes inside `create_app()` as closures (so they capture `get_db`, `reasoner_call_fn`, `templates`, etc.).
- `_base_ctx()` provides `reasoner_enabled`, `reasoner_provider`, `static_version` — merge with page-specific dicts via `_base_ctx() | page_ctx`.
- Display timezone is **America/Chicago** by default. Set `os.environ['TZ']` at top of `main.py` before any `datetime` import — affects SQLite `date(ts, 'localtime')` AND Python `datetime.now()`. Override via `CLAUDE_TOOL_DISPLAY_TZ` env var.

### Database
- Schema bootstrap in `app/db.py` `SCHEMA` constant via `executescript` (CREATE TABLE IF NOT EXISTS only).
- Additive migrations beyond CREATE: put in `migrate(conn)` function, called from `connect()` after `init_schema()`.
- Foreign keys enabled (`PRAGMA foreign_keys = ON`).
- Cost data: `cost_events` is loose-joined to `sessions` via text `session_id` (no FK — sidecar may ingest before session is indexed).
- Timestamps: ISO-8601 UTC with `Z` suffix in storage. Display via `local_time` / `local_datetime` Jinja filters.

### Tests
- pytest with `db` fixture in `tests/conftest.py` — fresh in-memory schema for each test.
- Test files mirror module names (`test_<module>.py`).
- Integration tests use `TestClient(create_app())` with `monkeypatch.setenv("CLAUDE_TOOL_CLAUDE_ROOT", ...)` and `("CLAUDE_TOOL_DATA_DIR", ...)` so each test gets isolated fake roots.
- **Real SQLite, not mocks.** This is a deliberate choice (mocks lie about migrations).
- Pre-existing `tests/test_wire_nav.py` errors are Playwright fixture issues unrelated to this codebase — ignore.

### Watcher (Node)
- ESM, Node 18+, zero deps except `ccsniff`.
- ccsniff event payload uses `conversation.file` (not `.path`). The 30s safety re-scan is intentional defense-in-depth in case events are missed.
- State file at `data/.watcher-state.json` is gitignored. Idempotent — safe to delete and rebuild offsets from scratch (backfill will replay; `INSERT OR IGNORE` on `message_uuid` drops dupes).

## Safety invariants (P1/P2/cost watcher — non-negotiable)

1. **Read-only over JSONL corpus.** Code under `app/session_*.py` and `watcher/` never writes to / moves / deletes anything under `~/.claude/projects/`. Parse + project into SQLite only.
2. **Prompt content stays local.** `prompts_fts` is never sent to an LLM. Reasoner only sees entry-purpose metadata, never user prompt text.
3. **Cost Q&A snapshot is bounded.** `build_snapshot()` returns ~1-2 KB of stats — totals, top-N sessions, model/cwd breakdowns. Never raw prompt content. Don't expand.
4. **Deny-by-default deletion.** The audit's `executor.py` only runs delete when `armed=true` is explicitly checked. Mock/dry-run mode is the default.
5. **No prompt-text leak into git.** Logs, error messages, debug output: never include `first_prompt` or message content.

## Where things are documented

- **README.md** — user-facing run instructions
- **docs/superpowers/specs/** — design docs (cost watcher: `2026-04-25-cost-watcher-design.md`)
- **docs/superpowers/plans/** — implementation plans (cost watcher: `2026-04-25-cost-watcher.md`)

## Common tasks

**Add a new model to pricing:**
1. Add `(model_name, "standard"): _OPUS|_SONNET|_HAIKU` (or new template) in `app/pricing.py` `RATES`
2. `python -m app.cost_recompute` to backfill `unknown_pricing=1` rows
3. Refresh `/costs` — the ⚠ warning disappears

**Reset cost data:**
```bash
sqlite3 data/workspace.db "DELETE FROM cost_events; VACUUM;"
rm data/.watcher-state.json   # only if you want to re-backfill old JSONL
```

**Re-run watcher backfill from scratch:**
```bash
rm data/.watcher-state.json
gbc-watch
```
Idempotent — `message_uuid UNIQUE` drops duplicates.

**Add a new aggregation query:**
- Stays in `app/cost_query.py`
- Pure-SQL where possible; CTE rollup pattern for subagent attribution
- Add a test in `tests/test_cost_query.py` using the `db` fixture and synthetic INSERTs

## Style preferences

- Zen of Python — explicit > implicit
- One responsibility per module
- Don't add comments that explain WHAT (names should do that). Add a comment for non-obvious WHY.
- Don't write multi-paragraph docstrings; one short summary line is enough.
- Match existing patterns in this repo over generic "best practices."

## When in doubt

Read the spec at `docs/superpowers/specs/2026-04-25-cost-watcher-design.md` before changing cost-tracking surface area. Read `app/db.py` SCHEMA before changing data shape. Run the full pytest + npm test suite before claiming a feature is done.
