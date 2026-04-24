# app/allowlists.py
"""Hardcoded name allowlists for the classifier.

Changes here leave a clear git diff. Do not make these config-driven.
"""

HARNESS_PROTECTED: frozenset[str] = frozenset({
    "sessions",
    "projects",
    "history.jsonl",
    "settings.json",
    "settings.local.json",
    "hooks",
    "ide",
    "shell-snapshots",
    "session-env",
    "mcp.json",
    "statusline-command.sh",
    "cache",
    "cowork_plugins",
    "cowork_settings.json",
    "telemetry",
    "usage-data",
    "plugins",
    "commands",
    "agents",
    "skills",
})

KILL_CANDIDATES: frozenset[str] = frozenset({
    "paste-cache",
    ".window-cleaner-backups",
    "backups",
    "skills-archive",
    "debug",
    "downloads",
    "file-history",
    ".DS_Store",
    "stats-cache.json",
    "RTK.md",
})


def is_harness_protected(name: str) -> bool:
    return name in HARNESS_PROTECTED


def is_kill_candidate(name: str) -> bool:
    return name in KILL_CANDIDATES
