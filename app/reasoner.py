from __future__ import annotations
import os
from datetime import datetime
from pathlib import Path
from app.models import Entry

DEFAULT_CALL_CAP = 50


def _size_bucket(n: int) -> str:
    if n < 1_000_000:
        return "< 1 MB"
    if n < 100_000_000:
        return "1-100 MB"
    return "> 100 MB"


def _age_bucket(mtime: datetime, now: datetime | None = None) -> str:
    now = now or datetime.now()
    days = (now - mtime).days
    if days < 7:
        return "< 7 days"
    if days < 30:
        return "1-4 weeks"
    if days < 180:
        return "1-6 months"
    return "> 6 months"


def build_prompt(entry: Entry) -> str:
    name = Path(entry.path).name
    return (
        "Guess, in 1-2 sentences, the likely purpose of this filesystem entry. "
        "Reply with ONLY the guess, no preamble.\n\n"
        f"Name: {name}\n"
        f"Kind: {entry.kind.value}\n"
        f"Size: {_size_bucket(entry.size_bytes)}\n"
        f"Age: {_age_bucket(entry.mtime)}\n"
        f"File count: {entry.file_count}\n"
        f"Sample filenames: {', '.join(entry.sample_files) or '(none)'}\n"
    )


class Reasoner:
    """Provider-agnostic reasoner. Pass a `call_fn(prompt) -> str` strategy.

    Backwards-compatible: if a legacy `client` (Anthropic-shaped) is passed instead,
    it will be wrapped automatically.
    """
    def __init__(self, call_fn=None, client=None, call_cap: int = DEFAULT_CALL_CAP):
        if call_fn is None and client is not None:
            def _legacy(prompt: str) -> str:
                resp = client.messages.create(
                    model="claude-haiku-4-5",
                    max_tokens=120,
                    messages=[{"role": "user", "content": prompt}],
                )
                return resp.content[0].text.strip()
            call_fn = _legacy
        self.call_fn = call_fn
        self.call_cap = call_cap
        self._calls = 0
        self._cache: dict[tuple[str, str], str] = {}

    def purpose(self, entry: Entry) -> str:
        if os.environ.get("CLAUDE_TOOL_DISABLE_REASONER") == "1":
            return "(reasoner disabled)"
        if self.call_fn is None:
            return "(reasoner disabled)"

        key = (entry.path, entry.mtime.isoformat(timespec="seconds"))
        if key in self._cache:
            return self._cache[key]
        if self._calls >= self.call_cap:
            return "(not reasoned)"

        try:
            text = self.call_fn(build_prompt(entry))
        except Exception:
            text = "(reasoner unavailable)"

        self._calls += 1
        self._cache[key] = text
        return text
