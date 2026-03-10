"""Shared pytest fixtures for the judges test suite."""

from __future__ import annotations

import os

# Set required env vars before any devbench modules are imported.
# config.py raises RuntimeError at import time if these are unset.
os.environ.setdefault("JUDGE_CLAUDE_MODEL", "test-model")
os.environ.setdefault("JUDGE_ALLOWED_REPOS", "caylent-solutions/git-repo,caylent-solutions/devbench")
os.environ.setdefault("JUDGE_WORKSPACE_ROOT", "/tmp/test-workspace")
os.environ.setdefault("JUDGE_LOG_FILE", "/tmp/judges-test-orchestrator.log")

from pathlib import Path
from unittest.mock import patch

import pytest
from testing import make_llm_pass_result

from devbench.backlog.work_unit import WorkUnit, WorkUnitStatus, WorkUnitType

_WORKSPACE_ROOT = os.environ.get("JUDGE_WORKSPACE_ROOT", "/tmp/test-workspace")

# ---------------------------------------------------------------------------
# Work-unit markdown template
# ---------------------------------------------------------------------------

_WORK_UNIT_TEMPLATE = """\
# {unit_id}: {title}

## Status: {status}

## Description

{description}

## Target Repository

- **Repo:** `{repo}`
- **Local path:** `{workspace_root}/{repo_short}`
- **Branch:** `backlog/{unit_id_lower}`

## Dependencies

| ID | Title | Status |
|----|-------|--------|
{dep_rows}

## Acceptance Criteria

- [ ] AC-FUNC-001 Implement the primary feature
- [ ] AC-TEST-001 All tests pass
- [ ] AC-DOC-001 Update `README.md` with new feature documentation

## Changes Manifest

- `src/main.py`
- `tests/test_main.py`
- `README.md`

## Comments
"""


@pytest.fixture
def tmp_work_unit_file(tmp_path: Path) -> Path:
    """Create a temporary .md file with valid work-unit format."""
    content = _WORK_UNIT_TEMPLATE.format(
        unit_id="E0-F1-S1-T1",
        title="Create Test Makefile",
        status="in-queue",
        description="Create the Makefile structure for the test repository.",
        repo="caylent-solutions/git-repo",
        repo_short="git-repo",
        unit_id_lower="e0-f1-s1-t1",
        dep_rows="| E0-F1-S1 | git-repo Makefile and Targets | Done |",
        workspace_root=_WORKSPACE_ROOT,
    )
    file_path = tmp_path / "E0-F1-S1-T1.md"
    file_path.write_text(content)
    return file_path


@pytest.fixture
def tmp_repo_dir(tmp_path: Path) -> Path:
    """Create a temporary directory with an initialised git repository."""
    import subprocess

    repo_dir = tmp_path / "repo"
    repo_dir.mkdir()

    subprocess.run(
        ["git", "init"],
        cwd=repo_dir,
        capture_output=True,
        check=True,
    )
    subprocess.run(
        ["git", "config", "user.email", "test@example.com"],
        cwd=repo_dir,
        capture_output=True,
        check=True,
    )
    subprocess.run(
        ["git", "config", "user.name", "Test User"],
        cwd=repo_dir,
        capture_output=True,
        check=True,
    )
    # Create initial commit
    readme = repo_dir / "README.md"
    readme.write_text("# Test Repo\n")
    subprocess.run(
        ["git", "add", "-A"],
        cwd=repo_dir,
        capture_output=True,
        check=True,
    )
    subprocess.run(
        ["git", "commit", "-m", "Initial commit"],
        cwd=repo_dir,
        capture_output=True,
        check=True,
    )
    # Ensure branch is called "main" regardless of system git defaults
    subprocess.run(
        ["git", "branch", "-M", "main"],
        cwd=repo_dir, capture_output=True, check=True,
    )
    # Set up origin so _get_default_branch() works: point origin at self,
    # fetch to create origin/main, then write origin/HEAD symref.
    subprocess.run(
        ["git", "remote", "add", "origin", repo_dir.as_posix()],
        cwd=repo_dir, capture_output=True, check=True,
    )
    subprocess.run(
        ["git", "fetch", "origin"],
        cwd=repo_dir, capture_output=True, check=True,
    )
    head_ref = repo_dir / ".git" / "refs" / "remotes" / "origin" / "HEAD"
    head_ref.write_text("ref: refs/remotes/origin/main\n")
    return repo_dir


@pytest.fixture
def mock_backlog_index(tmp_path: Path) -> Path:
    """Create a temporary BACKLOG.md with sample table rows."""
    content = """\
# Backlog

## Full Work Unit Index

### E0: Repository Development Tooling Setup

| ID | Title | Type | Status | Dependencies | Repo | File Path |
|-----|-------|------|--------|-------------|------|-----------|
| E0-F1-S1-T1 | Create Makefile | Task | in-queue | None | git-repo | `backlog/E0-F1-S1-T1.md` |
| E0-F1-S1-T2 | Lint Targets | Task | in-queue | E0-F1-S1-T1 | git-repo | `backlog/E0-F1-S1-T2.md` |
| E0-F1-S1-T3 | Test Targets | Task | done | None | git-repo | `backlog/E0-F1-S1-T3.md` |
| E0-F1-S1 | Story One | Story | in-queue | None | git-repo | `backlog/E0-F1-S1.md` |
| E0-F1 | Feature One | Feature | in-queue | None | git-repo | `backlog/E0-F1.md` |
"""
    index_path = tmp_path / "BACKLOG.md"
    index_path.write_text(content)
    return index_path


@pytest.fixture
def sample_work_unit(tmp_path: Path) -> WorkUnit:
    """Return a WorkUnit dataclass instance with test data, backed by a real file."""
    file_path = tmp_path / "E0-F1-S1-T1.md"
    file_path.write_text(
        _WORK_UNIT_TEMPLATE.format(
            unit_id="E0-F1-S1-T1",
            title="Create Test Makefile",
            status="in-queue",
            description="Sample description.",
            repo="caylent-solutions/git-repo",
            repo_short="git-repo",
            unit_id_lower="e0-f1-s1-t1",
            dep_rows="| E0-F1-S1 | Story One | Done |",
            workspace_root=_WORKSPACE_ROOT,
        )
    )
    return WorkUnit(
        id="E0-F1-S1-T1",
        title="Create Test Makefile",
        status=WorkUnitStatus.IN_QUEUE,
        unit_type=WorkUnitType.TASK,
        file_path=file_path,
        repo="caylent-solutions/git-repo",
        dependencies=["E0-F1-S1"],
        acceptance_criteria=["AC-FUNC-001", "AC-TEST-001"],
        description="Sample description.",
    )


@pytest.fixture
def mock_llm_pass():
    """Context manager fixture that mocks _llm_evaluate to return PASS for any judge."""

    def _mock_llm_evaluate(self, system_prompt, evidence_sections, cwd=None, timeout=None):
        return make_llm_pass_result(self.name)

    with patch("devbench.judges.base.BaseJudge._llm_evaluate", _mock_llm_evaluate):
        yield
