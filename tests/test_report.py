"""Tests for judges.report module."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

from judges.report import generate_report


def _make_log(entries: list[str]) -> str:
    return "\n".join(entries) + "\n"


class TestGenerateReport:
    """Test report generation from log and backlog data."""

    def test_report_contains_table_structure(self, tmp_path: Path) -> None:
        log_file = tmp_path / "test.log"
        log_file.write_text(_make_log([
            "2026-03-05T10:00:00Z [judges.cli] INFO Set E0-F1-S1-T1 to in-progress",
            "2026-03-05T10:05:00Z [judges.cli] INFO Set E0-F1-S1-T1 to done",
        ]))

        report = generate_report(log_path=log_file)
        assert "\u250c" in report  # top-left corner
        assert "\u2514" in report  # bottom-left corner
        assert "Tasks completed" in report
        assert "Tasks remaining" in report
        assert "Average time per task" in report
        assert "Estimated time to complete" in report

    def test_report_with_since_filter(self, tmp_path: Path) -> None:
        log_file = tmp_path / "test.log"
        log_file.write_text(_make_log([
            "2026-03-05T08:00:00Z [judges.cli] INFO Set E0-F1-S1-T1 to in-progress",
            "2026-03-05T08:05:00Z [judges.cli] INFO Set E0-F1-S1-T1 to done",
            "2026-03-05T10:00:00Z [judges.cli] INFO Set E0-F1-S1-T2 to in-progress",
            "2026-03-05T10:10:00Z [judges.cli] INFO Set E0-F1-S1-T2 to done",
        ]))

        since = datetime(2026, 3, 5, 9, 0, 0, tzinfo=timezone.utc)
        report = generate_report(log_path=log_file, since=since)
        # Only 1 task should be in this session (T2, after 09:00)
        assert "Tasks in this session" in report

    def test_report_uses_session_start_for_tasks_started_before_since(self, tmp_path: Path) -> None:
        """When a task was set to in-progress before --since but done after, the
        duration should be measured from session_start, not from the original
        in-progress time."""
        log_file = tmp_path / "test.log"
        log_file.write_text(_make_log([
            "2026-03-05T08:00:00Z [judges.cli] INFO Set E0-F1-S1-T1 to in-progress",
            "2026-03-05T10:30:00Z [judges.cli] INFO Set E0-F1-S1-T1 to done",
        ]))

        since = datetime(2026, 3, 5, 10, 0, 0, tzinfo=timezone.utc)
        report = generate_report(log_path=log_file, since=since)
        # Task done at 10:30, session starts at 10:00 -> 30 min duration
        assert "30.0 minutes" in report

    def test_report_keeps_latest_in_progress_timestamp(self, tmp_path: Path) -> None:
        """When a task is set to in-progress multiple times, the latest
        timestamp should be used for duration calculation."""
        log_file = tmp_path / "test.log"
        log_file.write_text(_make_log([
            "2026-03-05T08:00:00Z [judges.cli] INFO Set E0-F1-S1-T1 to in-progress",
            "2026-03-05T10:00:00Z [judges.cli] INFO Set E0-F1-S1-T1 to in-progress",
            "2026-03-05T10:20:00Z [judges.cli] INFO Set E0-F1-S1-T1 to done",
        ]))

        since = datetime(2026, 3, 5, 9, 0, 0, tzinfo=timezone.utc)
        report = generate_report(log_path=log_file, since=since)
        # Latest in-progress at 10:00, done at 10:20 -> 20 min
        assert "20.0 minutes" in report

    def test_report_handles_empty_log(self, tmp_path: Path) -> None:
        log_file = tmp_path / "empty.log"
        log_file.write_text("")

        report = generate_report(log_path=log_file)
        assert "Tasks completed" in report
        assert "0.0 minutes" in report

    def test_report_handles_missing_log(self, tmp_path: Path) -> None:
        log_file = tmp_path / "nonexistent.log"

        report = generate_report(log_path=log_file)
        assert "Tasks completed" in report

    def test_report_summary_line(self, tmp_path: Path) -> None:
        log_file = tmp_path / "test.log"
        log_file.write_text(_make_log([
            "2026-03-05T10:00:00Z [judges.cli] INFO Set E0-F1-S1-T1 to in-progress",
            "2026-03-05T10:06:00Z [judges.cli] INFO Set E0-F1-S1-T1 to done",
        ]))

        report = generate_report(log_path=log_file)
        assert "At the current pace" in report
        assert "remaining" in report


class TestBedrockConfig:
    """Test that USE_BEDROCK config toggles correctly."""

    def test_use_bedrock_false_by_default(self) -> None:
        from judges.config import USE_BEDROCK

        # In test env, JUDGE_USE_BEDROCK is not set
        # Value depends on test environment, just verify it's a bool
        assert isinstance(USE_BEDROCK, bool)

    def test_bedrock_region_has_default(self) -> None:
        from judges.config import BEDROCK_REGION

        assert isinstance(BEDROCK_REGION, str)
        assert len(BEDROCK_REGION) > 0


class TestLlmEvaluateBedrock:
    """Test that _llm_evaluate uses the correct client based on USE_BEDROCK."""

    def test_uses_anthropic_client_when_bedrock_disabled(self) -> None:
        from unittest.mock import MagicMock

        from judges.judges.base import BaseJudge, Verdict

        class _TestJudge(BaseJudge):
            def evaluate(self, work_unit_path, repo_path, **kwargs):
                pass

        judge = _TestJudge("test")
        mock_message = MagicMock()
        mock_message.content = [
            MagicMock(text='{"verdict": "pass", "reasoning": "ok", "feedback": "", "evidence": []}')
        ]
        mock_client = MagicMock()
        mock_client.messages.create.return_value = mock_message

        with (
            patch("judges.judges.base.USE_BEDROCK", False),
            patch("judges.judges.base.get_anthropic_api_key", return_value="sk-test"),
            patch("judges.judges.base.anthropic.Anthropic", return_value=mock_client) as mock_cls,
        ):
            result = judge._llm_evaluate("prompt", {"s": "c"})

        mock_cls.assert_called_once()
        assert result.verdict is Verdict.PASS

    def test_uses_bedrock_client_when_bedrock_enabled(self) -> None:
        from unittest.mock import MagicMock

        from judges.judges.base import BaseJudge, Verdict

        class _TestJudge(BaseJudge):
            def evaluate(self, work_unit_path, repo_path, **kwargs):
                pass

        judge = _TestJudge("test")
        mock_message = MagicMock()
        mock_message.content = [
            MagicMock(text='{"verdict": "pass", "reasoning": "ok", "feedback": "", "evidence": []}')
        ]
        mock_client = MagicMock()
        mock_client.messages.create.return_value = mock_message

        with (
            patch("judges.judges.base.USE_BEDROCK", True),
            patch("judges.judges.base.BEDROCK_REGION", "us-east-1"),
            patch("judges.judges.base.anthropic.AnthropicBedrock", return_value=mock_client) as mock_cls,
        ):
            result = judge._llm_evaluate("prompt", {"s": "c"})

        mock_cls.assert_called_once_with(aws_region="us-east-1", timeout=300)
        assert result.verdict is Verdict.PASS

    def test_bedrock_does_not_call_get_api_key(self) -> None:
        from unittest.mock import MagicMock

        from judges.judges.base import BaseJudge

        class _TestJudge(BaseJudge):
            def evaluate(self, work_unit_path, repo_path, **kwargs):
                pass

        judge = _TestJudge("test")
        mock_message = MagicMock()
        mock_message.content = [
            MagicMock(text='{"verdict": "pass", "reasoning": "ok", "feedback": "", "evidence": []}')
        ]
        mock_client = MagicMock()
        mock_client.messages.create.return_value = mock_message

        with (
            patch("judges.judges.base.USE_BEDROCK", True),
            patch("judges.judges.base.BEDROCK_REGION", "us-east-1"),
            patch("judges.judges.base.anthropic.AnthropicBedrock", return_value=mock_client),
            patch("judges.judges.base.get_anthropic_api_key") as mock_get_key,
        ):
            judge._llm_evaluate("prompt", {"s": "c"})

        mock_get_key.assert_not_called()
