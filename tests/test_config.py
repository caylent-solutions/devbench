"""Tests for judges.config module."""

from __future__ import annotations

import importlib
import json
import os
from pathlib import Path
from unittest.mock import patch

import pytest

from devbench import config
from devbench.config import ALLOWED_REPOS, validate_repo


class TestAllowedRepos:
    """Verify ALLOWED_REPOS is driven exclusively by YAML config."""

    def test_judge_allowed_repos_is_frozenset(self) -> None:
        assert isinstance(ALLOWED_REPOS, frozenset)

    def test_allowed_repos_from_yaml(self) -> None:
        """ALLOWED_REPOS is sourced exclusively from YAML repos keys."""
        env = {k: v for k, v in os.environ.items() if k != "JUDGE_ALLOWED_REPOS"}
        with patch.dict(os.environ, env, clear=True):
            importlib.reload(config)
            assert isinstance(config.ALLOWED_REPOS, frozenset)
            assert len(config.ALLOWED_REPOS) > 0

        importlib.reload(config)

    def test_judge_allowed_repos_env_var_has_no_effect(self) -> None:
        """JUDGE_ALLOWED_REPOS env var is ignored — repos come from YAML only."""
        with patch.dict(os.environ, {"JUDGE_ALLOWED_REPOS": "org/repo-a,org/repo-b"}, clear=False):
            importlib.reload(config)
            assert frozenset({"org/repo-a", "org/repo-b"}) != config.ALLOWED_REPOS
            assert isinstance(config.ALLOWED_REPOS, frozenset)
            assert len(config.ALLOWED_REPOS) > 0

        importlib.reload(config)

    def test_validate_repo_passes_for_allowed_repo(self) -> None:
        repo = next(iter(ALLOWED_REPOS))
        validate_repo(repo)

    def test_validate_repo_raises_for_unknown_repo(self) -> None:
        with pytest.raises(ValueError, match="not allowed"):
            validate_repo("some-org/unknown-repo")

    def test_validate_repo_rejects_wrong_org_when_judge_gh_org_set(self) -> None:
        with patch.object(config, "ALLOWED_GH_ORG", "caylent-solutions"):
            with pytest.raises(ValueError, match="JUDGE_GH_ORG restricts access"):
                config.validate_repo("wrong-org/git-repo")

    def test_validate_repo_skips_org_check_when_judge_gh_org_empty(self) -> None:
        with patch.object(config, "ALLOWED_GH_ORG", ""):
            with pytest.raises(ValueError, match="not allowed"):
                config.validate_repo("other-org/some-repo")


class TestGetGhToken:
    """Test GitHub token retrieval from file and environment."""

    def test_get_gh_token_reads_from_file(self, tmp_path: Path) -> None:
        token_file = tmp_path / "gh_token"
        token_file.write_text("file-token-abc\n")

        with patch.object(config, "GH_TOKEN_FILE", token_file):
            result = config.get_gh_token()

        assert result == "file-token-abc"

    def test_get_gh_token_falls_back_to_env_var(self, tmp_path: Path) -> None:
        missing_file = tmp_path / "nonexistent_token"

        with (
            patch.object(config, "GH_TOKEN_FILE", missing_file),
            patch.dict(os.environ, {"GH_TOKEN": "env-token-xyz"}, clear=False),
        ):
            result = config.get_gh_token()

        assert result == "env-token-xyz"

    def test_get_gh_token_raises_when_neither_available(self, tmp_path: Path) -> None:
        missing_file = tmp_path / "nonexistent_token"

        with (
            patch.object(config, "GH_TOKEN_FILE", missing_file),
            patch.dict(os.environ, {"GH_TOKEN": ""}, clear=False),
        ):
            with pytest.raises(RuntimeError, match="GitHub token not found"):
                config.get_gh_token()

    def test_get_gh_token_ignores_empty_file(self, tmp_path: Path) -> None:
        token_file = tmp_path / "gh_token"
        token_file.write_text("   \n")

        with (
            patch.object(config, "GH_TOKEN_FILE", token_file),
            patch.dict(os.environ, {"GH_TOKEN": "fallback-token"}, clear=False),
        ):
            result = config.get_gh_token()

        assert result == "fallback-token"


class TestGetAnthropicApiKey:
    """Test Claude credential reading from credentials file."""

    def test_reads_token_from_credentials_file(self, tmp_path: Path) -> None:
        creds_file = tmp_path / "credentials.json"
        creds_file.write_text(json.dumps({
            "claudeAiOauth": {
                "accessToken": "sk-ant-oat01-test-token",
                "scopes": ["user:inference"],
            }
        }))

        with patch.object(config, "CLAUDE_CREDENTIALS_FILE", creds_file):
            result = config.get_anthropic_api_key()

        assert result == "sk-ant-oat01-test-token"

    def test_raises_when_file_missing(self, tmp_path: Path) -> None:
        missing = tmp_path / "nonexistent.json"

        with patch.object(config, "CLAUDE_CREDENTIALS_FILE", missing):
            with pytest.raises(RuntimeError, match="credentials file not found"):
                config.get_anthropic_api_key()

    def test_raises_when_no_oauth_section(self, tmp_path: Path) -> None:
        creds_file = tmp_path / "credentials.json"
        creds_file.write_text('{"other": "data"}')

        with patch.object(config, "CLAUDE_CREDENTIALS_FILE", creds_file):
            with pytest.raises(RuntimeError, match="claudeAiOauth"):
                config.get_anthropic_api_key()

    def test_raises_when_token_empty(self, tmp_path: Path) -> None:
        creds_file = tmp_path / "credentials.json"
        creds_file.write_text(json.dumps({
            "claudeAiOauth": {"accessToken": "  ", "scopes": []}
        }))

        with patch.object(config, "CLAUDE_CREDENTIALS_FILE", creds_file):
            with pytest.raises(RuntimeError, match="No access token"):
                config.get_anthropic_api_key()

    def test_raises_on_invalid_json(self, tmp_path: Path) -> None:
        creds_file = tmp_path / "credentials.json"
        creds_file.write_text("not json {{{")

        with patch.object(config, "CLAUDE_CREDENTIALS_FILE", creds_file):
            with pytest.raises(RuntimeError, match="Failed to read"):
                config.get_anthropic_api_key()


class TestMergeStrategy:
    """Test MergeStrategy enum values, flags, and env var validation."""

    def test_valid_values(self) -> None:
        from devbench.config import MergeStrategy

        assert MergeStrategy("merge") is MergeStrategy.MERGE
        assert MergeStrategy("squash") is MergeStrategy.SQUASH
        assert MergeStrategy("rebase") is MergeStrategy.REBASE

    def test_flag_property(self) -> None:
        from devbench.config import MergeStrategy

        assert MergeStrategy.MERGE.flag == "--merge"
        assert MergeStrategy.SQUASH.flag == "--squash"
        assert MergeStrategy.REBASE.flag == "--rebase"

    def test_invalid_value_raises_runtime_error(self) -> None:
        with patch.dict(os.environ, {"JUDGE_MERGE_STRATEGY": "fast-forward"}, clear=False):
            with pytest.raises(RuntimeError, match="JUDGE_MERGE_STRATEGY must be one of"):
                importlib.reload(config)

        importlib.reload(config)

    def test_default_is_squash(self) -> None:
        env_copy = {k: v for k, v in os.environ.items() if k != "JUDGE_MERGE_STRATEGY"}
        with patch.dict(os.environ, env_copy, clear=True):
            importlib.reload(config)
            assert config.MERGE_STRATEGY == config.MergeStrategy.SQUASH

        importlib.reload(config)


class TestConfigOverrides:
    """Test that config values can be overridden via environment variables."""

    def test_max_retry_attempts_from_env(self) -> None:
        with patch.dict(os.environ, {"JUDGE_MAX_RETRIES": "7"}, clear=False):
            importlib.reload(config)
            assert config.MAX_RETRY_ATTEMPTS == 7

        with patch.dict(os.environ, {"JUDGE_MAX_RETRIES": "3"}, clear=False):
            importlib.reload(config)

    def test_github_check_timeout_from_env(self) -> None:
        with patch.dict(os.environ, {"JUDGE_GH_TIMEOUT": "120"}, clear=False):
            importlib.reload(config)
            assert config.GITHUB_CHECK_TIMEOUT_SECONDS == 120

        with patch.dict(os.environ, {"JUDGE_GH_TIMEOUT": "600"}, clear=False):
            importlib.reload(config)

    def test_backlog_root_derived_from_workspace_root(self) -> None:
        """BACKLOG_ROOT is always derived from JUDGE_WORKSPACE_ROOT, not from env."""
        from devbench.constants import BACKLOG_SUBDIR

        env_copy = {k: v for k, v in os.environ.items() if k != "JUDGE_BACKLOG_ROOT"}
        with patch.dict(os.environ, env_copy, clear=True):
            importlib.reload(config)
            expected = Path(os.environ["JUDGE_WORKSPACE_ROOT"]) / BACKLOG_SUBDIR
            assert expected == config.BACKLOG_ROOT

        importlib.reload(config)

    def test_backlog_index_derived_from_workspace_root(self) -> None:
        """BACKLOG_INDEX is always derived from JUDGE_WORKSPACE_ROOT, not from env."""
        env_copy = {k: v for k, v in os.environ.items() if k != "JUDGE_BACKLOG_INDEX"}
        with patch.dict(os.environ, env_copy, clear=True):
            importlib.reload(config)
            expected = Path(os.environ["JUDGE_WORKSPACE_ROOT"]) / "BACKLOG.md"
            assert expected == config.BACKLOG_INDEX

        importlib.reload(config)

    def test_judge_backlog_root_env_var_has_no_effect(self, tmp_path: Path) -> None:
        """JUDGE_BACKLOG_ROOT env var is ignored — path derived from JUDGE_WORKSPACE_ROOT."""
        from devbench.constants import BACKLOG_SUBDIR

        custom_root = tmp_path / "custom-backlog"
        with patch.dict(os.environ, {"JUDGE_BACKLOG_ROOT": str(custom_root)}, clear=False):
            importlib.reload(config)
            expected = Path(os.environ["JUDGE_WORKSPACE_ROOT"]) / BACKLOG_SUBDIR
            assert expected == config.BACKLOG_ROOT
            assert custom_root != config.BACKLOG_ROOT

        importlib.reload(config)

    def test_judge_backlog_index_env_var_has_no_effect(self, tmp_path: Path) -> None:
        """JUDGE_BACKLOG_INDEX env var is ignored — path derived from JUDGE_WORKSPACE_ROOT."""
        custom_index = tmp_path / "CUSTOM_BACKLOG.md"
        with patch.dict(os.environ, {"JUDGE_BACKLOG_INDEX": str(custom_index)}, clear=False):
            importlib.reload(config)
            expected = Path(os.environ["JUDGE_WORKSPACE_ROOT"]) / "BACKLOG.md"
            assert expected == config.BACKLOG_INDEX
            assert custom_index != config.BACKLOG_INDEX

        importlib.reload(config)
