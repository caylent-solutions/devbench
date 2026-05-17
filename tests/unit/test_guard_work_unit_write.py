"""Unit tests for the guard-work-unit-write PreToolUse hook script."""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

import pytest

SCRIPT_PATH = Path(__file__).parent.parent.parent / "plugin" / "devbench" / "scripts" / "guard-work-unit-write.sh"


def _run_hook(
    payload: dict,
    extra_env: dict | None = None,
) -> subprocess.CompletedProcess:
    """Invoke the hook script with the given JSON payload on stdin."""
    # Strip legacy JUDGE_WORKSPACE_ROOT and JUDGE_LOG_FILE: _hook_lib.sh rejects
    # these vars at source time (AC-197-9) and all hooks source _hook_lib.sh.
    env = {k: v for k, v in os.environ.items() if k not in ("JUDGE_WORKSPACE_ROOT", "JUDGE_LOG_FILE")}
    if extra_env:
        env.update(extra_env)
    return subprocess.run(
        ["bash", str(SCRIPT_PATH)],
        input=json.dumps(payload),
        capture_output=True,
        text=True,
        env=env,
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
            f"Expected exit 2 for Write to '{file_path}', got {result.returncode}. stderr: {result.stderr}"
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
            f"Expected exit 2 for Edit to '{file_path}', got {result.returncode}. stderr: {result.stderr}"
        )
        assert "guard-work-unit-write" in result.stderr

    def test_blocked_message_is_actionable(self) -> None:
        """AC-2: Error message must be clear and actionable."""
        payload = _make_write_payload("backlog/E201-F1-S4-T1.md")
        result = _run_hook(payload)
        assert result.returncode == 2
        stderr_lower = result.stderr.lower()
        assert any(word in stderr_lower for word in ["backlog", "work unit", "orchestrate", "blocked"]), (
            f"Error message not actionable: {result.stderr}"
        )

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
            f"Expected exit 0 for Write to '{file_path}', got {result.returncode}. stderr: {result.stderr}"
        )

    def test_backlog_non_md_file_is_allowed(self) -> None:
        """AC-3: Non-.md files under backlog/ are not blocked (only .md work units are)."""
        payload = _make_write_payload("backlog/some-output.json")
        result = _run_hook(payload)
        assert result.returncode == 0, (
            f"Expected exit 0 for non-.md file under backlog/, got {result.returncode}. stderr: {result.stderr}"
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


@pytest.mark.unit
class TestGuardWorkUnitWriteContentValidation:
    """Tests for rule-10 (em-dash) and rule-11 (checkout_directory prefix) content checks."""

    def test_em_dash_in_content_rejected_with_exit_2(self) -> None:
        """Rule 10: content containing U+2014 is rejected with exit 2."""
        content = "## Changes Manifest\n| `src/foo—bar.py` | fix |\n"
        payload = {
            "tool_name": "Write",
            "tool_input": {
                "file_path": "backlog/E1-F1-S1-T1.md",
                "content": content,
            },
        }
        result = _run_hook(payload)
        assert result.returncode == 2, (
            f"Expected exit 2 for em-dash content, got {result.returncode}. stderr: {result.stderr}"
        )
        assert "rule 10" in result.stderr

    def test_checkout_directory_prefix_in_manifest_row_rejected(self, tmp_path: Path) -> None:
        """Rule 11: a Changes Manifest row prefixed with checkout_directory is rejected."""
        yaml_content = "repos:\n  org/kanon:\n    checkout_directory: kanon\n"
        config_dir = tmp_path / "backlog" / "config"
        config_dir.mkdir(parents=True)
        (config_dir / "devbench.yaml").write_text(yaml_content)

        content = "## Changes Manifest\n| `kanon/src/foo.py` | add feature |\n"
        payload = {
            "tool_name": "Write",
            "tool_input": {
                "file_path": "backlog/E1-F1-S1-T2.md",
                "content": content,
            },
        }
        result = _run_hook(payload, extra_env={"DEVBENCH_WORKSPACE_ROOT": str(tmp_path)})
        assert result.returncode == 2, (
            f"Expected exit 2 for checkout_directory prefix content, got {result.returncode}. stderr: {result.stderr}"
        )
        assert "rule 11" in result.stderr

    def test_valid_content_and_manifest_paths_pass(self) -> None:
        """Content with no em-dashes and repo-relative paths exits 0 (path-based block still applies)."""
        content = "## Changes Manifest\n| `src/foo.py` | add feature |\n"
        payload = {
            "tool_name": "Write",
            "tool_input": {
                "file_path": "backlog/E1-F1-S1-T3.md",
                "content": content,
            },
        }
        # This should still exit 2 due to the path-based block (work unit .md),
        # but the content checks must NOT fire before the path block.
        result = _run_hook(payload)
        assert result.returncode == 2
        assert "rule 10" not in result.stderr
        assert "rule 11" not in result.stderr
        assert "guard-work-unit-write" in result.stderr


@pytest.mark.unit
class TestGuardWorkUnitWriteOrchestratorBypass:
    """Issue #160: JUDGE_AGENT_ROLE=orchestrator allows corrective edits
    on backlog/**/*.md while content rules (rule 10 + rule 11) still fire.
    Executor-role and missing-role still BLOCK (preserves the original
    safety guarantee)."""

    def test_orchestrator_role_allows_clean_content(self) -> None:
        """ALLOW: clean content + orchestrator role -> exit 0 (no block)."""
        content = "## Changes Manifest\n| `src/foo.py` | add feature |\n"
        payload = {
            "tool_name": "Write",
            "tool_input": {
                "file_path": "backlog/E1-F1-S1-T1.md",
                "content": content,
            },
        }
        result = _run_hook(payload, extra_env={"JUDGE_AGENT_ROLE": "orchestrator"})
        assert result.returncode == 0, (
            f"Expected exit 0 for orchestrator-role + clean content, got {result.returncode}. stderr: {result.stderr}"
        )

    def test_orchestrator_role_still_enforces_rule_10(self) -> None:
        """Content rule 10 (em-dash) MUST fire even when role=orchestrator.
        The role bypass only affects the final block-or-allow gate; content
        rules run first and BLOCK regardless of role."""
        content = "## Changes Manifest\n| `src/foo—bar.py` | fix |\n"
        payload = {
            "tool_name": "Write",
            "tool_input": {
                "file_path": "backlog/E1-F1-S1-T2.md",
                "content": content,
            },
        }
        result = _run_hook(payload, extra_env={"JUDGE_AGENT_ROLE": "orchestrator"})
        assert result.returncode == 2, (
            f"Expected exit 2 (rule 10 fires) even for orchestrator-role, "
            f"got {result.returncode}. stderr: {result.stderr}"
        )
        assert "rule 10" in result.stderr

    def test_orchestrator_role_still_enforces_rule_11(self, tmp_path: Path) -> None:
        """Content rule 11 (checkout_directory prefix) MUST fire even when role=orchestrator."""
        yaml_content = "repos:\n  org/kanon:\n    checkout_directory: kanon\n"
        config_dir = tmp_path / "backlog" / "config"
        config_dir.mkdir(parents=True)
        (config_dir / "devbench.yaml").write_text(yaml_content)

        content = "## Changes Manifest\n| `kanon/src/foo.py` | add feature |\n"
        payload = {
            "tool_name": "Write",
            "tool_input": {
                "file_path": "backlog/E1-F1-S1-T3.md",
                "content": content,
            },
        }
        result = _run_hook(
            payload,
            extra_env={
                "JUDGE_AGENT_ROLE": "orchestrator",
                "DEVBENCH_WORKSPACE_ROOT": str(tmp_path),
            },
        )
        assert result.returncode == 2, (
            f"Expected exit 2 (rule 11 fires) even for orchestrator-role, "
            f"got {result.returncode}. stderr: {result.stderr}"
        )
        assert "rule 11" in result.stderr

    def test_executor_role_still_blocks(self) -> None:
        """BLOCK: executor role on a backlog/**/*.md write is rejected with exit 2."""
        content = "## Changes Manifest\n| `src/foo.py` | add feature |\n"
        payload = {
            "tool_name": "Write",
            "tool_input": {
                "file_path": "backlog/E1-F1-S1-T4.md",
                "content": content,
            },
        }
        result = _run_hook(payload, extra_env={"JUDGE_AGENT_ROLE": "executor"})
        assert result.returncode == 2
        assert "guard-work-unit-write" in result.stderr

    def test_missing_role_defaults_to_block(self) -> None:
        """BLOCK: missing JUDGE_AGENT_ROLE defaults to executor-tier behaviour
        (preserves the original safety guarantee for legacy callers)."""
        content = "## Changes Manifest\n| `src/foo.py` | add feature |\n"
        payload = {
            "tool_name": "Write",
            "tool_input": {
                "file_path": "backlog/E1-F1-S1-T5.md",
                "content": content,
            },
        }
        # Strip any inherited JUDGE_AGENT_ROLE the dev shell might be carrying.
        # Also strip legacy JUDGE_WORKSPACE_ROOT and JUDGE_LOG_FILE per AC-197-9.
        _stripped = {"JUDGE_WORKSPACE_ROOT", "JUDGE_LOG_FILE", "JUDGE_AGENT_ROLE"}
        env = {k: v for k, v in os.environ.items() if k not in _stripped}
        result = subprocess.run(
            ["bash", str(SCRIPT_PATH)],
            input=json.dumps(payload),
            capture_output=True,
            text=True,
            env=env,
        )
        assert result.returncode == 2
        assert "guard-work-unit-write" in result.stderr

    def test_unknown_role_defaults_to_block(self) -> None:
        """BLOCK: an unrecognised JUDGE_AGENT_ROLE value defaults to BLOCK."""
        content = "## Changes Manifest\n| `src/foo.py` | add feature |\n"
        payload = {
            "tool_name": "Write",
            "tool_input": {
                "file_path": "backlog/E1-F1-S1-T6.md",
                "content": content,
            },
        }
        result = _run_hook(payload, extra_env={"JUDGE_AGENT_ROLE": "rogue-agent-007"})
        assert result.returncode == 2
        assert "guard-work-unit-write" in result.stderr
