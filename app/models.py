from __future__ import annotations
from datetime import datetime
from enum import Enum
from pydantic import BaseModel, Field


class EntryKind(str, Enum):
    DIR = "dir"
    FILE = "file"


class Status(str, Enum):
    HARNESS_PROTECTED = "harness_protected"
    KILL_CANDIDATE = "kill_candidate"
    ACTIVE = "active"
    UNKNOWN = "unknown"


class ActionState(str, Enum):
    PLANNED = "planned"
    EXECUTED = "executed"
    FAILED = "failed"
    SKIPPED = "skipped"


class Entry(BaseModel):
    id: int | None = None
    scan_id: int | None = None
    path: str
    kind: EntryKind
    inode: int
    size_bytes: int
    mtime: datetime
    file_count: int
    sample_files: list[str] = Field(default_factory=list)
    status: Status | None = None
    reason: str | None = None
    purpose: str | None = None
    user_decision: str | None = None


class Verdict(BaseModel):
    status: Status
    reason: str


class Action(BaseModel):
    id: int | None = None
    scan_id: int
    entry_id: int | None
    ts: datetime
    action: str  # archive | delete
    path: str
    archive_path: str | None = None
    state: ActionState
    error_detail: str | None = None
