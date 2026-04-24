import pytest
from app.families import detect, collapse_worktree, FamilyOverride


def test_collapse_claude_worktree():
    p = "/Users/jondev/dev/socratink/prod/socratink-app/.claude/worktrees/determined-bhaskara"
    assert collapse_worktree(p) == "/Users/jondev/dev/socratink/prod/socratink-app"


def test_collapse_dot_worktrees():
    p = "/Users/jondev/dev/socratink/prod/socratink-app/.worktrees/repair-reps-slice-b"
    assert collapse_worktree(p) == "/Users/jondev/dev/socratink/prod/socratink-app"


def test_collapse_noop_when_no_worktree():
    p = "/Users/jondev/dev/socratink/prod/socratink-app"
    assert collapse_worktree(p) == p


def test_detect_clusters_sibling_subdirs():
    cwds = [
        "/Users/jondev/dev/socratink/prod/socratink-app",
        "/Users/jondev/dev/socratink/prod/socratink-app/.claude/worktrees/determined-bhaskara",
        "/Users/jondev/dev/socratink/prod/socratink-landing",
    ]
    result = detect(cwds, overrides=[])
    assert "socratink-app" in result[cwds[0]].lower() or "socratink" in result[cwds[0]].lower()


def test_unsorted_fallback():
    cwds = ["/Users/jondev/tetris"]
    result = detect(cwds, overrides=[])
    assert cwds[0] in result


def test_override_longest_prefix_wins():
    cwds = ["/Users/jondev/dev/socratink/prod/socratink-app/docs"]
    overrides = [
        FamilyOverride(name="socratink", path_prefix="/Users/jondev/dev/socratink"),
        FamilyOverride(name="socratink-docs", path_prefix="/Users/jondev/dev/socratink/prod/socratink-app/docs"),
    ]
    result = detect(cwds, overrides=overrides)
    assert result[cwds[0]] == "socratink-docs"


def test_segment_aware_not_substring():
    # /Users/jondev/sockratink (typo variant) must NOT match override prefix /Users/jondev/socratink
    cwds = ["/Users/jondev/sockratink"]
    overrides = [FamilyOverride(name="socratink", path_prefix="/Users/jondev/socratink")]
    result = detect(cwds, overrides=overrides)
    assert result[cwds[0]] != "socratink"
