"""Structural pins for docs/adr/24-quota-wait-and-resume.md (E5-F7-S1-T3, AC-STRUCT-1).

Verifies that the ADR exists and contains the required structural elements:
- Status and Date headers
- Context section covering the motivation (autonomous runs survive quota windows)
- Decision section covering config-driven wait/resume
- Alternatives considered section (operator-driven retry, fixed sleep, auto-failover)
- Consequences section (operator playbook, safety bounds, what's out of scope)
- References section linking to implementation and companion docs
- Cross-references to docs/quota-handling.md and spec section 4.5
- No em-dash characters (U+2014) -- prohibited by devbench coding standards

Spec source: spec/devbench-self-improve.md section 5.2.
Issue: #193.
Companion: tests/test_docs/test_adr_23_named_sessions.py (ADR-23 structural pins).
"""

from __future__ import annotations

from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).parent.parent.parent
ADR_24 = REPO_ROOT / "docs" / "adr" / "24-quota-wait-and-resume.md"


@pytest.mark.unit
class TestAdr24QuotaWaitAndResume:
    """Structural pins for ADR-24 (quota wait-and-resume policy rationale)."""

    def test_adr_24_file_exists(self) -> None:
        """The ADR file must exist at the canonical path."""
        assert ADR_24.is_file(), (
            "docs/adr/24-quota-wait-and-resume.md must exist -- "
            "spec section 5.2 and E5-F7-S1-T3 mandate this ADR."
        )

    def test_adr_24_has_status_header(self) -> None:
        """ADR-24 must carry an explicit Status line."""
        text = ADR_24.read_text(encoding="utf-8")
        assert "**Status:**" in text, (
            "ADR-24 must have a '**Status:**' header line matching the ADR template."
        )

    def test_adr_24_has_date_header(self) -> None:
        """ADR-24 must carry an explicit Date line."""
        text = ADR_24.read_text(encoding="utf-8")
        assert "**Date:**" in text, (
            "ADR-24 must have a '**Date:**' header line matching the ADR template."
        )

    def test_adr_24_has_context_section(self) -> None:
        """ADR-24 must include a Context section covering the motivation."""
        text = ADR_24.read_text(encoding="utf-8")
        assert "## Context" in text, (
            "ADR-24 must have a '## Context' section explaining why quota wait-and-resume is needed."
        )

    def test_adr_24_context_covers_autonomous_runs(self) -> None:
        """The Context section must address autonomous-run survival across quota windows."""
        text = ADR_24.read_text(encoding="utf-8")
        lower = text.lower()
        has_autonomous = (
            "autonomous" in lower
            or "unattended" in lower
            or "overnight" in lower
            or "long-running" in lower
        )
        assert has_autonomous, (
            "ADR-24's Context section must address the autonomous-run scenario -- "
            "the motivation is that unattended orchestration runs must survive quota windows "
            "without operator intervention (spec section 5.2 and issue #193)."
        )

    def test_adr_24_has_decision_section(self) -> None:
        """ADR-24 must include a Decision section."""
        text = ADR_24.read_text(encoding="utf-8")
        assert "## Decision" in text, (
            "ADR-24 must have a '## Decision' section documenting the chosen design."
        )

    def test_adr_24_decision_covers_config_driven_wait(self) -> None:
        """The Decision section must describe the config-driven wait/resume mechanism."""
        text = ADR_24.read_text(encoding="utf-8")
        lower = text.lower()
        has_config_driven = (
            "config" in lower
            or "quota_handling" in text
            or "devbench.yaml" in text
        )
        assert has_config_driven, (
            "ADR-24's Decision section must describe the config-driven wait/resume mechanism "
            "controlled by the quota_handling section in devbench.yaml "
            "(spec section 4.5.6 and AC-193-19)."
        )

    def test_adr_24_decision_covers_on_exhaustion(self) -> None:
        """The Decision section must describe the on_exhaustion config field."""
        text = ADR_24.read_text(encoding="utf-8")
        has_on_exhaustion = "on_exhaustion" in text
        assert has_on_exhaustion, (
            "ADR-24 must describe the 'on_exhaustion' config field (wait/fail/drain) "
            "as a key architectural decision (spec section 4.5.2)."
        )

    def test_adr_24_decision_covers_resume_strategy(self) -> None:
        """The Decision section must describe the resume_strategy config field."""
        text = ADR_24.read_text(encoding="utf-8")
        has_resume = "resume_strategy" in text or "resume strategy" in text.lower()
        assert has_resume, (
            "ADR-24 must describe the 'resume_strategy' config field "
            "(continue_current_wu / restart_wu / drain_and_resume -- "
            "AC-193-9/10/11 / spec section 4.5.2)."
        )

    def test_adr_24_decision_covers_recovery_probe(self) -> None:
        """The Decision section must describe the recovery probe mechanism."""
        text = ADR_24.read_text(encoding="utf-8")
        lower = text.lower()
        has_probe = "recovery_probe" in text or "recovery probe" in lower
        assert has_probe, (
            "ADR-24 must describe the recovery_probe mechanism that validates "
            "quota recovery before resuming (spec section 4.5.1)."
        )

    def test_adr_24_has_alternatives_considered_section(self) -> None:
        """ADR-24 must include an Alternatives considered section."""
        text = ADR_24.read_text(encoding="utf-8")
        assert "## Alternatives" in text, (
            "ADR-24 must have an '## Alternatives' section documenting rejected designs."
        )

    def test_adr_24_alternatives_covers_operator_driven_retry(self) -> None:
        """Alternatives must include the operator-driven retry approach."""
        text = ADR_24.read_text(encoding="utf-8")
        lower = text.lower()
        has_operator_retry = (
            "operator" in lower and ("retry" in lower or "restart" in lower or "manual" in lower)
        )
        assert has_operator_retry, (
            "ADR-24's Alternatives section must discuss operator-driven retry "
            "(the manual restart approach that existed before #193)."
        )

    def test_adr_24_alternatives_covers_fixed_sleep(self) -> None:
        """Alternatives must include the fixed-sleep approach."""
        text = ADR_24.read_text(encoding="utf-8")
        lower = text.lower()
        has_fixed_sleep = (
            "fixed sleep" in lower
            or "fixed-sleep" in lower
            or "hardcoded" in lower
            or "hard-coded" in lower
            or "static" in lower
        )
        assert has_fixed_sleep, (
            "ADR-24's Alternatives section must discuss the fixed-sleep approach "
            "(time.sleep with a constant delay instead of parsing the reset header)."
        )

    def test_adr_24_alternatives_covers_auto_failover(self) -> None:
        """Alternatives must include the automatic provider failover approach."""
        text = ADR_24.read_text(encoding="utf-8")
        lower = text.lower()
        has_failover = "failover" in lower or "fail-over" in lower or "fallback" in lower
        assert has_failover, (
            "ADR-24's Alternatives section must discuss automatic provider failover "
            "(the rejected approach of switching to a fallback API key or provider on quota exhaustion)."
        )

    def test_adr_24_has_consequences_section(self) -> None:
        """ADR-24 must include a Consequences section."""
        text = ADR_24.read_text(encoding="utf-8")
        assert "## Consequences" in text, (
            "ADR-24 must have a '## Consequences' section covering operator impact."
        )

    def test_adr_24_consequences_covers_operator_playbook(self) -> None:
        """The Consequences section must reference the operator playbook."""
        text = ADR_24.read_text(encoding="utf-8")
        lower = text.lower()
        has_playbook = (
            "playbook" in lower
            or "quota-handling" in lower
            or "quota_handling" in lower
            or "docs/quota" in lower
        )
        assert has_playbook, (
            "ADR-24's Consequences section must reference the operator playbook "
            "(docs/quota-handling.md) for runbook-style guidance (spec section 5.2)."
        )

    def test_adr_24_consequences_covers_safety_bounds(self) -> None:
        """The Consequences section must describe the safety bounds (max_wait_seconds etc.)."""
        text = ADR_24.read_text(encoding="utf-8")
        lower = text.lower()
        has_safety = (
            "max_wait" in lower
            or "safety" in lower
            or "bound" in lower
            or "ceiling" in lower
            or "limit" in lower
        )
        assert has_safety, (
            "ADR-24's Consequences section must describe the safety bounds "
            "(max_wait_seconds ceiling, on_exhaustion_timeout) that prevent "
            "the orchestrator from waiting indefinitely."
        )

    def test_adr_24_consequences_covers_out_of_scope(self) -> None:
        """The Consequences section must explicitly state what is out of scope."""
        text = ADR_24.read_text(encoding="utf-8")
        lower = text.lower()
        has_out_of_scope = "out of scope" in lower or "not in scope" in lower
        assert has_out_of_scope, (
            "ADR-24's Consequences section must explicitly state what is out of scope "
            "(e.g., auto-failover, multi-account pooling, predictive throttling)."
        )

    def test_adr_24_has_references_section(self) -> None:
        """ADR-24 must include a References section linking to implementation."""
        text = ADR_24.read_text(encoding="utf-8")
        assert "## References" in text, (
            "ADR-24 must have a '## References' section linking to the implementation and companion docs."
        )

    def test_adr_24_references_quota_py(self) -> None:
        """ADR-24 must reference the quota.py implementation module."""
        text = ADR_24.read_text(encoding="utf-8")
        assert "quota.py" in text, (
            "ADR-24 must cross-reference src/devbench/quota.py as the implementation module."
        )

    def test_adr_24_references_quota_handling_doc(self) -> None:
        """ADR-24 must reference the docs/quota-handling.md playbook."""
        text = ADR_24.read_text(encoding="utf-8")
        has_ref = "quota-handling" in text.lower() or "quota_handling" in text
        assert has_ref, (
            "ADR-24 must cross-reference docs/quota-handling.md as the operator playbook "
            "companion to this architectural rationale document."
        )

    def test_adr_24_references_issue_193(self) -> None:
        """ADR-24 must reference GitHub issue #193."""
        text = ADR_24.read_text(encoding="utf-8")
        assert "#193" in text, (
            "ADR-24 must reference issue #193 (quota wait-and-resume) for traceability."
        )

    def test_adr_24_references_adr_23(self) -> None:
        """ADR-24 must reference ADR-23 (named sessions -- quota_pause.json shares the per-session dir)."""
        text = ADR_24.read_text(encoding="utf-8")
        has_adr_23 = "ADR-23" in text or "23-named-sessions" in text
        assert has_adr_23, (
            "ADR-24 must cross-reference ADR-23 (named sessions) because quota_pause.json "
            "shares the per-session state directory introduced by #192."
        )

    def test_adr_24_mentions_spec_section_45(self) -> None:
        """ADR-24 must reference spec section 4.5 as the authoritative specification."""
        text = ADR_24.read_text(encoding="utf-8")
        has_spec = "4.5" in text
        assert has_spec, (
            "ADR-24 must reference spec section 4.5 as the authoritative behavioural "
            "specification for the quota wait-and-resume feature."
        )

    def test_adr_24_no_em_dash(self) -> None:
        """ADR-24 must not contain the em-dash character (U+2014).

        Per devbench coding standards: use '--' (double hyphen) in docs.
        """
        text = ADR_24.read_text(encoding="utf-8")
        em_dash = "\u2014"
        assert em_dash not in text, (
            "ADR-24 must not contain the em-dash character (U+2014). "
            "Use '--' (double hyphen) instead. "
            "(devbench validate-backlog rule 10 / spec critical rule 8)."
        )

    def test_adr_24_minimum_length(self) -> None:
        """ADR-24 must be substantive -- at least 1500 characters."""
        text = ADR_24.read_text(encoding="utf-8")
        assert len(text) >= 1500, (
            f"ADR-24 is too short ({len(text)} chars). "
            "An ADR must provide enough context to be useful to future readers."
        )

    def test_adr_24_covers_quota_pause_json(self) -> None:
        """ADR-24 must describe the quota_pause.json checkpoint file."""
        text = ADR_24.read_text(encoding="utf-8")
        has_checkpoint = "quota_pause.json" in text
        assert has_checkpoint, (
            "ADR-24 must describe the quota_pause.json checkpoint file "
            "written on wait and removed on resume (AC-193-8 / spec section 4.5.1)."
        )

    def test_adr_24_covers_multi_error_types(self) -> None:
        """ADR-24 must cover the multiple quota error types (429, 402, Bedrock)."""
        text = ADR_24.read_text(encoding="utf-8")
        lower = text.lower()
        has_429 = "429" in text or "rate limit" in lower
        has_402 = "402" in text or "credit" in lower
        has_bedrock = "bedrock" in lower
        assert has_429 and has_402 and has_bedrock, (
            "ADR-24 must cover all three quota error types: HTTP 429 (rate limit), "
            "HTTP 402 (credit/billing exhaustion), and Bedrock throttle errors "
            "(spec section 4.5.1 / AC-193-1 through AC-193-3)."
        )
