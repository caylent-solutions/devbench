"""Unit tests for the assert-tests-pass PostToolUse hook script."""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

import pytest

SCRIPT_PATH = Path(__file__).parent.parent.parent / "plugin" / "devbench" / "scripts" / "assert-tests-pass.sh"


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
class TestAssertTestsPassHook:
    """Tests for assert-tests-pass.sh PostToolUse hook."""

    def test_script_exists_and_is_executable(self) -> None:
        """AC-1: The script must exist and be executable."""
        assert SCRIPT_PATH.exists(), f"Script not found at {SCRIPT_PATH}"
        assert os.access(SCRIPT_PATH, os.X_OK), f"Script is not executable: {SCRIPT_PATH}"

    def test_pytest_fail_exits_2(self) -> None:
        """AC-2: Exit code 2 with clear message when pytest exits non-zero."""
        payload = {
            "tool_name": "Bash",
            "tool_input": {"command": "pytest tests/"},
            "tool_result": {"exit_code": 1, "output": "1 failed, 0 passed"},
        }
        result = _run_hook(payload)
        assert result.returncode == 2
        assert "assert-tests-pass" in result.stderr
        assert "pytest" in result.stderr or "test" in result.stderr.lower()

    def test_make_test_fail_exits_2(self) -> None:
        """AC-2: Exit code 2 with clear message when make test exits non-zero."""
        payload = {
            "tool_name": "Bash",
            "tool_input": {"command": "make test"},
            "tool_result": {"exit_code": 2, "output": "Error: tests failed"},
        }
        result = _run_hook(payload)
        assert result.returncode == 2
        assert "assert-tests-pass" in result.stderr

    def test_pytest_pass_exits_0(self) -> None:
        """AC-3: Exit 0 when pytest exits zero (tests passed)."""
        payload = {
            "tool_name": "Bash",
            "tool_input": {"command": "pytest tests/"},
            "tool_result": {"exit_code": 0, "output": "5 passed"},
        }
        result = _run_hook(payload)
        assert result.returncode == 0

    def test_make_test_pass_exits_0(self) -> None:
        """AC-3: Exit 0 when make test exits zero."""
        payload = {
            "tool_name": "Bash",
            "tool_input": {"command": "make test"},
            "tool_result": {"exit_code": 0, "output": "All tests passed"},
        }
        result = _run_hook(payload)
        assert result.returncode == 0

    def test_non_test_command_exits_0(self) -> None:
        """AC-3: Non-test bash commands are always allowed regardless of exit code."""
        payload = {
            "tool_name": "Bash",
            "tool_input": {"command": "ls -la"},
            "tool_result": {"exit_code": 1, "output": "some error"},
        }
        result = _run_hook(payload)
        assert result.returncode == 0

    def test_git_command_exits_0(self) -> None:
        """AC-3: git commands are not test commands and always allowed."""
        payload = {
            "tool_name": "Bash",
            "tool_input": {"command": "git status"},
            "tool_result": {"exit_code": 128, "output": "fatal: not a git repository"},
        }
        result = _run_hook(payload)
        assert result.returncode == 0

    def test_make_validate_fail_exits_2(self) -> None:
        """AC-2: make test-unit and make validate are also treated as test commands."""
        payload = {
            "tool_name": "Bash",
            "tool_input": {"command": "make test-unit"},
            "tool_result": {"exit_code": 1, "output": "tests failed"},
        }
        result = _run_hook(payload)
        assert result.returncode == 2

    def test_empty_command_exits_0(self) -> None:
        """AC-3: Empty/missing command does not crash the hook."""
        payload = {
            "tool_name": "Bash",
            "tool_input": {},
            "tool_result": {"exit_code": 1, "output": ""},
        }
        result = _run_hook(payload)
        assert result.returncode == 0

    def test_pytest_command_with_args_fail_exits_2(self) -> None:
        """AC-2: pytest with flags is still matched as a test command."""
        payload = {
            "tool_name": "Bash",
            "tool_input": {"command": "uv run pytest tests/ -v --tb=short"},
            "tool_result": {"exit_code": 1, "output": "FAILED tests/test_foo.py::test_bar"},
        }
        result = _run_hook(payload)
        assert result.returncode == 2
        assert "assert-tests-pass" in result.stderr

    def test_error_message_includes_command(self) -> None:
        """AC-2: Error message includes the failing command for actionability."""
        cmd = "pytest tests/unit/"
        payload = {
            "tool_name": "Bash",
            "tool_input": {"command": cmd},
            "tool_result": {"exit_code": 1, "output": "FAILED"},
        }
        result = _run_hook(payload)
        assert result.returncode == 2
        assert cmd in result.stderr
