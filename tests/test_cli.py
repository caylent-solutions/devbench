"""Tests for judges.cli module."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from devbench import cli
from devbench.backlog.work_unit import WorkUnit, WorkUnitStatus, WorkUnitType
from devbench.constants import BACKLOG_SUBDIR
from devbench.judges.base import JudgeResult, Verdict


@pytest.fixture
def mock_units() -> list[WorkUnit]:
    """Create a list of mock work units for testing."""
    return [
        WorkUnit(
            id="E0-F1-S1-T1",
            title="First Task",
            status=WorkUnitStatus.DONE,
            unit_type=WorkUnitType.TASK,
            file_path=Path("backlog/E0-F1-S1-T1.md"),
            repo="caylent-solutions/git-repo",
            dependencies=[],
        ),
        WorkUnit(
            id="E0-F1-S1-T2",
            title="Second Task",
            status=WorkUnitStatus.IN_QUEUE,
            unit_type=WorkUnitType.TASK,
            file_path=Path("backlog/E0-F1-S1-T2.md"),
            repo="caylent-solutions/git-repo",
            dependencies=["E0-F1-S1-T1"],
        ),
        WorkUnit(
            id="E0-F1-S1-T3",
            title="Blocked Task",
            status=WorkUnitStatus.BLOCKED,
            unit_type=WorkUnitType.TASK,
            file_path=Path("backlog/E0-F1-S1-T3.md"),
            repo="caylent-solutions/git-repo",
            dependencies=[],
        ),
    ]


class TestFindUnit:
    """Test _find_unit helper."""

    def test_finds_by_id(self, mock_units: list[WorkUnit]) -> None:
        result = cli._find_unit(mock_units, "E0-F1-S1-T2")
        assert result is not None
        assert result.id == "E0-F1-S1-T2"

    def test_finds_case_insensitive(self, mock_units: list[WorkUnit]) -> None:
        result = cli._find_unit(mock_units, "e0-f1-s1-t1")
        assert result is not None
        assert result.id == "E0-F1-S1-T1"

    def test_returns_none_when_not_found(self, mock_units: list[WorkUnit]) -> None:
        result = cli._find_unit(mock_units, "NONEXISTENT")
        assert result is None


class TestCmdStatus:
    """Test cmd_status command."""

    def test_returns_zero(self, mock_units: list[WorkUnit]) -> None:
        mock_parser = MagicMock()
        mock_parser.parse_index.return_value = mock_units
        mock_parser.get_parallel_candidates.return_value = [mock_units[1]]
        mock_parser.all_done.return_value = False
        mock_parser.get_blocked_units.return_value = [mock_units[2]]

        with patch("devbench.cli.BacklogParser", return_value=mock_parser):
            result = cli.cmd_status()

        assert result == 0

    def test_shows_all_done_message(self, capsys: pytest.CaptureFixture[str]) -> None:
        done_unit = WorkUnit(
            id="T1",
            title="Done",
            status=WorkUnitStatus.DONE,
            unit_type=WorkUnitType.TASK,
            file_path=Path("t.md"),
            repo="caylent-solutions/git-repo",
            dependencies=[],
        )
        mock_parser = MagicMock()
        mock_parser.parse_index.return_value = [done_unit]
        mock_parser.get_parallel_candidates.return_value = []
        mock_parser.all_done.return_value = True

        with patch("devbench.cli.BacklogParser", return_value=mock_parser):
            result = cli.cmd_status()

        assert result == 0
        assert "All work units are DONE" in capsys.readouterr().out

    def test_shows_blocked_count(self, mock_units: list[WorkUnit], capsys: pytest.CaptureFixture[str]) -> None:
        mock_parser = MagicMock()
        mock_parser.parse_index.return_value = mock_units
        mock_parser.get_parallel_candidates.return_value = []
        mock_parser.all_done.return_value = False
        mock_parser.get_blocked_units.return_value = [mock_units[2]]

        with patch("devbench.cli.BacklogParser", return_value=mock_parser):
            result = cli.cmd_status()

        assert result == 0
        assert "1 blocked" in capsys.readouterr().out


@pytest.fixture
def detail_units() -> list[WorkUnit]:
    """Work units for --detail flag tests.

    Contains:
    - A done TASK (E1-F1-S1-T1)
    - An in-queue TASK with satisfied deps (E1-F1-S1-T2, dep on T1 which is done)
    - An in-queue TASK with unsatisfied deps / blocked (E2-F1-S1-T1, dep on E2 not done)
    - A STORY rollup that should be excluded from --detail output (E1-F1-S1)
    - A FEATURE rollup that should be excluded from --detail output (E1-F1)
    - A blocked TASK with an unresolvable dep reference (E3-F1-S1-T1, dep on E99)
    """
    return [
        WorkUnit(
            id="E1-F1-S1-T1",
            title="Done task",
            status=WorkUnitStatus.DONE,
            unit_type=WorkUnitType.TASK,
            file_path=Path("backlog/E1-F1-S1-T1.md"),
            repo="caylent-solutions/devbench",
            dependencies=[],
        ),
        WorkUnit(
            id="E1-F1-S1-T2",
            title="In-queue task with met dep",
            status=WorkUnitStatus.IN_QUEUE,
            unit_type=WorkUnitType.TASK,
            file_path=Path("backlog/E1-F1-S1-T2.md"),
            repo="caylent-solutions/devbench",
            dependencies=["E1-F1-S1-T1"],
        ),
        WorkUnit(
            id="E2-F1-S1-T1",
            title="Blocked task with unmet dep",
            status=WorkUnitStatus.BLOCKED,
            unit_type=WorkUnitType.TASK,
            file_path=Path("backlog/E2-F1-S1-T1.md"),
            repo="caylent-solutions/devbench",
            dependencies=["E2"],
        ),
        WorkUnit(
            id="E1-F1-S1",
            title="Story rollup",
            status=WorkUnitStatus.IN_QUEUE,
            unit_type=WorkUnitType.STORY,
            file_path=Path("backlog/E1-F1-S1.md"),
            repo="caylent-solutions/devbench",
            dependencies=[],
        ),
        WorkUnit(
            id="E1-F1",
            title="Feature rollup",
            status=WorkUnitStatus.IN_QUEUE,
            unit_type=WorkUnitType.FEATURE,
            file_path=Path("backlog/E1-F1.md"),
            repo="caylent-solutions/devbench",
            dependencies=[],
        ),
        WorkUnit(
            id="E3-F1-S1-T1",
            title="Blocked task unknown dep",
            status=WorkUnitStatus.BLOCKED,
            unit_type=WorkUnitType.TASK,
            file_path=Path("backlog/E3-F1-S1-T1.md"),
            repo="caylent-solutions/devbench",
            dependencies=["E99"],
        ),
    ]


class TestCmdStatusDetail:
    """Test cmd_status --detail flag (AC-1 through AC-4)."""

    def test_status_detail_shows_in_queue_tasks(
        self, detail_units: list[WorkUnit], capsys: pytest.CaptureFixture[str]
    ) -> None:
        """
        Given: backlog with in-queue TASK units
        When: cmd_status(detail=True) is called
        Then: in-queue Tasks are listed under 'In Queue' section
        Spec: AC-1
        """
        mock_parser = MagicMock()
        mock_parser.parse_index.return_value = detail_units
        # T2 is the only in-queue task with all deps met
        mock_parser.get_parallel_candidates.return_value = [detail_units[1]]
        mock_parser.all_done.return_value = False
        mock_parser.get_blocked_units.return_value = [detail_units[2], detail_units[5]]

        with patch("devbench.cli.BacklogParser", return_value=mock_parser):
            result = cli.cmd_status(detail=True)

        assert result == 0
        out = capsys.readouterr().out
        assert "In Queue" in out
        assert "E1-F1-S1-T2" in out
        assert "In-queue task with met dep" in out

    def test_status_detail_in_queue_tasks_in_priority_order(
        self, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """
        Given: multiple in-queue Tasks
        When: cmd_status(detail=True) is called
        Then: in-queue Tasks appear in priority order (in-progress first, then by numeric ID)
        Spec: AC-1
        """
        units = [
            WorkUnit(
                id="E9-F1-S1-T1",
                title="Numeric nine task",
                status=WorkUnitStatus.IN_QUEUE,
                unit_type=WorkUnitType.TASK,
                file_path=Path("backlog/E9-F1-S1-T1.md"),
                repo="caylent-solutions/devbench",
                dependencies=[],
            ),
            WorkUnit(
                id="E15-F1-S1-T1",
                title="Numeric fifteen task",
                status=WorkUnitStatus.IN_QUEUE,
                unit_type=WorkUnitType.TASK,
                file_path=Path("backlog/E15-F1-S1-T1.md"),
                repo="caylent-solutions/devbench",
                dependencies=[],
            ),
            WorkUnit(
                id="E2-F1-S1-T1",
                title="In-progress task",
                status=WorkUnitStatus.IN_PROGRESS,
                unit_type=WorkUnitType.TASK,
                file_path=Path("backlog/E2-F1-S1-T1.md"),
                repo="caylent-solutions/devbench",
                dependencies=[],
            ),
        ]
        # get_parallel_candidates returns in-progress first, then in-queue by numeric ID
        mock_parser = MagicMock()
        mock_parser.parse_index.return_value = units
        mock_parser.get_parallel_candidates.return_value = [units[2], units[0], units[1]]
        mock_parser.all_done.return_value = False
        mock_parser.get_blocked_units.return_value = []

        with patch("devbench.cli.BacklogParser", return_value=mock_parser):
            result = cli.cmd_status(detail=True)

        assert result == 0
        out = capsys.readouterr().out
        pos_in_progress = out.index("E2-F1-S1-T1")
        pos_nine = out.index("E9-F1-S1-T1")
        pos_fifteen = out.index("E15-F1-S1-T1")
        assert pos_in_progress < pos_nine < pos_fifteen

    def test_status_detail_shows_blocked_tasks_with_deps(
        self, detail_units: list[WorkUnit], capsys: pytest.CaptureFixture[str]
    ) -> None:
        """
        Given: backlog with blocked TASK units that have unresolved deps
        When: cmd_status(detail=True) is called
        Then: blocked Tasks appear under 'Blocked' section with their unresolved dep IDs
        Spec: AC-2
        """
        mock_parser = MagicMock()
        mock_parser.parse_index.return_value = detail_units
        mock_parser.get_parallel_candidates.return_value = [detail_units[1]]
        mock_parser.all_done.return_value = False
        mock_parser.get_blocked_units.return_value = [detail_units[2], detail_units[5]]

        with patch("devbench.cli.BacklogParser", return_value=mock_parser):
            result = cli.cmd_status(detail=True)

        assert result == 0
        out = capsys.readouterr().out
        assert "Blocked" in out
        assert "E2-F1-S1-T1" in out
        # dep ID must appear next to the blocked unit
        assert "E2" in out
        assert "E3-F1-S1-T1" in out
        assert "E99" in out

    def test_status_detail_excludes_non_task_units(
        self, detail_units: list[WorkUnit], capsys: pytest.CaptureFixture[str]
    ) -> None:
        """
        Given: backlog with Story and Feature rollup units alongside Tasks
        When: cmd_status(detail=True) is called
        Then: Story and Feature IDs do NOT appear in the --detail sections
        Spec: AC-3
        """
        mock_parser = MagicMock()
        mock_parser.parse_index.return_value = detail_units
        mock_parser.get_parallel_candidates.return_value = [detail_units[1]]
        mock_parser.all_done.return_value = False
        mock_parser.get_blocked_units.return_value = [detail_units[2], detail_units[5]]

        with patch("devbench.cli.BacklogParser", return_value=mock_parser):
            result = cli.cmd_status(detail=True)

        assert result == 0
        out = capsys.readouterr().out
        # Extract just the --detail sections (after the summary separator)
        # Story and Feature rollup IDs must not appear in the detail sections
        assert "E1-F1-S1\n" not in out  # story
        assert "E1-F1\n" not in out     # feature

    def test_status_without_detail_unchanged(
        self, detail_units: list[WorkUnit], capsys: pytest.CaptureFixture[str]
    ) -> None:
        """
        Given: any backlog state
        When: cmd_status() is called without detail=True
        Then: output does NOT contain 'In Queue (' or 'Blocked (' detail sections
        Spec: AC-4
        """
        mock_parser = MagicMock()
        mock_parser.parse_index.return_value = detail_units
        mock_parser.get_parallel_candidates.return_value = [detail_units[1]]
        mock_parser.all_done.return_value = False
        mock_parser.get_blocked_units.return_value = [detail_units[2], detail_units[5]]

        with patch("devbench.cli.BacklogParser", return_value=mock_parser):
            result = cli.cmd_status()

        assert result == 0
        out = capsys.readouterr().out
        # Detail sections must not appear in plain status
        assert "In Queue (" not in out
        assert "Blocked (" not in out

    def test_status_detail_via_main_dispatch(
        self, detail_units: list[WorkUnit], capsys: pytest.CaptureFixture[str]
    ) -> None:
        """
        Given: sys.argv contains ['devbench', 'status', '--detail']
        When: main() is called
        Then: cmd_status is invoked with detail=True and detail sections are printed
        Spec: AC-1, AC-4
        """
        mock_parser = MagicMock()
        mock_parser.parse_index.return_value = detail_units
        mock_parser.get_parallel_candidates.return_value = [detail_units[1]]
        mock_parser.all_done.return_value = False
        mock_parser.get_blocked_units.return_value = [detail_units[2], detail_units[5]]

        with (
            patch("sys.argv", ["devbench", "status", "--detail"]),
            patch("devbench.cli.BacklogParser", return_value=mock_parser),
        ):
            result = cli.main()

        assert result == 0
        out = capsys.readouterr().out
        assert "In Queue (" in out or "Blocked (" in out


class TestCmdNext:
    """Test cmd_next command."""

    def test_returns_json_when_actionable(self, mock_units: list[WorkUnit], capsys: pytest.CaptureFixture) -> None:
        mock_parser = MagicMock()
        mock_parser.parse_index.return_value = mock_units
        mock_parser.get_parallel_candidates.return_value = [mock_units[1]]

        with patch("devbench.cli.BacklogParser", return_value=mock_parser):
            result = cli.cmd_next()

        assert result == 0
        output = capsys.readouterr().out.strip()
        data = json.loads(output)
        assert data["id"] == "E0-F1-S1-T2"

    def test_prints_all_done_when_complete(self, capsys: pytest.CaptureFixture) -> None:
        done_unit = WorkUnit(
            id="T1",
            title="Done",
            status=WorkUnitStatus.DONE,
            unit_type=WorkUnitType.TASK,
            file_path=Path("t.md"),
            repo="caylent-solutions/git-repo",
            dependencies=[],
        )
        mock_parser = MagicMock()
        mock_parser.parse_index.return_value = [done_unit]
        mock_parser.get_parallel_candidates.return_value = []
        mock_parser.all_done.return_value = True

        with patch("devbench.cli.BacklogParser", return_value=mock_parser):
            result = cli.cmd_next()

        assert result == 0
        assert "ALL_DONE" in capsys.readouterr().out

    def test_prints_no_actionable(self, mock_units: list[WorkUnit], capsys: pytest.CaptureFixture) -> None:
        mock_parser = MagicMock()
        mock_parser.parse_index.return_value = mock_units
        mock_parser.get_parallel_candidates.return_value = []
        mock_parser.all_done.return_value = False

        with patch("devbench.cli.BacklogParser", return_value=mock_parser):
            result = cli.cmd_next()

        assert result == 0
        assert "NO_ACTIONABLE" in capsys.readouterr().out


class TestCmdLog:
    """Test cmd_log command."""

    def test_returns_zero(self, capsys: pytest.CaptureFixture) -> None:
        result = cli.cmd_log("test message")
        assert result == 0
        assert "Logged" in capsys.readouterr().out


class TestCmdReview:
    """Test cmd_review command."""

    def test_returns_1_when_unit_not_found(self, mock_units: list[WorkUnit]) -> None:
        mock_parser = MagicMock()
        mock_parser.parse_index.return_value = mock_units

        with patch("devbench.cli.BacklogParser", return_value=mock_parser):
            result = cli.cmd_review("NONEXISTENT")

        assert result == 1

    def test_returns_0_when_all_pass(
        self, mock_units: list[WorkUnit], tmp_path: Path, backlog_dir: Path, capsys: pytest.CaptureFixture
    ) -> None:
        wu_file = backlog_dir / "E0-F1-S1-T2.md"
        wu_file.write_text("# E0-F1-S1-T2: Task\n## Status: in-review\n")

        mock_parser = MagicMock()
        mock_parser.parse_index.return_value = mock_units

        pass_result = JudgeResult(
            judge_name="test",
            verdict=Verdict.PASS,
            reasoning="ok",
            feedback="",
            evidence=[],
        )
        mock_judge = MagicMock()
        mock_judge.name = "mock_judge"
        mock_judge.evaluate.return_value = pass_result

        mock_mgr = MagicMock()

        with patch("devbench.cli.BacklogParser", return_value=mock_parser):
            with patch("devbench.cli.BACKLOG_ROOT", backlog_dir):
                with patch("devbench.cli.BACKLOG_INDEX", tmp_path / "BACKLOG.md"):
                    with patch("devbench.cli.WORKSPACE_ROOT", tmp_path):
                        with patch("devbench.cli.REPO_LOCAL_PATHS", {"caylent-solutions/git-repo": tmp_path}):
                            with patch("devbench.cli.BacklogManager", return_value=mock_mgr):
                                with patch("devbench.cli.CodeReviewJudge", return_value=mock_judge):
                                    with patch("devbench.cli.TestReviewJudge", return_value=mock_judge):
                                        with patch("devbench.cli.DocReviewJudge", return_value=mock_judge):
                                            with patch("devbench.cli.ChangesManifestJudge", return_value=mock_judge):
                                                result = cli.cmd_review("E0-F1-S1-T2")

        assert result == 0
        mock_mgr.force_status.assert_called_once()
        output = json.loads(capsys.readouterr().out)
        assert output["all_passed"] is True


class TestCmdExecute:
    """Test cmd_execute command."""

    def test_returns_1_when_unit_not_found(self, mock_units: list[WorkUnit]) -> None:
        mock_parser = MagicMock()
        mock_parser.parse_index.return_value = mock_units

        with patch("devbench.cli.BacklogParser", return_value=mock_parser):
            result = cli.cmd_execute("NONEXISTENT")

        assert result == 1

    def test_returns_0_on_in_review(
        self, mock_units: list[WorkUnit], tmp_path: Path, backlog_dir: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        from devbench.execution.executor import ExecutionResult, ExecutionStatus

        wu_file = backlog_dir / "E0-F1-S1-T2.md"
        wu_file.write_text("# Task\n")

        mock_parser = MagicMock()
        mock_parser.parse_index.return_value = mock_units
        exec_result = ExecutionResult(status=ExecutionStatus.IN_REVIEW, output="done", blocker="")

        with patch("devbench.cli.BacklogParser", return_value=mock_parser):
            with patch("devbench.cli.BACKLOG_ROOT", backlog_dir):
                with patch("devbench.execution.executor.execute", return_value=exec_result):
                    result = cli.cmd_execute("E0-F1-S1-T2")

        assert result == 0
        output = json.loads(capsys.readouterr().out)
        assert output["status"] == "in-review"

    def test_returns_1_on_failure(self, mock_units: list[WorkUnit], tmp_path: Path, backlog_dir: Path) -> None:
        from devbench.execution.executor import ExecutionResult, ExecutionStatus

        wu_file = backlog_dir / "E0-F1-S1-T2.md"
        wu_file.write_text("# Task\n")

        mock_parser = MagicMock()
        mock_parser.parse_index.return_value = mock_units
        exec_result = ExecutionResult(status=ExecutionStatus.FAILED, output="error", blocker="")

        with patch("devbench.cli.BacklogParser", return_value=mock_parser):
            with patch("devbench.cli.BACKLOG_ROOT", backlog_dir):
                with patch("devbench.execution.executor.execute", return_value=exec_result):
                    result = cli.cmd_execute("E0-F1-S1-T2")

        assert result == 1


class TestCmdSecurityReview:
    """Test cmd_security_review command."""

    def test_returns_1_when_unit_not_found(self, mock_units: list[WorkUnit]) -> None:
        mock_parser = MagicMock()
        mock_parser.parse_index.return_value = mock_units

        with patch("devbench.cli.BacklogParser", return_value=mock_parser):
            result = cli.cmd_security_review("NONEXISTENT")

        assert result == 1

    def test_returns_1_when_no_repo_path(self, mock_units: list[WorkUnit]) -> None:
        mock_parser = MagicMock()
        mock_parser.parse_index.return_value = mock_units

        with patch("devbench.cli.BacklogParser", return_value=mock_parser):
            with patch("devbench.cli.REPO_LOCAL_PATHS", {}):
                result = cli.cmd_security_review("E0-F1-S1-T2")

        assert result == 1

    def test_returns_0_when_pass(
        self, mock_units: list[WorkUnit], tmp_path: Path, backlog_dir: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        wu_file = backlog_dir / "E0-F1-S1-T2.md"
        wu_file.write_text("# Task\n")

        mock_parser = MagicMock()
        mock_parser.parse_index.return_value = mock_units

        pass_result = JudgeResult(
            judge_name="security_review",
            verdict=Verdict.PASS,
            reasoning="clean",
            feedback="",
            evidence=[],
        )
        mock_judge = MagicMock()
        mock_judge.evaluate.return_value = pass_result

        with patch("devbench.cli.BacklogParser", return_value=mock_parser):
            with patch("devbench.cli.BACKLOG_ROOT", backlog_dir):
                with patch("devbench.cli.REPO_LOCAL_PATHS", {"caylent-solutions/git-repo": tmp_path}):
                    with patch("devbench.cli.SecurityReviewJudge", return_value=mock_judge):
                        result = cli.cmd_security_review("E0-F1-S1-T2")

        assert result == 0
        output = json.loads(capsys.readouterr().out)
        assert output["verdict"] == "pass"


class TestCmdSetStatus:
    """Test cmd_set_status command."""

    def test_returns_1_for_invalid_status(self, capsys: pytest.CaptureFixture[str]) -> None:
        result = cli.cmd_set_status("E0-F1-S1-T1", "invalid")
        assert result == 1
        assert "Invalid status" in capsys.readouterr().err

    def test_returns_1_when_unit_not_found(self, mock_units: list[WorkUnit]) -> None:
        mock_parser = MagicMock()
        mock_parser.parse_index.return_value = mock_units

        with patch("devbench.cli.BacklogParser", return_value=mock_parser):
            result = cli.cmd_set_status("NONEXISTENT", "in-progress")

        assert result == 1

    def test_returns_0_on_success(
        self, mock_units: list[WorkUnit], tmp_path: Path, backlog_dir: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        wu_file = backlog_dir / "E0-F1-S1-T2.md"
        wu_file.write_text("# Task\n## Status: in-queue\n")

        mock_parser = MagicMock()
        mock_parser.parse_index.return_value = mock_units

        mock_mgr = MagicMock()

        with patch("devbench.cli.BacklogParser", return_value=mock_parser):
            with patch("devbench.cli.BACKLOG_ROOT", backlog_dir):
                with patch("devbench.cli.BacklogManager", return_value=mock_mgr):
                    result = cli.cmd_set_status("E0-F1-S1-T2", "in-progress")

        assert result == 0
        assert "in-progress" in capsys.readouterr().out
        mock_mgr.force_status.assert_called_once()


class TestCmdMarkDone:
    """Test cmd_mark_done enforces the done-gate via mark_done()."""

    def test_returns_1_when_unit_not_found(self, mock_units: list[WorkUnit]) -> None:
        mock_parser = MagicMock()
        mock_parser.parse_index.return_value = mock_units

        with patch("devbench.cli.BacklogParser", return_value=mock_parser):
            result = cli.cmd_mark_done("NONEXISTENT")

        assert result == 1

    def test_returns_0_on_success(
        self, mock_units: list[WorkUnit], tmp_path: Path, backlog_dir: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        wu_file = backlog_dir / "E0-F1-S1-T2.md"
        wu_file.write_text("# Task\n## Status: in-review\n")

        mock_parser = MagicMock()
        mock_parser.parse_index.return_value = mock_units

        mock_mgr = MagicMock()

        with patch("devbench.cli.BacklogParser", return_value=mock_parser):
            with patch("devbench.cli.BACKLOG_ROOT", backlog_dir):
                with patch("devbench.cli.BacklogManager", return_value=mock_mgr):
                    result = cli.cmd_mark_done("E0-F1-S1-T2")

        assert result == 0
        mock_mgr.mark_done.assert_called_once()

    def test_returns_1_when_done_gate_fails(
        self, mock_units: list[WorkUnit], tmp_path: Path, backlog_dir: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        wu_file = backlog_dir / "E0-F1-S1-T2.md"
        wu_file.write_text("# Task\n## Status: in-review\n")

        mock_parser = MagicMock()
        mock_parser.parse_index.return_value = mock_units

        mock_mgr = MagicMock()
        mock_mgr.mark_done.side_effect = RuntimeError("not all required judges passed")

        with patch("devbench.cli.BacklogParser", return_value=mock_parser):
            with patch("devbench.cli.BACKLOG_ROOT", backlog_dir):
                with patch("devbench.cli.BacklogManager", return_value=mock_mgr):
                    result = cli.cmd_mark_done("E0-F1-S1-T2")

        assert result == 1
        assert "not all required judges passed" in capsys.readouterr().err


class TestCmdReviewNoRepoPath:
    """Test cmd_review when repo path is missing."""

    def test_returns_1_when_no_repo_path(self, mock_units: list[WorkUnit]) -> None:
        mock_parser = MagicMock()
        mock_parser.parse_index.return_value = mock_units

        with patch("devbench.cli.BacklogParser", return_value=mock_parser):
            with patch("devbench.cli.REPO_LOCAL_PATHS", {}):
                result = cli.cmd_review("E0-F1-S1-T2")

        assert result == 1


class TestGetPriorFeedback:
    """Test _get_prior_feedback parses orchestrator log for previous review feedback."""

    def test_returns_empty_when_no_log(self, tmp_path: Path) -> None:
        with patch.dict("os.environ", {"JUDGE_LOG_FILE": str(tmp_path / "missing.log")}):
            result = cli._get_prior_feedback("E0-F1-S1-T1")
        assert result == {}

    def test_extracts_feedback_per_judge(self, tmp_path: Path) -> None:
        log_file = tmp_path / "test.log"
        log_file.write_text(
            "2026-03-05T05:22:05Z [judges.cli] INFO code_review judge feedback for E0-F1-S1-T1: Fix the timeout\n"
            "2026-03-05T05:22:31Z [judges.cli] INFO test_review judge feedback for E0-F1-S1-T1: Add assertions\n"
        )
        with patch.dict("os.environ", {"JUDGE_LOG_FILE": str(log_file)}):
            result = cli._get_prior_feedback("E0-F1-S1-T1")
        assert result["code_review"] == "Fix the timeout"
        assert result["test_review"] == "Add assertions"

    def test_keeps_only_latest_feedback(self, tmp_path: Path) -> None:
        log_file = tmp_path / "test.log"
        log_file.write_text(
            "2026-03-05T05:00:00Z [judges.cli] INFO code_review judge feedback for E0-T1: Old feedback\n"
            "2026-03-05T06:00:00Z [judges.cli] INFO code_review judge feedback for E0-T1: New feedback\n"
        )
        with patch.dict("os.environ", {"JUDGE_LOG_FILE": str(log_file)}):
            result = cli._get_prior_feedback("E0-T1")
        assert result["code_review"] == "New feedback"

    def test_ignores_other_unit_ids(self, tmp_path: Path) -> None:
        log_file = tmp_path / "test.log"
        log_file.write_text(
            "2026-03-05T05:00:00Z [judges.cli] INFO code_review judge feedback for E0-T1: Relevant\n"
            "2026-03-05T05:00:00Z [judges.cli] INFO code_review judge feedback for E0-T2: Irrelevant\n"
        )
        with patch.dict("os.environ", {"JUDGE_LOG_FILE": str(log_file)}):
            result = cli._get_prior_feedback("E0-T1")
        assert result["code_review"] == "Relevant"
        assert "E0-T2" not in str(result)


class TestCmdReviewWritesComments:
    """Bug fix: cmd_review must write REVIEW_PASS/REVIEW_FAIL comments to work-unit file.

    Previously cmd_review only logged to the orchestrator log; it never wrote
    judge comments to the work-unit file.  That made mark_done (which reads
    [REVIEW_PASS] entries from the file) always fail after a successful review.
    """

    def _make_pass_result(self, judge_name: str) -> JudgeResult:
        return JudgeResult(
            judge_name=judge_name,
            verdict=Verdict.PASS,
            reasoning="all good",
            feedback="",
            evidence=[],
        )

    def test_cmd_review_writes_review_pass_comments_for_all_judges(
        self, mock_units: list[WorkUnit], tmp_path: Path, backlog_dir: Path
    ) -> None:
        wu_file = backlog_dir / "E0-F1-S1-T2.md"
        wu_file.write_text("# E0-F1-S1-T2: Task\n\n## Status: in-review\n", encoding="utf-8")

        judge_names = ["code_review", "test_review", "doc_review", "changes_manifest"]
        mock_judges = []
        for name in judge_names:
            m = MagicMock()
            m.name = name
            m.previous_feedback = ""
            m.evaluate.return_value = self._make_pass_result(name)
            mock_judges.append(m)

        mock_parser = MagicMock()
        mock_parser.parse_index.return_value = mock_units
        mock_mgr = MagicMock()

        with (
            patch("devbench.cli.BacklogParser", return_value=mock_parser),
            patch("devbench.cli.BACKLOG_ROOT", backlog_dir),
            patch("devbench.cli.BACKLOG_INDEX", tmp_path / "BACKLOG.md"),
            patch("devbench.cli.WORKSPACE_ROOT", tmp_path),
            patch("devbench.cli.REPO_LOCAL_PATHS", {"caylent-solutions/git-repo": tmp_path}),
            patch("devbench.cli.BacklogManager", return_value=mock_mgr),
            patch("devbench.cli.CodeReviewJudge", return_value=mock_judges[0]),
            patch("devbench.cli.TestReviewJudge", return_value=mock_judges[1]),
            patch("devbench.cli.DocReviewJudge", return_value=mock_judges[2]),
            patch("devbench.cli.ChangesManifestJudge", return_value=mock_judges[3]),
            patch("devbench.cli.resolve_repo", return_value="caylent-solutions/git-repo"),
            patch("devbench.cli.validate_repo"),
        ):
            result = cli.cmd_review("E0-F1-S1-T2")

        assert result == 0
        content = wu_file.read_text(encoding="utf-8")
        assert "[REVIEW_PASS]" in content
        for name in judge_names:
            assert f"[judge/{name}]" in content

    def test_review_then_mark_done_succeeds(
        self, mock_units: list[WorkUnit], tmp_path: Path, backlog_dir: Path
    ) -> None:
        """Full flow: review writes comments, mark_done reads them and succeeds."""
        from devbench.backlog.manager import BacklogManager as RealMgr

        wu_file = backlog_dir / "E0-F1-S1-T2.md"
        wu_file.write_text("# E0-F1-S1-T2: Task\n\n## Status: in-review\n", encoding="utf-8")

        backlog_index = tmp_path / "BACKLOG.md"
        backlog_index.write_text(
            "# Backlog\n\n"
            "| ID | Title | Type | Status | Dependencies | Repo | File Path |\n"
            "|-----|-------|------|--------|-------------|------|-----------|\n"
            "| E0-F1-S1-T2 | Second Task | Task | in-review | none | repo |"
            " `backlog/E0-F1-S1-T2.md` |\n",
            encoding="utf-8",
        )

        judge_names = ["code_review", "test_review", "doc_review", "changes_manifest"]
        mock_judges = []
        for name in judge_names:
            m = MagicMock()
            m.name = name
            m.previous_feedback = ""
            m.evaluate.return_value = self._make_pass_result(name)
            mock_judges.append(m)

        mock_parser = MagicMock()
        mock_parser.parse_index.return_value = mock_units

        with (
            patch("devbench.cli.BacklogParser", return_value=mock_parser),
            patch("devbench.cli.BACKLOG_ROOT", backlog_dir),
            patch("devbench.cli.BACKLOG_INDEX", backlog_index),
            patch("devbench.cli.WORKSPACE_ROOT", tmp_path),
            patch("devbench.cli.REPO_LOCAL_PATHS", {"caylent-solutions/git-repo": tmp_path}),
            patch("devbench.cli.CodeReviewJudge", return_value=mock_judges[0]),
            patch("devbench.cli.TestReviewJudge", return_value=mock_judges[1]),
            patch("devbench.cli.DocReviewJudge", return_value=mock_judges[2]),
            patch("devbench.cli.ChangesManifestJudge", return_value=mock_judges[3]),
            patch("devbench.cli.resolve_repo", return_value="caylent-solutions/git-repo"),
            patch("devbench.cli.validate_repo"),
        ):
            review_result = cli.cmd_review("E0-F1-S1-T2")

        assert review_result == 0

        # After review, mark_done must succeed (gate passes because comments were written)
        real_mgr = RealMgr()
        real_mgr.mark_done(wu_file, backlog_index, "E0-F1-S1-T2")
        assert "## Status: done" in wu_file.read_text(encoding="utf-8")


class TestCmdValidateBacklogPathResolution:
    """Bug fix: cmd_validate_backlog must pass workspace root (BACKLOG_INDEX.parent) to validate(),
    not BACKLOG_ROOT — otherwise file paths of the form 'backlog/...' get resolved as
    BACKLOG_ROOT/backlog/... which is a double 'backlog/' and causes false 'file missing' errors.
    """

    def _make_layout(self, workspace: Path) -> tuple[Path, Path]:
        """Create realistic layout: BACKLOG.md at workspace root, work unit in workspace/backlog/."""
        backlog_dir = workspace / BACKLOG_SUBDIR
        backlog_dir.mkdir(parents=True, exist_ok=True)
        wu = backlog_dir / "E0-F1-S1-T1.md"
        wu.write_text("# E0-F1-S1-T1: Task\n\n## Status: in-queue\n\n## Comments\n", encoding="utf-8")
        idx = workspace / "BACKLOG.md"
        idx.write_text(
            "# Backlog\n\n"
            "| ID | Title | Type | Status | Dependencies | Repo | File Path |\n"
            "|-----|-------|------|--------|-------------|------|-----------|\n"
            "| E0-F1-S1-T1 | Task 1 | Task | in-queue | none | repo | `backlog/E0-F1-S1-T1.md` |\n",
            encoding="utf-8",
        )
        return idx, backlog_dir

    def test_no_false_file_missing_errors_with_real_layout(self, tmp_path: Path) -> None:
        """When BACKLOG_INDEX is at workspace root and BACKLOG_ROOT = workspace/backlog,
        validate-backlog must return 0 (no false 'file missing' errors).
        """
        idx, backlog_dir = self._make_layout(tmp_path)
        # Simulate production: BACKLOG_INDEX at workspace, BACKLOG_ROOT = workspace/backlog
        with (
            patch("devbench.cli.BACKLOG_INDEX", idx),
            patch("devbench.cli.BACKLOG_ROOT", backlog_dir),
        ):
            result = cli.cmd_validate_backlog()
        assert result == 0

    def test_validate_called_with_workspace_root_not_backlog_root(self, tmp_path: Path) -> None:
        """validate() must receive backlog_index.parent (workspace root), not BACKLOG_ROOT."""
        idx, backlog_dir = self._make_layout(tmp_path)
        mock_mgr = MagicMock()
        mock_mgr.validate.return_value = []

        with (
            patch("devbench.cli.BacklogManager", return_value=mock_mgr),
            patch("devbench.cli.BACKLOG_INDEX", idx),
            patch("devbench.cli.BACKLOG_ROOT", backlog_dir),
        ):
            cli.cmd_validate_backlog()

        # Second arg must be workspace root (idx.parent), not BACKLOG_ROOT (backlog_dir)
        _, call_kwargs = mock_mgr.validate.call_args
        positional = mock_mgr.validate.call_args.args
        workspace_root_arg = positional[1] if len(positional) > 1 else call_kwargs.get("backlog_root")
        assert workspace_root_arg == idx.parent
        assert workspace_root_arg != backlog_dir


class TestCmdValidateBacklog:
    """Test cmd_validate_backlog command."""

    def test_returns_0_when_backlog_is_valid(self, tmp_path: Path) -> None:
        mock_mgr = MagicMock()
        mock_mgr.validate.return_value = []

        with patch("devbench.cli.BacklogManager", return_value=mock_mgr):
            with patch("devbench.cli.BACKLOG_INDEX", tmp_path / "BACKLOG.md"):
                with patch("devbench.cli.BACKLOG_ROOT", tmp_path):
                    result = cli.cmd_validate_backlog()

        assert result == 0

    def test_returns_1_and_prints_errors_when_invalid(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        mock_mgr = MagicMock()
        mock_mgr.validate.return_value = ["E0-T1: work unit file missing", "E0-T2: status mismatch"]

        with patch("devbench.cli.BacklogManager", return_value=mock_mgr):
            with patch("devbench.cli.BACKLOG_INDEX", tmp_path / "BACKLOG.md"):
                with patch("devbench.cli.BACKLOG_ROOT", tmp_path):
                    result = cli.cmd_validate_backlog()

        assert result == 1
        output = capsys.readouterr().out
        assert "E0-T1" in output
        assert "E0-T2" in output


class TestMain:
    """Test main argument parsing."""

    def test_no_args_returns_1(self) -> None:
        with patch("sys.argv", ["judges.cli"]):
            result = cli.main()
        assert result == 1

    def test_unknown_command_returns_1(self) -> None:
        with patch("sys.argv", ["judges.cli", "nonexistent"]):
            result = cli.main()
        assert result == 1

    def test_dispatches_status(self) -> None:
        mock_fn = MagicMock(return_value=0)
        with patch("sys.argv", ["judges.cli", "status"]):
            with patch.dict(cli._COMMANDS, {"status": (mock_fn, 0, "Show backlog summary")}):
                result = cli.main()
        assert result == 0
        mock_fn.assert_called_once()

    def test_dispatches_log_with_arg(self) -> None:
        mock_fn = MagicMock(return_value=0)
        with patch("sys.argv", ["judges.cli", "log", "hello"]):
            with patch.dict(cli._COMMANDS, {"log": (mock_fn, 1, "Log a message")}):
                result = cli.main()
        assert result == 0
        mock_fn.assert_called_once_with("hello")

    def test_missing_required_arg_returns_1(self) -> None:
        with patch("sys.argv", ["judges.cli", "execute"]):
            result = cli.main()
        assert result == 1

    def test_dispatches_with_extra_args(self) -> None:
        mock_fn = MagicMock(return_value=0)
        with patch("sys.argv", ["judges.cli", "execute", "T1", "feedback-text"]):
            with patch.dict(cli._COMMANDS, {"execute": (mock_fn, 1, "Execute")}):
                result = cli.main()
        assert result == 0


class TestPreParseConfig:
    """Test --config CLI pre-parse helper."""

    def test_sets_env_var_and_removes_args(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        import os

        config_path = str(tmp_path / "custom.yaml")
        argv = ["judges.cli", "--config", config_path, "status"]
        monkeypatch.delenv("JUDGE_CONFIG_PATH", raising=False)
        cli._pre_parse_config(argv)
        assert os.environ.get("JUDGE_CONFIG_PATH") == config_path
        assert "--config" not in argv
        assert config_path not in argv
        assert argv == ["judges.cli", "status"]

    def test_noop_when_config_not_present(self) -> None:
        argv = ["judges.cli", "status"]
        original = argv.copy()
        cli._pre_parse_config(argv)
        assert argv == original

    def test_noop_when_config_has_no_value(self) -> None:
        argv = ["judges.cli", "--config"]
        original = argv.copy()
        cli._pre_parse_config(argv)
        assert argv == original


# ---------------------------------------------------------------------------
# Fixtures shared by TestCmdSyncBlocked
# ---------------------------------------------------------------------------

def _make_unit(
    unit_id: str,
    title: str,
    status: WorkUnitStatus,
    deps: list[str],
    file_path: Path | None = None,
) -> WorkUnit:
    """Factory for WorkUnit instances used in sync-blocked tests."""
    return WorkUnit(
        id=unit_id,
        title=title,
        status=status,
        unit_type=WorkUnitType.TASK,
        file_path=file_path or Path(f"backlog/{unit_id}.md"),
        repo="caylent-solutions/devbench",
        dependencies=deps,
    )


class TestCmdSyncBlocked:
    """Tests for the sync-blocked CLI command (AC-1 through AC-6)."""

    # ------------------------------------------------------------------
    # AC-1: in-queue tasks with incomplete deps are marked blocked
    # ------------------------------------------------------------------

    def test_sync_blocked_marks_tasks_with_incomplete_epic_dep(
        self,
        tmp_path: Path,
        backlog_dir: Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """
        Given: E14-F2-T1 depends on E9 which is in-progress (not done)
        When: sync-blocked is run
        Then: E14-F2-T1 is blocked; report lists it with E9 as unmet dep
        Spec: AC-1
        """
        e9_file = backlog_dir / "E9.md"
        e9_file.write_text("# E9: Epic Nine\n\n## Status: in-progress\n")
        e14_file = backlog_dir / "E14-F2-T1.md"
        e14_file.write_text("# E14-F2-T1: Feature Two Task\n\n## Status: in-queue\n")

        e9 = _make_unit("E9", "Epic Nine", WorkUnitStatus.IN_PROGRESS, [], e9_file)
        e14_t1 = _make_unit("E14-F2-T1", "Feature Two Task", WorkUnitStatus.IN_QUEUE, ["E9"], e14_file)

        mock_parser = MagicMock()
        mock_parser.parse_index.return_value = [e9, e14_t1]

        mock_mgr = MagicMock()
        backlog_index = tmp_path / "BACKLOG.md"

        with (
            patch("devbench.cli.BacklogParser", return_value=mock_parser),
            patch("devbench.cli.BacklogManager", return_value=mock_mgr),
            patch("devbench.cli.BACKLOG_INDEX", backlog_index),
            patch("devbench.cli.BACKLOG_ROOT", backlog_dir),
        ):
            result = cli.cmd_sync_blocked()

        assert result == 0
        mock_mgr.force_status.assert_called_once_with(e14_file, backlog_index, "E14-F2-T1", "blocked")
        out = capsys.readouterr().out
        assert "E14-F2-T1" in out
        assert "E9" in out

    # ------------------------------------------------------------------
    # AC-2: idempotent — running twice produces same state
    # ------------------------------------------------------------------

    def test_sync_blocked_idempotent(
        self,
        tmp_path: Path,
        backlog_dir: Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """
        Given: E14-F2-T1 is already blocked (second run)
        When: sync-blocked is run again
        Then: force_status is NOT called again; exit 0
        Spec: AC-2
        """
        e9_file = backlog_dir / "E9.md"
        e9_file.write_text("# E9: Epic Nine\n\n## Status: in-progress\n")
        e14_file = backlog_dir / "E14-F2-T1.md"
        e14_file.write_text("# E14-F2-T1: Feature Two Task\n\n## Status: blocked\n")

        e9 = _make_unit("E9", "Epic Nine", WorkUnitStatus.IN_PROGRESS, [], e9_file)
        # Already blocked — sync-blocked must not re-process it
        e14_t1 = _make_unit("E14-F2-T1", "Feature Two Task", WorkUnitStatus.BLOCKED, ["E9"], e14_file)

        mock_parser = MagicMock()
        mock_parser.parse_index.return_value = [e9, e14_t1]

        mock_mgr = MagicMock()
        backlog_index = tmp_path / "BACKLOG.md"

        with (
            patch("devbench.cli.BacklogParser", return_value=mock_parser),
            patch("devbench.cli.BacklogManager", return_value=mock_mgr),
            patch("devbench.cli.BACKLOG_INDEX", backlog_index),
            patch("devbench.cli.BACKLOG_ROOT", backlog_dir),
        ):
            result = cli.cmd_sync_blocked()

        assert result == 0
        mock_mgr.force_status.assert_not_called()

    # ------------------------------------------------------------------
    # AC-3: in-queue units with all deps done stay in-queue
    # ------------------------------------------------------------------

    def test_sync_blocked_leaves_satisfied_units_in_queue(
        self,
        tmp_path: Path,
        backlog_dir: Path,
    ) -> None:
        """
        Given: T2 depends on T1 which is done
        When: sync-blocked is run
        Then: T2 status is unchanged (not blocked)
        Spec: AC-3
        """
        t1_file = backlog_dir / "E0-T1.md"
        t1_file.write_text("# E0-T1: Task One\n\n## Status: done\n")
        t2_file = backlog_dir / "E0-T2.md"
        t2_file.write_text("# E0-T2: Task Two\n\n## Status: in-queue\n")

        t1 = _make_unit("E0-T1", "Task One", WorkUnitStatus.DONE, [], t1_file)
        t2 = _make_unit("E0-T2", "Task Two", WorkUnitStatus.IN_QUEUE, ["E0-T1"], t2_file)

        mock_parser = MagicMock()
        mock_parser.parse_index.return_value = [t1, t2]

        mock_mgr = MagicMock()
        backlog_index = tmp_path / "BACKLOG.md"

        with (
            patch("devbench.cli.BacklogParser", return_value=mock_parser),
            patch("devbench.cli.BacklogManager", return_value=mock_mgr),
            patch("devbench.cli.BACKLOG_INDEX", backlog_index),
            patch("devbench.cli.BACKLOG_ROOT", backlog_dir),
        ):
            result = cli.cmd_sync_blocked()

        assert result == 0
        mock_mgr.force_status.assert_not_called()

    # ------------------------------------------------------------------
    # AC-4: already-blocked units are not double-processed
    # ------------------------------------------------------------------

    def test_sync_blocked_skips_already_blocked_units(
        self,
        tmp_path: Path,
        backlog_dir: Path,
    ) -> None:
        """
        Given: T3 is already blocked (with unmet dep T1 that is in-queue)
        When: sync-blocked is run
        Then: force_status is NOT called for T3 (already blocked)
        Spec: AC-4
        """
        t1_file = backlog_dir / "E0-T1.md"
        t1_file.write_text("# E0-T1: Task One\n\n## Status: in-queue\n")
        t3_file = backlog_dir / "E0-T3.md"
        t3_file.write_text("# E0-T3: Task Three\n\n## Status: blocked\n")

        t1 = _make_unit("E0-T1", "Task One", WorkUnitStatus.IN_QUEUE, [], t1_file)
        t3 = _make_unit("E0-T3", "Task Three", WorkUnitStatus.BLOCKED, ["E0-T1"], t3_file)

        mock_parser = MagicMock()
        mock_parser.parse_index.return_value = [t1, t3]

        mock_mgr = MagicMock()
        backlog_index = tmp_path / "BACKLOG.md"

        with (
            patch("devbench.cli.BacklogParser", return_value=mock_parser),
            patch("devbench.cli.BacklogManager", return_value=mock_mgr),
            patch("devbench.cli.BACKLOG_INDEX", backlog_index),
            patch("devbench.cli.BACKLOG_ROOT", backlog_dir),
        ):
            result = cli.cmd_sync_blocked()

        assert result == 0
        mock_mgr.force_status.assert_not_called()

    # ------------------------------------------------------------------
    # AC-5: done/in-progress/in-review units are not touched
    # ------------------------------------------------------------------

    def test_sync_blocked_skips_done_in_progress_in_review(
        self,
        tmp_path: Path,
        backlog_dir: Path,
    ) -> None:
        """
        Given: units in done, in-progress, in-review status with unmet deps
        When: sync-blocked is run
        Then: none of them are changed
        Spec: AC-5
        """
        dep_file = backlog_dir / "E0-DEP.md"
        dep_file.write_text("# E0-DEP: Dep Unit\n\n## Status: in-queue\n")
        done_file = backlog_dir / "E0-DONE.md"
        done_file.write_text("# E0-DONE: Done Unit\n\n## Status: done\n")
        prog_file = backlog_dir / "E0-PROG.md"
        prog_file.write_text("# E0-PROG: Progress Unit\n\n## Status: in-progress\n")
        rev_file = backlog_dir / "E0-REV.md"
        rev_file.write_text("# E0-REV: Review Unit\n\n## Status: in-review\n")

        dep = _make_unit("E0-DEP", "Dep Unit", WorkUnitStatus.IN_QUEUE, [], dep_file)
        done_unit = _make_unit("E0-DONE", "Done Unit", WorkUnitStatus.DONE, ["E0-DEP"], done_file)
        prog_unit = _make_unit("E0-PROG", "Progress Unit", WorkUnitStatus.IN_PROGRESS, ["E0-DEP"], prog_file)
        rev_unit = _make_unit("E0-REV", "Review Unit", WorkUnitStatus.IN_REVIEW, ["E0-DEP"], rev_file)

        mock_parser = MagicMock()
        mock_parser.parse_index.return_value = [dep, done_unit, prog_unit, rev_unit]

        mock_mgr = MagicMock()
        backlog_index = tmp_path / "BACKLOG.md"

        with (
            patch("devbench.cli.BacklogParser", return_value=mock_parser),
            patch("devbench.cli.BacklogManager", return_value=mock_mgr),
            patch("devbench.cli.BACKLOG_INDEX", backlog_index),
            patch("devbench.cli.BACKLOG_ROOT", backlog_dir),
        ):
            result = cli.cmd_sync_blocked()

        assert result == 0
        mock_mgr.force_status.assert_not_called()

    # ------------------------------------------------------------------
    # AC-6: output lists each newly blocked unit with its unmet dep IDs
    # ------------------------------------------------------------------

    def test_sync_blocked_report_lists_unmet_deps(
        self,
        tmp_path: Path,
        backlog_dir: Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """
        Given: T2 depends on T1 (in-queue) and T3 (in-queue) — both unmet
        When: sync-blocked is run
        Then: report lists T2 with both T1 and T3 as unmet dep IDs
        Spec: AC-6
        """
        t1_file = backlog_dir / "E0-T1.md"
        t1_file.write_text("# E0-T1: Task One\n\n## Status: in-queue\n")
        t3_file = backlog_dir / "E0-T3.md"
        t3_file.write_text("# E0-T3: Task Three\n\n## Status: in-queue\n")
        t2_file = backlog_dir / "E0-T2.md"
        t2_file.write_text("# E0-T2: Task Two\n\n## Status: in-queue\n")

        t1 = _make_unit("E0-T1", "Task One", WorkUnitStatus.IN_QUEUE, [], t1_file)
        t3 = _make_unit("E0-T3", "Task Three", WorkUnitStatus.IN_QUEUE, [], t3_file)
        t2 = _make_unit("E0-T2", "Task Two", WorkUnitStatus.IN_QUEUE, ["E0-T1", "E0-T3"], t2_file)

        mock_parser = MagicMock()
        mock_parser.parse_index.return_value = [t1, t3, t2]

        mock_mgr = MagicMock()
        backlog_index = tmp_path / "BACKLOG.md"

        with (
            patch("devbench.cli.BacklogParser", return_value=mock_parser),
            patch("devbench.cli.BacklogManager", return_value=mock_mgr),
            patch("devbench.cli.BACKLOG_INDEX", backlog_index),
            patch("devbench.cli.BACKLOG_ROOT", backlog_dir),
        ):
            result = cli.cmd_sync_blocked()

        assert result == 0
        out = capsys.readouterr().out
        assert "E0-T2" in out
        assert "E0-T1" in out
        assert "E0-T3" in out

    # ------------------------------------------------------------------
    # Fail-fast: missing work unit file
    # ------------------------------------------------------------------

    def test_sync_blocked_missing_file_returns_nonzero(
        self,
        tmp_path: Path,
        backlog_dir: Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """
        Given: T2 has unmet dep T1 but T2's file_path does not exist on disk
        When: sync-blocked is run
        Then: exits 1 with an error message on stderr naming the unit and path
        Spec: Fail-fast principle — no silent fallback
        """
        # T1 file exists (it's done); T2's file does NOT exist on disk
        t1_file = backlog_dir / "E0-T1.md"
        t1_file.write_text("# E0-T1: Task One\n\n## Status: in-queue\n")
        # Provide an absolute path that does not exist
        missing_file = backlog_dir / "E0-T2-nonexistent.md"

        t1 = _make_unit("E0-T1", "Task One", WorkUnitStatus.IN_QUEUE, [], t1_file)
        t2 = _make_unit("E0-T2", "Task Two", WorkUnitStatus.IN_QUEUE, ["E0-T1"], missing_file)

        mock_parser = MagicMock()
        mock_parser.parse_index.return_value = [t1, t2]

        mock_mgr = MagicMock()
        backlog_index = tmp_path / "BACKLOG.md"

        with (
            patch("devbench.cli.BacklogParser", return_value=mock_parser),
            patch("devbench.cli.BacklogManager", return_value=mock_mgr),
            patch("devbench.cli.BACKLOG_INDEX", backlog_index),
            patch("devbench.cli.BACKLOG_ROOT", backlog_dir),
        ):
            result = cli.cmd_sync_blocked()

        assert result == 1
        err = capsys.readouterr().err
        assert "ERROR" in err
        assert "E0-T2" in err
        mock_mgr.force_status.assert_not_called()

    # ------------------------------------------------------------------
    # Summary line format
    # ------------------------------------------------------------------

    def test_sync_blocked_summary_line(
        self,
        tmp_path: Path,
        backlog_dir: Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """
        Given: 1 newly blocked, 1 already blocked, 2 satisfied in-queue
               (one with no deps, one whose dep is done)
        When: sync-blocked is run
        Then: summary line reads 'Blocked 1 unit(s). 1 already blocked. 2 in-queue units have all deps met.'
        Spec: Definition of Done — summary line
        """
        dep_file = backlog_dir / "E0-DEP.md"
        dep_file.write_text("# E0-DEP: Dep\n\n## Status: in-queue\n")
        done_dep_file = backlog_dir / "E0-DONE-DEP.md"
        done_dep_file.write_text("# E0-DONE-DEP: Done Dep\n\n## Status: done\n")
        t_blocked_already_file = backlog_dir / "E0-TB.md"
        t_blocked_already_file.write_text("# E0-TB: Already Blocked\n\n## Status: blocked\n")
        t_new_blocked_file = backlog_dir / "E0-TN.md"
        t_new_blocked_file.write_text("# E0-TN: New Block\n\n## Status: in-queue\n")
        t_satisfied_file = backlog_dir / "E0-TS.md"
        t_satisfied_file.write_text("# E0-TS: Satisfied\n\n## Status: in-queue\n")

        # dep: in-queue, no deps → counted as satisfied in-queue (1)
        dep = _make_unit("E0-DEP", "Dep", WorkUnitStatus.IN_QUEUE, [], dep_file)
        done_dep = _make_unit("E0-DONE-DEP", "Done Dep", WorkUnitStatus.DONE, [], done_dep_file)
        t_blocked_already = _make_unit(
            "E0-TB", "Already Blocked", WorkUnitStatus.BLOCKED, ["E0-DEP"], t_blocked_already_file
        )
        # t_new_blocked: in-queue, dep on E0-DEP (in-queue, not done) → newly blocked (1)
        t_new_blocked = _make_unit("E0-TN", "New Block", WorkUnitStatus.IN_QUEUE, ["E0-DEP"], t_new_blocked_file)
        # t_satisfied: in-queue, dep on E0-DONE-DEP (done) → satisfied in-queue (2)
        t_satisfied = _make_unit("E0-TS", "Satisfied", WorkUnitStatus.IN_QUEUE, ["E0-DONE-DEP"], t_satisfied_file)

        mock_parser = MagicMock()
        mock_parser.parse_index.return_value = [dep, done_dep, t_blocked_already, t_new_blocked, t_satisfied]

        mock_mgr = MagicMock()
        backlog_index = tmp_path / "BACKLOG.md"

        with (
            patch("devbench.cli.BacklogParser", return_value=mock_parser),
            patch("devbench.cli.BacklogManager", return_value=mock_mgr),
            patch("devbench.cli.BACKLOG_INDEX", backlog_index),
            patch("devbench.cli.BACKLOG_ROOT", backlog_dir),
        ):
            result = cli.cmd_sync_blocked()

        assert result == 0
        out = capsys.readouterr().out
        assert "Blocked 1 unit(s). 1 already blocked. 2 in-queue units have all deps met." in out


# ---------------------------------------------------------------------------
# Helpers shared by TestDepGuardExecute / TestDepGuardNext
# ---------------------------------------------------------------------------

def _make_dep_guard_units(backlog_dir: Path) -> tuple[list, object, object]:
    """Build units for dep-guard tests.

    Returns (units_list, epic_unit, task_unit) where:
      - epic_unit (E9) is in-progress (not done)
      - task_unit (E15-F1-S1-T1) depends on E9
    """
    epic = _make_unit("E9", "Epic Nine", WorkUnitStatus.IN_PROGRESS, [])
    task = _make_unit(
        "E15-F1-S1-T1",
        "Dep Guard Task",
        WorkUnitStatus.IN_QUEUE,
        ["E9"],
        backlog_dir / "E15-F1-S1-T1.md",
    )
    return [epic, task], epic, task


class TestDepGuardExecute:
    """Tests for the pre-run dep guard in cmd_execute (AC-1, AC-2, AC-4)."""

    def test_execute_refuses_unit_with_incomplete_dep(
        self,
        tmp_path: Path,
        backlog_dir: Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """
        Given: E15-F1-S1-T1 depends on E9 which is in-progress (not done)
        When: execute E15-F1-S1-T1 is invoked
        Then: exits 1; error on stderr names E9 and its status
        Spec: AC-1
        """
        units, epic, task = _make_dep_guard_units(backlog_dir)

        mock_parser = MagicMock()
        mock_parser.parse_index.return_value = units

        with (
            patch("devbench.cli.BacklogParser", return_value=mock_parser),
            patch("devbench.cli.BACKLOG_ROOT", backlog_dir),
        ):
            result = cli.cmd_execute("E15-F1-S1-T1")

        assert result == 1
        err = capsys.readouterr().err
        assert "E9" in err
        assert "in progress" in err.lower()

    def test_execute_proceeds_when_all_deps_done(
        self,
        tmp_path: Path,
        backlog_dir: Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """
        Given: E15-F1-S1-T1 depends on E9 which is done
        When: execute E15-F1-S1-T1 is invoked
        Then: the guard passes and the executor is called
        Spec: AC-2
        """
        from devbench.execution.executor import ExecutionResult, ExecutionStatus

        wu_file = backlog_dir / "E15-F1-S1-T1.md"
        wu_file.write_text("# E15-F1-S1-T1: Dep Guard Task\n\n## Status: in-queue\n")

        epic_done = _make_unit("E9", "Epic Nine", WorkUnitStatus.DONE, [])
        task = _make_unit(
            "E15-F1-S1-T1",
            "Dep Guard Task",
            WorkUnitStatus.IN_QUEUE,
            ["E9"],
            wu_file,
        )
        units = [epic_done, task]

        mock_parser = MagicMock()
        mock_parser.parse_index.return_value = units

        exec_result = ExecutionResult(status=ExecutionStatus.IN_REVIEW, output="done", blocker="")

        with (
            patch("devbench.cli.BacklogParser", return_value=mock_parser),
            patch("devbench.cli.BACKLOG_ROOT", backlog_dir),
            patch("devbench.execution.executor.execute", return_value=exec_result),
        ):
            result = cli.cmd_execute("E15-F1-S1-T1")

        assert result == 0
        output = json.loads(capsys.readouterr().out)
        assert output["status"] == "in-review"

    def test_execute_dep_guard_error_on_stderr_not_stdout(
        self,
        tmp_path: Path,
        backlog_dir: Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """
        Given: E15-F1-S1-T1 has unmet dep E9
        When: execute E15-F1-S1-T1 is invoked
        Then: the dep error appears on stderr; stdout is empty
        Spec: AC-4
        """
        units, epic, task = _make_dep_guard_units(backlog_dir)

        mock_parser = MagicMock()
        mock_parser.parse_index.return_value = units

        with (
            patch("devbench.cli.BacklogParser", return_value=mock_parser),
            patch("devbench.cli.BACKLOG_ROOT", backlog_dir),
        ):
            cli.cmd_execute("E15-F1-S1-T1")

        captured = capsys.readouterr()
        assert captured.out.strip() == ""
        assert "E9" in captured.err


class TestDepGuardNext:
    """Tests for the secondary dep guard in cmd_next (AC-3, AC-4)."""

    def test_next_secondary_guard_blocks_unit_with_unmet_dep(
        self,
        tmp_path: Path,
        backlog_dir: Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """
        Given: get_parallel_candidates returns a task whose epic dep is in-progress
        When: cmd_next is invoked
        Then: exits 1; error on stderr lists the unmet dep ID and status;
              BacklogManager.force_status is NOT called
        Spec: AC-3
        """
        epic = _make_unit("E9", "Epic Nine", WorkUnitStatus.IN_PROGRESS, [])
        task = _make_unit(
            "E15-F1-S1-T1",
            "Dep Guard Task",
            WorkUnitStatus.IN_QUEUE,
            ["E9"],
            backlog_dir / "E15-F1-S1-T1.md",
        )
        units = [epic, task]

        mock_parser = MagicMock()
        mock_parser.parse_index.return_value = units
        # Simulate the guard being triggered: candidates returns the task
        # even though its dep is unmet (this is the safety-net scenario)
        mock_parser.get_parallel_candidates.return_value = [task]

        mock_mgr = MagicMock()

        with (
            patch("devbench.cli.BacklogParser", return_value=mock_parser),
            patch("devbench.cli.BacklogManager", return_value=mock_mgr),
        ):
            result = cli.cmd_next()

        assert result == 1
        err = capsys.readouterr().err
        assert "E9" in err
        mock_mgr.force_status.assert_not_called()

    def test_next_dep_guard_error_on_stderr_not_stdout(
        self,
        tmp_path: Path,
        backlog_dir: Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """
        Given: cmd_next candidate has unmet dep
        When: cmd_next is invoked
        Then: error on stderr; stdout is empty
        Spec: AC-4
        """
        epic = _make_unit("E9", "Epic Nine", WorkUnitStatus.IN_PROGRESS, [])
        task = _make_unit(
            "E15-F1-S1-T1",
            "Dep Guard Task",
            WorkUnitStatus.IN_QUEUE,
            ["E9"],
            backlog_dir / "E15-F1-S1-T1.md",
        )

        mock_parser = MagicMock()
        mock_parser.parse_index.return_value = [epic, task]
        mock_parser.get_parallel_candidates.return_value = [task]

        mock_mgr = MagicMock()

        with (
            patch("devbench.cli.BacklogParser", return_value=mock_parser),
            patch("devbench.cli.BacklogManager", return_value=mock_mgr),
        ):
            cli.cmd_next()

        captured = capsys.readouterr()
        assert captured.out.strip() == ""
        assert "E9" in captured.err


class TestCmdNextReadOnly:
    """Tests for AC-1, AC-2, AC-4: next is read-only; --claim sets in-progress."""

    def test_next_does_not_mutate_status(
        self,
        mock_units: list[WorkUnit],
        tmp_path: Path,
        backlog_dir: Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """
        Given: a valid actionable work unit
        When: cmd_next() is called without --claim
        Then: JSON is printed AND force_status is never called
        Spec: AC-1
        """
        wu_file = backlog_dir / "E0-F1-S1-T2.md"
        wu_file.write_text("# E0-F1-S1-T2: Task\n\n## Status: in-queue\n")

        mock_parser = MagicMock()
        mock_parser.parse_index.return_value = mock_units
        mock_parser.get_parallel_candidates.return_value = [mock_units[1]]

        mock_mgr = MagicMock()

        with (
            patch("devbench.cli.BacklogParser", return_value=mock_parser),
            patch("devbench.cli.BacklogManager", return_value=mock_mgr),
            patch("devbench.cli.BACKLOG_ROOT", backlog_dir),
        ):
            result = cli.cmd_next()

        assert result == 0
        output = json.loads(capsys.readouterr().out.strip())
        assert output["id"] == "E0-F1-S1-T2"
        mock_mgr.force_status.assert_not_called()

    def test_next_claim_sets_in_progress(
        self,
        mock_units: list[WorkUnit],
        tmp_path: Path,
        backlog_dir: Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """
        Given: a valid actionable work unit
        When: cmd_next(claim=True) is called
        Then: JSON is printed AND force_status is called with in-progress
        Spec: AC-2
        """
        wu_file = backlog_dir / "E0-F1-S1-T2.md"
        wu_file.write_text("# E0-F1-S1-T2: Task\n\n## Status: in-queue\n")

        # Use an absolute file_path so the code resolves it directly without BACKLOG_ROOT
        claimable_unit = WorkUnit(
            id="E0-F1-S1-T2",
            title="Second Task",
            status=WorkUnitStatus.IN_QUEUE,
            unit_type=WorkUnitType.TASK,
            file_path=wu_file,
            repo="caylent-solutions/git-repo",
            dependencies=[],
        )
        units = [mock_units[0], claimable_unit, mock_units[2]]

        mock_parser = MagicMock()
        mock_parser.parse_index.return_value = units
        mock_parser.get_parallel_candidates.return_value = [claimable_unit]

        mock_mgr = MagicMock()

        with (
            patch("devbench.cli.BacklogParser", return_value=mock_parser),
            patch("devbench.cli.BacklogManager", return_value=mock_mgr),
            patch("devbench.cli.BACKLOG_INDEX", tmp_path / "BACKLOG.md"),
        ):
            result = cli.cmd_next(claim=True)

        assert result == 0
        output = json.loads(capsys.readouterr().out.strip())
        assert output["id"] == "E0-F1-S1-T2"
        mock_mgr.force_status.assert_called_once()
        call_args = mock_mgr.force_status.call_args
        assert call_args.args[3] == "in-progress"

    def test_next_twice_returns_same_unit(
        self,
        mock_units: list[WorkUnit],
        backlog_dir: Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """
        Given: an actionable work unit
        When: cmd_next() is called twice (without --claim)
        Then: both calls return the same unit ID (no status mutation between calls)
        Spec: AC-4
        """
        wu_file = backlog_dir / "E0-F1-S1-T2.md"
        wu_file.write_text("# E0-F1-S1-T2: Task\n\n## Status: in-queue\n")

        mock_parser = MagicMock()
        mock_parser.parse_index.return_value = mock_units
        mock_parser.get_parallel_candidates.return_value = [mock_units[1]]

        with (
            patch("devbench.cli.BacklogParser", return_value=mock_parser),
            patch("devbench.cli.BACKLOG_ROOT", backlog_dir),
        ):
            result1 = cli.cmd_next()
            out1 = json.loads(capsys.readouterr().out.strip())
            result2 = cli.cmd_next()
            out2 = json.loads(capsys.readouterr().out.strip())

        assert result1 == 0
        assert result2 == 0
        assert out1["id"] == out2["id"] == "E0-F1-S1-T2"

    def test_next_claim_missing_file_returns_1(
        self,
        mock_units: list[WorkUnit],
        tmp_path: Path,
        backlog_dir: Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """
        Given: a candidate work unit whose file does not exist on disk
        When: cmd_next(claim=True) is called
        Then: exit code is 1, error message on stderr, force_status is never called
        Spec: AC-2 (fail-fast on missing file during claim)
        """
        # Unit with an absolute path pointing to a file that does NOT exist
        missing_file = backlog_dir / "E0-F1-S1-T2.md"
        # Intentionally do NOT create the file
        claimable_unit = WorkUnit(
            id="E0-F1-S1-T2",
            title="Second Task",
            status=WorkUnitStatus.IN_QUEUE,
            unit_type=WorkUnitType.TASK,
            file_path=missing_file,
            repo="caylent-solutions/git-repo",
            dependencies=[],
        )
        units = [mock_units[0], claimable_unit, mock_units[2]]

        mock_parser = MagicMock()
        mock_parser.parse_index.return_value = units
        mock_parser.get_parallel_candidates.return_value = [claimable_unit]

        mock_mgr = MagicMock()

        with (
            patch("devbench.cli.BacklogParser", return_value=mock_parser),
            patch("devbench.cli.BacklogManager", return_value=mock_mgr),
            patch("devbench.cli.BACKLOG_INDEX", tmp_path / "BACKLOG.md"),
            patch("devbench.cli.BACKLOG_ROOT", backlog_dir),
            patch("devbench.cli.WORKSPACE_ROOT", tmp_path),
        ):
            result = cli.cmd_next(claim=True)

        assert result == 1
        captured = capsys.readouterr()
        assert "Cannot claim" in captured.err
        assert "E0-F1-S1-T2" in captured.err
        mock_mgr.force_status.assert_not_called()
