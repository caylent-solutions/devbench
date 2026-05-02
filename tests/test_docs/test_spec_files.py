"""Structural pins for spec / future-work files under docs/ (ADR-10 slice H)."""

from __future__ import annotations

from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).parent.parent.parent
SPEC_ATTN = REPO_ROOT / "docs" / "spec-operator-attention-alerts.md"
CANONICAL_AC = REPO_ROOT / "docs" / "acceptance-criteria-canonical.md"


@pytest.mark.unit
class TestOperatorAttentionAlertSpec:
    """ADR-10 slice H: the spec file exists under docs/ and carries the expected structure."""

    def test_operator_attention_alert_spec_exists_under_docs(self) -> None:
        assert SPEC_ATTN.is_file(), (
            "docs/spec-operator-attention-alerts.md must exist -- roadmap.md and ADR-10 both link to it."
        )

    def test_spec_file_lists_three_design_options(self) -> None:
        """The spec intentionally surfaces three implementation options + their trade-offs."""
        text = SPEC_ATTN.read_text(encoding="utf-8")
        assert "Option A" in text
        assert "Option B" in text
        assert "Option C" in text

    def test_spec_file_documents_open_questions(self) -> None:
        """Open questions are the hand-off to the implementer."""
        text = SPEC_ATTN.read_text(encoding="utf-8")
        assert "Open questions" in text, (
            "The spec must enumerate open questions so the implementer knows what to decide."
        )

    def test_spec_file_references_the_two_classifiers_it_composes(self) -> None:
        """The spec must name ``classify_proposed_task`` and ``classify_blocked_task`` as the building blocks."""
        text = SPEC_ATTN.read_text(encoding="utf-8")
        assert "classify_proposed_task" in text
        assert "classify_blocked_task" in text

    def test_adr_10_links_to_spec_file(self) -> None:
        """ADR-10 mentions the spec in its Downstream observability subsection."""
        adr_10 = REPO_ROOT / "docs" / "adr" / "10-multi-target-proposal-wiring.md"
        text = adr_10.read_text(encoding="utf-8")
        assert "spec-operator-attention-alerts.md" in text, (
            "ADR-10 must cross-reference the spec file so the design context is discoverable."
        )


@pytest.mark.unit
class TestCanonicalAcVendoredCarveOut:
    """Pins that acceptance-criteria-canonical.md exists and contains the vendored
    code carve-out section documenting acceptable AC-FINAL-004 / AC-FINAL-008
    wording for repos with third-party vendored trees."""

    def test_canonical_ac_file_exists(self) -> None:
        assert CANONICAL_AC.is_file(), (
            "docs/acceptance-criteria-canonical.md must exist -- it is the SOURCE OF TRUTH "
            "for the AC-FINAL set and is referenced by agent prompts and backlog generators."
        )

    def test_canonical_ac_contains_vendored_carve_out_section(self) -> None:
        """The file must contain a 'Vendored code carve-out' section."""
        text = CANONICAL_AC.read_text(encoding="utf-8")
        assert "vendored" in text.lower(), (
            "docs/acceptance-criteria-canonical.md must contain a 'Vendored code carve-out' "
            "section documenting the acceptable AC wording for repos with vendored trees."
        )
        assert "carve-out" in text.lower() or "carve out" in text.lower(), (
            "docs/acceptance-criteria-canonical.md must use the term 'carve-out' in the "
            "vendored-code section so reviewers can find it by searching."
        )

    def test_canonical_ac_vendored_section_references_ac_final_004(self) -> None:
        """The vendored section must document the mypy carve-out (AC-FINAL-004)."""
        text = CANONICAL_AC.read_text(encoding="utf-8")
        assert "AC-FINAL-004" in text, (
            "docs/acceptance-criteria-canonical.md must reference AC-FINAL-004 in the "
            "vendored carve-out section to document the mypy gate exception."
        )

    def test_canonical_ac_vendored_section_references_ac_final_008(self) -> None:
        """The vendored section must document the bandit carve-out (AC-FINAL-008)."""
        text = CANONICAL_AC.read_text(encoding="utf-8")
        assert "AC-FINAL-008" in text, (
            "docs/acceptance-criteria-canonical.md must reference AC-FINAL-008 in the "
            "vendored carve-out section to document the bandit gate exception."
        )

    def test_canonical_ac_vendored_section_distinguishes_carve_out_from_bypass(self) -> None:
        """The vendored section must explicitly state that carve-outs are NOT bypass
        annotations -- they are scope demarcations at the build-config layer."""
        text = CANONICAL_AC.read_text(encoding="utf-8")
        # The section must contrast carve-outs with bypass annotations.
        assert "noqa" in text or "nosec" in text, (
            "docs/acceptance-criteria-canonical.md must name '# noqa' or '# nosec' in the "
            "vendored section to make clear that those inline bypasses are prohibited."
        )
        # It must explain the scope-demarcation vs bypass distinction.
        assert "build-config" in text or "build config" in text or "exclude_dirs" in text, (
            "docs/acceptance-criteria-canonical.md must explain that vendored carve-outs "
            "are implemented at the build-config layer (not via inline suppression comments)."
        )
