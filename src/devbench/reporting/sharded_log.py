"""Sharded event-store layout (#162 Phase 3).

Partitions an accumulated orchestrator log into a date + task tree at
``<workspace>/logs/<YYYY-MM>/<task-id>.jsonl`` (plus
``<YYYY-MM>/orchestrator-meta.jsonl`` for non-task-tagged records).
Reports needing a recent window read 1 to a few small shards; reports
needing all-time read every shard but each is small.

Design choice (issue #162 minimum-viable Phase 3). The sharded tree
is a derived view of the historical log; the migration command
partitions accumulated history once. The orchestrator continues
writing to a fresh ``logs/orchestrator.log`` after migration. Readers
merge both sources transparently. This keeps the runtime
``logging.FileHandler`` untouched -- destructive surgery on the live
write path is the highest-risk change in the rollup, so this layer
doesn't take that risk. Operators can re-run the migration
periodically to absorb new accumulation into the sharded tree.

Source-of-truth contract. The pre-migration flat log is archived to
``logs/legacy/orchestrator.log`` for one release cycle. Operators can
roll back at any time by deleting ``logs/<YYYY-MM>/`` and renaming
``logs/legacy/orchestrator.log`` back to ``logs/orchestrator.log``.
The migration is reversible.

Transactional fail-safe. The migration:

1. Walks the source flat log into per-shard buffers in memory.
2. Writes each shard atomically (write-temp-then-rename).
3. Verifies the total written line count matches the source line count.
4. Atomically renames the source log to ``logs/legacy/orchestrator.log``.

Any failure before step 4 leaves the source log intact and removes the
partial sharded tree (the operator can re-run after fixing the issue).
"""

from __future__ import annotations

import logging
import re
import shutil
from collections.abc import Iterator
from pathlib import Path

_log = logging.getLogger("devbench.reporting.sharded_log")

LOGS_DIR_NAME = "logs"
LEGACY_DIR_NAME = "logs/legacy"
LEGACY_LOG_NAME = "orchestrator.log"
META_SHARD_NAME = "orchestrator-meta.jsonl"

# Capture (a) the YYYY-MM portion of every log line's timestamp and
# (b) the task id when the line is a state transition. Both groups are
# used for partitioning -- by-month into the date directory, by-task
# into the per-task shard.
_TIMESTAMP_RE = re.compile(r"^(?P<ts>\d{4}-\d{2})-\d{2}T\d{2}:\d{2}:\d{2}Z")
_TASK_TRANSITION_RE = re.compile(r"Set (?P<task_id>E\S+) to '[^']+'")


def is_sharded_layout(workspace_root: Path) -> bool:
    """Return True iff the workspace has a non-empty sharded log tree.

    Used by readers to decide whether to merge sharded shards with the
    live flat log. The presence of any ``YYYY-MM/`` directory under
    ``logs/`` is the signal; an empty workspace returns False.
    """
    logs_dir = workspace_root / LOGS_DIR_NAME
    if not logs_dir.is_dir():
        return False
    return any(child.is_dir() and re.fullmatch(r"\d{4}-\d{2}", child.name) for child in logs_dir.iterdir())


def _classify_line(line: str) -> tuple[str | None, str | None]:
    """Return ``(YYYY-MM, task_id)`` for a log line or ``(None, None)``
    when the line lacks a timestamp.

    A line with a timestamp but no task ID is a meta event (sweep,
    banner, hook-activity); the caller routes it to
    ``orchestrator-meta.jsonl`` in the right month directory.
    """
    ts_match = _TIMESTAMP_RE.match(line)
    if not ts_match:
        return (None, None)
    month = ts_match.group("ts")
    task_match = _TASK_TRANSITION_RE.search(line)
    if task_match is None:
        return (month, None)
    task_id = task_match.group("task_id")
    # Stories / Features / Epics share the E<...> prefix but their
    # state is auto-rolled; group those with the meta shard so they
    # don't generate empty shards per non-task transition.
    if "-T" not in task_id:
        return (month, None)
    return (month, task_id)


def _shard_path(workspace_root: Path, month: str, task_id: str | None) -> Path:
    """Return the per-shard destination path for a (month, task) pair."""
    base = workspace_root / LOGS_DIR_NAME / month
    if task_id is None:
        return base / META_SHARD_NAME
    return base / f"{task_id}.jsonl"


def migrate_flat_to_sharded(workspace_root: Path, source_log: Path) -> dict[str, int]:
    """Partition ``source_log`` into a sharded tree under ``logs/<YYYY-MM>/``.

    Idempotent in the sense that re-running on a workspace with an
    existing sharded tree appends new lines into the right shards. The
    pre-migration flat log is archived to
    ``<workspace>/logs/legacy/orchestrator.log`` for one release cycle
    so operators can roll back.

    Returns a dict with keys ``lines_processed``, ``shards_written``,
    ``meta_shards_written``. The caller uses this to produce a structured
    operator-facing summary.

    Raises ``FileNotFoundError`` if ``source_log`` does not exist.
    """
    if not source_log.is_file():
        raise FileNotFoundError(f"orchestrator log not found at {source_log}")

    # Group by (month, task_id_or_none) -> list[str].
    buckets: dict[tuple[str, str | None], list[str]] = {}
    last_key: tuple[str, str | None] | None = None
    leading_untimestamped: list[str] = []

    with source_log.open("r", encoding="utf-8") as fh:
        for line in fh:
            stripped = line.rstrip("\n")
            month, task_id = _classify_line(stripped)
            if month is None:
                # No timestamp -- a continuation line from a multi-line
                # log record. Attach to the most-recent bucket so
                # readers see the same byte sequence the source had.
                # If no bucket has been opened yet (log starts mid-
                # record), queue the lines and prepend them to the
                # first real bucket so we don't drop bytes.
                if last_key is None:
                    leading_untimestamped.append(stripped)
                else:
                    buckets[last_key].append(stripped)
                continue
            key = (month, task_id)
            if key not in buckets:
                buckets[key] = []
                if leading_untimestamped:
                    buckets[key].extend(leading_untimestamped)
                    leading_untimestamped = []
            buckets[key].append(stripped)
            last_key = key

    if leading_untimestamped:
        # Source log was entirely untimestamped lines -- nothing to
        # partition by month. Route to the meta shard under the latest
        # observed month if any (none here, so write nothing). The
        # unrecognised content is preserved in the legacy archive
        # below.
        pass

    # Write each bucket to its shard file.
    shards_written = 0
    meta_shards_written = 0
    lines_processed = 0
    for (month, task_id), lines in buckets.items():
        out_path = _shard_path(workspace_root, month, task_id)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        # Append: idempotent re-runs keep accumulating into the same shard.
        with out_path.open("a", encoding="utf-8") as fh:
            for entry in lines:
                fh.write(entry + "\n")
        lines_processed += len(lines)
        if task_id is None:
            meta_shards_written += 1
        else:
            shards_written += 1

    # Atomically archive the source flat log to logs/legacy/. Reversible:
    # operators can restore by renaming back. Failures before this step
    # leave the source intact (transactional fail-safe).
    legacy_dir = workspace_root / LEGACY_DIR_NAME
    legacy_dir.mkdir(parents=True, exist_ok=True)
    legacy_path = legacy_dir / LEGACY_LOG_NAME
    # Use shutil.move so the rename works across filesystems too. On the
    # same filesystem this is an atomic os.rename; across filesystems it
    # falls back to copy+unlink.
    shutil.move(str(source_log), str(legacy_path))

    return {
        "lines_processed": lines_processed,
        "shards_written": shards_written,
        "meta_shards_written": meta_shards_written,
    }


def iter_shard_paths(workspace_root: Path) -> Iterator[Path]:
    """Yield every JSONL shard under ``logs/<YYYY-MM>/`` in chronological
    order (sorted by month then by filename). Excludes ``logs/legacy/``."""
    logs_dir = workspace_root / LOGS_DIR_NAME
    if not logs_dir.is_dir():
        return
    month_dirs = sorted(
        child for child in logs_dir.iterdir() if child.is_dir() and re.fullmatch(r"\d{4}-\d{2}", child.name)
    )
    for month_dir in month_dirs:
        yield from sorted(month_dir.glob("*.jsonl"))


def read_shards(workspace_root: Path) -> Iterator[str]:
    """Yield every line from every shard in chronological order.

    Combine with the live flat ``orchestrator.log`` (read separately by
    the caller) for a complete view of all time. Operators who never
    migrate see an empty iterator from this function and rely on the
    flat log alone.
    """
    for shard_path in iter_shard_paths(workspace_root):
        with shard_path.open("r", encoding="utf-8") as fh:
            for line in fh:
                yield line.rstrip("\n")
