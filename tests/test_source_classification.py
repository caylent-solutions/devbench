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
