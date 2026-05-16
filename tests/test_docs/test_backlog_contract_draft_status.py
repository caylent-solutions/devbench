"""Structural pins for the draft-status additions in docs/backlog-contract.md.

Verifies that the Status Values table and lifecycle diagram in
docs/backlog-contract.md include the ``draft`` status entry and the
``draft -> in-queue`` lifecycle transition as required by spec section 4.1
and AC-189-9.
"""

from __future__ import annotations

from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).parent.parent.parent
BACKLOG_CONTRACT_DOC = REPO_ROOT / "docs" / "backlog-contract.md"


@pytest.mark.unit
class TestBacklogContractDraftStatusEnum:
    """AC-189-9: docs/backlog-contract.md Status Values table includes draft."""

    def test_backlog_contract_doc_exists(self) -> None:
        assert BACKLOG_CONTRACT_DOC.is_file(), (
            "docs/backlog-contract.md must exist -- it is the authoritative "
            "contract reference for validate-backlog rules."
        )

    def test_status_values_table_includes_draft_row(self) -> None:
        """The Status Values table must contain a row for the draft status."""
        text = BACKLOG_CONTRACT_DOC.read_text(encoding="utf-8")
        assert "| Draft |" in text or "| draft |" in text, (
            "docs/backlog-contract.md Status Values table must include a Draft row. "
            "Add '| Draft | Pre-queue; not yet approved for autonomous claim | `draft` | no |' "
            "to the table."
        )

    def test_status_values_table_draft_written_as_draft(self) -> None:
        """The Written-as column for draft must show the string 'draft'."""
        text = BACKLOG_CONTRACT_DOC.read_text(encoding="utf-8")
        assert "`draft`" in text, (
            "docs/backlog-contract.md must document the canonical written form '`draft`' "
            "in the Status Values table so implementors know the exact string to write."
        )

    def test_status_values_table_draft_is_not_terminal(self) -> None:
        """The draft row must mark Terminal? as 'no'."""
        text = BACKLOG_CONTRACT_DOC.read_text(encoding="utf-8")
        # The row must contain 'draft' and must NOT mark it as terminal.
        # We look for the draft row and verify it ends with 'no'.
        lines = text.splitlines()
        draft_row_found = False
        for line in lines:
            normalised = line.lower()
            if "draft" in normalised and normalised.startswith("|") and "pre-" in normalised:
                draft_row_found = True
                # The terminal column must be 'no'.
                assert "| no |" in line or line.rstrip().endswith("| no |") or line.rstrip().endswith("no |"), (
                    f"Draft Status row must mark 'Terminal?' as 'no', got: {line!r}"
                )
                break
        assert draft_row_found, (
            "No 'draft' row with a 'pre-' description found in docs/backlog-contract.md Status Values table. "
            "The row must describe draft as a pre-queue status."
        )


@pytest.mark.unit
class TestBacklogContractLifecycleDiagram:
    """AC-189-9: docs/backlog-contract.md contains the lifecycle diagram with draft."""

    def test_lifecycle_includes_draft_to_in_queue_transition(self) -> None:
        """The doc must show the draft -> in-queue transition in the lifecycle."""
        text = BACKLOG_CONTRACT_DOC.read_text(encoding="utf-8")
        assert "draft" in text and "in-queue" in text, (
            "docs/backlog-contract.md must document both 'draft' and 'in-queue' statuses."
        )
        # Check that the transition arrow appears somewhere in the doc.
        has_arrow = "->" in text or "-->" in text
        assert has_arrow, (
            "docs/backlog-contract.md must contain a lifecycle diagram or text showing "
            "the draft -> in-queue transition using '->' or '-->'."
        )

    def test_lifecycle_draft_arrow_leads_to_in_queue(self) -> None:
        """The lifecycle must show draft -> in-queue, not some other target."""
        text = BACKLOG_CONTRACT_DOC.read_text(encoding="utf-8")
        # The sequence 'draft -> in-queue' (or 'draft --> in-queue') must appear.
        assert "draft -> in-queue" in text or "draft --> in-queue" in text, (
            "docs/backlog-contract.md must contain 'draft -> in-queue' to document "
            "the canonical lifecycle transition from draft to in-queue."
        )

    def test_lifecycle_full_happy_path_documented(self) -> None:
        """The lifecycle section must document the full happy-path flow."""
        text = BACKLOG_CONTRACT_DOC.read_text(encoding="utf-8")
        # All five main states must appear in the happy-path sequence.
        for status in ("draft", "in-queue", "in-progress", "in-review", "done"):
            assert status in text, (
                f"docs/backlog-contract.md must mention '{status}' in the lifecycle "
                "to show the full happy-path: draft -> in-queue -> in-progress -> in-review -> done."
            )


@pytest.mark.unit
class TestBacklogContractDraftCallout:
    """AC-189-9: docs/backlog-contract.md contains the agile-standard callout for draft."""

    def test_draft_callout_mentions_not_yet_refined_or_approved(self) -> None:
        """The doc must include the required callout text about draft meaning."""
        text = BACKLOG_CONTRACT_DOC.read_text(encoding="utf-8")
        # The callout must explain draft as the agile-standard term.
        assert "not yet refined" in text.lower() or "not yet approved" in text.lower(), (
            "docs/backlog-contract.md must include a callout explaining that 'draft' is the "
            "agile-standard term for items not yet refined / approved for autonomous claim."
        )

    def test_draft_callout_mentions_autonomous_claim(self) -> None:
        """The callout must reference autonomous claim so operators understand the gate."""
        text = BACKLOG_CONTRACT_DOC.read_text(encoding="utf-8")
        assert "autonomous claim" in text.lower() or "autonomous" in text.lower(), (
            "docs/backlog-contract.md must mention 'autonomous claim' in the draft callout "
            "so operators understand that draft items are not picked up automatically."
        )
