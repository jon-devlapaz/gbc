"""Display formatters used by Jinja templates."""
from __future__ import annotations
import os
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

DISPLAY_TZ = ZoneInfo(os.environ.get("CLAUDE_TOOL_DISPLAY_TZ", "America/Chicago"))


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


def _parse_iso_utc(iso_str: str) -> datetime | None:
    """Parse an ISO-8601 timestamp; assume UTC if naive or 'Z'-suffixed."""
    if not iso_str:
        return None
    s = iso_str.replace("Z", "+00:00")
    try:
        dt = datetime.fromisoformat(s)
    except (TypeError, ValueError):
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def format_local_time(iso_str: str | None) -> str:
    """ISO-8601 (UTC) → 'HH:MM:SS' in DISPLAY_TZ. Returns '—' on parse failure."""
    dt = _parse_iso_utc(iso_str or "")
    if dt is None:
        return "—"
    return dt.astimezone(DISPLAY_TZ).strftime("%H:%M:%S")


def format_local_datetime(iso_str: str | None) -> str:
    """ISO-8601 (UTC) → 'YYYY-MM-DD HH:MM:SS' in DISPLAY_TZ. Returns '—' on parse failure."""
    dt = _parse_iso_utc(iso_str or "")
    if dt is None:
        return "—"
    return dt.astimezone(DISPLAY_TZ).strftime("%Y-%m-%d %H:%M:%S")
