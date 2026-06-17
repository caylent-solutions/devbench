"""Tests for cmd_claim pre-claim target-repo guard (issue #241, AC-241-1, AC-241a-1).

Verifies that cmd_claim resolves the target repo before acquiring the lock,
writes the [BLOCKED_TARGET_REPO_UNRESOLVED] marker idempotently, sets the
unit to blocked, and returns exit code 44 (CLAIM_BLOCKED_PRECLAIM) with no
lock acquired.

Also covers the on-claim foreign-WIP eviction (TDI-006): a unit claimed into a
checkout that still carries orphaned staged / working-tree WIP from a prior
interrupted unit must start from a clean tree, with the orphaned (non-manifest)
WIP evicted from BOTH the index and the working tree -- while the claimed
unit's own legitimate manifest work is never clobbered.
"""

from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Any
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


class TestCmdClaimSerializeBackstop:
    """Hard backstop: refuse to claim a NEW unit while the in-progress cap is saturated.

    Root cause of tracked-issue 002: parallel claims share ONE target checkout. ``cmd_claim``
    refuses (deferral, NOT a unit failure) when the target unit is not already IN_PROGRESS and
    the number of OTHER IN_PROGRESS units is at or above ``max_parallel_in_progress`` (default 1).
    Re-claiming an already-in-progress unit stays idempotent (rc 0).
    """

    def _unit(self, unit_id: str, status: WorkUnitStatus, repo: str = "caylent-solutions/git-repo") -> WorkUnit:
        return WorkUnit(
            id=unit_id,
            title="Test",
            status=status,
            unit_type=WorkUnitType.TASK,
            file_path=Path(f"backlog/{unit_id}.md"),
            repo=repo,
            dependencies=[],
        )

    def test_second_unit_deferred_when_one_in_progress(
        self,
        backlog_dir: Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """Claiming a 2nd different unit while one is IN_PROGRESS returns CLAIM_DEFERRED_SERIALIZED."""
        from devbench.constants import CLAIM_DEFERRED_SERIALIZED

        target, wu_file = _make_unit(backlog_dir, unit_id="E0-F1-S1-T2", repo="caylent-solutions/git-repo")
        busy = self._unit("E0-F1-S1-T1", WorkUnitStatus.IN_PROGRESS)
        backlog_index = _make_backlog_index(backlog_dir.parent, target.id)
        mock_parser = MagicMock()
        mock_parser.parse_index.return_value = [busy, target]
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
            rc = cli.cmd_claim(target.id)

        assert rc == CLAIM_DEFERRED_SERIALIZED
        # Deferral writes NO status: no force_status, no flock entered, file unchanged on disk.
        mock_mgr.force_status.assert_not_called()
        assert not entered, "deferral must not acquire the lock"
        content = wu_file.read_text(encoding="utf-8")
        assert "## Status: in-queue" in content, "the deferred unit's status must be unchanged"
        assert "[BLOCKED" not in content, "deferral must NOT mark the unit blocked"
        err = capsys.readouterr().err
        assert "E0-F1-S1-T1" in err, "the error must name the in-progress unit"
        assert "retry" in err.lower(), "the message must say to retry after the in-progress unit completes"

    def test_reclaim_same_in_progress_unit_succeeds(
        self,
        backlog_dir: Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """Re-claiming the SAME already-IN_PROGRESS unit is idempotent (rc 0), even at the cap."""
        target, wu_file = _make_unit(
            backlog_dir, unit_id="E0-F1-S1-T1", status="in-progress", repo="caylent-solutions/git-repo"
        )
        target.status = WorkUnitStatus.IN_PROGRESS
        backlog_index = _make_backlog_index(backlog_dir.parent, target.id, status="in-progress")
        mock_parser = MagicMock()
        mock_parser.parse_index.return_value = [target]
        mock_mgr = MagicMock()
        entered: list[bool] = []
        flock_factory = _make_noop_flock(entered=entered)

        with (
            patch("devbench.cli.BacklogParser", return_value=mock_parser),
            patch("devbench.cli.BACKLOG_ROOT", backlog_dir.parent),
            patch("devbench.cli.BACKLOG_INDEX", backlog_index),
            patch("devbench.cli.WORKSPACE_ROOT", backlog_dir.parent),
            patch("devbench.cli.flock_backlog", flock_factory),
            patch("devbench.cli.BacklogManager", return_value=mock_mgr),
        ):
            rc = cli.cmd_claim(target.id)

        assert rc == 0, "re-claiming an already in-progress unit must remain idempotent"
        assert entered, "the idempotent re-claim still proceeds through the lock"

    def test_first_claim_proceeds_when_no_other_in_progress(
        self,
        backlog_dir: Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """With zero OTHER in-progress units the claim proceeds normally (rc 0)."""
        target, wu_file = _make_unit(backlog_dir, unit_id="E0-F1-S1-T2", repo="caylent-solutions/git-repo")
        backlog_index = _make_backlog_index(backlog_dir.parent, target.id)
        mock_parser = MagicMock()
        mock_parser.parse_index.return_value = [target]
        mock_mgr = MagicMock()
        entered: list[bool] = []
        flock_factory = _make_noop_flock(entered=entered)

        with (
            patch("devbench.cli.BacklogParser", return_value=mock_parser),
            patch("devbench.cli.BACKLOG_ROOT", backlog_dir.parent),
            patch("devbench.cli.BACKLOG_INDEX", backlog_index),
            patch("devbench.cli.WORKSPACE_ROOT", backlog_dir.parent),
            patch("devbench.cli.flock_backlog", flock_factory),
            patch("devbench.cli.BacklogManager", return_value=mock_mgr),
        ):
            rc = cli.cmd_claim(target.id)

        assert rc == 0
        assert entered, "a first claim with no other in-progress unit proceeds through the lock"

    def test_cap_two_allows_second_claim(
        self,
        backlog_dir: Path,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """Env cap=2: a second claim while one unit is in-progress proceeds (rc 0)."""
        monkeypatch.setenv("DEVBENCH_ORCHESTRATOR_MAX_PARALLEL_IN_PROGRESS", "2")
        target, wu_file = _make_unit(backlog_dir, unit_id="E0-F1-S1-T2", repo="caylent-solutions/git-repo")
        busy = self._unit("E0-F1-S1-T1", WorkUnitStatus.IN_PROGRESS)
        backlog_index = _make_backlog_index(backlog_dir.parent, target.id)
        mock_parser = MagicMock()
        mock_parser.parse_index.return_value = [busy, target]
        mock_mgr = MagicMock()
        entered: list[bool] = []
        flock_factory = _make_noop_flock(entered=entered)

        with (
            patch("devbench.cli.BacklogParser", return_value=mock_parser),
            patch("devbench.cli.BACKLOG_ROOT", backlog_dir.parent),
            patch("devbench.cli.BACKLOG_INDEX", backlog_index),
            patch("devbench.cli.WORKSPACE_ROOT", backlog_dir.parent),
            patch("devbench.cli.flock_backlog", flock_factory),
            patch("devbench.cli.BacklogManager", return_value=mock_mgr),
        ):
            rc = cli.cmd_claim(target.id)

        assert rc == 0, "with cap=2 and only one in-progress unit, a second claim proceeds"
        assert entered


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


# ---------------------------------------------------------------------------
# TDI-006: on-claim foreign-WIP eviction
# ---------------------------------------------------------------------------


def _git(repo: Path, *args: str) -> str:
    """Run a git command in *repo* and return stripped stdout (fail-fast)."""
    result = subprocess.run(
        ["git", "-C", str(repo), *args],
        check=True,
        capture_output=True,
        text=True,
    )
    return result.stdout.strip()


def _init_checkout(repo: Path) -> None:
    """Initialise a git checkout with one committed baseline file."""
    repo.mkdir(parents=True, exist_ok=True)
    _git(repo, "init", "-q")
    _git(repo, "config", "user.email", "test@example.com")
    _git(repo, "config", "user.name", "Test")
    (repo / "README.md").write_text("baseline\n", encoding="utf-8")
    _git(repo, "add", "README.md")
    _git(repo, "commit", "-q", "-m", "baseline")


def _staged_paths(repo: Path) -> set[str]:
    """Return the set of paths currently staged (index differs from HEAD)."""
    out = _git(repo, "diff", "--cached", "--name-only")
    return {line for line in out.splitlines() if line}


def _porcelain(repo: Path) -> str:
    """Return ``git status --porcelain`` output for *repo*."""
    return _git(repo, "status", "--porcelain")


class TestCmdClaimEvictsForeignWip:
    """TDI-006: on claim the checkout is cleaned of foreign orphaned WIP."""

    def _claim_into_checkout(
        self,
        backlog_dir: Path,
        checkout: Path,
        *,
        manifest_path: str = "src/x.py",
    ) -> int:
        """Run cmd_claim for a unit whose manifest lists *manifest_path*.

        Wires the unit's resolvable repo to *checkout* via REPO_LOCAL_PATHS so
        the on-claim cleanup operates on the real git checkout.
        """
        unit, _wu_file = _make_unit(backlog_dir, repo="caylent-solutions/git-repo")
        # Rewrite the manifest row so the unit's own legitimate path is known.
        _wu_file.write_text(
            f"# {unit.id}: Test\n\n## Status: in-queue\n\n## Changes Manifest\n\n"
            "| File | Change |\n|------|--------|\n"
            f"| {manifest_path} | modify |\n\n## Comments\n\n",
            encoding="utf-8",
        )
        backlog_index = _make_backlog_index(backlog_dir.parent, unit.id)
        mock_parser = MagicMock()
        mock_parser.parse_index.return_value = [unit]
        mock_mgr = MagicMock()
        canonical = "caylent-solutions/git-repo"

        with (
            patch("devbench.cli.BacklogParser", return_value=mock_parser),
            patch("devbench.cli.BACKLOG_ROOT", backlog_dir.parent),
            patch("devbench.cli.BACKLOG_INDEX", backlog_index),
            patch("devbench.cli.WORKSPACE_ROOT", backlog_dir.parent),
            patch("devbench.cli.BacklogManager", return_value=mock_mgr),
            patch("devbench.cli.flock_backlog", _make_noop_flock()),
            patch("devbench.cli.resolve_repo", return_value=canonical),
            patch.dict("devbench.cli.REPO_LOCAL_PATHS", {canonical: checkout}, clear=False),
        ):
            return cli.cmd_claim(unit.id)

    def test_orphaned_staged_wip_is_evicted_from_index(
        self,
        backlog_dir: Path,
        tmp_path: Path,
    ) -> None:
        """Foreign staged WIP in the checkout is unstaged AND removed from the tree on claim."""
        checkout = tmp_path / "checkout"
        _init_checkout(checkout)
        # Orphaned WIP from a prior interrupted unit: a foreign file, staged.
        foreign = checkout / "src" / "lockfile.py"
        foreign.parent.mkdir(parents=True, exist_ok=True)
        foreign.write_text("orphaned = True\n", encoding="utf-8")
        _git(checkout, "add", "src/lockfile.py")
        assert "src/lockfile.py" in _staged_paths(checkout), "precondition: foreign WIP staged"

        rc = self._claim_into_checkout(backlog_dir, checkout, manifest_path="docs/guide.md")

        assert rc == 0
        # The foreign path must no longer be staged...
        assert "src/lockfile.py" not in _staged_paths(checkout)
        # ...and must no longer pollute the working tree (evicted, not merely unstaged).
        assert not foreign.exists(), "foreign WIP must be removed from the working tree"
        # The index must be clean (no staged changes at all).
        assert _staged_paths(checkout) == set()

    def test_orphaned_wip_is_recoverable_via_stash(
        self,
        backlog_dir: Path,
        tmp_path: Path,
    ) -> None:
        """Evicted foreign WIP is parked (stashed), not destroyed -- it is recoverable."""
        checkout = tmp_path / "checkout"
        _init_checkout(checkout)
        foreign = checkout / "src" / "lockfile.py"
        foreign.parent.mkdir(parents=True, exist_ok=True)
        foreign.write_text("orphaned = True\n", encoding="utf-8")
        _git(checkout, "add", "src/lockfile.py")

        rc = self._claim_into_checkout(backlog_dir, checkout, manifest_path="docs/guide.md")

        assert rc == 0
        # A stash entry must exist holding the evicted WIP (recoverable backup).
        stash_list = _git(checkout, "stash", "list")
        assert stash_list, "evicted foreign WIP must be parked in a stash for recovery"

    def test_claimed_units_own_manifest_work_is_preserved(
        self,
        backlog_dir: Path,
        tmp_path: Path,
    ) -> None:
        """The claimed unit's own in-progress manifest work survives the cleanup."""
        checkout = tmp_path / "checkout"
        _init_checkout(checkout)
        # The claimed unit's OWN legitimate in-progress work (resumed unit).
        own = checkout / "src" / "x.py"
        own.parent.mkdir(parents=True, exist_ok=True)
        own.write_text("legit = 1\n", encoding="utf-8")
        _git(checkout, "add", "src/x.py")
        # A foreign orphan from a prior interrupted unit, also staged.
        foreign = checkout / "src" / "lockfile.py"
        foreign.write_text("orphaned = True\n", encoding="utf-8")
        _git(checkout, "add", "src/lockfile.py")

        rc = self._claim_into_checkout(backlog_dir, checkout, manifest_path="src/x.py")

        assert rc == 0
        # Foreign orphan evicted...
        assert not foreign.exists()
        assert "src/lockfile.py" not in _staged_paths(checkout)
        # ...but the unit's own manifest file is untouched in the working tree.
        assert own.exists(), "the claimed unit's own manifest work must NOT be clobbered"
        assert own.read_text(encoding="utf-8") == "legit = 1\n"

    def test_clean_checkout_claim_is_a_noop(
        self,
        backlog_dir: Path,
        tmp_path: Path,
    ) -> None:
        """Claiming into an already-clean checkout leaves the tree untouched (no stash)."""
        checkout = tmp_path / "checkout"
        _init_checkout(checkout)
        assert _porcelain(checkout) == "", "precondition: clean checkout"

        rc = self._claim_into_checkout(backlog_dir, checkout, manifest_path="docs/guide.md")

        assert rc == 0
        assert _porcelain(checkout) == "", "a clean claim must not dirty the tree"
        assert _git(checkout, "stash", "list") == "", "a clean claim must create no stash"

    def test_non_git_checkout_is_a_noop(
        self,
        backlog_dir: Path,
        tmp_path: Path,
    ) -> None:
        """Claiming when the configured checkout is not a git repo is a safe no-op."""
        checkout = tmp_path / "not-a-git-repo"
        checkout.mkdir()
        (checkout / "file.txt").write_text("plain\n", encoding="utf-8")

        rc = self._claim_into_checkout(backlog_dir, checkout, manifest_path="docs/guide.md")

        assert rc == 0
        # The directory and its contents are left intact (no crash, no eviction).
        assert (checkout / "file.txt").exists()


class TestDirtyPaths:
    """Unit coverage for the _dirty_paths git-status parser."""

    def test_nonexistent_path_returns_none(self, tmp_path: Path) -> None:
        """A path that is not a directory yields None (nothing to clean)."""
        assert cli._dirty_paths(tmp_path / "does-not-exist") is None

    def test_non_git_directory_returns_none(self, tmp_path: Path) -> None:
        """An existing directory that is not a git checkout yields None."""
        plain = tmp_path / "plain"
        plain.mkdir()
        (plain / "a.txt").write_text("x\n", encoding="utf-8")
        assert cli._dirty_paths(plain) is None

    def test_lists_staged_untracked_and_modified(self, tmp_path: Path) -> None:
        """Staged, untracked, and working-tree-modified paths are all reported."""
        repo = tmp_path / "repo"
        _init_checkout(repo)
        # Modify the committed baseline (working-tree change).
        (repo / "README.md").write_text("changed\n", encoding="utf-8")
        # A new staged file.
        (repo / "staged.py").write_text("s = 1\n", encoding="utf-8")
        _git(repo, "add", "staged.py")
        # An untracked file.
        (repo / "untracked.txt").write_text("u\n", encoding="utf-8")

        result = cli._dirty_paths(repo)

        assert result is not None
        assert set(result) == {"README.md", "staged.py", "untracked.txt"}

    def test_rename_record_reports_only_destination(self, tmp_path: Path) -> None:
        """A staged rename reports the destination path, not the original, exactly once."""
        repo = tmp_path / "repo"
        _init_checkout(repo)
        (repo / "old_name.py").write_text("body\n", encoding="utf-8")
        _git(repo, "add", "old_name.py")
        _git(repo, "commit", "-q", "-m", "add old_name")
        # Rename via git so status reports an "R" record (two NUL fields).
        _git(repo, "mv", "old_name.py", "new_name.py")

        result = cli._dirty_paths(repo)

        assert result is not None
        assert "new_name.py" in result
        assert "old_name.py" not in result
        # The destination must appear exactly once (the original field is consumed).
        assert result.count("new_name.py") == 1


class TestOwnManifestPaths:
    """Unit coverage for _own_manifest_paths fallbacks."""

    def test_returns_empty_when_wu_file_unresolvable(self) -> None:
        """A unit whose work-unit file cannot be resolved yields an empty set."""
        unit = WorkUnit(
            id="E0-F1-S1-T9",
            title="Test",
            status=WorkUnitStatus.IN_QUEUE,
            unit_type=WorkUnitType.TASK,
            file_path=Path("backlog/E0-F1-S1-T9.md"),
            repo="caylent-solutions/git-repo",
            dependencies=[],
        )
        with patch("devbench.cli._resolve_unit_file", return_value=None):
            assert cli._own_manifest_paths(unit) == set()

    def test_returns_empty_when_manifest_unparsable(self, backlog_dir: Path) -> None:
        """A work-unit file with no Changes Manifest section yields an empty set.

        parse_manifest raises ManifestParseError (a ValueError) when the
        ``## Changes Manifest`` section is absent; the helper swallows it.
        """
        unit, wu_file = _make_unit(backlog_dir, repo="caylent-solutions/git-repo")
        # Remove the Changes Manifest section so parse_manifest raises.
        wu_file.write_text(
            f"# {unit.id}: Test\n\n## Status: in-queue\n\n## Comments\n\n",
            encoding="utf-8",
        )
        with patch("devbench.cli._resolve_unit_file", return_value=wu_file):
            assert cli._own_manifest_paths(unit) == set()

    def test_returns_committable_manifest_paths(self, backlog_dir: Path) -> None:
        """A valid manifest yields its real (non-sentinel) file paths."""
        unit, wu_file = _make_unit(backlog_dir, repo="caylent-solutions/git-repo")
        with patch("devbench.cli._resolve_unit_file", return_value=wu_file):
            assert cli._own_manifest_paths(unit) == {"src/x.py"}


class TestResolveClaimCheckout:
    """Unit coverage for _resolve_claim_checkout guards."""

    def _unit(self, *, repo: str) -> WorkUnit:
        return WorkUnit(
            id="E0-F1-S1-T8",
            title="Test",
            status=WorkUnitStatus.IN_QUEUE,
            unit_type=WorkUnitType.TASK,
            file_path=Path("backlog/E0-F1-S1-T8.md"),
            repo=repo,
            dependencies=[],
        )

    def test_empty_repo_returns_none(self) -> None:
        """A unit with no repo cannot be cleaned (None)."""
        assert cli._resolve_claim_checkout(self._unit(repo="")) is None

    def test_unresolvable_repo_returns_none(self) -> None:
        """A unit whose repo cannot be resolved yields None (no cleanup)."""
        with patch("devbench.cli.resolve_repo", side_effect=ValueError("nope")):
            assert cli._resolve_claim_checkout(self._unit(repo="x/y")) is None

    def test_no_configured_checkout_returns_none(self) -> None:
        """A resolvable repo with no configured local checkout yields None."""
        with (
            patch("devbench.cli.resolve_repo", return_value="x/y"),
            patch.dict("devbench.cli.REPO_LOCAL_PATHS", {}, clear=True),
        ):
            assert cli._resolve_claim_checkout(self._unit(repo="x/y")) is None

    def test_missing_checkout_dir_returns_none(self, tmp_path: Path) -> None:
        """A configured checkout path that does not exist yields None."""
        missing = tmp_path / "missing"
        with (
            patch("devbench.cli.resolve_repo", return_value="x/y"),
            patch.dict("devbench.cli.REPO_LOCAL_PATHS", {"x/y": missing}, clear=True),
        ):
            assert cli._resolve_claim_checkout(self._unit(repo="x/y")) is None


class TestCleanForeignWipBranches:
    """Unit coverage for _clean_foreign_wip_on_claim edge branches."""

    def _unit(self, *, repo: str = "caylent-solutions/git-repo") -> WorkUnit:
        return WorkUnit(
            id="E0-F1-S1-T7",
            title="Test",
            status=WorkUnitStatus.IN_QUEUE,
            unit_type=WorkUnitType.TASK,
            file_path=Path("backlog/E0-F1-S1-T7.md"),
            repo=repo,
            dependencies=[],
        )

    def test_unresolvable_checkout_is_noop(self) -> None:
        """No checkout resolved -> no eviction (False)."""
        with patch("devbench.cli._resolve_claim_checkout", return_value=None):
            assert cli._clean_foreign_wip_on_claim(self._unit()) is False

    def test_only_own_manifest_dirty_is_noop(self, tmp_path: Path) -> None:
        """When the only dirty paths belong to the claimed unit, nothing is evicted."""
        repo = tmp_path / "repo"
        _init_checkout(repo)
        own = repo / "src" / "x.py"
        own.parent.mkdir(parents=True, exist_ok=True)
        own.write_text("legit = 1\n", encoding="utf-8")
        _git(repo, "add", "src/x.py")

        with (
            patch("devbench.cli._resolve_claim_checkout", return_value=repo),
            patch("devbench.cli._own_manifest_paths", return_value={"src/x.py"}),
        ):
            assert cli._clean_foreign_wip_on_claim(self._unit()) is False

        # The unit's own staged work is untouched and no stash was created.
        assert "src/x.py" in _staged_paths(repo)
        assert _git(repo, "stash", "list") == ""

    def test_stash_push_failure_returns_false(self, tmp_path: Path) -> None:
        """A failing git stash push is reported (False) and never raises."""
        repo = tmp_path / "repo"
        _init_checkout(repo)
        foreign = repo / "foreign.py"
        foreign.write_text("orphan = 1\n", encoding="utf-8")
        _git(repo, "add", "foreign.py")

        failing = subprocess.CompletedProcess(args=[], returncode=1, stdout="", stderr="boom")
        real_run = subprocess.run

        def _fake_run(cmd: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
            if len(cmd) >= 4 and cmd[3] == "stash":
                return failing
            return real_run(cmd, **kwargs)

        with (
            patch("devbench.cli._resolve_claim_checkout", return_value=repo),
            patch("devbench.cli._own_manifest_paths", return_value=set()),
            patch("devbench.cli.subprocess.run", side_effect=_fake_run),
        ):
            assert cli._clean_foreign_wip_on_claim(self._unit()) is False
