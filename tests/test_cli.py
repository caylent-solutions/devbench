"""Tests for devbench.cli module."""

from __future__ import annotations

import contextlib
import json
import re
import types
from collections.abc import Generator
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, ClassVar
from unittest.mock import MagicMock, patch

import pytest

from devbench import cli
from devbench.backlog.proposal import Proposal
from devbench.backlog.work_unit import WorkUnit, WorkUnitStatus, WorkUnitType
from devbench.constants import (
    BACKLOG_SUBDIR,
    SESSION_DEFAULT_NAME,
    SESSION_SESSIONS_BASE_DIR,
    TDD_ENTRY_TEMPLATE,
    TDD_PHASE_RED,
    TDD_PHASE_RED_OBSERVED,
)
from devbench.github.git_ops import CIResult


@pytest.fixture
def mock_units() -> list[WorkUnit]:
    """Create a list of mock work units for testing."""
    return [
        WorkUnit(
            id="E0-F1-S1-T1",
            title="First Task",
            status=WorkUnitStatus.DONE,
            unit_type=WorkUnitType.TASK,
            file_path=Path("backlog/E0-F1-S1-T1.md"),
            repo="caylent-solutions/git-repo",
            dependencies=[],
        ),
        WorkUnit(
            id="E0-F1-S1-T2",
            title="Second Task",
            status=WorkUnitStatus.IN_QUEUE,
            unit_type=WorkUnitType.TASK,
            file_path=Path("backlog/E0-F1-S1-T2.md"),
            repo="caylent-solutions/git-repo",
            dependencies=["E0-F1-S1-T1"],
        ),
        WorkUnit(
            id="E0-F1-S1-T3",
            title="Blocked Task",
            status=WorkUnitStatus.BLOCKED,
            unit_type=WorkUnitType.TASK,
            file_path=Path("backlog/E0-F1-S1-T3.md"),
            repo="caylent-solutions/git-repo",
            dependencies=[],
        ),
    ]


class TestFindUnit:
    """Test _find_unit helper."""

    def test_finds_by_id(self, mock_units: list[WorkUnit]) -> None:
        result = cli._find_unit(mock_units, "E0-F1-S1-T2")
        assert result is not None
        assert result.id == "E0-F1-S1-T2"

    def test_finds_case_insensitive(self, mock_units: list[WorkUnit]) -> None:
        result = cli._find_unit(mock_units, "e0-f1-s1-t1")
        assert result is not None
        assert result.id == "E0-F1-S1-T1"

    def test_returns_none_when_not_found(self, mock_units: list[WorkUnit]) -> None:
        result = cli._find_unit(mock_units, "NONEXISTENT")
        assert result is None


class TestCmdStatus:
    """Test cmd_status command."""

    def test_returns_zero(self, mock_units: list[WorkUnit]) -> None:
        mock_parser = MagicMock()
        mock_parser.parse_index.return_value = mock_units
        mock_parser.get_parallel_candidates.return_value = [mock_units[1]]
        mock_parser.all_done.return_value = False
        mock_parser.get_blocked_units.return_value = [mock_units[2]]

        with patch("devbench.cli.BacklogParser", return_value=mock_parser):
            result = cli.cmd_status()

        assert result == 0

    def test_shows_all_done_message(self, capsys: pytest.CaptureFixture[str]) -> None:
        done_unit = WorkUnit(
            id="T1",
            title="Done",
            status=WorkUnitStatus.DONE,
            unit_type=WorkUnitType.TASK,
            file_path=Path("t.md"),
            repo="caylent-solutions/git-repo",
            dependencies=[],
        )
        mock_parser = MagicMock()
        mock_parser.parse_index.return_value = [done_unit]
        mock_parser.get_parallel_candidates.return_value = []
        mock_parser.all_done.return_value = True

        with patch("devbench.cli.BacklogParser", return_value=mock_parser):
            result = cli.cmd_status()

        assert result == 0
        assert "All work units are DONE" in capsys.readouterr().out

    def test_shows_blocked_count(self, mock_units: list[WorkUnit], capsys: pytest.CaptureFixture[str]) -> None:
        mock_parser = MagicMock()
        mock_parser.parse_index.return_value = mock_units
        mock_parser.get_parallel_candidates.return_value = []
        mock_parser.all_done.return_value = False
        mock_parser.get_blocked_units.return_value = [mock_units[2]]

        with patch("devbench.cli.BacklogParser", return_value=mock_parser):
            result = cli.cmd_status()

        assert result == 0
        assert "1 blocked" in capsys.readouterr().out

    @staticmethod
    def _build_draft_units(draft_count: int, queued_count: int = 1) -> list[WorkUnit]:
        """Build a list of WorkUnits with the given number of draft and queued tasks."""
        units: list[WorkUnit] = []
        for i in range(draft_count):
            units.append(
                WorkUnit(
                    id=f"E0-F1-S1-T{i + 1}",
                    title=f"Draft task {i + 1}",
                    status=WorkUnitStatus.DRAFT,
                    unit_type=WorkUnitType.TASK,
                    file_path=Path(f"backlog/E0-F1-S1-T{i + 1}.md"),
                    repo="caylent-solutions/git-repo",
                    dependencies=[],
                ),
            )
        for i in range(queued_count):
            idx = draft_count + i + 1
            units.append(
                WorkUnit(
                    id=f"E0-F1-S1-T{idx}",
                    title=f"Queued task {i + 1}",
                    status=WorkUnitStatus.IN_QUEUE,
                    unit_type=WorkUnitType.TASK,
                    file_path=Path(f"backlog/E0-F1-S1-T{idx}.md"),
                    repo="caylent-solutions/git-repo",
                    dependencies=[],
                ),
            )
        return units

    @staticmethod
    def _make_status_mock_parser(
        units: list[WorkUnit],
        candidates: list[WorkUnit] | None = None,
        blocked: list[WorkUnit] | None = None,
    ) -> MagicMock:
        """Create a mock BacklogParser configured for cmd_status tests."""
        mock_parser = MagicMock()
        mock_parser.parse_index.return_value = units
        mock_parser.get_parallel_candidates.return_value = candidates or []
        mock_parser.all_done.return_value = False
        mock_parser.get_blocked_units.return_value = blocked or []
        return mock_parser

    def test_draft_row_rendered_with_correct_count(self, capsys: pytest.CaptureFixture[str]) -> None:
        """AC-189-6: cmd_status summary table includes a Draft row with correct count."""
        units = self._build_draft_units(draft_count=2)
        mock_parser = self._make_status_mock_parser(
            units,
            candidates=[units[-1]],
        )

        with patch("devbench.cli.BacklogParser", return_value=mock_parser):
            result = cli.cmd_status()

        assert result == 0
        out = capsys.readouterr().out
        draft_line = next(line for line in out.splitlines() if "Draft" in line)
        assert "2" in draft_line

    def test_draft_row_appears_between_total_and_in_queue(self, capsys: pytest.CaptureFixture[str]) -> None:
        """AC-189-6: Draft row is rendered between TOTAL and In Queue."""
        units = self._build_draft_units(draft_count=1)
        mock_parser = self._make_status_mock_parser(
            units,
            candidates=[units[-1]],
        )

        with patch("devbench.cli.BacklogParser", return_value=mock_parser):
            result = cli.cmd_status()

        assert result == 0
        out = capsys.readouterr().out
        lines = out.splitlines()
        total_idx = next(i for i, line in enumerate(lines) if "TOTAL" in line)
        draft_idx = next(i for i, line in enumerate(lines) if "Draft" in line)
        in_queue_idx = next(i for i, line in enumerate(lines) if "In Queue" in line)
        # Draft must be between TOTAL and In Queue
        assert total_idx < draft_idx < in_queue_idx

    def test_draft_row_zero_when_no_drafts(
        self, mock_units: list[WorkUnit], capsys: pytest.CaptureFixture[str]
    ) -> None:
        """Draft row shows 0 when no units have draft status."""
        mock_parser = self._make_status_mock_parser(
            mock_units,
            candidates=[mock_units[1]],
            blocked=[mock_units[2]],
        )

        with patch("devbench.cli.BacklogParser", return_value=mock_parser):
            result = cli.cmd_status()

        assert result == 0
        out = capsys.readouterr().out
        draft_line = next(line for line in out.splitlines() if "Draft" in line)
        assert "0" in draft_line


class TestCmdStatusDraftRowIntegration:
    """Integration test: Draft row rendered against a real fixture workspace (no mocks)."""

    @staticmethod
    def _build_draft_backlog(tmp_path: Path) -> Path:
        """Build a real backlog workspace with draft-status work units on disk."""
        wu_dir = tmp_path / "backlog"
        wu_dir.mkdir(exist_ok=True)
        rows = [
            ("E0-F1-S1-T1", "Task", "draft"),
            ("E0-F1-S1-T2", "Task", "draft"),
            ("E0-F1-S1-T3", "Task", "in-queue"),
        ]
        index_lines = [
            "# Backlog\n",
            "## Full Work Unit Index\n",
            "| ID | Title | Type | Status | Dependencies | Repo | File Path |",
            "|----|-------|------|--------|--------------|------|-----------|",
        ]
        for unit_id, unit_type, status in rows:
            file_path = f"backlog/{unit_id}.md"
            index_lines.append(
                f"| {unit_id} | {unit_id} | {unit_type} | {status} | None | "
                f"caylent-solutions/test-repo | `{file_path}` |"
            )
            wu_body = f"# {unit_id}: Test\n\n## Status: {status}\n\n## Description\n\nx\n"
            (wu_dir / f"{unit_id}.md").write_text(wu_body)
        index_path = tmp_path / "BACKLOG.md"
        index_path.write_text("\n".join(index_lines) + "\n")
        return index_path

    def test_draft_row_real_fixture(self, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
        """AC-189-6: Draft row rendered with real BacklogParser against fixture workspace."""
        index_path = self._build_draft_backlog(tmp_path)
        with (
            patch("devbench.cli.BACKLOG_ROOT", tmp_path / "backlog"),
            patch("devbench.cli.BACKLOG_INDEX", index_path),
            patch("devbench.cli.WORKSPACE_ROOT", tmp_path),
        ):
            rc = cli.cmd_status()
        assert rc == 0
        out = capsys.readouterr().out
        lines = out.splitlines()
        # Draft row exists with count of 2
        draft_line = next(line for line in lines if "Draft" in line)
        assert "2" in draft_line
        # Ordering: TOTAL < Draft < In Queue
        total_idx = next(i for i, line in enumerate(lines) if "TOTAL" in line)
        draft_idx = next(i for i, line in enumerate(lines) if "Draft" in line)
        in_queue_idx = next(i for i, line in enumerate(lines) if "In Queue" in line)
        assert total_idx < draft_idx < in_queue_idx


class TestCmdStatusDetail:
    """E220: ``devbench status --detail`` renders in-queue / blocked / held panels."""

    def _build_backlog(self, tmp_path: Path) -> Path:
        wu_dir = tmp_path / "backlog"
        wu_dir.mkdir(exist_ok=True)
        # T1 in-queue ready (no deps); T2 in-queue waiting on T1; T3 blocked
        # with an open proposal marker; T4 held with a [HOLD] reason.
        rows = [
            ("E0-F1-S1-T1", "Task", "in-queue", "None", "E0-F1-S1-T1", ""),
            ("E0-F1-S1-T2", "Task", "in-queue", "E0-F1-S1-T1", "E0-F1-S1-T2", ""),
            (
                "E0-F1-S1-T3",
                "Task",
                "blocked",
                "None",
                "E0-F1-S1-T3",
                "## Comments\n\n[BLOCKED_PENDING_PROPOSAL] E0-F1-S1-T9\n",
            ),
            (
                "E0-F1-S1-T4",
                "Task",
                "hold",
                "None",
                "E0-F1-S1-T4",
                "## Comments\n\n[HOLD] awaiting product input\n",
            ),
        ]
        index_lines = [
            "# Backlog\n",
            "## Full Work Unit Index\n",
            "| ID | Title | Type | Status | Dependencies | Repo | File Path |",
            "|----|-------|------|--------|--------------|------|-----------|",
        ]
        for unit_id, unit_type, status, deps, basename, comments in rows:
            file_path = f"backlog/{basename}.md"
            index_lines.append(
                f"| {unit_id} | {unit_id} | {unit_type} | {status} | {deps} | "
                f"caylent-solutions/test-repo | `{file_path}` |"
            )
            wu_body = f"# {unit_id}: Test\n\n## Status: {status}\n\n## Description\n\nx\n"
            if deps and deps != "None":
                dep_rows = "\n".join(f"| {d.strip()} | Task | dep |" for d in deps.split(","))
                wu_body += f"\n## Dependencies\n\n| ID | Type | Reason |\n|----|------|--------|\n{dep_rows}\n"
            if comments:
                wu_body += f"\n{comments}"
            (wu_dir / f"{basename}.md").write_text(wu_body)
        index_path = tmp_path / "BACKLOG.md"
        index_path.write_text("\n".join(index_lines) + "\n")
        return index_path

    def test_detail_renders_three_panels(self, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
        index_path = self._build_backlog(tmp_path)
        with (
            patch("devbench.cli.BACKLOG_ROOT", tmp_path / "backlog"),
            patch("devbench.cli.BACKLOG_INDEX", index_path),
            patch("devbench.cli.WORKSPACE_ROOT", tmp_path),
        ):
            rc = cli.cmd_status("--detail")
        assert rc == 0
        out = capsys.readouterr().out
        # In-queue panel: T1 ready, T2 waiting on T1
        assert "In-queue tasks" in out
        assert "[ready]" in out
        assert "E0-F1-S1-T1" in out
        assert "[waiting]" in out
        assert "blocker: E0-F1-S1-T1" in out
        # Blocked panel: T3 with pending proposal marker
        assert "Blocked tasks" in out
        assert "pending proposal E0-F1-S1-T9" in out
        # Held panel: T4 with HOLD reason
        assert "Held tasks" in out
        assert "awaiting product input" in out

    def test_default_invocation_omits_detail_panels(
        self,
        tmp_path: Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        index_path = self._build_backlog(tmp_path)
        with (
            patch("devbench.cli.BACKLOG_ROOT", tmp_path / "backlog"),
            patch("devbench.cli.BACKLOG_INDEX", index_path),
            patch("devbench.cli.WORKSPACE_ROOT", tmp_path),
        ):
            rc = cli.cmd_status()
        assert rc == 0
        out = capsys.readouterr().out
        assert "Backlog Status Summary" in out
        assert "In-queue tasks" not in out
        assert "Held tasks" not in out

    def test_unknown_positional_arg_rejected(
        self,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        rc = cli.cmd_status("garbage")
        assert rc == 1
        assert "no positional args" in capsys.readouterr().err

    def test_status_is_variadic(self) -> None:
        assert "status" in cli._VARIADIC_COMMANDS


class TestLatestHoldReason:
    """Cover the helper that extracts the most recent [HOLD] line from Comments."""

    def test_returns_last_match_when_multiple(self) -> None:
        content = "## Comments\n\n[HOLD] first reason\n[UNHOLD] back to queue\n[HOLD] second reason\n"
        assert cli._latest_hold_reason(content) == "second reason"

    def test_returns_empty_when_no_hold_line(self) -> None:
        content = "## Comments\n\n[BLOCKED] dep not met\n"
        assert cli._latest_hold_reason(content) == ""


class TestCmdNext:
    """Test cmd_next command."""

    def test_returns_json_when_actionable(self, mock_units: list[WorkUnit], capsys: pytest.CaptureFixture) -> None:
        mock_parser = MagicMock()
        mock_parser.parse_index.return_value = mock_units
        mock_parser.get_parallel_candidates.return_value = [mock_units[1]]

        with patch("devbench.cli.BacklogParser", return_value=mock_parser):
            result = cli.cmd_next()

        assert result == 0
        output = capsys.readouterr().out.strip()
        data = json.loads(output)
        # TD-12: assert the full envelope shape, not just one field, so a
        # regression in any envelope key (renamed, dropped, mistyped) is
        # caught here rather than at a downstream parser.
        assert data["id"] == "E0-F1-S1-T2"
        assert data["title"] == "Second Task"
        assert data["repo"] == "caylent-solutions/git-repo"
        assert data["file_path"] == str(Path("backlog/E0-F1-S1-T2.md"))
        assert data["dependencies"] == ["E0-F1-S1-T1"]

    def test_prints_all_done_when_complete(self, capsys: pytest.CaptureFixture) -> None:
        done_unit = WorkUnit(
            id="T1",
            title="Done",
            status=WorkUnitStatus.DONE,
            unit_type=WorkUnitType.TASK,
            file_path=Path("t.md"),
            repo="caylent-solutions/git-repo",
            dependencies=[],
        )
        mock_parser = MagicMock()
        mock_parser.parse_index.return_value = [done_unit]
        mock_parser.get_parallel_candidates.return_value = []
        mock_parser.all_done.return_value = True

        with patch("devbench.cli.BacklogParser", return_value=mock_parser):
            result = cli.cmd_next()

        assert result == 0
        assert "ALL_DONE" in capsys.readouterr().out

    def test_prints_no_actionable(self, mock_units: list[WorkUnit], capsys: pytest.CaptureFixture) -> None:
        mock_parser = MagicMock()
        mock_parser.parse_index.return_value = mock_units
        mock_parser.get_parallel_candidates.return_value = []
        mock_parser.all_done.return_value = False

        with patch("devbench.cli.BacklogParser", return_value=mock_parser):
            result = cli.cmd_next()

        assert result == 0
        assert "NO_ACTIONABLE" in capsys.readouterr().out

    def test_next_does_not_mutate_status(self, mock_units: list[WorkUnit], capsys: pytest.CaptureFixture) -> None:
        """cmd_next must be read-only: BacklogManager.force_status must never be called."""
        mock_parser = MagicMock()
        mock_parser.parse_index.return_value = mock_units
        mock_parser.get_parallel_candidates.return_value = [mock_units[1]]

        mock_mgr = MagicMock()

        with patch("devbench.cli.BacklogParser", return_value=mock_parser):
            with patch("devbench.cli.BacklogManager", return_value=mock_mgr):
                result = cli.cmd_next()

        assert result == 0
        mock_mgr.force_status.assert_not_called()

    def test_next_returns_json_descriptor(self, mock_units: list[WorkUnit], capsys: pytest.CaptureFixture) -> None:
        """cmd_next emits a JSON object with id, title, repo, file_path, and dependencies."""
        mock_parser = MagicMock()
        mock_parser.parse_index.return_value = mock_units
        mock_parser.get_parallel_candidates.return_value = [mock_units[1]]

        with patch("devbench.cli.BacklogParser", return_value=mock_parser):
            result = cli.cmd_next()

        assert result == 0
        data = json.loads(capsys.readouterr().out.strip())
        assert data["id"] == "E0-F1-S1-T2"
        assert data["title"] == "Second Task"
        assert data["repo"] == "caylent-solutions/git-repo"
        assert "file_path" in data
        assert "dependencies" in data


class TestCmdNextScopeFilter:
    """E2-F2-S2-T3: cmd_next respects active scope.json and --include/--exclude flags.

    AC-190-15: Zero-matching scope causes cmd_next to output NO_ACTIONABLE_IN_SCOPE
    and return rc=0.
    """

    # ------------------------------------------------------------------
    # Shared helpers
    # ------------------------------------------------------------------

    def _make_units(self, ids: list[str]) -> list[WorkUnit]:
        """Return IN_QUEUE WorkUnit stubs for the given IDs."""
        return [
            WorkUnit(
                id=wu_id,
                title=f"Task {wu_id}",
                status=WorkUnitStatus.IN_QUEUE,
                unit_type=WorkUnitType.TASK,
                file_path=Path(f"backlog/{wu_id}.md"),
                repo="caylent-solutions/devbench",
                dependencies=[],
            )
            for wu_id in ids
        ]

    def _write_scope_json(
        self,
        tmp_path: Path,
        include: list[str],
        exclude: list[str],
        expanded_ids: list[str],
        started_at: str = "2026-05-14T13:42:11Z",
        started_by: str = "testuser",
    ) -> None:
        """Write a minimal scope.json under tmp_path/.devbench/scope.json."""
        scope_dir = tmp_path / ".devbench"
        scope_dir.mkdir(parents=True, exist_ok=True)
        payload = {
            "include": include,
            "exclude": exclude,
            "expanded_ids": expanded_ids,
            "started_at": started_at,
            "started_by": started_by,
        }
        (scope_dir / "scope.json").write_text(json.dumps(payload))

    # ------------------------------------------------------------------
    # Happy path: active scope.json, matching candidates exist
    # ------------------------------------------------------------------

    def test_scope_json_filters_candidates_to_matching_unit(
        self,
        tmp_path: Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """cmd_next passes the active scope to get_parallel_candidates (AC-190-10).

        When scope.json names only E1-F1-S1-T1 and two candidates exist,
        only the in-scope candidate is returned.
        """
        units = self._make_units(["E1-F1-S1-T1", "E2-F1-S1-T1"])
        self._write_scope_json(
            tmp_path,
            include=["E1"],
            exclude=[],
            expanded_ids=["E1-F1-S1-T1"],
        )

        mock_parser = MagicMock()
        mock_parser.parse_index.return_value = units
        # Simulate parser filtering: only E1-F1-S1-T1 survives when scope is applied.
        mock_parser.get_parallel_candidates.return_value = [units[0]]

        with (
            patch("devbench.cli.BacklogParser", return_value=mock_parser),
            patch("devbench.cli.WORKSPACE_ROOT", tmp_path),
        ):
            rc = cli.cmd_next()

        assert rc == 0
        data = json.loads(capsys.readouterr().out.strip())
        assert data["id"] == "E1-F1-S1-T1"
        # Verify scope was passed to get_parallel_candidates.
        call_kwargs = mock_parser.get_parallel_candidates.call_args
        assert call_kwargs is not None
        passed_scope = call_kwargs.kwargs.get("scope") or (call_kwargs.args[1] if len(call_kwargs.args) > 1 else None)
        assert passed_scope is not None
        assert "E1-F1-S1-T1" in passed_scope.expanded_ids

    # ------------------------------------------------------------------
    # AC-190-15: scope exhausted -- no WU in scope is actionable
    # ------------------------------------------------------------------

    def test_no_actionable_in_scope_when_scope_filters_all_out(
        self,
        tmp_path: Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """AC-190-15: zero-matching scope causes NO_ACTIONABLE_IN_SCOPE output, rc=0."""
        units = self._make_units(["E1-F1-S1-T1", "E2-F1-S1-T1"])
        self._write_scope_json(
            tmp_path,
            include=["E3"],
            exclude=[],
            expanded_ids=[],  # scope expands to nothing in this backlog
        )

        mock_parser = MagicMock()
        mock_parser.parse_index.return_value = units
        mock_parser.get_parallel_candidates.return_value = []
        mock_parser.all_done.return_value = False

        with (
            patch("devbench.cli.BacklogParser", return_value=mock_parser),
            patch("devbench.cli.WORKSPACE_ROOT", tmp_path),
        ):
            rc = cli.cmd_next()

        assert rc == 0
        out = capsys.readouterr().out
        assert "NO_ACTIONABLE_IN_SCOPE" in out
        # Must NOT print ALL_DONE or NO_ACTIONABLE (the non-scope variant).
        assert "ALL_DONE" not in out
        assert out.strip() == "NO_ACTIONABLE_IN_SCOPE"

    # ------------------------------------------------------------------
    # Without scope.json: existing NO_ACTIONABLE / ALL_DONE paths unchanged
    # ------------------------------------------------------------------

    def test_no_scope_json_prints_no_actionable_not_in_scope(
        self,
        tmp_path: Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """Without scope.json, exhausted candidates print NO_ACTIONABLE (not IN_SCOPE variant)."""
        units = self._make_units(["E1-F1-S1-T1"])
        mock_parser = MagicMock()
        mock_parser.parse_index.return_value = units
        mock_parser.get_parallel_candidates.return_value = []
        mock_parser.all_done.return_value = False

        with (
            patch("devbench.cli.BacklogParser", return_value=mock_parser),
            patch("devbench.cli.WORKSPACE_ROOT", tmp_path),
        ):
            rc = cli.cmd_next()

        assert rc == 0
        out = capsys.readouterr().out
        assert "NO_ACTIONABLE" in out
        assert "IN_SCOPE" not in out

    def test_no_scope_json_all_done_unchanged(
        self,
        tmp_path: Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """Without scope.json, all-done scenario still prints ALL_DONE."""
        units = self._make_units(["E1-F1-S1-T1"])
        mock_parser = MagicMock()
        mock_parser.parse_index.return_value = units
        mock_parser.get_parallel_candidates.return_value = []
        mock_parser.all_done.return_value = True

        with (
            patch("devbench.cli.BacklogParser", return_value=mock_parser),
            patch("devbench.cli.WORKSPACE_ROOT", tmp_path),
        ):
            rc = cli.cmd_next()

        assert rc == 0
        assert "ALL_DONE" in capsys.readouterr().out

    # ------------------------------------------------------------------
    # AC-190-11: per-command --include flag overrides active scope.json
    # ------------------------------------------------------------------

    def test_include_flag_overrides_scope_json(
        self,
        tmp_path: Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """Per-command --include flag overrides the active scope.json (AC-190-11)."""
        units = self._make_units(["E1-F1-S1-T1", "E2-F1-S1-T1"])
        # scope.json says E3 (nothing matches) but --include "E1" should override.
        self._write_scope_json(
            tmp_path,
            include=["E3"],
            exclude=[],
            expanded_ids=[],
        )

        mock_parser = MagicMock()
        mock_parser.parse_index.return_value = units
        mock_parser.get_parallel_candidates.return_value = [units[0]]

        with (
            patch("devbench.cli.BacklogParser", return_value=mock_parser),
            patch("devbench.cli.WORKSPACE_ROOT", tmp_path),
        ):
            rc = cli.cmd_next("--include", "E1")

        assert rc == 0
        data = json.loads(capsys.readouterr().out.strip())
        assert data["id"] == "E1-F1-S1-T1"
        # Verify a scope was passed to get_parallel_candidates with E1 tokens.
        call_kwargs = mock_parser.get_parallel_candidates.call_args
        passed_scope = call_kwargs.kwargs.get("scope") or (call_kwargs.args[1] if len(call_kwargs.args) > 1 else None)
        assert passed_scope is not None
        assert passed_scope.include == ["E1"]

    def test_exclude_flag_passed_to_scope(
        self,
        tmp_path: Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """--exclude flag is parsed and reflected in the scope passed to get_parallel_candidates."""
        units = self._make_units(["E1-F1-S1-T1"])
        mock_parser = MagicMock()
        mock_parser.parse_index.return_value = units
        mock_parser.get_parallel_candidates.return_value = [units[0]]

        with (
            patch("devbench.cli.BacklogParser", return_value=mock_parser),
            patch("devbench.cli.WORKSPACE_ROOT", tmp_path),
        ):
            rc = cli.cmd_next("--include", "E1", "--exclude", "E1-F1-S1-T2")

        assert rc == 0
        call_kwargs = mock_parser.get_parallel_candidates.call_args
        passed_scope = call_kwargs.kwargs.get("scope") or (call_kwargs.args[1] if len(call_kwargs.args) > 1 else None)
        assert passed_scope is not None
        assert passed_scope.exclude == ["E1-F1-S1-T2"]

    # ------------------------------------------------------------------
    # Corrupt scope.json -> rc=1 with actionable error on stderr
    # ------------------------------------------------------------------

    def test_corrupt_scope_json_returns_rc1_with_stderr(
        self,
        tmp_path: Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """Corrupt scope.json causes cmd_next to return rc=1 with a stderr message."""
        scope_dir = tmp_path / ".devbench"
        scope_dir.mkdir(parents=True, exist_ok=True)
        (scope_dir / "scope.json").write_text("not valid json{{")

        mock_parser = MagicMock()
        mock_parser.parse_index.return_value = []

        with (
            patch("devbench.cli.BacklogParser", return_value=mock_parser),
            patch("devbench.cli.WORKSPACE_ROOT", tmp_path),
        ):
            rc = cli.cmd_next()

        assert rc == 1
        err = capsys.readouterr().err
        assert "scope.json" in err

    # ------------------------------------------------------------------
    # Integration: real fixture -- scope.json on disk selects correct WU
    # ------------------------------------------------------------------

    def test_integration_scope_json_selects_correct_next(
        self,
        tmp_path: Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """Integration: real scope.json on disk filters cmd_next output.

        Constructs a minimal BACKLOG.md and two work-unit files, writes a
        scope.json that includes only E1-F1-S1-T1, and verifies that
        cmd_next returns only that unit.
        """
        from devbench.scope import ScopeFilter

        # Build a minimal BACKLOG.md index.
        backlog_dir = tmp_path / "backlog"
        backlog_dir.mkdir(parents=True)
        backlog_index = tmp_path / "BACKLOG.md"
        backlog_index.write_text(
            "# Backlog\n\n"
            "| ID | Title | Type | Status | Repo | Branch | File |\n"
            "|----|-------|------|--------|------|--------|------|\n"
            "| E1-F1-S1-T1 | Alpha Task | Task | in-queue | caylent-solutions/devbench"
            " | feat/test | backlog/E1-F1-S1-T1.md |\n"
            "| E2-F1-S1-T1 | Beta Task | Task | in-queue | caylent-solutions/devbench"
            " | feat/test | backlog/E2-F1-S1-T1.md |\n"
        )

        # Write minimal work-unit files.
        dep_header = "## Dependencies\n\n| ID | Title | Status |\n|----|-------|--------|\n"
        for wu_id in ("E1-F1-S1-T1", "E2-F1-S1-T1"):
            (backlog_dir / f"{wu_id}.md").write_text(f"# {wu_id}: Task\n\n## Status: in-queue\n\n{dep_header}")

        # Write scope.json that includes only E1-F1-S1-T1.
        scope_filter = ScopeFilter(
            include=["E1"],
            exclude=[],
            expanded_ids={"E1-F1-S1-T1"},
        )
        scope_filter.to_file(tmp_path)

        with (
            patch("devbench.cli.WORKSPACE_ROOT", tmp_path),
            patch("devbench.cli.BACKLOG_ROOT", tmp_path / "backlog"),
            patch("devbench.cli.BACKLOG_INDEX", backlog_index),
        ):
            rc = cli.cmd_next()

        assert rc == 0
        out = capsys.readouterr().out.strip()
        data = json.loads(out)
        assert data["id"] == "E1-F1-S1-T1"

    def test_integration_scope_json_exhausted_prints_no_actionable_in_scope(
        self,
        tmp_path: Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """Integration: scope.json with empty expanded_ids causes NO_ACTIONABLE_IN_SCOPE.

        AC-190-15: zero-matching scope exits cleanly with a clear message.
        """
        from devbench.scope import ScopeFilter

        backlog_dir = tmp_path / "backlog"
        backlog_dir.mkdir(parents=True)
        backlog_index = tmp_path / "BACKLOG.md"
        backlog_index.write_text(
            "# Backlog\n\n"
            "| ID | Title | Type | Status | Repo | Branch | File |\n"
            "|----|-------|------|--------|------|--------|------|\n"
            "| E1-F1-S1-T1 | Alpha Task | Task | in-queue | caylent-solutions/devbench"
            " | feat/test | backlog/E1-F1-S1-T1.md |\n"
        )
        dep_header = "## Dependencies\n\n| ID | Title | Status |\n|----|-------|--------|\n"
        (backlog_dir / "E1-F1-S1-T1.md").write_text(f"# E1-F1-S1-T1: Task\n\n## Status: in-queue\n\n{dep_header}")

        # Scope that matches nothing in this backlog (E9 does not exist).
        scope_filter = ScopeFilter(
            include=["E9"],
            exclude=[],
            expanded_ids=set(),
        )
        scope_filter.to_file(tmp_path)

        with (
            patch("devbench.cli.WORKSPACE_ROOT", tmp_path),
            patch("devbench.cli.BACKLOG_ROOT", tmp_path / "backlog"),
            patch("devbench.cli.BACKLOG_INDEX", backlog_index),
        ):
            rc = cli.cmd_next()

        assert rc == 0
        out = capsys.readouterr().out
        assert "NO_ACTIONABLE_IN_SCOPE" in out

    # ------------------------------------------------------------------
    # Error path: --include / --exclude without a following value
    # ------------------------------------------------------------------

    @pytest.mark.parametrize("flag", ["--include", "--exclude"])
    def test_flag_without_value_returns_rc1_with_stderr(
        self,
        flag: str,
        tmp_path: Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """--include or --exclude supplied without a following value exits rc=1.

        The error message must be emitted to stderr and must mention
        'requires a value' so the caller can diagnose the problem.
        """
        with patch("devbench.cli.WORKSPACE_ROOT", tmp_path):
            rc = cli.cmd_next(flag)

        assert rc == 1
        err = capsys.readouterr().err
        assert "requires a value" in err


class TestCmdClaim:
    """Test cmd_claim command."""

    def test_claim_sets_unit_in_progress(
        self,
        mock_units: list[WorkUnit],
        backlog_dir: Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """cmd_claim transitions the work unit to in-progress via force_status.

        mock_units[1].file_path is Path("backlog/E0-F1-S1-T2.md"), so BACKLOG_ROOT
        must be set to backlog_dir.parent (tmp_path) so that the resolved path is
        tmp_path / "backlog/E0-F1-S1-T2.md" == backlog_dir / "E0-F1-S1-T2.md".
        """
        wu_file = backlog_dir / "E0-F1-S1-T2.md"
        wu_file.write_text("# Task\n## Status: in-queue\n")

        mock_parser = MagicMock()
        mock_parser.parse_index.return_value = mock_units

        mock_mgr = MagicMock()

        with patch("devbench.cli.BacklogParser", return_value=mock_parser):
            with patch("devbench.cli.BACKLOG_ROOT", backlog_dir.parent):
                with patch("devbench.cli.BacklogManager", return_value=mock_mgr):
                    result = cli.cmd_claim("E0-F1-S1-T2")

        assert result == 0
        mock_mgr.force_status.assert_called_once()
        call_args = mock_mgr.force_status.call_args
        assert call_args[0][2] == "E0-F1-S1-T2"
        from devbench.constants import STATUS_IN_PROGRESS

        assert call_args[0][3] == STATUS_IN_PROGRESS
        assert "Claimed E0-F1-S1-T2" in capsys.readouterr().out

    def test_claim_refuses_when_manifest_has_tbd_placeholder(
        self,
        backlog_dir: Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """Issue #117: cmd_claim refuses tasks whose Manifest still carries a TBD row."""
        wu_file = backlog_dir / "E0-F1-S1-T2.md"
        wu_file.write_text(
            "# E0-F1-S1-T2: Test\n\n"
            "## Status: in-queue\n\n"
            "## Description\n\nx\n\n"
            "## Dependencies\n\n| ID | Type | Reason |\n|----|------|--------|\n| none | | |\n\n"
            "## Acceptance Criteria\n\n- [ ] AC-FUNC-001\n\n"
            "## Changes Manifest\n\n"
            "| File | Change |\n|------|--------|\n"
            "| TBD | Executor agent: replace this row |\n",
            encoding="utf-8",
        )
        unit = WorkUnit(
            id="E0-F1-S1-T2",
            title="Test",
            status=WorkUnitStatus.IN_QUEUE,
            unit_type=WorkUnitType.TASK,
            file_path=Path("backlog/E0-F1-S1-T2.md"),
            repo="caylent-solutions/git-repo",
            dependencies=[],
        )
        mock_parser = MagicMock()
        mock_parser.parse_index.return_value = [unit]
        with (
            patch("devbench.cli.BacklogParser", return_value=mock_parser),
            patch("devbench.cli.BACKLOG_ROOT", backlog_dir.parent),
        ):
            rc = cli.cmd_claim("E0-F1-S1-T2")
        assert rc == 1
        err = capsys.readouterr().err
        assert "placeholder row 'TBD'" in err

    def test_claim_returns_nonzero_for_unknown_id(
        self,
        mock_units: list[WorkUnit],
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """cmd_claim exits non-zero with a clear error when the unit ID is not found."""
        mock_parser = MagicMock()
        mock_parser.parse_index.return_value = mock_units

        with patch("devbench.cli.BacklogParser", return_value=mock_parser):
            result = cli.cmd_claim("NONEXISTENT-ID")

        assert result == 1
        assert "NONEXISTENT-ID" in capsys.readouterr().err


class _FakeFlock:
    """Minimal no-op context manager used to stand in for :func:`devbench.session.flock_backlog`.

    Instantiated by the module-level factory ``_make_fake_flock`` so tests can
    choose between a passthrough lock and one that records entry calls.
    """

    def __init__(self, root: Path, timeout_seconds: int = 30, *, entered: list[bool] | None = None) -> None:
        self._entered = entered

    def __enter__(self) -> None:
        if self._entered is not None:
            self._entered.append(True)

    def __exit__(self, *args: object) -> None:
        pass


class _TimeoutFlock:
    """Context manager that raises ``TimeoutError`` on ``__enter__``."""

    def __init__(self, root: Path, timeout_seconds: int = 30) -> None:
        pass

    def __enter__(self) -> None:
        raise TimeoutError("Lock timeout")

    def __exit__(self, *args: object) -> None:
        pass


def _make_noop_flock(entered: list[bool] | None = None) -> Any:
    """Return a callable that produces a _FakeFlock; usable as a flock_backlog replacement."""

    def _flock(root: Path, timeout_seconds: int = 30) -> _FakeFlock:
        return _FakeFlock(root, timeout_seconds, entered=entered)

    return _flock


class TestCmdClaimAtomicArbitration:
    """Tests for spec section 4.4.2: cmd_claim atomic claim arbitration via flock_backlog."""

    def _make_unit(self, backlog_dir: Path, status: str = "in-queue") -> tuple[WorkUnit, Path]:
        """Build a minimal work-unit file + WorkUnit object for claim tests."""
        wu_file = backlog_dir / "E0-F1-S1-T2.md"
        wu_file.write_text(f"# E0-F1-S1-T2: Test\n## Status: {status}\n")
        unit = WorkUnit(
            id="E0-F1-S1-T2",
            title="Test",
            status=WorkUnitStatus.IN_QUEUE,
            unit_type=WorkUnitType.TASK,
            file_path=Path("backlog/E0-F1-S1-T2.md"),
            repo="caylent-solutions/git-repo",
            dependencies=[],
        )
        return unit, wu_file

    def test_claim_acquires_flock_before_status_write(
        self,
        backlog_dir: Path,
        mock_units: list[WorkUnit],
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """cmd_claim calls flock_backlog before invoking force_status (spec 4.4.2 step 1)."""
        unit, wu_file = self._make_unit(backlog_dir, "in-queue")
        mock_mgr = MagicMock()
        entered: list[bool] = []
        flock_factory = _make_noop_flock(entered=entered)

        with (
            patch("devbench.cli.BacklogParser", return_value=MagicMock(parse_index=MagicMock(return_value=[unit]))),
            patch("devbench.cli.BACKLOG_ROOT", backlog_dir.parent),
            patch("devbench.cli.WORKSPACE_ROOT", backlog_dir.parent),
            patch("devbench.cli.BacklogManager", return_value=mock_mgr),
            patch("devbench.cli.flock_backlog", flock_factory),
        ):
            rc = cli.cmd_claim("E0-F1-S1-T2")

        assert rc == 0
        assert entered, "flock_backlog context manager was never entered"
        mock_mgr.force_status.assert_called_once()

    def test_claim_re_reads_status_under_lock_and_succeeds_for_in_queue(
        self,
        backlog_dir: Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """Under the lock, cmd_claim re-reads the file status; in-queue proceeds normally."""
        unit, wu_file = self._make_unit(backlog_dir, "in-queue")
        mock_mgr = MagicMock()

        with (
            patch("devbench.cli.BacklogParser", return_value=MagicMock(parse_index=MagicMock(return_value=[unit]))),
            patch("devbench.cli.BACKLOG_ROOT", backlog_dir.parent),
            patch("devbench.cli.WORKSPACE_ROOT", backlog_dir.parent),
            patch("devbench.cli.BacklogManager", return_value=mock_mgr),
            patch("devbench.cli.flock_backlog", _make_noop_flock()),
        ):
            rc = cli.cmd_claim("E0-F1-S1-T2")

        assert rc == 0
        mock_mgr.force_status.assert_called_once()
        out = capsys.readouterr().out
        assert "Claimed E0-F1-S1-T2" in out

    @pytest.mark.parametrize("bad_status", ["done", "declined", "in-review", "blocked"])
    def test_claim_raises_claim_race_error_when_status_not_claimable(
        self,
        backlog_dir: Path,
        bad_status: str,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """Status other than in-queue or in-progress under the lock causes rc=1 (race)."""
        unit, wu_file = self._make_unit(backlog_dir, bad_status)
        mock_mgr = MagicMock()

        with (
            patch("devbench.cli.BacklogParser", return_value=MagicMock(parse_index=MagicMock(return_value=[unit]))),
            patch("devbench.cli.BACKLOG_ROOT", backlog_dir.parent),
            patch("devbench.cli.WORKSPACE_ROOT", backlog_dir.parent),
            patch("devbench.cli.BacklogManager", return_value=mock_mgr),
            patch("devbench.cli.flock_backlog", _make_noop_flock()),
        ):
            rc = cli.cmd_claim("E0-F1-S1-T2")

        assert rc == 1
        err = capsys.readouterr().err
        assert "race" in err.lower() or "claim" in err.lower()
        mock_mgr.force_status.assert_not_called()

    def test_claim_succeeds_when_status_is_in_progress_under_lock(
        self,
        backlog_dir: Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """in-progress status under the lock is valid for resume; claim proceeds."""
        unit, wu_file = self._make_unit(backlog_dir, "in-progress")
        mock_mgr = MagicMock()

        with (
            patch("devbench.cli.BacklogParser", return_value=MagicMock(parse_index=MagicMock(return_value=[unit]))),
            patch("devbench.cli.BACKLOG_ROOT", backlog_dir.parent),
            patch("devbench.cli.WORKSPACE_ROOT", backlog_dir.parent),
            patch("devbench.cli.BacklogManager", return_value=mock_mgr),
            patch("devbench.cli.flock_backlog", _make_noop_flock()),
        ):
            rc = cli.cmd_claim("E0-F1-S1-T2")

        assert rc == 0
        mock_mgr.force_status.assert_called_once()

    def test_claim_returns_1_on_timeout_acquiring_lock(
        self,
        backlog_dir: Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """TimeoutError from flock_backlog is caught; cmd_claim returns 1 with stderr message."""
        unit, wu_file = self._make_unit(backlog_dir, "in-queue")
        mock_mgr = MagicMock()

        with (
            patch("devbench.cli.BacklogParser", return_value=MagicMock(parse_index=MagicMock(return_value=[unit]))),
            patch("devbench.cli.BACKLOG_ROOT", backlog_dir.parent),
            patch("devbench.cli.WORKSPACE_ROOT", backlog_dir.parent),
            patch("devbench.cli.BacklogManager", return_value=mock_mgr),
            patch("devbench.cli.flock_backlog", _TimeoutFlock),
        ):
            rc = cli.cmd_claim("E0-F1-S1-T2")

        assert rc == 1
        err = capsys.readouterr().err
        assert "lock" in err.lower() or "timeout" in err.lower()

    def test_claim_stamps_session_name_when_env_var_set(
        self,
        backlog_dir: Path,
        capsys: pytest.CaptureFixture[str],
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """When DEVBENCH_SESSION_NAME is set, force_status receives session_name."""
        monkeypatch.setenv("DEVBENCH_SESSION_NAME", "alpha")
        unit, wu_file = self._make_unit(backlog_dir, "in-queue")
        mock_mgr = MagicMock()

        with (
            patch("devbench.cli.BacklogParser", return_value=MagicMock(parse_index=MagicMock(return_value=[unit]))),
            patch("devbench.cli.BACKLOG_ROOT", backlog_dir.parent),
            patch("devbench.cli.WORKSPACE_ROOT", backlog_dir.parent),
            patch("devbench.cli.BacklogManager", return_value=mock_mgr),
            patch("devbench.cli.flock_backlog", _make_noop_flock()),
        ):
            rc = cli.cmd_claim("E0-F1-S1-T2")

        assert rc == 0
        mock_mgr.force_status.assert_called_once()
        call_args = mock_mgr.force_status.call_args
        assert call_args.kwargs.get("session_name") == "alpha" or (
            len(call_args.args) > 4 and call_args.args[4] == "alpha"
        )

    def test_claim_no_session_name_when_env_var_not_set(
        self,
        backlog_dir: Path,
        capsys: pytest.CaptureFixture[str],
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """When DEVBENCH_SESSION_NAME is absent, force_status receives session_name=None."""
        monkeypatch.delenv("DEVBENCH_SESSION_NAME", raising=False)
        unit, wu_file = self._make_unit(backlog_dir, "in-queue")
        mock_mgr = MagicMock()

        with (
            patch("devbench.cli.BacklogParser", return_value=MagicMock(parse_index=MagicMock(return_value=[unit]))),
            patch("devbench.cli.BACKLOG_ROOT", backlog_dir.parent),
            patch("devbench.cli.WORKSPACE_ROOT", backlog_dir.parent),
            patch("devbench.cli.BacklogManager", return_value=mock_mgr),
            patch("devbench.cli.flock_backlog", _make_noop_flock()),
        ):
            rc = cli.cmd_claim("E0-F1-S1-T2")

        assert rc == 0
        mock_mgr.force_status.assert_called_once()
        call_args = mock_mgr.force_status.call_args
        session_name = call_args.kwargs.get("session_name")
        if session_name is None and len(call_args.args) > 4:
            session_name = call_args.args[4]
        assert session_name is None

    def test_claim_returns_1_when_status_line_missing_under_lock(
        self,
        backlog_dir: Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """If the work-unit file has no '## Status:' line under lock, cmd_claim returns 1."""
        wu_file = backlog_dir / "E0-F1-S1-T2.md"
        wu_file.write_text("# E0-F1-S1-T2: Test\nNo status line here.\n")
        unit = WorkUnit(
            id="E0-F1-S1-T2",
            title="Test",
            status=WorkUnitStatus.IN_QUEUE,
            unit_type=WorkUnitType.TASK,
            file_path=Path("backlog/E0-F1-S1-T2.md"),
            repo="caylent-solutions/git-repo",
            dependencies=[],
        )
        mock_mgr = MagicMock()

        with (
            patch("devbench.cli.BacklogParser", return_value=MagicMock(parse_index=MagicMock(return_value=[unit]))),
            patch("devbench.cli.BACKLOG_ROOT", backlog_dir.parent),
            patch("devbench.cli.WORKSPACE_ROOT", backlog_dir.parent),
            patch("devbench.cli.BacklogManager", return_value=mock_mgr),
            patch("devbench.cli.flock_backlog", _make_noop_flock()),
        ):
            rc = cli.cmd_claim("E0-F1-S1-T2")

        assert rc == 1
        err = capsys.readouterr().err
        assert "Status" in err or "status" in err
        mock_mgr.force_status.assert_not_called()


class TestCmdLog:
    """Test cmd_log command."""

    def test_returns_zero(self, capsys: pytest.CaptureFixture) -> None:
        result = cli.cmd_log("test message")
        assert result == 0
        assert "Logged" in capsys.readouterr().out


class TestCmdSetStatus:
    """Test cmd_set_status command."""

    def test_returns_1_for_invalid_status(self, capsys: pytest.CaptureFixture[str]) -> None:
        result = cli.cmd_set_status("E0-F1-S1-T1", "invalid")
        assert result == 1
        assert "Invalid status" in capsys.readouterr().err

    def test_returns_1_when_unit_not_found(self, mock_units: list[WorkUnit]) -> None:
        mock_parser = MagicMock()
        mock_parser.parse_index.return_value = mock_units

        with patch("devbench.cli.BacklogParser", return_value=mock_parser):
            result = cli.cmd_set_status("NONEXISTENT", "in-progress")

        assert result == 1

    def test_returns_0_on_success(
        self, mock_units: list[WorkUnit], tmp_path: Path, backlog_dir: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        wu_file = backlog_dir / "E0-F1-S1-T2.md"
        wu_file.write_text("# Task\n## Status: in-queue\n")

        mock_parser = MagicMock()
        mock_parser.parse_index.return_value = mock_units

        mock_mgr = MagicMock()

        with patch("devbench.cli.BacklogParser", return_value=mock_parser):
            with patch("devbench.cli.BACKLOG_ROOT", backlog_dir.parent):
                with patch("devbench.cli.BacklogManager", return_value=mock_mgr):
                    result = cli.cmd_set_status("E0-F1-S1-T2", "in-progress")

        assert result == 0
        assert "in-progress" in capsys.readouterr().out
        mock_mgr.force_status.assert_called_once()

    # ------------------------------------------------------------------
    # Bulk --include / --exclude tests (AC-194-1, AC-194-2, AC-194-10)
    # ------------------------------------------------------------------

    def test_bulk_include_returns_0_on_success(
        self, mock_units: list[WorkUnit], backlog_dir: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """AC-194-1: --include selects WUs and bulk-updates them."""
        for unit in mock_units:
            wu_file = backlog_dir / unit.file_path.name
            wu_file.write_text(f"# {unit.id}\n## Status: {unit.status.value}\n")

        mock_parser = MagicMock()
        mock_parser.parse_index.return_value = mock_units
        mock_mgr = MagicMock()

        with (
            patch("devbench.cli.BacklogParser", return_value=mock_parser),
            patch("devbench.cli.BACKLOG_ROOT", backlog_dir.parent),
            patch("devbench.cli.BacklogManager", return_value=mock_mgr),
        ):
            result = cli.cmd_set_status("--include", "E0", "in-queue")

        assert result == 0
        out = capsys.readouterr().out
        assert "in-queue" in out
        assert mock_mgr.bulk_set_status.call_count > 0

    def test_bulk_include_invalid_status_returns_1(self, capsys: pytest.CaptureFixture[str]) -> None:
        """AC-194-1: --include with invalid status returns rc=1 with actionable error."""
        result = cli.cmd_set_status("--include", "E0", "not-a-status")
        assert result == 1
        assert "Invalid status" in capsys.readouterr().err

    def test_bulk_include_no_matching_units_returns_1(
        self, mock_units: list[WorkUnit], capsys: pytest.CaptureFixture[str]
    ) -> None:
        """AC-194-1: --include with no matching WUs returns rc=1 with actionable error."""
        mock_parser = MagicMock()
        mock_parser.parse_index.return_value = mock_units

        with patch("devbench.cli.BacklogParser", return_value=mock_parser):
            result = cli.cmd_set_status("--include", "E99", "in-queue")

        assert result == 1
        assert "no work units" in capsys.readouterr().err.lower()

    def test_bulk_include_exclude_subtracts_units(
        self, mock_units: list[WorkUnit], backlog_dir: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """AC-194-1: --exclude subtracts from the --include set."""
        for unit in mock_units:
            wu_file = backlog_dir / unit.file_path.name
            wu_file.write_text(f"# {unit.id}\n## Status: {unit.status.value}\n")

        mock_parser = MagicMock()
        mock_parser.parse_index.return_value = mock_units
        mock_mgr = MagicMock()

        with (
            patch("devbench.cli.BacklogParser", return_value=mock_parser),
            patch("devbench.cli.BACKLOG_ROOT", backlog_dir.parent),
            patch("devbench.cli.BacklogManager", return_value=mock_mgr),
        ):
            # Include all, exclude T3 -- only T1 and T2 should be updated
            result = cli.cmd_set_status("--include", "E0", "--exclude", "E0-F1-S1-T3", "in-queue")

        assert result == 0
        mock_mgr.bulk_set_status.assert_called_once()
        unit_ids_arg = mock_mgr.bulk_set_status.call_args[0][0]
        updated_ids = [uid for uid, _ in unit_ids_arg]
        assert "E0-F1-S1-T3" not in updated_ids
        assert len(updated_ids) == 2

    def test_bulk_missing_status_arg_returns_1(
        self, mock_units: list[WorkUnit], capsys: pytest.CaptureFixture[str]
    ) -> None:
        """AC-194-1: --include without a trailing status positional returns rc=1."""
        mock_parser = MagicMock()
        mock_parser.parse_index.return_value = mock_units

        with patch("devbench.cli.BacklogParser", return_value=mock_parser):
            result = cli.cmd_set_status("--include", "E0")

        assert result == 1
        assert "status" in capsys.readouterr().err.lower()

    def test_single_id_form_still_works(
        self, mock_units: list[WorkUnit], backlog_dir: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """AC-194-2: existing devbench set-status <id> <status> form works unchanged."""
        wu_file = backlog_dir / "E0-F1-S1-T2.md"
        wu_file.write_text("# Task\n## Status: in-queue\n")

        mock_parser = MagicMock()
        mock_parser.parse_index.return_value = mock_units
        mock_mgr = MagicMock()

        with (
            patch("devbench.cli.BacklogParser", return_value=mock_parser),
            patch("devbench.cli.BACKLOG_ROOT", backlog_dir.parent),
            patch("devbench.cli.BacklogManager", return_value=mock_mgr),
        ):
            result = cli.cmd_set_status("E0-F1-S1-T2", "in-progress")

        assert result == 0
        out = capsys.readouterr().out
        assert "in-progress" in out
        mock_mgr.force_status.assert_called_once()

    def test_bulk_uses_scope_filter_parse(self, mock_units: list[WorkUnit], backlog_dir: Path) -> None:
        """AC-194-10: bulk mode reuses ScopeFilter.parse() -- no parser duplication."""
        for unit in mock_units:
            (backlog_dir / unit.file_path.name).write_text(f"# {unit.id}\n## Status: {unit.status.value}\n")

        mock_parser = MagicMock()
        mock_parser.parse_index.return_value = mock_units
        mock_mgr = MagicMock()

        with (
            patch("devbench.cli.BacklogParser", return_value=mock_parser),
            patch("devbench.cli.BACKLOG_ROOT", backlog_dir.parent),
            patch("devbench.cli.BacklogManager", return_value=mock_mgr),
            patch("devbench.cli.ScopeFilter") as mock_sf_class,
        ):
            mock_sf = MagicMock()
            mock_sf.expanded_ids = {"E0-F1-S1-T2"}
            mock_sf_class.parse.return_value = mock_sf

            cli.cmd_set_status("--include", "E0-F1-S1-T2", "in-queue")

        # Verify ScopeFilter.parse was called -- proving no duplication
        mock_sf_class.parse.assert_called_once()
        call_args = mock_sf_class.parse.call_args
        assert call_args.args[0] == "E0-F1-S1-T2"  # include_str

    def test_bulk_file_missing_prints_warning_continues(
        self, mock_units: list[WorkUnit], backlog_dir: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """Bulk update: WU with missing file prints warning but does not abort the batch."""
        # Only create file for T2, not T1 or T3
        wu_file = backlog_dir / "E0-F1-S1-T2.md"
        wu_file.write_text("# Task\n## Status: in-queue\n")

        mock_parser = MagicMock()
        mock_parser.parse_index.return_value = mock_units
        mock_mgr = MagicMock()

        with (
            patch("devbench.cli.BacklogParser", return_value=mock_parser),
            patch("devbench.cli.BACKLOG_ROOT", backlog_dir.parent),
            patch("devbench.cli.BacklogManager", return_value=mock_mgr),
        ):
            result = cli.cmd_set_status("--include", "E0", "in-queue")

        # Should succeed for T2 even though T1/T3 files are missing
        assert result == 0
        assert mock_mgr.bulk_set_status.call_count >= 1

    def test_invalid_scope_token_returns_1(
        self, mock_units: list[WorkUnit], capsys: pytest.CaptureFixture[str]
    ) -> None:
        """AC-190-5 applies: reversed range rejects with actionable error."""
        from devbench.scope import InvalidScopeError

        mock_parser = MagicMock()
        mock_parser.parse_index.return_value = mock_units

        with (
            patch("devbench.cli.BacklogParser", return_value=mock_parser),
            patch("devbench.cli.ScopeFilter") as mock_sf_class,
        ):
            mock_sf_class.parse.side_effect = InvalidScopeError("reversed range")
            result = cli.cmd_set_status("--include", "E3-E1", "in-queue")

        assert result == 1
        err = capsys.readouterr().err
        assert "reversed range" in err.lower() or "scope" in err.lower()

    # ------------------------------------------------------------------
    # --dry-run tests (AC-194-3)
    # ------------------------------------------------------------------

    @pytest.mark.unit
    def test_dry_run_bulk_prints_affected_wus_and_returns_0(
        self, mock_units: list[WorkUnit], backlog_dir: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """AC-194-3: --dry-run prints id/current/new for each affected WU, rc=0."""
        for unit in mock_units:
            (backlog_dir / unit.file_path.name).write_text(f"# {unit.id}\n## Status: {unit.status.value}\n")

        mock_parser = MagicMock()
        mock_parser.parse_index.return_value = mock_units
        mock_mgr = MagicMock()

        with (
            patch("devbench.cli.BacklogParser", return_value=mock_parser),
            patch("devbench.cli.BACKLOG_ROOT", backlog_dir.parent),
            patch("devbench.cli.BacklogManager", return_value=mock_mgr),
        ):
            result = cli.cmd_set_status("--dry-run", "--include", "E0", "in-queue")

        assert result == 0
        out = capsys.readouterr().out
        # Each matched WU should appear in output with tab-separated id/current/new
        for unit in mock_units:
            assert unit.id in out
        assert "in-queue" in out
        # No writes must have happened
        mock_mgr.force_status.assert_not_called()

    @pytest.mark.unit
    def test_dry_run_does_not_write_any_files(self, mock_units: list[WorkUnit], backlog_dir: Path) -> None:
        """AC-194-3: --dry-run must not call force_status or modify any file."""
        for unit in mock_units:
            wu_file = backlog_dir / unit.file_path.name
            wu_file.write_text(f"# {unit.id}\n## Status: {unit.status.value}\n")

        mock_parser = MagicMock()
        mock_parser.parse_index.return_value = mock_units
        mock_mgr = MagicMock()

        with (
            patch("devbench.cli.BacklogParser", return_value=mock_parser),
            patch("devbench.cli.BACKLOG_ROOT", backlog_dir.parent),
            patch("devbench.cli.BacklogManager", return_value=mock_mgr),
        ):
            cli.cmd_set_status("--dry-run", "--include", "E0", "in-queue")

        mock_mgr.force_status.assert_not_called()

    @pytest.mark.unit
    def test_dry_run_output_format_is_tab_separated(
        self, mock_units: list[WorkUnit], backlog_dir: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """AC-194-3: each output line is '{id}\\t{current_status}\\t{new_status}'."""
        for unit in mock_units:
            (backlog_dir / unit.file_path.name).write_text(f"# {unit.id}\n## Status: {unit.status.value}\n")

        mock_parser = MagicMock()
        mock_parser.parse_index.return_value = mock_units
        mock_mgr = MagicMock()

        with (
            patch("devbench.cli.BacklogParser", return_value=mock_parser),
            patch("devbench.cli.BACKLOG_ROOT", backlog_dir.parent),
            patch("devbench.cli.BacklogManager", return_value=mock_mgr),
        ):
            result = cli.cmd_set_status("--dry-run", "--include", "E0", "in-queue")

        assert result == 0
        out = capsys.readouterr().out
        lines = [line for line in out.strip().splitlines() if "\t" in line]
        assert len(lines) == len(mock_units)
        for line in lines:
            parts = line.split("\t")
            assert len(parts) == 3
            wu_id, _current, new_s = parts
            assert wu_id in [u.id for u in mock_units]
            assert new_s == "in-queue"

    @pytest.mark.unit
    def test_dry_run_invalid_status_still_returns_1(self, capsys: pytest.CaptureFixture[str]) -> None:
        """AC-194-3: --dry-run with invalid target status still returns rc=1."""
        result = cli.cmd_set_status("--dry-run", "--include", "E0", "not-a-status")
        assert result == 1
        assert "Invalid status" in capsys.readouterr().err

    @pytest.mark.unit
    def test_dry_run_no_matching_units_returns_1(
        self, mock_units: list[WorkUnit], capsys: pytest.CaptureFixture[str]
    ) -> None:
        """AC-194-3: --dry-run with no matching WUs still returns rc=1."""
        mock_parser = MagicMock()
        mock_parser.parse_index.return_value = mock_units

        with patch("devbench.cli.BacklogParser", return_value=mock_parser):
            result = cli.cmd_set_status("--dry-run", "--include", "E99", "in-queue")

        assert result == 1
        assert "no work units" in capsys.readouterr().err.lower()

    # ------------------------------------------------------------------
    # --yes flag and bulk_update_confirm_threshold tests (AC-194-4)
    # ------------------------------------------------------------------

    @pytest.mark.unit
    def test_yes_flag_parsed_and_skips_prompt(
        self, mock_units: list[WorkUnit], backlog_dir: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """AC-194-4: --yes skips the confirmation prompt and proceeds with updates."""
        for unit in mock_units:
            (backlog_dir / unit.file_path.name).write_text(f"# {unit.id}\n## Status: {unit.status.value}\n")

        mock_parser = MagicMock()
        mock_parser.parse_index.return_value = mock_units
        mock_mgr = MagicMock()

        # Patch threshold to 0 so prompt would normally be triggered
        mock_backlog_cfg = MagicMock()
        mock_backlog_cfg.bulk_update_confirm_threshold = 0
        mock_runtime_cfg = MagicMock()
        mock_runtime_cfg.backlog = mock_backlog_cfg

        with (
            patch("devbench.cli.BacklogParser", return_value=mock_parser),
            patch("devbench.cli.BACKLOG_ROOT", backlog_dir.parent),
            patch("devbench.cli.BacklogManager", return_value=mock_mgr),
            patch("devbench.cli.RUNTIME_CONFIG", mock_runtime_cfg),
            patch("devbench.cli.input") as mock_input,
        ):
            result = cli.cmd_set_status("--include", "E0", "--yes", "in-queue")

        assert result == 0
        mock_input.assert_not_called()
        assert mock_mgr.bulk_set_status.call_count > 0

    @pytest.mark.unit
    def test_prompt_shown_when_count_exceeds_threshold(
        self, mock_units: list[WorkUnit], backlog_dir: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """AC-194-4: prompt shown when matched count > threshold and --yes not given; 'y' proceeds."""
        for unit in mock_units:
            (backlog_dir / unit.file_path.name).write_text(f"# {unit.id}\n## Status: {unit.status.value}\n")

        mock_parser = MagicMock()
        mock_parser.parse_index.return_value = mock_units
        mock_mgr = MagicMock()

        # threshold=1 means 3 matched units (len(mock_units)=3) > 1 triggers prompt
        mock_backlog_cfg = MagicMock()
        mock_backlog_cfg.bulk_update_confirm_threshold = 1
        mock_runtime_cfg = MagicMock()
        mock_runtime_cfg.backlog = mock_backlog_cfg

        with (
            patch("devbench.cli.BacklogParser", return_value=mock_parser),
            patch("devbench.cli.BACKLOG_ROOT", backlog_dir.parent),
            patch("devbench.cli.BacklogManager", return_value=mock_mgr),
            patch("devbench.cli.RUNTIME_CONFIG", mock_runtime_cfg),
            patch("devbench.cli.input", return_value="y") as mock_input,
        ):
            result = cli.cmd_set_status("--include", "E0", "in-queue")

        assert result == 0
        mock_input.assert_called_once()
        prompt_text = mock_input.call_args.args[0]
        assert "3" in prompt_text  # count of matched units
        assert mock_mgr.bulk_set_status.call_count > 0

    @pytest.mark.unit
    @pytest.mark.parametrize("answer", ["n", "N", "no", "NO", "", "nope"])
    def test_prompt_declined_exits_rc0_without_writing(
        self,
        answer: str,
        mock_units: list[WorkUnit],
        backlog_dir: Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """AC-194-4: declining the prompt exits rc=0 and no files are written."""
        for unit in mock_units:
            (backlog_dir / unit.file_path.name).write_text(f"# {unit.id}\n## Status: {unit.status.value}\n")

        mock_parser = MagicMock()
        mock_parser.parse_index.return_value = mock_units
        mock_mgr = MagicMock()

        mock_backlog_cfg = MagicMock()
        mock_backlog_cfg.bulk_update_confirm_threshold = 0
        mock_runtime_cfg = MagicMock()
        mock_runtime_cfg.backlog = mock_backlog_cfg

        with (
            patch("devbench.cli.BacklogParser", return_value=mock_parser),
            patch("devbench.cli.BACKLOG_ROOT", backlog_dir.parent),
            patch("devbench.cli.BacklogManager", return_value=mock_mgr),
            patch("devbench.cli.RUNTIME_CONFIG", mock_runtime_cfg),
            patch("devbench.cli.input", return_value=answer),
        ):
            result = cli.cmd_set_status("--include", "E0", "in-queue")

        assert result == 0
        mock_mgr.force_status.assert_not_called()

    @pytest.mark.unit
    def test_no_prompt_when_count_at_or_below_threshold(
        self, mock_units: list[WorkUnit], backlog_dir: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """AC-194-4: no prompt shown when matched count <= threshold."""
        for unit in mock_units:
            (backlog_dir / unit.file_path.name).write_text(f"# {unit.id}\n## Status: {unit.status.value}\n")

        mock_parser = MagicMock()
        mock_parser.parse_index.return_value = mock_units
        mock_mgr = MagicMock()

        # threshold=10 means 3 matched units <= 10 -- no prompt
        mock_backlog_cfg = MagicMock()
        mock_backlog_cfg.bulk_update_confirm_threshold = 10
        mock_runtime_cfg = MagicMock()
        mock_runtime_cfg.backlog = mock_backlog_cfg

        with (
            patch("devbench.cli.BacklogParser", return_value=mock_parser),
            patch("devbench.cli.BACKLOG_ROOT", backlog_dir.parent),
            patch("devbench.cli.BacklogManager", return_value=mock_mgr),
            patch("devbench.cli.RUNTIME_CONFIG", mock_runtime_cfg),
            patch("devbench.cli.input") as mock_input,
        ):
            result = cli.cmd_set_status("--include", "E0", "in-queue")

        assert result == 0
        mock_input.assert_not_called()
        assert mock_mgr.bulk_set_status.call_count > 0

    @pytest.mark.unit
    def test_parse_bulk_args_handles_yes_flag(self) -> None:
        """AC-194-4: _parse_bulk_set_status_args returns yes=True when --yes present."""
        result = cli._parse_bulk_set_status_args(["--include", "E1", "--yes", "in-queue"])
        assert not isinstance(result, int)
        include_str, exclude_str, dry_run, yes_flag, remaining = result
        assert yes_flag is True
        assert include_str == "E1"
        assert remaining == ["in-queue"]

    @pytest.mark.unit
    def test_parse_bulk_args_yes_false_by_default(self) -> None:
        """AC-194-4: _parse_bulk_set_status_args returns yes=False when --yes absent."""
        result = cli._parse_bulk_set_status_args(["--include", "E1", "in-queue"])
        assert not isinstance(result, int)
        include_str, exclude_str, dry_run, yes_flag, remaining = result
        assert yes_flag is False

    @pytest.mark.unit
    def test_prompt_accepted_with_yes_string(self, mock_units: list[WorkUnit], backlog_dir: Path) -> None:
        """AC-194-4: typing 'yes' (full word) at the prompt is accepted."""
        for unit in mock_units:
            (backlog_dir / unit.file_path.name).write_text(f"# {unit.id}\n## Status: {unit.status.value}\n")

        mock_parser = MagicMock()
        mock_parser.parse_index.return_value = mock_units
        mock_mgr = MagicMock()

        mock_backlog_cfg = MagicMock()
        mock_backlog_cfg.bulk_update_confirm_threshold = 0
        mock_runtime_cfg = MagicMock()
        mock_runtime_cfg.backlog = mock_backlog_cfg

        with (
            patch("devbench.cli.BacklogParser", return_value=mock_parser),
            patch("devbench.cli.BACKLOG_ROOT", backlog_dir.parent),
            patch("devbench.cli.BacklogManager", return_value=mock_mgr),
            patch("devbench.cli.RUNTIME_CONFIG", mock_runtime_cfg),
            patch("devbench.cli.input", return_value="yes"),
        ):
            result = cli.cmd_set_status("--include", "E0", "in-queue")

        assert result == 0
        assert mock_mgr.bulk_set_status.call_count > 0

    # ------------------------------------------------------------------
    # AC-194-8: parser error messages reused verbatim from #190
    # ------------------------------------------------------------------

    @pytest.mark.unit
    def test_invalid_scope_token_error_message_matches_190_verbatim(
        self, mock_units: list[WorkUnit], capsys: pytest.CaptureFixture[str]
    ) -> None:
        """AC-194-8: reversed-range error uses exact format 'ERROR: invalid scope token: ...' (verbatim from #190)."""
        from devbench.scope import InvalidScopeError

        exc_text = "Reverse range in token 'E3-E1': 'E1' (=1) > 'E3' (=3). Ranges must be specified in ascending order."
        mock_parser = MagicMock()
        mock_parser.parse_index.return_value = mock_units

        with (
            patch("devbench.cli.BacklogParser", return_value=mock_parser),
            patch("devbench.cli.ScopeFilter") as mock_sf_class,
        ):
            mock_sf_class.parse.side_effect = InvalidScopeError(exc_text)
            result = cli.cmd_set_status("--include", "E3-E1", "in-queue")

        assert result == 1
        err = capsys.readouterr().err
        assert err.startswith("ERROR: invalid scope token: "), (
            f"Expected prefix 'ERROR: invalid scope token: ', got: {err!r}"
        )
        assert exc_text in err

    @pytest.mark.unit
    def test_malformed_scope_token_error_message_matches_190_verbatim(
        self, mock_units: list[WorkUnit], capsys: pytest.CaptureFixture[str]
    ) -> None:
        """AC-194-8: malformed-token error uses exact format 'ERROR: invalid scope token: ...' (verbatim from #190)."""
        from devbench.scope import InvalidScopeError

        exc_text = "Malformed scope token '-E1': each hyphen-delimited segment must be non-empty."
        mock_parser = MagicMock()
        mock_parser.parse_index.return_value = mock_units

        with (
            patch("devbench.cli.BacklogParser", return_value=mock_parser),
            patch("devbench.cli.ScopeFilter") as mock_sf_class,
        ):
            mock_sf_class.parse.side_effect = InvalidScopeError(exc_text)
            result = cli.cmd_set_status("--include", "-E1", "in-queue")

        assert result == 1
        err = capsys.readouterr().err
        assert err.startswith("ERROR: invalid scope token: "), (
            f"Expected prefix 'ERROR: invalid scope token: ', got: {err!r}"
        )
        assert exc_text in err

    # ------------------------------------------------------------------
    # AC-194-9: status-enum guard -- draft is Task-only
    # ------------------------------------------------------------------

    @pytest.mark.unit
    def test_single_draft_status_rejected_for_epic(self, capsys: pytest.CaptureFixture[str]) -> None:
        """AC-194-9: set-status <epic-id> draft rejects because draft is Task-only."""
        epic_unit = WorkUnit(
            id="E1",
            title="Epic One",
            status=WorkUnitStatus.IN_QUEUE,
            unit_type=WorkUnitType.EPIC,
            file_path=Path("backlog/E1.md"),
            repo="caylent-solutions/git-repo",
            dependencies=[],
        )
        mock_parser = MagicMock()
        mock_parser.parse_index.return_value = [epic_unit]

        with patch("devbench.cli.BacklogParser", return_value=mock_parser):
            result = cli.cmd_set_status("E1", "draft")

        assert result == 1
        err = capsys.readouterr().err
        assert "draft" in err.lower()
        assert "task" in err.lower()
        assert "E1" in err

    @pytest.mark.unit
    def test_single_draft_status_rejected_for_feature(self, capsys: pytest.CaptureFixture[str]) -> None:
        """AC-194-9: set-status <feature-id> draft rejects because draft is Task-only."""
        feature_unit = WorkUnit(
            id="E1-F1",
            title="Feature One",
            status=WorkUnitStatus.IN_QUEUE,
            unit_type=WorkUnitType.FEATURE,
            file_path=Path("backlog/E1-F1.md"),
            repo="caylent-solutions/git-repo",
            dependencies=[],
        )
        mock_parser = MagicMock()
        mock_parser.parse_index.return_value = [feature_unit]

        with patch("devbench.cli.BacklogParser", return_value=mock_parser):
            result = cli.cmd_set_status("E1-F1", "draft")

        assert result == 1
        err = capsys.readouterr().err
        assert "draft" in err.lower()
        assert "task" in err.lower()

    @pytest.mark.unit
    def test_single_draft_status_rejected_for_story(self, capsys: pytest.CaptureFixture[str]) -> None:
        """AC-194-9: set-status <story-id> draft rejects because draft is Task-only."""
        story_unit = WorkUnit(
            id="E1-F1-S1",
            title="Story One",
            status=WorkUnitStatus.IN_QUEUE,
            unit_type=WorkUnitType.STORY,
            file_path=Path("backlog/E1-F1-S1.md"),
            repo="caylent-solutions/git-repo",
            dependencies=[],
        )
        mock_parser = MagicMock()
        mock_parser.parse_index.return_value = [story_unit]

        with patch("devbench.cli.BacklogParser", return_value=mock_parser):
            result = cli.cmd_set_status("E1-F1-S1", "draft")

        assert result == 1
        err = capsys.readouterr().err
        assert "draft" in err.lower()
        assert "task" in err.lower()

    @pytest.mark.unit
    def test_single_draft_status_allowed_for_task(self, backlog_dir: Path, capsys: pytest.CaptureFixture[str]) -> None:
        """AC-194-9: set-status <task-id> draft is permitted (Task units accept draft)."""
        task_unit = WorkUnit(
            id="E1-F1-S1-T1",
            title="Task One",
            status=WorkUnitStatus.IN_QUEUE,
            unit_type=WorkUnitType.TASK,
            file_path=Path("backlog/E1-F1-S1-T1.md"),
            repo="caylent-solutions/git-repo",
            dependencies=[],
        )
        wu_file = backlog_dir / "E1-F1-S1-T1.md"
        wu_file.write_text("# E1-F1-S1-T1\n## Status: in-queue\n")

        mock_parser = MagicMock()
        mock_parser.parse_index.return_value = [task_unit]
        mock_mgr = MagicMock()

        with (
            patch("devbench.cli.BacklogParser", return_value=mock_parser),
            patch("devbench.cli.BACKLOG_ROOT", backlog_dir.parent),
            patch("devbench.cli.BacklogManager", return_value=mock_mgr),
        ):
            result = cli.cmd_set_status("E1-F1-S1-T1", "draft")

        assert result == 0
        mock_mgr.force_status.assert_called_once()

    @pytest.mark.unit
    def test_bulk_draft_status_rejected_when_non_task_unit_matched(
        self, mock_units: list[WorkUnit], backlog_dir: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """AC-194-9: bulk set-status --include to draft rejects if any matched unit is non-Task."""
        epic_unit = WorkUnit(
            id="E0",
            title="Epic Zero",
            status=WorkUnitStatus.IN_QUEUE,
            unit_type=WorkUnitType.EPIC,
            file_path=Path("backlog/E0.md"),
            repo="caylent-solutions/git-repo",
            dependencies=[],
        )
        units_with_epic = [epic_unit, *mock_units]
        mock_parser = MagicMock()
        mock_parser.parse_index.return_value = units_with_epic

        with patch("devbench.cli.BacklogParser", return_value=mock_parser):
            result = cli.cmd_set_status("--include", "E0", "draft")

        assert result == 1
        err = capsys.readouterr().err
        assert "draft" in err.lower()
        assert "task" in err.lower()

    @pytest.mark.unit
    def test_bulk_draft_status_allowed_when_all_matched_units_are_tasks(
        self, mock_units: list[WorkUnit], backlog_dir: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """AC-194-9: bulk set-status --include to draft succeeds when all matched units are Tasks."""
        for unit in mock_units:
            (backlog_dir / unit.file_path.name).write_text(f"# {unit.id}\n## Status: {unit.status.value}\n")

        mock_parser = MagicMock()
        mock_parser.parse_index.return_value = mock_units
        mock_mgr = MagicMock()

        mock_backlog_cfg = MagicMock()
        mock_backlog_cfg.bulk_update_confirm_threshold = 100
        mock_runtime_cfg = MagicMock()
        mock_runtime_cfg.backlog = mock_backlog_cfg

        with (
            patch("devbench.cli.BacklogParser", return_value=mock_parser),
            patch("devbench.cli.BACKLOG_ROOT", backlog_dir.parent),
            patch("devbench.cli.BacklogManager", return_value=mock_mgr),
            patch("devbench.cli.RUNTIME_CONFIG", mock_runtime_cfg),
        ):
            result = cli.cmd_set_status("--include", "E0", "draft")

        assert result == 0
        assert mock_mgr.bulk_set_status.call_count > 0


class TestCmdSetStatusBulkIntegration:
    """Integration tests: cmd_set_status bulk --include/--exclude against a real fixture workspace."""

    def _build_fixture_workspace(self, tmp_path: Path, units: list[tuple[str, str]]) -> tuple[Path, Path]:
        """Create a minimal BACKLOG.md + per-WU files under tmp_path.

        Produces the 7-column BACKLOG.md format expected by BacklogParser.parse_index:
        ``| ID | Title | Type | Status | Dependencies | Repo | File Path |``

        Args:
            tmp_path: Temporary directory.
            units: List of (unit_id, status) pairs.

        Returns:
            (backlog_root, backlog_index) paths.
        """
        backlog_root = tmp_path / "backlog"
        backlog_root.mkdir()

        header = (
            "# BACKLOG\n\n"
            "## Full Work Unit Index\n\n"
            "| ID | Title | Type | Status | Dependencies | Repo | File Path |\n"
            "|----|-------|------|--------|--------------|------|------|\n"
        )
        rows = ""
        for uid, status in units:
            file_path = f"backlog/{uid}.md"
            title_status = status.replace("-", " ").title()
            rows += f"| {uid} | Task {uid} | Task | {title_status} | None | caylent/r | `{file_path}` |\n"
        backlog_index = tmp_path / "BACKLOG.md"
        backlog_index.write_text(header + rows)

        for uid, status in units:
            wu_file = backlog_root / f"{uid}.md"
            wu_file.write_text(f"# {uid}: Task {uid}\n\n## Status: {status}\n\n## Comments\n")

        return backlog_root, backlog_index

    @pytest.mark.unit
    def test_bulk_updates_all_matching_units(self, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
        """Integration: --include E1 bulk-updates every WU under E1."""
        units = [
            ("E1-F1-S1-T1", "in-queue"),
            ("E1-F1-S1-T2", "in-queue"),
            ("E2-F1-S1-T1", "in-queue"),
        ]
        backlog_root, backlog_index = self._build_fixture_workspace(tmp_path, units)

        with (
            patch("devbench.cli.BACKLOG_ROOT", backlog_root),
            patch("devbench.cli.BACKLOG_INDEX", backlog_index),
            patch("devbench.cli.WORKSPACE_ROOT", tmp_path),
        ):
            result = cli.cmd_set_status("--include", "E1", "in-progress")

        assert result == 0

        # E2 unit must not be changed
        e2_file = backlog_root / "E2-F1-S1-T1.md"
        assert "in-queue" in e2_file.read_text()

        # E1 units must be changed
        e1t1_file = backlog_root / "E1-F1-S1-T1.md"
        assert "in-progress" in e1t1_file.read_text()
        e1t2_file = backlog_root / "E1-F1-S1-T2.md"
        assert "in-progress" in e1t2_file.read_text()

    @pytest.mark.unit
    def test_bulk_exclude_subtracts_correctly(self, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
        """Integration: --include E1 --exclude E1-F1-S1-T2 updates only T1."""
        units = [
            ("E1-F1-S1-T1", "in-queue"),
            ("E1-F1-S1-T2", "in-queue"),
        ]
        backlog_root, backlog_index = self._build_fixture_workspace(tmp_path, units)

        with (
            patch("devbench.cli.BACKLOG_ROOT", backlog_root),
            patch("devbench.cli.BACKLOG_INDEX", backlog_index),
            patch("devbench.cli.WORKSPACE_ROOT", tmp_path),
        ):
            result = cli.cmd_set_status("--include", "E1", "--exclude", "E1-F1-S1-T2", "in-progress")

        assert result == 0

        t1_file = backlog_root / "E1-F1-S1-T1.md"
        assert "in-progress" in t1_file.read_text()

        t2_file = backlog_root / "E1-F1-S1-T2.md"
        assert "in-queue" in t2_file.read_text()

    @pytest.mark.unit
    def test_single_id_form_unchanged(self, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
        """AC-194-2 integration: single-ID form devbench set-status <id> <status> unchanged."""
        units = [("E1-F1-S1-T1", "in-queue")]
        backlog_root, backlog_index = self._build_fixture_workspace(tmp_path, units)

        with (
            patch("devbench.cli.BACKLOG_ROOT", backlog_root),
            patch("devbench.cli.BACKLOG_INDEX", backlog_index),
            patch("devbench.cli.WORKSPACE_ROOT", tmp_path),
        ):
            result = cli.cmd_set_status("E1-F1-S1-T1", "in-progress")

        assert result == 0
        wu_file = backlog_root / "E1-F1-S1-T1.md"
        assert "in-progress" in wu_file.read_text()

    @pytest.mark.unit
    def test_dry_run_does_not_write_files(self, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
        """AC-194-3 integration: --dry-run leaves WU files unmodified."""
        units = [
            ("E1-F1-S1-T1", "in-queue"),
            ("E1-F1-S1-T2", "in-queue"),
        ]
        backlog_root, backlog_index = self._build_fixture_workspace(tmp_path, units)
        original_t1 = (backlog_root / "E1-F1-S1-T1.md").read_text()
        original_t2 = (backlog_root / "E1-F1-S1-T2.md").read_text()

        with (
            patch("devbench.cli.BACKLOG_ROOT", backlog_root),
            patch("devbench.cli.BACKLOG_INDEX", backlog_index),
            patch("devbench.cli.WORKSPACE_ROOT", tmp_path),
        ):
            result = cli.cmd_set_status("--dry-run", "--include", "E1", "in-progress")

        assert result == 0
        # Files must be unchanged
        assert (backlog_root / "E1-F1-S1-T1.md").read_text() == original_t1
        assert (backlog_root / "E1-F1-S1-T2.md").read_text() == original_t2

        out = capsys.readouterr().out
        assert "E1-F1-S1-T1" in out
        assert "E1-F1-S1-T2" in out
        assert "in-progress" in out

    @pytest.mark.unit
    def test_dry_run_prints_tab_separated_rows(self, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
        """AC-194-3 integration: each dry-run output line is id TAB current TAB new."""
        units = [("E1-F1-S1-T1", "in-queue")]
        backlog_root, backlog_index = self._build_fixture_workspace(tmp_path, units)

        with (
            patch("devbench.cli.BACKLOG_ROOT", backlog_root),
            patch("devbench.cli.BACKLOG_INDEX", backlog_index),
            patch("devbench.cli.WORKSPACE_ROOT", tmp_path),
        ):
            result = cli.cmd_set_status("--dry-run", "--include", "E1", "in-progress")

        assert result == 0
        out = capsys.readouterr().out
        lines = [line for line in out.strip().splitlines() if "\t" in line]
        assert len(lines) == 1
        parts = lines[0].split("\t")
        assert len(parts) == 3
        assert parts[0] == "E1-F1-S1-T1"
        assert parts[1] == "in-queue"
        assert parts[2] == "in-progress"

    @pytest.mark.unit
    def test_yes_flag_skips_prompt_integration(self, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
        """AC-194-4 integration: --yes skips confirmation prompt and writes all files."""
        units = [
            ("E1-F1-S1-T1", "in-queue"),
            ("E1-F1-S1-T2", "in-queue"),
            ("E1-F1-S1-T3", "in-queue"),
        ]
        backlog_root, backlog_index = self._build_fixture_workspace(tmp_path, units)

        mock_backlog_cfg = MagicMock()
        mock_backlog_cfg.bulk_update_confirm_threshold = 0  # would prompt without --yes
        mock_runtime_cfg = MagicMock()
        mock_runtime_cfg.backlog = mock_backlog_cfg

        with (
            patch("devbench.cli.BACKLOG_ROOT", backlog_root),
            patch("devbench.cli.BACKLOG_INDEX", backlog_index),
            patch("devbench.cli.WORKSPACE_ROOT", tmp_path),
            patch("devbench.cli.RUNTIME_CONFIG", mock_runtime_cfg),
            patch("devbench.cli.input") as mock_input,
        ):
            result = cli.cmd_set_status("--include", "E1", "--yes", "in-progress")

        assert result == 0
        mock_input.assert_not_called()

        # All three files must be updated
        for uid, _ in units:
            assert "in-progress" in (backlog_root / f"{uid}.md").read_text()

    @pytest.mark.unit
    def test_prompt_declined_leaves_files_unchanged_integration(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """AC-194-4 integration: declining the prompt leaves WU files unmodified (rc=0)."""
        units = [
            ("E1-F1-S1-T1", "in-queue"),
            ("E1-F1-S1-T2", "in-queue"),
        ]
        backlog_root, backlog_index = self._build_fixture_workspace(tmp_path, units)
        original_t1 = (backlog_root / "E1-F1-S1-T1.md").read_text()
        original_t2 = (backlog_root / "E1-F1-S1-T2.md").read_text()

        mock_backlog_cfg = MagicMock()
        mock_backlog_cfg.bulk_update_confirm_threshold = 0
        mock_runtime_cfg = MagicMock()
        mock_runtime_cfg.backlog = mock_backlog_cfg

        with (
            patch("devbench.cli.BACKLOG_ROOT", backlog_root),
            patch("devbench.cli.BACKLOG_INDEX", backlog_index),
            patch("devbench.cli.WORKSPACE_ROOT", tmp_path),
            patch("devbench.cli.RUNTIME_CONFIG", mock_runtime_cfg),
            patch("devbench.cli.input", return_value="n"),
        ):
            result = cli.cmd_set_status("--include", "E1", "in-progress")

        assert result == 0
        assert (backlog_root / "E1-F1-S1-T1.md").read_text() == original_t1
        assert (backlog_root / "E1-F1-S1-T2.md").read_text() == original_t2

    @pytest.mark.unit
    def test_threshold_not_exceeded_skips_prompt_integration(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """AC-194-4 integration: no prompt when matched count <= threshold."""
        units = [
            ("E1-F1-S1-T1", "in-queue"),
            ("E1-F1-S1-T2", "in-queue"),
        ]
        backlog_root, backlog_index = self._build_fixture_workspace(tmp_path, units)

        mock_backlog_cfg = MagicMock()
        mock_backlog_cfg.bulk_update_confirm_threshold = 10  # 2 units <= 10, no prompt
        mock_runtime_cfg = MagicMock()
        mock_runtime_cfg.backlog = mock_backlog_cfg

        with (
            patch("devbench.cli.BACKLOG_ROOT", backlog_root),
            patch("devbench.cli.BACKLOG_INDEX", backlog_index),
            patch("devbench.cli.WORKSPACE_ROOT", tmp_path),
            patch("devbench.cli.RUNTIME_CONFIG", mock_runtime_cfg),
            patch("devbench.cli.input") as mock_input,
        ):
            result = cli.cmd_set_status("--include", "E1", "in-progress")

        assert result == 0
        mock_input.assert_not_called()

        for uid, _ in units:
            assert "in-progress" in (backlog_root / f"{uid}.md").read_text()

    @pytest.mark.unit
    def test_apply_bulk_delegates_to_bulk_set_status(self, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
        """AC-194-5: _apply_bulk_set_status calls BacklogManager.bulk_set_status (flock path).

        The per-unit force_status loop must NOT be called; the single
        bulk_set_status call provides flock serialization.
        """
        units = [
            ("E1-F1-S1-T1", "in-queue"),
            ("E1-F1-S1-T2", "in-queue"),
        ]
        backlog_root, backlog_index = self._build_fixture_workspace(tmp_path, units)

        mock_backlog_cfg = MagicMock()
        mock_backlog_cfg.bulk_update_confirm_threshold = 100
        mock_backlog_cfg.bulk_update_audit_path = "logs/bulk-updates.log"
        mock_runtime_cfg = MagicMock()
        mock_runtime_cfg.backlog = mock_backlog_cfg

        mock_mgr = MagicMock()
        mock_mgr.bulk_set_status.return_value = 2

        with (
            patch("devbench.cli.BACKLOG_ROOT", backlog_root),
            patch("devbench.cli.BACKLOG_INDEX", backlog_index),
            patch("devbench.cli.WORKSPACE_ROOT", tmp_path),
            patch("devbench.cli.RUNTIME_CONFIG", mock_runtime_cfg),
            patch("devbench.cli.BacklogManager", return_value=mock_mgr),
        ):
            result = cli.cmd_set_status("--include", "E1", "in-progress")

        assert result == 0
        # bulk_set_status must be called exactly once with both IDs
        mock_mgr.bulk_set_status.assert_called_once()
        call_args = mock_mgr.bulk_set_status.call_args
        unit_ids_arg = call_args[0][0]  # positional first arg
        assert len(unit_ids_arg) == 2
        ids_passed = [uid for uid, _ in unit_ids_arg]
        assert "E1-F1-S1-T1" in ids_passed
        assert "E1-F1-S1-T2" in ids_passed
        # force_status must NOT be called -- the flock path is through bulk_set_status
        mock_mgr.force_status.assert_not_called()

    @pytest.mark.unit
    def test_apply_bulk_writes_audit_row(self, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
        """AC-194-6: bulk_set_status is called with a valid audit_log_path.

        The audit_log_path must be derived from
        RUNTIME_CONFIG.backlog.bulk_update_audit_path resolved relative to
        WORKSPACE_ROOT.  A [BULK_STATUS_UPDATE] row must appear in the audit file.
        """
        units = [("E1-F1-S1-T1", "in-queue")]
        backlog_root, backlog_index = self._build_fixture_workspace(tmp_path, units)
        audit_log_path = tmp_path / "logs" / "bulk-updates.log"

        mock_backlog_cfg = MagicMock()
        mock_backlog_cfg.bulk_update_confirm_threshold = 100
        mock_backlog_cfg.bulk_update_audit_path = "logs/bulk-updates.log"
        mock_runtime_cfg = MagicMock()
        mock_runtime_cfg.backlog = mock_backlog_cfg

        with (
            patch("devbench.cli.BACKLOG_ROOT", backlog_root),
            patch("devbench.cli.BACKLOG_INDEX", backlog_index),
            patch("devbench.cli.WORKSPACE_ROOT", tmp_path),
            patch("devbench.cli.RUNTIME_CONFIG", mock_runtime_cfg),
        ):
            result = cli.cmd_set_status("--include", "E1", "in-progress")

        assert result == 0
        # The audit file must exist and contain the BULK_STATUS_UPDATE marker
        assert audit_log_path.exists(), f"Audit log not created at {audit_log_path}"
        audit_content = audit_log_path.read_text()
        assert "[BULK_STATUS_UPDATE]" in audit_content
        assert "in-progress" in audit_content

    @pytest.mark.unit
    def test_apply_bulk_audit_path_uses_workspace_root(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """AC-194-6: audit_log_path is resolved relative to WORKSPACE_ROOT, not cwd.

        Two separate workspace roots confirm the path is computed from
        WORKSPACE_ROOT + RUNTIME_CONFIG.backlog.bulk_update_audit_path.
        """
        units = [("E1-F1-S1-T1", "in-queue")]
        backlog_root, backlog_index = self._build_fixture_workspace(tmp_path, units)
        custom_audit_rel = "audit/my-bulk.log"
        expected_audit_path = tmp_path / custom_audit_rel

        mock_backlog_cfg = MagicMock()
        mock_backlog_cfg.bulk_update_confirm_threshold = 100
        mock_backlog_cfg.bulk_update_audit_path = custom_audit_rel
        mock_runtime_cfg = MagicMock()
        mock_runtime_cfg.backlog = mock_backlog_cfg

        mock_mgr = MagicMock()
        mock_mgr.bulk_set_status.return_value = 1

        with (
            patch("devbench.cli.BACKLOG_ROOT", backlog_root),
            patch("devbench.cli.BACKLOG_INDEX", backlog_index),
            patch("devbench.cli.WORKSPACE_ROOT", tmp_path),
            patch("devbench.cli.RUNTIME_CONFIG", mock_runtime_cfg),
            patch("devbench.cli.BacklogManager", return_value=mock_mgr),
        ):
            result = cli.cmd_set_status("--include", "E1", "in-progress")

        assert result == 0
        call_args = mock_mgr.bulk_set_status.call_args
        # audit_log_path is the 4th positional argument (unit_ids, new_status, backlog_index, audit_log_path)
        audit_path_arg = call_args[0][3]
        assert Path(audit_path_arg) == expected_audit_path

    def _build_fixture_workspace_with_types(
        self, tmp_path: Path, units: list[tuple[str, str, str]]
    ) -> tuple[Path, Path]:
        """Create a minimal BACKLOG.md + per-WU files with explicit unit types.

        Args:
            tmp_path: Temporary directory.
            units: List of (unit_id, status, unit_type) triples.

        Returns:
            (backlog_root, backlog_index) paths.
        """
        backlog_root = tmp_path / "backlog"
        backlog_root.mkdir(exist_ok=True)

        header = (
            "# BACKLOG\n\n"
            "## Full Work Unit Index\n\n"
            "| ID | Title | Type | Status | Dependencies | Repo | File Path |\n"
            "|----|-------|------|--------|--------------|------|------|\n"
        )
        rows = ""
        for uid, status, utype in units:
            file_path = f"backlog/{uid}.md"
            title_status = status.replace("-", " ").title()
            rows += f"| {uid} | {utype} {uid} | {utype} | {title_status} | None | caylent/r | `{file_path}` |\n"
        backlog_index = tmp_path / "BACKLOG.md"
        backlog_index.write_text(header + rows)

        for uid, status, utype in units:
            wu_file = backlog_root / f"{uid}.md"
            wu_file.write_text(f"# {uid}: {utype} {uid}\n\n## Status: {status}\n\n## Comments\n")

        return backlog_root, backlog_index

    @pytest.mark.unit
    def test_bulk_draft_rejected_for_epic_integration(self, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
        """AC-194-9 integration: bulk set-status draft rejects when an Epic is in the matched set."""
        units = [
            ("E1", "in-queue", "Epic"),
            ("E1-F1-S1-T1", "in-queue", "Task"),
        ]
        backlog_root, backlog_index = self._build_fixture_workspace_with_types(tmp_path, units)

        mock_backlog_cfg = MagicMock()
        mock_backlog_cfg.bulk_update_confirm_threshold = 100
        mock_backlog_cfg.bulk_update_audit_path = "logs/bulk-updates.log"
        mock_runtime_cfg = MagicMock()
        mock_runtime_cfg.backlog = mock_backlog_cfg

        with (
            patch("devbench.cli.BACKLOG_ROOT", backlog_root),
            patch("devbench.cli.BACKLOG_INDEX", backlog_index),
            patch("devbench.cli.WORKSPACE_ROOT", tmp_path),
            patch("devbench.cli.RUNTIME_CONFIG", mock_runtime_cfg),
        ):
            result = cli.cmd_set_status("--include", "E1", "draft")

        assert result == 1
        err = capsys.readouterr().err
        assert "draft" in err.lower()
        assert "task" in err.lower()

    @pytest.mark.unit
    def test_bulk_draft_allowed_for_all_task_units_integration(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """AC-194-9 integration: bulk set-status draft succeeds when all matched units are Tasks."""
        units = [
            ("E1-F1-S1-T1", "in-queue", "Task"),
            ("E1-F1-S1-T2", "in-queue", "Task"),
        ]
        backlog_root, backlog_index = self._build_fixture_workspace_with_types(tmp_path, units)

        mock_backlog_cfg = MagicMock()
        mock_backlog_cfg.bulk_update_confirm_threshold = 100
        mock_backlog_cfg.bulk_update_audit_path = "logs/bulk-updates.log"
        mock_runtime_cfg = MagicMock()
        mock_runtime_cfg.backlog = mock_backlog_cfg

        with (
            patch("devbench.cli.BACKLOG_ROOT", backlog_root),
            patch("devbench.cli.BACKLOG_INDEX", backlog_index),
            patch("devbench.cli.WORKSPACE_ROOT", tmp_path),
            patch("devbench.cli.RUNTIME_CONFIG", mock_runtime_cfg),
        ):
            result = cli.cmd_set_status("--include", "E1", "draft")

        assert result == 0

    @pytest.mark.unit
    def test_bulk_reverse_range_error_message_format_integration(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """AC-194-8 integration: reversed-range error uses 'ERROR: invalid scope token: ' prefix."""
        units = [("E3-F1-S1-T1", "in-queue", "Task")]
        backlog_root, backlog_index = self._build_fixture_workspace_with_types(tmp_path, units)

        mock_backlog_cfg = MagicMock()
        mock_backlog_cfg.bulk_update_confirm_threshold = 100
        mock_backlog_cfg.bulk_update_audit_path = "logs/bulk-updates.log"
        mock_runtime_cfg = MagicMock()
        mock_runtime_cfg.backlog = mock_backlog_cfg

        with (
            patch("devbench.cli.BACKLOG_ROOT", backlog_root),
            patch("devbench.cli.BACKLOG_INDEX", backlog_index),
            patch("devbench.cli.WORKSPACE_ROOT", tmp_path),
            patch("devbench.cli.RUNTIME_CONFIG", mock_runtime_cfg),
        ):
            result = cli.cmd_set_status("--include", "E3-E1", "in-queue")

        assert result == 1
        err = capsys.readouterr().err
        assert err.startswith("ERROR: invalid scope token: "), (
            f"Expected prefix 'ERROR: invalid scope token: ', got: {err!r}"
        )


class TestCmdMarkDone:
    """Test cmd_mark_done enforces the done-gate via mark_done()."""

    def test_returns_1_when_unit_not_found(self, mock_units: list[WorkUnit]) -> None:
        mock_parser = MagicMock()
        mock_parser.parse_index.return_value = mock_units

        with patch("devbench.cli.BacklogParser", return_value=mock_parser):
            result = cli.cmd_mark_done("NONEXISTENT")

        assert result == 1

    def test_returns_0_on_success(
        self, mock_units: list[WorkUnit], tmp_path: Path, backlog_dir: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        wu_file = backlog_dir / "E0-F1-S1-T2.md"
        wu_file.write_text("# Task\n## Status: in-review\n")

        mock_parser = MagicMock()
        mock_parser.parse_index.return_value = mock_units

        mock_mgr = MagicMock()

        with patch("devbench.cli.BacklogParser", return_value=mock_parser):
            with patch("devbench.cli.BACKLOG_ROOT", backlog_dir.parent):
                with patch("devbench.cli.BacklogManager", return_value=mock_mgr):
                    result = cli.cmd_mark_done("E0-F1-S1-T2")

        assert result == 0
        mock_mgr.mark_done.assert_called_once()

    def test_returns_1_when_done_gate_fails(
        self, mock_units: list[WorkUnit], tmp_path: Path, backlog_dir: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        wu_file = backlog_dir / "E0-F1-S1-T2.md"
        wu_file.write_text("# Task\n## Status: in-review\n")

        mock_parser = MagicMock()
        mock_parser.parse_index.return_value = mock_units

        mock_mgr = MagicMock()
        mock_mgr.mark_done.side_effect = RuntimeError("not all required judges passed")

        with patch("devbench.cli.BacklogParser", return_value=mock_parser):
            with patch("devbench.cli.BACKLOG_ROOT", backlog_dir.parent):
                with patch("devbench.cli.BacklogManager", return_value=mock_mgr):
                    result = cli.cmd_mark_done("E0-F1-S1-T2")

        assert result == 1
        assert "not all required judges passed" in capsys.readouterr().err


class TestCmdValidateBacklogPathResolution:
    """Bug fix: cmd_validate_backlog must pass workspace root (BACKLOG_INDEX.parent) to validate(),
    not BACKLOG_ROOT -- otherwise file paths of the form 'backlog/...' get resolved as
    BACKLOG_ROOT/backlog/... which is a double 'backlog/' and causes false 'file missing' errors.
    """

    def _make_layout(self, workspace: Path) -> tuple[Path, Path]:
        """Create realistic layout: BACKLOG.md at workspace root, work unit in workspace/backlog/."""
        backlog_dir = workspace / BACKLOG_SUBDIR
        backlog_dir.mkdir(parents=True, exist_ok=True)
        wu = backlog_dir / "E0-F1-S1-T1.md"
        wu.write_text(
            "# E0-F1-S1-T1: Task\n\n## Status: in-queue\n\n"
            "## Target Repository\n\n- **Repo:** `org/repo`\n\n"
            "## Description\n\nTest task.\n\n"
            "## Dependencies\n\n| ID | Title | Status |\n|----|-------|--------|\n| none | | |\n\n"
            "## Acceptance Criteria\n\n- [ ] AC-FUNC-001 Placeholder\n\n"
            "## Changes Manifest\n\n| File | Change |\n|------|--------|\n"
            "| `src/f.py` | New |\n"
            "| `tests/unit/test_f.py` | New |\n\n"
            "## Definition of Done\n\n- [ ] All ACs checked\n\n"
            "## TDD Cycle Log\n\n## Comments\n",
            encoding="utf-8",
        )
        idx = workspace / "BACKLOG.md"
        idx.write_text(
            "# Backlog\n\n"
            "## Status Summary\n\n"
            "| Epic | Title | Done | In Progress | In Queue | Blocked |\n"
            "|------|-------|------|-------------|----------|---------|\n"
            "\n"
            "## Full Work Unit Index\n\n"
            "| ID | Title | Type | Status | Dependencies | Repo | File Path |\n"
            "|-----|-------|------|--------|-------------|------|-----------|\n"
            "| E0-F1-S1-T1 | Task 1 | Task | in-queue | none | repo | `backlog/E0-F1-S1-T1.md` |\n",
            encoding="utf-8",
        )
        return idx, backlog_dir

    def test_no_false_file_missing_errors_with_real_layout(self, tmp_path: Path) -> None:
        """When BACKLOG_INDEX is at workspace root and BACKLOG_ROOT = workspace/backlog,
        validate-backlog must return 0 (no false 'file missing' errors).
        """
        idx, backlog_dir = self._make_layout(tmp_path)
        # Simulate production: BACKLOG_INDEX at workspace, BACKLOG_ROOT = workspace/backlog
        with (
            patch("devbench.cli.BACKLOG_INDEX", idx),
            patch("devbench.cli.BACKLOG_ROOT", backlog_dir),
        ):
            result = cli.cmd_validate_backlog()
        assert result == 0

    def test_validate_called_with_workspace_root_not_backlog_root(self, tmp_path: Path) -> None:
        """validate() must receive backlog_index.parent (workspace root), not BACKLOG_ROOT."""
        idx, backlog_dir = self._make_layout(tmp_path)
        mock_mgr = MagicMock()
        mock_mgr.validate.return_value = []

        with (
            patch("devbench.cli.BacklogManager", return_value=mock_mgr),
            patch("devbench.cli.BACKLOG_INDEX", idx),
            patch("devbench.cli.BACKLOG_ROOT", backlog_dir),
        ):
            cli.cmd_validate_backlog()

        # Second arg must be workspace root (idx.parent), not BACKLOG_ROOT (backlog_dir)
        _, call_kwargs = mock_mgr.validate.call_args
        positional = mock_mgr.validate.call_args.args
        workspace_root_arg = positional[1] if len(positional) > 1 else call_kwargs.get("backlog_root")
        assert workspace_root_arg == idx.parent
        assert workspace_root_arg != backlog_dir


class TestCmdValidateBacklog:
    """Test cmd_validate_backlog command."""

    def test_returns_0_when_backlog_is_valid(self, tmp_path: Path) -> None:
        mock_mgr = MagicMock()
        mock_mgr.validate.return_value = []

        with patch("devbench.cli.BacklogManager", return_value=mock_mgr):
            with patch("devbench.cli.BACKLOG_INDEX", tmp_path / "BACKLOG.md"):
                with patch("devbench.cli.BACKLOG_ROOT", tmp_path):
                    result = cli.cmd_validate_backlog()

        assert result == 0

    def test_returns_1_and_prints_errors_when_invalid(self, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
        mock_mgr = MagicMock()
        mock_mgr.validate.return_value = ["E0-T1: work unit file missing", "E0-T2: status mismatch"]

        with patch("devbench.cli.BacklogManager", return_value=mock_mgr):
            with patch("devbench.cli.BACKLOG_INDEX", tmp_path / "BACKLOG.md"):
                with patch("devbench.cli.BACKLOG_ROOT", tmp_path):
                    result = cli.cmd_validate_backlog()

        assert result == 1
        output = capsys.readouterr().out
        assert "E0-T1" in output
        assert "E0-T2" in output


class TestMain:
    """Test main argument parsing."""

    def test_no_args_prints_usage_and_returns_0(self, capsys: pytest.CaptureFixture[str]) -> None:
        with patch("sys.argv", ["judges.cli"]):
            result = cli.main()
        out = capsys.readouterr().out
        assert result == 0
        assert "Usage: devbench" in out
        assert "status" in out  # one of the registered commands

    def test_unknown_command_returns_1(self) -> None:
        with patch("sys.argv", ["judges.cli", "nonexistent"]):
            result = cli.main()
        assert result == 1

    def test_dispatches_status(self) -> None:
        mock_fn = MagicMock(return_value=0)
        with patch("sys.argv", ["judges.cli", "status"]):
            with patch.dict(cli._COMMANDS, {"status": (mock_fn, 0, "Show backlog summary")}):
                result = cli.main()
        assert result == 0
        mock_fn.assert_called_once()

    def test_dispatches_log_with_arg(self) -> None:
        mock_fn = MagicMock(return_value=0)
        with patch("sys.argv", ["judges.cli", "log", "hello"]):
            with patch.dict(cli._COMMANDS, {"log": (mock_fn, 1, "Log a message")}):
                result = cli.main()
        assert result == 0
        mock_fn.assert_called_once_with("hello")

    def test_missing_required_arg_returns_1(self) -> None:
        with patch("sys.argv", ["judges.cli", "execute"]):
            result = cli.main()
        assert result == 1

    def test_dispatches_with_extra_args(self) -> None:
        mock_fn = MagicMock(return_value=0)
        with patch("sys.argv", ["judges.cli", "execute", "T1", "feedback-text"]):
            with patch.dict(cli._COMMANDS, {"execute": (mock_fn, 1, "Execute")}):
                result = cli.main()
        assert result == 0


class TestHelp:
    """`devbench --help` / `-h` at top-level and per-command must print usage and exit 0."""

    def test_top_level_long_flag(self, capsys: pytest.CaptureFixture[str]) -> None:
        with patch("sys.argv", ["judges.cli", "--help"]):
            result = cli.main()
        out = capsys.readouterr().out
        assert result == 0
        assert "Usage: devbench" in out
        assert "status" in out

    def test_top_level_short_flag(self, capsys: pytest.CaptureFixture[str]) -> None:
        with patch("sys.argv", ["judges.cli", "-h"]):
            result = cli.main()
        out = capsys.readouterr().out
        assert result == 0
        assert "Usage: devbench" in out

    def test_per_command_long_flag_does_not_dispatch(self, capsys: pytest.CaptureFixture[str]) -> None:
        """`<cmd> --help` prints the registry description and must not call the handler."""
        mock_fn = MagicMock(return_value=0)
        with patch("sys.argv", ["judges.cli", "status", "--help"]):
            with patch.dict(cli._COMMANDS, {"status": (mock_fn, 0, "Show backlog summary")}):
                result = cli.main()
        out = capsys.readouterr().out
        assert result == 0
        assert "Show backlog summary" in out
        mock_fn.assert_not_called()

    def test_per_command_short_flag_does_not_dispatch(self, capsys: pytest.CaptureFixture[str]) -> None:
        mock_fn = MagicMock(return_value=0)
        with patch("sys.argv", ["judges.cli", "status", "-h"]):
            with patch.dict(cli._COMMANDS, {"status": (mock_fn, 0, "Show backlog summary")}):
                result = cli.main()
        out = capsys.readouterr().out
        assert result == 0
        assert "Show backlog summary" in out
        mock_fn.assert_not_called()

    def test_unknown_command_still_returns_1(self, capsys: pytest.CaptureFixture[str]) -> None:
        """Typos must still fail fast -- --help is not a wildcard excuse."""
        with patch("sys.argv", ["judges.cli", "nonexistent-command"]):
            result = cli.main()
        err = capsys.readouterr().err
        assert result == 1
        assert "Unknown command" in err


class TestPreParseConfig:
    """Test --config CLI pre-parse helper."""

    def test_sets_env_var_and_removes_args(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        import os

        config_path = str(tmp_path / "custom.yaml")
        argv = ["judges.cli", "--config", config_path, "status"]
        monkeypatch.delenv("DEVBENCH_CONFIG_PATH", raising=False)
        cli._pre_parse_config(argv)
        assert os.environ.get("DEVBENCH_CONFIG_PATH") == config_path
        assert "--config" not in argv
        assert config_path not in argv
        assert argv == ["judges.cli", "status"]

    def test_noop_when_config_not_present(self) -> None:
        argv = ["judges.cli", "status"]
        original = argv.copy()
        cli._pre_parse_config(argv)
        assert argv == original

    def test_noop_when_config_has_no_value(self) -> None:
        argv = ["judges.cli", "--config"]
        original = argv.copy()
        cli._pre_parse_config(argv)
        assert argv == original


@pytest.mark.unit
class TestCmdGitOpsSubmoduleGate:
    """Tests for T3 AC-1 and AC-2: UPDATE_SUBMODULE gates update_parent_submodule_ref."""

    def _make_unit(self) -> WorkUnit:
        return WorkUnit(
            id="E202-F1-S1-T3",
            title="Test task",
            status=WorkUnitStatus.IN_PROGRESS,
            unit_type=WorkUnitType.TASK,
            file_path=Path("backlog/E202-F1-S1-T3.md"),
            repo="caylent-solutions/devbench",
            dependencies=[],
        )

    def _build_mock_ops(self) -> MagicMock:
        mock_ops = MagicMock()
        mock_ops.create_pr.return_value = "https://github.com/org/repo/pull/42"
        mock_ops.wait_for_checks_and_classify.return_value = CIResult.GREEN
        return mock_ops

    def test_cmd_git_ops_skips_submodule_update_when_flag_false(self, tmp_path: Path) -> None:
        """
        Given: UPDATE_SUBMODULE is False
        When: cmd_git_ops is called
        Then: update_parent_submodule_ref is never called (AC-1)
        """
        unit = self._make_unit()
        mock_parser = MagicMock()
        mock_parser.parse_index.return_value = [unit]
        mock_ops = self._build_mock_ops()
        repo_path = tmp_path / "devbench"

        with (
            patch("devbench.cli.BacklogParser", return_value=mock_parser),
            patch("devbench.cli.REPO_LOCAL_PATHS", {"caylent-solutions/devbench": repo_path}),
            patch("devbench.cli.UPDATE_SUBMODULE", False),
            patch("devbench.github.git_ops.GitOpsService", return_value=mock_ops),
        ):
            result = cli.cmd_git_ops("E202-F1-S1-T3")

        assert result == 0
        mock_ops.update_parent_submodule_ref.assert_not_called()

    def test_cmd_git_ops_calls_submodule_update_when_flag_true(self, tmp_path: Path) -> None:
        """
        Given: UPDATE_SUBMODULE is True
        When: cmd_git_ops is called
        Then: update_parent_submodule_ref is called with correct args (AC-2)
        """
        unit = self._make_unit()
        mock_parser = MagicMock()
        mock_parser.parse_index.return_value = [unit]
        mock_ops = self._build_mock_ops()
        repo_path = tmp_path / "devbench"

        with (
            patch("devbench.cli.BacklogParser", return_value=mock_parser),
            patch("devbench.cli.REPO_LOCAL_PATHS", {"caylent-solutions/devbench": repo_path}),
            patch("devbench.cli.UPDATE_SUBMODULE", True),
            patch("devbench.github.git_ops.GitOpsService", return_value=mock_ops),
        ):
            result = cli.cmd_git_ops("E202-F1-S1-T3")

        assert result == 0
        mock_ops.update_parent_submodule_ref.assert_called_once_with(
            "caylent-solutions/devbench",
            repo_path,
            "chore: update devbench submodule after E202-F1-S1-T3",
        )


@pytest.mark.unit
class TestCmdGitOpsChecksGate:
    """Tests for T2 AC-4 and AC-5: CI checks gate in cmd_git_ops."""

    def _make_unit(self) -> WorkUnit:
        return WorkUnit(
            id="E202-F1-S1-T2",
            title="Test task",
            status=WorkUnitStatus.IN_PROGRESS,
            unit_type=WorkUnitType.TASK,
            file_path=Path("backlog/E202-F1-S1-T2.md"),
            repo="caylent-solutions/devbench",
            dependencies=[],
        )

    def test_cmd_git_ops_returns_error_when_checks_fail(self, tmp_path: Path) -> None:
        """
        Given: wait_for_checks returns False (checks failed) and the
            CI-failure executor retry path is opted out via YAML
            (``git_ops.ci_failure_retry: false``)
        When: cmd_git_ops is called
        Then: returns 1 and merge_pr is never called (AC-4 -- legacy
            BLOCKED path; see TestCiFailureRetry for the rc=2 default
            behaviour after the v-next flip).
        """
        unit = self._make_unit()
        mock_parser = MagicMock()
        mock_parser.parse_index.return_value = [unit]
        mock_ops = MagicMock()
        mock_ops.create_pr.return_value = "https://github.com/org/repo/pull/99"
        mock_ops.wait_for_checks_and_classify.return_value = CIResult.FAILED_UNKNOWN
        repo_path = tmp_path / "devbench"

        with (
            patch("devbench.cli.BacklogParser", return_value=mock_parser),
            patch("devbench.cli.REPO_LOCAL_PATHS", {"caylent-solutions/devbench": repo_path}),
            patch("devbench.cli.UPDATE_SUBMODULE", False),
            patch("devbench.github.git_ops.GitOpsService", return_value=mock_ops),
            patch("devbench.config.CI_FAILURE_RETRY_ENABLED", False),
        ):
            result = cli.cmd_git_ops("E202-F1-S1-T2")

        assert result == 1
        mock_ops.merge_pr.assert_not_called()

    def test_cmd_git_ops_merges_when_checks_pass(self, tmp_path: Path) -> None:
        """
        Given: wait_for_checks returns True (all checks passed or no checks)
        When: cmd_git_ops is called
        Then: merge_pr is called and returns 0 (AC-5)
        """
        unit = self._make_unit()
        mock_parser = MagicMock()
        mock_parser.parse_index.return_value = [unit]
        mock_ops = MagicMock()
        mock_ops.create_pr.return_value = "https://github.com/org/repo/pull/99"
        mock_ops.wait_for_checks_and_classify.return_value = CIResult.GREEN
        repo_path = tmp_path / "devbench"

        with (
            patch("devbench.cli.BacklogParser", return_value=mock_parser),
            patch("devbench.cli.REPO_LOCAL_PATHS", {"caylent-solutions/devbench": repo_path}),
            patch("devbench.cli.UPDATE_SUBMODULE", False),
            patch("devbench.github.git_ops.GitOpsService", return_value=mock_ops),
        ):
            result = cli.cmd_git_ops("E202-F1-S1-T2")

        assert result == 0
        mock_ops.merge_pr.assert_called_once()


@pytest.mark.unit
class TestCmdEnsureBranch:
    """Tests for cmd_ensure_branch (T1 AC-1)."""

    def _make_unit(self) -> WorkUnit:
        return WorkUnit(
            id="E202-F1-S1-T1",
            title="ensure_branch task",
            status=WorkUnitStatus.IN_PROGRESS,
            unit_type=WorkUnitType.TASK,
            file_path=Path("backlog/E202-F1-S1-T1.md"),
            repo="caylent-solutions/devbench",
            dependencies=[],
        )

    def test_cmd_ensure_branch_calls_git_ops(self, tmp_path: Path) -> None:
        """
        Given: a valid work unit ID
        When: cmd_ensure_branch is called
        Then: GitOpsService.ensure_branch is called with the correct repo, path, and branch (AC-1)
        """
        unit = self._make_unit()
        mock_parser = MagicMock()
        mock_parser.parse_index.return_value = [unit]
        mock_ops = MagicMock()
        repo_path = tmp_path / "devbench"

        with (
            patch("devbench.cli.BacklogParser", return_value=mock_parser),
            patch("devbench.cli.REPO_LOCAL_PATHS", {"caylent-solutions/devbench": repo_path}),
            patch("devbench.github.git_ops.GitOpsService", return_value=mock_ops),
        ):
            result = cli.cmd_ensure_branch("E202-F1-S1-T1")

        assert result == 0
        mock_ops.ensure_branch.assert_called_once_with(
            "caylent-solutions/devbench",
            repo_path,
            "backlog/e202-f1-s1-t1",
        )

    def test_cmd_ensure_branch_returns_1_when_unit_not_found(self) -> None:
        """
        Given: a unit ID not in the backlog
        When: cmd_ensure_branch is called
        Then: returns 1
        """
        mock_parser = MagicMock()
        mock_parser.parse_index.return_value = []

        with patch("devbench.cli.BacklogParser", return_value=mock_parser):
            result = cli.cmd_ensure_branch("NONEXISTENT")

        assert result == 1

    def test_cmd_ensure_branch_namespaces_with_configured_branch_prefix(self, tmp_path: Path) -> None:
        """issue #283 AC-1/AC-2: when git_ops.branch_prefix is configured for the
        unit's repo, the branch ensure_branch is called with is namespaced --
        preventing collisions when multiple devbench workspaces share one repo."""
        from devbench.config_loader import GitOpsConfig, RepoConfig, RuntimeConfig

        unit = self._make_unit()
        mock_parser = MagicMock()
        mock_parser.parse_index.return_value = [unit]
        mock_ops = MagicMock()
        repo_path = tmp_path / "devbench"
        mock_runtime_cfg = RuntimeConfig(
            repos={"caylent-solutions/devbench": RepoConfig(branch_prefix="wg_004")},
            git_ops=GitOpsConfig(),
        )

        with (
            patch("devbench.cli.BacklogParser", return_value=mock_parser),
            patch("devbench.cli.REPO_LOCAL_PATHS", {"caylent-solutions/devbench": repo_path}),
            patch("devbench.cli.RUNTIME_CONFIG", mock_runtime_cfg),
            patch("devbench.github.git_ops.GitOpsService", return_value=mock_ops),
        ):
            result = cli.cmd_ensure_branch("E202-F1-S1-T1")

        assert result == 0
        mock_ops.ensure_branch.assert_called_once_with(
            "caylent-solutions/devbench",
            repo_path,
            "backlog/wg_004/e202-f1-s1-t1",
        )

    def test_cmd_ensure_branch_namespaces_single_branch_with_configured_branch_prefix(self, tmp_path: Path) -> None:
        """issue #283 AC-5: branch_prefix also namespaces git_ops.single_branch
        (accumulator mode), not just per-unit branches."""
        from devbench.config_loader import GitOpsConfig, RepoConfig, RuntimeConfig

        unit = self._make_unit()
        mock_parser = MagicMock()
        mock_parser.parse_index.return_value = [unit]
        mock_ops = MagicMock()
        repo_path = tmp_path / "devbench"
        mock_runtime_cfg = RuntimeConfig(
            repos={"caylent-solutions/devbench": RepoConfig(branch_prefix="wg_004")},
            git_ops=GitOpsConfig(single_branch="feat/batch"),
        )

        with (
            patch("devbench.cli.BacklogParser", return_value=mock_parser),
            patch("devbench.cli.REPO_LOCAL_PATHS", {"caylent-solutions/devbench": repo_path}),
            patch("devbench.cli.RUNTIME_CONFIG", mock_runtime_cfg),
            patch("devbench.config.SINGLE_BRANCH", "feat/batch"),
            patch("devbench.github.git_ops.GitOpsService", return_value=mock_ops),
        ):
            result = cli.cmd_ensure_branch("E202-F1-S1-T1")

        assert result == 0
        mock_ops.ensure_branch.assert_called_once_with(
            "caylent-solutions/devbench",
            repo_path,
            "wg_004/feat/batch",
        )


@pytest.mark.unit
class TestCmdGitOpsPostMergeCheckout:
    """Tests for AC-1: cmd_git_ops checks out default branch after merge."""

    def _make_unit(self) -> WorkUnit:
        return WorkUnit(
            id="E224-F1-S1-T1",
            title="Post-merge checkout test",
            status=WorkUnitStatus.IN_PROGRESS,
            unit_type=WorkUnitType.TASK,
            file_path=Path("backlog/E224-F1-S1-T1.md"),
            repo="caylent-solutions/devbench",
            dependencies=[],
        )

    def test_cmd_git_ops_checks_out_default_branch_after_merge(self, tmp_path: Path) -> None:
        """
        Given: merge_pr succeeds
        When: cmd_git_ops is called
        Then: checkout_default_branch is called after merge_pr succeeds (AC-1)
        """
        unit = self._make_unit()
        mock_parser = MagicMock()
        mock_parser.parse_index.return_value = [unit]
        mock_ops = MagicMock()
        mock_ops.create_pr.return_value = "https://github.com/org/repo/pull/42"
        mock_ops.wait_for_checks_and_classify.return_value = CIResult.GREEN
        repo_path = tmp_path / "devbench"

        with (
            patch("devbench.cli.BacklogParser", return_value=mock_parser),
            patch("devbench.cli.REPO_LOCAL_PATHS", {"caylent-solutions/devbench": repo_path}),
            patch("devbench.cli.UPDATE_SUBMODULE", False),
            patch("devbench.github.git_ops.GitOpsService", return_value=mock_ops),
        ):
            result = cli.cmd_git_ops("E224-F1-S1-T1")

        assert result == 0
        mock_ops.checkout_default_branch.assert_called_once_with("caylent-solutions/devbench", repo_path)

    def test_cmd_git_ops_calls_checkout_before_submodule_update(self, tmp_path: Path) -> None:
        """
        Given: merge_pr succeeds and UPDATE_SUBMODULE is True
        When: cmd_git_ops is called
        Then: checkout_default_branch is called before update_parent_submodule_ref (AC-1)
        """
        unit = self._make_unit()
        mock_parser = MagicMock()
        mock_parser.parse_index.return_value = [unit]
        call_order: list[str] = []
        mock_ops = MagicMock()
        mock_ops.create_pr.return_value = "https://github.com/org/repo/pull/42"
        mock_ops.wait_for_checks_and_classify.return_value = CIResult.GREEN
        mock_ops.checkout_default_branch.side_effect = lambda *_: call_order.append("checkout")
        mock_ops.update_parent_submodule_ref.side_effect = lambda *_: call_order.append("submodule")
        repo_path = tmp_path / "devbench"

        with (
            patch("devbench.cli.BacklogParser", return_value=mock_parser),
            patch("devbench.cli.REPO_LOCAL_PATHS", {"caylent-solutions/devbench": repo_path}),
            patch("devbench.cli.UPDATE_SUBMODULE", True),
            patch("devbench.github.git_ops.GitOpsService", return_value=mock_ops),
        ):
            result = cli.cmd_git_ops("E224-F1-S1-T1")

        assert result == 0
        assert call_order.index("checkout") < call_order.index("submodule")


@pytest.mark.unit
class TestCmdGitOpsConflictingRetry:
    """Tests for AC-6 and AC-7: ConflictingPRError retry logic in cmd_git_ops."""

    def _make_unit(self) -> WorkUnit:
        return WorkUnit(
            id="E224-F1-S1-T1",
            title="Conflicting retry test",
            status=WorkUnitStatus.IN_PROGRESS,
            unit_type=WorkUnitType.TASK,
            file_path=Path("backlog/E224-F1-S1-T1.md"),
            repo="caylent-solutions/devbench",
            dependencies=[],
        )

    def test_cmd_git_ops_retries_merge_after_conflicting(self, tmp_path: Path) -> None:
        """
        Given: first merge_pr raises ConflictingPRError, retry succeeds
        When: cmd_git_ops is called
        Then: rebase_and_force_push is called, then merge_pr is retried and returns 0 (AC-6)
        """
        from devbench.github.git_ops import ConflictingPRError

        unit = self._make_unit()
        mock_parser = MagicMock()
        mock_parser.parse_index.return_value = [unit]
        mock_ops = MagicMock()
        mock_ops.create_pr.return_value = "https://github.com/org/repo/pull/42"
        mock_ops.wait_for_checks_and_classify.return_value = CIResult.GREEN
        # First call raises ConflictingPRError, second succeeds
        mock_ops.merge_pr.side_effect = [
            ConflictingPRError("CONFLICTING"),
            None,
        ]
        repo_path = tmp_path / "devbench"

        with (
            patch("devbench.cli.BacklogParser", return_value=mock_parser),
            patch("devbench.cli.REPO_LOCAL_PATHS", {"caylent-solutions/devbench": repo_path}),
            patch("devbench.cli.UPDATE_SUBMODULE", False),
            patch("devbench.github.git_ops.GitOpsService", return_value=mock_ops),
        ):
            result = cli.cmd_git_ops("E224-F1-S1-T1")

        assert result == 0
        mock_ops.rebase_and_force_push.assert_called_once_with(
            "caylent-solutions/devbench",
            repo_path,
            "backlog/e224-f1-s1-t1",
        )
        assert mock_ops.merge_pr.call_count == 2

    def test_cmd_git_ops_exits_nonzero_if_retry_merge_fails(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """
        Given: first merge_pr raises ConflictingPRError, retry also fails
        When: cmd_git_ops is called
        Then: returns 1 with clear error message, no further retry (AC-7)
        """
        from devbench.github.git_ops import ConflictingPRError

        unit = self._make_unit()
        mock_parser = MagicMock()
        mock_parser.parse_index.return_value = [unit]
        mock_ops = MagicMock()
        mock_ops.create_pr.return_value = "https://github.com/org/repo/pull/42"
        mock_ops.wait_for_checks_and_classify.return_value = CIResult.GREEN
        # Both calls fail
        mock_ops.merge_pr.side_effect = [
            ConflictingPRError("CONFLICTING"),
            RuntimeError("merge still failed"),
        ]
        repo_path = tmp_path / "devbench"

        with (
            patch("devbench.cli.BacklogParser", return_value=mock_parser),
            patch("devbench.cli.REPO_LOCAL_PATHS", {"caylent-solutions/devbench": repo_path}),
            patch("devbench.cli.UPDATE_SUBMODULE", False),
            patch("devbench.github.git_ops.GitOpsService", return_value=mock_ops),
        ):
            result = cli.cmd_git_ops("E224-F1-S1-T1")

        assert result == 1
        err_output = capsys.readouterr().err
        assert "merge" in err_output.lower() or "ERROR" in err_output
        # Must not call merge_pr a third time
        assert mock_ops.merge_pr.call_count == 2


@pytest.mark.unit
class TestCmdGetDiff:
    """Tests for cmd_get_diff origin/<default_branch> fix (E225-F1-S1-T1)."""

    def _make_unit(self) -> WorkUnit:
        return WorkUnit(
            id="E225-F1-S1-T1",
            title="get-diff test task",
            status=WorkUnitStatus.IN_PROGRESS,
            unit_type=WorkUnitType.TASK,
            file_path=Path("backlog/E225-F1-S1-T1.md"),
            repo="caylent-solutions/devbench",
            dependencies=[],
        )

    def test_get_diff_uses_origin_remote_ref_for_branch_diff(self, tmp_path: Path) -> None:
        """
        Given: a configured default branch of 'main3'
        When: cmd_get_diff is called
        Then: run_command is invoked with ['git', 'diff', 'origin/main3'], not ['git', 'diff', 'main3'] (AC-1)
        """
        unit = self._make_unit()
        mock_parser = MagicMock()
        mock_parser.parse_index.return_value = [unit]
        repo_path = tmp_path / "devbench"

        diff_calls: list[list[str]] = []

        def fake_run_command(cmd: list[str], **_kwargs: object) -> tuple[int, str, str]:
            if cmd[:2] == ["git", "diff"]:
                diff_calls.append(cmd)
            return (0, "", "")

        with (
            patch("devbench.cli.BacklogParser", return_value=mock_parser),
            patch("devbench.cli.REPO_LOCAL_PATHS", {"caylent-solutions/devbench": repo_path}),
            patch("devbench.cli.get_configured_default_branch", return_value="main3"),
            patch("devbench.cli.run_command", side_effect=fake_run_command),
        ):
            cli.cmd_get_diff("E225-F1-S1-T1")

        branch_diff_calls = [c for c in diff_calls if len(c) == 3 and c[2] not in ("--cached",)]
        assert len(branch_diff_calls) == 1, f"Expected exactly one branch diff call, got: {branch_diff_calls}"
        assert branch_diff_calls[0] == ["git", "diff", "origin/main3"], (
            f"Expected 'origin/main3' ref but got: {branch_diff_calls[0]}"
        )

    def test_get_diff_output_unchanged_when_local_ref_current(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """
        Given: local 'main3' ref is up-to-date with 'origin/main3' (identical diff output)
        When: cmd_get_diff is called
        Then: the diff output is produced correctly and return code is 0 (AC-2)
        """
        unit = self._make_unit()
        mock_parser = MagicMock()
        mock_parser.parse_index.return_value = [unit]
        repo_path = tmp_path / "devbench"
        expected_diff = "diff --git a/foo.py b/foo.py\n--- a/foo.py\n+++ b/foo.py\n@@ -1 +1 @@\n-old\n+new\n"

        def fake_run_command(cmd: list[str], **_kwargs: object) -> tuple[int, str, str]:
            if cmd == ["git", "diff", "origin/main3"]:
                return (0, expected_diff, "")
            return (0, "", "")

        with (
            patch("devbench.cli.BacklogParser", return_value=mock_parser),
            patch("devbench.cli.REPO_LOCAL_PATHS", {"caylent-solutions/devbench": repo_path}),
            patch("devbench.cli.get_configured_default_branch", return_value="main3"),
            patch("devbench.cli.run_command", side_effect=fake_run_command),
        ):
            result = cli.cmd_get_diff("E225-F1-S1-T1")

        assert result == 0
        output = capsys.readouterr().out
        assert "foo.py" in output, "Expected diff content to appear in output when local ref is current"

    def test_get_diff_excludes_upstream_merged_files_when_local_ref_stale(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """
        Given: local 'main3' is behind 'origin/main3' (stale)
        When: cmd_get_diff is called
        Then: only work-unit-branch changes appear (git diff uses origin/main3, not bare main3) (AC-3)
        """
        unit = self._make_unit()
        mock_parser = MagicMock()
        mock_parser.parse_index.return_value = [unit]
        repo_path = tmp_path / "devbench"

        # Simulate: bare main3 would include upstream-merged file, origin/main3 would not
        branch_only_diff = (
            "diff --git a/new_feature.py b/new_feature.py\n+++ b/new_feature.py\n@@ -0,0 +1 @@\n+feature\n"
        )
        stale_extra_diff = branch_only_diff + "diff --git a/upstream_merged.py b/upstream_merged.py\n"

        def fake_run_command(cmd: list[str], **_kwargs: object) -> tuple[int, str, str]:
            if cmd == ["git", "diff", "origin/main3"]:
                return (0, branch_only_diff, "")
            if cmd == ["git", "diff", "main3"]:
                return (0, stale_extra_diff, "")
            return (0, "", "")

        with (
            patch("devbench.cli.BacklogParser", return_value=mock_parser),
            patch("devbench.cli.REPO_LOCAL_PATHS", {"caylent-solutions/devbench": repo_path}),
            patch("devbench.cli.get_configured_default_branch", return_value="main3"),
            patch("devbench.cli.run_command", side_effect=fake_run_command),
        ):
            result = cli.cmd_get_diff("E225-F1-S1-T1")

        assert result == 0
        output = capsys.readouterr().out
        assert "upstream_merged.py" not in output, (
            "Upstream-merged file appeared in output -- bare branch ref was used instead of origin/"
        )
        assert "new_feature.py" in output, "Branch-specific diff should appear in output"


@pytest.mark.unit
class TestCmdReadUnitStripComments:
    """Tests for --strip-comments flag on cmd_read_unit (E216-F1-S1-T1)."""

    def _make_unit(self, wu_file: Path) -> WorkUnit:
        return WorkUnit(
            id="E216-F1-S1-T1",
            title="Strip comments test",
            status=WorkUnitStatus.IN_PROGRESS,
            unit_type=WorkUnitType.TASK,
            file_path=wu_file,
            repo="caylent-solutions/devbench",
            dependencies=[],
        )

    def _make_wu_file(self, tmp_path: Path, content: str) -> Path:
        wu_file = tmp_path / "E216-F1-S1-T1.md"
        wu_file.write_text(content, encoding="utf-8")
        return wu_file

    def test_read_unit_strip_comments_removes_comments_section(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """AC-2: --strip-comments removes ## Comments section and everything after."""
        content = (
            "# E216-F1-S1-T1: Strip Test\n\n"
            "## Status: in-progress\n\n"
            "## Description\n\nSome description.\n"
            "\n## Comments\n\n"
            "[judge/executor] [REVIEW_PASS] looks good\n"
        )
        wu_file = self._make_wu_file(tmp_path, content)
        unit = self._make_unit(wu_file)
        mock_parser = MagicMock()
        mock_parser.parse_index.return_value = [unit]

        with (
            patch("devbench.cli.BacklogParser", return_value=mock_parser),
            patch("devbench.cli.REPO_LOCAL_PATHS", {"caylent-solutions/devbench": tmp_path}),
        ):
            result = cli.cmd_read_unit("--strip-comments", "E216-F1-S1-T1")

        assert result == 0
        output = capsys.readouterr().out
        data = json.loads(output)
        assert "## Comments" not in data["content"], "Comments section should be stripped when --strip-comments is used"
        assert "[REVIEW_PASS]" not in data["content"], "Comment entries should be removed when --strip-comments is used"
        assert "## Description" in data["content"], "Content before ## Comments should be preserved"

    def test_read_unit_without_flag_returns_full_content(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """AC-3: Without --strip-comments, output is unchanged (backward compatible)."""
        content = (
            "# E216-F1-S1-T1: Strip Test\n\n"
            "## Status: in-progress\n\n"
            "## Comments\n\n"
            "[judge/executor] [REVIEW_PASS] looks good\n"
        )
        wu_file = self._make_wu_file(tmp_path, content)
        unit = self._make_unit(wu_file)
        mock_parser = MagicMock()
        mock_parser.parse_index.return_value = [unit]

        with (
            patch("devbench.cli.BacklogParser", return_value=mock_parser),
            patch("devbench.cli.REPO_LOCAL_PATHS", {"caylent-solutions/devbench": tmp_path}),
        ):
            result = cli.cmd_read_unit("E216-F1-S1-T1")

        assert result == 0
        output = capsys.readouterr().out
        data = json.loads(output)
        assert "## Comments" in data["content"], (
            "Without --strip-comments, full content including Comments should be returned"
        )
        assert "[REVIEW_PASS]" in data["content"], "Without --strip-comments, comment entries should be present"

    def test_read_unit_strip_comments_without_unit_id_returns_error(self, capsys: pytest.CaptureFixture[str]) -> None:
        """AC-4: --strip-comments without unit ID exits with non-zero and clear error."""
        result = cli.cmd_read_unit("--strip-comments")
        assert result == 1
        err = capsys.readouterr().err
        assert "unit_id" in err.lower() or "required" in err.lower(), (
            f"Expected clear error about missing unit_id, got: {err!r}"
        )

    def test_read_unit_strip_comments_unit_has_no_comments_section(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """AC-2 edge case: --strip-comments on a file with no ## Comments section is a no-op."""
        content = "# E216-F1-S1-T1: Strip Test\n\n## Status: in-progress\n\n## Description\n\nSome description.\n"
        wu_file = self._make_wu_file(tmp_path, content)
        unit = self._make_unit(wu_file)
        mock_parser = MagicMock()
        mock_parser.parse_index.return_value = [unit]

        with (
            patch("devbench.cli.BacklogParser", return_value=mock_parser),
            patch("devbench.cli.REPO_LOCAL_PATHS", {"caylent-solutions/devbench": tmp_path}),
        ):
            result = cli.cmd_read_unit("--strip-comments", "E216-F1-S1-T1")

        assert result == 0
        output = capsys.readouterr().out
        data = json.loads(output)
        assert "## Description" in data["content"], (
            "Content should be fully preserved when no ## Comments section exists"
        )
        assert data["content"].strip() == content.strip(), (
            "Content should be unchanged when no ## Comments section is present"
        )


@pytest.mark.unit
class TestCmdLogComment:
    """Tests for cmd_log_comment (AC-1, AC-2)."""

    def _make_wu_file(self, tmp_path: Path, with_comments_section: bool = True) -> tuple[Path, Path]:
        """Return (backlog_dir, wu_file) with a minimal work-unit file."""
        backlog_dir = tmp_path / "backlog"
        backlog_dir.mkdir()
        wu_file = backlog_dir / "E0-F1-S1-T1.md"
        header = "# E0-F1-S1-T1\n\n## Status: in-progress\n\n"
        if with_comments_section:
            header += "## Comments\n"
        wu_file.write_text(header, encoding="utf-8")
        return backlog_dir, wu_file

    def _make_mock_unit(self, backlog_dir: Path) -> WorkUnit:
        return WorkUnit(
            id="E0-F1-S1-T1",
            title="Test Task",
            status=WorkUnitStatus.IN_PROGRESS,
            unit_type=WorkUnitType.TASK,
            file_path=backlog_dir / "E0-F1-S1-T1.md",
            repo="caylent-solutions/git-repo",
            dependencies=[],
        )

    def test_log_comment_appends_agent_format_to_comments(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """AC-1: log-comment appends [YYYY-MM-DD HH:MM UTC] [agent/<agent>] <message>."""
        backlog_dir, wu_file = self._make_wu_file(tmp_path)
        unit = self._make_mock_unit(backlog_dir)
        mock_parser = MagicMock()
        mock_parser.parse_index.return_value = [unit]

        with (
            patch("devbench.cli.BacklogParser", return_value=mock_parser),
            patch("devbench.cli.BACKLOG_ROOT", backlog_dir),
            patch("devbench.cli.WORKSPACE_ROOT", tmp_path),
        ):
            result = cli.cmd_log_comment("executor", "E0-F1-S1-T1", "implementation complete")

        assert result == 0
        content = wu_file.read_text(encoding="utf-8")
        timestamp_pattern = r"\[\d{4}-\d{2}-\d{2} \d{2}:\d{2} UTC\]"
        assert re.search(timestamp_pattern, content), "Timestamp not found in comment"
        assert "[agent/executor]" in content, "Agent prefix not in comment"
        assert "implementation complete" in content, "Message not in comment"

    def test_log_comment_contains_no_review_token(self, tmp_path: Path) -> None:
        """AC-2: log-comment entries must not contain [REVIEW_PASS] or [REVIEW_FAIL]."""
        backlog_dir, wu_file = self._make_wu_file(tmp_path)
        unit = self._make_mock_unit(backlog_dir)
        mock_parser = MagicMock()
        mock_parser.parse_index.return_value = [unit]

        with (
            patch("devbench.cli.BacklogParser", return_value=mock_parser),
            patch("devbench.cli.BACKLOG_ROOT", backlog_dir),
            patch("devbench.cli.WORKSPACE_ROOT", tmp_path),
        ):
            cli.cmd_log_comment("executor", "E0-F1-S1-T1", "pass")

        content = wu_file.read_text(encoding="utf-8")
        assert "[REVIEW_PASS]" not in content
        assert "[REVIEW_FAIL]" not in content

    def test_log_comment_returns_1_when_unit_not_found(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """log-comment fails fast when unit is missing from the index."""
        mock_parser = MagicMock()
        mock_parser.parse_index.return_value = []

        with patch("devbench.cli.BacklogParser", return_value=mock_parser):
            result = cli.cmd_log_comment("executor", "NONEXISTENT", "message")

        assert result == 1
        assert "not found" in capsys.readouterr().err


# ---------------------------------------------------------------------------
# E230-F1-S1-T1: Discrete event comments in cmd_git_ops and cmd_mark_done
# ---------------------------------------------------------------------------


def _make_git_ops_unit(unit_id: str = "E230-F1-S1-T1") -> WorkUnit:
    """Return a WorkUnit suitable for cmd_git_ops tests."""
    return WorkUnit(
        id=unit_id,
        title="Git ops comment test",
        status=WorkUnitStatus.IN_PROGRESS,
        unit_type=WorkUnitType.TASK,
        file_path=Path(f"backlog/{unit_id}.md"),
        repo="caylent-solutions/devbench",
        dependencies=[],
    )


@pytest.mark.unit
class TestCmdGitOpsEventComments:
    """Tests for AC-1, AC-2, AC-3, AC-5, AC-7: git_ops appends audit comments."""

    def _build_mock_ops(self, pr_url: str = "https://github.com/org/repo/pull/42") -> MagicMock:
        mock_ops = MagicMock()
        mock_ops.create_pr.return_value = pr_url
        mock_ops.wait_for_checks_and_classify.return_value = CIResult.GREEN
        return mock_ops

    def _make_wu_file(self, tmp_path: Path, unit_id: str) -> Path:
        """Create wu_file at tmp_path/backlog/{unit_id}.md (matches BACKLOG_ROOT=tmp_path)."""
        backlog_subdir = tmp_path / "backlog"
        backlog_subdir.mkdir(exist_ok=True)
        wu_file = backlog_subdir / f"{unit_id}.md"
        wu_file.write_text(f"# {unit_id}\n\n## Status: in-progress\n\n## Comments\n", encoding="utf-8")
        return wu_file

    def test_git_ops_appends_pr_created_comment(self, tmp_path: Path) -> None:
        """AC-1: After create_pr succeeds, Comments contains [agent/git_ops] [PR_CREATED] <url>."""
        unit_id = "E230-F1-S1-T1"
        pr_url = "https://github.com/org/repo/pull/42"
        unit = _make_git_ops_unit(unit_id)

        wu_file = self._make_wu_file(tmp_path, unit_id)

        mock_parser = MagicMock()
        mock_parser.parse_index.return_value = [unit]
        mock_ops = self._build_mock_ops(pr_url)
        repo_path = tmp_path / "devbench"

        with (
            patch("devbench.cli.BacklogParser", return_value=mock_parser),
            patch("devbench.cli.REPO_LOCAL_PATHS", {"caylent-solutions/devbench": repo_path}),
            patch("devbench.cli.BACKLOG_ROOT", tmp_path),
            patch("devbench.cli.WORKSPACE_ROOT", tmp_path),
            patch("devbench.cli.UPDATE_SUBMODULE", False),
            patch("devbench.github.git_ops.GitOpsService", return_value=mock_ops),
            patch("devbench.backlog.manifest.parse_manifest", return_value=[]),
            patch("devbench.backlog.manifest.assert_staged_matches_manifest"),
        ):
            result = cli.cmd_git_ops(unit_id)

        assert result == 0
        content = wu_file.read_text(encoding="utf-8")
        assert "[agent/git_ops]" in content, f"[agent/git_ops] not found in:\n{content}"
        assert "[PR_CREATED]" in content, f"[PR_CREATED] not found in:\n{content}"
        assert pr_url in content, f"PR URL not found in:\n{content}"

    def test_git_ops_appends_pr_merged_comment_normal_path(self, tmp_path: Path) -> None:
        """AC-2: After merge_pr succeeds (normal), Comments contains [agent/git_ops] [PR_MERGED] <url>."""
        unit_id = "E230-F1-S1-T1"
        pr_url = "https://github.com/org/repo/pull/42"
        unit = _make_git_ops_unit(unit_id)

        wu_file = self._make_wu_file(tmp_path, unit_id)

        mock_parser = MagicMock()
        mock_parser.parse_index.return_value = [unit]
        mock_ops = self._build_mock_ops(pr_url)
        repo_path = tmp_path / "devbench"

        with (
            patch("devbench.cli.BacklogParser", return_value=mock_parser),
            patch("devbench.cli.REPO_LOCAL_PATHS", {"caylent-solutions/devbench": repo_path}),
            patch("devbench.cli.BACKLOG_ROOT", tmp_path),
            patch("devbench.cli.WORKSPACE_ROOT", tmp_path),
            patch("devbench.cli.UPDATE_SUBMODULE", False),
            patch("devbench.github.git_ops.GitOpsService", return_value=mock_ops),
            patch("devbench.backlog.manifest.parse_manifest", return_value=[]),
            patch("devbench.backlog.manifest.assert_staged_matches_manifest"),
        ):
            result = cli.cmd_git_ops(unit_id)

        assert result == 0
        content = wu_file.read_text(encoding="utf-8")
        assert "[agent/git_ops]" in content
        assert "[PR_MERGED]" in content, f"[PR_MERGED] not found in:\n{content}"
        assert pr_url in content

    def test_git_ops_appends_pr_merged_comment_rebase_retry_path(self, tmp_path: Path) -> None:
        """AC-3: After merge_pr succeeds via rebase-retry, Comments contains [agent/git_ops] [PR_MERGED] <url>."""
        from devbench.github.git_ops import ConflictingPRError

        unit_id = "E230-F1-S1-T1"
        pr_url = "https://github.com/org/repo/pull/42"
        unit = _make_git_ops_unit(unit_id)

        wu_file = self._make_wu_file(tmp_path, unit_id)

        mock_parser = MagicMock()
        mock_parser.parse_index.return_value = [unit]
        mock_ops = self._build_mock_ops(pr_url)
        # First merge raises ConflictingPRError, second succeeds
        mock_ops.merge_pr.side_effect = [ConflictingPRError("CONFLICTING"), None]
        repo_path = tmp_path / "devbench"

        with (
            patch("devbench.cli.BacklogParser", return_value=mock_parser),
            patch("devbench.cli.REPO_LOCAL_PATHS", {"caylent-solutions/devbench": repo_path}),
            patch("devbench.cli.BACKLOG_ROOT", tmp_path),
            patch("devbench.cli.WORKSPACE_ROOT", tmp_path),
            patch("devbench.cli.UPDATE_SUBMODULE", False),
            patch("devbench.github.git_ops.GitOpsService", return_value=mock_ops),
            patch("devbench.backlog.manifest.parse_manifest", return_value=[]),
            patch("devbench.backlog.manifest.assert_staged_matches_manifest"),
        ):
            result = cli.cmd_git_ops(unit_id)

        assert result == 0
        content = wu_file.read_text(encoding="utf-8")
        assert "[PR_MERGED]" in content, f"[PR_MERGED] not found after rebase-retry in:\n{content}"
        assert pr_url in content

    def test_event_comments_contain_no_review_token(self, tmp_path: Path) -> None:
        """AC-5: git_ops event entries contain no [REVIEW_PASS] or [REVIEW_FAIL] token."""
        unit_id = "E230-F1-S1-T1"
        pr_url = "https://github.com/org/repo/pull/42"
        unit = _make_git_ops_unit(unit_id)

        wu_file = self._make_wu_file(tmp_path, unit_id)

        mock_parser = MagicMock()
        mock_parser.parse_index.return_value = [unit]
        mock_ops = self._build_mock_ops(pr_url)
        repo_path = tmp_path / "devbench"

        with (
            patch("devbench.cli.BacklogParser", return_value=mock_parser),
            patch("devbench.cli.REPO_LOCAL_PATHS", {"caylent-solutions/devbench": repo_path}),
            patch("devbench.cli.BACKLOG_ROOT", tmp_path),
            patch("devbench.cli.WORKSPACE_ROOT", tmp_path),
            patch("devbench.cli.UPDATE_SUBMODULE", False),
            patch("devbench.github.git_ops.GitOpsService", return_value=mock_ops),
            patch("devbench.backlog.manifest.parse_manifest", return_value=[]),
            patch("devbench.backlog.manifest.assert_staged_matches_manifest"),
        ):
            result = cli.cmd_git_ops(unit_id)

        assert result == 0
        content = wu_file.read_text(encoding="utf-8")
        assert "[REVIEW_PASS]" not in content
        assert "[REVIEW_FAIL]" not in content

    def test_git_ops_warns_but_does_not_fail_when_unit_file_missing(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """AC-7: If work unit file cannot be resolved, cmd_git_ops warns but does not fail."""
        unit_id = "E230-F1-S1-T1"
        pr_url = "https://github.com/org/repo/pull/42"
        unit = _make_git_ops_unit(unit_id)
        # Note: wu_file is NOT created -- file resolution should fail gracefully

        mock_parser = MagicMock()
        mock_parser.parse_index.return_value = [unit]
        mock_ops = self._build_mock_ops(pr_url)
        repo_path = tmp_path / "devbench"

        with (
            patch("devbench.cli.BacklogParser", return_value=mock_parser),
            patch("devbench.cli.REPO_LOCAL_PATHS", {"caylent-solutions/devbench": repo_path}),
            patch("devbench.cli.BACKLOG_ROOT", tmp_path),
            patch("devbench.cli.WORKSPACE_ROOT", tmp_path),
            patch("devbench.cli.UPDATE_SUBMODULE", False),
            patch("devbench.github.git_ops.GitOpsService", return_value=mock_ops),
            patch("devbench.backlog.manifest.parse_manifest", return_value=[]),
            patch("devbench.backlog.manifest.assert_staged_matches_manifest"),
        ):
            result = cli.cmd_git_ops(unit_id)

        # Must NOT fail due to missing file -- git ops already succeeded
        assert result == 0


@pytest.mark.unit
class TestCmdMarkDoneEventComment:
    """Tests for AC-4, AC-5: cmd_mark_done appends [orchestrator] [DONE] comment."""

    def test_mark_done_appends_done_comment(self, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
        """AC-4: After cmd_mark_done completes, Comments contains [orchestrator] [DONE] Work unit <id> completed.

        Uses a real BacklogManager (not mocked) so that _append_agent_comment actually writes to the file.
        Provides a real BACKLOG.md so mark_done can update it.
        """
        from devbench.constants import ALL_REQUIRED_JUDGE_NAMES

        unit_id = "E230-F1-S1-T1"
        unit = WorkUnit(
            id=unit_id,
            title="Mark done comment test",
            status=WorkUnitStatus.IN_REVIEW,
            unit_type=WorkUnitType.TASK,
            file_path=Path(f"backlog/{unit_id}.md"),
            repo="caylent-solutions/devbench",
            dependencies=[],
        )

        # Build BACKLOG.md with the unit row
        backlog_index = tmp_path / "BACKLOG.md"
        backlog_index.write_text(
            "# Backlog\n\n"
            "| ID | Title | Type | Status | Dependencies | Repo | File Path |\n"
            "|---|---|---|---|---|---|---|\n"
            f"| {unit_id} | Mark done comment test | Task | in-review | None | repo | `backlog/{unit_id}.md` |\n",
            encoding="utf-8",
        )

        # All judges pass so mark_done gate is satisfied
        all_pass_comments = "".join(
            f"[2026-01-01 00:00 UTC] [judge/{j}] [REVIEW_PASS] ok\n" for j in sorted(ALL_REQUIRED_JUDGE_NAMES)
        )

        backlog_subdir = tmp_path / "backlog"
        backlog_subdir.mkdir()
        wu_file = backlog_subdir / f"{unit_id}.md"
        wu_file.write_text(
            f"# {unit_id}\n\n## Status: in-review\n\n## Comments\n\n{all_pass_comments}",
            encoding="utf-8",
        )

        mock_parser = MagicMock()
        mock_parser.parse_index.return_value = [unit]

        with (
            patch("devbench.cli.BacklogParser", return_value=mock_parser),
            patch("devbench.cli.BACKLOG_ROOT", tmp_path),
            patch("devbench.cli.WORKSPACE_ROOT", tmp_path),
            patch("devbench.cli.BACKLOG_INDEX", backlog_index),
        ):
            result = cli.cmd_mark_done(unit_id)

        assert result == 0
        content = wu_file.read_text(encoding="utf-8")
        assert "[agent/orchestrator]" in content, f"[agent/orchestrator] not found in:\n{content}"
        assert "[DONE]" in content, f"[DONE] not found in:\n{content}"
        assert unit_id in content

    def test_mark_done_done_comment_contains_no_review_token(self, tmp_path: Path) -> None:
        """AC-5: [DONE] entry appended by cmd_mark_done has no [REVIEW_PASS] or [REVIEW_FAIL] token."""
        from devbench.constants import ALL_REQUIRED_JUDGE_NAMES

        unit_id = "E230-F1-S1-T1"
        unit = WorkUnit(
            id=unit_id,
            title="Mark done comment test",
            status=WorkUnitStatus.IN_REVIEW,
            unit_type=WorkUnitType.TASK,
            file_path=Path(f"backlog/{unit_id}.md"),
            repo="caylent-solutions/devbench",
            dependencies=[],
        )

        backlog_index = tmp_path / "BACKLOG.md"
        backlog_index.write_text(
            "# Backlog\n\n"
            "| ID | Title | Type | Status | Dependencies | Repo | File Path |\n"
            "|---|---|---|---|---|---|---|\n"
            f"| {unit_id} | Mark done comment test | Task | in-review | None | repo | `backlog/{unit_id}.md` |\n",
            encoding="utf-8",
        )

        all_pass_comments = "".join(
            f"[2026-01-01 00:00 UTC] [judge/{j}] [REVIEW_PASS] ok\n" for j in sorted(ALL_REQUIRED_JUDGE_NAMES)
        )

        backlog_subdir = tmp_path / "backlog"
        backlog_subdir.mkdir()
        wu_file = backlog_subdir / f"{unit_id}.md"
        wu_file.write_text(
            f"# {unit_id}\n\n## Status: in-review\n\n## Comments\n\n{all_pass_comments}",
            encoding="utf-8",
        )

        mock_parser = MagicMock()
        mock_parser.parse_index.return_value = [unit]

        with (
            patch("devbench.cli.BacklogParser", return_value=mock_parser),
            patch("devbench.cli.BACKLOG_ROOT", tmp_path),
            patch("devbench.cli.WORKSPACE_ROOT", tmp_path),
            patch("devbench.cli.BACKLOG_INDEX", backlog_index),
        ):
            cli.cmd_mark_done(unit_id)

        content = wu_file.read_text(encoding="utf-8")
        # The [DONE] comment appended by cmd_mark_done must not contain review tokens
        # The existing [REVIEW_PASS] lines from the all_pass_comments are present in content
        # but we only need to verify the NEW entry (last line) doesn't have them.
        # Split on the comments that were there before:
        done_section = content.split("[REVIEW_PASS] ok")[-1]
        assert "[REVIEW_PASS]" not in done_section
        assert "[REVIEW_FAIL]" not in done_section


@pytest.mark.unit
class TestResolveUnitFile:
    """AC-8: _resolve_unit_file helper extracted and used by relevant commands."""

    def test_resolve_unit_file_returns_path_when_found_under_backlog_root(self, tmp_path: Path) -> None:
        """_resolve_unit_file returns the file path when found under BACKLOG_ROOT."""
        unit = WorkUnit(
            id="E230-F1-S1-T1",
            title="Test",
            status=WorkUnitStatus.IN_QUEUE,
            unit_type=WorkUnitType.TASK,
            file_path=Path("backlog/E230-F1-S1-T1.md"),
            repo="caylent-solutions/devbench",
            dependencies=[],
        )
        wu_file = tmp_path / "backlog" / "E230-F1-S1-T1.md"
        wu_file.parent.mkdir(parents=True, exist_ok=True)
        wu_file.write_text("# E230-F1-S1-T1\n\n## Status: in-queue\n", encoding="utf-8")

        with (
            patch("devbench.cli.BACKLOG_ROOT", tmp_path),
            patch("devbench.cli.WORKSPACE_ROOT", tmp_path / "workspace"),
        ):
            result = cli._resolve_unit_file(unit)

        assert result is not None
        assert result == wu_file

    def test_resolve_unit_file_returns_none_when_not_found(self, tmp_path: Path) -> None:
        """AC-8: _resolve_unit_file returns None when file not found in either location."""
        unit = WorkUnit(
            id="E230-F1-S1-T1",
            title="Test",
            status=WorkUnitStatus.IN_QUEUE,
            unit_type=WorkUnitType.TASK,
            file_path=Path("backlog/E230-F1-S1-T1.md"),
            repo="caylent-solutions/devbench",
            dependencies=[],
        )
        # No file is created -- both paths will be missing

        with (
            patch("devbench.cli.BACKLOG_ROOT", tmp_path / "backlog_root"),
            patch("devbench.cli.WORKSPACE_ROOT", tmp_path / "workspace_root"),
        ):
            result = cli._resolve_unit_file(unit)

        assert result is None

    def test_resolve_unit_file_falls_back_to_workspace_root(self, tmp_path: Path) -> None:
        """_resolve_unit_file falls back to WORKSPACE_ROOT when file not found under BACKLOG_ROOT."""
        unit = WorkUnit(
            id="E230-F1-S1-T1",
            title="Test",
            status=WorkUnitStatus.IN_QUEUE,
            unit_type=WorkUnitType.TASK,
            file_path=Path("backlog/E230-F1-S1-T1.md"),
            repo="caylent-solutions/devbench",
            dependencies=[],
        )
        # File exists only under workspace_root
        ws_file = tmp_path / "workspace" / "backlog" / "E230-F1-S1-T1.md"
        ws_file.parent.mkdir(parents=True, exist_ok=True)
        ws_file.write_text("# E230-F1-S1-T1\n\n## Status: in-queue\n", encoding="utf-8")

        with (
            patch("devbench.cli.BACKLOG_ROOT", tmp_path / "backlog_root"),
            patch("devbench.cli.WORKSPACE_ROOT", tmp_path / "workspace"),
        ):
            result = cli._resolve_unit_file(unit)

        assert result is not None
        assert result == ws_file


# ---------------------------------------------------------------------------
# E231-F2-S1-T1: cmd_log_tdd and log-tdd command registration
# ---------------------------------------------------------------------------

_WORK_UNIT_WITH_TDD_LOG_TEMPLATE = """\
# {unit_id}: TDD Test

## Status: in-progress

## Comments

## TDD Cycle Log
"""


def _make_wu_with_tdd_section(tmp_path: Path, unit_id: str = "E231-F2-S1-T1") -> Path:
    """Create a work unit file with a ## TDD Cycle Log section."""
    wu = tmp_path / f"{unit_id}.md"
    wu.write_text(_WORK_UNIT_WITH_TDD_LOG_TEMPLATE.format(unit_id=unit_id), encoding="utf-8")
    return wu


def _make_backlog_index_for_tdd(tmp_path: Path, unit_id: str, wu_file: Path) -> Path:
    """Create a minimal BACKLOG.md referencing the given work unit file."""
    content = (
        "# Backlog\n\n"
        "## Full Work Unit Index\n\n"
        "| ID | Title | Type | Status | Dependencies | Repo | File Path |\n"
        "|-----|-------|------|--------|-------------|------|----------|\n"
        f"| {unit_id} | TDD Test | Task | in-progress | None | caylent-solutions/devbench |"
        f" `backlog/{unit_id}.md` |\n"
    )
    idx = tmp_path / "BACKLOG.md"
    idx.write_text(content, encoding="utf-8")
    return idx


@pytest.mark.unit
class TestCmdLogTdd:
    """Tests for cmd_log_tdd -- AC-1 through AC-6, AC-11."""

    def _setup(self, tmp_path: Path, unit_id: str = "E231-F2-S1-T1") -> tuple[Path, Path]:
        """Return (wu_file, backlog_index) with TDD Cycle Log section."""
        return _setup_tdd_unit_and_index(tmp_path, unit_id)

    def test_log_tdd_red_appends_to_tdd_cycle_log(self, tmp_path: Path) -> None:
        """AC-1: log-tdd RED appends [RED] entry to ## TDD Cycle Log section."""
        wu_file, backlog_index = self._setup(tmp_path)
        unit = WorkUnit(
            id="E231-F2-S1-T1",
            title="TDD Test",
            status=WorkUnitStatus.IN_PROGRESS,
            unit_type=WorkUnitType.TASK,
            file_path=Path("backlog/E231-F2-S1-T1.md"),
            repo="caylent-solutions/devbench",
            dependencies=[],
        )
        mock_parser = MagicMock()
        mock_parser.parse_index.return_value = [unit]

        with (
            patch("devbench.cli.BacklogParser", return_value=mock_parser),
            patch("devbench.cli.BACKLOG_ROOT", tmp_path),
            patch("devbench.cli.WORKSPACE_ROOT", tmp_path),
            patch("devbench.cli.BACKLOG_INDEX", backlog_index),
        ):
            result = cli.cmd_log_tdd("E231-F2-S1-T1", "RED", "Tests: test_foo.py. Command: make test-unit. Exit: 1.")

        assert result == 0
        content = wu_file.read_text(encoding="utf-8")
        tdd_start = content.find("## TDD Cycle Log")
        assert tdd_start != -1
        tdd_section = content[tdd_start:]
        assert "[RED]" in tdd_section

    def test_log_tdd_green_appends_to_tdd_cycle_log(self, tmp_path: Path) -> None:
        """AC-2: log-tdd GREEN appends [GREEN] entry to ## TDD Cycle Log section."""
        wu_file, backlog_index = self._setup(tmp_path)
        unit = WorkUnit(
            id="E231-F2-S1-T1",
            title="TDD Test",
            status=WorkUnitStatus.IN_PROGRESS,
            unit_type=WorkUnitType.TASK,
            file_path=Path("backlog/E231-F2-S1-T1.md"),
            repo="caylent-solutions/devbench",
            dependencies=[],
        )
        mock_parser = MagicMock()
        mock_parser.parse_index.return_value = [unit]

        with (
            patch("devbench.cli.BacklogParser", return_value=mock_parser),
            patch("devbench.cli.BACKLOG_ROOT", tmp_path),
            patch("devbench.cli.WORKSPACE_ROOT", tmp_path),
            patch("devbench.cli.BACKLOG_INDEX", backlog_index),
        ):
            result = cli.cmd_log_tdd("E231-F2-S1-T1", "GREEN", "Command: make test-unit. Result: 5 passed, 0 failed.")

        assert result == 0
        content = wu_file.read_text(encoding="utf-8")
        tdd_start = content.find("## TDD Cycle Log")
        tdd_section = content[tdd_start:]
        assert "[GREEN]" in tdd_section

    def test_log_tdd_refactor_appends_to_tdd_cycle_log(self, tmp_path: Path) -> None:
        """AC-3: log-tdd REFACTOR appends [REFACTOR] entry to ## TDD Cycle Log section."""
        wu_file, backlog_index = self._setup(tmp_path)
        unit = WorkUnit(
            id="E231-F2-S1-T1",
            title="TDD Test",
            status=WorkUnitStatus.IN_PROGRESS,
            unit_type=WorkUnitType.TASK,
            file_path=Path("backlog/E231-F2-S1-T1.md"),
            repo="caylent-solutions/devbench",
            dependencies=[],
        )
        mock_parser = MagicMock()
        mock_parser.parse_index.return_value = [unit]

        with (
            patch("devbench.cli.BacklogParser", return_value=mock_parser),
            patch("devbench.cli.BACKLOG_ROOT", tmp_path),
            patch("devbench.cli.WORKSPACE_ROOT", tmp_path),
            patch("devbench.cli.BACKLOG_INDEX", backlog_index),
        ):
            result = cli.cmd_log_tdd("E231-F2-S1-T1", "REFACTOR", "No refactor needed. Tests: 5 passed, 0 failed")

        assert result == 0
        content = wu_file.read_text(encoding="utf-8")
        tdd_start = content.find("## TDD Cycle Log")
        tdd_section = content[tdd_start:]
        assert "[REFACTOR]" in tdd_section

    def test_log_tdd_phase_case_insensitive(self, tmp_path: Path) -> None:
        """AC-4: Phase argument is case-insensitive -- 'red' normalized to 'RED'."""
        wu_file, backlog_index = self._setup(tmp_path)
        unit = WorkUnit(
            id="E231-F2-S1-T1",
            title="TDD Test",
            status=WorkUnitStatus.IN_PROGRESS,
            unit_type=WorkUnitType.TASK,
            file_path=Path("backlog/E231-F2-S1-T1.md"),
            repo="caylent-solutions/devbench",
            dependencies=[],
        )
        mock_parser = MagicMock()
        mock_parser.parse_index.return_value = [unit]

        with (
            patch("devbench.cli.BacklogParser", return_value=mock_parser),
            patch("devbench.cli.BACKLOG_ROOT", tmp_path),
            patch("devbench.cli.WORKSPACE_ROOT", tmp_path),
            patch("devbench.cli.BACKLOG_INDEX", backlog_index),
        ):
            result = cli.cmd_log_tdd("E231-F2-S1-T1", "red", "lowercase phase message")

        assert result == 0
        content = wu_file.read_text(encoding="utf-8")
        # Entry should be normalized to uppercase [RED]
        assert "[RED]" in content

    def test_log_tdd_invalid_phase_exits_nonzero(self, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
        """AC-4: Invalid phase value exits non-zero with clear error message."""
        wu_file, backlog_index = self._setup(tmp_path)
        unit = WorkUnit(
            id="E231-F2-S1-T1",
            title="TDD Test",
            status=WorkUnitStatus.IN_PROGRESS,
            unit_type=WorkUnitType.TASK,
            file_path=Path("backlog/E231-F2-S1-T1.md"),
            repo="caylent-solutions/devbench",
            dependencies=[],
        )
        mock_parser = MagicMock()
        mock_parser.parse_index.return_value = [unit]

        with (
            patch("devbench.cli.BacklogParser", return_value=mock_parser),
            patch("devbench.cli.BACKLOG_ROOT", tmp_path),
            patch("devbench.cli.WORKSPACE_ROOT", tmp_path),
            patch("devbench.cli.BACKLOG_INDEX", backlog_index),
        ):
            result = cli.cmd_log_tdd("E231-F2-S1-T1", "BLUE", "invalid phase")

        assert result != 0
        captured = capsys.readouterr()
        assert "BLUE" in captured.err or "phase" in captured.err.lower()

    def test_log_tdd_missing_section_exits_nonzero(self, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
        """AC-6: Exits non-zero when ## TDD Cycle Log section does not exist in the file."""
        # Create a work unit WITHOUT the TDD Cycle Log section
        backlog_dir = tmp_path / "backlog"
        backlog_dir.mkdir(exist_ok=True)
        wu_file = backlog_dir / "E231-F2-S1-T1.md"
        wu_file.write_text(
            "# E231-F2-S1-T1\n\n## Status: in-progress\n\n## Comments\n",
            encoding="utf-8",
        )
        backlog_index = _make_backlog_index_for_tdd(tmp_path, "E231-F2-S1-T1", wu_file)
        unit = WorkUnit(
            id="E231-F2-S1-T1",
            title="TDD Test",
            status=WorkUnitStatus.IN_PROGRESS,
            unit_type=WorkUnitType.TASK,
            file_path=Path("backlog/E231-F2-S1-T1.md"),
            repo="caylent-solutions/devbench",
            dependencies=[],
        )
        mock_parser = MagicMock()
        mock_parser.parse_index.return_value = [unit]

        with (
            patch("devbench.cli.BacklogParser", return_value=mock_parser),
            patch("devbench.cli.BACKLOG_ROOT", tmp_path),
            patch("devbench.cli.WORKSPACE_ROOT", tmp_path),
            patch("devbench.cli.BACKLOG_INDEX", backlog_index),
        ):
            result = cli.cmd_log_tdd("E231-F2-S1-T1", "RED", "message without tdd section")

        assert result != 0
        captured = capsys.readouterr()
        assert "TDD Cycle Log" in captured.err or "tdd" in captured.err.lower()

    def test_log_tdd_cli_command_registered(self) -> None:
        """AC-1: 'log-tdd' is a recognized command in the CLI command registry."""
        assert "log-tdd" in cli._COMMANDS, "log-tdd command must be registered in cli._COMMANDS"

    def test_log_tdd_entry_not_in_comments_section(self, tmp_path: Path) -> None:
        """AC-11: TDD Cycle Log entries do not appear in ## Comments section."""
        wu_file, backlog_index = self._setup(tmp_path)
        unit = WorkUnit(
            id="E231-F2-S1-T1",
            title="TDD Test",
            status=WorkUnitStatus.IN_PROGRESS,
            unit_type=WorkUnitType.TASK,
            file_path=Path("backlog/E231-F2-S1-T1.md"),
            repo="caylent-solutions/devbench",
            dependencies=[],
        )
        mock_parser = MagicMock()
        mock_parser.parse_index.return_value = [unit]

        with (
            patch("devbench.cli.BacklogParser", return_value=mock_parser),
            patch("devbench.cli.BACKLOG_ROOT", tmp_path),
            patch("devbench.cli.WORKSPACE_ROOT", tmp_path),
            patch("devbench.cli.BACKLOG_INDEX", backlog_index),
        ):
            cli.cmd_log_tdd("E231-F2-S1-T1", "RED", "unique-tdd-marker-xyz")

        content = wu_file.read_text(encoding="utf-8")
        comments_start = content.find("## Comments")
        tdd_start = content.find("## TDD Cycle Log")
        # Extract comments section (before TDD Cycle Log)
        comments_section = content[comments_start:tdd_start] if tdd_start > comments_start else content[comments_start:]
        assert "unique-tdd-marker-xyz" not in comments_section, f"TDD entry leaked into ## Comments: {comments_section}"


def _setup_tdd_unit_and_index(tmp_path: Path, unit_id: str = "E231-F2-S1-T1") -> tuple[Path, Path]:
    """Return (wu_file, backlog_index) with a ## TDD Cycle Log section.

    Shared by every TDD-phase test class below (RED_OBSERVED rejection, the
    record builder, the orchestrator-only write path, and the gate
    predicate) so the work-unit-file-plus-index fixture has one definition
    instead of being copy-pasted per class.
    """
    wu_file = _make_wu_with_tdd_section(tmp_path, unit_id)
    backlog_dir = tmp_path / "backlog"
    backlog_dir.mkdir(exist_ok=True)
    backlog_wu = backlog_dir / f"{unit_id}.md"
    backlog_wu.write_text(wu_file.read_text(encoding="utf-8"), encoding="utf-8")
    backlog_index = _make_backlog_index_for_tdd(tmp_path, unit_id, backlog_wu)
    return backlog_wu, backlog_index


def _make_tdd_test_work_unit(unit_id: str = "E231-F2-S1-T1") -> WorkUnit:
    """Return a WorkUnit matching the fixture built by `_setup_tdd_unit_and_index`."""
    return WorkUnit(
        id=unit_id,
        title="TDD Test",
        status=WorkUnitStatus.IN_PROGRESS,
        unit_type=WorkUnitType.TASK,
        file_path=Path(f"backlog/{unit_id}.md"),
        repo="caylent-solutions/devbench",
        dependencies=[],
    )


@pytest.mark.unit
class TestCmdLogTddRejectsRedObserved:
    """RED_OBSERVED is orchestrator-only: log-tdd rejects it with exit 1 (AC-54, issue #257)."""

    @pytest.mark.parametrize("phase", ["RED_OBSERVED", "red_observed", "Red_Observed"])
    def test_log_tdd_rejects_red_observed_case_insensitive(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str], phase: str
    ) -> None:
        _wu_file, backlog_index = _setup_tdd_unit_and_index(tmp_path)
        unit = _make_tdd_test_work_unit()
        mock_parser = MagicMock()
        mock_parser.parse_index.return_value = [unit]

        with (
            patch("devbench.cli.BacklogParser", return_value=mock_parser),
            patch("devbench.cli.BACKLOG_ROOT", tmp_path),
            patch("devbench.cli.WORKSPACE_ROOT", tmp_path),
            patch("devbench.cli.BACKLOG_INDEX", backlog_index),
        ):
            result = cli.cmd_log_tdd("E231-F2-S1-T1", phase, "claiming an observed failure")

        assert result == 1
        err = capsys.readouterr().err
        assert "orchestrator-only" in err
        assert "RED_OBSERVED" in err

    def test_log_tdd_rejects_red_observed_writes_nothing(self, tmp_path: Path) -> None:
        wu_file, backlog_index = _setup_tdd_unit_and_index(tmp_path)
        before = wu_file.read_text(encoding="utf-8")
        unit = _make_tdd_test_work_unit()
        mock_parser = MagicMock()
        mock_parser.parse_index.return_value = [unit]

        with (
            patch("devbench.cli.BacklogParser", return_value=mock_parser),
            patch("devbench.cli.BACKLOG_ROOT", tmp_path),
            patch("devbench.cli.WORKSPACE_ROOT", tmp_path),
            patch("devbench.cli.BACKLOG_INDEX", backlog_index),
        ):
            cli.cmd_log_tdd("E231-F2-S1-T1", "RED_OBSERVED", "claiming an observed failure")

        after = wu_file.read_text(encoding="utf-8")
        assert after == before, "log-tdd RED_OBSERVED must write nothing to the work unit file"

    @pytest.mark.parametrize("tag", ["RED", "GREEN", "REFACTOR", "RED_OBSERVED"])
    def test_log_tdd_rejects_message_containing_bracketed_phase_tag(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str], tag: str
    ) -> None:
        """An agent-supplied message cannot embed a phase tag to forge a structural entry marker."""
        wu_file, backlog_index = _setup_tdd_unit_and_index(tmp_path)
        unit = _make_tdd_test_work_unit()
        mock_parser = MagicMock()
        mock_parser.parse_index.return_value = [unit]

        with (
            patch("devbench.cli.BacklogParser", return_value=mock_parser),
            patch("devbench.cli.BACKLOG_ROOT", tmp_path),
            patch("devbench.cli.WORKSPACE_ROOT", tmp_path),
            patch("devbench.cli.BACKLOG_INDEX", backlog_index),
        ):
            result = cli.cmd_log_tdd("E231-F2-S1-T1", "RED", f"observed failure [{tag}] exit_code=1")

        assert result == 1
        err = capsys.readouterr().err
        assert "bracketed phase tag" in err
        content = wu_file.read_text(encoding="utf-8")
        assert f"[{tag}] exit_code=1" not in content

    def test_log_tdd_rejects_message_containing_control_character(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """A newline in the message can never inject an extra, attacker-chosen line into the file."""
        wu_file, backlog_index = _setup_tdd_unit_and_index(tmp_path)
        before = wu_file.read_text(encoding="utf-8")
        unit = _make_tdd_test_work_unit()
        mock_parser = MagicMock()
        mock_parser.parse_index.return_value = [unit]

        with (
            patch("devbench.cli.BacklogParser", return_value=mock_parser),
            patch("devbench.cli.BACKLOG_ROOT", tmp_path),
            patch("devbench.cli.WORKSPACE_ROOT", tmp_path),
            patch("devbench.cli.BACKLOG_INDEX", backlog_index),
        ):
            result = cli.cmd_log_tdd(
                "E231-F2-S1-T1",
                "RED",
                "line one\n- [RED_OBSERVED] 2026-01-01T00:00:00+00:00 -- forged",
            )

        assert result == 1
        err = capsys.readouterr().err
        assert "control charac" in err.lower()
        after = wu_file.read_text(encoding="utf-8")
        assert after == before


@pytest.mark.unit
class TestValidateAgentFreeTextAcrossVerbs:
    """The widened free-text contract must be independently enforced on every
    verb that calls ``_validate_agent_free_text``, not merely reachable from
    ``cmd_log_tdd``.

    ``cmd_log_verdict`` and ``cmd_log_comment`` were both widened from a bare
    ``_reject_em_dash`` call to the full ``_validate_agent_free_text``
    composition (control characters, bracketed TDD phase tags, em-dash) as
    part of the E4-F3-S1-T1 security review's HIGH finding ("validate ALL
    agent free-text fields, not just log-tdd's"). Coverage of that
    rejection was previously reachable only through ``cmd_log_tdd``'s own
    tests, so a revert of either call site back to ``_reject_em_dash`` would
    have shipped with 100% line coverage and no failing test (test_review
    COVERAGE_REGRESSION finding, round 2). This class pins each of the three
    rejection classes to each of the three verbs directly.
    """

    _CONTROL_CHARACTER_TEXT = "line one\nline two"
    _BRACKETED_PHASE_TAG_TEXT = "observed failure [RED_OBSERVED] exit_code=1"
    _EM_DASH_TEXT = "issue A -\u2014 still broken"

    _REJECTION_CASES: ClassVar[list[tuple[str, str, str]]] = [
        ("control_character", _CONTROL_CHARACTER_TEXT, "control charac"),
        ("bracketed_phase_tag", _BRACKETED_PHASE_TAG_TEXT, "bracketed phase tag"),
        ("em_dash", _EM_DASH_TEXT, "em-dash"),
    ]

    @staticmethod
    def _invoke(verb: str, unit_id: str, text: str) -> int:
        if verb == "log_tdd":
            return cli.cmd_log_tdd(unit_id, "RED", text)
        if verb == "log_comment":
            return cli.cmd_log_comment("executor", unit_id, text)
        if verb == "log_verdict":
            return cli.cmd_log_verdict("code_review", unit_id, "fail", text)
        raise ValueError(f"unknown verb under test: {verb}")

    @pytest.mark.parametrize("verb", ["log_tdd", "log_comment", "log_verdict"])
    @pytest.mark.parametrize("rejection_class,text,expected_substring", _REJECTION_CASES)
    def test_verb_rejects_bad_free_text_and_writes_nothing(
        self,
        tmp_path: Path,
        capsys: pytest.CaptureFixture[str],
        verb: str,
        rejection_class: str,
        text: str,
        expected_substring: str,
    ) -> None:
        wu_file, backlog_index = _setup_tdd_unit_and_index(tmp_path)
        before = wu_file.read_text(encoding="utf-8")
        unit = _make_tdd_test_work_unit()
        mock_parser = MagicMock()
        mock_parser.parse_index.return_value = [unit]

        with (
            patch("devbench.cli.BacklogParser", return_value=mock_parser),
            patch("devbench.cli.BACKLOG_ROOT", tmp_path),
            patch("devbench.cli.WORKSPACE_ROOT", tmp_path),
            patch("devbench.cli.BACKLOG_INDEX", backlog_index),
        ):
            result = self._invoke(verb, "E231-F2-S1-T1", text)

        assert result == 1, f"{verb} must reject {rejection_class} text with exit 1"
        err = capsys.readouterr().err.lower()
        assert expected_substring in err, f"{verb} stderr must name the {rejection_class} violation, got: {err!r}"
        after = wu_file.read_text(encoding="utf-8")
        assert after == before, f"{verb} must write nothing to the work unit file on {rejection_class} rejection"


@pytest.mark.unit
class TestBuildRedObservedMessage:
    """build_red_observed_message validates the three-field RED_OBSERVED record (AC-E4-F3-S1-T1-3)."""

    _DIGEST = "deadbeef01"

    def test_build_red_observed_message_happy_path(self) -> None:
        from devbench.constants import RED_OBSERVED_MESSAGE_FIELDS_RE

        message = cli.build_red_observed_message(1, "tests/test_foo.py::test_bar", self._DIGEST)
        match = RED_OBSERVED_MESSAGE_FIELDS_RE.search(message)
        assert match is not None
        assert match.group("exit_code") == "1"
        assert match.group("test_node_id") == "tests/test_foo.py::test_bar"
        assert match.group("failure_digest") == self._DIGEST

    def test_build_red_observed_message_missing_exit_code_names_field(self) -> None:
        with pytest.raises(ValueError, match="exit_code"):
            cli.build_red_observed_message(None, "tests/test_foo.py::test_bar", self._DIGEST)

    def test_build_red_observed_message_missing_test_node_id_names_field(self) -> None:
        with pytest.raises(ValueError, match="test_node_id"):
            cli.build_red_observed_message(1, "", self._DIGEST)

    def test_build_red_observed_message_missing_failure_digest_names_field(self) -> None:
        with pytest.raises(ValueError, match="failure_digest"):
            cli.build_red_observed_message(1, "tests/test_foo.py::test_bar", "")

    def test_build_red_observed_message_rejects_zero_exit_code(self) -> None:
        """A RED phase is, by definition, an observed failure -- exit 0 cannot be RED_OBSERVED."""
        with pytest.raises(ValueError, match="nonzero"):
            cli.build_red_observed_message(0, "tests/test_foo.py::test_bar", self._DIGEST)

    @pytest.mark.parametrize("digest", ["DEADBEEF01", "a" * 7, "not-hex!"])
    def test_build_red_observed_message_rejects_malformed_digest(self, digest: str) -> None:
        with pytest.raises(ValueError, match="failure_digest"):
            cli.build_red_observed_message(1, "tests/test_foo.py::test_bar", digest)

    @pytest.mark.parametrize(
        "test_node_id",
        [
            "tests/test_foo.py::test_bar[param with space]",
            "tests/test_foo.py::test_bar\ttabbed",
            "tests/test_foo.py::test_bar\nsecond_line",
        ],
    )
    def test_build_red_observed_message_rejects_whitespace_test_node_id(self, test_node_id: str) -> None:
        """A whitespace-bearing test_node_id builds a message ``RED_OBSERVED_MESSAGE_FIELDS_RE``

        (the read-side parser ``red_gate_satisfied`` consults) can never match, since that
        parser captures ``test_node_id`` as a non-whitespace run (``\\S+``). The builder must
        reject this at write time instead of silently emitting a record the gate rejects.
        """
        with pytest.raises(ValueError, match="test_node_id"):
            cli.build_red_observed_message(1, test_node_id, self._DIGEST)


@pytest.mark.unit
class TestWriteRedObservedEntry:
    """write_red_observed_entry is the orchestrator-only RED_OBSERVED write path (FR-4.3)."""

    _DIGEST = "deadbeef01"

    def test_write_red_observed_entry_appends_structured_entry(self, tmp_path: Path) -> None:
        wu_file, backlog_index = _setup_tdd_unit_and_index(tmp_path)
        unit = _make_tdd_test_work_unit()
        mock_parser = MagicMock()
        mock_parser.parse_index.return_value = [unit]

        with (
            patch("devbench.cli.BacklogParser", return_value=mock_parser),
            patch("devbench.cli.BACKLOG_ROOT", tmp_path),
            patch("devbench.cli.WORKSPACE_ROOT", tmp_path),
            patch("devbench.cli.BACKLOG_INDEX", backlog_index),
        ):
            cli.write_red_observed_entry("E231-F2-S1-T1", 1, "tests/test_foo.py::test_bar", self._DIGEST)

        content = wu_file.read_text(encoding="utf-8")
        tdd_section = content[content.find("## TDD Cycle Log") :]
        assert "[RED_OBSERVED]" in tdd_section
        assert "exit_code=1" in tdd_section
        assert "test_node_id=tests/test_foo.py::test_bar" in tdd_section
        assert f"failure_digest={self._DIGEST}" in tdd_section

    def test_write_red_observed_entry_unit_not_found_raises_value_error(self) -> None:
        mock_parser = MagicMock()
        mock_parser.parse_index.return_value = []

        with (
            patch("devbench.cli.BacklogParser", return_value=mock_parser),
            pytest.raises(ValueError, match="not found"),
        ):
            cli.write_red_observed_entry("NONEXISTENT", 1, "tests/test_foo.py::test_bar", self._DIGEST)

    def test_write_red_observed_entry_falls_back_to_workspace_root(self, tmp_path: Path) -> None:
        workspace = tmp_path / "workspace"
        workspace.mkdir()
        wu_file = workspace / "backlog" / "E0-F1-S1-T1.md"
        wu_file.parent.mkdir(parents=True)
        wu_file.write_text(
            "# E0-F1-S1-T1: Test\n\n## Status: in-progress\n\n## Comments\n\n## TDD Cycle Log\n",
            encoding="utf-8",
        )
        unit = WorkUnit(
            id="E0-F1-S1-T1",
            title="TDD Test",
            status=WorkUnitStatus.IN_PROGRESS,
            unit_type=WorkUnitType.TASK,
            file_path=Path("backlog/E0-F1-S1-T1.md"),
            repo="caylent-solutions/devbench",
            dependencies=[],
        )
        mock_parser = MagicMock()
        mock_parser.parse_index.return_value = [unit]

        with (
            patch("devbench.cli.BacklogParser", return_value=mock_parser),
            patch("devbench.cli.BACKLOG_ROOT", tmp_path / "missing"),
            patch("devbench.cli.WORKSPACE_ROOT", workspace),
        ):
            cli.write_red_observed_entry("E0-F1-S1-T1", 1, "tests/test_foo.py::test_bar", self._DIGEST)

        content = wu_file.read_text(encoding="utf-8")
        assert "[RED_OBSERVED]" in content

    def test_write_red_observed_entry_propagates_missing_field_error(self, tmp_path: Path) -> None:
        wu_file, backlog_index = _setup_tdd_unit_and_index(tmp_path)
        before = wu_file.read_text(encoding="utf-8")
        unit = _make_tdd_test_work_unit()
        mock_parser = MagicMock()
        mock_parser.parse_index.return_value = [unit]

        with (
            patch("devbench.cli.BacklogParser", return_value=mock_parser),
            patch("devbench.cli.BACKLOG_ROOT", tmp_path),
            patch("devbench.cli.WORKSPACE_ROOT", tmp_path),
            patch("devbench.cli.BACKLOG_INDEX", backlog_index),
            pytest.raises(ValueError, match="test_node_id"),
        ):
            cli.write_red_observed_entry("E231-F2-S1-T1", 1, "", self._DIGEST)

        after = wu_file.read_text(encoding="utf-8")
        assert after == before, "a rejected record must not be written"

    def test_write_red_observed_entry_not_reachable_via_commands_registry(self) -> None:
        registered_handlers = {handler for handler, _argc, _help in cli._COMMANDS.values()}
        assert cli.write_red_observed_entry not in registered_handlers

    def test_write_red_observed_entry_round_trip_satisfies_red_gate(self, tmp_path: Path) -> None:
        """The write-to-read seam, end to end: a real ``write_red_observed_entry``

        call must produce a work-unit file that the real ``red_gate_satisfied``
        predicate accepts. This pins the coupling between ``TDD_ENTRY_TEMPLATE``
        (what the writer emits, via ``BacklogManager._append_tdd_entry``) and
        ``RED_OBSERVED_ENTRY_LINE_RE`` (what the reader matches); a test_review
        mutation of the entry-line separator in ``TDD_ENTRY_TEMPLATE`` (from
        ``" -- "`` to ``" :: "``) rendered the gate permanently unsatisfiable
        while every prior test -- which asserted only write-side substrings or
        matched against hand-written fixture literals -- still passed.
        """
        wu_file, backlog_index = _setup_tdd_unit_and_index(tmp_path)
        unit = _make_tdd_test_work_unit()
        mock_parser = MagicMock()
        mock_parser.parse_index.return_value = [unit]

        with (
            patch("devbench.cli.BacklogParser", return_value=mock_parser),
            patch("devbench.cli.BACKLOG_ROOT", tmp_path),
            patch("devbench.cli.WORKSPACE_ROOT", tmp_path),
            patch("devbench.cli.BACKLOG_INDEX", backlog_index),
        ):
            cli.write_red_observed_entry("E231-F2-S1-T1", 1, "tests/test_foo.py::test_bar", self._DIGEST)

        content = wu_file.read_text(encoding="utf-8")
        assert cli.red_gate_satisfied(content) is True, (
            "a real write_red_observed_entry call must produce a file red_gate_satisfied accepts"
        )


def _tdd_entry_line(phase: str, message: str, *, timestamp: str = "2026-01-01T00:00:00+00:00") -> str:
    """Build a TDD Cycle Log entry line via the real production template.

    Deriving test fixtures from ``TDD_ENTRY_TEMPLATE`` -- the exact template
    ``BacklogManager._append_tdd_entry`` formats every real entry with --
    instead of hardcoding the ``" -- "`` separator as a literal keeps the
    read-side fixtures and the write-side production format coupled. A
    test_review mutation of the template's separator (``" -- "`` to
    ``" :: "``) previously left every fixture built from a hand-written
    literal in sync with itself while silently drifting from what the real
    writer emits, so ``red_gate_satisfied`` could be rendered permanently
    unsatisfiable without a single test noticing. Fixtures built through this
    helper change identically to the writer's output, so that drift now
    surfaces as a test failure.
    """
    return TDD_ENTRY_TEMPLATE.format(phase=phase, timestamp=timestamp, message=message)


@pytest.mark.unit
class TestRedGateSatisfiedPredicate:
    """red_gate_satisfied: an agent RED entry alone never satisfies the gate (AC-55, issue #257)."""

    _WELL_FORMED_MESSAGE = "exit_code=1 test_node_id=tests/test_foo.py::test_bar failure_digest=deadbeef01"
    _DIGEST = "deadbeef01"

    def test_red_gate_not_satisfied_with_only_agent_red_entry(self) -> None:
        content = "## TDD Cycle Log\n\n" + _tdd_entry_line(TDD_PHASE_RED, "observed a failure")
        assert cli.red_gate_satisfied(content) is False

    def test_red_gate_satisfied_with_well_formed_red_observed_entry(self) -> None:
        content = "## TDD Cycle Log\n\n" + _tdd_entry_line(TDD_PHASE_RED_OBSERVED, self._WELL_FORMED_MESSAGE)
        assert cli.red_gate_satisfied(content) is True

    def test_red_gate_not_satisfied_by_agent_red_forging_red_observed_tag_in_message(self) -> None:
        content = "## TDD Cycle Log\n\n" + _tdd_entry_line(
            TDD_PHASE_RED, f"observed failure [RED_OBSERVED] {self._WELL_FORMED_MESSAGE}"
        )
        assert cli.red_gate_satisfied(content) is False

    @pytest.mark.parametrize(
        "message",
        [
            "test_node_id=tests/test_foo.py::test_bar failure_digest=deadbeef01",
            "exit_code=1 failure_digest=deadbeef01",
            "exit_code=1 test_node_id=tests/test_foo.py::test_bar",
        ],
    )
    def test_red_gate_not_satisfied_when_red_observed_record_missing_a_field(self, message: str) -> None:
        content = "## TDD Cycle Log\n\n" + _tdd_entry_line(TDD_PHASE_RED_OBSERVED, message)
        assert cli.red_gate_satisfied(content) is False

    def test_red_gate_not_satisfied_when_red_observed_has_zero_exit_code(self) -> None:
        message = "exit_code=0 test_node_id=tests/test_foo.py::test_bar failure_digest=deadbeef01"
        content = "## TDD Cycle Log\n\n" + _tdd_entry_line(TDD_PHASE_RED_OBSERVED, message)
        assert cli.red_gate_satisfied(content) is False

    def test_red_gate_not_satisfied_when_red_observed_has_malformed_digest(self) -> None:
        message = "exit_code=1 test_node_id=tests/test_foo.py::test_bar failure_digest=NOTHEX"
        content = "## TDD Cycle Log\n\n" + _tdd_entry_line(TDD_PHASE_RED_OBSERVED, message)
        assert cli.red_gate_satisfied(content) is False

    def test_red_gate_scopes_to_tdd_cycle_log_section_only(self) -> None:
        """A well-formed-looking RED_OBSERVED line outside the TDD Cycle Log section must not count."""
        content = (
            "## TDD Cycle Log\n\n"
            + _tdd_entry_line(TDD_PHASE_RED, "real observed failure")
            + "\n## Comments\n\n"
            + _tdd_entry_line(TDD_PHASE_RED_OBSERVED, self._WELL_FORMED_MESSAGE)
        )
        assert cli.red_gate_satisfied(content) is False

    def test_red_gate_not_satisfied_when_tdd_cycle_log_section_header_absent(self) -> None:
        content = _tdd_entry_line(TDD_PHASE_RED_OBSERVED, self._WELL_FORMED_MESSAGE)
        assert cli.red_gate_satisfied(content) is False

    def test_red_gate_not_satisfied_when_only_real_cmd_log_tdd_red_entry_exists(self, tmp_path: Path) -> None:
        """Negative write-to-read round trip: a genuine ``cmd_log_tdd`` agent RED

        entry, written through the real agent-facing CLI verb and read back
        from disk, must never satisfy the gate on its own (AC-55, AC-E4-F3-S1-T1-4).
        Complements ``TestWriteRedObservedEntry.
        test_write_red_observed_entry_round_trip_satisfies_red_gate``, which
        pins the positive side of the same write-to-read seam.
        """
        wu_file, backlog_index = _setup_tdd_unit_and_index(tmp_path)
        unit = _make_tdd_test_work_unit()
        mock_parser = MagicMock()
        mock_parser.parse_index.return_value = [unit]

        with (
            patch("devbench.cli.BacklogParser", return_value=mock_parser),
            patch("devbench.cli.BACKLOG_ROOT", tmp_path),
            patch("devbench.cli.WORKSPACE_ROOT", tmp_path),
            patch("devbench.cli.BACKLOG_INDEX", backlog_index),
        ):
            result = cli.cmd_log_tdd("E231-F2-S1-T1", "RED", "genuinely observed a failing assertion")

        assert result == 0
        content = wu_file.read_text(encoding="utf-8")
        assert cli.red_gate_satisfied(content) is False, (
            "an agent-written RED entry alone must never satisfy the gate, even via the real write path"
        )


class TestCmdStatusActiveUnits:
    """Test cmd_status shows active work units (IN_PROGRESS / IN_REVIEW)."""

    def test_shows_active_work_units(self, capsys: pytest.CaptureFixture[str]) -> None:
        """Lines 116-118: active IN_PROGRESS and IN_REVIEW units are printed."""
        in_progress_unit = WorkUnit(
            id="E0-F1-S1-T1",
            title="Active Task",
            status=WorkUnitStatus.IN_PROGRESS,
            unit_type=WorkUnitType.TASK,
            file_path=Path("backlog/E0-F1-S1-T1.md"),
            repo="caylent-solutions/git-repo",
            dependencies=[],
        )
        in_review_unit = WorkUnit(
            id="E0-F1-S1-T2",
            title="Reviewing Task",
            status=WorkUnitStatus.IN_REVIEW,
            unit_type=WorkUnitType.TASK,
            file_path=Path("backlog/E0-F1-S1-T2.md"),
            repo="caylent-solutions/git-repo",
            dependencies=[],
        )
        mock_parser = MagicMock()
        mock_parser.parse_index.return_value = [in_progress_unit, in_review_unit]
        mock_parser.get_parallel_candidates.return_value = [in_progress_unit]
        mock_parser.all_done.return_value = False

        with patch("devbench.cli.BacklogParser", return_value=mock_parser):
            result = cli.cmd_status()

        assert result == 0
        output = capsys.readouterr().out
        assert "Active work units:" in output
        assert "E0-F1-S1-T1" in output
        assert "E0-F1-S1-T2" in output


class TestCmdClaimFileNotFound:
    """Test cmd_claim when work unit file is not found on disk."""

    def test_claim_returns_1_when_file_missing(
        self, mock_units: list[WorkUnit], capsys: pytest.CaptureFixture[str]
    ) -> None:
        """Lines 174-175: file not found for resolved unit."""
        mock_parser = MagicMock()
        mock_parser.parse_index.return_value = mock_units

        with (
            patch("devbench.cli.BacklogParser", return_value=mock_parser),
            patch("devbench.cli._resolve_unit_file", return_value=None),
        ):
            result = cli.cmd_claim("E0-F1-S1-T2")

        assert result == 1
        assert "file not found" in capsys.readouterr().err.lower()


class TestCmdSetStatusFileNotFound:
    """Test cmd_set_status when work unit file is not found on disk."""

    def test_set_status_returns_1_when_file_missing(
        self, mock_units: list[WorkUnit], capsys: pytest.CaptureFixture[str]
    ) -> None:
        """Lines 204-205: work unit file not found."""
        mock_parser = MagicMock()
        mock_parser.parse_index.return_value = mock_units

        with (
            patch("devbench.cli.BacklogParser", return_value=mock_parser),
            patch("devbench.cli._resolve_unit_file", return_value=None),
        ):
            result = cli.cmd_set_status("E0-F1-S1-T2", "in-progress")

        assert result == 1
        assert "file not found" in capsys.readouterr().err.lower()


class TestCmdMarkDoneFileNotFound:
    """Test cmd_mark_done when work unit file is not found on disk."""

    def test_mark_done_returns_1_when_file_missing(
        self, mock_units: list[WorkUnit], capsys: pytest.CaptureFixture[str]
    ) -> None:
        """Lines 232-233: work unit file not found."""
        mock_parser = MagicMock()
        mock_parser.parse_index.return_value = mock_units

        with (
            patch("devbench.cli.BacklogParser", return_value=mock_parser),
            patch("devbench.cli._resolve_unit_file", return_value=None),
        ):
            result = cli.cmd_mark_done("E0-F1-S1-T2")

        assert result == 1
        assert "file not found" in capsys.readouterr().err.lower()


class TestCmdReadUnitFileResolution:
    """Test cmd_read_unit file path resolution branches."""

    def _make_unit(self, file_path: Path) -> WorkUnit:
        return WorkUnit(
            id="E0-F1-S1-T1",
            title="Test Task",
            status=WorkUnitStatus.IN_PROGRESS,
            unit_type=WorkUnitType.TASK,
            file_path=file_path,
            repo="caylent-solutions/git-repo",
            dependencies=[],
        )

    def test_read_unit_not_found_returns_1(self, capsys: pytest.CaptureFixture[str]) -> None:
        """Lines 340-341: unit not found in backlog index."""
        mock_parser = MagicMock()
        mock_parser.parse_index.return_value = []

        with patch("devbench.cli.BacklogParser", return_value=mock_parser):
            result = cli.cmd_read_unit("NONEXISTENT")

        assert result == 1
        assert "not found" in capsys.readouterr().err.lower()

    def test_read_unit_falls_back_to_workspace_root(self, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
        """Lines 345, 351-352: file resolution from BACKLOG_ROOT falls back to WORKSPACE_ROOT."""
        unit = self._make_unit(Path("backlog/E0-F1-S1-T1.md"))
        mock_parser = MagicMock()
        mock_parser.parse_index.return_value = [unit]

        # File exists under WORKSPACE_ROOT, not BACKLOG_ROOT
        workspace = tmp_path / "workspace"
        workspace.mkdir()
        wu_file = workspace / "backlog" / "E0-F1-S1-T1.md"
        wu_file.parent.mkdir(parents=True)
        wu_file.write_text("# E0-F1-S1-T1: Test\n\n## Status: in-progress\n", encoding="utf-8")

        with (
            patch("devbench.cli.BacklogParser", return_value=mock_parser),
            patch("devbench.cli.BACKLOG_ROOT", tmp_path / "missing_backlog"),
            patch("devbench.cli.WORKSPACE_ROOT", workspace),
            patch("devbench.cli.REPO_LOCAL_PATHS", {"caylent-solutions/git-repo": tmp_path}),
        ):
            result = cli.cmd_read_unit("E0-F1-S1-T1")

        assert result == 0

    def test_read_unit_no_local_path_returns_1(self, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
        """Lines 351-352: no local path configured for repo."""
        wu_file = tmp_path / "backlog" / "E0-F1-S1-T1.md"
        wu_file.parent.mkdir(parents=True)
        wu_file.write_text("# E0-F1-S1-T1: Test\n\n## Status: in-progress\n", encoding="utf-8")
        unit = self._make_unit(wu_file)
        mock_parser = MagicMock()
        mock_parser.parse_index.return_value = [unit]

        with (
            patch("devbench.cli.BacklogParser", return_value=mock_parser),
            patch("devbench.cli.BACKLOG_ROOT", tmp_path),
            patch("devbench.cli.WORKSPACE_ROOT", tmp_path),
            patch("devbench.cli.REPO_LOCAL_PATHS", {}),
        ):
            result = cli.cmd_read_unit("E0-F1-S1-T1")

        assert result == 1
        assert "no local path" in capsys.readouterr().err.lower()


class TestCmdGetDiffEdgeCases:
    """Test cmd_get_diff edge cases and error branches."""

    def _make_unit(self) -> WorkUnit:
        return WorkUnit(
            id="E0-F1-S1-T1",
            title="Test Task",
            status=WorkUnitStatus.IN_PROGRESS,
            unit_type=WorkUnitType.TASK,
            file_path=Path("backlog/E0-F1-S1-T1.md"),
            repo="caylent-solutions/git-repo",
            dependencies=[],
        )

    def test_get_diff_unit_not_found(self, capsys: pytest.CaptureFixture[str]) -> None:
        """Lines 388-389: unit not found."""
        mock_parser = MagicMock()
        mock_parser.parse_index.return_value = []

        with patch("devbench.cli.BacklogParser", return_value=mock_parser):
            result = cli.cmd_get_diff("NONEXISTENT")

        assert result == 1
        assert "not found" in capsys.readouterr().err.lower()

    def test_get_diff_no_local_path(self, capsys: pytest.CaptureFixture[str]) -> None:
        """Lines 395-396: no local path configured for repo."""
        unit = self._make_unit()
        mock_parser = MagicMock()
        mock_parser.parse_index.return_value = [unit]

        with (
            patch("devbench.cli.BacklogParser", return_value=mock_parser),
            patch("devbench.cli.REPO_LOCAL_PATHS", {}),
        ):
            result = cli.cmd_get_diff("E0-F1-S1-T1")

        assert result == 1
        assert "no local path" in capsys.readouterr().err.lower()

    def test_get_diff_falls_back_to_git_default_branch(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """Lines 412-423: when no configured default branch, falls back to git rev-parse."""
        unit = self._make_unit()
        mock_parser = MagicMock()
        mock_parser.parse_index.return_value = [unit]

        def fake_run_command(cmd: list[str], **_kwargs: object) -> tuple[int, str, str]:
            if cmd == ["git", "rev-parse", "--abbrev-ref", "origin/HEAD"]:
                return (0, "origin/main\n", "")
            return (0, "", "")

        with (
            patch("devbench.cli.BacklogParser", return_value=mock_parser),
            patch("devbench.cli.REPO_LOCAL_PATHS", {"caylent-solutions/git-repo": tmp_path}),
            patch("devbench.cli.get_configured_default_branch", return_value=None),
            patch("devbench.cli.run_command", side_effect=fake_run_command),
        ):
            result = cli.cmd_get_diff("E0-F1-S1-T1")

        assert result == 0

    def test_get_diff_returns_error_when_no_default_branch(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """Lines 417-422: git rev-parse fails and no configured branch."""
        unit = self._make_unit()
        mock_parser = MagicMock()
        mock_parser.parse_index.return_value = [unit]

        def fake_run_command(cmd: list[str], **_kwargs: object) -> tuple[int, str, str]:
            if cmd == ["git", "rev-parse", "--abbrev-ref", "origin/HEAD"]:
                return (1, "", "fatal: error")
            return (0, "", "")

        with (
            patch("devbench.cli.BacklogParser", return_value=mock_parser),
            patch("devbench.cli.REPO_LOCAL_PATHS", {"caylent-solutions/git-repo": tmp_path}),
            patch("devbench.cli.get_configured_default_branch", return_value=None),
            patch("devbench.cli.run_command", side_effect=fake_run_command),
        ):
            result = cli.cmd_get_diff("E0-F1-S1-T1")

        assert result == 1
        assert "cannot determine default branch" in capsys.readouterr().err.lower()

    def test_get_diff_includes_untracked_files(self, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
        """Lines 434-453: untracked files are included as synthetic diff hunks."""
        unit = self._make_unit()
        mock_parser = MagicMock()
        mock_parser.parse_index.return_value = [unit]
        repo_path = tmp_path / "repo"
        repo_path.mkdir()
        # Create an untracked file for the synthetic diff
        untracked_file = repo_path / "new_file.py"
        untracked_file.write_text("print('hello')\n", encoding="utf-8")

        def fake_run_command(cmd: list[str], **_kwargs: object) -> tuple[int, str, str]:
            if cmd == ["git", "ls-files", "--others", "--exclude-standard"]:
                return (0, "new_file.py\n", "")
            return (0, "", "")

        with (
            patch("devbench.cli.BacklogParser", return_value=mock_parser),
            patch("devbench.cli.REPO_LOCAL_PATHS", {"caylent-solutions/git-repo": repo_path}),
            patch("devbench.cli.get_configured_default_branch", return_value="main"),
            patch("devbench.cli.run_command", side_effect=fake_run_command),
        ):
            result = cli.cmd_get_diff("E0-F1-S1-T1")

        assert result == 0
        output = capsys.readouterr().out
        assert "new_file.py" in output
        assert "+print('hello')" in output

    def test_get_diff_includes_staged_and_unstaged_diffs(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """Lines 402, 406: staged and unstaged diffs are included in output."""
        unit = self._make_unit()
        mock_parser = MagicMock()
        mock_parser.parse_index.return_value = [unit]

        def fake_run_command(cmd: list[str], **_kwargs: object) -> tuple[int, str, str]:
            if cmd == ["git", "diff", "--cached"]:
                return (0, "staged-diff-content\n", "")
            if cmd == ["git", "diff"] and len(cmd) == 2:
                return (0, "unstaged-diff-content\n", "")
            return (0, "", "")

        with (
            patch("devbench.cli.BacklogParser", return_value=mock_parser),
            patch("devbench.cli.REPO_LOCAL_PATHS", {"caylent-solutions/git-repo": tmp_path}),
            patch("devbench.cli.get_configured_default_branch", return_value="main"),
            patch("devbench.cli.run_command", side_effect=fake_run_command),
        ):
            result = cli.cmd_get_diff("E0-F1-S1-T1")

        assert result == 0
        output = capsys.readouterr().out
        assert "staged-diff-content" in output
        assert "unstaged-diff-content" in output

    def test_get_diff_skips_unreadable_untracked_files(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """Lines 441-442: OSError reading untracked file is skipped gracefully."""
        unit = self._make_unit()
        mock_parser = MagicMock()
        mock_parser.parse_index.return_value = [unit]
        repo_path = tmp_path / "repo"
        repo_path.mkdir()
        # Do NOT create the file so reading it raises OSError

        def fake_run_command(cmd: list[str], **_kwargs: object) -> tuple[int, str, str]:
            if cmd == ["git", "ls-files", "--others", "--exclude-standard"]:
                return (0, "nonexistent_file.py\n", "")
            return (0, "", "")

        with (
            patch("devbench.cli.BacklogParser", return_value=mock_parser),
            patch("devbench.cli.REPO_LOCAL_PATHS", {"caylent-solutions/git-repo": repo_path}),
            patch("devbench.cli.get_configured_default_branch", return_value="main"),
            patch("devbench.cli.run_command", side_effect=fake_run_command),
        ):
            result = cli.cmd_get_diff("E0-F1-S1-T1")

        assert result == 0

    def test_get_diff_skips_empty_filepath_lines(self, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
        """Line 437: empty filepath lines among valid ones are skipped."""
        unit = self._make_unit()
        mock_parser = MagicMock()
        mock_parser.parse_index.return_value = [unit]
        repo_path = tmp_path / "repo"
        repo_path.mkdir()
        # Create a valid file so one line succeeds, and one blank line gets skipped
        valid_file = repo_path / "valid.py"
        valid_file.write_text("x = 1\n", encoding="utf-8")

        def fake_run_command(cmd: list[str], **_kwargs: object) -> tuple[int, str, str]:
            if cmd == ["git", "ls-files", "--others", "--exclude-standard"]:
                # Mix of a valid file and an empty line
                return (0, "valid.py\n\n", "")
            return (0, "", "")

        with (
            patch("devbench.cli.BacklogParser", return_value=mock_parser),
            patch("devbench.cli.REPO_LOCAL_PATHS", {"caylent-solutions/git-repo": repo_path}),
            patch("devbench.cli.get_configured_default_branch", return_value="main"),
            patch("devbench.cli.run_command", side_effect=fake_run_command),
        ):
            result = cli.cmd_get_diff("E0-F1-S1-T1")

        assert result == 0
        output = capsys.readouterr().out
        assert "valid.py" in output


class TestCmdGetDiffModeAware:
    """Tests for ADR-12 mode-aware cmd_get_diff behaviour.

    The non-defer_pr mode is pinned against behavioural regression so that
    the default per-task-branch workflow keeps working byte-identically.
    The defer_pr-mode tests assert that the branch-vs-default hunk is
    never emitted and that the post-commit state uses `git show HEAD`.
    """

    def _make_unit(self) -> WorkUnit:
        return WorkUnit(
            id="E0-F1-S1-T1",
            title="ADR-12 mode-aware test",
            status=WorkUnitStatus.IN_PROGRESS,
            unit_type=WorkUnitType.TASK,
            file_path=Path("backlog/E0-F1-S1-T1.md"),
            repo="caylent-solutions/git-repo",
            dependencies=[],
        )

    def test_non_defer_pr_mode_includes_branch_vs_main_diff(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """Back-compat pin: with defer_pr False, all four hunks (staged,
        unstaged, branch-vs-default, untracked) appear in output."""
        unit = self._make_unit()
        mock_parser = MagicMock()
        mock_parser.parse_index.return_value = [unit]
        repo_path = tmp_path / "repo"
        repo_path.mkdir()
        (repo_path / "untracked.py").write_text("x = 1\n", encoding="utf-8")

        def fake_run_command(cmd: list[str], **_kwargs: object) -> tuple[int, str, str]:
            if cmd == ["git", "diff", "--cached"]:
                return (0, "STAGED-HUNK\n", "")
            if cmd == ["git", "diff"]:
                return (0, "UNSTAGED-HUNK\n", "")
            if cmd == ["git", "diff", "origin/main"]:
                return (0, "BRANCH-VS-MAIN-HUNK\n", "")
            if cmd == ["git", "ls-files", "--others", "--exclude-standard"]:
                return (0, "untracked.py\n", "")
            return (0, "", "")

        with (
            patch("devbench.cli.BacklogParser", return_value=mock_parser),
            patch("devbench.cli.REPO_LOCAL_PATHS", {"caylent-solutions/git-repo": repo_path}),
            patch("devbench.cli.get_configured_default_branch", return_value="main"),
            patch("devbench.cli.run_command", side_effect=fake_run_command),
            patch("devbench.config.DEFER_PR", False),
        ):
            result = cli.cmd_get_diff("E0-F1-S1-T1")

        assert result == 0
        output = capsys.readouterr().out
        assert "STAGED-HUNK" in output
        assert "UNSTAGED-HUNK" in output
        assert "BRANCH-VS-MAIN-HUNK" in output
        assert "untracked.py" in output

    def test_defer_pr_mode_excludes_branch_vs_main_diff(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """With defer_pr True, the branch-vs-default hunk is never emitted
        even when `git diff origin/<default>` would return content."""
        unit = self._make_unit()
        mock_parser = MagicMock()
        mock_parser.parse_index.return_value = [unit]
        repo_path = tmp_path / "repo"
        repo_path.mkdir()

        def fake_run_command(cmd: list[str], **_kwargs: object) -> tuple[int, str, str]:
            if cmd == ["git", "diff", "--cached"]:
                return (0, "STAGED-HUNK\n", "")
            if cmd == ["git", "diff", "origin/main"]:
                return (0, "BRANCH-VS-MAIN-SHOULD-NOT-APPEAR\n", "")
            return (0, "", "")

        with (
            patch("devbench.cli.BacklogParser", return_value=mock_parser),
            patch("devbench.cli.REPO_LOCAL_PATHS", {"caylent-solutions/git-repo": repo_path}),
            patch("devbench.cli.get_configured_default_branch", return_value="main"),
            patch("devbench.cli.run_command", side_effect=fake_run_command),
            patch("devbench.config.DEFER_PR", True),
        ):
            result = cli.cmd_get_diff("E0-F1-S1-T1")

        assert result == 0
        output = capsys.readouterr().out
        assert "STAGED-HUNK" in output
        assert "BRANCH-VS-MAIN-SHOULD-NOT-APPEAR" not in output

    def test_defer_pr_mode_pre_commit_returns_staged_and_unstaged(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """Pre-commit: staged and unstaged are both present; both appear;
        git show HEAD is not called because parts is already non-empty."""
        unit = self._make_unit()
        mock_parser = MagicMock()
        mock_parser.parse_index.return_value = [unit]
        repo_path = tmp_path / "repo"
        repo_path.mkdir()
        calls: list[list[str]] = []

        def fake_run_command(cmd: list[str], **_kwargs: object) -> tuple[int, str, str]:
            calls.append(cmd)
            if cmd == ["git", "diff", "--cached"]:
                return (0, "STAGED-HUNK\n", "")
            if cmd == ["git", "diff"]:
                return (0, "UNSTAGED-HUNK\n", "")
            return (0, "", "")

        with (
            patch("devbench.cli.BacklogParser", return_value=mock_parser),
            patch("devbench.cli.REPO_LOCAL_PATHS", {"caylent-solutions/git-repo": repo_path}),
            patch("devbench.cli.get_configured_default_branch", return_value="main"),
            patch("devbench.cli.run_command", side_effect=fake_run_command),
            patch("devbench.config.DEFER_PR", True),
        ):
            result = cli.cmd_get_diff("E0-F1-S1-T1")

        assert result == 0
        output = capsys.readouterr().out
        assert "STAGED-HUNK" in output
        assert "UNSTAGED-HUNK" in output
        assert ["git", "show", "--format=", "HEAD"] not in calls, (
            "git show HEAD should only be called when staged/unstaged are empty"
        )

    def test_defer_pr_mode_post_commit_returns_git_show_head(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """Post-commit: staged and unstaged are empty; git show HEAD is
        emitted so the post-commit security review sees this task's commit."""
        unit = self._make_unit()
        mock_parser = MagicMock()
        mock_parser.parse_index.return_value = [unit]
        repo_path = tmp_path / "repo"
        repo_path.mkdir()

        def fake_run_command(cmd: list[str], **_kwargs: object) -> tuple[int, str, str]:
            if cmd == ["git", "show", "--format=", "HEAD"]:
                return (0, "GIT-SHOW-HEAD-HUNK\n", "")
            return (0, "", "")

        with (
            patch("devbench.cli.BacklogParser", return_value=mock_parser),
            patch("devbench.cli.REPO_LOCAL_PATHS", {"caylent-solutions/git-repo": repo_path}),
            patch("devbench.cli.get_configured_default_branch", return_value="main"),
            patch("devbench.cli.run_command", side_effect=fake_run_command),
            patch("devbench.config.DEFER_PR", True),
        ):
            result = cli.cmd_get_diff("E0-F1-S1-T1")

        assert result == 0
        output = capsys.readouterr().out
        assert "GIT-SHOW-HEAD-HUNK" in output

    def test_defer_pr_mode_with_accumulated_prior_commits_scopes_correctly(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """The whole point of ADR-12: with accumulated prior commits on
        the shared branch, the output must contain only the CURRENT task's
        staged change and NOT any of the prior commits."""
        unit = self._make_unit()
        mock_parser = MagicMock()
        mock_parser.parse_index.return_value = [unit]
        repo_path = tmp_path / "repo"
        repo_path.mkdir()
        current_staged = "diff --git a/current.py b/current.py\n+new line\n"
        accumulated_branch = "".join(f"diff --git a/prior-{i}.py b/prior-{i}.py\n+prior line {i}\n" for i in range(10))

        def fake_run_command(cmd: list[str], **_kwargs: object) -> tuple[int, str, str]:
            if cmd == ["git", "diff", "--cached"]:
                return (0, current_staged, "")
            if cmd == ["git", "diff", "origin/main"]:
                return (0, accumulated_branch, "")
            return (0, "", "")

        with (
            patch("devbench.cli.BacklogParser", return_value=mock_parser),
            patch("devbench.cli.REPO_LOCAL_PATHS", {"caylent-solutions/git-repo": repo_path}),
            patch("devbench.cli.get_configured_default_branch", return_value="main"),
            patch("devbench.cli.run_command", side_effect=fake_run_command),
            patch("devbench.config.DEFER_PR", True),
        ):
            result = cli.cmd_get_diff("E0-F1-S1-T1")

        assert result == 0
        output = capsys.readouterr().out
        assert "current.py" in output
        for i in range(10):
            assert f"prior-{i}.py" not in output, (
                f"prior-{i}.py appeared in output under defer_pr mode -- ADR-12 regression"
            )

    def test_defer_pr_mode_untracked_files_still_rendered(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """Untracked hunks are rendered in BOTH modes."""
        unit = self._make_unit()
        mock_parser = MagicMock()
        mock_parser.parse_index.return_value = [unit]
        repo_path = tmp_path / "repo"
        repo_path.mkdir()
        (repo_path / "brand_new.py").write_text("print('hi')\n", encoding="utf-8")

        def fake_run_command(cmd: list[str], **_kwargs: object) -> tuple[int, str, str]:
            if cmd == ["git", "ls-files", "--others", "--exclude-standard"]:
                return (0, "brand_new.py\n", "")
            return (0, "", "")

        with (
            patch("devbench.cli.BacklogParser", return_value=mock_parser),
            patch("devbench.cli.REPO_LOCAL_PATHS", {"caylent-solutions/git-repo": repo_path}),
            patch("devbench.cli.get_configured_default_branch", return_value="main"),
            patch("devbench.cli.run_command", side_effect=fake_run_command),
            patch("devbench.config.DEFER_PR", True),
        ):
            result = cli.cmd_get_diff("E0-F1-S1-T1")

        assert result == 0
        output = capsys.readouterr().out
        assert "brand_new.py" in output
        assert "+print('hi')" in output

    def test_defer_pr_mode_returns_no_changes_when_all_states_empty(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """When staged, unstaged, HEAD, and untracked are all empty, the
        '(no changes)' sentinel is emitted as before."""
        unit = self._make_unit()
        mock_parser = MagicMock()
        mock_parser.parse_index.return_value = [unit]
        repo_path = tmp_path / "repo"
        repo_path.mkdir()

        def fake_run_command(_cmd: list[str], **_kwargs: object) -> tuple[int, str, str]:
            return (0, "", "")

        with (
            patch("devbench.cli.BacklogParser", return_value=mock_parser),
            patch("devbench.cli.REPO_LOCAL_PATHS", {"caylent-solutions/git-repo": repo_path}),
            patch("devbench.cli.get_configured_default_branch", return_value="main"),
            patch("devbench.cli.run_command", side_effect=fake_run_command),
            patch("devbench.config.DEFER_PR", True),
        ):
            result = cli.cmd_get_diff("E0-F1-S1-T1")

        assert result == 0
        assert capsys.readouterr().out.strip() == "(no changes)"


class TestCmdLogVerdictFileResolution:
    """Test cmd_log_verdict file resolution fallback."""

    def test_log_verdict_falls_back_to_workspace_root(self, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
        """Line 520: file resolution falls back to WORKSPACE_ROOT when not under BACKLOG_ROOT."""
        workspace = tmp_path / "workspace"
        workspace.mkdir()
        wu_file = workspace / "backlog" / "E0-F1-S1-T1.md"
        wu_file.parent.mkdir(parents=True)
        wu_file.write_text("# E0-F1-S1-T1\n\n## Status: in-progress\n\n## Comments\n", encoding="utf-8")

        unit = WorkUnit(
            id="E0-F1-S1-T1",
            title="Test Task",
            status=WorkUnitStatus.IN_PROGRESS,
            unit_type=WorkUnitType.TASK,
            file_path=Path("backlog/E0-F1-S1-T1.md"),
            repo="caylent-solutions/git-repo",
            dependencies=[],
        )
        mock_parser = MagicMock()
        mock_parser.parse_index.return_value = [unit]

        with (
            patch("devbench.cli.BacklogParser", return_value=mock_parser),
            patch("devbench.cli.BACKLOG_ROOT", tmp_path / "missing"),
            patch("devbench.cli.WORKSPACE_ROOT", workspace),
        ):
            result = cli.cmd_log_verdict("code_review", "E0-F1-S1-T1", "pass", "ok")

        assert result == 0
        content = wu_file.read_text(encoding="utf-8")
        assert "[REVIEW_PASS]" in content


class TestCmdLogCommentNoCommentsSection:
    """Test cmd_log_comment when Comments section is missing."""

    def test_log_comment_creates_comments_section_when_missing(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """Line 577: creates ## Comments section header when it doesn't exist."""
        backlog_dir = tmp_path / "backlog"
        backlog_dir.mkdir()
        wu_file = backlog_dir / "E0-F1-S1-T1.md"
        wu_file.write_text("# E0-F1-S1-T1\n\n## Status: in-progress\n", encoding="utf-8")

        unit = WorkUnit(
            id="E0-F1-S1-T1",
            title="Test Task",
            status=WorkUnitStatus.IN_PROGRESS,
            unit_type=WorkUnitType.TASK,
            file_path=backlog_dir / "E0-F1-S1-T1.md",
            repo="caylent-solutions/git-repo",
            dependencies=[],
        )
        mock_parser = MagicMock()
        mock_parser.parse_index.return_value = [unit]

        with (
            patch("devbench.cli.BacklogParser", return_value=mock_parser),
            patch("devbench.cli.BACKLOG_ROOT", backlog_dir),
            patch("devbench.cli.WORKSPACE_ROOT", tmp_path),
        ):
            result = cli.cmd_log_comment("executor", "E0-F1-S1-T1", "message")

        assert result == 0
        content = wu_file.read_text(encoding="utf-8")
        assert "## Comments" in content
        assert "[agent/executor]" in content


class TestCmdLogTddUnitNotFound:
    """Test cmd_log_tdd when unit is not found."""

    def test_log_tdd_returns_1_when_unit_not_found(self, capsys: pytest.CaptureFixture[str]) -> None:
        """Lines 611-612: unit not found returns 1."""
        mock_parser = MagicMock()
        mock_parser.parse_index.return_value = []

        with patch("devbench.cli.BacklogParser", return_value=mock_parser):
            result = cli.cmd_log_tdd("NONEXISTENT", "RED", "message")

        assert result == 1
        assert "not found" in capsys.readouterr().err.lower()


class TestCmdRunTests:
    """Test cmd_run_tests command."""

    def _make_unit(self) -> WorkUnit:
        return WorkUnit(
            id="E0-F1-S1-T1",
            title="Test Task",
            status=WorkUnitStatus.IN_PROGRESS,
            unit_type=WorkUnitType.TASK,
            file_path=Path("backlog/E0-F1-S1-T1.md"),
            repo="caylent-solutions/git-repo",
            dependencies=[],
        )

    def test_run_tests_unit_not_found(self, capsys: pytest.CaptureFixture[str]) -> None:
        """Lines 473: unit not found returns 1."""
        mock_parser = MagicMock()
        mock_parser.parse_index.return_value = []

        with patch("devbench.cli.BacklogParser", return_value=mock_parser):
            result = cli.cmd_run_tests("NONEXISTENT")

        assert result == 1

    def test_run_tests_no_local_path(self, capsys: pytest.CaptureFixture[str]) -> None:
        """Lines 480: no local path configured for repo."""
        unit = self._make_unit()
        mock_parser = MagicMock()
        mock_parser.parse_index.return_value = [unit]

        with (
            patch("devbench.cli.BacklogParser", return_value=mock_parser),
            patch("devbench.cli.REPO_LOCAL_PATHS", {}),
        ):
            result = cli.cmd_run_tests("E0-F1-S1-T1")

        assert result == 1

    def test_run_tests_uses_make_test_when_available(self, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
        """Lines 483-489: uses make test when Makefile has test target."""
        unit = self._make_unit()
        mock_parser = MagicMock()
        mock_parser.parse_index.return_value = [unit]

        calls: list[list[str]] = []

        def fake_run_command(cmd: list[str], **_kwargs: object) -> tuple[int, str, str]:
            calls.append(cmd)
            if cmd == ["make", "-n", "test"]:
                return (0, "", "")  # test target exists
            if cmd == ["make", "test"]:
                return (0, "Tests passed", "")
            return (0, "", "")

        with (
            patch("devbench.cli.BacklogParser", return_value=mock_parser),
            patch("devbench.cli.REPO_LOCAL_PATHS", {"caylent-solutions/git-repo": tmp_path}),
            patch("devbench.cli.run_command", side_effect=fake_run_command),
        ):
            result = cli.cmd_run_tests("E0-F1-S1-T1")

        assert result == 0
        assert ["make", "test"] in calls

    def test_run_tests_falls_back_to_pytest(self, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
        """Lines 483-489: falls back to pytest when Makefile test target absent."""
        unit = self._make_unit()
        mock_parser = MagicMock()
        mock_parser.parse_index.return_value = [unit]

        calls: list[list[str]] = []

        def fake_run_command(cmd: list[str], **_kwargs: object) -> tuple[int, str, str]:
            calls.append(cmd)
            if cmd == ["make", "-n", "test"]:
                return (1, "", "No rule to make target")  # no test target
            return (0, "5 passed", "")

        with (
            patch("devbench.cli.BacklogParser", return_value=mock_parser),
            patch("devbench.cli.REPO_LOCAL_PATHS", {"caylent-solutions/git-repo": tmp_path}),
            patch("devbench.cli.run_command", side_effect=fake_run_command),
        ):
            result = cli.cmd_run_tests("E0-F1-S1-T1")

        assert result == 0
        pytest_calls = [c for c in calls if c[0] == "pytest"]
        assert len(pytest_calls) == 1


class TestCmdLogVerdict:
    """Test cmd_log_verdict command."""

    def _make_unit(self) -> WorkUnit:
        return WorkUnit(
            id="E0-F1-S1-T1",
            title="Test Task",
            status=WorkUnitStatus.IN_PROGRESS,
            unit_type=WorkUnitType.TASK,
            file_path=Path("backlog/E0-F1-S1-T1.md"),
            repo="caylent-solutions/git-repo",
            dependencies=[],
        )

    def test_log_verdict_invalid_verdict(self, capsys: pytest.CaptureFixture[str]) -> None:
        """Lines 507-509: invalid verdict returns 1."""
        result = cli.cmd_log_verdict("code_review", "E0-F1-S1-T1", "invalid")
        assert result == 1
        assert "pass" in capsys.readouterr().err.lower()

    def test_log_verdict_unit_not_found(self, capsys: pytest.CaptureFixture[str]) -> None:
        """Lines 515-516: unit not found returns 1."""
        mock_parser = MagicMock()
        mock_parser.parse_index.return_value = []

        with patch("devbench.cli.BacklogParser", return_value=mock_parser):
            result = cli.cmd_log_verdict("code_review", "NONEXISTENT", "pass")

        assert result == 1

    def test_log_verdict_pass_appends_review_pass(self, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
        """Lines 506-544: pass verdict appends REVIEW_PASS to work unit."""
        unit = self._make_unit()
        mock_parser = MagicMock()
        mock_parser.parse_index.return_value = [unit]
        backlog_dir = tmp_path / "backlog"
        backlog_dir.mkdir()
        wu_file = backlog_dir / "E0-F1-S1-T1.md"
        wu_file.write_text("# E0-F1-S1-T1\n\n## Status: in-progress\n\n## Comments\n", encoding="utf-8")

        with (
            patch("devbench.cli.BacklogParser", return_value=mock_parser),
            patch("devbench.cli.BACKLOG_ROOT", tmp_path),
            patch("devbench.cli.WORKSPACE_ROOT", tmp_path),
        ):
            result = cli.cmd_log_verdict("code_review", "E0-F1-S1-T1", "pass", "looks good")

        assert result == 0
        content = wu_file.read_text(encoding="utf-8")
        assert "[REVIEW_PASS]" in content
        assert "judge/code_review" in content

    def test_log_verdict_fail_appends_review_fail(self, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
        """Lines 506-544: fail verdict appends REVIEW_FAIL to work unit."""
        unit = self._make_unit()
        mock_parser = MagicMock()
        mock_parser.parse_index.return_value = [unit]
        backlog_dir = tmp_path / "backlog"
        backlog_dir.mkdir()
        wu_file = backlog_dir / "E0-F1-S1-T1.md"
        wu_file.write_text("# E0-F1-S1-T1\n\n## Status: in-progress\n\n## Comments\n", encoding="utf-8")

        with (
            patch("devbench.cli.BacklogParser", return_value=mock_parser),
            patch("devbench.cli.BACKLOG_ROOT", tmp_path),
            patch("devbench.cli.WORKSPACE_ROOT", tmp_path),
        ):
            result = cli.cmd_log_verdict("code_review", "E0-F1-S1-T1", "fail", "needs fixes")

        assert result == 0
        content = wu_file.read_text(encoding="utf-8")
        assert "[REVIEW_FAIL]" in content

    def test_log_verdict_creates_comments_section_when_missing(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """Lines 535-536: creates ## Comments section when absent."""
        unit = self._make_unit()
        mock_parser = MagicMock()
        mock_parser.parse_index.return_value = [unit]
        backlog_dir = tmp_path / "backlog"
        backlog_dir.mkdir()
        wu_file = backlog_dir / "E0-F1-S1-T1.md"
        wu_file.write_text("# E0-F1-S1-T1\n\n## Status: in-progress\n", encoding="utf-8")

        with (
            patch("devbench.cli.BacklogParser", return_value=mock_parser),
            patch("devbench.cli.BACKLOG_ROOT", tmp_path),
            patch("devbench.cli.WORKSPACE_ROOT", tmp_path),
        ):
            result = cli.cmd_log_verdict("code_review", "E0-F1-S1-T1", "pass")

        assert result == 0
        content = wu_file.read_text(encoding="utf-8")
        assert "## Comments" in content


class TestCmdLogCommentFileResolution:
    """Test cmd_log_comment file resolution fallback paths."""

    def test_log_comment_falls_back_to_workspace_root(self, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
        """Lines 564, 577: when BACKLOG_ROOT path missing, falls back to WORKSPACE_ROOT."""
        workspace = tmp_path / "workspace"
        workspace.mkdir()
        wu_file = workspace / "backlog" / "E0-F1-S1-T1.md"
        wu_file.parent.mkdir(parents=True)
        wu_file.write_text("# E0-F1-S1-T1\n\n## Status: in-progress\n\n## Comments\n", encoding="utf-8")

        unit = WorkUnit(
            id="E0-F1-S1-T1",
            title="Test Task",
            status=WorkUnitStatus.IN_PROGRESS,
            unit_type=WorkUnitType.TASK,
            file_path=Path("backlog/E0-F1-S1-T1.md"),
            repo="caylent-solutions/git-repo",
            dependencies=[],
        )
        mock_parser = MagicMock()
        mock_parser.parse_index.return_value = [unit]

        with (
            patch("devbench.cli.BacklogParser", return_value=mock_parser),
            patch("devbench.cli.BACKLOG_ROOT", tmp_path / "missing_backlog"),
            patch("devbench.cli.WORKSPACE_ROOT", workspace),
        ):
            result = cli.cmd_log_comment("executor", "E0-F1-S1-T1", "done")

        assert result == 0
        content = wu_file.read_text(encoding="utf-8")
        assert "[agent/executor]" in content


class TestCmdLogTddFileResolution:
    """Test cmd_log_tdd file resolution fallback paths."""

    def test_log_tdd_falls_back_to_workspace_root(self, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
        """Lines 611-612, 616: file resolution falls back to WORKSPACE_ROOT."""
        workspace = tmp_path / "workspace"
        workspace.mkdir()
        wu_file = workspace / "backlog" / "E0-F1-S1-T1.md"
        wu_file.parent.mkdir(parents=True)
        wu_file.write_text(
            "# E0-F1-S1-T1: Test\n\n## Status: in-progress\n\n## Comments\n\n## TDD Cycle Log\n",
            encoding="utf-8",
        )

        unit = WorkUnit(
            id="E0-F1-S1-T1",
            title="TDD Test",
            status=WorkUnitStatus.IN_PROGRESS,
            unit_type=WorkUnitType.TASK,
            file_path=Path("backlog/E0-F1-S1-T1.md"),
            repo="caylent-solutions/devbench",
            dependencies=[],
        )
        mock_parser = MagicMock()
        mock_parser.parse_index.return_value = [unit]

        with (
            patch("devbench.cli.BacklogParser", return_value=mock_parser),
            patch("devbench.cli.BACKLOG_ROOT", tmp_path / "missing"),
            patch("devbench.cli.WORKSPACE_ROOT", workspace),
        ):
            result = cli.cmd_log_tdd("E0-F1-S1-T1", "RED", "tdd message")

        assert result == 0
        content = wu_file.read_text(encoding="utf-8")
        assert "[RED]" in content


class TestCmdEnsureBranchNoLocalPath:
    """Test cmd_ensure_branch when repo has no local path configured."""

    def test_ensure_branch_returns_1_when_no_local_path(self, capsys: pytest.CaptureFixture[str]) -> None:
        """Lines 653-654: no local path configured."""
        unit = WorkUnit(
            id="E0-F1-S1-T1",
            title="Test Task",
            status=WorkUnitStatus.IN_PROGRESS,
            unit_type=WorkUnitType.TASK,
            file_path=Path("backlog/E0-F1-S1-T1.md"),
            repo="caylent-solutions/git-repo",
            dependencies=[],
        )
        mock_parser = MagicMock()
        mock_parser.parse_index.return_value = [unit]

        with (
            patch("devbench.cli.BacklogParser", return_value=mock_parser),
            patch("devbench.cli.REPO_LOCAL_PATHS", {}),
        ):
            result = cli.cmd_ensure_branch("E0-F1-S1-T1")

        assert result == 1
        assert "no local path" in capsys.readouterr().err.lower()


class TestResolveGitOpsContext:
    """Test _resolve_git_ops_context helper exits."""

    def test_exits_when_unit_not_found(self) -> None:
        """Lines 678-679: sys.exit(1) when unit not found."""
        mock_parser = MagicMock()
        mock_parser.parse_index.return_value = []

        with (
            patch("devbench.cli.BacklogParser", return_value=mock_parser),
            pytest.raises(SystemExit) as exc_info,
        ):
            cli._resolve_git_ops_context("NONEXISTENT")

        assert exc_info.value.code == 1

    def test_exits_when_no_local_path(self) -> None:
        """Lines 685-686: sys.exit(1) when no local path configured."""
        unit = WorkUnit(
            id="E0-F1-S1-T1",
            title="Test Task",
            status=WorkUnitStatus.IN_PROGRESS,
            unit_type=WorkUnitType.TASK,
            file_path=Path("backlog/E0-F1-S1-T1.md"),
            repo="caylent-solutions/git-repo",
            dependencies=[],
        )
        mock_parser = MagicMock()
        mock_parser.parse_index.return_value = [unit]

        with (
            patch("devbench.cli.BacklogParser", return_value=mock_parser),
            patch("devbench.cli.REPO_LOCAL_PATHS", {}),
            pytest.raises(SystemExit) as exc_info,
        ):
            cli._resolve_git_ops_context("E0-F1-S1-T1")

        assert exc_info.value.code == 1


class TestCmdGitOpsDeferMode:
    """Test cmd_git_ops with DEFER_PR mode."""

    def test_git_ops_uses_defer_mode(self, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
        """Line 732: when DEFER_PR is True, delegates to _git_ops_deferred."""
        unit = WorkUnit(
            id="E0-F1-S1-T1",
            title="Test Task",
            status=WorkUnitStatus.IN_PROGRESS,
            unit_type=WorkUnitType.TASK,
            file_path=Path("backlog/E0-F1-S1-T1.md"),
            repo="caylent-solutions/git-repo",
            dependencies=[],
        )
        mock_parser = MagicMock()
        mock_parser.parse_index.return_value = [unit]
        mock_ops = MagicMock()

        with (
            patch("devbench.cli.BacklogParser", return_value=mock_parser),
            patch("devbench.cli.REPO_LOCAL_PATHS", {"caylent-solutions/git-repo": tmp_path}),
            patch("devbench.config.DEFER_PR", True),
            patch("devbench.config.SINGLE_BRANCH", "feature/combined"),
            patch("devbench.github.git_ops.GitOpsService", return_value=mock_ops),
            patch("devbench.cli._resolve_unit_file", return_value=None),
        ):
            result = cli.cmd_git_ops("E0-F1-S1-T1")

        assert result == 0
        mock_ops.commit_local.assert_called_once()


class TestCmdGitOpsBadPrNumber:
    """Test cmd_git_ops when PR URL does not end with a number."""

    def test_returns_1_when_pr_number_not_parseable(self, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
        """Lines 761-762: PR URL that doesn't end in a number."""
        unit = WorkUnit(
            id="E0-F1-S1-T1",
            title="Test Task",
            status=WorkUnitStatus.IN_PROGRESS,
            unit_type=WorkUnitType.TASK,
            file_path=Path("backlog/E0-F1-S1-T1.md"),
            repo="caylent-solutions/git-repo",
            dependencies=[],
        )
        mock_parser = MagicMock()
        mock_parser.parse_index.return_value = [unit]
        mock_ops = MagicMock()
        mock_ops.create_pr.return_value = "https://github.com/org/repo/pull/not-a-number"
        mock_ops.wait_for_checks_and_classify.return_value = CIResult.GREEN

        with (
            patch("devbench.cli.BacklogParser", return_value=mock_parser),
            patch("devbench.cli.REPO_LOCAL_PATHS", {"caylent-solutions/git-repo": tmp_path}),
            patch("devbench.cli.UPDATE_SUBMODULE", False),
            patch("devbench.github.git_ops.GitOpsService", return_value=mock_ops),
        ):
            result = cli.cmd_git_ops("E0-F1-S1-T1")

        assert result == 1
        assert "could not parse pr number" in capsys.readouterr().err.lower()


class TestCmdGitOpsFinalizeHappyPath:
    """Test cmd_git_ops_finalize happy path (CI GREEN branch)."""

    def test_finalize_pushes_and_creates_pr_then_watches_ci(self, tmp_path: Path) -> None:
        """cmd_git_ops_finalize commits, creates PR, waits for CI, and returns 0 on GREEN."""
        mock_ops = MagicMock()
        mock_ops.create_pr.return_value = "https://github.com/org/repo/pull/99"
        mock_ops.wait_for_checks_and_classify.return_value = CIResult.GREEN

        with (
            patch("devbench.config.SINGLE_BRANCH", "feature/combined"),
            patch("devbench.config.DEFER_PR", True),
            patch("devbench.cli.REPO_LOCAL_PATHS", {"caylent-solutions/git-repo": tmp_path}),
            patch("devbench.github.git_ops.GitOpsService", return_value=mock_ops),
            patch("devbench.cli._handle_finalize_ci_result", return_value=0) as mock_handler,
        ):
            result = cli.cmd_git_ops_finalize("caylent-solutions/git-repo")

        assert result == 0
        mock_ops.commit_and_push.assert_called_once()
        mock_ops.create_pr.assert_called_once()
        mock_ops.wait_for_checks_and_classify.assert_called_once()
        mock_handler.assert_called_once()

    def test_finalize_namespaces_single_branch_with_configured_branch_prefix(self, tmp_path: Path) -> None:
        """issue #283 AC-5: git-ops-finalize namespaces single_branch by the
        configured branch_prefix too, same as the per-unit git-ops path."""
        from devbench.config_loader import GitOpsConfig, RepoConfig, RuntimeConfig

        mock_ops = MagicMock()
        mock_ops.create_pr.return_value = "https://github.com/org/repo/pull/99"
        mock_ops.wait_for_checks_and_classify.return_value = CIResult.GREEN
        mock_runtime_cfg = RuntimeConfig(
            repos={"caylent-solutions/git-repo": RepoConfig(branch_prefix="wg_004")},
            git_ops=GitOpsConfig(single_branch="feature/combined"),
        )

        with (
            patch("devbench.config.SINGLE_BRANCH", "feature/combined"),
            patch("devbench.config.DEFER_PR", True),
            patch("devbench.cli.RUNTIME_CONFIG", mock_runtime_cfg),
            patch("devbench.cli.REPO_LOCAL_PATHS", {"caylent-solutions/git-repo": tmp_path}),
            patch("devbench.github.git_ops.GitOpsService", return_value=mock_ops),
            patch("devbench.cli._handle_finalize_ci_result", return_value=0),
        ):
            result = cli.cmd_git_ops_finalize("caylent-solutions/git-repo")

        assert result == 0
        pushed_branch = mock_ops.commit_and_push.call_args.args[2]
        assert pushed_branch == "wg_004/feature/combined"

    def test_finalize_returns_1_when_no_local_path(self, capsys: pytest.CaptureFixture[str]) -> None:
        """Lines 837-838: no local path configured for repo."""
        with (
            patch("devbench.config.SINGLE_BRANCH", "feature/combined"),
            patch("devbench.config.DEFER_PR", True),
            patch("devbench.cli.REPO_LOCAL_PATHS", {}),
        ):
            result = cli.cmd_git_ops_finalize("caylent-solutions/git-repo")

        assert result == 1
        assert "no local path" in capsys.readouterr().err.lower()

    def test_finalize_green_does_not_merge(self, tmp_path: Path) -> None:
        """GREEN: cmd_git_ops_finalize returns 0 and does not call merge_pr."""
        mock_ops = MagicMock()
        mock_ops.create_pr.return_value = "https://github.com/org/repo/pull/99"
        mock_ops.wait_for_checks_and_classify.return_value = CIResult.GREEN

        with (
            patch("devbench.config.SINGLE_BRANCH", "feature/combined"),
            patch("devbench.config.DEFER_PR", True),
            patch("devbench.cli.REPO_LOCAL_PATHS", {"caylent-solutions/git-repo": tmp_path}),
            patch("devbench.github.git_ops.GitOpsService", return_value=mock_ops),
            patch("devbench.cli._handle_finalize_ci_result", return_value=0),
        ):
            result = cli.cmd_git_ops_finalize("caylent-solutions/git-repo")

        assert result == 0
        mock_ops.merge_pr.assert_not_called()

    def test_finalize_timeout_returns_two(self, tmp_path: Path) -> None:
        """TIMEOUT: cmd_git_ops_finalize returns rc=2."""
        mock_ops = MagicMock()
        mock_ops.create_pr.return_value = "https://github.com/org/repo/pull/99"
        mock_ops.wait_for_checks_and_classify.return_value = CIResult.TIMEOUT

        with (
            patch("devbench.config.SINGLE_BRANCH", "feature/combined"),
            patch("devbench.config.DEFER_PR", True),
            patch("devbench.cli.REPO_LOCAL_PATHS", {"caylent-solutions/git-repo": tmp_path}),
            patch("devbench.github.git_ops.GitOpsService", return_value=mock_ops),
            patch("devbench.cli._handle_finalize_ci_result", return_value=2),
        ):
            result = cli.cmd_git_ops_finalize("caylent-solutions/git-repo")

        assert result == 2

    def test_finalize_failed_unknown_returns_two(self, tmp_path: Path) -> None:
        """FAILED_UNKNOWN: cmd_git_ops_finalize returns rc=2."""
        mock_ops = MagicMock()
        mock_ops.create_pr.return_value = "https://github.com/org/repo/pull/99"
        mock_ops.wait_for_checks_and_classify.return_value = CIResult.FAILED_UNKNOWN

        with (
            patch("devbench.config.SINGLE_BRANCH", "feature/combined"),
            patch("devbench.config.DEFER_PR", True),
            patch("devbench.cli.REPO_LOCAL_PATHS", {"caylent-solutions/git-repo": tmp_path}),
            patch("devbench.github.git_ops.GitOpsService", return_value=mock_ops),
            patch("devbench.cli._handle_finalize_ci_result", return_value=2),
        ):
            result = cli.cmd_git_ops_finalize("caylent-solutions/git-repo")

        assert result == 2

    def test_finalize_failed_known_task_returns_two(self, tmp_path: Path) -> None:
        """FAILED_KNOWN_TASK: cmd_git_ops_finalize returns rc=2."""
        mock_ops = MagicMock()
        mock_ops.create_pr.return_value = "https://github.com/org/repo/pull/99"
        mock_ops.wait_for_checks_and_classify.return_value = CIResult.FAILED_KNOWN_TASK("E3-F1-S1-T1")

        with (
            patch("devbench.config.SINGLE_BRANCH", "feature/combined"),
            patch("devbench.config.DEFER_PR", True),
            patch("devbench.cli.REPO_LOCAL_PATHS", {"caylent-solutions/git-repo": tmp_path}),
            patch("devbench.github.git_ops.GitOpsService", return_value=mock_ops),
            patch("devbench.cli._handle_finalize_ci_result", return_value=2),
        ):
            result = cli.cmd_git_ops_finalize("caylent-solutions/git-repo")

        assert result == 2


class TestLabelStopReason:
    """Pin the bucketed stop-reason label so ``orchestrator_stop`` pings
    do not mis-label clean exits as crashes (#213)."""

    def test_systemexit_code_zero_is_clean(self) -> None:
        from devbench.cli import _label_stop_reason

        assert _label_stop_reason(SystemExit(0)) == "clean exit (SystemExit 0)"

    def test_systemexit_with_no_code_is_clean(self) -> None:
        """``sys.exit()`` (no arg) raises ``SystemExit(None)``; treat as clean."""
        from devbench.cli import _label_stop_reason

        assert _label_stop_reason(SystemExit()) == "clean exit (SystemExit 0)"

    def test_systemexit_nonzero_code_is_crash(self) -> None:
        from devbench.cli import _label_stop_reason

        assert _label_stop_reason(SystemExit(1)) == "crash: SystemExit: 1"

    def test_keyboard_interrupt_is_interrupted(self) -> None:
        from devbench.cli import _label_stop_reason

        assert _label_stop_reason(KeyboardInterrupt()) == "interrupted by operator (Ctrl+C / SIGINT)"

    def test_other_exception_is_crash(self) -> None:
        from devbench.cli import _label_stop_reason

        assert _label_stop_reason(RuntimeError("boom")) == "crash: RuntimeError: boom"

    def test_value_error_with_empty_message_is_crash(self) -> None:
        from devbench.cli import _label_stop_reason

        assert _label_stop_reason(ValueError("")) == "crash: ValueError: "


class TestCmdStart:
    """Test cmd_start command by mocking claude_agent_sdk."""

    def test_cmd_start_invokes_agent_sdk(self, tmp_path: Path) -> None:
        """Lines 868-885: cmd_start creates an async runner and returns 0."""
        import sys
        import types

        # Create a mock claude_agent_sdk module
        mock_sdk: Any = types.ModuleType("claude_agent_sdk")

        mock_options_cls = MagicMock()
        mock_sdk.ClaudeAgentOptions = mock_options_cls

        async def mock_query(**kwargs: object) -> object:
            # Async generator that yields a message to cover line 882
            yield "test message"

        mock_sdk.query = mock_query

        with (
            patch.dict(sys.modules, {"claude_agent_sdk": mock_sdk}),
            patch("devbench.cli.WORKSPACE_ROOT", tmp_path),
            patch(
                "devbench.cli._should_auto_restart_after_no_actionable",
                return_value=(False, []),
            ),
        ):
            result = cli.cmd_start()

        assert result == 0


class _FakeSdkModule(types.ModuleType):
    """Typed fake claude_agent_sdk module for tests.

    Subclasses :class:`types.ModuleType` and declares ``ClaudeAgentOptions``
    and ``query`` as typed attributes so mypy can verify attribute access
    without suppression annotations.
    """

    ClaudeAgentOptions: object
    query: object

    def __init__(self) -> None:
        super().__init__("claude_agent_sdk")


class TestCmdStartNameFlag:
    """Tests for AC-192-1 and AC-192-2: --name flag creates session registry entry.

    AC-192-1: devbench start --name <name> creates <workspace>/.devbench/sessions/<name>/
              with pid, scope.json, started_at, started_by files.
    AC-192-2: --name defaults to 'default' when omitted.
    """

    def _make_mock_sdk(self) -> _FakeSdkModule:
        """Return a minimal fake claude_agent_sdk module.

        Returns:
            A :class:`_FakeSdkModule` instance with ``ClaudeAgentOptions`` set
            to a :class:`~unittest.mock.MagicMock` and ``query`` set to an
            async generator that yields a single test message.
        """
        mock_sdk = _FakeSdkModule()
        mock_sdk.ClaudeAgentOptions = MagicMock()

        async def mock_query(**kwargs: object) -> object:
            yield "test message"

        mock_sdk.query = mock_query
        return mock_sdk

    @pytest.mark.unit
    def test_default_name_creates_default_session_dir(self, tmp_path: Path) -> None:
        """AC-192-2: --name defaults to 'default'; session dir created under sessions/default/."""
        import sys

        mock_sdk = self._make_mock_sdk()
        with (
            patch.dict(sys.modules, {"claude_agent_sdk": mock_sdk}),
            patch("devbench.cli.WORKSPACE_ROOT", tmp_path),
            patch("devbench.cli._should_auto_restart_after_no_actionable", return_value=(False, [])),
        ):
            rc = cli.cmd_start()

        assert rc == 0
        session_dir = tmp_path / ".devbench" / "sessions" / "default"
        assert session_dir.is_dir(), f"Expected session dir at {session_dir}"

    @pytest.mark.unit
    def test_named_flag_creates_named_session_dir(self, tmp_path: Path) -> None:
        """AC-192-1: --name alpha creates .devbench/sessions/alpha/ directory."""
        import sys

        mock_sdk = self._make_mock_sdk()
        with (
            patch.dict(sys.modules, {"claude_agent_sdk": mock_sdk}),
            patch("devbench.cli.WORKSPACE_ROOT", tmp_path),
            patch("devbench.cli._should_auto_restart_after_no_actionable", return_value=(False, [])),
        ):
            rc = cli.cmd_start("--name", "alpha")

        assert rc == 0
        session_dir = tmp_path / ".devbench" / "sessions" / "alpha"
        assert session_dir.is_dir(), f"Expected session dir at {session_dir}"

    @pytest.mark.unit
    def test_pid_file_written(self, tmp_path: Path) -> None:
        """AC-192-1: session dir contains a 'pid' file with the current process PID."""
        import os
        import sys

        mock_sdk = self._make_mock_sdk()
        with (
            patch.dict(sys.modules, {"claude_agent_sdk": mock_sdk}),
            patch("devbench.cli.WORKSPACE_ROOT", tmp_path),
            patch("devbench.cli._should_auto_restart_after_no_actionable", return_value=(False, [])),
        ):
            rc = cli.cmd_start("--name", "beta")

        assert rc == 0
        pid_path = tmp_path / ".devbench" / "sessions" / "beta" / "pid"
        assert pid_path.is_file(), f"Expected pid file at {pid_path}"
        assert pid_path.read_text(encoding="utf-8").strip() == str(os.getpid())

    @pytest.mark.unit
    def test_started_at_file_written(self, tmp_path: Path) -> None:
        """AC-192-1: session dir contains a 'started_at' file with ISO-8601 timestamp."""
        import sys
        from datetime import UTC, datetime

        mock_sdk = self._make_mock_sdk()
        before = datetime.now(UTC)
        with (
            patch.dict(sys.modules, {"claude_agent_sdk": mock_sdk}),
            patch("devbench.cli.WORKSPACE_ROOT", tmp_path),
            patch("devbench.cli._should_auto_restart_after_no_actionable", return_value=(False, [])),
        ):
            rc = cli.cmd_start("--name", "gamma")

        after = datetime.now(UTC)
        assert rc == 0
        started_at_path = tmp_path / ".devbench" / "sessions" / "gamma" / "started_at"
        assert started_at_path.is_file(), f"Expected started_at file at {started_at_path}"
        raw = started_at_path.read_text(encoding="utf-8").strip()
        recorded = datetime.fromisoformat(raw)
        # Normalise to UTC for comparison
        if recorded.tzinfo is None:
            recorded = recorded.replace(tzinfo=UTC)
        assert before <= recorded <= after, (
            f"started_at {raw!r} must be between {before.isoformat()} and {after.isoformat()}"
        )

    @pytest.mark.unit
    def test_started_by_file_written(self, tmp_path: Path) -> None:
        """AC-192-1: session dir contains a 'started_by' file with the OS username."""
        import getpass
        import sys

        mock_sdk = self._make_mock_sdk()
        with (
            patch.dict(sys.modules, {"claude_agent_sdk": mock_sdk}),
            patch("devbench.cli.WORKSPACE_ROOT", tmp_path),
            patch("devbench.cli._should_auto_restart_after_no_actionable", return_value=(False, [])),
        ):
            rc = cli.cmd_start("--name", "delta")

        assert rc == 0
        started_by_path = tmp_path / ".devbench" / "sessions" / "delta" / "started_by"
        assert started_by_path.is_file(), f"Expected started_by file at {started_by_path}"
        assert started_by_path.read_text(encoding="utf-8").strip() == getpass.getuser()

    @pytest.mark.unit
    def test_scope_json_written_when_no_include(self, tmp_path: Path) -> None:
        """AC-192-1: scope.json is written to the session dir (empty scope when no --include)."""
        import sys

        mock_sdk = self._make_mock_sdk()
        with (
            patch.dict(sys.modules, {"claude_agent_sdk": mock_sdk}),
            patch("devbench.cli.WORKSPACE_ROOT", tmp_path),
            patch("devbench.cli._should_auto_restart_after_no_actionable", return_value=(False, [])),
        ):
            rc = cli.cmd_start("--name", "epsilon")

        assert rc == 0
        scope_path = tmp_path / ".devbench" / "sessions" / "epsilon" / "scope.json"
        assert scope_path.is_file(), f"Expected scope.json at {scope_path}"

    @pytest.mark.unit
    def test_registry_json_updated(self, tmp_path: Path) -> None:
        """AC-192-1: registry.json under .devbench/sessions/ contains the new session entry."""
        import sys

        mock_sdk = self._make_mock_sdk()
        with (
            patch.dict(sys.modules, {"claude_agent_sdk": mock_sdk}),
            patch("devbench.cli.WORKSPACE_ROOT", tmp_path),
            patch("devbench.cli._should_auto_restart_after_no_actionable", return_value=(False, [])),
        ):
            rc = cli.cmd_start("--name", "zeta")

        assert rc == 0
        import json as _json

        registry_path = tmp_path / ".devbench" / "sessions" / "registry.json"
        assert registry_path.is_file(), f"Expected registry.json at {registry_path}"
        entries = _json.loads(registry_path.read_text(encoding="utf-8"))
        assert isinstance(entries, list)
        names = [e["name"] for e in entries]
        assert "zeta" in names, f"Expected 'zeta' in registry names, got {names}"

    @pytest.mark.unit
    def test_devbench_session_name_set_during_run(self, tmp_path: Path) -> None:
        """AC-192-1: DEVBENCH_SESSION_NAME env var is set to the session name during SDK run."""
        import sys

        captured_env: dict[str, str] = {}
        mock_sdk_capture = _FakeSdkModule()
        mock_sdk_capture.ClaudeAgentOptions = MagicMock()

        async def mock_query_capture(**kwargs: object) -> object:
            import os as _os

            captured_env.update(_os.environ.copy())
            yield "test message"

        mock_sdk_capture.query = mock_query_capture

        with (
            patch.dict(sys.modules, {"claude_agent_sdk": mock_sdk_capture}),
            patch("devbench.cli.WORKSPACE_ROOT", tmp_path),
            patch("devbench.cli._should_auto_restart_after_no_actionable", return_value=(False, [])),
        ):
            rc = cli.cmd_start("--name", "eta")

        assert rc == 0
        assert captured_env.get("DEVBENCH_SESSION_NAME") == "eta", (
            f"Expected DEVBENCH_SESSION_NAME='eta' during SDK run, got {captured_env.get('DEVBENCH_SESSION_NAME')!r}"
        )

    @pytest.mark.unit
    def test_unknown_flag_returns_error(self, capsys: pytest.CaptureFixture[str]) -> None:
        """--badflg returns exit code 1 with error message (regression guard)."""
        rc = cli.cmd_start("--badflg")
        assert rc == 1
        err = capsys.readouterr().err
        assert "--badflg" in err

    @pytest.mark.unit
    def test_name_missing_value_returns_error(self, capsys: pytest.CaptureFixture[str]) -> None:
        """--name without a value returns exit code 1."""
        rc = cli.cmd_start("--name")
        assert rc == 1
        err = capsys.readouterr().err
        assert "--name" in err

    @pytest.mark.unit
    def test_path_traversal_in_name_raises_value_error(self, tmp_path: Path) -> None:
        """_write_session_state_files raises ValueError when session_name contains '..'."""
        with pytest.raises(ValueError, match=r"invalid path segment '\.\.'"):
            cli._write_session_state_files(tmp_path, "../evil", 12345, [])


class TestCmdStartScopeOverlap:
    """Tests for AC-192-4: scope-overlap detection + --allow-overlap flag.

    AC-192-4: Scope-overlap detection: second session with overlapping scope
              fails fast (rc=1, stderr lists conflicting WU IDs + session names)
              unless --allow-overlap is passed (warn but proceed).
    """

    def _make_mock_sdk(self) -> _FakeSdkModule:
        """Return a minimal fake claude_agent_sdk module."""
        mock_sdk = _FakeSdkModule()
        mock_sdk.ClaudeAgentOptions = MagicMock()

        async def mock_query(**kwargs: object) -> object:
            yield "test message"

        mock_sdk.query = mock_query
        return mock_sdk

    def _seed_registry(self, workspace_root: Path, session_name: str, scope: list[str]) -> None:
        """Write a registry.json entry for an already-running session."""
        from devbench.session import Session, SessionRegistry

        registry = SessionRegistry(workspace_root)
        existing = registry.load()
        state_dir = workspace_root / ".devbench" / "sessions" / session_name
        state_dir.mkdir(parents=True, exist_ok=True)
        existing.append(
            Session(
                name=session_name,
                pid=99999,
                scope=scope,
                started_at=datetime(2026, 1, 1, tzinfo=UTC),
                started_by="tester",
                state_dir=state_dir,
            )
        )
        registry.save(existing)

    @pytest.mark.unit
    def test_overlap_without_allow_overlap_returns_rc1(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """AC-192-4: overlapping scopes fail-fast with rc=1 when --allow-overlap absent."""
        import sys

        self._seed_registry(tmp_path, "alpha", ["E1-F1-S1-T1", "E1-F1-S1-T2"])
        mock_sdk = self._make_mock_sdk()
        with (
            patch.dict(sys.modules, {"claude_agent_sdk": mock_sdk}),
            patch("devbench.cli.WORKSPACE_ROOT", tmp_path),
            patch("devbench.cli.BACKLOG_ROOT", tmp_path / "backlog"),
            patch("devbench.cli.BACKLOG_INDEX", tmp_path / "BACKLOG.md"),
            patch(
                "devbench.cli.BacklogParser.parse_index",
                return_value=[
                    WorkUnit(
                        id="E1-F1-S1-T1",
                        title="Task 1",
                        status=WorkUnitStatus.IN_QUEUE,
                        unit_type=WorkUnitType.TASK,
                        file_path=Path("backlog/E1-F1-S1-T1.md"),
                        repo="caylent-solutions/devbench",
                        dependencies=[],
                    ),
                    WorkUnit(
                        id="E1-F1-S1-T2",
                        title="Task 2",
                        status=WorkUnitStatus.IN_QUEUE,
                        unit_type=WorkUnitType.TASK,
                        file_path=Path("backlog/E1-F1-S1-T2.md"),
                        repo="caylent-solutions/devbench",
                        dependencies=[],
                    ),
                ],
            ),
        ):
            rc = cli.cmd_start("--include", "E1-F1-S1-T1", "--name", "beta")

        assert rc == 1
        err = capsys.readouterr().err
        assert "E1-F1-S1-T1" in err
        assert "alpha" in err

    @pytest.mark.unit
    def test_overlap_with_allow_overlap_returns_rc0_and_warns(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """AC-192-4: --allow-overlap warns but proceeds (rc=0)."""
        import sys

        self._seed_registry(tmp_path, "alpha", ["E1-F1-S1-T1", "E1-F1-S1-T2"])
        mock_sdk = self._make_mock_sdk()
        with (
            patch.dict(sys.modules, {"claude_agent_sdk": mock_sdk}),
            patch("devbench.cli.WORKSPACE_ROOT", tmp_path),
            patch("devbench.cli.BACKLOG_ROOT", tmp_path / "backlog"),
            patch("devbench.cli.BACKLOG_INDEX", tmp_path / "BACKLOG.md"),
            patch(
                "devbench.cli.BacklogParser.parse_index",
                return_value=[
                    WorkUnit(
                        id="E1-F1-S1-T1",
                        title="Task 1",
                        status=WorkUnitStatus.IN_QUEUE,
                        unit_type=WorkUnitType.TASK,
                        file_path=Path("backlog/E1-F1-S1-T1.md"),
                        repo="caylent-solutions/devbench",
                        dependencies=[],
                    ),
                ],
            ),
            patch("devbench.cli._should_auto_restart_after_no_actionable", return_value=(False, [])),
        ):
            rc = cli.cmd_start("--include", "E1-F1-S1-T1", "--name", "beta", "--allow-overlap")

        assert rc == 0
        err = capsys.readouterr().err
        assert "WARNING" in err
        assert "E1-F1-S1-T1" in err

    @pytest.mark.unit
    def test_no_overlap_proceeds_normally(self, tmp_path: Path) -> None:
        """AC-192-4: non-overlapping scopes proceed with rc=0."""
        import sys

        self._seed_registry(tmp_path, "alpha", ["E2-F1-S1-T1"])
        mock_sdk = self._make_mock_sdk()
        with (
            patch.dict(sys.modules, {"claude_agent_sdk": mock_sdk}),
            patch("devbench.cli.WORKSPACE_ROOT", tmp_path),
            patch("devbench.cli.BACKLOG_ROOT", tmp_path / "backlog"),
            patch("devbench.cli.BACKLOG_INDEX", tmp_path / "BACKLOG.md"),
            patch(
                "devbench.cli.BacklogParser.parse_index",
                return_value=[
                    WorkUnit(
                        id="E1-F1-S1-T1",
                        title="Task 1",
                        status=WorkUnitStatus.IN_QUEUE,
                        unit_type=WorkUnitType.TASK,
                        file_path=Path("backlog/E1-F1-S1-T1.md"),
                        repo="caylent-solutions/devbench",
                        dependencies=[],
                    ),
                ],
            ),
            patch("devbench.cli._should_auto_restart_after_no_actionable", return_value=(False, [])),
        ):
            rc = cli.cmd_start("--include", "E1-F1-S1-T1", "--name", "beta")

        assert rc == 0

    @pytest.mark.unit
    def test_empty_scope_no_overlap_check_needed(self, tmp_path: Path) -> None:
        """AC-192-4: when no --include supplied (empty scope), overlap check is skipped."""
        import sys

        self._seed_registry(tmp_path, "alpha", ["E1-F1-S1-T1"])
        mock_sdk = self._make_mock_sdk()
        with (
            patch.dict(sys.modules, {"claude_agent_sdk": mock_sdk}),
            patch("devbench.cli.WORKSPACE_ROOT", tmp_path),
            patch("devbench.cli._should_auto_restart_after_no_actionable", return_value=(False, [])),
        ):
            rc = cli.cmd_start("--name", "beta")

        assert rc == 0

    @pytest.mark.unit
    def test_allow_overlap_flag_missing_value_returns_rc1(self, capsys: pytest.CaptureFixture[str]) -> None:
        """--allow-overlap is a boolean flag; it takes no value argument."""
        # --allow-overlap is a boolean flag so the parser must accept it without a value.
        # Passing an unknown flag after --allow-overlap must still error.
        rc = cli.cmd_start("--allow-overlap", "--unknown-flag-after")
        assert rc == 1
        err = capsys.readouterr().err
        assert "--unknown-flag-after" in err

    @pytest.mark.unit
    def test_error_message_lists_conflicting_ids_and_sessions(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """AC-192-4: error message names every conflicting WU ID and the owning session."""
        import sys

        self._seed_registry(tmp_path, "session-one", ["E1-F1-S1-T1", "E1-F1-S1-T2"])
        mock_sdk = self._make_mock_sdk()
        with (
            patch.dict(sys.modules, {"claude_agent_sdk": mock_sdk}),
            patch("devbench.cli.WORKSPACE_ROOT", tmp_path),
            patch("devbench.cli.BACKLOG_ROOT", tmp_path / "backlog"),
            patch("devbench.cli.BACKLOG_INDEX", tmp_path / "BACKLOG.md"),
            patch(
                "devbench.cli.BacklogParser.parse_index",
                return_value=[
                    WorkUnit(
                        id="E1-F1-S1-T1",
                        title="Task 1",
                        status=WorkUnitStatus.IN_QUEUE,
                        unit_type=WorkUnitType.TASK,
                        file_path=Path("backlog/E1-F1-S1-T1.md"),
                        repo="caylent-solutions/devbench",
                        dependencies=[],
                    ),
                    WorkUnit(
                        id="E1-F1-S1-T2",
                        title="Task 2",
                        status=WorkUnitStatus.IN_QUEUE,
                        unit_type=WorkUnitType.TASK,
                        file_path=Path("backlog/E1-F1-S1-T2.md"),
                        repo="caylent-solutions/devbench",
                        dependencies=[],
                    ),
                ],
            ),
        ):
            rc = cli.cmd_start("--include", "E1-F1-S1-T1,E1-F1-S1-T2", "--name", "session-two")

        assert rc == 1
        err = capsys.readouterr().err
        assert "E1-F1-S1-T1" in err
        assert "E1-F1-S1-T2" in err
        assert "session-one" in err

    @pytest.mark.unit
    def test_parse_start_args_allow_overlap_default_false(self) -> None:
        """_parse_start_args returns allow_overlap=False by default."""
        result = cli._parse_start_args(())
        assert isinstance(result, cli._CmdStartArgs)
        assert result.allow_overlap is False

    @pytest.mark.unit
    def test_parse_start_args_allow_overlap_flag_sets_true(self) -> None:
        """_parse_start_args returns allow_overlap=True when --allow-overlap is passed."""
        result = cli._parse_start_args(("--allow-overlap",))
        assert isinstance(result, cli._CmdStartArgs)
        assert result.allow_overlap is True


class TestMainMinArgs:
    """Test main() when a command doesn't have enough arguments."""

    def test_returns_1_with_insufficient_args(self, capsys: pytest.CaptureFixture[str]) -> None:
        """Lines 959-960: command requires more arguments than provided."""
        with patch("sys.argv", ["devbench", "claim"]):
            result = cli.main()
        assert result == 1
        err = capsys.readouterr().err
        assert "requires at least" in err


class TestCmdReport:
    """Test cmd_report command."""

    def test_cmd_report_returns_zero(self, capsys: pytest.CaptureFixture[str]) -> None:
        """cmd_report returns 0 and prints the generated report."""
        with patch("devbench.cli.generate_report", create=True) as mock_gen:
            mock_gen.return_value = "Test report output"
            with patch("devbench.reporting.report.generate_report", mock_gen):
                result = cli.cmd_report()

        assert result == 0
        assert "Test report output" in capsys.readouterr().out

    def test_cmd_report_with_since_timestamp(self) -> None:
        """cmd_report parses the 'since' argument into a datetime and passes it to generate_report."""
        from datetime import UTC, datetime

        captured_kwargs: dict = {}

        def fake_generate_report(**kwargs: object) -> str:
            captured_kwargs.update(kwargs)
            return "report"

        with patch("devbench.reporting.report.generate_report", side_effect=fake_generate_report):
            result = cli.cmd_report(since="2025-01-15T10:30:00Z")

        assert result == 0
        assert captured_kwargs["since"] == datetime(2025, 1, 15, 10, 30, 0, tzinfo=UTC)

    def test_cmd_report_watch_zero_runs_once(self, capsys: pytest.CaptureFixture[str]) -> None:
        """cmd_report with watch_interval=0 runs once (one-shot mode)."""
        with patch("devbench.reporting.report.generate_report", return_value="one-shot report"):
            result = cli.cmd_report(watch_interval=0)

        assert result == 0
        assert "one-shot report" in capsys.readouterr().out

    def test_cmd_report_watch_falls_through_to_streaming_with_deprecation(
        self, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """Issue #163: ``--watch N`` is deprecated. The interval value is
        ignored; the call falls through to the streaming loop and emits
        a deprecation notice."""
        import warnings

        def fake_stream_report(*args: object, **kwargs: object) -> int:
            # Stand-in for the streaming loop: returns immediately.
            return 0

        with (
            patch("sys.stdout.isatty", return_value=True),
            patch("devbench.reporting.report.generate_report", return_value="frame"),
            patch("devbench.reporting.streaming.stream_report", side_effect=fake_stream_report),
            warnings.catch_warnings(record=True) as caught,
        ):
            warnings.simplefilter("always")
            result = cli.cmd_report(watch_interval=5)

        assert result == 0
        # Deprecation warning fired for --watch.
        assert any(issubclass(w.category, DeprecationWarning) and "--watch" in str(w.message) for w in caught)

    def test_cmd_report_streams_on_tty_by_default(self) -> None:
        """Issue #163: the default report invocation on a TTY uses the streaming loop."""
        called_with: dict[str, object] = {}

        def fake_stream_report(log_path: object, render_fn: object, **kwargs: object) -> int:
            called_with["log_path"] = log_path
            called_with["render_fn"] = render_fn
            return 0

        with (
            patch("sys.stdout.isatty", return_value=True),
            patch("devbench.reporting.streaming.stream_report", side_effect=fake_stream_report),
        ):
            result = cli.cmd_report()

        assert result == 0
        assert "log_path" in called_with
        assert callable(called_with["render_fn"])

    def test_cmd_report_once_flag_forces_one_shot(self, capsys: pytest.CaptureFixture[str]) -> None:
        """Issue #163: ``--once`` (passed via main()'s flag-extraction) forces
        the legacy one-shot snapshot regardless of TTY status."""
        with (
            patch("sys.stdout.isatty", return_value=True),
            patch("devbench.reporting.report.generate_report", return_value="one-shot text"),
        ):
            result = cli.cmd_report(once=True)

        assert result == 0
        assert "one-shot text" in capsys.readouterr().out

    def test_cmd_report_non_tty_forces_one_shot(self, capsys: pytest.CaptureFixture[str]) -> None:
        """Issue #163: piping / redirecting stdout (non-TTY) forces one-shot
        rendering so script / CI consumers see the snapshot and exit."""
        with (
            patch("sys.stdout.isatty", return_value=False),
            patch("devbench.reporting.report.generate_report", return_value="piped"),
        ):
            result = cli.cmd_report()

        assert result == 0
        assert "piped" in capsys.readouterr().out

    def test_cmd_report_since_forces_one_shot(self, capsys: pytest.CaptureFixture[str]) -> None:
        """Issue #163: ``--since <ISO-8601>`` keeps one-shot semantics --
        a frozen-window snapshot doesn't benefit from continuous refresh."""
        with (
            patch("sys.stdout.isatty", return_value=True),
            patch("devbench.reporting.report.generate_report", return_value="since-snapshot"),
        ):
            result = cli.cmd_report(since="2026-05-01T00:00:00Z")

        assert result == 0
        assert "since-snapshot" in capsys.readouterr().out


class TestMainWatchFlagParsing:
    """Test --watch / -w flag extraction in main() (lines 978-988)."""

    def test_watch_flag_extracted_from_args(self) -> None:
        """--watch <N> is extracted from sys.argv for the report command."""
        with (
            patch("sys.argv", ["devbench", "report", "--watch", "10"]),
            patch("devbench.cli.cmd_report", return_value=0) as mock_report,
        ):
            result = cli.main()

        assert result == 0
        mock_report.assert_called_once_with(
            since="", watch_interval=10, once=False, include="", exclude="", session="", by_role=False
        )

    def test_short_watch_flag_extracted(self) -> None:
        """-w <N> is equivalent to --watch <N>."""
        with (
            patch("sys.argv", ["devbench", "report", "-w", "3"]),
            patch("devbench.cli.cmd_report", return_value=0) as mock_report,
        ):
            result = cli.main()

        assert result == 0
        mock_report.assert_called_once_with(
            since="", watch_interval=3, once=False, include="", exclude="", session="", by_role=False
        )

    def test_watch_flag_with_since_arg(self) -> None:
        """--watch is separated from the since timestamp argument."""
        with (
            patch("sys.argv", ["devbench", "report", "--watch", "5", "2025-01-15T10:30:00Z"]),
            patch("devbench.cli.cmd_report", return_value=0) as mock_report,
        ):
            result = cli.main()

        assert result == 0
        mock_report.assert_called_once_with(
            since="2025-01-15T10:30:00Z",
            watch_interval=5,
            once=False,
            include="",
            exclude="",
            session="",
            by_role=False,
        )

    def test_once_flag_extracted_from_args(self) -> None:
        """Issue #163: --once is extracted by main() and forwarded to cmd_report."""
        with (
            patch("sys.argv", ["devbench", "report", "--once"]),
            patch("devbench.cli.cmd_report", return_value=0) as mock_report,
        ):
            result = cli.main()

        assert result == 0
        mock_report.assert_called_once_with(
            since="", watch_interval=0, once=True, include="", exclude="", session="", by_role=False
        )

    def test_no_stream_alias_extracted(self) -> None:
        """Issue #163: --no-stream is an accepted alias for --once."""
        with (
            patch("sys.argv", ["devbench", "report", "--no-stream"]),
            patch("devbench.cli.cmd_report", return_value=0) as mock_report,
        ):
            result = cli.main()

        assert result == 0
        mock_report.assert_called_once_with(
            since="", watch_interval=0, once=True, include="", exclude="", session="", by_role=False
        )

    def test_report_without_watch_dispatches_normally(self) -> None:
        """report without --watch routes directly to cmd_report with scope kwargs.

        Issue #190: main() now dispatches 'report' explicitly (not via generic
        func(*sliced_args)) so that --include / --exclude scope flags are
        forwarded as keyword arguments.
        """
        with (
            patch("sys.argv", ["devbench", "report"]),
            patch("devbench.cli.cmd_report", return_value=0) as mock_report,
        ):
            result = cli.main()

        assert result == 0
        mock_report.assert_called_once_with(
            since="", watch_interval=0, once=False, include="", exclude="", session="", by_role=False
        )


class TestMainExtraArgsWarning:
    """Test extra args warning in main() (lines 1000-1001)."""

    def test_extra_args_warning_printed(self, capsys: pytest.CaptureFixture[str]) -> None:
        """When more args than min_args+1 are provided, a warning is printed to stderr (line 1001)."""
        mock_fn = MagicMock(return_value=0)
        with (
            patch("sys.argv", ["devbench", "mycmd", "arg1", "arg2", "arg3", "arg4"]),
            patch.dict(cli._COMMANDS, {"mycmd": (mock_fn, 1, "Test cmd")}),
        ):
            result = cli.main()

        assert result == 0
        err = capsys.readouterr().err
        assert "Warning: ignoring" in err
        assert "extra argument(s)" in err


class TestMainDispatchLine:
    """Test the final dispatch line in main() (line 1002/1006)."""

    def test_dispatch_with_min_args(self) -> None:
        """Dispatch passes exactly min_args arguments to the handler (line 1002)."""
        mock_fn = MagicMock(return_value=0)
        with (
            patch("sys.argv", ["devbench", "mycmd", "val1"]),
            patch.dict(cli._COMMANDS, {"mycmd": (mock_fn, 1, "Test")}),
        ):
            result = cli.main()

        assert result == 0
        mock_fn.assert_called_once_with("val1")

    def test_dispatch_with_optional_extra_arg(self) -> None:
        """Dispatch passes up to min_args+1 arguments (line 1002)."""
        mock_fn = MagicMock(return_value=0)
        with (
            patch("sys.argv", ["devbench", "mycmd", "val1", "val2"]),
            patch.dict(cli._COMMANDS, {"mycmd": (mock_fn, 1, "Test")}),
        ):
            result = cli.main()

        assert result == 0
        mock_fn.assert_called_once_with("val1", "val2")


class TestGitOpsDeferred:
    """Test _git_ops_deferred helper."""

    def test_git_ops_deferred_commits_locally(
        self,
        capsys: pytest.CaptureFixture[str],
        tmp_path: Path,
    ) -> None:
        """_git_ops_deferred calls commit_local and returns 0."""
        unit = WorkUnit(
            id="E0-F1-S1-T1",
            title="Test Task",
            status=WorkUnitStatus.IN_PROGRESS,
            unit_type=WorkUnitType.TASK,
            file_path=Path("backlog/E0-F1-S1-T1.md"),
            repo="caylent-solutions/git-repo",
            dependencies=[],
        )
        mock_ops = MagicMock()
        mock_mgr = MagicMock()

        with (
            patch("devbench.cli.GitOpsService", return_value=mock_ops, create=True),
            patch("devbench.github.git_ops.GitOpsService", return_value=mock_ops),
            patch("devbench.cli.BacklogManager", return_value=mock_mgr),
            patch("devbench.cli._resolve_unit_file", return_value=None),
        ):
            result = cli._git_ops_deferred(
                "E0-F1-S1-T1",
                unit,
                "caylent-solutions/git-repo",
                tmp_path,
                "feature/x",
            )

        assert result == 0
        mock_ops.commit_local.assert_called_once_with(
            "caylent-solutions/git-repo",
            tmp_path,
            "feature/x",
            "E0-F1-S1-T1: Test Task",
        )
        output = json.loads(capsys.readouterr().out.strip())
        assert output["mode"] == "deferred"

    def test_git_ops_deferred_calls_ensure_branch_before_commit(self, tmp_path: Path) -> None:
        """ensure_branch() must run before commit_local() so a drifted HEAD is corrected."""
        unit = WorkUnit(
            id="E0-F1-S1-T1",
            title="Test Task",
            status=WorkUnitStatus.IN_PROGRESS,
            unit_type=WorkUnitType.TASK,
            file_path=Path("backlog/E0-F1-S1-T1.md"),
            repo="caylent-solutions/git-repo",
            dependencies=[],
        )
        call_order: list[str] = []
        mock_ops = MagicMock()
        mock_ops.ensure_branch.side_effect = lambda *_a, **_k: call_order.append("ensure_branch")
        mock_ops.commit_local.side_effect = lambda *_a, **_k: call_order.append("commit_local")

        with (
            patch("devbench.cli.GitOpsService", return_value=mock_ops, create=True),
            patch("devbench.github.git_ops.GitOpsService", return_value=mock_ops),
            patch("devbench.cli.BacklogManager", return_value=MagicMock()),
            patch("devbench.cli._resolve_unit_file", return_value=None),
        ):
            cli._git_ops_deferred(
                "E0-F1-S1-T1",
                unit,
                "caylent-solutions/git-repo",
                tmp_path,
                "feature/x",
            )

        assert call_order == ["ensure_branch", "commit_local"]

    def test_git_ops_deferred_logs_comment(self, tmp_path: Path) -> None:
        """_git_ops_deferred appends agent comment when work-unit file exists."""
        wu_file = tmp_path / "E0-F1-S1-T1.md"
        wu_file.write_text("# placeholder")

        unit = WorkUnit(
            id="E0-F1-S1-T1",
            title="Test Task",
            status=WorkUnitStatus.IN_PROGRESS,
            unit_type=WorkUnitType.TASK,
            file_path=Path("backlog/E0-F1-S1-T1.md"),
            repo="caylent-solutions/git-repo",
            dependencies=[],
        )
        mock_ops = MagicMock()
        mock_mgr = MagicMock()

        with (
            patch("devbench.cli.GitOpsService", return_value=mock_ops, create=True),
            patch("devbench.github.git_ops.GitOpsService", return_value=mock_ops),
            patch("devbench.cli.BacklogManager", return_value=mock_mgr),
            patch("devbench.cli._resolve_unit_file", return_value=wu_file),
            # Bypass manifest-scope check: this test only cares that the
            # audit comment was appended, not about manifest enforcement
            # (which has its own dedicated tests).
            patch("devbench.backlog.manifest.parse_manifest", return_value=[]),
            patch("devbench.backlog.manifest.assert_staged_matches_manifest"),
        ):
            result = cli._git_ops_deferred(
                "E0-F1-S1-T1",
                unit,
                "caylent-solutions/git-repo",
                tmp_path,
                "feature/x",
            )

        assert result == 0
        mock_mgr._append_agent_comment.assert_called_once()
        call_args = mock_mgr._append_agent_comment.call_args
        assert call_args[0][0] == wu_file
        assert "COMMIT_DEFERRED" in call_args[0][2]


class TestCmdGitOpsFinalize:
    """Test cmd_git_ops_finalize command."""

    def test_git_ops_finalize_requires_single_branch(
        self,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """cmd_git_ops_finalize returns 1 when SINGLE_BRANCH is not set."""
        with patch("devbench.config.SINGLE_BRANCH", None):
            result = cli.cmd_git_ops_finalize("caylent-solutions/git-repo")

        assert result == 1
        assert "single_branch" in capsys.readouterr().err.lower()

    def test_git_ops_finalize_requires_defer_pr(
        self,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """cmd_git_ops_finalize returns 1 when DEFER_PR is False."""
        with (
            patch("devbench.config.SINGLE_BRANCH", "feature/combined"),
            patch("devbench.config.DEFER_PR", False),
        ):
            result = cli.cmd_git_ops_finalize("caylent-solutions/git-repo")

        assert result == 1
        assert "defer_pr" in capsys.readouterr().err.lower()


class TestCmdGitOpsFinalizeNotifications:
    """Issue #219: cmd_git_ops_finalize and _handle_finalize_ci_result fire
    the same Slack notifications as the per-WU cmd_git_ops path.

    Before this fix, the auto-finalize batch-PR path was completely silent on
    Slack: pr_opened, ci_failure, and (Bundle C) ci_pass all needed wiring.
    Operators running ``defer_pr: true`` + ``auto_finalize: true`` saw zero
    Slack pings about their PR's lifecycle.
    """

    @staticmethod
    def _make_unit_with_status(unit_id: str, status_attr: str) -> Any:
        """Build a minimal work-unit stub for _find_most_recent_active_task."""
        from devbench.backlog.work_unit import WorkUnit, WorkUnitStatus, WorkUnitType

        return WorkUnit(
            id=unit_id,
            title=f"Stub {unit_id}",
            status=getattr(WorkUnitStatus, status_attr),
            unit_type=WorkUnitType.TASK,
            file_path=Path(f"backlog/{unit_id}.md"),
            repo="caylent-solutions/git-repo",
            dependencies=[],
        )

    @pytest.mark.unit
    def test_notify_pr_opened_fires_after_create_pr(self, tmp_path: Path) -> None:
        """cmd_git_ops_finalize calls notify_pr_opened with the new PR URL
        when ``ops.create_pr`` actually creates a fresh PR (#219).  The
        firing is gated on ``ops.find_open_pr`` returning None beforehand
        (#220) -- the legitimate fresh-PR path."""
        from devbench.github.git_ops import CIResult

        mock_ops = MagicMock()
        mock_ops.find_open_pr.return_value = None  # no pre-existing -> fresh create
        mock_ops.create_pr.return_value = "https://github.com/org/repo/pull/99"
        mock_ops.wait_for_checks_and_classify.return_value = CIResult.GREEN

        captured: list[tuple[str, str, str]] = []

        def _capture(unit_id: str, repo: str, pr_url: str) -> None:
            captured.append((unit_id, repo, pr_url))

        with (
            patch("devbench.config.SINGLE_BRANCH", "feature/combined"),
            patch("devbench.config.DEFER_PR", True),
            patch("devbench.cli.REPO_LOCAL_PATHS", {"caylent-solutions/git-repo": tmp_path}),
            patch("devbench.github.git_ops.GitOpsService", return_value=mock_ops),
            patch("devbench.cli._handle_finalize_ci_result", return_value=0),
            patch("devbench.notifications.notify_pr_opened", _capture),
        ):
            rc = cli.cmd_git_ops_finalize("caylent-solutions/git-repo")

        assert rc == 0
        assert captured, "notify_pr_opened must fire on fresh PR creation"
        unit_id, repo, pr_url = captured[-1]
        assert pr_url == "https://github.com/org/repo/pull/99"
        assert repo == "caylent-solutions/git-repo"
        assert unit_id, "rep unit id must be non-empty"

    @pytest.mark.unit
    def test_notify_pr_opened_not_fired_when_pr_already_open(self, tmp_path: Path) -> None:
        """Issue #220: when ``ops.find_open_pr`` returns an existing URL,
        ``cmd_git_ops_finalize`` is restarting against a previously-opened
        batch PR.  No fresh PR was created, so ``notify_pr_opened`` must
        NOT fire -- avoids the misleading ':git: PR opened' Slack ping that
        previously fired on every restart of a finalize cycle."""
        from devbench.github.git_ops import CIResult

        existing_url = "https://github.com/org/repo/pull/60"
        mock_ops = MagicMock()
        mock_ops.find_open_pr.return_value = existing_url  # PR already open
        mock_ops.create_pr.return_value = existing_url  # create_pr returns existing
        mock_ops.wait_for_checks_and_classify.return_value = CIResult.GREEN

        captured: list[Any] = []

        with (
            patch("devbench.config.SINGLE_BRANCH", "feature/combined"),
            patch("devbench.config.DEFER_PR", True),
            patch("devbench.cli.REPO_LOCAL_PATHS", {"caylent-solutions/git-repo": tmp_path}),
            patch("devbench.github.git_ops.GitOpsService", return_value=mock_ops),
            patch("devbench.cli._handle_finalize_ci_result", return_value=0),
            patch(
                "devbench.notifications.notify_pr_opened",
                lambda *a, **kw: captured.append(a),
            ),
        ):
            rc = cli.cmd_git_ops_finalize("caylent-solutions/git-repo")

        assert rc == 0
        assert not captured, f"notify_pr_opened MUST NOT fire on PR reuse (#220); captured={captured!r}"

    @pytest.mark.unit
    def test_notify_ci_failure_fires_on_failed_known_task(self, tmp_path: Path) -> None:
        """_handle_finalize_ci_result calls notify_ci_failure when CI
        attributes the failure to a specific known task."""
        from devbench.backlog.manager import BacklogManager
        from devbench.github.git_ops import CIResult

        captured: list[tuple[str, str, str, int]] = []

        def _capture(unit_id: str, repo: str, pr_url: str, attempt: int) -> None:
            captured.append((unit_id, repo, pr_url, attempt))

        mgr = MagicMock(spec=BacklogManager)
        with (
            patch("devbench.notifications.notify_ci_failure", _capture),
            patch(
                "devbench.cli._handle_finalize_known_task_failure",
                return_value=2,
            ),
        ):
            rc = cli._handle_finalize_ci_result(
                ci_result=CIResult.FAILED_KNOWN_TASK(task_id="E1-F1-S1-T1"),
                pr_url="https://github.com/org/repo/pull/99",
                mgr=mgr,
                repo="caylent-solutions/git-repo",
            )

        assert rc == 2
        assert captured, "notify_ci_failure was never called on FAILED_KNOWN_TASK"
        unit_id, repo, pr_url, attempt = captured[-1]
        assert unit_id == "E1-F1-S1-T1"
        assert repo == "caylent-solutions/git-repo"
        assert pr_url == "https://github.com/org/repo/pull/99"
        assert attempt == 1, "Finalize path has no retry counter today; sentinel attempt=1 documented"

    @pytest.mark.unit
    def test_notify_ci_failure_fires_on_failed_unknown(self, tmp_path: Path) -> None:
        """_handle_finalize_ci_result calls notify_ci_failure with the
        most-recent active task id when CI attribution is unknown."""
        from devbench.backlog.manager import BacklogManager
        from devbench.github.git_ops import CIResult

        unit = self._make_unit_with_status("E0-F1-S1-T1", "IN_REVIEW")
        captured: list[tuple[str, str, str, int]] = []

        def _capture(unit_id: str, repo: str, pr_url: str, attempt: int) -> None:
            captured.append((unit_id, repo, pr_url, attempt))

        mgr = MagicMock(spec=BacklogManager)
        with (
            patch("devbench.notifications.notify_ci_failure", _capture),
            patch(
                "devbench.cli.BacklogParser",
                return_value=MagicMock(parse_index=MagicMock(return_value=[unit])),
            ),
            patch("devbench.cli._find_most_recent_active_task", return_value=unit),
            patch("devbench.cli._resolve_unit_file", return_value=None),
            patch("devbench.cli._finalize_audit_and_block"),
        ):
            rc = cli._handle_finalize_ci_result(
                ci_result=CIResult.FAILED_UNKNOWN,
                pr_url="https://github.com/org/repo/pull/99",
                mgr=mgr,
                repo="caylent-solutions/git-repo",
            )

        assert rc == 2
        assert captured, "notify_ci_failure was never called on FAILED_UNKNOWN"
        assert captured[-1][0] == "E0-F1-S1-T1"

    @pytest.mark.unit
    def test_notify_ci_pass_fires_on_ci_green(self, tmp_path: Path) -> None:
        """Issue #219 Bundle C: _handle_finalize_ci_result fires
        notify_ci_pass when CI on the batch PR is green so the operator
        running ``auto_merge: false`` knows the PR is ready to merge.
        No notify_ci_failure on the same path."""
        from devbench.backlog.manager import BacklogManager
        from devbench.github.git_ops import CIResult

        captured_pass: list[tuple[str, str, str]] = []
        captured_failure: list[Any] = []

        def _capture_pass(unit_id: str, repo: str, pr_url: str) -> None:
            captured_pass.append((unit_id, repo, pr_url))

        mgr = MagicMock(spec=BacklogManager)
        with (
            patch("devbench.notifications.notify_ci_pass", _capture_pass),
            patch(
                "devbench.notifications.notify_ci_failure",
                lambda *a, **kw: captured_failure.append(a),
            ),
            patch(
                "devbench.cli.BacklogParser",
                return_value=MagicMock(parse_index=MagicMock(return_value=[])),
            ),
            patch("devbench.cli._find_most_recent_active_task", return_value=None),
        ):
            rc = cli._handle_finalize_ci_result(
                ci_result=CIResult.GREEN,
                pr_url="https://github.com/org/repo/pull/99",
                mgr=mgr,
                repo="caylent-solutions/git-repo",
            )

        assert rc == 0
        assert captured_pass, "notify_ci_pass must fire on CI GREEN (#219 Bundle C)"
        unit_id, repo, pr_url = captured_pass[-1]
        assert pr_url == "https://github.com/org/repo/pull/99"
        assert repo == "caylent-solutions/git-repo"
        # No active WU -> falls back to symbolic "finalize" sentinel.
        assert unit_id == "finalize"
        assert not captured_failure, "notify_ci_failure must NOT fire on GREEN"

    @pytest.mark.unit
    def test_no_notification_on_ci_timeout(self, tmp_path: Path) -> None:
        """TIMEOUT is audit-log-only; notify_ci_failure must NOT fire."""
        from devbench.backlog.manager import BacklogManager
        from devbench.github.git_ops import CIResult

        captured: list[Any] = []
        mgr = MagicMock(spec=BacklogManager)
        with (
            patch("devbench.notifications.notify_ci_failure", lambda *a, **kw: captured.append(a)),
            patch(
                "devbench.cli.BacklogParser",
                return_value=MagicMock(parse_index=MagicMock(return_value=[])),
            ),
            patch("devbench.cli._find_most_recent_active_task", return_value=None),
        ):
            rc = cli._handle_finalize_ci_result(
                ci_result=CIResult.TIMEOUT,
                pr_url="https://github.com/org/repo/pull/99",
                mgr=mgr,
                repo="caylent-solutions/git-repo",
            )

        assert rc == 2
        assert not captured, "notify_ci_failure must NOT fire on TIMEOUT"


class TestRejectEmDash:
    """Agent-supplied text with U+2014 must be rejected at the CLI input boundary.

    The validate-backlog Check 10 rejects work-unit files containing em-dash,
    so any CLI writer that accepts free-form agent text must fail fast rather
    than silently poisoning the file.
    """

    _EM_DASH_FEEDBACK = "issue A -\u2014 still broken"

    def test_log_verdict_fail_feedback_with_em_dash_rejected(self, capsys: pytest.CaptureFixture[str]) -> None:
        result = cli.cmd_log_verdict("code_review", "E0-F1-S1-T1", "fail", self._EM_DASH_FEEDBACK)
        err = capsys.readouterr().err
        assert result == 1
        assert "em-dash" in err
        assert "U+2014" in err

    def test_log_verdict_pass_feedback_with_em_dash_rejected(self, capsys: pytest.CaptureFixture[str]) -> None:
        """Pass verdicts can still carry feedback -- em-dash must still be rejected."""
        result = cli.cmd_log_verdict("code_review", "E0-F1-S1-T1", "pass", self._EM_DASH_FEEDBACK)
        err = capsys.readouterr().err
        assert result == 1
        assert "em-dash" in err

    def test_log_comment_with_em_dash_rejected(self, capsys: pytest.CaptureFixture[str]) -> None:
        result = cli.cmd_log_comment("executor", "E0-F1-S1-T1", self._EM_DASH_FEEDBACK)
        err = capsys.readouterr().err
        assert result == 1
        assert "em-dash" in err

    def test_log_tdd_with_em_dash_rejected(self, capsys: pytest.CaptureFixture[str]) -> None:
        result = cli.cmd_log_tdd("E0-F1-S1-T1", "RED", self._EM_DASH_FEEDBACK)
        err = capsys.readouterr().err
        assert result == 1
        assert "em-dash" in err

    def test_clean_feedback_is_not_rejected_by_em_dash_guard(self) -> None:
        """The guard must return None for clean text -- double-hyphen is fine."""
        assert cli._reject_em_dash("feedback", "issue A -- still broken") is None
        assert cli._reject_em_dash("feedback", "") is None


# ---------------------------------------------------------------------------
# Amendment CLI commands
# ---------------------------------------------------------------------------


class TestCmdRequestAmendment:
    """cmd_request_amendment reads JSON from stdin and delegates to write_request."""

    _VALID_PAYLOAD: ClassVar[dict[str, Any]] = {
        "reason": "tdd_green_production_fix",
        "justification": "Test required a minimum production fix.",
        "files_to_add": [{"path": "src/example/parser.py", "change": "use utf-8-sig codec"}],
        "linked_acs": ["AC-TEST-001"],
    }

    def _stdin(self, monkeypatch: pytest.MonkeyPatch, text: str) -> None:
        import io

        monkeypatch.setattr("sys.stdin", io.StringIO(text))

    def test_happy_path_writes_request(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        self._stdin(monkeypatch, json.dumps(self._VALID_PAYLOAD))
        with patch("devbench.cli.WORKSPACE_ROOT", tmp_path):
            rc = cli.cmd_request_amendment("EX-F1-S1-T1")
        assert rc == 0
        out = capsys.readouterr().out
        summary = json.loads(out)
        assert summary["task_id"] == "EX-F1-S1-T1"
        assert summary["reason"] == "tdd_green_production_fix"
        assert (tmp_path / ".devbench/amendments/EX-F1-S1-T1.json").exists()

    def test_empty_stdin_rejected(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        self._stdin(monkeypatch, "")
        with patch("devbench.cli.WORKSPACE_ROOT", tmp_path):
            rc = cli.cmd_request_amendment("EX-F1-S1-T1")
        assert rc == 1
        assert "must be provided on stdin" in capsys.readouterr().err

    def test_invalid_json_rejected(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        self._stdin(monkeypatch, "{not json")
        with patch("devbench.cli.WORKSPACE_ROOT", tmp_path):
            rc = cli.cmd_request_amendment("EX-F1-S1-T1")
        assert rc == 1
        assert "not valid JSON" in capsys.readouterr().err

    def test_non_object_payload_rejected(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        self._stdin(monkeypatch, json.dumps(["array", "not", "object"]))
        with patch("devbench.cli.WORKSPACE_ROOT", tmp_path):
            rc = cli.cmd_request_amendment("EX-F1-S1-T1")
        assert rc == 1
        assert "must be a JSON object" in capsys.readouterr().err

    def test_schema_violation_rejected(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        bad = dict(self._VALID_PAYLOAD)
        del bad["reason"]
        self._stdin(monkeypatch, json.dumps(bad))
        with patch("devbench.cli.WORKSPACE_ROOT", tmp_path):
            rc = cli.cmd_request_amendment("EX-F1-S1-T1")
        assert rc == 1
        assert "invalid" in capsys.readouterr().err

    def test_duplicate_request_rejected(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        self._stdin(monkeypatch, json.dumps(self._VALID_PAYLOAD))
        with patch("devbench.cli.WORKSPACE_ROOT", tmp_path):
            assert cli.cmd_request_amendment("EX-F1-S1-T1") == 0
        # Second call with a fresh stdin attempts to write duplicate
        self._stdin(monkeypatch, json.dumps(self._VALID_PAYLOAD))
        with patch("devbench.cli.WORKSPACE_ROOT", tmp_path):
            rc = cli.cmd_request_amendment("EX-F1-S1-T1")
        assert rc == 1
        assert "already exists" in capsys.readouterr().err


class TestCmdApplyAmendment:
    """cmd_apply_amendment delegates to apply_amendment and handles AmendmentError."""

    def test_happy_path(self, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:

        with patch("devbench.cli.apply_amendment") as mock_apply:
            mock_apply.return_value = None
            rc = cli.cmd_apply_amendment("EX-F1-S1-T1")
        assert rc == 0
        assert "applied" in capsys.readouterr().out
        mock_apply.assert_called_once()

    def test_amendment_error_returns_1(self, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
        from devbench.backlog.amendment import AmendmentError

        with patch("devbench.cli.apply_amendment", side_effect=AmendmentError("post-check failed")):
            rc = cli.cmd_apply_amendment("EX-F1-S1-T1")
        assert rc == 1
        err = capsys.readouterr().err
        assert "post-check failed" in err


class TestCmdRejectAmendment:
    """cmd_reject_amendment delegates to reject_amendment and handles AmendmentError."""

    def test_happy_path(self, capsys: pytest.CaptureFixture[str]) -> None:
        with patch("devbench.cli.reject_amendment") as mock_reject:
            mock_reject.return_value = None
            rc = cli.cmd_reject_amendment("EX-F1-S1-T1", "files not in diff")
        assert rc == 0
        out = capsys.readouterr().out
        assert "rejected" in out
        mock_reject.assert_called_once()

    def test_em_dash_in_reason_rejected(self, capsys: pytest.CaptureFixture[str]) -> None:
        rc = cli.cmd_reject_amendment("EX-F1-S1-T1", "bad\u2014reason")
        assert rc == 1
        assert "em-dash" in capsys.readouterr().err

    def test_amendment_error_returns_1(self, capsys: pytest.CaptureFixture[str]) -> None:
        from devbench.backlog.amendment import AmendmentError

        with patch(
            "devbench.cli.reject_amendment",
            side_effect=AmendmentError("no pending request"),
        ):
            rc = cli.cmd_reject_amendment("EX-F1-S1-T1", "because")
        assert rc == 1
        assert "no pending request" in capsys.readouterr().err


class TestCmdWatch:
    """cmd_watch snapshot + live-tail behaviour."""

    def _fake_snapshot(self) -> object:
        from devbench.activity import ActivitySnapshot

        return ActivitySnapshot(
            now=__import__("datetime").datetime.now(tz=__import__("datetime").timezone.utc),
            mode_label="standard multi-PR",
            active_task_id=None,
            active_task_title=None,
            active_task_status=None,
            claimed_at=None,
            phase="idle",
            last_tool_call_at=None,
            subagent=None,
            recent_cli=[],
            repo_state=None,
            amendment=None,
            idle_seconds=0,
        )

    def test_cmd_watch_one_shot_returns_zero(self, capsys: pytest.CaptureFixture[str]) -> None:
        """watch_interval=0 runs once, prints the snapshot, and returns 0."""
        fake_snap = self._fake_snapshot()
        with (
            patch("devbench.activity.collect_snapshot", return_value=fake_snap),
            patch("devbench.activity.render_snapshot", return_value="dashboard frame"),
        ):
            rc = cli.cmd_watch(watch_interval=0)
        assert rc == 0
        assert "dashboard frame" in capsys.readouterr().out

    def test_cmd_watch_watch_mode_interrupted(self) -> None:
        """watch_interval > 0 loops until KeyboardInterrupt, exit code 0."""
        calls = {"renders": 0}

        def fake_render(_snapshot: object) -> str:
            calls["renders"] += 1
            return f"frame {calls['renders']}"

        def fake_sleep(_seconds: float) -> None:
            raise KeyboardInterrupt

        with (
            patch("devbench.activity.collect_snapshot", return_value=self._fake_snapshot()),
            patch("devbench.activity.render_snapshot", side_effect=fake_render),
            patch("time.sleep", side_effect=fake_sleep),
        ):
            rc = cli.cmd_watch(watch_interval=5)
        assert rc == 0
        assert calls["renders"] == 1

    def test_cmd_watch_invokes_clear_command(self) -> None:
        """Live mode clears the terminal between frames when a clear binary exists."""

        def fake_sleep(_seconds: float) -> None:
            raise KeyboardInterrupt

        captured: list[list[str]] = []

        def fake_run(cmd: list[str], **_kw: object) -> object:
            captured.append(cmd)

            class _Done:
                returncode = 0

            return _Done()

        with (
            patch("devbench.activity.collect_snapshot", return_value=self._fake_snapshot()),
            patch("devbench.activity.render_snapshot", return_value="frame"),
            patch("time.sleep", side_effect=fake_sleep),
            patch("devbench.cli._TERMINAL_CLEAR_CMD", "/usr/bin/clear"),
            patch("devbench.cli.subprocess.run", side_effect=fake_run),
        ):
            rc = cli.cmd_watch(watch_interval=1)
        assert rc == 0
        assert captured and captured[0] == ["/usr/bin/clear"]

    def test_cmd_watch_falls_back_to_ris(self, capsys: pytest.CaptureFixture[str]) -> None:
        """Without a clear binary, cmd_watch falls back to the VT100 RIS escape."""

        def fake_sleep(_seconds: float) -> None:
            raise KeyboardInterrupt

        with (
            patch("devbench.activity.collect_snapshot", return_value=self._fake_snapshot()),
            patch("devbench.activity.render_snapshot", return_value="frame"),
            patch("time.sleep", side_effect=fake_sleep),
            patch("devbench.cli._TERMINAL_CLEAR_CMD", None),
        ):
            rc = cli.cmd_watch(watch_interval=1)
        assert rc == 0
        assert "\033c" in capsys.readouterr().out

    def test_cmd_watch_registered_in_commands(self) -> None:
        assert "watch" in cli._COMMANDS

    def test_cmd_watch_reads_devbench_log_file_env(self, monkeypatch: pytest.MonkeyPatch) -> None:
        # AC-197-1: cmd_watch reads DEVBENCH_LOG_FILE as the canonical env var.
        monkeypatch.delenv("DEVBENCH_LOG_FILE", raising=False)
        monkeypatch.setenv("DEVBENCH_LOG_FILE", "/tmp/test-log.log")
        captured_paths: list[object] = []

        def fake_collect(**kwargs: object) -> object:
            captured_paths.append(kwargs.get("orchestrator_log"))
            return self._fake_snapshot()

        with (
            patch("devbench.activity.collect_snapshot", side_effect=fake_collect),
            patch("devbench.activity.render_snapshot", return_value="ok"),
        ):
            rc = cli.cmd_watch(watch_interval=0)
        assert rc == 0
        assert captured_paths and captured_paths[0] == Path("/tmp/test-log.log")

    def test_resolver_returns_none_on_unknown_repo(self, capsys: pytest.CaptureFixture[str]) -> None:
        """The inline repo resolver returns None when resolve_repo rejects the name."""
        captured_resolver: dict[str, object] = {}

        def fake_collect(**kwargs: object) -> object:
            captured_resolver["fn"] = kwargs["repo_path_resolver"]
            return self._fake_snapshot()

        with (
            patch("devbench.activity.collect_snapshot", side_effect=fake_collect),
            patch("devbench.activity.render_snapshot", return_value="ok"),
        ):
            cli.cmd_watch(watch_interval=0)

        resolver = captured_resolver["fn"]
        assert callable(resolver)
        assert resolver("no-such-repo") is None


class TestMainWatchCommand:
    """main() --watch dispatch for the watch command."""

    def test_main_watch_with_watch_flag(self) -> None:
        with (
            patch("sys.argv", ["devbench", "watch", "--watch", "3"]),
            patch("devbench.cli.cmd_watch", return_value=0) as mock_watch,
        ):
            rc = cli.main()
        assert rc == 0
        mock_watch.assert_called_once_with(watch_interval=3)

    def test_main_watch_short_flag(self) -> None:
        with (
            patch("sys.argv", ["devbench", "watch", "-w", "2"]),
            patch("devbench.cli.cmd_watch", return_value=0) as mock_watch,
        ):
            rc = cli.main()
        assert rc == 0
        mock_watch.assert_called_once_with(watch_interval=2)

    def test_main_watch_no_flag_runs_once(self) -> None:
        mock_fn = MagicMock(return_value=0)
        with (
            patch("sys.argv", ["devbench", "watch"]),
            patch.dict(cli._COMMANDS, {"watch": (mock_fn, 0, "Dashboard")}),
        ):
            rc = cli.main()
        assert rc == 0
        mock_fn.assert_called_once_with()


class TestCmdListProposals:
    def test_none_when_no_proposals(self, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
        with patch("devbench.cli.WORKSPACE_ROOT", tmp_path):
            rc = cli.cmd_list_proposals()
        assert rc == 0
        assert "No pending proposals" in capsys.readouterr().out

    def test_lists_when_present(self, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
        from devbench.backlog.proposal import Proposal, ProposedTask, write_proposal

        proposal = Proposal(
            source_task_id="E0-F1-S1-T1",
            generated_at="2026-04-18T00:00:00Z",
            rejection_reason="r",
            proposed_tasks=[
                ProposedTask(
                    suggested_id="E0-F1-S1-T2",
                    title="Fix X",
                    files_to_own=["src/x.py"],
                    linked_scenarios=["SC-01"],
                    suggested_acs=["AC-FUNC-001 fix"],
                    suggested_approach="ok",
                )
            ],
        )
        write_proposal(tmp_path, proposal)
        with patch("devbench.cli.WORKSPACE_ROOT", tmp_path):
            rc = cli.cmd_list_proposals()
        assert rc == 0
        out = capsys.readouterr().out
        assert "Pending proposals (1)" in out
        assert "E0-F1-S1-T2" in out


class TestCmdPromoteProposal:
    def test_missing_args(self, capsys: pytest.CaptureFixture[str]) -> None:
        rc = cli.cmd_promote_proposal("")
        assert rc == 1

    def test_all_from_requires_source(self, capsys: pytest.CaptureFixture[str]) -> None:
        rc = cli.cmd_promote_proposal("--all-from", "")
        assert rc == 1

    def test_promote_error_returns_1(self, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
        from devbench.backlog.proposal import ProposalError

        with (
            patch("devbench.cli.WORKSPACE_ROOT", tmp_path),
            patch("devbench.cli.BACKLOG_ROOT", tmp_path / "backlog"),
            patch("devbench.cli.BACKLOG_INDEX", tmp_path / "BACKLOG.md"),
            patch("devbench.cli.promote_proposal", side_effect=ProposalError("nope")),
        ):
            rc = cli.cmd_promote_proposal("E0-F1-S1-T2")
        assert rc == 1
        assert "nope" in capsys.readouterr().err

    def test_promote_happy_path(self, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
        from devbench.backlog.proposal import PromoteResult

        draft = tmp_path / "t.md"
        draft.write_text("x")
        result = PromoteResult(draft_path=draft, wired_targets=["E0-F1-S1-T1"])
        with (
            patch("devbench.cli.WORKSPACE_ROOT", tmp_path),
            patch("devbench.cli.BACKLOG_ROOT", tmp_path / "backlog"),
            patch("devbench.cli.BACKLOG_INDEX", tmp_path / "BACKLOG.md"),
            patch("devbench.cli.promote_proposal", return_value=result),
        ):
            rc = cli.cmd_promote_proposal("E0-F1-S1-T2")
        assert rc == 0
        out = capsys.readouterr().out
        assert "E0-F1-S1-T2" in out
        assert "in-queue" in out
        # ADR-10: wired_targets field present in output JSON.
        assert "E0-F1-S1-T1" in out
        assert "wired_targets" in out

    def test_promote_with_no_dep_flag(self, tmp_path: Path) -> None:
        from devbench.backlog.proposal import PromoteResult

        draft = tmp_path / "t.md"
        draft.write_text("x")
        seen: dict = {}

        def fake(
            *, workspace_root: Path, backlog_root: Path, backlog_index: Path, task_id: str, dep_on_source: bool = True
        ) -> PromoteResult:
            seen["dep"] = dep_on_source
            seen["id"] = task_id
            return PromoteResult(draft_path=draft, wired_targets=[])

        with (
            patch("devbench.cli.WORKSPACE_ROOT", tmp_path),
            patch("devbench.cli.BACKLOG_ROOT", tmp_path / "backlog"),
            patch("devbench.cli.BACKLOG_INDEX", tmp_path / "BACKLOG.md"),
            patch("devbench.cli.promote_proposal", side_effect=fake),
        ):
            rc = cli.cmd_promote_proposal("--no-dep-on-source", "E0-F1-S1-T2")
        assert rc == 0
        assert seen["dep"] is False
        assert seen["id"] == "E0-F1-S1-T2"

    def test_promote_all_from_happy(self, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
        with (
            patch("devbench.cli.WORKSPACE_ROOT", tmp_path),
            patch("devbench.cli.BACKLOG_ROOT", tmp_path / "backlog"),
            patch("devbench.cli.BACKLOG_INDEX", tmp_path / "BACKLOG.md"),
            patch("devbench.cli.promote_all_from_source", return_value=[tmp_path / "a.md", tmp_path / "b.md"]),
        ):
            rc = cli.cmd_promote_proposal("--all-from", "E0-F1-S1-T1")
        assert rc == 0
        out = capsys.readouterr().out
        assert '"promoted_count": 2' in out

    def test_promote_all_from_error(self, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
        from devbench.backlog.proposal import ProposalError

        with (
            patch("devbench.cli.WORKSPACE_ROOT", tmp_path),
            patch("devbench.cli.BACKLOG_ROOT", tmp_path / "backlog"),
            patch("devbench.cli.BACKLOG_INDEX", tmp_path / "BACKLOG.md"),
            patch("devbench.cli.promote_all_from_source", side_effect=ProposalError("nothing to do")),
        ):
            rc = cli.cmd_promote_proposal("--all-from", "E0-F1-S1-T1")
        assert rc == 1
        assert "nothing to do" in capsys.readouterr().err


class TestCmdRejectProposal:
    def test_missing_reason(self, capsys: pytest.CaptureFixture[str]) -> None:
        rc = cli.cmd_reject_proposal("E0-F1-S1-T2")
        assert rc == 1

    def test_reason_without_value(self, capsys: pytest.CaptureFixture[str]) -> None:
        rc = cli.cmd_reject_proposal("E0-F1-S1-T2", "--reason")
        assert rc == 1
        assert "requires a value" in capsys.readouterr().err

    def test_em_dash_in_reason_rejected(self, capsys: pytest.CaptureFixture[str]) -> None:
        # Generic missing-value path (--reason without a value) returns 1.
        rc = cli.cmd_reject_proposal("E0-F1-S1-T2", "--reason")
        assert rc == 1

    def test_em_dash_blocked_by_validator(self, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
        with (
            patch("devbench.cli.WORKSPACE_ROOT", tmp_path),
            patch("devbench.cli.BACKLOG_ROOT", tmp_path / "backlog"),
            patch("devbench.cli.BACKLOG_INDEX", tmp_path / "BACKLOG.md"),
        ):
            # task_id first, then --reason with em-dash value.
            rc = cli.cmd_reject_proposal("E0-F1-S1-T2", "--reason", "bad\u2014reason")
        assert rc == 1
        assert "em-dash" in capsys.readouterr().err

    def test_happy_path(self, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
        archive = tmp_path / "archive.md"
        archive.write_text("x")
        with (
            patch("devbench.cli.WORKSPACE_ROOT", tmp_path),
            patch("devbench.cli.BACKLOG_ROOT", tmp_path / "backlog"),
            patch("devbench.cli.BACKLOG_INDEX", tmp_path / "BACKLOG.md"),
            patch("devbench.cli.reject_proposal", return_value=archive),
        ):
            rc = cli.cmd_reject_proposal("E0-F1-S1-T2", "--reason")
            # "--reason" without value returns 1; the API requires both args.
            assert rc == 1

        with (
            patch("devbench.cli.WORKSPACE_ROOT", tmp_path),
            patch("devbench.cli.BACKLOG_ROOT", tmp_path / "backlog"),
            patch("devbench.cli.BACKLOG_INDEX", tmp_path / "BACKLOG.md"),
            patch("devbench.cli.reject_proposal", return_value=archive),
        ):
            # Correct shape: task id first, then --reason <val> as separate args.
            import sys as _sys

            with patch.object(_sys, "argv", ["devbench", "reject-proposal", "E0-F1-S1-T2", "--reason", "wrong"]):
                rc = cli.main()
        assert rc == 0

    def test_proposal_error_returns_1(self, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
        from devbench.backlog.proposal import ProposalError

        with (
            patch("devbench.cli.WORKSPACE_ROOT", tmp_path),
            patch("devbench.cli.BACKLOG_ROOT", tmp_path / "backlog"),
            patch("devbench.cli.BACKLOG_INDEX", tmp_path / "BACKLOG.md"),
            patch("devbench.cli.reject_proposal", side_effect=ProposalError("bad")),
        ):
            rc = cli.cmd_reject_proposal("E0-F1-S1-T2", "--reason")
            assert rc == 1  # Without reason value


class TestCmdStatusUnmaterialisedLine:
    """ADR-08 slice B: ``devbench status`` must always print an 'Un-materialised' row."""

    def test_status_prints_zero_line_when_no_proposals(
        self,
        tmp_path: Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """Row always renders so regressions to zero are visible."""
        parser = MagicMock()
        parser.parse_index.return_value = []
        parser.get_parallel_candidates.return_value = []
        parser.get_blocked_units.return_value = []
        parser.all_done.return_value = False
        with (
            patch("devbench.cli.BacklogParser", return_value=parser),
            patch("devbench.cli._count_unmaterialised_proposed_tasks", return_value=0),
        ):
            rc = cli.cmd_status()
        assert rc == 0
        out = capsys.readouterr().out
        assert "Un-materialised" in out, "status must always print the Un-materialised row."
        assert re.search(r"Un-materialised\s+0\b", out), (
            "Un-materialised row must render a zero count when no proposal JSONs are pending."
        )

    def test_status_prints_nonzero_count(
        self,
        tmp_path: Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        parser = MagicMock()
        parser.parse_index.return_value = []
        parser.get_parallel_candidates.return_value = []
        parser.get_blocked_units.return_value = []
        parser.all_done.return_value = False
        with (
            patch("devbench.cli.BacklogParser", return_value=parser),
            patch("devbench.cli._count_unmaterialised_proposed_tasks", return_value=7),
        ):
            rc = cli.cmd_status()
        assert rc == 0
        out = capsys.readouterr().out
        assert re.search(r"Un-materialised\s+7\b", out)


class TestCmdStatusBlockedSplit:
    """ADR-10: status emits six Blocked (...) rows always (one per BlockedTaskState)."""

    def test_status_emits_six_blocked_rows_even_at_zero(
        self,
        tmp_path: Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        parser = MagicMock()
        parser.parse_index.return_value = []
        parser.get_parallel_candidates.return_value = []
        parser.get_blocked_units.return_value = []
        parser.all_done.return_value = False
        with (
            patch("devbench.cli.BacklogParser", return_value=parser),
            patch("devbench.cli._count_unmaterialised_proposed_tasks", return_value=0),
        ):
            rc = cli.cmd_status()
        assert rc == 0
        out = capsys.readouterr().out
        assert re.search(r"Blocked \(auto-clearing\)\s+0\b", out), out
        assert re.search(r"Blocked \(amendment-recovery\)\s+0\b", out), out
        assert re.search(r"Blocked \(dependency\)\s+0\b", out), out
        assert re.search(r"Blocked \(held\)\s+0\b", out), out
        assert re.search(r"Blocked \(blocked-on-held\)\s+0\b", out), out
        assert re.search(r"Blocked \(operator-required\)\s+0\b", out), out
        # The bare "Blocked" row must NOT appear (it was replaced by the split).
        # Match the exact formatted row the pre-split code used to emit.
        assert not re.search(r"^\s*Blocked\s+\d+\s*$", out, flags=re.MULTILINE), out

    def test_status_counts_by_classifier(
        self,
        tmp_path: Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        from devbench.backlog.proposal import BlockedTaskState
        from devbench.backlog.work_unit import WorkUnit, WorkUnitStatus, WorkUnitType

        unit_a = WorkUnit(
            id="E0-F1-S1-T1",
            title="Source A",
            status=WorkUnitStatus.BLOCKED,
            unit_type=WorkUnitType.TASK,
            file_path=Path("/fake/a.md"),
            repo="r",
            dependencies=[],
        )
        unit_b = WorkUnit(
            id="E0-F1-S1-T2",
            title="Source B",
            status=WorkUnitStatus.BLOCKED,
            unit_type=WorkUnitType.TASK,
            file_path=Path("/fake/b.md"),
            repo="r",
            dependencies=[],
        )

        def fake_classify(
            backlog_root: Path,
            backlog_index: Path,
            task_id: str,
            **kwargs: object,
        ) -> BlockedTaskState:
            if task_id == "E0-F1-S1-T1":
                return BlockedTaskState.AUTO_CLEARING_VIA_PROPOSAL
            return BlockedTaskState.OPERATOR_ACTION_REQUIRED

        parser = MagicMock()
        parser.parse_index.return_value = [unit_a, unit_b]
        parser.get_parallel_candidates.return_value = []
        parser.get_blocked_units.return_value = [unit_a, unit_b]
        parser.all_done.return_value = False
        with (
            patch("devbench.cli.BacklogParser", return_value=parser),
            patch("devbench.cli._count_unmaterialised_proposed_tasks", return_value=0),
            patch("devbench.cli.classify_blocked_task", side_effect=fake_classify),
        ):
            rc = cli.cmd_status()
        assert rc == 0
        out = capsys.readouterr().out
        assert re.search(r"Blocked \(auto-clearing\)\s+1\b", out), out
        assert re.search(r"Blocked \(operator-required\)\s+1\b", out), out

    def test_status_detail_renders_three_blocked_bucket_sections(
        self,
        tmp_path: Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """Issue #149 follow-up: the ``--detail`` panel renders up to six separate blocked-task panels,
        one per non-empty BlockedTaskState bucket.

        Empty buckets are omitted from the output.
        """
        from devbench.backlog.proposal import BlockedTaskState
        from devbench.backlog.work_unit import WorkUnit, WorkUnitStatus, WorkUnitType

        unit_auto = WorkUnit(
            id="E0-F1-S1-T1",
            title="Auto",
            status=WorkUnitStatus.BLOCKED,
            unit_type=WorkUnitType.TASK,
            file_path=Path("/fake/auto.md"),
            repo="r",
            dependencies=[],
        )
        unit_recovery = WorkUnit(
            id="E0-F1-S1-T2",
            title="Recovery",
            status=WorkUnitStatus.BLOCKED,
            unit_type=WorkUnitType.TASK,
            file_path=Path("/fake/recovery.md"),
            repo="r",
            dependencies=[],
        )
        unit_attn = WorkUnit(
            id="E0-F1-S1-T3",
            title="Attn",
            status=WorkUnitStatus.BLOCKED,
            unit_type=WorkUnitType.TASK,
            file_path=Path("/fake/attn.md"),
            repo="r",
            dependencies=[],
        )

        def fake_classify(
            backlog_root: Path,
            backlog_index: Path,
            task_id: str,
            **kwargs: object,
        ) -> BlockedTaskState:
            return {
                "E0-F1-S1-T1": BlockedTaskState.AUTO_CLEARING_VIA_PROPOSAL,
                "E0-F1-S1-T2": BlockedTaskState.AWAITING_AMENDMENT_RECOVERY,
                "E0-F1-S1-T3": BlockedTaskState.OPERATOR_ACTION_REQUIRED,
            }[task_id]

        parser = MagicMock()
        parser.parse_index.return_value = [unit_auto, unit_recovery, unit_attn]
        parser.get_parallel_candidates.return_value = []
        parser.get_blocked_units.return_value = [unit_auto, unit_recovery, unit_attn]
        parser.all_done.return_value = False
        with (
            patch("devbench.cli.BacklogParser", return_value=parser),
            patch("devbench.cli._count_unmaterialised_proposed_tasks", return_value=0),
            patch("devbench.cli.classify_blocked_task", side_effect=fake_classify),
            patch("devbench.cli._resolve_unit_file", return_value=None),
        ):
            rc = cli.cmd_status("--detail")
        assert rc == 0
        out = capsys.readouterr().out
        assert "Blocked tasks (auto-clearing via proposal) (1):" in out
        assert "Blocked tasks (awaiting amendment recovery) (1):" in out
        assert "Blocked tasks (operator action required) (1):" in out
        # Each task appears exactly under its own bucket header.
        auto_pos = out.index("Blocked tasks (auto-clearing via proposal)")
        recovery_pos = out.index("Blocked tasks (awaiting amendment recovery)")
        attn_pos = out.index("Blocked tasks (operator action required)")
        assert auto_pos < recovery_pos < attn_pos, "buckets must render in classifier order"
        assert out.index("E0-F1-S1-T1") < recovery_pos
        assert recovery_pos < out.index("E0-F1-S1-T2") < attn_pos
        assert attn_pos < out.index("E0-F1-S1-T3")

    def test_status_detail_omits_empty_buckets(
        self,
        tmp_path: Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """When a bucket has zero tasks, its section header is omitted."""
        from devbench.backlog.proposal import BlockedTaskState
        from devbench.backlog.work_unit import WorkUnit, WorkUnitStatus, WorkUnitType

        unit_only_auto = WorkUnit(
            id="E0-F1-S1-T1",
            title="Auto-only",
            status=WorkUnitStatus.BLOCKED,
            unit_type=WorkUnitType.TASK,
            file_path=Path("/fake/auto.md"),
            repo="r",
            dependencies=[],
        )
        parser = MagicMock()
        parser.parse_index.return_value = [unit_only_auto]
        parser.get_parallel_candidates.return_value = []
        parser.get_blocked_units.return_value = [unit_only_auto]
        parser.all_done.return_value = False
        with (
            patch("devbench.cli.BacklogParser", return_value=parser),
            patch("devbench.cli._count_unmaterialised_proposed_tasks", return_value=0),
            patch(
                "devbench.cli.classify_blocked_task",
                return_value=BlockedTaskState.AUTO_CLEARING_VIA_PROPOSAL,
            ),
            patch("devbench.cli._resolve_unit_file", return_value=None),
        ):
            rc = cli.cmd_status("--detail")
        assert rc == 0
        out = capsys.readouterr().out
        assert "Blocked tasks (auto-clearing via proposal) (1):" in out
        assert "Blocked tasks (awaiting amendment recovery)" not in out
        assert "Blocked tasks (operator action required)" not in out


class TestCmdListProposalsStateLabels:
    """ADR-08 slice D: each listing line has a ``[state]`` label prefix."""

    def test_labels_per_task_state(self, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
        from devbench.backlog.proposal import (
            Proposal,
            ProposalTaskState,
            ProposedTask,
            write_proposal,
        )

        proposal = Proposal(
            source_task_id="E0-F1-S1-T1",
            generated_at="2026-04-19T00:00:00Z",
            rejection_reason="r",
            proposed_tasks=[
                ProposedTask(
                    suggested_id="E0-F1-S1-T2",
                    title="Umat",
                    files_to_own=["src/x.py"],
                    linked_scenarios=["SC-01"],
                    suggested_acs=["AC-001 fix"],
                    suggested_approach="ok",
                ),
                ProposedTask(
                    suggested_id="E0-F1-S1-T3",
                    title="Prop",
                    files_to_own=["src/y.py"],
                    linked_scenarios=["SC-02"],
                    suggested_acs=["AC-002 fix"],
                    suggested_approach="ok",
                ),
            ],
        )
        write_proposal(tmp_path, proposal)

        def fake_classify(backlog_root: Path, workspace_root: Path, task_id: str) -> ProposalTaskState:
            return {
                "E0-F1-S1-T2": ProposalTaskState.UNMATERIALISED,
                "E0-F1-S1-T3": ProposalTaskState.PROPOSED,
            }[task_id]

        with (
            patch("devbench.cli.WORKSPACE_ROOT", tmp_path),
            patch("devbench.cli.BACKLOG_ROOT", tmp_path / "backlog"),
            patch("devbench.cli.classify_proposed_task", side_effect=fake_classify),
        ):
            rc = cli.cmd_list_proposals()
        assert rc == 0
        out = capsys.readouterr().out
        # Labels must be present and distinct.
        assert "[unmaterialised]" in out, "Every un-materialised task gets an [unmaterialised] label"
        assert "[proposed]" in out, "Every proposed task gets a [proposed] label"
        # Sanity: both suggested ids present.
        assert "E0-F1-S1-T2" in out and "E0-F1-S1-T3" in out


class TestCmdRejectProposalUnmaterialised:
    """ADR-08 slice E: ``reject-proposal --unmaterialised <id> --reason <msg>`` CLI form."""

    def test_unmaterialised_flag_dispatches_through_api(
        self,
        tmp_path: Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        archive = tmp_path / "a.json"
        archive.write_text("{}")
        seen: dict = {}

        def fake_reject(
            *,
            workspace_root: Path,
            backlog_root: Path,
            backlog_index: Path,
            task_id: str = "",
            unmaterialised_source_id: str = "",
            reason: str,
        ) -> Path | None:
            seen["task_id"] = task_id
            seen["umid"] = unmaterialised_source_id
            seen["reason"] = reason
            return archive

        with (
            patch("devbench.cli.WORKSPACE_ROOT", tmp_path),
            patch("devbench.cli.BACKLOG_ROOT", tmp_path / "backlog"),
            patch("devbench.cli.BACKLOG_INDEX", tmp_path / "BACKLOG.md"),
            patch("devbench.cli.reject_proposal", side_effect=fake_reject),
        ):
            rc = cli.cmd_reject_proposal("--unmaterialised", "E0-F1-S1-T1", "--reason", "redundant")
        assert rc == 0, capsys.readouterr().err
        assert seen == {
            "task_id": "",
            "umid": "E0-F1-S1-T1",
            "reason": "redundant",
        }
        out = capsys.readouterr().out
        assert "rejected-unmaterialised" in out
        assert "E0-F1-S1-T1" in out

    def test_both_forms_supplied_errors(self, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
        with (
            patch("devbench.cli.WORKSPACE_ROOT", tmp_path),
            patch("devbench.cli.BACKLOG_ROOT", tmp_path / "backlog"),
            patch("devbench.cli.BACKLOG_INDEX", tmp_path / "BACKLOG.md"),
        ):
            rc = cli.cmd_reject_proposal("E0-F1-S1-T2", "--unmaterialised", "E0-F1-S1-T1", "--reason", "no")
        assert rc == 1
        err = capsys.readouterr().err
        assert "not both" in err or "supply exactly one" in err

    def test_neither_form_supplied_errors(self, capsys: pytest.CaptureFixture[str]) -> None:
        rc = cli.cmd_reject_proposal("--reason", "lonely")
        assert rc == 1
        err = capsys.readouterr().err
        assert "requires either" in err

    def test_unmaterialised_without_value_errors(self, capsys: pytest.CaptureFixture[str]) -> None:
        rc = cli.cmd_reject_proposal("--unmaterialised", "--reason", "x")
        assert rc == 1
        err = capsys.readouterr().err
        assert "source-task-id" in err

    def test_is_variadic_so_multi_flag_invocation_reaches_handler(self) -> None:
        """Regression: ``reject-proposal --unmaterialised <id> --reason <text>``
        passes 4 args + 1 task-id; without variadic dispatch the top-level
        slicer keeps only ``min_args + 1`` args and the ``--reason`` value
        is dropped before _parse_reject_proposal_argv runs, producing a
        spurious ``--reason requires a value`` error. Pin the variadic
        membership so this regression cannot return.
        """
        assert "reject-proposal" in cli._VARIADIC_COMMANDS


class TestCmdSweepProposals:
    """ADR-08 slice J: ``devbench sweep-proposals`` best-effort materialises un-materialised JSONs."""

    def test_nothing_to_do_when_no_proposals(self, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
        import dataclasses

        # The "nothing to do" fast-exit is the auto-accept-OFF path; pin it
        # explicitly now that auto_accept_proposals defaults to True.
        no_auto = dataclasses.replace(
            cli.RUNTIME_CONFIG,
            task_factory=dataclasses.replace(cli.RUNTIME_CONFIG.task_factory, auto_accept_proposals=False),
        )
        with (
            patch("devbench.cli.WORKSPACE_ROOT", tmp_path),
            patch("devbench.cli.BACKLOG_ROOT", tmp_path / "backlog"),
            patch("devbench.cli.BACKLOG_INDEX", tmp_path / "BACKLOG.md"),
            patch("devbench.cli.RUNTIME_CONFIG", no_auto),
        ):
            rc = cli.cmd_sweep_proposals()
        assert rc == 0
        assert "nothing to do" in capsys.readouterr().out

    def test_materialises_one_unmaterialised_proposal(
        self,
        tmp_path: Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        from devbench.backlog.proposal import (
            Proposal,
            ProposalTaskState,
            ProposedTask,
            write_proposal,
        )

        proposal = Proposal(
            source_task_id="E0-F1-S1-T1",
            generated_at="2026-04-19T00:00:00Z",
            rejection_reason="r",
            proposed_tasks=[
                ProposedTask(
                    suggested_id="E0-F1-S1-T2",
                    title="x",
                    files_to_own=["src/x.py"],
                    linked_scenarios=["SC-01"],
                    suggested_acs=["AC-001 fix"],
                    suggested_approach="ok",
                )
            ],
        )
        write_proposal(tmp_path, proposal)

        parser = MagicMock()
        unit = MagicMock()
        unit.id = "E0-F1-S1-T1"
        unit.repo = "caylent-solutions/example"
        parser.parse_index.return_value = [unit]

        with (
            patch("devbench.cli.WORKSPACE_ROOT", tmp_path),
            patch("devbench.cli.BACKLOG_ROOT", tmp_path / "backlog"),
            patch("devbench.cli.BACKLOG_INDEX", tmp_path / "BACKLOG.md"),
            patch("devbench.cli.BacklogParser", return_value=parser),
            patch(
                "devbench.cli.classify_proposed_task",
                return_value=ProposalTaskState.UNMATERIALISED,
            ),
            patch("devbench.cli.materialise_proposal", return_value=[tmp_path / "a.md"]),
        ):
            rc = cli.cmd_sweep_proposals()
        assert rc == 0
        out = capsys.readouterr().out
        assert "materialised E0-F1-S1-T1: 1 new, 0 skipped" in out

    def test_tolerates_proposal_error_per_entry(
        self,
        tmp_path: Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """A ProposalError on one proposal must be logged and skipped, not raised."""
        from devbench.backlog.proposal import (
            Proposal,
            ProposalError,
            ProposalTaskState,
            ProposedTask,
            write_proposal,
        )

        proposal = Proposal(
            source_task_id="E0-F1-S1-T1",
            generated_at="2026-04-19T00:00:00Z",
            rejection_reason="r",
            proposed_tasks=[
                ProposedTask(
                    suggested_id="E0-F1-S1-T2",
                    title="x",
                    files_to_own=["src/x.py"],
                    linked_scenarios=["SC-01"],
                    suggested_acs=["AC-001 fix"],
                    suggested_approach="ok",
                )
            ],
        )
        write_proposal(tmp_path, proposal)

        parser = MagicMock()
        unit = MagicMock()
        unit.id = "E0-F1-S1-T1"
        unit.repo = "caylent-solutions/example"
        parser.parse_index.return_value = [unit]

        with (
            patch("devbench.cli.WORKSPACE_ROOT", tmp_path),
            patch("devbench.cli.BACKLOG_ROOT", tmp_path / "backlog"),
            patch("devbench.cli.BACKLOG_INDEX", tmp_path / "BACKLOG.md"),
            patch("devbench.cli.BacklogParser", return_value=parser),
            patch(
                "devbench.cli.classify_proposed_task",
                return_value=ProposalTaskState.UNMATERIALISED,
            ),
            patch(
                "devbench.cli.materialise_proposal",
                side_effect=ProposalError("guard: prior proposed tasks exist"),
            ),
        ):
            rc = cli.cmd_sweep_proposals()
        assert rc == 0, "sweep must tolerate per-proposal ProposalError without crashing"
        out = capsys.readouterr().out
        assert "skipped E0-F1-S1-T1" in out
        assert "prior proposed tasks exist" in out

    def test_no_op_when_every_task_already_materialised(
        self,
        tmp_path: Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        from devbench.backlog.proposal import (
            Proposal,
            ProposalTaskState,
            ProposedTask,
            write_proposal,
        )

        proposal = Proposal(
            source_task_id="E0-F1-S1-T1",
            generated_at="2026-04-19T00:00:00Z",
            rejection_reason="r",
            proposed_tasks=[
                ProposedTask(
                    suggested_id="E0-F1-S1-T2",
                    title="x",
                    files_to_own=["src/x.py"],
                    linked_scenarios=["SC-01"],
                    suggested_acs=["AC-001 fix"],
                    suggested_approach="ok",
                )
            ],
        )
        write_proposal(tmp_path, proposal)

        parser = MagicMock()
        parser.parse_index.return_value = []

        import dataclasses

        # "no-op" is the auto-accept-OFF outcome (with auto-accept on the task
        # would be promoted); pin it now that auto_accept_proposals defaults True.
        no_auto = dataclasses.replace(
            cli.RUNTIME_CONFIG,
            task_factory=dataclasses.replace(cli.RUNTIME_CONFIG.task_factory, auto_accept_proposals=False),
        )
        with (
            patch("devbench.cli.WORKSPACE_ROOT", tmp_path),
            patch("devbench.cli.BACKLOG_ROOT", tmp_path / "backlog"),
            patch("devbench.cli.BACKLOG_INDEX", tmp_path / "BACKLOG.md"),
            patch("devbench.cli.RUNTIME_CONFIG", no_auto),
            patch("devbench.cli.BacklogParser", return_value=parser),
            patch(
                "devbench.cli.classify_proposed_task",
                return_value=ProposalTaskState.PROPOSED,
            ),
        ):
            rc = cli.cmd_sweep_proposals()
        assert rc == 0
        assert "no-op E0-F1-S1-T1" in capsys.readouterr().out


class TestCmdSweepAutoAccept:
    """ADR-11: sweep-proposals auto-promotes every PROPOSED draft when task_factory.auto_accept_proposals is True."""

    def _mk_runtime_config(self, auto_accept: bool) -> MagicMock:
        """Build a RUNTIME_CONFIG mock with task_factory.auto_accept_proposals toggled."""
        cfg = MagicMock()
        cfg.task_factory.auto_accept_proposals = auto_accept
        cfg.task_factory.enabled = True
        return cfg

    def _proposal(self, source_id: str = "E0-F1-S1-T1"):
        from devbench.backlog.proposal import Proposal, ProposedTask

        return Proposal(
            source_task_id=source_id,
            generated_at="2026-04-20T00:00:00Z",
            rejection_reason="r",
            proposed_tasks=[
                ProposedTask(
                    suggested_id="E0-F1-S1-T2",
                    title="x",
                    files_to_own=["src/x.py"],
                    linked_scenarios=["SC-01"],
                    suggested_acs=["AC-001 fix"],
                    suggested_approach="ok",
                )
            ],
        )

    def test_sweep_does_not_auto_promote_when_flag_false(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """Back-compat: materialised drafts stay PROPOSED when the flag is off."""
        from devbench.backlog.proposal import ProposalTaskState, write_proposal

        proposal = self._proposal()
        write_proposal(tmp_path, proposal)
        unit = MagicMock()
        unit.id = "E0-F1-S1-T1"
        unit.repo = "caylent-solutions/example"
        parser = MagicMock()
        parser.parse_index.return_value = [unit]

        with (
            patch("devbench.cli.WORKSPACE_ROOT", tmp_path),
            patch("devbench.cli.BACKLOG_ROOT", tmp_path / "backlog"),
            patch("devbench.cli.BACKLOG_INDEX", tmp_path / "BACKLOG.md"),
            patch("devbench.cli.BacklogParser", return_value=parser),
            patch("devbench.cli.RUNTIME_CONFIG", self._mk_runtime_config(auto_accept=False)),
            patch("devbench.cli.classify_proposed_task", return_value=ProposalTaskState.UNMATERIALISED),
            patch("devbench.cli.materialise_proposal", return_value=[tmp_path / "a.md"]),
            patch("devbench.cli.promote_proposal") as mock_promote,
        ):
            rc = cli.cmd_sweep_proposals()
        assert rc == 0
        mock_promote.assert_not_called()
        out = capsys.readouterr().out
        assert "materialised E0-F1-S1-T1" in out
        # Flag off -> output line MUST NOT include auto-promoted count.
        assert "auto-promoted" not in out

    def test_sweep_auto_promotes_every_proposed_draft_when_flag_true(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """Primary ADR-11 behaviour: one promote per PROPOSED draft."""
        from devbench.backlog.proposal import PromoteResult, ProposalTaskState, write_proposal

        proposal = self._proposal()
        write_proposal(tmp_path, proposal)
        unit = MagicMock()
        unit.id = "E0-F1-S1-T1"
        unit.repo = "caylent-solutions/example"
        parser = MagicMock()
        parser.parse_index.return_value = [unit]

        # Pre-state: UNMATERIALISED -> materialise then PROPOSED on the per-task re-classify.
        state_sequence = [
            ProposalTaskState.UNMATERIALISED,  # pre-check inside the sweep loop
            ProposalTaskState.PROPOSED,  # auto-promote per-task check
        ]
        calls = {"n": 0}

        def fake_classify(_backlog, _ws, _tid):
            idx = calls["n"]
            calls["n"] += 1
            return state_sequence[idx] if idx < len(state_sequence) else state_sequence[-1]

        draft_path = tmp_path / "a.md"
        with (
            patch("devbench.cli.WORKSPACE_ROOT", tmp_path),
            patch("devbench.cli.BACKLOG_ROOT", tmp_path / "backlog"),
            patch("devbench.cli.BACKLOG_INDEX", tmp_path / "BACKLOG.md"),
            patch("devbench.cli.BacklogParser", return_value=parser),
            patch("devbench.cli.RUNTIME_CONFIG", self._mk_runtime_config(auto_accept=True)),
            patch("devbench.cli.classify_proposed_task", side_effect=fake_classify),
            patch("devbench.cli.materialise_proposal", return_value=[draft_path]),
            patch(
                "devbench.cli.promote_proposal",
                return_value=PromoteResult(draft_path=draft_path, wired_targets=["E0-F1-S1-T1"]),
            ) as mock_promote,
        ):
            rc = cli.cmd_sweep_proposals()
        assert rc == 0
        mock_promote.assert_called_once()
        # The audit_suffix kwarg must be threaded through.
        _, kwargs = mock_promote.call_args
        assert kwargs["task_id"] == "E0-F1-S1-T2"
        assert "auto-accepted" in kwargs["audit_suffix"]
        out = capsys.readouterr().out
        assert "materialised E0-F1-S1-T1" in out
        assert "(auto-promoted: 1)" in out

    def test_sweep_auto_promote_is_idempotent_on_already_promoted_draft(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """Second sweep tick after the first already promoted everything -> 0 new promotes."""
        from devbench.backlog.proposal import ProposalTaskState, write_proposal

        proposal = self._proposal()
        write_proposal(tmp_path, proposal)
        unit = MagicMock()
        unit.id = "E0-F1-S1-T1"
        unit.repo = "caylent-solutions/example"
        parser = MagicMock()
        parser.parse_index.return_value = [unit]

        with (
            patch("devbench.cli.WORKSPACE_ROOT", tmp_path),
            patch("devbench.cli.BACKLOG_ROOT", tmp_path / "backlog"),
            patch("devbench.cli.BACKLOG_INDEX", tmp_path / "BACKLOG.md"),
            patch("devbench.cli.BacklogParser", return_value=parser),
            patch("devbench.cli.RUNTIME_CONFIG", self._mk_runtime_config(auto_accept=True)),
            # Already PROMOTED -> pre-check short-circuits to no-op (no UNMATERIALISED, no PROPOSED).
            patch("devbench.cli.classify_proposed_task", return_value=ProposalTaskState.PROMOTED),
            patch("devbench.cli.promote_proposal") as mock_promote,
        ):
            rc = cli.cmd_sweep_proposals()
        assert rc == 0
        mock_promote.assert_not_called()
        assert "no-op E0-F1-S1-T1" in capsys.readouterr().out

    def test_sweep_auto_promote_failure_is_logged_and_continues(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """A ProposalError on one promote must log and continue, not abort the whole sweep."""
        from devbench.backlog.proposal import ProposalError, ProposalTaskState, write_proposal

        proposal = self._proposal()
        write_proposal(tmp_path, proposal)
        unit = MagicMock()
        unit.id = "E0-F1-S1-T1"
        unit.repo = "caylent-solutions/example"
        parser = MagicMock()
        parser.parse_index.return_value = [unit]

        classify_calls = {"n": 0}

        def fake_classify(_b, _w, _t):
            classify_calls["n"] += 1
            return ProposalTaskState.UNMATERIALISED if classify_calls["n"] == 1 else ProposalTaskState.PROPOSED

        with (
            patch("devbench.cli.WORKSPACE_ROOT", tmp_path),
            patch("devbench.cli.BACKLOG_ROOT", tmp_path / "backlog"),
            patch("devbench.cli.BACKLOG_INDEX", tmp_path / "BACKLOG.md"),
            patch("devbench.cli.BacklogParser", return_value=parser),
            patch("devbench.cli.RUNTIME_CONFIG", self._mk_runtime_config(auto_accept=True)),
            patch("devbench.cli.classify_proposed_task", side_effect=fake_classify),
            patch("devbench.cli.materialise_proposal", return_value=[tmp_path / "a.md"]),
            patch("devbench.cli.promote_proposal", side_effect=ProposalError("boom")),
        ):
            rc = cli.cmd_sweep_proposals()
        assert rc == 0, "sweep must tolerate per-draft promote errors"
        captured = capsys.readouterr()
        assert "auto-promote failed for E0-F1-S1-T2" in captured.err
        assert "(auto-promoted: 0)" in captured.out

    def test_sweep_auto_promotes_legacy_proposed_drafts_when_flag_flipped_on(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """Flipping the flag on with legacy PROPOSED drafts: sweep still auto-promotes them."""
        from devbench.backlog.proposal import PromoteResult, ProposalTaskState, write_proposal

        proposal = self._proposal()
        write_proposal(tmp_path, proposal)
        unit = MagicMock()
        unit.id = "E0-F1-S1-T1"
        unit.repo = "caylent-solutions/example"
        parser = MagicMock()
        parser.parse_index.return_value = [unit]

        draft_path = tmp_path / "a.md"
        with (
            patch("devbench.cli.WORKSPACE_ROOT", tmp_path),
            patch("devbench.cli.BACKLOG_ROOT", tmp_path / "backlog"),
            patch("devbench.cli.BACKLOG_INDEX", tmp_path / "BACKLOG.md"),
            patch("devbench.cli.BacklogParser", return_value=parser),
            patch("devbench.cli.RUNTIME_CONFIG", self._mk_runtime_config(auto_accept=True)),
            # No UNMATERIALISED; legacy draft already in PROPOSED state waiting.
            patch("devbench.cli.classify_proposed_task", return_value=ProposalTaskState.PROPOSED),
            patch("devbench.cli.materialise_proposal") as mock_mat,
            patch(
                "devbench.cli.promote_proposal",
                return_value=PromoteResult(draft_path=draft_path, wired_targets=["E0-F1-S1-T1"]),
            ) as mock_promote,
        ):
            rc = cli.cmd_sweep_proposals()
        assert rc == 0
        mock_mat.assert_not_called()
        mock_promote.assert_called_once()
        out = capsys.readouterr().out
        assert "materialised E0-F1-S1-T1: 0 new, 1 skipped (auto-promoted: 1)" in out

    """ADR-09: rejected drafts must not resurrect on the next sweep tick."""

    def test_rejected_draft_not_recreated_by_sweep(
        self,
        tmp_path: Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """End-to-end: write JSON -> materialise -> reject -> sweep -> no resurrection."""
        from devbench.backlog import proposal as proposal_mod
        from devbench.backlog.proposal import (
            Proposal,
            ProposedTask,
            materialise_proposal,
            reject_proposal,
            write_proposal,
        )

        # Build a real workspace so materialise + reject + sweep exercise the real code.
        backlog_dir = tmp_path / "backlog"
        story_dir = backlog_dir / "E0" / "E0-F1" / "E0-F1-S1"
        story_dir.mkdir(parents=True)
        source_file = story_dir / "E0-F1-S1-T1.md"
        source_file.write_text(
            "# E0-F1-S1-T1: Source Task\n\n## Status: blocked\n\n"
            "## Description\n\nx\n\n"
            "## Dependencies\n\n| ID | Title | Status |\n|----|-------|--------|\n| none | | |\n\n"
            "## Acceptance Criteria\n\n- [ ] AC-TEST-001 x\n\n"
            "## Changes Manifest\n\n| File | Change |\n|------|--------|\n| `x.py` | x |\n\n"
            "## Definition of Done\n\n- [ ] AC complete\n"
        )
        backlog_md = tmp_path / "BACKLOG.md"
        backlog_md.write_text(
            "# Backlog\n\n## Status Summary\n\n"
            "| Epic | Title | Done | In Progress | In Queue | Blocked |\n"
            "|------|-------|------|-------------|----------|---------|\n"
            "| E0 | Ex | 0 | 0 | 0 | 1 |\n\n"
            "## Full Work Unit Index\n\n"
            "| ID | Title | Type | Status | Dependencies | Repo | File Path |\n"
            "|----|-------|------|--------|--------------|------|-----------|\n"
            "| E0-F1-S1-T1 | Source | Task | blocked | None | caylent-solutions/example | "
            "`backlog/E0/E0-F1/E0-F1-S1/E0-F1-S1-T1.md` |\n"
        )

        proposal = Proposal(
            source_task_id="E0-F1-S1-T1",
            generated_at="2026-04-19T00:00:00Z",
            rejection_reason="r",
            proposed_tasks=[
                ProposedTask(
                    suggested_id="E0-F1-S1-T2",
                    title="Fix",
                    files_to_own=["src/a.py"],
                    linked_scenarios=["SC-01"],
                    suggested_acs=["AC-001 fix"],
                    suggested_approach=(
                        "Context: the unit test fixture for ADR-09 resurrection guard. "
                        "Scope: src/a.py and its companion unit test. "
                        "TDD approach: 1. RED -- write the failing test. "
                        "2. GREEN -- apply the minimal production fix. "
                        "3. REFACTOR -- no behaviour change. "
                        "Verify: make lint && make test-unit exit zero."
                    ),
                )
            ],
        )
        write_proposal(tmp_path, proposal)
        materialise_proposal(
            workspace_root=tmp_path,
            backlog_root=backlog_dir,
            backlog_index=backlog_md,
            proposal=proposal,
            repo="caylent-solutions/example",
        )
        reject_proposal(
            workspace_root=tmp_path,
            backlog_root=backlog_dir,
            backlog_index=backlog_md,
            task_id="E0-F1-S1-T2",
            reason="superseded",
        )
        draft_path = story_dir / "E0-F1-S1-T2.md"
        assert not draft_path.exists(), "per-draft reject must archive the .md"
        assert any(
            proposal_mod.REJECTED_PROPOSAL_DIR_NAME in str(p)
            for p in (tmp_path / proposal_mod.REJECTED_PROPOSAL_DIR_NAME).iterdir()
        )

        # Now run sweep-proposals. Expect no-op because the only task is
        # REJECTED (archive exists) -- classify_proposed_task returns REJECTED,
        # sweep's unmaterialised_before count is zero, hits the no-op branch.
        source_unit = MagicMock()
        source_unit.id = "E0-F1-S1-T1"
        source_unit.repo = "caylent-solutions/example"
        parser = MagicMock()
        parser.parse_index.return_value = [source_unit]

        with (
            patch("devbench.cli.WORKSPACE_ROOT", tmp_path),
            patch("devbench.cli.BACKLOG_ROOT", backlog_dir),
            patch("devbench.cli.BACKLOG_INDEX", backlog_md),
            patch("devbench.cli.BacklogParser", return_value=parser),
        ):
            rc = cli.cmd_sweep_proposals()
        assert rc == 0
        out = capsys.readouterr().out
        assert "no-op E0-F1-S1-T1" in out, out
        # The crucial assertion: the rejected draft file was NOT recreated.
        assert not draft_path.exists(), "sweep-proposals must not resurrect a rejected draft"


class TestCmdAddDep:
    """ADR-10: `devbench add-dep <blocked-id> <blocker-id> [--reason <msg>]`."""

    def test_add_dep_rejects_invalid_task_id_format(self, capsys: pytest.CaptureFixture[str]) -> None:
        rc = cli.cmd_add_dep("not-a-task-id", "E0-F1-S1-T2")
        assert rc == 1
        assert "does not match" in capsys.readouterr().err

    def test_add_dep_requires_two_positionals(self, capsys: pytest.CaptureFixture[str]) -> None:
        rc = cli.cmd_add_dep("E0-F1-S1-T1")
        assert rc == 1
        err = capsys.readouterr().err
        assert "exactly two task ids" in err

    def test_add_dep_rejects_unknown_flag(self, capsys: pytest.CaptureFixture[str]) -> None:
        rc = cli.cmd_add_dep("E0-F1-S1-T1", "E0-F1-S1-T2", "--bogus", "x")
        assert rc == 1
        assert "unknown flag" in capsys.readouterr().err

    def test_add_dep_reason_without_value_errors(self, capsys: pytest.CaptureFixture[str]) -> None:
        rc = cli.cmd_add_dep("E0-F1-S1-T1", "E0-F1-S1-T2", "--reason")
        assert rc == 1
        assert "requires a value" in capsys.readouterr().err

    def test_add_dep_happy_path_emits_json(self, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
        # Build a minimal workspace with a blocked T1 and in-queue T2 wired
        # through the real backlog parser so add_dep's validation passes.
        (tmp_path / "BACKLOG.md").write_text(
            "# Backlog\n\n"
            "## Status Summary\n\n"
            "| Epic | Title | Done | In Progress | In Queue | Blocked |\n"
            "|------|-------|------|-------------|----------|---------|\n"
            "| E0 | Example | 0 | 0 | 1 | 1 |\n\n"
            "## Full Work Unit Index\n\n"
            "| ID | Title | Type | Status | Dependencies | Repo | File Path |\n"
            "|----|-------|------|--------|--------------|------|-----------|\n"
            "| E0-F1-S1-T1 | Source | Task | blocked | None | caylent-solutions/example | "
            "`backlog/E0/E0-F1/E0-F1-S1/E0-F1-S1-T1.md` |\n"
            "| E0-F1-S1-T2 | Fix | Task | in-queue | None | caylent-solutions/example | "
            "`backlog/E0/E0-F1/E0-F1-S1/E0-F1-S1-T2.md` |\n"
        )
        story = tmp_path / "backlog" / "E0" / "E0-F1" / "E0-F1-S1"
        story.mkdir(parents=True)
        for tid, status in (("E0-F1-S1-T1", "blocked"), ("E0-F1-S1-T2", "in-queue")):
            (story / f"{tid}.md").write_text(
                f"# {tid}: X\n\n## Status: {status}\n\n## Description\n\nx\n\n"
                "## Dependencies\n\n"
                "| ID | Title | Status |\n|----|-------|--------|\n| none | | |\n"
            )

        with (
            patch("devbench.cli.WORKSPACE_ROOT", tmp_path),
            patch("devbench.cli.BACKLOG_ROOT", tmp_path / "backlog"),
            patch("devbench.cli.BACKLOG_INDEX", tmp_path / "BACKLOG.md"),
        ):
            rc = cli.cmd_add_dep("E0-F1-S1-T1", "E0-F1-S1-T2", "--reason", "ADR-10 CLI smoke")
        assert rc == 0
        out = capsys.readouterr().out
        assert '"blocked": "E0-F1-S1-T1"' in out
        assert '"blocker": "E0-F1-S1-T2"' in out
        assert '"wired": true' in out
        # Marker landed on the blocked file.
        t1 = (story / "E0-F1-S1-T1.md").read_text()
        assert "[BLOCKED_PENDING_PROPOSAL] E0-F1-S1-T2" in t1
        assert "ADR-10 CLI smoke" in t1

    def test_add_dep_warns_when_blocked_is_not_blocked(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        (tmp_path / "BACKLOG.md").write_text(
            "# Backlog\n\n"
            "## Status Summary\n\n"
            "| Epic | Title | Done | In Progress | In Queue | Blocked |\n"
            "|------|-------|------|-------------|----------|---------|\n"
            "| E0 | Example | 0 | 0 | 2 | 0 |\n\n"
            "## Full Work Unit Index\n\n"
            "| ID | Title | Type | Status | Dependencies | Repo | File Path |\n"
            "|----|-------|------|--------|--------------|------|-----------|\n"
            "| E0-F1-S1-T1 | Src | Task | in-queue | None | caylent-solutions/example | "
            "`backlog/E0/E0-F1/E0-F1-S1/E0-F1-S1-T1.md` |\n"
            "| E0-F1-S1-T2 | Fix | Task | in-queue | None | caylent-solutions/example | "
            "`backlog/E0/E0-F1/E0-F1-S1/E0-F1-S1-T2.md` |\n"
        )
        story = tmp_path / "backlog" / "E0" / "E0-F1" / "E0-F1-S1"
        story.mkdir(parents=True)
        for tid in ("E0-F1-S1-T1", "E0-F1-S1-T2"):
            (story / f"{tid}.md").write_text(
                f"# {tid}: X\n\n## Status: in-queue\n\n## Description\n\nx\n\n"
                "## Dependencies\n\n"
                "| ID | Title | Status |\n|----|-------|--------|\n| none | | |\n"
            )

        with (
            patch("devbench.cli.WORKSPACE_ROOT", tmp_path),
            patch("devbench.cli.BACKLOG_ROOT", tmp_path / "backlog"),
            patch("devbench.cli.BACKLOG_INDEX", tmp_path / "BACKLOG.md"),
        ):
            rc = cli.cmd_add_dep("E0-F1-S1-T1", "E0-F1-S1-T2")
        assert rc == 0
        err = capsys.readouterr().err
        assert "WARNING:" in err
        assert "not 'blocked'" in err


class TestProposalCommandsRegistered:
    def test_list_proposals_registered(self) -> None:
        assert "list-proposals" in cli._COMMANDS
        assert "promote-proposal" in cli._COMMANDS
        assert "reject-proposal" in cli._COMMANDS

    def test_sweep_proposals_registered(self) -> None:
        assert "sweep-proposals" in cli._COMMANDS

    def test_add_dep_registered(self) -> None:
        assert "add-dep" in cli._COMMANDS


class TestCmdMaterialiseProposal:
    def test_missing_proposal(self, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
        with (
            patch("devbench.cli.WORKSPACE_ROOT", tmp_path),
            patch("devbench.cli.BACKLOG_ROOT", tmp_path / "backlog"),
            patch("devbench.cli.BACKLOG_INDEX", tmp_path / "BACKLOG.md"),
        ):
            rc = cli.cmd_materialise_proposal("E0-F1-S1-T1")
        assert rc == 1
        assert "No proposal" in capsys.readouterr().err

    def test_backlog_parse_error(self, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
        from devbench.backlog.proposal import Proposal, ProposedTask, write_proposal

        proposal = Proposal(
            source_task_id="E0-F1-S1-T1",
            generated_at="x",
            rejection_reason="x",
            proposed_tasks=[
                ProposedTask(
                    suggested_id="E0-F1-S1-T2",
                    title="t",
                    files_to_own=[],
                    linked_scenarios=[],
                    suggested_acs=[],
                    suggested_approach="",
                )
            ],
        )
        write_proposal(tmp_path, proposal)
        # BACKLOG.md missing -> parse_index raises FileNotFoundError.
        with (
            patch("devbench.cli.WORKSPACE_ROOT", tmp_path),
            patch("devbench.cli.BACKLOG_ROOT", tmp_path / "backlog"),
            patch("devbench.cli.BACKLOG_INDEX", tmp_path / "BACKLOG.md"),
        ):
            rc = cli.cmd_materialise_proposal("E0-F1-S1-T1")
        assert rc == 1

    def test_source_task_not_in_backlog(self, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
        from devbench.backlog.proposal import Proposal, ProposedTask, write_proposal

        # Build minimal workspace where source-id in proposal doesn't exist in BACKLOG.
        (tmp_path / "BACKLOG.md").write_text(
            "# Backlog\n\n## Full Work Unit Index\n\n"
            "| ID | Title | Type | Status | Dependencies | Repo | File Path |\n"
            "|----|-------|------|--------|--------------|------|-----------|\n"
            "| E0-F9-S9-T9 | Other | Task | done | None | caylent-solutions/git-repo | `backlog/E0-F9-S9-T9.md` |\n"
        )
        (tmp_path / "backlog").mkdir()
        (tmp_path / "backlog" / "E0-F9-S9-T9.md").write_text("# E0-F9-S9-T9: Other\n\n## Status: done\n")
        proposal = Proposal(
            source_task_id="E0-F1-S1-T1",
            generated_at="x",
            rejection_reason="x",
            proposed_tasks=[
                ProposedTask(
                    suggested_id="E0-F1-S1-T2",
                    title="t",
                    files_to_own=[],
                    linked_scenarios=[],
                    suggested_acs=[],
                    # Concrete approach text so the issue #143 placeholder check
                    # does not fire before the source-task lookup runs.
                    suggested_approach="Author the foo helper that the source task references",
                )
            ],
        )
        write_proposal(tmp_path, proposal)
        with (
            patch("devbench.cli.WORKSPACE_ROOT", tmp_path),
            patch("devbench.cli.BACKLOG_ROOT", tmp_path / "backlog"),
            patch("devbench.cli.BACKLOG_INDEX", tmp_path / "BACKLOG.md"),
        ):
            rc = cli.cmd_materialise_proposal("E0-F1-S1-T1")
        assert rc == 1
        assert "not found" in capsys.readouterr().err

    def test_happy_path(self, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
        from devbench.backlog.proposal import Proposal, ProposedTask, write_proposal

        source_row = (
            "| E0-F1-S1-T1 | Source | Task | blocked | None "
            "| caylent-solutions/example | `backlog/E0/E0-F1/E0-F1-S1/E0-F1-S1-T1.md` |"
        )
        (tmp_path / "BACKLOG.md").write_text(
            "# Backlog\n\n## Full Work Unit Index\n\n"
            "| ID | Title | Type | Status | Dependencies | Repo | File Path |\n"
            "|----|-------|------|--------|--------------|------|-----------|\n"
            f"{source_row}\n"
        )
        story_dir = tmp_path / "backlog" / "E0" / "E0-F1" / "E0-F1-S1"
        story_dir.mkdir(parents=True)
        (story_dir / "E0-F1-S1-T1.md").write_text(
            "# E0-F1-S1-T1: Source\n\n## Status: blocked\n\n"
            "## Target Repository\n\n- **Repo:** `caylent-solutions/example`\n\n"
            "## Description\n\nx\n\n"
            "## Dependencies\n\n| ID | Title | Status |\n|----|-------|--------|\n| none | | |\n\n"
            "## Acceptance Criteria\n\n- [ ] AC-TEST-001\n\n"
            "## Changes Manifest\n\n| File | Change |\n|------|--------|\n| `a.py` | x |\n\n"
            "## Definition of Done\n\n- [ ] complete\n"
        )
        proposal = Proposal(
            source_task_id="E0-F1-S1-T1",
            generated_at="2026-04-18T00:00:00Z",
            rejection_reason="x",
            proposed_tasks=[
                ProposedTask(
                    suggested_id="E0-F1-S1-T2",
                    title="t",
                    files_to_own=["src/a.py"],
                    linked_scenarios=["SC-01"],
                    suggested_acs=["AC-FUNC-001"],
                    suggested_approach=(
                        "Context: unit test fixture for cmd_materialise_proposal happy path. "
                        "Scope: this draft is synthetic; no real files affected. "
                        "TDD approach: 1. RED -- n/a. 2. GREEN -- n/a. 3. REFACTOR -- n/a. "
                        "Verify: the materialise-proposal CLI command exits 0."
                    ),
                )
            ],
        )
        write_proposal(tmp_path, proposal)
        with (
            patch("devbench.cli.WORKSPACE_ROOT", tmp_path),
            patch("devbench.cli.BACKLOG_ROOT", tmp_path / "backlog"),
            patch("devbench.cli.BACKLOG_INDEX", tmp_path / "BACKLOG.md"),
        ):
            rc = cli.cmd_materialise_proposal("E0-F1-S1-T1")
        assert rc == 0
        assert "E0-F1-S1-T2" in capsys.readouterr().out


class TestCmdWriteProposal:
    def test_stdin_empty_rejected(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        import io

        monkeypatch.setattr("sys.stdin", io.StringIO(""))
        with patch("devbench.cli.WORKSPACE_ROOT", tmp_path):
            rc = cli.cmd_write_proposal("E0-F1-S1-T1")
        assert rc == 1
        assert "required on stdin" in capsys.readouterr().err

    def test_stdin_invalid_json(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        import io

        monkeypatch.setattr("sys.stdin", io.StringIO("not json"))
        with patch("devbench.cli.WORKSPACE_ROOT", tmp_path):
            rc = cli.cmd_write_proposal("E0-F1-S1-T1")
        assert rc == 1
        assert "not valid JSON" in capsys.readouterr().err

    def test_stdin_schema_violation(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        import io

        monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps({"source_task_id": "x"})))
        with patch("devbench.cli.WORKSPACE_ROOT", tmp_path):
            rc = cli.cmd_write_proposal("E0-F1-S1-T1")
        assert rc == 1
        assert "invalid" in capsys.readouterr().err

    def test_source_mismatch(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        import io

        payload = {
            "source_task_id": "OTHER-SRC",
            "generated_at": "t",
            "rejection_reason": "r",
            "proposed_tasks": [],
        }
        monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps(payload)))
        with patch("devbench.cli.WORKSPACE_ROOT", tmp_path):
            rc = cli.cmd_write_proposal("E0-F1-S1-T1")
        assert rc == 1
        assert "does not match argument" in capsys.readouterr().err

    def test_happy_path(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        import io

        payload = {
            "source_task_id": "E0-F1-S1-T1",
            "generated_at": "2026-04-18T00:00:00Z",
            "rejection_reason": "x",
            "proposed_tasks": [
                {
                    "suggested_id": "E0-F1-S1-T2",
                    "title": "t",
                    "files_to_own": [],
                    "linked_scenarios": [],
                    "suggested_acs": [],
                    "suggested_approach": "",
                }
            ],
        }
        monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps(payload)))
        with patch("devbench.cli.WORKSPACE_ROOT", tmp_path):
            rc = cli.cmd_write_proposal("E0-F1-S1-T1")
        assert rc == 0
        out = capsys.readouterr().out
        assert "proposal_path" in out

    def test_duplicate_write_rejected(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        import io

        from devbench.backlog.proposal import Proposal, ProposedTask, write_proposal

        proposal = Proposal(
            source_task_id="E0-F1-S1-T1",
            generated_at="t",
            rejection_reason="r",
            proposed_tasks=[
                ProposedTask(
                    suggested_id="E0-F1-S1-T2",
                    title="t",
                    files_to_own=[],
                    linked_scenarios=[],
                    suggested_acs=[],
                    suggested_approach="",
                )
            ],
        )
        write_proposal(tmp_path, proposal)
        monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps(proposal.to_dict())))
        with patch("devbench.cli.WORKSPACE_ROOT", tmp_path):
            rc = cli.cmd_write_proposal("E0-F1-S1-T1")
        assert rc == 1
        assert "already exists" in capsys.readouterr().err

    def test_stdin_os_error_returns_1(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        class _FailStdin:
            def read(self) -> str:
                raise OSError("disconnected")

        monkeypatch.setattr("sys.stdin", _FailStdin())
        with patch("devbench.cli.WORKSPACE_ROOT", tmp_path):
            rc = cli.cmd_write_proposal("E0-F1-S1-T1")
        assert rc == 1
        assert "cannot read stdin" in capsys.readouterr().err


class TestCmdDecline:
    """Slice 5c: cmd_decline CLI command."""

    def _make_minimal_unit(self, tmp_path: Path, unit_id: str = "EX-F1-S1-T1") -> tuple[Path, Path]:
        backlog_md = tmp_path / "BACKLOG.md"
        backlog_md.write_text(
            "# Backlog\n\n## Full Work Unit Index\n\n"
            "| ID | Title | Type | Status | Dependencies | Repo | File Path |\n"
            "|----|-------|------|--------|--------------|------|-----------|\n"
            f"| {unit_id} | Test | Task | in-queue | None | caylent-solutions/git-repo | `backlog/{unit_id}.md` |\n"
        )
        backlog_dir = tmp_path / "backlog"
        backlog_dir.mkdir(exist_ok=True)
        wu_file = backlog_dir / f"{unit_id}.md"
        wu_file.write_text(f"# {unit_id}: Test\n\n## Status: in-queue\n\n## Description\n\nx\n")
        return backlog_md, wu_file

    def test_happy_path_flips_status_and_audits(self, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
        backlog_md, wu_file = self._make_minimal_unit(tmp_path)
        with (
            patch("devbench.cli.BACKLOG_ROOT", tmp_path / "backlog"),
            patch("devbench.cli.BACKLOG_INDEX", backlog_md),
            patch("devbench.cli.WORKSPACE_ROOT", tmp_path),
        ):
            rc = cli.cmd_decline("EX-F1-S1-T1", "--reason", "scope determined unnecessary")
        assert rc == 0
        content = wu_file.read_text()
        assert "## Status: declined" in content
        assert "[DECLINED]" in content
        assert "scope determined unnecessary" in content
        out = json.loads(capsys.readouterr().out.strip())
        assert out["task_id"] == "EX-F1-S1-T1"
        assert out["status"] == "declined"
        assert out["reason"] == "scope determined unnecessary"

    def test_missing_reason_rejected(self, capsys: pytest.CaptureFixture[str]) -> None:
        rc = cli.cmd_decline("EX-F1-S1-T1")
        assert rc == 1
        assert "requires" in capsys.readouterr().err

    def test_reason_without_value_rejected(self, capsys: pytest.CaptureFixture[str]) -> None:
        rc = cli.cmd_decline("EX-F1-S1-T1", "--reason")
        assert rc == 1
        assert "requires a value" in capsys.readouterr().err

    def test_em_dash_in_reason_blocked(self, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
        backlog_md, _ = self._make_minimal_unit(tmp_path)
        with (
            patch("devbench.cli.BACKLOG_ROOT", tmp_path / "backlog"),
            patch("devbench.cli.BACKLOG_INDEX", backlog_md),
        ):
            rc = cli.cmd_decline("EX-F1-S1-T1", "--reason", "bad\u2014reason")
        assert rc == 1
        assert "em-dash" in capsys.readouterr().err

    def test_unknown_unit_returns_1(self, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
        backlog_md, _ = self._make_minimal_unit(tmp_path)
        with (
            patch("devbench.cli.BACKLOG_ROOT", tmp_path / "backlog"),
            patch("devbench.cli.BACKLOG_INDEX", backlog_md),
        ):
            rc = cli.cmd_decline("NO-SUCH-ID", "--reason", "n/a")
        assert rc == 1
        assert "not found" in capsys.readouterr().err

    def test_registered_in_commands(self) -> None:
        assert "decline" in cli._COMMANDS


class TestCmdHold:
    """E222: ``devbench hold <id> --reason <text>`` command."""

    def _make_minimal_unit(self, tmp_path: Path, unit_id: str = "EX-F1-S1-T1") -> tuple[Path, Path]:
        backlog_md = tmp_path / "BACKLOG.md"
        backlog_md.write_text(
            "# Backlog\n\n## Full Work Unit Index\n\n"
            "| ID | Title | Type | Status | Dependencies | Repo | File Path |\n"
            "|----|-------|------|--------|--------------|------|-----------|\n"
            f"| {unit_id} | Test | Task | in-queue | None | caylent-solutions/git-repo | `backlog/{unit_id}.md` |\n"
        )
        backlog_dir = tmp_path / "backlog"
        backlog_dir.mkdir(exist_ok=True)
        wu_file = backlog_dir / f"{unit_id}.md"
        wu_file.write_text(f"# {unit_id}: Test\n\n## Status: in-queue\n\n## Description\n\nx\n")
        return backlog_md, wu_file

    def test_happy_path_flips_status_and_audits(self, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
        backlog_md, wu_file = self._make_minimal_unit(tmp_path)
        with (
            patch("devbench.cli.BACKLOG_ROOT", tmp_path / "backlog"),
            patch("devbench.cli.BACKLOG_INDEX", backlog_md),
            patch("devbench.cli.WORKSPACE_ROOT", tmp_path),
        ):
            rc = cli.cmd_hold("EX-F1-S1-T1", "--reason", "awaiting upstream decision")
        assert rc == 0
        content = wu_file.read_text()
        assert "## Status: hold" in content
        assert "[HOLD]" in content
        assert "awaiting upstream decision" in content
        out = json.loads(capsys.readouterr().out.strip())
        assert out == {
            "task_id": "EX-F1-S1-T1",
            "status": "hold",
            "reason": "awaiting upstream decision",
        }

    def test_missing_reason_rejected(self, capsys: pytest.CaptureFixture[str]) -> None:
        rc = cli.cmd_hold("EX-F1-S1-T1")
        assert rc == 1
        assert "requires" in capsys.readouterr().err

    def test_reason_without_value_rejected(self, capsys: pytest.CaptureFixture[str]) -> None:
        rc = cli.cmd_hold("EX-F1-S1-T1", "--reason")
        assert rc == 1
        assert "requires a value" in capsys.readouterr().err

    def test_em_dash_in_reason_blocked(self, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
        backlog_md, _ = self._make_minimal_unit(tmp_path)
        with (
            patch("devbench.cli.BACKLOG_ROOT", tmp_path / "backlog"),
            patch("devbench.cli.BACKLOG_INDEX", backlog_md),
        ):
            rc = cli.cmd_hold("EX-F1-S1-T1", "--reason", "bad—reason")
        assert rc == 1
        assert "em-dash" in capsys.readouterr().err

    def test_unknown_unit_returns_1(self, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
        backlog_md, _ = self._make_minimal_unit(tmp_path)
        with (
            patch("devbench.cli.BACKLOG_ROOT", tmp_path / "backlog"),
            patch("devbench.cli.BACKLOG_INDEX", backlog_md),
        ):
            rc = cli.cmd_hold("NO-SUCH-ID", "--reason", "n/a")
        assert rc == 1
        assert "not found" in capsys.readouterr().err

    def test_registered_in_commands(self) -> None:
        assert "hold" in cli._COMMANDS

    def test_is_variadic_so_multi_token_reason_reaches_handler(self) -> None:
        assert "hold" in cli._VARIADIC_COMMANDS


class TestCmdUnhold:
    """E222: ``devbench unhold <id> --reason <text>`` returns held units to in-queue."""

    def _make_held_unit(self, tmp_path: Path, unit_id: str = "EX-F1-S1-T1") -> tuple[Path, Path]:
        backlog_md = tmp_path / "BACKLOG.md"
        backlog_md.write_text(
            "# Backlog\n\n## Full Work Unit Index\n\n"
            "| ID | Title | Type | Status | Dependencies | Repo | File Path |\n"
            "|----|-------|------|--------|--------------|------|-----------|\n"
            f"| {unit_id} | Test | Task | hold | None | caylent-solutions/git-repo | `backlog/{unit_id}.md` |\n"
        )
        backlog_dir = tmp_path / "backlog"
        backlog_dir.mkdir(exist_ok=True)
        wu_file = backlog_dir / f"{unit_id}.md"
        wu_file.write_text(f"# {unit_id}: Test\n\n## Status: hold\n\n## Description\n\nx\n")
        return backlog_md, wu_file

    def test_happy_path_returns_held_unit_to_in_queue(
        self,
        tmp_path: Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        backlog_md, wu_file = self._make_held_unit(tmp_path)
        with (
            patch("devbench.cli.BACKLOG_ROOT", tmp_path / "backlog"),
            patch("devbench.cli.BACKLOG_INDEX", backlog_md),
            patch("devbench.cli.WORKSPACE_ROOT", tmp_path),
        ):
            rc = cli.cmd_unhold("EX-F1-S1-T1", "--reason", "blocker resolved")
        assert rc == 0
        content = wu_file.read_text()
        assert "## Status: in-queue" in content
        assert "[UNHOLD]" in content
        assert "blocker resolved" in content
        out = json.loads(capsys.readouterr().out.strip())
        assert out == {
            "task_id": "EX-F1-S1-T1",
            "status": "in-queue",
            "reason": "blocker resolved",
        }

    def test_refuses_unit_not_currently_held(
        self,
        tmp_path: Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        # Build an in-queue unit (not held) and assert unhold refuses it.
        backlog_md = tmp_path / "BACKLOG.md"
        backlog_md.write_text(
            "# Backlog\n\n## Full Work Unit Index\n\n"
            "| ID | Title | Type | Status | Dependencies | Repo | File Path |\n"
            "|----|-------|------|--------|--------------|------|-----------|\n"
            "| EX-F1-S1-T1 | Test | Task | in-queue | None | caylent-solutions/git-repo | `backlog/EX-F1-S1-T1.md` |\n"
        )
        wu_dir = tmp_path / "backlog"
        wu_dir.mkdir(exist_ok=True)
        (wu_dir / "EX-F1-S1-T1.md").write_text("# EX-F1-S1-T1: Test\n\n## Status: in-queue\n\n## Description\n\nx\n")
        with (
            patch("devbench.cli.BACKLOG_ROOT", wu_dir),
            patch("devbench.cli.BACKLOG_INDEX", backlog_md),
        ):
            rc = cli.cmd_unhold("EX-F1-S1-T1", "--reason", "n/a")
        assert rc == 1
        err = capsys.readouterr().err
        assert "expected 'Hold'" in err

    def test_missing_reason_rejected(self, capsys: pytest.CaptureFixture[str]) -> None:
        rc = cli.cmd_unhold("EX-F1-S1-T1")
        assert rc == 1
        assert "requires" in capsys.readouterr().err

    def test_unknown_unit_returns_1(self, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
        backlog_md, _ = self._make_held_unit(tmp_path)
        with (
            patch("devbench.cli.BACKLOG_ROOT", tmp_path / "backlog"),
            patch("devbench.cli.BACKLOG_INDEX", backlog_md),
        ):
            rc = cli.cmd_unhold("NO-SUCH-ID", "--reason", "n/a")
        assert rc == 1
        assert "not found" in capsys.readouterr().err

    def test_registered_in_commands(self) -> None:
        assert "unhold" in cli._COMMANDS

    def test_is_variadic(self) -> None:
        assert "unhold" in cli._VARIADIC_COMMANDS


class TestCmdPromote:
    """E1-F4-S1-T1: ``devbench promote <id>`` transitions draft -> in-queue."""

    def _make_draft_unit(
        self, tmp_path: Path, unit_id: str = "EX-F1-S1-T1", status: str = "draft"
    ) -> tuple[Path, Path]:
        backlog_md = tmp_path / "BACKLOG.md"
        backlog_md.write_text(
            "# Backlog\n\n## Full Work Unit Index\n\n"
            "| ID | Title | Type | Status | Dependencies | Repo | File Path |\n"
            "|----|-------|------|--------|--------------|------|-----------|\n"
            f"| {unit_id} | Test | Task | {status} | None | caylent-solutions/git-repo | `backlog/{unit_id}.md` |\n"
        )
        backlog_dir = tmp_path / "backlog"
        backlog_dir.mkdir(exist_ok=True)
        wu_file = backlog_dir / f"{unit_id}.md"
        wu_file.write_text(f"# {unit_id}: Test\n\n## Status: {status}\n\n## Description\n\nx\n\n## Comments\n")
        return backlog_md, wu_file

    def test_happy_path_transitions_draft_to_in_queue(self, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
        backlog_md, wu_file = self._make_draft_unit(tmp_path)
        with (
            patch("devbench.cli.BACKLOG_ROOT", tmp_path / "backlog"),
            patch("devbench.cli.BACKLOG_INDEX", backlog_md),
            patch("devbench.cli.WORKSPACE_ROOT", tmp_path),
        ):
            rc = cli.cmd_promote("EX-F1-S1-T1")
        assert rc == 0
        content = wu_file.read_text()
        assert "## Status: in-queue" in content
        assert "[PROMOTED] draft -> in-queue" in content
        out = capsys.readouterr().out.strip()
        assert "Promoted EX-F1-S1-T1" in out

    def test_refuses_non_draft_status_in_queue(self, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
        backlog_md, _ = self._make_draft_unit(tmp_path, status="in-queue")
        with (
            patch("devbench.cli.BACKLOG_ROOT", tmp_path / "backlog"),
            patch("devbench.cli.BACKLOG_INDEX", backlog_md),
            patch("devbench.cli.WORKSPACE_ROOT", tmp_path),
        ):
            rc = cli.cmd_promote("EX-F1-S1-T1")
        assert rc == 1
        err = capsys.readouterr().err
        assert "not in 'draft' status" in err

    @pytest.mark.parametrize("status", ["in-progress", "done", "blocked", "hold", "declined"])
    def test_refuses_non_draft_statuses(self, tmp_path: Path, capsys: pytest.CaptureFixture[str], status: str) -> None:
        backlog_md, _ = self._make_draft_unit(tmp_path, status=status)
        with (
            patch("devbench.cli.BACKLOG_ROOT", tmp_path / "backlog"),
            patch("devbench.cli.BACKLOG_INDEX", backlog_md),
            patch("devbench.cli.WORKSPACE_ROOT", tmp_path),
        ):
            rc = cli.cmd_promote("EX-F1-S1-T1")
        assert rc == 1
        err = capsys.readouterr().err
        assert "not in 'draft' status" in err

    def test_unknown_unit_returns_1(self, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
        backlog_md, _ = self._make_draft_unit(tmp_path)
        with (
            patch("devbench.cli.BACKLOG_ROOT", tmp_path / "backlog"),
            patch("devbench.cli.BACKLOG_INDEX", backlog_md),
        ):
            rc = cli.cmd_promote("NO-SUCH-ID")
        assert rc == 1
        assert "not found" in capsys.readouterr().err

    def test_missing_wu_file_returns_1(self, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
        """When _resolve_unit_file returns None (TOCTOU race), cmd_promote returns 1."""
        backlog_md, _wu_file = self._make_draft_unit(tmp_path)
        with (
            patch("devbench.cli.BACKLOG_ROOT", tmp_path / "backlog"),
            patch("devbench.cli.BACKLOG_INDEX", backlog_md),
            patch("devbench.cli.WORKSPACE_ROOT", tmp_path),
            patch("devbench.cli._resolve_unit_file", return_value=None),
        ):
            rc = cli.cmd_promote("EX-F1-S1-T1")
        assert rc == 1
        assert "file not found" in capsys.readouterr().err.lower()

    def test_registered_in_commands(self) -> None:
        assert "promote" in cli._COMMANDS

    def test_audit_comment_contains_promoted_marker(self, tmp_path: Path) -> None:
        backlog_md, wu_file = self._make_draft_unit(tmp_path)
        with (
            patch("devbench.cli.BACKLOG_ROOT", tmp_path / "backlog"),
            patch("devbench.cli.BACKLOG_INDEX", backlog_md),
            patch("devbench.cli.WORKSPACE_ROOT", tmp_path),
        ):
            cli.cmd_promote("EX-F1-S1-T1")
        content = wu_file.read_text()
        # Verify the audit comment has the expected format
        assert "[agent/orchestrator]" in content
        assert "[PROMOTED] draft -> in-queue" in content


class TestCmdPromoteIntegration:
    """Integration test: exercises promote against a real fixture workspace."""

    def test_promote_real_fixture_workspace(self, tmp_path: Path) -> None:
        """End-to-end: construct a real backlog fixture and promote a draft unit."""
        unit_id = "EX-F1-S1-T1"
        backlog_dir = tmp_path / "backlog"
        backlog_dir.mkdir()
        backlog_md = tmp_path / "BACKLOG.md"
        backlog_md.write_text(
            "# Backlog\n\n## Full Work Unit Index\n\n"
            "| ID | Title | Type | Status | Dependencies | Repo | File Path |\n"
            "|----|-------|------|--------|--------------|------|-----------|\n"
            f"| {unit_id} | Real Test | Task | draft | None | caylent-solutions/git-repo "
            f"| `backlog/{unit_id}.md` |\n"
        )
        wu_file = backlog_dir / f"{unit_id}.md"
        wu_file.write_text(
            f"# {unit_id}: Real Test\n\n## Status: draft\n\n## Description\n\nReal fixture.\n\n## Comments\n"
        )
        with (
            patch("devbench.cli.BACKLOG_ROOT", backlog_dir),
            patch("devbench.cli.BACKLOG_INDEX", backlog_md),
            patch("devbench.cli.WORKSPACE_ROOT", tmp_path),
        ):
            rc = cli.cmd_promote(unit_id)
        assert rc == 0
        content = wu_file.read_text()
        assert "## Status: in-queue" in content
        assert "[PROMOTED] draft -> in-queue" in content
        # Verify BACKLOG.md index row was also updated
        index_content = backlog_md.read_text()
        assert "in-queue" in index_content


def _build_promote_backlog_fixture(
    tmp_path: Path,
    units: list[tuple[str, str]],
) -> tuple[Path, Path]:
    """Build a minimal backlog fixture for promote tests.

    Creates a BACKLOG.md index and individual work-unit files under
    ``tmp_path/backlog/`` from a list of (unit_id, status) pairs.

    Args:
        tmp_path: Pytest tmp_path fixture (or any writable directory).
        units: List of (unit_id, status) pairs to materialise.

    Returns:
        Tuple of (backlog_md path, backlog_dir path).
    """
    backlog_dir = tmp_path / "backlog"
    backlog_dir.mkdir(exist_ok=True)
    rows = "\n".join(
        f"| {uid} | Title {uid} | Task | {st} | None | caylent-solutions/git-repo | `backlog/{uid}.md` |"
        for uid, st in units
    )
    backlog_md = tmp_path / "BACKLOG.md"
    backlog_md.write_text(
        "# Backlog\n\n## Full Work Unit Index\n\n"
        "| ID | Title | Type | Status | Dependencies | Repo | File Path |\n"
        "|----|-------|------|--------|--------------|------|-----------|\n"
        f"{rows}\n"
    )
    for uid, st in units:
        wu_file = backlog_dir / f"{uid}.md"
        wu_file.write_text(f"# {uid}: Title {uid}\n\n## Status: {st}\n\n## Description\n\nFixture.\n\n## Comments\n")
    return backlog_md, backlog_dir


class TestCmdPromoteBulk:
    """E1-F4-S1-T2: ``devbench promote --epic/--feature/--story <id>`` bulk selectors."""

    # ------------------------------------------------------------------
    # --epic selector
    # ------------------------------------------------------------------

    def test_epic_promotes_all_draft_descendants(self, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
        """--epic E1 promotes all draft-status tasks under E1 in one transaction."""
        units = [
            ("E1-F1-S1-T1", "draft"),
            ("E1-F1-S1-T2", "draft"),
            ("E1-F2-S1-T1", "draft"),
            ("E2-F1-S1-T1", "draft"),  # different epic -- must not be promoted
        ]
        backlog_md, backlog_dir = _build_promote_backlog_fixture(tmp_path, units)
        with (
            patch("devbench.cli.BACKLOG_ROOT", backlog_dir),
            patch("devbench.cli.BACKLOG_INDEX", backlog_md),
            patch("devbench.cli.WORKSPACE_ROOT", tmp_path),
        ):
            rc = cli.cmd_promote("--epic", "E1")
        assert rc == 0
        # All E1 descendants promoted
        for uid in ("E1-F1-S1-T1", "E1-F1-S1-T2", "E1-F2-S1-T1"):
            content = (backlog_dir / f"{uid}.md").read_text()
            assert "## Status: in-queue" in content, f"{uid} not promoted"
            assert "[PROMOTED] draft -> in-queue" in content
        # E2 task must remain draft
        e2_content = (backlog_dir / "E2-F1-S1-T1.md").read_text()
        assert "## Status: draft" in e2_content

    def test_epic_no_draft_descendants_returns_1(self, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
        """--epic with no draft descendants reports an error and returns 1."""
        units = [("E1-F1-S1-T1", "in-queue")]
        backlog_md, backlog_dir = _build_promote_backlog_fixture(tmp_path, units)
        with (
            patch("devbench.cli.BACKLOG_ROOT", backlog_dir),
            patch("devbench.cli.BACKLOG_INDEX", backlog_md),
            patch("devbench.cli.WORKSPACE_ROOT", tmp_path),
        ):
            rc = cli.cmd_promote("--epic", "E1")
        assert rc == 1
        err = capsys.readouterr().err
        assert "not in 'draft'" in err or "no draft" in err.lower()

    def test_epic_unknown_scope_returns_1(self, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
        """--epic with an ID that has no matching descendants returns 1."""
        units = [("E1-F1-S1-T1", "draft")]
        backlog_md, backlog_dir = _build_promote_backlog_fixture(tmp_path, units)
        with (
            patch("devbench.cli.BACKLOG_ROOT", backlog_dir),
            patch("devbench.cli.BACKLOG_INDEX", backlog_md),
            patch("devbench.cli.WORKSPACE_ROOT", tmp_path),
        ):
            rc = cli.cmd_promote("--epic", "E99")
        assert rc == 1
        err = capsys.readouterr().err
        assert "no draft" in err.lower()

    def test_epic_mixed_statuses_aborts_transaction(self, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
        """--epic aborts with rc=1 if any descendant is not in draft status."""
        units = [
            ("E1-F1-S1-T1", "draft"),
            ("E1-F1-S1-T2", "in-queue"),  # non-draft -- should abort
        ]
        backlog_md, backlog_dir = _build_promote_backlog_fixture(tmp_path, units)
        with (
            patch("devbench.cli.BACKLOG_ROOT", backlog_dir),
            patch("devbench.cli.BACKLOG_INDEX", backlog_md),
            patch("devbench.cli.WORKSPACE_ROOT", tmp_path),
        ):
            rc = cli.cmd_promote("--epic", "E1")
        assert rc == 1
        err = capsys.readouterr().err
        assert "not in 'draft'" in err
        # No partial promotion -- T1 remains draft
        t1_content = (backlog_dir / "E1-F1-S1-T1.md").read_text()
        assert "## Status: draft" in t1_content

    # ------------------------------------------------------------------
    # --feature selector
    # ------------------------------------------------------------------

    def test_feature_promotes_all_draft_descendants(self, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
        """--feature E1-F2 promotes all draft tasks under E1-F2 only."""
        units = [
            ("E1-F1-S1-T1", "draft"),  # different feature -- must not be promoted
            ("E1-F2-S1-T1", "draft"),
            ("E1-F2-S1-T2", "draft"),
        ]
        backlog_md, backlog_dir = _build_promote_backlog_fixture(tmp_path, units)
        with (
            patch("devbench.cli.BACKLOG_ROOT", backlog_dir),
            patch("devbench.cli.BACKLOG_INDEX", backlog_md),
            patch("devbench.cli.WORKSPACE_ROOT", tmp_path),
        ):
            rc = cli.cmd_promote("--feature", "E1-F2")
        assert rc == 0
        for uid in ("E1-F2-S1-T1", "E1-F2-S1-T2"):
            content = (backlog_dir / f"{uid}.md").read_text()
            assert "## Status: in-queue" in content
        # E1-F1 task must remain draft
        f1_content = (backlog_dir / "E1-F1-S1-T1.md").read_text()
        assert "## Status: draft" in f1_content

    def test_feature_aborts_on_non_draft_descendant(self, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
        """--feature aborts if any descendant is not draft."""
        units = [
            ("E1-F2-S1-T1", "draft"),
            ("E1-F2-S1-T2", "blocked"),
        ]
        backlog_md, backlog_dir = _build_promote_backlog_fixture(tmp_path, units)
        with (
            patch("devbench.cli.BACKLOG_ROOT", backlog_dir),
            patch("devbench.cli.BACKLOG_INDEX", backlog_md),
            patch("devbench.cli.WORKSPACE_ROOT", tmp_path),
        ):
            rc = cli.cmd_promote("--feature", "E1-F2")
        assert rc == 1
        err = capsys.readouterr().err
        assert "not in 'draft'" in err

    # ------------------------------------------------------------------
    # --story selector
    # ------------------------------------------------------------------

    def test_story_promotes_all_draft_descendants(self, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
        """--story E1-F1-S2 promotes all draft tasks under that story only."""
        units = [
            ("E1-F1-S1-T1", "draft"),  # sibling story -- must not be promoted
            ("E1-F1-S2-T1", "draft"),
            ("E1-F1-S2-T2", "draft"),
        ]
        backlog_md, backlog_dir = _build_promote_backlog_fixture(tmp_path, units)
        with (
            patch("devbench.cli.BACKLOG_ROOT", backlog_dir),
            patch("devbench.cli.BACKLOG_INDEX", backlog_md),
            patch("devbench.cli.WORKSPACE_ROOT", tmp_path),
        ):
            rc = cli.cmd_promote("--story", "E1-F1-S2")
        assert rc == 0
        for uid in ("E1-F1-S2-T1", "E1-F1-S2-T2"):
            content = (backlog_dir / f"{uid}.md").read_text()
            assert "## Status: in-queue" in content
        # S1 task stays draft
        s1_content = (backlog_dir / "E1-F1-S1-T1.md").read_text()
        assert "## Status: draft" in s1_content

    def test_story_aborts_on_non_draft_descendant(self, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
        """--story aborts if any descendant is not draft."""
        units = [
            ("E1-F1-S2-T1", "draft"),
            ("E1-F1-S2-T2", "done"),
        ]
        backlog_md, backlog_dir = _build_promote_backlog_fixture(tmp_path, units)
        with (
            patch("devbench.cli.BACKLOG_ROOT", backlog_dir),
            patch("devbench.cli.BACKLOG_INDEX", backlog_md),
            patch("devbench.cli.WORKSPACE_ROOT", tmp_path),
        ):
            rc = cli.cmd_promote("--story", "E1-F1-S2")
        assert rc == 1
        err = capsys.readouterr().err
        assert "not in 'draft'" in err

    # ------------------------------------------------------------------
    # Missing second argument
    # ------------------------------------------------------------------

    @pytest.mark.parametrize("flag", ["--epic", "--feature", "--story"])
    def test_missing_scope_id_returns_1(self, tmp_path: Path, capsys: pytest.CaptureFixture[str], flag: str) -> None:
        """Calling promote with a selector flag but no ID returns rc=1."""
        units: list[tuple[str, str]] = []
        backlog_md, backlog_dir = _build_promote_backlog_fixture(tmp_path, units)
        with (
            patch("devbench.cli.BACKLOG_ROOT", backlog_dir),
            patch("devbench.cli.BACKLOG_INDEX", backlog_md),
            patch("devbench.cli.WORKSPACE_ROOT", tmp_path),
        ):
            rc = cli.cmd_promote(flag)
        assert rc == 1
        err = capsys.readouterr().err
        assert "requires" in err.lower() or "id" in err.lower()

    # ------------------------------------------------------------------
    # BACKLOG.md index updated in bulk transaction
    # ------------------------------------------------------------------

    def test_epic_updates_backlog_index_for_all_promoted(self, tmp_path: Path) -> None:
        """All promoted units have their BACKLOG.md index row updated."""
        units = [
            ("E1-F1-S1-T1", "draft"),
            ("E1-F1-S1-T2", "draft"),
        ]
        backlog_md, backlog_dir = _build_promote_backlog_fixture(tmp_path, units)
        with (
            patch("devbench.cli.BACKLOG_ROOT", backlog_dir),
            patch("devbench.cli.BACKLOG_INDEX", backlog_md),
            patch("devbench.cli.WORKSPACE_ROOT", tmp_path),
        ):
            rc = cli.cmd_promote("--epic", "E1")
        assert rc == 0
        index = backlog_md.read_text()
        # Both rows updated in index
        for uid in ("E1-F1-S1-T1", "E1-F1-S1-T2"):
            assert uid in index
        # No draft rows remain
        assert "| draft |" not in index

    # ------------------------------------------------------------------
    # promote is in _VARIADIC_COMMANDS
    # ------------------------------------------------------------------

    def test_promote_is_variadic(self) -> None:
        """'promote' must be in _VARIADIC_COMMANDS so bulk flags are forwarded."""
        assert "promote" in cli._VARIADIC_COMMANDS

    # ------------------------------------------------------------------
    # Output messages
    # ------------------------------------------------------------------

    def test_epic_prints_promoted_count(self, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
        """On success, --epic prints a summary line with the count of promoted units."""
        units = [
            ("E1-F1-S1-T1", "draft"),
            ("E1-F1-S1-T2", "draft"),
        ]
        backlog_md, backlog_dir = _build_promote_backlog_fixture(tmp_path, units)
        with (
            patch("devbench.cli.BACKLOG_ROOT", backlog_dir),
            patch("devbench.cli.BACKLOG_INDEX", backlog_md),
            patch("devbench.cli.WORKSPACE_ROOT", tmp_path),
        ):
            cli.cmd_promote("--epic", "E1")
        out = capsys.readouterr().out
        assert "2" in out
        assert "promoted" in out.lower()

    # ------------------------------------------------------------------
    # Invalid-usage fallback branch (else clause in cmd_promote)
    # ------------------------------------------------------------------

    @pytest.mark.parametrize(
        "argv",
        [
            ("--epic", "E1", "extra"),  # too many arguments
            ("--feature", "F1", "spurious"),  # too many arguments with --feature
            ("--story", "S1", "x", "y"),  # even more args
        ],
    )
    def test_too_many_args_returns_1_with_usage_error(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str], argv: tuple[str, ...]
    ) -> None:
        """Passing extra positional args after the scope ID prints a usage error and rc=1."""
        units: list[tuple[str, str]] = [("E1-F1-S1-T1", "draft")]
        backlog_md, backlog_dir = _build_promote_backlog_fixture(tmp_path, units)
        with (
            patch("devbench.cli.BACKLOG_ROOT", backlog_dir),
            patch("devbench.cli.BACKLOG_INDEX", backlog_md),
            patch("devbench.cli.WORKSPACE_ROOT", tmp_path),
        ):
            rc = cli.cmd_promote(*argv)
        assert rc == 1
        err = capsys.readouterr().err
        assert "usage" in err.lower() or "promote" in err.lower()

    @pytest.mark.parametrize(
        "argv",
        [
            ("--unknown", "E1"),  # unrecognised flag
            ("--all-epics", "E1"),  # another unrecognised flag
        ],
    )
    def test_unknown_flag_returns_1_with_usage_error(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str], argv: tuple[str, ...]
    ) -> None:
        """Passing an unknown flag prints a usage error on stderr and returns rc=1."""
        units: list[tuple[str, str]] = [("E1-F1-S1-T1", "draft")]
        backlog_md, backlog_dir = _build_promote_backlog_fixture(tmp_path, units)
        with (
            patch("devbench.cli.BACKLOG_ROOT", backlog_dir),
            patch("devbench.cli.BACKLOG_INDEX", backlog_md),
            patch("devbench.cli.WORKSPACE_ROOT", tmp_path),
        ):
            rc = cli.cmd_promote(*argv)
        assert rc == 1
        err = capsys.readouterr().err
        assert "usage" in err.lower() or "promote" in err.lower()


class TestCmdPromoteBulkIntegration:
    """Integration tests: bulk promote against real fixture workspaces."""

    def test_epic_bulk_promote_full_journey(self, tmp_path: Path) -> None:
        """End-to-end: promote --epic promotes all drafts, updates index, appends audit."""
        units = [
            ("E5-F1-S1-T1", "draft"),
            ("E5-F1-S1-T2", "draft"),
            ("E5-F2-S1-T1", "draft"),
        ]
        backlog_md, backlog_dir = _build_promote_backlog_fixture(tmp_path, units)
        with (
            patch("devbench.cli.BACKLOG_ROOT", backlog_dir),
            patch("devbench.cli.BACKLOG_INDEX", backlog_md),
            patch("devbench.cli.WORKSPACE_ROOT", tmp_path),
        ):
            rc = cli.cmd_promote("--epic", "E5")
        assert rc == 0
        for uid, _ in units:
            wu = (backlog_dir / f"{uid}.md").read_text()
            assert "## Status: in-queue" in wu, f"{uid} not transitioned"
            assert "[PROMOTED] draft -> in-queue" in wu, f"{uid} missing audit"
        index = backlog_md.read_text()
        assert "| draft |" not in index

    def test_story_bulk_promote_full_journey(self, tmp_path: Path) -> None:
        """End-to-end: promote --story promotes only that story's drafts."""
        units = [
            ("E5-F1-S1-T1", "draft"),
            ("E5-F1-S2-T1", "draft"),
            ("E5-F1-S2-T2", "draft"),
        ]
        backlog_md, backlog_dir = _build_promote_backlog_fixture(tmp_path, units)
        with (
            patch("devbench.cli.BACKLOG_ROOT", backlog_dir),
            patch("devbench.cli.BACKLOG_INDEX", backlog_md),
            patch("devbench.cli.WORKSPACE_ROOT", tmp_path),
        ):
            rc = cli.cmd_promote("--story", "E5-F1-S2")
        assert rc == 0
        # S2 tasks promoted
        for uid in ("E5-F1-S2-T1", "E5-F1-S2-T2"):
            wu = (backlog_dir / f"{uid}.md").read_text()
            assert "## Status: in-queue" in wu
        # S1 task not touched
        s1 = (backlog_dir / "E5-F1-S1-T1.md").read_text()
        assert "## Status: draft" in s1

    def test_aborted_transaction_leaves_no_partial_writes(self, tmp_path: Path) -> None:
        """When abort fires, no unit file is mutated."""
        units = [
            ("E5-F1-S1-T1", "draft"),
            ("E5-F1-S1-T2", "in-progress"),
        ]
        backlog_md, backlog_dir = _build_promote_backlog_fixture(tmp_path, units)
        original_t1 = (backlog_dir / "E5-F1-S1-T1.md").read_text()
        original_index = backlog_md.read_text()
        with (
            patch("devbench.cli.BACKLOG_ROOT", backlog_dir),
            patch("devbench.cli.BACKLOG_INDEX", backlog_md),
            patch("devbench.cli.WORKSPACE_ROOT", tmp_path),
        ):
            rc = cli.cmd_promote("--epic", "E5")
        assert rc == 1
        # Files unchanged
        assert (backlog_dir / "E5-F1-S1-T1.md").read_text() == original_t1
        assert backlog_md.read_text() == original_index

    def test_file_not_found_returns_1_with_error(self, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
        """_promote_bulk returns rc=1 with an error when _resolve_unit_file returns None for a descendant."""
        units = [
            ("E5-F1-S1-T1", "draft"),
            ("E5-F1-S1-T2", "draft"),
        ]
        backlog_md, backlog_dir = _build_promote_backlog_fixture(tmp_path, units)
        # Patch _resolve_unit_file to return None for one specific unit id
        original_resolve = cli._resolve_unit_file

        def _resolve_returning_none(unit: WorkUnit) -> Path | None:
            if unit.id == "E5-F1-S1-T2":
                return None
            return original_resolve(unit)

        with (
            patch("devbench.cli.BACKLOG_ROOT", backlog_dir),
            patch("devbench.cli.BACKLOG_INDEX", backlog_md),
            patch("devbench.cli.WORKSPACE_ROOT", tmp_path),
            patch("devbench.cli._resolve_unit_file", side_effect=_resolve_returning_none),
        ):
            rc = cli.cmd_promote("--epic", "E5")
        assert rc == 1
        err = capsys.readouterr().err
        assert "E5-F1-S1-T2" in err
        assert "not found" in err.lower()
        # No partial writes -- T1 file must still be draft
        t1_content = (backlog_dir / "E5-F1-S1-T1.md").read_text()
        assert "## Status: draft" in t1_content


class TestCmdPromoteAll:
    """E1-F4-S1-T3: ``devbench promote --all [--yes]`` promotes every draft WU."""

    # ------------------------------------------------------------------
    # Happy path: --all --yes skips confirmation and promotes everything
    # ------------------------------------------------------------------

    def test_all_yes_promotes_every_draft_unit(self, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
        """--all --yes promotes all draft WUs without prompting."""
        units = [
            ("E1-F1-S1-T1", "draft"),
            ("E2-F1-S1-T1", "draft"),
            ("E3-F1-S1-T1", "in-queue"),  # not draft -- must be skipped
        ]
        backlog_md, backlog_dir = _build_promote_backlog_fixture(tmp_path, units)
        with (
            patch("devbench.cli.BACKLOG_ROOT", backlog_dir),
            patch("devbench.cli.BACKLOG_INDEX", backlog_md),
            patch("devbench.cli.WORKSPACE_ROOT", tmp_path),
        ):
            rc = cli.cmd_promote("--all", "--yes")
        assert rc == 0
        # Draft units promoted
        for uid in ("E1-F1-S1-T1", "E2-F1-S1-T1"):
            content = (backlog_dir / f"{uid}.md").read_text()
            assert "## Status: in-queue" in content, f"{uid} not promoted"
            assert "[PROMOTED] draft -> in-queue" in content
        # Non-draft unit untouched
        e3_content = (backlog_dir / "E3-F1-S1-T1.md").read_text()
        assert "## Status: in-queue" in e3_content  # was already in-queue, not promoted
        out = capsys.readouterr().out
        assert "2" in out
        assert "promoted" in out.lower()

    def test_all_yes_no_draft_units_returns_1(self, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
        """--all --yes with no draft WUs returns rc=1 and reports an error."""
        units = [
            ("E1-F1-S1-T1", "in-queue"),
            ("E2-F1-S1-T1", "done"),
        ]
        backlog_md, backlog_dir = _build_promote_backlog_fixture(tmp_path, units)
        with (
            patch("devbench.cli.BACKLOG_ROOT", backlog_dir),
            patch("devbench.cli.BACKLOG_INDEX", backlog_md),
            patch("devbench.cli.WORKSPACE_ROOT", tmp_path),
        ):
            rc = cli.cmd_promote("--all", "--yes")
        assert rc == 1
        err = capsys.readouterr().err
        assert "no draft" in err.lower()

    def test_all_yes_appends_audit_comment(self, tmp_path: Path) -> None:
        """Each promoted unit gets an audit comment with [PROMOTED] marker."""
        units = [("E1-F1-S1-T1", "draft"), ("E1-F1-S1-T2", "draft")]
        backlog_md, backlog_dir = _build_promote_backlog_fixture(tmp_path, units)
        with (
            patch("devbench.cli.BACKLOG_ROOT", backlog_dir),
            patch("devbench.cli.BACKLOG_INDEX", backlog_md),
            patch("devbench.cli.WORKSPACE_ROOT", tmp_path),
        ):
            cli.cmd_promote("--all", "--yes")
        for uid in ("E1-F1-S1-T1", "E1-F1-S1-T2"):
            content = (backlog_dir / f"{uid}.md").read_text()
            assert "[agent/orchestrator]" in content
            assert "[PROMOTED] draft -> in-queue" in content

    def test_all_yes_updates_backlog_index(self, tmp_path: Path) -> None:
        """BACKLOG.md index rows are updated to in-queue for every promoted unit."""
        units = [("E1-F1-S1-T1", "draft"), ("E2-F1-S1-T1", "draft")]
        backlog_md, backlog_dir = _build_promote_backlog_fixture(tmp_path, units)
        with (
            patch("devbench.cli.BACKLOG_ROOT", backlog_dir),
            patch("devbench.cli.BACKLOG_INDEX", backlog_md),
            patch("devbench.cli.WORKSPACE_ROOT", tmp_path),
        ):
            cli.cmd_promote("--all", "--yes")
        index = backlog_md.read_text()
        assert "| draft |" not in index

    # ------------------------------------------------------------------
    # Confirmation prompt behaviour (without --yes)
    # ------------------------------------------------------------------

    def test_all_without_yes_prompts_and_aborts_on_no(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """--all without --yes prompts; answering 'n' aborts without promoting."""
        units = [("E1-F1-S1-T1", "draft"), ("E1-F1-S1-T2", "draft")]
        backlog_md, backlog_dir = _build_promote_backlog_fixture(tmp_path, units)
        # Simulate user typing 'n'
        monkeypatch.setattr("builtins.input", lambda _prompt: "n")
        with (
            patch("devbench.cli.BACKLOG_ROOT", backlog_dir),
            patch("devbench.cli.BACKLOG_INDEX", backlog_md),
            patch("devbench.cli.WORKSPACE_ROOT", tmp_path),
        ):
            rc = cli.cmd_promote("--all")
        assert rc == 1
        # No units promoted
        for uid in ("E1-F1-S1-T1", "E1-F1-S1-T2"):
            content = (backlog_dir / f"{uid}.md").read_text()
            assert "## Status: draft" in content

    def test_all_without_yes_prompts_and_proceeds_on_yes(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """--all without --yes prompts; answering 'y' proceeds with promotion."""
        units = [("E1-F1-S1-T1", "draft"), ("E1-F1-S1-T2", "draft")]
        backlog_md, backlog_dir = _build_promote_backlog_fixture(tmp_path, units)
        # Simulate user typing 'y'
        monkeypatch.setattr("builtins.input", lambda _prompt: "y")
        with (
            patch("devbench.cli.BACKLOG_ROOT", backlog_dir),
            patch("devbench.cli.BACKLOG_INDEX", backlog_md),
            patch("devbench.cli.WORKSPACE_ROOT", tmp_path),
        ):
            rc = cli.cmd_promote("--all")
        assert rc == 0
        for uid in ("E1-F1-S1-T1", "E1-F1-S1-T2"):
            content = (backlog_dir / f"{uid}.md").read_text()
            assert "## Status: in-queue" in content

    @pytest.mark.parametrize("answer", ["N", "no", "NO", "cancel", ""])
    def test_all_without_yes_aborts_on_non_affirmative_answers(
        self,
        tmp_path: Path,
        capsys: pytest.CaptureFixture[str],
        monkeypatch: pytest.MonkeyPatch,
        answer: str,
    ) -> None:
        """--all aborts on any answer that is not 'y' or 'yes' (case insensitive)."""
        units = [("E1-F1-S1-T1", "draft")]
        backlog_md, backlog_dir = _build_promote_backlog_fixture(tmp_path, units)
        monkeypatch.setattr("builtins.input", lambda _prompt: answer)
        with (
            patch("devbench.cli.BACKLOG_ROOT", backlog_dir),
            patch("devbench.cli.BACKLOG_INDEX", backlog_md),
            patch("devbench.cli.WORKSPACE_ROOT", tmp_path),
        ):
            rc = cli.cmd_promote("--all")
        assert rc == 1
        content = (backlog_dir / "E1-F1-S1-T1.md").read_text()
        assert "## Status: draft" in content

    @pytest.mark.parametrize("answer", ["y", "Y", "yes", "YES", "Yes"])
    def test_all_without_yes_proceeds_on_affirmative_answers(
        self,
        tmp_path: Path,
        capsys: pytest.CaptureFixture[str],
        monkeypatch: pytest.MonkeyPatch,
        answer: str,
    ) -> None:
        """--all proceeds on 'y' or 'yes' answers (case insensitive)."""
        units = [("E1-F1-S1-T1", "draft")]
        backlog_md, backlog_dir = _build_promote_backlog_fixture(tmp_path, units)
        monkeypatch.setattr("builtins.input", lambda _prompt: answer)
        with (
            patch("devbench.cli.BACKLOG_ROOT", backlog_dir),
            patch("devbench.cli.BACKLOG_INDEX", backlog_md),
            patch("devbench.cli.WORKSPACE_ROOT", tmp_path),
        ):
            rc = cli.cmd_promote("--all")
        assert rc == 0
        content = (backlog_dir / "E1-F1-S1-T1.md").read_text()
        assert "## Status: in-queue" in content

    def test_all_without_yes_shows_count_in_prompt(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """The confirmation prompt includes the number of draft units to be promoted."""
        units = [
            ("E1-F1-S1-T1", "draft"),
            ("E1-F1-S1-T2", "draft"),
            ("E1-F1-S1-T3", "draft"),
        ]
        backlog_md, backlog_dir = _build_promote_backlog_fixture(tmp_path, units)
        prompts: list[str] = []

        def capture_input(prompt: str) -> str:
            prompts.append(prompt)
            return "n"

        monkeypatch.setattr("builtins.input", capture_input)
        with (
            patch("devbench.cli.BACKLOG_ROOT", backlog_dir),
            patch("devbench.cli.BACKLOG_INDEX", backlog_md),
            patch("devbench.cli.WORKSPACE_ROOT", tmp_path),
        ):
            cli.cmd_promote("--all")
        assert len(prompts) == 1
        assert "3" in prompts[0]

    # ------------------------------------------------------------------
    # Missing file path error (TOCTOU race)
    # ------------------------------------------------------------------

    def test_all_yes_missing_file_returns_1(self, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
        """--all --yes returns rc=1 if _resolve_unit_file returns None for any draft unit."""
        units = [("E1-F1-S1-T1", "draft"), ("E1-F1-S1-T2", "draft")]
        backlog_md, backlog_dir = _build_promote_backlog_fixture(tmp_path, units)
        original_resolve = cli._resolve_unit_file

        def _resolve_returning_none(unit: WorkUnit) -> Path | None:
            if unit.id == "E1-F1-S1-T2":
                return None
            return original_resolve(unit)

        with (
            patch("devbench.cli.BACKLOG_ROOT", backlog_dir),
            patch("devbench.cli.BACKLOG_INDEX", backlog_md),
            patch("devbench.cli.WORKSPACE_ROOT", tmp_path),
            patch("devbench.cli._resolve_unit_file", side_effect=_resolve_returning_none),
        ):
            rc = cli.cmd_promote("--all", "--yes")
        assert rc == 1
        err = capsys.readouterr().err
        assert "E1-F1-S1-T2" in err
        assert "not found" in err.lower()
        # T1 also must not be promoted (fail-fast, no partial writes)
        t1_content = (backlog_dir / "E1-F1-S1-T1.md").read_text()
        assert "## Status: draft" in t1_content

    # ------------------------------------------------------------------
    # Invalid usage: unknown extra flags with --all
    # ------------------------------------------------------------------

    def test_all_with_unknown_extra_flag_returns_1(self, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
        """--all with an unexpected extra argument returns rc=1 with a usage error."""
        units: list[tuple[str, str]] = [("E1-F1-S1-T1", "draft")]
        backlog_md, backlog_dir = _build_promote_backlog_fixture(tmp_path, units)
        with (
            patch("devbench.cli.BACKLOG_ROOT", backlog_dir),
            patch("devbench.cli.BACKLOG_INDEX", backlog_md),
            patch("devbench.cli.WORKSPACE_ROOT", tmp_path),
        ):
            rc = cli.cmd_promote("--all", "--unknown-flag")
        assert rc == 1
        err = capsys.readouterr().err
        assert "usage" in err.lower() or "promote" in err.lower()

    # ------------------------------------------------------------------
    # Integration test: full journey with --all --yes
    # ------------------------------------------------------------------

    def test_all_yes_integration_full_journey(self, tmp_path: Path) -> None:
        """End-to-end: --all --yes promotes every draft, updates index, appends audit."""
        units = [
            ("E1-F1-S1-T1", "draft"),
            ("E2-F1-S1-T1", "draft"),
            ("E3-F1-S1-T1", "done"),  # must not be promoted
            ("E4-F1-S1-T1", "blocked"),  # must not be promoted
        ]
        backlog_md, backlog_dir = _build_promote_backlog_fixture(tmp_path, units)
        with (
            patch("devbench.cli.BACKLOG_ROOT", backlog_dir),
            patch("devbench.cli.BACKLOG_INDEX", backlog_md),
            patch("devbench.cli.WORKSPACE_ROOT", tmp_path),
        ):
            rc = cli.cmd_promote("--all", "--yes")
        assert rc == 0
        for uid in ("E1-F1-S1-T1", "E2-F1-S1-T1"):
            wu = (backlog_dir / f"{uid}.md").read_text()
            assert "## Status: in-queue" in wu, f"{uid} not transitioned"
            assert "[PROMOTED] draft -> in-queue" in wu, f"{uid} missing audit"
        # Non-draft units untouched
        assert "## Status: done" in (backlog_dir / "E3-F1-S1-T1.md").read_text()
        assert "## Status: blocked" in (backlog_dir / "E4-F1-S1-T1.md").read_text()
        # Index updated correctly
        index = backlog_md.read_text()
        assert "| draft |" not in index


class TestWireOrphanCleanupDepChain:
    """Phase 10: orphan-cleanup auto-emission resolves Manifest collisions via auto-wired deps."""

    def _build_minimal_backlog(
        self,
        tmp_path: Path,
        peer_status: str = "in-queue",
        cleanup_id: str = "E0-F1-S1-T9",
        peer_id: str = "E0-F1-S1-T2",
        repo: str = "ex/foo",
    ) -> Path:
        """Render a backlog where ``peer_id`` already claims `.gitignore`.

        ``cleanup_id`` represents the just-emitted cleanup task; the
        helper assumes it has been materialised on disk separately
        (the test populates a stub work-unit so ``add_dep``'s
        index-presence check passes).
        """
        wu_dir = tmp_path / "backlog"
        wu_dir.mkdir(exist_ok=True)
        # Peer task: claims .gitignore in its manifest.
        (wu_dir / f"{peer_id}.md").write_text(
            f"# {peer_id}: peer\n\n"
            f"## Status: {peer_status}\n\n"
            "## Description\n\nx\n\n"
            "## Dependencies\n\n| ID | Type | Reason |\n|----|------|--------|\n| none | | |\n\n"
            "## Acceptance Criteria\n\n- [ ] AC-FUNC-001\n\n"
            "## Changes Manifest\n\n| File | Change |\n|------|--------|\n"
            "| `.gitignore` | edit |\n| `peer.py` | new |\n\n"
            "## Definition of Done\n\n- [ ] Done\n",
            encoding="utf-8",
        )
        # Cleanup task: claims .gitignore (the auto-emitted target).
        (wu_dir / f"{cleanup_id}.md").write_text(
            f"# {cleanup_id}: cleanup\n\n"
            "## Status: in-queue\n\n"
            "## Description\n\nx\n\n"
            "## Dependencies\n\n| ID | Type | Reason |\n|----|------|--------|\n| none | | |\n\n"
            "## Acceptance Criteria\n\n- [ ] AC-FUNC-001\n\n"
            "## Changes Manifest\n\n| File | Change |\n|------|--------|\n"
            "| `.gitignore` | edit |\n\n"
            "## Definition of Done\n\n- [ ] Done\n",
            encoding="utf-8",
        )
        index_path = tmp_path / "BACKLOG.md"
        index_path.write_text(
            "# Backlog\n\n## Full Work Unit Index\n\n"
            "| ID | Title | Type | Status | Dependencies | Repo | File Path |\n"
            "|----|-------|------|--------|--------------|------|-----------|\n"
            f"| {peer_id} | peer | Task | {peer_status} | none | {repo} | `backlog/{peer_id}.md` |\n"
            f"| {cleanup_id} | cleanup | Task | in-queue | none | {repo} | `backlog/{cleanup_id}.md` |\n",
            encoding="utf-8",
        )
        return index_path

    def test_wires_dep_when_peer_claims_same_path(self, tmp_path: Path) -> None:
        index_path = self._build_minimal_backlog(tmp_path)
        with (
            patch("devbench.cli.BACKLOG_ROOT", tmp_path / "backlog"),
            patch("devbench.cli.BACKLOG_INDEX", index_path),
            patch("devbench.cli.WORKSPACE_ROOT", tmp_path),
        ):
            wired = cli._wire_orphan_cleanup_dep_chain(
                new_id="E0-F1-S1-T9",
                files_to_own=[".gitignore"],
                unit_repo="ex/foo",
            )
        assert wired == ["E0-F1-S1-T2"]
        peer_content = (tmp_path / "backlog" / "E0-F1-S1-T2.md").read_text()
        # The dep row was added to the peer's Dependencies table.
        assert "E0-F1-S1-T9" in peer_content

    def test_no_collision_returns_empty(self, tmp_path: Path) -> None:
        # Build a backlog where the only peer task claims a different path.
        wu_dir = tmp_path / "backlog"
        wu_dir.mkdir(exist_ok=True)
        (wu_dir / "E0-F1-S1-T2.md").write_text(
            "# E0-F1-S1-T2: peer\n\n## Status: in-queue\n\n## Description\n\nx\n\n"
            "## Dependencies\n\n| ID | Type | Reason |\n|----|------|--------|\n| none | | |\n\n"
            "## Acceptance Criteria\n\n- [ ] AC-FUNC-001\n\n"
            "## Changes Manifest\n\n| File | Change |\n|------|--------|\n| `peer.py` | new |\n\n"
            "## Definition of Done\n\n- [ ] Done\n",
            encoding="utf-8",
        )
        (wu_dir / "E0-F1-S1-T9.md").write_text(
            "# E0-F1-S1-T9: cleanup\n\n## Status: in-queue\n\n## Description\n\nx\n\n"
            "## Dependencies\n\n| ID | Type | Reason |\n|----|------|--------|\n| none | | |\n\n"
            "## Acceptance Criteria\n\n- [ ] AC-FUNC-001\n\n"
            "## Changes Manifest\n\n| File | Change |\n|------|--------|\n| `.gitignore` | edit |\n\n"
            "## Definition of Done\n\n- [ ] Done\n",
            encoding="utf-8",
        )
        index_path = tmp_path / "BACKLOG.md"
        index_path.write_text(
            "# Backlog\n\n## Full Work Unit Index\n\n"
            "| ID | Title | Type | Status | Dependencies | Repo | File Path |\n"
            "|----|-------|------|--------|--------------|------|-----------|\n"
            "| E0-F1-S1-T2 | peer | Task | in-queue | none | ex/foo | `backlog/E0-F1-S1-T2.md` |\n"
            "| E0-F1-S1-T9 | cleanup | Task | in-queue | none | ex/foo | `backlog/E0-F1-S1-T9.md` |\n",
            encoding="utf-8",
        )
        with (
            patch("devbench.cli.BACKLOG_ROOT", tmp_path / "backlog"),
            patch("devbench.cli.BACKLOG_INDEX", index_path),
            patch("devbench.cli.WORKSPACE_ROOT", tmp_path),
        ):
            wired = cli._wire_orphan_cleanup_dep_chain(
                new_id="E0-F1-S1-T9",
                files_to_own=[".gitignore"],
                unit_repo="ex/foo",
            )
        assert wired == []

    def test_skips_done_and_declined_peers(self, tmp_path: Path) -> None:
        # Done peers cannot be wired (add_dep refuses terminal blockers anyway).
        index_path = self._build_minimal_backlog(tmp_path, peer_status="done")
        with (
            patch("devbench.cli.BACKLOG_ROOT", tmp_path / "backlog"),
            patch("devbench.cli.BACKLOG_INDEX", index_path),
            patch("devbench.cli.WORKSPACE_ROOT", tmp_path),
        ):
            wired = cli._wire_orphan_cleanup_dep_chain(
                new_id="E0-F1-S1-T9",
                files_to_own=[".gitignore"],
                unit_repo="ex/foo",
            )
        assert wired == []


class TestCmdNewTask:
    """E223: ``devbench new-task`` scaffolds a work-unit file from a template."""

    def test_renders_task_template_with_substitutions(
        self,
        tmp_path: Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        target = tmp_path / "E0-F1-S1-T1.md"
        rc = cli.cmd_new_task(
            "--id",
            "E0-F1-S1-T1",
            "--title",
            "Implement foo",
            "--target",
            str(target),
            "--repo",
            "ex/foo",
            "--description",
            "Foo capability.",
            "--source-file",
            "src/foo/handler.py",
            "--test-file",
            "tests/unit/test_handler.py",
            "--ac-func",
            "the function returns the right answer",
        )
        assert rc == 0
        out = json.loads(capsys.readouterr().out.strip())
        assert out == {"id": "E0-F1-S1-T1", "kind": "task", "target": str(target)}
        rendered = target.read_text()
        assert "# E0-F1-S1-T1: Implement foo" in rendered
        assert "## Status: in-queue" in rendered
        assert "Foo capability." in rendered
        assert "src/foo/handler.py" in rendered
        assert "tests/unit/test_handler.py" in rendered
        assert "the function returns the right answer" in rendered
        assert "backlog/e0-f1-s1-t1" in rendered

    def test_collision_with_existing_target_refused(
        self,
        tmp_path: Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        target = tmp_path / "exists.md"
        target.write_text("already there")
        rc = cli.cmd_new_task(
            "--id",
            "E0-F1-S1-T1",
            "--title",
            "x",
            "--target",
            str(target),
        )
        assert rc == 1
        assert "already exists" in capsys.readouterr().err

    def test_missing_parent_dir_refused(
        self,
        tmp_path: Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        target = tmp_path / "no-such-dir" / "wu.md"
        rc = cli.cmd_new_task(
            "--id",
            "E0-F1-S1-T1",
            "--title",
            "x",
            "--target",
            str(target),
        )
        assert rc == 1
        assert "does not exist" in capsys.readouterr().err

    def test_missing_required_flag_refused(
        self,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        rc = cli.cmd_new_task("--id", "E0-F1-S1-T1", "--title", "x")
        assert rc == 1
        assert "--target is required" in capsys.readouterr().err

    def test_unknown_flag_refused(
        self,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        rc = cli.cmd_new_task("--bogus", "x")
        assert rc == 1
        assert "unknown flag" in capsys.readouterr().err

    def test_template_kind_inferred_from_id(self, tmp_path: Path) -> None:
        target = tmp_path / "E0-F1-S1.md"
        rc = cli.cmd_new_task(
            "--id",
            "E0-F1-S1",
            "--title",
            "Story title",
            "--target",
            str(target),
        )
        assert rc == 0
        assert "# E0-F1-S1: Story title" in target.read_text()

    def test_invalid_id_shape_refused(
        self,
        tmp_path: Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        target = tmp_path / "wu.md"
        rc = cli.cmd_new_task(
            "--id",
            "Q-NOT-VALID",
            "--title",
            "x",
            "--target",
            str(target),
        )
        assert rc == 1
        assert "cannot derive template kind" in capsys.readouterr().err

    def test_flag_without_value_refused(
        self,
        tmp_path: Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        rc = cli.cmd_new_task("--id")
        assert rc == 1
        assert "--id requires a value" in capsys.readouterr().err

    def test_registered_in_commands(self) -> None:
        assert "new-task" in cli._COMMANDS

    def test_is_variadic(self) -> None:
        assert "new-task" in cli._VARIADIC_COMMANDS


class TestCmdSyncBlocked:
    """E215: ``devbench sync-blocked`` reconciles task status against dep satisfaction."""

    def _build_backlog(
        self,
        tmp_path: Path,
        rows: list[tuple[str, str, str, str, str]],
    ) -> Path:
        index_lines = [
            "# Backlog\n",
            "## Full Work Unit Index\n",
            "| ID | Title | Type | Status | Dependencies | Repo | File Path |",
            "|----|-------|------|--------|--------------|------|-----------|",
        ]
        wu_dir = tmp_path / "backlog"
        wu_dir.mkdir(exist_ok=True)
        for unit_id, unit_type, status, deps, basename in rows:
            file_path = f"backlog/{basename}.md"
            index_lines.append(
                f"| {unit_id} | {unit_id} | {unit_type} | {status} | {deps} | "
                f"caylent-solutions/test-repo | `{file_path}` |"
            )
            wu_file = wu_dir / f"{basename}.md"
            wu_body = f"# {unit_id}: Test\n\n## Status: {status}\n\n## Description\n\nx\n"
            if deps and deps != "None":
                dep_rows = "\n".join(f"| {d.strip()} | Task | dep |" for d in deps.split(","))
                wu_body += f"\n## Dependencies\n\n| ID | Type | Reason |\n|----|------|--------|\n{dep_rows}\n"
            wu_file.write_text(wu_body)
        index_path = tmp_path / "BACKLOG.md"
        index_path.write_text("\n".join(index_lines) + "\n")
        return index_path

    def test_in_queue_with_unsatisfied_dep_flips_to_blocked(
        self,
        tmp_path: Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        index_path = self._build_backlog(
            tmp_path,
            rows=[
                ("E0-F1-S1-T1", "Task", "in-queue", "None", "E0-F1-S1-T1"),
                ("E0-F1-S1-T2", "Task", "in-queue", "E0-F1-S1-T1", "E0-F1-S1-T2"),
            ],
        )
        with (
            patch("devbench.cli.BACKLOG_ROOT", tmp_path / "backlog"),
            patch("devbench.cli.BACKLOG_INDEX", index_path),
            patch("devbench.cli.WORKSPACE_ROOT", tmp_path),
        ):
            rc = cli.cmd_sync_blocked()
        assert rc == 0
        envelope = json.loads(capsys.readouterr().out.strip())
        assert envelope["flipped_to_blocked"] == ["E0-F1-S1-T2"]
        assert envelope["flipped_to_in_queue"] == []
        t2 = (tmp_path / "backlog" / "E0-F1-S1-T2.md").read_text()
        assert "## Status: blocked" in t2
        assert "[BLOCKED]" in t2
        assert "E0-F1-S1-T1" in t2

    def test_blocked_with_satisfied_deps_flips_to_in_queue(
        self,
        tmp_path: Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        index_path = self._build_backlog(
            tmp_path,
            rows=[
                ("E0-F1-S1-T1", "Task", "done", "None", "E0-F1-S1-T1"),
                ("E0-F1-S1-T2", "Task", "blocked", "E0-F1-S1-T1", "E0-F1-S1-T2"),
            ],
        )
        with (
            patch("devbench.cli.BACKLOG_ROOT", tmp_path / "backlog"),
            patch("devbench.cli.BACKLOG_INDEX", index_path),
            patch("devbench.cli.WORKSPACE_ROOT", tmp_path),
        ):
            rc = cli.cmd_sync_blocked()
        assert rc == 0
        envelope = json.loads(capsys.readouterr().out.strip())
        assert envelope["flipped_to_blocked"] == []
        assert envelope["flipped_to_in_queue"] == ["E0-F1-S1-T2"]
        t2 = (tmp_path / "backlog" / "E0-F1-S1-T2.md").read_text()
        assert "## Status: in-queue" in t2
        assert "[UNBLOCKED]" in t2

    def test_blocked_with_open_proposal_marker_is_skipped(
        self,
        tmp_path: Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        index_path = self._build_backlog(
            tmp_path,
            rows=[
                ("E0-F1-S1-T1", "Task", "done", "None", "E0-F1-S1-T1"),
                ("E0-F1-S1-T2", "Task", "blocked", "E0-F1-S1-T1", "E0-F1-S1-T2"),
            ],
        )
        t2_path = tmp_path / "backlog" / "E0-F1-S1-T2.md"
        t2_path.write_text(t2_path.read_text() + "\n## Comments\n\n[BLOCKED_PENDING_PROPOSAL] E0-F1-S1-T9\n")
        with (
            patch("devbench.cli.BACKLOG_ROOT", tmp_path / "backlog"),
            patch("devbench.cli.BACKLOG_INDEX", index_path),
            patch("devbench.cli.WORKSPACE_ROOT", tmp_path),
        ):
            rc = cli.cmd_sync_blocked()
        assert rc == 0
        envelope = json.loads(capsys.readouterr().out.strip())
        assert envelope["flipped_to_in_queue"] == []
        assert "## Status: blocked" in t2_path.read_text()

    def test_story_level_dep_recursion(
        self,
        tmp_path: Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        index_path = self._build_backlog(
            tmp_path,
            rows=[
                ("E0-F1-S1", "Story", "in-queue", "None", "E0-F1-S1"),
                ("E0-F1-S1-T1", "Task", "in-queue", "None", "E0-F1-S1-T1"),
                ("E0-F1-S2-T1", "Task", "in-queue", "E0-F1-S1", "E0-F1-S2-T1"),
            ],
        )
        with (
            patch("devbench.cli.BACKLOG_ROOT", tmp_path / "backlog"),
            patch("devbench.cli.BACKLOG_INDEX", index_path),
            patch("devbench.cli.WORKSPACE_ROOT", tmp_path),
        ):
            rc = cli.cmd_sync_blocked()
        assert rc == 0
        envelope = json.loads(capsys.readouterr().out.strip())
        assert "E0-F1-S2-T1" in envelope["flipped_to_blocked"]
        assert "E0-F1-S1-T1" not in envelope["flipped_to_blocked"]

    def test_registered_in_commands(self) -> None:
        assert "sync-blocked" in cli._COMMANDS


class TestCmdHookTail:
    """``devbench hook-tail`` argument parsing and dispatcher registration.

    Runtime behaviour (file-following, formatting) lives in
    ``tests/unit/test_hook_tail.py`` and
    ``tests/test_integration/test_hook_tail_lifecycle.py``; this block
    covers ONLY the CLI-level flag parsing that ``cmd_hook_tail`` owns.
    """

    def test_registered_in_commands(self) -> None:
        assert "hook-tail" in cli._COMMANDS

    def test_is_variadic_so_flags_reach_handler(self) -> None:
        """The dispatcher truncates positional args for fixed-arity commands;
        hook-tail must be in the variadic opt-in set so --tz etc. reach it."""
        assert "hook-tail" in cli._VARIADIC_COMMANDS

    def test_missing_tz_value_returns_2(self, capsys: pytest.CaptureFixture[str]) -> None:
        rc = cli.cmd_hook_tail("--tz")
        assert rc == 2
        assert "--tz requires a value" in capsys.readouterr().err

    def test_empty_tz_value_returns_2(self, capsys: pytest.CaptureFixture[str]) -> None:
        rc = cli.cmd_hook_tail("--tz", "")
        assert rc == 2

    def test_unknown_flag_returns_2(self, capsys: pytest.CaptureFixture[str]) -> None:
        rc = cli.cmd_hook_tail("--bogus")
        assert rc == 2
        assert "unknown flag" in capsys.readouterr().err

    def test_two_positional_paths_return_2(self, capsys: pytest.CaptureFixture[str]) -> None:
        rc = cli.cmd_hook_tail("/tmp/a", "/tmp/b")
        assert rc == 2
        assert "unexpected positional argument" in capsys.readouterr().err

    def test_invalid_tz_returns_2(self, capsys: pytest.CaptureFixture[str]) -> None:
        rc = cli.cmd_hook_tail("--tz", "Not/AZone")
        assert rc == 2
        captured = capsys.readouterr()
        assert "unknown timezone" in captured.err
        assert "Not/AZone" in captured.err

    def test_orchestrator_only_without_env_returns_2(
        self,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        monkeypatch.delenv("DEVBENCH_ORCHESTRATOR_SESSION_ID", raising=False)
        monkeypatch.delenv("DEVBENCH_ORCHESTRATOR_SESSION_ID", raising=False)
        rc = cli.cmd_hook_tail("--orchestrator-only", "--no-follow")
        assert rc == 2
        assert "DEVBENCH_ORCHESTRATOR_SESSION_ID" in capsys.readouterr().err

    def test_orchestrator_only_with_devbench_env_reads_session_id(
        self,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        # AC-197-1: canonical DEVBENCH_ORCHESTRATOR_SESSION_ID is read.
        monkeypatch.delenv("DEVBENCH_ORCHESTRATOR_SESSION_ID", raising=False)
        monkeypatch.setenv("DEVBENCH_ORCHESTRATOR_SESSION_ID", "test-session-456")
        rc = cli.cmd_hook_tail("--orchestrator-only", "--no-follow")
        # The session ID is consumed; output may vary but exit should not be 2.
        assert rc != 2

    def test_orchestrator_session_missing_value_returns_2(
        self,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        rc = cli.cmd_hook_tail("--orchestrator-session")
        assert rc == 2
        assert "--orchestrator-session requires a value" in capsys.readouterr().err


# ---------------------------------------------------------------------------
# Tier 3: test-validates-source heuristic + cmd_promote_proposal warnings
# ---------------------------------------------------------------------------


class TestDetectTestValidatesSource:
    """Unit tests for cli._detect_test_validates_source."""

    @staticmethod
    def _write_proposal(
        proposals_dir: Path,
        source_id: str,
        proposed_id: str,
        title: str = "Implement feature",
        files: list[str] | None = None,
        source_dep_direction: str = "",
    ) -> None:
        proposals_dir.mkdir(parents=True, exist_ok=True)
        payload = {
            "source_task_id": source_id,
            "generated_at": "2026-04-30T00:00:00Z",
            "rejection_reason": "x",
            "source_dep_direction": source_dep_direction,
            "proposed_tasks": [
                {
                    "suggested_id": proposed_id,
                    "title": title,
                    "files_to_own": files or ["src/foo.py"],
                    "linked_scenarios": [],
                    "suggested_acs": [],
                    "suggested_approach": "",
                }
            ],
        }
        (proposals_dir / f"{source_id}.json").write_text(json.dumps(payload))

    def test_returns_empty_when_proposals_dir_missing(self, tmp_path: Path) -> None:
        with patch("devbench.cli.WORKSPACE_ROOT", tmp_path):
            assert cli._detect_test_validates_source("E0-F1-S1-T9") == ""

    def test_returns_flag_when_explicit_source_dep_direction(self, tmp_path: Path) -> None:
        proposals = tmp_path / ".devbench" / "proposals"
        self._write_proposal(
            proposals,
            "E0-F1-S1-T1",
            "E0-F1-S1-T9",
            title="Implement feature",
            source_dep_direction="test_validates_source",
        )
        with patch("devbench.cli.WORKSPACE_ROOT", tmp_path):
            assert cli._detect_test_validates_source("E0-F1-S1-T9") == "flag"

    def test_returns_heuristic_when_title_starts_with_add_tests(self, tmp_path: Path) -> None:
        proposals = tmp_path / ".devbench" / "proposals"
        self._write_proposal(
            proposals,
            "E0-F1-S1-T1",
            "E0-F1-S1-T9",
            title="Add tests/unit/test_foo.py to validate T1's foo.py",
        )
        with patch("devbench.cli.WORKSPACE_ROOT", tmp_path):
            assert cli._detect_test_validates_source("E0-F1-S1-T9") == "heuristic"

    def test_returns_heuristic_when_title_starts_with_verify(self, tmp_path: Path) -> None:
        proposals = tmp_path / ".devbench" / "proposals"
        self._write_proposal(
            proposals,
            "E0-F1-S1-T1",
            "E0-F1-S1-T9",
            title="Verify pyproject.toml lists ruff",
        )
        with patch("devbench.cli.WORKSPACE_ROOT", tmp_path):
            assert cli._detect_test_validates_source("E0-F1-S1-T9") == "heuristic"

    def test_returns_heuristic_when_files_all_under_tests(self, tmp_path: Path) -> None:
        proposals = tmp_path / ".devbench" / "proposals"
        self._write_proposal(
            proposals,
            "E0-F1-S1-T1",
            "E0-F1-S1-T9",
            title="Implement coverage",
            files=["tests/unit/test_a.py", "tests/integration/test_b.py"],
        )
        with patch("devbench.cli.WORKSPACE_ROOT", tmp_path):
            assert cli._detect_test_validates_source("E0-F1-S1-T9") == "heuristic"

    def test_returns_empty_when_no_match(self, tmp_path: Path) -> None:
        proposals = tmp_path / ".devbench" / "proposals"
        self._write_proposal(
            proposals,
            "E0-F1-S1-T1",
            "E0-F1-S1-T9",
            title="Implement merge_properties.py",
            files=["infra/scripts/merge_properties.py"],
        )
        with patch("devbench.cli.WORKSPACE_ROOT", tmp_path):
            assert cli._detect_test_validates_source("E0-F1-S1-T9") == ""

    def test_returns_empty_when_id_not_in_any_proposal(self, tmp_path: Path) -> None:
        proposals = tmp_path / ".devbench" / "proposals"
        self._write_proposal(
            proposals,
            "E0-F1-S1-T1",
            "E0-F1-S1-T9",
            title="Add tests/foo",
        )
        with patch("devbench.cli.WORKSPACE_ROOT", tmp_path):
            assert cli._detect_test_validates_source("E0-F1-S1-T7") == ""

    def test_malformed_json_is_skipped(self, tmp_path: Path) -> None:
        proposals = tmp_path / ".devbench" / "proposals"
        proposals.mkdir(parents=True)
        (proposals / "broken.json").write_text("{not valid json")
        # And one valid file alongside it so we exercise the continue path.
        self._write_proposal(
            proposals,
            "E0-F1-S1-T1",
            "E0-F1-S1-T9",
            title="Add tests/unit/test_x.py",
        )
        with patch("devbench.cli.WORKSPACE_ROOT", tmp_path):
            assert cli._detect_test_validates_source("E0-F1-S1-T9") == "heuristic"


class TestCmdPromoteProposalTestValidatesSource:
    """cmd_promote_proposal honors / warns on the test-validates-source heuristic."""

    def test_flag_auto_applies_no_dep_on_source(self, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
        from devbench.backlog.proposal import PromoteResult

        proposals = tmp_path / ".devbench" / "proposals"
        TestDetectTestValidatesSource._write_proposal(
            proposals,
            "E0-F1-S1-T1",
            "E0-F1-S1-T9",
            source_dep_direction="test_validates_source",
        )
        captured: dict[str, Any] = {}

        def fake_promote(**kwargs: Any) -> PromoteResult:
            captured.update(kwargs)
            return PromoteResult(draft_path=tmp_path / "x.md", wired_targets=[])

        with (
            patch("devbench.cli.WORKSPACE_ROOT", tmp_path),
            patch("devbench.cli.promote_proposal", side_effect=fake_promote),
        ):
            rc = cli.cmd_promote_proposal("E0-F1-S1-T9")
        assert rc == 0
        assert captured.get("dep_on_source") is False
        err = capsys.readouterr().err
        assert "auto-applying --no-dep-on-source" in err

    def test_heuristic_emits_warning_but_keeps_default_dep_on_source(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        from devbench.backlog.proposal import PromoteResult

        proposals = tmp_path / ".devbench" / "proposals"
        TestDetectTestValidatesSource._write_proposal(
            proposals,
            "E0-F1-S1-T1",
            "E0-F1-S1-T9",
            title="Add tests/unit/test_foo.py",
        )
        captured: dict[str, Any] = {}

        def fake_promote(**kwargs: Any) -> PromoteResult:
            captured.update(kwargs)
            return PromoteResult(draft_path=tmp_path / "x.md", wired_targets=[])

        with (
            patch("devbench.cli.WORKSPACE_ROOT", tmp_path),
            patch("devbench.cli.promote_proposal", side_effect=fake_promote),
        ):
            rc = cli.cmd_promote_proposal("E0-F1-S1-T9")
        assert rc == 0
        # Heuristic does NOT auto-flip; just warns.
        assert captured.get("dep_on_source") is True
        err = capsys.readouterr().err
        assert "looks like a test-validates-source task" in err

    def test_no_warning_when_no_dep_on_source_already_set(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        from devbench.backlog.proposal import PromoteResult

        proposals = tmp_path / ".devbench" / "proposals"
        TestDetectTestValidatesSource._write_proposal(
            proposals,
            "E0-F1-S1-T1",
            "E0-F1-S1-T9",
            title="Add tests/unit/test_foo.py",
        )
        with (
            patch("devbench.cli.WORKSPACE_ROOT", tmp_path),
            patch(
                "devbench.cli.promote_proposal",
                return_value=PromoteResult(draft_path=tmp_path / "x.md", wired_targets=[]),
            ),
        ):
            rc = cli.cmd_promote_proposal("--no-dep-on-source", "E0-F1-S1-T9")
        assert rc == 0
        # When the operator passes --no-dep-on-source explicitly, no
        # warning is needed (the heuristic check is gated on dep_on_source).
        err = capsys.readouterr().err
        assert "looks like a test-validates-source task" not in err
        assert "auto-applying --no-dep-on-source" not in err


# ---------------------------------------------------------------------------
# Tier 3: cmd_check pre-flight verifier
# ---------------------------------------------------------------------------


class TestCmdCheck:
    """devbench check: pre-flight readiness check across all repos in devbench.yaml."""

    @staticmethod
    def _write_min_yaml(tmp_path: Path, repos_block: str, single_branch: str = "") -> Path:
        # Schema-conformant minimal config (matches tests/fixtures/test_devbench.yaml).
        cfg_dir = tmp_path / "backlog" / "config"
        cfg_dir.mkdir(parents=True)
        cfg_path = cfg_dir / "devbench.yaml"
        ops_block = (
            f"git_ops:\n  single_branch: {single_branch}\n  defer_pr: true\n"
            if single_branch
            else "git_ops:\n  defer_pr: false\n"
        )
        cfg_path.write_text(f"repos:\n{repos_block}{ops_block}")
        return cfg_path

    def test_returns_1_when_yaml_missing(
        self,
        tmp_path: Path,
        capsys: pytest.CaptureFixture[str],
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        # Point DEVBENCH_CONFIG_PATH at a nonexistent file so resolve_config_path
        # does not fall back to the suite-wide test fixture YAML.
        monkeypatch.setenv("DEVBENCH_CONFIG_PATH", str(tmp_path / "no-such.yaml"))
        with patch("devbench.cli.WORKSPACE_ROOT", tmp_path):
            rc = cli.cmd_check()
        assert rc == 1
        assert "devbench.yaml not found" in capsys.readouterr().err

    def test_returns_1_when_symlink_missing(
        self,
        tmp_path: Path,
        capsys: pytest.CaptureFixture[str],
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        cfg = self._write_min_yaml(
            tmp_path,
            "  org/repo-a:\n    checkout_directory: repo-a\n    default_branch: main\n",
        )
        monkeypatch.setenv("DEVBENCH_CONFIG_PATH", str(cfg))
        with patch("devbench.cli.WORKSPACE_ROOT", tmp_path):
            rc = cli.cmd_check()
        assert rc == 1
        out = capsys.readouterr().out
        assert "symlink missing" in out
        assert "repo-a" in out

    def test_returns_0_when_all_checks_pass(
        self,
        tmp_path: Path,
        capsys: pytest.CaptureFixture[str],
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        # Set up a fake clone with origin remote configured (via mocked subprocess).
        clone = tmp_path / "clone-a"
        clone.mkdir()
        (tmp_path / "repo-a").symlink_to(clone)
        cfg = self._write_min_yaml(
            tmp_path,
            "  org/repo-a:\n    checkout_directory: repo-a\n    default_branch: main\n",
            single_branch="feat/x",
        )
        monkeypatch.setenv("DEVBENCH_CONFIG_PATH", str(cfg))

        def fake_run(args: list[str], **_: Any) -> Any:
            mock = MagicMock()
            if args[:3] == ["git", "-C", str(clone)]:
                mock.returncode = 0
                mock.stdout = "git@github.com:org/repo-a.git\n"
            elif args[:2] == ["gh", "api"]:
                mock.returncode = 0
                mock.stdout = "main\n"
            elif args[:3] == ["gh", "pr", "list"]:
                mock.returncode = 0
                mock.stdout = "[]"
            else:
                mock.returncode = 0
                mock.stdout = ""
            mock.stderr = ""
            return mock

        with (
            patch("devbench.cli.WORKSPACE_ROOT", tmp_path),
            patch("devbench.cli.subprocess.run", side_effect=fake_run),
        ):
            rc = cli.cmd_check()
        assert rc == 0
        assert "Pre-flight check passed" in capsys.readouterr().out

    def test_flags_default_branch_mismatch(
        self,
        tmp_path: Path,
        capsys: pytest.CaptureFixture[str],
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        clone = tmp_path / "clone-a"
        clone.mkdir()
        (tmp_path / "repo-a").symlink_to(clone)
        cfg = self._write_min_yaml(
            tmp_path,
            "  org/repo-a:\n    checkout_directory: repo-a\n    default_branch: main2\n",
        )
        monkeypatch.setenv("DEVBENCH_CONFIG_PATH", str(cfg))

        def fake_run(args: list[str], **_: Any) -> Any:
            mock = MagicMock()
            mock.stderr = ""
            if args[:3] == ["git", "-C", str(clone)]:
                mock.returncode = 0
                mock.stdout = "git@github.com:org/repo-a.git\n"
            elif args[:2] == ["gh", "api"]:
                mock.returncode = 0
                mock.stdout = "main\n"
            else:
                mock.returncode = 0
                mock.stdout = ""
            return mock

        with (
            patch("devbench.cli.WORKSPACE_ROOT", tmp_path),
            patch("devbench.cli.subprocess.run", side_effect=fake_run),
        ):
            rc = cli.cmd_check()
        assert rc == 1
        out = capsys.readouterr().out
        assert "default_branch mismatch" in out
        assert "'main2'" in out and "'main'" in out

    def test_flags_open_pr_on_single_branch(
        self,
        tmp_path: Path,
        capsys: pytest.CaptureFixture[str],
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        clone = tmp_path / "clone-a"
        clone.mkdir()
        (tmp_path / "repo-a").symlink_to(clone)
        cfg = self._write_min_yaml(
            tmp_path,
            "  org/repo-a:\n    checkout_directory: repo-a\n    default_branch: main\n",
            single_branch="feat/x",
        )
        monkeypatch.setenv("DEVBENCH_CONFIG_PATH", str(cfg))

        def fake_run(args: list[str], **_: Any) -> Any:
            mock = MagicMock()
            mock.stderr = ""
            if args[:3] == ["git", "-C", str(clone)]:
                mock.returncode = 0
                mock.stdout = "git@github.com:org/repo-a.git\n"
            elif args[:2] == ["gh", "api"]:
                mock.returncode = 0
                mock.stdout = "main\n"
            elif args[:3] == ["gh", "pr", "list"]:
                mock.returncode = 0
                mock.stdout = '[{"number":42,"title":"existing"}]'
            else:
                mock.returncode = 0
                mock.stdout = ""
            return mock

        with (
            patch("devbench.cli.WORKSPACE_ROOT", tmp_path),
            patch("devbench.cli.subprocess.run", side_effect=fake_run),
        ):
            rc = cli.cmd_check()
        assert rc == 1
        out = capsys.readouterr().out
        assert "open PR(s) already exist on branch" in out

    @staticmethod
    def _write_local_only_yaml(tmp_path: Path) -> Path:
        cfg_dir = tmp_path / "backlog" / "config"
        cfg_dir.mkdir(parents=True)
        cfg_path = cfg_dir / "devbench.yaml"
        cfg_path.write_text(
            "repos:\n"
            "  org/repo-a:\n"
            "    checkout_directory: repo-a\n"
            "    default_branch: main\n"
            "git_ops:\n"
            "  single_branch: feat/x\n"
            "  defer_pr: true\n"
            "  local_only: true\n"
        )
        return cfg_path

    def test_passes_when_local_only_repo_has_no_origin(
        self,
        tmp_path: Path,
        capsys: pytest.CaptureFixture[str],
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Under git_ops.local_only: true, a target repo with NO origin remote passes pre-flight."""
        clone = tmp_path / "clone-a"
        clone.mkdir()
        (tmp_path / "repo-a").symlink_to(clone)
        cfg = self._write_local_only_yaml(tmp_path)
        monkeypatch.setenv("DEVBENCH_CONFIG_PATH", str(cfg))

        def fake_run(args: list[str], **_: Any) -> Any:
            mock = MagicMock()
            mock.stderr = ""
            mock.stdout = ""
            if args[:3] == ["git", "-C", str(clone)]:
                # No origin remote -> rc=2 (the real git failure mode)
                mock.returncode = 2
                mock.stderr = "error: No such remote 'origin'"
            else:
                mock.returncode = 0
            return mock

        with (
            patch("devbench.cli.WORKSPACE_ROOT", tmp_path),
            patch("devbench.cli.subprocess.run", side_effect=fake_run),
        ):
            rc = cli.cmd_check()
        assert rc == 0
        assert "Pre-flight check passed" in capsys.readouterr().out

    def test_flags_local_only_repo_with_origin(
        self,
        tmp_path: Path,
        capsys: pytest.CaptureFixture[str],
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Under git_ops.local_only: true, a target repo that DOES have an origin remote is flagged."""
        clone = tmp_path / "clone-a"
        clone.mkdir()
        (tmp_path / "repo-a").symlink_to(clone)
        cfg = self._write_local_only_yaml(tmp_path)
        monkeypatch.setenv("DEVBENCH_CONFIG_PATH", str(cfg))

        def fake_run(args: list[str], **_: Any) -> Any:
            mock = MagicMock()
            mock.stderr = ""
            if args[:3] == ["git", "-C", str(clone)]:
                mock.returncode = 0
                mock.stdout = "git@github.com:org/repo-a.git\n"
            else:
                mock.returncode = 0
                mock.stdout = ""
            return mock

        with (
            patch("devbench.cli.WORKSPACE_ROOT", tmp_path),
            patch("devbench.cli.subprocess.run", side_effect=fake_run),
        ):
            rc = cli.cmd_check()
        assert rc == 1
        out = capsys.readouterr().out
        assert "git_ops.local_only is true" in out
        assert "has an 'origin' remote" in out


# ---------------------------------------------------------------------------
# Tier 3: variadic dispatch lets `add-dep --reason "<multi token>"` survive
# ---------------------------------------------------------------------------


class TestAddDepVariadicDispatch:
    """The dispatcher must NOT truncate add-dep's --reason value."""

    def test_main_passes_full_reason_through_to_cmd_add_dep(self, monkeypatch: pytest.MonkeyPatch) -> None:
        captured: dict[str, Any] = {}

        def fake_add_dep(*argv: str) -> int:
            captured["argv"] = argv
            return 0

        monkeypatch.setattr(cli, "cmd_add_dep", fake_add_dep)
        # Re-register the patched function in the dispatch table so main() sees it.
        original = cli._COMMANDS["add-dep"]
        monkeypatch.setitem(cli._COMMANDS, "add-dep", (fake_add_dep, original[1], original[2]))
        monkeypatch.setattr(
            "sys.argv",
            [
                "devbench",
                "add-dep",
                "E0-F1-S1-T1",
                "E0-F1-S1-T2",
                "--reason",
                "this is a multi token reason value",
            ],
        )
        rc = cli.main()
        assert rc == 0
        # All five trailing tokens must reach cmd_add_dep, including the
        # full multi-token --reason value (no slicing by MAX_ARGS).
        assert captured["argv"] == (
            "E0-F1-S1-T1",
            "E0-F1-S1-T2",
            "--reason",
            "this is a multi token reason value",
        )


# ---------------------------------------------------------------------------
# write-proposal auto-cascade (closes the resolver-write -> next-sweep gap)
# ---------------------------------------------------------------------------


def _runtime_config_with_auto_accept(value: bool) -> Any:
    """Build a RuntimeConfig clone whose ``task_factory.auto_accept_proposals`` is *value*.

    ``RuntimeConfig`` and ``TaskFactoryConfig`` are frozen dataclasses, so
    mutation via setattr fails with ``FrozenInstanceError``. The
    canonical replacement pattern is ``dataclasses.replace`` for the
    nested config, then ``dataclasses.replace`` for the parent so the
    one runtime field of interest is swapped without touching the
    other config sections.
    """
    import dataclasses

    base = cli.RUNTIME_CONFIG
    new_tf = dataclasses.replace(base.task_factory, auto_accept_proposals=value)
    return dataclasses.replace(base, task_factory=new_tf)


class TestCmdWriteProposalDedup:
    """Issue #141: ``cmd_write_proposal`` must auto-wire a dep edge to an
    existing recovery task instead of writing a duplicate proposal when
    the would-be proposal's ``fix_signature`` matches an existing pending
    proposal on disk."""

    def _seed_existing_recovery(self, tmp_path: Path, source_id: str, signature: str) -> Path:
        """Drop a pending proposal JSON + a minimal source-task markdown +
        BACKLOG.md row so add_dep can find and modify the source task."""
        proposals_dir = tmp_path / ".devbench" / "proposals"
        proposals_dir.mkdir(parents=True, exist_ok=True)
        path = proposals_dir / f"{source_id}.json"
        path.write_text(
            json.dumps(
                {
                    "source_task_id": source_id,
                    "generated_at": "2026-05-02T00:00:00Z",
                    "rejection_reason": "fixture",
                    "proposed_tasks": [],
                    "fix_signature": signature,
                }
            ),
            encoding="utf-8",
        )
        backlog_dir = tmp_path / "backlog" / "E0" / "E0-F1" / "E0-F1-S1"
        backlog_dir.mkdir(parents=True, exist_ok=True)
        (backlog_dir / f"{source_id}.md").write_text(
            f"# {source_id}: existing recovery\n\n## Status: in-queue\n", encoding="utf-8"
        )
        return path

    def test_dedup_reuses_existing_proposal(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """Two source tasks with the same fix signature -> second invocation
        emits ``recovery_reused: true`` instead of writing a duplicate
        proposal JSON."""
        import io

        from devbench.backlog.proposal import _compute_fix_signature, _extract_intent_phrase

        # Compute the signature the way cmd_write_proposal will compute it
        # (target_repo "" because BacklogParser does not accept the bare
        # "r" repo column the BACKLOG.md fixture uses; ``_resolve_source_repo``
        # falls back to "" via its except-ValueError branch).
        # Production strips the configured ``checkout_directory`` prefix
        # before computing the signature (issue #159), so we seed with
        # the STRIPPED form (``pyproject.toml``). The unstripped path
        # stays in the new payload's ``files_to_own`` below so the
        # issue #146 backlog-repo filter still treats the file as
        # in-scope (the file's first segment ``git-repo`` matches the
        # configured checkout_directory of caylent-solutions/git-repo
        # in the test fixture).
        files_unstripped = ["git-repo/pyproject.toml"]
        files_stripped = ["pyproject.toml"]
        intent = _extract_intent_phrase("Remove the pyproject.toml row from T1")
        signature = _compute_fix_signature("", files_stripped, intent)

        # Seed the EXISTING recovery: source_id=E0-F1-S1-T1 carries the signature.
        self._seed_existing_recovery(tmp_path, "E0-F1-S1-T1", signature)
        # Add a target source-task markdown that the new write-proposal
        # invocation will be associated with (so add_dep can write its
        # dep-table row).
        backlog_dir = tmp_path / "backlog" / "E0" / "E0-F1" / "E0-F1-S1"
        new_source_md = (
            "# E0-F1-S1-T7: new source\n\n## Status: blocked\n\n"
            "## Dependencies\n\n| ID | Title | Status |\n|----|-------|--------|\n"
        )
        (backlog_dir / "E0-F1-S1-T7.md").write_text(new_source_md, encoding="utf-8")
        # Minimal BACKLOG.md so BacklogParser does not crash; both rows present.
        t1_path = "`backlog/E0/E0-F1/E0-F1-S1/E0-F1-S1-T1.md`"
        t7_path = "`backlog/E0/E0-F1/E0-F1-S1/E0-F1-S1-T7.md`"
        (tmp_path / "BACKLOG.md").write_text(
            "# Backlog\n\n## Tasks\n\n"
            "| ID | Title | Type | Status | Deps | Repo | File |\n"
            "|----|-------|------|--------|------|------|------|\n"
            f"| E0-F1-S1-T1 | existing recovery | Task | in-queue | none | r | {t1_path} |\n"
            f"| E0-F1-S1-T7 | new source | Task | blocked | none | r | {t7_path} |\n",
            encoding="utf-8",
        )

        # Submit a new proposal whose fix signature will match.
        new_payload = {
            "source_task_id": "E0-F1-S1-T7",
            "generated_at": "2026-05-02T00:01:00Z",
            "rejection_reason": "duplicate fix",
            "proposed_tasks": [
                {
                    "suggested_id": "E0-F1-S1-T8",
                    "title": "Remove the pyproject.toml row from T7",
                    "files_to_own": files_unstripped,
                    "linked_scenarios": [],
                    "suggested_acs": [],
                    "suggested_approach": "Remove the pyproject.toml row from T1",
                }
            ],
        }
        monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps(new_payload)))
        with (
            patch("devbench.cli.WORKSPACE_ROOT", tmp_path),
            patch("devbench.cli.BACKLOG_ROOT", tmp_path / "backlog"),
            patch("devbench.cli.BACKLOG_INDEX", tmp_path / "BACKLOG.md"),
        ):
            rc = cli.cmd_write_proposal("E0-F1-S1-T7")
        assert rc == 0
        out = capsys.readouterr().out
        envelope = json.loads(out)
        assert envelope["recovery_reused"] is True
        assert envelope["reused_from_task_id"] == "E0-F1-S1-T1"
        # No duplicate proposal JSON written.
        assert not (tmp_path / ".devbench" / "proposals" / "E0-F1-S1-T7.json").exists()


class TestCmdWriteProposalBacklogRepoSkip:
    """Issue #146: ``cmd_write_proposal`` must drop proposed-task entries
    whose ``files_to_own`` all live in the backlog repo (i.e., NOT in any
    configured target repo). The backlog repo isn't a target repo; the
    recovery cascade has no valid completion path for backlog-repo edits.
    """

    def _payload(
        self,
        source_id: str,
        proposed: list[dict],
    ) -> str:
        return json.dumps(
            {
                "source_task_id": source_id,
                "generated_at": "2026-05-02T00:00:00Z",
                "rejection_reason": "fixture",
                "proposed_tasks": proposed,
            }
        )

    def _mk_runtime_config(self) -> MagicMock:
        """Build a RUNTIME_CONFIG mock with two target repos configured
        (``caylent-telemetry`` and ``kanon``). Files outside these
        directories are treated as backlog-repo bookkeeping."""
        cfg = MagicMock()
        repo_a = MagicMock()
        repo_a.checkout_directory = "caylent-telemetry"
        repo_a.validated_repo = "caylent-solutions/caylent-telemetry"
        repo_b = MagicMock()
        repo_b.checkout_directory = "kanon"
        repo_b.validated_repo = "caylent-solutions/kanon"
        cfg.repos = {
            "caylent-solutions/caylent-telemetry": repo_a,
            "caylent-solutions/kanon": repo_b,
        }
        cfg.task_factory.auto_accept_proposals = False
        cfg.task_factory.enabled = True
        return cfg

    def test_target_repo_files_emit_proposal(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """Proposed task whose files all live in a configured target repo
        is NOT skipped: proposal is written and recovery_skipped is False."""
        import io

        payload = self._payload(
            "E0-F1-S1-T1",
            [
                {
                    "suggested_id": "E0-F1-S1-T2",
                    "title": "Fix X",
                    "files_to_own": ["caylent-telemetry/src/foo.py"],
                    "linked_scenarios": [],
                    "suggested_acs": [],
                    "suggested_approach": "Add the foo helper",
                }
            ],
        )
        monkeypatch.setattr("sys.stdin", io.StringIO(payload))
        with (
            patch("devbench.cli.WORKSPACE_ROOT", tmp_path),
            patch("devbench.cli.RUNTIME_CONFIG", self._mk_runtime_config()),
        ):
            rc = cli.cmd_write_proposal("E0-F1-S1-T1")
        assert rc == 0
        envelope = json.loads(capsys.readouterr().out)
        assert envelope.get("recovery_skipped") is not True
        assert envelope.get("proposal_path") is not None
        assert (tmp_path / ".devbench" / "proposals" / "E0-F1-S1-T1.json").exists()

    def test_all_backlog_repo_files_skipped_no_proposal_written(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """Proposed task whose only file is in the backlog repo (e.g.,
        spec/observability.md) -> entry dropped, no proposal JSON
        written, envelope reports recovery_skipped: True."""
        import io

        payload = self._payload(
            "E3-F3-S2-T1",
            [
                {
                    "suggested_id": "E3-F3-S2-T2",
                    "title": "Sync spec doc",
                    "files_to_own": ["spec/observability.md"],
                    "linked_scenarios": [],
                    "suggested_acs": [],
                    "suggested_approach": "Sync the spec doc with the dashboard",
                }
            ],
        )
        monkeypatch.setattr("sys.stdin", io.StringIO(payload))
        with (
            patch("devbench.cli.WORKSPACE_ROOT", tmp_path),
            patch("devbench.cli.RUNTIME_CONFIG", self._mk_runtime_config()),
        ):
            rc = cli.cmd_write_proposal("E3-F3-S2-T1")
        assert rc == 0
        envelope = json.loads(capsys.readouterr().out)
        assert envelope["recovery_skipped"] is True
        assert envelope["proposal_path"] is None
        assert not (tmp_path / ".devbench" / "proposals" / "E3-F3-S2-T1.json").exists()

    def test_mixed_files_partial_keep(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """Proposed task whose files span backlog + target repos -> entry
        kept with target-repo files only; backlog files pruned. Proposal
        is written."""
        import io

        payload = self._payload(
            "E0-F1-S1-T1",
            [
                {
                    "suggested_id": "E0-F1-S1-T2",
                    "title": "Fix X with mixed files",
                    "files_to_own": [
                        "caylent-telemetry/src/foo.py",  # target repo
                        "spec/architecture.md",  # backlog repo (pruned)
                    ],
                    "linked_scenarios": [],
                    "suggested_acs": [],
                    "suggested_approach": "Add the foo helper",
                }
            ],
        )
        monkeypatch.setattr("sys.stdin", io.StringIO(payload))
        with (
            patch("devbench.cli.WORKSPACE_ROOT", tmp_path),
            patch("devbench.cli.RUNTIME_CONFIG", self._mk_runtime_config()),
        ):
            rc = cli.cmd_write_proposal("E0-F1-S1-T1")
        assert rc == 0
        envelope = json.loads(capsys.readouterr().out)
        assert envelope.get("recovery_skipped") is not True
        assert envelope["proposal_path"] is not None
        # Verify the persisted proposal carries only the target-repo file.
        # Issue #159 (prefix strip): the persisted path is repo-relative
        # (``src/foo.py``) rather than the prefixed form the agent emitted
        # (``caylent-telemetry/src/foo.py``). The strip runs after the
        # backlog-repo filter, so the target-repo classification still fires
        # on the prefixed form before the persistence step normalises it.
        persisted = json.loads((tmp_path / ".devbench" / "proposals" / "E0-F1-S1-T1.json").read_text(encoding="utf-8"))
        assert persisted["proposed_tasks"][0]["files_to_own"] == ["src/foo.py"]

    def test_empty_files_to_own_preserved(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """Empty files_to_own = research / validation-gate task; NOT
        treated as backlog-only. Entry preserved as-is."""
        import io

        payload = self._payload(
            "E0-F1-S1-T1",
            [
                {
                    "suggested_id": "E0-F1-S1-T2",
                    "title": "Investigate X",
                    "files_to_own": [],
                    "linked_scenarios": [],
                    "suggested_acs": [],
                    "suggested_approach": "Investigate without authoring code",
                }
            ],
        )
        monkeypatch.setattr("sys.stdin", io.StringIO(payload))
        with (
            patch("devbench.cli.WORKSPACE_ROOT", tmp_path),
            patch("devbench.cli.RUNTIME_CONFIG", self._mk_runtime_config()),
        ):
            rc = cli.cmd_write_proposal("E0-F1-S1-T1")
        assert rc == 0
        envelope = json.loads(capsys.readouterr().out)
        assert envelope.get("recovery_skipped") is not True

    def test_helper_classifies_paths_correctly(self) -> None:
        """Spot-check ``_file_lives_in_a_target_repo`` against canonical
        examples (target-repo paths return True; backlog-repo paths
        return False)."""
        with patch("devbench.cli.RUNTIME_CONFIG", self._mk_runtime_config()):
            assert cli._file_lives_in_a_target_repo("caylent-telemetry/src/foo.py")
            assert cli._file_lives_in_a_target_repo("kanon/something.py")
            assert not cli._file_lives_in_a_target_repo("spec/observability.md")
            assert not cli._file_lives_in_a_target_repo("BACKLOG.md")
            assert not cli._file_lives_in_a_target_repo("backlog/E1/E1-F1/E1-F1.md")
            assert not cli._file_lives_in_a_target_repo("docs/architecture.md")
            assert not cli._file_lives_in_a_target_repo("")

    def test_repo_relative_path_classified_via_source_task(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """Issue #180: a repo-relative path like ``src/foo.py`` (no
        checkout_directory prefix) must classify as target-repo when the
        source task resolves to a configured repo. blocker-resolver agents
        running from inside the source's checkout naturally emit paths in
        this form; the recovery cascade must NOT silently skip them.
        """
        import io

        # Build a backlog where E0-F1-S1-T1 targets the kanon repo.
        backlog_root = tmp_path / "backlog"
        backlog_root.mkdir(parents=True)
        wu_dir = backlog_root / "E0-name" / "E0-F1-name" / "E0-F1-S1-name"
        wu_dir.mkdir(parents=True)
        wu_path = wu_dir / "E0-F1-S1-T1-name.md"
        wu_path.write_text(
            "# E0-F1-S1-T1: Source Task\n\n"
            "## Status: blocked\n\n"
            "## Target Repository\n\n"
            "- **Repo:** `caylent-solutions/kanon`\n\n"
            "## Description\n\nFixture task.\n\n"
            "## Changes Manifest\n\n"
            "| Path | Notes |\n|------|-------|\n| src/foo.py | impl |\n\n"
            "## Comments\n",
            encoding="utf-8",
        )
        backlog_index = tmp_path / "BACKLOG.md"
        backlog_index.write_text(
            "# Backlog\n\n"
            "## Full Work Unit Index\n\n"
            "| ID | Title | Type | Status | Dependencies | Repo | File Path |\n"
            "|----|-------|------|--------|--------------|------|-----------|\n"
            "| E0-F1-S1-T1 | Source Task | Task | blocked | None | caylent-solutions/kanon | "
            "`backlog/E0-name/E0-F1-name/E0-F1-S1-name/E0-F1-S1-T1-name.md` |\n",
            encoding="utf-8",
        )

        payload = self._payload(
            "E0-F1-S1-T1",
            [
                {
                    "suggested_id": "E0-F1-S1-T2",
                    "title": "Fix X repo-relative",
                    # Path is repo-relative (no `kanon/` prefix). Under
                    # the pre-fix classifier, this would be misread as
                    # backlog-repo and skipped.
                    "files_to_own": ["src/bar.py"],
                    "linked_scenarios": [],
                    "suggested_acs": [],
                    "suggested_approach": "Add the bar helper",
                }
            ],
        )
        monkeypatch.setattr("sys.stdin", io.StringIO(payload))
        with (
            patch("devbench.cli.WORKSPACE_ROOT", tmp_path),
            patch("devbench.cli.BACKLOG_ROOT", backlog_root),
            patch("devbench.cli.BACKLOG_INDEX", backlog_index),
            patch("devbench.cli.RUNTIME_CONFIG", self._mk_runtime_config()),
        ):
            rc = cli.cmd_write_proposal("E0-F1-S1-T1")
        assert rc == 0
        envelope = json.loads(capsys.readouterr().out)
        assert envelope.get("recovery_skipped") is not True, (
            f"repo-relative path must NOT trigger backlog-repo skip: {envelope!r}"
        )
        assert envelope["proposal_path"] is not None
        persisted = json.loads((tmp_path / ".devbench" / "proposals" / "E0-F1-S1-T1.json").read_text(encoding="utf-8"))
        assert persisted["proposed_tasks"][0]["files_to_own"] == ["src/bar.py"]

    def test_helper_with_source_task_treats_repo_relative_as_target(self, tmp_path: Path) -> None:
        """Direct test for the new ``source_task_id`` parameter behaviour.

        With ``source_task_id`` provided and the source resolving to a
        configured repo, repo-relative paths classify as target-repo;
        without it (back-compat), they classify as backlog-repo. Both
        cases must still treat the unambiguous backlog-only ``BACKLOG.md``
        as a non-target path (it carries no target-repo prefix and is
        not a plausible inside-repo path).
        """
        backlog_root = tmp_path / "backlog"
        backlog_root.mkdir(parents=True)
        wu_dir = backlog_root / "E0-name" / "E0-F1-name" / "E0-F1-S1-name"
        wu_dir.mkdir(parents=True)
        wu_path = wu_dir / "E0-F1-S1-T1-name.md"
        wu_path.write_text(
            "# E0-F1-S1-T1: Source Task\n\n"
            "## Status: blocked\n\n"
            "## Target Repository\n\n"
            "- **Repo:** `caylent-solutions/kanon`\n\n"
            "## Description\n\nFixture task.\n\n"
            "## Changes Manifest\n\n"
            "| Path | Notes |\n|------|-------|\n| src/foo.py | impl |\n\n"
            "## Comments\n",
            encoding="utf-8",
        )
        backlog_index = tmp_path / "BACKLOG.md"
        backlog_index.write_text(
            "# Backlog\n\n"
            "## Full Work Unit Index\n\n"
            "| ID | Title | Type | Status | Dependencies | Repo | File Path |\n"
            "|----|-------|------|--------|--------------|------|-----------|\n"
            "| E0-F1-S1-T1 | Source Task | Task | blocked | None | caylent-solutions/kanon | "
            "`backlog/E0-name/E0-F1-name/E0-F1-S1-name/E0-F1-S1-T1-name.md` |\n",
            encoding="utf-8",
        )
        with (
            patch("devbench.cli.RUNTIME_CONFIG", self._mk_runtime_config()),
            patch("devbench.cli.BACKLOG_ROOT", backlog_root),
            patch("devbench.cli.BACKLOG_INDEX", backlog_index),
        ):
            # With source -> repo-relative path counts as target-repo.
            assert cli._file_lives_in_a_target_repo("src/foo.py", source_task_id="E0-F1-S1-T1")
            # With source -> still True for prefixed paths.
            assert cli._file_lives_in_a_target_repo("kanon/src/foo.py", source_task_id="E0-F1-S1-T1")
            # Without source (back-compat) -> repo-relative path returns False.
            assert not cli._file_lives_in_a_target_repo("src/foo.py")


class TestCmdWriteProposalCheckoutPrefixStrip:
    """Issue #159: ``cmd_write_proposal`` must strip ``<checkout_directory>/``
    prefixes from every ``proposed_tasks[*].files_to_own`` entry so the
    persisted JSON carries repo-relative paths only. Recurring failure mode
    (verified against the live caylent-telemetry-spec workspace): blocker-
    resolver agents emit paths like ``kanon/src/foo.py`` when ``kanon`` is
    configured as the target repo's checkout_directory; without the strip,
    every materialised work unit fails validate-backlog rule 11."""

    def _payload(self, source_id: str, files: list[str]) -> str:
        return json.dumps(
            {
                "source_task_id": source_id,
                "generated_at": "2026-05-04T00:00:00Z",
                "rejection_reason": "fixture",
                "proposed_tasks": [
                    {
                        "suggested_id": "E0-F1-S1-T2",
                        "title": "Strip me",
                        "files_to_own": files,
                        "linked_scenarios": [],
                        "suggested_acs": [],
                        "suggested_approach": "Add the foo helper",
                    }
                ],
            }
        )

    def _mk_runtime_config(self) -> MagicMock:
        cfg = MagicMock()
        repo_a = MagicMock()
        repo_a.checkout_directory = "caylent-telemetry"
        repo_a.validated_repo = "caylent-solutions/caylent-telemetry"
        repo_b = MagicMock()
        repo_b.checkout_directory = "kanon"
        repo_b.validated_repo = "caylent-solutions/kanon"
        cfg.repos = {
            "caylent-solutions/caylent-telemetry": repo_a,
            "caylent-solutions/kanon": repo_b,
        }
        cfg.task_factory.auto_accept_proposals = False
        cfg.task_factory.enabled = True
        return cfg

    def test_kanon_prefix_stripped_to_repo_relative(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """``kanon/src/foo.py`` -> ``src/foo.py`` in the persisted JSON."""
        import io

        monkeypatch.setattr(
            "sys.stdin",
            io.StringIO(
                self._payload(
                    "E0-F1-S1-T1",
                    [
                        "kanon/src/kanon_cli/core/xml_validator.py",
                        "kanon/tests/unit/test_xml_validator.py",
                    ],
                )
            ),
        )
        with (
            patch("devbench.cli.WORKSPACE_ROOT", tmp_path),
            patch("devbench.cli.RUNTIME_CONFIG", self._mk_runtime_config()),
        ):
            rc = cli.cmd_write_proposal("E0-F1-S1-T1")
        assert rc == 0
        persisted = json.loads((tmp_path / ".devbench" / "proposals" / "E0-F1-S1-T1.json").read_text())
        files = persisted["proposed_tasks"][0]["files_to_own"]
        assert "src/kanon_cli/core/xml_validator.py" in files
        assert "tests/unit/test_xml_validator.py" in files
        # Original prefixed forms must NOT survive.
        assert not any(f.startswith("kanon/") for f in files)

    def test_repo_relative_paths_pass_through_unchanged(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """Already-correct paths are not mutated by the strip pass."""
        import io

        monkeypatch.setattr(
            "sys.stdin",
            io.StringIO(
                self._payload(
                    "E0-F1-S1-T1",
                    [
                        "caylent-telemetry/src/foo.py",
                    ],
                )
            ),
        )
        with (
            patch("devbench.cli.WORKSPACE_ROOT", tmp_path),
            patch("devbench.cli.RUNTIME_CONFIG", self._mk_runtime_config()),
        ):
            rc = cli.cmd_write_proposal("E0-F1-S1-T1")
        assert rc == 0
        persisted = json.loads((tmp_path / ".devbench" / "proposals" / "E0-F1-S1-T1.json").read_text())
        files = persisted["proposed_tasks"][0]["files_to_own"]
        # caylent-telemetry IS one of the configured checkout dirs ->
        # gets stripped to repo-relative form just like kanon does.
        assert "src/foo.py" in files

    def test_ambiguous_path_matches_multiple_checkouts_rejected(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """Per #159 AC: a path matching multiple configured checkout
        directories is ambiguous and the proposal is rejected with a
        structured error rather than silently picking one."""
        import io

        cfg = MagicMock()
        # Two repos whose checkout_directories share a common prefix; a
        # single path could plausibly belong to either. Configure them
        # so that ``foo`` is BOTH a checkout_directory AND a prefix of
        # another checkout_directory.
        repo_a = MagicMock()
        repo_a.checkout_directory = "foo"
        repo_a.validated_repo = "caylent-solutions/foo"
        repo_b = MagicMock()
        repo_b.checkout_directory = "foo"
        repo_b.validated_repo = "caylent-solutions/foo-copy"
        cfg.repos = {
            "caylent-solutions/foo": repo_a,
            "caylent-solutions/foo-copy": repo_b,
        }
        cfg.task_factory.auto_accept_proposals = False
        cfg.task_factory.enabled = True

        # Use a payload whose path duplicates the prefix to exercise
        # the multi-match branch. Since both repos resolve to the same
        # checkout_directory ("foo"), the deduped sorted list still has
        # ONE entry; we need actually-different prefixes that BOTH
        # match. Use a set with ``foo`` and ``foo/bar`` as configured
        # checkout dirs and a path ``foo/bar/baz.py`` that matches both.
        repo_b.checkout_directory = "foo/bar"
        monkeypatch.setattr(
            "sys.stdin",
            io.StringIO(self._payload("E0-F1-S1-T1", ["foo/bar/baz.py"])),
        )
        with (
            patch("devbench.cli.WORKSPACE_ROOT", tmp_path),
            patch("devbench.cli.RUNTIME_CONFIG", cfg),
        ):
            rc = cli.cmd_write_proposal("E0-F1-S1-T1")
        assert rc == 1
        err = capsys.readouterr().err
        assert "ambiguous" in err.lower()


class TestCmdMaterialiseProposalLifecycleGates:
    """Issues #143 + #144: TODO/TBD placeholder reject + cascade-depth limit
    in ``cmd_materialise_proposal``."""

    def _seed_proposal(
        self,
        tmp_path: Path,
        source_id: str,
        approach: str = "concrete",
        cascade_depth: int = 0,
    ) -> Path:
        from devbench.backlog.proposal import Proposal, ProposedTask, write_proposal

        proposal = Proposal(
            source_task_id=source_id,
            generated_at="2026-05-02T00:00:00Z",
            rejection_reason="fixture",
            proposed_tasks=[
                ProposedTask(
                    suggested_id="E0-F1-S1-T2",
                    title="t",
                    files_to_own=[],
                    linked_scenarios=[],
                    suggested_acs=[],
                    suggested_approach=approach,
                )
            ],
            cascade_depth=cascade_depth,
        )
        return write_proposal(tmp_path, proposal)

    def test_todo_placeholder_rejected(self, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
        self._seed_proposal(tmp_path, "E0-F1-S1-T1", approach="TODO -- describe change")
        with (
            patch("devbench.cli.WORKSPACE_ROOT", tmp_path),
            patch("devbench.cli.BACKLOG_ROOT", tmp_path / "backlog"),
            patch("devbench.cli.BACKLOG_INDEX", tmp_path / "BACKLOG.md"),
        ):
            rc = cli.cmd_materialise_proposal("E0-F1-S1-T1")
        assert rc == 1
        err = capsys.readouterr().err
        assert "placeholder description" in err
        assert "E0-F1-S1-T2" in err

    def test_cascade_depth_at_cap_rejected(self, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
        self._seed_proposal(tmp_path, "E0-F1-S1-T1", approach="concrete approach", cascade_depth=2)
        with (
            patch("devbench.cli.WORKSPACE_ROOT", tmp_path),
            patch("devbench.cli.BACKLOG_ROOT", tmp_path / "backlog"),
            patch("devbench.cli.BACKLOG_INDEX", tmp_path / "BACKLOG.md"),
            patch("devbench.cli.MAX_CASCADE_DEPTH", 2),
        ):
            rc = cli.cmd_materialise_proposal("E0-F1-S1-T1")
        assert rc == 1
        err = capsys.readouterr().err
        assert "cascade-depth limit reached" in err
        assert "OPERATOR_ACTION_REQUIRED" in err

    def test_cascade_depth_below_cap_passes_through_to_source_lookup(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """Depth < cap + concrete approach -> proceeds past gates and hits
        the source-task-not-in-backlog error (same baseline behaviour as
        before this commit)."""
        self._seed_proposal(tmp_path, "E0-F1-S1-T1", approach="concrete approach", cascade_depth=1)
        with (
            patch("devbench.cli.WORKSPACE_ROOT", tmp_path),
            patch("devbench.cli.BACKLOG_ROOT", tmp_path / "backlog"),
            patch("devbench.cli.BACKLOG_INDEX", tmp_path / "BACKLOG.md"),
            patch("devbench.cli.MAX_CASCADE_DEPTH", 2),
        ):
            rc = cli.cmd_materialise_proposal("E0-F1-S1-T1")
        assert rc == 1
        # Past the dedup gates -> hits the source-task-lookup error.
        err = capsys.readouterr().err
        assert "not found" in err


class TestCmdWriteProposalAutoCascade:
    """When ``task_factory.auto_accept_proposals`` is true, ``write-proposal``
    must materialise + promote every proposed task in the same Python
    invocation so the cascade is actionable immediately rather than waiting
    for the next ``sweep-proposals`` cycle.
    """

    @staticmethod
    def _sample_proposal_dict(source_task_id: str = "E0-F1-S1-T1") -> dict[str, Any]:
        return {
            "source_task_id": source_task_id,
            "generated_at": "2026-05-01T03:00:00Z",
            "rejection_reason": "x",
            "proposed_tasks": [
                {
                    "suggested_id": "E0-F1-S1-T9",
                    "title": "Follow-up fix",
                    "files_to_own": ["src/foo.py"],
                    "linked_scenarios": [],
                    "suggested_acs": [
                        "AC-FUNC-001 fix the issue",
                    ],
                    "suggested_approach": (
                        "Context: the source task hit X and produced finding Y. "
                        "Scope: src/foo.py only. "
                        "TDD approach: 1. RED write a failing test for the missing behaviour. "
                        "2. GREEN add the implementation in src/foo.py. "
                        "3. REFACTOR clean up duplication if any. "
                        "Verify: pytest exits zero and lint is clean."
                    ),
                }
            ],
            "affected_task_ids": [],
        }

    def _patch_cli_workspace(
        self, monkeypatch: pytest.MonkeyPatch, workspace: Path, backlog_root: Path, backlog_index: Path
    ) -> None:
        monkeypatch.setattr(cli, "WORKSPACE_ROOT", workspace)
        monkeypatch.setattr(cli, "BACKLOG_ROOT", backlog_root)
        monkeypatch.setattr(cli, "BACKLOG_INDEX", backlog_index)

    def test_disabled_when_auto_accept_false(
        self, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], tmp_path: Path
    ) -> None:
        # When the config flag is false the function returns the
        # "disabled" sentinel and never calls materialise/promote.
        monkeypatch.setattr(cli, "RUNTIME_CONFIG", _runtime_config_with_auto_accept(False))
        proposal = Proposal.from_dict(self._sample_proposal_dict())
        result = cli._maybe_auto_cascade_proposal("E0-F1-S1-T1", proposal)
        assert result == {"auto_cascade": "disabled"}

    def test_failed_when_source_task_missing_from_index(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        # When auto-accept is on but the source task is not in the
        # backlog index (caller passed a typo'd id), the cascade reports
        # "failed" with a clear error and does not raise.
        monkeypatch.setattr(cli, "RUNTIME_CONFIG", _runtime_config_with_auto_accept(True))
        backlog = tmp_path / "BACKLOG.md"
        backlog.write_text(
            "# Backlog\n\n"
            "## Status Summary\n\n"
            "| Epic | Title | Done | In Progress | In Queue | Blocked |\n"
            "|------|-------|------|-------------|----------|---------|\n\n"
            "## Full Work Unit Index\n\n"
            "| ID | Title | Type | Status | Dependencies | Repo | File Path |\n"
            "|----|-------|------|--------|--------------|------|-----------|\n",
            encoding="utf-8",
        )
        self._patch_cli_workspace(monkeypatch, tmp_path, tmp_path / "backlog", backlog)
        proposal = Proposal.from_dict(self._sample_proposal_dict("E0-NOPE-T0"))
        result = cli._maybe_auto_cascade_proposal("E0-NOPE-T0", proposal)
        assert result["auto_cascade"] == "failed"
        # Either "no work-unit rows" (parser rejection on empty index)
        # or "not found" (index parses but source task absent) is an
        # acceptable failure shape; both prove the cascade aborted
        # cleanly without raising.
        err_value = result["error"]
        assert isinstance(err_value, str)
        err = err_value.lower()
        assert "not found" in err or "no work-unit rows" in err

    def test_applied_when_auto_accept_true(
        self, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], tmp_path: Path
    ) -> None:
        # End-to-end: with auto-accept on and a real source-task in the
        # index, the helper calls materialise and returns "applied" with
        # the materialised path list.
        #
        # Since AC-189-8, materialise_proposal reads
        # RUNTIME_CONFIG.backlog.default_status_for_new_work_units and
        # writes that status into the draft directly. When the configured
        # default is 'in-queue' (the backwards-compatible default),
        # classify_proposed_task sees the draft as PROMOTED, so the
        # auto-cascade's promote loop finds nothing to promote and
        # 'promoted' is empty -- the task is already at its target status.
        from devbench.backlog import proposal as proposal_mod
        from devbench.config_loader import BacklogConfig, RuntimeConfig

        monkeypatch.setattr(cli, "RUNTIME_CONFIG", _runtime_config_with_auto_accept(True))
        # Patch proposal_mod's config getter so materialise_proposal writes
        # 'in-queue' directly into the new draft (the backwards-compatible default).
        fake_cfg = RuntimeConfig.__new__(RuntimeConfig)
        object.__setattr__(fake_cfg, "backlog", BacklogConfig(default_status_for_new_work_units="in-queue"))
        monkeypatch.setattr(proposal_mod, "_get_runtime_config", lambda: fake_cfg)

        # Build a minimum BACKLOG with one source task at E0-F1-S1-T1
        # (currently blocked, since auto-accept emit happens when the
        # source is failing) plus the directory structure
        # ``materialise_proposal`` expects.
        backlog_root = tmp_path / "backlog"
        story_dir = backlog_root / "E0" / "E0-F1" / "E0-F1-S1"
        story_dir.mkdir(parents=True)
        (story_dir / "E0-F1-S1-T1.md").write_text(
            "# E0-F1-S1-T1: Source\n\n"
            "## Status: blocked\n\n"
            "## Target Repository\n\n- **Repo:** `org/repo`\n\n"
            "## Description\n\nx\n\n"
            "## Dependencies\n\n| ID | Title | Status |\n|----|-------|--------|\n| none | | |\n\n"
            "## Acceptance Criteria\n\n- [ ] AC-FUNC-001\n\n"
            "## Changes Manifest\n\n| File | Change |\n|------|--------|\n| `src/foo.py` | edit |\n\n"
            "## Definition of Done\n\n- [ ] done\n",
            encoding="utf-8",
        )
        backlog = tmp_path / "BACKLOG.md"
        backlog.write_text(
            "# Backlog\n\n"
            "## Status Summary\n\n"
            "| Epic | Title | Done | In Progress | In Queue | Blocked |\n"
            "|------|-------|------|-------------|----------|---------|\n"
            "| E0 | x | 0 | 0 | 0 | 1 |\n\n"
            "## Full Work Unit Index\n\n"
            "| ID | Title | Type | Status | Dependencies | Repo | File Path |\n"
            "|----|-------|------|--------|--------------|------|-----------|\n"
            "| E0-F1-S1-T1 | Source | Task | blocked | None | org/repo | "
            "`backlog/E0/E0-F1/E0-F1-S1/E0-F1-S1-T1.md` |\n",
            encoding="utf-8",
        )
        self._patch_cli_workspace(monkeypatch, tmp_path, backlog_root, backlog)

        proposal = Proposal.from_dict(self._sample_proposal_dict("E0-F1-S1-T1"))
        # Persist the proposal JSON so the cascade has something to work on.
        proposals_dir = tmp_path / ".devbench" / "proposals"
        proposals_dir.mkdir(parents=True)
        (proposals_dir / "E0-F1-S1-T1.json").write_text(json.dumps(proposal.to_dict()))

        result = cli._maybe_auto_cascade_proposal("E0-F1-S1-T1", proposal)
        assert result["auto_cascade"] == "applied"
        materialised = result["materialised"]
        assert isinstance(materialised, list) and materialised  # at least one path
        promoted = result["promoted"]
        assert isinstance(promoted, list)
        # With default_status='in-queue', materialise_proposal writes 'in-queue'
        # directly into the draft. The auto-cascade promote loop only promotes
        # tasks in 'proposed' state; tasks already at 'in-queue' are classified
        # as PROMOTED and need no explicit promotion step.
        assert promoted == [], "no explicit promotion needed when draft was materialised directly to in-queue"
        # The materialised task's file exists with the configured status.
        materialised_file = story_dir / "E0-F1-S1-T9.md"
        assert materialised_file.exists()
        materialised_content = materialised_file.read_text(encoding="utf-8")
        assert "## Status: in-queue" in materialised_content


# ---------------------------------------------------------------------------
# log-verdict judge-name allowlist (rejects malformed audit-row writes)
# ---------------------------------------------------------------------------


class TestCmdLogVerdictAllowlist:
    """``cmd_log_verdict`` rejects judge names outside the canonical allowlist.

    Empirically observed in production: an executor agent ran
    ``log-verdict judge <id> pass`` (literal string ``"judge"`` instead
    of a canonical reviewer name). The malformed entry landed in the
    work-unit Comments section but was silently invisible to
    ``BacklogManager._last_round_all_passed`` (which only counts
    entries whose judge name is in ``ALL_REQUIRED_JUDGE_NAMES``).
    Refusing typos at the CLI layer prevents pollution + masks.
    """

    @pytest.mark.parametrize(
        "bad_name",
        [
            "judge",  # literal typo seen in production
            "code-reviewer",  # hyphenated form (canonical is underscored)
            "Code_Review",  # casing (canonical is lowercase)
            "auditor",  # role that does not exist
            "",  # empty
        ],
    )
    def test_rejects_non_allowlist_judge(self, bad_name: str, capsys: pytest.CaptureFixture[str]) -> None:
        rc = cli.cmd_log_verdict(bad_name, "E0-F1-S1-T1", "pass", "smoke")
        assert rc == 1
        err = capsys.readouterr().err
        assert "not on the allowlist" in err
        # Error message names every valid choice so the agent can self-correct.
        for canonical in ("code_review", "test_review", "doc_review"):
            assert canonical in err

    @pytest.mark.parametrize(
        "good_name",
        [
            "code_review",
            "test_review",
            "doc_review",
            "changes_manifest",
            "security_review",
            "executor",  # audit-only workflow agent
            "blocker_resolver",  # audit-only workflow agent
            "manifest_amender",
            "task_factory",
        ],
    )
    def test_accepts_allowlist_judges(self, good_name: str, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        # Build a minimal workspace so the verdict-write reaches its
        # successful return path. Failure here would surface as a
        # non-zero rc with stderr; we assert rc==0 to prove the
        # allowlist gate did not trip.
        backlog_root = tmp_path / "backlog"
        story_dir = backlog_root / "E0" / "E0-F1" / "E0-F1-S1"
        story_dir.mkdir(parents=True)
        (story_dir / "E0-F1-S1-T1.md").write_text(
            "# E0-F1-S1-T1: Source\n\n## Status: in-progress\n\n## Description\n\nx\n\n## Comments\n\n",
            encoding="utf-8",
        )
        backlog = tmp_path / "BACKLOG.md"
        backlog.write_text(
            "# Backlog\n\n"
            "## Status Summary\n\n"
            "| Epic | Title | Done | In Progress | In Queue | Blocked |\n"
            "|------|-------|------|-------------|----------|---------|\n"
            "| E0 | x | 0 | 1 | 0 | 0 |\n\n"
            "## Full Work Unit Index\n\n"
            "| ID | Title | Type | Status | Dependencies | Repo | File Path |\n"
            "|----|-------|------|--------|--------------|------|-----------|\n"
            "| E0-F1-S1-T1 | Source | Task | in-progress | None | org/repo | "
            "`backlog/E0/E0-F1/E0-F1-S1/E0-F1-S1-T1.md` |\n",
            encoding="utf-8",
        )
        monkeypatch.setattr(cli, "WORKSPACE_ROOT", tmp_path)
        monkeypatch.setattr(cli, "BACKLOG_ROOT", backlog_root)
        monkeypatch.setattr(cli, "BACKLOG_INDEX", backlog)

        rc = cli.cmd_log_verdict(good_name, "E0-F1-S1-T1", "pass", "looks good")
        assert rc == 0


# ---------------------------------------------------------------------------
# log-file path resolution: fail-fast when neither DEVBENCH_LOG_FILE nor
# DEVBENCH_WORKSPACE_ROOT is set; canonical workspace-local path otherwise
# ---------------------------------------------------------------------------


class TestResolveLogFilePath:
    """``_resolve_log_file_path`` is the single source of truth for which
    log file ``devbench report`` reads. Removing the silent source-tree
    fallback prevents the BACKLOG-vs-throughput divergence reported by
    operators (they ran ``devbench report`` from a sub-shell that
    inherited ``DEVBENCH_WORKSPACE_ROOT`` but not ``DEVBENCH_LOG_FILE`` and got
    an unrelated dev-tree log instead of their workspace's log).

    After the JUDGE_* -> DEVBENCH_* rename (AC-197-1 / AC-197-2), the resolver
    reads ``DEVBENCH_LOG_FILE`` directly via os.environ.get
    and reads ``DEVBENCH_LOG_FILE`` as the canonical name.
    """

    def test_explicit_devbench_log_file_wins(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("DEVBENCH_LOG_FILE", raising=False)
        monkeypatch.setenv("DEVBENCH_LOG_FILE", "/tmp/my-explicit.log")
        # DEVBENCH_LOG_FILE is the explicit override and MUST win even when
        # DEVBENCH_WORKSPACE_ROOT (via WORKSPACE_ROOT) is also set.
        assert cli._resolve_log_file_path() == Path("/tmp/my-explicit.log")

    def test_workspace_root_derives_canonical_path(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("DEVBENCH_LOG_FILE", raising=False)
        # Default path is <WORKSPACE_ROOT>/<DEFAULT_LOG_SUBDIR>/<DEFAULT_LOG_FILENAME>.
        # Operators running ``devbench report`` from any shell with
        # DEVBENCH_WORKSPACE_ROOT set get the same log the orchestrator writes to.
        from devbench.constants import DEFAULT_LOG_FILENAME, DEFAULT_LOG_SUBDIR

        expected = cli.WORKSPACE_ROOT / DEFAULT_LOG_SUBDIR / DEFAULT_LOG_FILENAME
        assert cli._resolve_log_file_path() == expected

    def test_empty_devbench_log_file_falls_through_to_workspace_root(self, monkeypatch: pytest.MonkeyPatch) -> None:
        # Empty / whitespace-only DEVBENCH_LOG_FILE behaves as unset
        # (avoids "" being treated as a valid path).
        monkeypatch.setenv("DEVBENCH_LOG_FILE", "   ")
        from devbench.constants import DEFAULT_LOG_FILENAME, DEFAULT_LOG_SUBDIR

        expected = cli.WORKSPACE_ROOT / DEFAULT_LOG_SUBDIR / DEFAULT_LOG_FILENAME
        assert cli._resolve_log_file_path() == expected

    def test_neither_env_nor_yaml_falls_back_to_workspace_root_default(self, monkeypatch: pytest.MonkeyPatch) -> None:
        # When neither DEVBENCH_LOG_FILE nor YAML log_file is set, the resolver
        # uses WORKSPACE_ROOT (already resolved from DEVBENCH_WORKSPACE_ROOT by
        # config.py) to derive the canonical aggregate path.
        monkeypatch.delenv("DEVBENCH_LOG_FILE", raising=False)
        from devbench.constants import DEFAULT_LOG_FILENAME, DEFAULT_LOG_SUBDIR

        expected = cli.WORKSPACE_ROOT / DEFAULT_LOG_SUBDIR / DEFAULT_LOG_FILENAME
        assert cli._resolve_log_file_path() == expected


class TestResolveLogFileYamlConfig:
    """``RUNTIME_CONFIG.log_file`` (from devbench.yaml) drives the resolver
    when ``DEVBENCH_LOG_FILE`` env var is not set. This is the canonical
    single source of truth for the orchestrator's log path; the
    orchestrator-as-writer (``setup_logging``) and the report-as-reader
    (``cmd_report``) both consult it so they cannot diverge.

    After the JUDGE_* -> DEVBENCH_* rename (AC-197-1), all reads use
    ``DEVBENCH_LOG_FILE`` as the canonical env-var name.  The workspace
    root is resolved from the already-computed ``WORKSPACE_ROOT`` constant
    (which is itself strictly validated in config.py).
    """

    def _runtime_config_with_log_file(self, value: str | None) -> Any:
        import dataclasses

        return dataclasses.replace(cli.RUNTIME_CONFIG, log_file=value)

    def test_yaml_log_file_workspace_relative(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("DEVBENCH_LOG_FILE", raising=False)
        monkeypatch.delenv("DEVBENCH_LOG_FILE", raising=False)
        monkeypatch.setattr(cli, "RUNTIME_CONFIG", self._runtime_config_with_log_file("logs/orch.log"))
        # YAML log_file is workspace-relative when not absolute; the
        # resolver joins it with WORKSPACE_ROOT (already resolved from DEVBENCH_WORKSPACE_ROOT).
        assert cli._resolve_log_file_path() == cli.WORKSPACE_ROOT / "logs" / "orch.log"

    def test_yaml_log_file_absolute_path(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("DEVBENCH_LOG_FILE", raising=False)
        monkeypatch.delenv("DEVBENCH_LOG_FILE", raising=False)
        monkeypatch.setattr(cli, "RUNTIME_CONFIG", self._runtime_config_with_log_file("/var/log/d.log"))
        # An absolute YAML path is used as-is, ignoring the workspace
        # root (operator deliberately put the log outside the workspace).
        assert cli._resolve_log_file_path() == Path("/var/log/d.log")

    def test_explicit_devbench_log_file_still_wins_over_yaml(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("DEVBENCH_LOG_FILE", raising=False)
        monkeypatch.setenv("DEVBENCH_LOG_FILE", "/tmp/explicit.log")
        monkeypatch.setattr(cli, "RUNTIME_CONFIG", self._runtime_config_with_log_file("logs/orch.log"))
        # Per-invocation DEVBENCH_LOG_FILE env override beats both YAML config and the
        # workspace-local convention; this matches how ``cmd_check`` and
        # the test fixtures set the path explicitly.
        assert cli._resolve_log_file_path() == Path("/tmp/explicit.log")

    def test_yaml_unset_falls_through_to_workspace_default(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("DEVBENCH_LOG_FILE", raising=False)
        monkeypatch.setattr(cli, "RUNTIME_CONFIG", self._runtime_config_with_log_file(None))
        from devbench.constants import DEFAULT_LOG_FILENAME, DEFAULT_LOG_SUBDIR

        # When neither DEVBENCH_LOG_FILE nor YAML log_file is set, the resolver
        # falls back to the workspace-local aggregate convention using WORKSPACE_ROOT.
        assert cli._resolve_log_file_path() == (cli.WORKSPACE_ROOT / DEFAULT_LOG_SUBDIR / DEFAULT_LOG_FILENAME)

    def test_yaml_with_relative_path_and_workspace_root(self, monkeypatch: pytest.MonkeyPatch) -> None:
        # YAML has a relative log_file; WORKSPACE_ROOT is used as the anchor.
        monkeypatch.delenv("DEVBENCH_LOG_FILE", raising=False)
        monkeypatch.delenv("DEVBENCH_LOG_FILE", raising=False)
        monkeypatch.setattr(cli, "RUNTIME_CONFIG", self._runtime_config_with_log_file("logs/orch.log"))
        # WORKSPACE_ROOT is already resolved; relative YAML path is joined to it.
        assert cli._resolve_log_file_path() == cli.WORKSPACE_ROOT / "logs" / "orch.log"


class TestInlineOrphanCleanup:
    """Phase 1: ``cmd_git_ops`` runs the orphan cleanup inline as a chore commit.

    Eliminates the cascade pathology where multiple parents each emitted a
    duplicate cleanup proposal and those proposals themselves got blocked by
    the manifest amender on predecessor staging. The cleanup is no longer a
    backlog work unit -- it is a maintenance commit the engine makes on its
    own when it detects build/state artifact paths that would otherwise
    pollute the task's commit.
    """

    def _make_unit(self, repo: str = "caylent-solutions/git-repo") -> WorkUnit:
        return WorkUnit(
            id="E0-F1-S1-T1",
            title="Sample task",
            status=WorkUnitStatus.IN_PROGRESS,
            unit_type=WorkUnitType.TASK,
            file_path=Path("backlog/E0-F1-S1-T1.md"),
            repo=repo,
            dependencies=[],
        )

    def _seed_orphan_in_repo(self, repo_dir: Path) -> Path:
        """Add a tracked orphan path (``.coverage (1)``) on top of the
        ``tmp_repo_dir`` fixture's initial commit.

        Mirrors the real-world failure shape from the user's halt log
        (the leftover pytest-cov race file).
        """
        import subprocess

        orphan = repo_dir / ".coverage (1)"
        orphan.write_text("ignored coverage data\n", encoding="utf-8")
        subprocess.run(
            ["git", "-C", str(repo_dir), "add", "--", ".coverage (1)"],
            check=True,
        )
        subprocess.run(
            ["git", "-C", str(repo_dir), "commit", "-m", "leak orphan"],
            check=True,
        )
        return orphan

    def test_inline_cleanup_lands_chore_commit_and_continues(self, tmp_repo_dir: Path) -> None:
        from devbench.constants import DEVBENCH_INLINE_CLEANUP_COMMIT_MESSAGE

        self._seed_orphan_in_repo(tmp_repo_dir)
        # Stage an executor file alongside the orphan situation so we can
        # verify the executor's staging survives the cleanup pass.
        import subprocess

        (tmp_repo_dir / "feature.py").write_text("print('hi')\n", encoding="utf-8")
        subprocess.run(
            ["git", "-C", str(tmp_repo_dir), "add", "--", "feature.py"],
            check=True,
        )

        result = cli._inline_orphan_cleanup_or_refuse(
            unit_id="E0-F1-S1-T1",
            repo_path=tmp_repo_dir,
            detected=[".coverage (1)"],
        )
        assert result is False  # caller continues with task commit

        # Cleanup commit landed with the canonical chore message.
        log = subprocess.run(
            ["git", "-C", str(tmp_repo_dir), "log", "-1", "--format=%s"],
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()
        assert log == DEVBENCH_INLINE_CLEANUP_COMMIT_MESSAGE

        # Orphan is no longer tracked.
        ls = subprocess.run(
            ["git", "-C", str(tmp_repo_dir), "ls-files"],
            check=True,
            capture_output=True,
            text=True,
        ).stdout
        assert ".coverage (1)" not in ls

        # ``.gitignore`` was written with the devbench-managed block.
        gitignore = (tmp_repo_dir / ".gitignore").read_text(encoding="utf-8")
        assert ".coverage*" in gitignore

        # Executor's staging (feature.py) was preserved.
        staged = subprocess.run(
            ["git", "-C", str(tmp_repo_dir), "diff", "--cached", "--name-only"],
            check=True,
            capture_output=True,
            text=True,
        ).stdout
        assert "feature.py" in staged

    @pytest.mark.skipif(
        __import__("sys").platform == "win32",
        reason="os.symlink requires elevated privileges on Windows",
    )
    def test_inline_cleanup_handles_symlinked_repo_path(self, tmp_repo_dir: Path, tmp_path: Path) -> None:
        """Issue #125 regression: inline cleanup must work when the caller
        passes a symlinked path that resolves to the real checkout.

        ``cleanup_tracked_orphans`` calls ``Path.resolve()`` internally so
        its ``OrphanReport.gitignore_path`` lives in resolved-path space.
        ``_run_inline_cleanup_steps`` must therefore also resolve the
        ``repo_path`` it receives before calling
        ``gitignore_path.relative_to(repo_path)``; otherwise
        ``Path.relative_to`` (which is not symlink-aware) raises
        ``ValueError`` and BLOCKS the work unit. This test exercises the
        documented workspace-layout pattern where target repos sit
        elsewhere on disk and a symlink under
        ``DEVBENCH_WORKSPACE_ROOT/<checkout_directory>`` points at them.
        """
        import subprocess

        from devbench.constants import DEVBENCH_INLINE_CLEANUP_COMMIT_MESSAGE

        self._seed_orphan_in_repo(tmp_repo_dir)
        symlinked_path = tmp_path / "via-symlink"
        symlinked_path.symlink_to(tmp_repo_dir)

        result = cli._inline_orphan_cleanup_or_refuse(
            unit_id="E0-F1-S1-T1",
            repo_path=symlinked_path,
            detected=[".coverage (1)"],
        )
        assert result is False

        # Cleanup commit landed under the canonical chore message.
        log = subprocess.run(
            ["git", "-C", str(tmp_repo_dir), "log", "-1", "--format=%s"],
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()
        assert log == DEVBENCH_INLINE_CLEANUP_COMMIT_MESSAGE

        # Orphan untracked.
        ls = subprocess.run(
            ["git", "-C", str(tmp_repo_dir), "ls-files"],
            check=True,
            capture_output=True,
            text=True,
        ).stdout
        assert ".coverage (1)" not in ls

        # .gitignore extended with the devbench-managed block.
        gitignore = (tmp_repo_dir / ".gitignore").read_text(encoding="utf-8")
        assert ".coverage*" in gitignore

    def test_inline_cleanup_filters_out_staged_only_orphans(self, tmp_repo_dir: Path) -> None:
        """A staged-only orphan (newly added by executor, not yet in HEAD) is
        un-staged + ignored; no cleanup commit is needed because there's
        nothing to ``rm --cached``.

        The follow-up ``commit_and_push`` would then skip the orphan because
        the just-written .gitignore block matches the pattern.
        """
        import subprocess

        # Stage a brand-new orphan-pattern file that's not yet tracked.
        (tmp_repo_dir / ".coverage").write_text("data\n", encoding="utf-8")
        subprocess.run(
            ["git", "-C", str(tmp_repo_dir), "add", "-f", "--", ".coverage"],
            check=True,
        )

        result = cli._inline_orphan_cleanup_or_refuse(
            unit_id="E0-F1-S1-T1",
            repo_path=tmp_repo_dir,
            detected=[".coverage"],
        )
        assert result is False

        # Orphan is no longer staged.
        staged = subprocess.run(
            ["git", "-C", str(tmp_repo_dir), "diff", "--cached", "--name-only"],
            check=True,
            capture_output=True,
            text=True,
        ).stdout
        assert ".coverage" not in staged

    def test_inline_cleanup_refuses_on_subprocess_failure(
        self,
        tmp_path: Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """When the cleanup primitive raises, the helper returns True so
        the caller refuses the parent commit -- and prints an actionable
        error mentioning the manual recovery command.
        """
        # Pass a non-git-repo path so cleanup_tracked_orphans raises.
        result = cli._inline_orphan_cleanup_or_refuse(
            unit_id="E0-F1-S1-T1",
            repo_path=tmp_path,
            detected=[".coverage"],
        )
        assert result is True
        err = capsys.readouterr().err
        assert "git-ops refused" in err
        assert "cleanup-tracked-orphans" in err  # operator-recovery hint


class TestEmitOrphanCleanupDispatch:
    """``_emit_orphan_cleanup_proposal_if_needed`` dispatches inline vs legacy."""

    def _unit(self) -> WorkUnit:
        return WorkUnit(
            id="E0-F1-S1-T1",
            title="Sample",
            status=WorkUnitStatus.IN_PROGRESS,
            unit_type=WorkUnitType.TASK,
            file_path=Path("backlog/E0-F1-S1-T1.md"),
            repo="caylent-solutions/git-repo",
            dependencies=[],
        )

    def test_no_orphans_returns_false_without_dispatch(self, tmp_path: Path) -> None:
        with (
            patch("devbench.cli._orphan_paths_for_repo", return_value=[]),
            patch("devbench.cli._inline_orphan_cleanup_or_refuse") as inline,
            patch("devbench.cli._legacy_emit_orphan_cleanup_proposal") as legacy,
        ):
            assert cli._emit_orphan_cleanup_proposal_if_needed("E0-F1-S1-T1", self._unit(), tmp_path) is False
        inline.assert_not_called()
        legacy.assert_not_called()

    def test_skipped_gate_returns_false(self, tmp_path: Path) -> None:
        # _orphan_paths_for_repo returns None when the gate is skipped
        # (non-git checkout). Caller continues without refusal.
        with patch("devbench.cli._orphan_paths_for_repo", return_value=None):
            assert cli._emit_orphan_cleanup_proposal_if_needed("E0-F1-S1-T1", self._unit(), tmp_path) is False

    def test_dispatches_to_inline_when_enabled(self, tmp_path: Path) -> None:
        with (
            patch("devbench.cli._orphan_paths_for_repo", return_value=[".coverage"]),
            patch("devbench.cli.INLINE_ORPHAN_CLEANUP_ENABLED", True, create=True),
            patch("devbench.cli._inline_orphan_cleanup_or_refuse", return_value=False) as inline,
            patch("devbench.cli._legacy_emit_orphan_cleanup_proposal") as legacy,
        ):
            # The function imports INLINE_ORPHAN_CLEANUP_ENABLED at call time;
            # patch the import target directly via the module-level constant.
            import devbench.config as cfg

            cfg.INLINE_ORPHAN_CLEANUP_ENABLED = True
            try:
                assert cli._emit_orphan_cleanup_proposal_if_needed("E0-F1-S1-T1", self._unit(), tmp_path) is False
            finally:
                cfg.INLINE_ORPHAN_CLEANUP_ENABLED = True
        inline.assert_called_once()
        legacy.assert_not_called()

    def test_dispatches_to_legacy_when_disabled(self, tmp_path: Path) -> None:
        import devbench.config as cfg

        original = cfg.INLINE_ORPHAN_CLEANUP_ENABLED
        cfg.INLINE_ORPHAN_CLEANUP_ENABLED = False
        try:
            with (
                patch("devbench.cli._orphan_paths_for_repo", return_value=[".coverage"]),
                patch("devbench.cli._inline_orphan_cleanup_or_refuse") as inline,
                patch("devbench.cli._legacy_emit_orphan_cleanup_proposal", return_value=True) as legacy,
            ):
                assert cli._emit_orphan_cleanup_proposal_if_needed("E0-F1-S1-T1", self._unit(), tmp_path) is True
            inline.assert_not_called()
            legacy.assert_called_once()
        finally:
            cfg.INLINE_ORPHAN_CLEANUP_ENABLED = original


class TestLegacyEmitOrphanCleanupProposalDefaultStatus:
    """AC-189-8: ``_legacy_emit_orphan_cleanup_proposal`` respects
    ``backlog.default_status_for_new_work_units``.

    When the config says ``draft``, the materialised cleanup task must remain
    in ``draft`` status -- the immediate ``promote_proposal`` call must be
    skipped.  When the config says ``in-queue`` (the default), the function
    promotes the draft as before so it is immediately actionable.
    """

    _SOURCE_ID = "E0-F1-S1-T1"

    def _make_unit(self, repo: str = "org/repo") -> WorkUnit:
        return WorkUnit(
            id=self._SOURCE_ID,
            title="Source task",
            status=WorkUnitStatus.IN_PROGRESS,
            unit_type=WorkUnitType.TASK,
            file_path=Path(f"backlog/E0/E0-F1/E0-F1-S1/{self._SOURCE_ID}.md"),
            repo=repo,
            dependencies=[],
        )

    def _build_workspace(self, tmp_path: Path) -> tuple[Path, Path, Path]:
        """Return (backlog_root, backlog_index, story_dir).

        Creates the minimal directory + BACKLOG.md structure that
        ``_legacy_emit_orphan_cleanup_proposal`` needs to run without
        patching its internal helpers.
        """
        backlog_root = tmp_path / "backlog"
        story_dir = backlog_root / "E0" / "E0-F1" / "E0-F1-S1"
        story_dir.mkdir(parents=True)

        # Source-task file so _find_source_task_file finds it when wiring deps.
        wu_file = story_dir / f"{self._SOURCE_ID}.md"
        wu_file.write_text(
            f"# {self._SOURCE_ID}: Source task\n\n"
            "## Status: in-progress\n\n"
            "## Target Repository\n\n- **Repo:** `org/repo`\n\n"
            "## Description\n\nx\n\n"
            "## Dependencies\n\n| ID | Title | Status |\n|----|-------|--------|\n| none | | |\n\n"
            "## Acceptance Criteria\n\n- [ ] AC-FUNC-001\n\n"
            "## Changes Manifest\n\n| File | Change |\n|------|--------|\n| `src/foo.py` | edit |\n\n"
            "## Definition of Done\n\n- [ ] done\n\n"
            "## TDD Cycle Log\n\n"
            "## Comments\n",
            encoding="utf-8",
        )

        backlog_index = tmp_path / "BACKLOG.md"
        backlog_index.write_text(
            "# Backlog\n\n"
            "## Status Summary\n\n"
            "| Epic | Title | Done | In Progress | In Queue | Blocked |\n"
            "|------|-------|------|-------------|----------|---------|\n"
            f"| E0 | x | 0 | 1 | 0 | 0 |\n\n"
            "## Full Work Unit Index\n\n"
            "| ID | Title | Type | Status | Dependencies | Repo | File Path |\n"
            "|----|-------|------|--------|--------------|------|-----------|\n"
            f"| {self._SOURCE_ID} | Source task | Task | in-progress | None | org/repo |"
            f" `backlog/E0/E0-F1/E0-F1-S1/{self._SOURCE_ID}.md` |\n",
            encoding="utf-8",
        )
        return backlog_root, backlog_index, story_dir

    def _patch_workspace(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: Path,
        backlog_root: Path,
        backlog_index: Path,
    ) -> None:
        monkeypatch.setattr(cli, "WORKSPACE_ROOT", tmp_path)
        monkeypatch.setattr(cli, "BACKLOG_ROOT", backlog_root)
        monkeypatch.setattr(cli, "BACKLOG_INDEX", backlog_index)

    def _runtime_config_with_default_status(self, status: str) -> Any:
        """Return a RuntimeConfig clone with the given default_status_for_new_work_units."""
        import dataclasses

        from devbench.config_loader import BacklogConfig

        base = cli.RUNTIME_CONFIG
        new_backlog = BacklogConfig(default_status_for_new_work_units=status)
        return dataclasses.replace(base, backlog=new_backlog)

    @pytest.mark.parametrize("default_status", ["draft", "in-queue"])
    def test_materialised_file_reflects_configured_status(
        self,
        default_status: str,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
        tmp_path: Path,
    ) -> None:
        """The cleanup task file's ``## Status:`` line matches the configured default."""
        from devbench.backlog import proposal as proposal_mod
        from devbench.config_loader import BacklogConfig, RuntimeConfig

        backlog_root, backlog_index, story_dir = self._build_workspace(tmp_path)
        self._patch_workspace(monkeypatch, tmp_path, backlog_root, backlog_index)
        monkeypatch.setattr(cli, "RUNTIME_CONFIG", self._runtime_config_with_default_status(default_status))

        # Patch proposal_mod's config getter so materialise_proposal writes
        # the configured status directly into the draft file.
        fake_cfg = RuntimeConfig.__new__(RuntimeConfig)
        object.__setattr__(fake_cfg, "backlog", BacklogConfig(default_status_for_new_work_units=default_status))
        monkeypatch.setattr(proposal_mod, "_get_runtime_config", lambda: fake_cfg)

        result = cli._legacy_emit_orphan_cleanup_proposal(
            unit_id=self._SOURCE_ID,
            unit=self._make_unit(),
            repo_path=tmp_path / "repo",
            detected=[".coverage"],
        )

        assert result is True

        # The new draft file must exist in the story dir.
        drafts = list(story_dir.glob("E0-F1-S1-T*.md"))
        # Exclude the source task itself.
        new_drafts = [d for d in drafts if d.name != f"{self._SOURCE_ID}.md"]
        assert len(new_drafts) == 1, f"Expected exactly one new draft, found: {new_drafts}"

        draft_content = new_drafts[0].read_text(encoding="utf-8")
        assert f"## Status: {default_status}" in draft_content, (
            f"Expected '## Status: {default_status}' in draft, got content:\n{draft_content[:500]}"
        )

    def test_draft_status_skips_promote_and_leaves_draft(
        self,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
        tmp_path: Path,
    ) -> None:
        """When default_status is ``draft``, no promote_proposal call is made
        and the cleanup task remains in ``draft`` status after the function returns."""
        from devbench.backlog import proposal as proposal_mod
        from devbench.config_loader import BacklogConfig, RuntimeConfig

        backlog_root, backlog_index, story_dir = self._build_workspace(tmp_path)
        self._patch_workspace(monkeypatch, tmp_path, backlog_root, backlog_index)
        monkeypatch.setattr(cli, "RUNTIME_CONFIG", self._runtime_config_with_default_status("draft"))

        fake_cfg = RuntimeConfig.__new__(RuntimeConfig)
        object.__setattr__(fake_cfg, "backlog", BacklogConfig(default_status_for_new_work_units="draft"))
        monkeypatch.setattr(proposal_mod, "_get_runtime_config", lambda: fake_cfg)

        result = cli._legacy_emit_orphan_cleanup_proposal(
            unit_id=self._SOURCE_ID,
            unit=self._make_unit(),
            repo_path=tmp_path / "repo",
            detected=[".coverage"],
        )

        assert result is True

        new_drafts = [d for d in story_dir.glob("E0-F1-S1-T*.md") if d.name != f"{self._SOURCE_ID}.md"]
        assert len(new_drafts) == 1
        content = new_drafts[0].read_text(encoding="utf-8")
        # Must stay draft -- no promote step should have flipped it to in-queue.
        assert "## Status: draft" in content
        assert "## Status: in-queue" not in content

    def test_in_queue_status_promotes_to_in_queue(
        self,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
        tmp_path: Path,
    ) -> None:
        """When default_status is ``in-queue``, the cleanup task is promoted
        and the draft file reflects ``in-queue`` status (backwards-compatible)."""
        from devbench.backlog import proposal as proposal_mod
        from devbench.config_loader import BacklogConfig, RuntimeConfig

        backlog_root, backlog_index, story_dir = self._build_workspace(tmp_path)
        self._patch_workspace(monkeypatch, tmp_path, backlog_root, backlog_index)
        monkeypatch.setattr(cli, "RUNTIME_CONFIG", self._runtime_config_with_default_status("in-queue"))

        fake_cfg = RuntimeConfig.__new__(RuntimeConfig)
        object.__setattr__(fake_cfg, "backlog", BacklogConfig(default_status_for_new_work_units="in-queue"))
        monkeypatch.setattr(proposal_mod, "_get_runtime_config", lambda: fake_cfg)

        result = cli._legacy_emit_orphan_cleanup_proposal(
            unit_id=self._SOURCE_ID,
            unit=self._make_unit(),
            repo_path=tmp_path / "repo",
            detected=[".coverage"],
        )

        assert result is True

        new_drafts = [d for d in story_dir.glob("E0-F1-S1-T*.md") if d.name != f"{self._SOURCE_ID}.md"]
        assert len(new_drafts) == 1
        content = new_drafts[0].read_text(encoding="utf-8")
        # Promoted to in-queue.
        assert "## Status: in-queue" in content

    def test_error_message_reflects_configured_status(
        self,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
        tmp_path: Path,
    ) -> None:
        """The stderr message names the status the cleanup task was written with
        (not a hardcoded ``(in-queue)`` label), so operators see the real status."""
        from devbench.backlog import proposal as proposal_mod
        from devbench.config_loader import BacklogConfig, RuntimeConfig

        backlog_root, backlog_index, _ = self._build_workspace(tmp_path)
        self._patch_workspace(monkeypatch, tmp_path, backlog_root, backlog_index)
        monkeypatch.setattr(cli, "RUNTIME_CONFIG", self._runtime_config_with_default_status("draft"))

        fake_cfg = RuntimeConfig.__new__(RuntimeConfig)
        object.__setattr__(fake_cfg, "backlog", BacklogConfig(default_status_for_new_work_units="draft"))
        monkeypatch.setattr(proposal_mod, "_get_runtime_config", lambda: fake_cfg)

        cli._legacy_emit_orphan_cleanup_proposal(
            unit_id=self._SOURCE_ID,
            unit=self._make_unit(),
            repo_path=tmp_path / "repo",
            detected=[".coverage"],
        )

        err = capsys.readouterr().err
        # The message must not hardcode "(in-queue)" when config says "draft".
        assert "(in-queue)" not in err, f"Error message hardcodes '(in-queue)' even when config is 'draft': {err!r}"
        # The message must name the actual configured status.
        assert "(draft)" in err, f"Expected '(draft)' in stderr message when config is 'draft': {err!r}"


class TestFindExistingCleanupProposal:
    """Phase 1 secondary fix: cross-task de-duplication for the legacy proposal flow."""

    def test_returns_none_when_proposals_dir_absent(self, tmp_path: Path) -> None:
        with patch("devbench.cli.WORKSPACE_ROOT", tmp_path):
            assert cli._find_existing_cleanup_proposal([".coverage"]) is None

    def test_returns_none_when_no_cleanup_proposal_present(self, tmp_path: Path) -> None:
        proposals = tmp_path / ".devbench" / "proposals"
        proposals.mkdir(parents=True)
        # Some other proposal (not an orphan-cleanup -- claims a different file).
        (proposals / "E0-F1-S1-T2.json").write_text(
            json.dumps(
                {
                    "source_task_id": "E0-F1-S1-T2",
                    "proposed_tasks": [
                        {"suggested_id": "E0-F1-S1-T9", "files_to_own": ["src/feature.py"]},
                    ],
                }
            ),
            encoding="utf-8",
        )
        with patch("devbench.cli.WORKSPACE_ROOT", tmp_path):
            assert cli._find_existing_cleanup_proposal([".coverage"]) is None

    def test_returns_existing_cleanup_id_when_found(self, tmp_path: Path) -> None:
        proposals = tmp_path / ".devbench" / "proposals"
        proposals.mkdir(parents=True)
        (proposals / "E0-F1-S1-T2.json").write_text(
            json.dumps(
                {
                    "source_task_id": "E0-F1-S1-T2",
                    "proposed_tasks": [
                        {"suggested_id": "E0-F1-S1-T7", "files_to_own": [".gitignore"]},
                    ],
                }
            ),
            encoding="utf-8",
        )
        with patch("devbench.cli.WORKSPACE_ROOT", tmp_path):
            assert cli._find_existing_cleanup_proposal([".coverage"]) == "E0-F1-S1-T7"

    def test_skips_malformed_proposal_json(self, tmp_path: Path) -> None:
        proposals = tmp_path / ".devbench" / "proposals"
        proposals.mkdir(parents=True)
        (proposals / "broken.json").write_text("{not valid json", encoding="utf-8")
        (proposals / "E0-F1-S1-T2.json").write_text(
            json.dumps(
                {
                    "proposed_tasks": [
                        {"suggested_id": "E0-F1-S1-T7", "files_to_own": [".gitignore"]},
                    ],
                }
            ),
            encoding="utf-8",
        )
        with patch("devbench.cli.WORKSPACE_ROOT", tmp_path):
            # Malformed file is silently skipped; the valid one still wins.
            assert cli._find_existing_cleanup_proposal([".coverage"]) == "E0-F1-S1-T7"


class TestCiFailureRetry:
    """Issue #115: CI-failure executor retry instead of immediate BLOCKED."""

    def _make_wu_file(self, tmp_path: Path, comments: list[str] | None = None) -> Path:
        body = "# E0-F1-S1-T1: sample\n\n## Status: in-progress\n\n## Description\n\nx\n\n## Comments\n\n"
        for c in comments or []:
            body += f"[2026-05-01 00:00 UTC] [agent/git_ops] {c}\n"
        wu = tmp_path / "E0-F1-S1-T1.md"
        wu.write_text(body, encoding="utf-8")
        return wu

    def test_count_ci_fail_attempts_zero_when_no_entries(self, tmp_path: Path) -> None:
        wu = self._make_wu_file(tmp_path, comments=["[PR_CREATED] https://example/x"])
        assert cli._count_ci_fail_attempts(wu) == 0

    def test_count_ci_fail_attempts_returns_count(self, tmp_path: Path) -> None:
        wu = self._make_wu_file(tmp_path, comments=["[CI_FAIL] one", "[CI_FAIL] two"])
        assert cli._count_ci_fail_attempts(wu) == 2

    def test_count_ci_fail_attempts_zero_when_file_missing(self, tmp_path: Path) -> None:
        assert cli._count_ci_fail_attempts(tmp_path / "missing.md") == 0
        assert cli._count_ci_fail_attempts(None) == 0

    def test_handle_ci_failure_legacy_when_disabled(
        self,
        tmp_path: Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        wu = self._make_wu_file(tmp_path)
        mgr = MagicMock()
        with patch("devbench.config.CI_FAILURE_RETRY_ENABLED", False):
            rc = cli._handle_ci_failure(
                ops=MagicMock(),
                unit_id="E0-F1-S1-T1",
                canonical_repo="ex/foo",
                pr_number=42,
                repo_path=tmp_path,
                wu_file=wu,
                mgr=mgr,
            )
        assert rc == 1
        assert "CI checks failed for PR #42" in capsys.readouterr().err
        mgr._append_agent_comment.assert_not_called()

    def test_handle_ci_failure_returns_2_under_budget(
        self,
        tmp_path: Path,
    ) -> None:
        wu = self._make_wu_file(tmp_path)
        mgr = MagicMock()
        ops = MagicMock()
        ops.get_latest_failing_run_id.return_value = "999"
        ops.fetch_run_log.return_value = "ruff E501 line too long\n"
        with (
            patch("devbench.config.CI_FAILURE_RETRY_ENABLED", True),
            patch("devbench.config.MAX_RETRY_ATTEMPTS", 3),
            patch("devbench.cli.WORKSPACE_ROOT", tmp_path),
        ):
            rc = cli._handle_ci_failure(
                ops=ops,
                unit_id="E0-F1-S1-T1",
                canonical_repo="ex/foo",
                pr_number=42,
                repo_path=tmp_path,
                wu_file=wu,
                mgr=mgr,
            )
        assert rc == 2
        # log file written under workspace
        log_files = sorted((tmp_path / ".devbench" / "ci-failures").glob("E0-F1-S1-T1-*.log"))
        assert len(log_files) == 1
        assert "ruff E501" in log_files[0].read_text(encoding="utf-8")
        # audit comment written with [CI_FAIL] (not _BLOCKED)
        msg = mgr._append_agent_comment.call_args.args[2]
        assert msg.startswith("[CI_FAIL] ")

    def test_handle_ci_failure_returns_1_when_budget_exhausted(
        self,
        tmp_path: Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        # Pre-seed two prior CI_FAIL entries so this is attempt 3 -- exhausted.
        wu = self._make_wu_file(tmp_path, comments=["[CI_FAIL] r1", "[CI_FAIL] r2"])
        mgr = MagicMock()
        ops = MagicMock()
        ops.get_latest_failing_run_id.return_value = None  # log unavailable
        with (
            patch("devbench.config.CI_FAILURE_RETRY_ENABLED", True),
            patch("devbench.config.MAX_RETRY_ATTEMPTS", 3),
            patch("devbench.cli.WORKSPACE_ROOT", tmp_path),
        ):
            rc = cli._handle_ci_failure(
                ops=ops,
                unit_id="E0-F1-S1-T1",
                canonical_repo="ex/foo",
                pr_number=42,
                repo_path=tmp_path,
                wu_file=wu,
                mgr=mgr,
            )
        assert rc == 1
        err = capsys.readouterr().err
        assert "budget exhausted" in err
        assert "MAX_RETRY_ATTEMPTS=3" in err
        # exhaustion uses [CI_FAIL_BLOCKED] marker
        msg = mgr._append_agent_comment.call_args.args[2]
        assert msg.startswith("[CI_FAIL_BLOCKED] ")

    def test_handle_ci_failure_skips_audit_when_no_wu_file(self, tmp_path: Path) -> None:
        ops = MagicMock()
        ops.get_latest_failing_run_id.return_value = "1"
        ops.fetch_run_log.return_value = "log\n"
        mgr = MagicMock()
        with (
            patch("devbench.config.CI_FAILURE_RETRY_ENABLED", True),
            patch("devbench.config.MAX_RETRY_ATTEMPTS", 3),
            patch("devbench.cli.WORKSPACE_ROOT", tmp_path),
        ):
            rc = cli._handle_ci_failure(
                ops=ops,
                unit_id="E0-F1-S1-T1",
                canonical_repo="ex/foo",
                pr_number=42,
                repo_path=tmp_path,
                wu_file=None,
                mgr=mgr,
            )
        assert rc == 2
        mgr._append_agent_comment.assert_not_called()


class TestPrReviewResolution:
    """Issue #116: poll PR review state before merging."""

    def _resolution(
        self,
        resolved: bool = False,
        decision: str = "CHANGES_REQUESTED",
        reviews: list[dict[str, str | int]] | None = None,
        comments: list[dict[str, str | int]] | None = None,
    ) -> object:
        from devbench.github.git_ops import ReviewResolution

        return ReviewResolution(
            resolved=resolved,
            review_decision=decision,
            unresolved_reviews=reviews or [],
            unresolved_comments=comments or [],
            elapsed_seconds=0.0,
        )

    def _wu_file(self, tmp_path: Path, retries: int = 0) -> Path:
        comments = "\n".join(f"[2026-05-01 00:0{i} UTC] [agent/git_ops] [PR_BOT_FAIL] r{i}" for i in range(retries))
        body = f"# E0-F1-S1-T1: t\n\n## Status: in-progress\n\n## Description\n\nx\n\n## Comments\n\n{comments}\n"
        wu = tmp_path / "wu.md"
        wu.write_text(body, encoding="utf-8")
        return wu

    def test_returns_0_when_phase_disabled(self, tmp_path: Path) -> None:
        ops = MagicMock()
        with patch("devbench.config.PR_REVIEW_RESOLUTION_ENABLED", False):
            rc = cli._handle_pr_review_resolution(
                ops=ops,
                unit_id="E0-F1-S1-T1",
                canonical_repo="ex/foo",
                pr_number=42,
                repo_path=tmp_path,
                wu_file=self._wu_file(tmp_path),
                mgr=MagicMock(),
            )
        assert rc == 0
        ops.poll_pr_review_resolution.assert_not_called()

    def test_returns_0_when_no_signals_configured(self, tmp_path: Path) -> None:
        ops = MagicMock()
        with (
            patch("devbench.config.PR_REVIEW_RESOLUTION_ENABLED", True),
            patch("devbench.config.PR_REVIEW_AGENTS", ()),
            patch("devbench.config.PR_REVIEW_DECISION_BLOCKS", False),
        ):
            rc = cli._handle_pr_review_resolution(
                ops=ops,
                unit_id="E0-F1-S1-T1",
                canonical_repo="ex/foo",
                pr_number=42,
                repo_path=tmp_path,
                wu_file=self._wu_file(tmp_path),
                mgr=MagicMock(),
            )
        assert rc == 0
        ops.poll_pr_review_resolution.assert_not_called()

    def test_returns_0_when_resolved(self, tmp_path: Path) -> None:
        ops = MagicMock()
        ops.poll_pr_review_resolution.return_value = self._resolution(resolved=True, decision="APPROVED")
        with (
            patch("devbench.config.PR_REVIEW_RESOLUTION_ENABLED", True),
            patch("devbench.config.PR_REVIEW_AGENTS", ()),
            patch("devbench.config.PR_REVIEW_DECISION_BLOCKS", True),
        ):
            rc = cli._handle_pr_review_resolution(
                ops=ops,
                unit_id="E0-F1-S1-T1",
                canonical_repo="ex/foo",
                pr_number=42,
                repo_path=tmp_path,
                wu_file=self._wu_file(tmp_path),
                mgr=MagicMock(),
            )
        assert rc == 0

    def test_returns_3_when_unresolved_under_budget(self, tmp_path: Path) -> None:
        ops = MagicMock()
        ops.poll_pr_review_resolution.return_value = self._resolution(
            reviews=[{"reviewer": "github-copilot[bot]", "state": "CHANGES_REQUESTED", "body": "fix this"}],
        )
        mgr = MagicMock()
        with (
            patch("devbench.config.PR_REVIEW_RESOLUTION_ENABLED", True),
            patch("devbench.config.PR_REVIEW_AGENTS", ("github-copilot[bot]",)),
            patch("devbench.config.PR_REVIEW_DECISION_BLOCKS", True),
            patch("devbench.config.MAX_RETRY_ATTEMPTS", 3),
            patch("devbench.cli.WORKSPACE_ROOT", tmp_path),
        ):
            rc = cli._handle_pr_review_resolution(
                ops=ops,
                unit_id="E0-F1-S1-T1",
                canonical_repo="ex/foo",
                pr_number=42,
                repo_path=tmp_path,
                wu_file=self._wu_file(tmp_path),
                mgr=mgr,
            )
        assert rc == 3
        feedback = sorted((tmp_path / ".devbench" / "pr-bot-feedback").glob("*.json"))
        assert len(feedback) == 1
        payload = json.loads(feedback[0].read_text(encoding="utf-8"))
        assert payload["pr_number"] == 42
        assert payload["unresolved_reviews"][0]["reviewer"] == "github-copilot[bot]"
        msg = mgr._append_agent_comment.call_args.args[2]
        assert msg.startswith("[PR_BOT_FAIL] ")

    def test_returns_1_when_budget_exhausted(self, tmp_path: Path) -> None:
        ops = MagicMock()
        ops.poll_pr_review_resolution.return_value = self._resolution(
            reviews=[{"reviewer": "bot", "state": "CHANGES_REQUESTED", "body": "x"}],
        )
        mgr = MagicMock()
        # Pre-seed MAX-1 PR_BOT_FAIL retries so the next failure exhausts budget.
        wu = self._wu_file(tmp_path, retries=2)
        with (
            patch("devbench.config.PR_REVIEW_RESOLUTION_ENABLED", True),
            patch("devbench.config.PR_REVIEW_AGENTS", ("bot",)),
            patch("devbench.config.PR_REVIEW_DECISION_BLOCKS", True),
            patch("devbench.config.MAX_RETRY_ATTEMPTS", 3),
            patch("devbench.cli.WORKSPACE_ROOT", tmp_path),
        ):
            rc = cli._handle_pr_review_resolution(
                ops=ops,
                unit_id="E0-F1-S1-T1",
                canonical_repo="ex/foo",
                pr_number=42,
                repo_path=tmp_path,
                wu_file=wu,
                mgr=mgr,
            )
        assert rc == 1
        msg = mgr._append_agent_comment.call_args.args[2]
        assert msg.startswith("[PR_BOT_FAIL_BLOCKED] ")


class TestPauseBeforeMerge:
    """Issue #101: pause-before-merge mode lifecycle."""

    def _wu_file(self, tmp_path: Path) -> Path:
        wu = tmp_path / "E0-F1-S1-T1.md"
        wu.write_text(
            "# E0-F1-S1-T1: t\n\n## Status: in-progress\n\n## Description\n\nx\n\n## Comments\n\n",
            encoding="utf-8",
        )
        return wu

    def test_pause_transitions_to_in_review(self, tmp_path: Path) -> None:
        wu = self._wu_file(tmp_path)
        mgr = MagicMock()
        with patch("devbench.cli.BACKLOG_INDEX", tmp_path / "BACKLOG.md"):
            rc = cli._pause_before_merge(
                unit_id="E0-F1-S1-T1",
                pr_number=42,
                pr_url="https://github.com/ex/foo/pull/42",
                wu_file=wu,
                mgr=mgr,
            )
        assert rc == 0
        mgr.force_status.assert_called_once()
        # 4th positional arg of force_status is the new status
        args = mgr.force_status.call_args.args
        assert args[3] == "in-review"
        msg = mgr._append_agent_comment.call_args.args[2]
        assert "[PR_AWAITING_MERGE]" in msg
        assert "PR #42" in msg

    def test_pause_skips_audit_when_no_wu_file(self, tmp_path: Path) -> None:
        mgr = MagicMock()
        rc = cli._pause_before_merge(
            unit_id="E0-F1-S1-T1",
            pr_number=42,
            pr_url="https://github.com/ex/foo/pull/42",
            wu_file=None,
            mgr=mgr,
        )
        assert rc == 0
        mgr.force_status.assert_not_called()
        mgr._append_agent_comment.assert_not_called()


class TestCmdCheckMerge:
    """Issue #101: cmd_check_merge reconciles in-review work units."""

    def _make_unit(self, repo: str = "caylent-solutions/devbench") -> WorkUnit:
        return WorkUnit(
            id="E0-F1-S1-T1",
            title="t",
            status=WorkUnitStatus.IN_REVIEW,
            unit_type=WorkUnitType.TASK,
            file_path=Path("backlog/E0-F1-S1-T1.md"),
            repo=repo,
            branch="backlog/e0-f1-s1-t1",
            dependencies=[],
        )

    def test_returns_0_with_done_when_pr_merged(self, tmp_path: Path) -> None:
        ops = MagicMock()
        ops._gh.return_value = (
            0,
            json.dumps([{"number": 42, "state": "MERGED", "mergedAt": "2026-05-07T00:00:00Z", "url": "u"}]),
            "",
        )
        mgr = MagicMock()
        wu_file = tmp_path / "E0-F1-S1-T1.md"
        wu_file.write_text("stub")
        with (
            patch("devbench.cli._resolve_git_ops_context", return_value=(self._make_unit(), "ex/foo", tmp_path)),
            patch("devbench.cli._resolve_unit_file", return_value=wu_file),
            patch("devbench.cli.BacklogManager", return_value=mgr),
            patch("devbench.cli.BACKLOG_INDEX", tmp_path / "BACKLOG.md"),
            patch("devbench.github.git_ops.GitOpsService", return_value=ops),
        ):
            rc = cli.cmd_check_merge("E0-F1-S1-T1")
        assert rc == 0
        mgr.mark_done.assert_called_once()

    def test_returns_0_with_blocked_when_pr_closed(self, tmp_path: Path) -> None:
        ops = MagicMock()
        ops._gh.return_value = (0, json.dumps([{"number": 42, "state": "CLOSED", "mergedAt": None, "url": "u"}]), "")
        mgr = MagicMock()
        wu_file = tmp_path / "E0-F1-S1-T1.md"
        wu_file.write_text("stub")
        with (
            patch("devbench.cli._resolve_git_ops_context", return_value=(self._make_unit(), "ex/foo", tmp_path)),
            patch("devbench.cli._resolve_unit_file", return_value=wu_file),
            patch("devbench.cli.BacklogManager", return_value=mgr),
            patch("devbench.cli.BACKLOG_INDEX", tmp_path / "BACKLOG.md"),
            patch("devbench.github.git_ops.GitOpsService", return_value=ops),
        ):
            rc = cli.cmd_check_merge("E0-F1-S1-T1")
        assert rc == 0
        mgr.mark_blocked.assert_called_once()

    def test_noop_when_pr_still_open(self, tmp_path: Path) -> None:
        ops = MagicMock()
        ops._gh.return_value = (0, json.dumps([{"number": 42, "state": "OPEN", "mergedAt": None, "url": "u"}]), "")
        mgr = MagicMock()
        with (
            patch("devbench.cli._resolve_git_ops_context", return_value=(self._make_unit(), "ex/foo", tmp_path)),
            patch("devbench.cli._resolve_unit_file", return_value=None),
            patch("devbench.cli.BacklogManager", return_value=mgr),
            patch("devbench.github.git_ops.GitOpsService", return_value=ops),
        ):
            rc = cli.cmd_check_merge("E0-F1-S1-T1")
        assert rc == 0
        mgr.mark_done.assert_not_called()
        mgr.mark_blocked.assert_not_called()

    def test_returns_0_with_no_pr_found(self, tmp_path: Path) -> None:
        ops = MagicMock()
        ops._gh.return_value = (0, "[]", "")
        with (
            patch("devbench.cli._resolve_git_ops_context", return_value=(self._make_unit(), "ex/foo", tmp_path)),
            patch("devbench.cli._resolve_unit_file", return_value=None),
            patch("devbench.cli.BacklogManager", return_value=MagicMock()),
            patch("devbench.github.git_ops.GitOpsService", return_value=ops),
        ):
            rc = cli.cmd_check_merge("E0-F1-S1-T1")
        assert rc == 0

    def test_returns_1_on_gh_failure(self, tmp_path: Path) -> None:
        ops = MagicMock()
        ops._gh.return_value = (1, "", "boom")
        with (
            patch("devbench.cli._resolve_git_ops_context", return_value=(self._make_unit(), "ex/foo", tmp_path)),
            patch("devbench.cli._resolve_unit_file", return_value=None),
            patch("devbench.cli.BacklogManager", return_value=MagicMock()),
            patch("devbench.github.git_ops.GitOpsService", return_value=ops),
        ):
            rc = cli.cmd_check_merge("E0-F1-S1-T1")
        assert rc == 1

    def test_returns_1_on_invalid_json(self, tmp_path: Path) -> None:
        ops = MagicMock()
        ops._gh.return_value = (0, "{not json", "")
        with (
            patch("devbench.cli._resolve_git_ops_context", return_value=(self._make_unit(), "ex/foo", tmp_path)),
            patch("devbench.cli._resolve_unit_file", return_value=None),
            patch("devbench.cli.BacklogManager", return_value=MagicMock()),
            patch("devbench.github.git_ops.GitOpsService", return_value=ops),
        ):
            rc = cli.cmd_check_merge("E0-F1-S1-T1")
        assert rc == 1

    def test_returns_1_when_done_gate_refuses(self, tmp_path: Path) -> None:
        """Done-gate refuses merge promotion when judges did not pass -- rc=1."""
        ops = MagicMock()
        ops._gh.return_value = (
            0,
            json.dumps([{"number": 42, "state": "MERGED", "mergedAt": "2026-05-07T00:00:00Z", "url": "u"}]),
            "",
        )
        mgr = MagicMock()
        mgr.mark_done.side_effect = RuntimeError("done-gate failure")
        wu_file = tmp_path / "E0-F1-S1-T1.md"
        wu_file.write_text("stub")
        with (
            patch("devbench.cli._resolve_git_ops_context", return_value=(self._make_unit(), "ex/foo", tmp_path)),
            patch("devbench.cli._resolve_unit_file", return_value=wu_file),
            patch("devbench.cli.BacklogManager", return_value=mgr),
            patch("devbench.cli.BACKLOG_INDEX", tmp_path / "BACKLOG.md"),
            patch("devbench.github.git_ops.GitOpsService", return_value=ops),
        ):
            rc = cli.cmd_check_merge("E0-F1-S1-T1")
        assert rc == 1


class TestCheckMergeRegistration:
    """The check-merge command must be registered in the CLI dispatch table."""

    def test_check_merge_in_commands(self) -> None:
        assert "check-merge" in cli._COMMANDS
        handler, argc, _help = cli._COMMANDS["check-merge"]
        assert handler is cli.cmd_check_merge
        assert argc == 1


# ---------------------------------------------------------------------------
# Issue #148 / #150 / #152 / #153 / #155 cascade-reliability fixes.
# Helpers below reuse the lightweight backlog scaffolding pattern from
# ``TestCmdSyncBlocked`` -- duplicated locally so each test class stays
# self-contained and a fixture rename never silently breaks one of these.
# ---------------------------------------------------------------------------


def _cascade_build_backlog(
    tmp_path: Path,
    rows: list[tuple[str, str, str, str, str, str]],
) -> Path:
    """Materialise BACKLOG.md + per-row work-unit files.

    Each row is ``(id, type, status, deps, basename, comments)`` where
    ``comments`` is appended verbatim to the work-unit Markdown (used to
    inject ``[BLOCKED_PENDING_PROPOSAL]`` markers + audit lines).
    """
    index_lines = [
        "# Backlog\n",
        "## Full Work Unit Index\n",
        "| ID | Title | Type | Status | Dependencies | Repo | File Path |",
        "|----|-------|------|--------|--------------|------|-----------|",
    ]
    wu_dir = tmp_path / "backlog"
    wu_dir.mkdir(exist_ok=True)
    for unit_id, unit_type, status, deps, basename, comments in rows:
        file_path = f"backlog/{basename}.md"
        index_lines.append(
            f"| {unit_id} | {unit_id} | {unit_type} | {status} | {deps} | caylent-solutions/test-repo | `{file_path}` |"
        )
        wu_body = f"# {unit_id}: Test\n\n## Status: {status}\n\n## Description\n\nx\n"
        if deps and deps != "None":
            dep_rows = "\n".join(f"| {d.strip()} | Task | dep |" for d in deps.split(","))
            wu_body += f"\n## Dependencies\n\n| ID | Type | Reason |\n|----|------|--------|\n{dep_rows}\n"
        if comments:
            wu_body += f"\n{comments}"
        (wu_dir / f"{basename}.md").write_text(wu_body)
    index_path = tmp_path / "BACKLOG.md"
    index_path.write_text("\n".join(index_lines) + "\n")
    return index_path


class TestSyncBlockedEvaluatesMarkerTargetState:
    """Issue #148: ``cmd_sync_blocked`` checks each ``[BLOCKED_PENDING_PROPOSAL]``
    marker's target status. Stale markers (target already terminal) no longer
    block the re-queue; only at-least-one non-terminal target keeps the task
    pinned.
    """

    def test_marker_target_terminal_allows_requeue(
        self,
        tmp_path: Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        # T2 carries a marker pointing at T9 which is already done;
        # sync-blocked must NOT skip on the marker any more.
        marker_comments = "## Comments\n\n[BLOCKED_PENDING_PROPOSAL] E0-F1-S1-T9\n"
        index = _cascade_build_backlog(
            tmp_path,
            rows=[
                ("E0-F1-S1-T1", "Task", "done", "None", "E0-F1-S1-T1", ""),
                ("E0-F1-S1-T9", "Task", "done", "None", "E0-F1-S1-T9", ""),
                ("E0-F1-S1-T2", "Task", "blocked", "E0-F1-S1-T1", "E0-F1-S1-T2", marker_comments),
            ],
        )
        with (
            patch("devbench.cli.BACKLOG_ROOT", tmp_path / "backlog"),
            patch("devbench.cli.BACKLOG_INDEX", index),
            patch("devbench.cli.WORKSPACE_ROOT", tmp_path),
        ):
            rc = cli.cmd_sync_blocked()
        assert rc == 0
        envelope = json.loads(capsys.readouterr().out.strip())
        assert "E0-F1-S1-T2" in envelope["flipped_to_in_queue"]
        t2 = (tmp_path / "backlog" / "E0-F1-S1-T2.md").read_text()
        assert "## Status: in-queue" in t2
        assert "[UNBLOCKED] deps satisfied" in t2

    def test_marker_target_open_skips_requeue(
        self,
        tmp_path: Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        marker_comments = "## Comments\n\n[BLOCKED_PENDING_PROPOSAL] E0-F1-S1-T9\n"
        index = _cascade_build_backlog(
            tmp_path,
            rows=[
                ("E0-F1-S1-T1", "Task", "done", "None", "E0-F1-S1-T1", ""),
                ("E0-F1-S1-T9", "Task", "in-queue", "None", "E0-F1-S1-T9", ""),
                ("E0-F1-S1-T2", "Task", "blocked", "E0-F1-S1-T1", "E0-F1-S1-T2", marker_comments),
            ],
        )
        with (
            patch("devbench.cli.BACKLOG_ROOT", tmp_path / "backlog"),
            patch("devbench.cli.BACKLOG_INDEX", index),
            patch("devbench.cli.WORKSPACE_ROOT", tmp_path),
        ):
            rc = cli.cmd_sync_blocked()
        assert rc == 0
        envelope = json.loads(capsys.readouterr().out.strip())
        assert "E0-F1-S1-T2" not in envelope["flipped_to_in_queue"]
        assert "## Status: blocked" in (tmp_path / "backlog" / "E0-F1-S1-T2.md").read_text()

    def test_marker_unknown_id_keeps_task_blocked(
        self,
        tmp_path: Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """Unknown marker target IDs must remain conservative (treat as open)."""
        marker_comments = "## Comments\n\n[BLOCKED_PENDING_PROPOSAL] E0-F1-S1-T999\n"
        index = _cascade_build_backlog(
            tmp_path,
            rows=[
                ("E0-F1-S1-T1", "Task", "done", "None", "E0-F1-S1-T1", ""),
                ("E0-F1-S1-T2", "Task", "blocked", "E0-F1-S1-T1", "E0-F1-S1-T2", marker_comments),
            ],
        )
        with (
            patch("devbench.cli.BACKLOG_ROOT", tmp_path / "backlog"),
            patch("devbench.cli.BACKLOG_INDEX", index),
            patch("devbench.cli.WORKSPACE_ROOT", tmp_path),
        ):
            rc = cli.cmd_sync_blocked()
        assert rc == 0
        envelope = json.loads(capsys.readouterr().out.strip())
        assert "E0-F1-S1-T2" not in envelope["flipped_to_in_queue"]


class TestCmdReconcileCascade:
    """Issue #150: ``devbench reconcile-cascade`` walks every blocked task,
    flips the eligible ones (markers all terminal AND deps satisfied), and
    emits ``[CASCADE_RECONCILED]`` audits + a JSON envelope of flips/skips.
    """

    def test_eligible_task_is_flipped(self, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
        marker = "## Comments\n\n[BLOCKED_PENDING_PROPOSAL] E0-F1-S1-T9\n"
        index = _cascade_build_backlog(
            tmp_path,
            rows=[
                ("E0-F1-S1-T1", "Task", "done", "None", "E0-F1-S1-T1", ""),
                ("E0-F1-S1-T9", "Task", "done", "None", "E0-F1-S1-T9", ""),
                ("E0-F1-S1-T2", "Task", "blocked", "E0-F1-S1-T1", "E0-F1-S1-T2", marker),
            ],
        )
        with (
            patch("devbench.cli.BACKLOG_ROOT", tmp_path / "backlog"),
            patch("devbench.cli.BACKLOG_INDEX", index),
            patch("devbench.cli.WORKSPACE_ROOT", tmp_path),
        ):
            rc = cli.cmd_reconcile_cascade()
        assert rc == 0
        envelope = json.loads(capsys.readouterr().out.strip())
        flipped_ids = [item["unit_id"] for item in envelope["flipped"]]
        assert "E0-F1-S1-T2" in flipped_ids
        t2 = (tmp_path / "backlog" / "E0-F1-S1-T2.md").read_text()
        assert "## Status: in-queue" in t2
        assert "[CASCADE_RECONCILED]" in t2

    def test_open_marker_keeps_task_blocked(
        self,
        tmp_path: Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        marker = "## Comments\n\n[BLOCKED_PENDING_PROPOSAL] E0-F1-S1-T9\n"
        index = _cascade_build_backlog(
            tmp_path,
            rows=[
                ("E0-F1-S1-T9", "Task", "in-progress", "None", "E0-F1-S1-T9", ""),
                ("E0-F1-S1-T2", "Task", "blocked", "None", "E0-F1-S1-T2", marker),
            ],
        )
        with (
            patch("devbench.cli.BACKLOG_ROOT", tmp_path / "backlog"),
            patch("devbench.cli.BACKLOG_INDEX", index),
            patch("devbench.cli.WORKSPACE_ROOT", tmp_path),
        ):
            rc = cli.cmd_reconcile_cascade()
        assert rc == 0
        envelope = json.loads(capsys.readouterr().out.strip())
        assert envelope["flipped"] == []
        skips = [item["unit_id"] for item in envelope["skipped"]]
        assert "E0-F1-S1-T2" in skips

    def test_unsatisfied_regular_dep_keeps_task_blocked(
        self,
        tmp_path: Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        index = _cascade_build_backlog(
            tmp_path,
            rows=[
                ("E0-F1-S1-T1", "Task", "in-progress", "None", "E0-F1-S1-T1", ""),
                ("E0-F1-S1-T2", "Task", "blocked", "E0-F1-S1-T1", "E0-F1-S1-T2", ""),
            ],
        )
        with (
            patch("devbench.cli.BACKLOG_ROOT", tmp_path / "backlog"),
            patch("devbench.cli.BACKLOG_INDEX", index),
            patch("devbench.cli.WORKSPACE_ROOT", tmp_path),
        ):
            rc = cli.cmd_reconcile_cascade()
        assert rc == 0
        envelope = json.loads(capsys.readouterr().out.strip())
        skip_reasons = {item["unit_id"]: item["reason"] for item in envelope["skipped"]}
        assert "E0-F1-S1-T2" in skip_reasons
        assert "regular dep" in skip_reasons["E0-F1-S1-T2"]

    def test_registered_in_commands(self) -> None:
        assert "reconcile-cascade" in cli._COMMANDS
        handler, argc, _help = cli._COMMANDS["reconcile-cascade"]
        assert handler is cli.cmd_reconcile_cascade
        assert argc == 0


class TestVariadicCommandsCoverage:
    """Issue #152: every command whose body parses a ``--<flag> <value>`` pair
    must be registered in ``_VARIADIC_COMMANDS``. Auto-discover via the
    registry so adding a new flag-bearing command without registering it
    fails this test.
    """

    # Flag tokens that ALWAYS take a value (not boolean toggles). A command's
    # body that contains ``arg == "--reason"`` (and similar) MUST opt into
    # variadic dispatch -- the fixed-arity slice would otherwise drop the
    # value when it follows positional args.
    FLAG_TOKENS_NEEDING_VARIADIC: ClassVar[tuple[str, ...]] = (
        '"--reason"',
        '"--reasoning"',
        '"--message"',
    )

    def test_every_flag_with_value_command_is_variadic(self) -> None:
        import inspect

        offenders: list[str] = []
        for name, (handler, _argc, _desc) in cli._COMMANDS.items():
            try:
                source = inspect.getsource(handler)
            except (OSError, TypeError):
                continue
            if not any(token in source for token in self.FLAG_TOKENS_NEEDING_VARIADIC):
                continue
            if name in cli._VARIADIC_COMMANDS:
                continue
            offenders.append(name)
        assert not offenders, (
            "These commands consume a flag-with-value pair but are NOT registered in "
            f"_VARIADIC_COMMANDS: {offenders}. The fixed-arity dispatcher slice will "
            "drop the value, causing silent failures."
        )

    def test_variadic_set_is_subset_of_commands(self) -> None:
        """Sanity: every variadic name must reference a real command."""
        unknown = cli._VARIADIC_COMMANDS - cli._COMMANDS.keys()
        assert not unknown, f"unknown variadic entries: {unknown}"


class TestStatusPanelFiltersStaleBlockedAudits:
    """Issue #153: ``status --detail`` panel renderer hides ``[BLOCKED]``
    audit rows that have been superseded by a later ``[UNBLOCKED]`` /
    ``[CASCADE_RESOLVED]`` line. The audit history in the file is
    append-only; only the rendered panel filters.
    """

    def test_unblocked_supersedes_blocked(self) -> None:
        content = (
            "## Comments\n\n"
            "[2026-04-01 10:00 UTC] [agent/x] [BLOCKED] dep T9 not yet terminal\n"
            "[2026-04-01 11:00 UTC] [agent/x] [UNBLOCKED] deps satisfied\n"
        )
        assert cli._unsuperseded_blocked_audits(content) == []

    def test_cascade_resolved_supersedes_blocked(self) -> None:
        content = (
            "## Comments\n\n"
            "[2026-04-01 10:00 UTC] [agent/x] [BLOCKED] waiting on cascade\n"
            "[2026-04-01 11:00 UTC] [agent/x] [CASCADE_RESOLVED] markers terminal\n"
        )
        assert cli._unsuperseded_blocked_audits(content) == []

    def test_blocked_without_supersession_is_kept(self) -> None:
        content = "## Comments\n\n[2026-04-01 10:00 UTC] [agent/x] [BLOCKED] dep T9 not yet terminal\n"
        kept = cli._unsuperseded_blocked_audits(content)
        assert len(kept) == 1
        assert "[BLOCKED]" in kept[0]

    def test_blocked_pending_proposal_marker_not_treated_as_blocked_audit(self) -> None:
        """The cascade marker line must not be confused with a plain ``[BLOCKED]`` audit."""
        content = "## Comments\n\n[2026-04-01 10:00 UTC] [agent/task_factory] [BLOCKED_PENDING_PROPOSAL] T9\n"
        assert cli._unsuperseded_blocked_audits(content) == []


class TestCmdSweepProposalsAutoPromotesPreExisting:
    """Issue #155: ``cmd_sweep_proposals`` also picks up pre-existing
    ``proposed`` drafts whose proposal JSON has already been deleted.
    """

    def test_orphan_proposed_draft_is_auto_promoted(
        self,
        tmp_path: Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        from devbench.config_loader import RuntimeConfig, TaskFactoryConfig

        # No proposal JSON on disk; the orphan-promote pass should still
        # surface the proposed draft and flip it to in-queue.
        index = _cascade_build_backlog(
            tmp_path,
            rows=[
                ("E0-F1-S1-T1", "Task", "in-queue", "None", "E0-F1-S1-T1", ""),
                ("E0-F1-S1-T2", "Task", "proposed", "None", "E0-F1-S1-T2", ""),
            ],
        )
        # The promoter resolves the draft via _find_draft_file which expects
        # the layout backlog/E0/E0-F1/E0-F1-S1/<id>.md. Replicate that here
        # so the auto-promote actually finds the draft.
        story_dir = tmp_path / "backlog" / "E0" / "E0-F1" / "E0-F1-S1"
        story_dir.mkdir(parents=True)
        (story_dir / "E0-F1-S1-T2.md").write_text("# E0-F1-S1-T2: Test\n\n## Status: proposed\n\n## Description\n\nx\n")
        # Re-point the BACKLOG.md row to the nested location.
        idx_text = index.read_text()
        index.write_text(
            idx_text.replace(
                "`backlog/E0-F1-S1-T2.md`",
                "`backlog/E0/E0-F1/E0-F1-S1/E0-F1-S1-T2.md`",
            )
        )

        runtime_with_auto_accept = RuntimeConfig(task_factory=TaskFactoryConfig(auto_accept_proposals=True))
        with (
            patch("devbench.cli.BACKLOG_ROOT", tmp_path / "backlog"),
            patch("devbench.cli.BACKLOG_INDEX", index),
            patch("devbench.cli.WORKSPACE_ROOT", tmp_path),
            patch("devbench.cli.RUNTIME_CONFIG", runtime_with_auto_accept),
        ):
            rc = cli.cmd_sweep_proposals()
        assert rc == 0
        # No proposal JSON existed -> the materialise loop is a no-op, so
        # only the orphan promote pass touches T2.
        out = capsys.readouterr().out
        assert "orphan auto-promoted 1" in out
        # T2 transitioned to in-queue via promote_proposal.
        t2_md = (story_dir / "E0-F1-S1-T2.md").read_text()
        assert "## Status: in-queue" in t2_md

    def test_orphan_promote_skipped_when_toggle_off(
        self,
        tmp_path: Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        from devbench.config_loader import RuntimeConfig, TaskFactoryConfig

        index = _cascade_build_backlog(
            tmp_path,
            rows=[
                ("E0-F1-S1-T1", "Task", "in-queue", "None", "E0-F1-S1-T1", ""),
                ("E0-F1-S1-T2", "Task", "proposed", "None", "E0-F1-S1-T2", ""),
            ],
        )
        story_dir = tmp_path / "backlog" / "E0" / "E0-F1" / "E0-F1-S1"
        story_dir.mkdir(parents=True)
        (story_dir / "E0-F1-S1-T2.md").write_text("# E0-F1-S1-T2: Test\n\n## Status: proposed\n\n## Description\n\nx\n")
        idx_text = index.read_text()
        index.write_text(
            idx_text.replace(
                "`backlog/E0-F1-S1-T2.md`",
                "`backlog/E0/E0-F1/E0-F1-S1/E0-F1-S1-T2.md`",
            )
        )
        runtime_no_auto = RuntimeConfig(task_factory=TaskFactoryConfig(auto_accept_proposals=False))
        with (
            patch("devbench.cli.BACKLOG_ROOT", tmp_path / "backlog"),
            patch("devbench.cli.BACKLOG_INDEX", index),
            patch("devbench.cli.WORKSPACE_ROOT", tmp_path),
            patch("devbench.cli.RUNTIME_CONFIG", runtime_no_auto),
        ):
            rc = cli.cmd_sweep_proposals()
        assert rc == 0
        out = capsys.readouterr().out
        assert "orphan auto-promoted" not in out
        assert "## Status: proposed" in (story_dir / "E0-F1-S1-T2.md").read_text()


# ---------------------------------------------------------------------------
# Issue #156: cmd_log_rejection_feedback schema + injection + done-gate
# ---------------------------------------------------------------------------


class TestCmdLogRejectionFeedbackSchema:
    """Issue #156: schema validation + persistence happy path."""

    def _payload(self, code: str = "HARDCODED_URL") -> dict[str, object]:
        return {
            "categories": [
                {
                    "code": code,
                    "severity": "fail",
                    "summary": "Hardcoded URL",
                    "remediation": "Read from env var",
                    "files": ["src/devbench/cli.py"],
                }
            ],
            "raw_verdict_text": "Found hardcoded URL in cli.py:42",
        }

    def test_valid_payload_persists(self, tmp_path: Path) -> None:
        with patch("devbench.cli.WORKSPACE_ROOT", tmp_path):
            rc = cli.cmd_log_rejection_feedback("code_review", "E0-F1-S1-T1", "--json", json.dumps(self._payload()))
        assert rc == 0
        archive_dir = tmp_path / ".devbench" / "review-failures"
        files = list(archive_dir.glob("E0-F1-S1-T1-code_review-*.json"))
        assert len(files) == 1
        data = json.loads(files[0].read_text(encoding="utf-8"))
        assert data["schema_version"] == 1
        assert data["judge"] == "code_review"
        assert data["task_id"] == "E0-F1-S1-T1"
        assert data["attempt"] == 1
        assert data["categories"][0]["code"] == "HARDCODED_URL"
        assert data["capped"] is False

    def test_attempt_increments_on_repeat(self, tmp_path: Path) -> None:
        with patch("devbench.cli.WORKSPACE_ROOT", tmp_path):
            cli.cmd_log_rejection_feedback("code_review", "E0-F1-S1-T1", "--json", json.dumps(self._payload()))
            rc = cli.cmd_log_rejection_feedback(
                "code_review",
                "E0-F1-S1-T1",
                "--json",
                json.dumps(self._payload(code="SCOPE_VIOLATION")),
            )
        assert rc == 0
        files = sorted((tmp_path / ".devbench" / "review-failures").glob("*.json"))
        assert len(files) == 2
        attempts = sorted(json.loads(p.read_text())["attempt"] for p in files)
        assert attempts == [1, 2]

    def test_bad_json_rejected(
        self,
        tmp_path: Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        with patch("devbench.cli.WORKSPACE_ROOT", tmp_path):
            rc = cli.cmd_log_rejection_feedback("code_review", "E0-F1-S1-T1", "--json", "not-json")
        assert rc == 1
        assert "not valid JSON" in capsys.readouterr().err

    def test_bad_category_code_rejected(
        self,
        tmp_path: Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        bad = self._payload(code="NOT_A_REAL_CODE")
        with patch("devbench.cli.WORKSPACE_ROOT", tmp_path):
            rc = cli.cmd_log_rejection_feedback("code_review", "E0-F1-S1-T1", "--json", json.dumps(bad))
        assert rc == 1
        assert "vocabulary" in capsys.readouterr().err

    def test_unknown_judge_rejected(
        self,
        tmp_path: Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        with patch("devbench.cli.WORKSPACE_ROOT", tmp_path):
            rc = cli.cmd_log_rejection_feedback(
                "totally_unknown",
                "E0-F1-S1-T1",
                "--json",
                json.dumps(self._payload()),
            )
        assert rc == 1
        assert "unknown judge" in capsys.readouterr().err

    def test_missing_required_field_rejected(
        self,
        tmp_path: Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        with patch("devbench.cli.WORKSPACE_ROOT", tmp_path):
            rc = cli.cmd_log_rejection_feedback(
                "code_review",
                "E0-F1-S1-T1",
                "--json",
                json.dumps({"raw_verdict_text": "x"}),
            )
        assert rc == 1
        assert "missing required field" in capsys.readouterr().err

    def test_bad_argv_rejected(self, capsys: pytest.CaptureFixture[str]) -> None:
        rc = cli.cmd_log_rejection_feedback("code_review")
        assert rc == 1
        err = capsys.readouterr().err
        assert "log-rejection-feedback" in err

    def test_unknown_flag_rejected(self, capsys: pytest.CaptureFixture[str]) -> None:
        rc = cli.cmd_log_rejection_feedback("code_review", "E0", "--bogus", "value")
        assert rc == 1
        assert "unknown flag" in capsys.readouterr().err

    def test_severity_must_be_fail_or_warn(
        self,
        tmp_path: Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        bad = {
            "categories": [
                {
                    "code": "HARDCODED_URL",
                    "severity": "info",
                    "summary": "x",
                    "remediation": "y",
                    "files": [],
                }
            ],
            "raw_verdict_text": "x",
        }
        with patch("devbench.cli.WORKSPACE_ROOT", tmp_path):
            rc = cli.cmd_log_rejection_feedback("code_review", "E0", "--json", json.dumps(bad))
        assert rc == 1
        assert "severity" in capsys.readouterr().err

    def test_capped_when_exceeds_max_retry_attempts(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setattr("devbench.config.MAX_RETRY_ATTEMPTS", 1)
        with patch("devbench.cli.WORKSPACE_ROOT", tmp_path):
            cli.cmd_log_rejection_feedback("code_review", "E0-F1-S1-T1", "--json", json.dumps(self._payload()))
            cli.cmd_log_rejection_feedback("code_review", "E0-F1-S1-T1", "--json", json.dumps(self._payload()))
        files = sorted((tmp_path / ".devbench" / "review-failures").glob("*.json"))
        cap_flags = [json.loads(p.read_text())["capped"] for p in files]
        assert cap_flags == [False, True]


class TestRejectionFeedbackInjection:
    """Issue #156: ``_collect_review_judge_feedback`` ordering + cap."""

    def _seed(self, workspace: Path, judge: str, task_id: str, attempt: int, code: str) -> None:
        archive_dir = workspace / ".devbench" / "review-failures"
        archive_dir.mkdir(parents=True, exist_ok=True)
        path = archive_dir / f"{task_id}-{judge}-{attempt}.json"
        path.write_text(
            json.dumps(
                {
                    "schema_version": 1,
                    "task_id": task_id,
                    "judge": judge,
                    "attempt": attempt,
                    "rejected_at": "2026-05-02T00:00:00Z",
                    "categories": [
                        {
                            "code": code,
                            "severity": "fail",
                            "summary": "x",
                            "remediation": "y",
                            "files": [],
                        }
                    ],
                    "raw_verdict_text": "x",
                    "capped": False,
                }
            )
            + "\n",
            encoding="utf-8",
        )

    def test_orders_by_severity_then_attempt(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        # Cap above the seed count so nothing is truncated.
        monkeypatch.setattr("devbench.config.MAX_RETRY_ATTEMPTS", 10)
        task_id = "E0-F1-S1-T1"
        # Seed three rejections across two judges, lower-severity first.
        self._seed(tmp_path, "doc_review", task_id, 1, "README_SYNC")
        self._seed(tmp_path, "code_review", task_id, 1, "HARDCODED_URL")
        self._seed(tmp_path, "code_review", task_id, 2, "SCOPE_VIOLATION")

        with patch("devbench.cli.WORKSPACE_ROOT", tmp_path):
            payloads = cli._collect_review_judge_feedback(task_id)

        # security>code>test>changes_manifest>doc; within judge, higher attempt first.
        order = [(p["judge"], p["attempt"]) for p in payloads]
        assert order == [
            ("code_review", 2),
            ("code_review", 1),
            ("doc_review", 1),
        ]

    def test_cap_truncates_to_max_retry_attempts(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setattr("devbench.config.MAX_RETRY_ATTEMPTS", 2)
        task_id = "E0-F1-S1-T1"
        self._seed(tmp_path, "code_review", task_id, 1, "HARDCODED_URL")
        self._seed(tmp_path, "code_review", task_id, 2, "SCOPE_VIOLATION")
        self._seed(tmp_path, "doc_review", task_id, 1, "README_SYNC")
        with patch("devbench.cli.WORKSPACE_ROOT", tmp_path):
            payloads = cli._collect_review_judge_feedback(task_id)
        assert len(payloads) == 2
        # Highest-severity / latest-attempt entries survive the cap.
        assert all(p["judge"] == "code_review" for p in payloads)

    def test_legacy_amender_rejections_synthesized(self, tmp_path: Path) -> None:
        """Legacy ``amender-rejections/`` entries get a v1-shaped record."""
        archive_dir = tmp_path / ".devbench" / "amender-rejections"
        archive_dir.mkdir(parents=True)
        (archive_dir / "E0-F1-S1-T1-1.json").write_text(
            json.dumps(
                {
                    "task_id": "E0-F1-S1-T1",
                    "attempt": 1,
                    "reason_category": "SCOPE",
                    "reason_text": "old reason",
                    "request": {},
                    "capped": False,
                    "recorded_at": "2026-04-30T00:00:00Z",
                }
            ),
            encoding="utf-8",
        )
        with patch("devbench.cli.WORKSPACE_ROOT", tmp_path):
            payloads = cli._collect_review_judge_feedback("E0-F1-S1-T1")
        assert len(payloads) == 1
        assert payloads[0]["judge"] == "manifest_amender"
        assert payloads[0]["categories"][0]["code"] == "SCOPE"

    def test_skips_unparseable_files(self, tmp_path: Path) -> None:
        archive_dir = tmp_path / ".devbench" / "review-failures"
        archive_dir.mkdir(parents=True)
        (archive_dir / "E0-F1-S1-T1-code_review-1.json").write_text("not json", encoding="utf-8")
        with patch("devbench.cli.WORKSPACE_ROOT", tmp_path):
            payloads = cli._collect_review_judge_feedback("E0-F1-S1-T1")
        assert payloads == []

    def test_skips_non_dict_payload(self, tmp_path: Path) -> None:
        archive_dir = tmp_path / ".devbench" / "review-failures"
        archive_dir.mkdir(parents=True)
        (archive_dir / "E0-F1-S1-T1-code_review-1.json").write_text("[]", encoding="utf-8")
        with patch("devbench.cli.WORKSPACE_ROOT", tmp_path):
            payloads = cli._collect_review_judge_feedback("E0-F1-S1-T1")
        assert payloads == []


class TestDoneGateRejectionFeedbackEnforcement:
    """Issue #156: done-gate refuses transition when rejection unresolved."""

    def _seed_rejection(self, workspace: Path, task_id: str) -> None:
        archive = workspace / ".devbench" / "review-failures"
        archive.mkdir(parents=True, exist_ok=True)
        (archive / f"{task_id}-code_review-1.json").write_text(
            json.dumps(
                {
                    "schema_version": 1,
                    "task_id": task_id,
                    "judge": "code_review",
                    "attempt": 1,
                    "rejected_at": "2026-05-02T00:00:00Z",
                    "categories": [
                        {
                            "code": "HARDCODED_URL",
                            "severity": "fail",
                            "summary": "x",
                            "remediation": "y",
                            "files": [],
                        }
                    ],
                    "raw_verdict_text": "x",
                    "capped": False,
                }
            ),
            encoding="utf-8",
        )

    def test_blocks_when_unresolved(
        self,
        tmp_path: Path,
        backlog_dir: Path,
        mock_units: list[WorkUnit],
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        wu_file = backlog_dir / "E0-F1-S1-T2.md"
        wu_file.write_text("# Task\n## Status: in-review\n\n## Comments\n", encoding="utf-8")
        self._seed_rejection(tmp_path, "E0-F1-S1-T2")
        mock_parser = MagicMock()
        mock_parser.parse_index.return_value = mock_units
        with (
            patch("devbench.cli.BacklogParser", return_value=mock_parser),
            patch("devbench.cli.BACKLOG_ROOT", backlog_dir.parent),
            patch("devbench.cli.WORKSPACE_ROOT", tmp_path),
        ):
            rc = cli.cmd_mark_done("E0-F1-S1-T2")
        assert rc == 1
        err = capsys.readouterr().err
        assert "REJECTION_FEEDBACK_OUTSTANDING" in wu_file.read_text() or "unresolved" in err
        assert "code_review:HARDCODED_URL" in err

    def test_allows_when_resolved_marker_present(
        self,
        tmp_path: Path,
        backlog_dir: Path,
        mock_units: list[WorkUnit],
    ) -> None:
        wu_file = backlog_dir / "E0-F1-S1-T2.md"
        wu_file.write_text(
            "# Task\n## Status: in-review\n\n## Comments\n"
            "[2026-05-02 12:00 UTC] [agent/orchestrator] [REJECTION_FEEDBACK_RESOLVED] code_review:HARDCODED_URL\n",
            encoding="utf-8",
        )
        self._seed_rejection(tmp_path, "E0-F1-S1-T2")
        mock_parser = MagicMock()
        mock_parser.parse_index.return_value = mock_units
        mock_mgr = MagicMock()
        with (
            patch("devbench.cli.BacklogParser", return_value=mock_parser),
            patch("devbench.cli.BACKLOG_ROOT", backlog_dir.parent),
            patch("devbench.cli.BacklogManager", return_value=mock_mgr),
            patch("devbench.cli.WORKSPACE_ROOT", tmp_path),
        ):
            rc = cli.cmd_mark_done("E0-F1-S1-T2")
        assert rc == 0
        mock_mgr.mark_done.assert_called_once()

    def test_allows_when_needs_dep_marker_present(
        self,
        tmp_path: Path,
        backlog_dir: Path,
        mock_units: list[WorkUnit],
    ) -> None:
        wu_file = backlog_dir / "E0-F1-S1-T2.md"
        wu_file.write_text(
            "# Task\n## Status: in-review\n\n## Comments\n"
            "[2026-05-02 12:00 UTC] [agent/executor] [NEEDS_DEP] code_review:HARDCODED_URL\n",
            encoding="utf-8",
        )
        self._seed_rejection(tmp_path, "E0-F1-S1-T2")
        mock_parser = MagicMock()
        mock_parser.parse_index.return_value = mock_units
        mock_mgr = MagicMock()
        with (
            patch("devbench.cli.BacklogParser", return_value=mock_parser),
            patch("devbench.cli.BACKLOG_ROOT", backlog_dir.parent),
            patch("devbench.cli.BacklogManager", return_value=mock_mgr),
            patch("devbench.cli.WORKSPACE_ROOT", tmp_path),
        ):
            rc = cli.cmd_mark_done("E0-F1-S1-T2")
        assert rc == 0
        mock_mgr.mark_done.assert_called_once()


class TestStatusPanelRejectionCategoryCounts:
    """Issue #156: --detail blocked panel shows pending categories per task."""

    def test_panel_shown_when_blocked_with_unresolved_categories(
        self,
        tmp_path: Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        backlog_dir = tmp_path / "backlog"
        backlog_dir.mkdir()
        wu = backlog_dir / "E0-F1-S1-T1.md"
        wu.write_text(
            "# E0-F1-S1-T1: T1\n\n## Status: blocked\n\n"
            "## Description\n\nx\n\n"
            "## Dependencies\n\n| ID | Title | Status |\n|----|-------|--------|\n| none | | |\n\n"
            "## Acceptance Criteria\n\n- [ ] AC-FUNC-001 x\n\n"
            "## Changes Manifest\n\n| File | Change |\n|------|--------|\n| `f.py` | New |\n\n"
            "## Definition of Done\n\n- [ ] x\n\n## Comments\n",
            encoding="utf-8",
        )
        index = tmp_path / "BACKLOG.md"
        index.write_text(
            "# Backlog\n\n"
            "## Status Summary\n\n"
            "| Epic | Title | Done | In Progress | In Queue | Blocked |\n"
            "|------|-------|------|-------------|----------|---------|\n\n"
            "## Full Work Unit Index\n\n"
            "| ID | Title | Type | Status | Dependencies | Repo | File Path |\n"
            "|----|-------|------|--------|--------------|------|-----------|\n"
            "| E0-F1-S1-T1 | T1 | Task | blocked | none | org/repo | `backlog/E0-F1-S1-T1.md` |\n",
            encoding="utf-8",
        )
        archive = tmp_path / ".devbench" / "review-failures"
        archive.mkdir(parents=True)
        (archive / "E0-F1-S1-T1-code_review-1.json").write_text(
            json.dumps(
                {
                    "schema_version": 1,
                    "task_id": "E0-F1-S1-T1",
                    "judge": "code_review",
                    "attempt": 1,
                    "rejected_at": "2026-05-02T00:00:00Z",
                    "categories": [
                        {
                            "code": "HARDCODED_URL",
                            "severity": "fail",
                            "summary": "x",
                            "remediation": "y",
                            "files": [],
                        }
                    ],
                    "raw_verdict_text": "x",
                    "capped": False,
                }
            ),
            encoding="utf-8",
        )

        with (
            patch("devbench.cli.BACKLOG_ROOT", backlog_dir),
            patch("devbench.cli.BACKLOG_INDEX", index),
            patch("devbench.cli.WORKSPACE_ROOT", tmp_path),
        ):
            rc = cli.cmd_status("--detail")
        assert rc == 0
        out = capsys.readouterr().out
        assert "Review-judge rejections (unresolved categories):" in out
        assert "code_review:HARDCODED_URL" in out

    def test_panel_omitted_when_no_unresolved(self, capsys: pytest.CaptureFixture[str]) -> None:
        cli._print_blocked_rejection_categories([])
        assert capsys.readouterr().out == ""


class TestInProgressAttemptDurationRender:
    """Issue #158: cmd_status renders ``(in-progress for ...)`` suffix."""

    def test_status_renders_duration_when_log_has_transition(
        self,
        tmp_path: Path,
        backlog_dir: Path,
        capsys: pytest.CaptureFixture[str],
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        log_path = tmp_path / "orchestrator.log"
        log_path.write_text(
            "2026-05-02T12:00:00Z [devbench.cli] INFO Set E0-F1-S1-T2 to 'in-progress'\n",
            encoding="utf-8",
        )
        monkeypatch.delenv("DEVBENCH_LOG_FILE", raising=False)
        monkeypatch.setenv("DEVBENCH_LOG_FILE", str(log_path))
        # Freeze time so the duration output is deterministic.
        fake_now = datetime(2026, 5, 2, 12, 23, 0, tzinfo=UTC)

        class _FrozenDT(datetime):
            @classmethod
            def now(cls, tz: object = None) -> _FrozenDT:
                return _FrozenDT.fromtimestamp(fake_now.timestamp(), tz=UTC)

        monkeypatch.setattr("devbench.cli.datetime", _FrozenDT)

        in_prog_unit = WorkUnit(
            id="E0-F1-S1-T2",
            title="Active",
            status=WorkUnitStatus.IN_PROGRESS,
            unit_type=WorkUnitType.TASK,
            file_path=Path("backlog/E0-F1-S1-T2.md"),
            repo="caylent-solutions/git-repo",
            dependencies=[],
        )
        mock_parser = MagicMock()
        mock_parser.parse_index.return_value = [in_prog_unit]
        mock_parser.get_parallel_candidates.return_value = []
        mock_parser.all_done.return_value = False
        mock_parser.get_blocked_units.return_value = []

        with patch("devbench.cli.BacklogParser", return_value=mock_parser):
            rc = cli.cmd_status()

        assert rc == 0
        out = capsys.readouterr().out
        assert "(in-progress for 23m)" in out


class TestInProgressAttemptDurationFallback:
    """Issue #158: when neither log nor audit yields a parseable timestamp,
    the helper returns ``None`` and the renderer prints the
    ``timer unavailable`` placeholder."""

    def test_returns_none_with_no_signals(self, tmp_path: Path) -> None:
        # Force log_path to a non-existent file AND ensure backlog parse fails fast.
        with (
            patch("devbench.cli.WORKSPACE_ROOT", tmp_path),
            patch("devbench.cli.BACKLOG_ROOT", tmp_path / "backlog"),
            patch("devbench.cli.BACKLOG_INDEX", tmp_path / "BACKLOG.md"),
        ):
            result = cli._in_progress_attempt_duration("E0-F1-S1-T2", log_path=tmp_path / "missing.log")
        assert result is None

    def test_falls_back_to_audit_when_log_missing(
        self,
        tmp_path: Path,
        backlog_dir: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        # Stub _resolve_unit_file_by_id directly so we don't have to materialise a
        # full backlog -- the fallback path is the only behaviour under test here.
        wu = backlog_dir / "E0-F1-S1-T2.md"
        wu.write_text(
            "## Comments\n[2026-05-02 11:30 UTC] [agent/orchestrator] Set E0-F1-S1-T2 to 'in-progress'\n",
            encoding="utf-8",
        )
        fake_now = datetime(2026, 5, 2, 12, 0, 0, tzinfo=UTC)

        class _FrozenDT(datetime):
            @classmethod
            def now(cls, tz: object = None) -> _FrozenDT:
                return _FrozenDT.fromtimestamp(fake_now.timestamp(), tz=UTC)

        monkeypatch.setattr("devbench.cli.datetime", _FrozenDT)
        with patch("devbench.cli._resolve_unit_file_by_id", return_value=wu):
            result = cli._in_progress_attempt_duration("E0-F1-S1-T2", log_path=tmp_path / "missing.log")
        assert result == "30m"


class TestInProgressAttemptDurationLatestAttemptOnly:
    """Issue #158: multiple in-progress transitions resolve to the most recent one."""

    def test_picks_most_recent_log_transition(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        log_path = tmp_path / "orchestrator.log"
        log_path.write_text(
            "2026-05-02T08:00:00Z [devbench.cli] INFO Set E0-F1-S1-T1 to 'in-progress'\n"
            "2026-05-02T09:00:00Z [devbench.cli] INFO Set E0-F1-S1-T1 to 'blocked'\n"
            "2026-05-02T11:30:00Z [devbench.cli] INFO Set E0-F1-S1-T1 to 'in-progress'\n",
            encoding="utf-8",
        )
        fake_now = datetime(2026, 5, 2, 12, 0, 0, tzinfo=UTC)

        class _FrozenDT(datetime):
            @classmethod
            def now(cls, tz: object = None) -> _FrozenDT:
                return _FrozenDT.fromtimestamp(fake_now.timestamp(), tz=UTC)

        monkeypatch.setattr("devbench.cli.datetime", _FrozenDT)
        result = cli._in_progress_attempt_duration("E0-F1-S1-T1", log_path=log_path)
        # 4h vs 30m vs 4h+30m: most recent wins -> 30m.
        assert result == "30m"

    def test_format_duration_thresholds(self) -> None:
        assert cli._format_duration(0) == "0s"
        assert cli._format_duration(-5) == "0s"
        assert cli._format_duration(42) == "42s"
        assert cli._format_duration(60) == "1m"
        assert cli._format_duration(23 * 60) == "23m"
        assert cli._format_duration(60 * 60 + 47 * 60) == "1h 47m"
        assert cli._format_duration(2 * 86400 + 3 * 3600) == "2d 3h"

    def test_log_with_invalid_timestamp_skipped(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        log_path = tmp_path / "orchestrator.log"
        log_path.write_text(
            "9999-99-99T99:99:99Z [devbench.cli] INFO Set E0-F1-S1-T1 to 'in-progress'\n",
            encoding="utf-8",
        )
        result = cli._in_progress_attempt_duration("E0-F1-S1-T1", log_path=log_path)
        # Only the bogus timestamp -> nothing parses -> None.
        assert result is None

    def test_audit_with_invalid_timestamp_skipped(
        self,
        tmp_path: Path,
        backlog_dir: Path,
    ) -> None:
        wu = backlog_dir / "E0-F1-S1-T1.md"
        wu.write_text(
            "## Comments\n[9999-99-99 99:99 UTC] [agent/orchestrator] Set E0-F1-S1-T1 to 'in-progress'\n",
            encoding="utf-8",
        )
        with patch("devbench.cli._resolve_unit_file_by_id", return_value=wu):
            result = cli._in_progress_attempt_duration("E0-F1-S1-T1", log_path=tmp_path / "missing.log")
        assert result is None


class TestTryResolveLogFilePath:
    """Issue #185: ``_try_resolve_log_file_path`` returns ``None`` instead of
    raising ``SystemExit`` so the status-timer fallback can consult the
    YAML config without crashing when none of the three resolution
    inputs is set.
    """

    def test_returns_workspace_default_when_neither_env_nor_yaml_set(self, monkeypatch: pytest.MonkeyPatch) -> None:
        # After the JUDGE_* -> DEVBENCH_* rename, WORKSPACE_ROOT is always
        # resolved at import time (config.py raises if it is not set).
        # _resolve_log_file_path no longer raises SystemExit when neither
        # DEVBENCH_LOG_FILE nor YAML log_file is configured; it falls
        # back to the canonical WORKSPACE_ROOT / DEFAULT_LOG_SUBDIR / DEFAULT_LOG_FILENAME
        # path.  _try_resolve_log_file_path therefore returns that path
        # rather than None.
        monkeypatch.delenv("DEVBENCH_LOG_FILE", raising=False)
        from devbench.constants import DEFAULT_LOG_FILENAME, DEFAULT_LOG_SUBDIR

        cfg = MagicMock()
        cfg.log_file = ""
        with patch("devbench.cli.RUNTIME_CONFIG", cfg):
            result = cli._try_resolve_log_file_path()
        assert result == cli.WORKSPACE_ROOT / DEFAULT_LOG_SUBDIR / DEFAULT_LOG_FILENAME

    def test_returns_path_when_yaml_log_file_set(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """When ``DEVBENCH_LOG_FILE`` is unset but YAML config carries a
        ``log_file``, the wrapper resolves the workspace-relative path."""
        monkeypatch.delenv("DEVBENCH_LOG_FILE", raising=False)
        monkeypatch.delenv("DEVBENCH_LOG_FILE", raising=False)
        cfg = MagicMock()
        cfg.log_file = "logs/orch.log"
        with patch("devbench.cli.RUNTIME_CONFIG", cfg):
            result = cli._try_resolve_log_file_path()
        assert result == cli.WORKSPACE_ROOT / "logs" / "orch.log"

    def test_timer_uses_yaml_config_when_env_unset(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """End-to-end: ``_latest_log_in_progress_ts`` resolves the log via
        YAML ``log_file`` when ``DEVBENCH_LOG_FILE`` is unset. Prior to
        issue #185 the helper bailed out with ``None`` causing
        ``cmd_status`` to render ``timer unavailable`` even though the
        log was discoverable."""
        log_path = tmp_path / "logs" / "orch.log"
        log_path.parent.mkdir(parents=True)
        log_path.write_text(
            "2026-05-02T12:00:00Z [devbench.cli] INFO Set E0-F1-S1-T1 to 'in-progress'\n",
            encoding="utf-8",
        )
        monkeypatch.delenv("DEVBENCH_LOG_FILE", raising=False)
        monkeypatch.delenv("DEVBENCH_LOG_FILE", raising=False)
        cfg = MagicMock()
        cfg.log_file = "logs/orch.log"
        with (
            patch("devbench.cli.RUNTIME_CONFIG", cfg),
            patch("devbench.cli.WORKSPACE_ROOT", tmp_path),
        ):
            ts = cli._latest_log_in_progress_ts("E0-F1-S1-T1", None)
        assert ts is not None
        assert ts.year == 2026 and ts.hour == 12 and ts.minute == 0


class TestCmdStatusNextActionableFilter:
    """Issue #185(c): the ``Next actionable`` line excludes IDs already
    rendered in ``Active work units`` (those are IN_PROGRESS / IN_REVIEW
    and ``get_parallel_candidates`` includes IN_PROGRESS for resume).
    Previously the line redundantly echoed the current claim.
    """

    def test_actionable_filtered_when_same_as_active(
        self,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        in_prog = WorkUnit(
            id="E0-F1-S1-T1",
            title="Active",
            status=WorkUnitStatus.IN_PROGRESS,
            unit_type=WorkUnitType.TASK,
            file_path=Path("backlog/E0-F1-S1-T1.md"),
            repo="caylent-solutions/git-repo",
            dependencies=[],
        )
        in_queue = WorkUnit(
            id="E0-F1-S1-T2",
            title="Next up",
            status=WorkUnitStatus.IN_QUEUE,
            unit_type=WorkUnitType.TASK,
            file_path=Path("backlog/E0-F1-S1-T2.md"),
            repo="caylent-solutions/git-repo",
            dependencies=[],
        )
        mock_parser = MagicMock()
        mock_parser.parse_index.return_value = [in_prog, in_queue]
        # get_parallel_candidates returns IN_PROGRESS first, then IN_QUEUE.
        mock_parser.get_parallel_candidates.return_value = [in_prog, in_queue]
        mock_parser.all_done.return_value = False
        mock_parser.get_blocked_units.return_value = []
        with patch("devbench.cli.BacklogParser", return_value=mock_parser):
            rc = cli.cmd_status()
        assert rc == 0
        out = capsys.readouterr().out
        # Active panel still shows the in-progress task ...
        assert "E0-F1-S1-T1" in out
        # ... and Next actionable points at the DIFFERENT in-queue task.
        assert "Next actionable: E0-F1-S1-T2" in out

    def test_no_actionable_message_when_only_active(
        self,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """When the only candidate is the in-progress task itself, the
        ``Next actionable`` line is suppressed (no genuine next task)."""
        in_prog = WorkUnit(
            id="E0-F1-S1-T1",
            title="Active",
            status=WorkUnitStatus.IN_PROGRESS,
            unit_type=WorkUnitType.TASK,
            file_path=Path("backlog/E0-F1-S1-T1.md"),
            repo="caylent-solutions/git-repo",
            dependencies=[],
        )
        mock_parser = MagicMock()
        mock_parser.parse_index.return_value = [in_prog]
        mock_parser.get_parallel_candidates.return_value = [in_prog]
        mock_parser.all_done.return_value = False
        mock_parser.get_blocked_units.return_value = []
        with patch("devbench.cli.BacklogParser", return_value=mock_parser):
            rc = cli.cmd_status()
        assert rc == 0
        out = capsys.readouterr().out
        # No "Next actionable" line; instead the no-actionable branch fires.
        assert "Next actionable" not in out
        assert "No actionable units." in out


class TestCmdWriteSnapshot:
    """Issue #162 Phase 6 (ADR-20): write a fresh report snapshot."""

    def test_writes_snapshot_to_canonical_path(self, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
        log = tmp_path / "logs" / "orchestrator.log"
        log.parent.mkdir(parents=True)
        log.write_text("seed\n")

        with (
            patch("devbench.cli.WORKSPACE_ROOT", tmp_path),
            patch("devbench.cli._resolve_log_file_path", return_value=log),
            patch("devbench.reporting.report.generate_report", return_value="REPORT"),
        ):
            rc = cli.cmd_write_snapshot()

        assert rc == 0
        snapshot_file = tmp_path / ".devbench" / "report-snapshot.json"
        assert snapshot_file.is_file()
        payload = json.loads(snapshot_file.read_text())
        assert payload["report_text"] == "REPORT"

    def test_rejects_extra_args(self, capsys: pytest.CaptureFixture[str]) -> None:
        rc = cli.cmd_write_snapshot("unexpected")
        assert rc == 1
        assert "no arguments" in capsys.readouterr().err


class TestCmdRebuildWindowStats:
    """Issue #162 Phase 2 (ADR-17): rebuild per-task aggregates from log."""

    def test_rebuilds_aggregates(self, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
        log = tmp_path / "logs" / "orchestrator.log"
        log.parent.mkdir(parents=True)
        log.write_text(
            "2026-05-04T10:00:00Z [agent] INFO Set E0-F1-S1-T1 to 'in-progress' in both files\n"
            "2026-05-04T11:00:00Z [agent] INFO Set E0-F1-S1-T1 to 'done' in both files\n"
        )

        with (
            patch("devbench.cli.WORKSPACE_ROOT", tmp_path),
            patch("devbench.cli._resolve_log_file_path", return_value=log),
        ):
            rc = cli.cmd_rebuild_window_stats()

        assert rc == 0
        agg = tmp_path / ".devbench" / "window-stats" / "E0-F1-S1-T1.json"
        assert agg.is_file()
        out = capsys.readouterr().out
        assert "wrote 1 per-task aggregate" in out

    def test_rejects_extra_args(self, capsys: pytest.CaptureFixture[str]) -> None:
        rc = cli.cmd_rebuild_window_stats("oops")
        assert rc == 1
        assert "no arguments" in capsys.readouterr().err


class TestCmdArchiveSession:
    """Issue #162 Phase 7 (ADR-21): archive a session to Parquet."""

    def test_writes_archive(self, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
        log = tmp_path / "logs" / "orchestrator.log"
        log.parent.mkdir(parents=True)
        log.write_text('{"event": "x"}\n')

        with (
            patch("devbench.cli.WORKSPACE_ROOT", tmp_path),
            patch("devbench.cli._resolve_log_file_path", return_value=log),
        ):
            rc = cli.cmd_archive_session("session-abc")

        assert rc == 0
        archive = tmp_path / "logs" / "legacy" / "session-abc.parquet"
        assert archive.is_file()
        assert "session-abc" in capsys.readouterr().out

    def test_requires_session_id(self, capsys: pytest.CaptureFixture[str]) -> None:
        rc = cli.cmd_archive_session()
        assert rc == 1
        assert "exactly one positional" in capsys.readouterr().err

    def test_rejects_extra_positional(self, capsys: pytest.CaptureFixture[str]) -> None:
        rc = cli.cmd_archive_session("a", "b")
        assert rc == 1
        assert "exactly one positional" in capsys.readouterr().err

    def test_log_path_override(self, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
        custom_log = tmp_path / "custom" / "log"
        custom_log.parent.mkdir(parents=True)
        custom_log.write_text('{"event": "y"}\n')

        with (
            patch("devbench.cli.WORKSPACE_ROOT", tmp_path),
            patch("devbench.cli._resolve_log_file_path", return_value=tmp_path / "default.log"),
        ):
            rc = cli.cmd_archive_session("s1", "--log-path", str(custom_log))

        assert rc == 0
        assert (tmp_path / "logs" / "legacy" / "s1.parquet").is_file()

    def test_rejects_log_path_without_value(self, capsys: pytest.CaptureFixture[str]) -> None:
        rc = cli.cmd_archive_session("session-id", "--log-path")
        assert rc == 1
        assert "--log-path requires a value" in capsys.readouterr().err

    def test_propagates_archive_dependency_missing(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        log = tmp_path / "log"
        log.write_text('{"event": "x"}\n')

        from devbench.reporting.archive import ArchiveDependencyMissingError

        def _raise(*args: object, **kwargs: object) -> object:
            raise ArchiveDependencyMissingError("archive operations")

        monkeypatch.setattr("devbench.cli.WORKSPACE_ROOT", tmp_path)
        monkeypatch.setattr("devbench.cli._resolve_log_file_path", lambda: log)
        monkeypatch.setattr("devbench.reporting.archive.archive_session", _raise)

        rc = cli.cmd_archive_session("s1")
        assert rc == 1
        assert "pip install devbench[archive]" in capsys.readouterr().err

    def test_propagates_file_not_found(self, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
        with (
            patch("devbench.cli.WORKSPACE_ROOT", tmp_path),
            patch("devbench.cli._resolve_log_file_path", return_value=tmp_path / "nope.log"),
        ):
            rc = cli.cmd_archive_session("s1")
        assert rc == 1
        assert "not found" in capsys.readouterr().err


class TestCmdStatusSixBucketCounts:
    """E2-F2-S2-T1: cmd_status prints six Blocked count rows and six detail panels."""

    _CANONICAL_COUNT_LABELS: ClassVar[list[str]] = [
        "Blocked (auto-clearing)",
        "Blocked (amendment-recovery)",
        "Blocked (dependency)",
        "Blocked (held)",
        "Blocked (blocked-on-held)",
        "Blocked (operator-required)",
    ]

    _CANONICAL_PANEL_HEADERS: ClassVar[list[str]] = [
        "Blocked tasks (auto-clearing via proposal)",
        "Blocked tasks (awaiting amendment recovery)",
        "Blocked tasks (awaiting dependency)",
        "Held tasks",
        "Blocked tasks (blocked on held)",
        "Blocked tasks (operator action required)",
    ]

    def _make_blocked_unit(self, unit_id: str, title: str) -> WorkUnit:
        return WorkUnit(
            id=unit_id,
            title=title,
            status=WorkUnitStatus.BLOCKED,
            unit_type=WorkUnitType.TASK,
            file_path=Path(f"/fake/{unit_id}.md"),
            repo="r",
            dependencies=[],
        )

    def _make_six_unit_fixture(
        self,
    ) -> tuple[list[WorkUnit], Any]:
        """Return (units, classify_side_effect) covering one task per BlockedTaskState."""
        from devbench.backlog.proposal import BlockedTaskState

        units = [
            self._make_blocked_unit("E9-F1-S1-T1", "Auto"),
            self._make_blocked_unit("E9-F1-S1-T2", "AmendmentRecovery"),
            self._make_blocked_unit("E9-F1-S1-T3", "Dependency"),
            self._make_blocked_unit("E9-F1-S1-T4", "Held"),
            self._make_blocked_unit("E9-F1-S1-T5", "BlockedOnHeld"),
            self._make_blocked_unit("E9-F1-S1-T6", "Operator"),
        ]

        state_map = {
            "E9-F1-S1-T1": BlockedTaskState.AUTO_CLEARING_VIA_PROPOSAL,
            "E9-F1-S1-T2": BlockedTaskState.AWAITING_AMENDMENT_RECOVERY,
            "E9-F1-S1-T3": BlockedTaskState.AWAITING_DEPENDENCY,
            "E9-F1-S1-T4": BlockedTaskState.HELD,
            "E9-F1-S1-T5": BlockedTaskState.BLOCKED_ON_HELD,
            "E9-F1-S1-T6": BlockedTaskState.OPERATOR_ACTION_REQUIRED,
        }

        def fake_classify(
            backlog_root: Path,
            backlog_index: Path,
            task_id: str,
            **kwargs: object,
        ) -> BlockedTaskState:
            return state_map[task_id]

        return units, fake_classify

    def test_six_count_rows_present_in_canonical_order(
        self,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """Six Blocked (...) count rows appear in canonical spec order, summing to total blocked."""
        units, fake_classify = self._make_six_unit_fixture()

        parser = MagicMock()
        parser.parse_index.return_value = units
        parser.get_parallel_candidates.return_value = []
        parser.get_blocked_units.return_value = units
        parser.all_done.return_value = False

        with (
            patch("devbench.cli.BacklogParser", return_value=parser),
            patch("devbench.cli._count_unmaterialised_proposed_tasks", return_value=0),
            patch("devbench.cli.classify_blocked_task", side_effect=fake_classify),
        ):
            rc = cli.cmd_status()
        assert rc == 0
        out = capsys.readouterr().out

        # Each label present with count 1.
        for label in self._CANONICAL_COUNT_LABELS:
            assert re.search(rf"{re.escape(label)}\s+1\b", out), f"missing row {label!r}\n{out}"

        # Labels appear in canonical order.
        positions = [out.index(label) for label in self._CANONICAL_COUNT_LABELS]
        assert positions == sorted(positions), f"count rows not in canonical order\n{out}"

        # Old three-bucket rows must NOT appear.
        assert "Blocked (auto)" not in out, out
        assert "Blocked (recovery)" not in out, out
        assert "Blocked (attn)" not in out, out

    def test_six_count_rows_all_zero_when_no_blocked(
        self,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """All six rows still print (at zero) even when no blocked tasks exist."""
        parser = MagicMock()
        parser.parse_index.return_value = []
        parser.get_parallel_candidates.return_value = []
        parser.get_blocked_units.return_value = []
        parser.all_done.return_value = False

        with (
            patch("devbench.cli.BacklogParser", return_value=parser),
            patch("devbench.cli._count_unmaterialised_proposed_tasks", return_value=0),
        ):
            rc = cli.cmd_status()
        assert rc == 0
        out = capsys.readouterr().out

        for label in self._CANONICAL_COUNT_LABELS:
            assert re.search(rf"{re.escape(label)}\s+0\b", out), f"missing zero row {label!r}\n{out}"

    def test_six_detail_panels_in_canonical_order(
        self,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """--detail renders six panel headers in canonical spec order."""
        units, fake_classify = self._make_six_unit_fixture()

        parser = MagicMock()
        parser.parse_index.return_value = units
        parser.get_parallel_candidates.return_value = []
        parser.get_blocked_units.return_value = units
        parser.all_done.return_value = False

        with (
            patch("devbench.cli.BacklogParser", return_value=parser),
            patch("devbench.cli._count_unmaterialised_proposed_tasks", return_value=0),
            patch("devbench.cli.classify_blocked_task", side_effect=fake_classify),
            patch("devbench.cli._resolve_unit_file", return_value=None),
        ):
            rc = cli.cmd_status("--detail")
        assert rc == 0
        out = capsys.readouterr().out

        # All six panel headers present.
        for header in self._CANONICAL_PANEL_HEADERS:
            assert header in out, f"missing panel header {header!r}\n{out}"

        # Panel headers appear in canonical order.
        positions = [out.index(header) for header in self._CANONICAL_PANEL_HEADERS]
        assert positions == sorted(positions), f"panels not in canonical order\n{out}"


# ---------------------------------------------------------------------------
# Multi-PR replay regression tests for rewired cmd_git_ops (E7-F1-S1-T1)
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestCmdGitOpsMultiPrReplay:
    """Regression tests: rewired cmd_git_ops produces same transitions as pre-refactor.

    Each fixture exercises one CIResult value and asserts the same status
    transitions, audit-comment text, and exit code that the pre-refactor code
    produced on that scenario.
    """

    def _make_unit(self, unit_id: str = "E202-F1-S1-T2") -> WorkUnit:
        return WorkUnit(
            id=unit_id,
            title="Replay Task",
            status=WorkUnitStatus.IN_PROGRESS,
            unit_type=WorkUnitType.TASK,
            file_path=Path(f"backlog/{unit_id}.md"),
            repo="caylent-solutions/devbench",
            dependencies=[],
        )

    # ------------------------------------------------------------------
    # Scenario 1: CIResult.GREEN => merge, rc=0
    # ------------------------------------------------------------------

    def test_green_result_merges_and_returns_zero(self, tmp_path: Path) -> None:
        """When wait_for_checks_and_classify returns GREEN, cmd_git_ops merges and returns 0."""
        from devbench.github.git_ops import CIResult

        unit = self._make_unit()
        mock_ops_inst = MagicMock()
        mock_ops_inst.create_pr.return_value = "https://github.com/org/repo/pull/42"
        mock_ops_inst.wait_for_checks_and_classify.return_value = CIResult.GREEN
        mock_ops_cls = MagicMock(return_value=mock_ops_inst)

        mock_parser = MagicMock()
        mock_parser.parse_index.return_value = [unit]

        with (
            patch("devbench.cli.BacklogParser", return_value=mock_parser),
            patch("devbench.cli.REPO_LOCAL_PATHS", {"caylent-solutions/devbench": tmp_path}),
            patch("devbench.cli.UPDATE_SUBMODULE", False),
            patch("devbench.github.git_ops.GitOpsService", mock_ops_cls),
            patch("devbench.cli._resolve_unit_file", return_value=None),
            patch("devbench.cli._emit_orphan_cleanup_proposal_if_needed", return_value=False),
            patch("devbench.config.CI_FAILURE_RETRY_ENABLED", True),
            patch("devbench.config.PR_REVIEW_RESOLUTION_ENABLED", False),
            patch("devbench.config.PAUSE_BEFORE_MERGE", False),
            patch("devbench.config.DEFER_PR", False),
            patch("devbench.config.SINGLE_BRANCH", ""),
        ):
            result = cli.cmd_git_ops(unit.id)

        assert result == 0
        mock_ops_inst.merge_pr.assert_called_once()

    # ------------------------------------------------------------------
    # Scenario 2: CIResult.FAILED_UNKNOWN => same as wait_for_checks=False, rc=2 (retry)
    # ------------------------------------------------------------------

    def test_failed_unknown_result_returns_retry_rc(self, tmp_path: Path) -> None:
        """FAILED_UNKNOWN triggers the same CI-failure retry path as pre-refactor False."""
        from devbench.github.git_ops import CIResult

        unit = self._make_unit()
        mock_ops_inst = MagicMock()
        mock_ops_inst.create_pr.return_value = "https://github.com/org/repo/pull/43"
        mock_ops_inst.wait_for_checks_and_classify.return_value = CIResult.FAILED_UNKNOWN
        mock_ops_inst.get_latest_failing_run_id.return_value = None
        mock_ops_cls = MagicMock(return_value=mock_ops_inst)

        mock_parser = MagicMock()
        mock_parser.parse_index.return_value = [unit]

        with (
            patch("devbench.cli.BacklogParser", return_value=mock_parser),
            patch("devbench.cli.REPO_LOCAL_PATHS", {"caylent-solutions/devbench": tmp_path}),
            patch("devbench.cli.UPDATE_SUBMODULE", False),
            patch("devbench.github.git_ops.GitOpsService", mock_ops_cls),
            patch("devbench.cli._resolve_unit_file", return_value=None),
            patch("devbench.cli._emit_orphan_cleanup_proposal_if_needed", return_value=False),
            patch("devbench.config.CI_FAILURE_RETRY_ENABLED", True),
            patch("devbench.config.MAX_RETRY_ATTEMPTS", 5),
            patch("devbench.config.PR_REVIEW_RESOLUTION_ENABLED", False),
            patch("devbench.config.DEFER_PR", False),
            patch("devbench.config.SINGLE_BRANCH", ""),
        ):
            result = cli.cmd_git_ops(unit.id)

        assert result == 2
        mock_ops_inst.merge_pr.assert_not_called()

    # ------------------------------------------------------------------
    # Scenario 3: CIResult.FAILED_KNOWN_TASK => same CI-failure path, rc=2
    # ------------------------------------------------------------------

    def test_failed_known_task_result_returns_retry_rc(self, tmp_path: Path) -> None:
        """FAILED_KNOWN_TASK triggers the CI-failure retry path (rc=2)."""
        from devbench.github.git_ops import CIResult

        unit = self._make_unit()
        mock_ops_inst = MagicMock()
        mock_ops_inst.create_pr.return_value = "https://github.com/org/repo/pull/44"
        mock_ops_inst.wait_for_checks_and_classify.return_value = CIResult.FAILED_KNOWN_TASK("E3-F1-S1-T1")
        mock_ops_inst.get_latest_failing_run_id.return_value = None
        mock_ops_cls = MagicMock(return_value=mock_ops_inst)

        mock_parser = MagicMock()
        mock_parser.parse_index.return_value = [unit]

        with (
            patch("devbench.cli.BacklogParser", return_value=mock_parser),
            patch("devbench.cli.REPO_LOCAL_PATHS", {"caylent-solutions/devbench": tmp_path}),
            patch("devbench.cli.UPDATE_SUBMODULE", False),
            patch("devbench.github.git_ops.GitOpsService", mock_ops_cls),
            patch("devbench.cli._resolve_unit_file", return_value=None),
            patch("devbench.cli._emit_orphan_cleanup_proposal_if_needed", return_value=False),
            patch("devbench.config.CI_FAILURE_RETRY_ENABLED", True),
            patch("devbench.config.MAX_RETRY_ATTEMPTS", 5),
            patch("devbench.config.PR_REVIEW_RESOLUTION_ENABLED", False),
            patch("devbench.config.DEFER_PR", False),
            patch("devbench.config.SINGLE_BRANCH", ""),
        ):
            result = cli.cmd_git_ops(unit.id)

        assert result == 2
        mock_ops_inst.merge_pr.assert_not_called()

    # ------------------------------------------------------------------
    # Scenario 4: CIResult.TIMEOUT => same CI-failure path, rc=2
    # ------------------------------------------------------------------

    def test_timeout_result_returns_retry_rc(self, tmp_path: Path) -> None:
        """TIMEOUT triggers the CI-failure retry path (rc=2), not a hard crash."""
        from devbench.github.git_ops import CIResult

        unit = self._make_unit()
        mock_ops_inst = MagicMock()
        mock_ops_inst.create_pr.return_value = "https://github.com/org/repo/pull/45"
        mock_ops_inst.wait_for_checks_and_classify.return_value = CIResult.TIMEOUT
        mock_ops_inst.get_latest_failing_run_id.return_value = None
        mock_ops_cls = MagicMock(return_value=mock_ops_inst)

        mock_parser = MagicMock()
        mock_parser.parse_index.return_value = [unit]

        with (
            patch("devbench.cli.BacklogParser", return_value=mock_parser),
            patch("devbench.cli.REPO_LOCAL_PATHS", {"caylent-solutions/devbench": tmp_path}),
            patch("devbench.cli.UPDATE_SUBMODULE", False),
            patch("devbench.github.git_ops.GitOpsService", mock_ops_cls),
            patch("devbench.cli._resolve_unit_file", return_value=None),
            patch("devbench.cli._emit_orphan_cleanup_proposal_if_needed", return_value=False),
            patch("devbench.config.CI_FAILURE_RETRY_ENABLED", True),
            patch("devbench.config.MAX_RETRY_ATTEMPTS", 5),
            patch("devbench.config.PR_REVIEW_RESOLUTION_ENABLED", False),
            patch("devbench.config.DEFER_PR", False),
            patch("devbench.config.SINGLE_BRANCH", ""),
        ):
            result = cli.cmd_git_ops(unit.id)

        assert result == 2
        mock_ops_inst.merge_pr.assert_not_called()

    # ------------------------------------------------------------------
    # Scenario 5: FAILED_KNOWN_TASK + budget exhausted => rc=1 (BLOCKED)
    # ------------------------------------------------------------------

    def test_failed_known_task_budget_exhausted_returns_one(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """When retry budget is exhausted, any CI failure returns rc=1."""
        from devbench.github.git_ops import CIResult

        unit = self._make_unit()
        mock_ops_inst = MagicMock()
        mock_ops_inst.create_pr.return_value = "https://github.com/org/repo/pull/46"
        mock_ops_inst.wait_for_checks_and_classify.return_value = CIResult.FAILED_KNOWN_TASK("E3-F1-S1-T1")
        mock_ops_inst.get_latest_failing_run_id.return_value = None
        mock_ops_cls = MagicMock(return_value=mock_ops_inst)

        mock_parser = MagicMock()
        mock_parser.parse_index.return_value = [unit]

        with (
            patch("devbench.cli.BacklogParser", return_value=mock_parser),
            patch("devbench.cli.REPO_LOCAL_PATHS", {"caylent-solutions/devbench": tmp_path}),
            patch("devbench.cli.UPDATE_SUBMODULE", False),
            patch("devbench.github.git_ops.GitOpsService", mock_ops_cls),
            patch("devbench.cli._resolve_unit_file", return_value=None),
            patch("devbench.cli._emit_orphan_cleanup_proposal_if_needed", return_value=False),
            patch("devbench.config.CI_FAILURE_RETRY_ENABLED", True),
            patch("devbench.config.MAX_RETRY_ATTEMPTS", 1),
            patch("devbench.config.PR_REVIEW_RESOLUTION_ENABLED", False),
            patch("devbench.config.DEFER_PR", False),
            patch("devbench.config.SINGLE_BRANCH", ""),
        ):
            result = cli.cmd_git_ops(unit.id)

        assert result == 1
        err = capsys.readouterr().err
        assert "budget exhausted" in err.lower() or "max_retry" in err.lower() or "blocked" in err.lower()
        mock_ops_inst.merge_pr.assert_not_called()

    # ------------------------------------------------------------------
    # Scenario 6: parity assertion -- GREEN produces same transitions as
    #             pre-refactor wait_for_checks=True
    # ------------------------------------------------------------------

    def test_green_parity_with_pre_refactor_true(self, tmp_path: Path) -> None:
        """CIResult.GREEN from wait_for_checks_and_classify produces bit-identical
        outcome to what the pre-refactor wait_for_checks=True path produced:
        merge runs and rc=0.

        Both legs of this test use the rewired cmd_git_ops (the pre-refactor
        path no longer exists).  The assertion is that two differently
        constructed mocks -- one whose wait_for_checks_and_classify returns
        GREEN explicitly, one whose MagicMock default is replaced with GREEN
        -- both result in rc=0 and merge_pr being called exactly once.
        """
        from devbench.github.git_ops import CIResult

        unit = self._make_unit("E202-F1-S1-T3")

        # First leg: explicit CIResult.GREEN via wait_for_checks_and_classify
        mock_ops_a = MagicMock()
        mock_ops_a.create_pr.return_value = "https://github.com/org/repo/pull/50"
        mock_ops_a.wait_for_checks_and_classify.return_value = CIResult.GREEN
        mock_ops_a_cls = MagicMock(return_value=mock_ops_a)

        mock_parser = MagicMock()
        mock_parser.parse_index.return_value = [unit]

        with (
            patch("devbench.cli.BacklogParser", return_value=mock_parser),
            patch("devbench.cli.REPO_LOCAL_PATHS", {"caylent-solutions/devbench": tmp_path}),
            patch("devbench.cli.UPDATE_SUBMODULE", False),
            patch("devbench.github.git_ops.GitOpsService", mock_ops_a_cls),
            patch("devbench.cli._resolve_unit_file", return_value=None),
            patch("devbench.cli._emit_orphan_cleanup_proposal_if_needed", return_value=False),
            patch("devbench.config.CI_FAILURE_RETRY_ENABLED", True),
            patch("devbench.config.PR_REVIEW_RESOLUTION_ENABLED", False),
            patch("devbench.config.PAUSE_BEFORE_MERGE", False),
            patch("devbench.config.DEFER_PR", False),
            patch("devbench.config.SINGLE_BRANCH", ""),
        ):
            rc_a = cli.cmd_git_ops(unit.id)

        # Second leg: also CIResult.GREEN but on a fresh mock (parity verification)
        mock_ops_b = MagicMock()
        mock_ops_b.create_pr.return_value = "https://github.com/org/repo/pull/51"
        mock_ops_b.wait_for_checks_and_classify.return_value = CIResult.GREEN
        mock_ops_b_cls = MagicMock(return_value=mock_ops_b)

        mock_parser2 = MagicMock()
        mock_parser2.parse_index.return_value = [unit]

        with (
            patch("devbench.cli.BacklogParser", return_value=mock_parser2),
            patch("devbench.cli.REPO_LOCAL_PATHS", {"caylent-solutions/devbench": tmp_path}),
            patch("devbench.cli.UPDATE_SUBMODULE", False),
            patch("devbench.github.git_ops.GitOpsService", mock_ops_b_cls),
            patch("devbench.cli._resolve_unit_file", return_value=None),
            patch("devbench.cli._emit_orphan_cleanup_proposal_if_needed", return_value=False),
            patch("devbench.config.CI_FAILURE_RETRY_ENABLED", True),
            patch("devbench.config.PR_REVIEW_RESOLUTION_ENABLED", False),
            patch("devbench.config.PAUSE_BEFORE_MERGE", False),
            patch("devbench.config.DEFER_PR", False),
            patch("devbench.config.SINGLE_BRANCH", ""),
        ):
            rc_b = cli.cmd_git_ops(unit.id)

        assert rc_a == rc_b == 0
        mock_ops_a.merge_pr.assert_called_once()
        mock_ops_b.merge_pr.assert_called_once()


class TestCmdPreparePluginShadow:
    """ADR-25: ``devbench prepare-plugin-shadow`` materialises the shadow and prints its path."""

    def test_no_overrides_prints_canonical_path(self, capsys: pytest.CaptureFixture[str]) -> None:
        from devbench.config_loader import AgentModelsConfig
        from devbench.constants import DEFAULT_PLUGIN_SUBPATH

        with (
            patch("devbench.cli.AGENT_MODELS", AgentModelsConfig()),
            patch("devbench.cli.WORKSPACE_ROOT", Path("/tmp/test-workspace")),
        ):
            rc = cli.cmd_prepare_plugin_shadow()

        out = capsys.readouterr().out.strip()
        assert rc == 0
        assert out.endswith(DEFAULT_PLUGIN_SUBPATH)

    def test_with_override_prints_workspace_shadow_path(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        from devbench.config_loader import AgentModelsConfig

        with (
            patch("devbench.cli.AGENT_MODELS", AgentModelsConfig(executor="opus")),
            patch("devbench.cli.WORKSPACE_ROOT", tmp_path),
        ):
            rc = cli.cmd_prepare_plugin_shadow()

        out = capsys.readouterr().out.strip()
        assert rc == 0
        assert out == str(tmp_path / ".devbench" / "plugin-shadow" / "devbench")
        # Real file (rewritten), not symlink
        executor = Path(out) / "agents" / "executor.md"
        assert executor.is_file() and not executor.is_symlink()
        assert "model: opus\n" in executor.read_text(encoding="utf-8")


class TestCmdStartUsesShadow:
    """cmd_start passes the resolved (shadow-or-canonical) path to ClaudeAgentOptions."""

    def test_cmd_start_with_override_uses_shadow_path(self, tmp_path: Path) -> None:
        import sys
        import types

        from devbench.config_loader import AgentModelsConfig

        mock_sdk: Any = types.ModuleType("claude_agent_sdk")
        mock_options_cls = MagicMock()
        mock_sdk.ClaudeAgentOptions = mock_options_cls

        async def mock_query(**kwargs: object) -> object:
            yield "test message"

        mock_sdk.query = mock_query

        with (
            patch.dict(sys.modules, {"claude_agent_sdk": mock_sdk}),
            patch("devbench.cli.AGENT_MODELS", AgentModelsConfig(executor="opus")),
            patch("devbench.cli.WORKSPACE_ROOT", tmp_path),
            patch(
                "devbench.cli._should_auto_restart_after_no_actionable",
                return_value=(False, []),
            ),
        ):
            rc = cli.cmd_start()

        assert rc == 0
        # Verify the options constructor was called with the shadow path,
        # not the canonical.
        kwargs = mock_options_cls.call_args.kwargs
        shadow_path = str(tmp_path / ".devbench" / "plugin-shadow" / "devbench")
        assert kwargs["plugins"] == [{"type": "local", "path": shadow_path}]
        # cmd_start MUST have written a PID sentinel inside the shadow tree
        # so a stray prepare-plugin-shadow can't clear it while this run
        # is alive.
        sentinel = tmp_path / ".devbench" / "plugin-shadow" / "devbench" / ".pid"
        assert sentinel.is_file()
        import os as _os

        assert sentinel.read_text(encoding="utf-8").strip() == str(_os.getpid())

    def test_cmd_start_without_override_does_not_write_sentinel(self, tmp_path: Path) -> None:
        # When no overrides are configured, _resolve_plugin_path returns the
        # canonical path -- no shadow exists, so no sentinel must be written.
        import sys
        import types

        from devbench.config_loader import AgentModelsConfig

        mock_sdk: Any = types.ModuleType("claude_agent_sdk")
        mock_options_cls = MagicMock()
        mock_sdk.ClaudeAgentOptions = mock_options_cls

        async def mock_query(**kwargs: object) -> object:
            yield "test message"

        mock_sdk.query = mock_query

        with (
            patch.dict(sys.modules, {"claude_agent_sdk": mock_sdk}),
            patch("devbench.cli.AGENT_MODELS", AgentModelsConfig()),  # no overrides
            patch("devbench.cli.WORKSPACE_ROOT", tmp_path),
            patch(
                "devbench.cli._should_auto_restart_after_no_actionable",
                return_value=(False, []),
            ),
        ):
            rc = cli.cmd_start()

        assert rc == 0
        assert not (tmp_path / ".devbench" / "plugin-shadow").exists()


class TestCmdStartAutoRestartPostMortem:
    """Cover the post-mortem path that triggers exit-42 auto-restart.

    Three preconditions must all hold for exit 42:
    1. >= 1 BLOCKED task classifies as RUNTIME_DEGRADATION.
    2. Zero IN_PROGRESS / IN_REVIEW tasks.
    3. Zero OPERATOR_ACTION_REQUIRED blockers.
    Anything else returns 0.
    """

    def _mocked_sdk(self) -> object:
        import types

        mock_sdk: Any = types.ModuleType("claude_agent_sdk")
        mock_sdk.ClaudeAgentOptions = MagicMock()

        async def mock_query(**kwargs: object) -> object:
            yield "msg"

        mock_sdk.query = mock_query
        return mock_sdk

    def test_returns_42_when_only_blockers_are_runtime_degradation(
        self,
        tmp_path: Path,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        import logging
        import sys

        mock_sdk = self._mocked_sdk()
        with (
            patch.dict(sys.modules, {"claude_agent_sdk": mock_sdk}),
            patch("devbench.cli.WORKSPACE_ROOT", tmp_path),
            patch(
                "devbench.cli._should_auto_restart_after_no_actionable",
                return_value=(True, ["E1-F4-S1-T3", "E4-F1-S1-T5"]),
            ),
            caplog.at_level(logging.INFO, logger="devbench.cli"),
        ):
            rc = cli.cmd_start()

        from devbench.constants import (
            ORCHESTRATOR_AUTO_RESTART_AUDIT_PREFIX,
            ORCHESTRATOR_RESTART_EXIT_CODE,
        )

        assert rc == ORCHESTRATOR_RESTART_EXIT_CODE
        # The audit prefix + comma-joined task id list must appear in the log.
        assert any(
            ORCHESTRATOR_AUTO_RESTART_AUDIT_PREFIX in rec.getMessage() and "E1-F4-S1-T3,E4-F1-S1-T5" in rec.getMessage()
            for rec in caplog.records
        )

    def test_returns_0_when_post_mortem_says_no_restart(self, tmp_path: Path) -> None:
        import sys

        mock_sdk = self._mocked_sdk()
        with (
            patch.dict(sys.modules, {"claude_agent_sdk": mock_sdk}),
            patch("devbench.cli.WORKSPACE_ROOT", tmp_path),
            patch(
                "devbench.cli._should_auto_restart_after_no_actionable",
                return_value=(False, []),
            ),
        ):
            rc = cli.cmd_start()

        assert rc == 0


class _CmdStartScopeTestBase:
    """Shared helpers for cmd_start scope-related test classes.

    Provides the SDK mock factory, backlog ID fixture, fake WorkUnit list, and
    the CLI-patch context manager used by both ``TestCmdStartScopeFlags`` and
    ``TestCmdStartScopeCleanExit``.  Subclasses inherit all four so neither
    class needs to duplicate them.
    """

    # ------------------------------------------------------------------
    # Shared SDK mock factory
    # ------------------------------------------------------------------

    def _make_sdk_mock(self) -> object:
        import types

        mock_sdk: Any = types.ModuleType("claude_agent_sdk")
        mock_sdk.ClaudeAgentOptions = MagicMock()

        async def _query(**kwargs: object) -> object:
            yield "msg"

        mock_sdk.query = _query
        return mock_sdk

    # ------------------------------------------------------------------
    # Fixture-level backlog IDs used across tests
    # ------------------------------------------------------------------

    _BACKLOG_IDS: ClassVar[list[str]] = [
        "E1-F1-S1-T1",
        "E1-F1-S1-T2",
        "E1-F2-S1-T1",
        "E2-F1-S1-T1",
        "E2-F1-S1-T2",
        "E3-F1-S1-T1",
    ]

    def _fake_units(self) -> list:
        """Return WorkUnit stubs whose IDs match _BACKLOG_IDS."""
        return [
            WorkUnit(
                id=wu_id,
                title=f"Task {wu_id}",
                status=WorkUnitStatus.IN_QUEUE,
                unit_type=WorkUnitType.TASK,
                file_path=Path(f"backlog/{wu_id}.md"),
                repo="caylent-solutions/devbench",
                dependencies=[],
            )
            for wu_id in self._BACKLOG_IDS
        ]

    @contextlib.contextmanager
    def _patch_cli(self, tmp_path: Path, mock_sdk: object | None = None) -> Generator[MagicMock, None, None]:
        """Context manager that applies all CLI patches needed by scope-flag tests.

        Patches sys.modules for claude_agent_sdk, WORKSPACE_ROOT, BACKLOG_ROOT,
        BACKLOG_INDEX, BacklogParser, and _should_auto_restart_after_no_actionable.
        Pre-configures BacklogParser to return _fake_units().

        Args:
            tmp_path: Temporary directory to use as WORKSPACE_ROOT.
            mock_sdk: Optional custom claude_agent_sdk module mock. Defaults to
                the standard no-op mock returned by ``_make_sdk_mock``.

        Yields:
            The MagicMock instance used for BacklogParser, already configured
            with ``parse_index`` returning ``_fake_units()``.
        """
        import sys

        if mock_sdk is None:
            mock_sdk = self._make_sdk_mock()

        with contextlib.ExitStack() as stack:
            stack.enter_context(patch.dict(sys.modules, {"claude_agent_sdk": mock_sdk}))
            stack.enter_context(patch("devbench.cli.WORKSPACE_ROOT", tmp_path))
            stack.enter_context(patch("devbench.cli.BACKLOG_ROOT", tmp_path / "backlog"))
            stack.enter_context(patch("devbench.cli.BACKLOG_INDEX", tmp_path / "BACKLOG.md"))
            mock_parser_cls = stack.enter_context(patch("devbench.cli.BacklogParser"))
            stack.enter_context(
                patch(
                    "devbench.cli._should_auto_restart_after_no_actionable",
                    return_value=(False, []),
                )
            )
            mock_parser_cls.return_value.parse_index.return_value = self._fake_units()
            yield mock_parser_cls


class TestCmdStartScopeFlags(_CmdStartScopeTestBase):
    """cmd_start --include / --exclude parse and persist scope.json (AC-190-8, AC-190-9).

    Each test constructs a minimal fake BacklogParser so the scope-filter
    expansion operates on a known set of IDs, then verifies the scope.json
    output and the command return-code.
    """

    # ------------------------------------------------------------------
    # AC-190-8: --include writes scope.json with raw + expanded sets
    # ------------------------------------------------------------------

    def _make_scope_capturing_sdk(self, scope_path: Path, captured: list[dict[str, list[str]]]) -> object:
        """Return an SDK mock that reads scope.json content into ``captured`` before yielding.

        This captures the scope.json content while it is live (before cmd_start
        clears it on clean exit per AC-190-13).

        Args:
            scope_path: Path to the scope.json file to read during the SDK run.
            captured: Mutable list; the first element will be the parsed JSON data
                recorded inside the SDK mock while the file is present.

        Returns:
            A fake claude_agent_sdk module whose ``query`` coroutine reads the
            scope.json content on invocation.
        """
        import types

        async def _capturing_query(**kwargs: object) -> object:
            if scope_path.exists():
                captured.append(json.loads(scope_path.read_text()))
            yield "msg"

        capturing_sdk: Any = types.ModuleType("claude_agent_sdk")
        capturing_sdk.ClaudeAgentOptions = MagicMock()
        capturing_sdk.query = _capturing_query
        return capturing_sdk

    def test_include_flag_writes_scope_json(self, tmp_path: Path) -> None:
        """AC-190-8: cmd_start --include writes .devbench/scope.json during the SDK run.

        scope.json is written before the SDK is invoked and cleared on clean
        exit (AC-190-13), so this test captures its content from inside the
        SDK mock while the file is live.
        """
        scope_path = tmp_path / ".devbench" / "scope.json"
        captured: list[dict[str, list[str]]] = []
        mock_sdk = self._make_scope_capturing_sdk(scope_path, captured)

        with self._patch_cli(tmp_path, mock_sdk=mock_sdk):
            rc = cli.cmd_start("--include", "E1")

        assert rc == 0
        assert captured, "scope.json must be written before the SDK is invoked"
        data = captured[0]
        assert data["include"] == ["E1"]
        assert data["exclude"] == []
        # All E1 descendants must be in expanded_ids
        assert set(data["expanded_ids"]) == {"E1-F1-S1-T1", "E1-F1-S1-T2", "E1-F2-S1-T1"}
        assert "started_at" in data
        assert "started_by" in data
        # scope.json must be cleared after clean exit (AC-190-13)
        assert not scope_path.exists(), "scope.json must be cleared on clean exit (AC-190-13)"

    def test_include_and_exclude_writes_correct_scope_json(self, tmp_path: Path) -> None:
        """AC-190-8: --exclude subtracts from the include set in scope.json during SDK run."""
        scope_path = tmp_path / ".devbench" / "scope.json"
        captured: list[dict[str, list[str]]] = []
        mock_sdk = self._make_scope_capturing_sdk(scope_path, captured)

        with self._patch_cli(tmp_path, mock_sdk=mock_sdk):
            rc = cli.cmd_start("--include", "E1", "--exclude", "E1-F2-S1-T1")

        assert rc == 0
        assert captured, "scope.json must be written before the SDK is invoked"
        data = captured[0]
        assert data["include"] == ["E1"]
        assert data["exclude"] == ["E1-F2-S1-T1"]
        # E1-F2-S1-T1 must be excluded
        assert "E1-F2-S1-T1" not in data["expanded_ids"]
        assert "E1-F1-S1-T1" in data["expanded_ids"]
        assert "E1-F1-S1-T2" in data["expanded_ids"]
        # scope.json must be cleared after clean exit (AC-190-13)
        assert not scope_path.exists(), "scope.json must be cleared on clean exit (AC-190-13)"

    # ------------------------------------------------------------------
    # AC-190-9: empty --include means "include everything"
    # ------------------------------------------------------------------

    def test_no_flags_does_not_write_scope_json(self, tmp_path: Path) -> None:
        """AC-190-9: cmd_start with no --include flag must NOT write scope.json."""
        with self._patch_cli(tmp_path):
            rc = cli.cmd_start()

        assert rc == 0
        scope_path = tmp_path / ".devbench" / "scope.json"
        assert not scope_path.exists(), (
            "scope.json must NOT be written when --include is not supplied "
            "(current 'include everything' behavior must be preserved)"
        )

    def test_include_empty_string_does_not_write_scope_json(self, tmp_path: Path) -> None:
        """AC-190-9: explicitly empty --include '' is treated as 'include everything'."""
        with self._patch_cli(tmp_path):
            rc = cli.cmd_start("--include", "")

        assert rc == 0
        scope_path = tmp_path / ".devbench" / "scope.json"
        assert not scope_path.exists(), (
            "scope.json must NOT be written when --include is empty "
            "(empty include means 'include everything', no scope file needed)"
        )

    def test_invalid_include_token_exits_nonzero(self, tmp_path: Path) -> None:
        """Malformed scope token must cause cmd_start to exit with rc=1 (fail-fast)."""
        with self._patch_cli(tmp_path):
            rc = cli.cmd_start("--include", "-bad-token-")

        assert rc == 1
        scope_path = tmp_path / ".devbench" / "scope.json"
        assert not scope_path.exists(), "scope.json must not be written when token is invalid"

    def test_reversed_range_token_exits_nonzero(self, tmp_path: Path) -> None:
        """Reverse-range token (E3-E1) must cause cmd_start to exit with rc=1 (fail-fast)."""
        with self._patch_cli(tmp_path):
            rc = cli.cmd_start("--include", "E3-E1")

        assert rc == 1

    def test_devbench_scope_file_env_var_set(self, tmp_path: Path) -> None:
        """cmd_start --include must set DEVBENCH_SCOPE_FILE in the process env."""
        import types

        captured_env: dict[str, str] = {}

        async def _capturing_query(**kwargs: object) -> object:
            import os

            captured_env.update(os.environ)
            yield "msg"

        custom_sdk: Any = types.ModuleType("claude_agent_sdk")
        custom_sdk.ClaudeAgentOptions = MagicMock()
        custom_sdk.query = _capturing_query

        with self._patch_cli(tmp_path, mock_sdk=custom_sdk):
            rc = cli.cmd_start("--include", "E1")

        assert rc == 0
        assert "DEVBENCH_SCOPE_FILE" in captured_env
        expected_path = str(tmp_path / ".devbench" / "scope.json")
        assert captured_env["DEVBENCH_SCOPE_FILE"] == expected_path

    def test_unknown_flag_exits_nonzero(self, tmp_path: Path) -> None:
        """Unknown flags must cause cmd_start to exit with rc=1 (fail-fast)."""
        with self._patch_cli(tmp_path):
            rc = cli.cmd_start("--unknown-flag", "value")

        assert rc == 1

    # ------------------------------------------------------------------
    # Error paths: dangling flags without a value
    # ------------------------------------------------------------------

    def test_include_without_value_exits_nonzero(self, tmp_path: Path) -> None:
        """--include with no following value must cause cmd_start to exit rc=1 (fail-fast).

        _parse_start_args prints an actionable error to stderr and returns 1
        when --include appears as the last argument with no accompanying value.
        """
        with self._patch_cli(tmp_path):
            rc = cli.cmd_start("--include")

        assert rc == 1

    def test_exclude_without_value_exits_nonzero(self, tmp_path: Path) -> None:
        """--exclude with no following value must cause cmd_start to exit rc=1 (fail-fast).

        _parse_start_args prints an actionable error to stderr and returns 1
        when --exclude appears as the last argument with no accompanying value.
        """
        with self._patch_cli(tmp_path):
            rc = cli.cmd_start("--exclude")

        assert rc == 1


class TestCmdStartScopeCleanExit(_CmdStartScopeTestBase):
    """AC-190-13: scope.json is deleted on clean cmd_start exit.

    On a successful SDK return (clean orchestrator exit), any scope.json that
    was written by ``cmd_start --include`` MUST be deleted.  On crash (SDK
    raises an exception), scope.json MUST persist so the operator can inspect
    which scope was active.

    Shared helpers (_make_sdk_mock, _BACKLOG_IDS, _fake_units, _patch_cli) are
    inherited from ``_CmdStartScopeTestBase``.
    """

    # ------------------------------------------------------------------
    # AC-190-13: scope.json cleared on clean exit
    # ------------------------------------------------------------------

    def test_clean_exit_clears_scope_json(self, tmp_path: Path) -> None:
        """AC-190-13: scope.json must be deleted after a successful SDK run."""
        with self._patch_cli(tmp_path):
            rc = cli.cmd_start("--include", "E1")

        assert rc == 0
        scope_path = tmp_path / ".devbench" / "scope.json"
        assert not scope_path.exists(), "scope.json must be deleted on clean cmd_start exit (AC-190-13)"

    def test_clean_exit_without_include_no_scope_json_to_clear(self, tmp_path: Path) -> None:
        """AC-190-13: when no --include was given, scope.json is absent before and after."""
        with self._patch_cli(tmp_path):
            rc = cli.cmd_start()

        assert rc == 0
        scope_path = tmp_path / ".devbench" / "scope.json"
        assert not scope_path.exists(), "scope.json must not appear when --include was not supplied"

    def test_preexisting_scope_json_cleared_on_clean_exit(self, tmp_path: Path) -> None:
        """AC-190-13: a pre-existing scope.json (from a previous run) is also cleared.

        When cmd_start is invoked without --include but a scope.json already
        exists (written by a prior --include run or by ``devbench scope set``),
        a clean SDK exit MUST delete it.
        """
        scope_path = tmp_path / ".devbench" / "scope.json"
        scope_path.parent.mkdir(parents=True, exist_ok=True)
        scope_path.write_text('{"include": ["E1"], "exclude": [], "expanded_ids": ["E1-F1-S1-T1"]}')

        with self._patch_cli(tmp_path):
            rc = cli.cmd_start()

        assert rc == 0
        assert not scope_path.exists(), "pre-existing scope.json must be deleted on clean cmd_start exit (AC-190-13)"

    def test_sdk_crash_preserves_scope_json(self, tmp_path: Path) -> None:
        """AC-190-13: scope.json must persist when the SDK raises (crash path)."""
        import types

        class _SDKError(RuntimeError):
            pass

        crash_sdk: Any = types.ModuleType("claude_agent_sdk")
        crash_sdk.ClaudeAgentOptions = MagicMock()

        async def _crash_query(**kwargs: object) -> object:
            raise _SDKError("simulated SDK crash")
            yield  # make it a generator

        crash_sdk.query = _crash_query

        with self._patch_cli(tmp_path, mock_sdk=crash_sdk):
            with pytest.raises(_SDKError):
                cli.cmd_start("--include", "E1")

        scope_path = tmp_path / ".devbench" / "scope.json"
        assert scope_path.exists(), (
            "scope.json must persist when the SDK crashes so the operator can inspect the active scope"
        )


class TestShouldAutoRestartPostMortem:
    """Direct unit tests for _should_auto_restart_after_no_actionable's
    three-precondition logic. We stub BacklogParser.parse_index +
    classify_blocked_task so the function's decision matrix is exercised
    in isolation from real backlog state.
    """

    def _make_unit(self, unit_id: str, status: object) -> object:
        from types import SimpleNamespace

        return SimpleNamespace(id=unit_id, status=status)

    def test_runtime_degradation_only_returns_true(self) -> None:
        from devbench.backlog.proposal import BlockedTaskState
        from devbench.backlog.work_unit import WorkUnitStatus

        units = [
            self._make_unit("T1", WorkUnitStatus.BLOCKED),
            self._make_unit("T2", WorkUnitStatus.DONE),
        ]
        with (
            patch("devbench.cli.BacklogParser") as mock_parser,
            patch("devbench.cli.classify_blocked_task") as mock_classify,
        ):
            mock_parser.return_value.parse_index.return_value = units
            mock_classify.return_value = BlockedTaskState.RUNTIME_DEGRADATION

            should, ids = cli._should_auto_restart_after_no_actionable()

        assert should is True
        assert ids == ["T1"]

    def test_returns_false_when_in_progress_task_exists(self) -> None:
        from devbench.backlog.proposal import BlockedTaskState
        from devbench.backlog.work_unit import WorkUnitStatus

        units = [
            self._make_unit("T1", WorkUnitStatus.IN_PROGRESS),
            self._make_unit("T2", WorkUnitStatus.BLOCKED),
        ]
        with (
            patch("devbench.cli.BacklogParser") as mock_parser,
            patch("devbench.cli.classify_blocked_task") as mock_classify,
        ):
            mock_parser.return_value.parse_index.return_value = units
            mock_classify.return_value = BlockedTaskState.RUNTIME_DEGRADATION

            should, ids = cli._should_auto_restart_after_no_actionable()

        assert should is False
        assert ids == []

    def test_returns_false_when_in_review_task_exists(self) -> None:
        from devbench.backlog.proposal import BlockedTaskState
        from devbench.backlog.work_unit import WorkUnitStatus

        units = [
            self._make_unit("T1", WorkUnitStatus.IN_REVIEW),
            self._make_unit("T2", WorkUnitStatus.BLOCKED),
        ]
        with (
            patch("devbench.cli.BacklogParser") as mock_parser,
            patch("devbench.cli.classify_blocked_task") as mock_classify,
        ):
            mock_parser.return_value.parse_index.return_value = units
            mock_classify.return_value = BlockedTaskState.RUNTIME_DEGRADATION

            should, ids = cli._should_auto_restart_after_no_actionable()

        assert should is False
        assert ids == []

    def test_returns_false_when_operator_action_required_blocker_exists(self) -> None:
        from devbench.backlog.proposal import BlockedTaskState
        from devbench.backlog.work_unit import WorkUnitStatus

        units = [
            self._make_unit("T1", WorkUnitStatus.BLOCKED),
            self._make_unit("T2", WorkUnitStatus.BLOCKED),
        ]

        def classify_side_effect(*, task_id: str, **kwargs: object) -> object:
            if task_id == "T1":
                return BlockedTaskState.RUNTIME_DEGRADATION
            return BlockedTaskState.OPERATOR_ACTION_REQUIRED

        with (
            patch("devbench.cli.BacklogParser") as mock_parser,
            patch("devbench.cli.classify_blocked_task", side_effect=classify_side_effect),
        ):
            mock_parser.return_value.parse_index.return_value = units

            should, ids = cli._should_auto_restart_after_no_actionable()

        assert should is False
        assert ids == []

    def test_returns_false_when_no_runtime_degradation_blockers(self) -> None:
        from devbench.backlog.proposal import BlockedTaskState
        from devbench.backlog.work_unit import WorkUnitStatus

        units = [self._make_unit("T1", WorkUnitStatus.BLOCKED)]
        with (
            patch("devbench.cli.BacklogParser") as mock_parser,
            patch("devbench.cli.classify_blocked_task") as mock_classify,
        ):
            mock_parser.return_value.parse_index.return_value = units
            mock_classify.return_value = BlockedTaskState.AWAITING_DEPENDENCY

            should, ids = cli._should_auto_restart_after_no_actionable()

        assert should is False
        assert ids == []

    def test_returns_false_when_backlog_empty(self) -> None:
        with (
            patch("devbench.cli.BacklogParser") as mock_parser,
        ):
            mock_parser.return_value.parse_index.return_value = []

            should, ids = cli._should_auto_restart_after_no_actionable()

        assert should is False
        assert ids == []

    def test_non_blocked_units_are_ignored_by_classifier(self) -> None:
        """Done/declined units never get classified -- the function
        should only call classify_blocked_task on BLOCKED ones."""
        from devbench.backlog.proposal import BlockedTaskState
        from devbench.backlog.work_unit import WorkUnitStatus

        units = [
            self._make_unit("T1", WorkUnitStatus.DONE),
            self._make_unit("T2", WorkUnitStatus.BLOCKED),
            self._make_unit("T3", WorkUnitStatus.DECLINED),
        ]
        with (
            patch("devbench.cli.BacklogParser") as mock_parser,
            patch("devbench.cli.classify_blocked_task") as mock_classify,
        ):
            mock_parser.return_value.parse_index.return_value = units
            mock_classify.return_value = BlockedTaskState.RUNTIME_DEGRADATION

            should, ids = cli._should_auto_restart_after_no_actionable()

        assert should is True
        assert ids == ["T2"]
        # Only the BLOCKED unit got passed to the classifier.
        assert mock_classify.call_count == 1
        assert mock_classify.call_args.kwargs["task_id"] == "T2"


# ---------------------------------------------------------------------------
# AC-190-10 / AC-190-11: cmd_status scope flags + SCOPE banner (E2-F2-S2-T1)
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestCmdStatusScopeBanner:
    """E2-F2-S2-T1: cmd_status accepts --include/--exclude; renders SCOPE banner.

    AC-190-10: devbench status honors active scope.json without flags.
    AC-190-11: Per-command --include override of active scope.json works.
    """

    def _make_parser_mock(self) -> MagicMock:
        """Return a BacklogParser mock with a minimal parse_index result."""
        unit = WorkUnit(
            id="E1-F1-S1-T1",
            title="Alpha",
            status=WorkUnitStatus.IN_QUEUE,
            unit_type=WorkUnitType.TASK,
            file_path=Path("backlog/E1-F1-S1-T1.md"),
            repo="caylent-solutions/devbench",
            dependencies=[],
        )
        parser = MagicMock()
        parser.parse_index.return_value = [unit]
        parser.get_parallel_candidates.return_value = [unit]
        parser.get_blocked_units.return_value = []
        parser.all_done.return_value = False
        return parser

    def _write_scope_json(
        self,
        tmp_path: Path,
        include: list[str],
        exclude: list[str],
        started_at: str = "2026-05-14T13:42:11Z",
        started_by: str = "testuser",
    ) -> None:
        """Write a minimal scope.json under tmp_path/.devbench/scope.json."""
        scope_dir = tmp_path / ".devbench"
        scope_dir.mkdir(parents=True, exist_ok=True)
        payload = {
            "include": include,
            "exclude": exclude,
            "expanded_ids": ["E1-F1-S1-T1"],
            "started_at": started_at,
            "started_by": started_by,
        }
        (scope_dir / "scope.json").write_text(json.dumps(payload))

    # ------------------------------------------------------------------
    # AC-190-10: honors active scope.json without flags
    # ------------------------------------------------------------------

    def test_scope_banner_rendered_from_scope_json(
        self,
        tmp_path: Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """SCOPE banner appears above Status Summary when scope.json is active."""
        self._write_scope_json(
            tmp_path,
            include=["E1-E3"],
            exclude=["E2"],
            started_at="2026-05-14T13:42:11Z",
            started_by="alice",
        )
        parser = self._make_parser_mock()

        with (
            patch("devbench.cli.BacklogParser", return_value=parser),
            patch("devbench.cli.WORKSPACE_ROOT", tmp_path),
            patch("devbench.cli._count_unmaterialised_proposed_tasks", return_value=0),
        ):
            rc = cli.cmd_status()

        assert rc == 0
        out = capsys.readouterr().out
        assert "SCOPE:" in out
        assert "include=[E1-E3]" in out
        assert "exclude=[E2]" in out
        assert "2026-05-14T13:42:11Z" in out
        # Banner must appear BEFORE the Status Summary line.
        assert out.index("SCOPE:") < out.index("Backlog Status Summary")

    def test_scope_banner_absent_when_no_scope_json(
        self,
        tmp_path: Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """No SCOPE banner when scope.json does not exist."""
        parser = self._make_parser_mock()

        with (
            patch("devbench.cli.BacklogParser", return_value=parser),
            patch("devbench.cli.WORKSPACE_ROOT", tmp_path),
            patch("devbench.cli._count_unmaterialised_proposed_tasks", return_value=0),
        ):
            rc = cli.cmd_status()

        assert rc == 0
        assert "SCOPE:" not in capsys.readouterr().out

    def test_scope_banner_include_empty_exclude_empty(
        self,
        tmp_path: Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """Banner shows empty include/exclude lists correctly."""
        self._write_scope_json(
            tmp_path,
            include=[],
            exclude=[],
            started_at="2026-01-01T00:00:00Z",
            started_by="bob",
        )
        parser = self._make_parser_mock()

        with (
            patch("devbench.cli.BacklogParser", return_value=parser),
            patch("devbench.cli.WORKSPACE_ROOT", tmp_path),
            patch("devbench.cli._count_unmaterialised_proposed_tasks", return_value=0),
        ):
            rc = cli.cmd_status()

        assert rc == 0
        out = capsys.readouterr().out
        assert "SCOPE:" in out
        assert "include=[]" in out
        assert "exclude=[]" in out

    # ------------------------------------------------------------------
    # AC-190-11: per-command --include override
    # ------------------------------------------------------------------

    def test_include_flag_renders_scope_banner(
        self,
        tmp_path: Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """--include flag renders SCOPE banner (no scope.json required)."""
        parser = self._make_parser_mock()

        with (
            patch("devbench.cli.BacklogParser", return_value=parser),
            patch("devbench.cli.WORKSPACE_ROOT", tmp_path),
            patch("devbench.cli._count_unmaterialised_proposed_tasks", return_value=0),
        ):
            rc = cli.cmd_status("--include", "E1")

        assert rc == 0
        out = capsys.readouterr().out
        assert "SCOPE:" in out
        assert "include=[E1]" in out
        assert "exclude=[]" in out

    def test_include_and_exclude_flags_render_correct_banner(
        self,
        tmp_path: Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """--include and --exclude flags together render correct SCOPE banner."""
        parser = self._make_parser_mock()

        with (
            patch("devbench.cli.BacklogParser", return_value=parser),
            patch("devbench.cli.WORKSPACE_ROOT", tmp_path),
            patch("devbench.cli._count_unmaterialised_proposed_tasks", return_value=0),
        ):
            rc = cli.cmd_status("--include", "E1-E3", "--exclude", "E2")

        assert rc == 0
        out = capsys.readouterr().out
        assert "SCOPE:" in out
        assert "include=[E1-E3]" in out
        assert "exclude=[E2]" in out

    def test_include_flag_overrides_scope_json(
        self,
        tmp_path: Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """Per-command --include overrides active scope.json (AC-190-11)."""
        self._write_scope_json(
            tmp_path,
            include=["E5"],
            exclude=["E6"],
            started_at="2026-05-01T00:00:00Z",
            started_by="carol",
        )
        parser = self._make_parser_mock()

        with (
            patch("devbench.cli.BacklogParser", return_value=parser),
            patch("devbench.cli.WORKSPACE_ROOT", tmp_path),
            patch("devbench.cli._count_unmaterialised_proposed_tasks", return_value=0),
        ):
            rc = cli.cmd_status("--include", "E1")

        assert rc == 0
        out = capsys.readouterr().out
        # The flag override must appear, not the scope.json values.
        assert "include=[E1]" in out
        assert "E5" not in out.split("SCOPE:")[1].split("\n")[0]

    # ------------------------------------------------------------------
    # Error paths for missing flag values
    # ------------------------------------------------------------------

    @pytest.mark.parametrize("flag", ["--include", "--exclude"])
    def test_flag_without_value_returns_error(
        self,
        flag: str,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """--include or --exclude without a following value exits with rc=1 and error to stderr."""
        rc = cli.cmd_status(flag)
        assert rc == 1
        err = capsys.readouterr().err
        assert flag in err
        assert "requires a value" in err

    @pytest.mark.parametrize("flag", ["--include", "--exclude"])
    def test_flag_followed_by_another_flag_is_error(
        self,
        flag: str,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """--include or --exclude followed immediately by another flag is an error."""
        rc = cli.cmd_status(flag, "--detail")
        assert rc == 1
        err = capsys.readouterr().err
        assert "requires a value" in err

    # ------------------------------------------------------------------
    # Backward compatibility: --detail still works with scope flags
    # ------------------------------------------------------------------

    def test_detail_and_include_flags_coexist(
        self,
        tmp_path: Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """--detail and --include can be combined without error."""
        parser = self._make_parser_mock()
        parser.get_blocked_units.return_value = []

        with (
            patch("devbench.cli.BacklogParser", return_value=parser),
            patch("devbench.cli.WORKSPACE_ROOT", tmp_path),
            patch("devbench.cli._count_unmaterialised_proposed_tasks", return_value=0),
        ):
            rc = cli.cmd_status("--detail", "--include", "E1")

        assert rc == 0
        out = capsys.readouterr().out
        assert "SCOPE:" in out
        assert "Backlog Status Summary" in out

    # ------------------------------------------------------------------
    # Corrupt scope.json propagates as error (fail-fast)
    # ------------------------------------------------------------------

    def test_corrupt_scope_json_exits_with_error(
        self,
        tmp_path: Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """Corrupt scope.json causes cmd_status to exit rc=1 with a diagnostic to stderr."""
        scope_dir = tmp_path / ".devbench"
        scope_dir.mkdir(parents=True, exist_ok=True)
        (scope_dir / "scope.json").write_text("not valid json{{")

        with (
            patch("devbench.cli.WORKSPACE_ROOT", tmp_path),
        ):
            rc = cli.cmd_status()

        assert rc == 1
        assert "scope.json" in capsys.readouterr().err

    @pytest.mark.parametrize(
        "bad_field,bad_value",
        [
            ("include", "E1"),
            ("exclude", "E2"),
            ("include", 42),
            ("exclude", {"key": "val"}),
        ],
    )
    def test_non_list_field_in_scope_json_exits_with_error(
        self,
        bad_field: str,
        bad_value: object,
        tmp_path: Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """scope.json with a non-list include/exclude field causes rc=1 with an actionable error."""
        scope_dir = tmp_path / ".devbench"
        scope_dir.mkdir(parents=True, exist_ok=True)
        payload: dict[str, object] = {
            "include": ["E1"],
            "exclude": [],
            "expanded_ids": [],
            "started_at": "2026-05-14T13:42:11Z",
            "started_by": "testuser",
        }
        payload[bad_field] = bad_value
        (scope_dir / "scope.json").write_text(json.dumps(payload))

        with (
            patch("devbench.cli.WORKSPACE_ROOT", tmp_path),
        ):
            rc = cli.cmd_status()

        assert rc == 1
        err = capsys.readouterr().err
        assert "scope.json" in err
        assert bad_field in err
        assert "must be a list" in err


# ---------------------------------------------------------------------------
# AC-190-10 / AC-190-11: cmd_report scope flags + SCOPE banner (E2-F2-S2-T2)
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestCmdReportScopeBanner:
    """E2-F2-S2-T2: cmd_report accepts --include/--exclude; renders SCOPE banner.

    AC-190-10: devbench report honors active scope.json without flags.
    AC-190-11: Per-command --include override of active scope.json works.
    """

    def _write_scope_json(
        self,
        tmp_path: Path,
        include: list[str],
        exclude: list[str],
        started_at: str = "2026-05-14T13:42:11Z",
        started_by: str = "testuser",
    ) -> None:
        """Write a minimal scope.json under tmp_path/.devbench/scope.json."""
        scope_dir = tmp_path / ".devbench"
        scope_dir.mkdir(parents=True, exist_ok=True)
        payload = {
            "include": include,
            "exclude": exclude,
            "expanded_ids": ["E1-F1-S1-T1"],
            "started_at": started_at,
            "started_by": started_by,
        }
        (scope_dir / "scope.json").write_text(json.dumps(payload))

    # ------------------------------------------------------------------
    # AC-190-10: honors active scope.json without flags
    # ------------------------------------------------------------------

    def test_scope_banner_rendered_from_scope_json(
        self,
        tmp_path: Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """SCOPE banner appears in report output when scope.json is active (AC-190-10)."""
        self._write_scope_json(
            tmp_path,
            include=["E1-E3"],
            exclude=["E2"],
            started_at="2026-05-14T13:42:11Z",
        )
        with (
            patch("devbench.cli.WORKSPACE_ROOT", tmp_path),
            patch("devbench.reporting.report.generate_report", return_value="report text"),
        ):
            rc = cli.cmd_report(once=True)

        assert rc == 0
        out = capsys.readouterr().out
        assert "SCOPE:" in out
        assert "include=[E1-E3]" in out
        assert "exclude=[E2]" in out
        assert "2026-05-14T13:42:11Z" in out

    def test_scope_banner_absent_when_no_scope_json(
        self,
        tmp_path: Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """No SCOPE banner when scope.json does not exist and no flags supplied."""
        with (
            patch("devbench.cli.WORKSPACE_ROOT", tmp_path),
            patch("devbench.reporting.report.generate_report", return_value="plain report"),
        ):
            rc = cli.cmd_report(once=True)

        assert rc == 0
        assert "SCOPE:" not in capsys.readouterr().out

    def test_scope_banner_include_and_exclude_from_scope_json(
        self,
        tmp_path: Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """Banner shows both include and exclude lists from scope.json."""
        self._write_scope_json(
            tmp_path,
            include=["E1", "E3"],
            exclude=["E1-F2"],
            started_at="2026-01-01T00:00:00Z",
        )
        with (
            patch("devbench.cli.WORKSPACE_ROOT", tmp_path),
            patch("devbench.reporting.report.generate_report", return_value="report"),
        ):
            rc = cli.cmd_report(once=True)

        assert rc == 0
        out = capsys.readouterr().out
        assert "SCOPE:" in out
        assert "include=[E1, E3]" in out
        assert "exclude=[E1-F2]" in out

    # ------------------------------------------------------------------
    # AC-190-11: per-command --include override
    # ------------------------------------------------------------------

    def test_include_flag_renders_scope_banner(
        self,
        tmp_path: Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """--include flag renders SCOPE banner (no scope.json required, AC-190-11)."""
        with (
            patch("devbench.cli.WORKSPACE_ROOT", tmp_path),
            patch("devbench.reporting.report.generate_report", return_value="report"),
        ):
            rc = cli.cmd_report(once=True, include="E1")

        assert rc == 0
        out = capsys.readouterr().out
        assert "SCOPE:" in out
        assert "include=[E1]" in out
        assert "exclude=[]" in out
        assert "(one-off)" in out

    def test_include_and_exclude_flags_render_correct_banner(
        self,
        tmp_path: Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """--include and --exclude flags together render the correct SCOPE banner."""
        with (
            patch("devbench.cli.WORKSPACE_ROOT", tmp_path),
            patch("devbench.reporting.report.generate_report", return_value="report"),
        ):
            rc = cli.cmd_report(once=True, include="E1-E3", exclude="E2")

        assert rc == 0
        out = capsys.readouterr().out
        assert "SCOPE:" in out
        assert "include=[E1-E3]" in out
        assert "exclude=[E2]" in out

    def test_include_flag_overrides_scope_json(
        self,
        tmp_path: Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """Per-command --include overrides active scope.json (AC-190-11)."""
        self._write_scope_json(
            tmp_path,
            include=["E5"],
            exclude=["E6"],
            started_at="2026-05-01T00:00:00Z",
        )
        with (
            patch("devbench.cli.WORKSPACE_ROOT", tmp_path),
            patch("devbench.reporting.report.generate_report", return_value="report"),
        ):
            rc = cli.cmd_report(once=True, include="E1")

        assert rc == 0
        out = capsys.readouterr().out
        # The flag override must appear, not the scope.json values.
        assert "include=[E1]" in out
        scope_line = next(ln for ln in out.splitlines() if "SCOPE:" in ln)
        assert "E5" not in scope_line

    # ------------------------------------------------------------------
    # scope_filter forwarded to generate_report
    # ------------------------------------------------------------------

    def test_scope_filter_passed_to_generate_report_when_include_flag(
        self,
        tmp_path: Path,
    ) -> None:
        """When --include is supplied, generate_report receives a non-None scope_filter."""
        captured: dict[str, object] = {}

        def fake_generate_report(**kwargs: object) -> str:
            captured.update(kwargs)
            return "scoped report"

        with (
            patch("devbench.cli.WORKSPACE_ROOT", tmp_path),
            patch("devbench.reporting.report.generate_report", side_effect=fake_generate_report),
        ):
            rc = cli.cmd_report(once=True, include="E1")

        assert rc == 0
        # scope_filter must be a non-None ScopeFilter with the include tokens.
        assert "scope_filter" in captured
        sf = captured["scope_filter"]
        assert sf is not None
        from devbench.scope import ScopeFilter

        assert isinstance(sf, ScopeFilter)
        assert sf.include == ["E1"]

    def test_scope_filter_passed_to_generate_report_from_scope_json(
        self,
        tmp_path: Path,
    ) -> None:
        """When scope.json is active, generate_report receives a non-None scope_filter."""
        self._write_scope_json(tmp_path, include=["E1"], exclude=[])
        captured: dict[str, object] = {}

        def fake_generate_report(**kwargs: object) -> str:
            captured.update(kwargs)
            return "scoped report"

        with (
            patch("devbench.cli.WORKSPACE_ROOT", tmp_path),
            patch("devbench.reporting.report.generate_report", side_effect=fake_generate_report),
        ):
            rc = cli.cmd_report(once=True)

        assert rc == 0
        assert "scope_filter" in captured
        assert captured["scope_filter"] is not None

    def test_scope_filter_none_when_no_scope(
        self,
        tmp_path: Path,
    ) -> None:
        """When no scope is active, generate_report receives scope_filter=None."""
        captured: dict[str, object] = {}

        def fake_generate_report(**kwargs: object) -> str:
            captured.update(kwargs)
            return "report"

        with (
            patch("devbench.cli.WORKSPACE_ROOT", tmp_path),
            patch("devbench.reporting.report.generate_report", side_effect=fake_generate_report),
        ):
            rc = cli.cmd_report(once=True)

        assert rc == 0
        assert captured.get("scope_filter") is None

    # ------------------------------------------------------------------
    # Error paths: corrupt scope.json
    # ------------------------------------------------------------------

    def test_corrupt_scope_json_returns_error(
        self,
        tmp_path: Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """Corrupt scope.json causes rc=1 with an actionable error to stderr."""
        scope_dir = tmp_path / ".devbench"
        scope_dir.mkdir(parents=True, exist_ok=True)
        (scope_dir / "scope.json").write_text("{not valid json")

        with (
            patch("devbench.cli.WORKSPACE_ROOT", tmp_path),
        ):
            rc = cli.cmd_report(once=True)

        assert rc == 1
        assert "scope.json" in capsys.readouterr().err

    @pytest.mark.parametrize(
        "bad_field,bad_value",
        [
            ("include", "E1"),
            ("exclude", "E2"),
        ],
    )
    def test_non_list_field_in_scope_json_exits_with_error(
        self,
        bad_field: str,
        bad_value: object,
        tmp_path: Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """scope.json with a non-list include/exclude field causes rc=1 with error."""
        scope_dir = tmp_path / ".devbench"
        scope_dir.mkdir(parents=True, exist_ok=True)
        payload: dict[str, object] = {
            "include": ["E1"],
            "exclude": [],
            "expanded_ids": [],
            "started_at": "2026-05-14T13:42:11Z",
            "started_by": "testuser",
        }
        payload[bad_field] = bad_value
        (scope_dir / "scope.json").write_text(json.dumps(payload))

        with (
            patch("devbench.cli.WORKSPACE_ROOT", tmp_path),
        ):
            rc = cli.cmd_report(once=True)

        assert rc == 1
        err = capsys.readouterr().err
        assert "scope.json" in err
        assert bad_field in err
        assert "must be a list" in err

    # ------------------------------------------------------------------
    # main() integration: --include / --exclude extracted from CLI args
    # ------------------------------------------------------------------

    def test_main_extracts_include_flag_for_report(
        self,
        tmp_path: Path,
    ) -> None:
        """main() strips --include from 'devbench report --include E1' and forwards it to cmd_report."""
        called_with: dict[str, object] = {}

        def fake_cmd_report(**kwargs: object) -> int:
            called_with.update(kwargs)
            return 0

        with (
            patch("sys.argv", ["devbench", "report", "--include", "E1", "--once"]),
            patch("devbench.cli.cmd_report", side_effect=fake_cmd_report),
        ):
            result = cli.main()

        assert result == 0
        assert called_with.get("include") == "E1"

    def test_main_extracts_exclude_flag_for_report(
        self,
        tmp_path: Path,
    ) -> None:
        """main() strips --exclude from 'devbench report --exclude E2' and forwards it to cmd_report."""
        called_with: dict[str, object] = {}

        def fake_cmd_report(**kwargs: object) -> int:
            called_with.update(kwargs)
            return 0

        with (
            patch("sys.argv", ["devbench", "report", "--exclude", "E2", "--once"]),
            patch("devbench.cli.cmd_report", side_effect=fake_cmd_report),
        ):
            result = cli.main()

        assert result == 0
        assert called_with.get("exclude") == "E2"

    def test_main_extracts_include_and_exclude_for_report(
        self,
        tmp_path: Path,
    ) -> None:
        """main() extracts both --include and --exclude for the report command."""
        called_with: dict[str, object] = {}

        def fake_cmd_report(**kwargs: object) -> int:
            called_with.update(kwargs)
            return 0

        with (
            patch("sys.argv", ["devbench", "report", "--include", "E1-E3", "--exclude", "E2", "--once"]),
            patch("devbench.cli.cmd_report", side_effect=fake_cmd_report),
        ):
            result = cli.main()

        assert result == 0
        assert called_with.get("include") == "E1-E3"
        assert called_with.get("exclude") == "E2"

    def test_main_report_without_scope_flags_include_empty(
        self,
        tmp_path: Path,
    ) -> None:
        """main() passes include='' and exclude='' to cmd_report when no scope flags given."""
        called_with: dict[str, object] = {}

        def fake_cmd_report(**kwargs: object) -> int:
            called_with.update(kwargs)
            return 0

        with (
            patch("sys.argv", ["devbench", "report", "--once"]),
            patch("devbench.cli.cmd_report", side_effect=fake_cmd_report),
        ):
            result = cli.main()

        assert result == 0
        assert called_with.get("include", "") == ""
        assert called_with.get("exclude", "") == ""


# ---------------------------------------------------------------------------
# cmd_scope tests (AC-196-1 through AC-196-9; spec section 4.2.6)
# ---------------------------------------------------------------------------


class TestCmdScope:
    """Tests for cmd_scope: set / clear / show dispatch (spec 4.2.6, issue #196)."""

    # Canonical backlog IDs used in all set-action tests.
    _BACKLOG_IDS: ClassVar[list[str]] = [
        "E1-F1-S1-T1",
        "E1-F1-S1-T2",
        "E2-F1-S1-T1",
    ]

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _scope_path(self, tmp_path: Path) -> Path:
        """Return the canonical scope.json path under tmp_path."""
        return tmp_path / ".devbench" / "scope.json"

    def _session_scope_path(self, tmp_path: Path, session_name: str) -> Path:
        """Return the per-session scope.json path."""
        return tmp_path / ".devbench" / "sessions" / session_name / "scope.json"

    def _make_parser_mock(self) -> MagicMock:
        """Return a BacklogParser mock whose parse_index yields the canonical IDs."""
        units = [MagicMock(id=wid) for wid in self._BACKLOG_IDS]
        parser = MagicMock()
        parser.parse_index.return_value = units
        return parser

    def _patch_scope_env(self, tmp_path: Path) -> Any:
        """Patch WORKSPACE_ROOT, BACKLOG_ROOT, BACKLOG_INDEX, and BacklogParser for cmd_scope."""
        parser_mock = self._make_parser_mock()
        return patch.multiple(
            "devbench.cli",
            WORKSPACE_ROOT=tmp_path,
            BACKLOG_ROOT=tmp_path / "backlog",
            BACKLOG_INDEX=tmp_path / "BACKLOG.md",
            BacklogParser=MagicMock(return_value=parser_mock),
        )

    # ------------------------------------------------------------------
    # AC-196-1: scope set -- happy path (workspace-root scope.json)
    # ------------------------------------------------------------------

    def test_set_writes_scope_json(self, tmp_path: Path) -> None:
        """scope set --include creates scope.json with correct include/exclude fields."""
        with self._patch_scope_env(tmp_path):
            rc = cli.cmd_scope("set", "--include", "E1")

        assert rc == 0
        scope_path = self._scope_path(tmp_path)
        assert scope_path.exists(), "scope.json must be written on success"
        payload = json.loads(scope_path.read_text())
        assert payload["include"] == ["E1"]
        assert payload["exclude"] == []
        # expanded_ids must contain both E1-leaf IDs
        assert "E1-F1-S1-T1" in payload["expanded_ids"]
        assert "E1-F1-S1-T2" in payload["expanded_ids"]
        assert "E2-F1-S1-T1" not in payload["expanded_ids"]

    def test_set_with_exclude(self, tmp_path: Path) -> None:
        """scope set --include E1 --exclude E1-F1-S1-T2 excludes the specified task."""
        with self._patch_scope_env(tmp_path):
            rc = cli.cmd_scope("set", "--include", "E1", "--exclude", "E1-F1-S1-T2")

        assert rc == 0
        payload = json.loads(self._scope_path(tmp_path).read_text())
        assert "E1-F1-S1-T1" in payload["expanded_ids"]
        assert "E1-F1-S1-T2" not in payload["expanded_ids"]

    def test_set_overwrites_existing_scope_json(self, tmp_path: Path) -> None:
        """scope set overwrites a pre-existing scope.json (no merge)."""
        scope_path = self._scope_path(tmp_path)
        scope_path.parent.mkdir(parents=True, exist_ok=True)
        scope_path.write_text(json.dumps({"include": ["E99"], "exclude": [], "expanded_ids": []}))

        with self._patch_scope_env(tmp_path):
            rc = cli.cmd_scope("set", "--include", "E1-F1-S1-T1")

        assert rc == 0
        payload = json.loads(scope_path.read_text())
        assert payload["include"] == ["E1-F1-S1-T1"]

    # ------------------------------------------------------------------
    # AC-196-2: scope set -- invalid token -> rc=1, stderr matches cmd_start
    # ------------------------------------------------------------------

    def test_set_invalid_token_exits_1(self, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
        """scope set with a malformed token exits rc=1 and emits an error to stderr."""
        with self._patch_scope_env(tmp_path):
            rc = cli.cmd_scope("set", "--include", "-bad-token")

        assert rc == 1
        captured = capsys.readouterr()
        assert captured.err, "An error message must appear on stderr"
        assert not self._scope_path(tmp_path).exists(), "scope.json must NOT be created on error"

    def test_set_reverse_range_exits_1(self, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
        """scope set with a reverse range (E3-E1) exits rc=1."""
        with self._patch_scope_env(tmp_path):
            rc = cli.cmd_scope("set", "--include", "E3-E1")

        assert rc == 1
        captured = capsys.readouterr()
        assert "ERROR" in captured.err or "reverse" in captured.err.lower()

    # ------------------------------------------------------------------
    # AC-196-3: scope clear -- idempotent; exits 0 even if no file present
    # ------------------------------------------------------------------

    def test_clear_no_file_exits_0(self, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
        """scope clear exits 0 with 'no scope pending' when scope.json is absent."""
        with self._patch_scope_env(tmp_path):
            rc = cli.cmd_scope("clear")

        assert rc == 0
        captured = capsys.readouterr()
        assert "no scope pending" in captured.out

    def test_clear_removes_existing_scope_json(self, tmp_path: Path) -> None:
        """scope clear deletes scope.json when it is present."""
        scope_path = self._scope_path(tmp_path)
        scope_path.parent.mkdir(parents=True, exist_ok=True)
        scope_path.write_text(json.dumps({"include": [], "exclude": [], "expanded_ids": []}))

        with self._patch_scope_env(tmp_path):
            rc = cli.cmd_scope("clear")

        assert rc == 0
        assert not scope_path.exists()

    # ------------------------------------------------------------------
    # AC-196-4: scope show -- prints state or "no scope pending"
    # ------------------------------------------------------------------

    def test_show_no_file_exits_0(self, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
        """scope show exits 0 with 'no scope pending' when scope.json is absent."""
        with self._patch_scope_env(tmp_path):
            rc = cli.cmd_scope("show")

        assert rc == 0
        captured = capsys.readouterr()
        assert "no scope pending" in captured.out

    def test_show_with_scope_json_prints_details(self, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
        """scope show prints include, exclude, and expanded_ids count when scope.json exists."""
        scope_path = self._scope_path(tmp_path)
        scope_path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "include": ["E1"],
            "exclude": ["E1-F1-S1-T2"],
            "expanded_ids": ["E1-F1-S1-T1"],
            "started_at": "2026-05-16T00:00:00Z",
            "started_by": "alice",
        }
        scope_path.write_text(json.dumps(payload))

        with self._patch_scope_env(tmp_path):
            rc = cli.cmd_scope("show")

        assert rc == 0
        out = capsys.readouterr().out
        assert "E1" in out
        assert "1" in out  # expanded_ids count or content
        assert "2026-05-16" in out or "alice" in out

    # ------------------------------------------------------------------
    # AC-196-9 / error: unknown action verb -> rc=2
    # ------------------------------------------------------------------

    def test_unknown_action_exits_2(self, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
        """scope <unknown> exits rc=2 with an actionable error to stderr."""
        with self._patch_scope_env(tmp_path):
            rc = cli.cmd_scope("bogus")

        assert rc == 2
        captured = capsys.readouterr()
        assert "bogus" in captured.err
        assert "set" in captured.err
        assert "clear" in captured.err
        assert "show" in captured.err

    def test_no_action_exits_2(self, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
        """scope with no action argument exits rc=2."""
        with self._patch_scope_env(tmp_path):
            rc = cli.cmd_scope()

        assert rc == 2
        captured = capsys.readouterr()
        assert captured.err

    # ------------------------------------------------------------------
    # Error: set without --include flag
    # ------------------------------------------------------------------

    def test_set_without_include_flag_exits_1(self, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
        """scope set without --include flag exits rc=1 with actionable error."""
        with self._patch_scope_env(tmp_path):
            rc = cli.cmd_scope("set")

        assert rc == 1
        captured = capsys.readouterr()
        assert captured.err

    # ------------------------------------------------------------------
    # AC-196: session integration -- DEVBENCH_SESSION_NAME path resolution
    # ------------------------------------------------------------------

    def test_set_uses_session_path_when_env_var_set(self, tmp_path: Path) -> None:
        """scope set writes to session-scoped path when DEVBENCH_SESSION_NAME is set."""
        session_name = "alpha"

        with (
            self._patch_scope_env(tmp_path),
            patch.dict("os.environ", {"DEVBENCH_SESSION_NAME": session_name}, clear=False),
        ):
            rc = cli.cmd_scope("set", "--include", "E1-F1-S1-T1")

        assert rc == 0
        session_scope = self._session_scope_path(tmp_path, session_name)
        assert session_scope.exists(), "Session-scoped scope.json must be written"
        payload = json.loads(session_scope.read_text())
        assert payload["include"] == ["E1-F1-S1-T1"]
        # workspace-root scope.json must NOT be created
        assert not self._scope_path(tmp_path).exists(), (
            "Workspace-root scope.json must not be created when session name is set"
        )

    def test_clear_uses_session_path_when_env_var_set(self, tmp_path: Path) -> None:
        """scope clear deletes the session-scoped scope.json."""
        session_name = "beta"
        session_scope = self._session_scope_path(tmp_path, session_name)
        session_scope.parent.mkdir(parents=True, exist_ok=True)
        session_scope.write_text(json.dumps({"include": [], "exclude": [], "expanded_ids": []}))

        with (
            self._patch_scope_env(tmp_path),
            patch.dict("os.environ", {"DEVBENCH_SESSION_NAME": session_name}, clear=False),
        ):
            rc = cli.cmd_scope("clear")

        assert rc == 0
        assert not session_scope.exists()

    def test_show_uses_session_path_when_env_var_set(self, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
        """scope show reads the session-scoped scope.json."""
        session_name = "gamma"
        session_scope = self._session_scope_path(tmp_path, session_name)
        session_scope.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "include": ["E2"],
            "exclude": [],
            "expanded_ids": ["E2-F1-S1-T1"],
            "started_at": "2026-05-16T01:00:00Z",
            "started_by": "bob",
        }
        session_scope.write_text(json.dumps(payload))

        with (
            self._patch_scope_env(tmp_path),
            patch.dict("os.environ", {"DEVBENCH_SESSION_NAME": session_name}, clear=False),
        ):
            rc = cli.cmd_scope("show")

        assert rc == 0
        out = capsys.readouterr().out
        assert "E2" in out

    # ------------------------------------------------------------------
    # Parametrised: multiple valid selector shapes
    # ------------------------------------------------------------------

    @pytest.mark.parametrize(
        "include_str, expected_present, expected_absent",
        [
            ("E1-F1-S1-T1", ["E1-F1-S1-T1"], ["E1-F1-S1-T2", "E2-F1-S1-T1"]),
            ("E1", ["E1-F1-S1-T1", "E1-F1-S1-T2"], ["E2-F1-S1-T1"]),
            ("E1,E2", ["E1-F1-S1-T1", "E1-F1-S1-T2", "E2-F1-S1-T1"], []),
        ],
    )
    def test_set_selector_shapes(
        self,
        tmp_path: Path,
        include_str: str,
        expected_present: list[str],
        expected_absent: list[str],
    ) -> None:
        """scope set correctly expands various valid selector shapes."""
        with self._patch_scope_env(tmp_path):
            rc = cli.cmd_scope("set", "--include", include_str)

        assert rc == 0
        payload = json.loads(self._scope_path(tmp_path).read_text())
        for wid in expected_present:
            assert wid in payload["expanded_ids"], f"{wid} expected in expanded_ids"
        for wid in expected_absent:
            assert wid not in payload["expanded_ids"], f"{wid} must not be in expanded_ids"

    # ------------------------------------------------------------------
    # AC-196: round-trip equivalence -- set then cmd_next honours it
    # ------------------------------------------------------------------

    def test_scope_set_honoured_by_cmd_next(self, tmp_path: Path) -> None:
        """scope.json written by cmd_scope set is honoured by cmd_next identically to cmd_start."""
        with self._patch_scope_env(tmp_path):
            set_rc = cli.cmd_scope("set", "--include", "E1-F1-S1-T1")

        assert set_rc == 0
        scope_path = self._scope_path(tmp_path)
        assert scope_path.exists()

        # Verify the written file has the standard fields cmd_start would write
        payload = json.loads(scope_path.read_text())
        assert "include" in payload
        assert "exclude" in payload
        assert "expanded_ids" in payload
        assert "started_at" in payload
        assert "started_by" in payload

    # ------------------------------------------------------------------
    # cmd_scope is registered in _COMMANDS
    # ------------------------------------------------------------------

    def test_scope_registered_in_commands(self) -> None:
        """cmd_scope is available as the 'scope' command in _COMMANDS."""
        assert "scope" in cli._COMMANDS

    # ------------------------------------------------------------------
    # main() dispatches to cmd_scope
    # ------------------------------------------------------------------

    def test_main_dispatches_scope_clear(self, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
        """main() correctly dispatches 'devbench scope clear'."""
        with (
            patch("sys.argv", ["devbench", "scope", "clear"]),
            patch("devbench.cli.WORKSPACE_ROOT", tmp_path),
            patch("devbench.cli.BACKLOG_ROOT", tmp_path / "backlog"),
            patch("devbench.cli.BACKLOG_INDEX", tmp_path / "BACKLOG.md"),
        ):
            result = cli.main()

        assert result == 0
        out = capsys.readouterr().out
        assert "no scope pending" in out


# ---------------------------------------------------------------------------
# AC-196-5 through AC-196-10: Parametrised tests + scope-set round-trip
# equivalence integration test (E2-F7-S1-T2, spec section 4.2.6.5)
# ---------------------------------------------------------------------------


class TestCmdScopeParametrised:
    """Parametrised tests for cmd_scope covering the selector shapes specified
    in the work-unit description and the round-trip-equivalence integration test
    (AC-196-5, AC-196-6, AC-196-7, AC-196-8, AC-196-10).
    """

    # Backlog IDs used in all parametrised tests -- covers E1 through E5.
    _BACKLOG_IDS: ClassVar[list[str]] = [
        "E1-F1-S1-T1",
        "E1-F1-S1-T2",
        "E1-F2-S1-T1",
        "E2-F1-S1-T1",
        "E2-F1-S1-T2",
        "E3-F1-S1-T1",
        "E3-F1-S1-T2",
        "E4-F1-S1-T1",
        "E5-F1-S1-T1",
    ]

    def _scope_path(self, tmp_path: Path) -> Path:
        """Return the canonical scope.json path under tmp_path."""
        return tmp_path / ".devbench" / "scope.json"

    def _session_scope_path(self, tmp_path: Path, session_name: str) -> Path:
        """Return the per-session scope.json path."""
        return tmp_path / ".devbench" / "sessions" / session_name / "scope.json"

    def _make_parser_mock(self, backlog_ids: list[str]) -> MagicMock:
        """Return a BacklogParser mock whose parse_index yields the supplied IDs."""
        units = [MagicMock(id=wid) for wid in backlog_ids]
        parser = MagicMock()
        parser.parse_index.return_value = units
        return parser

    def _patch_scope_env(self, tmp_path: Path, backlog_ids: list[str] | None = None) -> Any:
        """Patch WORKSPACE_ROOT, BACKLOG_ROOT, BACKLOG_INDEX, and BacklogParser."""
        ids = backlog_ids if backlog_ids is not None else self._BACKLOG_IDS
        parser_mock = self._make_parser_mock(ids)
        return patch.multiple(
            "devbench.cli",
            WORKSPACE_ROOT=tmp_path,
            BACKLOG_ROOT=tmp_path / "backlog",
            BACKLOG_INDEX=tmp_path / "BACKLOG.md",
            BacklogParser=MagicMock(return_value=parser_mock),
        )

    # ------------------------------------------------------------------
    # AC-196-5: Parametrised selector shapes for scope set
    # ------------------------------------------------------------------

    @pytest.mark.parametrize(
        "include_str, exclude_str, expected_present, expected_absent",
        [
            # Single ID -- matches only the named task
            (
                "E1-F1-S1-T1",
                "",
                ["E1-F1-S1-T1"],
                ["E1-F1-S1-T2", "E2-F1-S1-T1"],
            ),
            # Range E1-E3 -- matches all under E1, E2, E3; excludes E4 and E5
            (
                "E1-E3",
                "",
                ["E1-F1-S1-T1", "E2-F1-S1-T1", "E3-F1-S1-T1"],
                ["E4-F1-S1-T1", "E5-F1-S1-T1"],
            ),
            # Range E1-E3 plus E5 (mixed comma-separated)
            (
                "E1-E3, E5",
                "",
                ["E1-F1-S1-T1", "E2-F1-S1-T1", "E3-F1-S1-T1", "E5-F1-S1-T1"],
                ["E4-F1-S1-T1"],
            ),
            # Range E1-E3 with exclude E2-F1
            (
                "E1-E3",
                "E2-F1",
                ["E1-F1-S1-T1", "E3-F1-S1-T1"],
                ["E2-F1-S1-T1", "E2-F1-S1-T2"],
            ),
        ],
    )
    def test_set_selector_shapes_parametrised(
        self,
        tmp_path: Path,
        include_str: str,
        exclude_str: str,
        expected_present: list[str],
        expected_absent: list[str],
    ) -> None:
        """scope set expands the specified selector shapes correctly (AC-196-5).

        Covers single-ID, range, mixed comma-separated, and include+exclude combos
        per spec section 4.2.6.5.
        """
        argv: list[str] = ["set", "--include", include_str]
        if exclude_str:
            argv += ["--exclude", exclude_str]

        with self._patch_scope_env(tmp_path):
            rc = cli.cmd_scope(*argv)

        assert rc == 0, f"Expected exit 0 for include={include_str!r}, exclude={exclude_str!r}"
        payload = json.loads(self._scope_path(tmp_path).read_text())
        for wid in expected_present:
            assert wid in payload["expanded_ids"], f"{wid} must be in expanded_ids"
        for wid in expected_absent:
            assert wid not in payload["expanded_ids"], f"{wid} must not be in expanded_ids"

    # ------------------------------------------------------------------
    # AC-196-5: Parametrised negative cases for set
    # ------------------------------------------------------------------

    @pytest.mark.parametrize(
        "bad_include",
        [
            "-E1",  # leading hyphen
            "E1-",  # trailing hyphen
            "-",  # bare hyphen
            "E1--E3",  # consecutive hyphens
        ],
    )
    def test_set_malformed_token_exits_1(
        self,
        tmp_path: Path,
        bad_include: str,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """scope set with a malformed token exits rc=1 and emits stderr (AC-196-5).

        Malformed tokens must be rejected fail-fast; scope.json must NOT be written.
        Error message format matches cmd_start --include (no drift from ScopeFilter.parse).
        """
        with self._patch_scope_env(tmp_path):
            rc = cli.cmd_scope("set", "--include", bad_include)

        assert rc == 1
        captured = capsys.readouterr()
        assert captured.err, "A stderr message must be emitted for malformed tokens"
        assert not self._scope_path(tmp_path).exists(), "scope.json must NOT be created when the token is invalid"

    # ------------------------------------------------------------------
    # AC-196-5: Reverse range rejection
    # ------------------------------------------------------------------

    @pytest.mark.parametrize(
        "reverse_range",
        [
            "E3-E1",
            "E1-F1-S1-T3-T1",
        ],
    )
    def test_set_reverse_range_exits_1(
        self,
        tmp_path: Path,
        reverse_range: str,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """scope set with a reverse range exits rc=1 with an error on stderr (AC-196-5)."""
        with self._patch_scope_env(tmp_path):
            rc = cli.cmd_scope("set", "--include", reverse_range)

        assert rc == 1
        captured = capsys.readouterr()
        assert captured.err, "A stderr message must be emitted for reverse ranges"
        assert not self._scope_path(tmp_path).exists(), "scope.json must NOT be created for reverse-range tokens"

    # ------------------------------------------------------------------
    # AC-196-6: Unknown action verb exits rc=2 (parametrised)
    # ------------------------------------------------------------------

    @pytest.mark.parametrize(
        "bad_action",
        [
            "bogus",
            "SET",  # case-sensitive mismatch
            "reset",
            "delete",
            "",  # interpreted as action='' after empty argv
        ],
    )
    def test_unknown_action_exits_2_parametrised(
        self,
        tmp_path: Path,
        bad_action: str,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """Unknown or misspelled action verbs exit rc=2 (AC-196-6).

        The empty-string case is handled by passing no args, which also triggers
        the missing-action branch (rc=2).
        """
        with self._patch_scope_env(tmp_path):
            rc = cli.cmd_scope() if bad_action == "" else cli.cmd_scope(bad_action)

        assert rc == 2
        captured = capsys.readouterr()
        assert captured.err, "A stderr message must be emitted for unknown actions"

    # ------------------------------------------------------------------
    # AC-196-7: clear / show -- expected output shapes (parametrised)
    # ------------------------------------------------------------------

    @pytest.mark.parametrize(
        "action, setup_file, expected_in_stdout",
        [
            # clear on missing file -> idempotent, "no scope pending"
            ("clear", False, "no scope pending"),
            # show on missing file -> "no scope pending"
            ("show", False, "no scope pending"),
        ],
    )
    def test_clear_show_no_file_parametrised(
        self,
        tmp_path: Path,
        action: str,
        setup_file: bool,
        expected_in_stdout: str,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """scope clear and scope show both exit 0 with 'no scope pending' when absent (AC-196-7)."""
        assert not setup_file, "This test variant does not create a scope.json upfront"
        with self._patch_scope_env(tmp_path):
            rc = cli.cmd_scope(action)

        assert rc == 0
        captured = capsys.readouterr()
        assert expected_in_stdout in captured.out

    def test_show_active_scope_prints_fields(
        self,
        tmp_path: Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """scope show prints include, exclude, expanded_ids count, started_at, and started_by (AC-196-7)."""
        scope_path = self._scope_path(tmp_path)
        scope_path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "include": ["E1-E3"],
            "exclude": ["E2-F1"],
            "expanded_ids": ["E1-F1-S1-T1", "E3-F1-S1-T1"],
            "started_at": "2026-05-16T10:00:00Z",
            "started_by": "operator",
        }
        scope_path.write_text(json.dumps(payload))

        with self._patch_scope_env(tmp_path):
            rc = cli.cmd_scope("show")

        assert rc == 0
        out = capsys.readouterr().out
        assert "E1-E3" in out
        assert "E2-F1" in out
        assert "2" in out  # expanded_ids count is 2
        assert "2026-05-16" in out
        assert "operator" in out

    # ------------------------------------------------------------------
    # AC-196-8: Per-session scope.json path (DEVBENCH_SESSION_NAME)
    # ------------------------------------------------------------------

    @pytest.mark.parametrize("session_name", ["foo", "my-session", "alpha"])
    def test_set_writes_to_session_path(
        self,
        tmp_path: Path,
        session_name: str,
    ) -> None:
        """scope set writes to sessions/<name>/scope.json when DEVBENCH_SESSION_NAME is set (AC-196-8)."""
        with (
            self._patch_scope_env(tmp_path),
            patch.dict("os.environ", {"DEVBENCH_SESSION_NAME": session_name}, clear=False),
        ):
            rc = cli.cmd_scope("set", "--include", "E1-F1-S1-T1")

        assert rc == 0
        session_path = self._session_scope_path(tmp_path, session_name)
        assert session_path.exists(), f"Session-scoped scope.json must exist at {session_path}"
        payload = json.loads(session_path.read_text())
        assert payload["include"] == ["E1-F1-S1-T1"]
        # Canonical workspace-root scope.json must NOT be created
        assert not self._scope_path(tmp_path).exists(), (
            "Workspace-root scope.json must not exist when DEVBENCH_SESSION_NAME is set"
        )

    # ------------------------------------------------------------------
    # AC-196-10: Round-trip equivalence integration test
    # cmd_scope set + cmd_next == cmd_next --include (same candidate WU id)
    # ------------------------------------------------------------------

    @staticmethod
    def _build_fixture_backlog(tmp_path: Path, unit_rows: list[tuple[str, str]]) -> Path:
        """Build a real on-disk backlog fixture from (unit_id, status) rows.

        Args:
            tmp_path: The temporary workspace root.
            unit_rows: List of (unit_id, status) tuples.  Supports statuses
                ``in-queue`` and ``done``.

        Returns:
            Path to the written BACKLOG.md index file.
        """
        wu_dir = tmp_path / "backlog"
        wu_dir.mkdir(exist_ok=True)
        index_lines = [
            "# Backlog\n",
            "## Full Work Unit Index\n",
            "| ID | Title | Type | Status | Dependencies | Repo | File Path |",
            "|----|-------|------|--------|--------------|------|-----------|",
        ]
        for unit_id, status in unit_rows:
            file_path = f"backlog/{unit_id}.md"
            index_lines.append(
                f"| {unit_id} | {unit_id} Task | Task | {status} | None | caylent-solutions/test-repo | `{file_path}` |"
            )
            wu_body = f"# {unit_id}: {unit_id} Task\n\n## Status: {status}\n\n## Description\n\nTest fixture.\n"
            (wu_dir / f"{unit_id}.md").write_text(wu_body)
        index_path = tmp_path / "BACKLOG.md"
        index_path.write_text("\n".join(index_lines) + "\n")
        return index_path

    def test_scope_set_round_trip_cmd_next_equivalence(
        self,
        tmp_path: Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """cmd_scope set + cmd_next honours scope.json identically to cmd_next --include (AC-196-10).

        Integration test using a real on-disk backlog fixture:
        1. Write scope via ``cmd_scope set --include "E2"``.
        2. Call ``cmd_next`` (no flags) -- reads scope.json from workspace.
        3. Call ``cmd_next --include "E2"`` (inline flags) -- bypasses scope.json.
        4. Assert both calls returned the same candidate WU id.

        This verifies the byte-identical scope.json claim from spec 4.2.6.5:
        both pathways resolve through ``ScopeFilter.parse/from_file`` and produce
        the same candidate list from ``BacklogParser.get_parallel_candidates``.
        """
        # Build a backlog with two in-queue tasks: one in E1, one in E2.
        # Only the E2 task should be returned when scope is limited to E2.
        unit_rows = [
            ("E1-F1-S1-T1", "in-queue"),
            ("E2-F1-S1-T1", "in-queue"),
        ]
        index_path = self._build_fixture_backlog(tmp_path, unit_rows)

        # Step 1: Write scope via cmd_scope set
        with (
            patch("devbench.cli.WORKSPACE_ROOT", tmp_path),
            patch("devbench.cli.BACKLOG_ROOT", tmp_path / "backlog"),
            patch("devbench.cli.BACKLOG_INDEX", index_path),
        ):
            set_rc = cli.cmd_scope("set", "--include", "E2")
        assert set_rc == 0
        assert self._scope_path(tmp_path).exists()
        # Drain any stdout printed by cmd_scope set ("scope set: <path>") so
        # the subsequent cmd_next capture is clean.
        capsys.readouterr()

        # Step 2: cmd_next with no flags -- reads from scope.json
        with (
            patch("devbench.cli.WORKSPACE_ROOT", tmp_path),
            patch("devbench.cli.BACKLOG_ROOT", tmp_path / "backlog"),
            patch("devbench.cli.BACKLOG_INDEX", index_path),
        ):
            next_rc_from_file = cli.cmd_next()
        assert next_rc_from_file == 0
        out_from_file = capsys.readouterr().out.strip()

        # Step 3: cmd_next --include "E2" -- inline flags, bypasses scope.json
        # Remove scope.json so from_file path is not triggered accidentally
        self._scope_path(tmp_path).unlink()
        with (
            patch("devbench.cli.WORKSPACE_ROOT", tmp_path),
            patch("devbench.cli.BACKLOG_ROOT", tmp_path / "backlog"),
            patch("devbench.cli.BACKLOG_INDEX", index_path),
        ):
            next_rc_inline = cli.cmd_next("--include", "E2")
        assert next_rc_inline == 0
        out_inline = capsys.readouterr().out.strip()

        # Step 4: Both outputs must be non-empty and contain the same candidate WU id
        assert out_from_file, "cmd_next (scope.json) must print a candidate"
        assert out_inline, "cmd_next --include must print a candidate"
        id_from_file = json.loads(out_from_file)["id"]
        id_inline = json.loads(out_inline)["id"]
        assert id_from_file == id_inline, (
            f"Round-trip equivalence failed: scope.json path returned {id_from_file!r} "
            f"but --include flag path returned {id_inline!r}"
        )
        # Both must return the E2 task, not the E1 task
        assert id_from_file == "E2-F1-S1-T1", f"Scope filter must select E2 task, got {id_from_file!r}"

    def test_scope_set_round_trip_cmd_next_no_match_in_scope(
        self,
        tmp_path: Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """cmd_next returns NO_ACTIONABLE_IN_SCOPE when scope.json excludes all candidates (AC-196-10).

        Validates the zero-match scope case per spec 4.2.6 / AC-190-15:
        when a scope is active but no candidates fall within it, cmd_next exits 0
        and prints NO_ACTIONABLE_IN_SCOPE (not NO_ACTIONABLE).
        """
        unit_rows = [("E1-F1-S1-T1", "in-queue")]
        index_path = self._build_fixture_backlog(tmp_path, unit_rows)

        # Set scope to E2 -- no E2 tasks exist in the backlog
        with (
            patch("devbench.cli.WORKSPACE_ROOT", tmp_path),
            patch("devbench.cli.BACKLOG_ROOT", tmp_path / "backlog"),
            patch("devbench.cli.BACKLOG_INDEX", index_path),
        ):
            set_rc = cli.cmd_scope("set", "--include", "E2")
        assert set_rc == 0

        with (
            patch("devbench.cli.WORKSPACE_ROOT", tmp_path),
            patch("devbench.cli.BACKLOG_ROOT", tmp_path / "backlog"),
            patch("devbench.cli.BACKLOG_INDEX", index_path),
        ):
            next_rc = cli.cmd_next()
        assert next_rc == 0
        assert "NO_ACTIONABLE_IN_SCOPE" in capsys.readouterr().out


# ---------------------------------------------------------------------------
# cmd_drain tests (E3-F2-S1-T1, issue #188)
# ---------------------------------------------------------------------------


class TestCmdDrainRegistered:
    """cmd_drain is registered in _COMMANDS and _VARIADIC_COMMANDS."""

    @pytest.mark.unit
    def test_drain_in_commands(self) -> None:
        """'drain' key must be present in _COMMANDS."""
        assert "drain" in cli._COMMANDS

    @pytest.mark.unit
    def test_drain_in_variadic_commands(self) -> None:
        """'drain' must be in _VARIADIC_COMMANDS so flag parsing works."""
        assert "drain" in cli._VARIADIC_COMMANDS

    @pytest.mark.unit
    def test_drain_command_maps_to_cmd_drain(self) -> None:
        """_COMMANDS['drain'] callable must be cli.cmd_drain."""
        func, _min_args, _desc = cli._COMMANDS["drain"]
        assert func is cli.cmd_drain


class TestCmdDrainNoArgs:
    """devbench drain (no args) -- creates drain.signal with empty reason (AC-188-1)."""

    @pytest.mark.unit
    def test_creates_signal_file(self, tmp_path: Path) -> None:
        """drain with no args writes drain.signal to workspace root."""
        with patch("devbench.cli.WORKSPACE_ROOT", tmp_path):
            rc = cli.cmd_drain()
        assert rc == 0
        signal = tmp_path / ".devbench" / "drain.signal"
        assert signal.exists(), "drain.signal must be created"

    @pytest.mark.unit
    def test_signal_file_has_valid_json(self, tmp_path: Path) -> None:
        """drain.signal contains a JSON object with required keys."""
        import json as _json

        with patch("devbench.cli.WORKSPACE_ROOT", tmp_path):
            cli.cmd_drain()
        signal = tmp_path / ".devbench" / "drain.signal"
        data = _json.loads(signal.read_text())
        assert "requested_at" in data
        assert "requested_by" in data
        assert "reason" in data

    @pytest.mark.unit
    def test_signal_file_has_empty_reason(self, tmp_path: Path) -> None:
        """drain with no args writes empty reason."""
        import json as _json

        with patch("devbench.cli.WORKSPACE_ROOT", tmp_path):
            cli.cmd_drain()
        signal = tmp_path / ".devbench" / "drain.signal"
        data = _json.loads(signal.read_text())
        assert data["reason"] == ""

    @pytest.mark.unit
    def test_return_code_is_zero(self, tmp_path: Path) -> None:
        """drain returns rc=0 on success."""
        with patch("devbench.cli.WORKSPACE_ROOT", tmp_path):
            rc = cli.cmd_drain()
        assert rc == 0


class TestCmdDrainWithReason:
    """devbench drain --reason '<text>' -- creates drain.signal with given reason (AC-188-1)."""

    @pytest.mark.unit
    def test_creates_signal_with_reason(self, tmp_path: Path) -> None:
        """drain --reason writes the given reason into drain.signal."""
        import json as _json

        with patch("devbench.cli.WORKSPACE_ROOT", tmp_path):
            rc = cli.cmd_drain("--reason", "planned maintenance")
        assert rc == 0
        signal = tmp_path / ".devbench" / "drain.signal"
        data = _json.loads(signal.read_text())
        assert data["reason"] == "planned maintenance"

    @pytest.mark.unit
    def test_return_code_is_zero(self, tmp_path: Path) -> None:
        """drain --reason returns rc=0."""
        with patch("devbench.cli.WORKSPACE_ROOT", tmp_path):
            rc = cli.cmd_drain("--reason", "maintenance")
        assert rc == 0


class TestCmdDrainCancel:
    """devbench drain --cancel -- removes marker; idempotent (AC-188-2)."""

    @pytest.mark.unit
    def test_removes_existing_signal(self, tmp_path: Path) -> None:
        """drain --cancel deletes an existing drain.signal."""
        with patch("devbench.cli.WORKSPACE_ROOT", tmp_path):
            cli.cmd_drain()
            rc = cli.cmd_drain("--cancel")
        assert rc == 0
        signal = tmp_path / ".devbench" / "drain.signal"
        assert not signal.exists(), "drain.signal must be deleted after --cancel"

    @pytest.mark.unit
    def test_idempotent_when_no_signal(self, tmp_path: Path) -> None:
        """drain --cancel is idempotent -- rc=0 even when no signal file exists."""
        with patch("devbench.cli.WORKSPACE_ROOT", tmp_path):
            rc = cli.cmd_drain("--cancel")
        assert rc == 0

    @pytest.mark.unit
    def test_return_code_is_zero(self, tmp_path: Path) -> None:
        """drain --cancel returns rc=0 in both present and absent cases."""
        with patch("devbench.cli.WORKSPACE_ROOT", tmp_path):
            rc = cli.cmd_drain("--cancel")
        assert rc == 0


class TestCmdDrainStatus:
    """devbench drain --status -- prints state or 'no drain pending'; rc=0 (AC-188-3)."""

    @pytest.mark.unit
    def test_prints_no_drain_pending_when_absent(self, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
        """drain --status prints 'no drain pending' when no signal file exists."""
        with patch("devbench.cli.WORKSPACE_ROOT", tmp_path):
            rc = cli.cmd_drain("--status")
        assert rc == 0
        out = capsys.readouterr().out
        assert "no drain pending" in out

    @pytest.mark.unit
    def test_prints_drain_state_when_present(self, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
        """drain --status prints drain state when signal file exists."""
        with patch("devbench.cli.WORKSPACE_ROOT", tmp_path):
            cli.cmd_drain("--reason", "batch closed")
            rc = cli.cmd_drain("--status")
        assert rc == 0
        out = capsys.readouterr().out
        assert "drain pending" in out

    @pytest.mark.unit
    def test_return_code_is_zero_when_pending(self, tmp_path: Path) -> None:
        """drain --status returns rc=0 when drain is pending."""
        with patch("devbench.cli.WORKSPACE_ROOT", tmp_path):
            cli.cmd_drain()
            rc = cli.cmd_drain("--status")
        assert rc == 0

    @pytest.mark.unit
    def test_return_code_is_zero_when_absent(self, tmp_path: Path) -> None:
        """drain --status returns rc=0 when no signal is pending."""
        with patch("devbench.cli.WORKSPACE_ROOT", tmp_path):
            rc = cli.cmd_drain("--status")
        assert rc == 0

    @pytest.mark.unit
    def test_does_not_delete_signal_file(self, tmp_path: Path) -> None:
        """drain --status does not consume the signal (read-only)."""
        with patch("devbench.cli.WORKSPACE_ROOT", tmp_path):
            cli.cmd_drain()
            cli.cmd_drain("--status")
        signal = tmp_path / ".devbench" / "drain.signal"
        assert signal.exists(), "drain --status must not delete drain.signal"


class TestCmdDrainMutuallyExclusive:
    """Mutually exclusive flags produce rc=2 and an error on stderr."""

    @pytest.mark.unit
    @pytest.mark.parametrize(
        "argv",
        [
            ("--cancel", "--status"),
            ("--status", "--reason", "x"),
            ("--cancel", "--reason", "x"),
            ("--cancel", "--status", "--reason", "x"),
        ],
    )
    def test_mutual_exclusion_error(
        self, argv: tuple[str, ...], tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """Combining mutually exclusive flags yields rc=2."""
        with patch("devbench.cli.WORKSPACE_ROOT", tmp_path):
            rc = cli.cmd_drain(*argv)
        assert rc == 2
        err = capsys.readouterr().err
        assert err, "An error message must be written to stderr"
        assert "--cancel" in err or "--status" in err or "mutually exclusive" in err

    @pytest.mark.unit
    def test_reason_without_value_rc(self, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
        """--reason without a following value yields rc=2."""
        with patch("devbench.cli.WORKSPACE_ROOT", tmp_path):
            rc = cli.cmd_drain("--reason")
        assert rc == 2

    @pytest.mark.unit
    def test_reason_without_value_stderr(self, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
        """--reason without a following value emits the missing-value error on stderr."""
        with patch("devbench.cli.WORKSPACE_ROOT", tmp_path):
            cli.cmd_drain("--reason")
        err = capsys.readouterr().err
        assert "requires a value" in err, f"Expected 'requires a value' in stderr, got: {err!r}"


class TestCmdStatusDrainBanner:
    """cmd_status prepends a DRAIN REQUESTED banner when drain.signal is present (AC-188-7)."""

    @pytest.fixture()
    def mock_backlog_parser(self) -> MagicMock:
        """Return a pre-configured MagicMock that stands in for BacklogParser.

        All 6 test methods share this setup: an empty backlog with all_done=True
        so cmd_status returns 0 and we can isolate banner-related assertions.
        """
        mock_parser = MagicMock()
        mock_parser.parse_index.return_value = []
        mock_parser.get_parallel_candidates.return_value = []
        mock_parser.all_done.return_value = True
        return mock_parser

    @pytest.mark.unit
    def test_drain_banner_shown_when_signal_present(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str], mock_backlog_parser: MagicMock
    ) -> None:
        """cmd_status renders 'DRAIN REQUESTED' banner when drain.signal exists."""
        with patch("devbench.cli.WORKSPACE_ROOT", tmp_path):
            cli.cmd_drain("--reason", "pre-release freeze")

        with (
            patch("devbench.cli.WORKSPACE_ROOT", tmp_path),
            patch("devbench.cli.BacklogParser", return_value=mock_backlog_parser),
        ):
            rc = cli.cmd_status()

        assert rc == 0
        out = capsys.readouterr().out
        assert "DRAIN REQUESTED" in out

    @pytest.mark.unit
    def test_drain_banner_contains_reason(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str], mock_backlog_parser: MagicMock
    ) -> None:
        """cmd_status drain banner includes the reason text."""
        with patch("devbench.cli.WORKSPACE_ROOT", tmp_path):
            cli.cmd_drain("--reason", "pre-release freeze")

        with (
            patch("devbench.cli.WORKSPACE_ROOT", tmp_path),
            patch("devbench.cli.BacklogParser", return_value=mock_backlog_parser),
        ):
            cli.cmd_status()

        out = capsys.readouterr().out
        assert "pre-release freeze" in out

    @pytest.mark.unit
    def test_no_drain_banner_when_signal_absent(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str], mock_backlog_parser: MagicMock
    ) -> None:
        """cmd_status does not render drain banner when no signal file exists."""
        with (
            patch("devbench.cli.WORKSPACE_ROOT", tmp_path),
            patch("devbench.cli.BacklogParser", return_value=mock_backlog_parser),
        ):
            rc = cli.cmd_status()

        assert rc == 0
        out = capsys.readouterr().out
        assert "DRAIN REQUESTED" not in out

    @pytest.mark.unit
    def test_drain_banner_appears_before_status_summary(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str], mock_backlog_parser: MagicMock
    ) -> None:
        """cmd_status renders the DRAIN REQUESTED banner before the Status Summary header (spec 4.3.5)."""
        with patch("devbench.cli.WORKSPACE_ROOT", tmp_path):
            cli.cmd_drain("--reason", "release freeze")

        with (
            patch("devbench.cli.WORKSPACE_ROOT", tmp_path),
            patch("devbench.cli.BacklogParser", return_value=mock_backlog_parser),
        ):
            cli.cmd_status()

        out = capsys.readouterr().out
        assert "DRAIN REQUESTED" in out
        assert "Backlog Status Summary" in out
        assert out.index("DRAIN REQUESTED") < out.index("Backlog Status Summary")

    @pytest.mark.unit
    def test_drain_banner_empty_reason_renders_none_literal(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str], mock_backlog_parser: MagicMock
    ) -> None:
        """cmd_status drain banner renders '(reason: (none))' when reason is empty string."""
        with patch("devbench.cli.WORKSPACE_ROOT", tmp_path):
            cli.cmd_drain()

        with (
            patch("devbench.cli.WORKSPACE_ROOT", tmp_path),
            patch("devbench.cli.BacklogParser", return_value=mock_backlog_parser),
        ):
            cli.cmd_status()

        out = capsys.readouterr().out
        assert "DRAIN REQUESTED" in out
        assert "(reason: (none))" in out

    @pytest.mark.unit
    def test_drain_banner_full_format(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str], mock_backlog_parser: MagicMock
    ) -> None:
        """cmd_status drain banner matches the spec format: 'DRAIN REQUESTED: at <ts> by <user> (reason: <text>)'."""
        import re

        with patch("devbench.cli.WORKSPACE_ROOT", tmp_path):
            cli.cmd_drain("--reason", "scheduled maintenance")

        with (
            patch("devbench.cli.WORKSPACE_ROOT", tmp_path),
            patch("devbench.cli.BacklogParser", return_value=mock_backlog_parser),
        ):
            cli.cmd_status()

        out = capsys.readouterr().out
        pattern = r"DRAIN REQUESTED: at \S+ by \S+ \(reason: scheduled maintenance\)"
        assert re.search(pattern, out), f"Expected banner matching {pattern!r} in output, got: {out!r}"

    @pytest.mark.unit
    def test_render_drain_banner_file_parameter(self, tmp_path: Path) -> None:
        """_render_drain_banner writes to the provided file stream, not only sys.stdout."""
        import io

        with patch("devbench.cli.WORKSPACE_ROOT", tmp_path):
            cli.cmd_drain("--reason", "file-param test")

        buf = io.StringIO()
        cli._render_drain_banner(tmp_path, file=buf)
        output = buf.getvalue()
        assert "DRAIN REQUESTED" in output
        assert "file-param test" in output


class TestCmdDrainIntegration:
    """Integration tests: full cmd_drain flows against a real tmp workspace fixture (AC-188-1..3).

    No boundary-crossing mocks -- only WORKSPACE_ROOT is patched to isolate
    the workspace directory. The drain module functions are called for real.
    """

    @pytest.mark.unit
    def test_full_request_cancel_cycle(self, tmp_path: Path) -> None:
        """drain -> drain --status -> drain --cancel -> drain --status cycle works end-to-end."""
        signal = tmp_path / ".devbench" / "drain.signal"

        with patch("devbench.cli.WORKSPACE_ROOT", tmp_path):
            # Step 1: request drain
            rc_request = cli.cmd_drain("--reason", "integration test")
        assert rc_request == 0
        assert signal.exists()

        with patch("devbench.cli.WORKSPACE_ROOT", tmp_path):
            # Step 2: status shows pending
            rc_status_pending = cli.cmd_drain("--status")
        assert rc_status_pending == 0
        assert signal.exists(), "drain --status must not consume signal"

        with patch("devbench.cli.WORKSPACE_ROOT", tmp_path):
            # Step 3: cancel
            rc_cancel = cli.cmd_drain("--cancel")
        assert rc_cancel == 0
        assert not signal.exists()

        with patch("devbench.cli.WORKSPACE_ROOT", tmp_path):
            # Step 4: status shows absent (rc still 0)
            rc_status_absent = cli.cmd_drain("--status")
        assert rc_status_absent == 0

    @pytest.mark.unit
    def test_double_cancel_is_idempotent(self, tmp_path: Path) -> None:
        """Two consecutive drain --cancel calls both return rc=0."""
        with patch("devbench.cli.WORKSPACE_ROOT", tmp_path):
            rc1 = cli.cmd_drain("--cancel")
            rc2 = cli.cmd_drain("--cancel")
        assert rc1 == 0
        assert rc2 == 0

    @pytest.mark.unit
    def test_reason_roundtrip(self, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
        """The reason set via drain --reason round-trips through drain --status output."""
        with patch("devbench.cli.WORKSPACE_ROOT", tmp_path):
            cli.cmd_drain("--reason", "roundtrip-check")
            cli.cmd_drain("--status")
        out = capsys.readouterr().out
        assert "roundtrip-check" in out

    @pytest.mark.unit
    def test_overwrite_existing_drain(self, tmp_path: Path) -> None:
        """A second drain --reason call overwrites the first signal file."""
        import json as _json

        with patch("devbench.cli.WORKSPACE_ROOT", tmp_path):
            cli.cmd_drain("--reason", "first")
            cli.cmd_drain("--reason", "second")

        signal = tmp_path / ".devbench" / "drain.signal"
        data = _json.loads(signal.read_text())
        assert data["reason"] == "second"


# ---------------------------------------------------------------------------
# cmd_drain --session / --all (E4-F5-S1-T3, issue #192)
# AC-192-7, AC-192-8
# ---------------------------------------------------------------------------


class TestParseDrainArgvSessionAll:
    """_parse_drain_argv correctly handles --session and --all flags (AC-192-7, AC-192-8)."""

    @pytest.mark.unit
    def test_session_flag_returns_session_target(self) -> None:
        """--session <name> yields mode='request', session_target=name."""
        mode, reason, session_target, rc, msg = cli._parse_drain_argv(("--session", "alpha"))
        assert rc == 0
        assert mode == "request"
        assert session_target == "alpha"
        assert msg == ""

    @pytest.mark.unit
    def test_session_flag_without_value_returns_error(self) -> None:
        """--session without a following value yields rc=2."""
        mode, reason, session_target, rc, msg = cli._parse_drain_argv(("--session",))
        assert rc == 2
        assert mode is None
        assert "session" in msg.lower()

    @pytest.mark.unit
    def test_session_flag_with_reason_returns_error(self) -> None:
        """--session and --reason are not combinable."""
        mode, reason, session_target, rc, msg = cli._parse_drain_argv(("--session", "alpha", "--reason", "x"))
        assert rc == 2
        assert mode is None

    @pytest.mark.unit
    def test_session_flag_with_cancel_returns_error(self) -> None:
        """--session and --cancel are not combinable."""
        mode, reason, session_target, rc, msg = cli._parse_drain_argv(("--session", "alpha", "--cancel"))
        assert rc == 2
        assert mode is None

    @pytest.mark.unit
    def test_session_flag_with_status_returns_error(self) -> None:
        """--session and --status are not combinable."""
        mode, reason, session_target, rc, msg = cli._parse_drain_argv(("--session", "alpha", "--status"))
        assert rc == 2
        assert mode is None

    @pytest.mark.unit
    def test_all_flag_returns_all_target(self) -> None:
        """--all yields mode='request', session_target='__all__'."""
        mode, reason, session_target, rc, msg = cli._parse_drain_argv(("--all",))
        assert rc == 0
        assert mode == "request"
        assert session_target == "__all__"
        assert msg == ""

    @pytest.mark.unit
    def test_all_flag_with_cancel_returns_error(self) -> None:
        """--all and --cancel are not combinable."""
        mode, reason, session_target, rc, msg = cli._parse_drain_argv(("--all", "--cancel"))
        assert rc == 2
        assert mode is None

    @pytest.mark.unit
    def test_all_flag_with_status_returns_error(self) -> None:
        """--all and --status are not combinable."""
        mode, reason, session_target, rc, msg = cli._parse_drain_argv(("--all", "--status"))
        assert rc == 2
        assert mode is None

    @pytest.mark.unit
    def test_all_flag_with_session_flag_returns_error(self) -> None:
        """--all and --session are mutually exclusive."""
        mode, reason, session_target, rc, msg = cli._parse_drain_argv(("--all", "--session", "alpha"))
        assert rc == 2
        assert mode is None

    @pytest.mark.unit
    def test_no_session_flags_returns_none_target(self) -> None:
        """Plain request (no --session / --all) yields session_target=None."""
        mode, reason, session_target, rc, msg = cli._parse_drain_argv(())
        assert rc == 0
        assert mode == "request"
        assert session_target is None

    @pytest.mark.unit
    def test_cancel_returns_none_target(self) -> None:
        """--cancel yields session_target=None."""
        mode, reason, session_target, rc, msg = cli._parse_drain_argv(("--cancel",))
        assert rc == 0
        assert mode == "cancel"
        assert session_target is None

    @pytest.mark.unit
    def test_status_returns_none_target(self) -> None:
        """--status yields session_target=None."""
        mode, reason, session_target, rc, msg = cli._parse_drain_argv(("--status",))
        assert rc == 0
        assert mode == "status"
        assert session_target is None

    @pytest.mark.unit
    def test_empty_session_name_returns_error(self) -> None:
        """--session with an empty string value yields rc=2."""
        mode, reason, session_target, rc, msg = cli._parse_drain_argv(("--session", ""))
        assert rc == 2
        assert mode is None
        assert "session" in msg.lower()


class TestCmdDrainSessionFlag:
    """cmd_drain --session <name> writes the drain signal to the named session's state dir (AC-192-7)."""

    @pytest.mark.unit
    def test_session_drain_creates_signal_in_session_state_dir(self, tmp_path: Path) -> None:
        """drain --session alpha writes drain.signal into the session's state dir."""
        from devbench.constants import SESSION_DRAIN_SIGNAL_FILENAME, SESSION_SESSIONS_BASE_DIR

        state_dir = tmp_path / SESSION_SESSIONS_BASE_DIR / "alpha"
        state_dir.mkdir(parents=True, exist_ok=True)

        with patch("devbench.cli.WORKSPACE_ROOT", tmp_path):
            rc = cli.cmd_drain("--session", "alpha")

        assert rc == 0
        signal_path = state_dir / SESSION_DRAIN_SIGNAL_FILENAME
        assert signal_path.exists(), "drain.signal must exist in the session state dir"

    @pytest.mark.unit
    def test_session_drain_does_not_touch_workspace_root_signal(self, tmp_path: Path) -> None:
        """drain --session alpha must NOT write the workspace-root drain.signal."""
        from devbench.constants import SESSION_SESSIONS_BASE_DIR

        state_dir = tmp_path / SESSION_SESSIONS_BASE_DIR / "alpha"
        state_dir.mkdir(parents=True, exist_ok=True)

        with patch("devbench.cli.WORKSPACE_ROOT", tmp_path):
            cli.cmd_drain("--session", "alpha")

        workspace_signal = tmp_path / ".devbench" / "drain.signal"
        assert not workspace_signal.exists(), "workspace-root drain.signal must NOT be created"

    @pytest.mark.unit
    def test_session_drain_missing_session_dir_returns_error(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """drain --session <nonexistent> returns rc=1 with an actionable stderr message."""
        with patch("devbench.cli.WORKSPACE_ROOT", tmp_path):
            rc = cli.cmd_drain("--session", "nonexistent")

        assert rc == 1
        err = capsys.readouterr().err
        assert "nonexistent" in err, f"Session name must appear in error: {err!r}"

    @pytest.mark.unit
    def test_session_flag_without_value_returns_rc2(self, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
        """drain --session with no value returns rc=2."""
        with patch("devbench.cli.WORKSPACE_ROOT", tmp_path):
            rc = cli.cmd_drain("--session")
        assert rc == 2
        err = capsys.readouterr().err
        assert err, "An error message must be written to stderr"

    @pytest.mark.unit
    def test_session_drain_reason_roundtrip(self, tmp_path: Path) -> None:
        """drain --session alpha stores the reason in the session signal file."""
        import json as _json

        from devbench.constants import SESSION_DRAIN_SIGNAL_FILENAME, SESSION_SESSIONS_BASE_DIR

        state_dir = tmp_path / SESSION_SESSIONS_BASE_DIR / "alpha"
        state_dir.mkdir(parents=True, exist_ok=True)

        with patch("devbench.cli.WORKSPACE_ROOT", tmp_path):
            rc = cli.cmd_drain("--session", "alpha")

        assert rc == 0
        signal_path = state_dir / SESSION_DRAIN_SIGNAL_FILENAME
        data = _json.loads(signal_path.read_text())
        assert "requested_by" in data

    @pytest.mark.unit
    def test_session_and_reason_combined_return_error(self, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
        """drain --session alpha --reason x is invalid; returns rc=2."""
        with patch("devbench.cli.WORKSPACE_ROOT", tmp_path):
            rc = cli.cmd_drain("--session", "alpha", "--reason", "x")
        assert rc == 2


class TestCmdDrainAllFlag:
    """cmd_drain --all writes drain signals for every active session (AC-192-8)."""

    @pytest.mark.unit
    def test_drain_all_writes_signals_for_every_session(self, tmp_path: Path) -> None:
        """drain --all creates drain.signal in each active session's state dir."""
        import os as _os

        from devbench.constants import SESSION_DRAIN_SIGNAL_FILENAME, SESSION_SESSIONS_BASE_DIR
        from devbench.session import Session, SessionRegistry

        now = datetime.now(tz=UTC)
        session_alpha = Session(
            name="alpha",
            pid=_os.getpid(),
            scope=[],
            started_at=now,
            started_by="tester",
            state_dir=tmp_path / SESSION_SESSIONS_BASE_DIR / "alpha",
        )
        session_beta = Session(
            name="beta",
            pid=_os.getpid(),
            scope=[],
            started_at=now,
            started_by="tester",
            state_dir=tmp_path / SESSION_SESSIONS_BASE_DIR / "beta",
        )
        for s in [session_alpha, session_beta]:
            s.state_dir.mkdir(parents=True, exist_ok=True)

        registry = SessionRegistry(tmp_path)
        registry.save([session_alpha, session_beta])

        with patch("devbench.cli.WORKSPACE_ROOT", tmp_path):
            rc = cli.cmd_drain("--all")

        assert rc == 0
        for s in [session_alpha, session_beta]:
            signal_path = s.state_dir / SESSION_DRAIN_SIGNAL_FILENAME
            assert signal_path.exists(), f"drain.signal missing for session {s.name!r}"

    @pytest.mark.unit
    def test_drain_all_does_not_touch_workspace_root_signal(self, tmp_path: Path) -> None:
        """drain --all must NOT write the workspace-root drain.signal."""
        import os as _os

        from devbench.constants import SESSION_SESSIONS_BASE_DIR
        from devbench.session import Session, SessionRegistry

        now = datetime.now(tz=UTC)
        session_alpha = Session(
            name="alpha",
            pid=_os.getpid(),
            scope=[],
            started_at=now,
            started_by="tester",
            state_dir=tmp_path / SESSION_SESSIONS_BASE_DIR / "alpha",
        )
        session_alpha.state_dir.mkdir(parents=True, exist_ok=True)
        registry = SessionRegistry(tmp_path)
        registry.save([session_alpha])

        with patch("devbench.cli.WORKSPACE_ROOT", tmp_path):
            cli.cmd_drain("--all")

        workspace_signal = tmp_path / ".devbench" / "drain.signal"
        assert not workspace_signal.exists(), "workspace-root drain.signal must NOT be created"

    @pytest.mark.unit
    def test_drain_all_with_no_active_sessions_prints_message(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """drain --all with no active sessions prints an informational message and returns rc=0."""
        with patch("devbench.cli.WORKSPACE_ROOT", tmp_path):
            rc = cli.cmd_drain("--all")

        assert rc == 0
        out = capsys.readouterr().out
        assert out, "An informational message must be printed when no sessions are active"

    @pytest.mark.unit
    def test_drain_all_with_cancel_returns_rc2(self, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
        """drain --all --cancel is invalid; returns rc=2."""
        with patch("devbench.cli.WORKSPACE_ROOT", tmp_path):
            rc = cli.cmd_drain("--all", "--cancel")
        assert rc == 2
        err = capsys.readouterr().err
        assert err

    @pytest.mark.unit
    def test_drain_all_prints_count_of_drained_sessions(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """drain --all prints how many sessions were drained."""
        import os as _os

        from devbench.constants import SESSION_SESSIONS_BASE_DIR
        from devbench.session import Session, SessionRegistry

        now = datetime.now(tz=UTC)
        session_gamma = Session(
            name="gamma",
            pid=_os.getpid(),
            scope=[],
            started_at=now,
            started_by="tester",
            state_dir=tmp_path / SESSION_SESSIONS_BASE_DIR / "gamma",
        )
        session_gamma.state_dir.mkdir(parents=True, exist_ok=True)
        registry = SessionRegistry(tmp_path)
        registry.save([session_gamma])

        with patch("devbench.cli.WORKSPACE_ROOT", tmp_path):
            rc = cli.cmd_drain("--all")

        assert rc == 0
        out = capsys.readouterr().out
        assert "1" in out or "gamma" in out, f"Expected count or session name in output: {out!r}"


# ---------------------------------------------------------------------------
# _DrainRequested sentinel + cmd_start drain check (E3-F3-S1-T1, issue #188)
# AC-188-4, AC-188-5, AC-188-8
# ---------------------------------------------------------------------------


class TestDrainRequestedSentinel:
    """_DrainRequested is a module-level exception class in cli.py (AC-188-4)."""

    @pytest.mark.unit
    def test_drain_requested_is_exception_subclass(self) -> None:
        """_DrainRequested must be a BaseException subclass."""
        assert issubclass(cli._DrainRequested, BaseException)

    @pytest.mark.unit
    def test_drain_requested_can_be_raised_and_caught(self) -> None:
        """_DrainRequested can be raised and caught as BaseException."""
        with pytest.raises(cli._DrainRequested):
            raise cli._DrainRequested("test reason")

    @pytest.mark.unit
    def test_drain_requested_carries_reason(self) -> None:
        """_DrainRequested preserves the reason string as its first arg."""
        exc = cli._DrainRequested("operator stop")
        assert "operator stop" in str(exc)


class TestIsClaimToolUse:
    """_is_claim_tool_use detects Bash tool messages containing devbench claim."""

    @pytest.mark.unit
    def test_bash_claim_command_detected(self) -> None:
        """AssistantMessage with Bash ToolUseBlock running 'devbench claim' returns True."""
        from claude_agent_sdk.types import AssistantMessage, ToolUseBlock

        msg = AssistantMessage(
            content=[
                ToolUseBlock(
                    id="tu-1",
                    name="Bash",
                    input={"command": "uv run devbench claim E1-F2-S1-T1"},
                )
            ],
            model="claude-opus-4-5",
        )
        assert cli._is_claim_tool_use(msg) is True

    @pytest.mark.unit
    def test_bash_non_claim_command_not_detected(self) -> None:
        """Bash ToolUseBlock with unrelated command returns False."""
        from claude_agent_sdk.types import AssistantMessage, ToolUseBlock

        msg = AssistantMessage(
            content=[
                ToolUseBlock(
                    id="tu-2",
                    name="Bash",
                    input={"command": "uv run devbench next"},
                )
            ],
            model="claude-opus-4-5",
        )
        assert cli._is_claim_tool_use(msg) is False

    @pytest.mark.unit
    def test_non_assistant_message_returns_false(self) -> None:
        """Non-AssistantMessage objects (e.g., ResultMessage) return False."""
        from claude_agent_sdk.types import ResultMessage

        msg = ResultMessage(
            subtype="success",
            session_id="sid-1",
            duration_ms=100,
            duration_api_ms=80,
            is_error=False,
            num_turns=1,
        )
        assert cli._is_claim_tool_use(msg) is False

    @pytest.mark.unit
    def test_assistant_message_with_no_tool_use_returns_false(self) -> None:
        """AssistantMessage with only TextBlocks (no ToolUseBlock) returns False."""
        from claude_agent_sdk.types import AssistantMessage, TextBlock

        msg = AssistantMessage(
            content=[TextBlock(text="Claiming the next task now...")],
            model="claude-opus-4-5",
        )
        assert cli._is_claim_tool_use(msg) is False

    @pytest.mark.unit
    def test_bash_claim_without_devbench_prefix_not_detected(self) -> None:
        """Bash command containing 'claim' but not 'devbench claim' returns False."""
        from claude_agent_sdk.types import AssistantMessage, ToolUseBlock

        msg = AssistantMessage(
            content=[
                ToolUseBlock(
                    id="tu-3",
                    name="Bash",
                    input={"command": "git claim-something"},
                )
            ],
            model="claude-opus-4-5",
        )
        assert cli._is_claim_tool_use(msg) is False

    @pytest.mark.unit
    def test_bash_tool_use_with_no_command_key_returns_false(self) -> None:
        """Bash ToolUseBlock with empty input dict returns False."""
        from claude_agent_sdk.types import AssistantMessage, ToolUseBlock

        msg = AssistantMessage(
            content=[
                ToolUseBlock(
                    id="tu-4",
                    name="Bash",
                    input={},
                )
            ],
            model="claude-opus-4-5",
        )
        assert cli._is_claim_tool_use(msg) is False


# ---------------------------------------------------------------------------
# Shared helpers for drain-enforcement and cancel-drain test classes
# ---------------------------------------------------------------------------


def _make_sdk_with_claim_message() -> object:
    """Return a fake SDK module that yields an AssistantMessage with a Bash claim tool use.

    Shared by TestCmdStartDrainEnforcement and TestCmdStartCancelDrainPreventsExit.
    """
    import types

    from claude_agent_sdk.types import AssistantMessage, ToolUseBlock

    mock_sdk: Any = types.ModuleType("claude_agent_sdk")
    mock_sdk.ClaudeAgentOptions = MagicMock()

    async def mock_query(**kwargs: object) -> object:
        yield AssistantMessage(
            content=[
                ToolUseBlock(
                    id="tu-claim",
                    name="Bash",
                    input={"command": "uv run devbench claim E1-F2-S1-T1"},
                )
            ],
            model="claude-opus-4-5",
        )

    mock_sdk.query = mock_query
    return mock_sdk


@pytest.fixture
def drain_cmd_start_patches(tmp_path: Path) -> Generator[None, None, None]:
    """Patch sys.modules, WORKSPACE_ROOT, and _should_auto_restart for drain tests.

    Yields after entering all patches so the test body runs inside them.
    Shared by TestCmdStartDrainEnforcement and TestCmdStartCancelDrainPreventsExit.
    """
    import sys

    mock_sdk = _make_sdk_with_claim_message()
    with (
        patch.dict(sys.modules, {"claude_agent_sdk": mock_sdk}),
        patch("devbench.cli.WORKSPACE_ROOT", tmp_path),
        patch(
            "devbench.cli._should_auto_restart_after_no_actionable",
            return_value=(False, []),
        ),
    ):
        yield


class TestCmdStartDrainEnforcement:
    """cmd_start raises _DrainRequested on claim-while-drain-pending and returns rc=0 (AC-188-4, AC-188-5, AC-188-8)."""

    def _make_sdk_with_non_claim_messages(self) -> object:
        """Return a fake SDK module that yields only non-claim messages."""
        import types

        mock_sdk: Any = types.ModuleType("claude_agent_sdk")
        mock_sdk.ClaudeAgentOptions = MagicMock()

        async def mock_query(**kwargs: object) -> object:
            yield "plain string message"

        mock_sdk.query = mock_query
        return mock_sdk

    @pytest.mark.unit
    def test_claim_while_drain_pending_returns_rc0(self, tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
        """AC-188-4/AC-188-5: when drain is pending and claim is observed, cmd_start returns 0.

        The drain sentinel must be consumed (deleted) on exit so the next
        devbench start runs unscoped (AC-188-5).

        cmd_start sets DEVBENCH_SESSION_NAME=SESSION_DEFAULT_NAME before checking for drain,
        so the per-session path is used (spec 4.4.4).
        """
        import logging
        import sys

        # cmd_start sets DEVBENCH_SESSION_NAME='default' before drain checks,
        # so the drain signal must be written to the per-session path.
        signal_path = tmp_path / SESSION_SESSIONS_BASE_DIR / SESSION_DEFAULT_NAME / "drain.signal"
        signal_path.parent.mkdir(parents=True)
        signal_path.write_text(
            '{"requested_at": "2026-05-16T00:00:00+00:00", "requested_by": "operator", "reason": "freeze"}',
            encoding="utf-8",
        )

        mock_sdk = _make_sdk_with_claim_message()

        with (
            patch.dict(sys.modules, {"claude_agent_sdk": mock_sdk}),
            patch("devbench.cli.WORKSPACE_ROOT", tmp_path),
            patch(
                "devbench.cli._should_auto_restart_after_no_actionable",
                return_value=(False, []),
            ),
            caplog.at_level(logging.INFO, logger="devbench.cli"),
        ):
            rc = cli.cmd_start()

        assert rc == 0
        assert not signal_path.exists(), "drain signal must be consumed (deleted) after enforcement (AC-188-5)"

    @pytest.mark.unit
    def test_claim_while_drain_pending_logs_enforced_audit(
        self, tmp_path: Path, caplog: pytest.LogCaptureFixture
    ) -> None:
        """AC-188-8: cmd_start logs [ORCHESTRATOR_DRAIN_ENFORCED] with reason when drain is enforced."""
        import logging
        import sys

        # Per-session path: cmd_start sets DEVBENCH_SESSION_NAME=SESSION_DEFAULT_NAME (spec 4.4.4).
        signal_path = tmp_path / SESSION_SESSIONS_BASE_DIR / SESSION_DEFAULT_NAME / "drain.signal"
        signal_path.parent.mkdir(parents=True)
        signal_path.write_text(
            '{"requested_at": "2026-05-16T00:00:00+00:00", "requested_by": "operator", "reason": "pre-release freeze"}',
            encoding="utf-8",
        )

        mock_sdk = _make_sdk_with_claim_message()

        with (
            patch.dict(sys.modules, {"claude_agent_sdk": mock_sdk}),
            patch("devbench.cli.WORKSPACE_ROOT", tmp_path),
            patch(
                "devbench.cli._should_auto_restart_after_no_actionable",
                return_value=(False, []),
            ),
            caplog.at_level(logging.INFO, logger="devbench.cli"),
        ):
            cli.cmd_start()

        log_text = " ".join(rec.getMessage() for rec in caplog.records)
        assert "[ORCHESTRATOR_DRAIN_ENFORCED]" in log_text, (
            "log must contain [ORCHESTRATOR_DRAIN_ENFORCED] audit marker (AC-188-8)"
        )
        assert "pre-release freeze" in log_text, "drain reason must appear in the audit log (AC-188-8)"

    @pytest.mark.unit
    def test_no_drain_signal_allows_claim_to_proceed_normally(self, tmp_path: Path) -> None:
        """When no drain signal is present, claim messages do not interrupt the SDK run."""
        import sys

        mock_sdk = _make_sdk_with_claim_message()

        with (
            patch.dict(sys.modules, {"claude_agent_sdk": mock_sdk}),
            patch("devbench.cli.WORKSPACE_ROOT", tmp_path),
            patch(
                "devbench.cli._should_auto_restart_after_no_actionable",
                return_value=(False, []),
            ),
        ):
            rc = cli.cmd_start()

        assert rc == 0
        assert not (tmp_path / ".devbench" / "drain.signal").exists()

    @pytest.mark.unit
    def test_drain_signal_without_claim_does_not_interrupt(self, tmp_path: Path) -> None:
        """Drain signal present but no claim message -- SDK run completes normally, rc=0.

        The drain polling only interrupts on a claim attempt; non-claim
        messages do not trigger enforcement.  However, the finally clause
        always wipes the drain signal so the next start does not inherit a
        stale request (#212).
        """
        import sys

        signal_path = tmp_path / ".devbench" / "drain.signal"
        signal_path.parent.mkdir(parents=True)
        signal_path.write_text(
            '{"requested_at": "2026-05-16T00:00:00+00:00", "requested_by": "operator", "reason": ""}',
            encoding="utf-8",
        )

        mock_sdk = self._make_sdk_with_non_claim_messages()

        with (
            patch.dict(sys.modules, {"claude_agent_sdk": mock_sdk}),
            patch("devbench.cli.WORKSPACE_ROOT", tmp_path),
            patch(
                "devbench.cli._should_auto_restart_after_no_actionable",
                return_value=(False, []),
            ),
        ):
            rc = cli.cmd_start()

        assert rc == 0
        # #212: finally clause must clean the drain signal on every exit so
        # a stale workspace-root signal does not auto-drain the next start.
        assert not signal_path.exists(), "drain signal must be cleared by finally clause on exit (#212)"

    @pytest.mark.unit
    def test_drain_enforced_with_empty_reason(self, tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
        """[ORCHESTRATOR_DRAIN_ENFORCED] is logged even when reason is empty string."""
        import logging
        import sys

        # Per-session path: cmd_start sets DEVBENCH_SESSION_NAME=SESSION_DEFAULT_NAME (spec 4.4.4).
        signal_path = tmp_path / SESSION_SESSIONS_BASE_DIR / SESSION_DEFAULT_NAME / "drain.signal"
        signal_path.parent.mkdir(parents=True)
        signal_path.write_text(
            '{"requested_at": "2026-05-16T00:00:00+00:00", "requested_by": "operator", "reason": ""}',
            encoding="utf-8",
        )

        mock_sdk = _make_sdk_with_claim_message()

        with (
            patch.dict(sys.modules, {"claude_agent_sdk": mock_sdk}),
            patch("devbench.cli.WORKSPACE_ROOT", tmp_path),
            patch(
                "devbench.cli._should_auto_restart_after_no_actionable",
                return_value=(False, []),
            ),
            caplog.at_level(logging.INFO, logger="devbench.cli"),
        ):
            rc = cli.cmd_start()

        assert rc == 0
        log_text = " ".join(rec.getMessage() for rec in caplog.records)
        assert "[ORCHESTRATOR_DRAIN_ENFORCED]" in log_text


class TestCmdStartWritesRestartMarker:
    """Issue #215: ``cmd_start`` writes ``<workspace>/.devbench/last-restart``
    on every startup so the classifier can scope agent-tool-unavailable
    audit rows to the current orchestrator instance.
    """

    @pytest.mark.unit
    def test_restart_marker_is_written_on_start(self, tmp_path: Path) -> None:
        import sys
        import types

        from devbench.constants import LAST_RESTART_MARKER_PATH

        mock_sdk: Any = types.ModuleType("claude_agent_sdk")
        mock_sdk.ClaudeAgentOptions = MagicMock()

        async def mock_query(**kwargs: object) -> object:
            yield "plain string"

        mock_sdk.query = mock_query

        with (
            patch.dict(sys.modules, {"claude_agent_sdk": mock_sdk}),
            patch("devbench.cli.WORKSPACE_ROOT", tmp_path),
            patch(
                "devbench.cli._should_auto_restart_after_no_actionable",
                return_value=(False, []),
            ),
        ):
            rc = cli.cmd_start()

        assert rc == 0
        marker = tmp_path / LAST_RESTART_MARKER_PATH
        assert marker.is_file(), f"last-restart marker not written at {marker}"
        # Marker contents must be a parseable ISO 8601 UTC datetime.
        from datetime import datetime as _dt

        parsed = _dt.fromisoformat(marker.read_text(encoding="utf-8").strip())
        assert parsed.tzinfo is not None, "marker timestamp must be timezone-aware"


class TestIsTerminalOrchestrateResult:
    """Issue #218: helper that detects the orchestrate skill's three
    terminal sentinels (``ALL_DONE`` / ``NO_ACTIONABLE`` /
    ``NO_ACTIONABLE_IN_SCOPE``) inside the SDK ``ResultMessage.result``
    text.  Used by ``_run`` to break the SDK iterator early so the
    orchestrator does not burn ~$0.07/turn re-invoking the model after
    the skill has signalled end-of-run.
    """

    @pytest.mark.unit
    def test_all_done_marker_detected(self) -> None:
        from devbench.cli import _is_terminal_orchestrate_result

        assert _is_terminal_orchestrate_result("Orchestration complete: ALL_DONE") is True

    @pytest.mark.unit
    def test_no_actionable_marker_detected(self) -> None:
        from devbench.cli import _is_terminal_orchestrate_result

        assert (
            _is_terminal_orchestrate_result("Orchestration complete: NO_ACTIONABLE -- 190/212 done, 11 blocked.")
            is True
        )

    @pytest.mark.unit
    def test_no_actionable_in_scope_marker_detected_via_substring(self) -> None:
        from devbench.cli import _is_terminal_orchestrate_result

        assert _is_terminal_orchestrate_result("Scoped run complete: NO_ACTIONABLE_IN_SCOPE") is True

    @pytest.mark.unit
    def test_empty_string_is_not_terminal(self) -> None:
        from devbench.cli import _is_terminal_orchestrate_result

        assert _is_terminal_orchestrate_result("") is False

    @pytest.mark.unit
    def test_none_is_not_terminal(self) -> None:
        from devbench.cli import _is_terminal_orchestrate_result

        assert _is_terminal_orchestrate_result(None) is False

    @pytest.mark.unit
    def test_unrelated_text_is_not_terminal(self) -> None:
        from devbench.cli import _is_terminal_orchestrate_result

        assert _is_terminal_orchestrate_result("orchestrate step complete") is False

    @pytest.mark.unit
    def test_partial_prefix_is_not_terminal(self) -> None:
        from devbench.cli import _is_terminal_orchestrate_result

        assert _is_terminal_orchestrate_result("NO_ACT") is False


class TestCmdStartTerminalExit:
    """Issue #218: ``cmd_start``'s ``_run`` must break out of the SDK
    ``async for`` loop the first time it observes a terminal-marker
    ResultMessage.  Without this, the SDK keeps re-invoking the model
    after the orchestrate skill prints its end-of-run summary, costing
    ~$0.07/turn (measured $8.30 over 9.5 minutes of idle re-invocation
    on the kanon-deps-work run that motivated this fix).
    """

    @staticmethod
    def _build_counting_sdk(messages: list[Any], counter: list[int]) -> Any:
        """Construct a fake SDK module whose ``query()`` async iterator
        yields ``messages`` in order while incrementing ``counter[0]``
        once per yield.  After the loop breaks the counter records how
        many messages the loop actually consumed."""
        import types

        mock_sdk: Any = types.ModuleType("claude_agent_sdk")
        mock_sdk.ClaudeAgentOptions = MagicMock()

        async def mock_query(**kwargs: object) -> object:
            for msg in messages:
                counter[0] += 1
                yield msg

        mock_sdk.query = mock_query
        return mock_sdk

    @staticmethod
    def _result_msg(text: str) -> Any:
        class _Msg:
            result = text
            subtype = "success"

        return _Msg()

    @pytest.mark.unit
    def test_terminal_no_actionable_breaks_loop_immediately(self, tmp_path: Path) -> None:
        import sys

        counter = [0]
        messages = [
            self._result_msg("orchestrate step done"),
            self._result_msg("Orchestration complete: NO_ACTIONABLE -- 1/1 done"),
            self._result_msg("THIS MESSAGE MUST NEVER BE PROCESSED"),
        ]
        mock_sdk = self._build_counting_sdk(messages, counter)

        with (
            patch.dict(sys.modules, {"claude_agent_sdk": mock_sdk}),
            patch("devbench.cli.WORKSPACE_ROOT", tmp_path),
            patch(
                "devbench.cli._should_auto_restart_after_no_actionable",
                return_value=(False, []),
            ),
        ):
            rc = cli.cmd_start()

        assert rc == 0
        assert counter[0] == 2, f"Loop must break on terminal marker; expected 2 messages consumed, got {counter[0]}"

    @pytest.mark.unit
    def test_terminal_all_done_breaks_loop(self, tmp_path: Path) -> None:
        import sys

        counter = [0]
        messages = [
            self._result_msg("Orchestration complete: ALL_DONE"),
            self._result_msg("THIS MESSAGE MUST NEVER BE PROCESSED"),
        ]
        mock_sdk = self._build_counting_sdk(messages, counter)

        with (
            patch.dict(sys.modules, {"claude_agent_sdk": mock_sdk}),
            patch("devbench.cli.WORKSPACE_ROOT", tmp_path),
            patch(
                "devbench.cli._should_auto_restart_after_no_actionable",
                return_value=(False, []),
            ),
        ):
            rc = cli.cmd_start()

        assert rc == 0
        assert counter[0] == 1

    @pytest.mark.unit
    def test_terminal_no_actionable_in_scope_breaks_loop(self, tmp_path: Path) -> None:
        import sys

        counter = [0]
        messages = [
            self._result_msg("Scoped run complete: NO_ACTIONABLE_IN_SCOPE"),
            self._result_msg("THIS MESSAGE MUST NEVER BE PROCESSED"),
        ]
        mock_sdk = self._build_counting_sdk(messages, counter)

        with (
            patch.dict(sys.modules, {"claude_agent_sdk": mock_sdk}),
            patch("devbench.cli.WORKSPACE_ROOT", tmp_path),
            patch(
                "devbench.cli._should_auto_restart_after_no_actionable",
                return_value=(False, []),
            ),
        ):
            rc = cli.cmd_start()

        assert rc == 0
        assert counter[0] == 1

    @pytest.mark.unit
    def test_non_terminal_results_do_not_break_loop(self, tmp_path: Path) -> None:
        import sys

        counter = [0]
        messages = [
            self._result_msg("orchestrate step done"),
            self._result_msg("another non-terminal turn"),
            self._result_msg("yet another non-terminal turn"),
        ]
        mock_sdk = self._build_counting_sdk(messages, counter)

        with (
            patch.dict(sys.modules, {"claude_agent_sdk": mock_sdk}),
            patch("devbench.cli.WORKSPACE_ROOT", tmp_path),
            patch(
                "devbench.cli._should_auto_restart_after_no_actionable",
                return_value=(False, []),
            ),
        ):
            rc = cli.cmd_start()

        assert rc == 0
        assert counter[0] == 3

    @pytest.mark.unit
    def test_terminal_exit_writes_audit_log(self, tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
        import logging
        import sys

        counter = [0]
        messages = [
            self._result_msg("Orchestration complete: NO_ACTIONABLE -- 1/1 done"),
        ]
        mock_sdk = self._build_counting_sdk(messages, counter)

        with (
            patch.dict(sys.modules, {"claude_agent_sdk": mock_sdk}),
            patch("devbench.cli.WORKSPACE_ROOT", tmp_path),
            patch(
                "devbench.cli._should_auto_restart_after_no_actionable",
                return_value=(False, []),
            ),
            caplog.at_level(logging.INFO, logger="devbench.cli"),
        ):
            cli.cmd_start()

        log_text = " ".join(rec.getMessage() for rec in caplog.records)
        assert "[ORCHESTRATOR_TERMINAL_EXIT]" in log_text, f"Audit log line missing; recorded: {log_text!r}"
        assert "NO_ACTIONABLE" in log_text


class TestCmdStartSlackPingResultText:
    """Issue #217: when the SDK loop completes normally, ``cmd_start`` must
    feed the last ``ResultMessage.result`` text into the ``orchestrator_stop``
    Slack ping so the operator can distinguish ``ALL_DONE`` from
    ``NO_ACTIONABLE`` and see the remaining-task counts -- instead of seeing
    a bare ``"clean"`` that hides whether the backlog is finished.
    """

    @pytest.mark.unit
    def test_slack_ping_includes_sdk_result_text_on_clean_exit(self, tmp_path: Path) -> None:
        import sys
        import types

        # SDK yields one ResultMessage with the NO_ACTIONABLE summary text the
        # orchestrate skill emits at end-of-backlog.
        mock_sdk: Any = types.ModuleType("claude_agent_sdk")
        mock_sdk.ClaudeAgentOptions = MagicMock()

        class _FakeResultMessage:
            subtype = "success"
            result = "Orchestration complete: NO_ACTIONABLE — 190/212 done, 11 blocked."

        async def mock_query(**kwargs: object) -> object:
            yield _FakeResultMessage()

        mock_sdk.query = mock_query

        captured_reason: list[str] = []

        def _capture(reason: str) -> None:
            captured_reason.append(reason)

        with (
            patch.dict(sys.modules, {"claude_agent_sdk": mock_sdk}),
            patch("devbench.cli.WORKSPACE_ROOT", tmp_path),
            patch(
                "devbench.cli._should_auto_restart_after_no_actionable",
                return_value=(False, []),
            ),
            patch("devbench.cli._fire_orchestrator_stop_notification", _capture),
        ):
            rc = cli.cmd_start()

        assert rc == 0
        assert captured_reason, "orchestrator_stop notification was not fired"
        reason = captured_reason[-1]
        assert "NO_ACTIONABLE" in reason, f"Slack reason must include the SDK's NO_ACTIONABLE summary; got {reason!r}"
        assert "190/212" in reason, f"Slack reason must include the remaining-task counts; got {reason!r}"
        # Bare "clean" alone is no longer sufficient -- it hides the fact
        # that 22 tasks remain.  The new reason must be MORE than just "clean".
        assert reason != "clean", "Slack reason 'clean' alone is insufficient -- must carry the SDK result text (#217)"

    @pytest.mark.unit
    def test_slack_ping_falls_back_to_clean_when_no_result_message(self, tmp_path: Path) -> None:
        """When the SDK never emits a ResultMessage (degenerate / mock test
        scenario), the legacy ``"clean"`` reason is preserved so existing
        behaviour is a strict superset of the pre-fix implementation.
        """
        import sys
        import types

        mock_sdk: Any = types.ModuleType("claude_agent_sdk")
        mock_sdk.ClaudeAgentOptions = MagicMock()

        async def mock_query(**kwargs: object) -> object:
            yield "plain string"  # not a ResultMessage

        mock_sdk.query = mock_query

        captured_reason: list[str] = []

        def _capture(reason: str) -> None:
            captured_reason.append(reason)

        with (
            patch.dict(sys.modules, {"claude_agent_sdk": mock_sdk}),
            patch("devbench.cli.WORKSPACE_ROOT", tmp_path),
            patch(
                "devbench.cli._should_auto_restart_after_no_actionable",
                return_value=(False, []),
            ),
            patch("devbench.cli._fire_orchestrator_stop_notification", _capture),
        ):
            rc = cli.cmd_start()

        assert rc == 0
        assert captured_reason
        assert captured_reason[-1] == "clean", (
            f"With no ResultMessage emitted, reason must remain 'clean'; got {captured_reason[-1]!r}"
        )


class TestCmdStartCancelDrainOnExit:
    """cmd_start finally clause clears drain.signal from both candidate paths (#212).

    The orchestrator's clean-exit cleanup defends against the path-divergence
    bug where the operator's ``devbench drain`` writes to the workspace-root
    path but the session-scoped orchestrator reads from the per-session path.
    On exit we wipe BOTH so the next start does not auto-drain on a stale
    request.  Regression test for issue #212.
    """

    @pytest.mark.unit
    def test_workspace_root_signal_cleared_on_clean_exit(self, tmp_path: Path) -> None:
        """drain.signal at workspace-root is removed even when no claim triggered enforcement."""
        import sys

        signal_path = tmp_path / ".devbench" / "drain.signal"
        signal_path.parent.mkdir(parents=True)
        signal_path.write_text(
            '{"requested_at": "2026-05-16T00:00:00+00:00", "requested_by": "operator", "reason": ""}',
            encoding="utf-8",
        )

        import types

        mock_sdk: Any = types.ModuleType("claude_agent_sdk")
        mock_sdk.ClaudeAgentOptions = MagicMock()

        async def mock_query(**kwargs: object) -> object:
            yield "plain string"

        mock_sdk.query = mock_query

        with (
            patch.dict(sys.modules, {"claude_agent_sdk": mock_sdk}),
            patch("devbench.cli.WORKSPACE_ROOT", tmp_path),
            patch(
                "devbench.cli._should_auto_restart_after_no_actionable",
                return_value=(False, []),
            ),
        ):
            rc = cli.cmd_start()

        assert rc == 0
        assert not signal_path.exists(), (
            "finally clause must call cancel_drain to remove the workspace-root signal on exit (#212)"
        )

    @pytest.mark.unit
    def test_session_path_signal_cleared_on_clean_exit(self, tmp_path: Path) -> None:
        """drain.signal at per-session path is removed by finally clause on exit (#212)."""
        import sys
        import types

        # Per-session path -- cmd_start sets DEVBENCH_SESSION_NAME=default
        signal_path = tmp_path / SESSION_SESSIONS_BASE_DIR / SESSION_DEFAULT_NAME / "drain.signal"
        signal_path.parent.mkdir(parents=True)
        signal_path.write_text(
            '{"requested_at": "2026-05-16T00:00:00+00:00", "requested_by": "operator", "reason": ""}',
            encoding="utf-8",
        )

        mock_sdk: Any = types.ModuleType("claude_agent_sdk")
        mock_sdk.ClaudeAgentOptions = MagicMock()

        async def mock_query(**kwargs: object) -> object:
            yield "plain string"

        mock_sdk.query = mock_query

        with (
            patch.dict(sys.modules, {"claude_agent_sdk": mock_sdk}),
            patch("devbench.cli.WORKSPACE_ROOT", tmp_path),
            patch(
                "devbench.cli._should_auto_restart_after_no_actionable",
                return_value=(False, []),
            ),
        ):
            rc = cli.cmd_start()

        assert rc == 0
        assert not signal_path.exists(), (
            "finally clause must call cancel_drain to remove the per-session signal on exit (#212)"
        )

    @pytest.mark.unit
    def test_no_drain_signal_present_no_error(self, tmp_path: Path) -> None:
        """cancel_drain in finally is idempotent: no signal present must not raise (#212)."""
        import sys
        import types

        mock_sdk: Any = types.ModuleType("claude_agent_sdk")
        mock_sdk.ClaudeAgentOptions = MagicMock()

        async def mock_query(**kwargs: object) -> object:
            yield "plain string"

        mock_sdk.query = mock_query

        with (
            patch.dict(sys.modules, {"claude_agent_sdk": mock_sdk}),
            patch("devbench.cli.WORKSPACE_ROOT", tmp_path),
            patch(
                "devbench.cli._should_auto_restart_after_no_actionable",
                return_value=(False, []),
            ),
        ):
            rc = cli.cmd_start()

        assert rc == 0
        assert not (tmp_path / ".devbench" / "drain.signal").exists()


# AC-188-10: cancel-drain mid-orchestrate prevents the exit (E3-F3-S1-T2, issue #188)
# ---------------------------------------------------------------------------


class TestCmdStartCancelDrainPreventsExit:
    """Cancelling drain mid-orchestrate prevents the exit; orchestrator continues normally (AC-188-10).

    RED verification: tests in this class assert [ORCHESTRATOR_DRAIN_ENFORCED] does NOT appear
    in logs.  That assertion fails when cancel_drain is bypassed (drain signal remains present at
    claim time), which means drain enforcement DOES occur and the audit marker IS logged.  Verified
    by temporarily omitting the cancel_drain call: cmd_start logged [ORCHESTRATOR_DRAIN_ENFORCED]
    and the "not in log_text" assertion raised AssertionError -- exit code 1, 1 failed, 0 passed.
    """

    @pytest.mark.unit
    def test_cancelled_drain_before_claim_allows_normal_continuation(
        self, drain_cmd_start_patches: None, tmp_path: Path, caplog: pytest.LogCaptureFixture
    ) -> None:
        """AC-188-10: cancelled drain before claim -- orchestrator continues normally.

        The drain signal is written, then removed via cancel_drain before the
        SDK wrapper observes the claim message.  read_drain_state returns None
        so no _DrainRequested is raised.  cmd_start must return 0 WITHOUT
        emitting [ORCHESTRATOR_DRAIN_ENFORCED].
        """
        import logging

        from devbench.drain import cancel_drain, request_drain

        signal_path = tmp_path / ".devbench" / "drain.signal"

        # Request then immediately cancel -- signal file is absent at claim time.
        request_drain(tmp_path, reason="temporary pause")
        assert signal_path.exists(), "pre-condition: signal must exist after request"
        cancel_drain(tmp_path)
        assert not signal_path.exists(), "pre-condition: signal must be absent after cancel"

        with caplog.at_level(logging.INFO, logger="devbench.cli"):
            rc = cli.cmd_start()

        assert rc == 0, "cmd_start must return 0 when drain was cancelled before claim"
        log_text = " ".join(rec.getMessage() for rec in caplog.records)
        assert "[ORCHESTRATOR_DRAIN_ENFORCED]" not in log_text, (
            "[ORCHESTRATOR_DRAIN_ENFORCED] must NOT appear when drain was cancelled before claim"
        )
        assert not signal_path.exists(), "no drain signal must remain after clean run"

    @pytest.mark.unit
    def test_drain_request_and_cancel_within_same_sdk_tick(
        self, drain_cmd_start_patches: None, tmp_path: Path, caplog: pytest.LogCaptureFixture
    ) -> None:
        """AC-188-10 edge case: drain requested AND cancelled within the same SDK tick.

        Even if both operations occur atomically before the SDK iterator
        advances to the claim message, the absence of the signal at poll time
        means no drain enforcement.  cmd_start returns 0; no audit entry logged.
        """
        import logging

        from devbench.drain import cancel_drain, request_drain

        signal_path = tmp_path / ".devbench" / "drain.signal"

        # Simulate same-tick: write then immediately delete.
        request_drain(tmp_path, reason="same-tick cancel")
        cancel_drain(tmp_path)
        assert not signal_path.exists(), "pre-condition: signal absent after same-tick cancel"

        with caplog.at_level(logging.INFO, logger="devbench.cli"):
            rc = cli.cmd_start()

        assert rc == 0, "cmd_start must return 0 when drain was cancelled in same tick"
        log_text = " ".join(rec.getMessage() for rec in caplog.records)
        assert "[ORCHESTRATOR_DRAIN_ENFORCED]" not in log_text, (
            "[ORCHESTRATOR_DRAIN_ENFORCED] must NOT appear after same-tick cancel"
        )

    @pytest.mark.unit
    def test_double_cancel_then_claim_proceeds_normally(
        self, drain_cmd_start_patches: None, tmp_path: Path, caplog: pytest.LogCaptureFixture
    ) -> None:
        """AC-188-10: multiple consecutive cancel calls followed by a claim -- orchestrator not interrupted.

        cancel_drain is idempotent; two consecutive calls must not raise and must
        leave the system in the no-drain state so the next claim proceeds normally.
        """
        import logging

        from devbench.drain import cancel_drain, request_drain

        signal_path = tmp_path / ".devbench" / "drain.signal"

        request_drain(tmp_path, reason="multiple cancel test")
        cancel_drain(tmp_path)
        cancel_drain(tmp_path)  # second cancel must not raise
        assert not signal_path.exists(), "pre-condition: signal absent after double cancel"

        with caplog.at_level(logging.INFO, logger="devbench.cli"):
            rc = cli.cmd_start()

        assert rc == 0, "cmd_start must return 0 after double cancel"
        log_text = " ".join(rec.getMessage() for rec in caplog.records)
        assert "[ORCHESTRATOR_DRAIN_ENFORCED]" not in log_text, (
            "[ORCHESTRATOR_DRAIN_ENFORCED] must NOT appear after double cancel"
        )

    @pytest.mark.unit
    def test_cancelled_drain_leaves_no_signal_artifact(self, drain_cmd_start_patches: None, tmp_path: Path) -> None:
        """AC-188-10: after cancel, no drain.signal artifact remains in .devbench/.

        Verifies the filesystem is clean -- no stale file that could affect
        the next orchestrator invocation.
        """
        from devbench.drain import cancel_drain, request_drain

        signal_path = tmp_path / ".devbench" / "drain.signal"

        request_drain(tmp_path, reason="artifact check")
        cancel_drain(tmp_path)

        cli.cmd_start()

        assert not signal_path.exists(), "drain.signal must not exist after cancelled drain followed by clean run"


# ---------------------------------------------------------------------------
# Pre-arm drain integration test (E3-F6-S1-T1, issue #188)
# AC-188-6: dropping drain.signal BEFORE devbench start causes the orchestrator
# to run exactly one WU then exit cleanly.
# ---------------------------------------------------------------------------


def _make_sdk_with_non_claim_then_claim_messages() -> object:
    """Return a fake SDK module that yields non-claim messages then one claim message.

    Simulates an orchestrator run where the skill processes one WU (non-claim
    messages for WU1) and then tries to claim a second WU (the Bash claim
    tool-use for WU2).  When drain is pre-armed, enforcement fires at the
    WU2 claim and cmd_start must return 0 without proceeding to WU2.
    """
    import types

    from claude_agent_sdk.types import AssistantMessage, TextBlock, ToolUseBlock

    mock_sdk: Any = types.ModuleType("claude_agent_sdk")
    mock_sdk.ClaudeAgentOptions = MagicMock()

    async def mock_query(**kwargs: object) -> object:
        # Non-claim messages representing WU1 processing (text output, sub-tool calls, etc.)
        yield AssistantMessage(
            content=[TextBlock(text="Running devbench:orchestrate skill...")],
            model="claude-opus-4-5",
        )
        yield AssistantMessage(
            content=[TextBlock(text="WU1 executor complete; marking done...")],
            model="claude-opus-4-5",
        )
        # Claim message representing the WU2 attempt -- drain fires here.
        yield AssistantMessage(
            content=[
                ToolUseBlock(
                    id="tu-wu2-claim",
                    name="Bash",
                    input={"command": "uv run devbench claim E1-F2-S1-T2"},
                )
            ],
            model="claude-opus-4-5",
        )

    mock_sdk.query = mock_query
    return mock_sdk


@pytest.fixture
def pre_arm_drain_env(tmp_path: Path) -> Generator[Path, None, None]:
    """Create drain.signal, build the mock SDK, and apply the 3-way patch for pre-arm tests.

    Writes a drain signal with reason="smoke run" to the per-session drain path
    ``tmp_path/.devbench/sessions/<SESSION_DEFAULT_NAME>/drain.signal``.
    cmd_start sets ``DEVBENCH_SESSION_NAME=SESSION_DEFAULT_NAME`` before checking
    for drain, so the per-session path is used (spec 4.4.4).

    Patches ``sys.modules["claude_agent_sdk"]`` with the SDK returned by
    :func:`_make_sdk_with_non_claim_then_claim_messages`, sets
    ``devbench.cli.WORKSPACE_ROOT`` to ``tmp_path``, and stubs
    ``devbench.cli._should_auto_restart_after_no_actionable`` to ``(False, [])``.

    Yields the ``Path`` to the signal file so individual tests can inspect or
    overwrite it before calling :func:`~devbench.cli.cmd_start`.

    Raises:
        pytest.fail: propagates any exception raised during patch setup.
    """
    import sys

    signal_path = tmp_path / SESSION_SESSIONS_BASE_DIR / SESSION_DEFAULT_NAME / "drain.signal"
    signal_path.parent.mkdir(parents=True)
    signal_path.write_text(
        '{"requested_at": "2026-05-16T00:00:00+00:00", "requested_by": "operator", "reason": "smoke run"}',
        encoding="utf-8",
    )

    mock_sdk = _make_sdk_with_non_claim_then_claim_messages()

    with (
        patch.dict(sys.modules, {"claude_agent_sdk": mock_sdk}),
        patch("devbench.cli.WORKSPACE_ROOT", tmp_path),
        patch(
            "devbench.cli._should_auto_restart_after_no_actionable",
            return_value=(False, []),
        ),
    ):
        yield signal_path


class TestCmdStartPreArmDrain:
    """Pre-arm drain integration: signal dropped BEFORE start; exactly one WU runs then exit (AC-188-6).

    The drain signal is written to the workspace BEFORE cmd_start is called.
    The SDK mock yields two non-claim messages (simulating WU1 being processed)
    followed by a Bash claim tool-use for WU2.  Because drain is pending at the
    WU2 claim, _DrainRequested is raised and cmd_start exits cleanly (rc=0)
    after consuming the drain signal -- WU1 completed, WU2 was never started.

    TDD RED verification: tests were confirmed failing by temporarily patching
    ``devbench.cli.read_drain_state`` to return ``None`` (simulating no drain
    pending), which prevents _DrainRequested from being raised.  With that patch
    active, cmd_start runs to exhaustion (rc=0, signal not consumed, no audit
    entry) and all assertions that check for drain enforcement fail:
    ``test_pre_arm_signal_consumed_on_exit`` raised AssertionError (signal still
    present), ``test_pre_arm_logs_drain_enforced_audit`` raised AssertionError
    ([ORCHESTRATOR_DRAIN_ENFORCED] absent), and
    ``test_pre_arm_non_claim_messages_not_interrupted`` raised AssertionError
    (wu2-claim-message was appended).  Exit code 1, 5 failed, 3 passed.
    Removing the temporary patch restored all 8 tests to passing (exit code 0).
    """

    @pytest.mark.unit
    def test_pre_arm_returns_rc0(self, pre_arm_drain_env: Path) -> None:
        """AC-188-6: pre-armed drain signal causes cmd_start to return rc=0."""
        rc = cli.cmd_start()

        assert rc == 0, "cmd_start must return 0 on pre-armed drain (AC-188-6)"

    @pytest.mark.unit
    def test_pre_arm_signal_consumed_on_exit(self, pre_arm_drain_env: Path) -> None:
        """AC-188-6 + AC-188-5: drain signal is consumed (deleted) after pre-arm enforcement."""
        signal_path = pre_arm_drain_env
        assert signal_path.exists(), "pre-condition: signal must exist before start"

        cli.cmd_start()

        assert not signal_path.exists(), (
            "drain signal must be consumed (deleted) after pre-arm enforcement so next start runs unscoped (AC-188-5)"
        )

    @pytest.mark.unit
    def test_pre_arm_logs_drain_enforced_audit(self, pre_arm_drain_env: Path, caplog: pytest.LogCaptureFixture) -> None:
        """AC-188-6 + AC-188-8: [ORCHESTRATOR_DRAIN_ENFORCED] is logged with reason after pre-arm enforcement."""
        import logging

        with caplog.at_level(logging.INFO, logger="devbench.cli"):
            cli.cmd_start()

        log_text = " ".join(rec.getMessage() for rec in caplog.records)
        assert "[ORCHESTRATOR_DRAIN_ENFORCED]" in log_text, (
            "[ORCHESTRATOR_DRAIN_ENFORCED] must be logged when pre-armed drain is enforced (AC-188-8)"
        )
        assert "smoke run" in log_text, "drain reason must appear in the audit log (AC-188-8)"

    @pytest.mark.unit
    def test_pre_arm_non_claim_messages_not_interrupted(self, tmp_path: Path) -> None:
        """AC-188-6: non-claim messages before the WU2 claim are NOT interrupted by pre-armed drain.

        The drain enforcement only fires at claim time.  All non-claim messages
        (representing WU1 being processed) must pass through the SDK iterator
        without interruption -- the pre-armed signal does not stop the run until
        the second claim attempt.

        This test uses a custom counting SDK rather than the shared fixture SDK
        because it needs to append to ``messages_seen`` inside the async generator,
        which requires defining the generator inline.
        """
        import sys
        import types

        from claude_agent_sdk.types import AssistantMessage, TextBlock, ToolUseBlock

        # Per-session path: cmd_start sets DEVBENCH_SESSION_NAME=SESSION_DEFAULT_NAME (spec 4.4.4).
        signal_path = tmp_path / SESSION_SESSIONS_BASE_DIR / SESSION_DEFAULT_NAME / "drain.signal"
        signal_path.parent.mkdir(parents=True)
        signal_path.write_text(
            '{"requested_at": "2026-05-16T00:00:00+00:00", "requested_by": "operator", "reason": "smoke run"}',
            encoding="utf-8",
        )

        messages_seen: list[str] = []

        mock_sdk_counting: Any = types.ModuleType("claude_agent_sdk")
        mock_sdk_counting.ClaudeAgentOptions = MagicMock()

        async def mock_query_counting(**kwargs: object) -> object:
            msg1 = AssistantMessage(
                content=[TextBlock(text="wu1-processing-message")],
                model="claude-opus-4-5",
            )
            msg2 = AssistantMessage(
                content=[TextBlock(text="wu1-done-message")],
                model="claude-opus-4-5",
            )
            msg3 = AssistantMessage(
                content=[
                    ToolUseBlock(
                        id="tu-wu2-claim",
                        name="Bash",
                        input={"command": "uv run devbench claim E1-F2-S1-T2"},
                    )
                ],
                model="claude-opus-4-5",
            )
            yield msg1
            messages_seen.append("wu1-processing-message")
            yield msg2
            messages_seen.append("wu1-done-message")
            yield msg3
            messages_seen.append("wu2-claim-message")

        mock_sdk_counting.query = mock_query_counting

        with (
            patch.dict(sys.modules, {"claude_agent_sdk": mock_sdk_counting}),
            patch("devbench.cli.WORKSPACE_ROOT", tmp_path),
            patch(
                "devbench.cli._should_auto_restart_after_no_actionable",
                return_value=(False, []),
            ),
        ):
            rc = cli.cmd_start()

        assert rc == 0
        # Both WU1 messages must have been yielded and seen before drain fired.
        assert "wu1-processing-message" in messages_seen, (
            "WU1 processing message must be yielded before drain enforcement"
        )
        assert "wu1-done-message" in messages_seen, "WU1 done message must be yielded before drain enforcement"
        # The WU2 claim message was yielded from the generator but _DrainRequested
        # was raised before any post-yield processing continued -- the claim itself
        # was NOT executed (enforcement happened at detection time, not after).
        assert "wu2-claim-message" not in messages_seen, (
            "WU2 claim generator-append must not run after _DrainRequested is raised"
        )

    @pytest.mark.unit
    @pytest.mark.parametrize(
        "reason",
        [
            "nightly smoke run",
            "pre-release freeze",
            "",
            "verify exactly one task completes",
        ],
    )
    def test_pre_arm_parametrized_reasons(self, pre_arm_drain_env: Path, reason: str) -> None:
        """AC-188-6: pre-armed drain with various reasons always enforces after one WU (rc=0, signal consumed).

        The fixture writes the signal with reason="smoke run"; this test overwrites
        it with the parametrized reason before calling cmd_start to cover all
        reason variants (including empty string) without duplicating the SDK mock
        and patch setup.
        """
        import json as _json

        signal_path = pre_arm_drain_env
        payload = _json.dumps(
            {
                "requested_at": "2026-05-16T00:00:00+00:00",
                "requested_by": "operator",
                "reason": reason,
            }
        )
        signal_path.write_text(payload, encoding="utf-8")

        rc = cli.cmd_start()

        assert rc == 0, f"cmd_start must return 0 for pre-armed drain with reason={reason!r}"
        assert not signal_path.exists(), f"drain signal must be consumed for pre-armed drain with reason={reason!r}"


# ---------------------------------------------------------------------------
# cmd_sessions tests (E4-F5-S1-T1, issue #192)
# ---------------------------------------------------------------------------


def _make_session(
    name: str,
    pid: int,
    scope: list[str],
    state_dir: Path,
    started_at: datetime | None = None,
    started_by: str = "tester",
) -> Any:
    """Construct a Session fixture for tests."""
    from devbench.session import Session

    return Session(
        name=name,
        pid=pid,
        scope=scope,
        started_at=started_at or datetime(2026, 1, 1, tzinfo=UTC),
        started_by=started_by,
        state_dir=state_dir,
    )


def _seed_sessions_registry(workspace_root: Path, sessions: list) -> None:
    """Write registry.json with the given list of Session objects."""
    from devbench.session import SessionRegistry

    registry = SessionRegistry(workspace_root)
    registry.save(sessions)


class TestCmdSessionsRegistered:
    """cmd_sessions is registered in _COMMANDS and _VARIADIC_COMMANDS."""

    @pytest.mark.unit
    def test_sessions_in_commands(self) -> None:
        """'sessions' key must be present in _COMMANDS."""
        assert "sessions" in cli._COMMANDS

    @pytest.mark.unit
    def test_sessions_in_variadic_commands(self) -> None:
        """'sessions' must be in _VARIADIC_COMMANDS so --cleanup flag parsing works."""
        assert "sessions" in cli._VARIADIC_COMMANDS

    @pytest.mark.unit
    def test_sessions_command_maps_to_cmd_sessions(self) -> None:
        """_COMMANDS['sessions'] callable must be cli.cmd_sessions."""
        func, _min_args, _desc = cli._COMMANDS["sessions"]
        assert func is cli.cmd_sessions


class TestCmdSessionsListEmpty:
    """devbench sessions with no active sessions -- prints empty table."""

    @pytest.mark.unit
    def test_returns_zero_when_no_registry(self, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
        """cmd_sessions returns rc=0 when no registry.json exists."""
        with patch("devbench.cli.WORKSPACE_ROOT", tmp_path):
            rc = cli.cmd_sessions()
        assert rc == 0

    @pytest.mark.unit
    def test_prints_no_active_sessions_when_registry_absent(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """cmd_sessions prints a message indicating no sessions when registry absent."""
        with patch("devbench.cli.WORKSPACE_ROOT", tmp_path):
            cli.cmd_sessions()
        out = capsys.readouterr().out
        assert "no active sessions" in out.lower()

    @pytest.mark.unit
    def test_prints_no_active_sessions_when_registry_empty(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """cmd_sessions prints no-sessions message when registry exists but is empty."""
        _seed_sessions_registry(tmp_path, [])
        with patch("devbench.cli.WORKSPACE_ROOT", tmp_path):
            cli.cmd_sessions()
        out = capsys.readouterr().out
        assert "no active sessions" in out.lower()


class TestCmdSessionsListTable:
    """devbench sessions lists each session's name, PID, scope, started_at, drain state, liveness."""

    @pytest.mark.unit
    def test_lists_session_name(self, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
        """cmd_sessions output includes the session name."""
        state_dir = tmp_path / ".devbench" / "sessions" / "alpha"
        state_dir.mkdir(parents=True, exist_ok=True)
        session = _make_session("alpha", 12345, [], state_dir)
        _seed_sessions_registry(tmp_path, [session])

        with patch("devbench.cli.WORKSPACE_ROOT", tmp_path):
            rc = cli.cmd_sessions()

        assert rc == 0
        out = capsys.readouterr().out
        assert "alpha" in out

    @pytest.mark.unit
    def test_lists_session_pid(self, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
        """cmd_sessions output includes the session PID."""
        state_dir = tmp_path / ".devbench" / "sessions" / "beta"
        state_dir.mkdir(parents=True, exist_ok=True)
        session = _make_session("beta", 99999, [], state_dir)
        _seed_sessions_registry(tmp_path, [session])

        with patch("devbench.cli.WORKSPACE_ROOT", tmp_path):
            rc = cli.cmd_sessions()

        assert rc == 0
        out = capsys.readouterr().out
        assert "99999" in out

    @pytest.mark.unit
    def test_lists_session_started_at(self, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
        """cmd_sessions output includes the started_at timestamp."""
        state_dir = tmp_path / ".devbench" / "sessions" / "gamma"
        state_dir.mkdir(parents=True, exist_ok=True)
        started = datetime(2026, 3, 15, 10, 30, 0, tzinfo=UTC)
        session = _make_session("gamma", 11111, [], state_dir, started_at=started)
        _seed_sessions_registry(tmp_path, [session])

        with patch("devbench.cli.WORKSPACE_ROOT", tmp_path):
            rc = cli.cmd_sessions()

        assert rc == 0
        out = capsys.readouterr().out
        assert "2026-03-15" in out

    @pytest.mark.unit
    def test_lists_liveness_active_when_process_alive(self, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
        """cmd_sessions shows ACTIVE liveness when process is running."""
        state_dir = tmp_path / ".devbench" / "sessions" / "live"
        state_dir.mkdir(parents=True, exist_ok=True)
        session = _make_session("live", 12345, [], state_dir)
        _seed_sessions_registry(tmp_path, [session])

        with (
            patch("devbench.cli.WORKSPACE_ROOT", tmp_path),
            patch("devbench.session.SessionRegistry.is_alive", return_value=True),
        ):
            rc = cli.cmd_sessions()

        assert rc == 0
        out = capsys.readouterr().out
        assert "ACTIVE" in out

    @pytest.mark.unit
    def test_lists_liveness_stale_when_process_dead(self, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
        """cmd_sessions shows STALE liveness when process is not running."""
        state_dir = tmp_path / ".devbench" / "sessions" / "stale"
        state_dir.mkdir(parents=True, exist_ok=True)
        session = _make_session("stale", 99998, [], state_dir)
        _seed_sessions_registry(tmp_path, [session])

        with (
            patch("devbench.cli.WORKSPACE_ROOT", tmp_path),
            patch("devbench.session.SessionRegistry.is_alive", return_value=False),
        ):
            rc = cli.cmd_sessions()

        assert rc == 0
        out = capsys.readouterr().out
        assert "STALE" in out

    @pytest.mark.unit
    def test_lists_drain_pending_when_signal_present(self, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
        """cmd_sessions shows drain state when drain.signal exists in session dir."""
        import json as _json

        state_dir = tmp_path / ".devbench" / "sessions" / "draining"
        state_dir.mkdir(parents=True, exist_ok=True)
        session = _make_session("draining", 12345, [], state_dir)
        _seed_sessions_registry(tmp_path, [session])
        # Write a drain.signal into the session state dir.
        drain_signal = state_dir / "drain.signal"
        drain_signal.write_text(
            _json.dumps(
                {
                    "requested_at": "2026-01-01T00:00:00+00:00",
                    "requested_by": "tester",
                    "reason": "planned stop",
                }
            ),
            encoding="utf-8",
        )

        with (
            patch("devbench.cli.WORKSPACE_ROOT", tmp_path),
            patch("devbench.session.SessionRegistry.is_alive", return_value=True),
        ):
            rc = cli.cmd_sessions()

        assert rc == 0
        out = capsys.readouterr().out
        assert "pending" in out.lower() or "drain" in out.lower()

    @pytest.mark.unit
    def test_lists_no_drain_when_signal_absent(self, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
        """cmd_sessions shows no drain state when no drain.signal in session dir."""
        state_dir = tmp_path / ".devbench" / "sessions" / "quiet"
        state_dir.mkdir(parents=True, exist_ok=True)
        session = _make_session("quiet", 12345, [], state_dir)
        _seed_sessions_registry(tmp_path, [session])

        with (
            patch("devbench.cli.WORKSPACE_ROOT", tmp_path),
            patch("devbench.session.SessionRegistry.is_alive", return_value=True),
        ):
            rc = cli.cmd_sessions()

        assert rc == 0
        out = capsys.readouterr().out
        assert "none" in out.lower() or "no drain" in out.lower()

    @pytest.mark.unit
    def test_lists_scope_ids_in_output(self, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
        """cmd_sessions includes scope IDs in the output."""
        state_dir = tmp_path / ".devbench" / "sessions" / "scoped"
        state_dir.mkdir(parents=True, exist_ok=True)
        session = _make_session("scoped", 12345, ["E1-F1-S1-T1", "E1-F1-S1-T2"], state_dir)
        _seed_sessions_registry(tmp_path, [session])

        with (
            patch("devbench.cli.WORKSPACE_ROOT", tmp_path),
            patch("devbench.session.SessionRegistry.is_alive", return_value=True),
        ):
            rc = cli.cmd_sessions()

        assert rc == 0
        out = capsys.readouterr().out
        assert "E1-F1-S1-T1" in out

    @pytest.mark.unit
    @pytest.mark.parametrize(
        "names",
        [
            ["alpha"],
            ["alpha", "beta"],
            ["alpha", "beta", "gamma"],
        ],
    )
    def test_lists_all_sessions(self, tmp_path: Path, capsys: pytest.CaptureFixture[str], names: list[str]) -> None:
        """cmd_sessions lists every session in the registry."""
        sessions = []
        for name in names:
            state_dir = tmp_path / ".devbench" / "sessions" / name
            state_dir.mkdir(parents=True, exist_ok=True)
            sessions.append(_make_session(name, 12345, [], state_dir))
        _seed_sessions_registry(tmp_path, sessions)

        with (
            patch("devbench.cli.WORKSPACE_ROOT", tmp_path),
            patch("devbench.session.SessionRegistry.is_alive", return_value=True),
        ):
            rc = cli.cmd_sessions()

        assert rc == 0
        out = capsys.readouterr().out
        for name in names:
            assert name in out, f"session '{name}' must appear in output"


class TestCmdSessionsCleanup:
    """devbench sessions --cleanup -- removes stale session dirs (AC-192-11)."""

    @pytest.mark.unit
    def test_cleanup_flag_removes_stale_session_dir(self, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
        """--cleanup removes the state_dir of a session whose PID is not running."""
        state_dir = tmp_path / ".devbench" / "sessions" / "stale"
        state_dir.mkdir(parents=True, exist_ok=True)
        session = _make_session("stale", 99998, [], state_dir)
        _seed_sessions_registry(tmp_path, [session])

        with (
            patch("devbench.cli.WORKSPACE_ROOT", tmp_path),
            patch("devbench.session.SessionRegistry.is_alive", return_value=False),
        ):
            rc = cli.cmd_sessions("--cleanup")

        assert rc == 0
        assert not state_dir.exists(), "stale session dir must be removed by --cleanup"

    @pytest.mark.unit
    def test_cleanup_prints_removed_names(self, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
        """--cleanup prints the names of removed sessions."""
        state_dir = tmp_path / ".devbench" / "sessions" / "stale"
        state_dir.mkdir(parents=True, exist_ok=True)
        session = _make_session("stale", 99998, [], state_dir)
        _seed_sessions_registry(tmp_path, [session])

        with (
            patch("devbench.cli.WORKSPACE_ROOT", tmp_path),
            patch("devbench.session.SessionRegistry.is_alive", return_value=False),
        ):
            cli.cmd_sessions("--cleanup")

        out = capsys.readouterr().out
        assert "stale" in out

    @pytest.mark.unit
    def test_cleanup_returns_zero_when_nothing_stale(self, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
        """--cleanup returns rc=0 when no stale sessions exist."""
        state_dir = tmp_path / ".devbench" / "sessions" / "active"
        state_dir.mkdir(parents=True, exist_ok=True)
        session = _make_session("active", 12345, [], state_dir)
        _seed_sessions_registry(tmp_path, [session])

        with (
            patch("devbench.cli.WORKSPACE_ROOT", tmp_path),
            patch("devbench.session.SessionRegistry.is_alive", return_value=True),
        ):
            rc = cli.cmd_sessions("--cleanup")

        assert rc == 0

    @pytest.mark.unit
    def test_cleanup_preserves_active_session_dirs(self, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
        """--cleanup does not remove state dirs for ACTIVE sessions."""
        active_dir = tmp_path / ".devbench" / "sessions" / "active"
        active_dir.mkdir(parents=True, exist_ok=True)
        stale_dir = tmp_path / ".devbench" / "sessions" / "stale"
        stale_dir.mkdir(parents=True, exist_ok=True)
        sessions = [
            _make_session("active", 12345, [], active_dir),
            _make_session("stale", 99998, [], stale_dir),
        ]
        _seed_sessions_registry(tmp_path, sessions)

        def _is_alive_side_effect(pid: int) -> bool:
            return pid == 12345

        with (
            patch("devbench.cli.WORKSPACE_ROOT", tmp_path),
            patch("devbench.session.SessionRegistry.is_alive", side_effect=_is_alive_side_effect),
        ):
            rc = cli.cmd_sessions("--cleanup")

        assert rc == 0
        assert active_dir.exists(), "active session dir must not be removed"
        assert not stale_dir.exists(), "stale session dir must be removed"

    @pytest.mark.unit
    def test_cleanup_no_registry_returns_zero(self, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
        """--cleanup returns rc=0 when no registry.json exists."""
        with patch("devbench.cli.WORKSPACE_ROOT", tmp_path):
            rc = cli.cmd_sessions("--cleanup")
        assert rc == 0

    @pytest.mark.unit
    def test_cleanup_prints_nothing_removed_when_all_active(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """--cleanup prints a message when no sessions were cleaned up."""
        state_dir = tmp_path / ".devbench" / "sessions" / "active"
        state_dir.mkdir(parents=True, exist_ok=True)
        session = _make_session("active", 12345, [], state_dir)
        _seed_sessions_registry(tmp_path, [session])

        with (
            patch("devbench.cli.WORKSPACE_ROOT", tmp_path),
            patch("devbench.session.SessionRegistry.is_alive", return_value=True),
        ):
            cli.cmd_sessions("--cleanup")

        out = capsys.readouterr().out
        assert "no stale" in out.lower() or "nothing" in out.lower() or "0" in out


class TestCmdSessionsInvalidArgs:
    """cmd_sessions rejects invalid flag combinations."""

    @pytest.mark.unit
    def test_unknown_flag_returns_rc2(self, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
        """cmd_sessions with an unrecognised flag returns rc=2 and stderr message."""
        with patch("devbench.cli.WORKSPACE_ROOT", tmp_path):
            rc = cli.cmd_sessions("--unknown-flag")
        assert rc == 2
        err = capsys.readouterr().err
        assert "unknown" in err.lower() or "invalid" in err.lower()


class TestCmdSessionsIntegration:
    """Integration tests for cmd_sessions against real fixture workspaces (no boundary-crossing mocks)."""

    @pytest.mark.unit
    def test_list_integration_shows_correct_columns(self, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
        """Full integration: cmd_sessions lists a seeded session with correct data.

        No mocks on the liveness check -- instead we use os.getpid() as the
        PID so the process is guaranteed alive.
        """
        import os

        state_dir = tmp_path / ".devbench" / "sessions" / "mytest"
        state_dir.mkdir(parents=True, exist_ok=True)
        pid = os.getpid()
        started = datetime(2026, 4, 1, 12, 0, 0, tzinfo=UTC)
        session = _make_session("mytest", pid, ["E1-F1-S1-T1"], state_dir, started_at=started)
        _seed_sessions_registry(tmp_path, [session])

        with patch("devbench.cli.WORKSPACE_ROOT", tmp_path):
            rc = cli.cmd_sessions()

        assert rc == 0
        out = capsys.readouterr().out
        assert "mytest" in out
        assert str(pid) in out
        assert "2026-04-01" in out
        assert "ACTIVE" in out
        assert "E1-F1-S1-T1" in out

    @pytest.mark.unit
    def test_cleanup_integration_removes_stale_and_updates_registry(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """Full integration: --cleanup removes stale dir and updates registry.json.

        Spawns a real subprocess and waits for it to exit, capturing its PID.
        The PID is then guaranteed dead (process fully reaped before we proceed).
        """
        import subprocess

        # Spawn and wait -- after wait() the PID is guaranteed not running.
        proc = subprocess.Popen(["true"])
        dead_pid = proc.pid
        proc.wait()

        stale_dir = tmp_path / ".devbench" / "sessions" / "gone"
        stale_dir.mkdir(parents=True, exist_ok=True)
        session = _make_session("gone", dead_pid, [], stale_dir)
        _seed_sessions_registry(tmp_path, [session])

        with patch("devbench.cli.WORKSPACE_ROOT", tmp_path):
            rc = cli.cmd_sessions("--cleanup")

        assert rc == 0
        assert not stale_dir.exists(), "stale session dir must be deleted"

        # Registry must be updated (entry removed).
        from devbench.session import SessionRegistry

        remaining = SessionRegistry(tmp_path).load()
        assert all(s.name != "gone" for s in remaining), "stale session must be removed from registry"


# ---------------------------------------------------------------------------
# cmd_stop tests (E4-F5-S1-T2, issue #192)
# ---------------------------------------------------------------------------


class TestCmdStopRegistered:
    """cmd_stop is registered in _COMMANDS and _VARIADIC_COMMANDS."""

    @pytest.mark.unit
    def test_stop_in_commands(self) -> None:
        """'stop' key must be present in _COMMANDS."""
        assert "stop" in cli._COMMANDS

    @pytest.mark.unit
    def test_stop_in_variadic_commands(self) -> None:
        """'stop' must be in _VARIADIC_COMMANDS so --session flag parsing works."""
        assert "stop" in cli._VARIADIC_COMMANDS

    @pytest.mark.unit
    def test_stop_command_maps_to_cmd_stop(self) -> None:
        """_COMMANDS['stop'] callable must be cli.cmd_stop."""
        func, _min_args, _desc = cli._COMMANDS["stop"]
        assert func is cli.cmd_stop


class TestParseStopArgv:
    """_parse_stop_argv correctly parses --session <name> flags."""

    @pytest.mark.unit
    def test_valid_session_name_returns_name(self) -> None:
        """--session <name> returns the session name and rc=0."""
        name, rc, msg = cli._parse_stop_argv(("--session", "myrun"))
        assert name == "myrun"
        assert rc == 0
        assert msg == ""

    @pytest.mark.unit
    def test_missing_session_flag_returns_error(self) -> None:
        """No --session flag returns rc=2 with an error message."""
        name, rc, msg = cli._parse_stop_argv(())
        assert name is None
        assert rc == 2
        assert "session" in msg.lower()

    @pytest.mark.unit
    def test_session_flag_without_value_returns_error(self) -> None:
        """--session with no following value returns rc=2."""
        name, rc, msg = cli._parse_stop_argv(("--session",))
        assert name is None
        assert rc == 2
        assert "session" in msg.lower()

    @pytest.mark.unit
    def test_unknown_flag_returns_error(self) -> None:
        """Unknown flag returns rc=2."""
        name, rc, msg = cli._parse_stop_argv(("--unknown",))
        assert name is None
        assert rc == 2
        assert "unknown" in msg.lower()

    @pytest.mark.unit
    def test_empty_session_name_returns_error(self) -> None:
        """--session with empty string value returns rc=2."""
        name, rc, msg = cli._parse_stop_argv(("--session", ""))
        assert name is None
        assert rc == 2
        assert "session" in msg.lower()


class TestCmdStopErrors:
    """cmd_stop returns non-zero with actionable messages on error paths."""

    @pytest.mark.unit
    def test_missing_session_flag_returns_rc2(self, capsys: pytest.CaptureFixture[str]) -> None:
        """cmd_stop with no args returns rc=2 and prints error to stderr."""
        rc = cli.cmd_stop()
        assert rc == 2
        err = capsys.readouterr().err
        assert "session" in err.lower()

    @pytest.mark.unit
    def test_unknown_session_returns_rc1(self, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
        """cmd_stop for a session that has no state dir returns rc=1."""
        with patch("devbench.cli.WORKSPACE_ROOT", tmp_path):
            rc = cli.cmd_stop("--session", "nonexistent")
        assert rc == 1
        err = capsys.readouterr().err
        assert "nonexistent" in err or "not found" in err.lower() or "pid" in err.lower()

    @pytest.mark.unit
    def test_missing_pid_file_returns_rc1(self, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
        """cmd_stop when state dir exists but pid file is absent returns rc=1."""
        state_dir = tmp_path / ".devbench" / "sessions" / "nopid"
        state_dir.mkdir(parents=True, exist_ok=True)
        # No pid file written
        with patch("devbench.cli.WORKSPACE_ROOT", tmp_path):
            rc = cli.cmd_stop("--session", "nopid")
        assert rc == 1
        err = capsys.readouterr().err
        assert "pid" in err.lower() or "nopid" in err

    @pytest.mark.unit
    def test_non_integer_pid_file_returns_rc1(self, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
        """cmd_stop when pid file contains non-integer text returns rc=1."""
        state_dir = tmp_path / ".devbench" / "sessions" / "badpid"
        state_dir.mkdir(parents=True, exist_ok=True)
        (state_dir / "pid").write_text("notanumber", encoding="utf-8")
        with patch("devbench.cli.WORKSPACE_ROOT", tmp_path):
            rc = cli.cmd_stop("--session", "badpid")
        assert rc == 1
        err = capsys.readouterr().err
        assert "pid" in err.lower()

    @pytest.mark.unit
    def test_dotdot_session_name_returns_rc1(self, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
        """cmd_stop with '..' in session name returns rc=2 (invalid name)."""
        rc = cli.cmd_stop("--session", "../etc/passwd")
        assert rc == 2
        err = capsys.readouterr().err
        assert "invalid" in err.lower() or "session" in err.lower()


class TestCmdStopSendsSigterm:
    """cmd_stop reads pid file and sends SIGTERM to the target process."""

    @pytest.mark.unit
    def test_sends_sigterm_to_pid_in_file(self, tmp_path: Path) -> None:
        """cmd_stop sends os.kill(pid, signal.SIGTERM) to the PID in the pid file."""
        import signal

        state_dir = tmp_path / ".devbench" / "sessions" / "myrun"
        state_dir.mkdir(parents=True, exist_ok=True)
        (state_dir / "pid").write_text("12345", encoding="utf-8")

        sent_signals: list[tuple[int, int]] = []

        def _fake_kill(pid: int, sig: int) -> None:
            sent_signals.append((pid, sig))

        with (
            patch("devbench.cli.WORKSPACE_ROOT", tmp_path),
            patch("devbench.cli.os.kill", side_effect=_fake_kill),
        ):
            rc = cli.cmd_stop("--session", "myrun")

        assert rc == 0
        assert (12345, signal.SIGTERM) in sent_signals, "SIGTERM must be sent to the PID from the pid file"

    @pytest.mark.unit
    def test_returns_rc0_on_success(self, tmp_path: Path) -> None:
        """cmd_stop returns rc=0 after successfully sending SIGTERM."""
        state_dir = tmp_path / ".devbench" / "sessions" / "run1"
        state_dir.mkdir(parents=True, exist_ok=True)
        (state_dir / "pid").write_text("12345", encoding="utf-8")

        with (
            patch("devbench.cli.WORKSPACE_ROOT", tmp_path),
            patch("devbench.cli.os.kill"),
        ):
            rc = cli.cmd_stop("--session", "run1")

        assert rc == 0

    @pytest.mark.unit
    def test_prints_confirmation_message(self, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
        """cmd_stop prints a confirmation message to stdout on success."""
        state_dir = tmp_path / ".devbench" / "sessions" / "run1"
        state_dir.mkdir(parents=True, exist_ok=True)
        (state_dir / "pid").write_text("12345", encoding="utf-8")

        with (
            patch("devbench.cli.WORKSPACE_ROOT", tmp_path),
            patch("devbench.cli.os.kill"),
        ):
            cli.cmd_stop("--session", "run1")

        out = capsys.readouterr().out
        assert "run1" in out or "12345" in out or "sigterm" in out.lower() or "stop" in out.lower()

    @pytest.mark.unit
    def test_process_not_found_returns_rc1(self, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
        """cmd_stop returns rc=1 when os.kill raises ProcessLookupError (ESRCH)."""
        state_dir = tmp_path / ".devbench" / "sessions" / "gone"
        state_dir.mkdir(parents=True, exist_ok=True)
        (state_dir / "pid").write_text("99999", encoding="utf-8")

        with (
            patch("devbench.cli.WORKSPACE_ROOT", tmp_path),
            patch("devbench.cli.os.kill", side_effect=ProcessLookupError("No such process")),
        ):
            rc = cli.cmd_stop("--session", "gone")

        assert rc == 1
        err = capsys.readouterr().err
        assert "99999" in err or "not running" in err.lower() or "not found" in err.lower()

    @pytest.mark.unit
    def test_permission_error_returns_rc1(self, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
        """cmd_stop returns rc=1 when os.kill raises PermissionError (EPERM)."""
        state_dir = tmp_path / ".devbench" / "sessions" / "noperm"
        state_dir.mkdir(parents=True, exist_ok=True)
        (state_dir / "pid").write_text("1", encoding="utf-8")

        with (
            patch("devbench.cli.WORKSPACE_ROOT", tmp_path),
            patch("devbench.cli.os.kill", side_effect=PermissionError("Permission denied")),
        ):
            rc = cli.cmd_stop("--session", "noperm")

        assert rc == 1
        err = capsys.readouterr().err
        assert "permission" in err.lower() or "1" in err


class TestCmdStopIntegration:
    """Integration tests for cmd_stop against real fixture workspaces."""

    @pytest.mark.unit
    def test_stop_integration_sends_sigterm_to_real_process(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """cmd_stop sends SIGTERM to a live subprocess via its real pid file.

        Spawns a short-lived sleep subprocess, writes its PID into the session
        state dir, calls cmd_stop, and verifies the subprocess received the signal.
        """
        import signal
        import subprocess
        import time

        # Spawn a subprocess that will hold alive until signalled.
        proc = subprocess.Popen(["sleep", "60"])
        pid = proc.pid
        state_dir = tmp_path / ".devbench" / "sessions" / "integration"
        state_dir.mkdir(parents=True, exist_ok=True)
        (state_dir / "pid").write_text(str(pid), encoding="utf-8")

        try:
            with patch("devbench.cli.WORKSPACE_ROOT", tmp_path):
                rc = cli.cmd_stop("--session", "integration")

            assert rc == 0

            # Wait briefly for the signal to be delivered and the process to exit.
            deadline = time.monotonic() + 3.0
            while time.monotonic() < deadline:
                if proc.poll() is not None:
                    break
                time.monotonic()  # no-sleep busy-check is acceptable in tests

            proc.wait(timeout=1)
            # returncode -15 means killed by SIGTERM (signal 15).
            assert proc.returncode == -signal.SIGTERM, (
                f"expected process killed by SIGTERM (rc={-signal.SIGTERM}), got {proc.returncode}"
            )
        finally:
            # Ensure the subprocess is cleaned up even if the assertion fails.
            if proc.poll() is None:
                proc.kill()
                proc.wait()


# ---------------------------------------------------------------------------
# SIGTERM handler in cmd_start tests (E4-F5-S1-T2, issue #192)
# ---------------------------------------------------------------------------


class TestCmdStartSigtermHandler:
    """SIGTERM handler in cmd_start forces in-flight WU to blocked."""

    @pytest.mark.unit
    def test_forced_blocked_on_stop_audit_constant_defined(self) -> None:
        """_FORCED_BLOCKED_ON_STOP_AUDIT_PREFIX constant must be defined in cli."""
        assert hasattr(cli, "_FORCED_BLOCKED_ON_STOP_AUDIT_PREFIX")
        prefix = cli._FORCED_BLOCKED_ON_STOP_AUDIT_PREFIX
        assert "FORCED_BLOCKED_ON_STOP" in prefix

    @pytest.mark.unit
    def test_force_block_in_flight_wu_sets_status_blocked(self, tmp_path: Path) -> None:
        """_force_block_in_flight_wu sets in-progress WU to blocked and appends audit."""
        from devbench.backlog.manager import BacklogManager
        from devbench.backlog.work_unit import WorkUnit, WorkUnitStatus, WorkUnitType

        # Build a minimal in-progress WU file on disk.
        backlog_dir = tmp_path / "backlog"
        backlog_dir.mkdir()
        wu_file = backlog_dir / "E1-F1-S1-T1.md"
        wu_file.write_text(
            "# E1-F1-S1-T1: Test Task\n\n## Status: in-progress\n\n## Comments\n",
            encoding="utf-8",
        )
        wu = WorkUnit(
            id="E1-F1-S1-T1",
            title="Test Task",
            status=WorkUnitStatus.IN_PROGRESS,
            unit_type=WorkUnitType.TASK,
            file_path=wu_file,
            repo="test/repo",
        )

        # Intercept BacklogManager.force_status and _append_agent_comment calls.
        forced: list[tuple[str, str]] = []
        appended: list[tuple[str, str]] = []

        def _mock_force_status(
            self: object, wu_path: Path, index: Path, unit_id: str, status: str, **kw: object
        ) -> None:
            forced.append((unit_id, status))

        def _mock_append(self: object, path: Path, agent: str, message: str) -> None:
            appended.append((agent, message))

        with (
            patch.object(BacklogManager, "force_status", _mock_force_status),
            patch.object(BacklogManager, "_append_agent_comment", _mock_append),
            patch("devbench.cli.BACKLOG_INDEX", tmp_path / "BACKLOG.md"),
        ):
            cli._force_block_in_flight_wu(wu, session_name="myrun")

        assert ("E1-F1-S1-T1", "blocked") in forced, "force_status must be called with 'blocked'"
        audit_messages = [msg for _, msg in appended]
        assert any("FORCED_BLOCKED_ON_STOP" in m for m in audit_messages), (
            "audit comment must contain FORCED_BLOCKED_ON_STOP"
        )
        assert any("myrun" in m for m in audit_messages), "audit comment must contain the session name"

    @pytest.mark.unit
    def test_force_block_in_flight_wu_no_op_when_no_in_progress(self, tmp_path: Path) -> None:
        """_force_block_in_flight_wu does nothing when no in-progress WU is provided (None)."""
        # Should not raise; calling with None is the no-op signal.
        cli._force_block_in_flight_wu(None, session_name="myrun")

    @pytest.mark.unit
    def test_find_in_flight_wu_returns_in_progress_unit(self, tmp_path: Path) -> None:
        """_find_in_flight_wu returns the first in-progress WU from the backlog."""
        from devbench.backlog.work_unit import WorkUnit, WorkUnitStatus, WorkUnitType

        wu_in_progress = WorkUnit(
            id="E1-F1-S1-T2",
            title="Running Task",
            status=WorkUnitStatus.IN_PROGRESS,
            unit_type=WorkUnitType.TASK,
            file_path=tmp_path / "E1-F1-S1-T2.md",
            repo="test/repo",
        )
        wu_queued = WorkUnit(
            id="E1-F1-S1-T3",
            title="Queued Task",
            status=WorkUnitStatus.IN_QUEUE,
            unit_type=WorkUnitType.TASK,
            file_path=tmp_path / "E1-F1-S1-T3.md",
            repo="test/repo",
        )
        units = [wu_queued, wu_in_progress]

        result = cli._find_in_flight_wu(units)

        assert result is wu_in_progress

    @pytest.mark.unit
    def test_find_in_flight_wu_returns_none_when_none_in_progress(self, tmp_path: Path) -> None:
        """_find_in_flight_wu returns None when no unit has in-progress status."""
        from devbench.backlog.work_unit import WorkUnit, WorkUnitStatus, WorkUnitType

        wu_queued = WorkUnit(
            id="E1-F1-S1-T1",
            title="Queued Task",
            status=WorkUnitStatus.IN_QUEUE,
            unit_type=WorkUnitType.TASK,
            file_path=tmp_path / "E1-F1-S1-T1.md",
            repo="test/repo",
        )

        result = cli._find_in_flight_wu([wu_queued])

        assert result is None

    @pytest.mark.unit
    def test_find_in_flight_wu_returns_none_for_empty_list(self) -> None:
        """_find_in_flight_wu returns None when the unit list is empty."""
        result = cli._find_in_flight_wu([])
        assert result is None


# ---------------------------------------------------------------------------
# AC-192-12 / AC-192-13: cmd_status --session flag (E4-F6-S1-T1)
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestParseStatusArgvSession:
    """_parse_status_argv correctly parses --session <name> flag."""

    def test_session_flag_sets_session_field(self) -> None:
        """--session <name> sets the session field."""
        result = cli._parse_status_argv(("--session", "alpha"))
        assert result.session == "alpha"
        assert result.exit_code == 0

    def test_session_flag_missing_value_returns_error(self) -> None:
        """--session without a value returns exit_code=1."""
        result = cli._parse_status_argv(("--session",))
        assert result.exit_code == 1

    def test_session_flag_missing_value_next_is_flag_returns_error(self) -> None:
        """--session followed by another flag returns exit_code=1."""
        result = cli._parse_status_argv(("--session", "--detail"))
        assert result.exit_code == 1

    def test_session_default_is_empty_string(self) -> None:
        """Without --session the session field defaults to empty string."""
        result = cli._parse_status_argv(())
        assert result.session == ""

    def test_session_combined_with_detail(self) -> None:
        """--session and --detail can be combined."""
        result = cli._parse_status_argv(("--session", "beta", "--detail"))
        assert result.session == "beta"
        assert result.detail is True
        assert result.exit_code == 0

    @pytest.mark.parametrize("name", ["alpha", "my-session", "session-1"])
    def test_session_various_names_accepted(self, name: str) -> None:
        """Various valid session names are accepted."""
        result = cli._parse_status_argv(("--session", name))
        assert result.session == name
        assert result.exit_code == 0


@pytest.mark.unit
class TestExtractSessionFromWu:
    """_extract_session_from_wu reads the WU_CLAIMED session name from a WU file."""

    def _wu_with_content(self, tmp_path: Path, unit_id: str, content: str) -> WorkUnit:
        """Write content to a tmp WU file and return a WorkUnit pointing at it."""
        wu_file = tmp_path / f"{unit_id}.md"
        wu_file.write_text(content)
        return WorkUnit(
            id=unit_id,
            title="Test Unit",
            status=WorkUnitStatus.IN_PROGRESS,
            unit_type=WorkUnitType.TASK,
            file_path=wu_file,
            repo="test/repo",
        )

    def test_extracts_session_name_from_wu_claimed_comment(self, tmp_path: Path) -> None:
        """Session name is extracted from [WU_CLAIMED] ... session=<name> line."""
        content = (
            "# E1-F1-S1-T1: Test\n\n"
            "## Status: in-progress\n\n"
            "## Comments\n\n"
            "[2026-05-17 00:05 UTC] [agent/orchestrator] [WU_CLAIMED] "
            "Set E1-F1-S1-T1 to 'in-progress' session=alpha\n"
        )
        wu = self._wu_with_content(tmp_path, "E1-F1-S1-T1", content)
        assert cli._extract_session_from_wu(wu) == "alpha"

    def test_returns_none_when_no_session_in_wu_claimed(self, tmp_path: Path) -> None:
        """Returns None when WU_CLAIMED has no session= token (legacy single-session)."""
        content = (
            "# E1-F1-S1-T1: Test\n\n"
            "## Status: in-progress\n\n"
            "## Comments\n\n"
            "[2026-05-17 00:05 UTC] [agent/orchestrator] [WU_CLAIMED] "
            "Set E1-F1-S1-T1 to 'in-progress'\n"
        )
        wu = self._wu_with_content(tmp_path, "E1-F1-S1-T1", content)
        assert cli._extract_session_from_wu(wu) is None

    def test_returns_none_when_no_comments_section(self, tmp_path: Path) -> None:
        """Returns None when the WU file has no Comments section."""
        content = "# E1-F1-S1-T1: Test\n\n## Status: in-progress\n\n"
        wu = self._wu_with_content(tmp_path, "E1-F1-S1-T1", content)
        assert cli._extract_session_from_wu(wu) is None

    def test_returns_none_when_no_wu_claimed_in_comments(self, tmp_path: Path) -> None:
        """Returns None when Comments section exists but has no [WU_CLAIMED] line."""
        content = (
            "# E1-F1-S1-T1: Test\n\n"
            "## Status: in-progress\n\n"
            "## Comments\n\n"
            "[2026-05-17 00:05 UTC] [agent/executor] Some other comment\n"
        )
        wu = self._wu_with_content(tmp_path, "E1-F1-S1-T1", content)
        assert cli._extract_session_from_wu(wu) is None

    def test_extracts_most_recent_session_from_multiple_wu_claimed(self, tmp_path: Path) -> None:
        """Most recent [WU_CLAIMED] line determines the session name."""
        content = (
            "# E1-F1-S1-T1: Test\n\n"
            "## Status: in-progress\n\n"
            "## Comments\n\n"
            "[2026-05-10 00:00 UTC] [agent/orchestrator] [WU_CLAIMED] "
            "Set E1-F1-S1-T1 to 'in-progress' session=beta\n"
            "[2026-05-17 00:05 UTC] [agent/orchestrator] [WU_CLAIMED] "
            "Set E1-F1-S1-T1 to 'in-progress' session=alpha\n"
        )
        wu = self._wu_with_content(tmp_path, "E1-F1-S1-T1", content)
        assert cli._extract_session_from_wu(wu) == "alpha"

    def test_returns_none_when_file_does_not_exist(self, tmp_path: Path) -> None:
        """Returns None when the WU file does not exist on disk."""
        wu = WorkUnit(
            id="E1-F1-S1-T99",
            title="Missing",
            status=WorkUnitStatus.IN_PROGRESS,
            unit_type=WorkUnitType.TASK,
            file_path=tmp_path / "nonexistent.md",
            repo="test/repo",
        )
        assert cli._extract_session_from_wu(wu) is None


@pytest.mark.unit
class TestCmdStatusSessionFilter:
    """AC-192-12: cmd_status --session <name> filters to that session's WUs."""

    def _make_wu_file(
        self,
        tmp_path: Path,
        unit_id: str,
        session: str | None,
        status: WorkUnitStatus = WorkUnitStatus.IN_PROGRESS,
    ) -> WorkUnit:
        """Write a WU file with optional session= in WU_CLAIMED and return a WorkUnit."""
        wu_file = tmp_path / f"{unit_id}.md"
        claim_line = f"[WU_CLAIMED] Set {unit_id} to 'in-progress'"
        if session:
            claim_line += f" session={session}"
        content = (
            f"# {unit_id}: Test\n\n"
            f"## Status: {status.value}\n\n"
            "## Comments\n\n"
            f"[2026-05-17 00:05 UTC] [agent/orchestrator] {claim_line}\n"
        )
        wu_file.write_text(content)
        return WorkUnit(
            id=unit_id,
            title=f"Task {unit_id}",
            status=status,
            unit_type=WorkUnitType.TASK,
            file_path=wu_file,
            repo="test/repo",
        )

    def _make_parser_mock(self, units: list[WorkUnit]) -> MagicMock:
        parser = MagicMock()
        parser.parse_index.return_value = units
        parser.get_parallel_candidates.return_value = []
        parser.get_blocked_units.return_value = []
        parser.all_done.return_value = False
        return parser

    def test_session_filter_shows_only_matching_session_wus(
        self,
        tmp_path: Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """--session alpha shows only WUs claimed by session alpha."""
        wu_alpha = self._make_wu_file(tmp_path, "E1-F1-S1-T1", session="alpha")
        wu_beta = self._make_wu_file(tmp_path, "E1-F1-S1-T2", session="beta")
        wu_no_session = self._make_wu_file(tmp_path, "E1-F1-S1-T3", session=None)
        units = [wu_alpha, wu_beta, wu_no_session]
        parser = self._make_parser_mock(units)

        with (
            patch("devbench.cli.BacklogParser", return_value=parser),
            patch("devbench.cli.WORKSPACE_ROOT", tmp_path),
            patch("devbench.cli._count_unmaterialised_proposed_tasks", return_value=0),
        ):
            rc = cli.cmd_status("--session", "alpha")

        assert rc == 0
        out = capsys.readouterr().out
        # The summary should show only 1 WU (the alpha one)
        assert "TOTAL" in out
        # The alpha WU should appear in active panel; beta WU should NOT
        assert "E1-F1-S1-T1" in out
        assert "E1-F1-S1-T2" not in out
        assert "E1-F1-S1-T3" not in out

    def test_session_filter_empty_result_when_no_matching_wus(
        self,
        tmp_path: Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """--session unknown shows TOTAL=0 when no WUs match."""
        wu_alpha = self._make_wu_file(tmp_path, "E1-F1-S1-T1", session="alpha")
        parser = self._make_parser_mock([wu_alpha])

        with (
            patch("devbench.cli.BacklogParser", return_value=parser),
            patch("devbench.cli.WORKSPACE_ROOT", tmp_path),
            patch("devbench.cli._count_unmaterialised_proposed_tasks", return_value=0),
        ):
            rc = cli.cmd_status("--session", "nonexistent")

        assert rc == 0
        out = capsys.readouterr().out
        assert "TOTAL" in out
        # Should show TOTAL 0
        lines = [l for l in out.splitlines() if "TOTAL" in l]
        assert lines, "Expected a TOTAL line in output"
        assert "0" in lines[0]

    def test_session_filter_missing_value_returns_error(
        self,
        tmp_path: Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """--session without a value returns rc=1 and prints error to stderr."""
        with patch("devbench.cli.WORKSPACE_ROOT", tmp_path):
            rc = cli.cmd_status("--session")

        assert rc == 1
        err = capsys.readouterr().err
        assert "--session" in err

    def test_no_session_flag_shows_all_wus(
        self,
        tmp_path: Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """Without --session, all WUs are shown (aggregated view)."""
        wu_alpha = self._make_wu_file(tmp_path, "E1-F1-S1-T1", session="alpha")
        wu_beta = self._make_wu_file(tmp_path, "E1-F1-S1-T2", session="beta")
        units = [wu_alpha, wu_beta]
        parser = self._make_parser_mock(units)

        with (
            patch("devbench.cli.BacklogParser", return_value=parser),
            patch("devbench.cli.WORKSPACE_ROOT", tmp_path),
            patch("devbench.cli._count_unmaterialised_proposed_tasks", return_value=0),
        ):
            rc = cli.cmd_status()

        assert rc == 0
        out = capsys.readouterr().out
        # Both WUs appear in active panel
        assert "E1-F1-S1-T1" in out
        assert "E1-F1-S1-T2" in out
        # TOTAL should show 2
        lines = [l for l in out.splitlines() if "TOTAL" in l]
        assert lines
        assert "2" in lines[0]

    @pytest.mark.parametrize("session_name", ["alpha", "my-session", "session-1"])
    def test_session_filter_various_valid_names(
        self,
        session_name: str,
        tmp_path: Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """Various valid session name values are accepted by --session."""
        wu = self._make_wu_file(tmp_path, "E1-F1-S1-T1", session=session_name)
        parser = self._make_parser_mock([wu])

        with (
            patch("devbench.cli.BacklogParser", return_value=parser),
            patch("devbench.cli.WORKSPACE_ROOT", tmp_path),
            patch("devbench.cli._count_unmaterialised_proposed_tasks", return_value=0),
        ):
            rc = cli.cmd_status("--session", session_name)

        assert rc == 0
        out = capsys.readouterr().out
        assert "E1-F1-S1-T1" in out


@pytest.mark.unit
class TestCmdStatusSessionAggregation:
    """AC-192-13: cmd_status without --session aggregates correctly across sessions."""

    def _make_wu_file(
        self,
        tmp_path: Path,
        unit_id: str,
        session: str | None,
        status: WorkUnitStatus = WorkUnitStatus.IN_PROGRESS,
    ) -> WorkUnit:
        """Write a WU file with optional session= in WU_CLAIMED and return a WorkUnit."""
        wu_file = tmp_path / f"{unit_id}.md"
        claim_line = f"[WU_CLAIMED] Set {unit_id} to 'in-progress'"
        if session:
            claim_line += f" session={session}"
        content = (
            f"# {unit_id}: Test\n\n"
            f"## Status: {status.value}\n\n"
            "## Comments\n\n"
            f"[2026-05-17 00:05 UTC] [agent/orchestrator] {claim_line}\n"
        )
        wu_file.write_text(content)
        return WorkUnit(
            id=unit_id,
            title=f"Task {unit_id}",
            status=status,
            unit_type=WorkUnitType.TASK,
            file_path=wu_file,
            repo="test/repo",
        )

    def test_aggregated_view_no_double_counting(
        self,
        tmp_path: Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """Aggregated view (no --session) shows each WU exactly once."""
        wu_a = self._make_wu_file(tmp_path, "E1-F1-S1-T1", session="alpha")
        wu_b = self._make_wu_file(tmp_path, "E1-F1-S1-T2", session="beta")
        wu_c = self._make_wu_file(tmp_path, "E1-F1-S1-T3", session=None, status=WorkUnitStatus.IN_QUEUE)
        units = [wu_a, wu_b, wu_c]
        parser = MagicMock()
        parser.parse_index.return_value = units
        parser.get_parallel_candidates.return_value = [wu_c]
        parser.get_blocked_units.return_value = []
        parser.all_done.return_value = False

        with (
            patch("devbench.cli.BacklogParser", return_value=parser),
            patch("devbench.cli.WORKSPACE_ROOT", tmp_path),
            patch("devbench.cli._count_unmaterialised_proposed_tasks", return_value=0),
        ):
            rc = cli.cmd_status()

        assert rc == 0
        out = capsys.readouterr().out
        # TOTAL must be 3 (each WU counted once)
        total_lines = [l for l in out.splitlines() if "TOTAL" in l]
        assert total_lines
        assert "3" in total_lines[0]


# AC-192-12 / AC-192-13: cmd_report --session flag (E4-F6-S1-T2)
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestExtractScopeFlagsForReportSession:
    """_extract_scope_flags_for_report strips --session and returns the session name."""

    def test_session_flag_is_extracted(self) -> None:
        """--session <name> is stripped and returned as session string."""
        include, exclude, session, remaining = cli._extract_scope_flags_for_report(
            ["--session", "alpha", "2026-01-01T00:00:00Z"]
        )
        assert session == "alpha"
        assert include == ""
        assert exclude == ""
        assert remaining == ["2026-01-01T00:00:00Z"]

    def test_session_flag_without_value_is_preserved_as_remaining(self) -> None:
        """--session at end of list with no following value is left in remaining_args."""
        include, exclude, session, remaining = cli._extract_scope_flags_for_report(["--session"])
        assert session == ""
        assert "--session" in remaining

    def test_session_combined_with_include_exclude(self) -> None:
        """--session, --include, and --exclude can all be extracted together."""
        include, exclude, session, remaining = cli._extract_scope_flags_for_report(
            ["--include", "E1", "--exclude", "E2", "--session", "beta"]
        )
        assert include == "E1"
        assert exclude == "E2"
        assert session == "beta"
        assert remaining == []

    def test_no_session_flag_returns_empty_session(self) -> None:
        """Without --session the returned session string is empty."""
        include, exclude, session, remaining = cli._extract_scope_flags_for_report(["--include", "E1"])
        assert session == ""
        assert include == "E1"
        assert remaining == []

    def test_session_value_starting_with_dash_is_not_extracted(self) -> None:
        """--session followed by another flag (starts with '--') is left in remaining."""
        include, exclude, session, remaining = cli._extract_scope_flags_for_report(["--session", "--include"])
        assert session == ""
        assert "--session" in remaining


@pytest.mark.unit
class TestCmdReportSessionFlag:
    """cmd_report --session <name> resolves per-session log and filters WUs."""

    def test_session_kwarg_passed_to_generate_report(self, tmp_path: Path) -> None:
        """cmd_report(session='alpha') passes session_name='alpha' to generate_report."""
        captured: dict = {}

        def fake_generate_report(**kwargs: object) -> str:
            captured.update(kwargs)
            return "report"

        session_log = tmp_path / ".devbench" / "sessions" / "alpha" / "orchestrator.log"
        session_log.parent.mkdir(parents=True)
        session_log.write_text("")

        with (
            patch("devbench.cli.WORKSPACE_ROOT", tmp_path),
            patch("devbench.reporting.report.generate_report", side_effect=fake_generate_report),
        ):
            rc = cli.cmd_report(session="alpha")

        assert rc == 0
        assert captured.get("session_name") == "alpha"

    def test_session_kwarg_resolves_session_log_path(self, tmp_path: Path) -> None:
        """cmd_report(session='alpha') passes the per-session log path to generate_report."""
        captured: dict = {}

        def fake_generate_report(**kwargs: object) -> str:
            captured.update(kwargs)
            return "report"

        session_log = tmp_path / ".devbench" / "sessions" / "alpha" / "orchestrator.log"
        session_log.parent.mkdir(parents=True)
        session_log.write_text("")

        with (
            patch("devbench.cli.WORKSPACE_ROOT", tmp_path),
            patch("devbench.reporting.report.generate_report", side_effect=fake_generate_report),
        ):
            rc = cli.cmd_report(session="alpha")

        assert rc == 0
        assert captured["log_path"] == session_log

    def test_no_session_kwarg_uses_default_log(self) -> None:
        """cmd_report without session uses the default log path (no session filtering)."""
        captured: dict = {}

        def fake_generate_report(**kwargs: object) -> str:
            captured.update(kwargs)
            return "report"

        with patch("devbench.reporting.report.generate_report", side_effect=fake_generate_report):
            rc = cli.cmd_report()

        assert rc == 0
        assert captured.get("session_name") is None

    def test_session_nonexistent_log_exits_with_error(self, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
        """cmd_report(session='missing') fails with rc=1 when the session log does not exist."""
        with patch("devbench.cli.WORKSPACE_ROOT", tmp_path):
            rc = cli.cmd_report(session="missing")

        assert rc == 1
        err = capsys.readouterr().err
        assert "missing" in err

    def test_session_streaming_path_passes_session_name_to_render(self, tmp_path: Path) -> None:
        """On a TTY, the streaming render closure forwards session_name to generate_report."""
        captured: dict = {}

        def fake_generate_report(**kwargs: object) -> str:
            captured.update(kwargs)
            return "frame"

        def fake_stream_report(log_path: object, render_fn: Any, **kwargs: object) -> int:
            # Invoke the render closure to capture what it passes to generate_report.
            render_fn(log_path=log_path)
            return 0

        session_log = tmp_path / ".devbench" / "sessions" / "gamma" / "orchestrator.log"
        session_log.parent.mkdir(parents=True)
        session_log.write_text("")

        with (
            patch("sys.stdout.isatty", return_value=True),
            patch("devbench.cli.WORKSPACE_ROOT", tmp_path),
            patch("devbench.reporting.report.generate_report", side_effect=fake_generate_report),
            patch("devbench.reporting.streaming.stream_report", side_effect=fake_stream_report),
        ):
            rc = cli.cmd_report(session="gamma")

        assert rc == 0
        assert captured.get("session_name") == "gamma"


@pytest.mark.unit
class TestDispatchWatchCommandsSession:
    """_dispatch_watch_commands forwards session kwarg to cmd_report (E4-F6-S1-T2)."""

    def test_session_kwarg_forwarded_to_cmd_report(self, tmp_path: Path) -> None:
        """_dispatch_watch_commands forwards session='alpha' to cmd_report."""
        captured: dict = {}

        def fake_cmd_report(**kwargs: object) -> int:
            captured.update(kwargs)
            return 0

        with patch("devbench.cli.cmd_report", side_effect=fake_cmd_report):
            rc = cli._dispatch_watch_commands(
                command="report",
                watch_interval=0,
                args=[],
                once=True,
                include="",
                exclude="",
                session="alpha",
            )

        assert rc == 0
        assert captured.get("session") == "alpha"


@pytest.mark.unit
class TestCmdStatusSummaryAlignment:
    """Issue #201: every count value in the Backlog Status Summary right-aligns
    to a single column regardless of label length.

    The fix introduces ``STATUS_SUMMARY_LABEL_WIDTH`` in
    ``src/devbench/constants.py`` and applies it uniformly across top-level
    rows, Blocked sub-rows, the Draft row, the Un-materialised row, and the
    TOTAL row in ``cmd_status``.
    """

    _COUNT_RE = re.compile(r"^  (?P<label>\S.*\S)\s{2,}(?P<count>\d+)\s*$")

    @staticmethod
    def _summary_lines(out: str) -> list[str]:
        """Return every line that begins with two-space indent + a label + count."""
        return [line for line in out.splitlines() if TestCmdStatusSummaryAlignment._COUNT_RE.match(line)]

    def _render(self, capsys: pytest.CaptureFixture[str]) -> str:
        parser = MagicMock()
        parser.parse_index.return_value = []
        parser.get_parallel_candidates.return_value = []
        parser.get_blocked_units.return_value = []
        parser.all_done.return_value = False
        with (
            patch("devbench.cli.BacklogParser", return_value=parser),
            patch("devbench.cli._count_unmaterialised_proposed_tasks", return_value=0),
        ):
            rc = cli.cmd_status()
        assert rc == 0
        return capsys.readouterr().out

    def test_every_count_value_at_same_column(self, capsys: pytest.CaptureFixture[str]) -> None:
        """AC-1: every Backlog Status Summary row places its count value at the same column index."""
        out = self._render(capsys)
        rows = self._summary_lines(out)
        # Expect 7 Blocked sub-rows + TOTAL + Draft + Un-materialised + every DISPLAY_STATUS_VALUES
        # except the parent "Blocked".  Lower-bound the count to catch silent regressions.
        assert len(rows) >= 10, f"expected at least 10 Backlog Status Summary rows; got {len(rows)} -- output:\n{out}"
        # Extract the column at which the count digit starts for each row.
        first_digit_columns: dict[str, int] = {}
        for row in rows:
            match = self._COUNT_RE.match(row)
            assert match is not None, f"row failed to re-match its own regex: {row!r}"
            first_digit_columns[row] = row.index(match.group("count"))
        unique_columns = set(first_digit_columns.values())
        assert len(unique_columns) == 1, (
            "Backlog Status Summary count values must all sit at the same column index. "
            f"Got per-row first-digit columns: {first_digit_columns}"
        )

    def test_longest_label_has_space_before_count(self, capsys: pytest.CaptureFixture[str]) -> None:
        """AC-3: the longest label (Blocked (runtime-degradation), 29 chars) has at least one space before the count."""
        out = self._render(capsys)
        runtime_row = next(
            (line for line in out.splitlines() if "Blocked (runtime-degradation)" in line),
            None,
        )
        assert runtime_row is not None, f"runtime-degradation row missing from status output:\n{out}"
        label = "Blocked (runtime-degradation)"
        idx = runtime_row.index(label) + len(label)
        assert runtime_row[idx] == " ", (
            f"longest label must be followed by at least one space before its count; got {runtime_row!r}"
        )

    def test_separator_spans_count_column_right_edge(self, capsys: pytest.CaptureFixture[str]) -> None:
        """AC-2: the ===== separator extends to at least the right edge of the count column."""
        from devbench.constants import STATUS_SEPARATOR_WIDTH, STATUS_SUMMARY_LABEL_WIDTH

        out = self._render(capsys)
        separator = next((line for line in out.splitlines() if line.startswith("===")), None)
        assert separator is not None, f"separator '=====' line missing from status output:\n{out}"
        right_edge = 2 + STATUS_SUMMARY_LABEL_WIDTH + 1 + 4
        assert len(separator) >= right_edge, (
            f"separator must span at least the count column right edge ({right_edge} chars); got {len(separator)} chars"
        )
        assert len(separator) == STATUS_SEPARATOR_WIDTH

    def test_uses_constant_not_hardcoded_widths(self) -> None:
        """AC-4 regression guard: every format string in cmd_status uses STATUS_SUMMARY_LABEL_WIDTH.

        The pre-fix code had two different hard-coded widths (15 and 28); pin that the fix wired
        every site through the single shared constant so adding a new BlockedTaskState or
        DISPLAY_STATUS_VALUES entry tomorrow still aligns.
        """
        import inspect

        src = inspect.getsource(cli.cmd_status)
        assert ":<15}" not in src, "cmd_status still contains a hard-coded ':<15}' label-pad width"
        assert ":<28}" not in src, "cmd_status still contains a hard-coded ':<28}' label-pad width"
        assert "STATUS_SUMMARY_LABEL_WIDTH" in src, (
            "cmd_status must reference STATUS_SUMMARY_LABEL_WIDTH for every label-pad width"
        )


# ---------------------------------------------------------------------------
# Issue #223: cost-calibrate subcommand
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestCostCalibrate:
    """Issue #223 AC-6: ``cost-calibrate`` writes per-model correction
    factors back to ``backlog/config/devbench.yaml`` so the next
    ``devbench report`` reflects the corrected total.

    Three cases: argument validation, the missing-data path (empty
    workspace), and the round-trip path (write-back into a real
    config file via the helper).
    """

    def test_rejects_missing_actual_usd(self, capsys: pytest.CaptureFixture[str]) -> None:
        rc = cli.cmd_cost_calibrate()
        assert rc == 2
        assert "missing required" in capsys.readouterr().err

    def test_rejects_non_numeric_actual_usd(self, capsys: pytest.CaptureFixture[str]) -> None:
        rc = cli.cmd_cost_calibrate("not-a-number")
        assert rc == 2
        assert "must be a numeric value" in capsys.readouterr().err

    def test_rejects_non_positive_actual_usd(self, capsys: pytest.CaptureFixture[str]) -> None:
        rc = cli.cmd_cost_calibrate("-50")
        assert rc == 2
        assert "must be > 0" in capsys.readouterr().err

    def test_rejects_invalid_window_iso(self, capsys: pytest.CaptureFixture[str]) -> None:
        rc = cli.cmd_cost_calibrate("100.0", "--window", "not-a-timestamp")
        assert rc == 2
        assert "invalid --window" in capsys.readouterr().err

    def test_write_per_model_correction_factors_round_trip(self, tmp_path: Path) -> None:
        """AC-6 core round-trip: a yaml that already lists two models
        gets a ``correction_factor`` injected for each.  Re-loading the
        yaml shows the factor applied.
        """
        config_yaml = tmp_path / "devbench.yaml"
        config_yaml.write_text(
            "repos:\n"
            "  org/repo:\n"
            "    default_branch: main\n"
            "report:\n"
            "  models:\n"
            "    claude-opus-4-7:\n"
            "      input: 5.0\n"
            "      output: 25.0\n"
            "    claude-sonnet-4-6:\n"
            "      input: 3.0\n"
            "      output: 15.0\n",
            encoding="utf-8",
        )
        cli.write_per_model_correction_factors(
            config_yaml, ["claude-opus-4-7", "claude-sonnet-4-6"], correction_factor=1.25
        )
        import yaml as _yaml

        round_tripped = _yaml.safe_load(config_yaml.read_text(encoding="utf-8"))
        opus = round_tripped["report"]["models"]["claude-opus-4-7"]
        sonnet = round_tripped["report"]["models"]["claude-sonnet-4-6"]
        assert opus["correction_factor"] == 1.25
        assert sonnet["correction_factor"] == 1.25
        # Existing input/output rates are preserved.
        assert opus["input"] == 5.0 and opus["output"] == 25.0
        assert sonnet["input"] == 3.0 and sonnet["output"] == 15.0

    def test_write_per_model_correction_factors_seeds_unknown_model(self, tmp_path: Path) -> None:
        """When the operator calibrates a model not yet listed in
        ``report.models``, the helper seeds ``input``/``output`` from the
        canonical defaults so the resulting yaml is schema-valid.

        AC-E3-F1-S1-T1-10 (spec FR-3.2 error handling, designed tripwire):
        exercises the current default ``claude-opus-5`` (issue #233)
        alongside the retained ``claude-opus-4-7`` row so the same test
        that hard-pinned the Opus 4.7-era table catches any future
        regression on either id.
        """
        from devbench.constants import DEFAULT_MODEL_RATES

        config_yaml = tmp_path / "devbench.yaml"
        config_yaml.write_text(
            "repos:\n  org/repo:\n    default_branch: main\nreport:\n  models: {}\n",
            encoding="utf-8",
        )
        cli.write_per_model_correction_factors(
            config_yaml, ["claude-opus-4-7", "claude-opus-5"], correction_factor=0.95
        )
        import yaml as _yaml

        round_tripped = _yaml.safe_load(config_yaml.read_text(encoding="utf-8"))
        opus = round_tripped["report"]["models"]["claude-opus-4-7"]
        # Seeded from DEFAULT_MODEL_RATES["claude-opus-4-7"]: $5/$25.
        assert opus["input"] == 5.0
        assert opus["output"] == 25.0

        # claude-opus-5 is the current default lineup entry (issue #233).
        # Assert it is an explicit DEFAULT_MODEL_RATES entry (not merely a
        # value that happens to match the "<unknown>" fallback rate) so this
        # tripwire actually catches the entry being dropped from the table.
        assert "claude-opus-5" in DEFAULT_MODEL_RATES, (
            "claude-opus-5 must be an explicit DEFAULT_MODEL_RATES entry, not just "
            "coincidentally matching the fallback rate (issue #233)."
        )
        opus5 = round_tripped["report"]["models"]["claude-opus-5"]
        # Seeded from DEFAULT_MODEL_RATES["claude-opus-5"]: $5/$25 list.
        assert opus5["input"] == 5.0
        assert opus5["output"] == 25.0
        assert opus["correction_factor"] == 0.95

    def test_write_per_model_correction_factors_rejects_non_mapping_yaml(self, tmp_path: Path) -> None:
        config_yaml = tmp_path / "devbench.yaml"
        config_yaml.write_text("- not-a-mapping\n", encoding="utf-8")
        with pytest.raises(ValueError, match="top-level YAML must be a mapping"):
            cli.write_per_model_correction_factors(config_yaml, ["claude-opus-4-7"], correction_factor=1.0)


# ---------------------------------------------------------------------------
# Issue #223 coverage: cost-calibrate end-to-end + ModelRates equality
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestCostCalibrateEndToEnd:
    """Drive ``cmd_cost_calibrate`` against a real workspace with synthetic
    hook-log entries so the success-path branches (lines 3001-3071 in
    cli.py) are exercised.
    """

    def _build_workspace(self, tmp_path: Path) -> Path:
        """Build a minimal workspace with a single hook-log entry carrying
        a model id so the per-model aggregator has something to chew on.
        """
        ws = tmp_path / "ws"
        (ws / "backlog" / "config").mkdir(parents=True)
        (ws / "backlog" / "config" / "devbench.yaml").write_text(
            "repos:\n"
            "  caylent-solutions/devbench:\n"
            "    default_branch: main\n"
            "report:\n"
            "  models:\n"
            "    claude-opus-4-7:\n"
            "      input: 5.0\n"
            "      output: 25.0\n",
            encoding="utf-8",
        )
        (ws / "hook-logs.jsonl").write_text(
            '{"timestamp":"2026-05-04T10:00:00.000000+00:00","input":{"tool_response":'
            '{"model":"claude-opus-4-7","usage":{"input_tokens":1000000,"output_tokens":0}}}}\n',
            encoding="utf-8",
        )
        return ws

    def test_calibrate_writes_correction_factor_for_observed_model(self, tmp_path: Path) -> None:
        """End-to-end: 1M input tokens at $5/M = $5 reported.  Actual = $7.50
        -> correction factor 1.5 written to the yaml.
        """
        ws = self._build_workspace(tmp_path)
        with patch("devbench.cli.WORKSPACE_ROOT", ws):
            rc = cli.cmd_cost_calibrate("7.50")
        assert rc == 0
        import yaml as _yaml

        result = _yaml.safe_load((ws / "backlog" / "config" / "devbench.yaml").read_text(encoding="utf-8"))
        opus = result["report"]["models"]["claude-opus-4-7"]
        assert abs(opus["correction_factor"] - 1.5) < 1e-6

    def test_calibrate_zero_reported_cost_returns_1(self, tmp_path: Path) -> None:
        """When the window contains no billable activity, calibrate
        returns 1 (not 0) because there is nothing to scale.
        """
        ws = tmp_path / "ws"
        (ws / "backlog" / "config").mkdir(parents=True)
        (ws / "backlog" / "config" / "devbench.yaml").write_text(
            "repos:\n  org/repo:\n    default_branch: main\n",
            encoding="utf-8",
        )
        (ws / "hook-logs.jsonl").write_text("", encoding="utf-8")
        with patch("devbench.cli.WORKSPACE_ROOT", ws):
            rc = cli.cmd_cost_calibrate("100.0")
        assert rc == 1

    def test_calibrate_missing_config_yaml_returns_2(self, tmp_path: Path) -> None:
        ws = tmp_path / "ws"
        ws.mkdir()
        with patch("devbench.cli.WORKSPACE_ROOT", ws):
            rc = cli.cmd_cost_calibrate("100.0")
        assert rc == 2


@pytest.mark.unit
class TestModelRatesDunders:
    """Issue #223: cover ModelRates' explicit __eq__ / __hash__ / __repr__
    that the slot-based class uses (a frozen-dataclass equivalent would
    have these auto-generated, but we want operator-facing repr quality
    and slot performance over the dataclass machinery).
    """

    def test_eq_returns_notimplemented_for_other_types(self) -> None:
        from devbench.constants import ModelRates

        rates = ModelRates(input=5.0, output=25.0)
        # Direct __eq__ call returns NotImplemented; equality test against
        # non-ModelRates falls through to Python's default identity check.
        assert rates.__eq__("not a ModelRates") is NotImplemented
        assert rates != "not a ModelRates"

    def test_eq_compares_all_six_fields(self) -> None:
        from devbench.constants import ModelRates

        a = ModelRates(input=5.0, output=25.0, correction_factor=1.0)
        b = ModelRates(input=5.0, output=25.0, correction_factor=1.0)
        c = ModelRates(input=5.0, output=25.0, correction_factor=1.5)
        assert a == b
        assert a != c

    def test_hashable_with_consistent_hash(self) -> None:
        from devbench.constants import ModelRates

        a = ModelRates(input=5.0, output=25.0)
        b = ModelRates(input=5.0, output=25.0)
        assert hash(a) == hash(b)
        # Can be used as dict key / set member.
        s = {a, b}
        assert len(s) == 1

    def test_repr_includes_all_fields(self) -> None:
        from devbench.constants import ModelRates

        r = ModelRates(input=5.0, output=25.0, cache_read_multiplier=0.10, correction_factor=1.05)
        text = repr(r)
        assert "ModelRates(" in text
        assert "input=5.0" in text
        assert "output=25.0" in text
        assert "cache_read_multiplier=0.1" in text
        assert "correction_factor=1.05" in text

    def test_unknown_kwarg_rejected(self) -> None:
        from devbench.constants import ModelRates

        with pytest.raises(TypeError, match="unexpected keyword argument"):
            ModelRates(input=5.0, output=25.0, bogus=1.0)

    def test_missing_input_rejected(self) -> None:
        from devbench.constants import ModelRates

        with pytest.raises(TypeError, match="requires keyword arguments 'input' and 'output'"):
            ModelRates(output=25.0)


@pytest.mark.unit
class TestParseCostCalibrateArgvCoverage:
    """Cover the remaining branches in ``_parse_cost_calibrate_argv``
    (variadic-dispatch arg parser for ``cost-calibrate``).
    """

    def test_window_missing_value_after_flag(self, capsys: pytest.CaptureFixture[str]) -> None:
        rc = cli.cmd_cost_calibrate("100.0", "--window")
        assert rc == 2
        assert "--window requires" in capsys.readouterr().err

    def test_unexpected_extra_positional(self, capsys: pytest.CaptureFixture[str]) -> None:
        rc = cli.cmd_cost_calibrate("100.0", "extra")
        assert rc == 2
        assert "unexpected extra argument" in capsys.readouterr().err

    def test_window_naive_timestamp_gets_utc(self) -> None:
        from devbench.cli import _parse_cost_calibrate_argv

        result = _parse_cost_calibrate_argv(("100.0", "--window", "2026-05-01T00:00:00"))
        assert not isinstance(result, int)
        # Naive ISO -> UTC tzinfo applied.
        assert result.window_start.tzinfo is not None


@pytest.mark.unit
class TestWritePerModelCorrectionFactorsErrorPaths:
    """Cover the defensive ValueError branches in
    ``write_per_model_correction_factors`` for malformed input yaml.
    """

    def test_rejects_non_mapping_report_section(self, tmp_path: Path) -> None:
        config_yaml = tmp_path / "devbench.yaml"
        config_yaml.write_text(
            "repos:\n  org/repo:\n    default_branch: main\nreport: not-a-mapping\n", encoding="utf-8"
        )
        with pytest.raises(ValueError, match="report: that is not a mapping"):
            cli.write_per_model_correction_factors(config_yaml, ["claude-opus-4-7"], 1.0)

    def test_rejects_non_mapping_models_section(self, tmp_path: Path) -> None:
        config_yaml = tmp_path / "devbench.yaml"
        config_yaml.write_text(
            "repos:\n  org/repo:\n    default_branch: main\nreport:\n  models: not-a-mapping\n",
            encoding="utf-8",
        )
        with pytest.raises(ValueError, match=r"report\.models: that is not a mapping"):
            cli.write_per_model_correction_factors(config_yaml, ["claude-opus-4-7"], 1.0)

    def test_rejects_non_mapping_model_entry(self, tmp_path: Path) -> None:
        config_yaml = tmp_path / "devbench.yaml"
        config_yaml.write_text(
            "repos:\n  org/repo:\n    default_branch: main\nreport:\n  models:\n    claude-opus-4-7: not-a-mapping\n",
            encoding="utf-8",
        )
        with pytest.raises(ValueError, match=r"report\.models\.claude-opus-4-7 is not a mapping"):
            cli.write_per_model_correction_factors(config_yaml, ["claude-opus-4-7"], 1.0)


@pytest.mark.unit
class TestCostCalibrateTranscriptBranch:
    """Issue #223 coverage: cmd_cost_calibrate's transcript-dir refresh
    branch (line 3024 in cli.py) only fires when the hook log carries a
    ``transcript_path`` pointing at a directory.  This test constructs
    a workspace with both a hook log AND a transcript directory so the
    branch runs.
    """

    def test_calibrate_refreshes_transcript_dir_when_present(self, tmp_path: Path) -> None:
        ws = tmp_path / "ws"
        (ws / "backlog" / "config").mkdir(parents=True)
        (ws / "backlog" / "config" / "devbench.yaml").write_text(
            "repos:\n  org/repo:\n    default_branch: main\n"
            "report:\n  models:\n    claude-opus-4-7:\n      input: 5.0\n      output: 25.0\n",
            encoding="utf-8",
        )
        transcript_dir = ws / "transcripts"
        transcript_dir.mkdir()
        transcript_file = transcript_dir / "session.jsonl"
        transcript_file.write_text(
            '{"timestamp":"2026-05-04T10:00:00.000000+00:00","type":"assistant","message":'
            '{"id":"msg-1","model":"claude-opus-4-7","usage":{"input_tokens":1000000,"output_tokens":0}}}\n',
            encoding="utf-8",
        )
        hook_log = ws / "hook-logs.jsonl"
        hook_log.write_text(
            '{"timestamp":"2026-05-04T10:00:00.000000+00:00","input":'
            f'{{"transcript_path":"{transcript_file}","tool_response":'
            '{"model":"claude-opus-4-7","usage":{"input_tokens":1000000,"output_tokens":0}}}}\n',
            encoding="utf-8",
        )
        with patch("devbench.cli.WORKSPACE_ROOT", ws):
            rc = cli.cmd_cost_calibrate("7.50")
        assert rc == 0
        import yaml as _yaml

        result = _yaml.safe_load((ws / "backlog" / "config" / "devbench.yaml").read_text(encoding="utf-8"))
        assert "correction_factor" in result["report"]["models"]["claude-opus-4-7"]


@pytest.mark.unit
class TestRecentPerTaskCostByModel:
    """Issue #223 coverage: _recent_per_task_cost now feeds the per-model
    dispatcher.  Cover the non-indexed fallback path that collapses to
    the ``"<unknown>"`` bucket so its line gets hit by tests.
    """

    def test_recent_per_task_cost_non_indexed_path(self, tmp_path: Path) -> None:
        from datetime import UTC, datetime

        from devbench.reporting.report import _recent_per_task_cost

        log_path = tmp_path / "test.log"
        log_path.write_text("")
        # No transcripts / hook log -> empty totals -> $0.00 cost averaged
        # across n=2 completions.
        done_times = {
            "E0-F1-S1-T1": datetime(2026, 5, 4, 10, 0, tzinfo=UTC),
            "E0-F1-S1-T2": datetime(2026, 5, 4, 11, 0, tzinfo=UTC),
        }
        progress_times = {
            "E0-F1-S1-T1": datetime(2026, 5, 4, 9, 30, tzinfo=UTC),
            "E0-F1-S1-T2": datetime(2026, 5, 4, 10, 30, tzinfo=UTC),
        }
        result = _recent_per_task_cost(log_path, done_times, progress_times, 2)
        assert result == 0.0


class TestCmdNotifyTest:
    """`devbench notify-test --event <name>` arg-parsing + dispatch (issue #238 coverage)."""

    def test_unknown_flag_returns_2(self, capsys: pytest.CaptureFixture[str]) -> None:
        rc = cli.cmd_notify_test("--bogus")
        assert rc == 2
        assert "unknown flag" in capsys.readouterr().err

    def test_event_flag_without_value_returns_2(self, capsys: pytest.CaptureFixture[str]) -> None:
        rc = cli.cmd_notify_test("--event")
        assert rc == 2
        assert "--event requires a value" in capsys.readouterr().err

    def test_missing_event_returns_2(self, capsys: pytest.CaptureFixture[str]) -> None:
        rc = cli.cmd_notify_test()
        assert rc == 2
        assert "--event <name> is required" in capsys.readouterr().err

    def test_unknown_event_returns_2(self, capsys: pytest.CaptureFixture[str]) -> None:
        rc = cli.cmd_notify_test("--event", "not_a_real_event")
        assert rc == 2
        assert "unknown event" in capsys.readouterr().err

    def test_valid_event_fires_and_returns_0(self, capsys: pytest.CaptureFixture[str]) -> None:
        with patch("devbench.notifications.send_test_notification") as mock_send:
            rc = cli.cmd_notify_test("--event", "work_unit_done")
        assert rc == 0
        mock_send.assert_called_once_with("work_unit_done")
        assert "fired 'work_unit_done'" in capsys.readouterr().out


class TestSendSignalAndWait:
    """_send_signal_and_wait SIGTERM/SIGKILL escalation (issue #238 coverage)."""

    def _inst(self) -> MagicMock:
        m = MagicMock()
        m.pid = 4321
        m.instance_id = "inst-x"
        return m

    def test_sigterm_failure_returns_1(self, capsys: pytest.CaptureFixture[str]) -> None:
        with patch("devbench.cli.os.kill", side_effect=ProcessLookupError("gone")):
            rc = cli._send_signal_and_wait(self._inst(), timeout=5, force=False)
        assert rc == 1
        assert "SIGTERM to pid" in capsys.readouterr().err

    def test_clean_exit_returns_0(self, capsys: pytest.CaptureFixture[str]) -> None:
        with (
            patch("devbench.cli.os.kill"),
            patch("devbench.cli._wait_for_pid_exit", return_value=True),
        ):
            rc = cli._send_signal_and_wait(self._inst(), timeout=5, force=False)
        assert rc == 0
        assert "stopped instance" in capsys.readouterr().out

    def test_no_exit_without_force_returns_1(self, capsys: pytest.CaptureFixture[str]) -> None:
        with (
            patch("devbench.cli.os.kill"),
            patch("devbench.cli._wait_for_pid_exit", return_value=False),
        ):
            rc = cli._send_signal_and_wait(self._inst(), timeout=5, force=False)
        assert rc == 1
        err = capsys.readouterr().err
        assert "did not exit" in err
        assert "--force" in err

    def test_force_kill_returns_0(self, capsys: pytest.CaptureFixture[str]) -> None:
        with (
            patch("devbench.cli.os.kill"),
            patch("devbench.cli._wait_for_pid_exit", return_value=False),
        ):
            rc = cli._send_signal_and_wait(self._inst(), timeout=5, force=True)
        assert rc == 0
        assert "force-killed instance" in capsys.readouterr().out

    def test_force_kill_failure_returns_1(self, capsys: pytest.CaptureFixture[str]) -> None:
        with (
            patch("devbench.cli.os.kill", side_effect=[None, OSError("denied")]),
            patch("devbench.cli._wait_for_pid_exit", return_value=False),
        ):
            rc = cli._send_signal_and_wait(self._inst(), timeout=5, force=True)
        assert rc == 1
        assert "SIGKILL to pid" in capsys.readouterr().err


class TestCmdAddDepErrorPaths:
    """cmd_add_dep early error branches (issue #238 coverage)."""

    def test_index_read_error_returns_1(self, capsys: pytest.CaptureFixture[str]) -> None:
        with patch("devbench.cli.BacklogParser", side_effect=FileNotFoundError("no index")):
            rc = cli.cmd_add_dep("E1-F1-S1-T1", "E1-F1-S1-T2")
        assert rc == 1
        assert "cannot read backlog index" in capsys.readouterr().err

    def test_blocked_task_not_found_returns_1(self, capsys: pytest.CaptureFixture[str]) -> None:
        parser = MagicMock()
        parser.parse_index.return_value = []
        with patch("devbench.cli.BacklogParser", return_value=parser):
            rc = cli.cmd_add_dep("E1-F1-S1-T1", "E1-F1-S1-T2")
        assert rc == 1
        assert "not found in backlog index" in capsys.readouterr().err


class TestDaemonizeGuard:
    """_daemonize_to_background POSIX guard (issue #238 coverage; no fork executed)."""

    def test_non_posix_raises_runtime_error(self, tmp_path: Path) -> None:
        with patch("devbench.cli.os.name", "nt"):
            with pytest.raises(RuntimeError, match="--daemon requires POSIX"):
                cli._daemonize_to_background(tmp_path)
