"""WorkUnit model for the judges system.

Defines the ``WorkUnit`` dataclass and supporting enums that represent
a single unit of work parsed from the backlog.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import Enum
from pathlib import Path

from devbench.constants import COMMENT_ENTRY_TEMPLATE, COMMENTS_SECTION_HEADER, EM_DASH, STATUS_LINE_RE


def validate_manifest_paths(paths: list[str], checkout_directories: list[str]) -> None:
    """Validate Changes Manifest file paths against backlog hygiene rules.

    Raises ``ValueError`` with an actionable message if any path:
    - Starts with ``<checkout_dir>/`` (validate-backlog rule 11: paths must be
      repo-relative, not prefixed with the checkout directory).
    - Contains an em-dash U+2014 (validate-backlog rule 10).

    Args:
        paths: List of file paths from the Changes Manifest ``files_to_own`` field.
        checkout_directories: List of checkout directory names from the devbench
            config (e.g. ``["kanon", "devbench"]``).

    Raises:
        ValueError: If any path violates rule 10 or rule 11, with an actionable
            message naming the first offending path.
    """
    for path in paths:
        if EM_DASH in path:
            raise ValueError(
                f"Changes Manifest path contains em-dash (U+2014) -- "
                f"validate-backlog rule 10 will reject this file. "
                f"Offending path: '{path}'. Use '--' (double hyphen) instead."
            )
        for checkout_dir in checkout_directories:
            prefix = f"{checkout_dir}/"
            if path.startswith(prefix):
                raise ValueError(
                    f"Changes Manifest path is prefixed with checkout_directory '{checkout_dir}/' -- "
                    f"validate-backlog rule 11 requires repo-relative paths. "
                    f"Offending path: '{path}'. Remove the '{prefix}' prefix."
                )


class WorkUnitStatus(Enum):
    """Lifecycle status of a work unit."""

    IN_QUEUE = "In Queue"
    IN_PROGRESS = "In Progress"
    IN_REVIEW = "In Review"
    DONE = "Done"
    BLOCKED = "Blocked"
    PROPOSED = "Proposed"
    DECLINED = "Declined"
    HOLD = "Hold"


class WorkUnitType(Enum):
    """Hierarchy level of a work unit."""

    EPIC = "Epic"
    FEATURE = "Feature"
    STORY = "Story"
    TASK = "Task"


@dataclass
class WorkUnit:
    """A single backlog work unit backed by a Markdown file on disk."""

    id: str
    title: str
    status: WorkUnitStatus
    unit_type: WorkUnitType
    file_path: Path
    repo: str
    dependencies: list[str] = field(default_factory=list)
    acceptance_criteria: list[str] = field(default_factory=list)
    description: str = ""
    branch: str = ""

    # ------------------------------------------------------------------
    # Type predicates
    # ------------------------------------------------------------------

    def is_epic(self) -> bool:
        """Return ``True`` if this work unit is an Epic."""
        return self.unit_type is WorkUnitType.EPIC

    def is_feature(self) -> bool:
        """Return ``True`` if this work unit is a Feature."""
        return self.unit_type is WorkUnitType.FEATURE

    def is_story(self) -> bool:
        """Return ``True`` if this work unit is a Story."""
        return self.unit_type is WorkUnitType.STORY

    def is_task(self) -> bool:
        """Return ``True`` if this work unit is a Task."""
        return self.unit_type is WorkUnitType.TASK

    # ------------------------------------------------------------------
    # ID parsing
    # ------------------------------------------------------------------

    def parse_id(self) -> tuple[str, ...]:
        """Split the compound ID into its hierarchical parts.

        Example: ``"E0-F1-S1-T1"`` -> ``("E0", "F1", "S1", "T1")``
        """
        parts = self.id.split("-")
        if not parts or not all(parts):
            raise ValueError(f"Invalid work-unit ID format: '{self.id}'")
        return tuple(parts)

    # ------------------------------------------------------------------
    # Disk mutations
    # ------------------------------------------------------------------

    def set_status(self, new_status: WorkUnitStatus) -> None:
        """Update the ``## Status:`` line in the backing Markdown file.

        Raises ``FileNotFoundError`` if the file does not exist and
        ``ValueError`` if the status line is not found.
        """
        content = self.file_path.read_text()

        if not STATUS_LINE_RE.search(content):
            raise ValueError(f"Could not find '## Status: ...' line in {self.file_path}")

        updated = STATUS_LINE_RE.sub(rf"\g<1>{new_status.value}", content, count=1)
        self.file_path.write_text(updated)
        self.status = new_status

    def log_comment(self, agent_id: str, action: str, message: str) -> None:
        """Append an HTML comment entry to the ``## Comments`` section.

        Format::

            <!-- [YYYY-MM-DD HH:MM UTC] [agent_id] [action] message -->

        If the ``## Comments`` section does not exist it is created at the
        end of the file.
        """
        timestamp = datetime.now(tz=UTC).strftime("%Y-%m-%d %H:%M UTC")
        entry = COMMENT_ENTRY_TEMPLATE.format(timestamp=timestamp, agent_id=agent_id, action=action, message=message)

        content = self.file_path.read_text()

        comments_header = COMMENTS_SECTION_HEADER
        if comments_header in content:
            content = content.rstrip("\n") + "\n\n" + entry
        else:
            content = content.rstrip("\n") + "\n\n" + comments_header + "\n\n" + entry

        self.file_path.write_text(content)
