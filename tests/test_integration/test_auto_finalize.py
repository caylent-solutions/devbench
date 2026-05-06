"""End-to-end integration tests for auto_finalize and auto_merge toggle matrix.

Covers AC-FUNC-005..008 and AC-CYCLE-001.

Each test builds a minimal YAML fixture, loads the config via
``load_runtime_config``, and asserts the resolved field values and the
cross-field validation rules match the specification exactly.

The "invoke git-ops-finalize / gh pr merge" paths belong to the orchestrate
SKILL.md (prose-based) and are verified here through the config-layer
invariants that the skill branches on:

- ``git_ops.auto_finalize`` and ``git_ops.auto_merge`` are exposed to the
  skill as ``AUTO_FINALIZE`` and ``AUTO_MERGE`` constants from ``config.py``.
- The validation rules embedded in ``load_runtime_config`` guarantee that any
  combination that would produce undefined behaviour is caught at config-load
  time rather than at runtime.

Toggle matrix exercised:
  off/off   -- both defaults False: no finalize, no merge
  on/off    -- auto_finalize True, auto_merge False: finalize runs, no merge
  on/on     -- both True (with required companions): finalize runs, merge runs
  local_only edge case: auto_finalize + local_only raises
  watcher-absent edge case: auto_merge without E7 watcher emits skip audit
"""

from __future__ import annotations

import textwrap
from pathlib import Path

import pytest

from devbench.config_loader import load_runtime_config


@pytest.mark.integration
class TestAutoFinalizeToggleMatrix:
    """AC-CYCLE-001: full off/off, on/off, on/on toggle matrix."""

    def _write(self, path: Path, content: str) -> Path:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(textwrap.dedent(content), encoding="utf-8")
        return path

    def test_off_off_both_defaults_false(self, tmp_path: Path) -> None:
        """Toggle matrix row: off/off -- both defaults False, no finalize, no merge."""
        cfg = self._write(
            tmp_path / "cfg.yaml",
            """\
            repos:
              org/repo:
                default_branch: main
            """,
        )
        rt = load_runtime_config(cfg, {})
        assert rt.git_ops.auto_finalize is False, "auto_finalize must default to False"
        assert rt.git_ops.auto_merge is False, "auto_merge must default to False"

    def test_on_off_auto_finalize_without_auto_merge(self, tmp_path: Path) -> None:
        """Toggle matrix row: on/off -- finalize runs, no merge.

        auto_finalize: true requires defer_pr: true (and single_branch).
        auto_merge stays False.
        """
        cfg = self._write(
            tmp_path / "cfg.yaml",
            """\
            repos:
              org/repo:
                default_branch: main
            git_ops:
              single_branch: feat/batch
              defer_pr: true
              auto_finalize: true
            """,
        )
        rt = load_runtime_config(cfg, {})
        assert rt.git_ops.auto_finalize is True, "auto_finalize must be True"
        assert rt.git_ops.auto_merge is False, "auto_merge must remain False"
        assert rt.git_ops.defer_pr is True, "defer_pr must be True when auto_finalize is True"

    def test_on_on_both_toggles_with_required_companions(self, tmp_path: Path) -> None:
        """Toggle matrix row: on/on -- both finalize and merge configured.

        Requires: defer_pr: true, single_branch set, auto_finalize: true.
        """
        cfg = self._write(
            tmp_path / "cfg.yaml",
            """\
            repos:
              org/repo:
                default_branch: main
            git_ops:
              single_branch: feat/batch
              defer_pr: true
              auto_finalize: true
              auto_merge: true
            """,
        )
        rt = load_runtime_config(cfg, {})
        assert rt.git_ops.auto_finalize is True, "auto_finalize must be True"
        assert rt.git_ops.auto_merge is True, "auto_merge must be True"
        assert rt.git_ops.defer_pr is True, "defer_pr must be True"


@pytest.mark.integration
class TestAutoFinalizeEdgeCases:
    """AC-FUNC-005..008: edge cases validated at config-load time."""

    def _write(self, path: Path, content: str) -> Path:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(textwrap.dedent(content), encoding="utf-8")
        return path

    def test_auto_finalize_requires_defer_pr(self, tmp_path: Path) -> None:
        """AC-FUNC-008 analogue: auto_finalize + local_only is blocked because
        the skill emits [AUTO_FINALIZE_SKIPPED] local_only=true.
        The config layer enforces local_only requires defer_pr, and
        auto_finalize also requires defer_pr -- both load cleanly when
        defer_pr and single_branch are provided together.

        This test confirms the rejection of auto_finalize: true without defer_pr.
        """
        cfg = self._write(
            tmp_path / "cfg.yaml",
            """\
            repos:
              org/repo:
                default_branch: main
            git_ops:
              auto_finalize: true
            """,
        )
        with pytest.raises(ValueError, match=r"auto_finalize: true requires .*defer_pr: true"):
            load_runtime_config(cfg, {})

    def test_auto_merge_requires_auto_finalize(self, tmp_path: Path) -> None:
        """AC-FUNC-003: auto_merge without auto_finalize is rejected."""
        cfg = self._write(
            tmp_path / "cfg.yaml",
            """\
            repos:
              org/repo:
                default_branch: main
            git_ops:
              single_branch: feat/batch
              defer_pr: true
              auto_merge: true
            """,
        )
        with pytest.raises(ValueError, match=r"auto_merge: true requires .*auto_finalize: true"):
            load_runtime_config(cfg, {})

    def test_auto_merge_with_local_only_rejected(self, tmp_path: Path) -> None:
        """AC-FUNC-004: auto_merge: true + local_only: true is rejected.

        local_only repos have no remote -- there is no PR to merge.
        The config loader must reject this combination. The auto_finalize +
        local_only check fires first (before the auto_merge + local_only check)
        because both are incompatible with local_only; either check satisfies
        the AC requirement that the combination is rejected.
        """
        cfg = self._write(
            tmp_path / "cfg.yaml",
            """\
            repos:
              org/repo:
                default_branch: main
            git_ops:
              single_branch: feat/batch
              defer_pr: true
              local_only: true
              auto_finalize: true
              auto_merge: true
            """,
        )
        with pytest.raises(ValueError, match=r"local_only: true"):
            load_runtime_config(cfg, {})

    def test_auto_finalize_with_local_only_rejected(self, tmp_path: Path) -> None:
        """AC-FUNC-008: auto_finalize: true + local_only: true is rejected.

        local_only repos have no remote -- git-ops-finalize cannot push or
        create a PR. The skill emits [AUTO_FINALIZE_SKIPPED] local_only=true
        as an audit row; this test asserts the config-layer rejects it eagerly
        so operators learn about the conflict at startup, not at runtime.
        """
        cfg = self._write(
            tmp_path / "cfg.yaml",
            """\
            repos:
              org/repo:
                default_branch: main
            git_ops:
              single_branch: feat/batch
              defer_pr: true
              local_only: true
              auto_finalize: true
            """,
        )
        with pytest.raises(ValueError, match=r"auto_finalize: true.*local_only: true"):
            load_runtime_config(cfg, {})

    def test_config_constants_exposed_auto_finalize(self, tmp_path: Path) -> None:
        """AC-FUNC-005 prerequisite: AUTO_FINALIZE constant is exported from config.py.

        The orchestrate skill reads ``AUTO_FINALIZE`` from ``devbench.config``.
        This test verifies the field is present on the RuntimeConfig loaded by
        config.py and that the config.py module exposes the constants at the
        module level. Rather than reloading config.py (which causes test isolation
        issues because config.py is a module-level singleton), we verify the
        contract via the config_loader directly and the module attribute list.
        """
        import devbench.config as cfg_mod

        # Verify the module exports the constants at module level.
        assert hasattr(cfg_mod, "AUTO_FINALIZE"), "devbench.config must export AUTO_FINALIZE"
        assert hasattr(cfg_mod, "AUTO_MERGE"), "devbench.config must export AUTO_MERGE"
        # Both are booleans.
        assert isinstance(cfg_mod.AUTO_FINALIZE, bool), "AUTO_FINALIZE must be a bool"
        assert isinstance(cfg_mod.AUTO_MERGE, bool), "AUTO_MERGE must be a bool"

    def test_config_constants_exposed_auto_merge(self, tmp_path: Path) -> None:
        """AC-FUNC-006 prerequisite: AUTO_MERGE constant is exported from config.py.

        Verifies the module-level attribute exists and is a bool. The actual
        value depends on the test workspace's devbench.yaml (which defaults
        auto_merge to False), so we assert type and presence rather than value.
        """
        import devbench.config as cfg_mod

        assert hasattr(cfg_mod, "AUTO_MERGE"), "devbench.config must export AUTO_MERGE"
        assert isinstance(cfg_mod.AUTO_MERGE, bool), "AUTO_MERGE must be a bool"
        # Verify that when auto_merge is False in the loaded config, AUTO_FINALIZE
        # is also False (since auto_merge requires auto_finalize).
        if cfg_mod.AUTO_MERGE:
            assert cfg_mod.AUTO_FINALIZE, "AUTO_FINALIZE must be True when AUTO_MERGE is True"

    def test_auto_merge_skipped_audit_label_watcher_absent(self) -> None:
        """AC-FUNC-007: when the E7 watcher is absent, the skill emits
        [AUTO_MERGE_SKIPPED] no_ci_watcher and does NOT merge.

        The audit label string itself is the contract with the orchestrate
        SKILL.md prose. This test asserts the constant is defined and
        matches the spec exactly so that any refactor that renames the
        string will fail here first.
        """
        from devbench.config_loader import AUTO_MERGE_SKIPPED_NO_CI_WATCHER

        assert AUTO_MERGE_SKIPPED_NO_CI_WATCHER == "[AUTO_MERGE_SKIPPED] no_ci_watcher"

    def test_auto_finalize_skipped_audit_label_local_only(self) -> None:
        """AC-FUNC-008: when local_only is true the skill emits
        [AUTO_FINALIZE_SKIPPED] local_only=true as an audit row.

        Analogous to the watcher-absent test: the string constant is the
        contract with the SKILL.md prose.
        """
        from devbench.config_loader import AUTO_FINALIZE_SKIPPED_LOCAL_ONLY

        assert AUTO_FINALIZE_SKIPPED_LOCAL_ONLY == "[AUTO_FINALIZE_SKIPPED] local_only=true"

    def test_batch_pr_created_audit_label(self) -> None:
        """AC-FUNC-005: on successful auto-finalize the skill emits
        [BATCH_PR_CREATED] <pr_url>.

        The prefix constant is pinned here so SKILL.md refactors are caught.
        """
        from devbench.config_loader import BATCH_PR_CREATED_AUDIT_PREFIX

        assert BATCH_PR_CREATED_AUDIT_PREFIX == "[BATCH_PR_CREATED]"

    def test_batch_pr_merged_audit_label(self) -> None:
        """AC-FUNC-006: on successful auto-merge the skill emits
        [BATCH_PR_MERGED] <pr_url>.
        """
        from devbench.config_loader import BATCH_PR_MERGED_AUDIT_PREFIX

        assert BATCH_PR_MERGED_AUDIT_PREFIX == "[BATCH_PR_MERGED]"
