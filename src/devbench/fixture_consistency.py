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

Spec 4.7 bullet 4 (source-literal extraction, caylent-solutions/devbench-internal-backlog#17,
E6-F2-S1-T1): ``gates.fixture_consistency.extract_source_literals`` (default ``False``, read
directly off the parsed :class:`~devbench.config_loader.FixtureConsistencyConfig` this module
already consumes -- see ``resolve_gate_config`` for how the resolved value reaches a caller) adds
a fifth loud-error path and a second source of ``missing_key``/``load_error`` findings alongside
the scan-target cross-reference above. When enabled, this module additionally enumerates the
classified source files under the repo checkout via
:func:`devbench.source_classification.iter_classified_source_files` (PM-3: the single owner of
extension classification and the walk entry point -- this module declares no extension tuple of
its own; the walk also prunes a fixed set of dependency/build/vendor directories, so this is a
scanning boundary, not literally every file in the checkout) and, per DISTINCT configured
``identifier_field`` name, scans each file's text line-by-line for an assignment whose key
matches that field (the *identifier_field grammar*: ``<field>``, optionally quoted, followed by
``:`` or ``=``, followed by a single/double-quoted string literal or a bare integer/float literal
-- a deliberately narrow, per-line regex grammar, not a real parser for any of the languages it
scans). A matched literal is resolved against the UNION of every canonical source sharing that
``identifier_field`` name (never cross-producted against an unrelated canonical source); a
literal absent from that union is a ``missing_key`` finding carrying ``file:line`` (a 1-based
line number) so a reviewer can jump straight to the offending assignment. This mode is heuristic
and config-gated for exactly that reason -- see ``docs/devbench-yaml-reference.md``'s
``gates.fixture_consistency.extract_source_literals`` section for its documented accuracy bounds
(including that a source-literal finding has no waiver mechanism) and the rationale for
defaulting off. Enabling the mode while the repo checkout resolves ZERO classified source files
(including a checkout whose classified sources live entirely under a pruned directory) is a
loud, pre-scan error naming the resolved scope and the config key (mirroring the
zero-``scan``-list and zero-match-``identifier_field`` shapes above -- an enabled mode that
silently inspected nothing must never look identical to a genuine, clean pass); a directory that
cannot be listed produces exactly one ``load_error`` finding naming the unreadable directory
rather than silently skipping that subtree; a source file that raises ``UnicodeDecodeError`` or
``OSError`` while being read produces exactly one ``load_error`` finding naming the file, never a
silent ``continue``.
"""

from __future__ import annotations

import json
import re
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any, NoReturn

import yaml

from devbench.source_classification import iter_classified_source_files

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
# cannot drift key-by-key across the six `prefix=_CONFIG_KEY_PREFIX` finding
# sites below (E6-F2-S1-T1 round-1 code_review Blocking 4: this count has
# drifted on every task that touched this module so far -- if a future
# change adds or removes a `prefix=_CONFIG_KEY_PREFIX` call site, grep for
# that exact string and update this count in the same change).
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
# Five of these are the loud-error summaries actually passed through
# `_raise_loud_error` (which prefixes each with the spec Section 7
# `ERROR: <summary>` shape): the empty-scan-list finding (322-D05), the
# zero-match-identifier-field finding (322-D02/D03), the malformed-waiver-
# marker raise site, the unmatchable-waiver-marker raise site (the latter
# two both spec 4.7 bullet 5), and the zero-classified-source-files raise
# site (spec 4.7 bullet 4, E6-F2-S1-T1) below. `_MSG_UNSUPPORTED_EXTENSION`
# is declared alongside them but is NOT one of the five -- it backs
# `_UnsupportedFixtureExtensionError`, a distinct exception class raised
# from `_load_parsed` (see that class's own docstring), never passed
# through `_raise_loud_error`.
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
_MSG_ZERO_CLASSIFIED_SOURCE_FILES: str = (
    "{prefix}.extract_source_literals is enabled but zero classified source files were found "
    "under resolved scope '{scope}' (devbench.source_classification.iter_classified_source_files "
    "returned no candidates); the mode has nothing to scan."
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
# `check_fixture_consistency`'s three finding-producing helpers
# (`_check_canonical_sources`, `_check_scan_targets`,
# `_check_source_literals`) format each at their one call site.
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
    "Fixture '{location}' field '{field}' references {count} key(s) absent from canonical source "
    "'{canonical_path}': {keys}. Fix the fixture to reference a real canonical key, or if this is "
    "an intentional edge-case fixture (e.g. testing an empty/not-found state), add a structured "
    '{{"allow_missing": {{"reason": "<non-empty reason>"}}}} marker directly to the record in '
    "'{location}' (spec 4.7 bullet 5 -- the waiver lives in the fixture artifact itself, not in "
    "workspace config)."
)
_MSG_WAIVER_APPLIED: str = (
    "Fixture '{path}' waives missing key '{key}' via its in-fixture allow_missing marker (reason: {reason})."
)
_MSG_SOURCE_LOAD_FAILED: str = "Failed to read source file '{path}' during source-literal extraction: {exc}"
_MSG_SOURCE_SCAN_DIRECTORY_FAILED: str = (
    "Could not enumerate classified source files during source-literal extraction under scope "
    "'{scope}': failed to list directory '{directory}': {exc}. The walk was aborted at this "
    "directory rather than silently skipping it and reporting a clean pass having inspected only "
    "part of the resolved scope; fix the directory's permissions (or otherwise make it readable) "
    "and re-run."
)
_MSG_SOURCE_LITERAL_MISSING_KEY: str = (
    "Source file '{location}' assigns '{field}' the literal value '{value}', which is absent from "
    "canonical source '{canonical_path}' ({prefix}.extract_source_literals heuristic scan mode -- "
    "spec 4.7 bullet 4). Fix the literal to reference a real canonical key, correct the canonical "
    "source if it is the one that is incomplete, or disable {prefix}.extract_source_literals if this "
    "is a false positive (see docs/devbench-yaml-reference.md for the mode's documented accuracy "
    "bounds)."
)

#: SECURITY (security_review AND code_review round-4, CONVERGENT findings; CLAUDE.md 'Sensitive
#: Data Handling' -- never log/display/expose a credential, API key, access/auth token, or
#: session identifier, and mask/redact sensitive data in logs unconditionally). This module
#: previously carried a 32-character length threshold below which a value was echoed IN FULL,
#: plus a 4-character disclosed prefix on any value over that threshold. Both were measured to
#: leak real credential shapes: a Stripe live secret key and a 32-character JSESSIONID sit
#: EXACTLY on a 32-character threshold; an AWS access key ID (20 chars), a PHPSESSID (26 chars),
#: and a short database password (17 chars) all leaked in full under it; and the 4-character
#: prefix on longer values separately disclosed credential TYPE and ISSUER (``ghp_``, ``AKIA``,
#: ``AIza``, ``eyJh`` are all exactly 4 characters) -- a targeting signal `file:line` alone does
#: not provide. No length threshold is defensible: this module's own selection logic reports
#: exactly the literals ABSENT from the canonical catalog, so a hard-coded credential assigned to
#: a matching key is, by construction, guaranteed to be reported here regardless of its length --
#: the same reasoning that justifies withholding a LONG value applies just as much to a SHORT
#: one. :func:`_redact_source_literal_value` therefore redacts every extracted literal
#: UNCONDITIONALLY, never any part of it, and discloses only the value's original length via this
#: message template.
_MSG_SOURCE_LITERAL_VALUE_REDACTED: str = "<redacted, {length} chars total; see file:line above to inspect it directly>"


def _format_fixture_location(path: str, line: int | None = None) -> str:
    """Build the ``path`` or ``path:line`` location fragment plugged into a finding message.

    Single builder shared by :func:`_check_scan_targets` (the structured
    JSON/YAML scan-target cross-reference, which never has a line number --
    a whole-record match, not a per-line one) and :func:`_check_source_literals`
    (the source-literal extraction mode, spec 4.7 bullet 4, which always has one)
    for the ``missing_key`` finding's location fragment, so the ``file`` vs
    ``file:line`` formatting decision exists in exactly one place rather than
    being hand-rolled at each call site (REFACTOR, spec 4.7 bullet 4 Approach
    step 9). *line* is 1-based when provided, matching a text editor's own line
    numbering.
    """
    return path if line is None else f"{path}:{line}"


def _redact_source_literal_value(value: str) -> str:
    """Return a redacted placeholder for *value*, unconditionally -- never any part of *value*.

    SECURITY (security_review AND code_review round-4, CONVERGENT findings; CLAUDE.md 'Sensitive
    Data Handling'): the sole caller, :func:`_check_source_literals`, plugs this function's
    result into :data:`_MSG_SOURCE_LITERAL_MISSING_KEY`'s ``{value}`` slot -- never the raw
    extracted literal. Redaction is UNCONDITIONAL: there is no length below which a value is
    echoed in full, and no leading-character prefix is disclosed either (see
    :data:`_MSG_SOURCE_LITERAL_VALUE_REDACTED`'s own docstring for why any threshold or prefix
    was found indefensible). A `missing_key` finding already carries `file:line`
    (:func:`_format_fixture_location`) and the matched field name, sufficient for a reviewer to
    open the file and inspect the value directly, so the value never needs to be reproduced, in
    whole or in part, for the finding to remain actionable. The only information this function
    discloses is *value*'s original length.
    """
    return _MSG_SOURCE_LITERAL_VALUE_REDACTED.format(length=len(value))


class FixtureConsistencyConfigError(ValueError):
    """A ``gates.fixture_consistency`` configuration cannot produce a meaningful check.

    Raised only by :func:`_raise_loud_error`, for five spec 4.7 loud-error
    paths: 322-D02/D03 zero-match ``identifier_field``, 322-D05 empty
    resolved ``scan`` list while the gate is enabled, (spec 4.7 bullet 5,
    E6-F1-S1-T2) a malformed in-fixture ``allow_missing`` waiver marker
    (wrong shape, or a record missing a non-empty ``reason``), (spec 4.7
    bullet 5, E6-F1-S1-T2, code_review round 1) an ``allow_missing``
    marker attached to a dict whose configured ``identifier_field`` has no
    value in that same dict -- a misspelled/absent identifier field, or a
    marker placed at a fixture's envelope level rather than on an
    individual record -- which can never be matched to a record and so
    must never be silently ignored, and (spec 4.7 bullet 4, E6-F2-S1-T1)
    ``gates.fixture_consistency.extract_source_literals`` enabled while the
    repo checkout resolves zero classified source files to scan -- an
    enabled mode that silently inspected nothing must never look identical
    to a genuine, clean pass, exactly like the ``scan``-list case above.
    The first two are raised OUTSIDE ``_check_canonical_sources``/
    ``_check_scan_targets``'s own parse try/except blocks; the malformed-
    and unmatched-waiver-marker paths are raised FROM INSIDE the parse call
    those blocks wrap (a malformed or unmatched marker is discovered while
    walking a fixture's already-parsed content), so both functions add an
    explicit
    ``except FixtureConsistencyConfigError: raise`` ahead of their generic
    ``(OSError, ValueError, yaml.YAMLError)`` catch -- a malformed or
    unmatched marker must never be silently downgraded into a
    ``load_error`` finding the way a genuine parse failure is. The fifth,
    zero-classified-source-files, path is raised OUTSIDE
    ``_check_source_literals``'s own per-file read try/except, before any
    source file is opened, mirroring the empty-``scan``-list ordering. A
    plain ``ValueError`` also carries two
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
    zero-match-identifier-field (322-D02/D03), malformed-waiver-marker,
    unmatchable-waiver-marker (both spec 4.7 bullet 5), and
    zero-classified-source-files (spec 4.7 bullet 4, E6-F2-S1-T1) raise
    sites cannot drift into independently formatted strings. The exception
    itself is a
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
        kind: One of ``"missing_key"`` (a scan-target identifier, or --
            spec 4.7 bullet 4, E6-F2-S1-T1 -- a source-literal extracted by
            the ``extract_source_literals`` mode, is absent from its
            canonical source; the message text and its ``file`` vs
            ``file:line`` location disambiguate which origin produced a
            given finding), ``"coverage_shortfall"`` (a canonical source's
            distinct identifier count does not match its configured
            ``expected_count``), ``"load_error"`` (a configured fixture
            file could not be found or parsed, or -- spec 4.7 bullet 4 --
            a classified source file raised ``UnicodeDecodeError``/
            ``OSError`` while being read for source-literal extraction),
            or ``"waiver_applied"`` (spec 4.7 bullet 5, E6-F1-S1-T2: a
            record's in-fixture ``allow_missing`` marker suppressed what
            would otherwise have been a ``missing_key`` finding for that
            identifier value -- surfaced here so the suppression is
            visible in the check's own report, not only in the fixture's
            diff).
        message: Human-readable, actionable description including the
            offending file path(s) (``file:line`` for a source-literal
            finding) and identifier value(s).
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
                        location=_format_fixture_location(scan.path),
                        field=scan.identifier_field,
                        count=len(missing),
                        canonical_path=canonical_path,
                        keys=", ".join(missing),
                    ),
                )
            )

    return findings


# ---------------------------------------------------------------------------
# Source-literal extraction (spec 4.7 bullet 4; caylent-solutions/
# devbench-internal-backlog#17 AC-19; E6-F2-S1-T1): the config-gated
# ``extract_source_literals`` scan mode.
# ---------------------------------------------------------------------------
#
# The *identifier_field grammar* (Definition of Ready bullet 3): a candidate
# assignment is ``<field>``, optionally single/double-quoted, followed by a
# ``:`` or ``=`` separator (optional surrounding whitespace), followed by
# EITHER a single/double-quoted string literal OR a bare integer/float
# literal. Matched per PHYSICAL LINE, never across a multi-line span --
# this bounds every regex application to one line's length (linear on
# adversarial whitespace/quote runs; no nested/ambiguous quantifiers), and
# is what makes the resulting 1-based line number meaningful. This is a
# deliberately narrow heuristic grammar, not a real parser for any of the
# languages `source_classification.SOURCE_EXTENSIONS` covers -- it matches
# a Python dict-literal key (`"sku": "X"`), a Python keyword/module-level
# assignment (`sku = "X"`), a JS/TS object-literal property (`sku: "X"`),
# and analogous shapes in every other classified language, but it has no
# notion of comments, string interpolation, or which shapes are actually
# reachable code. See `docs/devbench-yaml-reference.md`'s
# `gates.fixture_consistency.extract_source_literals` section for the full
# documented accuracy bounds this heuristic posture implies.
#
# `(?!\1)` immediately after the opening quote group (doc_review round-1 D2)
# rejects an opening quote that is IMMEDIATELY followed by another instance
# of the same quote character -- a single `\1`-length backreference peek,
# not a new quantified sub-pattern, so this stays linear on adversarial
# input (re-measured after this change, see the perf note above this
# module's `_compile_source_literal_patterns`). Without it, the opening
# `""`/`''` of a triple-quoted string (`"""..."""`) was misread as a
# COMPLETE, closed, empty-string literal -- a correctness defect (the
# finding reported the wrong value, not merely a missed detection) rather
# than the intended "a value spread across more than one physical line is
# never matched" bound. With the guard, a single-line triple-quoted value
# and a genuinely empty `""`/`''` string are both simply unmatched -- a
# deliberately narrower, but never factually wrong, result.
_SOURCE_LITERAL_STRING_PATTERN = r"""\b{field}\b['"]?\s*[:=]\s*(['"])(?!\1)((?:[^'"\\]|\\.)*)\1"""
_SOURCE_LITERAL_NUMBER_PATTERN = r"""\b{field}\b['"]?\s*[:=]\s*(-?\d+(?:\.\d+)?)\b"""


def _compile_source_literal_patterns(field_name: str) -> tuple[re.Pattern[str], re.Pattern[str]]:
    """Compile the string- and number-literal identifier-field-grammar patterns for *field_name*.

    *field_name* is escaped via :func:`re.escape` before being interpolated
    into either template -- an operator-configured ``identifier_field``
    containing a regex metacharacter (e.g. ``sku.id``) must match itself
    literally, never be interpreted as a regex fragment.
    """
    escaped = re.escape(field_name)
    return (
        re.compile(_SOURCE_LITERAL_STRING_PATTERN.format(field=escaped)),
        re.compile(_SOURCE_LITERAL_NUMBER_PATTERN.format(field=escaped)),
    )


def _extract_identifier_literals(lines: list[str], field_name: str) -> list[tuple[int, str]]:
    """Scan *lines* for the identifier_field grammar and return every ``(1-based line, value)`` match.

    Applies the compiled string- and number-literal patterns to EACH line
    independently (never the joined multi-line text) -- see the module
    section comment above this function for why that bounds regex cost and
    is what makes the returned line number meaningful. A line carrying more
    than one match (e.g. two records on one line) contributes one tuple per
    match.
    """
    string_re, number_re = _compile_source_literal_patterns(field_name)
    matches: list[tuple[int, str]] = []
    for line_no, line in enumerate(lines, start=1):
        for match in string_re.finditer(line):
            matches.append((line_no, match.group(2)))
        for match in number_re.finditer(line):
            matches.append((line_no, match.group(1)))
    return matches


def _group_relevant_sources_by_identifier_field(
    canonical_sources: tuple[FixtureCanonicalSource, ...],
    canonical_values: dict[str, set[str]],
) -> dict[str, list[FixtureCanonicalSource]]:
    """Group canonical sources whose own load succeeded by their ``identifier_field`` name.

    A raw source-file literal carries no explicit ``canonical_source``
    designation the way a configured ``scan`` entry does (there is nothing
    for :func:`_resolve_canonical_path` to read here) -- but every
    canonical source sharing the same ``identifier_field`` name describes
    the same conceptual identifier namespace, so grouping by that name is
    the resolution :func:`_check_source_literals` needs (E6-F2-S1-T1
    round-1 code_review Blocking 2): a matched literal is checked against
    the UNION of its group's canonical value sets exactly once, so it is
    never cross-producted against an unrelated identifier namespace, and
    never yields more than one finding per (file, line, value).

    Only sources present in *canonical_values* (i.e. whose own load already
    succeeded) are included -- a canonical source that failed to load has
    no identifier set to compare against and is already reported by
    :func:`_check_canonical_sources`.
    """
    groups: dict[str, list[FixtureCanonicalSource]] = {}
    for source in canonical_sources:
        if source.path not in canonical_values:
            continue
        groups.setdefault(source.identifier_field, []).append(source)
    return groups


def _check_source_literals(
    repo_path: Path,
    canonical_sources: tuple[FixtureCanonicalSource, ...],
    canonical_values: dict[str, set[str]],
) -> list[FixtureFinding]:
    """Scan the classified source files in *repo_path* for identifier literals absent from a canonical source.

    Only called by :func:`check_fixture_consistency` when
    ``config.extract_source_literals`` is true (spec 4.7 bullet 4,
    E6-F2-S1-T1) -- the default-off contract (AC-E6-F2-S1-T1-2) lives at
    that call site, not here.

    Enumerates candidate files via
    :func:`devbench.source_classification.iter_classified_source_files`
    (PM-3: the single owner of extension classification -- this module
    declares no extension tuple of its own, AC-E6-F2-S1-T1-3) exactly once,
    then reads each file's text exactly once regardless of how many
    canonical sources are configured, so a file that fails to read produces
    exactly one ``load_error`` finding (AC-E6-F2-S1-T1-5) rather than one
    per configured canonical source. Every successfully-read file is then
    scanned once per DISTINCT ``identifier_field`` name (via
    :func:`_group_relevant_sources_by_identifier_field`, round-1
    code_review Blocking 2), never once per canonical source -- a matched
    literal is resolved against the union of every canonical source sharing
    that field name, so it produces at most one finding regardless of how
    many canonical sources share the field.

    Reads are unbounded (``Path.read_text``), matching this module's
    existing ``_load_parsed`` precedent: a classified source file is
    developer-authored content already trusted within the target repo
    checkout, not untrusted external input, so no additional size bound is
    applied here beyond what the rest of this module already accepts.

    Raises:
        FixtureConsistencyConfigError: If zero classified source files are
            found under *repo_path* (AC-E6-F2-S1-T1-4) -- checked before
            any source file is read, naming the resolved scope
            (*repo_path*) and the ``extract_source_literals`` config key,
            mirroring :func:`check_fixture_consistency`'s own
            empty-``scan``-list guard.
    """
    try:
        source_files = iter_classified_source_files(repo_path)
    except OSError as exc:
        # `iter_classified_source_files` aborts its walk (rather than
        # silently skipping the unreadable subtree, the round-1
        # code_review Blocking 1 fix) the moment a directory under
        # *repo_path* cannot be listed. That must never look identical to
        # a clean pass that genuinely inspected the whole scope, so it
        # becomes a single blocking `load_error` finding naming the
        # unreadable directory rather than a sixth `_raise_loud_error`
        # path (spec Section 7; BLOCKING_FINDING_KINDS already makes
        # `load_error` block the gate).
        raw_directory = Path(getattr(exc, "filename", None) or repo_path)
        if raw_directory == repo_path:
            # W-b (round-3 code_review): the unreadable directory IS the resolved scope
            # root itself -- `raw_directory.relative_to(repo_path)` collapses to `Path('.')`,
            # whose `.as_posix()` is the bare, unhelpful string `'.'`. Name the scope path
            # itself instead, matching the `under scope '<scope>'` clause already present
            # earlier in the same message.
            directory = str(repo_path)
        else:
            try:
                # Repo-relative, mirroring `_MSG_SOURCE_LOAD_FAILED`'s `path` slot
                # below (W4, E6-F2-S1-T1 round-2 code_review finding): the two
                # sibling `load_error` message templates must format the same way,
                # rather than one being absolute and the other repo-relative.
                directory = raw_directory.relative_to(repo_path).as_posix()
            except ValueError:
                directory = str(raw_directory)
        return [
            FixtureFinding(
                "load_error",
                _MSG_SOURCE_SCAN_DIRECTORY_FAILED.format(scope=repo_path, directory=directory, exc=exc),
            )
        ]

    if not source_files:
        _raise_loud_error(_MSG_ZERO_CLASSIFIED_SOURCE_FILES.format(prefix=_CONFIG_KEY_PREFIX, scope=repo_path))

    field_groups = _group_relevant_sources_by_identifier_field(canonical_sources, canonical_values)
    findings: list[FixtureFinding] = []

    for abs_path in source_files:
        rel_path = abs_path.relative_to(repo_path).as_posix()
        try:
            text = abs_path.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError) as exc:
            findings.append(FixtureFinding("load_error", _MSG_SOURCE_LOAD_FAILED.format(path=rel_path, exc=exc)))
            continue

        lines = text.splitlines()
        for field_name, group in field_groups.items():
            union_values: set[str] = set()
            for source in group:
                union_values |= canonical_values[source.path]
            for line_no, value in _extract_identifier_literals(lines, field_name):
                if value in union_values:
                    continue
                findings.append(
                    FixtureFinding(
                        "missing_key",
                        _MSG_SOURCE_LITERAL_MISSING_KEY.format(
                            location=_format_fixture_location(rel_path, line_no),
                            field=field_name,
                            value=_redact_source_literal_value(value),
                            canonical_path=", ".join(sorted(source.path for source in group)),
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
            resolved identifier set is empty for any reason (322-D02/D03);
            or (via :func:`_check_canonical_sources` or
            :func:`_check_scan_targets`, spec 4.7 bullet 5) if any
            fixture's content carries a malformed or unmatchable in-fixture
            ``allow_missing`` waiver marker; or (via
            :func:`_check_source_literals`, spec 4.7 bullet 4, E6-F2-S1-T1)
            if ``config.extract_source_literals`` is true but the repo
            checkout resolves zero classified source files to scan --
            checked before any source file is read.
    """
    if not config.canonical_sources:
        return []

    if not config.scan:
        _raise_loud_error(_MSG_EMPTY_SCAN_LIST)

    canonical_findings, canonical_values = _check_canonical_sources(repo_path, config.canonical_sources)
    scan_findings = _check_scan_targets(repo_path, config.scan, config.canonical_sources, canonical_values)
    literal_findings: list[FixtureFinding] = []
    if config.extract_source_literals:
        literal_findings = _check_source_literals(repo_path, config.canonical_sources, canonical_values)
    return canonical_findings + scan_findings + literal_findings
