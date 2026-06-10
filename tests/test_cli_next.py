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
        # Two in-queue tasks (held_dep is HOLD, not in-queue; blocked_a and blocked_b are in-queue)
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
