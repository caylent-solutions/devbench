"""Supplementary unit tests for ``devbench.reporting.report`` that pin
error / edge-path branches not covered by the main test suite.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import Any, cast
from unittest.mock import patch

import pytest

from devbench.reporting import report as report_mod
from devbench.reporting.report import (
    CostBreakdown,
    HookLogTotals,
    WindowStats,
    _accumulate_entry,
    _accumulate_transcript_message,
    _empty_totals_acc,
    _entry_in_window,
    _extract_session_from_wu,
    _extract_usage_totals,
    _format_duration,
    _format_est_hours_display,
    _parse_hook_log_metrics,
    _parse_transcript_metrics,
    _parse_transcript_metrics_by_role,
    _read_last_log_timestamp,
    _recent_per_task_cost,
    _render_grouped_progress_table,
    _resolve_transcript_dir,
    _should_use_color,
    _unit_status_listing,
)


def _make_window_stats(**overrides: object) -> WindowStats:
    """Construct a WindowStats with sensible defaults for tests."""
    defaults = {
        "window_start": datetime(2025, 1, 1, tzinfo=UTC),
        "window_hours": 1.0,
        "tasks_in_window": 0,
        "avg_minutes": 0.0,
        "est_hours": 0.0,
        "totals": HookLogTotals(),
        "cost": CostBreakdown(
            input_cost=0.0,
            output_cost=0.0,
            cache_read_cost=0.0,
            cache_write_5m_cost=0.0,
            cache_write_1h_cost=0.0,
            total_cost=0.0,
        ),
        "cache_hit_rate": None,
        "tokens_per_task": 0.0,
        "est_total_cost": 0.0,
        "api_hours": 0.0,
        "api_efficiency": None,
    }
    defaults.update(overrides)
    return WindowStats(**cast(Any, defaults))


class TestExtractUsageTotals:
    """Lines 345, 352-353, 371-376, 378-383: token-aggregation branches."""

    def test_returns_false_for_non_dict_usage(self) -> None:
        acc = _empty_totals_acc()
        assert _extract_usage_totals("not-a-dict", acc) is False

    def test_cache_creation_dict_path_populates_5m_and_1h(self) -> None:
        acc = _empty_totals_acc()
        usage = {
            "input_tokens": 10,
            "output_tokens": 5,
            "cache_creation": {
                "ephemeral_5m_input_tokens": 200,
                "ephemeral_1h_input_tokens": 300,
            },
        }
        assert _extract_usage_totals(usage, acc) is True
        assert acc["cache_write_5m_tokens"] == 200
        assert acc["cache_write_1h_tokens"] == 300

    def test_us_only_branch_tracks_per_subset_totals(self) -> None:
        acc = _empty_totals_acc()
        usage = {
            "input_tokens": 100,
            "output_tokens": 50,
            "cache_read_input_tokens": 25,
            "cache_creation": {"ephemeral_5m_input_tokens": 10, "ephemeral_1h_input_tokens": 20},
            "inference_geo": "US",
        }
        assert _extract_usage_totals(usage, acc) is True
        assert acc["entries_us_geo"] == 1
        assert acc["us_only_input_tokens"] == 100
        assert acc["us_only_output_tokens"] == 50
        assert acc["us_only_cache_read_tokens"] == 25
        assert acc["us_only_cache_write_5m_tokens"] == 10
        assert acc["us_only_cache_write_1h_tokens"] == 20

    def test_fast_mode_branch_tracks_per_subset_totals(self) -> None:
        acc = _empty_totals_acc()
        usage = {
            "input_tokens": 7,
            "output_tokens": 3,
            "cache_read_input_tokens": 2,
            "cache_creation": {"ephemeral_5m_input_tokens": 1, "ephemeral_1h_input_tokens": 4},
            "speed": "fast",
        }
        assert _extract_usage_totals(usage, acc) is True
        assert acc["entries_fast_mode"] == 1
        assert acc["fast_input_tokens"] == 7
        assert acc["fast_output_tokens"] == 3
        assert acc["fast_cache_read_tokens"] == 2
        assert acc["fast_cache_write_5m_tokens"] == 1
        assert acc["fast_cache_write_1h_tokens"] == 4


class TestResolveTranscriptDirCacheHit:
    """Line 407: ``_resolve_transcript_dir`` returns the cached parent dir."""

    def test_cache_hit_returns_parent(self, tmp_path: Path) -> None:
        from typing import cast

        from devbench.reporting.event_index import EventIndex

        class _FakeEventIndex:
            def first_hook_transcript_path(self, hook_log_path: Path) -> str:
                return "/cached/path/sess.jsonl"

        result = _resolve_transcript_dir(cast(EventIndex, _FakeEventIndex()), tmp_path / "hook.jsonl")
        assert result == Path("/cached/path")


class TestAccumulateTranscriptMessage:
    """Line 447: skip non-dict messages."""

    def test_non_dict_message_is_skipped(self) -> None:
        acc = _empty_totals_acc()
        _accumulate_transcript_message("not-a-dict", acc)
        assert acc == _empty_totals_acc()


class TestParseTranscriptMetricsEdgeLines:
    """Lines 482, 485-486, 536, 539-540, 542: blank / malformed transcript
    lines + role-based skip in ``_parse_transcript_metrics_by_role``.
    """

    def test_blank_and_malformed_lines_skipped(self, tmp_path: Path) -> None:
        tdir = tmp_path / "transcripts"
        tdir.mkdir()
        tfile = tdir / "a.jsonl"
        tfile.write_text(
            "\n"
            "{not valid json}\n"
            '{"timestamp": "2020-01-01T00:00:01Z", "message": {"usage": {"input_tokens": 10}, "id": "ok"}}\n',
            encoding="utf-8",
        )
        out = _parse_transcript_metrics(tdir, datetime(2020, 1, 1, tzinfo=UTC))
        assert out.input_tokens == 10

    def test_by_role_blank_and_malformed_and_out_of_window(self, tmp_path: Path) -> None:
        tdir = tmp_path / "transcripts"
        tdir.mkdir()
        tfile = tdir / "a.jsonl"
        # blank, malformed json, out-of-window entry, in-window entry
        in_window = (
            '{"timestamp": "2030-01-01T00:00:00Z", "attributionAgent": "devbench-orchestrate:executor", '
            '"message": {"usage": {"input_tokens": 7}, "id": "new"}}\n'
        )
        tfile.write_text(
            "\n"
            "{garbage}\n"
            '{"timestamp": "1990-01-01T00:00:00Z", "message": {"usage": {"input_tokens": 1}, "id": "old"}}\n'
            + in_window,
            encoding="utf-8",
        )
        out = _parse_transcript_metrics_by_role(tdir, datetime(2025, 1, 1, tzinfo=UTC))
        # Only the in-window entry is bucketed.
        assert "executor" in out
        assert out["executor"].input_tokens == 7


class TestEntryInWindow:
    """Lines 578, 581-582: window-filter branches."""

    def test_missing_timestamp_returns_true(self) -> None:
        assert _entry_in_window({}, datetime(2020, 1, 1, tzinfo=UTC)) is True

    def test_unparseable_timestamp_returns_true(self) -> None:
        assert _entry_in_window({"timestamp": "not-a-date"}, datetime(2020, 1, 1, tzinfo=UTC)) is True


class TestAccumulateEntry:
    """Line 595: tool_resp not a dict -> return early."""

    def test_tool_resp_not_dict_returns(self) -> None:
        acc = _empty_totals_acc()
        _accumulate_entry({"input": {"tool_response": "string-not-dict"}}, acc)
        assert acc == _empty_totals_acc()


class TestParseHookLogMetricsEdgeLines:
    """Lines 614, 617-618, 620: hook-log blank lines / decode errors /
    out-of-window entries."""

    def test_blank_and_malformed_and_window_filter(self, tmp_path: Path) -> None:
        hook_dir = tmp_path / "backlog" / "config"
        hook_dir.mkdir(parents=True)
        hook_log = tmp_path / "backlog" / "hook-logs.jsonl"
        hook_log.write_text(
            "\n"
            "{garbage json\n"
            '{"timestamp": "1990-01-01T00:00:00Z", "input": {"tool_response": {"totalDurationMs": 5}}}\n'
            '{"timestamp": "2030-01-01T00:00:00Z", "input": {"tool_response": {"totalDurationMs": 100}}}\n',
            encoding="utf-8",
        )
        # log_path's parent.parent is tmp_path -- hook_log_path resolves there.
        log_path = hook_dir / "orchestrator.log"
        log_path.write_text("", encoding="utf-8")
        result = _parse_hook_log_metrics(log_path, datetime(2025, 1, 1, tzinfo=UTC))
        # Only the in-window entry contributes 100ms.
        assert result.total_duration_ms == 100


class TestRecentPerTaskCostFallbackPath:
    """Lines 792-794: ``_recent_per_task_cost`` fallback branch when
    ``event_index`` is None."""

    def test_fallback_path_without_event_index(self, tmp_path: Path) -> None:
        """``event_index=None`` exercises the parser fallback (lines 792-794)."""
        log = tmp_path / "log"
        log.write_text("x", encoding="utf-8")
        done_times = {
            "E1-F1-S1-T1": datetime(2025, 1, 1, 1, 0, 0, tzinfo=UTC),
            "E1-F1-S1-T2": datetime(2025, 1, 1, 2, 0, 0, tzinfo=UTC),
        }
        progress_times = {
            "E1-F1-S1-T1": datetime(2025, 1, 1, 0, 30, 0, tzinfo=UTC),
            "E1-F1-S1-T2": datetime(2025, 1, 1, 1, 30, 0, tzinfo=UTC),
        }
        # With n=2 and event_index=None, function takes the fallback branch.
        result = _recent_per_task_cost(
            log_path=log,
            done_times=done_times,
            progress_times=progress_times,
            n=2,
            event_index=None,
        )
        # No hook log present -> no cost data -> per-task cost is 0.0
        assert result == pytest.approx(0.0)


class TestShouldUseColor:
    """Line 1040: NO_COLOR env var disables color."""

    def test_no_color_env_disables(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("NO_COLOR", "1")
        assert _should_use_color() is False


class TestFormatDuration:
    """Line 1071: hours < 24 branch."""

    def test_hours_under_one_day(self) -> None:
        # 2h 30m -> 2h 30m
        assert _format_duration(2 * 3600 + 30 * 60) == "2h 30m"


class TestReadLastLogTimestamp:
    """Lines 1092-1093, 1099, 1101-1102, 1106: tail-read edge cases."""

    def test_returns_none_for_missing_file(self, tmp_path: Path) -> None:
        assert _read_last_log_timestamp(tmp_path / "no-such.log") is None

    def test_returns_none_for_empty_file(self, tmp_path: Path) -> None:
        log = tmp_path / "log"
        log.write_text("", encoding="utf-8")
        assert _read_last_log_timestamp(log) is None

    def test_returns_none_when_no_log_lines(self, tmp_path: Path) -> None:
        log = tmp_path / "log"
        log.write_text("garbage without a timestamp prefix\n", encoding="utf-8")
        assert _read_last_log_timestamp(log) is None

    def test_seeks_when_file_larger_than_tail_buffer(self, tmp_path: Path) -> None:
        log = tmp_path / "log"
        big_blob = ("x" * 100 + "\n") * 1000  # ~101KB
        log.write_text(
            big_blob + "2025-01-01T00:00:00Z [judges.executor] INFO last line\n",
            encoding="utf-8",
        )
        result = _read_last_log_timestamp(log)
        assert result is not None
        assert result.year == 2025

    def test_stat_oserror_returns_none(self, tmp_path: Path) -> None:
        """``stat()`` may raise after a successful ``is_file()`` check (e.g.,
        a race where the file vanishes between the two calls).  Simulate
        that with a counter: first call (is_file) succeeds via the real
        stat, second call (the explicit stat()) raises ``OSError``.
        """
        log = tmp_path / "log"
        log.write_text("data", encoding="utf-8")
        original_stat = Path.stat
        counter = {"n": 0}

        def staggered(self: Path, follow_symlinks: bool = True) -> object:
            if self == log:
                counter["n"] += 1
                if counter["n"] >= 2:
                    raise OSError("denied")
            return original_stat(self, follow_symlinks=follow_symlinks)

        with patch("devbench.reporting.report.Path.stat", staggered):
            assert _read_last_log_timestamp(log) is None

    def test_open_oserror_returns_none(self, tmp_path: Path) -> None:
        log = tmp_path / "log"
        log.write_text("2025-01-01T00:00:00Z [logger] INFO x\n", encoding="utf-8")
        with patch.object(Path, "open", side_effect=OSError("denied")):
            assert _read_last_log_timestamp(log) is None


class TestRenderGroupedProgressTableEmptySections:
    """Line 1349: section with no rows is skipped silently."""

    def test_empty_section_is_skipped(self) -> None:
        sections: list[tuple[str, list[tuple[str, list[str] | str]]]] = [
            ("EMPTY", []),
            ("STATE", [("Metric A", ["0", "0"])]),
        ]
        lines = _render_grouped_progress_table(
            title="Title",
            column_labels=["col1", "col2"],
            sections=sections,
        )
        joined = "\n".join(lines)
        # EMPTY section's label must NOT appear because its rows list is empty.
        assert "EMPTY" not in joined
        assert "STATE" in joined


class TestFormatEstHoursDisplayRuntimeDegradation:
    """Line 1432: runtime-degradation ETA term included when count > 0."""

    def test_runtime_degradation_term_shown(self) -> None:
        stats = _make_window_stats(
            est_hours=3.0,
            recent_pace_minutes=12.0,
            eta_active=5,
            eta_blocked_runtime_degradation=3,
        )
        out = _format_est_hours_display(stats)
        assert "blocked-runtime-degradation 3" in out


class TestUnitStatusListing:
    """Lines 1868-1873: non-empty listing returns header + lines."""

    def test_empty_listing_returns_empty(self) -> None:
        from devbench.backlog.work_unit import WorkUnitStatus

        assert _unit_status_listing([], WorkUnitStatus.IN_PROGRESS, "Header") == []

    def test_populated_listing(self) -> None:
        from devbench.backlog.work_unit import WorkUnit, WorkUnitStatus, WorkUnitType

        u = WorkUnit(
            id="E1-F1-S1-T1",
            title="Some task",
            status=WorkUnitStatus.IN_PROGRESS,
            unit_type=WorkUnitType.TASK,
            file_path=Path("/dev/null"),
            repo="r",
        )
        out = _unit_status_listing([u], WorkUnitStatus.IN_PROGRESS, "Active tasks")
        assert out[0] == ""
        assert out[1] == "Active tasks:"
        assert out[2] == "  - E1-F1-S1-T1: Some task"


class TestExtractSessionFromWuEdgeCases:
    """Lines 2231, 2235: missing file + missing Comments section paths."""

    def test_returns_none_when_file_missing(self, tmp_path: Path) -> None:
        from devbench.backlog.work_unit import WorkUnit, WorkUnitStatus, WorkUnitType

        u = WorkUnit(
            id="x",
            title="t",
            status=WorkUnitStatus.IN_QUEUE,
            unit_type=WorkUnitType.TASK,
            file_path=tmp_path / "no-such.md",
            repo="r",
        )
        assert _extract_session_from_wu(u) is None

    def test_returns_none_when_no_comments_section(self, tmp_path: Path) -> None:
        from devbench.backlog.work_unit import WorkUnit, WorkUnitStatus, WorkUnitType

        wu_file = tmp_path / "wu.md"
        wu_file.write_text("# Title\n\nNo comments section here.\n", encoding="utf-8")
        u = WorkUnit(
            id="x",
            title="t",
            status=WorkUnitStatus.IN_QUEUE,
            unit_type=WorkUnitType.TASK,
            file_path=wu_file,
            repo="r",
        )
        assert _extract_session_from_wu(u) is None


class TestBacklogTotalsExceptionBranches:
    """Lines 1723-1724, 1732: ``_backlog_totals_from_units`` falls back to
    OPERATOR_ACTION_REQUIRED on classify failure + HELD branch tallying."""

    def test_classify_exception_routes_to_operator_action_required(self, tmp_path: Path) -> None:
        from devbench.backlog.work_unit import WorkUnit, WorkUnitStatus, WorkUnitType

        # Create a single BLOCKED task that the classifier will fail to
        # classify.  ``_backlog_totals_from_units`` reads BACKLOG_ROOT /
        # BACKLOG_INDEX; mock classify_blocked_task to raise.
        wu_file = tmp_path / "wu.md"
        wu_file.write_text("status: blocked\n", encoding="utf-8")
        u = WorkUnit(
            id="E1-F1-S1-T1",
            title="t",
            status=WorkUnitStatus.BLOCKED,
            unit_type=WorkUnitType.TASK,
            file_path=wu_file,
            repo="r",
        )
        with patch(
            "devbench.backlog.proposal.classify_blocked_task",
            side_effect=OSError("denied"),
        ):
            totals = report_mod._backlog_totals_from_units([u])
        assert totals.tasks_blocked_operator == 1

    def test_blocked_task_classified_as_held_counts_in_held_bucket(self, tmp_path: Path) -> None:
        """A BLOCKED-status task that the classifier marks as HELD reaches the
        per-state HELD branch at line 1730 (the HOLD-status short-circuit at
        line 1712 only fires for status=HOLD)."""
        from devbench.backlog.proposal import BlockedTaskState
        from devbench.backlog.work_unit import WorkUnit, WorkUnitStatus, WorkUnitType

        wu_file = tmp_path / "wu.md"
        wu_file.write_text("status: blocked\n", encoding="utf-8")
        u = WorkUnit(
            id="E1-F1-S1-T1",
            title="t",
            status=WorkUnitStatus.BLOCKED,
            unit_type=WorkUnitType.TASK,
            file_path=wu_file,
            repo="r",
        )
        with patch(
            "devbench.backlog.proposal.classify_blocked_task",
            return_value=BlockedTaskState.HELD,
        ):
            totals = report_mod._backlog_totals_from_units([u])
        assert totals.tasks_blocked_held == 1


class TestClassifyBlockedUnitIntoBucketsHeldAndUnhandled:
    """Lines 1939, 1947: HELD bucket via the per-row classifier + the
    RuntimeError raised when an unknown BlockedTaskState slips through.
    """

    def test_held_state_routes_to_held_rows(self, tmp_path: Path) -> None:
        from devbench.backlog.proposal import BlockedTaskState
        from devbench.backlog.work_unit import WorkUnit, WorkUnitStatus, WorkUnitType

        wu_file = tmp_path / "wu.md"
        wu_file.write_text("status: blocked\n", encoding="utf-8")
        u = WorkUnit(
            id="x",
            title="t",
            status=WorkUnitStatus.BLOCKED,
            unit_type=WorkUnitType.TASK,
            file_path=wu_file,
            repo="r",
        )
        auto: list = []
        amend: list = []
        dep: list = []
        held: list = []
        on_held: list = []
        runtime_degradation: list = []
        op: list = []
        with patch(
            "devbench.backlog.proposal.classify_blocked_task",
            return_value=BlockedTaskState.HELD,
        ):
            report_mod._classify_blocked_unit_into_buckets(u, auto, amend, dep, held, on_held, runtime_degradation, op)
        assert held == [u]

    def test_unknown_state_raises_runtime_error(self, tmp_path: Path) -> None:
        from devbench.backlog.work_unit import WorkUnit, WorkUnitStatus, WorkUnitType

        wu_file = tmp_path / "wu.md"
        wu_file.write_text("status: blocked\n", encoding="utf-8")
        u = WorkUnit(
            id="x",
            title="t",
            status=WorkUnitStatus.BLOCKED,
            unit_type=WorkUnitType.TASK,
            file_path=wu_file,
            repo="r",
        )
        # A sentinel string is not a BlockedTaskState member -- the
        # else-branch fires.
        auto: list = []
        amend: list = []
        dep: list = []
        held: list = []
        on_held: list = []
        runtime_degradation: list = []
        op: list = []
        with patch(
            "devbench.backlog.proposal.classify_blocked_task",
            return_value="not-a-real-enum-member",
        ):
            with pytest.raises(RuntimeError, match=r"Unhandled BlockedTaskState"):
                report_mod._classify_blocked_unit_into_buckets(
                    u, auto, amend, dep, held, on_held, runtime_degradation, op
                )
