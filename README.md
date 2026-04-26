# claude-workspace-tool

Local FastAPI + HTMX tool. **Game Boy-themed personal operating system for Claude Code power use.** Three pillars:

- **P1 — Inventory.** Audit + clean `~/.claude/`. Live-walk dirs, edit/duplicate skills, copy paths, VS Code launch.
- **P2 — Memory.** FTS5 search + dashboard over your 374+ session transcripts under `~/.claude/projects/`. Resume past work.
- **P3 — Evolution.** *(planned)* Insight extraction from sessions into reusable artifacts.

Read-only over session content. Prompts never sent to an LLM.

## Run

Requires Python 3.11+.

```
python3 -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
uvicorn app.main:app --port 7878 --reload --env-file .env
```

Or, if `gbc` is installed in your shell rc (see `~/.zshrc`):

```
gbc
```

Open http://localhost:7878.

## Reasoner (optional LLM)

Provider-agnostic. Set one of:

- `ANTHROPIC_API_KEY` (Claude Haiku 4.5)
- `GEMINI_API_KEY` (Gemini 2.5 Flash)

In `.env` (gitignored):

```
GEMINI_API_KEY=AIza...your_key_here
# CLAUDE_TOOL_REASONER=gemini    # force a provider when both keys set
# CLAUDE_TOOL_DISABLE_REASONER=1 # hard kill switch
```

The reasoner is **metadata-only**: it sees entry name, kind, size bucket, age bucket, sample filenames. **Never** file contents.

## Safety model

- Deny-by-default for deletion: only entry names on a hardcoded kill-candidate allowlist (`app/allowlists.py`) are ever proposed for delete.
- TOCTOU-checked executor: inode + mtime re-verified before any `rm`.
- All subprocess calls use list args, `shell=False`, `--` separator.
- Archive-before-delete: tarball at `~/.claude-archive-YYYYMMDD-HHMMSS-<pid>.tar.gz`, integrity-checked.
- Dry-run default in UI; "armed" toggle required.
- Editable subdir whitelist for skill editor: `skills/`, `commands/`, `agents/`, `notes/`, `scripts/`. Sensitive blocklist (`.env*`, `*.key`, `*secret*`, etc.) refused on read AND write.
- Session content (P2) is read-only. Indexed prompt text never leaves the local DB.

## P1: Workspace audit

- Click **New scan** → walks `~/.claude/`, classifies each entry: `harness_protected | kill_candidate | active | unknown`.
- Click `▼ inspect` on any entry → detail page with metadata + 2-level dir tree + per-file preview.
- Click any sub-dir → drill-down via `/path` route.
- `▽` button → open + edit any text file (with whitelist).
- `📋` → copy path or preview to clipboard.
- `↗` → open in VS Code (`vscode://file/...`).
- `⎘` → duplicate a skill dir as `<name>-vN`.

## P2: Session index

- **/sessions** — searchable, filterable timeline of all Claude Code sessions across `~/.claude/projects/`.
- **/sessions/{id}** — transcript view, streamed line-by-line off the original jsonl, paginated 200 events/page.
- **/families** — auto-detected project families (path-prefix + worktree collapse) with override UI.
- **/reindex** — force rebuild button; otherwise the index refreshes lazily on `/` and `/sessions` page loads, comparing jsonl mtimes.
- Search is **FTS5 on user prompts only** (not tool outputs, not assistant replies).

### Privacy

Session prompts may contain secrets you pasted into Claude conversations. The index is stored locally in `data/workspace.db`. Nothing is sent to an LLM. Use **Families → Redact prompts** with a SQL `LIKE` pattern (e.g. `%sk_live_%`) to delete matching FTS rows.

## Game Boy nav

- **Arrow keys** — navigate entries / groups
- **Space** — toggle kill-candidate checkbox
- **A / Enter** — inspect focused entry
- **B / Esc** — back one level
- **Topnav** — Home · Sessions · Families
- **Status LED** (top right) — green = reasoner up, blue = thinking, red = connect fail. Re-polls every 3s on bad, 30s on ok.

## Test

```
pytest                       # full suite (~146 unit + 5 Playwright wire-nav)
pytest tests/test_wire_nav.py -v --headed --slowmo 500   # watch the browser
```

## Layout

```
app/
  main.py            # FastAPI app + routes
  scanner.py         # P1: walk ~/.claude/
  classifier.py      # P1: deny-by-default rule engine
  executor.py        # P1: gauntlet-gated archive + delete
  taxonomy.py        # P1: markdown writer
  inspector.py       # P1: live dir walk for detail view
  files.py           # P1: read/edit/duplicate w/ whitelist
  events.py          # P2: jsonl parser
  families.py        # P2: project-family detector
  fts.py             # P2: safe FTS5 query layer
  session_reader.py  # P2: stream transcripts
  session_index.py   # P2: lazy incremental reindex
  reasoner.py        # provider-agnostic LLM purpose
  llm.py             # anthropic / gemini adapters
  formatting.py      # size + age helpers
  allowlists.py      # harness + kill-candidate sets
  db.py              # sqlite schema + connect
  models.py          # pydantic models
  templates/         # jinja
static/style.css     # Game Boy DMG palette
docs/superpowers/
  specs/             # design specs (P1, P2)
  plans/             # implementation plans (P1, P2)
data/
  workspace.db       # sqlite (sessions, prompts_fts, families, scans, entries, actions, index_runs)
  taxonomy.md        # generated, gitignored
```

## Cost watcher (`gbc-watch`)

The cost watcher is a Node sidecar that tails Claude Code session JSONL files
under `~/.claude/projects/` and posts token-usage records to FastAPI's
`/ingest/usage`. The `/costs` page then renders running USD totals.

### One-time setup

```bash
cd watcher
npm install
```

Add this function to `~/.zshrc`:

```zsh
gbc-watch() {
  cd ~/dev/claude-workspace-tool/watcher && node index.js
}
```

Then `source ~/.zshrc`.

### Run

```bash
# terminal 1
gbc           # FastAPI on http://127.0.0.1:7878

# terminal 2
gbc-watch     # tails ~/.claude/projects/, posts to /ingest/usage
```

Open `http://127.0.0.1:7878/costs`. The first run backfills from existing
JSONL files (idempotent — safe to restart any time). After backfill, totals
update reactively as new assistant messages are written.

Order of `gbc` / `gbc-watch` doesn't matter — the watcher's retry queue
absorbs `ECONNREFUSED` while FastAPI is starting.

### Adding a new model's pricing

When Anthropic releases a new model, you'll see a `⚠ N events without pricing`
warning on `/costs`. To resolve:

1. Add an entry to `app/pricing.py`'s `RATES` dict (key: `(model, "standard")`).
2. Run: `python -m app.cost_recompute`
3. Refresh `/costs`.

Existing rows keep the rates they were ingested with — the recompute only
updates rows that were marked `unknown_pricing=1`.

### Environment overrides

The watcher reads three optional env vars:

| Variable | Default |
|---|---|
| `CCT_INGEST_URL` | `http://127.0.0.1:7878/ingest/usage` |
| `CCT_PROJECTS_DIR` | `~/.claude/projects` |
| `CCT_STATE_PATH` | `<repo>/data/.watcher-state.json` |

## License

MIT-ish. Personal tool. Use at own risk.
