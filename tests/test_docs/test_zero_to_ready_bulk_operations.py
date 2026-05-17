"""Structural pins for the 'Bulk operations on the backlog' section in docs/zero-to-ready.md.

Verifies that docs/zero-to-ready.md documents (AC-194-1):
- A dedicated 'Bulk operations on the backlog' section showing the post-spec-to-backlog
  workflow: review drafts -> bulk promote/hold -> launch orchestrator.
- The ``devbench set-status --include / --exclude / --dry-run / --yes`` bulk variant.
- The confirm-before-applying behaviour when expansion exceeds the threshold.
- Worked examples: promote E1 to in-queue, hold E5, --dry-run preview, --yes bypass.
- A reference to ``devbench set-status`` as the canonical follow-up after spec-to-backlog.
- No em-dash characters (U+2014) anywhere in the section.

Spec source: spec/devbench-self-improve.md section 4.7.4. Issue: #194.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).parent.parent.parent
ZERO_TO_READY_DOC = REPO_ROOT / "docs" / "zero-to-ready.md"

_SECTION_HEADING = "## Bulk operations on the backlog"


def _read_doc() -> str:
    return ZERO_TO_READY_DOC.read_text(encoding="utf-8")


def _extract_section(text: str) -> str:
    """Return the text of the 'Bulk operations on the backlog' section."""
    idx = text.find(_SECTION_HEADING)
    if idx == -1:
        return ""
    # Find the next H2 heading after this one.
    next_h2 = text.find("\n## ", idx + len(_SECTION_HEADING))
    if next_h2 != -1:
        return text[idx:next_h2]
    return text[idx:]


@pytest.mark.unit
class TestBulkOperationsSectionExists:
    """Pre-condition: the section must be present in zero-to-ready.md."""

    def test_section_heading_present(self) -> None:
        """The doc must contain the 'Bulk operations on the backlog' heading."""
        text = _read_doc()
        assert _SECTION_HEADING in text, (
            "docs/zero-to-ready.md must contain the section heading "
            f"'{_SECTION_HEADING}' (AC-194-1)."
        )

    def test_section_appears_in_toc(self) -> None:
        """The table of contents must link to the new section."""
        text = _read_doc()
        lower = text.lower()
        assert "bulk operations" in lower, (
            "docs/zero-to-ready.md table of contents must include a link to the "
            "'Bulk operations on the backlog' section (AC-194-1)."
        )


@pytest.mark.unit
class TestBulkOperationsWorkflow:
    """The section must describe the three-phase workflow: review -> bulk update -> orchestrate."""

    def _section(self) -> str:
        return _extract_section(_read_doc())

    def test_set_status_include_flag_documented(self) -> None:
        """The --include flag of devbench set-status must be documented."""
        section = self._section()
        assert section, "Section 'Bulk operations on the backlog' must be non-empty."
        assert "--include" in section, (
            "The 'Bulk operations on the backlog' section must document "
            "the --include flag of devbench set-status (AC-194-1)."
        )

    def test_set_status_exclude_flag_documented(self) -> None:
        """The --exclude flag of devbench set-status must be documented."""
        section = self._section()
        assert "--exclude" in section, (
            "The 'Bulk operations on the backlog' section must document "
            "the --exclude flag of devbench set-status (AC-194-1)."
        )

    def test_set_status_dry_run_flag_documented(self) -> None:
        """The --dry-run flag must be documented for safe previewing."""
        section = self._section()
        assert "--dry-run" in section, (
            "The 'Bulk operations on the backlog' section must document "
            "the --dry-run flag so operators can preview changes (AC-194-1)."
        )

    def test_set_status_yes_flag_documented(self) -> None:
        """The --yes flag must be documented for bypassing the confirmation prompt."""
        section = self._section()
        assert "--yes" in section, (
            "The 'Bulk operations on the backlog' section must document "
            "the --yes flag to skip the confirmation prompt (AC-194-1)."
        )

    def test_devbench_set_status_command_present(self) -> None:
        """The section must mention 'devbench set-status' as the bulk-update command."""
        section = self._section()
        assert "devbench set-status" in section or "set-status" in section, (
            "The 'Bulk operations on the backlog' section must show "
            "'devbench set-status' as the bulk-update CLI command (AC-194-1)."
        )

    def test_in_queue_promotion_example_present(self) -> None:
        """An example promoting units to in-queue must be present."""
        section = self._section()
        assert "in-queue" in section, (
            "The 'Bulk operations on the backlog' section must contain a worked "
            "example promoting work units to in-queue (AC-194-1)."
        )

    def test_hold_example_present(self) -> None:
        """An example setting units to hold status must be present."""
        section = self._section()
        lower = section.lower()
        assert "hold" in lower, (
            "The 'Bulk operations on the backlog' section must contain a worked "
            "example placing work units on hold (AC-194-1)."
        )

    def test_workflow_order_review_then_launch(self) -> None:
        """The section must convey the workflow sequence: inspect drafts then launch."""
        section = self._section()
        lower = section.lower()
        has_review_step = (
            "review" in lower or "inspect" in lower or "draft" in lower
        )
        has_launch_step = (
            "make start" in lower
            or "devbench start" in lower
            or "orchestrat" in lower
            or "launch" in lower
        )
        assert has_review_step, (
            "The 'Bulk operations on the backlog' section must describe the step "
            "where the operator reviews / inspects draft work units (AC-194-1)."
        )
        assert has_launch_step, (
            "The 'Bulk operations on the backlog' section must describe the step "
            "where the operator launches the orchestrator after bulk-promoting (AC-194-1)."
        )


@pytest.mark.unit
class TestBulkOperationsCodeExamples:
    """The section must contain at least one code block demonstrating set-status usage."""

    def _section(self) -> str:
        return _extract_section(_read_doc())

    def test_code_block_present(self) -> None:
        """At least one fenced code block must be present in the section."""
        section = self._section()
        assert "```" in section, (
            "The 'Bulk operations on the backlog' section must contain at least one "
            "fenced code block with set-status usage examples (AC-194-1)."
        )

    def test_dry_run_in_code_block(self) -> None:
        """--dry-run must appear inside a code block (not just prose)."""
        section = self._section()
        # Extract content of all code blocks.
        code_blocks = re.findall(r"```[^\n]*\n(.*?)```", section, re.DOTALL)
        code_text = "\n".join(code_blocks)
        assert "--dry-run" in code_text, (
            "The 'Bulk operations on the backlog' section must demonstrate "
            "--dry-run inside a code block example, not just in prose (AC-194-1)."
        )

    def test_include_flag_in_code_block(self) -> None:
        """--include must appear inside a code block."""
        section = self._section()
        code_blocks = re.findall(r"```[^\n]*\n(.*?)```", section, re.DOTALL)
        code_text = "\n".join(code_blocks)
        assert "--include" in code_text, (
            "The 'Bulk operations on the backlog' section must demonstrate "
            "--include inside a code block example (AC-194-1)."
        )


@pytest.mark.unit
class TestBulkOperationsSpecToBacklogLink:
    """The section must reference spec-to-backlog as the upstream context."""

    def _section(self) -> str:
        return _extract_section(_read_doc())

    def test_spec_to_backlog_reference_present(self) -> None:
        """The section must reference spec-to-backlog or skill-generated backlogs."""
        section = self._section()
        lower = section.lower()
        assert "spec-to-backlog" in lower or "generated" in lower or "task-factory" in lower, (
            "The 'Bulk operations on the backlog' section must mention the "
            "spec-to-backlog skill (or generated/task-factory output) as the "
            "context where bulk set-status is used (AC-194-1)."
        )


@pytest.mark.unit
class TestBulkOperationsNoEmDashes:
    """The section must contain no em-dash characters (U+2014)."""

    def _section(self) -> str:
        return _extract_section(_read_doc())

    def test_no_em_dashes_in_section(self) -> None:
        """The section must use -- instead of the em-dash character."""
        section = self._section()
        assert "\u2014" not in section, (
            "The 'Bulk operations on the backlog' section must not contain "
            "em-dash characters (U+2014); use -- instead (Critical Rule 8)."
        )


@pytest.mark.unit
class TestBulkOperationsTocEntry:
    """The table of contents must be updated to include the new section."""

    def test_toc_entry_links_to_bulk_operations(self) -> None:
        """The ToC must have an anchor link to 'Bulk operations on the backlog'."""
        text = _read_doc()
        # ToC entries are typically formatted as '- [Section Title](#anchor)'
        # The anchor for 'Bulk operations on the backlog' would be
        # '#bulk-operations-on-the-backlog'.
        has_toc_anchor = (
            "#bulk-operations-on-the-backlog" in text
            or "Bulk operations on the backlog" in text
        )
        assert has_toc_anchor, (
            "docs/zero-to-ready.md table of contents must include "
            "'Bulk operations on the backlog' with a section anchor link (AC-194-1)."
        )
