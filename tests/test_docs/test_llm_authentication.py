"""Structural pins for docs/llm-authentication.md authentication-path sections.

Verifies that the llm-authentication.md doc documents each authentication path:

- Claude Pro / Max subscription (via Claude Code OAuth)
- AWS Bedrock
- Per-agent model overrides

Spec source: spec/devbench-self-improve.md section 4.5.
"""

from __future__ import annotations

from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).parent.parent.parent
DOC = REPO_ROOT / "docs" / "llm-authentication.md"


def _read_doc() -> str:
    return DOC.read_text(encoding="utf-8")


def _extract_section(text: str, heading: str) -> str:
    """Return content of the section starting at ``heading`` up to the next same-level heading."""
    idx = text.find(heading)
    if idx == -1:
        return ""
    section_text = text[idx:]
    level = len(heading.split(" ", maxsplit=1)[0])
    marker = "\n" + "#" * level + " "
    next_idx = section_text.find(marker, 1)
    if next_idx != -1:
        return section_text[:next_idx]
    return section_text


@pytest.mark.unit
class TestLlmAuthDocExists:
    """The doc file must exist and have a valid top-level heading."""

    def test_doc_file_exists(self) -> None:
        """docs/llm-authentication.md must exist at the canonical path."""
        assert DOC.is_file(), (
            "docs/llm-authentication.md must exist -- it is the primary authentication "
            "reference for all DevBench operators."
        )

    def test_no_em_dash(self) -> None:
        """The doc must use -- (double hyphen) instead of the em-dash character."""
        text = _read_doc()
        assert "\u2014" not in text, (
            "docs/llm-authentication.md must not contain em-dash (U+2014) characters. "
            "Use -- (double hyphen) instead. "
            "(devbench validate-backlog rule 10 / spec critical rule 8)."
        )


@pytest.mark.unit
class TestSubscriptionAuthSection:
    """Option 1 (Claude Pro / Max subscription via OAuth) section must exist."""

    def test_pro_max_subscription_section_exists(self) -> None:
        """The Option 1 section must exist and cover Pro / Max subscription."""
        text = _read_doc()
        option1_section = _extract_section(text, "## Option 1:")
        assert option1_section, (
            "docs/llm-authentication.md must have an 'Option 1' section for "
            "Claude Code OAuth (Pro / Max subscription) authentication."
        )


@pytest.mark.unit
class TestBedrockAuthSection:
    """Option 2 (AWS Bedrock) section must exist."""

    def test_bedrock_section_exists(self) -> None:
        """The Option 2 (Bedrock) section must exist."""
        text = _read_doc()
        option2_section = _extract_section(text, "## Option 2:")
        assert option2_section, (
            "docs/llm-authentication.md must have an 'Option 2' section for AWS Bedrock authentication."
        )


@pytest.mark.unit
class TestPerAgentModelOverridesSection:
    """The per-agent model overrides section must exist."""

    def test_per_agent_section_exists(self) -> None:
        """The per-agent model overrides section must exist."""
        text = _read_doc()
        per_agent_section = _extract_section(text, "## Per-agent model overrides")
        assert per_agent_section, "docs/llm-authentication.md must have a 'Per-agent model overrides' section."

    def test_review_supervisor_is_not_described_as_fan_out_coordinator(self) -> None:
        """Post-flatten (ADR-33), review-supervisor is a non-spawning aggregator.

        review-supervisor no longer declares an Agent tool and cannot spawn the
        four review_team judges, so describing it as a 'fan-out coordinator'
        (a role requiring Agent-tool reliability) is stale and misleading to
        an operator tuning the per-agent model overrides.
        """
        text = _read_doc()
        per_agent_section = _extract_section(text, "## Per-agent model overrides")
        assert "fan-out coordinator" not in per_agent_section, (
            "docs/llm-authentication.md must not describe review-supervisor as a "
            "'fan-out coordinator' -- post-flatten (ADR-33) it is a non-spawning "
            "aggregator with no Agent tool."
        )
        assert "aggregat" in per_agent_section.lower(), (
            "docs/llm-authentication.md's per-agent model overrides section must "
            "describe review-supervisor's post-flatten role as read-only "
            "aggregation of already-persisted verdicts."
        )
