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
