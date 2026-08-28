"""Copy-pattern permission/eligibility flag write-path audit (QA finding 07).

Specs sometimes introduce a new derived boolean permission/eligibility
field by instructing the implementer to "follow the exact existing
pattern of `<some-existing-flag>`." Left unchecked, ``spec-to-backlog``
would translate that into tasks for "add the field to the state slice"
and "gate the UI on the field" -- but never a task for "wire this flag
to a real (or explicit placeholder) data source." If the referenced
existing flag itself has no working write-path (hardcoded to a default
with no setter anywhere), the new flag silently inherits the same
defect, and nothing in backlog generation ever surfaces that risk.

This module gives the ``spec-to-backlog`` skill (Step 3b) a deterministic,
best-effort way to check a referenced flag's write-path status in the
target repo checkout BEFORE backlog generation proceeds, and to locate an
existing placeholder/mock permission-provider seam so the mandatory
new-flag write-path task (Step 4a) can name a concrete destination
instead of leaving it unspecified.

Heuristic, not an oracle
========================

Static analysis cannot reliably prove "this flag is never written" across
arbitrary languages and frameworks. This audit is a fast, source-grep-based
heuristic meant to surface a finding for a human/agent to confirm or
dismiss -- it is deliberately conservative: any verdict other than
``"live"`` (:attr:`WritePathAudit.is_verified_live` is ``False``) is
treated as a BLOCKING finding by the skill, requiring explicit operator
acknowledgement before Step 4 proceeds for that spec clause. False
positives (calling a real write path "not live") cost a confirmation
round-trip; false negatives (calling a dead flag "live") would silently
reproduce exactly the defect this audit exists to catch, so the
classifier never guesses in that direction.

The `spec-to-backlog` skill's Step 3b previously invoked this module
directly via the Bash tool, e.g.::

    uv run python -c "from devbench.plugin_helpers.permission_flag_writepath \\
        import audit_write_path; from pathlib import Path; \\
        print(audit_write_path(Path('<target-repo-checkout>'), \\
        '<existing-flag-name>').render())"

Spec 4.8 rejects that shape as an unversioned interface; the ``devbench
check-write-path`` CLI verb (:func:`devbench.cli.cmd_check_write_path`)
is now the versioned surface every caller other than the skill's own
Step 3b narrative should use. ``audit_write_path`` stays importable and
public for that narrative use.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from devbench.source_classification import is_write_path_audit_extension

# Directories excluded from the scan: dependency trees, build output, and
# the devbench backlog tree itself (a flag name can legitimately appear in
# work-unit prose without that being a write path).
_EXCLUDED_DIR_NAMES: frozenset[str] = frozenset(
    {
        "node_modules",
        ".git",
        "dist",
        "build",
        "out",
        "__pycache__",
        ".venv",
        "venv",
        "backlog",
        ".mypy_cache",
        ".pytest_cache",
        "coverage",
    }
)

# A relative-path signal that the assignment site is a static default /
# type / constant definition rather than a runtime write path.
_DEFAULT_SIGNAL_RE = re.compile(
    r"(default|initial[-_]?state|constants?\b|types?\b|schema|models?\b|fixtures?)",
    re.IGNORECASE,
)

# A relative-path signal that a file is a placeholder/mock permission or
# eligibility data-provider seam -- the destination the mandatory new-flag
# write-path task should register into when one exists (issue AC 3).
_PLACEHOLDER_SEAM_RE = re.compile(
    r"(mock|placeholder|stub|fake)[^/\\]*(permission|entitlement|eligib|flag)",
    re.IGNORECASE,
)

# Public verdict vocabulary (spec 4.8, 321-D03/321-D28): named once here
# rather than as private module-only constants, because `devbench.cli`'s
# `cmd_check_write_path` (spec Section 14) needs to branch on the SAME
# vocabulary this module produces -- a second, hand-typed copy of these
# literal strings in `cli.py` would be exactly the drift risk the DRY
# standard exists to close. `WritePathAudit.verdict` is always one of
# these five values.
VERDICT_LIVE: str = "live"
VERDICT_DEFAULT: str = "default"
VERDICT_NO_WRITE_PATH: str = "no_write_path"
VERDICT_NOT_FOUND: str = "not_found"
VERDICT_INDETERMINATE: str = "indeterminate"

# SECURITY (security_review MEDIUM SECRET_LEAK, this unit; CLAUDE.md
# "Sensitive Data Handling" -- never log/display/expose a credential, mask
# or redact sensitive data in logs unconditionally). `WritePathAudit.render`
# used to print `FlagAssignmentSite.line_text` verbatim, so a
# credential-shaped assignment (e.g. `STRIPE_SECRET_KEY = "sk_live_..."`)
# was materialised unredacted into gate stdout, CI logs and any PR comment
# quoting them. Following the sibling gate's precedent
# (`fixture_consistency._redact_source_literal_value`): redaction is
# UNCONDITIONAL, with no length threshold and no disclosed prefix -- a
# `relative_path:line_number` location plus the already-computed
# `expression_verdict` is sufficient for a reviewer to open the file and
# inspect the real value directly, so the matched line never needs to be
# reproduced, in whole or in part, for the finding to remain actionable.
_MSG_ASSIGNMENT_LINE_REDACTED: str = "<line redacted; see file:line above to inspect it directly>"


@dataclass(frozen=True)
class FlagAssignmentSite:
    """One line in the target repo that looks like it writes the flag.

    ``expression_verdict`` is the assignment-context classifier's verdict
    (spec 4.8, 321-D03) for THIS site alone -- one of
    :data:`VERDICT_LIVE`, :data:`VERDICT_DEFAULT` or
    :data:`VERDICT_INDETERMINATE` (never :data:`VERDICT_NO_WRITE_PATH` or
    :data:`VERDICT_NOT_FOUND`, which describe the absence of any site at
    all and so never apply to one that exists) -- computed once at
    collection time in :func:`audit_write_path` and consumed by
    :func:`_classify` to decide the overall :class:`WritePathAudit`
    verdict.

    ``line_text`` is retained on this dataclass for internal
    classification use (e.g. a future in-process consumer needing the raw
    matched line) but is NEVER rendered: :meth:`WritePathAudit.render`
    (security_review MEDIUM SECRET_LEAK, this unit) redacts it
    unconditionally, printing only ``relative_path``, ``line_number`` and
    ``expression_verdict``.
    """

    relative_path: str
    line_number: int
    line_text: str
    expression_verdict: str


@dataclass(frozen=True)
class WritePathAudit:
    """Result of auditing a single flag name's write-path status."""

    flag_name: str
    verdict: str
    assignment_sites: tuple[FlagAssignmentSite, ...]
    mention_count: int

    @property
    def is_verified_live(self) -> bool:
        """``True`` when the final verdict is `live` (doc_review round 1, W3).

        A `live` verdict is now reached ONLY through confirmed
        assignment-context analysis (:func:`_classify_rhs_expression`): at
        least one site's assigned value is itself evidently runtime-derived
        (a setter call argument, or an attribute/subscript access on a
        request/action/payload-shaped identifier). :func:`_classify_path_tiebreak`
        can NO LONGER manufacture a `live` verdict from path vocabulary
        alone -- security_review (this unit) found that branch decided
        `live` for a site whose expression analysis was inconclusive purely
        because its PATH carried live vocabulary (e.g. `isGhostFlag =
        someUnknownVar;` under a `slice`-named path previously classified
        `live` with zero confirmed runtime evidence), reproducing 321-D03
        in the direction the module's own docstring says the classifier
        must never guess. See :func:`_classify_path_tiebreak`'s own
        docstring for the removed branch and what remains.

        This describes the spec-to-backlog SKILL's Step 3b authoring-time
        contract: every other verdict (``default``, ``no_write_path``,
        ``not_found``, ``indeterminate``) is treated there as a blocking
        finding requiring explicit operator acknowledgement, per the
        module docstring. It does NOT describe the ``check-write-path``
        CLI verb's exit-code contract, where ``indeterminate`` is status
        pass and never blocks (AC-WP-005, docs/cli-reference.md).
        """
        return self.verdict == VERDICT_LIVE

    def render(self) -> str:
        """Render the `[PERMISSION_FLAG_WRITE_PATH_AUDIT]` audit line + site list.

        SECURITY (security_review MEDIUM SECRET_LEAK, this unit): each
        site's matched source line is REDACTED unconditionally -- never
        printed, in whole or in part. Only `relative_path`, `line_number`
        and the already-computed `expression_verdict` are rendered; see
        :data:`_MSG_ASSIGNMENT_LINE_REDACTED`'s own module-level comment for
        why no length threshold or disclosed prefix is used.
        """
        lines = [
            f"[PERMISSION_FLAG_WRITE_PATH_AUDIT] {self.flag_name}: "
            f"verdict={self.verdict} mentions={self.mention_count} "
            f"assignment_sites={len(self.assignment_sites)}"
        ]
        if self.assignment_sites:
            for site in self.assignment_sites:
                lines.append(
                    f"  - {site.relative_path}:{site.line_number} | "
                    f"expression_verdict={site.expression_verdict} {_MSG_ASSIGNMENT_LINE_REDACTED}"
                )
        else:
            lines.append("  (no assignment/setter sites found)")
        return "\n".join(lines)


def _is_contained_in_root(path: Path, resolved_root: Path) -> bool:
    """Return ``True`` when *path*'s resolved real location lies inside *resolved_root*.

    SECURITY (security_review MEDIUM, this unit): :func:`_iter_source_files`
    walks via ``Path.rglob``, which FOLLOWS file symlinks (a symlinked
    DIRECTORY is never descended into, matching ``os.walk``'s own default
    ``followlinks=False`` behaviour, so a symlinked directory pointing
    outside *resolved_root* is not a concern here). Before this fix, a
    committed symlink such as ``src/linked.ts -> /outside/repo/leak.ts``
    was scanned and its content echoed under a repo-relative path, with no
    boundary check at all -- an arbitrary-file-read primitive for anyone
    who could land a symlink in the audited repo.

    Mirrors the same ``resolve()`` + ``is_relative_to()`` containment check
    ``cli._reject_entry_point_outside_repo``-style helpers already apply to
    a different escape surface (spec 4.4's reachability entry points):
    *resolved_root* must already be resolved by the caller ONCE, outside
    the per-candidate loop, so a *root* itself reached through a symlink
    (e.g. a bind mount) is compared consistently against every candidate's
    own resolved location rather than being spuriously treated as if every
    real file under it resolved "outside" an unresolved root.
    """
    return path.resolve().is_relative_to(resolved_root)


def _iter_source_files(repo_root: Path) -> list[Path]:
    """Return every scanned source file under *repo_root*, excluded dirs filtered out.

    doc_review round 5: this enumerates every entry via ``rglob("*")``
    first and filters out excluded-directory names AFTER enumeration
    (``path.relative_to(repo_root).parts[:-1]`` checked against
    :data:`_EXCLUDED_DIR_NAMES`), not `os.walk`-style pruning that skips
    descending into those directories during the walk. A repo whose only
    content lives under an excluded directory therefore still has every
    entry enumerated before being discarded; it is never scanned (its
    content is never read), which is the operator-observable guarantee
    this docstring's summary line describes.

    "Source" here is this audit's own historical scan scope,
    :data:`devbench.source_classification.WRITE_PATH_AUDIT_SCAN_EXTENSIONS`
    (spec 3.5, 4.3, D-3, AC-E2-F6-S1-T1-5) -- this module no longer
    declares its own extension tuple, but the shared module preserves this
    audit's pre-migration 9-extension scan set byte-for-byte rather than
    widening it to the broader
    :data:`devbench.source_classification.SOURCE_EXTENSIONS` reachability
    union: scanning vendored/build artefacts in the six additional
    languages ``SOURCE_EXTENSIONS`` also recognises (``.cjs``, ``.kt``,
    ``.mjs``, ``.php``, ``.swift``, ``.vue``) would produce noise that
    undermines trust in a write-path finding, per this audit's original
    design rationale. Matching stays exact-case, as before this
    migration: :func:`devbench.source_classification.is_write_path_audit_extension`
    does not lowercase ``path.suffix`` first, preserving this audit's
    original case-sensitive matching style byte-for-byte. Broadening this
    scan set for the audit specifically is deferred to spec 4.8, not done
    here.

    A file symlink whose resolved real location escapes *repo_root* is
    excluded via :func:`_is_contained_in_root` (security_review MEDIUM,
    this unit). A file symlink whose target resolves back INSIDE
    *repo_root* is still scanned, and *repo_root* itself reached through a
    symlink still has its real files scanned, since both sides of the
    comparison are resolved consistently.
    """
    resolved_root = repo_root.resolve()
    files: list[Path] = []
    for path in repo_root.rglob("*"):
        if not path.is_file():
            continue
        if not is_write_path_audit_extension(path.suffix):
            continue
        if _EXCLUDED_DIR_NAMES.intersection(path.relative_to(repo_root).parts[:-1]):
            continue
        if not _is_contained_in_root(path, resolved_root):
            continue
        files.append(path)
    return sorted(files)


def _assignment_regex(flag_name: str) -> re.Pattern[str]:
    """Regex matching an assignment- or setter-shaped occurrence of *flag_name*.

    Matches three shapes, each capturing the right-hand-side expression so
    :func:`_classify_match_verdict` can classify from the ASSIGNED VALUE
    (spec 4.8, 321-D03) rather than from the path the assignment happens to
    live in -- the assignment-context rework this function was reworked
    for. Exactly one of the three named groups participates in any given
    match, since the three shapes are mutually exclusive alternatives.

    - ``<flag> = value`` / ``<flag>: Type = value`` / ``obj.<flag> =
      value`` (via the leading ``\\b`` word boundary). Deliberately
      excludes comparisons (``==``, ``!=``, ``>=``, ``<=``) via the
      negative lookahead after the ``=``. Captures the value (up to the
      end of the line/statement) as group ``assignment_rhs``.
    - ``<flag>: <literal>`` object-literal key/value shorthand (JS/TS/
      Python dict), e.g. ``isPremiumEligible: false,`` -- distinguished
      from an interface/type field declaration (``isPremiumEligible:
      boolean;``) by requiring the value to be a literal (bool/null/
      number/string), not a bare identifier. Captures the literal as
      group ``object_literal_value``.
    - ``set<flag>(`` / ``set_<flag>(`` setter calls. Captures the call's
      argument text (up to the closing paren) as group ``setter_arg``.

    SECURITY (security_review HIGH ReDoS, this unit, two rounds): the
    optional type-annotation group in the ``assignment`` alternative,
    ``(?::\\s*[\\w\\[\\].,<> ]+)?``, overlaps with the ``\\s*`` that
    follows it before ``=`` -- both can consume the same run of
    whitespace/word characters. Appending ``(?P<assignment_rhs>[^;]+)``
    means a line whose ``=`` ends the line (nothing for that group to
    capture) makes the whole alternative fail, forcing the engine to
    backtrack through every split point of that ambiguity before giving
    up.

    Round 1 made the annotation group itself ATOMIC
    (``(?>:\\s*[\\w\\[\\].,<> ]+)?``), which closes the ambiguity for
    lines that carry a colon (O(n^3) in the run between the flag name
    and ``=``; measured: n=2000 4.24s, n=4000 33.57s; a 3,019-byte
    crafted file took 14.23s end to end through
    ``cmd_check_write_path``). But that atomic boundary only encloses
    the colon branch: when a line has NO colon, the optional group
    cannot participate at all, and ``\\b{flag}\\b\\s*`` sits directly
    against the trailing ``\\s*=`` -- an unguarded adjacent-``\\s*``
    ambiguity, still reachable through the same
    ``(?P<assignment_rhs>[^;]+)`` group (O(n^2); measured: n=8000
    0.10s, n=16000 0.40s, n=32000 1.59s, n=64000 6.36s; a 64,019-byte
    crafted file took 6.42s end to end through ``audit_write_path``).

    Round 2 widens the atomic boundary to enclose the ENTIRE optional
    run -- the optional colon-annotation group AND the whitespace that
    follows it -- as a single atomic unit:
    ``(?>\\s*(?::\\s*[\\w\\[\\].,<> ]+)?\\s*)``. The engine now commits
    to that whole run on the way in and never re-splits it on
    backtrack, on either branch, which restores linear-time matching
    (measured: a 128 KB single-line file went from 26.671s to 0.003s
    end to end through ``audit_write_path``; 262,144 spaces resolves in
    0.005s) without changing which text the group matches on any
    realistic input. Atomic groups are supported by Python's ``re``
    module from 3.11; this project requires >=3.12
    (``pyproject.toml``), so ``(?>...)`` is used directly.

    SECURITY (security_review HIGH ReDoS, this unit, round 5): the SAME
    defect class recurred in the ``setter`` alternative. Its pre-image
    had no required element after the opening paren
    (``rf"\\bset[_-]?{escaped}\\s*\\("``), so it matched immediately and
    the ambiguity below was never reachable. This unit's own diff
    appends ``\\s*(?P<setter_arg>[^)]*)\\)``, which requires a closing
    paren. The added ``\\s*`` and ``[^)]*`` are adjacent variable-width
    regions that both match whitespace, so an unclosed call (no ``)``
    anywhere after the opening paren) makes the whole alternative fail,
    forcing the engine to backtrack through every split point of that
    overlap before giving up (O(n^2); measured isolated: n=4000
    0.0587s, n=8000 0.2343s, n=16000 0.9361s, n=32000 3.7438s, n=64000
    15.0750s; end to end through ``audit_write_path``: 16 KB 0.951s,
    64 KB 15.204s, 128 KB 61.151s, 256 KB 244.036s; a benign 322 KB
    ``.ts`` control file resolves in 0.002s). The fix wraps the leading
    whitespace inside the call in the same atomic treatment:
    ``\\((?>\\s*)(?P<setter_arg>[^)]*)\\)`` (measured: 128 KB attack
    from 60.30s to 0.0020s, zero divergences over 210,013 differential
    cases against the pre-fix pattern).

    SECURITY (security_review MEDIUM ReDoS, this unit, round 5): the
    ``object_literal`` alternative's trailing ``\\s*[,;]?\\s*$`` carries
    the identical overlap and is byte-identical to the pre-image -- a
    pre-existing defect, not a regression introduced by this unit's
    diff (this unit's rewrite of the assignment alternative actually
    improves this input's measured time, since the previous cubic
    assignment backtracking was masking it). Fixed here because it lives
    in the function this unit rewrote: measured end to end 0.278s at
    16 KB, 4.508s at 64 KB, 18.185s at 128 KB (exponent ~1.98). The fix
    wraps the whole suffix atomically: ``(?>\\s*[,;]?\\s*)$`` (measured:
    67.14s to 0.0144s, zero divergences).

    SECURITY (security_review HIGH ReDoS, this unit, round 6) -- METHOD
    ERROR, not just another instance: rounds 1/2/5 above enumerated this
    defect class by PATTERN ("is this alternative's shape ambiguous?"),
    found and closed the ambiguity, and reported the sweep complete --
    three times, each wrong. The class is actually
    (ambiguous-or-unbounded region) times (NUMBER OF START OFFSETS a
    ``re.search`` can retry within one line), and a per-pattern sweep
    only ever asked the first half of that question. Enumerating by
    DRIVER instead, for every alternative:

    - Driver A (long tail): does ONE start offset's ambiguous region
      cost O(L)? Rounds 1/2/5 each closed exactly one Driver A instance,
      with an atomic group around the ambiguous region.
    - Driver B (many offsets): how many start offsets can the
      alternative's prefix match in one line, and what does EACH cost?
      m offsets times O(L) each is O(L^2) even when every individual
      region is Driver-A-safe.

    Measured Driver B directly (many stacked/repeated occurrences on one
    line, not a single occurrence with a long tail) against all three
    alternatives:

    - ``setter``: VULNERABLE. Round 5's ``(?>\\s*)`` bounds only the
      LEADING WHITESPACE inside one call; it does nothing to
      ``[^)]*``, which still scans to end of line at EVERY start offset
      where the setter prefix (``\\bset[_-]?{flag}\\s*\\(``) occurs. An
      unclosed line with ``k`` repeated setter prefixes and no ``)``
      anywhere gives ``k`` start offsets that each cost O(remaining
      length) before failing: O(L^2) overall. Measured isolated:
      k=1000 0.076s, k=2000 0.305s, k=4000 1.219s, k=8000 4.857s,
      k=16000 19.343s, k=32000 75.389s (~4x per doubling, exponent
      ~2.0) -- the driver this unit's own round 5 fix missed.
    - ``assignment``: SAFE. 25 stacked no-rhs blocks (each individually
      Driver-A-shaped, the round-1/2 no-match case) measured 0.0001s-
      0.0007s across padding 200/400/800 (110,500 to 20,500 bytes) --
      linear. The reason: once the atomic annotation/whitespace group
      commits, a failing occurrence costs only O(its own padding), not
      O(padding^2), so ``k`` occurrences cost O(k * padding) = O(L),
      not O(L^2). (A hand-reverted, non-atomic copy of this pattern
      DOES reproduce quadratic-per-occurrence growth on the identical
      input -- 0.130s/0.936s/7.064s at padding 200/400/800 -- confirming
      the atomic group, not the input shape, is what keeps this safe.)
    - ``object_literal``: SAFE. 25 stacked anchor-defeating blocks (the
      round-5 no-match case) measured 0.0009s-0.0035s across padding
      2000/4000/8000 (50,650 to 200,650 bytes) -- linear, for the same
      reason as ``assignment``. (A hand-reverted, non-atomic copy
      reproduces quadratic-per-occurrence growth on the identical
      input: 0.417s/1.668s/6.634s at padding 2000/4000/8000.)

    The fix for ``setter`` bounds the argument capture itself rather
    than adding another atomic group (an atomic group alone would still
    let ``[^)]*`` scan an unbounded distance at each of the ``k`` failing
    offsets before its enclosing group could decide to give up):
    ``[^)\\n]{0,512}``, the same bounded-quantifier treatment
    ``_TRAILING_TYPE_ASSERTION_RE`` already uses. 512 is chosen to
    comfortably exceed any realistic setter argument (an identifier
    chain, a call expression, a small literal) while capping the
    per-offset worst case to a small constant: measured post-fix, the
    same many-offset unclosed-call attack runs in 0.011s-0.766s across
    k=2000-128000 (linear; compare pre-fix k=2000 0.305s already
    trending toward the k=8000 4.857s / k=16000 19.343s pre-fix values
    above).

    Operator-visible effect of the bound: a setter argument LONGER than
    512 characters no longer matches the setter alternative at all
    (measured: a 613-character argument with a genuine closing paren,
    which the pre-fix pattern matched and captured in full, no longer
    matches post-fix). This is the CONSERVATIVE direction, not a new
    fail-open risk: the line still counts as a mention
    (``if flag_name not in line``), but produces no
    :class:`FlagAssignmentSite`, so :func:`_classify` falls through to
    :data:`VERDICT_NO_WRITE_PATH` when it is the flag's only site --
    a BLOCKING finding, ``status="fail"``, exit 1 via
    :func:`devbench.cli.cmd_check_write_path` -- rather than ever
    resolving to :data:`VERDICT_LIVE` on unexamined text. This mirrors
    every other unresolved-shape case this module already treats as
    "report, do not guess `live`" (module docstring). A setter argument
    this long is not a realistic pattern this audit needs to resolve
    precisely; failing closed on it costs, at worst, the same
    confirmation round-trip every other conservative verdict already
    costs.

    Every alternative in this function is now guarded against BOTH
    drivers: an atomic group around each ambiguous variable-width
    region (Driver A, rounds 1/2/5) plus, for ``setter``, a bounded
    quantifier closing the many-start-offsets amplification a bare
    atomic group does not address (Driver B, round 6).

    CORRECTION (security_review, this unit, round 6): the module's
    ``_PLACEHOLDER_SEAM_RE`` was previously described here as
    structurally incapable of this backtracking shape. That conclusion
    (it never causes a slow scan in this codebase) is right, but the
    reasoning was wrong: in isolation, against an adversarial synthetic
    string, the pattern itself DOES exhibit Driver B amplification
    (measured: 20.756s against a crafted 64 KB string with many
    ``mock``/``placeholder``/``stub``/``fake`` prefixes and no closing
    keyword). What actually bounds it is its INPUT, not its shape:
    :func:`find_placeholder_seam` applies it only to a real filesystem
    relative path (``relative_posix``), never to an arbitrary or
    attacker-controlled string, and a path's length is bounded by the
    filesystem (measured: 0.000019s for a 255-byte path component,
    0.000308s for a realistic 16-component ~4 KB path). No production
    code path feeds this pattern anything else, so it is left unchanged
    (byte-identical to HEAD; security_review raised no finding against
    the pattern itself, only against this docstring's prior reasoning).
    """
    escaped = re.escape(flag_name)
    assignment = rf"\b{escaped}\b(?>\s*(?::\s*[\w\[\].,<> ]+)?\s*)=\s*(?!=)(?P<assignment_rhs>[^;]+)"
    literal = r"(true|false|null|undefined|none|-?\d+(\.\d+)?|'[^']*'|\"[^\"]*\")"
    object_literal = rf"\b{escaped}\b\s*:\s*(?P<object_literal_value>{literal})(?>\s*[,;]?\s*)$"
    setter = rf"\bset[_-]?{escaped}\s*\((?>\s*)(?P<setter_arg>[^)\n]{{0,512}})\)"
    return re.compile(f"(?:{assignment})|(?:{object_literal})|(?:{setter})", re.IGNORECASE)


# Runtime-derived value signals consulted by :func:`_classify_rhs_expression`
# (spec 4.8): attribute/subscript access on a request/action/param-shaped
# identifier is the syntactic evidence that "this value comes from outside
# this assignment," covering the spec's named live shapes -- a function
# parameter, a request object, a store action payload -- other than the
# setter-argument shape, which :func:`_classify_rhs_expression` handles by
# call site rather than by keyword (any non-literal setter argument counts,
# regardless of the identifier it names).
_RUNTIME_SOURCE_ACCESS_RE = re.compile(
    r"\b(action|payload|request|req|event|args|kwargs|params|context|ctx)\b\s*[.\[]",
    re.IGNORECASE,
)

# A bare literal right-hand side (spec 4.8: "literal-only assignments ...
# classify default"). Reuses the exact literal vocabulary
# `_assignment_regex`'s object-literal shorthand shape already restricts
# matches to (bool/null/number/string), now applied to the captured
# assignment/setter-argument expression too, anchored to the FULL
# (stripped) expression so a literal embedded inside a larger call
# (`models.BooleanField(default=False)`) does not match here -- that shape
# is handled by `_DEFAULT_KEYWORD_ARG_RE` below instead.
_LITERAL_VALUE_RE = re.compile(
    r"^(true|false|null|undefined|none|-?\d+(\.\d+)?|'[^']*'|\"[^\"]*\")\s*$",
    re.IGNORECASE,
)

# A field/constructor keyword-default argument, e.g. Django's
# ``models.BooleanField(default=False)`` (321-D28): the outer call is not
# itself a bare literal (`_LITERAL_VALUE_RE` does not match it), but the
# value it hard-codes into the field definition is, so this still
# classifies `default` rather than falling through to the runtime-source
# check below (which would otherwise misread the `models.BooleanField(`
# attribute access as a live signal).
#
# Precedence (code_review round 1, W2): `_classify_rhs_expression` checks
# this BEFORE the runtime-source check, unconditionally -- not only for
# Django's `models.BooleanField(...)` shape. This is deliberate, not
# Django-only: `resolve(request.user, default=False)` also matches here
# and classifies `default` despite the co-located `request.user`
# runtime-source token, because a literal `default=` keyword argument is
# itself direct written evidence the value at this call site is
# hardcoded, which the module's conservative design (see module
# docstring: never guess `live` on an ambiguous case) treats as the
# stronger, earlier signal.
_DEFAULT_KEYWORD_ARG_RE = re.compile(
    r"\bdefault\s*[:=]\s*(true|false|null|undefined|none|-?\d+(\.\d+)?|'[^']*'|\"[^\"]*\")",
    re.IGNORECASE,
)

# A trailing TypeScript type assertion (``as const``, ``as boolean``, or a
# `satisfies <Type>` assertion): the assigned value is still the bare
# literal to its left, so this suffix is stripped before `_LITERAL_VALUE_RE`
# is checked, exactly like a trailing line/block comment.
#
# SECURITY (security_review MEDIUM ReDoS, this unit): the retired
# `_AS_CONST_SUFFIX_RE = re.compile(r"\s+as\s+const\s*$")` was QUADRATIC on
# a mid-expression whitespace run: an UNBOUNDED `\s+` anchored only at the
# END (`$`), applied via `.sub()`, forces the engine to retry the match at
# EVERY offset within a long whitespace run, and at each offset `\s+`
# itself backtracks over the remaining run length before failing --
# O(run_length^2) overall (measured: 8k=0.104s, 16k=0.400s, 32k=1.592s,
# 64k=6.360s, a clean 4x per doubling; a single 1.53 MB crafted `.ts` file
# drove `audit_write_path` to 40.19s). Bounding EVERY quantifier below
# (`{1,32}`/`{1,64}`) caps the worst-case backtracking at each of the O(n)
# start offsets `.sub()` tries to a small constant, making the whole scan
# O(n) again regardless of how long a whitespace run in the input is.
_TRAILING_TYPE_ASSERTION_RE = re.compile(r"[ \t]{1,32}(?:as|satisfies)[ \t]{1,32}[A-Za-z_][\w.\[\]<>]{0,64}\s*$")


@dataclass
class _QuoteScanState:
    """Tracks single-/double-quote nesting for one forward character scan.

    Shared by :func:`_strip_trailing_line_comment` and
    :func:`_strip_trailing_block_comment` (code_review DRY warn, this
    unit: the two functions carried an identical 32-of-36-body-line quote
    state machine, similarity 0.831, differing only in their terminal
    dispatch). Both need the SAME quote tracking so a comment marker
    embedded inside a string literal's content (a URL's ``//``, or a
    string containing ``/*``) is never mistaken for a real comment start.

    :meth:`consume` is called once per index of the forward scan; the
    caller advances its own index by the returned count and only inspects
    the character itself when the return value is ``0`` (the position is
    outside any quoted run).
    """

    in_single_quote: bool = False
    in_double_quote: bool = False

    def consume(self, expression: str, index: int) -> int:
        """Return how many characters at *index* belong to quote handling.

        Returns ``2`` for a backslash escape inside a quote, ``1`` when
        *index* opens, continues, or closes a quoted run, and ``0`` when
        *index* is outside any quoted run (the caller inspects
        ``expression[index]`` itself in that case).

        Single- and double-quote state is handled by ONE shared branch
        below (``self.in_single_quote or self.in_double_quote``), keyed by
        whichever quote character is currently active, rather than two
        near-identical branches -- this keeps the function's return-point
        count low without losing the distinction between the two quote
        kinds (a `"` inside a single-quoted run, or a `'` inside a
        double-quoted one, is ordinary content, not a closing marker).
        """
        char = expression[index]
        if self.in_single_quote or self.in_double_quote:
            active_quote_char = "'" if self.in_single_quote else '"'
            if char == "\\":
                return 2
            if char == active_quote_char:
                self.in_single_quote = False
                self.in_double_quote = False
            return 1
        if char == "'":
            self.in_single_quote = True
            return 1
        if char == '"':
            self.in_double_quote = True
            return 1
        return 0


def _strip_trailing_line_comment(expression: str) -> str:
    """Truncate *expression* at a trailing ``//`` or ``#`` line comment.

    Quote-aware via :class:`_QuoteScanState`: a comment marker is only
    recognised OUTSIDE a single- or double-quoted string, so a string
    literal whose CONTENT happens to contain ``//`` (a URL, e.g.
    ``"http://example.com"``) or ``#`` is never truncated mid-string -- a
    naive ``str.split`` on the first marker would turn
    ``isFlag = "http://example.com"`` into the unterminated
    ``isFlag = "http:``, which no longer matches any literal pattern and
    silently falls through to the path-vocabulary tiebreak (321-D03).

    Without this, a literal right-hand side spelled with a trailing
    comment -- the most common real-world spelling of a placeholder flag
    awaiting wiring, e.g. ``false // TODO: wire to entitlements API`` --
    is not recognised as a literal by `_LITERAL_VALUE_RE` (anchored to the
    end of the RAW captured text) and falls through to
    `_classify_path_tiebreak`, reproducing 321-D03 for exactly that
    spelling.
    """
    state = _QuoteScanState()
    index = 0
    length = len(expression)
    while index < length:
        consumed = state.consume(expression, index)
        if consumed:
            index += consumed
            continue
        char = expression[index]
        if char == "#":
            return expression[:index]
        if char == "/" and index + 1 < length and expression[index + 1] == "/":
            return expression[:index]
        index += 1
    return expression


def _strip_trailing_block_comment(expression: str) -> str:
    """Truncate *expression* at a trailing, quote-aware ``/* ... */`` block comment.

    Quote-aware like :func:`_strip_trailing_line_comment` (both share
    :class:`_QuoteScanState`): a `/*` inside a quoted string is never
    mistaken for a comment start. Only a block comment that runs to the
    END of the expression (nothing but whitespace after its closing
    ``*/``) is stripped -- a ``/* note */`` embedded mid-expression is
    left alone, since this audit does not attempt to parse the
    surrounding code and removing an interior comment could change what
    the remaining text means.

    Widens literal recognition (security_review HIGH, prior round): a
    hardcoded literal spelled with a trailing block comment, e.g.
    ``false /* TODO: wire */``, previously fell through to
    `_classify_path_tiebreak` unrecognised, exactly the 321-D03 shape a
    trailing line comment already had to be stripped for.

    SECURITY (security_review MEDIUM ReDoS, this unit): the previous
    implementation re-checked "is everything after this comment's `*/`
    pure whitespace" with a FRESH `expression[end + 2 :].strip() == ""`
    for EVERY block comment encountered -- an O(remaining-length) slice
    and scan repeated once per comment, giving O(n^2) total work over n
    block comments in one expression (measured: 120k=0.041s, 960k=6.806s,
    1.92 MB=22.015s isolated; 1.37 MB=27.209s end to end through
    `audit_write_path` on one crafted file -- the same DoS magnitude as
    the `_AS_CONST_SUFFIX_RE` ReDoS this unit's earlier round fixed).
    `trailing_whitespace_boundary` below computes `len(expression.rstrip())`
    ONCE, up front (a single O(n) pass), and every candidate comment's
    "does only whitespace follow?" check becomes an O(1) index comparison
    against that fixed boundary instead: everything from
    `trailing_whitespace_boundary` to the end of `expression` is, by
    construction, pure trailing whitespace (or empty), and
    `expression[trailing_whitespace_boundary - 1]` (if any) is
    non-whitespace -- so a comment's closing `*/` at or after that
    boundary is followed by whitespace only, and one strictly before it
    is followed by more than whitespace. This keeps the whole function
    O(n) regardless of how many block comments the expression contains.
    """
    trailing_whitespace_boundary = len(expression.rstrip())
    state = _QuoteScanState()
    index = 0
    length = len(expression)
    while index < length:
        consumed = state.consume(expression, index)
        if consumed:
            index += consumed
            continue
        char = expression[index]
        if char == "/" and index + 1 < length and expression[index + 1] == "*":
            comment_start = index
            end = expression.find("*/", index + 2)
            if end == -1:
                return expression
            if end + 2 >= trailing_whitespace_boundary:
                return expression[:comment_start]
            index = end + 2
            continue
        index += 1
    return expression


def _strip_wrapping_parens(expression: str) -> str:
    """Strip one layer of parentheses that wrap the ENTIRE *expression*, if present.

    Widens literal recognition (security_review HIGH, this unit): a
    hardcoded literal spelled ``(false)`` previously did not match
    `_LITERAL_VALUE_RE` (anchored to the raw text) and fell through to the
    path-vocabulary tiebreak.

    A single forward scan tracks paren depth; the outer parens are stripped
    ONLY when the '(' at index 0 is the match for the ')' at the last
    index -- i.e. depth never returns to zero before the final character.
    ``(a)+(b)`` is left untouched: its first '(' closes before the
    expression ends, so stripping it would change the expression's
    meaning rather than merely unwrap it.
    """
    if not (expression.startswith("(") and expression.endswith(")")):
        return expression
    depth = 0
    for index, char in enumerate(expression):
        if char == "(":
            depth += 1
        elif char == ")":
            depth -= 1
            if depth == 0 and index != len(expression) - 1:
                return expression
    return expression[1:-1].strip()


def _strip_leading_negation(expression: str) -> str:
    """Strip any number of leading ``!`` negation operators from *expression*.

    Widens literal recognition (security_review HIGH, prior round): a
    hardcoded literal spelled ``!!false`` (or ``!false``) is still a
    hardcoded literal -- this classifier never evaluates boolean truth
    value, only whether the assigned value is derived from runtime input,
    so the number of leading negations is irrelevant to the verdict.

    SECURITY (security_review MEDIUM ReDoS, this unit): the previous
    implementation was `while stripped.startswith("!"): stripped =
    stripped[1:]`, which performs a fresh O(k) string COPY on every
    iteration for a run of k leading `!` characters -- O(k^2) total
    (measured: 40k=0.0141s, 640k=3.1685s, a clean ~4x per doubling;
    0.76 MB end to end through `audit_write_path` = 5.385s). A single
    forward INDEX scan locates the end of the `!` run in one O(k) pass,
    and the run is removed with exactly ONE slice, keeping the whole
    function O(k).
    """
    index = 0
    length = len(expression)
    while index < length and expression[index] == "!":
        index += 1
    return expression[index:].strip()


# Bounded so a pathological input cannot drive this into unbounded work: a
# hardcoded literal spelled with any REALISTIC combination of the six
# normalisations _normalize_rhs_expression applies below (a trailing line
# comment, a trailing block comment, a trailing type assertion, a trailing
# comma, wrapping parens, leading negation) converges in well under this
# many passes; a fixed, small ceiling keeps :func:`_normalize_rhs_expression`
# itself linear in the input length regardless of how many passes run.
_MAX_NORMALIZATION_PASSES = 4


def _normalize_rhs_expression(rhs_expression: str) -> str:
    """Strip literal-obscuring noise before classification (321-D03; security_review HIGH, this unit).

    Applied once, up front, to the already-`.strip()`-ed expression so
    every downstream check (`_LITERAL_VALUE_RE`, `_DEFAULT_KEYWORD_ARG_RE`,
    `_RUNTIME_SOURCE_ACCESS_RE`) sees the same normalised text. Runs a
    fixed, bounded number of passes (:data:`_MAX_NORMALIZATION_PASSES`) of
    every strip below, in order, so a combination like ``(!!false),`` (a
    negated literal, wrapped in parens, with a trailing comma) converges
    to the bare literal even though no single pass handles every layer:

    - a trailing ``//``/``#`` line comment (:func:`_strip_trailing_line_comment`)
    - a trailing ``/* ... */`` block comment (:func:`_strip_trailing_block_comment`)
    - a trailing ``as <Type>``/``satisfies <Type>`` assertion
      (:data:`_TRAILING_TYPE_ASSERTION_RE`)
    - a single trailing comma
    - one layer of wrapping parentheses (:func:`_strip_wrapping_parens`)
    - leading ``!``/``!!`` negation (:func:`_strip_leading_negation`)

    Each pass is itself linear in the input length (no unbounded
    backtracking survives in any of the six steps -- see
    :data:`_TRAILING_TYPE_ASSERTION_RE`'s own comment for the ReDoS this
    replaced), so the whole function stays O(n) in the expression length.
    """
    value = rhs_expression.strip()
    for _ in range(_MAX_NORMALIZATION_PASSES):
        next_value = _strip_trailing_line_comment(value)
        next_value = _strip_trailing_block_comment(next_value)
        next_value = _TRAILING_TYPE_ASSERTION_RE.sub("", next_value).strip()
        if next_value.endswith(","):
            next_value = next_value[:-1].strip()
        next_value = _strip_wrapping_parens(next_value.strip())
        next_value = _strip_leading_negation(next_value.strip())
        if next_value == value:
            return next_value
        value = next_value
    return value


def _classify_rhs_expression(rhs_expression: str, *, is_setter_argument: bool) -> str:
    """Classify one assignment's right-hand-side expression (spec 4.8, 321-D03).

    This is the core of the assignment-context rework: the verdict is
    decided from the ASSIGNED VALUE, not from the file's path -- a literal
    default hardcoded inside a `store`/`slice`-named directory (321-D03's
    flagship false-`live`) classifies `default` here exactly as it would
    inside a `constants`-named one.

    Args:
        rhs_expression: The captured right-hand-side text: the assignment
            operator's value, the object-literal shorthand's literal, or a
            setter call's argument text. Normalised via
            :func:`_normalize_rhs_expression` before any check below runs,
            so a trailing line comment or TypeScript `as const` suffix
            never masks a literal, keyword-default argument or
            runtime-source access underneath it.
        is_setter_argument: ``True`` when *rhs_expression* is a setter
            call's argument. A setter's argument is one of the spec's
            named positive-live shapes (function parameter, request
            object, store action payload, setter argument): ANY
            non-literal argument passed to a `set<Flag>(...)` call counts
            as live, even when the argument expression itself carries no
            recognised runtime-source token, because invoking the setter
            is itself the runtime write.

    Returns:
        :data:`VERDICT_LIVE` when the expression is evidently
        runtime-derived, :data:`VERDICT_DEFAULT` when it is a bare literal
        (or a literal keyword-default argument), and
        :data:`VERDICT_INDETERMINATE` when neither can be established
        (spec 4.8: an unresolved shape is reported, never guessed at).
    """
    value = _normalize_rhs_expression(rhs_expression.strip())
    if not value:
        return VERDICT_INDETERMINATE
    if _LITERAL_VALUE_RE.match(value):
        return VERDICT_DEFAULT
    if _DEFAULT_KEYWORD_ARG_RE.search(value):
        return VERDICT_DEFAULT
    if is_setter_argument:
        return VERDICT_LIVE
    if _RUNTIME_SOURCE_ACCESS_RE.search(value):
        return VERDICT_LIVE
    return VERDICT_INDETERMINATE


def _is_unbalanced_setter_argument_capture(setter_arg: str) -> bool:
    """Return True when *setter_arg* is a truncated setter-argument capture.

    SECURITY (security_review MEDIUM fail-open, this unit): `_assignment_regex`'s
    setter alternative captures the call's argument text with
    ``(?P<setter_arg>[^)\n]{0,512})``, which stops at the FIRST closing
    parenthesis. Two independent shapes truncate that capture into a
    fragment that no longer represents the true argument text, and both
    are checked here:

    1. An unmatched opening parenthesis. A parenthesised or call-wrapped
       argument -- ``set_is_premium_eligible((False))`` or
       ``set_is_premium_eligible(bool(False))`` -- is captured as a
       truncated fragment (``(False``, ``bool(False``) that still carries
       an unclosed ``(``. `_strip_wrapping_parens` correctly declines to
       unwrap a fragment that does not itself END in ``)``, so
       `_LITERAL_VALUE_RE` never matches it, and
       `_classify_rhs_expression`'s setter-argument branch would
       otherwise return :data:`VERDICT_LIVE` unconditionally on this
       corrupted text -- a false `live` for what may be a hardcoded
       literal, the exact fail-open direction the module docstring says
       the classifier must never guess in. Detected by an unequal count
       of `(` and `)` in the fragment; because the capturing class
       excludes `)` entirely, a truncated fragment can never contain a
       `)` character, so this reduces to "any `(` present at all".

    2. M-1 (security_review MEDIUM, this unit): a `)` inside a QUOTED
       setter argument. ``set_isPremiumEligible("a)b")`` captures only
       ``"a`` -- balanced on parens (case 1 above does not catch it) and
       not a complete literal, so `_LITERAL_VALUE_RE` does not match it
       either, and the setter-argument branch would again return
       :data:`VERDICT_LIVE` unconditionally on a hardcoded string
       literal. Detected the same way as case 1: an ODD count of `"` or
       `'` characters in the fragment means the fragment's quote was
       opened but never closed within the captured text, so the
       fragment's true right-hand boundary lies beyond what the regex
       captured. This is a raw character count, not escape-aware -- a
       complete, non-truncated argument containing an odd number of
       embedded quote characters (for example one backslash-escaped
       quote) is over-flagged as truncated too, trading a `default` this
       helper could in principle recover for the safe `indeterminate` it
       returns instead. That is the same fail-closed trade the
       call-wrapped case below already accepts, never fail-open.

    W4 (code_review, this unit): depth-balancing `_assignment_regex`'s
    setter-parenthesis capture WOULD resolve a plain parenthesised literal
    like ``(False)`` to `default` (`_strip_wrapping_parens` already
    unwraps a properly-balanced ``(False)`` once captured correctly), so
    this is not futile in every case. It remains genuinely futile only
    for a CALL-wrapped argument -- ``bool(False)`` -- since a call
    wrapping a non-literal argument (``bool(request.isEligible)``) is
    indistinguishable from it by shape alone even once the parentheses
    are correctly balanced. Rather than rewriting `_assignment_regex` to
    depth-balance (which the module's own ReDoS history this unit fixed
    twice argues for treating with caution: a naive recursive-descent
    balance scan is an easy place to reintroduce non-linear behaviour),
    this returns a simple O(k) signal instead: when the captured
    fragment's TRUE right-hand boundary demonstrably lies beyond what the
    regex captured (more `(` than `)`, or an odd quote-character count, in
    the fragment), the fragment cannot be reliably classified either way
    -- including the plain-parenthesised case this could otherwise
    resolve -- and the caller reports :data:`VERDICT_INDETERMINATE`
    instead of guessing, consistent with the module's documented
    invariant that an unresolved shape is reported, never guessed at.
    This is strictly better than the fail-open `live` the module
    replaced, and errs closed rather than open, at the cost of
    `set_is_premium_eligible((False))` and
    `set_isPremiumEligible("a)b")` currently reporting `indeterminate`
    (exit 0) rather than the `default` (exit 1) a depth-balanced,
    escape-aware capture could in principle recover for those shapes.
    """
    if setter_arg.count("(") != setter_arg.count(")"):
        return True
    if setter_arg.count('"') % 2 != 0:
        return True
    return setter_arg.count("'") % 2 != 0


def _classify_match_verdict(match: re.Match[str]) -> str:
    """Return the per-site expression verdict for one `_assignment_regex` match.

    Dispatches on which of the three named groups (`setter_arg`,
    `assignment_rhs`, `object_literal_value`) actually participated in
    *match* -- exactly one does, per `_assignment_regex`'s docstring -- to
    :func:`_classify_rhs_expression`. A `setter_arg` capture that
    :func:`_is_unbalanced_setter_argument_capture` flags as truncated is
    classified :data:`VERDICT_INDETERMINATE` directly, without reaching
    :func:`_classify_rhs_expression` at all (security_review MEDIUM
    fail-open, this unit; see that helper's own docstring).

    Raises:
        AssertionError: when NONE of the three named groups is populated.
            `_assignment_regex`'s three shapes are mutually exclusive
            alternatives that each populate exactly one named group
            whenever they match (see its own docstring); a match with none
            set can only happen if that regex invariant is broken by a
            future edit. Fail-fast here (CLAUDE.md: no fallback logic, no
            silent guess) rather than silently returning
            :data:`VERDICT_INDETERMINATE` for a case that should be
            structurally impossible.
    """
    setter_arg = match.group("setter_arg")
    if setter_arg is not None:
        if _is_unbalanced_setter_argument_capture(setter_arg):
            return VERDICT_INDETERMINATE
        return _classify_rhs_expression(setter_arg, is_setter_argument=True)
    assignment_rhs = match.group("assignment_rhs")
    if assignment_rhs is not None:
        return _classify_rhs_expression(assignment_rhs, is_setter_argument=False)
    object_literal_value = match.group("object_literal_value")
    if object_literal_value is not None:
        return _classify_rhs_expression(object_literal_value, is_setter_argument=False)
    raise AssertionError(
        "_assignment_regex matched with none of its three named groups (setter_arg, "
        "assignment_rhs, object_literal_value) populated -- the regex's three "
        "alternatives are mutually exclusive and each populate exactly one named group "
        "when they match; this indicates a regex-definition bug, not a recoverable "
        "classification case."
    )


def audit_write_path(repo_root: Path, flag_name: str) -> WritePathAudit:
    """Audit whether *flag_name* has a live, non-default write path in *repo_root*.

    Args:
        repo_root: Target repo checkout to scan.
        flag_name: The existing flag name a spec clause instructs a new
            field to "follow the pattern of."

    Returns:
        A :class:`WritePathAudit` recording every assignment/setter-shaped
        occurrence found and a conservative verdict.

    Raises:
        FileNotFoundError: when ``repo_root`` does not exist. A typo'd
            repo path is a defect the caller should see immediately, not
            something this helper silently returns an empty result for.
    """
    if not repo_root.exists():
        raise FileNotFoundError(f"repo_root does not exist: {repo_root}. Pass the target repo checkout to audit.")

    assignment_re = _assignment_regex(flag_name)
    sites: list[FlagAssignmentSite] = []
    mention_count = 0

    for path in _iter_source_files(repo_root):
        try:
            text = path.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue
        relative_path = path.relative_to(repo_root).as_posix()
        for line_number, line in enumerate(text.splitlines(), start=1):
            if flag_name not in line:
                continue
            mention_count += 1
            match = assignment_re.search(line)
            if match:
                sites.append(
                    FlagAssignmentSite(
                        relative_path=relative_path,
                        line_number=line_number,
                        line_text=line,
                        expression_verdict=_classify_match_verdict(match),
                    )
                )

    verdict = _classify(sites, mention_count)
    return WritePathAudit(
        flag_name=flag_name,
        verdict=verdict,
        assignment_sites=tuple(sites),
        mention_count=mention_count,
    )


def _classify(sites: list[FlagAssignmentSite], mention_count: int) -> str:
    """Return the write-path verdict for the collected assignment sites (spec 4.8).

    Assignment-context analysis (321-D03) decides first, from each site's
    already-computed :attr:`FlagAssignmentSite.expression_verdict`: any
    site whose expression is runtime-derived makes the whole flag
    :data:`VERDICT_LIVE` (a confirmed write path exists, regardless of
    what other sites hardcode); when every site's expression is a bare
    literal, the flag is :data:`VERDICT_DEFAULT` -- even when a site's
    PATH carries live-sounding vocabulary (the flagship false-`live` this
    rework exists to close, 321-D03). Path vocabulary (`_DEFAULT_SIGNAL_RE`)
    is demoted to a tiebreak, consulted only when expression analysis could
    not resolve the sites to `live` or to all-`default` -- i.e. only for
    genuinely indeterminate shapes (spec 4.8: "path vocabulary remains only
    as a tiebreak"). The tiebreak itself can never produce `live` --
    see :func:`_classify_path_tiebreak`'s own docstring.
    """
    if mention_count == 0:
        return VERDICT_NOT_FOUND
    if not sites:
        return VERDICT_NO_WRITE_PATH

    site_verdicts = [site.expression_verdict for site in sites]
    if VERDICT_LIVE in site_verdicts:
        return VERDICT_LIVE
    if all(verdict == VERDICT_DEFAULT for verdict in site_verdicts):
        return VERDICT_DEFAULT

    return _classify_path_tiebreak(sites)


def _classify_path_tiebreak(sites: list[FlagAssignmentSite]) -> str:
    """Path-vocabulary tiebreak for sites expression analysis left unresolved.

    This is the audit's PRE-rework classifier, demoted (spec 4.8, 321-D03)
    to a tiebreak :func:`_classify` consults only when it could not
    resolve the sites to `live` (no site's expression was runtime-derived)
    or to `default` (not every site's expression was a bare literal) --
    the residual case is at least one genuinely `indeterminate` site
    alongside, at most, some `default` sites.

    **SECURITY (security_review HIGH fail-open, this unit): the `live`
    branch is REMOVED.** This function can no longer return
    :data:`VERDICT_LIVE` under any input. The module's own documented
    invariant (see module docstring) is that the classifier never guesses
    `live` on an ambiguous case -- false negatives ("calling a dead flag
    live") would silently reproduce 321-D03, the exact defect this audit
    exists to catch. The removed branch violated that invariant directly:
    it decided `live` from path vocabulary ALONE whenever expression
    analysis could not resolve a site, with zero confirmed runtime
    evidence (measured: `isPremiumEligible = someUnknownVar;` under a
    `slice`-named path classified `live`), and it flipped a hardcoded
    literal's verdict on a bare directory rename
    (`src/constants/... -> src/services/...`, `default` to `live`, no
    code or value change at all). Both defects are structurally
    impossible now that this function has no path through which
    :data:`VERDICT_LIVE` can be returned.

    **What remains, and why (AC-WP-005 reading, code_review round 1 W1,
    unchanged by this fix):** the `default` branch below still produces a
    BLOCKING verdict (``status="fail"``, exit 1 via
    :func:`cmd_check_write_path`) purely from path vocabulary, with no
    site's own assigned-value expression itself resolved. This is kept
    deliberately: "no code path auto-blocks on an unrecognised shape"
    (AC-WP-005) is read as guaranteeing that the genuinely-unresolved
    FINAL verdict, :data:`VERDICT_INDETERMINATE` -- where neither the
    assigned-value expression NOR the path corroborates a reading either
    way -- never auto-blocks (see the fallback `return
    VERDICT_INDETERMINATE` below, and :func:`cmd_check_write_path`'s own
    ``status="pass"`` mapping for it). Once every site's PATH corroborates
    a `default` reading, the shape is no longer unrecognised in that
    narrower sense: it has been classified, conservatively, by the same
    heuristic direction this module's own docstring documents as the
    audit's original design. This direction is asymmetric with the
    removed `live` branch and is NOT equally risky: resolving an
    unresolved shape to `default` costs, at worst, a confirmation
    round-trip (a real write path gets a false BLOCKING finding, exit 1,
    which a human/agent can override); resolving one to `live` would have
    silently reproduced 321-D03 (a dead flag reported as a confirmed write
    path, exit 0, no further scrutiny) -- the exact fail-open direction
    security_review flagged. Any site with no path signal at all, or with
    live-vocabulary path but no confirmed runtime-derived expression, now
    falls to the closing `return VERDICT_INDETERMINATE` instead.
    """
    default_sites = [s for s in sites if _DEFAULT_SIGNAL_RE.search(s.relative_path)]
    if len(default_sites) == len(sites):
        return VERDICT_DEFAULT

    return VERDICT_INDETERMINATE


def find_placeholder_seam(repo_root: Path) -> str | None:
    """Return the relative path of an existing placeholder/mock permission-provider seam, if any.

    Used by Step 4a of ``spec-to-backlog`` so the mandatory new-flag
    write-path task can name a concrete minimum-viable destination
    (issue AC 3) instead of leaving "wire this to real or placeholder
    data" unspecified. Returns the first match in sorted (deterministic)
    order, or ``None`` when no such seam exists in the checkout.

    Raises:
        FileNotFoundError: when ``repo_root`` does not exist.
    """
    if not repo_root.exists():
        raise FileNotFoundError(f"repo_root does not exist: {repo_root}. Pass the target repo checkout to scan.")

    candidates: list[str] = []
    for path in repo_root.rglob("*"):
        if not path.is_file():
            continue
        relative = path.relative_to(repo_root)
        if _EXCLUDED_DIR_NAMES.intersection(relative.parts[:-1]):
            continue
        relative_posix = relative.as_posix()
        if _PLACEHOLDER_SEAM_RE.search(relative_posix):
            candidates.append(relative_posix)

    return sorted(candidates)[0] if candidates else None


def render_blocking_finding(new_field_name: str, audit: WritePathAudit) -> str:
    """Render the `[BLOCKING_FINDING]` line for a non-live referenced-flag audit.

    The calling skill step (Step 3b) emits this line and requires explicit
    operator acknowledgement before Step 4 proceeds for the spec clause
    that referenced ``audit.flag_name`` -- see module docstring.
    """
    sites = "; ".join(f"{s.relative_path}:{s.line_number}" for s in audit.assignment_sites) or "(none found)"
    return (
        f"[BLOCKING_FINDING] Spec instructs new field '{new_field_name}' to follow the pattern of "
        f"'{audit.flag_name}', but '{audit.flag_name}' has no verified live write-path "
        f"(verdict={audit.verdict}). Assignment/setter sites found: {sites}. Copying this pattern "
        "would propagate the same defect to the new field. Confirm with the operator (spec "
        "amendment, or confirmation that a fix is already planned) before generating tasks that "
        "assume this pattern is sound."
    )
