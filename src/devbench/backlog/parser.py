"""Backlog parser module for the judges system.

Parses ``BACKLOG.md`` index tables and individual work-unit Markdown files
into ``WorkUnit`` objects. Provides methods for querying actionable,
blocked, and parallel-candidate work units.
"""

from __future__ import annotations

import logging
import re
from pathlib import Path
from typing import TYPE_CHECKING, ClassVar

from devbench.backlog.work_unit import WorkUnit, WorkUnitStatus, WorkUnitType
from devbench.config import BACKLOG_INDEX, BACKLOG_ROOT, RUNTIME_CONFIG
from devbench.config_loader import format_branch_name, get_effective_branch_prefix
from devbench.constants import (
    BACKLOG_AC_RE,
    BACKLOG_BRANCH_RE,
    BACKLOG_DEP_TABLE_ROW_RE,
    BACKLOG_INDEX_TABLE_ROW_RE,
    BACKLOG_REPO_RE,
    BACKLOG_STATUS_RE,
    EPIC_PLACEHOLDER_ID,
    STATUS_BLOCKED,
    STATUS_DECLINED,
    STATUS_DONE,
    STATUS_HOLD,
    STATUS_IN_PROGRESS,
    STATUS_IN_QUEUE,
    STATUS_IN_REVIEW,
    STATUS_PROPOSED,
)

if TYPE_CHECKING:
    from devbench.scope import ScopeFilter

# ---------------------------------------------------------------------------
# Mapping from raw markdown status strings to WorkUnitStatus enum values.
# The backlog markdown uses lowercase-hyphenated forms while the enum uses
# title-case values.  This map bridges the two representations.
# ---------------------------------------------------------------------------
_RAW_STATUS_TO_ENUM: dict[str, WorkUnitStatus] = {
    "draft": WorkUnitStatus.DRAFT,  # Temporary literal; replaced by STATUS_DRAFT constant in E1-F1-S1-T3.
    STATUS_IN_QUEUE: WorkUnitStatus.IN_QUEUE,
    STATUS_IN_PROGRESS: WorkUnitStatus.IN_PROGRESS,
    STATUS_IN_REVIEW: WorkUnitStatus.IN_REVIEW,
    STATUS_DONE: WorkUnitStatus.DONE,
    STATUS_BLOCKED: WorkUnitStatus.BLOCKED,
    STATUS_PROPOSED: WorkUnitStatus.PROPOSED,
    STATUS_DECLINED: WorkUnitStatus.DECLINED,
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

    if not unit_id:
        raise ValueError(f"Cannot infer type from empty ID: '{unit_id}'")
    parts = unit_id.split("-")

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
        is then constructed by delegating to :meth:`parse_work_unit_file`, which
        is the single authoritative constructor.  This ensures all fields
        (including ``branch``) are always populated from the file.

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
            try:
                unit = self.parse_work_unit_file(file_path)
            except FileNotFoundError:
                # Single-shot retry against the atomic-rename / writer-window
                # race: SDK-driven Write/Edit tools outside BacklogManager
                # may leave the path momentarily unreadable. The retry is
                # synchronous (microsecond-scale) and closes the race window
                # without any sleep / temporal logic. On persistent failure
                # the second attempt re-raises the original FileNotFoundError
                # with the missing path intact, preserving fail-fast semantics.
                unit = self.parse_work_unit_file(file_path)

            # Cross-check: warn when BACKLOG.md index disagrees with the work-unit file.
            # The file is the source of truth (parse_work_unit_file already read it),
            # so no correction is needed -- only observability.
            index_status = _RAW_STATUS_TO_ENUM.get(raw_status.lower())
            if index_status is not None and index_status != unit.status:
                logger.warning(
                    "Status mismatch for %s: BACKLOG.md says '%s', "
                    "work unit file says '%s'. Using file as source of truth.",
                    unit.id,
                    raw_status,
                    unit.status.value,
                )

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
            branch = format_branch_name(unit_id, get_effective_branch_prefix(repo, RUNTIME_CONFIG))

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

    def get_parallel_candidates(
        self,
        units: list[WorkUnit],
        scope: ScopeFilter | None = None,
    ) -> list[WorkUnit]:
        """Return all actionable tasks sorted by topological depth.

        A task is *actionable* when:
        - Its status is ``IN_QUEUE`` or ``IN_PROGRESS`` (resume interrupted work)
        - Its type is ``TASK``
        - All of its dependencies are satisfied (see :meth:`_deps_satisfied`)
        - Its ID is in ``scope.expanded_ids`` when a ``ScopeFilter`` is provided

        ``IN_PROGRESS`` tasks are returned before ``IN_QUEUE`` tasks so that
        interrupted work is resumed before new work is started. Within each
        status group (issue #121), tasks are returned in **topological-depth
        order**: a task with zero declared dependencies (depth 0) precedes a
        task with one transitive dependency (depth 1), which precedes a task
        with two (depth 2), and so on. The lexicographic ``id`` is the stable
        tiebreaker within a depth band so the order is reproducible.

        Topological depth is computed across the FULL backlog -- not just
        among candidates -- so the "build-order foundation first" intuition
        holds even when most ancestors are already ``done``. Self-loops or
        unresolvable IDs collapse to depth 0 (no penalty); the
        ``validate-backlog`` integrity rule reports those upstream.

        Args:
            units: All work units from the parsed backlog.
            scope: Optional scope filter. When provided, only work units whose
                IDs appear in ``scope.expanded_ids`` are returned. When
                ``None`` (the default), all actionable tasks are returned
                unchanged.

        Returns:
            List of actionable ``WorkUnit`` objects sorted by
            ``(status_priority, topological_depth, id)``.
        """
        actionable_statuses = {WorkUnitStatus.IN_QUEUE, WorkUnitStatus.IN_PROGRESS}
        units_by_id = {u.id: u for u in units}
        candidates: list[WorkUnit] = []

        for unit in units:
            if unit.status not in actionable_statuses:
                continue
            if unit.unit_type is not WorkUnitType.TASK:
                continue
            if not self._deps_satisfied(unit, units_by_id):
                continue
            candidates.append(unit)

        # Compute topological depth across all units. Memoized recursion with
        # cycle protection; each unit's depth = 0 if it has no resolvable
        # deps, else 1 + max(depth of its deps).
        depth_cache: dict[str, int] = {}

        def _depth(unit_id: str, visiting: frozenset[str] = frozenset()) -> int:
            if unit_id in depth_cache:
                return depth_cache[unit_id]
            if unit_id in visiting:
                # Cycle: refuse to recurse further; depth contribution is 0.
                return 0
            unit = units_by_id.get(unit_id)
            if unit is None or not unit.dependencies:
                depth_cache[unit_id] = 0
                return 0
            next_visiting = visiting | {unit_id}
            max_dep_depth = 0
            for dep_id in unit.dependencies:
                if dep_id == unit_id:
                    # Self-dep: skip without penalty (validate-backlog reports it).
                    continue
                max_dep_depth = max(max_dep_depth, _depth(dep_id, next_visiting))
            d = max_dep_depth + 1
            depth_cache[unit_id] = d
            return d

        # Apply scope filter: remove candidates whose IDs are outside the scope.
        if scope is not None:
            candidates = [u for u in candidates if u.id in scope.expanded_ids]

        # IN_PROGRESS first (resume interrupted work), then IN_QUEUE; within
        # the same status group, order by topological depth (shallow first),
        # then by ID for deterministic ordering.
        status_priority = {WorkUnitStatus.IN_PROGRESS: 0, WorkUnitStatus.IN_QUEUE: 1}
        candidates.sort(key=lambda u: (status_priority.get(u.status, 2), _depth(u.id), u.id))
        return candidates

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    # Statuses that satisfy a dependency: ``done`` (intentional completion) and
    # ``declined`` (intentional non-completion). Mirrors
    # ``BacklogManager._TERMINAL_CHILD_STATUSES`` so the orchestrator's
    # actionability scan and the rollup logic agree on what "finished" means.
    _DEP_TERMINAL_STATUSES: ClassVar[frozenset[WorkUnitStatus]] = frozenset(
        {WorkUnitStatus.DONE, WorkUnitStatus.DECLINED}
    )

    @classmethod
    def _deps_satisfied(cls, unit: WorkUnit, units_by_id: dict[str, WorkUnit]) -> bool:
        """Return ``True`` if every dependency of ``unit`` is satisfied.

        Dependency types and their satisfaction rules:

        - **Task dep** (``T`` segment): satisfied when the dep task's status
          is ``done`` or ``declined``.
        - **Epic / Feature / Story dep** (no ``T`` segment): satisfied when
          EVERY descendant task whose ID starts with ``<dep_id>-`` is in a
          terminal state. An epic/feature/story with no task descendants is
          vacuously satisfied -- there is nothing to wait on.
        - **Unknown dep ID** (not in ``units_by_id``): treated as satisfied
          so the orchestrator does not deadlock on a typo'd ID;
          ``validate-backlog`` reports unknown deps as integrity errors so
          the typo cannot hide indefinitely.

        This replaces the prior task-only check that silently returned
        ``True`` for every story/feature/epic dep -- a deadlock-hiding
        gap that let parallel-candidate scans hand out tasks whose
        parent-level prerequisites had not yet completed.
        """
        for dep_id in unit.dependencies:
            dep_unit = units_by_id.get(dep_id)
            if dep_unit is None:
                # Unknown ID: treat as satisfied; validate-backlog reports
                # the integrity error so the typo cannot hide.
                continue
            if dep_unit.unit_type is WorkUnitType.TASK:
                if dep_unit.status not in cls._DEP_TERMINAL_STATUSES:
                    return False
                continue
            # Non-task dep: every descendant TASK must be terminal. Walk
            # the units list rather than recursing through the hierarchy
            # -- IDs are flat-prefixed so a starts-with comparison covers
            # every descendant level (Feature -> Story -> Task).
            descendants = [
                u
                for u in units_by_id.values()
                if u.id != dep_id and u.id.startswith(dep_id + "-") and u.unit_type is WorkUnitType.TASK
            ]
            for descendant in descendants:
                if descendant.status not in cls._DEP_TERMINAL_STATUSES:
                    return False
        return True

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
