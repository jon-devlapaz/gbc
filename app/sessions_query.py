"""Read-only aggregations over the indexed `sessions` table for dashboard views."""
from __future__ import annotations
import sqlite3
from datetime import datetime, timedelta


def by_day(conn: sqlite3.Connection, days: int = 14, per_day_limit: int = 25) -> list[dict]:
    """
    Group sessions by local date of started_at.

    Returns: [{date, session_count, sessions: [{...}]}, ...] DESC by date.
    Days with no sessions are omitted.
    """
    cutoff_local = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d")
    rows = conn.execute(
        """
        SELECT
          date(started_at, 'localtime') AS local_date,
          session_id, family, cwd, started_at, first_prompt
        FROM sessions
        WHERE date(started_at, 'localtime') >= ?
        ORDER BY local_date DESC, started_at DESC
        """,
        (cutoff_local,),
    ).fetchall()

    days_map: dict[str, dict] = {}
    for r in rows:
        d = r["local_date"]
        if d not in days_map:
            days_map[d] = {"date": d, "session_count": 0, "sessions": []}
        if len(days_map[d]["sessions"]) < per_day_limit:
            days_map[d]["sessions"].append({
                "session_id": r["session_id"],
                "family": r["family"],
                "cwd": r["cwd"],
                "started_at": r["started_at"],
                "first_prompt": r["first_prompt"],
            })
        days_map[d]["session_count"] += 1

    return list(days_map.values())
