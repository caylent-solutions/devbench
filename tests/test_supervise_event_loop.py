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

import pexpect
import pytest
from fixtures.supervise import FakePexpectChild, _ScriptStep

from devbench.config_loader import SuperviseConfig
from devbench.constants import SUPERVISE_INJECTABLE_COMMANDS_DEFAULT
from devbench.supervise import (
    SUPERVISE_EXIT_REASON_GRACEFUL_STOP,
    SUPERVISE_STATE_COMPLETED_CLEAN,
    SUPERVISE_STATE_DRAINING,
    SUPERVISE_STATE_FAULTED,
    SUPERVISE_STATE_STOPPED,
    DetectionPatterns,
    EventLoopResult,
    PtyDriver,
    QuotaDecision,
    run_supervise_event_loop,
)


def _driver(child) -> PtyDriver:
    return PtyDriver(child=child, patterns=DetectionPatterns(SuperviseConfig().detection_patterns))


class _SpinnerChild:
    """A pexpect double that emits the working-prompt spinner FOREVER (no terminal).

    This reproduces the root-cause hang class (design point 1): claude's turn
    ended and the loop hung repeating the CLI auto-updater spinner -- the PTY kept
    emitting bytes so the PTY-silence idle timer NEVER fires, while NO real
    orchestrator work happens. Every ``expect`` matches the working-prompt pattern,
    so the loop classifies pure working-activity on every read (cumulative_idle is
    reset to 0 each iteration) and would spin forever absent the progress watchdog.

    When ``release`` is set True (e.g. by a relaunch callback simulating a resumed,
    healthy session) the NEXT ``expect`` raises EOF with ``exitstatus`` so the loop
    can reach a clean terminal -- letting a single stall+recover be asserted.
    """

    def __init__(self) -> None:
        self.sent: list[str] = []
        self.before = ""
        self.after = ""
        self.exitstatus: int | None = None
        self.release = False
        self.terminated = False
        self.terminate_force: bool | None = None
        self._alive = True

    def expect(self, patterns: list[str], timeout: int | None = None) -> int:
        import re as _re

        if self.release:
            self._alive = False
            self.exitstatus = 0
            self.before = "ALL_DONE"
            self.after = ""
            raise pexpect.EOF("released to clean terminal")
        emit = "esc to interrupt"
        for index, pattern in enumerate(patterns):
            if _re.search(pattern, emit):
                self.before = emit
                self.after = emit
                return index
        raise pexpect.TIMEOUT("spinner emitted no requested pattern")

    def sendline(self, payload: str = "") -> int:
        self.sent.append(payload)
        return len(payload) + 1

    def send(self, payload: str = "") -> int:
        self.sent.append(payload)
        return len(payload)

    def terminate(self, force: bool = False) -> bool:
        self.terminated = True
        self.terminate_force = force
        self._alive = False
        return True

    def isalive(self) -> bool:
        return self._alive


class _FakeClock:
    """A deterministic monotonic clock: each ``__call__`` advances by ``step``.

    The progress watchdog reads the clock once per loop iteration, so advancing a
    fixed ``step`` per call makes the stall arithmetic exact and NEEDS NO real
    sleep (CLAUDE.md Section 7.5): after ``ceil(progress_stall_seconds / step)``
    iterations of no log growth the watchdog trips.
    """

    def __init__(self, *, step: float) -> None:
        self._step = step
        self._now = 0.0

    def __call__(self) -> float:
        now = self._now
        self._now += self._step
        return now


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


@pytest.mark.unit
class TestEventLoopGracefulStop:
    """An operator ``stop.request`` drives running -> draining -> stopped (Section 4.2)."""

    def test_stop_request_drains_inflight_then_stops_exit_zero(self) -> None:
        # The stop_poll fires True on the first iteration: the loop enters
        # ``draining``, lets the in-flight turn finish (sends /exit), reads to the
        # child EOF, and reaches ``stopped`` (operator-initiated -> exit 0). The
        # /exit (drain_now) literal must have been injected into the child.
        child = FakePexpectChild(
            [
                # /exit is a SLASH command: it is typed (no newline), the menu
                # render settles, then a single Enter (\r) submits it. Gate the
                # post-submit output on that \r so wait_until_quiescent's
                # expect([r".+"]) settles (sees no available step -> TIMEOUT)
                # BEFORE the Enter, and the in-flight wind-down + clean EOF only
                # become visible after submission.
                _ScriptStep(emit="wrapping up current work unit", on_send=r"\r"),
                _ScriptStep(emit="", eof=True, exitstatus=0, on_send=r"\r"),
            ]
        )
        stop_calls = {"n": 0}

        def _stop_poll() -> bool:
            stop_calls["n"] += 1
            return stop_calls["n"] == 1

        result = run_supervise_event_loop(
            driver=_driver(child),
            config=SuperviseConfig(),
            quota_waiter=_StubQuotaWaiter(),
            log_poll=lambda: None,
            relaunch=lambda **_k: None,
            stop_poll=_stop_poll,
        )
        assert result.exit_code == 0
        assert result.final_state == SUPERVISE_STATE_STOPPED
        assert result.exit_reason == SUPERVISE_EXIT_REASON_GRACEFUL_STOP
        # The graceful drain injected the configured /exit (drain_now) command.
        assert SUPERVISE_INJECTABLE_COMMANDS_DEFAULT["drain_now"] in child.sent

    def test_stop_poll_default_none_does_not_drain(self) -> None:
        # With no stop_poll supplied (the default), a clean ALL_DONE still wins:
        # the absence of an operator stop must not perturb the normal terminal.
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
        assert result.final_state == SUPERVISE_STATE_COMPLETED_CLEAN

    def test_draining_state_is_observable_via_state_machine(self) -> None:
        # The loop must drive the machine through ``draining`` (FR-27) on the way
        # to ``stopped``; the recorded history is asserted so the edge is real.
        from devbench.supervise import SupervisorStateMachine

        sm = SupervisorStateMachine()
        sm.on_event("ready")
        sm.on_event("orchestrate-injected")
        child = FakePexpectChild(
            [
                # Gate on the submit Enter (\r): /exit is typed then submitted, so
                # the wind-down output only appears after the single Enter (the
                # quiescence wait settles on the unmet gate before submission).
                _ScriptStep(emit="finishing", on_send=r"\r"),
                _ScriptStep(emit="", eof=True, exitstatus=0, on_send=r"\r"),
            ]
        )
        result = run_supervise_event_loop(
            driver=_driver(child),
            config=SuperviseConfig(),
            quota_waiter=_StubQuotaWaiter(),
            log_poll=lambda: None,
            relaunch=lambda **_k: None,
            stop_poll=lambda: True,
            state_machine=sm,
        )
        assert result.final_state == SUPERVISE_STATE_STOPPED
        # The (running -> draining) edge fired before reaching stopped.
        assert (SUPERVISE_STATE_DRAINING, SUPERVISE_STATE_STOPPED, "drain-complete") in sm.history
        assert any(to == SUPERVISE_STATE_DRAINING for (_f, to, _e) in sm.history)


@pytest.mark.unit
class TestEventLoopProgressWatchdog:
    """The progress watchdog catches a work-progress stall the idle timer cannot.

    Design points 1-4: the root-cause hang has the PTY emitting spinner bytes
    forever (so the cumulative-idle timer NEVER fires) while the orchestrator's own
    log does NOT grow (no real orchestrate work). The watchdog watches log GROWTH,
    not PTY silence, so it trips within ``progress_stall_seconds`` and auto-restarts
    -- whereas a genuine long op (heartbeating the log) must NOT trip.
    """

    @staticmethod
    def _short_stall_config(**extra: int):
        # A tight stall window keeps the deterministic fake-clock arithmetic small.
        from dataclasses import replace

        from devbench.config_loader import SuperviseConfig

        base = SuperviseConfig()
        timeouts = replace(base.timeouts, progress_stall_seconds=600, **extra)
        return replace(base, timeouts=timeouts)

    def test_stall_trips_when_log_quiet_and_pty_spins(self) -> None:
        # The orchestrator log never grows (progress_poll always False) while the
        # PTY spins forever. The idle timer can NEVER fire (every read is working
        # activity). The progress watchdog must trip and auto-restart; the relaunch
        # releases the child to a clean terminal so a single stall+recover is seen.
        child = _SpinnerChild()
        relaunches: list = []

        def _relaunch(**k):
            relaunches.append(k)
            child.release = True  # the resumed session makes progress -> clean exit

        result = run_supervise_event_loop(
            driver=_driver(child),
            config=self._short_stall_config(),
            quota_waiter=_StubQuotaWaiter(),
            log_poll=lambda: None,
            relaunch=_relaunch,
            progress_poll=lambda: False,  # the orchestrator log NEVER grows
            clock=_FakeClock(step=100),
        )
        # The watchdog tripped exactly once and auto-restarted with resume context.
        assert len(relaunches) == 1
        assert relaunches[0].get("reason") == "progress-stall"
        assert relaunches[0].get("resume") is True
        # The hung child was terminated before the relaunch (unlike exit-42 EOF).
        assert child.terminated is True
        assert child.terminate_force is True
        # The restart was counted (persisted to registry.restart_count by the CLI).
        assert result.restarts_used == 1
        assert result.exit_code == 0
        assert result.final_state == SUPERVISE_STATE_COMPLETED_CLEAN

    def test_no_stall_when_long_op_heartbeats_the_log(self) -> None:
        # The orchestrator log is quiet ONLY because a genuine long op is running --
        # but the long-op heartbeat keeps GROWING the log (progress_poll True), so
        # the watchdog must NOT trip. The op then finishes clean. This is the
        # no-false-stall-on-long-op proof (design point 4): same spinner PTY, same
        # fake clock, but BECAUSE the log grows the watchdog never fires.
        child = _SpinnerChild()
        relaunches: list = []
        # The heartbeat arrives for many iterations (well past the stall window),
        # then the op completes and the child is released to a clean terminal.
        heartbeats = iter([True] * 50)

        def _progress_poll() -> bool:
            grew = next(heartbeats, False)
            if not grew:
                # The long op finished: release the child so the loop can terminate
                # cleanly (the test asserts the watchdog never tripped meanwhile).
                child.release = True
            return grew

        result = run_supervise_event_loop(
            driver=_driver(child),
            config=self._short_stall_config(),
            quota_waiter=_StubQuotaWaiter(),
            log_poll=lambda: None,
            relaunch=lambda **k: relaunches.append(k),
            progress_poll=_progress_poll,
            clock=_FakeClock(step=100),
        )
        # The watchdog NEVER tripped: the heartbeat-driven log growth kept resetting
        # the progress timer for the full (simulated) long op.
        assert relaunches == []
        assert child.terminated is False
        assert result.exit_code == 0
        assert result.final_state == SUPERVISE_STATE_COMPLETED_CLEAN

    def test_stall_restart_cap_exhausted_faults(self) -> None:
        # Every relaunch re-stalls (the resumed session immediately hangs again).
        # Bounded by max_attempts=2 the watchdog faults with
        # progress-stall-restart-cap-exhausted rather than restarting forever.
        from devbench.config_loader import SuperviseRestartConfig

        cfg = self._short_stall_config()
        from dataclasses import replace

        cfg = replace(cfg, restart=SuperviseRestartConfig(max_attempts=2))
        child = _SpinnerChild()
        relaunches: list = []
        result = run_supervise_event_loop(
            driver=_driver(child),
            config=cfg,
            quota_waiter=_StubQuotaWaiter(),
            log_poll=lambda: None,
            relaunch=lambda **k: relaunches.append(k),  # never releases -> keeps stalling
            progress_poll=lambda: False,
            clock=_FakeClock(step=100),
        )
        assert result.exit_code != 0
        assert result.final_state == SUPERVISE_STATE_FAULTED
        assert result.exit_reason == "progress-stall-restart-cap-exhausted"
        # Exactly max_attempts relaunches were attempted before giving up.
        assert len(relaunches) == 2
        assert result.restarts_used == 2

    def test_watchdog_disabled_when_progress_poll_is_none(self) -> None:
        # The SDK-style callers do not supply progress_poll; the watchdog must then
        # be inert (no behaviour change). A normal clean ALL_DONE still wins.
        child = FakePexpectChild(
            [
                _ScriptStep(emit="esc to interrupt"),
                _ScriptStep(emit="ALL_DONE", eof=True, exitstatus=0),
            ]
        )
        result = run_supervise_event_loop(
            driver=_driver(child),
            config=self._short_stall_config(),
            quota_waiter=_StubQuotaWaiter(),
            log_poll=lambda: None,
            relaunch=lambda **_k: None,
            clock=_FakeClock(step=100000),  # would trip instantly IF the watchdog ran
        )
        assert result.exit_code == 0
        assert result.final_state == SUPERVISE_STATE_COMPLETED_CLEAN

    def test_log_growth_resets_progress_timer_before_stall(self) -> None:
        # A single late heartbeat (log growth) just before the stall window elapses
        # resets the timer, so the watchdog does NOT trip on that window. Proves the
        # reset-on-growth branch (not merely the never-grows case).
        child = _SpinnerChild()
        relaunches: list = []
        # Grow on iterations 1-5, then go quiet; with step=100 and a 600 window the
        # reset on iter 5 pushes the next possible trip out, and the child is
        # released shortly after so no stall is reached.
        growth = iter([True, True, True, True, True])
        polls = {"n": 0}

        def _progress_poll() -> bool:
            polls["n"] += 1
            grew = next(growth, False)
            if polls["n"] >= 9:
                child.release = True
            return grew

        result = run_supervise_event_loop(
            driver=_driver(child),
            config=self._short_stall_config(),
            quota_waiter=_StubQuotaWaiter(),
            log_poll=lambda: None,
            relaunch=lambda **k: relaunches.append(k),
            progress_poll=_progress_poll,
            clock=_FakeClock(step=100),
        )
        assert relaunches == []
        assert result.exit_code == 0
        assert result.final_state == SUPERVISE_STATE_COMPLETED_CLEAN


@pytest.mark.unit
class TestEventLoopTurnContinuation:
    """A turn that ends awaiting input is deterministically re-driven (design point 6).

    The root-cause gap: after TDD GREEN claude's interactive turn ended printing
    "how would you like to proceed" and NOTHING re-drove it. The supervisor must
    detect the turn-end-awaiting-input prompt, re-inject the loop_continuation, and
    VERIFY it took (working-prompt ack). If the ack never comes (claude stayed
    idle), it is treated as a stall and the session is restarted -- NOT a
    fire-and-forget injection.
    """

    def test_turn_end_reinjects_continuation_then_continues(self) -> None:
        from devbench.constants import SUPERVISE_INJECTABLE_COMMANDS_DEFAULT

        loop_cont = SUPERVISE_INJECTABLE_COMMANDS_DEFAULT["loop_continuation"]
        child = FakePexpectChild(
            [
                # Turn ended awaiting input: the supervisor must re-inject.
                _ScriptStep(emit="How would you like to proceed?"),
                # After the continuation is SUBMITTED (\r), claude resumes working
                # then finishes clean. Gate on \r so the type->settle->Enter ack flow
                # works exactly like the orchestrate kickoff.
                _ScriptStep(emit="esc to interrupt", on_send=r"\r"),
                _ScriptStep(emit="ALL_DONE", eof=True, exitstatus=0, on_send=r"\r"),
            ]
        )
        result = run_supervise_event_loop(
            driver=_driver(child),
            config=SuperviseConfig(),
            quota_waiter=_StubQuotaWaiter(),
            log_poll=lambda: None,
            relaunch=lambda **_k: None,
        )
        # The continuation literal was injected to re-drive the loop across the turn.
        assert loop_cont in child.sent
        assert result.exit_code == 0
        assert result.final_state == SUPERVISE_STATE_COMPLETED_CLEAN

    def test_turn_end_no_ack_is_stall_and_restarts(self) -> None:
        # The continuation is injected but NO working ack comes back (claude stays
        # idle): a fire-and-forget would hang forever. The supervisor must treat the
        # missing ack as a progress stall and restart (bounded). The relaunch
        # releases a fresh child to a clean terminal so the recover is asserted.
        child = FakePexpectChild(
            [
                # Turn ended awaiting input; after the submit \r NO ack arrives (no
                # further step matches the working pattern) -> expect_working False.
                _ScriptStep(emit="How would you like to proceed?"),
            ]
        )
        relaunches: list = []

        def _relaunch(**k):
            relaunches.append(k)

        run_supervise_event_loop(
            driver=_driver(child),
            config=SuperviseConfig(),
            quota_waiter=_StubQuotaWaiter(),
            log_poll=lambda: None,
            relaunch=_relaunch,
        )
        # A no-ack continuation was escalated to a restart (not left hanging).
        assert len(relaunches) >= 1
        assert relaunches[0].get("reason") == "progress-stall"

    def test_turn_end_no_ack_at_restart_cap_faults(self) -> None:
        # With the restart budget already exhausted (max_attempts=0), a no-ack
        # continuation cannot restart -> it faults terminally with the
        # progress-stall cap reason (the cont_result-is-not-None branch).
        from dataclasses import replace

        from devbench.config_loader import SuperviseRestartConfig

        cfg = replace(SuperviseConfig(), restart=SuperviseRestartConfig(max_attempts=0))
        child = FakePexpectChild([_ScriptStep(emit="How would you like to proceed?")])
        relaunches: list = []
        result = run_supervise_event_loop(
            driver=_driver(child),
            config=cfg,
            quota_waiter=_StubQuotaWaiter(),
            log_poll=lambda: None,
            relaunch=lambda **k: relaunches.append(k),
        )
        assert relaunches == []  # the cap was already reached: no relaunch attempted
        assert result.exit_code != 0
        assert result.final_state == SUPERVISE_STATE_FAULTED
        assert result.exit_reason == "progress-stall-restart-cap-exhausted"
