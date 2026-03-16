"""Backlog manager that updates work-unit statuses and traceability.

Owns the backlog lifecycle: status writes, done-gate checks, rollups,
comments, backlog validation, and traceability logging.

Public API
----------
``force_status``              — write any status to both files with no gate
                                checks.  Use for automated lifecycle transitions
                                (in-progress, in-review) and manual recovery.
``mark_done``                 — gated completion: verifies all required review
                                judges passed before writing ``done``.
``mark_blocked``              — writes ``blocked`` and appends a reason comment.
``validate``                  — returns integrity errors (missing files, status
                                drift, orphans, broken deps, Status Summary count
                                drift, and missing required section headers).
``log_to_traceability_matrix``— appends a spec/test mapping entry to the
                                traceability matrix file.

Constructor
-----------
``BacklogManager(logger=None)``
    Accepts an optional ``logging.Logger`` instance.  Defaults to
    ``logging.getLogger("devbench.backlog_manager")`` when omitted.

All writes go through the private ``_set_status`` workhorse which updates
both the work-unit file and BACKLOG.md atomically.

Validation Checks
-----------------
``validate`` runs six integrity checks in order:

1. Every row in BACKLOG.md has a corresponding work unit file.
2. Every work unit file's status matches the index.
3. No orphaned work unit files (in workspace_root/backlog/ but not in index).
4. All dependency IDs reference real work unit IDs in the index.
5. Status Summary counts match actual per-status counts in the Full Work Unit
   Index.  Only status columns present in the Summary table are compared.
   ``blocked`` units count toward their respective status column.  This check
   is silently skipped when no Status Summary section is found.
6. Every work unit file contains a ``## Comments`` section header.  This is
   required by the backlog contract so that agents have a designated location
   to append log entries.

The full runtime status vocabulary is: ``in-queue``, ``in-progress``,
``in-review``, ``done``, ``blocked``.  All five values are valid in both the
work-unit file and the BACKLOG.md index; status mismatches are reported
regardless of which status value is involved.
"""

import contextlib
import logging
import re
from datetime import UTC, datetime
from pathlib import Path

from devbench.constants import (
    BACKLOG_STATUS_RE,
    BACKLOG_SUBDIR,
    COMMENT_ENTRY_TEMPLATE,
    COMMENTS_SECTION_HEADER,
    DEPENDENCY_NONE_VALUES,
    REVIEW_JUDGE_NAMES,
    STATUS_BLOCKED,
    STATUS_DONE,
    STATUS_LINE_RE,
    TABLE_STATUS_VALUES,
    TRACEABILITY_MATRIX_HEADER,
    VALID_STATUSES,
)


class BacklogManager:
    """Owns backlog lifecycle: status writes, done-gate checks, rollups, comments, and validation."""

    def __init__(self, logger: logging.Logger | None = None) -> None:
        """Initialize the manager.

        Args:
            logger: Optional logger instance.  Defaults to
                ``logging.getLogger("devbench.backlog_manager")`` when omitted.
        """
        self.logger = logger or logging.getLogger("devbench.backlog_manager")

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
        for agent-driven completion — it enforces that all judges have passed.

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

    def validate(self, backlog_index: Path, workspace_root: Path) -> list[str]:
        """Check backlog integrity and return a list of error messages.

        Checks performed:
        1. Every row in BACKLOG.md has a corresponding work unit file.
        2. Every work unit file's status matches the index.
        3. No orphaned work unit files (in workspace_root/backlog/ but not in index).
        4. All dependency IDs reference real work unit IDs in the index.
        5. Status Summary counts match the actual per-status counts in the index.
           Only status columns present in the Summary table are compared.
           Silently skipped when no Status Summary section is found.
        6. Every work unit file contains a ``## Comments`` section header.

        Args:
            backlog_index: Path to the ``BACKLOG.md`` index file.
            workspace_root: Workspace root containing BACKLOG.md and the backlog/ subdirectory.

        Returns:
            A list of error strings. Empty list means the backlog is valid.
        """
        rows = self._parse_backlog_rows(backlog_index)
        known_ids = {row_id for row_id, _, _ in rows if row_id and not row_id.startswith("-")}

        errors: list[str] = []
        indexed_files = self._check_files_and_statuses(rows, workspace_root, errors)
        self._check_orphans(workspace_root, indexed_files, errors)
        self._check_dependencies(backlog_index, known_ids, errors)
        self._check_status_summary_counts(backlog_index, rows, errors)
        self._check_required_section_headers(rows, workspace_root, errors)
        return errors

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
                errors.append(f"{row_id}: work unit file missing — expected {file_path_str}")
                continue

            content = wu_path.read_text(encoding="utf-8")
            m = BACKLOG_STATUS_RE.search(content)
            if m:
                file_status = m.group(1).strip().lower()
                if file_status != index_status:
                    errors.append(
                        f"{row_id}: status mismatch — index has '{index_status}', file has '{file_status}'"
                    )
            else:
                errors.append(f"{row_id}: work unit file missing '## Status:' line")
        return indexed_files

    def _check_orphans(
        self, workspace_root: Path, indexed_files: set[Path], errors: list[str]
    ) -> None:
        """Check 3: no orphaned work unit files."""
        backlog_dir = workspace_root / BACKLOG_SUBDIR
        if backlog_dir.exists():
            for wu_file in backlog_dir.glob("*.md"):
                if wu_file.resolve() not in indexed_files:
                    errors.append(f"{wu_file.name}: orphaned work unit file not in BACKLOG.md")

    def _check_dependencies(
        self, backlog_index: Path, known_ids: set[str], errors: list[str]
    ) -> None:
        """Check 4: all dependency IDs reference real IDs."""
        content = backlog_index.read_text(encoding="utf-8")
        for line in content.splitlines():
            if not line.strip().startswith("|"):
                continue
            cells = [c.strip() for c in line.split("|")]
            if len(cells) != 9:  # work-unit index rows have exactly 7 columns
                continue
            row_id = cells[1]
            if not row_id or row_id.lower() == "id" or row_id.startswith("-"):
                continue
            dep_cell = cells[5]
            for raw_dep in dep_cell.split(","):
                dep_id = raw_dep.strip()
                if dep_id and dep_id.lower() not in DEPENDENCY_NONE_VALUES and dep_id not in known_ids:
                    errors.append(
                        f"{row_id}: dependency '{dep_id}' not found in backlog index"
                    )

    def _check_status_summary_counts(
        self,
        backlog_index: Path,
        rows: list[tuple[str, str, str]],
        errors: list[str],
    ) -> None:
        """Check 5: Status Summary counts match actual per-status counts in the index.

        Parses the ``## Status Summary`` section from ``backlog_index`` and extracts
        the total count for each status column present in that table.  Counts the
        corresponding statuses from ``rows`` (the parsed Full Work Unit Index), then
        reports an error for every column where the declared count differs from the
        actual count.

        Only columns present in the Summary table header are checked.  Columns not
        present in the header are ignored.  When no Status Summary section is found,
        this check is silently skipped.

        Args:
            backlog_index: Path to the ``BACKLOG.md`` index file.
            rows: Parsed ``(id, status, file_path)`` tuples from the Full Work Unit Index.
            errors: Mutable list to which error strings are appended.
        """
        status_col_indices = self._parse_summary_status_columns(backlog_index)
        if status_col_indices is None:
            return

        declared_counts = self._sum_summary_declared_counts(backlog_index, status_col_indices)
        actual_counts = self._count_index_statuses(rows, status_col_indices)

        for status in status_col_indices:
            declared = declared_counts[status]
            actual = actual_counts[status]
            if declared != actual:
                errors.append(
                    f"Status Summary count mismatch for '{status}': "
                    f"summary declares {declared} but index has {actual}"
                )

    @staticmethod
    def _parse_summary_table_rows(summary_text: str) -> tuple[list[str], list[list[str]]]:
        """Parse a markdown table from summary_text into a header row and data rows.

        Returns a tuple of (header_cells, data_rows) where header_cells is a list
        of lowercase column names and data_rows is a list of cell lists.  Separator
        rows (all-dash cells) are excluded.  Returns empty lists when no table is found.
        """
        header_row: list[str] = []
        data_rows: list[list[str]] = []
        for line in summary_text.splitlines():
            stripped = line.strip()
            if not stripped.startswith("|"):
                continue
            cells = [c.strip() for c in stripped.split("|") if c.strip()]
            if not cells:
                continue
            if all(set(c) <= {"-", " "} for c in cells):
                continue
            if not header_row:
                header_row = [c.lower() for c in cells]
            else:
                data_rows.append(cells)
        return header_row, data_rows

    def _parse_summary_status_columns(self, backlog_index: Path) -> dict[str, int] | None:
        """Return a mapping of canonical status name -> column index from the Status Summary.

        Returns ``None`` when no Status Summary section or no recognisable status
        columns are found, indicating the count check should be skipped.

        The column name mapping normalises display names (e.g. ``"in queue"``) to
        canonical hyphenated names (e.g. ``"in-queue"``).
        """
        content = backlog_index.read_text(encoding="utf-8")
        summary_match = re.search(
            r"^##\s+Status Summary\s*\n(.*?)(?=^##\s|\Z)",
            content,
            re.MULTILINE | re.DOTALL,
        )
        if summary_match is None:
            return None

        header_row, _ = self._parse_summary_table_rows(summary_match.group(1))
        if not header_row:
            return None

        display_to_canonical: dict[str, str] = {
            "in queue": "in-queue",
            "in progress": "in-progress",
            "in review": "in-review",
            "done": "done",
            "blocked": "blocked",
        }
        status_col_indices: dict[str, int] = {
            display_to_canonical[col_name]: col_idx
            for col_idx, col_name in enumerate(header_row)
            if col_name in display_to_canonical
        }
        return status_col_indices if status_col_indices else None

    def _sum_summary_declared_counts(
        self,
        backlog_index: Path,
        status_col_indices: dict[str, int],
    ) -> dict[str, int]:
        """Sum declared counts from the Status Summary table for each status column.

        Non-numeric cells (e.g. bold ``**Total**`` rows) are silently ignored.

        Args:
            backlog_index: Path to the ``BACKLOG.md`` index file.
            status_col_indices: Mapping from canonical status name to column index.

        Returns:
            A dict mapping each status to its declared total count.
        """
        content = backlog_index.read_text(encoding="utf-8")
        summary_match = re.search(
            r"^##\s+Status Summary\s*\n(.*?)(?=^##\s|\Z)",
            content,
            re.MULTILINE | re.DOTALL,
        )
        declared_counts = dict.fromkeys(status_col_indices, 0)
        if summary_match is None:
            return declared_counts

        _, data_rows = self._parse_summary_table_rows(summary_match.group(1))
        for row_cells in data_rows:
            for status, col_idx in status_col_indices.items():
                if col_idx < len(row_cells):
                    with contextlib.suppress(ValueError):
                        declared_counts[status] += int(row_cells[col_idx].strip())
        return declared_counts

    @staticmethod
    def _count_index_statuses(
        rows: list[tuple[str, str, str]],
        status_col_indices: dict[str, int],
    ) -> dict[str, int]:
        """Count actual statuses in the index for each status present in status_col_indices.

        Only rows with a non-empty ``file_path`` are counted as real work-unit rows;
        rows without a file path are header rows, separator rows, or Status Summary
        data rows incidentally parsed by ``_parse_backlog_rows``.

        Args:
            rows: Parsed ``(id, status, file_path)`` tuples from the Full Work Unit Index.
            status_col_indices: Mapping from canonical status name to column index (used
                only to determine which statuses to count).

        Returns:
            A dict mapping each status to its actual count in the index.
        """
        actual_counts = dict.fromkeys(status_col_indices, 0)
        for row_id, row_status, file_path_str in rows:
            if not row_id or row_id.lower() == "id" or row_id.startswith("-"):
                continue
            if not file_path_str:
                continue
            if row_status in actual_counts:
                actual_counts[row_status] += 1
        return actual_counts

    def _check_required_section_headers(
        self,
        rows: list[tuple[str, str, str]],
        workspace_root: Path,
        errors: list[str],
    ) -> None:
        """Check 6: every work unit file contains a ``## Comments`` section header.

        The backlog contract requires a ``## Comments`` section in every work unit
        file so that agents have a designated location for log entries.  This check
        reports an error for each file where the header is absent.  Files that do
        not exist on disk are skipped (already reported by check 1).

        Args:
            rows: Parsed ``(id, status, file_path)`` tuples from the Full Work Unit Index.
            workspace_root: Workspace root used to resolve relative file paths.
            errors: Mutable list to which error strings are appended.
        """
        for row_id, _, file_path_str in rows:
            if not row_id or row_id.startswith("-") or row_id.lower() == "id":
                continue
            if not file_path_str:
                continue
            wu_path = workspace_root / file_path_str
            if not wu_path.exists():
                continue  # Already reported by _check_files_and_statuses.
            content = wu_path.read_text(encoding="utf-8")
            if COMMENTS_SECTION_HEADER not in content:
                errors.append(
                    f"{row_id}: work unit file missing '## Comments' section header"
                )

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
            raise ValueError(
                f"Invalid status '{new_status}'. "
                f"Valid statuses: {', '.join(sorted(VALID_STATUSES))}"
            )

        self._update_status(work_unit_path, canonical)
        self._update_backlog_index(backlog_index, unit_id, canonical)
        self.logger.info(
            "Set %s to '%s' in both work-unit file and BACKLOG.md",
            unit_id,
            canonical,
        )

        if canonical == STATUS_DONE:
            self._rollup_parent_status(backlog_index, unit_id)

    def _last_round_all_passed(self, work_unit_path: Path) -> bool:
        """Check whether the most recent review round had all required judges pass.

        Reads the work-unit file's comment history in reverse. Collects
        ``[REVIEW_PASS]`` entries per judge; stops and resets if a
        ``[REVIEW_REJECTED]`` line is encountered (prior round boundary).

        Returns:
            True if every judge in ``REVIEW_JUDGE_NAMES`` has a ``[REVIEW_PASS]``
            entry in the most recent round; False otherwise.
        """
        content = work_unit_path.read_text(encoding="utf-8")
        passed: set[str] = set()
        for line in reversed(content.splitlines()):
            if "[REVIEW_REJECTED]" in line:
                break  # everything before this belongs to a prior round
            for judge in REVIEW_JUDGE_NAMES:
                if f"[judge/{judge}]" in line and "[REVIEW_PASS]" in line:
                    passed.add(judge)
        return passed >= REVIEW_JUDGE_NAMES

    def _rollup_parent_status(self, backlog_index: Path, unit_id: str) -> None:
        """If all children of the parent unit are Done, mark the parent Done too.

        Derives the parent ID by removing the last segment (e.g. E0-F1-S1-T1 → E0-F1-S1).
        Calls ``_set_status`` directly (bypassing the done-gate — parent units are
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

        parent_ids_with_status = {row_id for row_id, status, _ in rows if row_id and status}
        if parent_id not in parent_ids_with_status:
            self.logger.debug(
                "Skipping rollup for '%s': not found with a recognized status in %s",
                parent_id,
                backlog_index,
            )
            return

        parent_file = self._find_work_unit_file(rows, parent_id, backlog_index.parent)
        if parent_file is None:
            self.logger.warning("Could not find file for parent %s — skipping rollup", parent_id)
            return

        self.logger.info("All children of %s are done — rolling up status", parent_id)
        # _set_status: atomic write to both files; cascades via _rollup_parent_status.
        self._set_status(parent_file, backlog_index, parent_id, STATUS_DONE)

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
        """Check if the parent exists, is not already Done, and all direct children are Done."""
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
                if status != STATUS_DONE:
                    return False

        return parent_found and has_children

    def _find_work_unit_file(
        self, rows: list[tuple[str, str, str]], unit_id: str, workspace_root: Path,
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
            timestamp=timestamp, agent_id="backlog_manager", action=action, message=message,
        )

        content = work_unit_path.read_text(encoding="utf-8")

        comments_header = COMMENTS_SECTION_HEADER
        if comments_header in content:
            content = content.rstrip("\n") + "\n\n" + entry
        else:
            content = content.rstrip("\n") + "\n\n" + comments_header + "\n\n" + entry

        work_unit_path.write_text(content, encoding="utf-8")
