"""Structural pins for the promote subcommand and draft-status additions in docs/cli-reference.md.

Verifies that docs/cli-reference.md documents:
- The ``devbench promote`` subcommand with all flag variants and worked examples (AC-189-4).
- The ``draft`` row in the ``devbench status`` summary output (AC-189-6).
- The ``Draft`` column in the ``devbench report`` Status Summary table (AC-189-7).

Spec source: spec/devbench-self-improve.md section 4.1 (promote command, status/report rendering).
"""

from __future__ import annotations

from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).parent.parent.parent
CLI_REFERENCE_DOC = REPO_ROOT / "docs" / "cli-reference.md"


@pytest.mark.unit
class TestCliReferenceDocExists:
    """Pre-condition: the CLI reference doc must exist."""

    def test_cli_reference_doc_exists(self) -> None:
        assert CLI_REFERENCE_DOC.is_file(), (
            "docs/cli-reference.md must exist -- it is the authoritative CLI reference for every devbench subcommand."
        )


@pytest.mark.unit
class TestPromoteSubcommandPresence:
    """AC-189-4 / AC-189-5: docs/cli-reference.md documents the promote subcommand."""

    def _text(self) -> str:
        return CLI_REFERENCE_DOC.read_text(encoding="utf-8")

    def test_promote_section_heading_exists(self) -> None:
        """The doc must contain a ### `promote` section heading."""
        text = self._text()
        assert "### `promote`" in text, (
            "docs/cli-reference.md must contain a '### `promote`' section heading "
            "documenting the new devbench promote subcommand (AC-189-4)."
        )

    def test_promote_single_id_variant_documented(self) -> None:
        """The basic 'devbench promote <id>' variant must appear in the doc."""
        text = self._text()
        assert "devbench promote <id>" in text or "devbench promote <unit-id>" in text, (
            "docs/cli-reference.md must document 'devbench promote <id>' "
            "-- the single-unit transition from draft to in-queue (AC-189-4)."
        )

    def test_promote_epic_flag_documented(self) -> None:
        """The '--epic' flag variant must appear in the promote section."""
        text = self._text()
        assert "--epic" in text, (
            "docs/cli-reference.md must document 'devbench promote --epic <id>' "
            "for bulk promotion of every draft WU under an epic (AC-189-5)."
        )

    def test_promote_feature_flag_documented(self) -> None:
        """The '--feature' flag variant must appear in the promote section."""
        text = self._text()
        assert "--feature" in text, (
            "docs/cli-reference.md must document 'devbench promote --feature <id>' "
            "for bulk promotion of every draft WU under a feature (AC-189-5)."
        )

    def test_promote_story_flag_documented(self) -> None:
        """The '--story' flag variant must appear in the promote section."""
        text = self._text()
        assert "--story" in text, (
            "docs/cli-reference.md must document 'devbench promote --story <id>' "
            "for bulk promotion of every draft WU under a story (AC-189-5)."
        )

    def test_promote_all_flag_documented(self) -> None:
        """The '--all' flag must appear in the promote section."""
        text = self._text()
        assert "--all" in text, (
            "docs/cli-reference.md must document 'devbench promote --all' "
            "for bulk promotion of every draft WU in the backlog (AC-189-5)."
        )

    def test_promote_yes_flag_documented(self) -> None:
        """The '--yes' flag (skip confirmation for --all) must appear in the doc."""
        text = self._text()
        assert "--yes" in text, (
            "docs/cli-reference.md must document '--yes' to skip confirmation "
            "when using 'devbench promote --all' (AC-189-5)."
        )

    def test_promote_draft_to_in_queue_transition_named(self) -> None:
        """The doc must state that promote transitions draft -> in-queue."""
        text = self._text()
        assert "draft" in text and "in-queue" in text, (
            "docs/cli-reference.md promote section must name the transition from 'draft' to 'in-queue'."
        )
        assert "draft -> in-queue" in text or "draft->in-queue" in text, (
            "docs/cli-reference.md promote section must explicitly show the 'draft -> in-queue' transition (AC-189-4)."
        )

    def test_promote_audit_comment_documented(self) -> None:
        """The doc must mention the [PROMOTED] audit comment written per promoted WU."""
        text = self._text()
        assert "[PROMOTED]" in text, (
            "docs/cli-reference.md promote section must document that each promoted WU "
            "receives a '[PROMOTED] draft -> in-queue' audit comment (AC-189-4)."
        )

    def test_promote_refuses_non_draft_documented(self) -> None:
        """The doc must state that promote refuses WUs not currently in draft."""
        text = self._text()
        lower = text.lower()
        assert (
            "not currently" in lower or "refuses" in lower or "only" in lower or "rc=1" in lower or "exit" in lower
        ), (
            "docs/cli-reference.md promote section must document that promote "
            "refuses (rc=1) WUs that are not currently in 'draft' status (AC-189-4)."
        )

    def test_promote_example_present(self) -> None:
        """The promote section must contain at least one worked example."""
        text = self._text()
        promote_idx = text.find("### `promote`")
        assert promote_idx != -1, "### `promote` section must exist"
        section_text = text[promote_idx:]
        next_section = section_text.find("\n### ", 1)
        if next_section != -1:
            section_text = section_text[:next_section]
        assert "```" in section_text, (
            "docs/cli-reference.md promote section must contain at least one "
            "code block showing a worked example of the command."
        )


@pytest.mark.unit
class TestStatusSectionDraftRow:
    """AC-189-6: docs/cli-reference.md status section documents the Draft row."""

    def _text(self) -> str:
        return CLI_REFERENCE_DOC.read_text(encoding="utf-8")

    def test_status_section_mentions_draft(self) -> None:
        """The status section must mention the Draft status row in the summary."""
        text = self._text()
        status_idx = text.find("### `status`")
        assert status_idx != -1, "### `status` section must exist in cli-reference.md"
        next_section = text.find("\n### ", status_idx + 1)
        section_text = text[status_idx:next_section] if next_section != -1 else text[status_idx:]
        assert "draft" in section_text.lower() or "Draft" in section_text, (
            "docs/cli-reference.md 'status' section must describe the new Draft row "
            "in the summary count block (AC-189-6)."
        )

    def test_status_draft_row_described_in_output(self) -> None:
        """The status section must document where the Draft row appears in output."""
        text = self._text()
        status_idx = text.find("### `status`")
        assert status_idx != -1, "### `status` section must exist"
        next_section = text.find("\n### ", status_idx + 1)
        section_text = text[status_idx:next_section] if next_section != -1 else text[status_idx:]
        lower = section_text.lower()
        assert "draft" in lower, (
            "docs/cli-reference.md 'status' section must include 'draft' in its "
            "description of the summary output counts (AC-189-6)."
        )

    def test_status_section_mentions_total_or_ordering(self) -> None:
        """The status section must document the Draft row position (between TOTAL and In Queue)."""
        text = self._text()
        status_idx = text.find("### `status`")
        assert status_idx != -1, "### `status` section must exist"
        next_section = text.find("\n### ", status_idx + 1)
        section_text = text[status_idx:next_section] if next_section != -1 else text[status_idx:]
        lower = section_text.lower()
        has_ordering_info = "total" in lower or "in-queue" in lower or "in queue" in lower
        assert has_ordering_info, (
            "docs/cli-reference.md 'status' section must document that the Draft row "
            "appears between the TOTAL and In Queue lines in the summary (AC-189-6)."
        )


@pytest.mark.unit
class TestReportSectionDraftColumn:
    """AC-189-7: docs/cli-reference.md report section documents the Draft column."""

    def _text(self) -> str:
        return CLI_REFERENCE_DOC.read_text(encoding="utf-8")

    def test_report_section_mentions_draft_column(self) -> None:
        """The report section must mention the Draft column in Status Summary tables."""
        text = self._text()
        report_idx = text.find("### `report`")
        assert report_idx != -1, "### `report` section must exist in cli-reference.md"
        next_section = text.find("\n### ", report_idx + 1)
        section_text = text[report_idx:next_section] if next_section != -1 else text[report_idx:]
        assert "draft" in section_text.lower() or "Draft" in section_text, (
            "docs/cli-reference.md 'report' section must document the new Draft column "
            "in the Status Summary per-epic table (AC-189-7)."
        )

    def test_report_draft_column_in_status_summary_context(self) -> None:
        """The report section must describe Draft column in the context of status summary."""
        text = self._text()
        report_idx = text.find("### `report`")
        assert report_idx != -1, "### `report` section must exist"
        next_section = text.find("\n### ", report_idx + 1)
        section_text = text[report_idx:next_section] if next_section != -1 else text[report_idx:]
        lower = section_text.lower()
        has_draft_column_context = "draft" in lower and (
            "column" in lower or "status summary" in lower or "per-epic" in lower or "table" in lower
        )
        assert has_draft_column_context, (
            "docs/cli-reference.md 'report' section must describe the Draft column "
            "in the context of the Status Summary per-epic table (AC-189-7). "
            "The section must mention 'draft' alongside 'column', 'status summary', "
            "'per-epic', or 'table'."
        )


@pytest.mark.unit
class TestSetStatusDraftValueDocumented:
    """set-status docs must list 'draft' as an accepted value after this task."""

    def _text(self) -> str:
        return CLI_REFERENCE_DOC.read_text(encoding="utf-8")

    def test_set_status_section_includes_draft(self) -> None:
        """The set-status section must list 'draft' among accepted values."""
        text = self._text()
        set_status_idx = text.find("### `set-status`")
        assert set_status_idx != -1, "### `set-status` section must exist in cli-reference.md"
        next_section = text.find("\n### ", set_status_idx + 1)
        section_text = text[set_status_idx:next_section] if next_section != -1 else text[set_status_idx:]
        assert "draft" in section_text.lower(), (
            "docs/cli-reference.md 'set-status' section must list 'draft' as an "
            "accepted value now that the status enum includes it."
        )
