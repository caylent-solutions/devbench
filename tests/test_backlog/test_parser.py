"""Tests for judges.backlog_parser module."""

from __future__ import annotations

from pathlib import Path

import pytest

from devbench.backlog.parser import BacklogParser
from devbench.backlog.work_unit import WorkUnit, WorkUnitStatus, WorkUnitType


class TestParseIndex:
    """Test parse_index returns WorkUnit list from BACKLOG.md."""

    def test_parse_index_from_actual_backlog(self) -> None:
        """Parse the real BACKLOG.md file and verify results."""
        actual_backlog = Path("/workspaces/general-agent-env/BACKLOG.md")
        if not actual_backlog.is_file():
            pytest.skip("Actual BACKLOG.md not found")

        parser = BacklogParser(
            backlog_root=Path("/workspaces/general-agent-env/backlog"),
            backlog_index=actual_backlog,
        )
        units = parser.parse_index()

        assert len(units) > 0
        # Every parsed unit should have a non-empty id and title
        for unit in units:
            assert unit.id, f"Unit has empty id: {unit}"
            assert unit.title, f"Unit has empty title: {unit}"
            assert isinstance(unit.status, WorkUnitStatus)
            assert isinstance(unit.unit_type, WorkUnitType)

    def test_parse_index_from_mock(self, mock_backlog_index: Path) -> None:
        parser = BacklogParser(
            backlog_root=mock_backlog_index.parent,
            backlog_index=mock_backlog_index,
        )
        units = parser.parse_index()

        # The mock has 3 Task rows, 1 Story row, 1 Feature row
        task_units = [u for u in units if u.unit_type is WorkUnitType.TASK]
        assert len(task_units) == 3

        story_units = [u for u in units if u.unit_type is WorkUnitType.STORY]
        assert len(story_units) == 1

        feature_units = [u for u in units if u.unit_type is WorkUnitType.FEATURE]
        assert len(feature_units) == 1

    def test_parse_index_raises_file_not_found(self, tmp_path: Path) -> None:
        parser = BacklogParser(
            backlog_root=tmp_path,
            backlog_index=tmp_path / "nonexistent.md",
        )
        with pytest.raises(FileNotFoundError, match="Backlog index not found"):
            parser.parse_index()

    def test_parse_index_raises_when_no_rows(self, tmp_path: Path) -> None:
        empty_index = tmp_path / "BACKLOG.md"
        empty_index.write_text("# Backlog\n\nNo table here.\n")

        parser = BacklogParser(backlog_root=tmp_path, backlog_index=empty_index)
        with pytest.raises(ValueError, match="No work-unit rows found"):
            parser.parse_index()


class TestParseWorkUnitFile:
    """Test parse_work_unit_file parses a sample .md file correctly."""

    def test_parse_work_unit_file(self, tmp_work_unit_file: Path) -> None:
        parser = BacklogParser(
            backlog_root=tmp_work_unit_file.parent,
            backlog_index=tmp_work_unit_file.parent / "BACKLOG.md",
        )
        wu = parser.parse_work_unit_file(tmp_work_unit_file)

        assert wu.id == "E0-F1-S1-T1"
        assert wu.title == "Create Test Makefile"
        assert wu.status is WorkUnitStatus.IN_QUEUE
        assert wu.unit_type is WorkUnitType.TASK
        assert wu.repo == "caylent-solutions/git-repo"
        assert "E0-F1-S1" in wu.dependencies

    def test_parse_work_unit_file_raises_file_not_found(self, tmp_path: Path) -> None:
        parser = BacklogParser(backlog_root=tmp_path, backlog_index=tmp_path / "B.md")
        with pytest.raises(FileNotFoundError, match="Work-unit file not found"):
            parser.parse_work_unit_file(tmp_path / "nonexistent.md")

    def test_parse_work_unit_file_raises_when_no_heading(self, tmp_path: Path) -> None:
        bad_file = tmp_path / "bad.md"
        bad_file.write_text("No heading here.\n## Status: In Queue\n")

        parser = BacklogParser(backlog_root=tmp_path, backlog_index=tmp_path / "B.md")
        with pytest.raises(ValueError, match="No top-level heading"):
            parser.parse_work_unit_file(bad_file)

    def test_parse_work_unit_file_raises_when_no_colon_in_heading(self, tmp_path: Path) -> None:
        bad_file = tmp_path / "bad.md"
        bad_file.write_text("# NoColonHere\n## Status: In Queue\n")

        parser = BacklogParser(backlog_root=tmp_path, backlog_index=tmp_path / "B.md")
        with pytest.raises(ValueError, match="does not contain"):
            parser.parse_work_unit_file(bad_file)

    def test_parse_work_unit_extracts_acceptance_criteria(self, tmp_work_unit_file: Path) -> None:
        parser = BacklogParser(
            backlog_root=tmp_work_unit_file.parent,
            backlog_index=tmp_work_unit_file.parent / "BACKLOG.md",
        )
        wu = parser.parse_work_unit_file(tmp_work_unit_file)

        assert len(wu.acceptance_criteria) >= 1
        assert any("AC-FUNC-001" in ac for ac in wu.acceptance_criteria)


class TestFindNextActionable:
    """Test find_next_actionable returns correct unit based on status and dependencies."""

    def _make_units(self) -> list[WorkUnit]:
        """Create a set of work units for testing actionability."""
        p = Path("/dev/null")
        return [
            WorkUnit(
                id="E0-F1-S1-T1",
                title="Task 1",
                status=WorkUnitStatus.DONE,
                unit_type=WorkUnitType.TASK,
                file_path=p,
                repo="r",
            ),
            WorkUnit(
                id="E0-F1-S1-T2",
                title="Task 2 (depends on T1)",
                status=WorkUnitStatus.IN_QUEUE,
                unit_type=WorkUnitType.TASK,
                file_path=p,
                repo="r",
                dependencies=["E0-F1-S1-T1"],
            ),
            WorkUnit(
                id="E0-F1-S1-T3",
                title="Task 3 (depends on T2)",
                status=WorkUnitStatus.IN_QUEUE,
                unit_type=WorkUnitType.TASK,
                file_path=p,
                repo="r",
                dependencies=["E0-F1-S1-T2"],
            ),
            WorkUnit(
                id="E0-F1-S1",
                title="Story",
                status=WorkUnitStatus.IN_QUEUE,
                unit_type=WorkUnitType.STORY,
                file_path=p,
                repo="r",
            ),
        ]

    def test_find_next_actionable_returns_task_with_deps_done(self) -> None:
        parser = BacklogParser.__new__(BacklogParser)
        parser._backlog_root = Path("/tmp")
        parser._backlog_index = Path("/tmp/B.md")

        units = self._make_units()
        result = parser.find_next_actionable(units)

        assert result is not None
        assert result.id == "E0-F1-S1-T2"

    def test_find_next_actionable_returns_none_when_nothing_actionable(self) -> None:
        parser = BacklogParser.__new__(BacklogParser)
        parser._backlog_root = Path("/tmp")
        parser._backlog_index = Path("/tmp/B.md")

        p = Path("/dev/null")
        units = [
            WorkUnit(
                id="E0-F1-S1-T1",
                title="Task blocked",
                status=WorkUnitStatus.BLOCKED,
                unit_type=WorkUnitType.TASK,
                file_path=p,
                repo="r",
            ),
            WorkUnit(
                id="E0-F1-S1-T2",
                title="Task depends on blocked",
                status=WorkUnitStatus.IN_QUEUE,
                unit_type=WorkUnitType.TASK,
                file_path=p,
                repo="r",
                dependencies=["E0-F1-S1-T1"],
            ),
        ]
        result = parser.find_next_actionable(units)
        assert result is None

    def test_find_next_actionable_skips_non_task_types(self) -> None:
        parser = BacklogParser.__new__(BacklogParser)
        parser._backlog_root = Path("/tmp")
        parser._backlog_index = Path("/tmp/B.md")

        p = Path("/dev/null")
        units = [
            WorkUnit(
                id="E0-F1",
                title="Feature",
                status=WorkUnitStatus.IN_QUEUE,
                unit_type=WorkUnitType.FEATURE,
                file_path=p,
                repo="r",
            ),
        ]
        result = parser.find_next_actionable(units)
        assert result is None


class TestAllDone:
    """Test all_done returns True/False correctly."""

    def test_all_done_true(self) -> None:
        parser = BacklogParser.__new__(BacklogParser)
        parser._backlog_root = Path("/tmp")
        parser._backlog_index = Path("/tmp/B.md")

        p = Path("/dev/null")
        units = [
            WorkUnit(
                id="T1",
                title="a",
                status=WorkUnitStatus.DONE,
                unit_type=WorkUnitType.TASK,
                file_path=p,
                repo="r",
            ),
            WorkUnit(
                id="T2",
                title="b",
                status=WorkUnitStatus.DONE,
                unit_type=WorkUnitType.TASK,
                file_path=p,
                repo="r",
            ),
        ]
        assert parser.all_done(units) is True

    def test_all_done_false(self) -> None:
        parser = BacklogParser.__new__(BacklogParser)
        parser._backlog_root = Path("/tmp")
        parser._backlog_index = Path("/tmp/B.md")

        p = Path("/dev/null")
        units = [
            WorkUnit(
                id="T1",
                title="a",
                status=WorkUnitStatus.DONE,
                unit_type=WorkUnitType.TASK,
                file_path=p,
                repo="r",
            ),
            WorkUnit(
                id="T2",
                title="b",
                status=WorkUnitStatus.IN_QUEUE,
                unit_type=WorkUnitType.TASK,
                file_path=p,
                repo="r",
            ),
        ]
        assert parser.all_done(units) is False


class TestGetBlockedUnits:
    """Test get_blocked_units filters correctly."""

    def test_returns_only_blocked(self) -> None:
        parser = BacklogParser.__new__(BacklogParser)
        parser._backlog_root = Path("/tmp")
        parser._backlog_index = Path("/tmp/B.md")

        p = Path("/dev/null")
        units = [
            WorkUnit(
                id="T1",
                title="a",
                status=WorkUnitStatus.BLOCKED,
                unit_type=WorkUnitType.TASK,
                file_path=p,
                repo="r",
            ),
            WorkUnit(
                id="T2",
                title="b",
                status=WorkUnitStatus.IN_QUEUE,
                unit_type=WorkUnitType.TASK,
                file_path=p,
                repo="r",
            ),
            WorkUnit(
                id="T3",
                title="c",
                status=WorkUnitStatus.BLOCKED,
                unit_type=WorkUnitType.TASK,
                file_path=p,
                repo="r",
            ),
        ]
        blocked = parser.get_blocked_units(units)
        assert len(blocked) == 2
        assert all(u.status is WorkUnitStatus.BLOCKED for u in blocked)

    def test_returns_empty_when_none_blocked(self) -> None:
        parser = BacklogParser.__new__(BacklogParser)
        parser._backlog_root = Path("/tmp")
        parser._backlog_index = Path("/tmp/B.md")

        p = Path("/dev/null")
        units = [
            WorkUnit(
                id="T1",
                title="a",
                status=WorkUnitStatus.DONE,
                unit_type=WorkUnitType.TASK,
                file_path=p,
                repo="r",
            ),
        ]
        assert parser.get_blocked_units(units) == []


class TestGetParallelCandidates:
    """Test get_parallel_candidates returns multiple actionable tasks."""

    def test_returns_multiple_candidates(self) -> None:
        parser = BacklogParser.__new__(BacklogParser)
        parser._backlog_root = Path("/tmp")
        parser._backlog_index = Path("/tmp/B.md")

        p = Path("/dev/null")
        units = [
            WorkUnit(
                id="E0-F1-S1-T1",
                title="a",
                status=WorkUnitStatus.DONE,
                unit_type=WorkUnitType.TASK,
                file_path=p,
                repo="r",
            ),
            WorkUnit(
                id="E0-F1-S1-T2",
                title="b",
                status=WorkUnitStatus.IN_QUEUE,
                unit_type=WorkUnitType.TASK,
                file_path=p,
                repo="r",
                dependencies=["E0-F1-S1-T1"],
            ),
            WorkUnit(
                id="E0-F1-S1-T3",
                title="c",
                status=WorkUnitStatus.IN_QUEUE,
                unit_type=WorkUnitType.TASK,
                file_path=p,
                repo="r",
                dependencies=["E0-F1-S1-T1"],
            ),
        ]
        candidates = parser.get_parallel_candidates(units)
        assert len(candidates) == 2
        assert candidates[0].id == "E0-F1-S1-T2"
        assert candidates[1].id == "E0-F1-S1-T3"

    def test_candidates_sorted_by_id(self) -> None:
        parser = BacklogParser.__new__(BacklogParser)
        parser._backlog_root = Path("/tmp")
        parser._backlog_index = Path("/tmp/B.md")

        p = Path("/dev/null")
        units = [
            WorkUnit(
                id="E0-F2-S1-T1",
                title="later",
                status=WorkUnitStatus.IN_QUEUE,
                unit_type=WorkUnitType.TASK,
                file_path=p,
                repo="r",
            ),
            WorkUnit(
                id="E0-F1-S1-T1",
                title="earlier",
                status=WorkUnitStatus.IN_QUEUE,
                unit_type=WorkUnitType.TASK,
                file_path=p,
                repo="r",
            ),
        ]
        candidates = parser.get_parallel_candidates(units)
        assert candidates[0].id == "E0-F1-S1-T1"
        assert candidates[1].id == "E0-F2-S1-T1"

    def test_in_progress_prioritized_over_in_queue(self) -> None:
        parser = BacklogParser.__new__(BacklogParser)
        parser._backlog_root = Path("/tmp")
        parser._backlog_index = Path("/tmp/B.md")

        p = Path("/dev/null")
        units = [
            WorkUnit(
                id="E0-F1-S1-T1",
                title="queued task",
                status=WorkUnitStatus.IN_QUEUE,
                unit_type=WorkUnitType.TASK,
                file_path=p,
                repo="r",
            ),
            WorkUnit(
                id="E0-F1-S1-T2",
                title="in-progress task",
                status=WorkUnitStatus.IN_PROGRESS,
                unit_type=WorkUnitType.TASK,
                file_path=p,
                repo="r",
            ),
        ]
        candidates = parser.get_parallel_candidates(units)
        assert len(candidates) == 2
        assert candidates[0].id == "E0-F1-S1-T2"
        assert candidates[0].status is WorkUnitStatus.IN_PROGRESS
        assert candidates[1].id == "E0-F1-S1-T1"
        assert candidates[1].status is WorkUnitStatus.IN_QUEUE

    def test_find_next_returns_in_progress_before_in_queue(self) -> None:
        parser = BacklogParser.__new__(BacklogParser)
        parser._backlog_root = Path("/tmp")
        parser._backlog_index = Path("/tmp/B.md")

        p = Path("/dev/null")
        units = [
            WorkUnit(
                id="E0-F1-S1-T1",
                title="queued",
                status=WorkUnitStatus.IN_QUEUE,
                unit_type=WorkUnitType.TASK,
                file_path=p,
                repo="r",
            ),
            WorkUnit(
                id="E0-F1-S1-T3",
                title="in progress",
                status=WorkUnitStatus.IN_PROGRESS,
                unit_type=WorkUnitType.TASK,
                file_path=p,
                repo="r",
            ),
        ]
        result = parser.find_next_actionable(units)
        assert result is not None
        assert result.id == "E0-F1-S1-T3"
        assert result.status is WorkUnitStatus.IN_PROGRESS


class TestAlignStatuses:
    """Test _align_statuses corrects mismatches between BACKLOG.md and work unit files."""

    def test_file_status_overrides_index_status(self, tmp_path: Path) -> None:
        """When the work unit file disagrees with BACKLOG.md, the file wins."""
        # Set up a backlog dir with a work unit file that says in-progress
        backlog_dir = tmp_path / "backlog"
        backlog_dir.mkdir()
        wu_file = backlog_dir / "T1.md"
        wu_file.write_text("# E0-F1-S1-T1: Task\n\n## Status: in-progress\n")

        parser = BacklogParser.__new__(BacklogParser)
        parser._backlog_root = backlog_dir
        parser._backlog_index = tmp_path / "BACKLOG.md"

        p = Path("backlog/T1.md")
        units = [
            WorkUnit(
                id="E0-F1-S1-T1",
                title="Task",
                status=WorkUnitStatus.DONE,  # BACKLOG.md says done
                unit_type=WorkUnitType.TASK,
                file_path=p,
                repo="r",
            ),
        ]

        parser._align_statuses(units)
        assert units[0].status is WorkUnitStatus.IN_PROGRESS

    def test_no_change_when_statuses_match(self, tmp_path: Path) -> None:
        """No correction when both sources agree."""
        backlog_dir = tmp_path / "backlog"
        backlog_dir.mkdir()
        wu_file = backlog_dir / "T1.md"
        wu_file.write_text("# E0-F1-S1-T1: Task\n\n## Status: in-queue\n")

        parser = BacklogParser.__new__(BacklogParser)
        parser._backlog_root = backlog_dir
        parser._backlog_index = tmp_path / "BACKLOG.md"

        p = Path("backlog/T1.md")
        units = [
            WorkUnit(
                id="E0-F1-S1-T1",
                title="Task",
                status=WorkUnitStatus.IN_QUEUE,
                unit_type=WorkUnitType.TASK,
                file_path=p,
                repo="r",
            ),
        ]

        parser._align_statuses(units)
        assert units[0].status is WorkUnitStatus.IN_QUEUE

    def test_missing_file_is_silently_skipped(self, tmp_path: Path) -> None:
        """If the work unit file doesn't exist, no correction is made."""
        parser = BacklogParser.__new__(BacklogParser)
        parser._backlog_root = tmp_path
        parser._backlog_index = tmp_path / "BACKLOG.md"

        p = Path("backlog/nonexistent.md")
        units = [
            WorkUnit(
                id="E0-F1-S1-T1",
                title="Task",
                status=WorkUnitStatus.DONE,
                unit_type=WorkUnitType.TASK,
                file_path=p,
                repo="r",
            ),
        ]

        parser._align_statuses(units)
        assert units[0].status is WorkUnitStatus.DONE
