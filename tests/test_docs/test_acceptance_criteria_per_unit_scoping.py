"""Documentation guard tests for the per-unit / epic-capstone gate split.

The canonical AC-FINAL set previously gated EVERY Python leaf Task on the WHOLE
``tests/unit`` suite (AC-FINAL-005) and global coverage (AC-FINAL-014). A single
flaky / order-dependent sibling test could then block an otherwise-complete,
unrelated unit non-deterministically. These tests guard the documented fix:

- The per-unit AC-FINAL-005/006/007 gate is SCOPED to the unit's OWN tests
  (the test files in the unit's Changes Manifest), not the full suite.
- The per-unit AC-FINAL-014 coverage gate is scoped to the unit's OWN modules.
- The FULL-suite green + global-coverage checks are moved to separate
  epic-capstone / CI gate AC IDs (AC-FINAL-016 / AC-FINAL-017) that apply only
  to the capstone task, never to every leaf unit.
- The split is documented (rationale: one unit is never hostage to another's
  tests) in both the canonical doc and the spec-to-backlog skill.

These are content/contract guards: they keep the authoring guidance and the
canonical source of truth in sync with the verify-ac deterministic-gate fix.
"""

from __future__ import annotations

from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).parent.parent.parent
CANONICAL_DOC = REPO_ROOT / "docs" / "acceptance-criteria-canonical.md"
CLI_REFERENCE_DOC = REPO_ROOT / "docs" / "cli-reference.md"
SPEC_TO_BACKLOG_SKILL = (
    REPO_ROOT / "plugin-authoring" / "devbench-authoring" / "skills" / "spec-to-backlog" / "SKILL.md"
)

_EM_DASH = "—"


@pytest.fixture
def canonical_text() -> str:
    assert CANONICAL_DOC.is_file(), "docs/acceptance-criteria-canonical.md must exist."
    return CANONICAL_DOC.read_text(encoding="utf-8")


@pytest.fixture
def skill_text() -> str:
    assert SPEC_TO_BACKLOG_SKILL.is_file(), "spec-to-backlog/SKILL.md must exist."
    return SPEC_TO_BACKLOG_SKILL.read_text(encoding="utf-8")


@pytest.mark.unit
class TestPerUnitGateScopedToOwnTests:
    """AC-FINAL-005/006/007 gate the unit's OWN tests, not the full suite."""

    def test_ac_final_005_scoped_to_own_tests(self, canonical_text: str) -> None:
        assert "AC-FINAL-005" in canonical_text
        assert "own test" in canonical_text.lower(), (
            "AC-FINAL-005 must be documented as scoped to the unit's OWN test files "
            "(the test paths in the unit's Changes Manifest), not the full tests/unit suite."
        )

    def test_full_suite_phrase_attributed_to_capstone_not_per_unit(self, canonical_text: str) -> None:
        assert "AC-FINAL-016" in canonical_text, (
            "A capstone AC-FINAL-016 must own the FULL-suite green check so it is not a per-unit block."
        )

    def test_global_coverage_moved_to_capstone(self, canonical_text: str) -> None:
        assert "AC-FINAL-017" in canonical_text, (
            "A capstone AC-FINAL-017 must own the GLOBAL coverage / no-regression check."
        )


@pytest.mark.unit
class TestCapstoneGateDocumented:
    """The per-unit vs. epic-capstone / CI gate split is explained."""

    def test_capstone_section_present(self, canonical_text: str) -> None:
        lowered = canonical_text.lower()
        assert "capstone" in lowered, (
            "The canonical doc must document an epic-capstone / CI gate that owns the "
            "full-suite and global-coverage checks."
        )

    def test_rationale_one_unit_not_hostage(self, canonical_text: str) -> None:
        lowered = canonical_text.lower()
        assert ("order-dependent" in lowered) or ("flaky" in lowered), (
            "The doc must explain the flaky / order-dependent rationale for splitting the "
            "per-unit gate from the full-suite capstone gate."
        )

    def test_capstone_ids_not_per_unit_applicability(self, canonical_text: str) -> None:
        assert "Epic capstone" in canonical_text or "epic-capstone" in canonical_text.lower(), (
            "AC-FINAL-016/017 must declare epic-capstone applicability so leaf Tasks do not "
            "inherit the full-suite block."
        )


@pytest.mark.unit
class TestExistingIdsNotRepurposed:
    """Lifecycle rule: existing AC-FINAL IDs are refined in scope, not renumbered/repurposed."""

    def test_ids_005_through_015_still_present(self, canonical_text: str) -> None:
        for n in range(1, 16):
            ident = f"AC-FINAL-{n:03d}"
            assert ident in canonical_text, f"{ident} must still be defined (IDs must not be renumbered)."

    def test_capstone_ids_are_new_numbers(self, canonical_text: str) -> None:
        assert "AC-FINAL-016" in canonical_text
        assert "AC-FINAL-017" in canonical_text


@pytest.mark.unit
class TestSkillGuidanceScopesPerUnitGate:
    """The spec-to-backlog skill instructs authors to scope the per-unit gate."""

    def test_skill_mentions_per_unit_scoping(self, skill_text: str) -> None:
        lowered = skill_text.lower()
        assert "own test" in lowered, (
            "spec-to-backlog SKILL.md must instruct authors that the per-unit AC-FINAL-005 "
            "pytest gate is scoped to the unit's OWN tests, not the full suite."
        )

    def test_skill_mentions_capstone_split(self, skill_text: str) -> None:
        lowered = skill_text.lower()
        assert "capstone" in lowered, (
            "spec-to-backlog SKILL.md must reference the epic-capstone / CI gate that owns the "
            "full-suite green + global-coverage check (AC-FINAL-016/017)."
        )

    def test_skill_references_capstone_ac_ids(self, skill_text: str) -> None:
        assert "AC-FINAL-016" in skill_text and "AC-FINAL-017" in skill_text, (
            "spec-to-backlog SKILL.md must name the new capstone AC IDs so authors place the "
            "full-suite checks on the capstone task, not every leaf."
        )


@pytest.mark.unit
class TestVerifyAcDeterministicGateDocumented:
    """cli-reference.md documents the verify-ac deterministic ordering seed."""

    @pytest.fixture
    def cli_ref_text(self) -> str:
        assert CLI_REFERENCE_DOC.is_file(), "docs/cli-reference.md must exist."
        return CLI_REFERENCE_DOC.read_text(encoding="utf-8")

    def test_env_var_documented(self, cli_ref_text: str) -> None:
        assert "DEVBENCH_VERIFY_AC_PYTEST_SEED" in cli_ref_text, (
            "cli-reference.md verify-ac section must document the DEVBENCH_VERIFY_AC_PYTEST_SEED "
            "deterministic-gate env var."
        )

    def test_mentions_pytest_randomly_determinism(self, cli_ref_text: str) -> None:
        lowered = cli_ref_text.lower()
        assert "pytest-randomly" in lowered or "randomly-seed" in lowered, (
            "cli-reference.md verify-ac section must explain the pinned pytest-randomly seed."
        )


@pytest.mark.unit
class TestItem3DeferralDocumented:
    """The deferred flaky-vs-real discriminator (item 3) is documented as not shipped."""

    def test_deferral_note_present(self, canonical_text: str) -> None:
        lowered = canonical_text.lower()
        assert "deferred" in lowered and "isolation" in lowered, (
            "The canonical doc must record that the automatic flaky-vs-real "
            "re-run-in-isolation discriminator is deferred / not implemented."
        )


@pytest.mark.unit
class TestNoEmDash:
    """No em-dash (U+2014) introduced (AC-FINAL-012)."""

    def test_canonical_doc_has_no_em_dash(self, canonical_text: str) -> None:
        assert _EM_DASH not in canonical_text, "No em-dash (U+2014) allowed in the canonical doc; use '--'."
