# app/main.py
from __future__ import annotations
import json
import os
import sqlite3
from datetime import datetime
from pathlib import Path
from fastapi import FastAPI, Form, Request
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from app import cost_ingest as cost_ingest_mod
from app import cost_query as cost_query_mod
from app import db as db_mod
from app import files as files_mod
from app.classifier import classify
from app.executor import Executor
from app.files import FileSafetyError
from app.formatting import format_age, format_size
from app.inspector import inspect as inspect_dir
from app.llm import select_provider
from app.models import Status
from app.fts import search as fts_search
from app.reasoner import Reasoner
from app.scanner import walk
from app.session_index import reindex
from app.session_reader import stream as stream_events
from app.taxonomy import write_taxonomy


def _env_path(key: str, default: Path) -> Path:
    v = os.environ.get(key)
    return Path(v) if v else default


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

    selected = select_provider()
    reasoner_call_fn = selected[1] if selected else None
    reasoner_provider = selected[0] if selected else None
    reasoner_enabled = reasoner_call_fn is not None

    def get_db() -> sqlite3.Connection:
        return db_mod.connect(db_path)

    cost_ingest_mod.register_routes(app, get_db)

    def _static_version() -> str:
        """mtime of style.css → cache-busting query string. Cheap stat per request."""
        try:
            return str(int((static_dir / "style.css").stat().st_mtime))
        except OSError:
            return "0"

    def _base_ctx() -> dict:
        return {
            "reasoner_enabled": reasoner_enabled,
            "reasoner_provider": reasoner_provider,
            "static_version": _static_version(),
        }

    def _costs_ctx(conn) -> dict:
        today = cost_query_mod.today_total(conn)
        week = cost_query_mod.range_total(conn, days=7)
        month = cost_query_mod.range_total(conn, days=30)
        bm = cost_query_mod.by_model(conn, days=7)
        bm_total = sum(c for _, c in bm) or 1.0
        bm_rows = [
            {"model": m, "cost_usd": c, "pct": (c / bm_total) * 100}
            for m, c in bm
        ]
        sessions = cost_query_mod.by_session(conn, limit=50)
        days_grouped = cost_query_mod.by_day(conn, days=30)
        unknown_count = conn.execute(
            "SELECT COUNT(*) FROM cost_events WHERE unknown_pricing = 1"
        ).fetchone()[0]
        return {
            "today_usd": today,
            "week_usd": week,
            "month_usd": month,
            "by_model": bm_rows,
            "sessions": sessions,
            "days": days_grouped,
            "today_local": datetime.now().strftime("%Y-%m-%d"),
            "unknown_count": unknown_count,
            "qa_chips": [
                "What did I spend yesterday?",
                "Most expensive session this week",
                "Cost by project this week",
                "Why did today spike?",
                "Subagent vs main breakdown",
            ],
        }

    @app.get("/costs", response_class=HTMLResponse)
    def costs(request: Request):
        conn = get_db()
        ctx = _base_ctx() | _costs_ctx(conn)
        return templates.TemplateResponse(request, "costs.html", ctx)

    @app.get("/costs/partial", response_class=HTMLResponse)
    def costs_partial(request: Request):
        conn = get_db()
        ctx = _costs_ctx(conn)
        return templates.TemplateResponse(request, "_costs_body.html", ctx)

    @app.post("/costs/ask", response_class=HTMLResponse)
    def costs_ask(request: Request, question: str = Form(...)):
        from app.cost_qa import build_snapshot, ask
        if not reasoner_call_fn:
            html = '<div class="qa-answer warn">Reasoner not configured. Set GEMINI_API_KEY or ANTHROPIC_API_KEY in .env.</div>'
            return HTMLResponse(html)
        question = question.strip()
        if not question:
            return HTMLResponse('<div class="qa-answer muted">Ask a question above.</div>')
        conn = get_db()
        snapshot = build_snapshot(conn)
        try:
            answer = ask(snapshot, question, reasoner_call_fn)
        except Exception as e:
            return HTMLResponse(f'<div class="qa-answer warn">Error: {type(e).__name__}: {e}</div>')
        ctx = {"request": request, "question": question, "answer": answer, "provider": reasoner_provider}
        return templates.TemplateResponse(request, "_costs_qa_answer.html", ctx)

    @app.get("/health")
    def health():
        return {
            "reasoner": reasoner_enabled,
            "provider": reasoner_provider,
        }

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
        from app import sessions_query as sessions_query_mod
        sessions_by_day = sessions_query_mod.by_day(conn, days=14)
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
             "sessions_by_day": sessions_by_day,
             "today_local": datetime.now().strftime("%Y-%m-%d"),
             "top_families": top_families,
             "last_index": dict(last_index) if last_index else None},
        )

    @app.post("/scan", response_class=HTMLResponse)
    def scan(request: Request):
        """Fast scan: walk + classify only. Purposes stay NULL.

        Reasoner runs lazily per entry via POST /explain/{id} (the `?` button
        in the review UI). Avoids holding sqlite write lock during slow LLM calls.
        """
        conn = get_db()
        cur = conn.execute("INSERT INTO scans(started_at) VALUES (?)", (datetime.now().isoformat(),))
        scan_id = cur.lastrowid
        conn.commit()

        for entry in walk(claude_root):
            verdict = classify(entry)
            conn.execute(
                "INSERT INTO entries(scan_id,path,kind,inode,size_bytes,mtime,file_count,sample_files,status,reason,purpose) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                (scan_id, entry.path, entry.kind.value, entry.inode, entry.size_bytes,
                 entry.mtime.isoformat(), entry.file_count, json.dumps(entry.sample_files),
                 verdict.status.value, verdict.reason, None),
            )
            conn.commit()  # release lock per row so other reads can interleave
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
        tree = None
        if row["kind"] == "dir":
            try:
                tree = inspect_dir(Path(row["path"]), claude_root=claude_root)
            except Exception:
                tree = None
        is_editable_dir = files_mod.is_editable(Path(row["path"]), claude_root) if row["kind"] == "dir" else False
        real_path = Path(row["path"])
        crumbs = [
            {"name": f"Scan #{row['scan_id']}", "href": f"/review/{row['scan_id']}"},
            {"name": "~/.claude", "href": f"/path?path={claude_root}"},
        ]
        try:
            rel = real_path.relative_to(claude_root)
            cur = claude_root
            for part in rel.parts:
                cur = cur / part
                crumbs.append({"name": part, "href": f"/path?path={cur}"})
        except ValueError:
            crumbs.append({"name": row["path"], "href": f"/entry/{row['id']}"})
        return templates.TemplateResponse(
            request, "entry.html",
            {**_base_ctx(), "e": dict(row), "sample_files": sample_files,
             "actions": [dict(a) for a in actions], "tree": tree,
             "claude_root": str(claude_root),
             "is_editable_dir": is_editable_dir,
             "crumbs": crumbs, "is_adhoc": False,
             "parent_scan_id": row["scan_id"]},
        )

    @app.get("/path", response_class=HTMLResponse)
    def path_detail(request: Request, path: str):
        """Ad-hoc inspection of any path under ~/.claude/, including sub-dirs not in the scan."""
        try:
            real = Path(os.path.realpath(path))
            real.relative_to(claude_root)
        except (OSError, ValueError) as e:
            return HTMLResponse(f"(refused: {e})", status_code=400)
        if not real.exists():
            return HTMLResponse("(path does not exist)", status_code=404)

        st = real.stat()
        is_dir = real.is_dir()
        synthetic = {
            "id": None,
            "scan_id": None,
            "path": str(real),
            "kind": "dir" if is_dir else "file",
            "inode": st.st_ino,
            "size_bytes": st.st_size if not is_dir else 0,
            "mtime": datetime.fromtimestamp(st.st_mtime).isoformat(timespec="seconds"),
            "file_count": None,
            "sample_files": "[]",
            "status": None,
            "reason": None,
            "purpose": None,
            "user_decision": None,
        }
        tree = inspect_dir(real, claude_root=claude_root) if is_dir else None
        is_editable_dir = is_dir and files_mod.is_editable(real, claude_root)

        # Most recent scan (for "Scan #N" crumb at the front)
        conn = get_db()
        scan_any = conn.execute(
            "SELECT id FROM scans ORDER BY id DESC LIMIT 1"
        ).fetchone()
        parent_scan_id = scan_any["id"] if scan_any else None

        # Crumb trail: [scan → ~/.claude → parts…]
        rel = real.relative_to(claude_root)
        crumbs = []
        if parent_scan_id:
            crumbs.append({"name": f"Scan #{parent_scan_id}", "href": f"/review/{parent_scan_id}"})
        crumbs.append({"name": "~/.claude", "href": f"/path?path={claude_root}"})
        cur = claude_root
        for part in rel.parts:
            cur = cur / part
            crumbs.append({"name": part, "href": f"/path?path={cur}"})

        return templates.TemplateResponse(
            request, "entry.html",
            {**_base_ctx(),
             "e": synthetic, "sample_files": [], "actions": [],
             "tree": tree, "claude_root": str(claude_root),
             "is_editable_dir": is_editable_dir,
             "crumbs": crumbs, "is_adhoc": True,
             "parent_scan_id": parent_scan_id},
        )

    @app.get("/file", response_class=HTMLResponse)
    def get_file(request: Request, path: str):
        try:
            content = files_mod.read(Path(path), claude_root)
        except FileSafetyError as e:
            return HTMLResponse(f"<pre class='preview error'>{e}</pre>", status_code=400)
        # Always render as a fragment for HTMX swap.
        return templates.TemplateResponse(
            request, "_file_view.html",
            {"path": path, "content": content},
        )

    @app.post("/file", response_class=HTMLResponse)
    async def save_file(request: Request):
        form = await request.form()
        path = form.get("path", "")
        content = form.get("content", "")
        try:
            real = files_mod.write(Path(path), str(content), claude_root)
        except FileSafetyError as e:
            return HTMLResponse(f"<div class='banner banner-warn'>SAVE FAILED — {e}</div>", status_code=400)
        return HTMLResponse(
            f"<div class='banner banner-success'><strong>SAVED</strong> {real}</div>"
        )

    @app.post("/duplicate", response_class=HTMLResponse)
    async def duplicate(request: Request):
        form = await request.form()
        src = form.get("path", "")
        try:
            new_path = files_mod.duplicate_dir(Path(src), claude_root)
        except FileSafetyError as e:
            return HTMLResponse(f"<div class='banner banner-warn'>DUPLICATE FAILED — {e}</div>", status_code=400)
        return HTMLResponse(
            f"<div class='banner banner-success'><strong>DUPLICATED</strong> → <code>{new_path}</code></div>"
        )

    @app.post("/explain/{entry_id}", response_class=HTMLResponse)
    def explain(request: Request, entry_id: int):
        conn = get_db()
        row = conn.execute("SELECT * FROM entries WHERE id=?", (entry_id,)).fetchone()
        if not row:
            return HTMLResponse("(not found)", status_code=404)
        if reasoner_call_fn is None:
            return HTMLResponse("(reasoner disabled — set ANTHROPIC_API_KEY or GEMINI_API_KEY)")
        from app.models import Entry, EntryKind
        entry = Entry(
            path=row["path"], kind=EntryKind(row["kind"]), inode=row["inode"],
            size_bytes=row["size_bytes"] or 0, mtime=datetime.fromisoformat(row["mtime"]),
            file_count=row["file_count"] or 0,
            sample_files=json.loads(row["sample_files"] or "[]"),
        )
        reasoner = Reasoner(call_fn=reasoner_call_fn)
        purpose = reasoner.purpose(entry)
        conn.execute("UPDATE entries SET purpose=? WHERE id=?", (purpose, entry_id))
        conn.commit()
        return HTMLResponse(purpose)

    @app.get("/sessions", response_class=HTMLResponse)
    def sessions_list(request: Request, q: str = "", family: str = "", limit: int = 50):
        conn = get_db()
        try:
            reindex(conn, claude_root)
        except Exception:
            pass  # render list even if reindex fails

        if q.strip():
            hits = fts_search(conn, q, family=family or None, limit=limit)
            rows = hits
            mode = "search"
        else:
            sql = ["SELECT session_id, family, cwd, started_at, prompt_count, first_prompt FROM sessions"]
            params: list = []
            if family:
                sql.append("WHERE family=?"); params.append(family)
            sql.append("ORDER BY started_at DESC LIMIT ?"); params.append(limit)
            rows = [dict(r) for r in conn.execute(" ".join(sql), params).fetchall()]
            mode = "list"

        families = [
            r["family"] for r in conn.execute(
                "SELECT family, COUNT(*) AS n FROM sessions GROUP BY family ORDER BY n DESC"
            ).fetchall() if r["family"]
        ]

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
