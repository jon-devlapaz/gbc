# tests/test_classifier.py
from datetime import datetime, timedelta
import pytest
from app.classifier import classify
from app.models import Entry, EntryKind, Status


def _entry(name: str, mtime: datetime, kind: EntryKind = EntryKind.DIR) -> Entry:
    return Entry(
        path=f"/Users/jondev/.claude/{name}",
        kind=kind,
        inode=1,
        size_bytes=0,
        mtime=mtime,
        file_count=0,
        sample_files=[],
    )


NOW = datetime.now()


@pytest.mark.parametrize("name", ["sessions", "settings.json", "plugins", "history.jsonl"])
def test_harness_names_protected(name):
    v = classify(_entry(name, NOW))
    assert v.status == Status.HARNESS_PROTECTED


@pytest.mark.parametrize("name", ["paste-cache", "backups", "file-history", ".DS_Store"])
def test_kill_candidate_names(name):
    v = classify(_entry(name, NOW))
    assert v.status == Status.KILL_CANDIDATE


def test_recent_unknown_is_active():
    v = classify(_entry("unknown-dir", NOW - timedelta(days=2)))
    assert v.status == Status.ACTIVE


def test_old_unknown_stays_unknown_not_dead():
    v = classify(_entry("mystery-dir", NOW - timedelta(days=200)))
    assert v.status == Status.UNKNOWN


def test_new_upstream_harness_addition_not_deletable():
    """Simulates a hypothetical new harness dir not yet in allowlist."""
    v = classify(_entry("brand-new-harness-thing", NOW - timedelta(days=400)))
    assert v.status == Status.UNKNOWN  # never kill_candidate


def test_reason_is_populated():
    v = classify(_entry("sessions", NOW))
    assert v.reason
