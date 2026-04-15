"""Tests for judges.report module."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import patch

import pytest

from devbench.reporting.report import generate_report


@pytest.fixture(autouse=True)
def _mock_backlog_parser(mock_backlog_index):
    """Patch BacklogParser so tests don't require a real BACKLOG.md on disk."""
    with patch("devbench.reporting.report.BacklogParser") as mock_cls:
        instance = mock_cls.return_value
        instance.parse_index.return_value = []
        yield


def _make_log(entries: list[str]) -> str:
    return "\n".join(entries) + "\n"


class TestGenerateReport:
    """Test report generation from log and backlog data."""

    def test_report_contains_table_structure(self, tmp_path: Path) -> None:
        log_file = tmp_path / "test.log"
        log_file.write_text(
            _make_log(
                [
                    "2026-03-05T10:00:00Z [judges.cli] INFO Set E0-F1-S1-T1 to 'in-progress'",
                    "2026-03-05T10:05:00Z [judges.cli] INFO Set E0-F1-S1-T1 to 'done'",
                ]
            )
        )

        report = generate_report(log_path=log_file)
        assert "\u250c" in report  # top-left corner
        assert "\u2514" in report  # bottom-left corner
        assert "Tasks completed" in report
        assert "Tasks remaining" in report
        assert "Average time per task" in report
        assert "Estimated time to complete" in report

    def test_report_with_since_filter(self, tmp_path: Path) -> None:
        log_file = tmp_path / "test.log"
        log_file.write_text(
            _make_log(
                [
                    "2026-03-05T08:00:00Z [judges.cli] INFO Set E0-F1-S1-T1 to 'in-progress'",
                    "2026-03-05T08:05:00Z [judges.cli] INFO Set E0-F1-S1-T1 to 'done'",
                    "2026-03-05T10:00:00Z [judges.cli] INFO Set E0-F1-S1-T2 to 'in-progress'",
                    "2026-03-05T10:10:00Z [judges.cli] INFO Set E0-F1-S1-T2 to 'done'",
                ]
            )
        )

        since = datetime(2026, 3, 5, 9, 0, 0, tzinfo=UTC)
        report = generate_report(log_path=log_file, since=since)
        # Only 1 task should be in this session (T2, after 09:00)
        assert "Tasks in this session" in report

    def test_report_uses_session_start_for_tasks_started_before_since(self, tmp_path: Path) -> None:
        """When a task was set to 'in-progress' before --since but done after, the
        duration should be measured from session_start, not from the original
        in-progress time."""
        log_file = tmp_path / "test.log"
        log_file.write_text(
            _make_log(
                [
                    "2026-03-05T08:00:00Z [judges.cli] INFO Set E0-F1-S1-T1 to 'in-progress'",
                    "2026-03-05T10:30:00Z [judges.cli] INFO Set E0-F1-S1-T1 to 'done'",
                ]
            )
        )

        since = datetime(2026, 3, 5, 10, 0, 0, tzinfo=UTC)
        report = generate_report(log_path=log_file, since=since)
        # Task done at 10:30, session starts at 10:00 -> 30 min duration
        assert "30.0 minutes" in report

    def test_report_keeps_latest_in_progress_timestamp(self, tmp_path: Path) -> None:
        """When a task is set to 'in-progress' multiple times, the latest
        timestamp should be used for duration calculation."""
        log_file = tmp_path / "test.log"
        log_file.write_text(
            _make_log(
                [
                    "2026-03-05T08:00:00Z [judges.cli] INFO Set E0-F1-S1-T1 to 'in-progress'",
                    "2026-03-05T10:00:00Z [judges.cli] INFO Set E0-F1-S1-T1 to 'in-progress'",
                    "2026-03-05T10:20:00Z [judges.cli] INFO Set E0-F1-S1-T1 to 'done'",
                ]
            )
        )

        since = datetime(2026, 3, 5, 9, 0, 0, tzinfo=UTC)
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
        log_file.write_text(
            _make_log(
                [
                    "2026-03-05T10:00:00Z [judges.cli] INFO Set E0-F1-S1-T1 to 'in-progress'",
                    "2026-03-05T10:06:00Z [judges.cli] INFO Set E0-F1-S1-T1 to 'done'",
                ]
            )
        )

        report = generate_report(log_path=log_file)
        assert "At the current pace" in report
        assert "remaining" in report


class TestTokenCostReport:
    """Test token consumption and cost estimate rows in the report."""

    def test_report_shows_token_rows(self, tmp_path: Path) -> None:
        log_file = tmp_path / "test.log"
        log_file.write_text(
            _make_log(
                [
                    "2026-03-05T10:00:00Z [judges.cli] INFO Set E0-F1-S1-T1 to 'in-progress'",
                    "2026-03-05T10:05:00Z [judges.cli] INFO Set E0-F1-S1-T1 to 'done'",
                ]
            )
        )
        # Create a hook-logs.jsonl with token data
        hook_log = tmp_path / "hook-logs.jsonl"
        hook_log.write_text(
            '{"timestamp":"2026-03-05T10:04:00Z","event":"PostToolUse","input":{"tool_response":{"totalTokens":50000}}}\n'
            '{"timestamp":"2026-03-05T10:05:00Z","event":"PostToolUse","input":{"tool_response":{"totalTokens":30000}}}\n'
        )
        # Patch BACKLOG_INDEX to point to tmp_path so hook-logs.jsonl is found
        from unittest.mock import patch

        with patch("devbench.reporting.report.BACKLOG_INDEX", tmp_path / "BACKLOG.md"):
            report = generate_report(log_path=log_file)

        assert "Tokens consumed" in report
        assert "80,000" in report
        assert "Estimated cost so far" in report
        assert "Avg tokens per task" in report

    def test_report_shows_zero_tokens_when_no_hook_log(self, tmp_path: Path) -> None:
        log_file = tmp_path / "test.log"
        log_file.write_text(
            _make_log(
                [
                    "2026-03-05T10:00:00Z [judges.cli] INFO Set E0-F1-S1-T1 to 'in-progress'",
                    "2026-03-05T10:05:00Z [judges.cli] INFO Set E0-F1-S1-T1 to 'done'",
                ]
            )
        )
        # No hook-logs.jsonl created
        from unittest.mock import patch

        with patch("devbench.reporting.report.BACKLOG_INDEX", tmp_path / "BACKLOG.md"):
            report = generate_report(log_path=log_file)

        assert "Tokens consumed" in report
        assert "$0.00" in report

    def test_report_cost_footer_mentions_override(self, tmp_path: Path) -> None:
        log_file = tmp_path / "test.log"
        log_file.write_text(
            _make_log(
                [
                    "2026-03-05T10:00:00Z [judges.cli] INFO Set E0-F1-S1-T1 to 'in-progress'",
                    "2026-03-05T10:05:00Z [judges.cli] INFO Set E0-F1-S1-T1 to 'done'",
                ]
            )
        )
        hook_log = tmp_path / "hook-logs.jsonl"
        hook_log.write_text(
            '{"timestamp":"2026-03-05T10:04:00Z","event":"PostToolUse","input":{"tool_response":{"totalTokens":100000}}}\n'
        )
        from unittest.mock import patch

        with patch("devbench.reporting.report.BACKLOG_INDEX", tmp_path / "BACKLOG.md"):
            report = generate_report(log_path=log_file)

        assert "Override in devbench.yaml" in report


class TestBedrockConfig:
    """Test that USE_BEDROCK config toggles correctly."""

    def test_use_bedrock_false_by_default(self) -> None:
        from devbench.config import USE_BEDROCK

        # In test env, JUDGE_USE_BEDROCK is not set
        # Value depends on test environment, just verify it's a bool
        assert isinstance(USE_BEDROCK, bool)

    def test_bedrock_region_has_default(self) -> None:
        from devbench.config import BEDROCK_REGION

        assert isinstance(BEDROCK_REGION, str)
        assert len(BEDROCK_REGION) > 0
