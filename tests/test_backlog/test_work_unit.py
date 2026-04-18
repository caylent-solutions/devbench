"""Tests for judges.work_unit module."""

from __future__ import annotations

from pathlib import Path

import pytest

from devbench.backlog.work_unit import WorkUnit, WorkUnitStatus, WorkUnitType


class TestWorkUnitStatusEnum:
    """Verify WorkUnitStatus enum values."""

    def test_in_queue_value(self) -> None:
        assert WorkUnitStatus.IN_QUEUE.value == "In Queue"

    def test_in_progress_value(self) -> None:
        assert WorkUnitStatus.IN_PROGRESS.value == "In Progress"

    def test_in_review_value(self) -> None:
        assert WorkUnitStatus.IN_REVIEW.value == "In Review"

    def test_done_value(self) -> None:
        assert WorkUnitStatus.DONE.value == "Done"

    def test_blocked_value(self) -> None:
        assert WorkUnitStatus.BLOCKED.value == "Blocked"

    def test_all_statuses_present(self) -> None:
        names = {s.name for s in WorkUnitStatus}
        assert names == {"IN_QUEUE", "IN_PROGRESS", "IN_REVIEW", "DONE", "BLOCKED", "PROPOSED"}

    def test_proposed_value(self) -> None:
        assert WorkUnitStatus.PROPOSED.value == "Proposed"


class TestWorkUnitTypeEnum:
    """Verify WorkUnitType enum values."""

    def test_epic_value(self) -> None:
        assert WorkUnitType.EPIC.value == "Epic"

    def test_feature_value(self) -> None:
        assert WorkUnitType.FEATURE.value == "Feature"

    def test_story_value(self) -> None:
        assert WorkUnitType.STORY.value == "Story"

    def test_task_value(self) -> None:
        assert WorkUnitType.TASK.value == "Task"

    def test_all_types_present(self) -> None:
        names = {t.name for t in WorkUnitType}
        assert names == {"EPIC", "FEATURE", "STORY", "TASK"}


class TestWorkUnitCreation:
    """Test WorkUnit dataclass instantiation."""

    def test_minimal_creation(self, tmp_path: Path) -> None:
        wu = WorkUnit(
            id="E0-F1-S1-T1",
            title="Test Task",
            status=WorkUnitStatus.IN_QUEUE,
            unit_type=WorkUnitType.TASK,
            file_path=tmp_path / "task.md",
            repo="caylent-solutions/git-repo",
        )
        assert wu.id == "E0-F1-S1-T1"
        assert wu.title == "Test Task"
        assert wu.status is WorkUnitStatus.IN_QUEUE
        assert wu.unit_type is WorkUnitType.TASK
        assert wu.dependencies == []
        assert wu.acceptance_criteria == []
        assert wu.description == ""

    def test_full_creation(self, sample_work_unit: WorkUnit) -> None:
        assert sample_work_unit.id == "E0-F1-S1-T1"
        assert sample_work_unit.repo == "caylent-solutions/git-repo"
        assert len(sample_work_unit.dependencies) == 1
        assert sample_work_unit.dependencies[0] == "E0-F1-S1"
        assert len(sample_work_unit.acceptance_criteria) == 2


class TestSetStatus:
    """Test set_status updates the .md file on disk."""

    def test_set_status_updates_file(self, tmp_work_unit_file: Path) -> None:
        wu = WorkUnit(
            id="E0-F1-S1-T1",
            title="Test",
            status=WorkUnitStatus.IN_QUEUE,
            unit_type=WorkUnitType.TASK,
            file_path=tmp_work_unit_file,
            repo="caylent-solutions/git-repo",
        )
        wu.set_status(WorkUnitStatus.IN_PROGRESS)

        content = tmp_work_unit_file.read_text()
        assert "## Status: In Progress" in content
        assert wu.status is WorkUnitStatus.IN_PROGRESS

    def test_set_status_updates_from_done_to_blocked(self, tmp_work_unit_file: Path) -> None:
        wu = WorkUnit(
            id="E0-F1-S1-T1",
            title="Test",
            status=WorkUnitStatus.IN_QUEUE,
            unit_type=WorkUnitType.TASK,
            file_path=tmp_work_unit_file,
            repo="caylent-solutions/git-repo",
        )
        wu.set_status(WorkUnitStatus.DONE)
        assert wu.status is WorkUnitStatus.DONE

        wu.set_status(WorkUnitStatus.BLOCKED)
        content = tmp_work_unit_file.read_text()
        assert "## Status: Blocked" in content
        assert wu.status is WorkUnitStatus.BLOCKED

    def test_set_status_raises_when_file_missing(self, tmp_path: Path) -> None:
        wu = WorkUnit(
            id="E0-F1-S1-T1",
            title="Test",
            status=WorkUnitStatus.IN_QUEUE,
            unit_type=WorkUnitType.TASK,
            file_path=tmp_path / "nonexistent.md",
            repo="caylent-solutions/git-repo",
        )
        with pytest.raises(FileNotFoundError):
            wu.set_status(WorkUnitStatus.DONE)

    def test_set_status_raises_when_no_status_line(self, tmp_path: Path) -> None:
        bad_file = tmp_path / "bad.md"
        bad_file.write_text("# No status line here\n\nJust content.\n")

        wu = WorkUnit(
            id="E0-F1-S1-T1",
            title="Test",
            status=WorkUnitStatus.IN_QUEUE,
            unit_type=WorkUnitType.TASK,
            file_path=bad_file,
            repo="caylent-solutions/git-repo",
        )
        with pytest.raises(ValueError, match="Could not find"):
            wu.set_status(WorkUnitStatus.DONE)


class TestLogComment:
    """Test log_comment appends to Comments section."""

    def test_log_comment_appends_to_existing_section(self, tmp_work_unit_file: Path) -> None:
        wu = WorkUnit(
            id="E0-F1-S1-T1",
            title="Test",
            status=WorkUnitStatus.IN_QUEUE,
            unit_type=WorkUnitType.TASK,
            file_path=tmp_work_unit_file,
            repo="caylent-solutions/git-repo",
        )
        wu.log_comment("test-agent", "START", "Beginning execution")

        content = tmp_work_unit_file.read_text()
        assert "[test-agent]" in content
        assert "[START]" in content
        assert "Beginning execution" in content

    def test_log_comment_creates_section_if_missing(self, tmp_path: Path) -> None:
        no_comments_file = tmp_path / "no_comments.md"
        no_comments_file.write_text("# E0-F1-S1-T1: Test\n\n## Status: In Queue\n")

        wu = WorkUnit(
            id="E0-F1-S1-T1",
            title="Test",
            status=WorkUnitStatus.IN_QUEUE,
            unit_type=WorkUnitType.TASK,
            file_path=no_comments_file,
            repo="caylent-solutions/git-repo",
        )
        wu.log_comment("agent-1", "ACTION", "Did something")

        content = no_comments_file.read_text()
        assert "## Comments" in content
        assert "[agent-1]" in content
        assert "Did something" in content

    def test_log_comment_appends_multiple_entries(self, tmp_work_unit_file: Path) -> None:
        wu = WorkUnit(
            id="E0-F1-S1-T1",
            title="Test",
            status=WorkUnitStatus.IN_QUEUE,
            unit_type=WorkUnitType.TASK,
            file_path=tmp_work_unit_file,
            repo="caylent-solutions/git-repo",
        )
        wu.log_comment("agent-1", "FIRST", "First entry")
        wu.log_comment("agent-2", "SECOND", "Second entry")

        content = tmp_work_unit_file.read_text()
        assert content.index("[FIRST]") < content.index("[SECOND]")


class TestTypePredicates:
    """Test is_task, is_story, is_feature, is_epic."""

    def test_is_task_returns_true_for_task(self) -> None:
        wu = WorkUnit(
            id="T1",
            title="t",
            status=WorkUnitStatus.IN_QUEUE,
            unit_type=WorkUnitType.TASK,
            file_path=Path("/dev/null"),
            repo="r",
        )
        assert wu.is_task() is True
        assert wu.is_story() is False
        assert wu.is_feature() is False
        assert wu.is_epic() is False

    def test_is_story_returns_true_for_story(self) -> None:
        wu = WorkUnit(
            id="S1",
            title="s",
            status=WorkUnitStatus.IN_QUEUE,
            unit_type=WorkUnitType.STORY,
            file_path=Path("/dev/null"),
            repo="r",
        )
        assert wu.is_story() is True
        assert wu.is_task() is False

    def test_is_feature_returns_true_for_feature(self) -> None:
        wu = WorkUnit(
            id="F1",
            title="f",
            status=WorkUnitStatus.IN_QUEUE,
            unit_type=WorkUnitType.FEATURE,
            file_path=Path("/dev/null"),
            repo="r",
        )
        assert wu.is_feature() is True
        assert wu.is_task() is False

    def test_is_epic_returns_true_for_epic(self) -> None:
        wu = WorkUnit(
            id="E0",
            title="e",
            status=WorkUnitStatus.IN_QUEUE,
            unit_type=WorkUnitType.EPIC,
            file_path=Path("/dev/null"),
            repo="r",
        )
        assert wu.is_epic() is True
        assert wu.is_task() is False


class TestParseId:
    """Test parse_id splits the compound ID correctly."""

    def test_parse_four_part_id(self) -> None:
        wu = WorkUnit(
            id="E0-F1-S1-T1",
            title="t",
            status=WorkUnitStatus.IN_QUEUE,
            unit_type=WorkUnitType.TASK,
            file_path=Path("/dev/null"),
            repo="r",
        )
        assert wu.parse_id() == ("E0", "F1", "S1", "T1")

    def test_parse_single_part_id(self) -> None:
        wu = WorkUnit(
            id="E0",
            title="t",
            status=WorkUnitStatus.IN_QUEUE,
            unit_type=WorkUnitType.EPIC,
            file_path=Path("/dev/null"),
            repo="r",
        )
        assert wu.parse_id() == ("E0",)

    def test_parse_two_part_id(self) -> None:
        wu = WorkUnit(
            id="E0-F1",
            title="t",
            status=WorkUnitStatus.IN_QUEUE,
            unit_type=WorkUnitType.FEATURE,
            file_path=Path("/dev/null"),
            repo="r",
        )
        assert wu.parse_id() == ("E0", "F1")

    def test_parse_id_raises_for_empty(self) -> None:
        wu = WorkUnit(
            id="",
            title="t",
            status=WorkUnitStatus.IN_QUEUE,
            unit_type=WorkUnitType.TASK,
            file_path=Path("/dev/null"),
            repo="r",
        )
        with pytest.raises(ValueError, match="Invalid work-unit ID"):
            wu.parse_id()
