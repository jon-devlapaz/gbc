# app/taxonomy.py
from __future__ import annotations
import sqlite3
from pathlib import Path


def write_taxonomy(db: sqlite3.Connection, scan_id: int, out_path: Path) -> None:
    rows = db.execute(
        "SELECT path, kind, size_bytes, mtime, file_count, status, reason, purpose "
        "FROM entries WHERE scan_id=? ORDER BY path",
        (scan_id,),
    ).fetchall()

    lines = [f"# ~/.claude/ Taxonomy — scan {scan_id}", ""]
    for r in rows:
        name = Path(r["path"]).name
        lines.append(f"## {name}")
        lines.append(f"- **Kind:** {r['kind']}")
        lines.append(f"- **Status:** {r['status']}")
        lines.append(f"- **Rule:** {r['reason']}")
        lines.append(f"- **Purpose:** {r['purpose'] or '(not reasoned)'}")
        lines.append(f"- **Size:** {r['size_bytes']} bytes")
        lines.append(f"- **Last mtime:** {r['mtime']}")
        lines.append(f"- **File count:** {r['file_count']}")
        lines.append("")

    tmp = out_path.with_suffix(out_path.suffix + ".tmp")
    tmp.write_text("\n".join(lines))
    tmp.replace(out_path)
