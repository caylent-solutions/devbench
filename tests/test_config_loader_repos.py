"""Tests for RepoConfig.local_only, relaxed repos key pattern, and per-repo precedence.

Covers AC-244-1 and AC-244a-1 from E7-F2-S1-T1:

- The repos key pattern admits two-segment keys (e.g. ``caylent/devbench``) and
  bare keys (e.g. ``workspace-local``).
- Three-segment keys (e.g. ``caylent/foo/bar``) and empty/trailing-slash keys
  (e.g. ``org/``) are rejected at schema validation.
- ``RepoConfig.local_only`` defaults to ``False`` and is parsed from YAML.
- effective_local_only is the per-repo value when set, otherwise the top-level
  ``git_ops.local_only``.
- At most one local_only repo is allowed; otherwise a ``ValueError`` is raised
  with the verbatim message ``at most one local_only repo is allowed; found: <ids>``.
- ensure-branch uses no ``git fetch origin`` for a local_only repo.
- git-ops-finalize is a no-op for a local_only repo.
"""

from __future__ import annotations

import textwrap
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from devbench.config_loader import (
    RepoConfig,
    load_runtime_config,
)


def _write_yaml(tmp_path: Path, content: str) -> Path:
    """Write *content* to a temp devbench.yaml and return the path."""
    cfg = tmp_path / "devbench.yaml"
    cfg.write_text(textwrap.dedent(content), encoding="utf-8")
    return cfg


@pytest.mark.unit
class TestReposKeyPattern:
    """Schema-level validation of the relaxed repos key pattern."""

    def test_two_segment_key_validates(self, tmp_path: Path) -> None:
        """caylent/devbench (two-segment) must be accepted by the schema."""
        cfg = _write_yaml(
            tmp_path,
            """\
            repos:
              caylent/devbench:
                default_branch: main
            """,
        )
        result = load_runtime_config(cfg, {})
        assert "caylent/devbench" in result.repos

    def test_bare_key_validates(self, tmp_path: Path) -> None:
        """workspace-local (bare, no slash) must be accepted by the schema."""
        cfg = _write_yaml(
            tmp_path,
            """\
            repos:
              workspace-local:
                default_branch: main
                local_only: true
            git_ops:
              defer_pr: true
              single_branch: feat/work
            """,
        )
        result = load_runtime_config(cfg, {})
        assert "workspace-local" in result.repos

    @pytest.mark.parametrize(
        "bad_key",
        [
            "caylent/foo/bar",
            "org/",
        ],
    )
    def test_invalid_keys_rejected(self, tmp_path: Path, bad_key: str) -> None:
        """Three-segment and empty-segment keys must be rejected at schema validation."""
        cfg = _write_yaml(
            tmp_path,
            f"""\
            repos:
              {bad_key}:
                default_branch: main
            """,
        )
        with pytest.raises(ValueError, match="schema validation"):
            load_runtime_config(cfg, {})


@pytest.mark.unit
class TestRepoConfigLocalOnly:
    """RepoConfig.local_only default and parsing from YAML."""

    def test_local_only_defaults_to_false(self) -> None:
        """RepoConfig() with no arguments must have local_only == False."""
        cfg = RepoConfig()
        assert cfg.local_only is False

    def test_local_only_parsed_as_true(self, tmp_path: Path) -> None:
        """local_only: true on a bare repo key is parsed into RepoConfig."""
        cfg = _write_yaml(
            tmp_path,
            """\
            repos:
              workspace-local:
                default_branch: main
                local_only: true
            git_ops:
              defer_pr: true
              single_branch: feat/work
            """,
        )
        result = load_runtime_config(cfg, {})
        assert result.repos["workspace-local"].local_only is True

    def test_local_only_parsed_as_false(self, tmp_path: Path) -> None:
        """local_only: false on a repo key is parsed into RepoConfig."""
        cfg = _write_yaml(
            tmp_path,
            """\
            repos:
              caylent/devbench:
                default_branch: main
                local_only: false
            """,
        )
        result = load_runtime_config(cfg, {})
        assert result.repos["caylent/devbench"].local_only is False

    def test_local_only_absent_defaults_to_false(self, tmp_path: Path) -> None:
        """When local_only is absent from a repo entry it defaults to False."""
        cfg = _write_yaml(
            tmp_path,
            """\
            repos:
              caylent/devbench:
                default_branch: main
            """,
        )
        result = load_runtime_config(cfg, {})
        assert result.repos["caylent/devbench"].local_only is False


@pytest.mark.unit
class TestEffectiveLocalOnly:
    """effective_local_only is per-repo if set, else top-level git_ops.local_only."""

    def test_per_repo_true_overrides_top_level_false(self, tmp_path: Path) -> None:
        """Per-repo local_only: true wins over git_ops.local_only: false."""
        cfg = _write_yaml(
            tmp_path,
            """\
            repos:
              workspace-local:
                default_branch: main
                local_only: true
            git_ops:
              defer_pr: true
              single_branch: feat/work
              local_only: false
            """,
        )
        result = load_runtime_config(cfg, {})
        repo = result.repos["workspace-local"]
        assert repo.local_only is True

    def test_per_repo_false_overrides_top_level_true(self, tmp_path: Path) -> None:
        """Per-repo local_only: false wins over git_ops.local_only: true
        for that specific repo; other repos inherit the top-level value."""
        cfg = _write_yaml(
            tmp_path,
            """\
            repos:
              workspace-local:
                default_branch: main
                local_only: true
              remote-repo:
                default_branch: main
                local_only: false
            git_ops:
              defer_pr: true
              single_branch: feat/work
              local_only: true
            """,
        )
        result = load_runtime_config(cfg, {})
        assert result.repos["workspace-local"].local_only is True
        assert result.repos["remote-repo"].local_only is False

    def test_top_level_local_only_inherited_when_per_repo_unset(self, tmp_path: Path) -> None:
        """When per-repo local_only is absent, git_ops.local_only is the effective value."""
        cfg = _write_yaml(
            tmp_path,
            """\
            repos:
              workspace-local:
                default_branch: main
            git_ops:
              defer_pr: true
              single_branch: feat/work
              local_only: true
            """,
        )
        result = load_runtime_config(cfg, {})
        assert result.repos["workspace-local"].local_only is True


@pytest.mark.unit
class TestAtMostOneLocalOnly:
    """At most one local_only repo is allowed."""

    def test_two_local_only_repos_raise_value_error(self, tmp_path: Path) -> None:
        """Two repos with effective local_only == True raise the verbatim ValueError."""
        cfg = _write_yaml(
            tmp_path,
            """\
            repos:
              workspace-alpha:
                default_branch: main
                local_only: true
              workspace-beta:
                default_branch: main
                local_only: true
            git_ops:
              defer_pr: true
              single_branch: feat/work
            """,
        )
        with pytest.raises(ValueError, match="at most one local_only repo is allowed; found:"):
            load_runtime_config(cfg, {})

    def test_two_local_only_repos_error_names_both_ids(self, tmp_path: Path) -> None:
        """The ValueError message lists all local_only repo ids."""
        cfg = _write_yaml(
            tmp_path,
            """\
            repos:
              workspace-alpha:
                default_branch: main
                local_only: true
              workspace-beta:
                default_branch: main
                local_only: true
            git_ops:
              defer_pr: true
              single_branch: feat/work
            """,
        )
        with pytest.raises(ValueError) as exc_info:
            load_runtime_config(cfg, {})
        msg = str(exc_info.value)
        assert "workspace-alpha" in msg
        assert "workspace-beta" in msg

    def test_one_local_only_repo_ok(self, tmp_path: Path) -> None:
        """Exactly one local_only repo must not raise."""
        cfg = _write_yaml(
            tmp_path,
            """\
            repos:
              workspace-local:
                default_branch: main
                local_only: true
            git_ops:
              defer_pr: true
              single_branch: feat/work
            """,
        )
        result = load_runtime_config(cfg, {})
        assert result.repos["workspace-local"].local_only is True

    def test_top_level_local_only_single_repo_ok(self, tmp_path: Path) -> None:
        """git_ops.local_only with a single repo and no per-repo override must not raise."""
        cfg = _write_yaml(
            tmp_path,
            """\
            repos:
              workspace-only:
                default_branch: main
            git_ops:
              defer_pr: true
              single_branch: feat/work
              local_only: true
            """,
        )
        result = load_runtime_config(cfg, {})
        assert result.repos["workspace-only"].local_only is True

    def test_top_level_local_only_two_repos_raise(self, tmp_path: Path) -> None:
        """git_ops.local_only: true with two repos and no per-repo overrides raises."""
        cfg = _write_yaml(
            tmp_path,
            """\
            repos:
              workspace-alpha:
                default_branch: main
              workspace-beta:
                default_branch: main
            git_ops:
              defer_pr: true
              single_branch: feat/work
              local_only: true
            """,
        )
        with pytest.raises(ValueError, match="at most one local_only repo is allowed; found:"):
            load_runtime_config(cfg, {})


@pytest.mark.unit
class TestEnsureBranchLocalOnly:
    """ensure_branch must not call git fetch origin for a local_only repo."""

    def test_ensure_branch_skips_fetch_origin_for_local_only(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Per-repo local_only: True causes ensure_branch to skip git fetch origin
        even when git_ops.local_only is False (proving per-repo precedence over
        the top-level fallback via _effective_local_only)."""
        from devbench.github.git_ops import GitOpsService

        repo_path = tmp_path / "repo"
        repo_path.mkdir()

        def _fake_run_command(cmd: list[str], cwd: object = None) -> tuple[int, str, str]:
            if cmd == ["git", "fetch", "origin"]:
                raise AssertionError("git fetch origin must not be called for local_only repo")
            if cmd[:2] == ["git", "rev-parse"]:
                return 0, "some-other-branch\n", ""
            if cmd[:2] == ["git", "status"]:
                return 0, "", ""
            if cmd[:2] == ["git", "show-ref"]:
                return 1, "", ""
            return 0, "main\n", ""

        mock_repo_cfg = MagicMock()
        mock_repo_cfg.local_only = True

        mock_config = MagicMock()
        mock_config.git_ops.local_only = False
        mock_config.repos = {"workspace-local": mock_repo_cfg}

        with (
            patch("devbench.github.git_ops.run_command", side_effect=_fake_run_command),
            patch("devbench.github.git_ops.RUNTIME_CONFIG", mock_config),
            patch("devbench.github.git_ops.validate_repo"),
        ):
            ops = GitOpsService.__new__(GitOpsService)
            ops.logger = MagicMock()
            git_mock = MagicMock(return_value=(0, "main\n", ""))
            monkeypatch.setattr(ops, "_git", git_mock)
            monkeypatch.setattr(ops, "_get_default_branch", MagicMock(return_value="main"))

            ops.ensure_branch("workspace-local", repo_path, "feat/work")

        for call in git_mock.call_args_list:
            args = call[0][0] if call[0] else []
            assert args[:2] != ["fetch", "origin"], f"Unexpected fetch origin call: {call}"


@pytest.mark.unit
class TestGitOpsFinalizeLocalOnly:
    """git-ops-finalize must return 0 (no-op) for a local_only repo."""

    def test_git_ops_finalize_noop_for_local_only(self, tmp_path: Path) -> None:
        """When the effective repo is local_only, cmd_git_ops_finalize returns 0 immediately."""
        import devbench.config as devbench_config
        from devbench.cli import cmd_git_ops_finalize

        mock_repo_cfg = MagicMock()
        mock_repo_cfg.local_only = True

        original_single_branch = devbench_config.SINGLE_BRANCH
        original_defer_pr = devbench_config.DEFER_PR
        try:
            devbench_config.SINGLE_BRANCH = "feat/work"
            devbench_config.DEFER_PR = True

            with (
                patch("devbench.cli.resolve_repo", return_value="workspace-local"),
                patch("devbench.cli.validate_repo"),
                patch("devbench.cli.REPO_LOCAL_PATHS", {"workspace-local": tmp_path}),
                patch("devbench.cli.RUNTIME_CONFIG") as mock_rt,
            ):
                mock_rt.repos = {"workspace-local": mock_repo_cfg}

                result = cmd_git_ops_finalize("workspace-local")
        finally:
            devbench_config.SINGLE_BRANCH = original_single_branch
            devbench_config.DEFER_PR = original_defer_pr

        assert result == 0, f"Expected 0 (no-op) for local_only repo, got {result}"
