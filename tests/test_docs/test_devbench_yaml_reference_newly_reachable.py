"""Doc-pin: docs/devbench-yaml-reference.md's ``gates.newly_reachable_paths.paths``
section cross-references a heading that actually exists in
docs/newly-reachable-paths.md, and uses the shipped ``behavior-fix`` task-type
vocabulary rather than the retired ``bug-fix`` term (E8-F1-S1-T1, commit
b35fd53f, replaced the title-starts-with-"Fix" heuristic with the
``## Task Type: behavior-fix`` taxonomy).

Context: E8-F1-S1-T2's own (unlanded, currently blocked) rewrite of
docs/newly-reachable-paths.md renames its "Optional: the cross-cutting-primitives
registry" heading to "Gate config: gates.newly_reachable_paths". That rename is
NOT present on this branch's committed tree as of this task -- E8-F1-S1-T2 is
blocked and its diff lives only in a stash. This module therefore pins the
cross-reference against the heading set that is actually present in the
*committed* docs/newly-reachable-paths.md, rather than hard-coding a heading
name that has not landed: the assertion resolves the heading name quoted in
docs/devbench-yaml-reference.md dynamically and checks it against the real
(fence-excluded) heading set of docs/newly-reachable-paths.md, so this test
starts failing on its own -- with no code change here -- the moment either file
next changes and the two fall out of sync (including once E8-F1-S1-T2 lands).

Source: E8-F1-S1-T4 (AC-TEST-001, AC-CODE-001, AC-CODE-002). Scenario
E8-F1-S1-T2-review-round-1-doc_review-CONFIG_DOCS.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).parent.parent.parent
YAML_REFERENCE_DOC = REPO_ROOT / "docs" / "devbench-yaml-reference.md"
NEWLY_REACHABLE_DOC = REPO_ROOT / "docs" / "newly-reachable-paths.md"

_FENCE_RE = re.compile(r"```(?:[a-zA-Z0-9]*)\n.*?```", re.DOTALL)
_HEADING_RE = re.compile(r"^(#{1,6})\s+(.+?)\s*$", re.MULTILINE)

# Matches the cross-reference sentence's quoted heading name, e.g.:
#   (`docs/newly-reachable-paths.md`'s "Optional: the cross-cutting-primitives registry" section)
_CROSS_REFERENCE_RE = re.compile(r"docs/newly-reachable-paths\.md`'s \"([^\"]+)\" section")

_GATES_SECTION_HEADING = "### `gates.newly_reachable_paths.paths`"


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _extract_section(text: str, heading: str) -> str:
    """Return the content of the section starting at ``heading`` up to the next same-level heading."""
    idx = text.find(heading)
    if idx == -1:
        return ""
    section_text = text[idx:]
    level = len(heading.split(" ", maxsplit=1)[0])  # count leading '#'
    marker = "\n" + "#" * level + " "
    next_idx = section_text.find(marker, 1)
    if next_idx != -1:
        return section_text[:next_idx]
    return section_text


def _real_headings(text: str) -> set[str]:
    """Return every actual Markdown heading in *text*, excluding text inside fenced code blocks.

    A fenced ```markdown example block (used elsewhere in docs/newly-reachable-paths.md to show a
    hand-maintained registry file's format) can contain a line starting with ``#`` that is example
    content, not a real navigable heading -- those must not be counted as real headings.
    """
    stripped = _FENCE_RE.sub("", text)
    return {match.group(2).strip() for match in _HEADING_RE.finditer(stripped)}


@pytest.mark.unit
class TestGatesNewlyReachablePathsSection:
    """Pins the accuracy of the ``gates.newly_reachable_paths.paths`` doc section."""

    def test_section_exists(self) -> None:
        text = _read(YAML_REFERENCE_DOC)
        assert _GATES_SECTION_HEADING in text, (
            f"docs/devbench-yaml-reference.md must retain a {_GATES_SECTION_HEADING!r} section."
        )

    def test_cross_reference_names_a_heading_that_actually_exists(self) -> None:
        section = _extract_section(_read(YAML_REFERENCE_DOC), _GATES_SECTION_HEADING)
        assert section, f"{_GATES_SECTION_HEADING!r} section must exist"

        match = _CROSS_REFERENCE_RE.search(section)
        assert match, (
            f"{_GATES_SECTION_HEADING!r} section must cross-reference a quoted "
            "docs/newly-reachable-paths.md heading in the form "
            'docs/newly-reachable-paths.md`\'s "<heading text>" section.'
        )
        cited_heading_text = match.group(1)

        # _real_headings already strips the leading '#' markers, so its members are the bare
        # heading text -- directly comparable to the quoted cross-reference text above.
        real_headings = _real_headings(_read(NEWLY_REACHABLE_DOC))
        assert cited_heading_text in real_headings, (
            f"docs/devbench-yaml-reference.md cites docs/newly-reachable-paths.md heading "
            f"{cited_heading_text!r}, which does not exist in the committed "
            f"docs/newly-reachable-paths.md. Actual headings: {sorted(real_headings)}."
        )

    def test_no_retired_bug_fix_vocabulary(self) -> None:
        section = _extract_section(_read(YAML_REFERENCE_DOC), _GATES_SECTION_HEADING)
        assert section, f"{_GATES_SECTION_HEADING!r} section must exist"
        assert "bug-fix" not in section.lower(), (
            f"{_GATES_SECTION_HEADING!r} section must not use the retired 'bug-fix' task-type "
            'vocabulary; E8-F1-S1-T1 (commit b35fd53f) replaced the title-starts-with-"Fix" '
            "heuristic with the '## Task Type: behavior-fix' taxonomy."
        )
        assert "behavior-fix task" in section, (
            f"{_GATES_SECTION_HEADING!r} section must describe the cross-checked task using the "
            "shipped 'behavior-fix task' vocabulary."
        )


@pytest.mark.unit
def test_whole_document_carries_no_retired_bug_fix_task_phrasing() -> None:
    """Whole-file sweep: no 'bug-fix task' / 'bug fix task' phrasing survives anywhere in the doc."""
    text = _read(YAML_REFERENCE_DOC).lower()
    assert "bug-fix task" not in text, (
        "docs/devbench-yaml-reference.md must not contain the retired 'bug-fix task' phrasing "
        'anywhere in the document (E8-F1-S1-T1 retired the title-starts-with-"Fix" heuristic '
        "in favor of the '## Task Type: behavior-fix' taxonomy)."
    )
    assert "bug fix task" not in text, (
        "docs/devbench-yaml-reference.md must not contain the retired 'bug fix task' phrasing anywhere in the document."
    )


@pytest.mark.unit
def test_no_em_dash_present() -> None:
    text = _read(YAML_REFERENCE_DOC)
    assert "—" not in text, "docs/devbench-yaml-reference.md must not contain an em-dash (U+2014)."
