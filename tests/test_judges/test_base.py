"""Tests for judges.base module — _llm_evaluate, _parse_llm_response, and shared utilities."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import anthropic as anthropic_mod
import pytest

from devbench.judges.base import BaseJudge, JudgeResult, Verdict


def _make_result(
    verdict: Verdict, reasoning: str = "", feedback: str = "", evidence: list[str] | None = None
) -> JudgeResult:
    return JudgeResult(
        judge_name="test_judge",
        verdict=verdict,
        reasoning=reasoning,
        feedback=feedback,
        evidence=evidence or [],
    )


class _ConcreteJudge(BaseJudge):
    """Concrete subclass for testing abstract base."""

    def evaluate(self, work_unit_path, repo_path, **kwargs):
        return _make_result(Verdict.PASS)


class TestParseLlmResponse:
    """Test _parse_llm_response handles various Claude output formats."""

    def test_parses_clean_json(self) -> None:
        judge = _ConcreteJudge("test_judge")
        raw = '{"verdict": "pass", "reasoning": "looks good", "feedback": "", "evidence": ["checked X"]}'
        result = judge._parse_llm_response(raw)
        assert result.verdict is Verdict.PASS
        assert result.reasoning == "looks good"
        assert result.evidence == ["checked X"]

    def test_parses_json_in_markdown_fences(self) -> None:
        judge = _ConcreteJudge("test_judge")
        raw = '```json\n{"verdict": "fail", "reasoning": "bad", "feedback": "fix it", "evidence": []}\n```'
        result = judge._parse_llm_response(raw)
        assert result.verdict is Verdict.FAIL
        assert result.feedback == "fix it"

    def test_parses_json_with_preamble(self) -> None:
        judge = _ConcreteJudge("test_judge")
        raw = 'Here is my analysis:\n{"verdict": "pass", "reasoning": "ok", "feedback": "", "evidence": []}'
        result = judge._parse_llm_response(raw)
        assert result.verdict is Verdict.PASS

    def test_handles_no_json(self) -> None:
        judge = _ConcreteJudge("test_judge")
        raw = "I cannot provide a JSON response."
        result = judge._parse_llm_response(raw)
        assert result.verdict is Verdict.FAIL
        assert "did not contain valid JSON" in result.reasoning

    def test_handles_invalid_json(self) -> None:
        judge = _ConcreteJudge("test_judge")
        raw = '{"verdict": "pass", broken}'
        result = judge._parse_llm_response(raw)
        assert result.verdict is Verdict.FAIL
        assert "Failed to parse" in result.reasoning

    def test_defaults_unknown_verdict_to_fail(self) -> None:
        judge = _ConcreteJudge("test_judge")
        raw = '{"verdict": "maybe", "reasoning": "unsure", "feedback": "", "evidence": []}'
        result = judge._parse_llm_response(raw)
        assert result.verdict is Verdict.FAIL

    def test_handles_missing_fields(self) -> None:
        judge = _ConcreteJudge("test_judge")
        raw = '{"verdict": "pass"}'
        result = judge._parse_llm_response(raw)
        assert result.verdict is Verdict.PASS
        assert result.reasoning == ""
        assert result.feedback == ""
        assert result.evidence == []


class TestLlmEvaluate:
    """Test _llm_evaluate method with mocked Anthropic SDK."""

    @pytest.fixture(autouse=True)
    def disable_bedrock(self):
        """Force USE_BEDROCK=False so tests always exercise the Anthropic API path."""
        with patch("devbench.judges.base.USE_BEDROCK", False):
            yield

    def test_returns_pass_on_valid_response(self) -> None:
        judge = _ConcreteJudge("test_judge")
        mock_message = MagicMock()
        mock_message.content = [
            MagicMock(text='{"verdict": "pass", "reasoning": "ok", "feedback": "", "evidence": ["e1"]}')
        ]

        mock_client = MagicMock()
        mock_client.messages.create.return_value = mock_message

        with (
            patch("devbench.judges.base.get_anthropic_api_key", return_value="sk-ant-test"),
            patch("devbench.judges.base.anthropic.Anthropic", return_value=mock_client),
        ):
            result = judge._llm_evaluate(
                system_prompt="Test prompt",
                evidence_sections={"section": "content"},
            )

        assert result.verdict is Verdict.PASS
        assert result.reasoning == "ok"

    def test_includes_previous_feedback_in_prompt(self) -> None:
        judge = _ConcreteJudge("test_judge")
        judge.previous_feedback = "Fix the hardcoded timeout default."
        mock_message = MagicMock()
        mock_message.content = [
            MagicMock(text='{"verdict": "pass", "reasoning": "ok", "feedback": "", "evidence": []}')
        ]

        mock_client = MagicMock()
        mock_client.messages.create.return_value = mock_message

        with (
            patch("devbench.judges.base.get_anthropic_api_key", return_value="sk-ant-test"),
            patch("devbench.judges.base.anthropic.Anthropic", return_value=mock_client),
        ):
            judge._llm_evaluate(
                system_prompt="Test prompt",
                evidence_sections={"section": "content"},
            )

        call_kwargs = mock_client.messages.create.call_args
        user_msg = call_kwargs[1]["messages"][0]["content"]
        assert "Previous Review Feedback" in user_msg
        assert "Fix the hardcoded timeout default." in user_msg
        assert "do not re-raise" in user_msg.lower()

    def test_omits_previous_feedback_when_empty(self) -> None:
        judge = _ConcreteJudge("test_judge")
        mock_message = MagicMock()
        mock_message.content = [
            MagicMock(text='{"verdict": "pass", "reasoning": "ok", "feedback": "", "evidence": []}')
        ]

        mock_client = MagicMock()
        mock_client.messages.create.return_value = mock_message

        with (
            patch("devbench.judges.base.get_anthropic_api_key", return_value="sk-ant-test"),
            patch("devbench.judges.base.anthropic.Anthropic", return_value=mock_client),
        ):
            judge._llm_evaluate(
                system_prompt="Test prompt",
                evidence_sections={"section": "content"},
            )

        call_kwargs = mock_client.messages.create.call_args
        user_msg = call_kwargs[1]["messages"][0]["content"]
        assert "Previous Review Feedback" not in user_msg

    def test_returns_fail_on_api_error(self) -> None:
        judge = _ConcreteJudge("test_judge")

        mock_client = MagicMock()
        mock_client.messages.create.side_effect = anthropic_mod.APIError(
            message="Service unavailable",
            request=MagicMock(),
            body=None,
        )

        with (
            patch("devbench.judges.base.get_anthropic_api_key", return_value="sk-ant-test"),
            patch("devbench.judges.base.anthropic.Anthropic", return_value=mock_client),
        ):
            result = judge._llm_evaluate(
                system_prompt="Test prompt",
                evidence_sections={"section": "content"},
            )

        assert result.verdict is Verdict.FAIL
        assert "LLM API call failed" in result.reasoning

    def test_truncates_large_evidence(self) -> None:
        judge = _ConcreteJudge("test_judge")
        mock_message = MagicMock()
        mock_message.content = [
            MagicMock(text='{"verdict": "pass", "reasoning": "ok", "feedback": "", "evidence": []}')
        ]

        mock_client = MagicMock()
        mock_client.messages.create.return_value = mock_message

        large_content = "x" * 20000

        with (
            patch("devbench.judges.base.get_anthropic_api_key", return_value="sk-ant-test"),
            patch("devbench.judges.base.anthropic.Anthropic", return_value=mock_client),
        ):
            judge._llm_evaluate(
                system_prompt="Test prompt",
                evidence_sections={"big": large_content},
            )

        # Verify the user prompt sent to Claude had truncated content with marker
        call_kwargs = mock_client.messages.create.call_args
        user_msg = call_kwargs[1]["messages"][0]["content"]
        assert len(user_msg) < 20000
        assert "TRUNCATED" in user_msg
        assert "20000" in user_msg

    def test_raises_when_no_credentials(self) -> None:
        judge = _ConcreteJudge("test_judge")

        with patch("devbench.judges.base.get_anthropic_api_key", side_effect=RuntimeError("No credentials")):
            with pytest.raises(RuntimeError, match="No credentials"):
                judge._llm_evaluate(
                    system_prompt="Test prompt",
                    evidence_sections={"section": "content"},
                )


class TestGetDefaultBranch:
    """Test _get_default_branch remote flag (AC-1 through AC-3, E25-F1-S1-T1)."""

    # --- YAML-config path (remote x bare) ---

    @pytest.mark.parametrize("remote,expected", [
        (False, "main2"),
        (True, "origin/main2"),
    ])
    def test_yaml_path(self, tmp_path: Path, remote: bool, expected: str) -> None:
        judge = _ConcreteJudge("test_judge")
        with patch("devbench.judges.base.get_configured_default_branch", return_value="main2"):
            result = judge._get_default_branch(tmp_path, repo="org/repo", remote=remote)
        assert result == expected

    # --- git-fallback path (remote x bare) ---

    @pytest.mark.parametrize("remote,git_output,expected", [
        (False, "origin/main\n", "main"),
        (True, "origin/main2\n", "origin/main2"),
    ])
    def test_git_fallback_path(
        self, tmp_path: Path, remote: bool, git_output: str, expected: str
    ) -> None:
        judge = _ConcreteJudge("test_judge")
        with patch("devbench.judges.base.get_configured_default_branch", return_value=None):
            with patch.object(judge, "_run_command", return_value=(0, git_output, "")):
                result = judge._get_default_branch(tmp_path, repo="org/repo", remote=remote)
        assert result == expected

    def test_default_remote_is_false(self, tmp_path: Path) -> None:
        """Omitting remote= preserves existing bare-name behaviour for all callers."""
        judge = _ConcreteJudge("test_judge")
        with patch("devbench.judges.base.get_configured_default_branch", return_value="main2"):
            result = judge._get_default_branch(tmp_path, repo="org/repo")
        assert result == "main2"

    @pytest.mark.parametrize("rc,stdout", [
        (1, ""),
        (0, ""),
    ])
    def test_git_fallback_raises_when_git_fails(
        self, tmp_path: Path, rc: int, stdout: str
    ) -> None:
        """RuntimeError is raised when git cannot determine the default branch."""
        judge = _ConcreteJudge("test_judge")
        with patch("devbench.judges.base.get_configured_default_branch", return_value=None):
            with patch.object(judge, "_run_command", return_value=(rc, stdout, "")):
                with pytest.raises(RuntimeError, match="Cannot determine default branch"):
                    judge._get_default_branch(tmp_path, repo="org/repo")


class TestGetDiff:
    """Test _get_diff method on BaseJudge."""

    def test_includes_staged_diff(self, tmp_path: Path) -> None:
        judge = _ConcreteJudge("test_judge")

        def side_effect(cmd, cwd, **kwargs):
            if "--cached" in cmd:
                return (0, "staged changes", "")
            return (0, "", "")

        with patch.object(judge, "_get_default_branch", return_value="origin/main"):
            with patch.object(judge, "_run_command", side_effect=side_effect):
                diff = judge._get_diff(tmp_path)
        assert "staged changes" in diff

    def test_includes_unstaged_diff(self, tmp_path: Path) -> None:
        judge = _ConcreteJudge("test_judge")

        def side_effect(cmd, cwd, **kwargs):
            if cmd == ["git", "diff"]:
                return (0, "unstaged changes", "")
            return (0, "", "")

        with patch.object(judge, "_get_default_branch", return_value="origin/main"):
            with patch.object(judge, "_run_command", side_effect=side_effect):
                diff = judge._get_diff(tmp_path)
        assert "unstaged changes" in diff

    def test_includes_committed_branch_diff(self, tmp_path: Path) -> None:
        judge = _ConcreteJudge("test_judge")

        def side_effect(cmd, cwd, **kwargs):
            if "origin/main" in cmd:
                return (0, "branch changes", "")
            return (0, "", "")

        with patch.object(judge, "_get_default_branch", return_value="origin/main"):
            with patch.object(judge, "_run_command", side_effect=side_effect):
                diff = judge._get_diff(tmp_path)
        assert "branch changes" in diff

    def test_includes_untracked_file_as_synthetic_hunk(self, tmp_path: Path) -> None:
        judge = _ConcreteJudge("test_judge")
        new_file = tmp_path / "new_module.py"
        new_file.write_text("def hello():\n    pass\n")

        def side_effect(cmd, cwd, **kwargs):
            if "--others" in cmd:
                return (0, "new_module.py\n", "")
            return (0, "", "")

        with patch.object(judge, "_get_default_branch", return_value="origin/main"):
            with patch.object(judge, "_run_command", side_effect=side_effect):
                diff = judge._get_diff(tmp_path)
        assert "new_module.py" in diff
        assert "+def hello():" in diff
        assert "--- /dev/null" in diff

    def test_returns_empty_when_all_fail(self, tmp_path: Path) -> None:
        judge = _ConcreteJudge("test_judge")
        with patch.object(judge, "_get_default_branch", return_value="origin/main"):
            with patch.object(judge, "_run_command", return_value=(1, "", "error")):
                diff = judge._get_diff(tmp_path)
        assert diff == ""


class TestReadFile:
    """Test _read_file method."""

    def test_reads_existing_file(self, tmp_path) -> None:
        judge = _ConcreteJudge("test_judge")
        f = tmp_path / "test.txt"
        f.write_text("hello")
        assert judge._read_file(f) == "hello"

    def test_raises_for_missing_file(self, tmp_path) -> None:
        judge = _ConcreteJudge("test_judge")
        with pytest.raises(FileNotFoundError):
            judge._read_file(tmp_path / "missing.txt")


class TestStripAgentLog:
    """Test _strip_agent_log removes ## Comments section from work unit content."""

    def test_removes_comments_section(self) -> None:
        """AC-1: content with \\n## Comments is truncated before that marker."""
        content = "## Description\n\nsome spec\n## Comments\n[REVIEW_FAIL] bad"
        result = _ConcreteJudge._strip_agent_log(content)
        assert result == "## Description\n\nsome spec", (
            f"Expected spec only, got: {result!r}"
        )

    def test_no_op_when_absent(self) -> None:
        """AC-2: content without ## Comments section is returned unchanged."""
        content = "## Description\n\nsome spec\n## Definition of Done\n- [ ] item"
        result = _ConcreteJudge._strip_agent_log(content)
        assert result == content, f"Expected unchanged content, got: {result!r}"

    def test_boundary_returns_empty_string(self) -> None:
        """AC-3: content ending exactly at \\n## Comments returns empty string."""
        content = "\n## Comments"
        result = _ConcreteJudge._strip_agent_log(content)
        assert result == "", f"Expected empty string at boundary, got: {result!r}"


class TestReadWorkUnit:
    """Test _read_work_unit reads file and strips agent log."""

    def test_strips_agent_log_from_file(self, tmp_path: Path) -> None:
        """AC-4: _read_work_unit returns content with ## Comments section removed."""
        wu = tmp_path / "wu.md"
        wu.write_text("## Description\n\nspec\n## Comments\n[REVIEW_FAIL] noise")
        judge = _ConcreteJudge("test_judge")
        result = judge._read_work_unit(wu)
        assert result == "## Description\n\nspec", (
            f"Expected stripped content, got: {result!r}"
        )

    def test_raises_for_missing_file(self, tmp_path: Path) -> None:
        judge = _ConcreteJudge("test_judge")
        with pytest.raises(FileNotFoundError):
            judge._read_work_unit(tmp_path / "missing.md")


class TestLlmEvaluateTruncationWarning:
    """Test _llm_evaluate emits warning when evidence sections are truncated."""

    @pytest.fixture(autouse=True)
    def disable_bedrock(self):
        with patch("devbench.judges.base.USE_BEDROCK", False):
            yield

    def _make_mock_client(self):
        mock_message = MagicMock()
        mock_message.content = [
            MagicMock(text='{"verdict": "pass", "reasoning": "ok", "feedback": "", "evidence": []}')
        ]
        mock_client = MagicMock()
        mock_client.messages.create.return_value = mock_message
        return mock_client

    def test_warns_when_section_truncated(self) -> None:
        """AC-6: warning logged with section name and char counts when truncated."""
        judge = _ConcreteJudge("test_judge")
        large_content = "x" * 20000

        mock_client = self._make_mock_client()
        with (
            patch("devbench.judges.base.get_anthropic_api_key", return_value="sk-ant-test"),
            patch("devbench.judges.base.anthropic.Anthropic", return_value=mock_client),
        ):
            with patch.object(judge, "logger") as mock_logger:
                judge._llm_evaluate(
                    system_prompt="Test prompt",
                    evidence_sections={"big_section": large_content},
                )
        mock_logger.warning.assert_called_once()
        warning_args = mock_logger.warning.call_args[0]
        assert "big_section" in warning_args[1], (
            "Warning should include the section name"
        )
        assert 20000 in warning_args, (
            "Warning should include the original char count"
        )

    def test_no_warning_when_under_limit(self) -> None:
        """AC-7: no warning logged when no section exceeds LLM_EVIDENCE_TRUNCATION."""
        judge = _ConcreteJudge("test_judge")
        small_content = "x" * 100

        mock_client = self._make_mock_client()
        with (
            patch("devbench.judges.base.get_anthropic_api_key", return_value="sk-ant-test"),
            patch("devbench.judges.base.anthropic.Anthropic", return_value=mock_client),
        ):
            with patch.object(judge, "logger") as mock_logger:
                judge._llm_evaluate(
                    system_prompt="Test prompt",
                    evidence_sections={"small_section": small_content},
                )
        mock_logger.warning.assert_not_called()
