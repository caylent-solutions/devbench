"""Structural pin for E2-F7-S1-T1 (spec `integration-reality-gates-hardening.md`
section 4.11; AC-12): rubric item numbering in the two flattened judge prompts
and the `spec-to-backlog` SKILL must be unique, contiguous from 1, and
monotonic in file order within every independent numbered list. Exit
conditions must never state a bare item count (e.g. "all 10 items") because a
hard-coded count silently becomes a lie the next time a rubric item is
appended -- exit conditions must reference "every item" generically instead.

The eight E1 cherry-pick tasks preserved each source PR's own rubric item
numbers verbatim (spec 4.14): every PR was cut from a pre-0.4.0 base, so
several PRs' insertions collide with each other and with the shipped
baseline. This module is both the regression pin proving the one-time
renumbering (spec 4.11) landed correctly, and the guard against future
regressions: any new rubric insertion that reintroduces a duplicate or a gap
fails this suite immediately.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]

TEST_REVIEWER_PATH = REPO_ROOT / "plugin" / "devbench-orchestrate" / "agents" / "review_team" / "test-reviewer.md"
CODE_REVIEWER_PATH = REPO_ROOT / "plugin" / "devbench-orchestrate" / "agents" / "review_team" / "code-reviewer.md"
SKILL_PATH = REPO_ROOT / "plugin-authoring" / "devbench-authoring" / "skills" / "spec-to-backlog" / "SKILL.md"
COMPOSITION_ROOT_DOC_PATH = REPO_ROOT / "docs" / "composition-root-testing.md"

_ITEM_RE = re.compile(r"^(\d+)\.\s")
_HEADING_RE = re.compile(r"^(#{2,3})\s+(.*)$")
_BARE_ITEM_COUNT_RE = re.compile(r"\ball\s+\d+\s+items?\b", re.IGNORECASE)


@dataclass(frozen=True)
class RubricItem:
    """One numbered rubric line: the number itself, its 1-based line number
    in the source text, and the section heading it falls under (used only
    for diagnostic messages)."""

    number: int
    line_no: int
    section: str


def extract_rubric_items(text: str, *, reset_per_heading: bool) -> dict[str, list[RubricItem]]:
    """Walk `text` top-to-bottom and collect every top-level numbered rubric
    line (lines matching ``^\\d+\\.\\s``), grouped into independent numbering
    lists.

    `reset_per_heading=True` starts a fresh group at every ``##``/``###``
    heading (the SKILL, where each Step or self-critique rubric owns its own
    independent 1..N sequence). `reset_per_heading=False` collects every
    item into a single group spanning the whole file (the judge prompts,
    where numbering is one continuous stream across every ``##`` section).
    """
    groups: dict[str, list[RubricItem]] = {}
    current_section = "<whole file>"
    groups[current_section] = []
    for line_no, line in enumerate(text.splitlines(), start=1):
        heading = _HEADING_RE.match(line)
        if heading and reset_per_heading:
            current_section = f"{line_no}:{heading.group(2).strip()}"
            groups.setdefault(current_section, [])
            continue
        item = _ITEM_RE.match(line)
        if item:
            groups.setdefault(current_section, []).append(
                RubricItem(number=int(item.group(1)), line_no=line_no, section=current_section)
            )
    return {section: items for section, items in groups.items() if items}


def assert_numbering_sound(groups: dict[str, list[RubricItem]], *, file_label: str) -> None:
    """Assert every group's item numbers are unique, contiguous from 1, and
    monotonically increasing in file order. Raises `AssertionError` naming
    the file, the section, and the offending numbers on the first
    violation found (duplicates are checked before contiguity/monotonicity
    so a duplicate is reported as a duplicate, not a spurious gap)."""
    for section, items in groups.items():
        numbers = [it.number for it in items]

        seen: dict[int, int] = {}
        for it in items:
            if it.number in seen:
                raise AssertionError(
                    f"{file_label}: section '{section}' has duplicate rubric item number "
                    f"{it.number} at lines {seen[it.number]} and {it.line_no}."
                )
            seen[it.number] = it.line_no

        if numbers != sorted(numbers):
            raise AssertionError(
                f"{file_label}: section '{section}' rubric item numbers {numbers} are not monotonic in file order."
            )

        expected = list(range(1, len(numbers) + 1))
        if numbers != expected:
            raise AssertionError(
                f"{file_label}: section '{section}' rubric item numbers {numbers} are not "
                f"contiguous from 1 (expected {expected})."
            )


def find_bare_item_counts(text: str) -> list[tuple[int, str]]:
    """Return `(line_no, line)` for every line stating a bare item count
    (e.g. "all 13 items scored PASS") -- forbidden in exit conditions per
    spec 4.11, since a hard-coded count drifts the next time a rubric item
    is appended."""
    return [
        (line_no, line) for line_no, line in enumerate(text.splitlines(), start=1) if _BARE_ITEM_COUNT_RE.search(line)
    ]


def _line_for_item(text: str, number: int) -> str:
    """Return the first top-level rubric line labelled `number.` in `text`,
    or raise `AssertionError` if none exists (used by the allocation-table
    placement checks below, which assert on the item's own content, not
    just its numeric position)."""
    for line in text.splitlines():
        match = _ITEM_RE.match(line)
        if match and int(match.group(1)) == number:
            return line
    raise AssertionError(f"no top-level rubric item numbered {number}. found in the given text")


def _section_text(text: str, heading_substring: str) -> str:
    """Return the slice of `text` starting at the line whose heading
    contains `heading_substring`, up to (but excluding) the next `##`/`###`
    heading. Used to scope `_line_for_item` to one independent SKILL list
    (e.g. "4b", "5b") instead of matching an unrelated list elsewhere in
    the file that happens to share the same item numbers."""
    lines = text.splitlines()
    start = None
    for idx, line in enumerate(lines):
        heading = _HEADING_RE.match(line)
        if heading and heading_substring in heading.group(2):
            start = idx
            break
    if start is None:
        raise AssertionError(f"no heading containing {heading_substring!r} found in the given text")
    end = len(lines)
    for idx in range(start + 1, len(lines)):
        if _HEADING_RE.match(lines[idx]):
            end = idx
            break
    return "\n".join(lines[start:end])


def _resolve_item_number_by_content(section_text: str, content_substring: str) -> int:
    """Return the item number of the top-level rubric line in `section_text`
    whose text contains `content_substring`. Reuses `extract_rubric_items`
    (no second rubric parser is introduced here) to locate every top-level
    item, then matches on line content. Raises `AssertionError` naming the
    substring if no matching item is found."""
    groups = extract_rubric_items(section_text, reset_per_heading=False)
    lines = section_text.splitlines()
    for items in groups.values():
        for item in items:
            if content_substring in lines[item.line_no - 1]:
                return item.number
    raise AssertionError(f"no top-level rubric item containing {content_substring!r} found in the given section text")


def assert_doc_cites_rubric_item(doc_text: str, *, citation_prefix: str, item_number: int) -> None:
    """Assert `doc_text` contains the literal citation
    `'<citation_prefix> item <item_number>'`. Raises `AssertionError` naming
    the expected citation and the doc text searched otherwise -- used both
    by the live cross-file pin and its seeded negative control."""
    expected_citation = f"{citation_prefix} item {item_number}"
    if expected_citation not in doc_text:
        raise AssertionError(f"expected citation {expected_citation!r} not found in doc text: {doc_text!r}")


@pytest.mark.unit
class TestRubricNumberingIsSound:
    """AC-E2-F7-S1-T1-1 / AC-E2-F7-S1-T1-2 (spec 4.11; AC-12): the shipped
    prompts and SKILL carry unique, contiguous, monotonic rubric numbering."""

    def test_test_reviewer_numbering_is_sound(self) -> None:
        text = TEST_REVIEWER_PATH.read_text(encoding="utf-8")
        groups = extract_rubric_items(text, reset_per_heading=False)
        assert_numbering_sound(groups, file_label=TEST_REVIEWER_PATH.name)

    def test_code_reviewer_numbering_is_sound(self) -> None:
        text = CODE_REVIEWER_PATH.read_text(encoding="utf-8")
        groups = extract_rubric_items(text, reset_per_heading=False)
        assert_numbering_sound(groups, file_label=CODE_REVIEWER_PATH.name)

    def test_skill_numbering_is_sound_per_independent_list(self) -> None:
        text = SKILL_PATH.read_text(encoding="utf-8")
        groups = extract_rubric_items(text, reset_per_heading=True)
        assert_numbering_sound(groups, file_label=SKILL_PATH.name)


@pytest.mark.unit
class TestAllocationTablePlacement:
    """AC-E2-F7-S1-T1-3 / AC-E2-F7-S1-T1-4 (spec 4.11): the specific
    post-0.4.0-baseline allocation-table anchors land exactly where section
    4.11 states, and every renumbered item still carries the content that
    identifies it (a pure duplicate-number bug could otherwise pass a
    numbers-only check by coincidence)."""

    def test_test_reviewer_insertions_occupy_54_to_56(self) -> None:
        text = TEST_REVIEWER_PATH.read_text(encoding="utf-8")
        assert "FIXTURE_CATALOG_MISMATCH" in _line_for_item(text, 54)
        assert "canonical_sources" in _line_for_item(text, 55) or "skip note" in _line_for_item(text, 55)
        assert "allow_missing" in _line_for_item(text, 56)

    def test_code_reviewer_insertions_occupy_53_to_55(self) -> None:
        text = CODE_REVIEWER_PATH.read_text(encoding="utf-8")
        assert "NEWLY_REACHABLE" in _line_for_item(text, 53)
        assert "newly-reachable path" in _line_for_item(text, 54).lower()
        assert "cross-cutting-primitives" in _line_for_item(text, 55)

    def test_skill_step_4b_occupies_8_to_9(self) -> None:
        text = _section_text(SKILL_PATH.read_text(encoding="utf-8"), "4b -- Self-critique at Epic granularity")
        assert "permission/eligibility flag has its own write-path task" in _line_for_item(text, 8)
        assert "Work-group dependency gate present" in _line_for_item(text, 9)

    def test_skill_step_5b_anchors_span_13_to_15(self) -> None:
        text = _section_text(SKILL_PATH.read_text(encoding="utf-8"), "5b -- Self-critique at per-Task granularity")
        assert "AC-FINAL tier-suffix on non-Python tasks" in _line_for_item(text, 13)
        assert "Write-path task is distinct and seam-referenced" in _line_for_item(text, 14)
        assert "Composition-root DoD item present when required" in _line_for_item(text, 15)

    def test_skill_step_7_rubric_occupies_12_to_13(self) -> None:
        text = _section_text(SKILL_PATH.read_text(encoding="utf-8"), "Self-critique rubric for spec-to-backlog")
        assert "Write-path ownership never implicit" in _line_for_item(text, 12)
        assert "Ancestry gate present and fully wired" in _line_for_item(text, 13)


@pytest.mark.unit
class TestNoBareItemCounts:
    """AC-E2-F7-S1-T1-5 (spec 4.11; AC-12): no exit condition states a bare
    item count anywhere in the three edited files."""

    @pytest.mark.parametrize(
        "path",
        [TEST_REVIEWER_PATH, CODE_REVIEWER_PATH, SKILL_PATH],
        ids=lambda p: p.name,
    )
    def test_no_bare_item_count_in_shipped_file(self, path: Path) -> None:
        text = path.read_text(encoding="utf-8")
        hits = find_bare_item_counts(text)
        assert not hits, f"{path.name} states a bare item count at: {hits}"

    def test_bare_item_count_detector_flags_a_seeded_violation(self) -> None:
        """Seeded-violation case (Approach step 1c): proves the detector
        genuinely fires rather than trivially passing by construction."""
        seeded = "Exit conditions:\n2. Every leaf task passes the rubric (all 10 items scored PASS).\n"
        hits = find_bare_item_counts(seeded)
        assert hits == [(2, "2. Every leaf task passes the rubric (all 10 items scored PASS).")]

    def test_bare_item_count_detector_does_not_flag_generic_wording(self) -> None:
        """Negative control: "every item" (the required generic wording) must
        not itself trip the detector."""
        generic = "2. Every leaf task passes the rubric (every item scored PASS in Step 5b).\n"
        assert find_bare_item_counts(generic) == []


@pytest.mark.unit
class TestExtractorDetectsSeededViolations:
    """Approach step 1c: a seeded-violation case over a synthetic string
    proving the extractor genuinely detects a duplicate and a gap, not
    merely passing because the shipped files happen to be clean."""

    def test_extractor_flags_a_duplicate_item_number(self) -> None:
        synthetic = "## Section\n1. one\n2. two\n2. duplicate two\n3. three\n"
        groups = extract_rubric_items(synthetic, reset_per_heading=False)
        with pytest.raises(AssertionError, match=r"duplicate rubric item number 2 at lines 3 and 4"):
            assert_numbering_sound(groups, file_label="synthetic.md")

    def test_extractor_flags_a_gap_in_numbering(self) -> None:
        synthetic = "## Section\n1. one\n2. two\n4. four (gap, missing 3)\n"
        groups = extract_rubric_items(synthetic, reset_per_heading=False)
        with pytest.raises(AssertionError, match="not contiguous from 1"):
            assert_numbering_sound(groups, file_label="synthetic.md")

    def test_extractor_passes_a_clean_synthetic_list(self) -> None:
        """Positive control: a genuinely clean list must not raise."""
        synthetic = "## Section\n1. one\n2. two\n3. three\n"
        groups = extract_rubric_items(synthetic, reset_per_heading=False)
        assert_numbering_sound(groups, file_label="synthetic.md")

    def test_extractor_resets_per_heading_when_requested(self) -> None:
        """SKILL-style lists: two independent headings each restart at 1,
        which must NOT be reported as a duplicate/non-contiguous violation
        when `reset_per_heading=True`."""
        synthetic = "### 4b -- A\n1. a\n2. b\n### 5b -- B\n1. c\n2. d\n3. e\n"
        groups = extract_rubric_items(synthetic, reset_per_heading=True)
        assert_numbering_sound(groups, file_label="synthetic.md")
        assert len(groups) == 2


@pytest.mark.unit
class TestCrossFileDocCitationStaysInSync:
    """AC-E2-F7-S1-T3-1 / AC-E2-F7-S1-T3-3 / AC-E2-F7-S1-T3-4 (AC-E2-F7-S1-T1-6):
    `docs/composition-root-testing.md` cites the Step 5b composition-root
    rubric item by its real, run-time-resolved position in `SKILL.md`
    rather than a hard-coded number, so a future renumbering that forgets
    this doc fails loudly naming the expected citation and the stale text."""

    def test_doc_cites_the_resolved_step_5b_item_number(self) -> None:
        skill_text = SKILL_PATH.read_text(encoding="utf-8")
        section_text = _section_text(skill_text, "5b -- Self-critique at per-Task granularity")
        expected_number = _resolve_item_number_by_content(
            section_text, "Composition-root DoD item present when required"
        )
        doc_text = COMPOSITION_ROOT_DOC_PATH.read_text(encoding="utf-8")
        assert_doc_cites_rubric_item(doc_text, citation_prefix="Step 5b", item_number=expected_number)

    def test_seeded_wrong_citation_fails_the_shared_assertion(self) -> None:
        """Seeded negative control (Approach step 2): a synthetic doc string
        carrying a deliberately wrong item number must raise, proving the
        pin cannot pass by construction if the doc were emptied, renamed, or
        the sentence deleted."""
        synthetic_doc = "`spec-to-backlog` (Step 1b item 13 and Step 5b item 13) requires the ..."
        with pytest.raises(AssertionError, match=r"expected citation 'Step 5b item 15' not found"):
            assert_doc_cites_rubric_item(synthetic_doc, citation_prefix="Step 5b", item_number=15)
