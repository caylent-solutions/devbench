"""Within-claim repeated-identical-failure bound (churn prevention).

A single in-progress claim that repeats the SAME unresolvable failure for hours
-- while staying "busy" so the inactivity budget keeps resetting -- must BLOCK
rather than churn. ``ClaimConvergenceTracker`` watches the SDK message stream
for a repeated identical failing AC-verify / TDD-RED / test signature within one
claim and trips the ``[CLAIM_NOT_CONVERGING]`` bound when the SAME signature
recurs ``max_within_claim_attempts`` times.

CRITICAL: the bound keys on REPEATED IDENTICAL failure, never raw duration, so a
genuinely-progressing long live run (a different signal each round) is NOT
killed.

The tracker is a pure object driven with synthetic SDK message doubles, so the
tests never spawn a session.
"""

from __future__ import annotations

import os
import signal
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from devbench.cli import (
    ClaimConvergenceTracker,
    _extract_failure_signature,
)

_BLOCK_INDEX = """\
# Backlog

## Status Summary

| Epic | Title | Done | In Progress | In Queue | Blocked |
|------|-------|------|-------------|----------|---------|
| EX | Example Epic | 0 | 1 | 0 | 0 |

## Full Work Unit Index

| ID | Title | Type | Status | Dependencies | Repo | File Path |
|----|-------|------|--------|--------------|------|-----------|
| EX-F1-S1-T1 | Sample | Task | in-progress | None | caylent-solutions/example | `backlog/EX-F1-S1-T1.md` |
"""

_BLOCK_WU = """\
# EX-F1-S1-T1: Sample

## Status: in-progress

## Description

x

## Dependencies

| ID | Title | Status |
|----|-------|--------|
| none | | |

## Acceptance Criteria

- [ ] AC-1 something

## Changes Manifest

| File | Change |
|------|--------|
| `src/x.py` | modify |

## Definition of Done

- [ ] done
"""


def _write_block_backlog(tmp_path: Path, monkeypatch: Any) -> None:
    """Write the minimal index + unit and point the cli module at it."""
    from devbench import cli

    (tmp_path / "BACKLOG.md").write_text(_BLOCK_INDEX, encoding="utf-8")
    backlog = tmp_path / "backlog"
    backlog.mkdir()
    (backlog / "EX-F1-S1-T1.md").write_text(_BLOCK_WU, encoding="utf-8")
    monkeypatch.setattr(cli, "BACKLOG_ROOT", backlog)
    monkeypatch.setattr(cli, "BACKLOG_INDEX", tmp_path / "BACKLOG.md")


def _spawn_sleeper_group() -> tuple[subprocess.Popen[bytes], int]:
    """Spawn a long-lived sleeper as a session leader; return (proc, pgid).

    ``start_new_session=True`` puts the child in its OWN process group whose
    pgid equals the child pid -- the positively-attributed group a real
    executor's ``make``/``go test``/``terraform`` tree would form when launched
    the same way.
    """
    proc = subprocess.Popen(
        [sys.executable, "-c", "import time; time.sleep(120)"],
        start_new_session=True,
    )
    return proc, os.getpgid(proc.pid)


def _wait_until_dead(proc: subprocess.Popen[bytes], *, timeout: float = 5.0) -> bool:
    """Poll until *proc* terminates (readiness detection, not a fixed sleep)."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if proc.poll() is not None:
            return True
        time.sleep(0.02)
    return proc.poll() is not None


@dataclass
class _ToolUseBlock:
    name: str
    input: dict[str, Any]


@dataclass
class _ToolResultBlock:
    """Duck-typed double for the SDK's ToolResultBlock (the result of a Bash run)."""

    content: str | list[dict[str, Any]] | None = None
    is_error: bool | None = None
    tool_use_id: str = "toolu_x"


@dataclass
class _Msg:
    content: list[Any]


def _bash(command: str) -> _Msg:
    return _Msg(content=[_ToolUseBlock(name="Bash", input={"command": command})])


def _claim_msg(unit_id: str) -> _Msg:
    return _bash(f"uv run devbench claim {unit_id}")


def _verify_fail(unit_id: str) -> _Msg:
    return _bash(f"uv run devbench verify-ac {unit_id}")


def _pytest_run(target: str) -> _Msg:
    return _bash(f"uv run pytest {target}")


def _timeout_result(text: str = "Command timed out after 120s") -> _Msg:
    return _Msg(content=[_ToolResultBlock(content=text, is_error=True)])


class TestExtractFailureSignature:
    def test_verify_ac_command_yields_signature(self) -> None:
        sig = _extract_failure_signature(_verify_fail("E1-F1-S1-T1"))
        assert sig is not None
        assert "verify-ac" in sig
        assert "E1-F1-S1-T1" in sig

    def test_identical_verify_commands_share_signature(self) -> None:
        a = _extract_failure_signature(_verify_fail("E1-F1-S1-T1"))
        b = _extract_failure_signature(_verify_fail("E1-F1-S1-T1"))
        assert a == b

    def test_test_command_yields_signature(self) -> None:
        sig = _extract_failure_signature(_bash("make tf-test MODULE_PATH=providers/aws/alb-listener"))
        assert sig is not None
        assert "alb-listener" in sig

    def test_non_failure_command_yields_none(self) -> None:
        assert _extract_failure_signature(_claim_msg("E1-F1-S1-T1")) is None
        assert _extract_failure_signature(_bash("uv run devbench status")) is None

    def test_message_without_content_yields_none(self) -> None:
        assert _extract_failure_signature(object()) is None


class TestClaimConvergenceTrackerRepeatedFailure:
    def test_same_signature_n_times_trips_bound(self) -> None:
        tracker = ClaimConvergenceTracker(max_within_claim_attempts=4, max_claim_wall_clock_seconds=0)
        tracker.note_claim("E1-F1-S1-T1", now=0.0)
        result = None
        for i in range(4):
            tracker.observe(_bash("edit something"), now=float(i))
            result = tracker.observe(_verify_fail("E1-F1-S1-T1"), now=float(i))
        assert result is not None, "the bound must trip after the 4th identical failure"
        assert "verify-ac" in result and "E1-F1-S1-T1" in result

    def test_below_threshold_does_not_trip(self) -> None:
        tracker = ClaimConvergenceTracker(max_within_claim_attempts=4, max_claim_wall_clock_seconds=0)
        tracker.note_claim("E1-F1-S1-T1", now=0.0)
        results = [tracker.observe(_verify_fail("E1-F1-S1-T1"), now=float(i)) for i in range(3)]
        assert all(r is None for r in results)

    def test_varying_progress_long_run_not_blocked(self) -> None:
        tracker = ClaimConvergenceTracker(max_within_claim_attempts=4, max_claim_wall_clock_seconds=0)
        tracker.note_claim("E1-F1-S1-T1", now=0.0)
        modules = [f"providers/aws/mod-{i}" for i in range(40)]
        tripped = False
        for i, mod in enumerate(modules):
            r = tracker.observe(_bash(f"make tf-test MODULE_PATH={mod}"), now=float(i))
            if r is not None:
                tripped = True
        assert not tripped, "a varying-progress long run must NOT trip the repeated-failure bound"

    def test_new_claim_resets_counts(self) -> None:
        tracker = ClaimConvergenceTracker(max_within_claim_attempts=4, max_claim_wall_clock_seconds=0)
        tracker.note_claim("E1-F1-S1-T1", now=0.0)
        for i in range(3):
            tracker.observe(_verify_fail("E1-F1-S1-T1"), now=float(i))
        tracker.note_claim("E1-F1-S1-T2", now=10.0)
        r = tracker.observe(_verify_fail("E1-F1-S1-T1"), now=11.0)
        assert r is None

    def test_observe_before_claim_is_safe(self) -> None:
        tracker = ClaimConvergenceTracker(max_within_claim_attempts=4, max_claim_wall_clock_seconds=0)
        assert tracker.observe(_verify_fail("E1-F1-S1-T1"), now=0.0) is None

    def test_current_unit_id_tracks_claim(self) -> None:
        tracker = ClaimConvergenceTracker(max_within_claim_attempts=4, max_claim_wall_clock_seconds=0)
        tracker.note_claim("E1-F1-S1-T1", now=0.0)
        assert tracker.current_unit_id == "E1-F1-S1-T1"

    def test_clear_current_claim_stops_retripping_blocked_unit(self) -> None:
        tracker = ClaimConvergenceTracker(max_within_claim_attempts=2, max_claim_wall_clock_seconds=0)
        tracker.note_claim("E1-F1-S1-T1", now=0.0)
        first = None
        for i in range(2):
            first = tracker.observe(_verify_fail("E1-F1-S1-T1"), now=float(i))
        assert first is not None, "the bound trips on the 2nd identical failure"
        tracker.clear_current_claim()
        assert tracker.current_unit_id is None
        for i in range(5):
            assert tracker.observe(_verify_fail("E1-F1-S1-T1"), now=float(10 + i)) is None
        tracker.note_claim("E1-F1-S1-T2", now=100.0)
        again = None
        for i in range(2):
            again = tracker.observe(_verify_fail("E1-F1-S1-T2"), now=float(100 + i))
        assert again is not None


class TestClaimConvergenceTrackerWallClock:
    def test_wall_clock_backstop_trips(self) -> None:
        tracker = ClaimConvergenceTracker(max_within_claim_attempts=999, max_claim_wall_clock_seconds=100.0)
        tracker.note_claim("E1-F1-S1-T1", now=0.0)
        assert tracker.observe(_bash("edit"), now=50.0) is None
        result = tracker.observe(_bash("edit"), now=150.0)
        assert result is not None
        assert "wall-clock" in result.lower() or "E1-F1-S1-T1" in result

    def test_wall_clock_disabled_when_zero(self) -> None:
        tracker = ClaimConvergenceTracker(max_within_claim_attempts=4, max_claim_wall_clock_seconds=0)
        tracker.note_claim("E1-F1-S1-T1", now=0.0)
        result = None
        for i in range(50):
            result = tracker.observe(_bash(f"make tf-test MODULE_PATH=mod-{i}"), now=float(i) * 252.0)
        assert result is None

    def test_default_wall_clock_does_not_kill_legit_long_run(self) -> None:
        from devbench.constants import DEFAULT_MAX_CLAIM_WALL_CLOCK_SECONDS

        assert DEFAULT_MAX_CLAIM_WALL_CLOCK_SECONDS > 3.5 * 3600
        tracker = ClaimConvergenceTracker(
            max_within_claim_attempts=4,
            max_claim_wall_clock_seconds=DEFAULT_MAX_CLAIM_WALL_CLOCK_SECONDS,
        )
        tracker.note_claim("E1-F1-S1-T1", now=0.0)
        result = None
        for i in range(52):
            result = tracker.observe(_bash(f"make tf-test MODULE_PATH=mod-{i}"), now=float(i) * 242.0)
        assert result is None


class TestClaimConvergenceTrackerNoClaimActivityBackstop:
    """Inter-claim backstop for the 'active but no unit claimed' wedge.

    The orchestrator can keep emitting SDK messages (so the per-message
    inactivity timeout never fires) while NO unit is claimed -- e.g. an
    executor still churning AFTER its unit was force-blocked, or a loop stuck
    processing a huge command output without claiming the next unit. The
    streaming report shows 0 in-progress while hook-logs keep flowing. The
    per-claim bounds do not cover this because there is no current claim.

    SAFETY: a legitimate long op (a live terraform apply / terratest) always
    runs INSIDE a claim, where the per-signature + wall-clock bounds apply.
    Bounding the NO-CLAIM window therefore cannot false-positive a real long
    op; it only catches an orphaned/churning loop that claims no work.
    """

    def test_no_claim_activity_trips_after_window(self) -> None:
        tracker = ClaimConvergenceTracker(
            max_within_claim_attempts=4,
            max_claim_wall_clock_seconds=0,
            max_no_claim_activity_seconds=300.0,
        )
        assert tracker.observe(_bash("Read agent-transcript"), now=0.0) is None, (
            "the window only starts on the first no-claim message"
        )
        assert tracker.observe(_bash("Read agent-transcript"), now=299.0) is None, (
            "must not trip before the window elapses"
        )
        tripped = tracker.observe(_bash("Read agent-transcript"), now=300.0)
        assert tripped is not None, "must trip once active-but-no-claim exceeds the window"
        assert "no claim" in tripped.lower()

    def test_no_claim_backstop_disabled_when_zero(self) -> None:
        tracker = ClaimConvergenceTracker(
            max_within_claim_attempts=4,
            max_claim_wall_clock_seconds=0,
            max_no_claim_activity_seconds=0,
        )
        results = [tracker.observe(_bash("x"), now=float(i) * 1000) for i in range(5)]
        assert all(r is None for r in results)

    def test_claim_resets_no_claim_window_and_restarts_after_block(self) -> None:
        tracker = ClaimConvergenceTracker(
            max_within_claim_attempts=4,
            max_claim_wall_clock_seconds=0,
            max_no_claim_activity_seconds=300.0,
        )
        tracker.observe(_bash("Read"), now=0.0)
        tracker.note_claim("E1-F1-S1-T1", now=100.0)
        assert tracker.observe(_bash("work"), now=10_000.0) is None
        tracker.clear_current_claim()
        assert tracker.observe(_bash("Read"), now=10_001.0) is None
        assert tracker.observe(_bash("Read"), now=10_300.0) is None
        assert tracker.observe(_bash("Read"), now=10_301.0) is not None

    def test_default_no_claim_backstop_is_set_and_generous(self) -> None:
        from devbench.constants import DEFAULT_MAX_NO_CLAIM_ACTIVITY_SECONDS

        assert DEFAULT_MAX_NO_CLAIM_ACTIVITY_SECONDS > 0
        assert DEFAULT_MAX_NO_CLAIM_ACTIVITY_SECONDS >= 300


class TestNoClaimBackstopSuppressedByInProgressUnit:
    """Tracked-issue 003: the no-claim backstop is the 'active but ZERO claims
    in-progress' wedge only -- it must NOT fire while a unit is genuinely
    IN_PROGRESS and the executor is emitting SDK messages.

    The tracker's own ``current_unit_id`` can diverge from the authoritative
    backlog (e.g. it was cleared after a force-block, or the ``devbench claim``
    message was never observed) while a unit is in fact IN_PROGRESS in the
    backlog and its executor is emitting messages on a legitimately-long single
    claim (a live ``terragrunt apply``). The backstop is gated on the injected
    authoritative ``in_progress_count`` so a long-but-progressing claim is never
    misread as a stall; the WITHIN-claim wall-clock backstop already governs a
    genuinely-hung single claim.
    """

    def test_in_progress_claim_with_activity_does_not_trip_past_window(self) -> None:
        tracker = ClaimConvergenceTracker(
            max_within_claim_attempts=4,
            max_claim_wall_clock_seconds=0,
            max_no_claim_activity_seconds=300.0,
        )
        results = [
            tracker.observe(_bash("Read agent-transcript"), now=float(t), in_progress_count=1)
            for t in (0.0, 299.0, 300.0, 600.0, 100_000.0)
        ]
        assert all(r is None for r in results), (
            "a unit IS in-progress (executor active) -- the no-claim wedge backstop "
            "must be suppressed; the within-claim wall-clock backstop governs a hang"
        )

    def test_zero_in_progress_still_trips_the_wedge(self) -> None:
        tracker = ClaimConvergenceTracker(
            max_within_claim_attempts=4,
            max_claim_wall_clock_seconds=0,
            max_no_claim_activity_seconds=300.0,
        )
        assert tracker.observe(_bash("Read"), now=0.0, in_progress_count=0) is None
        assert tracker.observe(_bash("Read"), now=299.0, in_progress_count=0) is None
        tripped = tracker.observe(_bash("Read"), now=300.0, in_progress_count=0)
        assert tripped is not None, "the orphaned/wedge case (0 in-progress) must still trip"
        assert "no claim" in tripped.lower()

    def test_executor_activity_resets_the_timer(self) -> None:
        tracker = ClaimConvergenceTracker(
            max_within_claim_attempts=4,
            max_claim_wall_clock_seconds=0,
            max_no_claim_activity_seconds=300.0,
        )
        assert tracker.observe(_bash("Read"), now=0.0, in_progress_count=0) is None
        assert tracker.observe(_bash("work"), now=200.0, in_progress_count=1) is None
        assert tracker.observe(_bash("work"), now=10_000.0, in_progress_count=1) is None
        assert tracker.observe(_bash("Read"), now=10_001.0, in_progress_count=0) is None
        assert tracker.observe(_bash("Read"), now=10_300.0, in_progress_count=0) is None
        assert tracker.observe(_bash("Read"), now=10_301.0, in_progress_count=0) is not None


class TestCountInProgressUnits:
    """Tracked-issue 003: the authoritative IN_PROGRESS count gating the backstop."""

    def _unit(self, unit_id: str, status: Any) -> Any:
        from devbench.backlog.work_unit import WorkUnit, WorkUnitType

        return WorkUnit(
            id=unit_id,
            title=f"u {unit_id}",
            status=status,
            unit_type=WorkUnitType.TASK,
            file_path=Path(f"backlog/{unit_id}.md"),
            repo="caylent-solutions/example",
            dependencies=[],
        )

    def test_counts_only_in_progress_units(self, monkeypatch: Any) -> None:
        from unittest.mock import MagicMock

        from devbench import cli
        from devbench.backlog.work_unit import WorkUnitStatus

        units = [
            self._unit("E1-F1-S1-T1", WorkUnitStatus.IN_PROGRESS),
            self._unit("E1-F1-S1-T2", WorkUnitStatus.IN_PROGRESS),
            self._unit("E1-F1-S1-T3", WorkUnitStatus.IN_QUEUE),
            self._unit("E1-F1-S1-T4", WorkUnitStatus.IN_REVIEW),
            self._unit("E1-F1-S1-T5", WorkUnitStatus.DONE),
        ]
        parser = MagicMock()
        parser.parse_index.return_value = units
        monkeypatch.setattr(cli, "BacklogParser", MagicMock(return_value=parser))
        assert cli._count_in_progress_units() == 2

    def test_parse_failure_yields_zero(self, monkeypatch: Any) -> None:
        from unittest.mock import MagicMock

        from devbench import cli

        parser = MagicMock()
        parser.parse_index.side_effect = ValueError("malformed backlog index")
        monkeypatch.setattr(cli, "BacklogParser", MagicMock(return_value=parser))
        assert cli._count_in_progress_units() == 0


class TestResolveMaxNoClaimActivitySeconds:
    def test_default_when_unset(self, monkeypatch: Any) -> None:
        from devbench import cli
        from devbench.constants import DEFAULT_MAX_NO_CLAIM_ACTIVITY_SECONDS

        monkeypatch.delenv("DEVBENCH_ORCHESTRATOR_MAX_NO_CLAIM_ACTIVITY_SECONDS", raising=False)
        assert cli._resolve_max_no_claim_activity_seconds() == DEFAULT_MAX_NO_CLAIM_ACTIVITY_SECONDS

    def test_env_override(self, monkeypatch: Any) -> None:
        from devbench import cli

        monkeypatch.setenv("DEVBENCH_ORCHESTRATOR_MAX_NO_CLAIM_ACTIVITY_SECONDS", "123.5")
        assert cli._resolve_max_no_claim_activity_seconds() == 123.5


class TestClaimedUnitId:
    def test_extracts_unit_id_from_claim_command(self) -> None:
        from devbench import cli

        assert cli._claimed_unit_id(_claim_msg("E1-F1-S1-T1")) == "E1-F1-S1-T1"

    def test_none_for_non_claim(self) -> None:
        from devbench import cli

        assert cli._claimed_unit_id(_verify_fail("E1-F1-S1-T1")) is None


class TestClassifyOrchestratorExit:
    def test_too_many_non_converging_routes_through_auto_restart(self, monkeypatch) -> None:  # type: ignore[no-untyped-def]
        from devbench import cli

        captured: dict[str, str] = {}

        def _fake_restart(reason: str) -> tuple[int, str]:
            captured["reason"] = reason
            return 0, reason

        monkeypatch.setattr(cli, "_check_auto_restart_and_notify", _fake_restart)
        rc, reason = cli._classify_orchestrator_exit(
            fatal_error_code=None,
            continuation_exhausted=False,
            too_many_non_converging=3,
            sdk_result_text=None,
        )
        assert rc == 0
        assert "too many non-converging claims" in reason
        assert "(3)" in reason
        assert "(3)" in captured["reason"]

    def test_single_non_converging_does_not_halt(self, monkeypatch) -> None:  # type: ignore[no-untyped-def]
        from devbench import cli

        monkeypatch.setattr(cli, "_check_auto_restart_and_notify", lambda reason: (0, reason))
        rc, reason = cli._classify_orchestrator_exit(
            fatal_error_code=None,
            continuation_exhausted=False,
            too_many_non_converging=0,
            sdk_result_text="NO_ACTIONABLE -- 4/5 done, 1 blocked",
        )
        assert rc == 0
        assert "too many non-converging" not in reason
        assert reason.startswith("clean")

    def test_fatal_error_takes_precedence(self) -> None:
        from devbench import cli
        from devbench.constants import ORCHESTRATOR_FATAL_ERROR_EXIT_CODE

        rc, reason = cli._classify_orchestrator_exit(
            fatal_error_code="model_not_found",
            continuation_exhausted=True,
            too_many_non_converging=5,
            sdk_result_text=None,
        )
        assert rc == ORCHESTRATOR_FATAL_ERROR_EXIT_CODE
        assert "model_not_found" in reason


class TestBlockNonConvergingClaim:
    def test_force_blocks_unit_with_marker(self, tmp_path, monkeypatch) -> None:  # type: ignore[no-untyped-def]
        from devbench import cli
        from devbench.backlog.parser import BacklogParser
        from devbench.constants import CLAIM_NOT_CONVERGING_MARKER

        index = """\
# Backlog

## Status Summary

| Epic | Title | Done | In Progress | In Queue | Blocked |
|------|-------|------|-------------|----------|---------|
| EX | Example Epic | 0 | 1 | 0 | 0 |

## Full Work Unit Index

| ID | Title | Type | Status | Dependencies | Repo | File Path |
|----|-------|------|--------|--------------|------|-----------|
| EX-F1-S1-T1 | Sample | Task | in-progress | None | caylent-solutions/example | `backlog/EX-F1-S1-T1.md` |
"""
        wu = """\
# EX-F1-S1-T1: Sample

## Status: in-progress

## Description

x

## Dependencies

| ID | Title | Status |
|----|-------|--------|
| none | | |

## Acceptance Criteria

- [ ] AC-1 something

## Changes Manifest

| File | Change |
|------|--------|
| `src/x.py` | modify |

## Definition of Done

- [ ] done
"""
        (tmp_path / "BACKLOG.md").write_text(index, encoding="utf-8")
        backlog = tmp_path / "backlog"
        backlog.mkdir()
        (backlog / "EX-F1-S1-T1.md").write_text(wu, encoding="utf-8")

        monkeypatch.setattr(cli, "BACKLOG_ROOT", backlog)
        monkeypatch.setattr(cli, "BACKLOG_INDEX", tmp_path / "BACKLOG.md")

        cli._block_non_converging_claim("EX-F1-S1-T1", "verify-ac::EX-F1-S1-T1")

        updated = (backlog / "EX-F1-S1-T1.md").read_text(encoding="utf-8")
        assert "## Status: blocked" in updated
        assert CLAIM_NOT_CONVERGING_MARKER in updated
        assert "verify-ac::EX-F1-S1-T1" in updated
        parser = BacklogParser(backlog_root=backlog, backlog_index=tmp_path / "BACKLOG.md")
        unit = next(u for u in parser.parse_index() if u.id == "EX-F1-S1-T1")
        assert unit.status.value.lower() == "blocked"

    def test_missing_unit_is_safe(self, tmp_path, monkeypatch) -> None:  # type: ignore[no-untyped-def]
        from devbench import cli

        index = """\
# Backlog

## Status Summary

| Epic | Title | Done | In Progress | In Queue | Blocked |
|------|-------|------|-------------|----------|---------|
| EX | Example Epic | 0 | 0 | 0 | 0 |

## Full Work Unit Index

| ID | Title | Type | Status | Dependencies | Repo | File Path |
|----|-------|------|--------|--------------|------|-----------|
"""
        (tmp_path / "BACKLOG.md").write_text(index, encoding="utf-8")
        backlog = tmp_path / "backlog"
        backlog.mkdir()
        monkeypatch.setattr(cli, "BACKLOG_ROOT", backlog)
        monkeypatch.setattr(cli, "BACKLOG_INDEX", tmp_path / "BACKLOG.md")
        cli._block_non_converging_claim("EX-F1-S1-T9", "verify-ac::EX-F1-S1-T9")

    def test_unit_not_in_index_is_safe(self, monkeypatch) -> None:  # type: ignore[no-untyped-def]
        from types import SimpleNamespace

        from devbench import cli

        monkeypatch.setattr(cli, "BacklogParser", lambda **kwargs: SimpleNamespace(parse_index=list))
        monkeypatch.setattr(cli, "_find_unit", lambda _units, _uid: None)
        cli._block_non_converging_claim("EX-F1-S1-T9", "verify-ac::EX-F1-S1-T9")

    def test_unresolvable_file_is_safe(self, monkeypatch) -> None:  # type: ignore[no-untyped-def]
        from types import SimpleNamespace

        from devbench import cli

        unit = SimpleNamespace(id="EX-F1-S1-T1", repo="acme/x")
        monkeypatch.setattr(cli, "BacklogParser", lambda **kwargs: SimpleNamespace(parse_index=lambda: [unit]))
        monkeypatch.setattr(cli, "_find_unit", lambda _units, _uid: unit)
        monkeypatch.setattr(cli, "_resolve_unit_file", lambda _unit: None)
        cli._block_non_converging_claim("EX-F1-S1-T1", "verify-ac::EX-F1-S1-T1")


class TestResolveMaxNonConvergingClaims:
    """The aggregate safety-valve threshold K (env > YAML > constant default)."""

    def test_defaults_to_constant_when_unset(self, monkeypatch) -> None:  # type: ignore[no-untyped-def]
        from devbench import cli
        from devbench.constants import DEFAULT_MAX_NON_CONVERGING_CLAIMS

        monkeypatch.delenv("DEVBENCH_ORCHESTRATOR_MAX_NON_CONVERGING_CLAIMS", raising=False)
        monkeypatch.setattr(cli.RUNTIME_CONFIG.orchestrate, "max_non_converging_claims", None)
        assert cli._resolve_max_non_converging_claims() == DEFAULT_MAX_NON_CONVERGING_CLAIMS

    def test_default_constant_is_sane(self) -> None:
        from devbench.constants import DEFAULT_MAX_NON_CONVERGING_CLAIMS

        assert DEFAULT_MAX_NON_CONVERGING_CLAIMS >= 2

    def test_env_overrides_yaml_and_default(self, monkeypatch) -> None:  # type: ignore[no-untyped-def]
        from devbench import cli

        monkeypatch.setattr(cli.RUNTIME_CONFIG.orchestrate, "max_non_converging_claims", 7)
        monkeypatch.setenv("DEVBENCH_ORCHESTRATOR_MAX_NON_CONVERGING_CLAIMS", "5")
        assert cli._resolve_max_non_converging_claims() == 5

    def test_yaml_overrides_default(self, monkeypatch) -> None:  # type: ignore[no-untyped-def]
        from devbench import cli

        monkeypatch.delenv("DEVBENCH_ORCHESTRATOR_MAX_NON_CONVERGING_CLAIMS", raising=False)
        monkeypatch.setattr(cli.RUNTIME_CONFIG.orchestrate, "max_non_converging_claims", 9)
        assert cli._resolve_max_non_converging_claims() == 9


class TestTerminateProcessGroup:
    """The attributed single-pgid teardown primitive (Item B).

    Tears down EXACTLY one positively-attributed process group via
    ``os.killpg(pgid, SIGTERM)`` -- never a broad / machine-wide kill, and
    never the orchestrator's own group, so it can never reach an unrelated
    session.
    """

    def test_signals_only_the_attributed_group(self) -> None:
        from devbench import cli

        target_proc, target_pgid = _spawn_sleeper_group()
        bystander_proc, _ = _spawn_sleeper_group()
        try:
            signalled = cli._terminate_process_group(target_pgid)
            assert signalled is True
            assert _wait_until_dead(target_proc), "attributed executor group was not torn down"
            assert bystander_proc.poll() is None, "an UNRELATED process group was killed"
        finally:
            for proc in (target_proc, bystander_proc):
                if proc.poll() is None:
                    with contextlib_suppress():
                        os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
                    proc.wait(timeout=5)

    def test_refuses_init_and_kernel_groups(self) -> None:
        from devbench import cli

        assert cli._terminate_process_group(0) is False
        assert cli._terminate_process_group(1) is False

    def test_refuses_own_process_group(self) -> None:
        from devbench import cli

        own_pgid = os.getpgrp()
        assert cli._terminate_process_group(own_pgid) is False

    def test_missing_group_is_safe(self) -> None:
        from devbench import cli

        proc, pgid = _spawn_sleeper_group()
        os.killpg(pgid, signal.SIGKILL)
        proc.wait(timeout=5)
        cli._terminate_process_group(pgid)

    def test_oserror_on_signal_returns_false(self, monkeypatch) -> None:  # type: ignore[no-untyped-def]
        from devbench import cli

        def _boom(_pgid: int, _sig: int) -> None:
            raise PermissionError("not permitted")

        monkeypatch.setattr(cli.os, "killpg", _boom)
        assert cli._terminate_process_group(424242) is False


class TestRunClaimTeardownCleanupHook:
    """The sanctioned post-teardown cleanup hook runner (best-effort)."""

    def test_success_path(self, monkeypatch) -> None:  # type: ignore[no-untyped-def]
        from devbench import cli

        monkeypatch.setattr(cli, "run_command", lambda _cmd: (0, "ok", ""))
        cli._run_claim_teardown_cleanup_hook("sweep")

    def test_nonzero_exit_is_logged_not_raised(self, monkeypatch) -> None:  # type: ignore[no-untyped-def]
        from devbench import cli

        monkeypatch.setattr(cli, "run_command", lambda _cmd: (2, "", "sweep failed"))
        cli._run_claim_teardown_cleanup_hook("sweep")


class TestTeardownNonConvergingExecutor:
    """The orchestration of teardown -> conditional cleanup hook."""

    def test_noop_when_no_pgid(self, monkeypatch) -> None:  # type: ignore[no-untyped-def]
        from devbench import cli

        called: list[int] = []
        monkeypatch.setattr(cli, "_terminate_process_group", called.append)
        cli._teardown_non_converging_executor("EX-F1-S1-T1", None)
        assert called == []

    def test_skips_cleanup_when_teardown_refused(self, monkeypatch) -> None:  # type: ignore[no-untyped-def]
        from devbench import cli

        monkeypatch.setattr(cli, "_terminate_process_group", lambda _pgid: False)
        hook_calls: list[str] = []
        monkeypatch.setattr(cli, "_resolve_claim_teardown_cleanup_hook", lambda: "sweep")
        monkeypatch.setattr(cli, "_run_claim_teardown_cleanup_hook", hook_calls.append)
        cli._teardown_non_converging_executor("EX-F1-S1-T1", 4242)
        assert hook_calls == []


class TestRegisterExecutorPgidErrorPath:
    """_register_executor_pgid tolerates a write failure (best-effort)."""

    def test_write_failure_is_logged_not_raised(self, tmp_path, monkeypatch) -> None:  # type: ignore[no-untyped-def]
        from devbench import cli

        monkeypatch.setattr(cli, "WORKSPACE_ROOT", tmp_path)

        def _boom(_path: object, _text: str) -> None:
            raise OSError("disk full")

        monkeypatch.setattr(cli, "atomic_write_text", _boom)
        cli._register_executor_pgid(4242, session_name="alpha")


class TestBlockNonConvergingClaimTeardown:
    """``_block_non_converging_claim`` tears down the attributed executor group.

    On blocking a non-converging claim the executor's spawned subprocess group
    (a live ``terraform apply`` / ``go test`` tree) must be torn down so it is
    not orphaned to init and left leaking billable resources (Item B).
    """

    def test_block_tears_down_attributed_executor_group(self, tmp_path, monkeypatch) -> None:  # type: ignore[no-untyped-def]
        from devbench import cli
        from devbench.constants import CLAIM_NOT_CONVERGING_MARKER

        _write_block_backlog(tmp_path, monkeypatch)
        executor_proc, executor_pgid = _spawn_sleeper_group()
        bystander_proc, _ = _spawn_sleeper_group()
        try:
            cli._block_non_converging_claim(
                "EX-F1-S1-T1",
                "make tf-test::EX-F1-S1-T1",
                executor_pgid=executor_pgid,
            )
            updated = (tmp_path / "backlog" / "EX-F1-S1-T1.md").read_text(encoding="utf-8")
            assert "## Status: blocked" in updated
            assert CLAIM_NOT_CONVERGING_MARKER in updated
            assert _wait_until_dead(executor_proc), "executor subprocess group was orphaned, not torn down"
            assert bystander_proc.poll() is None, "an UNRELATED process group was killed"
        finally:
            for proc in (executor_proc, bystander_proc):
                if proc.poll() is None:
                    with contextlib_suppress():
                        os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
                    proc.wait(timeout=5)

    def test_block_without_pgid_does_not_signal(self, tmp_path, monkeypatch) -> None:  # type: ignore[no-untyped-def]
        from devbench import cli

        _write_block_backlog(tmp_path, monkeypatch)
        captured: list[int] = []

        def _record(pgid: int) -> bool:
            captured.append(pgid)
            return True

        monkeypatch.setattr(cli, "_terminate_process_group", _record)
        cli._block_non_converging_claim("EX-F1-S1-T1", "verify-ac::EX-F1-S1-T1")
        assert captured == []
        updated = (tmp_path / "backlog" / "EX-F1-S1-T1.md").read_text(encoding="utf-8")
        assert "## Status: blocked" in updated

    def test_block_runs_sanctioned_cleanup_hook_when_configured(self, tmp_path, monkeypatch) -> None:  # type: ignore[no-untyped-def]
        from devbench import cli

        _write_block_backlog(tmp_path, monkeypatch)
        executor_proc, executor_pgid = _spawn_sleeper_group()
        sentinel = tmp_path / "cleanup-ran.txt"
        monkeypatch.setattr(cli, "_resolve_claim_teardown_cleanup_hook", lambda: f"touch {sentinel}")
        try:
            cli._block_non_converging_claim(
                "EX-F1-S1-T1",
                "make tf-test::EX-F1-S1-T1",
                executor_pgid=executor_pgid,
            )
            assert sentinel.exists(), "configured sanctioned cleanup hook was not triggered on block"
        finally:
            if executor_proc.poll() is None:
                with contextlib_suppress():
                    os.killpg(os.getpgid(executor_proc.pid), signal.SIGKILL)
                executor_proc.wait(timeout=5)

    def test_block_skips_cleanup_hook_when_unconfigured(self, tmp_path, monkeypatch) -> None:  # type: ignore[no-untyped-def]
        from devbench import cli

        _write_block_backlog(tmp_path, monkeypatch)
        executor_proc, executor_pgid = _spawn_sleeper_group()
        ran: list[str] = []
        monkeypatch.setattr(cli, "_resolve_claim_teardown_cleanup_hook", lambda: None)
        monkeypatch.setattr(cli, "_run_claim_teardown_cleanup_hook", ran.append)
        try:
            cli._block_non_converging_claim(
                "EX-F1-S1-T1",
                "make tf-test::EX-F1-S1-T1",
                executor_pgid=executor_pgid,
            )
            assert ran == [], "cleanup hook was run despite no hook being configured"
        finally:
            if executor_proc.poll() is None:
                with contextlib_suppress():
                    os.killpg(os.getpgid(executor_proc.pid), signal.SIGKILL)
                executor_proc.wait(timeout=5)


def contextlib_suppress() -> Any:
    """Local alias kept tiny so the teardown finally-blocks stay readable."""
    import contextlib

    return contextlib.suppress(ProcessLookupError, OSError)


class TestExecutorPgidRegistry:
    """The session-scoped executor-pgid attribution channel.

    The in-session live-command runner records the pgid of the external command
    it launched; the orchestrator reads it at block time. Keyed by session so two
    sessions on one workspace never cross-attribute.
    """

    def test_register_then_read_round_trips(self, tmp_path, monkeypatch) -> None:  # type: ignore[no-untyped-def]
        from devbench import cli

        monkeypatch.setattr(cli, "WORKSPACE_ROOT", tmp_path)
        cli._register_executor_pgid(4242, session_name="alpha")
        assert cli._read_attributed_executor_pgid(session_name="alpha") == 4242

    def test_read_is_none_when_unregistered(self, tmp_path, monkeypatch) -> None:  # type: ignore[no-untyped-def]
        from devbench import cli

        monkeypatch.setattr(cli, "WORKSPACE_ROOT", tmp_path)
        assert cli._read_attributed_executor_pgid(session_name="alpha") is None

    def test_clear_removes_the_registration(self, tmp_path, monkeypatch) -> None:  # type: ignore[no-untyped-def]
        from devbench import cli

        monkeypatch.setattr(cli, "WORKSPACE_ROOT", tmp_path)
        cli._register_executor_pgid(99, session_name="alpha")
        cli._clear_executor_pgid(session_name="alpha")
        assert cli._read_attributed_executor_pgid(session_name="alpha") is None

    def test_sessions_are_isolated(self, tmp_path, monkeypatch) -> None:  # type: ignore[no-untyped-def]
        from devbench import cli

        monkeypatch.setattr(cli, "WORKSPACE_ROOT", tmp_path)
        cli._register_executor_pgid(11, session_name="alpha")
        cli._register_executor_pgid(22, session_name="beta")
        assert cli._read_attributed_executor_pgid(session_name="alpha") == 11
        assert cli._read_attributed_executor_pgid(session_name="beta") == 22

    def test_read_rejects_reserved_pgid(self, tmp_path, monkeypatch) -> None:  # type: ignore[no-untyped-def]
        from devbench import cli

        monkeypatch.setattr(cli, "WORKSPACE_ROOT", tmp_path)
        cli._register_executor_pgid(1, session_name="alpha")
        assert cli._read_attributed_executor_pgid(session_name="alpha") is None

    def test_read_is_none_on_malformed_file(self, tmp_path, monkeypatch) -> None:  # type: ignore[no-untyped-def]
        from devbench import cli

        monkeypatch.setattr(cli, "WORKSPACE_ROOT", tmp_path)
        path = cli._executor_pgid_file("alpha")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("not-a-pid\n", encoding="utf-8")
        assert cli._read_attributed_executor_pgid(session_name="alpha") is None


class TestResolveClaimTeardownCleanupHook:
    """The sanctioned post-teardown cleanup hook (env > YAML > constant)."""

    def test_none_when_unset(self, monkeypatch) -> None:  # type: ignore[no-untyped-def]
        from devbench import cli

        monkeypatch.delenv("DEVBENCH_ORCHESTRATOR_CLAIM_TEARDOWN_CLEANUP_HOOK", raising=False)
        monkeypatch.setattr(cli.RUNTIME_CONFIG.orchestrate, "claim_teardown_cleanup_hook", None)
        assert cli._resolve_claim_teardown_cleanup_hook() is None

    def test_empty_string_means_no_hook(self, monkeypatch) -> None:  # type: ignore[no-untyped-def]
        from devbench import cli

        monkeypatch.delenv("DEVBENCH_ORCHESTRATOR_CLAIM_TEARDOWN_CLEANUP_HOOK", raising=False)
        monkeypatch.setattr(cli.RUNTIME_CONFIG.orchestrate, "claim_teardown_cleanup_hook", "   ")
        assert cli._resolve_claim_teardown_cleanup_hook() is None

    def test_env_overrides_yaml(self, monkeypatch) -> None:  # type: ignore[no-untyped-def]
        from devbench import cli

        monkeypatch.setattr(cli.RUNTIME_CONFIG.orchestrate, "claim_teardown_cleanup_hook", "yaml-sweep")
        monkeypatch.setenv("DEVBENCH_ORCHESTRATOR_CLAIM_TEARDOWN_CLEANUP_HOOK", "env-sweep")
        assert cli._resolve_claim_teardown_cleanup_hook() == "env-sweep"

    def test_yaml_used_when_env_unset(self, monkeypatch) -> None:  # type: ignore[no-untyped-def]
        from devbench import cli

        monkeypatch.delenv("DEVBENCH_ORCHESTRATOR_CLAIM_TEARDOWN_CLEANUP_HOOK", raising=False)
        monkeypatch.setattr(cli.RUNTIME_CONFIG.orchestrate, "claim_teardown_cleanup_hook", "yaml-sweep")
        assert cli._resolve_claim_teardown_cleanup_hook() == "yaml-sweep"


class TestTimeoutResultDetection:
    """``_is_timeout_result`` recognises a kill-by-timeout Bash result.

    A timed-out run is a non-deterministic provisioning failure (e.g. a cold
    ``uv`` env syncing dependencies on the first invocation), NOT the "same
    deterministic test failure". It carries timeout text + an error flag and no
    captured assertion/collection output.
    """

    def test_timeout_text_with_error_flag_is_timeout(self) -> None:
        from devbench.cli import _is_timeout_result

        assert _is_timeout_result(_timeout_result("Command timed out after 120s")) is True

    def test_run_command_style_timeout_text_is_timeout(self) -> None:
        from devbench.cli import _is_timeout_result

        assert _is_timeout_result(_timeout_result("uv run pytest tests/unit/x.py: timed out after 3600s")) is True

    def test_real_assertion_failure_is_not_timeout(self) -> None:
        from devbench.cli import _is_timeout_result

        msg = _Msg(content=[_ToolResultBlock(content="E   assert 1 == 2\n1 failed in 0.12s", is_error=True)])
        assert _is_timeout_result(msg) is False

    def test_non_result_message_is_not_timeout(self) -> None:
        from devbench.cli import _is_timeout_result

        assert _is_timeout_result(_pytest_run("tests/unit/x.py")) is False
        assert _is_timeout_result(object()) is False


class TestTimeoutDoesNotAccrueConvergence:
    def test_timed_out_pytest_runs_do_not_trip_bound(self) -> None:
        tracker = ClaimConvergenceTracker(max_within_claim_attempts=4, max_claim_wall_clock_seconds=0)
        tracker.note_claim("E10-F3-S4-T1", now=0.0)
        trips: list[str] = []
        for i in range(8):
            run = tracker.observe(_pytest_run("tests/unit/test_live_verify.py"), now=float(2 * i))
            res = tracker.observe(_timeout_result(), now=float(2 * i + 1))
            trips += [r for r in (run, res) if r is not None]
        assert trips == [], f"repeated TIMED-OUT runs must not trip the convergence bound (#016); got {trips}"

    def test_deterministic_failure_still_trips_after_timeouts(self) -> None:
        tracker = ClaimConvergenceTracker(max_within_claim_attempts=3, max_claim_wall_clock_seconds=0)
        tracker.note_claim("E1-F1-S1-T1", now=0.0)
        for i in range(2):
            tracker.observe(_pytest_run("tests/unit/x.py"), now=float(i))
            tracker.observe(_timeout_result(), now=float(i) + 0.5)
        result = None
        for i in range(3):
            result = tracker.observe(_pytest_run("tests/unit/x.py"), now=10.0 + i)
        assert result is not None, "a genuine repeated deterministic failure must still trip the bound"
        assert "pytest" in result and "tests/unit/x.py" in result

    def test_timeout_result_only_exempts_the_preceding_run(self) -> None:
        tracker = ClaimConvergenceTracker(max_within_claim_attempts=2, max_claim_wall_clock_seconds=0)
        tracker.note_claim("E1-F1-S1-T1", now=0.0)
        assert tracker.observe(_pytest_run("tests/unit/x.py"), now=0.0) is None
        tracker.observe(_pytest_run("tests/unit/x.py"), now=1.0)
        assert tracker.observe(_timeout_result(), now=1.5) is None
        result = tracker.observe(_pytest_run("tests/unit/x.py"), now=2.0)
        assert result is not None, "two genuine deterministic failures (1 timeout in between) must trip"


class TestTimeoutMarkersAndResultShapes:
    def test_env_override_extends_timeout_markers(self, monkeypatch: Any) -> None:
        from devbench.cli import _is_timeout_result, _resolve_timeout_result_markers

        monkeypatch.setenv("DEVBENCH_ORCHESTRATOR_TIMEOUT_RESULT_MARKERS", "deadline exceeded, killed by watchdog")
        assert _resolve_timeout_result_markers() == ("deadline exceeded", "killed by watchdog")
        assert _is_timeout_result(_Msg(content=[_ToolResultBlock(content="job hit DEADLINE EXCEEDED")])) is True
        assert _is_timeout_result(_timeout_result("Command timed out after 5s")) is False

    def test_blank_env_falls_back_to_defaults(self, monkeypatch: Any) -> None:
        from devbench.cli import _resolve_timeout_result_markers
        from devbench.constants import TIMEOUT_RESULT_MARKERS

        monkeypatch.setenv("DEVBENCH_ORCHESTRATOR_TIMEOUT_RESULT_MARKERS", "   ")
        assert _resolve_timeout_result_markers() == TIMEOUT_RESULT_MARKERS

    def test_list_of_dicts_result_content_is_read(self) -> None:
        from devbench.cli import _is_timeout_result

        msg = _Msg(content=[_ToolResultBlock(content=[{"type": "text", "text": "Command timed out after 60s"}])])
        assert _is_timeout_result(msg) is True

    def test_timed_out_run_still_hits_wall_clock_backstop(self) -> None:
        tracker = ClaimConvergenceTracker(max_within_claim_attempts=999, max_claim_wall_clock_seconds=100.0)
        tracker.note_claim("E1-F1-S1-T1", now=0.0)
        tracker.observe(_pytest_run("tests/unit/x.py"), now=10.0)
        result = tracker.observe(_timeout_result(), now=150.0)
        assert result is not None and "wall-clock" in result.lower()


_REPO_ROOT = "/workspaces/telemetry/target/devbench"


def _abs_pytest(target: str) -> _Msg:
    """A pytest run against an absolute target path."""
    return _bash(f"uv run pytest {target}")


class TestIsWholeSuiteTarget:
    """Truth table for the pure ``_is_whole_suite_target`` classifier."""

    def _fn(self) -> Any:
        from devbench.cli import _is_whole_suite_target

        return _is_whole_suite_target

    def test_verify_ac_is_never_whole_suite(self) -> None:
        f = self._fn()
        assert f("devbench verify-ac", "E1-F1-S1-T1", (_REPO_ROOT,)) is False
        assert f("devbench verify-ac", "", (_REPO_ROOT,)) is False

    def test_empty_target_is_whole_suite(self) -> None:
        f = self._fn()
        assert f("pytest", "", (_REPO_ROOT,)) is True
        assert f("make test", "", (_REPO_ROOT,)) is True

    def test_bare_directory_is_whole_suite(self) -> None:
        f = self._fn()
        assert f("pytest", "tests", (_REPO_ROOT,)) is True
        assert f("pytest", "tests/unit", (_REPO_ROOT,)) is True
        assert f("pytest", "tests/", (_REPO_ROOT,)) is True

    def test_repo_root_abs_path_is_whole_suite(self) -> None:
        f = self._fn()
        assert f("pytest", _REPO_ROOT, (_REPO_ROOT,)) is True
        assert f("pytest", f"{_REPO_ROOT}/tests", (_REPO_ROOT,)) is True
        assert f("pytest", f"{_REPO_ROOT}/tests/unit", (_REPO_ROOT,)) is True

    def test_specific_test_file_is_scoped(self) -> None:
        f = self._fn()
        assert f("pytest", "tests/unit/test_foo.py", (_REPO_ROOT,)) is False
        assert f("pytest", "tests/unit/test_foo.py::test_x", (_REPO_ROOT,)) is False
        assert f("pytest", f"{_REPO_ROOT}/tests/unit/test_foo.py", (_REPO_ROOT,)) is False

    def test_parameterised_module_value_is_scoped(self) -> None:
        f = self._fn()
        assert f("tf-test", "providers/aws/alb-listener", (_REPO_ROOT,)) is False
        assert f("terratest", "providers/aws/vpc", (_REPO_ROOT,)) is False


class TestWholeSuiteFailureDoesNotConverge:
    def test_repeated_whole_suite_abs_path_does_not_trip(self) -> None:
        tracker = ClaimConvergenceTracker(
            max_within_claim_attempts=3,
            max_claim_wall_clock_seconds=0,
            repo_roots=(_REPO_ROOT,),
        )
        tracker.note_claim("E10-F3-S4-T1", now=0.0)
        trips = [tracker.observe(_abs_pytest(_REPO_ROOT), now=float(i)) for i in range(8)]
        assert all(t is None for t in trips), "a whole-suite run against the checkout root must never trip the bound"

    def test_repeated_bare_dir_does_not_trip(self) -> None:
        tracker = ClaimConvergenceTracker(
            max_within_claim_attempts=3,
            max_claim_wall_clock_seconds=0,
            repo_roots=(_REPO_ROOT,),
        )
        tracker.note_claim("E1-F1-S1-T1", now=0.0)
        trips = [tracker.observe(_pytest_run("tests/unit"), now=float(i)) for i in range(8)]
        assert all(t is None for t in trips), "a bare-directory whole-suite run must never trip the bound"

    def test_repeated_bare_pytest_empty_target_does_not_trip(self) -> None:
        tracker = ClaimConvergenceTracker(
            max_within_claim_attempts=3,
            max_claim_wall_clock_seconds=0,
            repo_roots=(_REPO_ROOT,),
        )
        tracker.note_claim("E1-F1-S1-T1", now=0.0)
        trips = [tracker.observe(_bash("uv run pytest"), now=float(i)) for i in range(8)]
        assert all(t is None for t in trips), "a bare pytest (empty target) must never trip the bound"

    def test_repeated_verify_ac_still_trips(self) -> None:
        tracker = ClaimConvergenceTracker(
            max_within_claim_attempts=3,
            max_claim_wall_clock_seconds=0,
            repo_roots=(_REPO_ROOT,),
        )
        tracker.note_claim("E1-F1-S1-T1", now=0.0)
        result = None
        for i in range(3):
            result = tracker.observe(_verify_fail("E1-F1-S1-T1"), now=float(i))
        assert result is not None, "a repeated verify-ac failure (authoritative gate) must still trip"
        assert "verify-ac" in result and "E1-F1-S1-T1" in result

    def test_repeated_scoped_test_file_still_trips(self) -> None:
        tracker = ClaimConvergenceTracker(
            max_within_claim_attempts=3,
            max_claim_wall_clock_seconds=0,
            repo_roots=(_REPO_ROOT,),
        )
        tracker.note_claim("E1-F1-S1-T1", now=0.0)
        result = None
        for i in range(3):
            result = tracker.observe(_pytest_run("tests/unit/test_foo.py::test_x"), now=float(i))
        assert result is not None, "a repeated scoped test-file failure must still trip the bound"
        assert "pytest" in result and "test_foo.py" in result

    def test_whole_suite_skip_logs_a_note(self, caplog: Any) -> None:
        import logging

        tracker = ClaimConvergenceTracker(
            max_within_claim_attempts=3,
            max_claim_wall_clock_seconds=0,
            repo_roots=(_REPO_ROOT,),
        )
        tracker.note_claim("E1-F1-S1-T1", now=0.0)
        with caplog.at_level(logging.INFO, logger="devbench.cli"):
            tracker.observe(_pytest_run("tests/unit"), now=0.0)
        assert any("whole-suite" in rec.getMessage().lower() for rec in caplog.records), (
            "a one-line audit note must explain why a whole-suite failure was not counted"
        )

    def test_no_repo_roots_still_classifies_bare_dir_and_empty(self) -> None:
        tracker = ClaimConvergenceTracker(max_within_claim_attempts=3, max_claim_wall_clock_seconds=0)
        tracker.note_claim("E1-F1-S1-T1", now=0.0)
        trips = [tracker.observe(_pytest_run("tests/unit"), now=float(i)) for i in range(8)]
        assert all(t is None for t in trips)
