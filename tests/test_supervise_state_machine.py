"""SupervisorStateMachine: pure lifecycle transitions (AC-1, FR-27, Section 4.8).

The state machine has NO I/O; it validates that each event drives the documented
transition and rejects illegal transitions fail-fast. Phase 2 wires
launch -> ready -> orchestrate-injected -> working; quota/restart transitions are
defined here and exercised in full in Phase 3.
"""

from __future__ import annotations

import pytest

from devbench.constants import (
    SUPERVISE_STATE_COMPLETED_CLEAN,
    SUPERVISE_STATE_DRAINING,
    SUPERVISE_STATE_FAULTED,
    SUPERVISE_STATE_QUOTA_WAITING,
    SUPERVISE_STATE_RUNNING,
    SUPERVISE_STATE_STARTING,
    SUPERVISE_STATE_STOPPED,
)
from devbench.supervise import (
    SuperviseTransitionError,
    SupervisorStateMachine,
)


@pytest.mark.unit
class TestStartingToRunning:
    """AC-1: starting -> running on ready + orchestrate-injected."""

    def test_initial_state_is_starting(self) -> None:
        sm = SupervisorStateMachine()
        assert sm.state == SUPERVISE_STATE_STARTING

    def test_ready_then_inject_reaches_running(self) -> None:
        sm = SupervisorStateMachine()
        sm.on_event("ready")
        assert sm.state == SUPERVISE_STATE_STARTING
        sm.on_event("orchestrate-injected")
        assert sm.state == SUPERVISE_STATE_RUNNING

    def test_transition_records_history(self) -> None:
        sm = SupervisorStateMachine()
        sm.on_event("ready")
        sm.on_event("orchestrate-injected")
        assert (SUPERVISE_STATE_STARTING, SUPERVISE_STATE_RUNNING, "orchestrate-injected") in sm.history


@pytest.mark.unit
class TestRunningTransitions:
    """AC-1: running -> quota-waiting on quota-detected; -> completed/faulted."""

    def _running(self) -> SupervisorStateMachine:
        sm = SupervisorStateMachine()
        sm.on_event("ready")
        sm.on_event("orchestrate-injected")
        return sm

    def test_quota_detected(self) -> None:
        sm = self._running()
        sm.on_event("quota-detected")
        assert sm.state == SUPERVISE_STATE_QUOTA_WAITING

    def test_working_activity_stays_running(self) -> None:
        sm = self._running()
        sm.on_event("working-activity")
        assert sm.state == SUPERVISE_STATE_RUNNING

    def test_terminal_clean(self) -> None:
        sm = self._running()
        sm.on_event("terminal-clean")
        assert sm.state == SUPERVISE_STATE_COMPLETED_CLEAN
        assert sm.is_terminal()

    def test_fault(self) -> None:
        sm = self._running()
        sm.on_event("fault")
        assert sm.state == SUPERVISE_STATE_FAULTED
        assert sm.is_terminal()


@pytest.mark.unit
class TestGracefulDrainTransitions:
    """Section 4.2/4.8: an operator stop drains then reaches stopped (exit 0)."""

    def _running(self) -> SupervisorStateMachine:
        sm = SupervisorStateMachine()
        sm.on_event("ready")
        sm.on_event("orchestrate-injected")
        return sm

    def test_drain_requested_enters_draining(self) -> None:
        sm = self._running()
        sm.on_event("drain-requested")
        assert sm.state == SUPERVISE_STATE_DRAINING

    def test_draining_drain_complete_reaches_stopped(self) -> None:
        sm = self._running()
        sm.on_event("drain-requested")
        sm.on_event("drain-complete")
        assert sm.state == SUPERVISE_STATE_STOPPED
        assert sm.is_terminal()
        assert (SUPERVISE_STATE_DRAINING, SUPERVISE_STATE_STOPPED, "drain-complete") in sm.history

    def test_draining_stop_hard_still_reaches_stopped(self) -> None:
        sm = self._running()
        sm.on_event("drain-requested")
        sm.on_event("stop-hard")
        assert sm.state == SUPERVISE_STATE_STOPPED


@pytest.mark.unit
class TestIllegalTransitions:
    """Illegal transitions fail fast (no silent state corruption)."""

    def test_inject_before_ready_raises(self) -> None:
        sm = SupervisorStateMachine()
        with pytest.raises(SuperviseTransitionError, match="orchestrate-injected"):
            sm.on_event("orchestrate-injected")

    def test_unknown_event_raises(self) -> None:
        sm = SupervisorStateMachine()
        with pytest.raises(SuperviseTransitionError, match="unknown"):
            sm.on_event("not-a-real-event")

    def test_event_from_terminal_raises(self) -> None:
        sm = SupervisorStateMachine()
        sm.on_event("ready")
        sm.on_event("orchestrate-injected")
        sm.on_event("terminal-clean")
        with pytest.raises(SuperviseTransitionError):
            sm.on_event("working-activity")
