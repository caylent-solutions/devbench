"""Deterministic name-coverage pre-pass for the spec-to-backlog coverage audit.

Issue #265 E12-F3-S1: after the first green ``validate-backlog``,
``spec-to-backlog`` runs an adversarial coverage audit. This module
provides the deterministic pre-pass that greps every named
work-item/module/unit/workflow/app/config element enumerated by the
spec against task manifests to seed that audit.

Public API:

- ``ELEMENT_CATEGORIES`` -- mapping from category name to compiled
  regex that extracts named elements of that category from spec text.
- ``SpecElement`` -- a named element found in a spec (name + category).
- ``CoverageResult`` -- element paired with the covering task-id (or
  ``None``) and a boolean ``is_covered`` flag.
- ``GapReport`` -- structured gap record: severity, spec-requirement
  quote, covering task-id or ``None``, what is missing, and fix action.
- ``enumerate_spec_elements`` -- extract all named elements across all
  categories from a spec string.
- ``run_name_coverage_pre_pass`` -- cross-reference spec elements
  against task manifest files and return a ``CoverageResult`` per
  element.
- ``verify_gap`` -- independent re-verification of a single gap to
  eliminate false positives before forwarding to gap-fill.

Spec Section 4 E12-F3-S1 AC-1, AC-2, AC-3.
Spec Appendix D E12-F3 (name-coverage pre-pass helper).
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

ELEMENT_CATEGORIES: dict[str, re.Pattern[str]] = {
    "work-item": re.compile(
        r"`([A-Za-z][A-Za-z0-9_-]*)` work-item"
        r"|work-item `([A-Za-z][A-Za-z0-9_-]*)`",
        re.IGNORECASE,
    ),
    "module": re.compile(
        r"`([A-Za-z][A-Za-z0-9_-]*)` module"
        r"|module `([A-Za-z][A-Za-z0-9_-]*)`"
        r"|the `([A-Za-z][A-Za-z0-9_-]*)` module",
        re.IGNORECASE,
    ),
    "unit": re.compile(
        r"`([A-Za-z][A-Za-z0-9_-]*)` unit"
        r"|unit `([A-Za-z][A-Za-z0-9_-]*)`"
        r"|the `([A-Za-z][A-Za-z0-9_-]*)` unit",
        re.IGNORECASE,
    ),
    "workflow": re.compile(
        r"`([A-Za-z][A-Za-z0-9_-]*)` workflow"
        r"|workflow `([A-Za-z][A-Za-z0-9_-]*)`"
        r"|the `([A-Za-z][A-Za-z0-9_-]*)` workflow",
        re.IGNORECASE,
    ),
    "app": re.compile(
        r"`([A-Za-z][A-Za-z0-9_-]*)` app"
        r"|app `([A-Za-z][A-Za-z0-9_-]*)`"
        r"|the `([A-Za-z][A-Za-z0-9_-]*)` app",
        re.IGNORECASE,
    ),
    "config": re.compile(
        r"`([A-Za-z][A-Za-z0-9_.\-]*)` config"
        r"|config `([A-Za-z][A-Za-z0-9_.\-]*)`"
        r"|via (?:the )?`([A-Za-z][A-Za-z0-9_.\-]*)` config"
        r"|`([A-Za-z][A-Za-z0-9_.\-]+\.[a-z]{2,5})` config"
        r"|config(?:uration)? (?:is )?managed via (?:the )?`([A-Za-z][A-Za-z0-9_.\-]+\.[a-z]{2,5})`",
        re.IGNORECASE,
    ),
}


@dataclass(frozen=True)
class SpecElement:
    """A named element extracted from a spec.

    Attributes:
        name: The element's identifier as it appears in the spec (backticks
            stripped).
        category: One of the six ELEMENT_CATEGORIES keys.
    """

    name: str
    category: str


@dataclass(frozen=True)
class CoverageResult:
    """Outcome of checking one spec element against task manifests.

    Attributes:
        element: The ``SpecElement`` being checked.
        covering_task_id: The task-file stem (e.g. ``E1-F1-S1-T1``) of the
            first manifest file whose content contains the element name, or
            ``None`` when no manifest covers it.
        is_covered: ``True`` iff ``covering_task_id`` is not ``None``.
    """

    element: SpecElement
    covering_task_id: str | None
    is_covered: bool


@dataclass(frozen=True)
class GapReport:
    """Structured gap record emitted by the coverage audit.

    Shape:
        {severity, spec_requirement_quote, covering_task_id or NONE,
         what_is_missing, fix: 'NEW TASK' or 'ENHANCE <id>'}

    Spec Section 4 E12-F3-S1 AC-2.

    Attributes:
        severity: One of ``"high"``, ``"medium"``, or ``"low"``.
        spec_requirement_quote: The verbatim spec line(s) that establish the
            requirement for the missing element.
        covering_task_id: The task-id that partially covers this element, or
            ``None`` when no task covers it at all.
        what_is_missing: Human-readable description of what is absent.
        fix: Either the literal string ``"NEW TASK"`` or the string
            ``"ENHANCE <task-id>"``.
    """

    severity: str
    spec_requirement_quote: str
    covering_task_id: str | None
    what_is_missing: str
    fix: str


def enumerate_spec_elements(spec_text: str) -> list[SpecElement]:
    """Return every named element found in *spec_text* across all categories.

    Applies the regexes in ``ELEMENT_CATEGORIES`` to *spec_text* and
    returns one ``SpecElement`` per (name, category) pair, deduplicated
    within each category. Elements that appear under multiple categories
    are reported once per category.

    Args:
        spec_text: The full text of a spec Markdown file.

    Returns:
        A deduplicated list of ``SpecElement`` instances in document order
        (first occurrence per (name, category) pair wins).
    """
    seen: set[tuple[str, str]] = set()
    results: list[SpecElement] = []

    for category, pattern in ELEMENT_CATEGORIES.items():
        for match in pattern.finditer(spec_text):
            name = next((g for g in match.groups() if g is not None), None)
            if name is None:
                continue
            name = name.strip()
            if not name:
                continue
            key = (name, category)
            if key in seen:
                continue
            seen.add(key)
            results.append(SpecElement(name=name, category=category))

    return results


def run_name_coverage_pre_pass(
    *,
    spec_text: str,
    manifest_dir: Path,
) -> list[CoverageResult]:
    """Cross-reference spec-named elements against task manifest files.

    For every element returned by ``enumerate_spec_elements``, scans all
    ``*.md`` files under *manifest_dir* (recursively) for the element name.
    The first file whose content contains the name is recorded as the
    covering task.

    Args:
        spec_text: The full text of the spec to analyse.
        manifest_dir: Root directory containing task manifest ``.md`` files
            to search (typically ``backlog/``).

    Returns:
        A ``CoverageResult`` per spec element (same order as
        ``enumerate_spec_elements``). Elements with no covering manifest
        carry ``covering_task_id=None`` and ``is_covered=False``.

    Raises:
        FileNotFoundError: When *manifest_dir* does not exist on disk.
            The message always names the path so the caller can report it
            to the operator.
    """
    if not manifest_dir.exists():
        raise FileNotFoundError(
            f"ERROR: manifest_dir does not exist: {manifest_dir}\n"
            "Ensure the backlog directory has been initialised before running "
            "the name-coverage pre-pass."
        )

    elements = enumerate_spec_elements(spec_text)
    if not elements:
        return []

    manifest_files: list[tuple[str, str]] = []
    for md_file in sorted(manifest_dir.rglob("*.md")):
        try:
            content = md_file.read_text(encoding="utf-8")
        except OSError as exc:
            raise OSError(
                f"ERROR: failed to read manifest file {md_file}: {exc}\nCheck file permissions and encoding."
            ) from exc
        manifest_files.append((md_file.stem, content))

    results: list[CoverageResult] = []
    for elem in elements:
        covering_task_id: str | None = None
        for task_id, content in manifest_files:
            if elem.name in content:
                covering_task_id = task_id
                break
        results.append(
            CoverageResult(
                element=elem,
                covering_task_id=covering_task_id,
                is_covered=covering_task_id is not None,
            )
        )

    return results


def verify_gap(
    *,
    gap: GapReport,
    element: SpecElement,
    manifest_dir: Path,
) -> bool:
    """Independently verify that *gap* is a genuine gap (not a false positive).

    Re-scans *manifest_dir* for the element name. Returns ``True`` when the
    element is still absent (genuine gap). Returns ``False`` when the element
    is now found (false positive -- the gap should be dropped).

    The independent verification step is mandatory per spec Section 4
    E12-F3-S1 AC-3: each gap must be confirmed before being forwarded to
    gap-fill. A gap that cannot be confirmed is silently dropped; it is
    never forwarded to gap-fill unverified.

    Args:
        gap: The ``GapReport`` candidate to verify.
        element: The ``SpecElement`` the gap refers to.
        manifest_dir: Root directory containing task manifest ``.md`` files.

    Returns:
        ``True`` when the gap is confirmed genuine; ``False`` when it is a
        false positive.

    Raises:
        FileNotFoundError: When *manifest_dir* does not exist on disk.
            The message always names the path.
    """
    if not manifest_dir.exists():
        raise FileNotFoundError(
            f"ERROR: manifest_dir does not exist: {manifest_dir}\n"
            f"Cannot verify gap for element '{element.name}' "
            f"(spec: {gap.spec_requirement_quote!r}).\n"
            "Ensure the backlog directory is accessible before running "
            "gap verification."
        )

    for md_file in manifest_dir.rglob("*.md"):
        try:
            content = md_file.read_text(encoding="utf-8")
        except OSError as exc:
            raise OSError(
                f"ERROR: failed to read manifest file {md_file}: {exc}\nCheck file permissions and encoding."
            ) from exc
        if element.name in content:
            return False

    return True
