"""Structural pins for docs/onboarding.md chained-skill operator workflow (AC-191-9).

Verifies that docs/onboarding.md exists and describes the chained-skill operator
workflow: create-spec -> spec-to-backlog -> configure-devbench -> bootstrap-environment
-> make start.

Spec source: spec/devbench-self-improve.md section 4.6.6.
Issue: #191.
"""

from __future__ import annotations

from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).parent.parent.parent
ONBOARDING_DOC = REPO_ROOT / "docs" / "onboarding.md"


@pytest.mark.unit
class TestOnboardingDocExists:
    """docs/onboarding.md must exist as a readable file."""

    def test_onboarding_doc_file_exists(self) -> None:
        """docs/onboarding.md must exist (AC-191-9)."""
        assert ONBOARDING_DOC.is_file(), (
            "docs/onboarding.md must exist -- it describes the chained-skill operator "
            "workflow as required by AC-191-9 (spec section 4.6.6)."
        )

    def test_onboarding_doc_is_nonempty(self) -> None:
        """docs/onboarding.md must have meaningful content."""
        text = ONBOARDING_DOC.read_text(encoding="utf-8")
        assert len(text) > 500, (
            "docs/onboarding.md must have substantial content describing the chained-skill workflow (AC-191-9)."
        )


@pytest.mark.unit
class TestOnboardingDocChainedWorkflow:
    """docs/onboarding.md must describe every step in the chained-skill workflow."""

    def test_onboarding_doc_mentions_create_spec(self) -> None:
        """docs/onboarding.md must reference the create-spec skill."""
        text = ONBOARDING_DOC.read_text(encoding="utf-8")
        assert "create-spec" in text, (
            "docs/onboarding.md must describe the create-spec skill step in the "
            "chained-skill workflow (AC-191-9 -- chain: create-spec -> spec-to-backlog "
            "-> configure-devbench -> bootstrap-environment -> make start)."
        )

    def test_onboarding_doc_mentions_spec_to_backlog(self) -> None:
        """docs/onboarding.md must reference the spec-to-backlog skill."""
        text = ONBOARDING_DOC.read_text(encoding="utf-8")
        assert "spec-to-backlog" in text, (
            "docs/onboarding.md must describe the spec-to-backlog skill step in the chained-skill workflow (AC-191-9)."
        )

    def test_onboarding_doc_mentions_configure_devbench(self) -> None:
        """docs/onboarding.md must reference the configure-devbench skill."""
        text = ONBOARDING_DOC.read_text(encoding="utf-8")
        assert "configure-devbench" in text, (
            "docs/onboarding.md must describe the configure-devbench skill step in "
            "the chained-skill workflow (AC-191-9)."
        )

    def test_onboarding_doc_mentions_bootstrap_environment(self) -> None:
        """docs/onboarding.md must reference the bootstrap-environment skill."""
        text = ONBOARDING_DOC.read_text(encoding="utf-8")
        assert "bootstrap-environment" in text, (
            "docs/onboarding.md must describe the bootstrap-environment skill step in "
            "the chained-skill workflow (AC-191-9)."
        )

    def test_onboarding_doc_mentions_make_start(self) -> None:
        """docs/onboarding.md must reference 'make start' as the final step."""
        text = ONBOARDING_DOC.read_text(encoding="utf-8")
        assert "make start" in text, (
            "docs/onboarding.md must reference 'make start' as the final step that "
            "launches the orchestrator after the chained workflow completes (AC-191-9)."
        )

    def test_onboarding_doc_describes_workflow_order(self) -> None:
        """The workflow chain must appear in spec-prescribed order."""
        text = ONBOARDING_DOC.read_text(encoding="utf-8")
        pos_create = text.find("create-spec")
        pos_s2b = text.find("spec-to-backlog")
        pos_configure = text.find("configure-devbench")
        pos_bootstrap = text.find("bootstrap-environment")
        pos_make_start = text.find("make start")
        assert pos_create < pos_s2b, (
            "docs/onboarding.md must list create-spec before spec-to-backlog in the workflow chain (AC-191-9)."
        )
        assert pos_s2b < pos_configure or pos_configure < pos_bootstrap, (
            "docs/onboarding.md must describe configure-devbench and "
            "bootstrap-environment after spec-to-backlog (AC-191-9)."
        )
        assert pos_bootstrap < pos_make_start, (
            "docs/onboarding.md must describe bootstrap-environment before the final 'make start' step (AC-191-9)."
        )


@pytest.mark.unit
class TestOnboardingDocWorkedExample:
    """docs/onboarding.md must include a worked example walkthrough."""

    def test_onboarding_doc_has_worked_example_or_steps(self) -> None:
        """docs/onboarding.md must include worked steps or an example project."""
        text = ONBOARDING_DOC.read_text(encoding="utf-8")
        lower = text.lower()
        has_example = "example" in lower or "step" in lower or "walkthrough" in lower or "workflow" in lower
        assert has_example, (
            "docs/onboarding.md must include a worked example or step-by-step walkthrough "
            "so operators can follow along with a real project (AC-191-9 -- spec section "
            "4.6.6 describes this as an end-to-end walkthrough with worked example)."
        )

    def test_onboarding_doc_has_invocation_commands(self) -> None:
        """docs/onboarding.md must show actual CLI commands for the skill chain."""
        text = ONBOARDING_DOC.read_text(encoding="utf-8")
        has_commands = "claude run" in text or "devbench:" in text or "```" in text
        assert has_commands, (
            "docs/onboarding.md must show actual CLI commands (e.g., 'claude run "
            "devbench-authoring:create-spec') so operators know how to invoke each skill "
            "in the chain (AC-191-9)."
        )


@pytest.mark.unit
class TestOnboardingDocStructure:
    """docs/onboarding.md must be structured with markdown headings."""

    def test_onboarding_doc_has_markdown_headings(self) -> None:
        """docs/onboarding.md must use markdown headings for navigation."""
        text = ONBOARDING_DOC.read_text(encoding="utf-8")
        heading_lines = [line for line in text.splitlines() if line.startswith("#")]
        assert len(heading_lines) >= 3, (
            "docs/onboarding.md must have at least 3 markdown headings to structure "
            "the chained-skill workflow into navigable sections (AC-191-9)."
        )

    def test_onboarding_doc_has_cross_references(self) -> None:
        """docs/onboarding.md must include cross-references to companion docs."""
        text = ONBOARDING_DOC.read_text(encoding="utf-8")
        has_links = "](" in text or "docs/" in text or "zero-to-ready" in text
        assert has_links, (
            "docs/onboarding.md must include cross-references to companion docs "
            "(e.g., per-skill quickstart docs, zero-to-ready.md) so operators can "
            "navigate to deeper reference material (AC-191-9)."
        )

    def test_onboarding_doc_no_em_dashes(self) -> None:
        """docs/onboarding.md must not contain em-dash characters (U+2014)."""
        text = ONBOARDING_DOC.read_text(encoding="utf-8")
        assert "\u2014" not in text, (
            "docs/onboarding.md must not contain em-dash characters (U+2014). "
            "Use '--' (double hyphen) instead. "
            "This rule applies to all doc files authored alongside work units."
        )

    def test_onboarding_doc_references_skill_docs(self) -> None:
        """docs/onboarding.md must reference the per-skill quickstart docs."""
        text = ONBOARDING_DOC.read_text(encoding="utf-8")
        lower = text.lower()
        has_skill_refs = "docs/skills" in text or "skills/" in text or "quickstart" in lower
        assert has_skill_refs, (
            "docs/onboarding.md must reference the per-skill quickstart docs under "
            "docs/skills/ so operators can navigate to per-skill reference material "
            "(AC-191-9 -- the onboarding doc is the hub for the skill chain)."
        )


@pytest.mark.unit
class TestOnboardingDocPrerequisites:
    """docs/onboarding.md must document prerequisites for the chained workflow."""

    def test_onboarding_doc_mentions_prerequisites(self) -> None:
        """docs/onboarding.md must describe what operators need before starting."""
        text = ONBOARDING_DOC.read_text(encoding="utf-8")
        lower = text.lower()
        has_prereqs = (
            "prerequisite" in lower or "before" in lower or "requires" in lower or "install" in lower or "need" in lower
        )
        assert has_prereqs, (
            "docs/onboarding.md must document the prerequisites operators need before "
            "running the chained-skill workflow (AC-191-9)."
        )

    def test_onboarding_doc_mentions_plugin(self) -> None:
        """docs/onboarding.md must mention the devbench plugin."""
        text = ONBOARDING_DOC.read_text(encoding="utf-8")
        lower = text.lower()
        assert "plugin" in lower, (
            "docs/onboarding.md must mention the devbench plugin that makes the skill chain available (AC-191-9)."
        )
