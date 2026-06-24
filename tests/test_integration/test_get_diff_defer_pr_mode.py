"""Integration tests for ADR-12 mode-aware cmd_get_diff.

Exercises the full CLI entry point against real git repositories and a
real backlog workspace. The defer_pr-mode test is the regression pin
against the 2026-04-20 kanon judge misread: with N prior commits on a
shared branch and one new staged change, get-diff must return only the
staged change -- not the accumulated branch-vs-default diff.
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
    ONLY the staged change and must NOT include any prior commit."""

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
    emission is unchanged. Branch-vs-default IS included."""

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
            patch("devbench.cli.resolve_repo", return_value="caylent-solutions/example"),
            patch("devbench.cli.validate_repo", return_value=None),
            patch("devbench.cli.get_configured_default_branch", return_value="main"),
            patch("devbench.config.DEFER_PR", False),
        ):
            rc = cli.cmd_get_diff("EX-F1-S1-T1")

        assert rc == 0
        output = capsys.readouterr().out
        assert "current.py" in output, "Staged file missing from per-branch mode output"
        assert any(f"prior-{i}.py" in output for i in range(3)), (
            "Per-branch mode must include branch-vs-default hunk (which contains prior commits)"
        )
