"""End-to-end ``__run`` event loop against scripted stub-claude outputs (FR-13/27).

Spec Section 4.1 step 8 / 4.6 / 4.8 / 4.9: after kickoff, the supervisor runs the
event loop watching the PTY + log-tail and handling clean/fault/quota/restart:

  ready -> working -> ALL_DONE  (child exit 0)  -> completed-clean, supervisor exit 0
  ready -> working -> crash     (child exit !=0) -> faulted, supervisor exit non-zero
  ready -> working -> quota-prompt              -> quota-waiting -> resume (NOT exit)
  ready -> working -> exit-42-equivalent        -> restarting -> relaunch (bounded)

Driven through the REAL PtyDriver wrapping the FakePexpectChild double (no real
claude, no real screen). The state machine transitions are asserted so FR-27 is
exercised end to end.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from fixtures.supervise import FakePexpectChild, _ScriptStep

from devbench.config_loader import SuperviseConfig
from devbench.supervise import (
    SUPERVISE_STATE_COMPLETED_CLEAN,
    SUPERVISE_STATE_FAULTED,
    DetectionPatterns,
    EventLoopResult,
    PtyDriver,
    QuotaDecision,
    run_supervise_event_loop,
)


def _driver(child: FakePexpectChild) -> PtyDriver:
    return PtyDriver(child=child, patterns=DetectionPatterns(SuperviseConfig().detection_patterns))


class _StubQuotaWaiter:
    """A QuotaWaiter stand-in for the loop tests (the real waiter is unit-tested elsewhere).

    ``decisions`` is an ordered list of :class:`QuotaDecision` values returned by
    successive ``wait_and_decide`` calls (so a WAIT-then-RESUME re-wait can be
    scripted); when exhausted the last value repeats. ``cap_exhausted`` forces a
    single FAULT decision.
    """

    def __init__(self, *, recovered: bool = True, cap_exhausted: bool = False, decisions=None) -> None:
        self._cap_exhausted = cap_exhausted
        if decisions is not None:
            self._decisions = list(decisions)
        elif recovered:
            self._decisions = [QuotaDecision.RESUME]
        else:
            self._decisions = [QuotaDecision.WAIT]
        self.calls = 0

    def parse_reset_at(self, _text: str):
        return datetime.now(UTC) + timedelta(hours=1)

    def wait_and_decide(self, *, reset_at, resumes_used):
        from devbench.supervise import QuotaDecisionResult

        self.calls += 1
        if self._cap_exhausted:
            return QuotaDecisionResult(
                action=QuotaDecision.FAULT,
                expected_resume=reset_at,
                exit_reason="quota-resume-cap-exhausted",
            )
        idx = min(self.calls - 1, len(self._decisions) - 1)
        action = self._decisions[idx]
        reason = "quota-resume-cap-exhausted" if action is QuotaDecision.FAULT else None
        return QuotaDecisionResult(action=action, expected_resume=reset_at, exit_reason=reason)


@pytest.mark.unit
class TestEventLoopCleanExit:
    """ALL_DONE with child exit 0 -> completed-clean, supervisor exit 0 (AC-13 shape)."""

    def test_all_done_clean_exit_zero(self) -> None:
        child = FakePexpectChild(
            [
                _ScriptStep(emit="esc to interrupt"),  # working activity
                _ScriptStep(emit="ALL_DONE", eof=True, exitstatus=0),  # terminal clean
            ]
        )
        result = run_supervise_event_loop(
            driver=_driver(child),
            config=SuperviseConfig(),
            quota_waiter=_StubQuotaWaiter(),
            log_poll=lambda: None,
            relaunch=lambda **_k: None,
        )
        assert isinstance(result, EventLoopResult)
        assert result.exit_code == 0
        assert result.final_state == SUPERVISE_STATE_COMPLETED_CLEAN
        assert result.exit_reason == "all-done"


@pytest.mark.unit
class TestEventLoopCrash:
    """A crash + non-zero child exit -> faulted, supervisor exit non-zero (AC-14 shape)."""

    def test_crash_faults_nonzero(self) -> None:
        child = FakePexpectChild(
            [
                _ScriptStep(emit="esc to interrupt"),
                _ScriptStep(emit="Traceback (most recent call last): boom", eof=True, exitstatus=1),
            ]
        )
        result = run_supervise_event_loop(
            driver=_driver(child),
            config=SuperviseConfig(),
            quota_waiter=_StubQuotaWaiter(),
            log_poll=lambda: None,
            relaunch=lambda **_k: None,
        )
        assert result.exit_code != 0
        assert result.final_state == SUPERVISE_STATE_FAULTED
        assert result.exit_reason.startswith("claude-exit-")


@pytest.mark.unit
class TestEventLoopQuotaWaitThenResume:
    """A quota prompt -> quota-waiting -> resume (NOT a non-zero exit) (AC-9/AC-15 shape)."""

    def test_quota_prompt_waits_then_resumes_then_clean(self) -> None:
        # The child emits a quota-limit line, then (after the resume) becomes ready
        # again and finishes clean. The loop must NOT exit non-zero on the quota.
        child = FakePexpectChild(
            [
                _ScriptStep(emit="You've hit your limit; resets 8:00am (UTC)"),  # quota
                _ScriptStep(emit="ALL_DONE", eof=True, exitstatus=0),  # post-resume clean
            ]
        )
        waiter = _StubQuotaWaiter(recovered=True)
        relaunches: list = []
        result = run_supervise_event_loop(
            driver=_driver(child),
            config=SuperviseConfig(),
            quota_waiter=waiter,
            log_poll=lambda: None,
            relaunch=lambda **k: relaunches.append(k),
        )
        # Quota was handled (waiter consulted) and the run still ended cleanly 0.
        assert waiter.calls == 1
        assert result.exit_code == 0
        assert result.final_state == SUPERVISE_STATE_COMPLETED_CLEAN

    def test_quota_resume_cap_exhausted_faults(self) -> None:
        child = FakePexpectChild(
            [
                _ScriptStep(emit="You've hit your limit; resets 8:00am (UTC)"),
            ]
        )
        waiter = _StubQuotaWaiter(cap_exhausted=True)
        result = run_supervise_event_loop(
            driver=_driver(child),
            config=SuperviseConfig(),
            quota_waiter=waiter,
            log_poll=lambda: None,
            relaunch=lambda **_k: None,
        )
        assert result.exit_code != 0
        assert result.final_state == SUPERVISE_STATE_FAULTED
        assert result.exit_reason == "quota-resume-cap-exhausted"


@pytest.mark.unit
class TestEventLoopRestartSignal:
    """exit-42-equivalent within bound -> restarting -> relaunch (AC-10/AC-16 shape)."""

    def test_restart_signal_relaunches_then_clean(self) -> None:
        restart_line = "[ORCHESTRATOR_AUTO_RESTART] reason=runtime_degradation tasks=1"
        child = FakePexpectChild(
            [
                _ScriptStep(emit=restart_line, eof=True, exitstatus=42),
                _ScriptStep(emit="ALL_DONE", eof=True, exitstatus=0),  # after relaunch
            ]
        )
        relaunches: list = []
        result = run_supervise_event_loop(
            driver=_driver(child),
            config=SuperviseConfig(),
            quota_waiter=_StubQuotaWaiter(),
            log_poll=lambda: None,
            relaunch=lambda **k: relaunches.append(k),
        )
        assert len(relaunches) == 1
        assert result.exit_code == 0
        assert result.final_state == SUPERVISE_STATE_COMPLETED_CLEAN

    def test_restart_cap_exhausted_faults(self) -> None:
        # Every relaunch immediately re-signals exit-42; bounded by max_attempts=2
        # the loop faults with restart-cap-exhausted rather than looping forever.
        steps = [_ScriptStep(emit="[ORCHESTRATOR_AUTO_RESTART] tasks=1", eof=True, exitstatus=42) for _ in range(5)]
        child = FakePexpectChild(steps)
        cfg = SuperviseConfig()
        from dataclasses import replace

        from devbench.config_loader import SuperviseRestartConfig

        cfg = replace(cfg, restart=SuperviseRestartConfig(max_attempts=2))
        relaunches: list = []
        result = run_supervise_event_loop(
            driver=_driver(child),
            config=cfg,
            quota_waiter=_StubQuotaWaiter(),
            log_poll=lambda: None,
            relaunch=lambda **k: relaunches.append(k),
        )
        assert result.exit_code != 0
        assert result.final_state == SUPERVISE_STATE_FAULTED
        assert result.exit_reason == "restart-cap-exhausted"
        # Exactly max_attempts relaunches were attempted before giving up.
        assert len(relaunches) == 2


@pytest.mark.unit
class TestEventLoopLogTailDetection:
    """A clean terminal observed via the log-tail (not the PTY) still exits 0 (FR-14 hybrid)."""

    def test_clean_via_log_tail(self) -> None:
        from devbench.supervise import LogTailHit, LogTailKind

        # The PTY shows only working activity then EOF with status 0; the clean
        # signal arrives via the orchestrator log-tail.
        child = FakePexpectChild(
            [
                _ScriptStep(emit="esc to interrupt"),
                _ScriptStep(emit="", eof=True, exitstatus=0),
            ]
        )
        hits = iter([None, LogTailHit(kind=LogTailKind.CLEAN, line="ALL_DONE")])
        result = run_supervise_event_loop(
            driver=_driver(child),
            config=SuperviseConfig(),
            quota_waiter=_StubQuotaWaiter(),
            log_poll=lambda: next(hits, None),
            relaunch=lambda **_k: None,
        )
        assert result.exit_code == 0
        assert result.final_state == SUPERVISE_STATE_COMPLETED_CLEAN

    def test_fault_via_log_tail(self) -> None:
        from devbench.supervise import LogTailHit, LogTailKind

        # A FAULT log marker terminates the loop with a classified non-zero exit
        # even though the PTY only shows working activity.
        child = FakePexpectChild([_ScriptStep(emit="esc to interrupt")])
        hits = iter([LogTailHit(kind=LogTailKind.FAULT, line="[ORCHESTRATOR_STOP_REASON] reason=premature-turn-end")])
        result = run_supervise_event_loop(
            driver=_driver(child),
            config=SuperviseConfig(),
            quota_waiter=_StubQuotaWaiter(),
            log_poll=lambda: next(hits, None),
            relaunch=lambda **_k: None,
        )
        assert result.exit_code != 0
        assert result.final_state == SUPERVISE_STATE_FAULTED
        assert result.exit_reason.startswith("stop-reason-")

    def test_advisory_log_hit_falls_through_to_pty(self) -> None:
        from devbench.supervise import LogTailHit, LogTailKind

        # A QUOTA/RESTART log marker is advisory: the loop falls through to the
        # PTY (authoritative), which here finishes clean.
        child = FakePexpectChild([_ScriptStep(emit="ALL_DONE", eof=True, exitstatus=0)])
        hits = iter([LogTailHit(kind=LogTailKind.RESTART, line="[ORCHESTRATOR_AUTO_RESTART] tasks=1")])
        result = run_supervise_event_loop(
            driver=_driver(child),
            config=SuperviseConfig(),
            quota_waiter=_StubQuotaWaiter(),
            log_poll=lambda: next(hits, None),
            relaunch=lambda **_k: None,
        )
        assert result.exit_code == 0
        assert result.final_state == SUPERVISE_STATE_COMPLETED_CLEAN


@pytest.mark.unit
class TestEventLoopMidSessionFaultAndTimeout:
    """Mid-session fault PTY markers and a prompt timeout both fault (Section 4.6)."""

    def test_circuit_breaker_pty_marker_faults(self) -> None:
        # A circuit-breaker line on the PTY (no EOF) faults immediately.
        child = FakePexpectChild(
            [
                _ScriptStep(emit="[CIRCUIT_BREAKER] cascade depth exceeded"),
            ]
        )
        result = run_supervise_event_loop(
            driver=_driver(child),
            config=SuperviseConfig(),
            quota_waiter=_StubQuotaWaiter(),
            log_poll=lambda: None,
            relaunch=lambda **_k: None,
        )
        assert result.exit_code != 0
        assert result.final_state == SUPERVISE_STATE_FAULTED
        assert result.exit_reason == "circuit-breaker"

    def test_terminal_marker_in_pty_text_before_eof(self) -> None:
        # The child prints "ALL_DONE" as on-screen text (no EOF yet) -- the loop
        # records it as working activity and keeps reading until the real EOF, so
        # the clean classification comes from the child's exit-0 (defense: a text
        # match alone is not a terminal). Exercises the ALL_DONE text branch.
        child = FakePexpectChild(
            [
                _ScriptStep(emit="ALL_DONE printed mid-stream"),  # text, not EOF
                _ScriptStep(emit="", eof=True, exitstatus=0),  # real terminal
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
        assert result.final_state == SUPERVISE_STATE_COMPLETED_CLEAN

    def test_no_actionable_text_in_pty_before_eof(self) -> None:
        # Same as above for the NO_ACTIONABLE text branch.
        child = FakePexpectChild(
            [
                _ScriptStep(emit="NO_ACTIONABLE in scope right now"),
                _ScriptStep(emit="", eof=True, exitstatus=0),
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
        assert result.final_state == SUPERVISE_STATE_COMPLETED_CLEAN

    def test_prompt_timeout_faults(self) -> None:
        # No scripted steps: the first read_chunk TIMEOUTs -> prompt-timeout fault.
        child = FakePexpectChild([])
        result = run_supervise_event_loop(
            driver=_driver(child),
            config=SuperviseConfig(),
            quota_waiter=_StubQuotaWaiter(),
            log_poll=lambda: None,
            relaunch=lambda **_k: None,
        )
        assert result.exit_code != 0
        assert result.final_state == SUPERVISE_STATE_FAULTED
        assert result.exit_reason == "prompt-timeout-idle"

    def test_quota_wait_keeps_waiting_then_resumes_then_clean(self) -> None:
        # The waiter returns WAIT first (window not refreshed), then RESUME on the
        # re-wait. The supervisor stays in quota-waiting across the WAIT (NEVER an
        # exit), then resumes -> relaunch -> the post-resume PTY is a clean terminal.
        child = FakePexpectChild(
            [
                _ScriptStep(emit="You've hit your limit; resets 8:00am (UTC)"),
                _ScriptStep(emit="ALL_DONE", eof=True, exitstatus=0),  # after resume relaunch
            ]
        )
        waiter = _StubQuotaWaiter(decisions=[QuotaDecision.WAIT, QuotaDecision.RESUME])
        relaunches: list = []
        result = run_supervise_event_loop(
            driver=_driver(child),
            config=SuperviseConfig(),
            quota_waiter=waiter,
            log_poll=lambda: None,
            relaunch=lambda **k: relaunches.append(k),
        )
        # The wait was re-delegated once (WAIT) before recovering (RESUME).
        assert waiter.calls == 2
        assert len(relaunches) == 1
        assert result.exit_code == 0
        assert result.final_state == SUPERVISE_STATE_COMPLETED_CLEAN
