"""Structural pins for docs/quota-handling.md (E5-F7-S1-T1, AC-193-13, AC-193-15).

Verifies that the operator playbook for quota wait-and-resume exists and contains
the required structural elements:

- File existence at the canonical path
- Required top-level sections: Overview, When Quota Waits Fire, Configuration,
  Sample Configs (Pro/Max/API-key/Bedrock), What Happens When max_wait_seconds
  Is Exceeded, Troubleshooting, Status Banner, Cross-references
- Cross-references to ADR-24 and spec section 4.5
- CLI commands shown for quota-watcher and devbench status
- quota_pause.json described
- No em-dash characters (U+2014) -- prohibited by devbench coding standards
- AC-193-13: non-interactive end-to-end wait-and-resume flow described
- AC-193-15: QUOTA WAIT banner with reset countdown described

Spec source: spec/devbench-self-improve.md section 4.5.
Issue: #193.
Companion: tests/test_docs/test_adr_23_named_sessions.py (ADR-23 structural pins).
"""

from __future__ import annotations

from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).parent.parent.parent
DOC = REPO_ROOT / "docs" / "quota-handling.md"


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
class TestQuotaHandlingDocExists:
    """The doc file must exist and have a valid top-level heading."""

    def test_doc_file_exists(self) -> None:
        """docs/quota-handling.md must exist at the canonical path."""
        assert DOC.is_file(), (
            "docs/quota-handling.md must exist -- E5-F7-S1-T1 and AC-193-13 / AC-193-15 mandate this playbook."
        )

    def test_doc_has_top_level_heading(self) -> None:
        """The doc must have a top-level # heading."""
        text = _read_doc()
        lines = text.splitlines()
        has_h1 = any(line.startswith("# ") for line in lines)
        assert has_h1, "docs/quota-handling.md must have a top-level # heading."

    def test_doc_is_not_empty(self) -> None:
        """The doc must have substantial content (more than a stub)."""
        text = _read_doc()
        assert len(text) > 2000, (
            f"docs/quota-handling.md must have substantial content (got {len(text)} chars). "
            "A playbook doc must be comprehensive, not a stub."
        )

    def test_doc_has_table_of_contents(self) -> None:
        """The doc must include a Table of contents for navigation."""
        text = _read_doc()
        lower = text.lower()
        has_toc = "table of contents" in lower or "## contents" in lower
        assert has_toc, (
            "docs/quota-handling.md must include a Table of contents section "
            "for navigation (documentation standards)."
        )


@pytest.mark.unit
class TestQuotaHandlingRequiredSections:
    """The doc must contain all required sections."""

    def test_has_overview_section(self) -> None:
        """The doc must have an Overview section."""
        text = _read_doc()
        assert "## Overview" in text, (
            "docs/quota-handling.md must have an '## Overview' section explaining "
            "what quota wait-and-resume is and when it applies."
        )

    def test_has_when_quota_waits_fire_section(self) -> None:
        """The doc must have a section explaining when quota waits fire."""
        text = _read_doc()
        lower = text.lower()
        has_when = (
            "when quota" in lower
            or "## when" in lower
            or "quota waits fire" in lower
            or "rate limit" in lower
        )
        assert has_when, (
            "docs/quota-handling.md must have a section explaining when quota waits fire "
            "(HTTP 429, HTTP 402, Bedrock throttle) and which Anthropic plans are affected."
        )

    def test_has_configuration_section(self) -> None:
        """The doc must have a Configuration section covering the quota_handling yaml block."""
        text = _read_doc()
        lower = text.lower()
        has_config = "## configuration" in lower or "## config" in lower
        assert has_config, (
            "docs/quota-handling.md must have a '## Configuration' section documenting "
            "the quota_handling yaml schema (spec section 4.5.6)."
        )

    def test_has_sample_configs_section(self) -> None:
        """The doc must have a section with sample yaml configs."""
        text = _read_doc()
        lower = text.lower()
        has_samples = (
            "sample config" in lower
            or "example config" in lower
            or "## sample" in lower
            or "## example" in lower
        )
        assert has_samples, (
            "docs/quota-handling.md must have a sample configurations section "
            "covering Pro / Max / API-key / Bedrock (spec section 4.5.6)."
        )

    def test_has_max_wait_exceeded_section(self) -> None:
        """The doc must have a section explaining what happens when max_wait_seconds is exceeded."""
        text = _read_doc()
        lower = text.lower()
        has_timeout = (
            "max_wait" in lower
            or "on_exhaustion_timeout" in lower
            or "timeout" in lower
        )
        assert has_timeout, (
            "docs/quota-handling.md must have a section on max_wait_seconds exceeded / "
            "on_exhaustion_timeout behavior (spec section 4.5.6, AC-193-12)."
        )

    def test_has_troubleshooting_section(self) -> None:
        """The doc must have a Troubleshooting section."""
        text = _read_doc()
        lower = text.lower()
        has_troubleshoot = "## troubleshooting" in lower or "troubleshoot" in lower
        assert has_troubleshoot, (
            "docs/quota-handling.md must have a '## Troubleshooting' section "
            "with actionable diagnostics for common failure modes."
        )

    def test_has_status_banner_section(self) -> None:
        """The doc must have a section describing the QUOTA WAIT status banner (AC-193-15)."""
        text = _read_doc()
        lower = text.lower()
        has_banner = (
            "quota wait" in lower
            or "status banner" in lower
            or "reset countdown" in lower
            or "## devbench status" in lower
        )
        assert has_banner, (
            "docs/quota-handling.md must have a section describing the "
            "QUOTA WAIT banner shown by 'devbench status' (AC-193-15)."
        )

    def test_has_cross_references_section(self) -> None:
        """The doc must have a Cross-references section."""
        text = _read_doc()
        lower = text.lower()
        has_xref = "## cross-reference" in lower or "cross-references" in lower
        assert has_xref, (
            "docs/quota-handling.md must have a 'Cross-references' section linking to related documentation."
        )


@pytest.mark.unit
class TestQuotaHandlingAc193ConfigCoverage:
    """The doc must cover all four Anthropic plan / backend types."""

    def test_pro_plan_covered(self) -> None:
        """Sample configs must include a Claude Pro / subscription rate-limit scenario."""
        text = _read_doc()
        lower = text.lower()
        has_pro = "pro" in lower or "subscription" in lower or "subscription_rate_limit" in lower
        assert has_pro, (
            "docs/quota-handling.md must cover the Claude Pro subscription rate-limit "
            "scenario with a sample yaml config."
        )

    def test_max_plan_covered(self) -> None:
        """Sample configs must include a Claude Max scenario."""
        text = _read_doc()
        lower = text.lower()
        has_max = "max" in lower
        assert has_max, (
            "docs/quota-handling.md must cover the Claude Max plan scenario."
        )

    def test_api_key_covered(self) -> None:
        """Sample configs must include an API-key / SDK-credit-exhausted scenario."""
        text = _read_doc()
        lower = text.lower()
        has_api = (
            "api key" in lower
            or "api_key" in lower
            or "sdk_credit" in lower
            or "api billing" in lower
        )
        assert has_api, (
            "docs/quota-handling.md must cover the API-key / SDK-credit-exhausted scenario "
            "with a sample yaml config."
        )

    def test_bedrock_covered(self) -> None:
        """Sample configs must include a Bedrock throttle scenario."""
        text = _read_doc()
        lower = text.lower()
        has_bedrock = "bedrock" in lower
        assert has_bedrock, (
            "docs/quota-handling.md must cover the AWS Bedrock throttle scenario "
            "with a sample yaml config."
        )


@pytest.mark.unit
class TestQuotaHandlingAc19313WaitAndResume:
    """AC-193-13: Non-interactive end-to-end wait-and-resume flow must be documented."""

    def test_wait_and_resume_flow_described(self) -> None:
        """The doc must describe the non-interactive wait-and-resume flow."""
        text = _read_doc()
        lower = text.lower()
        has_flow = "wait" in lower and "resume" in lower
        assert has_flow, (
            "docs/quota-handling.md must describe the non-interactive wait-and-resume "
            "flow (AC-193-13 / spec section 4.5)."
        )

    def test_quota_pause_json_described(self) -> None:
        """The doc must describe quota_pause.json -- the checkpoint file."""
        text = _read_doc()
        has_checkpoint = "quota_pause.json" in text
        assert has_checkpoint, (
            "docs/quota-handling.md must describe the quota_pause.json checkpoint file "
            "written on wait and removed on resume (AC-193-8 / spec section 4.5.1)."
        )

    def test_quota_waiting_audit_described(self) -> None:
        """The doc must describe the [QUOTA_WAITING] audit comment."""
        text = _read_doc()
        has_audit = "[QUOTA_WAITING]" in text or "QUOTA_WAITING" in text
        assert has_audit, (
            "docs/quota-handling.md must describe the '[QUOTA_WAITING] reason=... reset_at=...' "
            "audit comment written to the in-flight work unit (AC-193-6 / spec 4.5.7)."
        )

    def test_quota_resumed_audit_described(self) -> None:
        """The doc must describe the [QUOTA_RESUMED] audit comment."""
        text = _read_doc()
        has_resumed = "[QUOTA_RESUMED]" in text or "QUOTA_RESUMED" in text
        assert has_resumed, (
            "docs/quota-handling.md must describe the '[QUOTA_RESUMED] waited_seconds=...' "
            "audit comment written on resume (AC-193-7 / spec 4.5.7)."
        )

    def test_on_exhaustion_config_described(self) -> None:
        """The doc must describe the on_exhaustion config field (wait/fail/drain)."""
        text = _read_doc()
        has_exhaustion = "on_exhaustion" in text
        assert has_exhaustion, (
            "docs/quota-handling.md must describe the 'on_exhaustion' config field "
            "with its three values: wait, fail, drain (spec section 4.5.2)."
        )

    def test_recovery_probe_described(self) -> None:
        """The doc must describe the recovery probe mechanism."""
        text = _read_doc()
        lower = text.lower()
        has_probe = "recovery_probe" in text or "recovery probe" in lower
        assert has_probe, (
            "docs/quota-handling.md must describe the recovery_probe mechanism "
            "that validates quota recovery before resuming (spec section 4.5.1)."
        )

    def test_ac_193_13_explicitly_referenced(self) -> None:
        """The doc must explicitly reference AC-193-13."""
        text = _read_doc()
        assert "AC-193-13" in text, (
            "docs/quota-handling.md must explicitly reference AC-193-13 "
            "to connect the wait-and-resume flow to the acceptance criterion."
        )


@pytest.mark.unit
class TestQuotaHandlingAc19315StatusBanner:
    """AC-193-15: devbench status QUOTA WAIT banner with reset countdown must be documented."""

    def test_quota_wait_banner_mentioned(self) -> None:
        """The doc must mention the QUOTA WAIT banner shown by devbench status."""
        text = _read_doc()
        upper = text.upper()
        has_banner = "QUOTA WAIT" in upper
        assert has_banner, (
            "docs/quota-handling.md must mention the 'QUOTA WAIT' banner displayed "
            "by 'devbench status' (AC-193-15 / spec section 4.5)."
        )

    def test_reset_countdown_mentioned(self) -> None:
        """The doc must describe the reset countdown shown in the status banner."""
        text = _read_doc()
        lower = text.lower()
        has_countdown = "countdown" in lower or "reset" in lower
        assert has_countdown, (
            "docs/quota-handling.md must describe the reset countdown shown in "
            "the QUOTA WAIT status banner (AC-193-15)."
        )

    def test_devbench_status_command_shown(self) -> None:
        """The doc must show the devbench status command."""
        text = _read_doc()
        has_status = "devbench status" in text
        assert has_status, (
            "docs/quota-handling.md must show the 'devbench status' command "
            "that displays the QUOTA WAIT banner (AC-193-15)."
        )

    def test_ac_193_15_explicitly_referenced(self) -> None:
        """The doc must explicitly reference AC-193-15."""
        text = _read_doc()
        assert "AC-193-15" in text, (
            "docs/quota-handling.md must explicitly reference AC-193-15 "
            "to connect the status banner description to the acceptance criterion."
        )


@pytest.mark.unit
class TestQuotaHandlingCrossReferences:
    """The doc must cross-reference ADR-24 and spec section 4.5."""

    def test_adr_24_cross_reference_present(self) -> None:
        """The doc must cross-reference docs/adr/24-quota-wait-and-resume.md."""
        text = _read_doc()
        has_adr_24 = "24-quota-wait-and-resume" in text or "ADR-24" in text or "adr/24" in text.lower()
        assert has_adr_24, (
            "docs/quota-handling.md must cross-reference docs/adr/24-quota-wait-and-resume.md "
            "for the architectural decisions behind the quota wait policy."
        )

    def test_spec_section_45_cross_reference_present(self) -> None:
        """The doc must reference spec section 4.5."""
        text = _read_doc()
        has_spec = "section 4.5" in text or "spec" in text.lower()
        assert has_spec, (
            "docs/quota-handling.md must reference spec section 4.5 as the authoritative "
            "specification for the quota wait-and-resume behavior."
        )

    def test_llm_authentication_cross_reference_present(self) -> None:
        """The doc must cross-reference docs/llm-authentication.md."""
        text = _read_doc()
        has_llm_auth = "llm-authentication" in text.lower() or "llm_authentication" in text.lower()
        assert has_llm_auth, (
            "docs/quota-handling.md must cross-reference docs/llm-authentication.md "
            "for per-agent model overrides as a quota management strategy."
        )

    def test_llm_authentication_file_resolves(self) -> None:
        """The referenced docs/llm-authentication.md must exist on disk."""
        llm_auth = REPO_ROOT / "docs" / "llm-authentication.md"
        assert llm_auth.is_file(), (
            "docs/llm-authentication.md is cross-referenced from quota-handling.md "
            "but does not exist on disk."
        )

    def test_cli_reference_cross_reference_present(self) -> None:
        """The doc must cross-reference docs/cli-reference.md."""
        text = _read_doc()
        assert "cli-reference" in text.lower(), (
            "docs/quota-handling.md must cross-reference docs/cli-reference.md "
            "for the full quota-watcher command reference."
        )

    def test_cli_reference_file_resolves(self) -> None:
        """The referenced docs/cli-reference.md must exist on disk."""
        cli_ref = REPO_ROOT / "docs" / "cli-reference.md"
        assert cli_ref.is_file(), (
            "docs/cli-reference.md is cross-referenced from quota-handling.md but does not exist on disk."
        )


@pytest.mark.unit
class TestQuotaHandlingConfigSchema:
    """The doc must document the quota_handling yaml schema fields."""

    def test_enabled_field_described(self) -> None:
        """The doc must describe the quota_handling.enabled field."""
        text = _read_doc()
        assert "enabled" in text, (
            "docs/quota-handling.md must describe the 'quota_handling.enabled' field "
            "(AC-193-4: false preserves legacy behavior / AC-193-19: default true)."
        )

    def test_poll_interval_seconds_described(self) -> None:
        """The doc must describe the poll_interval_seconds field."""
        text = _read_doc()
        assert "poll_interval_seconds" in text, (
            "docs/quota-handling.md must describe the 'poll_interval_seconds' config field "
            "(spec section 4.5.6)."
        )

    def test_max_wait_seconds_described(self) -> None:
        """The doc must describe the max_wait_seconds field."""
        text = _read_doc()
        assert "max_wait_seconds" in text, (
            "docs/quota-handling.md must describe the 'max_wait_seconds' config field "
            "(AC-193-12 / spec section 4.5.6)."
        )

    def test_on_exhaustion_timeout_described(self) -> None:
        """The doc must describe the on_exhaustion_timeout field."""
        text = _read_doc()
        assert "on_exhaustion_timeout" in text, (
            "docs/quota-handling.md must describe the 'on_exhaustion_timeout' config field "
            "with its three values: drain, fail, keep_waiting (AC-193-12)."
        )

    def test_resume_strategy_described(self) -> None:
        """The doc must describe the resume_strategy field."""
        text = _read_doc()
        assert "resume_strategy" in text, (
            "docs/quota-handling.md must describe the 'resume_strategy' config field "
            "(continue_current_wu / restart_wu / drain_and_resume -- AC-193-9/10/11)."
        )

    def test_detect_modes_described(self) -> None:
        """The doc must describe the detect_modes field."""
        text = _read_doc()
        assert "detect_modes" in text, (
            "docs/quota-handling.md must describe the 'detect_modes' config field "
            "listing the four detection modes (spec section 4.5.6)."
        )

    def test_notify_on_pause_described(self) -> None:
        """The doc must describe the notify_on_pause webhook config."""
        text = _read_doc()
        assert "notify_on_pause" in text or "notify_on_resume" in text, (
            "docs/quota-handling.md must describe the notify_on_pause / notify_on_resume "
            "webhook config fields (AC-193-17 / spec section 4.5.6)."
        )


@pytest.mark.unit
class TestQuotaHandlingQuotaWatcherCommand:
    """The doc must cover the devbench quota-watcher command."""

    def test_quota_watcher_command_shown(self) -> None:
        """The doc must show the devbench quota-watcher command."""
        text = _read_doc()
        has_watcher = "quota-watcher" in text
        assert has_watcher, (
            "docs/quota-handling.md must show the 'devbench quota-watcher' command "
            "(spec section 4.5.3)."
        )

    def test_daemon_flag_shown(self) -> None:
        """The doc must show the --daemon flag for quota-watcher."""
        text = _read_doc()
        has_daemon = "--daemon" in text
        assert has_daemon, (
            "docs/quota-handling.md must show the 'quota-watcher --daemon' flag "
            "for long-running quota polling (spec section 4.5.3)."
        )

    def test_once_flag_shown(self) -> None:
        """The doc must show the --once flag for quota-watcher."""
        text = _read_doc()
        has_once = "--once" in text
        assert has_once, (
            "docs/quota-handling.md must show the 'quota-watcher --once' flag "
            "for single-tick polling (spec section 4.5.3)."
        )


@pytest.mark.unit
class TestQuotaHandlingNoEmDash:
    """The doc must not contain em-dash characters (U+2014)."""

    def test_no_em_dash(self) -> None:
        """The doc must use -- (double hyphen) instead of the em-dash character."""
        text = _read_doc()
        assert "\u2014" not in text, (
            "docs/quota-handling.md must not contain em-dash (U+2014) characters. "
            "Use -- (double hyphen) instead. "
            "(devbench validate-backlog rule 10 / spec critical rule 8)."
        )


@pytest.mark.unit
class TestQuotaHandlingMultiSessionAwareness:
    """The doc must cover multi-session quota awareness (AC-193-16)."""

    def test_per_session_quota_pause_json_described(self) -> None:
        """The doc must describe per-session quota_pause.json paths (AC-193-16)."""
        text = _read_doc()
        lower = text.lower()
        has_per_session = (
            "per-session" in lower
            or "session" in lower
        )
        assert has_per_session, (
            "docs/quota-handling.md must describe per-session quota_pause.json "
            "as each session has its own pause file (AC-193-16 / spec 4.5.3)."
        )

    def test_session_dir_path_shown(self) -> None:
        """The doc must show the .devbench/sessions/<name>/ path for quota_pause.json."""
        text = _read_doc()
        has_session_dir = ".devbench/sessions/" in text or ".devbench/sessions" in text
        assert has_session_dir, (
            "docs/quota-handling.md must show the "
            "'.devbench/sessions/<name>/' directory path for per-session quota_pause.json."
        )
