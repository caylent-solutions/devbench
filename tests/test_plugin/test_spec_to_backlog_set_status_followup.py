"""Structural pin tests for spec-to-backlog/SKILL.md set-status follow-up recommendation.

Verifies that plugin-authoring/devbench-authoring/skills/spec-to-backlog/SKILL.md (AC-194-1):
- Recommends the canonical post-generation follow-up command:
  ``devbench set-status --include "E1" in-queue``
- References ``docs/zero-to-ready.md`` for full bulk-operations documentation.
- Does not use the em-dash character (U+2014) in the relevant section.
- Presents the recommendation in a "Next steps" or similar section within Step 8.

Spec source: spec/devbench-self-improve.md section 4.7.4. Issue: #194.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

SKILL_PATH = (
    Path(__file__).parent.parent.parent
    / "plugin-authoring"
    / "devbench-authoring"
    / "skills"
    / "spec-to-backlog"
    / "SKILL.md"
)


def _read_skill() -> str:
    return SKILL_PATH.read_text(encoding="utf-8")


def _extract_step8(text: str) -> str:
    """Return the text of Step 8 (success message section)."""
    idx = text.find("## Step 8")
    if idx == -1:
        return ""
    next_h2 = text.find("\n## ", idx + len("## Step 8"))
    if next_h2 != -1:
        return text[idx:next_h2]
    return text[idx:]


@pytest.mark.unit
class TestSetStatusFollowUpPresent:
    """AC-194-1: Step 8 success message must recommend devbench set-status follow-up."""

    def test_step8_exists(self) -> None:
        """SKILL.md must contain Step 8 (success message / quality-reference step)."""
        content = _read_skill()
        assert "## Step 8" in content, (
            "spec-to-backlog/SKILL.md must contain '## Step 8' (the success message / quality-reference step)"
        )

    def test_set_status_command_in_step8(self) -> None:
        """Step 8 must include the devbench set-status command as a follow-up recommendation."""
        section = _extract_step8(_read_skill())
        assert section, "## Step 8 section must be non-empty"
        assert "set-status" in section, (
            "spec-to-backlog/SKILL.md Step 8 success message must recommend "
            "'devbench set-status' as the canonical follow-up command (AC-194-1, "
            "spec section 4.7.4)."
        )

    def test_include_flag_in_step8(self) -> None:
        """Step 8 follow-up recommendation must show the --include flag."""
        section = _extract_step8(_read_skill())
        assert "--include" in section, (
            "spec-to-backlog/SKILL.md Step 8 success message must show the "
            "'--include' flag of devbench set-status in the follow-up example "
            "(AC-194-1, spec section 4.7.4)."
        )

    def test_in_queue_target_status_in_step8(self) -> None:
        """Step 8 follow-up recommendation must show in-queue as the target status."""
        section = _extract_step8(_read_skill())
        assert "in-queue" in section, (
            "spec-to-backlog/SKILL.md Step 8 success message must show 'in-queue' "
            "as the target status in the devbench set-status follow-up example "
            "(AC-194-1, spec section 4.7.4)."
        )

    def test_epic_selector_example_in_step8(self) -> None:
        """Step 8 follow-up example must demonstrate per-epic selection (e.g. --include E1)."""
        section = _extract_step8(_read_skill())
        has_epic_selector = re.search(r'--include\s+["\']?E\d', section) is not None
        assert has_epic_selector, (
            "spec-to-backlog/SKILL.md Step 8 success message must demonstrate "
            'an epic-scoped --include selector (e.g. --include "E1") in the '
            "devbench set-status follow-up example (AC-194-1, spec section 4.7.4)."
        )

    def test_step8_followup_in_code_block(self) -> None:
        """The set-status follow-up command must appear inside a code block in Step 8."""
        section = _extract_step8(_read_skill())
        code_blocks = re.findall(r"```[^\n]*\n(.*?)```", section, re.DOTALL)
        code_text = "\n".join(code_blocks)
        assert "set-status" in code_text, (
            "spec-to-backlog/SKILL.md Step 8 must present the devbench set-status "
            "follow-up inside a fenced code block (not just in prose) so operators "
            "can copy-paste the command (AC-194-1)."
        )


@pytest.mark.unit
class TestZeroToReadyReferenceInStep8:
    """AC-194-1: Step 8 must reference docs/zero-to-ready.md for bulk-operations docs."""

    def test_zero_to_ready_reference_in_step8(self) -> None:
        """Step 8 follow-up section must reference docs/zero-to-ready.md."""
        section = _extract_step8(_read_skill())
        assert "zero-to-ready" in section, (
            "spec-to-backlog/SKILL.md Step 8 must reference 'docs/zero-to-ready.md' "
            "for full bulk-operations documentation (AC-194-1, spec section 4.7.4)."
        )


@pytest.mark.unit
class TestNoEmDashInStep8:
    """AC-194-1 + Critical Rule 8: Step 8 must not contain em-dash characters (U+2014)."""

    def test_no_em_dash_in_step8(self) -> None:
        """Step 8 must use -- instead of the em-dash character."""
        section = _extract_step8(_read_skill())
        assert "\u2014" not in section, (
            "spec-to-backlog/SKILL.md Step 8 must not contain em-dash characters "
            "(U+2014); use -- instead (Critical Rule 8)."
        )


@pytest.mark.unit
class TestSetStatusFollowUpContextual:
    """AC-194-1: The recommendation must appear in the context of the skill's success flow."""

    def test_step8_mentions_draft_status_context(self) -> None:
        """Step 8 must acknowledge that tasks default to draft before recommending set-status.

        The follow-up recommendation is meaningful only because spec-to-backlog generates
        draft tasks; the message should make this flow explicit.
        """
        section = _extract_step8(_read_skill())
        has_draft_context = "draft" in section.lower()
        assert has_draft_context, (
            "spec-to-backlog/SKILL.md Step 8 success message must mention the draft "
            "status context (tasks default to draft) before recommending set-status "
            "to promote them to in-queue (AC-194-1)."
        )

    def test_step8_explains_purpose_of_set_status_followup(self) -> None:
        """Step 8 must explain that set-status releases epics for autonomous work.

        The recommendation must convey the intent: releasing tasks for autonomous
        orchestrator execution -- not just list the command without context.
        """
        section = _extract_step8(_read_skill())
        lower = section.lower()
        has_purpose = any(
            kw in lower
            for kw in (
                "release",
                "autonomous",
                "orchestrat",
                "promote",
                "queue",
                "start",
                "ready",
                "work",
            )
        )
        assert has_purpose, (
            "spec-to-backlog/SKILL.md Step 8 must explain the purpose of the "
            "devbench set-status follow-up (releasing epics for autonomous work) -- "
            "not just list the command in isolation (AC-194-1)."
        )
