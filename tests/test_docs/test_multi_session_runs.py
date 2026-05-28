"""Structural pins for docs/multi-session-runs.md (E4-F7-S1-T3, AC-192-3, AC-192-12).

Verifies that the operator playbook for multi-session runs exists and contains
the required structural elements:

- File existence at the canonical path
- Required top-level sections: Concepts, Prerequisites, Worked example steps,
  Overlap detection, Audit trail, Per-session state layout, Common error messages,
  Cross-references
- Cross-references that resolve to real files on disk
- Code blocks containing expected CLI commands (start, sessions, status, report,
  drain, stop, scope)
- No em-dash characters (U+2014) -- prohibited by devbench coding standards
- AC-192-3: three-session disjoint-scope worked example present
- AC-192-12: per-session --session filter shown for status and report commands

Spec source: spec/devbench-self-improve.md section 4.4.
Issue: #192.
Companion: tests/test_docs/test_adr_23_named_sessions.py (ADR-23 structural pins).
"""

from __future__ import annotations

from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).parent.parent.parent
DOC = REPO_ROOT / "docs" / "multi-session-runs.md"


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
class TestMultiSessionRunsDocExists:
    """The doc file must exist and have a valid top-level heading."""

    def test_doc_file_exists(self) -> None:
        """docs/multi-session-runs.md must exist at the canonical path."""
        assert DOC.is_file(), (
            "docs/multi-session-runs.md must exist -- E4-F7-S1-T3 and AC-192-3 / AC-192-12 mandate this playbook."
        )

    def test_doc_has_top_level_heading(self) -> None:
        """The doc must have a top-level # heading."""
        text = _read_doc()
        lines = text.splitlines()
        has_h1 = any(line.startswith("# ") for line in lines)
        assert has_h1, "docs/multi-session-runs.md must have a top-level # heading."

    def test_doc_is_not_empty(self) -> None:
        """The doc must have substantial content (more than a stub)."""
        text = _read_doc()
        assert len(text) > 1000, (
            f"docs/multi-session-runs.md must have substantial content (got {len(text)} chars). "
            "A playbook doc must be comprehensive, not a stub."
        )

    def test_doc_has_table_of_contents(self) -> None:
        """The doc must include a Table of contents for navigation."""
        text = _read_doc()
        lower = text.lower()
        has_toc = "table of contents" in lower or "## contents" in lower
        assert has_toc, (
            "docs/multi-session-runs.md must include a Table of contents section "
            "for navigation (documentation standards)."
        )


@pytest.mark.unit
class TestMultiSessionRunsRequiredSections:
    """The doc must contain all required sections."""

    def test_has_concepts_section(self) -> None:
        """The doc must have a Concepts section explaining session identity."""
        text = _read_doc()
        assert "## Concepts" in text, (
            "docs/multi-session-runs.md must have a '## Concepts' section explaining "
            "what a named session is and how DEVBENCH_SESSION_NAME works."
        )

    def test_has_prerequisites_section(self) -> None:
        """The doc must have a Prerequisites section."""
        text = _read_doc()
        assert "## Prerequisites" in text, (
            "docs/multi-session-runs.md must have a '## Prerequisites' section listing "
            "what operators need before starting multiple sessions."
        )

    def test_has_worked_example_section(self) -> None:
        """The doc must have a worked example section covering three sessions."""
        text = _read_doc()
        lower = text.lower()
        has_example = "worked example" in lower or "## example" in lower
        assert has_example, (
            "docs/multi-session-runs.md must have a 'Worked example' section "
            "demonstrating three sessions on a 30-epic backlog (AC-192-3)."
        )

    def test_has_overlap_detection_section(self) -> None:
        """The doc must have an overlap detection section."""
        text = _read_doc()
        lower = text.lower()
        has_overlap = "overlap detection" in lower or "## overlap" in lower or "allow-overlap" in lower
        assert has_overlap, (
            "docs/multi-session-runs.md must have an overlap detection section "
            "covering --allow-overlap and ClaimRaceError (spec section 4.4.3)."
        )

    def test_has_audit_trail_section(self) -> None:
        """The doc must have an audit trail section explaining WU_CLAIMED stamps."""
        text = _read_doc()
        lower = text.lower()
        has_audit = "audit trail" in lower or "## audit" in lower or "claim stamp" in lower
        assert has_audit, (
            "docs/multi-session-runs.md must have an audit trail section explaining "
            "the session=<name> field added to [WU_CLAIMED] audit comments."
        )

    def test_has_per_session_state_section(self) -> None:
        """The doc must have a per-session state layout section."""
        text = _read_doc()
        lower = text.lower()
        has_state = "per-session state" in lower or "state layout" in lower or "## per-session" in lower
        assert has_state, (
            "docs/multi-session-runs.md must have a per-session state layout section "
            "showing the directory structure under .devbench/sessions/<name>/."
        )

    def test_has_common_error_messages_section(self) -> None:
        """The doc must have a common error messages section."""
        text = _read_doc()
        lower = text.lower()
        has_errors = "common error" in lower or "error message" in lower or "## error" in lower
        assert has_errors, (
            "docs/multi-session-runs.md must have a 'Common error messages' section "
            "documenting actionable fixes for known failure modes."
        )

    def test_has_cross_references_section(self) -> None:
        """The doc must have a cross-references section."""
        text = _read_doc()
        lower = text.lower()
        has_xref = "## cross-reference" in lower or "cross-references" in lower
        assert has_xref, (
            "docs/multi-session-runs.md must have a 'Cross-references' section linking to related documentation."
        )


@pytest.mark.unit
class TestMultiSessionRunsAc1923WorkedExample:
    """AC-192-3: Three-session disjoint-scope concurrent execution must be demonstrated."""

    def test_three_sessions_mentioned(self) -> None:
        """The doc must describe running three sessions concurrently."""
        text = _read_doc()
        lower = text.lower()
        has_three = "three session" in lower or "3 session" in lower or "three terminals" in lower
        assert has_three, (
            "docs/multi-session-runs.md must describe running three sessions concurrently "
            "(AC-192-3 / spec section 4.4 worked example)."
        )

    def test_thirty_epic_backlog_example(self) -> None:
        """The worked example must reference a 30-epic backlog."""
        text = _read_doc()
        lower = text.lower()
        has_thirty = "30-epic" in lower or "30 epic" in lower or "e1-e10" in lower or "e1 through e30" in lower
        assert has_thirty, (
            "docs/multi-session-runs.md must show a 30-epic backlog as the worked example "
            "(AC-192-3 / spec section 4.4)."
        )

    def test_session_names_early_mid_late(self) -> None:
        """The worked example must define three named sessions: early, mid, late."""
        text = _read_doc()
        has_early = "early" in text
        has_mid = "mid" in text
        has_late = "late" in text
        assert has_early and has_mid and has_late, (
            "docs/multi-session-runs.md must name the three example sessions 'early', 'mid', and 'late' "
            "(AC-192-3 / spec section 4.4)."
        )

    def test_disjoint_scope_tokens_shown(self) -> None:
        """The worked example must show disjoint scope tokens for each session."""
        text = _read_doc()
        has_e1_e10 = "E1-E10" in text
        has_e11_e20 = "E11-E20" in text
        has_e21_e30 = "E21-E30" in text
        assert has_e1_e10 and has_e11_e20 and has_e21_e30, (
            "docs/multi-session-runs.md must show three disjoint scope tokens: "
            "'E1-E10', 'E11-E20', and 'E21-E30' (AC-192-3)."
        )

    def test_flock_arbitration_mentioned(self) -> None:
        """The doc must mention flock-serialised claim arbitration."""
        text = _read_doc()
        lower = text.lower()
        has_flock = "flock" in lower
        assert has_flock, (
            "docs/multi-session-runs.md must mention flock-serialised claim arbitration "
            "as the safety mechanism for concurrent sessions (AC-192-3 / spec 4.4.2)."
        )

    def test_ac_192_3_explicitly_referenced(self) -> None:
        """The doc must explicitly reference AC-192-3."""
        text = _read_doc()
        assert "AC-192-3" in text, (
            "docs/multi-session-runs.md must explicitly reference AC-192-3 "
            "to connect the worked example to the acceptance criterion."
        )


@pytest.mark.unit
class TestMultiSessionRunsAc19212SessionFilter:
    """AC-192-12: Per-session --session filter on status and report must be shown."""

    def test_session_flag_shown_for_status(self) -> None:
        """The doc must show devbench status --session <name>."""
        text = _read_doc()
        has_status_session = "status --session" in text or "devbench status --session" in text
        assert has_status_session, (
            "docs/multi-session-runs.md must show 'devbench status --session <name>' "
            "demonstrating per-session filtering (AC-192-12)."
        )

    def test_session_flag_shown_for_report(self) -> None:
        """The doc must show devbench report --session <name>."""
        text = _read_doc()
        has_report_session = "report --session" in text or "devbench report --session" in text
        assert has_report_session, (
            "docs/multi-session-runs.md must show 'devbench report --session <name>' "
            "demonstrating per-session report filtering (AC-192-12)."
        )

    def test_ac_192_12_explicitly_referenced(self) -> None:
        """The doc must explicitly reference AC-192-12."""
        text = _read_doc()
        assert "AC-192-12" in text, (
            "docs/multi-session-runs.md must explicitly reference AC-192-12 "
            "to connect the --session filter example to the acceptance criterion."
        )

    def test_aggregate_vs_filtered_view_explained(self) -> None:
        """The doc must explain the difference between aggregate and per-session views."""
        text = _read_doc()
        lower = text.lower()
        has_aggregate = "aggregate" in lower or "all sessions" in lower or "without --session" in lower
        assert has_aggregate, (
            "docs/multi-session-runs.md must explain that status / report without "
            "--session aggregates across all sessions (AC-192-12)."
        )


@pytest.mark.unit
class TestMultiSessionRunsCliCommands:
    """The doc must contain expected CLI command examples."""

    def test_devbench_start_command_shown(self) -> None:
        """The doc must show devbench start --include <scope>."""
        text = _read_doc()
        has_start = "devbench start --include" in text or "uv run devbench start" in text
        assert has_start, (
            "docs/multi-session-runs.md must show a 'devbench start --include <scope>' "
            "command for launching each session."
        )

    def test_devbench_sessions_command_shown(self) -> None:
        """The doc must show devbench sessions for listing active sessions."""
        text = _read_doc()
        has_sessions = "devbench sessions" in text
        assert has_sessions, (
            "docs/multi-session-runs.md must show the 'devbench sessions' command "
            "for listing and monitoring active sessions."
        )

    def test_devbench_drain_command_shown(self) -> None:
        """The doc must show devbench drain --session <name>."""
        text = _read_doc()
        has_drain = "devbench drain" in text
        assert has_drain, (
            "docs/multi-session-runs.md must show the 'devbench drain' command for graceful session shutdown."
        )

    def test_devbench_stop_command_shown(self) -> None:
        """The doc must show devbench stop --session <name>."""
        text = _read_doc()
        has_stop = "devbench stop" in text
        assert has_stop, (
            "docs/multi-session-runs.md must show the 'devbench stop' command for immediate session termination."
        )

    def test_devbench_sessions_cleanup_command_shown(self) -> None:
        """The doc must show devbench sessions --cleanup for stale state removal."""
        text = _read_doc()
        has_cleanup = "sessions --cleanup" in text or "--cleanup" in text
        assert has_cleanup, (
            "docs/multi-session-runs.md must show 'devbench sessions --cleanup' "
            "for removing stale session state directories."
        )

    def test_devbench_session_name_env_var_shown(self) -> None:
        """The doc must show the DEVBENCH_SESSION_NAME environment variable."""
        text = _read_doc()
        assert "DEVBENCH_SESSION_NAME" in text, (
            "docs/multi-session-runs.md must show DEVBENCH_SESSION_NAME env var "
            "-- it is the mechanism that activates named-session mode."
        )

    def test_drain_all_flag_shown(self) -> None:
        """The doc must show devbench drain --all for draining every active session."""
        text = _read_doc()
        has_drain_all = "drain --all" in text or "--all" in text
        assert has_drain_all, (
            "docs/multi-session-runs.md must show 'devbench drain --all' "
            "for draining every active session simultaneously."
        )


@pytest.mark.unit
class TestMultiSessionRunsPerSessionStateLayout:
    """The doc must document the per-session state directory layout."""

    def test_sessions_directory_shown(self) -> None:
        """The doc must show the .devbench/sessions/ directory in the state layout."""
        text = _read_doc()
        has_sessions_dir = ".devbench/sessions/" in text or ".devbench/sessions" in text
        assert has_sessions_dir, (
            "docs/multi-session-runs.md must show the '.devbench/sessions/' directory "
            "in the per-session state layout section."
        )

    def test_registry_json_mentioned(self) -> None:
        """The doc must mention registry.json as the session registry file."""
        text = _read_doc()
        assert "registry.json" in text, (
            "docs/multi-session-runs.md must mention 'registry.json' as the "
            "session registry file under .devbench/sessions/."
        )

    def test_pid_file_mentioned(self) -> None:
        """The doc must mention the pid file in the per-session state layout."""
        text = _read_doc()
        lower = text.lower()
        has_pid = "pid" in lower
        assert has_pid, (
            "docs/multi-session-runs.md must mention the 'pid' file in the "
            "per-session state layout (used for SIGTERM delivery and liveness check)."
        )

    def test_backlog_lock_mentioned(self) -> None:
        """The doc must mention BACKLOG.lock as the flock sentinel."""
        text = _read_doc()
        has_lock = "BACKLOG.lock" in text
        assert has_lock, (
            "docs/multi-session-runs.md must mention 'BACKLOG.lock' as the "
            "advisory flock sentinel for claim arbitration."
        )

    def test_drain_signal_file_mentioned(self) -> None:
        """The doc must mention the drain.signal file in the per-session state layout."""
        text = _read_doc()
        has_drain_signal = "drain.signal" in text
        assert has_drain_signal, (
            "docs/multi-session-runs.md must mention 'drain.signal' in the "
            "per-session state layout (per-session drain marker)."
        )


@pytest.mark.unit
class TestMultiSessionRunsCrossReferences:
    """The doc must cross-reference related documentation that resolves to real files."""

    def test_adr_23_cross_reference_present(self) -> None:
        """The doc must cross-reference docs/adr/23-named-sessions.md."""
        text = _read_doc()
        has_adr_23 = "23-named-sessions" in text or "adr/23" in text.lower()
        assert has_adr_23, (
            "docs/multi-session-runs.md must cross-reference docs/adr/23-named-sessions.md "
            "for the architectural decisions behind named sessions."
        )

    def test_adr_23_file_resolves(self) -> None:
        """The referenced docs/adr/23-named-sessions.md must exist on disk."""
        adr_23 = REPO_ROOT / "docs" / "adr" / "23-named-sessions.md"
        assert adr_23.is_file(), (
            "docs/adr/23-named-sessions.md is cross-referenced from multi-session-runs.md but does not exist on disk."
        )

    def test_cli_reference_cross_reference_present(self) -> None:
        """The doc must cross-reference docs/cli-reference.md."""
        text = _read_doc()
        assert "cli-reference" in text.lower(), (
            "docs/multi-session-runs.md must cross-reference docs/cli-reference.md for the full command reference."
        )

    def test_cli_reference_file_resolves(self) -> None:
        """The referenced docs/cli-reference.md must exist on disk."""
        cli_ref = REPO_ROOT / "docs" / "cli-reference.md"
        assert cli_ref.is_file(), (
            "docs/cli-reference.md is cross-referenced from multi-session-runs.md but does not exist on disk."
        )

    def test_concurrent_multi_workspace_cross_reference_present(self) -> None:
        """The doc must cross-reference docs/concurrent-multi-workspace.md."""
        text = _read_doc()
        assert "concurrent-multi-workspace" in text.lower(), (
            "docs/multi-session-runs.md must cross-reference docs/concurrent-multi-workspace.md "
            "for the older two-clone pattern."
        )

    def test_concurrent_multi_workspace_file_resolves(self) -> None:
        """The referenced docs/concurrent-multi-workspace.md must exist on disk."""
        cmw = REPO_ROOT / "docs" / "concurrent-multi-workspace.md"
        assert cmw.is_file(), (
            "docs/concurrent-multi-workspace.md is cross-referenced from multi-session-runs.md "
            "but does not exist on disk."
        )

    def test_glossary_cross_reference_present(self) -> None:
        """The doc must cross-reference docs/glossary.md."""
        text = _read_doc()
        assert "glossary" in text.lower(), (
            "docs/multi-session-runs.md must cross-reference docs/glossary.md "
            "for canonical definitions of 'session', 'drain', 'scope', and 'audit comment'."
        )

    def test_glossary_file_resolves(self) -> None:
        """The referenced docs/glossary.md must exist on disk."""
        glossary = REPO_ROOT / "docs" / "glossary.md"
        assert glossary.is_file(), (
            "docs/glossary.md is cross-referenced from multi-session-runs.md but does not exist on disk."
        )

    def test_zero_to_ready_cross_reference_present(self) -> None:
        """The doc must cross-reference docs/zero-to-ready.md."""
        text = _read_doc()
        assert "zero-to-ready" in text.lower(), (
            "docs/multi-session-runs.md must cross-reference docs/zero-to-ready.md "
            "for initial devbench setup instructions."
        )

    def test_zero_to_ready_file_resolves(self) -> None:
        """The referenced docs/zero-to-ready.md must exist on disk."""
        ztr = REPO_ROOT / "docs" / "zero-to-ready.md"
        assert ztr.is_file(), (
            "docs/zero-to-ready.md is cross-referenced from multi-session-runs.md but does not exist on disk."
        )


@pytest.mark.unit
class TestMultiSessionRunsNoEmDash:
    """The doc must not contain em-dash characters (U+2014)."""

    def test_no_em_dash(self) -> None:
        """The doc must use -- (double hyphen) instead of the em-dash character."""
        text = _read_doc()
        assert "\u2014" not in text, (
            "docs/multi-session-runs.md must not contain em-dash (U+2014) characters. "
            "Use -- (double hyphen) instead. "
            "(devbench validate-backlog rule 10 / spec critical rule 8)."
        )
