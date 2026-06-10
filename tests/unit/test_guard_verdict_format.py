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

# H3: an agent type permitted to write canonical reviewer verdicts.
_REVIEWER_AGENT_TYPE = "devbench-orchestrate:review-supervisor"
# A per-round token written to the round-token FILE each review round (H3 second
# factor; ADR-29 replaced the env var with a file). Fix B: it must be scoped to
# the unit under review (prefix "<unit-id>-"); the canonical-judge test below
# uses unit E201-F1-S2-T1.
_ROUND_TOKEN = "E201-F1-S2-T1-r1-token"


def _clean_env() -> dict[str, str]:
    """Return the process env with DEVBENCH vars that interfere with hooks stripped.

    _hook_lib.sh rejects legacy JUDGE_* hook vars (AC-197-9). Tests that source
    _hook_lib.sh must not inherit those vars from the pytest process environment.

    ``BASH_ENV``/``ENV`` are stripped too: the devcontainer sets
    ``BASH_ENV=/workspaces/telemetry/shell.env``, so a non-interactive
    ``bash <hook>`` re-sources ``shell.env`` on startup. Without stripping it the
    hook subprocess would re-acquire vars this helper controls, making the tests
    non-hermetic. The obsolete ``DEVBENCH_REVIEW_ROUND_TOKEN`` (ADR-29 moved the
    token into a file) is stripped as well -- harmless, kept for hermeticity.
    """
    excluded = {
        "DEVBENCH_WORKSPACE_ROOT",
        "DEVBENCH_LOG_FILE",
        "DEVBENCH_REVIEW_ROUND_TOKEN",
        "BASH_ENV",
        "ENV",
    }
    return {k: v for k, v in os.environ.items() if k not in excluded}


def _run_hook(
    payload: dict,
    workspace_root: Path | None = None,
    round_token: str | None = None,
) -> subprocess.CompletedProcess:
    """Invoke the hook script with the given JSON payload on stdin.

    Args:
        payload: The JSON payload to pass on stdin.
        workspace_root: When set, export DEVBENCH_WORKSPACE_ROOT pointing at it so
            the guard can locate the round-token file. Required for any canonical
            verdict path; harmless for non-canonical paths.
        round_token: When set, write this value to the round-token FILE at
            <workspace_root>/.devbench/review-round-token. Requires workspace_root.
    """
    env = _clean_env()
    if workspace_root is not None:
        env["DEVBENCH_WORKSPACE_ROOT"] = str(workspace_root)
    if round_token is not None:
        if workspace_root is None:
            raise ValueError("round_token requires workspace_root to locate the token file")
        devbench_dir = workspace_root / ".devbench"
        devbench_dir.mkdir(parents=True, exist_ok=True)
        (devbench_dir / "review-round-token").write_text(round_token)
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
    def test_canonical_judge_names_accepted_for_reviewer_with_token(self, judge_name: str, tmp_path: Path) -> None:
        """AC-2/H3: Canonical reviewer judge identifiers are accepted for reviewer agent with round token."""
        payload = _make_payload(
            f"uv run devbench log-verdict {judge_name} E201-F1-S2-T1 pass",
            agent_type=_REVIEWER_AGENT_TYPE,
        )
        result = _run_hook(payload, workspace_root=tmp_path, round_token=_ROUND_TOKEN)
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

    # ------------------------------------------------------------------ H3: round-token file fail-closed

    def test_canonical_verdict_without_token_file_exits_2(self, tmp_path: Path) -> None:
        """H3/ADR-29: a reviewer agent with no round-token FILE is blocked from a canonical verdict."""
        payload = _make_payload(
            "uv run devbench log-verdict code_review E201-F1-S2-T1 pass",
            agent_type=_REVIEWER_AGENT_TYPE,
        )
        # workspace_root set but no token written -> the file is absent.
        result = _run_hook(payload, workspace_root=tmp_path, round_token=None)
        assert result.returncode == 2
        assert "guard-verdict-format" in result.stderr
        assert "token" in result.stderr.lower()

    def test_canonical_verdict_with_foreign_unit_token_exits_2(self, tmp_path: Path) -> None:
        """H3/Fix B: a round-token FILE scoped to a different unit is rejected."""
        payload = _make_payload(
            "uv run devbench log-verdict code_review E201-F1-S2-T1 pass",
            agent_type=_REVIEWER_AGENT_TYPE,
        )
        result = _run_hook(payload, workspace_root=tmp_path, round_token="E999-F9-S9-T9-r1-stale")
        assert result.returncode == 2
        assert "E201-F1-S2-T1" in result.stderr

    def test_canonical_verdict_with_unset_workspace_root_exits_2(self) -> None:
        """H3/ADR-29: without DEVBENCH_WORKSPACE_ROOT the guard cannot locate the token and fails closed."""
        payload = _make_payload(
            "uv run devbench log-verdict code_review E201-F1-S2-T1 pass",
            agent_type=_REVIEWER_AGENT_TYPE,
        )
        # No workspace_root -> DEVBENCH_WORKSPACE_ROOT stays unset.
        result = _run_hook(payload)
        assert result.returncode == 2
        assert "DEVBENCH_WORKSPACE_ROOT" in result.stderr

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
