"""Tests for src/devbench/drain.py.

Coverage requirement: 100% line + branch on devbench.drain.

Covers:
- DRAIN_SIGNAL_NAME constant value contract
- DrainState dataclass construction, to_dict, from_dict (happy path + error paths)
- request_drain: writes signal file atomically; respects USER/USERNAME env vars;
  falls back to "unknown"; returns signal path; parent dir created on demand;
  cleanup on write failure; overwrites existing signal
- cancel_drain: deletes file when present (returns True); idempotent when absent (returns False)
- read_drain_state: returns None when absent; returns DrainState when present;
  raises ValueError for invalid JSON; raises ValueError for non-dict JSON root;
  raises KeyError for missing required fields; raises ValueError for bad datetime
- consume_drain: returns None when absent; returns DrainState and deletes file when present;
  propagates ValueError from read_drain_state; tolerates concurrent deletion (TOCTOU);
  propagates non-FileNotFoundError OSErrors from unlink
- Per-session path routing: when DEVBENCH_SESSION_NAME is set, all public helpers
  use <workspace>/.devbench/sessions/<name>/drain.signal instead of the workspace-root
  path (spec 4.4.4, AC-192-7).
"""

from __future__ import annotations

import json
import os
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest

from devbench.drain import (
    DRAIN_SIGNAL_NAME,
    DrainState,
    cancel_drain,
    consume_drain,
    read_drain_state,
    request_drain,
    resolve_drain_signal_path,
)

# ---------------------------------------------------------------------------
# Module-level sentinel datetime for reuse across tests
# ---------------------------------------------------------------------------

_NOW = datetime(2026, 1, 15, 12, 0, 0, tzinfo=UTC)


# ---------------------------------------------------------------------------
# DRAIN_SIGNAL_NAME constant
# ---------------------------------------------------------------------------


class TestDrainSignalNameConstant:
    """DRAIN_SIGNAL_NAME must equal the spec-mandated value."""

    @pytest.mark.unit
    def test_value_equals_spec(self) -> None:
        assert DRAIN_SIGNAL_NAME == ".devbench/drain.signal"

    @pytest.mark.unit
    def test_type_is_str(self) -> None:
        assert isinstance(DRAIN_SIGNAL_NAME, str)


# ---------------------------------------------------------------------------
# DrainState dataclass
# ---------------------------------------------------------------------------


class TestDrainStateConstruction:
    """DrainState can be constructed with all fields and with reason defaulting to empty."""

    @pytest.mark.unit
    def test_all_fields_stored(self) -> None:
        state = DrainState(requested_at=_NOW, requested_by="alice", reason="maintenance")
        assert state.requested_at == _NOW
        assert state.requested_by == "alice"
        assert state.reason == "maintenance"

    @pytest.mark.unit
    def test_reason_defaults_to_empty_string(self) -> None:
        state = DrainState(requested_at=_NOW, requested_by="bob")
        assert state.reason == ""

    @pytest.mark.unit
    def test_requested_at_is_datetime(self) -> None:
        state = DrainState(requested_at=_NOW, requested_by="x")
        assert isinstance(state.requested_at, datetime)

    @pytest.mark.unit
    def test_requested_by_is_str(self) -> None:
        state = DrainState(requested_at=_NOW, requested_by="operator")
        assert isinstance(state.requested_by, str)

    @pytest.mark.unit
    def test_str_contains_requested_by(self) -> None:
        """__str__ must include the requester so status output is human-readable."""
        state = DrainState(requested_at=_NOW, requested_by="carol", reason="maintenance")
        assert "carol" in str(state)

    @pytest.mark.unit
    def test_str_contains_requested_at(self) -> None:
        """__str__ must include the timestamp."""
        state = DrainState(requested_at=_NOW, requested_by="carol", reason="maintenance")
        assert "2026-01-15" in str(state)

    @pytest.mark.unit
    def test_str_contains_reason_when_set(self) -> None:
        """__str__ includes the reason when it is non-empty."""
        state = DrainState(requested_at=_NOW, requested_by="carol", reason="planned upgrade")
        assert "planned upgrade" in str(state)

    @pytest.mark.unit
    def test_str_reason_absent_label_when_empty(self) -> None:
        """__str__ includes a placeholder when reason is empty."""
        state = DrainState(requested_at=_NOW, requested_by="carol", reason="")
        text = str(state)
        assert "(none)" in text


# ---------------------------------------------------------------------------
# DrainState.to_dict
# ---------------------------------------------------------------------------


class TestDrainStateToDict:
    """to_dict serializes all fields to a JSON-serializable dict."""

    @pytest.mark.unit
    def test_keys_present(self) -> None:
        d = DrainState(requested_at=_NOW, requested_by="alice", reason="r").to_dict()
        assert set(d.keys()) == {"requested_at", "requested_by", "reason"}

    @pytest.mark.unit
    def test_requested_at_is_iso_string(self) -> None:
        d = DrainState(requested_at=_NOW, requested_by="alice").to_dict()
        assert isinstance(d["requested_at"], str)
        # Must be parseable back to the same datetime
        parsed = datetime.fromisoformat(d["requested_at"])
        assert parsed == _NOW

    @pytest.mark.unit
    def test_requested_by_value(self) -> None:
        d = DrainState(requested_at=_NOW, requested_by="carol").to_dict()
        assert d["requested_by"] == "carol"

    @pytest.mark.unit
    def test_reason_value_when_set(self) -> None:
        d = DrainState(requested_at=_NOW, requested_by="x", reason="planned").to_dict()
        assert d["reason"] == "planned"

    @pytest.mark.unit
    def test_reason_empty_string_when_default(self) -> None:
        d = DrainState(requested_at=_NOW, requested_by="x").to_dict()
        assert d["reason"] == ""


# ---------------------------------------------------------------------------
# DrainState.from_dict
# ---------------------------------------------------------------------------


class TestDrainStateFromDict:
    """from_dict deserializes a dict produced by to_dict."""

    def _make_dict(
        self,
        requested_at: str = "2026-01-15T12:00:00+00:00",
        requested_by: str = "alice",
        reason: str = "test",
    ) -> dict[str, Any]:
        return {
            "requested_at": requested_at,
            "requested_by": requested_by,
            "reason": reason,
        }

    @pytest.mark.unit
    def test_round_trip(self) -> None:
        original = DrainState(requested_at=_NOW, requested_by="alice", reason="r")
        restored = DrainState.from_dict(original.to_dict())
        assert restored.requested_at == original.requested_at
        assert restored.requested_by == original.requested_by
        assert restored.reason == original.reason

    @pytest.mark.unit
    def test_requested_at_utc_aware(self) -> None:
        state = DrainState.from_dict(self._make_dict())
        assert state.requested_at.tzinfo is not None
        utcoffset = state.requested_at.utcoffset()
        assert utcoffset is not None
        assert utcoffset.total_seconds() == 0

    @pytest.mark.unit
    def test_naive_datetime_gets_utc(self) -> None:
        d = self._make_dict(requested_at="2026-01-15T12:00:00")
        state = DrainState.from_dict(d)
        assert state.requested_at.tzinfo is not None

    @pytest.mark.unit
    def test_non_utc_datetime_normalised(self) -> None:
        # +05:30 is IST; should normalise to UTC
        d = self._make_dict(requested_at="2026-01-15T17:30:00+05:30")
        state = DrainState.from_dict(d)
        assert state.requested_at == datetime(2026, 1, 15, 12, 0, 0, tzinfo=UTC)

    @pytest.mark.unit
    def test_reason_optional_defaults_empty(self) -> None:
        d: dict[str, Any] = {"requested_at": "2026-01-15T12:00:00+00:00", "requested_by": "bob"}
        state = DrainState.from_dict(d)
        assert state.reason == ""

    @pytest.mark.unit
    def test_missing_requested_at_raises_key_error(self) -> None:
        d: dict[str, Any] = {"requested_by": "alice", "reason": "r"}
        with pytest.raises(KeyError):
            DrainState.from_dict(d)

    @pytest.mark.unit
    def test_missing_requested_by_raises_key_error(self) -> None:
        d: dict[str, Any] = {"requested_at": "2026-01-15T12:00:00+00:00", "reason": "r"}
        with pytest.raises(KeyError):
            DrainState.from_dict(d)

    @pytest.mark.unit
    def test_invalid_datetime_string_raises_value_error(self) -> None:
        d = self._make_dict(requested_at="not-a-datetime")
        with pytest.raises(ValueError, match="not a valid ISO 8601"):
            DrainState.from_dict(d)


# ---------------------------------------------------------------------------
# request_drain
# ---------------------------------------------------------------------------


class TestRequestDrain:
    """request_drain writes the drain signal file and returns its path."""

    @pytest.mark.unit
    def test_returns_signal_path(self, tmp_path: Path) -> None:
        path = request_drain(tmp_path)
        assert path == tmp_path / DRAIN_SIGNAL_NAME

    @pytest.mark.unit
    def test_file_created(self, tmp_path: Path) -> None:
        request_drain(tmp_path)
        assert (tmp_path / DRAIN_SIGNAL_NAME).exists()

    @pytest.mark.unit
    def test_file_contains_valid_json(self, tmp_path: Path) -> None:
        request_drain(tmp_path)
        raw = (tmp_path / DRAIN_SIGNAL_NAME).read_text(encoding="utf-8")
        data = json.loads(raw)
        assert isinstance(data, dict)

    @pytest.mark.unit
    def test_json_has_required_keys(self, tmp_path: Path) -> None:
        request_drain(tmp_path)
        data = json.loads((tmp_path / DRAIN_SIGNAL_NAME).read_text(encoding="utf-8"))
        assert "requested_at" in data
        assert "requested_by" in data
        assert "reason" in data

    @pytest.mark.unit
    def test_reason_written_when_provided(self, tmp_path: Path) -> None:
        request_drain(tmp_path, reason="upgrade")
        data = json.loads((tmp_path / DRAIN_SIGNAL_NAME).read_text(encoding="utf-8"))
        assert data["reason"] == "upgrade"

    @pytest.mark.unit
    def test_reason_empty_when_not_provided(self, tmp_path: Path) -> None:
        request_drain(tmp_path)
        data = json.loads((tmp_path / DRAIN_SIGNAL_NAME).read_text(encoding="utf-8"))
        assert data["reason"] == ""

    @pytest.mark.unit
    def test_requested_at_is_recent_utc(self, tmp_path: Path) -> None:
        before = datetime.now(tz=UTC)
        request_drain(tmp_path)
        after = datetime.now(tz=UTC)
        data = json.loads((tmp_path / DRAIN_SIGNAL_NAME).read_text(encoding="utf-8"))
        ts = datetime.fromisoformat(data["requested_at"])
        assert before <= ts <= after

    @pytest.mark.unit
    def test_requested_by_uses_user_env(self, tmp_path: Path) -> None:
        with patch.dict(os.environ, {"USER": "testuser"}, clear=False):
            request_drain(tmp_path)
        data = json.loads((tmp_path / DRAIN_SIGNAL_NAME).read_text(encoding="utf-8"))
        assert data["requested_by"] == "testuser"

    @pytest.mark.unit
    def test_requested_by_falls_back_to_username_env(self, tmp_path: Path) -> None:
        env = {k: v for k, v in os.environ.items() if k not in ("USER", "USERNAME")}
        env["USERNAME"] = "winuser"
        with patch.dict(os.environ, env, clear=True):
            request_drain(tmp_path)
        data = json.loads((tmp_path / DRAIN_SIGNAL_NAME).read_text(encoding="utf-8"))
        assert data["requested_by"] == "winuser"

    @pytest.mark.unit
    def test_requested_by_falls_back_to_unknown(self, tmp_path: Path) -> None:
        env = {k: v for k, v in os.environ.items() if k not in ("USER", "USERNAME")}
        with patch.dict(os.environ, env, clear=True):
            request_drain(tmp_path)
        data = json.loads((tmp_path / DRAIN_SIGNAL_NAME).read_text(encoding="utf-8"))
        assert data["requested_by"] == "unknown"

    @pytest.mark.unit
    def test_creates_parent_directory(self, tmp_path: Path) -> None:
        nested = tmp_path / "nested" / "workspace"
        request_drain(nested)
        assert (nested / DRAIN_SIGNAL_NAME).exists()

    @pytest.mark.unit
    def test_overwrites_existing_signal(self, tmp_path: Path) -> None:
        request_drain(tmp_path, reason="first")
        request_drain(tmp_path, reason="second")
        data = json.loads((tmp_path / DRAIN_SIGNAL_NAME).read_text(encoding="utf-8"))
        assert data["reason"] == "second"

    @pytest.mark.unit
    def test_exception_during_write_cleans_up_tmp_and_reraises(self, tmp_path: Path) -> None:
        """When the atomic write fails, the tmp file is removed and the exception re-raised."""
        # Pre-create the parent dir so mkdir doesn't fail
        (tmp_path / ".devbench").mkdir(parents=True, exist_ok=True)

        original_write = Path.write_text

        call_count = 0

        def failing_write(self: Path, *args: Any, **kwargs: Any) -> int:
            nonlocal call_count
            call_count += 1
            # Only fail the first write_text call (the tmp file write)
            if self.suffix == ".tmp":
                raise OSError("simulated disk full")
            return original_write(self, *args, **kwargs)

        with patch.object(Path, "write_text", failing_write):
            with pytest.raises(OSError, match="simulated disk full"):
                request_drain(tmp_path, reason="doomed")

        # The tmp file must not exist after the failed write
        tmp_file = tmp_path / ".devbench" / "drain.tmp"
        assert not tmp_file.exists()

    @pytest.mark.unit
    def test_exception_during_write_reraises_when_unlink_also_fails(self, tmp_path: Path) -> None:
        """When write fails and the subsequent tmp unlink also raises OSError,
        the original write exception is still re-raised (unlink error is suppressed)."""
        (tmp_path / ".devbench").mkdir(parents=True, exist_ok=True)

        original_write = Path.write_text
        original_unlink = Path.unlink

        def failing_write(self: Path, *args: Any, **kwargs: Any) -> int:
            if self.suffix == ".tmp":
                raise OSError("simulated disk full")
            return original_write(self, *args, **kwargs)

        def failing_unlink(self: Path, **kwargs: Any) -> None:
            if self.name == "drain.tmp":
                raise OSError("simulated unlink failure")
            return original_unlink(self, **kwargs)

        with patch.object(Path, "write_text", failing_write):
            with patch.object(Path, "unlink", failing_unlink):
                with pytest.raises(OSError, match="simulated disk full"):
                    request_drain(tmp_path, reason="doomed2")


# ---------------------------------------------------------------------------
# cancel_drain
# ---------------------------------------------------------------------------


class TestCancelDrain:
    """cancel_drain deletes the signal file when present, returns False when absent."""

    @pytest.mark.unit
    def test_returns_true_when_signal_present(self, tmp_path: Path) -> None:
        request_drain(tmp_path)
        result = cancel_drain(tmp_path)
        assert result is True

    @pytest.mark.unit
    def test_file_deleted_after_cancel(self, tmp_path: Path) -> None:
        request_drain(tmp_path)
        cancel_drain(tmp_path)
        assert not (tmp_path / DRAIN_SIGNAL_NAME).exists()

    @pytest.mark.unit
    def test_returns_false_when_signal_absent(self, tmp_path: Path) -> None:
        result = cancel_drain(tmp_path)
        assert result is False

    @pytest.mark.unit
    def test_idempotent_second_cancel_returns_false(self, tmp_path: Path) -> None:
        request_drain(tmp_path)
        cancel_drain(tmp_path)
        result = cancel_drain(tmp_path)
        assert result is False


# ---------------------------------------------------------------------------
# read_drain_state
# ---------------------------------------------------------------------------


class TestReadDrainState:
    """read_drain_state returns None when absent, DrainState when present."""

    @pytest.mark.unit
    def test_returns_none_when_absent(self, tmp_path: Path) -> None:
        result = read_drain_state(tmp_path)
        assert result is None

    @pytest.mark.unit
    def test_returns_drain_state_when_present(self, tmp_path: Path) -> None:
        request_drain(tmp_path, reason="check")
        state = read_drain_state(tmp_path)
        assert state is not None
        assert isinstance(state, DrainState)

    @pytest.mark.unit
    def test_reason_preserved(self, tmp_path: Path) -> None:
        request_drain(tmp_path, reason="mycheck")
        state = read_drain_state(tmp_path)
        assert state is not None
        assert state.reason == "mycheck"

    @pytest.mark.unit
    def test_requested_by_preserved(self, tmp_path: Path) -> None:
        with patch.dict(os.environ, {"USER": "operator"}, clear=False):
            request_drain(tmp_path)
        state = read_drain_state(tmp_path)
        assert state is not None
        assert state.requested_by == "operator"

    @pytest.mark.unit
    def test_requested_at_utc_aware(self, tmp_path: Path) -> None:
        request_drain(tmp_path)
        state = read_drain_state(tmp_path)
        assert state is not None
        assert state.requested_at.tzinfo is not None

    @pytest.mark.unit
    def test_raises_value_error_for_invalid_json(self, tmp_path: Path) -> None:
        signal = tmp_path / DRAIN_SIGNAL_NAME
        signal.parent.mkdir(parents=True, exist_ok=True)
        signal.write_text("not-valid-json{{{", encoding="utf-8")
        with pytest.raises(ValueError, match="invalid JSON"):
            read_drain_state(tmp_path)

    @pytest.mark.unit
    def test_raises_value_error_for_non_dict_json(self, tmp_path: Path) -> None:
        signal = tmp_path / DRAIN_SIGNAL_NAME
        signal.parent.mkdir(parents=True, exist_ok=True)
        signal.write_text(json.dumps([1, 2, 3]), encoding="utf-8")
        with pytest.raises(ValueError, match="must contain a JSON object"):
            read_drain_state(tmp_path)

    @pytest.mark.unit
    def test_raises_key_error_for_missing_requested_at(self, tmp_path: Path) -> None:
        signal = tmp_path / DRAIN_SIGNAL_NAME
        signal.parent.mkdir(parents=True, exist_ok=True)
        signal.write_text(json.dumps({"requested_by": "alice", "reason": "r"}), encoding="utf-8")
        with pytest.raises(KeyError):
            read_drain_state(tmp_path)

    @pytest.mark.unit
    def test_raises_value_error_for_bad_datetime(self, tmp_path: Path) -> None:
        signal = tmp_path / DRAIN_SIGNAL_NAME
        signal.parent.mkdir(parents=True, exist_ok=True)
        signal.write_text(
            json.dumps({"requested_at": "garbage", "requested_by": "alice", "reason": "r"}),
            encoding="utf-8",
        )
        with pytest.raises(ValueError, match="not a valid ISO 8601"):
            read_drain_state(tmp_path)


# ---------------------------------------------------------------------------
# consume_drain
# ---------------------------------------------------------------------------


class TestConsumeDrain:
    """consume_drain atomically reads and removes the drain signal."""

    @pytest.mark.unit
    def test_returns_none_when_absent(self, tmp_path: Path) -> None:
        result = consume_drain(tmp_path)
        assert result is None

    @pytest.mark.unit
    def test_returns_drain_state_when_present(self, tmp_path: Path) -> None:
        request_drain(tmp_path, reason="consume-me")
        state = consume_drain(tmp_path)
        assert state is not None
        assert state.reason == "consume-me"

    @pytest.mark.unit
    def test_file_deleted_after_consume(self, tmp_path: Path) -> None:
        request_drain(tmp_path)
        consume_drain(tmp_path)
        assert not (tmp_path / DRAIN_SIGNAL_NAME).exists()

    @pytest.mark.unit
    def test_second_consume_returns_none(self, tmp_path: Path) -> None:
        request_drain(tmp_path)
        consume_drain(tmp_path)
        result = consume_drain(tmp_path)
        assert result is None

    @pytest.mark.unit
    def test_propagates_value_error_for_invalid_json(self, tmp_path: Path) -> None:
        signal = tmp_path / DRAIN_SIGNAL_NAME
        signal.parent.mkdir(parents=True, exist_ok=True)
        signal.write_text("bad json!!!", encoding="utf-8")
        with pytest.raises(ValueError):
            consume_drain(tmp_path)

    @pytest.mark.unit
    def test_returned_state_is_drain_state_type(self, tmp_path: Path) -> None:
        request_drain(tmp_path, reason="typed")
        state = consume_drain(tmp_path)
        assert isinstance(state, DrainState)

    @pytest.mark.unit
    def test_reason_matches_what_was_requested(self, tmp_path: Path) -> None:
        request_drain(tmp_path, reason="scheduled")
        state = consume_drain(tmp_path)
        assert state is not None
        assert state.reason == "scheduled"

    @pytest.mark.unit
    def test_toctou_concurrent_deletion_returns_state(self, tmp_path: Path) -> None:
        """If the signal file is deleted by another process between read and unlink,
        consume_drain must return the successfully-read DrainState rather than raising
        FileNotFoundError -- the drain was consumed (by whoever deleted the file first)."""
        request_drain(tmp_path, reason="race")
        signal = tmp_path / DRAIN_SIGNAL_NAME
        original_unlink = Path.unlink

        def racing_unlink(self: Path, missing_ok: bool = False) -> None:
            if self == signal:
                # Simulate another process deleting the file first
                original_unlink(self, missing_ok=True)
                # Now raise FileNotFoundError as if the file was already gone
                raise FileNotFoundError(f"[Errno 2] No such file or directory: '{self}'")
            return original_unlink(self, missing_ok=missing_ok)

        with patch.object(Path, "unlink", racing_unlink):
            state = consume_drain(tmp_path)

        assert state is not None
        assert state.reason == "race"

    @pytest.mark.unit
    def test_non_file_not_found_oserror_propagates(self, tmp_path: Path) -> None:
        """OSErrors other than FileNotFoundError (e.g. permission denied) propagate
        from consume_drain so the caller knows the consume failed."""
        request_drain(tmp_path, reason="perm-error")
        signal = tmp_path / DRAIN_SIGNAL_NAME
        original_unlink = Path.unlink

        def permission_denied_unlink(self: Path, missing_ok: bool = False) -> None:
            if self == signal:
                raise PermissionError(f"[Errno 13] Permission denied: '{self}'")
            return original_unlink(self, missing_ok=missing_ok)

        with patch.object(Path, "unlink", permission_denied_unlink):
            with pytest.raises(PermissionError):
                consume_drain(tmp_path)


# ---------------------------------------------------------------------------
# resolve_drain_signal_path -- per-session path routing (spec 4.4.4, AC-192-7)
# ---------------------------------------------------------------------------


class TestResolveDrainSignalPath:
    """resolve_drain_signal_path returns per-session or workspace-root path."""

    @pytest.mark.unit
    def test_no_session_name_returns_workspace_root_path(self, tmp_path: Path) -> None:
        """Without DEVBENCH_SESSION_NAME, returns the canonical workspace-root drain.signal."""
        env = {k: v for k, v in os.environ.items() if k != "DEVBENCH_SESSION_NAME"}
        with patch.dict(os.environ, env, clear=True):
            result = resolve_drain_signal_path(tmp_path)
        assert result == tmp_path / DRAIN_SIGNAL_NAME

    @pytest.mark.unit
    def test_empty_session_name_returns_workspace_root_path(self, tmp_path: Path) -> None:
        """DEVBENCH_SESSION_NAME set to empty string is treated as unset."""
        with patch.dict(os.environ, {"DEVBENCH_SESSION_NAME": ""}, clear=False):
            result = resolve_drain_signal_path(tmp_path)
        assert result == tmp_path / DRAIN_SIGNAL_NAME

    @pytest.mark.unit
    def test_whitespace_only_session_name_returns_workspace_root_path(self, tmp_path: Path) -> None:
        """DEVBENCH_SESSION_NAME set to whitespace-only is treated as unset."""
        with patch.dict(os.environ, {"DEVBENCH_SESSION_NAME": "   "}, clear=False):
            result = resolve_drain_signal_path(tmp_path)
        assert result == tmp_path / DRAIN_SIGNAL_NAME

    @pytest.mark.unit
    def test_session_name_set_returns_per_session_path(self, tmp_path: Path) -> None:
        """When DEVBENCH_SESSION_NAME is set, returns path inside the session dir."""
        with patch.dict(os.environ, {"DEVBENCH_SESSION_NAME": "my-session"}, clear=False):
            result = resolve_drain_signal_path(tmp_path)
        expected = tmp_path / ".devbench" / "sessions" / "my-session" / "drain.signal"
        assert result == expected

    @pytest.mark.unit
    def test_session_name_set_different_session_names(self, tmp_path: Path) -> None:
        """Different session names produce different per-session paths."""
        with patch.dict(os.environ, {"DEVBENCH_SESSION_NAME": "alpha"}, clear=False):
            path_alpha = resolve_drain_signal_path(tmp_path)
        with patch.dict(os.environ, {"DEVBENCH_SESSION_NAME": "beta"}, clear=False):
            path_beta = resolve_drain_signal_path(tmp_path)
        assert path_alpha != path_beta
        assert "alpha" in str(path_alpha)
        assert "beta" in str(path_beta)

    @pytest.mark.unit
    def test_session_path_is_relative_to_workspace_arg(self, tmp_path: Path) -> None:
        """Per-session drain path is always relative to the ``workspace`` argument, not JUDGE_WORKSPACE_ROOT."""
        other_path = tmp_path / "other-workspace"
        with patch.dict(os.environ, {"DEVBENCH_SESSION_NAME": "sess"}, clear=False):
            result_a = resolve_drain_signal_path(tmp_path)
            result_b = resolve_drain_signal_path(other_path)
        assert result_a != result_b
        assert str(result_a).startswith(str(tmp_path))
        assert str(result_b).startswith(str(other_path))

    @pytest.mark.unit
    def test_session_path_contains_sessions_subdir(self, tmp_path: Path) -> None:
        """Per-session drain path is nested inside the sessions base directory."""
        with patch.dict(os.environ, {"DEVBENCH_SESSION_NAME": "my-session"}, clear=False):
            result = resolve_drain_signal_path(tmp_path)
        assert ".devbench/sessions" in str(result)

    @pytest.mark.unit
    def test_session_path_filename_is_drain_signal(self, tmp_path: Path) -> None:
        """Per-session drain path ends with 'drain.signal'."""
        with patch.dict(os.environ, {"DEVBENCH_SESSION_NAME": "sess"}, clear=False):
            result = resolve_drain_signal_path(tmp_path)
        assert result.name == "drain.signal"


# ---------------------------------------------------------------------------
# Per-session path routing integration -- public helpers use session path
# ---------------------------------------------------------------------------


class TestPerSessionDrainHelpers:
    """All public drain helpers use per-session path when DEVBENCH_SESSION_NAME is set."""

    def _session_env(self, workspace: Path, name: str) -> dict[str, str]:
        """Build an env dict that activates per-session routing.

        Only ``DEVBENCH_SESSION_NAME`` is needed -- the session path is always
        relative to the ``workspace`` argument passed to drain helpers, so
        ``JUDGE_WORKSPACE_ROOT`` is not consulted by ``drain.py``.
        """
        return {
            "DEVBENCH_SESSION_NAME": name,
        }

    def _expected_session_signal(self, workspace: Path, name: str) -> Path:
        return workspace / ".devbench" / "sessions" / name / "drain.signal"

    @pytest.mark.unit
    def test_request_drain_writes_to_session_path(self, tmp_path: Path) -> None:
        """request_drain writes to the per-session path when session name is set."""
        env = self._session_env(tmp_path, "session-a")
        with patch.dict(os.environ, env, clear=False):
            result = request_drain(tmp_path)
        expected = self._expected_session_signal(tmp_path, "session-a")
        assert result == expected
        assert expected.exists()

    @pytest.mark.unit
    def test_request_drain_does_not_write_workspace_root_path(self, tmp_path: Path) -> None:
        """When session name is set, workspace-root drain.signal is NOT written."""
        env = self._session_env(tmp_path, "session-b")
        with patch.dict(os.environ, env, clear=False):
            request_drain(tmp_path)
        workspace_root_signal = tmp_path / DRAIN_SIGNAL_NAME
        assert not workspace_root_signal.exists()

    @pytest.mark.unit
    def test_cancel_drain_removes_session_path(self, tmp_path: Path) -> None:
        """cancel_drain removes the per-session drain.signal when session name is set."""
        env = self._session_env(tmp_path, "session-c")
        with patch.dict(os.environ, env, clear=False):
            request_drain(tmp_path)
            result = cancel_drain(tmp_path)
        assert result is True
        expected = self._expected_session_signal(tmp_path, "session-c")
        assert not expected.exists()

    @pytest.mark.unit
    def test_cancel_drain_returns_false_when_session_signal_absent(self, tmp_path: Path) -> None:
        """cancel_drain returns False when per-session drain.signal is absent."""
        env = self._session_env(tmp_path, "session-d")
        with patch.dict(os.environ, env, clear=False):
            result = cancel_drain(tmp_path)
        assert result is False

    @pytest.mark.unit
    def test_read_drain_state_reads_session_path(self, tmp_path: Path) -> None:
        """read_drain_state reads from the per-session path when session name is set."""
        env = self._session_env(tmp_path, "session-e")
        with patch.dict(os.environ, env, clear=False):
            request_drain(tmp_path, reason="session-read")
            state = read_drain_state(tmp_path)
        assert state is not None
        assert state.reason == "session-read"

    @pytest.mark.unit
    def test_read_drain_state_returns_none_for_session_when_workspace_root_has_signal(self, tmp_path: Path) -> None:
        """read_drain_state returns None for session when only workspace-root signal exists."""
        # Write a drain signal at workspace root (no session env vars)
        request_drain(tmp_path, reason="root-only")
        # Now read with session env vars -- should NOT find the workspace-root signal
        env = self._session_env(tmp_path, "session-f")
        with patch.dict(os.environ, env, clear=False):
            state = read_drain_state(tmp_path)
        assert state is None

    @pytest.mark.unit
    def test_consume_drain_consumes_session_path(self, tmp_path: Path) -> None:
        """consume_drain reads and removes the per-session drain.signal."""
        env = self._session_env(tmp_path, "session-g")
        with patch.dict(os.environ, env, clear=False):
            request_drain(tmp_path, reason="session-consume")
            state = consume_drain(tmp_path)
        assert state is not None
        assert state.reason == "session-consume"
        expected = self._expected_session_signal(tmp_path, "session-g")
        assert not expected.exists()

    @pytest.mark.unit
    def test_consume_drain_does_not_touch_workspace_root_signal(self, tmp_path: Path) -> None:
        """consume_drain with session env set does not touch the workspace-root drain.signal."""
        # Write a workspace-root drain signal first
        request_drain(tmp_path, reason="root-signal")
        workspace_root_signal = tmp_path / DRAIN_SIGNAL_NAME
        assert workspace_root_signal.exists()

        # Now consume with session env -- should only look at session path
        env = self._session_env(tmp_path, "session-h")
        with patch.dict(os.environ, env, clear=False):
            state = consume_drain(tmp_path)
        # Session path has no signal, so consume returns None
        assert state is None
        # Workspace-root signal must still exist
        assert workspace_root_signal.exists()

    @pytest.mark.unit
    def test_session_isolation_across_two_sessions(self, tmp_path: Path) -> None:
        """Two session names produce isolated drain.signal paths that do not interfere."""
        env_s1 = self._session_env(tmp_path, "session-s1")
        env_s2 = self._session_env(tmp_path, "session-s2")

        # Write to session-s1
        with patch.dict(os.environ, env_s1, clear=False):
            request_drain(tmp_path, reason="s1-drain")

        # Session s2 must not see session s1's drain signal
        with patch.dict(os.environ, env_s2, clear=False):
            state_s2 = read_drain_state(tmp_path)
        assert state_s2 is None

        # Session s1 still has its signal
        with patch.dict(os.environ, env_s1, clear=False):
            state_s1 = read_drain_state(tmp_path)
        assert state_s1 is not None
        assert state_s1.reason == "s1-drain"

    @pytest.mark.unit
    def test_request_drain_returns_session_path(self, tmp_path: Path) -> None:
        """request_drain returns the session path (not workspace root) when session is active."""
        env = self._session_env(tmp_path, "session-ret")
        with patch.dict(os.environ, env, clear=False):
            result = request_drain(tmp_path)
        expected = self._expected_session_signal(tmp_path, "session-ret")
        assert result == expected

    @pytest.mark.parametrize("session_name", ["alpha", "beta-session", "session-123"])
    @pytest.mark.unit
    def test_resolve_drain_signal_path_parametrised(self, tmp_path: Path, session_name: str) -> None:
        """resolve_drain_signal_path correctly encodes various session names into the path."""
        with patch.dict(os.environ, {"DEVBENCH_SESSION_NAME": session_name}, clear=False):
            result = resolve_drain_signal_path(tmp_path)
        assert result == tmp_path / ".devbench" / "sessions" / session_name / "drain.signal"
