"""Structural pins for docs/llm-authentication.md quota_handling cross-references.

Verifies that the llm-authentication.md doc cross-references docs/quota-handling.md
for each authentication path:

- Claude Pro / Max subscription (via Claude Code OAuth)
- API key (direct Anthropic API)
- AWS Bedrock

Each auth-path section must contain a link or reference to docs/quota-handling.md
and its relevant detection mode (subscription_rate_limit, sdk_credit_exhausted /
api_billing_error, bedrock_throttle).

Spec source: spec/devbench-self-improve.md section 4.5.
Issue: #193.
AC: AC-193-13.
Companion: tests/test_docs/test_quota_handling.py (quota-handling.md structural pins).
"""

from __future__ import annotations

from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).parent.parent.parent
DOC = REPO_ROOT / "docs" / "llm-authentication.md"
QUOTA_DOC = REPO_ROOT / "docs" / "quota-handling.md"


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

    def test_quota_doc_file_exists(self) -> None:
        """docs/quota-handling.md must exist to be cross-referenceable."""
        assert QUOTA_DOC.is_file(), (
            "docs/quota-handling.md must exist -- it is the target of cross-references "
            "from docs/llm-authentication.md (E5-F7-S1-T1)."
        )


@pytest.mark.unit
class TestLlmAuthQuotaHandlingCrossReference:
    """The doc must cross-reference docs/quota-handling.md at the document level."""

    def test_quota_handling_link_present(self) -> None:
        """docs/llm-authentication.md must link to docs/quota-handling.md."""
        text = _read_doc()
        has_link = "quota-handling" in text.lower() or "quota_handling" in text.lower()
        assert has_link, (
            "docs/llm-authentication.md must contain a link or reference to "
            "docs/quota-handling.md so operators discover the quota wait-and-resume "
            "playbook from each auth path (AC-193-13)."
        )

    def test_quota_handling_file_resolves(self) -> None:
        """The referenced docs/quota-handling.md must exist on disk."""
        assert QUOTA_DOC.is_file(), (
            "docs/quota-handling.md is cross-referenced from docs/llm-authentication.md "
            "but does not exist on disk."
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
class TestSubscriptionAuthQuotaCrossRef:
    """Option 1 (Claude Pro / Max subscription via OAuth) must reference quota-handling.md."""

    def test_subscription_section_references_quota_handling(self) -> None:
        """The Claude Code OAuth section must cross-reference quota-handling.md."""
        text = _read_doc()
        option1_section = _extract_section(text, "## Option 1:")
        has_quota_ref = (
            "quota-handling" in option1_section.lower()
            or "quota_handling" in option1_section.lower()
        )
        assert has_quota_ref, (
            "The 'Option 1' (Claude Code OAuth / subscription) section of "
            "docs/llm-authentication.md must cross-reference docs/quota-handling.md "
            "for the subscription_rate_limit wait-and-resume behavior (AC-193-13)."
        )

    def test_subscription_rate_limit_mode_mentioned(self) -> None:
        """The subscription section must mention the subscription_rate_limit detect mode."""
        text = _read_doc()
        option1_section = _extract_section(text, "## Option 1:")
        has_mode = (
            "subscription_rate_limit" in option1_section
            or "SubscriptionRateLimit" in option1_section
        )
        assert has_mode, (
            "The 'Option 1' section of docs/llm-authentication.md must mention the "
            "'subscription_rate_limit' detect mode so operators know which quota "
            "error class applies to their auth path (AC-193-13 / quota-handling.md)."
        )

    def test_api_key_credit_modes_mentioned(self) -> None:
        """The subscription section must mention the API key credit / billing error quota modes."""
        text = _read_doc()
        option1_section = _extract_section(text, "## Option 1:")
        has_api_key_mode = (
            "sdk_credit_exhausted" in option1_section
            or "api_billing_error" in option1_section
        )
        assert has_api_key_mode, (
            "The 'Option 1' section of docs/llm-authentication.md must mention the "
            "'sdk_credit_exhausted' or 'api_billing_error' detect modes so that operators "
            "using a direct Anthropic API key know which quota error class applies to them "
            "(AC-193-13 / quota-handling.md)."
        )

    def test_pro_max_subscription_section_exists(self) -> None:
        """The Option 1 section must exist and cover Pro / Max subscription."""
        text = _read_doc()
        option1_section = _extract_section(text, "## Option 1:")
        assert option1_section, (
            "docs/llm-authentication.md must have an 'Option 1' section for "
            "Claude Code OAuth (Pro / Max subscription) authentication."
        )


@pytest.mark.unit
class TestBedrockAuthQuotaCrossRef:
    """Option 2 (AWS Bedrock) section must reference quota-handling.md."""

    def test_bedrock_section_references_quota_handling(self) -> None:
        """The AWS Bedrock section must cross-reference quota-handling.md."""
        text = _read_doc()
        option2_section = _extract_section(text, "## Option 2:")
        has_quota_ref = (
            "quota-handling" in option2_section.lower()
            or "quota_handling" in option2_section.lower()
        )
        assert has_quota_ref, (
            "The 'Option 2' (AWS Bedrock) section of docs/llm-authentication.md "
            "must cross-reference docs/quota-handling.md for the bedrock_throttle "
            "wait-and-resume behavior (AC-193-13)."
        )

    def test_bedrock_throttle_mode_mentioned(self) -> None:
        """The Bedrock section must mention the bedrock_throttle detect mode."""
        text = _read_doc()
        option2_section = _extract_section(text, "## Option 2:")
        has_mode = (
            "bedrock_throttle" in option2_section
            or "BedrockThrottle" in option2_section
        )
        assert has_mode, (
            "The 'Option 2' (AWS Bedrock) section of docs/llm-authentication.md "
            "must mention the 'bedrock_throttle' detect mode so operators know "
            "which quota error class applies to their auth path (AC-193-13 / quota-handling.md)."
        )

    def test_bedrock_section_exists(self) -> None:
        """The Option 2 (Bedrock) section must exist."""
        text = _read_doc()
        option2_section = _extract_section(text, "## Option 2:")
        assert option2_section, (
            "docs/llm-authentication.md must have an 'Option 2' section for "
            "AWS Bedrock authentication."
        )


@pytest.mark.unit
class TestPerAgentModelOverridesQuotaCrossRef:
    """The per-agent model overrides section must reference quota-handling.md."""

    def test_per_agent_section_references_quota_handling(self) -> None:
        """The per-agent model overrides section must cross-reference quota-handling.md."""
        text = _read_doc()
        per_agent_section = _extract_section(text, "## Per-agent model overrides")
        has_quota_ref = (
            "quota-handling" in per_agent_section.lower()
            or "quota_handling" in per_agent_section.lower()
        )
        assert has_quota_ref, (
            "The 'Per-agent model overrides' section of docs/llm-authentication.md "
            "must cross-reference docs/quota-handling.md since model selection "
            "is a quota management strategy (AC-193-13)."
        )

    def test_per_agent_section_exists(self) -> None:
        """The per-agent model overrides section must exist."""
        text = _read_doc()
        per_agent_section = _extract_section(text, "## Per-agent model overrides")
        assert per_agent_section, (
            "docs/llm-authentication.md must have a 'Per-agent model overrides' section."
        )
