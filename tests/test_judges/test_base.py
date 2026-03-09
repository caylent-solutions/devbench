"""Tests for judges.base module — _llm_evaluate, _parse_llm_response, and shared utilities."""

from __future__ import annotations

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
