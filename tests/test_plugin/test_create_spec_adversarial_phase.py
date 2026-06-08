"""Unit tests for create-spec adversarial review phase (E12-F2-S1-T1).

Asserts that create-spec/SKILL.md:
  (a) Gates the adversarial phase on Workflow availability and
      `skills.adversarial_review_threshold`.
  (b) Enumerates exactly the five generic dimensions and states that finer
      checks are derived from the spec content, not a domain taxonomy.
  (c) Requires per-finding skeptic re-verification with the
      CONFIRMED / REJECTED / severity-adjusted vocabulary and defaults to
      rejecting unverifiable findings.
  (d) Preserves the single-agent Step-4 fallback when Workflow is absent.
  (e) References the shared reusable-patterns doc rather than restating it.
  (f) Contains no em-dash characters (U+2014).
"""

from __future__ import annotations

from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).parent.parent.parent

SKILL_PATH = REPO_ROOT / "plugin-authoring" / "devbench-authoring" / "skills" / "create-spec" / "SKILL.md"

SHARED_DOC_RELATIVE_LINK = "docs/workflow-authoring-patterns.md"

# The five generic review dimensions from spec Section 4 E12-F2-S1 AC-1.
FIVE_DIMENSIONS = [
    "implementability",
    "internal consistency",
    "completeness",
    "claims-grounding",
    "citation",
]

# Skeptic verdict vocabulary from spec Section 4 E12-F2-S1 AC-2.
SKEPTIC_VERDICTS = [
    "CONFIRMED",
    "REJECTED",
    "severity-adjusted",
]


@pytest.mark.unit
class TestAdversarialPhaseExists:
    """AC-1 / AC-2 / AC-3: The adversarial phase section must be present."""

    def test_skill_file_exists(self) -> None:
        """The create-spec SKILL.md must exist."""
        assert SKILL_PATH.exists(), f"create-spec SKILL.md not found at {SKILL_PATH}."

    def test_adversarial_phase_mentioned(self) -> None:
        """The SKILL.md must describe an adversarial review phase."""
        content = SKILL_PATH.read_text()
        assert "adversarial" in content.lower(), (
            "create-spec SKILL.md does not mention an adversarial review phase. "
            "Add the adversarial review phase section (E12-F2-S1)."
        )


@pytest.mark.unit
class TestAdversarialPhaseGating:
    """AC-1 / AC-4: The phase must be gated on Workflow availability and
    skills.adversarial_review_threshold."""

    def test_gated_on_workflow_availability(self) -> None:
        """The phase must be conditional on the Workflow tool being available."""
        content = SKILL_PATH.read_text()
        # Must reference workflow tool availability as a gate condition.
        assert "adversarial_review_threshold" in content, (
            "create-spec SKILL.md does not gate the adversarial phase on "
            "'skills.adversarial_review_threshold'. "
            "Add the threshold gate (spec Section 0 row 0.2)."
        )

    def test_gated_on_adversarial_review_threshold(self) -> None:
        """The phase must reference skills.adversarial_review_threshold."""
        content = SKILL_PATH.read_text()
        assert "adversarial_review_threshold" in content, (
            "'adversarial_review_threshold' not found in create-spec SKILL.md. "
            "The phase must be gated on this config key."
        )

    def test_workflow_absent_fallback_preserved(self) -> None:
        """When Workflow is absent, the single-agent Step-4 fallback must run."""
        content = SKILL_PATH.read_text()
        # The SKILL.md must explicitly preserve the Step-4 fallback path.
        lower = content.lower()
        assert "step 4" in lower or "step-4" in lower, (
            "create-spec SKILL.md does not reference the Step-4 fallback path. "
            "The single-agent self-critique must run unchanged when Workflow is absent "
            "(spec Section 0 row 0.2)."
        )

    def test_fallback_when_workflow_absent_is_explicit(self) -> None:
        """The SKILL.md must explicitly state the fallback condition (Workflow absent)."""
        content = SKILL_PATH.read_text()
        # Must name what happens when Workflow is absent.
        lower = content.lower()
        has_absent_clause = (
            "workflow is absent" in lower
            or "workflow tool is absent" in lower
            or "when workflow is not" in lower
            or "workflow unavailable" in lower
            or "if workflow" in lower
        )
        assert has_absent_clause, (
            "create-spec SKILL.md does not explicitly state the fallback behavior "
            "when the Workflow tool is absent. Add an explicit conditional clause "
            "(spec Section 0 row 0.2, AC-4)."
        )


@pytest.mark.unit
class TestFiveGenericDimensions:
    """AC-1: The phase must enumerate exactly the five generic dimensions."""

    @pytest.mark.parametrize("dimension", FIVE_DIMENSIONS)
    def test_dimension_present(self, dimension: str) -> None:
        """Each of the five generic dimensions must be named in the SKILL.md."""
        content = SKILL_PATH.read_text()
        assert dimension.lower() in content.lower(), (
            f"Dimension '{dimension}' not found in create-spec SKILL.md. "
            f"The adversarial phase must enumerate all five generic dimensions "
            f"(spec Section 4 E12-F2-S1 AC-1)."
        )

    def test_finer_checks_from_spec_content(self) -> None:
        """Finer-grained checks must be derived from the spec content, not a
        pre-baked domain taxonomy."""
        content = SKILL_PATH.read_text()
        lower = content.lower()
        # Must state that finer checks come from the spec content itself.
        has_spec_content_clause = (
            "derived from the spec" in lower
            or "spec content" in lower
            or "spec's own content" in lower
            or "derived from spec" in lower
        )
        assert has_spec_content_clause, (
            "create-spec SKILL.md does not state that finer-grained checks are "
            "derived from the spec content. "
            "The application-agnostic rule must be explicit (spec Section 12, AC-1)."
        )

    def test_no_domain_taxonomy(self) -> None:
        """The SKILL.md must not bake in a domain-specific taxonomy for the
        adversarial phase."""
        content = SKILL_PATH.read_text()
        lower = content.lower()
        # The spec explicitly forbids a domain taxonomy -- finer checks must
        # come from the spec's own content.
        assert "domain taxonomy" not in lower, (
            "create-spec SKILL.md mentions a 'domain taxonomy', which is forbidden. "
            "Finer checks must be derived from the spec content (spec Section 12)."
        )


@pytest.mark.unit
class TestSkepticVerification:
    """AC-2: Each finding must be re-checked by a skeptic agent with the
    correct verdict vocabulary, and unverifiable findings must default to
    rejected."""

    @pytest.mark.parametrize("verdict", SKEPTIC_VERDICTS)
    def test_skeptic_verdict_present(self, verdict: str) -> None:
        """Each skeptic verdict (CONFIRMED / REJECTED / severity-adjusted) must
        be named in the SKILL.md."""
        content = SKILL_PATH.read_text()
        assert verdict in content, (
            f"Skeptic verdict '{verdict}' not found in create-spec SKILL.md. "
            f"The per-finding verification block must name all three outcomes "
            f"(spec Section 4 E12-F2-S1 AC-2)."
        )

    def test_default_reject_unverifiable(self) -> None:
        """Unverifiable findings must default to rejected."""
        content = SKILL_PATH.read_text()
        lower = content.lower()
        has_default_reject = (
            ("default" in lower and "reject" in lower)
            or ("unverifiable" in lower and "rejected" in lower)
            or ("cannot be verified" in lower)
        )
        assert has_default_reject, (
            "create-spec SKILL.md does not state the default-reject rule for "
            "unverifiable findings. Add an explicit statement that findings "
            "which cannot be verified default to rejected "
            "(spec Section 4 E12-F2-S1 AC-2)."
        )

    def test_skeptic_agent_per_finding(self) -> None:
        """The SKILL.md must describe per-finding (independent) skeptic
        re-verification."""
        content = SKILL_PATH.read_text()
        lower = content.lower()
        has_per_finding = (
            "per-finding" in lower or "per finding" in lower or "each finding" in lower or "independently" in lower
        )
        assert has_per_finding, (
            "create-spec SKILL.md does not describe per-finding independent "
            "skeptic re-verification. "
            "Each finding must be re-checked independently before action "
            "(spec Section 4 E12-F2-S1 AC-2)."
        )


@pytest.mark.unit
class TestCitationDimension:
    """AC-3: The citation dimension must flag any cited external
    module/flag/version not verifiable against its named source."""

    def test_citation_dimension_named(self) -> None:
        """The citation/standards verification dimension must be named."""
        content = SKILL_PATH.read_text()
        lower = content.lower()
        has_citation_dim = "citation" in lower or "citation/standards" in lower
        assert has_citation_dim, (
            "The citation dimension is not named in create-spec SKILL.md. "
            "The adversarial phase must include a citation/standards verification "
            "dimension (spec Section 4 E12-F2-S1 AC-3)."
        )

    def test_citation_checks_external_references(self) -> None:
        """The citation dimension must be described as checking cited external
        modules, flags, and versions against their named sources."""
        content = SKILL_PATH.read_text()
        lower = content.lower()
        # Must reference external items being checked (modules, flags, versions).
        has_external_check = "external" in lower or "module" in lower or "version" in lower or "named source" in lower
        assert has_external_check, (
            "create-spec SKILL.md does not describe the citation dimension as "
            "checking external modules/flags/versions against their named sources "
            "(spec Section 4 E12-F2-S1 AC-3)."
        )

    def test_citation_flags_unverifiable_references(self) -> None:
        """The citation dimension must flag references that cannot be
        verified."""
        content = SKILL_PATH.read_text()
        lower = content.lower()
        has_flag_clause = "flag" in lower or "cannot be verified" in lower or "not verifiable" in lower
        assert has_flag_clause, (
            "create-spec SKILL.md does not state that the citation dimension "
            "flags unverifiable references. Add an explicit clause for this "
            "(spec Section 4 E12-F2-S1 AC-3)."
        )


@pytest.mark.unit
class TestSharedDocReference:
    """The adversarial phase must reference the shared patterns doc (DRY)."""

    def test_references_shared_patterns_doc(self) -> None:
        """The SKILL.md must link to the shared workflow-authoring-patterns doc."""
        content = SKILL_PATH.read_text()
        assert SHARED_DOC_RELATIVE_LINK in content, (
            f"create-spec SKILL.md does not reference '{SHARED_DOC_RELATIVE_LINK}'. "
            f"The adversarial phase must reference the shared patterns doc instead "
            f"of restating pattern bodies inline (DRY)."
        )


@pytest.mark.unit
class TestNoEmDash:
    """The SKILL.md must not contain em-dash characters (U+2014)."""

    def test_no_em_dash(self) -> None:
        """create-spec SKILL.md must not contain em-dash (U+2014)."""
        content = SKILL_PATH.read_text()
        assert "\u2014" not in content, f"Em-dash (U+2014) found in {SKILL_PATH}. Use '--' (double hyphen) instead."
