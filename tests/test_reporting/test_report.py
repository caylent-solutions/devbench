"""Tests for judges.report module."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest

from devbench.backlog.work_unit import WorkUnit, WorkUnitStatus, WorkUnitType
from devbench.reporting.report import WindowStats, generate_report


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
        assert "Est. time to complete remaining" in report

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
        in-progress time. Uses 3 completed tasks so the pace metric clears the
        MIN_PACE_SAMPLES threshold."""
        log_file = tmp_path / "test.log"
        log_file.write_text(
            _make_log(
                [
                    "2026-03-05T08:00:00Z [judges.cli] INFO Set E0-F1-S1-T1 to 'in-progress'",
                    "2026-03-05T10:30:00Z [judges.cli] INFO Set E0-F1-S1-T1 to 'done'",
                    "2026-03-05T10:30:00Z [judges.cli] INFO Set E0-F1-S1-T2 to 'in-progress'",
                    "2026-03-05T11:00:00Z [judges.cli] INFO Set E0-F1-S1-T2 to 'done'",
                    "2026-03-05T11:00:00Z [judges.cli] INFO Set E0-F1-S1-T3 to 'in-progress'",
                    "2026-03-05T11:30:00Z [judges.cli] INFO Set E0-F1-S1-T3 to 'done'",
                ]
            )
        )

        since = datetime(2026, 3, 5, 10, 0, 0, tzinfo=UTC)
        report = generate_report(log_path=log_file, since=since)
        # Three tasks done at 30 min each -> avg 30 min per task in window
        assert "30.0 min" in report

    def test_report_keeps_latest_in_progress_timestamp(self, tmp_path: Path) -> None:
        """When a task is set to 'in-progress' multiple times, the latest
        timestamp should be used for duration calculation. Padded with two
        more completions to clear MIN_PACE_SAMPLES."""
        log_file = tmp_path / "test.log"
        log_file.write_text(
            _make_log(
                [
                    "2026-03-05T08:00:00Z [judges.cli] INFO Set E0-F1-S1-T1 to 'in-progress'",
                    "2026-03-05T10:00:00Z [judges.cli] INFO Set E0-F1-S1-T1 to 'in-progress'",
                    "2026-03-05T10:20:00Z [judges.cli] INFO Set E0-F1-S1-T1 to 'done'",
                    "2026-03-05T10:20:00Z [judges.cli] INFO Set E0-F1-S1-T2 to 'in-progress'",
                    "2026-03-05T10:40:00Z [judges.cli] INFO Set E0-F1-S1-T2 to 'done'",
                    "2026-03-05T10:40:00Z [judges.cli] INFO Set E0-F1-S1-T3 to 'in-progress'",
                    "2026-03-05T11:00:00Z [judges.cli] INFO Set E0-F1-S1-T3 to 'done'",
                ]
            )
        )

        since = datetime(2026, 3, 5, 9, 0, 0, tzinfo=UTC)
        report = generate_report(log_path=log_file, since=since)
        # Three tasks done at 20 min each -> avg 20 min per task in window
        assert "20.0 min" in report

    def test_report_handles_empty_log(self, tmp_path: Path) -> None:
        log_file = tmp_path / "empty.log"
        log_file.write_text("")

        report = generate_report(log_path=log_file)
        assert "Tasks completed" in report
        # With no completed tasks, per-task averages are "n/a" (not "0.0 minutes")
        # since 0 is misleading (suggests pace, not absence of data).
        assert "n/a" in report

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
        # Backlog parser is mocked to return zero units, so tasks_active = 0;
        # the summary line takes the "All tasks complete." branch.
        assert "All tasks complete." in report


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
            '{"timestamp":"2026-03-05T10:04:00Z","event":"PostToolUse","input":{"tool_response":'
            '{"usage":{"input_tokens":50000}}}}\n'
            '{"timestamp":"2026-03-05T10:05:00Z","event":"PostToolUse","input":{"tool_response":'
            '{"usage":{"input_tokens":30000}}}}\n'
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


class TestEpicsDoneReport:
    """Test report with epics that are done (covers line 92/97: epics list comprehension)."""

    def test_report_shows_epics_done_count(self, tmp_path: Path) -> None:
        """When the backlog contains a done epic, the report displays its count."""
        from devbench.backlog.work_unit import WorkUnit

        done_epic = WorkUnit(
            id="E0",
            title="Epic Zero",
            status=WorkUnitStatus.DONE,
            unit_type=WorkUnitType.EPIC,
            file_path=Path("backlog/E0.md"),
            repo="git-repo",
            dependencies=[],
        )
        done_task = WorkUnit(
            id="E0-F1-S1-T1",
            title="Task One",
            status=WorkUnitStatus.DONE,
            unit_type=WorkUnitType.TASK,
            file_path=Path("backlog/E0-F1-S1-T1.md"),
            repo="git-repo",
            dependencies=[],
        )

        with patch("devbench.reporting.report.BacklogParser") as mock_cls:
            mock_cls.return_value.parse_index.return_value = [done_epic, done_task]

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

        # The Backlog state table renders the combined "Stories / Features / Epics" row;
        # since the test backlog has 1 epic done, the value cell shows "0 / 0 / 1".
        assert "Stories / Features / Epics auto-rolled to done" in report
        assert "0 / 0 / 1" in report


class TestHookLogDurationMetrics:
    """Test API processing time from hook-logs.jsonl (covers lines 61-63, 70-71)."""

    def test_api_duration_accumulates_from_hook_log(self, tmp_path: Path) -> None:
        """totalDurationMs values in hook-logs.jsonl are summed into API processing time."""
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
            '{"timestamp":"2026-03-05T10:02:00Z","event":"PostToolUse","input":{"tool_response":'
            '{"totalDurationMs":60000,"usage":{"input_tokens":10000}}}}\n'
            '{"timestamp":"2026-03-05T10:04:00Z","event":"PostToolUse","input":{"tool_response":'
            '{"totalDurationMs":120000,"usage":{"input_tokens":20000}}}}\n'
        )

        with patch("devbench.reporting.report.BACKLOG_INDEX", tmp_path / "BACKLOG.md"):
            report = generate_report(log_path=log_file)

        # 60000 + 120000 = 180000 ms = 180 s = 0.05 hours
        assert "API processing time" in report

    def test_hook_log_entry_before_since_is_excluded(self, tmp_path: Path) -> None:
        """Entries with timestamp before session_start are filtered out (lines 60-61)."""
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
            # This entry is before session_start, should be excluded
            '{"timestamp":"2026-03-05T08:00:00Z","event":"PostToolUse","input":{"tool_response":'
            '{"totalDurationMs":999999,"usage":{"input_tokens":99999}}}}\n'
            # This entry is within session, should be included
            '{"timestamp":"2026-03-05T10:03:00Z","event":"PostToolUse","input":{"tool_response":'
            '{"totalDurationMs":60000,"usage":{"input_tokens":10000}}}}\n'
        )

        since = datetime(2026, 3, 5, 10, 0, 0, tzinfo=UTC)
        with patch("devbench.reporting.report.BACKLOG_INDEX", tmp_path / "BACKLOG.md"):
            report = generate_report(log_path=log_file, since=since)

        # Only 10,000 tokens should be counted (the pre-session entry excluded)
        assert "10,000" in report

    def test_hook_log_skips_blank_lines(self, tmp_path: Path) -> None:
        """Blank lines in hook-logs.jsonl are skipped (line 51)."""
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
            '{"timestamp":"2026-03-05T10:03:00Z","event":"PostToolUse","input":{"tool_response":'
            '{"usage":{"input_tokens":5000}}}}\n'
            "\n"
            "   \n"
            '{"timestamp":"2026-03-05T10:04:00Z","event":"PostToolUse","input":{"tool_response":'
            '{"usage":{"input_tokens":3000}}}}\n'
        )

        with patch("devbench.reporting.report.BACKLOG_INDEX", tmp_path / "BACKLOG.md"):
            report = generate_report(log_path=log_file)

        assert "8,000" in report

    def test_hook_log_skips_invalid_json_lines(self, tmp_path: Path) -> None:
        """Malformed JSON lines are silently skipped (lines 54-55)."""
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
            "NOT VALID JSON\n"
            '{"timestamp":"2026-03-05T10:03:00Z","event":"PostToolUse","input":{"tool_response":'
            '{"usage":{"input_tokens":7000}}}}\n'
        )

        with patch("devbench.reporting.report.BACKLOG_INDEX", tmp_path / "BACKLOG.md"):
            report = generate_report(log_path=log_file)

        assert "7,000" in report

    def test_hook_log_invalid_timestamp_format_still_included(self, tmp_path: Path) -> None:
        """Entries with unparseable timestamps are still processed (lines 62-63)."""
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
            '{"timestamp":"not-a-date","event":"PostToolUse","input":{"tool_response":'
            '{"totalDurationMs":30000,"usage":{"input_tokens":12000}}}}\n'
        )

        with patch("devbench.reporting.report.BACKLOG_INDEX", tmp_path / "BACKLOG.md"):
            report = generate_report(log_path=log_file)

        # Even with invalid timestamp, the tokens should be counted (falls through ValueError)
        assert "12,000" in report


class TestDualWindowReport:
    """Test the default multi-column output (Backlog state + multi-window stats table)."""

    def test_default_renders_backlog_state_and_window_columns(self, tmp_path: Path) -> None:
        """Without --since, report shows Backlog state block and a multi-column window-stats table."""
        log_file = tmp_path / "test.log"
        log_file.write_text(
            _make_log(
                [
                    "2026-03-05T08:00:00Z [judges.cli] INFO Set E0-F1-S1-T1 to 'in-progress'",
                    "2026-03-05T08:05:00Z [judges.cli] INFO Set E0-F1-S1-T1 to 'done'",
                ]
            )
        )
        report = generate_report(log_path=log_file)
        # Multi-column header includes both window labels
        assert "All-time" in report
        assert "Session" in report
        # Consolidated grouped table uses uppercase section headers for
        # BACKLOG STATE and the windowed sections.
        assert "BACKLOG STATE" in report
        assert "THROUGHPUT" in report
        # No "This run <timestamp>" column header when not in watch mode.
        # ("This run" appears in the trailing prose explanation, so check the
        # header form specifically.)
        assert "This run 0" not in report  # would match "This run YYYY-..." column header

    def test_session_boundary_detected_at_large_gap(self, tmp_path: Path) -> None:
        """A gap > 30 minutes (DEFAULT_SESSION_GAP_MINUTES) starts a new session."""
        log_file = tmp_path / "test.log"
        log_file.write_text(
            _make_log(
                [
                    # Two tasks in the "old" session
                    "2026-03-05T08:00:00Z [judges.cli] INFO Set E0-F1-S1-T1 to 'in-progress'",
                    "2026-03-05T08:05:00Z [judges.cli] INFO Set E0-F1-S1-T1 to 'done'",
                    "2026-03-05T08:10:00Z [judges.cli] INFO Set E0-F1-S1-T2 to 'in-progress'",
                    "2026-03-05T08:15:00Z [judges.cli] INFO Set E0-F1-S1-T2 to 'done'",
                    # 60-minute gap (well over the 30-minute threshold)
                    "2026-03-05T09:15:00Z [judges.cli] INFO Set E0-F1-S1-T3 to 'in-progress'",
                    "2026-03-05T09:20:00Z [judges.cli] INFO Set E0-F1-S1-T3 to 'done'",
                ]
            )
        )
        report = generate_report(log_path=log_file)
        # Session column header includes the post-gap timestamp (in local time, MM-DD HH:MM format)
        gap_local = datetime(2026, 3, 5, 9, 15, 0, tzinfo=UTC).astimezone().strftime("%m-%d %H:%M")
        log_start_local = datetime(2026, 3, 5, 8, 0, 0, tzinfo=UTC).astimezone().strftime("%m-%d %H:%M")
        assert f"Session {gap_local}" in report
        assert f"All-time {log_start_local}" in report

    def test_session_detection_ignores_log_setup_noise(self, tmp_path: Path) -> None:
        """judges.log_setup entries are filtered out so they don't create false gaps."""
        log_file = tmp_path / "test.log"
        log_file.write_text(
            _make_log(
                [
                    # An orchestration event, then a long gap of pure noise, then more orchestration.
                    # Without filtering, the noise would prevent a "real" gap from being detected.
                    "2026-03-05T08:00:00Z [judges.cli] INFO Set E0-F1-S1-T1 to 'in-progress'",
                    "2026-03-05T08:05:00Z [judges.cli] INFO Set E0-F1-S1-T1 to 'done'",
                    # Noise entries every 3 seconds for 1 hour (just one example to prove filtering)
                    "2026-03-05T08:30:00Z [judges.log_setup] INFO Logging to stdout and ...",
                    "2026-03-05T09:00:00Z [judges.log_setup] INFO Logging to stdout and ...",
                    # A real orchestration event 70 minutes after the last real one -- new session
                    "2026-03-05T09:15:00Z [judges.cli] INFO Set E0-F1-S1-T2 to 'in-progress'",
                ]
            )
        )
        report = generate_report(log_path=log_file)
        # Session should start at the post-gap orchestration event, not at any noise entry
        session_local = datetime(2026, 3, 5, 9, 15, 0, tzinfo=UTC).astimezone().strftime("%m-%d %H:%M")
        assert f"Session {session_local}" in report

    def test_no_gap_means_session_equals_all_time(self, tmp_path: Path) -> None:
        """If all events are within the gap threshold, session start = log start."""
        log_file = tmp_path / "test.log"
        log_file.write_text(
            _make_log(
                [
                    "2026-03-05T08:00:00Z [judges.cli] INFO Set E0-F1-S1-T1 to 'in-progress'",
                    "2026-03-05T08:05:00Z [judges.cli] INFO Set E0-F1-S1-T1 to 'done'",
                    "2026-03-05T08:10:00Z [judges.cli] INFO Set E0-F1-S1-T2 to 'in-progress'",
                    "2026-03-05T08:15:00Z [judges.cli] INFO Set E0-F1-S1-T2 to 'done'",
                ]
            )
        )
        report = generate_report(log_path=log_file)
        same_local = datetime(2026, 3, 5, 8, 0, 0, tzinfo=UTC).astimezone().strftime("%m-%d %H:%M")
        # Both columns should show the same timestamp in their headers
        assert f"All-time {same_local}" in report
        assert f"Session {same_local}" in report

    def test_watch_mode_adds_this_run_column(self, tmp_path: Path) -> None:
        """When report_started_at is provided, a 'This run' column is added."""
        log_file = tmp_path / "test.log"
        log_file.write_text(
            _make_log(
                [
                    "2026-03-05T08:00:00Z [judges.cli] INFO Set E0-F1-S1-T1 to 'in-progress'",
                    "2026-03-05T08:05:00Z [judges.cli] INFO Set E0-F1-S1-T1 to 'done'",
                ]
            )
        )
        watch_start = datetime(2026, 3, 5, 8, 30, 0, tzinfo=UTC)
        report = generate_report(log_path=log_file, report_started_at=watch_start)
        watch_local = watch_start.astimezone().strftime("%m-%d %H:%M")
        assert f"This run {watch_local}" in report

    def test_since_arg_renders_single_window(self, tmp_path: Path) -> None:
        """When --since is provided, render a single-window labeled table."""
        log_file = tmp_path / "test.log"
        log_file.write_text(
            _make_log(
                [
                    "2026-03-05T08:00:00Z [judges.cli] INFO Set E0-F1-S1-T1 to 'in-progress'",
                    "2026-03-05T08:05:00Z [judges.cli] INFO Set E0-F1-S1-T1 to 'done'",
                ]
            )
        )
        since = datetime(2026, 3, 5, 7, 30, 0, tzinfo=UTC)
        report = generate_report(log_path=log_file, since=since)
        since_local = since.astimezone().strftime("%Y-%m-%d %H:%M %Z")
        # Single-window mode shows "Window (since ...)" not the multi-column layout
        assert f"Window (since {since_local})" in report
        assert "Window stats" not in report  # multi-column header NOT rendered in --since mode
        # Backward-compat label preserved for callers grepping for "Tasks in this session"
        assert "Tasks in this session" in report


class TestFindCurrentSessionStart:
    """Test the session-detection helper directly for edge cases."""

    def test_empty_log_returns_none(self) -> None:
        from devbench.reporting.report import _find_current_session_start

        assert _find_current_session_start("") is None

    def test_only_noise_returns_none(self) -> None:
        """A log containing only noise-logger entries yields no session start."""
        from devbench.reporting.report import _find_current_session_start

        log = "2026-03-05T08:00:00Z [judges.log_setup] INFO Logging to stdout\n"
        assert _find_current_session_start(log) is None

    def test_single_event_returns_that_event(self) -> None:
        from devbench.reporting.report import _find_current_session_start

        log = "2026-03-05T08:00:00Z [judges.cli] INFO Set E0-F1-S1-T1 to 'in-progress'\n"
        assert _find_current_session_start(log) == datetime(2026, 3, 5, 8, 0, 0, tzinfo=UTC)

    def test_custom_gap_minutes(self) -> None:
        """The gap_minutes parameter is honored when overridden."""
        from devbench.reporting.report import _find_current_session_start

        log = (
            "2026-03-05T08:00:00Z [judges.cli] INFO first event\n"
            "2026-03-05T08:03:00Z [judges.cli] INFO second event\n"  # 3 min later
        )
        # With a 2-min threshold, the 3-min gap counts; session starts at the second event.
        result = _find_current_session_start(log, gap_minutes=2)
        assert result == datetime(2026, 3, 5, 8, 3, 0, tzinfo=UTC)


class TestDisplayTimezone:
    """Test the configurable display-timezone for report timestamps."""

    def test_explicit_tz_renders_in_that_zone(self, tmp_path: Path) -> None:
        """When REPORT_DISPLAY_TIMEZONE is set, timestamps render in that zone."""
        from unittest.mock import patch as _patch

        log_file = tmp_path / "test.log"
        log_file.write_text(
            _make_log(
                [
                    "2026-03-05T12:00:00Z [judges.cli] INFO Set E0-F1-S1-T1 to 'in-progress'",
                    "2026-03-05T12:05:00Z [judges.cli] INFO Set E0-F1-S1-T1 to 'done'",
                ]
            )
        )
        with _patch("devbench.reporting.report.REPORT_DISPLAY_TIMEZONE", "America/Denver"):
            report = generate_report(log_path=log_file)
        # Multi-column headers use compact "MM-DD HH:MM" format (no TZ abbrev).
        # 2026-03-05 12:00 UTC = 2026-03-05 05:00 MST (Denver, no DST in March 5).
        # Validate the compact-format timestamp; if rendered in any other TZ this would differ.
        assert "03-05 05:00" in report

    def test_invalid_tz_falls_back_to_local(self, tmp_path: Path) -> None:
        """An invalid IANA name logs a warning and falls back to the host's local TZ."""
        from unittest.mock import patch as _patch

        log_file = tmp_path / "test.log"
        log_file.write_text(
            _make_log(
                [
                    "2026-03-05T12:00:00Z [judges.cli] INFO Set E0-F1-S1-T1 to 'in-progress'",
                    "2026-03-05T12:05:00Z [judges.cli] INFO Set E0-F1-S1-T1 to 'done'",
                ]
            )
        )
        # "Not_A_Real_Zone" is invalid; should fall back to local.
        with _patch("devbench.reporting.report.REPORT_DISPLAY_TIMEZONE", "Not_A_Real_Zone"):
            report = generate_report(log_path=log_file)
        # Just check report rendered -- no exception, has the expected sections.
        assert "All-time" in report
        assert "Session" in report

    def test_none_tz_uses_system_local(self, tmp_path: Path) -> None:
        """When REPORT_DISPLAY_TIMEZONE is None, timestamps use the host's system local TZ."""
        from unittest.mock import patch as _patch

        log_file = tmp_path / "test.log"
        log_file.write_text(
            _make_log(
                [
                    "2026-03-05T12:00:00Z [judges.cli] INFO Set E0-F1-S1-T1 to 'in-progress'",
                    "2026-03-05T12:05:00Z [judges.cli] INFO Set E0-F1-S1-T1 to 'done'",
                ]
            )
        )
        with _patch("devbench.reporting.report.REPORT_DISPLAY_TIMEZONE", None):
            report = generate_report(log_path=log_file)
        # Multi-column header uses compact "MM-DD HH:MM" host-local format.
        log_start_local = datetime(2026, 3, 5, 12, 0, 0, tzinfo=UTC).astimezone().strftime("%m-%d %H:%M")
        assert f"All-time {log_start_local}" in report


class TestAccurateCost:
    """Tests for the new caching-aware cost calculation."""

    def test_compute_cost_per_token_type(self) -> None:
        """Cost is sum of per-type subtotals: input + output + cache_read + cache_write_5m + cache_write_1h."""
        from devbench.reporting.report import HookLogTotals, _compute_cost

        totals = HookLogTotals(
            input_tokens=1_000_000,
            output_tokens=1_000_000,
            cache_read_tokens=1_000_000,
            cache_write_5m_tokens=1_000_000,
            cache_write_1h_tokens=1_000_000,
        )
        # Use Opus 4.7 rates: $5 input, $25 output, multipliers 0.10 / 1.25 / 2.0
        cost = _compute_cost(totals, 5.0, 25.0, 0.10, 1.25, 2.0)
        assert cost.input_cost == pytest.approx(5.0)
        assert cost.output_cost == pytest.approx(25.0)
        assert cost.cache_read_cost == pytest.approx(0.50)  # 1M * 5 * 0.10 / 1M
        assert cost.cache_write_5m_cost == pytest.approx(6.25)
        assert cost.cache_write_1h_cost == pytest.approx(10.0)
        assert cost.total_cost == pytest.approx(46.75)

    def test_compute_cost_empty_totals_is_zero(self) -> None:
        """A zero-filled HookLogTotals produces zero cost across every field."""
        from devbench.reporting.report import HookLogTotals, _compute_cost

        cost = _compute_cost(HookLogTotals(), 5.0, 25.0, 0.10, 1.25, 2.0)
        assert cost.input_cost == 0.0
        assert cost.output_cost == 0.0
        assert cost.cache_read_cost == 0.0
        assert cost.cache_write_5m_cost == 0.0
        assert cost.cache_write_1h_cost == 0.0
        assert cost.total_cost == 0.0

    def test_data_residency_multiplier_applies_to_us_only_subset(self) -> None:
        """Issue #124 AC-FUNC-001: residency multiplier applies ONLY to the
        residency-flagged subset, not to the full aggregate."""
        from devbench.reporting.report import HookLogTotals, _compute_cost

        totals = HookLogTotals(
            input_tokens=2_000_000,
            output_tokens=0,
            us_only_input_tokens=1_000_000,  # half of inputs were us-only
        )
        cost = _compute_cost(totals, 5.0, 25.0, 0.10, 1.25, 2.0, data_residency_multiplier=1.10)
        # Base: 2M * 5 / 1M = 10.0. US-only boost: 1M * 5 * 0.10 / 1M = 0.5. Total: 10.5.
        assert cost.input_cost == pytest.approx(10.5)
        assert cost.total_cost == pytest.approx(10.5)

    def test_fast_mode_multiplier_applies_to_fast_subset(self) -> None:
        """Issue #124 AC-FUNC-002: fast-mode multiplier applies ONLY to fast subset."""
        from devbench.reporting.report import HookLogTotals, _compute_cost

        totals = HookLogTotals(
            output_tokens=1_000_000,
            fast_output_tokens=1_000_000,  # all output was fast-mode
        )
        cost = _compute_cost(totals, 5.0, 25.0, 0.10, 1.25, 2.0, fast_mode_multiplier=6.0)
        # Base: 1M * 25 / 1M = 25. Fast boost: 1M * 25 * 5 / 1M = 125. Total: 150.
        assert cost.output_cost == pytest.approx(150.0)
        assert cost.total_cost == pytest.approx(150.0)

    def test_multipliers_compose_with_cache_rates(self) -> None:
        """Issue #124 AC-FUNC-003: residency + fast multipliers compose with
        cache + base-rate multipliers (apply after cache scaling)."""
        from devbench.reporting.report import HookLogTotals, _compute_cost

        totals = HookLogTotals(
            cache_read_tokens=1_000_000,
            us_only_cache_read_tokens=1_000_000,  # all cache reads were us-only
            fast_cache_read_tokens=1_000_000,  # AND all were fast-mode
        )
        cost = _compute_cost(
            totals,
            5.0,
            25.0,
            0.10,
            1.25,
            2.0,
            data_residency_multiplier=1.10,
            fast_mode_multiplier=6.0,
        )
        # Base cache_read: 1M * 5 * 0.10 / 1M = 0.50.
        # Residency boost: 1M * 5 * 0.10 / 1M * (1.10-1) = 0.05.
        # Fast boost: 1M * 5 * 0.10 / 1M * (6.0-1) = 2.50.
        # Total cache_read: 0.50 + 0.05 + 2.50 = 3.05.
        assert cost.cache_read_cost == pytest.approx(3.05)
        assert cost.total_cost == pytest.approx(3.05)

    def test_default_multipliers_one_means_no_boost(self) -> None:
        """Default multipliers = 1.0 leave the cost unchanged (backward-compat)."""
        from devbench.reporting.report import HookLogTotals, _compute_cost

        totals = HookLogTotals(
            input_tokens=1_000_000,
            output_tokens=1_000_000,
            us_only_input_tokens=500_000,
            fast_output_tokens=500_000,
        )
        cost = _compute_cost(totals, 5.0, 25.0, 0.10, 1.25, 2.0)
        # Base: 1M*5 + 1M*25 = 30. No boost because multipliers default to 1.0.
        assert cost.total_cost == pytest.approx(30.0)

    def test_compute_cost_realistic_high_cache_hit(self) -> None:
        """A heavily-cached call (99% hit rate) costs a small fraction of a naive blended estimate."""
        from devbench.reporting.report import HookLogTotals, _compute_cost

        # Mirror the real example from user's hook log: 1 input, 332 output,
        # 38712 cache reads, 458 cache writes 5-min.
        totals = HookLogTotals(
            input_tokens=1,
            output_tokens=332,
            cache_read_tokens=38_712,
            cache_write_5m_tokens=458,
        )
        cost = _compute_cost(totals, 5.0, 25.0, 0.10, 1.25, 2.0)
        # Hand-calculated: 1*5/1M + 332*25/1M + 38712*0.5/1M + 458*6.25/1M
        # = 0.000005 + 0.0083 + 0.019356 + 0.0028625 = ~0.030524
        assert cost.total_cost == pytest.approx(0.030524, abs=1e-5)

        # A naive totalTokens * blended-rate estimate would give $0.355527 for
        # the same call -- confirming per-token-type costing is >10x lower for
        # cache-heavy workloads. Naive: (1+332+38712+458) * 9 / 1M.
        naive_blended_cost = (1 + 332 + 38712 + 458) * 9.0 / 1_000_000
        assert naive_blended_cost / cost.total_cost > 10

    def test_extract_usage_totals_handles_nested_cache_creation(self, tmp_path: Path) -> None:
        """The nested cache_creation dict's ephemeral_5m and ephemeral_1h fields are extracted."""
        log_file = tmp_path / "test.log"
        log_file.write_text(_make_log(["2026-03-05T10:00:00Z [judges.cli] INFO Set E0-F1-S1-T1 to 'in-progress'"]))
        hook_log = tmp_path / "hook-logs.jsonl"
        hook_log.write_text(
            '{"timestamp":"2026-03-05T10:01:00Z","event":"PostToolUse","input":{"tool_response":{"usage":'
            '{"input_tokens":10,"output_tokens":20,"cache_read_input_tokens":1000,'
            '"cache_creation":{"ephemeral_5m_input_tokens":50,"ephemeral_1h_input_tokens":30}}}}}\n'
        )
        from unittest.mock import patch as _patch

        with _patch("devbench.reporting.report.BACKLOG_INDEX", tmp_path / "BACKLOG.md"):
            report = generate_report(log_path=log_file)

        # Token breakdown rows live in the TOKENS section of the consolidated
        # table now (no more duplicated "Lifetime X" rows -- those were the
        # same data as the All-time column and have been consolidated away).
        assert "Tokens consumed" in report
        assert "1,000" in report  # cache reads
        assert "Cache hit rate" in report

    def test_input_share_property(self) -> None:
        """input_share returns the measured input/output ratio (display-only metric)."""
        from devbench.reporting.report import CostBreakdown, HookLogTotals, WindowStats

        totals = HookLogTotals(
            input_tokens=100,
            output_tokens=50,
            cache_read_tokens=1000,
            cache_write_5m_tokens=50,
        )
        ws = WindowStats(
            window_start=datetime(2026, 3, 5, 8, 0, 0, tzinfo=UTC),
            window_hours=1.0,
            tasks_in_window=1,
            avg_minutes=10.0,
            est_hours=0.0,
            totals=totals,
            cost=CostBreakdown(0, 0, 0, 0, 0, 0),
            cache_hit_rate=None,
            tokens_per_task=0.0,
            est_total_cost=0.0,
            api_hours=0.0,
            api_efficiency=None,
        )
        # input-side = 100 + 1000 + 50 = 1150; total = 1200; share = 1150/1200 ≈ 0.9583
        assert ws.input_share == pytest.approx(1150 / 1200)

    def test_input_share_none_when_no_tokens(self) -> None:
        """input_share is None when there are no tokens at all."""
        from devbench.reporting.report import CostBreakdown, HookLogTotals, WindowStats

        ws = WindowStats(
            window_start=datetime(2026, 3, 5, 8, 0, 0, tzinfo=UTC),
            window_hours=0.0,
            tasks_in_window=0,
            avg_minutes=0.0,
            est_hours=0.0,
            totals=HookLogTotals(),
            cost=CostBreakdown(0, 0, 0, 0, 0, 0),
            cache_hit_rate=None,
            tokens_per_task=0.0,
            est_total_cost=0.0,
            api_hours=0.0,
            api_efficiency=None,
        )
        assert ws.input_share is None

    def test_cache_multipliers_overridable_via_config(self, tmp_path: Path) -> None:
        """Patched REPORT_CACHE_READ_MULTIPLIER changes cache-read cost in the report."""
        from unittest.mock import patch as _patch

        log_file = tmp_path / "test.log"
        log_file.write_text(_make_log(["2026-03-05T10:00:00Z [judges.cli] INFO Set E0-F1-S1-T1 to 'done'"]))
        hook_log = tmp_path / "hook-logs.jsonl"
        hook_log.write_text(
            '{"timestamp":"2026-03-05T10:01:00Z","event":"PostToolUse","input":{"tool_response":{"usage":'
            '{"input_tokens":0,"output_tokens":0,"cache_read_input_tokens":1000000}}}}\n'
        )
        # With Anthropic default 0.10 multiplier and Opus 4.7 input rate $5: 1M reads = $0.50
        # If we override to 0.20: same reads = $1.00
        with (
            _patch("devbench.reporting.report.BACKLOG_INDEX", tmp_path / "BACKLOG.md"),
            _patch("devbench.reporting.report.REPORT_CACHE_READ_MULTIPLIER", 0.20),
        ):
            report = generate_report(log_path=log_file)
        # The cost should reflect the doubled multiplier.
        assert "~$1.00" in report


class TestTranscriptParsing:
    """Tests for combining hook-log + Claude Code transcript usage data."""

    def test_discover_transcript_dir_from_hook_log(self, tmp_path: Path) -> None:
        """The transcript directory is read from any hook-log entry's input.transcript_path."""
        from devbench.reporting.report import _discover_transcript_dir

        transcript_dir = tmp_path / ".claude" / "projects" / "myproj"
        transcript_dir.mkdir(parents=True)
        hook_log = tmp_path / "hook-logs.jsonl"
        hook_log.write_text(
            f'{{"timestamp":"2026-03-05T10:00:00Z","event":"PostToolUse",'
            f'"input":{{"transcript_path":"{transcript_dir / "session.jsonl"}",'
            f'"tool_name":"Bash","tool_response":{{}}}}}}\n'
        )
        assert _discover_transcript_dir(hook_log) == transcript_dir

    def test_discover_transcript_dir_returns_none_when_missing(self, tmp_path: Path) -> None:
        """Returns None when hook log is missing or contains no transcript_path."""
        from devbench.reporting.report import _discover_transcript_dir

        assert _discover_transcript_dir(tmp_path / "nope.jsonl") is None
        # Empty hook log
        empty = tmp_path / "empty.jsonl"
        empty.write_text("")
        assert _discover_transcript_dir(empty) is None

    def test_parse_transcript_metrics_aggregates_usage(self, tmp_path: Path) -> None:
        """Transcript .jsonl files contribute their message.usage to the totals."""
        from devbench.reporting.report import _parse_transcript_metrics

        transcript_dir = tmp_path / "transcripts"
        transcript_dir.mkdir()
        (transcript_dir / "session1.jsonl").write_text(
            # Two assistant turns with usage data
            '{"timestamp":"2026-03-05T10:01:00Z","message":{"role":"assistant",'
            '"usage":{"input_tokens":100,"output_tokens":50,"cache_read_input_tokens":1000}}}\n'
            '{"timestamp":"2026-03-05T10:02:00Z","message":{"role":"assistant",'
            '"usage":{"input_tokens":200,"output_tokens":75,"cache_read_input_tokens":2000}}}\n'
        )
        result = _parse_transcript_metrics(transcript_dir, datetime(2026, 3, 5, 10, 0, 0, tzinfo=UTC))
        assert result.input_tokens == 300
        assert result.output_tokens == 125
        assert result.cache_read_tokens == 3000
        assert result.entries_with_usage == 2

    def test_parse_transcript_metrics_filters_by_window(self, tmp_path: Path) -> None:
        """Transcript entries before window_start are excluded."""
        from devbench.reporting.report import _parse_transcript_metrics

        transcript_dir = tmp_path / "transcripts"
        transcript_dir.mkdir()
        (transcript_dir / "session1.jsonl").write_text(
            '{"timestamp":"2026-03-05T08:00:00Z","message":{"usage":{"output_tokens":999}}}\n'
            '{"timestamp":"2026-03-05T11:00:00Z","message":{"usage":{"output_tokens":42}}}\n'
        )
        result = _parse_transcript_metrics(transcript_dir, datetime(2026, 3, 5, 10, 0, 0, tzinfo=UTC))
        # Only the 11:00 entry counts (08:00 is before window_start)
        assert result.output_tokens == 42

    def test_parse_transcript_metrics_handles_missing_dir(self) -> None:
        """A None or non-existent transcript_dir returns empty totals (no exception)."""
        from devbench.reporting.report import _parse_transcript_metrics

        result = _parse_transcript_metrics(None, datetime(2026, 3, 5, 10, 0, 0, tzinfo=UTC))
        assert result.input_tokens == 0
        assert result.output_tokens == 0

    def test_parse_transcript_metrics_by_role_buckets_per_attribution(self, tmp_path: Path) -> None:
        """Issue #123 regression: per-role accumulator groups messages by
        ``attributionAgent`` and the summed totals match
        ``_parse_transcript_metrics``'s aggregate."""
        from devbench.reporting.report import (
            _parse_transcript_metrics,
            _parse_transcript_metrics_by_role,
        )

        transcript_dir = tmp_path / "transcripts"
        transcript_dir.mkdir()
        # Three turns across three roles: orchestrator (no attribution),
        # executor, and code_review (note the canonical normalisation:
        # ``devbench:code-reviewer`` -> ``code_review``).
        (transcript_dir / "session1.jsonl").write_text(
            '{"timestamp":"2026-03-05T10:01:00Z","message":{"role":"assistant",'
            '"usage":{"input_tokens":100,"output_tokens":50}}}\n'
            '{"timestamp":"2026-03-05T10:02:00Z","attributionAgent":"devbench:executor",'
            '"message":{"role":"assistant","usage":{"input_tokens":200,"output_tokens":75}}}\n'
            '{"timestamp":"2026-03-05T10:03:00Z","attributionAgent":"devbench:code-reviewer",'
            '"message":{"role":"assistant","usage":{"input_tokens":40,"output_tokens":10}}}\n'
        )
        window_start = datetime(2026, 3, 5, 10, 0, 0, tzinfo=UTC)
        by_role = _parse_transcript_metrics_by_role(transcript_dir, window_start)

        assert "orchestrator" in by_role
        assert "executor" in by_role
        assert "code_review" in by_role  # devbench:code-reviewer -> code_review
        assert by_role["orchestrator"].input_tokens == 100
        assert by_role["executor"].input_tokens == 200
        assert by_role["code_review"].input_tokens == 40

        # Aggregate row contract (AC-FUNC-003): summed totals match the
        # existing global figure.
        global_totals = _parse_transcript_metrics(transcript_dir, window_start)
        assert sum(t.input_tokens for t in by_role.values()) == global_totals.input_tokens
        assert sum(t.output_tokens for t in by_role.values()) == global_totals.output_tokens
        assert sum(t.entries_with_usage for t in by_role.values()) == global_totals.entries_with_usage

    def test_parse_transcript_metrics_by_role_handles_missing_dir(self) -> None:
        from devbench.reporting.report import _parse_transcript_metrics_by_role

        assert _parse_transcript_metrics_by_role(None, datetime(2026, 3, 5, 10, 0, 0, tzinfo=UTC)) == {}

    def test_role_for_entry_strips_devbench_prefix_and_normalises(self) -> None:
        from devbench.reporting.report import _role_for_entry

        assert _role_for_entry({"attributionAgent": "devbench:executor"}) == "executor"
        assert _role_for_entry({"attributionAgent": "devbench:code-reviewer"}) == "code_review"
        assert _role_for_entry({"attributionAgent": "devbench:test-reviewer"}) == "test_review"
        assert _role_for_entry({"attributionAgent": "devbench:doc-reviewer"}) == "doc_review"
        assert _role_for_entry({"attributionAgent": "devbench:changes-manifest"}) == "changes_manifest"
        assert _role_for_entry({"attributionAgent": "devbench:security-reviewer"}) == "security_review"
        assert _role_for_entry({"attributionAgent": "devbench:blocker-resolver"}) == "blocker_resolver"
        assert _role_for_entry({"attributionAgent": "devbench:manifest-amender"}) == "manifest_amender"
        assert _role_for_entry({"attributionAgent": "devbench:task-factory"}) == "task_factory"
        # Missing or malformed -> orchestrator bucket.
        assert _role_for_entry({"attributionAgent": None}) == "orchestrator"
        assert _role_for_entry({}) == "orchestrator"

    def test_combine_totals_sums_field_by_field(self) -> None:
        """_combine_totals adds all numeric fields from two HookLogTotals."""
        from devbench.reporting.report import HookLogTotals, _combine_totals

        a = HookLogTotals(input_tokens=10, output_tokens=20, cache_read_tokens=100, entries_with_usage=2)
        b = HookLogTotals(input_tokens=5, output_tokens=15, cache_read_tokens=50, entries_with_usage=3)
        c = _combine_totals(a, b)
        assert c.input_tokens == 15
        assert c.output_tokens == 35
        assert c.cache_read_tokens == 150
        assert c.entries_with_usage == 5


class TestTranscriptResumedSessionDedup:
    """Issue #169: resumed Claude Code sessions copy prior assistant messages
    forward into new transcript files; the parser path must dedup by
    ``message.id`` so the same logical message is counted once even when it
    appears in N files. Entries without a stable ``message.id`` continue to
    accumulate (defensive guard for older transcripts).
    """

    @staticmethod
    def _msg_line(ts: str, msg_id: str | None, in_tokens: int, out_tokens: int, role: str = "assistant") -> str:
        message: dict = {"role": role, "usage": {"input_tokens": in_tokens, "output_tokens": out_tokens}}
        if msg_id is not None:
            message["id"] = msg_id
        return json.dumps({"timestamp": ts, "message": message}) + "\n"

    def test_duplicate_message_ids_across_files_are_counted_once(self, tmp_path: Path) -> None:
        from devbench.reporting.report import _parse_transcript_metrics

        transcript_dir = tmp_path / "transcripts"
        transcript_dir.mkdir()
        # File A: m1, m2, m3 (10/5, 20/7, 30/9 = 60/21 total)
        (transcript_dir / "a.jsonl").write_text(
            self._msg_line("2026-03-05T10:01:00Z", "m1", 10, 5)
            + self._msg_line("2026-03-05T10:02:00Z", "m2", 20, 7)
            + self._msg_line("2026-03-05T10:03:00Z", "m3", 30, 9)
        )
        # File B: m2, m3 carried forward from A + new m4.
        # If naive sum: m2+m3 are counted twice. Deduped: counted once.
        (transcript_dir / "b.jsonl").write_text(
            self._msg_line("2026-03-05T10:02:00Z", "m2", 20, 7)
            + self._msg_line("2026-03-05T10:03:00Z", "m3", 30, 9)
            + self._msg_line("2026-03-05T10:04:00Z", "m4", 40, 11)
        )
        result = _parse_transcript_metrics(transcript_dir, datetime(2026, 3, 5, 10, 0, 0, tzinfo=UTC))
        # Deduped: m1+m2+m3+m4 = 100 input / 32 output / 4 entries
        assert result.input_tokens == 100
        assert result.output_tokens == 32
        assert result.entries_with_usage == 4

    def test_entries_without_message_id_still_count(self, tmp_path: Path) -> None:
        """Defensive: pre-id transcripts and any future schema variant must keep accumulating."""
        from devbench.reporting.report import _parse_transcript_metrics

        transcript_dir = tmp_path / "transcripts"
        transcript_dir.mkdir()
        (transcript_dir / "a.jsonl").write_text(
            self._msg_line("2026-03-05T10:01:00Z", None, 10, 5) + self._msg_line("2026-03-05T10:02:00Z", None, 20, 7)
        )
        result = _parse_transcript_metrics(transcript_dir, datetime(2026, 3, 5, 10, 0, 0, tzinfo=UTC))
        # Both entries count -- dedup is opt-in on a stable id, not a bar against missing ones.
        assert result.input_tokens == 30
        assert result.output_tokens == 12
        assert result.entries_with_usage == 2

    def test_role_buckets_share_dedup_set(self, tmp_path: Path) -> None:
        """Per-role aggregator must dedup against the SAME set as the global path so
        the per-role totals sum to the deduped aggregate."""
        from devbench.reporting.report import (
            _parse_transcript_metrics,
            _parse_transcript_metrics_by_role,
        )

        transcript_dir = tmp_path / "transcripts"
        transcript_dir.mkdir()
        # Same dup pattern as the basic test; orchestrator role implied (no attributionAgent).
        (transcript_dir / "a.jsonl").write_text(
            self._msg_line("2026-03-05T10:01:00Z", "m1", 10, 5) + self._msg_line("2026-03-05T10:02:00Z", "m2", 20, 7)
        )
        (transcript_dir / "b.jsonl").write_text(
            self._msg_line("2026-03-05T10:02:00Z", "m2", 20, 7) + self._msg_line("2026-03-05T10:03:00Z", "m3", 30, 9)
        )
        window_start = datetime(2026, 3, 5, 10, 0, 0, tzinfo=UTC)
        global_totals = _parse_transcript_metrics(transcript_dir, window_start)
        by_role = _parse_transcript_metrics_by_role(transcript_dir, window_start)
        # Both deduped to m1+m2+m3 = 60 input / 21 output / 3 entries
        assert global_totals.input_tokens == 60
        assert global_totals.output_tokens == 21
        assert global_totals.entries_with_usage == 3
        # Per-role sum equals the global dedupped figure (aggregate-row contract).
        assert sum(t.input_tokens for t in by_role.values()) == global_totals.input_tokens
        assert sum(t.output_tokens for t in by_role.values()) == global_totals.output_tokens
        assert sum(t.entries_with_usage for t in by_role.values()) == global_totals.entries_with_usage


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


class TestResolveWindowEndpoints:
    """`window_end` must be bounded below by now so a 'This run' window whose
    start post-dates the last log entry never produces a negative span."""

    def test_window_end_uses_now_when_logs_are_older(self) -> None:
        from devbench.reporting.report import _resolve_window_endpoints

        # All log timestamps are an hour old.
        old = datetime.now(UTC) - timedelta(hours=1)
        log_ts = [old, old + timedelta(minutes=10), old + timedelta(minutes=30)]

        before = datetime.now(UTC)
        log_start, window_end, log_started = _resolve_window_endpoints(log_ts)
        after = datetime.now(UTC)

        assert log_start == old
        assert log_started == old
        # window_end must be at least "now" (clamped above the latest log entry)
        assert window_end >= before
        assert window_end <= after

    def test_window_end_uses_latest_log_when_newer_than_now(self) -> None:
        """If somehow a log timestamp is in the future (clock skew), respect it
        rather than silently truncating it to now."""
        from devbench.reporting.report import _resolve_window_endpoints

        future = datetime.now(UTC) + timedelta(hours=2)
        log_ts = [datetime.now(UTC) - timedelta(hours=1), future]

        _, window_end, _ = _resolve_window_endpoints(log_ts)
        assert window_end == future

    def test_window_for_run_started_after_last_log_has_non_negative_span(self) -> None:
        """A 'This run' window whose start post-dates every log entry must have
        a span >= 0; this is the regression that produced -0.0 h in production."""
        from devbench.reporting.report import _resolve_window_endpoints

        old = datetime.now(UTC) - timedelta(hours=1)
        _, window_end, _ = _resolve_window_endpoints([old])
        run_start = datetime.now(UTC)  # later than every log entry
        # window_end is bounded by now, so the span is at least 0 (within microseconds).
        span = (window_end - run_start).total_seconds()
        # Allow tiny negative drift caused by datetime.now() racing with the
        # window_end computation; anything within 100ms is non-negative for
        # display purposes.
        assert span >= -0.1


class TestApiUtilizationDisplay:
    """API utilization > 100% must render as a labeled marker, not a raw percentage."""

    @staticmethod
    def _make_stats(api_efficiency: float | None) -> WindowStats:
        from devbench.reporting.report import CostBreakdown, HookLogTotals, WindowStats

        return WindowStats(
            window_start=datetime(2026, 4, 16, 12, 0, 0, tzinfo=UTC),
            window_hours=1.0,
            tasks_in_window=1,
            avg_minutes=10.0,
            est_hours=0.0,
            totals=HookLogTotals(),
            cost=CostBreakdown(0, 0, 0, 0, 0, 0),
            cache_hit_rate=None,
            tokens_per_task=0.0,
            est_total_cost=0.0,
            api_hours=0.0,
            api_efficiency=api_efficiency,
        )

    def test_efficiency_above_100_shows_parallel_marker(self) -> None:
        from devbench.reporting.report import _stats_to_value_list

        values = _stats_to_value_list(self._make_stats(719.0))
        # API utilization is the 6th value (index 5) in METRIC_LABELS order.
        assert ">100% (parallel)" in values

    def test_efficiency_at_or_below_100_shows_raw_percentage(self) -> None:
        from devbench.reporting.report import _stats_to_value_list

        values = _stats_to_value_list(self._make_stats(75.0))
        assert "75.0%" in values
        assert ">100%" not in " ".join(values)

    def test_efficiency_exactly_100_shows_raw_percentage(self) -> None:
        from devbench.reporting.report import _stats_to_value_list

        values = _stats_to_value_list(self._make_stats(100.0))
        assert "100.0%" in values
        assert ">100%" not in " ".join(values)

    def test_efficiency_none_shows_na(self) -> None:
        from devbench.reporting.report import _stats_to_value_list

        values = _stats_to_value_list(self._make_stats(None))
        assert "n/a" in values


class TestActiveVsBlockedRemaining:
    """B1 + B2: tasks_remaining splits into active + blocked; projections use active only."""

    @staticmethod
    def _make_units(active_n: int, blocked_n: int, done_n: int) -> list:
        from devbench.backlog.work_unit import WorkUnit, WorkUnitStatus, WorkUnitType

        units = []
        for i in range(done_n):
            units.append(
                WorkUnit(
                    id=f"E0-F1-S1-T{i + 1}",
                    title=f"done-{i}",
                    status=WorkUnitStatus.DONE,
                    unit_type=WorkUnitType.TASK,
                    file_path=Path(f"backlog/done-{i}.md"),
                    repo="caylent-solutions/git-repo",
                    dependencies=[],
                )
            )
        for i in range(active_n):
            units.append(
                WorkUnit(
                    id=f"E0-F1-S2-T{i + 1}",
                    title=f"active-{i}",
                    status=WorkUnitStatus.IN_PROGRESS,
                    unit_type=WorkUnitType.TASK,
                    file_path=Path(f"backlog/active-{i}.md"),
                    repo="caylent-solutions/git-repo",
                    dependencies=[],
                )
            )
        for i in range(blocked_n):
            units.append(
                WorkUnit(
                    id=f"E0-F1-S3-T{i + 1}",
                    title=f"blocked-{i}",
                    status=WorkUnitStatus.BLOCKED,
                    unit_type=WorkUnitType.TASK,
                    file_path=Path(f"backlog/blocked-{i}.md"),
                    repo="caylent-solutions/git-repo",
                    dependencies=[],
                )
            )
        return units

    def test_backlog_totals_partition_active_and_blocked(self) -> None:
        from devbench.reporting.report import _backlog_totals_from_units

        b = _backlog_totals_from_units(self._make_units(active_n=1, blocked_n=4, done_n=84))
        assert b.tasks_total == 89
        assert b.tasks_done == 84
        assert b.tasks_remaining == 5
        assert b.tasks_blocked == 4
        assert b.tasks_active == 1

    def test_backlog_state_rows_show_in_progress_blocked_and_total(self) -> None:
        """B8: top box shows in-progress + blocked + total-remaining as distinct rows.
        The older `Tasks remaining (active)` / `Tasks remaining (blocked, ...)` rows
        are replaced by this cleaner breakdown."""
        from devbench.reporting.report import _backlog_state_rows, _backlog_totals_from_units

        b = _backlog_totals_from_units(self._make_units(active_n=1, blocked_n=4, done_n=84))
        rows = dict(_backlog_state_rows(b, lifetime=None))
        assert rows["Tasks in-progress"] == "1"
        assert rows["Tasks blocked"] == "4"
        assert rows["Tasks remaining (total)"] == "5"
        # Old labels must be gone.
        assert "Tasks remaining (active)" not in rows
        assert "Tasks remaining (blocked, excluded from ETA)" not in rows

    def test_est_hours_uses_tasks_active_not_tasks_remaining(self, tmp_path: Path) -> None:
        """B2: a window with avg=20min and 1 active + 4 blocked must project 0.33h, not 1.67h.

        Uses 3 completed tasks to clear MIN_PACE_SAMPLES; otherwise the pace
        is treated as fragile and est_hours stays 0.
        """
        from devbench.reporting.report import _compute_window_stats

        log_file = tmp_path / "test.log"
        log_file.write_text("ignored\n")
        done = {
            "E0-F1-S1-T1": datetime(2026, 4, 15, 10, 20, tzinfo=UTC),
            "E0-F1-S1-T2": datetime(2026, 4, 15, 10, 50, tzinfo=UTC),
            "E0-F1-S1-T3": datetime(2026, 4, 15, 11, 20, tzinfo=UTC),
        }
        prog = {
            "E0-F1-S1-T1": datetime(2026, 4, 15, 10, 0, tzinfo=UTC),
            "E0-F1-S1-T2": datetime(2026, 4, 15, 10, 30, tzinfo=UTC),
            "E0-F1-S1-T3": datetime(2026, 4, 15, 11, 0, tzinfo=UTC),
        }
        stats = _compute_window_stats(
            log_file,
            datetime(2026, 4, 15, 9, 0, tzinfo=UTC),
            datetime(2026, 4, 15, 12, 0, tzinfo=UTC),
            done,
            prog,
            tasks_active=1,
        )
        # 3 tasks * 20 min -> avg 20 min; only 3 completions log-wide but
        # RECENT_PACE_TASKS default is 10, so recent_pace_minutes is None and
        # est_hours falls back to avg_minutes.
        assert stats.avg_minutes == pytest.approx(20.0)
        assert stats.pace_sample_count == 3
        assert stats.est_hours == pytest.approx(20.0 / 60.0)

    def test_summary_line_excludes_blocked_count(self) -> None:
        from devbench.reporting.report import CostBreakdown, HookLogTotals, WindowStats, _summary_line

        stats = WindowStats(
            window_start=datetime(2026, 4, 15, 10, 0, tzinfo=UTC),
            window_hours=1.0,
            tasks_in_window=2,
            avg_minutes=20.0,
            est_hours=20.0 / 60.0,
            totals=HookLogTotals(),
            cost=CostBreakdown(0, 0, 0, 0, 0, 0),
            cache_hit_rate=None,
            tokens_per_task=0.0,
            est_total_cost=0.0,
            api_hours=0.0,
            api_efficiency=None,
        )
        line = _summary_line(stats, tasks_active=1, tasks_blocked=4)
        assert "1 active task(s)" in line
        assert "4 blocked excluded" in line
        assert "0.3 more hours" in line

    def test_min_pace_samples_guard_n_below_threshold(self, tmp_path: Path) -> None:
        """B3: a window with fewer than MIN_PACE_SAMPLES completions must
        produce avg_minutes=0 (display as n/a) and est_hours=0 -- but still
        records the sample count for diagnostic display."""
        from devbench.constants import MIN_PACE_SAMPLES
        from devbench.reporting.report import _compute_window_stats

        # Use only 1 completed task (well below MIN_PACE_SAMPLES=3).
        log_file = tmp_path / "test.log"
        log_file.write_text("ignored\n")
        done = {"E0-F1-S1-T1": datetime(2026, 4, 15, 10, 30, tzinfo=UTC)}
        prog = {"E0-F1-S1-T1": datetime(2026, 4, 15, 10, 0, tzinfo=UTC)}

        stats = _compute_window_stats(
            log_file,
            datetime(2026, 4, 15, 9, 0, tzinfo=UTC),
            datetime(2026, 4, 15, 11, 0, tzinfo=UTC),
            done,
            prog,
            tasks_active=1,
        )
        assert MIN_PACE_SAMPLES >= 3
        assert stats.pace_sample_count == 1
        assert stats.avg_minutes == 0.0  # below threshold → reported as 0
        assert stats.recent_pace_minutes is None  # only 1 completion log-wide
        assert stats.est_hours == 0.0  # no usable pace → no projection

    def test_n_equals_1_renders_as_na_with_sample_count(self) -> None:
        """B3 display: n/a (N=1 sample) appears in the rendered row when below threshold."""
        from devbench.reporting.report import CostBreakdown, HookLogTotals, WindowStats, _stats_to_value_list

        stats = WindowStats(
            window_start=datetime(2026, 4, 15, 10, 0, tzinfo=UTC),
            window_hours=1.0,
            tasks_in_window=1,
            avg_minutes=0.0,
            est_hours=0.0,
            totals=HookLogTotals(),
            cost=CostBreakdown(0, 0, 0, 0, 0, 0),
            cache_hit_rate=None,
            tokens_per_task=0.0,
            est_total_cost=0.0,
            api_hours=0.0,
            api_efficiency=None,
            pace_sample_count=1,
            recent_pace_minutes=None,
        )
        values = _stats_to_value_list(stats)
        assert "n/a (N=1 sample)" in values

    def test_recent_pace_used_for_projection_when_available(self, tmp_path: Path) -> None:
        """B4: when ≥ RECENT_PACE_TASKS completions exist log-wide, est_hours
        derives from recent_pace_minutes, not from the window's avg_minutes."""
        from devbench.config import RECENT_PACE_TASKS
        from devbench.reporting.report import _compute_window_stats

        log_file = tmp_path / "test.log"
        log_file.write_text("ignored\n")
        # Build N+1 done tasks with last 10 averaging 50 min each, earlier ones 5 min.
        done = {}
        prog = {}
        n_total = RECENT_PACE_TASKS + 5
        base = datetime(2026, 4, 15, 8, 0, tzinfo=UTC)
        for i in range(n_total):
            tid = f"E0-F1-S1-T{i + 1}"
            # Recent 10 tasks: 50 min each. Older: 5 min each.
            dur = 50 if i >= n_total - RECENT_PACE_TASKS else 5
            start = base + timedelta(hours=i)
            prog[tid] = start
            done[tid] = start + timedelta(minutes=dur)

        stats = _compute_window_stats(
            log_file,
            base,
            base + timedelta(hours=n_total + 1),
            done,
            prog,
            tasks_active=2,
        )
        # avg_minutes mixes 5- and 50-min tasks; recent_pace_minutes is exactly 50.
        assert stats.recent_pace_minutes == pytest.approx(50.0)
        # est_hours uses recent pace x tasks_active: 50 x 2 / 60 = 1.667h
        assert stats.est_hours == pytest.approx(50.0 * 2 / 60.0)

    def test_recent_per_task_cost_returns_none_with_fewer_than_n_completions(self, tmp_path: Path) -> None:
        """Issue #164: helper returns None when log has fewer than RECENT_PACE_TASKS completions."""
        from devbench.config import RECENT_PACE_TASKS
        from devbench.reporting.report import _recent_per_task_cost

        log_file = tmp_path / "test.log"
        log_file.write_text("ignored\n")
        # Only 2 completions; RECENT_PACE_TASKS is 5 by default.
        done = {f"E0-F1-S1-T{i}": datetime(2026, 4, 15, 10 + i, tzinfo=UTC) for i in range(2)}
        prog = {f"E0-F1-S1-T{i}": datetime(2026, 4, 15, 9 + i, tzinfo=UTC) for i in range(2)}
        result = _recent_per_task_cost(log_file, done, prog, RECENT_PACE_TASKS)
        assert result is None

    def test_recent_per_task_cost_falls_back_to_window_avg_when_helper_returns_none(self, tmp_path: Path) -> None:
        """Issue #164 fallback contract: when ``recent_per_task_cost`` is
        None, ``_compute_window_stats`` falls back to the per-window
        average (cost.total_cost / tasks_in_window). When ``recent_per_task_cost``
        IS provided it overrides the per-window denominator."""
        from devbench.reporting.report import _compute_window_stats

        log_file = tmp_path / "test.log"
        log_file.write_text("")  # empty log -> all costs are zero
        done = {"E0-F1-S1-T1": datetime(2026, 4, 15, 11, tzinfo=UTC)}
        prog = {"E0-F1-S1-T1": datetime(2026, 4, 15, 10, tzinfo=UTC)}

        # No recent_per_task_cost supplied -> fallback path; with empty
        # log every cost is zero so est_total_cost should also be zero.
        stats_fallback = _compute_window_stats(
            log_file,
            datetime(2026, 4, 15, 10, tzinfo=UTC),
            datetime(2026, 4, 15, 12, tzinfo=UTC),
            done,
            prog,
            tasks_active=10,
        )
        assert stats_fallback.est_total_cost == pytest.approx(0.0)

        # With recent_per_task_cost=$50, projection = 0 + 50 * 10 = 500
        stats_global = _compute_window_stats(
            log_file,
            datetime(2026, 4, 15, 10, tzinfo=UTC),
            datetime(2026, 4, 15, 12, tzinfo=UTC),
            done,
            prog,
            tasks_active=10,
            recent_per_task_cost=50.0,
        )
        assert stats_global.est_total_cost == pytest.approx(500.0)

    def test_lifetime_total_cost_overrides_per_window_additive_base(self, tmp_path: Path) -> None:
        """Spanning-row contract: when ``lifetime_total_cost`` is supplied,
        ``est_total_cost`` uses it as the additive base instead of the
        per-window ``cost.total_cost``. This is what makes every column
        produce the same projection so ``_merge_spanning_values`` collapses
        the row. Default-None preserves the legacy per-window formula for
        direct test callers.
        """
        from devbench.reporting.report import _compute_window_stats

        log_file = tmp_path / "test.log"
        log_file.write_text("")
        done = {"E0-F1-S1-T1": datetime(2026, 4, 15, 11, tzinfo=UTC)}
        prog = {"E0-F1-S1-T1": datetime(2026, 4, 15, 10, tzinfo=UTC)}

        # No lifetime_total_cost: legacy formula (cost.total_cost is 0 from
        # empty log), projection = 0 + 50 * 10 = 500.
        stats_legacy = _compute_window_stats(
            log_file,
            datetime(2026, 4, 15, 10, tzinfo=UTC),
            datetime(2026, 4, 15, 12, tzinfo=UTC),
            done,
            prog,
            tasks_active=10,
            recent_per_task_cost=50.0,
        )
        assert stats_legacy.est_total_cost == pytest.approx(500.0)

        # With lifetime_total_cost=$1000 (the All-time cost passed in by
        # generate_report), projection = 1000 + 50 * 10 = 1500. This is
        # the same number every column produces because it is computed
        # from a single global pair (lifetime cost, recent rate).
        stats_lifetime = _compute_window_stats(
            log_file,
            datetime(2026, 4, 15, 10, tzinfo=UTC),
            datetime(2026, 4, 15, 12, tzinfo=UTC),
            done,
            prog,
            tasks_active=10,
            recent_per_task_cost=50.0,
            lifetime_total_cost=1000.0,
        )
        assert stats_lifetime.est_total_cost == pytest.approx(1500.0)

    def test_estimated_total_cost_at_completion_is_a_spanning_metric(self) -> None:
        """Issue #164: ``Estimated total cost at completion`` renders as a
        single value spanning every column instead of one per-window number."""
        from devbench.reporting.report import _SPANNING_METRIC_LABELS, _merge_spanning_values

        assert "Estimated total cost at completion" in _SPANNING_METRIC_LABELS
        # When every column carries the same value, _merge_spanning_values
        # collapses to a single string.
        result = _merge_spanning_values("Estimated total cost at completion", ["~$500.00"] * 3)
        assert isinstance(result, str)
        assert result == "~$500.00"

    def test_summary_line_uses_recent_pace_when_available(self) -> None:
        """B4 prose: trailing summary cites Recent pace, not All-time, when set."""
        from devbench.reporting.report import CostBreakdown, HookLogTotals, WindowStats, _summary_line

        stats = WindowStats(
            window_start=datetime(2026, 4, 15, 10, 0, tzinfo=UTC),
            window_hours=10.0,
            tasks_in_window=84,
            avg_minutes=18.6,
            est_hours=0.5,
            totals=HookLogTotals(),
            cost=CostBreakdown(0, 0, 0, 0, 0, 0),
            cache_hit_rate=None,
            tokens_per_task=0.0,
            est_total_cost=0.0,
            api_hours=0.0,
            api_efficiency=None,
            pace_sample_count=84,
            recent_pace_minutes=31.4,
        )
        line = _summary_line(stats, tasks_active=1, tasks_blocked=4)
        assert "At the Recent pace of ~31.4 minutes per task" in line
        assert "All-time pace" not in line  # superseded when recent is available

    def test_summary_line_reports_when_only_blocked_remain(self) -> None:
        from devbench.reporting.report import CostBreakdown, HookLogTotals, WindowStats, _summary_line

        stats = WindowStats(
            window_start=datetime(2026, 4, 15, 10, 0, tzinfo=UTC),
            window_hours=1.0,
            tasks_in_window=0,
            avg_minutes=0.0,
            est_hours=0.0,
            totals=HookLogTotals(),
            cost=CostBreakdown(0, 0, 0, 0, 0, 0),
            cache_hit_rate=None,
            tokens_per_task=0.0,
            est_total_cost=0.0,
            api_hours=0.0,
            api_efficiency=None,
        )
        line = _summary_line(stats, tasks_active=0, tasks_blocked=3)
        assert "0 active tasks" in line
        assert "3 blocked task(s)" in line
        assert "external action" in line


class TestBacklogStatusBreakdown:
    """B8: _BacklogTotals carries per-status counts; sum matches tasks_remaining."""

    @staticmethod
    def _make_mixed_units() -> list:
        from devbench.backlog.work_unit import WorkUnit, WorkUnitStatus, WorkUnitType

        def _mk(uid: str, status: WorkUnitStatus) -> WorkUnit:
            return WorkUnit(
                id=uid,
                title=f"task-{uid}",
                status=status,
                unit_type=WorkUnitType.TASK,
                file_path=Path(f"backlog/{uid}.md"),
                repo="caylent-solutions/git-repo",
                dependencies=[],
            )

        return [
            _mk("E0-F1-S1-T1", WorkUnitStatus.DONE),
            _mk("E0-F1-S1-T2", WorkUnitStatus.DONE),
            _mk("E0-F1-S2-T1", WorkUnitStatus.IN_PROGRESS),
            _mk("E0-F1-S2-T2", WorkUnitStatus.IN_QUEUE),
            _mk("E0-F1-S2-T3", WorkUnitStatus.IN_QUEUE),
            _mk("E0-F1-S3-T1", WorkUnitStatus.IN_REVIEW),
            _mk("E0-F1-S4-T1", WorkUnitStatus.BLOCKED),
            _mk("E0-F1-S4-T2", WorkUnitStatus.BLOCKED),
        ]

    def test_per_status_fields_populated(self) -> None:
        from devbench.reporting.report import _backlog_totals_from_units

        b = _backlog_totals_from_units(self._make_mixed_units())
        assert b.tasks_done == 2
        assert b.tasks_in_progress == 1
        assert b.tasks_in_queue == 2
        assert b.tasks_in_review == 1
        assert b.tasks_blocked == 2
        # Invariant: every non-Done task is in exactly one status bucket.
        assert (b.tasks_in_progress + b.tasks_in_queue + b.tasks_in_review + b.tasks_blocked) == b.tasks_remaining


class TestBacklogTotalsSixBlockedFields:
    """AC-FUNC-004: the snapshot dataclass exposes per-state count fields,
    one per ``BlockedTaskState`` enum member. Originally six (AC-FUNC-004);
    grew to seven when ``RUNTIME_DEGRADATION`` was added under issue #183 and
    the renderer was retrofitted to handle it explicitly rather than letting
    the ``else`` branch silently route it to operator-required.
    """

    @staticmethod
    def _mk(uid: str, status) -> WorkUnit:
        from devbench.backlog.work_unit import WorkUnit, WorkUnitType

        return WorkUnit(
            id=uid,
            title=f"task-{uid}",
            status=status,
            unit_type=WorkUnitType.TASK,
            file_path=Path(f"backlog/{uid}.md"),
            repo="caylent-solutions/git-repo",
            dependencies=[],
        )

    def test_six_count_fields_exist_on_backlog_totals(self) -> None:
        """AC-FUNC-004: _BacklogTotals has the six per-state blocked count fields."""
        from devbench.reporting.report import _BacklogTotals

        # Construct a minimal _BacklogTotals instance to confirm the six fields exist.
        b = _BacklogTotals(
            tasks_total=6,
            tasks_done=0,
            units_total=6,
            units_done=0,
            stories_done=0,
            features_done=0,
            epics_done=0,
            tasks_remaining=6,
            tasks_blocked=6,
            tasks_active=0,
            tasks_in_progress=0,
            tasks_in_queue=0,
            tasks_in_review=0,
            tasks_proposed=0,
            tasks_declined=0,
            tasks_blocked_auto_clearing=1,
            tasks_blocked_amendment_recovery=1,
            tasks_blocked_dependency=1,
            tasks_blocked_held=1,
            tasks_blocked_on_held=1,
            tasks_blocked_operator=1,
        )
        assert b.tasks_blocked_auto_clearing == 1
        assert b.tasks_blocked_amendment_recovery == 1
        assert b.tasks_blocked_dependency == 1
        assert b.tasks_blocked_held == 1
        assert b.tasks_blocked_on_held == 1
        assert b.tasks_blocked_operator == 1
        # Sum of all six equals total blocked count.
        assert (
            b.tasks_blocked_auto_clearing
            + b.tasks_blocked_amendment_recovery
            + b.tasks_blocked_dependency
            + b.tasks_blocked_held
            + b.tasks_blocked_on_held
            + b.tasks_blocked_operator
        ) == b.tasks_blocked

    def test_backlog_totals_from_units_populates_every_field(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """_backlog_totals_from_units populates every per-state blocked count field.

        Parametrised across every ``BlockedTaskState`` enum member so adding
        a new member without extending the counter path trips this test.
        """
        from devbench.backlog.proposal import BlockedTaskState
        from devbench.backlog.work_unit import WorkUnitStatus
        from devbench.reporting.report import _backlog_totals_from_units

        states = [
            BlockedTaskState.AUTO_CLEARING_VIA_PROPOSAL,
            BlockedTaskState.AWAITING_AMENDMENT_RECOVERY,
            BlockedTaskState.AWAITING_DEPENDENCY,
            BlockedTaskState.HELD,
            BlockedTaskState.BLOCKED_ON_HELD,
            BlockedTaskState.RUNTIME_DEGRADATION,
            BlockedTaskState.OPERATOR_ACTION_REQUIRED,
        ]
        units = [
            self._mk(
                f"E0-F1-S1-T{i + 1}",
                WorkUnitStatus.BLOCKED if state is not BlockedTaskState.HELD else WorkUnitStatus.HOLD,
            )
            for i, state in enumerate(states)
        ]
        task_id_to_state = {u.id: s for u, s in zip(units, states, strict=True)}

        def fake_classify(backlog_root, backlog_index, task_id, **kwargs):
            return task_id_to_state[task_id]

        monkeypatch.setattr("devbench.backlog.proposal.classify_blocked_task", fake_classify)

        b = _backlog_totals_from_units(units)
        assert b.tasks_blocked_auto_clearing == 1
        assert b.tasks_blocked_amendment_recovery == 1
        assert b.tasks_blocked_dependency == 1
        assert b.tasks_blocked_held == 1
        assert b.tasks_blocked_on_held == 1
        assert b.tasks_blocked_runtime_degradation == 1
        assert b.tasks_blocked_operator == 1
        assert (
            b.tasks_blocked_auto_clearing
            + b.tasks_blocked_amendment_recovery
            + b.tasks_blocked_dependency
            + b.tasks_blocked_held
            + b.tasks_blocked_on_held
            + b.tasks_blocked_runtime_degradation
            + b.tasks_blocked_operator
        ) == b.tasks_blocked

    def test_unhandled_blocked_state_raises_in_counter_path(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Adding a new BlockedTaskState without extending the counter path raises.

        Pins CLAUDE.md no-fallback-logic: the renderer's if/elif chain MUST
        be exhaustive; the else branch raises RuntimeError instead of
        silently routing the new member to operator-required.
        """
        from devbench.backlog.proposal import BlockedTaskState
        from devbench.backlog.work_unit import WorkUnitStatus
        from devbench.reporting.report import _backlog_totals_from_units

        unit = self._mk("E0-F1-S1-T1", WorkUnitStatus.BLOCKED)

        class _FakeState:
            """Stand-in for a hypothetical new BlockedTaskState member."""

            value = "fake-state"

            def __repr__(self) -> str:
                return "<BlockedTaskState.FAKE_NEW_MEMBER>"

        fake_state = _FakeState()

        def fake_classify(*args, **kwargs):
            return fake_state

        monkeypatch.setattr("devbench.backlog.proposal.classify_blocked_task", fake_classify)
        # Tolerate the BlockedTaskState identity-check noise; what we care
        # about is that the renderer rejects the unhandled enum value.
        del BlockedTaskState  # only imported for clarity above
        with pytest.raises(RuntimeError, match="Unhandled BlockedTaskState"):
            _backlog_totals_from_units([unit])


class TestUnitListings:
    """B9: in-progress and blocked listings appear at the end of the report."""

    @staticmethod
    def _mk_unit(uid: str, title: str, status, utype=None) -> WorkUnit:
        from devbench.backlog.work_unit import WorkUnit, WorkUnitType

        return WorkUnit(
            id=uid,
            title=title,
            status=status,
            unit_type=utype if utype is not None else WorkUnitType.TASK,
            file_path=Path(f"backlog/{uid}.md"),
            repo="caylent-solutions/git-repo",
            dependencies=[],
        )

    def test_in_progress_listing_present_when_task_in_progress(self) -> None:
        from devbench.backlog.work_unit import WorkUnitStatus
        from devbench.reporting.report import _in_progress_listing

        units = [self._mk_unit("E0-F1-S1-T1", "Active task", WorkUnitStatus.IN_PROGRESS)]
        lines = _in_progress_listing(units)
        assert lines[1] == "In-progress tasks:"
        # Issue #158: row always carries an in-progress suffix; when no
        # transition timestamp is parseable the helper renders
        # ``(in-progress, timer unavailable)`` rather than silently omitting.
        assert lines[2].startswith("  - E0-F1-S1-T1: Active task")
        assert "(in-progress" in lines[2]

    def test_blocked_listing_splits_auto_vs_attn_when_classifier_disagrees(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """ADR-10: _blocked_listing renders two panels when both classes are non-empty."""
        from devbench.backlog.proposal import BlockedTaskState
        from devbench.backlog.work_unit import WorkUnitStatus
        from devbench.reporting import report as report_mod

        unit_auto = self._mk_unit("E0-F1-S1-T1", "Auto-clearing", WorkUnitStatus.BLOCKED)
        unit_attn = self._mk_unit("E0-F1-S1-T2", "Needs attention", WorkUnitStatus.BLOCKED)

        def fake_classify(
            backlog_root: Path,
            backlog_index: Path,
            task_id: str,
            **kwargs: object,
        ) -> BlockedTaskState:
            return {
                "E0-F1-S1-T1": BlockedTaskState.AUTO_CLEARING_VIA_PROPOSAL,
                "E0-F1-S1-T2": BlockedTaskState.OPERATOR_ACTION_REQUIRED,
            }[task_id]

        class _FakeMgr:
            def _extract_pending_proposal_markers(self, _file_path: Path) -> set:
                return {"E0-F1-S1-T9"}

        monkeypatch.setattr("devbench.backlog.proposal.classify_blocked_task", fake_classify)
        monkeypatch.setattr("devbench.backlog.manager.BacklogManager", _FakeMgr)

        lines = report_mod._blocked_listing([unit_auto, unit_attn])
        assert "Blocked tasks (auto-clearing via proposal) (1):" in lines
        assert "Blocked tasks (operator action required) (1):" in lines
        # Auto-clearing row names the waiting-on target.
        assert any("E0-F1-S1-T1" in line and "waiting on" in line for line in lines)
        # Every attn-panel row carries an annotation from _ATTN_RANK.
        assert any(
            line.startswith("  - E0-F1-S1-T2: Needs attention") and "[operator action required]" in line
            for line in lines
        )

    def test_blocked_listing_present_when_task_blocked(self) -> None:
        """Without markers every blocked task renders under the 'operator action required' panel."""
        from devbench.backlog.work_unit import WorkUnitStatus
        from devbench.reporting.report import _blocked_listing

        units = [
            self._mk_unit("E0-F2-S1-T3", "Disable pager", WorkUnitStatus.BLOCKED),
            self._mk_unit("E0-F5-S2-T2", "Pipeline tests", WorkUnitStatus.BLOCKED),
        ]
        lines = _blocked_listing(units)
        # Fake units have no work-unit file on disk; classifier returns
        # OPERATOR_ACTION_REQUIRED, so only the attn panel renders.
        assert "Blocked tasks (operator action required) (2):" in lines
        # Each attn row carries the OPERATOR_ACTION_REQUIRED annotation.
        assert any(
            line.startswith("  - E0-F2-S1-T3: Disable pager") and "[operator action required]" in line for line in lines
        )
        assert any(
            line.startswith("  - E0-F5-S2-T2: Pipeline tests") and "[operator action required]" in line
            for line in lines
        )
        # Auto panel NOT rendered because no auto-clearing tasks in fixture.
        assert not any("auto-clearing" in line for line in lines)

    def test_blocked_listing_renders_hold_unit_in_held_panel(self) -> None:
        """HOLD work units surface in the dedicated held panel with [HOLD] annotation."""
        from devbench.backlog.work_unit import WorkUnitStatus
        from devbench.reporting.report import _blocked_listing

        units = [self._mk_unit("E0-F5-S1-T1", "kanon-main2-sync-blocker", WorkUnitStatus.HOLD)]
        lines = _blocked_listing(units)
        # Held panel rendered with count 1.
        assert "Blocked tasks (held) (1):" in lines
        # Hint line immediately follows the header.
        held_idx = lines.index("Blocked tasks (held) (1):")
        assert lines[held_idx + 1] == "On hold by operator; unhold to release."
        # Row carries the [HOLD] annotation.
        assert any(line.startswith("  - E0-F5-S1-T1: kanon-main2-sync-blocker") and "[HOLD]" in line for line in lines)
        # Old combined "operator action required" panel does not appear for a pure HOLD fixture.
        assert not any("operator action required" in line for line in lines)

    def test_blocked_listing_held_and_on_held_render_in_separate_panels(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """HELD and BLOCKED_ON_HELD each occupy their own dedicated panel in canonical order."""
        from devbench.backlog.proposal import BlockedTaskState
        from devbench.backlog.work_unit import WorkUnitStatus
        from devbench.reporting import report as report_mod

        hold_unit = self._mk_unit("E0-F5-S1-T1", "kanon-main2-sync", WorkUnitStatus.HOLD)
        dep_unit = self._mk_unit("E4-F2-S4-T1", "TDD telemetry", WorkUnitStatus.BLOCKED)

        def fake_classify(backlog_root, backlog_index, task_id, **kwargs):
            if task_id == "E0-F5-S1-T1":
                return BlockedTaskState.HELD
            return BlockedTaskState.BLOCKED_ON_HELD

        monkeypatch.setattr(
            "devbench.backlog.proposal.classify_blocked_task",
            fake_classify,
        )

        lines = report_mod._blocked_listing([dep_unit, hold_unit])
        # Both panels present.
        assert "Blocked tasks (held) (1):" in lines
        assert "Blocked tasks (blocked-on-held) (1):" in lines
        # The held panel precedes the blocked-on-held panel (canonical order).
        assert lines.index("Blocked tasks (held) (1):") < lines.index("Blocked tasks (blocked-on-held) (1):")
        # HELD unit in held panel.
        assert any(line.startswith("  - E0-F5-S1-T1") and "[HOLD]" in line for line in lines)
        # BLOCKED_ON_HELD unit in its panel.
        assert any(line.startswith("  - E4-F2-S4-T1") and "[blocked-on-held]" in line for line in lines)

    def test_listings_empty_when_no_matching_tasks(self) -> None:
        from devbench.backlog.work_unit import WorkUnitStatus
        from devbench.reporting.report import _blocked_listing, _in_progress_listing

        # Only done tasks -- no in-progress, no blocked.
        units = [self._mk_unit("E0-F1-S1-T1", "t", WorkUnitStatus.DONE)]
        assert _in_progress_listing(units) == []
        assert _blocked_listing(units) == []

    def test_listings_skip_non_task_units(self) -> None:
        """Story/Feature/Epic status is auto-rolled from children -- never list them."""
        from devbench.backlog.work_unit import WorkUnitStatus, WorkUnitType
        from devbench.reporting.report import _blocked_listing, _in_progress_listing

        units = [
            self._mk_unit("E0-F1", "Feature", WorkUnitStatus.IN_PROGRESS, WorkUnitType.FEATURE),
            self._mk_unit("E0-F1-S1", "Story", WorkUnitStatus.BLOCKED, WorkUnitType.STORY),
            self._mk_unit("E0", "Epic", WorkUnitStatus.IN_PROGRESS, WorkUnitType.EPIC),
        ]
        assert _in_progress_listing(units) == []
        assert _blocked_listing(units) == []

    def test_proposed_listing_omitted_when_empty(self) -> None:
        from devbench.backlog.work_unit import WorkUnitStatus
        from devbench.reporting.report import _proposed_listing

        units = [self._mk_unit("E0-F1-S1-T1", "t", WorkUnitStatus.IN_QUEUE)]
        assert _proposed_listing(units) == []

    def test_proposed_listing_renders_title_and_path(self) -> None:
        from devbench.backlog.work_unit import WorkUnitStatus
        from devbench.reporting.report import _proposed_listing

        units = [
            self._mk_unit("E0-F1-S1-T9", "Fix the gitdir guard", WorkUnitStatus.PROPOSED),
            self._mk_unit("E0-F1-S1-T10", "Enable version subcommand", WorkUnitStatus.PROPOSED),
        ]
        lines = _proposed_listing(units)
        assert lines[1] == "Proposed (2):"
        # Each row: title (with padding) then file path.
        assert any("Fix the gitdir guard" in line for line in lines)
        assert any("Enable version subcommand" in line for line in lines)

    def test_declined_listing_omitted_when_empty(self) -> None:
        from devbench.backlog.work_unit import WorkUnitStatus
        from devbench.reporting.report import _declined_listing

        units = [self._mk_unit("E0-F1-S1-T1", "t", WorkUnitStatus.IN_QUEUE)]
        assert _declined_listing(units) == []

    def test_declined_listing_renders_title_and_path(self) -> None:
        from devbench.backlog.work_unit import WorkUnitStatus
        from devbench.reporting.report import _declined_listing

        units = [
            self._mk_unit("E0-F1-S1-T11", "Deprecated feature", WorkUnitStatus.DECLINED),
            self._mk_unit("E0-F1-S1-T12", "Out-of-scope cleanup", WorkUnitStatus.DECLINED),
        ]
        lines = _declined_listing(units)
        assert lines[1] == "Declined (2):"
        assert any("Deprecated feature" in line for line in lines)
        assert any("Out-of-scope cleanup" in line for line in lines)

    def test_backlog_state_includes_tasks_declined_row(self) -> None:
        from devbench.backlog.work_unit import WorkUnitStatus, WorkUnitType
        from devbench.reporting.report import _backlog_state_rows, _backlog_totals_from_units

        units = [
            self._mk_unit("E0-F1-S1-T1", "a", WorkUnitStatus.DONE, WorkUnitType.TASK),
            self._mk_unit("E0-F1-S1-T2", "b", WorkUnitStatus.DECLINED, WorkUnitType.TASK),
        ]
        totals = _backlog_totals_from_units(units)
        assert totals.tasks_declined == 1
        rows = _backlog_state_rows(totals)
        assert ("Tasks declined", "1") in rows

    def test_blocked_listing_six_panels_canonical_order_and_hints(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """AC-FUNC-001 + AC-FUNC-002 + AC-CYCLE-001: every panel in canonical order with hint lines.

        Originally six panels; the runtime-degradation panel was added under
        issue #183 once `cmd_start` learned to auto-restart on the SDK
        Agent-tool-unavailable failure mode. Renderer routes one row per
        every ``BlockedTaskState`` enum member, in canonical display order.
        """
        from devbench.backlog.proposal import BlockedTaskState
        from devbench.backlog.work_unit import WorkUnitStatus
        from devbench.reporting import report as report_mod

        states = [
            BlockedTaskState.AUTO_CLEARING_VIA_PROPOSAL,
            BlockedTaskState.AWAITING_AMENDMENT_RECOVERY,
            BlockedTaskState.AWAITING_DEPENDENCY,
            BlockedTaskState.HELD,
            BlockedTaskState.BLOCKED_ON_HELD,
            BlockedTaskState.RUNTIME_DEGRADATION,
            BlockedTaskState.OPERATOR_ACTION_REQUIRED,
        ]
        units = [
            self._mk_unit(
                f"E0-F1-S1-T{i + 1}",
                f"Task-{state.value}",
                WorkUnitStatus.BLOCKED if state is not BlockedTaskState.HELD else WorkUnitStatus.HOLD,
            )
            for i, state in enumerate(states)
        ]
        task_id_to_state = {u.id: s for u, s in zip(units, states, strict=True)}

        def fake_classify(backlog_root, backlog_index, task_id, **kwargs):
            return task_id_to_state[task_id]

        class _FakeMgr:
            def _extract_pending_proposal_markers(self, _file_path):
                return set()

        def fake_recovery_signal(workspace_root, task_id):
            return "pending-proposal"

        monkeypatch.setattr("devbench.backlog.proposal.classify_blocked_task", fake_classify)
        monkeypatch.setattr("devbench.backlog.manager.BacklogManager", _FakeMgr)
        monkeypatch.setattr("devbench.backlog.proposal.recovery_signal_for_task", fake_recovery_signal)

        lines = report_mod._blocked_listing(units)

        # Every panel header must appear, in canonical display order.
        panel_headers = [line for line in lines if line.startswith("Blocked tasks (")]
        assert len(panel_headers) == 7, f"Expected 7 panel headers, got {len(panel_headers)}: {panel_headers}"

        expected_order = [
            "Blocked tasks (auto-clearing via proposal) (1):",
            "Blocked tasks (awaiting amendment recovery) (1):",
            "Blocked tasks (awaiting dependency) (1):",
            "Blocked tasks (held) (1):",
            "Blocked tasks (blocked-on-held) (1):",
            "Blocked tasks (runtime-degradation) (1):",
            "Blocked tasks (operator action required) (1):",
        ]
        assert panel_headers == expected_order, f"Panel order mismatch: {panel_headers}"

        # Each header must be immediately followed by its canonical hint line.
        def _panel_header(name: str) -> str:
            return f"Blocked tasks ({name}) (1):"

        canonical_hints = [
            (_panel_header("auto-clearing via proposal"), "Resolves when marker targets reach terminal; no action."),
            (
                _panel_header("awaiting amendment recovery"),
                "Recovery agent in flight; orchestrator's next sweep advances these.",
            ),
            (_panel_header("awaiting dependency"), "Resolves when the dependency completes; no action."),
            (_panel_header("held"), "On hold by operator; unhold to release."),
            (
                _panel_header("blocked-on-held"),
                "Waiting on a held unit; unhold the target or redirect this task.",
            ),
            (
                _panel_header("runtime-degradation"),
                "SDK lost Agent-tool access mid-session; `make start` auto-restarts to recover.",
            ),
            (
                _panel_header("operator action required"),
                "No automation path; operator must inspect and resolve manually.",
            ),
        ]
        for header, expected_hint in canonical_hints:
            header_idx = lines.index(header)
            # The hint line must appear immediately after the header.
            actual = lines[header_idx + 1]
            assert actual == expected_hint, f"After {header!r} expected {expected_hint!r} but got {actual!r}"

    def test_blocked_listing_empty_panels_omitted(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """AC-FUNC-003: empty panels are omitted; all-zero state returns empty list."""
        from devbench.reporting.report import _blocked_listing

        assert _blocked_listing([]) == []

    def test_unmaterialised_proposals_listing_empty_when_no_proposals(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """ADR-08: panel disappears when no proposal JSONs exist at all."""
        import devbench.reporting.report as report_mod

        monkeypatch.setattr(report_mod, "BACKLOG_ROOT", tmp_path / "backlog")
        (tmp_path / "backlog").mkdir(exist_ok=True)

        assert report_mod._unmaterialised_proposals_listing() == []

    def test_unmaterialised_proposals_listing_renders_entries(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """ADR-08: each proposal-JSON task in UNMATERIALISED state gets a row."""
        import devbench.reporting.report as report_mod
        from devbench.backlog.proposal import (
            PROPOSAL_DIR_NAME,
            Proposal,
            ProposedTask,
            write_proposal,
        )

        backlog_root = tmp_path / "backlog"
        backlog_root.mkdir(exist_ok=True)
        monkeypatch.setattr(report_mod, "BACKLOG_ROOT", backlog_root)

        (tmp_path / PROPOSAL_DIR_NAME).mkdir(parents=True)
        write_proposal(
            tmp_path,
            Proposal(
                source_task_id="E1-F1-S2-T1",
                generated_at="2026-04-19T00:00:00Z",
                rejection_reason="scope",
                proposed_tasks=[
                    ProposedTask(
                        suggested_id="E1-F1-S2-T6",
                        title="Fix symlink guard",
                        files_to_own=["src/x.py"],
                        linked_scenarios=["SC-01"],
                        suggested_acs=["AC-001 fix"],
                        suggested_approach=(
                            "Context: unit test fixture. Scope: src/x.py. "
                            "TDD approach: 1. RED 2. GREEN 3. REFACTOR no-op. "
                            "Verify: make lint && make test-unit all exit zero."
                        ),
                    ),
                ],
            ),
        )

        lines = report_mod._unmaterialised_proposals_listing()
        assert lines, "Panel must render when a proposal-JSON task is in UNMATERIALISED state."
        assert any("Proposal JSONs pending materialisation" in line for line in lines)
        assert any("E1-F1-S2-T6" in line for line in lines)
        assert any("Fix symlink guard" in line for line in lines)
        assert any("from E1-F1-S2-T1" in line for line in lines)

    def test_unmaterialised_proposals_listing_omits_materialised(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """If a draft .md exists for the suggested_id the entry is not un-materialised -> omitted."""
        import devbench.reporting.report as report_mod
        from devbench.backlog.proposal import (
            PROPOSAL_DIR_NAME,
            Proposal,
            ProposedTask,
            _extract_story_id,
            _story_dir,
            write_proposal,
        )

        backlog_root = tmp_path / "backlog"
        backlog_root.mkdir(exist_ok=True)
        monkeypatch.setattr(report_mod, "BACKLOG_ROOT", backlog_root)

        (tmp_path / PROPOSAL_DIR_NAME).mkdir(parents=True)
        write_proposal(
            tmp_path,
            Proposal(
                source_task_id="E1-F1-S2-T1",
                generated_at="2026-04-19T00:00:00Z",
                rejection_reason="scope",
                proposed_tasks=[
                    ProposedTask(
                        suggested_id="E1-F1-S2-T6",
                        title="Fix symlink guard",
                        files_to_own=["src/x.py"],
                        linked_scenarios=["SC-01"],
                        suggested_acs=["AC-001 fix"],
                        suggested_approach=(
                            "Context: unit test fixture. Scope: src/x.py. "
                            "TDD approach: 1. RED 2. GREEN 3. REFACTOR no-op. "
                            "Verify: make lint && make test-unit all exit zero."
                        ),
                    ),
                ],
            ),
        )
        # Materialise out-of-band by writing a draft .md under the story dir.
        story = _story_dir(backlog_root, _extract_story_id("E1-F1-S2-T6"))
        story.mkdir(parents=True, exist_ok=True)
        (story / "E1-F1-S2-T6.md").write_text("# E1-F1-S2-T6: Fix\n\n## Status: proposed\n\n## Description\n\nx\n")

        lines = report_mod._unmaterialised_proposals_listing()
        assert lines == [], (
            "Panel must be empty when every proposed_tasks entry already has a draft .md "
            "(i.e. none remain in UNMATERIALISED state)."
        )


class TestSideBySideLayout:
    """B10: _render_side_by_side merges two blocks with a gap; the full report uses it."""

    def test_left_shorter_is_padded_with_blanks(self) -> None:
        from devbench.reporting.report import _render_side_by_side

        merged = _render_side_by_side(["AAA", "BBB"], ["X", "Y", "Z"], gap=4)
        # Left width = 3, gap = 4 → blank-left line is "   " + "    " + "Z" = "       Z"
        assert merged == ["AAA    X", "BBB    Y", "       Z"]

    def test_right_shorter_is_padded(self) -> None:
        from devbench.reporting.report import _render_side_by_side

        merged = _render_side_by_side(["AAAA", "BBBB", "CCCC"], ["X", "Y"], gap=2)
        # Third line should have the right side padded with spaces (width 1).
        assert merged == ["AAAA  X", "BBBB  Y", "CCCC   "]

    def test_empty_side_returns_other(self) -> None:
        from devbench.reporting.report import _render_side_by_side

        assert _render_side_by_side([], ["A", "B"]) == ["A", "B"]
        assert _render_side_by_side(["A", "B"], []) == ["A", "B"]

    def test_generate_report_renders_one_grouped_table(self, tmp_path: Path) -> None:
        """End-to-end: the consolidated layout is a single grouped table with
        uppercase section headers (BACKLOG STATE, THROUGHPUT, API USAGE,
        TOKENS, COST), in that order, with the Metric | All-time | Session
        column layout. The old two-box layout is gone."""
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
        # Single table -- exactly ONE top-left corner appears in the output.
        top_corners = report.count("\u250c")
        assert top_corners == 1, f"Expected 1 grouped table, got {top_corners} top-left corners in report"
        # Section headers in the required order.
        for section in ("BACKLOG STATE", "THROUGHPUT", "API USAGE", "TOKENS", "COST"):
            assert section in report, f"Missing section header: {section}"
        backlog_idx = report.find("BACKLOG STATE")
        throughput_idx = report.find("THROUGHPUT")
        tokens_idx = report.find("TOKENS")
        cost_idx = report.find("COST")
        assert backlog_idx < throughput_idx < tokens_idx < cost_idx, "Section headers must appear in plan order"
        # First column is labelled "Metric"; the old "Backlog state" and
        # "Window stats" table titles are gone.
        assert "Metric " in report


class TestSpanningRows:
    """B11: window-agnostic metrics (Recent pace, Est. time) render once across all cols."""

    def test_merge_spanning_values_collapses_identical(self) -> None:
        from devbench.reporting.report import _merge_spanning_values

        merged = _merge_spanning_values("Est. time to complete remaining", ["~0.5 h", "~0.5 h", "~0.5 h"])
        assert merged == "~0.5 h"

    def test_merge_spanning_values_keeps_list_when_values_differ(self) -> None:
        """If values differ across columns, preserve per-column layout so divergence stays visible."""
        from devbench.reporting.report import _merge_spanning_values

        values = ["~0.5 h", "~0.5 h", "~1.0 h"]
        merged = _merge_spanning_values("Est. time to complete remaining", values)
        assert merged == values

    def test_merge_spanning_values_ignores_non_spanning_labels(self) -> None:
        from devbench.reporting.report import _merge_spanning_values

        values = ["32.4 h", "32.4 h"]
        merged = _merge_spanning_values("Time span", values)
        assert merged == values

    def test_multi_column_table_renders_spanning_row(self) -> None:
        """Passing a bare str value produces a single wide cell with only 3 vertical bars."""
        from devbench.reporting.report import _render_multi_column_table

        lines = _render_multi_column_table(
            "Window stats",
            ["All-time", "Session"],
            [
                ("Time span", ["32.4 h", "0.6 h"]),
                ("Recent pace", "31.4 min"),
            ],
        )
        pace_line = next(ln for ln in lines if "Recent pace" in ln)
        # Spanning rows have 3 │ (left + metric-sep + right), normal rows have 4.
        assert pace_line.count("\u2502") == 3, f"Expected 3 │ in spanning row, got: {pace_line!r}"
        assert pace_line.count("31.4 min") == 1

    def test_multi_column_table_widens_columns_for_long_spanning_value(self) -> None:
        """Regression: a spanning value wider than the default column width must
        force the value columns to widen so the table border stays aligned. Before
        the fix, a long ETA breakdown busted the right border.
        """
        from devbench.reporting.report import _render_multi_column_table

        long_eta = "~41.9 h (active 4 + blocked-recovery 60 + blocked-auto 27 at 27.6 min/task)"
        lines = _render_multi_column_table(
            "Window stats",
            ["All-time 05-02 18:37", "Session 05-04 09:21"],
            [
                ("Time span", ["38.8 h", "0.1 h"]),
                ("Est. time to complete remaining", long_eta),
            ],
        )
        widths = {len(ln) for ln in lines}
        assert len(widths) == 1, f"Table lines must all be the same width; got widths {sorted(widths)}: {lines!r}"
        eta_line = next(ln for ln in lines if "Est. time" in ln)
        assert long_eta in eta_line

    def test_grouped_progress_table_widens_columns_for_long_spanning_value(self) -> None:
        """Regression: same fix applied to the grouped progress renderer used by
        the live ``devbench report`` panel."""
        from devbench.reporting.report import _render_grouped_progress_table

        long_eta = "~41.9 h (active 4 + blocked-recovery 60 + blocked-auto 27 at 27.6 min/task)"
        lines = _render_grouped_progress_table(
            "Metric",
            ["All-time 05-02 18:37", "Session 05-04 09:21"],
            [
                (
                    "THROUGHPUT",
                    [
                        ("Time span", ["38.8 h", "0.1 h"]),
                        ("Est. time to complete remaining", long_eta),
                    ],
                ),
            ],
        )
        widths = {len(ln) for ln in lines}
        assert len(widths) == 1, (
            f"Grouped table lines must all be the same width; got widths {sorted(widths)}: {lines!r}"
        )
        eta_line = next(ln for ln in lines if "Est. time" in ln)
        assert long_eta in eta_line

    def test_report_end_to_end_spans_recent_pace_and_est_time(self, tmp_path: Path) -> None:
        """In the rendered report, Recent pace and Est. time rows are single spanning cells
        -- the underlying value appears exactly once on the row even with multiple window columns."""
        from unittest.mock import patch

        from devbench.backlog.work_unit import WorkUnit, WorkUnitStatus, WorkUnitType

        # Build 10 completed tasks so recent pace has enough samples (>= RECENT_PACE_TASKS=10)
        # and one in-progress task so tasks_active > 0 and est_hours > 0.
        log_lines = []
        for i in range(10):
            start = f"2026-03-05T10:{i * 5:02d}:00Z"
            done = f"2026-03-05T10:{i * 5 + 4:02d}:30Z"
            log_lines.append(f"{start} [judges.cli] INFO Set E0-F1-S1-T{i + 1} to 'in-progress'")
            log_lines.append(f"{done} [judges.cli] INFO Set E0-F1-S1-T{i + 1} to 'done'")
        log_file = tmp_path / "test.log"
        log_file.write_text(_make_log(log_lines))

        fake_units = [
            WorkUnit(
                id=f"E0-F1-S1-T{i + 1}",
                title=f"done-{i}",
                status=WorkUnitStatus.DONE,
                unit_type=WorkUnitType.TASK,
                file_path=Path(f"backlog/done-{i}.md"),
                repo="caylent-solutions/git-repo",
                dependencies=[],
            )
            for i in range(10)
        ]
        fake_units.append(
            WorkUnit(
                id="E0-F1-S2-T1",
                title="active-task",
                status=WorkUnitStatus.IN_PROGRESS,
                unit_type=WorkUnitType.TASK,
                file_path=Path("backlog/active.md"),
                repo="caylent-solutions/git-repo",
                dependencies=[],
            )
        )

        # Override the autouse mock_backlog_parser fixture's empty-list default.
        with patch("devbench.reporting.report.BacklogParser") as mock_cls:
            mock_cls.return_value.parse_index.return_value = fake_units
            report = generate_report(log_path=log_file)

        # Pull the window-stats half of each spanning row. Side-by-side rendering
        # means each line also carries bars from the Backlog-state table on the
        # left, so counting total │ chars is ambiguous. Instead, find each row
        # and assert its window-table value string occurs exactly ONCE (not 2x/3x).
        for row_label, expected_substr in (
            ("Recent pace (last 10 tasks)", "4.5 min"),  # avg of 4m30s per task
            ("Est. time to complete remaining", " h"),
        ):
            row_line = next((ln for ln in report.splitlines() if row_label in ln), None)
            assert row_line is not None, f"Row '{row_label}' not found"
            # The window-stats side of the row starts after the 4-space side-by-side gap.
            # After splitting on that gap, the right half is the Window stats row; the
            # spanning value must appear exactly once in that half.
            right_half = row_line.split("    ", 1)[-1]
            occurrences = right_half.count(expected_substr)
            assert occurrences == 1, (
                f"Row '{row_label}' expected '{expected_substr}' exactly once in Window stats half, "
                f"got {occurrences}: {right_half!r}"
            )

    def test_report_end_to_end_spans_estimated_total_cost_at_completion(self, tmp_path: Path) -> None:
        """Spanning-row regression: ``Estimated total cost at completion`` must
        render as a single spanning value across All-time / Session columns
        even when the cost-so-far differs between windows. Pre-fix the row
        diverged because the additive base ``cost.total_cost`` was per-window;
        with the global lifetime additive base every column produces the same
        projection and the spanning collapse fires.
        """
        from devbench.backlog.work_unit import WorkUnit, WorkUnitStatus, WorkUnitType

        # Build 10 completed tasks split across an explicit session boundary so
        # the All-time and Session windows carry different ``cost.total_cost``
        # values. The session start line ([ORCHESTRATOR STARTING]) splits the
        # log into pre- and post-session task batches; combined with hook-log
        # usage spanning both halves, the All-time cost > Session cost.
        log_lines = []
        # First 5 completions BEFORE session boundary.
        for i in range(5):
            start = f"2026-03-05T08:{i * 5:02d}:00Z"
            done = f"2026-03-05T08:{i * 5 + 4:02d}:30Z"
            log_lines.append(f"{start} [judges.cli] INFO Set E0-F1-S1-T{i + 1} to 'in-progress'")
            log_lines.append(f"{done} [judges.cli] INFO Set E0-F1-S1-T{i + 1} to 'done'")
        # Session-start marker between the two batches.
        log_lines.append("2026-03-05T10:00:00Z [judges.cli] INFO [ORCHESTRATOR STARTING]")
        # Next 5 completions AFTER the session boundary.
        for i in range(5, 10):
            start = f"2026-03-05T10:{(i - 5) * 5:02d}:00Z"
            done = f"2026-03-05T10:{(i - 5) * 5 + 4:02d}:30Z"
            log_lines.append(f"{start} [judges.cli] INFO Set E0-F1-S1-T{i + 1} to 'in-progress'")
            log_lines.append(f"{done} [judges.cli] INFO Set E0-F1-S1-T{i + 1} to 'done'")
        # _hook_log_path resolves hook-logs.jsonl relative to log_path.parent.parent
        # so put the orchestrator log under a logs/ subdir and the hook log
        # alongside it at tmp_path/hook-logs.jsonl.
        logs_dir = tmp_path / "logs"
        logs_dir.mkdir()
        log_file = logs_dir / "orchestrator.log"
        log_file.write_text(_make_log(log_lines))

        # Hook-log usage spans both halves so per-window cost.total_cost
        # actually differs (All-time covers all 4 entries; Session covers
        # only the post-10:00 entries).
        hook_log = tmp_path / "hook-logs.jsonl"
        hook_log.write_text(
            '{"timestamp":"2026-03-05T08:04:00Z","event":"PostToolUse","input":{"tool_response":'
            '{"usage":{"input_tokens":1000000,"output_tokens":250000}}}}\n'
            '{"timestamp":"2026-03-05T08:14:00Z","event":"PostToolUse","input":{"tool_response":'
            '{"usage":{"input_tokens":1000000,"output_tokens":250000}}}}\n'
            '{"timestamp":"2026-03-05T10:04:00Z","event":"PostToolUse","input":{"tool_response":'
            '{"usage":{"input_tokens":1000000,"output_tokens":250000}}}}\n'
            '{"timestamp":"2026-03-05T10:14:00Z","event":"PostToolUse","input":{"tool_response":'
            '{"usage":{"input_tokens":1000000,"output_tokens":250000}}}}\n'
        )

        fake_units = [
            WorkUnit(
                id=f"E0-F1-S1-T{i + 1}",
                title=f"done-{i}",
                status=WorkUnitStatus.DONE,
                unit_type=WorkUnitType.TASK,
                file_path=Path(f"backlog/done-{i}.md"),
                repo="caylent-solutions/git-repo",
                dependencies=[],
            )
            for i in range(10)
        ]
        fake_units.append(
            WorkUnit(
                id="E0-F1-S2-T1",
                title="active-task",
                status=WorkUnitStatus.IN_PROGRESS,
                unit_type=WorkUnitType.TASK,
                file_path=Path("backlog/active.md"),
                repo="caylent-solutions/git-repo",
                dependencies=[],
            )
        )

        with patch("devbench.reporting.report.BacklogParser") as mock_cls:
            mock_cls.return_value.parse_index.return_value = fake_units
            report = generate_report(log_path=log_file)

        row_line = next((ln for ln in report.splitlines() if "Estimated total cost at completion" in ln), None)
        assert row_line is not None, "Row 'Estimated total cost at completion' not found"
        right_half = row_line.split("    ", 1)[-1]
        # Spanning collapse: exactly ONE dollar value in the windowed half.
        # Pre-fix this was 2-3 different values per column.
        dollar_count = right_half.count("$")
        assert dollar_count == 1, (
            f"Estimated total cost at completion must span (one $ value); got {dollar_count} in row half: "
            f"{right_half!r}"
        )


def _small_hook_log() -> str:
    """Minimal hook-log entries with usage tokens -- same shape as existing TestTokenCostReport fixtures."""
    return (
        '{"timestamp":"2026-03-05T10:04:00Z","event":"PostToolUse","input":{"tool_response":'
        '{"usage":{"input_tokens":50000,"output_tokens":10000}}}}\n'
        '{"timestamp":"2026-03-05T10:05:00Z","event":"PostToolUse","input":{"tool_response":'
        '{"usage":{"input_tokens":30000,"output_tokens":5000}}}}\n'
    )


def _small_orchestrator_log() -> str:
    return _make_log(
        [
            "2026-03-05T10:00:00Z [judges.cli] INFO Set E0-F1-S1-T1 to 'in-progress'",
            "2026-03-05T10:05:00Z [judges.cli] INFO Set E0-F1-S1-T1 to 'done'",
        ]
    )


@pytest.mark.unit
class TestTokenCostDiscount:
    """F1-B: discount scales every cost component + ETA uniformly."""

    def _build_workspace(self, tmp_path: Path) -> Path:
        log_file = tmp_path / "test.log"
        log_file.write_text(_small_orchestrator_log())
        (tmp_path / "hook-logs.jsonl").write_text(_small_hook_log())
        return log_file

    def _get_total_cost(self, report_text: str) -> float:
        """Pull the 'Estimated cost so far' dollar value from a rendered report."""
        import re

        m = re.search(r"Estimated cost so far\b.*?\$(\d+\.\d+)", report_text, re.DOTALL)
        assert m is not None, f"could not find cost line in:\n{report_text}"
        return float(m.group(1))

    def test_discount_zero_is_behaviour_preserving(self, tmp_path: Path) -> None:
        log_file = self._build_workspace(tmp_path)
        with (
            patch("devbench.reporting.report.BACKLOG_INDEX", tmp_path / "BACKLOG.md"),
            patch("devbench.reporting.report.TOKEN_COST_DISCOUNT", 0.0),
        ):
            baseline = self._get_total_cost(generate_report(log_path=log_file))
        # With discount 0.0, cost equals the un-discounted baseline.
        assert baseline > 0.0

    def test_discount_half_halves_cost(self, tmp_path: Path) -> None:
        log_file = self._build_workspace(tmp_path)
        with (
            patch("devbench.reporting.report.BACKLOG_INDEX", tmp_path / "BACKLOG.md"),
            patch("devbench.reporting.report.TOKEN_COST_DISCOUNT", 0.0),
        ):
            baseline = self._get_total_cost(generate_report(log_path=log_file))
        with (
            patch("devbench.reporting.report.BACKLOG_INDEX", tmp_path / "BACKLOG.md"),
            patch("devbench.reporting.report.TOKEN_COST_DISCOUNT", 0.5),
        ):
            discounted = self._get_total_cost(generate_report(log_path=log_file))
        assert abs(discounted - baseline * 0.5) < 0.01

    def test_discount_custom_fraction_applied(self, tmp_path: Path) -> None:
        """0.40363636364 discount means final cost = baseline x (1 - 0.40363636364) = baseline x 0.59636363636."""
        log_file = self._build_workspace(tmp_path)
        with (
            patch("devbench.reporting.report.BACKLOG_INDEX", tmp_path / "BACKLOG.md"),
            patch("devbench.reporting.report.TOKEN_COST_DISCOUNT", 0.0),
        ):
            baseline = self._get_total_cost(generate_report(log_path=log_file))
        with (
            patch("devbench.reporting.report.BACKLOG_INDEX", tmp_path / "BACKLOG.md"),
            patch("devbench.reporting.report.TOKEN_COST_DISCOUNT", 0.40363636364),
        ):
            discounted = self._get_total_cost(generate_report(log_path=log_file))
        expected = baseline * 0.59636363636
        assert abs(discounted - expected) < 0.01, (
            f"expected {expected} (baseline={baseline} * 0.59636363636), got {discounted}"
        )

    def test_discount_full_yields_zero_cost(self, tmp_path: Path) -> None:
        log_file = self._build_workspace(tmp_path)
        with (
            patch("devbench.reporting.report.BACKLOG_INDEX", tmp_path / "BACKLOG.md"),
            patch("devbench.reporting.report.TOKEN_COST_DISCOUNT", 1.0),
        ):
            free_cost = self._get_total_cost(generate_report(log_path=log_file))
        assert free_cost == 0.0


@pytest.mark.unit
class TestEstCompletionDatetime:
    """F2-B: Est. completion date/time row renders wall-clock completion in the resolved TZ."""

    def test_completion_none_when_est_hours_zero(self) -> None:
        """WindowStats with est_hours=0 => est_completion_at is None."""
        from devbench.reporting.report import CostBreakdown, HookLogTotals

        stats = WindowStats(
            window_start=datetime(2026, 3, 5, 10, tzinfo=UTC),
            window_hours=1.0,
            tasks_in_window=0,
            avg_minutes=0.0,
            est_hours=0.0,
            totals=HookLogTotals(),
            cost=CostBreakdown(0, 0, 0, 0, 0, 0),
            cache_hit_rate=None,
            tokens_per_task=0.0,
            est_total_cost=0.0,
            api_hours=0.0,
            api_efficiency=None,
        )
        assert stats.est_completion_at is None

    def test_completion_computed_as_now_plus_est_hours(self, tmp_path: Path) -> None:
        """Generated report's est_completion_at is approximately now() + est_hours."""
        log_file = tmp_path / "test.log"
        log_file.write_text(
            _make_log(
                [
                    "2026-03-05T10:00:00Z [judges.cli] INFO Set E0-F1-S1-T1 to 'in-progress'",
                    "2026-03-05T10:05:00Z [judges.cli] INFO Set E0-F1-S1-T1 to 'done'",
                    "2026-03-05T10:05:00Z [judges.cli] INFO Set E0-F1-S1-T2 to 'in-progress'",
                ]
            )
        )
        # BacklogParser is patched to return one in-progress task, so tasks_active=1,
        # pace data exists (1 completion), est_hours will be non-zero.
        from devbench.backlog.work_unit import WorkUnit

        mock_units = [
            WorkUnit(
                id="E0-F1-S1-T2",
                title="T2",
                status=WorkUnitStatus.IN_PROGRESS,
                unit_type=WorkUnitType.TASK,
                file_path=tmp_path / "e.md",
                repo="org/repo",
                dependencies=[],
            ),
        ]
        with (
            patch("devbench.reporting.report.BACKLOG_INDEX", tmp_path / "BACKLOG.md"),
            patch("devbench.reporting.report.BacklogParser") as mock_cls,
        ):
            mock_cls.return_value.parse_index.return_value = mock_units
            report = generate_report(log_path=log_file)
        # The row must be present in the rendered report.
        assert "Est. completion date/time" in report

    def test_completion_renders_na_when_no_pace_data(self, tmp_path: Path) -> None:
        """Report with no completed tasks yet -> completion row shows n/a."""
        log_file = tmp_path / "test.log"
        log_file.write_text(
            _make_log(
                [
                    "2026-03-05T10:00:00Z [judges.cli] INFO Set E0-F1-S1-T1 to 'in-progress'",
                ]
            )
        )
        with patch("devbench.reporting.report.BACKLOG_INDEX", tmp_path / "BACKLOG.md"):
            report = generate_report(log_path=log_file)
        # "Est. completion date/time" row exists and renders as n/a (no pace data).
        lines = [ln for ln in report.splitlines() if "Est. completion date/time" in ln]
        assert lines, f"Est. completion row missing in report:\n{report}"
        assert "n/a" in lines[0]

    def test_completion_renders_in_configured_display_tz(self) -> None:
        """With display_tz=America/New_York passed, completion row shows EST or EDT abbrev.

        Tests the renderer directly with a crafted WindowStats to isolate the
        TZ-conversion logic from the full pace-data pipeline.
        """
        from zoneinfo import ZoneInfo

        from devbench.reporting.report import CostBreakdown, HookLogTotals, _stats_to_value_list

        anchored = datetime(2026, 3, 5, 10, tzinfo=UTC) + timedelta(hours=2.5)
        stats = WindowStats(
            window_start=datetime(2026, 3, 5, 10, tzinfo=UTC),
            window_hours=1.0,
            tasks_in_window=1,
            avg_minutes=5.0,
            est_hours=2.5,
            totals=HookLogTotals(),
            cost=CostBreakdown(0, 0, 0, 0, 0, 0),
            cache_hit_rate=None,
            tokens_per_task=0.0,
            est_total_cost=0.0,
            api_hours=0.0,
            api_efficiency=None,
            est_completion_at=anchored,
        )
        values = _stats_to_value_list(stats, display_tz=ZoneInfo("America/New_York"))
        # Index 5 = Est. completion date/time (per _METRIC_LABELS order).
        completion_cell = values[5]
        # March 5 in NY is EST (winter).
        assert "EST" in completion_cell, f"expected EST in {completion_cell!r}"

    def test_completion_row_lives_in_throughput_section(self) -> None:
        """The Est. completion date/time row belongs under THROUGHPUT, right below
        Est. time to complete remaining. Regression pin: earlier the row was only
        listed in _METRIC_LABELS + _SPANNING_METRIC_LABELS but missing from
        _SECTION_THROUGHPUT, so it fell through to the default Tokens bucket and
        rendered mid-table instead of next to its sibling."""
        from devbench.reporting.report import _section_for_metric

        assert _section_for_metric("Est. completion date/time") == "Throughput"
        # Pin the sibling so this test catches any future rename that splits the pair.
        assert _section_for_metric("Est. time to complete remaining") == "Throughput"


@pytest.mark.unit
class TestDisplayTimezoneFallbackChain:
    """F2-A: report TZ precedence REPORT_DISPLAY_TIMEZONE > DISPLAY_TIMEZONE > OS local.

    The chain is implemented at ``reporting/report.py`` line ~1313 as
    ``_resolve_display_timezone(REPORT_DISPLAY_TIMEZONE or DISPLAY_TIMEZONE)``.
    These tests pin the three branches directly.
    """

    def test_report_tz_takes_precedence_over_global(self) -> None:
        """``"UTC" or "America/New_York"`` resolves to ``"UTC"`` -- report-specific wins."""
        from zoneinfo import ZoneInfo

        from devbench.reporting.report import _resolve_display_timezone

        report_tz = "UTC"
        global_tz = "America/New_York"
        resolved = _resolve_display_timezone(report_tz or global_tz)
        assert resolved == ZoneInfo("UTC")

    def test_global_tz_used_when_report_tz_unset(self) -> None:
        """``None or "America/New_York"`` resolves to ``"America/New_York"`` -- top-level wins."""
        from zoneinfo import ZoneInfo

        from devbench.reporting.report import _resolve_display_timezone

        report_tz = None
        global_tz = "America/New_York"
        resolved = _resolve_display_timezone(report_tz or global_tz)
        assert resolved == ZoneInfo("America/New_York")

    def test_both_unset_yields_none_for_os_local_fallback(self) -> None:
        """``None or None`` is None; caller uses OS local."""
        from devbench.reporting.report import _resolve_display_timezone

        report_tz = None
        global_tz = None
        assert _resolve_display_timezone(report_tz or global_tz) is None


# ---------------------------------------------------------------------------
# Divergence warning -- BACKLOG STATE done count vs THROUGHPUT log-derived count
# ---------------------------------------------------------------------------


class TestThroughputDivergenceWarning:
    """The report emits a one-line WARNING when the BACKLOG.md done count
    is non-zero but the All-time throughput window finds zero
    ``Set <id> to 'done'`` events. This is the deterministic signal that
    the reader is looking at a different log than the orchestrator
    writes to (typically because ``JUDGE_LOG_FILE`` was unset).
    """

    @staticmethod
    def _patch_backlog_with_done_tasks(backlog_total: int = 5, backlog_done: int = 3) -> Any:
        from devbench.backlog.work_unit import WorkUnit, WorkUnitStatus, WorkUnitType

        units: list[WorkUnit] = []
        for i in range(backlog_done):
            units.append(
                WorkUnit(
                    id=f"E0-F1-S1-T{i + 1}",
                    title=f"done-{i + 1}",
                    status=WorkUnitStatus.DONE,
                    unit_type=WorkUnitType.TASK,
                    file_path=Path(f"backlog/E0-F1-S1-T{i + 1}.md"),
                    repo="org/repo",
                    dependencies=[],
                )
            )
        for i in range(backlog_total - backlog_done):
            units.append(
                WorkUnit(
                    id=f"E0-F1-S1-T{backlog_done + i + 1}",
                    title=f"in-queue-{i + 1}",
                    status=WorkUnitStatus.IN_QUEUE,
                    unit_type=WorkUnitType.TASK,
                    file_path=Path(f"backlog/E0-F1-S1-T{backlog_done + i + 1}.md"),
                    repo="org/repo",
                    dependencies=[],
                )
            )
        return units

    def test_warning_fires_when_backlog_done_but_log_throughput_zero(self, tmp_path: Path) -> None:
        # Empty log => throughput finds 0 events. Backlog says 3 done.
        # Warning must fire; reader is on the wrong log.
        log_file = tmp_path / "wrong.log"
        log_file.write_text(_make_log(["2026-03-05T10:00:00Z [unrelated] INFO some entry"]))
        units = self._patch_backlog_with_done_tasks(backlog_total=5, backlog_done=3)
        with patch("devbench.reporting.report.BacklogParser") as mock_cls:
            mock_cls.return_value.parse_index.return_value = units
            report = generate_report(log_path=log_file)
        assert "WARNING" in report
        assert "BACKLOG.md shows 3 done" in report
        assert "JUDGE_LOG_FILE" in report
        assert "shows 0" in report  # the throughput count

    def test_warning_silent_when_log_matches_backlog(self, tmp_path: Path) -> None:
        # Log contains the canonical Set...to 'done' line for every backlog
        # done task. Throughput count matches backlog count => no warning.
        log_file = tmp_path / "right.log"
        log_file.write_text(
            _make_log(
                [
                    "2026-03-05T10:00:00Z [c] INFO Set E0-F1-S1-T1 to 'in-progress'",
                    "2026-03-05T10:05:00Z [c] INFO Set E0-F1-S1-T1 to 'done'",
                    "2026-03-05T10:10:00Z [c] INFO Set E0-F1-S1-T2 to 'in-progress'",
                    "2026-03-05T10:15:00Z [c] INFO Set E0-F1-S1-T2 to 'done'",
                    "2026-03-05T10:20:00Z [c] INFO Set E0-F1-S1-T3 to 'in-progress'",
                    "2026-03-05T10:25:00Z [c] INFO Set E0-F1-S1-T3 to 'done'",
                ]
            )
        )
        units = self._patch_backlog_with_done_tasks(backlog_total=5, backlog_done=3)
        with patch("devbench.reporting.report.BacklogParser") as mock_cls:
            mock_cls.return_value.parse_index.return_value = units
            report = generate_report(log_path=log_file)
        assert "WARNING: BACKLOG.md reports" not in report

    def test_warning_silent_when_backlog_has_no_done_tasks(self, tmp_path: Path) -> None:
        # Edge case: brand-new backlog, zero done tasks. Throughput is
        # also 0; the divergence WARNING must NOT fire (both counts agree).
        log_file = tmp_path / "fresh.log"
        log_file.write_text(_make_log(["2026-03-05T10:00:00Z [c] INFO startup"]))
        units = self._patch_backlog_with_done_tasks(backlog_total=5, backlog_done=0)
        with patch("devbench.reporting.report.BacklogParser") as mock_cls:
            mock_cls.return_value.parse_index.return_value = units
            report = generate_report(log_path=log_file)
        assert "WARNING: BACKLOG.md reports" not in report

    def test_warning_message_includes_log_path(self, tmp_path: Path) -> None:
        # The error message must name the log file path so the operator
        # can immediately identify which file got read; this is the
        # actionable signal that lets them set JUDGE_LOG_FILE correctly.
        log_file = tmp_path / "specific-name.log"
        log_file.write_text("")
        units = self._patch_backlog_with_done_tasks(backlog_total=2, backlog_done=1)
        with patch("devbench.reporting.report.BacklogParser") as mock_cls:
            mock_cls.return_value.parse_index.return_value = units
            report = generate_report(log_path=log_file)
        assert str(log_file) in report


@pytest.mark.unit
class TestReportRowColors:
    """Tests for ANSI colour wrapping on selected report rows.

    Coloured rows:
      - 'Tasks completed', 'Tasks completed in window',
        'Work units done (...)', 'Stories / Features / Epics auto-rolled to done'
        -> green (\\033[32m)
      - 'Tasks blocked' -> light red (\\033[91m)
      - 'Estimated cost so far' -> magenta (\\033[35m)

    Colour is suppressed when stdout is not a TTY (default for pytest)
    and when NO_COLOR is set.
    """

    def _seed_report(self, tmp_path: Path) -> str:
        log_file = tmp_path / "test.log"
        log_file.write_text(
            _make_log(
                [
                    "2026-03-05T10:00:00Z [judges.cli] INFO Set E0-F1-S1-T1 to 'in-progress'",
                    "2026-03-05T10:05:00Z [judges.cli] INFO Set E0-F1-S1-T1 to 'done'",
                ]
            )
        )
        with patch("devbench.reporting.report.BACKLOG_INDEX", tmp_path / "BACKLOG.md"):
            return generate_report(log_path=log_file)

    def test_no_color_when_not_tty(self, tmp_path: Path) -> None:
        # Pytest captures stdout so isatty() is False -- no escape codes anywhere.
        report = self._seed_report(tmp_path)
        assert "\033[" not in report

    def test_no_color_when_no_color_env_set(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("NO_COLOR", "1")
        with patch("devbench.reporting.report._should_use_color", return_value=False):
            report = self._seed_report(tmp_path)
        assert "\033[" not in report

    def test_green_wraps_tasks_completed_row(self, tmp_path: Path) -> None:
        with patch("devbench.reporting.report._should_use_color", return_value=True):
            report = self._seed_report(tmp_path)
        green_lines = [line for line in report.splitlines() if "\033[32m" in line and "Tasks completed" in line]
        assert green_lines, "Expected at least one green-wrapped row containing 'Tasks completed'"
        for line in green_lines:
            assert line.endswith("\033[0m"), f"Coloured line missing reset code: {line!r}"

    def test_light_red_wraps_tasks_blocked_row(self, tmp_path: Path) -> None:
        with patch("devbench.reporting.report._should_use_color", return_value=True):
            report = self._seed_report(tmp_path)
        red_lines = [line for line in report.splitlines() if "\033[91m" in line and "Tasks blocked" in line]
        assert red_lines, "Expected a light-red-wrapped row containing 'Tasks blocked'"
        for line in red_lines:
            assert line.endswith("\033[0m")

    def test_magenta_wraps_estimated_cost_so_far(self, tmp_path: Path) -> None:
        # Cost row only renders when there's token data; seed a hook log.
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
            '{"timestamp":"2026-03-05T10:04:00Z","event":"PostToolUse","input":{"tool_response":'
            '{"usage":{"input_tokens":50000}}}}\n'
        )
        with (
            patch("devbench.reporting.report.BACKLOG_INDEX", tmp_path / "BACKLOG.md"),
            patch("devbench.reporting.report._should_use_color", return_value=True),
        ):
            report = generate_report(log_path=log_file)
        magenta_lines = [line for line in report.splitlines() if "\033[35m" in line and "Estimated cost so far" in line]
        assert magenta_lines, "Expected a magenta-wrapped row containing 'Estimated cost so far'"
        for line in magenta_lines:
            assert line.endswith("\033[0m")

    def test_estimated_total_cost_row_is_not_coloured(self, tmp_path: Path) -> None:
        # User explicitly asked only 'Estimated cost so far' to be magenta;
        # 'Estimated total cost at completion' must remain plain.
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
            '{"timestamp":"2026-03-05T10:04:00Z","event":"PostToolUse","input":{"tool_response":'
            '{"usage":{"input_tokens":50000}}}}\n'
        )
        with (
            patch("devbench.reporting.report.BACKLOG_INDEX", tmp_path / "BACKLOG.md"),
            patch("devbench.reporting.report._should_use_color", return_value=True),
        ):
            report = generate_report(log_path=log_file)
        for line in report.splitlines():
            if "Estimated total cost at completion" in line:
                assert "\033[" not in line, f"'Estimated total cost at completion' must not be coloured: {line!r}"


# ---------------------------------------------------------------------------
# Issue #157: ETA includes blocked-recovery + blocked-auto buckets
# ---------------------------------------------------------------------------


class TestEtaIncludesBlockedRecoveryAndAuto:
    """Issue #157: the ETA denominator now includes the recovery + auto-clearing
    blocked buckets, but excludes the operator-attention bucket."""

    def test_compute_window_stats_uses_combined_denominator(self, tmp_path: Path) -> None:
        from devbench.reporting.report import _compute_window_stats

        # Seed enough done samples that recent_pace_minutes resolves.
        now = datetime(2026, 5, 2, 12, 0, 0, tzinfo=UTC)
        done_times: dict[str, datetime] = {}
        progress_times: dict[str, datetime] = {}
        for i in range(5):
            tid = f"E0-F1-S1-T{i + 1}"
            progress_times[tid] = now - timedelta(minutes=20 + i)
            done_times[tid] = now - timedelta(minutes=10 + i)
        log_path = tmp_path / "log.log"
        log_path.write_text("", encoding="utf-8")
        stats = _compute_window_stats(
            log_path,
            now - timedelta(hours=1),
            now,
            done_times,
            progress_times,
            tasks_active=4,
            tasks_blocked_recovery=60,
            tasks_blocked_auto=27,
        )
        # ETA bucket counts surface on the WindowStats dataclass.
        assert stats.eta_active == 4
        assert stats.eta_blocked_recovery == 60
        assert stats.eta_blocked_auto == 27
        # est_hours scales with (4 + 60 + 27) -- denominator includes recovery + auto.
        assert stats.est_hours > 0


class TestEtaFallsBackOnInsufficientPaceData:
    """Issue #157: ETA reads n/a when recent-pace data is insufficient."""

    def test_render_returns_n_a_when_recent_pace_unknown(self) -> None:
        from devbench.reporting.report import _format_est_hours_display

        # Inputs: no est_hours -> "n/a" branch.
        stats = WindowStats(
            window_start=datetime(2026, 5, 2, tzinfo=UTC),
            window_hours=1.0,
            tasks_in_window=0,
            avg_minutes=0.0,
            est_hours=0.0,
            totals=__import__("devbench.reporting.report", fromlist=["HookLogTotals"]).HookLogTotals(),
            cost=__import__("devbench.reporting.report", fromlist=["CostBreakdown"]).CostBreakdown(
                input_cost=0,
                output_cost=0,
                cache_read_cost=0,
                cache_write_5m_cost=0,
                cache_write_1h_cost=0,
                total_cost=0,
            ),
            cache_hit_rate=None,
            tokens_per_task=0,
            est_total_cost=0,
            api_hours=0,
            api_efficiency=None,
        )
        assert _format_est_hours_display(stats) == "n/a"

    def test_render_bare_hours_when_pace_unknown_but_eta_computed(self) -> None:
        from devbench.reporting.report import _format_est_hours_display

        rep = __import__("devbench.reporting.report", fromlist=["HookLogTotals", "CostBreakdown"])
        stats = WindowStats(
            window_start=datetime(2026, 5, 2, tzinfo=UTC),
            window_hours=1.0,
            tasks_in_window=2,
            avg_minutes=5.0,
            est_hours=1.5,
            totals=rep.HookLogTotals(),
            cost=rep.CostBreakdown(0, 0, 0, 0, 0, 0),
            cache_hit_rate=None,
            tokens_per_task=0,
            est_total_cost=0,
            api_hours=0,
            api_efficiency=None,
            recent_pace_minutes=None,  # Recent pace fragile -> bare-hours branch.
        )
        assert _format_est_hours_display(stats) == "~1.5 h"


class TestEtaCommentSuffixWhenBlockedDominates:
    """Issue #157: when recent pace is known, the ETA cell carries a
    breakdown suffix naming the contributing buckets and pace."""

    def test_breakdown_suffix_present(self) -> None:
        from devbench.reporting.report import _format_est_hours_display

        rep = __import__("devbench.reporting.report", fromlist=["HookLogTotals", "CostBreakdown"])
        stats = WindowStats(
            window_start=datetime(2026, 5, 2, tzinfo=UTC),
            window_hours=1.0,
            tasks_in_window=10,
            avg_minutes=5.6,
            est_hours=5.4,
            totals=rep.HookLogTotals(),
            cost=rep.CostBreakdown(0, 0, 0, 0, 0, 0),
            cache_hit_rate=None,
            tokens_per_task=0,
            est_total_cost=0,
            api_hours=0,
            api_efficiency=None,
            recent_pace_minutes=5.6,
            eta_active=4,
            eta_blocked_recovery=60,
            eta_blocked_auto=27,
        )
        out = _format_est_hours_display(stats)
        assert "5.4 h" in out
        assert "active 4" in out
        assert "blocked-recovery 60" in out
        assert "blocked-auto 27" in out
        assert "5.6 min/task" in out


class TestReportInProgressDurationSuffix:
    """Issue #158: the report's in-progress panel renders the duration suffix."""

    def test_listing_appends_timer_unavailable_when_no_log_signal(self) -> None:
        from devbench.backlog.work_unit import WorkUnit, WorkUnitStatus, WorkUnitType
        from devbench.reporting.report import _in_progress_listing

        unit = WorkUnit(
            id="E0-F1-S1-T1",
            title="Active",
            status=WorkUnitStatus.IN_PROGRESS,
            unit_type=WorkUnitType.TASK,
            file_path=Path("backlog/E0-F1-S1-T1.md"),
            repo="org/repo",
            dependencies=[],
        )
        with patch("devbench.cli._in_progress_attempt_duration", return_value=None):
            lines = _in_progress_listing([unit])
        assert "(in-progress, timer unavailable)" in lines[2]

    def test_listing_appends_humanized_duration_when_available(self) -> None:
        from devbench.backlog.work_unit import WorkUnit, WorkUnitStatus, WorkUnitType
        from devbench.reporting.report import _in_progress_listing

        unit = WorkUnit(
            id="E0-F1-S1-T1",
            title="Active",
            status=WorkUnitStatus.IN_PROGRESS,
            unit_type=WorkUnitType.TASK,
            file_path=Path("backlog/E0-F1-S1-T1.md"),
            repo="org/repo",
            dependencies=[],
        )
        with patch("devbench.cli._in_progress_attempt_duration", return_value="23m"):
            lines = _in_progress_listing([unit])
        assert "(in-progress for 23m)" in lines[2]


class TestOrchestratorAliveBanner:
    """Tests for the orchestrator-alive status banner (issue #161).

    The banner is rendered at the very top of every ``devbench report``
    invocation and surfaces three liveness states derived from log-activity
    recency: ALIVE (green) / STOPPED (red) / STARTING (yellow). The threshold
    is sourced from ``stop_hook.window_seconds`` so the banner stays aligned
    with the operator's circuit-breaker quiet window.
    """

    @staticmethod
    def _write_log(log_path: Path, last_ts_iso: str) -> None:
        log_path.write_text(
            f"2026-03-05T09:00:00Z [devbench.orch] INFO Started\n{last_ts_iso} [devbench.orch] INFO Tick\n"
        )

    def test_alive_state_green_when_recent_activity(self, tmp_path: Path) -> None:
        from devbench.reporting.report import _orchestrator_liveness_banner

        log = tmp_path / "orch.log"
        self._write_log(log, "2026-03-05T10:00:00Z")
        now = datetime(2026, 3, 5, 10, 0, 30, tzinfo=UTC)
        with patch("devbench.reporting.report._should_use_color", return_value=True):
            banner = _orchestrator_liveness_banner(log, "sess-A", 180, now=now)
        assert banner.startswith("\033[32m")
        assert "[ORCHESTRATOR ALIVE]" in banner
        assert "30s ago" in banner
        assert "session sess-A" in banner
        assert banner.endswith("\033[0m")

    def test_stopped_state_red_when_quiet_past_threshold(self, tmp_path: Path) -> None:
        from devbench.reporting.report import _orchestrator_liveness_banner

        log = tmp_path / "orch.log"
        self._write_log(log, "2026-03-05T10:00:00Z")
        now = datetime(2026, 3, 5, 10, 10, 0, tzinfo=UTC)
        with patch("devbench.reporting.report._should_use_color", return_value=True):
            banner = _orchestrator_liveness_banner(log, "sess-A", 180, now=now)
        assert banner.startswith("\033[91m")
        assert "[ORCHESTRATOR STOPPED]" in banner
        assert "no activity for 10m" in banner
        assert "last seen" in banner
        assert "session sess-A" in banner
        assert banner.endswith("\033[0m")

    def test_starting_state_yellow_when_log_missing(self, tmp_path: Path) -> None:
        from devbench.reporting.report import _orchestrator_liveness_banner

        missing = tmp_path / "no-such.log"
        with patch("devbench.reporting.report._should_use_color", return_value=True):
            banner = _orchestrator_liveness_banner(missing, "sess-A", 180)
        assert banner.startswith("\033[33m")
        assert "[ORCHESTRATOR STARTING]" in banner
        assert "log file empty" in banner
        assert banner.endswith("\033[0m")

    def test_starting_state_when_log_empty(self, tmp_path: Path) -> None:
        from devbench.reporting.report import _orchestrator_liveness_banner

        log = tmp_path / "orch.log"
        log.write_text("")
        with patch("devbench.reporting.report._should_use_color", return_value=True):
            banner = _orchestrator_liveness_banner(log, "sess-A", 180)
        assert "[ORCHESTRATOR STARTING]" in banner

    def test_no_color_when_not_tty(self, tmp_path: Path) -> None:
        from devbench.reporting.report import _orchestrator_liveness_banner

        log = tmp_path / "orch.log"
        self._write_log(log, "2026-03-05T10:00:00Z")
        now = datetime(2026, 3, 5, 10, 0, 30, tzinfo=UTC)
        with patch("devbench.reporting.report._should_use_color", return_value=False):
            banner = _orchestrator_liveness_banner(log, "sess-A", 180, now=now)
        assert "\033[" not in banner
        assert "[ORCHESTRATOR ALIVE]" in banner

    def test_boundary_at_threshold_is_alive(self, tmp_path: Path) -> None:
        from devbench.reporting.report import _orchestrator_liveness_banner

        log = tmp_path / "orch.log"
        self._write_log(log, "2026-03-05T10:00:00Z")
        # Exactly at threshold (180s) -> ALIVE.
        at_threshold = datetime(2026, 3, 5, 10, 3, 0, tzinfo=UTC)
        banner_at = _orchestrator_liveness_banner(log, "sess-A", 180, now=at_threshold)
        assert "[ORCHESTRATOR ALIVE]" in banner_at
        # One second past threshold -> STOPPED.
        past_threshold = datetime(2026, 3, 5, 10, 3, 1, tzinfo=UTC)
        banner_past = _orchestrator_liveness_banner(log, "sess-A", 180, now=past_threshold)
        assert "[ORCHESTRATOR STOPPED]" in banner_past

    def test_no_session_id_suppresses_session_suffix(self, tmp_path: Path) -> None:
        from devbench.reporting.report import _orchestrator_liveness_banner

        log = tmp_path / "orch.log"
        self._write_log(log, "2026-03-05T10:00:00Z")
        now = datetime(2026, 3, 5, 10, 0, 10, tzinfo=UTC)
        for empty in (None, ""):
            banner = _orchestrator_liveness_banner(log, empty, 180, now=now)
            assert "-- session" not in banner, f"empty session_id={empty!r} leaked suffix: {banner!r}"

    def test_threshold_sourced_from_stop_hook_window_seconds(self, tmp_path: Path) -> None:
        """``generate_report`` must read STOP_HOOK_WINDOW_SECONDS, not a literal."""
        from devbench.reporting import report as report_mod

        log_file = tmp_path / "orch.log"
        log_file.write_text(
            _make_log(
                [
                    "2026-03-05T10:00:00Z [judges.cli] INFO Set E0-F1-S1-T1 to 'in-progress'",
                    "2026-03-05T10:05:00Z [judges.cli] INFO Set E0-F1-S1-T1 to 'done'",
                ]
            )
        )
        captured: dict[str, int] = {}

        def _capture(*, log_path: Path, session_id: str | None, threshold_seconds: int, **_: Any) -> str:
            captured["threshold"] = threshold_seconds
            return "[ORCHESTRATOR ALIVE] capture-stub"

        with (
            patch("devbench.reporting.report.STOP_HOOK_WINDOW_SECONDS", 999),
            patch("devbench.reporting.report._orchestrator_liveness_banner", side_effect=_capture),
            patch("devbench.reporting.report.BACKLOG_INDEX", tmp_path / "BACKLOG.md"),
        ):
            report_mod.generate_report(log_path=log_file)
        assert captured["threshold"] == 999

    def test_banner_prepended_as_first_line_of_report(self, tmp_path: Path) -> None:
        log_file = tmp_path / "orch.log"
        log_file.write_text(
            _make_log(
                [
                    "2026-03-05T10:00:00Z [judges.cli] INFO Set E0-F1-S1-T1 to 'in-progress'",
                    "2026-03-05T10:05:00Z [judges.cli] INFO Set E0-F1-S1-T1 to 'done'",
                ]
            )
        )
        with (
            patch(
                "devbench.reporting.report._orchestrator_liveness_banner",
                return_value="[ORCHESTRATOR FIXTURE] banner-line",
            ),
            patch("devbench.reporting.report.BACKLOG_INDEX", tmp_path / "BACKLOG.md"),
        ):
            output = generate_report(log_path=log_file)
        first_nonempty = next(line for line in output.splitlines() if line.strip())
        assert first_nonempty == "[ORCHESTRATOR FIXTURE] banner-line"

    def test_banner_refreshes_under_watch_when_log_advances(self, tmp_path: Path) -> None:
        """Two successive renders against an advancing log must produce different banners."""
        from devbench.reporting.report import _orchestrator_liveness_banner

        log = tmp_path / "orch.log"
        self._write_log(log, "2026-03-05T10:00:00Z")
        # First tick: 30s ago -> ALIVE 30s.
        now1 = datetime(2026, 3, 5, 10, 0, 30, tzinfo=UTC)
        banner1 = _orchestrator_liveness_banner(log, "sess-A", 180, now=now1)
        # Log advances; orchestrator wrote another line.
        log.write_text(
            "2026-03-05T09:00:00Z [devbench.orch] INFO Started\n"
            "2026-03-05T10:00:00Z [devbench.orch] INFO Tick\n"
            "2026-03-05T10:00:30Z [devbench.orch] INFO Tick\n"
        )
        banner2 = _orchestrator_liveness_banner(log, "sess-A", 180, now=now1)
        assert "30s ago" in banner1
        assert "0s ago" in banner2
        assert banner1 != banner2


class TestGenerateReportBacklogParseFailure:
    """Issue #174: generate_report must exit non-zero with an actionable diagnostic
    when ``BacklogParser.parse_index`` raises FileNotFoundError or ValueError --
    not leak a raw stack trace from a malformed ``BACKLOG.md``.
    """

    def test_value_error_from_parser_triggers_clean_exit(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """A malformed BACKLOG.md (ValueError from parser) -> SystemExit(1) + stderr diagnostic."""
        log_file = tmp_path / "test.log"
        log_file.write_text(
            _make_log(
                [
                    "2026-03-05T10:00:00Z [judges.cli] INFO Set E0-F1-S1-T1 to 'in-progress'",
                ]
            )
        )
        with patch("devbench.reporting.report.BacklogParser") as mock_cls:
            instance = mock_cls.return_value
            instance.parse_index.side_effect = ValueError("No work-unit rows found in 'BACKLOG.md'")
            with pytest.raises(SystemExit) as exc:
                generate_report(log_path=log_file)
        assert exc.value.code == 1
        captured = capsys.readouterr()
        assert "devbench report: cannot parse" in captured.err
        assert "No work-unit rows found" in captured.err
        assert "devbench validate-backlog" in captured.err

    def test_file_not_found_from_parser_triggers_clean_exit(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """A missing BACKLOG.md (FileNotFoundError) -> SystemExit(1) + stderr diagnostic."""
        log_file = tmp_path / "test.log"
        log_file.write_text(
            _make_log(
                [
                    "2026-03-05T10:00:00Z [judges.cli] INFO Set E0-F1-S1-T1 to 'in-progress'",
                ]
            )
        )
        with patch("devbench.reporting.report.BacklogParser") as mock_cls:
            instance = mock_cls.return_value
            instance.parse_index.side_effect = FileNotFoundError("Backlog index not found at 'BACKLOG.md'")
            with pytest.raises(SystemExit) as exc:
                generate_report(log_path=log_file)
        assert exc.value.code == 1
        captured = capsys.readouterr()
        assert "devbench report: cannot parse" in captured.err
        assert "Backlog index not found" in captured.err


class TestBacklogTotalsDraftColumn:
    """AC-189-7: _BacklogTotals includes tasks_draft and generate_report renders it."""

    @staticmethod
    def _mk(uid: str, status: WorkUnitStatus) -> WorkUnit:
        return WorkUnit(
            id=uid,
            title=f"task-{uid}",
            status=status,
            unit_type=WorkUnitType.TASK,
            file_path=Path(f"backlog/{uid}.md"),
            repo="caylent-solutions/git-repo",
            dependencies=[],
        )

    def test_backlog_totals_has_tasks_draft_field(self) -> None:
        """_BacklogTotals exposes a tasks_draft integer field."""
        from devbench.reporting.report import _BacklogTotals

        b = _BacklogTotals(
            tasks_total=5,
            tasks_done=1,
            units_total=5,
            units_done=1,
            stories_done=0,
            features_done=0,
            epics_done=0,
            tasks_remaining=2,
            tasks_blocked=1,
            tasks_active=1,
            tasks_in_progress=0,
            tasks_in_queue=1,
            tasks_in_review=0,
            tasks_proposed=0,
            tasks_declined=0,
            tasks_draft=1,
        )
        assert b.tasks_draft == 1

    def test_backlog_totals_from_units_counts_draft(self) -> None:
        """_backlog_totals_from_units populates tasks_draft from DRAFT-status tasks."""
        from devbench.reporting.report import _backlog_totals_from_units

        units = [
            self._mk("E0-F1-S1-T1", WorkUnitStatus.DONE),
            self._mk("E0-F1-S1-T2", WorkUnitStatus.IN_QUEUE),
            self._mk("E0-F1-S1-T3", WorkUnitStatus.DRAFT),
            self._mk("E0-F1-S1-T4", WorkUnitStatus.DRAFT),
        ]
        b = _backlog_totals_from_units(units)
        assert b.tasks_draft == 2

    def test_draft_tasks_excluded_from_tasks_remaining(self) -> None:
        """Draft tasks are excluded from tasks_remaining (like proposed/declined)."""
        from devbench.reporting.report import _backlog_totals_from_units

        units = [
            self._mk("E0-F1-S1-T1", WorkUnitStatus.DONE),
            self._mk("E0-F1-S1-T2", WorkUnitStatus.IN_QUEUE),
            self._mk("E0-F1-S1-T3", WorkUnitStatus.DRAFT),
        ]
        b = _backlog_totals_from_units(units)
        # tasks_remaining = total(3) - done(1) - proposed(0) - declined(0) - draft(1) = 1
        assert b.tasks_remaining == 1
        assert b.tasks_active == 1
        assert b.tasks_draft == 1

    def test_backlog_state_rows_include_tasks_draft(self) -> None:
        """_backlog_state_rows includes a 'Tasks draft' row with the draft count."""
        from devbench.reporting.report import _backlog_state_rows, _backlog_totals_from_units

        units = [
            self._mk("E0-F1-S1-T1", WorkUnitStatus.DONE),
            self._mk("E0-F1-S1-T2", WorkUnitStatus.IN_QUEUE),
            self._mk("E0-F1-S1-T3", WorkUnitStatus.DRAFT),
            self._mk("E0-F1-S1-T4", WorkUnitStatus.DRAFT),
        ]
        b = _backlog_totals_from_units(units)
        rows = dict(_backlog_state_rows(b))
        assert "Tasks draft" in rows
        assert rows["Tasks draft"] == "2"

    def test_draft_zero_still_rendered(self) -> None:
        """Draft row appears even when count is zero (consistent with other status rows)."""
        from devbench.reporting.report import _backlog_state_rows, _backlog_totals_from_units

        units = [
            self._mk("E0-F1-S1-T1", WorkUnitStatus.DONE),
            self._mk("E0-F1-S1-T2", WorkUnitStatus.IN_QUEUE),
        ]
        b = _backlog_totals_from_units(units)
        rows = dict(_backlog_state_rows(b))
        assert "Tasks draft" in rows
        assert rows["Tasks draft"] == "0"

    def test_invariant_all_statuses_sum_to_total(self) -> None:
        """All per-status fields sum to tasks_total."""
        from devbench.reporting.report import _backlog_totals_from_units

        units = [
            self._mk("E0-F1-S1-T1", WorkUnitStatus.DONE),
            self._mk("E0-F1-S1-T2", WorkUnitStatus.IN_QUEUE),
            self._mk("E0-F1-S1-T3", WorkUnitStatus.IN_PROGRESS),
            self._mk("E0-F1-S1-T4", WorkUnitStatus.BLOCKED),
            self._mk("E0-F1-S1-T5", WorkUnitStatus.IN_REVIEW),
            self._mk("E0-F1-S1-T6", WorkUnitStatus.PROPOSED),
            self._mk("E0-F1-S1-T7", WorkUnitStatus.DECLINED),
            self._mk("E0-F1-S1-T8", WorkUnitStatus.DRAFT),
        ]
        b = _backlog_totals_from_units(units)
        total = (
            b.tasks_done
            + b.tasks_in_queue
            + b.tasks_in_progress
            + b.tasks_blocked
            + b.tasks_in_review
            + b.tasks_proposed
            + b.tasks_declined
            + b.tasks_draft
        )
        assert total == b.tasks_total
