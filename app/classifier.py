# app/classifier.py
from __future__ import annotations
from datetime import datetime, timedelta
from pathlib import Path
from app.allowlists import is_harness_protected, is_kill_candidate
from app.models import Entry, Status, Verdict

ACTIVE_THRESHOLD = timedelta(days=7)


def classify(entry: Entry, now: datetime | None = None) -> Verdict:
    now = now or datetime.now()
    name = Path(entry.path).name

    if is_harness_protected(name):
        return Verdict(status=Status.HARNESS_PROTECTED, reason=f"'{name}' is a harness-owned path")
    if is_kill_candidate(name):
        return Verdict(status=Status.KILL_CANDIDATE, reason=f"'{name}' is a hardcoded kill-candidate")
    if (now - entry.mtime) < ACTIVE_THRESHOLD:
        return Verdict(status=Status.ACTIVE, reason="touched within 7 days")
    return Verdict(status=Status.UNKNOWN, reason="not on any allowlist; deny-by-default")
