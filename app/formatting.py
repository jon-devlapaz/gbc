"""Display formatters used by Jinja templates."""
from __future__ import annotations
from datetime import datetime


def format_size(n: int | None) -> str:
    if n is None:
        return "—"
    if n < 1024:
        return f"{n} B"
    units = ["KB", "MB", "GB", "TB"]
    size = float(n) / 1024
    for unit in units:
        if size < 1024:
            return f"{size:.1f} {unit}"
        size /= 1024
    return f"{size:.1f} PB"


def format_age(mtime_iso: str | None, now: datetime | None = None) -> str:
    if not mtime_iso:
        return "—"
    try:
        mtime = datetime.fromisoformat(mtime_iso)
    except (TypeError, ValueError):
        return mtime_iso
    now = now or datetime.now()
    delta = now - mtime
    seconds = int(delta.total_seconds())
    if seconds < 60:
        return "just now"
    minutes = seconds // 60
    if minutes < 60:
        return f"{minutes}m ago"
    hours = minutes // 60
    if hours < 24:
        return f"{hours}h ago"
    days = hours // 24
    if days < 7:
        return f"{days}d ago"
    if days < 30:
        return f"{days // 7}w ago"
    if days < 365:
        return f"{days // 30}mo ago"
    return f"{days // 365}y ago"
