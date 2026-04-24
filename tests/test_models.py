from datetime import datetime
from app.models import Entry, Verdict, Action, EntryKind, Status, ActionState


def test_entry_roundtrip():
    e = Entry(
        path="/Users/jondev/.claude/paste-cache",
        kind=EntryKind.DIR,
        inode=123456,
        size_bytes=4096,
        mtime=datetime(2026, 1, 1),
        file_count=84,
        sample_files=["a.txt", "b.txt"],
    )
    assert e.kind == "dir"
    assert e.sample_files == ["a.txt", "b.txt"]


def test_verdict_statuses():
    v = Verdict(status=Status.KILL_CANDIDATE, reason="matches paste-cache allowlist")
    assert v.status == "kill_candidate"


def test_action_state_enum():
    a = Action(
        scan_id=1,
        entry_id=2,
        ts=datetime.now(),
        action="delete",
        path="/tmp/x",
        state=ActionState.PLANNED,
    )
    assert a.state == "planned"
