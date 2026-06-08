"""Agnostic resolution catalog for the auto-resolve engine.

Issue #263, spec Section 4 E11-F2-S1.

The catalog persists learned resolution patterns at
``<workspace>/.devbench/operator-resolution-catalog.json``.  Each entry is
keyed by ``<classification>:<normalized_signature>`` and records:

- The classification and normalized signature (no backlog or app content).
- The remediation verb that was applied.
- A success count and failure count.
- A last-applied timestamp (UTC ISO-8601).

The catalog is schema-versioned (``CATALOG_SCHEMA_VERSION``).  A malformed or
legacy catalog (wrong or missing schema version, invalid JSON, unexpected
structure) is treated as empty and self-heals -- the load path never raises a
fatal error.  Writes are atomic: the new payload is first written to a
``<filename>.tmp`` sibling and then renamed into place (the same pattern used
by ``watchdog.py``).

Public API
----------
- ``CATALOG_SCHEMA_VERSION``    -- integer schema sentinel for future migrations.
- ``CatalogRecord``             -- frozen dataclass holding a single entry.
- ``catalog_path(workspace_root)``        -- path resolver (reusable, no I/O).
- ``load_catalog(workspace_root)``        -- self-healing reader.
- ``save_catalog(workspace_root, entries)`` -- atomic writer.
- ``lookup_entry(workspace_root, classification, normalized_signature)``
- ``record_outcome(workspace_root, classification, normalized_signature,
                    remediation, outcome)``
"""

from __future__ import annotations

import dataclasses
import json
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal

__all__ = [
    "CATALOG_SCHEMA_VERSION",
    "CatalogRecord",
    "catalog_path",
    "load_catalog",
    "lookup_entry",
    "record_outcome",
    "save_catalog",
]

# Schema version sentinel.  Bump when the on-disk format changes in a
# backward-incompatible way; the loader will treat any version != this as empty.
CATALOG_SCHEMA_VERSION: int = 1

# Valid outcome literals accepted by record_outcome().
# "novel" records an unrecognized signature for operator review; it does not
# increment success or failure counts.  The engine routes novel signatures to
# advise-only and never auto-applies them until the operator confirms the
# pattern (spec Section 4 E11-F2-S2 AC-1).
OutcomeLiteral = Literal["applied", "escalated", "failed", "novel"]

_VALID_OUTCOMES: frozenset[str] = frozenset({"applied", "escalated", "failed", "novel"})

# Filename of the catalog inside the .devbench state directory.
_CATALOG_FILENAME: str = "operator-resolution-catalog.json"

# Intermediate temp-file suffix used for atomic writes.
_TMP_SUFFIX: str = ".tmp"


@dataclasses.dataclass(frozen=True)
class CatalogRecord:
    """A single entry in the operator resolution catalog.

    Attributes:
        classification: The block-classifier bucket (e.g. ``"RUNTIME_DEGRADATION"``).
            Contains no backlog or application-specific content.
        normalized_signature: An agnostic signature derived from the blocker
            pattern, stripped of any task ID or application-specific text.
        remediation: The remediation verb that produced the recorded outcome.
        success_count: Number of times this pattern was successfully auto-applied.
        failure_count: Number of times the apply attempt failed.
        last_applied: UTC timestamp of the most recent outcome recording.
    """

    classification: str
    normalized_signature: str
    remediation: str
    success_count: int
    failure_count: int
    last_applied: datetime


def catalog_path(workspace_root: Path) -> Path:
    """Return the absolute path to the catalog file.

    Args:
        workspace_root: Absolute path to the devbench workspace root
            (``DEVBENCH_WORKSPACE_ROOT``).

    Returns:
        ``<workspace_root>/.devbench/operator-resolution-catalog.json``
    """
    return workspace_root / ".devbench" / _CATALOG_FILENAME


def _make_catalog_key(classification: str, normalized_signature: str) -> str:
    """Return the composite catalog key for a classification + signature pair.

    Args:
        classification: The block-classifier bucket.
        normalized_signature: The agnostic blocker signature.

    Returns:
        ``"<classification>:<normalized_signature>"``
    """
    return f"{classification}:{normalized_signature}"


def _record_to_dict(record: CatalogRecord) -> dict[str, object]:
    """Serialize a CatalogRecord to a JSON-safe dictionary."""
    return {
        "classification": record.classification,
        "normalized_signature": record.normalized_signature,
        "remediation": record.remediation,
        "success_count": record.success_count,
        "failure_count": record.failure_count,
        "last_applied": record.last_applied.isoformat(),
    }


def _record_from_dict(data: dict[str, object]) -> CatalogRecord:
    """Deserialize a CatalogRecord from a dictionary.

    Args:
        data: Raw dictionary from the JSON payload.

    Returns:
        A populated ``CatalogRecord``.

    Raises:
        KeyError: When a required field is absent.
        TypeError: When a field has an unexpected type.
        ValueError: When ``last_applied`` is not a valid ISO-8601 timestamp.
    """
    last_applied_raw = str(data["last_applied"])
    last_applied = datetime.fromisoformat(last_applied_raw)
    if last_applied.tzinfo is None:
        last_applied = last_applied.replace(tzinfo=UTC)
    success_raw = data["success_count"]
    failure_raw = data["failure_count"]
    if not isinstance(success_raw, int):
        raise TypeError(f"success_count must be int, got {type(success_raw)!r}")
    if not isinstance(failure_raw, int):
        raise TypeError(f"failure_count must be int, got {type(failure_raw)!r}")
    return CatalogRecord(
        classification=str(data["classification"]),
        normalized_signature=str(data["normalized_signature"]),
        remediation=str(data["remediation"]),
        success_count=success_raw,
        failure_count=failure_raw,
        last_applied=last_applied,
    )


def load_catalog(workspace_root: Path) -> dict[str, CatalogRecord]:
    """Load the catalog from disk, self-healing on any parse or schema error.

    If the catalog file is absent, malformed, or carries a schema version
    different from ``CATALOG_SCHEMA_VERSION``, the function logs a WARNING to
    stderr and returns an empty dict -- it never raises a fatal error.

    Args:
        workspace_root: Absolute path to the devbench workspace root.

    Returns:
        A dict mapping ``"<classification>:<normalized_signature>"`` keys to
        ``CatalogRecord`` instances.  Empty when the catalog is absent or
        unreadable.
    """
    path = catalog_path(workspace_root)
    if not path.exists():
        return {}

    raw_text = path.read_text(encoding="utf-8")

    try:
        payload = json.loads(raw_text)
    except json.JSONDecodeError as exc:
        print(
            f"WARNING: operator-resolution-catalog: malformed JSON in {path}: {exc}. "
            "Treating catalog as empty (self-healing).",
            file=sys.stderr,
        )
        return {}

    if not isinstance(payload, dict):
        print(
            f"WARNING: operator-resolution-catalog: malformed catalog at {path}: "
            "expected a JSON object at the top level. Treating catalog as empty (self-healing).",
            file=sys.stderr,
        )
        return {}

    schema_version = payload.get("schema_version")
    if schema_version != CATALOG_SCHEMA_VERSION:
        print(
            f"WARNING: operator-resolution-catalog: schema version mismatch in {path}: "
            f"expected {CATALOG_SCHEMA_VERSION}, got {schema_version!r}. "
            "Treating catalog as empty (self-healing).",
            file=sys.stderr,
        )
        return {}

    entries_raw = payload.get("entries")
    if not isinstance(entries_raw, dict):
        print(
            f"WARNING: operator-resolution-catalog: malformed entries in {path}: "
            "expected a dict under 'entries'. Treating catalog as empty (self-healing).",
            file=sys.stderr,
        )
        return {}

    entries: dict[str, CatalogRecord] = {}
    for key, value in entries_raw.items():
        if not isinstance(value, dict):
            print(
                f"WARNING: operator-resolution-catalog: skipping entry {key!r} in {path}: "
                f"expected a dict, got {type(value)!r}.",
                file=sys.stderr,
            )
            continue
        try:
            entries[key] = _record_from_dict(value)
        except Exception as exc:
            print(
                f"WARNING: operator-resolution-catalog: skipping entry {key!r} in {path}: {exc}.",
                file=sys.stderr,
            )
    return entries


def save_catalog(workspace_root: Path, entries: dict[str, CatalogRecord]) -> None:
    """Write the catalog to disk using an atomic tmp-then-replace write.

    The function ensures the ``.devbench/`` directory exists, writes the new
    payload to a ``.tmp`` sibling, then renames it to the final path.  An
    interrupted write therefore never leaves a corrupt catalog file.

    Args:
        workspace_root: Absolute path to the devbench workspace root.
        entries: Mapping of catalog keys to ``CatalogRecord`` instances.
    """
    path = catalog_path(workspace_root)
    path.parent.mkdir(parents=True, exist_ok=True)

    serialized_entries = {key: _record_to_dict(record) for key, record in entries.items()}
    payload = {
        "schema_version": CATALOG_SCHEMA_VERSION,
        "entries": serialized_entries,
    }
    json_text = json.dumps(payload, indent=2) + "\n"

    tmp_path = path.with_suffix(path.suffix + _TMP_SUFFIX)
    tmp_path.write_text(json_text, encoding="utf-8")
    tmp_path.replace(path)


def lookup_entry(
    workspace_root: Path,
    classification: str,
    normalized_signature: str,
) -> CatalogRecord | None:
    """Return the catalog record for a classification + signature pair, or None.

    Args:
        workspace_root: Absolute path to the devbench workspace root.
        classification: The block-classifier bucket.
        normalized_signature: The agnostic blocker signature.

    Returns:
        The matching ``CatalogRecord`` if present, otherwise ``None``.
    """
    entries = load_catalog(workspace_root)
    key = _make_catalog_key(classification, normalized_signature)
    return entries.get(key)


def record_outcome(
    workspace_root: Path,
    *,
    classification: str,
    normalized_signature: str,
    remediation: str,
    outcome: str,
) -> None:
    """Record the outcome of an auto-resolve attempt in the catalog.

    Loads the current catalog, updates or creates the entry for the given
    classification + signature pair, and atomically saves the result.

    - ``"applied"``   -- increments ``success_count`` and updates ``last_applied``.
    - ``"failed"``    -- increments ``failure_count`` and updates ``last_applied``.
    - ``"escalated"`` -- updates ``last_applied`` only (no count change).
    - ``"novel"``     -- records the signature for operator review; updates
                         ``last_applied`` only (no count change).  The engine
                         uses this to mark unrecognized signatures so the
                         operator can confirm them before auto-apply proceeds
                         (spec Section 4 E11-F2-S2 AC-1).

    Args:
        workspace_root: Absolute path to the devbench workspace root.
        classification: The block-classifier bucket.
        normalized_signature: The agnostic blocker signature.
        remediation: The remediation verb that was applied.
        outcome: One of ``"applied"``, ``"escalated"``, ``"failed"``, or ``"novel"``.

    Raises:
        ValueError: When ``outcome`` is not a recognised value.
    """
    if outcome not in _VALID_OUTCOMES:
        valid = ", ".join(sorted(_VALID_OUTCOMES))
        raise ValueError(f"ERROR: record_outcome: invalid outcome {outcome!r}. Expected one of: {valid}.")

    entries = load_catalog(workspace_root)
    key = _make_catalog_key(classification, normalized_signature)
    now = datetime.now(UTC)

    existing = entries.get(key)
    if existing is None:
        success_count = 0
        failure_count = 0
    else:
        success_count = existing.success_count
        failure_count = existing.failure_count

    if outcome == "applied":
        success_count += 1
    elif outcome == "failed":
        failure_count += 1
    # "escalated" and "novel" leave both counts unchanged

    entries[key] = CatalogRecord(
        classification=classification,
        normalized_signature=normalized_signature,
        remediation=remediation,
        success_count=success_count,
        failure_count=failure_count,
        last_applied=now,
    )
    save_catalog(workspace_root, entries)
