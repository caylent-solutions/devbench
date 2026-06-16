"""drain/stop must not leave an interrupted unit's staged WIP in the index.

Tracked issue: ``drain-leaves-interrupted-unit-staged-wip-in-index``.

When a session is drained/stopped while a unit is mid git-ops -- after the
executor ran ``git add <manifest files>`` but BEFORE the commit -- the staged
changes are left in the checkout's index. The unit's status is force-blocked, but
its STAGED working-tree changes were NOT unstaged, so any subsequent ``git commit``
in the same checkout (a follow-up offline fix, or the next unit's git-ops) sweeps
those orphaned staged files in under the wrong unit/message.

These tests exercise a real git repo to prove the staged WIP is unstaged on
interruption (edits preserved in the working tree, recoverable), so a later commit
cannot include it.
"""

from __future__ import annotations

import subprocess
from pathlib import Path
from unittest.mock import patch

import pytest

from devbench import cli
from devbench.backlog.work_unit import WorkUnit, WorkUnitStatus, WorkUnitType

pytestmark = pytest.mark.unit

_REPO = "caylent-solutions/git-repo"


def _git(repo: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", "-C", str(repo), *args],
        check=True,
        capture_output=True,
        text=True,
    )
    return result.stdout


def _init_repo_with_staged_wip(repo: Path) -> None:
    """Init a git repo, make an initial commit, then stage (but do not commit) a change."""
    repo.mkdir(parents=True, exist_ok=True)
    _git(repo, "init", "-q")
    _git(repo, "config", "user.email", "test@example.com")
    _git(repo, "config", "user.name", "Test")
    (repo / "seed.txt").write_text("seed\n", encoding="utf-8")
    _git(repo, "add", "seed.txt")
    _git(repo, "commit", "-q", "-m", "initial")
    # Now simulate the mid-git-ops state: a manifest file staged but not committed.
    (repo / "manifest_file.txt").write_text("interrupted unit WIP\n", encoding="utf-8")
    _git(repo, "add", "manifest_file.txt")


def _staged_paths(repo: Path) -> list[str]:
    out = _git(repo, "diff", "--cached", "--name-only")
    return [line for line in out.splitlines() if line.strip()]


def _make_in_progress_wu(backlog_dir: Path) -> WorkUnit:
    backlog_dir.mkdir(parents=True, exist_ok=True)
    wu_file = backlog_dir / "E1-F1-S1-T1.md"
    wu_file.write_text(
        "# E1-F1-S1-T1: Test Task\n\n## Status: in-progress\n\n## Comments\n",
        encoding="utf-8",
    )
    return WorkUnit(
        id="E1-F1-S1-T1",
        title="Test Task",
        status=WorkUnitStatus.IN_PROGRESS,
        unit_type=WorkUnitType.TASK,
        file_path=wu_file,
        repo=_REPO,
    )


class TestUnstageInterruptedWip:
    """The shared _unstage_interrupted_wip primitive unstages without losing edits."""

    def test_unstages_staged_changes(self, tmp_path: Path) -> None:
        repo = tmp_path / "repo"
        _init_repo_with_staged_wip(repo)
        assert _staged_paths(repo) == ["manifest_file.txt"], "precondition: file is staged"

        unstaged = cli._unstage_interrupted_wip(repo, unit_id="E1-F1-S1-T1", reason="drain")

        assert unstaged is True
        assert _staged_paths(repo) == [], "the staged WIP must be unstaged"
        # The edit is NOT lost -- it remains in the working tree (recoverable).
        assert (repo / "manifest_file.txt").read_text(encoding="utf-8") == "interrupted unit WIP\n"

    def test_clean_index_is_noop(self, tmp_path: Path) -> None:
        repo = tmp_path / "repo"
        repo.mkdir()
        _git(repo, "init", "-q")
        _git(repo, "config", "user.email", "t@e.com")
        _git(repo, "config", "user.name", "T")
        (repo / "seed.txt").write_text("seed\n", encoding="utf-8")
        _git(repo, "add", "seed.txt")
        _git(repo, "commit", "-q", "-m", "initial")

        unstaged = cli._unstage_interrupted_wip(repo, unit_id="E1-F1-S1-T1", reason="drain")
        assert unstaged is False

    def test_non_git_dir_is_noop(self, tmp_path: Path) -> None:
        plain = tmp_path / "plain"
        plain.mkdir()
        # Must not raise, returns False.
        assert cli._unstage_interrupted_wip(plain, unit_id="E1-F1-S1-T1", reason="drain") is False


class TestForceBlockUnstagesWip:
    """The SIGTERM/stop force-block path unstages the interrupted unit's WIP."""

    def test_force_block_unstages_interrupted_unit_wip(self, tmp_path: Path) -> None:
        repo = tmp_path / "repo"
        _init_repo_with_staged_wip(repo)
        backlog_dir = tmp_path / "backlog"
        wu = _make_in_progress_wu(backlog_dir)
        index = tmp_path / "BACKLOG.md"
        index.write_text(
            "# Backlog\n\n## Full Work Unit Index\n\n"
            "| ID | Title | Type | Status | Dependencies | Repo | File Path |\n"
            "|-----|-------|------|--------|-------------|------|-----------|\n"
            f"| E1-F1-S1-T1 | Test Task | Task | in-progress | None | {_REPO} | `backlog/E1-F1-S1-T1.md` |\n",
            encoding="utf-8",
        )

        with (
            patch("devbench.cli.BACKLOG_INDEX", index),
            patch("devbench.cli.REPO_LOCAL_PATHS", {_REPO: repo}),
        ):
            cli._force_block_in_flight_wu(wu, session_name="serial")

        # The unit is force-blocked AND its staged WIP is unstaged so the next
        # commit in this checkout cannot sweep it in.
        assert _staged_paths(repo) == [], "force-block must unstage the interrupted unit's WIP"
        # The edit survives in the working tree (recoverable when re-claimed).
        assert (repo / "manifest_file.txt").read_text(encoding="utf-8") == "interrupted unit WIP\n"
        assert "## Status: blocked" in wu.file_path.read_text(encoding="utf-8")
