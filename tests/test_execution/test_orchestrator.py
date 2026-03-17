"""Tests for judges.orchestrator module."""

from __future__ import annotations

from collections.abc import Generator
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from devbench.backlog.work_unit import WorkUnit, WorkUnitStatus, WorkUnitType
from devbench.config_loader import RepoConfig
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
        branch=f"backlog/{unit_id.lower()}",
        dependencies=[],
        description="Test description",
    )


def _make_repo_config(local_path: Path) -> RepoConfig:
    """Factory for a test RepoConfig with the given local_path."""
    return RepoConfig(
        name="caylent-solutions/git-repo",
        short_name="git-repo",
        local_path=local_path,
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
                        results = run_review_judges(unit, tmp_path, unit.repo)

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
                        results = run_review_judges(unit, tmp_path, unit.repo)

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

        with patch(f"{_ORC}.resolve_repo", return_value=_make_repo_config(tmp_path)):
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

        mock_git_ops = MagicMock()
        mock_git_ops.is_committed_and_pushed.return_value = False

        with patch(f"{_ORC}.resolve_repo", return_value=_make_repo_config(tmp_path)):
            with patch(f"{_ORC}.claude_executor") as mock_exec:
                mock_exec.execute.return_value = exec_result
                with patch(f"{_ORC}.GitOpsJudge", return_value=mock_git_ops):
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

        mock_git_ops = MagicMock()
        mock_git_ops.is_committed_and_pushed.return_value = False

        call_count = 0

        def exec_side_effect(**kwargs):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return blocked_result
            return failed_result

        with patch(f"{_ORC}.resolve_repo", return_value=_make_repo_config(tmp_path)):
            with patch(f"{_ORC}.claude_executor") as mock_exec:
                mock_exec.execute.side_effect = exec_side_effect
                with patch(f"{_ORC}.GitOpsJudge", return_value=mock_git_ops):
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

        with pytest.raises(ValueError, match=r"not allowed|not recognised"):
            process_work_unit(unit)

    def test_canonicalizes_short_repo_name(self, tmp_path: Path) -> None:
        """AC-6: short repo name is resolved to full name before downstream calls."""
        from devbench.execution.orchestrator import process_work_unit

        unit = _make_unit(tmp_path)
        unit.repo = "git-repo"  # short name, not "caylent-solutions/git-repo"

        exec_result = ExecutionResult(status=ExecutionStatus.IN_REVIEW, output="done", blocker="")
        mock_judge = MagicMock()
        mock_judge.name = "mock"
        mock_judge.evaluate.return_value = _pass_result("mock")
        mock_git_ops = MagicMock()
        mock_git_ops.create_pr.return_value = "https://github.com/org/repo/pull/1"
        mock_git_ops.wait_for_checks.return_value = True
        mock_mgr = MagicMock()

        with patch(f"{_ORC}.resolve_repo", return_value=_make_repo_config(tmp_path)):
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
                                                with patch(f"{_ORC}.BACKLOG_INDEX", tmp_path / "BACKLOG.md"):
                                                    result = process_work_unit(unit)

        assert result is True
        # All git ops called with a RepoConfig whose name is the canonical full repo name
        mock_git_ops.commit_and_push.assert_called_once()
        call_kwargs = mock_git_ops.commit_and_push.call_args
        repo_config_arg = call_kwargs.kwargs.get("repo_config")
        assert repo_config_arg is not None
        assert repo_config_arg.name == "caylent-solutions/git-repo"

    def test_commit_and_push_uses_spec_branch(self, tmp_path: Path) -> None:
        """commit_and_push receives the branch from the work unit spec, not the template."""
        from devbench.execution.orchestrator import process_work_unit

        unit = _make_unit(tmp_path)
        unit.branch = "feature/my-custom-branch"

        exec_result = ExecutionResult(status=ExecutionStatus.IN_REVIEW, output="done", blocker="")
        mock_judge = MagicMock()
        mock_judge.name = "mock"
        mock_judge.evaluate.return_value = _pass_result("mock")
        mock_git_ops = MagicMock()
        mock_git_ops.create_pr.return_value = "https://github.com/org/repo/pull/42"
        mock_git_ops.wait_for_checks.return_value = True
        mock_mgr = MagicMock()

        with patch(f"{_ORC}.resolve_repo", return_value=_make_repo_config(tmp_path)):
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
        call_kwargs = mock_git_ops.commit_and_push.call_args
        assert call_kwargs.kwargs.get("branch") == "feature/my-custom-branch"

    def test_commit_and_push_uses_template_branch_from_parser(self, tmp_path: Path) -> None:
        """When spec has no Branch field, parser populates branch via template; orchestrator uses it."""
        from devbench.execution.orchestrator import process_work_unit

        unit = _make_unit(tmp_path, unit_id="E0-F1-S1-T1")
        unit.branch = "backlog/e0-f1-s1-t1"  # as populated by BacklogParser template fallback

        exec_result = ExecutionResult(status=ExecutionStatus.IN_REVIEW, output="done", blocker="")
        mock_judge = MagicMock()
        mock_judge.name = "mock"
        mock_judge.evaluate.return_value = _pass_result("mock")
        mock_git_ops = MagicMock()
        mock_git_ops.create_pr.return_value = "https://github.com/org/repo/pull/7"
        mock_git_ops.wait_for_checks.return_value = True
        mock_mgr = MagicMock()

        with patch(f"{_ORC}.resolve_repo", return_value=_make_repo_config(tmp_path)):
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
        call_kwargs = mock_git_ops.commit_and_push.call_args
        assert call_kwargs.kwargs.get("branch") == "backlog/e0-f1-s1-t1"

    def test_raises_for_missing_repo_path(self, tmp_path: Path) -> None:
        from devbench.execution.orchestrator import process_work_unit

        unit = _make_unit(tmp_path)

        with patch(f"{_ORC}.resolve_repo", side_effect=ValueError("not recognised")):
            with pytest.raises(ValueError, match="not recognised"):
                process_work_unit(unit)

    def test_raises_when_branch_is_empty(self, tmp_path: Path) -> None:
        """process_work_unit must fail fast with a clear error when branch is empty."""
        from devbench.execution.orchestrator import process_work_unit

        unit = _make_unit(tmp_path)
        unit.branch = ""  # simulate a WorkUnit not populated by BacklogParser

        with patch(f"{_ORC}.resolve_repo", return_value=_make_repo_config(tmp_path)):
            with patch(f"{_ORC}.GitOpsJudge"):
                with patch(f"{_ORC}.BacklogManager"):
                    with patch(f"{_ORC}.BlockerResolverJudge"):
                        with patch(f"{_ORC}.BACKLOG_INDEX", tmp_path / "BACKLOG.md"):
                            with pytest.raises(ValueError, match="no branch set"):
                                process_work_unit(unit)

    def test_orchestrator_calls_ensure_branch_before_execute(self, tmp_path: Path) -> None:
        """AC-1: ensure_branch is called before claude_executor.execute() on every attempt."""
        from devbench.execution.orchestrator import process_work_unit

        unit = _make_unit(tmp_path)
        exec_result = ExecutionResult(status=ExecutionStatus.IN_REVIEW, output="done", blocker="")
        mock_judge = MagicMock()
        mock_judge.name = "mock"
        mock_judge.evaluate.return_value = _pass_result("mock")

        call_order: list[str] = []

        mock_git_ops = MagicMock()
        mock_git_ops.is_committed_and_pushed.return_value = False
        mock_git_ops.create_pr.return_value = "https://github.com/org/repo/pull/1"
        mock_git_ops.wait_for_checks.return_value = True

        def ensure_branch_side_effect(**kwargs):
            call_order.append("ensure_branch")

        mock_git_ops.ensure_branch.side_effect = ensure_branch_side_effect

        def execute_side_effect(**kwargs):
            call_order.append("execute")
            return exec_result

        mock_mgr = MagicMock()

        with patch(f"{_ORC}.resolve_repo", return_value=_make_repo_config(tmp_path)):
            with patch(f"{_ORC}.claude_executor") as mock_exec:
                mock_exec.execute.side_effect = execute_side_effect
                with patch(f"{_ORC}.CodeReviewJudge", return_value=mock_judge):
                    with patch(f"{_ORC}.TestReviewJudge", return_value=mock_judge):
                        with patch(f"{_ORC}.DocReviewJudge", return_value=mock_judge):
                            with patch(f"{_ORC}.ChangesManifestJudge", return_value=mock_judge):
                                with patch(f"{_ORC}.SecurityReviewJudge", return_value=mock_judge):
                                    with patch(f"{_ORC}.GitOpsJudge", return_value=mock_git_ops):
                                        with patch(f"{_ORC}.BacklogManager", return_value=mock_mgr):
                                            with patch(f"{_ORC}.BlockerResolverJudge"):
                                                with patch(f"{_ORC}.BACKLOG_INDEX", tmp_path / "BACKLOG.md"):
                                                    process_work_unit(unit)

        ensure_idx = call_order.index("ensure_branch")
        execute_idx = call_order.index("execute")
        assert ensure_idx < execute_idx, "ensure_branch must be called before execute"

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

        with patch(f"{_ORC}.resolve_repo", return_value=_make_repo_config(tmp_path)):
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

    def test_security_judge_called_with_repo_kwarg(self, tmp_path: Path) -> None:
        """AC-3: security_judge.evaluate() receives repo=work_unit.repo as a kwarg."""
        from devbench.execution.orchestrator import process_work_unit

        unit = _make_unit(tmp_path)
        exec_result = ExecutionResult(status=ExecutionStatus.IN_REVIEW, output="done", blocker="")
        mock_review_judge = MagicMock()
        mock_review_judge.name = "mock"
        mock_review_judge.evaluate.return_value = _pass_result("mock")

        mock_security = MagicMock()
        mock_security.evaluate.return_value = _pass_result("security")

        mock_git_ops = MagicMock()
        mock_git_ops.is_committed_and_pushed.return_value = False
        mock_git_ops.create_pr.return_value = "https://github.com/org/repo/pull/1"
        mock_git_ops.wait_for_checks.return_value = True

        with patch(f"{_ORC}.resolve_repo", return_value=_make_repo_config(tmp_path)):
            with patch(f"{_ORC}.claude_executor") as mock_exec:
                mock_exec.execute.return_value = exec_result
                with patch(f"{_ORC}.CodeReviewJudge", return_value=mock_review_judge):
                    with patch(f"{_ORC}.TestReviewJudge", return_value=mock_review_judge):
                        with patch(f"{_ORC}.DocReviewJudge", return_value=mock_review_judge):
                            with patch(f"{_ORC}.ChangesManifestJudge", return_value=mock_review_judge):
                                with patch(f"{_ORC}.SecurityReviewJudge", return_value=mock_security):
                                    with patch(f"{_ORC}.GitOpsJudge", return_value=mock_git_ops):
                                        with patch(f"{_ORC}.BacklogManager"):
                                            with patch(f"{_ORC}.BlockerResolverJudge"):
                                                with patch(f"{_ORC}.BACKLOG_INDEX", tmp_path / "BACKLOG.md"):
                                                    process_work_unit(unit)

        mock_security.evaluate.assert_called_once_with(
            work_unit_path=unit.file_path,
            repo_path=tmp_path,
            repo="caylent-solutions/git-repo",
        )

    def test_handles_security_review_failure(self, tmp_path: Path) -> None:
        from devbench.execution.orchestrator import process_work_unit

        unit = _make_unit(tmp_path)
        exec_result = ExecutionResult(status=ExecutionStatus.IN_REVIEW, output="done", blocker="")
        mock_review_judge = MagicMock()
        mock_review_judge.name = "review"
        mock_review_judge.evaluate.return_value = _pass_result("review")

        mock_security = MagicMock()
        mock_security.evaluate.return_value = _fail_result("security")

        mock_git_ops = MagicMock()
        mock_git_ops.is_committed_and_pushed.return_value = False

        with patch(f"{_ORC}.resolve_repo", return_value=_make_repo_config(tmp_path)):
            with patch(f"{_ORC}.claude_executor") as mock_exec:
                mock_exec.execute.return_value = exec_result
                with patch(f"{_ORC}.CodeReviewJudge", return_value=mock_review_judge):
                    with patch(f"{_ORC}.TestReviewJudge", return_value=mock_review_judge):
                        with patch(f"{_ORC}.DocReviewJudge", return_value=mock_review_judge):
                            with patch(f"{_ORC}.ChangesManifestJudge", return_value=mock_review_judge):
                                with patch(f"{_ORC}.SecurityReviewJudge", return_value=mock_security):
                                    with patch(f"{_ORC}.GitOpsJudge", return_value=mock_git_ops):
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

        mock_git_ops = MagicMock()
        mock_git_ops.is_committed_and_pushed.return_value = False

        with patch(f"{_ORC}.resolve_repo", return_value=_make_repo_config(tmp_path)):
            with patch(f"{_ORC}.claude_executor") as mock_exec:
                mock_exec.execute.return_value = exec_result
                with patch(f"{_ORC}.CodeReviewJudge", return_value=mock_review_judge):
                    with patch(f"{_ORC}.TestReviewJudge", return_value=mock_review_judge):
                        with patch(f"{_ORC}.DocReviewJudge", return_value=mock_review_judge):
                            with patch(f"{_ORC}.ChangesManifestJudge", return_value=mock_review_judge):
                                with patch(f"{_ORC}.SecurityReviewJudge", return_value=mock_security):
                                    with patch(f"{_ORC}.GitOpsJudge", return_value=mock_git_ops):
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

        with patch(f"{_ORC}.resolve_repo", return_value=_make_repo_config(tmp_path)):
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

        with patch(f"{_ORC}.resolve_repo", return_value=_make_repo_config(tmp_path)):
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

    def test_orchestrator_retries_when_checks_failed(self, tmp_path: Path) -> None:
        """AC-4: when wait_for_checks returns False, orchestrator logs CHECKS_FAILED and retries."""
        from devbench.execution.orchestrator import process_work_unit

        unit = _make_unit(tmp_path)
        exec_result = ExecutionResult(status=ExecutionStatus.IN_REVIEW, output="done", blocker="")
        mock_judge = MagicMock()
        mock_judge.name = "mock"
        mock_judge.evaluate.return_value = _pass_result("mock")

        mock_git_ops = MagicMock()
        mock_git_ops.is_committed_and_pushed.return_value = False
        mock_git_ops.create_pr.return_value = "https://github.com/org/repo/pull/1"
        mock_git_ops.wait_for_checks.return_value = False

        execute_calls: list[dict] = []

        def capture_execute(**kwargs):
            execute_calls.append(dict(kwargs))
            return exec_result

        with patch(f"{_ORC}.resolve_repo", return_value=_make_repo_config(tmp_path)):
            with patch(f"{_ORC}.claude_executor") as mock_exec:
                mock_exec.execute.side_effect = capture_execute
                with patch(f"{_ORC}.CodeReviewJudge", return_value=mock_judge):
                    with patch(f"{_ORC}.TestReviewJudge", return_value=mock_judge):
                        with patch(f"{_ORC}.DocReviewJudge", return_value=mock_judge):
                            with patch(f"{_ORC}.ChangesManifestJudge", return_value=mock_judge):
                                with patch(f"{_ORC}.SecurityReviewJudge", return_value=mock_judge):
                                    with patch(f"{_ORC}.GitOpsJudge", return_value=mock_git_ops):
                                        with patch(f"{_ORC}.BacklogManager"):
                                            with patch(f"{_ORC}.BlockerResolverJudge"):
                                                with patch(f"{_ORC}.MAX_RETRY_ATTEMPTS", 2):
                                                    with patch(f"{_ORC}.BACKLOG_INDEX", tmp_path / "BACKLOG.md"):
                                                        process_work_unit(unit)

        # CHECKS_FAILED must be written to the work unit file
        content = unit.file_path.read_text(encoding="utf-8")
        assert "CHECKS_FAILED" in content
        # Second attempt must receive non-empty CI failure feedback
        assert len(execute_calls) >= 2
        assert execute_calls[1]["feedback"] != ""
        assert "CI" in execute_calls[1]["feedback"] or "checks" in execute_calls[1]["feedback"].lower()

    def test_orchestrator_merges_when_checks_pass(self, tmp_path: Path) -> None:
        """AC-5: when wait_for_checks returns True, orchestrator proceeds to merge."""
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

        with patch(f"{_ORC}.resolve_repo", return_value=_make_repo_config(tmp_path)):
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
                                                with patch(f"{_ORC}.BACKLOG_INDEX", tmp_path / "BACKLOG.md"):
                                                    result = process_work_unit(unit)

        assert result is True
        mock_git_ops.merge_pr.assert_called_once()

    def test_orchestrator_skips_submodule_update_when_flag_false(self, tmp_path: Path) -> None:
        """AC-1: update_parent_submodule_ref is never called when UPDATE_SUBMODULE is False."""
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

        with patch(f"{_ORC}.resolve_repo", return_value=_make_repo_config(tmp_path)):
            with patch(f"{_ORC}.UPDATE_SUBMODULE", False):
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
                                                    with patch(f"{_ORC}.BACKLOG_INDEX", tmp_path / "BACKLOG.md"):
                                                        result = process_work_unit(unit)

        assert result is True
        mock_git_ops.update_parent_submodule_ref.assert_not_called()

    def test_orchestrator_calls_submodule_update_when_flag_true(self, tmp_path: Path) -> None:
        """AC-2: update_parent_submodule_ref is called after merge when UPDATE_SUBMODULE is True."""
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

        with patch(f"{_ORC}.resolve_repo", return_value=_make_repo_config(tmp_path)):
            with patch(f"{_ORC}.UPDATE_SUBMODULE", True):
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
                                                    with patch(f"{_ORC}.BACKLOG_INDEX", tmp_path / "BACKLOG.md"):
                                                        result = process_work_unit(unit)

        assert result is True
        mock_git_ops.update_parent_submodule_ref.assert_called_once()


class TestSkipReviewWhenCommittedAndPushed:
    """Tests for the orchestrator's is_committed_and_pushed skip logic (AC-5, AC-6)."""

    def test_orchestrator_skips_review_when_committed_and_pushed(self, tmp_path: Path) -> None:
        """AC-5: Orchestrator skips executor and run_review_judges when is_committed_and_pushed returns True.

        Given: is_committed_and_pushed returns True (branch already committed and pushed)
        When: process_work_unit runs
        Then: claude_executor.execute and run_review_judges are NOT called; git ops are called
        """
        from devbench.execution.orchestrator import process_work_unit

        unit = _make_unit(tmp_path)

        mock_git_ops = MagicMock()
        mock_git_ops.is_committed_and_pushed.return_value = True
        mock_git_ops.create_pr.return_value = "https://github.com/org/repo/pull/1"
        mock_git_ops.wait_for_checks.return_value = True
        mock_mgr = MagicMock()

        mock_judge = MagicMock()
        mock_judge.name = "security"
        mock_judge.evaluate.return_value = _pass_result("security")

        with patch(f"{_ORC}.resolve_repo", return_value=_make_repo_config(tmp_path)):
            with patch(f"{_ORC}.claude_executor") as mock_exec:
                with patch(f"{_ORC}.SecurityReviewJudge", return_value=mock_judge):
                    with patch(f"{_ORC}.GitOpsJudge", return_value=mock_git_ops):
                        with patch(f"{_ORC}.BacklogManager", return_value=mock_mgr):
                            with patch(f"{_ORC}.BlockerResolverJudge"):
                                with patch(f"{_ORC}.BACKLOG_INDEX", tmp_path / "BACKLOG.md"):
                                    with patch(f"{_ORC}.run_review_judges") as mock_run_review:
                                        result = process_work_unit(unit)

        assert result is True
        mock_exec.execute.assert_not_called()
        mock_run_review.assert_not_called()
        mock_git_ops.commit_and_push.assert_called_once()

    def test_orchestrator_runs_review_when_not_committed_and_pushed(self, tmp_path: Path) -> None:
        """AC-6: Orchestrator calls executor and run_review_judges when is_committed_and_pushed returns False.

        Given: is_committed_and_pushed returns False (normal case — work not yet done)
        When: process_work_unit runs
        Then: claude_executor.execute and run_review_judges ARE called normally
        """
        from devbench.execution.orchestrator import process_work_unit

        unit = _make_unit(tmp_path)
        exec_result = ExecutionResult(status=ExecutionStatus.IN_REVIEW, output="done", blocker="")

        mock_git_ops = MagicMock()
        mock_git_ops.is_committed_and_pushed.return_value = False
        mock_git_ops.create_pr.return_value = "https://github.com/org/repo/pull/1"
        mock_git_ops.wait_for_checks.return_value = True
        mock_mgr = MagicMock()

        mock_judge = MagicMock()
        mock_judge.name = "mock"
        mock_judge.evaluate.return_value = _pass_result("mock")

        with patch(f"{_ORC}.resolve_repo", return_value=_make_repo_config(tmp_path)):
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
                                                with patch(f"{_ORC}.BACKLOG_INDEX", tmp_path / "BACKLOG.md"):
                                                    result = process_work_unit(unit)

        assert result is True
        mock_exec.execute.assert_called_once()
        mock_git_ops.commit_and_push.assert_called_once()


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
        mock_git_ops.is_committed_and_pushed.return_value = False
        mock_git_ops.create_pr.return_value = "https://github.com/org/repo/pull/1"
        mock_git_ops.wait_for_checks.return_value = True

        attempt = [0]

        def make_judge():
            attempt[0] += 1
            # First 4 judges (attempt 1) fail, next 4 (attempt 2) pass
            if attempt[0] <= 4:
                return fail_judge
            return pass_judge

        with patch(f"{_ORC}.resolve_repo", return_value=_make_repo_config(tmp_path)):
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

        mock_git_ops = MagicMock()
        mock_git_ops.is_committed_and_pushed.return_value = False

        execute_calls: list[dict] = []
        call_count = [0]

        def capture_execute(**kwargs):
            execute_calls.append(dict(kwargs))
            call_count[0] += 1
            if call_count[0] == 1:
                return blocked_result
            return failed_result

        with patch(f"{_ORC}.resolve_repo", return_value=_make_repo_config(tmp_path)):
            with patch(f"{_ORC}.claude_executor") as mock_exec:
                mock_exec.execute.side_effect = capture_execute
                with patch(f"{_ORC}.GitOpsJudge", return_value=mock_git_ops):
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


@contextmanager
def _patch_process_work_unit(
    tmp_path: Path,
    mock_git_ops: MagicMock,
    mock_judge: MagicMock,
    mock_mgr: MagicMock | None = None,
    max_retry_attempts: int = 3,
) -> Generator[MagicMock, None, None]:
    """Context manager that applies all patches needed for process_work_unit integration tests.

    Yields the mock executor so callers can set return values or assert calls.
    """
    effective_mgr = mock_mgr if mock_mgr is not None else MagicMock()
    with patch(f"{_ORC}.resolve_repo", return_value=_make_repo_config(tmp_path)):
        with patch(f"{_ORC}.claude_executor") as mock_exec:
            with patch(f"{_ORC}.CodeReviewJudge", return_value=mock_judge):
                with patch(f"{_ORC}.TestReviewJudge", return_value=mock_judge):
                    with patch(f"{_ORC}.DocReviewJudge", return_value=mock_judge):
                        with patch(f"{_ORC}.ChangesManifestJudge", return_value=mock_judge):
                            with patch(f"{_ORC}.SecurityReviewJudge", return_value=mock_judge):
                                with patch(f"{_ORC}.GitOpsJudge", return_value=mock_git_ops):
                                    with patch(f"{_ORC}.BacklogManager", return_value=effective_mgr):
                                        with patch(f"{_ORC}.BlockerResolverJudge"):
                                            with patch(f"{_ORC}.MAX_RETRY_ATTEMPTS", max_retry_attempts):
                                                with patch(
                                                    f"{_ORC}.BACKLOG_INDEX", tmp_path / "BACKLOG.md"
                                                ):
                                                    yield mock_exec


class TestConflictingPrRebase:
    """Tests for AC-1 through AC-4: CONFLICTING PR rebase-and-retry logic."""

    def test_conflicting_pr_triggers_rebase_and_retry(self, tmp_path: Path) -> None:
        """AC-1: A CONFLICTING PR triggers rebase_onto_default + force-push + retry merge.

        Given: merge_pr raises RuntimeError containing 'not mergeable' on the first call
        When: process_work_unit runs
        Then: rebase_onto_default is called, then merge_pr is retried and succeeds
        """
        from devbench.execution.orchestrator import process_work_unit

        unit = _make_unit(tmp_path)
        exec_result = ExecutionResult(status=ExecutionStatus.IN_REVIEW, output="done", blocker="")
        mock_judge = MagicMock()
        mock_judge.name = "mock"
        mock_judge.evaluate.return_value = _pass_result("mock")
        mock_mgr = MagicMock()

        mock_git_ops = MagicMock()
        mock_git_ops.is_committed_and_pushed.return_value = False
        mock_git_ops.create_pr.return_value = "https://github.com/org/repo/pull/5"
        mock_git_ops.wait_for_checks.return_value = True

        merge_calls = [0]

        def merge_side_effect(**kwargs):
            merge_calls[0] += 1
            if merge_calls[0] == 1:
                raise RuntimeError(
                    "Failed to merge PR #5 on caylent-solutions/git-repo: "
                    "not mergeable: the merge commit cannot be cleanly created"
                )

        mock_git_ops.merge_pr.side_effect = merge_side_effect

        with _patch_process_work_unit(tmp_path, mock_git_ops, mock_judge, mock_mgr) as mock_exec:
            mock_exec.execute.return_value = exec_result
            result = process_work_unit(unit)

        assert result is True
        mock_git_ops.rebase_onto_default.assert_called_once()
        assert merge_calls[0] == 2  # first failed, second succeeded

    def test_clean_pr_no_rebase(self, tmp_path: Path) -> None:
        """AC-2: A cleanly mergeable PR goes straight to merge (no rebase attempted).

        Given: merge_pr succeeds on the first call (no conflict)
        When: process_work_unit runs
        Then: rebase_onto_default is NOT called
        """
        from devbench.execution.orchestrator import process_work_unit

        unit = _make_unit(tmp_path)
        exec_result = ExecutionResult(status=ExecutionStatus.IN_REVIEW, output="done", blocker="")
        mock_judge = MagicMock()
        mock_judge.name = "mock"
        mock_judge.evaluate.return_value = _pass_result("mock")
        mock_mgr = MagicMock()

        mock_git_ops = MagicMock()
        mock_git_ops.is_committed_and_pushed.return_value = False
        mock_git_ops.create_pr.return_value = "https://github.com/org/repo/pull/3"
        mock_git_ops.wait_for_checks.return_value = True
        # merge_pr succeeds immediately (no exception)

        with _patch_process_work_unit(tmp_path, mock_git_ops, mock_judge, mock_mgr) as mock_exec:
            mock_exec.execute.return_value = exec_result
            result = process_work_unit(unit)

        assert result is True
        mock_git_ops.rebase_onto_default.assert_not_called()

    def test_rebase_failure_raises_not_loops(self, tmp_path: Path) -> None:
        """AC-3: Rebase failure surfaces as error; orchestrator does not loop back to create_pr.

        Given: merge_pr fails with 'not mergeable', then rebase_onto_default raises RuntimeError
        When: process_work_unit runs with MAX_RETRY_ATTEMPTS=3
        Then: GIT_ERROR is logged and create_pr is called exactly once (no looping back)

        Using MAX_RETRY_ATTEMPTS=3 proves the fix works independently of the retry cap:
        if the old loop-back-to-create_pr behavior were restored, create_pr would be called
        3 times (once per attempt). Asserting pr_create_calls[0] == 1 with 3 attempts available
        demonstrates that rebase failure causes fast-exit from the git ops block, not iteration restart.
        """
        from devbench.execution.orchestrator import process_work_unit

        unit = _make_unit(tmp_path)
        exec_result = ExecutionResult(status=ExecutionStatus.IN_REVIEW, output="done", blocker="")
        mock_judge = MagicMock()
        mock_judge.name = "mock"
        mock_judge.evaluate.return_value = _pass_result("mock")

        pr_create_calls = [0]
        mock_git_ops = MagicMock()
        mock_git_ops.is_committed_and_pushed.return_value = False

        def create_pr_side_effect(**kwargs):
            pr_create_calls[0] += 1
            return f"https://github.com/org/repo/pull/{pr_create_calls[0]}"

        mock_git_ops.create_pr.side_effect = create_pr_side_effect
        mock_git_ops.wait_for_checks.return_value = True
        mock_git_ops.merge_pr.side_effect = RuntimeError(
            "Failed to merge PR: not mergeable: the merge commit cannot be cleanly created"
        )
        mock_git_ops.rebase_onto_default.side_effect = RuntimeError(
            "git rebase origin/main2 failed (exit 1): conflict in src/foo.py"
        )

        with _patch_process_work_unit(
            tmp_path, mock_git_ops, mock_judge, max_retry_attempts=3
        ) as mock_exec:
            mock_exec.execute.return_value = exec_result
            result = process_work_unit(unit)

        assert result is False
        # With MAX_RETRY_ATTEMPTS=3, if the old loop-back behavior were present create_pr
        # would be called 3 times. Asserting exactly 1 proves rebase failure causes fast-exit.
        assert pr_create_calls[0] == 1
        # GIT_ERROR must be logged in the work unit file
        content = unit.file_path.read_text(encoding="utf-8")
        assert "GIT_ERROR" in content

    def test_rebase_retry_does_not_consume_extra_attempt(self, tmp_path: Path) -> None:
        """The rebase+retry is handled within the same iteration (no extra attempt consumed).

        Given: merge_pr fails with 'not mergeable' then succeeds after rebase
        When: process_work_unit runs with MAX_RETRY_ATTEMPTS=1
        Then: result is True (completed within the single attempt)
        """
        from devbench.execution.orchestrator import process_work_unit

        unit = _make_unit(tmp_path)
        exec_result = ExecutionResult(status=ExecutionStatus.IN_REVIEW, output="done", blocker="")
        mock_judge = MagicMock()
        mock_judge.name = "mock"
        mock_judge.evaluate.return_value = _pass_result("mock")
        mock_mgr = MagicMock()

        mock_git_ops = MagicMock()
        mock_git_ops.is_committed_and_pushed.return_value = False
        mock_git_ops.create_pr.return_value = "https://github.com/org/repo/pull/7"
        mock_git_ops.wait_for_checks.return_value = True

        merge_calls = [0]

        def merge_side_effect(**kwargs):
            merge_calls[0] += 1
            if merge_calls[0] == 1:
                raise RuntimeError("not mergeable: the merge commit cannot be cleanly created")

        mock_git_ops.merge_pr.side_effect = merge_side_effect

        with _patch_process_work_unit(
            tmp_path, mock_git_ops, mock_judge, mock_mgr, max_retry_attempts=1
        ) as mock_exec:
            mock_exec.execute.return_value = exec_result
            result = process_work_unit(unit)

        assert result is True
        mock_git_ops.rebase_onto_default.assert_called_once()


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


class TestOrchestratorCheckoutAfterMerge:
    """Tests for AC-1: orchestrator calls checkout_default_branch after every successful merge_pr."""

    def test_orchestrator_calls_checkout_default_branch_after_merge(self, tmp_path: Path) -> None:
        """AC-1: checkout_default_branch is called immediately after a successful merge_pr.

        Given: merge_pr succeeds
        When: process_work_unit runs
        Then: checkout_default_branch is called after merge_pr, before mark_done
        """
        from devbench.execution.orchestrator import process_work_unit

        unit = _make_unit(tmp_path)
        exec_result = ExecutionResult(status=ExecutionStatus.IN_REVIEW, output="done", blocker="")
        mock_judge = MagicMock()
        mock_judge.name = "mock"
        mock_judge.evaluate.return_value = _pass_result("mock")
        mock_mgr = MagicMock()

        call_order: list[str] = []

        mock_git_ops = MagicMock()
        mock_git_ops.is_committed_and_pushed.return_value = False
        mock_git_ops.create_pr.return_value = "https://github.com/org/repo/pull/99"
        mock_git_ops.wait_for_checks.return_value = True

        def merge_pr_side_effect(**kwargs: object) -> None:
            call_order.append("merge_pr")

        def checkout_default_side_effect(**kwargs: object) -> None:
            call_order.append("checkout_default_branch")

        mock_git_ops.merge_pr.side_effect = merge_pr_side_effect
        mock_git_ops.checkout_default_branch.side_effect = checkout_default_side_effect

        with _patch_process_work_unit(tmp_path, mock_git_ops, mock_judge, mock_mgr) as mock_exec:
            mock_exec.execute.return_value = exec_result
            result = process_work_unit(unit)

        assert result is True
        assert "merge_pr" in call_order, "merge_pr must be called"
        assert "checkout_default_branch" in call_order, "checkout_default_branch must be called after merge_pr"
        merge_idx = call_order.index("merge_pr")
        checkout_idx = call_order.index("checkout_default_branch")
        assert merge_idx < checkout_idx, "checkout_default_branch must come after merge_pr"


class TestProcessWorkUnitClaimsUnit:
    """AC-3: Orchestrator still claims unit as in-progress before executing."""

    def test_orchestrator_claims_unit_before_execute(self, tmp_path: Path) -> None:
        """
        Given: process_work_unit is called with a valid work unit
        When: the work unit succeeds
        Then: force_status is called with in-progress before the executor runs
        Spec: AC-3
        """
        from devbench.execution.orchestrator import process_work_unit

        unit = _make_unit(tmp_path)
        exec_result = ExecutionResult(status=ExecutionStatus.IN_REVIEW, output="done", blocker="")
        mock_judge = MagicMock()
        mock_judge.name = "mock"
        mock_judge.evaluate.return_value = _pass_result("mock")

        mock_git_ops = MagicMock()
        mock_git_ops.is_committed_and_pushed.return_value = False
        mock_git_ops.create_pr.return_value = "https://github.com/org/repo/pull/1"
        mock_git_ops.wait_for_checks.return_value = True
        mock_mgr = MagicMock()

        call_order: list[str] = []

        def force_status_side_effect(*args: object, **kwargs: object) -> None:
            call_order.append("force_status")

        def execute_side_effect(**kwargs: object) -> ExecutionResult:
            call_order.append("execute")
            return exec_result

        mock_mgr.force_status.side_effect = force_status_side_effect

        with _patch_process_work_unit(tmp_path, mock_git_ops, mock_judge, mock_mgr) as mock_exec:
            mock_exec.execute.side_effect = execute_side_effect
            result = process_work_unit(unit)

        assert result is True
        # force_status (claim) must precede execute
        assert "force_status" in call_order, "force_status must be called"
        assert "execute" in call_order, "execute must be called"
        claim_idx = call_order.index("force_status")
        execute_idx = call_order.index("execute")
        assert claim_idx < execute_idx, "unit must be claimed before executor runs"
        # Verify the claim was for in-progress
        first_force_call = mock_mgr.force_status.call_args_list[0]
        assert first_force_call.args[3] == "in-progress"
