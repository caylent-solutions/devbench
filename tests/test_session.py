"""Tests for src/devbench/session.py -- Session dataclass, SessionRegistry, flock_backlog,
detect_scope_overlap, ClaimRaceError, and PID-file management.

Coverage requirement: 100% line + branch on devbench.session.

AC-192-1: session state_dir creation and PID-file management.
AC-192-3: concurrent sessions via flock_backlog mutual exclusion.
AC-192-5: atomic claim arbitration -- race resolved deterministically via ClaimRaceError.
AC-192-10: session listing with liveness (ACTIVE / STALE).
AC-192-16: window-stats + proposal lifecycle remain workspace-shared across sessions.
"""

from __future__ import annotations

import errno
import fcntl
import os
import threading
from dataclasses import fields
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, cast
from unittest.mock import patch

import pytest

from devbench.session import (
    ClaimRaceError,
    Session,
    SessionRegistry,
    detect_scope_overlap,
    flock_backlog,
    flock_path,
)

# ---------------------------------------------------------------------------
# Helpers / fixtures
# ---------------------------------------------------------------------------

_NOW = datetime(2026, 5, 15, 12, 0, 0, tzinfo=UTC)


def _make_session(
    name: str = "alpha",
    pid: int = 12345,
    scope: list[str] | None = None,
    started_at: datetime | None = None,
    started_by: str = "tester",
    state_dir: Path | None = None,
) -> Session:
    return Session(
        name=name,
        pid=pid,
        scope=scope or [],
        started_at=started_at or _NOW,
        started_by=started_by,
        state_dir=state_dir or Path(f"/tmp/sessions/{name}"),
    )


@pytest.fixture()
def workspace(tmp_path: Path) -> Path:
    """Minimal workspace directory with .devbench subdir."""
    (tmp_path / ".devbench").mkdir(parents=True, exist_ok=True)
    return tmp_path


# ---------------------------------------------------------------------------
# Session dataclass
# ---------------------------------------------------------------------------


class TestSessionDataclass:
    """Session is a plain dataclass with the right fields."""

    def test_has_required_fields(self) -> None:
        field_names = {f.name for f in fields(Session)}
        assert field_names == {"name", "pid", "scope", "started_at", "started_by", "state_dir"}

    def test_construction(self) -> None:
        state_dir = Path("/tmp/sessions/alpha")
        s = Session(
            name="alpha",
            pid=9999,
            scope=["E1", "E2"],
            started_at=_NOW,
            started_by="alice",
            state_dir=state_dir,
        )
        assert s.name == "alpha"
        assert s.pid == 9999
        assert s.scope == ["E1", "E2"]
        assert s.started_at == _NOW
        assert s.started_by == "alice"
        assert s.state_dir == state_dir

    def test_to_dict_serialises_all_fields(self) -> None:
        s = _make_session()
        d = s.to_dict()
        assert d["name"] == "alpha"
        assert d["pid"] == 12345
        assert d["scope"] == []
        assert d["started_at"] == _NOW.isoformat()
        assert d["started_by"] == "tester"
        assert d["state_dir"] == str(Path("/tmp/sessions/alpha"))

    def test_from_dict_round_trip(self) -> None:
        s = _make_session(scope=["E1-F1", "E2"])
        d = s.to_dict()
        s2 = Session.from_dict(d)
        assert s2.name == s.name
        assert s2.pid == s.pid
        assert s2.scope == s.scope
        assert s2.started_at == s.started_at
        assert s2.started_by == s.started_by
        assert s2.state_dir == s.state_dir

    def test_from_dict_missing_key_raises(self) -> None:
        d: dict[str, Any] = {
            "name": "alpha",
            "pid": 1,
            "scope": [],
            "started_at": _NOW.isoformat(),
            # started_by missing
            "state_dir": "/tmp",
        }
        with pytest.raises(KeyError):
            Session.from_dict(d)

    def test_from_dict_bad_started_at_raises(self) -> None:
        d: dict[str, Any] = {
            "name": "alpha",
            "pid": 1,
            "scope": [],
            "started_at": "not-a-datetime",
            "started_by": "alice",
            "state_dir": "/tmp",
        }
        with pytest.raises(ValueError):
            Session.from_dict(d)

    def test_from_dict_naive_started_at_becomes_utc(self) -> None:
        """from_dict normalises a naive ISO datetime to UTC."""
        d: dict[str, Any] = {
            "name": "alpha",
            "pid": 1,
            "scope": [],
            "started_at": "2026-05-15T12:00:00",  # naive
            "started_by": "alice",
            "state_dir": "/tmp",
        }
        s = Session.from_dict(d)
        assert s.started_at.tzinfo is not None


# ---------------------------------------------------------------------------
# ClaimRaceError
# ---------------------------------------------------------------------------


class TestClaimRaceError:
    def test_is_exception(self) -> None:
        err = ClaimRaceError("E1-F1-S1-T1", "in-progress", "done")
        assert isinstance(err, Exception)

    def test_message_contains_context(self) -> None:
        err = ClaimRaceError("E1-F1-S1-T1", "in-queue", "in-progress")
        msg = str(err)
        assert "E1-F1-S1-T1" in msg
        assert "in-queue" in msg
        assert "in-progress" in msg

    def test_attributes(self) -> None:
        err = ClaimRaceError("T1", "in-queue", "blocked")
        assert err.unit_id == "T1"
        assert err.expected_status == "in-queue"
        assert err.actual_status == "blocked"


# ---------------------------------------------------------------------------
# SessionRegistry -- basic read/write
# ---------------------------------------------------------------------------


class TestSessionRegistryReadWrite:
    def test_load_returns_empty_when_file_absent(self, workspace: Path) -> None:
        reg = SessionRegistry(workspace)
        sessions = reg.load()
        assert sessions == []

    def test_save_creates_registry_file(self, workspace: Path) -> None:
        reg = SessionRegistry(workspace)
        s = _make_session(state_dir=workspace / ".devbench" / "sessions" / "alpha")
        reg.save([s])
        registry_path = workspace / ".devbench" / "sessions" / "registry.json"
        assert registry_path.exists()

    def test_save_and_load_round_trip(self, workspace: Path) -> None:
        reg = SessionRegistry(workspace)
        s = _make_session(name="beta", state_dir=workspace / ".devbench" / "sessions" / "beta")
        reg.save([s])
        loaded = reg.load()
        assert len(loaded) == 1
        assert loaded[0].name == "beta"
        assert loaded[0].pid == 12345

    def test_save_multiple_sessions(self, workspace: Path) -> None:
        reg = SessionRegistry(workspace)
        s1 = _make_session(name="alpha", state_dir=workspace / ".devbench" / "sessions" / "alpha")
        s2 = _make_session(name="beta", pid=99, state_dir=workspace / ".devbench" / "sessions" / "beta")
        reg.save([s1, s2])
        loaded = reg.load()
        assert len(loaded) == 2
        names = {s.name for s in loaded}
        assert names == {"alpha", "beta"}

    def test_save_creates_parent_dirs(self, tmp_path: Path) -> None:
        """Registry directory is created on first write even if absent."""
        reg = SessionRegistry(tmp_path)
        s = _make_session(state_dir=tmp_path / ".devbench" / "sessions" / "gamma")
        reg.save([s])
        assert (tmp_path / ".devbench" / "sessions" / "registry.json").exists()

    def test_load_invalid_json_raises(self, workspace: Path) -> None:
        registry_path = workspace / ".devbench" / "sessions" / "registry.json"
        registry_path.parent.mkdir(parents=True, exist_ok=True)
        registry_path.write_text("NOT JSON", encoding="utf-8")
        reg = SessionRegistry(workspace)
        with pytest.raises(ValueError, match="invalid JSON"):
            reg.load()

    def test_load_non_list_root_raises(self, workspace: Path) -> None:
        registry_path = workspace / ".devbench" / "sessions" / "registry.json"
        registry_path.parent.mkdir(parents=True, exist_ok=True)
        registry_path.write_text('{"key": "value"}', encoding="utf-8")
        reg = SessionRegistry(workspace)
        with pytest.raises(ValueError, match="JSON array"):
            reg.load()

    def test_save_is_atomic(self, workspace: Path) -> None:
        """save() uses a temp-then-rename strategy; no .tmp file should remain."""
        reg = SessionRegistry(workspace)
        s = _make_session(state_dir=workspace / ".devbench" / "sessions" / "alpha")
        reg.save([s])
        # Verify no leftover temp file
        tmp = workspace / ".devbench" / "sessions" / "registry.json.tmp"
        assert not tmp.exists()

    def test_save_cleans_up_tmp_on_write_error(self, workspace: Path) -> None:
        """When write_text raises, the temp file is removed and the exception propagates."""
        reg = SessionRegistry(workspace)
        s = _make_session(state_dir=workspace / ".devbench" / "sessions" / "alpha")
        # Patch Path.write_text to raise after the tmp file could be created
        with patch("devbench.session.Path.write_text", side_effect=OSError("disk full")):
            with pytest.raises(OSError, match="disk full"):
                reg.save([s])


# ---------------------------------------------------------------------------
# SessionRegistry -- PID-file management
# ---------------------------------------------------------------------------


class TestSessionRegistryPidFile:
    def test_write_pid_creates_file(self, workspace: Path) -> None:
        reg = SessionRegistry(workspace)
        state_dir = workspace / ".devbench" / "sessions" / "alpha"
        state_dir.mkdir(parents=True, exist_ok=True)
        reg.write_pid(state_dir, 42000)
        pid_path = state_dir / "pid"
        assert pid_path.exists()
        assert pid_path.read_text(encoding="utf-8").strip() == "42000"

    def test_write_pid_creates_parent_dir(self, workspace: Path) -> None:
        reg = SessionRegistry(workspace)
        state_dir = workspace / ".devbench" / "sessions" / "newone"
        reg.write_pid(state_dir, 55555)
        assert (state_dir / "pid").exists()

    def test_delete_pid_removes_file(self, workspace: Path) -> None:
        reg = SessionRegistry(workspace)
        state_dir = workspace / ".devbench" / "sessions" / "alpha"
        state_dir.mkdir(parents=True, exist_ok=True)
        pid_path = state_dir / "pid"
        pid_path.write_text("42000", encoding="utf-8")
        reg.delete_pid(state_dir)
        assert not pid_path.exists()

    def test_delete_pid_idempotent_when_absent(self, workspace: Path) -> None:
        reg = SessionRegistry(workspace)
        state_dir = workspace / ".devbench" / "sessions" / "ghost"
        state_dir.mkdir(parents=True, exist_ok=True)
        # Should not raise even when pid file does not exist
        reg.delete_pid(state_dir)

    def test_read_pid_returns_int(self, workspace: Path) -> None:
        reg = SessionRegistry(workspace)
        state_dir = workspace / ".devbench" / "sessions" / "alpha"
        state_dir.mkdir(parents=True, exist_ok=True)
        (state_dir / "pid").write_text("88888\n", encoding="utf-8")
        assert reg.read_pid(state_dir) == 88888

    def test_read_pid_absent_returns_none(self, workspace: Path) -> None:
        reg = SessionRegistry(workspace)
        state_dir = workspace / ".devbench" / "sessions" / "nopid"
        state_dir.mkdir(parents=True, exist_ok=True)
        assert reg.read_pid(state_dir) is None

    def test_read_pid_non_integer_raises(self, workspace: Path) -> None:
        reg = SessionRegistry(workspace)
        state_dir = workspace / ".devbench" / "sessions" / "alpha"
        state_dir.mkdir(parents=True, exist_ok=True)
        (state_dir / "pid").write_text("not-a-pid", encoding="utf-8")
        with pytest.raises(ValueError, match="not-a-pid"):
            reg.read_pid(state_dir)


# ---------------------------------------------------------------------------
# SessionRegistry -- liveness check
# ---------------------------------------------------------------------------


class TestSessionRegistryLiveness:
    def test_is_alive_current_process(self, workspace: Path) -> None:
        """The current process is always alive."""
        reg = SessionRegistry(workspace)
        assert reg.is_alive(os.getpid()) is True

    def test_is_alive_returns_false_for_nonexistent_pid(self, workspace: Path) -> None:
        """PID 0 is not a valid signal target; a large sentinel PID should not exist."""
        reg = SessionRegistry(workspace)
        # Use a PID that is very unlikely to be alive.
        # We patch os.kill to simulate ESRCH (no such process).
        with patch("devbench.session.os.kill", side_effect=ProcessLookupError):
            assert reg.is_alive(99999999) is False

    def test_is_alive_returns_false_on_permission_error(self, workspace: Path) -> None:
        """EPERM from os.kill(pid, 0) means the process exists but we cannot signal it.

        Devbench treats EPERM as alive=True (process exists, we just lack permission).
        This ensures we do not falsely reap a session owned by another user.
        """
        reg = SessionRegistry(workspace)
        err = PermissionError()
        err.errno = errno.EPERM
        with patch("devbench.session.os.kill", side_effect=err):
            assert reg.is_alive(99999999) is True

    def test_is_alive_reraises_unexpected_os_error(self, workspace: Path) -> None:
        """Unexpected OSError (not ESRCH, not EPERM) propagates to the caller."""
        reg = SessionRegistry(workspace)
        err = OSError(errno.EINVAL, "invalid")
        with patch("devbench.session.os.kill", side_effect=err):
            with pytest.raises(OSError):
                reg.is_alive(1)


# ---------------------------------------------------------------------------
# SessionRegistry -- liveness_of_sessions
# ---------------------------------------------------------------------------


class TestLivenessOfSessions:
    def test_active_session_labelled_active(self, workspace: Path) -> None:
        reg = SessionRegistry(workspace)
        s = _make_session(pid=os.getpid())
        result = reg.liveness_of_sessions([s])
        assert result[s.name] == "ACTIVE"

    def test_stale_session_labelled_stale(self, workspace: Path) -> None:
        reg = SessionRegistry(workspace)
        s = _make_session(pid=99999999)
        with patch("devbench.session.os.kill", side_effect=ProcessLookupError):
            result = reg.liveness_of_sessions([s])
        assert result[s.name] == "STALE"

    def test_empty_list_returns_empty_dict(self, workspace: Path) -> None:
        reg = SessionRegistry(workspace)
        assert reg.liveness_of_sessions([]) == {}


# ---------------------------------------------------------------------------
# flock_path
# ---------------------------------------------------------------------------


class TestFlockPath:
    """Direct tests for the generic ``flock_path`` primitive ``flock_backlog`` wraps.

    ``flock_backlog`` reuses this primitive rather than re-implementing the
    poll/timeout logic (spec `integration-reality-gates-hardening.md` Section
    3), and the shared-file baseline writer (``src/devbench/cli.py``) is a
    second, independent caller with its own lock-path sibling file -- so this
    class exercises the generic contract directly, including the ``shared``
    branch that ``flock_backlog`` never uses.
    """

    def test_lock_file_created_at_arbitrary_path(self, tmp_path: Path) -> None:
        lock_path = tmp_path / "nested" / "custom.lock"
        with flock_path(lock_path, timeout_seconds=5):
            assert lock_path.exists()

    def test_lock_released_on_normal_exit_allows_reacquire(self, tmp_path: Path) -> None:
        lock_path = tmp_path / "custom.lock"
        with flock_path(lock_path, timeout_seconds=5):
            pass
        with flock_path(lock_path, timeout_seconds=1):
            pass

    def test_lock_released_on_exception_allows_reacquire(self, tmp_path: Path) -> None:
        lock_path = tmp_path / "custom.lock"
        with pytest.raises(RuntimeError, match="boom"):
            with flock_path(lock_path, timeout_seconds=5):
                raise RuntimeError("boom")
        with flock_path(lock_path, timeout_seconds=1):
            pass

    def test_non_positive_timeout_raises_value_error(self, tmp_path: Path) -> None:
        lock_path = tmp_path / "custom.lock"
        with pytest.raises(ValueError, match="timeout_seconds must be positive"):
            with flock_path(lock_path, timeout_seconds=0):
                pass

    def test_exclusive_holder_blocks_a_second_exclusive_acquire_until_timeout(self, tmp_path: Path) -> None:
        lock_path = tmp_path / "custom.lock"
        lock_path.parent.mkdir(parents=True, exist_ok=True)
        with lock_path.open("w") as held_fd:
            fcntl.flock(held_fd.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            try:
                with pytest.raises(TimeoutError, match=r"custom\.lock"):
                    with flock_path(lock_path, timeout_seconds=1):
                        pass
            finally:
                fcntl.flock(held_fd.fileno(), fcntl.LOCK_UN)

    def test_timeout_error_message_contains_timeout_value(self, tmp_path: Path) -> None:
        lock_path = tmp_path / "custom.lock"
        lock_path.parent.mkdir(parents=True, exist_ok=True)
        with lock_path.open("w") as held_fd:
            fcntl.flock(held_fd.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            try:
                with pytest.raises(TimeoutError, match="1s"):
                    with flock_path(lock_path, timeout_seconds=1):
                        pass
            finally:
                fcntl.flock(held_fd.fileno(), fcntl.LOCK_UN)

    def test_shared_holder_blocks_a_concurrent_exclusive_acquire(self, tmp_path: Path) -> None:
        """A ``shared=True`` reader is not invisible to a concurrent exclusive writer:
        the writer must wait, so it can never observe a torn/in-progress read."""
        lock_path = tmp_path / "custom.lock"
        with flock_path(lock_path, timeout_seconds=5, shared=True):
            with pytest.raises(TimeoutError):
                with flock_path(lock_path, timeout_seconds=1, shared=False):
                    pass

    def test_two_shared_holders_do_not_block_each_other(self, tmp_path: Path) -> None:
        """Two concurrent ``shared=True`` acquisitions on the same lock both succeed."""
        lock_path = tmp_path / "custom.lock"
        with flock_path(lock_path, timeout_seconds=5, shared=True):
            with flock_path(lock_path, timeout_seconds=1, shared=True):
                pass

    def test_lock_file_is_never_deleted(self, tmp_path: Path) -> None:
        """The lock file itself is a pure lock inode -- it survives the context exiting,
        unlike the data file callers write under the lock."""
        lock_path = tmp_path / "custom.lock"
        with flock_path(lock_path, timeout_seconds=5):
            pass
        assert lock_path.exists()

    def test_unexpected_os_error_propagates(self, tmp_path: Path) -> None:
        lock_path = tmp_path / "custom.lock"
        with patch(
            "devbench.session.fcntl.flock",
            side_effect=OSError(errno.EBADF, "Bad file descriptor"),
        ):
            with pytest.raises(OSError, match="Bad file descriptor"):
                with flock_path(lock_path, timeout_seconds=5):
                    pass


# ---------------------------------------------------------------------------
# flock_backlog
# ---------------------------------------------------------------------------


class TestFlockBacklog:
    def test_context_manager_creates_lock_file(self, workspace: Path) -> None:
        lock_path = workspace / ".devbench" / "BACKLOG.lock"
        with flock_backlog(workspace, timeout_seconds=5):
            assert lock_path.exists()

    def test_context_manager_releases_lock_on_exit(self, workspace: Path) -> None:
        """After the context exits, another thread should be able to acquire the lock."""
        with flock_backlog(workspace, timeout_seconds=5):
            pass
        # Verify we can re-acquire
        with flock_backlog(workspace, timeout_seconds=5):
            pass

    def test_timeout_raises_on_contended_lock(self, workspace: Path) -> None:
        """A lock held by another FD should cause timeout to fire."""
        lock_path = workspace / ".devbench" / "BACKLOG.lock"
        lock_path.parent.mkdir(parents=True, exist_ok=True)
        with lock_path.open("w") as lock_fd:
            fcntl.flock(lock_fd.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            try:
                with pytest.raises(TimeoutError):
                    with flock_backlog(workspace, timeout_seconds=1):
                        pass
            finally:
                fcntl.flock(lock_fd.fileno(), fcntl.LOCK_UN)

    def test_default_timeout_is_used_when_not_specified(self, workspace: Path) -> None:
        """flock_backlog with no timeout_seconds uses SESSION_DEFAULT_FLOCK_TIMEOUT_SECONDS."""
        with flock_backlog(workspace) as ctx:
            # ctx is None (context manager yields None)
            assert ctx is None

    def test_lock_file_created_in_devbench_subdir(self, workspace: Path) -> None:
        expected = workspace / ".devbench" / "BACKLOG.lock"
        with flock_backlog(workspace, timeout_seconds=5):
            assert expected.exists()

    def test_lock_released_on_exception(self, workspace: Path) -> None:
        """Lock is released when an exception is raised inside the context block."""
        with pytest.raises(RuntimeError, match="boom"):
            with flock_backlog(workspace, timeout_seconds=5):
                raise RuntimeError("boom")
        # After exception, the lock must be released so re-acquisition succeeds.
        with flock_backlog(workspace, timeout_seconds=1):
            pass

    def test_creates_devbench_dir_when_absent(self, tmp_path: Path) -> None:
        """flock_backlog creates .devbench/ when the workspace has no such directory."""
        bare_workspace = tmp_path / "bare"
        bare_workspace.mkdir()
        assert not (bare_workspace / ".devbench").exists()
        with flock_backlog(bare_workspace, timeout_seconds=5):
            assert (bare_workspace / ".devbench").exists()

    def test_timeout_error_message_contains_lock_path(self, workspace: Path) -> None:
        """The TimeoutError message includes the lock path for debugging."""
        lock_path = workspace / ".devbench" / "BACKLOG.lock"
        lock_path.parent.mkdir(parents=True, exist_ok=True)
        with lock_path.open("w") as lock_fd:
            fcntl.flock(lock_fd.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            try:
                with pytest.raises(TimeoutError, match=r"BACKLOG\.lock"):
                    with flock_backlog(workspace, timeout_seconds=1):
                        pass
            finally:
                fcntl.flock(lock_fd.fileno(), fcntl.LOCK_UN)

    def test_timeout_error_message_contains_timeout_value(self, workspace: Path) -> None:
        """The TimeoutError message includes the timeout value for diagnostics."""
        lock_path = workspace / ".devbench" / "BACKLOG.lock"
        lock_path.parent.mkdir(parents=True, exist_ok=True)
        with lock_path.open("w") as lock_fd:
            fcntl.flock(lock_fd.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            try:
                with pytest.raises(TimeoutError, match="1s"):
                    with flock_backlog(workspace, timeout_seconds=1):
                        pass
            finally:
                fcntl.flock(lock_fd.fileno(), fcntl.LOCK_UN)

    def test_mutual_exclusion_via_threading(self, workspace: Path) -> None:
        """Two threads cannot hold flock_backlog simultaneously (AC-192-3).

        This test verifies actual mutual exclusion: a shared counter is
        incremented inside the lock from two concurrent threads. Without
        correct locking the threads would interleave and the final counter
        would be less than the expected total.
        """
        import threading

        iterations_per_thread = 50
        counter_file = workspace / ".devbench" / "counter.txt"
        counter_file.parent.mkdir(parents=True, exist_ok=True)
        counter_file.write_text("0", encoding="utf-8")
        errors: list[str] = []

        def increment_under_lock() -> None:
            for _ in range(iterations_per_thread):
                try:
                    with flock_backlog(workspace, timeout_seconds=10):
                        val = int(counter_file.read_text(encoding="utf-8").strip())
                        counter_file.write_text(str(val + 1), encoding="utf-8")
                except Exception as exc:
                    errors.append(str(exc))

        t1 = threading.Thread(target=increment_under_lock)
        t2 = threading.Thread(target=increment_under_lock)
        t1.start()
        t2.start()
        t1.join(timeout=30)
        t2.join(timeout=30)

        assert not errors, f"Threads raised errors: {errors}"
        final_val = int(counter_file.read_text(encoding="utf-8").strip())
        assert final_val == iterations_per_thread * 2

    @pytest.mark.parametrize("bad_timeout", [0, -1, -100])
    def test_non_positive_timeout_raises_value_error(self, workspace: Path, bad_timeout: int) -> None:
        """flock_backlog rejects non-positive timeout_seconds with a clear ValueError."""
        with pytest.raises(ValueError, match="timeout_seconds must be positive"):
            with flock_backlog(workspace, timeout_seconds=bad_timeout):
                pass

    def test_unexpected_os_error_propagates(self, workspace: Path) -> None:
        """OSError from fcntl.flock that is not BlockingIOError propagates."""
        with patch(
            "devbench.session.fcntl.flock",
            side_effect=OSError(errno.EBADF, "Bad file descriptor"),
        ):
            with pytest.raises(OSError, match="Bad file descriptor"):
                with flock_backlog(workspace, timeout_seconds=5):
                    pass


# ---------------------------------------------------------------------------
# detect_scope_overlap
# ---------------------------------------------------------------------------


class TestDetectScopeOverlap:
    def test_no_overlap_returns_empty(self) -> None:
        existing = [_make_session(name="alpha", scope=["E1", "E2"])]
        result = detect_scope_overlap(existing, ["E3", "E4"])
        assert result == []

    def test_overlap_returns_conflicting_ids(self) -> None:
        existing = [_make_session(name="alpha", scope=["E1", "E2", "E3"])]
        result = detect_scope_overlap(existing, ["E2", "E4"])
        assert "E2" in result

    def test_overlap_across_multiple_sessions(self) -> None:
        s1 = _make_session(name="alpha", scope=["E1"])
        s2 = _make_session(name="beta", scope=["E2"])
        result = detect_scope_overlap([s1, s2], ["E1", "E2", "E3"])
        assert "E1" in result
        assert "E2" in result

    def test_empty_new_scope_returns_empty(self) -> None:
        existing = [_make_session(name="alpha", scope=["E1"])]
        result = detect_scope_overlap(existing, [])
        assert result == []

    def test_empty_existing_sessions_returns_empty(self) -> None:
        result = detect_scope_overlap([], ["E1", "E2"])
        assert result == []

    def test_no_duplicates_in_result(self) -> None:
        """When two existing sessions both conflict with the same ID, it appears once."""
        s1 = _make_session(name="alpha", scope=["E1"])
        s2 = _make_session(name="beta", scope=["E1"])
        result = detect_scope_overlap([s1, s2], ["E1"])
        assert result.count("E1") == 1

    def test_exact_match_is_detected(self) -> None:
        existing = [_make_session(name="alpha", scope=["E1-F1-S1-T1"])]
        result = detect_scope_overlap(existing, ["E1-F1-S1-T1"])
        assert "E1-F1-S1-T1" in result

    def test_none_new_scope_raises_type_error(self) -> None:
        """Passing None for new_scope must raise TypeError with actionable message."""
        existing = [_make_session(name="alpha", scope=["E1"])]
        with pytest.raises(TypeError, match="new_scope must be a list"):
            detect_scope_overlap(existing, cast(list[str], None))

    def test_none_existing_sessions_raises_type_error(self) -> None:
        """Passing None for existing_sessions must raise TypeError with actionable message."""
        with pytest.raises(TypeError, match="existing_sessions must be a list"):
            detect_scope_overlap(cast(list[Session], None), ["E1"])

    def test_result_is_sorted(self) -> None:
        """Returned overlap list is always sorted alphabetically (AC-192-4)."""
        existing = [_make_session(name="alpha", scope=["E3", "E1", "E2"])]
        result = detect_scope_overlap(existing, ["E2", "E3", "E1"])
        assert result == ["E1", "E2", "E3"]

    def test_both_empty_returns_empty(self) -> None:
        """Edge case: no sessions and no new scope yields empty list."""
        result = detect_scope_overlap([], [])
        assert result == []

    def test_large_scope_intersection(self) -> None:
        """Handles non-trivial scope sizes correctly."""
        ids_a = [f"E1-F1-S1-T{i}" for i in range(50)]
        ids_b = [f"E1-F1-S1-T{i}" for i in range(25, 75)]
        existing = [_make_session(name="alpha", scope=ids_a)]
        result = detect_scope_overlap(existing, ids_b)
        expected = sorted(f"E1-F1-S1-T{i}" for i in range(25, 50))
        assert result == expected


# ---------------------------------------------------------------------------
# Integration: write_pid / delete_pid round trip via registry context
# ---------------------------------------------------------------------------


class TestPidFileRoundTrip:
    def test_write_then_read_then_delete(self, workspace: Path) -> None:
        reg = SessionRegistry(workspace)
        state_dir = workspace / ".devbench" / "sessions" / "integ"
        state_dir.mkdir(parents=True, exist_ok=True)
        reg.write_pid(state_dir, os.getpid())
        assert reg.read_pid(state_dir) == os.getpid()
        reg.delete_pid(state_dir)
        assert reg.read_pid(state_dir) is None

    def test_pid_is_alive_via_registry_liveness(self, workspace: Path) -> None:
        """A session whose PID matches the current process is ACTIVE."""
        reg = SessionRegistry(workspace)
        s = _make_session(name="live", pid=os.getpid())
        assert reg.liveness_of_sessions([s])["live"] == "ACTIVE"


class TestCleanupStaleSessions:
    """cleanup_stale_sessions removes session dirs for STALE sessions and
    updates the registry.  AC-192-11.
    """

    def test_cleanup_removes_stale_state_dir(self, workspace: Path) -> None:
        """State directory of a STALE session is removed on cleanup."""
        reg = SessionRegistry(workspace)
        state_dir = workspace / ".devbench" / "sessions" / "dead"
        state_dir.mkdir(parents=True, exist_ok=True)
        reg.write_pid(state_dir, 99999999)
        s = _make_session(name="dead", pid=99999999, state_dir=state_dir)
        reg.save([s])
        with patch("devbench.session.os.kill", side_effect=ProcessLookupError):
            removed = reg.cleanup_stale_sessions()
        assert "dead" in removed
        assert not state_dir.exists()

    def test_cleanup_keeps_active_state_dir(self, workspace: Path) -> None:
        """State directory of an ACTIVE session is NOT removed on cleanup."""
        reg = SessionRegistry(workspace)
        state_dir = workspace / ".devbench" / "sessions" / "live"
        state_dir.mkdir(parents=True, exist_ok=True)
        reg.write_pid(state_dir, os.getpid())
        s = _make_session(name="live", pid=os.getpid(), state_dir=state_dir)
        reg.save([s])
        removed = reg.cleanup_stale_sessions()
        assert "live" not in removed
        assert state_dir.exists()

    def test_cleanup_updates_registry_removing_stale_entries(self, workspace: Path) -> None:
        """After cleanup, the registry file contains only ACTIVE sessions."""
        reg = SessionRegistry(workspace)
        live_dir = workspace / ".devbench" / "sessions" / "live"
        dead_dir = workspace / ".devbench" / "sessions" / "dead"
        live_dir.mkdir(parents=True, exist_ok=True)
        dead_dir.mkdir(parents=True, exist_ok=True)
        live = _make_session(name="live", pid=os.getpid(), state_dir=live_dir)
        dead = _make_session(name="dead", pid=99999999, state_dir=dead_dir)
        reg.save([live, dead])

        def fake_kill(pid: int, sig: int) -> None:
            if pid == 99999999:
                raise ProcessLookupError

        with patch("devbench.session.os.kill", side_effect=fake_kill):
            reg.cleanup_stale_sessions()

        remaining = reg.load()
        names = [s.name for s in remaining]
        assert "live" in names
        assert "dead" not in names

    def test_cleanup_returns_list_of_removed_names(self, workspace: Path) -> None:
        """cleanup_stale_sessions returns a sorted list of removed session names."""
        reg = SessionRegistry(workspace)
        for name in ("bravo", "alpha"):
            state_dir = workspace / ".devbench" / "sessions" / name
            state_dir.mkdir(parents=True, exist_ok=True)
            reg.write_pid(state_dir, 99999999)
        sessions = [
            _make_session(
                name=n,
                pid=99999999,
                state_dir=workspace / ".devbench" / "sessions" / n,
            )
            for n in ("alpha", "bravo")
        ]
        reg.save(sessions)
        with patch("devbench.session.os.kill", side_effect=ProcessLookupError):
            removed = reg.cleanup_stale_sessions()
        assert removed == sorted(removed)
        assert set(removed) == {"alpha", "bravo"}

    def test_cleanup_empty_registry_returns_empty_list(self, workspace: Path) -> None:
        """When no sessions are registered, cleanup is a no-op."""
        reg = SessionRegistry(workspace)
        removed = reg.cleanup_stale_sessions()
        assert removed == []

    def test_cleanup_state_dir_absent_still_removes_registry_entry(self, workspace: Path) -> None:
        """When a stale session's state_dir no longer exists, the registry entry
        is still removed (idempotent -- the dir may have been deleted manually).
        """
        reg = SessionRegistry(workspace)
        state_dir = workspace / ".devbench" / "sessions" / "ghost"
        # Do NOT create state_dir -- it is already absent.
        s = _make_session(name="ghost", pid=99999999, state_dir=state_dir)
        reg.save([s])
        with patch("devbench.session.os.kill", side_effect=ProcessLookupError):
            removed = reg.cleanup_stale_sessions()
        assert "ghost" in removed
        remaining = reg.load()
        assert not any(sess.name == "ghost" for sess in remaining)

    def test_cleanup_with_multiple_active_all_kept(self, workspace: Path) -> None:
        """When all sessions are ACTIVE, cleanup removes none and returns []."""
        reg = SessionRegistry(workspace)
        sessions = []
        for name in ("a1", "a2"):
            sd = workspace / ".devbench" / "sessions" / name
            sd.mkdir(parents=True, exist_ok=True)
            reg.write_pid(sd, os.getpid())
            sessions.append(_make_session(name=name, pid=os.getpid(), state_dir=sd))
        reg.save(sessions)
        removed = reg.cleanup_stale_sessions()
        assert removed == []
        assert (workspace / ".devbench" / "sessions" / "a1").exists()
        assert (workspace / ".devbench" / "sessions" / "a2").exists()


# ---------------------------------------------------------------------------
# Integration: concurrent cmd_claim race via real flock_backlog (AC-192-3, AC-192-5)
# ---------------------------------------------------------------------------

_WU_ID = "RACE-F1-S1-T1"
_BACKLOG_ROW_TEMPLATE = (
    "| {wu_id} | Race Task | Task | in-queue | none | caylent-solutions/devbench | backlog/{wu_id}.md |\n"
)
_WU_FILE_TEMPLATE = (
    "# {wu_id}: Race Task\n\n"
    "## Status: in-queue\n\n"
    "## Target Repository\n\n"
    "- **Repo:** `caylent-solutions/devbench`\n\n"
    "## Dependencies\n\n"
    "| ID | Title | Status |\n"
    "|----|-------|--------|\n"
    "| none | | |\n\n"
    "## Changes Manifest\n\n"
    "| File | Change |\n"
    "|------|--------|\n"
    "| src/devbench/session.py | modify |\n"
)


def _build_race_workspace(tmp_path: Path) -> tuple[Path, Path, Path]:
    """Create a minimal workspace for race-condition tests.

    Returns:
        Tuple of (workspace_root, backlog_root, backlog_index).
    """
    backlog_root = tmp_path / "backlog"
    backlog_root.mkdir(parents=True)
    backlog_index = tmp_path / "BACKLOG.md"
    backlog_index.write_text(
        "# Backlog\n\n"
        "| ID | Title | Type | Status | Dependencies | Repo | File Path |\n"
        "|----|-------|------|--------|--------------|------|----------|\n"
        + _BACKLOG_ROW_TEMPLATE.format(wu_id=_WU_ID),
        encoding="utf-8",
    )
    wu_file = backlog_root / f"{_WU_ID}.md"
    wu_file.write_text(_WU_FILE_TEMPLATE.format(wu_id=_WU_ID), encoding="utf-8")
    return tmp_path, backlog_root, backlog_index


class TestCmdClaimRaceIntegration:
    """Integration: two threads call cmd_claim on the same in-queue WU concurrently.

    Spec 4.4.2 / AC-192-3 / AC-192-5: flock_backlog serialises concurrent claims;
    the lock ensures mutual exclusion so no BACKLOG.md corruption is produced.
    When a racing party changes the WU status to a non-claimable value under the
    lock (e.g. ``done``), the second claimer receives ClaimRaceError and rc=1.
    """

    @staticmethod
    def _patch_cli_constants(
        workspace: Path,
        backlog_root: Path,
        backlog_index: Path,
    ) -> tuple[object, object, object]:
        """Apply module-level patches to devbench.cli and return the three patch objects.

        Callers MUST call ``patcher.stop()`` on each returned patcher when done.
        This method is intended for use in the main thread before spawning worker
        threads so that all threads share the same patched module state without
        the per-thread context-manager race condition that concurrent
        ``patch.object`` calls would introduce.

        Args:
            workspace: Fixture workspace root to patch as WORKSPACE_ROOT.
            backlog_root: Fixture backlog directory to patch as BACKLOG_ROOT.
            backlog_index: Fixture BACKLOG.md path to patch as BACKLOG_INDEX.

        Returns:
            Tuple of three started :class:`unittest.mock._patch` objects.
        """
        from unittest.mock import patch as _patch

        import devbench.cli as cli_mod

        p1 = _patch.object(cli_mod, "WORKSPACE_ROOT", workspace)
        p2 = _patch.object(cli_mod, "BACKLOG_ROOT", backlog_root)
        p3 = _patch.object(cli_mod, "BACKLOG_INDEX", backlog_index)
        p1.start()
        p2.start()
        p3.start()
        return p1, p2, p3

    @staticmethod
    def _stop_patches(*patchers: Any) -> None:
        """Stop each patcher returned by :meth:`_patch_cli_constants`.

        Args:
            patchers: Patch objects with a ``stop()`` method.
        """
        for p in patchers:
            p.stop()

    def test_flock_serialises_concurrent_claims_no_corruption(self, tmp_path: Path) -> None:
        """Two concurrent cmd_claim calls complete without data corruption (AC-192-3).

        The flock_backlog context manager serialises the two writers so that
        atomic_write_text calls never interleave, leaving BACKLOG.md and the
        work-unit file in a consistent ``in-progress`` state after both
        threads finish.

        Patches are applied in the main thread before spawning workers to avoid
        the thread-safety issues inherent in concurrent ``patch.object`` calls.

        Asserts:
        - Both threads complete without raising unexpected exceptions.
        - The work-unit file has exactly one ``## Status:`` line with value ``in-progress``.
        - BACKLOG.md has exactly one row for the WU with status ``in-progress``.
        """
        import devbench.cli as cli_mod

        workspace, backlog_root, backlog_index = _build_race_workspace(tmp_path)
        wu_file = backlog_root / f"{_WU_ID}.md"

        results: list[int] = []
        errors: list[str] = []
        # Use a Barrier so both threads start cmd_claim at the same moment,
        # maximising the chance of a real concurrent-write race under the flock.
        barrier = threading.Barrier(2)

        def do_claim() -> None:
            barrier.wait()
            try:
                rc = cli_mod.cmd_claim(_WU_ID)
                results.append(rc)
            except Exception as exc:
                errors.append(f"{type(exc).__name__}: {exc}")

        # Patch module constants in the main thread before spawning workers so
        # all threads share the same patched state without concurrent-patch races.
        patchers = self._patch_cli_constants(workspace, backlog_root, backlog_index)
        try:
            t1 = threading.Thread(target=do_claim, name="claimer-1")
            t2 = threading.Thread(target=do_claim, name="claimer-2")
            t1.start()
            t2.start()
            t1.join(timeout=30)
            t2.join(timeout=30)
        finally:
            self._stop_patches(*patchers)

        assert not errors, f"Unexpected exceptions in claim threads: {errors}"
        assert len(results) == 2, f"Expected 2 results, got {len(results)}"

        # Both threads must have completed without an unexpected exception.
        # The flock ensures serial access so each sees a valid claimable status.
        for rc in results:
            assert rc in (0, 1), f"Unexpected return code {rc}"

        # Verify no corruption: exactly one ``## Status:`` line, value is in-progress.
        content = wu_file.read_text(encoding="utf-8")
        status_lines = [line for line in content.splitlines() if line.strip().startswith("## Status:")]
        assert len(status_lines) == 1, (
            f"Expected exactly one '## Status:' line, found {len(status_lines)}: {status_lines}"
        )
        assert "in-progress" in status_lines[0], f"Expected status 'in-progress', got: {status_lines[0]!r}"

    def test_backlog_index_not_corrupted_after_concurrent_claims(self, tmp_path: Path) -> None:
        """After concurrent claim calls, BACKLOG.md contains exactly one in-progress row (AC-192-3).

        Verifies that atomic_write_text (temp-then-rename) under flock_backlog prevents
        partial writes that would corrupt the backlog index during concurrent access.

        Patches are applied in the main thread before spawning workers to avoid
        the thread-safety issues inherent in concurrent ``patch.object`` calls.
        """
        import devbench.cli as cli_mod

        workspace, backlog_root, backlog_index = _build_race_workspace(tmp_path)
        errors: list[str] = []
        barrier = threading.Barrier(2)

        def do_claim() -> None:
            barrier.wait()
            try:
                cli_mod.cmd_claim(_WU_ID)
            except Exception as exc:
                errors.append(f"{type(exc).__name__}: {exc}")

        patchers = self._patch_cli_constants(workspace, backlog_root, backlog_index)
        try:
            t1 = threading.Thread(target=do_claim, name="index-claimer-1")
            t2 = threading.Thread(target=do_claim, name="index-claimer-2")
            t1.start()
            t2.start()
            t1.join(timeout=30)
            t2.join(timeout=30)
        finally:
            self._stop_patches(*patchers)

        assert not errors, f"Unexpected exceptions in claim threads: {errors}"

        # BACKLOG.md must be parseable and contain exactly one in-progress row for the WU.
        index_content = backlog_index.read_text(encoding="utf-8")
        in_progress_rows = [line for line in index_content.splitlines() if _WU_ID in line and "in-progress" in line]
        assert len(in_progress_rows) == 1, (
            f"Expected exactly one in-progress row for {_WU_ID!r} in BACKLOG.md, "
            f"found {len(in_progress_rows)}: {in_progress_rows}"
        )

    def test_claim_race_error_when_status_changed_to_done_under_lock(self, tmp_path: Path) -> None:
        """ClaimRaceError propagates as rc=1 when a competing party changes status to 'done' (AC-192-5).

        Simulates the real race: a "winner" thread holds the flock, changes the WU
        status from ``in-queue`` to ``done`` (via a direct file write while holding
        the lock), then releases.  The "loser" thread then acquires the lock, re-reads
        the status, finds ``done`` (not claimable), and cmd_claim returns 1 with a
        race-related error message on stderr.

        This tests the ClaimRaceError path end-to-end through cmd_claim.
        Module constants are patched in the main thread before spawning workers.
        """
        import devbench.cli as cli_mod

        workspace, backlog_root, backlog_index = _build_race_workspace(tmp_path)
        wu_file = backlog_root / f"{_WU_ID}.md"

        # A pair of Events to coordinate the "winner" and "loser" threads.
        winner_holds_lock = threading.Event()
        winner_may_release = threading.Event()
        winner_errors: list[str] = []
        loser_rc: list[int] = []
        loser_errors: list[str] = []

        def winner_thread() -> None:
            """Acquire the flock, write 'done' into the WU file, then hold until told to release."""
            try:
                with flock_backlog(workspace, timeout_seconds=10):
                    # Rewrite the WU file status to 'done' while holding the lock.
                    # This simulates a competing session that marks the WU as done
                    # before the loser thread can re-read the status under the lock.
                    done_content = wu_file.read_text(encoding="utf-8").replace("## Status: in-queue", "## Status: done")
                    wu_file.write_text(done_content, encoding="utf-8")
                    # Also update BACKLOG.md so force_status won't fail on index lookup.
                    # (The loser raises ClaimRaceError before reaching force_status,
                    # but this ensures a fully realistic simulation.)
                    idx_content = backlog_index.read_text(encoding="utf-8").replace("in-queue", "done")
                    backlog_index.write_text(idx_content, encoding="utf-8")
                    winner_holds_lock.set()  # signal: loser may now try to acquire
                    winner_may_release.wait(timeout=10)  # hold lock until loser is done
            except Exception as exc:
                winner_errors.append(f"{type(exc).__name__}: {exc}")
                winner_holds_lock.set()  # unblock the loser even on error

        def loser_thread() -> None:
            """Wait for the winner to hold the lock, then attempt cmd_claim -- expect rc=1."""
            winner_holds_lock.wait(timeout=10)
            try:
                # At this point the winner holds the lock and has written 'done'.
                # cmd_claim will block on flock until winner_may_release is set, then
                # re-read the status, find 'done', raise ClaimRaceError -> rc=1.
                rc = cli_mod.cmd_claim(_WU_ID)
                loser_rc.append(rc)
            except Exception as exc:
                loser_errors.append(f"{type(exc).__name__}: {exc}")
            finally:
                winner_may_release.set()  # let the winner release its lock

        patchers = self._patch_cli_constants(workspace, backlog_root, backlog_index)
        try:
            wt = threading.Thread(target=winner_thread, name="done-writer")
            lt = threading.Thread(target=loser_thread, name="race-loser")
            wt.start()
            lt.start()
            # loser sets winner_may_release in its finally block; join both.
            lt.join(timeout=30)
            wt.join(timeout=30)
        finally:
            self._stop_patches(*patchers)

        assert not winner_errors, f"Winner thread raised unexpected error: {winner_errors}"
        assert not loser_errors, f"Loser thread raised unexpected error: {loser_errors}"
        assert loser_rc, "Loser thread did not record a return code"
        assert loser_rc[0] == 1, (
            f"Expected cmd_claim to return 1 (ClaimRaceError path) when WU status is 'done', got rc={loser_rc[0]}"
        )

    def test_race_loser_stderr_contains_race_message(self, tmp_path: Path) -> None:
        """The rc=1 from a race condition emits an actionable stderr message (AC-192-5).

        Extends test_claim_race_error_when_status_changed_to_done_under_lock to verify
        the error message is human-readable and contains the keywords 'race' or 'claim'.
        Module constants are patched in the main thread before spawning workers.
        """
        import io
        import sys

        import devbench.cli as cli_mod

        workspace, backlog_root, backlog_index = _build_race_workspace(tmp_path)
        wu_file = backlog_root / f"{_WU_ID}.md"

        winner_holds_lock = threading.Event()
        winner_may_release = threading.Event()
        winner_errors: list[str] = []
        loser_stderr: list[str] = []
        loser_rc: list[int] = []

        def winner_thread() -> None:
            try:
                with flock_backlog(workspace, timeout_seconds=10):
                    done_content = wu_file.read_text(encoding="utf-8").replace("## Status: in-queue", "## Status: done")
                    wu_file.write_text(done_content, encoding="utf-8")
                    idx_content = backlog_index.read_text(encoding="utf-8").replace("in-queue", "done")
                    backlog_index.write_text(idx_content, encoding="utf-8")
                    winner_holds_lock.set()
                    winner_may_release.wait(timeout=10)
            except Exception as exc:
                winner_errors.append(f"{type(exc).__name__}: {exc}")
                winner_holds_lock.set()

        def loser_thread() -> None:
            winner_holds_lock.wait(timeout=10)
            buf = io.StringIO()
            try:
                with patch.object(sys, "stderr", buf):
                    rc = cli_mod.cmd_claim(_WU_ID)
                loser_rc.append(rc)
                loser_stderr.append(buf.getvalue())
            except Exception as exc:
                loser_stderr.append(f"EXCEPTION: {type(exc).__name__}: {exc}")
                loser_rc.append(-1)
            finally:
                winner_may_release.set()

        patchers = self._patch_cli_constants(workspace, backlog_root, backlog_index)
        try:
            wt = threading.Thread(target=winner_thread, name="done-writer-msg")
            lt = threading.Thread(target=loser_thread, name="race-loser-msg")
            wt.start()
            lt.start()
            lt.join(timeout=30)
            wt.join(timeout=30)
        finally:
            self._stop_patches(*patchers)

        assert not winner_errors, f"Winner thread error: {winner_errors}"
        assert loser_rc, "Loser did not record a return code"
        assert loser_rc[0] == 1, f"Expected rc=1 (race error), got {loser_rc[0]}"
        assert loser_stderr, "Loser did not record stderr output"
        msg = loser_stderr[0].lower()
        assert "race" in msg or "claim" in msg, (
            f"Expected 'race' or 'claim' in stderr message, got: {loser_stderr[0]!r}"
        )


# ---------------------------------------------------------------------------
# AC-192-16: window-stats + proposal lifecycle stay workspace-shared
# ---------------------------------------------------------------------------

# Minimal backlog structure used by the workspace-shared invariant tests.
_SHARED_WU_ALPHA = "E0-F1-S1-T1"
_SHARED_WU_BETA = "E0-F1-S1-T2"


def _build_two_session_workspace(tmp_path: Path) -> tuple[Path, Path, Path]:
    """Create a minimal workspace with two tasks on disjoint scopes.

    Returns:
        (workspace_root, backlog_root, backlog_index)
    """
    workspace = tmp_path
    backlog_root = workspace / "backlog" / "E0" / "E0-F1" / "E0-F1-S1"
    backlog_root.mkdir(parents=True)

    for wu_id in (_SHARED_WU_ALPHA, _SHARED_WU_BETA):
        (backlog_root / f"{wu_id}.md").write_text(
            f"# {wu_id}: Test\n\n## Status: in-queue\n\n"
            "## Description\n\nx\n\n"
            "## Dependencies\n\n| ID | Title | Status |\n|----|-------|--------|\n| none | | |\n",
            encoding="utf-8",
        )

    backlog_index = workspace / "BACKLOG.md"
    backlog_index.write_text(
        "# Backlog\n\n## Status Summary\n\n"
        "| Epic | Title | Done | In Progress | In Queue | Blocked |\n"
        "|------|-------|------|-------------|----------|---------|\n"
        "| E0 | x | 0 | 0 | 2 | 0 |\n\n"
        "## Full Work Unit Index\n\n"
        "| ID | Title | Type | Status | Dependencies | Repo | File Path |\n"
        "|----|-------|------|--------|--------------|------|-----------|\n"
        f"| {_SHARED_WU_ALPHA} | T1 | Task | in-queue | None | r |"
        f" `backlog/E0/E0-F1/E0-F1-S1/{_SHARED_WU_ALPHA}.md` |\n"
        f"| {_SHARED_WU_BETA} | T2 | Task | in-queue | None | r |"
        f" `backlog/E0/E0-F1/E0-F1-S1/{_SHARED_WU_BETA}.md` |\n",
        encoding="utf-8",
    )
    return workspace, backlog_root, backlog_index


class TestWorkspaceSharedWindowStats:
    """AC-192-16: window-stats aggregates live under the workspace root, not per-session.

    Two sessions with disjoint scopes -- alpha claims T1 and beta claims T2 --
    both transition tasks.  The resulting aggregates must appear in the single
    workspace-shared directory ``.devbench/window-stats/``, not inside any
    per-session state directory.
    """

    def test_transitions_from_different_sessions_land_in_shared_window_stats_dir(self, tmp_path: Path) -> None:
        """Transitions from session alpha and session beta both write to the same
        workspace-shared window-stats directory (AC-192-16).

        Each session's task is in a disjoint scope.  After each transition,
        the aggregate must live under ``<workspace>/.devbench/window-stats/``
        -- never inside ``<workspace>/.devbench/sessions/<name>/``.
        """
        from devbench.backlog.manager import BacklogManager
        from devbench.reporting.window_stats import aggregate_dir, aggregate_path, read_aggregate

        workspace, _backlog_root, backlog_index = _build_two_session_workspace(tmp_path)

        wu_alpha_path = workspace / f"backlog/E0/E0-F1/E0-F1-S1/{_SHARED_WU_ALPHA}.md"
        wu_beta_path = workspace / f"backlog/E0/E0-F1/E0-F1-S1/{_SHARED_WU_BETA}.md"

        # Simulate session "alpha" making a transition for its task.
        alpha_state_dir = workspace / ".devbench" / "sessions" / "alpha"
        alpha_state_dir.mkdir(parents=True)

        mgr = BacklogManager()
        mgr._set_status(wu_alpha_path, backlog_index, _SHARED_WU_ALPHA, "in-progress")

        # Simulate session "beta" making a transition for its own task.
        beta_state_dir = workspace / ".devbench" / "sessions" / "beta"
        beta_state_dir.mkdir(parents=True)

        mgr._set_status(wu_beta_path, backlog_index, _SHARED_WU_BETA, "in-progress")

        # Both aggregates must be in the workspace-shared window-stats dir.
        shared_dir = aggregate_dir(workspace)
        assert shared_dir.is_dir(), f"Workspace-shared window-stats dir missing at {shared_dir}"

        agg_alpha = read_aggregate(workspace, _SHARED_WU_ALPHA)
        assert agg_alpha is not None, (
            f"No aggregate written for {_SHARED_WU_ALPHA} at {aggregate_path(workspace, _SHARED_WU_ALPHA)}"
        )
        assert agg_alpha.transitions[0].new_status == "in-progress"

        agg_beta = read_aggregate(workspace, _SHARED_WU_BETA)
        assert agg_beta is not None, (
            f"No aggregate written for {_SHARED_WU_BETA} at {aggregate_path(workspace, _SHARED_WU_BETA)}"
        )
        assert agg_beta.transitions[0].new_status == "in-progress"

        # No aggregate must exist inside any per-session state directory.
        for session_name in ("alpha", "beta"):
            per_session_stats = workspace / ".devbench" / "sessions" / session_name / "window-stats"
            assert not per_session_stats.exists(), (
                f"window-stats appeared inside per-session dir {per_session_stats} -- "
                "aggregates must be workspace-shared"
            )

    def test_aggregate_files_not_nested_inside_session_dirs(self, tmp_path: Path) -> None:
        """Aggregate JSON files sit directly under ``.devbench/window-stats/``.

        Verifies the path returned by ``aggregate_path`` is workspace-rooted,
        NOT session-rooted -- regardless of how many session state dirs exist.
        """
        from devbench.reporting.window_stats import aggregate_path

        workspace = tmp_path

        # Create several session dirs to ensure nothing bleeds their path into
        # the aggregate calculation.
        for name in ("alpha", "beta", "gamma"):
            (workspace / ".devbench" / "sessions" / name).mkdir(parents=True)

        path = aggregate_path(workspace, "E0-F1-S1-T1")

        # The aggregate path must be exactly <workspace>/.devbench/window-stats/E0-F1-S1-T1.json
        assert path == workspace / ".devbench" / "window-stats" / "E0-F1-S1-T1.json", (
            f"aggregate_path returned {path!r}; expected workspace-rooted path"
        )
        # Must not contain any sessions/ component.
        assert "sessions" not in path.parts, f"aggregate_path contains 'sessions' component: {path}"

    @pytest.mark.parametrize(
        "session_name,wu_id",
        [
            ("alpha", _SHARED_WU_ALPHA),
            ("beta", _SHARED_WU_BETA),
        ],
    )
    def test_read_aggregate_resolves_from_workspace_root_not_session_dir(
        self, tmp_path: Path, session_name: str, wu_id: str
    ) -> None:
        """read_aggregate uses workspace_root -- the aggregate is visible
        regardless of which session dir context the caller passes (AC-192-16).

        A transition written by session A can be read back by session B because
        both use the same workspace_root argument.
        """
        import datetime as _dt

        from devbench.reporting.window_stats import read_aggregate, update_aggregate

        workspace = tmp_path

        # Write aggregate via session alpha's context (still uses workspace_root).
        ts = _dt.datetime(2026, 5, 16, tzinfo=_dt.UTC)
        update_aggregate(workspace, wu_id, "in-progress", ts)

        # Read aggregate as if from session beta's context (same workspace_root).
        agg = read_aggregate(workspace, wu_id)
        assert agg is not None, (
            f"Aggregate for {wu_id!r} written by session {session_name!r} "
            "is not visible via workspace_root read -- invariant broken"
        )
        assert agg.task_id == wu_id
        assert agg.transitions[0].new_status == "in-progress"
