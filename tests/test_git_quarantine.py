"""Tests for quarantining foreign uncommitted work out of the shared checkout.

The single-branch modes run every work unit in one shared checkout, so a unit
that blocks leaves its uncommitted changes for the next unit to inherit.
devbench runs unattended, so that residue is stashed out of the way rather
than reported to a stopped run. The stash must be non-destructive: executor
output is expensive and a blocked unit's work is often correct and merely
waiting on a dependency.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from devbench.git_quarantine import (
    QUARANTINE_STASH_PREFIX,
    UNATTRIBUTED_OWNER,
    group_paths_by_owner,
    quarantine_paths,
)


def _repo(tmp_path: Path, files: dict[str, str]) -> Path:
    """Create a git repo with *files* committed as the baseline."""
    repo = tmp_path / "checkout"
    repo.mkdir()
    for args in (
        ["git", "init"],
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


def _porcelain(repo: Path) -> list[str]:
    out = subprocess.run(["git", "status", "--porcelain"], cwd=repo, check=True, capture_output=True, text=True)
    return sorted(line[3:] for line in out.stdout.splitlines() if line.strip())


def _stash_subjects(repo: Path) -> list[str]:
    out = subprocess.run(["git", "stash", "list", "--format=%gs"], cwd=repo, check=True, capture_output=True, text=True)
    return [line for line in out.stdout.splitlines() if line.strip()]


class TestGroupPathsByOwner:
    def test_path_is_attributed_to_the_unit_that_declares_it(self) -> None:
        grouped = group_paths_by_owner(
            ["src/a.py", "docs/b.md"],
            {"E1-F1-S1-T1": ["src/a.py"], "E1-F1-S1-T2": ["docs/b.md"]},
        )
        assert grouped == {"E1-F1-S1-T1": ["src/a.py"], "E1-F1-S1-T2": ["docs/b.md"]}

    def test_undeclared_path_falls_to_the_unattributed_bucket(self) -> None:
        grouped = group_paths_by_owner(["src/orphan.py"], {"E1-F1-S1-T1": ["src/a.py"]})
        assert grouped == {UNATTRIBUTED_OWNER: ["src/orphan.py"]}

    def test_paths_are_sorted_within_each_owner(self) -> None:
        grouped = group_paths_by_owner(["src/z.py", "src/a.py"], {"E1-F1-S1-T1": ["src/a.py", "src/z.py"]})
        assert grouped == {"E1-F1-S1-T1": ["src/a.py", "src/z.py"]}

    def test_overlapping_declarations_resolve_deterministically(self) -> None:
        """The manifest-conflict rule prevents this; attribution must still be stable."""
        manifests = {"E1-F1-S1-T2": ["src/a.py"], "E1-F1-S1-T1": ["src/a.py"]}
        first = group_paths_by_owner(["src/a.py"], manifests)
        second = group_paths_by_owner(["src/a.py"], manifests)
        assert first == second == {"E1-F1-S1-T1": ["src/a.py"]}

    def test_empty_paths_yield_no_groups(self) -> None:
        assert group_paths_by_owner([], {"E1-F1-S1-T1": ["src/a.py"]}) == {}


class TestQuarantinePaths:
    def test_no_paths_creates_no_stash(self, tmp_path: Path) -> None:
        repo = _repo(tmp_path, {"src/a.py": "x\n"})
        assert quarantine_paths(repo, [], {}, "E1-F1-S1-T1") == []
        assert _stash_subjects(repo) == []

    def test_modified_tracked_file_leaves_the_worktree(self, tmp_path: Path) -> None:
        repo = _repo(tmp_path, {"src/a.py": "baseline\n"})
        (repo / "src/a.py").write_text("sibling work\n", encoding="utf-8")
        quarantine_paths(repo, ["src/a.py"], {}, "E1-F1-S1-T1")
        assert _porcelain(repo) == []
        assert (repo / "src/a.py").read_text(encoding="utf-8") == "baseline\n"

    def test_untracked_file_is_captured(self, tmp_path: Path) -> None:
        repo = _repo(tmp_path, {"src/a.py": "baseline\n"})
        (repo / "src/new.py").write_text("brand new\n", encoding="utf-8")
        quarantine_paths(repo, ["src/new.py"], {}, "E1-F1-S1-T1")
        assert _porcelain(repo) == []
        assert not (repo / "src/new.py").exists()

    def test_staged_file_is_captured(self, tmp_path: Path) -> None:
        repo = _repo(tmp_path, {"src/a.py": "baseline\n"})
        (repo / "src/a.py").write_text("staged work\n", encoding="utf-8")
        subprocess.run(["git", "add", "src/a.py"], cwd=repo, check=True, capture_output=True)
        quarantine_paths(repo, ["src/a.py"], {}, "E1-F1-S1-T1")
        assert _porcelain(repo) == []

    def test_quarantined_content_is_recoverable(self, tmp_path: Path) -> None:
        """Non-destructive is the whole point: a blocked unit's work is often correct."""
        repo = _repo(tmp_path, {"src/a.py": "baseline\n"})
        work = "expensive executor output\n"
        (repo / "src/a.py").write_text(work, encoding="utf-8")
        quarantine_paths(repo, ["src/a.py"], {}, "E1-F1-S1-T1")
        subprocess.run(["git", "stash", "pop"], cwd=repo, check=True, capture_output=True)
        assert (repo / "src/a.py").read_text(encoding="utf-8") == work

    def test_paths_outside_the_named_set_are_untouched(self, tmp_path: Path) -> None:
        repo = _repo(tmp_path, {"src/a.py": "baseline\n", "src/keep.py": "baseline\n"})
        (repo / "src/a.py").write_text("quarantine me\n", encoding="utf-8")
        (repo / "src/keep.py").write_text("leave me alone\n", encoding="utf-8")
        quarantine_paths(repo, ["src/a.py"], {}, "E1-F1-S1-T1")
        assert (repo / "src/keep.py").read_text(encoding="utf-8") == "leave me alone\n"
        assert _porcelain(repo) == ["src/keep.py"]

    def test_stash_message_names_owner_and_claiming_unit(self, tmp_path: Path) -> None:
        repo = _repo(tmp_path, {"src/a.py": "baseline\n"})
        (repo / "src/a.py").write_text("sibling work\n", encoding="utf-8")
        records = quarantine_paths(repo, ["src/a.py"], {"E1-F1-S1-T9": ["src/a.py"]}, "E1-F1-S1-T1")
        assert len(records) == 1
        assert records[0].owner_id == "E1-F1-S1-T9"
        assert records[0].paths == ("src/a.py",)
        subject = _stash_subjects(repo)[0]
        assert f"{QUARANTINE_STASH_PREFIX}:E1-F1-S1-T9" in subject
        assert "displaced by claim of E1-F1-S1-T1" in subject

    def test_each_owner_gets_its_own_entry(self, tmp_path: Path) -> None:
        repo = _repo(tmp_path, {"src/a.py": "baseline\n", "src/b.py": "baseline\n"})
        (repo / "src/a.py").write_text("owned by T8\n", encoding="utf-8")
        (repo / "src/b.py").write_text("owned by T9\n", encoding="utf-8")
        records = quarantine_paths(
            repo,
            ["src/a.py", "src/b.py"],
            {"E1-F1-S1-T8": ["src/a.py"], "E1-F1-S1-T9": ["src/b.py"]},
            "E1-F1-S1-T1",
        )
        assert [r.owner_id for r in records] == ["E1-F1-S1-T8", "E1-F1-S1-T9"]
        assert len(_stash_subjects(repo)) == 2
        assert _porcelain(repo) == []

    def test_git_failure_raises_rather_than_reporting_success(self, tmp_path: Path) -> None:
        """A swallowed failure would hand the claiming unit the tree it was meant to clear."""
        not_a_repo = tmp_path / "plain"
        not_a_repo.mkdir()
        (not_a_repo / "a.py").write_text("x\n", encoding="utf-8")
        with pytest.raises(RuntimeError, match="quarantine: git stash push"):
            quarantine_paths(not_a_repo, ["a.py"], {}, "E1-F1-S1-T1")

    def test_timeout_raises_runtime_error(self, tmp_path: Path) -> None:
        from unittest.mock import patch

        repo = _repo(tmp_path, {"src/a.py": "baseline\n"})
        (repo / "src/a.py").write_text("sibling work\n", encoding="utf-8")
        with (
            patch("subprocess.run", side_effect=subprocess.TimeoutExpired(cmd="git", timeout=1)),
            pytest.raises(RuntimeError, match="timed out"),
        ):
            quarantine_paths(repo, ["src/a.py"], {}, "E1-F1-S1-T1")
