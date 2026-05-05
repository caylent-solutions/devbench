"""Columnar cold-archive for ended orchestrator sessions (#162 Phase 7).

After an orchestrate session ends, operators can archive its log lines
to a per-session Parquet file under ``logs/legacy/<session-id>.parquet``
for long-term retention. The archive is faster to read for all-time
queries than walking thousands of JSONL lines and lighter on disk
(compressed columnar format).

Opt-in dependency. ``pyarrow`` is the only new package introduced by
this layer. It is NOT a mandatory devbench dependency -- operators who
don't archive don't install it. Install via:

    pip install devbench[archive]

When ``pyarrow`` is missing, every public function in this module
raises :class:`ArchiveDependencyMissingError` with a structured message
naming the install command. No fallback path; the operator either
installs the extra or doesn't archive (per CLAUDE.md fail-fast).

Source-of-truth contract. The JSONL log remains authoritative. The
Parquet archive is a derived view; deleting an archive is always
safe (the source JSONL still holds the events). Round-trip parity
is pinned by tests: writing a JSONL session to Parquet and reading
back produces byte-identical event records.
"""

from __future__ import annotations

import json as _json
from collections.abc import Iterator
from pathlib import Path
from typing import Any

LEGACY_DIR_NAME = "logs/legacy"


class ArchiveDependencyMissingError(RuntimeError):
    """Raised when ``pyarrow`` is required but not installed.

    Carries the install command in the message so operators can paste
    the fix directly. Per CLAUDE.md fail-fast: no silent fallback to
    JSONL when archive operations are explicitly requested.
    """

    def __init__(self, operation: str) -> None:
        super().__init__(f"{operation} requires the 'archive' extra. Install with: pip install devbench[archive]")


def _import_pyarrow() -> tuple[Any, Any]:
    """Lazy-import pyarrow; raise structured error when absent.

    Returns ``(pyarrow_module, pyarrow_parquet_module)`` on success.
    Typed as ``Any`` because pyarrow is an optional dependency: callers
    cannot import it at module load (mainline installs don't carry it),
    so static typing has no shape to check against. Runtime behaviour
    is pinned by ``tests/test_reporting/test_archive.py``.
    """
    try:
        import pyarrow as pa
        import pyarrow.parquet as pq
    except ImportError as exc:
        raise ArchiveDependencyMissingError("archive operations") from exc
    return pa, pq


def archive_path(workspace_root: Path, session_id: str) -> Path:
    """Return the canonical Parquet archive path for a session id."""
    return workspace_root / LEGACY_DIR_NAME / f"{session_id}.parquet"


def archive_session(
    workspace_root: Path,
    session_id: str,
    log_path: Path,
) -> Path:
    """Write every JSONL line in ``log_path`` to a Parquet archive.

    Each row of the resulting Parquet file is one parsed JSON event
    plus the original raw line (so a round-trip back to JSONL is
    byte-faithful). Lines that fail to parse as JSON are kept as
    raw-only rows -- the archive never silently drops events.

    The destination is ``<workspace>/logs/legacy/<session-id>.parquet``.
    Returns the destination path on success.

    Raises :class:`ArchiveDependencyMissingError` if ``pyarrow`` is not
    installed; raises :class:`FileNotFoundError` if ``log_path`` does
    not exist.
    """
    pa, pq = _import_pyarrow()

    if not log_path.is_file():
        raise FileNotFoundError(f"orchestrator log not found at {log_path}")

    raw_lines: list[str] = []
    parsed_payloads: list[str] = []
    with log_path.open("r", encoding="utf-8") as fh:
        for line in fh:
            stripped = line.rstrip("\n")
            raw_lines.append(stripped)
            # Try to capture the JSON payload alongside the raw line so
            # downstream readers can skip re-parsing. Lines that aren't
            # JSON keep an empty payload string; the raw line still rides.
            try:
                _json.loads(stripped)
                parsed_payloads.append(stripped)
            except _json.JSONDecodeError:
                parsed_payloads.append("")

    table = pa.table({"raw_line": raw_lines, "parsed_json": parsed_payloads})
    out_path = archive_path(workspace_root, session_id)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    pq.write_table(table, str(out_path))
    return out_path


def read_archived_session(archive_file: Path) -> Iterator[str]:
    """Iterate the raw log lines from a Parquet archive.

    Yields the same string sequence ``log_path`` would have yielded
    line-by-line, so callers can treat the archive interchangeably
    with the JSONL source.

    Raises :class:`ArchiveDependencyMissingError` if ``pyarrow`` is not
    installed; raises :class:`FileNotFoundError` if ``archive_file``
    does not exist.
    """
    _pa, pq = _import_pyarrow()

    if not archive_file.is_file():
        raise FileNotFoundError(f"archive not found at {archive_file}")

    table = pq.read_table(str(archive_file), columns=["raw_line"])
    raw_column = table.column("raw_line")
    for chunk in raw_column.iterchunks():
        yield from chunk.to_pylist()
