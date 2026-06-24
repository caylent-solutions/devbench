"""Tests for cmd_set_status done-refusal (E8-F1-S1-T1).

Verifies that set-status <id> done and set-status --include <tokens> done both
exit with rc=1, emit the verbatim error to stderr, and write neither the
work-unit file nor the backlog index.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from devbench import cli
from devbench.backlog.work_unit import WorkUnit, WorkUnitStatus, WorkUnitType

_VERBATIM_SET_STATUS_ERROR = (
    "ERROR: 'set-status done' is not allowed; completion must go through"
    " 'mark-done' (enforces the done-gate: all required judges passed)"
)


def _make_task(unit_id: str, status: WorkUnitStatus = WorkUnitStatus.IN_QUEUE) -> WorkUnit:
    return WorkUnit(
        id=unit_id,
        title="Test Task",
        status=status,
        unit_type=WorkUnitType.TASK,
        file_path=Path(f"backlog/{unit_id}.md"),
        repo="caylent-solutions/git-repo",
        dependencies=[],
    )


def _write_wu(backlog_dir: Path, unit_id: str, status: str = "in-queue") -> Path:
    wu = backlog_dir / f"{unit_id}.md"
    wu.write_text(f"# {unit_id}: Test Task\n\n## Status: {status}\n\n## Comments\n\n")
    return wu


def _write_index(tmp_path: Path, unit_id: str, status: str = "in-queue") -> Path:
    index = tmp_path / "BACKLOG.md"
    index.write_text(
        "# Backlog\n\n"
        "## Full Work Unit Index\n\n"
        "| ID | Title | Type | Status | Dependencies | Repo | File Path |\n"
        "|-----|-------|------|--------|-------------|------|-----------|\n"
        f"| {unit_id} | Test Task | Task | {status} | None | git-repo |"
        f" `backlog/{unit_id}.md` |\n"
    )
    return index


@pytest.mark.unit
class TestCmdSetStatusSingleRefusesDone:
    """set-status <id> done exits 1 with verbatim stderr, writes nothing."""

    def test_exits_rc1_on_done(
        self,
        backlog_dir: Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """set-status <id> done returns rc=1."""
        unit_id = "E0-F1-S1-T1"
        wu = _write_wu(backlog_dir, unit_id)
        _write_index(backlog_dir.parent, unit_id)

        unit = _make_task(unit_id)
        mock_parser = MagicMock()
        mock_parser.parse_index.return_value = [unit]

        with (
            patch("devbench.cli.BacklogParser", return_value=mock_parser),
            patch("devbench.cli.BACKLOG_ROOT", backlog_dir.parent),
            patch("devbench.cli.BACKLOG_INDEX", backlog_dir.parent / "BACKLOG.md"),
        ):
            rc = cli.cmd_set_status(unit_id, "done")

        assert rc == 1
        _ = wu

    def test_emits_verbatim_error_to_stderr(
        self,
        backlog_dir: Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """set-status <id> done emits the verbatim error string to stderr."""
        unit_id = "E0-F1-S1-T1"
        _write_wu(backlog_dir, unit_id)
        _write_index(backlog_dir.parent, unit_id)

        unit = _make_task(unit_id)
        mock_parser = MagicMock()
        mock_parser.parse_index.return_value = [unit]

        with (
            patch("devbench.cli.BacklogParser", return_value=mock_parser),
            patch("devbench.cli.BACKLOG_ROOT", backlog_dir.parent),
            patch("devbench.cli.BACKLOG_INDEX", backlog_dir.parent / "BACKLOG.md"),
        ):
            cli.cmd_set_status(unit_id, "done")

        err = capsys.readouterr().err
        assert _VERBATIM_SET_STATUS_ERROR in err

    def test_writes_neither_file_on_done(
        self,
        backlog_dir: Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """set-status <id> done must not modify the work-unit file or the index."""
        unit_id = "E0-F1-S1-T1"
        wu = _write_wu(backlog_dir, unit_id)
        index = _write_index(backlog_dir.parent, unit_id)
        original_wu = wu.read_text()
        original_index = index.read_text()

        unit = _make_task(unit_id)
        mock_parser = MagicMock()
        mock_parser.parse_index.return_value = [unit]

        with (
            patch("devbench.cli.BacklogParser", return_value=mock_parser),
            patch("devbench.cli.BACKLOG_ROOT", backlog_dir.parent),
            patch("devbench.cli.BACKLOG_INDEX", backlog_dir.parent / "BACKLOG.md"),
        ):
            cli.cmd_set_status(unit_id, "done")

        assert wu.read_text() == original_wu
        assert index.read_text() == original_index

    def test_done_titlecase_is_also_refused(
        self,
        backlog_dir: Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """Title-case 'Done' resolves to 'done' and is also refused with rc=1."""
        unit_id = "E0-F1-S1-T1"
        _write_wu(backlog_dir, unit_id)
        _write_index(backlog_dir.parent, unit_id)

        unit = _make_task(unit_id)
        mock_parser = MagicMock()
        mock_parser.parse_index.return_value = [unit]

        with (
            patch("devbench.cli.BacklogParser", return_value=mock_parser),
            patch("devbench.cli.BACKLOG_ROOT", backlog_dir.parent),
            patch("devbench.cli.BACKLOG_INDEX", backlog_dir.parent / "BACKLOG.md"),
        ):
            rc = cli.cmd_set_status(unit_id, "Done")

        assert rc == 1
        assert _VERBATIM_SET_STATUS_ERROR in capsys.readouterr().err


@pytest.mark.unit
class TestCmdSetStatusBulkRefusesDone:
    """set-status --include <tokens> done exits 1 with verbatim stderr, writes nothing."""

    def test_bulk_exits_rc1_on_done(
        self,
        backlog_dir: Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """Bulk set-status with done status returns rc=1."""
        unit_id = "E0-F1-S1-T1"
        _write_wu(backlog_dir, unit_id)
        _write_index(backlog_dir.parent, unit_id)

        unit = _make_task(unit_id)
        mock_parser = MagicMock()
        mock_parser.parse_index.return_value = [unit]
        mock_mgr = MagicMock()

        with (
            patch("devbench.cli.BacklogParser", return_value=mock_parser),
            patch("devbench.cli.BACKLOG_ROOT", backlog_dir.parent),
            patch("devbench.cli.BACKLOG_INDEX", backlog_dir.parent / "BACKLOG.md"),
            patch("devbench.cli.BacklogManager", return_value=mock_mgr),
        ):
            rc = cli.cmd_set_status("--include", "E0", "done")

        assert rc == 1

    def test_bulk_emits_verbatim_error_to_stderr(
        self,
        backlog_dir: Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """Bulk set-status with done status emits verbatim error to stderr."""
        unit_id = "E0-F1-S1-T1"
        _write_wu(backlog_dir, unit_id)
        _write_index(backlog_dir.parent, unit_id)

        unit = _make_task(unit_id)
        mock_parser = MagicMock()
        mock_parser.parse_index.return_value = [unit]
        mock_mgr = MagicMock()

        with (
            patch("devbench.cli.BacklogParser", return_value=mock_parser),
            patch("devbench.cli.BACKLOG_ROOT", backlog_dir.parent),
            patch("devbench.cli.BACKLOG_INDEX", backlog_dir.parent / "BACKLOG.md"),
            patch("devbench.cli.BacklogManager", return_value=mock_mgr),
        ):
            cli.cmd_set_status("--include", "E0", "done")

        err = capsys.readouterr().err
        assert _VERBATIM_SET_STATUS_ERROR in err

    def test_bulk_writes_no_files_on_done(
        self,
        backlog_dir: Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """Bulk set-status with done status must not call force_status or bulk_set_status."""
        unit_id = "E0-F1-S1-T1"
        _write_wu(backlog_dir, unit_id)
        _write_index(backlog_dir.parent, unit_id)

        unit = _make_task(unit_id)
        mock_parser = MagicMock()
        mock_parser.parse_index.return_value = [unit]
        mock_mgr = MagicMock()

        with (
            patch("devbench.cli.BacklogParser", return_value=mock_parser),
            patch("devbench.cli.BACKLOG_ROOT", backlog_dir.parent),
            patch("devbench.cli.BACKLOG_INDEX", backlog_dir.parent / "BACKLOG.md"),
            patch("devbench.cli.BacklogManager", return_value=mock_mgr),
        ):
            cli.cmd_set_status("--include", "E0", "done")

        mock_mgr.force_status.assert_not_called()
        mock_mgr.bulk_set_status.assert_not_called()
