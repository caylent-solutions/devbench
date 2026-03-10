"""Tests for judges.cli module."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from devbench import cli
from devbench.backlog.work_unit import WorkUnit, WorkUnitStatus, WorkUnitType
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
        self, mock_units: list[WorkUnit], tmp_path: Path, capsys: pytest.CaptureFixture
    ) -> None:
        wu_file = tmp_path / "backlog" / "E0-F1-S1-T2.md"
        wu_file.parent.mkdir(parents=True, exist_ok=True)
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
            with patch("devbench.cli.BACKLOG_ROOT", tmp_path / "backlog"):
                with patch("devbench.cli.BACKLOG_INDEX", tmp_path / "BACKLOG.md"):
                    with patch("devbench.cli.WORKSPACE_ROOT", tmp_path):
                        with patch("devbench.cli.REPO_LOCAL_PATHS", {"caylent-solutions/git-repo": tmp_path}):
                            with patch("devbench.cli.BacklogManagerJudge", return_value=mock_mgr):
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
        self, mock_units: list[WorkUnit], tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        from devbench.execution.executor import ExecutionResult, ExecutionStatus

        wu_file = tmp_path / "backlog" / "E0-F1-S1-T2.md"
        wu_file.parent.mkdir(parents=True, exist_ok=True)
        wu_file.write_text("# Task\n")

        mock_parser = MagicMock()
        mock_parser.parse_index.return_value = mock_units
        exec_result = ExecutionResult(status=ExecutionStatus.IN_REVIEW, output="done", blocker="")

        with patch("devbench.cli.BacklogParser", return_value=mock_parser):
            with patch("devbench.cli.BACKLOG_ROOT", tmp_path / "backlog"):
                with patch("devbench.execution.executor.execute", return_value=exec_result):
                    result = cli.cmd_execute("E0-F1-S1-T2")

        assert result == 0
        output = json.loads(capsys.readouterr().out)
        assert output["status"] == "in-review"

    def test_returns_1_on_failure(self, mock_units: list[WorkUnit], tmp_path: Path) -> None:
        from devbench.execution.executor import ExecutionResult, ExecutionStatus

        wu_file = tmp_path / "backlog" / "E0-F1-S1-T2.md"
        wu_file.parent.mkdir(parents=True, exist_ok=True)
        wu_file.write_text("# Task\n")

        mock_parser = MagicMock()
        mock_parser.parse_index.return_value = mock_units
        exec_result = ExecutionResult(status=ExecutionStatus.FAILED, output="error", blocker="")

        with patch("devbench.cli.BacklogParser", return_value=mock_parser):
            with patch("devbench.cli.BACKLOG_ROOT", tmp_path / "backlog"):
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
        self, mock_units: list[WorkUnit], tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        wu_file = tmp_path / "backlog" / "E0-F1-S1-T2.md"
        wu_file.parent.mkdir(parents=True, exist_ok=True)
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
            with patch("devbench.cli.BACKLOG_ROOT", tmp_path / "backlog"):
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
        self, mock_units: list[WorkUnit], tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        wu_file = tmp_path / "backlog" / "E0-F1-S1-T2.md"
        wu_file.parent.mkdir(parents=True, exist_ok=True)
        wu_file.write_text("# Task\n## Status: in-queue\n")

        mock_parser = MagicMock()
        mock_parser.parse_index.return_value = mock_units

        mock_mgr = MagicMock()

        with patch("devbench.cli.BacklogParser", return_value=mock_parser):
            with patch("devbench.cli.BACKLOG_ROOT", tmp_path / "backlog"):
                with patch("devbench.cli.BacklogManagerJudge", return_value=mock_mgr):
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
        self, mock_units: list[WorkUnit], tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        wu_file = tmp_path / "backlog" / "E0-F1-S1-T2.md"
        wu_file.parent.mkdir(parents=True, exist_ok=True)
        wu_file.write_text("# Task\n## Status: in-review\n")

        mock_parser = MagicMock()
        mock_parser.parse_index.return_value = mock_units

        mock_mgr = MagicMock()

        with patch("devbench.cli.BacklogParser", return_value=mock_parser):
            with patch("devbench.cli.BACKLOG_ROOT", tmp_path / "backlog"):
                with patch("devbench.cli.BacklogManagerJudge", return_value=mock_mgr):
                    result = cli.cmd_mark_done("E0-F1-S1-T2")

        assert result == 0
        mock_mgr.mark_done.assert_called_once()

    def test_returns_1_when_done_gate_fails(
        self, mock_units: list[WorkUnit], tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        wu_file = tmp_path / "backlog" / "E0-F1-S1-T2.md"
        wu_file.parent.mkdir(parents=True, exist_ok=True)
        wu_file.write_text("# Task\n## Status: in-review\n")

        mock_parser = MagicMock()
        mock_parser.parse_index.return_value = mock_units

        mock_mgr = MagicMock()
        mock_mgr.mark_done.side_effect = RuntimeError("not all required judges passed")

        with patch("devbench.cli.BacklogParser", return_value=mock_parser):
            with patch("devbench.cli.BACKLOG_ROOT", tmp_path / "backlog"):
                with patch("devbench.cli.BacklogManagerJudge", return_value=mock_mgr):
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
        self, mock_units: list[WorkUnit], tmp_path: Path
    ) -> None:
        wu_file = tmp_path / "backlog" / "E0-F1-S1-T2.md"
        wu_file.parent.mkdir(parents=True, exist_ok=True)
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
            patch("devbench.cli.BACKLOG_ROOT", tmp_path / "backlog"),
            patch("devbench.cli.BACKLOG_INDEX", tmp_path / "BACKLOG.md"),
            patch("devbench.cli.WORKSPACE_ROOT", tmp_path),
            patch("devbench.cli.REPO_LOCAL_PATHS", {"caylent-solutions/git-repo": tmp_path}),
            patch("devbench.cli.BacklogManagerJudge", return_value=mock_mgr),
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
        self, mock_units: list[WorkUnit], tmp_path: Path
    ) -> None:
        """Full flow: review writes comments, mark_done reads them and succeeds."""
        from devbench.backlog.manager import BacklogManagerJudge as RealMgr

        wu_file = tmp_path / "backlog" / "E0-F1-S1-T2.md"
        wu_file.parent.mkdir(parents=True, exist_ok=True)
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
            patch("devbench.cli.BACKLOG_ROOT", tmp_path / "backlog"),
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
        backlog_dir = workspace / "backlog"
        backlog_dir.mkdir(parents=True, exist_ok=True)
        wu = backlog_dir / "E0-F1-S1-T1.md"
        wu.write_text("# E0-F1-S1-T1: Task\n\n## Status: in-queue\n", encoding="utf-8")
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
            patch("devbench.cli.BacklogManagerJudge", return_value=mock_mgr),
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

        with patch("devbench.cli.BacklogManagerJudge", return_value=mock_mgr):
            with patch("devbench.cli.BACKLOG_INDEX", tmp_path / "BACKLOG.md"):
                with patch("devbench.cli.BACKLOG_ROOT", tmp_path):
                    result = cli.cmd_validate_backlog()

        assert result == 0

    def test_returns_1_and_prints_errors_when_invalid(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        mock_mgr = MagicMock()
        mock_mgr.validate.return_value = ["E0-T1: work unit file missing", "E0-T2: status mismatch"]

        with patch("devbench.cli.BacklogManagerJudge", return_value=mock_mgr):
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
        mock_fn.assert_called_once_with("T1", "feedback-text")
