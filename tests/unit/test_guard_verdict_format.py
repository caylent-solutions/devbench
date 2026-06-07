"""Unit tests for the guard-verdict-format PreToolUse hook script."""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

import pytest

SCRIPT_PATH = (
    Path(__file__).parent.parent.parent / "plugin" / "devbench-orchestrate" / "scripts" / "guard-verdict-format.sh"
)

# H3: the two agent types permitted to write canonical reviewer verdicts.
_REVIEWER_AGENT_TYPE = "devbench-orchestrate:review-supervisor"
# A token value injected by the orchestrator each review round (H3 second factor).
_ROUND_TOKEN = "test-round-token"


def _clean_env() -> dict[str, str]:
    """Return the process env with legacy DEVBENCH_WORKSPACE_ROOT and DEVBENCH_LOG_FILE stripped.

    _hook_lib.sh rejects legacy JUDGE_* hook vars (AC-197-9). Tests that source
    _hook_lib.sh must not inherit those vars from the pytest process environment.
    Also strips DEVBENCH_REVIEW_ROUND_TOKEN so tests that omit it start clean.
    """
    return {
        k: v
        for k, v in os.environ.items()
        if k not in ("DEVBENCH_WORKSPACE_ROOT", "DEVBENCH_LOG_FILE", "DEVBENCH_REVIEW_ROUND_TOKEN")
    }


def _run_hook(payload: dict, round_token: str | None = None) -> subprocess.CompletedProcess:
    """Invoke the hook script with the given JSON payload on stdin.

    Args:
        payload: The JSON payload to pass on stdin.
        round_token: When set, inject DEVBENCH_REVIEW_ROUND_TOKEN into the env.
    """
    env = _clean_env()
    if round_token is not None:
        env["DEVBENCH_REVIEW_ROUND_TOKEN"] = round_token
    return subprocess.run(
        ["bash", str(SCRIPT_PATH)],
        input=json.dumps(payload),
        capture_output=True,
        text=True,
        env=env,
    )


def _make_payload(command: str, agent_type: str | None = None) -> dict:
    """Build a minimal PreToolUse Bash hook payload.

    Args:
        command: The bash command string.
        agent_type: When provided, adds the agent_type field to the payload.
    """
    payload: dict = {
        "tool_name": "Bash",
        "tool_input": {"command": command},
    }
    if agent_type is not None:
        payload["agent_type"] = agent_type
    return payload


@pytest.mark.unit
class TestGuardVerdictFormatHook:
    """Tests for guard-verdict-format.sh PreToolUse hook."""

    # ------------------------------------------------------------------ AC-1

    def test_script_exists_and_is_executable(self) -> None:
        """AC-1: The script must exist and be executable."""
        assert SCRIPT_PATH.exists(), f"Script not found at {SCRIPT_PATH}"
        assert os.access(SCRIPT_PATH, os.X_OK), f"Script is not executable: {SCRIPT_PATH}"

    # ------------------------------------------------------------------ AC-2: verdict validation

    def test_invalid_verdict_value_exits_2(self) -> None:
        """AC-2: Exit 2 when verdict value is neither 'pass' nor 'fail'."""
        payload = _make_payload("uv run devbench log-verdict executor E201-F1-S2-T1 maybe")
        result = _run_hook(payload)
        assert result.returncode == 2
        assert "guard-verdict-format" in result.stderr
        assert "verdict" in result.stderr.lower()

    def test_unknown_judge_name_exits_2(self) -> None:
        """AC-2: Exit 2 when judge name is not a known identifier."""
        payload = _make_payload("uv run devbench log-verdict unknown_judge E201-F1-S2-T1 pass")
        result = _run_hook(payload)
        assert result.returncode == 2
        assert "guard-verdict-format" in result.stderr
        assert "judge" in result.stderr.lower()

    def test_fail_verdict_with_empty_feedback_exits_2(self) -> None:
        """AC-2: Exit 2 when verdict is 'fail' but feedback is missing."""
        payload = _make_payload("uv run devbench log-verdict executor E201-F1-S2-T1 fail")
        result = _run_hook(payload)
        assert result.returncode == 2
        assert "guard-verdict-format" in result.stderr
        assert "feedback" in result.stderr.lower()

    def test_fail_verdict_with_feedback_exits_0(self) -> None:
        """AC-2: Exit 0 when verdict is 'fail' and feedback is provided."""
        payload = _make_payload('uv run devbench log-verdict executor E201-F1-S2-T1 fail "some reason"')
        result = _run_hook(payload)
        assert result.returncode == 0, f"stderr: {result.stderr}"

    def test_pass_verdict_without_feedback_exits_0(self) -> None:
        """AC-2: Exit 0 when verdict is 'pass' (feedback not required)."""
        payload = _make_payload("uv run devbench log-verdict executor E201-F1-S2-T1 pass")
        result = _run_hook(payload)
        assert result.returncode == 0, f"stderr: {result.stderr}"

    def test_pass_verdict_with_feedback_exits_0(self) -> None:
        """AC-2: Exit 0 when verdict is 'pass' with optional feedback."""
        payload = _make_payload('uv run devbench log-verdict executor E201-F1-S2-T1 pass "looks good"')
        result = _run_hook(payload)
        assert result.returncode == 0, f"stderr: {result.stderr}"

    # ------------------------------------------------------------------ AC-2: all known judge names accepted

    @pytest.mark.parametrize(
        "judge_name",
        [
            "code_review",
            "test_review",
            "doc_review",
            "changes_manifest",
            "security_review",
        ],
    )
    def test_canonical_judge_names_accepted_for_reviewer_with_token(self, judge_name: str) -> None:
        """AC-2/H3: Canonical reviewer judge identifiers are accepted for reviewer agent with round token."""
        payload = _make_payload(
            f"uv run devbench log-verdict {judge_name} E201-F1-S2-T1 pass",
            agent_type=_REVIEWER_AGENT_TYPE,
        )
        result = _run_hook(payload, round_token=_ROUND_TOKEN)
        assert result.returncode == 0, f"judge '{judge_name}' was incorrectly rejected: {result.stderr}"

    @pytest.mark.parametrize(
        "judge_name",
        [
            "executor",
            "blocker_resolver",
            "manifest_amender",
            "task_factory",
        ],
    )
    def test_non_canonical_judge_names_accepted_without_agent_type(self, judge_name: str) -> None:
        """AC-2: Non-canonical (audit-only) judge identifiers are accepted without an agent type."""
        payload = _make_payload(f"uv run devbench log-verdict {judge_name} E201-F1-S2-T1 pass")
        result = _run_hook(payload)
        assert result.returncode == 0, f"judge '{judge_name}' was incorrectly rejected: {result.stderr}"

    # ------------------------------------------------------------------ AC-3: non-log-verdict commands allowed

    def test_non_log_verdict_command_exits_0(self) -> None:
        """AC-3: Non-log-verdict Bash commands are always allowed."""
        payload = _make_payload("ls -la")
        result = _run_hook(payload)
        assert result.returncode == 0

    def test_git_command_exits_0(self) -> None:
        """AC-3: git commands are not intercepted."""
        payload = _make_payload("git status")
        result = _run_hook(payload)
        assert result.returncode == 0

    def test_make_validate_exits_0(self) -> None:
        """AC-3: make validate is not a log-verdict command and is allowed."""
        payload = _make_payload("make validate")
        result = _run_hook(payload)
        assert result.returncode == 0

    def test_empty_command_exits_0(self) -> None:
        """AC-3: Empty/missing command does not crash the hook."""
        payload = {"tool_name": "Bash", "tool_input": {}}
        result = _run_hook(payload)
        assert result.returncode == 0

    def test_other_devbench_command_exits_0(self) -> None:
        """AC-3: Other uv run devbench commands (not log-verdict) are allowed."""
        payload = _make_payload("uv run devbench read-unit E201-F1-S2-T1")
        result = _run_hook(payload)
        assert result.returncode == 0

    # ------------------------------------------------------------------ error message quality

    def test_error_message_includes_actionable_fix(self) -> None:
        """AC-2: Error message must be clear and actionable."""
        payload = _make_payload("uv run devbench log-verdict executor E201-F1-S2-T1 maybe")
        result = _run_hook(payload)
        assert result.returncode == 2
        # Should include the offending command or fix guidance
        assert len(result.stderr.strip()) > 0

    def test_fail_verdict_error_mentions_feedback_requirement(self) -> None:
        """AC-2: When fail verdict lacks feedback, error must mention feedback requirement."""
        payload = _make_payload("uv run devbench log-verdict code_review E201-F1-S2-T1 fail")
        result = _run_hook(payload)
        assert result.returncode == 2
        stderr_lower = result.stderr.lower()
        assert "feedback" in stderr_lower or "message" in stderr_lower or "required" in stderr_lower
