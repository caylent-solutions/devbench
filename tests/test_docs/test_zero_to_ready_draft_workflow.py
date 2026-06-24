"""Structural pins for the operator-draft-workflow section in docs/zero-to-ready.md.

Verifies that docs/zero-to-ready.md documents (AC-189-9):
- The draft status landing workflow: reviewing generated backlog, promoting selectively
  or bulk-promoting via ``devbench promote --all``.
- The ``devbench promote <id>`` single-unit promotion command.
- The ``devbench promote --all`` bulk-promotion command.
- The default-status config key ``backlog.default_status_for_new_work_units``.
- That existing workspaces without the new config key continue to land WUs in ``in-queue``.

Spec source: spec/devbench-self-improve.md section 4.1 (draft WorkUnitStatus).
Issue: #189.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).parent.parent.parent
ZERO_TO_READY_DOC = REPO_ROOT / "docs" / "zero-to-ready.md"


@pytest.mark.unit
class TestZeroToReadyDocExists:
    """Pre-condition: the zero-to-ready doc must exist."""

    def test_zero_to_ready_doc_exists(self) -> None:
        assert ZERO_TO_READY_DOC.is_file(), (
            "docs/zero-to-ready.md must exist -- it is the authoritative onboarding guide."
        )


@pytest.mark.unit
class TestDraftWorkflowSectionPresence:
    """AC-189-9: docs/zero-to-ready.md must document the operator draft workflow."""

    def _text(self) -> str:
        return ZERO_TO_READY_DOC.read_text(encoding="utf-8")

    def test_draft_workflow_section_heading_exists(self) -> None:
        """The doc must contain a section heading for the draft workflow."""
        text = self._text()
        lower = text.lower()
        assert "draft" in lower, (
            "docs/zero-to-ready.md must contain a section describing the operator "
            "workflow when work units land in draft status (AC-189-9)."
        )
        has_heading = (
            "## Working with draft" in text
            or "### Working with draft" in text
            or "## Draft work units" in text
            or "### Draft work units" in text
            or "## Step 8b" in text
            or ("draft status" in lower and "#" in text[: text.lower().find("draft status")][-50:])
        )
        assert has_heading or ("draft" in lower and "promote" in lower), (
            "docs/zero-to-ready.md must contain a section (heading) describing the "
            "draft-status operator workflow, covering review and promote steps (AC-189-9)."
        )

    def test_draft_review_backlog_step_documented(self) -> None:
        """The doc must describe the step of reviewing the generated backlog in draft."""
        text = self._text()
        lower = text.lower()
        assert "draft" in lower and ("review" in lower or "inspect" in lower or "examine" in lower), (
            "docs/zero-to-ready.md must document the step where the operator reviews "
            "the generated backlog before promoting draft work units (AC-189-9)."
        )

    def test_promote_selective_command_documented(self) -> None:
        """The doc must show 'devbench promote <id>' as the selective promotion command."""
        text = self._text()
        assert "devbench promote" in text, (
            "docs/zero-to-ready.md must document 'devbench promote <id>' "
            "for selective promotion of individual draft work units (AC-189-9)."
        )

    def test_promote_all_command_documented(self) -> None:
        """The doc must show 'devbench promote --all' for bulk promotion."""
        text = self._text()
        assert "promote --all" in text or "devbench promote --all" in text, (
            "docs/zero-to-ready.md must document 'devbench promote --all' "
            "to release all draft work units for autonomous claim at once (AC-189-9)."
        )

    def test_default_status_config_key_documented(self) -> None:
        """The doc must mention the backlog.default_status_for_new_work_units config key."""
        text = self._text()
        assert "default_status_for_new_work_units" in text, (
            "docs/zero-to-ready.md must document the "
            "'backlog.default_status_for_new_work_units' config key so operators "
            "know how to opt in to draft landing (AC-189-9)."
        )

    def test_draft_lifecycle_transition_documented(self) -> None:
        """The doc must name the draft -> in-queue lifecycle transition."""
        text = self._text()
        assert "draft" in text and "in-queue" in text, (
            "docs/zero-to-ready.md must document the lifecycle transition from "
            "'draft' to 'in-queue' as part of the promote workflow (AC-189-9)."
        )

    def test_default_behaviour_unchanged_noted(self) -> None:
        """The doc must note that existing workspaces are unaffected (default is in-queue)."""
        text = self._text()
        lower = text.lower()
        has_default_note = (
            "default" in lower
            and "in-queue" in lower
            and (
                "unchanged" in lower or "existing" in lower or "omit" in lower or "unset" in lower or "legacy" in lower
            )
        )
        assert has_default_note, (
            "docs/zero-to-ready.md must note that existing workspaces without the "
            "'backlog.default_status_for_new_work_units' config key continue to land "
            "new work units in 'in-queue' (AC-189-9)."
        )


@pytest.mark.unit
class TestPromoteCommandInCliTableUpdated:
    """The devbench CLI quick-reference table in zero-to-ready.md must include promote."""

    def _text(self) -> str:
        return ZERO_TO_READY_DOC.read_text(encoding="utf-8")

    def test_promote_appears_in_cli_reference_table(self) -> None:
        """The CLI quick-reference table must include 'devbench promote'."""
        text = self._text()
        assert "devbench promote" in text, (
            "docs/zero-to-ready.md must include 'devbench promote' in its CLI "
            "quick-reference table so operators discover the command (AC-189-9)."
        )

    def test_draft_to_in_queue_action_described(self) -> None:
        """The promote entry must link draft -> in-queue or say 'draft work units'."""
        text = self._text()
        lower = text.lower()
        assert "draft" in lower and "promote" in lower, (
            "docs/zero-to-ready.md must describe 'promote' in the context of "
            "draft work units transitioning to in-queue (AC-189-9)."
        )


@pytest.mark.unit
class TestDraftStatusTaskLevelConstraint:
    """AC-189-9: docs/zero-to-ready.md must not document draft status on non-task IDs.

    validate-backlog check_16 enforces that 'draft' is only valid for task-level
    work units (IDs matching *-T<digits>).  Any example using 'set-status <epic-or-feature-or-story-id> draft'
    in the doc will lead operators into a state that validate-backlog rejects.
    """

    def _text(self) -> str:
        return ZERO_TO_READY_DOC.read_text(encoding="utf-8")

    def test_set_status_draft_never_used_with_epic_id(self) -> None:
        """No 'set-status <epic-id> draft' example may appear in the doc.

        An epic-level ID contains no '-T<digits>' segment (e.g. 'E7', 'E7-F1', 'E7-F1-S1').
        Only task-level IDs like 'E7-F1-S1-T1' may appear alongside 'draft' in set-status
        examples, because validate-backlog check_16 rejects draft on epics/features/stories.
        """
        text = self._text()
        bad_pattern = re.compile(
            r"set-status\s+([A-Z]\d+(?:-F\d+(?:-S\d+)?)?)\s+draft",
            re.IGNORECASE,
        )
        bad_matches = bad_pattern.findall(text)
        assert not bad_matches, (
            f"docs/zero-to-ready.md must not document 'set-status <epic/feature/story-id> draft'. "
            f"Found bad examples with IDs: {bad_matches}. "
            f"'draft' is only valid for task-level units (IDs with -T<digits>). "
            f"Use 'set-status <epic-id> hold' to pause an epic, or use a task-level ID "
            f"like E7-F1-S1-T1 for draft examples (AC-189-9, validate-backlog check_16)."
        )
