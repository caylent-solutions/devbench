"""Tests for ``devbench.reporting.window_stats`` (issue #162 Phase 2, ADR-17).

Pins the per-task aggregate write contract, atomic-write semantics,
schema-version invalidation, and the rebuild-from-log parity guarantee.
"""

from __future__ import annotations

import json as _json
from datetime import UTC, datetime
from pathlib import Path

from devbench.reporting.window_stats import (
    WINDOW_STATS_DIR_NAME,
    TaskAggregate,
    TransitionEvent,
    aggregate_dir,
    aggregate_path,
    read_aggregate,
    rebuild_from_log,
    update_aggregate,
)


class TestAggregatePath:
    def test_dir_under_dotdevbench(self, tmp_path: Path) -> None:
        assert aggregate_dir(tmp_path) == tmp_path / WINDOW_STATS_DIR_NAME

    def test_path_uses_task_id_filename(self, tmp_path: Path) -> None:
        path = aggregate_path(tmp_path, "E0-F1-S1-T1")
        assert path.name == "E0-F1-S1-T1.json"
        assert path.parent == aggregate_dir(tmp_path)


class TestUpdateAggregate:
    def test_creates_directory_if_missing(self, tmp_path: Path) -> None:
        update_aggregate(tmp_path, "E0-F1-S1-T1", "in-progress", datetime(2026, 5, 4, tzinfo=UTC))
        assert aggregate_dir(tmp_path).is_dir()

    def test_first_transition_creates_file(self, tmp_path: Path) -> None:
        ts = datetime(2026, 5, 4, 12, 30, 45, tzinfo=UTC)
        path = update_aggregate(tmp_path, "E0-F1-S1-T1", "in-progress", ts)
        assert path == aggregate_path(tmp_path, "E0-F1-S1-T1")
        assert path.is_file()
        payload = _json.loads(path.read_text())
        assert payload["schema_version"] == 1
        assert payload["task_id"] == "E0-F1-S1-T1"
        assert payload["transitions"] == [{"timestamp_iso": "2026-05-04T12:30:45Z", "new_status": "in-progress"}]

    def test_second_transition_appends_to_history(self, tmp_path: Path) -> None:
        update_aggregate(tmp_path, "E0-F1-S1-T1", "in-progress", datetime(2026, 5, 4, 10, tzinfo=UTC))
        update_aggregate(tmp_path, "E0-F1-S1-T1", "done", datetime(2026, 5, 4, 11, tzinfo=UTC))
        agg = read_aggregate(tmp_path, "E0-F1-S1-T1")
        assert agg is not None
        assert [t.new_status for t in agg.transitions] == ["in-progress", "done"]

    def test_atomic_replace_no_leftover_tmp(self, tmp_path: Path) -> None:
        update_aggregate(tmp_path, "E0-F1-S1-T1", "in-progress", datetime.now(UTC))
        update_aggregate(tmp_path, "E0-F1-S1-T1", "done", datetime.now(UTC))
        leftover = aggregate_path(tmp_path, "E0-F1-S1-T1").with_suffix(".json.tmp")
        assert not leftover.exists()


class TestReadAggregate:
    def test_returns_none_when_file_missing(self, tmp_path: Path) -> None:
        assert read_aggregate(tmp_path, "E0-F1-S1-T1") is None

    def test_returns_none_on_corrupt_json(self, tmp_path: Path) -> None:
        path = aggregate_path(tmp_path, "E0-F1-S1-T1")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("{not valid")
        assert read_aggregate(tmp_path, "E0-F1-S1-T1") is None

    def test_returns_none_on_schema_version_mismatch(self, tmp_path: Path) -> None:
        path = aggregate_path(tmp_path, "E0-F1-S1-T1")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(_json.dumps({"schema_version": 999, "task_id": "E0-F1-S1-T1", "transitions": []}))
        assert read_aggregate(tmp_path, "E0-F1-S1-T1") is None

    def test_returns_none_on_wrong_root_type(self, tmp_path: Path) -> None:
        """A JSON list at the root (not a dict) is invalid; defensive read returns None."""
        path = aggregate_path(tmp_path, "E0-F1-S1-T1")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(_json.dumps([]))
        assert read_aggregate(tmp_path, "E0-F1-S1-T1") is None

    def test_returns_none_on_missing_required_field(self, tmp_path: Path) -> None:
        path = aggregate_path(tmp_path, "E0-F1-S1-T1")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(_json.dumps({"schema_version": 1, "task_id": "E0-F1-S1-T1"}))
        assert read_aggregate(tmp_path, "E0-F1-S1-T1") is None

    def test_returns_none_on_wrong_field_type(self, tmp_path: Path) -> None:
        path = aggregate_path(tmp_path, "E0-F1-S1-T1")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(_json.dumps({"schema_version": 1, "task_id": 42, "transitions": []}))
        assert read_aggregate(tmp_path, "E0-F1-S1-T1") is None

    def test_returns_none_on_wrong_transitions_shape(self, tmp_path: Path) -> None:
        path = aggregate_path(tmp_path, "E0-F1-S1-T1")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            _json.dumps(
                {
                    "schema_version": 1,
                    "task_id": "E0-F1-S1-T1",
                    "transitions": ["not a dict"],
                }
            )
        )
        assert read_aggregate(tmp_path, "E0-F1-S1-T1") is None

    def test_returns_none_on_wrong_transition_field_type(self, tmp_path: Path) -> None:
        path = aggregate_path(tmp_path, "E0-F1-S1-T1")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            _json.dumps(
                {
                    "schema_version": 1,
                    "task_id": "E0-F1-S1-T1",
                    "transitions": [{"timestamp_iso": "x", "new_status": 5}],
                }
            )
        )
        assert read_aggregate(tmp_path, "E0-F1-S1-T1") is None


class TestRebuildFromLog:
    def test_returns_zero_when_log_missing(self, tmp_path: Path) -> None:
        assert rebuild_from_log(tmp_path, tmp_path / "nope.log") == 0

    def test_walks_log_and_writes_one_file_per_task(self, tmp_path: Path) -> None:
        log = tmp_path / "log"
        log.write_text(
            "2026-05-04T10:00:00Z [agent] INFO Set E0-F1-S1-T1 to 'in-progress' in both files\n"
            "2026-05-04T10:30:00Z [agent] INFO Set E0-F1-S1-T1 to 'done' in both files\n"
            "2026-05-04T11:00:00Z [agent] INFO Set E0-F1-S1-T2 to 'blocked' in both files\n"
        )
        count = rebuild_from_log(tmp_path, log)
        assert count == 2
        agg1 = read_aggregate(tmp_path, "E0-F1-S1-T1")
        assert agg1 is not None
        assert [t.new_status for t in agg1.transitions] == ["in-progress", "done"]
        agg2 = read_aggregate(tmp_path, "E0-F1-S1-T2")
        assert agg2 is not None
        assert [t.new_status for t in agg2.transitions] == ["blocked"]

    def test_skips_non_task_ids(self, tmp_path: Path) -> None:
        """Story / Feature / Epic ids match the regex's E<...> prefix but
        their state is auto-rolled; skip them in window-stats."""
        log = tmp_path / "log"
        log.write_text(
            "2026-05-04T10:00:00Z [agent] INFO Set E0-F1-S1 to 'in-progress' in both files\n"
            "2026-05-04T10:30:00Z [agent] INFO Set E0-F1 to 'in-progress' in both files\n"
            "2026-05-04T11:00:00Z [agent] INFO Set E0-F1-S1-T1 to 'done' in both files\n"
        )
        count = rebuild_from_log(tmp_path, log)
        assert count == 1
        assert read_aggregate(tmp_path, "E0-F1-S1") is None
        assert read_aggregate(tmp_path, "E0-F1") is None
        assert read_aggregate(tmp_path, "E0-F1-S1-T1") is not None

    def test_idempotent_rerun_overwrites(self, tmp_path: Path) -> None:
        """Re-running rebuild on the same log produces the same aggregates."""
        log = tmp_path / "log"
        log.write_text(
            "2026-05-04T10:00:00Z [agent] INFO Set E0-F1-S1-T1 to 'in-progress' in both files\n"
            "2026-05-04T10:30:00Z [agent] INFO Set E0-F1-S1-T1 to 'done' in both files\n"
        )
        rebuild_from_log(tmp_path, log)
        rebuild_from_log(tmp_path, log)
        agg = read_aggregate(tmp_path, "E0-F1-S1-T1")
        assert agg is not None
        # Idempotent: each rebuild overwrites the file with one entry per
        # log line, so two log lines give two aggregate entries (not four).
        assert len(agg.transitions) == 2


class TestSetStatusHook:
    """Issue #162 Phase 2 wires per-task aggregate updates into
    ``BacklogManager._set_status``. Test asserts the hook fires."""

    def test_set_status_writes_aggregate(self, tmp_path: Path) -> None:
        """Synthetic backlog with one task; manager._set_status fires the
        update_aggregate hook on every transition."""
        from devbench.backlog.manager import BacklogManager

        # Minimal backlog: one work-unit file + one BACKLOG.md row.
        work_unit_dir = tmp_path / "backlog" / "E0" / "E0-F1" / "E0-F1-S1"
        work_unit_dir.mkdir(parents=True)
        unit_file = work_unit_dir / "E0-F1-S1-T1.md"
        unit_file.write_text(
            "# E0-F1-S1-T1: Test\n\n## Status: in-queue\n\n"
            "## Description\n\nx\n\n"
            "## Dependencies\n\n| ID | Title | Status |\n|----|-------|--------|\n| none | | |\n"
        )
        backlog_index = tmp_path / "BACKLOG.md"
        backlog_index.write_text(
            "# Backlog\n\n## Status Summary\n\n"
            "| Epic | Title | Done | In Progress | In Queue | Blocked |\n"
            "|------|-------|------|-------------|----------|---------|\n"
            "| E0 | x | 0 | 0 | 1 | 0 |\n\n"
            "## Full Work Unit Index\n\n"
            "| ID | Title | Type | Status | Dependencies | Repo | File Path |\n"
            "|----|-------|------|--------|--------------|------|-----------|\n"
            "| E0-F1-S1-T1 | Test | Task | in-queue | None | r | `backlog/E0/E0-F1/E0-F1-S1/E0-F1-S1-T1.md` |\n"
        )

        mgr = BacklogManager()
        mgr._set_status(unit_file, backlog_index, "E0-F1-S1-T1", "in-progress")

        agg = read_aggregate(tmp_path, "E0-F1-S1-T1")
        assert agg is not None
        assert agg.task_id == "E0-F1-S1-T1"
        assert len(agg.transitions) == 1
        assert agg.transitions[0].new_status == "in-progress"

    def test_set_status_skips_non_task_units(self, tmp_path: Path) -> None:
        """Story / Feature / Epic transitions must NOT write a per-task
        aggregate (those units have no -T<N> segment)."""
        from devbench.backlog.manager import BacklogManager

        # Minimal backlog with a Story unit (no task).
        story_dir = tmp_path / "backlog" / "E0" / "E0-F1"
        story_dir.mkdir(parents=True)
        story_file = story_dir / "E0-F1-S1.md"
        story_file.write_text(
            "# E0-F1-S1: Story\n\n## Status: in-queue\n\n"
            "## Description\n\nx\n\n"
            "## Dependencies\n\n| ID | Title | Status |\n|----|-------|--------|\n| none | | |\n"
        )
        backlog_index = tmp_path / "BACKLOG.md"
        backlog_index.write_text(
            "# Backlog\n\n## Status Summary\n\n"
            "| Epic | Title | Done | In Progress | In Queue | Blocked |\n"
            "|------|-------|------|-------------|----------|---------|\n"
            "| E0 | x | 0 | 0 | 1 | 0 |\n\n"
            "## Full Work Unit Index\n\n"
            "| ID | Title | Type | Status | Dependencies | Repo | File Path |\n"
            "|----|-------|------|--------|--------------|------|-----------|\n"
            "| E0-F1-S1 | Story | Story | in-queue | None | r | `backlog/E0/E0-F1/E0-F1-S1.md` |\n"
        )

        mgr = BacklogManager()
        mgr._set_status(story_file, backlog_index, "E0-F1-S1", "in-progress")

        # No aggregate file should exist for the story.
        assert read_aggregate(tmp_path, "E0-F1-S1") is None
        assert not aggregate_dir(tmp_path).exists() or not any(aggregate_dir(tmp_path).iterdir())


class TestDataclasses:
    """Sanity tests for the dataclass shapes themselves."""

    def test_transition_event_fields(self) -> None:
        ev = TransitionEvent(timestamp_iso="2026-05-04T10:00:00Z", new_status="done")
        assert ev.timestamp_iso == "2026-05-04T10:00:00Z"
        assert ev.new_status == "done"

    def test_task_aggregate_default_fields(self) -> None:
        agg = TaskAggregate(task_id="E0-F1-S1-T1")
        assert agg.task_id == "E0-F1-S1-T1"
        assert agg.transitions == []
        assert agg.schema_version == 1
