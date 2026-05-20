"""Backlog manager that updates work-unit statuses and traceability.

Owns the backlog lifecycle: status writes, done-gate checks, rollups,
comments, backlog validation, and traceability logging.

Public API
----------
``force_status``              -- write any status to both files with no gate
                                checks.  Use for automated lifecycle transitions
                                (in-progress, in-review) and manual recovery.
``mark_done``                 -- gated completion: verifies all required review
                                judges passed before writing ``done``.
``mark_blocked``              -- writes ``blocked`` and appends a reason comment.
``validate``                  -- returns integrity errors (missing files, status
                                drift, orphans, broken deps, summary mismatch).
``log_to_traceability_matrix``-- appends a spec/test mapping entry to the
                                traceability matrix file.
``_append_tdd_entry``         -- appends a timestamped TDD phase entry to the
                                ``## TDD Cycle Log`` section of a work-unit file.

Constructor
-----------
``BacklogManager(logger=None)``
    Accepts an optional ``logging.Logger`` instance.  Defaults to
    ``logging.getLogger("devbench.backlog_manager")`` when omitted.

All writes go through the private ``_set_status`` workhorse which updates
both the work-unit file and BACKLOG.md atomically.  After each write,
``_set_status`` calls the private ``_update_status_summary`` helper to keep
the ``## Status Summary`` table in sync.
"""

import itertools
import logging
import re
from collections import deque
from datetime import UTC, datetime
from pathlib import Path
from typing import ClassVar

from devbench.constants import (
    ALL_REQUIRED_JUDGE_NAMES,
    BACKLOG_INDEX_CELL_COUNT,
    BACKLOG_STATUS_RE,
    BACKLOG_SUBDIR,
    COMMENT_AGENT_TEMPLATE,
    COMMENT_ENTRY_TEMPLATE,
    COMMENT_TIMESTAMP_FORMAT,
    COMMENTS_SECTION_HEADER,
    DEPENDENCY_NONE_VALUES,
    EM_DASH,
    EPIC_ID_RE,
    STATUS_BLOCKED,
    STATUS_DECLINED,
    STATUS_DONE,
    STATUS_HOLD,
    STATUS_IN_PROGRESS,
    STATUS_IN_QUEUE,
    STATUS_LINE_RE,
    STATUS_SUMMARY_SECTION_HEADER,
    STATUS_SUMMARY_TABLE_HEADER,
    STRIP_SUMMARY_RE,
    TABLE_STATUS_VALUES,
    TDD_CYCLE_LOG_SECTION_HEADER,
    TDD_ENTRY_TEMPLATE,
    TRACEABILITY_MATRIX_HEADER,
    VALID_STATUSES,
)

# Terminal statuses for parent-rollup purposes: a child in either state is
# "finalised" and does not block its parent from rolling to done. Kept at
# module level so tests can import and assert the exact set.
_TERMINAL_CHILD_STATUSES: frozenset[str] = frozenset({STATUS_DONE, STATUS_DECLINED})

# Marker written by ``promote-proposal`` to the source task's Comments section
# whenever a proposed draft is wired as a dependency. The auto-requeue scan
# (``_auto_requeue_marker_dependents``) reads these to discriminate blocks
# caused by a promoted proposal chain (auto-recoverable) from blocks caused by
# review failures, git-ops errors, or operator decisions (stay manual). The
# regex captures the target task ID in group 1; the scan is scoped to the
# Comments section so markers quoted in Description/Approach text cannot
# trigger the cascade.
_BLOCKED_PENDING_PROPOSAL_RE: re.Pattern[str] = re.compile(r"\[BLOCKED_PENDING_PROPOSAL\]\s+(\S+)")


class BacklogManager:
    """Owns backlog lifecycle: status writes, done-gate checks, rollups, comments, and validation."""

    def __init__(self, logger: logging.Logger | None = None) -> None:
        """Initialize the manager.

        Args:
            logger: Optional logger instance.  Defaults to
                ``logging.getLogger("devbench.backlog_manager")`` when omitted.
        """
        self.logger = logger or logging.getLogger("devbench.backlog_manager")
        # Idempotency guard for the auto-requeue cascade (issue #147). A
        # ``(backlog_index, unit_id)`` pair is added when the cascade runs;
        # subsequent ``_set_status`` calls for the same terminal target skip
        # the scan. The set is per-instance so independent ``BacklogManager``
        # constructions get their own guard (tests share no state).
        self._cascade_fired_for: set[tuple[str, str]] = set()
        # Populated by ``validate(fix=True)``: ``(fix_count, files_fixed)``.
        self._fix_summary: tuple[int, int] = (0, 0)

    def force_status(
        self,
        work_unit_path: Path,
        backlog_index: Path,
        unit_id: str,
        new_status: str,
    ) -> None:
        """Write any status to both files, bypassing all gate checks.

        Use this for automated lifecycle transitions (``in-progress``,
        ``in-review``) and for manual recovery overrides where the done-gate
        would incorrectly block a legitimate repair.  Prefer ``mark_done``
        for agent-driven completion -- it enforces that all judges have passed.

        Args:
            work_unit_path: Path to the work-unit ``.md`` file.
            backlog_index: Path to the ``BACKLOG.md`` file.
            unit_id: The work-unit identifier (e.g. ``E0-F1-S1-T1``).
            new_status: Status in CLI form (``in-queue``, ``in-progress``,
                ``in-review``, ``done``, ``blocked``) or title-case form.

        Raises:
            FileNotFoundError: If either file does not exist.
            ValueError: If the status is invalid, the ``## Status:`` line
                is missing, or the unit is not found in the backlog index.
        """
        self._set_status(work_unit_path, backlog_index, unit_id, new_status)

    def mark_done(self, work_unit_path: Path, backlog_index: Path, unit_id: str) -> None:
        """Mark a work unit as Done in both files.

        Raises ``RuntimeError`` if not all required review judges have passed
        in the most recent review round (done-gate enforcement).

        Args:
            work_unit_path: Path to the work-unit ``.md`` file.
            backlog_index: Path to the ``BACKLOG.md`` file.
            unit_id: The work-unit identifier.

        Raises:
            RuntimeError: If not all required judges passed in the last round.
            FileNotFoundError: If either file does not exist.
            ValueError: If the status line or unit row is not found.
        """
        if not self._last_round_all_passed(work_unit_path):
            raise RuntimeError(
                f"Cannot mark {unit_id} done: not all required judges passed in the most recent review round"
            )
        self._set_status(work_unit_path, backlog_index, unit_id, STATUS_DONE)

    def mark_blocked(self, work_unit_path: Path, backlog_index: Path, unit_id: str, reason: str) -> None:
        """Mark a work unit as Blocked in both files and append a comment.

        Args:
            work_unit_path: Path to the work-unit ``.md`` file.
            backlog_index: Path to the ``BACKLOG.md`` file.
            unit_id: The work-unit identifier.
            reason: Human-readable reason the work unit is blocked.

        Raises:
            FileNotFoundError: If either file does not exist.
            ValueError: If the status line or unit row is not found.
        """
        self._set_status(work_unit_path, backlog_index, unit_id, STATUS_BLOCKED)
        self._append_comment(work_unit_path, "BLOCKED", reason)

    def mark_declined(self, work_unit_path: Path, backlog_index: Path, unit_id: str, reason: str) -> None:
        """Mark a work unit as Declined in both files and append a comment.

        Declined means "this work has been determined to never be done" -- a
        deliberate, final decision distinct from Blocked (waiting) and Done
        (completed). Children marked Declined count as terminal-complete for
        parent rollup purposes (see :meth:`_all_children_done`).

        Args:
            work_unit_path: Path to the work-unit ``.md`` file.
            backlog_index: Path to the ``BACKLOG.md`` file.
            unit_id: The work-unit identifier.
            reason: Human-readable rationale for the decision. Captured in
                the Comments audit trail alongside a ``[DECLINED]`` marker.

        Raises:
            FileNotFoundError: If either file does not exist.
            ValueError: If the status line or unit row is not found.
        """
        self._set_status(work_unit_path, backlog_index, unit_id, STATUS_DECLINED)
        self._append_comment(work_unit_path, "DECLINED", reason)

    def mark_held(self, work_unit_path: Path, backlog_index: Path, unit_id: str, reason: str) -> None:
        """Mark a work unit as ``hold`` in both files and append an audit comment.

        ``hold`` is a deferred-decision status: the unit is intentionally
        skipped by the orchestrator's ``next``/parallel-candidate scan
        (``BacklogParser.get_parallel_candidates`` filters to
        ``IN_QUEUE``/``IN_PROGRESS`` only) until an operator runs
        ``unmark_held`` to return it to ``in-queue``. Unlike ``declined``,
        ``hold`` is **not** terminal -- a held child does NOT count toward
        a parent's auto-rollup to ``done``; the parent stays open while
        any descendant is on hold.

        Args:
            work_unit_path: Path to the work-unit ``.md`` file.
            backlog_index: Path to the ``BACKLOG.md`` file.
            unit_id: The work-unit identifier.
            reason: Human-readable rationale for the deferral. Captured in
                the Comments audit trail alongside a ``[HOLD]`` marker.

        Raises:
            FileNotFoundError: If either file does not exist.
            ValueError: If the status line or unit row is not found.
        """
        self._set_status(work_unit_path, backlog_index, unit_id, STATUS_HOLD)
        self._append_comment(work_unit_path, "HOLD", reason)

    def unmark_held(self, work_unit_path: Path, backlog_index: Path, unit_id: str, reason: str) -> None:
        """Return a held work unit to ``in-queue`` and append an audit comment.

        Args:
            work_unit_path: Path to the work-unit ``.md`` file.
            backlog_index: Path to the ``BACKLOG.md`` file.
            unit_id: The work-unit identifier.
            reason: Human-readable rationale for the release. Captured in
                the Comments audit trail alongside a ``[UNHOLD]`` marker.

        Raises:
            FileNotFoundError: If either file does not exist.
            ValueError: If the status line or unit row is not found.
        """
        self._set_status(work_unit_path, backlog_index, unit_id, STATUS_IN_QUEUE)
        self._append_comment(work_unit_path, "UNHOLD", reason)

    def validate(self, backlog_index: Path, workspace_root: Path, fix: bool = False) -> list[str]:
        """Check backlog integrity and return a list of error messages.

        Checks performed:
        1. Every row in BACKLOG.md has a corresponding work unit file.
        2. Every work unit file's status matches the index.
        3. No orphaned work unit files (in workspace_root/backlog/ but not in index).
        4. All dependency IDs reference real work unit IDs in the index.
        5. Status Summary table exists and counts match the Full Work Unit Index.
        6. Task files have non-empty ## Description section.
        7. Task files have ## Acceptance Criteria with at least one AC- item.
        8. Task files have ## Changes Manifest with at least one entry.
        9. Task files have ## Definition of Done section.
        10. No em-dash character (U+2014) in work unit files.
        11. Changes Manifest paths do not start with a ``checkout_directory`` prefix.
        12. Manifest path conflicts (no two in-queue Tasks claim the same file).
        13. Language-AC alignment (non-Python tasks must mark Python ACs N/A).
        14. Source-test atomicity (every prod source has a paired test in the same Manifest).
        15. Required sections (Status, Dependencies, Changes Manifest) on every Task.
        16. Status enum (every parsed ``## Status:`` value matches ``VALID_STATUSES``).
        17. Dependency-ID format (every entry in a ``## Dependencies`` table matches the canonical regex).
        18. Branch uniqueness (no two Tasks derive the same branch name; skipped in single-PR mode).
        19. No placeholder Manifest rows (no active Task carries a ``TBD`` row in its Changes Manifest).
        20. No orphan path tokens in AC / DoD (gated by ``validate.check_orphan_path_tokens``):
            backtick-quoted path-shaped tokens in ``## Acceptance Criteria`` and
            ``## Definition of Done`` must appear in the Task's Changes Manifest after
            normalisation, OR be marked read-only via a trailing ``(ref)`` suffix.

        Args:
            backlog_index: Path to the ``BACKLOG.md`` index file.
            workspace_root: Workspace root containing BACKLOG.md and the backlog/ subdirectory.
            fix: When ``True``, auto-correct rule-10 (em-dash) and rule-11
                (checkout_directory prefix) violations in-place and append an
                audit comment to each corrected file's ``## Comments`` section.
                Violations that were corrected are NOT included in the returned
                error list. Without ``fix``, the method is read-only.

        Returns:
            A list of error strings. Empty list means the backlog is valid (or
            all fixable violations were corrected when ``fix=True``).
        """
        rows = self._parse_backlog_rows(backlog_index)
        known_ids = {row_id for row_id, _, _ in rows if row_id and not row_id.startswith("-")}

        errors: list[str] = []
        self._check_full_index_has_rows(backlog_index, errors)
        indexed_files = self._check_files_and_statuses(rows, workspace_root, errors)
        self._check_orphans(workspace_root, indexed_files, errors)
        self._check_dependencies(backlog_index, known_ids, errors)
        self._check_dep_cycles(backlog_index, errors)
        self._check_status_summary(backlog_index, rows, errors)
        if fix:
            fix_count, fix_files = self._apply_fixes(rows, workspace_root)
            self._fix_summary = (fix_count, fix_files)
            rows = self._parse_backlog_rows(backlog_index)
        self._check_task_content(rows, workspace_root, errors)
        self._check_manifest_path_prefixes(rows, workspace_root, errors)
        self._check_manifest_conflicts(rows, workspace_root, errors)
        self._check_language_ac_alignment(rows, workspace_root, errors)
        self._check_source_test_pairs(rows, workspace_root, errors)
        self._check_required_sections(rows, workspace_root, errors)
        self._check_status_enum(rows, workspace_root, errors)
        self._check_dep_id_format(rows, workspace_root, errors)
        self._check_branch_uniqueness(rows, workspace_root, errors)
        self._check_no_placeholder_manifest_rows(rows, workspace_root, errors)
        self._check_no_orphan_path_tokens(rows, workspace_root, errors)
        return errors

    _CANONICAL_FULL_INDEX_HEADER_CELLS: tuple[str, ...] = (
        "",
        "ID",
        "Title",
        "Type",
        "Status",
        "Dependencies",
        "Repo",
        "File Path",
        "",
    )

    @staticmethod
    def _scan_full_index_rows(
        backlog_index: Path,
    ) -> tuple[list[str] | None, list[str] | None, int]:
        """Return ``(header_text_cells, separator_cells, real_row_count)``.

        First-pass scanner used by ``_check_full_index_has_rows``. The
        header text row is the first pipe-row inside ``## Full Work Unit
        Index`` whose second cell is ``id`` (case-insensitive); the
        separator row is the next pipe-row whose second cell begins
        with ``-``.
        """
        content = backlog_index.read_text(encoding="utf-8")
        in_full_index = False
        header_text_cells: list[str] | None = None
        separator_cells: list[str] | None = None
        real_row_count = 0
        for line in content.splitlines():
            stripped = line.strip()
            if stripped.startswith("## "):
                in_full_index = "Full Work Unit Index" in stripped
                continue
            if not in_full_index or not stripped.startswith("|"):
                continue
            cells = [c.strip() for c in line.split("|")]
            if header_text_cells is None and len(cells) >= 3 and cells[1].lower() == "id":
                header_text_cells = cells
                continue
            if (
                header_text_cells is not None
                and separator_cells is None
                and len(cells) >= 2
                and cells[1].startswith("-")
            ):
                separator_cells = cells
                continue
            if len(cells) != BACKLOG_INDEX_CELL_COUNT:
                continue
            row_id = cells[1]
            if not row_id or row_id.lower() == "id" or row_id.startswith("-"):
                continue
            real_row_count += 1
        return header_text_cells, separator_cells, real_row_count

    @staticmethod
    def _format_header_validation_errors(
        backlog_index: Path,
        header_text_cells: list[str] | None,
        separator_cells: list[str] | None,
        real_row_count: int,
    ) -> list[str]:
        """Convert scan results into the ordered list of prepended errors."""
        prepended: list[str] = []
        if header_text_cells is None:
            prepended.append(
                f"No '## Full Work Unit Index' header text row found in '{backlog_index}'."
                " Expected canonical 7-column header:"
                " | ID | Title | Type | Status | Dependencies | Repo | File Path |"
            )
        elif len(header_text_cells) != BACKLOG_INDEX_CELL_COUNT:
            prepended.append(
                f"'## Full Work Unit Index' header row in '{backlog_index}' has"
                f" {len(header_text_cells)} cells; expected {BACKLOG_INDEX_CELL_COUNT}"
                " (canonical 7-column format:"
                " | ID | Title | Type | Status | Dependencies | Repo | File Path |)."
            )
        elif tuple(header_text_cells) != BacklogManager._CANONICAL_FULL_INDEX_HEADER_CELLS:
            prepended.append(
                f"'## Full Work Unit Index' header row in '{backlog_index}' does not match"
                " the canonical column order/spelling."
                f" Got: | {' | '.join(header_text_cells[1:-1])} |."
                " Expected: | ID | Title | Type | Status | Dependencies | Repo | File Path |."
            )

        if header_text_cells is not None and separator_cells is None:
            prepended.append(f"'## Full Work Unit Index' separator row (|---|---|...) is missing in '{backlog_index}'.")
        elif separator_cells is not None and len(separator_cells) != BACKLOG_INDEX_CELL_COUNT:
            prepended.append(
                f"'## Full Work Unit Index' separator row in '{backlog_index}' has"
                f" {len(separator_cells)} cells; expected {BACKLOG_INDEX_CELL_COUNT}."
            )

        if real_row_count == 0:
            prepended.append(
                f"No work-unit rows parsed from '{backlog_index}'."
                " The '## Full Work Unit Index' section is missing or malformed."
                " Expected 7-column rows:"
                " | ID | Title | Type | Status | Dependencies | Repo | File Path |"
            )
        return prepended

    @staticmethod
    def _check_full_index_has_rows(backlog_index: Path, errors: list[str]) -> None:
        """Rule-0: verify '## Full Work Unit Index' uses the canonical 7-column header and contains at least one row.

        The header text row MUST read exactly:
            ``| ID | Title | Type | Status | Dependencies | Repo | File Path |``
        and the separator row immediately below it MUST have the same
        number of cells. A zero-row result, an unexpected column order /
        spelling, or a malformed separator all surface as distinct errors
        so the operator can fix the root cause directly.

        This check fires BEFORE orphan detection (rule 3) so a malformed
        index does not produce a stale orphan-storm that obscures the root
        cause. New errors are prepended so they appear first regardless of
        call order within ``validate()``.

        Args:
            backlog_index: Path to the ``BACKLOG.md`` index file.
            errors: Mutable error list; new errors are prepended.
        """
        header_text_cells, separator_cells, real_row_count = BacklogManager._scan_full_index_rows(backlog_index)
        prepended = BacklogManager._format_header_validation_errors(
            backlog_index, header_text_cells, separator_cells, real_row_count
        )
        # Prepend in reverse so the order in ``errors`` matches the order
        # we appended (header issue first, separator issue second, row
        # count third).
        for msg in reversed(prepended):
            errors.insert(0, msg)

    def _apply_fixes(
        self,
        rows: list[tuple[str, str, str]],
        workspace_root: Path,
    ) -> tuple[int, int]:
        """Auto-correct rule-10 (em-dash) and rule-11 (checkout_directory prefix) violations.

        For each task work unit:
        - Rule 10: replace every U+2014 character with ``--``.
        - Rule 11: strip the ``checkout_directory/`` prefix from Changes Manifest
          paths that carry it.

        After correcting a violation, appends an audit comment to the work
        unit's ``## Comments`` section:
        ``[VALIDATE_FIX] auto-corrected <rule>: <before> -> <after>``

        Args:
            rows: Parsed backlog rows as returned by ``_parse_backlog_rows``.
            workspace_root: Workspace root used to resolve work-unit file paths.

        Returns:
            ``(fix_count, files_fixed)`` -- total individual corrections applied
            and the count of distinct files that were modified.
        """
        timestamp = datetime.now(tz=UTC).strftime("%Y-%m-%d %H:%M UTC")
        fix_count = 0
        files_fixed: set[Path] = set()

        for row_id, _, file_path_str in rows:
            if not row_id or row_id.startswith("-") or row_id.lower() == "id":
                continue
            if not file_path_str or not self._is_task_id(row_id):
                continue

            wu_path = workspace_root / file_path_str
            if not wu_path.exists():
                continue

            content = wu_path.read_text(encoding="utf-8")
            original = content
            audit_lines: list[str] = []

            content, r10_count = self._fix_em_dash(content, audit_lines)
            fix_count += r10_count

            content, r11_count = self._fix_manifest_prefixes(content, audit_lines)
            fix_count += r11_count

            if content != original or audit_lines:
                content = self._append_fix_audit(content, timestamp, audit_lines)
                wu_path.write_text(content, encoding="utf-8")
                files_fixed.add(wu_path)

        return fix_count, len(files_fixed)

    @staticmethod
    def _fix_em_dash(content: str, audit_lines: list[str]) -> tuple[str, int]:
        """Replace all U+2014 em-dash characters with '--'.

        Returns the (possibly modified) content and the count of replacements.
        Appends an audit line to ``audit_lines`` when at least one replacement is made.
        """
        if EM_DASH not in content:
            return content, 0
        count = content.count(EM_DASH)
        content = content.replace(EM_DASH, "--")
        audit_lines.append(
            f"[VALIDATE_FIX] auto-corrected rule-10: replaced {count} em-dash character(s) (U+2014) with '--'"
        )
        return content, count

    @staticmethod
    def _fix_manifest_prefixes(content: str, audit_lines: list[str]) -> tuple[str, int]:
        """Strip checkout_directory prefix from Changes Manifest path cells.

        Returns the (possibly modified) content and the count of path corrections.
        Appends audit lines to ``audit_lines`` for each corrected path.
        """
        from devbench.backlog.manifest import parse_manifest
        from devbench.config import RUNTIME_CONFIG

        repo = BacklogManager._extract_repo(content)
        if repo is None or repo not in RUNTIME_CONFIG.repos:
            return content, 0
        checkout_dir = RUNTIME_CONFIG.repos[repo].checkout_directory
        if not checkout_dir:
            return content, 0

        prefix = f"{checkout_dir.rstrip('/')}/"
        try:
            manifest_rows = parse_manifest(content)
        except Exception:
            return content, 0

        fix_count = 0
        for mrow in manifest_rows:
            if not mrow.file.startswith(prefix):
                continue
            stripped = mrow.file[len(prefix) :]
            old_cell = f"`{mrow.file}`"
            new_cell = f"`{stripped}`"
            if old_cell in content:
                content = content.replace(old_cell, new_cell, 1)
                audit_lines.append(
                    f"[VALIDATE_FIX] auto-corrected rule-11: "
                    f"stripped checkout_directory prefix from "
                    f"'{mrow.file}' -> '{stripped}'"
                )
                fix_count += 1

        return content, fix_count

    @staticmethod
    def _append_fix_audit(content: str, timestamp: str, audit_lines: list[str]) -> str:
        """Append the VALIDATE_FIX audit block to the work-unit file content.

        If the file already has a ``## Comments`` section, appends after the last
        line; otherwise creates the section first.
        """
        if not audit_lines:
            return content
        audit_block = "\n".join(f"[{timestamp}] [validate_fix] {line}" for line in audit_lines)
        if COMMENTS_SECTION_HEADER in content:
            return content.rstrip("\n") + "\n\n" + audit_block + "\n"
        return content.rstrip("\n") + "\n\n" + COMMENTS_SECTION_HEADER + "\n\n" + audit_block + "\n"

    def _check_files_and_statuses(
        self,
        rows: list[tuple[str, str, str]],
        workspace_root: Path,
        errors: list[str],
    ) -> set[Path]:
        """Check file existence and status consistency (checks 1 and 2)."""
        indexed_files: set[Path] = set()
        for row_id, index_status, file_path_str in rows:
            if not row_id or row_id.startswith("-") or row_id.lower() == "id":
                continue
            if not file_path_str:
                continue
            wu_path = workspace_root / file_path_str
            indexed_files.add(wu_path.resolve())

            if not wu_path.exists():
                errors.append(f"{row_id}: work unit file missing -- expected {file_path_str}")
                continue

            content = wu_path.read_text(encoding="utf-8")
            m = BACKLOG_STATUS_RE.search(content)
            if m:
                file_status = m.group(1).strip().lower()
                if file_status != index_status:
                    errors.append(f"{row_id}: status mismatch -- index has '{index_status}', file has '{file_status}'")
            else:
                errors.append(f"{row_id}: work unit file missing '## Status:' line")
        return indexed_files

    def _check_orphans(self, workspace_root: Path, indexed_files: set[Path], errors: list[str]) -> None:
        """Check 3: no orphaned work unit files."""
        backlog_dir = workspace_root / BACKLOG_SUBDIR
        if backlog_dir.exists():
            for wu_file in backlog_dir.rglob("*.md"):
                if wu_file.resolve() not in indexed_files:
                    rel = wu_file.relative_to(backlog_dir)
                    errors.append(f"{rel}: orphaned work unit file not in BACKLOG.md")

    def _check_dependencies(self, backlog_index: Path, known_ids: set[str], errors: list[str]) -> None:
        """Check 4: all dependency IDs in the Full Work Unit Index reference real IDs.

        The Status Summary table has the same cell count as the Full Work
        Unit Index after the Declined column was added, so a naive pipe-row
        scan would mis-parse Summary rows as index rows. Track the current
        ``##`` section to scope dependency parsing to the index only.
        """
        content = backlog_index.read_text(encoding="utf-8")
        in_full_index = False
        for line in content.splitlines():
            # Section tracking: dependency parsing is valid only inside the
            # Full Work Unit Index section. Any other ## header closes it.
            stripped = line.strip()
            if stripped.startswith("## "):
                in_full_index = "Full Work Unit Index" in stripped
                continue
            if not in_full_index:
                continue
            if not stripped.startswith("|"):
                continue
            cells = [c.strip() for c in line.split("|")]
            if len(cells) != BACKLOG_INDEX_CELL_COUNT:
                continue
            row_id = cells[1]
            if not row_id or row_id.lower() == "id" or row_id.startswith("-"):
                continue
            dep_cell = cells[5]
            for raw_dep in dep_cell.split(","):
                dep_id = raw_dep.strip()
                if dep_id and dep_id.lower() not in DEPENDENCY_NONE_VALUES and dep_id not in known_ids:
                    errors.append(f"{row_id}: dependency '{dep_id}' not found in backlog index")

    def _check_dep_cycles(self, backlog_index: Path, errors: list[str]) -> None:
        """Issue #151: detect dependency cycles via DFS-with-recursion-stack.

        Walks the dep graph derived from the Full Work Unit Index. A cycle
        exists when, during DFS, we encounter a node that is currently in
        the recursion stack (the "gray" set). Self-edges and chains of any
        length (4-node, N-node) are detected because the recursion-stack
        membership check is the unique cycle witness.

        Reports one error per cycle, naming the participating node IDs in
        traversal order so the operator can spot the offending chain. Cycle
        reporting is normalised: each cycle is rotated to start at its
        lexicographically smallest ID and reported once even when the DFS
        encounters it from multiple roots.
        """
        graph = self._build_dependency_graph(backlog_index)
        if not graph:
            return

        # DFS color tracking: 0 = white (unvisited), 1 = gray (on the
        # recursion stack -- a back-edge to a gray node is the cycle
        # witness), 2 = black (fully processed). ``stack`` records the
        # current DFS chain so we can extract the cycle nodes when a
        # back-edge fires.
        color: dict[str, int] = dict.fromkeys(graph, 0)
        stack: list[str] = []
        reported: set[tuple[str, ...]] = set()

        def visit(node: str) -> None:
            color[node] = 1
            stack.append(node)
            for nxt in graph.get(node, ()):
                if nxt not in color:
                    # Dependency on a non-indexed ID -- already reported by
                    # _check_dependencies; skip without traversal.
                    continue
                if color[nxt] == 1:
                    # Cycle: rotate so the lexicographically smallest node
                    # starts the chain, then dedupe.
                    cycle_start = stack.index(nxt)
                    cycle = tuple(stack[cycle_start:])
                    rotation = cycle.index(min(cycle))
                    normalised = cycle[rotation:] + cycle[:rotation]
                    if normalised not in reported:
                        reported.add(normalised)
                        chain = " -> ".join([*normalised, normalised[0]])
                        errors.append(f"dependency cycle detected: {chain}")
                    continue
                if color[nxt] == 0:
                    visit(nxt)
            stack.pop()
            color[node] = 2

        for node in sorted(graph):
            if color.get(node) == 0:
                visit(node)

    def _build_dependency_graph(self, backlog_index: Path) -> dict[str, list[str]]:
        """Parse the Full Work Unit Index into a ``{id: [dep_id, ...]}`` map.

        Mirrors the parsing scope used by :meth:`_check_dependencies`. Only
        rows inside the ``## Full Work Unit Index`` section are considered;
        Status Summary rows have the same cell count and would otherwise
        pollute the graph.
        """
        graph: dict[str, list[str]] = {}
        if not backlog_index.exists():
            return graph
        content = backlog_index.read_text(encoding="utf-8")
        in_full_index = False
        for line in content.splitlines():
            stripped = line.strip()
            if stripped.startswith("## "):
                in_full_index = "Full Work Unit Index" in stripped
                continue
            if not in_full_index or not stripped.startswith("|"):
                continue
            cells = [c.strip() for c in line.split("|")]
            if len(cells) != BACKLOG_INDEX_CELL_COUNT:
                continue
            row_id = cells[1]
            if not row_id or row_id.lower() == "id" or row_id.startswith("-"):
                continue
            dep_cell = cells[5]
            deps: list[str] = []
            for raw_dep in dep_cell.split(","):
                dep_id = raw_dep.strip()
                if not dep_id or dep_id.lower() in DEPENDENCY_NONE_VALUES:
                    continue
                deps.append(dep_id)
            graph[row_id] = deps
        return graph

    def log_to_traceability_matrix(self, matrix_path: Path, spec_ref: str, test_ref: str) -> None:
        """Append an entry to the traceability matrix.

        Creates the file with a header row if it does not exist.

        Args:
            matrix_path: Path to the traceability matrix markdown file.
            spec_ref: Specification reference (e.g. ``AC-FUNC-001``).
            test_ref: Test reference (e.g. ``test_user_creation``).
        """
        timestamp = datetime.now(tz=UTC).strftime("%Y-%m-%d %H:%M UTC")

        if not matrix_path.exists():
            header = TRACEABILITY_MATRIX_HEADER
            matrix_path.parent.mkdir(parents=True, exist_ok=True)
            matrix_path.write_text(header, encoding="utf-8")
            self.logger.info("Created traceability matrix at %s", matrix_path)

        row = f"| {spec_ref} | {test_ref} | {timestamp} |\n"
        with matrix_path.open("a", encoding="utf-8") as fh:
            fh.write(row)

        self.logger.info("Logged traceability: %s -> %s", spec_ref, test_ref)

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    # Target section headings whose checkboxes are ticked on done.
    _TICK_SECTIONS: frozenset[str] = frozenset({"Acceptance Criteria", "Definition of Done"})

    def _tick_completion_checkboxes(self, work_unit_path: Path) -> None:
        """Rewrite unchecked / legacy-ticked AC and DoD checkboxes to checked + U+2705.

        Walks the work-unit file line by line, tracking the current ``## ``
        section.  Inside ``## Acceptance Criteria`` and ``## Definition of Done``
        only, rewrites:

        - ``- [ ] <content>``          -> ``- [x] <content> \u2705``
        - ``- [x] <content>`` (no emoji) -> ``- [x] <content> \u2705``
        - Already ``- [x] <content> \u2705`` lines -> no-op.

        Lines outside the two target sections are never modified.  The file is
        written back **only** when at least one line changed (preserving mtime
        when the file is already fully ticked).

        Args:
            work_unit_path: Path to the work-unit ``.md`` file.
        """
        green_check = "\u2705"
        content = work_unit_path.read_text(encoding="utf-8")
        lines = content.splitlines(keepends=True)

        in_target_section = False
        new_lines: list[str] = []
        changed = False

        for line in lines:
            stripped = line.rstrip("\n")
            # Track section changes on ``## `` headings
            if stripped.startswith("## "):
                section_title = stripped[3:].strip()
                in_target_section = section_title in self._TICK_SECTIONS
                new_lines.append(line)
                continue

            if in_target_section:
                # Match ``- [ ] ...`` (unchecked)
                if stripped.startswith("- [ ] "):
                    body = stripped[6:]
                    new_line = f"- [x] {body} {green_check}\n"
                    new_lines.append(new_line)
                    changed = True
                    continue
                # Match ``- [x] ...`` without a trailing green-check (legacy ticked)
                if stripped.startswith("- [x] ") and not stripped.endswith(green_check):
                    body = stripped[6:]
                    new_line = f"- [x] {body} {green_check}\n"
                    new_lines.append(new_line)
                    changed = True
                    continue

            new_lines.append(line)

        if changed:
            work_unit_path.write_text("".join(new_lines), encoding="utf-8")

    def _set_status(
        self,
        work_unit_path: Path,
        backlog_index: Path,
        unit_id: str,
        new_status: str,
    ) -> None:
        """Private workhorse: write status to both files with no gate checks.

        All public transition methods (``force_status``, ``mark_done``,
        ``mark_blocked``) and internal rollup code call this method so that
        every write goes through a single code path.
        """
        canonical = VALID_STATUSES.get(new_status.lower())
        if canonical is None:
            raise ValueError(f"Invalid status '{new_status}'. Valid statuses: {', '.join(sorted(VALID_STATUSES))}")

        self._update_status(work_unit_path, canonical)
        self._update_backlog_index(backlog_index, unit_id, canonical)
        self._update_status_summary(backlog_index)
        self.logger.info(
            "Set %s to '%s' in both work-unit file and BACKLOG.md",
            unit_id,
            canonical,
        )

        # Issue #185: every transition into ``in-progress`` (claim,
        # resume after blocker clear, force-status) writes a
        # ``[WU_CLAIMED]`` audit-comment row carrying the canonical
        # ``Set <id> to 'in-progress'`` phrase so the status-timer
        # fallback (`_latest_audit_in_progress_ts`) can recover the
        # claim timestamp from the work-unit file alone when the
        # orchestrator log has rotated. Skips Stories / Features /
        # Epics whose status is auto-rolled from children (no human
        # ever claims those directly).
        if canonical == STATUS_IN_PROGRESS and "-T" in unit_id:
            self._append_agent_comment(
                work_unit_path,
                "orchestrator",
                f"[WU_CLAIMED] Set {unit_id} to 'in-progress'",
            )

        if canonical == STATUS_DONE:
            self._tick_completion_checkboxes(work_unit_path)

        # Issue #162 Phase 2 (ADR-17): every task-state transition lands
        # in a per-task aggregate JSON at
        # ``<workspace>/.devbench/window-stats/<task-id>.json`` so the
        # reporter can read aggregates instead of re-scanning the log.
        # Single hook point: every public transition method routes
        # through ``_set_status``, so this one call covers all of them.
        # Stories / Features / Epics are skipped (their state is auto-
        # rolled from children; window-stats only tracks tasks).
        if "-T" in unit_id:
            from datetime import UTC, datetime

            from devbench.reporting.window_stats import update_aggregate

            workspace_root = backlog_index.parent
            update_aggregate(workspace_root, unit_id, canonical, datetime.now(UTC))

        if canonical in _TERMINAL_CHILD_STATUSES:
            # Issue #147: every terminal transition (``done`` AND ``declined``)
            # fires the auto-requeue cascade. Previously only ``mark_done``
            # routed through here; ``mark_declined`` / ``force_status declined``
            # silently skipped the scan, leaving downstream blocked tasks
            # marooned. The idempotency guard prevents a redundant scan when
            # the same target is set to the same terminal status twice in
            # one process lifetime (e.g. via repeated ``set-status`` calls).
            cascade_key = (str(backlog_index), unit_id)
            if cascade_key not in self._cascade_fired_for:
                self._cascade_fired_for.add(cascade_key)
                # Auto-requeue reverse-dependents first so the rollup check
                # that follows sees any freshly-unblocked children as
                # non-terminal and correctly declines to promote the parent
                # to done.
                self._auto_requeue_marker_dependents(backlog_index, unit_id)
            if canonical == STATUS_DONE:
                self._rollup_parent_status(backlog_index, unit_id)

    def _last_round_all_passed(self, work_unit_path: Path) -> bool:
        """Check whether the most recent review round had all required judges pass.

        Reads the work-unit file's comment history in reverse. Collects
        ``[REVIEW_PASS]`` entries per judge; stops and resets if a
        ``[REVIEW_REJECTED]`` line is encountered (prior round boundary).

        Returns:
            True if every judge in ``ALL_REQUIRED_JUDGE_NAMES`` has a ``[REVIEW_PASS]``
            entry in the most recent round; False otherwise.
        """
        content = work_unit_path.read_text(encoding="utf-8")
        passed: set[str] = set()
        for line in reversed(content.splitlines()):
            if "[REVIEW_REJECTED]" in line:
                break  # everything before this belongs to a prior round
            for judge in ALL_REQUIRED_JUDGE_NAMES:
                if f"[judge/{judge}]" in line and "[REVIEW_PASS]" in line:
                    passed.add(judge)
        return passed >= ALL_REQUIRED_JUDGE_NAMES

    def _rollup_parent_status(self, backlog_index: Path, unit_id: str) -> None:
        """If all children of the parent unit are Done, mark the parent Done too.

        Derives the parent ID by removing the last segment (e.g. E0-F1-S1-T1 → E0-F1-S1).
        Calls ``_set_status`` directly (bypassing the done-gate -- parent units are
        structurally done when all children are done, no judge review required).
        Cascades upward by recursing through ``_set_status`` → ``_rollup_parent_status``.
        """
        parts = unit_id.rsplit("-", 1)
        if len(parts) < 2:
            return

        parent_id = parts[0]
        rows = self._parse_backlog_rows(backlog_index)

        if not self._all_children_done(rows, parent_id):
            return

        parent_file = self._find_work_unit_file(rows, parent_id, backlog_index.parent)
        if parent_file is None:
            self.logger.warning("Could not find file for parent %s -- skipping rollup", parent_id)
            return

        self.logger.info("All children of %s are done -- rolling up status", parent_id)
        # _set_status: atomic write to both files; cascades via _rollup_parent_status.
        self._set_status(parent_file, backlog_index, parent_id, STATUS_DONE)

        # Write audit comment to parent work unit
        timestamp = datetime.now(tz=UTC).strftime(COMMENT_TIMESTAMP_FORMAT)
        rollup_comment = COMMENT_AGENT_TEMPLATE.format(
            timestamp=timestamp,
            name="orchestrator",
            message="Auto-rolled to done -- all children completed",
        )
        content = parent_file.read_text(encoding="utf-8")
        if COMMENTS_SECTION_HEADER in content:
            content = content.rstrip("\n") + "\n\n" + rollup_comment
        else:
            content = content.rstrip("\n") + "\n\n" + COMMENTS_SECTION_HEADER + "\n\n" + rollup_comment
        parent_file.write_text(content, encoding="utf-8")

    def _extract_pending_proposal_markers(self, work_unit_path: Path) -> set[str]:
        """Return the set of task IDs flagged by ``[BLOCKED_PENDING_PROPOSAL]`` markers.

        Scans only the ``## Comments`` body of ``work_unit_path`` so marker
        text quoted in Description, Approach, or other narrative sections
        cannot trigger the auto-requeue cascade. Returns an empty set when
        the file is missing, has no Comments section, or carries no markers.

        Args:
            work_unit_path: Path to the work-unit ``.md`` file.

        Returns:
            Set of promoted-dependency task IDs that the source task is
            currently waiting on.
        """
        if not work_unit_path.exists():
            return set()
        content = work_unit_path.read_text(encoding="utf-8")
        sections = self._extract_sections(content)
        comments = sections.get("Comments", "")
        return set(_BLOCKED_PENDING_PROPOSAL_RE.findall(comments))

    def _auto_requeue_marker_dependents(self, backlog_index: Path, newly_done_id: str) -> None:
        """Auto-requeue any blocked task whose promoted proposals are now all terminal.

        Symmetric counterpart to :meth:`_rollup_parent_status`: both fire
        from ``_set_status`` when a task transitions to ``done``; the rollup
        cascades upward (child done -> parent done), this cascade runs
        sideways (dep done -> blocked dependent requeued).

        Narrow trigger. A blocked candidate is auto-requeued only when ALL
        of the following hold:

        1. Its status is ``blocked`` (non-blocked candidates are skipped).
        2. The just-completed task (``newly_done_id``) appears in the
           candidate's declared Dependencies table.
        3. The candidate's Comments section carries at least one
           ``[BLOCKED_PENDING_PROPOSAL]`` marker.
        4. Every task ID named by those markers is in a terminal state
           (``done`` or ``declined``). Unknown IDs (e.g. rejected proposals
           no longer in the index) count as non-terminal so the cascade
           stays conservative.

        Tasks that block for other reasons (review failure, git-ops error,
        operator intervention) never carry the marker and stay manual. The
        transition uses :meth:`force_status` and writes an ``[AUTO_UNBLOCKED]``
        audit comment naming every marker ID.

        The scan operates directly on ``_parse_backlog_rows`` output (the
        same lightweight tuple-based view ``_rollup_parent_status`` uses)
        rather than the full ``BacklogParser.parse_index`` object model,
        because the latter validates every file in the index exists and a
        single missing sibling file would otherwise abort the whole scan.

        Args:
            backlog_index: Path to ``BACKLOG.md``.
            newly_done_id: The task that just transitioned to ``done``.
        """
        try:
            rows = self._parse_backlog_rows(backlog_index)
        except FileNotFoundError as exc:
            self.logger.warning("Auto-requeue scan skipped -- %s", exc)
            return

        terminal_ids = {row_id for row_id, status, _ in rows if row_id and status in _TERMINAL_CHILD_STATUSES}
        workspace = backlog_index.parent

        for row_id, status, file_path in rows:
            if not row_id or row_id.startswith("-") or row_id.lower() == "id":
                continue
            if status != STATUS_BLOCKED:
                continue
            if not file_path:
                continue
            candidate_file = workspace / file_path
            if not candidate_file.exists():
                self.logger.warning(
                    "Auto-requeue scan: candidate file missing for %s at %s -- skipping",
                    row_id,
                    candidate_file,
                )
                continue

            # Must be a declared dep. Parse inline from the file content so
            # the scan never depends on the full index being loadable.
            content = candidate_file.read_text(encoding="utf-8")
            if newly_done_id not in self._parse_candidate_dependencies(content):
                continue

            marker_ids = self._extract_pending_proposal_markers(candidate_file)
            if not marker_ids:
                continue
            if not marker_ids.issubset(terminal_ids):
                continue

            sorted_markers = sorted(marker_ids)
            self.logger.info(
                "Auto-requeuing %s -- promoted proposals %s are terminal",
                row_id,
                sorted_markers,
            )
            self.force_status(candidate_file, backlog_index, row_id, STATUS_IN_QUEUE)
            # Issue #153: emit ``[CASCADE_RESOLVED]`` so the status-detail
            # panel renderer can supersede the earlier ``[BLOCKED]`` audit
            # row. ``[AUTO_UNBLOCKED]`` is retained alongside for backward
            # compatibility with operator tooling that already greps for it.
            self._append_agent_comment(
                candidate_file,
                "backlog_manager",
                f"[AUTO_UNBLOCKED] [CASCADE_RESOLVED] promoted proposals {sorted_markers} are terminal; re-queuing",
            )

    @staticmethod
    def _parse_candidate_dependencies(content: str) -> list[str]:
        """Extract declared dependency task IDs from a work-unit file's content.

        Scoped to the ``## Dependencies`` section body. Header rows
        (``| ID | Title | Status |``), separator rows (``|----|...``), and
        the ``| none | | |`` sentinel are all filtered out. Mirrors the
        parser's ``_parse_dependency_table`` but works on already-loaded
        content and does not require instantiating a full parser.
        """
        deps: list[str] = []
        in_deps_section = False
        for raw_line in content.splitlines():
            stripped = raw_line.strip()
            if stripped.startswith("## "):
                in_deps_section = stripped == "## Dependencies"
                continue
            if not in_deps_section or not stripped.startswith("|"):
                continue
            cells = [c.strip() for c in stripped.split("|")]
            if len(cells) < 4:
                continue
            first = cells[1]
            if not first:
                continue
            lowered = first.lower()
            if lowered == "id" or lowered in DEPENDENCY_NONE_VALUES or first.startswith("-"):
                continue
            deps.append(first)
        return deps

    def _parse_backlog_rows(self, backlog_index: Path) -> list[tuple[str, str, str]]:
        """Parse BACKLOG.md table rows into (id, status, file_path) tuples."""
        recognized = {v.lower() for v in TABLE_STATUS_VALUES} | {v.lower() for v in VALID_STATUSES}
        content = backlog_index.read_text(encoding="utf-8")
        rows: list[tuple[str, str, str]] = []

        for line in content.splitlines():
            if not line.strip().startswith("|"):
                continue
            cells = [c.strip() for c in line.split("|")]
            if len(cells) < 5:
                continue

            row_id = cells[1]
            status = ""
            file_path = ""
            for cell in cells:
                if cell.lower() in recognized:
                    status = cell.lower()
                stripped = cell.strip("`")
                if stripped.startswith("backlog/") and stripped.endswith(".md"):
                    file_path = stripped
            rows.append((row_id, status, file_path))

        return rows

    def _all_children_done(self, rows: list[tuple[str, str, str]], parent_id: str) -> bool:
        """Check if the parent exists, is not already Done, and all direct children are in a terminal state.

        A child is "terminal" when its status is Done OR Declined. Declined
        work has been intentionally taken off the table; it should not block
        the parent from rolling to Done. Done + Declined together mean every
        child has been resolved one way or the other.
        """
        parent_depth = parent_id.count("-")
        parent_found = False
        has_children = False

        for row_id, status, _ in rows:
            if row_id == parent_id:
                parent_found = True
                if status == STATUS_DONE:
                    return False  # Already done
                continue

            if row_id.startswith(parent_id + "-") and row_id.count("-") == parent_depth + 1:
                has_children = True
                if status not in _TERMINAL_CHILD_STATUSES:
                    return False

        return parent_found and has_children

    def _find_work_unit_file(
        self,
        rows: list[tuple[str, str, str]],
        unit_id: str,
        workspace_root: Path,
    ) -> Path | None:
        """Find the work unit file path for a given ID."""
        for row_id, _, file_path in rows:
            if row_id == unit_id and file_path:
                candidate = workspace_root / file_path
                if candidate.exists():
                    return candidate
        return None

    def _update_status(self, work_unit_path: Path, new_status: str) -> None:
        """Replace the ``## Status:`` value in a work-unit file."""
        if not work_unit_path.exists():
            raise FileNotFoundError(f"Work-unit file not found: {work_unit_path}")

        content = work_unit_path.read_text(encoding="utf-8")

        if not STATUS_LINE_RE.search(content):
            raise ValueError(f"Could not find '## Status: ...' line in {work_unit_path}")

        updated = STATUS_LINE_RE.sub(rf"\g<1>{new_status}", content, count=1)
        work_unit_path.write_text(updated, encoding="utf-8")

    def _update_backlog_index(self, backlog_index: Path, unit_id: str, new_status: str) -> None:
        """Update the status column for a work unit in the BACKLOG.md table."""
        if not backlog_index.exists():
            raise FileNotFoundError(f"Backlog index not found: {backlog_index}")

        # Build a case-insensitive lookup of recognized statuses
        recognized = {v.lower() for v in TABLE_STATUS_VALUES} | {v.lower() for v in VALID_STATUSES}

        content = backlog_index.read_text(encoding="utf-8")
        lines = content.splitlines()
        updated = False

        for i, line in enumerate(lines):
            if not line.strip().startswith("|"):
                continue
            cells = line.split("|")
            # Match the ID cell exactly (cells[0] is empty before first |)
            row_id = cells[1].strip() if len(cells) > 1 else ""
            if row_id != unit_id:
                continue
            for j, cell in enumerate(cells):
                if cell.strip().lower() in recognized:
                    cells[j] = f" {new_status} "
                    lines[i] = "|".join(cells)
                    updated = True
                    break
            if updated:
                break

        if not updated:
            raise ValueError(f"Could not find unit '{unit_id}' with a recognized status in {backlog_index}")

        backlog_index.write_text("\n".join(lines) + "\n", encoding="utf-8")
        self.logger.info("Updated %s status to '%s' in %s", unit_id, new_status, backlog_index.name)

    def _append_comment(self, work_unit_path: Path, action: str, message: str) -> None:
        """Append a comment entry to the Comments section of a work-unit file."""
        timestamp = datetime.now(tz=UTC).strftime("%Y-%m-%d %H:%M UTC")
        entry = COMMENT_ENTRY_TEMPLATE.format(
            timestamp=timestamp,
            agent_id="backlog_manager",
            action=action,
            message=message,
        )

        content = work_unit_path.read_text(encoding="utf-8")

        comments_header = COMMENTS_SECTION_HEADER
        if comments_header in content:
            content = content.rstrip("\n") + "\n\n" + entry
        else:
            content = content.rstrip("\n") + "\n\n" + comments_header + "\n\n" + entry

        work_unit_path.write_text(content, encoding="utf-8")

    def _append_agent_comment(self, work_unit_path: Path, agent_name: str, message: str) -> None:
        """Append an agent comment using COMMENT_AGENT_TEMPLATE format.

        Writes: ``[YYYY-MM-DD HH:MM UTC] [agent/<agent_name>] <message>``

        Args:
            work_unit_path: Path to the work-unit ``.md`` file.
            agent_name: Agent name (e.g. ``git_ops``, ``orchestrator``).
            message: Message to append (may contain token and detail).
        """
        timestamp = datetime.now(tz=UTC).strftime("%Y-%m-%d %H:%M UTC")
        entry = COMMENT_AGENT_TEMPLATE.format(
            timestamp=timestamp,
            name=agent_name,
            message=message,
        )

        content = work_unit_path.read_text(encoding="utf-8")

        if COMMENTS_SECTION_HEADER in content:
            content = content.rstrip("\n") + "\n\n" + entry
        else:
            content = content.rstrip("\n") + "\n\n" + COMMENTS_SECTION_HEADER + "\n\n" + entry

        work_unit_path.write_text(content, encoding="utf-8")

    @staticmethod
    def _find_next_section_index(lines: list[str], start: int) -> int:
        """Return the line index of the next ``## `` heading after ``start``.

        Scans forward from ``start + 1`` (exclusive of the TDD Cycle Log heading
        itself) and returns the index of the first line whose first three
        characters are ``## `` (case-sensitive).  Returns ``-1`` when no such
        line exists.

        Args:
            lines: File content split into lines (no trailing newline on each).
            start: Index of the ``## TDD Cycle Log`` heading line.

        Returns:
            Index of the next ``## `` heading, or ``-1`` if none found.
        """
        for idx in range(start + 1, len(lines)):
            if lines[idx].startswith("## "):
                return idx
        return -1

    def _append_tdd_entry(self, work_unit_path: Path, phase: str, message: str) -> None:
        """Append a TDD phase entry to the TDD Cycle Log section of a work-unit file.

        Writes: ``- [<PHASE>] <ISO-8601 timestamp> -- <message>``

        The entry is inserted immediately before the next ``## `` heading after
        ``## TDD Cycle Log``.  When ``## TDD Cycle Log`` is the last section in
        the file (no following ``## `` heading exists), the entry is appended at
        EOF -- preserving the original edge-case behaviour.

        Exactly one blank line separates the new entry from the preceding
        content, and exactly one blank line separates the new entry from the
        following ``## `` heading (when one exists).

        Args:
            work_unit_path: Path to the work-unit ``.md`` file.
            phase: TDD phase -- must be one of ``RED``, ``GREEN``, or ``REFACTOR``
                (caller must pass normalized uppercase value).
            message: Description of the TDD phase outcome.

        Raises:
            ValueError: If the ``## TDD Cycle Log`` section does not exist in the file.
        """
        timestamp = datetime.now(tz=UTC).isoformat()
        entry = TDD_ENTRY_TEMPLATE.format(phase=phase, timestamp=timestamp, message=message)

        content = work_unit_path.read_text(encoding="utf-8")

        if TDD_CYCLE_LOG_SECTION_HEADER not in content:
            raise ValueError(
                f"'## TDD Cycle Log' section not found in {work_unit_path}. "
                "The section must already exist in the work unit file (it is defined in the task spec template)."
            )

        lines = content.splitlines()
        tdd_heading_idx = next(i for i, line in enumerate(lines) if line == TDD_CYCLE_LOG_SECTION_HEADER)
        next_section_idx = self._find_next_section_index(lines, tdd_heading_idx)

        if next_section_idx == -1:
            # ## TDD Cycle Log is the last section -- append to EOF.
            content = content.rstrip("\n") + "\n\n" + entry
        else:
            # Insert before the next ## heading, preserving exactly one blank
            # line above the entry and exactly one blank line below it.
            before_next = lines[:next_section_idx]
            # Strip trailing blank lines from the block before the next heading.
            while before_next and before_next[-1].strip() == "":
                before_next.pop()
            new_lines = before_next + ["", entry.rstrip("\n"), "", lines[next_section_idx]]
            new_lines.extend(lines[next_section_idx + 1 :])
            content = "\n".join(new_lines) + "\n"

        work_unit_path.write_text(content, encoding="utf-8")

    def _update_status_summary(self, backlog_index: Path) -> None:
        """Rewrite the Status Summary table section in BACKLOG.md.

        Reads all rows from the Full Work Unit Index, groups descendants by epic,
        counts statuses, and either inserts or replaces the ``## Status Summary``
        section immediately before ``## Full Work Unit Index``.

        An epic row is identified as any row whose ID matches the pattern
        ``E<digits>`` (no hyphen suffix). Counts are computed over all descendant
        rows -- rows whose ID starts with ``<epic-id>-``.
        """
        rows = self._parse_backlog_rows(backlog_index)
        epic_titles = self._parse_epic_titles(backlog_index)
        epic_counts = self._compute_epic_counts(rows)
        summary_block = self._build_summary_block(epic_counts, epic_titles)

        content = backlog_index.read_text(encoding="utf-8")

        # Remove any existing Status Summary section
        if STATUS_SUMMARY_SECTION_HEADER in content:
            content = self._strip_summary_section(content)

        # Insert before the Full Work Unit Index heading
        full_index_marker = "## Full Work Unit Index"
        if full_index_marker in content:
            content = content.replace(
                full_index_marker,
                summary_block + full_index_marker,
                1,
            )
        else:
            content = content.rstrip("\n") + "\n\n" + summary_block

        backlog_index.write_text(content, encoding="utf-8")

    def _parse_epic_titles(self, backlog_index: Path) -> dict[str, str]:
        """Parse epic IDs to their titles from BACKLOG.md table rows.

        Returns a dict mapping epic_id -> title string.
        """
        titles: dict[str, str] = {}
        content = backlog_index.read_text(encoding="utf-8")

        for line in content.splitlines():
            if not line.strip().startswith("|"):
                continue
            cells = [c.strip() for c in line.split("|")]
            if len(cells) < 4:
                continue
            row_id = cells[1]
            if EPIC_ID_RE.match(row_id):
                titles[row_id] = cells[2]

        return titles

    def _compute_epic_counts(self, rows: list[tuple[str, str, str]]) -> dict[str, dict[str, int]]:
        """Compute per-epic status counts from backlog rows.

        Returns a dict mapping epic_id -> {status: count} where status is one
        of ``done``, ``in-progress``, ``in-queue``, ``blocked``, ``declined``.
        Only descendant rows (those starting with ``<epic-id>-``) are counted;
        the epic row itself is excluded. Proposed children are not surfaced
        in the summary (they are inert drafts awaiting operator action).
        """
        epic_order: list[str] = []
        for row_id, _, _ in rows:
            if row_id and EPIC_ID_RE.match(row_id):
                epic_order.append(row_id)

        counts: dict[str, dict[str, int]] = {
            epic_id: {
                STATUS_DONE: 0,
                STATUS_IN_PROGRESS: 0,
                STATUS_IN_QUEUE: 0,
                STATUS_BLOCKED: 0,
                STATUS_DECLINED: 0,
            }
            for epic_id in epic_order
        }

        for row_id, status, _ in rows:
            if not row_id or not status:
                continue
            for epic_id in epic_order:
                if row_id.startswith(epic_id + "-"):
                    canonical_status = status.lower()
                    if canonical_status in counts[epic_id]:
                        counts[epic_id][canonical_status] += 1
                    break

        return counts

    def _build_summary_block(
        self,
        epic_counts: dict[str, dict[str, int]],
        epic_titles: dict[str, str],
    ) -> str:
        """Build the full Status Summary section as a markdown string."""
        table_rows = ""
        for epic_id, c in epic_counts.items():
            title = epic_titles.get(epic_id, "")
            table_rows += (
                f"| {epic_id} | {title} | {c[STATUS_DONE]} | "
                f"{c[STATUS_IN_PROGRESS]} | {c[STATUS_IN_QUEUE]} | "
                f"{c[STATUS_BLOCKED]} | {c[STATUS_DECLINED]} |\n"
            )

        return STATUS_SUMMARY_SECTION_HEADER + "\n\n" + STATUS_SUMMARY_TABLE_HEADER + table_rows + "\n"

    def _strip_summary_section(self, content: str) -> str:
        """Remove the existing Status Summary section from BACKLOG.md content."""
        without_summary = STRIP_SUMMARY_RE.sub("", content)
        # Collapse any resulting triple+ blank lines to double blank lines
        return re.sub(r"\n{3,}", "\n\n", without_summary)

    def _check_status_summary(
        self,
        backlog_index: Path,
        rows: list[tuple[str, str, str]],
        errors: list[str],
    ) -> None:
        """Check 5: Status Summary table exists and counts match the index."""
        content = backlog_index.read_text(encoding="utf-8")

        if STATUS_SUMMARY_SECTION_HEADER not in content:
            errors.append(
                "Status Summary section missing from BACKLOG.md -- run 'devbench validate-backlog' to regenerate it"
            )
            return

        # Compute expected counts and parse the actual table
        epic_counts = self._compute_epic_counts(rows)
        actual_counts = self._parse_summary_table(content)

        for epic_id, expected in epic_counts.items():
            if epic_id not in actual_counts:
                errors.append(f"Status Summary missing epic row '{epic_id}'")
                continue
            actual = actual_counts[epic_id]
            for status_key in (STATUS_DONE, STATUS_IN_PROGRESS, STATUS_IN_QUEUE, STATUS_BLOCKED, STATUS_DECLINED):
                if expected[status_key] != actual.get(status_key, -1):
                    errors.append(
                        f"Status Summary mismatch for {epic_id}: "
                        f"expected {status_key}={expected[status_key]}, "
                        f"got {actual.get(status_key, 'missing')}"
                    )

    def _parse_summary_table(self, content: str) -> dict[str, dict[str, int]]:
        """Parse the Status Summary table from BACKLOG.md content.

        Returns a dict mapping epic_id -> {status: count}.
        """
        result: dict[str, dict[str, int]] = {}

        in_summary = False
        for line in content.splitlines():
            if line.strip() == STATUS_SUMMARY_SECTION_HEADER:
                in_summary = True
                continue
            if in_summary and line.startswith("##"):
                break
            if not in_summary or not line.strip().startswith("|"):
                continue
            cells = [c.strip() for c in line.split("|")]
            # Splitting "|" around a pipe-delimited row produces empty flank
            # cells, so a 6-data-column row yields 8 cells total and a
            # 7-data-column row (with the new trailing Declined column)
            # yields 9. Both shapes are accepted for backward compatibility;
            # legacy rows default Declined to 0 until the backlog is
            # regenerated.
            if len(cells) < 7:
                continue
            row_id = cells[1]
            if not EPIC_ID_RE.match(row_id):
                continue
            try:
                declined_count = int(cells[7]) if len(cells) >= 9 else 0
                result[row_id] = {
                    STATUS_DONE: int(cells[3]),
                    STATUS_IN_PROGRESS: int(cells[4]),
                    STATUS_IN_QUEUE: int(cells[5]),
                    STATUS_BLOCKED: int(cells[6]),
                    STATUS_DECLINED: declined_count,
                }
            except (ValueError, IndexError):
                continue

        return result

    def _check_task_content(
        self,
        rows: list[tuple[str, str, str]],
        workspace_root: Path,
        errors: list[str],
    ) -> None:
        """Checks 6-10: validate content quality for task-level work units.

        Only task files (IDs ending with -T{n}) are checked. Epic, Feature,
        and Story files are skipped because they do not require the same
        level of detail.
        """
        for row_id, _, file_path_str in rows:
            if not row_id or row_id.startswith("-") or row_id.lower() == "id":
                continue
            if not file_path_str:
                continue
            if not self._is_task_id(row_id):
                continue

            wu_path = workspace_root / file_path_str
            if not wu_path.exists():
                continue  # Already reported by _check_files_and_statuses

            content = wu_path.read_text(encoding="utf-8")
            sections = self._extract_sections(content)

            # Check 6: non-empty Description
            if "Description" not in sections:
                errors.append(f"{row_id}: missing required '## Description' section")
            elif not sections["Description"].strip():
                errors.append(f"{row_id}: '## Description' section is empty")

            # Check 7: Acceptance Criteria with AC- items
            if "Acceptance Criteria" not in sections:
                errors.append(f"{row_id}: missing required '## Acceptance Criteria' section")
            elif "AC-" not in sections["Acceptance Criteria"]:
                errors.append(f"{row_id}: '## Acceptance Criteria' has no AC- items")

            # Check 8: Changes Manifest with entries
            if "Changes Manifest" not in sections:
                errors.append(f"{row_id}: missing required '## Changes Manifest' section")

            # Check 9: Definition of Done
            if "Definition of Done" not in sections:
                errors.append(f"{row_id}: missing required '## Definition of Done' section")

            # Check 10: no em-dash (U+2014)
            if EM_DASH in content:
                errors.append(f"{row_id}: contains em-dash character (U+2014) -- use double hyphen instead")

    def _check_manifest_path_prefixes(
        self,
        rows: list[tuple[str, str, str]],
        workspace_root: Path,
        errors: list[str],
    ) -> None:
        """Check 11: Changes Manifest paths must be repo-relative.

        For every task work unit whose target repo has ``checkout_directory``
        configured in ``backlog/config/devbench.yaml``, reject any manifest
        row whose path begins with ``<checkout_directory>/``. Such paths
        block at ``git-ops`` time because ``assert_staged_matches_manifest``
        (``src/devbench/backlog/manifest.py``) compares manifest entries
        against ``git diff --name-only`` output, which is always
        repo-relative.

        This is a state-based discriminator, not a silent-on-failure
        fallback: work units for repos without a configured
        ``checkout_directory`` are skipped because the check genuinely
        does not apply there (no prefix to compare against).
        """
        from devbench.backlog.manifest import parse_manifest
        from devbench.config import RUNTIME_CONFIG

        for row_id, _, file_path_str in rows:
            if not row_id or row_id.startswith("-") or row_id.lower() == "id":
                continue
            if not file_path_str:
                continue
            if not self._is_task_id(row_id):
                continue

            wu_path = workspace_root / file_path_str
            if not wu_path.exists():
                continue  # already reported by _check_files_and_statuses

            content = wu_path.read_text(encoding="utf-8")
            repo = self._extract_repo(content)
            if repo is None or repo not in RUNTIME_CONFIG.repos:
                continue

            checkout_dir = RUNTIME_CONFIG.repos[repo].checkout_directory
            if not checkout_dir:
                continue

            prefix = f"{checkout_dir.rstrip('/')}/"
            for manifest_row in parse_manifest(content):
                if manifest_row.file.startswith(prefix):
                    errors.append(
                        f"{row_id}: Changes Manifest path {manifest_row.file!r} "
                        f"begins with checkout_directory prefix {prefix!r}. "
                        f"Paths must be repo-relative (drop the prefix); "
                        f"see docs/backlog-contract.md."
                    )

    # Language tier classification used by the source-test pair and
    # language-AC-alignment rules. Mirrors docs/acceptance-criteria-canonical.md
    # tier table. Production-source globs identify paths whose Python files
    # require a sibling test entry in the same Manifest per
    # docs/source-test-atomicity.md; the test-path globs are the matching
    # locations the rule searches for that sibling.
    _PYTHON_EXTS: ClassVar[tuple[str, ...]] = (".py",)
    _NON_PY_EXTS_TO_TIER: ClassVar[dict[str, str]] = {
        ".hcl": "HCL",
        ".tf": "HCL",
        ".tfvars": "HCL",
        ".yaml": "YAML",
        ".yml": "YAML",
        ".json": "JSON",
        ".xml": "XML",
        ".toml": "TOML",
        ".md": "Markdown",
    }
    _PROD_SRC_PATTERNS: ClassVar[tuple[str, ...]] = (
        "src/",
        "infra/scripts/",
    )
    _PROD_SRC_NESTED_PATTERNS: ClassVar[tuple[str, ...]] = ("/src/",)
    _AC_FINAL_LANGUAGE_TIER_IDS: ClassVar[frozenset[str]] = frozenset(
        {
            "AC-FINAL-002",
            "AC-FINAL-003",
            "AC-FINAL-004",
            "AC-FINAL-005",
            "AC-FINAL-006",
            "AC-FINAL-008",
            "AC-FINAL-014",
        }
    )

    @classmethod
    def _classify_manifest_tier(cls, paths: list[str]) -> str:
        """Return the language tier for a Manifest's file paths.

        Returns ``"Python"`` if any path ends in ``.py``; otherwise the
        single non-Python tier matching all paths; otherwise ``"Mixed"``.
        Returns ``""`` if the path list is empty.
        """
        if not paths:
            return ""
        py_paths = [p for p in paths if any(p.lower().endswith(ext) for ext in cls._PYTHON_EXTS)]
        if py_paths:
            return "Python"
        tiers = set()
        for p in paths:
            lower = p.lower()
            for ext, tier in cls._NON_PY_EXTS_TO_TIER.items():
                if lower.endswith(ext):
                    tiers.add(tier)
                    break
        if len(tiers) == 1:
            return tiers.pop()
        if len(tiers) > 1:
            return "Mixed"
        return ""

    @classmethod
    def _is_production_source(cls, path: str) -> bool:
        """Return True if the path looks like production Python source.

        Used by the source-test pair rule to distinguish source files (which
        require a matching test entry) from one-off scripts in
        configuration-only directories. Test files themselves (paths under
        any ``tests/`` segment) are excluded -- they are not production source
        even when their extension is ``.py``.
        """
        if not any(path.lower().endswith(ext) for ext in cls._PYTHON_EXTS):
            return False
        # Exclude test files
        if path.startswith("tests/") or "/tests/" in path:
            return False
        # Exclude package marker files
        from pathlib import PurePosixPath

        if PurePosixPath(path).name == "__init__.py":
            return False
        return any(path.startswith(p) for p in cls._PROD_SRC_PATTERNS) or any(
            seg in path for seg in cls._PROD_SRC_NESTED_PATTERNS
        )

    @staticmethod
    def _is_real_manifest_path(path: str) -> bool:
        """Return True if the Manifest entry is a real file path.

        Filters out placeholder strings like ``(none)``, ``(no file changes;
        ...)``, etc. that documentation-only or verification-only tasks use to
        indicate an empty Manifest. Also filters out sentinel values like
        ``<verification-only>``, ``<decision-only>``, and any
        ``<name>``-shaped variant -- see ``devbench.backlog.sentinels`` for
        the canonical allowlist + pattern + rationale.
        """
        from devbench.backlog.sentinels import is_sentinel_manifest_value

        if not path:
            return False
        stripped = path.strip()
        if stripped.startswith("(") and stripped.endswith(")"):
            return False
        return not is_sentinel_manifest_value(stripped)

    def _check_manifest_conflicts(
        self,
        rows: list[tuple[str, str, str]],
        workspace_root: Path,
        errors: list[str],
    ) -> None:
        """Check 12: no two in-queue tasks in the SAME REPO own the same Manifest path.

        Per docs/backlog-contract.md "Manifest Conflict Rule": two in-queue
        tasks claiming ownership of the same path collide at git-ops time.
        Tasks with explicit Dependencies between them are exempt because the
        ordering resolves the conflict. The check is scoped by ``(repo, path)``
        because two tasks targeting different repos can legitimately list the
        same path (e.g., ``.devcontainer/devcontainer.json`` exists in both
        caylent-telemetry and the kanon repo's per-repo edits).
        """
        from devbench.backlog.manifest import ManifestParseError, parse_manifest

        # Map: (repo, path) -> list of (task_id, status)
        ownership: dict[tuple[str, str], list[tuple[str, str]]] = {}
        deps_by_task: dict[str, set[str]] = {}

        for row_id, status, file_path_str in rows:
            if not row_id or row_id.startswith("-") or row_id.lower() == "id":
                continue
            if not file_path_str or not self._is_task_id(row_id):
                continue
            wu_path = workspace_root / file_path_str
            if not wu_path.exists():
                continue
            content = wu_path.read_text(encoding="utf-8")
            repo = self._extract_repo(content) or ""
            try:
                manifest_rows = parse_manifest(content)
            except ManifestParseError:
                # Other validate rules (TestValidateContent) emit the missing
                # Manifest error; conflict detection treats unparseable
                # Manifests as having zero entries to avoid duplicate noise.
                manifest_rows = []
            for manifest_row in manifest_rows:
                if not self._is_real_manifest_path(manifest_row.file):
                    continue
                ownership.setdefault((repo, manifest_row.file), []).append((row_id, status))
            deps_by_task[row_id] = self._extract_dep_ids(content)

        for (repo, path), owners in ownership.items():
            if len(owners) < 2:
                continue
            # Filter to tasks not in done/declined/in-progress -- those are not
            # in flight any more or are actively being executed; the conflict
            # rule targets in-queue/proposed/blocked overlap.
            relevant = [(tid, st) for tid, st in owners if st in ("in-queue", "proposed", "blocked")]
            if len(relevant) < 2:
                continue
            # Check whether every pair is comparable via the transitive
            # dep graph (any DAG that totally orders the set is sufficient).
            ids = [tid for tid, _ in relevant]
            if self._tasks_form_dep_chain(ids, deps_by_task):
                continue
            sorted_ids = sorted(ids)
            chain_hint = "\n".join(
                f"    uv run devbench add-dep {later} {earlier}" for earlier, later in itertools.pairwise(sorted_ids)
            )
            errors.append(
                f"Manifest conflict on {path!r} in repo {repo or '(unknown)'}: "
                f"claimed by {', '.join(sorted_ids)}. Wire a serial dep chain:\n"
                f"{chain_hint}\n"
                f"  -- or any other DAG that totally orders the set. See "
                f"docs/backlog-contract.md 'Manifest Conflict Rule'."
            )

    @staticmethod
    def _extract_dep_ids(content: str) -> set[str]:
        """Return the set of task IDs in a work-unit's ## Dependencies table."""
        deps: set[str] = set()
        in_deps = False
        for line in content.splitlines():
            stripped = line.strip()
            if stripped.startswith("## "):
                in_deps = stripped.startswith("## Dependencies")
                continue
            if not in_deps:
                continue
            if not stripped.startswith("|"):
                continue
            # `startswith("|")` guarantees at least 2 cells after split.
            cells = [c.strip() for c in stripped.split("|")]
            cell = cells[1]
            if not cell or cell.lower() == "id" or cell.startswith("-"):
                continue
            if cell in DEPENDENCY_NONE_VALUES:
                continue
            # Cell may contain a comma-separated dep list
            for raw in cell.split(","):
                token = raw.strip()
                # Allow IDs with ":" suffix variants; keep core ID
                if not token:
                    continue
                # Validate against task-id shape
                if re.fullmatch(r"E\d+(-F\d+)?(-S\d+)?(-T\d+)?", token):
                    deps.add(token)
        return deps

    @staticmethod
    def _tasks_form_dep_chain(ids: list[str], deps: dict[str, set[str]]) -> bool:
        """Return True iff every id in ``ids`` is comparable via transitive reachability.

        Two task ids are *comparable* when one is reachable from the other by
        following dep edges. A dep chain that resolves ownership conflicts
        requires the conflict set to be totally ordered: every pair of
        claimants must be comparable in at least one direction. This is the
        canonical contract -- any DAG that totally orders the set (a clean
        N-1 chain, a branching DAG that merges, full N*(N-1)/2 pairwise
        edges) is accepted (issue #145).
        """
        if len(ids) < 2:
            return True
        id_set = set(ids)

        def reachable(start: str) -> set[str]:
            seen: set[str] = set()
            queue: deque[str] = deque([start])
            while queue:
                node = queue.popleft()
                for child in deps.get(node, set()):
                    if child not in id_set or child in seen:
                        continue
                    seen.add(child)
                    queue.append(child)
            return seen

        reach = {tid: reachable(tid) for tid in ids}
        for i, a in enumerate(ids):
            for b in ids[i + 1 :]:
                if b not in reach[a] and a not in reach[b]:
                    return False
        return True

    def _check_language_ac_alignment(
        self,
        rows: list[tuple[str, str, str]],
        workspace_root: Path,
        errors: list[str],
    ) -> None:
        """Check 13: AC-FINAL Python-tooling lines must carry the N/A suffix on non-Python tasks.

        Per docs/acceptance-criteria-canonical.md, AC-FINAL-002..005, 008,
        and 014 apply only to Python tasks. Non-Python tasks must append
        ``-- N/A for <tier> Tasks (no <language> source authored)`` so
        reviewers do not enforce inapplicable checks.
        """
        from devbench.backlog.manifest import ManifestParseError, parse_manifest

        for row_id, _, file_path_str in rows:
            if not row_id or row_id.startswith("-") or row_id.lower() == "id":
                continue
            if not file_path_str or not self._is_task_id(row_id):
                continue
            wu_path = workspace_root / file_path_str
            if not wu_path.exists():
                continue
            content = wu_path.read_text(encoding="utf-8")
            try:
                paths = [m.file for m in parse_manifest(content)]
            except ManifestParseError:
                continue
            tier = self._classify_manifest_tier(paths)
            if tier in ("", "Python", "Mixed"):
                # Mixed tasks have at least one .py file -> Python ACs apply
                # to that subset; do not emit warnings.
                continue

            # Walk AC-FINAL lines; for each Python-tier AC ID, require the
            # N/A suffix unless the line is missing entirely (handled by
            # other rules).
            for line in content.splitlines():
                stripped = line.strip()
                if not stripped.startswith("- ["):
                    continue
                m = re.match(r"^- \[[ x]\] (AC-FINAL-\d{3})\b", stripped)
                if not m:
                    continue
                ac_id = m.group(1)
                if ac_id not in self._AC_FINAL_LANGUAGE_TIER_IDS:
                    continue
                if "-- N/A" in stripped:
                    continue
                errors.append(
                    f"{row_id}: {ac_id} requires the N/A suffix on "
                    f"{tier}-tier task (no Python source in Manifest). "
                    f"Append '-- N/A for {tier} Tasks (no Python source authored)' "
                    f"per docs/acceptance-criteria-canonical.md."
                )

    def _check_source_test_pairs(
        self,
        rows: list[tuple[str, str, str]],
        workspace_root: Path,
        errors: list[str],
    ) -> None:
        """Check 14: every production-source .py file in a Manifest needs a matching test entry.

        Per docs/source-test-atomicity.md, splitting source and test across
        sibling tasks causes AC-FINAL-014 (100% coverage) to fail in the
        source-authoring task. The rule ensures the test pair is in the
        same Manifest.
        """
        from devbench.backlog.manifest import ManifestParseError, parse_manifest

        for row_id, _, file_path_str in rows:
            if not row_id or row_id.startswith("-") or row_id.lower() == "id":
                continue
            if not file_path_str or not self._is_task_id(row_id):
                continue
            wu_path = workspace_root / file_path_str
            if not wu_path.exists():
                continue
            content = wu_path.read_text(encoding="utf-8")
            try:
                paths = [m.file for m in parse_manifest(content)]
            except ManifestParseError:
                continue
            for source_path in paths:
                if not self._is_production_source(source_path):
                    continue
                source_stem = self._source_stem_for_pair_match(source_path)
                if not source_stem:
                    continue
                # A test pair is any `test_*.py` (or `*_test.py`) entry in the
                # SAME Manifest whose basename contains the source stem as a
                # substring. This accepts project naming conventions such as
                # `test_telemetry_event.py` for `event.py`, while still
                # catching the split-Manifest anti-pattern (where the test
                # file lives in a sibling task's Manifest entirely).
                has_pair = any(self._test_filename_pairs_with_stem(p, source_stem) for p in paths)
                if not has_pair:
                    errors.append(
                        f"{row_id}: production source {source_path!r} has no "
                        f"matching test in the same Manifest "
                        f"(expected a test_*.py whose basename contains "
                        f"{source_stem!r}, e.g., tests/unit/test_{source_stem}.py). "
                        f"Add the test entry per docs/source-test-atomicity.md."
                    )

    # ------------------------------------------------------------------
    # E209: Backlog-Contract Alignment hardening rules
    # ------------------------------------------------------------------

    _REQUIRED_TASK_SECTIONS: ClassVar[tuple[str, ...]] = ("Status", "Dependencies", "Changes Manifest")
    # Accepts the canonical Epic shape ``E\d+`` plus the test-harness
    # convention ``EX``. Anything else (a free-text dep, a typo) fails
    # the rule. Mirrors the ``EPIC_ID_RE`` shape in constants.py while
    # adding the optional Feature/Story/Task suffixes.
    _DEP_ID_PATTERN: ClassVar[re.Pattern[str]] = re.compile(r"^E[A-Z0-9]+(-F\d+)?(-S\d+)?(-T\d+)?$")

    def _check_required_sections(
        self,
        rows: list[tuple[str, str, str]],
        workspace_root: Path,
        errors: list[str],
    ) -> None:
        """Check 15: every Task work-unit file declares Status, Dependencies, and Changes Manifest.

        Epic, Feature, and Story rows are exempt (their bodies are
        scaffolding -- a Manifest does not apply). Tasks that miss any
        required section receive an explicit error naming the section,
        so authors know exactly what to add.
        """
        for row_id, _, file_path_str in rows:
            if not row_id or row_id.startswith("-"):
                continue
            if not self._is_task_id(row_id):
                continue
            if not file_path_str:
                continue
            wu_path = workspace_root / file_path_str
            if not wu_path.is_file():
                continue
            content = wu_path.read_text(encoding="utf-8")
            sections = self._extract_sections(content)
            for required in self._REQUIRED_TASK_SECTIONS:
                if required not in sections:
                    errors.append(
                        f"{row_id}: missing required section '## {required}'. "
                        f"Add the section per docs/backlog-contract.md."
                    )

    def _check_status_enum(
        self,
        rows: list[tuple[str, str, str]],
        workspace_root: Path,
        errors: list[str],
    ) -> None:
        """Check 16: every parsed ``## Status:`` value is in ``VALID_STATUSES``.

        The CLI's ``set-status`` and ``mark-*`` helpers reject invalid
        values at write time, but a hand-edited file can drift; this
        check catches the drift at validate-backlog time so the
        orchestrator never sees a bad status mid-run.
        """
        for row_id, _, file_path_str in rows:
            if not row_id or row_id.startswith("-"):
                continue
            if not file_path_str:
                continue
            wu_path = workspace_root / file_path_str
            if not wu_path.is_file():
                continue
            content = wu_path.read_text(encoding="utf-8")
            match = STATUS_LINE_RE.search(content)
            if match is None:
                # Missing status line is reported by _check_files_and_statuses;
                # don't double-report here.
                continue
            raw_status = match.group(2).strip().lower()
            if raw_status not in VALID_STATUSES:
                errors.append(
                    f"{row_id}: invalid '## Status:' value {raw_status!r}. Allowed values: {sorted(VALID_STATUSES)}."
                )

    def _check_dep_id_format(
        self,
        rows: list[tuple[str, str, str]],
        workspace_root: Path,
        errors: list[str],
    ) -> None:
        """Check 17: every ``## Dependencies`` row's first cell is a valid task-ID shape.

        ``DEPENDENCY_NONE_VALUES`` cells (``None`` / ``-``) are skipped.
        Header rows (``ID``, separator dashes) are skipped. Anything
        else must match the canonical ID regex
        ``E\\d+(-F\\d+)?(-S\\d+)?(-T\\d+)?``; mismatches produce an
        explicit error with the offending row text so authors can fix
        the typo.
        """
        for row_id, _, file_path_str in rows:
            if not row_id or row_id.startswith("-"):
                continue
            if not file_path_str:
                continue
            wu_path = workspace_root / file_path_str
            if not wu_path.is_file():
                continue
            content = wu_path.read_text(encoding="utf-8")
            for dep_id in self._iter_dep_ids(content):
                if not self._DEP_ID_PATTERN.match(dep_id):
                    errors.append(
                        f"{row_id}: dependency ID {dep_id!r} does not match the "
                        f"canonical task-ID regex E<n>[-F<n>][-S<n>][-T<n>]. "
                        f"Fix the row in '## Dependencies' or remove it."
                    )

    def _check_branch_uniqueness(
        self,
        rows: list[tuple[str, str, str]],
        workspace_root: Path,
        errors: list[str],
    ) -> None:
        """Check 18 (E219): no two Tasks derive the same branch name.

        Each Task's branch is either an explicit ``- **Branch:**
        `backlog/<id>``` line in the work-unit file or the canonical
        ``backlog/<id-lowercase>`` derivation. A collision means two
        Tasks would push to the same branch, breaking auto-merge and
        producing false review failures.

        Skipped entirely when single-PR mode is active (the
        ``git_ops.single_branch`` yaml field is set), since every task
        legitimately shares the configured branch.
        """
        from devbench.config import RUNTIME_CONFIG

        single_branch = getattr(RUNTIME_CONFIG.git_ops, "single_branch", None)
        if single_branch:
            return
        branches: dict[str, list[str]] = {}
        for row_id, _, file_path_str in rows:
            if not row_id or row_id.startswith("-"):
                continue
            if not self._is_task_id(row_id):
                continue
            branch = self._derive_branch_for_row(row_id, file_path_str, workspace_root)
            branches.setdefault(branch, []).append(row_id)
        for branch, ids in sorted(branches.items()):
            if len(ids) > 1:
                errors.append(
                    f"Branch collision on {branch!r}: claimed by {sorted(ids)}. "
                    f"Rename one of the work units (or its explicit '**Branch:**' override) "
                    f"so each task pushes to a unique branch; see docs/backlog-contract.md "
                    f"'Branch Uniqueness Rule'."
                )

    # Issue #117: the changes_manifest reviewer was passing work units whose
    # Manifest still contained the placeholder row authors are supposed to
    # replace. Catch the placeholder at validate-backlog time so the orchestrator
    # never even claims the task; the executor cannot build atop a TBD manifest.
    _PLACEHOLDER_MANIFEST_RE: ClassVar[re.Pattern[str]] = re.compile(r"^TBD\b", re.IGNORECASE)
    _ACTIVE_TASK_STATUSES_FOR_PLACEHOLDER_CHECK: ClassVar[frozenset[str]] = frozenset(
        {STATUS_IN_QUEUE, STATUS_IN_PROGRESS, STATUS_BLOCKED}
    )

    def _check_no_placeholder_manifest_rows(
        self,
        rows: list[tuple[str, str, str]],
        workspace_root: Path,
        errors: list[str],
    ) -> None:
        """Issue #117: reject work units whose Manifest still contains a TBD row.

        The canonical placeholder reads ``TBD | Executor agent: replace
        this row with the actual files to be created or modified.`` The
        check fires for every active Task (in-queue / in-progress /
        blocked); ``in-review`` and terminal statuses are skipped because
        the executor has either already amended the Manifest or the task
        is closed.

        Each offending row produces an error naming the task ID and the
        first cell text so the operator (or the auto-amender) can fix
        it before any agent claims the unit.
        """
        for row_id, status, file_path_str in rows:
            if not row_id or row_id.startswith("-"):
                continue
            if not self._is_task_id(row_id):
                continue
            if not file_path_str:
                continue
            if status.lower() not in self._ACTIVE_TASK_STATUSES_FOR_PLACEHOLDER_CHECK:
                continue
            wu_path = workspace_root / file_path_str
            if not wu_path.is_file():
                continue
            content = wu_path.read_text(encoding="utf-8")
            placeholder_cell = self._first_placeholder_manifest_cell(content)
            if placeholder_cell:
                errors.append(
                    f"{row_id}: Changes Manifest still has placeholder row "
                    f"{placeholder_cell!r}. Replace with real file entries before "
                    f"claim; see docs/backlog-contract.md 'No Placeholder Rows Rule'."
                )

    @classmethod
    def _first_placeholder_manifest_cell(cls, content: str) -> str:
        """Return the first ``TBD`` cell in ``## Changes Manifest`` or ``""``.

        Walks the Manifest table once: skips the section heading line,
        the header row (cells like ``File`` / ``Change``), and any
        separator row (cells starting with ``-``). The first data row
        whose cell-1 starts with ``TBD`` (case-insensitive) is returned
        verbatim; any other row terminates the scan.
        """
        in_manifest = False
        seen_header = False
        for raw in content.splitlines():
            stripped = raw.strip()
            if stripped.startswith("## "):
                in_manifest = stripped.startswith("## Changes Manifest")
                seen_header = False
                continue
            if not in_manifest or not stripped.startswith("|"):
                continue
            cells = [c.strip() for c in stripped.split("|")]
            cell = cells[1]
            if not cell or cell.startswith("-"):
                continue
            if not seen_header:
                # First non-separator row is the header; skip it.
                seen_header = True
                continue
            if cls._PLACEHOLDER_MANIFEST_RE.match(cell):
                return cell
        return ""

    # Rule 20: orphan path tokens in AC / DoD. The Manifest is the single
    # source of truth for files a Task produces; AC / DoD that restate
    # paths is redundancy that drifts. The check is gated by
    # ``RUNTIME_CONFIG.validate.check_orphan_path_tokens`` so existing
    # backlogs see no behaviour change until they opt in.
    #
    # Token regex: a single-backtick group whose body has no whitespace.
    # The optional second group captures a trailing `` (ref)`` marker --
    # an inline escape hatch declaring the token a read-only reference
    # (e.g. an external config file the Task reads but does not modify).
    _ORPHAN_TOKEN_RE: ClassVar[re.Pattern[str]] = re.compile(r"`([^`\s]+)`(\s*\(ref\))?")
    _ORPHAN_PATH_EXTS: ClassVar[tuple[str, ...]] = (
        ".md",
        ".py",
        ".ts",
        ".tsx",
        ".js",
        ".jsx",
        ".go",
        ".java",
        ".kt",
        ".rb",
        ".rs",
        ".swift",
        ".cpp",
        ".c",
        ".h",
        ".hpp",
        ".yaml",
        ".yml",
        ".toml",
        ".json",
        ".xml",
        ".tf",
        ".tfvars",
        ".hcl",
        ".sh",
        ".dockerfile",
        ".html",
        ".css",
        ".scss",
        ".sql",
    )
    _ORPHAN_KNOWN_PREFIXES: ClassVar[tuple[str, ...]] = (
        "src/",
        "tests/",
        "test/",
        "infra/",
        "docs/",
        "backlog/",
        "config/",
    )

    @classmethod
    def _is_path_shaped(cls, token: str, manifest_dir_prefixes: set[str]) -> bool:
        """Return ``True`` if ``token`` looks like a single-file path reference.

        Excludes shell flags, key=value forms, glob patterns, URL-scheme
        tokens, and tokens with no path-like marker. Includes tokens
        ending in a known extension, starting with a built-in directory
        prefix, or starting with a directory prefix observed anywhere in
        the Task's parsed Manifest. Uses no domain knowledge -- the
        prefix and extension lists are static.

        Bare extensions like ``.md`` (3 chars, no filename stem, no
        directory) appear in prose like "only ``.md`` files modified" and
        are NOT path references. Tokens that end in a known extension
        must have either a ``/`` separator OR at least one alphanumeric
        character in the stem to qualify as a path.
        """
        if not token or token.startswith("-") or "=" in token or "*" in token or "://" in token:
            return False
        lower = token.lower()
        for ext in cls._ORPHAN_PATH_EXTS:
            if lower.endswith(ext):
                stem = token[: -len(ext)]
                # Require a directory separator OR a real filename stem.
                # Bare ``.md`` / ``.py`` / etc. (literal extension in prose)
                # have an empty stem and MUST NOT be treated as paths.
                return "/" in stem or bool(stem and any(c.isalnum() for c in stem))
        if any(token.startswith(p) for p in cls._ORPHAN_KNOWN_PREFIXES):
            return True
        return any(token.startswith(prefix) for prefix in manifest_dir_prefixes)

    @staticmethod
    def _normalise_orphan_path(token: str, checkout_dir: str | None) -> str:
        """Strip leading ``./``, optional ``checkout_dir/`` prefix, and trailing ``/``.

        Mirrors the normalisation rule 11 applies to Manifest paths so
        AC / DoD tokens authored with the prefix still match Manifest
        entries that the prefix-rule fixer stripped.
        """
        s = token
        if s.startswith("./"):
            s = s[2:]
        if checkout_dir:
            prefix = checkout_dir.rstrip("/") + "/"
            if s.startswith(prefix):
                s = s[len(prefix) :]
        return s.rstrip("/")

    @classmethod
    def _iter_orphan_candidates(cls, section_body: str) -> "list[tuple[str, bool]]":
        """Yield ``(token, has_ref_marker)`` for every backtick-quoted no-whitespace token.

        Does not filter for path-shape -- the caller decides which tokens
        are path-shaped relative to the Task's Manifest. The boolean is
        ``True`` when the token is immediately followed by ``(ref)``,
        signalling the inline read-only escape hatch.
        """
        out: list[tuple[str, bool]] = []
        for match in cls._ORPHAN_TOKEN_RE.finditer(section_body):
            token = match.group(1)
            has_ref = match.group(2) is not None
            out.append((token, has_ref))
        return out

    def _check_no_orphan_path_tokens(
        self,
        rows: list[tuple[str, str, str]],
        workspace_root: Path,
        errors: list[str],
    ) -> None:
        """Rule 20: AC / DoD must not name paths that are absent from the Changes Manifest.

        For every Task work-unit (Stories/Features/Epics short-circuit;
        they have no Manifest), extract the ``## Acceptance Criteria``
        and ``## Definition of Done`` section bodies and walk every
        backtick-quoted no-whitespace token. If a token is path-shaped
        (per ``_is_path_shaped``) and is not present in the Task's
        parsed Manifest after normalisation -- and is not declared
        read-only via the inline ``(ref)`` marker -- emit an integrity
        error.

        Gated by ``RUNTIME_CONFIG.validate.check_orphan_path_tokens``;
        returns immediately when the flag is ``False`` (the default),
        so this rule is invisible to backlogs that have not opted in.
        """
        from devbench.config import RUNTIME_CONFIG

        if not getattr(RUNTIME_CONFIG.validate, "check_orphan_path_tokens", False):
            return

        for row_id, _, file_path_str in rows:
            if not row_id or row_id.startswith("-") or row_id.lower() == "id":
                continue
            if not file_path_str or not self._is_task_id(row_id):
                continue
            wu_path = workspace_root / file_path_str
            if not wu_path.is_file():
                continue
            self._check_one_task_orphan_paths(row_id, wu_path, errors)

    def _check_one_task_orphan_paths(
        self,
        row_id: str,
        wu_path: Path,
        errors: list[str],
    ) -> None:
        """Per-task body for ``_check_no_orphan_path_tokens``."""
        from devbench.backlog.manifest import ManifestParseError, parse_manifest
        from devbench.config import RUNTIME_CONFIG

        content = wu_path.read_text(encoding="utf-8")
        try:
            manifest_rows = parse_manifest(content)
        except ManifestParseError:
            # Already reported by other rules; skip this task quietly.
            return

        repo = self._extract_repo(content)
        checkout_dir: str | None = None
        if repo is not None and repo in RUNTIME_CONFIG.repos:
            checkout_dir = RUNTIME_CONFIG.repos[repo].checkout_directory

        manifest_paths = {
            self._normalise_orphan_path(r.file, checkout_dir)
            for r in manifest_rows
            if self._is_real_manifest_path(r.file)
        }
        manifest_dir_prefixes = {p.split("/", 1)[0] + "/" for p in manifest_paths if "/" in p}

        sections = self._extract_sections(content)
        for section_name in ("Acceptance Criteria", "Definition of Done"):
            body = sections.get(section_name, "")
            if not body:
                continue
            for token, has_ref in self._iter_orphan_candidates(body):
                if has_ref or not self._is_path_shaped(token, manifest_dir_prefixes):
                    continue
                normalised = self._normalise_orphan_path(token, checkout_dir)
                if normalised in manifest_paths:
                    continue
                errors.append(
                    f"{row_id}: orphan path {token!r} in '## {section_name}' "
                    f"not in Changes Manifest. Either add the path to the "
                    f"Manifest, rewrite the AC/DoD line behaviourally "
                    f"(reference the Manifest symbolically), or suffix the "
                    f"token with ' (ref)' to declare it a read-only "
                    f"reference. See docs/backlog-contract.md "
                    f"'No Orphan Path Tokens Rule'."
                )

    @staticmethod
    def _derive_branch_for_row(unit_id: str, file_path_str: str, workspace_root: Path) -> str:
        """Resolve the branch name a Task row would push to.

        Mirrors ``BacklogParser._parse_branch``: prefer an explicit
        ``- **Branch:** \\`<name>\\``` line in the work-unit file; fall
        back to the canonical lowercase-ID template when the explicit
        line is absent or unreadable.
        """
        if file_path_str:
            wu_path = workspace_root / file_path_str
            if wu_path.is_file():
                content = wu_path.read_text(encoding="utf-8")
                explicit = re.search(r"-\s+\*?\*?Branch:?\*?\*?\s*`([^`]+)`", content)
                if explicit:
                    return explicit.group(1).strip()
        return f"backlog/{unit_id.lower()}"

    @classmethod
    def _iter_dep_ids(cls, content: str) -> list[str]:
        """Yield every dep-ID candidate in a work-unit's ``## Dependencies`` table.

        Returns the list of cell-1 strings (after stripping). Header
        rows, separator rows, and ``DEPENDENCY_NONE_VALUES`` cells are
        excluded. Comma-separated tokens within a single cell are
        split so ``| E1, E2 |`` yields two candidates. Mirrors the
        parsing rules used by the runtime ``_extract_dep_ids`` helper
        but exposed for the format-check rule.
        """
        deps: list[str] = []
        in_deps = False
        for raw in content.splitlines():
            stripped = raw.strip()
            if stripped.startswith("## "):
                in_deps = stripped.startswith("## Dependencies")
                continue
            if not in_deps or not stripped.startswith("|"):
                continue
            cells = [c.strip() for c in stripped.split("|")]
            cell = cells[1]
            if not cell or cell.lower() == "id" or cell.startswith("-"):
                continue
            if cell in DEPENDENCY_NONE_VALUES:
                continue
            for raw_token in cell.split(","):
                token = raw_token.strip()
                if token:
                    deps.append(token)
        return deps

    @staticmethod
    def _source_stem_for_pair_match(source_path: str) -> str:
        """Return the basename stem (without ``.py``) for source-test pairing.

        Returns ``""`` for ``__init__.py`` and non-Python paths so callers
        can skip the pairing check on packaging-only entries.
        """
        base = source_path.rsplit("/", 1)[-1]
        if base == "__init__.py" or not base.endswith(".py"):
            return ""
        return base[:-3]

    @staticmethod
    def _test_filename_pairs_with_stem(path: str, source_stem: str) -> bool:
        """Return True if ``path`` is a test file whose basename references ``source_stem``.

        Accepts both ``test_<...>.py`` and ``<...>_test.py`` patterns and
        requires ``source_stem`` to appear as a substring of the basename
        (minus extension and any ``test_`` / ``_test`` framing).
        """
        base = path.rsplit("/", 1)[-1]
        if not base.endswith(".py") or base == "__init__.py":
            return False
        stem = base[:-3]
        if stem.startswith("test_"):
            inner = stem[len("test_") :]
        elif stem.endswith("_test"):
            inner = stem[: -len("_test")]
        else:
            return False
        return source_stem in inner.split("_") or source_stem in inner

    @staticmethod
    def _extract_repo(content: str) -> str | None:
        """Extract the canonical ``org/repo`` string from a work-unit ``## Target Repository`` section.

        Returns ``None`` if the section is absent or malformed; callers
        treat that case as "check does not apply".
        """
        m = re.search(r"\*\*Repo:\*\*\s*`([^`]+)`", content)
        return m.group(1) if m else None

    @staticmethod
    def _is_task_id(unit_id: str) -> bool:
        """Return True if the ID represents a task (contains -T followed by digits)."""
        parts = unit_id.split("-")
        return any(p.startswith("T") and p[1:].isdigit() for p in parts)

    @staticmethod
    def _extract_sections(content: str) -> dict[str, str]:
        """Extract ## sections from markdown content into a dict.

        Keys are section names (without '## ' prefix), values are the
        body text between this heading and the next ## heading.
        """
        sections: dict[str, str] = {}
        current_name: str | None = None
        current_lines: list[str] = []

        for line in content.splitlines():
            if line.startswith("## "):
                if current_name is not None:
                    sections[current_name] = "\n".join(current_lines)
                heading = line[3:].strip()
                # Handle "## Status: in-queue" -> key "Status"
                current_name = heading.split(":")[0].strip()
                current_lines = []
            elif current_name is not None:
                current_lines.append(line)

        if current_name is not None:
            sections[current_name] = "\n".join(current_lines)

        return sections
