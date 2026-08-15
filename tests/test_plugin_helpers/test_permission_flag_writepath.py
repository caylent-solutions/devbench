"""Tests for ``devbench.plugin_helpers.permission_flag_writepath`` (QA finding 07).

Covers the write-path verdict classifier (live / default_only /
no_write_path / not_found / indeterminate), the placeholder-seam
finder, and the rendered audit-line / blocking-finding text the
``spec-to-backlog`` skill's Step 3b pastes into its audit trail.
"""

from __future__ import annotations

import textwrap
from pathlib import Path
from types import ModuleType

import pytest


def _pfw() -> ModuleType:
    """Import ``devbench.plugin_helpers.permission_flag_writepath`` on demand.

    Deferred to each test's own body (never at module scope) so pytest can
    collect this file even when the production module has not landed yet:
    the RED gate (``devbench.tdd_gate``) proves a genuine pre-pick failure
    by stashing only the production-source Changes Manifest rows and
    re-running this file. A module-scope import would turn the missing
    submodule into a collection error (pytest exit 2, "interrupted"); this
    deferred import instead raises inside the named test's own call phase,
    which pytest reports as a genuine FAILED test (pytest exit 1) -- the
    outcome the gate requires.
    """
    import devbench.plugin_helpers.permission_flag_writepath as module

    return module


def _write(path: Path, content: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(textwrap.dedent(content).lstrip("\n"), encoding="utf-8")
    return path


# ---------------------------------------------------------------------------
# audit_write_path -- verdict classification
# ---------------------------------------------------------------------------

# Hoisted once (test_review round-3 DRY_VIOLATION remediation) and referenced
# by both `test_every_excluded_dir_name_is_skipped`'s parametrize decorator
# and the drift-guard test below it, so removing a member from this tuple
# cannot leave the drift guard passing on a stale copy. A plain tuple of
# string literals needs no production import, so the collection-time
# deferral rationale documented in `_pfw()`'s own docstring does not apply
# here -- this can safely live at module scope.
_DIRECTLY_PARAMETRIZED_EXCLUDED_DIR_NAMES = (
    ".venv",
    "venv",
    "backlog",
    ".pytest_cache",
    ".mypy_cache",
    "coverage",
    "dist",
    "build",
    "out",
)


@pytest.mark.unit
class TestAuditWritePath:
    def test_missing_repo_root_raises(self, tmp_path: Path) -> None:
        pfw = _pfw()
        with pytest.raises(FileNotFoundError, match="repo_root does not exist"):
            pfw.audit_write_path(tmp_path / "nope", "isPremiumEligible")

    def test_flag_never_mentioned_is_not_found(self, tmp_path: Path) -> None:
        pfw = _pfw()
        _write(tmp_path / "src" / "other.ts", "export const x = 1;\n")
        audit = pfw.audit_write_path(tmp_path, "isPremiumEligible")
        assert audit.verdict == "not_found"
        assert audit.mention_count == 0
        assert audit.assignment_sites == ()
        assert audit.is_verified_live is False

    def test_flag_only_read_is_no_write_path(self, tmp_path: Path) -> None:
        pfw = _pfw()
        _write(
            tmp_path / "src" / "components" / "Banner.tsx",
            """
            if (user.isPremiumEligible) {
              return <Banner />;
            }
            """,
        )
        audit = pfw.audit_write_path(tmp_path, "isPremiumEligible")
        assert audit.verdict == "no_write_path"
        assert audit.mention_count == 1
        assert audit.assignment_sites == ()
        assert audit.is_verified_live is False

    def test_flag_assigned_only_in_default_signalled_file_is_default_only(self, tmp_path: Path) -> None:
        pfw = _pfw()
        _write(
            tmp_path / "src" / "state" / "defaultState.ts",
            """
            export const defaultState = {
              isPremiumEligible: false,
            };
            """,
        )
        audit = pfw.audit_write_path(tmp_path, "isPremiumEligible")
        assert audit.verdict == "default_only"
        assert audit.is_verified_live is False
        assert len(audit.assignment_sites) == 1
        assert audit.assignment_sites[0].relative_path == "src/state/defaultState.ts"

    def test_flag_assigned_in_reducer_is_live(self, tmp_path: Path) -> None:
        pfw = _pfw()
        _write(
            tmp_path / "src" / "state" / "defaultState.ts",
            """
            export const defaultState = {
              isPremiumEligible: false,
            };
            """,
        )
        _write(
            tmp_path / "src" / "reducers" / "permissionReducer.ts",
            """
            case 'SET_ELIGIBILITY':
              isPremiumEligible = action.payload.value;
              return state;
            """,
        )
        audit = pfw.audit_write_path(tmp_path, "isPremiumEligible")
        assert audit.verdict == "live"
        assert audit.is_verified_live is True
        paths = {s.relative_path for s in audit.assignment_sites}
        assert "src/reducers/permissionReducer.ts" in paths

    def test_setter_call_in_service_counts_as_live(self, tmp_path: Path) -> None:
        pfw = _pfw()
        _write(
            tmp_path / "src" / "services" / "eligibilityService.py",
            """
            def apply(self, payload):
                self.set_isPremiumEligible(payload["eligible"])
            """,
        )
        audit = pfw.audit_write_path(tmp_path, "isPremiumEligible")
        assert audit.verdict == "live"

    def test_assignment_site_with_no_signal_is_indeterminate(self, tmp_path: Path) -> None:
        pfw = _pfw()
        _write(
            tmp_path / "src" / "utils" / "helpers.py",
            "isPremiumEligible = compute_something()\n",
        )
        audit = pfw.audit_write_path(tmp_path, "isPremiumEligible")
        assert audit.verdict == "indeterminate"
        assert audit.is_verified_live is False

    def test_comparison_operators_are_not_counted_as_assignment(self, tmp_path: Path) -> None:
        pfw = _pfw()
        _write(
            tmp_path / "src" / "reducers" / "permissionReducer.ts",
            """
            if (isPremiumEligible == true) { return; }
            if (isPremiumEligible === false) { return; }
            if (isPremiumEligible != null) { return; }
            const ok = isPremiumEligible >= 1;
            """,
        )
        audit = pfw.audit_write_path(tmp_path, "isPremiumEligible")
        assert audit.assignment_sites == ()
        assert audit.verdict == "no_write_path"
        assert audit.mention_count == 4

    def test_excluded_dirs_are_skipped(self, tmp_path: Path) -> None:
        pfw = _pfw()
        _write(
            tmp_path / "node_modules" / "vendor" / "reducer.js",
            "isPremiumEligible = true;\n",
        )
        audit = pfw.audit_write_path(tmp_path, "isPremiumEligible")
        assert audit.verdict == "not_found"
        assert audit.mention_count == 0

    @pytest.mark.parametrize("excluded_dir", _DIRECTLY_PARAMETRIZED_EXCLUDED_DIR_NAMES)
    def test_every_excluded_dir_name_is_skipped(self, tmp_path: Path, excluded_dir: str) -> None:
        pfw = _pfw()
        _write(
            tmp_path / excluded_dir / "nested" / "reducer.py",
            "isPremiumEligible = true;\n",
        )
        audit = pfw.audit_write_path(tmp_path, "isPremiumEligible")
        assert audit.verdict == "not_found"
        assert audit.mention_count == 0

    def test_every_excluded_dir_name_is_skipped_parametrization_tracks_the_module_set(self) -> None:
        """DRY drift-guard (code_review + test_review round-1 advisory, both
        non-blocking; hoist to a shared module-level tuple per test_review
        round-3 DRY_VIOLATION): `test_every_excluded_dir_name_is_skipped`'s
        parametrize list is a literal copy of most of `_EXCLUDED_DIR_NAMES`
        rather than a direct parametrize over the frozenset, because
        parametrize arguments are evaluated at collection time and this
        file defers every import of the production module to each test's
        own body (see `_pfw()` docstring) so the RED gate can stash it
        cleanly. This test closes the gap the copy leaves open: it fails
        the moment `_EXCLUDED_DIR_NAMES` gains or loses a member without
        every list here being updated to match, instead of silently
        under-covering the constant forever. Both this guard and the
        parametrize decorator above now read the same
        `_DIRECTLY_PARAMETRIZED_EXCLUDED_DIR_NAMES` module-level tuple, so a
        member removed from one cannot silently leave the other unchanged."""
        pfw = _pfw()
        directly_parametrized = set(_DIRECTLY_PARAMETRIZED_EXCLUDED_DIR_NAMES)
        covered_by_test_excluded_dirs_are_skipped = {"node_modules"}
        # `.git` and `__pycache__` are members this test module cannot
        # safely exercise directly: nesting a `.git` directory inside
        # `tmp_path` risks colliding with git tooling that walks parent
        # directories looking for a repo root, and `__pycache__` may be
        # recreated by Python's own bytecode caching of the pytest run
        # itself.
        unexercised_for_tooling_safety = {".git", "__pycache__"}
        accounted_for = (
            directly_parametrized | covered_by_test_excluded_dirs_are_skipped | unexercised_for_tooling_safety
        )
        assert accounted_for == pfw._EXCLUDED_DIR_NAMES

    def test_non_source_extension_is_skipped(self, tmp_path: Path) -> None:
        pfw = _pfw()
        _write(
            tmp_path / "docs" / "notes.md",
            "isPremiumEligible = true\n",
        )
        audit = pfw.audit_write_path(tmp_path, "isPremiumEligible")
        assert audit.verdict == "not_found"
        assert audit.mention_count == 0

    def test_scan_set_is_the_narrow_write_path_audit_set_not_source_extensions(self, tmp_path: Path) -> None:
        """AC-E2-F6-S1-T1-5 (code_review round-2 MISSING_AC_EVIDENCE): the
        audit's scan scope stays exactly the pre-migration 9-extension
        set, not the broader 15-extension `SOURCE_EXTENSIONS`
        reachability union. A `.vue` file (recognised by the
        reachability consumer, never by this audit) must stay invisible
        to `audit_write_path` -- the previous round's 70 green-green
        witnesses contained no such fixture, so this closes that gap."""
        pfw = _pfw()
        _write(
            tmp_path / "src" / "components" / "Widget.vue",
            "isPremiumEligible = true;\n",
        )
        audit = pfw.audit_write_path(tmp_path, "isPremiumEligible")
        assert audit.verdict == "not_found"
        assert audit.mention_count == 0

    # A migration pin for `_iter_source_files`'s dynamic consumption of
    # `devbench.source_classification.WRITE_PATH_AUDIT_SCAN_EXTENSIONS`
    # (AC-2, AC-3; originally added here in test_review round-1
    # COVERAGE_REGRESSION as `test_iter_source_files_extension_membership_
    # is_driven_by_the_shared_module`) lives at
    # `tests/test_source_classification.py::TestWritePathAuditConsumerIntegration
    # ::test_iter_source_files_extension_membership_is_driven_by_the_shared_module`
    # instead of in this file (test_review round-3 TDD_CYCLE_MISSING
    # remediation): that test's `from devbench import source_classification`
    # import legitimately fails before this migration lands, and
    # `default_pytest_runner` runs pytest at FILE scope for
    # `green-green-check`, so a before-state failure anywhere in this file
    # would reject every witness selected from it, not merely its own
    # outcome. `tests/test_source_classification.py` is never used as a
    # green-green witness source, so it is the safe home for this pin;
    # nothing here was weakened -- the assertions, the mutation-catching
    # power, and the coverage of `_iter_source_files` are unchanged, only
    # the file moved.

    def test_unreadable_binary_file_is_skipped_not_fatal(self, tmp_path: Path) -> None:
        pfw = _pfw()
        binary_path = tmp_path / "src" / "asset.py"
        binary_path.parent.mkdir(parents=True, exist_ok=True)
        binary_path.write_bytes(b"\xff\xfe\x00isPremiumEligible = true\x00")
        # Should not raise even though decoding may be lossy/fail for some bytes.
        pfw.audit_write_path(tmp_path, "isPremiumEligible")


# ---------------------------------------------------------------------------
# WritePathAudit.render
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestRender:
    def test_render_includes_flag_and_verdict(self, tmp_path: Path) -> None:
        pfw = _pfw()
        audit = pfw.audit_write_path(tmp_path, "isPremiumEligible")
        rendered = audit.render()
        assert rendered.startswith("[PERMISSION_FLAG_WRITE_PATH_AUDIT] isPremiumEligible:")
        assert "verdict=not_found" in rendered
        assert "(no assignment/setter sites found)" in rendered

    def test_render_lists_each_assignment_site(self, tmp_path: Path) -> None:
        pfw = _pfw()
        _write(
            tmp_path / "src" / "reducers" / "permissionReducer.ts",
            "isPremiumEligible = action.payload.value;\n",
        )
        audit = pfw.audit_write_path(tmp_path, "isPremiumEligible")
        rendered = audit.render()
        assert "src/reducers/permissionReducer.ts:1" in rendered


# ---------------------------------------------------------------------------
# find_placeholder_seam
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestFindPlaceholderSeam:
    def test_missing_repo_root_raises(self, tmp_path: Path) -> None:
        pfw = _pfw()
        with pytest.raises(FileNotFoundError, match="repo_root does not exist"):
            pfw.find_placeholder_seam(tmp_path / "nope")

    def test_no_seam_returns_none(self, tmp_path: Path) -> None:
        pfw = _pfw()
        _write(tmp_path / "src" / "reducers" / "permissionReducer.ts", "export {};\n")
        assert pfw.find_placeholder_seam(tmp_path) is None

    def test_finds_mock_permission_provider(self, tmp_path: Path) -> None:
        pfw = _pfw()
        _write(tmp_path / "src" / "providers" / "mockPermissionProvider.ts", "export {};\n")
        seam = pfw.find_placeholder_seam(tmp_path)
        assert seam == "src/providers/mockPermissionProvider.ts"

    def test_finds_placeholder_eligibility_seam(self, tmp_path: Path) -> None:
        pfw = _pfw()
        _write(tmp_path / "src" / "fixtures" / "placeholderEligibilityFlags.ts", "export {};\n")
        seam = pfw.find_placeholder_seam(tmp_path)
        assert seam == "src/fixtures/placeholderEligibilityFlags.ts"

    def test_excluded_dirs_are_skipped(self, tmp_path: Path) -> None:
        pfw = _pfw()
        _write(tmp_path / "node_modules" / "mockPermissionProvider.ts", "export {};\n")
        assert pfw.find_placeholder_seam(tmp_path) is None

    def test_deterministic_first_match_when_multiple(self, tmp_path: Path) -> None:
        pfw = _pfw()
        _write(tmp_path / "src" / "b" / "mockPermissionProvider.ts", "export {};\n")
        _write(tmp_path / "src" / "a" / "stubEntitlementProvider.ts", "export {};\n")
        seam = pfw.find_placeholder_seam(tmp_path)
        assert seam == "src/a/stubEntitlementProvider.ts"


# ---------------------------------------------------------------------------
# render_blocking_finding
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestRenderBlockingFinding:
    def test_renders_expected_shape(self, tmp_path: Path) -> None:
        pfw = _pfw()
        _write(
            tmp_path / "src" / "state" / "defaultState.ts",
            "isPremiumEligible: false,\n",
        )
        audit = pfw.audit_write_path(tmp_path, "isPremiumEligible")
        finding = pfw.render_blocking_finding("isTrialEligible", audit)
        assert finding.startswith("[BLOCKING_FINDING]")
        assert "isTrialEligible" in finding
        assert "isPremiumEligible" in finding
        assert "verdict=default_only" in finding
        assert "src/state/defaultState.ts:1" in finding

    def test_renders_none_found_when_no_sites(self, tmp_path: Path) -> None:
        pfw = _pfw()
        audit = pfw.audit_write_path(tmp_path, "isPremiumEligible")
        finding = pfw.render_blocking_finding("isTrialEligible", audit)
        assert "(none found)" in finding
