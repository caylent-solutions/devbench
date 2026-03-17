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
                                drift, orphans, broken deps, summary mismatch).
``log_to_traceability_matrix``— appends a spec/test mapping entry to the
                                traceability matrix file.

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

import logging
import re
from datetime import UTC, datetime
from pathlib import Path

from devbench.constants import (
    ALL_REQUIRED_JUDGE_NAMES,
    BACKLOG_STATUS_RE,
    BACKLOG_SUBDIR,
    COMMENT_ENTRY_TEMPLATE,
    COMMENTS_SECTION_HEADER,
    DEPENDENCY_NONE_VALUES,
    EPIC_ID_RE,
    STATUS_BLOCKED,
    STATUS_DONE,
    STATUS_IN_PROGRESS,
    STATUS_IN_QUEUE,
    STATUS_LINE_RE,
    STATUS_SUMMARY_SECTION_HEADER,
    STATUS_SUMMARY_TABLE_HEADER,
    STRIP_SUMMARY_RE,
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
        5. Status Summary table exists and counts match the Full Work Unit Index.

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
        self._check_status_summary(backlog_index, rows, errors)
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
        self._update_status_summary(backlog_index)
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

    def _update_status_summary(self, backlog_index: Path) -> None:
        """Rewrite the Status Summary table section in BACKLOG.md.

        Reads all rows from the Full Work Unit Index, groups descendants by epic,
        counts statuses, and either inserts or replaces the ``## Status Summary``
        section immediately before ``## Full Work Unit Index``.

        An epic row is identified as any row whose ID matches the pattern
        ``E<digits>`` (no hyphen suffix). Counts are computed over all descendant
        rows — rows whose ID starts with ``<epic-id>-``.
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

    def _compute_epic_counts(
        self, rows: list[tuple[str, str, str]]
    ) -> dict[str, dict[str, int]]:
        """Compute per-epic status counts from backlog rows.

        Returns a dict mapping epic_id -> {status: count} where status is one of
        the canonical status strings (``done``, ``in-progress``, ``in-queue``,
        ``blocked``).  Only descendant rows (those starting with
        ``<epic-id>-``) are counted; the epic row itself is excluded.
        """
        epic_order: list[str] = []
        for row_id, _, _ in rows:
            if row_id and EPIC_ID_RE.match(row_id):
                epic_order.append(row_id)

        counts: dict[str, dict[str, int]] = {
            epic_id: {STATUS_DONE: 0, STATUS_IN_PROGRESS: 0, STATUS_IN_QUEUE: 0, STATUS_BLOCKED: 0}
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
                f"{c[STATUS_IN_PROGRESS]} | {c[STATUS_IN_QUEUE]} | {c[STATUS_BLOCKED]} |\n"
            )

        return (
            STATUS_SUMMARY_SECTION_HEADER + "\n\n"
            + STATUS_SUMMARY_TABLE_HEADER
            + table_rows
            + "\n"
        )

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
                "Status Summary section missing from BACKLOG.md — "
                "run '_update_status_summary' to generate it"
            )
            return

        # Compute expected counts and parse the actual table
        epic_counts = self._compute_epic_counts(rows)
        actual_counts = self._parse_summary_table(content)

        for epic_id, expected in epic_counts.items():
            if epic_id not in actual_counts:
                errors.append(
                    f"Status Summary missing epic row '{epic_id}'"
                )
                continue
            actual = actual_counts[epic_id]
            for status_key in (STATUS_DONE, STATUS_IN_PROGRESS, STATUS_IN_QUEUE, STATUS_BLOCKED):
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
            if len(cells) < 7:
                continue
            row_id = cells[1]
            if not EPIC_ID_RE.match(row_id):
                continue
            try:
                result[row_id] = {
                    STATUS_DONE: int(cells[3]),
                    STATUS_IN_PROGRESS: int(cells[4]),
                    STATUS_IN_QUEUE: int(cells[5]),
                    STATUS_BLOCKED: int(cells[6]),
                }
            except (ValueError, IndexError):
                continue

        return result
