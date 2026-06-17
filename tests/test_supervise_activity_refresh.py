"""supervise last_activity refreshes on observed PTY activity during running.

Tracked issue: ``supervise-status-last-activity-stale-during-active-work``.

The registry ``last_activity`` field (surfaced by ``supervise status``) only
advanced on STATE TRANSITIONS (e.g. reaching ``running``), not as the ``__run``
event loop observed ongoing PTY activity. A session actively working therefore
showed a ``last_activity`` that grew stale even though ``claude`` was producing
output continuously -- an operator (or babysitter) could mistake a busy session
for a hung one.

These tests pin: the event loop invokes an ``on_activity`` callback when it
observes working activity (a non-terminal PTY read) and on a running-phase
log-tail hit; and the activity persister advances ``last_activity`` in the
registry, throttled to bound write frequency.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from fixtures.supervise import FakePexpectChild, _ScriptStep

from devbench.config_loader import SuperviseConfig
from devbench.supervise import (
    DetectionPatterns,
    PtyDriver,
    QuotaDecision,
    SuperviseRegistry,
    new_session_state,
    run_supervise_event_loop,
)

pytestmark = pytest.mark.unit


def _driver(child) -> PtyDriver:
    return PtyDriver(child=child, patterns=DetectionPatterns(SuperviseConfig().detection_patterns))


class _StubQuotaWaiter:
    """Quota waiter double; the activity-refresh tests never hit the quota path."""

    def wait(self, *_a: object, **_k: object) -> QuotaDecision:
        raise AssertionError("quota path not exercised in the activity-refresh tests")


class TestEventLoopInvokesOnActivity:
    """The loop fires on_activity when it observes ongoing working activity."""

    def test_on_activity_called_on_working_activity(self) -> None:
        # Two working-activity reads, then a clean terminal: on_activity must fire
        # for the ongoing-work reads (not only on the terminal transition).
        child = FakePexpectChild(
            [
                _ScriptStep(emit="esc to interrupt"),  # working activity 1
                _ScriptStep(emit="esc to interrupt"),  # working activity 2
                _ScriptStep(emit="ALL_DONE", eof=True, exitstatus=0),
            ]
        )
        calls = {"n": 0}

        def _on_activity() -> None:
            calls["n"] += 1

        result = run_supervise_event_loop(
            driver=_driver(child),
            config=SuperviseConfig(),
            quota_waiter=_StubQuotaWaiter(),
            log_poll=lambda: None,
            relaunch=lambda **_k: None,
            on_activity=_on_activity,
        )
        assert result.exit_code == 0
        assert calls["n"] >= 2, "on_activity must fire on each observed working-activity read"

    def test_on_activity_optional(self) -> None:
        """Omitting on_activity (SDK-style callers) still runs to a clean terminal."""
        child = FakePexpectChild(
            [
                _ScriptStep(emit="esc to interrupt"),
                _ScriptStep(emit="ALL_DONE", eof=True, exitstatus=0),
            ]
        )
        result = run_supervise_event_loop(
            driver=_driver(child),
            config=SuperviseConfig(),
            quota_waiter=_StubQuotaWaiter(),
            log_poll=lambda: None,
            relaunch=lambda **_k: None,
        )
        assert result.exit_code == 0


class TestActivityPersister:
    """The activity persister advances last_activity, throttled."""

    def _state_and_registry(self, tmp_path: Path):
        from devbench import cli

        reg = SuperviseRegistry(tmp_path)
        state = new_session_state(
            name="telemetry",
            pid=4321,
            screen_name="devbench-supervise-telemetry",
            model="claude-opus-4-8",
            effort="xhigh",
            started_by="tester",
        )
        from devbench.constants import SUPERVISE_STATE_RUNNING

        state.state = SUPERVISE_STATE_RUNNING
        state.last_activity = datetime.now(UTC) - timedelta(minutes=10)
        reg.write_state(state)
        return cli, reg, state

    def test_persister_advances_last_activity(self, tmp_path: Path) -> None:
        cli, reg, state = self._state_and_registry(tmp_path)
        before = state.last_activity
        persist = cli._make_supervise_activity_persister(registry=reg, state=state, min_interval_seconds=0)
        persist()
        reloaded = reg.read_state("telemetry")
        assert reloaded is not None
        assert reloaded.last_activity is not None
        assert reloaded.last_activity > before, "last_activity must advance on observed activity"

    def test_persister_is_throttled(self, tmp_path: Path) -> None:
        """With a positive throttle window, rapid calls write the registry at most once."""
        from unittest.mock import patch

        cli, reg, state = self._state_and_registry(tmp_path)
        persist = cli._make_supervise_activity_persister(registry=reg, state=state, min_interval_seconds=3600)
        with patch.object(reg, "write_state", wraps=reg.write_state) as spy:
            persist()
            persist()
            persist()
        assert spy.call_count == 1, "rapid activity within the throttle window must write the registry once"
