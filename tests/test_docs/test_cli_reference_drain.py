"""Structural pins for the drain subcommand addition in docs/cli-reference.md.

Verifies that docs/cli-reference.md documents:
- The ``devbench drain`` subcommand (AC-188-3).
- All four flag variants: bare drain, --reason, --cancel, --status.
- Pre-arm pattern (dropping marker before devbench start).
- Status banner documented (DRAIN REQUESTED header).
- Exit codes (rc=0 in both states for --status).
- Worked examples for each variant.

Spec source: spec/devbench-self-improve.md section 4.3. Issue #188.
"""

from __future__ import annotations

from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).parent.parent.parent
CLI_REFERENCE_DOC = REPO_ROOT / "docs" / "cli-reference.md"


def _read_doc() -> str:
    return CLI_REFERENCE_DOC.read_text(encoding="utf-8")


def _extract_section(text: str, heading: str) -> str:
    """Return content of the section starting at ``heading`` up to the next same-level heading."""
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
class TestDrainSubcommandExists:
    """The drain subcommand section must exist in cli-reference.md."""

    def test_drain_section_exists(self) -> None:
        """A ### `drain` section must be present in the document."""
        text = _read_doc()
        assert "### `drain`" in text, (
            "docs/cli-reference.md must contain a '### `drain`' section documenting "
            "the graceful orchestrator stop subcommand (spec section 4.3.2, AC-188-3)."
        )

    def test_drain_section_is_nonempty(self) -> None:
        """The drain section must contain prose, not just a heading."""
        text = _read_doc()
        section = _extract_section(text, "### `drain`")
        assert section, "### `drain` section must exist in cli-reference.md"
        # Must have at least some content lines beyond the heading itself.
        lines = [ln.strip() for ln in section.splitlines() if ln.strip()]
        assert len(lines) > 2, (
            "docs/cli-reference.md '### `drain`' section must contain substantive "
            "documentation, not just a heading (spec section 4.3.2)."
        )


@pytest.mark.unit
class TestDrainBareCommand:
    """devbench drain (no flags) must be documented (spec 4.3.2)."""

    def test_bare_drain_usage_documented(self) -> None:
        """The drain section must show the bare 'devbench drain' usage."""
        text = _read_doc()
        section = _extract_section(text, "### `drain`")
        assert section, "### `drain` section must exist"
        has_bare = "devbench drain" in section or "uv run devbench drain" in section
        assert has_bare, (
            "docs/cli-reference.md '### `drain`' section must document the bare "
            "'devbench drain' usage (spec section 4.3.2: requests graceful stop with "
            "empty reason; AC-188-3)."
        )

    def test_drain_creates_signal_documented(self) -> None:
        """The section must explain that drain creates the drain signal file."""
        text = _read_doc()
        section = _extract_section(text, "### `drain`")
        assert section, "### `drain` section must exist"
        lower = section.lower()
        has_signal_doc = "drain.signal" in lower or "signal" in lower or "marker" in lower
        assert has_signal_doc, (
            "docs/cli-reference.md '### `drain`' section must explain that 'devbench drain' "
            "creates the drain signal file (AC-188-1: drain creates "
            "<workspace>/.devbench/drain.signal with valid JSON payload)."
        )


@pytest.mark.unit
class TestDrainReasonFlag:
    """devbench drain --reason must be documented (spec 4.3.2)."""

    def test_reason_flag_documented(self) -> None:
        """The drain section must document the --reason flag."""
        text = _read_doc()
        section = _extract_section(text, "### `drain`")
        assert section, "### `drain` section must exist"
        assert "--reason" in section, (
            "docs/cli-reference.md '### `drain`' section must document the "
            "'--reason' flag (spec section 4.3.2: devbench drain --reason \"<text>\"; "
            "AC-188-3)."
        )

    def test_reason_flag_takes_text_argument(self) -> None:
        """The doc must show that --reason accepts a text argument."""
        text = _read_doc()
        section = _extract_section(text, "### `drain`")
        assert section, "### `drain` section must exist"
        has_text_arg = '--reason "<text>"' in section or "--reason <text>" in section or '--reason "' in section
        assert has_text_arg, (
            "docs/cli-reference.md '### `drain`' section must show that --reason "
            'accepts a text argument (spec section 4.3.2: devbench drain --reason "<text>").'
        )


@pytest.mark.unit
class TestDrainCancelFlag:
    """devbench drain --cancel must be documented (spec 4.3.2, AC-188-2)."""

    def test_cancel_flag_documented(self) -> None:
        """The drain section must document the --cancel flag."""
        text = _read_doc()
        section = _extract_section(text, "### `drain`")
        assert section, "### `drain` section must exist"
        assert "--cancel" in section, (
            "docs/cli-reference.md '### `drain`' section must document the "
            "'--cancel' flag (spec section 4.3.2: devbench drain --cancel; AC-188-2)."
        )

    def test_cancel_is_idempotent_documented(self) -> None:
        """The doc must state that --cancel is idempotent (rc=0 even when no marker)."""
        text = _read_doc()
        section = _extract_section(text, "### `drain`")
        assert section, "### `drain` section must exist"
        lower = section.lower()
        has_idempotent = (
            "idempotent" in lower or "no drain pending" in lower or "not present" in lower or "no marker" in lower
        )
        assert has_idempotent, (
            "docs/cli-reference.md '### `drain`' section must document that "
            "--cancel is idempotent (removes marker; rc=0 even when no marker present; "
            "AC-188-2)."
        )


@pytest.mark.unit
class TestDrainStatusFlag:
    """devbench drain --status must be documented (spec 4.3.2, AC-188-3)."""

    def test_status_flag_documented(self) -> None:
        """The drain section must document the --status flag."""
        text = _read_doc()
        section = _extract_section(text, "### `drain`")
        assert section, "### `drain` section must exist"
        assert "--status" in section, (
            "docs/cli-reference.md '### `drain`' section must document the "
            "'--status' flag (spec section 4.3.2: devbench drain --status; AC-188-3)."
        )

    def test_status_prints_pending_or_no_drain(self) -> None:
        """The doc must state that --status prints marker contents or 'no drain pending'."""
        text = _read_doc()
        section = _extract_section(text, "### `drain`")
        assert section, "### `drain` section must exist"
        lower = section.lower()
        has_no_drain = "no drain pending" in lower or "no drain" in lower
        assert has_no_drain, (
            "docs/cli-reference.md '### `drain`' section must document that "
            "--status prints 'no drain pending' when no marker exists (AC-188-3)."
        )

    def test_status_rc0_always(self) -> None:
        """The doc must state that --status exits rc=0 in both states."""
        text = _read_doc()
        section = _extract_section(text, "### `drain`")
        assert section, "### `drain` section must exist"
        lower = section.lower()
        has_rc0 = (
            "rc=0" in lower or "exit code 0" in lower or "exits 0" in lower or "returns 0" in lower or "rc 0" in lower
        )
        assert has_rc0, (
            "docs/cli-reference.md '### `drain`' section must document that "
            "--status exits rc=0 in both states (pending and no drain pending; AC-188-3)."
        )


@pytest.mark.unit
class TestDrainPreArmPattern:
    """The drain section must document the pre-arm pattern (AC-188-6)."""

    def test_pre_arm_pattern_mentioned(self) -> None:
        """The drain section must describe the pre-arm workflow."""
        text = _read_doc()
        section = _extract_section(text, "### `drain`")
        assert section, "### `drain` section must exist"
        lower = section.lower()
        has_pre_arm = (
            "pre-arm" in lower
            or ("before" in lower and ("start" in lower or "orchestrat" in lower))
            or ("drop" in lower and "marker" in lower)
        )
        assert has_pre_arm, (
            "docs/cli-reference.md '### `drain`' section must document the pre-arm "
            "pattern: dropping the marker before 'devbench start' causes the orchestrator "
            "to run exactly one WU then exit (AC-188-6 / spec section 4.3.2)."
        )


@pytest.mark.unit
class TestDrainStatusBanner:
    """The drain section or status section must document the DRAIN REQUESTED banner."""

    def test_drain_requested_banner_documented(self) -> None:
        """The doc must mention the 'DRAIN REQUESTED' banner shown by devbench status."""
        text = _read_doc()
        has_banner = "DRAIN REQUESTED" in text or "drain requested" in text.lower()
        assert has_banner, (
            "docs/cli-reference.md must document the 'DRAIN REQUESTED: at <ts> by <user> "
            "(reason: <text>)' banner prepended by 'devbench status' when the drain marker "
            "is present (spec section 4.3.5, AC-188-7)."
        )


@pytest.mark.unit
class TestDrainOrchestratorBehavior:
    """The drain section must document what the orchestrator does on drain signal."""

    def test_orchestrator_finish_current_wu_documented(self) -> None:
        """The doc must explain that orchestrator finishes the current WU then exits."""
        text = _read_doc()
        section = _extract_section(text, "### `drain`")
        assert section, "### `drain` section must exist"
        lower = section.lower()
        has_finish_wu = (
            ("finish" in lower and ("current" in lower or "work unit" in lower or "wu" in lower))
            or ("complete" in lower and ("current" in lower or "work unit" in lower or "wu" in lower))
            or "graceful" in lower
        )
        assert has_finish_wu, (
            "docs/cli-reference.md '### `drain`' section must document that the running "
            "orchestrator detects the marker between WUs and exits after the current WU "
            "completes (spec section 4.3.3, AC-188-4)."
        )

    def test_drain_consumed_on_exit_documented(self) -> None:
        """The doc must explain that the marker is consumed (deleted) on orchestrator exit."""
        text = _read_doc()
        section = _extract_section(text, "### `drain`")
        assert section, "### `drain` section must exist"
        lower = section.lower()
        has_consumed = "consumed" in lower or "deleted" in lower or "removed" in lower
        assert has_consumed, (
            "docs/cli-reference.md '### `drain`' section must document that the drain "
            "marker is consumed (deleted) on orchestrator exit (spec section 4.3.3, AC-188-5)."
        )


@pytest.mark.unit
class TestDrainWorkedExamples:
    """The drain section must include worked examples."""

    def test_drain_examples_present(self) -> None:
        """The drain section must include at least one code block with examples."""
        text = _read_doc()
        section = _extract_section(text, "### `drain`")
        assert section, "### `drain` section must exist"
        has_code_block = "```" in section
        assert has_code_block, (
            "docs/cli-reference.md '### `drain`' section must include at least one "
            "code block with worked examples (spec section 4.3.2)."
        )

    def test_cancel_example_present(self) -> None:
        """The drain section must include a --cancel example."""
        text = _read_doc()
        section = _extract_section(text, "### `drain`")
        assert section, "### `drain` section must exist"
        assert "--cancel" in section, (
            "docs/cli-reference.md '### `drain`' section must include a worked "
            "'--cancel' example (spec section 4.3.2, AC-188-2)."
        )

    def test_status_example_present(self) -> None:
        """The drain section must include a --status example."""
        text = _read_doc()
        section = _extract_section(text, "### `drain`")
        assert section, "### `drain` section must exist"
        assert "--status" in section, (
            "docs/cli-reference.md '### `drain`' section must include a worked "
            "'--status' example (spec section 4.3.2, AC-188-3)."
        )


@pytest.mark.unit
class TestDrainContentsTable:
    """The Contents table must reference the drain subcommand."""

    def test_contents_includes_drain_entry(self) -> None:
        """The Contents table must link to the drain section."""
        text = _read_doc()
        contents_idx = text.find("## Contents")
        if contents_idx == -1:
            pytest.skip("no Contents table found in cli-reference.md")
        next_section = text.find("\n---", contents_idx)
        if next_section == -1:
            next_section = contents_idx + 1000
        contents_block = text[contents_idx:next_section]
        has_drain = "drain" in contents_block.lower()
        assert has_drain, (
            "docs/cli-reference.md Contents table must include an entry linking to "
            "the drain subcommand section (spec section 4.3.2)."
        )
