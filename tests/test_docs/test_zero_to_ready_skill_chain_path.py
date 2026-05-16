"""Structural pins for docs/zero-to-ready.md chained-skill setup path (AC-191-9).

Verifies that docs/zero-to-ready.md:
- Offers two setup paths: manual (existing) and chained-skill (new).
- Mentions the chained-skill alternative and links to docs/onboarding.md.
- Describes that operators can use the skill chain instead of manual step-by-step setup.

Spec source: spec/devbench-self-improve.md section 4.6.6.
Issue: #191.
"""

from __future__ import annotations

from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).parent.parent.parent
ZERO_TO_READY_DOC = REPO_ROOT / "docs" / "zero-to-ready.md"


@pytest.mark.unit
class TestZeroToReadyTwoSetupPaths:
    """docs/zero-to-ready.md must offer both manual and skill-driven setup paths."""

    def _text(self) -> str:
        return ZERO_TO_READY_DOC.read_text(encoding="utf-8")

    def test_zero_to_ready_mentions_skill_driven_path(self) -> None:
        """docs/zero-to-ready.md must mention the skill-driven setup path (AC-191-9).

        Spec section 4.6.6: 'docs/zero-to-ready.md restructured to mention both manual
        and skill-driven setup.'
        """
        text = self._text()
        lower = text.lower()
        has_skill_path = "skill" in lower and (
            "chain" in lower or "onboarding" in lower or "create-spec" in lower or "spec-to-backlog" in lower
        )
        assert has_skill_path, (
            "docs/zero-to-ready.md must describe the skill-driven setup path as an "
            "alternative to the manual step-by-step instructions. Spec section 4.6.6 "
            "requires the doc to be 'restructured to mention both manual and skill-driven "
            "setup' (AC-191-9)."
        )

    def test_zero_to_ready_links_to_onboarding_doc(self) -> None:
        """docs/zero-to-ready.md must link to docs/onboarding.md (AC-191-9)."""
        text = self._text()
        assert "onboarding.md" in text, (
            "docs/zero-to-ready.md must link to docs/onboarding.md -- the chained-skill "
            "operator workflow hub. Spec section 4.6.6 requires zero-to-ready.md to "
            "cross-link to docs/onboarding.md (AC-191-9)."
        )

    def test_zero_to_ready_distinguishes_manual_vs_skill_paths(self) -> None:
        """docs/zero-to-ready.md must clearly distinguish the manual vs skill paths (AC-191-9)."""
        text = self._text()
        lower = text.lower()
        # The doc should have both 'manual' and 'skill' (or equivalent) to show two distinct paths.
        has_manual_path = "manual" in lower or "step-by-step" in lower or "step 1" in lower
        has_skill_path = "skill" in lower or "onboarding.md" in lower
        assert has_manual_path and has_skill_path, (
            "docs/zero-to-ready.md must present both the manual setup path (existing "
            "step-by-step guide) and the skill-driven setup path (chained marketplace "
            "skills). Spec section 4.6.6: 'restructured to mention both manual and "
            "skill-driven setup' (AC-191-9)."
        )


@pytest.mark.unit
class TestZeroToReadyOnboardingSkillsNamed:
    """docs/zero-to-ready.md must name the onboarding skills when describing the skill path."""

    def _text(self) -> str:
        return ZERO_TO_READY_DOC.read_text(encoding="utf-8")

    def test_zero_to_ready_names_at_least_one_onboarding_skill(self) -> None:
        """docs/zero-to-ready.md must name at least one onboarding skill (AC-191-9)."""
        text = self._text()
        skill_names = [
            "create-spec",
            "spec-to-backlog",
            "configure-devbench",
            "bootstrap-environment",
        ]
        named = [s for s in skill_names if s in text]
        assert named, (
            "docs/zero-to-ready.md must name at least one of the four onboarding skills "
            "(create-spec, spec-to-backlog, configure-devbench, bootstrap-environment) "
            "when describing the skill-driven setup path (AC-191-9)."
        )

    def test_zero_to_ready_no_em_dashes(self) -> None:
        """docs/zero-to-ready.md must not introduce em-dash characters (U+2014)."""
        text = self._text()
        assert "\u2014" not in text, (
            "docs/zero-to-ready.md must not contain em-dash characters (U+2014). "
            "Use '--' (double hyphen) instead (Code Standards rule 8)."
        )
