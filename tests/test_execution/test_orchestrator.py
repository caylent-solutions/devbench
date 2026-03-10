"""Tests for judges.orchestrator module."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from devbench.backlog.work_unit import WorkUnit, WorkUnitStatus, WorkUnitType
from devbench.execution.executor import ExecutionResult, ExecutionStatus
from devbench.judges.base import JudgeResult, Verdict

_ORC = "devbench.execution.orchestrator"


def _make_unit(
    tmp_path: Path, unit_id: str = "E0-F1-S1-T1", status: WorkUnitStatus = WorkUnitStatus.IN_QUEUE
) -> WorkUnit:
    """Create a WorkUnit backed by a real file."""
    fp = tmp_path / f"{unit_id}.md"
    fp.write_text(f"# {unit_id}: Task\n\n## Status: {status.value}\n\n## Comments\n")
    return WorkUnit(
        id=unit_id,
        title="Test Task",
        status=status,
        unit_type=WorkUnitType.TASK,
        file_path=fp,
        repo="caylent-solutions/git-repo",
        dependencies=[],
        description="Test description",
    )


def _pass_result(name: str = "test") -> JudgeResult:
    return JudgeResult(judge_name=name, verdict=Verdict.PASS, reasoning="ok", feedback="", evidence=[])


def _fail_result(name: str = "test") -> JudgeResult:
    return JudgeResult(judge_name=name, verdict=Verdict.FAIL, reasoning="bad", feedback="fix it", evidence=[])


class TestFormatJudgeFeedback:
    """Test _format_judge_feedback helper."""

    def test_formats_failed_judges(self) -> None:
        from devbench.execution.orchestrator import _format_judge_feedback

        verdicts = [
            ("code_review", _fail_result("code_review")),
            ("test_review", _pass_result("test_review")),
            ("doc_review", _fail_result("doc_review")),
        ]
        feedback = _format_judge_feedback(verdicts)
        assert "code_review FAILED" in feedback
        assert "doc_review FAILED" in feedback
        assert "test_review" not in feedback

    def test_returns_empty_when_all_pass(self) -> None:
        from devbench.execution.orchestrator import _format_judge_feedback

        verdicts = [("code_review", _pass_result("code_review"))]
        assert _format_judge_feedback(verdicts) == ""


class TestRunReviewJudges:
    """Test run_review_judges function."""

    def test_runs_all_four_judges(self, tmp_path: Path) -> None:
        from devbench.execution.orchestrator import run_review_judges

        unit = _make_unit(tmp_path)
        mock_judge = MagicMock()
        mock_judge.name = "mock"
        mock_judge.evaluate.return_value = _pass_result("mock")

        with patch(f"{_ORC}.CodeReviewJudge", return_value=mock_judge):
            with patch(f"{_ORC}.TestReviewJudge", return_value=mock_judge):
                with patch(f"{_ORC}.DocReviewJudge", return_value=mock_judge):
                    with patch(f"{_ORC}.ChangesManifestJudge", return_value=mock_judge):
                        results = run_review_judges(unit, tmp_path)

        assert len(results) == 4
        assert all(r.verdict == Verdict.PASS for _, r in results)

    def test_logs_failure_comments(self, tmp_path: Path) -> None:
        from devbench.execution.orchestrator import run_review_judges

        unit = _make_unit(tmp_path)
        mock_judge = MagicMock()
        mock_judge.name = "failing_judge"
        mock_judge.evaluate.return_value = _fail_result("failing_judge")

        with patch(f"{_ORC}.CodeReviewJudge", return_value=mock_judge):
            with patch(f"{_ORC}.TestReviewJudge", return_value=mock_judge):
                with patch(f"{_ORC}.DocReviewJudge", return_value=mock_judge):
                    with patch(f"{_ORC}.ChangesManifestJudge", return_value=mock_judge):
                        results = run_review_judges(unit, tmp_path)

        assert all(r.verdict == Verdict.FAIL for _, r in results)
        # Comments should have been logged to file
        content = unit.file_path.read_text()
        assert "REVIEW_FAIL" in content


class TestProcessWorkUnit:
    """Test process_work_unit function."""

    def test_succeeds_on_first_attempt(self, tmp_path: Path) -> None:
        from devbench.execution.orchestrator import process_work_unit

        unit = _make_unit(tmp_path)
        exec_result = ExecutionResult(status=ExecutionStatus.IN_REVIEW, output="done", blocker="")
        mock_judge = MagicMock()
        mock_judge.name = "mock"
        mock_judge.evaluate.return_value = _pass_result("mock")

        mock_git_ops = MagicMock()
        mock_git_ops.create_pr.return_value = "https://github.com/org/repo/pull/1"
        mock_git_ops.wait_for_checks.return_value = True
        mock_mgr = MagicMock()

        with patch(f"{_ORC}.REPO_LOCAL_PATHS", {"caylent-solutions/git-repo": tmp_path}):
            with patch(f"{_ORC}.claude_executor") as mock_exec:
                mock_exec.execute.return_value = exec_result
                with patch(f"{_ORC}.CodeReviewJudge", return_value=mock_judge):
                    with patch(f"{_ORC}.TestReviewJudge", return_value=mock_judge):
                        with patch(f"{_ORC}.DocReviewJudge", return_value=mock_judge):
                            with patch(f"{_ORC}.ChangesManifestJudge", return_value=mock_judge):
                                with patch(f"{_ORC}.SecurityReviewJudge", return_value=mock_judge):
                                    with patch(f"{_ORC}.GitOpsJudge", return_value=mock_git_ops):
                                        with patch(f"{_ORC}.BacklogManager", return_value=mock_mgr):
                                            with patch(f"{_ORC}.BlockerResolverJudge"):
                                                with patch(
                                                    f"{_ORC}.BACKLOG_INDEX", tmp_path / "BACKLOG.md"
                                                ):
                                                    result = process_work_unit(unit)

        assert result is True
        # Completion must go through mark_done (gated), not force_status
        mock_mgr.mark_done.assert_called_once()
        mock_mgr.force_status.assert_any_call(
            unit.file_path, tmp_path / "BACKLOG.md", unit.id, "in-progress"
        )

    def test_returns_false_after_max_retries(self, tmp_path: Path) -> None:
        from devbench.execution.orchestrator import process_work_unit

        unit = _make_unit(tmp_path)
        exec_result = ExecutionResult(status=ExecutionStatus.FAILED, output="error", blocker="")

        with patch(f"{_ORC}.REPO_LOCAL_PATHS", {"caylent-solutions/git-repo": tmp_path}):
            with patch(f"{_ORC}.claude_executor") as mock_exec:
                mock_exec.execute.return_value = exec_result
                with patch(f"{_ORC}.GitOpsJudge"):
                    with patch(f"{_ORC}.SecurityReviewJudge"):
                        with patch(f"{_ORC}.BacklogManager") as mock_mgr_cls:
                            with patch(f"{_ORC}.BlockerResolverJudge"):
                                with patch(f"{_ORC}.MAX_RETRY_ATTEMPTS", 2):
                                    with patch(f"{_ORC}.BACKLOG_INDEX", tmp_path / "BACKLOG.md"):
                                        result = process_work_unit(unit)

        assert result is False
        mock_mgr_cls.return_value.mark_blocked.assert_called_once()

    def test_handles_blocked_execution(self, tmp_path: Path) -> None:
        from devbench.execution.orchestrator import process_work_unit

        unit = _make_unit(tmp_path)
        blocked_result = ExecutionResult(status=ExecutionStatus.BLOCKED, output="", blocker="Missing API key")
        failed_result = ExecutionResult(status=ExecutionStatus.FAILED, output="still failed", blocker="")

        mock_blocker = MagicMock()
        mock_blocker.evaluate.return_value = _fail_result("blocker")

        call_count = 0

        def exec_side_effect(**kwargs):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return blocked_result
            return failed_result

        with patch(f"{_ORC}.REPO_LOCAL_PATHS", {"caylent-solutions/git-repo": tmp_path}):
            with patch(f"{_ORC}.claude_executor") as mock_exec:
                mock_exec.execute.side_effect = exec_side_effect
                with patch(f"{_ORC}.GitOpsJudge"):
                    with patch(f"{_ORC}.SecurityReviewJudge"):
                        with patch(f"{_ORC}.BacklogManager"):
                            with patch(f"{_ORC}.BlockerResolverJudge", return_value=mock_blocker):
                                with patch(f"{_ORC}.MAX_RETRY_ATTEMPTS", 2):
                                    with patch(f"{_ORC}.BACKLOG_INDEX", tmp_path / "BACKLOG.md"):
                                        result = process_work_unit(unit)

        assert result is False

    def test_raises_for_unknown_repo(self, tmp_path: Path) -> None:
        from devbench.execution.orchestrator import process_work_unit

        unit = _make_unit(tmp_path)
        unit.repo = "evil/repo"

        with pytest.raises(ValueError, match="not allowed"):
            process_work_unit(unit)

    def test_raises_for_missing_repo_path(self, tmp_path: Path) -> None:
        from devbench.execution.orchestrator import process_work_unit

        unit = _make_unit(tmp_path)

        with patch(f"{_ORC}.REPO_LOCAL_PATHS", {}):
            with pytest.raises(ValueError, match="No local path"):
                process_work_unit(unit)

    def test_handles_review_rejection_then_pass(self, tmp_path: Path) -> None:
        from devbench.execution.orchestrator import process_work_unit

        unit = _make_unit(tmp_path)

        exec_result = ExecutionResult(status=ExecutionStatus.IN_REVIEW, output="done", blocker="")

        fail_judge = MagicMock()
        fail_judge.name = "fail"
        fail_judge.evaluate.return_value = _fail_result("fail")

        pass_judge = MagicMock()
        pass_judge.name = "pass"
        pass_judge.evaluate.return_value = _pass_result("pass")

        mock_git_ops = MagicMock()
        mock_git_ops.create_pr.return_value = "https://github.com/org/repo/pull/1"
        mock_git_ops.wait_for_checks.return_value = True

        call_count = [0]

        def make_judge_factory(cls_name):
            def factory():
                call_count[0] += 1
                # First 4 calls (attempt 1) fail, next 4 (attempt 2) pass
                if call_count[0] <= 4:
                    return fail_judge
                return pass_judge

            return factory

        with patch(f"{_ORC}.REPO_LOCAL_PATHS", {"caylent-solutions/git-repo": tmp_path}):
            with patch(f"{_ORC}.claude_executor") as mock_exec:
                mock_exec.execute.return_value = exec_result
                with patch(f"{_ORC}.CodeReviewJudge", side_effect=make_judge_factory("code")):
                    with patch(f"{_ORC}.TestReviewJudge", side_effect=make_judge_factory("test")):
                        with patch(f"{_ORC}.DocReviewJudge", side_effect=make_judge_factory("doc")):
                            with patch(
                                f"{_ORC}.ChangesManifestJudge", side_effect=make_judge_factory("manifest")
                            ):
                                with patch(f"{_ORC}.SecurityReviewJudge", return_value=pass_judge):
                                    with patch(f"{_ORC}.GitOpsJudge", return_value=mock_git_ops):
                                        with patch(f"{_ORC}.BacklogManager"):
                                            with patch(f"{_ORC}.BlockerResolverJudge"):
                                                with patch(
                                                    f"{_ORC}.BACKLOG_INDEX", tmp_path / "BACKLOG.md"
                                                ):
                                                    result = process_work_unit(unit)

        assert result is True

    def test_handles_security_review_failure(self, tmp_path: Path) -> None:
        from devbench.execution.orchestrator import process_work_unit

        unit = _make_unit(tmp_path)
        exec_result = ExecutionResult(status=ExecutionStatus.IN_REVIEW, output="done", blocker="")
        mock_review_judge = MagicMock()
        mock_review_judge.name = "review"
        mock_review_judge.evaluate.return_value = _pass_result("review")

        mock_security = MagicMock()
        mock_security.evaluate.return_value = _fail_result("security")

        with patch(f"{_ORC}.REPO_LOCAL_PATHS", {"caylent-solutions/git-repo": tmp_path}):
            with patch(f"{_ORC}.claude_executor") as mock_exec:
                mock_exec.execute.return_value = exec_result
                with patch(f"{_ORC}.CodeReviewJudge", return_value=mock_review_judge):
                    with patch(f"{_ORC}.TestReviewJudge", return_value=mock_review_judge):
                        with patch(f"{_ORC}.DocReviewJudge", return_value=mock_review_judge):
                            with patch(f"{_ORC}.ChangesManifestJudge", return_value=mock_review_judge):
                                with patch(f"{_ORC}.SecurityReviewJudge", return_value=mock_security):
                                    with patch(f"{_ORC}.GitOpsJudge"):
                                        with patch(f"{_ORC}.BacklogManager"):
                                            with patch(f"{_ORC}.BlockerResolverJudge"):
                                                with patch(f"{_ORC}.MAX_RETRY_ATTEMPTS", 1):
                                                    with patch(
                                                        f"{_ORC}.BACKLOG_INDEX", tmp_path / "BACKLOG.md"
                                                    ):
                                                        result = process_work_unit(unit)

        assert result is False

    def test_security_failure_writes_review_rejected_to_reset_done_gate(self, tmp_path: Path) -> None:
        """After security fails, REVIEW_REJECTED must be written so mark_done cannot pass
        on the stale [REVIEW_PASS] entries from the pre-security round."""
        from devbench.execution.orchestrator import process_work_unit

        unit = _make_unit(tmp_path)
        exec_result = ExecutionResult(status=ExecutionStatus.IN_REVIEW, output="done", blocker="")

        mock_review_judge = MagicMock()
        mock_review_judge.name = "review"
        mock_review_judge.evaluate.return_value = _pass_result("review")

        mock_security = MagicMock()
        mock_security.evaluate.return_value = _fail_result("security_review")

        with patch(f"{_ORC}.REPO_LOCAL_PATHS", {"caylent-solutions/git-repo": tmp_path}):
            with patch(f"{_ORC}.claude_executor") as mock_exec:
                mock_exec.execute.return_value = exec_result
                with patch(f"{_ORC}.CodeReviewJudge", return_value=mock_review_judge):
                    with patch(f"{_ORC}.TestReviewJudge", return_value=mock_review_judge):
                        with patch(f"{_ORC}.DocReviewJudge", return_value=mock_review_judge):
                            with patch(f"{_ORC}.ChangesManifestJudge", return_value=mock_review_judge):
                                with patch(f"{_ORC}.SecurityReviewJudge", return_value=mock_security):
                                    with patch(f"{_ORC}.GitOpsJudge"):
                                        with patch(f"{_ORC}.BacklogManager"):
                                            with patch(f"{_ORC}.BlockerResolverJudge"):
                                                with patch(f"{_ORC}.MAX_RETRY_ATTEMPTS", 1):
                                                    with patch(f"{_ORC}.BACKLOG_INDEX", tmp_path / "BACKLOG.md"):
                                                        process_work_unit(unit)

        content = unit.file_path.read_text(encoding="utf-8")
        # SECURITY_FAIL must be present
        assert "[SECURITY_FAIL]" in content
        # REVIEW_REJECTED must follow to reset the done-gate window
        assert "[REVIEW_REJECTED]" in content
        # REVIEW_REJECTED must appear after SECURITY_FAIL in the file
        assert content.index("[REVIEW_REJECTED]") > content.index("[SECURITY_FAIL]")

    def test_handles_git_error(self, tmp_path: Path) -> None:
        from devbench.execution.orchestrator import process_work_unit

        unit = _make_unit(tmp_path)
        exec_result = ExecutionResult(status=ExecutionStatus.IN_REVIEW, output="done", blocker="")
        mock_judge = MagicMock()
        mock_judge.name = "mock"
        mock_judge.evaluate.return_value = _pass_result("mock")

        mock_git_ops = MagicMock()
        mock_git_ops.commit_and_push.side_effect = RuntimeError("push failed")

        with patch(f"{_ORC}.REPO_LOCAL_PATHS", {"caylent-solutions/git-repo": tmp_path}):
            with patch(f"{_ORC}.claude_executor") as mock_exec:
                mock_exec.execute.return_value = exec_result
                with patch(f"{_ORC}.CodeReviewJudge", return_value=mock_judge):
                    with patch(f"{_ORC}.TestReviewJudge", return_value=mock_judge):
                        with patch(f"{_ORC}.DocReviewJudge", return_value=mock_judge):
                            with patch(f"{_ORC}.ChangesManifestJudge", return_value=mock_judge):
                                with patch(f"{_ORC}.SecurityReviewJudge", return_value=mock_judge):
                                    with patch(f"{_ORC}.GitOpsJudge", return_value=mock_git_ops):
                                        with patch(f"{_ORC}.BacklogManager"):
                                            with patch(f"{_ORC}.BlockerResolverJudge"):
                                                with patch(f"{_ORC}.MAX_RETRY_ATTEMPTS", 1):
                                                    with patch(
                                                        f"{_ORC}.BACKLOG_INDEX", tmp_path / "BACKLOG.md"
                                                    ):
                                                        result = process_work_unit(unit)

        assert result is False

    def test_handles_checks_failure(self, tmp_path: Path) -> None:
        from devbench.execution.orchestrator import process_work_unit

        unit = _make_unit(tmp_path)
        exec_result = ExecutionResult(status=ExecutionStatus.IN_REVIEW, output="done", blocker="")
        mock_judge = MagicMock()
        mock_judge.name = "mock"
        mock_judge.evaluate.return_value = _pass_result("mock")

        mock_git_ops = MagicMock()
        mock_git_ops.create_pr.return_value = "https://github.com/org/repo/pull/1"
        mock_git_ops.wait_for_checks.return_value = False

        with patch(f"{_ORC}.REPO_LOCAL_PATHS", {"caylent-solutions/git-repo": tmp_path}):
            with patch(f"{_ORC}.claude_executor") as mock_exec:
                mock_exec.execute.return_value = exec_result
                with patch(f"{_ORC}.CodeReviewJudge", return_value=mock_judge):
                    with patch(f"{_ORC}.TestReviewJudge", return_value=mock_judge):
                        with patch(f"{_ORC}.DocReviewJudge", return_value=mock_judge):
                            with patch(f"{_ORC}.ChangesManifestJudge", return_value=mock_judge):
                                with patch(f"{_ORC}.SecurityReviewJudge", return_value=mock_judge):
                                    with patch(f"{_ORC}.GitOpsJudge", return_value=mock_git_ops):
                                        with patch(f"{_ORC}.BacklogManager"):
                                            with patch(f"{_ORC}.BlockerResolverJudge"):
                                                with patch(f"{_ORC}.MAX_RETRY_ATTEMPTS", 1):
                                                    with patch(
                                                        f"{_ORC}.BACKLOG_INDEX", tmp_path / "BACKLOG.md"
                                                    ):
                                                        result = process_work_unit(unit)

        assert result is False


class TestFeedbackPropagation:
    """Test that feedback is set correctly on each retry path."""

    def test_feedback_from_failed_judge_is_in_next_execute_call(self, tmp_path: Path) -> None:
        """After review rejection, the feedback is passed to the next execute call."""
        from devbench.execution.orchestrator import process_work_unit

        unit = _make_unit(tmp_path)
        exec_result = ExecutionResult(status=ExecutionStatus.IN_REVIEW, output="done", blocker="")

        fail_judge = MagicMock()
        fail_judge.name = "code_review"
        fail_judge.evaluate.return_value = _fail_result("code_review")

        pass_judge = MagicMock()
        pass_judge.name = "pass"
        pass_judge.evaluate.return_value = _pass_result("pass")

        execute_calls: list[dict] = []

        def capture_execute(**kwargs):
            execute_calls.append(dict(kwargs))
            return exec_result

        mock_git_ops = MagicMock()
        mock_git_ops.create_pr.return_value = "https://github.com/org/repo/pull/1"
        mock_git_ops.wait_for_checks.return_value = True

        attempt = [0]

        def make_judge():
            attempt[0] += 1
            # First 4 judges (attempt 1) fail, next 4 (attempt 2) pass
            if attempt[0] <= 4:
                return fail_judge
            return pass_judge

        with patch(f"{_ORC}.REPO_LOCAL_PATHS", {"caylent-solutions/git-repo": tmp_path}):
            with patch(f"{_ORC}.claude_executor") as mock_exec:
                mock_exec.execute.side_effect = capture_execute
                with patch(f"{_ORC}.CodeReviewJudge", side_effect=make_judge):
                    with patch(f"{_ORC}.TestReviewJudge", side_effect=make_judge):
                        with patch(f"{_ORC}.DocReviewJudge", side_effect=make_judge):
                            with patch(f"{_ORC}.ChangesManifestJudge", side_effect=make_judge):
                                with patch(f"{_ORC}.SecurityReviewJudge", return_value=pass_judge):
                                    with patch(f"{_ORC}.GitOpsJudge", return_value=mock_git_ops):
                                        with patch(f"{_ORC}.BacklogManager"):
                                            with patch(f"{_ORC}.BlockerResolverJudge"):
                                                with patch(f"{_ORC}.BACKLOG_INDEX", tmp_path / "BACKLOG.md"):
                                                    process_work_unit(unit)

        assert len(execute_calls) >= 2
        # Second call must have non-empty feedback from the failed judge
        assert execute_calls[1]["feedback"] != ""
        assert "code_review" in execute_calls[1]["feedback"].lower()

    def test_feedback_set_on_blocked_retry_path(self, tmp_path: Path) -> None:
        """After BLOCKED status, feedback is set from blocker resolver's reasoning."""
        from devbench.execution.orchestrator import process_work_unit

        unit = _make_unit(tmp_path)
        blocked_result = ExecutionResult(status=ExecutionStatus.BLOCKED, output="", blocker="API key missing")
        failed_result = ExecutionResult(status=ExecutionStatus.FAILED, output="still failed", blocker="")

        mock_blocker = MagicMock()
        mock_blocker.evaluate.return_value = JudgeResult(
            judge_name="blocker",
            verdict=Verdict.PASS,
            reasoning="Resolved: use env var",
            feedback="",
            evidence=[],
        )

        execute_calls: list[dict] = []
        call_count = [0]

        def capture_execute(**kwargs):
            execute_calls.append(dict(kwargs))
            call_count[0] += 1
            if call_count[0] == 1:
                return blocked_result
            return failed_result

        with patch(f"{_ORC}.REPO_LOCAL_PATHS", {"caylent-solutions/git-repo": tmp_path}):
            with patch(f"{_ORC}.claude_executor") as mock_exec:
                mock_exec.execute.side_effect = capture_execute
                with patch(f"{_ORC}.GitOpsJudge"):
                    with patch(f"{_ORC}.SecurityReviewJudge"):
                        with patch(f"{_ORC}.BacklogManager"):
                            with patch(f"{_ORC}.BlockerResolverJudge", return_value=mock_blocker):
                                with patch(f"{_ORC}.MAX_RETRY_ATTEMPTS", 2):
                                    with patch(f"{_ORC}.BACKLOG_INDEX", tmp_path / "BACKLOG.md"):
                                        process_work_unit(unit)

        assert len(execute_calls) >= 2
        # Second call must include feedback from the blocker resolver
        assert execute_calls[1]["feedback"] != ""
        assert "resolved" in execute_calls[1]["feedback"].lower()


class TestMain:
    """Test orchestrator main loop."""

    def test_all_done_immediately(self) -> None:
        from devbench.execution.orchestrator import main

        mock_parser = MagicMock()
        mock_parser.parse_index.return_value = []
        mock_parser.find_next_actionable.return_value = None
        mock_parser.all_done.return_value = True

        mock_mgr = MagicMock()
        mock_mgr.validate.return_value = []

        with patch(f"{_ORC}.setup_all_repos", return_value={}):
            with patch(f"{_ORC}.BacklogParser", return_value=mock_parser):
                with patch(f"{_ORC}.BacklogManager", return_value=mock_mgr):
                    main()

    def test_preflight_validation_aborts_on_errors(self) -> None:
        """Orchestrator exits early if backlog validation fails."""
        from devbench.execution.orchestrator import main

        mock_mgr = MagicMock()
        mock_mgr.validate.return_value = ["E0-T1: work unit file missing"]

        with patch(f"{_ORC}.setup_all_repos", return_value={}):
            with patch(f"{_ORC}.BacklogManager", return_value=mock_mgr):
                with patch(f"{_ORC}.BacklogParser") as mock_parser_cls:
                    main()
                    # BacklogParser.parse_index should never be called — we aborted early
                    mock_parser_cls.return_value.parse_index.assert_not_called()

    def test_deadlock_detection(self) -> None:
        from devbench.execution.orchestrator import main

        mock_parser = MagicMock()
        mock_parser.parse_index.return_value = []
        mock_parser.find_next_actionable.return_value = None
        mock_parser.all_done.return_value = False
        mock_parser.get_blocked_units.return_value = []

        mock_mgr = MagicMock()
        mock_mgr.validate.return_value = []

        with patch(f"{_ORC}.setup_all_repos", return_value={}):
            with patch(f"{_ORC}.BacklogParser", return_value=mock_parser):
                with patch(f"{_ORC}.BacklogManager", return_value=mock_mgr):
                    main()

    def test_processes_one_unit_then_done(self, tmp_path: Path) -> None:
        from devbench.execution.orchestrator import main

        unit = _make_unit(tmp_path)
        mock_parser = MagicMock()
        call_count = [0]

        def find_next(units):
            call_count[0] += 1
            if call_count[0] == 1:
                return unit
            return None

        mock_parser.parse_index.return_value = [unit]
        mock_parser.find_next_actionable.side_effect = find_next
        mock_parser.all_done.return_value = True

        mock_mgr = MagicMock()
        mock_mgr.validate.return_value = []

        with patch(f"{_ORC}.setup_all_repos", return_value={}):
            with patch(f"{_ORC}.BacklogParser", return_value=mock_parser):
                with patch(f"{_ORC}.BacklogManager", return_value=mock_mgr):
                    with patch(f"{_ORC}.process_work_unit", return_value=True):
                        main()

    def test_blocked_units_logged(self) -> None:
        from devbench.execution.orchestrator import main

        mock_parser = MagicMock()
        mock_parser.parse_index.return_value = []
        mock_parser.find_next_actionable.return_value = None
        mock_parser.all_done.return_value = False
        blocked = MagicMock()
        blocked.id = "T1"
        mock_parser.get_blocked_units.return_value = [blocked]

        mock_mgr = MagicMock()
        mock_mgr.validate.return_value = []

        with patch(f"{_ORC}.setup_all_repos", return_value={}):
            with patch(f"{_ORC}.BacklogParser", return_value=mock_parser):
                with patch(f"{_ORC}.BacklogManager", return_value=mock_mgr):
                    main()
