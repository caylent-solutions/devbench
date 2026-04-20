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
from enum import Enum
from pathlib import Path

from devbench.backlog.manager import BacklogManager
from devbench.backlog.parser import BacklogParser
from devbench.constants import (
    COMMENT_AGENT_TEMPLATE,
    COMMENT_TIMESTAMP_FORMAT,
    COMMENTS_SECTION_HEADER,
    STATUS_DECLINED,
    STATUS_DONE,
    STATUS_IN_QUEUE,
    STATUS_PROPOSED,
)

logger = logging.getLogger(__name__)

PROPOSAL_DIR_NAME = ".devbench/proposals"
REJECTED_PROPOSAL_DIR_NAME = ".devbench/rejected-proposals"
LOCK_FILE_NAME = ".devbench/task-factory.lock"

# Minimum character length for a ``suggested_approach`` field. Enforced by
# ``materialise_proposal`` as a fail-fast contract against thin auto-generated
# drafts that previously required operator hand-editing on every promotion.
# Threshold calibrated to the four-section minimum (Context + Scope + TDD
# approach + Verify) that ``blocker-resolver.md`` now requires: a genuinely
# complete approach narrative cannot fit under 160 characters.
_SUGGESTED_APPROACH_MIN_CHARS: int = 160


class ProposalTaskState(Enum):
    """Lifecycle state of a single ``proposed_tasks[].suggested_id``.

    Used by observability surfaces (``devbench status`` un-materialised count,
    ``devbench report`` panel, ``devbench list-proposals`` state labels) and
    by ``reject_proposal``'s un-materialised-rejection guard to discriminate
    "can drop the JSON" from "drafts already exist, use per-task reject".

    - ``UNMATERIALISED`` -- the proposal JSON names this id but no .md draft
      has been created anywhere (task-factory has not run for this source, or
      its "skip when prior unresolved" safety guard fired).
    - ``PROPOSED`` -- draft .md exists with ``## Status: proposed``; operator
      has not yet promoted or rejected.
    - ``PROMOTED`` -- draft .md has been promoted to an active lifecycle
      status (``in-queue`` / ``in-progress`` / ``in-review`` / ``blocked``).
      ``blocked`` is lumped here because proposal-lifecycle tracking cares
      about "past promotion", not current runability.
    - ``DONE`` -- draft completed its lifecycle.
    - ``DECLINED`` -- draft terminated via ``devbench decline``.
    - ``REJECTED`` -- draft was archived by ``reject-proposal`` into
      ``.devbench/rejected-proposals/``; no live .md remains in the tree.
    """

    UNMATERIALISED = "unmaterialised"
    PROPOSED = "proposed"
    PROMOTED = "promoted"
    DONE = "done"
    DECLINED = "declined"
    REJECTED = "rejected"


def classify_proposed_task(backlog_root: Path, workspace_root: Path, suggested_id: str) -> ProposalTaskState:
    """Return the lifecycle state of a proposed-task id.

    Looks first for a live draft .md under ``backlog_root`` via
    ``_find_draft_file``. If present, reads the ``## Status:`` line and
    maps to the appropriate state. If absent, checks the
    ``.devbench/rejected-proposals/`` archive for a matching file to
    distinguish ``REJECTED`` from ``UNMATERIALISED``.

    Args:
        backlog_root: Root of the backlog tree (``<workspace>/backlog``).
        workspace_root: Workspace root (parent of ``backlog/`` and
            ``.devbench/``). Needed because the rejected-proposals archive
            lives under the workspace, not the backlog.
        suggested_id: The ``proposed_tasks[].suggested_id`` from a proposal
            JSON.

    Returns:
        A ``ProposalTaskState`` value. Always returns; never raises for
        missing files (missing means ``UNMATERIALISED`` unless an archive
        entry exists).
    """
    draft = _find_draft_file(backlog_root, suggested_id)
    if draft is None:
        archive_dir = workspace_root / REJECTED_PROPOSAL_DIR_NAME
        if archive_dir.is_dir():
            for archived in archive_dir.glob(f"{suggested_id}-*.md"):
                if archived.is_file():
                    return ProposalTaskState.REJECTED
        return ProposalTaskState.UNMATERIALISED

    status_value = _read_draft_status(draft).lower()
    if status_value == STATUS_PROPOSED:
        return ProposalTaskState.PROPOSED
    if status_value == STATUS_DONE:
        return ProposalTaskState.DONE
    if status_value == STATUS_DECLINED:
        return ProposalTaskState.DECLINED
    # in-queue / in-progress / in-review / blocked all count as PROMOTED:
    # proposal-lifecycle tracking only cares whether the draft has advanced
    # past operator review, not what activity the draft is currently in.
    return ProposalTaskState.PROMOTED


def _read_draft_status(draft_path: Path) -> str:
    """Read the ``## Status:`` value from a draft .md file.

    Returns the empty string when the file has no ``## Status:`` line
    (malformed draft); callers treat that as ``PROMOTED`` conservatively.
    """
    for line in draft_path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if stripped.startswith("## Status:"):
            return stripped[len("## Status:") :].strip()
    return ""


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

    Refuses (with ``ProposalError``) when any proposed task's
    ``suggested_approach`` is too terse to produce a useful draft. The
    threshold is a module-level constant; blocker-resolver's prompt
    (``plugin/devbench/agents/blocker-resolver.md``) requires the
    Context / Scope / TDD approach / Verify four-section structure whose
    minimum honest length exceeds the threshold. Drafts below the floor
    always require operator hand-editing before promotion, so stopping
    them at the materialise boundary surfaces the defect upstream in
    blocker-resolver where it can be fixed once, instead of downstream
    every time a thin draft lands.
    """
    # Thin-approach refusal -- fail fast before any file write so a partial
    # materialisation cannot leave the backlog half-written. Applies to the
    # whole proposal, even if some tasks are already resolved, because a
    # JSON with thin content should not be accepted regardless of which
    # specific tasks would be created on this call.
    for proposed in proposal.proposed_tasks:
        approach = (proposed.suggested_approach or "").strip()
        if len(approach) < _SUGGESTED_APPROACH_MIN_CHARS:
            raise ProposalError(
                f"suggested_approach too terse for {proposed.suggested_id} "
                f"({len(approach)} chars, minimum {_SUGGESTED_APPROACH_MIN_CHARS}); "
                "re-run blocker-resolver with the Context / Scope / TDD approach / Verify "
                "four-section structure documented in blocker-resolver.md."
            )

    # Classify every task up front so we know which ones actually need
    # creating. The classifier is the single source of truth for proposal
    # lifecycle state -- it reads the backlog tree AND the
    # rejected-proposals/ archive, so a previously-rejected draft classifies
    # as REJECTED (not UNMATERIALISED) and is skipped here.
    classifications = [
        (proposed, classify_proposed_task(backlog_root, workspace_root, proposed.suggested_id))
        for proposed in proposal.proposed_tasks
    ]
    needs_create = any(state is ProposalTaskState.UNMATERIALISED for _, state in classifications)

    # Unresolved-prior-proposals guard applies only when this call would
    # actually create new `proposed` rows. Exclude this proposal's own
    # task IDs so a partial re-materialise (some tasks already PROPOSED)
    # doesn't see itself as the blocker.
    if needs_create:
        exclude = frozenset(t.suggested_id for t in proposal.proposed_tasks)
        if _has_unresolved_proposals(backlog_index, exclude_task_ids=exclude):
            raise ProposalError(
                "Skipped proposal generation -- unresolved proposed tasks already exist. "
                "Resolve those via promote-proposal or reject-proposal before generating new proposals."
            )

    drafts: list[Path] = []
    mgr = BacklogManager()
    for proposed, state in classifications:
        if state is not ProposalTaskState.UNMATERIALISED:
            # Classify-aware skip. The task is already in one of the
            # terminal-for-materialise states: a draft exists (PROPOSED,
            # PROMOTED, DONE, DECLINED) or a reject archive exists
            # (REJECTED). Recreating the draft would resurrect rejected
            # work or duplicate in-flight work.
            logger.info(
                "materialise_proposal: skipping %s in state %s "
                "(already materialised, rejected, promoted, done, or declined)",
                proposed.suggested_id,
                state.value,
            )
            continue
        story_id = _extract_story_id(proposed.suggested_id)
        story_dir = _story_dir(backlog_root, story_id)
        story_dir.mkdir(parents=True, exist_ok=True)
        target = story_dir / f"{proposed.suggested_id}.md"
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
    if drafts:
        # Rebuild Status Summary counts so the `proposed` rows appear.
        # Skipped entirely when no drafts were created -- the summary is
        # already correct.
        mgr._update_status_summary(backlog_index)
    return drafts


def _has_unresolved_proposals(backlog_index: Path, *, exclude_task_ids: frozenset[str] = frozenset()) -> bool:
    """Return True when any row in BACKLOG.md already carries status ``proposed``.

    ``exclude_task_ids`` skips rows whose ID is in the set, which lets
    ``materialise_proposal`` re-run on an already-partially-materialised
    proposal without the guard misfiring on the proposal's own tasks.
    """
    if not backlog_index.is_file():
        return False
    for line in backlog_index.read_text(encoding="utf-8").splitlines():
        if not line.strip().startswith("|"):
            continue
        cells = [c.strip() for c in line.split("|")]
        if len(cells) < 5:
            continue
        task_id = cells[1] if len(cells) >= 2 else ""
        if task_id in exclude_task_ids:
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
    """Append an audit line naming both the promotion and the pending-proposal marker.

    Writes a single comment to ``source_file``'s Comments section containing
    two structured markers:

    - ``[PROPOSAL_PROMOTED]`` -- audit detail describing the wiring event.
    - ``[BLOCKED_PENDING_PROPOSAL] <id>`` -- state marker read by
      ``BacklogManager._auto_requeue_marker_dependents`` when the promoted
      dependency eventually completes, so the source task auto-flips from
      ``blocked`` back to ``in-queue`` without manual intervention.

    Writing both markers on the same comment line keeps the audit trail
    compact while preserving the state marker's scan-scoped position in
    the Comments section.
    """
    timestamp = datetime.now(tz=UTC).strftime(COMMENT_TIMESTAMP_FORMAT)
    entry = COMMENT_AGENT_TEMPLATE.format(
        timestamp=timestamp,
        name="task_factory",
        message=(
            f"[PROPOSAL_PROMOTED] {promoted_task_id} promoted and wired as dependency of {source_task_id}. "
            f"[BLOCKED_PENDING_PROPOSAL] {promoted_task_id}"
        ),
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
    task_id: str = "",
    unmaterialised_source_id: str = "",
    reason: str,
) -> Path | None:
    """Reject a proposed draft (per-task) OR a whole un-materialised JSON (per-source).

    Two mutually-exclusive forms:

    - ``task_id`` set: classic per-draft rejection. Archive the draft .md,
      remove its BACKLOG.md row, audit on source, AND strip the corresponding
      ``[BLOCKED_PENDING_PROPOSAL]`` marker from source Comments. After the
      strip, invoke the auto-requeue cascade so a source whose remaining
      markers are all terminal flips back to ``in-queue`` cleanly.
    - ``unmaterialised_source_id`` set: rejects an entire proposal JSON whose
      drafts have never been materialised. Archive the JSON to
      ``.devbench/rejected-proposals/<source>-unmaterialised-<ts>.json``,
      delete the live JSON, audit a ``[PROPOSAL_JSON_REJECTED]`` comment on
      the source. Refuses when any task in the JSON has already been
      materialised in any form (fail-fast).

    Supplying neither or both raises :class:`ProposalError`.

    Returns the archive path (``.md`` for per-task, ``.json`` for
    un-materialised). Returns ``None`` only when the per-task form runs
    against a draft that was already missing (idempotent no-op).
    """
    if not reason or not reason.strip():
        raise ProposalError("reject_proposal requires a non-empty reason")

    # Fail-fast on the mutual exclusion guard. Either form must be chosen
    # explicitly; the defaults (empty strings) are what ``cmd_reject_proposal``
    # passes when a flag was not supplied.
    has_task_id = bool(task_id)
    has_source_id = bool(unmaterialised_source_id)
    if has_task_id and has_source_id:
        raise ProposalError("reject_proposal: supply exactly one of task_id or unmaterialised_source_id, not both")
    if not has_task_id and not has_source_id:
        raise ProposalError("reject_proposal: supply exactly one of task_id or unmaterialised_source_id")

    if has_source_id:
        return _reject_unmaterialised_proposal(
            workspace_root=workspace_root,
            backlog_root=backlog_root,
            backlog_index=backlog_index,
            source_task_id=unmaterialised_source_id,
            reason=reason,
        )

    return _reject_per_draft_proposal(
        workspace_root=workspace_root,
        backlog_root=backlog_root,
        backlog_index=backlog_index,
        task_id=task_id,
        reason=reason,
    )


def _reject_per_draft_proposal(
    *,
    workspace_root: Path,
    backlog_root: Path,
    backlog_index: Path,
    task_id: str,
    reason: str,
) -> Path | None:
    """Per-draft rejection path: archive + remove row + strip marker + cascade."""
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

    source = _find_originating_source_task(workspace_root, task_id)
    if source is not None:
        source_file = _find_source_task_file(backlog_root, backlog_index, source)
        if source_file is not None:
            _append_reject_audit_comment(source_file, task_id, reason)
            # Strip the BLOCKED_PENDING_PROPOSAL marker for this specific
            # rejected task so the auto-requeue cascade can correctly
            # evaluate the remaining markers. Without this strip, the
            # cascade would see a marker pointing at a now-rejected ID,
            # treat it as non-terminal (unknown), and refuse to requeue
            # even when all other markers are terminal.
            _strip_pending_proposal_marker(source_file, task_id)
            # Invoke the cascade on the just-rejected id as the "newly
            # terminal" signal for the scan's dependent walk. Source gets
            # re-evaluated; remaining markers all terminal -> source flips.
            # Remaining markers include non-terminal OR no markers remain ->
            # scan abstains per the existing ADR-07 contract.
            BacklogManager()._auto_requeue_marker_dependents(backlog_index, task_id)

    # Refresh Status Summary so the deleted row is no longer counted.
    BacklogManager()._update_status_summary(backlog_index)
    return archive


def _reject_unmaterialised_proposal(
    *,
    workspace_root: Path,
    backlog_root: Path,
    backlog_index: Path,
    source_task_id: str,
    reason: str,
) -> Path:
    """Reject an entire proposal JSON whose drafts have not been materialised.

    Refuses when any task in the JSON has any state other than
    ``UNMATERIALISED`` (a partial-materialise situation the operator should
    resolve with per-task rejection instead).
    """
    json_path = proposal_path(workspace_root, source_task_id)
    if not json_path.is_file():
        raise ProposalError(f"No proposal JSON found at {json_path}")

    proposal = read_proposal(workspace_root, source_task_id)
    for task in proposal.proposed_tasks:
        state = classify_proposed_task(backlog_root, workspace_root, task.suggested_id)
        if state is not ProposalTaskState.UNMATERIALISED:
            raise ProposalError(
                f"Cannot reject {source_task_id} as un-materialised: "
                f"draft {task.suggested_id} is already in state {state.value}. "
                f"Use per-task reject (reject-proposal {task.suggested_id} --reason ...) "
                f"or resolve the draft first."
            )

    # Archive the JSON. Use a dedicated suffix so audit-reviewers can tell
    # un-materialised rejections apart from per-draft rejections at a glance.
    archive_dir = workspace_root / REJECTED_PROPOSAL_DIR_NAME
    archive_dir.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now(tz=UTC).strftime("%Y%m%dT%H%M%SZ")
    archive_path = archive_dir / f"{source_task_id}-unmaterialised-{timestamp}.json"
    json_path.rename(archive_path)

    source_file = _find_source_task_file(backlog_root, backlog_index, source_task_id)
    if source_file is not None:
        timestamp_human = datetime.now(tz=UTC).strftime(COMMENT_TIMESTAMP_FORMAT)
        message = f"[PROPOSAL_JSON_REJECTED] {source_task_id} rejected (un-materialised): {reason}"
        entry = COMMENT_AGENT_TEMPLATE.format(timestamp=timestamp_human, name="task_factory", message=message)
        content = source_file.read_text(encoding="utf-8")
        if COMMENTS_SECTION_HEADER in content:
            content = content.rstrip("\n") + "\n\n" + entry
        else:
            content = content.rstrip("\n") + "\n\n" + COMMENTS_SECTION_HEADER + "\n\n" + entry
        source_file.write_text(content, encoding="utf-8")

    return archive_path


def _append_reject_audit_comment(source_file: Path, task_id: str, reason: str) -> None:
    """Write a ``[PROPOSAL_REJECTED]`` audit line to the source task."""
    timestamp = datetime.now(tz=UTC).strftime(COMMENT_TIMESTAMP_FORMAT)
    message = f"[PROPOSAL_REJECTED] {task_id} rejected: {reason}"
    entry = COMMENT_AGENT_TEMPLATE.format(timestamp=timestamp, name="task_factory", message=message)
    content = source_file.read_text(encoding="utf-8")
    if COMMENTS_SECTION_HEADER in content:
        content = content.rstrip("\n") + "\n\n" + entry
    else:
        content = content.rstrip("\n") + "\n\n" + COMMENTS_SECTION_HEADER + "\n\n" + entry
    source_file.write_text(content, encoding="utf-8")


# Single-marker regex lifted from manager.py. Duplicated here (as a constant,
# not an import) to keep the proposal <-> manager module seam one-way: manager
# depends on nothing in proposal; proposal depends on manager for the cascade
# invocation. If the marker format ever changes, both constants must move in
# lockstep -- the pin in tests/test_plugin/test_agent_structure.py catches
# drift.
_REJECT_MARKER_STRIP_RE = re.compile(r"^.*\[BLOCKED_PENDING_PROPOSAL\]\s+(\S+).*$\n?", re.MULTILINE)


def _strip_pending_proposal_marker(source_file: Path, rejected_task_id: str) -> None:
    """Remove ``[BLOCKED_PENDING_PROPOSAL] <rejected_task_id>`` marker lines.

    Strips every full comment line (not just the marker substring) whose
    marker ID matches ``rejected_task_id`` exactly. Collapses any resulting
    consecutive blank lines down to one so the Comments section stays
    readable.

    No-op when the source file has no matching marker -- safe to call even
    when the rejected task was never promoted.
    """
    content = source_file.read_text(encoding="utf-8")

    def _drop_if_match(match: re.Match[str]) -> str:
        return "" if match.group(1) == rejected_task_id else match.group(0)

    updated = _REJECT_MARKER_STRIP_RE.sub(_drop_if_match, content)
    # Collapse runs of 3+ newlines down to 2 (one blank line between paragraphs).
    updated = re.sub(r"\n{3,}", "\n\n", updated)
    if updated != content:
        source_file.write_text(updated, encoding="utf-8")
