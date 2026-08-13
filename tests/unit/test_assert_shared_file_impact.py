"""Unit tests for the assert-shared-file-impact PostToolUse hook script.

Issue caylent-solutions/devbench-internal-backlog#13 (shared-file full-suite
regression gate): this hook enforces
`devbench check-shared-file-impact`'s exit code the same way
`assert-tests-pass.sh` already enforces `run-tests` / `pytest` / `make
test` exit codes -- a non-zero exit blocks silent progression rather than
being an advisory instruction an agent can skip under time pressure.
"""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

import pytest

SCRIPT_PATH = (
    Path(__file__).parent.parent.parent / "plugin" / "devbench-orchestrate" / "scripts" / "assert-shared-file-impact.sh"
)


def _clean_env() -> dict[str, str]:
    """Return the process env with legacy DEVBENCH_WORKSPACE_ROOT and DEVBENCH_LOG_FILE stripped.

    _hook_lib.sh rejects legacy JUDGE_* hook vars (AC-197-9). Tests that source
    _hook_lib.sh must not inherit those vars from the pytest process environment.
    """
    return {k: v for k, v in os.environ.items() if k not in ("DEVBENCH_WORKSPACE_ROOT", "DEVBENCH_LOG_FILE")}


def _run_hook(payload: dict) -> subprocess.CompletedProcess:
    """Invoke the hook script with the given JSON payload on stdin."""
    return subprocess.run(
        ["bash", str(SCRIPT_PATH)],
        input=json.dumps(payload),
        capture_output=True,
        text=True,
        env=_clean_env(),
    )


@pytest.mark.unit
class TestAssertSharedFileImpactHook:
    """Tests for assert-shared-file-impact.sh PostToolUse hook."""

    def test_script_exists_and_is_executable(self) -> None:
        assert SCRIPT_PATH.exists(), f"Script not found at {SCRIPT_PATH}"
        assert os.access(SCRIPT_PATH, os.X_OK), f"Script is not executable: {SCRIPT_PATH}"

    def test_new_regression_exits_2(self) -> None:
        """A blocking check-shared-file-impact exit (new failures) is enforced with exit 2."""
        payload = {
            "tool_name": "Bash",
            "tool_input": {"command": "uv run devbench check-shared-file-impact E0-F1-S1-T1"},
            "tool_result": {"exit_code": 1, "output": '{"verdict": "block", "new_failures": ["t1"]}'},
        }
        result = _run_hook(payload)
        assert result.returncode == 2
        assert "assert-shared-file-impact" in result.stderr
        assert "check-shared-file-impact" in result.stderr

    def test_no_shared_file_match_exits_0(self) -> None:
        """A clean check-shared-file-impact exit (no match, bootstrap, or pass) is allowed."""
        payload = {
            "tool_name": "Bash",
            "tool_input": {"command": "uv run devbench check-shared-file-impact E0-F1-S1-T1"},
            "tool_result": {"exit_code": 0, "output": '{"shared_file_impact": false}'},
        }
        result = _run_hook(payload)
        assert result.returncode == 0

    def test_bare_command_variant_matched(self) -> None:
        """The matcher is a substring match so bare invocations (no `uv run` prefix) are also caught."""
        payload = {
            "tool_name": "Bash",
            "tool_input": {"command": "devbench check-shared-file-impact E0-F1-S1-T1"},
            "tool_result": {"exit_code": 1, "output": ""},
        }
        result = _run_hook(payload)
        assert result.returncode == 2

    def test_unrelated_command_exits_0_regardless_of_exit_code(self) -> None:
        """AC parity with assert-tests-pass.sh: non-matching commands are always allowed."""
        payload = {
            "tool_name": "Bash",
            "tool_input": {"command": "uv run devbench run-tests E0-F1-S1-T1"},
            "tool_result": {"exit_code": 1, "output": "1 failed"},
        }
        result = _run_hook(payload)
        assert result.returncode == 0

    def test_git_command_exits_0(self) -> None:
        payload = {
            "tool_name": "Bash",
            "tool_input": {"command": "git status"},
            "tool_result": {"exit_code": 128, "output": "fatal: not a git repository"},
        }
        result = _run_hook(payload)
        assert result.returncode == 0

    def test_empty_command_exits_0(self) -> None:
        payload = {
            "tool_name": "Bash",
            "tool_input": {},
            "tool_result": {"exit_code": 1, "output": ""},
        }
        result = _run_hook(payload)
        assert result.returncode == 0

    def test_error_message_includes_command(self) -> None:
        cmd = "uv run devbench check-shared-file-impact E0-F1-S1-T1"
        payload = {
            "tool_name": "Bash",
            "tool_input": {"command": cmd},
            "tool_result": {"exit_code": 1, "output": ""},
        }
        result = _run_hook(payload)
        assert result.returncode == 2
        assert cmd in result.stderr

    def test_error_message_points_to_new_failures_field(self) -> None:
        """Fix guidance names the JSON field an agent needs to read to act on the block."""
        payload = {
            "tool_name": "Bash",
            "tool_input": {"command": "uv run devbench check-shared-file-impact E0-F1-S1-T1"},
            "tool_result": {"exit_code": 1, "output": ""},
        }
        result = _run_hook(payload)
        assert result.returncode == 2
        assert "new_failures" in result.stderr
