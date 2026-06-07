"""Tests for cmd_claim pre-claim target-repo guard (issue #241, AC-241-1, AC-241a-1).

Verifies that cmd_claim resolves the target repo before acquiring the lock,
writes the [BLOCKED_TARGET_REPO_UNRESOLVED] marker idempotently, sets the
unit to blocked, and returns exit code 44 (CLAIM_BLOCKED_PRECLAIM) with no
lock acquired.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from devbench import cli
from devbench.backlog.work_unit import WorkUnit, WorkUnitStatus, WorkUnitType

pytestmark = pytest.mark.unit

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_BLOCKED_MARKER = "[BLOCKED_TARGET_REPO_UNRESOLVED]"


def _make_unit(
    backlog_dir: Path,
    *,
    unit_id: str = "E0-F1-S1-T2",
    status: str = "in-queue",
    repo: str = "caylent-solutions/git-repo",
) -> tuple[WorkUnit, Path]:
    """Write a minimal work-unit file and return (WorkUnit, Path)."""
    wu_file = backlog_dir / f"{unit_id}.md"
    wu_file.write_text(
        f"# {unit_id}: Test\n\n## Status: {status}\n\n## Changes Manifest\n\n"
        "| File | Change |\n|------|--------|\n| src/x.py | modify |\n\n"
        "## Comments\n\n",
        encoding="utf-8",
    )
    unit = WorkUnit(
        id=unit_id,
        title="Test",
        status=WorkUnitStatus.IN_QUEUE,
        unit_type=WorkUnitType.TASK,
        file_path=Path(f"backlog/{unit_id}.md"),
        repo=repo,
        dependencies=[],
    )
    return unit, wu_file


class _FakeFlock:
    """Passthrough flock that records entry; used to assert lock was NOT acquired."""

    def __init__(self, root: Path, timeout_seconds: int = 30, *, entered: list[bool] | None = None) -> None:
        self._entered = entered

    def __enter__(self) -> None:
        if self._entered is not None:
            self._entered.append(True)

    def __exit__(self, *args: object) -> None:
        pass


def _make_noop_flock(entered: list[bool] | None = None) -> object:
    """Return a callable that produces _FakeFlock instances."""

    def _flock(root: Path, timeout_seconds: int = 30) -> _FakeFlock:
        return _FakeFlock(root, timeout_seconds, entered=entered)

    return _flock


def _make_backlog_index(tmp_path: Path, unit_id: str, status: str = "in-queue") -> Path:
    """Write a minimal BACKLOG.md containing the given unit_id and return its path."""
    idx = tmp_path / "BACKLOG.md"
    idx.write_text(
        "# Backlog\n\n## Full Work Unit Index\n\n"
        "| ID | Title | Type | Status | Dependencies | Repo | File Path |\n"
        "|----|-------|------|--------|--------------|------|-----------|\n"
        f"| {unit_id} | Test | Task | {status} | none | git-repo | `backlog/{unit_id}.md` |\n",
        encoding="utf-8",
    )
    return idx


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestCmdClaimUnresolvableRepo:
    """AC-241-1: cmd_claim guard for unresolvable target repo."""

    def test_unresolvable_repo_returns_44(
        self,
        backlog_dir: Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """Claiming a WU with an unresolvable repo returns exit code 44."""
        unit, wu_file = _make_unit(backlog_dir, repo="unknown-org/no-such-repo")
        backlog_index = _make_backlog_index(backlog_dir.parent, unit.id)
        mock_parser = MagicMock()
        mock_parser.parse_index.return_value = [unit]
        entered: list[bool] = []
        flock_factory = _make_noop_flock(entered=entered)
        mock_mgr = MagicMock()

        with (
            patch("devbench.cli.BacklogParser", return_value=mock_parser),
            patch("devbench.cli.BACKLOG_ROOT", backlog_dir.parent),
            patch("devbench.cli.BACKLOG_INDEX", backlog_index),
            patch("devbench.cli.WORKSPACE_ROOT", backlog_dir.parent),
            patch("devbench.cli.flock_backlog", flock_factory),
            patch("devbench.cli.BacklogManager", return_value=mock_mgr),
        ):
            rc = cli.cmd_claim(unit.id)

        assert rc == 44

    def test_unresolvable_repo_no_lock_acquired(
        self,
        backlog_dir: Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """No flock is acquired when the repo is unresolvable (lock-free fast exit)."""
        unit, wu_file = _make_unit(backlog_dir, repo="unknown-org/no-such-repo")
        backlog_index = _make_backlog_index(backlog_dir.parent, unit.id)
        mock_parser = MagicMock()
        mock_parser.parse_index.return_value = [unit]
        entered: list[bool] = []
        flock_factory = _make_noop_flock(entered=entered)
        mock_mgr = MagicMock()

        with (
            patch("devbench.cli.BacklogParser", return_value=mock_parser),
            patch("devbench.cli.BACKLOG_ROOT", backlog_dir.parent),
            patch("devbench.cli.BACKLOG_INDEX", backlog_index),
            patch("devbench.cli.WORKSPACE_ROOT", backlog_dir.parent),
            patch("devbench.cli.flock_backlog", flock_factory),
            patch("devbench.cli.BacklogManager", return_value=mock_mgr),
        ):
            cli.cmd_claim(unit.id)

        assert not entered, "flock_backlog must NOT be entered when repo is unresolvable"

    def test_unresolvable_repo_writes_blocked_marker(
        self,
        backlog_dir: Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """Claiming an unresolvable-repo WU writes the verbatim marker in the Comments section."""
        unit, wu_file = _make_unit(backlog_dir, repo="unknown-org/no-such-repo")
        backlog_index = _make_backlog_index(backlog_dir.parent, unit.id)
        mock_parser = MagicMock()
        mock_parser.parse_index.return_value = [unit]
        mock_mgr = MagicMock()

        with (
            patch("devbench.cli.BacklogParser", return_value=mock_parser),
            patch("devbench.cli.BACKLOG_ROOT", backlog_dir.parent),
            patch("devbench.cli.BACKLOG_INDEX", backlog_index),
            patch("devbench.cli.WORKSPACE_ROOT", backlog_dir.parent),
            patch("devbench.cli.flock_backlog", _make_noop_flock()),
            patch("devbench.cli.BacklogManager", return_value=mock_mgr),
        ):
            cli.cmd_claim(unit.id)

        content = wu_file.read_text(encoding="utf-8")
        assert _BLOCKED_MARKER in content
        assert "unknown-org/no-such-repo" in content

    def test_unresolvable_repo_calls_force_status_blocked(
        self,
        backlog_dir: Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """cmd_claim calls BacklogManager.force_status with STATUS_BLOCKED when repo is unresolvable."""
        from devbench.constants import STATUS_BLOCKED

        unit, wu_file = _make_unit(backlog_dir, repo="unknown-org/no-such-repo")
        backlog_index = _make_backlog_index(backlog_dir.parent, unit.id)
        mock_parser = MagicMock()
        mock_parser.parse_index.return_value = [unit]
        mock_mgr = MagicMock()

        with (
            patch("devbench.cli.BacklogParser", return_value=mock_parser),
            patch("devbench.cli.BACKLOG_ROOT", backlog_dir.parent),
            patch("devbench.cli.BACKLOG_INDEX", backlog_index),
            patch("devbench.cli.WORKSPACE_ROOT", backlog_dir.parent),
            patch("devbench.cli.flock_backlog", _make_noop_flock()),
            patch("devbench.cli.BacklogManager", return_value=mock_mgr),
        ):
            cli.cmd_claim(unit.id)

        mock_mgr.force_status.assert_called_once()
        call_args = mock_mgr.force_status.call_args
        # STATUS_BLOCKED must appear in the force_status call arguments
        assert STATUS_BLOCKED in str(call_args)

    def test_unresolvable_repo_stderr_contains_repo_name(
        self,
        backlog_dir: Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """An error message referencing the unresolvable repo appears on stderr."""
        unit, wu_file = _make_unit(backlog_dir, repo="unknown-org/no-such-repo")
        backlog_index = _make_backlog_index(backlog_dir.parent, unit.id)
        mock_parser = MagicMock()
        mock_parser.parse_index.return_value = [unit]
        mock_mgr = MagicMock()

        with (
            patch("devbench.cli.BacklogParser", return_value=mock_parser),
            patch("devbench.cli.BACKLOG_ROOT", backlog_dir.parent),
            patch("devbench.cli.BACKLOG_INDEX", backlog_index),
            patch("devbench.cli.WORKSPACE_ROOT", backlog_dir.parent),
            patch("devbench.cli.flock_backlog", _make_noop_flock()),
            patch("devbench.cli.BacklogManager", return_value=mock_mgr),
        ):
            cli.cmd_claim(unit.id)

        err = capsys.readouterr().err
        assert "unknown-org/no-such-repo" in err


class TestCmdClaimMarkerIdempotency:
    """AC-241a-1: the [BLOCKED_TARGET_REPO_UNRESOLVED] marker write is idempotent."""

    def test_repeat_claim_does_not_duplicate_marker(
        self,
        backlog_dir: Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """A second cmd_claim call on an unresolvable-repo WU does not append a duplicate marker."""
        unit, wu_file = _make_unit(backlog_dir, repo="unknown-org/no-such-repo")
        backlog_index = _make_backlog_index(backlog_dir.parent, unit.id)
        mock_parser = MagicMock()
        mock_parser.parse_index.return_value = [unit]
        mock_mgr = MagicMock()

        with (
            patch("devbench.cli.BacklogParser", return_value=mock_parser),
            patch("devbench.cli.BACKLOG_ROOT", backlog_dir.parent),
            patch("devbench.cli.BACKLOG_INDEX", backlog_index),
            patch("devbench.cli.WORKSPACE_ROOT", backlog_dir.parent),
            patch("devbench.cli.flock_backlog", _make_noop_flock()),
            patch("devbench.cli.BacklogManager", return_value=mock_mgr),
        ):
            cli.cmd_claim(unit.id)
            cli.cmd_claim(unit.id)

        content = wu_file.read_text(encoding="utf-8")
        count = content.count(_BLOCKED_MARKER)
        assert count == 1, f"Expected exactly 1 occurrence of marker but found {count}"

    def test_repeat_claim_still_returns_44(
        self,
        backlog_dir: Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """A second cmd_claim on an unresolvable-repo WU still returns 44."""
        unit, wu_file = _make_unit(backlog_dir, repo="unknown-org/no-such-repo")
        backlog_index = _make_backlog_index(backlog_dir.parent, unit.id)
        mock_parser = MagicMock()
        mock_parser.parse_index.return_value = [unit]
        mock_mgr = MagicMock()

        with (
            patch("devbench.cli.BacklogParser", return_value=mock_parser),
            patch("devbench.cli.BACKLOG_ROOT", backlog_dir.parent),
            patch("devbench.cli.BACKLOG_INDEX", backlog_index),
            patch("devbench.cli.WORKSPACE_ROOT", backlog_dir.parent),
            patch("devbench.cli.flock_backlog", _make_noop_flock()),
            patch("devbench.cli.BacklogManager", return_value=mock_mgr),
        ):
            cli.cmd_claim(unit.id)
            rc = cli.cmd_claim(unit.id)

        assert rc == 44


class TestCmdClaimMarkerWriteEdgeCases:
    """Edge-case coverage for _claim_write_unresolved_repo_marker."""

    def test_marker_written_when_comments_section_absent(
        self,
        backlog_dir: Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """When the WU file has no ## Comments section, the section is created with the marker."""
        unit_id = "E0-F1-S1-T3"
        wu_file = backlog_dir / f"{unit_id}.md"
        # WU file deliberately missing the ## Comments section
        wu_file.write_text(
            f"# {unit_id}: Test\n\n## Status: in-queue\n\n## Changes Manifest\n\n"
            "| File | Change |\n|------|--------|\n| src/x.py | modify |\n",
            encoding="utf-8",
        )
        unit = WorkUnit(
            id=unit_id,
            title="Test",
            status=WorkUnitStatus.IN_QUEUE,
            unit_type=WorkUnitType.TASK,
            file_path=Path(f"backlog/{unit_id}.md"),
            repo="unknown-org/no-such-repo",
            dependencies=[],
        )
        backlog_index = _make_backlog_index(backlog_dir.parent, unit_id)
        mock_parser = MagicMock()
        mock_parser.parse_index.return_value = [unit]
        mock_mgr = MagicMock()

        with (
            patch("devbench.cli.BacklogParser", return_value=mock_parser),
            patch("devbench.cli.BACKLOG_ROOT", backlog_dir.parent),
            patch("devbench.cli.BACKLOG_INDEX", backlog_index),
            patch("devbench.cli.WORKSPACE_ROOT", backlog_dir.parent),
            patch("devbench.cli.flock_backlog", _make_noop_flock()),
            patch("devbench.cli.BacklogManager", return_value=mock_mgr),
        ):
            rc = cli.cmd_claim(unit_id)

        assert rc == 44
        content = wu_file.read_text(encoding="utf-8")
        assert "## Comments" in content
        assert _BLOCKED_MARKER in content


class TestCmdClaimResolvableRepoProceeds:
    """A resolvable repo proceeds normally (regression guard)."""

    def test_resolvable_repo_proceeds_normally(
        self,
        backlog_dir: Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """Claiming a WU with a valid repo proceeds to the lock-acquire path (rc=0)."""
        unit, wu_file = _make_unit(backlog_dir, repo="caylent-solutions/git-repo")
        backlog_index = _make_backlog_index(backlog_dir.parent, unit.id)
        mock_parser = MagicMock()
        mock_parser.parse_index.return_value = [unit]
        mock_mgr = MagicMock()
        entered: list[bool] = []
        flock_factory = _make_noop_flock(entered=entered)

        with (
            patch("devbench.cli.BacklogParser", return_value=mock_parser),
            patch("devbench.cli.BACKLOG_ROOT", backlog_dir.parent),
            patch("devbench.cli.BACKLOG_INDEX", backlog_index),
            patch("devbench.cli.WORKSPACE_ROOT", backlog_dir.parent),
            patch("devbench.cli.BacklogManager", return_value=mock_mgr),
            patch("devbench.cli.flock_backlog", flock_factory),
        ):
            rc = cli.cmd_claim(unit.id)

        assert rc == 0
        assert entered, "flock_backlog must be entered for a resolvable repo"
        mock_mgr.force_status.assert_called_once()
