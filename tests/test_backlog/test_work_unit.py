"""Tests for judges.work_unit module."""

from __future__ import annotations

from pathlib import Path

import pytest

from devbench.backlog.work_unit import WorkUnit, WorkUnitStatus, WorkUnitType, validate_manifest_paths


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

    def test_draft_value(self) -> None:
        assert WorkUnitStatus.DRAFT.value == "Draft"

    def test_draft_is_before_in_queue_in_lifecycle_order(self) -> None:
        """DRAFT must appear before IN_QUEUE so lifecycle reads draft -> in-queue."""
        members = list(WorkUnitStatus)
        draft_idx = members.index(WorkUnitStatus.DRAFT)
        in_queue_idx = members.index(WorkUnitStatus.IN_QUEUE)
        assert draft_idx < in_queue_idx, f"DRAFT (index {draft_idx}) must precede IN_QUEUE (index {in_queue_idx})"

    def test_all_statuses_present(self) -> None:
        names = {s.name for s in WorkUnitStatus}
        assert names == {
            "DRAFT",
            "IN_QUEUE",
            "IN_PROGRESS",
            "IN_REVIEW",
            "DONE",
            "BLOCKED",
            "PROPOSED",
            "DECLINED",
            "HOLD",
        }

    def test_proposed_value(self) -> None:
        assert WorkUnitStatus.PROPOSED.value == "Proposed"

    def test_declined_value(self) -> None:
        assert WorkUnitStatus.DECLINED.value == "Declined"

    def test_hold_value(self) -> None:
        assert WorkUnitStatus.HOLD.value == "Hold"


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
    """Test is_task, is_story, is_feature, is_epic.

    TD-11 collapsed four nearly-identical per-type tests into a single
    parametrized case. Each row asserts that the matching predicate
    returns ``True`` and every other predicate returns ``False``.
    """

    @pytest.mark.parametrize(
        ("unit_type", "expected_predicate"),
        [
            (WorkUnitType.TASK, "is_task"),
            (WorkUnitType.STORY, "is_story"),
            (WorkUnitType.FEATURE, "is_feature"),
            (WorkUnitType.EPIC, "is_epic"),
        ],
    )
    def test_predicates_are_mutually_exclusive(
        self,
        unit_type: WorkUnitType,
        expected_predicate: str,
    ) -> None:
        wu = WorkUnit(
            id=unit_type.value[0],
            title="x",
            status=WorkUnitStatus.IN_QUEUE,
            unit_type=unit_type,
            file_path=Path("/dev/null"),
            repo="r",
        )
        all_predicates = {"is_task", "is_story", "is_feature", "is_epic"}
        assert getattr(wu, expected_predicate)() is True
        for other in all_predicates - {expected_predicate}:
            assert getattr(wu, other)() is False, (
                f"{other}() returned True when {expected_predicate}() was expected to be the sole match"
            )


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


class TestValidateManifestPaths:
    """Tests for validate_manifest_paths rule-10 and rule-11 enforcement."""

    def test_rejects_path_prefixed_with_checkout_directory(self) -> None:
        """Rule 11: a path starting with a known checkout_directory is rejected."""
        with pytest.raises(ValueError, match="rule 11"):
            validate_manifest_paths(["kanon/src/foo.py"], ["kanon"])

    def test_accepts_repo_relative_path(self) -> None:
        """A plain repo-relative path is accepted regardless of checkout_directories."""
        validate_manifest_paths(["src/foo.py"], ["kanon"])

    def test_rejects_path_containing_em_dash(self) -> None:
        """Rule 10: a path containing U+2014 is rejected."""
        with pytest.raises(ValueError, match="rule 10"):
            validate_manifest_paths(["src/foo—bar.py"], [])

    def test_accepts_empty_path_list(self) -> None:
        """An empty path list passes without error."""
        validate_manifest_paths([], ["kanon", "devbench"])

    def test_error_message_names_first_offending_path(self) -> None:
        """The error message must include the offending path."""
        offending = "kanon/src/bad.py"
        with pytest.raises(ValueError, match=offending):
            validate_manifest_paths([offending], ["kanon"])
