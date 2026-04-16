"""Unit tests for orchestrate SKILL.md content correctness."""

from __future__ import annotations

from pathlib import Path

import pytest

SKILL_PATH = Path(__file__).parent.parent.parent / "plugin" / "devbench" / "skills" / "orchestrate" / "SKILL.md"


@pytest.mark.unit
class TestOrchestrateSkillReviewSupervisor:
    """AC-1: Step 5 invokes review-supervisor, not 4 individual review agents."""

    def test_skill_references_review_supervisor(self) -> None:
        """AC-1: SKILL.md must reference review-supervisor."""
        content = SKILL_PATH.read_text()
        assert "review-supervisor" in content, "SKILL.md must invoke review-supervisor in step 5"

    def test_skill_no_individual_reviewer_invocations(self) -> None:
        """AC-8: SKILL.md must not reference individual review agents."""
        content = SKILL_PATH.read_text()
        forbidden = [
            "devbench:code-reviewer",
            "devbench:test-reviewer",
            "devbench:doc-reviewer",
            "devbench:changes-manifest",
        ]
        for agent in forbidden:
            assert agent not in content, (
                f"SKILL.md must not reference individual reviewer '{agent}' — use review-supervisor instead"
            )


@pytest.mark.unit
class TestOrchestrateSkillStep5Branching:
    """AC-2: Step 5 explicitly branches on REVIEW_PASS and REVIEW_FAIL."""

    def test_step5_branches_on_review_pass(self) -> None:
        """AC-2: Step 5 must explicitly route REVIEW_PASS to step 7."""
        content = SKILL_PATH.read_text()
        assert "REVIEW_PASS" in content, "SKILL.md step 5 must explicitly handle REVIEW_PASS"

    def test_step5_branches_on_review_fail(self) -> None:
        """AC-2: Step 5 must explicitly route REVIEW_FAIL to step 6."""
        content = SKILL_PATH.read_text()
        assert "REVIEW_FAIL" in content, "SKILL.md step 5 must explicitly handle REVIEW_FAIL"


@pytest.mark.unit
class TestOrchestrateSkillStep6RetryLoop:
    """AC-3: Step 6 explicitly states loop target and excludes security."""

    def test_step6_return_to_step5(self) -> None:
        """AC-3: Step 6 must explicitly say 'Return to step 5'."""
        content = SKILL_PATH.read_text()
        assert "Return to step 5" in content, "SKILL.md step 6 must say 'Return to step 5' explicitly"

    def test_step6_excludes_security(self) -> None:
        """AC-3: Step 6 must explicitly say do NOT invoke security-reviewer."""
        content = SKILL_PATH.read_text()
        assert "Do NOT invoke security-reviewer" in content, (
            "SKILL.md step 6 must say 'Do NOT invoke security-reviewer' explicitly"
        )


@pytest.mark.unit
class TestOrchestrateSkillStep7SecurityPass:
    """AC-4 and AC-5: Step 7 handles security PASS with explicit routing."""

    def test_step7_proceed_to_step8_on_pass(self) -> None:
        """AC-4: Step 7 must say 'proceed immediately to step 8' on security PASS."""
        content = SKILL_PATH.read_text()
        assert "proceed immediately to step 8" in content, (
            "SKILL.md step 7 must say 'proceed immediately to step 8' on security PASS"
        )

    def test_step7_no_rerun_review_supervisor(self) -> None:
        """AC-5: Step 7 must say 'Do NOT re-run review-supervisor' on security PASS."""
        content = SKILL_PATH.read_text()
        assert "Do NOT re-run review-supervisor" in content, (
            "SKILL.md step 7 must say 'Do NOT re-run review-supervisor' on security PASS"
        )


@pytest.mark.unit
class TestOrchestrateSkillStandards:
    """AC-6 and AC-7: Standards section enforces security-once and retry-loop rules."""

    def test_standards_security_runs_once(self) -> None:
        """AC-6: Standards section must state security runs exactly once per work unit."""
        content = SKILL_PATH.read_text()
        assert "Security review runs exactly once per work unit" in content, (
            "SKILL.md Standards section must state 'Security review runs exactly once per work unit'"
        )

    def test_standards_retry_loop_no_security(self) -> None:
        """AC-7: Standards section must state retry loop re-runs only review-supervisor."""
        content = SKILL_PATH.read_text()
        assert "never security-reviewer" in content, (
            "SKILL.md Standards section must state the retry loop "
            "re-runs only review-supervisor, never security-reviewer"
        )
