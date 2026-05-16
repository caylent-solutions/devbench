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
                f"SKILL.md must not reference individual reviewer '{agent}' -- use review-supervisor instead"
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


@pytest.mark.unit
class TestOrchestrateSkillStepZeroSweepProposals:
    """ADR-08 slice J: SKILL must have a step 0 that sweeps un-materialised proposal JSONs."""

    def test_skill_references_sweep_proposals(self) -> None:
        """The SKILL must invoke ``devbench sweep-proposals`` so un-materialised JSONs are surfaced."""
        content = SKILL_PATH.read_text()
        assert "sweep-proposals" in content, (
            "SKILL.md must invoke `devbench sweep-proposals` as step 0 so every loop iteration "
            "best-effort materialises any un-materialised proposal JSONs before validate-backlog runs."
        )

    def test_skill_sweep_proposals_appears_before_validate_backlog(self) -> None:
        """The sweep must run BEFORE validate-backlog so freshly materialised drafts are visible."""
        content = SKILL_PATH.read_text()
        sweep_pos = content.find("sweep-proposals")
        validate_pos = content.find("validate-backlog")
        assert sweep_pos >= 0, "SKILL.md must reference sweep-proposals"
        assert validate_pos >= 0, "SKILL.md must reference validate-backlog"
        assert sweep_pos < validate_pos, (
            "sweep-proposals must run BEFORE validate-backlog so any drafts created by the sweep "
            "are visible to the parse + pre-flight checks of the main loop."
        )


@pytest.mark.unit
class TestOrchestrateSkillStep1cScopeFilter:
    """AC-190-15: SKILL must have a Step 1c scope-filter instruction between validate-backlog and next."""

    def test_skill_references_scope_json(self) -> None:
        """Step 1c must mention scope.json so the orchestrator knows which file to consult."""
        content = SKILL_PATH.read_text()
        assert "scope.json" in content, "SKILL.md must reference scope.json in the Step 1c scope-filter instruction"

    def test_skill_references_no_actionable_in_scope(self) -> None:
        """Step 1c must name the NO_ACTIONABLE_IN_SCOPE sentinel so the clean-exit path is clear."""
        content = SKILL_PATH.read_text()
        assert "NO_ACTIONABLE_IN_SCOPE" in content, (
            "SKILL.md Step 1c must name the NO_ACTIONABLE_IN_SCOPE sentinel for the clean-exit path"
        )

    def test_skill_step1c_appears_between_validate_backlog_and_next(self) -> None:
        """Step 1c must appear after validate-backlog and before step 2 devbench next."""
        content = SKILL_PATH.read_text()
        validate_pos = content.find("uv run devbench validate-backlog")
        scope_pos = content.find("scope.json")
        # Step 2 starts with "2." -- find the first occurrence of step 2's next invocation
        step2_marker = "2. `uv run devbench next`"
        next_pos = content.find(step2_marker)
        assert validate_pos >= 0, "SKILL.md must reference uv run devbench validate-backlog"
        assert scope_pos >= 0, "SKILL.md must reference scope.json"
        assert next_pos >= 0, f"SKILL.md must contain step 2 marker: {step2_marker!r}"
        assert validate_pos < scope_pos < next_pos, (
            "scope.json (Step 1c) must appear AFTER validate-backlog and BEFORE step 2 `uv run devbench next` "
            "so scope is consulted between the integrity check and the claim decision"
        )

    def test_skill_step1c_instructs_clean_exit_on_exhausted_scope(self) -> None:
        """Step 1c must instruct the orchestrator to exit cleanly when no WU matches scope."""
        content = SKILL_PATH.read_text()
        assert "exit cleanly" in content, (
            "SKILL.md Step 1c must instruct the orchestrator to exit cleanly when scope is exhausted"
        )


@pytest.mark.unit
class TestOrchestrateSkillDrainCheck:
    """AC-188-4, AC-188-8, AC-188-9: SKILL must include a drain check between mark-done and loop-back."""

    def test_skill_references_drain_status(self) -> None:
        """AC-188-4: SKILL.md must invoke 'devbench drain --status' to detect a pending drain signal."""
        content = SKILL_PATH.read_text()
        assert "drain --status" in content, (
            "SKILL.md must invoke `uv run devbench drain --status` in the drain check step "
            "between mark-done (step 9) and loop-back (step 10)"
        )

    def test_skill_drain_check_appears_after_mark_done(self) -> None:
        """AC-188-4: The drain check must appear after 'mark-done' and before 'Return to step 1'."""
        content = SKILL_PATH.read_text()
        mark_done_pos = content.find("mark-done")
        drain_pos = content.find("drain --status")
        loop_back_pos = content.find("Return to step 1")
        assert mark_done_pos >= 0, "SKILL.md must reference mark-done"
        assert drain_pos >= 0, "SKILL.md must reference drain --status"
        assert loop_back_pos >= 0, "SKILL.md must reference 'Return to step 1'"
        assert mark_done_pos < drain_pos < loop_back_pos, (
            "drain --status check must appear AFTER mark-done and BEFORE 'Return to step 1' "
            "so the orchestrator checks for a pending drain before restarting the loop"
        )

    def test_skill_drain_check_logs_orchestrator_drain_comment(self) -> None:
        """AC-188-8: SKILL.md must instruct the orchestrator to log [ORCHESTRATOR_DRAIN] audit comment."""
        content = SKILL_PATH.read_text()
        assert "ORCHESTRATOR_DRAIN" in content, (
            "SKILL.md drain check step must reference [ORCHESTRATOR_DRAIN] audit comment "
            "so the orchestrator log records the cooperative drain event"
        )

    def test_skill_drain_check_exits_cleanly_on_pending(self) -> None:
        """AC-188-4: SKILL.md must instruct exit with rc=0 when drain is pending."""
        content = SKILL_PATH.read_text()
        drain_pos = content.find("drain --status")
        assert drain_pos >= 0, "SKILL.md must reference drain --status"
        # The drain check section must mention exiting cleanly
        drain_section = content[drain_pos : drain_pos + 500]
        assert "exit" in drain_section.lower(), (
            "SKILL.md drain check step must instruct the orchestrator to exit cleanly "
            "when a drain is pending"
        )
