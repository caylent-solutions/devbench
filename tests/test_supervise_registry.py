"""Tests for SuperviseRegistry + per-session state files (FR-17, Section 5.5).

The SuperviseRegistry mirrors SessionRegistry's file/atomic-write shape but is a
SEPARATE registry under ``.devbench/supervise/`` (D-8). Covers save/load
round-trip, atomic temp-then-rename, liveness via ``os.kill(pid, 0)``,
stale-reaping, per-session state-dir + path helpers, and multi-session listing.
"""

from __future__ import annotations

import fcntl
import json
import os
import threading
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import patch

import pytest

from devbench.constants import (
    SUPERVISE_BASE_DIR,
    SUPERVISE_BILLING_CHANNEL,
    SUPERVISE_PTY_LOG_FILENAME,
    SUPERVISE_REGISTRY_PATH,
    SUPERVISE_STATE_FILENAME,
    SUPERVISE_STATE_RUNNING,
    SUPERVISE_STATE_STARTING,
    SUPERVISE_STOP_REQUEST_FILENAME,
    SUPERVISE_SUPERVISOR_LOG_FILENAME,
)
from devbench.supervise import (
    SuperviseRegistry,
    SuperviseSessionState,
    new_session_state,
    supervise_pty_log_path,
    supervise_state_dir,
    supervise_stop_request_path,
    supervise_supervisor_log_path,
)


def _state(name: str, pid: int, state: str = SUPERVISE_STATE_RUNNING) -> SuperviseSessionState:
    return SuperviseSessionState(
        name=name,
        pid=pid,
        state=state,
        screen_name=f"devbench-supervise-{name}",
        started_at=datetime.now(UTC),
        started_by="tester",
        model="opus",
        effort="xhigh",
        scope=["E1-F1-S1-T1"],
        billing_channel=SUPERVISE_BILLING_CHANNEL,
    )


@pytest.mark.unit
class TestSuperviseStateDirHelpers:
    """Path helpers resolve the canonical per-session paths (Section 5.5)."""

    def test_state_dir_path(self, tmp_path: Path) -> None:
        assert supervise_state_dir(tmp_path, "nightly") == tmp_path / SUPERVISE_BASE_DIR / "nightly"

    def test_pty_log_path(self, tmp_path: Path) -> None:
        expected = tmp_path / SUPERVISE_BASE_DIR / "nightly" / SUPERVISE_PTY_LOG_FILENAME
        assert supervise_pty_log_path(tmp_path, "nightly") == expected

    def test_stop_request_path(self, tmp_path: Path) -> None:
        expected = tmp_path / SUPERVISE_BASE_DIR / "nightly" / SUPERVISE_STOP_REQUEST_FILENAME
        assert supervise_stop_request_path(tmp_path, "nightly") == expected

    def test_supervisor_log_path(self, tmp_path: Path) -> None:
        expected = tmp_path / SUPERVISE_BASE_DIR / "nightly" / SUPERVISE_SUPERVISOR_LOG_FILENAME
        assert supervise_supervisor_log_path(tmp_path, "nightly") == expected

    def test_path_helper_rejects_traversal(self, tmp_path: Path) -> None:
        with pytest.raises(ValueError, match=r"\.\."):
            supervise_state_dir(tmp_path, "../escape")


@pytest.mark.unit
class TestSuperviseSessionStateSerialization:
    """SuperviseSessionState round-trips through to_dict/from_dict."""

    def test_round_trip(self) -> None:
        original = _state("nightly", os.getpid())
        restored = SuperviseSessionState.from_dict(original.to_dict())
        assert restored.name == original.name
        assert restored.pid == original.pid
        assert restored.state == original.state
        assert restored.scope == original.scope
        assert restored.billing_channel == SUPERVISE_BILLING_CHANNEL

    def test_to_dict_is_json_serializable(self) -> None:
        # The registry persists JSON, so the dict must be serializable.
        json.dumps(_state("a", 1).to_dict())

    def test_from_dict_missing_key_fails_fast(self) -> None:
        with pytest.raises(KeyError):
            SuperviseSessionState.from_dict({"name": "x"})

    def test_from_dict_invalid_started_at_fails_fast(self) -> None:
        data = _state("a", 1).to_dict()
        data["started_at"] = "not-a-date"
        with pytest.raises(ValueError, match="ISO-8601"):
            SuperviseSessionState.from_dict(data)

    def test_from_dict_invalid_optional_datetime_fails_fast(self) -> None:
        data = _state("a", 1).to_dict()
        data["last_activity"] = "garbage"
        with pytest.raises(ValueError, match="ISO-8601"):
            SuperviseSessionState.from_dict(data)

    def test_from_dict_null_started_at_fails_fast(self) -> None:
        data = _state("a", 1).to_dict()
        data["started_at"] = None
        with pytest.raises(ValueError, match="started_at is required"):
            SuperviseSessionState.from_dict(data)

    def test_from_dict_preserves_optional_datetimes(self) -> None:
        now = datetime.now(UTC)
        state = _state("a", 1)
        state.last_activity = now
        state.expected_resume = now
        restored = SuperviseSessionState.from_dict(state.to_dict())
        assert restored.last_activity is not None
        assert restored.expected_resume is not None


@pytest.mark.unit
class TestSuperviseRegistryIO:
    """Registry save/load round-trip + atomic write (mirrors SessionRegistry)."""

    def test_load_absent_returns_empty(self, tmp_path: Path) -> None:
        assert SuperviseRegistry(tmp_path).load() == []

    def test_save_then_load_round_trip(self, tmp_path: Path) -> None:
        reg = SuperviseRegistry(tmp_path)
        sessions = [_state("a", 1), _state("b", 2)]
        reg.save(sessions)
        loaded = reg.load()
        assert sorted(s.name for s in loaded) == ["a", "b"]

    def test_registry_path_is_supervise_tree(self, tmp_path: Path) -> None:
        reg = SuperviseRegistry(tmp_path)
        reg.save([_state("a", 1)])
        assert (tmp_path / SUPERVISE_REGISTRY_PATH).exists()

    def test_save_is_atomic_no_partial_temp_left(self, tmp_path: Path) -> None:
        reg = SuperviseRegistry(tmp_path)
        reg.save([_state("a", 1)])
        # No leftover temp file beside the registry.
        registry_dir = (tmp_path / SUPERVISE_REGISTRY_PATH).parent
        leftovers = [p for p in registry_dir.iterdir() if p.name.endswith(".tmp")]
        assert leftovers == []

    def test_load_invalid_json_fails_fast(self, tmp_path: Path) -> None:
        registry_path = tmp_path / SUPERVISE_REGISTRY_PATH
        registry_path.parent.mkdir(parents=True, exist_ok=True)
        registry_path.write_text("{not json", encoding="utf-8")
        with pytest.raises(ValueError, match="invalid JSON"):
            SuperviseRegistry(tmp_path).load()

    def test_load_non_array_fails_fast(self, tmp_path: Path) -> None:
        registry_path = tmp_path / SUPERVISE_REGISTRY_PATH
        registry_path.parent.mkdir(parents=True, exist_ok=True)
        registry_path.write_text('{"name": "a"}', encoding="utf-8")
        with pytest.raises(ValueError, match="array"):
            SuperviseRegistry(tmp_path).load()


@pytest.mark.unit
class TestSuperviseRegistryStateFile:
    """Per-session state.json read/write (Section 5.5)."""

    def test_write_state_creates_state_json(self, tmp_path: Path) -> None:
        reg = SuperviseRegistry(tmp_path)
        reg.write_state(_state("nightly", os.getpid()))
        state_file = tmp_path / SUPERVISE_BASE_DIR / "nightly" / SUPERVISE_STATE_FILENAME
        assert state_file.exists()

    def test_read_state_round_trip(self, tmp_path: Path) -> None:
        reg = SuperviseRegistry(tmp_path)
        original = _state("nightly", os.getpid(), state=SUPERVISE_STATE_STARTING)
        reg.write_state(original)
        restored = reg.read_state("nightly")
        assert restored is not None
        assert restored.state == SUPERVISE_STATE_STARTING

    def test_read_state_absent_returns_none(self, tmp_path: Path) -> None:
        assert SuperviseRegistry(tmp_path).read_state("ghost") is None


@pytest.mark.unit
class TestSuperviseRegistryLiveness:
    """Liveness + stale reaping (mirrors SessionRegistry, os.kill(pid, 0))."""

    def test_is_alive_for_current_process(self, tmp_path: Path) -> None:
        assert SuperviseRegistry(tmp_path).is_alive(os.getpid()) is True

    def test_is_alive_false_for_dead_pid(self, tmp_path: Path) -> None:
        # PID 2**31 - 1 is virtually never a live process.
        assert SuperviseRegistry(tmp_path).is_alive(2**31 - 1) is False

    def test_liveness_of_sessions(self, tmp_path: Path) -> None:
        reg = SuperviseRegistry(tmp_path)
        sessions = [_state("live", os.getpid()), _state("dead", 2**31 - 1)]
        liveness = reg.liveness_of_sessions(sessions)
        assert liveness["live"] == "ACTIVE"
        assert liveness["dead"] == "STALE"

    def test_cleanup_stale_removes_dead_and_keeps_live(self, tmp_path: Path) -> None:
        reg = SuperviseRegistry(tmp_path)
        reg.save([_state("live", os.getpid()), _state("dead", 2**31 - 1)])
        # Materialize a state dir for the dead session so cleanup must remove it.
        reg.write_state(_state("dead", 2**31 - 1))
        removed = reg.cleanup_stale_sessions()
        assert removed == ["dead"]
        remaining = [s.name for s in reg.load()]
        assert remaining == ["live"]
        assert not (tmp_path / SUPERVISE_BASE_DIR / "dead").exists()

    def test_cleanup_stale_no_dead_is_noop(self, tmp_path: Path) -> None:
        reg = SuperviseRegistry(tmp_path)
        reg.save([_state("live", os.getpid())])
        assert reg.cleanup_stale_sessions() == []
        assert [s.name for s in reg.load()] == ["live"]

    def test_is_alive_eperm_is_active(self, tmp_path: Path) -> None:
        # EPERM (cross-user) means the process exists -> ACTIVE.
        reg = SuperviseRegistry(tmp_path)
        with patch("devbench.supervise.os.kill", side_effect=PermissionError):
            assert reg.is_alive(1234) is True


@pytest.mark.unit
class TestSuperviseRegistryMutation:
    """upsert / remove operate by name (atomic)."""

    def test_upsert_replaces_by_name(self, tmp_path: Path) -> None:
        reg = SuperviseRegistry(tmp_path)
        reg.save([_state("a", 1, state=SUPERVISE_STATE_STARTING)])
        reg.upsert(_state("a", 1, state=SUPERVISE_STATE_RUNNING))
        loaded = reg.load()
        assert len(loaded) == 1
        assert loaded[0].state == SUPERVISE_STATE_RUNNING

    def test_remove_drops_by_name(self, tmp_path: Path) -> None:
        reg = SuperviseRegistry(tmp_path)
        reg.save([_state("a", 1), _state("b", 2)])
        reg.remove("a")
        assert [s.name for s in reg.load()] == ["b"]

    def test_remove_absent_is_idempotent(self, tmp_path: Path) -> None:
        reg = SuperviseRegistry(tmp_path)
        reg.save([_state("a", 1)])
        reg.remove("ghost")
        assert [s.name for s in reg.load()] == ["a"]

    def test_read_state_malformed_json_fails_fast(self, tmp_path: Path) -> None:
        reg = SuperviseRegistry(tmp_path)
        state_dir = tmp_path / SUPERVISE_BASE_DIR / "broken"
        state_dir.mkdir(parents=True, exist_ok=True)
        (state_dir / SUPERVISE_STATE_FILENAME).write_text("{not json", encoding="utf-8")
        with pytest.raises(ValueError, match="invalid JSON"):
            reg.read_state("broken")


@pytest.mark.unit
class TestSuperviseRegistryAtomicWriteFailure:
    """Atomic-write failure cleans up the temp file and re-raises (fail-fast)."""

    def test_save_write_failure_cleans_temp_and_raises(self, tmp_path: Path) -> None:
        reg = SuperviseRegistry(tmp_path)
        with patch("pathlib.Path.write_text", side_effect=OSError("disk full")):
            with pytest.raises(OSError, match="disk full"):
                reg.save([_state("a", 1)])
        # No leftover temp file.
        registry_dir = (tmp_path / SUPERVISE_REGISTRY_PATH).parent
        assert not any(p.name.endswith(".tmp") for p in registry_dir.iterdir())

    def test_write_state_write_failure_cleans_temp_and_raises(self, tmp_path: Path) -> None:
        reg = SuperviseRegistry(tmp_path)
        with patch("pathlib.Path.write_text", side_effect=OSError("disk full")):
            with pytest.raises(OSError, match="disk full"):
                reg.write_state(_state("a", 1))


@pytest.mark.unit
class TestNewSessionState:
    """new_session_state builds a fresh starting-state record."""

    def test_starting_state(self) -> None:
        state = new_session_state(
            name="nightly",
            pid=os.getpid(),
            screen_name="devbench-supervise-nightly",
            model="opus",
            effort="xhigh",
            started_by="tester",
        )
        assert state.state == SUPERVISE_STATE_STARTING
        assert state.billing_channel == SUPERVISE_BILLING_CHANNEL
        assert state.scope == []

    def test_scope_recorded(self) -> None:
        state = new_session_state(
            name="x",
            pid=1,
            screen_name="s",
            model="opus",
            effort="xhigh",
            started_by="t",
            scope=["E1-F1-S1-T1"],
        )
        assert state.scope == ["E1-F1-S1-T1"]


@pytest.mark.unit
class TestRegistryConcurrency:
    """Concurrent-write safety for two parallel supervise sessions (FR-17, FR-32)."""

    def test_unique_tmp_path_per_writer(self, tmp_path: Path) -> None:
        # The atomic-write temp name is unique per writer (pid + counter) so two
        # parallel sessions' renames never collide on a shared temp path.
        reg = SuperviseRegistry(tmp_path)
        first = reg._unique_tmp_path()
        second = reg._unique_tmp_path()
        assert first != second
        assert str(first).startswith(str(reg._registry_path))
        assert str(first).endswith(".tmp")

    def test_concurrent_upserts_keep_both_entries(self, tmp_path: Path) -> None:
        # Two threads each upsert their OWN session concurrently; the index lock
        # serializes the read-modify-write so neither entry is clobbered (FR-32).
        reg = SuperviseRegistry(tmp_path)
        barrier = threading.Barrier(2)
        errors: list[Exception] = []

        def _writer(name: str) -> None:
            try:
                barrier.wait(timeout=10)
                for _ in range(20):
                    reg.upsert(_state(name, pid=1))
            except (OSError, ValueError, TimeoutError, threading.BrokenBarrierError) as exc:
                errors.append(exc)

        threads = [threading.Thread(target=_writer, args=(name,)) for name in ("alpha", "beta")]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=30)

        assert not errors
        names = {s.name for s in SuperviseRegistry(tmp_path).load()}
        assert names == {"alpha", "beta"}

    def test_index_lock_times_out_when_held(self, tmp_path: Path) -> None:
        # An external holder of the inter-process flock makes the contention branch
        # park then fail fast with a TimeoutError (Section 5.7, FR-30).
        from devbench import supervise

        reg = SuperviseRegistry(tmp_path)
        reg.save([_state("seed", 1)])  # create the registry dir
        lock_path = reg._registry_path.with_name(reg._registry_path.name + ".lock")

        with (
            lock_path.open("w") as holder,
            patch.object(supervise, "SUPERVISE_REGISTRY_LOCK_TIMEOUT_SECONDS", 0.2),
            patch.object(supervise, "SUPERVISE_REGISTRY_LOCK_POLL_SECONDS", 0.05),
        ):
            fcntl.flock(holder.fileno(), fcntl.LOCK_EX)
            try:
                with pytest.raises(TimeoutError, match="supervise registry lock"):
                    reg.upsert(_state("late", 1))
            finally:
                fcntl.flock(holder.fileno(), fcntl.LOCK_UN)
