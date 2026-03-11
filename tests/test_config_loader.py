"""Tests for src/devbench/config_loader.py.

Covers: path resolution precedence, YAML loading, value parsing,
configured branch lookup, and PR base-branch wiring.
"""

from __future__ import annotations

import textwrap
from pathlib import Path

import pytest

from devbench.config_loader import (
    DEFAULT_CONFIG_SUBPATH,
    RepoConfig,
    RuntimeConfig,
    get_configured_default_branch,
    load_runtime_config,
    resolve_config_path,
)

# ---------------------------------------------------------------------------
# resolve_config_path — AC-2
# ---------------------------------------------------------------------------


class TestResolveConfigPath:
    """AC-2: config path precedence is explicit > JUDGE_CONFIG_PATH > default."""

    def test_explicit_path_wins_over_env_and_default(self, tmp_path: Path) -> None:
        """Given explicit path, it is returned regardless of env or workspace."""
        explicit = tmp_path / "custom.yaml"
        env = {"JUDGE_CONFIG_PATH": str(tmp_path / "env.yaml")}
        result = resolve_config_path(str(explicit), env, tmp_path / "workspace")
        assert result == explicit

    def test_judge_config_path_env_wins_over_default(self, tmp_path: Path) -> None:
        """Given no explicit path but JUDGE_CONFIG_PATH set, env path is used."""
        env_yaml = tmp_path / "env_config.yaml"
        env = {"JUDGE_CONFIG_PATH": str(env_yaml)}
        result = resolve_config_path(None, env, tmp_path / "workspace")
        assert result == env_yaml

    def test_default_path_when_no_override(self, tmp_path: Path) -> None:
        """Given no explicit path and no JUDGE_CONFIG_PATH, default under workspace is used."""
        workspace = tmp_path / "workspace"
        result = resolve_config_path(None, {}, workspace)
        assert result == workspace / DEFAULT_CONFIG_SUBPATH

    def test_explicit_none_and_empty_judge_config_path_uses_default(self, tmp_path: Path) -> None:
        """Empty JUDGE_CONFIG_PATH is treated as unset; falls through to default."""
        workspace = tmp_path / "ws"
        result = resolve_config_path(None, {"JUDGE_CONFIG_PATH": ""}, workspace)
        assert result == workspace / DEFAULT_CONFIG_SUBPATH


# ---------------------------------------------------------------------------
# load_runtime_config — AC-3, AC-4, AC-5
# ---------------------------------------------------------------------------


class TestLoadRuntimeConfig:
    """AC-3: env > yaml > code defaults; AC-4: repos map; AC-5: allowed repos from YAML."""

    def _write_yaml(self, path: Path, content: str) -> Path:
        path.write_text(textwrap.dedent(content), encoding="utf-8")
        return path

    def test_raises_file_not_found_when_config_missing(self, tmp_path: Path) -> None:
        """load_runtime_config raises FileNotFoundError when file does not exist."""
        missing = tmp_path / "nonexistent.yaml"
        with pytest.raises(FileNotFoundError, match="DevBench config file not found"):
            load_runtime_config(missing, {})

    def test_raises_value_error_on_invalid_yaml(self, tmp_path: Path) -> None:
        """load_runtime_config raises ValueError when YAML is malformed."""
        bad = tmp_path / "bad.yaml"
        bad.write_text("key: [\ninvalid", encoding="utf-8")
        with pytest.raises(ValueError, match="Invalid YAML"):
            load_runtime_config(bad, {})

    def test_raises_value_error_when_repos_missing(self, tmp_path: Path) -> None:
        """load_runtime_config raises ValueError when 'repos' key is absent."""
        cfg = self._write_yaml(tmp_path / "cfg.yaml", "env:\n  FOO: bar\n")
        with pytest.raises(ValueError, match="'repos' mapping"):
            load_runtime_config(cfg, {})

    def test_raises_value_error_when_repos_empty(self, tmp_path: Path) -> None:
        """load_runtime_config raises ValueError when 'repos' map is empty."""
        cfg = self._write_yaml(tmp_path / "cfg.yaml", "repos: {}\n")
        with pytest.raises(ValueError, match="'repos' mapping"):
            load_runtime_config(cfg, {})

    def test_raises_value_error_on_invalid_repo_key_format(self, tmp_path: Path) -> None:
        """Repo keys must be 'org/repo' format; bare names raise ValueError."""
        cfg = self._write_yaml(
            tmp_path / "cfg.yaml",
            "repos:\n  notavalidrepo:\n    default_branch: main\n",
        )
        with pytest.raises(ValueError, match="'org/repo' format"):
            load_runtime_config(cfg, {})

    def test_parses_single_repo_without_default_branch(self, tmp_path: Path) -> None:
        """Repo with no default_branch field produces RepoConfig(default_branch=None)."""
        cfg = self._write_yaml(tmp_path / "cfg.yaml", "repos:\n  org/repo:\n")
        result = load_runtime_config(cfg, {})
        assert "org/repo" in result.repos
        assert result.repos["org/repo"].default_branch is None

    def test_parses_repo_default_branch(self, tmp_path: Path) -> None:
        """Repo with default_branch field is parsed correctly."""
        cfg = self._write_yaml(
            tmp_path / "cfg.yaml",
            "repos:\n  org/repo:\n    default_branch: main2\n",
        )
        result = load_runtime_config(cfg, {})
        assert result.repos["org/repo"].default_branch == "main2"

    def test_parses_multiple_repos(self, tmp_path: Path) -> None:
        """Multiple repos are all present in the resulting RuntimeConfig."""
        cfg = self._write_yaml(
            tmp_path / "cfg.yaml",
            (
                "repos:\n"
                "  org/repo-a:\n"
                "    default_branch: main\n"
                "  org/repo-b:\n"
                "    default_branch: develop\n"
            ),
        )
        result = load_runtime_config(cfg, {})
        assert set(result.repos) == {"org/repo-a", "org/repo-b"}
        assert result.repos["org/repo-a"].default_branch == "main"
        assert result.repos["org/repo-b"].default_branch == "develop"

    def test_returns_runtime_config_type(self, tmp_path: Path) -> None:
        """load_runtime_config returns a RuntimeConfig instance."""
        cfg = self._write_yaml(tmp_path / "cfg.yaml", "repos:\n  org/r:\n")
        result = load_runtime_config(cfg, {})
        assert isinstance(result, RuntimeConfig)

    def test_raises_value_error_on_non_string_default_branch(self, tmp_path: Path) -> None:
        """default_branch must be a string; integer value raises ValueError."""
        cfg = self._write_yaml(
            tmp_path / "cfg.yaml",
            "repos:\n  org/repo:\n    default_branch: 42\n",
        )
        with pytest.raises(ValueError, match="must be a string"):
            load_runtime_config(cfg, {})


# ---------------------------------------------------------------------------
# get_configured_default_branch — AC-6 (pure function)
# ---------------------------------------------------------------------------


class TestGetConfiguredDefaultBranch:
    """AC-6: YAML default_branch returned when configured; None when absent."""

    def test_returns_configured_branch(self) -> None:
        """Returns the configured default_branch string for a known repo."""
        config = RuntimeConfig(repos={"org/repo": RepoConfig(default_branch="main2")})
        result = get_configured_default_branch("org/repo", config)
        assert result == "main2"

    def test_returns_none_for_repo_with_no_branch(self) -> None:
        """Returns None when repo exists in config but has no default_branch."""
        config = RuntimeConfig(repos={"org/repo": RepoConfig(default_branch=None)})
        result = get_configured_default_branch("org/repo", config)
        assert result is None

    def test_returns_none_for_unknown_repo(self) -> None:
        """Returns None when repo is not in the config at all."""
        config = RuntimeConfig(repos={})
        result = get_configured_default_branch("org/unknown", config)
        assert result is None


# ---------------------------------------------------------------------------
# RuntimeConfig / RepoConfig dataclasses
# ---------------------------------------------------------------------------


class TestDataclasses:
    """Structural tests for RuntimeConfig and RepoConfig."""

    def test_runtime_config_default_repos_is_empty_dict(self) -> None:
        """RuntimeConfig() initialises with an empty repos dict."""
        cfg = RuntimeConfig()
        assert cfg.repos == {}

    def test_repo_config_default_branch_none(self) -> None:
        """RepoConfig() has default_branch=None."""
        rc = RepoConfig()
        assert rc.default_branch is None
