"""Shared pytest fixtures for the devbench test suite."""

from __future__ import annotations

import os
from collections.abc import Iterator
from pathlib import Path
from types import ModuleType

# Set required env vars before any devbench modules are imported.
# config.py raises RuntimeError at import time if these are unset.
# Remove any inherited legacy JUDGE_* bootstrap vars so the strict env-var
# checker in config.py does not reject them during test collection (AC-197-10).
os.environ.pop("JUDGE_CLAUDE_MODEL", None)
os.environ.pop("JUDGE_WORKSPACE_ROOT", None)
os.environ.pop("JUDGE_LOG_FILE", None)
os.environ.pop("JUDGE_CONFIG_PATH", None)

# AC-197-10: all pre-import setdefaults use DEVBENCH_* names only.
os.environ.setdefault("DEVBENCH_CLAUDE_MODEL", "test-model")
os.environ.setdefault("DEVBENCH_WORKSPACE_ROOT", "/tmp/test-workspace")
os.environ.setdefault("DEVBENCH_LOG_FILE", "/tmp/judges-test-orchestrator.log")
# Point to the test fixture YAML config so config.py can resolve ALLOWED_REPOS
# from the YAML repos section (the only supported source).
os.environ.setdefault(
    "DEVBENCH_CONFIG_PATH",
    str(Path(__file__).parent / "fixtures" / "test_devbench.yaml"),
)

# Backward-compatibility aliases for modules not yet migrated to DEVBENCH_* names.
# config.py (JUDGE_WORKSPACE_ROOT / JUDGE_CLAUDE_MODEL) has been migrated by
# E9-F2-S1-T1 -- those aliases are removed.
# cli.py (JUDGE_LOG_FILE) and config_loader.py (JUDGE_CONFIG_PATH) are not yet
# migrated; their aliases remain until the owning tasks complete.
os.environ["JUDGE_LOG_FILE"] = os.environ["DEVBENCH_LOG_FILE"]
os.environ["JUDGE_CONFIG_PATH"] = os.environ["DEVBENCH_CONFIG_PATH"]

import pytest
from fixtures.data import WORK_UNIT_MARKDOWN_TEMPLATE as _WORK_UNIT_TEMPLATE

from devbench.backlog.work_unit import WorkUnit, WorkUnitStatus, WorkUnitType

_WORKSPACE_ROOT = os.environ.get("DEVBENCH_WORKSPACE_ROOT", "/tmp/test-workspace")


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
        cwd=repo_dir,
        capture_output=True,
        check=True,
    )
    # Set up origin so _get_default_branch() works: point origin at self,
    # fetch to create origin/main, then write origin/HEAD symref.
    subprocess.run(
        ["git", "remote", "add", "origin", repo_dir.as_posix()],
        cwd=repo_dir,
        capture_output=True,
        check=True,
    )
    subprocess.run(
        ["git", "fetch", "origin"],
        cwd=repo_dir,
        capture_output=True,
        check=True,
    )
    head_ref = repo_dir / ".git" / "refs" / "remotes" / "origin" / "HEAD"
    head_ref.write_text("ref: refs/remotes/origin/main\n")
    return repo_dir


@pytest.fixture
def mock_backlog_index(tmp_path: Path) -> Path:
    """Create a temporary BACKLOG.md with sample table rows and matching work-unit files."""
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

    # Create the work-unit files referenced by the index so parse_index can
    # delegate to parse_work_unit_file for each row.
    backlog_dir = tmp_path / "backlog"
    backlog_dir.mkdir(exist_ok=True)
    _work_units = [
        ("E0-F1-S1-T1", "Create Makefile", "in-queue", "Task"),
        ("E0-F1-S1-T2", "Lint Targets", "in-queue", "Task"),
        ("E0-F1-S1-T3", "Test Targets", "done", "Task"),
        ("E0-F1-S1", "Story One", "in-queue", "Story"),
        ("E0-F1", "Feature One", "in-queue", "Feature"),
    ]
    for unit_id, title, status, _ in _work_units:
        (backlog_dir / f"{unit_id}.md").write_text(f"# {unit_id}: {title}\n\n## Status: {status}\n")

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
def backlog_dir(tmp_path: Path) -> Path:
    """Create and return the backlog subdirectory under tmp_path."""
    from devbench.constants import BACKLOG_SUBDIR

    d = tmp_path / BACKLOG_SUBDIR
    d.mkdir()
    return d


@pytest.fixture
def fresh_config(monkeypatch: pytest.MonkeyPatch) -> Iterator[ModuleType]:
    """Yield a freshly-imported ``devbench.config`` module under monkeypatched env.

    TD-10: collapses the ad-hoc ``importlib.reload(config)`` pattern in
    ``tests/test_config.py``. Each test that wants a fresh config calls
    this fixture, applies whatever env-var overrides via the supplied
    ``monkeypatch``, and gets a re-imported config module that picks
    up the new env values. On teardown the module is reloaded once
    more under the test runner's normal env so subsequent tests see
    the baseline state.
    """
    import importlib

    from devbench import config as _config

    importlib.reload(_config)
    try:
        yield _config
    finally:
        importlib.reload(_config)


# ---------------------------------------------------------------------------
# TD-8: every collected test gets a marker.
#
# The pyproject's ``[tool.pytest.ini_options].markers`` registry defines
# ``unit`` and ``functional``. Files under ``tests/test_integration/``
# default to ``functional``; everything else defaults to ``unit``. Tests
# that already declare a marker explicitly (via ``@pytest.mark.<name>``
# at function/class level OR ``pytestmark`` at module level) keep that
# marker -- the hook is purely additive.
# ---------------------------------------------------------------------------


def pytest_configure(config: pytest.Config) -> None:
    """Ensure the test workspace contains a minimal BACKLOG.md at session start.

    CLI functions that call BacklogParser(backlog_index=BACKLOG_INDEX).parse_index()
    -- where BACKLOG_INDEX resolves relative to DEVBENCH_WORKSPACE_ROOT -- raise
    FileNotFoundError when the file is absent and the test does not patch
    BACKLOG_INDEX itself.  Creating a stub BACKLOG.md here prevents those
    failures without altering any test fixture or production code.

    The stub uses a non-conflicting ID (E0-F0-S0-T0) so it cannot be resolved
    by _resolve_unit_file() for tests that use IDs like E0-F1-S1-T1.
    """
    workspace = Path(os.environ.get("DEVBENCH_WORKSPACE_ROOT", "/tmp/test-workspace"))
    workspace.mkdir(parents=True, exist_ok=True)
    backlog_index = workspace / "BACKLOG.md"
    if backlog_index.exists():
        return
    stub_dir = workspace / "backlog" / "E0-stub"
    stub_dir.mkdir(parents=True, exist_ok=True)
    (stub_dir / "E0-F0-S0-T0.md").write_text(
        "# E0-F0-S0-T0: Stub\n\n## Status: done\n\n## Changes Manifest\n\n| File | Change |\n|------|--------|\n"
    )
    backlog_index.write_text(
        "# Backlog\n\n"
        "## Full Work Unit Index\n\n"
        "### E0: Test\n\n"
        "| ID | Title | Type | Status | Dependencies | Repo | File Path |\n"
        "|-----|-------|------|--------|-------------|------|----------|\n"
        "| E0-F0-S0-T0 | Stub | Task | done | None | test-repo |"
        " `backlog/E0-stub/E0-F0-S0-T0.md` |\n"
    )


def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    """Apply default ``unit`` / ``functional`` markers based on path."""
    for item in items:
        existing = {mark.name for mark in item.iter_markers()}
        if "unit" in existing or "functional" in existing:
            continue
        location = str(getattr(item, "fspath", "")) or str(item.nodeid)
        if "/test_integration/" in location:
            item.add_marker(pytest.mark.functional)
        else:
            item.add_marker(pytest.mark.unit)
