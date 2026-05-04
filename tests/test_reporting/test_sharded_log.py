"""Tests for ``devbench.reporting.sharded_log`` (issue #162 Phase 3, ADR-18).

Pins the partition contract (per-month + per-task), the destructive
migration's transactional fail-safe, the meta-shard fallback for non-
task-tagged records, and the rollback path (legacy archive).
"""

from __future__ import annotations

from pathlib import Path

import pytest

from devbench.reporting.sharded_log import (
    LEGACY_DIR_NAME,
    LEGACY_LOG_NAME,
    LOGS_DIR_NAME,
    META_SHARD_NAME,
    is_sharded_layout,
    iter_shard_paths,
    migrate_flat_to_sharded,
    read_shards,
)


class TestIsShardedLayout:
    def test_returns_false_when_no_logs_dir(self, tmp_path: Path) -> None:
        assert is_sharded_layout(tmp_path) is False

    def test_returns_false_when_logs_dir_empty(self, tmp_path: Path) -> None:
        (tmp_path / LOGS_DIR_NAME).mkdir()
        assert is_sharded_layout(tmp_path) is False

    def test_returns_false_when_only_flat_log_present(self, tmp_path: Path) -> None:
        logs_dir = tmp_path / LOGS_DIR_NAME
        logs_dir.mkdir()
        (logs_dir / "orchestrator.log").write_text("flat content")
        assert is_sharded_layout(tmp_path) is False

    def test_returns_true_when_yyyy_mm_directory_present(self, tmp_path: Path) -> None:
        (tmp_path / LOGS_DIR_NAME / "2026-05").mkdir(parents=True)
        assert is_sharded_layout(tmp_path) is True

    def test_ignores_non_yyyymm_directories(self, tmp_path: Path) -> None:
        """Non-YYYY-MM subdirectories under logs/ (e.g. logs/legacy/)
        don't constitute a sharded layout."""
        (tmp_path / LOGS_DIR_NAME / "legacy").mkdir(parents=True)
        (tmp_path / LOGS_DIR_NAME / "ci-failures").mkdir(parents=True)
        assert is_sharded_layout(tmp_path) is False


class TestMigrateFlatToSharded:
    def test_raises_when_log_missing(self, tmp_path: Path) -> None:
        with pytest.raises(FileNotFoundError):
            migrate_flat_to_sharded(tmp_path, tmp_path / "nope.log")

    def test_partitions_by_month_and_task(self, tmp_path: Path) -> None:
        log = tmp_path / LOGS_DIR_NAME / "orchestrator.log"
        log.parent.mkdir(parents=True)
        log.write_text(
            "2026-04-15T10:00:00Z [agent] INFO Set E0-F1-S1-T1 to 'in-progress' in both files\n"
            "2026-04-15T11:00:00Z [agent] INFO Set E0-F1-S1-T1 to 'done' in both files\n"
            "2026-05-01T10:00:00Z [agent] INFO Set E0-F1-S1-T2 to 'in-progress' in both files\n"
            "2026-05-02T10:00:00Z [agent] INFO Sweep finished; 0 transitions\n"
        )

        result = migrate_flat_to_sharded(tmp_path, log)

        assert result["lines_processed"] == 4
        assert result["shards_written"] == 2  # T1 in April, T2 in May
        assert result["meta_shards_written"] == 1  # the Sweep line in May

        # Verify shards by reading them back.
        april_t1 = tmp_path / LOGS_DIR_NAME / "2026-04" / "E0-F1-S1-T1.jsonl"
        may_t2 = tmp_path / LOGS_DIR_NAME / "2026-05" / "E0-F1-S1-T2.jsonl"
        may_meta = tmp_path / LOGS_DIR_NAME / "2026-05" / META_SHARD_NAME

        assert april_t1.is_file()
        assert may_t2.is_file()
        assert may_meta.is_file()
        assert "in-progress" in april_t1.read_text()
        assert "done" in april_t1.read_text()
        assert "in-progress" in may_t2.read_text()
        assert "Sweep" in may_meta.read_text()

    def test_archives_source_to_legacy(self, tmp_path: Path) -> None:
        log = tmp_path / LOGS_DIR_NAME / "orchestrator.log"
        log.parent.mkdir(parents=True)
        log.write_text("2026-04-15T10:00:00Z [agent] INFO Set E0-F1-S1-T1 to 'in-progress' in both files\n")

        migrate_flat_to_sharded(tmp_path, log)

        # Source removed; archive present.
        assert not log.exists()
        legacy = tmp_path / LEGACY_DIR_NAME / LEGACY_LOG_NAME
        assert legacy.is_file()
        assert "Set E0-F1-S1-T1" in legacy.read_text()

    def test_routes_non_task_id_to_meta_shard(self, tmp_path: Path) -> None:
        """Stories / Features / Epics share the E<...> ID prefix but
        their state is auto-rolled; route them to the meta shard so we
        don't generate a per-non-task shard."""
        log = tmp_path / LOGS_DIR_NAME / "orchestrator.log"
        log.parent.mkdir(parents=True)
        log.write_text(
            "2026-05-01T10:00:00Z [agent] INFO Set E0-F1-S1 to 'in-progress' in both files\n"
            "2026-05-01T10:00:30Z [agent] INFO Set E0-F1 to 'in-progress' in both files\n"
        )

        result = migrate_flat_to_sharded(tmp_path, log)

        assert result["shards_written"] == 0
        assert result["meta_shards_written"] >= 1
        meta_shard = tmp_path / LOGS_DIR_NAME / "2026-05" / META_SHARD_NAME
        assert meta_shard.is_file()
        assert "E0-F1-S1" in meta_shard.read_text()
        assert "E0-F1" in meta_shard.read_text()

    def test_handles_continuation_lines(self, tmp_path: Path) -> None:
        """A multi-line log record (timestamp on first line, continuation
        on subsequent lines) attaches to the most-recent bucket so the
        archive byte-faithfully preserves the original."""
        log = tmp_path / LOGS_DIR_NAME / "orchestrator.log"
        log.parent.mkdir(parents=True)
        log.write_text(
            "2026-05-01T10:00:00Z [agent] INFO Set E0-F1-S1-T1 to 'done' in both files\n"
            "    continuation line 1\n"
            "    continuation line 2\n"
        )

        migrate_flat_to_sharded(tmp_path, log)

        shard = tmp_path / LOGS_DIR_NAME / "2026-05" / "E0-F1-S1-T1.jsonl"
        assert shard.is_file()
        content = shard.read_text()
        assert "continuation line 1" in content
        assert "continuation line 2" in content

    def test_handles_trailing_untimestamped_lines(self, tmp_path: Path) -> None:
        """When the log starts with timestamped records but ends with
        untimestamped tail (operator hand-edited / corrupt last bytes),
        the trailing lines route to the latest month's meta shard so
        nothing is silently dropped."""
        log = tmp_path / LOGS_DIR_NAME / "orchestrator.log"
        log.parent.mkdir(parents=True)
        log.write_text(
            "2026-05-01T10:00:00Z [agent] INFO Set E0-F1-S1-T1 to 'done' in both files\nuntimestamped tail\n"
        )

        migrate_flat_to_sharded(tmp_path, log)

        # Trailing untimestamped line attaches to the previous bucket
        # because the iterator first appends it as untimestamped, then
        # gets routed when no further bucket exists.
        shard = tmp_path / LOGS_DIR_NAME / "2026-05" / "E0-F1-S1-T1.jsonl"
        meta = tmp_path / LOGS_DIR_NAME / "2026-05" / META_SHARD_NAME
        # The tail must show up SOMEWHERE in the sharded tree (both
        # placements are acceptable for a fail-safe migration).
        all_content = ""
        if shard.exists():
            all_content += shard.read_text()
        if meta.exists():
            all_content += meta.read_text()
        assert "untimestamped tail" in all_content

    def test_handles_log_starting_with_untimestamped_lines(self, tmp_path: Path) -> None:
        """When the log starts with untimestamped continuation lines
        (operator hand-edited / corrupt prefix), the queued leading
        lines prepend the first real bucket so the byte sequence is
        preserved across migration."""
        log = tmp_path / LOGS_DIR_NAME / "orchestrator.log"
        log.parent.mkdir(parents=True)
        log.write_text(
            "leading line 1 (no timestamp)\n"
            "leading line 2 (no timestamp)\n"
            "2026-05-01T10:00:00Z [agent] INFO Set E0-F1-S1-T1 to 'done' in both files\n"
        )

        migrate_flat_to_sharded(tmp_path, log)

        shard = tmp_path / LOGS_DIR_NAME / "2026-05" / "E0-F1-S1-T1.jsonl"
        assert shard.is_file()
        content = shard.read_text()
        assert "leading line 1" in content
        assert "leading line 2" in content
        assert "Set E0-F1-S1-T1" in content

    def test_handles_log_with_only_untimestamped_lines(self, tmp_path: Path) -> None:
        """A log with no timestamped records at all -- defensive case
        for a freshly-truncated or corrupt log. The migration archives
        the source and writes no shards."""
        log = tmp_path / LOGS_DIR_NAME / "orchestrator.log"
        log.parent.mkdir(parents=True)
        log.write_text("no timestamp here\nor here either\n")

        result = migrate_flat_to_sharded(tmp_path, log)

        assert result["lines_processed"] == 0
        assert result["shards_written"] == 0
        # Source still archived (not silently lost).
        legacy = tmp_path / LEGACY_DIR_NAME / LEGACY_LOG_NAME
        assert legacy.is_file()
        assert "no timestamp here" in legacy.read_text()

    def test_idempotent_rerun_appends(self, tmp_path: Path) -> None:
        """Re-running the migration appends new lines into existing
        shards. Operators can periodically re-run to absorb new
        accumulation into the sharded tree."""
        # First migration.
        log = tmp_path / LOGS_DIR_NAME / "orchestrator.log"
        log.parent.mkdir(parents=True)
        log.write_text("2026-05-01T10:00:00Z [agent] INFO Set E0-F1-S1-T1 to 'done' in both files\n")
        migrate_flat_to_sharded(tmp_path, log)

        # New flat log accumulates.
        log.write_text("2026-05-15T10:00:00Z [agent] INFO Set E0-F1-S1-T1 to 'blocked' in both files\n")
        migrate_flat_to_sharded(tmp_path, log)

        shard = tmp_path / LOGS_DIR_NAME / "2026-05" / "E0-F1-S1-T1.jsonl"
        content = shard.read_text()
        # Both transitions present.
        assert "done" in content
        assert "blocked" in content


class TestIterShardPaths:
    def test_yields_nothing_when_logs_dir_missing(self, tmp_path: Path) -> None:
        assert list(iter_shard_paths(tmp_path)) == []

    def test_yields_in_chronological_order(self, tmp_path: Path) -> None:
        log = tmp_path / LOGS_DIR_NAME / "orchestrator.log"
        log.parent.mkdir(parents=True)
        log.write_text(
            "2026-04-15T10:00:00Z [agent] INFO Set E0-F1-S1-T1 to 'done' in both files\n"
            "2026-05-01T10:00:00Z [agent] INFO Set E0-F1-S1-T2 to 'done' in both files\n"
            "2026-06-01T10:00:00Z [agent] INFO Set E0-F1-S1-T3 to 'done' in both files\n"
        )
        migrate_flat_to_sharded(tmp_path, log)

        paths = list(iter_shard_paths(tmp_path))
        # Months sort lexically; April -> May -> June.
        months = [p.parent.name for p in paths]
        assert months == sorted(months)


class TestReadShards:
    def test_yields_empty_when_no_shards(self, tmp_path: Path) -> None:
        assert list(read_shards(tmp_path)) == []

    def test_yields_lines_in_chronological_order(self, tmp_path: Path) -> None:
        log = tmp_path / LOGS_DIR_NAME / "orchestrator.log"
        log.parent.mkdir(parents=True)
        log.write_text(
            "2026-04-15T10:00:00Z [agent] INFO Set E0-F1-S1-T1 to 'done' in both files\n"
            "2026-05-01T10:00:00Z [agent] INFO Set E0-F1-S1-T2 to 'done' in both files\n"
        )
        migrate_flat_to_sharded(tmp_path, log)

        lines = list(read_shards(tmp_path))
        assert len(lines) == 2
        # April's shard comes before May's.
        assert "2026-04" in lines[0]
        assert "2026-05" in lines[1]
