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

# H3: reviewer agent type and round token needed for canonical verdicts.
_REVIEWER_AGENT_TYPE = "devbench-orchestrate:review-supervisor"
# Fix B: the token must be scoped to the unit under review (prefix "<unit-id>-");
# the happy-path canonical tests below use unit E0-F8.
_ROUND_TOKEN = "E0-F8-r1-token"


def _clean_env() -> dict[str, str]:
    """Return the process env with legacy DEVBENCH_WORKSPACE_ROOT and DEVBENCH_LOG_FILE stripped.

    _hook_lib.sh rejects legacy JUDGE_* hook vars (AC-197-9). Tests that source
    _hook_lib.sh must not inherit those vars from the pytest process environment.
    Also strips DEVBENCH_REVIEW_ROUND_TOKEN so tests that omit it start clean.

    ``BASH_ENV``/``ENV`` are stripped too: the devcontainer sets
    ``BASH_ENV=/workspaces/telemetry/shell.env``, so a non-interactive
    ``bash <hook>`` re-sources ``shell.env`` on startup -- which the orchestrate
    skill populates with a per-round ``DEVBENCH_REVIEW_ROUND_TOKEN``. Without
    stripping it the hook subprocess would re-acquire (or overwrite) the very
    vars this helper controls, making the tests non-hermetic.
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
) -> subprocess.CompletedProcess[str]:
    """Invoke the hook with a crafted Bash tool-input command string.

    Args:
        command: The bash command string to embed in the hook payload.
        agent_type: When set, adds agent_type field to the JSON payload.
        round_token: When set, injects DEVBENCH_REVIEW_ROUND_TOKEN env var.
    """
    payload: dict[str, object] = {"tool_name": "Bash", "tool_input": {"command": command}}
    if agent_type is not None:
        payload["agent_type"] = agent_type
    env = _clean_env()
    if round_token is not None:
        env["DEVBENCH_REVIEW_ROUND_TOKEN"] = round_token
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
        # Must not report the misleading "invalid verdict ''" that the pre-fix code emitted.
        assert "invalid verdict" not in result.stderr

    def test_shell_redirection_does_not_count_as_positional(self) -> None:
        """`log-verdict 2>&1 | tail` must not treat '2>&1' as the judge name."""
        result = _run("uv run devbench log-verdict 2>&1 | tail -20")
        assert result.returncode == 2
        assert "missing required argument" in result.stderr
        # Must not flag the redirection token as an unknown judge.
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

    def test_happy_path_pass_allows(self) -> None:
        """Reviewer agent with round token writing a canonical pass verdict is allowed (H3)."""
        result = _run(
            'uv run devbench log-verdict code_review E0-F8 pass "all good"',
            agent_type=_REVIEWER_AGENT_TYPE,
            round_token=_ROUND_TOKEN,
        )
        assert result.returncode == 0
        assert result.stderr == ""

    def test_happy_path_fail_with_feedback_allows(self) -> None:
        """Reviewer agent with round token writing a canonical fail verdict with feedback is allowed (H3)."""
        result = _run(
            'uv run devbench log-verdict code_review E0-F8 fail "one issue"',
            agent_type=_REVIEWER_AGENT_TYPE,
            round_token=_ROUND_TOKEN,
        )
        assert result.returncode == 0
        assert result.stderr == ""

    def test_non_log_verdict_command_is_not_intercepted(self) -> None:
        """The hook must only intercept `log-verdict`; unrelated bash commands pass through."""
        result = _run("uv run devbench status")
        assert result.returncode == 0
        assert result.stderr == ""
