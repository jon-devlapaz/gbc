# app/main.py
from __future__ import annotations
import json
import os
import sqlite3
from datetime import datetime
from pathlib import Path
from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from app import db as db_mod
from app.classifier import classify
from app.executor import Executor
from app.formatting import format_age, format_size
from app.models import Status
from app.reasoner import Reasoner
from app.scanner import walk
from app.taxonomy import write_taxonomy


def _env_path(key: str, default: Path) -> Path:
    v = os.environ.get(key)
    return Path(v) if v else default


def _maybe_anthropic_client():
    if os.environ.get("CLAUDE_TOOL_DISABLE_REASONER") == "1":
        return None
    if not os.environ.get("ANTHROPIC_API_KEY"):
        return None
    try:
        from anthropic import Anthropic
        return Anthropic()
    except Exception:
        return None


def _is_htmx(request: Request) -> bool:
    return request.headers.get("HX-Request") == "true"


def create_app() -> FastAPI:
    app = FastAPI()
    claude_root = _env_path("CLAUDE_TOOL_CLAUDE_ROOT", Path.home() / ".claude")
    data_dir = _env_path("CLAUDE_TOOL_DATA_DIR", Path(__file__).parent.parent / "data")
    db_path = data_dir / "workspace.db"
    templates_dir = Path(__file__).parent / "templates"
    static_dir = Path(__file__).parent.parent / "static"

    templates = Jinja2Templates(directory=str(templates_dir))
    templates.env.filters["size"] = format_size
    templates.env.filters["age"] = format_age
    app.mount("/static", StaticFiles(directory=str(static_dir)), name="static")

    anthropic_client = _maybe_anthropic_client()
    reasoner_enabled = anthropic_client is not None

    def get_db() -> sqlite3.Connection:
        return db_mod.connect(db_path)

    def _base_ctx() -> dict:
        return {"reasoner_enabled": reasoner_enabled}

    @app.get("/", response_class=HTMLResponse)
    def home(request: Request):
        conn = get_db()
        row = conn.execute("SELECT id, started_at FROM scans ORDER BY id DESC LIMIT 1").fetchone()
        return templates.TemplateResponse(
            request, "home.html", {**_base_ctx(), "last_scan": row}
        )

    @app.post("/scan", response_class=HTMLResponse)
    def scan(request: Request):
        conn = get_db()
        cur = conn.execute("INSERT INTO scans(started_at) VALUES (?)", (datetime.now().isoformat(),))
        scan_id = cur.lastrowid
        conn.commit()

        reasoner = Reasoner(client=anthropic_client) if anthropic_client else None

        for entry in walk(claude_root):
            verdict = classify(entry)
            purpose = reasoner.purpose(entry) if reasoner else None
            conn.execute(
                "INSERT INTO entries(scan_id,path,kind,inode,size_bytes,mtime,file_count,sample_files,status,reason,purpose) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                (scan_id, entry.path, entry.kind.value, entry.inode, entry.size_bytes,
                 entry.mtime.isoformat(), entry.file_count, json.dumps(entry.sample_files),
                 verdict.status.value, verdict.reason, purpose),
            )
        conn.execute("UPDATE scans SET finished_at=? WHERE id=?", (datetime.now().isoformat(), scan_id))
        conn.commit()
        return _render_review(request, templates, conn, scan_id, result=None, ctx=_base_ctx())

    @app.get("/review/{scan_id}", response_class=HTMLResponse)
    def review(request: Request, scan_id: int):
        conn = get_db()
        return _render_review(request, templates, conn, scan_id, result=None, ctx=_base_ctx())

    @app.post("/execute/{scan_id}", response_class=HTMLResponse)
    async def execute(request: Request, scan_id: int):
        form = await request.form()
        entry_ids = [int(v) for v in form.getlist("entry_id")]
        armed = form.get("armed") == "true"
        conn = get_db()

        ex = Executor(db=conn, claude_root=claude_root, data_dir=data_dir)
        result = ex.run(scan_id=scan_id, entry_ids=entry_ids, armed=armed)

        if armed and result.executed:
            write_taxonomy(conn, scan_id, data_dir / "taxonomy.md")

        return _render_review(request, templates, conn, scan_id, result=result, ctx=_base_ctx())

    @app.get("/entry/{entry_id}", response_class=HTMLResponse)
    def entry_detail(request: Request, entry_id: int):
        conn = get_db()
        row = conn.execute("SELECT * FROM entries WHERE id=?", (entry_id,)).fetchone()
        if not row:
            return HTMLResponse("(entry not found)", status_code=404)
        actions = conn.execute(
            "SELECT * FROM actions WHERE entry_id=? ORDER BY ts DESC", (entry_id,)
        ).fetchall()
        sample_files = json.loads(row["sample_files"] or "[]")
        return templates.TemplateResponse(
            request, "entry.html",
            {**_base_ctx(), "e": dict(row), "sample_files": sample_files,
             "actions": [dict(a) for a in actions]},
        )

    @app.post("/explain/{entry_id}", response_class=HTMLResponse)
    def explain(request: Request, entry_id: int):
        conn = get_db()
        row = conn.execute("SELECT * FROM entries WHERE id=?", (entry_id,)).fetchone()
        if not row:
            return HTMLResponse("(not found)", status_code=404)
        if anthropic_client is None:
            return HTMLResponse("(reasoner disabled — set ANTHROPIC_API_KEY)")
        from app.models import Entry, EntryKind
        entry = Entry(
            path=row["path"], kind=EntryKind(row["kind"]), inode=row["inode"],
            size_bytes=row["size_bytes"] or 0, mtime=datetime.fromisoformat(row["mtime"]),
            file_count=row["file_count"] or 0,
            sample_files=json.loads(row["sample_files"] or "[]"),
        )
        reasoner = Reasoner(client=anthropic_client)
        purpose = reasoner.purpose(entry)
        conn.execute("UPDATE entries SET purpose=? WHERE id=?", (purpose, entry_id))
        conn.commit()
        return HTMLResponse(purpose)

    return app


def _render_review(request, templates, conn, scan_id, result, ctx):
    rows = conn.execute(
        "SELECT * FROM entries WHERE scan_id=? ORDER BY path", (scan_id,)
    ).fetchall()
    by_status: dict[str, list[dict]] = {s.value: [] for s in Status}
    for r in rows:
        by_status[r["status"]].append(dict(r))
    groups = [
        ("Kill candidates", "kill_candidate"),
        ("Unknown (deny-by-default)", "unknown"),
        ("Active (recent)", "active"),
        ("Harness-protected", "harness_protected"),
    ]
    template_name = "_review_body.html" if _is_htmx(request) else "review.html"
    return templates.TemplateResponse(
        request,
        template_name,
        {**ctx, "scan_id": scan_id, "groups": groups, "by_status": by_status, "result": result},
    )


app = create_app()
