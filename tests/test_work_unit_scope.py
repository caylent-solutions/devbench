"""Tests for `devbench.work_unit_scope.resolve_changed_files` (spec 4.3, PM-6, AC-9).

Every test drives the extracted contract directly, over a scratch backlog
index (`BACKLOG.md` + one work-unit `.md` file) and a scratch git fixture
repo -- never through `devbench.cli`'s command handlers, which are covered
separately in `tests/test_cli.py`.
"""

from __future__ import annotations

import subprocess
from pathlib import Path
from unittest.mock import patch

import pytest

from devbench import work_unit_scope
from devbench.backlog.manifest import ManifestParseError
from devbench.gate_records import compute_scope_hash
from devbench.work_unit_scope import (
    ALLOWED_MODES,
    MODE_DEFER_PR,
    MODE_PER_TASK_BRANCH,
    ScopeResult,
    resolve_changed_files,
)


def _seed_backlog(
    tmp_path: Path,
    unit_id: str = "E0-F1-S1-T1",
    files: tuple[str, ...] = ("owned.py",),
    *,
    manifest_body: str | None = None,
) -> tuple[Path, Path]:
    """Write a scratch `BACKLOG.md` + one work-unit `.md` file under `tmp_path`.

    Returns `(backlog_root, backlog_index)`, matching the shape
    `devbench.config.BACKLOG_ROOT` / `BACKLOG_INDEX` normally resolve to, so
    a test can patch `devbench.work_unit_scope.BACKLOG_ROOT` /
    `BACKLOG_INDEX` and have `resolve_changed_files` resolve `unit_id`
    against this fixture instead of the live backlog.
    """
    backlog_root = tmp_path / "backlog"
    backlog_root.mkdir(exist_ok=True)
    wu_file = backlog_root / f"{unit_id}.md"
    if manifest_body is None:
        rows = "".join(f"| `{f}` | modify |\n" for f in files)
        manifest_body = f"| File | Change |\n|------|--------|\n{rows}"
    wu_file.write_text(
        f"# {unit_id}: Scope test task\n\n## Status: in-progress\n\n"
        f"## Changes Manifest\n\n{manifest_body}\n\n## Comments\n",
        encoding="utf-8",
    )
    backlog_index = tmp_path / "BACKLOG.md"
    backlog_index.write_text(
        "## Full Work Unit Index\n\n"
        "| ID | Title | Type | Status | Dependencies | Repo | File Path |\n"
        "|-----|-------|------|--------|-------------|------|----------|\n"
        f"| {unit_id} | Scope test task | Task | in-progress | None | git-repo | `backlog/{unit_id}.md` |\n",
        encoding="utf-8",
    )
    return backlog_root, backlog_index


def _init_repo(repo_path: Path) -> None:
    """Initialise a real git repository at `repo_path` with one commit."""
    repo_path.mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "init"], cwd=repo_path, capture_output=True, check=True)
    subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=repo_path, capture_output=True, check=True)
    subprocess.run(["git", "config", "user.name", "Test User"], cwd=repo_path, capture_output=True, check=True)
    (repo_path / "README.md").write_text("# Test Repo\n", encoding="utf-8")
    subprocess.run(["git", "add", "README.md"], cwd=repo_path, capture_output=True, check=True)
    subprocess.run(["git", "commit", "-m", "Initial commit"], cwd=repo_path, capture_output=True, check=True)


@pytest.mark.unit
class TestResolveChangedFilesModes:
    """AC-E2-F3-S1-T1-1: every ADR-12 mode returns the documented file set."""

    def test_per_task_branch_mode_returns_manifest_files_no_commit_shas(self, tmp_path: Path) -> None:
        repo_path = tmp_path / "repo"
        _init_repo(repo_path)
        (repo_path / "owned.py").write_text("x = 1\n", encoding="utf-8")
        subprocess.run(["git", "add", "owned.py"], cwd=repo_path, capture_output=True, check=True)
        backlog_root, backlog_index = _seed_backlog(tmp_path, files=("owned.py",))

        with (
            patch.object(work_unit_scope, "BACKLOG_ROOT", backlog_root),
            patch.object(work_unit_scope, "BACKLOG_INDEX", backlog_index),
        ):
            result = resolve_changed_files("E0-F1-S1-T1", repo_path, MODE_PER_TASK_BRANCH)

        assert result == ScopeResult(
            files=["owned.py"], mode=MODE_PER_TASK_BRANCH, commit_shas=[], scope_hash=result.scope_hash
        )
        assert result.scope_hash != ""

    def test_defer_pr_mode_resolves_own_commit_shas_by_subject(self, tmp_path: Path) -> None:
        repo_path = tmp_path / "repo"
        _init_repo(repo_path)
        (repo_path / "owned.py").write_text("x = 1\n", encoding="utf-8")
        subprocess.run(["git", "add", "owned.py"], cwd=repo_path, capture_output=True, check=True)
        subprocess.run(
            ["git", "commit", "-m", "E0-F1-S1-T1: seed owned.py"], cwd=repo_path, capture_output=True, check=True
        )
        expected_sha = subprocess.run(
            ["git", "rev-parse", "HEAD"], cwd=repo_path, capture_output=True, check=True, text=True
        ).stdout.strip()
        backlog_root, backlog_index = _seed_backlog(tmp_path, files=("owned.py",))

        with (
            patch.object(work_unit_scope, "BACKLOG_ROOT", backlog_root),
            patch.object(work_unit_scope, "BACKLOG_INDEX", backlog_index),
        ):
            result = resolve_changed_files("E0-F1-S1-T1", repo_path, MODE_DEFER_PR)

        assert result.files == ["owned.py"]
        assert result.mode == MODE_DEFER_PR
        assert result.commit_shas == [expected_sha]

    def test_defer_pr_mode_returns_all_matching_commits_most_recent_first(self, tmp_path: Path) -> None:
        """A task may carry more than one of its own commits (db-247)."""
        repo_path = tmp_path / "repo"
        _init_repo(repo_path)
        (repo_path / "owned.py").write_text("v1\n", encoding="utf-8")
        subprocess.run(["git", "add", "owned.py"], cwd=repo_path, capture_output=True, check=True)
        subprocess.run(["git", "commit", "-m", "E0-F1-S1-T1: initial"], cwd=repo_path, capture_output=True, check=True)
        initial_sha = subprocess.run(
            ["git", "rev-parse", "HEAD"], cwd=repo_path, capture_output=True, check=True, text=True
        ).stdout.strip()
        (repo_path / "owned.py").write_text("v2\n", encoding="utf-8")
        subprocess.run(["git", "add", "owned.py"], cwd=repo_path, capture_output=True, check=True)
        subprocess.run(
            ["git", "commit", "-m", "E0-F1-S1-T1: pr_review_resolution fix"],
            cwd=repo_path,
            capture_output=True,
            check=True,
        )
        fix_sha = subprocess.run(
            ["git", "rev-parse", "HEAD"], cwd=repo_path, capture_output=True, check=True, text=True
        ).stdout.strip()
        backlog_root, backlog_index = _seed_backlog(tmp_path, files=("owned.py",))

        with (
            patch.object(work_unit_scope, "BACKLOG_ROOT", backlog_root),
            patch.object(work_unit_scope, "BACKLOG_INDEX", backlog_index),
        ):
            result = resolve_changed_files("E0-F1-S1-T1", repo_path, MODE_DEFER_PR)

        assert result.commit_shas == [fix_sha, initial_sha]

    def test_defer_pr_mode_no_matching_commit_returns_empty_list_not_error(self, tmp_path: Path) -> None:
        repo_path = tmp_path / "repo"
        _init_repo(repo_path)
        backlog_root, backlog_index = _seed_backlog(tmp_path, files=("owned.py",))

        with (
            patch.object(work_unit_scope, "BACKLOG_ROOT", backlog_root),
            patch.object(work_unit_scope, "BACKLOG_INDEX", backlog_index),
        ):
            result = resolve_changed_files("E0-F1-S1-T1", repo_path, MODE_DEFER_PR)

        assert result.commit_shas == []
        assert result.files == ["owned.py"]

    def test_per_task_branch_mode_never_resolves_commit_shas(self, tmp_path: Path) -> None:
        """Even when a matching commit exists, per_task_branch mode ignores it (ADR-12:
        commit-sha substitution is a defer_pr-only attribution mechanism)."""
        repo_path = tmp_path / "repo"
        _init_repo(repo_path)
        (repo_path / "owned.py").write_text("x = 1\n", encoding="utf-8")
        subprocess.run(["git", "add", "owned.py"], cwd=repo_path, capture_output=True, check=True)
        subprocess.run(
            ["git", "commit", "-m", "E0-F1-S1-T1: seed owned.py"], cwd=repo_path, capture_output=True, check=True
        )
        backlog_root, backlog_index = _seed_backlog(tmp_path, files=("owned.py",))

        with (
            patch.object(work_unit_scope, "BACKLOG_ROOT", backlog_root),
            patch.object(work_unit_scope, "BACKLOG_INDEX", backlog_index),
        ):
            result = resolve_changed_files("E0-F1-S1-T1", repo_path, MODE_PER_TASK_BRANCH)

        assert result.commit_shas == []


@pytest.mark.unit
class TestResolveChangedFilesEmptyManifest:
    def test_empty_manifest_returns_empty_scope_result_without_any_git_query(self, tmp_path: Path) -> None:
        """AC-E2-F3-S1-T1-1/4: a verification-only unit's scope is empty, and the
        function never touches the repo -- proven by pointing repo_path at a
        directory that is not even a git work tree."""
        not_a_repo = tmp_path / "not-a-repo"
        not_a_repo.mkdir()
        backlog_root, backlog_index = _seed_backlog(tmp_path, files=())

        with (
            patch.object(work_unit_scope, "BACKLOG_ROOT", backlog_root),
            patch.object(work_unit_scope, "BACKLOG_INDEX", backlog_index),
        ):
            result = resolve_changed_files("E0-F1-S1-T1", not_a_repo, MODE_DEFER_PR)

        assert result == ScopeResult(files=[], mode=MODE_DEFER_PR, commit_shas=[], scope_hash="")


@pytest.mark.unit
class TestResolveChangedFilesErrorSemantics:
    """AC-E2-F3-S1-T1-3/4: the three documented error paths, verbatim."""

    def test_unknown_unit_id_raises_value_error_naming_id(self, tmp_path: Path) -> None:
        backlog_root, backlog_index = _seed_backlog(tmp_path, unit_id="E0-F1-S1-T1", files=("owned.py",))

        with (
            patch.object(work_unit_scope, "BACKLOG_ROOT", backlog_root),
            patch.object(work_unit_scope, "BACKLOG_INDEX", backlog_index),
        ):
            with pytest.raises(ValueError, match="Unknown work unit id: 'NOPE-1'"):
                resolve_changed_files("NOPE-1", tmp_path / "repo", MODE_PER_TASK_BRANCH)

    def test_missing_repo_path_raises_value_error_naming_path_and_config_key(self, tmp_path: Path) -> None:
        backlog_root, backlog_index = _seed_backlog(tmp_path, files=("owned.py",))
        missing_repo_path = tmp_path / "does-not-exist"

        with (
            patch.object(work_unit_scope, "BACKLOG_ROOT", backlog_root),
            patch.object(work_unit_scope, "BACKLOG_INDEX", backlog_index),
        ):
            with pytest.raises(ValueError, match="does not exist or is not a git work tree") as exc_info:
                resolve_changed_files("E0-F1-S1-T1", missing_repo_path, MODE_PER_TASK_BRANCH)

        assert str(missing_repo_path) in str(exc_info.value)
        assert "checkout_directory" in str(exc_info.value)

    def test_non_work_tree_repo_path_raises_value_error(self, tmp_path: Path) -> None:
        """A directory that exists but has no `.git` entry is not a work tree."""
        not_a_repo = tmp_path / "plain-dir"
        not_a_repo.mkdir()
        backlog_root, backlog_index = _seed_backlog(tmp_path, files=("owned.py",))

        with (
            patch.object(work_unit_scope, "BACKLOG_ROOT", backlog_root),
            patch.object(work_unit_scope, "BACKLOG_INDEX", backlog_index),
        ):
            with pytest.raises(ValueError, match="does not exist or is not a git work tree"):
                resolve_changed_files("E0-F1-S1-T1", not_a_repo, MODE_PER_TASK_BRANCH)

    def test_git_plumbing_failure_raises_runtime_error_with_stderr(self, tmp_path: Path) -> None:
        repo_path = tmp_path / "repo"
        _init_repo(repo_path)
        (repo_path / "owned.py").write_text("x = 1\n", encoding="utf-8")
        subprocess.run(["git", "add", "owned.py"], cwd=repo_path, capture_output=True, check=True)
        subprocess.run(["git", "commit", "-m", "seed"], cwd=repo_path, capture_output=True, check=True)
        backlog_root, backlog_index = _seed_backlog(tmp_path, files=("owned.py",))

        def fake_run_command(cmd: list[str], **_kwargs: object) -> tuple[int, str, str]:
            return (128, "", "fatal: simulated git plumbing failure")

        with (
            patch.object(work_unit_scope, "BACKLOG_ROOT", backlog_root),
            patch.object(work_unit_scope, "BACKLOG_INDEX", backlog_index),
            patch.object(work_unit_scope, "run_command", side_effect=fake_run_command),
        ):
            with pytest.raises(RuntimeError, match="simulated git plumbing failure"):
                resolve_changed_files("E0-F1-S1-T1", repo_path, MODE_DEFER_PR)

    def test_malformed_manifest_raises_manifest_parse_error(self, tmp_path: Path) -> None:
        backlog_root, backlog_index = _seed_backlog(
            tmp_path,
            manifest_body="| File | Change | Extra |\n|---|---|---|\n| `A.py` | modify | oops |",
        )

        with (
            patch.object(work_unit_scope, "BACKLOG_ROOT", backlog_root),
            patch.object(work_unit_scope, "BACKLOG_INDEX", backlog_index),
        ):
            with pytest.raises(ManifestParseError):
                resolve_changed_files("E0-F1-S1-T1", tmp_path / "repo", MODE_PER_TASK_BRANCH)

    def test_deleted_work_unit_file_raises_file_not_found_before_scope_resolution(self, tmp_path: Path) -> None:
        """Deleting the work-unit file the index points at surfaces as
        ``FileNotFoundError`` from ``BacklogParser.parse_index()`` itself
        (a real backlog-index inconsistency), not a second, independent
        existence check inside this module (no fallback logic)."""
        backlog_root, backlog_index = _seed_backlog(tmp_path, files=("owned.py",))
        # Delete the work-unit file the index points at, after seeding the index.
        (backlog_root / "E0-F1-S1-T1.md").unlink()

        with (
            patch.object(work_unit_scope, "BACKLOG_ROOT", backlog_root),
            patch.object(work_unit_scope, "BACKLOG_INDEX", backlog_index),
        ):
            with pytest.raises(FileNotFoundError):
                resolve_changed_files("E0-F1-S1-T1", tmp_path / "repo", MODE_PER_TASK_BRANCH)

    def test_hash_object_count_mismatch_raises_runtime_error(self, tmp_path: Path) -> None:
        """A ``git hash-object`` call that returns the wrong number of hashes
        for the paths queried is a loud ``RuntimeError``, never a silently
        misaligned scope hash."""
        repo_path = tmp_path / "repo"
        _init_repo(repo_path)
        (repo_path / "owned.py").write_text("x = 1\n", encoding="utf-8")
        backlog_root, backlog_index = _seed_backlog(tmp_path, files=("owned.py",))

        def fake_run_command(cmd: list[str], **_kwargs: object) -> tuple[int, str, str]:
            if cmd[:2] == ["git", "hash-object"]:
                # One path queried, zero hashes returned.
                return (0, "", "")
            return (0, "", "")

        with (
            patch.object(work_unit_scope, "BACKLOG_ROOT", backlog_root),
            patch.object(work_unit_scope, "BACKLOG_INDEX", backlog_index),
            patch.object(work_unit_scope, "run_command", side_effect=fake_run_command),
        ):
            with pytest.raises(RuntimeError, match="expected exactly one hash per path"):
                resolve_changed_files("E0-F1-S1-T1", repo_path, MODE_PER_TASK_BRANCH)

    def test_unknown_mode_raises_value_error(self, tmp_path: Path) -> None:
        backlog_root, backlog_index = _seed_backlog(tmp_path, files=("owned.py",))

        with (
            patch.object(work_unit_scope, "BACKLOG_ROOT", backlog_root),
            patch.object(work_unit_scope, "BACKLOG_INDEX", backlog_index),
        ):
            with pytest.raises(ValueError, match="Unknown scope mode 'bogus_mode'"):
                resolve_changed_files("E0-F1-S1-T1", tmp_path / "repo", "bogus_mode")


@pytest.mark.unit
class TestScopeHash:
    """AC-E2-F3-S1-T1-6: the exposed scope hash is the same value the
    gate-record freshness rule (spec 4.2) consumes -- i.e. it is produced by
    `devbench.gate_records.compute_scope_hash`, not an independent formula."""

    def test_scope_hash_matches_compute_scope_hash_over_blob_hashes(self, tmp_path: Path) -> None:
        repo_path = tmp_path / "repo"
        _init_repo(repo_path)
        (repo_path / "owned.py").write_text("x = 1\n", encoding="utf-8")
        subprocess.run(["git", "add", "owned.py"], cwd=repo_path, capture_output=True, check=True)
        expected_blob = subprocess.run(
            ["git", "hash-object", "owned.py"], cwd=repo_path, capture_output=True, check=True, text=True
        ).stdout.strip()
        backlog_root, backlog_index = _seed_backlog(tmp_path, files=("owned.py",))

        with (
            patch.object(work_unit_scope, "BACKLOG_ROOT", backlog_root),
            patch.object(work_unit_scope, "BACKLOG_INDEX", backlog_index),
        ):
            result = resolve_changed_files("E0-F1-S1-T1", repo_path, MODE_PER_TASK_BRANCH)

        assert result.scope_hash == compute_scope_hash({"owned.py": expected_blob})

    def test_scope_hash_changes_when_file_content_changes(self, tmp_path: Path) -> None:
        repo_path = tmp_path / "repo"
        _init_repo(repo_path)
        (repo_path / "owned.py").write_text("v1\n", encoding="utf-8")
        subprocess.run(["git", "add", "owned.py"], cwd=repo_path, capture_output=True, check=True)
        backlog_root, backlog_index = _seed_backlog(tmp_path, files=("owned.py",))

        with (
            patch.object(work_unit_scope, "BACKLOG_ROOT", backlog_root),
            patch.object(work_unit_scope, "BACKLOG_INDEX", backlog_index),
        ):
            first = resolve_changed_files("E0-F1-S1-T1", repo_path, MODE_PER_TASK_BRANCH)

        (repo_path / "owned.py").write_text("v2\n", encoding="utf-8")

        with (
            patch.object(work_unit_scope, "BACKLOG_ROOT", backlog_root),
            patch.object(work_unit_scope, "BACKLOG_INDEX", backlog_index),
        ):
            second = resolve_changed_files("E0-F1-S1-T1", repo_path, MODE_PER_TASK_BRANCH)

        assert first.scope_hash != second.scope_hash

    def test_scope_hash_handles_manifest_path_absent_from_disk(self, tmp_path: Path) -> None:
        """A Manifest-declared file with no on-disk content still hashes (no crash),
        via the absent-blob sentinel rather than being silently dropped."""
        repo_path = tmp_path / "repo"
        _init_repo(repo_path)
        backlog_root, backlog_index = _seed_backlog(tmp_path, files=("never_created.py",))

        with (
            patch.object(work_unit_scope, "BACKLOG_ROOT", backlog_root),
            patch.object(work_unit_scope, "BACKLOG_INDEX", backlog_index),
        ):
            result = resolve_changed_files("E0-F1-S1-T1", repo_path, MODE_PER_TASK_BRANCH)

        assert result.scope_hash == compute_scope_hash({"never_created.py": "<absent>"})


@pytest.mark.unit
class TestAllowedModesConstant:
    def test_allowed_modes_contains_exactly_the_two_adr12_modes(self) -> None:
        assert set(ALLOWED_MODES) == {MODE_PER_TASK_BRANCH, MODE_DEFER_PR}
