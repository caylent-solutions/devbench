"""Unit tests for the guard-bash PreToolUse hook script (issue #335).

Until issue #335 this was the only registered guard hook with no test
module. The blocked-pattern list is exercised pattern by pattern, and the
issue's regression class -- conflict-side selection during a merge or
cherry-pick -- is pinned as allowed.
"""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

import pytest

SCRIPT_PATH = Path(__file__).parent.parent.parent / "plugin" / "devbench-orchestrate" / "scripts" / "guard-bash.sh"


def _run_hook(payload: dict) -> subprocess.CompletedProcess:
    """Invoke the hook script with the given JSON payload on stdin."""
    # Hooks source _hook_lib.sh, which reads these vars at source time
    # (AC-197-9); strip them so tests never touch a live workspace.
    env = {k: v for k, v in os.environ.items() if k not in ("DEVBENCH_WORKSPACE_ROOT", "DEVBENCH_LOG_FILE")}
    return subprocess.run(
        ["bash", str(SCRIPT_PATH)],
        input=json.dumps(payload),
        capture_output=True,
        text=True,
        env=env,
    )


def _make_payload(command: str) -> dict:
    """Build a minimal PreToolUse Bash hook payload."""
    return {
        "tool_name": "Bash",
        "tool_input": {"command": command},
    }


@pytest.mark.unit
class TestGuardBashHook:
    """Tests for guard-bash.sh PreToolUse hook."""

    def test_script_exists_and_is_executable(self) -> None:
        """The script must exist and be executable."""
        assert SCRIPT_PATH.exists(), f"Script not found at {SCRIPT_PATH}"
        assert os.access(SCRIPT_PATH, os.X_OK), f"Script is not executable: {SCRIPT_PATH}"

    # ------------------------------------------------------------------ blocked patterns

    @pytest.mark.parametrize(
        "command",
        [
            "rm -rf /tmp/dir",
            "rm -fr build/",
            "git push --force origin main",
            "git push -f origin main",
            "git reset --hard HEAD~1",
            "git checkout -- src/devbench/cli.py",
            "git clean -f",
            "git clean -fd",
            "git clean -fdx",
            "long_running_job > /dev/null 2>&1 &",
        ],
    )
    def test_destructive_command_blocked(self, command: str) -> None:
        """Every declared destructive pattern blocks with exit 2."""
        result = _run_hook(_make_payload(command))
        assert result.returncode == 2, f"Expected exit 2 for '{command}', got {result.returncode}"
        assert "guard-bash" in result.stderr

    def test_blocked_message_names_pattern_and_command(self) -> None:
        """The block message must be actionable: name the pattern and echo the command."""
        result = _run_hook(_make_payload("git reset --hard HEAD"))
        assert result.returncode == 2
        assert "git reset --hard" in result.stderr
        assert "HEAD" in result.stderr

    # ------------------------------------------------------------------ issue #335: conflict-side selection allowed

    @pytest.mark.parametrize(
        "command",
        [
            "git checkout --theirs src/devbench/cli.py",
            "git checkout --ours src/devbench/cli.py",
            "git checkout --theirs -- src/devbench/cli.py",
            "git checkout --ours -- src/devbench/cli.py",
        ],
    )
    def test_conflict_side_selection_allowed(self, command: str) -> None:
        """Issue #335: --theirs/--ours are non-destructive and must pass.

        The pre-fix pattern was the bare substring 'git checkout --', which
        matched these commands and hard-blocked conflict resolution during a
        merge or cherry-pick with no override available.
        """
        result = _run_hook(_make_payload(command))
        assert result.returncode == 0, f"Expected exit 0 for '{command}': {result.stderr}"

    def test_file_restore_form_still_blocked(self) -> None:
        """Issue #335 regression pin: the destructive file-restore form stays blocked."""
        result = _run_hook(_make_payload("git checkout -- ."))
        assert result.returncode == 2
        assert "git checkout -- " in result.stderr

    # ------------------------------------------------------------------ allowed commands

    @pytest.mark.parametrize(
        "command",
        [
            "git status",
            "git fetch origin",
            "git fetch origin pull/315/head:pr-315",
            "git cherry-pick --no-commit abc1234",
            "git cherry-pick --continue",
            "git cherry-pick --abort",
            "git restore --theirs src/devbench/cli.py",
            "git restore --ours src/devbench/cli.py",
            "git add -- src/devbench/cli.py",
            "git merge --no-commit --no-ff origin/main",
            "uv run pytest tests/",
            "ls -la",
        ],
    )
    def test_ordinary_command_allowed(self, command: str) -> None:
        """Non-destructive commands pass with exit 0."""
        result = _run_hook(_make_payload(command))
        assert result.returncode == 0, f"Expected exit 0 for '{command}': {result.stderr}"

    def test_empty_command_allowed(self) -> None:
        """Empty/missing command does not crash the hook."""
        result = _run_hook({"tool_name": "Bash", "tool_input": {}})
        assert result.returncode == 0
