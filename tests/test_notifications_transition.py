"""Transition-aware notification dispatch (#207).

The base ``notify_work_unit_blocked_operator`` is one-shot at the moment
``mark_blocked`` runs.  When a blocked task's classification later transitions
into ``OPERATOR_ACTION_REQUIRED`` (e.g. because a dep landed but the task
never auto-unblocked, or a ``[BLOCKED]`` audit went stale), no ping fires.

``notify_blocked_operator_transition`` closes that gap with a workspace-local
classification cache.  On each call:

- new == ``OPERATOR_ACTION_REQUIRED`` AND cached value is None / not-equal
  → fire raw notification once, then write the new value to cache.
- new != ``OPERATOR_ACTION_REQUIRED`` → update cache, no fire.
- cache corruption → treated as empty cache, regenerated on next write.

The cache lives at ``<workspace_root>/.devbench/notification-state.json``.
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

import pytest

from devbench.notifications import NOTIFICATION_STATE_FILENAME, notify_blocked_operator_transition

_OPERATOR = "OPERATOR_ACTION_REQUIRED"
_AWAITING = "AWAITING_DEPENDENCY"
_AUTO_CLEARING = "AUTO_CLEARING_VIA_PROPOSAL"


def _patch_event_enabled(enabled: bool):
    """Patch is_event_enabled so tests don't depend on RUNTIME_CONFIG state."""
    return patch("devbench.notifications.is_event_enabled", return_value=enabled)


def _cache_path(workspace_root: Path) -> Path:
    return workspace_root / ".devbench" / NOTIFICATION_STATE_FILENAME


@pytest.mark.unit
class TestNotifyBlockedOperatorTransition:
    """Each path the transition-aware dispatcher must honour."""

    def test_first_time_operator_action_required_fires(self, tmp_path: Path) -> None:
        """No cache entry + classification OPERATOR_ACTION_REQUIRED → one ping fires."""
        with (
            _patch_event_enabled(True),
            patch("devbench.notifications.notify_work_unit_blocked_operator") as raw,
        ):
            notify_blocked_operator_transition("E1-F1-S1-T1", "title", "reason", _OPERATOR, tmp_path)
        raw.assert_called_once_with("E1-F1-S1-T1", "title", "reason")

    def test_second_call_same_classification_no_fire(self, tmp_path: Path) -> None:
        """Idempotent: re-call with the same OPERATOR_ACTION_REQUIRED → no second ping."""
        with (
            _patch_event_enabled(True),
            patch("devbench.notifications.notify_work_unit_blocked_operator") as raw,
        ):
            notify_blocked_operator_transition("E1-F1-S1-T1", "title", "reason", _OPERATOR, tmp_path)
            notify_blocked_operator_transition("E1-F1-S1-T1", "title", "reason", _OPERATOR, tmp_path)
        assert raw.call_count == 1

    def test_awaiting_dependency_first_then_operator_fires_once(self, tmp_path: Path) -> None:
        """The headline scenario: AWAITING_DEPENDENCY (no fire) → OPERATOR (fires once)."""
        with (
            _patch_event_enabled(True),
            patch("devbench.notifications.notify_work_unit_blocked_operator") as raw,
        ):
            notify_blocked_operator_transition("E10-F2-S1-T3", "title", "reason", _AWAITING, tmp_path)
            assert raw.call_count == 0
            notify_blocked_operator_transition("E10-F2-S1-T3", "title", "reason", _OPERATOR, tmp_path)
        raw.assert_called_once_with("E10-F2-S1-T3", "title", "reason")

    def test_exit_from_operator_clears_so_subsequent_re_entry_fires(self, tmp_path: Path) -> None:
        """OPERATOR → AWAITING (no fire, cache updates) → OPERATOR again (fires)."""
        with (
            _patch_event_enabled(True),
            patch("devbench.notifications.notify_work_unit_blocked_operator") as raw,
        ):
            notify_blocked_operator_transition("E1-F1-S1-T1", "title", "reason1", _OPERATOR, tmp_path)
            notify_blocked_operator_transition("E1-F1-S1-T1", "title", "reason2", _AWAITING, tmp_path)
            notify_blocked_operator_transition("E1-F1-S1-T1", "title", "reason3", _OPERATOR, tmp_path)
        assert raw.call_count == 2
        # Last fire carries reason3, not the stale reason1.
        raw.assert_called_with("E1-F1-S1-T1", "title", "reason3")

    def test_non_operator_classification_never_fires(self, tmp_path: Path) -> None:
        """AWAITING_DEPENDENCY / AUTO_CLEARING_VIA_PROPOSAL → never fire."""
        with (
            _patch_event_enabled(True),
            patch("devbench.notifications.notify_work_unit_blocked_operator") as raw,
        ):
            notify_blocked_operator_transition("E1-F1-S1-T1", "title", "reason", _AWAITING, tmp_path)
            notify_blocked_operator_transition("E1-F1-S1-T1", "title", "reason", _AUTO_CLEARING, tmp_path)
        assert raw.call_count == 0

    def test_event_disabled_short_circuits(self, tmp_path: Path) -> None:
        """When the event toggle is off the cache file is never created."""
        with (
            _patch_event_enabled(False),
            patch("devbench.notifications.notify_work_unit_blocked_operator") as raw,
        ):
            notify_blocked_operator_transition("E1-F1-S1-T1", "title", "reason", _OPERATOR, tmp_path)
        assert raw.call_count == 0
        assert not _cache_path(tmp_path).exists()

    def test_corrupt_cache_treated_as_empty_and_regenerated(self, tmp_path: Path) -> None:
        """Truncated / non-JSON cache file is treated as empty and overwritten."""
        cache_dir = tmp_path / ".devbench"
        cache_dir.mkdir()
        _cache_path(tmp_path).write_text("not-valid-json{[", encoding="utf-8")

        with (
            _patch_event_enabled(True),
            patch("devbench.notifications.notify_work_unit_blocked_operator") as raw,
        ):
            notify_blocked_operator_transition("E1-F1-S1-T1", "title", "reason", _OPERATOR, tmp_path)
        raw.assert_called_once()
        # File regenerated with valid JSON containing the new entry.
        regenerated = json.loads(_cache_path(tmp_path).read_text(encoding="utf-8"))
        assert regenerated == {"E1-F1-S1-T1": _OPERATOR}

    def test_non_dict_cache_payload_treated_as_empty(self, tmp_path: Path) -> None:
        """Top-level non-dict JSON ([], 42, 'str', null) is treated as empty cache.

        Mirrors the fail-safe shape guard pattern used elsewhere (e.g. scope.json).
        """
        cache_dir = tmp_path / ".devbench"
        cache_dir.mkdir()
        _cache_path(tmp_path).write_text("[]", encoding="utf-8")

        with (
            _patch_event_enabled(True),
            patch("devbench.notifications.notify_work_unit_blocked_operator") as raw,
        ):
            notify_blocked_operator_transition("E1-F1-S1-T1", "title", "reason", _OPERATOR, tmp_path)
        raw.assert_called_once()
        assert json.loads(_cache_path(tmp_path).read_text(encoding="utf-8")) == {"E1-F1-S1-T1": _OPERATOR}

    def test_multiple_tasks_tracked_independently(self, tmp_path: Path) -> None:
        """Cache state for one task does not gate the other; both fire on first OPERATOR."""
        with (
            _patch_event_enabled(True),
            patch("devbench.notifications.notify_work_unit_blocked_operator") as raw,
        ):
            notify_blocked_operator_transition("E1-F1-S1-T1", "t1", "r1", _OPERATOR, tmp_path)
            notify_blocked_operator_transition("E2-F1-S1-T1", "t2", "r2", _OPERATOR, tmp_path)
        assert raw.call_count == 2
        cache = json.loads(_cache_path(tmp_path).read_text(encoding="utf-8"))
        assert cache == {"E1-F1-S1-T1": _OPERATOR, "E2-F1-S1-T1": _OPERATOR}
