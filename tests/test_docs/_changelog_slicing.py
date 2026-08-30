"""Shared markdown section/bullet-slicing helpers for CHANGELOG.md docs pins.

Every ``tests/test_docs/test_changelog_*.py`` module pins one or more
CHANGELOG.md bullets to a specific campaign (a delivering work unit, a spec
citation, a set of operator-visible facts). The slicing mechanics those pins
need -- find a ``## [...]`` version section, find the section that contains a
given phrase, find one bullet's full text starting at a phrase, and collapse
markdown hard-wrap whitespace so a multi-word phrase assertion is not
sensitive to exactly where a line wraps -- are generic markdown structure
utilities with zero campaign-specific content. This module holds that shared
mechanism so campaign pin modules only need to define their own start
phrases and assertions.

This module is intentionally NOT a test module (no ``test_`` prefix, no
``Test*`` classes) so pytest's default ``test_*.py`` collection pattern never
picks it up; it is imported by test modules under ``tests/test_docs/``.
"""

from __future__ import annotations

import re
from pathlib import Path

REPO_ROOT = Path(__file__).parent.parent.parent
CHANGELOG_PATH = REPO_ROOT / "CHANGELOG.md"

_HEADING_PREFIX_RE = re.compile(r"^(#{1,6}) ")
_VERSION_HEADING_RE = re.compile(r"^## \[[^\]]+\].*$", re.MULTILINE)
_HEADING_LINE_RE = re.compile(r"^(#{1,6}) ", re.MULTILINE)
_WHITESPACE_RE = re.compile(r"\s+")


def read_doc() -> str:
    """Return CHANGELOG.md's full text."""
    return CHANGELOG_PATH.read_text(encoding="utf-8")


def extract_section(text: str, heading: str) -> str:
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


def section_containing(text: str, phrase: str) -> str:
    """Return the ``## [...]`` version section that contains *phrase*.

    Resolving the owning section by content (rather than assuming the entry
    always sits in the newest ``## [Unreleased] -- v-next`` section) survives
    a release cut renaming or relocating that heading.
    """
    match = _VERSION_HEADING_RE.search(text)
    while match is not None:
        section = extract_section(text, match.group(0))
        if phrase in section:
            return section
        nxt = _VERSION_HEADING_RE.search(text, match.end())
        match = nxt
    return ""


def extract_bullet(text: str, start_phrase: str) -> str:
    """Return one CHANGELOG bullet's full text, from *start_phrase* to the
    next bullet or heading boundary.

    CHANGELOG.md's bullets are separated by a blank line before the next
    ``- **`` bullet marker; a bullet list itself is terminated by the next
    ``##``-level heading. Whichever boundary comes first (if any) ends the
    returned slice. Raises ``ValueError`` (rather than a bare ``assert``,
    which ``python -O`` strips) when *start_phrase* is not present, so a
    missing bullet fails loudly instead of silently returning a truncated
    tail slice.
    """
    start = text.find(start_phrase)
    if start == -1:
        raise ValueError(f"CHANGELOG.md must contain a bullet starting with {start_phrase!r}.")
    next_bullet = text.find("\n\n- **", start)
    next_heading = text.find("\n## ", start)
    boundaries = [pos for pos in (next_bullet, next_heading) if pos != -1]
    end = min(boundaries) if boundaries else len(text)
    return text[start:end]


def normalize_whitespace(text: str) -> str:
    """Collapse all runs of whitespace (including newlines from markdown
    hard-wrapping) to a single space, so a phrase assertion can match text
    that spans a hard line-wrap without being sensitive to exactly where the
    wrap falls."""
    return _WHITESPACE_RE.sub(" ", text)
