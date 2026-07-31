"""Shared pytest fixtures for the devbench test suite."""

from __future__ import annotations

import os
import tempfile
from collections.abc import Iterator
from pathlib import Path
from types import ModuleType

# ---------------------------------------------------------------------------
# Workspace isolation (issue #292).
#
# These were previously ``os.environ.setdefault``, which meant the suite
# INHERITED whatever the ambient shell already had. devbench is developed with
# devbench, so the executor runs the suite from inside a live workspace with
# DEVBENCH_WORKSPACE_ROOT and DEVBENCH_LOG_FILE exported. Tests therefore
# resolved live paths and wrote to them: fixture work-unit state landed in the
# real ``.devbench/ci-failures/`` and ``.devbench/pr-bot-feedback/``, and
# fabricated lifecycle records -- [ORCHESTRATOR_TERMINAL_EXIT], [QUOTA_WAITING],
# [ORCHESTRATOR_AUTO_RESTART], "Merged PR #42" -- were appended to the live
# orchestrator log for events that never happened. Those are the exact markers
# the reporting layer parses, so a test run could drive an operator's status
# and report output.
#
# Assignment is now unconditional. An ambient value is not a configuration the
# suite may honour; it is the hazard. The root is a fresh per-run temporary
# directory rather than a fixed path so concurrent runs cannot collide and no
# run can be steered onto a real workspace.
# ---------------------------------------------------------------------------
os.environ["DEVBENCH_CLAUDE_MODEL"] = "test-model"
os.environ["DEVBENCH_WORKSPACE_ROOT"] = tempfile.mkdtemp(prefix="devbench-test-workspace-")
os.environ["DEVBENCH_PROJECT_ROOT"] = os.environ["DEVBENCH_WORKSPACE_ROOT"]
os.environ["DEVBENCH_LOG_FILE"] = str(Path(os.environ["DEVBENCH_WORKSPACE_ROOT"]) / "orchestrator.log")
# Point to the test fixture YAML config so config.py can resolve ALLOWED_REPOS
# from the YAML repos section (the only supported source). Also forced: an
# inherited config path would reintroduce the operator's real repo list.
os.environ["DEVBENCH_CONFIG_PATH"] = str(Path(__file__).parent / "fixtures" / "test_devbench.yaml")
# A session name makes log_setup resolve a per-session directory under the
# workspace root; leaving an inherited one in place would send session logs to
# the live workspace's session tree.
os.environ.pop("DEVBENCH_SESSION_NAME", None)

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
