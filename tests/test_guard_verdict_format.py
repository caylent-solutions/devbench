"""H3 default-deny tests for guard-verdict-format.sh.

AC-H3-1: Every non-reviewer agent type (including absent) is blocked from
    canonical verdicts; reviewer agents with the round token succeed.
AC-H3-2: A canonical verdict without the per-round token FILE is blocked
    even when the agent_type is a reviewer.

ADR-29: the round token is no longer an env var. The guard reads a FILE at
    <DEVBENCH_WORKSPACE_ROOT>/.devbench/review-round-token. The file must
    exist, be non-empty, and be scoped to the unit under review (prefix
    "<unit-id>-"). If DEVBENCH_WORKSPACE_ROOT is unset, or the file is
    absent/empty/foreign-unit-prefixed, the canonical verdict is blocked.
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
    # ADR-28: the four review_team reviewers are dispatched directly by the
    # orchestrate skill and present their subdir-namespaced agent_type, so each
    # is allowlisted in its REGISTERED `review_team:`-infixed form.
    "devbench-orchestrate:review_team:code-reviewer",
    "devbench-orchestrate:review_team:test-reviewer",
    "devbench-orchestrate:review_team:doc-reviewer",
    "devbench-orchestrate:review_team:changes-manifest",
    "devbench-orchestrate:security-reviewer",
    # Deprecated, but still in the allowlist for back-compat.
    "devbench-orchestrate:review-supervisor",
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
# ADR-29 + Fix B: a valid token is scoped to the unit under review
# (prefix "<unit-id>-") and is written to the round-token FILE.
ROUND_TOKEN = f"{SAMPLE_UNIT_ID}-r1-abc123"


def _clean_env() -> dict[str, str]:
    """Return process env stripped of DEVBENCH vars that interfere with hooks.

    ``BASH_ENV`` (and the POSIX ``ENV``) are stripped: the devcontainer sets
    ``BASH_ENV=/workspaces/telemetry/shell.env``, which makes a non-interactive
    ``bash <hook>`` re-source ``shell.env`` on startup. Stripping it guarantees
    the subprocess sees exactly the env this helper constructs. The obsolete
    ``DEVBENCH_REVIEW_ROUND_TOKEN`` (ADR-29 replaced it with a file) is stripped
    too -- harmless, but keeps the subprocess hermetic against any leftover.
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
    command: str,
    tmp_path: Path,
    agent_type: str | None = None,
    round_token: str | None = None,
) -> subprocess.CompletedProcess[str]:
    """Invoke the hook script with a crafted payload.

    Args:
        command: The bash command string to embed in the hook payload.
        tmp_path: A per-test temp dir used as the DEVBENCH_WORKSPACE_ROOT.
        agent_type: The agent_type value to embed in the JSON payload.
            When None the field is omitted (absent).
        round_token: When set, write this value to the round-token FILE at
            <tmp_path>/.devbench/review-round-token. When None the file is not
            created, so the canonical-verdict guard fails closed.
    """
    payload: dict[str, object] = {
        "tool_name": "Bash",
        "tool_input": {"command": command},
    }
    if agent_type is not None:
        payload["agent_type"] = agent_type

    env = _clean_env()
    env["DEVBENCH_WORKSPACE_ROOT"] = str(tmp_path)
    if round_token is not None:
        devbench_dir = tmp_path / ".devbench"
        devbench_dir.mkdir(parents=True, exist_ok=True)
        (devbench_dir / "review-round-token").write_text(round_token)

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
    def test_non_reviewer_blocked_from_canonical_verdict(self, judge: str, agent_type: str, tmp_path: Path) -> None:
        """Non-reviewer agent types must not write canonical reviewer verdicts."""
        cmd = f"uv run devbench log-verdict {judge} {SAMPLE_UNIT_ID} pass"
        result = _run_hook(cmd, tmp_path, agent_type=agent_type, round_token=ROUND_TOKEN)
        assert result.returncode == 2, (
            f"agent_type='{agent_type}' judge='{judge}' should be blocked (exit 2)"
            f" but got exit {result.returncode}; stderr: {result.stderr}"
        )
        assert "BLOCKED" in result.stderr or "blocked" in result.stderr.lower(), (
            f"stderr must mention block reason, got: {result.stderr!r}"
        )

    @pytest.mark.parametrize("judge", CANONICAL_JUDGES)
    def test_absent_agent_type_blocked_from_canonical_verdict(self, judge: str, tmp_path: Path) -> None:
        """Absent agent_type must be blocked from canonical reviewer verdicts."""
        cmd = f"uv run devbench log-verdict {judge} {SAMPLE_UNIT_ID} pass"
        result = _run_hook(cmd, tmp_path, agent_type=None, round_token=ROUND_TOKEN)
        assert result.returncode == 2, (
            f"absent agent_type judge='{judge}' should be blocked (exit 2)"
            f" but got exit {result.returncode}; stderr: {result.stderr}"
        )
        assert "BLOCKED" in result.stderr or "blocked" in result.stderr.lower(), (
            f"stderr must mention block reason, got: {result.stderr!r}"
        )

    @pytest.mark.parametrize("judge", CANONICAL_JUDGES)
    @pytest.mark.parametrize("agent_type", REVIEWER_AGENT_TYPES)
    def test_reviewer_with_token_allowed_for_canonical_verdict(
        self, judge: str, agent_type: str, tmp_path: Path
    ) -> None:
        """Reviewer agent types WITH the round token must be allowed for canonical verdicts."""
        cmd = f"uv run devbench log-verdict {judge} {SAMPLE_UNIT_ID} pass"
        result = _run_hook(cmd, tmp_path, agent_type=agent_type, round_token=ROUND_TOKEN)
        assert result.returncode == 0, (
            f"agent_type='{agent_type}' judge='{judge}' with round token should be allowed (exit 0)"
            f" but got exit {result.returncode}; stderr: {result.stderr}"
        )

    @pytest.mark.parametrize("judge", NON_CANONICAL_JUDGES)
    @pytest.mark.parametrize("agent_type", NON_REVIEWER_AGENT_TYPES)
    def test_non_reviewer_allowed_for_non_canonical_judge(self, judge: str, agent_type: str, tmp_path: Path) -> None:
        """Non-reviewer agent types may use non-canonical (audit-only) judge names."""
        cmd = f"uv run devbench log-verdict {judge} {SAMPLE_UNIT_ID} pass"
        result = _run_hook(cmd, tmp_path, agent_type=agent_type, round_token=ROUND_TOKEN)
        assert result.returncode == 0, (
            f"agent_type='{agent_type}' judge='{judge}' (non-canonical) should be allowed"
            f" but got exit {result.returncode}; stderr: {result.stderr}"
        )

    def test_absent_agent_type_allowed_for_executor_judge(self, tmp_path: Path) -> None:
        """Absent agent_type must still be allowed for audit-only 'executor' judge."""
        cmd = f"uv run devbench log-verdict executor {SAMPLE_UNIT_ID} pass"
        result = _run_hook(cmd, tmp_path, agent_type=None, round_token=ROUND_TOKEN)
        assert result.returncode == 0, (
            f"absent agent_type with executor judge should be allowed"
            f" but got exit {result.returncode}; stderr: {result.stderr}"
        )


# --------------------------------------------------------------------------- AC-H3-2


@pytest.mark.unit
class TestH3RoundTokenRequired:
    """AC-H3-2: canonical verdicts require the round-token FILE even for reviewer agents."""

    @pytest.mark.parametrize("judge", CANONICAL_JUDGES)
    @pytest.mark.parametrize("agent_type", REVIEWER_AGENT_TYPES)
    def test_reviewer_without_token_file_blocked(self, judge: str, agent_type: str, tmp_path: Path) -> None:
        """Reviewer agent type with no round-token FILE must be blocked for canonical verdicts."""
        cmd = f"uv run devbench log-verdict {judge} {SAMPLE_UNIT_ID} pass"
        # round_token=None -> the file is never created (absent token file).
        result = _run_hook(cmd, tmp_path, agent_type=agent_type, round_token=None)
        assert result.returncode == 2, (
            f"agent_type='{agent_type}' judge='{judge}' WITHOUT token file should be blocked (exit 2)"
            f" but got exit {result.returncode}; stderr: {result.stderr}"
        )
        stderr_lower = result.stderr.lower()
        assert "token" in stderr_lower or "round" in stderr_lower or "blocked" in stderr_lower, (
            f"stderr must mention token requirement, got: {result.stderr!r}"
        )

    @pytest.mark.parametrize("judge", CANONICAL_JUDGES)
    @pytest.mark.parametrize("agent_type", REVIEWER_AGENT_TYPES)
    def test_reviewer_with_empty_token_file_blocked(self, judge: str, agent_type: str, tmp_path: Path) -> None:
        """Reviewer agent type with an empty round-token FILE must be blocked."""
        cmd = f"uv run devbench log-verdict {judge} {SAMPLE_UNIT_ID} pass"
        result = _run_hook(cmd, tmp_path, agent_type=agent_type, round_token="")
        assert result.returncode == 2, (
            f"agent_type='{agent_type}' judge='{judge}' with empty token file should be blocked"
            f" but got exit {result.returncode}; stderr: {result.stderr}"
        )

    @pytest.mark.parametrize("judge", CANONICAL_JUDGES)
    @pytest.mark.parametrize("agent_type", REVIEWER_AGENT_TYPES)
    def test_reviewer_workspace_root_unset_blocked(self, judge: str, agent_type: str, tmp_path: Path) -> None:
        """Without DEVBENCH_WORKSPACE_ROOT the guard cannot locate the token file and fails closed."""
        cmd = f"uv run devbench log-verdict {judge} {SAMPLE_UNIT_ID} pass"
        payload: dict[str, object] = {
            "tool_name": "Bash",
            "tool_input": {"command": cmd},
            "agent_type": agent_type,
        }
        # Build env WITHOUT DEVBENCH_WORKSPACE_ROOT (so the guard cannot resolve the file).
        env = _clean_env()
        result = subprocess.run(
            ["bash", str(HOOK_PATH)],
            input=json.dumps(payload),
            capture_output=True,
            text=True,
            check=False,
            env=env,
        )
        assert result.returncode == 2, (
            f"agent_type='{agent_type}' judge='{judge}' with unset workspace root should be blocked"
            f" but got exit {result.returncode}; stderr: {result.stderr}"
        )
        assert "DEVBENCH_WORKSPACE_ROOT" in result.stderr, (
            f"stderr must explain the missing workspace root, got: {result.stderr!r}"
        )

    @pytest.mark.parametrize("judge", CANONICAL_JUDGES)
    @pytest.mark.parametrize("agent_type", REVIEWER_AGENT_TYPES)
    def test_reviewer_with_token_and_fail_verdict_allowed(self, judge: str, agent_type: str, tmp_path: Path) -> None:
        """Reviewer with token can write 'fail' verdicts (with required feedback)."""
        cmd = f'uv run devbench log-verdict {judge} {SAMPLE_UNIT_ID} fail "some finding"'
        result = _run_hook(cmd, tmp_path, agent_type=agent_type, round_token=ROUND_TOKEN)
        assert result.returncode == 0, (
            f"agent_type='{agent_type}' judge='{judge}' with token and feedback should be allowed"
            f" but got exit {result.returncode}; stderr: {result.stderr}"
        )

    @pytest.mark.parametrize("judge", NON_CANONICAL_JUDGES)
    @pytest.mark.parametrize("agent_type", REVIEWER_AGENT_TYPES)
    def test_reviewer_non_canonical_allowed_without_token(self, judge: str, agent_type: str, tmp_path: Path) -> None:
        """Round token is not required for non-canonical judge names (audit-only path)."""
        cmd = f"uv run devbench log-verdict {judge} {SAMPLE_UNIT_ID} pass"
        result = _run_hook(cmd, tmp_path, agent_type=agent_type, round_token=None)
        assert result.returncode == 0, (
            f"agent_type='{agent_type}' non-canonical judge='{judge}' should be allowed without token"
            f" but got exit {result.returncode}; stderr: {result.stderr}"
        )


# --------------------------------------------------------------------------- Fix B: round token unit-scoped


@pytest.mark.unit
class TestH3RoundTokenUnitScoped:
    """Fix B: the round token must be scoped to the unit being reviewed.

    The orchestrate skill writes the round-token FILE as ``<unit-id>-r<n>-<rand>``
    before each review round and clears it after, so a leftover token scoped to a
    DIFFERENT unit can never satisfy this unit's canonical verdict -- the exact
    staleness that masked the E9-F1-S1-T5 incident.
    """

    @pytest.mark.parametrize("judge", CANONICAL_JUDGES)
    @pytest.mark.parametrize("agent_type", REVIEWER_AGENT_TYPES)
    def test_token_scoped_to_other_unit_blocked(self, judge: str, agent_type: str, tmp_path: Path) -> None:
        """A token file prefixed for a different unit is rejected even for a reviewer agent."""
        cmd = f"uv run devbench log-verdict {judge} {SAMPLE_UNIT_ID} pass"
        stale = "E1-F1-S1-T9-r1-stale"  # scoped to a DIFFERENT unit than SAMPLE_UNIT_ID
        result = _run_hook(cmd, tmp_path, agent_type=agent_type, round_token=stale)
        assert result.returncode == 2, (
            f"agent_type='{agent_type}' judge='{judge}' with a foreign-unit token should be blocked"
            f" but got exit {result.returncode}; stderr: {result.stderr}"
        )
        assert SAMPLE_UNIT_ID in result.stderr, (
            f"stderr must name the unit the token failed to scope to, got: {result.stderr!r}"
        )

    @pytest.mark.parametrize("judge", CANONICAL_JUDGES)
    @pytest.mark.parametrize("agent_type", REVIEWER_AGENT_TYPES)
    def test_token_scoped_to_correct_unit_allowed(self, judge: str, agent_type: str, tmp_path: Path) -> None:
        """A token file correctly scoped to the unit under review is accepted."""
        cmd = f"uv run devbench log-verdict {judge} {SAMPLE_UNIT_ID} pass"
        result = _run_hook(cmd, tmp_path, agent_type=agent_type, round_token=f"{SAMPLE_UNIT_ID}-r2-xyz")
        assert result.returncode == 0, (
            f"agent_type='{agent_type}' judge='{judge}' with a correctly-scoped token should be allowed"
            f" but got exit {result.returncode}; stderr: {result.stderr}"
        )


# --------------------------------------------------------------------------- Regression: existing rules still hold


@pytest.mark.unit
class TestH3ExistingBehaviorPreserved:
    """Non-log-verdict commands and basic validation rules are unaffected by H3."""

    def test_non_log_verdict_always_allowed(self, tmp_path: Path) -> None:
        """Non-log-verdict Bash commands must never be intercepted."""
        result = _run_hook("git status", tmp_path, agent_type=None, round_token=None)
        assert result.returncode == 0

    def test_help_flag_passes_through(self, tmp_path: Path) -> None:
        """`log-verdict --help` must not block; CLI is allowed to print usage."""
        result = _run_hook("uv run devbench log-verdict --help", tmp_path, agent_type=None, round_token=None)
        assert result.returncode == 0

    def test_unknown_judge_still_blocked(self, tmp_path: Path) -> None:
        """Unknown judge names remain blocked regardless of agent_type."""
        cmd = f"uv run devbench log-verdict unknown_judge {SAMPLE_UNIT_ID} pass"
        result = _run_hook(cmd, tmp_path, agent_type="devbench-orchestrate:review-supervisor", round_token=ROUND_TOKEN)
        assert result.returncode == 2
        assert "unknown judge" in result.stderr.lower()

    def test_fail_verdict_without_feedback_still_blocked(self, tmp_path: Path) -> None:
        """'fail' verdict without feedback stays blocked even for reviewer with token."""
        cmd = f"uv run devbench log-verdict code_review {SAMPLE_UNIT_ID} fail"
        result = _run_hook(cmd, tmp_path, agent_type="devbench-orchestrate:review-supervisor", round_token=ROUND_TOKEN)
        assert result.returncode == 2
        assert "feedback" in result.stderr.lower()

    def test_invalid_verdict_value_still_blocked(self, tmp_path: Path) -> None:
        """'maybe' as verdict value stays blocked even for reviewer with token."""
        cmd = f"uv run devbench log-verdict code_review {SAMPLE_UNIT_ID} maybe"
        result = _run_hook(cmd, tmp_path, agent_type="devbench-orchestrate:review-supervisor", round_token=ROUND_TOKEN)
        assert result.returncode == 2
        assert "verdict" in result.stderr.lower()

    def test_error_message_names_allowed_agent_types(self, tmp_path: Path) -> None:
        """Block message for canonical verdict must name the allowed agent types."""
        cmd = f"uv run devbench log-verdict code_review {SAMPLE_UNIT_ID} pass"
        result = _run_hook(cmd, tmp_path, agent_type="devbench-orchestrate:executor", round_token=ROUND_TOKEN)
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
    H3 default-deny rules: only allowed reviewer agent types (including
    devbench-orchestrate:iac-deploy-reviewer) may write it, and only with the
    round token file present and unit-scoped.
    """

    def test_iac_review_is_a_known_judge(self, tmp_path: Path) -> None:
        """iac_review must be accepted as a known judge name (not 'unknown judge')."""
        cmd = f"uv run devbench log-verdict iac_review {SAMPLE_UNIT_ID} pass"
        result = _run_hook(cmd, tmp_path, agent_type=IAC_REVIEWER_AGENT_TYPE, round_token=ROUND_TOKEN)
        assert "unknown judge" not in result.stderr.lower(), (
            f"iac_review must be a known judge; stderr: {result.stderr!r}"
        )

    def test_iac_reviewer_agent_with_token_allowed(self, tmp_path: Path) -> None:
        """The iac-deploy-reviewer agent WITH the round token may write iac_review."""
        cmd = f"uv run devbench log-verdict iac_review {SAMPLE_UNIT_ID} pass"
        result = _run_hook(cmd, tmp_path, agent_type=IAC_REVIEWER_AGENT_TYPE, round_token=ROUND_TOKEN)
        assert result.returncode == 0, (
            f"iac-deploy-reviewer with token should write iac_review (exit 0); stderr: {result.stderr!r}"
        )

    @pytest.mark.parametrize("agent_type", REVIEWER_AGENT_TYPES)
    def test_existing_reviewer_agents_also_allowed_for_iac_review(self, agent_type: str, tmp_path: Path) -> None:
        """The review_team reviewers and security-reviewer may also write iac_review (canonical)."""
        cmd = f"uv run devbench log-verdict iac_review {SAMPLE_UNIT_ID} pass"
        result = _run_hook(cmd, tmp_path, agent_type=agent_type, round_token=ROUND_TOKEN)
        assert result.returncode == 0, (
            f"agent_type='{agent_type}' should write iac_review (exit 0); stderr: {result.stderr!r}"
        )

    @pytest.mark.parametrize("agent_type", NON_REVIEWER_AGENT_TYPES)
    def test_non_reviewer_blocked_from_iac_review(self, agent_type: str, tmp_path: Path) -> None:
        """Non-reviewer agent types are default-denied from the canonical iac_review verdict."""
        cmd = f"uv run devbench log-verdict iac_review {SAMPLE_UNIT_ID} pass"
        result = _run_hook(cmd, tmp_path, agent_type=agent_type, round_token=ROUND_TOKEN)
        assert result.returncode == 2, (
            f"agent_type='{agent_type}' must be blocked from iac_review (exit 2); stderr: {result.stderr!r}"
        )

    def test_iac_reviewer_without_token_blocked(self, tmp_path: Path) -> None:
        """iac_review still requires the round token file (H3 second factor)."""
        cmd = f"uv run devbench log-verdict iac_review {SAMPLE_UNIT_ID} pass"
        result = _run_hook(cmd, tmp_path, agent_type=IAC_REVIEWER_AGENT_TYPE, round_token=None)
        assert result.returncode == 2, (
            f"iac_review without round token must be blocked (exit 2); stderr: {result.stderr!r}"
        )

    def test_iac_deploy_reviewer_allowed_for_core_canonical_verdict(self, tmp_path: Path) -> None:
        """The iac-deploy-reviewer agent is in the allowlist, so it may also write core verdicts.

        The allowlist is per-agent-type, not per-judge; this asserts the
        allowlist addition is wired through for code_review as well.
        """
        cmd = f"uv run devbench log-verdict code_review {SAMPLE_UNIT_ID} pass"
        result = _run_hook(cmd, tmp_path, agent_type=IAC_REVIEWER_AGENT_TYPE, round_token=ROUND_TOKEN)
        assert result.returncode == 0, (
            f"iac-deploy-reviewer is an allowed reviewer agent type; stderr: {result.stderr!r}"
        )
