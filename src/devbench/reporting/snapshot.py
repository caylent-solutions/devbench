"""Pre-rendered report snapshot (#162 Phase 6).

Materialises a snapshot of the rendered ``devbench report`` output to
``<workspace>/.devbench/report-snapshot.json`` after every orchestrate-
loop iteration. ``devbench report`` can read the snapshot directly when
it is fresh -- no log parse, no per-window aggregation, no event-index
refresh -- and falls back to live aggregation through the Phase 1+4
cache when the snapshot is stale or missing.

Freshness contract. The snapshot carries the orchestrator log's
``(mtime_ns, size)`` tuple at the moment it was written. ``read_snapshot``
returns the cached report only when the current log's tuple still
matches; any change (new bytes appended, log rotated, file shrunk)
invalidates the snapshot and the caller falls back to live.

Atomic write. ``write_snapshot`` writes to ``report-snapshot.json.tmp``
in the same directory then renames atomically (POSIX same-filesystem
rename is atomic). A crash mid-write leaves either the old snapshot
intact or the new snapshot fully present -- never a torn write that
returns a corrupt JSON document.

Schema version. The top-level ``schema_version`` lets future code
invalidate snapshots from older devbench releases without manual
cleanup. A version mismatch on read returns ``None``; the caller
rebuilds via the live path on next iteration.

Self-healing. Snapshot deletion is always safe -- the next
``devbench report`` invocation rebuilds via the live aggregation path
through the Phase 1+4 cache. The snapshot is a derived view, never
the source of truth.
"""

from __future__ import annotations

import json as _json
import logging
from dataclasses import dataclass
from pathlib import Path

_log = logging.getLogger("devbench.reporting.snapshot")

SNAPSHOT_DIR_NAME = ".devbench"
SNAPSHOT_FILE_NAME = "report-snapshot.json"

_SCHEMA_VERSION = 2


@dataclass(frozen=True)
class SnapshotData:
    """Decoded snapshot payload. Returned by ``read_snapshot`` when fresh."""

    report_text: str
    log_mtime_ns: int
    log_size: int


def snapshot_path(workspace_root: Path) -> Path:
    """Return the canonical path of the report snapshot file for a workspace."""
    return workspace_root / SNAPSHOT_DIR_NAME / SNAPSHOT_FILE_NAME


def _stat_or_zero(path: Path) -> tuple[int, int]:
    """Return ``(mtime_ns, size)`` for ``path``; ``(0, 0)`` when absent.

    Used so that recording a snapshot against a missing source still
    produces a valid freshness key. The next time the source comes
    into existence (any non-zero mtime / size), the snapshot reads
    as stale and the caller rebuilds.
    """
    try:
        stat = path.stat()
    except (FileNotFoundError, OSError):
        return (0, 0)
    return (stat.st_mtime_ns, stat.st_size)


def _compute_log_freshness_key(
    workspace_root: Path,
    log_path: Path,
) -> tuple[int, int, list[list[int]]]:
    """Compute the freshness key for a workspace's orchestrator log set.

    Issue #168: post-Phase-3 migration the orch-log content is
    distributed across the sharded tree under ``logs/<YYYY-MM>/`` plus
    the live flat ``logs/orchestrator.log``. The freshness key carries:

    1. ``(live_log_mtime_ns, live_log_size)`` -- the live flat log.
    2. List of ``(shard_mtime_ns, shard_size)`` pairs in
       chronological-order-of-iteration -- one entry per shard.

    Any change to any source -- live log appended, shard updated,
    new shard added, shard removed -- changes the key and invalidates
    the snapshot. Workspaces with no sharded layout produce an empty
    list as the third element; the key reduces to "(live, live, [])"
    which matches the legacy single-source snapshot semantics.
    """
    from devbench.reporting.sharded_log import is_sharded_layout, iter_shard_paths

    live_mtime, live_size = _stat_or_zero(log_path)
    shard_keys: list[list[int]] = []
    if is_sharded_layout(workspace_root):
        for shard_path in iter_shard_paths(workspace_root):
            shard_mtime, shard_size = _stat_or_zero(shard_path)
            shard_keys.append([shard_mtime, shard_size])
    return (live_mtime, live_size, shard_keys)


def write_snapshot(
    workspace_root: Path,
    report_text: str,
    log_path: Path,
) -> Path:
    """Persist ``report_text`` plus the source log set's freshness key.

    Writes to ``<workspace>/.devbench/report-snapshot.json.tmp`` then
    renames atomically over the canonical filename so a crash mid-write
    never leaves a torn JSON document. The freshness key (issue #168)
    covers the live flat log AND the sharded-tree shard mtime
    aggregate; reads validate against the current state of every
    source before returning the cached text.
    """
    out_path = snapshot_path(workspace_root)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    live_mtime, live_size, shard_keys = _compute_log_freshness_key(workspace_root, log_path)
    payload = {
        "schema_version": _SCHEMA_VERSION,
        "log_mtime_ns": live_mtime,
        "log_size": live_size,
        "shard_keys": shard_keys,
        "report_text": report_text,
    }
    tmp_path = out_path.with_suffix(out_path.suffix + ".tmp")
    tmp_path.write_text(_json.dumps(payload), encoding="utf-8")
    tmp_path.replace(out_path)
    return out_path


def read_snapshot(workspace_root: Path, log_path: Path) -> SnapshotData | None:
    """Return the cached snapshot when fresh, or None when stale / missing.

    Stale = the current freshness key differs from what the snapshot
    recorded. Issue #168: the freshness key covers BOTH the live flat
    log AND the sharded-tree mtime aggregate, so any shard mutation
    invalidates the snapshot.

    Missing = the snapshot file does not exist. Schema-version
    mismatch (e.g. v1 snapshot from before #168) also returns None so
    future devbench versions can evolve the snapshot format without
    manual cleanup.

    Self-healing: when this returns None the caller rebuilds via the
    live path; the next ``write_snapshot`` overwrites the stale file.
    """
    payload = _load_payload(snapshot_path(workspace_root))
    if payload is None:
        return None
    decoded = _validate_payload(payload)
    if decoded is None:
        return None
    snapshot_mtime, snapshot_size, snapshot_shards, report_text = decoded
    current_key = _compute_log_freshness_key(workspace_root, log_path)
    current_mtime, current_size, current_shards = current_key
    snapshot_shards_normalised = [tuple(pair) for pair in snapshot_shards]
    current_shards_normalised = [tuple(pair) for pair in current_shards]
    if (
        snapshot_mtime != current_mtime
        or snapshot_size != current_size
        or snapshot_shards_normalised != current_shards_normalised
    ):
        return None
    return SnapshotData(
        report_text=report_text,
        log_mtime_ns=snapshot_mtime,
        log_size=snapshot_size,
    )


def _load_payload(path: Path) -> dict | None:
    """Return the JSON payload, or None when missing / corrupt.

    Self-healing: a corrupt snapshot reads as missing; the next write
    overwrites it with a valid payload.
    """
    if not path.is_file():
        return None
    try:
        payload = _json.loads(path.read_text(encoding="utf-8"))
    except (_json.JSONDecodeError, OSError):
        return None
    return payload if isinstance(payload, dict) else None


def _validate_payload(payload: dict) -> tuple[int, int, list[list[int]], str] | None:
    """Validate the payload's required fields (issue #168 schema v2).

    Returns ``(live_mtime, live_size, shard_keys, report_text)`` or
    ``None`` when any required field is missing / has the wrong type.
    Schema-version mismatch is treated as invalid (forces a rebuild
    on next write).
    """
    if payload.get("schema_version") != _SCHEMA_VERSION:
        return None
    mtime = payload.get("log_mtime_ns")
    size = payload.get("log_size")
    shard_keys = payload.get("shard_keys")
    report_text = payload.get("report_text")
    if (
        not isinstance(mtime, int)
        or not isinstance(size, int)
        or not isinstance(report_text, str)
        or not isinstance(shard_keys, list)
    ):
        return None
    if not _shard_keys_well_formed(shard_keys):
        return None
    return (mtime, size, shard_keys, report_text)


def _shard_keys_well_formed(shard_keys: list) -> bool:
    """Each shard-key entry must be a 2-element list of ints."""
    for entry in shard_keys:
        if not isinstance(entry, list) or len(entry) != 2:
            return False
        if not all(isinstance(v, int) for v in entry):
            return False
    return True
