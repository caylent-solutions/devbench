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
   canonical source's value set -- unless the fixture artifact itself
   marks it waived via a structured ``allow_missing`` marker attached to
   the record (the opt-out for fixtures that intentionally model a
   not-found/empty-state edge case; see the spec 4.7 bullet 5 paragraph
   below).
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

Spec 4.7 bullet 5 (PM-5's in-diff exception, caylent-solutions/devbench-internal-backlog#17
E6-F1-S1-T2): the ``allow_missing`` waiver that scopes an intentional not-found/empty-state
edge-case fixture used to live in workspace config
(``gates.fixture_consistency.scan[].allow_missing``) -- invisible to a reviewer who never opens
``devbench.yaml`` for the unit under review, defeating the point of a machine-blocking gate at
exactly the moment a human could challenge the suppression. It now lives IN the scanned fixture
artifact itself, as a structured ``{"allow_missing": {"reason": "<non-empty reason>"}}`` marker
attached directly to the waived record -- visible in the same diff the reviewer is already
looking at. This is a complete replacement, not an addition:
``gates.fixture_consistency.scan[].allow_missing`` is a removed config key (``config_loader.py``
fails config load fast, naming the in-fixture replacement), and every applied waiver is itself
surfaced as a ``waiver_applied`` finding in this module's own report, so the suppression is
visible there too, not only in the fixture's diff. A malformed marker (wrong shape, or a record
missing a non-empty ``reason``) or a well-formed marker that cannot be matched to any record (a
misspelled identifier field, or a marker placed at a fixture's envelope level) raises loudly
rather than silently suppressing.
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
    "BLOCKING_FINDING_KINDS",
    "FixtureConsistencyConfigError",
    "FixtureFinding",
    "check_fixture_consistency",
    "collect_identifiers",
]

# The `FixtureFinding.kind` values that make `cli.cmd_check_fixture_consistency`'s
# gate status "fail" (spec 4.7; `cmd_check_fixture_consistency`'s own docstring
# repeats this list, so keep both in sync). `"waiver_applied"` (spec 4.7 bullet
# 5, E6-F1-S1-T2) is deliberately excluded: it documents that a mismatch was
# suppressed by a validated in-fixture marker, not that one was found -- a
# fixture whose only findings are `waiver_applied` waivers has nothing left
# to fail the gate on (AC-E6-F1-S1-T2-1). This is the single source of truth
# for the blocking/informational split; `cli.py` imports this constant rather
# than re-declaring the three kind literals at its own comparison site, so the
# two modules can never drift on which kinds block the gate.
BLOCKING_FINDING_KINDS: frozenset[str] = frozenset({"missing_key", "coverage_shortfall", "load_error"})

# Dotted YAML key prefix for every operator-facing config-key reference in
# this module's finding messages. Centralised so the spec 4.1 gates
# migration (top-level `fixture_consistency:` -> `gates.fixture_consistency:`)
# cannot drift key-by-key across the four finding sites below.
_CONFIG_KEY_PREFIX = "gates.fixture_consistency"

# Explicit extension -> parser dispatch (spec 4.7 bullet 3). Shared by both
# the canonical-source reader and the scan-target reader (both funnel
# through `_load_parsed` via `_collect_identifiers_and_waivers`), so
# recognized formats can never drift between the two, and an unconfigured
# extension is never handed to a parser at all -- closing the old
# implicit-JSON-fallback defect where a `.txt`/`.csv` target whose content
# happened to be valid JSON was silently accepted.
_EXTENSION_PARSERS: dict[str, Callable[[str], Any]] = {
    ".json": json.loads,
    ".yaml": yaml.safe_load,
    ".yml": yaml.safe_load,
}

# The reserved in-fixture waiver marker (spec 4.7 bullet 5, E6-F1-S1-T2): a
# record carrying this key alongside its identifier-field value is waived
# from a `missing_key` finding. `_WAIVER_REASON_KEY` is the marker's own
# sole permitted key -- the marker's shape is exactly
# `{"allow_missing": {"reason": "<non-empty reason>"}}`, never a bare
# string/list/other shape (a malformed marker raises rather than silently
# suppressing; see `_validate_waiver_marker`).
_WAIVER_MARKER_KEY: str = "allow_missing"
_WAIVER_REASON_KEY: str = "reason"

# Named message templates (spec 4.7; Code Standards Critical Rule 4 -- no
# inline literal message strings in this module). Every ``FixtureFinding``
# and every raised loud error is built from exactly one of these, so a
# message's wording is defined once regardless of how many call sites (or
# parametrized tests) reference it.
#
# The first five are the loud-error summaries: `_raise_loud_error` is the
# single site that prefixes each with the spec Section 7 `ERROR: <summary>`
# shape, so the empty-scan-list finding 322-D05, the zero-match-identifier
# field finding 322-D02/D03, the malformed-waiver-marker raise site, and
# the unmatchable-waiver-marker raise site (the latter two both spec 4.7
# bullet 5) can never independently drift the prefix.
_MSG_EMPTY_SCAN_LIST: str = "gate enabled but scan list is empty"
_MSG_IDENTIFIER_FIELD_ZERO_MATCH: str = "identifier field '{field}' matched zero records in {path}"
_MSG_UNSUPPORTED_EXTENSION: str = (
    "Unsupported fixture file extension '{ext}' for '{path}'; expected one of: {allowed} "
    "(configured under {prefix}.canonical_sources or {prefix}.scan)."
)
_MSG_MALFORMED_WAIVER_MARKER: str = (
    "Fixture '{path}' has a malformed in-fixture allow_missing marker for key '{key}': {detail}"
)
_MSG_UNMATCHED_WAIVER_MARKER: str = (
    "Fixture '{path}' has an allow_missing marker that cannot be matched to any record: "
    "identifier field '{field}' has no value in this record (keys present: {keys}). A waiver "
    "must be attached to the same record whose '{field}' value it protects."
)

# Sub-templates plugged into `_MSG_MALFORMED_WAIVER_MARKER`'s `{detail}`/`{key}` slots and
# `_MSG_UNMATCHED_WAIVER_MARKER`'s callers, so those two top-level templates never carry an
# inline literal string either.
_MSG_MARKER_WRONG_SHAPE_DETAIL: str = (
    "expected a mapping of exactly {{'{reason_key}': '<non-empty reason>'}}, got {marker!r}."
)
_MSG_MARKER_REASON_INVALID_DETAIL: str = "'{reason_key}' must be a non-empty string, got {reason!r}."
_MSG_NO_IDENTIFIER_VALUE_LOCATOR: str = "<no '{field}' value on this record; keys present: {keys}>"

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
    "an intentional edge-case fixture (e.g. testing an empty/not-found state), add a structured "
    '{{"allow_missing": {{"reason": "<non-empty reason>"}}}} marker directly to the record in '
    "'{path}' (spec 4.7 bullet 5 -- the waiver lives in the fixture artifact itself, not in "
    "workspace config)."
)
_MSG_WAIVER_APPLIED: str = (
    "Fixture '{path}' waives missing key '{key}' via its in-fixture allow_missing marker (reason: {reason})."
)


class FixtureConsistencyConfigError(ValueError):
    """A ``gates.fixture_consistency`` configuration cannot produce a meaningful check.

    Raised only by :func:`_raise_loud_error`, for four spec 4.7 loud-error
    paths: 322-D02/D03 zero-match ``identifier_field``, 322-D05 empty
    resolved ``scan`` list while the gate is enabled, (spec 4.7 bullet 5,
    E6-F1-S1-T2) a malformed in-fixture ``allow_missing`` waiver marker
    (wrong shape, or a record missing a non-empty ``reason``), and (spec
    4.7 bullet 5, E6-F1-S1-T2, code_review round 1) an ``allow_missing``
    marker attached to a dict whose configured ``identifier_field`` has no
    value in that same dict -- a misspelled/absent identifier field, or a
    marker placed at a fixture's envelope level rather than on an
    individual record -- which can never be matched to a record and so
    must never be silently ignored. The first two are raised OUTSIDE
    ``_check_canonical_sources``/``_check_scan_targets``'s own parse
    try/except blocks; the remaining two are raised FROM INSIDE the parse
    call those blocks wrap (a malformed or unmatched marker is discovered
    while walking a fixture's already-parsed content), so both functions
    add an explicit
    ``except FixtureConsistencyConfigError: raise`` ahead of their generic
    ``(OSError, ValueError, yaml.YAMLError)`` catch -- a malformed or
    unmatched marker must never be silently downgraded into a
    ``load_error`` finding the way a genuine parse failure is. A plain
    ``ValueError`` also carries two
    unrelated meanings inside this module -- the unsupported-extension
    raise from ``_load_parsed`` (converted into a ``load_error`` finding by
    ``_check_canonical_sources`` and ``_check_scan_targets``, so it never
    reaches ``cmd_check_fixture_consistency``, though it still propagates
    uncaught out of the public ``collect_identifiers`` when that function
    is called directly) and ``json.JSONDecodeError`` (also caught and
    converted internally by those same two callers) -- so callers
    (``cli.cmd_check_fixture_consistency``) catch this specific subclass
    rather than the bare builtin to avoid mis-handling an unrelated
    ``ValueError`` as a config error.
    """


def _raise_loud_error(summary: str) -> NoReturn:
    """Raise the shared ``ERROR: <summary>`` shape for every fixture-consistency loud-error path.

    Single site that prefixes a config-error summary with the spec
    Section 7 ``ERROR: <summary>`` shape, so the empty-scan-list (322-D05),
    zero-match-identifier-field (322-D02/D03), malformed-waiver-marker and
    unmatchable-waiver-marker (both spec 4.7 bullet 5) raise sites cannot
    drift into independently formatted strings. The exception itself is a
    :class:`FixtureConsistencyConfigError` -- a misconfigured gate (or a
    malformed or unmatchable in-fixture waiver marker) is a value/config
    problem, not a generic failure.
    """
    raise FixtureConsistencyConfigError(f"ERROR: {summary}")


class _UnsupportedFixtureExtensionError(ValueError):
    """A fixture file's extension is not a key of ``_EXTENSION_PARSERS``.

    Raised only by ``_load_parsed``, whose sole caller is
    :func:`_collect_identifiers_and_waivers` (which has no try/except of its
    own and does not catch this, so it propagates through unchanged).
    ``_check_canonical_sources``/``_check_scan_targets`` -- the two internal
    callers of ``_collect_identifiers_and_waivers`` -- catch it ahead of
    their generic ``(OSError, ValueError, yaml.YAMLError)`` parse-failure
    guard, so the resulting ``load_error`` finding uses the unsupported-
    extension message verbatim instead of being re-wrapped in the "Failed to
    parse" phrasing that guard applies to genuine parse failures -- no parser
    was ever invoked for this path, so the finding must not claim one was.
    The public :func:`collect_identifiers`, exported in ``__all__``, is
    ``_collect_identifiers_and_waivers``'s third caller and has no
    try/except of its own, so a caller that invokes ``collect_identifiers``
    directly (rather than through those two internal callers) sees this
    exception propagate uncaught, as the ``ValueError`` subclass documented
    in ``collect_identifiers``'s own ``Raises`` section.
    """


@dataclass(frozen=True)
class FixtureFinding:
    """One cross-reference problem found by the check.

    Attributes:
        kind: One of ``"missing_key"`` (a scan-target identifier is absent
            from its canonical source), ``"coverage_shortfall"`` (a
            canonical source's distinct identifier count does not match
            its configured ``expected_count``), ``"load_error"``
            (a configured file could not be found or parsed), or
            ``"waiver_applied"`` (spec 4.7 bullet 5, E6-F1-S1-T2: a
            record's in-fixture ``allow_missing`` marker suppressed what
            would otherwise have been a ``missing_key`` finding for that
            identifier value -- surfaced here so the suppression is
            visible in the check's own report, not only in the fixture's
            diff).
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


def _validate_waiver_marker(marker: Any, source_path: str, offending_key: str) -> str:
    """Validate one record's structured in-fixture ``allow_missing`` marker and return its reason.

    The documented shape is exactly ``{"reason": "<non-empty reason>"}`` -- a single
    ``_WAIVER_REASON_KEY`` key mapping to a non-empty (post-strip) string. Anything else is a
    malformed waiver (spec 4.7 bullet 5, AC-E6-F1-S1-T2-3): a fixture author who intends to waive
    a record must say why, and a waiver that cannot be understood must never silently suppress a
    finding the way a well-formed one does.

    Raises:
        FixtureConsistencyConfigError: If *marker* is not a ``{"reason": "<non-empty string>"}``
            mapping, naming *source_path* and *offending_key* (the record's own identifier
            value).
    """
    if not isinstance(marker, dict) or set(marker) != {_WAIVER_REASON_KEY}:
        _raise_loud_error(
            _MSG_MALFORMED_WAIVER_MARKER.format(
                path=source_path,
                key=offending_key,
                detail=_MSG_MARKER_WRONG_SHAPE_DETAIL.format(reason_key=_WAIVER_REASON_KEY, marker=marker),
            )
        )
    reason = marker[_WAIVER_REASON_KEY]
    if not isinstance(reason, str) or not reason.strip():
        _raise_loud_error(
            _MSG_MALFORMED_WAIVER_MARKER.format(
                path=source_path,
                key=offending_key,
                detail=_MSG_MARKER_REASON_INVALID_DETAIL.format(reason_key=_WAIVER_REASON_KEY, reason=reason),
            )
        )
    return reason


def _walk_identifiers_and_waivers(
    node: Any,
    field_name: str,
    source_path: str,
    identifiers: set[str],
    waivers: dict[str, str],
) -> None:
    """Recursively walk a parsed fixture structure collecting ``field_name`` values and waivers.

    Any dict found at any nesting depth that has ``field_name`` as a key contributes the
    stringified value of that key to *identifiers* (unchanged from the pre-T2
    ``_walk_identifier_values`` behaviour this function replaces). ANY dict found at any nesting
    depth that carries the reserved ``allow_missing`` marker (spec 4.7 bullet 5) has that marker
    validated via :func:`_validate_waiver_marker` UNCONDITIONALLY -- whether or not the SAME dict
    also resolves ``field_name`` -- because a marker's shape must never depend on whether its
    host happens to be a genuine, identifiable record (code_review round 1, E6-F1-S1-T2): a
    dict carrying a misspelled identifier field, or an envelope-level dict that merely wraps the
    real records, must reject a malformed marker exactly as loudly as a well-formed record does.
    When the marker is well-formed but *field_name* resolves no value on the same dict, the
    marker can never be matched to any record at all -- that is ALSO a loud error (see
    ``_MSG_UNMATCHED_WAIVER_MARKER``), never a silent no-op, because a waiver nobody can ever
    apply is dead configuration a fixture author believes is protecting a record it is not
    attached to. Only once both checks pass is the reason collected into *waivers*, keyed by the
    record's own stringified identifier value -- the visible, in-diff replacement for the retired
    ``gates.fixture_consistency.scan[].allow_missing`` config allowlist. Lists and nested dicts
    are descended into so callers do not need to know a fixture's exact shape up front (a
    top-level list of records, a dict keyed by id with record values, or a nested envelope like
    ``{"data": {"items": [...]}}`` all work the same way).

    Raises:
        FixtureConsistencyConfigError: If a dict's ``allow_missing`` marker is malformed, or if
            a well-formed marker is attached to a dict whose *field_name* resolves no value (an
            unmatchable waiver).
    """
    if isinstance(node, dict):
        value = node.get(field_name)
        has_identifier = isinstance(value, (str, int, float)) and not isinstance(value, bool)
        if _WAIVER_MARKER_KEY in node:
            locator = (
                str(value)
                if has_identifier
                else _MSG_NO_IDENTIFIER_VALUE_LOCATOR.format(field=field_name, keys=sorted(node.keys()))
            )
            # Validate the marker's own shape unconditionally, regardless of whether this dict
            # also resolves an identifier -- a malformed marker on a misspelled-field record or
            # an envelope-level dict is exactly as loud a defect as one on a genuine record.
            reason = _validate_waiver_marker(node[_WAIVER_MARKER_KEY], source_path, locator)
            if not has_identifier:
                _raise_loud_error(
                    _MSG_UNMATCHED_WAIVER_MARKER.format(
                        path=source_path,
                        field=field_name,
                        keys=sorted(node.keys()),
                    )
                )
            waivers[locator] = reason
        if has_identifier:
            identifiers.add(str(value))
        for child in node.values():
            _walk_identifiers_and_waivers(child, field_name, source_path, identifiers, waivers)
    elif isinstance(node, list):
        for item in node:
            _walk_identifiers_and_waivers(item, field_name, source_path, identifiers, waivers)


def _collect_identifiers_and_waivers(path: Path, field_name: str) -> tuple[set[str], dict[str, str]]:
    """Load *path* and collect both ``field_name`` identifier values and in-fixture waivers.

    Single parse-and-walk helper shared by both :func:`_check_canonical_sources` and
    :func:`_check_scan_targets` (spec 4.7 bullet 5 REFACTOR: one helper, not a second parse path)
    -- reused through the SAME extension dispatch table :func:`_load_parsed` already uses, so a
    fixture is never parsed twice and the waiver-marker parsing can never drift from the
    identifier-collection parsing. Canonical-source callers discard the waivers dict; only scan
    targets apply it.

    Raises:
        OSError: If *path* cannot be read.
        ValueError: If *path* is not valid JSON, or if *path*'s extension is not one of
            ``.json``/``.yaml``/``.yml`` (a ``_UnsupportedFixtureExtensionError`` for an
            unrecognized extension propagates as this same ``ValueError`` subclass before any
            parser is ever invoked).
        yaml.YAMLError: If *path* is not valid YAML.
        FixtureConsistencyConfigError: If any record's ``allow_missing`` marker is malformed, or
            if a well-formed marker cannot be matched to any record (spec 4.7 bullet 5) -- this
            function is the direct caller of the walk (:func:`_walk_identifiers_and_waivers`)
            that raises both.
    """
    data = _load_parsed(path)
    identifiers: set[str] = set()
    waivers: dict[str, str] = {}
    _walk_identifiers_and_waivers(data, field_name, str(path), identifiers, waivers)
    return identifiers, waivers


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
            not one of ``.json``/``.yaml``/``.yml`` (this function delegates
            to :func:`_collect_identifiers_and_waivers`, which is
            ``_load_parsed``'s sole caller, so a
            ``_UnsupportedFixtureExtensionError`` for an unrecognized
            extension propagates as this same ``ValueError`` subclass before
            any parser is ever invoked).
        yaml.YAMLError: If *path* is not valid YAML.
        FixtureConsistencyConfigError: If any record's in-fixture
            ``allow_missing`` marker is malformed, or if a well-formed
            marker cannot be matched to any record (spec 4.7 bullet 5) --
            this public function still validates a fixture's waiver
            markers even though it discards them, since a malformed or
            unmatchable marker is a defect in *path* regardless of which
            caller asked only for identifiers.
    """
    identifiers, _waivers = _collect_identifiers_and_waivers(path, field_name)
    return identifiers


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
            target; or (spec 4.7 bullet 5) if a canonical source's own
            content carries a malformed or unmatchable in-fixture
            ``allow_missing`` marker -- the shared parse-and-walk helper
            validates every marker it encounters, canonical sources
            included.
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
            values, _waivers = _collect_identifiers_and_waivers(full_path, source.identifier_field)
        except _UnsupportedFixtureExtensionError as exc:
            # No parser was ever invoked for this path -- report the
            # dispatch-table rejection verbatim rather than through the
            # "Failed to parse" wrapper below, which would falsely imply a
            # parse attempt happened.
            findings.append(FixtureFinding("load_error", str(exc)))
            continue
        except FixtureConsistencyConfigError:
            # A malformed or unmatchable in-fixture allow_missing marker
            # (spec 4.7 bullet 5) is a loud, blocking error -- never
            # silently downgraded into a load_error finding the way a
            # genuine parse failure below is.
            raise
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

    A scan-target identifier value that would otherwise mismatch is suppressed only when the
    fixture's own content carries a validated in-fixture ``allow_missing`` marker for that value
    (spec 4.7 bullet 5, E6-F1-S1-T2 -- the production waiver mechanism; every such suppression is
    itself surfaced as a ``waiver_applied`` finding, spec AC-19/PM-5 visibility). There is no
    config-allowlist read path; ``gates.fixture_consistency.scan[].allow_missing`` is a removed
    config key rejected long before this function runs (see
    ``_reject_removed_fixture_allow_missing_key`` in ``config_loader.py``).

    Raises:
        FixtureConsistencyConfigError: If a scan target's own content carries a malformed or
            unmatchable in-fixture ``allow_missing`` marker (spec 4.7 bullet 5) -- the shared
            parse-and-walk helper validates every marker it encounters, scan targets included.
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
            scan_values, scan_waivers = _collect_identifiers_and_waivers(full_path, scan.identifier_field)
        except _UnsupportedFixtureExtensionError as exc:
            # No parser was ever invoked for this path -- report the
            # dispatch-table rejection verbatim rather than through the
            # "Failed to parse" wrapper below, which would falsely imply a
            # parse attempt happened.
            findings.append(FixtureFinding("load_error", str(exc)))
            continue
        except FixtureConsistencyConfigError:
            # A malformed or unmatchable in-fixture allow_missing marker
            # (spec 4.7 bullet 5) is a loud, blocking error -- never
            # silently downgraded into a load_error finding the way a
            # genuine parse failure below is.
            raise
        except (OSError, ValueError, yaml.YAMLError) as exc:
            findings.append(
                FixtureFinding(
                    "load_error",
                    _MSG_SCAN_PARSE_FAILED.format(path=scan.path, exc=exc),
                )
            )
            continue

        would_be_missing = scan_values - canonical_values[canonical_path]
        applied_waivers = {value: reason for value, reason in scan_waivers.items() if value in would_be_missing}
        missing = sorted(would_be_missing - applied_waivers.keys())

        for value in sorted(applied_waivers):
            findings.append(
                FixtureFinding(
                    "waiver_applied",
                    _MSG_WAIVER_APPLIED.format(path=scan.path, key=value, reason=applied_waivers[value]),
                )
            )

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
            resolved identifier set is empty for any reason (322-D02/D03);
            or (via :func:`_check_canonical_sources` or
            :func:`_check_scan_targets`, spec 4.7 bullet 5) if any
            fixture's content carries a malformed or unmatchable in-fixture
            ``allow_missing`` waiver marker.
    """
    if not config.canonical_sources:
        return []

    if not config.scan:
        _raise_loud_error(_MSG_EMPTY_SCAN_LIST)

    canonical_findings, canonical_values = _check_canonical_sources(repo_path, config.canonical_sources)
    scan_findings = _check_scan_targets(repo_path, config.scan, config.canonical_sources, canonical_values)
    return canonical_findings + scan_findings
