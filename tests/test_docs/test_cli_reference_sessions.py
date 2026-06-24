"""Structural pins for the named-sessions additions in docs/cli-reference.md.

Verifies that docs/cli-reference.md documents:
- The ``devbench sessions`` subcommand (AC-192-10).
- The ``devbench stop --session <name>`` subcommand (AC-192-9).
- The ``--name`` and ``--allow-overlap`` flags on ``devbench start`` (spec 4.4.3).
- The ``--session`` flag on ``devbench status`` and ``devbench report`` (spec 4.4.6).
- The ``[WU_CLAIMED]`` audit format extension (spec 4.4.7).
- The Contents table entry for the named-sessions section.
- No em-dash characters (U+2014) in the added sections.

Spec source: spec/devbench-self-improve.md section 4.4. Issue: #192.
"""

from __future__ import annotations

from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).parent.parent.parent
CLI_REFERENCE_DOC = REPO_ROOT / "docs" / "cli-reference.md"


def _read_doc() -> str:
    return CLI_REFERENCE_DOC.read_text(encoding="utf-8")


def _extract_section(text: str, heading: str) -> str:
    """Return the content of the section starting at ``heading`` up to the next same-level heading."""
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
class TestSessionsSectionExists:
    """A Named sessions section must exist in cli-reference.md."""

    def test_named_sessions_section_heading_present(self) -> None:
        """The doc must have a section dedicated to named sessions."""
        text = _read_doc()
        assert "### `sessions`" in text, (
            "docs/cli-reference.md must contain a '### `sessions`' section documenting "
            "the devbench sessions subcommand (spec section 4.4.5, AC-192-10)."
        )

    def test_sessions_section_has_content(self) -> None:
        """The sessions section must not be a stub -- it must have substantive prose."""
        text = _read_doc()
        section = _extract_section(text, "### `sessions`")
        assert section, "### `sessions` section must exist in cli-reference.md"
        lines = [ln.strip() for ln in section.splitlines() if ln.strip()]
        assert len(lines) > 3, (
            "docs/cli-reference.md '### `sessions`' section must contain substantive "
            "documentation, not just a heading (spec section 4.4.5, AC-192-10)."
        )


@pytest.mark.unit
class TestSessionsListsActiveSessions:
    """devbench sessions must document listing active sessions (AC-192-10)."""

    def test_sessions_lists_name_pid_scope_started_at(self) -> None:
        """The sessions section must state that the command lists name, PID, scope, started_at."""
        text = _read_doc()
        section = _extract_section(text, "### `sessions`")
        assert section, "### `sessions` section must exist"
        lower = section.lower()
        assert "name" in lower, (
            "docs/cli-reference.md '### `sessions`' must mention that 'name' is shown in session listings (AC-192-10)."
        )
        assert "pid" in lower, (
            "docs/cli-reference.md '### `sessions`' must mention that 'PID' is shown in session listings (AC-192-10)."
        )
        assert "scope" in lower, (
            "docs/cli-reference.md '### `sessions`' must mention that 'scope' is shown in session listings (AC-192-10)."
        )
        assert "started_at" in lower or "started at" in lower, (
            "docs/cli-reference.md '### `sessions`' must mention that 'started_at' is shown "
            "in session listings (AC-192-10)."
        )

    def test_sessions_lists_liveness(self) -> None:
        """The sessions section must document ACTIVE / STALE liveness display."""
        text = _read_doc()
        section = _extract_section(text, "### `sessions`")
        assert section, "### `sessions` section must exist"
        has_liveness = "ACTIVE" in section or "STALE" in section or "liveness" in section.lower()
        assert has_liveness, (
            "docs/cli-reference.md '### `sessions`' must document the ACTIVE / STALE "
            "liveness indicator (spec section 4.4.5, AC-192-10)."
        )

    def test_sessions_lists_drain_state(self) -> None:
        """The sessions section must mention that drain state is shown."""
        text = _read_doc()
        section = _extract_section(text, "### `sessions`")
        assert section, "### `sessions` section must exist"
        lower = section.lower()
        has_drain = "drain" in lower
        assert has_drain, (
            "docs/cli-reference.md '### `sessions`' must document that drain state is "
            "included in the session listing (spec section 4.4.5, AC-192-10)."
        )


@pytest.mark.unit
class TestSessionsCleanupFlag:
    """devbench sessions --cleanup must be documented (spec 4.4.5)."""

    def test_cleanup_flag_documented(self) -> None:
        """The sessions section must document the --cleanup flag."""
        text = _read_doc()
        section = _extract_section(text, "### `sessions`")
        assert section, "### `sessions` section must exist"
        assert "--cleanup" in section, (
            "docs/cli-reference.md '### `sessions`' must document the '--cleanup' flag "
            "that removes stale session directories (spec section 4.4.5)."
        )

    def test_cleanup_removes_stale_dirs_documented(self) -> None:
        """The doc must explain that --cleanup removes dirs with non-running PIDs."""
        text = _read_doc()
        section = _extract_section(text, "### `sessions`")
        assert section, "### `sessions` section must exist"
        lower = section.lower()
        has_stale_removal = (
            "stale" in lower
            or ("non-running" in lower or "not running" in lower or "dead" in lower)
            or "remove" in lower
            or "clean" in lower
        )
        assert has_stale_removal, (
            "docs/cli-reference.md '### `sessions`' must explain that '--cleanup' "
            "removes session directories whose PID is no longer running "
            "(spec section 4.4.5)."
        )


@pytest.mark.unit
class TestStopSessionFlag:
    """devbench stop --session <name> must be documented (AC-192-9)."""

    def test_stop_section_exists(self) -> None:
        """A ### `stop` section must be present in the document."""
        text = _read_doc()
        assert "### `stop`" in text, (
            "docs/cli-reference.md must contain a '### `stop`' section documenting "
            "the devbench stop subcommand (spec section 4.4.5, AC-192-9)."
        )

    def test_stop_session_flag_documented(self) -> None:
        """The stop section must document the --session flag."""
        text = _read_doc()
        section = _extract_section(text, "### `stop`")
        assert section, "### `stop` section must exist"
        assert "--session" in section, (
            "docs/cli-reference.md '### `stop`' must document the '--session <name>' "
            "flag (spec section 4.4.5, AC-192-9)."
        )

    def test_stop_sends_sigterm_documented(self) -> None:
        """The stop section must explain that SIGTERM is sent via PID file."""
        text = _read_doc()
        section = _extract_section(text, "### `stop`")
        assert section, "### `stop` section must exist"
        lower = section.lower()
        has_sigterm = "sigterm" in lower or "signal" in lower
        assert has_sigterm, (
            "docs/cli-reference.md '### `stop`' must document that SIGTERM is sent "
            "via the session's PID file (spec section 4.4.5, AC-192-9)."
        )

    def test_stop_blocks_in_flight_wu_documented(self) -> None:
        """The stop section must document that the in-flight WU is marked blocked."""
        text = _read_doc()
        section = _extract_section(text, "### `stop`")
        assert section, "### `stop` section must exist"
        lower = section.lower()
        has_blocked = "blocked" in lower or "block" in lower
        assert has_blocked, (
            "docs/cli-reference.md '### `stop`' must document that the in-flight work "
            "unit is marked 'blocked' with an audit comment (spec section 4.4.5, AC-192-9)."
        )

    def test_stop_audit_comment_documented(self) -> None:
        """The stop section must mention the [FORCED_BLOCKED_ON_STOP] audit comment."""
        text = _read_doc()
        section = _extract_section(text, "### `stop`")
        assert section, "### `stop` section must exist"
        has_audit = "FORCED_BLOCKED_ON_STOP" in section or "forced_blocked" in section.lower()
        assert has_audit, (
            "docs/cli-reference.md '### `stop`' must mention the "
            "'[FORCED_BLOCKED_ON_STOP] session=<name>' audit comment appended to the "
            "in-flight work unit (spec section 4.4.5, AC-192-9)."
        )

    def test_stop_section_has_worked_example(self) -> None:
        """The stop section must include a worked example."""
        text = _read_doc()
        section = _extract_section(text, "### `stop`")
        assert section, "### `stop` section must exist"
        assert "```" in section, (
            "docs/cli-reference.md '### `stop`' must include at least one code block "
            "with a worked example (spec section 4.4.5, AC-192-9)."
        )


@pytest.mark.unit
class TestStartNameFlag:
    """devbench start --name flag must be documented (spec 4.4.3)."""

    def test_name_flag_on_start_documented(self) -> None:
        """The start section must document the --name flag."""
        text = _read_doc()
        section = _extract_section(text, "### `start`")
        assert section, "### `start` section must exist"
        assert "--name" in section, (
            "docs/cli-reference.md '### `start`' must document the '--name <name>' "
            "flag for named sessions (spec section 4.4.3)."
        )

    def test_name_defaults_to_default_documented(self) -> None:
        """The start section must state that --name defaults to 'default' when omitted."""
        text = _read_doc()
        section = _extract_section(text, "### `start`")
        assert section, "### `start` section must exist"
        lower = section.lower()
        has_default = "default" in lower and ("omit" in lower or "when not" in lower or "defaults to" in lower)
        assert has_default, (
            "docs/cli-reference.md '### `start`' must state that '--name' defaults to "
            "'default' when omitted (spec section 4.4.3, AC-192-2)."
        )

    def test_name_creates_session_dir_documented(self) -> None:
        """The start section must mention the per-session state directory creation."""
        text = _read_doc()
        section = _extract_section(text, "### `start`")
        assert section, "### `start` section must exist"
        lower = section.lower()
        has_session_dir = "sessions" in lower and (
            ".devbench" in lower or "state" in lower or "directory" in lower or "dir" in lower
        )
        assert has_session_dir, (
            "docs/cli-reference.md '### `start`' must document that '--name' creates "
            "a per-session state directory under <workspace>/.devbench/sessions/<name>/ "
            "(spec section 4.4.1, AC-192-1)."
        )


@pytest.mark.unit
class TestStartAllowOverlapFlag:
    """devbench start --allow-overlap flag must be documented (spec 4.4.3)."""

    def test_allow_overlap_flag_documented(self) -> None:
        """The start section must document the --allow-overlap flag."""
        text = _read_doc()
        section = _extract_section(text, "### `start`")
        assert section, "### `start` section must exist"
        assert "--allow-overlap" in section, (
            "docs/cli-reference.md '### `start`' must document the '--allow-overlap' flag (spec section 4.4.3)."
        )

    def test_overlap_detection_fail_fast_documented(self) -> None:
        """The start section must explain that overlap detection fails fast by default."""
        text = _read_doc()
        section = _extract_section(text, "### `start`")
        assert section, "### `start` section must exist"
        lower = section.lower()
        has_fail_fast = ("fail" in lower and "overlap" in lower) or (
            "overlap" in lower and ("error" in lower or "exit" in lower or "abort" in lower or "reject" in lower)
        )
        assert has_fail_fast, (
            "docs/cli-reference.md '### `start`' must document that scope overlap "
            "detection fails fast by default when two sessions would claim the same work "
            "units (spec section 4.4.3, AC-192-4)."
        )

    def test_allow_overlap_warn_documented(self) -> None:
        """The start section must explain that --allow-overlap allows overlap with a warning."""
        text = _read_doc()
        section = _extract_section(text, "### `start`")
        assert section, "### `start` section must exist"
        lower = section.lower()
        has_warn = "warn" in lower or "proceed" in lower or "allow" in lower
        assert has_warn and "--allow-overlap" in section, (
            "docs/cli-reference.md '### `start`' must document that '--allow-overlap' "
            "allows overlapping scopes with a warning (spec section 4.4.3)."
        )

    def test_start_session_example_present(self) -> None:
        """The start section must include an example showing --name usage."""
        text = _read_doc()
        section = _extract_section(text, "### `start`")
        assert section, "### `start` section must exist"
        has_name_example = "--name" in section and "```" in section
        assert has_name_example, (
            "docs/cli-reference.md '### `start`' must include a worked example using "
            "'--name' to launch a named session (spec section 4.4.3)."
        )


@pytest.mark.unit
class TestStatusSessionFlag:
    """devbench status --session flag must be documented (spec 4.4.6)."""

    def test_status_session_flag_documented(self) -> None:
        """The status section must document the --session flag."""
        text = _read_doc()
        section = _extract_section(text, "### `status`")
        assert section, "### `status` section must exist"
        assert "--session" in section, (
            "docs/cli-reference.md '### `status`' must document the '--session <name>' "
            "flag for filtering to one session's claimed work units (spec section 4.4.6)."
        )

    def test_status_session_filters_to_one_session_documented(self) -> None:
        """The status section must explain that --session filters to a single session's WUs."""
        text = _read_doc()
        section = _extract_section(text, "### `status`")
        assert section, "### `status` section must exist"
        lower = section.lower()
        has_filter = "filter" in lower or "filter" in section or "limit" in lower or "restrict" in lower
        assert has_filter and "--session" in section, (
            "docs/cli-reference.md '### `status`' must explain that '--session <name>' "
            "filters the output to that session's claimed work units (spec section 4.4.6)."
        )

    def test_status_without_session_aggregates_documented(self) -> None:
        """The status section must state that without --session, output aggregates all sessions."""
        text = _read_doc()
        section = _extract_section(text, "### `status`")
        assert section, "### `status` section must exist"
        lower = section.lower()
        has_aggregate = "aggregat" in lower or "all session" in lower or "all active" in lower
        assert has_aggregate, (
            "docs/cli-reference.md '### `status`' must state that without '--session', "
            "the command aggregates across all active sessions (spec section 4.4.6)."
        )


@pytest.mark.unit
class TestReportSessionFlag:
    """devbench report --session flag must be documented (spec 4.4.6)."""

    def test_report_session_flag_documented(self) -> None:
        """The report section must document the --session flag."""
        text = _read_doc()
        section = _extract_section(text, "### `report`")
        assert section, "### `report` section must exist"
        assert "--session" in section, (
            "docs/cli-reference.md '### `report`' must document the '--session <name>' "
            "flag for filtering to one session's claimed work units (spec section 4.4.6)."
        )

    def test_report_without_session_aggregates_documented(self) -> None:
        """The report section must state that without --session, output aggregates all sessions."""
        text = _read_doc()
        section = _extract_section(text, "### `report`")
        assert section, "### `report` section must exist"
        lower = section.lower()
        has_aggregate = "aggregat" in lower or "all session" in lower or "all active" in lower
        assert has_aggregate, (
            "docs/cli-reference.md '### `report`' must state that without '--session', "
            "the command aggregates across all active sessions (spec section 4.4.6)."
        )


@pytest.mark.unit
class TestWuClaimedAuditExtension:
    """The [WU_CLAIMED] audit format extension must be documented (spec 4.4.7)."""

    def test_wu_claimed_session_suffix_documented(self) -> None:
        """The doc must mention the session=<name> suffix on [WU_CLAIMED] audits."""
        text = _read_doc()
        has_audit_doc = "[WU_CLAIMED]" in text and "session=" in text
        assert has_audit_doc, (
            "docs/cli-reference.md must document the '[WU_CLAIMED] ... session=<name>' "
            "audit format extension applied when DEVBENCH_SESSION_NAME is set "
            "(spec section 4.4.7)."
        )

    def test_wu_claimed_no_change_without_session_documented(self) -> None:
        """The doc must state that without DEVBENCH_SESSION_NAME the format is unchanged."""
        text = _read_doc()
        lower = text.lower()
        has_legacy_note = "devbench_session_name" in lower and (
            "unset" in lower or "single-session" in lower or "unchanged" in lower or "omitted" in lower
        )
        assert has_legacy_note, (
            "docs/cli-reference.md must document that when DEVBENCH_SESSION_NAME is "
            "unset the [WU_CLAIMED] comment format is unchanged (spec section 4.4.7)."
        )


@pytest.mark.unit
class TestContentsTableNamedSessions:
    """The Contents table must reference named sessions."""

    def test_contents_includes_named_sessions_entry(self) -> None:
        """The Contents table must link to the named sessions section."""
        text = _read_doc()
        contents_idx = text.find("## Contents")
        if contents_idx == -1:
            pytest.skip("no Contents table found in cli-reference.md")
        next_section = text.find("\n---", contents_idx)
        if next_section == -1:
            next_section = contents_idx + 2000
        contents_block = text[contents_idx:next_section]
        has_sessions = "session" in contents_block.lower()
        assert has_sessions, (
            "docs/cli-reference.md Contents table must include an entry linking to "
            "the named sessions section (spec section 4.4.5, AC-192-9, AC-192-10)."
        )


@pytest.mark.unit
class TestNoEmDashInNewSections:
    """The new sessions and stop sections must not contain em-dash characters (U+2014)."""

    def test_sessions_section_no_em_dash(self) -> None:
        """The ### `sessions` section must not use em-dash."""
        text = _read_doc()
        section = _extract_section(text, "### `sessions`")
        em_dash = "\u2014"
        assert em_dash not in section, (
            "docs/cli-reference.md '### `sessions`' must not contain the em-dash "
            "character (U+2014). Use '--' (double hyphen) per devbench coding standards."
        )

    def test_stop_section_no_em_dash(self) -> None:
        """The ### `stop` section must not use em-dash."""
        text = _read_doc()
        section = _extract_section(text, "### `stop`")
        em_dash = "\u2014"
        assert em_dash not in section, (
            "docs/cli-reference.md '### `stop`' must not contain the em-dash "
            "character (U+2014). Use '--' (double hyphen) per devbench coding standards."
        )


@pytest.mark.unit
class TestNamedSessionsSection:
    """A top-level Named sessions section must group the sessions and stop commands."""

    def test_named_sessions_top_level_section_exists(self) -> None:
        """A ## Named sessions section must be present to group sessions and stop commands."""
        text = _read_doc()
        assert "## Named sessions" in text, (
            "docs/cli-reference.md must contain a '## Named sessions' top-level section "
            "grouping the 'sessions' and 'stop' subcommands (spec section 4.4.5)."
        )
