"""Unit tests for ``devbench.watchdog``.

Covers the three pure functions (``find_in_progress_task``,
``last_orchestrator_log_ts``, ``detect_stuck``) plus the file writer
(``write_flag_file``). All tests use ``tmp_path`` and inject ``now``
explicitly so they are deterministic.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from devbench.watchdog import (
    StuckDetection,
    detect_stuck,
    find_in_progress_task,
    last_orchestrator_log_ts,
    write_flag_file,
)

_IN_PROGRESS_SAMPLE = """# Backlog

## Status Summary

| Epic | Title | Done | In Progress | In Queue |
|------|-------|------|-------------|----------|
| E1   | Test  | 10   | 1           | 5        |

## Full Work Unit Index

| ID | Title | Type | Status | Dependencies | Repo | File Path |
|----|-------|------|--------|-------------|------|-----------|
| E0-F1-S1-T1 | done one | Task | done | None | r/r | `backlog/E0/E0-F1/E0-F1-S1/E0-F1-S1-T1.md` |
| E1-F2-S26-T3 | upload errors | Task | in-progress | E1-F2-S26 | r/r | `backlog/E1/E1-F2/E1-F2-S26/E1-F2-S26-T3.md` |
| E1-F2-S26-T4 | flags | Task | in-queue | E1-F2-S26-T3 | r/r | `backlog/E1/E1-F2/E1-F2-S26/E1-F2-S26-T4.md` |
"""

_NO_IN_PROGRESS_SAMPLE = """| ID | Title | Type | Status | Dependencies | Repo | File Path |
|----|-------|------|--------|-------------|------|-----------|
| E0-T1 | done | Task | done | None | r/r | `backlog/E0/t1.md` |
| E0-T2 | queued | Task | in-queue | None | r/r | `backlog/E0/t2.md` |
"""

_LOG_SAMPLE = """2026-04-22T19:49:33Z [devbench.cli] INFO TDD REFACTOR entry logged
2026-04-22T20:02:50Z [devbench.cli] INFO agent/executor comment
2026-04-22T20:08:01Z [devbench.cli] INFO [USER] Stop hook blocked (1/5): review fail
"""


@pytest.mark.unit
class TestFindInProgressTask:
    def test_returns_id_and_path_for_in_progress_row(self, tmp_path: Path) -> None:
        index = tmp_path / "BACKLOG.md"
        index.write_text(_IN_PROGRESS_SAMPLE)
        result = find_in_progress_task(index)
        assert result == (
            "E1-F2-S26-T3",
            "backlog/E1/E1-F2/E1-F2-S26/E1-F2-S26-T3.md",
        )

    def test_returns_none_when_no_in_progress(self, tmp_path: Path) -> None:
        index = tmp_path / "BACKLOG.md"
        index.write_text(_NO_IN_PROGRESS_SAMPLE)
        assert find_in_progress_task(index) is None

    def test_returns_none_when_file_missing(self, tmp_path: Path) -> None:
        assert find_in_progress_task(tmp_path / "nope.md") is None

    def test_returns_first_match_when_multiple_in_progress(self, tmp_path: Path) -> None:
        index = tmp_path / "BACKLOG.md"
        index.write_text(
            "| ID | Title | Type | Status | Dependencies | Repo | File Path |\n"
            "|----|-------|------|--------|-------------|------|-----------|\n"
            "| E1-A | a | Task | in-progress | None | r/r | `p/a.md` |\n"
            "| E1-B | b | Task | in-progress | None | r/r | `p/b.md` |\n"
        )
        result = find_in_progress_task(index)
        assert result == ("E1-A", "p/a.md")


@pytest.mark.unit
class TestLastOrchestratorLogTs:
    def test_returns_last_timestamp_from_file(self, tmp_path: Path) -> None:
        log = tmp_path / "orchestrator.log"
        log.write_text(_LOG_SAMPLE)
        result = last_orchestrator_log_ts(log)
        assert result == datetime(2026, 4, 22, 20, 8, 1, tzinfo=UTC)

    def test_returns_none_when_file_missing(self, tmp_path: Path) -> None:
        assert last_orchestrator_log_ts(tmp_path / "nope.log") is None

    def test_returns_none_when_file_has_no_timestamps(self, tmp_path: Path) -> None:
        log = tmp_path / "orchestrator.log"
        log.write_text("no timestamp lines here\njust noise\n")
        assert last_orchestrator_log_ts(log) is None

    def test_only_scans_tail_bytes(self, tmp_path: Path) -> None:
        """Earlier timestamps outside the tail window must be ignored."""
        log = tmp_path / "orchestrator.log"
        early = "2020-01-01T00:00:00Z [old] INFO ancient\n"
        padding = "x" * 10_000 + "\n"
        recent = "2026-04-22T20:08:01Z [devbench.cli] INFO recent\n"
        log.write_text(early + padding + recent)
        result = last_orchestrator_log_ts(log, tail_bytes=1024)
        assert result == datetime(2026, 4, 22, 20, 8, 1, tzinfo=UTC)


@pytest.mark.unit
class TestDetectStuck:
    def _setup(self, tmp_path: Path, *, in_progress: bool, log_last: datetime | None) -> tuple[Path, Path]:
        index = tmp_path / "BACKLOG.md"
        index.write_text(_IN_PROGRESS_SAMPLE if in_progress else _NO_IN_PROGRESS_SAMPLE)
        log = tmp_path / "orchestrator.log"
        if log_last is not None:
            log.write_text(f"{log_last.strftime('%Y-%m-%dT%H:%M:%S')}Z [devbench.cli] INFO line\n")
        return index, log

    def test_no_in_progress_returns_healthy(self, tmp_path: Path) -> None:
        index, log = self._setup(tmp_path, in_progress=False, log_last=None)
        now = datetime(2026, 4, 22, 21, 0, 0, tzinfo=UTC)
        result = detect_stuck(
            backlog_index=index,
            log_file=log,
            now=now,
            idle_threshold_seconds=300,
            stale_task_minutes=120,
        )
        assert result.stuck is None
        assert "no in-progress" in result.reason

    def test_in_progress_with_recent_log_returns_healthy(self, tmp_path: Path) -> None:
        now = datetime(2026, 4, 22, 20, 10, 0, tzinfo=UTC)
        index, log = self._setup(tmp_path, in_progress=True, log_last=now - timedelta(seconds=30))
        result = detect_stuck(
            backlog_index=index,
            log_file=log,
            now=now,
            idle_threshold_seconds=300,
            stale_task_minutes=120,
        )
        assert result.stuck is None
        assert "active" in result.reason
        assert "30s" in result.reason

    def test_in_progress_with_stale_log_returns_stuck(self, tmp_path: Path) -> None:
        now = datetime(2026, 4, 22, 21, 0, 0, tzinfo=UTC)
        last_ts = now - timedelta(minutes=10)
        index, log = self._setup(tmp_path, in_progress=True, log_last=last_ts)
        result = detect_stuck(
            backlog_index=index,
            log_file=log,
            now=now,
            idle_threshold_seconds=300,
            stale_task_minutes=120,
        )
        assert result.stuck is not None
        assert result.stuck.task_id == "E1-F2-S26-T3"
        assert result.stuck.idle_seconds == 600
        assert result.stuck.idle_threshold_seconds == 300
        assert result.stuck.stale_minutes_threshold == 120
        assert result.stuck.orchestrator_log_last_ts == last_ts

    def test_in_progress_with_missing_log_returns_stuck(self, tmp_path: Path) -> None:
        index = tmp_path / "BACKLOG.md"
        index.write_text(_IN_PROGRESS_SAMPLE)
        now = datetime(2026, 4, 22, 21, 0, 0, tzinfo=UTC)
        result = detect_stuck(
            backlog_index=index,
            log_file=tmp_path / "missing.log",
            now=now,
            idle_threshold_seconds=300,
            stale_task_minutes=120,
        )
        assert result.stuck is not None
        assert result.stuck.idle_seconds == 300
        assert result.stuck.orchestrator_log_last_ts is None


@pytest.mark.unit
class TestWriteFlagFile:
    def test_writes_json_with_all_fields(self, tmp_path: Path) -> None:
        flag = tmp_path / ".devbench" / "needs-restart.flag"
        stuck = StuckDetection(
            task_id="E1-T1",
            task_file_path="backlog/p/E1-T1.md",
            orchestrator_log_last_ts=datetime(2026, 4, 22, 20, 8, 1, tzinfo=UTC),
            idle_seconds=600,
            stale_minutes_threshold=120,
            idle_threshold_seconds=300,
        )
        now = datetime(2026, 4, 22, 21, 0, 0, tzinfo=UTC)
        write_flag_file(flag, stuck, now)
        payload = json.loads(flag.read_text())
        assert payload == {
            "ts": "2026-04-22T21:00:00Z",
            "task_id": "E1-T1",
            "task_file_path": "backlog/p/E1-T1.md",
            "orchestrator_idle_seconds": 600,
            "last_orchestrator_log_ts": "2026-04-22T20:08:01Z",
            "idle_threshold_seconds": 300,
            "stale_task_minutes_threshold": 120,
        }

    def test_writes_null_log_ts_when_absent(self, tmp_path: Path) -> None:
        flag = tmp_path / "flag.json"
        stuck = StuckDetection(
            task_id="E1-T1",
            task_file_path="p.md",
            orchestrator_log_last_ts=None,
            idle_seconds=300,
            stale_minutes_threshold=120,
            idle_threshold_seconds=300,
        )
        write_flag_file(flag, stuck, datetime(2026, 4, 22, 21, 0, 0, tzinfo=UTC))
        payload = json.loads(flag.read_text())
        assert payload["last_orchestrator_log_ts"] is None

    def test_atomic_replace_creates_parent_dirs(self, tmp_path: Path) -> None:
        flag = tmp_path / "nested" / "deeper" / "flag.json"
        stuck = StuckDetection(
            task_id="E1-T1",
            task_file_path="p.md",
            orchestrator_log_last_ts=None,
            idle_seconds=300,
            stale_minutes_threshold=120,
            idle_threshold_seconds=300,
        )
        write_flag_file(flag, stuck, datetime(2026, 4, 22, 21, 0, 0, tzinfo=UTC))
        assert flag.is_file()
        assert not flag.with_suffix(flag.suffix + ".tmp").exists()

    def test_overwrites_existing_flag(self, tmp_path: Path) -> None:
        flag = tmp_path / "flag.json"
        flag.write_text('{"old": true}\n')
        stuck = StuckDetection(
            task_id="E1-NEW",
            task_file_path="p.md",
            orchestrator_log_last_ts=None,
            idle_seconds=300,
            stale_minutes_threshold=120,
            idle_threshold_seconds=300,
        )
        write_flag_file(flag, stuck, datetime(2026, 4, 22, 21, 0, 0, tzinfo=UTC))
        payload = json.loads(flag.read_text())
        assert payload["task_id"] == "E1-NEW"
