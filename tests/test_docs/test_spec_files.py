"""Structural pins for spec / future-work files under docs/ (ADR-10 slice H)."""

from __future__ import annotations

from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).parent.parent.parent
SPEC_ATTN = REPO_ROOT / "docs" / "spec-operator-attention-alerts.md"
ROADMAP = REPO_ROOT / "docs" / "roadmap.md"


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

    def test_roadmap_links_to_spec_file(self) -> None:
        """roadmap.md carries a bullet pointing at the spec file so it does not get lost."""
        text = ROADMAP.read_text(encoding="utf-8")
        assert "spec-operator-attention-alerts.md" in text, (
            "docs/roadmap.md must link to the spec file so a reader searching "
            "the roadmap discovers the future-work design."
        )

    def test_adr_10_links_to_spec_file(self) -> None:
        """ADR-10 mentions the spec in its Downstream observability subsection."""
        adr_10 = REPO_ROOT / "docs" / "adr" / "10-multi-target-proposal-wiring.md"
        text = adr_10.read_text(encoding="utf-8")
        assert "spec-operator-attention-alerts.md" in text, (
            "ADR-10 must cross-reference the spec file so the design context is discoverable."
        )
