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

# ---------------------------------------------------------------------------
# Test constants derived from the test fixture (tests/fixtures/test_devbench.yaml)
# so that test data is not embedded as inline deployment-configuration literals.
# The fixture defines repos under "caylent-solutions"; these constants mirror that.
# ---------------------------------------------------------------------------
_FIXTURE_ORG = "caylent-solutions"
_ALLOWED_REPO_IN_FIXTURE = f"{_FIXTURE_ORG}/git-repo"
_UNKNOWN_REPO = "test-sentinel-org/unknown-repo"  # deliberately absent from fixture
_WRONG_ORG_REPO = "wrong-org/git-repo"  # org not matching _FIXTURE_ORG


@pytest.mark.unit
class TestAllowedRepos:
    """Verify ALLOWED_REPOS is driven exclusively by YAML config."""

    def test_judge_allowed_repos_is_frozenset(self) -> None:
        assert isinstance(ALLOWED_REPOS, frozenset), (
            f"Expected ALLOWED_REPOS to be a frozenset, got {type(ALLOWED_REPOS).__name__}"
        )

    def test_allowed_repos_from_yaml(self) -> None:
        """ALLOWED_REPOS is sourced exclusively from YAML repos keys."""
        env = {k: v for k, v in os.environ.items() if k != "JUDGE_ALLOWED_REPOS"}
        with patch.dict(os.environ, env, clear=True):
            importlib.reload(config)
            assert isinstance(config.ALLOWED_REPOS, frozenset), (
                f"Expected ALLOWED_REPOS to be a frozenset after reload, got {type(config.ALLOWED_REPOS).__name__}"
            )
            assert len(config.ALLOWED_REPOS) > 0, "Expected ALLOWED_REPOS to be non-empty (sourced from YAML fixture)"

        importlib.reload(config)

    def test_judge_allowed_repos_env_var_has_no_effect(self) -> None:
        """JUDGE_ALLOWED_REPOS env var is ignored — repos come from YAML only."""
        # Capture the baseline ALLOWED_REPOS before patching.
        baseline = frozenset(config.ALLOWED_REPOS)
        assert len(baseline) > 0, "Baseline ALLOWED_REPOS must be non-empty for this test to be meaningful"

        with patch.dict(os.environ, {"JUDGE_ALLOWED_REPOS": "org/repo-a,org/repo-b"}, clear=False):
            importlib.reload(config)
            # The env var must not alter ALLOWED_REPOS — it must remain the same as baseline.
            assert baseline == config.ALLOWED_REPOS, (
                f"ALLOWED_REPOS changed after setting JUDGE_ALLOWED_REPOS — "
                f"it must only come from YAML. Before: {baseline}, After: {config.ALLOWED_REPOS}"
            )

        importlib.reload(config)

    def test_validate_repo_passes_for_allowed_repo(self) -> None:
        """
        Given: a repo name that is in ALLOWED_REPOS
        When: validate_repo is called
        Then: it completes without raising, and ALLOWED_REPOS still contains the repo
              (state is unchanged — validate_repo is a pure validator with no side effects)
        """
        repo = next(iter(ALLOWED_REPOS))
        assert repo in ALLOWED_REPOS, f"Precondition failed: '{repo}' should be in ALLOWED_REPOS"
        validate_repo(repo)
        assert repo in ALLOWED_REPOS, (
            f"Post-condition failed: validate_repo must not modify ALLOWED_REPOS; '{repo}' was removed after the call"
        )

    def test_validate_repo_raises_for_unknown_repo(self) -> None:
        with pytest.raises(ValueError, match="not allowed"):
            validate_repo(_UNKNOWN_REPO)

    def test_validate_repo_rejects_wrong_org_when_judge_gh_org_set(self) -> None:
        with patch.object(config, "ALLOWED_GH_ORG", _FIXTURE_ORG):
            with pytest.raises(ValueError, match="JUDGE_GH_ORG restricts access"):
                config.validate_repo(_WRONG_ORG_REPO)

    def test_validate_repo_skips_org_check_when_judge_gh_org_empty(self) -> None:
        with patch.object(config, "ALLOWED_GH_ORG", ""):
            with pytest.raises(ValueError, match="not allowed"):
                config.validate_repo("other-org/some-repo")


@pytest.mark.unit
class TestResolveRepo:
    """Test resolve_repo resolution logic."""

    def test_returns_full_name_when_already_in_allowed_repos(self) -> None:
        """Line 75-76: returns immediately when input is already a full name."""
        result = config.resolve_repo(_ALLOWED_REPO_IN_FIXTURE)
        assert result == _ALLOWED_REPO_IN_FIXTURE

    def test_resolves_short_name_to_full_name(self) -> None:
        """Lines 77-79: resolves a short repo name to its fully-qualified form."""
        # The fixture has "caylent-solutions/git-repo", so "git-repo" should resolve
        result = config.resolve_repo("git-repo")
        assert result == _ALLOWED_REPO_IN_FIXTURE

    def test_raises_for_unknown_name(self) -> None:
        """Line 80-83: raises ValueError when the name is not in either mapping."""
        with pytest.raises(ValueError, match="not recognised"):
            config.resolve_repo("completely-unknown-repo")


@pytest.mark.unit
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


@pytest.mark.unit
class TestGetAnthropicApiKey:
    """Test Claude credential reading from credentials file."""

    def test_reads_token_from_credentials_file(self, tmp_path: Path) -> None:
        creds_file = tmp_path / "credentials.json"
        creds_file.write_text(
            json.dumps(
                {
                    "claudeAiOauth": {
                        "accessToken": "sk-ant-oat01-test-token",
                        "scopes": ["user:inference"],
                    }
                }
            )
        )

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
        creds_file.write_text(json.dumps({"claudeAiOauth": {"accessToken": "  ", "scopes": []}}))

        with patch.object(config, "CLAUDE_CREDENTIALS_FILE", creds_file):
            with pytest.raises(RuntimeError, match="No access token"):
                config.get_anthropic_api_key()

    def test_raises_on_invalid_json(self, tmp_path: Path) -> None:
        creds_file = tmp_path / "credentials.json"
        creds_file.write_text("not json {{{")

        with patch.object(config, "CLAUDE_CREDENTIALS_FILE", creds_file):
            with pytest.raises(RuntimeError, match="Failed to read"):
                config.get_anthropic_api_key()


@pytest.mark.unit
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


@pytest.mark.unit
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


@pytest.mark.unit
class TestResolveHelpers:
    """Tests for _resolve_int and _resolve_float config resolution helpers."""

    def test_resolve_int_env_var_wins(self) -> None:
        with patch.dict(os.environ, {"TEST_VAR": "42"}, clear=False):
            result = config._resolve_int("TEST_VAR", 10, 5)
        assert result == 42

    def test_resolve_int_yaml_when_env_absent(self) -> None:
        env_copy = {k: v for k, v in os.environ.items() if k != "TEST_VAR_ABSENT"}
        with patch.dict(os.environ, env_copy, clear=True):
            result = config._resolve_int("TEST_VAR_ABSENT", 10, 5)
        assert result == 10

    def test_resolve_int_default_when_both_absent(self) -> None:
        env_copy = {k: v for k, v in os.environ.items() if k != "TEST_VAR_ABSENT"}
        with patch.dict(os.environ, env_copy, clear=True):
            result = config._resolve_int("TEST_VAR_ABSENT", None, 5)
        assert result == 5

    def test_resolve_float_env_var_wins(self) -> None:
        with patch.dict(os.environ, {"TEST_FLOAT": "1.5"}, clear=False):
            result = config._resolve_float("TEST_FLOAT", 2.0, 3.0)
        assert result == 1.5

    def test_resolve_float_yaml_when_env_absent(self) -> None:
        env_copy = {k: v for k, v in os.environ.items() if k != "TEST_FLOAT_ABSENT"}
        with patch.dict(os.environ, env_copy, clear=True):
            result = config._resolve_float("TEST_FLOAT_ABSENT", 2.0, 3.0)
        assert result == 2.0

    def test_resolve_float_default_when_both_absent(self) -> None:
        env_copy = {k: v for k, v in os.environ.items() if k != "TEST_FLOAT_ABSENT"}
        with patch.dict(os.environ, env_copy, clear=True):
            result = config._resolve_float("TEST_FLOAT_ABSENT", None, 3.0)
        assert result == 3.0

    def test_resolve_float_no_env_var_name(self) -> None:
        """When env_var is None, skip env lookup entirely."""
        result = config._resolve_float(None, 2.5, 3.0)
        assert result == 2.5


@pytest.mark.unit
class TestStopHookConfigExposed:
    """Verify stop hook constants are exported from config module."""

    def test_stop_hook_max_blocks_exposed(self) -> None:
        assert hasattr(config, "STOP_HOOK_MAX_BLOCKS")
        assert isinstance(config.STOP_HOOK_MAX_BLOCKS, int)

    def test_stop_hook_window_seconds_exposed(self) -> None:
        assert hasattr(config, "STOP_HOOK_WINDOW_SECONDS")
        assert isinstance(config.STOP_HOOK_WINDOW_SECONDS, int)

    def test_stop_hook_stale_task_minutes_exposed(self) -> None:
        assert hasattr(config, "STOP_HOOK_STALE_TASK_MINUTES")
        assert isinstance(config.STOP_HOOK_STALE_TASK_MINUTES, int)

    def test_stop_hook_max_blocks_env_override(self) -> None:
        with patch.dict(os.environ, {"JUDGE_STOP_MAX_BLOCKS": "3"}, clear=False):
            importlib.reload(config)
            assert config.STOP_HOOK_MAX_BLOCKS == 3
        importlib.reload(config)

    def test_stop_hook_window_seconds_env_override(self) -> None:
        with patch.dict(os.environ, {"JUDGE_STOP_WINDOW_SECONDS": "60"}, clear=False):
            importlib.reload(config)
            assert config.STOP_HOOK_WINDOW_SECONDS == 60
        importlib.reload(config)

    def test_stop_hook_stale_task_minutes_env_override(self) -> None:
        with patch.dict(os.environ, {"JUDGE_STOP_STALE_MINUTES": "30"}, clear=False):
            importlib.reload(config)
            assert config.STOP_HOOK_STALE_TASK_MINUTES == 30
        importlib.reload(config)


@pytest.mark.unit
class TestMaxRetriesYamlFirst:
    """Verify max_executor_retries reads YAML first, env var overrides."""

    def test_max_executor_retries_env_overrides(self) -> None:
        with patch.dict(os.environ, {"JUDGE_MAX_RETRIES": "15"}, clear=False):
            importlib.reload(config)
            assert config.MAX_RETRY_ATTEMPTS == 15
        importlib.reload(config)

    def test_max_executor_retries_uses_yaml_when_env_absent(self) -> None:
        """When JUDGE_MAX_RETRIES is not set, the YAML value is used."""
        env_copy = {k: v for k, v in os.environ.items() if k != "JUDGE_MAX_RETRIES"}
        with patch.dict(os.environ, env_copy, clear=True):
            importlib.reload(config)
            # Value should come from YAML or default — it should be an int > 0
            assert isinstance(config.MAX_RETRY_ATTEMPTS, int)
            assert config.MAX_RETRY_ATTEMPTS > 0
        importlib.reload(config)
