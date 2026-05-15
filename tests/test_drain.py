"""Tests for src/devbench/drain.py -- DRAIN_SIGNAL_NAME constant and DrainState dataclass.

Coverage requirement: 100% line + branch on devbench.drain.

Covers:
- DRAIN_SIGNAL_NAME constant value contract
- DrainState dataclass construction, to_dict, from_dict (happy path + error paths)
- request_drain: writes signal file atomically; respects USER/USERNAME env vars;
  falls back to "unknown"; returns signal path; parent dir created on demand
- cancel_drain: deletes file when present (returns True); idempotent when absent (returns False)
- read_drain_state: returns None when absent; returns DrainState when present;
  raises ValueError for invalid JSON; raises ValueError for non-dict JSON root;
  raises ValueError for missing required fields; raises ValueError for bad datetime
- consume_drain: returns None when absent; returns DrainState and deletes file when present;
  propagates ValueError from read_drain_state
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

    def test_value_equals_spec(self) -> None:
        assert DRAIN_SIGNAL_NAME == ".devbench/drain.signal"

    def test_type_is_str(self) -> None:
        assert isinstance(DRAIN_SIGNAL_NAME, str)


# ---------------------------------------------------------------------------
# DrainState dataclass
# ---------------------------------------------------------------------------


class TestDrainStateConstruction:
    """DrainState can be constructed with all fields and with reason defaulting to empty."""

    def test_all_fields_stored(self) -> None:
        state = DrainState(requested_at=_NOW, requested_by="alice", reason="maintenance")
        assert state.requested_at == _NOW
        assert state.requested_by == "alice"
        assert state.reason == "maintenance"

    def test_reason_defaults_to_empty_string(self) -> None:
        state = DrainState(requested_at=_NOW, requested_by="bob")
        assert state.reason == ""

    def test_requested_at_is_datetime(self) -> None:
        state = DrainState(requested_at=_NOW, requested_by="x")
        assert isinstance(state.requested_at, datetime)

    def test_requested_by_is_str(self) -> None:
        state = DrainState(requested_at=_NOW, requested_by="operator")
        assert isinstance(state.requested_by, str)


# ---------------------------------------------------------------------------
# DrainState.to_dict
# ---------------------------------------------------------------------------


class TestDrainStateToDict:
    """to_dict serializes all fields to a JSON-serializable dict."""

    def test_keys_present(self) -> None:
        d = DrainState(requested_at=_NOW, requested_by="alice", reason="r").to_dict()
        assert set(d.keys()) == {"requested_at", "requested_by", "reason"}

    def test_requested_at_is_iso_string(self) -> None:
        d = DrainState(requested_at=_NOW, requested_by="alice").to_dict()
        assert isinstance(d["requested_at"], str)
        # Must be parseable back to the same datetime
        parsed = datetime.fromisoformat(d["requested_at"])
        assert parsed == _NOW

    def test_requested_by_value(self) -> None:
        d = DrainState(requested_at=_NOW, requested_by="carol").to_dict()
        assert d["requested_by"] == "carol"

    def test_reason_value_when_set(self) -> None:
        d = DrainState(requested_at=_NOW, requested_by="x", reason="planned").to_dict()
        assert d["reason"] == "planned"

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

    def test_round_trip(self) -> None:
        original = DrainState(requested_at=_NOW, requested_by="alice", reason="r")
        restored = DrainState.from_dict(original.to_dict())
        assert restored.requested_at == original.requested_at
        assert restored.requested_by == original.requested_by
        assert restored.reason == original.reason

    def test_requested_at_utc_aware(self) -> None:
        state = DrainState.from_dict(self._make_dict())
        assert state.requested_at.tzinfo is not None
        utcoffset = state.requested_at.utcoffset()
        assert utcoffset is not None
        assert utcoffset.total_seconds() == 0

    def test_naive_datetime_gets_utc(self) -> None:
        d = self._make_dict(requested_at="2026-01-15T12:00:00")
        state = DrainState.from_dict(d)
        assert state.requested_at.tzinfo is not None

    def test_non_utc_datetime_normalised(self) -> None:
        # +05:30 is IST; should normalise to UTC
        d = self._make_dict(requested_at="2026-01-15T17:30:00+05:30")
        state = DrainState.from_dict(d)
        assert state.requested_at == datetime(2026, 1, 15, 12, 0, 0, tzinfo=UTC)

    def test_reason_optional_defaults_empty(self) -> None:
        d: dict[str, Any] = {"requested_at": "2026-01-15T12:00:00+00:00", "requested_by": "bob"}
        state = DrainState.from_dict(d)
        assert state.reason == ""

    def test_missing_requested_at_raises_key_error(self) -> None:
        d: dict[str, Any] = {"requested_by": "alice", "reason": "r"}
        with pytest.raises(KeyError):
            DrainState.from_dict(d)

    def test_missing_requested_by_raises_key_error(self) -> None:
        d: dict[str, Any] = {"requested_at": "2026-01-15T12:00:00+00:00", "reason": "r"}
        with pytest.raises(KeyError):
            DrainState.from_dict(d)

    def test_invalid_datetime_string_raises_value_error(self) -> None:
        d = self._make_dict(requested_at="not-a-datetime")
        with pytest.raises(ValueError, match="not a valid ISO 8601"):
            DrainState.from_dict(d)


# ---------------------------------------------------------------------------
# request_drain
# ---------------------------------------------------------------------------


class TestRequestDrain:
    """request_drain writes the drain signal file and returns its path."""

    def test_returns_signal_path(self, tmp_path: Path) -> None:
        path = request_drain(tmp_path)
        assert path == tmp_path / DRAIN_SIGNAL_NAME

    def test_file_created(self, tmp_path: Path) -> None:
        request_drain(tmp_path)
        assert (tmp_path / DRAIN_SIGNAL_NAME).exists()

    def test_file_contains_valid_json(self, tmp_path: Path) -> None:
        request_drain(tmp_path)
        raw = (tmp_path / DRAIN_SIGNAL_NAME).read_text(encoding="utf-8")
        data = json.loads(raw)
        assert isinstance(data, dict)

    def test_json_has_required_keys(self, tmp_path: Path) -> None:
        request_drain(tmp_path)
        data = json.loads((tmp_path / DRAIN_SIGNAL_NAME).read_text(encoding="utf-8"))
        assert "requested_at" in data
        assert "requested_by" in data
        assert "reason" in data

    def test_reason_written_when_provided(self, tmp_path: Path) -> None:
        request_drain(tmp_path, reason="upgrade")
        data = json.loads((tmp_path / DRAIN_SIGNAL_NAME).read_text(encoding="utf-8"))
        assert data["reason"] == "upgrade"

    def test_reason_empty_when_not_provided(self, tmp_path: Path) -> None:
        request_drain(tmp_path)
        data = json.loads((tmp_path / DRAIN_SIGNAL_NAME).read_text(encoding="utf-8"))
        assert data["reason"] == ""

    def test_requested_at_is_recent_utc(self, tmp_path: Path) -> None:
        before = datetime.now(tz=UTC)
        request_drain(tmp_path)
        after = datetime.now(tz=UTC)
        data = json.loads((tmp_path / DRAIN_SIGNAL_NAME).read_text(encoding="utf-8"))
        ts = datetime.fromisoformat(data["requested_at"])
        assert before <= ts <= after

    def test_requested_by_uses_user_env(self, tmp_path: Path) -> None:
        with patch.dict(os.environ, {"USER": "testuser"}, clear=False):
            request_drain(tmp_path)
        data = json.loads((tmp_path / DRAIN_SIGNAL_NAME).read_text(encoding="utf-8"))
        assert data["requested_by"] == "testuser"

    def test_requested_by_falls_back_to_username_env(self, tmp_path: Path) -> None:
        env = {k: v for k, v in os.environ.items() if k not in ("USER", "USERNAME")}
        env["USERNAME"] = "winuser"
        with patch.dict(os.environ, env, clear=True):
            request_drain(tmp_path)
        data = json.loads((tmp_path / DRAIN_SIGNAL_NAME).read_text(encoding="utf-8"))
        assert data["requested_by"] == "winuser"

    def test_requested_by_falls_back_to_unknown(self, tmp_path: Path) -> None:
        env = {k: v for k, v in os.environ.items() if k not in ("USER", "USERNAME")}
        with patch.dict(os.environ, env, clear=True):
            request_drain(tmp_path)
        data = json.loads((tmp_path / DRAIN_SIGNAL_NAME).read_text(encoding="utf-8"))
        assert data["requested_by"] == "unknown"

    def test_creates_parent_directory(self, tmp_path: Path) -> None:
        nested = tmp_path / "nested" / "workspace"
        request_drain(nested)
        assert (nested / DRAIN_SIGNAL_NAME).exists()

    def test_overwrites_existing_signal(self, tmp_path: Path) -> None:
        request_drain(tmp_path, reason="first")
        request_drain(tmp_path, reason="second")
        data = json.loads((tmp_path / DRAIN_SIGNAL_NAME).read_text(encoding="utf-8"))
        assert data["reason"] == "second"

    def test_exception_during_write_cleans_up_tmp_and_reraises(self, tmp_path: Path) -> None:
        """When the atomic write fails, the tmp file is removed and the exception re-raised."""
        # Pre-create the parent dir so mkdir doesn't fail
        (tmp_path / ".devbench").mkdir(parents=True, exist_ok=True)

        original_write = Path.write_text

        call_count = 0

        def failing_write(self: Path, *args: object, **kwargs: object) -> None:
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

    def test_exception_during_write_reraises_when_unlink_also_fails(self, tmp_path: Path) -> None:
        """When write fails and the subsequent tmp unlink also raises OSError,
        the original write exception is still re-raised (unlink error is suppressed)."""
        (tmp_path / ".devbench").mkdir(parents=True, exist_ok=True)

        original_write = Path.write_text
        original_unlink = Path.unlink

        def failing_write(self: Path, *args: object, **kwargs: object) -> None:
            if self.suffix == ".tmp":
                raise OSError("simulated disk full")
            return original_write(self, *args, **kwargs)

        def failing_unlink(self: Path, **kwargs: object) -> None:
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

    def test_returns_true_when_signal_present(self, tmp_path: Path) -> None:
        request_drain(tmp_path)
        result = cancel_drain(tmp_path)
        assert result is True

    def test_file_deleted_after_cancel(self, tmp_path: Path) -> None:
        request_drain(tmp_path)
        cancel_drain(tmp_path)
        assert not (tmp_path / DRAIN_SIGNAL_NAME).exists()

    def test_returns_false_when_signal_absent(self, tmp_path: Path) -> None:
        result = cancel_drain(tmp_path)
        assert result is False

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

    def test_returns_none_when_absent(self, tmp_path: Path) -> None:
        result = read_drain_state(tmp_path)
        assert result is None

    def test_returns_drain_state_when_present(self, tmp_path: Path) -> None:
        request_drain(tmp_path, reason="check")
        state = read_drain_state(tmp_path)
        assert state is not None
        assert isinstance(state, DrainState)

    def test_reason_preserved(self, tmp_path: Path) -> None:
        request_drain(tmp_path, reason="mycheck")
        state = read_drain_state(tmp_path)
        assert state is not None
        assert state.reason == "mycheck"

    def test_requested_by_preserved(self, tmp_path: Path) -> None:
        with patch.dict(os.environ, {"USER": "operator"}, clear=False):
            request_drain(tmp_path)
        state = read_drain_state(tmp_path)
        assert state is not None
        assert state.requested_by == "operator"

    def test_requested_at_utc_aware(self, tmp_path: Path) -> None:
        request_drain(tmp_path)
        state = read_drain_state(tmp_path)
        assert state is not None
        assert state.requested_at.tzinfo is not None

    def test_raises_value_error_for_invalid_json(self, tmp_path: Path) -> None:
        signal = tmp_path / DRAIN_SIGNAL_NAME
        signal.parent.mkdir(parents=True, exist_ok=True)
        signal.write_text("not-valid-json{{{", encoding="utf-8")
        with pytest.raises(ValueError, match="invalid JSON"):
            read_drain_state(tmp_path)

    def test_raises_value_error_for_non_dict_json(self, tmp_path: Path) -> None:
        signal = tmp_path / DRAIN_SIGNAL_NAME
        signal.parent.mkdir(parents=True, exist_ok=True)
        signal.write_text(json.dumps([1, 2, 3]), encoding="utf-8")
        with pytest.raises(ValueError, match="must contain a JSON object"):
            read_drain_state(tmp_path)

    def test_raises_value_error_for_missing_requested_at(self, tmp_path: Path) -> None:
        signal = tmp_path / DRAIN_SIGNAL_NAME
        signal.parent.mkdir(parents=True, exist_ok=True)
        signal.write_text(json.dumps({"requested_by": "alice", "reason": "r"}), encoding="utf-8")
        with pytest.raises(KeyError):
            read_drain_state(tmp_path)

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

    def test_returns_none_when_absent(self, tmp_path: Path) -> None:
        result = consume_drain(tmp_path)
        assert result is None

    def test_returns_drain_state_when_present(self, tmp_path: Path) -> None:
        request_drain(tmp_path, reason="consume-me")
        state = consume_drain(tmp_path)
        assert state is not None
        assert state.reason == "consume-me"

    def test_file_deleted_after_consume(self, tmp_path: Path) -> None:
        request_drain(tmp_path)
        consume_drain(tmp_path)
        assert not (tmp_path / DRAIN_SIGNAL_NAME).exists()

    def test_second_consume_returns_none(self, tmp_path: Path) -> None:
        request_drain(tmp_path)
        consume_drain(tmp_path)
        result = consume_drain(tmp_path)
        assert result is None

    def test_propagates_value_error_for_invalid_json(self, tmp_path: Path) -> None:
        signal = tmp_path / DRAIN_SIGNAL_NAME
        signal.parent.mkdir(parents=True, exist_ok=True)
        signal.write_text("bad json!!!", encoding="utf-8")
        with pytest.raises(ValueError):
            consume_drain(tmp_path)

    def test_returned_state_is_drain_state_type(self, tmp_path: Path) -> None:
        request_drain(tmp_path, reason="typed")
        state = consume_drain(tmp_path)
        assert isinstance(state, DrainState)

    def test_reason_matches_what_was_requested(self, tmp_path: Path) -> None:
        request_drain(tmp_path, reason="scheduled")
        state = consume_drain(tmp_path)
        assert state is not None
        assert state.reason == "scheduled"
