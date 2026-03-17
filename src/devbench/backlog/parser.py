"""Backlog parser module for the judges system.

Parses ``BACKLOG.md`` index tables and individual work-unit Markdown files
into ``WorkUnit`` objects. Provides methods for querying actionable,
blocked, and parallel-candidate work units.

Supported Status Values
-----------------------
The parser recognises the full runtime status vocabulary:

- ``in-queue``     — work unit is ready to be picked up.
- ``in-progress``  — an agent is actively working on this unit.
- ``in-review``    — agent has completed work and requested human review.
- ``done``         — all judges passed, work is merged.
- ``blocked``      — unit cannot proceed due to an unresolved blocker.
- ``hold``         — unit is under debate or deferred; not actionable.

All six values are valid in both the BACKLOG.md index and individual
work-unit files.  Status mismatches between the index and the file are
logged as warnings; the file is the authoritative source of truth.
"""

from __future__ import annotations

import logging
import re
from pathlib import Path

from devbench.backlog.work_unit import WorkUnit, WorkUnitStatus, WorkUnitType
from devbench.config import BACKLOG_INDEX, BACKLOG_ROOT
from devbench.constants import (
    BACKLOG_AC_RE,
    BACKLOG_BRANCH_RE,
    BACKLOG_DEP_TABLE_ROW_RE,
    BACKLOG_INDEX_TABLE_ROW_RE,
    BACKLOG_REPO_RE,
    BACKLOG_STATUS_RE,
    BRANCH_NAME_TEMPLATE,
    DEPENDENCY_NONE_VALUES,
    EPIC_PLACEHOLDER_ID,
    STATUS_BLOCKED,
    STATUS_DONE,
    STATUS_HOLD,
    STATUS_IN_PROGRESS,
    STATUS_IN_QUEUE,
    STATUS_IN_REVIEW,
)

# ---------------------------------------------------------------------------
# Mapping from raw markdown status strings to WorkUnitStatus enum values.
# The backlog markdown uses lowercase-hyphenated forms while the enum uses
# title-case values.  This map bridges the two representations.
# ---------------------------------------------------------------------------
_RAW_STATUS_TO_ENUM: dict[str, WorkUnitStatus] = {
    STATUS_IN_QUEUE: WorkUnitStatus.IN_QUEUE,
    STATUS_IN_PROGRESS: WorkUnitStatus.IN_PROGRESS,
    STATUS_IN_REVIEW: WorkUnitStatus.IN_REVIEW,
    STATUS_DONE: WorkUnitStatus.DONE,
    STATUS_BLOCKED: WorkUnitStatus.BLOCKED,
    STATUS_HOLD: WorkUnitStatus.HOLD,
}

# Pattern to determine work-unit type from the last segment of a compound ID.
# E.g. E0-F1-S1-T1 -> last segment starts with "T" -> TASK
_ID_SEGMENT_TYPE: dict[str, WorkUnitType] = {
    "T": WorkUnitType.TASK,
    "S": WorkUnitType.STORY,
    "F": WorkUnitType.FEATURE,
    "E": WorkUnitType.EPIC,
}

# Valid values for the ``Type`` column in the index table.
_VALID_TYPE_VALUES: frozenset[str] = frozenset(t.value for t in WorkUnitType)


def _is_separator(raw: str) -> bool:
    """Return ``True`` if the raw cell content is a table separator (all dashes)."""
    stripped = raw.strip()
    return len(stripped) > 0 and all(c == "-" for c in stripped) and stripped != EPIC_PLACEHOLDER_ID


def _parse_status(raw: str) -> WorkUnitStatus:
    """Convert a raw status string (e.g. ``'in-queue'``) to a ``WorkUnitStatus``.

    Raises ``ValueError`` if the status string is not recognised.
    """
    normalised = raw.strip().lower()
    status = _RAW_STATUS_TO_ENUM.get(normalised)
    if status is None:
        raise ValueError(f"Unrecognised work-unit status '{raw}'. Valid statuses: {sorted(_RAW_STATUS_TO_ENUM)}")
    return status


def _infer_type_from_id(unit_id: str) -> WorkUnitType:
    """Infer the ``WorkUnitType`` from the last segment of a compound ID.

    ``E0``          -> EPIC
    ``E0-F1``       -> FEATURE
    ``E0-F1-S1``    -> STORY
    ``E0-F1-S1-T1`` -> TASK

    Raises ``ValueError`` if the ID does not match a known pattern.
    """
    if unit_id == EPIC_PLACEHOLDER_ID:
        return WorkUnitType.EPIC

    parts = unit_id.split("-")
    if not parts:
        raise ValueError(f"Cannot infer type from empty ID: '{unit_id}'")

    last_segment = parts[-1]
    # The first character of the last segment determines the type.
    prefix_char = last_segment[0].upper()
    unit_type = _ID_SEGMENT_TYPE.get(prefix_char)
    if unit_type is None:
        raise ValueError(
            f"Cannot infer work-unit type from ID '{unit_id}'. "
            f"Last segment '{last_segment}' does not start with "
            f"one of {sorted(_ID_SEGMENT_TYPE)}."
        )
    return unit_type


def _extract_section(content: str, header: str) -> str:
    """Extract text between ``## <header>`` and the next ``##`` heading.

    Returns an empty string if the section is not found.
    """
    pattern = re.compile(
        rf"^##\s+{re.escape(header)}\s*\n(.*?)(?=^##\s|\Z)",
        re.MULTILINE | re.DOTALL,
    )
    match = pattern.search(content)
    if match is None:
        return ""
    return match.group(1).strip()


class BacklogParser:
    """Parses the backlog index and individual work-unit Markdown files."""

    def __init__(
        self,
        backlog_root: Path = BACKLOG_ROOT,
        backlog_index: Path = BACKLOG_INDEX,
    ) -> None:
        self._backlog_root = backlog_root
        self._backlog_index = backlog_index

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def parse_index(self) -> list[WorkUnit]:
        """Parse ``BACKLOG.md`` table rows into a list of ``WorkUnit`` objects.

        Each row is used to locate the work-unit file; the complete ``WorkUnit``
        is then constructed by delegating to :meth:`parse_work_unit_file`.
        After parsing the file, ``unit.dependencies`` is **overwritten** with
        the dependency IDs from the ``Dependencies`` column of the index row.
        This makes ``BACKLOG.md`` the single authoritative source for the
        dependency DAG — rewiring dependencies requires only an index edit,
        not a change to individual work-unit files.

        If the index column deps differ from the file deps, a DEBUG-level log
        line is emitted (observability only — the index always wins).

        Raises ``FileNotFoundError`` if the index file does not exist and
        ``ValueError`` if a table row cannot be parsed.
        """
        if not self._backlog_index.is_file():
            raise FileNotFoundError(f"Backlog index not found at '{self._backlog_index}'")

        content = self._backlog_index.read_text()
        units: list[WorkUnit] = []

        logger = logging.getLogger(__name__)

        for match in BACKLOG_INDEX_TABLE_ROW_RE.finditer(content):
            raw_id = match.group(1).strip()
            raw_type = match.group(3).strip()
            raw_status = match.group(4).strip()
            raw_index_deps = match.group(5).strip()
            raw_file_path = match.group(7).strip().strip("`")

            # Skip header rows, separator rows, and non-work-unit rows
            # (e.g. ``Doc``, ``Template``).  Only rows whose type column
            # matches a known WorkUnitType value are processed.
            if raw_id.lower() == "id" or raw_type.lower() == "type":
                continue
            if _is_separator(raw_id) or _is_separator(raw_type):
                continue
            if raw_type not in _VALID_TYPE_VALUES:
                continue

            unit_type = _infer_type_from_id(raw_id)

            # Validate that the explicit type column matches the inferred type.
            if raw_type.lower() != unit_type.value.lower():
                raise ValueError(
                    f"Type mismatch for '{raw_id}': column says '{raw_type}' but ID implies '{unit_type.value}'."
                )

            if not raw_file_path:
                raise ValueError(f"Work unit '{raw_id}' has no file path in BACKLOG.md")

            file_path = (self._backlog_root.parent / raw_file_path).resolve()
            unit = self.parse_work_unit_file(file_path)

            # Cross-check: warn when BACKLOG.md index disagrees with the work-unit file.
            # The file is the source of truth for status (parse_work_unit_file already read it),
            # so no correction is needed — only observability.
            index_status = _RAW_STATUS_TO_ENUM.get(raw_status.lower())
            if index_status is not None and index_status != unit.status:
                logger.warning(
                    "Status mismatch for %s: BACKLOG.md says '%s', "
                    "work unit file says '%s'. Using file as source of truth.",
                    unit.id,
                    raw_status,
                    unit.status.value,
                )

            # Overwrite deps from the index column — index is authoritative for DAG.
            index_deps = self._parse_index_dep_column(raw_index_deps)
            if index_deps != unit.dependencies:
                logger.debug(
                    "Dep mismatch for %s: index column has %r, file has %r. "
                    "Using index column as authoritative source.",
                    unit.id,
                    index_deps,
                    unit.dependencies,
                )
            unit.dependencies = index_deps

            units.append(unit)

        if not units:
            raise ValueError(
                f"No work-unit rows found in '{self._backlog_index}'. "
                "Verify the 'Full Work Unit Index' section exists and "
                "contains correctly formatted table rows."
            )

        return units

    def parse_work_unit_file(self, file_path: Path) -> WorkUnit:
        """Parse a single work-unit ``.md`` file into a ``WorkUnit``.

        Raises ``FileNotFoundError`` if the file does not exist and
        ``ValueError`` if required fields are missing.
        """
        if not file_path.is_file():
            raise FileNotFoundError(f"Work-unit file not found: '{file_path}'")

        content = file_path.read_text()

        # --- Title and ID from the first ``# `` heading ---
        title_match = re.search(r"^#\s+(.+)$", content, re.MULTILINE)
        if title_match is None:
            raise ValueError(f"No top-level heading found in '{file_path}'")
        heading = title_match.group(1).strip()

        # Heading format: ``E0-F1-S1-T1: Title Text``
        colon_idx = heading.find(":")
        if colon_idx == -1:
            raise ValueError(f"Top-level heading in '{file_path}' does not contain an ID:Title separator (':').")

        unit_id = heading[:colon_idx].strip()
        title = heading[colon_idx + 1 :].strip()

        # --- Status ---
        status_match = BACKLOG_STATUS_RE.search(content)
        if status_match is None:
            raise ValueError(f"No '## Status:' line found in '{file_path}'")
        status = _parse_status(status_match.group(1))

        # --- Type from ID ---
        unit_type = _infer_type_from_id(unit_id)

        # --- Repo ---
        repo = ""
        repo_match = BACKLOG_REPO_RE.search(content)
        if repo_match is not None:
            repo = repo_match.group(1).strip()

        # --- Branch ---
        # Use the spec-defined branch when present; fall back to the standard
        # naming convention so WorkUnit.branch is always a concrete value.
        branch_match = BACKLOG_BRANCH_RE.search(content)
        if branch_match is not None:
            branch = branch_match.group(1).strip()
        else:
            branch = BRANCH_NAME_TEMPLATE.format(unit_id=unit_id.lower())

        # --- Description ---
        description = _extract_section(content, "Description")

        # --- Dependencies from table ---
        dependencies = self._parse_dependency_table(content)

        # --- Acceptance Criteria ---
        acceptance_criteria: list[str] = []
        for ac_match in BACKLOG_AC_RE.finditer(content):
            acceptance_criteria.append(ac_match.group(1).strip())

        return WorkUnit(
            id=unit_id,
            title=title,
            status=status,
            unit_type=unit_type,
            file_path=file_path,
            repo=repo,
            branch=branch,
            dependencies=dependencies,
            acceptance_criteria=acceptance_criteria,
            description=description,
        )

    def find_next_actionable(self, units: list[WorkUnit]) -> WorkUnit | None:
        """Return the first actionable work unit, or ``None``.

        A work unit is *actionable* when:
        - Its status is ``IN_QUEUE`` or ``IN_PROGRESS``
        - Its type is ``TASK`` (only tasks are directly executable)
        - All of its dependencies have status ``DONE``

        ``IN_PROGRESS`` tasks take priority over ``IN_QUEUE`` so that
        interrupted work is resumed first. Within the same status group,
        units are ordered by ID (lexicographic, giving E0 before E1, etc.).
        """
        candidates = self.get_parallel_candidates(units)
        if not candidates:
            return None
        return candidates[0]

    def all_done(self, units: list[WorkUnit]) -> bool:
        """Return ``True`` if every unit in the list has status ``DONE``."""
        return all(u.status is WorkUnitStatus.DONE for u in units)

    def get_blocked_units(self, units: list[WorkUnit]) -> list[WorkUnit]:
        """Return all units whose status is ``BLOCKED``."""
        return [u for u in units if u.status is WorkUnitStatus.BLOCKED]

    def get_parallel_candidates(self, units: list[WorkUnit]) -> list[WorkUnit]:
        """Return all actionable tasks sorted by numeric-aware ID order.

        A task is *actionable* when:
        - Its status is ``IN_QUEUE`` or ``IN_PROGRESS`` (resume interrupted work)
        - Its type is ``TASK``
        - All of its dependencies have status ``DONE``

        ``IN_PROGRESS`` tasks are returned before ``IN_QUEUE`` tasks so that
        interrupted work is resumed before new work is started.  Within the
        same status group, tasks are ordered by numeric-aware ID so that
        ``E9-*`` sorts before ``E15-*`` (numeric, not lexicographic).

        All dependency types (Task, Story, Feature, Epic) are blocking.
        A dependency is only satisfied when it appears in the ``done`` set.
        """
        actionable_statuses = {WorkUnitStatus.IN_QUEUE, WorkUnitStatus.IN_PROGRESS}
        done_ids = self._done_ids(units)
        candidates: list[WorkUnit] = []

        for unit in units:
            if unit.status not in actionable_statuses:
                continue
            if unit.unit_type is not WorkUnitType.TASK:
                continue
            if not self._deps_satisfied(unit, done_ids):
                continue
            candidates.append(unit)

        # IN_PROGRESS first (resume interrupted work), then IN_QUEUE; within
        # the same status group, sort by numeric-aware ID for deterministic
        # ordering (e.g. E9 before E15, T1 before T2 before T10).
        status_priority = {WorkUnitStatus.IN_PROGRESS: 0, WorkUnitStatus.IN_QUEUE: 1}
        candidates.sort(key=lambda u: (status_priority.get(u.status, 2), self._id_sort_key(u.id)))
        return candidates

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _id_sort_key(unit_id: str) -> tuple:
        """Return a numeric-aware sort key for a work-unit ID.

        Splits the ID on digit boundaries and converts numeric segments to
        ``int`` so that ``E9-*`` sorts before ``E15-*`` (numeric order, not
        lexicographic).

        Examples::

            _id_sort_key("E9-F1-S1-T1")  -> ("E", 9, "-F", 1, "-S", 1, "-T", 1)
            _id_sort_key("E15-F1-S1-T1") -> ("E", 15, "-F", 1, "-S", 1, "-T", 1)
        """
        return tuple(int(p) if p.isdigit() else p for p in re.split(r"(\d+)", unit_id) if p)

    @staticmethod
    def _done_ids(units: list[WorkUnit]) -> frozenset[str]:
        """Collect IDs of all units with status ``DONE``."""
        return frozenset(u.id for u in units if u.status is WorkUnitStatus.DONE)

    @staticmethod
    def _deps_satisfied(unit: WorkUnit, done_ids: frozenset[str]) -> bool:
        """Return ``True`` if every listed dependency is satisfied.

        All dependency types (Task, Story, Feature, Epic) are treated as
        blocking.  A dependency is satisfied only when its ID appears in
        ``done_ids``.  An empty dependency list is always considered satisfied.
        """
        return all(dep in done_ids for dep in unit.dependencies)

    @staticmethod
    def _parse_index_dep_column(raw: str) -> list[str]:
        """Parse the ``Dependencies`` column of a BACKLOG.md index row.

        The column may contain:
        - A comma-separated list of dependency IDs (e.g. ``"E9, E14-F1-S1-T1"``)
        - A sentinel meaning "no dependencies" (``None``, ``--``, ``---``, or empty)

        Returns an empty list for any sentinel value, otherwise returns the
        list of stripped dependency IDs.
        """
        stripped = raw.strip()
        if stripped.lower() in DEPENDENCY_NONE_VALUES:
            return []
        return [token.strip() for token in stripped.split(",") if token.strip()]

    @staticmethod
    def _parse_dependency_table(content: str) -> list[str]:
        """Extract dependency IDs from the ``## Dependencies`` table."""
        section = _extract_section(content, "Dependencies")
        if not section:
            return []

        dependencies: list[str] = []
        for row_match in BACKLOG_DEP_TABLE_ROW_RE.finditer(section):
            dep_id = row_match.group(1).strip()
            # Skip the header row (typically ``ID`` or ``---``).
            if dep_id.lower() == "id" or dep_id.startswith("-"):
                continue
            dependencies.append(dep_id)

        return dependencies
