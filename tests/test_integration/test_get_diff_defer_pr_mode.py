"""Integration tests for ADR-12 mode-aware cmd_get_diff.

Exercises the full CLI entry point against real git repositories and a
real backlog workspace. The defer_pr-mode test is the regression pin
against the 2026-04-20 kanon judge misread: with N prior commits on a
shared branch and one new staged change, get-diff must return only the
staged change -- not the accumulated branch-vs-default diff.

``TestGetDiffDeferPrPostCommitTaskAttribution`` (db-247, FR-13) is the
co-designed companion e2e: with two tasks committed on the shared branch
(task A, then task B), a post-commit ``get-diff`` for task A must resolve
task A's OWN commit via ``git log --grep '^<id>:'`` and return only task
A's change -- never task B's commit, even though task B's commit is HEAD.
"""

from __future__ import annotations

import subprocess
from pathlib import Path
from unittest.mock import patch

import pytest

from devbench import cli

BACKLOG_INDEX_TEMPLATE = """\
# Backlog

## Status Summary

| Epic | Title | Done | In Progress | In Queue | Blocked |
|------|-------|------|-------------|----------|---------|
| EX | Example Epic | 0 | 1 | 0 | 0 |

## Full Work Unit Index

| ID | Title | Type | Status | Dependencies | Repo | File Path |
|----|-------|------|--------|--------------|------|-----------|
| EX-F1-S1-T1 | Sample Task | Task | in-progress | None | caylent-solutions/example | `backlog/EX-F1-S1-T1.md` |
"""

WORK_UNIT_TEMPLATE = """\
# EX-F1-S1-T1: Sample Task

## Status: in-progress

## Description

Example task description.

## Dependencies

| ID | Title | Status |
|----|-------|--------|
| none | | |

## Changes Manifest

| File | Change |
|------|--------|
| `current.py` | new module |
| `prior-0.py` | also owned by this task |
"""


def _run_git(repo: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True)


def _build_workspace(tmp_path: Path) -> tuple[Path, Path]:
    """Create a workspace + a git repo with 3 prior commits on a shared branch.

    Returns (workspace_root, repo_path).
    """
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "BACKLOG.md").write_text(BACKLOG_INDEX_TEMPLATE, encoding="utf-8")
    backlog_dir = workspace / "backlog"
    backlog_dir.mkdir()
    (backlog_dir / "EX-F1-S1-T1.md").write_text(WORK_UNIT_TEMPLATE, encoding="utf-8")

    repo = tmp_path / "target-repo"
    repo.mkdir()
    _run_git(repo, "init", "--initial-branch=main")
    _run_git(repo, "config", "user.email", "t@t")
    _run_git(repo, "config", "user.name", "t")
    (repo / "README.md").write_text("seed\n", encoding="utf-8")
    _run_git(repo, "add", "README.md")
    _run_git(repo, "commit", "-m", "seed")
    # Simulate an `origin/main` remote-tracking ref pointing at the seed
    # commit, so `git diff origin/main` has a target in both modes.
    _run_git(repo, "update-ref", "refs/remotes/origin/main", "main")
    _run_git(repo, "checkout", "-b", "feat/shared")

    for i in range(3):
        path = repo / f"prior-{i}.py"
        path.write_text(f"prior content {i}\n", encoding="utf-8")
        _run_git(repo, "add", f"prior-{i}.py")
        _run_git(repo, "commit", "-m", f"prior task {i}")

    (repo / "current.py").write_text("current task content\n", encoding="utf-8")
    _run_git(repo, "add", "current.py")

    return workspace, repo


class TestGetDiffDeferPrModeReturnsTaskLocalScope:
    """ADR-12 regression pin: with 3 prior commits on the shared branch
    plus a current staged change, defer_pr-mode get-diff must return
    ONLY the staged change and must NOT include any prior commit -- even
    when one of those prior commits (`prior-0.py`) IS in this task's own
    Manifest, proving the exclusion is the structural "no branch-vs-default
    hunk in defer_pr mode" behaviour (ADR-12), not merely Manifest scoping
    (db-296/FR-12)."""

    def test_get_diff_in_defer_pr_mode_returns_task_local_scope(
        self,
        tmp_path: Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        workspace, repo = _build_workspace(tmp_path)

        with (
            patch("devbench.cli.WORKSPACE_ROOT", workspace),
            patch("devbench.cli.BACKLOG_ROOT", workspace / "backlog"),
            patch("devbench.cli.BACKLOG_INDEX", workspace / "BACKLOG.md"),
            patch("devbench.cli.REPO_LOCAL_PATHS", {"caylent-solutions/example": repo}),
            patch("devbench.work_unit_scope.BACKLOG_ROOT", workspace / "backlog"),
            patch("devbench.work_unit_scope.BACKLOG_INDEX", workspace / "BACKLOG.md"),
            patch("devbench.cli.resolve_repo", return_value="caylent-solutions/example"),
            patch("devbench.cli.validate_repo", return_value=None),
            patch("devbench.cli.get_configured_default_branch", return_value="main"),
            patch("devbench.config.DEFER_PR", True),
        ):
            rc = cli.cmd_get_diff("EX-F1-S1-T1")

        assert rc == 0
        output = capsys.readouterr().out
        assert "current.py" in output, "Expected current staged file in output"
        for i in range(3):
            assert f"prior-{i}.py" not in output, f"ADR-12 regression: prior-{i}.py leaked into defer_pr-mode output"


class TestGetDiffPerBranchModeUnchanged:
    """ADR-12 back-compat pin: with defer_pr=False the four-hunk
    emission is unchanged. Branch-vs-default IS included, Manifest-scoped
    (db-296/FR-12): `prior-0.py` is in this task's Manifest and appears;
    `prior-1.py`/`prior-2.py` are not and do not."""

    def test_get_diff_in_per_branch_mode_unchanged(
        self,
        tmp_path: Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        workspace, repo = _build_workspace(tmp_path)

        with (
            patch("devbench.cli.WORKSPACE_ROOT", workspace),
            patch("devbench.cli.BACKLOG_ROOT", workspace / "backlog"),
            patch("devbench.cli.BACKLOG_INDEX", workspace / "BACKLOG.md"),
            patch("devbench.cli.REPO_LOCAL_PATHS", {"caylent-solutions/example": repo}),
            patch("devbench.work_unit_scope.BACKLOG_ROOT", workspace / "backlog"),
            patch("devbench.work_unit_scope.BACKLOG_INDEX", workspace / "BACKLOG.md"),
            patch("devbench.cli.resolve_repo", return_value="caylent-solutions/example"),
            patch("devbench.cli.validate_repo", return_value=None),
            patch("devbench.cli.get_configured_default_branch", return_value="main"),
            patch("devbench.config.DEFER_PR", False),
        ):
            rc = cli.cmd_get_diff("EX-F1-S1-T1")

        assert rc == 0
        output = capsys.readouterr().out
        assert "current.py" in output, "Staged file missing from per-branch mode output"
        assert "prior-0.py" in output, (
            "Per-branch mode must include the branch-vs-default hunk for a Manifest-owned prior file"
        )
        assert "prior-1.py" not in output, "prior-1.py is not in the Manifest and must not appear"
        assert "prior-2.py" not in output, "prior-2.py is not in the Manifest and must not appear"


BACKLOG_INDEX_TWO_TASKS_TEMPLATE = """\
# Backlog

## Status Summary

| Epic | Title | Done | In Progress | In Queue | Blocked |
|------|-------|------|-------------|----------|---------|
| EX | Example Epic | 0 | 2 | 0 | 0 |

## Full Work Unit Index

| ID | Title | Type | Status | Dependencies | Repo | File Path |
|----|-------|------|--------|--------------|------|-----------|
| EX-F1-S1-T1 | Task A | Task | in-progress | None | caylent-solutions/example | `backlog/EX-F1-S1-T1.md` |
| EX-F1-S1-T2 | Task B | Task | in-progress | None | caylent-solutions/example | `backlog/EX-F1-S1-T2.md` |
"""

WORK_UNIT_TASK_A_TEMPLATE = """\
# EX-F1-S1-T1: Task A

## Status: in-progress

## Description

Task A description.

## Dependencies

| ID | Title | Status |
|----|-------|--------|
| none | | |

## Changes Manifest

| File | Change |
|------|--------|
| `a.py` | task A module |
"""

WORK_UNIT_TASK_B_TEMPLATE = """\
# EX-F1-S1-T2: Task B

## Status: in-progress

## Description

Task B description.

## Dependencies

| ID | Title | Status |
|----|-------|--------|
| none | | |

## Changes Manifest

| File | Change |
|------|--------|
| `b.py` | task B module |
"""


def _build_two_task_workspace(tmp_path: Path) -> tuple[Path, Path]:
    """Create a workspace with two work units, and a repo where task A's
    commit landed on the shared branch BEFORE task B's commit (which is
    HEAD). Returns (workspace_root, repo_path).
    """
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "BACKLOG.md").write_text(BACKLOG_INDEX_TWO_TASKS_TEMPLATE, encoding="utf-8")
    backlog_dir = workspace / "backlog"
    backlog_dir.mkdir()
    (backlog_dir / "EX-F1-S1-T1.md").write_text(WORK_UNIT_TASK_A_TEMPLATE, encoding="utf-8")
    (backlog_dir / "EX-F1-S1-T2.md").write_text(WORK_UNIT_TASK_B_TEMPLATE, encoding="utf-8")

    repo = tmp_path / "target-repo"
    repo.mkdir()
    _run_git(repo, "init", "--initial-branch=main")
    _run_git(repo, "config", "user.email", "t@t")
    _run_git(repo, "config", "user.name", "t")
    (repo / "README.md").write_text("seed\n", encoding="utf-8")
    _run_git(repo, "add", "README.md")
    _run_git(repo, "commit", "-m", "seed")
    _run_git(repo, "update-ref", "refs/remotes/origin/main", "main")
    _run_git(repo, "checkout", "-b", "feat/shared")

    # Task A commits first, using the exact `<unit_id>: <title>` message
    # shape `cmd_git_ops` writes (git_ops.py commit_local/commit_and_push).
    (repo / "a.py").write_text("task A content\n", encoding="utf-8")
    _run_git(repo, "add", "a.py")
    _run_git(repo, "commit", "-m", "EX-F1-S1-T1: Task A")

    # Task B commits second -- this is HEAD by the time get-diff runs for A.
    (repo / "b.py").write_text("task B content\n", encoding="utf-8")
    _run_git(repo, "add", "b.py")
    _run_git(repo, "commit", "-m", "EX-F1-S1-T2: Task B")

    return workspace, repo


class TestGetDiffDeferPrPostCommitTaskAttribution:
    """db-247 (FR-13): a post-commit `get-diff` for task A must resolve task
    A's OWN commit via `git log --grep '^<id>:'`, not HEAD -- HEAD belongs
    to task B, which committed after task A on the shared branch."""

    def test_get_diff_post_commit_returns_own_commit_not_sibling_head(
        self,
        tmp_path: Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        workspace, repo = _build_two_task_workspace(tmp_path)

        with (
            patch("devbench.cli.WORKSPACE_ROOT", workspace),
            patch("devbench.cli.BACKLOG_ROOT", workspace / "backlog"),
            patch("devbench.cli.BACKLOG_INDEX", workspace / "BACKLOG.md"),
            patch("devbench.cli.REPO_LOCAL_PATHS", {"caylent-solutions/example": repo}),
            patch("devbench.work_unit_scope.BACKLOG_ROOT", workspace / "backlog"),
            patch("devbench.work_unit_scope.BACKLOG_INDEX", workspace / "BACKLOG.md"),
            patch("devbench.cli.resolve_repo", return_value="caylent-solutions/example"),
            patch("devbench.cli.validate_repo", return_value=None),
            patch("devbench.config.DEFER_PR", True),
        ):
            rc = cli.cmd_get_diff("EX-F1-S1-T1")

        assert rc == 0
        output = capsys.readouterr().out
        assert "a.py" in output, "Task A's own commit must appear in its post-commit get-diff"
        assert "task A content" in output
        assert "b.py" not in output, "Task B's commit (HEAD) leaked into task A's post-commit get-diff"
        assert "task B content" not in output
