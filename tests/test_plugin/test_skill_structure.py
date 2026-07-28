"""Unit tests for orchestrate, create-spec, and spec-to-backlog SKILL.md content correctness."""

from __future__ import annotations

from pathlib import Path

import pytest

SKILL_PATH = (
    Path(__file__).parent.parent.parent / "plugin" / "devbench-orchestrate" / "skills" / "orchestrate" / "SKILL.md"
)

CREATE_SPEC_SKILL_PATH = (
    Path(__file__).parent.parent.parent
    / "plugin-authoring"
    / "devbench-authoring"
    / "skills"
    / "create-spec"
    / "SKILL.md"
)

SPEC_TO_BACKLOG_SKILL_PATH = (
    Path(__file__).parent.parent.parent
    / "plugin-authoring"
    / "devbench-authoring"
    / "skills"
    / "spec-to-backlog"
    / "SKILL.md"
)


@pytest.mark.unit
class TestOrchestrateSkillReviewSupervisor:
    """AC-E4-F1-S1-T2-2/6/8 (post-flatten, ADR-33): the four review_team
    judges are first-level sub-agents invoked directly by the skill;
    review-supervisor is invoked separately, for read-only aggregation of
    the judges' already-persisted verdicts, and never spawns them itself.
    """

    def test_skill_invokes_all_four_review_team_judges_directly(self) -> None:
        """AC-64: SKILL.md must invoke each of the four review_team judges
        directly as first-level sub-agents, using the namespaced
        ``devbench-orchestrate:review_team:<judge>`` subagent_type form that
        ADR-33's own recorded reproduction uses (docs/adr/33 lines 196-205),
        not the retired bare ``devbench:<judge>`` prefix (which was never a
        valid plugin namespace -- ``plugin.json`` declares the plugin name
        as ``devbench-orchestrate``, so a bare ``devbench:`` prefix could
        never resolve to anything real).
        """
        content = SKILL_PATH.read_text()
        required = [
            "devbench-orchestrate:review_team:code-reviewer",
            "devbench-orchestrate:review_team:test-reviewer",
            "devbench-orchestrate:review_team:doc-reviewer",
            "devbench-orchestrate:review_team:changes-manifest",
        ]
        for agent in required:
            assert agent in content, (
                f"SKILL.md must invoke '{agent}' directly as a first-level sub-agent (AC-64); "
                "post-flatten, review-supervisor has no Agent-tool spawn capability and cannot "
                "invoke the judges on the skill's behalf."
            )

    def test_skill_references_review_supervisor_for_aggregation(self) -> None:
        """AC-E4-F1-S1-T2-6: SKILL.md must still invoke review-supervisor,
        but only for aggregating the four judges' already-persisted
        verdicts, not for spawning them.
        """
        content = SKILL_PATH.read_text()
        assert "review-supervisor" in content, (
            "SKILL.md must invoke review-supervisor to aggregate the four judges' verdicts"
        )
        assert "aggregat" in content.lower(), (
            "SKILL.md must describe review-supervisor's role as aggregation of "
            "already-persisted verdicts, not spawning the judges"
        )

    def test_skill_no_retired_bare_devbench_namespace(self) -> None:
        """The retired bare ``devbench:`` prefix (never a valid plugin
        namespace) must not reappear -- a regression test that stops being
        vacuous by asserting against the real, currently-used namespace
        form (``devbench-orchestrate:``) rather than a form that never
        existed in this file.
        """
        content = SKILL_PATH.read_text()
        forbidden = [
            "devbench:code-reviewer",
            "devbench:test-reviewer",
            "devbench:doc-reviewer",
            "devbench:changes-manifest",
        ]
        for agent in forbidden:
            assert agent not in content, (
                f"SKILL.md must not reference the retired bare-prefix form '{agent}'; "
                "the real plugin namespace is 'devbench-orchestrate:review_team:<judge>'"
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
            "SKILL.md drain check step must instruct the orchestrator to exit cleanly when a drain is pending"
        )


# ---------------------------------------------------------------------------
# create-spec/SKILL.md tests  (AC-191-2, AC-191-3)
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestCreateSpecSkillFrontmatter:
    """AC-191-2: create-spec/SKILL.md must have valid frontmatter with required fields."""

    def test_skill_file_exists(self) -> None:
        """AC-191-2: create-spec/SKILL.md must exist."""
        assert CREATE_SPEC_SKILL_PATH.exists(), f"create-spec/SKILL.md not found at {CREATE_SPEC_SKILL_PATH}"

    def test_frontmatter_name_is_create_spec(self) -> None:
        """AC-191-2: Frontmatter must declare name: create-spec."""
        content = CREATE_SPEC_SKILL_PATH.read_text()
        assert "name: create-spec" in content, "create-spec/SKILL.md frontmatter must contain 'name: create-spec'"

    def test_frontmatter_model_is_opus(self) -> None:
        """AC-191-2: Frontmatter must declare model: opus (top-tier reasoning for authoring + self-critique)."""
        content = CREATE_SPEC_SKILL_PATH.read_text()
        assert "model: opus" in content, (
            "create-spec/SKILL.md frontmatter must contain 'model: opus' "
            "(high-quality authoring + self-critique requires top-tier reasoning)"
        )

    def test_frontmatter_is_yaml_delimited(self) -> None:
        """AC-191-2: Frontmatter must be delimited by YAML front-matter markers."""
        content = CREATE_SPEC_SKILL_PATH.read_text()
        assert content.startswith("---"), "create-spec/SKILL.md must start with YAML frontmatter delimiter '---'"
        # Second --- must close the frontmatter block
        assert content.count("---") >= 2, (
            "create-spec/SKILL.md must have at least two '---' markers (open + close frontmatter)"
        )


@pytest.mark.unit
class TestCreateSpecSkillTools:
    """AC-191-2: create-spec/SKILL.md must declare tools: Read, Write, Edit, Bash."""

    def test_skill_declares_read_tool(self) -> None:
        """create-spec skill must declare the Read tool."""
        content = CREATE_SPEC_SKILL_PATH.read_text()
        assert "Read" in content, "create-spec/SKILL.md must declare the Read tool"

    def test_skill_declares_write_tool(self) -> None:
        """create-spec skill must declare the Write tool."""
        content = CREATE_SPEC_SKILL_PATH.read_text()
        assert "Write" in content, "create-spec/SKILL.md must declare the Write tool"

    def test_skill_declares_edit_tool(self) -> None:
        """create-spec skill must declare the Edit tool."""
        content = CREATE_SPEC_SKILL_PATH.read_text()
        assert "Edit" in content, "create-spec/SKILL.md must declare the Edit tool"

    def test_skill_declares_bash_tool(self) -> None:
        """create-spec skill must declare the Bash tool."""
        content = CREATE_SPEC_SKILL_PATH.read_text()
        assert "Bash" in content, "create-spec/SKILL.md must declare the Bash tool"


@pytest.mark.unit
class TestCreateSpecSkillExemplarConfig:
    """issue #221 E1-E10: Step 1 must reference a configurable exemplar path (not a hardcoded one)
    AND must enumerate the canonical 16-section skeleton as the embedded quality bar."""

    def test_skill_references_configurable_exemplar_path(self) -> None:
        """Step 1 must point at skills.exemplar_spec_path (configurable, not hardcoded)."""
        content = CREATE_SPEC_SKILL_PATH.read_text()
        assert "skills.exemplar_spec_path" in content, (
            "create-spec/SKILL.md step 1 must reference skills.exemplar_spec_path so operators "
            "can point the skill at their workspace exemplar without modifying SKILL.md"
        )
        assert "kanon-list-add-lock-features-spec.md" not in content, (
            "create-spec/SKILL.md must NOT contain the literal 'kanon-list-add-lock-features-spec.md' "
            "filename -- the skill is application-agnostic (issue #221 E1-E10)"
        )
        assert "kanon-deps-work" not in content, (
            "create-spec/SKILL.md must NOT contain the literal 'kanon-deps-work' path -- "
            "the skill is application-agnostic (issue #221 E1-E10)"
        )

    def test_skill_enumerates_16_canonical_sections(self) -> None:
        """Step 1b must enumerate the 16 canonical spec sections as the embedded quality bar."""
        content = CREATE_SPEC_SKILL_PATH.read_text()
        assert "Section 0" in content and "Section 15" in content, (
            "create-spec/SKILL.md step 1b must enumerate Sections 0 through 15 explicitly "
            "as the embedded quality bar (independent of any external exemplar)"
        )


@pytest.mark.unit
class TestCreateSpecSkillOperatorQuestions:
    """AC-191-3: Step 2 must ask structured questions covering all kanon-spec sections."""

    def test_skill_asks_about_problem_or_goals(self) -> None:
        """Step 2 must ask the operator about problem statement / goals."""
        content = CREATE_SPEC_SKILL_PATH.read_text()
        assert any(kw in content.lower() for kw in ("problem", "goal")), (
            "create-spec/SKILL.md step 2 must ask the operator about problem/goals"
        )

    def test_skill_asks_about_non_goals(self) -> None:
        """Step 2 must ask the operator about non-goals."""
        content = CREATE_SPEC_SKILL_PATH.read_text()
        assert "non-goal" in content.lower() or "out-of-scope" in content.lower(), (
            "create-spec/SKILL.md step 2 must ask the operator about non-goals / out-of-scope"
        )

    def test_skill_asks_about_functional_requirements(self) -> None:
        """Step 2 must ask about functional requirements (FR)."""
        content = CREATE_SPEC_SKILL_PATH.read_text()
        assert any(kw in content.lower() for kw in ("functional requirement", "fr", "feature")), (
            "create-spec/SKILL.md step 2 must ask the operator about functional requirements"
        )

    def test_skill_asks_about_acceptance_criteria(self) -> None:
        """Step 2 must ask about acceptance criteria."""
        content = CREATE_SPEC_SKILL_PATH.read_text()
        assert "acceptance criteria" in content.lower() or " ac" in content.lower(), (
            "create-spec/SKILL.md step 2 must ask the operator about acceptance criteria"
        )


@pytest.mark.unit
class TestCreateSpecSkillAuthoringFlow:
    """AC-191-3: Steps 3-5 must implement the one-section-at-a-time authoring flow."""

    def test_skill_mentions_section_by_section_authoring(self) -> None:
        """Step 3 must instruct authoring the spec one section at a time."""
        content = CREATE_SPEC_SKILL_PATH.read_text()
        phrases = ("section at a time", "one section", "section-by-section")
        assert any(phrase in content.lower() for phrase in phrases), (
            "create-spec/SKILL.md step 3 must instruct authoring the spec one section at a time"
        )

    def test_skill_writes_output_to_spec_dir(self) -> None:
        """AC-191-3: Output must be written to spec/<project-name>.md."""
        content = CREATE_SPEC_SKILL_PATH.read_text()
        assert "spec/" in content, "create-spec/SKILL.md must write output to spec/<project-name>.md"

    def test_skill_offers_spec_to_backlog_handoff(self) -> None:
        """End-of-skill must offer to invoke spec-to-backlog."""
        content = CREATE_SPEC_SKILL_PATH.read_text()
        assert "spec-to-backlog" in content, (
            "create-spec/SKILL.md must offer to invoke spec-to-backlog at the end of the skill"
        )


@pytest.mark.unit
class TestCreateSpecSkillIterateUntilPerfectLoop:
    """AC-191-3: Step 4 must implement the iterate-until-perfect self-critique loop."""

    def test_skill_implements_self_critique(self) -> None:
        """Step 4 must include self-critique against the create-spec rubric."""
        content = CREATE_SPEC_SKILL_PATH.read_text()
        assert any(kw in content.lower() for kw in ("self-critique", "self critique", "rubric")), (
            "create-spec/SKILL.md step 4 must implement self-critique against the create-spec rubric"
        )

    def test_skill_has_max_iterations_config(self) -> None:
        """Loop must respect max_iterations (configurable; default 5 per spec section 4.6.0)."""
        content = CREATE_SPEC_SKILL_PATH.read_text()
        assert "max_iterations" in content or "max iterations" in content.lower(), (
            "create-spec/SKILL.md must reference max_iterations for the iterate-until-perfect loop"
        )

    def test_skill_has_quality_threshold(self) -> None:
        """Loop must run until quality_threshold (zero unresolved items) is met."""
        content = CREATE_SPEC_SKILL_PATH.read_text()
        assert "zero" in content.lower() or "quality_threshold" in content or "unresolved" in content.lower(), (
            "create-spec/SKILL.md must reference the quality_threshold (zero unresolved items)"
        )

    def test_skill_blocks_on_max_iterations_without_convergence(self) -> None:
        """When max_iterations is reached without converging, skill must emit a BLOCKED audit."""
        content = CREATE_SPEC_SKILL_PATH.read_text()
        assert "BLOCKED" in content or "blocked" in content.lower(), (
            "create-spec/SKILL.md must emit a BLOCKED audit when max_iterations is reached "
            "without converging rather than silently shipping a sub-quality artefact"
        )


@pytest.mark.unit
class TestCreateSpecSkillRubricCoverage:
    """AC-191-3: Self-critique rubric must cover all 8 items from spec section 4.6.0."""

    def test_rubric_covers_16_canonical_sections(self) -> None:
        """Rubric item 1: all 16 top-level canonical sections (Sections 0-15) must be present."""
        content = CREATE_SPEC_SKILL_PATH.read_text()
        assert "16" in content or "sixteen" in content.lower(), (
            "create-spec/SKILL.md rubric must require all 16 top-level canonical sections "
            "(or explicit N/A justification)"
        )

    def test_rubric_requires_worked_examples_per_goal(self) -> None:
        """Rubric item 2: every goal must have a worked example."""
        content = CREATE_SPEC_SKILL_PATH.read_text()
        assert "worked example" in content.lower() or "worked-example" in content.lower(), (
            "create-spec/SKILL.md rubric must require a worked example for every goal"
        )

    def test_rubric_requires_error_handling_per_fr(self) -> None:
        """Rubric item 3: every FR must have explicit error-handling semantics."""
        content = CREATE_SPEC_SKILL_PATH.read_text()
        assert "error" in content.lower() and any(
            kw in content.lower() for kw in ("fr", "functional requirement", "each fr", "every fr")
        ), "create-spec/SKILL.md rubric must require explicit error-handling semantics for every FR"

    def test_rubric_requires_non_goals_stated(self) -> None:
        """Rubric item 4: every non-goal must be stated rather than implied."""
        content = CREATE_SPEC_SKILL_PATH.read_text()
        assert "non-goal" in content.lower() or "non goal" in content.lower(), (
            "create-spec/SKILL.md rubric must require that non-goals are stated rather than implied"
        )

    def test_rubric_requires_numbered_testable_acs(self) -> None:
        """Rubric item 5: acceptance criteria must be numbered and testable from the spec text alone."""
        content = CREATE_SPEC_SKILL_PATH.read_text()
        assert "testable" in content.lower() or "numbered" in content.lower(), (
            "create-spec/SKILL.md rubric must require numbered and testable acceptance criteria"
        )

    def test_rubric_requires_cross_references_to_primitives(self) -> None:
        """Rubric item 6: cross-references to existing primitives must exist for every reused component."""
        content = CREATE_SPEC_SKILL_PATH.read_text()
        lower = content.lower()
        has_cross_ref = "cross-reference" in lower or "cross reference" in lower or "primitives" in lower
        assert has_cross_ref, "create-spec/SKILL.md rubric must require cross-references to reused primitives"

    def test_rubric_requires_resolved_decisions_record(self) -> None:
        """Rubric item 7: resolved-decisions interview record must capture every design call."""
        content = CREATE_SPEC_SKILL_PATH.read_text()
        assert "resolved" in content.lower() and "decision" in content.lower(), (
            "create-spec/SKILL.md rubric must require a resolved-decisions interview record"
        )

    def test_rubric_requires_out_of_scope_section(self) -> None:
        """Rubric item 8: out-of-scope section must name every plausible adjacent ask not covered."""
        content = CREATE_SPEC_SKILL_PATH.read_text()
        assert "out-of-scope" in content.lower() or "out of scope" in content.lower(), (
            "create-spec/SKILL.md rubric must require an out-of-scope section"
        )


@pytest.mark.unit
class TestCreateSpecSkillOperatorFinalReview:
    """AC-191-3: Step 5 must include final operator review before writing the spec."""

    def test_skill_presents_spec_to_operator_before_writing(self) -> None:
        """Step 5 must present the spec to the operator for final review before writing."""
        content = CREATE_SPEC_SKILL_PATH.read_text()
        assert any(phrase in content.lower() for phrase in ("operator review", "review", "looks good", "feedback")), (
            "create-spec/SKILL.md step 5 must present the spec to the operator for final review"
        )

    def test_skill_re_enters_iterate_loop_on_operator_feedback(self) -> None:
        """Step 5 must re-enter the iterate loop when the operator provides feedback."""
        content = CREATE_SPEC_SKILL_PATH.read_text()
        # The skill should mention re-entering the loop or revising on feedback
        assert any(phrase in content.lower() for phrase in ("re-enter", "re-run", "revise", "iterate", "feedback")), (
            "create-spec/SKILL.md step 5 must re-enter the iterate loop on operator feedback"
        )

    def test_skill_target_output_size_mentioned(self) -> None:
        """Spec output target of 1000+ lines must be stated so operator knows the quality bar."""
        content = CREATE_SPEC_SKILL_PATH.read_text()
        assert "1000" in content or "1,000" in content, (
            "create-spec/SKILL.md must state the target output size (1000+ lines) "
            "so the operator understands the quality bar"
        )


@pytest.mark.unit
class TestCreateSpecSkillQualityReference:
    """AC-191-9: create-spec/SKILL.md must require emitting a [QUALITY_REFERENCE] audit comment.

    Per spec section 4.6.7 (provenance transparency): when create-spec completes, it must
    emit a [QUALITY_REFERENCE] log line naming the exact exemplar path it read so the
    audit record captures which quality reference was consulted.
    """

    def test_skill_requires_quality_reference_audit_comment(self) -> None:
        """SKILL.md must instruct the skill to emit a [QUALITY_REFERENCE] audit comment on completion."""
        content = CREATE_SPEC_SKILL_PATH.read_text()
        assert "[QUALITY_REFERENCE]" in content, (
            "create-spec/SKILL.md must instruct the skill to emit a [QUALITY_REFERENCE] "
            "audit comment naming the exemplar path read, per spec section 4.6.7 (provenance transparency)"
        )

    def test_skill_quality_reference_names_resolved_exemplar(self) -> None:
        """[QUALITY_REFERENCE] comment must reference the resolved exemplar path or the
        ``<embedded-canonical-sections>`` sentinel (issue #221 E1-E10)."""
        content = CREATE_SPEC_SKILL_PATH.read_text()
        qr_pos = content.find("[QUALITY_REFERENCE]")
        assert qr_pos >= 0, "create-spec/SKILL.md must contain [QUALITY_REFERENCE]"
        surrounding = content[qr_pos : qr_pos + 500]
        assert "exemplar" in surrounding.lower() or "embedded-canonical-sections" in surrounding, (
            "[QUALITY_REFERENCE] instruction must reference the resolved exemplar path or "
            "the <embedded-canonical-sections> sentinel so provenance is unambiguous"
        )
        assert "kanon" not in surrounding.lower(), (
            "[QUALITY_REFERENCE] instruction must NOT name 'kanon' literally -- "
            "the skill is application-agnostic (issue #221 E1-E10)"
        )

    def test_skill_quality_reference_appears_after_write_step(self) -> None:
        """[QUALITY_REFERENCE] must be emitted after the spec is written (completion, not authoring)."""
        content = CREATE_SPEC_SKILL_PATH.read_text()
        write_step_pos = content.find("## Step 6")
        qr_pos = content.find("[QUALITY_REFERENCE]")
        assert write_step_pos >= 0, "create-spec/SKILL.md must have a Step 6 (write the spec)"
        assert qr_pos >= 0, "create-spec/SKILL.md must contain [QUALITY_REFERENCE]"
        assert qr_pos > write_step_pos, (
            "[QUALITY_REFERENCE] audit comment must appear in or after Step 6 "
            "(after the spec is written, not during authoring) so the audit log records "
            "provenance at completion time"
        )


# ---------------------------------------------------------------------------
# spec-to-backlog/SKILL.md tests  (AC-191-4, AC-191-7)
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestSpecToBacklogSkillFrontmatter:
    """AC-191-2: spec-to-backlog/SKILL.md must have valid frontmatter with required fields."""

    def test_skill_file_exists(self) -> None:
        """AC-191-2: spec-to-backlog/SKILL.md must exist."""
        assert SPEC_TO_BACKLOG_SKILL_PATH.exists(), (
            f"spec-to-backlog/SKILL.md not found at {SPEC_TO_BACKLOG_SKILL_PATH}"
        )

    def test_frontmatter_name_is_spec_to_backlog(self) -> None:
        """AC-191-2: Frontmatter must declare name: spec-to-backlog."""
        content = SPEC_TO_BACKLOG_SKILL_PATH.read_text()
        assert "name: spec-to-backlog" in content, (
            "spec-to-backlog/SKILL.md frontmatter must contain 'name: spec-to-backlog'"
        )

    def test_frontmatter_model_is_opus(self) -> None:
        """AC-191-2: Frontmatter must declare model: opus (decomposition reasoning + self-critique)."""
        content = SPEC_TO_BACKLOG_SKILL_PATH.read_text()
        assert "model: opus" in content, (
            "spec-to-backlog/SKILL.md frontmatter must contain 'model: opus' "
            "(decomposition reasoning + self-critique requires top-tier reasoning)"
        )

    def test_frontmatter_is_yaml_delimited(self) -> None:
        """AC-191-2: Frontmatter must be delimited by YAML front-matter markers."""
        content = SPEC_TO_BACKLOG_SKILL_PATH.read_text()
        assert content.startswith("---"), "spec-to-backlog/SKILL.md must start with YAML frontmatter delimiter '---'"
        assert content.count("---") >= 2, (
            "spec-to-backlog/SKILL.md must have at least two '---' markers (open + close frontmatter)"
        )


@pytest.mark.unit
class TestSpecToBacklogSkillTools:
    """AC-191-2: spec-to-backlog/SKILL.md must declare tools: Read, Write, Edit, Bash."""

    def test_skill_declares_read_tool(self) -> None:
        """spec-to-backlog skill must declare the Read tool."""
        content = SPEC_TO_BACKLOG_SKILL_PATH.read_text()
        assert "Read" in content, "spec-to-backlog/SKILL.md must declare the Read tool"

    def test_skill_declares_write_tool(self) -> None:
        """spec-to-backlog skill must declare the Write tool."""
        content = SPEC_TO_BACKLOG_SKILL_PATH.read_text()
        assert "Write" in content, "spec-to-backlog/SKILL.md must declare the Write tool"

    def test_skill_declares_edit_tool(self) -> None:
        """spec-to-backlog skill must declare the Edit tool."""
        content = SPEC_TO_BACKLOG_SKILL_PATH.read_text()
        assert "Edit" in content, "spec-to-backlog/SKILL.md must declare the Edit tool"

    def test_skill_declares_bash_tool(self) -> None:
        """spec-to-backlog skill must declare the Bash tool."""
        content = SPEC_TO_BACKLOG_SKILL_PATH.read_text()
        assert "Bash" in content, "spec-to-backlog/SKILL.md must declare the Bash tool"


@pytest.mark.unit
class TestSpecToBacklogSkillKanonExemplarStep:
    """AC-191-4: Skill must read kanon BACKLOG.md and a representative task file to internalise quality bar."""

    def test_skill_references_backlog_output(self) -> None:
        """Step 1 must reference BACKLOG.md as the artefact the skill produces and (optionally)
        consults as a configured exemplar."""
        content = SPEC_TO_BACKLOG_SKILL_PATH.read_text()
        assert "BACKLOG.md" in content, "spec-to-backlog/SKILL.md must reference BACKLOG.md"

    def test_skill_reads_representative_task_file(self) -> None:
        """Step 1 must reference task-file authoring as the per-task quality bar."""
        content = SPEC_TO_BACKLOG_SKILL_PATH.read_text()
        assert "task" in content.lower(), (
            "spec-to-backlog/SKILL.md must reference per-task authoring to internalise the per-task quality bar"
        )

    def test_skill_references_configurable_exemplar_path(self) -> None:
        """Step 1 must reference the configurable exemplar path via skills.exemplar_backlog_path
        (issue #221 E1-E10: skill is application-agnostic; no hardcoded workspace path)."""
        content = SPEC_TO_BACKLOG_SKILL_PATH.read_text()
        assert "skills.exemplar_backlog_path" in content, (
            "spec-to-backlog/SKILL.md must reference skills.exemplar_backlog_path so operators "
            "can point the skill at their workspace exemplar without modifying the SKILL.md"
        )
        assert "kanon-deps-work" not in content, (
            "spec-to-backlog/SKILL.md must NOT contain the literal 'kanon-deps-work' path -- "
            "the skill is application-agnostic (issue #221 E1-E10)"
        )

    def test_skill_references_canonical_15_section_skeleton(self) -> None:
        """Step 1b must enumerate the 15 canonical task-file sections (the embedded quality bar
        that floors the skill when no workspace exemplar is configured)."""
        content = SPEC_TO_BACKLOG_SKILL_PATH.read_text()
        assert "15 canonical" in content or "15-section" in content or "15 canonical sections" in content, (
            "spec-to-backlog/SKILL.md must enumerate the 15 canonical task-file sections "
            "as the embedded quality bar (independent of any external exemplar)"
        )


@pytest.mark.unit
class TestSpecToBacklogSkillDecompositionHierarchy:
    """AC-191-4: Skill must decompose spec into Epic -> Feature -> Story -> Task hierarchy."""

    def test_skill_mentions_epic_level(self) -> None:
        """Skill must decompose at the Epic level."""
        content = SPEC_TO_BACKLOG_SKILL_PATH.read_text()
        assert "Epic" in content, "spec-to-backlog/SKILL.md must instruct decomposing specs at the Epic level"

    def test_skill_mentions_feature_level(self) -> None:
        """Skill must decompose at the Feature level."""
        content = SPEC_TO_BACKLOG_SKILL_PATH.read_text()
        assert "Feature" in content, "spec-to-backlog/SKILL.md must instruct decomposing specs at the Feature level"

    def test_skill_mentions_story_level(self) -> None:
        """Skill must decompose at the Story level."""
        content = SPEC_TO_BACKLOG_SKILL_PATH.read_text()
        assert "Story" in content, "spec-to-backlog/SKILL.md must instruct decomposing specs at the Story level"

    def test_skill_mentions_task_level(self) -> None:
        """Skill must decompose at the Task level."""
        content = SPEC_TO_BACKLOG_SKILL_PATH.read_text()
        assert "Task" in content, "spec-to-backlog/SKILL.md must instruct decomposing specs at the Task level"

    def test_skill_four_level_hierarchy_expressed(self) -> None:
        """Hierarchy chain Epic -> Feature -> Story -> Task must be explicitly stated."""
        content = SPEC_TO_BACKLOG_SKILL_PATH.read_text()
        # The spec mandates 4-level hierarchy -- verify all 4 are present together
        assert "Epic" in content and "Feature" in content and "Story" in content and "Task" in content, (
            "spec-to-backlog/SKILL.md must express the full 4-level hierarchy "
            "(Epic -> Feature -> Story -> Task) with no skipped levels"
        )


@pytest.mark.unit
class TestSpecToBacklogSkillKanonCanonicalSections:
    """AC-191-4: Each task file must have all kanon-canonical sections."""

    def test_skill_mentions_8_canonical_sections(self) -> None:
        """Skill must specify that each task file includes the 8 canonical work-unit sections."""
        content = SPEC_TO_BACKLOG_SKILL_PATH.read_text()
        # Spec section 4.6.0 names 8 canonical sections (previously said "22 sections" but
        # the per-task rubric enumerates 8 top-level ## headings)
        required_sections = [
            "Status",
            "Description",
            "Dependencies",
            "Acceptance Criteria",
            "Changes Manifest",
            "Definition of Done",
            "TDD Cycle Log",
            "Comments",
        ]
        for section in required_sections:
            assert section in content, (
                f"spec-to-backlog/SKILL.md must instruct writing '{section}' section "
                "in every task file to match kanon task-file depth"
            )

    def test_skill_requires_approach_section(self) -> None:
        """Each task file must include a task-specific Approach section (not generic boilerplate)."""
        content = SPEC_TO_BACKLOG_SKILL_PATH.read_text()
        assert "Approach" in content, (
            "spec-to-backlog/SKILL.md must require an Approach section in each task file "
            "with task-specific numbered TDD steps (not generic boilerplate)"
        )

    def test_skill_requires_definition_of_ready_section(self) -> None:
        """Each task file must include a Definition of Ready section with task-tailored items."""
        content = SPEC_TO_BACKLOG_SKILL_PATH.read_text()
        assert "Definition of Ready" in content, (
            "spec-to-backlog/SKILL.md must require a 'Definition of Ready' section "
            "in each task file with task-tailored checklist items"
        )

    def test_skill_requires_depends_on_this_table(self) -> None:
        """Each task file must include a 'Depends On This' reverse-dependency table."""
        content = SPEC_TO_BACKLOG_SKILL_PATH.read_text()
        assert "Depends On This" in content, (
            "spec-to-backlog/SKILL.md must require a 'Depends On This' reverse-dependency table "
            "in each task file (real WU IDs -- no placeholders)"
        )

    def test_skill_requires_changes_manifest_with_concrete_paths(self) -> None:
        """Changes Manifest must list concrete file paths with add/modify/delete annotations."""
        content = SPEC_TO_BACKLOG_SKILL_PATH.read_text()
        assert "Changes Manifest" in content, (
            "spec-to-backlog/SKILL.md must require a concrete Changes Manifest in each task file"
        )


@pytest.mark.unit
class TestSpecToBacklogSkillIterateUntilPerfect:
    """AC-191-4: Skill must implement iterate-until-perfect loop at three granularities."""

    def test_skill_iterates_at_epic_granularity(self) -> None:
        """Loop granularity 1: per-Epic decomposition critique."""
        content = SPEC_TO_BACKLOG_SKILL_PATH.read_text()
        assert "Epic" in content and any(
            kw in content.lower() for kw in ("decomposition", "critique", "iterate", "self-critique")
        ), (
            "spec-to-backlog/SKILL.md must implement per-Epic decomposition critique "
            "as the first iterate-until-perfect granularity"
        )

    def test_skill_iterates_at_task_granularity(self) -> None:
        """Loop granularity 2: per-Task authoring self-critique."""
        content = SPEC_TO_BACKLOG_SKILL_PATH.read_text()
        assert any(kw in content.lower() for kw in ("per task", "per-task", "each task", "every task")), (
            "spec-to-backlog/SKILL.md must implement per-Task authoring critique "
            "as the second iterate-until-perfect granularity"
        )

    def test_skill_runs_validate_backlog_after_every_task(self) -> None:
        """Loop granularity 3: whole-backlog post-pass via validate-backlog after every Task."""
        content = SPEC_TO_BACKLOG_SKILL_PATH.read_text()
        assert "validate-backlog" in content, (
            "spec-to-backlog/SKILL.md must run 'devbench validate-backlog' as the third "
            "iterate-until-perfect granularity (whole-backlog post-pass after every Task)"
        )

    def test_skill_regenerates_offending_task_on_validate_error(self) -> None:
        """On validate-backlog error, skill must regenerate the offending task and re-run validate."""
        content = SPEC_TO_BACKLOG_SKILL_PATH.read_text()
        assert any(
            kw in content.lower() for kw in ("regenerat", "re-run", "re-validate", "offending", "fix", "error")
        ), (
            "spec-to-backlog/SKILL.md must instruct regenerating the offending task on "
            "validate-backlog error and re-running validate (whole-backlog post-pass loop)"
        )

    def test_skill_has_max_iterations_config(self) -> None:
        """Loop must respect max_iterations (configurable; default 5 per spec section 4.6.0)."""
        content = SPEC_TO_BACKLOG_SKILL_PATH.read_text()
        assert "max_iterations" in content or "max iterations" in content.lower(), (
            "spec-to-backlog/SKILL.md must reference max_iterations for the iterate-until-perfect loop"
        )


@pytest.mark.unit
class TestSpecToBacklogSkillSuccessConditions:
    """AC-191-4: Skill exits only when all three quality gates pass simultaneously."""

    def test_skill_exits_only_when_validate_backlog_rc_zero(self) -> None:
        """Skill must exit only when validate-backlog returns rc=0."""
        content = SPEC_TO_BACKLOG_SKILL_PATH.read_text()
        assert "validate-backlog" in content, (
            "spec-to-backlog/SKILL.md must require validate-backlog rc=0 as exit condition"
        )

    def test_skill_exits_only_when_per_task_rubric_passes(self) -> None:
        """Skill must exit only when every Task passes the per-task rubric."""
        content = SPEC_TO_BACKLOG_SKILL_PATH.read_text()
        assert "rubric" in content.lower(), (
            "spec-to-backlog/SKILL.md must require every Task to pass the per-task rubric as an exit condition"
        )

    def test_skill_exits_only_when_backlog_index_count_matches(self) -> None:
        """Skill must verify BACKLOG.md Status Summary count matches Full Work Unit Index count."""
        content = SPEC_TO_BACKLOG_SKILL_PATH.read_text()
        assert "BACKLOG.md" in content and any(
            kw in content.lower() for kw in ("status summary", "count", "total", "index")
        ), (
            "spec-to-backlog/SKILL.md must verify BACKLOG.md Status Summary total "
            "matches the Full Work Unit Index count as the final exit gate"
        )

    def test_step7_re_enters_loop_bounded_by_max_iterations(self) -> None:
        """Step 7 must bound the re-entry iterate loop with max_iterations.

        Per spec section 4.6.3 and AC-191-4: 'Failure on any: re-enter the iterate loop
        until pass or max_iterations exhausted.' The final validation step must name
        max_iterations as the termination bound -- not just say 'return to step N' without
        stating when iteration must stop.
        """
        content = SPEC_TO_BACKLOG_SKILL_PATH.read_text()
        step7_start = content.find("## Step 7")
        step8_start = content.find("## Step 8")
        assert step7_start != -1, "spec-to-backlog/SKILL.md must contain '## Step 7'"
        assert step8_start != -1, "spec-to-backlog/SKILL.md must contain '## Step 8'"
        step7_content = content[step7_start:step8_start]
        assert "max_iterations" in step7_content, (
            "spec-to-backlog/SKILL.md Step 7 must state that re-iteration is bounded by "
            "max_iterations (per spec section 4.6.3: 'until pass or max_iterations exhausted') -- "
            "without this bound the skill has no termination condition for final validation failures"
        )

    def test_step7_emits_blocked_on_max_iterations_exhausted(self) -> None:
        """Step 7 must emit a [BLOCKED] audit comment when max_iterations is reached.

        Per spec section 4.6.3 and the iterate-until-perfect loop contract: if max_iterations
        is exhausted without all three exit conditions passing, the skill must emit a [BLOCKED]
        comment listing the unresolved items -- NOT silently exit or ship a sub-quality backlog.
        """
        content = SPEC_TO_BACKLOG_SKILL_PATH.read_text()
        step7_start = content.find("## Step 7")
        step8_start = content.find("## Step 8")
        assert step7_start != -1, "spec-to-backlog/SKILL.md must contain '## Step 7'"
        assert step8_start != -1, "spec-to-backlog/SKILL.md must contain '## Step 8'"
        step7_content = content[step7_start:step8_start]
        assert "[BLOCKED]" in step7_content, (
            "spec-to-backlog/SKILL.md Step 7 must instruct the skill to emit a [BLOCKED] audit "
            "comment when max_iterations is exhausted without all three exit conditions passing -- "
            "silently exiting with a sub-quality backlog violates the fail-fast / no-silent-failure "
            "requirements (spec section 4.6.3, CLAUDE.md Critical Rules 1 and 2)"
        )


@pytest.mark.unit
class TestSpecToBacklogSkillDefaultDraftStatus:
    """AC-191-4: New WUs must default to 'draft' status; overridable via config.

    spec-to-backlog reads RUNTIME_CONFIG.backlog.default_status_for_new_work_units and
    writes that value (default 'draft' for new workspaces, 'in-queue' for legacy).
    Tests verify both the draft path and the in-queue override path.

    Per spec section 4.6.3 and issue #191.
    """

    def test_skill_defaults_new_wus_to_draft_status(self) -> None:
        """Skill must set new work units to 'draft' status by default."""
        content = SPEC_TO_BACKLOG_SKILL_PATH.read_text()
        assert "draft" in content.lower(), (
            "spec-to-backlog/SKILL.md must default new work units to 'draft' status "
            "(per spec section 4.6.3 -- depends on E1 draft-status feature)"
        )

    def test_skill_writes_status_draft_header_for_default_path(self) -> None:
        """Skill must explicitly instruct writing '## Status: draft' as the status header.

        The generated task file must open with '## Status: draft' (the exact markdown heading)
        when the config key is absent or set to 'draft'.
        """
        content = SPEC_TO_BACKLOG_SKILL_PATH.read_text()
        assert "## Status: draft" in content, (
            "spec-to-backlog/SKILL.md must explicitly instruct writing '## Status: draft' "
            "as the task file status header for the default (draft) path -- "
            "per spec section 4.6.3 and AC-191-4"
        )

    def test_skill_mentions_config_override_for_default_status(self) -> None:
        """Default status must be overridable via backlog.default_status_for_new_work_units config."""
        content = SPEC_TO_BACKLOG_SKILL_PATH.read_text()
        assert "default_status_for_new_work_units" in content, (
            "spec-to-backlog/SKILL.md must name the config key 'default_status_for_new_work_units' "
            "that controls the default status for new work units in devbench.yaml"
        )

    def test_skill_covers_in_queue_override_path(self) -> None:
        """Skill must explicitly document the in-queue override path for legacy workspaces.

        When backlog.default_status_for_new_work_units is set to 'in-queue', every generated
        task file must open with '## Status: in-queue'. The SKILL.md must instruct this
        alternate path so the legacy (pre-draft-status) workspace behaviour is preserved.
        """
        content = SPEC_TO_BACKLOG_SKILL_PATH.read_text()
        assert "## Status: in-queue" in content, (
            "spec-to-backlog/SKILL.md must explicitly instruct writing '## Status: in-queue' "
            "when backlog.default_status_for_new_work_units is set to 'in-queue' -- "
            "this is the legacy workspace override path (per spec section 4.6.3 and AC-191-4)"
        )

    def test_skill_names_config_file_path_for_status_override(self) -> None:
        """Config key for status override must name the devbench.yaml config file path."""
        content = SPEC_TO_BACKLOG_SKILL_PATH.read_text()
        assert "backlog/config/devbench.yaml" in content or "devbench.yaml" in content, (
            "spec-to-backlog/SKILL.md must reference 'devbench.yaml' as the config file "
            "that holds 'default_status_for_new_work_units' so operators know where to set it"
        )

    def test_skill_draft_is_default_when_config_key_absent(self) -> None:
        """Skill must state that 'draft' is the default when the config key is absent.

        Per spec section 4.6.3: 'default is draft when that key is absent'. The SKILL.md
        must explicitly document this fallback-free default so the agent knows to write
        '## Status: draft' without requiring the operator to configure anything.
        """
        content = SPEC_TO_BACKLOG_SKILL_PATH.read_text()
        assert "absent" in content or "not set" in content.lower() or "when that key" in content, (
            "spec-to-backlog/SKILL.md must state that 'draft' is the default status "
            "when the config key 'default_status_for_new_work_units' is absent from devbench.yaml -- "
            "per spec section 4.6.3"
        )


@pytest.mark.unit
class TestSpecToBacklogSkillOutputContract:
    """AC-191-4: Skill must produce BACKLOG.md and work-unit .md files under backlog/."""

    def test_skill_produces_backlog_md(self) -> None:
        """Skill output must include BACKLOG.md."""
        content = SPEC_TO_BACKLOG_SKILL_PATH.read_text()
        assert "BACKLOG.md" in content, (
            "spec-to-backlog/SKILL.md must produce BACKLOG.md as part of its output contract"
        )

    def test_skill_produces_work_unit_files_under_backlog(self) -> None:
        """Skill output must include work-unit .md files written under backlog/."""
        content = SPEC_TO_BACKLOG_SKILL_PATH.read_text()
        assert "backlog/" in content, (
            "spec-to-backlog/SKILL.md must write work-unit .md files under the backlog/ directory"
        )

    def test_skill_mentions_ac_ties_to_spec(self) -> None:
        """Acceptance criteria in each task file must tie back to the input spec."""
        content = SPEC_TO_BACKLOG_SKILL_PATH.read_text()
        assert any(kw in content.lower() for kw in ("spec section", "ac tie", "ties to spec", "references the spec")), (
            "spec-to-backlog/SKILL.md must instruct that task ACs tie back to spec sections "
            "so every AC has a spec justification"
        )


@pytest.mark.unit
class TestSpecToBacklogSkillForbiddenPatterns:
    """AC-191-4: Skill must instruct avoiding forbidden subsection patterns from spec section 4.6.0."""

    def test_skill_prohibits_multiple_error_handling_subsections(self) -> None:
        """Skill must prohibit multiple Error Handling Contract subsections per task."""
        content = SPEC_TO_BACKLOG_SKILL_PATH.read_text()
        assert "Error Handling Contract" in content, (
            "spec-to-backlog/SKILL.md must reference 'Error Handling Contract' "
            "and instruct using ONE subsection per task (general + task-specific under same heading)"
        )

    def test_skill_prohibits_placeholder_deps(self) -> None:
        """Skill must prohibit placeholder text in Depends On This table."""
        content = SPEC_TO_BACKLOG_SKILL_PATH.read_text()
        # Spec says: no "filled in by validate-backlog" placeholders
        assert any(
            kw in content.lower() for kw in ("placeholder", "real wu id", "real ids", "no placeholder", "filled in at")
        ), (
            "spec-to-backlog/SKILL.md must prohibit placeholder text in Depends On This tables "
            "and require real WU IDs resolved at generation time"
        )

    def test_skill_prohibits_generic_approach_templates(self) -> None:
        """Skill must prohibit generic 11-step Approach boilerplate."""
        content = SPEC_TO_BACKLOG_SKILL_PATH.read_text()
        assert "Approach" in content and any(
            kw in content.lower() for kw in ("specific", "task-specific", "generic", "boilerplate", "file", "line")
        ), (
            "spec-to-backlog/SKILL.md must require task-specific Approach steps "
            "(not generic boilerplate templates) with file/line citations"
        )


@pytest.mark.unit
class TestSpecToBacklogSkillQualityReference:
    """AC-191-9: spec-to-backlog/SKILL.md must require emitting a [QUALITY_REFERENCE] audit comment.

    Per spec section 4.6.7 (provenance transparency): when spec-to-backlog completes, it must
    emit a [QUALITY_REFERENCE] log line naming the exact exemplar path it read so the
    audit record captures which quality reference was consulted.
    """

    def test_skill_requires_quality_reference_audit_comment(self) -> None:
        """SKILL.md must instruct the skill to emit a [QUALITY_REFERENCE] audit comment on completion."""
        content = SPEC_TO_BACKLOG_SKILL_PATH.read_text()
        assert "[QUALITY_REFERENCE]" in content, (
            "spec-to-backlog/SKILL.md must instruct the skill to emit a [QUALITY_REFERENCE] "
            "audit comment naming the exemplar path read, per spec section 4.6.7 (provenance transparency)"
        )

    def test_skill_quality_reference_names_resolved_exemplar(self) -> None:
        """[QUALITY_REFERENCE] comment must reference the resolved exemplar path or the
        ``<embedded-canonical-sections>`` sentinel (issue #221 E1-E10 -- skill is
        application-agnostic so the comment cannot reference any hardcoded workspace path)."""
        content = SPEC_TO_BACKLOG_SKILL_PATH.read_text()
        qr_pos = content.find("[QUALITY_REFERENCE]")
        assert qr_pos >= 0, "spec-to-backlog/SKILL.md must contain [QUALITY_REFERENCE]"
        surrounding = content[qr_pos : qr_pos + 500]
        assert "exemplar" in surrounding.lower() or "embedded-canonical-sections" in surrounding, (
            "[QUALITY_REFERENCE] instruction must reference the resolved exemplar path or "
            "the <embedded-canonical-sections> sentinel so provenance is unambiguous"
        )
        assert "kanon-deps-work" not in surrounding, (
            "[QUALITY_REFERENCE] instruction must NOT name 'kanon-deps-work' literally -- "
            "the skill is application-agnostic (issue #221 E1-E10)"
        )


@pytest.mark.unit
class TestSpecToBacklogSkillSelfCritiqueRubric:
    """AC-191-4: Self-critique rubric must cover all items from spec section 4.6.0."""

    def test_rubric_requires_every_fr_has_epic(self) -> None:
        """Rubric: every spec FR must have at least one Epic (or explicit N/A)."""
        content = SPEC_TO_BACKLOG_SKILL_PATH.read_text()
        assert "Epic" in content and any(
            kw in content.lower() for kw in ("fr", "functional requirement", "spec fr", "every fr")
        ), "spec-to-backlog/SKILL.md rubric must require every spec FR to have at least one Epic"

    def test_rubric_requires_no_skipped_hierarchy_levels(self) -> None:
        """Rubric: hierarchy must be exactly Epic -> Feature -> Story -> Task with no skipped levels."""
        content = SPEC_TO_BACKLOG_SKILL_PATH.read_text()
        # Verify all four levels appear together in a rubric-like context
        assert all(level in content for level in ["Epic", "Feature", "Story", "Task"]), (
            "spec-to-backlog/SKILL.md rubric must require Epic->Feature->Story->Task with no skipped levels"
        )

    def test_rubric_requires_dag_dependency_graph(self) -> None:
        """Rubric: dependency graph must be a DAG (validated by validate-backlog)."""
        content = SPEC_TO_BACKLOG_SKILL_PATH.read_text()
        assert "DAG" in content or "acyclic" in content.lower() or "dependency" in content.lower(), (
            "spec-to-backlog/SKILL.md rubric must require the dependency graph to be a DAG "
            "(validated by validate-backlog)"
        )


# ---------------------------------------------------------------------------
# bootstrap-environment/SKILL.md tests  (AC-191-5)
# ---------------------------------------------------------------------------

BOOTSTRAP_ENV_SKILL_PATH = (
    Path(__file__).parent.parent.parent
    / "plugin-authoring"
    / "devbench-authoring"
    / "skills"
    / "bootstrap-environment"
    / "SKILL.md"
)


@pytest.mark.unit
class TestBootstrapEnvironmentSkillFrontmatter:
    """AC-191-5: bootstrap-environment/SKILL.md must have valid frontmatter."""

    def test_skill_file_exists(self) -> None:
        """AC-191-5: bootstrap-environment/SKILL.md must exist."""
        assert BOOTSTRAP_ENV_SKILL_PATH.exists(), (
            f"bootstrap-environment/SKILL.md not found at {BOOTSTRAP_ENV_SKILL_PATH}"
        )

    def test_frontmatter_name_is_bootstrap_environment(self) -> None:
        """AC-191-5: Frontmatter must declare name: bootstrap-environment."""
        content = BOOTSTRAP_ENV_SKILL_PATH.read_text()
        assert "name: bootstrap-environment" in content, (
            "bootstrap-environment/SKILL.md frontmatter must contain 'name: bootstrap-environment'"
        )

    def test_frontmatter_model_is_sonnet(self) -> None:
        """AC-191-5: Frontmatter must declare model: sonnet per spec section 4.6.4."""
        content = BOOTSTRAP_ENV_SKILL_PATH.read_text()
        assert "model: sonnet" in content, (
            "bootstrap-environment/SKILL.md frontmatter must contain 'model: sonnet' "
            "(environment setup does not require top-tier reasoning)"
        )

    def test_frontmatter_is_yaml_delimited(self) -> None:
        """AC-191-5: Frontmatter must be delimited by YAML front-matter markers."""
        content = BOOTSTRAP_ENV_SKILL_PATH.read_text()
        assert content.startswith("---"), (
            "bootstrap-environment/SKILL.md must start with YAML frontmatter delimiter '---'"
        )
        assert content.count("---") >= 2, (
            "bootstrap-environment/SKILL.md must have at least two '---' markers (open + close frontmatter)"
        )


@pytest.mark.unit
class TestBootstrapEnvironmentSkillTools:
    """AC-191-5: bootstrap-environment/SKILL.md must declare tools: Bash, Read, Edit."""

    def test_skill_declares_bash_tool(self) -> None:
        """bootstrap-environment skill must declare the Bash tool."""
        content = BOOTSTRAP_ENV_SKILL_PATH.read_text()
        assert "Bash" in content, "bootstrap-environment/SKILL.md must declare the Bash tool"

    def test_skill_declares_read_tool(self) -> None:
        """bootstrap-environment skill must declare the Read tool."""
        content = BOOTSTRAP_ENV_SKILL_PATH.read_text()
        assert "Read" in content, "bootstrap-environment/SKILL.md must declare the Read tool"

    def test_skill_declares_edit_tool(self) -> None:
        """bootstrap-environment skill must declare the Edit tool."""
        content = BOOTSTRAP_ENV_SKILL_PATH.read_text()
        assert "Edit" in content, "bootstrap-environment/SKILL.md must declare the Edit tool"


@pytest.mark.unit
class TestBootstrapEnvironmentSkillRepoListStep:
    """AC-191-5: Skill must read target-repo list from devbench.yaml or ask interactively."""

    def test_skill_reads_repos_from_devbench_yaml(self) -> None:
        """Skill must read the repos: section from devbench.yaml per spec section 4.6.4."""
        content = BOOTSTRAP_ENV_SKILL_PATH.read_text()
        assert "devbench.yaml" in content, (
            "bootstrap-environment/SKILL.md must read target-repo list from devbench.yaml"
        )

    def test_skill_reads_repos_section(self) -> None:
        """Skill must reference the repos: key in devbench.yaml."""
        content = BOOTSTRAP_ENV_SKILL_PATH.read_text()
        assert "repos" in content, "bootstrap-environment/SKILL.md must reference the 'repos:' section in devbench.yaml"

    def test_skill_asks_interactively_when_yaml_missing(self) -> None:
        """Skill must ask interactively when devbench.yaml repos section is absent."""
        content = BOOTSTRAP_ENV_SKILL_PATH.read_text()
        assert any(kw in content.lower() for kw in ("interactively", "ask", "prompt", "operator")), (
            "bootstrap-environment/SKILL.md must ask interactively when repos list is not available in devbench.yaml"
        )


@pytest.mark.unit
class TestBootstrapEnvironmentSkillPerRepoSteps:
    """AC-191-5: For each repo, skill must: clone, install asdf toolchain, run make validate."""

    def test_skill_clones_to_checkout_directory(self) -> None:
        """Skill must clone each repo to checkout_directory per spec section 4.6.4."""
        content = BOOTSTRAP_ENV_SKILL_PATH.read_text()
        assert "checkout_directory" in content or "clone" in content.lower(), (
            "bootstrap-environment/SKILL.md must clone repos to checkout_directory"
        )

    def test_skill_detects_asdf_tool_versions(self) -> None:
        """Skill must detect + install asdf toolchain from .tool-versions per spec section 4.6.4."""
        content = BOOTSTRAP_ENV_SKILL_PATH.read_text()
        assert ".tool-versions" in content, (
            "bootstrap-environment/SKILL.md must detect .tool-versions file for asdf toolchain"
        )

    def test_skill_installs_asdf_toolchain(self) -> None:
        """Skill must install asdf toolchain versions found in .tool-versions."""
        content = BOOTSTRAP_ENV_SKILL_PATH.read_text()
        assert "asdf" in content, "bootstrap-environment/SKILL.md must install the asdf toolchain"

    def test_skill_runs_make_validate_baseline(self) -> None:
        """Skill must run make validate as baseline per spec section 4.6.4."""
        content = BOOTSTRAP_ENV_SKILL_PATH.read_text()
        assert "make validate" in content, (
            "bootstrap-environment/SKILL.md must run 'make validate' as the baseline check"
        )

    def test_skill_reports_per_repo_progress(self) -> None:
        """Skill must report per-repo progress per spec section 4.6.4."""
        content = BOOTSTRAP_ENV_SKILL_PATH.read_text()
        assert any(kw in content.lower() for kw in ("progress", "report", "status")), (
            "bootstrap-environment/SKILL.md must report per-repo progress"
        )


@pytest.mark.unit
class TestBootstrapEnvironmentSkillSelfVerifyLoop:
    """AC-191-5: After every step, skill must run sanity check and retry once on failure."""

    def test_skill_verifies_clone_present(self) -> None:
        """Self-verify loop: clone present check must be mentioned."""
        content = BOOTSTRAP_ENV_SKILL_PATH.read_text()
        assert "clone" in content.lower(), "bootstrap-environment/SKILL.md self-verify loop must check clone is present"

    def test_skill_verifies_tools_installed(self) -> None:
        """Self-verify loop: tools installed check must be mentioned."""
        content = BOOTSTRAP_ENV_SKILL_PATH.read_text()
        assert any(kw in content.lower() for kw in ("tools installed", "install", "asdf")), (
            "bootstrap-environment/SKILL.md self-verify loop must verify tools are installed"
        )

    def test_skill_verifies_make_validate_green(self) -> None:
        """Self-verify loop: make validate green check must be mentioned."""
        content = BOOTSTRAP_ENV_SKILL_PATH.read_text()
        assert "make validate" in content, (
            "bootstrap-environment/SKILL.md self-verify loop must verify make validate is green"
        )

    def test_skill_retries_once_on_failure(self) -> None:
        """Self-verify loop: on failure, log + retry once per spec section 4.6.4."""
        content = BOOTSTRAP_ENV_SKILL_PATH.read_text()
        assert "retry" in content.lower() or "retry once" in content.lower(), (
            "bootstrap-environment/SKILL.md must log + retry once on self-verify failure"
        )

    def test_skill_escalates_on_persistent_failure(self) -> None:
        """On persistent failure after retry, skill must escalate with clear diagnostic."""
        content = BOOTSTRAP_ENV_SKILL_PATH.read_text()
        assert any(kw in content.lower() for kw in ("escalate", "diagnostic", "persistent")), (
            "bootstrap-environment/SKILL.md must escalate to the operator with a diagnostic "
            "on persistent failure after retry"
        )

    def test_skill_pauses_on_failure_with_diagnostic(self) -> None:
        """Skill must pause on failure with diagnostic + suggested fix per spec section 4.6.4."""
        content = BOOTSTRAP_ENV_SKILL_PATH.read_text()
        assert any(kw in content.lower() for kw in ("pause", "diagnostic", "suggested fix", "suggest")), (
            "bootstrap-environment/SKILL.md must pause on failure with a diagnostic and suggested fix for the operator"
        )


# ---------------------------------------------------------------------------
# configure-devbench/SKILL.md tests  (AC-191-6)
# ---------------------------------------------------------------------------

CONFIGURE_DEVBENCH_SKILL_PATH = (
    Path(__file__).parent.parent.parent
    / "plugin-authoring"
    / "devbench-authoring"
    / "skills"
    / "configure-devbench"
    / "SKILL.md"
)


@pytest.mark.unit
class TestConfigureDevbenchSkillFrontmatter:
    """AC-191-6: configure-devbench/SKILL.md must have valid frontmatter with required fields."""

    def test_skill_file_exists(self) -> None:
        """AC-191-6: configure-devbench/SKILL.md must exist."""
        assert CONFIGURE_DEVBENCH_SKILL_PATH.exists(), (
            f"configure-devbench/SKILL.md not found at {CONFIGURE_DEVBENCH_SKILL_PATH}"
        )

    def test_frontmatter_name_is_configure_devbench(self) -> None:
        """AC-191-6: Frontmatter must declare name: configure-devbench."""
        content = CONFIGURE_DEVBENCH_SKILL_PATH.read_text()
        assert "name: configure-devbench" in content, (
            "configure-devbench/SKILL.md frontmatter must contain 'name: configure-devbench'"
        )

    def test_frontmatter_model_is_sonnet(self) -> None:
        """AC-191-6: Frontmatter must declare model: sonnet per spec section 4.6.5."""
        content = CONFIGURE_DEVBENCH_SKILL_PATH.read_text()
        assert "model: sonnet" in content, "configure-devbench/SKILL.md frontmatter must contain 'model: sonnet'"

    def test_frontmatter_is_yaml_delimited(self) -> None:
        """AC-191-6: Frontmatter must be delimited by YAML front-matter markers."""
        content = CONFIGURE_DEVBENCH_SKILL_PATH.read_text()
        assert content.startswith("---"), "configure-devbench/SKILL.md must start with YAML frontmatter delimiter '---'"
        assert content.count("---") >= 2, (
            "configure-devbench/SKILL.md must have at least two '---' markers (open + close frontmatter)"
        )


@pytest.mark.unit
class TestConfigureDevbenchSkillTools:
    """AC-191-6: configure-devbench/SKILL.md must declare tools: Read, Write, Edit, Bash."""

    def test_skill_declares_read_tool(self) -> None:
        """configure-devbench skill must declare the Read tool."""
        content = CONFIGURE_DEVBENCH_SKILL_PATH.read_text()
        assert "Read" in content, "configure-devbench/SKILL.md must declare the Read tool"

    def test_skill_declares_write_tool(self) -> None:
        """configure-devbench skill must declare the Write tool."""
        content = CONFIGURE_DEVBENCH_SKILL_PATH.read_text()
        assert "Write" in content, "configure-devbench/SKILL.md must declare the Write tool"

    def test_skill_declares_edit_tool(self) -> None:
        """configure-devbench skill must declare the Edit tool."""
        content = CONFIGURE_DEVBENCH_SKILL_PATH.read_text()
        assert "Edit" in content, "configure-devbench/SKILL.md must declare the Edit tool"

    def test_skill_declares_bash_tool(self) -> None:
        """configure-devbench skill must declare the Bash tool."""
        content = CONFIGURE_DEVBENCH_SKILL_PATH.read_text()
        assert "Bash" in content, "configure-devbench/SKILL.md must declare the Bash tool"


@pytest.mark.unit
class TestConfigureDevbenchSkillReposSection:
    """AC-191-6: Skill must walk the operator through the repos RuntimeConfig section."""

    def test_skill_covers_repos_section(self) -> None:
        """Skill must mention the repos: RuntimeConfig section."""
        content = CONFIGURE_DEVBENCH_SKILL_PATH.read_text()
        assert "repos" in content, (
            "configure-devbench/SKILL.md must walk operator through the 'repos' RuntimeConfig section"
        )

    def test_skill_covers_checkout_directory(self) -> None:
        """Skill must prompt for checkout_directory within the repos section."""
        content = CONFIGURE_DEVBENCH_SKILL_PATH.read_text()
        assert "checkout_directory" in content, (
            "configure-devbench/SKILL.md must prompt for 'checkout_directory' in the repos section"
        )

    def test_skill_covers_default_branch(self) -> None:
        """Skill must prompt for default_branch within the repos section."""
        content = CONFIGURE_DEVBENCH_SKILL_PATH.read_text()
        assert "default_branch" in content, (
            "configure-devbench/SKILL.md must prompt for 'default_branch' in the repos section"
        )

    def test_skill_covers_merge_strategy(self) -> None:
        """Skill must prompt for merge_strategy within the repos section."""
        content = CONFIGURE_DEVBENCH_SKILL_PATH.read_text()
        assert "merge_strategy" in content, (
            "configure-devbench/SKILL.md must prompt for 'merge_strategy' in the repos section"
        )


@pytest.mark.unit
class TestConfigureDevbenchSkillTimeoutsLimitsSection:
    """AC-191-6: Skill must walk the operator through timeouts and limits sections."""

    def test_skill_covers_timeouts(self) -> None:
        """Skill must mention the timeouts: RuntimeConfig section."""
        content = CONFIGURE_DEVBENCH_SKILL_PATH.read_text()
        assert "timeouts" in content, (
            "configure-devbench/SKILL.md must walk operator through the 'timeouts' RuntimeConfig section"
        )

    def test_skill_covers_limits(self) -> None:
        """Skill must mention the limits: RuntimeConfig section."""
        content = CONFIGURE_DEVBENCH_SKILL_PATH.read_text()
        assert "limits" in content, (
            "configure-devbench/SKILL.md must walk operator through the 'limits' RuntimeConfig section"
        )


@pytest.mark.unit
class TestConfigureDevbenchSkillModelSection:
    """AC-191-6: Skill must walk the operator through judge_model and executor_model settings."""

    def test_skill_covers_judge_model(self) -> None:
        """Skill must mention judge_model (agent_models) configuration."""
        content = CONFIGURE_DEVBENCH_SKILL_PATH.read_text()
        assert "judge_model" in content or "agent_models" in content or "agents:" in content, (
            "configure-devbench/SKILL.md must walk operator through judge_model / agent_models section"
        )

    def test_skill_covers_executor_model(self) -> None:
        """Skill must mention executor model configuration."""
        content = CONFIGURE_DEVBENCH_SKILL_PATH.read_text()
        assert "executor_model" in content or "executor" in content, (
            "configure-devbench/SKILL.md must walk operator through the executor_model setting"
        )

    def test_review_supervisor_is_not_described_as_fan_out_coordinator(self) -> None:
        """Post-flatten (ADR-33), review-supervisor is a non-spawning aggregator.

        review-supervisor no longer declares an Agent tool and cannot spawn the
        four review_team judges, so describing it as a 'fan-out coordinator'
        in the operator-facing agents: prompt is stale and misleading.
        """
        content = CONFIGURE_DEVBENCH_SKILL_PATH.read_text()
        assert "Fan-out coordinator" not in content, (
            "configure-devbench/SKILL.md must not describe review_supervisor as a "
            "'Fan-out coordinator' -- post-flatten (ADR-33) it is a non-spawning "
            "aggregator with no Agent tool."
        )
        assert "review_supervisor" in content and "aggregat" in content.lower(), (
            "configure-devbench/SKILL.md must describe review_supervisor's "
            "post-flatten role as aggregation of already-persisted verdicts."
        )


@pytest.mark.unit
class TestConfigureDevbenchSkillBedrockSection:
    """AC-191-6: Skill must walk the operator through bedrock_region and use_bedrock settings."""

    def test_skill_covers_use_bedrock(self) -> None:
        """Skill must mention the use_bedrock RuntimeConfig field."""
        content = CONFIGURE_DEVBENCH_SKILL_PATH.read_text()
        assert "use_bedrock" in content, (
            "configure-devbench/SKILL.md must walk operator through the 'use_bedrock' setting"
        )

    def test_skill_covers_bedrock_region(self) -> None:
        """Skill must mention the bedrock_region RuntimeConfig field."""
        content = CONFIGURE_DEVBENCH_SKILL_PATH.read_text()
        assert "bedrock_region" in content, (
            "configure-devbench/SKILL.md must walk operator through the 'bedrock_region' setting"
        )


@pytest.mark.unit
class TestConfigureDevbenchSkillTaskFactorySection:
    """AC-191-6: Skill must walk the operator through task_factory section."""

    def test_skill_covers_task_factory(self) -> None:
        """Skill must mention the task_factory: RuntimeConfig section."""
        content = CONFIGURE_DEVBENCH_SKILL_PATH.read_text()
        assert "task_factory" in content, (
            "configure-devbench/SKILL.md must walk operator through the 'task_factory' section"
        )


@pytest.mark.unit
class TestConfigureDevbenchSkillManifestAmendmentSection:
    """AC-191-6: Skill must walk the operator through manifest_amendment section."""

    def test_skill_covers_manifest_amendment(self) -> None:
        """Skill must mention the manifest_amendment: RuntimeConfig section."""
        content = CONFIGURE_DEVBENCH_SKILL_PATH.read_text()
        assert "manifest_amendment" in content, (
            "configure-devbench/SKILL.md must walk operator through the 'manifest_amendment' section"
        )


@pytest.mark.unit
class TestConfigureDevbenchSkillValidateDebugSection:
    """AC-191-6: Skill must walk the operator through validate and debug sections."""

    def test_skill_covers_validate(self) -> None:
        """Skill must mention the validate: RuntimeConfig section."""
        content = CONFIGURE_DEVBENCH_SKILL_PATH.read_text()
        assert "validate" in content, "configure-devbench/SKILL.md must walk operator through the 'validate' section"

    def test_skill_covers_debug(self) -> None:
        """Skill must mention the debug: RuntimeConfig section."""
        content = CONFIGURE_DEVBENCH_SKILL_PATH.read_text()
        assert "debug" in content, "configure-devbench/SKILL.md must walk operator through the 'debug' section"


@pytest.mark.unit
class TestConfigureDevbenchSkillStopHookSection:
    """AC-191-6: Skill must walk the operator through stop_hook and hook_tail sections."""

    def test_skill_covers_stop_hook(self) -> None:
        """Skill must mention the stop_hook: RuntimeConfig section."""
        content = CONFIGURE_DEVBENCH_SKILL_PATH.read_text()
        assert "stop_hook" in content, "configure-devbench/SKILL.md must walk operator through the 'stop_hook' section"

    def test_skill_covers_hook_tail(self) -> None:
        """Skill must mention the hook_tail: RuntimeConfig section."""
        content = CONFIGURE_DEVBENCH_SKILL_PATH.read_text()
        assert "hook_tail" in content, "configure-devbench/SKILL.md must walk operator through the 'hook_tail' section"


@pytest.mark.unit
class TestConfigureDevbenchSkillGitOpsSection:
    """AC-191-6: Skill must walk the operator through git_ops section with all sub-fields."""

    def test_skill_covers_git_ops(self) -> None:
        """Skill must mention the git_ops: RuntimeConfig section."""
        content = CONFIGURE_DEVBENCH_SKILL_PATH.read_text()
        assert "git_ops" in content, "configure-devbench/SKILL.md must walk operator through the 'git_ops' section"

    def test_skill_covers_single_branch(self) -> None:
        """Skill must mention single_branch within the git_ops section."""
        content = CONFIGURE_DEVBENCH_SKILL_PATH.read_text()
        assert "single_branch" in content, (
            "configure-devbench/SKILL.md must prompt for 'single_branch' in the git_ops section"
        )

    def test_skill_covers_defer_pr(self) -> None:
        """Skill must mention defer_pr within the git_ops section."""
        content = CONFIGURE_DEVBENCH_SKILL_PATH.read_text()
        assert "defer_pr" in content, "configure-devbench/SKILL.md must prompt for 'defer_pr' in the git_ops section"

    def test_skill_covers_auto_finalize(self) -> None:
        """Skill must mention auto_finalize within the git_ops section."""
        content = CONFIGURE_DEVBENCH_SKILL_PATH.read_text()
        assert "auto_finalize" in content, (
            "configure-devbench/SKILL.md must prompt for 'auto_finalize' in the git_ops section"
        )

    def test_skill_covers_auto_merge(self) -> None:
        """Skill must mention auto_merge within the git_ops section."""
        content = CONFIGURE_DEVBENCH_SKILL_PATH.read_text()
        assert "auto_merge" in content, (
            "configure-devbench/SKILL.md must prompt for 'auto_merge' in the git_ops section"
        )


@pytest.mark.unit
class TestConfigureDevbenchSkillBacklogSection:
    """AC-191-6: Skill must walk the operator through the backlog section (issue #189)."""

    def test_skill_covers_backlog_section(self) -> None:
        """Skill must mention the backlog: RuntimeConfig section."""
        content = CONFIGURE_DEVBENCH_SKILL_PATH.read_text()
        assert "backlog" in content, "configure-devbench/SKILL.md must walk operator through the 'backlog' section"

    def test_skill_covers_default_status_for_new_work_units(self) -> None:
        """Skill must mention default_status_for_new_work_units within the backlog section."""
        content = CONFIGURE_DEVBENCH_SKILL_PATH.read_text()
        assert "default_status_for_new_work_units" in content, (
            "configure-devbench/SKILL.md must prompt for 'default_status_for_new_work_units' in the backlog section"
        )


@pytest.mark.unit
class TestConfigureDevbenchSkillRoundTripValidation:
    """AC-191-6: Skill must round-trip values through RuntimeConfig before writing YAML."""

    def test_skill_references_runtimeconfig(self) -> None:
        """Skill must explicitly reference RuntimeConfig or config_loader validation."""
        content = CONFIGURE_DEVBENCH_SKILL_PATH.read_text()
        assert "RuntimeConfig" in content or "config_loader" in content or "devbench.yaml" in content, (
            "configure-devbench/SKILL.md must validate operator values via RuntimeConfig "
            "before writing backlog/config/devbench.yaml"
        )

    def test_skill_round_trips_values_immediately(self) -> None:
        """Skill must round-trip each value through RuntimeConfig parsing immediately after input."""
        content = CONFIGURE_DEVBENCH_SKILL_PATH.read_text()
        assert any(kw in content.lower() for kw in ("round-trip", "round trip", "validate", "validation")), (
            "configure-devbench/SKILL.md must specify that each operator-provided value is "
            "round-tripped through RuntimeConfig parsing immediately after input"
        )

    def test_skill_rejects_invalid_values_with_reprompt(self) -> None:
        """Skill must reject invalid values and re-prompt the operator."""
        content = CONFIGURE_DEVBENCH_SKILL_PATH.read_text()
        assert any(kw in content.lower() for kw in ("reject", "re-prompt", "reprompt", "re-ask", "invalid")), (
            "configure-devbench/SKILL.md must reject invalid values and re-prompt the operator"
        )

    def test_skill_writes_devbench_yaml(self) -> None:
        """Skill must write backlog/config/devbench.yaml as its final output."""
        content = CONFIGURE_DEVBENCH_SKILL_PATH.read_text()
        assert "devbench.yaml" in content, (
            "configure-devbench/SKILL.md must write backlog/config/devbench.yaml as its output"
        )


_ONBOARDING_SKILL_PATHS = {
    "create-spec": CREATE_SPEC_SKILL_PATH,
    "spec-to-backlog": SPEC_TO_BACKLOG_SKILL_PATH,
    "bootstrap-environment": (
        Path(__file__).parent.parent.parent
        / "plugin-authoring"
        / "devbench-authoring"
        / "skills"
        / "bootstrap-environment"
        / "SKILL.md"
    ),
    "configure-devbench": (
        Path(__file__).parent.parent.parent
        / "plugin-authoring"
        / "devbench-authoring"
        / "skills"
        / "configure-devbench"
        / "SKILL.md"
    ),
}


@pytest.mark.unit
class TestBoundedSelfCritiqueLoop:
    """Issue #204: every onboarding skill documents a bounded self-critique loop.

    Each SKILL.md must contain a `## Self-critique loop (bounded)` heading and
    reference the constants defined in ``src/devbench/constants.py`` by name so
    the operator-facing contract is observable in the doc and the runtime
    enforcement points at the same symbols.
    """

    @pytest.mark.parametrize("skill_name", sorted(_ONBOARDING_SKILL_PATHS))
    def test_skill_has_bounded_loop_section(self, skill_name: str) -> None:
        content = _ONBOARDING_SKILL_PATHS[skill_name].read_text(encoding="utf-8")
        assert "## Self-critique loop (bounded)" in content, (
            f"{skill_name}/SKILL.md must declare a `## Self-critique loop (bounded)` section "
            "to document the iteration-budget contract (issue #204)."
        )

    @pytest.mark.parametrize("skill_name", sorted(_ONBOARDING_SKILL_PATHS))
    def test_skill_references_max_iterations_constant(self, skill_name: str) -> None:
        content = _ONBOARDING_SKILL_PATHS[skill_name].read_text(encoding="utf-8")
        assert "SKILL_MAX_ITERATIONS" in content, f"{skill_name}/SKILL.md must reference SKILL_MAX_ITERATIONS by name."

    @pytest.mark.parametrize("skill_name", sorted(_ONBOARDING_SKILL_PATHS))
    def test_skill_references_quality_threshold_constant(self, skill_name: str) -> None:
        content = _ONBOARDING_SKILL_PATHS[skill_name].read_text(encoding="utf-8")
        assert "SKILL_QUALITY_THRESHOLD" in content, (
            f"{skill_name}/SKILL.md must reference SKILL_QUALITY_THRESHOLD by name."
        )

    @pytest.mark.parametrize("skill_name", sorted(_ONBOARDING_SKILL_PATHS))
    def test_skill_references_audit_tag_constants(self, skill_name: str) -> None:
        content = _ONBOARDING_SKILL_PATHS[skill_name].read_text(encoding="utf-8")
        for symbol in (
            "SKILL_AUDIT_MAX_ITERATIONS_REACHED",
            "SKILL_AUDIT_QUALITY_THRESHOLD_REACHED",
        ):
            assert symbol in content, f"{skill_name}/SKILL.md must reference {symbol} by name."

    @pytest.mark.parametrize("skill_name", sorted(_ONBOARDING_SKILL_PATHS))
    def test_skill_uses_own_name_in_helper_calls(self, skill_name: str) -> None:
        """The SKILL.md prose calls helpers with the matching skill name string."""
        content = _ONBOARDING_SKILL_PATHS[skill_name].read_text(encoding="utf-8")
        assert f'"{skill_name}"' in content, (
            f"{skill_name}/SKILL.md must reference its own name string in helper-call examples."
        )
