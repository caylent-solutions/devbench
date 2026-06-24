"""Resolved-decisions ledger for the ``create-spec`` adversarial hardening loop.

Issue #264 E12-F2-S2. The ``create-spec`` skill maintains a companion
artifact at ``spec/<name>-resolved-decisions.md`` during the adversarial
hardening loop. Each confirmed cross-section or cross-file contradiction
is resolved once and recorded as a ``D<N>`` entry. Later rounds defer to
the recorded resolution verbatim rather than re-litigating it.

The ledger is emitted alongside the spec file and consumed by
``spec-to-backlog`` as the contradiction tie-breaker (wired in E12-F3-S2).

File format
-----------
The ledger is a Markdown file with a top-level heading followed by one
second-level section per decision::

    # Resolved Decisions

    ## D1

    **Contradiction:** <description of the cross-section or cross-file conflict>

    **Resolution:** <the chosen resolution, stated verbatim>

    **Rationale:** <why this resolution was preferred>

    ## D2

    ...

Public API
----------
- ``DecisionEntry`` -- dataclass for the structured fields of a single decision.
- ``LedgerEntry`` -- dataclass carrying the serialised raw Markdown and index.
- ``DuplicateResolutionError`` -- raised when an already-recorded contradiction
  is re-submitted with a different resolution.
- ``next_index`` -- compute the next sequential ``D<N>`` integer.
- ``read_ledger`` -- parse all ``D<N>`` entries from the ledger file.
- ``append_decision`` -- atomically append a new ``D<N>`` entry.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from devbench.utils.io import atomic_write_text

__all__ = [
    "DecisionEntry",
    "DuplicateResolutionError",
    "LedgerEntry",
    "append_decision",
    "next_index",
    "read_ledger",
]


_LEDGER_HEADING = "# Resolved Decisions"
"""Top-level heading written at the start of every new ledger file."""

_ENTRY_HEADING_RE = re.compile(r"^## D(\d+)\s*$", re.MULTILINE)
"""Matches ``## D<N>`` section headings in the ledger Markdown."""


@dataclass(frozen=True)
class DecisionEntry:
    """Structured representation of a single decision to record.

    The ``index`` field is overwritten by ``append_decision`` with the
    next sequential value; callers SHOULD pass ``0`` as a placeholder.

    Attributes:
        index: Sequential ``D<N>`` integer. Set by the caller to 0 when
            submitting a new decision; set to the assigned index by
            ``append_decision`` in the returned ``LedgerEntry``.
        contradiction: Description of the cross-section or cross-file
            contradiction being resolved.
        resolution: The chosen resolution, stated verbatim. Later rounds
            MUST defer to this text without modification.
        rationale: Why this resolution was preferred over alternatives.
    """

    index: int
    contradiction: str
    resolution: str
    rationale: str


@dataclass(frozen=True)
class LedgerEntry:
    """A parsed entry as it appears in the ledger file.

    Attributes:
        index: The ``D<N>`` integer extracted from the section heading.
        raw: The full Markdown source of the ``## D<N>`` section,
            including the heading line and all body paragraphs up to
            (but not including) the next ``##`` heading or EOF.
    """

    index: int
    raw: str


class DuplicateResolutionError(ValueError):
    """Raised when an already-recorded contradiction is re-submitted.

    ``create-spec`` MUST defer to the existing resolution verbatim rather
    than appending a conflicting second entry.  Raising here lets the
    caller surface the existing ``D<N>`` reference so the operator can
    inspect the original resolution before deciding whether to amend it.

    Args:
        existing_index: The ``D<N>`` integer of the already-recorded entry.
        contradiction: The contradiction text that was found to be a duplicate.
    """

    def __init__(self, *, existing_index: int, contradiction: str) -> None:
        self.existing_index = existing_index
        self.contradiction = contradiction
        super().__init__(
            f"Contradiction already recorded as D{existing_index}; "
            f"defer to the existing resolution verbatim. "
            f"Contradiction text: {contradiction!r}"
        )


def _parse_entries(content: str) -> list[LedgerEntry]:
    """Parse ``## D<N>`` sections from *content* and return them sorted by index.

    Args:
        content: Full text of the ledger Markdown file.

    Returns:
        List of ``LedgerEntry`` objects in ascending index order.
        An empty list is returned when no ``## D<N>`` headings are found.
    """
    entries: list[LedgerEntry] = []
    parts = re.split(r"(?=^## D\d+\s*$)", content, flags=re.MULTILINE)
    for part in parts:
        match = _ENTRY_HEADING_RE.match(part.splitlines()[0].strip()) if part.strip() else None
        if match is None:
            continue
        index = int(match.group(1))
        entries.append(LedgerEntry(index=index, raw=part.rstrip("\n")))
    entries.sort(key=lambda e: e.index)
    return entries


def _format_entry(index: int, decision: DecisionEntry) -> str:
    """Render a single ``## D<N>`` Markdown section.

    Args:
        index: The assigned sequential index for this entry.
        decision: The structured decision fields to render.

    Returns:
        A Markdown string for the ``## D<N>`` section, ending with a
        trailing newline so concatenation produces well-formed Markdown.
    """
    return (
        f"## D{index}\n\n"
        f"**Contradiction:** {decision.contradiction}\n\n"
        f"**Resolution:** {decision.resolution}\n\n"
        f"**Rationale:** {decision.rationale}\n"
    )


def next_index(ledger_path: Path) -> int:
    """Compute the next sequential ``D<N>`` integer for *ledger_path*.

    Returns ``1`` when the ledger file does not exist or contains no
    ``## D<N>`` entries.  When entries exist, returns ``max(existing
    indices) + 1`` to guarantee strict monotonic growth even in the
    presence of gaps.

    Args:
        ledger_path: Absolute or workspace-relative path to the ledger
            file (e.g., ``spec/myproject-resolved-decisions.md``).

    Returns:
        The next free ``D<N>`` integer (always >= 1).
    """
    if not ledger_path.exists():
        return 1
    content = ledger_path.read_text(encoding="utf-8")
    matches = _ENTRY_HEADING_RE.findall(content)
    if not matches:
        return 1
    return max(int(m) for m in matches) + 1


def read_ledger(ledger_path: Path) -> list[LedgerEntry]:
    """Return all ``D<N>`` entries recorded in *ledger_path*, sorted by index.

    Args:
        ledger_path: Path to the ledger file.  When the file does not
            exist an empty list is returned (the ledger is considered
            empty on first use).

    Returns:
        A list of ``LedgerEntry`` objects in ascending index order.
        Returns an empty list when the file is absent or contains no
        ``## D<N>`` headings.
    """
    if not ledger_path.exists():
        return []
    content = ledger_path.read_text(encoding="utf-8")
    return _parse_entries(content)


def append_decision(ledger_path: Path, decision: DecisionEntry) -> LedgerEntry:
    """Append a new ``D<N>`` entry to the ledger at *ledger_path* atomically.

    Deduplication contract
    ~~~~~~~~~~~~~~~~~~~~~~
    Before writing, every existing entry's ``**Contradiction:**`` line is
    compared to ``decision.contradiction`` (exact string match, leading/
    trailing whitespace stripped on both sides).  When a match is found,
    ``DuplicateResolutionError`` is raised immediately with the existing
    ``D<N>`` index so the caller can surface the recorded resolution
    without any file mutation.

    Atomicity contract
    ~~~~~~~~~~~~~~~~~~
    The write uses the ``atomic_write_text`` helper from
    ``devbench.utils.io`` which writes to ``<ledger_path>.tmp`` first and
    then renames over the target.  A failure at any point before the
    rename leaves the prior ledger intact.

    Args:
        ledger_path: Path to the ledger file.  The parent directory MUST
            exist (i.e., ``spec/`` must already be present).
        decision: The decision to append.  ``decision.index`` is ignored;
            the next sequential index is assigned automatically via
            ``next_index``.

    Returns:
        A ``LedgerEntry`` reflecting the newly written ``## D<N>`` section
        with the assigned index.

    Raises:
        DuplicateResolutionError: When *decision.contradiction* matches an
            already-recorded entry (exact text match after stripping
            surrounding whitespace).
        FileNotFoundError: When *ledger_path*'s parent directory does not
            exist (propagated from ``atomic_write_text``).
        OSError: For any other IO error (disk full, permission, etc.).
    """
    existing_content = ""
    if ledger_path.exists():
        existing_content = ledger_path.read_text(encoding="utf-8")

    contradiction_line_re = re.compile(r"^\*\*Contradiction:\*\*\s*(.+)$", re.MULTILINE)
    for match in contradiction_line_re.finditer(existing_content):
        recorded_text = match.group(1).strip()
        if recorded_text == decision.contradiction.strip():
            pos = match.start()
            prior_text = existing_content[:pos]
            heading_matches = list(_ENTRY_HEADING_RE.finditer(prior_text))
            existing_index = int(heading_matches[-1].group(1)) if heading_matches else 1
            raise DuplicateResolutionError(
                existing_index=existing_index,
                contradiction=decision.contradiction,
            )

    index = next_index(ledger_path)

    new_entry_text = _format_entry(index, decision)

    if existing_content.strip():
        new_content = existing_content.rstrip("\n") + "\n\n" + new_entry_text
    else:
        new_content = _LEDGER_HEADING + "\n\n" + new_entry_text

    atomic_write_text(ledger_path, new_content)

    return LedgerEntry(index=index, raw=new_entry_text.rstrip("\n"))
