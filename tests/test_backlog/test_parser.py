"""Tests for judges.backlog_parser module."""

from __future__ import annotations

from pathlib import Path

import pytest

from devbench.backlog.parser import BacklogParser
from devbench.backlog.work_unit import WorkUnit, WorkUnitStatus, WorkUnitType
from devbench.scope import ScopeFilter


class TestParseIndex:
    """Test parse_index returns WorkUnit list from BACKLOG.md."""

    def test_parse_index_from_actual_backlog(self, tmp_path: Path) -> None:
        """Parse the workspace BACKLOG.md file and verify results.

        Reads ``DEVBENCH_WORKSPACE_ROOT`` from the environment when set
        (so CI runs against the live backlog), otherwise falls back to
        the pytest-supplied ``tmp_path`` -- never to a hardcoded
        ``/tmp/test-workspace`` (TD-6). When neither location holds a
        BACKLOG.md, the test is skipped because there is nothing to
        parse.
        """
        import os

        env_workspace = os.environ.get("DEVBENCH_WORKSPACE_ROOT")
        workspace = Path(env_workspace) if env_workspace else tmp_path
        actual_backlog = workspace / "BACKLOG.md"
        if not actual_backlog.is_file():
            pytest.skip("Actual BACKLOG.md not found")

        parser = BacklogParser(
            backlog_root=workspace / "backlog",
            backlog_index=actual_backlog,
        )
        units = parser.parse_index()

        assert len(units) > 0
        for unit in units:
            assert unit.id, f"Unit has empty id: {unit}"
            assert unit.title, f"Unit has empty title: {unit}"
            assert isinstance(unit.status, WorkUnitStatus)
            assert isinstance(unit.unit_type, WorkUnitType)

    def test_parse_index_from_mock(self, mock_backlog_index: Path) -> None:
        workspace_root = mock_backlog_index.parent
        parser = BacklogParser(
            backlog_root=workspace_root / "backlog",
            backlog_index=mock_backlog_index,
        )
        units = parser.parse_index()

        task_units = [u for u in units if u.unit_type is WorkUnitType.TASK]
        assert len(task_units) == 3

        story_units = [u for u in units if u.unit_type is WorkUnitType.STORY]
        assert len(story_units) == 1

        feature_units = [u for u in units if u.unit_type is WorkUnitType.FEATURE]
        assert len(feature_units) == 1

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
        assert units[0].status.value == "In Progress"
        assert any("mismatch" in r.message.lower() for r in caplog.records)


class TestParseIndexFNFRetry:
    """parse_index does a single-shot synchronous retry on FileNotFoundError
    from parse_work_unit_file to absorb the atomic-rename / writer-window
    race that SDK-driven Write/Edit tools (outside BacklogManager) create
    when they overwrite a WU md file. On persistent failure the second
    attempt re-raises with the original missing path intact."""

    def _build_minimal_backlog(self, tmp_path: Path) -> tuple[BacklogParser, Path]:

        backlog_dir = tmp_path / "backlog"
        backlog_dir.mkdir()
        wu_file = backlog_dir / "E0-F1-S1-T1.md"
        wu_file.write_text("# E0-F1-S1-T1: Create Makefile\n\n## Status: in-queue\n")

        index = tmp_path / "BACKLOG.md"
        index.write_text(
            "## Full Work Unit Index\n\n"
            "| ID | Title | Type | Status | Dependencies | Repo | File Path |\n"
            "|-----|-------|------|--------|-------------|------|----------|\n"
            "| E0-F1-S1-T1 | Create Makefile | Task | in-queue | None | git-repo | `backlog/E0-F1-S1-T1.md` |\n"
        )
        return BacklogParser(backlog_root=backlog_dir, backlog_index=index), wu_file

    def test_transient_fnf_recovers_via_single_retry(self, tmp_path: Path) -> None:
        """First call raises FileNotFoundError (mimicking the writer-window
        race); second call returns the real WorkUnit; parse_index succeeds."""
        from unittest.mock import patch

        parser, wu_file = self._build_minimal_backlog(tmp_path)
        real = parser.parse_work_unit_file

        call_count = {"n": 0}

        def flaky(file_path: Path) -> WorkUnit:
            call_count["n"] += 1
            if call_count["n"] == 1:
                raise FileNotFoundError(2, "No such file or directory", str(file_path))
            return real(file_path)

        with patch.object(parser, "parse_work_unit_file", side_effect=flaky):
            units = parser.parse_index()

        assert len(units) == 1
        assert units[0].id == "E0-F1-S1-T1"
        assert call_count["n"] == 2, "Expected exactly one retry on transient FNF"

    def test_persistent_fnf_propagates_after_retry(self, tmp_path: Path) -> None:
        """Both calls raise FileNotFoundError -- parse_index re-raises so
        the operator sees a genuine missing-file diagnostic with the path
        preserved on the exception."""
        from unittest.mock import patch

        parser, wu_file = self._build_minimal_backlog(tmp_path)
        call_count = {"n": 0}
        fake_path = str(wu_file)

        def always_missing(file_path: Path) -> WorkUnit:
            call_count["n"] += 1
            raise FileNotFoundError(2, "No such file or directory", fake_path)

        with (
            patch.object(parser, "parse_work_unit_file", side_effect=always_missing),
            pytest.raises(FileNotFoundError) as excinfo,
        ):
            parser.parse_index()

        assert call_count["n"] == 2, "Expected exactly one retry before propagating"
        assert excinfo.value.filename == fake_path


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


class TestGetParallelCandidatesTopologicalOrder:
    """Issue #121 regression: candidates ordered by topological depth.

    A task with zero declared dependencies (depth 0) precedes a task with
    one transitive dependency (depth 1), which precedes a task with two
    (depth 2). Lexicographic ``id`` is the stable tiebreaker within a depth
    band so the order is reproducible. Topological depth is computed across
    the full backlog, not just among candidates -- the "build-order
    foundation first" intuition holds even when most ancestors are already
    ``done``.
    """

    @staticmethod
    def _make_parser() -> BacklogParser:
        parser = BacklogParser.__new__(BacklogParser)
        parser._backlog_root = Path("/tmp")
        parser._backlog_index = Path("/tmp/B.md")
        return parser

    @staticmethod
    def _task(
        id_: str,
        *,
        status: WorkUnitStatus = WorkUnitStatus.IN_QUEUE,
        deps: list[str] | None = None,
    ) -> WorkUnit:
        return WorkUnit(
            id=id_,
            title=id_,
            status=status,
            unit_type=WorkUnitType.TASK,
            file_path=Path("/dev/null"),
            repo="r",
            dependencies=deps or [],
        )

    def test_three_depth_levels_with_parallel_siblings(self) -> None:
        """Hand-built dep graph with three depth bands of candidates (AC-TEST-001):

        Background DAG (DONE foundations):
          F0 (DONE) <-- F1 (DONE), F2 (DONE)

        Candidates (all deps satisfied):
          - Depth 0: D1 (no deps)
          - Depth 2: E1 (deps on F1)            [F1's depth = 1]
          - Depth 2: E2 (deps on F2)            [parallel sibling at depth 2]
          - Depth 3: G1 (deps on F1, F2)        [deepest -- still all-DONE deps]

        Expected order: D1, E1, E2, G1.
        Lexicographic id is the stable tiebreaker within depth bands.
        """
        parser = self._make_parser()
        units = [
            self._task("G1", deps=["F1", "F2"]),
            self._task("F0", status=WorkUnitStatus.DONE),
            self._task("E2", deps=["F2"]),
            self._task("F1", status=WorkUnitStatus.DONE, deps=["F0"]),
            self._task("D1"),
            self._task("E1", deps=["F1"]),
            self._task("F2", status=WorkUnitStatus.DONE, deps=["F0"]),
        ]
        order = [u.id for u in parser.get_parallel_candidates(units)]
        assert order == ["D1", "E1", "E2", "G1"], (
            f"Topological depth order broken (issue #121). Got {order!r}; "
            "expected D1 (depth 0), E1 + E2 (depth 2 with stable id tiebreaker), "
            "G1 (depth 3)."
        )

    def test_done_ancestors_still_yield_correct_depth(self) -> None:
        """When the only ancestors are DONE, candidates still order by their
        depth in the full DAG -- foundation-first regardless of ancestor status."""
        parser = self._make_parser()
        units = [
            self._task("A1", status=WorkUnitStatus.DONE),
            self._task("B1", deps=["A1"]),
            self._task("C1", deps=["B1"]),
            self._task("B2"),
        ]
        order = [u.id for u in parser.get_parallel_candidates(units)]
        assert order == ["B2", "B1"], f"Got {order!r}"

    def test_in_progress_priority_beats_topological_depth(self) -> None:
        """The status priority (IN_PROGRESS first) wins over depth ordering."""
        parser = self._make_parser()
        units = [
            self._task("A1"),
            self._task("D1", status=WorkUnitStatus.IN_PROGRESS, deps=[]),
        ]
        order = [u.id for u in parser.get_parallel_candidates(units)]
        assert order == ["D1", "A1"], f"Got {order!r}"

    def test_unknown_dep_id_does_not_raise(self) -> None:
        """A typo'd / unresolvable dep id must not raise during depth
        computation. ``validate-backlog`` reports the typo upstream; the
        depth helper just must not crash. X1 with one declared dep gets
        depth = 0 + 1 = 1 (declared deps still increment the depth band even
        when unresolvable, so a foundation-first task with no deps still
        wins the lexicographic tiebreak)."""
        parser = self._make_parser()
        units = [
            self._task("X1", deps=["NONEXISTENT-T1"]),
            self._task("Y1"),
        ]
        order = [u.id for u in parser.get_parallel_candidates(units)]
        assert order == ["Y1", "X1"], f"Got {order!r}"

    def test_cycle_in_dep_chain_collapses_to_zero(self) -> None:
        """When the dep graph has a cycle A -> B -> A, ``_depth`` must return 0
        for the back-edge instead of recursing infinitely. A is the candidate;
        B is DONE so A's _deps_satisfied check passes. The depth traversal
        through B sees A in the visiting set and short-circuits at the cycle
        guard."""
        parser = self._make_parser()
        units = [
            self._task("A1", deps=["B1"]),
            self._task("B1", status=WorkUnitStatus.DONE, deps=["A1"]),
        ]
        order = [u.id for u in parser.get_parallel_candidates(units)]
        assert order == ["A1"]

    def test_self_dep_in_transitive_chain_is_skipped(self) -> None:
        """An indirect self-dep (B depends on B) does not block depth
        computation -- the self-dep edge is skipped so traversal continues."""
        parser = self._make_parser()
        units = [
            self._task("A1", deps=["B1"]),
            self._task("B1", status=WorkUnitStatus.DONE, deps=["B1"]),
        ]
        order = [u.id for u in parser.get_parallel_candidates(units)]
        assert order == ["A1"]

    def test_self_loop_does_not_cause_infinite_recursion(self) -> None:
        """A self-dep on an in-queue task fails ``_deps_satisfied`` (the dep is
        the unit itself and it's not DONE/DECLINED), so the unit is filtered
        out. The depth computation must still terminate without recursing
        infinitely. Validated by the call returning at all."""
        parser = self._make_parser()
        units = [self._task("Z1", deps=["Z1"])]
        order = [u.id for u in parser.get_parallel_candidates(units)]
        assert order == []


class TestParseStatusEdgeCases:
    """Test _parse_status with invalid input."""

    def test_raises_for_unrecognised_status(self) -> None:
        """Line 72: raises ValueError for unrecognised status string."""
        from devbench.backlog.parser import _parse_status

        with pytest.raises(ValueError, match="Unrecognised work-unit status"):
            _parse_status("invalid-status")


class TestInferTypeFromIdEdgeCases:
    """Test _infer_type_from_id with edge cases."""

    def test_returns_epic_for_placeholder_id(self) -> None:
        """Line 87: returns EPIC for the placeholder ID '--'."""
        from devbench.backlog.parser import _infer_type_from_id

        result = _infer_type_from_id("--")
        assert result is WorkUnitType.EPIC

    def test_raises_for_empty_id(self) -> None:
        """Empty unit_id raises ValueError before any segment parsing."""
        from devbench.backlog.parser import _infer_type_from_id

        with pytest.raises(ValueError, match="Cannot infer type from empty ID"):
            _infer_type_from_id("")

    def test_raises_for_unknown_segment_prefix(self) -> None:
        """Line 98: raises ValueError when last segment has unknown prefix."""
        from devbench.backlog.parser import _infer_type_from_id

        with pytest.raises(ValueError, match="Cannot infer work-unit type"):
            _infer_type_from_id("E0-F1-X1")


class TestParseIndexEdgeCases:
    """Test parse_index edge cases."""

    def test_raises_when_type_column_mismatches_inferred(self, tmp_path: Path) -> None:
        """Lines 174-175 (169 already covered): raises ValueError on type mismatch."""
        backlog_dir = tmp_path / "backlog"
        backlog_dir.mkdir()
        index_path = tmp_path / "BACKLOG.md"
        index_path.write_text(
            "# Backlog\n\n## Full Work Unit Index\n\n"
            "| ID | Title | Type | Status | Dependencies | Repo | File Path |\n"
            "|-----|-------|------|--------|-------------|------|-----------||\n"
            "| E0-F1-S1-T1 | Mismatched | Story | in-queue | None | git-repo | `backlog/E0-F1-S1-T1.md` |\n"
        )
        (backlog_dir / "E0-F1-S1-T1.md").write_text("# E0-F1-S1-T1: Mismatched\n\n## Status: in-queue\n")

        parser = BacklogParser(backlog_root=backlog_dir, backlog_index=index_path)
        with pytest.raises(ValueError, match="Type mismatch"):
            parser.parse_index()

    def test_raises_when_file_path_empty(self, tmp_path: Path) -> None:
        """Line 180: raises ValueError when work unit has no file path."""
        index_path = tmp_path / "BACKLOG.md"
        index_path.write_text(
            "# Backlog\n\n## Full Work Unit Index\n\n"
            "| ID | Title | Type | Status | Dependencies | Repo | File Path |\n"
            "|-----|-------|------|--------|-------------|------|-----------||\n"
            "| E0-F1-S1-T1 | No Path | Task | in-queue | None | git-repo |  |\n"
        )

        parser = BacklogParser(backlog_root=tmp_path / "backlog", backlog_index=index_path)
        with pytest.raises(ValueError, match="has no file path"):
            parser.parse_index()

    def test_raises_when_no_work_unit_rows(self, tmp_path: Path) -> None:
        """Lines 200-205: raises ValueError when index has no parseable rows."""
        index_path = tmp_path / "BACKLOG.md"
        index_path.write_text("# Backlog\n\n## Full Work Unit Index\n\nNo table here.\n")

        parser = BacklogParser(backlog_root=tmp_path / "backlog", backlog_index=index_path)
        with pytest.raises(ValueError, match="No work-unit rows found"):
            parser.parse_index()

    def test_skips_rows_with_unknown_type(self, tmp_path: Path) -> None:
        """Line 169: rows with non-WorkUnitType values are skipped (e.g. Doc, Template)."""
        backlog_dir = tmp_path / "backlog"
        backlog_dir.mkdir()
        index_path = tmp_path / "BACKLOG.md"
        index_path.write_text(
            "# Backlog\n\n## Full Work Unit Index\n\n"
            "| ID | Title | Type | Status | Dependencies | Repo | File Path |\n"
            "|-----|-------|------|--------|-------------|------|-----------||\n"
            "| E0-F1-S1-T1 | Valid Task | Task | in-queue | None | git-repo | `backlog/E0-F1-S1-T1.md` |\n"
            "| DOC-1 | Some Doc | Doc | in-queue | None | git-repo | `backlog/DOC-1.md` |\n"
        )
        (backlog_dir / "E0-F1-S1-T1.md").write_text("# E0-F1-S1-T1: Valid Task\n\n## Status: in-queue\n")
        (backlog_dir / "DOC-1.md").write_text("# DOC-1: Some Doc\n\n## Status: in-queue\n")

        parser = BacklogParser(backlog_root=backlog_dir, backlog_index=index_path)
        units = parser.parse_index()
        assert len(units) == 1
        assert units[0].id == "E0-F1-S1-T1"


class TestParseWorkUnitFileEdgeCases:
    """Test parse_work_unit_file edge cases."""

    def test_raises_when_no_status_line(self, tmp_path: Path) -> None:
        """Line 237: raises ValueError when work-unit file has no ## Status: line."""
        wu_file = tmp_path / "unit.md"
        wu_file.write_text("# E0-F1-S1-T1: Test Task\n\nSome content without status.\n")

        parser = BacklogParser(backlog_root=tmp_path, backlog_index=tmp_path / "B.md")
        with pytest.raises(ValueError, match="No '## Status:' line found"):
            parser.parse_work_unit_file(wu_file)


class TestDepsSatisfiedHierarchical:
    """E215: ``_deps_satisfied`` recurses into descendants for non-task deps."""

    @staticmethod
    def _wu(unit_id: str, status: WorkUnitStatus, unit_type: WorkUnitType, deps: list[str] | None = None) -> WorkUnit:
        return WorkUnit(
            id=unit_id,
            title=unit_id,
            status=status,
            unit_type=unit_type,
            file_path=Path(f"/dev/null/{unit_id}.md"),
            repo="test/repo",
            dependencies=deps or [],
        )

    def test_task_dep_unsatisfied_when_blocker_in_queue(self) -> None:
        units = [
            self._wu("E0-F1-S1-T1", WorkUnitStatus.IN_QUEUE, WorkUnitType.TASK),
            self._wu("E0-F1-S1-T2", WorkUnitStatus.IN_QUEUE, WorkUnitType.TASK, deps=["E0-F1-S1-T1"]),
        ]
        units_by_id = {u.id: u for u in units}
        assert BacklogParser._deps_satisfied(units[1], units_by_id) is False

    def test_task_dep_satisfied_when_blocker_done(self) -> None:
        units = [
            self._wu("E0-F1-S1-T1", WorkUnitStatus.DONE, WorkUnitType.TASK),
            self._wu("E0-F1-S1-T2", WorkUnitStatus.IN_QUEUE, WorkUnitType.TASK, deps=["E0-F1-S1-T1"]),
        ]
        units_by_id = {u.id: u for u in units}
        assert BacklogParser._deps_satisfied(units[1], units_by_id) is True

    def test_task_dep_satisfied_when_blocker_declined(self) -> None:
        units = [
            self._wu("E0-F1-S1-T1", WorkUnitStatus.DECLINED, WorkUnitType.TASK),
            self._wu("E0-F1-S1-T2", WorkUnitStatus.IN_QUEUE, WorkUnitType.TASK, deps=["E0-F1-S1-T1"]),
        ]
        units_by_id = {u.id: u for u in units}
        assert BacklogParser._deps_satisfied(units[1], units_by_id) is True

    def test_story_dep_unsatisfied_when_descendant_task_in_queue(self) -> None:
        units = [
            self._wu("E0-F1-S1", WorkUnitStatus.IN_QUEUE, WorkUnitType.STORY),
            self._wu("E0-F1-S1-T1", WorkUnitStatus.DONE, WorkUnitType.TASK),
            self._wu("E0-F1-S1-T2", WorkUnitStatus.IN_QUEUE, WorkUnitType.TASK),
            self._wu(
                "E0-F1-S2-T1",
                WorkUnitStatus.IN_QUEUE,
                WorkUnitType.TASK,
                deps=["E0-F1-S1"],
            ),
        ]
        units_by_id = {u.id: u for u in units}
        assert BacklogParser._deps_satisfied(units[3], units_by_id) is False

    def test_story_dep_satisfied_when_all_descendant_tasks_terminal(self) -> None:
        units = [
            self._wu("E0-F1-S1", WorkUnitStatus.DONE, WorkUnitType.STORY),
            self._wu("E0-F1-S1-T1", WorkUnitStatus.DONE, WorkUnitType.TASK),
            self._wu("E0-F1-S1-T2", WorkUnitStatus.DECLINED, WorkUnitType.TASK),
            self._wu(
                "E0-F1-S2-T1",
                WorkUnitStatus.IN_QUEUE,
                WorkUnitType.TASK,
                deps=["E0-F1-S1"],
            ),
        ]
        units_by_id = {u.id: u for u in units}
        assert BacklogParser._deps_satisfied(units[3], units_by_id) is True

    def test_feature_dep_walks_two_hierarchy_levels(self) -> None:
        units = [
            self._wu("E0-F1", WorkUnitStatus.IN_QUEUE, WorkUnitType.FEATURE),
            self._wu("E0-F1-S1", WorkUnitStatus.IN_QUEUE, WorkUnitType.STORY),
            self._wu("E0-F1-S1-T1", WorkUnitStatus.DONE, WorkUnitType.TASK),
            self._wu("E0-F1-S2", WorkUnitStatus.IN_QUEUE, WorkUnitType.STORY),
            self._wu("E0-F1-S2-T1", WorkUnitStatus.IN_QUEUE, WorkUnitType.TASK),
            self._wu("E0-F2-S1-T1", WorkUnitStatus.IN_QUEUE, WorkUnitType.TASK, deps=["E0-F1"]),
        ]
        units_by_id = {u.id: u for u in units}
        assert BacklogParser._deps_satisfied(units[5], units_by_id) is False

    def test_epic_dep_with_no_descendants_is_vacuously_satisfied(self) -> None:
        units = [
            self._wu("E9", WorkUnitStatus.IN_QUEUE, WorkUnitType.EPIC),
            self._wu("E0-F1-S1-T1", WorkUnitStatus.IN_QUEUE, WorkUnitType.TASK, deps=["E9"]),
        ]
        units_by_id = {u.id: u for u in units}
        assert BacklogParser._deps_satisfied(units[1], units_by_id) is True

    def test_unknown_dep_id_treated_as_satisfied(self) -> None:
        units = [
            self._wu("E0-F1-S1-T1", WorkUnitStatus.IN_QUEUE, WorkUnitType.TASK, deps=["E9-F9-S9-T9"]),
        ]
        units_by_id = {u.id: u for u in units}
        assert BacklogParser._deps_satisfied(units[0], units_by_id) is True

    def test_get_parallel_candidates_excludes_unit_with_unsatisfied_story_dep(self) -> None:
        units = [
            self._wu("E0-F1-S1-T1", WorkUnitStatus.IN_QUEUE, WorkUnitType.TASK),
            self._wu(
                "E0-F1-S2-T1",
                WorkUnitStatus.IN_QUEUE,
                WorkUnitType.TASK,
                deps=["E0-F1-S1"],
            ),
            self._wu("E0-F1-S1", WorkUnitStatus.IN_QUEUE, WorkUnitType.STORY),
        ]
        parser = BacklogParser(backlog_root=Path("/tmp"), backlog_index=Path("/tmp/BACKLOG.md"))
        candidates = parser.get_parallel_candidates(units)
        candidate_ids = {u.id for u in candidates}
        assert "E0-F1-S1-T1" in candidate_ids
        assert "E0-F1-S2-T1" not in candidate_ids


class TestParseStatusDraft:
    """Test that the parser recognises 'draft' as a valid work-unit status."""

    @pytest.mark.parametrize(
        "raw_input",
        ["draft", "DRAFT", "Draft", "  draft  "],
    )
    def test_parse_status_draft_normalised(self, raw_input: str) -> None:
        """_parse_status accepts 'draft' in any case and with leading/trailing whitespace."""
        from devbench.backlog.parser import _parse_status

        result = _parse_status(raw_input)
        assert result is WorkUnitStatus.DRAFT

    def test_draft_key_present_in_raw_status_map(self) -> None:
        """'draft' must be a key in _RAW_STATUS_TO_ENUM mapping to WorkUnitStatus.DRAFT."""
        from devbench.backlog.parser import _RAW_STATUS_TO_ENUM

        assert "draft" in _RAW_STATUS_TO_ENUM
        assert _RAW_STATUS_TO_ENUM["draft"] is WorkUnitStatus.DRAFT

    def test_parse_work_unit_file_draft_status(self, tmp_path: Path) -> None:
        """parse_work_unit_file returns DRAFT status when the file contains '## Status: draft'."""
        wu_file = tmp_path / "E0-F1-S1-T1.md"
        wu_file.write_text("# E0-F1-S1-T1: Draft Task\n\n## Status: draft\n")

        parser = BacklogParser(backlog_root=tmp_path, backlog_index=tmp_path / "B.md")
        wu = parser.parse_work_unit_file(wu_file)

        assert wu.status is WorkUnitStatus.DRAFT

    def test_parse_index_draft_status(self, tmp_path: Path) -> None:
        """parse_index parses a BACKLOG.md row with status 'draft' without error."""
        backlog_dir = tmp_path / "backlog"
        backlog_dir.mkdir()
        wu_file = backlog_dir / "E0-F1-S1-T1.md"
        wu_file.write_text("# E0-F1-S1-T1: Draft Task\n\n## Status: draft\n")

        index = tmp_path / "BACKLOG.md"
        index.write_text(
            "## Full Work Unit Index\n\n"
            "| ID | Title | Type | Status | Dependencies | Repo | File Path |\n"
            "|-----|-------|------|--------|-------------|------|----------|\n"
            "| E0-F1-S1-T1 | Draft Task | Task | draft | None | git-repo | `backlog/E0-F1-S1-T1.md` |\n"
        )

        parser = BacklogParser(backlog_root=backlog_dir, backlog_index=index)
        units = parser.parse_index()

        assert len(units) == 1
        assert units[0].status is WorkUnitStatus.DRAFT


class TestGetParallelCandidatesWithScope:
    """Tests for get_parallel_candidates(scope=) -- AC-190-12.

    When a ``ScopeFilter`` is provided, only work units whose IDs are in
    ``scope.expanded_ids`` are returned.  When ``scope=None`` (the default),
    the existing behaviour is unchanged.
    """

    @staticmethod
    def _make_parser() -> BacklogParser:
        parser = BacklogParser.__new__(BacklogParser)
        parser._backlog_root = Path("/tmp")
        parser._backlog_index = Path("/tmp/B.md")
        return parser

    @staticmethod
    def _task(
        id_: str,
        *,
        status: WorkUnitStatus = WorkUnitStatus.IN_QUEUE,
        deps: list[str] | None = None,
    ) -> WorkUnit:
        return WorkUnit(
            id=id_,
            title=id_,
            status=status,
            unit_type=WorkUnitType.TASK,
            file_path=Path("/dev/null"),
            repo="r",
            dependencies=deps or [],
        )

    def test_no_scope_returns_all_candidates(self) -> None:
        """Default behaviour (scope=None) is unchanged -- all actionable tasks returned."""
        parser = self._make_parser()
        units = [
            self._task("E0-F1-S1-T1"),
            self._task("E0-F1-S1-T2"),
        ]
        candidates = parser.get_parallel_candidates(units, scope=None)
        assert len(candidates) == 2
        candidate_ids = {u.id for u in candidates}
        assert candidate_ids == {"E0-F1-S1-T1", "E0-F1-S1-T2"}

    def test_scope_filters_to_only_matching_ids(self) -> None:
        """When scope is provided, only WUs in scope.expanded_ids are returned."""
        parser = self._make_parser()
        units = [
            self._task("E0-F1-S1-T1"),
            self._task("E0-F1-S1-T2"),
            self._task("E0-F1-S1-T3"),
        ]
        scope = ScopeFilter(
            include=["E0-F1-S1-T1"],
            exclude=[],
            expanded_ids={"E0-F1-S1-T1"},
        )
        candidates = parser.get_parallel_candidates(units, scope=scope)
        assert len(candidates) == 1
        assert candidates[0].id == "E0-F1-S1-T1"

    def test_scope_empty_expanded_ids_returns_empty_list(self) -> None:
        """When scope.expanded_ids is empty, no candidates are returned."""
        parser = self._make_parser()
        units = [
            self._task("E0-F1-S1-T1"),
            self._task("E0-F1-S1-T2"),
        ]
        scope = ScopeFilter(
            include=[],
            exclude=[],
            expanded_ids=set(),
        )
        candidates = parser.get_parallel_candidates(units, scope=scope)
        assert candidates == []

    def test_scope_filters_but_deps_still_enforced(self) -> None:
        """Scope filtering is applied after dependency checking -- a task in scope
        with unsatisfied deps is still excluded."""
        parser = self._make_parser()
        units = [
            self._task("E0-F1-S1-T1", status=WorkUnitStatus.IN_QUEUE),
            self._task("E0-F1-S1-T2", deps=["E0-F1-S1-T1"]),
        ]
        scope = ScopeFilter(
            include=["E0-F1-S1"],
            exclude=[],
            expanded_ids={"E0-F1-S1-T1", "E0-F1-S1-T2"},
        )
        candidates = parser.get_parallel_candidates(units, scope=scope)
        candidate_ids = {u.id for u in candidates}
        assert "E0-F1-S1-T1" in candidate_ids
        assert "E0-F1-S1-T2" not in candidate_ids

    def test_scope_preserves_topological_sort_order(self) -> None:
        """When scope is active, the returned candidates are still sorted by
        (status_priority, depth, id) -- scope only narrows the set, not the order."""
        parser = self._make_parser()
        done = self._task("E0-F1-S1-T1", status=WorkUnitStatus.DONE)
        t2 = self._task("E0-F1-S1-T2", deps=["E0-F1-S1-T1"])
        t3 = self._task("E0-F1-S1-T3", deps=["E0-F1-S1-T1"])
        units = [done, t3, t2]
        scope = ScopeFilter(
            include=["E0-F1-S1-T2", "E0-F1-S1-T3"],
            exclude=[],
            expanded_ids={"E0-F1-S1-T2", "E0-F1-S1-T3"},
        )
        candidates = parser.get_parallel_candidates(units, scope=scope)
        assert len(candidates) == 2
        assert candidates[0].id == "E0-F1-S1-T2"
        assert candidates[1].id == "E0-F1-S1-T3"

    def test_scope_excludes_non_matching_actionable_tasks(self) -> None:
        """Actionable tasks NOT in scope.expanded_ids are excluded from the result."""
        parser = self._make_parser()
        units = [
            self._task("E1-F1-S1-T1"),
            self._task("E2-F1-S1-T1"),
            self._task("E3-F1-S1-T1"),
        ]
        scope = ScopeFilter(
            include=["E2"],
            exclude=[],
            expanded_ids={"E2-F1-S1-T1"},
        )
        candidates = parser.get_parallel_candidates(units, scope=scope)
        assert len(candidates) == 1
        assert candidates[0].id == "E2-F1-S1-T1"

    @pytest.mark.parametrize(
        "scope_ids,expected_ids",
        [
            ({"E0-F1-S1-T1"}, ["E0-F1-S1-T1"]),
            ({"E0-F1-S1-T2"}, ["E0-F1-S1-T2"]),
            ({"E0-F1-S1-T1", "E0-F1-S1-T2"}, ["E0-F1-S1-T1", "E0-F1-S1-T2"]),
            (set(), []),
        ],
    )
    def test_scope_filter_parametrized(
        self,
        scope_ids: set[str],
        expected_ids: list[str],
    ) -> None:
        """Parametrised: various scope sets return exactly the matching in-queue tasks."""
        parser = self._make_parser()
        units = [
            self._task("E0-F1-S1-T1"),
            self._task("E0-F1-S1-T2"),
        ]
        scope = ScopeFilter(include=[], exclude=[], expanded_ids=scope_ids)
        candidates = parser.get_parallel_candidates(units, scope=scope)
        result_ids = sorted(u.id for u in candidates)
        assert result_ids == sorted(expected_ids)
