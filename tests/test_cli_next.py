"""Tests for cmd_next NO_ACTIONABLE diagnostic (issue #253d, AC-253-4, AC-253d-1)."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from devbench import cli
from devbench.backlog.work_unit import WorkUnit, WorkUnitStatus, WorkUnitType


def _task(
    unit_id: str,
    status: WorkUnitStatus = WorkUnitStatus.IN_QUEUE,
    dependencies: list[str] | None = None,
) -> WorkUnit:
    """Build a minimal TASK WorkUnit for test fixtures."""
    return WorkUnit(
        id=unit_id,
        title=f"Task {unit_id}",
        status=status,
        unit_type=WorkUnitType.TASK,
        file_path=Path(f"backlog/{unit_id}.md"),
        repo="caylent-solutions/devbench",
        dependencies=dependencies or [],
    )


class TestCmdNextNoActionableDiagnostic:
    """AC-253-4 / AC-253d-1: NO_ACTIONABLE sentinel + reason line."""

    @pytest.mark.unit
    def test_no_actionable_sentinel_is_first_token(self, capsys: pytest.CaptureFixture[str]) -> None:
        """AC-253d-1: NO_ACTIONABLE must appear as the first token on the first line."""
        held_dep = _task("E1-F1-S1-T1", status=WorkUnitStatus.HOLD)
        blocked_task = _task("E1-F1-S1-T2", dependencies=["E1-F1-S1-T1"])

        mock_parser = MagicMock()
        mock_parser.parse_index.return_value = [held_dep, blocked_task]
        mock_parser.get_parallel_candidates.return_value = []
        mock_parser.all_done.return_value = False

        with patch("devbench.cli.BacklogParser", return_value=mock_parser):
            rc = cli.cmd_next()

        assert rc == 0
        out = capsys.readouterr().out
        first_line = out.splitlines()[0]
        assert first_line.split()[0] == "NO_ACTIONABLE", f"First token must be NO_ACTIONABLE, got: {first_line!r}"

    @pytest.mark.unit
    def test_held_blocking_classification(self, capsys: pytest.CaptureFixture[str]) -> None:
        """AC-253-4: stall reason reports held-blocking with the held task ids."""
        held_dep = _task("E1-F1-S1-T1", status=WorkUnitStatus.HOLD)
        blocked_task = _task("E1-F1-S1-T2", dependencies=["E1-F1-S1-T1"])

        mock_parser = MagicMock()
        mock_parser.parse_index.return_value = [held_dep, blocked_task]
        mock_parser.get_parallel_candidates.return_value = []
        mock_parser.all_done.return_value = False

        with patch("devbench.cli.BacklogParser", return_value=mock_parser):
            rc = cli.cmd_next()

        assert rc == 0
        out = capsys.readouterr().out
        lines = out.splitlines()
        assert lines[0] == "NO_ACTIONABLE"
        reason_line = lines[1]
        assert "1 in-queue" in reason_line
        assert "0 actionable" in reason_line
        assert "held-blocking" in reason_line
        assert "E1-F1-S1-T1" in reason_line
        assert "\u2014" not in reason_line, "em-dash must not appear in reason line"

    @pytest.mark.unit
    def test_awaiting_dep_classification(self, capsys: pytest.CaptureFixture[str]) -> None:
        """AC-253-4: stall reason reports awaiting-dep with the blocking dep ids.

        The blocking dep is BLOCKED (not done, not hold), so the in-queue task
        waiting on it gets classified as awaiting-dep.
        """
        dep_task_blocked = _task("E1-F1-S1-T1", status=WorkUnitStatus.BLOCKED)
        waiting_task_2 = _task("E1-F1-S2-T1", dependencies=["E1-F1-S1-T1"])

        mock_parser = MagicMock()
        mock_parser.parse_index.return_value = [dep_task_blocked, waiting_task_2]
        mock_parser.get_parallel_candidates.return_value = []
        mock_parser.all_done.return_value = False

        with patch("devbench.cli.BacklogParser", return_value=mock_parser):
            rc = cli.cmd_next()

        assert rc == 0
        out = capsys.readouterr().out
        lines = out.splitlines()
        assert lines[0] == "NO_ACTIONABLE"
        reason_line = lines[1]
        assert "in-queue" in reason_line
        assert "0 actionable" in reason_line
        assert "awaiting-dep" in reason_line
        assert "E1-F1-S1-T1" in reason_line
        assert "\u2014" not in reason_line, "em-dash must not appear in reason line"

    @pytest.mark.unit
    def test_cyclic_classification(self, capsys: pytest.CaptureFixture[str]) -> None:
        """AC-253-4: stall reason reports cyclic when in-queue tasks form a dep cycle."""
        task_a = _task("E1-F1-S1-T1", dependencies=["E1-F1-S1-T2"])
        task_b = _task("E1-F1-S1-T2", dependencies=["E1-F1-S1-T1"])

        mock_parser = MagicMock()
        mock_parser.parse_index.return_value = [task_a, task_b]
        mock_parser.get_parallel_candidates.return_value = []
        mock_parser.all_done.return_value = False

        with patch("devbench.cli.BacklogParser", return_value=mock_parser):
            rc = cli.cmd_next()

        assert rc == 0
        out = capsys.readouterr().out
        lines = out.splitlines()
        assert lines[0] == "NO_ACTIONABLE"
        reason_line = lines[1]
        assert "in-queue" in reason_line
        assert "0 actionable" in reason_line
        assert "cyclic" in reason_line
        assert "\u2014" not in reason_line, "em-dash must not appear in reason line"

    @pytest.mark.unit
    def test_cyclic_classification_names_actual_members(self, capsys: pytest.CaptureFixture[str]) -> None:
        """TDI-009 AC-3: the cyclic diagnostic lists the ACTUAL cycle members.

        A three-task chain T1 -> T2 -> T3 -> T1 must name all three loop members,
        not an arbitrary detection node.
        """
        task_a = _task("E1-F1-S1-T1", dependencies=["E1-F1-S1-T2"])
        task_b = _task("E1-F1-S1-T2", dependencies=["E1-F1-S1-T3"])
        task_c = _task("E1-F1-S1-T3", dependencies=["E1-F1-S1-T1"])

        mock_parser = MagicMock()
        mock_parser.parse_index.return_value = [task_a, task_b, task_c]
        mock_parser.get_parallel_candidates.return_value = []
        mock_parser.all_done.return_value = False

        with patch("devbench.cli.BacklogParser", return_value=mock_parser):
            rc = cli.cmd_next()

        assert rc == 0
        reason_line = capsys.readouterr().out.splitlines()[1]
        assert "cyclic" in reason_line
        for member in ("E1-F1-S1-T1", "E1-F1-S1-T2", "E1-F1-S1-T3"):
            assert member in reason_line

    @pytest.mark.unit
    def test_reason_line_has_no_em_dash(self, capsys: pytest.CaptureFixture[str]) -> None:
        """AC-253-4: the reason line must not contain an em-dash character."""
        held_dep = _task("E1-F1-S1-T1", status=WorkUnitStatus.HOLD)
        blocked_task = _task("E1-F1-S1-T2", dependencies=["E1-F1-S1-T1"])

        mock_parser = MagicMock()
        mock_parser.parse_index.return_value = [held_dep, blocked_task]
        mock_parser.get_parallel_candidates.return_value = []
        mock_parser.all_done.return_value = False

        with patch("devbench.cli.BacklogParser", return_value=mock_parser):
            cli.cmd_next()

        out = capsys.readouterr().out
        assert "\u2014" not in out, "em-dash U+2014 must not appear anywhere in the output"

    @pytest.mark.unit
    def test_no_diagnostic_when_actionable_exists(self, capsys: pytest.CaptureFixture[str]) -> None:
        """Normal run: when candidates exist, no NO_ACTIONABLE diagnostic is printed."""
        actionable = _task("E1-F1-S1-T1")

        mock_parser = MagicMock()
        mock_parser.parse_index.return_value = [actionable]
        mock_parser.get_parallel_candidates.return_value = [actionable]

        with patch("devbench.cli.BacklogParser", return_value=mock_parser):
            rc = cli.cmd_next()

        assert rc == 0
        out = capsys.readouterr().out
        assert "NO_ACTIONABLE" not in out

    @pytest.mark.unit
    def test_no_diagnostic_when_all_done(self, capsys: pytest.CaptureFixture[str]) -> None:
        """ALL_DONE path: no NO_ACTIONABLE diagnostic printed when everything is done."""
        done_task = _task("E1-F1-S1-T1", status=WorkUnitStatus.DONE)

        mock_parser = MagicMock()
        mock_parser.parse_index.return_value = [done_task]
        mock_parser.get_parallel_candidates.return_value = []
        mock_parser.all_done.return_value = True

        with patch("devbench.cli.BacklogParser", return_value=mock_parser):
            rc = cli.cmd_next()

        assert rc == 0
        out = capsys.readouterr().out
        assert "ALL_DONE" in out
        assert "NO_ACTIONABLE" not in out

    @pytest.mark.unit
    def test_in_queue_count_in_reason_line(self, capsys: pytest.CaptureFixture[str]) -> None:
        """AC-253-4: the reason line correctly reports the in-queue count."""
        held_dep = _task("E1-F1-S1-T1", status=WorkUnitStatus.HOLD)
        blocked_a = _task("E1-F1-S1-T2", dependencies=["E1-F1-S1-T1"])
        blocked_b = _task("E1-F1-S1-T3", dependencies=["E1-F1-S1-T1"])

        mock_parser = MagicMock()
        mock_parser.parse_index.return_value = [held_dep, blocked_a, blocked_b]
        mock_parser.get_parallel_candidates.return_value = []
        mock_parser.all_done.return_value = False

        with patch("devbench.cli.BacklogParser", return_value=mock_parser):
            rc = cli.cmd_next()

        assert rc == 0
        out = capsys.readouterr().out
        reason_line = out.splitlines()[1]
        assert "2 in-queue" in reason_line

    @pytest.mark.unit
    @pytest.mark.parametrize(
        ("status", "expected_class"),
        [
            (WorkUnitStatus.HOLD, "held-blocking"),
            (WorkUnitStatus.BLOCKED, "awaiting-dep"),
            (WorkUnitStatus.IN_PROGRESS, "awaiting-dep"),
        ],
    )
    def test_classification_by_dep_status(
        self,
        status: WorkUnitStatus,
        expected_class: str,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """AC-253-4: stall classification depends on the blocking dependency's status."""
        blocking_dep = _task("E1-F1-S1-T1", status=status)
        waiting_task = _task("E1-F1-S1-T2", dependencies=["E1-F1-S1-T1"])

        units = [blocking_dep, waiting_task]

        mock_parser = MagicMock()
        mock_parser.parse_index.return_value = units
        mock_parser.get_parallel_candidates.return_value = []
        mock_parser.all_done.return_value = False

        with patch("devbench.cli.BacklogParser", return_value=mock_parser):
            rc = cli.cmd_next()

        assert rc == 0
        out = capsys.readouterr().out
        lines = out.splitlines()
        assert lines[0] == "NO_ACTIONABLE"
        assert expected_class in lines[1], (
            f"Expected {expected_class!r} in reason line for dep status {status}, got: {lines[1]!r}"
        )


class TestCmdNextSerializeInProgressCap:
    """Serialize claims: never offer a NEW in-queue unit while the in-progress cap is saturated.

    Root cause of tracked-issue 002: parallel claims share ONE target checkout, so a
    concurrent unit's uncommitted files leak into another unit's get-diff/staged-index.
    ``cmd_next`` enforces a configurable cap on concurrently-in-progress units (default 1)
    by dropping IN_QUEUE candidates whenever the count of IN_PROGRESS units is at or above
    the cap.
    """

    @pytest.mark.unit
    def test_in_progress_at_cap_drops_in_queue_candidate(self, capsys: pytest.CaptureFixture[str]) -> None:
        """Cap=1: with one IN_PROGRESS + one actionable IN_QUEUE, the IN_QUEUE unit is NOT offered."""
        in_progress = _task("E1-F1-S1-T1", status=WorkUnitStatus.IN_PROGRESS)
        in_queue = _task("E1-F1-S1-T2", status=WorkUnitStatus.IN_QUEUE)

        mock_parser = MagicMock()
        mock_parser.parse_index.return_value = [in_progress, in_queue]
        mock_parser.get_parallel_candidates.return_value = [in_progress, in_queue]
        mock_parser.all_done.return_value = False

        with patch("devbench.cli.BacklogParser", return_value=mock_parser):
            rc = cli.cmd_next()

        assert rc == 0
        out = capsys.readouterr().out
        assert "E1-F1-S1-T2" not in out, "a NEW in-queue unit must not be offered while the cap is saturated"
        assert "E1-F1-S1-T1" in out, "the in-progress unit should still be resumable"

    @pytest.mark.unit
    def test_in_progress_at_cap_with_no_in_progress_candidate_prints_serialized_reason(
        self, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """Cap=1: an IN_PROGRESS unit not itself actionable + an IN_QUEUE unit -> serialized reason line.

        When filtering drops the only candidate (the in-queue one) but an in-progress unit
        exists, a distinct, clearly-labeled serialized reason names the in-progress id so the
        operator/loop can tell "serialized, busy" from "genuinely stalled".
        """
        in_progress = _task("E1-F1-S1-T1", status=WorkUnitStatus.IN_PROGRESS)
        in_queue = _task("E1-F1-S1-T2", status=WorkUnitStatus.IN_QUEUE)

        mock_parser = MagicMock()
        mock_parser.parse_index.return_value = [in_progress, in_queue]
        mock_parser.get_parallel_candidates.return_value = [in_queue]
        mock_parser.all_done.return_value = False

        with patch("devbench.cli.BacklogParser", return_value=mock_parser):
            rc = cli.cmd_next()

        assert rc == 0
        out = capsys.readouterr().out
        assert "IN_PROGRESS_AT_CAPACITY" in out, "a distinct serialized reason must be printed"
        assert "E1-F1-S1-T1" in out, "the serialized reason must name the in-progress unit id"
        assert "—" not in out, "em-dash U+2014 must not appear in the output"

    @pytest.mark.unit
    def test_cap_two_allows_one_more_in_queue(
        self, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """Cap=2 (env override): with one IN_PROGRESS, an IN_QUEUE candidate is NOT filtered out.

        Only one candidate (the in-queue unit) is actionable here, so it is the one
        ``cmd_next`` emits -- demonstrating the cap-2 filter did not drop it (under
        the default cap-1 it would be dropped; see the next test).
        """
        monkeypatch.setenv("DEVBENCH_ORCHESTRATOR_MAX_PARALLEL_IN_PROGRESS", "2")
        in_progress = _task("E1-F1-S1-T1", status=WorkUnitStatus.IN_PROGRESS)
        in_queue = _task("E1-F1-S1-T2", status=WorkUnitStatus.IN_QUEUE)

        mock_parser = MagicMock()
        mock_parser.parse_index.return_value = [in_progress, in_queue]
        mock_parser.get_parallel_candidates.return_value = [in_queue]
        mock_parser.all_done.return_value = False

        with patch("devbench.cli.BacklogParser", return_value=mock_parser):
            rc = cli.cmd_next()

        assert rc == 0
        out = capsys.readouterr().out
        assert "E1-F1-S1-T2" in out, "with cap=2 and only 1 in-progress, the in-queue unit may be offered"

    @pytest.mark.unit
    def test_cap_one_drops_in_queue_when_in_progress_not_actionable(self, capsys: pytest.CaptureFixture[str]) -> None:
        """Cap=1 (default): an IN_QUEUE candidate IS dropped while a unit is in-progress.

        Mirror image of the cap-2 test above: the SAME single in-queue candidate is
        filtered out under the default cap, proving the cap is what gates it.
        """
        in_progress = _task("E1-F1-S1-T1", status=WorkUnitStatus.IN_PROGRESS)
        in_queue = _task("E1-F1-S1-T2", status=WorkUnitStatus.IN_QUEUE)

        mock_parser = MagicMock()
        mock_parser.parse_index.return_value = [in_progress, in_queue]
        mock_parser.get_parallel_candidates.return_value = [in_queue]
        mock_parser.all_done.return_value = False

        with patch("devbench.cli.BacklogParser", return_value=mock_parser):
            rc = cli.cmd_next()

        assert rc == 0
        out = capsys.readouterr().out
        assert "E1-F1-S1-T2" not in out, "under cap=1 the in-queue candidate must be dropped"
        assert "IN_PROGRESS_AT_CAPACITY" in out

    @pytest.mark.unit
    def test_no_in_progress_offers_in_queue_normally(self, capsys: pytest.CaptureFixture[str]) -> None:
        """Cap=1, zero IN_PROGRESS: the IN_QUEUE candidate is offered (regression guard)."""
        in_queue = _task("E1-F1-S1-T1", status=WorkUnitStatus.IN_QUEUE)

        mock_parser = MagicMock()
        mock_parser.parse_index.return_value = [in_queue]
        mock_parser.get_parallel_candidates.return_value = [in_queue]
        mock_parser.all_done.return_value = False

        with patch("devbench.cli.BacklogParser", return_value=mock_parser):
            rc = cli.cmd_next()

        assert rc == 0
        out = capsys.readouterr().out
        assert "E1-F1-S1-T1" in out
        assert "IN_PROGRESS_AT_CAPACITY" not in out

    @pytest.mark.unit
    def test_scope_filter_still_yields_scope_sentinel_under_serialize(self, capsys: pytest.CaptureFixture[str]) -> None:
        """The serialize filter must not break the scope-filter NO_ACTIONABLE_IN_SCOPE path."""
        in_progress = _task("E1-F1-S1-T1", status=WorkUnitStatus.IN_PROGRESS)

        mock_parser = MagicMock()
        mock_parser.parse_index.return_value = [in_progress]
        mock_parser.get_parallel_candidates.return_value = []
        mock_parser.all_done.return_value = False

        with patch("devbench.cli.BacklogParser", return_value=mock_parser):
            rc = cli.cmd_next("--include", "E9")

        assert rc == 0
        out = capsys.readouterr().out
        assert "NO_ACTIONABLE_IN_SCOPE" in out


class TestResolveMaxParallelInProgress:
    """``_resolve_max_parallel_in_progress`` env > YAML > default precedence."""

    @pytest.mark.unit
    def test_default_when_unset(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from devbench.constants import DEFAULT_MAX_PARALLEL_IN_PROGRESS

        monkeypatch.delenv("DEVBENCH_ORCHESTRATOR_MAX_PARALLEL_IN_PROGRESS", raising=False)
        mock_orchestrate = MagicMock()
        mock_orchestrate.max_parallel_in_progress = None
        with patch("devbench.cli.RUNTIME_CONFIG") as mock_cfg:
            mock_cfg.orchestrate = mock_orchestrate
            assert cli._resolve_max_parallel_in_progress() == DEFAULT_MAX_PARALLEL_IN_PROGRESS

    @pytest.mark.unit
    def test_yaml_overrides_default(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("DEVBENCH_ORCHESTRATOR_MAX_PARALLEL_IN_PROGRESS", raising=False)
        mock_orchestrate = MagicMock()
        mock_orchestrate.max_parallel_in_progress = 3
        with patch("devbench.cli.RUNTIME_CONFIG") as mock_cfg:
            mock_cfg.orchestrate = mock_orchestrate
            assert cli._resolve_max_parallel_in_progress() == 3

    @pytest.mark.unit
    def test_env_overrides_yaml(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("DEVBENCH_ORCHESTRATOR_MAX_PARALLEL_IN_PROGRESS", "5")
        mock_orchestrate = MagicMock()
        mock_orchestrate.max_parallel_in_progress = 3
        with patch("devbench.cli.RUNTIME_CONFIG") as mock_cfg:
            mock_cfg.orchestrate = mock_orchestrate
            assert cli._resolve_max_parallel_in_progress() == 5

    @pytest.mark.unit
    def test_default_constant_is_one(self) -> None:
        """DEFAULT_MAX_PARALLEL_IN_PROGRESS serializes claims (value 1)."""
        from devbench.constants import DEFAULT_MAX_PARALLEL_IN_PROGRESS

        assert DEFAULT_MAX_PARALLEL_IN_PROGRESS == 1
