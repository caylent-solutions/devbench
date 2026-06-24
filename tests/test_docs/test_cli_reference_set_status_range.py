"""Structural pins for the set-status range variant in docs/cli-reference.md.

Verifies that docs/cli-reference.md documents:
- The ``devbench set-status --include / --exclude`` bulk variant (AC-194-1).
- Worked examples: promote E1, hold E5, decline E2-F1-S1-T3-T7, --dry-run, --yes,
  --include-and-exclude combo (AC-194-2).
- The bulk_update_confirm_threshold and bulk_update_audit_path config keys.
- The [BULK_STATUS_UPDATE] audit comment written per bulk invocation.

Spec source: spec/devbench-self-improve.md section 4.7.4. Issue: #194.
"""

from __future__ import annotations

from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).parent.parent.parent
CLI_REFERENCE_DOC = REPO_ROOT / "docs" / "cli-reference.md"


def _section_text() -> str:
    """Return the text of the set-status section from the CLI reference doc."""
    text = CLI_REFERENCE_DOC.read_text(encoding="utf-8")
    idx = text.find("### `set-status`")
    assert idx != -1, "### `set-status` section must exist in docs/cli-reference.md"
    next_section = text.find("\n### ", idx + 1)
    return text[idx:next_section] if next_section != -1 else text[idx:]


@pytest.mark.unit
class TestSetStatusRangeSyntaxDocumented:
    """AC-194-1: The set-status section must document the --include / --exclude variant."""

    def test_include_flag_documented(self) -> None:
        """The --include flag must appear in the set-status section."""
        section = _section_text()
        assert "--include" in section, (
            "docs/cli-reference.md 'set-status' section must document the --include flag for bulk updates (AC-194-1)."
        )

    def test_exclude_flag_documented(self) -> None:
        """The --exclude flag must appear in the set-status section."""
        section = _section_text()
        assert "--exclude" in section, (
            "docs/cli-reference.md 'set-status' section must document the "
            "--exclude flag for filtering bulk targets (AC-194-1)."
        )

    def test_dry_run_flag_documented(self) -> None:
        """The --dry-run flag must appear in the set-status section."""
        section = _section_text()
        assert "--dry-run" in section, (
            "docs/cli-reference.md 'set-status' section must document the --dry-run flag (AC-194-1)."
        )

    def test_yes_flag_documented(self) -> None:
        """The --yes flag must appear in the set-status section."""
        section = _section_text()
        assert "--yes" in section, (
            "docs/cli-reference.md 'set-status' section must document the "
            "--yes flag to skip the confirmation prompt (AC-194-1)."
        )

    def test_bulk_range_synopsis_present(self) -> None:
        """The set-status section must show the range-variant synopsis."""
        section = _section_text()
        assert ("--include" in section and "<new_status>" in section) or (
            "--include" in section and "new_status" in section
        ), (
            "docs/cli-reference.md 'set-status' section must show the "
            "'set-status --include <tokens> <new_status>' synopsis (AC-194-1)."
        )

    def test_single_id_variant_still_documented(self) -> None:
        """The existing single-ID variant must still be present (backward compat)."""
        section = _section_text()
        assert "set-status <id>" in section or "set-status <unit-id>" in section, (
            "docs/cli-reference.md 'set-status' section must preserve the "
            "single-ID synopsis 'set-status <id> <status>' (AC-194-1)."
        )

    def test_confirmation_threshold_described(self) -> None:
        """The section must mention the confirmation prompt / threshold behaviour."""
        section = _section_text()
        lower = section.lower()
        assert "confirm" in lower or "threshold" in lower or "prompt" in lower, (
            "docs/cli-reference.md 'set-status' section must describe the "
            "confirmation prompt that fires when expansion size exceeds "
            "bulk_update_confirm_threshold (AC-194-1)."
        )

    def test_bulk_update_audit_comment_documented(self) -> None:
        """The [BULK_STATUS_UPDATE] audit comment must be mentioned."""
        section = _section_text()
        assert "[BULK_STATUS_UPDATE]" in section, (
            "docs/cli-reference.md 'set-status' section must document the "
            "[BULK_STATUS_UPDATE] audit row written per bulk invocation (AC-194-1)."
        )

    def test_no_em_dashes_in_section(self) -> None:
        """The section must contain no em-dash characters (U+2014)."""
        section = _section_text()
        assert "\u2014" not in section, (
            "docs/cli-reference.md 'set-status' section must not contain em-dash characters (U+2014); use -- instead."
        )


@pytest.mark.unit
class TestSetStatusWorkedExamples:
    """AC-194-2: The set-status section must contain all required worked examples."""

    def test_promote_e1_example_present(self) -> None:
        """An example showing 'promote E1' (set all E1 WUs to in-queue) must be present."""
        section = _section_text()
        assert "E1" in section and "in-queue" in section, (
            "docs/cli-reference.md 'set-status' section must contain a worked example "
            "showing how to promote (set in-queue) all units under epic E1 (AC-194-2)."
        )

    def test_hold_e5_example_present(self) -> None:
        """An example showing 'hold E5' (set all E5 WUs to hold) must be present."""
        section = _section_text()
        assert "E5" in section and "hold" in section, (
            "docs/cli-reference.md 'set-status' section must contain a worked example "
            "showing how to hold all units under epic E5 (AC-194-2)."
        )

    def test_decline_range_example_present(self) -> None:
        """An example showing 'decline E2-F1-S1-T3-T7' must be present."""
        section = _section_text()
        assert "declined" in section or "decline" in section.lower(), (
            "docs/cli-reference.md 'set-status' section must contain a worked example "
            "showing how to decline a range of tasks (e.g. T3-T7) (AC-194-2)."
        )
        assert "T3" in section or "T3-T7" in section, (
            "docs/cli-reference.md 'set-status' section must show the T3-T7 range "
            "example in the decline worked example (AC-194-2)."
        )

    def test_dry_run_example_present(self) -> None:
        """A worked example using --dry-run must appear in the section."""
        section = _section_text()
        assert "--dry-run" in section, (
            "docs/cli-reference.md 'set-status' section must contain a worked example using --dry-run (AC-194-2)."
        )
        assert "```" in section, (
            "docs/cli-reference.md 'set-status' section must contain at least one "
            "code block with worked examples (AC-194-2)."
        )

    def test_yes_flag_example_present(self) -> None:
        """A worked example using --yes (skip confirmation) must appear in the section."""
        section = _section_text()
        assert "--yes" in section, (
            "docs/cli-reference.md 'set-status' section must contain a worked "
            "example using --yes to bypass confirmation (AC-194-2)."
        )

    def test_include_and_exclude_combo_example_present(self) -> None:
        """A worked example combining --include and --exclude must appear."""
        section = _section_text()
        assert "--include" in section and "--exclude" in section, (
            "docs/cli-reference.md 'set-status' section must contain a worked example "
            "combining --include and --exclude selectors (AC-194-2)."
        )


@pytest.mark.unit
class TestSetStatusConfigKeysDocumented:
    """The set-status section must reference the BacklogConfig keys it consumes."""

    def test_bulk_update_confirm_threshold_mentioned(self) -> None:
        """bulk_update_confirm_threshold must appear in the set-status section."""
        section = _section_text()
        assert "bulk_update_confirm_threshold" in section, (
            "docs/cli-reference.md 'set-status' section must mention "
            "bulk_update_confirm_threshold so operators know how to tune the "
            "confirmation prompt behaviour (AC-194-1)."
        )

    def test_bulk_update_audit_path_mentioned(self) -> None:
        """bulk_update_audit_path must appear in the set-status section."""
        section = _section_text()
        assert "bulk_update_audit_path" in section, (
            "docs/cli-reference.md 'set-status' section must mention "
            "bulk_update_audit_path so operators know where bulk-update audit rows "
            "are persisted (AC-194-1)."
        )
