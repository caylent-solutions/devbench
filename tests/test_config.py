"""Tests for judges.config module."""

from __future__ import annotations

import os
from pathlib import Path
from unittest.mock import patch

import pytest


class TestAllowedRepos:
    """Verify ALLOWED_REPOS constant and validate_repo behaviour."""

    def test_allowed_repos_is_frozenset(self) -> None:
        from devbench.config import ALLOWED_REPOS

        assert isinstance(ALLOWED_REPOS, frozenset)

    def test_allowed_repos_has_exactly_four_items(self) -> None:
        from devbench.config import ALLOWED_REPOS

        assert len(ALLOWED_REPOS) == 4

    @pytest.mark.parametrize(
        "repo",
        [
            "caylent-solutions/git-repo",
            "caylent-solutions/caylent-private-rpm",
            "caylent-solutions/rpm-claude-marketplaces",
            "caylent-solutions/rpm-claude-marketplaces-install",
        ],
    )
    def test_validate_repo_passes_for_allowed_repo(self, repo: str) -> None:
        from devbench.config import validate_repo

        # Should not raise
        validate_repo(repo)

    def test_validate_repo_raises_for_unknown_repo(self) -> None:
        from devbench.config import validate_repo

        with pytest.raises(ValueError, match="not allowed"):
            validate_repo("some-org/unknown-repo")

    def test_validate_repo_rejects_wrong_org_when_judge_gh_org_set(self) -> None:
        from devbench import config

        with patch.object(config, "ALLOWED_GH_ORG", "caylent-solutions"):
            with pytest.raises(ValueError, match="JUDGE_GH_ORG restricts access"):
                config.validate_repo("wrong-org/git-repo")

    def test_validate_repo_skips_org_check_when_judge_gh_org_empty(self) -> None:
        from devbench import config

        with patch.object(config, "ALLOWED_GH_ORG", ""):
            # Should still fail on allow-list, not org check
            with pytest.raises(ValueError, match="not allowed"):
                config.validate_repo("other-org/some-repo")


class TestGetGhToken:
    """Test GitHub token retrieval from file and environment."""

    def test_get_gh_token_reads_from_file(self, tmp_path: Path) -> None:
        from devbench import config

        token_file = tmp_path / "gh_token"
        token_file.write_text("file-token-abc\n")

        with patch.object(config, "GH_TOKEN_FILE", token_file):
            result = config.get_gh_token()

        assert result == "file-token-abc"

    def test_get_gh_token_falls_back_to_env_var(self, tmp_path: Path) -> None:
        from devbench import config

        # Point to a file that does not exist
        missing_file = tmp_path / "nonexistent_token"

        with (
            patch.object(config, "GH_TOKEN_FILE", missing_file),
            patch.dict(os.environ, {"GH_TOKEN": "env-token-xyz"}, clear=False),
        ):
            result = config.get_gh_token()

        assert result == "env-token-xyz"

    def test_get_gh_token_raises_when_neither_available(self, tmp_path: Path) -> None:
        from devbench import config

        missing_file = tmp_path / "nonexistent_token"

        with (
            patch.object(config, "GH_TOKEN_FILE", missing_file),
            patch.dict(os.environ, {"GH_TOKEN": ""}, clear=False),
        ):
            with pytest.raises(RuntimeError, match="GitHub token not found"):
                config.get_gh_token()

    def test_get_gh_token_ignores_empty_file(self, tmp_path: Path) -> None:
        from devbench import config

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
        import json

        from devbench import config

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
        from devbench import config

        missing = tmp_path / "nonexistent.json"

        with patch.object(config, "CLAUDE_CREDENTIALS_FILE", missing):
            with pytest.raises(RuntimeError, match="credentials file not found"):
                config.get_anthropic_api_key()

    def test_raises_when_no_oauth_section(self, tmp_path: Path) -> None:
        from devbench import config

        creds_file = tmp_path / "credentials.json"
        creds_file.write_text('{"other": "data"}')

        with patch.object(config, "CLAUDE_CREDENTIALS_FILE", creds_file):
            with pytest.raises(RuntimeError, match="claudeAiOauth"):
                config.get_anthropic_api_key()

    def test_raises_when_token_empty(self, tmp_path: Path) -> None:
        import json

        from devbench import config

        creds_file = tmp_path / "credentials.json"
        creds_file.write_text(json.dumps({
            "claudeAiOauth": {"accessToken": "  ", "scopes": []}
        }))

        with patch.object(config, "CLAUDE_CREDENTIALS_FILE", creds_file):
            with pytest.raises(RuntimeError, match="No access token"):
                config.get_anthropic_api_key()

    def test_raises_on_invalid_json(self, tmp_path: Path) -> None:
        from devbench import config

        creds_file = tmp_path / "credentials.json"
        creds_file.write_text("not json {{{")

        with patch.object(config, "CLAUDE_CREDENTIALS_FILE", creds_file):
            with pytest.raises(RuntimeError, match="Failed to read"):
                config.get_anthropic_api_key()


class TestConfigOverrides:
    """Test that config values can be overridden via environment variables."""

    def test_max_retry_attempts_from_env(self) -> None:
        with patch.dict(os.environ, {"JUDGE_MAX_RETRIES": "7"}, clear=False):
            # Re-import to pick up env var
            import importlib

            from devbench import config

            importlib.reload(config)
            assert config.MAX_RETRY_ATTEMPTS == 7

            # Restore default
            with patch.dict(os.environ, {"JUDGE_MAX_RETRIES": "3"}, clear=False):
                importlib.reload(config)

    def test_github_check_timeout_from_env(self) -> None:
        with patch.dict(os.environ, {"JUDGE_GH_TIMEOUT": "120"}, clear=False):
            import importlib

            from devbench import config

            importlib.reload(config)
            assert config.GITHUB_CHECK_TIMEOUT_SECONDS == 120

            # Restore default
            with patch.dict(os.environ, {"JUDGE_GH_TIMEOUT": "600"}, clear=False):
                importlib.reload(config)

    def test_backlog_root_from_env(self, tmp_path: Path) -> None:
        custom_root = str(tmp_path / "custom-backlog")
        with patch.dict(os.environ, {"JUDGE_BACKLOG_ROOT": custom_root}, clear=False):
            import importlib

            from devbench import config

            importlib.reload(config)
            assert Path(custom_root) == config.BACKLOG_ROOT

            # Restore
            env_copy = os.environ.copy()
            env_copy.pop("JUDGE_BACKLOG_ROOT", None)
            with patch.dict(os.environ, env_copy, clear=True):
                importlib.reload(config)
