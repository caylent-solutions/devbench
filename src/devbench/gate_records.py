"""`[GATE_PASS]` marker composition, parsing and scope-hash primitives.

Spec `integration-reality-gates-hardening.md` section 4.2 (tier taxonomy,
persisted machine record, scope hash) and section 5.3 (`[GATE_PASS <gate>]
<iso-utc> <scope-hash>` marker grammar). Per Section 3.6, executor agents are
not trusted to self-certify gate outcomes: this module is the sole authority
for the marker grammar (AC-E2-F2-S1-T1-6), so a `[GATE_PASS]` record can only
ever be produced by calling :func:`compose_gate_pass_record` -- never
hand-typed by agent prose.

Mirroring :mod:`devbench.tdd_gate` (the RED gate's pure observation engine),
this module performs no work-unit-file I/O of its own:
:func:`compose_gate_pass_record` returns text for the caller to persist (via
the same audit-append machinery :func:`devbench.cli.write_red_observed_entry`
uses for `RED_OBSERVED`, wired by the gate-specific tasks that consume this
module -- E3 through E7), and :func:`latest_gate_pass_record` operates on a
content string the caller supplies rather than reading a work-unit file
itself. Every timestamp is timezone-aware UTC, and :func:`compute_scope_hash`
is a pure function of a caller-supplied file-to-blob-hash mapping (resolved
from git plumbing output by the caller) -- so the whole module is testable
without a live repo (spec Section 3.6).

The marker is additive to the audit-comment contract (spec Section 5.3): a
gate command may embed it as the message body of a normal audit comment
(mirroring `[BLOCKED]`/`[QUOTA_WAITING]`-style bracketed tags -- see
`devbench.cli._BLOCKED_AUDIT_LINE_RE`), so :func:`latest_gate_pass_record`
locates the tag wherever it appears on a line rather than requiring the
marker to be the entire line.

:func:`gate_waiver_records` and :func:`gate_waiver_targets` (spec 4.9, Section
2 G7) are this module's read side for the sibling `[GATE_WAIVER <gate>]
<iso-utc> <target> <operator|executor> <reason>` marker family, and its SOLE
reader: :func:`gate_waiver_records` is the one shared scan-and-parse loop
every consumer of that marker family builds on --
:func:`devbench.backlog.manager._latest_gate_waiver_attribution` (the
generic `mark_done` gate-record invariant's whole-gate waiver bypass) calls
it directly rather than re-scanning the content with its own copy of the tag
regex, so "what a well-formed `[GATE_WAIVER]` marker looks like, and which
one is most recent" has exactly one implementation. A gate command (e.g.
`check-reachability`) calls :func:`gate_waiver_targets` to learn, per
candidate, the most recent full waiver record an operator or executor has
filed for it -- but because reachability (like every gate in
`constants.GATE_TIERS`'s machine-blocking tier, spec Section 3.6/D-6) does
not trust an executor to self-certify its own finding, the caller MUST check
`record.attribution` itself before treating a target as cleared: only an
operator-attributed record may suppress a finding, render `[WAIVED] <target>
-- <reason>`, or contribute to a clean run that persists a `[GATE_PASS]`
record. `devbench.backlog.manager.compose_gate_waiver_record` /
`parse_gate_waiver_record` remain the sole grammar authority for the marker
family (mirroring this module's own role for `[GATE_PASS]`); both readers
below consume that parser rather than duplicating its grammar, and -- like
every other reader in this module -- perform no work-unit-file I/O of their
own.
"""

from __future__ import annotations

import hashlib
import re
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import TYPE_CHECKING

from devbench.constants import GATE_TIERS

if TYPE_CHECKING:
    from devbench.backlog.manager import GateWaiverRecord

# Fixed width of a SHA-256 hex digest -- the scope-hash shape both
# `compose_gate_pass_record` (input validation) and the marker grammar
# (output validation) require. Mirrors `FAILURE_DIGEST_RE`'s hash-shape
# discipline for `RED_OBSERVED`: a scope hash is never raw free text, so a
# malformed/forged value can never be mistaken for a well-formed record.
_SCOPE_HASH_LENGTH: int = 64
_SCOPE_HASH_RE = re.compile(rf"^[0-9a-f]{{{_SCOPE_HASH_LENGTH}}}$")

# REFACTOR (spec 5.3): the "GATE_PASS" tag name is the one token every
# marker-grammar surface below is built from -- the record template
# (`compose_gate_pass_record`), the full-record pattern (`parse_gate_pass_record`),
# and the embedded-tag locator (`latest_gate_pass_record`) each used to spell
# it out independently. Single-sourcing it here means the literal string
# "GATE_PASS" is written exactly once in this module; every consumer builds
# from `_TAG_NAME`, so the tag can never drift between the three surfaces.
_TAG_NAME: str = "GATE_PASS"
_GATE_GROUP_RE: str = r"[A-Za-z0-9_]+"

# Single-sourced marker grammar (spec 5.3): "[GATE_PASS <gate>] <iso-utc>
# <scope-hash>". `_RECORD_TEMPLATE` (format) and `_RECORD_RE` (match) are the
# one shared grammar definition `compose_gate_pass_record` and
# `parse_gate_pass_record` both build on, so the shape is expressed exactly
# once.
_RECORD_TEMPLATE: str = "[" + _TAG_NAME + " {gate}] {timestamp} {scope_hash}"
_RECORD_RE = re.compile(
    r"^\[" + _TAG_NAME + r" (?P<gate>" + _GATE_GROUP_RE + r")\] (?P<timestamp>\S+) (?P<scope_hash>[0-9a-f]{64})$"
)

# Locates a `[GATE_PASS <gate>]` tag wherever it appears within a line --
# used by `latest_gate_pass_record` to find a record embedded inside a
# larger audit-comment line (e.g. `[<timestamp>] [agent/<name>] [GATE_PASS
# <gate>] <iso-utc> <scope-hash>`), not only a record that is the entire
# line.
_TAG_RE = re.compile(r"\[" + _TAG_NAME + r" (?P<gate>" + _GATE_GROUP_RE + r")\]")


@dataclass(frozen=True)
class GatePassRecord:
    """A parsed `[GATE_PASS <gate>] <iso-utc> <scope-hash>` marker (spec 5.3).

    Attributes:
        gate: The declared gate name (a `devbench.constants.GATE_TIERS` key).
        timestamp: The timezone-aware UTC instant the gate passed.
        scope_hash: The SHA-256 hex digest over the scope that passed.
    """

    gate: str
    timestamp: datetime
    scope_hash: str


def _require_declared_gate(gate: str) -> None:
    """Raise ValueError naming *gate* and the declared gates when *gate* is not declared."""
    if gate not in GATE_TIERS:
        raise ValueError(f"Unknown gate {gate!r}; declared gates are: {', '.join(sorted(GATE_TIERS))}.")


def compute_scope_hash(file_blob_hashes: Mapping[str, str]) -> str:
    """Compute the spec-4.2 scope hash over a changed-file -> blob-hash mapping.

    SHA-256 over the sorted changed-file list plus each file's blob hash, so
    the hash changes when a file is added to the scope or when a file's
    content (blob hash) changes, and is stable for identical inputs
    regardless of mapping insertion order (spec 4.2, AC-7). Blob hashes are
    computed by the caller from git plumbing output (e.g. `git
    hash-object`/`git ls-tree`); this function performs no git I/O of its
    own, keeping it a pure function of its inputs.

    Args:
        file_blob_hashes: Repo-relative changed-file path -> that file's git
            blob hash (hex string). Must be non-empty.

    Returns:
        A lowercase SHA-256 hex digest of the sorted `path:blob_hash` pairs.

    Raises:
        ValueError: If `file_blob_hashes` is empty -- an empty scope must
            never hash into a passing record.
    """
    if not file_blob_hashes:
        raise ValueError("Cannot compute a scope hash over an empty change set; at least one changed file is required.")

    digest_input = "\n".join(f"{path}:{file_blob_hashes[path]}" for path in sorted(file_blob_hashes))
    return hashlib.sha256(digest_input.encode("utf-8")).hexdigest()


def compose_gate_pass_record(gate: str, scope_hash: str, *, timestamp: datetime | None = None) -> str:
    """Compose the single-line `[GATE_PASS <gate>] <iso-utc> <scope-hash>` marker.

    The sole authorized builder of the marker text (AC-E2-F2-S1-T1-6): a gate
    command calls this function rather than formatting the tag itself, so a
    `[GATE_PASS]` record can only ever be produced from an already-resolved
    scope hash supplied by the caller -- never from agent prose.

    Args:
        gate: One of the declared `devbench.constants.GATE_TIERS` gate names.
        scope_hash: A SHA-256 hex digest, e.g. from `compute_scope_hash`.
        timestamp: The timezone-aware instant the gate passed. Defaults to
            the current UTC time. A timezone-aware value in another zone is
            converted to UTC; a naive value is rejected.

    Returns:
        The exact one-line marker text (no trailing newline).

    Raises:
        ValueError: If `gate` is not declared, `scope_hash` is not a
            64-character lowercase hex string, or `timestamp` is naive.
    """
    _require_declared_gate(gate)
    if not _SCOPE_HASH_RE.match(scope_hash):
        raise ValueError(
            f"scope_hash must be a {_SCOPE_HASH_LENGTH}-character lowercase hex string (SHA-256); got {scope_hash!r}."
        )

    if timestamp is None:
        resolved_timestamp = datetime.now(tz=UTC)
    elif timestamp.tzinfo is None:
        raise ValueError(f"timestamp must be timezone-aware; got a naive datetime {timestamp!r}.")
    else:
        resolved_timestamp = timestamp.astimezone(UTC)

    return _RECORD_TEMPLATE.format(gate=gate, timestamp=resolved_timestamp.isoformat(), scope_hash=scope_hash)


def parse_gate_pass_record(line: str) -> GatePassRecord:
    """Parse one isolated `[GATE_PASS <gate>] <iso-utc> <scope-hash>` marker.

    Args:
        line: A single already-isolated marker string, e.g. one produced by
            `compose_gate_pass_record` (leading/trailing whitespace is
            tolerated and stripped).

    Returns:
        The parsed `GatePassRecord`.

    Raises:
        ValueError: If `line` does not match the spec-5.3 grammar exactly,
            names an undeclared gate, or carries a timestamp that is not a
            timezone-aware ISO-8601 value. No partial record is ever
            returned.
    """
    match = _RECORD_RE.match(line.strip())
    if match is None:
        raise ValueError(f"Malformed [GATE_PASS] marker (does not match the spec 5.3 grammar): {line!r}")

    gate = match.group("gate")
    _require_declared_gate(gate)

    raw_timestamp = match.group("timestamp")
    try:
        timestamp = datetime.fromisoformat(raw_timestamp)
    except ValueError as exc:
        raise ValueError(f"Malformed [GATE_PASS] marker (timestamp is not valid ISO-8601): {line!r}") from exc
    if timestamp.tzinfo is None:
        raise ValueError(f"Malformed [GATE_PASS] marker (timestamp is not timezone-aware): {line!r}")

    return GatePassRecord(gate=gate, timestamp=timestamp, scope_hash=match.group("scope_hash"))


def latest_gate_pass_record(content: str, gate: str) -> GatePassRecord | None:
    """Return the most recent `[GATE_PASS <gate>]` record for *gate* in *content*.

    Scans *content* (typically a work unit's full text, or its audit
    section) line by line for a `[GATE_PASS <gate>]` tag -- wherever it
    appears on the line, since the marker is additive to the audit-comment
    contract and may be embedded after a `[<timestamp>] [agent/<name>]`
    prefix -- and returns the record parsed from the LAST matching line (the
    audit trail is append-only, so the last match is the most recent). Lines
    naming a different gate are ignored entirely. Returns `None` when no
    record for *gate* is present.

    Args:
        content: The text to scan.
        gate: The declared gate name to look up.

    Returns:
        The most recent matching `GatePassRecord`, or `None`.

    Raises:
        ValueError: If `gate` is not declared, or if a line tagged
            `[GATE_PASS <gate>]` is present but the remainder of that line
            does not parse as a well-formed record -- a malformed record for
            the requested gate is never silently skipped.
    """
    _require_declared_gate(gate)

    latest: GatePassRecord | None = None
    for line in content.splitlines():
        tag_match = _TAG_RE.search(line)
        if tag_match is None or tag_match.group("gate") != gate:
            continue
        latest = parse_gate_pass_record(line[tag_match.start() :])
    return latest


# Locates a `[GATE_WAIVER <gate>]` tag wherever it appears within a line,
# mirroring `_TAG_RE`'s role for the sibling `[GATE_PASS]` family --
# `gate_waiver_records` isolates the remainder of the line from this match's
# start and hands it to `devbench.backlog.manager.parse_gate_waiver_record`,
# the sole grammar authority for the full `[GATE_WAIVER <gate>] <iso-utc>
# <target> <operator|executor> <reason>` shape (spec 5.3), so this module
# never re-derives that grammar itself.
_WAIVER_TAG_RE = re.compile(r"\[GATE_WAIVER (?P<gate>" + _GATE_GROUP_RE + r")\]")


def gate_waiver_records(content: str, gate: str) -> list[GateWaiverRecord]:
    """Return every well-formed `[GATE_WAIVER <gate>]` record in *content*, in file order.

    This is the SOLE scan-and-parse loop for the `[GATE_WAIVER <gate>]`
    marker family (spec 4.9, 5.3): both :func:`gate_waiver_targets` below and
    `devbench.backlog.manager._latest_gate_waiver_attribution` (the generic
    `mark_done` gate-record invariant's whole-gate waiver bypass) build on
    this function rather than each re-scanning *content* with their own copy
    of the tag regex, so "what a well-formed `[GATE_WAIVER]` marker looks
    like" has exactly one implementation.

    Scans *content* line by line for a `[GATE_WAIVER <gate>]` tag -- wherever
    it appears on the line, the same embedded-anywhere convention
    :func:`latest_gate_pass_record` uses for `[GATE_PASS]` -- and parses the
    remainder of that line with
    `devbench.backlog.manager.parse_gate_waiver_record`, the sole grammar
    authority for the `[GATE_WAIVER]` marker family: this module duplicates
    no marker grammar of its own, it only consumes that parser. Every
    well-formed record is returned, including every attribution
    (`"operator"` or `"executor"`) and every target -- callers that must
    honour spec 3.6's operator-only rule for a machine-blocking gate (e.g.
    :func:`gate_waiver_targets`'s consumers) are responsible for filtering on
    `record.attribution` themselves; this function performs no such
    filtering, since a judge-evidence gate's caller may legitimately want
    every attribution.

    Args:
        content: The text to scan (typically a work unit's full text).
        gate: The declared gate name to look up.

    Returns:
        A list of `GateWaiverRecord` in the order their markers appear in
        *content* (the audit trail is append-only, so this is also
        chronological order). Empty when no marker for *gate* is present.

    Raises:
        ValueError: If `gate` is not declared, or if a line tagged
            `[GATE_WAIVER <gate>]` is present but the remainder of that line
            does not parse as a well-formed record (spec Section 7 fail-loud
            rule) -- a malformed waiver for the requested gate is never
            silently skipped or treated as "not a waiver".
    """
    _require_declared_gate(gate)
    from devbench.backlog.manager import parse_gate_waiver_record

    records: list[GateWaiverRecord] = []
    for line in content.splitlines():
        tag_match = _WAIVER_TAG_RE.search(line)
        if tag_match is None or tag_match.group("gate") != gate:
            continue
        records.append(parse_gate_waiver_record(line[tag_match.start() :]))
    return records


def gate_waiver_targets(content: str, gate: str) -> dict[str, GateWaiverRecord]:
    """Return ``{target: GateWaiverRecord}`` for every well-formed `[GATE_WAIVER <gate>]` marker in *content*.

    Spec 4.9 / Section 2 G7: a machine-blocking gate command (e.g.
    `check-reachability`) calls this to learn which of its own candidate
    findings have a waiver on file, so a waived artifact can be reported as
    `[WAIVED] <target> -- <reason>` and excluded from the gate's blocking
    findings count. The FULL record is returned -- not just the reason --
    because spec Section 3.6/D-6 makes the operator the only waiver
    authority for a machine-blocking gate: the caller MUST inspect
    `record.attribution` and honour only `"operator"`-attributed records
    before treating a target as cleared. An executor-attributed record is
    still returned here (this function performs no attribution filtering of
    its own -- that would silently hide the distinction from a caller that
    legitimately needs to see and reject it), but a caller for a
    machine-blocking gate must never suppress a finding, print `[WAIVED]`,
    or persist a `[GATE_PASS]` record on the strength of one alone.

    Built on :func:`gate_waiver_records`, this module's sole scan-and-parse
    loop for the marker family, rather than re-deriving the grammar. The
    audit trail is append-only, so when the same *target* is waived more
    than once, the LAST marker for that target wins.

    Args:
        content: The text to scan (typically a work unit's full text).
        gate: The declared gate name to look up.

    Returns:
        A `{target: GateWaiverRecord}` mapping. Empty when no marker for
        *gate* is present in *content*.

    Raises:
        ValueError: If `gate` is not declared, or if a line tagged
            `[GATE_WAIVER <gate>]` is present but the remainder of that line
            does not parse as a well-formed record (spec Section 7 fail-loud
            rule) -- a malformed waiver for the requested gate is never
            silently skipped or treated as "not a waiver".
    """
    targets: dict[str, GateWaiverRecord] = {}
    for record in gate_waiver_records(content, gate):
        targets[record.target] = record
    return targets
