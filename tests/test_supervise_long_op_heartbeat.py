"""Long-op heartbeat: keep the progress watchdog alive during a genuine long op.

Design point 4 (mechanism a): the in-session ``verify-ac`` runner shells out to
terraform apply / go test, which can run 30-60 min with ZERO orchestrator-log
output. Absent a heartbeat the progress watchdog would false-stall and kill a
healthy long op. The runner therefore emits a benign ``[LONG_OP_HEARTBEAT]`` line
to the orchestrator log on a configurable cadence (strictly < progress_stall) so
the watchdog's log-growth signal keeps advancing.

These tests pin the heartbeat wrapper's behaviour deterministically: the wrapped
op blocks until it has OBSERVED a heartbeat (an event, not a fixed sleep), the
heartbeat thread stops + joins on completion, and the result is passed through
unchanged. The heartbeat line is benign (matches no log_tail marker).
"""

from __future__ import annotations

import threading

import pytest

from devbench import cli


@pytest.mark.unit
class TestRunWithLongOpHeartbeat:
    """run_with_long_op_heartbeat emits periodic heartbeats around a blocking op."""

    def test_emits_at_least_one_heartbeat_during_a_long_op(self) -> None:
        beats: list[int] = []
        first_beat = threading.Event()

        def _emit(*, elapsed: int) -> None:
            beats.append(elapsed)
            first_beat.set()

        def _run() -> str:
            assert first_beat.wait(timeout=5), "heartbeat thread did not fire"
            return "op-result"

        result = cli.run_with_long_op_heartbeat(
            run=_run,
            heartbeat_interval_seconds=0,
            emit_heartbeat=_emit,
        )
        assert result == "op-result"
        assert len(beats) >= 1

    def test_result_passed_through_unchanged(self) -> None:
        sentinel = (127, "out", "err")
        result = cli.run_with_long_op_heartbeat(
            run=lambda: sentinel,
            heartbeat_interval_seconds=3600,
            emit_heartbeat=lambda *, elapsed: None,
        )
        assert result == sentinel

    def test_heartbeat_thread_stops_after_op_returns(self) -> None:
        beats: list[int] = []
        first_beat = threading.Event()

        def _emit(*, elapsed: int) -> None:
            beats.append(elapsed)
            first_beat.set()

        def _run() -> str:
            assert first_beat.wait(timeout=5)
            return "done"

        cli.run_with_long_op_heartbeat(
            run=_run,
            heartbeat_interval_seconds=0,
            emit_heartbeat=_emit,
        )
        count_at_return = len(beats)
        assert len(beats) == count_at_return

    def test_op_exception_still_stops_heartbeat(self) -> None:
        first_beat = threading.Event()

        def _emit(*, elapsed: int) -> None:
            first_beat.set()

        def _run() -> str:
            assert first_beat.wait(timeout=5)
            raise RuntimeError("boom")

        with pytest.raises(RuntimeError, match="boom"):
            cli.run_with_long_op_heartbeat(
                run=_run,
                heartbeat_interval_seconds=0,
                emit_heartbeat=_emit,
            )


@pytest.mark.unit
class TestLongOpHeartbeatLine:
    """The heartbeat line is benign: it matches no log_tail marker family."""

    def test_heartbeat_marker_matches_no_log_tail_marker(self) -> None:
        from devbench.config_loader import SuperviseLogTailConfig
        from devbench.constants import SUPERVISE_LONG_OP_HEARTBEAT_MARKER

        cfg = SuperviseLogTailConfig()
        all_markers = cfg.markers_clean + cfg.markers_quota + cfg.markers_fault + cfg.markers_restart
        for marker in all_markers:
            assert marker not in SUPERVISE_LONG_OP_HEARTBEAT_MARKER
            assert SUPERVISE_LONG_OP_HEARTBEAT_MARKER not in marker

    def test_format_heartbeat_message_includes_verb_unit_elapsed(self) -> None:
        from devbench.constants import SUPERVISE_LONG_OP_HEARTBEAT_MARKER

        msg = cli._format_long_op_heartbeat(verb="verify-ac", unit="E1-F1-S1-T1", elapsed_seconds=120)
        assert SUPERVISE_LONG_OP_HEARTBEAT_MARKER in msg
        assert "verify-ac" in msg
        assert "E1-F1-S1-T1" in msg
        assert "120" in msg
