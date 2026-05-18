"""End-to-end test: ``uv run devbench watch`` against prebuilt fixtures.

Builds a realistic tmp workspace out of ``tests/fixtures/activity/*`` --
a BACKLOG.md, one work-unit .md, an orchestrator.log, a hook-logs.jsonl,
and a subagent transcript -- then runs ``devbench watch`` as a subprocess
with a freshly-set ``DEVBENCH_WORKSPACE_ROOT`` pointing at the tmp directory.
The captured stdout must contain the mode label, active task ID, and the
latest subagent text; the process must exit 0 with no stderr surprises.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

_FIXTURES = Path(__file__).resolve().parent.parent / "fixtures" / "activity"


def _write_config(workspace: Path) -> Path:
    """Copy the test_devbench.yaml fixture into <workspace>/backlog/config/devbench.yaml."""
    src = Path(__file__).resolve().parent.parent / "fixtures" / "test_devbench.yaml"
    config_dir = workspace / "backlog" / "config"
    config_dir.mkdir(parents=True, exist_ok=True)
    target = config_dir / "devbench.yaml"
    shutil.copy(src, target)
    return target


def _materialise_workspace(tmp_path: Path) -> Path:
    """Copy fixture files into ``tmp_path`` in the shape the CLI expects."""
    shutil.copy(_FIXTURES / "BACKLOG.md", tmp_path / "BACKLOG.md")
    shutil.copy(_FIXTURES / "orchestrator.log", tmp_path / "orchestrator.log")

    backlog_dir = tmp_path / "backlog"
    backlog_dir.mkdir()
    shutil.copy(_FIXTURES / "EX-F1-S1-T1.md", backlog_dir / "EX-F1-S1-T1.md")

    session_dir = tmp_path / "session"
    session_dir.mkdir()
    # Create the main-session stub so the session_dir parent resolves cleanly.
    (session_dir / "main-session.jsonl").write_text("")
    subagents_dir = session_dir / "subagents"
    subagents_dir.mkdir()
    shutil.copy(_FIXTURES / "subagent-transcript.jsonl", subagents_dir / "agent-abc.jsonl")

    hook_src = (_FIXTURES / "hook-logs.jsonl").read_text(encoding="utf-8")
    hook_materialised = hook_src.replace("SESSION_DIR_PLACEHOLDER", str(session_dir))
    (tmp_path / "hook-logs.jsonl").write_text(hook_materialised, encoding="utf-8")

    _write_config(tmp_path)
    return tmp_path


def _run_watch(workspace: Path) -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    env["DEVBENCH_WORKSPACE_ROOT"] = str(workspace)
    env["DEVBENCH_LOG_FILE"] = str(workspace / "orchestrator.log")
    env["DEVBENCH_CLAUDE_MODEL"] = env.get("DEVBENCH_CLAUDE_MODEL", "test-model")
    # Use the same Python interpreter pytest is running under; invoke the
    # module directly so we don't depend on `uv run` being in PATH.
    return subprocess.run(
        [sys.executable, "-m", "devbench.cli", "watch"],
        capture_output=True,
        text=True,
        env=env,
        check=False,
        timeout=60,
    )


@pytest.fixture
def populated_workspace(tmp_path: Path) -> Path:
    """Return a workspace root already populated with dashboard fixtures."""
    return _materialise_workspace(tmp_path)


class TestWatchAgainstFixtures:
    def test_dashboard_renders_against_fixture(self, populated_workspace: Path) -> None:
        result = _run_watch(populated_workspace)
        assert result.returncode == 0, f"stderr:\n{result.stderr}"

        out = result.stdout
        # Mode label is rendered.
        assert "Mode: standard multi-PR" in out
        # Active task surfaces the backlog row.
        assert "EX-F1-S1-T1" in out
        # Latest agent text from the fixture transcript appears.
        assert "Looking at the test failures" in out
        # Footer is always emitted.
        assert "Ctrl+C to stop" in out

    def test_exit_code_zero_and_no_tracebacks(self, populated_workspace: Path) -> None:
        result = _run_watch(populated_workspace)
        assert result.returncode == 0
        assert "Traceback" not in result.stderr
        assert "Traceback" not in result.stdout
