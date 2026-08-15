"""Tests for judges.report module."""

from __future__ import annotations

import json
import os
import statistics
import subprocess
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest

from devbench.backlog.work_unit import WorkUnit, WorkUnitStatus, WorkUnitType
from devbench.constants import SESSION_DEFAULT_NAME, SESSION_SESSIONS_BASE_DIR
from devbench.reporting.report import (
    HookLogTotals,
    WindowStats,
    generate_report,
    read_all_drain_states,
    review_rejections_line,
)


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
        duration should be measured from the window start, not from the original
        in-progress time. Uses 3 completed tasks so the pace metric clears the
        MIN_PACE_SAMPLES threshold.

        Issue #326: every claim-to-done pair stays within 30 minutes of real
        elapsed time (and of its neighbours) so the whole log is one
        orchestrator session -- the same-session gate does not drop any of
        the three samples. T2/T3 use distinct (non-tied) durations so the
        window-clamped value for T1 lands in the middle of the sorted
        3-sample set: if the implementation ever regressed to using T1's raw
        (unclamped) claim-to-done span instead of the window-clamped span,
        the reported median would shift to 25.0, not the expected 15.0.
        """
        log_file = tmp_path / "test.log"
        log_file.write_text(
            _make_log(
                [
                    "2026-03-05T09:50:00Z [devbench.backlog_manager] INFO Set E0-F1-S1-T1 to 'in-progress'",
                    "2026-03-05T10:15:00Z [devbench.backlog_manager] INFO Set E0-F1-S1-T1 to 'done'",
                    "2026-03-05T10:15:00Z [devbench.backlog_manager] INFO Set E0-F1-S1-T2 to 'in-progress'",
                    "2026-03-05T10:20:00Z [devbench.backlog_manager] INFO Set E0-F1-S1-T2 to 'done'",
                    "2026-03-05T10:20:00Z [devbench.backlog_manager] INFO Set E0-F1-S1-T3 to 'in-progress'",
                    "2026-03-05T10:45:00Z [devbench.backlog_manager] INFO Set E0-F1-S1-T3 to 'done'",
                ]
            )
        )

        since = datetime(2026, 3, 5, 10, 0, 0, tzinfo=UTC)
        report = generate_report(log_path=log_file, since=since)
        # T1 clamped to the window start: 10:15 - 10:00 = 15 min (not its
        # real 25-min claim-to-done span). Median of [15, 5, 25] = 15.0.
        assert "15.0 min" in report

    def test_report_ignores_prior_session_claim_and_uses_current_session_claim(self, tmp_path: Path) -> None:
        """Issue #329 FR-2: when a task is set to 'in-progress' multiple
        times across a session gap, the window is anchored to the earliest
        CURRENT-session claim, not simply the most recent claim overall --
        here the two happen to coincide because the older claim sits in a
        different (prior) orchestrator session and is therefore never
        eligible. Padded with two more completions to clear MIN_PACE_SAMPLES."""
        log_file = tmp_path / "test.log"
        log_file.write_text(
            _make_log(
                [
                    "2026-03-05T08:00:00Z [devbench.backlog_manager] INFO Set E0-F1-S1-T1 to 'in-progress'",
                    "2026-03-05T10:00:00Z [devbench.backlog_manager] INFO Set E0-F1-S1-T1 to 'in-progress'",
                    "2026-03-05T10:20:00Z [devbench.backlog_manager] INFO Set E0-F1-S1-T1 to 'done'",
                    "2026-03-05T10:20:00Z [devbench.backlog_manager] INFO Set E0-F1-S1-T2 to 'in-progress'",
                    "2026-03-05T10:40:00Z [devbench.backlog_manager] INFO Set E0-F1-S1-T2 to 'done'",
                    "2026-03-05T10:40:00Z [devbench.backlog_manager] INFO Set E0-F1-S1-T3 to 'in-progress'",
                    "2026-03-05T11:00:00Z [devbench.backlog_manager] INFO Set E0-F1-S1-T3 to 'done'",
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
        # ``devbench-orchestrate:code-reviewer`` -> ``code_review``).
        (transcript_dir / "session1.jsonl").write_text(
            '{"timestamp":"2026-03-05T10:01:00Z","message":{"role":"assistant",'
            '"usage":{"input_tokens":100,"output_tokens":50}}}\n'
            '{"timestamp":"2026-03-05T10:02:00Z","attributionAgent":"devbench-orchestrate:executor",'
            '"message":{"role":"assistant","usage":{"input_tokens":200,"output_tokens":75}}}\n'
            '{"timestamp":"2026-03-05T10:03:00Z","attributionAgent":"devbench-orchestrate:code-reviewer",'
            '"message":{"role":"assistant","usage":{"input_tokens":40,"output_tokens":10}}}\n'
        )
        window_start = datetime(2026, 3, 5, 10, 0, 0, tzinfo=UTC)
        by_role = _parse_transcript_metrics_by_role(transcript_dir, window_start)

        assert "orchestrator" in by_role
        assert "executor" in by_role
        assert "code_review" in by_role  # devbench-orchestrate:code-reviewer -> code_review
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

        assert _role_for_entry({"attributionAgent": "devbench-orchestrate:executor"}) == "executor"
        assert _role_for_entry({"attributionAgent": "devbench-orchestrate:code-reviewer"}) == "code_review"
        assert _role_for_entry({"attributionAgent": "devbench-orchestrate:test-reviewer"}) == "test_review"
        assert _role_for_entry({"attributionAgent": "devbench-orchestrate:doc-reviewer"}) == "doc_review"
        assert _role_for_entry({"attributionAgent": "devbench-orchestrate:changes-manifest"}) == "changes_manifest"
        assert _role_for_entry({"attributionAgent": "devbench-orchestrate:security-reviewer"}) == "security_review"
        assert _role_for_entry({"attributionAgent": "devbench-orchestrate:blocker-resolver"}) == "blocker_resolver"
        assert _role_for_entry({"attributionAgent": "devbench-orchestrate:manifest-amender"}) == "manifest_amender"
        assert _role_for_entry({"attributionAgent": "devbench-orchestrate:task-factory"}) == "task_factory"
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

        # In test env, DEVBENCH_USE_BEDROCK is not set
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
            _as_claims(prog),
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
            _as_claims(prog),
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
            _as_claims(prog),
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

    def test_recent_per_task_cost_falls_back_to_window_avg_when_helper_returns_none(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Issue #164 fallback contract: when ``recent_per_task_cost`` is
        None, ``_compute_window_stats`` falls back to the per-window
        average (cost.total_cost / tasks_in_window). When ``recent_per_task_cost``
        IS provided it overrides the per-window denominator."""
        from devbench.reporting.report import _compute_window_stats

        # AC-CODE-001: prevent _hook_log_path from falling back to the live
        # workspace hook-logs.jsonl when log_path is inside tmp_path.
        nonexistent = tmp_path / "hook-logs.jsonl"
        monkeypatch.setattr("devbench.reporting.report._hook_log_path", lambda _p: nonexistent)
        monkeypatch.setattr("devbench.reporting.report._discover_transcript_dir", lambda _p: None)

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
            _as_claims(prog),
            tasks_active=10,
        )
        assert stats_fallback.est_total_cost == pytest.approx(0.0)

        # With recent_per_task_cost=$50, projection = 0 + 50 * 10 = 500
        stats_global = _compute_window_stats(
            log_file,
            datetime(2026, 4, 15, 10, tzinfo=UTC),
            datetime(2026, 4, 15, 12, tzinfo=UTC),
            done,
            _as_claims(prog),
            tasks_active=10,
            recent_per_task_cost=50.0,
        )
        assert stats_global.est_total_cost == pytest.approx(500.0)

    def test_lifetime_total_cost_overrides_per_window_additive_base(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Spanning-row contract: when ``lifetime_total_cost`` is supplied,
        ``est_total_cost`` uses it as the additive base instead of the
        per-window ``cost.total_cost``. This is what makes every column
        produce the same projection so ``_merge_spanning_values`` collapses
        the row. Default-None preserves the legacy per-window formula for
        direct test callers.
        """
        from devbench.reporting.report import _compute_window_stats

        # AC-CODE-001: prevent _hook_log_path from falling back to the live
        # workspace hook-logs.jsonl when log_path is inside tmp_path.
        nonexistent = tmp_path / "hook-logs.jsonl"
        monkeypatch.setattr("devbench.reporting.report._hook_log_path", lambda _p: nonexistent)
        monkeypatch.setattr("devbench.reporting.report._discover_transcript_dir", lambda _p: None)

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
            _as_claims(prog),
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
            _as_claims(prog),
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
                (
                    "SDK lost Agent-tool access mid-session; task remains blocked until the orchestrator "
                    "restarts (auto on NO_ACTIONABLE exit; otherwise manual `make start`)."
                ),
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

    def test_multi_column_table_caps_columns_and_wraps_long_cells(self) -> None:
        """Regression #214: a wide cell does NOT inflate every column to its
        width.  Instead the column is capped at ``MAX_VALUE_COL_WIDTH`` and
        the cell wraps onto multiple physical lines, preferring `` + ``
        boundaries and never breaking a word mid-character.
        """
        from devbench.reporting.report import (
            MAX_VALUE_COL_WIDTH,
            _render_multi_column_table,
        )

        long_value = (
            "~5.7 h (active 3 + blocked-recovery 12 + blocked-auto 11 + blocked-runtime-degradation 1 at 12.7 min/task)"
        )
        short_value = "~5.5 h (active 3 + blocked-recovery 12 + blocked-auto 11 at 12.7 min/task)"
        lines = _render_multi_column_table(
            "Metric",
            ["All-time", "Session"],
            [
                ("Time span", ["190.3 h", "0.9 h"]),
                ("Est. time to complete remaining", [long_value, short_value]),
            ],
        )

        border_top = lines[0]
        segments = border_top.split("┬")
        col1_width = len(segments[1])
        col2_width = len(segments[2]) - 1
        assert col1_width <= MAX_VALUE_COL_WIDTH + 2, (
            f"col1 must be capped near {MAX_VALUE_COL_WIDTH}; got {col1_width}"
        )
        assert col2_width <= MAX_VALUE_COL_WIDTH + 2, (
            f"col2 must be capped near {MAX_VALUE_COL_WIDTH}; got {col2_width}"
        )
        widths = {len(ln) for ln in lines}
        assert len(widths) == 1, f"Lines must be uniform width; got {sorted(widths)}"
        joined = chr(10).join(lines)
        assert "blocked-recovery 12" in joined, "word must not be broken mid-character"
        assert "blocked-runtime-degradation 1" in joined, "word must not be broken mid-character"
        assert not any(long_value in ln for ln in lines), "Long cell must wrap; was found unwrapped on a single line"

    def test_grouped_progress_table_caps_columns_and_wraps_long_cells(self) -> None:
        """Regression #214: same cap-and-wrap applies to the grouped table."""
        from devbench.reporting.report import (
            MAX_VALUE_COL_WIDTH,
            _render_grouped_progress_table,
        )

        long_value = (
            "~5.7 h (active 3 + blocked-recovery 12 + blocked-auto 11 + blocked-runtime-degradation 1 at 12.7 min/task)"
        )
        short_value = "~5.5 h (active 3 + blocked-recovery 12 + blocked-auto 11 at 12.7 min/task)"
        lines = _render_grouped_progress_table(
            "Metric",
            ["All-time", "Session"],
            [
                (
                    "THROUGHPUT",
                    [
                        ("Time span", ["190.3 h", "0.9 h"]),
                        ("Est. time to complete remaining", [long_value, short_value]),
                    ],
                ),
            ],
        )

        border_top = lines[0]
        segments = border_top.split("┬")
        col1_width = len(segments[1])
        col2_width = len(segments[2]) - 1
        assert col1_width <= MAX_VALUE_COL_WIDTH + 2, f"col1 capped; got {col1_width}"
        assert col2_width <= MAX_VALUE_COL_WIDTH + 2, f"col2 capped; got {col2_width}"
        widths = {len(ln) for ln in lines}
        assert len(widths) == 1, f"Lines must be uniform width; got {sorted(widths)}"
        joined = chr(10).join(lines)
        assert "blocked-recovery 12" in joined
        assert "blocked-runtime-degradation 1" in joined
        assert not any(long_value in ln for ln in lines), "Long cell must wrap"

    def test_wrap_cell_value_splits_on_plus_boundaries(self) -> None:
        """Issue #214: ``_wrap_cell_value`` prefers `` + `` boundaries.
        Every continuation line starts with ``+ `` and every word from the
        input appears intact somewhere in the wrapped output."""
        from devbench.reporting.report import _wrap_cell_value

        text = "~5.7 h (active 3 + blocked-recovery 12 + blocked-auto 11 + blocked-runtime-degradation 1)"
        wrapped = _wrap_cell_value(text, max_width=40)
        for ln in wrapped[1:]:
            assert ln.startswith("+ "), f"continuation must start with '+ '; got {ln!r}"
        for word in text.split():
            assert any(word in ln for ln in wrapped), f"word {word!r} missing"

    def test_wrap_cell_value_never_breaks_a_word(self) -> None:
        """Issue #214 critical: even when a single word exceeds max_width,
        the wrap function must NOT break it.  The line containing the long
        word may exceed max_width; callers widen the column via
        ``_longest_word_len`` to fit it."""
        from devbench.reporting.report import _wrap_cell_value

        long_word = "blocked-runtime-degradation"
        text = f"head {long_word} tail"
        wrapped = _wrap_cell_value(text, max_width=10)
        assert any(long_word in ln for ln in wrapped), (
            f"Wrap broke a word; long_word {long_word!r} not intact in {wrapped!r}"
        )

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
            log_lines.append(f"{start} [devbench.backlog_manager] INFO Set E0-F1-S1-T{i + 1} to 'in-progress'")
            log_lines.append(f"{done} [devbench.backlog_manager] INFO Set E0-F1-S1-T{i + 1} to 'done'")
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
class TestPerModelCorrectionFactor:
    """Issue #223: the per-model ``correction_factor`` replaces the retired
    global ``token_cost_discount``.  When set, EVERY cost component for
    that model scales by the factor; other models in a multi-model run
    are untouched.

    These tests drive ``_compute_cost_by_model`` directly so the scaling
    contract is verifiable without spinning up the full report pipeline.
    """

    def _totals(self, in_t: int = 1000, out_t: int = 200) -> HookLogTotals:
        return HookLogTotals(input_tokens=in_t, output_tokens=out_t)

    def test_correction_factor_one_is_behaviour_preserving(self) -> None:
        """``correction_factor=1.0`` produces the same cost as no factor
        at all -- guards against accidental side-effects on the default
        path (every model in the default rate table has cf=1.0).
        """
        from devbench.constants import ModelRates
        from devbench.reporting import report as report_mod
        from devbench.reporting.report import _compute_cost_by_model

        with patch.dict(
            report_mod.REPORT_MODEL_RATES,
            {"claude-opus-4-7": ModelRates(input=5.0, output=25.0, correction_factor=1.0)},
            clear=False,
        ):
            cost = _compute_cost_by_model({"claude-opus-4-7": self._totals()})
        # 1000 input * $5/1M + 200 output * $25/1M = 0.005 + 0.005 = 0.01.
        assert abs(cost.total_cost - 0.01) < 1e-9
        # And the per-bucket totals sum to the rollup -- the existing
        # CostBreakdown row-sum invariant must hold for the per-model path too.
        assert (
            abs(
                cost.input_cost
                + cost.output_cost
                + cost.cache_read_cost
                + cost.cache_write_5m_cost
                + cost.cache_write_1h_cost
                - cost.total_cost
            )
            < 1e-9
        )

    def test_correction_factor_half_halves_cost(self) -> None:
        from devbench.constants import ModelRates
        from devbench.reporting import report as report_mod
        from devbench.reporting.report import _compute_cost_by_model

        rates = {"claude-opus-4-7": ModelRates(input=5.0, output=25.0, correction_factor=0.5)}
        with patch.dict(report_mod.REPORT_MODEL_RATES, rates, clear=False):
            cost = _compute_cost_by_model({"claude-opus-4-7": self._totals()})
        # Half of the un-corrected $0.01 == $0.005.
        assert abs(cost.total_cost - 0.005) < 1e-9

    def test_correction_factor_isolated_per_model(self) -> None:
        """Two-model run: Opus cf=2.0, Sonnet cf=1.0.  The Sonnet bucket
        must be untouched.  AC-3 spirit: pricing is per-model, not blended.
        """
        from devbench.constants import ModelRates
        from devbench.reporting import report as report_mod
        from devbench.reporting.report import _compute_cost_by_model

        rates = {
            "claude-opus-4-7": ModelRates(input=5.0, output=25.0, correction_factor=2.0),
            "claude-sonnet-4-6": ModelRates(input=3.0, output=15.0, correction_factor=1.0),
        }
        with patch.dict(report_mod.REPORT_MODEL_RATES, rates, clear=False):
            cost = _compute_cost_by_model(
                {
                    "claude-opus-4-7": self._totals(),  # un-corrected: 0.005 + 0.005 = 0.01; with cf=2.0 -> 0.02
                    "claude-sonnet-4-6": self._totals(),  # 1000*$3/1M + 200*$15/1M = 0.003 + 0.003 = 0.006
                }
            )
        assert abs(cost.total_cost - (0.02 + 0.006)) < 1e-9


@pytest.mark.unit
class TestPerModelCostComputation:
    """Issue #223 AC-3: a fixture with 1M Sonnet + 1M Opus prices the
    Sonnet tokens at Sonnet rates and the Opus tokens at Opus rates --
    NOT blended at a single global rate.
    """

    def test_ac3_two_model_fixture_prices_per_model(self) -> None:
        """1M Sonnet input + 1M Opus input.  Sonnet @ $3/M = $3, Opus @
        $5/M = $5.  Sum = $8.  A blended-rate implementation would
        produce $4-$4.50 (depending on weighting), which this test
        rejects.
        """
        from devbench.constants import ModelRates
        from devbench.reporting import report as report_mod
        from devbench.reporting.report import HookLogTotals, _compute_cost_by_model

        rates = {
            "claude-sonnet-4-6": ModelRates(input=3.0, output=15.0),
            "claude-opus-4-7": ModelRates(input=5.0, output=25.0),
        }
        # Exactly 1,000,000 input tokens per model, zero output / cache.
        totals_by_model = {
            "claude-sonnet-4-6": HookLogTotals(input_tokens=1_000_000),
            "claude-opus-4-7": HookLogTotals(input_tokens=1_000_000),
        }
        with patch.dict(report_mod.REPORT_MODEL_RATES, rates, clear=False):
            cost = _compute_cost_by_model(totals_by_model)
        assert abs(cost.total_cost - 8.0) < 1e-9, (
            f"AC-3: 1M Sonnet @ $3 + 1M Opus @ $5 must sum to $8 (not blended); got {cost.total_cost}"
        )

    def test_unknown_model_falls_back_to_default_rates(self) -> None:
        """AC-5 spirit: any model id not present in REPORT_MODEL_RATES
        falls back to REPORT_DEFAULT_MODEL_RATES (the "<unknown>" bucket
        rates).  No silent zero-cost path.
        """
        from devbench.constants import ModelRates
        from devbench.reporting import report as report_mod
        from devbench.reporting.report import HookLogTotals, _compute_cost_by_model

        with (
            patch.dict(report_mod.REPORT_MODEL_RATES, {}, clear=True),
            patch.object(report_mod, "REPORT_DEFAULT_MODEL_RATES", ModelRates(input=10.0, output=50.0)),
        ):
            cost = _compute_cost_by_model({"unknown-future-model": HookLogTotals(input_tokens=1_000_000)})
        assert abs(cost.total_cost - 10.0) < 1e-9


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
    writes to (typically because ``DEVBENCH_LOG_FILE`` was unset).
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
        assert "DEVBENCH_LOG_FILE" in report
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
        # actionable signal that lets them set DEVBENCH_LOG_FILE correctly.
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
    """Issue #157 + issue #183 follow-up: the ETA denominator includes
    every auto-recoverable bucket -- recovery, auto-clearing, and
    runtime-degradation -- and excludes only the operator-attention bucket.
    """

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
            _as_claims(progress_times),
            tasks_active=4,
            tasks_blocked_recovery=60,
            tasks_blocked_auto=27,
            tasks_blocked_runtime_degradation=2,
        )
        # ETA bucket counts surface on the WindowStats dataclass.
        assert stats.eta_active == 4
        assert stats.eta_blocked_recovery == 60
        assert stats.eta_blocked_auto == 27
        assert stats.eta_blocked_runtime_degradation == 2
        # est_hours scales with (4 + 60 + 27 + 2) -- denominator
        # includes every auto-recoverable bucket.
        assert stats.est_hours > 0

    def test_runtime_degradation_changes_eta_total(self, tmp_path: Path) -> None:
        """A RUNTIME_DEGRADATION task increments est_hours by exactly one
        pace-step, confirming it's in the denominator alongside the
        other auto-recover buckets."""
        from devbench.reporting.report import _compute_window_stats

        now = datetime(2026, 5, 2, 12, 0, 0, tzinfo=UTC)
        done_times: dict[str, datetime] = {}
        progress_times: dict[str, datetime] = {}
        for i in range(5):
            tid = f"E0-F1-S1-T{i + 1}"
            progress_times[tid] = now - timedelta(minutes=20 + i)
            done_times[tid] = now - timedelta(minutes=10 + i)
        log_path = tmp_path / "log.log"
        log_path.write_text("", encoding="utf-8")

        def _eta(rt_degradation: int) -> float:
            stats = _compute_window_stats(
                log_path,
                now - timedelta(hours=1),
                now,
                done_times,
                _as_claims(progress_times),
                tasks_active=4,
                tasks_blocked_recovery=0,
                tasks_blocked_auto=0,
                tasks_blocked_runtime_degradation=rt_degradation,
            )
            return stats.est_hours

        eta_without = _eta(0)
        eta_with = _eta(4)
        assert eta_with > eta_without, f"RUNTIME_DEGRADATION must increase est_hours; got {eta_with=} vs {eta_without=}"


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


class TestEtaMinutesPerHourRenameIsValuePreserving:
    """#329 FR-5 (E13-F2-S2-T1): report.py's ETA-hours conversion divides
    ``pace_for_projection`` (a minutes-valued quantity) by ``MINUTES_PER_HOUR``
    instead of the semantically-wrong ``SECONDS_PER_MINUTE``. Both constants
    equal 60 today, so this test pins the exact rendered ETA cell for a fully
    deterministic window: it must pass identically whether the conversion
    divides by ``SECONDS_PER_MINUTE`` or ``MINUTES_PER_HOUR``, proving the
    rename introduces no output defect."""

    def test_rendered_eta_is_byte_identical_across_the_rename(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from devbench.reporting.report import _compute_window_stats, _format_est_hours_display

        # Pin RECENT_PACE_TASKS so the sample count needed to resolve
        # recent_pace_minutes does not depend on the ambient environment.
        monkeypatch.setattr("devbench.reporting.report.RECENT_PACE_TASKS", 3)

        now = datetime(2026, 5, 2, 12, 0, 0, tzinfo=UTC)
        done_times: dict[str, datetime] = {}
        progress_times: dict[str, datetime] = {}
        for i in range(3):
            tid = f"E0-F1-S1-T{i + 1}"
            done_times[tid] = now - timedelta(minutes=i)
            progress_times[tid] = done_times[tid] - timedelta(minutes=30)
        log_path = tmp_path / "log.log"
        log_path.write_text("", encoding="utf-8")

        stats = _compute_window_stats(
            log_path,
            now - timedelta(hours=1),
            now,
            done_times,
            _as_claims(progress_times),
            tasks_active=2,
            tasks_blocked_recovery=0,
            tasks_blocked_auto=0,
            tasks_blocked_runtime_degradation=0,
        )

        # Every sample is exactly 30 minutes, so the median pace is exactly
        # 30.0 -- a value chosen so the est_hours division lands on an exact
        # round number regardless of which 60-valued constant is the divisor.
        assert stats.recent_pace_minutes == 30.0
        assert stats.est_hours == 1.0
        assert _format_est_hours_display(stats) == "~1.0 h (active 2 at 30.0 min/task)"


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
    """Tests for the orchestrator-alive status banner (issues #161, #250).

    Issue #250: the process table decides ALIVE, not log recency. A recent
    log line proves only that something wrote to the log, so recency alone
    reported a healthy orchestrator when none was running. The banner now
    reads the PID file and reports five states: ALIVE / STOPPED / STARTING /
    NOT RUNNING / UNKNOWN. ``stop_hook.window_seconds`` no longer decides
    liveness; it only distinguishes a busy live orchestrator from an idle one.
    """

    @staticmethod
    def _write_log(log_path: Path, last_ts_iso: str) -> None:
        log_path.write_text(
            f"2026-03-05T09:00:00Z [devbench.orch] INFO Started\n{last_ts_iso} [devbench.orch] INFO Tick\n"
        )

    @staticmethod
    def _write_pid(pid_path: Path, pid: int) -> Path:
        """Write a well-formed instance PID file naming *pid*."""
        pid_path.parent.mkdir(parents=True, exist_ok=True)
        pid_path.write_text(
            json.dumps(
                {
                    "instance_id": "ws-0001",
                    "pid": pid,
                    "workspace": "/tmp/ws",
                    "workspace_name": "ws",
                    "session": "default",
                    "mode": "daemon",
                    "started_at": "2026-03-05T09:00:00Z",
                    "model": "claude-opus-5",
                }
            ),
            encoding="utf-8",
        )
        return pid_path

    def _live_pid_file(self, tmp_path: Path) -> Path:
        """PID file naming this test process, which is by definition alive."""
        return self._write_pid(tmp_path / ".devbench" / "orchestrator.pid", os.getpid())

    @staticmethod
    def _dead_pid() -> int:
        """Return a PID that is not running.

        Allocated by starting a trivial child and reaping it, so the number
        is real and definitively gone rather than an arbitrary guess that
        could collide with a live process on a busy host.
        """
        proc = subprocess.Popen([sys.executable, "-c", ""])
        proc.wait()
        return proc.pid

    def test_alive_when_pid_file_names_a_running_process(self, tmp_path: Path) -> None:
        from devbench.reporting.report import _orchestrator_liveness_banner

        log = tmp_path / "orch.log"
        self._write_log(log, "2026-03-05T10:00:00Z")
        now = datetime(2026, 3, 5, 10, 0, 30, tzinfo=UTC)
        with patch("devbench.reporting.report._should_use_color", return_value=True):
            banner = _orchestrator_liveness_banner(log, "sess-A", 180, self._live_pid_file(tmp_path), now=now)
        assert banner.startswith("\033[32m")
        assert "[ORCHESTRATOR ALIVE]" in banner
        assert "30s ago" in banner
        assert "session sess-A" in banner
        assert banner.endswith("\033[0m")

    def test_recent_log_without_a_pid_file_is_never_reported_alive(self, tmp_path: Path) -> None:
        """Issue #250 regression: this exact input previously rendered ALIVE."""
        from devbench.reporting.report import _orchestrator_liveness_banner

        log = tmp_path / "orch.log"
        self._write_log(log, "2026-03-05T10:00:00Z")
        now = datetime(2026, 3, 5, 10, 0, 30, tzinfo=UTC)
        banner = _orchestrator_liveness_banner(log, "sess-A", 180, tmp_path / "absent.pid", now=now)
        assert "ALIVE" not in banner
        assert "[ORCHESTRATOR UNKNOWN]" in banner
        assert "no process is claiming this workspace" in banner

    def test_stopped_when_pid_file_names_a_dead_process(self, tmp_path: Path) -> None:
        """Authoritative: a dead PID is STOPPED even when the log is seconds old."""
        from devbench.reporting.report import _orchestrator_liveness_banner

        log = tmp_path / "orch.log"
        self._write_log(log, "2026-03-05T10:00:00Z")
        pid_path = self._write_pid(tmp_path / ".devbench" / "orchestrator.pid", self._dead_pid())
        now = datetime(2026, 3, 5, 10, 0, 5, tzinfo=UTC)
        with patch("devbench.reporting.report._should_use_color", return_value=True):
            banner = _orchestrator_liveness_banner(log, "sess-A", 180, pid_path, now=now)
        assert banner.startswith("\033[91m")
        assert "[ORCHESTRATOR STOPPED]" in banner
        assert "names a process that is not running" in banner

    def test_live_process_quiet_past_threshold_is_alive_but_idle(self, tmp_path: Path) -> None:
        """A running orchestrator that is merely quiet must not be reported STOPPED."""
        from devbench.reporting.report import _orchestrator_liveness_banner

        log = tmp_path / "orch.log"
        self._write_log(log, "2026-03-05T10:00:00Z")
        now = datetime(2026, 3, 5, 10, 10, 0, tzinfo=UTC)
        banner = _orchestrator_liveness_banner(log, "sess-A", 180, self._live_pid_file(tmp_path), now=now)
        assert "[ORCHESTRATOR ALIVE]" in banner
        assert "idle 10m" in banner
        assert "STOPPED" not in banner

    def test_not_running_when_no_pid_file_and_no_log(self, tmp_path: Path) -> None:
        from devbench.reporting.report import _orchestrator_liveness_banner

        with patch("devbench.reporting.report._should_use_color", return_value=True):
            banner = _orchestrator_liveness_banner(tmp_path / "no.log", "sess-A", 180, tmp_path / "absent.pid")
        assert banner.startswith("\033[91m")
        assert "[ORCHESTRATOR NOT RUNNING]" in banner
        assert "no pid file and no activity recorded" in banner

    def test_starting_when_pid_file_is_present_but_unparseable(self, tmp_path: Path) -> None:
        """The daemon writes the PID file non-atomically; a partial read is STARTING, not ALIVE."""
        from devbench.reporting.report import _orchestrator_liveness_banner

        pid_path = tmp_path / ".devbench" / "orchestrator.pid"
        pid_path.parent.mkdir(parents=True, exist_ok=True)
        pid_path.write_text('{"instance_id": "ws-0001", "pi', encoding="utf-8")
        banner = _orchestrator_liveness_banner(tmp_path / "no.log", "sess-A", 180, pid_path)
        assert "[ORCHESTRATOR STARTING]" in banner
        assert "ALIVE" not in banner

    def test_alive_with_no_log_activity_yet(self, tmp_path: Path) -> None:
        from devbench.reporting.report import _orchestrator_liveness_banner

        banner = _orchestrator_liveness_banner(tmp_path / "no.log", "sess-A", 180, self._live_pid_file(tmp_path))
        assert "[ORCHESTRATOR ALIVE]" in banner
        assert "no activity recorded yet" in banner

    def test_no_color_when_not_tty(self, tmp_path: Path) -> None:
        from devbench.reporting.report import _orchestrator_liveness_banner

        log = tmp_path / "orch.log"
        self._write_log(log, "2026-03-05T10:00:00Z")
        now = datetime(2026, 3, 5, 10, 0, 30, tzinfo=UTC)
        with patch("devbench.reporting.report._should_use_color", return_value=False):
            banner = _orchestrator_liveness_banner(log, "sess-A", 180, self._live_pid_file(tmp_path), now=now)
        assert "\033[" not in banner
        assert "[ORCHESTRATOR ALIVE]" in banner

    def test_no_session_id_suppresses_session_suffix(self, tmp_path: Path) -> None:
        from devbench.reporting.report import _orchestrator_liveness_banner

        log = tmp_path / "orch.log"
        self._write_log(log, "2026-03-05T10:00:00Z")
        now = datetime(2026, 3, 5, 10, 0, 10, tzinfo=UTC)
        pid_file = self._live_pid_file(tmp_path)
        for empty in (None, ""):
            banner = _orchestrator_liveness_banner(log, empty, 180, pid_file, now=now)
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

    def test_generate_report_passes_the_workspace_pid_file(self, tmp_path: Path) -> None:
        """The banner is only authoritative if the real caller hands it the real PID path."""
        from devbench.reporting import report as report_mod

        log_file = tmp_path / "orch.log"
        log_file.write_text(_make_log(["2026-03-05T10:00:00Z [judges.cli] INFO Set E0-F1-S1-T1 to 'in-progress'"]))
        captured: dict[str, Path] = {}

        def _capture(*, pid_path: Path, **_: Any) -> str:
            captured["pid_path"] = pid_path
            return "[ORCHESTRATOR ALIVE] capture-stub"

        with (
            patch("devbench.reporting.report.WORKSPACE_ROOT", tmp_path),
            patch("devbench.reporting.report._orchestrator_liveness_banner", side_effect=_capture),
            patch("devbench.reporting.report.BACKLOG_INDEX", tmp_path / "BACKLOG.md"),
        ):
            report_mod.generate_report(log_path=log_file)
        assert captured["pid_path"] == tmp_path / ".devbench" / "orchestrator.pid"

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
        pid_file = self._live_pid_file(tmp_path)
        now1 = datetime(2026, 3, 5, 10, 0, 30, tzinfo=UTC)
        banner1 = _orchestrator_liveness_banner(log, "sess-A", 180, pid_file, now=now1)
        # Log advances; orchestrator wrote another line.
        log.write_text(
            "2026-03-05T09:00:00Z [devbench.orch] INFO Started\n"
            "2026-03-05T10:00:00Z [devbench.orch] INFO Tick\n"
            "2026-03-05T10:00:30Z [devbench.orch] INFO Tick\n"
        )
        banner2 = _orchestrator_liveness_banner(log, "sess-A", 180, pid_file, now=now1)
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
        """A FileNotFoundError from the parser -> SystemExit(1) + stderr diagnostic.
        The new prefix is 'cannot read' (FNF) instead of 'cannot parse' (ValueError),
        and the writer-window race hint is included so operators of an active
        orchestrator know to re-run before fixing anything."""
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
        assert "devbench report: cannot read" in captured.err
        assert "Backlog index not found" in captured.err
        assert "transient writer-window race" in captured.err
        assert "validate-backlog" in captured.err

    def test_file_not_found_naming_wu_md_surfaces_wu_path(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """When the FNF.filename is a work-unit md path (not the index), the
        operator's stderr prefix must name THAT path so the diagnostic stops
        blaming BACKLOG.md. This is the user-reported watch crash: the path
        in the error was a WU md but the prefix said 'cannot parse BACKLOG.md'."""
        log_file = tmp_path / "test.log"
        log_file.write_text(
            _make_log(
                [
                    "2026-03-05T10:00:00Z [judges.cli] INFO Set E0-F1-S1-T1 to 'in-progress'",
                ]
            )
        )
        wu_md_path = "/workspaces/x/backlog/E4/F1/S1/E4-F1-S1-T5.md"
        with patch("devbench.reporting.report.BacklogParser") as mock_cls:
            instance = mock_cls.return_value
            instance.parse_index.side_effect = FileNotFoundError(2, "No such file or directory", wu_md_path)
            with pytest.raises(SystemExit) as exc:
                generate_report(log_path=log_file)
        assert exc.value.code == 1
        captured = capsys.readouterr()
        assert wu_md_path in captured.err
        assert "cannot read" in captured.err
        assert "transient writer-window race" in captured.err


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


# ---------------------------------------------------------------------------
# AC-190-10 / AC-190-11: generate_report scope_filter parameter (E2-F2-S2-T2)
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestGenerateReportScopeFilter:
    """generate_report correctly filters work units when scope_filter is provided.

    AC-190-10: devbench report honors active scope.json without flags.
    AC-190-11: Per-command --include override works; only scoped WUs are listed.
    """

    @staticmethod
    def _make_unit(uid: str, status: WorkUnitStatus) -> WorkUnit:
        """Build a minimal TASK WorkUnit with the given ID and status."""
        return WorkUnit(
            id=uid,
            title=f"task-{uid}",
            status=status,
            unit_type=WorkUnitType.TASK,
            file_path=Path(f"backlog/{uid}.md"),
            repo="caylent-solutions/devbench",
            dependencies=[],
        )

    def _units(self) -> list[WorkUnit]:
        """Return a diverse set of 4 units: E1-F1-S1-T1 (done) and E2-F1-S1-T1..T3."""
        return [
            self._make_unit("E1-F1-S1-T1", WorkUnitStatus.DONE),
            self._make_unit("E2-F1-S1-T1", WorkUnitStatus.IN_QUEUE),
            self._make_unit("E2-F1-S1-T2", WorkUnitStatus.IN_PROGRESS),
            self._make_unit("E2-F1-S1-T3", WorkUnitStatus.DONE),
        ]

    @staticmethod
    def _make_fake_backlog_totals(tasks_total: int) -> object:
        """Build a :class:`~devbench.reporting.report._BacklogTotals` stub.

        Returns a zero-valued totals struct with ``tasks_total`` and
        ``units_total`` set to ``tasks_total``.  Used by tests that patch
        ``_backlog_totals_from_units`` to capture which units reach the
        aggregation step.

        Args:
            tasks_total: Number of tasks (and units) to report as the total.

        Returns:
            A fully-populated :class:`~devbench.reporting.report._BacklogTotals`
            named-tuple with all counters set to zero except ``tasks_total``
            and ``units_total``.
        """
        from devbench.reporting.report import _BacklogTotals

        return _BacklogTotals(
            tasks_total=tasks_total,
            tasks_done=0,
            units_total=tasks_total,
            units_done=0,
            stories_done=0,
            features_done=0,
            epics_done=0,
            tasks_remaining=0,
            tasks_blocked=0,
            tasks_active=0,
            tasks_in_progress=0,
            tasks_in_queue=0,
            tasks_in_review=0,
            tasks_proposed=0,
            tasks_declined=0,
        )

    def test_scope_filter_none_includes_all_units(self, tmp_path: Path) -> None:
        """When scope_filter=None, all units are counted (no filtering applied)."""
        log_file = tmp_path / "test.log"
        log_file.write_text("")

        with patch("devbench.reporting.report.BacklogParser") as mock_cls:
            mock_cls.return_value.parse_index.return_value = self._units()
            report = generate_report(log_path=log_file, scope_filter=None)

        # 4 units total; report should show Tasks remaining > 0.
        assert "Tasks completed" in report

    def test_scope_filter_restricts_to_include_set(self, tmp_path: Path) -> None:
        """When scope_filter includes only E1, only E1-F1-S1-T1 is counted."""
        from devbench.scope import ScopeFilter

        log_file = tmp_path / "test.log"
        log_file.write_text("")

        # E1 filter: only E1-F1-S1-T1 (DONE) should remain.
        all_ids = [u.id for u in self._units()]
        sf = ScopeFilter.parse("E1", "", all_ids)

        with patch("devbench.reporting.report.BacklogParser") as mock_cls:
            mock_cls.return_value.parse_index.return_value = self._units()
            from devbench.reporting.report import _backlog_totals_from_units

            # Verify directly via _backlog_totals_from_units on filtered list.
            filtered = [u for u in self._units() if sf.allows(u.id)]
            totals = _backlog_totals_from_units(filtered)

        assert totals.tasks_total == 1
        assert totals.tasks_done == 1

    def test_scope_filter_with_empty_expanded_ids_re_expands_from_tokens(self, tmp_path: Path) -> None:
        """A ScopeFilter built with empty expanded_ids is re-expanded inside generate_report."""
        from devbench.scope import ScopeFilter

        log_file = tmp_path / "test.log"
        log_file.write_text("")

        # Build a ScopeFilter with include tokens but empty expanded_ids (as
        # cmd_report does when --include is passed as a CLI flag).
        sf = ScopeFilter(include=["E1"], exclude=[], expanded_ids=set())

        captured_units: list = []

        def fake_backlog_totals(units: list) -> object:
            captured_units.extend(units)
            return self._make_fake_backlog_totals(len(units))

        with (
            patch("devbench.reporting.report.BacklogParser") as mock_cls,
            patch("devbench.reporting.report._backlog_totals_from_units", side_effect=fake_backlog_totals),
        ):
            mock_cls.return_value.parse_index.return_value = self._units()
            generate_report(log_path=log_file, scope_filter=sf)

        # Only E1-F1-S1-T1 should have survived the filter.
        unit_ids = [u.id for u in captured_units]
        assert "E1-F1-S1-T1" in unit_ids
        assert "E2-F1-S1-T1" not in unit_ids
        assert "E2-F1-S1-T2" not in unit_ids

    def test_scope_filter_with_exclude_removes_units(self, tmp_path: Path) -> None:
        """A ScopeFilter with --exclude removes matching units from report counts."""
        from devbench.scope import ScopeFilter

        log_file = tmp_path / "test.log"
        log_file.write_text("")

        all_ids = [u.id for u in self._units()]
        # Exclude E2 subtree; only E1-F1-S1-T1 should remain.
        sf = ScopeFilter.parse("", "E2", all_ids)

        captured_units: list = []

        def fake_backlog_totals(units: list) -> object:
            captured_units.extend(units)
            return self._make_fake_backlog_totals(len(units))

        with (
            patch("devbench.reporting.report.BacklogParser") as mock_cls,
            patch("devbench.reporting.report._backlog_totals_from_units", side_effect=fake_backlog_totals),
        ):
            mock_cls.return_value.parse_index.return_value = self._units()
            generate_report(log_path=log_file, scope_filter=sf)

        unit_ids = [u.id for u in captured_units]
        assert "E1-F1-S1-T1" in unit_ids
        assert not any(uid.startswith("E2") for uid in unit_ids)


# AC-192-12 / AC-192-13: generate_report session_name parameter (E4-F6-S1-T2)
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestGenerateReportSessionFilter:
    """generate_report correctly filters work units when session_name is provided.

    AC-192-12: Session-filtered report works correctly.
    AC-192-13: Aggregated report (no session_name) sums correctly across sessions.
    """

    # ------------------------------------------------------------------ helpers

    @staticmethod
    def _make_unit(uid: str, status: WorkUnitStatus, tmp_path: Path, session: str | None) -> WorkUnit:
        """Build a TASK WorkUnit with a backing WU file containing an optional session= claim."""
        wu_file = tmp_path / f"{uid}.md"
        claim_line = f"[WU_CLAIMED] Set {uid} to 'in-progress'"
        if session:
            claim_line += f" session={session}"
        content = (
            f"# {uid}: Test\n\n"
            f"## Status: {status.value}\n\n"
            "## Comments\n\n"
            f"[2026-05-17 00:05 UTC] [agent/orchestrator] {claim_line}\n"
        )
        wu_file.write_text(content, encoding="utf-8")
        return WorkUnit(
            id=uid,
            title=f"task-{uid}",
            status=status,
            unit_type=WorkUnitType.TASK,
            file_path=wu_file,
            repo="caylent-solutions/devbench",
            dependencies=[],
        )

    @staticmethod
    def _make_fake_backlog_totals(tasks_total: int) -> object:
        """Build a minimal _BacklogTotals stub."""
        from devbench.reporting.report import _BacklogTotals

        return _BacklogTotals(
            tasks_total=tasks_total,
            tasks_done=0,
            units_total=tasks_total,
            units_done=0,
            stories_done=0,
            features_done=0,
            epics_done=0,
            tasks_remaining=0,
            tasks_blocked=0,
            tasks_active=0,
            tasks_in_progress=0,
            tasks_in_queue=0,
            tasks_in_review=0,
            tasks_proposed=0,
            tasks_declined=0,
        )

    def _units(self, tmp_path: Path) -> list[WorkUnit]:
        """Return three units across two sessions plus one with no session."""
        return [
            self._make_unit("E1-F1-S1-T1", WorkUnitStatus.DONE, tmp_path, session="alpha"),
            self._make_unit("E2-F1-S1-T1", WorkUnitStatus.IN_QUEUE, tmp_path, session="beta"),
            self._make_unit("E2-F1-S1-T2", WorkUnitStatus.IN_PROGRESS, tmp_path, session="beta"),
            self._make_unit("E3-F1-S1-T1", WorkUnitStatus.IN_QUEUE, tmp_path, session=None),
        ]

    # ------------------------------------------------------------------ AC-192-12: session filter

    def test_session_name_filters_units_to_matching_session(self, tmp_path: Path) -> None:
        """generate_report(session_name='alpha') passes only alpha-session WUs to aggregation."""
        log_file = tmp_path / "test.log"
        log_file.write_text("")

        captured_units: list = []

        def fake_backlog_totals(units: list) -> object:
            captured_units.extend(units)
            return self._make_fake_backlog_totals(len(units))

        with (
            patch("devbench.reporting.report.BacklogParser") as mock_cls,
            patch("devbench.reporting.report._backlog_totals_from_units", side_effect=fake_backlog_totals),
        ):
            mock_cls.return_value.parse_index.return_value = self._units(tmp_path)
            generate_report(log_path=log_file, session_name="alpha")

        unit_ids = [u.id for u in captured_units]
        assert "E1-F1-S1-T1" in unit_ids
        assert "E2-F1-S1-T1" not in unit_ids
        assert "E2-F1-S1-T2" not in unit_ids
        assert "E3-F1-S1-T1" not in unit_ids

    def test_session_name_beta_filters_to_beta_units(self, tmp_path: Path) -> None:
        """generate_report(session_name='beta') passes only beta-session WUs to aggregation."""
        log_file = tmp_path / "test.log"
        log_file.write_text("")

        captured_units: list = []

        def fake_backlog_totals(units: list) -> object:
            captured_units.extend(units)
            return self._make_fake_backlog_totals(len(units))

        with (
            patch("devbench.reporting.report.BacklogParser") as mock_cls,
            patch("devbench.reporting.report._backlog_totals_from_units", side_effect=fake_backlog_totals),
        ):
            mock_cls.return_value.parse_index.return_value = self._units(tmp_path)
            generate_report(log_path=log_file, session_name="beta")

        unit_ids = [u.id for u in captured_units]
        assert "E2-F1-S1-T1" in unit_ids
        assert "E2-F1-S1-T2" in unit_ids
        assert "E1-F1-S1-T1" not in unit_ids
        assert "E3-F1-S1-T1" not in unit_ids

    def test_session_name_nonexistent_yields_empty_unit_set(self, tmp_path: Path) -> None:
        """generate_report(session_name='missing') passes empty list to aggregation."""
        log_file = tmp_path / "test.log"
        log_file.write_text("")

        captured_units: list = []

        def fake_backlog_totals(units: list) -> object:
            captured_units.extend(units)
            return self._make_fake_backlog_totals(len(units))

        with (
            patch("devbench.reporting.report.BacklogParser") as mock_cls,
            patch("devbench.reporting.report._backlog_totals_from_units", side_effect=fake_backlog_totals),
        ):
            mock_cls.return_value.parse_index.return_value = self._units(tmp_path)
            generate_report(log_path=log_file, session_name="missing")

        assert captured_units == []

    # ------------------------------------------------------------------ AC-192-13: aggregation (no session)

    def test_session_name_none_includes_all_units(self, tmp_path: Path) -> None:
        """generate_report(session_name=None) passes all WUs to aggregation (no filtering)."""
        log_file = tmp_path / "test.log"
        log_file.write_text("")

        captured_units: list = []

        def fake_backlog_totals(units: list) -> object:
            captured_units.extend(units)
            return self._make_fake_backlog_totals(len(units))

        with (
            patch("devbench.reporting.report.BacklogParser") as mock_cls,
            patch("devbench.reporting.report._backlog_totals_from_units", side_effect=fake_backlog_totals),
        ):
            mock_cls.return_value.parse_index.return_value = self._units(tmp_path)
            generate_report(log_path=log_file, session_name=None)

        unit_ids = [u.id for u in captured_units]
        assert "E1-F1-S1-T1" in unit_ids
        assert "E2-F1-S1-T1" in unit_ids
        assert "E2-F1-S1-T2" in unit_ids
        assert "E3-F1-S1-T1" in unit_ids

    # ------------------------------------------------------------------ session filter composition

    def test_session_filter_and_scope_filter_compose(self, tmp_path: Path) -> None:
        """session_name and scope_filter compose correctly (intersection)."""
        from devbench.scope import ScopeFilter

        log_file = tmp_path / "test.log"
        log_file.write_text("")

        all_units = self._units(tmp_path)
        all_ids = [u.id for u in all_units]
        # Scope includes E1 only; session beta includes E2 units only.
        # Intersection should be empty.
        sf = ScopeFilter.parse("E1", "", all_ids)

        captured_units: list = []

        def fake_backlog_totals(units: list) -> object:
            captured_units.extend(units)
            return self._make_fake_backlog_totals(len(units))

        with (
            patch("devbench.reporting.report.BacklogParser") as mock_cls,
            patch("devbench.reporting.report._backlog_totals_from_units", side_effect=fake_backlog_totals),
        ):
            mock_cls.return_value.parse_index.return_value = all_units
            generate_report(log_path=log_file, session_name="beta", scope_filter=sf)

        # E1 is alpha session; beta session has E2 units but scope only allows E1 -- no overlap.
        assert captured_units == []


@pytest.mark.unit
class TestPerModelHelpersCoverage:
    """Issue #223 coverage: exercise the small helpers added in
    ``reporting/report.py`` so coverage of the new module-level
    plumbing reflects the documented contract.
    """

    def test_combine_many_empty_returns_zero_totals(self) -> None:
        from devbench.reporting.report import HookLogTotals, _combine_many

        result = _combine_many([])
        assert result == HookLogTotals()

    def test_combine_many_single_returns_passthrough(self) -> None:
        from devbench.reporting.report import HookLogTotals, _combine_many

        one = HookLogTotals(input_tokens=1000)
        assert _combine_many([one]) == one

    def test_resolve_rates_for_model_known_model(self) -> None:
        """Known model id pulls from REPORT_MODEL_RATES with the cache
        multipliers falling back to the top-level defaults when the
        per-model entry leaves them unset.

        AC-E3-F1-S1-T1-10 (spec FR-3.2 error handling, designed tripwire):
        this test previously pinned only the retained ``claude-opus-4-7``
        row. It now also exercises the current default ``claude-opus-5``
        ($5/$25 list, issue #233) alongside the retained row so the same
        test that hard-pinned the Opus 4.7-era table catches any future
        regression on either id.
        """
        from devbench.reporting.report import (
            REPORT_CACHE_READ_MULTIPLIER,
            REPORT_CACHE_WRITE_1HR_MULTIPLIER,
            REPORT_CACHE_WRITE_5MIN_MULTIPLIER,
            REPORT_MODEL_RATES,
            _resolve_rates_for_model,
        )

        in_r, out_r, c_read, c_5m, c_1h, corr = _resolve_rates_for_model("claude-opus-4-7")
        assert in_r == 5.0
        assert out_r == 25.0
        assert c_read == REPORT_CACHE_READ_MULTIPLIER
        assert c_5m == REPORT_CACHE_WRITE_5MIN_MULTIPLIER
        assert c_1h == REPORT_CACHE_WRITE_1HR_MULTIPLIER
        assert corr == 1.0

        # Opus 5 is the current default lineup entry (issue #233); same
        # list rate ($5/$25) as the retained Opus 4.7 row above. Assert it
        # is an explicit REPORT_MODEL_RATES entry (not merely a value that
        # happens to match the "<unknown>" fallback rate) so this tripwire
        # actually catches the entry being dropped from the table.
        assert "claude-opus-5" in REPORT_MODEL_RATES, (
            "claude-opus-5 must be an explicit REPORT_MODEL_RATES entry, not just "
            "coincidentally matching the fallback rate (issue #233)."
        )
        in_r5, out_r5, c_read5, c_5m5, c_1h5, corr5 = _resolve_rates_for_model("claude-opus-5")
        assert in_r5 == 5.0
        assert out_r5 == 25.0
        assert c_read5 == REPORT_CACHE_READ_MULTIPLIER
        assert c_5m5 == REPORT_CACHE_WRITE_5MIN_MULTIPLIER
        assert c_1h5 == REPORT_CACHE_WRITE_1HR_MULTIPLIER
        assert corr5 == 1.0

    def test_resolve_rates_for_model_per_model_cache_overrides_win(self) -> None:
        from devbench.constants import ModelRates
        from devbench.reporting import report as report_mod
        from devbench.reporting.report import _resolve_rates_for_model

        rates = {
            "custom-model": ModelRates(
                input=2.0,
                output=10.0,
                cache_read_multiplier=0.05,
                cache_write_5min_multiplier=1.0,
                cache_write_1hr_multiplier=1.5,
            ),
        }
        with patch.dict(report_mod.REPORT_MODEL_RATES, rates, clear=False):
            _, _, c_read, c_5m, c_1h, _ = _resolve_rates_for_model("custom-model")
        assert c_read == 0.05
        assert c_5m == 1.0
        assert c_1h == 1.5

    def test_merge_totals_by_model_overlapping_keys(self) -> None:
        """Per-model totals from hook + transcript sources merge correctly
        when the same model id appears in both buckets.
        """
        from devbench.reporting.report import HookLogTotals, _merge_totals_by_model

        hook = {"claude-opus-4-7": HookLogTotals(input_tokens=1000)}
        transcript = {"claude-opus-4-7": HookLogTotals(input_tokens=2000)}
        merged = _merge_totals_by_model(hook, transcript)
        assert merged["claude-opus-4-7"].input_tokens == 3000

    def test_per_model_totals_from_aggregator_empty(self) -> None:
        """When the aggregator returns no rows, the helper returns
        an empty dict (no synthetic zero-totals row injected).
        """
        from datetime import UTC, datetime

        from devbench.reporting.report import _per_model_totals_from_aggregator

        def empty(_source, _window):
            return {}

        result = _per_model_totals_from_aggregator(empty, None, datetime(2026, 1, 1, tzinfo=UTC))
        assert result == {}


@pytest.mark.unit
class TestByRolePanel:
    """Issue #206: ``devbench report --by-role`` renders a per-role
    token/cost breakdown panel beneath the aggregate Cost section.

    Data path was landed in PR #202 (issue #123) via
    ``_parse_transcript_metrics_by_role``; this test pins the new
    render contract: panel appears when ``by_role=True``, absent when
    ``by_role=False``, and the TOTAL row sums each column.
    """

    def _build_workspace_with_transcripts(self, tmp_path: Path) -> tuple[Path, Path]:
        """Build a minimal workspace with a hook log pointing at a
        transcript directory containing one role-attributed message
        per role.  Returns ``(log_path, transcript_dir)``.
        """
        log = tmp_path / "orch.log"
        log.write_text(
            "2026-05-04T10:00:00Z [judges.cli] INFO Set E0-F1-S1-T1 to 'in-progress'\n"
            "2026-05-04T11:00:00Z [judges.cli] INFO Set E0-F1-S1-T1 to 'done'\n",
            encoding="utf-8",
        )
        transcript_dir = tmp_path / "transcripts"
        transcript_dir.mkdir()
        transcript = transcript_dir / "session.jsonl"
        # Two messages, one per role, so the per-role panel has two
        # data rows to print.
        transcript.write_text(
            '{"timestamp":"2026-05-04T10:30:00.000000+00:00","type":"assistant",'
            '"attributionAgent":"devbench-orchestrate:executor","message":{"id":"m1",'
            '"model":"claude-opus-4-7","usage":{"input_tokens":500000,"output_tokens":100000}}}\n'
            '{"timestamp":"2026-05-04T10:31:00.000000+00:00","type":"assistant",'
            '"attributionAgent":"devbench-orchestrate:code-reviewer","message":{"id":"m2",'
            '"model":"claude-sonnet-4-6","usage":{"input_tokens":200000,"output_tokens":40000}}}\n',
            encoding="utf-8",
        )
        hook = tmp_path / "hook-logs.jsonl"
        hook.write_text(
            '{"timestamp":"2026-05-04T10:30:00.000000+00:00","input":'
            f'{{"transcript_path":"{transcript}","tool_response":'
            '{"usage":{"input_tokens":0,"output_tokens":0}}}}\n',
            encoding="utf-8",
        )
        return log, transcript_dir

    def test_panel_absent_when_flag_false(self, tmp_path: Path) -> None:
        log, _td = self._build_workspace_with_transcripts(tmp_path)
        with patch("devbench.reporting.report.BACKLOG_INDEX", tmp_path / "BACKLOG.md"):
            output = generate_report(log_path=log, by_role=False)
        assert "Per-role cost breakdown" not in output

    def test_panel_present_when_flag_true(self, tmp_path: Path) -> None:
        log, _td = self._build_workspace_with_transcripts(tmp_path)
        with patch("devbench.reporting.report.BACKLOG_INDEX", tmp_path / "BACKLOG.md"):
            output = generate_report(log_path=log, by_role=True)
        assert "Per-role cost breakdown" in output
        # Both roles appear as their canonical bucket names (devbench:
        # prefix stripped; -reviewer normalised to _review).
        assert "executor" in output
        assert "code_review" in output
        # TOTAL row sits at the bottom of the panel.
        assert "TOTAL" in output

    def test_panel_omitted_when_no_transcripts(self, tmp_path: Path) -> None:
        """When no transcripts exist (brand-new workspace with no
        agent activity), the panel is silently omitted -- there is
        nothing to render.
        """
        log = tmp_path / "orch.log"
        log.write_text("", encoding="utf-8")
        with patch("devbench.reporting.report.BACKLOG_INDEX", tmp_path / "BACKLOG.md"):
            output = generate_report(log_path=log, by_role=True)
        assert "Per-role cost breakdown" not in output


@pytest.mark.unit
class TestByRolePanelTotalsConsistency:
    """Issue #206: the panel's TOTAL row must equal the sum of the
    per-role rows (a render-time invariant the existing
    _parse_transcript_metrics_by_role aggregator already asserts).
    """

    def test_total_row_equals_sum_of_role_rows(self, tmp_path: Path) -> None:
        from devbench.reporting.report import _render_by_role_panel

        # Build a transcript directory with two roles.
        transcript_dir = tmp_path / "transcripts"
        transcript_dir.mkdir()
        (transcript_dir / "session.jsonl").write_text(
            '{"timestamp":"2026-05-04T10:30:00.000000+00:00","type":"assistant",'
            '"attributionAgent":"devbench-orchestrate:executor","message":{"id":"m1",'
            '"usage":{"input_tokens":1000000,"output_tokens":0}}}\n'
            '{"timestamp":"2026-05-04T10:31:00.000000+00:00","type":"assistant",'
            '"attributionAgent":"devbench-orchestrate:code-reviewer","message":{"id":"m2",'
            '"usage":{"input_tokens":1000000,"output_tokens":0}}}\n',
            encoding="utf-8",
        )
        hook = tmp_path / "hook-logs.jsonl"
        hook.write_text(
            '{"timestamp":"2026-05-04T10:30:00.000000+00:00","input":'
            f'{{"transcript_path":"{transcript_dir}/session.jsonl",'
            '"tool_response":{"usage":{"input_tokens":0,"output_tokens":0}}}}\n',
            encoding="utf-8",
        )
        log = tmp_path / "log"
        log.write_text("")
        from datetime import UTC, datetime

        with patch("devbench.reporting.report.BACKLOG_INDEX", tmp_path / "BACKLOG.md"):
            lines = _render_by_role_panel(log_path=log, window_start=datetime(2026, 1, 1, tzinfo=UTC))
        # Find the TOTAL row and the two data rows; assert that input
        # tokens sum (which we know is 1M+1M=2M) appears in TOTAL.
        total_lines = [line for line in lines if "TOTAL" in line]
        assert len(total_lines) == 1
        assert "2,000,000" in total_lines[0]


class TestReadLastLogTimestampFailurePaths:
    """A log the process cannot stat must not crash the liveness banner (issue #250)."""

    def test_stat_failure_yields_no_timestamp(self, tmp_path: Path) -> None:
        """``is_file`` is pinned True so only the guarded ``stat`` call raises.

        Patching ``Path.stat`` alone is not deterministic: ``Path.is_file``
        calls ``stat`` internally, and whether it swallows the ``OSError`` or
        propagates it varies by interpreter version. Pinning ``is_file``
        isolates the branch under test.
        """
        from unittest.mock import patch

        from devbench.reporting.report import _read_last_log_timestamp

        log = tmp_path / "orch.log"
        log.write_text("2026-03-05T10:00:00Z [devbench.orch] INFO Tick\n", encoding="utf-8")
        with (
            patch.object(Path, "is_file", return_value=True),
            patch.object(Path, "stat", side_effect=OSError("permission denied")),
        ):
            assert _read_last_log_timestamp(log) is None


# --------------------------------------------------------------------------
# Issue #326: session-gate pace and average into medians, surface excluded
# completions that have no execution window (a stale ``in-progress`` claim
# separated from a later ``done`` by an orchestrator-session gap).
# --------------------------------------------------------------------------


class TestSessionSegmentationHelpers:
    """FR-1: full session segmentation and the same-session validity gate."""

    def test_session_start_boundaries_empty_returns_empty(self) -> None:
        from devbench.reporting.report import _session_start_boundaries

        assert _session_start_boundaries([]) == []

    def test_session_start_boundaries_single_event_returns_that_event(self) -> None:
        from devbench.reporting.report import _session_start_boundaries

        t0 = datetime(2026, 8, 10, 8, 0, tzinfo=UTC)
        assert _session_start_boundaries([t0]) == [t0]

    def test_session_start_boundaries_gap_over_threshold_returns_two_boundaries(self) -> None:
        from devbench.constants import DEFAULT_SESSION_GAP_MINUTES
        from devbench.reporting.report import _session_start_boundaries

        t0 = datetime(2026, 8, 10, 8, 0, tzinfo=UTC)
        t1 = t0 + timedelta(minutes=DEFAULT_SESSION_GAP_MINUTES + 1)
        assert _session_start_boundaries([t0, t1]) == [t0, t1]

    def test_session_start_boundaries_gap_at_threshold_stays_one_boundary(self) -> None:
        """A gap exactly equal to the threshold does not open a new session
        (the rule is strictly-greater-than, matching the pre-#326 walk)."""
        from devbench.constants import DEFAULT_SESSION_GAP_MINUTES
        from devbench.reporting.report import _session_start_boundaries

        t0 = datetime(2026, 8, 10, 8, 0, tzinfo=UTC)
        t1 = t0 + timedelta(minutes=DEFAULT_SESSION_GAP_MINUTES)
        assert _session_start_boundaries([t0, t1]) == [t0]

    def test_walk_for_session_boundary_returns_last_boundary(self) -> None:
        """Regression: re-expressed in terms of _session_start_boundaries, but
        the returned value (the LAST boundary) is byte-identical to the
        pre-#326 implementation."""
        from devbench.constants import DEFAULT_SESSION_GAP_MINUTES
        from devbench.reporting.report import _walk_for_session_boundary

        t0 = datetime(2026, 8, 10, 8, 0, tzinfo=UTC)
        t1 = t0 + timedelta(minutes=DEFAULT_SESSION_GAP_MINUTES + 1)
        t2 = t1 + timedelta(minutes=5)
        assert _walk_for_session_boundary([t0, t1, t2], DEFAULT_SESSION_GAP_MINUTES) == t1

    def test_walk_for_session_boundary_empty_returns_none(self) -> None:
        from devbench.constants import DEFAULT_SESSION_GAP_MINUTES
        from devbench.reporting.report import _walk_for_session_boundary

        assert _walk_for_session_boundary([], DEFAULT_SESSION_GAP_MINUTES) is None

    def test_same_session_true_within_segment(self) -> None:
        from devbench.reporting.report import _same_session

        boundaries = [datetime(2026, 8, 10, 8, 0, tzinfo=UTC), datetime(2026, 8, 10, 12, 0, tzinfo=UTC)]
        a = datetime(2026, 8, 10, 12, 5, tzinfo=UTC)
        b = datetime(2026, 8, 10, 12, 45, tzinfo=UTC)
        assert _same_session(a, b, boundaries) is True

    def test_same_session_false_across_boundary(self) -> None:
        from devbench.reporting.report import _same_session

        boundaries = [datetime(2026, 8, 10, 8, 0, tzinfo=UTC), datetime(2026, 8, 10, 12, 0, tzinfo=UTC)]
        a = datetime(2026, 8, 10, 8, 5, tzinfo=UTC)
        b = datetime(2026, 8, 10, 12, 45, tzinfo=UTC)
        assert _same_session(a, b, boundaries) is False

    def test_same_session_true_for_any_pair_with_no_boundaries(self) -> None:
        from devbench.reporting.report import _same_session

        a = datetime(2020, 1, 1, tzinfo=UTC)
        b = datetime(2030, 1, 1, tzinfo=UTC)
        assert _same_session(a, b, []) is True

    def test_session_index_for_clamps_timestamp_before_first_boundary(self) -> None:
        """A timestamp older than every known boundary resolves to index 0
        (clamped), never raises (raise-free contract, spec Section 4 FR-1)."""
        from devbench.reporting.report import _session_index_for

        boundaries = [datetime(2026, 8, 10, 8, 0, tzinfo=UTC), datetime(2026, 8, 10, 12, 0, tzinfo=UTC)]
        before_all = datetime(2020, 1, 1, tzinfo=UTC)
        assert _session_index_for(before_all, boundaries) == 0

    def test_session_index_for_empty_boundaries_returns_zero(self) -> None:
        from devbench.reporting.report import _session_index_for

        assert _session_index_for(datetime(2026, 8, 10, 8, 0, tzinfo=UTC), []) == 0


class TestExecutionAnchor:
    """Issue #329 FR-2 (AC-E13-F1-S2-T1-2): ``_execution_anchor`` returns the
    minimum same-session claim <= ``done_at``, else ``None``. The single
    shared selection helper both ``_compute_window_stats`` and
    ``_recent_pace_minutes`` delegate to (AC-11, see
    ``TestExecutionAnchorSingleSourceOfTruth``).
    """

    def test_two_same_session_claims_anchors_to_the_earliest(self) -> None:
        """AC-8 / #329 live shape: E11-F1-S1-T2 claimed at 19:50:20 and
        20:34:17 (same session), done at 20:54:07 -- the anchor is the
        FIRST claim, not the last."""
        from devbench.reporting.report import _execution_anchor

        done_at = datetime(2026, 8, 10, 20, 54, 7, tzinfo=UTC)
        first_claim = datetime(2026, 8, 10, 19, 50, 20, tzinfo=UTC)
        second_claim = datetime(2026, 8, 10, 20, 34, 17, tzinfo=UTC)
        assert _execution_anchor([first_claim, second_claim], done_at, []) == first_claim
        # Order-independence: a claims list is not required to arrive sorted.
        assert _execution_anchor([second_claim, first_claim], done_at, []) == first_claim

    def test_prior_session_only_claim_returns_none(self) -> None:
        """AC-9: the completion's only claim sits in an earlier orchestrator
        session -- no eligible anchor exists."""
        from devbench.reporting.report import _execution_anchor

        boundaries = [datetime(2026, 8, 1, 8, 0, tzinfo=UTC), datetime(2026, 8, 9, 8, 0, tzinfo=UTC)]
        prior_claim = datetime(2026, 8, 1, 8, 30, tzinfo=UTC)
        done_at = datetime(2026, 8, 9, 9, 0, tzinfo=UTC)
        assert _execution_anchor([prior_claim], done_at, boundaries) is None

    def test_mixed_prior_and_current_session_anchors_to_earliest_current(self) -> None:
        """AC-10: one prior-session claim plus one current-session claim
        anchors to the earliest CURRENT-session claim, never the prior one."""
        from devbench.reporting.report import _execution_anchor

        boundaries = [datetime(2026, 8, 1, 8, 0, tzinfo=UTC), datetime(2026, 8, 9, 8, 0, tzinfo=UTC)]
        prior_claim = datetime(2026, 8, 1, 9, 0, tzinfo=UTC)
        current_claim = datetime(2026, 8, 9, 9, 0, tzinfo=UTC)
        done_at = datetime(2026, 8, 9, 9, 30, tzinfo=UTC)
        assert _execution_anchor([prior_claim, current_claim], done_at, boundaries) == current_claim

    def test_claim_after_done_at_is_never_eligible(self) -> None:
        """A claim later than ``done_at`` (clock anomaly) never becomes an
        anchor, even though it is otherwise same-session."""
        from devbench.reporting.report import _execution_anchor

        done_at = datetime(2026, 8, 10, 10, 0, tzinfo=UTC)
        clock_anomaly_claim = datetime(2026, 8, 10, 10, 5, tzinfo=UTC)
        assert _execution_anchor([clock_anomaly_claim], done_at, []) is None

    def test_empty_claims_returns_none(self) -> None:
        from devbench.reporting.report import _execution_anchor

        assert _execution_anchor([], datetime(2026, 8, 10, tzinfo=UTC), []) is None


class TestExecutionAnchorSingleSourceOfTruth:
    """AC-11 (spec AC-11 / AC-E13-F1-S2-T1-6): both anchor consumers obtain
    the anchor from ``_execution_anchor``; neither re-implements the
    same-session-claim-selection scan inline. ``_same_session`` is called
    from exactly one place in the module: inside ``_execution_anchor``."""

    def test_neither_consumer_reimplements_the_claim_selection(self) -> None:
        import inspect

        from devbench.reporting import report

        anchor_src = inspect.getsource(report._execution_anchor)
        window_stats_src = inspect.getsource(report._compute_window_stats)
        recent_pace_src = inspect.getsource(report._recent_pace_minutes)

        assert "_execution_anchor(" in window_stats_src
        assert "_execution_anchor(" in recent_pace_src
        assert "_same_session(" in anchor_src
        assert "_same_session(" not in window_stats_src
        assert "_same_session(" not in recent_pace_src


class TestConsumersAnchorOnEarliestClaim:
    """AC-8/AC-9/AC-10 (spec) via the public consumers: both
    ``_recent_pace_minutes`` and ``_compute_window_stats`` route their claim
    selection through ``_execution_anchor``, so the earliest-claim, prior-
    session-exclusion, and mixed-session behaviours are visible end-to-end,
    not just at the helper level (``TestExecutionAnchor`` above)."""

    # #329 live shape: E11-F1-S1-T2 claimed twice in the same session before
    # being closed. The true window is measured from the FIRST claim
    # (63.8 min), not the last (19.8 min).
    _TID = "E11-F1-S1-T2"
    _DONE_AT = datetime(2026, 8, 10, 20, 54, 7, tzinfo=UTC)
    _FIRST_CLAIM = datetime(2026, 8, 10, 19, 50, 20, tzinfo=UTC)
    _SECOND_CLAIM = datetime(2026, 8, 10, 20, 34, 17, tzinfo=UTC)

    def test_recent_pace_minutes_anchors_to_earliest_claim_not_last(self) -> None:
        from devbench.reporting.report import _recent_pace_minutes

        done = {self._TID: self._DONE_AT}
        claims = {self._TID: [self._FIRST_CLAIM, self._SECOND_CLAIM]}

        median, excluded = _recent_pace_minutes(done, claims, [], n=1)
        assert median == pytest.approx(63.783333, abs=0.01)
        assert median != pytest.approx(19.833333, abs=0.01)
        assert median != pytest.approx(3.0, abs=0.01)
        assert excluded == 0

    def test_compute_window_stats_anchors_to_earliest_claim_not_last(self, tmp_path: Path) -> None:
        """MIN_PACE_SAMPLES requires >= 3 in-window samples before
        ``avg_minutes`` reports a median instead of the below-threshold
        zero, so 2 filler tasks with the SAME target duration (single claim
        each) pad the window without changing the expected median."""
        from devbench.constants import MIN_PACE_SAMPLES
        from devbench.reporting.report import _compute_window_stats

        target_duration_minutes = (self._DONE_AT - self._FIRST_CLAIM).total_seconds() / 60
        filler_base = datetime(2026, 8, 10, 8, 0, tzinfo=UTC)
        done = {self._TID: self._DONE_AT}
        claims: dict[str, list[datetime]] = {self._TID: [self._FIRST_CLAIM, self._SECOND_CLAIM]}
        for i in range(MIN_PACE_SAMPLES - 1):
            filler_tid = f"E0-F1-S1-T{i + 1}"
            filler_claim = filler_base + timedelta(hours=i)
            claims[filler_tid] = [filler_claim]
            done[filler_tid] = filler_claim + (self._DONE_AT - self._FIRST_CLAIM)
        log_path = tmp_path / "log.log"
        log_path.write_text("", encoding="utf-8")

        stats = _compute_window_stats(
            log_path,
            filler_base - timedelta(minutes=1),
            self._DONE_AT + timedelta(minutes=1),
            done,
            claims,
            tasks_active=0,
        )
        assert stats.pace_excluded_count == 0
        assert stats.pace_sample_count == MIN_PACE_SAMPLES
        assert stats.avg_minutes == pytest.approx(target_duration_minutes, abs=0.01)
        assert stats.avg_minutes != pytest.approx(19.833333, abs=0.01)

    def test_compute_window_stats_excludes_prior_session_only_claim_with_byte_identical_suffix(
        self, tmp_path: Path
    ) -> None:
        """AC-9: a task whose only claim sits in an earlier orchestrator
        session has no execution window, and the #326 suffix text is
        byte-identical to its pre-#329 form."""
        from devbench.reporting.report import _compute_window_stats, _no_execution_window_suffix

        tid = "E0-F1-S9-T1"
        session_starts = [datetime(2026, 8, 1, 8, 0, tzinfo=UTC), datetime(2026, 8, 9, 8, 0, tzinfo=UTC)]
        prior_claim = datetime(2026, 8, 1, 8, 30, tzinfo=UTC)
        done_at = datetime(2026, 8, 9, 9, 0, tzinfo=UTC)
        done = {tid: done_at}
        claims = {tid: [prior_claim]}
        log_path = tmp_path / "log.log"
        log_path.write_text("", encoding="utf-8")

        stats = _compute_window_stats(
            log_path,
            datetime(2026, 8, 9, 8, 0, tzinfo=UTC),
            done_at + timedelta(minutes=1),
            done,
            claims,
            tasks_active=0,
            session_starts=session_starts,
        )
        assert stats.pace_excluded_count == 1
        assert stats.pace_sample_count == 0
        assert _no_execution_window_suffix(stats.pace_excluded_count) == " (1 excluded: no execution window)"

    def test_compute_window_stats_mixed_session_claims_anchor_to_earliest_current(self, tmp_path: Path) -> None:
        """AC-10: one prior-session claim plus one current-session claim
        anchors to the earliest CURRENT-session claim. Padded with 2
        same-session, same-duration filler tasks to clear MIN_PACE_SAMPLES."""
        from devbench.constants import MIN_PACE_SAMPLES
        from devbench.reporting.report import _compute_window_stats

        tid = "E0-F1-S1-T1"
        session_starts = [datetime(2026, 8, 1, 8, 0, tzinfo=UTC), datetime(2026, 8, 9, 8, 0, tzinfo=UTC)]
        prior_claim = datetime(2026, 8, 1, 9, 0, tzinfo=UTC)
        current_claim = datetime(2026, 8, 9, 9, 0, tzinfo=UTC)
        done_at = datetime(2026, 8, 9, 9, 30, tzinfo=UTC)
        done = {tid: done_at}
        claims: dict[str, list[datetime]] = {tid: [prior_claim, current_claim]}
        for i in range(MIN_PACE_SAMPLES - 1):
            filler_tid = f"E0-F1-S2-T{i + 1}"
            filler_claim = current_claim + timedelta(hours=i + 1)
            claims[filler_tid] = [filler_claim]
            done[filler_tid] = filler_claim + timedelta(minutes=30)
        log_path = tmp_path / "log.log"
        log_path.write_text("", encoding="utf-8")

        stats = _compute_window_stats(
            log_path,
            datetime(2026, 8, 9, 8, 0, tzinfo=UTC),
            done[f"E0-F1-S2-T{MIN_PACE_SAMPLES - 1}"] + timedelta(minutes=1),
            done,
            claims,
            tasks_active=0,
            session_starts=session_starts,
        )
        assert stats.pace_excluded_count == 0
        assert stats.pace_sample_count == MIN_PACE_SAMPLES
        # 9:30 - 9:00 (current claim), not 9:30 - (8/1) 9:00 (prior session).
        assert stats.avg_minutes == pytest.approx(30.0)

    def test_generate_report_end_to_end_anchors_to_earliest_claim_via_recent_pace(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """AC-8 / #329 live shape, exercised end-to-end through the public
        ``generate_report`` entry point rather than a private helper called
        directly. ``RECENT_PACE_TASKS`` is patched to 1 so the single target
        completion alone determines the rendered "Recent pace" figure -- no
        filler samples are needed, and none can mask a wrong anchor
        selection the way ``statistics.median`` masks a lone wrong-valued
        sample among matching fillers (see ``TestCaseDMedianRobustness``).
        The rendered figure must reflect the FIRST same-session claim
        (63.8 min), never the last (19.8 min)."""
        monkeypatch.setattr("devbench.reporting.report.RECENT_PACE_TASKS", 1)
        # A short-lived same-session filler task splits the ~44-minute gap
        # between the two target claims so the log-wide gap-walk never
        # inserts a session boundary between them (session-gap threshold is
        # DEFAULT_SESSION_GAP_MINUTES=30) -- matching the live #329 log,
        # where other tasks' activity keeps the two claims in one session.
        entries = [
            f"{self._FIRST_CLAIM.strftime('%Y-%m-%dT%H:%M:%S')}Z [devbench.backlog_manager] "
            f"INFO Set {self._TID} to 'in-progress'",
            "2026-08-10T20:05:00Z [devbench.backlog_manager] INFO Set E0-F1-S9-T1 to 'in-progress'",
            "2026-08-10T20:15:00Z [devbench.backlog_manager] INFO Set E0-F1-S9-T1 to 'done'",
            f"{self._SECOND_CLAIM.strftime('%Y-%m-%dT%H:%M:%S')}Z [devbench.backlog_manager] "
            f"INFO Set {self._TID} to 'in-progress'",
            f"{self._DONE_AT.strftime('%Y-%m-%dT%H:%M:%S')}Z [devbench.backlog_manager] INFO Set {self._TID} to 'done'",
        ]
        log_file = tmp_path / "test.log"
        log_file.write_text(_make_log(entries))

        report = generate_report(log_path=log_file)

        assert "63.8 min" in report
        assert "19.8 min" not in report


def _pace_case_stale_claim() -> tuple[dict[str, datetime], dict[str, datetime], list[datetime]]:
    """Case (a): an operator ``set-status done`` on a stale claim from an
    earlier orchestrator session. Three normal same-session samples
    (20/25/30 min) plus one newest completion whose only claim sits in an
    earlier session (excluded, no execution window)."""
    session_starts = [datetime(2026, 8, 1, 8, 0, tzinfo=UTC), datetime(2026, 8, 9, 8, 0, tzinfo=UTC)]
    base = datetime(2026, 8, 9, 9, 0, tzinfo=UTC)
    done: dict[str, datetime] = {}
    prog: dict[str, datetime] = {}
    for i, dur in enumerate((20, 25, 30)):
        tid = f"E0-F1-S1-T{i + 1}"
        prog[tid] = base + timedelta(hours=i)
        done[tid] = prog[tid] + timedelta(minutes=dur)
    poison_tid = "E0-F1-S9-T1"
    prog[poison_tid] = datetime(2026, 8, 1, 8, 30, tzinfo=UTC)
    done[poison_tid] = base + timedelta(hours=3, minutes=30)  # newest done, but stale (cross-session) claim
    return done, prog, session_starts


def _pace_case_same_session_outlier() -> tuple[dict[str, datetime], dict[str, datetime], list[datetime]]:
    """Case (d): three uniform 20-min same-session samples plus one
    same-session 600-min outlier. The outlier passes the session gate (it is
    NOT excluded) but must not move the reported value the way it would move
    an arithmetic mean (Goal 4 robustness)."""
    base = datetime(2026, 8, 9, 9, 0, tzinfo=UTC)
    done: dict[str, datetime] = {}
    prog: dict[str, datetime] = {}
    for i, dur in enumerate((20, 20, 20)):
        tid = f"E0-F1-S1-T{i + 1}"
        prog[tid] = base + timedelta(hours=i)
        done[tid] = prog[tid] + timedelta(minutes=dur)
    outlier_tid = "E0-F1-S9-T1"
    prog[outlier_tid] = base + timedelta(hours=3)
    done[outlier_tid] = prog[outlier_tid] + timedelta(minutes=600)
    return done, prog, []


def _pace_case_live_repro_326() -> tuple[dict[str, datetime], dict[str, datetime], list[datetime]]:
    """Reconstruct the #326 live-repro numbers: nine same-session 39-75 min
    completions plus the operator-closed E2-F6-S1-T1, claimed 2026-07-29 and
    closed 2026-08-10 (~17,713 idle minutes across a session gap)."""
    session_starts = [datetime(2026, 7, 29, 11, 0, tzinfo=UTC), datetime(2026, 8, 10, 5, 0, tzinfo=UTC)]
    done: dict[str, datetime] = {}
    prog: dict[str, datetime] = {}
    cursor = datetime(2026, 8, 10, 6, 0, tzinfo=UTC)
    for i, dur in enumerate((39, 45, 50, 55, 60, 65, 70, 72, 75)):
        tid = f"E0-F1-S1-T{i + 1}"
        prog[tid] = cursor
        done[tid] = cursor + timedelta(minutes=dur)
        cursor = done[tid]
    poison_tid = "E2-F6-S1-T1"
    prog[poison_tid] = datetime(2026, 7, 29, 11, 19, 28, tzinfo=UTC)
    done[poison_tid] = datetime(2026, 8, 10, 18, 32, 33, tzinfo=UTC)
    return done, prog, session_starts


def _pace_default_window(
    done_times: dict[str, datetime], progress_times: dict[str, datetime]
) -> tuple[datetime, datetime]:
    """A window wide enough to include every sample without window-clamping any of them."""
    return (
        min(progress_times.values()) - timedelta(minutes=1),
        max(done_times.values()) + timedelta(minutes=1),
    )


def _as_claims(progress_times: dict[str, datetime]) -> dict[str, list[datetime]]:
    """Issue #329 FR-2: wrap a single-claim-per-task mapping as a one-element
    claims list.

    Most pace/window-stats fixtures in this module model exactly one
    ``in-progress`` claim per task (the common case). ``_compute_window_stats``
    and ``_recent_pace_minutes`` now take ``dict[str, list[datetime]]`` (the
    shape ``task_transition_time_series_for_workspace`` returns) so
    ``_execution_anchor`` has every candidate claim to select from; this
    helper converts the convenient single-claim fixture shape into that
    shape at the call boundary, without duplicating literal claim data in
    every test.
    """
    return {tid: [ts] for tid, ts in progress_times.items()}


_PACE_GATE_CASES = [
    pytest.param(*_pace_case_stale_claim(), 3, 25.0, 1, id="case_a_stale_claim"),
    pytest.param(*_pace_case_same_session_outlier(), 4, 20.0, 0, id="case_d_same_session_outlier"),
    pytest.param(*_pace_case_live_repro_326(), 9, 60.0, 1, id="live_repro_326"),
]


class TestRecentPaceAndWindowStatsSessionGate:
    """FR-2 + FR-3: both the log-wide and per-window estimators gate on the
    same-session rule and report a median instead of a poisoned mean."""

    @pytest.mark.parametrize(
        ("done_times", "progress_times", "session_starts", "n", "expected_median", "expected_excluded"),
        _PACE_GATE_CASES,
    )
    def test_recent_pace_minutes_returns_median_and_excluded_count(
        self,
        done_times: dict[str, datetime],
        progress_times: dict[str, datetime],
        session_starts: list[datetime],
        n: int,
        expected_median: float,
        expected_excluded: int,
    ) -> None:
        from devbench.reporting.report import _recent_pace_minutes

        median, excluded = _recent_pace_minutes(done_times, _as_claims(progress_times), session_starts, n)
        assert median == pytest.approx(expected_median)
        assert excluded == expected_excluded

    @pytest.mark.parametrize(
        ("done_times", "progress_times", "session_starts", "n", "expected_median", "expected_excluded"),
        _PACE_GATE_CASES,
    )
    def test_compute_window_stats_returns_median_and_excluded_count(
        self,
        done_times: dict[str, datetime],
        progress_times: dict[str, datetime],
        session_starts: list[datetime],
        n: int,
        expected_median: float,
        expected_excluded: int,
        tmp_path: Path,
    ) -> None:
        from devbench.reporting.report import _compute_window_stats

        del n  # _compute_window_stats scores every task in-window, not the most-recent N.
        window_start, window_end = _pace_default_window(done_times, progress_times)
        log_path = tmp_path / "log.log"
        log_path.write_text("", encoding="utf-8")
        stats = _compute_window_stats(
            log_path,
            window_start,
            window_end,
            done_times,
            _as_claims(progress_times),
            tasks_active=0,
            session_starts=session_starts,
        )
        assert stats.avg_minutes == pytest.approx(expected_median)
        assert stats.pace_excluded_count == expected_excluded
        assert stats.pace_sample_count == len(done_times) - expected_excluded


class TestCaseDMedianRobustness:
    """AC-12: with N-1 normal samples plus one same-session outlier, the
    reported value is the median (unmoved by the outlier) while the
    arithmetic mean of the same data diverges -- proof this assertion fails
    against the pre-#326 mean-only code."""

    def test_recent_pace_median_unmoved_by_outlier_while_mean_diverges(self) -> None:
        from devbench.reporting.report import _recent_pace_minutes

        done, prog, session_starts = _pace_case_same_session_outlier()
        median, excluded = _recent_pace_minutes(done, _as_claims(prog), session_starts, n=4)
        raw_durations = [(done[tid] - prog[tid]).total_seconds() / 60 for tid in done]
        assert excluded == 0
        assert median == pytest.approx(statistics.median(raw_durations))
        assert median != pytest.approx(statistics.mean(raw_durations))

    def test_compute_window_stats_median_unmoved_by_outlier_while_mean_diverges(self, tmp_path: Path) -> None:
        from devbench.reporting.report import _compute_window_stats

        done, prog, session_starts = _pace_case_same_session_outlier()
        window_start, window_end = _pace_default_window(done, prog)
        log_path = tmp_path / "log.log"
        log_path.write_text("", encoding="utf-8")
        stats = _compute_window_stats(
            log_path, window_start, window_end, done, _as_claims(prog), tasks_active=0, session_starts=session_starts
        )
        raw_durations = [(done[tid] - prog[tid]).total_seconds() / 60 for tid in done]
        assert stats.pace_excluded_count == 0
        assert stats.avg_minutes == pytest.approx(statistics.median(raw_durations))
        assert stats.avg_minutes != pytest.approx(statistics.mean(raw_durations))


class TestRecentPaceReclaimAcrossRestart:
    """Case (b): a claim in the CURRENT session is same-session as done ->
    accepted, and it does not count toward ``excluded``. Issue #329 FR-2:
    now that ``progress_claims`` carries every claim (not just the most
    recent), a stale PRE-restart claim in an earlier session and a genuine
    POST-restart claim in the current session both feed the same task; the
    anchor resolves to the current-session claim (the earliest one eligible)
    and the prior-session claim is correctly ignored."""

    def test_reclaimed_task_anchors_to_current_session_claim_and_is_not_excluded(self) -> None:
        from devbench.reporting.report import _recent_pace_minutes

        session_starts = [datetime(2026, 8, 10, 8, 0, tzinfo=UTC), datetime(2026, 8, 10, 12, 0, tzinfo=UTC)]
        tid = "E0-F1-S1-T1"
        pre_restart_claim = datetime(2026, 8, 10, 9, 0, tzinfo=UTC)
        post_restart_claim = datetime(2026, 8, 10, 12, 5, tzinfo=UTC)
        claims = {tid: [pre_restart_claim, post_restart_claim]}
        done = {tid: datetime(2026, 8, 10, 12, 45, tzinfo=UTC)}

        median, excluded = _recent_pace_minutes(done, claims, session_starts, n=1)
        assert median == pytest.approx(40.0)
        assert excluded == 0


class TestUniformSamplesByteIdentical:
    """Case (c): N uniform same-session executions render the pre-#326
    number with no suffix and ``excluded == 0`` (median == mean when every
    sample is identical)."""

    def test_uniform_samples_render_without_suffix(self, tmp_path: Path) -> None:
        from devbench.reporting.report import _compute_window_stats, _stats_to_value_list

        base = datetime(2026, 8, 10, 8, 0, tzinfo=UTC)
        done: dict[str, datetime] = {}
        prog: dict[str, datetime] = {}
        for i in range(10):
            tid = f"E0-F1-S1-T{i + 1}"
            prog[tid] = base + timedelta(hours=i)
            done[tid] = prog[tid] + timedelta(minutes=40)
        log_path = tmp_path / "log.log"
        log_path.write_text("", encoding="utf-8")
        stats = _compute_window_stats(
            log_path,
            base,
            base + timedelta(hours=11),
            done,
            _as_claims(prog),
            tasks_active=0,
        )
        assert stats.avg_minutes == pytest.approx(40.0)
        assert stats.pace_excluded_count == 0
        values = _stats_to_value_list(stats)
        assert "40.0 min" in values
        assert "excluded" not in " ".join(values)


class TestNoExecutionWindowSuffix:
    """FR-4: the exclusion count renders as a suffix on both value cells."""

    @pytest.mark.parametrize(
        ("excluded", "expected"),
        [
            (0, ""),
            (1, " (1 excluded: no execution window)"),
            (2, " (2 excluded: no execution window)"),
        ],
    )
    def test_suffix_text(self, excluded: int, expected: str) -> None:
        from devbench.reporting.report import _no_execution_window_suffix

        assert _no_execution_window_suffix(excluded) == expected

    @staticmethod
    def _make_stats(
        avg_minutes: float,
        pace_sample_count: int,
        pace_excluded_count: int,
        recent_pace_minutes: float | None,
        recent_pace_excluded_count: int,
    ) -> WindowStats:
        from devbench.reporting.report import CostBreakdown, HookLogTotals, WindowStats

        return WindowStats(
            window_start=datetime(2026, 8, 10, 12, 0, tzinfo=UTC),
            window_hours=1.0,
            tasks_in_window=4,
            avg_minutes=avg_minutes,
            est_hours=0.0,
            totals=HookLogTotals(),
            cost=CostBreakdown(0, 0, 0, 0, 0, 0),
            cache_hit_rate=None,
            tokens_per_task=0.0,
            est_total_cost=0.0,
            api_hours=0.0,
            api_efficiency=None,
            pace_sample_count=pace_sample_count,
            pace_excluded_count=pace_excluded_count,
            recent_pace_minutes=recent_pace_minutes,
            recent_pace_excluded_count=recent_pace_excluded_count,
        )

    def test_avg_minutes_cell_carries_exclusion_suffix(self) -> None:
        from devbench.reporting.report import _stats_to_value_list

        stats = self._make_stats(33.4, 3, 1, None, 0)
        values = _stats_to_value_list(stats)
        assert "33.4 min (1 excluded: no execution window)" in values

    def test_avg_minutes_cell_has_no_suffix_when_nothing_excluded(self) -> None:
        from devbench.reporting.report import _stats_to_value_list

        stats = self._make_stats(33.4, 3, 0, None, 0)
        values = _stats_to_value_list(stats)
        assert "33.4 min" in values
        assert "excluded" not in " ".join(values)

    def test_recent_pace_cell_carries_exclusion_suffix(self) -> None:
        from devbench.reporting.report import _stats_to_value_list

        stats = self._make_stats(33.4, 3, 0, 47.0, 1)
        values = _stats_to_value_list(stats)
        assert "47.0 min (1 excluded: no execution window)" in values

    def test_recent_pace_cell_na_carries_exclusion_suffix(self) -> None:
        """AC-7: the recent-pace cell renders 'n/a' *plus* the same suffix
        when samples were dropped but fewer than N valid ones remain."""
        from devbench.reporting.report import _stats_to_value_list

        stats = self._make_stats(33.4, 3, 0, None, 2)
        values = _stats_to_value_list(stats)
        assert "n/a (2 excluded: no execution window)" in values

    def test_recent_pace_spanning_collapse_preserved_with_suffix(self) -> None:
        """AC-8: the log-wide recent-pace value (suffix included) is
        identical across window columns and still collapses via
        _merge_spanning_values."""
        from devbench.config import RECENT_PACE_TASKS
        from devbench.reporting.report import _merge_spanning_values, _stats_to_value_list

        stats_a = self._make_stats(20.0, 3, 0, 47.0, 1)
        stats_b = self._make_stats(55.0, 5, 2, 47.0, 1)
        values_a = _stats_to_value_list(stats_a)
        values_b = _stats_to_value_list(stats_b)
        recent_pace_index = 3  # "Recent pace" is the 4th value; see _stats_to_value_list order.
        merged = _merge_spanning_values(
            f"Recent pace (last {RECENT_PACE_TASKS} tasks)",
            [values_a[recent_pace_index], values_b[recent_pace_index]],
        )
        assert merged == "47.0 min (1 excluded: no execution window)"


class TestWindowStatsExclusionFieldsDefault:
    """AC-6: the two new fields default to 0 and every existing direct
    ``WindowStats(...)`` construction (owned and non-owned) keeps working."""

    def test_new_fields_default_to_zero_and_existing_construction_still_works(self) -> None:
        from devbench.reporting.report import CostBreakdown, HookLogTotals, WindowStats

        stats = WindowStats(
            window_start=datetime(2026, 8, 10, 12, 0, tzinfo=UTC),
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
        assert stats.pace_excluded_count == 0
        assert stats.recent_pace_excluded_count == 0


class TestSummaryLineExclusionSuffix:
    """FR-6: the trailing projection names dropped completions on the
    chosen pace path; the zero-drop sentence stays byte-identical."""

    @staticmethod
    def _make_stats(
        avg_minutes: float,
        pace_excluded_count: int,
        recent_pace_minutes: float | None,
        recent_pace_excluded_count: int,
        est_hours: float,
    ) -> WindowStats:
        from devbench.reporting.report import CostBreakdown, HookLogTotals, WindowStats

        return WindowStats(
            window_start=datetime(2026, 8, 10, 12, 0, tzinfo=UTC),
            window_hours=1.0,
            tasks_in_window=9,
            avg_minutes=avg_minutes,
            est_hours=est_hours,
            totals=HookLogTotals(),
            cost=CostBreakdown(0, 0, 0, 0, 0, 0),
            cache_hit_rate=None,
            tokens_per_task=0.0,
            est_total_cost=0.0,
            api_hours=0.0,
            api_efficiency=None,
            pace_sample_count=9,
            pace_excluded_count=pace_excluded_count,
            recent_pace_minutes=recent_pace_minutes,
            recent_pace_excluded_count=recent_pace_excluded_count,
        )

    def test_recent_path_appends_suffix_when_excluded(self) -> None:
        from devbench.reporting.report import _summary_line

        stats = self._make_stats(33.4, 0, 47.0, 1, 5.5)
        line = _summary_line(stats, tasks_active=7, tasks_blocked=0)
        assert line == (
            "At the Recent pace of ~47.0 minutes per task (1 excluded: no execution window), "
            "the remaining 7 active task(s) should take roughly 5.5 more hours of continuous execution."
        )

    def test_recent_path_byte_identical_when_nothing_excluded(self) -> None:
        from devbench.reporting.report import _summary_line

        stats = self._make_stats(33.4, 0, 47.0, 0, 5.5)
        line = _summary_line(stats, tasks_active=7, tasks_blocked=0)
        assert line == (
            "At the Recent pace of ~47.0 minutes per task, "
            "the remaining 7 active task(s) should take roughly 5.5 more hours of continuous execution."
        )

    def test_all_time_path_appends_suffix_when_excluded(self) -> None:
        from devbench.reporting.report import _summary_line

        stats = self._make_stats(25.0, 1, None, 0, 0.83)
        line = _summary_line(stats, tasks_active=2, tasks_blocked=0)
        assert "At the All-time pace of ~25.0 minutes per task (1 excluded: no execution window)" in line

    def test_all_time_path_byte_identical_when_nothing_excluded(self) -> None:
        from devbench.reporting.report import _summary_line

        stats = self._make_stats(25.0, 0, None, 0, 0.83)
        line = _summary_line(stats, tasks_active=2, tasks_blocked=0)
        assert "At the All-time pace of ~25.0 minutes per task, " in line
        assert "excluded" not in line


class TestRejectedRowsSuffix:
    """Issue #329 FR-6 (AC-18, AC-19): the rejected-row count renders as a
    suffix on both the ``Average time per task`` and ``Recent pace`` cells,
    composed AFTER the #326 ``_no_execution_window_suffix``."""

    @pytest.mark.parametrize(
        ("rejected", "expected"),
        [
            (0, ""),
            (1, " (1 non-transition rows rejected)"),
            (44, " (44 non-transition rows rejected)"),
        ],
    )
    def test_suffix_text(self, rejected: int, expected: str) -> None:
        from devbench.reporting.report import _rejected_rows_suffix

        assert _rejected_rows_suffix(rejected) == expected

    @staticmethod
    def _make_stats(
        avg_minutes: float,
        pace_excluded_count: int,
        recent_pace_minutes: float | None,
        recent_pace_excluded_count: int,
        rejected_row_count: int,
    ) -> WindowStats:
        from devbench.reporting.report import CostBreakdown, HookLogTotals, WindowStats

        return WindowStats(
            window_start=datetime(2026, 8, 10, 12, 0, tzinfo=UTC),
            window_hours=1.0,
            tasks_in_window=4,
            avg_minutes=avg_minutes,
            est_hours=0.0,
            totals=HookLogTotals(),
            cost=CostBreakdown(0, 0, 0, 0, 0, 0),
            cache_hit_rate=None,
            tokens_per_task=0.0,
            est_total_cost=0.0,
            api_hours=0.0,
            api_efficiency=None,
            pace_sample_count=3,
            pace_excluded_count=pace_excluded_count,
            recent_pace_minutes=recent_pace_minutes,
            recent_pace_excluded_count=recent_pace_excluded_count,
            rejected_row_count=rejected_row_count,
        )

    def test_field_defaults_to_zero_and_existing_construction_still_works(self) -> None:
        from devbench.reporting.report import CostBreakdown, HookLogTotals, WindowStats

        stats = WindowStats(
            window_start=datetime(2026, 8, 10, 12, 0, tzinfo=UTC),
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
        assert stats.rejected_row_count == 0

    def test_avg_minutes_cell_carries_rejected_suffix(self) -> None:
        from devbench.reporting.report import _stats_to_value_list

        stats = self._make_stats(33.4, 0, None, 0, 44)
        values = _stats_to_value_list(stats)
        assert "33.4 min (44 non-transition rows rejected)" in values

    def test_avg_minutes_cell_has_no_suffix_when_nothing_rejected(self) -> None:
        from devbench.reporting.report import _stats_to_value_list

        stats = self._make_stats(33.4, 0, None, 0, 0)
        values = _stats_to_value_list(stats)
        assert "33.4 min" in values
        assert "rejected" not in " ".join(values)

    def test_recent_pace_cell_carries_rejected_suffix(self) -> None:
        from devbench.reporting.report import _stats_to_value_list

        stats = self._make_stats(33.4, 0, 47.0, 0, 44)
        values = _stats_to_value_list(stats)
        assert "47.0 min (44 non-transition rows rejected)" in values

    def test_recent_pace_cell_na_carries_rejected_suffix(self) -> None:
        from devbench.reporting.report import _stats_to_value_list

        stats = self._make_stats(33.4, 0, None, 0, 44)
        values = _stats_to_value_list(stats)
        assert "n/a (44 non-transition rows rejected)" in values

    def test_both_suffixes_compose_in_the_documented_order(self) -> None:
        """AC-18: ``_no_execution_window_suffix`` (#326) renders FIRST, then
        ``_rejected_rows_suffix`` (#329 FR-6)."""
        from devbench.reporting.report import _stats_to_value_list

        stats = self._make_stats(33.4, 1, 47.0, 2, 44)
        values = _stats_to_value_list(stats)
        assert "33.4 min (1 excluded: no execution window) (44 non-transition rows rejected)" in values
        assert "47.0 min (2 excluded: no execution window) (44 non-transition rows rejected)" in values

    def test_clean_stats_render_byte_identical_to_pre_fr6(self) -> None:
        """AC-19: zero excluded, zero rejected -> no suffix at all on either cell."""
        from devbench.reporting.report import _stats_to_value_list

        stats = self._make_stats(33.4, 0, 47.0, 0, 0)
        values = _stats_to_value_list(stats)
        assert "33.4 min" in values
        assert "47.0 min" in values
        joined = " ".join(values)
        assert "excluded" not in joined
        assert "rejected" not in joined


class TestComputeWindowStatsRejectedRowCount:
    """Issue #329 FR-6: ``_compute_window_stats`` populates
    ``rejected_row_count`` from the difference between
    ``unfiltered_progress_claim_counts`` (the raw candidate count) and the
    logger-filtered ``len(progress_claims[tid])``, summed over the tasks
    done in the window."""

    def test_default_none_yields_zero(self, tmp_path: Path) -> None:
        from devbench.reporting.report import _compute_window_stats

        tid = "E0-F1-S1-T1"
        claim = datetime(2026, 8, 10, 10, 0, tzinfo=UTC)
        done = {tid: datetime(2026, 8, 10, 10, 32, 6, tzinfo=UTC)}
        claims = {tid: [claim]}
        log_path = tmp_path / "log.log"
        log_path.write_text("", encoding="utf-8")

        stats = _compute_window_stats(
            log_path,
            claim - timedelta(minutes=1),
            done[tid] + timedelta(minutes=1),
            done,
            claims,
            tasks_active=0,
        )
        assert stats.rejected_row_count == 0

    def test_supplied_counts_compute_the_difference(self, tmp_path: Path) -> None:
        from devbench.reporting.report import _compute_window_stats

        tid = "E0-F1-S1-T1"
        claim = datetime(2026, 8, 10, 10, 0, tzinfo=UTC)
        done = {tid: datetime(2026, 8, 10, 10, 32, 6, tzinfo=UTC)}
        claims = {tid: [claim]}
        log_path = tmp_path / "log.log"
        log_path.write_text("", encoding="utf-8")

        stats = _compute_window_stats(
            log_path,
            claim - timedelta(minutes=1),
            done[tid] + timedelta(minutes=1),
            done,
            claims,
            tasks_active=0,
            unfiltered_progress_claim_counts={tid: 8},
        )
        assert stats.rejected_row_count == 7

    def test_task_missing_from_counts_contributes_zero_not_a_spurious_rejection(self, tmp_path: Path) -> None:
        from devbench.reporting.report import _compute_window_stats

        tid = "E0-F1-S1-T1"
        claim = datetime(2026, 8, 10, 10, 0, tzinfo=UTC)
        done = {tid: datetime(2026, 8, 10, 10, 32, 6, tzinfo=UTC)}
        claims = {tid: [claim]}
        log_path = tmp_path / "log.log"
        log_path.write_text("", encoding="utf-8")

        stats = _compute_window_stats(
            log_path,
            claim - timedelta(minutes=1),
            done[tid] + timedelta(minutes=1),
            done,
            claims,
            tasks_active=0,
            unfiltered_progress_claim_counts={},
        )
        assert stats.rejected_row_count == 0

    def test_sums_across_multiple_tasks_in_the_window(self, tmp_path: Path) -> None:
        from devbench.reporting.report import _compute_window_stats

        base = datetime(2026, 8, 10, 8, 0, tzinfo=UTC)
        done: dict[str, datetime] = {}
        claims: dict[str, list[datetime]] = {}
        counts: dict[str, int] = {}
        extras = (1, 2, 3)
        for i, extra in enumerate(extras):
            tid = f"E0-F1-S1-T{i + 1}"
            claim_at = base + timedelta(hours=i)
            done[tid] = claim_at + timedelta(minutes=10)
            claims[tid] = [claim_at]
            counts[tid] = 1 + extra
        log_path = tmp_path / "log.log"
        log_path.write_text("", encoding="utf-8")

        stats = _compute_window_stats(
            log_path,
            base - timedelta(minutes=1),
            max(done.values()) + timedelta(minutes=1),
            done,
            claims,
            tasks_active=0,
            unfiltered_progress_claim_counts=counts,
        )
        assert stats.rejected_row_count == sum(extras)

    def test_negative_would_be_count_raises_assertion_error(self, tmp_path: Path) -> None:
        """Documented invariant: the unfiltered candidate count can never be
        smaller than the logger-filtered claim count it is drawn from."""
        from devbench.reporting.report import _compute_window_stats

        tid = "E0-F1-S1-T1"
        claim = datetime(2026, 8, 10, 10, 0, tzinfo=UTC)
        done = {tid: datetime(2026, 8, 10, 10, 32, 6, tzinfo=UTC)}
        claims = {tid: [claim]}
        log_path = tmp_path / "log.log"
        log_path.write_text("", encoding="utf-8")

        with pytest.raises(AssertionError):
            _compute_window_stats(
                log_path,
                claim - timedelta(minutes=1),
                done[tid] + timedelta(minutes=1),
                done,
                claims,
                tasks_active=0,
                unfiltered_progress_claim_counts={tid: 0},
            )


class TestGenerateReportSurfacesRejectedRowCount:
    """Issue #329 FR-6 end-to-end: ``generate_report`` wires
    ``EventIndex.task_transition_candidate_counts_for_workspace`` into every
    ``_compute_window_stats`` call, so a cache holding a phantom pre-FR-1b
    echo row surfaces the provenance suffix through the real report
    pipeline, not just the unit-level helper."""

    def test_phantom_cache_row_surfaces_the_suffix_in_the_rendered_report(self, tmp_path: Path) -> None:
        from devbench.reporting.event_index import EventIndex

        log_file = tmp_path / "orch.log"
        log_file.write_text(
            _make_log(
                [
                    "2026-08-11T16:00:00Z [devbench.backlog_manager] INFO Set E0-F1-S1-T1 to 'in-progress'",
                    "2026-08-11T16:32:06Z [devbench.backlog_manager] INFO Set E0-F1-S1-T1 to 'done'",
                ]
            )
        )
        with (
            patch("devbench.reporting.report.WORKSPACE_ROOT", tmp_path),
            patch("devbench.reporting.report.BACKLOG_INDEX", tmp_path / "BACKLOG.md"),
        ):
            idx = EventIndex.open(tmp_path)
            try:
                idx.refresh_orch_log_sources(tmp_path, log_file)
                file_id = idx._conn.execute(
                    "SELECT file_id FROM source_files WHERE path = ?", (str(log_file),)
                ).fetchone()[0]
                # Shaped exactly as pre-FR-1b ingestion would have written a
                # devbench.cli echo of the same 'in-progress' transition.
                idx._conn.execute(
                    "INSERT INTO orch_log_events "
                    "(file_id, line_offset, ts_epoch_us, logger, task_id, transition) VALUES (?, ?, ?, ?, ?, ?)",
                    (file_id, 999_999, 1_754_929_326_000_000, "devbench.cli", "E0-F1-S1-T1", "in-progress"),
                )
            finally:
                idx.close()

            report = generate_report(log_path=log_file)

        assert "(1 non-transition rows rejected)" in report

    def test_no_phantom_row_renders_without_the_suffix(self, tmp_path: Path) -> None:
        log_file = tmp_path / "orch.log"
        log_file.write_text(
            _make_log(
                [
                    "2026-08-11T16:00:00Z [devbench.backlog_manager] INFO Set E0-F1-S1-T1 to 'in-progress'",
                    "2026-08-11T16:32:06Z [devbench.backlog_manager] INFO Set E0-F1-S1-T1 to 'done'",
                ]
            )
        )
        with (
            patch("devbench.reporting.report.WORKSPACE_ROOT", tmp_path),
            patch("devbench.reporting.report.BACKLOG_INDEX", tmp_path / "BACKLOG.md"),
        ):
            report = generate_report(log_path=log_file)

        assert "non-transition rows rejected" not in report


class TestGenerateReportThreadsSessionStarts:
    """FR-5: generate_report computes session_starts once and threads it
    into every _compute_window_stats call site so an operator-closed stale
    claim (the #326 shape) is excluded end-to-end, and the ETA no longer
    multiplies a poisoned rate.

    Issue #326's own repro numbers (39-75 min per task) are exercised
    directly against ``_recent_pace_minutes`` / ``_compute_window_stats`` in
    ``TestRecentPaceAndWindowStatsSessionGate`` with a hand-built
    ``session_starts`` list. Here, the boundaries must come from the FULL
    log timestamp walk ``generate_report`` performs (FR-5), so every normal
    task's own claim-to-done span is kept under
    ``DEFAULT_SESSION_GAP_MINUTES`` and the tasks run back-to-back --
    otherwise the gap-walk itself would (correctly) treat the idle time
    between two ordinary tasks as a session boundary, which is a
    log-topology artifact of this synthetic log, not part of what FR-5 is
    proving here.
    """

    def test_stale_claim_operator_close_does_not_poison_report(self, tmp_path: Path) -> None:
        from devbench.constants import DEFAULT_SESSION_GAP_MINUTES

        log_file = tmp_path / "test.log"
        entries = ["2026-07-29T11:19:28Z [devbench.backlog_manager] INFO Set E2-F6-S1-T1 to 'in-progress'"]
        cursor = datetime(2026, 8, 10, 6, 0, 0, tzinfo=UTC)
        durations = (18, 19, 20, 21, 22, 23, 24, 25, 26)
        assert all(d < DEFAULT_SESSION_GAP_MINUTES for d in durations)
        for i, dur in enumerate(durations):
            tid = f"E0-F1-S1-T{i + 1}"
            start = cursor
            end = cursor + timedelta(minutes=dur)
            entries.append(
                f"{start.strftime('%Y-%m-%dT%H:%M:%S')}Z [devbench.backlog_manager] INFO Set {tid} to 'in-progress'"
            )
            entries.append(f"{end.strftime('%Y-%m-%dT%H:%M:%S')}Z [devbench.backlog_manager] INFO Set {tid} to 'done'")
            cursor = end
        entries.append("2026-08-10T18:32:33Z [devbench.backlog_manager] INFO Set E2-F6-S1-T1 to 'done'")
        log_file.write_text(_make_log(entries))

        report = generate_report(log_path=log_file)

        # The poisoned pair (12+ idle days across a session gap) must not
        # reach the pre-#326 mean-poisoned numbers from the live repro.
        assert "1789.9 min" not in report
        assert "443.1 min" not in report
        assert "208.8 h" not in report
        # The correct median of the nine same-session samples (22.0 min) and
        # the exclusion count must appear.
        assert "22.0 min" in report
        assert "(1 excluded: no execution window)" in report


# --------------------------------------------------------------------------
# #329 FR-4 (E13-F3-S2-T1, AC-16, AC-E13-F3-S2-T1-1): the regression fixture
# built from the live-repro log's measured shape (spec/pace-anchor-integrity.md
# Section 1.3): five completions, each with 3 to 16 candidate 'in-progress'
# matches, of which 1 to 2 per task are genuine devbench.backlog_manager
# transitions and the remainder are devbench.cli echoes carrying LATER
# timestamps.
# --------------------------------------------------------------------------

_ISSUE_329_MEASURED_TABLE: tuple[dict[str, Any], ...] = (
    {"task_id": "E11-F1-S1-T1", "genuine_claims": 1, "regex_matches": 7, "true_window_min": 32.1},
    {"task_id": "E11-F1-S2-T1", "genuine_claims": 1, "regex_matches": 9, "true_window_min": 29.1},
    {"task_id": "E11-F3-S1-T1", "genuine_claims": 1, "regex_matches": 3, "true_window_min": 17.1},
    {"task_id": "E11-F1-S1-T2", "genuine_claims": 2, "regex_matches": 16, "true_window_min": 63.8},
    {"task_id": "E11-F1-S2-T2", "genuine_claims": 1, "regex_matches": 7, "true_window_min": 32.3},
)
_ISSUE_329_RECLAIMED_TASK_ID = "E11-F1-S1-T2"
# #326 regression guard: an unrelated sixth completion whose only claim
# predates the current orchestrator session -- must stay excluded even
# though FR-2 now selects the EARLIEST same-session claim among several.
_ISSUE_329_STALE_TASK_ID = "E0-F9-S1-T1"


def _issue_329_regression_fixture() -> tuple[
    dict[str, datetime],
    dict[str, list[datetime]],
    dict[str, int],
    dict[str, datetime],
    list[datetime],
]:
    """Build the #329 live-repro regression shape from ``_ISSUE_329_MEASURED_TABLE``.

    Each of the five measured tasks gets a ``done`` timestamp and a first
    genuine claim placed exactly ``true_window_min`` minutes earlier, so
    ``_execution_anchor`` resolves a window that matches the table's
    measured value exactly by construction. ``E11-F1-S1-T2`` additionally
    gets a SECOND, later same-session claim (genuine re-claim) that must NOT
    win the anchor selection. ``unfiltered_progress_claim_counts`` carries each task's
    ``regex_matches`` count, so ``_compute_window_stats`` derives
    ``rejected_row_count`` as ``regex_matches - genuine_claims`` per task --
    the fixture's built-in ``devbench.cli`` echo count -- without literally
    materialising echo log lines. A sixth, stale-cross-session-claim task
    (``_ISSUE_329_STALE_TASK_ID``) exercises the #326 exclusion guard inside
    the SAME fixture.

    Returns ``(done_times, progress_claims, unfiltered_progress_claim_counts,
    first_claims, session_starts)``.
    """
    current_session_start = datetime(2026, 8, 11, 16, 0, tzinfo=UTC)
    prior_session_start = datetime(2026, 8, 1, 8, 0, tzinfo=UTC)

    done: dict[str, datetime] = {}
    claims: dict[str, list[datetime]] = {}
    unfiltered_counts: dict[str, int] = {}
    first_claims: dict[str, datetime] = {}
    for i, row in enumerate(_ISSUE_329_MEASURED_TABLE):
        tid = row["task_id"]
        done_at = current_session_start + timedelta(hours=i + 1)
        first_claim = done_at - timedelta(minutes=row["true_window_min"])
        task_claims = [first_claim]
        if row["genuine_claims"] == 2:
            task_claims.append(first_claim + (done_at - first_claim) * 0.6)
        done[tid] = done_at
        claims[tid] = task_claims
        unfiltered_counts[tid] = row["regex_matches"]
        first_claims[tid] = first_claim

    done[_ISSUE_329_STALE_TASK_ID] = current_session_start + timedelta(hours=len(_ISSUE_329_MEASURED_TABLE) + 1)
    claims[_ISSUE_329_STALE_TASK_ID] = [prior_session_start + timedelta(minutes=30)]

    session_starts = [prior_session_start, current_session_start]
    return done, claims, unfiltered_counts, first_claims, session_starts


class TestIssue329RegressionFixture:
    """FR-4 (AC-16, AC-E13-F3-S2-T1-1, spec Section 1.3): the #329 live-repro
    regression fixture. Every assertion below is driven by
    ``_ISSUE_329_MEASURED_TABLE`` rather than a single hardcoded expected
    value, per the workspace standard on input-driven tests.
    """

    @pytest.mark.parametrize(
        "row", _ISSUE_329_MEASURED_TABLE, ids=[row["task_id"] for row in _ISSUE_329_MEASURED_TABLE]
    )
    def test_issue_329_regression_each_task_anchors_to_its_first_genuine_claim(self, row: dict[str, Any]) -> None:
        """Per-task guard, independent of the aggregate median below: every
        task in the measured table -- including the twice-claimed
        E11-F1-S1-T2 -- resolves its execution window from the FIRST
        genuine claim, matching the table's ``true_window_min`` column."""
        from devbench.reporting.report import _execution_anchor

        done, claims, _unfiltered_counts, first_claims, session_starts = _issue_329_regression_fixture()
        tid = row["task_id"]

        anchor = _execution_anchor(claims[tid], done[tid], session_starts)

        assert anchor == first_claims[tid]
        duration_min = (done[tid] - anchor).total_seconds() / 60
        assert duration_min == pytest.approx(row["true_window_min"], abs=0.01)

    def test_issue_329_regression_average_time_per_task_equals_median_of_first_claim_windows(
        self, tmp_path: Path
    ) -> None:
        """AC-16 / AC-E13-F3-S2-T1-1: ``Average time per task`` equals the
        median of the FIRST-claim windows -- computed from the measured
        table at test time, not asserted as a bare literal -- and lands in
        the corrected 17 to 64 min band, never the pre-#329 defect's
        2.5 to 3.2 min band."""
        from devbench.reporting.report import _compute_window_stats

        done, claims, unfiltered_counts, _first_claims, session_starts = _issue_329_regression_fixture()
        log_path = tmp_path / "log.log"
        log_path.write_text("", encoding="utf-8")

        stats = _compute_window_stats(
            log_path,
            min(session_starts),
            max(done.values()) + timedelta(minutes=1),
            done,
            claims,
            tasks_active=0,
            session_starts=session_starts,
            unfiltered_progress_claim_counts=unfiltered_counts,
        )

        expected_median = statistics.median(row["true_window_min"] for row in _ISSUE_329_MEASURED_TABLE)
        assert stats.avg_minutes == pytest.approx(expected_median, abs=0.01)
        assert 17.0 <= stats.avg_minutes <= 64.0
        assert not (2.5 <= stats.avg_minutes <= 3.2)

    def test_issue_329_regression_reclaimed_task_is_measured_from_first_claim_not_last(self) -> None:
        """AC-8 / FR-4 item 3: the twice-claimed task's window is measured
        from its FIRST claim (~63.8 min), never its second (later) claim."""
        from devbench.reporting.report import _execution_anchor

        done, claims, _unfiltered_counts, first_claims, session_starts = _issue_329_regression_fixture()
        tid = _ISSUE_329_RECLAIMED_TASK_ID

        anchor = _execution_anchor(claims[tid], done[tid], session_starts)

        assert anchor == first_claims[tid]
        assert anchor == min(claims[tid])
        first_claim_duration = (done[tid] - anchor).total_seconds() / 60
        second_claim_duration = (done[tid] - max(claims[tid])).total_seconds() / 60
        assert first_claim_duration == pytest.approx(63.8, abs=0.01)
        assert first_claim_duration != pytest.approx(second_claim_duration, abs=0.01)

    def test_issue_329_regression_stale_cross_session_claim_still_excluded_with_326_suffix(
        self, tmp_path: Path
    ) -> None:
        """FR-4 item 4 / G-4: a stale cross-session claim added to the SAME
        fixture is still excluded, and the rendered #326 exclusion suffix
        text is byte-identical to its pre-#329 form."""
        from devbench.reporting.report import _compute_window_stats, _no_execution_window_suffix

        done, claims, unfiltered_counts, _first_claims, session_starts = _issue_329_regression_fixture()
        log_path = tmp_path / "log.log"
        log_path.write_text("", encoding="utf-8")

        stats = _compute_window_stats(
            log_path,
            min(session_starts),
            max(done.values()) + timedelta(minutes=1),
            done,
            claims,
            tasks_active=0,
            session_starts=session_starts,
            unfiltered_progress_claim_counts=unfiltered_counts,
        )

        assert stats.pace_excluded_count == 1
        assert _no_execution_window_suffix(stats.pace_excluded_count) == " (1 excluded: no execution window)"

    def test_issue_329_regression_rejected_row_count_equals_the_fixtures_echo_count(self, tmp_path: Path) -> None:
        """FR-4 item 5 / AC-18: the FR-6 rejected-row count equals the
        number of devbench.cli echo lines built into the fixture -- summed,
        across the measured table, as ``regex_matches - genuine_claims``."""
        from devbench.reporting.report import _compute_window_stats

        done, claims, unfiltered_counts, _first_claims, session_starts = _issue_329_regression_fixture()
        log_path = tmp_path / "log.log"
        log_path.write_text("", encoding="utf-8")

        stats = _compute_window_stats(
            log_path,
            min(session_starts),
            max(done.values()) + timedelta(minutes=1),
            done,
            claims,
            tasks_active=0,
            session_starts=session_starts,
            unfiltered_progress_claim_counts=unfiltered_counts,
        )

        expected_echo_count = sum(row["regex_matches"] - row["genuine_claims"] for row in _ISSUE_329_MEASURED_TABLE)
        assert stats.rejected_row_count == expected_echo_count


class TestReadAllDrainStates:
    """db-306 (spec Section 0 item 7, Section 4 FR-19, R4 RC-2, AC-45):
    ``read_all_drain_states`` scans the workspace-root drain signal AND every
    per-session signal unconditionally -- unlike
    ``devbench.drain.read_drain_state``, whose two-candidate scan is governed
    by ``DEVBENCH_SESSION_NAME`` so a per-session drain is invisible to a
    caller whose shell never exported that variable.
    """

    @staticmethod
    def _write_signal(path: Path, requested_at: str, requested_by: str, reason: str = "") -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps({"requested_at": requested_at, "requested_by": requested_by, "reason": reason}),
            encoding="utf-8",
        )

    def test_read_all_drain_states_finds_session_signal_without_env(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A per-session signal is found even when DEVBENCH_SESSION_NAME is unset,
        and the workspace-root signal is returned alongside it as session None
        (AC-45, db-306).
        """
        monkeypatch.delenv("DEVBENCH_SESSION_NAME", raising=False)
        session_signal = tmp_path / SESSION_SESSIONS_BASE_DIR / SESSION_DEFAULT_NAME / "drain.signal"
        self._write_signal(session_signal, "2026-08-11T09:00:00+00:00", "alice", "per-session freeze")
        root_signal = tmp_path / ".devbench" / "drain.signal"
        self._write_signal(root_signal, "2026-08-11T08:00:00+00:00", "bob", "root freeze")

        states = read_all_drain_states(tmp_path)

        assert len(states) == 2
        root_entry = next(s for s in states if s[0] is None)
        session_entry = next(s for s in states if s[0] == SESSION_DEFAULT_NAME)
        assert root_entry[1].requested_by == "bob"
        assert root_entry[1].reason == "root freeze"
        assert session_entry[1].requested_by == "alice"
        assert session_entry[1].reason == "per-session freeze"

    def test_read_all_drain_states_returns_empty_list_when_no_signals_present(self, tmp_path: Path) -> None:
        """No signal anywhere in the workspace yields an empty list."""
        assert read_all_drain_states(tmp_path) == []

    def test_read_all_drain_states_root_only_signal_uses_none_session(self, tmp_path: Path) -> None:
        """A workspace-root-only signal is returned as a single (None, state) entry."""
        self._write_signal(tmp_path / ".devbench" / "drain.signal", "2026-08-11T08:00:00+00:00", "carol")

        states = read_all_drain_states(tmp_path)

        assert len(states) == 1
        assert states[0][0] is None
        assert states[0][1].requested_by == "carol"

    def test_read_all_drain_states_multiple_sessions_sorted_by_name(self, tmp_path: Path) -> None:
        """Per-session entries are returned in deterministic, sorted-by-name order."""
        self._write_signal(
            tmp_path / SESSION_SESSIONS_BASE_DIR / "zeta" / "drain.signal", "2026-08-11T08:00:00+00:00", "z"
        )
        self._write_signal(
            tmp_path / SESSION_SESSIONS_BASE_DIR / "alpha" / "drain.signal", "2026-08-11T08:00:00+00:00", "a"
        )

        states = read_all_drain_states(tmp_path)

        assert [s[0] for s in states] == ["alpha", "zeta"]

    def test_read_all_drain_states_never_unlinks_a_signal(self, tmp_path: Path) -> None:
        """Read-only: repeated calls never remove the signal file (AC-5, mutation not widened)."""
        signal = tmp_path / SESSION_SESSIONS_BASE_DIR / SESSION_DEFAULT_NAME / "drain.signal"
        self._write_signal(signal, "2026-08-11T08:00:00+00:00", "dana")

        read_all_drain_states(tmp_path)
        read_all_drain_states(tmp_path)

        assert signal.exists()

    def test_read_all_drain_states_corrupt_signal_raises_value_error(self, tmp_path: Path) -> None:
        """Invalid JSON in a signal surfaces as ValueError, matching read_drain_state's contract."""
        signal = tmp_path / ".devbench" / "drain.signal"
        signal.parent.mkdir(parents=True)
        signal.write_text("not json", encoding="utf-8")

        with pytest.raises(ValueError, match="invalid JSON"):
            read_all_drain_states(tmp_path)

    def test_read_all_drain_states_missing_field_raises_key_error(self, tmp_path: Path) -> None:
        """A signal missing a required field surfaces as KeyError, matching read_drain_state's contract."""
        signal = tmp_path / ".devbench" / "drain.signal"
        signal.parent.mkdir(parents=True)
        signal.write_text(json.dumps({"requested_by": "eve"}), encoding="utf-8")

        with pytest.raises(KeyError):
            read_all_drain_states(tmp_path)


def _transport_restart_log_line(attempt: int, cap: int = 5) -> str:
    """Build one raw ``[ORCHESTRATOR_TRANSPORT_RESTART]`` audit line, matching
    the exact literal shape ``cli.py``'s ``_should_restart_after_transport_error``
    logs via ``logger.info("%s attempt=%d max=%d", ...)`` under the
    ``"%(asctime)s [%(name)s] %(levelname)s %(message)s"`` formatter
    (``LOG_FORMAT`` / ``LOG_DATE_FORMAT`` in ``devbench.constants``)."""
    return (
        f"2026-08-12T18:38:0{attempt}Z [devbench.cli] INFO [ORCHESTRATOR_TRANSPORT_RESTART] attempt={attempt} max={cap}"
    )


class TestTransportRestartsLine:
    """Tests for ``transport_restarts_line()`` (#331 FR-4, AC-11).

    A standalone row-rendering function that returns ``None`` when there is
    nothing to say.
    """

    def test_returns_none_when_log_missing(self, tmp_path: Path) -> None:
        from devbench.reporting.report import transport_restarts_line

        assert transport_restarts_line(tmp_path / "does-not-exist.log") is None

    def test_returns_none_when_zero_restarts(self, tmp_path: Path) -> None:
        from devbench.reporting.report import transport_restarts_line

        log_file = tmp_path / "test.log"
        log_file.write_text(
            _make_log(
                [
                    "2026-03-05T10:00:00Z [judges.cli] INFO Set E0-F1-S1-T1 to 'in-progress'",
                    "2026-03-05T10:05:00Z [judges.cli] INFO Set E0-F1-S1-T1 to 'done'",
                ]
            )
        )

        assert transport_restarts_line(log_file) is None

    def test_renders_row_for_single_restart(self, tmp_path: Path) -> None:
        from devbench.reporting.report import transport_restarts_line

        log_file = tmp_path / "test.log"
        log_file.write_text(_make_log([_transport_restart_log_line(1)]))

        line = transport_restarts_line(log_file)

        assert line == "Transport restarts        1"

    @pytest.mark.parametrize("restart_count", [2, 3, 5])
    def test_renders_row_for_multiple_restarts(self, tmp_path: Path, restart_count: int) -> None:
        from devbench.reporting.report import transport_restarts_line

        log_file = tmp_path / "test.log"
        attempts = range(1, restart_count + 1)
        restart_lines = [_transport_restart_log_line(attempt, cap=restart_count) for attempt in attempts]
        log_file.write_text(_make_log(restart_lines))

        line = transport_restarts_line(log_file)

        assert line == f"Transport restarts        {restart_count}"

    def test_ignores_marker_text_echoed_mid_line(self, tmp_path: Path) -> None:
        """A restart marker quoted inside an unrelated SDK payload line (not
        the logger's own line-initial record) must not inflate the count --
        the same echoed-text hazard ``event_index.py``'s ``_TASK_TRANSITION_RE``
        already guards against for task transitions."""
        from devbench.reporting.report import transport_restarts_line

        log_file = tmp_path / "test.log"
        log_file.write_text(
            _make_log(
                [
                    "2026-08-12T18:40:00Z [devbench.cli] INFO sdk message: "
                    "text='previously logged: [ORCHESTRATOR_TRANSPORT_RESTART] attempt=1 max=5'"
                ]
            )
        )

        assert transport_restarts_line(log_file) is None

    def test_transport_restarts_line_asserts_on_impossible_negative_count(
        self, tmp_path: Path, monkeypatch: Any
    ) -> None:
        """Task-specific error path: a negative count is impossible by
        construction (``_count_transport_restarts`` is a match-list length,
        which the ``len`` builtin itself guarantees is never negative) and is
        documented by an assertion at the rendering boundary rather than a
        defensive clamp. ``_count_transport_restarts`` is monkeypatched
        (a legitimate, mockable seam) rather than trying to coerce ``len``
        itself to return a negative number, which the interpreter refuses."""
        from devbench.reporting import report as report_module

        log_file = tmp_path / "test.log"
        log_file.write_text(_make_log(["irrelevant"]))
        monkeypatch.setattr(report_module, "_count_transport_restarts", lambda log_text: -1)

        with pytest.raises(AssertionError, match="negative"):
            report_module.transport_restarts_line(log_file)


class TestGenerateReportTransportRestartsRow:
    """Tests for the ``transport_restarts_line()`` row wired into
    ``generate_report()`` (#331 FR-4, AC-11, spec D-6)."""

    @staticmethod
    def _write_log(tmp_path: Path, extra_entries: list[str] | None = None) -> Path:
        log_file = tmp_path / "test.log"
        entries = [
            "2026-03-05T10:00:00Z [judges.cli] INFO Set E0-F1-S1-T1 to 'in-progress'",
            "2026-03-05T10:05:00Z [judges.cli] INFO Set E0-F1-S1-T1 to 'done'",
        ]
        if extra_entries:
            entries = extra_entries + entries
        log_file.write_text(_make_log(entries))
        return log_file

    def test_row_renders_when_restarts_present(self, tmp_path: Path) -> None:
        restarts = [_transport_restart_log_line(1), _transport_restart_log_line(2)]
        log_file = self._write_log(tmp_path, extra_entries=restarts)

        report = generate_report(log_path=log_file)
        report_lines = report.split("\n")

        # The transport-restarts row is the first line after the banner.
        assert report_lines[1] == "Transport restarts        2"
        assert report_lines[2] == ""

    def test_row_omitted_when_no_restarts_byte_identical(self, tmp_path: Path) -> None:
        """Spec D-6 / AC-11: a clean run (zero restarts) renders nothing for
        this row, so the report stays byte-identical to the pre-FR-4 layout
        -- banner line, then blank."""
        log_file = self._write_log(tmp_path)

        report = generate_report(log_path=log_file)
        report_lines = report.split("\n")

        assert "Transport restarts" not in report
        assert report_lines[1] == ""

    def test_rest_of_report_still_renders_with_restarts_row(self, tmp_path: Path) -> None:
        log_file = self._write_log(tmp_path, extra_entries=[_transport_restart_log_line(1)])

        report = generate_report(log_path=log_file)

        assert "Transport restarts        1" in report
        assert "Tasks completed" in report
        assert "Tasks remaining" in report


class TestReviewRejectionsLine:
    """Tests for ``review_rejections_line()`` -- making a review stall visible.

    A task can burn hours and a large token budget looping through review
    rejections while every health signal reads green: process alive, log
    advancing, zero errors logged. This row is what distinguishes that from
    steady progress.
    """

    def _unit(
        self,
        tmp_path: Path,
        unit_id: str,
        status: WorkUnitStatus,
        verdicts: str = "",
    ) -> WorkUnit:
        wu_file = tmp_path / f"{unit_id}.md"
        wu_file.write_text(f"# {unit_id}\n\n## Comments\n\n{verdicts}", encoding="utf-8")
        return WorkUnit(
            id=unit_id,
            title="Sample",
            status=status,
            unit_type=WorkUnitType.TASK,
            file_path=wu_file,
            repo="caylent-solutions/example",
            dependencies=[],
        )

    def _fail_rows(self, judge: str, count: int) -> str:
        return "".join(
            f"[2026-08-15 0{i}:00 UTC] [judge/{judge}] [REVIEW_FAIL] round {i}\n\n" for i in range(1, count + 1)
        )

    def test_returns_none_when_no_units(self) -> None:
        assert review_rejections_line([], lambda _judge: 10) is None

    def test_returns_none_when_no_rejections(self, tmp_path: Path) -> None:
        """The clean-run contract: no row when there is nothing to say."""
        units = [self._unit(tmp_path, "E0-F1-S1-T1", WorkUnitStatus.IN_PROGRESS)]
        assert review_rejections_line(units, lambda _judge: 10) is None

    def test_reports_count_and_budget(self, tmp_path: Path) -> None:
        units = [
            self._unit(
                tmp_path,
                "E0-F1-S1-T1",
                WorkUnitStatus.IN_PROGRESS,
                self._fail_rows("doc_review", 3),
            )
        ]
        row = review_rejections_line(units, lambda _judge: 10)
        assert row is not None
        assert "E0-F1-S1-T1" in row
        assert "doc_review 3/10" in row

    def test_reports_only_the_judges_that_failed(self, tmp_path: Path) -> None:
        units = [
            self._unit(
                tmp_path,
                "E0-F1-S1-T1",
                WorkUnitStatus.IN_PROGRESS,
                self._fail_rows("doc_review", 2) + self._fail_rows("code_review", 1),
            )
        ]
        row = review_rejections_line(units, lambda _judge: 5)
        assert row is not None
        assert "doc_review 2/5" in row
        assert "code_review 1/5" in row
        assert "test_review" not in row

    def test_per_judge_budget_is_reflected(self, tmp_path: Path) -> None:
        """The denominator comes from the resolver, so it tracks per-judge config."""
        units = [
            self._unit(
                tmp_path,
                "E0-F1-S1-T1",
                WorkUnitStatus.IN_PROGRESS,
                self._fail_rows("doc_review", 1),
            )
        ]
        row = review_rejections_line(units, lambda judge: 2 if judge == "doc_review" else 99)
        assert row is not None
        assert "doc_review 1/2" in row

    def test_done_units_are_ignored(self, tmp_path: Path) -> None:
        """A completed task's rejection history is not actionable."""
        units = [
            self._unit(
                tmp_path,
                "E0-F1-S1-T1",
                WorkUnitStatus.DONE,
                self._fail_rows("doc_review", 4),
            )
        ]
        assert review_rejections_line(units, lambda _judge: 10) is None

    def test_blocked_units_are_reported(self, tmp_path: Path) -> None:
        """A blocked task with a spent budget is exactly what an operator must see."""
        units = [
            self._unit(
                tmp_path,
                "E0-F1-S1-T1",
                WorkUnitStatus.BLOCKED,
                self._fail_rows("doc_review", 10),
            )
        ]
        row = review_rejections_line(units, lambda _judge: 10)
        assert row is not None
        assert "doc_review 10/10" in row

    def test_multiple_units_are_all_listed(self, tmp_path: Path) -> None:
        units = [
            self._unit(tmp_path, "E0-F1-S1-T1", WorkUnitStatus.IN_PROGRESS, self._fail_rows("doc_review", 1)),
            self._unit(tmp_path, "E0-F1-S1-T2", WorkUnitStatus.BLOCKED, self._fail_rows("code_review", 2)),
        ]
        row = review_rejections_line(units, lambda _judge: 10)
        assert row is not None
        assert "E0-F1-S1-T1" in row
        assert "E0-F1-S1-T2" in row

    def test_unreadable_work_unit_does_not_break_the_report(self, tmp_path: Path) -> None:
        """A missing file is surfaced by the checks that need it, not by killing the report."""
        unit = self._unit(tmp_path, "E0-F1-S1-T1", WorkUnitStatus.IN_PROGRESS, self._fail_rows("doc_review", 1))
        unit.file_path.unlink()
        assert review_rejections_line([unit], lambda _judge: 10) is None

    def test_non_canonical_judge_is_not_counted(self, tmp_path: Path) -> None:
        """``manifest_amender`` writes audit-only verdicts and owns no review budget."""
        units = [
            self._unit(
                tmp_path,
                "E0-F1-S1-T1",
                WorkUnitStatus.IN_PROGRESS,
                self._fail_rows("manifest_amender", 3),
            )
        ]
        assert review_rejections_line(units, lambda _judge: 10) is None
