"""Unit tests for the guard-work-unit-write PreToolUse hook script."""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

import pytest

SCRIPT_PATH = (
    Path(__file__).parent.parent.parent
    / "plugin"
    / "devbench"
    / "scripts"
    / "guard-work-unit-write.sh"
)


def _run_hook(payload: dict) -> subprocess.CompletedProcess:
    """Invoke the hook script with the given JSON payload on stdin."""
    return subprocess.run(
        ["bash", str(SCRIPT_PATH)],
        input=json.dumps(payload),
        capture_output=True,
        text=True,
    )


def _make_write_payload(file_path: str) -> dict:
    """Build a minimal PreToolUse Write hook payload."""
    return {
        "tool_name": "Write",
        "tool_input": {"file_path": file_path},
    }


def _make_edit_payload(file_path: str) -> dict:
    """Build a minimal PreToolUse Edit hook payload."""
    return {
        "tool_name": "Edit",
        "tool_input": {"file_path": file_path},
    }


@pytest.mark.unit
class TestGuardWorkUnitWriteHook:
    """Tests for guard-work-unit-write.sh PreToolUse hook."""

    # ------------------------------------------------------------------ AC-1

    def test_script_exists_and_is_executable(self) -> None:
        """AC-1: The script must exist and be executable."""
        assert SCRIPT_PATH.exists(), f"Script not found at {SCRIPT_PATH}"
        assert os.access(SCRIPT_PATH, os.X_OK), f"Script is not executable: {SCRIPT_PATH}"

    # ------------------------------------------------------------------ AC-2: blocks work unit .md writes

    @pytest.mark.parametrize(
        "file_path",
        [
            "/workspace/backlog/E201-deterministic-hooks/E201-F1-S4-guard-work-unit-write/E201-F1-S4-T1.md",
            "/workspace/backlog/E100-feature/E100-F1-S1/E100-F1-S1-T1.md",
            "/home/user/project/backlog/some-epic/some-feature/some-story/T1.md",
            "backlog/E201-F1-S4-T1.md",
            "backlog/some/nested/path/work-unit.md",
        ],
    )
    def test_write_to_work_unit_md_is_blocked(self, file_path: str) -> None:
        """AC-2: Writing to a work unit .md file in backlog/ is blocked with exit 2."""
        payload = _make_write_payload(file_path)
        result = _run_hook(payload)
        assert result.returncode == 2, (
            f"Expected exit 2 for Write to '{file_path}', got {result.returncode}. "
            f"stderr: {result.stderr}"
        )
        assert "guard-work-unit-write" in result.stderr

    @pytest.mark.parametrize(
        "file_path",
        [
            "/workspace/backlog/E201-deterministic-hooks/E201-F1-S4-guard-work-unit-write/E201-F1-S4-T1.md",
            "backlog/E100-F1-S1-T1.md",
            "backlog/epic/feature/story/task.md",
        ],
    )
    def test_edit_to_work_unit_md_is_blocked(self, file_path: str) -> None:
        """AC-2: Editing a work unit .md file in backlog/ is blocked with exit 2."""
        payload = _make_edit_payload(file_path)
        result = _run_hook(payload)
        assert result.returncode == 2, (
            f"Expected exit 2 for Edit to '{file_path}', got {result.returncode}. "
            f"stderr: {result.stderr}"
        )
        assert "guard-work-unit-write" in result.stderr

    def test_blocked_message_is_actionable(self) -> None:
        """AC-2: Error message must be clear and actionable."""
        payload = _make_write_payload("backlog/E201-F1-S4-T1.md")
        result = _run_hook(payload)
        assert result.returncode == 2
        stderr_lower = result.stderr.lower()
        assert any(
            word in stderr_lower
            for word in ["backlog", "work unit", "orchestrate", "blocked"]
        ), f"Error message not actionable: {result.stderr}"

    # ------------------------------------------------------------------ AC-3: non-backlog paths are allowed

    @pytest.mark.parametrize(
        "file_path",
        [
            "/workspace/src/main.py",
            "src/devbench/config.py",
            "tests/unit/test_something.py",
            "README.md",
            "BACKLOG.md",
            "/workspace/BACKLOG.md",
            "docs/architecture.md",
            "/home/user/plugin/devbench/scripts/guard-bash.sh",
            "plugin/devbench/hooks/hooks.json",
        ],
    )
    def test_non_backlog_file_paths_are_allowed(self, file_path: str) -> None:
        """AC-3: Non-backlog file paths exit 0 (allowed)."""
        payload = _make_write_payload(file_path)
        result = _run_hook(payload)
        assert result.returncode == 0, (
            f"Expected exit 0 for Write to '{file_path}', got {result.returncode}. "
            f"stderr: {result.stderr}"
        )

    def test_backlog_non_md_file_is_allowed(self) -> None:
        """AC-3: Non-.md files under backlog/ are not blocked (only .md work units are)."""
        payload = _make_write_payload("backlog/some-output.json")
        result = _run_hook(payload)
        assert result.returncode == 0, (
            f"Expected exit 0 for non-.md file under backlog/, got {result.returncode}. "
            f"stderr: {result.stderr}"
        )

    def test_empty_file_path_is_allowed(self) -> None:
        """AC-3: Missing file_path does not crash the hook."""
        payload = {"tool_name": "Write", "tool_input": {}}
        result = _run_hook(payload)
        assert result.returncode == 0

    def test_write_to_config_yaml_in_backlog_is_allowed(self) -> None:
        """AC-3: YAML config files in backlog/ are not work unit .md files and are allowed."""
        payload = _make_write_payload("backlog/config/devbench.yaml")
        result = _run_hook(payload)
        assert result.returncode == 0

    def test_write_to_agent_instructions_in_backlog_config_is_allowed(self) -> None:
        """AC-3: AGENT-INSTRUCTIONS.md in backlog/config/ is not a work unit and is allowed."""
        payload = _make_write_payload("backlog/config/AGENT-INSTRUCTIONS.md")
        result = _run_hook(payload)
        assert result.returncode == 0
