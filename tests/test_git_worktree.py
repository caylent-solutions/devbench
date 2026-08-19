"""Tests for per-unit checkout isolation via git worktrees.

Two units that never share a working tree never collide, so an interrupted
unit's work is not something the next claim has to displace and survive. The
isolation is opt-in and mutually exclusive with single-branch mode, because
git allows a branch to be checked out in exactly one worktree at a time.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from devbench.git_worktree import (
    WORKTREE_DIR_NAME,
    ensure_unit_worktree,
    list_unit_worktrees,
    prune_worktrees,
    remove_unit_worktree,
    unit_branch_name,
    unit_worktree_path,
    worktree_root,
)


def _repo(tmp_path: Path, files: dict[str, str]) -> Path:
    """Create a git repo with *files* committed as the baseline."""
    repo = tmp_path / "checkout"
    repo.mkdir()
    for args in (
        ["git", "init", "--initial-branch=main"],
        ["git", "config", "user.email", "t@ex.com"],
        ["git", "config", "user.name", "Test"],
    ):
        subprocess.run(args, cwd=repo, check=True, capture_output=True)
    for rel, content in files.items():
        target = repo / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
    subprocess.run(["git", "add", "-A"], cwd=repo, check=True, capture_output=True)
    subprocess.run(["git", "commit", "-m", "baseline"], cwd=repo, check=True, capture_output=True)
    return repo


def _branches(repo: Path) -> list[str]:
    out = subprocess.run(
        ["git", "branch", "--format=%(refname:short)"], cwd=repo, check=True, capture_output=True, text=True
    )
    return sorted(line.strip() for line in out.stdout.splitlines() if line.strip())


class TestPathAndBranchNaming:
    def test_worktree_root_sits_beside_the_checkout(self, tmp_path: Path) -> None:
        """Inside the checkout it would show up in the repo's own status."""
        repo = tmp_path / "workspace" / "myrepo"
        assert worktree_root(repo) == tmp_path / "workspace" / WORKTREE_DIR_NAME / "myrepo"

    def test_each_unit_gets_a_distinct_directory(self, tmp_path: Path) -> None:
        repo = tmp_path / "myrepo"
        first = unit_worktree_path(repo, "E1-F1-S1-T1")
        second = unit_worktree_path(repo, "E1-F1-S1-T2")
        assert first != second
        assert first.name == "E1-F1-S1-T1"

    def test_branch_name_is_derived_from_the_unit_id(self) -> None:
        assert unit_branch_name("E1-F1-S1-T1") == "devbench/e1-f1-s1-t1"

    def test_branch_prefix_namespaces_the_branch(self) -> None:
        """Several workspaces sharing one downstream repo must not collide."""
        assert unit_branch_name("E1-F1-S1-T1", "acme") == "acme/devbench/e1-f1-s1-t1"


class TestEnsureUnitWorktree:
    def test_worktree_is_created_with_the_baseline_content(self, tmp_path: Path) -> None:
        repo = _repo(tmp_path, {"src/a.py": "baseline\n"})
        path = ensure_unit_worktree(repo, "E1-F1-S1-T1", "main")
        assert (path / "src/a.py").read_text(encoding="utf-8") == "baseline\n"

    def test_a_branch_is_created_for_the_unit(self, tmp_path: Path) -> None:
        repo = _repo(tmp_path, {"src/a.py": "baseline\n"})
        ensure_unit_worktree(repo, "E1-F1-S1-T1", "main")
        assert "devbench/e1-f1-s1-t1" in _branches(repo)

    def test_calling_twice_returns_the_same_worktree(self, tmp_path: Path) -> None:
        repo = _repo(tmp_path, {"src/a.py": "baseline\n"})
        first = ensure_unit_worktree(repo, "E1-F1-S1-T1", "main")
        second = ensure_unit_worktree(repo, "E1-F1-S1-T1", "main")
        assert first == second

    def test_in_flight_work_survives_a_re_claim(self, tmp_path: Path) -> None:
        """The whole point: an interrupted unit resumes rather than restarts."""
        repo = _repo(tmp_path, {"src/a.py": "baseline\n"})
        path = ensure_unit_worktree(repo, "E1-F1-S1-T1", "main")
        (path / "src/a.py").write_text("expensive executor output\n", encoding="utf-8")

        again = ensure_unit_worktree(repo, "E1-F1-S1-T1", "main")

        assert (again / "src/a.py").read_text(encoding="utf-8") == "expensive executor output\n"

    def test_two_units_do_not_share_a_working_tree(self, tmp_path: Path) -> None:
        repo = _repo(tmp_path, {"src/a.py": "baseline\n"})
        first = ensure_unit_worktree(repo, "E1-F1-S1-T1", "main")
        second = ensure_unit_worktree(repo, "E1-F1-S1-T2", "main")
        (first / "src/a.py").write_text("unit one work\n", encoding="utf-8")

        assert (second / "src/a.py").read_text(encoding="utf-8") == "baseline\n"

    def test_the_primary_checkout_is_untouched(self, tmp_path: Path) -> None:
        repo = _repo(tmp_path, {"src/a.py": "baseline\n"})
        path = ensure_unit_worktree(repo, "E1-F1-S1-T1", "main")
        (path / "src/a.py").write_text("unit work\n", encoding="utf-8")

        assert (repo / "src/a.py").read_text(encoding="utf-8") == "baseline\n"
        status = subprocess.run(["git", "status", "--porcelain"], cwd=repo, check=True, capture_output=True, text=True)
        assert status.stdout.strip() == ""

    def test_an_existing_branch_is_reused_rather_than_recreated(self, tmp_path: Path) -> None:
        """A removed worktree leaves its branch; recreating must continue that history."""
        repo = _repo(tmp_path, {"src/a.py": "baseline\n"})
        path = ensure_unit_worktree(repo, "E1-F1-S1-T1", "main")
        (path / "src/a.py").write_text("committed work\n", encoding="utf-8")
        subprocess.run(["git", "add", "-A"], cwd=path, check=True, capture_output=True)
        subprocess.run(["git", "commit", "-m", "unit work"], cwd=path, check=True, capture_output=True)
        remove_unit_worktree(repo, "E1-F1-S1-T1")

        recreated = ensure_unit_worktree(repo, "E1-F1-S1-T1", "main")

        assert (recreated / "src/a.py").read_text(encoding="utf-8") == "committed work\n"


class TestRemoveUnitWorktree:
    def test_absent_worktree_reports_nothing_removed(self, tmp_path: Path) -> None:
        repo = _repo(tmp_path, {"src/a.py": "baseline\n"})
        assert remove_unit_worktree(repo, "E1-F1-S1-T1") is False

    def test_clean_worktree_is_removed(self, tmp_path: Path) -> None:
        repo = _repo(tmp_path, {"src/a.py": "baseline\n"})
        path = ensure_unit_worktree(repo, "E1-F1-S1-T1", "main")
        assert remove_unit_worktree(repo, "E1-F1-S1-T1") is True
        assert not path.exists()

    def test_uncommitted_work_blocks_a_routine_removal(self, tmp_path: Path) -> None:
        """An unfinished unit must never be discarded by a cleanup pass."""
        repo = _repo(tmp_path, {"src/a.py": "baseline\n"})
        path = ensure_unit_worktree(repo, "E1-F1-S1-T1", "main")
        (path / "src/a.py").write_text("unfinished\n", encoding="utf-8")

        with pytest.raises(RuntimeError):
            remove_unit_worktree(repo, "E1-F1-S1-T1")

        assert (path / "src/a.py").read_text(encoding="utf-8") == "unfinished\n"

    def test_force_removes_a_dirty_worktree(self, tmp_path: Path) -> None:
        repo = _repo(tmp_path, {"src/a.py": "baseline\n"})
        path = ensure_unit_worktree(repo, "E1-F1-S1-T1", "main")
        (path / "src/a.py").write_text("unfinished\n", encoding="utf-8")

        assert remove_unit_worktree(repo, "E1-F1-S1-T1", force=True) is True
        assert not path.exists()

    def test_the_units_branch_outlives_its_worktree(self, tmp_path: Path) -> None:
        repo = _repo(tmp_path, {"src/a.py": "baseline\n"})
        ensure_unit_worktree(repo, "E1-F1-S1-T1", "main")
        remove_unit_worktree(repo, "E1-F1-S1-T1")
        assert "devbench/e1-f1-s1-t1" in _branches(repo)


class TestListAndPrune:
    def test_no_worktrees_lists_empty(self, tmp_path: Path) -> None:
        repo = _repo(tmp_path, {"src/a.py": "baseline\n"})
        assert list_unit_worktrees(repo) == ()

    def test_created_units_are_listed_sorted(self, tmp_path: Path) -> None:
        repo = _repo(tmp_path, {"src/a.py": "baseline\n"})
        ensure_unit_worktree(repo, "E1-F1-S1-T2", "main")
        ensure_unit_worktree(repo, "E1-F1-S1-T1", "main")
        assert list_unit_worktrees(repo) == ("E1-F1-S1-T1", "E1-F1-S1-T2")

    def test_prune_clears_a_record_left_by_an_external_delete(self, tmp_path: Path) -> None:
        """A directory removed outside git blocks recreation until pruned."""
        import shutil

        repo = _repo(tmp_path, {"src/a.py": "baseline\n"})
        path = ensure_unit_worktree(repo, "E1-F1-S1-T1", "main")
        shutil.rmtree(path)

        prune_worktrees(repo)

        recreated = ensure_unit_worktree(repo, "E1-F1-S1-T1", "main")
        assert (recreated / "src/a.py").read_text(encoding="utf-8") == "baseline\n"


class TestGitInvocationFailures:
    def test_a_timeout_is_surfaced_not_swallowed(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """A hung git process must fail the claim rather than stall it silently."""
        repo = _repo(tmp_path, {"src/a.py": "baseline\n"})

        def _hang(*_args: object, **_kwargs: object) -> None:
            raise subprocess.TimeoutExpired(cmd="git", timeout=1)

        monkeypatch.setattr(subprocess, "run", _hang)

        with pytest.raises(RuntimeError, match="timed out"):
            prune_worktrees(repo)

    def test_a_nonzero_exit_is_surfaced(self, tmp_path: Path) -> None:
        repo = _repo(tmp_path, {"src/a.py": "baseline\n"})
        with pytest.raises(RuntimeError, match="failed in"):
            ensure_unit_worktree(repo, "E1-F1-S1-T1", "no-such-ref")

    def test_removal_clears_an_empty_directory_git_leaves_behind(self, tmp_path: Path) -> None:
        repo = _repo(tmp_path, {"src/a.py": "baseline\n"})
        path = ensure_unit_worktree(repo, "E1-F1-S1-T1", "main")

        assert remove_unit_worktree(repo, "E1-F1-S1-T1") is True

        assert not path.exists()
        assert list_unit_worktrees(repo) == ()
