"""Tests for `devbench.source_classification` (spec `integration-reality-gates-hardening.md`
section 4.3, D-3, PM-3; AC-E2-F6-S1-T1-1 through -5).

Before this module existed, "which file extensions are source, which paths
are tests, which filenames are entry points" had two independent answers:
the CLI's own reachability-evidence classification
(`devbench.cli._is_reachability_candidate`) and the write-path audit
helper's scan vocabulary (`devbench.plugin_helpers.permission_flag_writepath`).
This module is the single home both consumers import from now. The
green-green witnesses selected from `tests/test_cli.py
::TestReachabilityHelperFunctions` and
`tests/test_plugin_helpers/test_permission_flag_writepath.py` (recorded in
this work unit's TDD Cycle Log GREEN_GREEN_OBSERVED entry) prove that each
consumer's own pre-migration classification behaviour is unchanged:
`cli.py`'s reachability-evidence set was already this module's full
`SOURCE_EXTENSIONS` union, and `permission_flag_writepath.py`'s narrower
write-path audit scan set is preserved byte-for-byte as the separate
`WRITE_PATH_AUDIT_SCAN_EXTENSIONS` below. That is what those witnesses
establish -- not a universal claim that nothing about this module's mere
existence is observable anywhere.

`devbench.source_classification` performs no I/O of its own, and most
tests here drive the module with plain strings -- no scratch git repos or
work-unit fixtures are needed for those. A small number of tests
(`TestWritePathAuditConsumerIntegration` below) instead exercise a
migrated consumer's *live* integration with this module's exported
attributes using `tmp_path`/`monkeypatch`; those necessarily involve real
file-system I/O because the consumer they pin
(`devbench.plugin_helpers.permission_flag_writepath._iter_source_files`)
does. `source_classification.py` is a brand-new module, so -- mirroring
`tests/test_gate_records.py` -- every import of it (and of any consumer
module that in turn imports it) is deferred inside each test body rather
than hoisted to module scope. This keeps the module importable (and every
test collectible) even when the orchestrator's RED gate, or
`green-green-check`'s scoped stash, removes the production file to
reconstruct a "before" state, so a genuinely failing assertion is reported
as a test FAILURE rather than a collection error. This file is
deliberately never used as a `green-green-check` witness source itself
(unlike the two consumer test files above): several tests here, by
design, only pass once the migration has landed, and `default_pytest_runner`
runs pytest at file scope, so any such test would poison the process exit
code check for every witness drawn from whichever file it lived in.
"""

from __future__ import annotations

from pathlib import Path

import pytest

# The historical extension membership of both pre-migration consumers
# (`cli._REACHABILITY_SOURCE_EXTENSIONS` and
# `permission_flag_writepath._SOURCE_EXTENSIONS`), inlined here as the
# behaviour-preservation contract this module's `SOURCE_EXTENSIONS` must
# still satisfy: every extension either consumer classified as "source"
# before the extraction must still classify as "source" after it.
_CLI_REACHABILITY_HISTORICAL_EXTENSIONS = (
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
)
_WRITE_PATH_AUDIT_HISTORICAL_EXTENSIONS = (
    ".py",
    ".ts",
    ".tsx",
    ".js",
    ".jsx",
    ".java",
    ".go",
    ".rb",
    ".cs",
)
_KNOWN_NON_SOURCE_EXTENSIONS = (".md", ".json", ".yml", ".yaml", ".txt", ".exe", ".png")

# The historical directory-marker membership of the deleted
# `cli._REACHABILITY_TEST_PATH_MARKERS` tuple (verified verbatim from the
# git diff of its deletion), inlined here as the behaviour-preservation
# contract this module's `TEST_PATH_MARKERS` must still satisfy exactly:
# a set-equality pin, not a one-way membership check, so it fails on a
# narrowing (a member silently dropped) as well as a widening.
_CLI_REACHABILITY_HISTORICAL_TEST_PATH_MARKERS = (
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
)


@pytest.mark.unit
class TestSourceExtensions:
    def test_is_a_frozenset(self) -> None:
        from devbench.source_classification import SOURCE_EXTENSIONS

        assert isinstance(SOURCE_EXTENSIONS, frozenset)

    def test_every_member_is_lowercase_and_dotted(self) -> None:
        from devbench.source_classification import SOURCE_EXTENSIONS

        for extension in SOURCE_EXTENSIONS:
            assert extension.startswith("."), f"{extension!r} must start with '.'"
            assert extension == extension.lower(), f"{extension!r} must be lowercase"

    @pytest.mark.parametrize("extension", _CLI_REACHABILITY_HISTORICAL_EXTENSIONS)
    def test_preserves_every_historical_reachability_extension(self, extension: str) -> None:
        from devbench.source_classification import SOURCE_EXTENSIONS

        assert extension in SOURCE_EXTENSIONS

    @pytest.mark.parametrize("extension", _WRITE_PATH_AUDIT_HISTORICAL_EXTENSIONS)
    def test_preserves_every_historical_write_path_audit_extension(self, extension: str) -> None:
        from devbench.source_classification import SOURCE_EXTENSIONS

        assert extension in SOURCE_EXTENSIONS

    def test_historical_reachability_extensions_equal_source_extensions_exactly(self) -> None:
        """Drift guard (test_review round-3 TDD_CYCLE_MISSING remediation):
        `_CLI_REACHABILITY_HISTORICAL_EXTENSIONS` above is also imported
        directly by `tests/test_cli.py` (module scope -- safe there
        because this module never imports the production package at its
        own module scope) to parametrize
        `TestReachabilityHelperFunctions::test_is_reachability_candidate_accepts_every_shared_source_extension`
        without a collection-time import of the live constant. This is
        a set-EQUALITY pin, not the one-way membership check above: it
        fails on a narrowing of `SOURCE_EXTENSIONS` (which
        `test_preserves_every_historical_reachability_extension` alone
        would not catch) as well as a widening, so both this test and
        `test_cli.py`'s parametrization stay tied to the live constant.
        Deliberately placed here rather than in `test_cli.py` itself:
        `green-green-check`'s scoped stash deletes this module to
        reconstruct the "before" state, and `default_pytest_runner` runs
        pytest at FILE scope, so a test that legitimately fails before
        the migration would poison the process exit code for every
        witness selected from whichever file it lived in -- this module
        is never used as a green-green witness source, so it is the safe
        home for this assertion."""
        from devbench.source_classification import SOURCE_EXTENSIONS

        assert frozenset(_CLI_REACHABILITY_HISTORICAL_EXTENSIONS) == SOURCE_EXTENSIONS


@pytest.mark.unit
class TestIsSourceExtension:
    @pytest.mark.parametrize("extension", _CLI_REACHABILITY_HISTORICAL_EXTENSIONS)
    def test_known_extension_is_source(self, extension: str) -> None:
        from devbench.source_classification import is_source_extension

        assert is_source_extension(extension) is True

    @pytest.mark.parametrize(
        ("lower", "mixed"),
        [
            (".py", ".PY"),
            (".ts", ".Ts"),
            (".rb", ".RB"),
        ],
    )
    def test_matching_is_case_insensitive_on_the_suffix(self, lower: str, mixed: str) -> None:
        from devbench.source_classification import is_source_extension

        assert is_source_extension(lower) is is_source_extension(mixed) is True

    @pytest.mark.parametrize("extension", _KNOWN_NON_SOURCE_EXTENSIONS)
    def test_unknown_extension_is_not_source(self, extension: str) -> None:
        from devbench.source_classification import is_source_extension

        assert is_source_extension(extension) is False

    def test_empty_suffix_is_not_source(self) -> None:
        from devbench.source_classification import is_source_extension

        assert is_source_extension("") is False


@pytest.mark.unit
class TestClassifyExtension:
    def test_known_extension_classifies_as_source(self) -> None:
        from devbench.source_classification import classify_extension

        assert classify_extension(".py") == "source"

    def test_classification_is_case_insensitive(self) -> None:
        from devbench.source_classification import classify_extension

        assert classify_extension(".PY") == classify_extension(".py") == "source"

    @pytest.mark.parametrize("extension", _KNOWN_NON_SOURCE_EXTENSIONS)
    def test_unknown_extension_classifies_as_unknown_not_source(self, extension: str) -> None:
        """AC-E2-F6-S1-T1-4: an unrecognised extension never silently defaults into source."""
        from devbench.source_classification import classify_extension

        assert classify_extension(extension) == "unknown"


@pytest.mark.unit
class TestWritePathAuditScanExtensions:
    """AC-E2-F6-S1-T1-5: the write-path audit's pre-migration 9-extension
    scan scope is preserved byte-for-byte as its own named set in this
    module, rather than widened to `SOURCE_EXTENSIONS`'s 15-extension
    union (code_review round-2 MISSING_AC_EVIDENCE remediation)."""

    def test_is_a_frozenset(self) -> None:
        from devbench.source_classification import WRITE_PATH_AUDIT_SCAN_EXTENSIONS

        assert isinstance(WRITE_PATH_AUDIT_SCAN_EXTENSIONS, frozenset)

    def test_every_member_is_lowercase_and_dotted(self) -> None:
        from devbench.source_classification import WRITE_PATH_AUDIT_SCAN_EXTENSIONS

        for extension in WRITE_PATH_AUDIT_SCAN_EXTENSIONS:
            assert extension.startswith("."), f"{extension!r} must start with '.'"
            assert extension == extension.lower(), f"{extension!r} must be lowercase"

    @pytest.mark.parametrize("extension", _WRITE_PATH_AUDIT_HISTORICAL_EXTENSIONS)
    def test_contains_every_historical_write_path_audit_extension(self, extension: str) -> None:
        from devbench.source_classification import WRITE_PATH_AUDIT_SCAN_EXTENSIONS

        assert extension in WRITE_PATH_AUDIT_SCAN_EXTENSIONS

    def test_membership_is_exactly_the_historical_nine_no_more_no_less(self) -> None:
        """A membership-equality pin, not a subset check: proves the deleted
        local `permission_flag_writepath._SOURCE_EXTENSIONS` tuple was
        reproduced exactly, with nothing added and nothing dropped."""
        from devbench.source_classification import WRITE_PATH_AUDIT_SCAN_EXTENSIONS

        assert frozenset(_WRITE_PATH_AUDIT_HISTORICAL_EXTENSIONS) == WRITE_PATH_AUDIT_SCAN_EXTENSIONS

    def test_is_a_strict_subset_of_source_extensions(self) -> None:
        """The audit's scan scope answers a deliberately narrower question
        than `SOURCE_EXTENSIONS` -- a second named set in the one module
        (spec 3.5, AC-2, AC-3), not a second duplicate definition site."""
        from devbench.source_classification import SOURCE_EXTENSIONS, WRITE_PATH_AUDIT_SCAN_EXTENSIONS

        assert WRITE_PATH_AUDIT_SCAN_EXTENSIONS < SOURCE_EXTENSIONS


@pytest.mark.unit
class TestIsWritePathAuditExtension:
    @pytest.mark.parametrize("extension", _WRITE_PATH_AUDIT_HISTORICAL_EXTENSIONS)
    def test_known_extension_is_scanned(self, extension: str) -> None:
        from devbench.source_classification import is_write_path_audit_extension

        assert is_write_path_audit_extension(extension) is True

    @pytest.mark.parametrize(
        "extension",
        sorted(set(_CLI_REACHABILITY_HISTORICAL_EXTENSIONS) - set(_WRITE_PATH_AUDIT_HISTORICAL_EXTENSIONS)),
    )
    def test_reachability_only_extension_is_not_scanned(self, extension: str) -> None:
        """The six extensions the reachability consumer recognises but the
        write-path audit's deleted local tuple never did (`.cjs`, `.kt`,
        `.mjs`, `.php`, `.swift`, `.vue`) must stay outside the audit's
        scan scope -- AC-E2-F6-S1-T1-5 requires byte-identical audit
        behaviour, not a widened one."""
        from devbench.source_classification import is_write_path_audit_extension

        assert is_write_path_audit_extension(extension) is False

    def test_matching_is_exact_case_not_lowercased(self) -> None:
        """Documented case-policy divergence from `is_source_extension`
        (code_review round-2 SOLID_VIOLATION remediation):
        `is_write_path_audit_extension` preserves
        `permission_flag_writepath`'s pre-migration exact-case scan, so a
        mixed-case suffix the reachability consumer accepts case-
        insensitively is not scanned by the write-path audit. This is a
        named, documented divergence pinned by this test -- not a silent
        raw membership test left at the call site."""
        from devbench.source_classification import is_source_extension, is_write_path_audit_extension

        assert is_write_path_audit_extension(".py") is True
        assert is_source_extension(".PY") is True
        assert is_write_path_audit_extension(".PY") is False

    def test_empty_suffix_is_not_scanned(self) -> None:
        from devbench.source_classification import is_write_path_audit_extension

        assert is_write_path_audit_extension("") is False


@pytest.mark.unit
class TestWritePathAuditConsumerIntegration:
    """Relocated from `tests/test_plugin_helpers/test_permission_flag_writepath.py`
    (test_review round-3 TDD_CYCLE_MISSING remediation; originally added
    there in test_review round-1 COVERAGE_REGRESSION). This test's
    `from devbench import source_classification` import legitimately fails
    before this migration lands, and `default_pytest_runner`
    (`devbench.tdd_gate`) runs pytest at FILE scope for
    `green-green-check` -- a before-state failure anywhere in a witness
    file rejects every witness selected from that file, not merely its own
    outcome. This module is never used as a green-green witness source, so
    it is the safe home for a migration pin that only passes after the
    migration lands. See the module docstring above for the full
    rationale."""

    def test_iter_source_files_extension_membership_is_driven_by_the_shared_module(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Migration pin (AC-2, AC-3): fails if
        `devbench.plugin_helpers.permission_flag_writepath` ever stops
        consuming `devbench.source_classification.WRITE_PATH_AUDIT_SCAN_EXTENSIONS`
        dynamically via `is_write_path_audit_extension` -- e.g. by
        re-declaring a private, frozen copy of the extension set.
        Widening the shared module's scan set must change
        `_iter_source_files`'s output; if a reversal re-declares a private
        tuple instead, the shared attribute either no longer exists to
        patch or patching it has no effect, and this test fails either
        way."""
        import devbench.plugin_helpers.permission_flag_writepath as pfw
        from devbench import source_classification

        target = tmp_path / "src" / "components" / "Widget.vue"
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text("isPremiumEligible = true;\n", encoding="utf-8")

        before = pfw._iter_source_files(tmp_path)
        assert before == []

        monkeypatch.setattr(
            source_classification,
            "WRITE_PATH_AUDIT_SCAN_EXTENSIONS",
            source_classification.WRITE_PATH_AUDIT_SCAN_EXTENSIONS | {".vue"},
        )
        after = pfw._iter_source_files(tmp_path)
        assert after == [target]


@pytest.mark.unit
class TestEntryPointStems:
    def test_is_a_frozenset(self) -> None:
        from devbench.source_classification import ENTRY_POINT_STEMS

        assert isinstance(ENTRY_POINT_STEMS, frozenset)

    def test_every_member_is_lowercase_and_undotted(self) -> None:
        """Stems are filename-without-extension identifiers, never formatted like an
        extension -- pins the categories' disjointness (spec 3.5, AC-E2-F6-S1-T1-1)."""
        from devbench.source_classification import ENTRY_POINT_STEMS, SOURCE_EXTENSIONS

        for stem in ENTRY_POINT_STEMS:
            assert not stem.startswith("."), f"{stem!r} must not look like an extension"
            assert stem == stem.lower(), f"{stem!r} must be lowercase"
        assert SOURCE_EXTENSIONS.isdisjoint(ENTRY_POINT_STEMS)

    @pytest.mark.parametrize("stem", ["index", "main", "app", "__init__", "setup", "conftest", "wsgi", "asgi"])
    def test_known_entry_point_stems(self, stem: str) -> None:
        from devbench.source_classification import is_entry_point_stem

        assert is_entry_point_stem(stem) is True

    def test_matching_is_case_insensitive(self) -> None:
        from devbench.source_classification import is_entry_point_stem

        assert is_entry_point_stem("Index") is is_entry_point_stem("INDEX") is True

    def test_ordinary_stem_is_not_an_entry_point(self) -> None:
        from devbench.source_classification import is_entry_point_stem

        assert is_entry_point_stem("Button") is False


@pytest.mark.unit
class TestTestPathMarkers:
    def test_markers_are_frozensets(self) -> None:
        from devbench.source_classification import TEST_FILENAME_MARKERS, TEST_PATH_MARKERS

        assert isinstance(TEST_PATH_MARKERS, frozenset)
        assert isinstance(TEST_FILENAME_MARKERS, frozenset)

    def test_every_path_marker_is_lowercase_and_slash_delimited(self) -> None:
        """Pins half of the disjointness contract the module docstring states
        (test_review round-3 COVERAGE_REGRESSION): `is_test_path` lowercases
        the path before comparing and matches each marker as a substring, so
        an uppercase or unslashed marker added later could never match --
        this test fails loudly if that ever happens, rather than leaving the
        shape invariant unpinned the way `SOURCE_EXTENSIONS`,
        `WRITE_PATH_AUDIT_SCAN_EXTENSIONS` and `ENTRY_POINT_STEMS` already
        are."""
        from devbench.source_classification import TEST_PATH_MARKERS

        for marker in TEST_PATH_MARKERS:
            assert marker.startswith("/"), f"{marker!r} must start with '/'"
            assert marker.endswith("/"), f"{marker!r} must end with '/'"
            assert marker == marker.lower(), f"{marker!r} must be lowercase"

    def test_every_filename_marker_is_lowercase_and_dot_delimited(self) -> None:
        """The filename-marker half of the same disjointness contract
        (test_review round-3 COVERAGE_REGRESSION): see
        `test_every_path_marker_is_lowercase_and_slash_delimited` above."""
        from devbench.source_classification import TEST_FILENAME_MARKERS

        for marker in TEST_FILENAME_MARKERS:
            assert marker.startswith("."), f"{marker!r} must start with '.'"
            assert marker.endswith("."), f"{marker!r} must end with '.'"
            assert marker == marker.lower(), f"{marker!r} must be lowercase"

    def test_membership_is_exactly_the_historical_eleven_no_more_no_less(self) -> None:
        """Set-equality pin (test_review round-4 COVERAGE_REGRESSION
        remediation): mirrors
        `TestWritePathAuditScanExtensions::test_membership_is_exactly_the_historical_nine_no_more_no_less`.
        `TEST_PATH_MARKERS` was the only migrated frozenset in this module
        left without a membership-completeness pin; deleting
        `/__snapshots__/`, `/specs/` and `/mocks/` from it left the full
        suite green because the hand-picked directory-marker
        parametrization below did not sample all 11 members. This proves
        the deleted `cli._REACHABILITY_TEST_PATH_MARKERS` 11-member tuple
        was reproduced exactly, with nothing added and nothing dropped."""
        from devbench.source_classification import TEST_PATH_MARKERS

        assert frozenset(_CLI_REACHABILITY_HISTORICAL_TEST_PATH_MARKERS) == TEST_PATH_MARKERS


@pytest.mark.unit
class TestIsTestPath:
    @pytest.mark.parametrize(
        "rel_path",
        [
            "src/__tests__/Button.tsx",
            "src/__mocks__/api.ts",
            "src/__snapshots__/Button.tsx",
            "src/test/helper.py",
            "src/tests/test_button.py",
            "src/spec/widget_spec.rb",
            "src/specs/widget.rb",
            "src/fixtures/seed.py",
            "src/mocks/handlers.ts",
            "src/.storybook/preview.ts",
            "src/stories/Button.tsx",
        ],
    )
    def test_directory_marker_is_a_test_path(self, rel_path: str) -> None:
        """Parametrized over all 11 `TEST_PATH_MARKERS` members (test_review
        round-4 COVERAGE_REGRESSION remediation), not the prior hand-picked
        8-of-11 sample -- each of `/__snapshots__/`, `/specs/` and `/mocks/`
        now has its own behavioural witness alongside the set-equality pin
        in `TestTestPathMarkers`."""
        from devbench.source_classification import is_test_path

        assert is_test_path(rel_path) is True

    @pytest.mark.parametrize(
        "rel_path",
        [
            "src/Button.test.tsx",
            "src/Button.spec.tsx",
            "src/Button.stories.tsx",
        ],
    )
    def test_filename_marker_is_a_test_path(self, rel_path: str) -> None:
        from devbench.source_classification import is_test_path

        assert is_test_path(rel_path) is True

    @pytest.mark.parametrize(
        "rel_path",
        [
            "src/test_button.py",
            "src/button_test.py",
        ],
    )
    def test_python_test_stem_convention_is_a_test_path(self, rel_path: str) -> None:
        from devbench.source_classification import is_test_path

        assert is_test_path(rel_path) is True

    def test_matching_is_case_insensitive(self) -> None:
        from devbench.source_classification import is_test_path

        assert is_test_path("src/__TESTS__/Button.tsx") is True

    def test_ordinary_source_path_is_not_a_test_path(self) -> None:
        from devbench.source_classification import is_test_path

        assert is_test_path("src/components/Button.tsx") is False

    def test_backslash_separators_are_normalized(self) -> None:
        from devbench.source_classification import is_test_path

        assert is_test_path("src\\__tests__\\Button.tsx") is True


# ---------------------------------------------------------------------------
# extract_import_targets (spec 4.6, issue #13 AC4; E5-F2-S1-T2 AC-5)
# ---------------------------------------------------------------------------


class TestJsTsFamilyExtensions:
    """`JS_TS_FAMILY_EXTENSIONS` is the single declared source for the JS/TS
    grouping (round-2 code_review finding) -- `devbench.cli`'s shared-file
    import-*resolution* step imports this same constant rather than
    redeclaring it, so this pin protects both consumers from silent drift."""

    def test_membership_is_pinned(self) -> None:
        from devbench.source_classification import JS_TS_FAMILY_EXTENSIONS

        assert JS_TS_FAMILY_EXTENSIONS == (".js", ".jsx", ".ts", ".tsx", ".mjs", ".cjs")

    def test_every_family_extension_dispatches_to_the_js_extractor(self) -> None:
        """Every extension `JS_TS_FAMILY_EXTENSIONS` names must actually route
        through the JS/TS extractor in `extract_import_targets` -- pins the two
        constants against drifting apart from each other."""
        from devbench.source_classification import JS_TS_FAMILY_EXTENSIONS, extract_import_targets

        for suffix in JS_TS_FAMILY_EXTENSIONS:
            assert extract_import_targets(suffix, "import Foo from './shared';\n") == ["./shared"]


class TestExtractImportTargets:
    """Language-appropriate import/require scanning dispatched on the extension
    the caller's file already carries -- every branch below is killed by a
    distinct test (extractor dispatch table AND the guard/absent-entry paths)."""

    def test_python_from_import_and_bare_import(self) -> None:
        from devbench.source_classification import extract_import_targets

        text = "import shared_module\nfrom pkg.other import Thing\nfrom . import local_mod\n"
        assert extract_import_targets(".py", text) == ["pkg.other", ".local_mod", "shared_module"]

    def test_python_dots_only_from_import_expands_per_imported_name(self) -> None:
        """``from . import a, b as c`` names no dotted module of its own -- each
        imported name becomes its own ``.``-prefixed target (spec 4.6, round-1 A3
        finding), the same shape ``from .a import x`` / ``from .b import x`` already
        produce explicitly."""
        from devbench.source_classification import extract_import_targets

        assert extract_import_targets(".py", "from . import a, b as c\n") == [".a", ".b"]

    def test_python_dots_only_from_import_matches_explicit_module_path_spelling(self) -> None:
        """Two idiomatic spellings of the same import must resolve identically
        (spec 4.6, round-1 A3 finding)."""
        from devbench.source_classification import extract_import_targets

        assert extract_import_targets(".py", "from . import target\n") == extract_import_targets(
            ".py", "from .target import x\n"
        )

    def test_python_dots_only_from_import_strips_parenthesized_clause(self) -> None:
        """The single-line parenthesized form (``from . import (a, b)``) strips the
        wrapping parens before splitting on commas, rather than treating ``(a``/
        ``b)`` as literal (and wrong) names."""
        from devbench.source_classification import extract_import_targets

        assert extract_import_targets(".py", "from . import (a, b)\n") == [".a", ".b"]

    def test_python_comma_separated_import_splits_and_strips_aliases(self) -> None:
        from devbench.source_classification import extract_import_targets

        assert extract_import_targets(".py", "import os, sys as _sys\n") == ["os", "sys"]

    @pytest.mark.parametrize("suffix", [".js", ".jsx", ".ts", ".tsx", ".mjs", ".cjs"])
    def test_js_family_import_export_and_require(self, suffix: str) -> None:
        from devbench.source_classification import extract_import_targets

        text = "import Foo from './shared_module';\nimport './other';\nconst x = require('./bar');\n"
        assert extract_import_targets(suffix, text) == ["./shared_module", "./other", "./bar"]

    def test_js_export_from_form(self) -> None:
        from devbench.source_classification import extract_import_targets

        assert extract_import_targets(".ts", "export { Foo } from './shared_module';\n") == ["./shared_module"]

    def test_js_dynamic_import_call_form(self) -> None:
        """F1 (round 1 test_review): the ``import(...)`` dynamic-call form (e.g.
        ``const mod = await import('./foo')``) is a distinct grammar shape from
        both the static ``import ... from`` form and ``require(...)`` -- both the
        no-space (``import('./foo')``) and spaced (``import ('./foo')``) call
        spellings must extract a target rather than silently returning nothing."""
        from devbench.source_classification import extract_import_targets

        assert extract_import_targets(".js", "const mod = await import('./dynamic_module');\n") == ["./dynamic_module"]
        assert extract_import_targets(".js", "await import ('./spaced_module');\n") == ["./spaced_module"]

    def test_go_single_line_and_grouped_import_block(self) -> None:
        from devbench.source_classification import extract_import_targets

        text = 'import "fmt"\nimport (\n\t"os"\n\t"myproj/shared"\n)\n'
        assert extract_import_targets(".go", text) == ["fmt", "os", "myproj/shared"]

    def test_go_multiple_grouped_import_blocks_are_all_read(self) -> None:
        """3b (round-2 test_review): a Go file with TWO grouped import blocks
        (e.g. one for stdlib, one for third-party imports, a shape
        `goimports` commonly produces) must have both blocks' targets
        extracted -- a `.search()`-only implementation stops at the first."""
        from devbench.source_classification import extract_import_targets

        text = 'import (\n\t"fmt"\n\t"os"\n)\n\nimport (\n\t"myproj/shared"\n\t"myproj/other"\n)\n'
        assert extract_import_targets(".go", text) == ["fmt", "os", "myproj/shared", "myproj/other"]

    def test_ruby_require_and_require_relative(self) -> None:
        from devbench.source_classification import extract_import_targets

        text = "require 'shared_module'\nrequire_relative './lib/other'\n"
        assert extract_import_targets(".rb", text) == ["shared_module", "./lib/other"]

    @pytest.mark.parametrize("suffix", [".java", ".kt"])
    def test_jvm_import_including_static(self, suffix: str) -> None:
        from devbench.source_classification import extract_import_targets

        text = "import com.example.SharedModule;\nimport static com.example.Utils.foo;\n"
        assert extract_import_targets(suffix, text) == ["com.example.SharedModule", "com.example.Utils.foo"]

    def test_swift_import(self) -> None:
        from devbench.source_classification import extract_import_targets

        assert extract_import_targets(".swift", "import SharedModule\n") == ["SharedModule"]

    def test_csharp_using_including_static(self) -> None:
        from devbench.source_classification import extract_import_targets

        text = "using MyProject.Shared;\nusing static MyProject.Utils;\n"
        assert extract_import_targets(".cs", text) == ["MyProject.Shared", "MyProject.Utils"]

    def test_php_require_include_and_use(self) -> None:
        from devbench.source_classification import extract_import_targets

        text = "require_once 'lib/shared.php';\ninclude 'lib/other.php';\nuse App\\Shared;\n"
        assert extract_import_targets(".php", text) == ["lib/shared.php", "lib/other.php", "App\\Shared"]

    def test_vue_extension_has_no_extractor_and_returns_empty(self) -> None:
        """`.vue` is a known SOURCE_EXTENSIONS member with no registered extractor
        (imports live inside an embedded <script> block this module does not
        parse) -- distinct from a wholly unrecognised extension."""
        from devbench.source_classification import extract_import_targets

        assert extract_import_targets(".vue", "import Foo from './shared_module'\n") == []

    def test_non_source_extension_returns_empty_without_scanning(self) -> None:
        from devbench.source_classification import extract_import_targets

        assert extract_import_targets(".md", "import Foo from './shared_module'\n") == []

    def test_is_source_extension_guard_is_checked_even_if_extractor_table_drifted(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Defense in depth: `extract_import_targets` gates on `is_source_extension`
        BEFORE the `_IMPORT_TARGET_EXTRACTORS` lookup, not merely relying on that
        table only ever containing SOURCE_EXTENSIONS members. Proven by patching in
        a non-source-extension entry and confirming it is still never consulted."""
        import devbench.source_classification as source_classification_module
        from devbench.source_classification import extract_import_targets

        def _should_never_run(_text: str) -> list[str]:
            raise AssertionError("a non-source extension's extractor must never be invoked")

        drifted = dict(source_classification_module._IMPORT_TARGET_EXTRACTORS)
        drifted[".md"] = _should_never_run
        monkeypatch.setattr(source_classification_module, "_IMPORT_TARGET_EXTRACTORS", drifted)

        assert extract_import_targets(".md", "import Foo from './shared_module'\n") == []

    def test_no_import_statements_returns_empty(self) -> None:
        from devbench.source_classification import extract_import_targets

        assert extract_import_targets(".py", "x = 1\n") == []

    def test_suffix_matching_is_case_insensitive(self) -> None:
        from devbench.source_classification import extract_import_targets

        assert extract_import_targets(".PY", "import shared_module\n") == ["shared_module"]


class TestClassifiedSourceWalkExcludedDirs:
    """`CLASSIFIED_SOURCE_WALK_EXCLUDED_DIRS` (spec 4.7 bullet 4, source-literal extraction,
    E6-F2-S1-T1) -- the directories `iter_classified_source_files` prunes during its walk."""

    def test_membership_is_pinned(self) -> None:
        from devbench.source_classification import CLASSIFIED_SOURCE_WALK_EXCLUDED_DIRS

        expected = frozenset(
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
        assert expected == CLASSIFIED_SOURCE_WALK_EXCLUDED_DIRS


class TestIterClassifiedSourceFiles:
    """`iter_classified_source_files` (spec 4.7 bullet 4; caylent-solutions/devbench-internal-backlog#17
    AC-19; E6-F2-S1-T1) -- the enumeration entry point `fixture_consistency`'s config-gated
    `extract_source_literals` scan mode uses to discover candidate source files, dispatching
    purely on `is_source_extension` (PM-3: no second extension tuple)."""

    def test_finds_classified_source_files_and_skips_others(self, tmp_path: Path) -> None:
        from devbench.source_classification import iter_classified_source_files

        (tmp_path / "app.py").write_text("x = 1\n", encoding="utf-8")
        (tmp_path / "notes.md").write_text("# not source\n", encoding="utf-8")
        (tmp_path / "data.json").write_text("{}", encoding="utf-8")

        results = iter_classified_source_files(tmp_path)

        assert results == [tmp_path / "app.py"]

    def test_prunes_excluded_directories(self, tmp_path: Path) -> None:
        from devbench.source_classification import iter_classified_source_files

        (tmp_path / "app.py").write_text("x = 1\n", encoding="utf-8")
        vendored = tmp_path / "node_modules" / "pkg"
        vendored.mkdir(parents=True)
        (vendored / "index.js").write_text("module.exports = {};\n", encoding="utf-8")

        results = iter_classified_source_files(tmp_path)

        assert results == [tmp_path / "app.py"]

    def test_unlisted_subdirectory_is_still_scanned(self, tmp_path: Path) -> None:
        """A subdirectory not in `CLASSIFIED_SOURCE_WALK_EXCLUDED_DIRS` (e.g. an app-defined
        `services/` tree) still has its own internals scanned and returned."""
        from devbench.source_classification import iter_classified_source_files

        nested = tmp_path / "services" / "billing"
        nested.mkdir(parents=True)
        (nested / "invoice.py").write_text("x = 1\n", encoding="utf-8")

        results = iter_classified_source_files(tmp_path)

        assert results == [nested / "invoice.py"]

    def test_empty_directory_returns_empty_list(self, tmp_path: Path) -> None:
        from devbench.source_classification import iter_classified_source_files

        assert iter_classified_source_files(tmp_path) == []

    def test_results_are_sorted_deterministically(self, tmp_path: Path) -> None:
        from devbench.source_classification import iter_classified_source_files

        (tmp_path / "zeta.py").write_text("x = 1\n", encoding="utf-8")
        (tmp_path / "alpha.py").write_text("x = 1\n", encoding="utf-8")

        results = iter_classified_source_files(tmp_path)

        assert results == [tmp_path / "alpha.py", tmp_path / "zeta.py"]


class TestIterClassifiedSourceFilesSymlinkBoundary:
    """SECURITY (security_review round-3 MEDIUM finding): the classified-source-file NAME (its
    path under *root*) previously determined whether a candidate was enumerated, while a later
    `Path.read_text` call on that candidate follows a symlink to whatever it actually points at
    -- so a symlink committed inside the repo whose target resolves OUTSIDE the walked root was a
    read primitive for arbitrary filesystem content under a path that looked like it belonged to
    the scanned repo. Combined with `fixture_consistency`'s source-literal extraction mode (which
    echoes matched file content into gate output), this was a read-and-echo primitive. The fix:
    skip a candidate whose resolved real path falls outside the resolved *root*, comparing both
    sides resolved so a *root* itself reached through a symlink (common for `/tmp` on macOS, or a
    bind mount) is not spuriously treated as "everything is outside root"."""

    def test_symlink_resolving_outside_root_is_excluded(self, tmp_path: Path) -> None:
        from devbench.source_classification import iter_classified_source_files

        root = tmp_path / "repo"
        root.mkdir()
        outside = tmp_path / "outside"
        outside.mkdir()
        (outside / "secret.py").write_text("SECRET_TOKEN = 'do-not-scan-me'\n", encoding="utf-8")
        (root / "linked.py").symlink_to(outside / "secret.py")
        (root / "normal.py").write_text("x = 1\n", encoding="utf-8")

        results = iter_classified_source_files(root)

        assert results == [root / "normal.py"], (
            f"a symlink whose resolved target lies outside the walked root must never be enumerated, got: {results}"
        )

    def test_symlink_resolving_inside_root_is_included(self, tmp_path: Path) -> None:
        """DECISION: a symlink whose target also resolves inside *root* discloses nothing
        outside the repo -- it is included, exactly like an ordinary file, even though this can
        enumerate the same on-disk content twice under two different repo-relative paths (once
        directly, once via the symlink). Excluding every symlink unconditionally would silently
        drop legitimate scan coverage for a real, in-repo authoring pattern (e.g. a compatibility
        shim re-exporting a moved module) with no security benefit, since nothing crosses the
        repo boundary."""
        from devbench.source_classification import iter_classified_source_files

        root = tmp_path / "repo"
        root.mkdir()
        (root / "real.py").write_text("VALUE = 1\n", encoding="utf-8")
        (root / "shim.py").symlink_to(root / "real.py")

        results = iter_classified_source_files(root)

        assert results == [root / "real.py", root / "shim.py"]

    def test_dangling_symlink_resolving_inside_root_is_still_included(self, tmp_path: Path) -> None:
        """A dangling symlink whose (nonexistent) target still names a location inside *root* is
        included, unchanged from pre-fix behaviour -- it is not filtered on `Path.is_file()` (the
        shared-file gate's own documented non-regression, `cli._derive_shared_file_registry`'s
        `Raises:` block), so attempting to read it still surfaces as a loud `OSError`/`load_error`
        downstream rather than a silent skip."""
        from devbench.source_classification import iter_classified_source_files

        root = tmp_path / "repo"
        root.mkdir()
        (root / "dangling.py").symlink_to(root / "does_not_exist.py")

        results = iter_classified_source_files(root)

        assert results == [root / "dangling.py"]

    def test_dangling_symlink_resolving_outside_root_is_excluded(self, tmp_path: Path) -> None:
        """A dangling symlink whose target string names a location OUTSIDE *root* is excluded
        the same way a live out-of-root symlink is -- the boundary check is based on the
        resolved target path, not on whether that target actually exists."""
        from devbench.source_classification import iter_classified_source_files

        root = tmp_path / "repo"
        root.mkdir()
        outside = tmp_path / "outside"
        (root / "dangling_outside.py").symlink_to(outside / "does_not_exist_either.py")
        (root / "normal.py").write_text("x = 1\n", encoding="utf-8")

        results = iter_classified_source_files(root)

        assert results == [root / "normal.py"]

    def test_root_itself_reached_through_a_symlink_still_scans_normally(self, tmp_path: Path) -> None:
        """The boundary check must resolve BOTH the candidate and *root* before comparing --
        resolving only the candidate while leaving *root* as the unresolved symlinked path passed
        in would make every real file under *root* look like it resolves "outside" that
        unresolved root, silently excluding everything."""
        from devbench.source_classification import iter_classified_source_files

        real_root = tmp_path / "real_repo"
        real_root.mkdir()
        (real_root / "app.py").write_text("x = 1\n", encoding="utf-8")
        symlinked_root = tmp_path / "symlinked_repo"
        symlinked_root.symlink_to(real_root)

        results = iter_classified_source_files(symlinked_root)

        assert results == [symlinked_root / "app.py"]

    def test_symlink_resolving_into_a_prefix_sharing_sibling_directory_is_excluded(self, tmp_path: Path) -> None:
        """SECURITY (WARN 2, test_review round-4): the boundary comparison in
        :func:`_resolves_outside_root` must be a true path-component containment check, never a
        naive string-prefix comparison. A root of ``tmp_path / "repo"`` and an out-of-root target
        of ``tmp_path / "repo-evil"`` share the string prefix ``"repo"`` even though
        ``repo-evil`` is a distinct SIBLING directory, not a subdirectory, of ``repo`` -- a
        rewrite of the comparison to
        ``not str(resolved_candidate).startswith(str(resolved_root))`` would wrongly treat
        ``repo-evil/secret.py`` as being "inside" ``repo`` (since the string ``".../repo-evil/..."``
        starts with the string ``".../repo"``) and include it, disclosing sibling-directory
        content the boundary check exists to keep out. The six other cases in this class all use
        ``tmp_path / "outside"`` as their out-of-root target, whose name shares no prefix with
        ``tmp_path / "repo"`` at all, so none of them can catch this particular escape."""
        from devbench.source_classification import iter_classified_source_files

        root = tmp_path / "repo"
        root.mkdir()
        evil_sibling = tmp_path / "repo-evil"
        evil_sibling.mkdir()
        (evil_sibling / "secret.py").write_text("SECRET_TOKEN = 'do-not-scan-me'\n", encoding="utf-8")
        (root / "linked.py").symlink_to(evil_sibling / "secret.py")
        (root / "normal.py").write_text("x = 1\n", encoding="utf-8")

        results = iter_classified_source_files(root)

        assert results == [root / "normal.py"], (
            f"a symlink resolving into a prefix-sharing SIBLING directory must be excluded "
            f"exactly like one resolving into any other out-of-root directory, got: {results}"
        )

    def test_symlinked_directory_is_never_descended_into(self, tmp_path: Path) -> None:
        """W4 (round-4 code_review): the "symlinked DIRECTORY is never descended into" claim in
        :func:`iter_classified_source_files`'s own docstring previously rested entirely on
        ``os.walk``'s unpinned default ``followlinks=False`` -- a future edit passing
        ``followlinks=True`` (e.g. to "fix" a coverage gap elsewhere) would silently flip this
        behaviour with no test catching it. A file living ONLY under a symlinked directory
        (whose target resolves INSIDE *root*, so the file-level boundary check in
        :func:`_resolves_outside_root` would not itself exclude it) must never appear in the
        result -- proving the exclusion is genuinely about not descending into the directory at
        all, not about the file-level symlink boundary check."""
        from devbench.source_classification import iter_classified_source_files

        root = tmp_path / "repo"
        root.mkdir()
        real_dir = root / "real_dir"
        real_dir.mkdir()
        (real_dir / "inner.py").write_text("x = 1\n", encoding="utf-8")
        (root / "linked_dir").symlink_to(real_dir)
        (root / "normal.py").write_text("x = 1\n", encoding="utf-8")

        results = iter_classified_source_files(root)

        assert results == [root / "normal.py", real_dir / "inner.py"], (
            f"a symlinked directory must never be descended into -- its contents must never be "
            f"reached via the symlinked path, got: {results}"
        )
        assert (root / "linked_dir" / "inner.py") not in results, (
            f"the file must never be reachable through the symlinked directory path, got: {results}"
        )
