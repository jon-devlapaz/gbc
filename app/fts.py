from __future__ import annotations
import re
import sqlite3

SAFE_TOKEN = re.compile(r"[A-Za-z0-9]+")
SNIPPET_LEN = 160
SNIPPET_CTX = 8  # tokens of context


def escape_query(user_input: str) -> str:
    """Extract safe tokens from user input; quote each; join with space (implicit AND).

    Returns empty string if no safe tokens were found. Caller MUST skip the MATCH
    in that case, since `MATCH ''` is a syntax error.
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
