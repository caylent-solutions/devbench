"""Provenance map + resolvability doc test (spec `integration-reality-gates-hardening.md`
section 4.12, PM-secondary-2; section 5.5; AC-3; AC-E2-F7-S1-T2-1 through -5).

`docs/issue-provenance.md` is the single table mapping every one of the eight
integration-reality gates to its `caylent-solutions/devbench-internal-backlog`
issue (`#10`-`#17`), its source pull request (`#315`-`#322`), any
`caylent-solutions/devbench`-repo issue it is tied to, and the spec section that
defines it. E11's closure work units (spec 4.13) read this table verbatim to know
which issues, in which repo, to close -- it is the input to that closure work, not
decoration (AC-E2-F7-S1-T2-5).

The eight source pull requests each carried a placeholder citation to the
internal-backlog issue they close, authored before the real internal-backlog
issues existed. Those placeholders used a bare, zero-padded two-digit form
(`#01`-`#08`) that no real GitHub issue number in either repo ever takes (GitHub
issue numbers are never zero-padded, and this repo's own issue numbering is
either the small 10-17 internal-backlog range with the full
`devbench-internal-backlog#<N>` qualifier, or the three-digit-and-up
`caylent-solutions/devbench` range). `find_unresolvable_citations` below is the
mechanical proof that none of those placeholders survived the Epic 1 cherry-pick
(AC-3, AC-E2-F7-S1-T2-2, AC-E2-F7-S1-T2-3): it walks exactly six root/extension
pairs -- `docs/*.md`, `plugin/*.md`, `plugin/*.sh`, `plugin-authoring/*.md`,
`src/devbench/*.py` and `tests/*.py` (see `_CAMPAIGN_GLOB_ROOTS`; this is not
every extension under every root), plus `CHANGELOG.md` (discovered by directory
walk per the Definition of Ready, so a file a later epic adds under one of
those six pairs is covered with no second registration step, and excluding
this map and its own test module -- see `_SELF_EXCLUDED_RELATIVE_PATHS`), for
the fully-qualified `devbench-internal-backlog#<N>` citation form and the
fabricated zero-padded `#01`-`#08` form, and asserts every one resolves
against a row in the map. Bare `caylent-solutions/devbench#<N>` citations and
any surface outside those six root/extension pairs -- including JSON config
files such as `src/devbench/config-schema.json` -- are outside this walk (see
`extract_issue_tokens` and `_CAMPAIGN_GLOB_ROOTS`).

`parse_provenance_map` is the single annotated helper every test case in this
module uses to read the map (Approach step 6): it is the one place that knows the
five-column table shape, and it raises naming the offending line when a data row
is missing a column (Task-specific error path in the work unit spec).
`find_invalid_spec_sections` mirrors `find_unresolvable_citations`'s shape for
the Spec Section column (AC-E2-F7-S1-T2-4): both the real check and the seeded
negative control call the same detector function, so a gutted detector fails
both.

`VALID_SPEC_SECTIONS` (AC-E2-F7-S1-T2-4) is a one-time, transcribed pin of the
real heading structure of `spec/integration-reality-gates-hardening.md` as of
this task's authoring (2026-08-18), not a live read of that file: the spec lives
one directory above this repo's checkout in the `devbench-updates` workspace, and
this repo's own test suite deliberately isolates itself from any live workspace
path (`tests/conftest.py`, issue #292) so the suite behaves identically whether
run inside this workspace or in `caylent-solutions/devbench`'s own standalone CI,
where no sibling `spec/` directory exists at all. Pinning the heading set here
mirrors the same technique `tests/test_plugin/test_rubric_numbering.py` already
uses for spec-derived facts (item numbers, section anchors) without ever
opening the external file.

Source: E2-F7-S1-T2. Spec sections 4.12, 5.5, 4.13, 8; AC-3, AC-23, AC-24;
AC-E2-F7-S1-T2-1 through -5.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

import pytest

from devbench.constants import GATE_NAMES

REPO_ROOT = Path(__file__).parent.parent.parent

PROVENANCE_MAP_PATH = REPO_ROOT / "docs" / "issue-provenance.md"
CHANGELOG_PATH = REPO_ROOT / "CHANGELOG.md"

_TABLE_HEADER = ("Gate", "Internal Issue", "Source PR", "Devbench Issues", "Spec Section")

# Named constant (DRY; DoR item 2): the eight gates' internal-backlog issue and
# source-PR numbers, transcribed once from spec sections 4.4-4.9 and
# cross-checked against the section-4 headings so no gate is mapped to the
# wrong issue. Keyed by `devbench.constants.GATE_NAMES` so a future gate
# addition to that tuple is impossible to silently leave out of this pin --
# `test_expected_gate_provenance_keys_match_gate_names` below fails loudly if
# the two ever drift apart.
EXPECTED_GATE_PROVENANCE: dict[str, tuple[int, int, str]] = {
    "reachability": (10, 315, "4.4"),
    "ancestry": (12, 317, "4.5"),
    "shared_file_impact": (13, 318, "4.6"),
    "fixture_consistency": (17, 322, "4.7"),
    "write_path_audit": (16, 321, "4.8"),
    "newly_reachable_paths": (15, 320, "4.9"),
    "composition_root": (11, 316, "4.9"),
    "layout_geometry": (14, 319, "4.9"),
}

# The devbench-repo issues DoR item 3 confirms as in scope: #335/#336 landed on
# `feat/bug-closure` before this campaign's branch was cut (spec Section 1.2,
# D-12).
LANDED_DEVBENCH_ISSUES: tuple[str, ...] = (
    "caylent-solutions/devbench#335",
    "caylent-solutions/devbench#336",
)

# The five follow-ups spec Section 15 defers filing until E11; each needs a
# placeholder row in the map (DoR item 3) since none has an issue number yet.
# Wording matches `docs/issue-provenance.md`'s placeholder rows verbatim.
SECTION_15_DEFERRED_FOLLOWUPS: tuple[str, ...] = (
    "assert-tests-pass.sh fail-open rework",
    "guard-git-stage rule-1 cwd/-C quirks",
    "real-browser layout machine-verification design",
    "build-time generation of rubric bodies",
    "auto-registry fan-in tuning telemetry",
)

# One-time transcription of every real heading in
# `spec/integration-reality-gates-hardening.md` (`## Section <N>` and
# `### <N>.<M> ...` forms only -- the bold inline paragraph markers such as
# "1.1" or "0.5" are prose, not headings, and are never cited by a map row).
# See the module docstring for why this is a pin rather than a live read.
VALID_SPEC_SECTIONS: frozenset[str] = frozenset(
    {
        "0",
        "1",
        "2",
        "3",
        "3.5",
        "3.6",
        "4",
        "4.1",
        "4.2",
        "4.3",
        "4.4",
        "4.5",
        "4.6",
        "4.7",
        "4.8",
        "4.9",
        "4.10",
        "4.11",
        "4.12",
        "4.13",
        "4.14",
        "4.15",
        "5",
        "6",
        "7",
        "8",
        "9",
        "10",
        "11",
        "12",
        "13",
        "14",
        "15",
    }
)

# Named constant (DRY, mirrors `tests/test_docs/test_gate_tier_vocabulary.py`'s
# `SCANNED_DIRECTORIES`): the campaign-touched surface the resolvability walk
# covers, expressed as directory roots plus a glob per spec-8's documentation
# same-commit list, not a hard-coded file list -- a file a later epic adds
# under any of these roots is swept in automatically (DoR item 4).
_CAMPAIGN_GLOB_ROOTS: tuple[tuple[Path, str], ...] = (
    (REPO_ROOT / "docs", "*.md"),
    (REPO_ROOT / "plugin", "*.md"),
    (REPO_ROOT / "plugin", "*.sh"),
    (REPO_ROOT / "plugin-authoring", "*.md"),
    (REPO_ROOT / "src" / "devbench", "*.py"),
    (REPO_ROOT / "tests", "*.py"),
)

# This map and its own test module cannot be evidence against themselves: the
# map is the resolution authority, and this module is what does the walking
# (its seeded fabricated-citation fixtures below would otherwise trip its own
# detector).
_SELF_EXCLUDED_RELATIVE_PATHS: frozenset[str] = frozenset(
    {
        "docs/issue-provenance.md",
        "tests/test_docs/test_issue_provenance.py",
    }
)

# Fully-qualified internal-backlog citation, e.g. "devbench-internal-backlog#10"
# or "caylent-solutions/devbench-internal-backlog#10".
_QUALIFIED_ISSUE_RE = re.compile(r"(?:caylent-solutions/)?devbench-internal-backlog#(\d+)")

# The exact placeholder shape the eight source PRs carried: a bare, zero-padded
# two-digit token. Never a legitimate citation in this codebase (GitHub issue
# numbers are never zero-padded), so its mere presence is itself the failure --
# no map lookup is needed to know it is wrong.
_FABRICATED_ISSUE_RE = re.compile(r"(?<!\w)#(0[1-8])\b")

_SEPARATOR_CELL_RE = re.compile(r"^:?-+:?$")
_LEADING_SECTION_NUMBER_RE = re.compile(r"^(\d+(?:\.\d+)*)")


@dataclass(frozen=True)
class ProvenanceRow:
    """One parsed data row of `docs/issue-provenance.md`'s five-column table."""

    gate: str
    internal_issue: str
    source_pr: str
    devbench_issues: str
    spec_section: str
    line_no: int


@dataclass(frozen=True)
class UnresolvedCitation:
    """One issue token found during the campaign-file walk that does not
    resolve against the provenance map (AC-E2-F7-S1-T2-2)."""

    path: Path
    line_no: int
    token: str


@dataclass(frozen=True)
class InvalidSpecSectionCitation:
    """One provenance-map row whose Spec Section cell does not resolve to a
    real heading in `VALID_SPEC_SECTIONS` (AC-E2-F7-S1-T2-4)."""

    line_no: int
    gate: str
    spec_section: str


def _strip_cell_markup(cell: str) -> str:
    """Strip a single layer of surrounding backticks from a table cell, so
    resolution logic reads the plain token underneath the doc's code-span
    styling."""
    stripped = cell.strip()
    if stripped.startswith("`") and stripped.endswith("`") and len(stripped) >= 2:
        return stripped[1:-1]
    return stripped


def parse_provenance_map(text: str) -> list[ProvenanceRow]:
    """Parse the single pipe-table in `docs/issue-provenance.md` into
    `ProvenanceRow` objects (Approach step 6's one annotated helper).

    Skips every line until the header row (`| Gate | Internal Issue | ... |`)
    is found, then the markdown separator row, then collects every following
    `|`-delimited line as a data row. Raises `ValueError` naming the line
    when a data row has fewer than the five required columns (Task-specific
    error path: "The map parser raises naming the row when a provenance row
    is missing one of the five required columns.").
    """
    rows: list[ProvenanceRow] = []
    header_seen = False
    for line_no, raw_line in enumerate(text.splitlines(), start=1):
        line = raw_line.strip()
        if not line.startswith("|"):
            continue
        cells = [c.strip() for c in line.strip("|").split("|")]
        if not header_seen:
            if tuple(cells[:5]) == _TABLE_HEADER:
                header_seen = True
            continue
        if all(_SEPARATOR_CELL_RE.match(c) for c in cells):
            continue
        if len(cells) < 5:
            raise ValueError(
                f"docs/issue-provenance.md line {line_no} has {len(cells)} column(s), "
                f"need 5 ({', '.join(_TABLE_HEADER)}): {line!r}"
            )
        rows.append(
            ProvenanceRow(
                gate=_strip_cell_markup(cells[0]),
                internal_issue=_strip_cell_markup(cells[1]),
                source_pr=_strip_cell_markup(cells[2]),
                devbench_issues=cells[3],
                spec_section=_strip_cell_markup(cells[4]),
                line_no=line_no,
            )
        )
    return rows


def extract_issue_tokens(text: str) -> list[tuple[int, str]]:
    """Return `(line_no, '#<digits>')` for every internal-backlog-style issue
    token in `text`: both the fully-qualified `devbench-internal-backlog#<N>`
    form and the bare zero-padded `#0[1-8]` fabricated-citation form."""
    found: list[tuple[int, str]] = []
    for line_no, line in enumerate(text.splitlines(), start=1):
        for match in _QUALIFIED_ISSUE_RE.finditer(line):
            found.append((line_no, f"#{match.group(1)}"))
        for match in _FABRICATED_ISSUE_RE.finditer(line):
            found.append((line_no, f"#{match.group(1)}"))
    return found


def resolves(token: str, valid_internal_issue_numbers: frozenset[int]) -> bool:
    """A token resolves when its numeric value is one of the map's internal-backlog
    issue numbers. Zero-padded tokens (the fabricated form) never parse to a
    number that is also zero-padded in the map, but `int()` normalises both
    sides, so resolution is judged on value, and the fabricated placeholders
    (`01`-`08`) are never in the map's 10-17 range regardless."""
    return int(token.lstrip("#")) in valid_internal_issue_numbers


def discover_campaign_files() -> list[Path]:
    """Discover the campaign-touched file set as a directory walk (DoR item 4),
    not a hard-coded list, so a file a later epic adds under any of
    `_CAMPAIGN_GLOB_ROOTS` is covered with no second registration step.

    Raises `FileNotFoundError` naming the missing root if any configured root
    directory does not exist, and the same for `CHANGELOG_PATH` if it is not a
    file -- a missing root or file is a repo-layout regression, never a
    silent zero-file scan.
    """
    found: set[Path] = set()
    for root, pattern in _CAMPAIGN_GLOB_ROOTS:
        if not root.is_dir():
            raise FileNotFoundError(f"campaign glob root does not exist: {root}")
        for path in root.rglob(pattern):
            relative = path.relative_to(REPO_ROOT).as_posix()
            if relative in _SELF_EXCLUDED_RELATIVE_PATHS:
                continue
            found.add(path)
    if not CHANGELOG_PATH.is_file():
        raise FileNotFoundError(f"campaign file does not exist: {CHANGELOG_PATH}")
    relative = CHANGELOG_PATH.relative_to(REPO_ROOT).as_posix()
    if relative not in _SELF_EXCLUDED_RELATIVE_PATHS:
        found.add(CHANGELOG_PATH)
    return sorted(found)


def find_unresolvable_citations(
    paths: list[Path], valid_internal_issue_numbers: frozenset[int]
) -> list[UnresolvedCitation]:
    """Walk `paths` and return one `UnresolvedCitation` per issue token that
    does not resolve against `valid_internal_issue_numbers` (AC-E2-F7-S1-T2-2)."""
    findings: list[UnresolvedCitation] = []
    for path in paths:
        text = path.read_text(encoding="utf-8")
        for line_no, token in extract_issue_tokens(text):
            if not resolves(token, valid_internal_issue_numbers):
                findings.append(UnresolvedCitation(path=path, line_no=line_no, token=token))
    return findings


def extract_section_number(cell: str) -> str:
    """Extract the leading dotted section number from a Spec Section cell,
    stripping an optional `Section ` prefix and any trailing parenthetical
    (e.g. `"4.9(a)"` -> `"4.9"`, `"Section 15"` -> `"15"`)."""
    text = _strip_cell_markup(cell)
    if text.lower().startswith("section "):
        text = text[len("section ") :].strip()
    match = _LEADING_SECTION_NUMBER_RE.match(text)
    if not match:
        raise ValueError(f"cannot extract a section number from Spec Section cell {cell!r}")
    return match.group(1)


def find_invalid_spec_sections(
    rows: list[ProvenanceRow], valid_spec_sections: frozenset[str]
) -> list[InvalidSpecSectionCitation]:
    """Return one `InvalidSpecSectionCitation` per map row whose Spec Section
    cell does not resolve to a real heading in `valid_spec_sections`
    (AC-E2-F7-S1-T2-4), mirroring `find_unresolvable_citations`'s shape so
    both detectors share the same walk-and-collect pattern (Approach step 6,
    DRY)."""
    findings: list[InvalidSpecSectionCitation] = []
    for row in rows:
        section = extract_section_number(row.spec_section)
        if section not in valid_spec_sections:
            findings.append(InvalidSpecSectionCitation(line_no=row.line_no, gate=row.gate, spec_section=section))
    return findings


def _map_rows() -> list[ProvenanceRow]:
    return parse_provenance_map(PROVENANCE_MAP_PATH.read_text(encoding="utf-8"))


def _valid_internal_issue_numbers(rows: list[ProvenanceRow]) -> frozenset[int]:
    numbers: set[int] = set()
    for row in rows:
        match = re.search(r"#(\d+)", row.internal_issue)
        if match:
            numbers.add(int(match.group(1)))
    return frozenset(numbers)


@pytest.mark.unit
class TestProvenanceMapStructure:
    """AC-E2-F7-S1-T2-1: the map carries one row per gate with all five columns,
    and DoR item 2's "no gate mapped to the wrong issue" check."""

    def test_expected_gate_provenance_keys_match_gate_names(self) -> None:
        """Guards the pin itself: `EXPECTED_GATE_PROVENANCE` must cover exactly
        `devbench.constants.GATE_NAMES`, so a future gate addition cannot
        silently bypass this module's coverage."""
        assert set(EXPECTED_GATE_PROVENANCE) == set(GATE_NAMES)

    def test_map_file_exists(self) -> None:
        assert PROVENANCE_MAP_PATH.is_file(), f"expected {PROVENANCE_MAP_PATH} to exist"

    def test_map_covers_all_eight_gates_exactly_once(self) -> None:
        rows = _map_rows()
        gate_rows = [row for row in rows if row.gate in GATE_NAMES]
        gates_seen = [row.gate for row in gate_rows]
        assert sorted(gates_seen) == sorted(GATE_NAMES), (
            f"expected every gate in {sorted(GATE_NAMES)} exactly once, got {sorted(gates_seen)}"
        )

    def test_every_gate_row_matches_the_pinned_spec_provenance(self) -> None:
        """DoR item 2: no gate is mapped to the wrong internal issue, source PR,
        or spec section."""
        rows_by_gate = {row.gate: row for row in _map_rows() if row.gate in GATE_NAMES}
        for gate, (expected_issue, expected_pr, expected_section) in EXPECTED_GATE_PROVENANCE.items():
            row = rows_by_gate[gate]
            assert row.internal_issue == f"caylent-solutions/devbench-internal-backlog#{expected_issue}", (
                f"{gate}: internal issue cell {row.internal_issue!r} does not match spec 4.4-4.9's #{expected_issue}"
            )
            assert row.source_pr == f"caylent-solutions/devbench#{expected_pr}", (
                f"{gate}: source PR cell {row.source_pr!r} does not match spec 4.14's #{expected_pr}"
            )
            assert extract_section_number(row.spec_section) == expected_section, (
                f"{gate}: spec section cell {row.spec_section!r} does not match spec's {expected_section}"
            )

    def test_parser_raises_on_row_missing_a_column(self) -> None:
        """Task-specific error path: the map parser raises naming the row when a
        provenance row is missing one of the five required columns."""
        synthetic = (
            "| Gate | Internal Issue | Source PR | Devbench Issues | Spec Section |\n"
            "|------|-----------------|-----------|------------------|--------------|\n"
            "| `reachability` | `caylent-solutions/devbench-internal-backlog#10` | `caylent-solutions/devbench#315` |\n"
        )
        with pytest.raises(ValueError, match=r"line 3 has 3 column\(s\), need 5"):
            parse_provenance_map(synthetic)


@pytest.mark.unit
class TestResolvability:
    """AC-E2-F7-S1-T2-2, AC-E2-F7-S1-T2-3 (spec 4.12; AC-3)."""

    def test_no_unresolvable_citations_in_campaign_touched_files(self) -> None:
        valid_numbers = _valid_internal_issue_numbers(_map_rows())
        paths = discover_campaign_files()
        assert paths, "expected at least one campaign-touched file to be discovered"
        findings = find_unresolvable_citations(paths, valid_numbers)
        assert findings == [], (
            f"campaign-touched files must carry zero unresolvable issue citations (AC-3); found: {findings}"
        )

    def test_seeded_fabricated_citation_is_detected(self, tmp_path: Path) -> None:
        """AC-E2-F7-S1-T2-3: a seeded file containing a fabricated `#01`-style
        citation fails resolution, naming the file, line and token."""
        seeded = tmp_path / "synthetic-fabricated-citation.md"
        seeded.write_text(
            "# Synthetic\n\nSee caylent-solutions/devbench-internal-backlog#01 for the original report.\n",
            encoding="utf-8",
        )
        valid_numbers = _valid_internal_issue_numbers(_map_rows())

        findings = find_unresolvable_citations([seeded], valid_numbers)

        assert len(findings) == 1, f"expected exactly one seeded finding, got: {findings}"
        finding = findings[0]
        assert finding.path == seeded
        assert finding.line_no == 3
        assert finding.token == "#01"

    def test_seeded_bare_fabricated_citation_is_detected(self, tmp_path: Path) -> None:
        """The bare (unqualified) placeholder form is also detected on its own,
        proving the fabricated-form detector does not require the
        `devbench-internal-backlog` qualifier to fire."""
        seeded = tmp_path / "synthetic-bare-fabricated-citation.md"
        seeded.write_text("Originally tracked as #08 before the real issue existed.\n", encoding="utf-8")
        valid_numbers = _valid_internal_issue_numbers(_map_rows())

        findings = find_unresolvable_citations([seeded], valid_numbers)

        assert findings == [UnresolvedCitation(path=seeded, line_no=1, token="#08")]

    def test_qualified_legitimate_citation_resolves(self) -> None:
        """Positive control: a genuinely in-range qualified citation resolves
        and produces no finding."""
        valid_numbers = frozenset({10, 11, 12, 13, 14, 15, 16, 17})
        assert extract_issue_tokens("See caylent-solutions/devbench-internal-backlog#11 for details.\n") == [(1, "#11")]
        assert resolves("#11", valid_numbers) is True

    def test_extractor_ignores_unrelated_bare_three_digit_issue_numbers(self) -> None:
        """Negative control: a bare, non-zero-padded three-digit devbench-repo
        issue citation (e.g. `(issue #228)`, a real, already-resolved,
        pre-campaign devbench issue unrelated to this campaign) must never be
        extracted as an internal-backlog token -- it is neither qualified nor
        the zero-padded fabricated shape -- while a genuinely qualified
        citation on the same line still resolves."""
        text = "Step 5b (issue #228 baseline, devbench-internal-backlog#15 newly-reachable-paths).\n"
        tokens = extract_issue_tokens(text)
        assert tokens == [(1, "#15")], f"expected only the qualified #15 form, #228 ignored; got {tokens}"

    def test_extractor_ignores_bare_two_digit_non_fabricated_numbers(self) -> None:
        """Negative control: a bare `#11` with no `devbench-internal-backlog`
        qualifier and no zero-padding is not itself the fabricated shape, so
        it is not extracted (only the qualified form or the zero-padded `#0N`
        form are)."""
        assert extract_issue_tokens("See (issue #11) for context.\n") == []

    def test_extractor_does_not_match_three_digit_zero_prefixed_tokens(self) -> None:
        """`#010` must not be mistaken for the two-digit fabricated form `#01`."""
        assert extract_issue_tokens("Reference #010 in an unrelated numbering scheme.\n") == []


@pytest.mark.unit
class TestSpecSectionsExist:
    """AC-E2-F7-S1-T2-4: every spec section cited by a map row exists in the spec."""

    def test_every_map_row_spec_section_exists(self) -> None:
        rows = _map_rows()
        assert rows, "expected at least one parsed provenance row"
        findings = find_invalid_spec_sections(rows, VALID_SPEC_SECTIONS)
        assert findings == [], (
            f"every spec section cited by a map row must exist in the spec (AC-E2-F7-S1-T2-4); found: {findings}"
        )

    def test_seeded_row_citing_a_nonexistent_section_is_detected(self) -> None:
        """Uses the real `find_invalid_spec_sections` detector (not a
        reimplemented membership check) on a seeded row, so gutting the
        detector this module actually relies on would fail this control."""
        synthetic = (
            "| Gate | Internal Issue | Source PR | Devbench Issues | Spec Section |\n"
            "|------|-----------------|-----------|------------------|--------------|\n"
            "| `reachability` | `caylent-solutions/devbench-internal-backlog#10` "
            "| `caylent-solutions/devbench#315` | none | `4.99` |\n"
        )
        rows = parse_provenance_map(synthetic)
        assert len(rows) == 1

        findings = find_invalid_spec_sections(rows, VALID_SPEC_SECTIONS)

        assert findings == [InvalidSpecSectionCitation(line_no=3, gate="reachability", spec_section="4.99")]

    def test_section_number_extractor_strips_section_prefix_and_letter_suffix(self) -> None:
        assert extract_section_number("`Section 15`") == "15"
        assert extract_section_number("`4.9(a)`") == "4.9"
        assert extract_section_number("`4.4`") == "4.4"


@pytest.mark.unit
class TestClosureCompleteness:
    """AC-E2-F7-S1-T2-5 (spec 4.13): the map is complete enough for E11 to drive
    closure from it, covering both repos' issues."""

    def test_internal_backlog_issues_10_through_17_are_all_present(self) -> None:
        valid_numbers = _valid_internal_issue_numbers(_map_rows())
        assert valid_numbers == frozenset(range(10, 18))

    def test_landed_devbench_issues_are_present_in_the_map(self) -> None:
        all_devbench_issue_cells = " ".join(row.devbench_issues for row in _map_rows())
        for issue in LANDED_DEVBENCH_ISSUES:
            assert issue in all_devbench_issue_cells, (
                f"expected {issue!r} to appear in the map's Devbench Issues column"
            )

    def test_every_section_15_followup_has_a_placeholder_row(self) -> None:
        rows = _map_rows()
        non_gate_rows_text = " ".join(row.gate for row in rows if row.gate not in GATE_NAMES)
        for followup in SECTION_15_DEFERRED_FOLLOWUPS:
            assert followup in non_gate_rows_text, f"expected a placeholder row naming {followup!r}"


@pytest.mark.unit
class TestDiscoverCampaignFiles:
    """DoR item 4: the walked file set is discovered, not hard-coded."""

    def test_discovers_known_campaign_files(self) -> None:
        paths = discover_campaign_files()
        relative_paths = {p.relative_to(REPO_ROOT).as_posix() for p in paths}
        assert "CHANGELOG.md" in relative_paths
        assert "src/devbench/constants.py" in relative_paths
        assert "docs/cli-reference.md" in relative_paths

    def test_excludes_the_map_and_its_own_test_module(self) -> None:
        paths = discover_campaign_files()
        relative_paths = {p.relative_to(REPO_ROOT).as_posix() for p in paths}
        assert "docs/issue-provenance.md" not in relative_paths
        assert "tests/test_docs/test_issue_provenance.py" not in relative_paths

    def test_missing_glob_root_raises(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        missing_root = tmp_path / "does-not-exist"
        monkeypatch.setattr(
            "tests.test_docs.test_issue_provenance._CAMPAIGN_GLOB_ROOTS",
            ((missing_root, "*.md"),),
        )
        with pytest.raises(FileNotFoundError, match=re.escape(str(missing_root))):
            discover_campaign_files()

    def test_missing_changelog_raises(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """The `CHANGELOG.md` inclusion must fail fast the same way a missing
        glob root does, never silently drop the file from the walked set."""
        missing_changelog = tmp_path / "does-not-exist-CHANGELOG.md"
        monkeypatch.setattr(
            "tests.test_docs.test_issue_provenance.CHANGELOG_PATH",
            missing_changelog,
        )
        with pytest.raises(FileNotFoundError, match=re.escape(str(missing_changelog))):
            discover_campaign_files()
