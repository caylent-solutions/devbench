"""Named-session management for devbench (spec 4.4.1, issue #192).

Provides:

- :class:`Session` -- dataclass capturing all per-session metadata.
- :class:`SessionRegistry` -- read/write ``registry.json``; PID-file management;
  liveness check via ``os.kill(pid, 0)``.
- :func:`flock_backlog` -- context manager that acquires an exclusive ``fcntl.flock``
  on ``<workspace>/.devbench/BACKLOG.lock`` with a configurable timeout.
- :class:`ClaimRaceError` -- raised when a work unit's status changes underneath
  the BACKLOG.lock (another session won the race).
- :func:`detect_scope_overlap` -- returns IDs that appear in both the new scope and
  any existing session's scope.

All path literals are sourced from :mod:`devbench.constants`; no strings are
hard-coded in this module.

Raises:
    None -- this module does not raise at import time.
"""

from __future__ import annotations

import contextlib
import fcntl
import json
import os
import shutil
import time
from collections.abc import Generator
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from devbench.constants import (
    SESSION_BACKLOG_LOCK_NAME,
    SESSION_DEFAULT_FLOCK_TIMEOUT_SECONDS,
    SESSION_FLOCK_POLL_INTERVAL_SECONDS,
    SESSION_PID_FILENAME,
    SESSION_REGISTRY_PATH,
    SESSION_REGISTRY_TMP_SUFFIX,
)

# ---------------------------------------------------------------------------
# ClaimRaceError
# ---------------------------------------------------------------------------


class ClaimRaceError(Exception):
    """Raised when a work unit's status changed underneath the BACKLOG.lock.

    This indicates that another session claimed (or otherwise mutated) the
    work unit between the moment the caller decided to claim it and the moment
    the lock was acquired.  The caller should skip the unit and move on to the
    next candidate.

    Attributes:
        unit_id: The ID of the work unit that was under contention.
        expected_status: The status the caller expected to find.
        actual_status: The status that was actually found under the lock.
    """

    def __init__(self, unit_id: str, expected_status: str, actual_status: str) -> None:
        self.unit_id = unit_id
        self.expected_status = expected_status
        self.actual_status = actual_status
        super().__init__(
            f"Claim race on {unit_id!r}: expected status {expected_status!r} "
            f"but found {actual_status!r} under lock -- another session won the race."
        )


# ---------------------------------------------------------------------------
# Session dataclass
# ---------------------------------------------------------------------------


@dataclass
class Session:
    """All metadata for a single named devbench session.

    Attributes:
        name: Short, human-chosen session name (e.g. ``"alpha"``).
        pid: OS process ID of the orchestrator managing this session.
        scope: List of work-unit ID strings included in this session's scope
            (post-expansion, as written to scope.json).
        started_at: UTC-aware datetime when the session was started.
        started_by: OS username of the user who started the session.
        state_dir: Absolute path to ``<workspace>/.devbench/sessions/<name>/``.
    """

    name: str
    pid: int
    scope: list[str]
    started_at: datetime
    started_by: str
    state_dir: Path

    def to_dict(self) -> dict[str, Any]:
        """Serialise to a JSON-serialisable dict.

        Returns:
            dict with string keys ``name``, ``pid``, ``scope``,
            ``started_at`` (ISO 8601), ``started_by``, and ``state_dir``
            (str representation of the path).
        """
        return {
            "name": self.name,
            "pid": self.pid,
            "scope": self.scope,
            "started_at": self.started_at.isoformat(),
            "started_by": self.started_by,
            "state_dir": str(self.state_dir),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Session:
        """Deserialise from a dict produced by :meth:`to_dict`.

        Args:
            data: Dict containing all required keys.

        Returns:
            A :class:`Session` instance.

        Raises:
            KeyError: A required field is absent from *data*.
            ValueError: ``started_at`` is not a valid ISO 8601 datetime string.
        """
        raw_at: str = data["started_at"]
        try:
            started_at = datetime.fromisoformat(raw_at)
        except ValueError:
            raise ValueError(f"started_at {raw_at!r} is not a valid ISO 8601 datetime") from None

        # Normalise naive datetime to UTC-aware.
        started_at = started_at.replace(tzinfo=UTC) if started_at.tzinfo is None else started_at.astimezone(UTC)

        return cls(
            name=data["name"],
            pid=data["pid"],
            scope=data["scope"],
            started_at=started_at,
            started_by=data["started_by"],
            state_dir=Path(data["state_dir"]),
        )


# ---------------------------------------------------------------------------
# SessionRegistry
# ---------------------------------------------------------------------------


class SessionRegistry:
    """Read/write the session registry and manage per-session PID files.

    The registry is a JSON array written to
    ``<workspace_root>/.devbench/sessions/registry.json``.  Each element is a
    serialised :class:`Session` dict.  Writes use a temp-then-rename pattern
    so readers never observe a partial file.

    Liveness is checked via ``os.kill(pid, 0)``: ``ESRCH`` means the process
    is gone (STALE); ``EPERM`` means the process exists but we cannot signal it
    (treated as ACTIVE to avoid false reaping cross-user); any other
    :exc:`OSError` propagates unchanged.

    Args:
        workspace_root: Root directory of the devbench workspace.
    """

    def __init__(self, workspace_root: Path) -> None:
        self._workspace_root = workspace_root
        self._registry_path = workspace_root / SESSION_REGISTRY_PATH

    # ------------------------------------------------------------------
    # Registry file I/O
    # ------------------------------------------------------------------

    def load(self) -> list[Session]:
        """Load and deserialise the registry file.

        Returns:
            List of :class:`Session` objects; empty list when the file is absent.

        Raises:
            ValueError: The file contains invalid JSON or a non-list JSON root.
        """
        if not self._registry_path.exists():
            return []

        raw = self._registry_path.read_text(encoding="utf-8")
        try:
            data = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise ValueError(f"registry.json contains invalid JSON: {exc}") from exc

        if not isinstance(data, list):
            raise ValueError(f"registry.json must contain a JSON array, got {type(data).__name__}")

        return [Session.from_dict(entry) for entry in data]

    def save(self, sessions: list[Session]) -> None:
        """Serialise and atomically write *sessions* to the registry file.

        Creates ``<workspace>/.devbench/sessions/`` if absent.  Uses a
        write-to-tmp-then-rename strategy so readers never see a partial file.

        Args:
            sessions: List of :class:`Session` objects to persist.

        Raises:
            OSError: The write or rename step fails (e.g. disk full, permission
                denied).  The temp file is cleaned up before re-raising.
        """
        self._registry_path.parent.mkdir(parents=True, exist_ok=True)
        payload = json.dumps([s.to_dict() for s in sessions], indent=2)
        tmp = self._registry_path.with_suffix(SESSION_REGISTRY_TMP_SUFFIX)
        try:
            tmp.write_text(payload, encoding="utf-8")
        except Exception:
            with contextlib.suppress(OSError):
                tmp.unlink(missing_ok=True)
            raise
        tmp.replace(self._registry_path)

    # ------------------------------------------------------------------
    # PID-file helpers
    # ------------------------------------------------------------------

    def write_pid(self, state_dir: Path, pid: int) -> None:
        """Write *pid* to ``<state_dir>/pid``.

        Creates *state_dir* and any parents if absent.

        Args:
            state_dir: Session state directory.
            pid: OS process ID to write.

        Raises:
            OSError: The write fails.
        """
        state_dir.mkdir(parents=True, exist_ok=True)
        pid_path = state_dir / SESSION_PID_FILENAME
        pid_path.write_text(str(pid), encoding="utf-8")

    def delete_pid(self, state_dir: Path) -> None:
        """Remove ``<state_dir>/pid`` if it exists (idempotent).

        Args:
            state_dir: Session state directory.

        Raises:
            OSError: The unlink fails for a reason other than the file being absent.
        """
        pid_path = state_dir / SESSION_PID_FILENAME
        pid_path.unlink(missing_ok=True)

    def read_pid(self, state_dir: Path) -> int | None:
        """Read and return the PID from ``<state_dir>/pid``.

        Args:
            state_dir: Session state directory.

        Returns:
            Integer PID, or ``None`` when the file is absent.

        Raises:
            ValueError: The file content is not a valid integer.
        """
        pid_path = state_dir / SESSION_PID_FILENAME
        if not pid_path.exists():
            return None
        raw = pid_path.read_text(encoding="utf-8").strip()
        try:
            return int(raw)
        except ValueError:
            raise ValueError(f"PID file {pid_path} contains non-integer content: {raw!r}") from None

    # ------------------------------------------------------------------
    # Liveness
    # ------------------------------------------------------------------

    def is_alive(self, pid: int) -> bool:
        """Return ``True`` when *pid* refers to a running process.

        Uses ``os.kill(pid, 0)`` -- the standard POSIX no-op signal used to
        check process existence:

        - Success (no exception): process is alive.
        - :exc:`ProcessLookupError` (``ESRCH``): process does not exist -- STALE.
        - :exc:`PermissionError` (``EPERM``): process exists but we cannot signal
          it (cross-user) -- treated as ACTIVE to avoid false reaping.
        - Any other :exc:`OSError`: propagated unchanged to the caller.

        Args:
            pid: OS process ID to check.

        Returns:
            ``True`` if the process is running or unqueryable due to permissions;
            ``False`` if the process does not exist.

        Raises:
            OSError: An unexpected OS error occurred (not ESRCH, not EPERM).
        """
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return False
        except PermissionError:
            # EPERM: process exists, but we lack permission to signal it.
            return True
        return True

    def liveness_of_sessions(self, sessions: list[Session]) -> dict[str, str]:
        """Return a mapping from session name to ``"ACTIVE"`` or ``"STALE"``.

        Args:
            sessions: Sessions to check.

        Returns:
            Dict mapping ``session.name`` -> ``"ACTIVE"`` or ``"STALE"``.
        """
        return {s.name: ("ACTIVE" if self.is_alive(s.pid) else "STALE") for s in sessions}

    def cleanup_stale_sessions(self) -> list[str]:
        """Remove state directories and registry entries for STALE sessions.

        A session is STALE when :meth:`is_alive` returns ``False`` for its PID.
        For each STALE session:

        1. Remove its ``state_dir`` tree (if it exists) via ``shutil.rmtree``.
        2. Drop it from the in-memory session list.

        After processing, the updated list of surviving sessions is written back
        to the registry via :meth:`save`.  The operation is idempotent: if a
        stale session's ``state_dir`` was already absent, the registry entry is
        still removed.

        Returns:
            Sorted list of session names that were removed.

        Raises:
            OSError: :meth:`is_alive` raised an unexpected OS error, or
                ``shutil.rmtree`` failed to remove the state directory.
            ValueError: The registry file contains invalid JSON (propagated
                from :meth:`load`).
        """
        sessions = self.load()
        surviving: list[Session] = []
        removed_names: list[str] = []

        for session in sessions:
            if self.is_alive(session.pid):
                surviving.append(session)
            else:
                removed_names.append(session.name)
                if session.state_dir.exists():
                    shutil.rmtree(session.state_dir)

        if removed_names:
            self.save(surviving)

        return sorted(removed_names)


# ---------------------------------------------------------------------------
# flock_backlog
# ---------------------------------------------------------------------------


@contextlib.contextmanager
def flock_backlog(
    workspace_root: Path,
    timeout_seconds: int = SESSION_DEFAULT_FLOCK_TIMEOUT_SECONDS,
) -> Generator[None, None, None]:
    """Acquire an exclusive ``fcntl.flock`` on the BACKLOG.lock file.

    The lock file is ``<workspace_root>/.devbench/BACKLOG.lock``.  The
    directory is created if absent.  The lock is released when the context
    manager exits (normal or exceptional).

    The implementation uses a non-blocking ``LOCK_EX | LOCK_NB`` attempt
    inside a poll loop with sleeps of at most
    :data:`~devbench.constants.SESSION_FLOCK_POLL_INTERVAL_SECONDS` so that
    ``timeout_seconds`` is a hard upper bound on how long the caller waits.

    Args:
        workspace_root: Root directory of the devbench workspace.
        timeout_seconds: Maximum seconds to wait for the lock.
            Defaults to :data:`~devbench.constants.SESSION_DEFAULT_FLOCK_TIMEOUT_SECONDS`.

    Yields:
        ``None`` -- callers use ``with flock_backlog(root):`` without capturing the value.

    Raises:
        ValueError: *timeout_seconds* is not positive.
        TimeoutError: The lock could not be acquired within *timeout_seconds*.
        OSError: An unexpected OS error from ``fcntl.flock``.
    """
    if timeout_seconds <= 0:
        raise ValueError(
            f"timeout_seconds must be positive, got {timeout_seconds}. "
            f"Pass a value > 0 or omit to use the default "
            f"({SESSION_DEFAULT_FLOCK_TIMEOUT_SECONDS}s)."
        )
    lock_dir = workspace_root / ".devbench"
    lock_dir.mkdir(parents=True, exist_ok=True)
    lock_path = lock_dir / SESSION_BACKLOG_LOCK_NAME

    with lock_path.open("w") as lock_fd:
        deadline = time.monotonic() + timeout_seconds
        while True:
            try:
                fcntl.flock(lock_fd.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except BlockingIOError:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise TimeoutError(
                        f"Could not acquire BACKLOG.lock within {timeout_seconds}s. "
                        f"Another devbench session is holding the lock at "
                        f"{lock_path}. Retry or investigate the holding process."
                    ) from None
                time.sleep(min(SESSION_FLOCK_POLL_INTERVAL_SECONDS, remaining))
        try:
            yield None
        finally:
            fcntl.flock(lock_fd.fileno(), fcntl.LOCK_UN)


# ---------------------------------------------------------------------------
# detect_scope_overlap
# ---------------------------------------------------------------------------


def detect_scope_overlap(existing_sessions: list[Session], new_scope: list[str]) -> list[str]:
    """Return work-unit IDs present in both *new_scope* and any existing session's scope.

    Args:
        existing_sessions: Currently active or registered sessions.
        new_scope: The expanded list of work-unit IDs for the new session being started.

    Returns:
        Sorted list of conflicting IDs (each ID appears at most once).
    """
    existing_ids: set[str] = set()
    for session in existing_sessions:
        existing_ids.update(session.scope)

    new_set = set(new_scope)
    overlap = new_set & existing_ids
    return sorted(overlap)
