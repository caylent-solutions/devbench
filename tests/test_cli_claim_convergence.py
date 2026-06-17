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

from dataclasses import dataclass
from typing import Any

from devbench.cli import (
    ClaimConvergenceTracker,
    _extract_failure_signature,
)

# --- Synthetic SDK message doubles (duck-typed like the real SDK messages) ---


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
    # A repeated verify-ac re-run is the canonical "re-checking the same AC"
    # signal the orchestrator observes for a non-converging unit.
    return _bash(f"uv run devbench verify-ac {unit_id}")


def _pytest_run(target: str) -> _Msg:
    # A repeated pytest re-run against the SAME target file is the signal that
    # surfaced TDI #016 (a cold ``uv`` env makes the first run time out).
    return _bash(f"uv run pytest {target}")


def _timeout_result(text: str = "Command timed out after 120s") -> _Msg:
    # The Bash tool surfaces a kill-by-timeout as a ToolResultBlock carrying an
    # error flag and timeout text -- NOT a captured assertion/collection failure.
    return _Msg(content=[_ToolResultBlock(content=text, is_error=True)])


# ---------------------------------------------------------------------------
# Signature extraction
# ---------------------------------------------------------------------------


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
        # A claim, a status read, an edit -- not a verification/test re-run.
        assert _extract_failure_signature(_claim_msg("E1-F1-S1-T1")) is None
        assert _extract_failure_signature(_bash("uv run devbench status")) is None

    def test_message_without_content_yields_none(self) -> None:
        assert _extract_failure_signature(object()) is None


# ---------------------------------------------------------------------------
# Tracker: repeated-identical-failure bound
# ---------------------------------------------------------------------------


class TestClaimConvergenceTrackerRepeatedFailure:
    def test_same_signature_n_times_trips_bound(self) -> None:
        # AC-1: the SAME failing signature repeated max_within_claim_attempts
        # times (with intervening tool-use, so the inactivity budget would NOT
        # trip) blocks with the recurring failure named.
        tracker = ClaimConvergenceTracker(max_within_claim_attempts=4, max_claim_wall_clock_seconds=0)
        tracker.note_claim("E1-F1-S1-T1", now=0.0)
        result = None
        for i in range(4):
            # intervening genuine tool-use (an edit) -- does NOT reset the
            # repeated-failure count because it is not a NEW failure signature.
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
        # AC-2: a claim that makes genuine, VARYING progress across many turns
        # before converging is NOT blocked (no false-positive on legit long
        # live runs). Each round emits a DIFFERENT signature (a different
        # module's live test), which resets the per-signature counts.
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
        # A NEW claim resets all per-signature counts.
        tracker.note_claim("E1-F1-S1-T2", now=10.0)
        # The same signature text under the OLD unit should not carry over.
        r = tracker.observe(_verify_fail("E1-F1-S1-T1"), now=11.0)
        assert r is None

    def test_observe_before_claim_is_safe(self) -> None:
        # Defensive: observing before any claim must not crash or trip.
        tracker = ClaimConvergenceTracker(max_within_claim_attempts=4, max_claim_wall_clock_seconds=0)
        assert tracker.observe(_verify_fail("E1-F1-S1-T1"), now=0.0) is None

    def test_current_unit_id_tracks_claim(self) -> None:
        tracker = ClaimConvergenceTracker(max_within_claim_attempts=4, max_claim_wall_clock_seconds=0)
        tracker.note_claim("E1-F1-S1-T1", now=0.0)
        assert tracker.current_unit_id == "E1-F1-S1-T1"

    def test_clear_current_claim_stops_retripping_blocked_unit(self) -> None:
        # Block-and-continue: after a unit trips the bound and is blocked, the
        # orchestrator clears the current claim so the SAME unit cannot re-trip
        # the bound on every subsequent identical-failure message before the
        # skill claims a new unit. ``observe`` must return None until note_claim.
        tracker = ClaimConvergenceTracker(max_within_claim_attempts=2, max_claim_wall_clock_seconds=0)
        tracker.note_claim("E1-F1-S1-T1", now=0.0)
        first = None
        for i in range(2):
            first = tracker.observe(_verify_fail("E1-F1-S1-T1"), now=float(i))
        assert first is not None, "the bound trips on the 2nd identical failure"
        tracker.clear_current_claim()
        assert tracker.current_unit_id is None
        # Further identical failures for the just-blocked unit must NOT re-trip.
        for i in range(5):
            assert tracker.observe(_verify_fail("E1-F1-S1-T1"), now=float(10 + i)) is None
        # A NEW claim resumes tracking normally.
        tracker.note_claim("E1-F1-S1-T2", now=100.0)
        again = None
        for i in range(2):
            again = tracker.observe(_verify_fail("E1-F1-S1-T2"), now=float(100 + i))
        assert again is not None


class TestClaimConvergenceTrackerWallClock:
    def test_wall_clock_backstop_trips(self) -> None:
        # AC-3 backstop: a claim that exceeds the generous wall-clock budget
        # trips even without a repeated signature. Default backstop is set well
        # above a 3.5h legit run; here we use a tiny budget to exercise the path.
        tracker = ClaimConvergenceTracker(max_within_claim_attempts=999, max_claim_wall_clock_seconds=100.0)
        tracker.note_claim("E1-F1-S1-T1", now=0.0)
        assert tracker.observe(_bash("edit"), now=50.0) is None
        result = tracker.observe(_bash("edit"), now=150.0)
        assert result is not None
        assert "wall-clock" in result.lower() or "E1-F1-S1-T1" in result

    def test_wall_clock_disabled_when_zero(self) -> None:
        # A 3.5h-class run with NO repeated signature and the backstop disabled
        # (0) is never blocked.
        tracker = ClaimConvergenceTracker(max_within_claim_attempts=4, max_claim_wall_clock_seconds=0)
        tracker.note_claim("E1-F1-S1-T1", now=0.0)
        # 3.5 hours of varying progress.
        result = None
        for i in range(50):
            result = tracker.observe(_bash(f"make tf-test MODULE_PATH=mod-{i}"), now=float(i) * 252.0)
        assert result is None

    def test_default_wall_clock_does_not_kill_legit_long_run(self) -> None:
        # AC-3: with the DEFAULT backstop, a 3.5h legit live run (varying
        # progress) is not killed.
        from devbench.constants import DEFAULT_MAX_CLAIM_WALL_CLOCK_SECONDS

        assert DEFAULT_MAX_CLAIM_WALL_CLOCK_SECONDS > 3.5 * 3600
        tracker = ClaimConvergenceTracker(
            max_within_claim_attempts=4,
            max_claim_wall_clock_seconds=DEFAULT_MAX_CLAIM_WALL_CLOCK_SECONDS,
        )
        tracker.note_claim("E1-F1-S1-T1", now=0.0)
        result = None
        # 3.5h of varying progress, one new module per ~4 minutes.
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
        # No claim ever noted; messages keep arriving (orphaned activity).
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
        tracker.observe(_bash("Read"), now=0.0)  # no-claim window starts at t=0
        tracker.note_claim("E1-F1-S1-T1", now=100.0)  # progress: a unit is claimed
        # While a unit is claimed the no-claim backstop must NEVER fire, even
        # long after the original no-claim window would have elapsed.
        assert tracker.observe(_bash("work"), now=10_000.0) is None
        # After a force-block clears the claim, the window RESTARTS fresh (it
        # does not retroactively count time spent while claimed).
        tracker.clear_current_claim()
        assert tracker.observe(_bash("Read"), now=10_001.0) is None
        assert tracker.observe(_bash("Read"), now=10_300.0) is None
        assert tracker.observe(_bash("Read"), now=10_301.0) is not None

    def test_default_no_claim_backstop_is_set_and_generous(self) -> None:
        from devbench.constants import DEFAULT_MAX_NO_CLAIM_ACTIVITY_SECONDS

        # Generous enough that a normal claim->work->claim cadence never trips,
        # but bounded (not disabled) so a true wedge is caught.
        assert DEFAULT_MAX_NO_CLAIM_ACTIVITY_SECONDS > 0
        assert DEFAULT_MAX_NO_CLAIM_ACTIVITY_SECONDS >= 300


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


# ---------------------------------------------------------------------------
# CLI plumbing: claim-id extraction, exit classification, force-block
# ---------------------------------------------------------------------------


class TestClaimedUnitId:
    def test_extracts_unit_id_from_claim_command(self) -> None:
        from devbench import cli

        assert cli._claimed_unit_id(_claim_msg("E1-F1-S1-T1")) == "E1-F1-S1-T1"

    def test_none_for_non_claim(self) -> None:
        from devbench import cli

        assert cli._claimed_unit_id(_verify_fail("E1-F1-S1-T1")) is None


class TestClassifyOrchestratorExit:
    def test_too_many_non_converging_routes_through_auto_restart(self, monkeypatch) -> None:  # type: ignore[no-untyped-def]
        # Block-and-continue: a SINGLE non-converging claim no longer halts the
        # session. Only the AGGREGATE valve -- K distinct non-converging units --
        # produces a stop reason, and it routes through the normal auto-restart
        # classification (so a restart picks up the remaining claimable units).
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
        # When the aggregate valve has NOT tripped (too_many_non_converging is 0)
        # the exit is classified by the normal terminal-sentinel rules, NOT by a
        # claim-not-converging stop reason. A session that blocked one unit and
        # then reached NO_ACTIONABLE exits cleanly.
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
        # Confirm the index/parser still reads the unit as blocked.
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
        # No crash when the unit is absent (parse_index raises -> swallowed).
        cli._block_non_converging_claim("EX-F1-S1-T9", "verify-ac::EX-F1-S1-T9")

    def test_unit_not_in_index_is_safe(self, monkeypatch) -> None:  # type: ignore[no-untyped-def]
        # _find_unit returns None -> warning branch, no crash.
        from types import SimpleNamespace

        from devbench import cli

        monkeypatch.setattr(cli, "BacklogParser", lambda **kwargs: SimpleNamespace(parse_index=list))
        monkeypatch.setattr(cli, "_find_unit", lambda _units, _uid: None)
        cli._block_non_converging_claim("EX-F1-S1-T9", "verify-ac::EX-F1-S1-T9")

    def test_unresolvable_file_is_safe(self, monkeypatch) -> None:  # type: ignore[no-untyped-def]
        # _resolve_unit_file returns None -> warning branch, no crash.
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

        # A sane operator-attention valve: more than one defect tolerated, but
        # bounded so a systemically-broken run still halts.
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


# ---------------------------------------------------------------------------
# TDI #016: a TIMED-OUT run (cold ``uv`` sync) must NOT accrue toward the bound
# ---------------------------------------------------------------------------


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
        # The shared ``run_command`` helper renders ``<cmd>: timed out after Ns``.
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
        # The exact #016 shape: the SAME pytest command is re-run, and EVERY run
        # is killed by the per-attempt timeout (cold ``uv`` sync). A timeout is
        # non-deterministic provisioning latency, not a deterministic failure, so
        # repeated timed-out runs must NOT trip CLAIM_NOT_CONVERGING.
        tracker = ClaimConvergenceTracker(max_within_claim_attempts=4, max_claim_wall_clock_seconds=0)
        tracker.note_claim("E10-F3-S4-T1", now=0.0)
        trips: list[str] = []
        for i in range(8):
            # The command is observed (assistant tool-use), then its result is a
            # timeout (ToolResultBlock) -- the run was killed, not a real failure.
            # The convergence bound must not trip on EITHER message.
            run = tracker.observe(_pytest_run("tests/unit/test_live_verify.py"), now=float(2 * i))
            res = tracker.observe(_timeout_result(), now=float(2 * i + 1))
            trips += [r for r in (run, res) if r is not None]
        assert trips == [], f"repeated TIMED-OUT runs must not trip the convergence bound (#016); got {trips}"

    def test_deterministic_failure_still_trips_after_timeouts(self) -> None:
        # Once the env is warm and the SAME command produces a REAL deterministic
        # failure (no timeout result), the bound must still trip normally -- the
        # timeout exemption must not defeat genuine non-convergence detection.
        tracker = ClaimConvergenceTracker(max_within_claim_attempts=3, max_claim_wall_clock_seconds=0)
        tracker.note_claim("E1-F1-S1-T1", now=0.0)
        # Two cold timed-out runs first: these must not count.
        for i in range(2):
            tracker.observe(_pytest_run("tests/unit/x.py"), now=float(i))
            tracker.observe(_timeout_result(), now=float(i) + 0.5)
        # Now three genuine deterministic failures (no timeout result follows).
        result = None
        for i in range(3):
            result = tracker.observe(_pytest_run("tests/unit/x.py"), now=10.0 + i)
        assert result is not None, "a genuine repeated deterministic failure must still trip the bound"
        assert "pytest" in result and "tests/unit/x.py" in result

    def test_timeout_result_only_exempts_the_preceding_run(self) -> None:
        # A timeout result rolls back ONLY the signature of the run it terminated;
        # it does not erase counts accrued by genuine prior deterministic failures
        # of the SAME signature.
        tracker = ClaimConvergenceTracker(max_within_claim_attempts=2, max_claim_wall_clock_seconds=0)
        tracker.note_claim("E1-F1-S1-T1", now=0.0)
        # One genuine deterministic failure (counts: 1).
        assert tracker.observe(_pytest_run("tests/unit/x.py"), now=0.0) is None
        # One timed-out run (the increment it caused is rolled back: counts back to 1).
        tracker.observe(_pytest_run("tests/unit/x.py"), now=1.0)
        assert tracker.observe(_timeout_result(), now=1.5) is None
        # A second genuine deterministic failure brings counts to 2 -> trips.
        result = tracker.observe(_pytest_run("tests/unit/x.py"), now=2.0)
        assert result is not None, "two genuine deterministic failures (1 timeout in between) must trip"


class TestTimeoutMarkersAndResultShapes:
    def test_env_override_extends_timeout_markers(self, monkeypatch: Any) -> None:
        from devbench.cli import _is_timeout_result, _resolve_timeout_result_markers

        monkeypatch.setenv("DEVBENCH_ORCHESTRATOR_TIMEOUT_RESULT_MARKERS", "deadline exceeded, killed by watchdog")
        assert _resolve_timeout_result_markers() == ("deadline exceeded", "killed by watchdog")
        # A result phrased with the custom marker is now recognised as a timeout;
        # the built-in defaults no longer apply once overridden.
        assert _is_timeout_result(_Msg(content=[_ToolResultBlock(content="job hit DEADLINE EXCEEDED")])) is True
        assert _is_timeout_result(_timeout_result("Command timed out after 5s")) is False

    def test_blank_env_falls_back_to_defaults(self, monkeypatch: Any) -> None:
        from devbench.cli import _resolve_timeout_result_markers
        from devbench.constants import TIMEOUT_RESULT_MARKERS

        monkeypatch.setenv("DEVBENCH_ORCHESTRATOR_TIMEOUT_RESULT_MARKERS", "   ")
        assert _resolve_timeout_result_markers() == TIMEOUT_RESULT_MARKERS

    def test_list_of_dicts_result_content_is_read(self) -> None:
        # The SDK's other ToolResultBlock shape: content is a list of
        # {"type": "text", "text": ...} parts rather than a bare string.
        from devbench.cli import _is_timeout_result

        msg = _Msg(content=[_ToolResultBlock(content=[{"type": "text", "text": "Command timed out after 60s"}])])
        assert _is_timeout_result(msg) is True

    def test_timed_out_run_still_hits_wall_clock_backstop(self) -> None:
        # The wall-clock backstop must still fire on a timed-out result message
        # (the timeout exemption rolls back the signature but does not bypass the
        # secondary backstop for an implausibly long stuck claim).
        tracker = ClaimConvergenceTracker(max_within_claim_attempts=999, max_claim_wall_clock_seconds=100.0)
        tracker.note_claim("E1-F1-S1-T1", now=0.0)
        tracker.observe(_pytest_run("tests/unit/x.py"), now=10.0)
        result = tracker.observe(_timeout_result(), now=150.0)
        assert result is not None and "wall-clock" in result.lower()
