# claude-workspace-tool

Local FastAPI + HTMX tool for auditing and cleaning `~/.claude/`.

## Run

Requires Python 3.11+.

```
python3 -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
uvicorn app.main:app --port 7878
```

Open http://localhost:7878.

## Test

```
pytest
```
