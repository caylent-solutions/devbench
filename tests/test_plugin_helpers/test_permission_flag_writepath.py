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

    def test_non_source_extension_is_skipped(self, tmp_path: Path) -> None:
        pfw = _pfw()
        _write(
            tmp_path / "docs" / "notes.md",
            "isPremiumEligible = true\n",
        )
        audit = pfw.audit_write_path(tmp_path, "isPremiumEligible")
        assert audit.verdict == "not_found"
        assert audit.mention_count == 0

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
