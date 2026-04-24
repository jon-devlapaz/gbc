# app/executor.py
from __future__ import annotations
import fcntl
import os
import subprocess
import sqlite3
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from app.allowlists import is_harness_protected
from app.models import ActionState, Status


class ExecutorError(RuntimeError):
    pass


@dataclass
class ActionRow:
    entry_id: int | None
    action: str
    path: str
    state: str
    error_detail: str | None = None


@dataclass
class RunResult:
    scan_id: int
    armed: bool
    archive_path: str | None = None
    executed: list[str] = field(default_factory=list)
    actions: list[ActionRow] = field(default_factory=list)


class Executor:
    def __init__(self, db: sqlite3.Connection, claude_root: Path, data_dir: Path):
        self.db = db
        self.claude_root = claude_root.resolve()
        self.data_dir = data_dir
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.lock_path = self.data_dir / ".scan.lock"

    def run(self, scan_id: int, entry_ids: list[int], armed: bool) -> RunResult:
        result = RunResult(scan_id=scan_id, armed=armed)

        with open(self.lock_path, "w") as lock_fp:
            fcntl.flock(lock_fp, fcntl.LOCK_EX)
            try:
                entries = self._load_entries(scan_id, entry_ids)
                approved = []
                for row in entries:
                    err = self._gate(row)
                    if err is not None:
                        action = ActionRow(
                            entry_id=row["id"], action="delete", path=row["path"],
                            state=ActionState.SKIPPED.value, error_detail=err,
                        )
                        self._write_action(scan_id, action)
                        result.actions.append(action)
                        continue
                    approved.append(row)

                if not approved:
                    return result

                if not armed:
                    for row in approved:
                        planned = ActionRow(
                            entry_id=row["id"], action="delete", path=row["path"],
                            state=ActionState.PLANNED.value,
                        )
                        self._write_action(scan_id, planned)
                        result.actions.append(planned)
                    return result

                archive_path = self._archive([row["path"] for row in approved])
                self._verify_archive(archive_path)
                result.archive_path = str(archive_path)

                for row in approved:
                    planned = ActionRow(
                        entry_id=row["id"], action="delete", path=row["path"],
                        state=ActionState.PLANNED.value,
                    )
                    action_id = self._write_action(scan_id, planned, archive_path=str(archive_path))
                    try:
                        subprocess.run(["rm", "-rf", "--", row["path"]], shell=False, check=True)
                        self._update_action(action_id, ActionState.EXECUTED.value)
                        result.actions.append(ActionRow(
                            entry_id=row["id"], action="delete", path=row["path"],
                            state=ActionState.EXECUTED.value,
                        ))
                        result.executed.append(row["path"])
                    except subprocess.CalledProcessError as e:
                        self._update_action(action_id, ActionState.FAILED.value, str(e))
                        result.actions.append(ActionRow(
                            entry_id=row["id"], action="delete", path=row["path"],
                            state=ActionState.FAILED.value, error_detail=str(e),
                        ))

                return result
            finally:
                fcntl.flock(lock_fp, fcntl.LOCK_UN)

    def _load_entries(self, scan_id: int, entry_ids: list[int]) -> list[sqlite3.Row]:
        if not entry_ids:
            return []
        placeholders = ",".join("?" * len(entry_ids))
        rows = self.db.execute(
            f"SELECT * FROM entries WHERE scan_id=? AND id IN ({placeholders})",
            (scan_id, *entry_ids),
        ).fetchall()
        return list(rows)

    def _gate(self, row: sqlite3.Row) -> str | None:
        path = row["path"]
        name = Path(path).name

        if row["status"] != Status.KILL_CANDIDATE.value:
            return f"status {row['status']} is not kill_candidate"
        if is_harness_protected(name):
            return f"'{name}' is harness_protected; refuse regardless of approval"

        try:
            real = Path(os.path.realpath(path))
        except OSError as e:
            return f"realpath failed: {e}"
        try:
            real.relative_to(self.claude_root)
        except ValueError:
            return f"realpath {real} is outside {self.claude_root}"

        try:
            st = os.stat(path, follow_symlinks=False)
        except FileNotFoundError:
            return "path no longer exists"
        if st.st_ino != row["inode"]:
            return f"inode mismatch (recorded {row['inode']}, now {st.st_ino})"
        recorded = datetime.fromisoformat(row["mtime"])
        now_mtime = datetime.fromtimestamp(st.st_mtime)
        if abs((now_mtime - recorded).total_seconds()) > 1.0:
            return f"mtime drifted (recorded {recorded}, now {now_mtime})"
        return None

    def _archive(self, paths: list[str]) -> Path:
        stamp = datetime.now().strftime("%Y%m%d-%H%M")
        archive = Path.home() / f".claude-archive-{stamp}.tar.gz"
        subprocess.run(["tar", "czf", str(archive), "--", *paths], shell=False, check=True)
        return archive

    def _verify_archive(self, archive: Path) -> None:
        try:
            subprocess.run(["tar", "tzf", str(archive)], shell=False, check=True, capture_output=True)
        except subprocess.CalledProcessError as e:
            raise ExecutorError(f"archive integrity check failed: {e.stderr!r}") from e

    def _write_action(self, scan_id: int, row: ActionRow, archive_path: str | None = None) -> int:
        cur = self.db.execute(
            "INSERT INTO actions(scan_id,entry_id,ts,action,path,archive_path,state,error_detail) "
            "VALUES (?,?,?,?,?,?,?,?)",
            (scan_id, row.entry_id, datetime.now().isoformat(), row.action,
             row.path, archive_path, row.state, row.error_detail),
        )
        self.db.commit()
        return cur.lastrowid

    def _update_action(self, action_id: int, state: str, error_detail: str | None = None) -> None:
        self.db.execute(
            "UPDATE actions SET state=?, error_detail=? WHERE id=?",
            (state, error_detail, action_id),
        )
        self.db.commit()
