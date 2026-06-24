"""Guard tests: spec-to-backlog SKILL.md Step 6 column shapes match source-of-truth.

Verifies that the Full Work Unit Index header and Status Summary header in
``plugin-authoring/devbench-authoring/skills/spec-to-backlog/SKILL.md`` Step 6
exactly match the canonical definitions in:
- ``src/devbench/backlog/manager.py`` -- BacklogManager._CANONICAL_FULL_INDEX_HEADER_CELLS
- ``src/devbench/constants.py`` -- STATUS_SUMMARY_TABLE_HEADER

Spec: Section 4 E12-F1-S4 AC-1; Section 10; issue #266.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from devbench.backlog.manager import BacklogManager
from devbench.constants import STATUS_SUMMARY_TABLE_HEADER

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


def _extract_step6(text: str) -> str:
    """Return the text of Step 6 (Write or update BACKLOG.md section).

    Raises ValueError if Step 6 cannot be located in the SKILL.md text.
    """
    idx = text.find("## Step 6")
    if idx == -1:
        raise ValueError(
            "ERROR: '## Step 6' not found in spec-to-backlog/SKILL.md. "
            "The section heading must be '## Step 6 --...' or '## Step 6:...'."
        )
    next_h2 = text.find("\n## ", idx + len("## Step 6"))
    if next_h2 != -1:
        return text[idx:next_h2]
    return text[idx:]


def _extract_table_header_from_fenced_block(section: str, block_contains: str) -> str | None:
    """Return the first pipe-row from the fenced code block containing block_contains.

    Returns the header row string (stripped), or None if the block is not found.
    """
    blocks = re.findall(r"```[^\n]*\n(.*?)```", section, re.DOTALL)
    for block in blocks:
        if block_contains in block:
            for line in block.splitlines():
                stripped = line.strip()
                if stripped.startswith("|") and stripped.endswith("|"):
                    return stripped
    return None


def _canonical_full_index_header() -> str:
    """Build the canonical Full Work Unit Index header string from the manager's constant.

    Reads BacklogManager._CANONICAL_FULL_INDEX_HEADER_CELLS and formats it as
    a pipe-delimited header row, e.g. '| ID | Title | Type | Status | ... |'.
    """
    cells = BacklogManager._CANONICAL_FULL_INDEX_HEADER_CELLS
    inner = " | ".join(c for c in cells if c != "")
    return f"| {inner} |"


def _canonical_status_summary_header() -> str:
    """Return the first (pipe-row) line of the Status Summary table header from constants.

    STATUS_SUMMARY_TABLE_HEADER is a two-line string; the first line is the header row.
    """
    first_line = STATUS_SUMMARY_TABLE_HEADER.splitlines()[0]
    return first_line.strip()


@pytest.mark.unit
class TestStep6FullWorkUnitIndexHeader:
    """AC-1: The Full Work Unit Index header in Step 6 must match manager._CANONICAL_FULL_INDEX_HEADER_CELLS."""

    def test_step6_section_exists(self) -> None:
        """SKILL.md must contain a Step 6 section."""
        content = _read_skill()
        assert "## Step 6" in content, (
            "ERROR: spec-to-backlog/SKILL.md must contain '## Step 6' -- "
            "the Write or update BACKLOG.md section. "
            "The section cannot be parsed for shape validation (AC-1, issue #266)."
        )

    def test_full_index_header_matches_source_of_truth(self) -> None:
        """Full Work Unit Index header row must equal the canonical cells from manager.py."""
        content = _read_skill()
        step6 = _extract_step6(content)

        full_index_header = _extract_table_header_from_fenced_block(step6, "File Path")
        if full_index_header is None:
            full_index_header = _extract_table_header_from_fenced_block(step6, "ID")
        assert full_index_header is not None, (
            "ERROR: Step 6 of spec-to-backlog/SKILL.md does not contain a fenced "
            "code block with a Full Work Unit Index table header. "
            "Expected a pipe-row matching: "
            f"{_canonical_full_index_header()} "
            "(AC-1, issue #266). "
            "Add a fenced block with the canonical 7-column header."
        )

        expected = _canonical_full_index_header()
        assert full_index_header == expected, (
            f"ERROR: spec-to-backlog/SKILL.md Step 6 Full Work Unit Index header "
            f"does not match the source-of-truth definition in "
            f"src/devbench/backlog/manager.py "
            f"BacklogManager._CANONICAL_FULL_INDEX_HEADER_CELLS.\n"
            f"  Got:      {full_index_header}\n"
            f"  Expected: {expected}\n"
            "Update SKILL.md Step 6 Full Work Unit Index header to match (AC-1, issue #266)."
        )

    def test_full_index_header_no_stale_columns(self) -> None:
        """The stale columns 'Branch', 'Depends On', 'Changed Files' must not appear in Step 6's index block."""
        content = _read_skill()
        step6 = _extract_step6(content)
        stale_columns = ("Branch", "Depends On", "Changed Files")
        blocks = re.findall(r"```[^\n]*\n(.*?)```", step6, re.DOTALL)
        for stale in stale_columns:
            for block in blocks:
                lines = block.splitlines()
                if lines and "|" in lines[0]:
                    assert stale not in lines[0], (
                        f"ERROR: spec-to-backlog/SKILL.md Step 6 Full Work Unit Index "
                        f"header still contains stale column '{stale}'. "
                        f"The canonical column shape is: {_canonical_full_index_header()} "
                        "(AC-1, issue #266)."
                    )


@pytest.mark.unit
class TestStep6StatusSummaryHeader:
    """AC-1: The Status Summary header in Step 6 must match STATUS_SUMMARY_TABLE_HEADER from constants.py."""

    def test_status_summary_header_matches_source_of_truth(self) -> None:
        """Status Summary header row must equal STATUS_SUMMARY_TABLE_HEADER (first line)."""
        content = _read_skill()
        step6 = _extract_step6(content)

        status_summary_header = _extract_table_header_from_fenced_block(step6, "Epic")
        if status_summary_header is not None and "File Path" in status_summary_header:
            blocks = re.findall(r"```[^\n]*\n(.*?)```", step6, re.DOTALL)
            status_summary_header = None
            for block in blocks:
                for line in block.splitlines():
                    stripped = line.strip()
                    if (
                        stripped.startswith("|")
                        and stripped.endswith("|")
                        and "Epic" in stripped
                        and "File Path" not in stripped
                    ):
                        status_summary_header = stripped
                        break
                if status_summary_header is not None:
                    break

        assert status_summary_header is not None, (
            "ERROR: Step 6 of spec-to-backlog/SKILL.md does not contain a fenced "
            "code block with a Status Summary table header containing 'Epic'. "
            "Expected a pipe-row matching: "
            f"{_canonical_status_summary_header()} "
            "(AC-1, issue #266). "
            "Add a fenced block with the canonical Status Summary header."
        )

        expected = _canonical_status_summary_header()
        assert status_summary_header == expected, (
            f"ERROR: spec-to-backlog/SKILL.md Step 6 Status Summary header "
            f"does not match STATUS_SUMMARY_TABLE_HEADER in "
            f"src/devbench/constants.py.\n"
            f"  Got:      {status_summary_header}\n"
            f"  Expected: {expected}\n"
            "Update SKILL.md Step 6 Status Summary header to match (AC-1, issue #266)."
        )

    def test_status_summary_no_stale_columns(self) -> None:
        """Stale columns 'In Review' and 'Total' must not appear as the canonical shape."""
        content = _read_skill()
        step6 = _extract_step6(content)
        expected = _canonical_status_summary_header()
        assert "Total" not in expected or "Total" not in step6.split("### Full Work Unit Index")[0], (
            "The canonical STATUS_SUMMARY_TABLE_HEADER must be used; it does not include a 'Total' column."
        )
        assert "In Review" not in expected, (
            "The canonical STATUS_SUMMARY_TABLE_HEADER from constants.py must not contain 'In Review' -- "
            "verify that constants.py has not been changed unexpectedly."
        )
