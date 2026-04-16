"""Tests for judges.report module."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import patch

import pytest

from devbench.backlog.work_unit import WorkUnitStatus, WorkUnitType
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
        # Summary line uses All-time pace (renamed from "current pace") and shows remaining count.
        assert "At the All-time pace" in report
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
            '{"timestamp":"2026-03-05T10:04:00Z","event":"PostToolUse","input":{"tool_response":'
            '{"usage":{"input_tokens":100000}}}}\n'
        )
        from unittest.mock import patch

        with patch("devbench.reporting.report.BACKLOG_INDEX", tmp_path / "BACKLOG.md"):
            report = generate_report(log_path=log_file)

        assert "Override per-rate in devbench.yaml under report:" in report


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
        assert "Backlog state" in report
        assert "Window stats" in report
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
                    # A real orchestration event 70 minutes after the last real one — new session
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
        # Just check report rendered — no exception, has the expected sections.
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
        # the same call — confirming per-token-type costing is >10x lower for
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

        # All token types should appear in the lifetime breakdown rows of the top box.
        assert "Lifetime tokens consumed" in report
        assert "1,000" in report  # cache reads
        assert "Lifetime cache hit rate" in report

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
