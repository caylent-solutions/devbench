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
    CHECKPOINT_REF_PREFIX,
    QUARANTINE_STASH_PREFIX,
    UNATTRIBUTED_OWNER,
    _paths_with_local_work,
    checkpoint_ref,
    checkpoint_work,
    find_checkpoint,
    find_quarantine_stash,
    group_paths_by_owner,
    quarantine_paths,
    restore_quarantine,
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


class TestFindQuarantineStash:
    def test_none_when_the_owner_has_no_quarantine(self, tmp_path: Path) -> None:
        repo = _repo(tmp_path, {"src/a.py": "baseline\n"})
        assert find_quarantine_stash(repo, "E1-F1-S1-T1") is None

    def test_owners_own_entry_is_found(self, tmp_path: Path) -> None:
        repo = _repo(tmp_path, {"src/a.py": "baseline\n"})
        (repo / "src/a.py").write_text("work\n", encoding="utf-8")
        quarantine_paths(repo, ["src/a.py"], {"E1-F1-S1-T1": ["src/a.py"]}, "E1-F1-S1-T2")
        assert find_quarantine_stash(repo, "E1-F1-S1-T1") == "stash@{0}"

    def test_another_owners_entry_is_not_returned(self, tmp_path: Path) -> None:
        repo = _repo(tmp_path, {"src/a.py": "baseline\n"})
        (repo / "src/a.py").write_text("work\n", encoding="utf-8")
        quarantine_paths(repo, ["src/a.py"], {"E1-F1-S1-T1": ["src/a.py"]}, "E1-F1-S1-T2")
        assert find_quarantine_stash(repo, "E9-F9-S9-T9") is None

    def test_a_human_stash_is_never_matched(self, tmp_path: Path) -> None:
        """Only devbench-created entries are eligible; a hand-made stash is the operator's."""
        repo = _repo(tmp_path, {"src/a.py": "baseline\n"})
        (repo / "src/a.py").write_text("human work\n", encoding="utf-8")
        subprocess.run(
            ["git", "stash", "push", "--message", "E1-F1-S1-T1 my own notes"],
            cwd=repo,
            check=True,
            capture_output=True,
        )
        assert find_quarantine_stash(repo, "E1-F1-S1-T1") is None

    def test_most_recent_entry_wins_when_an_owner_was_displaced_twice(self, tmp_path: Path) -> None:
        repo = _repo(tmp_path, {"src/a.py": "baseline\n"})
        manifests = {"E1-F1-S1-T1": ["src/a.py"]}
        (repo / "src/a.py").write_text("first attempt\n", encoding="utf-8")
        quarantine_paths(repo, ["src/a.py"], manifests, "E1-F1-S1-T2")
        (repo / "src/a.py").write_text("second attempt\n", encoding="utf-8")
        quarantine_paths(repo, ["src/a.py"], manifests, "E1-F1-S1-T3")
        # git pushes each new entry at stash@{0}, so index 0 is the newest.
        assert find_quarantine_stash(repo, "E1-F1-S1-T1") == "stash@{0}"


class TestRestoreQuarantine:
    def test_none_when_there_is_nothing_to_restore(self, tmp_path: Path) -> None:
        repo = _repo(tmp_path, {"src/a.py": "baseline\n"})
        assert restore_quarantine(repo, "E1-F1-S1-T1", {"src/a.py"}) is None

    def test_displaced_work_comes_back_into_the_worktree(self, tmp_path: Path) -> None:
        repo = _repo(tmp_path, {"src/a.py": "baseline\n"})
        work = "expensive executor output\n"
        (repo / "src/a.py").write_text(work, encoding="utf-8")
        quarantine_paths(repo, ["src/a.py"], {"E1-F1-S1-T1": ["src/a.py"]}, "E1-F1-S1-T2")
        assert (repo / "src/a.py").read_text(encoding="utf-8") == "baseline\n"

        record = restore_quarantine(repo, "E1-F1-S1-T1", {"src/a.py"})

        assert record is not None
        assert record.owner_id == "E1-F1-S1-T1"
        assert record.paths == ("src/a.py",)
        assert (repo / "src/a.py").read_text(encoding="utf-8") == work

    def test_untracked_work_comes_back(self, tmp_path: Path) -> None:
        repo = _repo(tmp_path, {"src/a.py": "baseline\n"})
        (repo / "src/new.py").write_text("brand new\n", encoding="utf-8")
        quarantine_paths(repo, ["src/new.py"], {"E1-F1-S1-T1": ["src/new.py"]}, "E1-F1-S1-T2")
        assert not (repo / "src/new.py").exists()

        restore_quarantine(repo, "E1-F1-S1-T1", {"src/new.py"})

        assert (repo / "src/new.py").read_text(encoding="utf-8") == "brand new\n"

    def test_staged_state_is_preserved_across_the_round_trip(self, tmp_path: Path) -> None:
        """The manifest-scope check reads the index, so staged-ness must survive."""
        repo = _repo(tmp_path, {"src/a.py": "baseline\n"})
        (repo / "src/a.py").write_text("staged work\n", encoding="utf-8")
        subprocess.run(["git", "add", "src/a.py"], cwd=repo, check=True, capture_output=True)
        quarantine_paths(repo, ["src/a.py"], {"E1-F1-S1-T1": ["src/a.py"]}, "E1-F1-S1-T2")

        restore_quarantine(repo, "E1-F1-S1-T1", {"src/a.py"})

        staged = subprocess.run(
            ["git", "diff", "--cached", "--name-only"], cwd=repo, check=True, capture_output=True, text=True
        )
        assert staged.stdout.split() == ["src/a.py"]

    def test_the_entry_is_consumed_so_it_cannot_apply_twice(self, tmp_path: Path) -> None:
        repo = _repo(tmp_path, {"src/a.py": "baseline\n"})
        (repo / "src/a.py").write_text("work\n", encoding="utf-8")
        quarantine_paths(repo, ["src/a.py"], {"E1-F1-S1-T1": ["src/a.py"]}, "E1-F1-S1-T2")

        restore_quarantine(repo, "E1-F1-S1-T1", {"src/a.py"})

        assert _stash_subjects(repo) == []
        assert restore_quarantine(repo, "E1-F1-S1-T1", {"src/a.py"}) is None

    def test_refuses_when_the_stash_holds_a_path_outside_the_manifest(self, tmp_path: Path) -> None:
        """Restoring out-of-scope work would recreate the contamination quarantine removes."""
        repo = _repo(tmp_path, {"src/a.py": "baseline\n", "src/b.py": "baseline\n"})
        (repo / "src/a.py").write_text("in scope\n", encoding="utf-8")
        (repo / "src/b.py").write_text("out of scope\n", encoding="utf-8")
        quarantine_paths(repo, ["src/a.py", "src/b.py"], {"E1-F1-S1-T1": ["src/a.py", "src/b.py"]}, "E1-F1-S1-T2")

        with pytest.raises(RuntimeError, match="outside its Changes Manifest"):
            restore_quarantine(repo, "E1-F1-S1-T1", {"src/a.py"})

        # The entry survives the refusal: nothing is lost when the restore declines.
        assert find_quarantine_stash(repo, "E1-F1-S1-T1") == "stash@{0}"

    def test_refuses_when_a_target_path_already_holds_local_work(self, tmp_path: Path) -> None:
        """A newer attempt in the tree outranks an older displaced one; never clobber it."""
        repo = _repo(tmp_path, {"src/a.py": "baseline\n"})
        (repo / "src/a.py").write_text("older attempt\n", encoding="utf-8")
        quarantine_paths(repo, ["src/a.py"], {"E1-F1-S1-T1": ["src/a.py"]}, "E1-F1-S1-T2")
        (repo / "src/a.py").write_text("newer attempt\n", encoding="utf-8")

        with pytest.raises(RuntimeError, match="already holds uncommitted work"):
            restore_quarantine(repo, "E1-F1-S1-T1", {"src/a.py"})

        assert (repo / "src/a.py").read_text(encoding="utf-8") == "newer attempt\n"
        assert find_quarantine_stash(repo, "E1-F1-S1-T1") == "stash@{0}"

    def test_unattributed_work_is_never_restored(self, tmp_path: Path) -> None:
        """The unattributed bucket has no owner to hand it back to."""
        repo = _repo(tmp_path, {"src/a.py": "baseline\n"})
        (repo / "src/orphan.py").write_text("nobody claims me\n", encoding="utf-8")
        quarantine_paths(repo, ["src/orphan.py"], {}, "E1-F1-S1-T2")

        assert restore_quarantine(repo, UNATTRIBUTED_OWNER, {"src/orphan.py"}) is None
        assert _stash_subjects(repo) == [
            f"On main: {QUARANTINE_STASH_PREFIX}:{UNATTRIBUTED_OWNER}: displaced by claim of E1-F1-S1-T2"
        ]


class TestCheckpointWork:
    def test_clean_checkout_produces_no_checkpoint(self, tmp_path: Path) -> None:
        repo = _repo(tmp_path, {"src/a.py": "baseline\n"})
        assert checkpoint_work(repo, "E1-F1-S1-T1") is None
        assert find_checkpoint(repo, "E1-F1-S1-T1") is None

    def test_in_flight_work_is_snapshotted_to_a_ref(self, tmp_path: Path) -> None:
        repo = _repo(tmp_path, {"src/a.py": "baseline\n"})
        (repo / "src/a.py").write_text("in flight\n", encoding="utf-8")

        sha = checkpoint_work(repo, "E1-F1-S1-T1")

        assert sha is not None
        assert find_checkpoint(repo, "E1-F1-S1-T1") == sha

    def test_the_worktree_is_not_disturbed(self, tmp_path: Path) -> None:
        """Checkpointing runs at stop time; it must never move the files it snapshots."""
        repo = _repo(tmp_path, {"src/a.py": "baseline\n"})
        (repo / "src/a.py").write_text("in flight\n", encoding="utf-8")

        checkpoint_work(repo, "E1-F1-S1-T1")

        assert (repo / "src/a.py").read_text(encoding="utf-8") == "in flight\n"
        assert _porcelain(repo) == ["src/a.py"]
        assert _stash_subjects(repo) == []

    def test_checkpoint_content_is_recoverable(self, tmp_path: Path) -> None:
        repo = _repo(tmp_path, {"src/a.py": "baseline\n"})
        work = "expensive executor output\n"
        (repo / "src/a.py").write_text(work, encoding="utf-8")

        sha = checkpoint_work(repo, "E1-F1-S1-T1")

        shown = subprocess.run(["git", "show", f"{sha}:src/a.py"], cwd=repo, check=True, capture_output=True, text=True)
        assert shown.stdout == work

    def test_checkpoint_survives_a_stash_clear(self, tmp_path: Path) -> None:
        """The whole reason for a ref: the stash stack is not a safe sole copy."""
        repo = _repo(tmp_path, {"src/a.py": "baseline\n"})
        (repo / "src/a.py").write_text("expensive\n", encoding="utf-8")
        sha = checkpoint_work(repo, "E1-F1-S1-T1")
        quarantine_paths(repo, ["src/a.py"], {"E1-F1-S1-T1": ["src/a.py"]}, "E1-F1-S1-T2")

        subprocess.run(["git", "stash", "clear"], cwd=repo, check=True, capture_output=True)

        assert _stash_subjects(repo) == []
        assert find_checkpoint(repo, "E1-F1-S1-T1") == sha
        shown = subprocess.run(["git", "show", f"{sha}:src/a.py"], cwd=repo, check=True, capture_output=True, text=True)
        assert shown.stdout == "expensive\n"

    def test_a_newer_snapshot_supersedes_the_previous_one(self, tmp_path: Path) -> None:
        repo = _repo(tmp_path, {"src/a.py": "baseline\n"})
        (repo / "src/a.py").write_text("first\n", encoding="utf-8")
        first = checkpoint_work(repo, "E1-F1-S1-T1")
        (repo / "src/a.py").write_text("second\n", encoding="utf-8")

        second = checkpoint_work(repo, "E1-F1-S1-T1")

        assert second != first
        assert find_checkpoint(repo, "E1-F1-S1-T1") == second

    def test_each_unit_gets_its_own_ref(self, tmp_path: Path) -> None:
        repo = _repo(tmp_path, {"src/a.py": "baseline\n"})
        (repo / "src/a.py").write_text("t1 work\n", encoding="utf-8")
        first = checkpoint_work(repo, "E1-F1-S1-T1")
        (repo / "src/a.py").write_text("t2 work\n", encoding="utf-8")
        second = checkpoint_work(repo, "E1-F1-S1-T2")

        assert find_checkpoint(repo, "E1-F1-S1-T1") == first
        assert find_checkpoint(repo, "E1-F1-S1-T2") == second
        assert first != second

    def test_ref_name_is_namespaced_under_the_devbench_prefix(self) -> None:
        assert checkpoint_ref("E1-F1-S1-T1") == f"{CHECKPOINT_REF_PREFIX}/E1-F1-S1-T1"


class TestRestoreEdgeCases:
    def test_no_paths_means_nothing_is_reported_dirty(self, tmp_path: Path) -> None:
        """Guard for the empty case: `git status -- ` with no pathspec scans everything."""
        repo = _repo(tmp_path, {"src/a.py": "baseline\n"})
        (repo / "src/a.py").write_text("dirty\n", encoding="utf-8")
        assert _paths_with_local_work(repo, ()) == ()

    def test_stash_listing_lines_without_the_separator_are_skipped(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A malformed listing line must not crash the lookup or mint a false match."""
        repo = _repo(tmp_path, {"src/a.py": "baseline\n"})
        monkeypatch.setattr(
            "devbench.git_quarantine._git",
            lambda _repo, _args: "garbage-without-separator\n",
        )
        assert find_quarantine_stash(repo, "E1-F1-S1-T1") is None
