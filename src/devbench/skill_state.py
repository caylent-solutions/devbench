"""Bounded skill iterate-until-perfect mechanism (spec section 4.6.0, issue #204).

Provides the :class:`SkillState` dataclass and three helpers
(:func:`read_checkpoint`, :func:`write_checkpoint`, :func:`emit_audit`) that
the four onboarding skills use to track self-critique iterations.

Per spec 4.6.0, each skill runs a bounded loop:

1. ``read_checkpoint(skill_name, workspace_root)`` -- returns the persisted
   iteration counter or ``None`` on first run.
2. Skill computes ``unresolved_count``; emits
   :data:`devbench.constants.SKILL_AUDIT_QUALITY_THRESHOLD_REACHED` and
   exits success when ``unresolved_count <= SKILL_QUALITY_THRESHOLD``.
3. Otherwise ``write_checkpoint(...)`` increments the iteration. When the
   counter reaches :data:`devbench.constants.SKILL_MAX_ITERATIONS`, the skill
   emits :data:`devbench.constants.SKILL_AUDIT_MAX_ITERATIONS_REACHED` via
   :func:`emit_audit` and exits non-zero so an operator can intervene.

The write path is atomic (tmp + rename) so readers never observe a partial
file. The read path raises on malformed JSON or I/O failure -- no silent
recovery, per CLAUDE.md fail-fast.

The audit-emission helper appends a single line to
``<workspace_root>/logs/orchestrator.log`` so the existing report and
hook-tail pipelines see the event without any new infrastructure.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path

from devbench.constants import (
    DEFAULT_LOG_FILENAME,
    DEFAULT_LOG_SUBDIR,
    SKILL_STATE_DIR_NAME,
)


@dataclass
class SkillState:
    """Persisted self-critique iteration state for one skill."""

    iteration: int
    unresolved_count: int
    started_at: str


@dataclass
class PerTaskCheckpoint:
    """Persisted per-task authoring progress for one skill (issue #221 A3).

    Used by ``spec-to-backlog`` to track which leaf-task IDs have already
    been written so a re-invocation skips them instead of regenerating
    every file from scratch. The checkpoint is a separate JSON file from
    :class:`SkillState`'s iteration counter so the two pieces of skill
    progress can evolve independently.

    Attributes:
        completed_task_ids: Set of fully-qualified work-unit IDs
            (e.g., ``E1-F1-S1-T1``) the skill has already authored.
        last_updated: ISO-8601 UTC timestamp of the most recent write.
    """

    completed_task_ids: set[str]
    last_updated: str


def _checkpoint_path(skill_name: str, workspace_root: Path) -> Path:
    """Return the absolute checkpoint path for *skill_name* under *workspace_root*."""
    return workspace_root / SKILL_STATE_DIR_NAME / f"{skill_name}.json"


def _per_task_checkpoint_path(skill_name: str, workspace_root: Path) -> Path:
    """Return the absolute per-task checkpoint path for *skill_name*."""
    return workspace_root / SKILL_STATE_DIR_NAME / f"{skill_name}-tasks.json"


def read_checkpoint(skill_name: str, workspace_root: Path) -> SkillState | None:
    """Return the persisted SkillState for *skill_name*, or ``None`` if absent.

    Args:
        skill_name: The skill identifier (e.g. ``create-spec``).
        workspace_root: Absolute path to the devbench workspace root.

    Returns:
        The decoded :class:`SkillState` when the checkpoint file exists, or
        ``None`` when it does not.

    Raises:
        json.JSONDecodeError: When the checkpoint file exists but is not valid JSON.
        KeyError: When the JSON payload is missing a required field.
        OSError: When the file exists but cannot be read.
    """
    path = _checkpoint_path(skill_name, workspace_root)
    if not path.exists():
        return None
    payload = json.loads(path.read_text(encoding="utf-8"))
    return SkillState(
        iteration=payload["iteration"],
        unresolved_count=payload["unresolved_count"],
        started_at=payload["started_at"],
    )


def write_checkpoint(skill_name: str, state: SkillState, workspace_root: Path) -> None:
    """Atomically persist *state* for *skill_name* under *workspace_root*.

    Uses a tmp-then-rename pattern so a reader never observes a partial file.

    Args:
        skill_name: The skill identifier (e.g. ``create-spec``).
        state: The :class:`SkillState` to persist.
        workspace_root: Absolute path to the devbench workspace root.

    Raises:
        OSError: When the checkpoint directory cannot be created or the file
            cannot be written / renamed.
    """
    path = _checkpoint_path(skill_name, workspace_root)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_suffix(path.suffix + ".tmp")
    tmp_path.write_text(json.dumps(asdict(state), indent=2), encoding="utf-8")
    tmp_path.replace(path)


def read_per_task_checkpoint(skill_name: str, workspace_root: Path) -> PerTaskCheckpoint | None:
    """Return the persisted per-task checkpoint, or ``None`` if absent (issue #221 A3).

    Args:
        skill_name: The skill identifier (e.g. ``spec-to-backlog``).
        workspace_root: Absolute path to the devbench workspace root.

    Returns:
        The decoded :class:`PerTaskCheckpoint` when the file exists, or
        ``None`` when it does not.

    Raises:
        json.JSONDecodeError: When the file exists but is not valid JSON.
        KeyError: When the JSON payload is missing a required field.
        OSError: When the file exists but cannot be read.
    """
    path = _per_task_checkpoint_path(skill_name, workspace_root)
    if not path.exists():
        return None
    payload = json.loads(path.read_text(encoding="utf-8"))
    return PerTaskCheckpoint(
        completed_task_ids=set(payload["completed_task_ids"]),
        last_updated=payload["last_updated"],
    )


def write_per_task_checkpoint(
    skill_name: str,
    checkpoint: PerTaskCheckpoint,
    workspace_root: Path,
) -> None:
    """Atomically persist the per-task checkpoint for *skill_name* (issue #221 A3).

    Uses the same tmp-then-rename pattern as :func:`write_checkpoint` so
    a reader never observes a partial file.

    Args:
        skill_name: The skill identifier (e.g. ``spec-to-backlog``).
        checkpoint: The :class:`PerTaskCheckpoint` to persist.
        workspace_root: Absolute path to the devbench workspace root.

    Raises:
        OSError: When the checkpoint directory cannot be created or the
            file cannot be written / renamed.
    """
    path = _per_task_checkpoint_path(skill_name, workspace_root)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_suffix(path.suffix + ".tmp")
    payload = {
        "completed_task_ids": sorted(checkpoint.completed_task_ids),
        "last_updated": checkpoint.last_updated,
    }
    tmp_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    tmp_path.replace(path)


def emit_audit(
    skill_name: str,
    tag: str,
    fields: dict[str, str],
    workspace_root: Path,
) -> None:
    """Append a structured audit row to the orchestrator log.

    The emitted line matches ``<ISO-8601 UTC> [skill_state] <tag> skill=<name>[ key=value ...]``.
    Existing report + hook-tail tooling parses any line starting with the
    same tag prefix, so this helper integrates without new infrastructure.

    Args:
        skill_name: The skill identifier (e.g. ``create-spec``).
        tag: One of :data:`devbench.constants.SKILL_AUDIT_MAX_ITERATIONS_REACHED`
            or :data:`devbench.constants.SKILL_AUDIT_QUALITY_THRESHOLD_REACHED`.
        fields: Additional key=value fields to include after ``skill=<name>``.
            Keys must match ``^[a-z_]+$``; values are formatted with ``str()``.
        workspace_root: Absolute path to the devbench workspace root.

    Raises:
        ValueError: When *tag* does not start with ``[SKILL_`` and end with ``]``,
            or when any key in *fields* contains whitespace.
        OSError: When the log directory cannot be created or the line cannot
            be appended.
    """
    if not (tag.startswith("[SKILL_") and tag.endswith("]")):
        msg = f"audit tag must look like [SKILL_*] (got {tag!r})"
        raise ValueError(msg)
    for key in fields:
        if not key or any(ch.isspace() for ch in key):
            msg = f"audit field key must be non-empty and whitespace-free (got {key!r})"
            raise ValueError(msg)

    log_path = workspace_root / DEFAULT_LOG_SUBDIR / DEFAULT_LOG_FILENAME
    log_path.parent.mkdir(parents=True, exist_ok=True)

    ts = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    body_parts = [f"skill={skill_name}"]
    for key, value in fields.items():
        body_parts.append(f"{key}={value}")
    line = f"{ts} [skill_state] {tag} " + " ".join(body_parts) + "\n"

    with log_path.open("a", encoding="utf-8") as handle:
        handle.write(line)
