"""Unit tests for spec-to-backlog SKILL.md Step 7c gap-fill and re-validate loop.

Verifies that plugin-authoring/devbench-authoring/skills/spec-to-backlog/SKILL.md
Step 7c correctly describes:

- AC-1: NEW TASK gaps routed through the existing Step-5 authoring path (15 canonical
  sections, canonical Code Standards block, dep wiring, index row).
- AC-1: ENHANCE gaps routed through file-partitioned fan-out (one agent per task file)
  adding missing ACs/Approach/Manifest/DoD from the cited spec section.
- AC-2: The loop regenerates the index, runs the post-processor and validate-backlog,
  then re-audits, repeating until zero confirmed gaps or skills.max_iterations.
- AC-2: [BLOCKED] escalation at skills.max_iterations listing unresolved gaps.
- AC-3: Success is declared only at zero confirmed gaps AND validate-backlog rc=0.
- AC-4: Single-agent FR/AC citation fallback when Workflow is unavailable; thresholds
  and rounds are config-driven.

Spec Section 4 E12-F3-S2 AC-1, AC-2, AC-3, AC-4. GitHub issue #265.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

SKILL_PATH = (
    Path(__file__).parent.parent.parent
    / "plugin-authoring"
    / "devbench-authoring"
    / "skills"
    / "spec-to-backlog"
    / "SKILL.md"
)

FIXTURE_SPEC_PATH = Path(__file__).parent / "fixtures" / "gap_fill_multi_file_spec.md"


def _read_skill() -> str:
    return SKILL_PATH.read_text(encoding="utf-8")


def _extract_step7c(text: str) -> str:
    """Return the text of Step 7c (gap-fill and re-validate loop section).

    Raises ValueError if Step 7c cannot be located.
    """
    idx = text.find("## Step 7c")
    if idx == -1:
        raise ValueError(
            "ERROR: '## Step 7c' not found in spec-to-backlog/SKILL.md. "
            "The gap-fill and re-validate loop section is required "
            "(spec Section 4 E12-F3-S2, issue #265)."
        )
    next_h2 = text.find("\n## ", idx + len("## Step 7c"))
    if next_h2 != -1:
        return text[idx:next_h2]
    return text[idx:]


@pytest.mark.unit
class TestStep7cSectionExists:
    """AC-1/AC-2/AC-3/AC-4: Step 7c must exist in SKILL.md."""

    def test_step7c_section_exists(self) -> None:
        """SKILL.md must contain a Step 7c section for the gap-fill loop."""
        content = _read_skill()
        assert "## Step 7c" in content, (
            "ERROR: spec-to-backlog/SKILL.md must contain '## Step 7c' -- "
            "the gap-fill and re-validate loop. "
            "Add '## Step 7c -- Gap-fill and re-validate loop' between Step 7b and Step 8 "
            "(spec Section 4 E12-F3-S2, issue #265)."
        )

    def test_step7c_appears_after_step7b(self) -> None:
        """Step 7c must appear after Step 7b in SKILL.md."""
        content = _read_skill()
        idx_7b = content.find("## Step 7b")
        idx_7c = content.find("## Step 7c")
        assert idx_7b != -1, "ERROR: '## Step 7b' not found in SKILL.md -- prerequisite for Step 7c ordering check."
        assert idx_7c != -1, "ERROR: '## Step 7c' not found in SKILL.md -- gap-fill section required (issue #265)."
        assert idx_7c > idx_7b, (
            "ERROR: '## Step 7c' must appear after '## Step 7b' in SKILL.md (spec Section 4 E12-F3-S2, issue #265)."
        )

    def test_step7c_appears_before_step8(self) -> None:
        """Step 7c must appear before Step 8 in SKILL.md."""
        content = _read_skill()
        idx_7c = content.find("## Step 7c")
        idx_8 = content.find("## Step 8")
        assert idx_7c != -1, "ERROR: '## Step 7c' not found in SKILL.md -- gap-fill section required (issue #265)."
        assert idx_8 != -1, "ERROR: '## Step 8' not found in SKILL.md -- prerequisite for Step 7c ordering check."
        assert idx_7c < idx_8, (
            "ERROR: '## Step 7c' must appear before '## Step 8' in SKILL.md (spec Section 4 E12-F3-S2, issue #265)."
        )


@pytest.mark.unit
class TestStep7cNewTaskRouting:
    """AC-1: NEW TASK gaps must be routed through the existing Step-5 authoring path."""

    def test_new_task_routing_via_step5(self) -> None:
        """Step 7c must route NEW TASK gaps through the existing Step-5 path."""
        content = _read_skill()
        section = _extract_step7c(content)
        assert "NEW TASK" in section, (
            "ERROR: spec-to-backlog/SKILL.md Step 7c must describe NEW TASK gap routing "
            "(spec Section 4 E12-F3-S2 AC-1, issue #265)."
        )
        assert "Step 5" in section or "Step-5" in section, (
            "ERROR: spec-to-backlog/SKILL.md Step 7c must route NEW TASK gaps through "
            "the existing Step-5 authoring path "
            "(spec Section 4 E12-F3-S2 AC-1, issue #265)."
        )

    def test_new_task_step5_includes_15_sections(self) -> None:
        """Step 7c must confirm NEW TASK authoring uses the 15-section path."""
        content = _read_skill()
        section = _extract_step7c(content)
        lower = section.lower()
        has_15_sections = "15" in section or "fifteen" in lower or "canonical section" in lower
        assert has_15_sections, (
            "ERROR: spec-to-backlog/SKILL.md Step 7c NEW TASK path must reference "
            "the 15 canonical sections from Step-5 "
            "(spec Section 4 E12-F3-S2 AC-1, issue #265)."
        )

    def test_new_task_includes_dep_wiring_and_index_row(self) -> None:
        """Step 7c NEW TASK authoring must include dep wiring and index row."""
        content = _read_skill()
        section = _extract_step7c(content)
        lower = section.lower()
        has_dep_wiring = "dep" in lower and ("wir" in lower or "depend" in lower)
        has_index_row = "index row" in lower or "index" in lower
        assert has_dep_wiring, (
            "ERROR: spec-to-backlog/SKILL.md Step 7c must include dependency wiring "
            "for NEW TASK gaps (spec Section 4 E12-F3-S2 AC-1, issue #265)."
        )
        assert has_index_row, (
            "ERROR: spec-to-backlog/SKILL.md Step 7c must include an index row "
            "for NEW TASK gaps (spec Section 4 E12-F3-S2 AC-1, issue #265)."
        )


@pytest.mark.unit
class TestStep7cEnhanceRouting:
    """AC-1: ENHANCE gaps must use file-partitioned fan-out, one agent per task file."""

    def test_enhance_routing_via_file_partitioned_fanout(self) -> None:
        """Step 7c must route ENHANCE gaps through file-partitioned fan-out."""
        content = _read_skill()
        section = _extract_step7c(content)
        assert "ENHANCE" in section, (
            "ERROR: spec-to-backlog/SKILL.md Step 7c must describe ENHANCE gap routing "
            "(spec Section 4 E12-F3-S2 AC-1, issue #265)."
        )
        has_fanout = "fan-out" in section.lower() or "fan out" in section.lower() or "fanout" in section.lower()
        assert has_fanout, (
            "ERROR: spec-to-backlog/SKILL.md Step 7c must route ENHANCE gaps through "
            "file-partitioned fan-out "
            "(spec Section 4 E12-F3-S2 AC-1, issue #265)."
        )

    def test_enhance_fanout_one_agent_per_file(self) -> None:
        """Step 7c ENHANCE fan-out must specify one agent per task file."""
        content = _read_skill()
        section = _extract_step7c(content)
        lower = section.lower()
        has_per_file = "per task file" in lower or "one agent per" in lower or "per-file" in lower
        assert has_per_file, (
            "ERROR: spec-to-backlog/SKILL.md Step 7c ENHANCE fan-out must specify "
            "one agent per task file "
            "(spec Section 4 E12-F3-S2 AC-1, issue #265)."
        )

    def test_enhance_copies_substance_from_spec_section(self) -> None:
        """Step 7c ENHANCE routing must copy substance from the cited spec section."""
        content = _read_skill()
        section = _extract_step7c(content)
        lower = section.lower()
        has_spec_substance = "spec section" in lower or "cited spec" in lower
        assert has_spec_substance, (
            "ERROR: spec-to-backlog/SKILL.md Step 7c must instruct the ENHANCE agent "
            "to copy substance from the cited spec section "
            "(spec Section 4 E12-F3-S2 AC-1, issue #265)."
        )

    def test_enhance_covers_missing_acs_approach_manifest_dod(self) -> None:
        """Step 7c ENHANCE routing must cover missing ACs, Approach, Manifest, and DoD."""
        content = _read_skill()
        section = _extract_step7c(content)
        lower = section.lower()
        missing_sections = ["ac", "approach", "manifest", "dod"]
        for expected in missing_sections:
            assert expected in lower, (
                f"ERROR: spec-to-backlog/SKILL.md Step 7c ENHANCE routing must address "
                f"missing '{expected.upper()}' in the enhanced task file "
                f"(spec Section 4 E12-F3-S2 AC-1, issue #265)."
            )

    def test_enhance_uses_ledger_as_tiebreaker(self) -> None:
        """Step 7c ENHANCE routing must use the resolved-decisions ledger as tie-breaker."""
        content = _read_skill()
        section = _extract_step7c(content)
        lower = section.lower()
        has_ledger = "ledger" in lower or "resolved-decision" in lower or "resolved decision" in lower
        assert has_ledger, (
            "ERROR: spec-to-backlog/SKILL.md Step 7c must reference the "
            "resolved-decisions ledger as the contradiction tie-breaker for ENHANCE gaps "
            "(spec Section 4 E12-F3-S2 AC-1, issue #265)."
        )


@pytest.mark.unit
class TestStep7cRevalidateLoop:
    """AC-2: The loop must regenerate index, run post-processor and validate-backlog, then re-audit."""

    def test_loop_regenerates_index(self) -> None:
        """Step 7c loop must regenerate the backlog index after each gap-fill round."""
        content = _read_skill()
        section = _extract_step7c(content)
        lower = section.lower()
        has_index_regen = "regenerate" in lower or "index" in lower
        assert has_index_regen, (
            "ERROR: spec-to-backlog/SKILL.md Step 7c loop must regenerate the "
            "backlog index after each gap-fill round "
            "(spec Section 4 E12-F3-S2 AC-2, issue #265)."
        )

    def test_loop_runs_post_processor(self) -> None:
        """Step 7c loop must run the post-processor after index regeneration."""
        content = _read_skill()
        section = _extract_step7c(content)
        lower = section.lower()
        has_post_processor = "post-processor" in lower or "post processor" in lower
        assert has_post_processor, (
            "ERROR: spec-to-backlog/SKILL.md Step 7c loop must run the post-processor "
            "after each gap-fill round "
            "(spec Section 4 E12-F3-S2 AC-2, issue #265)."
        )

    def test_loop_runs_validate_backlog(self) -> None:
        """Step 7c loop must run validate-backlog after the post-processor."""
        content = _read_skill()
        section = _extract_step7c(content)
        assert "validate-backlog" in section, (
            "ERROR: spec-to-backlog/SKILL.md Step 7c loop must run validate-backlog "
            "after each gap-fill round "
            "(spec Section 4 E12-F3-S2 AC-2, issue #265)."
        )

    def test_loop_re_audits_after_validate(self) -> None:
        """Step 7c loop must re-audit coverage after validate-backlog passes."""
        content = _read_skill()
        section = _extract_step7c(content)
        lower = section.lower()
        has_reaudit = "re-audit" in lower or "reaudit" in lower or "re-run" in lower or "audit" in lower
        assert has_reaudit, (
            "ERROR: spec-to-backlog/SKILL.md Step 7c must re-audit coverage after "
            "each validate-backlog pass "
            "(spec Section 4 E12-F3-S2 AC-2, issue #265)."
        )

    def test_loop_bounded_by_max_iterations(self) -> None:
        """Step 7c loop must be bounded by skills.max_iterations."""
        content = _read_skill()
        section = _extract_step7c(content)
        lower = section.lower()
        has_max_iter = "max_iterations" in section or "max iterations" in lower
        assert has_max_iter, (
            "ERROR: spec-to-backlog/SKILL.md Step 7c loop must be bounded by "
            "skills.max_iterations from devbench.yaml "
            "(spec Section 4 E12-F3-S2 AC-2, issue #265)."
        )

    def test_loop_emits_blocked_at_max_iterations(self) -> None:
        """Step 7c must emit [BLOCKED] escalation when skills.max_iterations is reached."""
        content = _read_skill()
        section = _extract_step7c(content)
        assert "[BLOCKED]" in section, (
            "ERROR: spec-to-backlog/SKILL.md Step 7c must emit a [BLOCKED] escalation "
            "when skills.max_iterations is reached with confirmed gaps remaining "
            "(spec Section 4 E12-F3-S2 AC-2, issue #265)."
        )

    def test_blocked_escalation_lists_unresolved_gaps(self) -> None:
        """Step 7c [BLOCKED] escalation must list the unresolved gaps."""
        content = _read_skill()
        section = _extract_step7c(content)
        lower = section.lower()
        has_gap_list = "unresolved gap" in lower or "remaining gap" in lower or "unresolved" in lower
        assert has_gap_list, (
            "ERROR: spec-to-backlog/SKILL.md Step 7c [BLOCKED] escalation must list "
            "the unresolved gaps so the operator knows what to fix "
            "(spec Section 4 E12-F3-S2 AC-2, issue #265)."
        )

    def test_loop_reuses_existing_post_processor_invocation(self) -> None:
        """Step 7c loop must reuse the post-processor invocation from Step 5d, not duplicate it."""
        content = _read_skill()
        section = _extract_step7c(content)
        lower = section.lower()
        has_reuse_reference = (
            "step 5d" in lower
            or "step-5d" in lower
            or "reuse" in lower
            or "same" in lower
            or "as in" in lower
            or "existing" in lower
        )
        assert has_reuse_reference, (
            "ERROR: spec-to-backlog/SKILL.md Step 7c must instruct the loop to reuse "
            "the existing post-processor invocation (from Step 5d) rather than duplicating "
            "it inline (DRY principle; spec Section 4 E12-F3-S2 AC-2, issue #265)."
        )


@pytest.mark.unit
class TestStep7cSuccessGate:
    """AC-3: Success must be declared only at zero confirmed gaps AND validate-backlog rc=0."""

    def test_success_requires_zero_confirmed_gaps(self) -> None:
        """Step 7c success gate must require zero confirmed gaps."""
        content = _read_skill()
        section = _extract_step7c(content)
        lower = section.lower()
        has_zero_gaps = "zero confirmed gap" in lower or "0 confirmed gap" in lower or "zero gap" in lower
        assert has_zero_gaps, (
            "ERROR: spec-to-backlog/SKILL.md Step 7c success gate must require "
            "zero confirmed gaps (spec Section 4 E12-F3-S2 AC-3, issue #265)."
        )

    def test_success_requires_validate_backlog_rc0(self) -> None:
        """Step 7c success gate must require validate-backlog to return rc=0."""
        content = _read_skill()
        section = _extract_step7c(content)
        lower = section.lower()
        has_rc0 = "rc=0" in lower or "rc = 0" in lower or "return code 0" in lower or "returns rc=0" in lower
        assert has_rc0, (
            "ERROR: spec-to-backlog/SKILL.md Step 7c success gate must require "
            "validate-backlog rc=0 (spec Section 4 E12-F3-S2 AC-3, issue #265)."
        )

    def test_success_requires_both_conditions(self) -> None:
        """Step 7c success gate must require BOTH zero gaps AND rc=0 simultaneously."""
        content = _read_skill()
        section = _extract_step7c(content)
        lower = section.lower()
        has_both = "both" in lower or "and" in lower
        has_gap_condition = "zero" in lower and "gap" in lower
        has_rc_condition = "rc=0" in lower or "rc = 0" in lower
        assert has_gap_condition and has_rc_condition and has_both, (
            "ERROR: spec-to-backlog/SKILL.md Step 7c success gate must require BOTH "
            "zero confirmed gaps AND validate-backlog rc=0 simultaneously -- "
            "neither condition alone is sufficient "
            "(spec Section 4 E12-F3-S2 AC-3, issue #265)."
        )

    def test_partial_success_not_allowed(self) -> None:
        """Step 7c must not allow declaring success while any confirmed gap remains."""
        content = _read_skill()
        section = _extract_step7c(content)
        lower = section.lower()
        has_strict_gate = "only" in lower or "must not" in lower or "cannot" in lower or "impossible" in lower
        assert has_strict_gate, (
            "ERROR: spec-to-backlog/SKILL.md Step 7c success gate must be strict -- "
            "declaring success while any confirmed gap remains must be impossible "
            "(spec Section 4 E12-F3-S2 AC-3, issue #265)."
        )


@pytest.mark.unit
class TestStep7cWorkflowFallback:
    """AC-4: When Workflow is unavailable the single-agent FR/AC citation rubric runs unchanged."""

    def test_workflow_absent_fallback_described(self) -> None:
        """Step 7c must describe the single-agent fallback when Workflow is unavailable."""
        content = _read_skill()
        section = _extract_step7c(content)
        lower = section.lower()
        has_fallback = "workflow" in lower and ("unavailable" in lower or "absent" in lower or "not available" in lower)
        assert has_fallback, (
            "ERROR: spec-to-backlog/SKILL.md Step 7c must describe what happens when "
            "the Workflow tool is unavailable "
            "(spec Section 4 E12-F3-S2 AC-4, issue #265)."
        )

    def test_single_agent_fr_ac_citation_rubric_preserved(self) -> None:
        """Step 7c must state that the single-agent FR/AC citation rubric runs unchanged."""
        content = _read_skill()
        section = _extract_step7c(content)
        lower = section.lower()
        has_single_agent = "single-agent" in lower or "single agent" in lower
        assert has_single_agent, (
            "ERROR: spec-to-backlog/SKILL.md Step 7c must state that the "
            "single-agent FR/AC citation rubric runs unchanged when Workflow is absent "
            "(spec Section 4 E12-F3-S2 AC-4, issue #265)."
        )
        has_citation_rubric = "citation" in lower or "fr/ac" in lower or "fr" in lower
        assert has_citation_rubric, (
            "ERROR: spec-to-backlog/SKILL.md Step 7c single-agent fallback must "
            "preserve the FR/AC citation rubric "
            "(spec Section 4 E12-F3-S2 AC-4, issue #265)."
        )

    def test_thresholds_are_config_driven(self) -> None:
        """Step 7c must state that thresholds and rounds are config-driven."""
        content = _read_skill()
        section = _extract_step7c(content)
        lower = section.lower()
        has_config_driven = "config" in lower or "devbench.yaml" in lower or "configur" in lower
        assert has_config_driven, (
            "ERROR: spec-to-backlog/SKILL.md Step 7c must state that thresholds "
            "and round counts are config-driven via skills.max_iterations in devbench.yaml "
            "(spec Section 4 E12-F3-S2 AC-4, issue #265)."
        )

    def test_max_iterations_source_from_config(self) -> None:
        """Step 7c must reference skills.max_iterations (from devbench.yaml) as the iteration bound."""
        content = _read_skill()
        section = _extract_step7c(content)
        assert "skills.max_iterations" in section or "max_iterations" in section, (
            "ERROR: spec-to-backlog/SKILL.md Step 7c must reference skills.max_iterations "
            "from devbench.yaml as the config-driven iteration bound "
            "(spec Section 4 E12-F3-S2 AC-4, issue #265)."
        )


@pytest.mark.unit
class TestStep7cNoEmDash:
    """Critical Rule 8: Step 7c must not contain em-dash characters (U+2014)."""

    def test_no_em_dash_in_step7c(self) -> None:
        """Step 7c must use -- instead of the em-dash character."""
        content = _read_skill()
        section = _extract_step7c(content)
        assert "\u2014" not in section, (
            "ERROR: spec-to-backlog/SKILL.md Step 7c must not contain em-dash "
            "characters (U+2014); use -- instead (Critical Rule 8)."
        )


@pytest.mark.unit
class TestFixtureSpec:
    """Tests that the fixture spec is well-formed and exercises the relevant gap-fill scenarios."""

    def test_fixture_spec_exists(self) -> None:
        """The multi-file fixture spec must exist at the declared path."""
        assert FIXTURE_SPEC_PATH.exists(), (
            f"ERROR: Fixture spec not found at {FIXTURE_SPEC_PATH}. "
            "Create tests/test_plugin/fixtures/gap_fill_multi_file_spec.md "
            "(Changes Manifest, issue #265)."
        )

    def test_fixture_spec_has_functional_requirements(self) -> None:
        """The fixture spec must contain FR lines for gap-fill scenario coverage."""
        content = FIXTURE_SPEC_PATH.read_text(encoding="utf-8")
        fr_lines = [line for line in content.splitlines() if re.match(r"^FR-\d+:", line.strip())]
        assert len(fr_lines) >= 4, (
            f"ERROR: Fixture spec must contain at least 4 FR-N lines to exercise "
            f"multi-file gap-fill scenarios. Found {len(fr_lines)}. "
            "(tests/test_plugin/fixtures/gap_fill_multi_file_spec.md, issue #265)."
        )

    def test_fixture_spec_has_acceptance_criteria_section(self) -> None:
        """The fixture spec must contain an AC-SECTION-START marker or Section 6."""
        content = FIXTURE_SPEC_PATH.read_text(encoding="utf-8")
        has_marker = "<!-- AC-SECTION-START -->" in content
        has_section6 = "section 6" in content.lower() or "## section 6" in content.lower()
        assert has_marker or has_section6, (
            "ERROR: Fixture spec must contain either the '<!-- AC-SECTION-START -->' "
            "marker or a '## Section 6' heading so spec-to-backlog can locate ACs. "
            "(tests/test_plugin/fixtures/gap_fill_multi_file_spec.md, issue #265)."
        )

    def test_fixture_spec_has_new_task_and_enhance_gap_scenarios(self) -> None:
        """The fixture spec must contain scenarios mapping to NEW TASK and ENHANCE gap types."""
        content = FIXTURE_SPEC_PATH.read_text(encoding="utf-8")
        lower = content.lower()
        has_new_task_scenario = "new task" in lower or "author new" in lower or "author" in lower
        has_enhance_scenario = "enhance" in lower or "missing" in lower or "fan-out" in lower
        assert has_new_task_scenario, (
            "ERROR: Fixture spec must contain a scenario that maps to a NEW TASK gap "
            "(a spec requirement with no covering task). "
            "(tests/test_plugin/fixtures/gap_fill_multi_file_spec.md, issue #265)."
        )
        assert has_enhance_scenario, (
            "ERROR: Fixture spec must contain a scenario that maps to an ENHANCE gap "
            "(a task that exists but is missing required sections). "
            "(tests/test_plugin/fixtures/gap_fill_multi_file_spec.md, issue #265)."
        )

    @pytest.mark.parametrize(
        "fr_keyword",
        [
            "new task",
            "enhance",
            "post-processor",
            "validate-backlog",
        ],
    )
    def test_fixture_spec_fr_covers_key_gap_fill_scenarios(self, fr_keyword: str) -> None:
        """Fixture spec FR lines must collectively cover all key gap-fill scenarios."""
        content = FIXTURE_SPEC_PATH.read_text(encoding="utf-8")
        lower = content.lower()
        assert fr_keyword in lower, (
            f"ERROR: Fixture spec must cover the '{fr_keyword}' gap-fill scenario "
            f"in its FR lines or AC section. "
            "(tests/test_plugin/fixtures/gap_fill_multi_file_spec.md, issue #265)."
        )
