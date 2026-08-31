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
files such as `src/devbench/config-schema.json` -- are outside *this*
detector's walk (see `extract_issue_tokens` and `_CAMPAIGN_GLOB_ROOTS`); bare
devbench-repo citations are covered by a second, separate walk over the same
discovered file set -- see `TestClosingKeywordCountInvariant`'s docstring
below.

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

`TestClosingKeywordCountInvariant` (AC-TEST-001, added by E11-F1-S2-T3) reads a
second file this module did not previously touch,
`docs/release-notes/candidate-release-integration-reality-gates.md`
(`RELEASE_NOTES_PATH`), and pins that its `### Closes` block carries exactly
one recognised GitHub closing-keyword line (case-insensitive `close(s/d)`,
`fix(es/ed)`, `resolve(s/d)`) per numbered mapped issue this campaign closes,
excluding the five deliberately-OPEN Section 15 follow-up rows, unfenced, and
raises naming any non-empty line under the heading it cannot classify rather
than silently skipping it (AC-DOC-003's fail-open hazard).

`extract_devbench_repo_issue_tokens` and `resolves` (AC-TEST-002, same task)
resolve a `caylent-solutions/devbench#<N>` citation against the map's
Devbench Issues column unioned with its Source PR column (both already
parsed into `ProvenanceRow`). `find_unresolvable_citations` is reused,
parameterised by extractor and resolver-input set, to walk the same
`discover_campaign_files()` surface this module already walks for
internal-backlog citations (`TestResolvability.
test_no_unresolvable_devbench_repo_citations_in_campaign_touched_files`), so a
fabricated `caylent-solutions/devbench#<N>` citation written into any real
campaign file now produces a real finding instead of passing vacuously.
Measured directly against the discovered file set (43 total qualified
`caylent-solutions/devbench#<N>` citations, not "hundreds"), only two numbers
are pre-campaign citations unrelated to any map row and require a documented
allowlist (`_PRE_CAMPAIGN_DEVBENCH_ISSUE_ALLOWLIST`: `#198`, the documented
haiku config-load ban; `#233`, one historical citation in
`tests/test_constants.py`); a third, `#900`, is a synthetic fixture string in
`tests/test_docs/test_config_schema_gate_provenance.py` and is excluded via
the same `_SELF_EXCLUDED_RELATIVE_PATHS` mechanism this module already uses
to exclude itself.

Source: E2-F7-S1-T2, E11-F1-S2-T3. Spec sections 4.12, 5.5, 4.13, 8; AC-3,
AC-23, AC-24; AC-E2-F7-S1-T2-1 through -5; AC-TEST-001, AC-TEST-002,
AC-TEST-003.
"""

from __future__ import annotations

import re
from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

import pytest

from devbench.constants import GATE_NAMES

REPO_ROOT = Path(__file__).parent.parent.parent

PROVENANCE_MAP_PATH = REPO_ROOT / "docs" / "issue-provenance.md"
CHANGELOG_PATH = REPO_ROOT / "CHANGELOG.md"
RELEASE_NOTES_PATH = REPO_ROOT / "docs" / "release-notes" / "candidate-release-integration-reality-gates.md"

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

# The five follow-ups spec Section 15 deferred filing until E11. E11-F1-S1-T3
# filed each directly as a `caylent-solutions/devbench` issue (`#356`-`#360`)
# and every one now has its own dedicated row in the map carrying that real
# number (see the Devbench Issues column and the `## Follow-up issues`
# subsection). Wording matches `docs/issue-provenance.md`'s row labels
# verbatim.
SECTION_15_DEFERRED_FOLLOWUPS: tuple[str, ...] = (
    "assert-tests-pass.sh fail-open rework",
    "guard-git-stage rule-1 cwd/-C quirks",
    "real-browser layout machine-verification design",
    "build-time generation of rubric bodies",
    "auto-registry fan-in tuning telemetry",
)

# The map's Devbench Issues column, unioned with its Source PR column, is the
# full set of `caylent-solutions/devbench#<N>` numbers this campaign's own
# rows account for (AC-TEST-002). A handful of pre-campaign citations in this
# repo predate the map and are not any row's number; each is named here with
# its provenance so the allowlist stays auditable rather than a bare set of
# digits (measured directly against `discover_campaign_files()`'s output,
# see `TestClosingKeywordCountInvariant`'s docstring paragraph above).
_PRE_CAMPAIGN_DEVBENCH_ISSUE_ALLOWLIST: frozenset[int] = frozenset(
    {
        198,  # the documented haiku config-load ban, cited repo-wide
        233,  # 'fable' model alias, cited once in tests/test_constants.py
    }
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
# detector). `test_config_schema_gate_provenance.py` carries the same hazard
# for one synthetic devbench-repo citation (`caylent-solutions/devbench#900`,
# a fixture number that is not, and must never become, a real map row) --
# see `TestClosingKeywordCountInvariant`'s docstring paragraph above.
_SELF_EXCLUDED_RELATIVE_PATHS: frozenset[str] = frozenset(
    {
        "docs/issue-provenance.md",
        "tests/test_docs/test_issue_provenance.py",
        "tests/test_docs/test_config_schema_gate_provenance.py",
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

# Fully-qualified `caylent-solutions/devbench`-repo citation, e.g.
# "caylent-solutions/devbench#356". AC-TEST-002: `extract_issue_tokens` (the
# internal-backlog campaign-file walker) deliberately never matches this
# form -- resolving it against the map's internal-backlog column would be a
# type error (different repo, different numbering range), not a
# resolvability check. `extract_devbench_repo_issue_tokens` below is the
# purpose-built extractor for this form; it is wired into its own real-file
# walk (`TestResolvability.
# test_no_unresolvable_devbench_repo_citations_in_campaign_touched_files`)
# resolved against the map's Devbench Issues column unioned with its Source
# PR column, plus `_PRE_CAMPAIGN_DEVBENCH_ISSUE_ALLOWLIST` for the small,
# named set of pre-campaign citations measured to fall outside both columns.
# Measured directly against `discover_campaign_files()`'s output: 43 total
# qualified citations of this form, not "hundreds".
_DEVBENCH_REPO_ISSUE_RE = re.compile(r"caylent-solutions/devbench#(\d+)")

_SEPARATOR_CELL_RE = re.compile(r"^:?-+:?$")
_LEADING_SECTION_NUMBER_RE = re.compile(r"^(\d+(?:\.\d+)*)")
_CLOSES_HEADING = "### Closes"

# GitHub's full closing-keyword vocabulary (case-insensitive): "close(s/d)",
# "fix(es/ed)", "resolve(s/d)" each auto-close a same-repo issue when merged.
# https://docs.github.com/en/issues/tracking-your-work-with-issues/using-issues/linking-a-pull-request-to-an-issue
_CLOSING_KEYWORDS: tuple[str, ...] = (
    "close",
    "closes",
    "closed",
    "fix",
    "fixes",
    "fixed",
    "resolve",
    "resolves",
    "resolved",
)
_CLOSING_KEYWORD_LINE_RE = re.compile(
    r"^(?:" + "|".join(_CLOSING_KEYWORDS) + r")\s+(\S+)$",
    re.IGNORECASE,
)


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


def _extract_tokens(text: str, *patterns: re.Pattern[str]) -> list[tuple[int, str]]:
    """Return `(line_no, '#<digits>')` for every match of any of `patterns` in
    `text`, walked once per line in `patterns` order (DRY: the single loop
    body shared by `extract_issue_tokens` and
    `extract_devbench_repo_issue_tokens`, which differ only in which
    compiled pattern(s) they feed in)."""
    found: list[tuple[int, str]] = []
    for line_no, line in enumerate(text.splitlines(), start=1):
        for pattern in patterns:
            for match in pattern.finditer(line):
                found.append((line_no, f"#{match.group(1)}"))
    return found


def extract_issue_tokens(text: str) -> list[tuple[int, str]]:
    """Return `(line_no, '#<digits>')` for every internal-backlog-style issue
    token in `text`: both the fully-qualified `devbench-internal-backlog#<N>`
    form and the bare zero-padded `#0[1-8]` fabricated-citation form."""
    return _extract_tokens(text, _QUALIFIED_ISSUE_RE, _FABRICATED_ISSUE_RE)


def resolves(token: str, valid_issue_numbers: frozenset[int]) -> bool:
    """A token resolves when its numeric value is one of `valid_issue_numbers`
    (DRY: shared by the internal-backlog and devbench-repo resolvability
    checks below, which differ only in which map column(s) the caller feeds
    in). Zero-padded tokens (the fabricated form) never parse to a number
    that is also zero-padded in the map, but `int()` normalises both sides,
    so resolution is judged on value, and the fabricated placeholders
    (`01`-`08`) are never in the map's 10-17 range regardless."""
    return int(token.lstrip("#")) in valid_issue_numbers


def extract_devbench_repo_issue_tokens(text: str) -> list[tuple[int, str]]:
    """Return `(line_no, '#<digits>')` for every fully-qualified
    `caylent-solutions/devbench#<N>` citation in `text` (AC-TEST-002); see
    `_DEVBENCH_REPO_ISSUE_RE`'s comment for how the result is resolved."""
    return _extract_tokens(text, _DEVBENCH_REPO_ISSUE_RE)


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
    paths: list[Path],
    valid_issue_numbers: frozenset[int],
    *,
    token_extractor: Callable[[str], list[tuple[int, str]]] = extract_issue_tokens,
    allowlist: frozenset[int] = frozenset(),
) -> list[UnresolvedCitation]:
    """Walk `paths` and return one `UnresolvedCitation` per issue token
    `token_extractor` finds that does not resolve against
    `valid_issue_numbers` (AC-E2-F7-S1-T2-2). Defaults to the
    internal-backlog extractor; `token_extractor=extract_devbench_repo_issue_tokens`
    reuses this same walk-and-collect loop for the devbench-repo check
    (AC-TEST-002), with `allowlist` carrying any pre-campaign issue numbers
    that are known-good but outside `valid_issue_numbers` (DRY: one walker
    for both resolvability checks)."""
    findings: list[UnresolvedCitation] = []
    for path in paths:
        text = path.read_text(encoding="utf-8")
        for line_no, token in token_extractor(text):
            number = int(token.lstrip("#"))
            if number in allowlist:
                continue
            if not resolves(token, valid_issue_numbers):
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


def _valid_issue_numbers(rows: list[ProvenanceRow], column: str) -> frozenset[int]:
    """Every issue number found in `column` (a `ProvenanceRow` attribute name)
    across `rows` (DRY: shared by the internal-backlog, devbench-repo and
    source-PR column checks, which differ only in which attribute is
    scanned)."""
    numbers: set[int] = set()
    for row in rows:
        cell = getattr(row, column)
        for match in re.finditer(r"#(\d+)", cell):
            numbers.add(int(match.group(1)))
    return frozenset(numbers)


def _valid_internal_issue_numbers(rows: list[ProvenanceRow]) -> frozenset[int]:
    return _valid_issue_numbers(rows, "internal_issue")


def _valid_devbench_issue_numbers(rows: list[ProvenanceRow]) -> frozenset[int]:
    """Every issue number in the map's Devbench Issues column (AC-TEST-002),
    across all rows -- gate rows with a cross-referenced devbench issue, the
    harness-guard row, and the five Section 15 follow-up rows alike."""
    return _valid_issue_numbers(rows, "devbench_issues")


def _valid_source_pr_numbers(rows: list[ProvenanceRow]) -> frozenset[int]:
    """Every issue number in the map's Source PR column (AC-TEST-002): the
    eight source pull requests `#315`-`#322`, unioned with the Devbench
    Issues column by the caller to form the full set of devbench-repo
    numbers this campaign's rows account for."""
    return _valid_issue_numbers(rows, "source_pr")


def extract_closing_keyword_lines(text: str) -> list[str]:
    """Return the target of every recognised GitHub closing-keyword line
    (case-insensitive `close(s/d)`, `fix(es/ed)`, `resolve(s/d)`; see
    `_CLOSING_KEYWORD_LINE_RE`) found directly under the release notes'
    `### Closes` heading (AC-TEST-001), stopping at the next heading line.
    Anchored on the heading line itself (exact match, not a substring
    match), so a prose sentence that merely mentions "Closes" elsewhere
    never collides -- the same hazard `docs/issue-provenance.md`'s
    `## Follow-up issues` subsection was deliberately kept a plain bullet
    list to avoid for the provenance-table parser.

    Fails fast (`ValueError`, naming the line) on any non-empty line inside
    the block this cannot classify, rather than silently skipping it
    (AC-DOC-003: a closing-keyword line in a form this parser does not
    recognise, e.g. `Closes #356`, would still auto-close a deliberately-OPEN
    same-repo issue on GitHub while this invariant reported zero defects).
    Also fails fast on a code-fence marker inside the block: GitHub does not
    honour closing keywords inside a fenced code block, so a fence here would
    silently void the `#335`/`#336` auto-close guarantee this block exists to
    provide."""
    lines = text.splitlines()
    try:
        start = lines.index(_CLOSES_HEADING) + 1
    except ValueError as exc:
        raise ValueError(f"heading {_CLOSES_HEADING!r} not found in release notes") from exc
    targets: list[str] = []
    for line in lines[start:]:
        stripped = line.strip()
        if stripped.startswith("#"):
            break
        if not stripped:
            continue
        if stripped.startswith("```"):
            raise ValueError(
                f"code fence found under {_CLOSES_HEADING!r} heading: {stripped!r} -- GitHub does not "
                "honour closing keywords inside a fenced code block"
            )
        match = _CLOSING_KEYWORD_LINE_RE.match(stripped)
        if not match:
            raise ValueError(
                f"unrecognised line under {_CLOSES_HEADING!r} heading: {stripped!r} -- expected a GitHub "
                f"closing-keyword line (one of {', '.join(_CLOSING_KEYWORDS)}, case-insensitive) followed "
                "by a single target"
            )
        targets.append(match.group(1))
    return targets


def compute_expected_closing_keyword_targets(rows: list[ProvenanceRow]) -> list[str]:
    """Return the exact `Fixes` targets the release notes' `### Closes` block
    must carry (AC-TEST-001): one fully-qualified
    `caylent-solutions/devbench-internal-backlog#<n>` target per gate row,
    plus one bare `#<n>` target per landed devbench-repo issue this campaign
    closed by hand (`LANDED_DEVBENCH_ISSUES`). The five deliberately-OPEN
    Section 15 follow-up rows are excluded by construction: they are neither
    gate rows nor in `LANDED_DEVBENCH_ISSUES`."""
    targets: list[str] = [row.internal_issue for row in rows if row.gate in GATE_NAMES]
    targets.extend(f"#{issue.rsplit('#', 1)[-1]}" for issue in LANDED_DEVBENCH_ISSUES)
    return targets


def _section_15_issue_numbers(rows: list[ProvenanceRow]) -> frozenset[str]:
    """Every issue number (as a string) filed under spec Section 15,
    derived from parsed map rows rather than transcribed (AC-TEST-001;
    AC-DOC-003), so a sixth follow-up filed under Section 15 and added to
    the map is automatically covered by `find_closing_keyword_count_defects`'s
    deliberately-OPEN guard with no second registration step."""
    numbers: set[str] = set()
    for row in rows:
        if extract_section_number(row.spec_section) != "15":
            continue
        numbers.update(match.group(1) for match in re.finditer(r"#(\d+)", row.devbench_issues))
    return frozenset(numbers)


def find_closing_keyword_count_defects(
    actual_targets: list[str], expected_targets: list[str], section_15_issue_numbers: frozenset[str]
) -> list[str]:
    """Return one human-readable defect string per way `actual_targets` (the
    `### Closes` block's parsed closing-keyword targets) diverges from
    `expected_targets` (AC-TEST-001): a missing mapped-issue line, a
    duplicated or unexpected line, or a line present for a deliberately-OPEN
    Section 15 follow-up issue (`section_15_issue_numbers`; AC-DOC-003)."""
    defects: list[str] = []
    actual_counts = Counter(actual_targets)
    expected_counts = Counter(expected_targets)
    for target, expected_count in expected_counts.items():
        found_count = actual_counts.get(target, 0)
        if found_count < expected_count:
            defects.append(
                f"missing Fixes line for mapped issue {target!r} (found {found_count}, expected {expected_count})"
            )
        elif found_count > expected_count:
            defects.append(
                f"duplicate Fixes line for mapped issue {target!r} (found {found_count}, expected {expected_count})"
            )
    for target in actual_counts:
        if target not in expected_counts:
            defects.append(f"unexpected Fixes line for {target!r}")
    for target in actual_targets:
        if target.rsplit("#", 1)[-1] in section_15_issue_numbers:
            defects.append(f"Fixes line present for deliberately-OPEN Section 15 issue {target!r}")
    return defects


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

    def test_devbench_repo_qualified_citation_resolves_against_devbench_issues_column(self) -> None:
        """AC-TEST-002 positive control: a genuine `caylent-solutions/devbench#<N>`
        citation of a Section 15 follow-up resolves against the map's Devbench
        Issues column, using the dedicated extractor and the shared `resolves`
        function -- not `extract_issue_tokens`, which stays scoped to
        internal-backlog citations only (see `_DEVBENCH_REPO_ISSUE_RE`)."""
        valid_devbench_numbers = _valid_devbench_issue_numbers(_map_rows())
        text = "Filed as caylent-solutions/devbench#356 (assert-tests-pass.sh fail-open rework).\n"

        tokens = extract_devbench_repo_issue_tokens(text)

        assert tokens == [(1, "#356")]
        assert resolves("#356", valid_devbench_numbers) is True

    def test_seeded_fabricated_devbench_repo_citation_is_not_resolved(self) -> None:
        """AC-TEST-002 negative control (isolated units, not yet wired to a
        real file -- see `test_no_unresolvable_devbench_repo_citations_in_campaign_touched_files`
        and `test_seeded_fabricated_devbench_repo_citation_in_a_real_campaign_file_is_detected`
        below for the wired proof): the dedicated extractor never returns an
        empty list for a qualified citation, and `resolves` correctly rejects
        a number that is not in the map's Devbench Issues column."""
        valid_devbench_numbers = _valid_devbench_issue_numbers(_map_rows())
        text = "See caylent-solutions/devbench#999999 for the (fabricated) origin.\n"

        tokens = extract_devbench_repo_issue_tokens(text)

        assert tokens == [(1, "#999999")]
        assert resolves("#999999", valid_devbench_numbers) is False

    def test_no_unresolvable_devbench_repo_citations_in_campaign_touched_files(self) -> None:
        """AC-TEST-002, wired: walks the same real `discover_campaign_files()`
        surface `test_no_unresolvable_citations_in_campaign_touched_files`
        walks above, this time resolving every `caylent-solutions/devbench#<N>`
        citation against the map's Devbench Issues column unioned with its
        Source PR column, minus the small, named
        `_PRE_CAMPAIGN_DEVBENCH_ISSUE_ALLOWLIST`. Before this task,
        `extract_devbench_repo_issue_tokens` was never called from this real
        walk, so a fabricated citation of a Section 15 follow-up number (or
        any other devbench-repo number) written into any real campaign file
        produced zero findings -- see the sibling test below for the seeded
        proof that this is no longer true."""
        rows = _map_rows()
        valid_numbers = _valid_devbench_issue_numbers(rows) | _valid_source_pr_numbers(rows)
        paths = discover_campaign_files()
        assert paths, "expected at least one campaign-touched file to be discovered"

        findings = find_unresolvable_citations(
            paths,
            valid_numbers,
            token_extractor=extract_devbench_repo_issue_tokens,
            allowlist=_PRE_CAMPAIGN_DEVBENCH_ISSUE_ALLOWLIST,
        )

        assert findings == [], (
            "campaign-touched files must carry zero unresolvable devbench-repo issue citations "
            f"(AC-TEST-002); found: {findings}"
        )

    def test_seeded_fabricated_devbench_repo_citation_in_a_real_campaign_file_is_detected(self, tmp_path: Path) -> None:
        """AC-TEST-002: proves a fabricated `caylent-solutions/devbench#<N>`
        number written into a real campaign file is now caught by the wired
        walk above, not merely by a literal the test itself authored. Mirrors
        `test_seeded_fabricated_citation_is_detected`'s shape for the
        internal-backlog check."""
        seeded = tmp_path / "synthetic-fabricated-devbench-citation.md"
        seeded.write_text(
            "# Synthetic\n\nFabricated citation caylent-solutions/devbench#999999.\n",
            encoding="utf-8",
        )
        rows = _map_rows()
        valid_numbers = _valid_devbench_issue_numbers(rows) | _valid_source_pr_numbers(rows)

        findings = find_unresolvable_citations(
            [seeded],
            valid_numbers,
            token_extractor=extract_devbench_repo_issue_tokens,
            allowlist=_PRE_CAMPAIGN_DEVBENCH_ISSUE_ALLOWLIST,
        )

        assert findings == [UnresolvedCitation(path=seeded, line_no=3, token="#999999")]


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

    def test_every_section_15_followup_has_a_filed_row(self) -> None:
        """AC-TEST-003: renamed from
        `test_every_section_15_followup_has_a_placeholder_row` now that all
        five rows carry a real filed issue number (`#356`-`#360`) rather than
        a `TBD` placeholder; the assertion behaviour (each follow-up's label
        appears in a non-gate row) is unchanged."""
        rows = _map_rows()
        non_gate_rows_text = " ".join(row.gate for row in rows if row.gate not in GATE_NAMES)
        for followup in SECTION_15_DEFERRED_FOLLOWUPS:
            assert followup in non_gate_rows_text, f"expected a row naming {followup!r}"


@pytest.mark.unit
class TestClosingKeywordCountInvariant:
    """AC-TEST-001 (spec 4.13, 5.6; AC-24): the release notes'
    `### Closes` block carries exactly one `Fixes` line per numbered mapped
    issue this campaign closes, and never a line for a deliberately-OPEN
    Section 15 follow-up (AC-DOC-003) -- test_review has requested this pin
    on three consecutive units; the release notes' preamble going stale in
    E11-F1-S1-T2 (fixed by this task, AC-DOC-001/AC-DOC-002) was invisible to
    CI because `TestDiscoverCampaignFiles`'s walk never reaches
    `docs/release-notes/` (see `_CAMPAIGN_GLOB_ROOTS`), so nothing previously
    read this file at all."""

    def test_release_notes_carries_exactly_one_fixes_line_per_mapped_issue(self) -> None:
        rows = _map_rows()
        expected = compute_expected_closing_keyword_targets(rows)
        actual = extract_closing_keyword_lines(RELEASE_NOTES_PATH.read_text(encoding="utf-8"))

        assert len(actual) == len(expected), (
            f"expected {len(expected)} closing-keyword lines under '### Closes' (one per map-derived "
            f"mapped issue), got {len(actual)}: {actual}"
        )
        defects = find_closing_keyword_count_defects(actual, expected, _section_15_issue_numbers(rows))
        assert defects == [], f"release notes Fixes-line count invariant violated: {defects}"

    def test_seeded_extra_fixes_line_for_open_followup_is_detected(self) -> None:
        """Demonstrates the invariant fails against a mutated fixture: a
        `Fixes #356` line added for a deliberately-OPEN Section 15 follow-up
        (AC-TEST-001's "fail if a line is added" case, and the exact hazard
        AC-DOC-003 forbids -- a same-repo `Fixes #356` line would auto-close
        it on merge)."""
        rows = _map_rows()
        expected = compute_expected_closing_keyword_targets(rows)
        actual = extract_closing_keyword_lines(RELEASE_NOTES_PATH.read_text(encoding="utf-8"))
        mutated = [*actual, "#356"]

        defects = find_closing_keyword_count_defects(mutated, expected, _section_15_issue_numbers(rows))

        assert defects == [
            "unexpected Fixes line for '#356'",
            "Fixes line present for deliberately-OPEN Section 15 issue '#356'",
        ]

    def test_seeded_missing_fixes_line_for_mapped_issue_is_detected(self) -> None:
        """Demonstrates the invariant fails against a mutated fixture: a
        mapped issue's `Fixes` line is dropped (AC-TEST-001's "fail if a line
        is removed, or if a mapped issue loses its line" case)."""
        rows = _map_rows()
        expected = compute_expected_closing_keyword_targets(rows)
        actual = extract_closing_keyword_lines(RELEASE_NOTES_PATH.read_text(encoding="utf-8"))
        dropped_target = "caylent-solutions/devbench-internal-backlog#10"
        assert dropped_target in actual, f"fixture assumption violated: {dropped_target!r} not found in {actual}"
        mutated = [target for target in actual if target != dropped_target]

        defects = find_closing_keyword_count_defects(mutated, expected, _section_15_issue_numbers(rows))

        assert defects == [f"missing Fixes line for mapped issue {dropped_target!r} (found 0, expected 1)"]

    def test_section_15_issue_numbers_derived_from_the_map_not_transcribed(self) -> None:
        """Proves `_section_15_issue_numbers` tracks the map rather than a
        transcription: a synthetic map row filed under Section 15 with a
        number not in the transcribed literal `{356..360}` is still picked
        up, and a non-Section-15 row's devbench issue is excluded."""
        synthetic = (
            "| Gate | Internal Issue | Source PR | Devbench Issues | Spec Section |\n"
            "|------|-----------------|-----------|------------------|--------------|\n"
            "| some deferred item | none | none | `caylent-solutions/devbench#999` | `15` |\n"
            "| `reachability` | `caylent-solutions/devbench-internal-backlog#10` "
            "| `caylent-solutions/devbench#315` | none | `4.4` |\n"
        )
        rows = parse_provenance_map(synthetic)

        assert _section_15_issue_numbers(rows) == frozenset({"999"})

    def test_extract_closing_keyword_lines_anchors_on_the_heading_not_a_substring(self) -> None:
        """A prose mention of the word "Closes" elsewhere in the document must
        never be mistaken for the `### Closes` heading -- the same hazard
        `docs/issue-provenance.md`'s `## Follow-up issues` subsection is kept
        a plain bullet list to avoid for the provenance-table parser."""
        text = (
            "Some prose that Closes over a topic without being the heading.\n\n"
            "### Closes\n\n"
            "Fixes #1\n"
            "Fixes #2\n\n"
            "## Next section\n\n"
            "Fixes #3\n"
        )
        assert extract_closing_keyword_lines(text) == ["#1", "#2"]

    def test_extract_closing_keyword_lines_raises_when_heading_missing(self) -> None:
        with pytest.raises(ValueError, match=re.escape(f"heading {_CLOSES_HEADING!r} not found")):
            extract_closing_keyword_lines("no closing heading here\n")

    @pytest.mark.parametrize(
        "keyword", ["Close", "closes", "Closed", "Fix", "fixes", "Fixed", "Resolve", "resolves", "RESOLVED"]
    )
    def test_extract_closing_keyword_lines_matches_full_github_keyword_set_case_insensitively(
        self, keyword: str
    ) -> None:
        """AC-DOC-003 fail-open fix: GitHub honours the full close/fix/resolve
        keyword family case-insensitively, not only an exact-case `Fixes`
        line. Probed directly by test_review: `Closes #356`, `Resolves #356`
        and `fixes #356` all failed to match the old `^Fixes\\s+(\\S+)$`-only
        pattern."""
        text = f"### Closes\n\n{keyword} #42\n"
        assert extract_closing_keyword_lines(text) == ["#42"]

    def test_extract_closing_keyword_lines_raises_on_unrecognised_line(self) -> None:
        """AC-DOC-003 fail-open fix: a non-empty line under the heading that
        is not a recognised closing-keyword line must raise, naming the line,
        rather than being silently skipped -- the old behaviour let an
        unrecognised form (e.g. a typo, or a keyword GitHub does not
        recognise) hide inside the block with zero defects reported."""
        text = "### Closes\n\nSee also #42\n"
        with pytest.raises(ValueError, match=re.escape("unrecognised line under '### Closes' heading")):
            extract_closing_keyword_lines(text)

    def test_extract_closing_keyword_lines_raises_on_fenced_block(self) -> None:
        """AC-DOC-003 fail-open fix: GitHub does not honour closing keywords
        inside a fenced code block, so wrapping the ten `Fixes` lines in
        triple backticks would silently void the `#335`/`#336` auto-close
        guarantee. test_review proved the old parser did not detect this and
        the count invariant passed against a fenced fixture; this must now
        raise instead."""
        text = "### Closes\n\n```\nFixes #335\nFixes #336\n```\n"
        with pytest.raises(ValueError, match=re.escape("code fence found under '### Closes' heading")):
            extract_closing_keyword_lines(text)

    def test_real_release_notes_closes_block_contains_no_code_fence(self) -> None:
        """Direct regression pin for the advisory test_review raised: an awk-
        equivalent scan confirming the real file's `### Closes` block is
        unfenced (AC-DOC-003 is load-bearing on this)."""
        text = RELEASE_NOTES_PATH.read_text(encoding="utf-8")
        lines = text.splitlines()
        start = lines.index(_CLOSES_HEADING) + 1
        block: list[str] = []
        for line in lines[start:]:
            if line.strip().startswith("#"):
                break
            block.append(line)
        assert not any(line.strip().startswith("```") for line in block), (
            "release notes '### Closes' block must never contain a code fence"
        )


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
