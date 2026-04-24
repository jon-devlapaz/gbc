# claude-workspace-tool

Local FastAPI + HTMX tool for auditing and cleaning `~/.claude/`.

## Run

```
pip install -e ".[dev]"
uvicorn app.main:app --port 7878
```

Open http://localhost:7878.

## Test

```
pytest
```
