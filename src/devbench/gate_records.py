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
"""

from __future__ import annotations

import hashlib
import re
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime

from devbench.constants import GATE_TIERS

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
