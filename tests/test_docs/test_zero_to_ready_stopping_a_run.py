"""Structural pins for the 'Stopping a run cleanly' section in docs/zero-to-ready.md.

Verifies that docs/zero-to-ready.md documents (AC-188-3):
- A dedicated 'Stopping a run cleanly' section showing how to drain the
  orchestrator gracefully between work units.
- The ``devbench drain`` command variants: bare, --reason, --cancel, --status.
- The pre-arm pattern: dropping the marker before ``devbench start`` so the
  orchestrator runs exactly one WU then exits.
- The ``devbench drain --status`` check with rc=0 in both states.
- The orchestrator finish-current-WU-then-exit semantics.
- The drain marker consumed (deleted) on orchestrator exit.
- A cross-reference to docs/cli-reference.md for the full drain reference.
- The section appears in the Table of contents.

Spec source: spec/devbench-self-improve.md section 4.3. Issue: #188.
"""

from __future__ import annotations

from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).parent.parent.parent
ZERO_TO_READY_DOC = REPO_ROOT / "docs" / "zero-to-ready.md"


def _read_doc() -> str:
    return ZERO_TO_READY_DOC.read_text(encoding="utf-8")


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


def _get_stopping_section() -> str:
    """Return the text of the 'Stopping a run cleanly' section from docs/zero-to-ready.md."""
    text = _read_doc()
    for heading in (
        "## Stopping a run cleanly",
        "### Stopping a run cleanly",
        "## Stopping a Run Cleanly",
        "### Stopping a Run Cleanly",
    ):
        section = _extract_section(text, heading)
        if section:
            return section
    return ""


@pytest.mark.unit
class TestStoppingARunSectionPresence:
    """AC-188-3: docs/zero-to-ready.md must contain a 'Stopping a run cleanly' section."""

    def test_stopping_section_heading_exists(self) -> None:
        """The doc must have a 'Stopping a run cleanly' (or equivalent) section heading."""
        text = _read_doc()
        has_heading = (
            "## Stopping a run cleanly" in text
            or "### Stopping a run cleanly" in text
            or "## Stopping a Run Cleanly" in text
            or "### Stopping a Run Cleanly" in text
        )
        assert has_heading, (
            "docs/zero-to-ready.md must contain a 'Stopping a run cleanly' section heading "
            "showing operators how to drain the orchestrator gracefully between work units "
            "(AC-188-3 / spec section 4.3)."
        )

    def test_stopping_section_in_table_of_contents(self) -> None:
        """The 'Stopping a run cleanly' section must appear in the Table of contents."""
        text = _read_doc()
        toc_idx = text.find("## Table of contents")
        assert toc_idx != -1, "docs/zero-to-ready.md must have a '## Table of contents' section."
        next_section = text.find("\n---", toc_idx)
        if next_section == -1:
            next_section = toc_idx + 2000
        toc_block = text[toc_idx:next_section]
        lower = toc_block.lower()
        has_stopping_entry = "stopping" in lower or "drain" in lower
        assert has_stopping_entry, (
            "docs/zero-to-ready.md Table of contents must include a link to the "
            "'Stopping a run cleanly' section (AC-188-3)."
        )


@pytest.mark.unit
class TestStoppingARunDrainCommand:
    """AC-188-3: The stopping section must document the devbench drain command."""

    def test_drain_command_documented(self) -> None:
        """The stopping section must mention the devbench drain command."""
        section = _get_stopping_section()
        assert section, "A 'Stopping a run cleanly' section must exist in docs/zero-to-ready.md."
        has_drain = "devbench drain" in section or "drain" in section.lower()
        assert has_drain, (
            "docs/zero-to-ready.md 'Stopping a run cleanly' section must document "
            "the 'devbench drain' command (AC-188-3 / spec section 4.3.2)."
        )

    def test_reason_flag_documented(self) -> None:
        """The stopping section must document the --reason flag."""
        section = _get_stopping_section()
        assert section, "A 'Stopping a run cleanly' section must exist in docs/zero-to-ready.md."
        assert "--reason" in section, (
            "docs/zero-to-ready.md 'Stopping a run cleanly' section must document "
            "the '--reason' flag variant (spec section 4.3.2)."
        )

    def test_cancel_flag_documented(self) -> None:
        """The stopping section must document the --cancel flag."""
        section = _get_stopping_section()
        assert section, "A 'Stopping a run cleanly' section must exist in docs/zero-to-ready.md."
        assert "--cancel" in section, (
            "docs/zero-to-ready.md 'Stopping a run cleanly' section must document "
            "the '--cancel' flag to withdraw a drain request (AC-188-2 / spec 4.3.2)."
        )

    def test_status_flag_documented(self) -> None:
        """The stopping section must document the --status flag."""
        section = _get_stopping_section()
        assert section, "A 'Stopping a run cleanly' section must exist in docs/zero-to-ready.md."
        assert "--status" in section, (
            "docs/zero-to-ready.md 'Stopping a run cleanly' section must document "
            "the '--status' flag (AC-188-3 / spec section 4.3.2)."
        )

    def test_status_prints_pending_or_no_drain_documented(self) -> None:
        """The doc must state that --status prints marker contents or 'no drain pending'."""
        section = _get_stopping_section()
        assert section, "A 'Stopping a run cleanly' section must exist in docs/zero-to-ready.md."
        lower = section.lower()
        has_no_drain = "no drain pending" in lower or "no drain" in lower
        assert has_no_drain, (
            "docs/zero-to-ready.md 'Stopping a run cleanly' section must document that "
            "'--status' prints 'no drain pending' when no marker exists (AC-188-3)."
        )

    def test_status_rc0_always_documented(self) -> None:
        """The doc must state that --status exits rc=0 in both states."""
        section = _get_stopping_section()
        assert section, "A 'Stopping a run cleanly' section must exist in docs/zero-to-ready.md."
        lower = section.lower()
        has_rc0 = (
            "rc=0" in lower or "exit code 0" in lower or "exits 0" in lower or "returns 0" in lower or "rc 0" in lower
        )
        assert has_rc0, (
            "docs/zero-to-ready.md 'Stopping a run cleanly' section must document that "
            "'--status' exits rc=0 in both states (pending and no drain pending; AC-188-3)."
        )


@pytest.mark.unit
class TestStoppingARunOrchestratorSemantics:
    """AC-188-3: The section must explain the orchestrator's drain behaviour."""

    def test_orchestrator_finishes_current_wu_documented(self) -> None:
        """The doc must explain that the orchestrator finishes the current WU then exits."""
        section = _get_stopping_section()
        assert section, "A 'Stopping a run cleanly' section must exist in docs/zero-to-ready.md."
        lower = section.lower()
        has_finish = (
            ("finish" in lower and ("current" in lower or "work unit" in lower or "wu" in lower))
            or ("complete" in lower and ("current" in lower or "work unit" in lower or "wu" in lower))
            or "graceful" in lower
        )
        assert has_finish, (
            "docs/zero-to-ready.md 'Stopping a run cleanly' section must document that "
            "the orchestrator finishes the current WU before exiting (AC-188-4 / spec 4.3.3)."
        )

    def test_drain_marker_consumed_on_exit_documented(self) -> None:
        """The doc must explain that the drain marker is consumed on orchestrator exit."""
        section = _get_stopping_section()
        assert section, "A 'Stopping a run cleanly' section must exist in docs/zero-to-ready.md."
        lower = section.lower()
        has_consumed = "consumed" in lower or "deleted" in lower or "removed" in lower
        assert has_consumed, (
            "docs/zero-to-ready.md 'Stopping a run cleanly' section must document that "
            "the drain marker is consumed (deleted) on orchestrator exit (AC-188-5 / spec 4.3.3)."
        )

    def test_pre_arm_pattern_documented(self) -> None:
        """The doc must document the pre-arm pattern: dropping marker before devbench start."""
        section = _get_stopping_section()
        assert section, "A 'Stopping a run cleanly' section must exist in docs/zero-to-ready.md."
        lower = section.lower()
        has_pre_arm = (
            "pre-arm" in lower
            or ("before" in lower and ("start" in lower or "orchestrat" in lower))
            or ("drop" in lower and "marker" in lower)
            or "one wu" in lower
            or "one work unit" in lower
        )
        assert has_pre_arm, (
            "docs/zero-to-ready.md 'Stopping a run cleanly' section must document the "
            "pre-arm pattern: dropping the drain marker before 'devbench start' causes "
            "the orchestrator to run exactly one WU then exit (AC-188-6 / spec 4.3.2)."
        )


@pytest.mark.unit
class TestStoppingARunCodeExamples:
    """AC-188-3: The section must include worked command examples."""

    def test_section_has_code_block(self) -> None:
        """The stopping section must include at least one code block with examples."""
        section = _get_stopping_section()
        assert section, "A 'Stopping a run cleanly' section must exist in docs/zero-to-ready.md."
        assert "```" in section, (
            "docs/zero-to-ready.md 'Stopping a run cleanly' section must include at least "
            "one code block with worked examples (spec section 4.3.2)."
        )

    def test_drain_status_example_present(self) -> None:
        """The section must include a 'devbench drain --status' example."""
        section = _get_stopping_section()
        assert section, "A 'Stopping a run cleanly' section must exist in docs/zero-to-ready.md."
        assert "drain --status" in section, (
            "docs/zero-to-ready.md 'Stopping a run cleanly' section must include a worked "
            "'devbench drain --status' example (AC-188-3 / spec section 4.3.2)."
        )

    def test_drain_cancel_example_present(self) -> None:
        """The section must include a 'devbench drain --cancel' example."""
        section = _get_stopping_section()
        assert section, "A 'Stopping a run cleanly' section must exist in docs/zero-to-ready.md."
        assert "drain --cancel" in section, (
            "docs/zero-to-ready.md 'Stopping a run cleanly' section must include a worked "
            "'devbench drain --cancel' example (AC-188-2 / spec section 4.3.2)."
        )


@pytest.mark.unit
class TestStoppingARunCrossReference:
    """AC-188-3: The section must cross-reference docs/cli-reference.md for the full drain reference."""

    def test_cli_reference_cross_reference_present(self) -> None:
        """The stopping section must cross-reference docs/cli-reference.md."""
        section = _get_stopping_section()
        assert section, "A 'Stopping a run cleanly' section must exist in docs/zero-to-ready.md."
        has_cli_ref = "cli-reference" in section.lower() or "cli-reference.md" in section.lower()
        assert has_cli_ref, (
            "docs/zero-to-ready.md 'Stopping a run cleanly' section must cross-reference "
            "docs/cli-reference.md for the full drain subcommand reference (spec 4.3.2)."
        )
