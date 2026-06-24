"""Marker-based FR / AC-N extraction and backlog-readiness self-check.

Issue #266 E12-F1-S3: every ``create-spec`` output must be directly
decomposable by ``spec-to-backlog`` with zero manual reshaping.

This module provides the deterministic contract layer:

- ``AC_MARKER`` -- the single stable string that ``create-spec`` embeds in
  every new spec to delimit the start of the AC-N section.  Both this
  self-check and ``spec-to-backlog`` use this constant so the marker lives in
  exactly one place.
- ``extract_fr_list`` -- returns every ``FR-N`` line found in a spec string.
- ``extract_ac_section`` -- resolves the AC-N block via ``AC_MARKER`` first;
  falls back to the positional ``Section 6`` heading for legacy specs that
  pre-date the marker convention.
- ``check_backlog_readiness`` -- verifies the four readiness invariants (FR
  list, locatable AC-N, repo + branch, unit inventory for multi-unit specs)
  and raises ``ReadinessError`` naming the first missing element.

The ``create-spec`` SKILL.md runs ``check_backlog_readiness`` as the final
self-check step; ``spec-to-backlog`` SKILL.md uses ``extract_ac_section`` to
locate the AC-N block.

Spec Section 4 E12-F1-S3 AC-1, AC-2, AC-3.
"""

from __future__ import annotations

import re

AC_MARKER: str = "<!-- AC-SECTION-START -->"


_REPO_PATTERN: re.Pattern[str] = re.compile(r"^\s*-\s+\*\*Repo:\*\*", re.MULTILINE)
_BRANCH_PATTERN: re.Pattern[str] = re.compile(r"^\s*-\s+\*\*Branch:\*\*", re.MULTILINE)
_FR_PATTERN: re.Pattern[str] = re.compile(r"^.*\bFR-\d+\b.*$", re.MULTILINE)
_SECTION_6_PATTERN: re.Pattern[str] = re.compile(r"^##\s+Section\s+6\b", re.MULTILINE | re.IGNORECASE)
_NEXT_SECTION_PATTERN: re.Pattern[str] = re.compile(r"^##\s+", re.MULTILINE)
_INVENTORY_PATTERN: re.Pattern[str] = re.compile(r"^##\s+Unit\s+Inventory\b", re.MULTILINE | re.IGNORECASE)


class ReadinessError(ValueError):
    """Raised by ``check_backlog_readiness`` when a required spec element is absent.

    The message always names the missing element so the operator knows exactly
    which part of the spec to add before re-running the skill.

    Inherits from ``ValueError`` so callers that catch broad ValueError also
    catch this (Liskov-compatible).
    """


def extract_fr_list(spec_text: str) -> list[str]:
    """Return every line in *spec_text* that contains a ``FR-N`` identifier.

    Lines are returned stripped of leading / trailing whitespace but otherwise
    verbatim.  The list is empty when the spec contains no ``FR-N`` patterns.

    Args:
        spec_text: The full text of a spec Markdown file.

    Returns:
        A list of stripped lines, one per distinct ``FR-N`` line in document
        order.  Duplicate lines are preserved if they appear multiple times.
    """
    return [m.group(0).strip() for m in _FR_PATTERN.finditer(spec_text)]


def extract_ac_section(spec_text: str) -> str:
    """Return the AC-N text block from *spec_text*.

    Resolution order:

    1. **Marker path**: if ``AC_MARKER`` appears in *spec_text*, return the
       text between ``AC_MARKER`` and the next ``##``-level heading (or the
       end of the document when no subsequent heading exists).
    2. **Legacy fallback**: if no marker is found, locate the positional
       ``## Section 6`` heading (case-insensitive) and return text from that
       heading to the next ``##``-level heading or end of document.
    3. **Fail fast**: when neither anchor is found, raise ``ReadinessError``
       naming the missing AC section so the caller can report the gap.

    Args:
        spec_text: The full text of a spec Markdown file.

    Returns:
        The text block containing the AC-N entries.

    Raises:
        ReadinessError: When neither the marker nor a positional ``Section 6``
            heading can be found.
    """
    marker_pos = spec_text.find(AC_MARKER)
    if marker_pos != -1:
        after_marker = spec_text[marker_pos + len(AC_MARKER) :]
        return _extract_until_next_section(after_marker)

    section_6_match = _SECTION_6_PATTERN.search(spec_text)
    if section_6_match:
        after_heading = spec_text[section_6_match.start() :]
        return _extract_until_next_section(after_heading)

    raise ReadinessError(
        "ERROR: AC section not found in spec.\n"
        "Expected either the machine-locatable marker "
        f"'{AC_MARKER}' or a '## Section 6' heading.\n"
        "Add one of these anchors before the AC-N list and re-run the skill."
    )


def _extract_until_next_section(text: str) -> str:
    """Return *text* up to (but not including) the next ``##``-level heading.

    When no subsequent ``##`` heading exists, the entire remaining *text* is
    returned.  The function never mutates its input.
    """
    lines = text.splitlines(keepends=True)
    collected: list[str] = []
    start = 0
    if lines and _NEXT_SECTION_PATTERN.match(lines[0]):
        start = 1
        collected.append(lines[0])
    for line in lines[start:]:
        if _NEXT_SECTION_PATTERN.match(line):
            break
        collected.append(line)
    return "".join(collected)


def check_backlog_readiness(spec_text: str, *, is_multi_unit: bool) -> None:
    """Verify the four backlog-readiness invariants of *spec_text*.

    Checks performed (in order):

    1. **FR list**: at least one ``FR-N`` line must be present.
    2. **AC-N section**: resolvable via ``AC_MARKER`` or positional Section 6.
    3. **Repo + branch**: a ``## Target Repository`` block with ``Repo:`` and
       ``Branch:`` fields must be present.
    4. **Unit inventory** (multi-unit specs only): a ``## Unit Inventory``
       heading must be present when *is_multi_unit* is ``True``.  Single-unit
       specs are NOT required to carry an inventory.

    The function fails fast on the first missing element, raising
    ``ReadinessError`` with a message that names the element and suggests a
    fix.  Callers that need all failures at once should call the individual
    helpers directly.

    Args:
        spec_text: The full text of a spec Markdown file.
        is_multi_unit: ``True`` when the spec covers more than one work unit;
            ``False`` for a single-unit spec.

    Raises:
        ReadinessError: When any required element is absent.  The message
            always names the missing element.
    """
    frs = extract_fr_list(spec_text)
    if not frs:
        raise ReadinessError(
            "ERROR: spec is missing the FR list.\n"
            "Add at least one 'FR-1: ...' line to the spec before the "
            "AC section and re-run the readiness check."
        )

    extract_ac_section(spec_text)

    if not _REPO_PATTERN.search(spec_text) or not _BRANCH_PATTERN.search(spec_text):
        raise ReadinessError(
            "ERROR: spec is missing the Target Repository block "
            "(Repo: and Branch: fields).\n"
            "Add a '## Target Repository' section with '- **Repo:** ...' "
            "and '- **Branch:** ...' lines and re-run the readiness check."
        )

    if is_multi_unit and not _INVENTORY_PATTERN.search(spec_text):
        raise ReadinessError(
            "ERROR: multi-unit spec is missing the Unit Inventory section.\n"
            "Add a '## Unit Inventory' section that lists each work item "
            "and re-run the readiness check."
        )
