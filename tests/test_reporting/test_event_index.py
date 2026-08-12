"""Tests for the persistent event-index cache (issue #162 Phase 1+4)."""

from __future__ import annotations

import json
import sqlite3
import time
from datetime import UTC, datetime
from pathlib import Path

import pytest

from devbench.reporting.event_index import EventIndex, _epoch_us_to_dt


def _write_orch_log(path: Path, lines: list[str]) -> None:
    """Write an orchestrator-style log file ending with a final newline."""
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _write_jsonl(path: Path, entries: list[dict]) -> None:
    """Write a JSONL file (one JSON object per line) ending with a final newline."""
    path.write_text("\n".join(json.dumps(e) for e in entries) + "\n", encoding="utf-8")


@pytest.fixture
def workspace(tmp_path: Path) -> Path:
    """Per-test workspace with .devbench dir present."""
    (tmp_path / ".devbench").mkdir()
    return tmp_path


class TestSchemaInitialisation:
    """The cache opens cleanly on a fresh dir, on schema-version drift, and on a corrupted DB file."""

    def test_open_creates_cache_dir_and_schema(self, workspace: Path) -> None:
        idx = EventIndex.open(workspace)
        idx.close()
        assert (workspace / ".devbench" / "report-cache" / "events.sqlite").is_file()

    def test_open_is_idempotent(self, workspace: Path) -> None:
        EventIndex.open(workspace).close()
        # Second open must not raise; schema is reapplied via CREATE IF NOT EXISTS.
        EventIndex.open(workspace).close()

    def test_open_rebuilds_on_schema_version_mismatch(self, workspace: Path) -> None:
        idx = EventIndex.open(workspace)
        idx.close()
        db = workspace / ".devbench" / "report-cache" / "events.sqlite"
        # Force a stale schema version; reopen should wipe + rebuild.
        with sqlite3.connect(str(db)) as conn:
            conn.execute("PRAGMA user_version = 0")
            conn.execute("CREATE TABLE rogue (a INTEGER)")
        idx = EventIndex.open(workspace)
        try:
            # ``rogue`` table from the old version was wiped during rebuild.
            with pytest.raises(sqlite3.OperationalError):
                idx._conn.execute("SELECT * FROM rogue").fetchone()
        finally:
            idx.close()

    def test_open_rebuilds_on_corrupted_db_file(self, workspace: Path) -> None:
        cache_dir = workspace / ".devbench" / "report-cache"
        cache_dir.mkdir(parents=True, exist_ok=True)
        db = cache_dir / "events.sqlite"
        # Garbage bytes that aren't a valid SQLite header.
        db.write_bytes(b"\x00" * 200)
        idx = EventIndex.open(workspace)
        try:
            # Cache must function after the rebuild.
            ts = idx.task_transition_times(workspace / "no-such.log", "done")
            assert ts == {}
        finally:
            idx.close()


class TestOrchestratorLogIncremental:
    """Phase 1: append-only mtime+offset incremental cache."""

    def test_perfect_hit_skips_reparse(self, workspace: Path) -> None:
        log = workspace / "orch.log"
        _write_orch_log(
            log,
            [
                "2026-05-04T10:00:00Z [devbench.backlog_manager] INFO Set E0-F1-S1-T1 to 'in-progress'",
                "2026-05-04T10:05:00Z [devbench.backlog_manager] INFO Set E0-F1-S1-T1 to 'done'",
            ],
        )
        idx = EventIndex.open(workspace)
        try:
            idx.refresh_orchestrator_log(log)
            first_done = idx.task_transition_times(log, "done")
            # Second refresh against an unchanged file: rows must not duplicate.
            idx.refresh_orchestrator_log(log)
            second_done = idx.task_transition_times(log, "done")
            assert first_done == second_done
            row_count = idx._conn.execute("SELECT COUNT(*) FROM orch_log_events").fetchone()[0]
            assert row_count == 2
        finally:
            idx.close()

    def test_append_only_growth_parses_just_the_tail(self, workspace: Path) -> None:
        log = workspace / "orch.log"
        _write_orch_log(
            log,
            ["2026-05-04T10:00:00Z [devbench.backlog_manager] INFO Set E0-F1-S1-T1 to 'in-progress'"],
        )
        idx = EventIndex.open(workspace)
        try:
            idx.refresh_orchestrator_log(log)
            assert idx.task_transition_times(log, "done") == {}
            initial_offset = idx._conn.execute("SELECT parsed_offset FROM source_files").fetchone()[0]
            # Append a line; mtime must advance for the cache to notice.
            time.sleep(0.01)
            with log.open("a") as f:
                f.write("2026-05-04T10:05:00Z [devbench.backlog_manager] INFO Set E0-F1-S1-T1 to 'done'\n")
            idx.refresh_orchestrator_log(log)
            done = idx.task_transition_times(log, "done")
            assert "E0-F1-S1-T1" in done
            new_offset = idx._conn.execute("SELECT parsed_offset FROM source_files").fetchone()[0]
            assert new_offset > initial_offset
        finally:
            idx.close()

    def test_truncation_invalidates_and_rebuilds(self, workspace: Path) -> None:
        log = workspace / "orch.log"
        _write_orch_log(
            log,
            [
                "2026-05-04T10:00:00Z [devbench.backlog_manager] INFO Set E0-F1-S1-T1 to 'done'",
                "2026-05-04T10:05:00Z [devbench.backlog_manager] INFO Set E0-F1-S1-T2 to 'done'",
            ],
        )
        idx = EventIndex.open(workspace)
        try:
            idx.refresh_orchestrator_log(log)
            assert len(idx.task_transition_times(log, "done")) == 2
            # Replace with a much shorter file (= truncation / rotation).
            log.write_text(
                "2026-05-04T10:10:00Z [devbench.backlog_manager] INFO Set E0-F1-S1-T9 to 'done'\n",
                encoding="utf-8",
            )
            idx.refresh_orchestrator_log(log)
            done = idx.task_transition_times(log, "done")
            assert done == {"E0-F1-S1-T9": datetime(2026, 5, 4, 10, 10, tzinfo=UTC)}
        finally:
            idx.close()

    def test_partial_unterminated_line_is_skipped_until_flush(self, workspace: Path) -> None:
        log = workspace / "orch.log"
        # No trailing newline -> the writer hasn't finished flushing the line.
        log.write_text(
            "2026-05-04T10:00:00Z [devbench.backlog_manager] INFO Set E0-F1-S1-T1 to 'done'",
            encoding="utf-8",
        )
        idx = EventIndex.open(workspace)
        try:
            idx.refresh_orchestrator_log(log)
            # Partial line must NOT enter the cache.
            assert idx.task_transition_times(log, "done") == {}
            # After the writer finishes the line, the next refresh picks it up.
            time.sleep(0.01)
            log.write_text(
                "2026-05-04T10:00:00Z [devbench.backlog_manager] INFO Set E0-F1-S1-T1 to 'done'\n",
                encoding="utf-8",
            )
            idx.refresh_orchestrator_log(log)
            assert "E0-F1-S1-T1" in idx.task_transition_times(log, "done")
        finally:
            idx.close()

    def test_missing_log_file_is_no_op(self, workspace: Path) -> None:
        idx = EventIndex.open(workspace)
        try:
            idx.refresh_orchestrator_log(workspace / "no-such.log")
            assert idx.task_transition_times(workspace / "no-such.log", "done") == {}
        finally:
            idx.close()


class TestQueryFiltering:
    """Phase 4: indexed queries must return data scoped to the requested source path only."""

    def test_queries_scope_to_source_path(self, workspace: Path) -> None:
        log_a = workspace / "session-a.log"
        log_b = workspace / "session-b.log"
        _write_orch_log(
            log_a,
            ["2026-05-04T10:00:00Z [devbench.backlog_manager] INFO Set E0-F1-S1-T1 to 'done'"],
        )
        _write_orch_log(
            log_b,
            ["2026-05-04T11:00:00Z [devbench.backlog_manager] INFO Set E0-F1-S1-T9 to 'done'"],
        )
        idx = EventIndex.open(workspace)
        try:
            idx.refresh_orchestrator_log(log_a)
            idx.refresh_orchestrator_log(log_b)
            # Each query must see only its own source's events.
            assert "E0-F1-S1-T1" in idx.task_transition_times(log_a, "done")
            assert "E0-F1-S1-T9" not in idx.task_transition_times(log_a, "done")
            assert "E0-F1-S1-T9" in idx.task_transition_times(log_b, "done")
            assert "E0-F1-S1-T1" not in idx.task_transition_times(log_b, "done")
        finally:
            idx.close()

    def test_non_noise_filter_drops_noise_logger(self, workspace: Path) -> None:
        log = workspace / "orch.log"
        _write_orch_log(
            log,
            [
                "2026-05-04T10:00:00Z [devbench.cli] INFO real event",
                "2026-05-04T10:00:30Z [judges.log_setup] INFO heartbeat",
                "2026-05-04T10:01:00Z [devbench.cli] INFO another real event",
            ],
        )
        idx = EventIndex.open(workspace)
        try:
            idx.refresh_orchestrator_log(log)
            non_noise = idx.non_noise_log_timestamps(log, "judges.log_setup")
            assert len(non_noise) == 2
            assert all(ts.minute != 0 or ts.second != 30 for ts in non_noise)  # 10:00:30 entry filtered
        finally:
            idx.close()


class TestLoggerAnchoredTransitions:
    """Issue #329 Defect A / FR-1: transition rows are anchored to the
    genuine emitting logger (``devbench.backlog_manager``), both at query
    time (FR-1a, authoritative) and at ingestion time (FR-1b, hardening).

    ``devbench.cli`` lines that echo prior orchestrator log text inside an
    SDK ``ToolResultBlock`` payload (e.g. a tool result that read the work
    unit's ``[WU_CLAIMED]`` audit comment) previously matched the unanchored
    ``_TASK_TRANSITION_RE.search(raw_line)`` and were counted as genuine
    transitions under the echo's own (later) timestamp.
    """

    def test_transition_logger_constant(self) -> None:
        """AC-1: the module constant is the real emitter's logger name."""
        from devbench.reporting.event_index import _TRANSITION_LOGGER

        assert _TRANSITION_LOGGER == "devbench.backlog_manager"

    def test_query_methods_return_only_the_genuine_logger_row(self, workspace: Path) -> None:
        """AC-2: given a genuine row and a later echo row for the same task
        and transition, both query methods return only the genuine timestamp."""
        log = workspace / "orch.log"
        _write_orch_log(
            log,
            [
                "2026-05-04T10:00:00Z [devbench.backlog_manager] INFO Set E1-F1-S1-T1 to 'in-progress' "
                "in both work-unit file and BACKLOG.md",
                "2026-05-04T10:38:00Z [devbench.cli] INFO ToolResultBlock echoed prior comment: "
                "[WU_CLAIMED] Set E1-F1-S1-T1 to 'in-progress' session=default",
            ],
        )
        idx = EventIndex.open(workspace)
        try:
            idx.refresh_orchestrator_log(log)
            expected = {"E1-F1-S1-T1": datetime(2026, 5, 4, 10, 0, tzinfo=UTC)}
            assert idx.task_transition_times(log, "in-progress") == expected
            assert idx.task_transition_times_for_workspace(workspace, log, "in-progress") == expected
        finally:
            idx.close()

    def test_null_logger_row_is_excluded_from_both_query_methods(self, workspace: Path) -> None:
        """AC-3: a corrupt/unattributable row (``logger IS NULL``) with a
        populated task_id/transition never satisfies the predicate."""
        log = workspace / "orch.log"
        _write_orch_log(log, ["2026-05-04T10:00:00Z [devbench.backlog_manager] INFO heartbeat"])
        idx = EventIndex.open(workspace)
        try:
            idx.refresh_orchestrator_log(log)
            file_id = idx._conn.execute("SELECT file_id FROM source_files WHERE path = ?", (str(log),)).fetchone()[0]
            idx._conn.execute(
                "INSERT INTO orch_log_events "
                "(file_id, line_offset, ts_epoch_us, logger, task_id, transition) VALUES (?, ?, ?, ?, ?, ?)",
                (file_id, 9999, 1_746_353_400_000_000, None, "E1-F1-S1-T1", "in-progress"),
            )
            assert idx.task_transition_times(log, "in-progress") == {}
            assert idx.task_transition_times_for_workspace(workspace, log, "in-progress") == {}
        finally:
            idx.close()

    def test_pre_existing_cache_is_corrected_with_no_rebuild(self, workspace: Path) -> None:
        """AC-4: a cache already holding a row shaped exactly as pre-FR-1b
        ingestion would have written it (task_id/transition populated from
        an echo line under a non-``backlog_manager`` logger) is corrected by
        the query-side predicate alone. No ``refresh_orchestrator_log`` call
        happens between the direct insert and the query below, so nothing
        gets re-ingested or rebuilt -- the existing cached row is simply
        queried correctly the moment the new predicate exists."""
        log = workspace / "orch.log"
        _write_orch_log(log, ["2026-05-04T10:00:00Z [devbench.backlog_manager] INFO heartbeat"])
        idx = EventIndex.open(workspace)
        try:
            idx.refresh_orchestrator_log(log)
            file_id = idx._conn.execute("SELECT file_id FROM source_files WHERE path = ?", (str(log),)).fetchone()[0]
            idx._conn.executemany(
                "INSERT INTO orch_log_events "
                "(file_id, line_offset, ts_epoch_us, logger, task_id, transition) VALUES (?, ?, ?, ?, ?, ?)",
                [
                    (file_id, 1000, 1_746_353_400_000_000, "devbench.backlog_manager", "E1-F1-S1-T1", "in-progress"),
                    # Shaped exactly as the unanchored pre-FR-1b ingestion
                    # would have captured a later echo line: task_id and
                    # transition are populated even though the logger is not
                    # the real emitter.
                    (file_id, 2000, 1_746_355_080_000_000, "devbench.cli", "E1-F1-S1-T1", "in-progress"),
                ],
            )
            expected = {"E1-F1-S1-T1": _epoch_us_to_dt(1_746_353_400_000_000)}
            assert idx.task_transition_times(log, "in-progress") == expected
            assert idx.task_transition_times_for_workspace(workspace, log, "in-progress") == expected
        finally:
            idx.close()

    def test_ingestion_anchors_to_own_message_not_a_quoted_payload(self, workspace: Path) -> None:
        """AC-5: at ingestion, a genuine transition line is stored with
        task_id/transition populated; an echo line whose payload quotes a
        transition later in the same message is stored with task_id and
        transition left NULL (over-anchoring guard: the genuine line still
        gets captured)."""
        log = workspace / "orch.log"
        _write_orch_log(
            log,
            [
                "2026-05-04T10:00:00Z [devbench.backlog_manager] INFO Set E1-F1-S1-T1 to 'in-progress' "
                "in both work-unit file and BACKLOG.md",
                "2026-05-04T10:38:00Z [devbench.cli] INFO ToolResultBlock echoed prior comment: "
                "[WU_CLAIMED] Set E1-F1-S1-T1 to 'in-progress' session=default",
            ],
        )
        idx = EventIndex.open(workspace)
        try:
            idx.refresh_orchestrator_log(log)
            rows = idx._conn.execute(
                "SELECT logger, task_id, transition FROM orch_log_events ORDER BY line_offset"
            ).fetchall()
            assert len(rows) == 2
            genuine, echo = rows
            assert genuine == ("devbench.backlog_manager", "E1-F1-S1-T1", "in-progress")
            assert echo[0] == "devbench.cli"
            assert echo[1] is None
            assert echo[2] is None
        finally:
            idx.close()


class TestTaskTransitionTimeSeriesForWorkspace:
    """Issue #329 FR-2 (AC-6): ``task_transition_time_series_for_workspace``
    returns EVERY matching timestamp per task_id, ascending, under the same
    file_id/transition/logger predicates as ``task_transition_times_for_workspace``
    (FR-1a). Unlike that method's ``MAX(ts_epoch_us) GROUP BY task_id``, no
    aggregation happens here -- ``_execution_anchor`` (report.py) needs every
    candidate claim, not just the most recent one, to find the earliest
    same-session claim before a completion.
    """

    def test_returns_every_matching_timestamp_ascending(self, workspace: Path) -> None:
        """#329 live shape: E11-F1-S1-T2 was claimed twice before being closed."""
        log = workspace / "orch.log"
        _write_orch_log(
            log,
            [
                "2026-08-10T19:50:20Z [devbench.backlog_manager] INFO Set E11-F1-S1-T2 to 'in-progress'",
                "2026-08-10T20:34:17Z [devbench.backlog_manager] INFO Set E11-F1-S1-T2 to 'in-progress'",
                "2026-08-10T20:54:07Z [devbench.backlog_manager] INFO Set E11-F1-S1-T2 to 'done'",
            ],
        )
        idx = EventIndex.open(workspace)
        try:
            idx.refresh_orch_log_sources(workspace, log)
            series = idx.task_transition_time_series_for_workspace(workspace, log, "in-progress")
            assert series == {
                "E11-F1-S1-T2": [
                    datetime(2026, 8, 10, 19, 50, 20, tzinfo=UTC),
                    datetime(2026, 8, 10, 20, 34, 17, tzinfo=UTC),
                ]
            }
        finally:
            idx.close()

    def test_applies_the_same_logger_predicate_as_fr1a(self, workspace: Path) -> None:
        """A ``devbench.cli`` echo of a prior claim line never contributes a
        series entry -- same predicate as ``task_transition_times_for_workspace``."""
        log = workspace / "orch.log"
        _write_orch_log(
            log,
            [
                "2026-05-04T10:00:00Z [devbench.backlog_manager] INFO Set E1-F1-S1-T1 to 'in-progress' "
                "in both work-unit file and BACKLOG.md",
                "2026-05-04T10:38:00Z [devbench.cli] INFO ToolResultBlock echoed prior comment: "
                "[WU_CLAIMED] Set E1-F1-S1-T1 to 'in-progress' session=default",
            ],
        )
        idx = EventIndex.open(workspace)
        try:
            idx.refresh_orch_log_sources(workspace, log)
            series = idx.task_transition_time_series_for_workspace(workspace, log, "in-progress")
            assert series == {"E1-F1-S1-T1": [datetime(2026, 5, 4, 10, 0, tzinfo=UTC)]}
        finally:
            idx.close()

    def test_null_logger_row_is_excluded(self, workspace: Path) -> None:
        log = workspace / "orch.log"
        _write_orch_log(log, ["2026-05-04T10:00:00Z [devbench.backlog_manager] INFO heartbeat"])
        idx = EventIndex.open(workspace)
        try:
            idx.refresh_orch_log_sources(workspace, log)
            file_id = idx._conn.execute("SELECT file_id FROM source_files WHERE path = ?", (str(log),)).fetchone()[0]
            idx._conn.execute(
                "INSERT INTO orch_log_events "
                "(file_id, line_offset, ts_epoch_us, logger, task_id, transition) VALUES (?, ?, ?, ?, ?, ?)",
                (file_id, 9999, 1_746_353_400_000_000, None, "E1-F1-S1-T1", "in-progress"),
            )
            assert idx.task_transition_time_series_for_workspace(workspace, log, "in-progress") == {}
        finally:
            idx.close()

    def test_multiple_tasks_each_get_their_own_ascending_series(self, workspace: Path) -> None:
        log = workspace / "orch.log"
        _write_orch_log(
            log,
            [
                "2026-05-04T10:00:00Z [devbench.backlog_manager] INFO Set E0-F1-S1-T1 to 'in-progress'",
                "2026-05-04T10:10:00Z [devbench.backlog_manager] INFO Set E0-F1-S1-T2 to 'in-progress'",
                "2026-05-04T10:20:00Z [devbench.backlog_manager] INFO Set E0-F1-S1-T1 to 'in-progress'",
            ],
        )
        idx = EventIndex.open(workspace)
        try:
            idx.refresh_orch_log_sources(workspace, log)
            series = idx.task_transition_time_series_for_workspace(workspace, log, "in-progress")
            assert series["E0-F1-S1-T1"] == [
                datetime(2026, 5, 4, 10, 0, tzinfo=UTC),
                datetime(2026, 5, 4, 10, 20, tzinfo=UTC),
            ]
            assert series["E0-F1-S1-T2"] == [datetime(2026, 5, 4, 10, 10, tzinfo=UTC)]
        finally:
            idx.close()

    def test_empty_workspace_returns_empty_dict(self, tmp_path: Path) -> None:
        (tmp_path / ".devbench").mkdir()
        live_log = tmp_path / "logs" / "orchestrator.log"
        idx = EventIndex.open(tmp_path)
        try:
            idx.refresh_orch_log_sources(tmp_path, live_log)
            assert idx.task_transition_time_series_for_workspace(tmp_path, live_log, "in-progress") == {}
        finally:
            idx.close()

    def test_merges_shards_with_live_log(self, tmp_path: Path) -> None:
        """Same shard-union behaviour as ``task_transition_times_for_workspace`` (issue #168)."""
        (tmp_path / ".devbench").mkdir()
        shard = tmp_path / "logs" / "2026-04" / "E0-F1-S1-T1.jsonl"
        shard.parent.mkdir(parents=True)
        _write_orch_log(
            shard,
            ["2026-04-15T10:00:00Z [devbench.backlog_manager] INFO Set E0-F1-S1-T1 to 'in-progress'"],
        )
        live_log = tmp_path / "logs" / "orchestrator.log"
        _write_orch_log(
            live_log,
            ["2026-05-10T10:00:00Z [devbench.backlog_manager] INFO Set E0-F1-S1-T1 to 'in-progress'"],
        )
        idx = EventIndex.open(tmp_path)
        try:
            idx.refresh_orch_log_sources(tmp_path, live_log)
            series = idx.task_transition_time_series_for_workspace(tmp_path, live_log, "in-progress")
            assert series["E0-F1-S1-T1"] == [
                datetime(2026, 4, 15, 10, 0, tzinfo=UTC),
                datetime(2026, 5, 10, 10, 0, tzinfo=UTC),
            ]
        finally:
            idx.close()


class TestHookLogAggregation:
    """Phase 4: hook-log entries aggregate via indexed range scans."""

    def test_aggregate_excludes_entries_before_window_start(self, workspace: Path) -> None:
        hook = workspace / "hook-logs.jsonl"
        _write_jsonl(
            hook,
            [
                {
                    "timestamp": "2026-05-04T10:00:00.000000+00:00",
                    "input": {
                        "tool_response": {"totalDurationMs": 1000, "usage": {"input_tokens": 100}},
                    },
                },
                {
                    "timestamp": "2026-05-04T11:00:00.000000+00:00",
                    "input": {
                        "tool_response": {"totalDurationMs": 2000, "usage": {"input_tokens": 200}},
                    },
                },
            ],
        )
        idx = EventIndex.open(workspace)
        try:
            idx.refresh_hook_log(hook)
            window_at_1030 = idx.aggregate_hook_window(hook, datetime(2026, 5, 4, 10, 30, tzinfo=UTC))
            assert window_at_1030["input_tokens"] == 200
            assert window_at_1030["total_duration_ms"] == 2000
            window_at_0900 = idx.aggregate_hook_window(hook, datetime(2026, 5, 4, 9, 0, tzinfo=UTC))
            assert window_at_0900["input_tokens"] == 300
            assert window_at_0900["total_duration_ms"] == 3000
        finally:
            idx.close()

    def test_unparseable_timestamp_entry_is_always_in_window(self, workspace: Path) -> None:
        hook = workspace / "hook-logs.jsonl"
        # One unparseable-timestamp entry; legacy ``_entry_in_window`` includes
        # it in every window so cost data is not silently dropped.
        _write_jsonl(
            hook,
            [
                {
                    "timestamp": "not-a-date",
                    "input": {
                        "tool_response": {"totalDurationMs": 5000, "usage": {"input_tokens": 7777}},
                    },
                }
            ],
        )
        idx = EventIndex.open(workspace)
        try:
            idx.refresh_hook_log(hook)
            window_far_future = idx.aggregate_hook_window(hook, datetime(2099, 1, 1, tzinfo=UTC))
            assert window_far_future["input_tokens"] == 7777
            assert window_far_future["total_duration_ms"] == 5000
        finally:
            idx.close()

    def test_us_only_and_fast_subsets_attribute_correctly(self, workspace: Path) -> None:
        hook = workspace / "hook-logs.jsonl"
        _write_jsonl(
            hook,
            [
                {
                    "timestamp": "2026-05-04T10:00:00.000000+00:00",
                    "input": {
                        "tool_response": {
                            "usage": {"input_tokens": 1000, "inference_geo": True},
                        },
                    },
                },
                {
                    "timestamp": "2026-05-04T10:01:00.000000+00:00",
                    "input": {
                        "tool_response": {
                            "usage": {"input_tokens": 500, "speed": "fast"},
                        },
                    },
                },
                {
                    "timestamp": "2026-05-04T10:02:00.000000+00:00",
                    "input": {"tool_response": {"usage": {"input_tokens": 200}}},
                },
            ],
        )
        idx = EventIndex.open(workspace)
        try:
            idx.refresh_hook_log(hook)
            agg = idx.aggregate_hook_window(hook, datetime(2026, 5, 4, 9, 0, tzinfo=UTC))
            assert agg["input_tokens"] == 1700
            assert agg["us_only_input_tokens"] == 1000
            assert agg["fast_input_tokens"] == 500
            assert agg["entries_us_geo"] == 1
            assert agg["entries_fast_mode"] == 1
            assert agg["entries_with_usage"] == 3
        finally:
            idx.close()


class TestTranscriptAggregation:
    """Phase 4: transcript-dir aggregation walks every file under the dir via indexed query."""

    def test_aggregates_across_every_jsonl_in_dir(self, workspace: Path) -> None:
        tdir = workspace / "transcripts"
        tdir.mkdir()
        _write_jsonl(
            tdir / "a.jsonl",
            [
                {
                    "timestamp": "2026-05-04T10:00:00.000000+00:00",
                    "message": {"usage": {"input_tokens": 100, "output_tokens": 50}},
                }
            ],
        )
        _write_jsonl(
            tdir / "b.jsonl",
            [
                {
                    "timestamp": "2026-05-04T10:30:00.000000+00:00",
                    "message": {"usage": {"input_tokens": 200, "output_tokens": 75}},
                }
            ],
        )
        idx = EventIndex.open(workspace)
        try:
            idx.refresh_transcripts(tdir)
            agg = idx.aggregate_transcript_window(tdir, datetime(2026, 5, 4, 9, 0, tzinfo=UTC))
            assert agg["input_tokens"] == 300
            assert agg["output_tokens"] == 125
        finally:
            idx.close()

    def test_unknown_dir_returns_empty(self, workspace: Path) -> None:
        idx = EventIndex.open(workspace)
        try:
            agg = idx.aggregate_transcript_window(None, datetime(2026, 5, 4, tzinfo=UTC))
            assert agg["input_tokens"] == 0
            agg = idx.aggregate_transcript_window(workspace / "no-such", datetime(2026, 5, 4, tzinfo=UTC))
            assert agg["input_tokens"] == 0
        finally:
            idx.close()

    def test_skips_messages_with_no_usage(self, workspace: Path) -> None:
        """A transcript message without a ``usage`` block contributes zero."""
        tdir = workspace / "transcripts"
        tdir.mkdir()
        _write_jsonl(
            tdir / "session.jsonl",
            [
                {
                    "timestamp": "2026-05-04T10:00:00.000000+00:00",
                    "message": {"role": "user", "content": "hi"},  # no usage
                },
                {
                    "timestamp": "2026-05-04T10:01:00.000000+00:00",
                    "message": {
                        "role": "assistant",
                        "usage": {"input_tokens": 42, "output_tokens": 7},
                    },
                },
            ],
        )
        idx = EventIndex.open(workspace)
        try:
            idx.refresh_transcripts(tdir)
            agg = idx.aggregate_transcript_window(tdir, datetime(2026, 5, 4, 9, 0, tzinfo=UTC))
            assert agg["input_tokens"] == 42
            assert agg["output_tokens"] == 7
            assert agg["entries_with_usage"] == 1
        finally:
            idx.close()


class TestTranscriptResumedSessionDedupIndexed:
    """Issue #169: the indexed path must dedup carried-forward messages by
    ``message.id`` so the cross-file aggregate matches the parser path's
    deduped figure. Implemented as a partial UNIQUE index +
    ``INSERT OR IGNORE`` at ingest time so the aggregate query stays simple.
    """

    @staticmethod
    def _msg_entry(ts: str, msg_id: str | None, in_tokens: int, out_tokens: int) -> dict:
        message: dict = {"role": "assistant", "usage": {"input_tokens": in_tokens, "output_tokens": out_tokens}}
        if msg_id is not None:
            message["id"] = msg_id
        return {"timestamp": ts, "message": message}

    def test_duplicate_message_ids_across_files_are_counted_once(self, workspace: Path) -> None:
        tdir = workspace / "transcripts"
        tdir.mkdir()
        # File A: m1, m2, m3. File B: m2 + m3 carried-forward + new m4.
        _write_jsonl(
            tdir / "a.jsonl",
            [
                self._msg_entry("2026-05-04T10:01:00.000000+00:00", "m1", 10, 5),
                self._msg_entry("2026-05-04T10:02:00.000000+00:00", "m2", 20, 7),
                self._msg_entry("2026-05-04T10:03:00.000000+00:00", "m3", 30, 9),
            ],
        )
        _write_jsonl(
            tdir / "b.jsonl",
            [
                self._msg_entry("2026-05-04T10:02:00.000000+00:00", "m2", 20, 7),
                self._msg_entry("2026-05-04T10:03:00.000000+00:00", "m3", 30, 9),
                self._msg_entry("2026-05-04T10:04:00.000000+00:00", "m4", 40, 11),
            ],
        )
        idx = EventIndex.open(workspace)
        try:
            idx.refresh_transcripts(tdir)
            agg = idx.aggregate_transcript_window(tdir, datetime(2026, 5, 4, 9, 0, tzinfo=UTC))
            # Deduped: m1+m2+m3+m4 = 100 input / 32 output / 4 entries
            assert agg["input_tokens"] == 100
            assert agg["output_tokens"] == 32
            assert agg["entries_with_usage"] == 4
        finally:
            idx.close()

    def test_entries_without_message_id_still_count(self, workspace: Path) -> None:
        """Partial unique index excludes NULL ``message_id`` so id-less rows still accumulate."""
        tdir = workspace / "transcripts"
        tdir.mkdir()
        _write_jsonl(
            tdir / "a.jsonl",
            [
                self._msg_entry("2026-05-04T10:01:00.000000+00:00", None, 10, 5),
                self._msg_entry("2026-05-04T10:02:00.000000+00:00", None, 20, 7),
            ],
        )
        idx = EventIndex.open(workspace)
        try:
            idx.refresh_transcripts(tdir)
            agg = idx.aggregate_transcript_window(tdir, datetime(2026, 5, 4, 9, 0, tzinfo=UTC))
            assert agg["input_tokens"] == 30
            assert agg["output_tokens"] == 12
            assert agg["entries_with_usage"] == 2
        finally:
            idx.close()

    def test_indexed_dedup_matches_parser_dedup(self, workspace: Path) -> None:
        """Parity: indexed and parser paths both report the same deduped totals."""
        from devbench.reporting.report import _parse_transcript_metrics

        tdir = workspace / "transcripts"
        tdir.mkdir()
        _write_jsonl(
            tdir / "a.jsonl",
            [
                self._msg_entry("2026-05-04T10:01:00.000000+00:00", "m1", 10, 5),
                self._msg_entry("2026-05-04T10:02:00.000000+00:00", "m2", 20, 7),
            ],
        )
        _write_jsonl(
            tdir / "b.jsonl",
            [
                self._msg_entry("2026-05-04T10:02:00.000000+00:00", "m2", 20, 7),  # dup of A's m2
                self._msg_entry("2026-05-04T10:03:00.000000+00:00", "m3", 30, 9),
            ],
        )
        window_start = datetime(2026, 5, 4, 9, 0, tzinfo=UTC)
        idx = EventIndex.open(workspace)
        try:
            idx.refresh_transcripts(tdir)
            indexed = idx.aggregate_transcript_window(tdir, window_start)
        finally:
            idx.close()
        parsed = _parse_transcript_metrics(tdir, window_start)
        assert indexed["input_tokens"] == parsed.input_tokens
        assert indexed["output_tokens"] == parsed.output_tokens
        assert indexed["entries_with_usage"] == parsed.entries_with_usage


class TestDiscoverTranscriptDirCacheHit:
    """``first_hook_transcript_path`` returns the earliest cached path; legacy fallback when empty."""

    def test_returns_first_hook_entry_path(self, workspace: Path) -> None:
        hook = workspace / "hook-logs.jsonl"
        _write_jsonl(
            hook,
            [
                {
                    "timestamp": "2026-05-04T10:00:00.000000+00:00",
                    "input": {
                        "transcript_path": "/home/u/.claude/projects/x/aaa.jsonl",
                        "tool_response": {"usage": {"input_tokens": 1}},
                    },
                },
                {
                    "timestamp": "2026-05-04T10:01:00.000000+00:00",
                    "input": {
                        "transcript_path": "/home/u/.claude/projects/x/bbb.jsonl",
                        "tool_response": {"usage": {"input_tokens": 1}},
                    },
                },
            ],
        )
        idx = EventIndex.open(workspace)
        try:
            idx.refresh_hook_log(hook)
            assert idx.first_hook_transcript_path(hook) == "/home/u/.claude/projects/x/aaa.jsonl"
        finally:
            idx.close()


class TestSnapshot:
    """Phase 6 schema rows: write/read freshness keyed on log mtime + size."""

    def test_snapshot_round_trips_when_log_unchanged(self, workspace: Path) -> None:
        log = workspace / "orch.log"
        log.write_text("2026-05-04T10:00:00Z [devbench.cli] INFO event\n", encoding="utf-8")
        idx = EventIndex.open(workspace)
        try:
            idx.write_snapshot(log, {"body": "rendered output"})
            assert idx.read_snapshot(log) == {"body": "rendered output"}
        finally:
            idx.close()

    def test_snapshot_invalidates_when_log_advances(self, workspace: Path) -> None:
        log = workspace / "orch.log"
        log.write_text("2026-05-04T10:00:00Z [devbench.cli] INFO a\n", encoding="utf-8")
        idx = EventIndex.open(workspace)
        try:
            idx.write_snapshot(log, {"body": "old"})
            time.sleep(0.01)
            with log.open("a") as f:
                f.write("2026-05-04T10:01:00Z [devbench.cli] INFO b\n")
            assert idx.read_snapshot(log) is None
        finally:
            idx.close()

    def test_snapshot_returns_none_when_log_missing(self, workspace: Path) -> None:
        idx = EventIndex.open(workspace)
        try:
            assert idx.read_snapshot(workspace / "no-such.log") is None
        finally:
            idx.close()


class TestParityAgainstParserPath:
    """Rendered output of ``generate_report`` must match between indexed + parser paths.

    Issue #162's overall AC: "no reporting accuracy regression". The
    indexed path is exercised by every other generate_report test in
    this suite (via the ``EventIndex.open`` call inside
    ``generate_report``). Here we additionally pin the parity by
    comparing the indexed-aggregate output of
    ``_compute_window_stats(event_index=...)`` against the parser-path
    output ``_compute_window_stats(event_index=None)`` for the same
    inputs.
    """

    def _seed(self, workspace: Path) -> tuple[Path, Path, EventIndex]:
        log = workspace / "logs" / "orchestrator.log"
        log.parent.mkdir(parents=True)
        _write_orch_log(
            log,
            [
                "2026-05-04T10:00:00Z [devbench.backlog_manager] INFO Set E0-F1-S1-T1 to 'in-progress'",
                "2026-05-04T10:05:00Z [devbench.backlog_manager] INFO Set E0-F1-S1-T1 to 'done'",
                "2026-05-04T10:05:00Z [devbench.backlog_manager] INFO Set E0-F1-S1-T2 to 'in-progress'",
                "2026-05-04T10:10:00Z [devbench.backlog_manager] INFO Set E0-F1-S1-T2 to 'done'",
                "2026-05-04T10:10:00Z [devbench.backlog_manager] INFO Set E0-F1-S1-T3 to 'in-progress'",
                "2026-05-04T10:15:00Z [devbench.backlog_manager] INFO Set E0-F1-S1-T3 to 'done'",
            ],
        )
        hook = workspace / "hook-logs.jsonl"
        _write_jsonl(
            hook,
            [
                {
                    "timestamp": "2026-05-04T10:00:30.000000+00:00",
                    "input": {
                        "tool_response": {"totalDurationMs": 1000, "usage": {"input_tokens": 1234}},
                    },
                },
                {
                    "timestamp": "2026-05-04T10:06:00.000000+00:00",
                    "input": {
                        "tool_response": {
                            "totalDurationMs": 2500,
                            "usage": {"output_tokens": 567, "cache_read_input_tokens": 50},
                        },
                    },
                },
            ],
        )
        idx = EventIndex.open(workspace)
        idx.refresh_orchestrator_log(log)
        idx.refresh_hook_log(hook)
        return log, hook, idx

    def test_indexed_and_parser_paths_match(self, workspace: Path) -> None:
        from devbench.reporting.report import _compute_window_stats

        log, _hook, idx = self._seed(workspace)
        try:
            done_times = idx.task_transition_times(log, "done")
            progress_times = idx.task_transition_times(log, "in-progress")
            # Issue #329 FR-2: `_compute_window_stats` now takes a claims
            # time-series per task; this fixture models one claim per task,
            # so a single-element list preserves the original parity intent.
            progress_claims = {tid: [ts] for tid, ts in progress_times.items()}
            window_start = datetime(2026, 5, 4, 10, 0, tzinfo=UTC)
            window_end = datetime(2026, 5, 4, 10, 16, tzinfo=UTC)
            indexed = _compute_window_stats(
                log_path=log,
                window_start=window_start,
                window_end=window_end,
                done_times=done_times,
                progress_claims=progress_claims,
                tasks_active=0,
                event_index=idx,
            )
            parsed = _compute_window_stats(
                log_path=log,
                window_start=window_start,
                window_end=window_end,
                done_times=done_times,
                progress_claims=progress_claims,
                tasks_active=0,
                event_index=None,
            )
            # Token totals identical.
            assert indexed.totals == parsed.totals
            # Cost totals identical.
            assert indexed.cost.total_cost == pytest.approx(parsed.cost.total_cost)
            # API hours identical.
            assert indexed.api_hours == pytest.approx(parsed.api_hours)
            # Tasks-in-window count identical.
            assert indexed.tasks_in_window == parsed.tasks_in_window
        finally:
            idx.close()

    def test_session_boundary_matches_parser(self, workspace: Path) -> None:
        from devbench.reporting.report import (
            _find_current_session_start,
            _find_current_session_start_from_index,
        )

        log = workspace / "orch.log"
        # Session boundary at 10:01 (gap > 10 minutes between earlier 09:00 and later 10:01).
        _write_orch_log(
            log,
            [
                "2026-05-04T08:00:00Z [devbench.cli] INFO old session",
                "2026-05-04T08:30:00Z [devbench.cli] INFO old session",
                "2026-05-04T09:00:00Z [devbench.cli] INFO old session",
                "2026-05-04T10:01:00Z [devbench.cli] INFO new session start",
                "2026-05-04T10:02:00Z [devbench.cli] INFO new session continues",
            ],
        )
        idx = EventIndex.open(workspace)
        try:
            idx.refresh_orchestrator_log(log)
            indexed_boundary = _find_current_session_start_from_index(idx, log)
            parsed_boundary = _find_current_session_start(log.read_text(encoding="utf-8"))
            assert indexed_boundary == parsed_boundary
        finally:
            idx.close()


class TestRefreshOrchLogSourcesShardAware:
    """Issue #168: ``refresh_orch_log_sources`` + workspace-aware queries
    must merge the live flat log with every shard under
    ``logs/<YYYY-MM>/`` so reports keep working post Phase-3 migration."""

    def _seed_post_migration_workspace(self, tmp_path: Path) -> Path:
        """Build a workspace where the historical events live in shards
        and the live log is empty (mirrors the post-migration state we
        hit on caylent-telemetry-spec). Returns the live log path."""
        (tmp_path / ".devbench").mkdir()
        # Shards under logs/2026-04/ + logs/2026-05/.
        shard_apr = tmp_path / "logs" / "2026-04" / "E0-F1-S1-T1.jsonl"
        shard_apr.parent.mkdir(parents=True)
        _write_orch_log(
            shard_apr,
            [
                "2026-04-15T10:00:00Z [devbench.backlog_manager] INFO Set E0-F1-S1-T1 to 'in-progress'",
                "2026-04-15T11:00:00Z [devbench.backlog_manager] INFO Set E0-F1-S1-T1 to 'done'",
            ],
        )
        shard_may_t2 = tmp_path / "logs" / "2026-05" / "E0-F1-S1-T2.jsonl"
        shard_may_t2.parent.mkdir(parents=True, exist_ok=True)
        _write_orch_log(
            shard_may_t2,
            [
                "2026-05-01T09:00:00Z [devbench.backlog_manager] INFO Set E0-F1-S1-T2 to 'in-progress'",
                "2026-05-01T09:30:00Z [devbench.backlog_manager] INFO Set E0-F1-S1-T2 to 'blocked'",
            ],
        )
        # Live flat log -- empty (operator hasn't started a new orchestrate
        # session post-migration yet).
        live_log = tmp_path / "logs" / "orchestrator.log"
        live_log.write_text("", encoding="utf-8")
        return live_log

    def test_refresh_orch_log_sources_reads_sharded_tree(self, tmp_path: Path) -> None:
        """The cache picks up events from every shard, not just the live log."""
        live_log = self._seed_post_migration_workspace(tmp_path)
        idx = EventIndex.open(tmp_path)
        try:
            idx.refresh_orch_log_sources(tmp_path, live_log)
            done_times = idx.task_transition_times_for_workspace(tmp_path, live_log, "done")
            assert "E0-F1-S1-T1" in done_times
            blocked_times = idx.task_transition_times_for_workspace(tmp_path, live_log, "blocked")
            assert "E0-F1-S1-T2" in blocked_times
        finally:
            idx.close()

    def test_refresh_orch_log_sources_merges_with_live_log(self, tmp_path: Path) -> None:
        """When the live log has post-migration events AND shards exist,
        both contribute to the union."""
        live_log = self._seed_post_migration_workspace(tmp_path)
        # New post-migration event lands in the live flat log.
        _write_orch_log(
            live_log,
            ["2026-05-10T10:00:00Z [devbench.backlog_manager] INFO Set E0-F1-S1-T3 to 'done'"],
        )

        idx = EventIndex.open(tmp_path)
        try:
            idx.refresh_orch_log_sources(tmp_path, live_log)
            done = idx.task_transition_times_for_workspace(tmp_path, live_log, "done")
            # Both the shard (T1 done) AND the live log (T3 done) appear.
            assert "E0-F1-S1-T1" in done
            assert "E0-F1-S1-T3" in done
        finally:
            idx.close()

    def test_workspace_queries_fall_back_to_single_file_when_no_shards(self, tmp_path: Path) -> None:
        """Pre-migration workspace (no shards): the workspace-aware path
        reads only from the live log, equivalent to the legacy single-
        file query helpers."""
        (tmp_path / ".devbench").mkdir()
        live_log = tmp_path / "logs" / "orchestrator.log"
        live_log.parent.mkdir(parents=True)
        _write_orch_log(
            live_log,
            [
                "2026-05-04T10:00:00Z [devbench.backlog_manager] INFO Set E0-F1-S1-T1 to 'done'",
            ],
        )
        idx = EventIndex.open(tmp_path)
        try:
            idx.refresh_orch_log_sources(tmp_path, live_log)
            via_workspace = idx.task_transition_times_for_workspace(tmp_path, live_log, "done")
            via_single = idx.task_transition_times(live_log, "done")
            assert via_workspace == via_single
            assert "E0-F1-S1-T1" in via_workspace
        finally:
            idx.close()

    def test_workspace_query_helpers_return_empty_for_empty_workspace(self, tmp_path: Path) -> None:
        """A brand-new workspace (no log, no shards) returns empty
        results from every workspace-aware query helper."""
        (tmp_path / ".devbench").mkdir()
        live_log = tmp_path / "logs" / "orchestrator.log"
        idx = EventIndex.open(tmp_path)
        try:
            idx.refresh_orch_log_sources(tmp_path, live_log)
            assert idx.task_transition_times_for_workspace(tmp_path, live_log, "done") == {}
            assert idx.all_log_timestamps_for_workspace(tmp_path, live_log) == []
            assert idx.non_noise_log_timestamps_for_workspace(tmp_path, live_log, "noise.logger") == []
        finally:
            idx.close()

    def test_all_log_timestamps_for_workspace_aggregates_chronologically(self, tmp_path: Path) -> None:
        """The workspace-aware variant returns timestamps from every
        source in ascending order."""
        live_log = self._seed_post_migration_workspace(tmp_path)
        idx = EventIndex.open(tmp_path)
        try:
            idx.refresh_orch_log_sources(tmp_path, live_log)
            timestamps = idx.all_log_timestamps_for_workspace(tmp_path, live_log)
            # 2 lines in April shard + 2 lines in May shard.
            assert len(timestamps) == 4
            assert timestamps == sorted(timestamps)
        finally:
            idx.close()

    def test_non_noise_log_timestamps_for_workspace_drops_noise(self, tmp_path: Path) -> None:
        """Noise logger filtering applies across the full union of sources."""
        (tmp_path / ".devbench").mkdir()
        shard = tmp_path / "logs" / "2026-05" / "E0-F1-S1-T1.jsonl"
        shard.parent.mkdir(parents=True)
        _write_orch_log(
            shard,
            [
                "2026-05-04T10:00:00Z [devbench.cli] INFO real event",
                "2026-05-04T10:01:00Z [noise.tick] INFO noise tick",
                "2026-05-04T10:02:00Z [devbench.cli] INFO another real event",
            ],
        )
        live_log = tmp_path / "logs" / "orchestrator.log"
        live_log.write_text("", encoding="utf-8")
        idx = EventIndex.open(tmp_path)
        try:
            idx.refresh_orch_log_sources(tmp_path, live_log)
            timestamps = idx.non_noise_log_timestamps_for_workspace(tmp_path, live_log, "noise.tick")
            # Two real events; noise tick dropped.
            assert len(timestamps) == 2
        finally:
            idx.close()


# ---------------------------------------------------------------------------
# Issue #223: per-model attribution in SQL index + per-model aggregators
# ---------------------------------------------------------------------------


class TestPerModelAttribution:
    """Issue #223 AC-1: every hook + transcript entry has its ``model``
    recorded in the SQL index so per-model aggregation can run.

    AC-5: bumping the schema version drops + rebuilds the index (this
    behaviour already existed for prior schema bumps; the test asserts
    the model column is present in the rebuilt schema, which is the
    operator-visible artefact of the v4 migration).
    """

    def test_hook_entries_table_has_model_column(self, workspace: Path) -> None:
        """AC-1: ``SELECT DISTINCT model FROM hook_entries`` returns
        the model attributions captured at parse time.
        """
        hook = workspace / "hook-logs.jsonl"
        _write_jsonl(
            hook,
            [
                {
                    "timestamp": "2026-05-04T10:00:00.000000+00:00",
                    "input": {
                        "tool_response": {
                            "model": "claude-sonnet-4-6",
                            "usage": {"input_tokens": 1000},
                        }
                    },
                },
                {
                    "timestamp": "2026-05-04T10:01:00.000000+00:00",
                    "input": {
                        "tool_response": {
                            "model": "claude-opus-4-7",
                            "usage": {"input_tokens": 2000},
                        }
                    },
                },
            ],
        )
        idx = EventIndex.open(workspace)
        try:
            idx.refresh_hook_log(hook)
            rows = idx._conn.execute("SELECT DISTINCT model FROM hook_entries ORDER BY model").fetchall()
            models = [row[0] for row in rows]
            assert models == ["claude-opus-4-7", "claude-sonnet-4-6"]
        finally:
            idx.close()

    def test_transcript_entries_table_has_model_column(self, workspace: Path, tmp_path: Path) -> None:
        """AC-1 mirror for transcript_entries -- ``message.model`` is
        captured at parse time and queryable.
        """
        from devbench.reporting.event_index import _KIND_TRANSCRIPT

        del _KIND_TRANSCRIPT  # imported to confirm module symbol exists
        transcript_dir = tmp_path / "transcripts"
        transcript_dir.mkdir()
        transcript_file = transcript_dir / "session.jsonl"
        _write_jsonl(
            transcript_file,
            [
                {
                    "timestamp": "2026-05-04T10:00:00.000000+00:00",
                    "type": "assistant",
                    "message": {
                        "id": "msg-abc",
                        "model": "claude-sonnet-4-6",
                        "usage": {"input_tokens": 1500},
                    },
                },
                {
                    "timestamp": "2026-05-04T10:01:00.000000+00:00",
                    "type": "assistant",
                    "message": {
                        "id": "msg-def",
                        "model": "claude-haiku-4-5",
                        "usage": {"input_tokens": 500},
                    },
                },
            ],
        )
        idx = EventIndex.open(workspace)
        try:
            idx.refresh_transcripts(transcript_dir)
            rows = idx._conn.execute("SELECT DISTINCT model FROM transcript_entries ORDER BY model").fetchall()
            models = [row[0] for row in rows]
            assert models == ["claude-haiku-4-5", "claude-sonnet-4-6"]
        finally:
            idx.close()

    def test_aggregate_hook_window_by_model_buckets_per_model(self, workspace: Path) -> None:
        """Per-model aggregation produces one totals dict per observed
        model id; the buckets sum back to the single-bucket aggregator's
        rollup (sanity check that we are not silently dropping tokens).
        """
        hook = workspace / "hook-logs.jsonl"
        _write_jsonl(
            hook,
            [
                {
                    "timestamp": "2026-05-04T10:00:00.000000+00:00",
                    "input": {
                        "tool_response": {
                            "model": "claude-sonnet-4-6",
                            "usage": {"input_tokens": 1000, "output_tokens": 200},
                        }
                    },
                },
                {
                    "timestamp": "2026-05-04T10:01:00.000000+00:00",
                    "input": {
                        "tool_response": {
                            "model": "claude-opus-4-7",
                            "usage": {"input_tokens": 3000, "output_tokens": 600},
                        }
                    },
                },
                {
                    "timestamp": "2026-05-04T10:02:00.000000+00:00",
                    "input": {
                        # No model attribution -- aggregates under "<unknown>".
                        "tool_response": {"usage": {"input_tokens": 500, "output_tokens": 100}},
                    },
                },
            ],
        )
        idx = EventIndex.open(workspace)
        try:
            idx.refresh_hook_log(hook)
            window_start = datetime(2026, 5, 4, 9, 0, tzinfo=UTC)
            by_model = idx.aggregate_hook_window_by_model(hook, window_start)
            assert set(by_model.keys()) == {"claude-sonnet-4-6", "claude-opus-4-7", "<unknown>"}
            assert by_model["claude-sonnet-4-6"]["input_tokens"] == 1000
            assert by_model["claude-opus-4-7"]["input_tokens"] == 3000
            assert by_model["<unknown>"]["input_tokens"] == 500
            # Roll-up sanity check.
            rollup = idx.aggregate_hook_window(hook, window_start)
            assert rollup["input_tokens"] == sum(b["input_tokens"] for b in by_model.values())
            assert rollup["output_tokens"] == sum(b["output_tokens"] for b in by_model.values())
        finally:
            idx.close()

    def test_aggregate_transcript_window_by_model_buckets_per_model(self, workspace: Path, tmp_path: Path) -> None:
        transcript_dir = tmp_path / "transcripts"
        transcript_dir.mkdir()
        transcript_file = transcript_dir / "session.jsonl"
        _write_jsonl(
            transcript_file,
            [
                {
                    "timestamp": "2026-05-04T10:00:00.000000+00:00",
                    "type": "assistant",
                    "message": {
                        "id": "msg-1",
                        "model": "claude-opus-4-7",
                        "usage": {"input_tokens": 5000, "output_tokens": 1000},
                    },
                },
                {
                    "timestamp": "2026-05-04T10:01:00.000000+00:00",
                    "type": "assistant",
                    "message": {
                        "id": "msg-2",
                        "model": "claude-sonnet-4-6",
                        "usage": {"input_tokens": 2000, "output_tokens": 400},
                    },
                },
            ],
        )
        idx = EventIndex.open(workspace)
        try:
            idx.refresh_transcripts(transcript_dir)
            window_start = datetime(2026, 5, 4, 9, 0, tzinfo=UTC)
            by_model = idx.aggregate_transcript_window_by_model(transcript_dir, window_start)
            assert set(by_model.keys()) == {"claude-opus-4-7", "claude-sonnet-4-6"}
            assert by_model["claude-opus-4-7"]["input_tokens"] == 5000
            assert by_model["claude-sonnet-4-6"]["input_tokens"] == 2000
        finally:
            idx.close()

    def test_schema_version_is_v4(self) -> None:
        """AC-5: bumping the schema version triggers a rebuild on next
        open.  The constant itself is the operator-visible artefact;
        existing prior-version tests in TestSchemaInitialisation already
        cover the rebuild-on-mismatch behaviour.
        """
        from devbench.reporting.event_index import _SCHEMA_VERSION

        assert _SCHEMA_VERSION >= 4, (
            f"Issue #223 bumps the schema to v4 to add the model column; got _SCHEMA_VERSION={_SCHEMA_VERSION}"
        )
