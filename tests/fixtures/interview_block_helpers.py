"""Shared markdown interview-block parsing and assertion helpers.

Extracted from `tests/test_plugin/test_configure_devbench_schema_coverage.py`
(E2-F8-S1-T1) so the structural pin for the `configure-devbench` SKILL and
the structural pin for the `bootstrap-environment` SKILL (E2-F8-S1-T2) share
one implementation of the `#### \\`key\\`` interview-block parser and the
Recommended/Alternatives/Free-form completeness check, rather than each
skill's test module re-implementing the same three-marker check
(test_review DRY_VIOLATION on E2-F8-S1-T2).

Every skill's own SKILL.md carries its own `#### \\`dotted.path\\`` (or
`#### \\`VAR_NAME\\``) interview headings; `parse_interview_blocks` is
agnostic to which skill produced the heading, so a single implementation
covers both.
"""

from __future__ import annotations

import re

# Heading marker every interview block starts with, followed immediately
# by the block's key in backticks, e.g. "#### `timeouts.gh_api` -- ..." or
# "#### `DEVBENCH_USE_BEDROCK` -- ...".
BLOCK_HEADING_RE = re.compile(r"^####\s+`([^`]+)`", re.MULTILINE)
# Any heading (## / ### / ####) marks the end of the current block's body.
ANY_HEADING_RE = re.compile(r"^#{2,4}\s", re.MULTILINE)

_REQUIRED_BLOCK_MARKERS: tuple[str, ...] = ("**Recommended:**", "**Alternatives:**", "**Free-form:**")

# Case-insensitive: the shipped SKILL files spell this "Current value shown
# to the operator: ..." consistently, but the check itself only requires the
# phrase, not the exact sentence, matching the looser check the sibling
# module already used for its own "prior values shown as current" pin.
_CURRENT_VALUE_MARKER_RE = re.compile(r"current value", re.IGNORECASE)


def parse_interview_blocks(skill_text: str) -> dict[str, str]:
    """Split `skill_text` into `{key: block_body}` for every
    `#### \\`key\\`` heading. `block_body` runs from immediately after the
    heading line to the next `##`/`###`/`####` heading (or end of file).
    Later headings for the same key (should never happen in a well-formed
    SKILL, but the parser does not assume uniqueness) overwrite earlier
    ones."""
    headings = list(BLOCK_HEADING_RE.finditer(skill_text))
    blocks: dict[str, str] = {}
    for match in headings:
        key = match.group(1)
        body_start = match.end()
        next_heading = ANY_HEADING_RE.search(skill_text, body_start)
        body_end = next_heading.start() if next_heading else len(skill_text)
        blocks[key] = skill_text[body_start:body_end]
    return blocks


def assert_interview_blocks_complete(
    blocks: dict[str, str], required_keys: list[str], *, skill_label: str = "SKILL.md"
) -> None:
    """Every key in `required_keys` must have a block in `blocks` carrying
    every marker in the shared Recommended/Alternatives/Free-form set.
    Raises `AssertionError` naming `skill_label`, the key, and the specific
    missing element (or 'entire block' when the heading itself is absent)
    on the FIRST violation found, in `required_keys` order. `skill_label`
    lets each caller's failure message name its own SKILL file without the
    shared helper hard-coding any one skill's name."""
    for key in required_keys:
        body = blocks.get(key)
        if body is None:
            raise AssertionError(
                f"{skill_label} has no '#### `{key}`' interview block at all "
                f"(key: {key!r}, missing element: entire block)"
            )
        missing_markers = [marker for marker in _REQUIRED_BLOCK_MARKERS if marker not in body]
        if missing_markers:
            raise AssertionError(
                f"{skill_label}'s interview block for '{key}' is missing: "
                f"{', '.join(missing_markers)} (key: {key!r}, missing element(s): {missing_markers!r})"
            )


def assert_interview_blocks_show_current_value(
    blocks: dict[str, str], required_keys: list[str], *, skill_label: str = "SKILL.md"
) -> None:
    """AC-E2-F8-S1-T2-3: every key in `required_keys` must have a block
    body that shows the operator the current value (a case-insensitive
    'current value' phrase), proving prior values are surfaced rather than
    silently reused instead of the interview quietly falling back to a
    remembered answer. Raises `AssertionError` naming `skill_label` and the
    key on the FIRST violation found, in `required_keys` order."""
    for key in required_keys:
        body = blocks.get(key)
        if body is None:
            raise AssertionError(
                f"{skill_label} has no '#### `{key}`' interview block at all "
                f"(key: {key!r}, cannot check current-value line)"
            )
        if not _CURRENT_VALUE_MARKER_RE.search(body):
            raise AssertionError(
                f"{skill_label}'s interview block for '{key}' does not show the current value to the "
                f"operator (key: {key!r}, missing element: current-value line)"
            )
