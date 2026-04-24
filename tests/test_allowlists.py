from app.allowlists import HARNESS_PROTECTED, KILL_CANDIDATES, is_harness_protected, is_kill_candidate


def test_harness_names_present():
    for name in ["sessions", "projects", "history.jsonl", "settings.json", "hooks", "plugins", "skills"]:
        assert name in HARNESS_PROTECTED


def test_kill_candidate_names_present():
    for name in ["paste-cache", "backups", "skills-archive", "debug", "downloads", "file-history", ".DS_Store"]:
        assert name in KILL_CANDIDATES


def test_lookup_helpers():
    assert is_harness_protected("sessions") is True
    assert is_harness_protected("random-new-dir") is False
    assert is_kill_candidate("paste-cache") is True
    assert is_kill_candidate("sessions") is False


def test_no_overlap_between_lists():
    assert HARNESS_PROTECTED.isdisjoint(KILL_CANDIDATES)
