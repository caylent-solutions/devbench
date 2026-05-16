"""Structural pins for README.md onboarding-skills feature list (AC-191-9).

Verifies that README.md:
- Mentions the four new onboarding marketplace skills (create-spec, spec-to-backlog,
  configure-devbench, bootstrap-environment).
- Links to docs/onboarding.md as the chained-skill operator workflow hub.
- Describes that operators can use the skill chain as an alternative to manual setup.

Spec source: spec/devbench-self-improve.md section 4.6.6.
Issue: #191.
"""

from __future__ import annotations

from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).parent.parent.parent
README = REPO_ROOT / "README.md"


@pytest.mark.unit
class TestReadmeOnboardingSkillsMentioned:
    """README.md must mention the four new onboarding skills in its feature list."""

    def _text(self) -> str:
        return README.read_text(encoding="utf-8")

    def test_readme_mentions_create_spec_skill(self) -> None:
        """README.md must mention the create-spec marketplace skill (AC-191-9)."""
        text = self._text()
        assert "create-spec" in text, (
            "README.md must mention the 'create-spec' marketplace skill in its feature "
            "list -- spec section 4.6.6 adds per-skill quickstart docs and the README "
            "feature list must reflect the new capabilities (AC-191-9)."
        )

    def test_readme_mentions_spec_to_backlog_skill(self) -> None:
        """README.md must mention the spec-to-backlog marketplace skill (AC-191-9)."""
        text = self._text()
        assert "spec-to-backlog" in text, (
            "README.md must mention the 'spec-to-backlog' marketplace skill "
            "(AC-191-9, spec section 4.6.6)."
        )

    def test_readme_mentions_configure_devbench_skill(self) -> None:
        """README.md must mention the configure-devbench marketplace skill (AC-191-9)."""
        text = self._text()
        assert "configure-devbench" in text, (
            "README.md must mention the 'configure-devbench' marketplace skill "
            "(AC-191-9, spec section 4.6.6)."
        )

    def test_readme_mentions_bootstrap_environment_skill(self) -> None:
        """README.md must mention the bootstrap-environment marketplace skill (AC-191-9)."""
        text = self._text()
        assert "bootstrap-environment" in text, (
            "README.md must mention the 'bootstrap-environment' marketplace skill "
            "(AC-191-9, spec section 4.6.6)."
        )


@pytest.mark.unit
class TestReadmeOnboardingMdLink:
    """README.md must link to docs/onboarding.md."""

    def _text(self) -> str:
        return README.read_text(encoding="utf-8")

    def test_readme_links_to_onboarding_doc(self) -> None:
        """README.md must contain a link to docs/onboarding.md (AC-191-9)."""
        text = self._text()
        assert "docs/onboarding.md" in text or "onboarding.md" in text, (
            "README.md must link to docs/onboarding.md, which describes the chained-skill "
            "operator workflow (create-spec -> spec-to-backlog -> configure-devbench -> "
            "bootstrap-environment -> make start). Spec section 4.6.6 requires the README "
            "feature list to be updated to reflect this new capability (AC-191-9)."
        )

    def test_readme_onboarding_link_uses_markdown_syntax(self) -> None:
        """README.md link to onboarding.md must use markdown link syntax (AC-191-9)."""
        text = self._text()
        has_md_link = "(docs/onboarding.md)" in text or ("[" in text and "onboarding" in text)
        assert has_md_link, (
            "README.md must link to docs/onboarding.md using markdown link syntax "
            "(e.g., [docs/onboarding.md](docs/onboarding.md)) so operators can navigate "
            "to the chained-skill workflow guide (AC-191-9)."
        )


@pytest.mark.unit
class TestReadmeSkillChainDescription:
    """README.md must describe the skill chain as a setup option."""

    def _text(self) -> str:
        return README.read_text(encoding="utf-8")

    def test_readme_describes_skill_chain_as_setup_path(self) -> None:
        """README.md must describe the skill chain as an alternative to manual setup (AC-191-9)."""
        text = self._text()
        lower = text.lower()
        has_skill_setup_context = (
            "skill" in lower
            and ("setup" in lower or "onboard" in lower or "chain" in lower or "zero to" in lower)
        )
        assert has_skill_setup_context, (
            "README.md must describe the marketplace skill chain as a way to set up or onboard "
            "DevBench -- spec section 4.6.6 requires the README to reflect both manual and "
            "skill-driven setup paths (AC-191-9)."
        )

    def test_readme_no_em_dashes(self) -> None:
        """README.md must not contain em-dash characters (U+2014)."""
        text = self._text()
        assert "\u2014" not in text, (
            "README.md must not contain em-dash characters (U+2014). "
            "Use '--' (double hyphen) instead (Code Standards rule 8)."
        )
