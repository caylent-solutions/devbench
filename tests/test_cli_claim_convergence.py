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
