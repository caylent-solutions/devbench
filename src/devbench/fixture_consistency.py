"""Fixture-catalog cross-reference check (caylent-solutions/devbench-internal-backlog#17).

A feature's data-fetch/lookup logic is frequently correct, but reads from a
mock/fixture lookup table whose keys (SKUs, ids, reference numbers, etc.)
were fabricated, keyed in the wrong namespace, or left incomplete relative
to the project's canonical shared fixture/demo dataset -- so the feature is
functionally dead or crashes for real records even though the underlying
logic is sound. This survives the unit suite because each task's own tests
construct a self-consistent fixture inline rather than exercising the
shared canonical dataset.

This module implements the opt-in check driven by the ``gates.fixture_consistency``
block of ``backlog/config/devbench.yaml`` (spec 4.1 gates migration; see
``config-schema.json`` and ``config_loader.FixtureConsistencyConfig``):

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

Hardening over the E1 cherry-pick of PR #322 (spec 4.7, register findings
322-D02/D03/D05, issue #17): a *configured* (non-empty ``canonical_sources``)
gate can no longer degrade into a silent pass or a mass false positive. At
the pre-hardening HEAD, a typo'd ``identifier_field`` and a canonical
source that is genuinely empty were indistinguishable -- both reduced to
an empty resolved identifier set -- and BOTH mass-false-positived every
scanned reference as a ``missing_key`` finding (an exit-1 failure, never a
silent pass). Only a *different* degenerate shape, an enabled gate with a
resolved ``scan`` list of zero targets, silently passed with zero findings
(exit 0) despite having inspected nothing. This module now raises loudly,
before any file is read, for the empty-``scan``-list shape (322-D05); and
raises loudly for the zero-match ``identifier_field`` shape (322-D02/D03,
now unified into one loud path instead of a mass false positive) as soon as
a canonical source's resolved identifier set comes back empty, for any
reason. Scan/canonical file parsing dispatches on an explicit
``.json``/``.yaml``/``.yml`` extension table -- any other configured
extension is a ``load_error`` finding naming the file; no extension ever
falls back to an implicit JSON parse attempt.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any, NoReturn

import yaml

if TYPE_CHECKING:
    from devbench.config_loader import (
        FixtureCanonicalSource,
        FixtureConsistencyConfig,
        FixtureScanTarget,
    )

__all__ = [
    "FixtureConsistencyConfigError",
    "FixtureFinding",
    "check_fixture_consistency",
    "collect_identifiers",
]

# Dotted YAML key prefix for every operator-facing config-key reference in
# this module's finding messages. Centralised so the spec 4.1 gates
# migration (top-level `fixture_consistency:` -> `gates.fixture_consistency:`)
# cannot drift key-by-key across the four finding sites below.
_CONFIG_KEY_PREFIX = "gates.fixture_consistency"

# Explicit extension -> parser dispatch (spec 4.7 bullet 3). Shared by both
# the canonical-source reader and the scan-target reader (both funnel
# through `_load_parsed`/`collect_identifiers`), so recognized formats can
# never drift between the two, and an unconfigured extension is never
# handed to a parser at all -- closing the old implicit-JSON-fallback
# defect where a `.txt`/`.csv` target whose content happened to be valid
# JSON was silently accepted.
_EXTENSION_PARSERS: dict[str, Callable[[str], Any]] = {
    ".json": json.loads,
    ".yaml": yaml.safe_load,
    ".yml": yaml.safe_load,
}

# Named message templates (spec 4.7; Code Standards Critical Rule 4 -- no
# inline literal message strings in this module). Every ``FixtureFinding``
# and every raised loud error is built from exactly one of these, so a
# message's wording is defined once regardless of how many call sites (or
# parametrized tests) reference it.
#
# The first three are the loud-error summaries: `_raise_loud_error` is the
# single site that prefixes each with the spec Section 7 `ERROR: <summary>`
# shape, so the empty-scan-list (322-D05) and zero-match-identifier-field
# (322-D02/D03) raise sites can never independently drift the prefix.
_MSG_EMPTY_SCAN_LIST: str = "gate enabled but scan list is empty"
_MSG_IDENTIFIER_FIELD_ZERO_MATCH: str = "identifier field '{field}' matched zero records in {path}"
_MSG_UNSUPPORTED_EXTENSION: str = (
    "Unsupported fixture file extension '{ext}' for '{path}'; expected one of: {allowed} "
    "(configured under {prefix}.canonical_sources or {prefix}.scan)."
)

# The remaining templates back a `FixtureFinding` (never raised) --
# `check_fixture_consistency`'s two loops format each at their one call
# site.
_MSG_CANONICAL_FILE_NOT_FOUND: str = (
    "Canonical fixture file not found: '{path}' (configured under {prefix}.canonical_sources)."
)
_MSG_CANONICAL_PARSE_FAILED: str = "Failed to parse canonical fixture '{path}': {exc}"
_MSG_COVERAGE_SHORTFALL: str = (
    "Canonical fixture '{path}' has {count} distinct '{field}' value(s); expected {expected}. "
    "A backfill task may have left the canonical dataset incomplete relative to its documented "
    "expectation."
)
_MSG_SCAN_TARGET_AMBIGUOUS: str = (
    "Scan target '{path}' does not resolve to a valid canonical_source (configured "
    "canonical_sources: {available}). Set {prefix}.scan[].canonical_source explicitly when more "
    "than one canonical_sources entry is configured."
)
_MSG_SCAN_FILE_NOT_FOUND: str = "Scan target fixture file not found: '{path}' (configured under {prefix}.scan)."
_MSG_SCAN_PARSE_FAILED: str = "Failed to parse fixture '{path}': {exc}"
_MSG_MISSING_KEY: str = (
    "Fixture '{path}' field '{field}' references {count} key(s) absent from canonical source "
    "'{canonical_path}': {keys}. Fix the fixture to reference a real canonical key, or if this is "
    "an intentional edge-case fixture (e.g. testing an empty/not-found state), add the value(s) to "
    "{prefix}.scan[].allow_missing for this scan target."
)


class FixtureConsistencyConfigError(ValueError):
    """A ``gates.fixture_consistency`` configuration cannot produce a meaningful check.

    Raised only by :func:`_raise_loud_error` for the two spec 4.7 loud-error
    paths (322-D02/D03 zero-match ``identifier_field``, 322-D05 empty
    resolved ``scan`` list while the gate is enabled). A plain ``ValueError``
    also carries two unrelated meanings inside this module -- the
    unsupported-extension raise from ``_load_parsed`` (converted into a
    ``load_error`` finding by ``_check_canonical_sources`` and
    ``_check_scan_targets``, so it never reaches ``cmd_check_fixture_
    consistency``, though it still propagates uncaught out of the public
    ``collect_identifiers`` when that function is called directly) and
    ``json.JSONDecodeError`` (also caught and converted internally by those
    same two callers) -- so callers (``cli.cmd_check_fixture_consistency``)
    catch this specific subclass rather than the bare builtin to avoid
    mis-handling an unrelated ``ValueError`` as a config error.
    """


def _raise_loud_error(summary: str) -> NoReturn:
    """Raise the shared ``ERROR: <summary>`` shape for every fixture-consistency loud-error path.

    Single site that prefixes a config-error summary with the spec
    Section 7 ``ERROR: <summary>`` shape, so the empty-scan-list (322-D05)
    and zero-match-identifier-field (322-D02/D03) raise sites cannot drift
    into two independently-formatted strings. The exception itself is a
    :class:`FixtureConsistencyConfigError` -- a misconfigured gate is a
    value/config problem, not a generic failure -- and is deliberately never
    caught by this module's own ``_check_canonical_sources``/
    ``_check_scan_targets`` try/except blocks (those wrap only their
    ``collect_identifiers`` parse calls, not this function), so it always
    propagates to the caller.
    """
    raise FixtureConsistencyConfigError(f"ERROR: {summary}")


class _UnsupportedFixtureExtensionError(ValueError):
    """A fixture file's extension is not a key of ``_EXTENSION_PARSERS``.

    Raised only by ``_load_parsed``, whose sole caller is the public
    :func:`collect_identifiers` (which has no try/except of its own and does
    not catch this). ``_check_canonical_sources``/``_check_scan_targets`` --
    the two internal callers of ``collect_identifiers`` -- catch it ahead of
    their generic ``(OSError, ValueError, yaml.YAMLError)`` parse-failure
    guard, so the resulting ``load_error`` finding uses the unsupported-
    extension message verbatim instead of being re-wrapped in the "Failed to
    parse" phrasing that guard applies to genuine parse failures -- no parser
    was ever invoked for this path, so the finding must not claim one was.
    ``collect_identifiers`` is public and exported in ``__all__``, so a
    caller that invokes it directly (rather than through those two internal
    callers) sees this exception propagate uncaught, as the ``ValueError``
    subclass documented in ``collect_identifiers``'s own ``Raises`` section.
    """


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
    """Load and parse a fixture file's content via the explicit extension dispatch table.

    Dispatches on ``path.suffix.lower()`` against ``_EXTENSION_PARSERS``:
    ``.json`` parses as JSON, ``.yaml``/``.yml`` parse as YAML. Both formats
    round-trip through the same in-memory shape (dicts/lists/scalars), so
    the rest of the pipeline is format-agnostic. Any other extension (or no
    extension) raises :class:`_UnsupportedFixtureExtensionError` *before*
    any parser is invoked -- no implicit JSON-parse attempt is ever made on
    an unrecognized extension, even when its content happens to be valid
    JSON (spec 4.7 bullet 3).

    Raises:
        _UnsupportedFixtureExtensionError: If *path*'s extension is not a
            key of ``_EXTENSION_PARSERS``.
    """
    suffix = path.suffix.lower()
    parser = _EXTENSION_PARSERS.get(suffix)
    if parser is None:
        raise _UnsupportedFixtureExtensionError(
            _MSG_UNSUPPORTED_EXTENSION.format(
                ext=suffix or "(none)",
                path=path,
                allowed=", ".join(sorted(_EXTENSION_PARSERS)),
                prefix=_CONFIG_KEY_PREFIX,
            )
        )
    text = path.read_text(encoding="utf-8")
    return parser(text)


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
        ValueError: If *path* is not valid JSON, or if *path*'s extension is
            not one of ``.json``/``.yaml``/``.yml`` (the sole caller of
            ``_load_parsed``, so a ``_UnsupportedFixtureExtensionError`` for
            an unrecognized extension propagates as this same ``ValueError``
            subclass before any parser is ever invoked).
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


def _check_canonical_sources(
    repo_path: Path, canonical_sources: tuple[FixtureCanonicalSource, ...]
) -> tuple[list[FixtureFinding], dict[str, set[str]]]:
    """Load every configured canonical source, flag coverage shortfalls, and collect its identifier set.

    Split out of :func:`check_fixture_consistency` purely to keep that
    function's branch count within the project's complexity lint budget
    (SRP: this loop owns canonical-source loading, the sibling
    :func:`_check_scan_targets` owns scan-target cross-referencing).

    Raises:
        FixtureConsistencyConfigError: If a canonical source's resolved
            identifier set is empty for any reason -- a typo'd
            ``identifier_field`` (322-D02) or records that genuinely never
            carry that field (322-D03) both take this same loud path rather
            than silently passing or mass-false-positiving every scan
            target.
    """
    findings: list[FixtureFinding] = []
    canonical_values: dict[str, set[str]] = {}

    for source in canonical_sources:
        full_path = repo_path / source.path
        if not full_path.is_file():
            findings.append(
                FixtureFinding(
                    "load_error",
                    _MSG_CANONICAL_FILE_NOT_FOUND.format(path=source.path, prefix=_CONFIG_KEY_PREFIX),
                )
            )
            continue
        try:
            values = collect_identifiers(full_path, source.identifier_field)
        except _UnsupportedFixtureExtensionError as exc:
            # No parser was ever invoked for this path -- report the
            # dispatch-table rejection verbatim rather than through the
            # "Failed to parse" wrapper below, which would falsely imply a
            # parse attempt happened.
            findings.append(FixtureFinding("load_error", str(exc)))
            continue
        except (OSError, ValueError, yaml.YAMLError) as exc:
            findings.append(
                FixtureFinding(
                    "load_error",
                    _MSG_CANONICAL_PARSE_FAILED.format(path=source.path, exc=exc),
                )
            )
            continue

        if not values:
            _raise_loud_error(_MSG_IDENTIFIER_FIELD_ZERO_MATCH.format(field=source.identifier_field, path=source.path))

        canonical_values[source.path] = values
        if source.expected_count is not None and len(values) != source.expected_count:
            findings.append(
                FixtureFinding(
                    "coverage_shortfall",
                    _MSG_COVERAGE_SHORTFALL.format(
                        path=source.path,
                        count=len(values),
                        field=source.identifier_field,
                        expected=source.expected_count,
                    ),
                )
            )

    return findings, canonical_values


def _check_scan_targets(
    repo_path: Path,
    scan_targets: tuple[FixtureScanTarget, ...],
    canonical_sources: tuple[FixtureCanonicalSource, ...],
    canonical_values: dict[str, set[str]],
) -> list[FixtureFinding]:
    """Cross-reference every configured scan target against its resolved canonical identifier set.

    Split out of :func:`check_fixture_consistency` for the same
    branch-count/SRP reason as :func:`_check_canonical_sources`.
    """
    findings: list[FixtureFinding] = []
    canonical_by_path: dict[str, FixtureCanonicalSource] = {source.path: source for source in canonical_sources}

    for scan in scan_targets:
        canonical_path = _resolve_canonical_path(scan, canonical_sources)
        if canonical_path is None or canonical_path not in canonical_by_path:
            findings.append(
                FixtureFinding(
                    "load_error",
                    _MSG_SCAN_TARGET_AMBIGUOUS.format(
                        path=scan.path,
                        available=sorted(canonical_by_path),
                        prefix=_CONFIG_KEY_PREFIX,
                    ),
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
                    _MSG_SCAN_FILE_NOT_FOUND.format(path=scan.path, prefix=_CONFIG_KEY_PREFIX),
                )
            )
            continue
        try:
            scan_values = collect_identifiers(full_path, scan.identifier_field)
        except _UnsupportedFixtureExtensionError as exc:
            # No parser was ever invoked for this path -- report the
            # dispatch-table rejection verbatim rather than through the
            # "Failed to parse" wrapper below, which would falsely imply a
            # parse attempt happened.
            findings.append(FixtureFinding("load_error", str(exc)))
            continue
        except (OSError, ValueError, yaml.YAMLError) as exc:
            findings.append(
                FixtureFinding(
                    "load_error",
                    _MSG_SCAN_PARSE_FAILED.format(path=scan.path, exc=exc),
                )
            )
            continue

        missing = sorted(scan_values - canonical_values[canonical_path] - scan.allow_missing)
        if missing:
            findings.append(
                FixtureFinding(
                    "missing_key",
                    _MSG_MISSING_KEY.format(
                        path=scan.path,
                        field=scan.identifier_field,
                        count=len(missing),
                        canonical_path=canonical_path,
                        keys=", ".join(missing),
                        prefix=_CONFIG_KEY_PREFIX,
                    ),
                )
            )

    return findings


def check_fixture_consistency(repo_path: Path, config: FixtureConsistencyConfig) -> list[FixtureFinding]:
    """Run the configured fixture-catalog cross-reference check against a repo checkout.

    Args:
        repo_path: Local checkout path of the target repo.
        config: Parsed ``fixture_consistency`` configuration.

    Returns:
        A list of ``FixtureFinding``s. Empty when the check passes --
        including the trivial pass when ``config.canonical_sources`` is
        empty (the workspace has not opted in).

    Raises:
        FixtureConsistencyConfigError: If ``config.canonical_sources`` is
            non-empty (the gate is opted in) but ``config.scan`` is empty
            (322-D05) -- checked before any file is read; or (via
            :func:`_check_canonical_sources`) if a canonical source's
            resolved identifier set is empty for any reason (322-D02/D03).
    """
    if not config.canonical_sources:
        return []

    if not config.scan:
        _raise_loud_error(_MSG_EMPTY_SCAN_LIST)

    canonical_findings, canonical_values = _check_canonical_sources(repo_path, config.canonical_sources)
    scan_findings = _check_scan_targets(repo_path, config.scan, config.canonical_sources, canonical_values)
    return canonical_findings + scan_findings
