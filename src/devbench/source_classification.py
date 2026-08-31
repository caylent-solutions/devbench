"""Single source-classification module (spec `integration-reality-gates-hardening.md`
section 4.3, D-3, PM-3).

Before this module existed, "which file extensions are source, which paths
are tests, which filenames are entry points" had two independent answers on
this branch: the CLI's own reachability-evidence classification
(``devbench.cli._is_reachability_candidate`` /
``_is_reachability_test_path``) and the write-path audit helper's scan
vocabulary (``devbench.plugin_helpers.permission_flag_writepath``). Two
implementations of the same question is two different answers every future
gate that reads this vocabulary (reachability's entry-point default set,
spec 4.4; shared-file impact's import scanning, spec 4.6; fixture
consistency's source-literal extraction, spec 4.7; write-path audit's path
tiebreak, spec 4.8) would have had to pick between.

:data:`SOURCE_EXTENSIONS`, :data:`ENTRY_POINT_STEMS`,
:data:`TEST_PATH_MARKERS` and :data:`TEST_FILENAME_MARKERS` are the one place
that answers the reachability-evidence question now, plus the small
predicate functions built on top of them. This extraction is
behaviour-preserving for both migrated consumers (AC-E2-F6-S1-T1-5):

- ``devbench.cli``'s reachability-evidence classification already used
  the full 15-extension union before this migration, so
  :func:`is_source_extension` classifies exactly the same files it
  always did.
- ``devbench.plugin_helpers.permission_flag_writepath``'s write-path
  audit historically scanned a narrower 9-extension set. Rather than
  widen that scan to :data:`SOURCE_EXTENSIONS`, this module keeps the
  audit's own scan scope as a *second* named set,
  :data:`WRITE_PATH_AUDIT_SCAN_EXTENSIONS`, consumed via
  :func:`is_write_path_audit_extension`. One definition site (this
  module, satisfying AC-2/AC-3 and D-3's "single-concept definitions
  live once"), two named scopes -- because the two consumers' historical
  vocabularies were never actually the same concept.
  ``SOURCE_EXTENSIONS`` answers "is this extension source code, for a
  consumer that wants the broadest recognised set"; the narrower
  ``WRITE_PATH_AUDIT_SCAN_EXTENSIONS`` answers the audit-specific
  question "is this one of the 9 extensions the write-path audit has
  always scanned." Broadening (or narrowing) the audit's scan set is
  left to the gate epic that actually needs it (spec 4.8), not this
  extraction.

:func:`is_source_extension` lowercases *suffix* before matching (its
contract, AC-E2-F6-S1-T1-4); ``devbench.cli`` already lowercased its own
suffix before this migration, so calling the predicate changes nothing for
it. :func:`is_write_path_audit_extension` matches *suffix* exact-case
instead, preserving ``permission_flag_writepath``'s pre-migration
case-sensitive scan byte-for-byte -- see that function's own docstring for
why this deliberate case-policy divergence from :func:`is_source_extension`
is a named, tested part of the module contract rather than a raw
membership test left at the call site.

Categories are deliberately disjoint by value shape, never by accident:
extensions are always dotted and lowercase, entry-point stems are always
undotted and lowercase, and path/filename markers are always lowercase
substrings. A stem can never collide with an extension and vice versa.
"""

from __future__ import annotations

import os
import re
from collections.abc import Callable
from pathlib import Path

# ---------------------------------------------------------------------------
# Extension category: which suffixes are source code.
# ---------------------------------------------------------------------------

#: Every file extension (lowercase, leading dot) treated as source code.
#: Union of the historical `devbench.cli` reachability-evidence set and the
#: historical `devbench.plugin_helpers.permission_flag_writepath` scan set
#: -- see module docstring for the non-regression rationale.
SOURCE_EXTENSIONS: frozenset[str] = frozenset(
    {
        ".js",
        ".jsx",
        ".ts",
        ".tsx",
        ".mjs",
        ".cjs",
        ".vue",
        ".py",
        ".go",
        ".rb",
        ".java",
        ".kt",
        ".swift",
        ".cs",
        ".php",
    }
)

#: Label returned by :func:`classify_extension` for a recognised source extension.
EXTENSION_CATEGORY_SOURCE: str = "source"

#: Label returned by :func:`classify_extension` for anything outside
#: :data:`SOURCE_EXTENSIONS`. An unrecognised extension is always
#: "unknown" -- it never silently defaults into "source".
EXTENSION_CATEGORY_UNKNOWN: str = "unknown"


def is_source_extension(suffix: str) -> bool:
    """Return ``True`` when *suffix* (e.g. ``".py"``) is a known source extension.

    Matching is case-insensitive on *suffix*. Any suffix outside
    :data:`SOURCE_EXTENSIONS` -- including the empty string -- returns
    ``False`` rather than defaulting to source.
    """
    return suffix.lower() in SOURCE_EXTENSIONS


def classify_extension(suffix: str) -> str:
    """Return :data:`EXTENSION_CATEGORY_SOURCE` or :data:`EXTENSION_CATEGORY_UNKNOWN` for *suffix*.

    Case-insensitive on *suffix*. Never returns "source" for an extension
    outside :data:`SOURCE_EXTENSIONS` (AC-E2-F6-S1-T1-4).
    """
    return EXTENSION_CATEGORY_SOURCE if is_source_extension(suffix) else EXTENSION_CATEGORY_UNKNOWN


#: Extensions the write-path audit
#: (:mod:`devbench.plugin_helpers.permission_flag_writepath`) scans.
#: Deliberately narrower than :data:`SOURCE_EXTENSIONS` and preserved
#: byte-for-byte from that module's pre-migration local
#: ``_SOURCE_EXTENSIONS`` tuple (AC-E2-F6-S1-T1-5): the audit's original
#: rationale for staying narrow is that scanning vendored/build artefacts
#: in less-common languages produces noise that undermines trust in a
#: write-path finding. Broadening or narrowing this set for the audit is
#: left to the gate epic that actually needs it (spec 4.8), not this
#: extraction.
WRITE_PATH_AUDIT_SCAN_EXTENSIONS: frozenset[str] = frozenset(
    {".py", ".ts", ".tsx", ".js", ".jsx", ".java", ".go", ".rb", ".cs"}
)


def is_write_path_audit_extension(suffix: str) -> bool:
    """Return ``True`` when *suffix* is in the write-path audit's scan set.

    Matching is exact-case (*suffix* is **not** lowercased) -- a
    deliberate divergence from :func:`is_source_extension`'s
    case-insensitive contract, preserving
    :mod:`devbench.plugin_helpers.permission_flag_writepath`'s
    pre-migration exact-case scan byte-for-byte (AC-E2-F6-S1-T1-5). A
    suffix such as ``".PY"`` therefore returns ``True`` for
    :func:`is_source_extension` but ``False`` here; this is intentional
    and pinned by
    ``tests/test_source_classification.py::TestIsWritePathAuditExtension``,
    not a silent per-consumer inconsistency.
    """
    return suffix in WRITE_PATH_AUDIT_SCAN_EXTENSIONS


# ---------------------------------------------------------------------------
# Entry-point category: which filename stems are composition roots.
# ---------------------------------------------------------------------------

#: Filename stems (lowercase, no extension) that are composition-root /
#: package-entry conventions. Nothing is expected to import these by name,
#: so flagging them as unreachable/orphaned would be a guaranteed false
#: positive rather than a useful signal.
ENTRY_POINT_STEMS: frozenset[str] = frozenset({"index", "main", "app", "__init__", "setup", "conftest", "wsgi", "asgi"})


def is_entry_point_stem(stem: str) -> bool:
    """Return ``True`` when *stem* (a filename with its extension stripped) is a
    known composition-root/entry-point name. Matching is case-insensitive."""
    return stem.lower() in ENTRY_POINT_STEMS


# ---------------------------------------------------------------------------
# Test-path category: which paths/filenames are tests, specs, stories, or fixtures.
# ---------------------------------------------------------------------------

#: Directory-segment markers (lowercase, leading and trailing ``/``) that
#: identify a path as test/spec/story/fixture-owned.
TEST_PATH_MARKERS: frozenset[str] = frozenset(
    {
        "/__tests__/",
        "/__mocks__/",
        "/__snapshots__/",
        "/test/",
        "/tests/",
        "/spec/",
        "/specs/",
        "/fixtures/",
        "/mocks/",
        "/.storybook/",
        "/stories/",
    }
)

#: Filename substring markers (lowercase) that identify a file as a
#: test/spec/story file regardless of its containing directory.
TEST_FILENAME_MARKERS: frozenset[str] = frozenset({".test.", ".spec.", ".stories."})


def is_test_path(rel_path: str) -> bool:
    """Return ``True`` when *rel_path* is a test, spec, story, or fixture file.

    Matches (in order) a :data:`TEST_PATH_MARKERS` directory segment, a
    :data:`TEST_FILENAME_MARKERS` filename substring, and finally the
    Python ``test_<name>`` / ``<name>_test`` stem convention. Matching is
    case-insensitive; both ``/`` and ``\\`` path separators are accepted.
    """
    normalized = "/" + rel_path.replace("\\", "/").lower()
    if any(marker in normalized for marker in TEST_PATH_MARKERS):
        return True
    filename = normalized.rsplit("/", 1)[-1]
    if any(marker in filename for marker in TEST_FILENAME_MARKERS):
        return True
    stem = filename.rsplit(".", 1)[0]
    return stem.startswith("test_") or stem.endswith("_test")


# ---------------------------------------------------------------------------
# Import-target category: language-appropriate import/require scanning
# (spec `integration-reality-gates-hardening.md` section 4.6, D-9;
# caylent-solutions/devbench-internal-backlog#13 AC4).
# ---------------------------------------------------------------------------
#
# This is scanning, not parsing: each family's own single-line import/
# require/using/include grammar, matched with a compiled regex, the same
# "cheap candidate surfacing, not a real compiler front-end" posture
# `devbench.cli`'s reachability importer search already documents for its
# own grep-based heuristic. :func:`extract_import_targets` answers only
# "what did this file's text say it imports" -- resolving a raw target
# string (a relative path fragment, a dotted module path, or a bare
# package name) to an actual on-disk file is the caller's job
# (`devbench.cli._derive_shared_file_registry`), never this module's.

_JS_IMPORT_TARGET_RE = re.compile(
    r"""(?:import\s+(?:[^'";]*?\bfrom\s+)?|export\s+[^'";]*?\bfrom\s+|require\s*\(\s*|import\s*\(\s*)['"]([^'"]+)['"]"""
)
_PY_FROM_IMPORT_RE = re.compile(r"^\s*from\s+([.\w]+)\s+import\s+(.+)$", re.MULTILINE)
_PY_IMPORT_RE = re.compile(r"^\s*import\s+([.\w]+(?:\s*,\s*[.\w]+)*)", re.MULTILINE)
_GO_SINGLE_IMPORT_RE = re.compile(r'^\s*import\s+(?:\w+\s+)?"([^"]+)"', re.MULTILINE)
_GO_IMPORT_BLOCK_RE = re.compile(r"import\s*\(([^)]*)\)", re.DOTALL)
_GO_IMPORT_BLOCK_ENTRY_RE = re.compile(r'"([^"]+)"')
_RUBY_IMPORT_TARGET_RE = re.compile(r"""require(?:_relative)?\s*\(?\s*['"]([^'"]+)['"]""")
_JVM_IMPORT_TARGET_RE = re.compile(r"^\s*import\s+(?:static\s+)?([\w.]+(?:\.\*)?)\s*;?", re.MULTILINE)
_SWIFT_IMPORT_TARGET_RE = re.compile(r"^\s*import\s+([\w.]+)", re.MULTILINE)
_CSHARP_IMPORT_TARGET_RE = re.compile(r"^\s*using\s+(?:static\s+)?([\w.]+)\s*;", re.MULTILINE)
_PHP_INCLUDE_TARGET_RE = re.compile(r"""(?:require|include)(?:_once)?\s*\(?\s*['"]([^'"]+)['"]""")
_PHP_USE_TARGET_RE = re.compile(r"^\s*use\s+([\w\\]+)", re.MULTILINE)


def _split_python_import_clause(imported_clause: str) -> list[str]:
    """Split a ``from X import <this clause>`` clause into bare imported names.

    Strips a wrapping ``(...)``, drops ``as <alias>`` aliasing, and skips the
    ``*`` wildcard form, which names no on-disk file any resolver could ever
    match. This function itself accepts a multi-line clause fine (it splits on
    ``,`` after stripping, so embedded newlines are harmless); the actual
    scanning limitation lives one level up, in the caller's
    ``_PY_FROM_IMPORT_RE`` (``re.MULTILINE`` without ``re.DOTALL``), whose
    ``.+$`` group only ever matches up to the FIRST physical line -- nothing is
    "collapsed"; for a multi-line grouped ``from . import (`` clause the regex
    simply never sees anything past the opening ``(``, so this function is
    called with a truncated one-character clause and the real imported names on
    the following lines are silently missed (a documented scanning limitation,
    not a parsing one -- no downstream resolver can act on a target this
    function was never given).
    """
    clause = imported_clause.strip()
    if clause.startswith("(") and clause.endswith(")"):
        clause = clause[1:-1]
    names = []
    for part in clause.split(","):
        name = part.strip().split(" as ")[0].strip()
        if name and name != "*":
            names.append(name)
    return names


def _extract_python_import_targets(text: str) -> list[str]:
    """Return ``from <target> import ...`` and ``import <target>[, <target> ...]`` targets.

    A ``from`` clause whose module path is dots-only (``from . import x``, ``from
    .. import x, y``) names no dotted module of its own -- ``x``/``y`` there are
    the submodule names a real Python resolver looks for directly under that
    package, the same shape ``from .x import y`` already names explicitly via its
    own module path. Both spellings are therefore reduced to one target per
    imported name (``.x``), rather than the dots-only path being emitted alone as
    a target no on-disk file could ever match (spec 4.6, round-1 A3 finding: ``from
    . import target`` and ``from .target import x`` must resolve the same way).
    """
    targets: list[str] = []
    for match in _PY_FROM_IMPORT_RE.finditer(text):
        module_path, imported_clause = match.group(1), match.group(2)
        if module_path and module_path.strip(".") == "":
            for imported_name in _split_python_import_clause(imported_clause):
                targets.append(module_path + imported_name)
        else:
            targets.append(module_path)
    for match in _PY_IMPORT_RE.finditer(text):
        for part in match.group(1).split(","):
            name = part.strip().split(" as ")[0].strip()
            if name:
                targets.append(name)
    return targets


def _extract_js_import_targets(text: str) -> list[str]:
    """Return ``import ... from '<target>'``, bare ``import '<target>'`` and ``require('<target>')`` targets."""
    return [match.group(1) for match in _JS_IMPORT_TARGET_RE.finditer(text)]


def _extract_go_import_targets(text: str) -> list[str]:
    """Return single-line ``import "<target>"`` and every grouped ``import (...)`` block's targets.

    Uses ``finditer`` (round-2 test_review finding), never ``search``: a Go file
    with more than one grouped import block (legal Go, and not unusual after a
    tool like ``goimports`` regroups stdlib vs third-party imports separately)
    has every block's targets extracted, not only the first one a single
    ``search`` call would find.
    """
    targets = [match.group(1) for match in _GO_SINGLE_IMPORT_RE.finditer(text)]
    for block in _GO_IMPORT_BLOCK_RE.finditer(text):
        targets.extend(match.group(1) for match in _GO_IMPORT_BLOCK_ENTRY_RE.finditer(block.group(1)))
    return targets


def _extract_ruby_import_targets(text: str) -> list[str]:
    """Return ``require '<target>'`` / ``require_relative '<target>'`` targets."""
    return [match.group(1) for match in _RUBY_IMPORT_TARGET_RE.finditer(text)]


def _extract_jvm_import_targets(text: str) -> list[str]:
    """Return Java/Kotlin ``import <target>;`` (including ``import static``) targets."""
    return [match.group(1) for match in _JVM_IMPORT_TARGET_RE.finditer(text)]


def _extract_swift_import_targets(text: str) -> list[str]:
    """Return Swift ``import <target>`` targets."""
    return [match.group(1) for match in _SWIFT_IMPORT_TARGET_RE.finditer(text)]


def _extract_csharp_import_targets(text: str) -> list[str]:
    """Return C# ``using <target>;`` (including ``using static``) targets."""
    return [match.group(1) for match in _CSHARP_IMPORT_TARGET_RE.finditer(text)]


def _extract_php_import_targets(text: str) -> list[str]:
    """Return PHP ``require``/``include`` (with ``_once`` variants) and ``use <target>`` targets."""
    targets = [match.group(1) for match in _PHP_INCLUDE_TARGET_RE.finditer(text)]
    targets.extend(match.group(1) for match in _PHP_USE_TARGET_RE.finditer(text))
    return targets


#: The JS/TS family's extensions (spec 4.6, round-2 code_review finding): the
#: SINGLE place this grouping is declared. ``devbench.cli``'s shared-file
#: import-target *resolution* step (as opposed to the *scanning* dispatch
#: below) needs this exact family too, to know which extensions and
#: directory-entry stem (``index``) a JS/TS-family relative import resolves
#: against -- it imports this constant rather than redeclaring its own
#: extension tuple, so adding a new JS/TS extension here (e.g. a future
#: ``.mts``) can never silently drift between the two call sites with no
#: failing test (mirrors the existing :data:`WRITE_PATH_AUDIT_SCAN_EXTENSIONS`
#: precedent: one named scope, every consumer imports it).
JS_TS_FAMILY_EXTENSIONS: tuple[str, ...] = (".js", ".jsx", ".ts", ".tsx", ".mjs", ".cjs")

#: Per-extension import-target extractor registry (spec 4.6 AC-5): every key
#: is a member of :data:`SOURCE_EXTENSIONS` (never a redeclared extension
#: set of its own -- :func:`extract_import_targets` dispatches on
#: :func:`is_source_extension` first, this dict only routes an
#: already-classified suffix to its language family's extractor). ``.vue``
#: is deliberately absent: its imports live inside an embedded ``<script>``
#: block this registry does not parse -- :func:`extract_import_targets`
#: returns ``[]`` for it, the same result an unmapped *known* source
#: extension gets, not an error.
_IMPORT_TARGET_EXTRACTORS: dict[str, Callable[[str], list[str]]] = {
    ".py": _extract_python_import_targets,
    **dict.fromkeys(JS_TS_FAMILY_EXTENSIONS, _extract_js_import_targets),
    ".go": _extract_go_import_targets,
    ".rb": _extract_ruby_import_targets,
    ".java": _extract_jvm_import_targets,
    ".kt": _extract_jvm_import_targets,
    ".swift": _extract_swift_import_targets,
    ".cs": _extract_csharp_import_targets,
    ".php": _extract_php_import_targets,
}


def extract_import_targets(suffix: str, text: str) -> list[str]:
    """Return the raw import/require/using/include target strings *text* contains.

    Dispatches on *suffix* (case-insensitive, via :func:`is_source_extension`) to the
    language family's extractor in :data:`_IMPORT_TARGET_EXTRACTORS` -- the SINGLE
    place per family's import grammar lives (spec 4.6 AC-5): no caller declares a
    second copy of any of these patterns, and this function declares no extension
    tuple of its own, dispatching purely on :data:`SOURCE_EXTENSIONS` membership via
    :func:`is_source_extension`.

    Targets are returned exactly as written in the source -- a relative path
    fragment (``"./shared_module"``), a dotted module/namespace path
    (``"pkg.shared_module"``, ``"com.example.SharedModule"``), or a bare package
    name -- in file order, duplicates included (fan-in counting is the caller's
    job). Resolving a target to an on-disk file is never this function's
    responsibility.

    Returns ``[]``, never raises, for a *suffix* outside :data:`SOURCE_EXTENSIONS`
    (not source at all) and for a recognised source extension with no entry in
    :data:`_IMPORT_TARGET_EXTRACTORS` (currently only ``.vue``) -- a file that
    contributes no import targets simply casts no fan-in vote; this is not a scan
    error. A genuinely unreadable file is a filesystem-level concern only the
    caller can detect (it alone touches disk), never this text-only function's.
    """
    if not is_source_extension(suffix):
        return []
    extractor = _IMPORT_TARGET_EXTRACTORS.get(suffix.lower())
    return extractor(text) if extractor is not None else []


# ---------------------------------------------------------------------------
# Classified-source-file enumeration (spec `integration-reality-gates-hardening.md`
# section 4.7 bullet 4; caylent-solutions/devbench-internal-backlog#17 AC-19,
# E6-F2-S1-T1): the repo-wide walk `fixture_consistency`'s config-gated
# `extract_source_literals` scan mode uses to discover candidate source files.
# ---------------------------------------------------------------------------

#: Directories pruned during :func:`iter_classified_source_files`'s walk --
#: dependency/build/vendor trees no source-literal scan should ever descend
#: into. This is now the SINGLE declaration of this exclusion set (E6-F2-S1-T1
#: code_review Blocking 6, DRY/PM-3-adjacent single-ownership rule): a
#: vendored or build directory is exactly as irrelevant to "does this literal
#: drift from the canonical catalog" as it is to "does this file participate
#: in the import graph" -- the sibling `shared_file_impact` gate's own walk
#: (spec 4.6, `devbench.cli._iter_shared_file_scan_candidates`) delegates
#: enumeration entirely to :func:`iter_classified_source_files`, which reads
#: this same constant, so the two walks' pruning can never silently drift
#: with no failing test. `vendor/` and `third_party/`
#: are included because Go -- a language both walks explicitly support --
#: canonically vendors third-party code under `vendor/`; excluding `.venv/`
#: matters in practice too, not just in principle: an unfiltered walk of this
#: very repo checkout spends the overwhelming majority of its enumerated
#: entries inside `.venv/` alone. This set is CLOSED and finite, not an
#: exhaustive denylist: an unlisted SUBDIRECTORY the walk descends into
#: (e.g. `bower_components/`, `.direnv/`, `target/`) still has its own
#: internals scanned, for both walks.
CLASSIFIED_SOURCE_WALK_EXCLUDED_DIRS: frozenset[str] = frozenset(
    {
        ".git",
        ".venv",
        "venv",
        "node_modules",
        "__pycache__",
        ".tox",
        ".nox",
        ".mypy_cache",
        ".pytest_cache",
        ".ruff_cache",
        ".eggs",
        "site-packages",
        "dist",
        "build",
        "htmlcov",
        "vendor",
        "third_party",
    }
)


def _reraise_walk_error(error: OSError) -> None:
    """``os.walk`` ``onerror`` callback that propagates a directory-listing failure.

    ``os.walk``'s default policy (``onerror=None``) SWALLOWS an ``OSError``
    raised while listing a directory (e.g. permission denied) and simply
    omits that subtree from the walk with no signal to the caller at all --
    a degraded-but-passing shape spec Section 7 bans (E6-F2-S1-T1 code_review
    Blocking 1): a caller enumerating "every classified source file" that
    silently inspects only part of the requested scope must never look
    identical to a caller that inspected the whole thing and found nothing
    of interest. Passing this function as ``os.walk``'s ``onerror`` callback
    makes an unreadable directory abort the walk with the original
    ``OSError`` instead, so :func:`iter_classified_source_files` itself
    never returns a silently-incomplete result; a caller that wants a softer
    outcome (e.g. a ``load_error`` finding naming the unreadable directory,
    as `fixture_consistency`'s `extract_source_literals` mode does) catches
    ``OSError`` around its own call to :func:`iter_classified_source_files`.
    """
    raise error


def _resolves_outside_root(candidate: Path, resolved_root: Path) -> bool:
    """Return ``True`` when *candidate*'s resolved real location does not lie under *resolved_root*.

    SECURITY (security_review round-3 MEDIUM finding): a candidate's repo-relative NAME (its
    path under the walked root) previously determined whether :func:`iter_classified_source_files`
    enumerated it, while a caller reading its content (``Path.read_text``) follows any symlink in
    the path to whatever it actually resolves to -- so a symlink committed inside the repo whose
    TARGET resolves outside the repo checkout was a read primitive for arbitrary filesystem
    content under a path that looked like it belonged to the scanned repo.

    Uses ``os.path.realpath`` (never ``Path.is_file()``/``Path.exists()``, both of which would
    silently treat a dangling symlink as "not found" rather than resolving its target for the
    boundary check): the boundary is evaluated against the resolved TARGET path, whether or not
    that target actually exists on disk, so a dangling symlink is classified the same way a live
    one pointing at the same location would be (see :func:`iter_classified_source_files`'s own
    docstring for why a dangling symlink resolving OUTSIDE the root must still be excluded, and
    one resolving inside it must not be).

    *resolved_root* must already be resolved by the caller (once, outside the per-candidate loop)
    -- comparing an unresolved *candidate* against an unresolved *root* would spuriously treat a
    *root* itself reached through a symlink (a common shape for ``/tmp`` on macOS, or a bind
    mount) as if every real file under it resolved "outside" that unresolved root.
    """
    resolved_candidate = Path(os.path.realpath(candidate))
    return resolved_candidate != resolved_root and resolved_root not in resolved_candidate.parents


def iter_classified_source_files(root: Path) -> list[Path]:
    """Return every file under *root* whose extension :func:`is_source_extension` classifies as source.

    Prunes :data:`CLASSIFIED_SOURCE_WALK_EXCLUDED_DIRS` DURING the walk (via
    in-place ``dirnames`` mutation), so a vendored/dependency/build tree is
    never descended into at all. Dispatches purely on
    :func:`is_source_extension` -- this function declares no extension
    tuple of its own (PM-3, spec 4.3): the single place the "is this
    extension source" answer lives is :data:`SOURCE_EXTENSIONS`.

    SECURITY (security_review round-3 MEDIUM finding): a candidate whose resolved real path
    (:func:`_resolves_outside_root`, via ``os.path.realpath``) falls OUTSIDE the resolved *root*
    is silently excluded from the result -- never read, never even named in the returned list.
    This affects only FILE symlinks; a symlinked DIRECTORY is never descended into at all
    (``os.walk``'s own default ``followlinks=False``, unchanged by this function), so a symlinked
    directory pointing outside *root* was never a concern in the first place. Two shapes are
    DELIBERATELY still included, both pre-existing behaviour this fix does not change:

    - A symlink whose target ALSO resolves inside *root* -- it discloses nothing outside the
      repo checkout, so excluding it would only cost real scan coverage (e.g. an in-repo
      compatibility shim re-exporting a moved module) for no security benefit. This can result in
      the same on-disk content being scanned twice, under two different repo-relative paths; that
      is a scanning-boundary characteristic documented here, never a correctness defect.
    - A DANGLING symlink whose target names a location inside *root* -- unchanged from
      pre-fix behaviour (it is never filtered on ``Path.is_file()``/``Path.exists()``, which
      would silently swallow a broken symlink instead of surfacing it), so attempting to read one
      still reaches the caller's own read call and raises there (mirrors
      ``cli._derive_shared_file_registry``'s documented dangling-symlink non-regression). A
      dangling symlink whose target names a location OUTSIDE *root* is excluded the same way a
      live out-of-root symlink is: the boundary check is evaluated against the resolved target
      path regardless of whether that target exists.

    Results are returned as absolute ``Path`` objects, sorted deterministically
    (directories and filenames are each sorted before being walked/collected),
    so a caller iterating the result gets a stable, reproducible order run to
    run on an unchanged checkout.

    Args:
        root: Absolute path to walk (a repo checkout root, or any
            subdirectory of one).

    Returns:
        A list of absolute ``Path``s to every classified source file found,
        possibly empty when *root* contains no classified source files at
        all (an empty result is not an error at this layer -- a caller that
        needs "zero classified source files" to be a loud, actionable error,
        such as `fixture_consistency`'s `extract_source_literals` mode,
        raises on that condition itself).

    Raises:
        OSError: If a directory under *root* cannot be listed (e.g.
            permission denied) -- see :func:`_reraise_walk_error`. This
            walk never silently skips an unreadable subtree the way
            ``os.walk``'s own default ``onerror`` policy would.
    """
    resolved_root = Path(os.path.realpath(root))
    results: list[Path] = []
    for dirpath, dirnames, filenames in os.walk(root, onerror=_reraise_walk_error):
        dirnames[:] = sorted(d for d in dirnames if d not in CLASSIFIED_SOURCE_WALK_EXCLUDED_DIRS)
        for filename in sorted(filenames):
            candidate = Path(dirpath) / filename
            if not is_source_extension(candidate.suffix):
                continue
            if _resolves_outside_root(candidate, resolved_root):
                continue
            results.append(candidate)
    return results
