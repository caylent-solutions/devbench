"""Structural pins for the 'Scoping a run' section in docs/zero-to-ready.md.

Verifies that docs/zero-to-ready.md documents (AC-190-8):
- A dedicated 'Scoping a run' section showing how to restrict the orchestrator
  to a subset of the backlog using printer-pages-style --include / --exclude flags.
- The printer-pages syntax reference: single-ID tokens, range tokens, mixed lists.
- The scope.json persistence behaviour when --include is supplied to devbench start.
- The devbench scope set / clear / show pre-arm workflow (spec section 4.2.6).
- Worked examples including simple and complex cases.

Spec source: spec/devbench-self-improve.md section 4.2. Issue: #190.
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


def _get_scoping_section() -> str:
    """Return the text of the 'Scoping a run' section from docs/zero-to-ready.md."""
    text = _read_doc()
    for heading in (
        "## Scoping a run",
        "### Scoping a run",
        "## Scoping a Run",
        "### Scoping a Run",
    ):
        section = _extract_section(text, heading)
        if section:
            return section
    return ""


@pytest.mark.unit
class TestScopingARunSectionPresence:
    """AC-190-8: docs/zero-to-ready.md must contain a 'Scoping a run' section."""

    def test_scoping_a_run_section_heading_exists(self) -> None:
        """The doc must have a 'Scoping a run' (or equivalent) section heading."""
        text = _read_doc()
        has_heading = (
            "## Scoping a run" in text
            or "### Scoping a run" in text
            or "## Scoping a Run" in text
            or "### Scoping a Run" in text
        )
        assert has_heading, (
            "docs/zero-to-ready.md must contain a 'Scoping a run' section heading "
            "showing operators how to restrict the orchestrator to a subset of the backlog "
            "(AC-190-8 / spec section 4.2)."
        )

    def test_scoping_section_in_table_of_contents(self) -> None:
        """The 'Scoping a run' section must appear in the Table of contents."""
        text = _read_doc()
        toc_idx = text.find("## Table of contents")
        assert toc_idx != -1, "docs/zero-to-ready.md must have a '## Table of contents' section."
        next_section = text.find("\n---", toc_idx)
        if next_section == -1:
            next_section = toc_idx + 2000
        toc_block = text[toc_idx:next_section]
        lower = toc_block.lower()
        has_scope_entry = "scoping" in lower
        assert has_scope_entry, (
            "docs/zero-to-ready.md Table of contents must include a link to the 'Scoping a run' section (AC-190-8)."
        )


@pytest.mark.unit
class TestScopingARunIncludeExcludeFlags:
    """AC-190-8: The scoping section must document --include and --exclude flags."""

    def test_include_flag_documented(self) -> None:
        """The scoping section must document the --include flag."""
        section = _get_scoping_section()
        assert section, "A 'Scoping a run' section must exist in docs/zero-to-ready.md."
        assert "--include" in section, (
            "docs/zero-to-ready.md 'Scoping a run' section must document the --include flag "
            "for restricting the orchestrator to a subset of the backlog (AC-190-8)."
        )

    def test_exclude_flag_documented(self) -> None:
        """The scoping section must document the --exclude flag."""
        section = _get_scoping_section()
        assert section, "A 'Scoping a run' section must exist in docs/zero-to-ready.md."
        assert "--exclude" in section, (
            "docs/zero-to-ready.md 'Scoping a run' section must document the --exclude flag "
            "for subtracting work units from the include set (AC-190-8)."
        )

    def test_devbench_start_include_example_present(self) -> None:
        """The scoping section must show 'devbench start --include' or equivalent."""
        section = _get_scoping_section()
        assert section, "A 'Scoping a run' section must exist in docs/zero-to-ready.md."
        has_start_include = "devbench start --include" in section or "start --include" in section
        assert has_start_include, (
            "docs/zero-to-ready.md 'Scoping a run' section must show a 'devbench start "
            "--include ...' example command (AC-190-8)."
        )


@pytest.mark.unit
class TestScopingARunPrinterPagesSyntaxReference:
    """AC-190-8: The section must reference the printer-pages token syntax."""

    def test_printer_pages_syntax_mentioned(self) -> None:
        """The scoping section must mention printer-pages-style syntax or reference tokens."""
        section = _get_scoping_section()
        assert section, "A 'Scoping a run' section must exist in docs/zero-to-ready.md."
        lower = section.lower()
        has_syntax_ref = (
            "printer-pages" in lower
            or "printer pages" in lower
            or "token" in lower
            or "single-id" in lower
            or "range" in lower
        )
        assert has_syntax_ref, (
            "docs/zero-to-ready.md 'Scoping a run' section must mention printer-pages "
            "syntax or describe single-ID / range tokens (AC-190-8 / spec section 4.2.1)."
        )

    def test_range_token_example_present(self) -> None:
        """The scoping section must show a range token example such as 'E1-E3'."""
        section = _get_scoping_section()
        assert section, "A 'Scoping a run' section must exist in docs/zero-to-ready.md."
        has_range = "E1-E3" in section or "E1-E5" in section or "E1-E10" in section
        assert has_range, (
            "docs/zero-to-ready.md 'Scoping a run' section must include a worked "
            "range-token example such as 'E1-E3' or 'E1-E10' (AC-190-8 / spec 4.2.1)."
        )

    def test_mixed_list_example_present(self) -> None:
        """The scoping section must show a comma-separated mixed-token example."""
        section = _get_scoping_section()
        assert section, "A 'Scoping a run' section must exist in docs/zero-to-ready.md."
        has_mixed = "E1-E3, E5" in section or "E1-E3,E5" in section or ", E5" in section
        assert has_mixed, (
            "docs/zero-to-ready.md 'Scoping a run' section must include a comma-separated "
            "mixed-token example such as 'E1-E3, E5' (AC-190-8 / spec 4.2.1)."
        )

    def test_cli_reference_cross_reference_present(self) -> None:
        """The scoping section must cross-reference docs/cli-reference.md for full syntax."""
        section = _get_scoping_section()
        assert section, "A 'Scoping a run' section must exist in docs/zero-to-ready.md."
        has_cli_ref = "cli-reference" in section.lower() or "cli-reference.md" in section.lower()
        assert has_cli_ref, (
            "docs/zero-to-ready.md 'Scoping a run' section must cross-reference "
            "docs/cli-reference.md for the full scope-selector syntax (AC-190-8)."
        )


@pytest.mark.unit
class TestScopingARunScopeJsonPersistence:
    """AC-190-8: scope.json written by --include must be mentioned in zero-to-ready."""

    def test_scope_json_mentioned(self) -> None:
        """The scoping section must mention scope.json persistence."""
        section = _get_scoping_section()
        assert section, "A 'Scoping a run' section must exist in docs/zero-to-ready.md."
        lower = section.lower()
        has_scope_json = "scope.json" in lower or ".devbench/scope" in lower
        assert has_scope_json, (
            "docs/zero-to-ready.md 'Scoping a run' section must mention scope.json "
            "-- the persistence file written by 'devbench start --include' (AC-190-8)."
        )

    def test_scope_cleared_on_exit_mentioned(self) -> None:
        """The scoping section must note that scope.json is cleared on clean exit."""
        section = _get_scoping_section()
        assert section, "A 'Scoping a run' section must exist in docs/zero-to-ready.md."
        lower = section.lower()
        has_clear_note = (
            "clean exit" in lower
            or "consumed" in lower
            or "deleted" in lower
            or "cleared" in lower
            or "removed" in lower
        )
        assert has_clear_note, (
            "docs/zero-to-ready.md 'Scoping a run' section must note that scope.json "
            "is cleared (deleted) on clean orchestrator exit (AC-190-13 / spec 4.2.5)."
        )


@pytest.mark.unit
class TestScopingARunPreArmWorkflow:
    """AC-190-8: The section must document the devbench scope set pre-arm workflow."""

    def test_scope_set_command_documented(self) -> None:
        """The scoping section must document 'devbench scope set' for pre-arming."""
        section = _get_scoping_section()
        assert section, "A 'Scoping a run' section must exist in docs/zero-to-ready.md."
        assert "scope set" in section, (
            "docs/zero-to-ready.md 'Scoping a run' section must document "
            "'devbench scope set --include ...' for pre-arming scope before launching "
            "interactive Claude Code (spec section 4.2.6.3)."
        )

    def test_scope_clear_command_documented(self) -> None:
        """The scoping section must document 'devbench scope clear'."""
        section = _get_scoping_section()
        assert section, "A 'Scoping a run' section must exist in docs/zero-to-ready.md."
        assert "scope clear" in section, (
            "docs/zero-to-ready.md 'Scoping a run' section must document "
            "'devbench scope clear' to remove the active scope (spec section 4.2.6.2)."
        )
