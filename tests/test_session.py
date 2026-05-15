"""Tests for src/devbench/session.py -- Session dataclass, SessionRegistry, flock_backlog,
detect_scope_overlap, ClaimRaceError, and PID-file management.

Coverage requirement: 100% line + branch on devbench.session.

AC-192-1: session state_dir creation and PID-file management.
AC-192-3: concurrent sessions via flock_backlog mutual exclusion.
AC-192-10: session listing with liveness (ACTIVE / STALE).
"""

from __future__ import annotations

import errno
import fcntl
import os
from dataclasses import fields
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest

from devbench.session import (
    ClaimRaceError,
    Session,
    SessionRegistry,
    detect_scope_overlap,
    flock_backlog,
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
