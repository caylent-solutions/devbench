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
        import os
        workspace = Path(os.environ.get("JUDGE_WORKSPACE_ROOT", "/tmp/test-workspace"))
        actual_backlog = workspace / "BACKLOG.md"
        if not actual_backlog.is_file():
            pytest.skip("Actual BACKLOG.md not found")

        parser = BacklogParser(
            backlog_root=workspace / "backlog",
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
        # mock_backlog_index is at tmp_path/BACKLOG.md; backlog files are under tmp_path/backlog/
        workspace_root = mock_backlog_index.parent
        parser = BacklogParser(
            backlog_root=workspace_root / "backlog",
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

        # file_path must be absolute and rooted at the workspace root
        for unit in units:
            assert unit.file_path.is_absolute(), f"{unit.id}: file_path is not absolute: {unit.file_path}"
            assert unit.file_path.is_relative_to(workspace_root), (
                f"{unit.id}: file_path {unit.file_path} is not under workspace root {workspace_root}"
            )

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

    def test_parse_index_warns_on_status_mismatch(self, tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
        """A warning is emitted when BACKLOG.md row status differs from the work-unit file."""
        import logging

        backlog_dir = tmp_path / "backlog"
        backlog_dir.mkdir()
        wu_file = backlog_dir / "E0-F1-S1-T1.md"
        wu_file.write_text("# E0-F1-S1-T1: Create Makefile\n\n## Status: in-progress\n")

        index = tmp_path / "BACKLOG.md"
        index.write_text(
            "## Full Work Unit Index\n\n"
            "| ID | Title | Type | Status | Dependencies | Repo | File Path |\n"
            "|-----|-------|------|--------|-------------|------|----------|\n"
            "| E0-F1-S1-T1 | Create Makefile | Task | in-queue | None | git-repo | `backlog/E0-F1-S1-T1.md` |\n"
        )
        parser = BacklogParser(backlog_root=backlog_dir, backlog_index=index)

        with caplog.at_level(logging.WARNING, logger="devbench.backlog.parser"):
            units = parser.parse_index()

        assert len(units) == 1
        assert units[0].status.value == "In Progress"  # file is source of truth
        assert any("mismatch" in r.message.lower() for r in caplog.records)


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


class TestParseWorkUnitFileBranch:
    """Test branch field parsing in parse_work_unit_file."""

    def test_parses_branch_from_spec(self, tmp_path: Path) -> None:
        wu_file = tmp_path / "E0-F1-S1-T1.md"
        wu_file.write_text(
            "# E0-F1-S1-T1: My Task\n\n"
            "## Status: in-queue\n\n"
            "## Target Repository\n\n"
            "- **Repo:** `caylent-solutions/git-repo`\n"
            "- **Branch:** `feature/remove-deprecated-env-vars`\n"
        )
        parser = BacklogParser(backlog_root=tmp_path, backlog_index=tmp_path / "B.md")
        wu = parser.parse_work_unit_file(wu_file)

        assert wu.branch == "feature/remove-deprecated-env-vars"

    def test_branch_falls_back_to_template_when_not_in_spec(self, tmp_path: Path) -> None:
        wu_file = tmp_path / "E0-F1-S1-T1.md"
        wu_file.write_text(
            "# E0-F1-S1-T1: My Task\n\n"
            "## Status: in-queue\n\n"
            "## Target Repository\n\n"
            "- **Repo:** `caylent-solutions/git-repo`\n"
        )
        parser = BacklogParser(backlog_root=tmp_path, backlog_index=tmp_path / "B.md")
        wu = parser.parse_work_unit_file(wu_file)

        assert wu.branch == "backlog/e0-f1-s1-t1"

    def test_parses_branch_with_backlog_prefix(self, tmp_path: Path) -> None:
        wu_file = tmp_path / "E0-F1-S1-T2.md"
        wu_file.write_text(
            "# E0-F1-S1-T2: Another Task\n\n"
            "## Status: in-queue\n\n"
            "## Target Repository\n\n"
            "- **Repo:** `caylent-solutions/git-repo`\n"
            "- **Branch:** `backlog/e0-f1-s1-t2`\n"
        )
        parser = BacklogParser(backlog_root=tmp_path, backlog_index=tmp_path / "B.md")
        wu = parser.parse_work_unit_file(wu_file)

        assert wu.branch == "backlog/e0-f1-s1-t2"

    def test_branch_parsed_from_conftest_template(self, tmp_work_unit_file: Path) -> None:
        parser = BacklogParser(
            backlog_root=tmp_work_unit_file.parent,
            backlog_index=tmp_work_unit_file.parent / "BACKLOG.md",
        )
        wu = parser.parse_work_unit_file(tmp_work_unit_file)

        assert wu.branch == "backlog/e0-f1-s1-t1"


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


class TestParserStatusVocabulary:
    """Test that BacklogParser recognises all five runtime status values.

    These tests cover the status vocabulary documented in the parser.py module
    docstring (AC-DOC-1 of E8-F1-S1-T1): in-queue, in-progress, in-review,
    done, and blocked must all be accepted by parse_work_unit_file without error.
    """

    def _make_wu_file(self, tmp_path: Path, status: str) -> Path:
        """Write a minimal compliant work-unit file with the given status."""
        wu = tmp_path / "E0-F1-S1-T1.md"
        wu.write_text(
            f"# E0-F1-S1-T1: Task Title\n\n## Status: {status}\n\n## Comments\n",
            encoding="utf-8",
        )
        return wu

    @pytest.mark.parametrize(
        "raw_status",
        ["in-queue", "in-progress", "in-review", "done", "blocked"],
    )
    def test_parse_work_unit_file_accepts_all_runtime_statuses(
        self, tmp_path: Path, raw_status: str
    ) -> None:
        """parse_work_unit_file must parse every status in the runtime vocabulary.

        Given: A work-unit file whose ## Status: line contains one of the five
               valid runtime status values.
        When:  parse_work_unit_file is called.
        Then:  The returned WorkUnit carries the corresponding WorkUnitStatus
               value without raising.
        """
        wu_file = self._make_wu_file(tmp_path, raw_status)
        parser = BacklogParser(backlog_root=tmp_path, backlog_index=tmp_path / "BACKLOG.md")
        unit = parser.parse_work_unit_file(wu_file)
        assert unit.status.value.lower().replace(" ", "-") == raw_status, (
            f"Expected status '{raw_status}' but got '{unit.status.value}'"
        )

    def test_parse_work_unit_file_rejects_unknown_status(self, tmp_path: Path) -> None:
        """parse_work_unit_file must raise ValueError for an unrecognised status.

        Given: A work-unit file whose ## Status: line contains an invalid value.
        When:  parse_work_unit_file is called.
        Then:  ValueError is raised with the unrecognised status in the message.
        """
        wu_file = self._make_wu_file(tmp_path, "not-a-status")
        parser = BacklogParser(backlog_root=tmp_path, backlog_index=tmp_path / "BACKLOG.md")
        with pytest.raises(ValueError, match="not-a-status"):
            parser.parse_work_unit_file(wu_file)

    def test_blocked_status_does_not_appear_in_parallel_candidates(
        self, tmp_path: Path
    ) -> None:
        """A BLOCKED work unit must not appear in get_parallel_candidates.

        Given: A list containing one BLOCKED task and one IN_QUEUE task with
               no unsatisfied dependencies.
        When:  get_parallel_candidates is called.
        Then:  Only the IN_QUEUE task is returned; the BLOCKED task is excluded.
        """
        parser = BacklogParser.__new__(BacklogParser)
        parser._backlog_root = tmp_path
        parser._backlog_index = tmp_path / "BACKLOG.md"

        p = Path("/dev/null")
        units = [
            WorkUnit(
                id="E0-F1-S1-T1",
                title="blocked task",
                status=WorkUnitStatus.BLOCKED,
                unit_type=WorkUnitType.TASK,
                file_path=p,
                repo="r",
            ),
            WorkUnit(
                id="E0-F1-S1-T2",
                title="queued task",
                status=WorkUnitStatus.IN_QUEUE,
                unit_type=WorkUnitType.TASK,
                file_path=p,
                repo="r",
            ),
        ]
        candidates = parser.get_parallel_candidates(units)
        ids = [u.id for u in candidates]
        assert "E0-F1-S1-T1" not in ids, "BLOCKED task must not appear in parallel candidates"
        assert "E0-F1-S1-T2" in ids, "IN_QUEUE task must appear in parallel candidates"


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


class TestDepsSatisfied:
    """Tests for the fixed _deps_satisfied: all dep types are blocking.

    These tests verify AC-1 through AC-5 of E15-F1-S1-T1.
    """

    def _parser(self) -> BacklogParser:
        parser = BacklogParser.__new__(BacklogParser)
        parser._backlog_root = Path("/dev/null")
        parser._backlog_index = Path("/dev/null")
        return parser

    def _task(self, unit_id: str, status: WorkUnitStatus, deps: list[str] | None = None) -> WorkUnit:
        return WorkUnit(
            id=unit_id,
            title=unit_id,
            status=status,
            unit_type=WorkUnitType.TASK,
            file_path=Path("/dev/null"),
            repo="r",
            dependencies=deps or [],
        )

    def _epic(self, unit_id: str, status: WorkUnitStatus) -> WorkUnit:
        return WorkUnit(
            id=unit_id,
            title=unit_id,
            status=status,
            unit_type=WorkUnitType.EPIC,
            file_path=Path("/dev/null"),
            repo="r",
        )

    def _feature(self, unit_id: str, status: WorkUnitStatus) -> WorkUnit:
        return WorkUnit(
            id=unit_id,
            title=unit_id,
            status=status,
            unit_type=WorkUnitType.FEATURE,
            file_path=Path("/dev/null"),
            repo="r",
        )

    def test_deps_satisfied_blocks_on_incomplete_epic(self) -> None:
        """AC-1: A task whose only dep is an in-queue epic is NOT returned.

        Given: A task with a single dependency on an in-queue epic.
        When:  get_parallel_candidates is called.
        Then:  The task is not returned (dep is unsatisfied).
        """
        parser = self._parser()
        epic = self._epic("E14", WorkUnitStatus.IN_QUEUE)
        task = self._task("E15-F1-S1-T1", WorkUnitStatus.IN_QUEUE, deps=["E14"])
        candidates = parser.get_parallel_candidates([epic, task])
        ids = [u.id for u in candidates]
        assert "E15-F1-S1-T1" not in ids

    def test_deps_satisfied_passes_when_epic_done(self) -> None:
        """AC-2: A task whose only dep is a done epic IS returned.

        Given: A task with a single dependency on a done epic.
        When:  get_parallel_candidates is called.
        Then:  The task is returned (dep is satisfied).
        """
        parser = self._parser()
        epic = self._epic("E14", WorkUnitStatus.DONE)
        task = self._task("E15-F1-S1-T1", WorkUnitStatus.IN_QUEUE, deps=["E14"])
        candidates = parser.get_parallel_candidates([epic, task])
        ids = [u.id for u in candidates]
        assert "E15-F1-S1-T1" in ids

    def test_deps_satisfied_passes_when_no_deps(self) -> None:
        """AC-3: A task with no deps IS returned.

        Given: A task with an empty dependencies list.
        When:  get_parallel_candidates is called.
        Then:  The task is returned.
        """
        parser = self._parser()
        task = self._task("E15-F1-S1-T1", WorkUnitStatus.IN_QUEUE)
        candidates = parser.get_parallel_candidates([task])
        ids = [u.id for u in candidates]
        assert "E15-F1-S1-T1" in ids

    def test_deps_satisfied_blocks_when_feature_dep_incomplete(self) -> None:
        """AC-4: Task-level dep done but feature-level dep in-queue → NOT returned.

        Given: A task with two deps: a done task and an in-queue feature.
        When:  get_parallel_candidates is called.
        Then:  The task is not returned (feature dep is unsatisfied).
        """
        parser = self._parser()
        done_task = self._task("E15-F1-S1-T1", WorkUnitStatus.DONE)
        feature = self._feature("E15-F1", WorkUnitStatus.IN_QUEUE)
        blocked_task = self._task(
            "E15-F1-S1-T2",
            WorkUnitStatus.IN_QUEUE,
            deps=["E15-F1-S1-T1", "E15-F1"],
        )
        candidates = parser.get_parallel_candidates([done_task, feature, blocked_task])
        ids = [u.id for u in candidates]
        assert "E15-F1-S1-T2" not in ids

    def test_task_ids_helper_removed(self) -> None:
        """AC-5: _task_ids helper no longer exists in BacklogParser.

        Given: The BacklogParser class.
        When:  Checking for the _task_ids attribute.
        Then:  It does not exist.
        """
        assert not hasattr(BacklogParser, "_task_ids")


class TestParseIndexInjectsIndexDeps:
    """Tests for AC-1 through AC-5 of E15-F2-S1-T1.

    parse_index overwrites unit.dependencies from the BACKLOG.md index column.
    """

    def _make_index_and_wu(
        self,
        tmp_path: Path,
        index_deps_col: str,
        file_dep_rows: str = "",
    ) -> tuple[Path, Path]:
        """Write an index file and a work-unit file, return (index_path, backlog_dir)."""
        backlog_dir = tmp_path / "backlog"
        backlog_dir.mkdir(exist_ok=True)

        dep_table = (
            "## Dependencies\n\n"
            "| ID | Title | Status |\n"
            "|----|-------|--------|\n"
            f"{file_dep_rows}\n"
        ) if file_dep_rows else ""

        wu_file = backlog_dir / "E0-F1-S1-T1.md"
        wu_file.write_text(
            "# E0-F1-S1-T1: Task One\n\n"
            "## Status: in-queue\n\n"
            f"{dep_table}"
        )

        index = tmp_path / "BACKLOG.md"
        index.write_text(
            "## Full Work Unit Index\n\n"
            "| ID | Title | Type | Status | Dependencies | Repo | File Path |\n"
            "|-----|-------|------|--------|-------------|------|----------|\n"
            f"| E0-F1-S1-T1 | Task One | Task | in-queue | {index_deps_col} | git-repo | `backlog/E0-F1-S1-T1.md` |\n"
        )
        return index, backlog_dir

    def test_spec_e15_f2_s1_t1_ac1_index_dep_injected_when_no_file_dep_table(
        self, tmp_path: Path
    ) -> None:
        """AC-1: A unit with E9 in the index column but no dep table in the file
        has dependencies == ["E9"].

        Given: Index row has "E9" in the Dependencies column; work-unit file has
               no ## Dependencies section.
        When:  parse_index is called.
        Then:  The returned WorkUnit has dependencies == ["E9"].
        Spec:  E15-F2-S1-T1 AC-1.
        """
        index, backlog_dir = self._make_index_and_wu(
            tmp_path, index_deps_col="E9", file_dep_rows=""
        )
        parser = BacklogParser(backlog_root=backlog_dir, backlog_index=index)
        units = parser.parse_index()

        assert len(units) == 1
        assert units[0].dependencies == ["E9"]

    def test_spec_e15_f2_s1_t1_ac2_none_sentinel_produces_empty_deps(
        self, tmp_path: Path
    ) -> None:
        """AC-2: A unit with None in the index column has dependencies == [].

        Given: Index row has "None" in the Dependencies column.
        When:  parse_index is called.
        Then:  The returned WorkUnit has dependencies == [].
        Spec:  E15-F2-S1-T1 AC-2.
        """
        index, backlog_dir = self._make_index_and_wu(
            tmp_path, index_deps_col="None", file_dep_rows="| E9 | Epic 9 | done |"
        )
        parser = BacklogParser(backlog_root=backlog_dir, backlog_index=index)
        units = parser.parse_index()

        assert len(units) == 1
        assert units[0].dependencies == []

    def test_spec_e15_f2_s1_t1_ac2_dash_dash_sentinel_produces_empty_deps(
        self, tmp_path: Path
    ) -> None:
        """AC-2 variant: -- sentinel in the index column produces dependencies == [].

        Given: Index row has "--" in the Dependencies column.
        When:  parse_index is called.
        Then:  The returned WorkUnit has dependencies == [].
        Spec:  E15-F2-S1-T1 AC-2.
        """
        index, backlog_dir = self._make_index_and_wu(
            tmp_path, index_deps_col="--", file_dep_rows="| E9 | Epic 9 | done |"
        )
        parser = BacklogParser(backlog_root=backlog_dir, backlog_index=index)
        units = parser.parse_index()

        assert len(units) == 1
        assert units[0].dependencies == []

    def test_spec_e15_f2_s1_t1_ac3_index_dep_overrides_file_dep(
        self, tmp_path: Path
    ) -> None:
        """AC-3: Changing only the index column dep updates the unit's deps
        without modifying the work-unit file.

        Given: Index row has "E14-F1-S1-T1" in Dependencies column; file has
               "E9" in its ## Dependencies table.
        When:  parse_index is called.
        Then:  The returned WorkUnit has dependencies == ["E14-F1-S1-T1"]
               (index wins over file).
        Spec:  E15-F2-S1-T1 AC-3.
        """
        index, backlog_dir = self._make_index_and_wu(
            tmp_path,
            index_deps_col="E14-F1-S1-T1",
            file_dep_rows="| E9 | Epic 9 | done |",
        )
        parser = BacklogParser(backlog_root=backlog_dir, backlog_index=index)
        units = parser.parse_index()

        assert len(units) == 1
        assert units[0].dependencies == ["E14-F1-S1-T1"]

    def test_spec_e15_f2_s1_t1_ac4_parse_work_unit_file_still_reads_file_deps(
        self, tmp_path: Path
    ) -> None:
        """AC-4: parse_work_unit_file called directly still reads deps from the
        file dep table (unaffected by parse_index changes).

        Given: A work-unit file with "E9" in its ## Dependencies table.
        When:  parse_work_unit_file is called directly (not via parse_index).
        Then:  The returned WorkUnit has dependencies == ["E9"].
        Spec:  E15-F2-S1-T1 AC-4.
        """
        backlog_dir = tmp_path / "backlog"
        backlog_dir.mkdir()
        wu_file = backlog_dir / "E0-F1-S1-T1.md"
        wu_file.write_text(
            "# E0-F1-S1-T1: Task One\n\n"
            "## Status: in-queue\n\n"
            "## Dependencies\n\n"
            "| ID | Title | Status |\n"
            "|----|-------|--------|\n"
            "| E9 | Epic 9 | done |\n"
        )
        parser = BacklogParser(backlog_root=backlog_dir, backlog_index=tmp_path / "BACKLOG.md")
        unit = parser.parse_work_unit_file(wu_file)

        assert unit.dependencies == ["E9"]

    def test_spec_e15_f2_s1_t1_ac5_mismatch_logs_debug(
        self, tmp_path: Path, caplog: pytest.LogCaptureFixture
    ) -> None:
        """AC-5: Mismatch between index and file emits a DEBUG-level log line.

        Given: Index row has "E14-F1-S1-T1" in Dependencies column; file has
               "E9" in its ## Dependencies table.
        When:  parse_index is called with DEBUG logging enabled.
        Then:  A DEBUG-level log record referencing the mismatch is emitted.
        Spec:  E15-F2-S1-T1 AC-5.
        """
        import logging

        index, backlog_dir = self._make_index_and_wu(
            tmp_path,
            index_deps_col="E14-F1-S1-T1",
            file_dep_rows="| E9 | Epic 9 | done |",
        )
        parser = BacklogParser(backlog_root=backlog_dir, backlog_index=index)

        with caplog.at_level(logging.DEBUG, logger="devbench.backlog.parser"):
            units = parser.parse_index()

        assert len(units) == 1
        assert units[0].dependencies == ["E14-F1-S1-T1"]
        assert any(
            r.levelno == logging.DEBUG and "dep" in r.message.lower()
            for r in caplog.records
        ), f"Expected DEBUG dep-mismatch log. Got: {[r.message for r in caplog.records]}"

    def test_spec_e15_f2_s1_t1_ac1_multiple_deps_in_index_column(
        self, tmp_path: Path
    ) -> None:
        """AC-1 extended: Comma-separated IDs in index column are all injected.

        Given: Index row has "E9, E14-F1-S1-T1" in the Dependencies column.
        When:  parse_index is called.
        Then:  The returned WorkUnit has dependencies == ["E9", "E14-F1-S1-T1"].
        Spec:  E15-F2-S1-T1 AC-1.
        """
        index, backlog_dir = self._make_index_and_wu(
            tmp_path, index_deps_col="E9, E14-F1-S1-T1", file_dep_rows=""
        )
        parser = BacklogParser(backlog_root=backlog_dir, backlog_index=index)
        units = parser.parse_index()

        assert len(units) == 1
        assert units[0].dependencies == ["E9", "E14-F1-S1-T1"]


class TestNumericSortCandidates:
    """Tests for numeric-aware sort order in get_parallel_candidates.

    These tests verify AC-1 through AC-4 of E15-F1-S1-T4.
    """

    def _parser(self) -> BacklogParser:
        parser = BacklogParser.__new__(BacklogParser)
        parser._backlog_root = Path("/dev/null")
        parser._backlog_index = Path("/dev/null")
        return parser

    def _task(self, unit_id: str, status: WorkUnitStatus, deps: list[str] | None = None) -> WorkUnit:
        return WorkUnit(
            id=unit_id,
            title=unit_id,
            status=status,
            unit_type=WorkUnitType.TASK,
            file_path=Path("/dev/null"),
            repo="r",
            dependencies=deps or [],
        )

    def test_candidates_e9_before_e15(self) -> None:
        """AC-1: E9-* candidates appear before E15-* candidates when both are actionable.

        Given: Two IN_QUEUE tasks with IDs E9-F1-S1-T1 and E15-F1-S1-T1, both with
               no unsatisfied dependencies.
        When:  get_parallel_candidates is called.
        Then:  E9-F1-S1-T1 appears before E15-F1-S1-T1 in the result list.
        Spec:  E15 plan: earlier epics must be picked up before later ones.
        """
        parser = self._parser()
        e15_task = self._task("E15-F1-S1-T1", WorkUnitStatus.IN_QUEUE)
        e9_task = self._task("E9-F1-S1-T1", WorkUnitStatus.IN_QUEUE)
        candidates = parser.get_parallel_candidates([e15_task, e9_task])
        ids = [u.id for u in candidates]
        assert ids.index("E9-F1-S1-T1") < ids.index("E15-F1-S1-T1"), (
            f"Expected E9-F1-S1-T1 before E15-F1-S1-T1 but got order: {ids}"
        )

    def test_candidates_e1_before_e2_before_e10(self) -> None:
        """AC-2: E1-* candidates appear before E2-* and E10-* candidates.

        Given: Three IN_QUEUE tasks with IDs E10-F1-S1-T1, E2-F1-S1-T1, and E1-F1-S1-T1,
               all with no unsatisfied dependencies.
        When:  get_parallel_candidates is called.
        Then:  The order is E1-F1-S1-T1, E2-F1-S1-T1, E10-F1-S1-T1.
        Spec:  E15 plan: earlier epics must be picked up before later ones.
        """
        parser = self._parser()
        e10_task = self._task("E10-F1-S1-T1", WorkUnitStatus.IN_QUEUE)
        e2_task = self._task("E2-F1-S1-T1", WorkUnitStatus.IN_QUEUE)
        e1_task = self._task("E1-F1-S1-T1", WorkUnitStatus.IN_QUEUE)
        candidates = parser.get_parallel_candidates([e10_task, e2_task, e1_task])
        ids = [u.id for u in candidates]
        assert ids.index("E1-F1-S1-T1") < ids.index("E2-F1-S1-T1"), (
            f"Expected E1 before E2 but got order: {ids}"
        )
        assert ids.index("E2-F1-S1-T1") < ids.index("E10-F1-S1-T1"), (
            f"Expected E2 before E10 but got order: {ids}"
        )

    def test_in_progress_priority_over_in_queue_regardless_of_id(self) -> None:
        """AC-3: IN_PROGRESS tasks still take priority over IN_QUEUE tasks regardless of ID.

        Given: An IN_QUEUE task E1-F1-S1-T1 and an IN_PROGRESS task E15-F1-S1-T1,
               both with no unsatisfied dependencies.
        When:  get_parallel_candidates is called.
        Then:  E15-F1-S1-T1 (IN_PROGRESS) appears before E1-F1-S1-T1 (IN_QUEUE)
               despite having a numerically higher epic ID.
        Spec:  IN_PROGRESS tasks are returned before IN_QUEUE tasks (interrupted work resumed).
        """
        parser = self._parser()
        e1_queued = self._task("E1-F1-S1-T1", WorkUnitStatus.IN_QUEUE)
        e15_in_progress = self._task("E15-F1-S1-T1", WorkUnitStatus.IN_PROGRESS)
        candidates = parser.get_parallel_candidates([e1_queued, e15_in_progress])
        assert len(candidates) == 2
        assert candidates[0].id == "E15-F1-S1-T1", (
            f"Expected IN_PROGRESS E15 task first, got {candidates[0].id}"
        )
        assert candidates[0].status is WorkUnitStatus.IN_PROGRESS
        assert candidates[1].id == "E1-F1-S1-T1"
        assert candidates[1].status is WorkUnitStatus.IN_QUEUE

    def test_within_epic_task_order_preserved(self) -> None:
        """AC-4: Within the same epic, task ordering is preserved (T1 before T2 before T3).

        Given: Three IN_QUEUE tasks in the same epic: E9-F1-S1-T3, E9-F1-S1-T1, E9-F1-S1-T2,
               all with no unsatisfied dependencies.
        When:  get_parallel_candidates is called.
        Then:  The order is T1, T2, T3.
        Spec:  Within the same epic, earlier tasks are picked up first.
        """
        parser = self._parser()
        t3 = self._task("E9-F1-S1-T3", WorkUnitStatus.IN_QUEUE)
        t1 = self._task("E9-F1-S1-T1", WorkUnitStatus.IN_QUEUE)
        t2 = self._task("E9-F1-S1-T2", WorkUnitStatus.IN_QUEUE)
        candidates = parser.get_parallel_candidates([t3, t1, t2])
        ids = [u.id for u in candidates]
        assert ids.index("E9-F1-S1-T1") < ids.index("E9-F1-S1-T2"), (
            f"Expected T1 before T2 but got order: {ids}"
        )
        assert ids.index("E9-F1-S1-T2") < ids.index("E9-F1-S1-T3"), (
            f"Expected T2 before T3 but got order: {ids}"
        )


