"""Issue #128 regression: executor must not act on REVIEW_PASS verdict bodies.

The orchestrate skill (`plugin/devbench/skills/orchestrate/SKILL.md`) and the
executor agent prompt (`plugin/devbench/agents/executor.md`) are runtime
prompts the LLM reads via Claude Agent SDK; there is no Python entry point to
call-and-assert against. This test pins the canonical "REVIEW_PASS is
terminal" language by-content so a future edit cannot silently remove the
rule that prevents the bug from returning.

Bug: at runtime the executor read a MEDIUM-severity informational note inside
a security_review PASS verdict, treated it as a required action item, and
performed a REFACTOR cycle (10 new tests + COGNITO_CONFIG guards) instead of
proceeding to git-ops. git-ops then failed because PR creation was redundant
on the same branch (companion issue #129). Issue #128.

Fix: SKILL.md step 7 now contains an explicit "CRITICAL (issue #128)" rule
that REVIEW_PASS is terminal -- the orchestrator branches solely on
pass-vs-fail, never on verdict-body content. executor.md gains a parallel
"REVIEW_PASS verdicts are terminal" section that lists the three (and only
three) situations where the executor is legitimately invoked, and forbids
acting on PASS verdict content.

This test asserts each protective fragment is present so an accidental edit
that removes one of them fails CI before merging.
"""

from __future__ import annotations

from pathlib import Path

import pytest

PLUGIN_ROOT = Path(__file__).resolve().parent.parent.parent / "plugin" / "devbench"
SKILL_PATH = PLUGIN_ROOT / "skills" / "orchestrate" / "SKILL.md"
EXECUTOR_PATH = PLUGIN_ROOT / "agents" / "executor.md"


@pytest.fixture(scope="module")
def skill_text() -> str:
    return SKILL_PATH.read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def executor_text() -> str:
    return EXECUTOR_PATH.read_text(encoding="utf-8")


@pytest.mark.integration
class TestSkillReviewPassTerminality:
    """Pin the issue #128 SKILL.md step-7 rule so the bug cannot return."""

    def test_skill_file_exists(self) -> None:
        assert SKILL_PATH.is_file(), f"orchestrate SKILL.md missing at {SKILL_PATH}"

    def test_critical_marker_present(self, skill_text: str) -> None:
        """Step 7 must carry the explicit 'CRITICAL (issue #128)' marker."""
        assert "**CRITICAL (issue #128)**" in skill_text, (
            "SKILL.md step 7 is missing the issue #128 CRITICAL marker. "
            "That marker anchors the rule that prevents the orchestrator from "
            "re-invoking the executor based on PASS-verdict body content."
        )

    @pytest.mark.parametrize(
        "fragment",
        [
            # Terminality + branch-on-pass-vs-fail rule.
            "REVIEW_PASS is a terminal signal",
            "branch SOLELY on pass-vs-fail",
            # Forbid inspecting verdict bodies for improvement signals.
            "do NOT inspect verdict bodies",
            # Forbid acting on informational PASS-verdict content.
            "Informational content in PASS verdicts",
            "MUST NOT trigger additional executor work cycles",
            # Same rule applies to security-reviewer.
            "Do NOT re-invoke executor based on the security_review verdict body",
        ],
    )
    def test_each_protective_fragment_present(self, skill_text: str, fragment: str) -> None:
        assert fragment in skill_text, (
            f"SKILL.md step 7 is missing protective fragment: {fragment!r}. "
            "All fragments must be present so issue #128 cannot return."
        )

    def test_regression_test_path_referenced(self, skill_text: str) -> None:
        """SKILL.md should mention this test by-name so future readers find it."""
        assert "test_executor_review_pass_terminality.py" in skill_text


@pytest.mark.integration
class TestExecutorReviewPassTerminality:
    """Pin the issue #128 executor.md rule so the bug cannot return."""

    def test_executor_file_exists(self) -> None:
        assert EXECUTOR_PATH.is_file(), f"executor agent prompt missing at {EXECUTOR_PATH}"

    def test_review_pass_terminal_section_present(self, executor_text: str) -> None:
        """The 'REVIEW_PASS verdicts are terminal' section must be present."""
        assert "REVIEW_PASS verdicts are terminal (issue #128)" in executor_text, (
            "executor.md is missing the issue #128 'REVIEW_PASS verdicts are "
            "terminal' section. That section enumerates the three (and only "
            "three) legitimate executor-invocation triggers; do not remove it."
        )

    @pytest.mark.parametrize(
        "fragment",
        [
            # Three (and only three) legitimate trigger scenarios.
            "first executor pass after claiming a task",
            "After a judge returns REVIEW_FAIL",
            "After git-ops returns exit code 2 (CI failure) or 3 (PR-bot review feedback)",
            # Explicit 'never invoked because of PASS verdict content'.
            "**never** invoked because of the content of a passing verdict",
            # Self-correcting heuristic.
            "If you find yourself reading a PASS verdict's body looking for things to fix, stop.",
            # Cross-link to the regression test.
            "test_executor_review_pass_terminality.py",
        ],
    )
    def test_each_protective_fragment_present(self, executor_text: str, fragment: str) -> None:
        assert fragment in executor_text, (
            f"executor.md is missing protective fragment: {fragment!r}. "
            "All fragments must be present so issue #128 cannot return."
        )
