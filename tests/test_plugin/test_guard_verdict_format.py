"""Tests for the `guard-verdict-format.sh` PreToolUse hook.

The hook validates `uv run devbench log-verdict ...` calls before they run,
so user mistakes produce actionable error messages instead of cryptic ones.
These tests exercise the hook via subprocess with crafted JSON stdin and
assert the exit code + stderr contents for each failure mode.
"""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

import pytest

HOOK_PATH = (
    Path(__file__).parent.parent.parent / "plugin" / "devbench-orchestrate" / "scripts" / "guard-verdict-format.sh"
).resolve()

_REVIEWER_AGENT_TYPE = "devbench-orchestrate:review-supervisor"
_ROUND_TOKEN = "E0-F8-r1-token"


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


def _run(
    command: str,
    agent_type: str | None = None,
    round_token: str | None = None,
    workspace_root: Path | None = None,
) -> subprocess.CompletedProcess[str]:
    """Invoke the hook with a crafted Bash tool-input command string.

    Args:
        command: The bash command string to embed in the hook payload.
        agent_type: When set, adds agent_type field to the JSON payload.
        round_token: When set, write this value to the round-token FILE at
            <workspace_root>/.devbench/review-round-token. Requires workspace_root.
        workspace_root: When set, export DEVBENCH_WORKSPACE_ROOT pointing at it so
            the guard can locate the round-token file.
    """
    payload: dict[str, object] = {"tool_name": "Bash", "tool_input": {"command": command}}
    if agent_type is not None:
        payload["agent_type"] = agent_type
    env = _clean_env()
    if workspace_root is not None:
        env["DEVBENCH_WORKSPACE_ROOT"] = str(workspace_root)
    if round_token is not None:
        if workspace_root is None:
            raise ValueError("round_token requires workspace_root to locate the token file")
        devbench_dir = workspace_root / ".devbench"
        devbench_dir.mkdir(parents=True, exist_ok=True)
        (devbench_dir / "review-round-token").write_text(round_token)
    stdin = json.dumps(payload)
    return subprocess.run(
        ["bash", str(HOOK_PATH)],
        input=stdin,
        capture_output=True,
        text=True,
        check=False,
        env=env,
    )


@pytest.mark.unit
class TestGuardVerdictFormat:
    """The hook must produce actionable errors for every common misuse."""

    def test_help_flag_passes_through(self) -> None:
        """`log-verdict --help` must not block; the CLI is allowed to print usage."""
        result = _run("uv run devbench log-verdict --help")
        assert result.returncode == 0
        assert result.stderr == ""

    def test_help_short_flag_passes_through(self) -> None:
        result = _run("uv run devbench log-verdict -h")
        assert result.returncode == 0

    def test_missing_args_reports_expected_order(self) -> None:
        """Fewer than 3 positional args must trigger the missing-arg error (not 'invalid verdict')."""
        result = _run("uv run devbench log-verdict code_review pass")
        assert result.returncode == 2
        assert "missing required argument" in result.stderr
        assert "Expected positional order: log-verdict <judge> <unit-id> <verdict> [feedback]" in result.stderr
        assert "invalid verdict" not in result.stderr

    def test_shell_redirection_does_not_count_as_positional(self) -> None:
        """`log-verdict 2>&1 | tail` must not treat '2>&1' as the judge name."""
        result = _run("uv run devbench log-verdict 2>&1 | tail -20")
        assert result.returncode == 2
        assert "missing required argument" in result.stderr
        assert "'2>&1'" not in result.stderr

    def test_unknown_judge_shows_expected_order(self) -> None:
        """Wrong positional order (unit-id first) must surface the expected layout."""
        result = _run('uv run devbench log-verdict E0-F8-S1-T6 code_review fail "msg"')
        assert result.returncode == 2
        assert "unknown judge name 'E0-F8-S1-T6'" in result.stderr
        assert "Expected positional order: log-verdict <judge> <unit-id> <verdict> [feedback]" in result.stderr

    def test_invalid_verdict_shows_expected_order(self) -> None:
        result = _run('uv run devbench log-verdict code_review E0-F8 maybe "msg"')
        assert result.returncode == 2
        assert "invalid verdict 'maybe'" in result.stderr
        assert "Expected positional order: log-verdict <judge> <unit-id> <verdict> [feedback]" in result.stderr

    def test_fail_without_feedback_is_blocked(self) -> None:
        result = _run("uv run devbench log-verdict code_review E0-F8 fail")
        assert result.returncode == 2
        assert "feedback is required" in result.stderr

    def test_happy_path_pass_allows(self, tmp_path: Path) -> None:
        """Reviewer agent with round-token file writing a canonical pass verdict is allowed (H3)."""
        result = _run(
            'uv run devbench log-verdict code_review E0-F8 pass "all good"',
            agent_type=_REVIEWER_AGENT_TYPE,
            round_token=_ROUND_TOKEN,
            workspace_root=tmp_path,
        )
        assert result.returncode == 0
        assert result.stderr == ""

    def test_happy_path_fail_with_feedback_allows(self, tmp_path: Path) -> None:
        """Reviewer agent with round-token file writing a canonical fail verdict with feedback is allowed (H3)."""
        result = _run(
            'uv run devbench log-verdict code_review E0-F8 fail "one issue"',
            agent_type=_REVIEWER_AGENT_TYPE,
            round_token=_ROUND_TOKEN,
            workspace_root=tmp_path,
        )
        assert result.returncode == 0
        assert result.stderr == ""

    def test_canonical_verdict_without_token_file_is_blocked(self, tmp_path: Path) -> None:
        """H3/ADR-29: a reviewer agent with no round-token FILE is blocked from a canonical verdict."""
        result = _run(
            'uv run devbench log-verdict code_review E0-F8 pass "all good"',
            agent_type=_REVIEWER_AGENT_TYPE,
            round_token=None,
            workspace_root=tmp_path,
        )
        assert result.returncode == 2
        assert "token" in result.stderr.lower()

    def test_canonical_verdict_with_foreign_unit_token_is_blocked(self, tmp_path: Path) -> None:
        """H3/Fix B: a round-token FILE scoped to a different unit is rejected."""
        result = _run(
            'uv run devbench log-verdict code_review E0-F8 pass "all good"',
            agent_type=_REVIEWER_AGENT_TYPE,
            round_token="E0-F9-r1-stale",
            workspace_root=tmp_path,
        )
        assert result.returncode == 2
        assert "E0-F8" in result.stderr

    def test_review_team_namespaced_reviewer_allowed(self, tmp_path: Path) -> None:
        """ADR-28: the registered review_team:-infixed reviewer agent type is allowlisted."""
        result = _run(
            'uv run devbench log-verdict code_review E0-F8 pass "all good"',
            agent_type="devbench-orchestrate:review_team:code-reviewer",
            round_token=_ROUND_TOKEN,
            workspace_root=tmp_path,
        )
        assert result.returncode == 0
        assert result.stderr == ""

    def test_non_log_verdict_command_is_not_intercepted(self) -> None:
        """The hook must only intercept `log-verdict`; unrelated bash commands pass through."""
        result = _run("uv run devbench status")
        assert result.returncode == 0
        assert result.stderr == ""
