"""Backlog manager judge that updates work-unit statuses and traceability.

Provides methods to mark work units as done or blocked, update the backlog
index, and log entries to the traceability matrix.

All status transitions flow through ``set_status`` which updates both the
work-unit file and BACKLOG.md atomically, preventing drift between the two.
"""

from datetime import UTC, datetime
from pathlib import Path

from devbench.constants import (
    COMMENT_ENTRY_TEMPLATE,
    COMMENTS_SECTION_HEADER,
    STATUS_LINE_RE,
    TABLE_STATUS_VALUES,
    TRACEABILITY_MATRIX_HEADER,
)
from devbench.judges.base import BaseJudge, JudgeResult, Verdict

# Mapping from CLI-style lowercase statuses to the canonical lowercase forms
# used in work-unit files and BACKLOG.md table rows.
VALID_STATUSES: dict[str, str] = {
    "in-queue": "in-queue",
    "in-progress": "in-progress",
    "in-review": "in-review",
    "done": "done",
    "blocked": "blocked",
}


class BacklogManagerJudge(BaseJudge):
    """Updates backlog statuses, the backlog index, and the traceability matrix."""

    def __init__(self) -> None:
        super().__init__("backlog_manager")

    def evaluate(self, work_unit_path: Path, repo_path: Path, **kwargs: object) -> JudgeResult:
        """Not used directly; BacklogManagerJudge exposes individual operation methods.

        Returns a PASS result as a no-op when called via the evaluate interface.
        """
        return JudgeResult(
            judge_name=self.name,
            verdict=Verdict.PASS,
            reasoning="BacklogManagerJudge.evaluate is a no-op; use specific operation methods.",
            feedback="",
            evidence=[],
        )

    def set_status(
        self,
        work_unit_path: Path,
        backlog_index: Path,
        unit_id: str,
        new_status: str,
    ) -> None:
        """Update the status in both the work-unit file and BACKLOG.md.

        This is the single code path for all status transitions.

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

        # When marking done, roll up parent status if all siblings are done
        if canonical == "done":
            self._rollup_parent_status(backlog_index, unit_id)

    def mark_done(self, work_unit_path: Path, backlog_index: Path, unit_id: str) -> None:
        """Mark a work unit as Done in both files.

        Args:
            work_unit_path: Path to the work-unit ``.md`` file.
            backlog_index: Path to the ``BACKLOG.md`` file.
            unit_id: The work-unit identifier.

        Raises:
            FileNotFoundError: If either file does not exist.
            ValueError: If the status line or unit row is not found.
        """
        self.set_status(work_unit_path, backlog_index, unit_id, "done")

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
        self.set_status(work_unit_path, backlog_index, unit_id, "blocked")
        self._append_comment(work_unit_path, "BLOCKED", reason)

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

    def _rollup_parent_status(self, backlog_index: Path, unit_id: str) -> None:
        """If all children of the parent unit are Done, mark the parent Done too.

        Derives the parent ID by removing the last segment (e.g. E0-F1-S1-T1 → E0-F1-S1).
        Cascades upward: Story → Feature → Epic.
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
        self._update_status(parent_file, "done")
        self._update_backlog_index(backlog_index, parent_id, "done")

        # Cascade upward
        self._rollup_parent_status(backlog_index, parent_id)

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
                if status == "done":
                    return False  # Already done
                continue

            if row_id.startswith(parent_id + "-") and row_id.count("-") == parent_depth + 1:
                has_children = True
                if status != "done":
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
                    updated = True
                    break
            lines[i] = "|".join(cells)
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
