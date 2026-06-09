"""H3 default-deny tests for guard-verdict-format.sh.

AC-H3-1: Every non-reviewer agent type (including absent) is blocked from
    canonical verdicts; reviewer agents with the round token succeed.
AC-H3-2: A canonical verdict without DEVBENCH_REVIEW_ROUND_TOKEN is blocked
    even when the agent_type is a reviewer.
"""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

import pytest

HOOK_PATH = (
    Path(__file__).parent.parent / "plugin" / "devbench-orchestrate" / "scripts" / "guard-verdict-format.sh"
).resolve()

CANONICAL_JUDGES = [
    "code_review",
    "test_review",
    "doc_review",
    "changes_manifest",
    "security_review",
]

NON_CANONICAL_JUDGES = [
    "executor",
    "blocker_resolver",
    "manifest_amender",
    "task_factory",
]

REVIEWER_AGENT_TYPES = [
    "devbench-orchestrate:review-supervisor",
    "devbench-orchestrate:security-reviewer",
]

NON_REVIEWER_AGENT_TYPES = [
    "devbench-orchestrate:executor",
    "devbench-orchestrate:manifest-amender",
    "devbench-orchestrate:blocker-resolver",
    "devbench-orchestrate:task-factory",
    "some-other-agent",
    "malicious-spoof",
]

SAMPLE_UNIT_ID = "E8-F3-S1-T1"
ROUND_TOKEN = "test-round-token-abc123"


def _clean_env() -> dict[str, str]:
    """Return process env stripped of DEVBENCH vars that interfere with hooks."""
    return {
        k: v
        for k, v in os.environ.items()
        if k not in ("DEVBENCH_WORKSPACE_ROOT", "DEVBENCH_LOG_FILE", "DEVBENCH_REVIEW_ROUND_TOKEN")
    }


def _run_hook(
    command: str,
    agent_type: str | None = None,
    round_token: str | None = None,
) -> subprocess.CompletedProcess[str]:
    """Invoke the hook script with a crafted payload.

    Args:
        command: The bash command string to embed in the hook payload.
        agent_type: The agent_type value to embed in the JSON payload.
            When None the field is omitted (absent).
        round_token: Value for DEVBENCH_REVIEW_ROUND_TOKEN env var.
            When None the var is absent from the subprocess env.
    """
    payload: dict[str, object] = {
        "tool_name": "Bash",
        "tool_input": {"command": command},
    }
    if agent_type is not None:
        payload["agent_type"] = agent_type

    env = _clean_env()
    if round_token is not None:
        env["DEVBENCH_REVIEW_ROUND_TOKEN"] = round_token

    return subprocess.run(
        ["bash", str(HOOK_PATH)],
        input=json.dumps(payload),
        capture_output=True,
        text=True,
        check=False,
        env=env,
    )


# --------------------------------------------------------------------------- AC-H3-1


@pytest.mark.unit
class TestH3DefaultDenyNonReviewerAgents:
    """AC-H3-1: non-reviewer agent types are blocked from canonical verdicts."""

    @pytest.mark.parametrize("judge", CANONICAL_JUDGES)
    @pytest.mark.parametrize("agent_type", NON_REVIEWER_AGENT_TYPES)
    def test_non_reviewer_blocked_from_canonical_verdict(self, judge: str, agent_type: str) -> None:
        """Non-reviewer agent types must not write canonical reviewer verdicts."""
        cmd = f"uv run devbench log-verdict {judge} {SAMPLE_UNIT_ID} pass"
        result = _run_hook(cmd, agent_type=agent_type, round_token=ROUND_TOKEN)
        assert result.returncode == 2, (
            f"agent_type='{agent_type}' judge='{judge}' should be blocked (exit 2)"
            f" but got exit {result.returncode}; stderr: {result.stderr}"
        )
        assert "BLOCKED" in result.stderr or "blocked" in result.stderr.lower(), (
            f"stderr must mention block reason, got: {result.stderr!r}"
        )

    @pytest.mark.parametrize("judge", CANONICAL_JUDGES)
    def test_absent_agent_type_blocked_from_canonical_verdict(self, judge: str) -> None:
        """Absent agent_type must be blocked from canonical reviewer verdicts."""
        cmd = f"uv run devbench log-verdict {judge} {SAMPLE_UNIT_ID} pass"
        result = _run_hook(cmd, agent_type=None, round_token=ROUND_TOKEN)
        assert result.returncode == 2, (
            f"absent agent_type judge='{judge}' should be blocked (exit 2)"
            f" but got exit {result.returncode}; stderr: {result.stderr}"
        )
        assert "BLOCKED" in result.stderr or "blocked" in result.stderr.lower(), (
            f"stderr must mention block reason, got: {result.stderr!r}"
        )

    @pytest.mark.parametrize("judge", CANONICAL_JUDGES)
    @pytest.mark.parametrize("agent_type", REVIEWER_AGENT_TYPES)
    def test_reviewer_with_token_allowed_for_canonical_verdict(self, judge: str, agent_type: str) -> None:
        """Reviewer agent types WITH the round token must be allowed for canonical verdicts."""
        cmd = f"uv run devbench log-verdict {judge} {SAMPLE_UNIT_ID} pass"
        result = _run_hook(cmd, agent_type=agent_type, round_token=ROUND_TOKEN)
        assert result.returncode == 0, (
            f"agent_type='{agent_type}' judge='{judge}' with round token should be allowed (exit 0)"
            f" but got exit {result.returncode}; stderr: {result.stderr}"
        )

    @pytest.mark.parametrize("judge", NON_CANONICAL_JUDGES)
    @pytest.mark.parametrize("agent_type", NON_REVIEWER_AGENT_TYPES)
    def test_non_reviewer_allowed_for_non_canonical_judge(self, judge: str, agent_type: str) -> None:
        """Non-reviewer agent types may use non-canonical (audit-only) judge names."""
        cmd = f"uv run devbench log-verdict {judge} {SAMPLE_UNIT_ID} pass"
        result = _run_hook(cmd, agent_type=agent_type, round_token=ROUND_TOKEN)
        assert result.returncode == 0, (
            f"agent_type='{agent_type}' judge='{judge}' (non-canonical) should be allowed"
            f" but got exit {result.returncode}; stderr: {result.stderr}"
        )

    def test_absent_agent_type_allowed_for_executor_judge(self) -> None:
        """Absent agent_type must still be allowed for audit-only 'executor' judge."""
        cmd = f"uv run devbench log-verdict executor {SAMPLE_UNIT_ID} pass"
        result = _run_hook(cmd, agent_type=None, round_token=ROUND_TOKEN)
        assert result.returncode == 0, (
            f"absent agent_type with executor judge should be allowed"
            f" but got exit {result.returncode}; stderr: {result.stderr}"
        )


# --------------------------------------------------------------------------- AC-H3-2


@pytest.mark.unit
class TestH3RoundTokenRequired:
    """AC-H3-2: canonical verdicts require DEVBENCH_REVIEW_ROUND_TOKEN even for reviewer agents."""

    @pytest.mark.parametrize("judge", CANONICAL_JUDGES)
    @pytest.mark.parametrize("agent_type", REVIEWER_AGENT_TYPES)
    def test_reviewer_without_token_blocked(self, judge: str, agent_type: str) -> None:
        """Reviewer agent type without round token must be blocked for canonical verdicts."""
        cmd = f"uv run devbench log-verdict {judge} {SAMPLE_UNIT_ID} pass"
        result = _run_hook(cmd, agent_type=agent_type, round_token=None)
        assert result.returncode == 2, (
            f"agent_type='{agent_type}' judge='{judge}' WITHOUT token should be blocked (exit 2)"
            f" but got exit {result.returncode}; stderr: {result.stderr}"
        )
        stderr_lower = result.stderr.lower()
        assert "token" in stderr_lower or "round" in stderr_lower or "blocked" in stderr_lower, (
            f"stderr must mention token requirement, got: {result.stderr!r}"
        )

    @pytest.mark.parametrize("judge", CANONICAL_JUDGES)
    @pytest.mark.parametrize("agent_type", REVIEWER_AGENT_TYPES)
    def test_reviewer_with_empty_token_blocked(self, judge: str, agent_type: str) -> None:
        """Reviewer agent type with empty token string must be blocked for canonical verdicts."""
        cmd = f"uv run devbench log-verdict {judge} {SAMPLE_UNIT_ID} pass"
        result = _run_hook(cmd, agent_type=agent_type, round_token="")
        assert result.returncode == 2, (
            f"agent_type='{agent_type}' judge='{judge}' with empty token should be blocked"
            f" but got exit {result.returncode}; stderr: {result.stderr}"
        )

    @pytest.mark.parametrize("judge", CANONICAL_JUDGES)
    @pytest.mark.parametrize("agent_type", REVIEWER_AGENT_TYPES)
    def test_reviewer_with_token_and_fail_verdict_allowed(self, judge: str, agent_type: str) -> None:
        """Reviewer with token can write 'fail' verdicts (with required feedback)."""
        cmd = f'uv run devbench log-verdict {judge} {SAMPLE_UNIT_ID} fail "some finding"'
        result = _run_hook(cmd, agent_type=agent_type, round_token=ROUND_TOKEN)
        assert result.returncode == 0, (
            f"agent_type='{agent_type}' judge='{judge}' with token and feedback should be allowed"
            f" but got exit {result.returncode}; stderr: {result.stderr}"
        )

    @pytest.mark.parametrize("judge", NON_CANONICAL_JUDGES)
    @pytest.mark.parametrize("agent_type", REVIEWER_AGENT_TYPES)
    def test_reviewer_non_canonical_allowed_without_token(self, judge: str, agent_type: str) -> None:
        """Round token is not required for non-canonical judge names (audit-only path)."""
        cmd = f"uv run devbench log-verdict {judge} {SAMPLE_UNIT_ID} pass"
        result = _run_hook(cmd, agent_type=agent_type, round_token=None)
        assert result.returncode == 0, (
            f"agent_type='{agent_type}' non-canonical judge='{judge}' should be allowed without token"
            f" but got exit {result.returncode}; stderr: {result.stderr}"
        )


# --------------------------------------------------------------------------- Regression: existing rules still hold


@pytest.mark.unit
class TestH3ExistingBehaviorPreserved:
    """Non-log-verdict commands and basic validation rules are unaffected by H3."""

    def test_non_log_verdict_always_allowed(self) -> None:
        """Non-log-verdict Bash commands must never be intercepted."""
        result = _run_hook("git status", agent_type=None, round_token=None)
        assert result.returncode == 0

    def test_help_flag_passes_through(self) -> None:
        """`log-verdict --help` must not block; CLI is allowed to print usage."""
        result = _run_hook("uv run devbench log-verdict --help", agent_type=None, round_token=None)
        assert result.returncode == 0

    def test_unknown_judge_still_blocked(self) -> None:
        """Unknown judge names remain blocked regardless of agent_type."""
        cmd = f"uv run devbench log-verdict unknown_judge {SAMPLE_UNIT_ID} pass"
        result = _run_hook(cmd, agent_type="devbench-orchestrate:review-supervisor", round_token=ROUND_TOKEN)
        assert result.returncode == 2
        assert "unknown judge" in result.stderr.lower()

    def test_fail_verdict_without_feedback_still_blocked(self) -> None:
        """'fail' verdict without feedback stays blocked even for reviewer with token."""
        cmd = f"uv run devbench log-verdict code_review {SAMPLE_UNIT_ID} fail"
        result = _run_hook(cmd, agent_type="devbench-orchestrate:review-supervisor", round_token=ROUND_TOKEN)
        assert result.returncode == 2
        assert "feedback" in result.stderr.lower()

    def test_invalid_verdict_value_still_blocked(self) -> None:
        """'maybe' as verdict value stays blocked even for reviewer with token."""
        cmd = f"uv run devbench log-verdict code_review {SAMPLE_UNIT_ID} maybe"
        result = _run_hook(cmd, agent_type="devbench-orchestrate:review-supervisor", round_token=ROUND_TOKEN)
        assert result.returncode == 2
        assert "verdict" in result.stderr.lower()

    def test_error_message_names_allowed_agent_types(self) -> None:
        """Block message for canonical verdict must name the allowed agent types."""
        cmd = f"uv run devbench log-verdict code_review {SAMPLE_UNIT_ID} pass"
        result = _run_hook(cmd, agent_type="devbench-orchestrate:executor", round_token=ROUND_TOKEN)
        assert result.returncode == 2
        assert "review-supervisor" in result.stderr or "security-reviewer" in result.stderr, (
            f"stderr must name allowed agent types, got: {result.stderr!r}"
        )


# --------------------------------------------------------------------------- Workstream D: iac_review optional judge


IAC_REVIEWER_AGENT_TYPE = "devbench-orchestrate:iac-deploy-reviewer"


@pytest.mark.unit
class TestIacReviewOptionalJudge:
    """Workstream D: iac_review is a known + canonical reviewer judge.

    iac_review is the optional evidence-verifying IaC judge. Its verdict
    satisfies the done-gate, so it is a canonical reviewer judge subject to the
    H3 default-deny rules: only allowed reviewer agent types (now including
    devbench-orchestrate:iac-deploy-reviewer) may write it, and only with the
    round token.
    """

    def test_iac_review_is_a_known_judge(self) -> None:
        """iac_review must be accepted as a known judge name (not 'unknown judge')."""
        cmd = f"uv run devbench log-verdict iac_review {SAMPLE_UNIT_ID} pass"
        result = _run_hook(cmd, agent_type=IAC_REVIEWER_AGENT_TYPE, round_token=ROUND_TOKEN)
        assert "unknown judge" not in result.stderr.lower(), (
            f"iac_review must be a known judge; stderr: {result.stderr!r}"
        )

    def test_iac_reviewer_agent_with_token_allowed(self) -> None:
        """The iac-deploy-reviewer agent WITH the round token may write iac_review."""
        cmd = f"uv run devbench log-verdict iac_review {SAMPLE_UNIT_ID} pass"
        result = _run_hook(cmd, agent_type=IAC_REVIEWER_AGENT_TYPE, round_token=ROUND_TOKEN)
        assert result.returncode == 0, (
            f"iac-deploy-reviewer with token should write iac_review (exit 0); stderr: {result.stderr!r}"
        )

    @pytest.mark.parametrize("agent_type", REVIEWER_AGENT_TYPES)
    def test_existing_reviewer_agents_also_allowed_for_iac_review(self, agent_type: str) -> None:
        """review-supervisor and security-reviewer may also write iac_review (canonical)."""
        cmd = f"uv run devbench log-verdict iac_review {SAMPLE_UNIT_ID} pass"
        result = _run_hook(cmd, agent_type=agent_type, round_token=ROUND_TOKEN)
        assert result.returncode == 0, (
            f"agent_type='{agent_type}' should write iac_review (exit 0); stderr: {result.stderr!r}"
        )

    @pytest.mark.parametrize("agent_type", NON_REVIEWER_AGENT_TYPES)
    def test_non_reviewer_blocked_from_iac_review(self, agent_type: str) -> None:
        """Non-reviewer agent types are default-denied from the canonical iac_review verdict."""
        cmd = f"uv run devbench log-verdict iac_review {SAMPLE_UNIT_ID} pass"
        result = _run_hook(cmd, agent_type=agent_type, round_token=ROUND_TOKEN)
        assert result.returncode == 2, (
            f"agent_type='{agent_type}' must be blocked from iac_review (exit 2); stderr: {result.stderr!r}"
        )

    def test_iac_reviewer_without_token_blocked(self) -> None:
        """iac_review still requires the round token (H3 second factor)."""
        cmd = f"uv run devbench log-verdict iac_review {SAMPLE_UNIT_ID} pass"
        result = _run_hook(cmd, agent_type=IAC_REVIEWER_AGENT_TYPE, round_token=None)
        assert result.returncode == 2, (
            f"iac_review without round token must be blocked (exit 2); stderr: {result.stderr!r}"
        )

    def test_iac_deploy_reviewer_allowed_for_core_canonical_verdict(self) -> None:
        """The iac-deploy-reviewer agent is in the allowlist, so it may also write core verdicts.

        The allowlist is per-agent-type, not per-judge; this asserts the
        allowlist addition is wired through for code_review as well.
        """
        cmd = f"uv run devbench log-verdict code_review {SAMPLE_UNIT_ID} pass"
        result = _run_hook(cmd, agent_type=IAC_REVIEWER_AGENT_TYPE, round_token=ROUND_TOKEN)
        assert result.returncode == 0, (
            f"iac-deploy-reviewer is an allowed reviewer agent type; stderr: {result.stderr!r}"
        )
