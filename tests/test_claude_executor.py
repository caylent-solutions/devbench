"""Tests for judges.claude_executor module."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest

from judges.claude_executor import (
    ExecutionResult,
    ExecutionStatus,
    _build_prompt,
    _extract_blocker,
    _is_nested_claude,
)


class TestBuildPrompt:
    """Test _build_prompt generates correct prompt."""

    def test_basic_prompt_contains_work_unit_path(self, tmp_path: Path) -> None:
        wu_path = tmp_path / "E0-F1-S1-T1.md"
        prompt = _build_prompt(wu_path)

        assert str(wu_path) in prompt
        assert "CLAUDE.md" in prompt
        assert "AGENT-INSTRUCTIONS.md" in prompt
        assert "TDD" in prompt
        assert "acceptance criteria" in prompt.lower()

    def test_prompt_does_not_contain_feedback_when_empty(self, tmp_path: Path) -> None:
        wu_path = tmp_path / "task.md"
        prompt = _build_prompt(wu_path)

        assert "Previous attempt was rejected" not in prompt

    def test_prompt_includes_feedback_when_provided(self, tmp_path: Path) -> None:
        wu_path = tmp_path / "task.md"
        feedback = "Code review failed: hardcoded sleep detected"
        prompt = _build_prompt(wu_path, feedback=feedback)

        assert "Previous attempt was rejected" in prompt
        assert feedback in prompt

    def test_prompt_contains_execution_instructions(self, tmp_path: Path) -> None:
        wu_path = tmp_path / "task.md"
        prompt = _build_prompt(wu_path)

        assert "in-review" in prompt.lower()
        assert "comments" in prompt.lower()
        assert "dependencies" in prompt.lower()

    def test_prompt_includes_all_required_file_references(self, tmp_path: Path) -> None:
        wu_path = tmp_path / "work.md"
        prompt = _build_prompt(wu_path)

        # Should reference CLAUDE.md, AGENT-INSTRUCTIONS.md, and the work unit
        assert "CLAUDE.md" in prompt
        assert "AGENT-INSTRUCTIONS.md" in prompt
        assert str(wu_path) in prompt


class TestExtractBlocker:
    """Test _extract_blocker extracts blocker description from output."""

    def test_extracts_line_with_blocked(self) -> None:
        output = "Starting execution...\nBLOCKED: dependency E0-F1-S1-T1 not done\nDone."
        result = _extract_blocker(output)
        assert "BLOCKED" in result
        assert "E0-F1-S1-T1" in result

    def test_extracts_line_with_blocker(self) -> None:
        output = "line1\nBlocker: missing API credentials\nline3"
        result = _extract_blocker(output)
        assert "Blocker" in result
        assert "API credentials" in result

    def test_raises_when_no_match(self) -> None:
        output = "Everything is fine\nNo issues here\n"
        with pytest.raises(RuntimeError, match="Could not extract blocker"):
            _extract_blocker(output)


class TestExecutionStatusEnum:
    """Test ExecutionStatus enum values."""

    def test_in_review_value(self) -> None:
        assert ExecutionStatus.IN_REVIEW.value == "in-review"

    def test_blocked_value(self) -> None:
        assert ExecutionStatus.BLOCKED.value == "blocked"

    def test_failed_value(self) -> None:
        assert ExecutionStatus.FAILED.value == "failed"


class TestExecutionResult:
    """Test ExecutionResult dataclass."""

    def test_creation_with_defaults(self) -> None:
        result = ExecutionResult(
            status=ExecutionStatus.IN_REVIEW,
            output="Agent completed",
        )
        assert result.status is ExecutionStatus.IN_REVIEW
        assert result.output == "Agent completed"
        assert result.blocker == ""

    def test_creation_with_blocker(self) -> None:
        result = ExecutionResult(
            status=ExecutionStatus.BLOCKED,
            output="Agent blocked",
            blocker="Missing dependency",
        )
        assert result.blocker == "Missing dependency"


class TestIsNestedClaude:
    """Test _is_nested_claude detection."""

    def test_returns_false_when_env_not_set(self) -> None:
        with patch.dict("os.environ", {}, clear=True):
            assert _is_nested_claude() is False

    def test_returns_true_when_env_set(self) -> None:
        with patch.dict("os.environ", {"CLAUDE_CONTEXT": "interactive"}):
            assert _is_nested_claude() is True

    def test_returns_false_when_env_empty(self) -> None:
        with patch.dict("os.environ", {"CLAUDE_CONTEXT": ""}):
            assert _is_nested_claude() is False


class TestExecuteNestedGuard:
    """Test that execute raises RuntimeError when called from inside Claude Code."""

    def test_raises_when_nested(self) -> None:
        from judges.claude_executor import execute

        with patch.dict("os.environ", {"CLAUDE_CONTEXT": "interactive"}):
            with pytest.raises(RuntimeError, match="Cannot spawn nested Claude CLI"):
                execute(
                    work_unit_path=Path("/tmp/task.md"),
                    repo="caylent-solutions/git-repo",
                )

    def test_error_message_includes_guidance(self) -> None:
        from judges.claude_executor import execute

        with patch.dict("os.environ", {"CLAUDE_CONTEXT": "interactive"}):
            with pytest.raises(RuntimeError, match=r"judges\.cli review"):
                execute(
                    work_unit_path=Path("/tmp/task.md"),
                    repo="caylent-solutions/git-repo",
                )


class TestExecute:
    """Test the execute function with mocked subprocess."""

    def test_execute_validates_repo(self) -> None:
        from judges.claude_executor import execute

        with pytest.raises(ValueError, match="not recognised"):
            execute(
                work_unit_path=Path("/tmp/task.md"),
                repo="evil-org/bad-repo",
            )

    def test_execute_raises_for_missing_local_path(self) -> None:
        from judges.claude_executor import execute

        with patch("judges.claude_executor.REPO_LOCAL_PATHS", {}):
            with pytest.raises(ValueError, match="No local path"):
                execute(
                    work_unit_path=Path("/tmp/task.md"),
                    repo="caylent-solutions/git-repo",
                )

    def test_execute_returns_in_review_on_success(self, tmp_path: Path) -> None:
        from judges.claude_executor import execute

        wu_file = tmp_path / "task.md"
        wu_file.write_text("## Status: in-review\n")

        mock_result = type(
            "Result",
            (),
            {
                "returncode": 0,
                "stdout": "Agent completed successfully",
                "stderr": "",
            },
        )()

        with (
            patch("judges.claude_executor.REPO_LOCAL_PATHS", {"caylent-solutions/git-repo": tmp_path}),
            patch("subprocess.run", return_value=mock_result),
        ):
            result = execute(
                work_unit_path=wu_file,
                repo="caylent-solutions/git-repo",
            )

        assert result.status is ExecutionStatus.IN_REVIEW

    def test_execute_returns_blocked_when_output_mentions_blocker(self, tmp_path: Path) -> None:
        from judges.claude_executor import execute

        wu_file = tmp_path / "task.md"
        wu_file.write_text("## Status: In Queue\n")

        mock_result = type(
            "Result",
            (),
            {
                "returncode": 1,
                "stdout": "BLOCKED: dependency not met",
                "stderr": "",
            },
        )()

        with (
            patch("judges.claude_executor.REPO_LOCAL_PATHS", {"caylent-solutions/git-repo": tmp_path}),
            patch("subprocess.run", return_value=mock_result),
        ):
            result = execute(
                work_unit_path=wu_file,
                repo="caylent-solutions/git-repo",
            )

        assert result.status is ExecutionStatus.BLOCKED
        assert "BLOCKED" in result.blocker

    def test_execute_returns_failed_on_timeout(self, tmp_path: Path) -> None:
        import subprocess as sp

        from judges.claude_executor import execute

        wu_file = tmp_path / "task.md"
        wu_file.write_text("## Status: In Queue\n")

        with (
            patch("judges.claude_executor.REPO_LOCAL_PATHS", {"caylent-solutions/git-repo": tmp_path}),
            patch("subprocess.run", side_effect=sp.TimeoutExpired(cmd="claude", timeout=10)),
        ):
            result = execute(
                work_unit_path=wu_file,
                repo="caylent-solutions/git-repo",
                timeout_seconds=10,
            )

        assert result.status is ExecutionStatus.FAILED
        assert "timed out" in result.output.lower()
