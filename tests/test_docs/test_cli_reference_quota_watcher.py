"""Structural pins for the quota-watcher subcommand addition in docs/cli-reference.md.

Verifies that docs/cli-reference.md documents:
- A ``## Quota wait-and-resume`` group section (AC-193-14).
- The ``### `quota-watcher``` subcommand heading.
- The ``--daemon`` flag (long-running mode).
- The ``--once`` flag (single-tick mode).
- The ``quota_pause.json`` checkpoint file is described.
- The recovery-probe mechanism mentioned.
- Multi-session awareness (per-session quota_pause.json paths).
- Exit-codes table.
- Worked examples for both modes.
- Contents table entry linking to the new section.
- No em-dash characters (U+2014) in the file.

Spec source: spec/devbench-self-improve.md section 4.5.3. Issue: #193. AC-193-14.
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
    level = len(heading.split(" ", maxsplit=1)[0])  # count leading '#'
    marker = "\n" + "#" * level + " "
    next_idx = section_text.find(marker, 1)
    if next_idx != -1:
        return section_text[:next_idx]
    return section_text


@pytest.mark.unit
class TestQuotaWatcherSectionExists:
    """A quota-watcher section must exist in cli-reference.md (AC-193-14)."""

    def test_quota_watcher_subcommand_heading_present(self) -> None:
        """The doc must have a ### `quota-watcher` section heading."""
        text = _read_doc()
        assert "### `quota-watcher`" in text, (
            "docs/cli-reference.md must contain a '### `quota-watcher`' section "
            "documenting the quota-watcher subcommand (spec section 4.5.3, AC-193-14)."
        )

    def test_quota_watcher_section_is_nonempty(self) -> None:
        """The quota-watcher section must contain substantive prose."""
        text = _read_doc()
        section = _extract_section(text, "### `quota-watcher`")
        assert section, "### `quota-watcher` section must exist in cli-reference.md"
        lines = [ln.strip() for ln in section.splitlines() if ln.strip()]
        assert len(lines) > 3, (
            "docs/cli-reference.md '### `quota-watcher`' section must contain substantive "
            "documentation, not just a heading (spec section 4.5.3)."
        )

    def test_quota_wait_and_resume_group_section_present(self) -> None:
        """A ## Quota wait-and-resume group section must be present."""
        text = _read_doc()
        assert "## Quota wait-and-resume" in text, (
            "docs/cli-reference.md must contain a '## Quota wait-and-resume' group section "
            "to house the quota-watcher subcommand (spec section 4.5, AC-193-14)."
        )


@pytest.mark.unit
class TestQuotaWatcherDaemonFlag:
    """The --daemon flag must be documented (spec 4.5.3)."""

    def test_daemon_flag_present(self) -> None:
        """The quota-watcher section must document the --daemon flag."""
        text = _read_doc()
        section = _extract_section(text, "### `quota-watcher`")
        assert section, "### `quota-watcher` section must exist"
        assert "--daemon" in section, (
            "docs/cli-reference.md '### `quota-watcher`' section must document the "
            "'--daemon' flag for long-running quota polling (spec section 4.5.3, AC-193-14)."
        )

    def test_daemon_described_as_long_running(self) -> None:
        """The doc must describe --daemon as long-running / continuous polling."""
        text = _read_doc()
        section = _extract_section(text, "### `quota-watcher`")
        assert section, "### `quota-watcher` section must exist"
        lower = section.lower()
        has_long_running = (
            "long-running" in lower or "long running" in lower or "continuous" in lower or "daemon" in lower
        )
        assert has_long_running, (
            "docs/cli-reference.md '### `quota-watcher`' section must describe --daemon "
            "as long-running / continuous polling mode (spec section 4.5.3)."
        )


@pytest.mark.unit
class TestQuotaWatcherOnceFlag:
    """The --once flag must be documented (spec 4.5.3)."""

    def test_once_flag_present(self) -> None:
        """The quota-watcher section must document the --once flag."""
        text = _read_doc()
        section = _extract_section(text, "### `quota-watcher`")
        assert section, "### `quota-watcher` section must exist"
        assert "--once" in section, (
            "docs/cli-reference.md '### `quota-watcher`' section must document the "
            "'--once' flag for single-tick polling (spec section 4.5.3, AC-193-14)."
        )

    def test_once_described_as_single_tick(self) -> None:
        """The doc must describe --once as a single-tick / one-shot operation."""
        text = _read_doc()
        section = _extract_section(text, "### `quota-watcher`")
        assert section, "### `quota-watcher` section must exist"
        lower = section.lower()
        has_single = (
            "single" in lower
            or "one-shot" in lower
            or "one shot" in lower
            or "single tick" in lower
            or "single-tick" in lower
            or "once" in lower
        )
        assert has_single, (
            "docs/cli-reference.md '### `quota-watcher`' section must describe --once "
            "as a single-tick operation (spec section 4.5.3)."
        )


@pytest.mark.unit
class TestQuotaWatcherCheckpointFile:
    """The quota_pause.json checkpoint file must be described (AC-193-8)."""

    def test_quota_pause_json_mentioned(self) -> None:
        """The quota-watcher section must mention quota_pause.json."""
        text = _read_doc()
        section = _extract_section(text, "### `quota-watcher`")
        assert section, "### `quota-watcher` section must exist"
        assert "quota_pause.json" in section, (
            "docs/cli-reference.md '### `quota-watcher`' section must mention "
            "'quota_pause.json' -- the checkpoint file written on pause and removed on resume "
            "(AC-193-8 / spec section 4.5.1)."
        )

    def test_session_scoped_path_mentioned(self) -> None:
        """The section must mention per-session quota_pause.json paths (AC-193-16)."""
        text = _read_doc()
        section = _extract_section(text, "### `quota-watcher`")
        assert section, "### `quota-watcher` section must exist"
        lower = section.lower()
        has_session_path = "session" in lower or ".devbench/sessions" in section
        assert has_session_path, (
            "docs/cli-reference.md '### `quota-watcher`' section must mention per-session "
            "quota_pause.json paths under .devbench/sessions/<name>/ (AC-193-16)."
        )


@pytest.mark.unit
class TestQuotaWatcherRecoveryProbe:
    """The recovery-probe mechanism must be described (spec 4.5.1, AC-193-18)."""

    def test_recovery_probe_mentioned(self) -> None:
        """The quota-watcher section must mention the recovery probe."""
        text = _read_doc()
        section = _extract_section(text, "### `quota-watcher`")
        assert section, "### `quota-watcher` section must exist"
        lower = section.lower()
        has_probe = "recovery_probe" in section or "recovery probe" in lower or "probe" in lower
        assert has_probe, (
            "docs/cli-reference.md '### `quota-watcher`' section must mention the "
            "recovery probe that validates quota recovery before resuming "
            "(spec section 4.5.1 / AC-193-18)."
        )


@pytest.mark.unit
class TestQuotaWatcherInteractiveMode:
    """Interactive Claude Code re-prompting must be documented (AC-193-14)."""

    def test_interactive_mode_resume_mentioned(self) -> None:
        """The section must describe re-prompting interactive Claude Code sessions on resume."""
        text = _read_doc()
        section = _extract_section(text, "### `quota-watcher`")
        assert section, "### `quota-watcher` section must exist"
        lower = section.lower()
        has_interactive = "interactive" in lower or "claude" in lower or "resume" in lower or "re-prompt" in lower
        assert has_interactive, (
            "docs/cli-reference.md '### `quota-watcher`' section must describe "
            "re-prompting interactive Claude Code sessions on quota recovery "
            "(spec section 4.5.3, AC-193-14)."
        )


@pytest.mark.unit
class TestQuotaWatcherExitCodes:
    """The quota-watcher section must include an exit-codes table or description."""

    def test_exit_codes_present(self) -> None:
        """The quota-watcher section must document exit codes."""
        text = _read_doc()
        section = _extract_section(text, "### `quota-watcher`")
        assert section, "### `quota-watcher` section must exist"
        lower = section.lower()
        has_exit = "rc=" in lower or "exit code" in lower or "exit 0" in lower or "exits 0" in lower
        assert has_exit, (
            "docs/cli-reference.md '### `quota-watcher`' section must document exit codes "
            "(spec exit-code contract for all CLI subcommands)."
        )


@pytest.mark.unit
class TestQuotaWatcherWorkedExamples:
    """The quota-watcher section must include worked examples for both modes."""

    def test_examples_code_block_present(self) -> None:
        """The section must include at least one code block with examples."""
        text = _read_doc()
        section = _extract_section(text, "### `quota-watcher`")
        assert section, "### `quota-watcher` section must exist"
        has_code_block = "```" in section
        assert has_code_block, (
            "docs/cli-reference.md '### `quota-watcher`' section must include at least one "
            "code block with worked examples (documentation standards)."
        )

    def test_daemon_example_present(self) -> None:
        """The section must include a --daemon example."""
        text = _read_doc()
        section = _extract_section(text, "### `quota-watcher`")
        assert section, "### `quota-watcher` section must exist"
        assert "--daemon" in section, (
            "docs/cli-reference.md '### `quota-watcher`' section must include a "
            "'--daemon' worked example (spec section 4.5.3)."
        )

    def test_once_example_present(self) -> None:
        """The section must include a --once example."""
        text = _read_doc()
        section = _extract_section(text, "### `quota-watcher`")
        assert section, "### `quota-watcher` section must exist"
        assert "--once" in section, (
            "docs/cli-reference.md '### `quota-watcher`' section must include a "
            "'--once' worked example (spec section 4.5.3)."
        )


@pytest.mark.unit
class TestQuotaWatcherContentsTable:
    """The Contents table must reference the quota wait-and-resume section."""

    def test_contents_includes_quota_entry(self) -> None:
        """The Contents table must link to the Quota wait-and-resume section."""
        text = _read_doc()
        contents_idx = text.find("## Contents")
        if contents_idx == -1:
            pytest.skip("no Contents table found in cli-reference.md")
        next_section = text.find("\n---", contents_idx)
        if next_section == -1:
            next_section = contents_idx + 1000
        contents_block = text[contents_idx:next_section]
        has_quota = "quota" in contents_block.lower()
        assert has_quota, (
            "docs/cli-reference.md Contents table must include an entry linking to "
            "the quota wait-and-resume section (spec section 4.5.3, AC-193-14)."
        )


@pytest.mark.unit
class TestQuotaWatcherNoEmDash:
    """The cli-reference.md must not contain em-dash characters (U+2014)."""

    def test_no_em_dash_in_quota_watcher_section(self) -> None:
        """The quota-watcher section must use -- (double hyphen) instead of em-dash."""
        text = _read_doc()
        section = _extract_section(text, "### `quota-watcher`")
        if not section:
            pytest.skip("quota-watcher section not present yet")
        assert "\u2014" not in section, (
            "docs/cli-reference.md '### `quota-watcher`' section must not contain "
            "em-dash (U+2014) characters. Use -- (double hyphen) instead. "
            "(devbench validate-backlog rule 10 / spec critical rule 8)."
        )


@pytest.mark.unit
class TestQuotaWatcherSpecReference:
    """The quota-watcher section must reference the spec and issue number."""

    def test_spec_section_reference_present(self) -> None:
        """The quota-watcher section or group section must reference spec section 4.5."""
        text = _read_doc()
        # Check in either the group ## section or the ### subcommand section
        group = _extract_section(text, "## Quota wait-and-resume")
        has_spec = "section 4.5" in group or "spec" in group.lower()
        assert has_spec, (
            "docs/cli-reference.md '## Quota wait-and-resume' section must reference "
            "spec/devbench-self-improve.md section 4.5 as the authoritative spec source "
            "(documentation standards: cross-references must resolve)."
        )

    def test_issue_193_reference_present(self) -> None:
        """The group or subcommand section must reference issue #193."""
        text = _read_doc()
        group = _extract_section(text, "## Quota wait-and-resume")
        has_issue = "#193" in group or "issue #193" in group.lower() or "Issue #193" in group
        assert has_issue, (
            "docs/cli-reference.md '## Quota wait-and-resume' section must reference "
            "Issue #193 for traceability (documentation standards)."
        )
