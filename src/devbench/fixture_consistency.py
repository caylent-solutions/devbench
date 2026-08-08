"""Fixture-catalog cross-reference check (issue #08).

A feature's data-fetch/lookup logic is frequently correct, but reads from a
mock/fixture lookup table whose keys (SKUs, ids, reference numbers, etc.)
were fabricated, keyed in the wrong namespace, or left incomplete relative
to the project's canonical shared fixture/demo dataset -- so the feature is
functionally dead or crashes for real records even though the underlying
logic is sound. This survives the unit suite because each task's own tests
construct a self-consistent fixture inline rather than exercising the
shared canonical dataset.

This module implements the opt-in check driven by the ``fixture_consistency``
block of ``backlog/config/devbench.yaml`` (see ``config-schema.json`` and
``config_loader.FixtureConsistencyConfig``):

1. Loads each configured canonical fixture/dataset file and collects the
   set of identifier values found under the configured
   ``identifier_field``.
2. Loads each configured scan target (a mock/fixture file) and collects
   the same shape of identifier values.
3. Flags any scan-target identifier value that is absent from its
   canonical source's value set -- unless the workspace explicitly
   allowlisted it via ``allow_missing`` (the opt-out for fixtures that
   intentionally model a not-found/empty-state edge case).
4. Flags a canonical source whose distinct identifier-value count does not
   match a configured ``expected_count`` (the backfill-coverage check).

The check is a deliberate no-op (returns no findings) when the workspace
has not configured any ``canonical_sources`` -- devbench cannot infer a
target repo's fixture-file layout on its own, so this is an explicit,
opt-in config surface rather than a default-on heuristic.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

import yaml

if TYPE_CHECKING:
    from devbench.config_loader import (
        FixtureCanonicalSource,
        FixtureConsistencyConfig,
        FixtureScanTarget,
    )

__all__ = [
    "FixtureFinding",
    "check_fixture_consistency",
    "collect_identifiers",
]


@dataclass(frozen=True)
class FixtureFinding:
    """One cross-reference problem found by the check.

    Attributes:
        kind: One of ``"missing_key"`` (a scan-target identifier is absent
            from its canonical source), ``"coverage_shortfall"`` (a
            canonical source's distinct identifier count does not match
            its configured ``expected_count``), or ``"load_error"``
            (a configured file could not be found or parsed).
        message: Human-readable, actionable description including the
            offending file path(s) and identifier value(s).
    """

    kind: str
    message: str


def _load_parsed(path: Path) -> Any:
    """Load and parse a fixture file's content as JSON or YAML.

    Dispatches on file extension: ``.yaml``/``.yml`` parses as YAML,
    everything else (including ``.json``) parses as JSON. Both formats
    round-trip through the same in-memory shape (dicts/lists/scalars), so
    the rest of the pipeline is format-agnostic.
    """
    text = path.read_text(encoding="utf-8")
    if path.suffix.lower() in (".yaml", ".yml"):
        return yaml.safe_load(text)
    return json.loads(text)


def _walk_identifier_values(node: Any, field_name: str, out: set[str]) -> None:
    """Recursively walk a parsed fixture structure collecting ``field_name`` values.

    Any dict found at any nesting depth that has ``field_name`` as a key
    contributes the stringified value of that key. Lists and nested dicts
    are descended into so callers do not need to know a fixture's exact
    shape up front (a top-level list of records, a dict keyed by id with
    record values, or a nested envelope like ``{"data": {"items": [...]}}``
    all work the same way).
    """
    if isinstance(node, dict):
        value = node.get(field_name)
        if isinstance(value, (str, int, float)) and not isinstance(value, bool):
            out.add(str(value))
        for child in node.values():
            _walk_identifier_values(child, field_name, out)
    elif isinstance(node, list):
        for item in node:
            _walk_identifier_values(item, field_name, out)


def collect_identifiers(path: Path, field_name: str) -> set[str]:
    """Load *path* (JSON or YAML) and collect every value of *field_name* found in it.

    Args:
        path: Fixture file to load.
        field_name: Key name to collect values for, at any nesting depth.

    Returns:
        The set of distinct stringified identifier values found.

    Raises:
        OSError: If *path* cannot be read.
        ValueError: If *path* is not valid JSON.
        yaml.YAMLError: If *path* is not valid YAML.
    """
    data = _load_parsed(path)
    out: set[str] = set()
    _walk_identifier_values(data, field_name, out)
    return out


def _resolve_canonical_path(
    scan: FixtureScanTarget,
    canonical_sources: tuple[FixtureCanonicalSource, ...],
) -> str | None:
    """Resolve the canonical_sources[].path a scan target checks against.

    Config-load time (``_parse_fixture_consistency_config``) already
    validates and resolves ``canonical_source`` when the config is loaded
    from YAML; this local re-resolution keeps ``check_fixture_consistency``
    usable directly with hand-built ``FixtureConsistencyConfig`` instances
    (as unit tests do) without going through the YAML loader.
    """
    if scan.canonical_source is not None:
        return scan.canonical_source
    if len(canonical_sources) == 1:
        return canonical_sources[0].path
    return None


def check_fixture_consistency(repo_path: Path, config: FixtureConsistencyConfig) -> list[FixtureFinding]:
    """Run the configured fixture-catalog cross-reference check against a repo checkout.

    Args:
        repo_path: Local checkout path of the target repo.
        config: Parsed ``fixture_consistency`` configuration.

    Returns:
        A list of ``FixtureFinding``s. Empty when the check passes --
        including the trivial pass when ``config.canonical_sources`` is
        empty (the workspace has not opted in).
    """
    findings: list[FixtureFinding] = []
    if not config.canonical_sources:
        return findings

    canonical_by_path: dict[str, FixtureCanonicalSource] = {source.path: source for source in config.canonical_sources}
    canonical_values: dict[str, set[str]] = {}

    for source in config.canonical_sources:
        full_path = repo_path / source.path
        if not full_path.is_file():
            findings.append(
                FixtureFinding(
                    "load_error",
                    f"Canonical fixture file not found: '{source.path}' "
                    f"(configured under fixture_consistency.canonical_sources).",
                )
            )
            continue
        try:
            values = collect_identifiers(full_path, source.identifier_field)
        except (OSError, ValueError, yaml.YAMLError) as exc:
            findings.append(
                FixtureFinding(
                    "load_error",
                    f"Failed to parse canonical fixture '{source.path}': {exc}",
                )
            )
            continue

        canonical_values[source.path] = values
        if source.expected_count is not None and len(values) != source.expected_count:
            findings.append(
                FixtureFinding(
                    "coverage_shortfall",
                    f"Canonical fixture '{source.path}' has {len(values)} distinct "
                    f"'{source.identifier_field}' value(s); expected {source.expected_count}. "
                    "A backfill task may have left the canonical dataset incomplete relative "
                    "to its documented expectation.",
                )
            )

    for scan in config.scan:
        canonical_path = _resolve_canonical_path(scan, config.canonical_sources)
        if canonical_path is None or canonical_path not in canonical_by_path:
            findings.append(
                FixtureFinding(
                    "load_error",
                    f"Scan target '{scan.path}' does not resolve to a valid canonical_source "
                    f"(configured canonical_sources: {sorted(canonical_by_path)}). Set "
                    "fixture_consistency.scan[].canonical_source explicitly when more than one "
                    "canonical_sources entry is configured.",
                )
            )
            continue
        if canonical_path not in canonical_values:
            # The canonical source itself failed to load; already reported above.
            continue

        full_path = repo_path / scan.path
        if not full_path.is_file():
            findings.append(
                FixtureFinding(
                    "load_error",
                    f"Scan target fixture file not found: '{scan.path}' "
                    f"(configured under fixture_consistency.scan).",
                )
            )
            continue
        try:
            scan_values = collect_identifiers(full_path, scan.identifier_field)
        except (OSError, ValueError, yaml.YAMLError) as exc:
            findings.append(
                FixtureFinding(
                    "load_error",
                    f"Failed to parse fixture '{scan.path}': {exc}",
                )
            )
            continue

        missing = sorted(scan_values - canonical_values[canonical_path] - scan.allow_missing)
        if missing:
            findings.append(
                FixtureFinding(
                    "missing_key",
                    f"Fixture '{scan.path}' field '{scan.identifier_field}' references "
                    f"{len(missing)} key(s) absent from canonical source '{canonical_path}': "
                    f"{', '.join(missing)}. Fix the fixture to reference a real canonical key, "
                    "or if this is an intentional edge-case fixture (e.g. testing an "
                    "empty/not-found state), add the value(s) to "
                    "fixture_consistency.scan[].allow_missing for this scan target.",
                )
            )

    return findings
