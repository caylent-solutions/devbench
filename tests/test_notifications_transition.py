"""Transition-aware notification dispatch (#207 + #209).

The base notifier API in ``devbench.notifications`` is one-shot at
``mark_blocked`` time -- it cannot fire when a blocked task's classification
later drifts to a different bucket because no write-site code path calls
the notifier again.

``notify_blocked_classification_transition`` closes that gap with a
workspace-local classification cache.  On each call:

- new == one of the seven recognised classes AND cache value is None /
  not-equal AND the matching per-class toggle is enabled
  -> fire the per-class ``notify_work_unit_blocked_<class>`` once, then
     write the new value to cache.
- new != cached -> always update cache (regardless of toggle state) so a
  later toggle-on operator does not get back-fired pings for state that
  was already cached.
- cache corruption -> treated as empty cache, regenerated on next write.

The cache lives at ``<workspace_root>/.devbench/notification-state.json``.

#209 extends the original #207 behaviour beyond ``OPERATOR_ACTION_REQUIRED``
to every blocked classification, with one toggle per class.
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

import pytest

from devbench.notifications import (
    NOTIFICATION_STATE_FILENAME,
    notify_blocked_classification_transition,
    prune_notification_state_for_unblocked,
)

_OPERATOR = "OPERATOR_ACTION_REQUIRED"
_AWAITING = "AWAITING_DEPENDENCY"
_AUTO_CLEARING = "AUTO_CLEARING_VIA_PROPOSAL"
_RUNTIME_DEGRADATION = "RUNTIME_DEGRADATION"
_HELD = "HELD"
_BLOCKED_ON_HELD = "BLOCKED_ON_HELD"
_AMENDMENT_RECOVERY = "AWAITING_AMENDMENT_RECOVERY"

# Maps the test fixture's classification token to the notify_* attribute name
# in :mod:`devbench.notifications` that should fire for that classification.
_NOTIFY_FN_BY_CLASS = {
    _OPERATOR: "notify_work_unit_blocked_operator",
    _AWAITING: "notify_work_unit_blocked_awaiting_dependency",
    _AUTO_CLEARING: "notify_work_unit_blocked_auto_clearing",
    _RUNTIME_DEGRADATION: "notify_work_unit_blocked_runtime_degradation",
    _HELD: "notify_work_unit_blocked_held",
    _BLOCKED_ON_HELD: "notify_work_unit_blocked_on_held",
    _AMENDMENT_RECOVERY: "notify_work_unit_blocked_amendment_recovery",
}


def _patch_event_enabled(enabled: bool):
    """Patch is_event_enabled so tests don't depend on RUNTIME_CONFIG state."""
    return patch("devbench.notifications.is_event_enabled", return_value=enabled)


def _cache_path(workspace_root: Path) -> Path:
    return workspace_root / ".devbench" / NOTIFICATION_STATE_FILENAME


@pytest.mark.unit
class TestNotifyClassificationTransitionPerClass:
    """One ping fires per transition INTO each of the seven blocked classes."""

    @pytest.mark.parametrize("classification", list(_NOTIFY_FN_BY_CLASS.keys()))
    def test_first_time_entry_fires_matching_event(self, tmp_path: Path, classification: str) -> None:
        """Initial entry into each class fires its dedicated notify_* helper exactly once."""
        notify_fn_name = _NOTIFY_FN_BY_CLASS[classification]
        with (
            _patch_event_enabled(True),
            patch(f"devbench.notifications.{notify_fn_name}") as raw,
        ):
            notify_blocked_classification_transition("E1-F1-S1-T1", "title", "reason", classification, tmp_path)
        raw.assert_called_once_with("E1-F1-S1-T1", "title", "reason")

    @pytest.mark.parametrize("classification", list(_NOTIFY_FN_BY_CLASS.keys()))
    def test_repeated_same_classification_no_re_fire(self, tmp_path: Path, classification: str) -> None:
        """Repeat calls with the same classification are idempotent per class."""
        notify_fn_name = _NOTIFY_FN_BY_CLASS[classification]
        with (
            _patch_event_enabled(True),
            patch(f"devbench.notifications.{notify_fn_name}") as raw,
        ):
            notify_blocked_classification_transition("E1-F1-S1-T1", "title", "reason", classification, tmp_path)
            notify_blocked_classification_transition("E1-F1-S1-T1", "title", "reason", classification, tmp_path)
        assert raw.call_count == 1


@pytest.mark.unit
class TestNotifyClassificationTransitionBehaviour:
    """Cross-class behaviour: transitions, cache write semantics, toggle gating."""

    def test_class_a_to_class_b_fires_b_only(self, tmp_path: Path) -> None:
        """AWAITING -> OPERATOR fires OPERATOR's notify, not AWAITING's."""
        with (
            _patch_event_enabled(True),
            patch("devbench.notifications.notify_work_unit_blocked_awaiting_dependency") as awaiting,
            patch("devbench.notifications.notify_work_unit_blocked_operator") as operator,
        ):
            notify_blocked_classification_transition("E1-F1-S1-T1", "title", "reason", _AWAITING, tmp_path)
            notify_blocked_classification_transition("E1-F1-S1-T1", "title", "reason", _OPERATOR, tmp_path)
        awaiting.assert_called_once()
        operator.assert_called_once_with("E1-F1-S1-T1", "title", "reason")

    def test_toggle_off_no_fire_but_cache_still_updates(self, tmp_path: Path) -> None:
        """Toggle disabled: no ping fires, but cache STILL updates so a later
        toggle-on doesn't back-fire pings for state already observed."""
        with (
            _patch_event_enabled(False),
            patch("devbench.notifications.notify_work_unit_blocked_operator") as raw,
        ):
            notify_blocked_classification_transition("E1-F1-S1-T1", "title", "reason", _OPERATOR, tmp_path)
        assert raw.call_count == 0
        cache = json.loads(_cache_path(tmp_path).read_text(encoding="utf-8"))
        assert cache == {"E1-F1-S1-T1": _OPERATOR}

    def test_unknown_classification_no_op(self, tmp_path: Path) -> None:
        """An unknown classification token (defensive guard) does nothing --
        no cache write, no ping."""
        with (
            _patch_event_enabled(True),
            patch("devbench.notifications.notify_work_unit_blocked_operator") as raw,
        ):
            notify_blocked_classification_transition("E1-F1-S1-T1", "title", "reason", "MADE_UP_BUCKET", tmp_path)
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
            notify_blocked_classification_transition("E1-F1-S1-T1", "title", "reason", _OPERATOR, tmp_path)
        raw.assert_called_once()
        assert json.loads(_cache_path(tmp_path).read_text(encoding="utf-8")) == {"E1-F1-S1-T1": _OPERATOR}

    def test_non_dict_cache_payload_treated_as_empty(self, tmp_path: Path) -> None:
        """Top-level non-dict JSON ([], 42, 'str', null) is treated as empty cache."""
        cache_dir = tmp_path / ".devbench"
        cache_dir.mkdir()
        _cache_path(tmp_path).write_text("[]", encoding="utf-8")
        with (
            _patch_event_enabled(True),
            patch("devbench.notifications.notify_work_unit_blocked_operator") as raw,
        ):
            notify_blocked_classification_transition("E1-F1-S1-T1", "title", "reason", _OPERATOR, tmp_path)
        raw.assert_called_once()
        assert json.loads(_cache_path(tmp_path).read_text(encoding="utf-8")) == {"E1-F1-S1-T1": _OPERATOR}

    def test_multiple_tasks_tracked_independently(self, tmp_path: Path) -> None:
        """Cache state per task is independent."""
        with (
            _patch_event_enabled(True),
            patch("devbench.notifications.notify_work_unit_blocked_operator") as op,
            patch("devbench.notifications.notify_work_unit_blocked_held") as held,
        ):
            notify_blocked_classification_transition("E1-F1-S1-T1", "t1", "r1", _OPERATOR, tmp_path)
            notify_blocked_classification_transition("E2-F1-S1-T1", "t2", "r2", _HELD, tmp_path)
        op.assert_called_once_with("E1-F1-S1-T1", "t1", "r1")
        held.assert_called_once_with("E2-F1-S1-T1", "t2", "r2")
        cache = json.loads(_cache_path(tmp_path).read_text(encoding="utf-8"))
        assert cache == {"E1-F1-S1-T1": _OPERATOR, "E2-F1-S1-T1": _HELD}


@pytest.mark.unit
class TestPruneNotificationStateForUnblocked:
    """Cache cleanup so a task that exits then re-enters blocked re-fires."""

    def test_prune_removes_entries_not_in_blocked_set(self, tmp_path: Path) -> None:
        cache_dir = tmp_path / ".devbench"
        cache_dir.mkdir()
        _cache_path(tmp_path).write_text(
            json.dumps({"A": _OPERATOR, "B": _AWAITING, "C": _AUTO_CLEARING}),
            encoding="utf-8",
        )
        prune_notification_state_for_unblocked(tmp_path, blocked_unit_ids={"B"})
        assert json.loads(_cache_path(tmp_path).read_text(encoding="utf-8")) == {"B": _AWAITING}

    def test_prune_no_op_when_already_in_sync(self, tmp_path: Path) -> None:
        """Idempotent: pruning a cache that already matches the set leaves the file untouched."""
        cache_dir = tmp_path / ".devbench"
        cache_dir.mkdir()
        initial = {"A": _OPERATOR, "B": _AWAITING}
        _cache_path(tmp_path).write_text(json.dumps(initial), encoding="utf-8")
        mtime_before = _cache_path(tmp_path).stat().st_mtime_ns
        prune_notification_state_for_unblocked(tmp_path, blocked_unit_ids={"A", "B"})
        assert _cache_path(tmp_path).stat().st_mtime_ns == mtime_before
        assert json.loads(_cache_path(tmp_path).read_text(encoding="utf-8")) == initial

    def test_prune_empty_cache_is_safe(self, tmp_path: Path) -> None:
        """Missing cache file: prune is a no-op."""
        prune_notification_state_for_unblocked(tmp_path, blocked_unit_ids={"A"})
        assert not _cache_path(tmp_path).exists()

    def test_re_entry_after_prune_fires_fresh_ping(self, tmp_path: Path) -> None:
        """Task enters OPERATOR -> exits blocked (prune drops cache entry)
        -> re-enters OPERATOR -> fires again."""
        with (
            _patch_event_enabled(True),
            patch("devbench.notifications.notify_work_unit_blocked_operator") as raw,
        ):
            notify_blocked_classification_transition("E1-F1-S1-T1", "title", "reason", _OPERATOR, tmp_path)
            assert raw.call_count == 1
            prune_notification_state_for_unblocked(tmp_path, blocked_unit_ids=set())
            notify_blocked_classification_transition("E1-F1-S1-T1", "title", "reason2", _OPERATOR, tmp_path)
            assert raw.call_count == 2
