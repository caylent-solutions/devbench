"""Structural pins for CHANGELOG.md's FR-D2 instances entry and the
devbench-observability-hardening citations on all four observability defect
entries (D1-D4), delivered by E9-F2-S2-T1 (spec Section 4 FR-5, FR-9;
AC-13, AC-14, AC-22).

Two things must remain true:

1. Under the newest ``## [...]`` -> ``### Fixed`` block, an entry for
   the FR-D2 instances fix exists -- daemons outside ``$HOME`` are
   discoverable because ``_resolve_search_roots`` now also joins
   ``DEVBENCH_WORKSPACE_ROOT`` into the default search roots
   (``instances.py:140-168``, delivered by E7-F2-S1-T1) (AC-13).
2. Each of the four observability defect entries (D1 scope.json #270, D2
   instances, D3 daemon recency, D4 pytest isolation #292) cites
   ``spec/devbench-observability-hardening.md`` (AC-14), satisfying OAC-8's
   "cites #270 and this spec" requirement.

Source: E9-F2-S2-T1 (CHANGELOG.md). Spec Section 4 FR-5, FR-9; AC-13, AC-14, AC-22.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).parent.parent.parent
CHANGELOG_PATH = REPO_ROOT / "CHANGELOG.md"

_HEADING_PREFIX_RE = re.compile(r"^(#{1,6}) ")
_VERSION_HEADING_RE = re.compile(r"^## \[[^\]]+\].*$", re.MULTILINE)
_HEADING_LINE_RE = re.compile(r"^(#{1,6}) ", re.MULTILINE)
_WHITESPACE_RE = re.compile(r"\s+")

_OBS_SPEC_CITATION = "devbench-observability-hardening"

_D1_START_PHRASE = "An unscoped session wrote a `scope.json` its own readers reject"
_D2_START_PHRASE = "`devbench instances` still reported no running orchestrator for a daemon"
_D3_START_PHRASE = "The orchestrator-alive banner reported ALIVE with no orchestrator"
_D4_START_PHRASE = "The test suite wrote into the live workspace and orchestrator log"


def _read_doc() -> str:
    return CHANGELOG_PATH.read_text(encoding="utf-8")


def _extract_section(text: str, heading: str) -> str:
    """Return the content of the section starting at ``heading``.

    ``heading`` must be the full markdown heading line, including its leading
    ``#`` markers (e.g. ``"## [Unreleased] -- v-next"``), not a bare title.
    The heading's level is derived from that prefix, and the returned section
    is bounded at the next heading whose level is the same as or higher
    (fewer ``#`` characters) than the starting heading. Passing a bare title
    (no ``#`` prefix) raises ``ValueError`` immediately rather than silently
    deriving a bogus level.
    """
    match = _HEADING_PREFIX_RE.match(heading)
    if match is None:
        raise ValueError(
            f"heading must be a full markdown heading starting with '#' markers "
            f"(e.g. '### {heading}'), got: {heading!r}"
        )
    level = len(match.group(1))
    idx = text.find(heading)
    if idx == -1:
        return ""
    section_text = text[idx:]
    for candidate in _HEADING_LINE_RE.finditer(section_text):
        if candidate.start() == 0:
            continue
        if len(candidate.group(1)) <= level:
            return section_text[: candidate.start()]
    return section_text


def _current_release_section(text: str) -> str:
    """Return the newest ``## [...]`` version section of the CHANGELOG.

    That heading is ``## [Unreleased] -- v-next`` between releases and
    ``## [<version>] -- <date>`` on a release commit, because cutting a release
    renames the heading in place rather than adding a new one.

    Resolving the heading is what keeps the entry pins below satisfiable.
    Hard-coding the literal ``"## [Unreleased] -- v-next"`` made them
    structurally unsatisfiable on any release commit: the release rename
    removed the very heading they required, so every release broke this
    module. The invariant these checks actually encode is "the entry lives in
    the newest section", which survives the rename.
    """
    match = _VERSION_HEADING_RE.search(text)
    if match is None:
        return ""
    return _extract_section(text, match.group(0))


def _section_containing(text: str, phrase: str) -> str:
    """Return the ``## [...]`` version section that contains *phrase*.

    Pinning the FR-D2 entry to the *newest* section was still over-specified:
    cutting a release renames the section the entry sits in, and opening a new
    ``## [Unreleased] -- v-next`` above it moves the entry out of "newest"
    again. The durable invariant is that the entry is recorded under a
    ``### Fixed`` block somewhere in the changelog, not that it sits in
    whichever section happens to be first. Resolve the owning section instead.
    """
    match = _VERSION_HEADING_RE.search(text)
    while match is not None:
        section = _extract_section(text, match.group(0))
        if phrase in section:
            return section
        nxt = _VERSION_HEADING_RE.search(text, match.end())
        match = nxt
    return ""


def _extract_bullet(text: str, start_phrase: str) -> str:
    """Return one CHANGELOG bullet's full text, from *start_phrase* to the
    next bullet or heading boundary.

    CHANGELOG.md's bullets are separated by a blank line before the next
    ``- **`` bullet marker; a bullet list itself is terminated by the next
    ``##``-level heading. Whichever boundary comes first (if any) ends the
    returned slice.
    """
    start = text.find(start_phrase)
    assert start != -1, f"CHANGELOG.md must contain a bullet starting with {start_phrase!r}."
    next_bullet = text.find("\n\n- **", start)
    next_heading = text.find("\n## ", start)
    boundaries = [pos for pos in (next_bullet, next_heading) if pos != -1]
    end = min(boundaries) if boundaries else len(text)
    return text[start:end]


def _normalize_whitespace(text: str) -> str:
    """Collapse all runs of whitespace (including newlines from markdown
    hard-wrapping) to a single space, so a phrase assertion can match text
    that spans a hard line-wrap without being sensitive to exactly where the
    wrap falls."""
    return _WHITESPACE_RE.sub(" ", text)


@pytest.mark.unit
class TestCurrentSectionFixedSubsectionExists:
    """Sanity precondition for the FR-D2 entry check below: the newest
    ``## [...]`` -> ``### Fixed`` nesting must exist, or that check would pass
    vacuously against an empty string."""

    def test_current_section_exists(self) -> None:
        text = _read_doc()
        section = _current_release_section(text)
        assert section, "CHANGELOG.md must contain a '## [<version>]' or '## [Unreleased]' section."

    def test_current_section_fixed_subsection_exists(self) -> None:
        text = _read_doc()
        owning = _section_containing(text, _D2_START_PHRASE)
        assert owning, f"No '## [...]' section contains {_D2_START_PHRASE!r}."
        fixed_section = _extract_section(owning, "### Fixed")
        assert fixed_section, "The section carrying the FR-D2 entry must contain a '### Fixed' subsection."


@pytest.mark.unit
class TestFrD2InstancesEntry:
    """AC-13 / AC-E9-F3-S2-T1-3: the FR-D2 instances Fixed entry (spec Section 4
    FR-5(a)) lives under the newest ``## [...]`` section's '### Fixed'."""

    def test_entry_exists_under_current_fixed(self) -> None:
        text = _read_doc()
        owning = _section_containing(text, _D2_START_PHRASE)
        assert owning, f"No '## [...]' section contains {_D2_START_PHRASE!r}."
        fixed_section = _extract_section(owning, "### Fixed")
        assert fixed_section, "The section carrying the FR-D2 entry must contain a '### Fixed' subsection."
        assert _D2_START_PHRASE in fixed_section, (
            "The FR-D2 instances entry must sit under a '### Fixed' subsection of its own "
            f"'## [...]' section, starting {_D2_START_PHRASE!r} (spec Section 4 FR-5(a), AC-13)."
        )

    def test_entry_names_the_workspace_root_join_and_source_lines(self) -> None:
        text = _read_doc()
        bullet = _extract_bullet(text, _D2_START_PHRASE)
        assert "DEVBENCH_WORKSPACE_ROOT" in bullet, (
            "The FR-D2 instances entry must name DEVBENCH_WORKSPACE_ROOT as the newly "
            "joined default search root (spec Section 4 FR-5(a))."
        )
        assert "instances.py:140-168" in bullet, (
            "The FR-D2 instances entry must cite the exact instances.py line range the "
            "fix touched (spec Section 4 FR-5(a))."
        )
        assert "E7-F2-S1-T1" in bullet, (
            "The FR-D2 instances entry must cite the delivering unit E7-F2-S1-T1 (spec Section 4 FR-5(a))."
        )

    def test_entry_cites_fr_d2_and_defect_d2(self) -> None:
        text = _read_doc()
        bullet = _normalize_whitespace(_extract_bullet(text, _D2_START_PHRASE))
        assert "FR-D2" in bullet, "The FR-D2 instances entry must cite 'FR-D2'."
        assert "defect D2" in bullet, "The FR-D2 instances entry must cite 'defect D2' verbatim."


@pytest.mark.unit
class TestObsSpecCitationOnEveryDefectEntry:
    """AC-14 / AC-E9-F3-S2-T1-3 (OAC-8): all four observability defect entries
    (D1-D4) cite devbench-observability-hardening."""

    @pytest.mark.parametrize(
        "defect_id,start_phrase,fr_token",
        [
            ("D1", _D1_START_PHRASE, "FR-D1"),
            ("D2", _D2_START_PHRASE, "FR-D2"),
            ("D3", _D3_START_PHRASE, "FR-D3"),
            ("D4", _D4_START_PHRASE, "FR-D4"),
        ],
        ids=["D1-scope-json", "D2-instances", "D3-daemon-recency", "D4-pytest-isolation"],
    )
    def test_defect_entry_cites_obs_spec(self, defect_id: str, start_phrase: str, fr_token: str) -> None:
        text = _read_doc()
        bullet = _extract_bullet(text, start_phrase)
        assert _OBS_SPEC_CITATION in bullet, (
            f"The {defect_id} entry (starting {start_phrase!r}) must cite "
            f"'{_OBS_SPEC_CITATION}' (OAC-8, spec Section 4 FR-5)."
        )
        normalized_bullet = _normalize_whitespace(bullet)
        assert fr_token in normalized_bullet, f"The {defect_id} entry must cite '{fr_token}'."
        assert f"defect {defect_id}" in normalized_bullet, (
            f"The {defect_id} entry must cite 'defect {defect_id}' verbatim."
        )
