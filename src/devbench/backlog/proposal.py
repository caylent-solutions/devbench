"""Task-factory proposal lifecycle.

After the manifest-amender rejects an amendment whose changes are legitimate
production fixes outside the task's scope, the orchestrator invokes
``devbench:blocker-resolver`` which writes a proposal JSON file describing
one or more new work units the factory should generate. ``devbench:task-factory``
then materialises each proposed task as a draft ``.md`` file with status
``proposed`` and inserts a row in ``BACKLOG.md``. The human reviews, edits,
and promotes (``devbench promote-proposal``) or rejects
(``devbench reject-proposal``) each draft.

All mutations live in this module so the CLI layer stays thin. Concurrency
is protected by a POSIX file lock on ``.devbench/task-factory.lock``:
``allocate_next_ids`` acquires the lock before scanning the story directory
and returning a contiguous sequence of free task IDs. Two factory runs
firing in parallel cannot produce colliding IDs.
"""

from __future__ import annotations

import contextlib
import fcntl
import json
import logging
import re
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path

from devbench.backlog.manager import BacklogManager
from devbench.backlog.parser import BacklogParser
from devbench.constants import (
    COMMENT_AGENT_TEMPLATE,
    COMMENT_TIMESTAMP_FORMAT,
    COMMENTS_SECTION_HEADER,
    STATUS_IN_QUEUE,
    STATUS_PROPOSED,
)

logger = logging.getLogger(__name__)

PROPOSAL_DIR_NAME = ".devbench/proposals"
REJECTED_PROPOSAL_DIR_NAME = ".devbench/rejected-proposals"
LOCK_FILE_NAME = ".devbench/task-factory.lock"

# Matches valid Story IDs such as ``E0-F1-S1``. Used by ``allocate_next_ids``
# to derive the on-disk directory layout (``backlog/E0/E0-F1/E0-F1-S1/``).
_STORY_ID_RE = re.compile(r"^E\d+-F\d+-S\d+$")

# Matches task IDs under a given story (e.g. ``E0-F1-S1-T<N>``).
_TASK_ID_SUFFIX_RE = re.compile(r"^T(\d+)$")

# Matches the ``## Status:`` line and rewrites the value.
_STATUS_LINE_RE = re.compile(r"^(##\s*Status:\s*)(.+)$", re.MULTILINE)

# Captures backlog-index rows: ``| ID | ... |``.
_BACKLOG_ROW_RE = re.compile(r"^\|\s*(\S+)\s*\|", re.MULTILINE)


# ---------------------------------------------------------------------------
# Dataclasses
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ProposedTask:
    """One task the blocker-resolver wants the factory to draft."""

    suggested_id: str
    title: str
    files_to_own: list[str]
    linked_scenarios: list[str]
    suggested_acs: list[str]
    suggested_approach: str


@dataclass(frozen=True)
class Proposal:
    """Complete payload emitted by blocker-resolver after an amendment reject."""

    source_task_id: str
    generated_at: str
    rejection_reason: str
    proposed_tasks: list[ProposedTask]

    def to_dict(self) -> dict:
        """JSON-serialisable form used for on-disk storage."""
        return {
            "source_task_id": self.source_task_id,
            "generated_at": self.generated_at,
            "rejection_reason": self.rejection_reason,
            "proposed_tasks": [asdict(t) for t in self.proposed_tasks],
        }

    @classmethod
    def from_dict(cls, data: dict) -> Proposal:
        """Build a :class:`Proposal` from a loaded JSON dict. Raises ``ValueError`` on schema errors."""
        if not isinstance(data, dict):
            raise ValueError(f"Proposal JSON must be an object, got {type(data).__name__}")
        for key in ("source_task_id", "generated_at", "rejection_reason", "proposed_tasks"):
            if key not in data:
                raise ValueError(f"Proposal JSON missing required field: {key}")
        tasks_raw = data["proposed_tasks"]
        if not isinstance(tasks_raw, list):
            raise ValueError("Proposal.proposed_tasks must be a list")
        tasks: list[ProposedTask] = []
        for entry in tasks_raw:
            if not isinstance(entry, dict):
                raise ValueError("Each entry in Proposal.proposed_tasks must be an object")
            required = (
                "suggested_id",
                "title",
                "files_to_own",
                "linked_scenarios",
                "suggested_acs",
                "suggested_approach",
            )
            for field_name in required:
                if field_name not in entry:
                    raise ValueError(f"Proposed task missing required field: {field_name}")
            tasks.append(
                ProposedTask(
                    suggested_id=str(entry["suggested_id"]).strip(),
                    title=str(entry["title"]).strip(),
                    files_to_own=[str(x) for x in entry["files_to_own"]],
                    linked_scenarios=[str(x) for x in entry["linked_scenarios"]],
                    suggested_acs=[str(x) for x in entry["suggested_acs"]],
                    suggested_approach=str(entry["suggested_approach"]),
                )
            )
        return cls(
            source_task_id=str(data["source_task_id"]).strip(),
            generated_at=str(data["generated_at"]),
            rejection_reason=str(data["rejection_reason"]),
            proposed_tasks=tasks,
        )


class ProposalError(RuntimeError):
    """Raised when a proposal cannot be processed."""


# ---------------------------------------------------------------------------
# Path helpers
# ---------------------------------------------------------------------------


def proposal_path(workspace_root: Path, source_task_id: str) -> Path:
    """Return the on-disk path for the blocker-resolver's proposal JSON."""
    return workspace_root / PROPOSAL_DIR_NAME / f"{source_task_id}.json"


# ---------------------------------------------------------------------------
# Concurrency-safe ID allocator
# ---------------------------------------------------------------------------


@contextmanager
def _id_allocation_lock(workspace_root: Path) -> Iterator[None]:
    """Acquire an exclusive POSIX file lock on ``.devbench/task-factory.lock``.

    Released even if the wrapped block raises. POSIX-only (Linux / macOS);
    DevBench does not claim Windows support today.
    """
    lock_path = workspace_root / LOCK_FILE_NAME
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    with lock_path.open("w") as fh:
        fcntl.flock(fh.fileno(), fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(fh.fileno(), fcntl.LOCK_UN)


def _story_dir(backlog_root: Path, story_id: str) -> Path:
    """Return the filesystem path that holds the given story's task files."""
    if not _STORY_ID_RE.match(story_id):
        raise ProposalError(f"story_id {story_id!r} is not a valid Story ID (expected ``E<N>-F<N>-S<N>``)")
    parts = story_id.split("-")
    return backlog_root / parts[0] / "-".join(parts[:2]) / story_id


def scan_story_for_task_ids(backlog_root: Path, story_id: str) -> set[str]:
    """Return every existing task ID under ``story_id`` as declared by the filesystem."""
    story_dir = _story_dir(backlog_root, story_id)
    if not story_dir.is_dir():
        return set()
    ids: set[str] = set()
    prefix = f"{story_id}-"
    for path in story_dir.iterdir():
        if not path.is_file() or path.suffix != ".md":
            continue
        stem = path.stem
        if stem.startswith(prefix):
            ids.add(stem)
    return ids


def allocate_next_ids(workspace_root: Path, backlog_root: Path, story_id: str, count: int) -> list[str]:
    """Return the next ``count`` free task IDs under ``story_id`` atomically.

    Uses a POSIX file lock to serialise concurrent factory runs. The IDs are
    returned in ascending ``T<N>`` order; the highest existing task number
    (from the filesystem listing) is incremented by one for each new ID.
    """
    if count < 1:
        raise ProposalError(f"count must be >= 1, got {count}")
    with _id_allocation_lock(workspace_root):
        existing = scan_story_for_task_ids(backlog_root, story_id)
        max_task_num = 0
        for tid in existing:
            suffix = tid.rsplit("-", 1)[-1]
            match = _TASK_ID_SUFFIX_RE.match(suffix)
            if match is not None:
                max_task_num = max(max_task_num, int(match.group(1)))
        return [f"{story_id}-T{max_task_num + i + 1}" for i in range(count)]


# ---------------------------------------------------------------------------
# Proposal I/O
# ---------------------------------------------------------------------------


def write_proposal(workspace_root: Path, proposal: Proposal) -> Path:
    """Persist ``proposal`` to the pending-proposals directory."""
    target = proposal_path(workspace_root, proposal.source_task_id)
    if target.exists():
        raise ProposalError(
            f"Proposal already exists for source task {proposal.source_task_id} at {target}. "
            "Resolve or reject its tasks before generating new proposals."
        )
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(proposal.to_dict(), indent=2) + "\n", encoding="utf-8")
    return target


def read_proposal(workspace_root: Path, source_task_id: str) -> Proposal:
    """Load a pending proposal from disk, raising :class:`ProposalError` on errors."""
    target = proposal_path(workspace_root, source_task_id)
    if not target.exists():
        raise ProposalError(f"No proposal for source task {source_task_id} at {target}")
    try:
        raw = json.loads(target.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ProposalError(f"Proposal for {source_task_id} is not valid JSON: {exc}") from exc
    try:
        return Proposal.from_dict(raw)
    except ValueError as exc:
        raise ProposalError(f"Proposal for {source_task_id} is invalid: {exc}") from exc


def delete_proposal(workspace_root: Path, source_task_id: str) -> None:
    """Delete the pending proposal JSON, if it exists."""
    target = proposal_path(workspace_root, source_task_id)
    if target.exists():
        target.unlink()


# ---------------------------------------------------------------------------
# Draft generation
# ---------------------------------------------------------------------------


DRAFT_TEMPLATE: str = """\
# {task_id}: {title}

## Status: {status}

## Target Repository

- **Repo:** `{repo}`
- **Branch:** `backlog/{task_id_lower}`

## Description

<!-- auto-generated by task-factory on {generated_at} from proposal for {source_task_id}.
     Review and edit before promoting to in-queue. -->

{approach}

### Related Scenarios

{linked_scenarios}

## Dependencies

| ID | Title | Status |
|----|-------|--------|
| none | | |

## Acceptance Criteria

{acceptance_criteria}

## Changes Manifest

| File | Change |
|------|--------|
{changes_manifest}

## Definition of Done

- [ ] All acceptance criteria checked
- [ ] Tests green
- [ ] Lint and format clean
- [ ] Only files in Changes Manifest are staged with `git add`

## TDD Cycle Log

## Comments
"""


def generate_draft_md(
    proposed: ProposedTask,
    *,
    repo: str,
    source_task_id: str,
    generated_at: str,
    status: str = STATUS_PROPOSED,
) -> str:
    """Render the markdown content for one proposed task file."""
    scenarios = ", ".join(proposed.linked_scenarios) if proposed.linked_scenarios else "(none documented)"
    ac_lines = (
        "\n".join(f"- [ ] {line}" for line in proposed.suggested_acs)
        if proposed.suggested_acs
        else "- [ ] AC-TODO-001 human must author AC"
    )
    manifest_lines = (
        "\n".join(f"| `{path}` | TODO -- describe change |" for path in proposed.files_to_own)
        if proposed.files_to_own
        else "| `TODO` | TODO -- describe change |"
    )
    return DRAFT_TEMPLATE.format(
        task_id=proposed.suggested_id,
        task_id_lower=proposed.suggested_id.lower(),
        title=proposed.title,
        repo=repo,
        status=status,
        generated_at=generated_at,
        source_task_id=source_task_id,
        approach=proposed.suggested_approach.strip() or "TODO -- human must author approach",
        linked_scenarios=scenarios,
        acceptance_criteria=ac_lines,
        changes_manifest=manifest_lines,
    )


# ---------------------------------------------------------------------------
# BACKLOG.md row manipulation
# ---------------------------------------------------------------------------


def _render_backlog_row(task_id: str, title: str, status: str, repo: str, rel_path: str) -> str:
    """Return one BACKLOG.md Full Work Unit Index row for the given task."""
    return f"| {task_id} | {title} | Task | {status} | None | {repo} | `{rel_path}` |\n"


def _append_backlog_row(backlog_index: Path, row: str) -> None:
    """Append ``row`` to the Full Work Unit Index table in ``backlog_index``."""
    content = backlog_index.read_text(encoding="utf-8")
    marker = "## Full Work Unit Index"
    if marker not in content:
        raise ProposalError(f"BACKLOG.md at {backlog_index} has no '## Full Work Unit Index' section")
    # Append at EOF (end of index block); after every existing row the
    # Status Summary regeneration will pick this up.
    content = content.rstrip("\n") + "\n" + row
    backlog_index.write_text(content, encoding="utf-8")


def _remove_backlog_row(backlog_index: Path, task_id: str) -> None:
    """Remove any row for ``task_id`` from the Full Work Unit Index."""
    content = backlog_index.read_text(encoding="utf-8")
    lines = content.splitlines(keepends=True)
    kept: list[str] = []
    removed = False
    for line in lines:
        match = _BACKLOG_ROW_RE.match(line)
        if match is not None and match.group(1) == task_id:
            removed = True
            continue
        kept.append(line)
    if not removed:
        raise ProposalError(f"Row for {task_id} not found in {backlog_index}")
    backlog_index.write_text("".join(kept), encoding="utf-8")


# ---------------------------------------------------------------------------
# Materialisation
# ---------------------------------------------------------------------------


def materialise_proposal(
    *,
    workspace_root: Path,
    backlog_root: Path,
    backlog_index: Path,
    proposal: Proposal,
    repo: str,
) -> list[Path]:
    """Write every proposed task as a draft ``.md`` and insert a row in ``BACKLOG.md``.

    Returns the list of materialised draft files. The caller is responsible
    for asserting that the proposal's source task is in ``blocked`` state
    beforehand; task-factory runs exclusively from that path.
    """
    if _has_unresolved_proposals(backlog_index):
        raise ProposalError(
            "Skipped proposal generation -- unresolved proposed tasks already exist. "
            "Resolve those via promote-proposal or reject-proposal before generating new proposals."
        )
    drafts: list[Path] = []
    mgr = BacklogManager()
    for proposed in proposal.proposed_tasks:
        story_id = _extract_story_id(proposed.suggested_id)
        story_dir = _story_dir(backlog_root, story_id)
        story_dir.mkdir(parents=True, exist_ok=True)
        target = story_dir / f"{proposed.suggested_id}.md"
        if target.exists():
            raise ProposalError(f"Draft file for {proposed.suggested_id} already exists at {target}")
        content = generate_draft_md(
            proposed,
            repo=repo,
            source_task_id=proposal.source_task_id,
            generated_at=proposal.generated_at,
        )
        target.write_text(content, encoding="utf-8")
        rel_path = target.relative_to(workspace_root).as_posix()
        row = _render_backlog_row(
            task_id=proposed.suggested_id,
            title=proposed.title,
            status=STATUS_PROPOSED,
            repo=repo,
            rel_path=rel_path,
        )
        _append_backlog_row(backlog_index, row)
        drafts.append(target)
        logger.info("Materialised proposed task %s -> %s", proposed.suggested_id, target)
    # Rebuild Status Summary counts so the `proposed` rows appear.
    mgr._update_status_summary(backlog_index)
    return drafts


def _has_unresolved_proposals(backlog_index: Path) -> bool:
    """Return True when any row in BACKLOG.md already carries status ``proposed``."""
    if not backlog_index.is_file():
        return False
    for line in backlog_index.read_text(encoding="utf-8").splitlines():
        if not line.strip().startswith("|"):
            continue
        cells = [c.strip() for c in line.split("|")]
        if len(cells) < 5:
            continue
        status_cell = cells[4].lower() if len(cells) >= 5 else ""
        if status_cell == STATUS_PROPOSED:
            return True
    return False


def _extract_story_id(task_id: str) -> str:
    """Return the Story portion of a task ID, e.g. ``E0-F1-S1-T3`` -> ``E0-F1-S1``."""
    parts = task_id.split("-")
    if len(parts) < 4:
        raise ProposalError(f"Cannot derive story_id from task_id {task_id!r}")
    return "-".join(parts[:3])


# ---------------------------------------------------------------------------
# Promote / reject
# ---------------------------------------------------------------------------


def list_proposals(workspace_root: Path) -> list[Proposal]:
    """Return every pending proposal under ``<workspace_root>/.devbench/proposals/``."""
    proposals_dir = workspace_root / PROPOSAL_DIR_NAME
    if not proposals_dir.is_dir():
        return []
    out: list[Proposal] = []
    for path in sorted(proposals_dir.iterdir()):
        if path.suffix != ".json" or not path.is_file():
            continue
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
            out.append(Proposal.from_dict(raw))
        except (json.JSONDecodeError, ValueError) as exc:
            logger.warning("Skipping unreadable proposal %s: %s", path, exc)
    return out


def _find_draft_file(backlog_root: Path, task_id: str) -> Path | None:
    """Return the draft .md path for a proposed task ID, or ``None`` if missing."""
    story_id = _extract_story_id(task_id)
    story_dir = _story_dir(backlog_root, story_id)
    target = story_dir / f"{task_id}.md"
    return target if target.is_file() else None


def _rewrite_status(md_path: Path, new_status: str) -> None:
    """Replace the ``## Status:`` value in a draft file."""
    content = md_path.read_text(encoding="utf-8")
    if not _STATUS_LINE_RE.search(content):
        raise ProposalError(f"Draft {md_path} has no '## Status:' line")
    content = _STATUS_LINE_RE.sub(rf"\g<1>{new_status}", content, count=1)
    md_path.write_text(content, encoding="utf-8")


def _rewrite_backlog_status(backlog_index: Path, task_id: str, new_status: str) -> None:
    """Replace the status column in the BACKLOG.md row for ``task_id``."""
    content = backlog_index.read_text(encoding="utf-8")
    lines = content.splitlines()
    updated = False
    for i, line in enumerate(lines):
        match = _BACKLOG_ROW_RE.match(line)
        if match is None or match.group(1) != task_id:
            continue
        cells = line.split("|")
        # Cells layout: ['', ' ID ', ' Title ', ' Type ', ' Status ', ' Deps ', ' Repo ', ' Path ', '']
        if len(cells) >= 5:
            cells[4] = f" {new_status} "
            lines[i] = "|".join(cells)
            updated = True
            break
    if not updated:
        raise ProposalError(f"Row for {task_id} not found in {backlog_index}")
    backlog_index.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _append_dependency_to_source(backlog_root: Path, backlog_index: Path, source_task_id: str, new_dep_id: str) -> None:
    """Append ``new_dep_id`` to ``source_task_id``'s Dependencies table."""
    source_unit = _find_source_task_file(backlog_root, backlog_index, source_task_id)
    if source_unit is None:
        raise ProposalError(f"Cannot find source task file for {source_task_id}")
    content = source_unit.read_text(encoding="utf-8")
    marker = "## Dependencies"
    idx = content.find(marker)
    if idx == -1:
        raise ProposalError(f"Source task file {source_unit} has no '## Dependencies' section")
    # Find the end of the Dependencies section and append a row inside it.
    next_section = content.find("\n## ", idx + 1)
    section = content[idx : next_section if next_section != -1 else len(content)]
    remainder = content[next_section:] if next_section != -1 else ""
    # Replace a placeholder ``| none | | |`` row when the dependencies table
    # is currently empty; otherwise append a new row to the end of the table.
    none_row_re = re.compile(r"^\|\s*none\s*\|\s*\|\s*\|\s*$", re.IGNORECASE | re.MULTILINE)
    if none_row_re.search(section):
        section = none_row_re.sub(f"| {new_dep_id} | (auto) | proposed |", section, count=1)
    else:
        section = section.rstrip("\n") + f"\n| {new_dep_id} | (auto) | proposed |\n"
    source_unit.write_text(content[:idx] + section + remainder, encoding="utf-8")


def _find_source_task_file(backlog_root: Path, backlog_index: Path, task_id: str) -> Path | None:
    """Locate the .md file for an arbitrary task ID by walking the backlog tree."""
    parser = BacklogParser(backlog_root=backlog_root, backlog_index=backlog_index)
    try:
        units = parser.parse_index()
    except (FileNotFoundError, ValueError):
        return None
    for unit in units:
        if unit.id == task_id:
            return unit.file_path
    return None


def promote_proposal(
    *,
    workspace_root: Path,
    backlog_root: Path,
    backlog_index: Path,
    task_id: str,
    dep_on_source: bool = True,
) -> Path:
    """Flip a proposed task to ``in-queue`` and (optionally) wire it as a source-task dep.

    Returns the path to the promoted draft file.
    """
    draft = _find_draft_file(backlog_root, task_id)
    if draft is None:
        raise ProposalError(f"No draft file for proposed task {task_id}")
    _rewrite_status(draft, STATUS_IN_QUEUE)
    _rewrite_backlog_status(backlog_index, task_id, STATUS_IN_QUEUE)
    # Refresh Status Summary counts after the status flip.
    BacklogManager()._update_status_summary(backlog_index)

    if dep_on_source:
        source = _find_originating_source_task(workspace_root, task_id)
        if source is not None:
            _append_dependency_to_source(backlog_root, backlog_index, source, task_id)
            source_file = _find_source_task_file(backlog_root, backlog_index, source)
            if source_file is not None:
                _append_promote_comment(source_file, source, task_id)
    return draft


def _append_promote_comment(source_file: Path, source_task_id: str, promoted_task_id: str) -> None:
    """Append a ``[PROPOSAL_PROMOTED]`` audit line to ``source_file``'s Comments section."""
    timestamp = datetime.now(tz=UTC).strftime(COMMENT_TIMESTAMP_FORMAT)
    entry = COMMENT_AGENT_TEMPLATE.format(
        timestamp=timestamp,
        name="task_factory",
        message=f"[PROPOSAL_PROMOTED] {promoted_task_id} promoted and wired as dependency of {source_task_id}.",
    )
    content = source_file.read_text(encoding="utf-8")
    if COMMENTS_SECTION_HEADER in content:
        content = content.rstrip("\n") + "\n\n" + entry
    else:
        content = content.rstrip("\n") + "\n\n" + COMMENTS_SECTION_HEADER + "\n\n" + entry
    source_file.write_text(content, encoding="utf-8")


def _find_originating_source_task(workspace_root: Path, promoted_task_id: str) -> str | None:
    """Return the source_task_id that originated ``promoted_task_id``, or ``None``.

    Walks every pending proposal and returns the first source whose
    ``proposed_tasks`` contains ``promoted_task_id``.
    """
    for proposal in list_proposals(workspace_root):
        for task in proposal.proposed_tasks:
            if task.suggested_id == promoted_task_id:
                return proposal.source_task_id
    return None


def promote_all_from_source(
    *,
    workspace_root: Path,
    backlog_root: Path,
    backlog_index: Path,
    source_task_id: str,
    dep_on_source: bool = True,
) -> list[Path]:
    """Promote every proposed task originating from ``source_task_id``."""
    try:
        proposal = read_proposal(workspace_root, source_task_id)
    except ProposalError as exc:
        raise ProposalError(f"Cannot resolve proposals for {source_task_id}: {exc}") from exc
    promoted: list[Path] = []
    for entry in proposal.proposed_tasks:
        promoted.append(
            promote_proposal(
                workspace_root=workspace_root,
                backlog_root=backlog_root,
                backlog_index=backlog_index,
                task_id=entry.suggested_id,
                dep_on_source=dep_on_source,
            )
        )
    return promoted


def reject_proposal(
    *,
    workspace_root: Path,
    backlog_root: Path,
    backlog_index: Path,
    task_id: str,
    reason: str,
) -> Path | None:
    """Archive a proposed task's draft, remove its BACKLOG.md row, audit on source.

    Returns the archive path of the removed draft, or ``None`` when the draft
    file was missing (e.g. already rejected). Raises :class:`ProposalError`
    if ``reason`` is empty.
    """
    if not reason or not reason.strip():
        raise ProposalError("reject_proposal requires a non-empty reason")
    draft = _find_draft_file(backlog_root, task_id)
    archive: Path | None = None
    if draft is not None:
        archive_dir = workspace_root / REJECTED_PROPOSAL_DIR_NAME
        archive_dir.mkdir(parents=True, exist_ok=True)
        timestamp = datetime.now(tz=UTC).strftime("%Y%m%dT%H%M%SZ")
        archive = archive_dir / f"{task_id}-{timestamp}.md"
        draft.rename(archive)
    with contextlib.suppress(ProposalError):
        _remove_backlog_row(backlog_index, task_id)
    # Suppress is intentional here: rejection is idempotent, so a missing
    # BACKLOG.md row (e.g. already rejected) is a no-op.

    # Audit comment on the source task.
    source = _find_originating_source_task(workspace_root, task_id)
    if source is not None:
        source_file = _find_source_task_file(backlog_root, backlog_index, source)
        if source_file is not None:
            timestamp = datetime.now(tz=UTC).strftime(COMMENT_TIMESTAMP_FORMAT)
            message = f"[PROPOSAL_REJECTED] {task_id} rejected: {reason}"
            entry = COMMENT_AGENT_TEMPLATE.format(timestamp=timestamp, name="task_factory", message=message)
            content = source_file.read_text(encoding="utf-8")
            if COMMENTS_SECTION_HEADER in content:
                content = content.rstrip("\n") + "\n\n" + entry
            else:
                content = content.rstrip("\n") + "\n\n" + COMMENTS_SECTION_HEADER + "\n\n" + entry
            source_file.write_text(content, encoding="utf-8")

    # Refresh Status Summary so the deleted row is no longer counted.
    BacklogManager()._update_status_summary(backlog_index)
    return archive
