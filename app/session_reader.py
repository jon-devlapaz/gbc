from __future__ import annotations
import json
from dataclasses import dataclass
from pathlib import Path

PREVIEW_LIMIT = 400


@dataclass
class EventView:
    kind: str           # prompt | assistant | tool_use | tool_result | meta | unknown
    timestamp: str
    body_preview: str


def _classify(ev: dict) -> tuple[str, str]:
    """Return (kind, preview_text)."""
    t = ev.get("type")
    msg = ev.get("message") or {}
    role = msg.get("role")
    content = msg.get("content")

    if t == "user" and role == "user":
        if isinstance(content, str):
            return ("prompt", content)
        if isinstance(content, list):
            for part in content:
                if isinstance(part, dict) and part.get("type") == "tool_result":
                    return ("tool_result", str(part.get("content", "")))
            texts = [p.get("text", "") for p in content
                     if isinstance(p, dict) and p.get("type") == "text"]
            return ("prompt", "\n".join(texts))
        return ("unknown", "")

    if t == "assistant" and role == "assistant":
        if isinstance(content, str):
            return ("assistant", content)
        if isinstance(content, list):
            for part in content:
                if isinstance(part, dict) and part.get("type") == "tool_use":
                    return ("tool_use", f"tool_use: {part.get('name', '?')}")
            texts = [p.get("text", "") for p in content
                     if isinstance(p, dict) and p.get("type") == "text"]
            return ("assistant", "\n".join(texts))

    return ("meta", json.dumps(ev)[:PREVIEW_LIMIT])


def stream(path: Path, offset: int = 0, limit: int = 200) -> list[EventView]:
    if not path.exists():
        return []
    out: list[EventView] = []
    with path.open("r", encoding="utf-8", errors="replace") as fp:
        for i, raw in enumerate(fp):
            if i < offset:
                continue
            if len(out) >= limit:
                break
            raw = raw.strip()
            if not raw:
                continue
            try:
                ev = json.loads(raw)
            except json.JSONDecodeError:
                continue
            kind, preview = _classify(ev)
            out.append(EventView(
                kind=kind,
                timestamp=ev.get("timestamp", ""),
                body_preview=preview[:PREVIEW_LIMIT],
            ))
    return out
