from __future__ import annotations
import json
from dataclasses import dataclass, field
from pathlib import Path

MAX_PROMPT_BYTES = 256_000
FIRST_PROMPT_CHARS = 200
TRUNCATE_SUFFIX = "[…truncated]"


@dataclass
class PromptRow:
    session_id: str
    timestamp: str
    content: str


@dataclass
class ParseError:
    line_number: int
    reason: str


@dataclass
class SessionMeta:
    session_id: str
    cwd: str | None = None
    started_at: str | None = None
    ended_at: str | None = None
    message_count: int = 0
    prompt_count: int = 0
    first_prompt: str | None = None


@dataclass
class ParseResult:
    session: SessionMeta
    prompts: list[PromptRow] = field(default_factory=list)
    errors: list[ParseError] = field(default_factory=list)


def _extract_prompt_text(msg: dict) -> str | None:
    """Return the human prompt text if this is a user prompt, else None."""
    if msg.get("role") != "user":
        return None
    content = msg.get("content")
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for part in content:
            if not isinstance(part, dict):
                continue
            if part.get("type") == "tool_result":
                return None
            if part.get("type") == "text" and isinstance(part.get("text"), str):
                parts.append(part["text"])
        if parts:
            return "\n".join(parts)
    return None


def _clamp(text: str) -> str:
    if len(text) <= MAX_PROMPT_BYTES:
        return text
    return text[:MAX_PROMPT_BYTES] + TRUNCATE_SUFFIX


def parse_file(path: Path) -> ParseResult:
    session_id = path.stem
    meta = SessionMeta(session_id=session_id)
    prompts: list[PromptRow] = []
    errors: list[ParseError] = []

    with path.open("r", encoding="utf-8", errors="replace") as fp:
        for i, raw in enumerate(fp, start=1):
            raw = raw.strip()
            if not raw:
                continue
            try:
                ev = json.loads(raw)
            except json.JSONDecodeError as e:
                errors.append(ParseError(line_number=i, reason=str(e)))
                continue

            meta.message_count += 1

            ts = ev.get("timestamp")
            if ts:
                if meta.started_at is None:
                    meta.started_at = ts
                meta.ended_at = ts

            if meta.cwd is None and ev.get("cwd"):
                meta.cwd = ev["cwd"]

            if ev.get("type") != "user" or ev.get("isMeta"):
                continue

            text = _extract_prompt_text(ev.get("message") or {})
            if text is None:
                continue

            meta.prompt_count += 1
            if meta.first_prompt is None:
                meta.first_prompt = text[:FIRST_PROMPT_CHARS]

            prompts.append(PromptRow(
                session_id=session_id,
                timestamp=ts or "",
                content=_clamp(text),
            ))

    return ParseResult(session=meta, prompts=prompts, errors=errors)
