from __future__ import annotations
from dataclasses import dataclass
from pathlib import PurePosixPath
from typing import Iterable

WORKTREE_MARKERS = (".claude/worktrees", ".worktrees")


@dataclass
class FamilyOverride:
    name: str
    path_prefix: str


def collapse_worktree(cwd: str) -> str:
    """If cwd is inside a claude-code worktree, collapse to the parent repo root."""
    for marker in WORKTREE_MARKERS:
        idx = cwd.find("/" + marker + "/")
        if idx >= 0:
            return cwd[:idx]
    return cwd


def _is_prefix(path: str, prefix: str) -> bool:
    """Segment-aware prefix check: /a/b matches /a/b and /a/b/c but NOT /a/bb."""
    if path == prefix:
        return True
    return path.startswith(prefix.rstrip("/") + "/")


def _auto_family_name(collapsed: str) -> str:
    return PurePosixPath(collapsed).name or "unsorted"


def detect(cwds: Iterable[str], overrides: list[FamilyOverride]) -> dict[str, str]:
    """Map each cwd to a family name.

    Rules:
      1. Longest matching override path_prefix wins.
      2. Else, collapse worktree paths and use the last path segment.
      3. Empty / root cwd → 'unsorted'.
    """
    sorted_overrides = sorted(overrides, key=lambda o: len(o.path_prefix), reverse=True)

    out: dict[str, str] = {}
    for cwd in cwds:
        matched: str | None = None
        for ov in sorted_overrides:
            if _is_prefix(cwd, ov.path_prefix):
                matched = ov.name
                break
        if matched is None:
            collapsed = collapse_worktree(cwd)
            matched = _auto_family_name(collapsed) or "unsorted"
        out[cwd] = matched
    return out
