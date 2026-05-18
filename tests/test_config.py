"""Tests for judges.config module."""

from __future__ import annotations

import importlib
import json
import os
import subprocess
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

from devbench import config
from devbench.config import ALLOWED_REPOS, validate_repo
from devbench.constants import DEVBENCH_BOOTSTRAP_ENV_VAR

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
        """JUDGE_ALLOWED_REPOS env var is ignored -- repos come from YAML only."""
        # Capture the baseline ALLOWED_REPOS before patching.
        baseline = frozenset(config.ALLOWED_REPOS)
        assert len(baseline) > 0, "Baseline ALLOWED_REPOS must be non-empty for this test to be meaningful"

        with patch.dict(os.environ, {"JUDGE_ALLOWED_REPOS": "org/repo-a,org/repo-b"}, clear=False):
            importlib.reload(config)
            # The env var must not alter ALLOWED_REPOS -- it must remain the same as baseline.
            assert baseline == config.ALLOWED_REPOS, (
                f"ALLOWED_REPOS changed after setting JUDGE_ALLOWED_REPOS -- "
                f"it must only come from YAML. Before: {baseline}, After: {config.ALLOWED_REPOS}"
            )

        importlib.reload(config)

    def test_validate_repo_passes_for_allowed_repo(self) -> None:
        """
        Given: a repo name that is in ALLOWED_REPOS
        When: validate_repo is called
        Then: it completes without raising, and ALLOWED_REPOS still contains the repo
              (state is unchanged -- validate_repo is a pure validator with no side effects)
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

    def test_validate_repo_rejects_wrong_org_when_devbench_gh_org_set(self) -> None:
        with patch.object(config, "ALLOWED_GH_ORG", _FIXTURE_ORG):
            with pytest.raises(ValueError, match="DEVBENCH_GH_ORG restricts access"):
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
        with patch.dict(os.environ, {"DEVBENCH_MERGE_STRATEGY": "fast-forward"}, clear=False):
            with pytest.raises(RuntimeError, match="DEVBENCH_MERGE_STRATEGY must be one of"):
                importlib.reload(config)

        importlib.reload(config)

    def test_default_is_squash(self) -> None:
        env_copy = {k: v for k, v in os.environ.items() if k != "DEVBENCH_MERGE_STRATEGY"}
        with patch.dict(os.environ, env_copy, clear=True):
            importlib.reload(config)
            assert config.MERGE_STRATEGY == config.MergeStrategy.SQUASH

        importlib.reload(config)


@pytest.mark.unit
class TestConfigOverrides:
    """Test that config values can be overridden via environment variables."""

    def test_max_retry_attempts_from_env(self) -> None:
        with patch.dict(os.environ, {"DEVBENCH_MAX_RETRIES": "7"}, clear=False):
            importlib.reload(config)
            assert config.MAX_RETRY_ATTEMPTS == 7

        with patch.dict(os.environ, {"DEVBENCH_MAX_RETRIES": "3"}, clear=False):
            importlib.reload(config)

    def test_github_check_timeout_from_env(self) -> None:
        with patch.dict(os.environ, {"DEVBENCH_GH_TIMEOUT": "120"}, clear=False):
            importlib.reload(config)
            assert config.GITHUB_CHECK_TIMEOUT_SECONDS == 120

        with patch.dict(os.environ, {"DEVBENCH_GH_TIMEOUT": "600"}, clear=False):
            importlib.reload(config)

    def test_backlog_root_derived_from_workspace_root(self) -> None:
        """BACKLOG_ROOT is always derived from DEVBENCH_WORKSPACE_ROOT, not from env."""
        from devbench.constants import BACKLOG_SUBDIR

        env_copy = {k: v for k, v in os.environ.items() if k not in ("JUDGE_BACKLOG_ROOT",)}
        with patch.dict(os.environ, env_copy, clear=True):
            importlib.reload(config)
            expected = Path(os.environ["DEVBENCH_WORKSPACE_ROOT"]) / BACKLOG_SUBDIR
            assert expected == config.BACKLOG_ROOT

        importlib.reload(config)

    def test_backlog_index_derived_from_workspace_root(self) -> None:
        """BACKLOG_INDEX is always derived from DEVBENCH_WORKSPACE_ROOT, not from env."""
        env_copy = {k: v for k, v in os.environ.items() if k not in ("JUDGE_BACKLOG_INDEX",)}
        with patch.dict(os.environ, env_copy, clear=True):
            importlib.reload(config)
            expected = Path(os.environ["DEVBENCH_WORKSPACE_ROOT"]) / "BACKLOG.md"
            assert expected == config.BACKLOG_INDEX

        importlib.reload(config)

    def test_judge_backlog_root_env_var_has_no_effect(self, tmp_path: Path) -> None:
        """JUDGE_BACKLOG_ROOT env var is ignored -- path derived from DEVBENCH_WORKSPACE_ROOT."""
        from devbench.constants import BACKLOG_SUBDIR

        custom_root = tmp_path / "custom-backlog"
        with patch.dict(os.environ, {"JUDGE_BACKLOG_ROOT": str(custom_root)}, clear=False):
            importlib.reload(config)
            expected = Path(os.environ["DEVBENCH_WORKSPACE_ROOT"]) / BACKLOG_SUBDIR
            assert expected == config.BACKLOG_ROOT
            assert custom_root != config.BACKLOG_ROOT

        importlib.reload(config)

    def test_judge_backlog_index_env_var_has_no_effect(self, tmp_path: Path) -> None:
        """JUDGE_BACKLOG_INDEX env var is ignored -- path derived from DEVBENCH_WORKSPACE_ROOT."""
        custom_index = tmp_path / "CUSTOM_BACKLOG.md"
        with patch.dict(os.environ, {"JUDGE_BACKLOG_INDEX": str(custom_index)}, clear=False):
            importlib.reload(config)
            expected = Path(os.environ["DEVBENCH_WORKSPACE_ROOT"]) / "BACKLOG.md"
            assert expected == config.BACKLOG_INDEX
            assert custom_index != config.BACKLOG_INDEX

        importlib.reload(config)


@pytest.mark.unit
class TestResolveHelpers:
    """Tests for _resolve_float config resolution helper."""

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


class TestCanonicalConfigToggles:
    """Verify the v-next canonical toggle resolutions read from YAML correctly.

    Each test patches ``RUNTIME_CONFIG.git_ops`` (or relevant nested) with a
    mocked dataclass instance and re-imports the resolved constant via
    ``importlib.reload``. The test patterns mirror the existing
    ``TestStopHookConfigExposed`` shape.
    """

    def test_inline_orphan_cleanup_default_on(self) -> None:
        """When neither env nor YAML opt out, INLINE_ORPHAN_CLEANUP_ENABLED is True."""
        from devbench.constants import DEFAULT_INLINE_ORPHAN_CLEANUP_ENABLED

        assert DEFAULT_INLINE_ORPHAN_CLEANUP_ENABLED is True
        assert config.INLINE_ORPHAN_CLEANUP_ENABLED is True

    def test_ci_failure_retry_default_on(self) -> None:
        """v-next default flip: CI_FAILURE_RETRY_ENABLED is True by default."""
        from devbench.constants import DEFAULT_CI_FAILURE_RETRY_ENABLED

        assert DEFAULT_CI_FAILURE_RETRY_ENABLED is True

    def test_pause_before_merge_default_off(self) -> None:
        """Issue #101: pause-before-merge ships off so existing flows are unchanged."""
        from devbench.constants import DEFAULT_PAUSE_BEFORE_MERGE

        assert DEFAULT_PAUSE_BEFORE_MERGE is False


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
        with patch.dict(os.environ, {"DEVBENCH_STOP_MAX_BLOCKS": "3"}, clear=False):
            importlib.reload(config)
            assert config.STOP_HOOK_MAX_BLOCKS == 3
        importlib.reload(config)

    def test_stop_hook_window_seconds_env_override(self) -> None:
        with patch.dict(os.environ, {"DEVBENCH_STOP_WINDOW_SECONDS": "60"}, clear=False):
            importlib.reload(config)
            assert config.STOP_HOOK_WINDOW_SECONDS == 60
        importlib.reload(config)

    def test_stop_hook_stale_task_minutes_env_override(self) -> None:
        with patch.dict(os.environ, {"DEVBENCH_STOP_STALE_MINUTES": "30"}, clear=False):
            importlib.reload(config)
            assert config.STOP_HOOK_STALE_TASK_MINUTES == 30
        importlib.reload(config)


@pytest.mark.unit
class TestMaxRetriesYamlFirst:
    """Verify max_executor_retries reads YAML first, env var overrides."""

    def test_max_executor_retries_env_overrides(self) -> None:
        with patch.dict(os.environ, {"DEVBENCH_MAX_RETRIES": "15"}, clear=False):
            importlib.reload(config)
            assert config.MAX_RETRY_ATTEMPTS == 15
        importlib.reload(config)

    def test_max_executor_retries_uses_yaml_when_env_absent(self) -> None:
        """When DEVBENCH_MAX_RETRIES is not set, the YAML value is used."""
        env_copy = {k: v for k, v in os.environ.items() if k != "DEVBENCH_MAX_RETRIES"}
        with patch.dict(os.environ, env_copy, clear=True):
            importlib.reload(config)
            # Value should come from YAML or default -- it should be an int > 0
            assert isinstance(config.MAX_RETRY_ATTEMPTS, int)
            assert config.MAX_RETRY_ATTEMPTS > 0
        importlib.reload(config)


@pytest.mark.unit
class TestAgentModelEnvOverrides:
    """ADR-25: DEVBENCH_AGENT_MODEL_<NAME> env vars override YAML at config-load time."""

    def test_executor_env_overrides_yaml(self) -> None:
        with patch.dict(os.environ, {"DEVBENCH_AGENT_MODEL_EXECUTOR": "opus"}, clear=False):
            importlib.reload(config)
            assert config.AGENT_MODELS.executor == "opus"
        importlib.reload(config)

    def test_review_team_env_overrides_yaml(self) -> None:
        with patch.dict(
            os.environ,
            {
                "DEVBENCH_AGENT_MODEL_CODE_REVIEWER": "haiku",
                "DEVBENCH_AGENT_MODEL_CHANGES_MANIFEST": "opus",
            },
            clear=False,
        ):
            importlib.reload(config)
            assert config.AGENT_MODELS.review_team.code_reviewer == "haiku"
            assert config.AGENT_MODELS.review_team.changes_manifest == "opus"
        importlib.reload(config)

    def test_invalid_env_value_rejected_at_load(self) -> None:
        """A garbage env value should fail-fast at config.py import (re-validated against USE_BEDROCK)."""
        with patch.dict(os.environ, {"DEVBENCH_AGENT_MODEL_EXECUTOR": "garbage-value-not-a-model"}, clear=False):
            with pytest.raises(ValueError, match="not a valid Anthropic API"):
                importlib.reload(config)
        importlib.reload(config)

    def test_empty_env_var_treated_as_unset(self) -> None:
        """Empty string env var must not override (treated as unset)."""
        with patch.dict(os.environ, {"DEVBENCH_AGENT_MODEL_EXECUTOR": ""}, clear=False):
            importlib.reload(config)
            # Fixture has no agents block, so executor stays None.
            assert config.AGENT_MODELS.executor is None
        importlib.reload(config)

    def test_all_agent_env_vars_covered(self) -> None:
        """Every defined DEVBENCH_AGENT_MODEL_* env var routes to a real field."""
        envs = {
            "DEVBENCH_AGENT_MODEL_EXECUTOR": "opus",
            "DEVBENCH_AGENT_MODEL_BLOCKER_RESOLVER": "opus",
            "DEVBENCH_AGENT_MODEL_MANIFEST_AMENDER": "opus",
            "DEVBENCH_AGENT_MODEL_SECURITY_REVIEWER": "opus",
            "DEVBENCH_AGENT_MODEL_TASK_FACTORY": "opus",
            "DEVBENCH_AGENT_MODEL_REVIEW_SUPERVISOR": "opus",
            "DEVBENCH_AGENT_MODEL_CODE_REVIEWER": "opus",
            "DEVBENCH_AGENT_MODEL_TEST_REVIEWER": "opus",
            "DEVBENCH_AGENT_MODEL_DOC_REVIEWER": "opus",
            "DEVBENCH_AGENT_MODEL_CHANGES_MANIFEST": "opus",
        }
        with patch.dict(os.environ, envs, clear=False):
            importlib.reload(config)
            am = config.AGENT_MODELS
            assert am.executor == "opus"
            assert am.blocker_resolver == "opus"
            assert am.manifest_amender == "opus"
            assert am.security_reviewer == "opus"
            assert am.task_factory == "opus"
            assert am.review_supervisor == "opus"
            assert am.review_team.code_reviewer == "opus"
            assert am.review_team.test_reviewer == "opus"
            assert am.review_team.doc_reviewer == "opus"
            assert am.review_team.changes_manifest == "opus"
        importlib.reload(config)


@pytest.mark.unit
class TestReadEnvStrict:
    """Tests for the _read_env_strict(new_name, legacy_name) helper.

    Covers the three canonical cases (AC-197-11):
      1. New-name only set -> returns value.
      2. Legacy name set (regardless of new-name presence) -> raises RuntimeError.
      3. Neither set -> returns None.
    Also covers the DEVBENCH_BOOTSTRAP bypass (AC-197-7).
    """

    _NEW = "DEVBENCH_TEST_STRICT_VAR"
    _LEGACY = "JUDGE_TEST_STRICT_VAR"

    def _clean_env(self) -> dict[str, str]:
        """Return os.environ without both test keys and DEVBENCH_BOOTSTRAP_ENV_VAR."""
        return {k: v for k, v in os.environ.items() if k not in (self._NEW, self._LEGACY, DEVBENCH_BOOTSTRAP_ENV_VAR)}

    @pytest.mark.parametrize(
        "legacy_val,new_val",
        [
            ("legacy-only", None),
            ("legacy-val", "new-val"),
        ],
    )
    def test_legacy_presence_always_raises(self, legacy_val: str, new_val: str | None) -> None:
        """Parametrised: any non-empty legacy value causes hard rejection."""
        env = self._clean_env()
        env[self._LEGACY] = legacy_val
        if new_val is not None:
            env[self._NEW] = new_val
        with patch.dict(os.environ, env, clear=True):
            with pytest.raises(RuntimeError, match="is no longer accepted"):
                config._read_env_strict(self._NEW, self._LEGACY)

    def test_error_message_canonical_format(self) -> None:
        """The RuntimeError message follows the exact canonical format from AC-197-2."""
        env = self._clean_env()
        env[self._LEGACY] = "some-value"
        with patch.dict(os.environ, env, clear=True):
            with pytest.raises(RuntimeError) as exc_info:
                config._read_env_strict(self._NEW, self._LEGACY)
        msg = str(exc_info.value)
        assert "is no longer accepted" in msg
        assert "use" in msg
        assert "devbench migrate-env" in msg
        assert "migration shell-script" in msg

    def test_bootstrap_bypass_skips_rejection(self) -> None:
        """When DEVBENCH_BOOTSTRAP=1 is set, legacy presence does not raise (AC-197-7)."""
        env = self._clean_env()
        env[self._LEGACY] = "old-value"
        env[DEVBENCH_BOOTSTRAP_ENV_VAR] = "1"
        with patch.dict(os.environ, env, clear=True):
            result = config._read_env_strict(self._NEW, self._LEGACY)
        assert result is None

    def test_bootstrap_bypass_returns_new_value_when_set(self) -> None:
        """Bootstrap bypass with both names set returns the new-name value, not legacy."""
        env = self._clean_env()
        env[self._LEGACY] = "old-value"
        env[self._NEW] = "new-value"
        env[DEVBENCH_BOOTSTRAP_ENV_VAR] = "1"
        with patch.dict(os.environ, env, clear=True):
            result = config._read_env_strict(self._NEW, self._LEGACY)
        assert result == "new-value"

    def test_bootstrap_zero_does_not_bypass(self) -> None:
        """DEVBENCH_BOOTSTRAP=0 must NOT activate the bypass."""
        env = self._clean_env()
        env[self._LEGACY] = "old-value"
        env[DEVBENCH_BOOTSTRAP_ENV_VAR] = "0"
        with patch.dict(os.environ, env, clear=True):
            with pytest.raises(RuntimeError):
                config._read_env_strict(self._NEW, self._LEGACY)

    @pytest.mark.parametrize("non_one_value", ["true", "yes", "True", "YES", "on", "1 "])
    def test_bootstrap_non_one_values_do_not_bypass(self, non_one_value: str) -> None:
        """Only the exact string '1' activates the bypass; other truthy values must not (AC-197-7)."""
        env = self._clean_env()
        env[self._LEGACY] = "old-value"
        env[DEVBENCH_BOOTSTRAP_ENV_VAR] = non_one_value
        with patch.dict(os.environ, env, clear=True):
            with pytest.raises(RuntimeError):
                config._read_env_strict(self._NEW, self._LEGACY)

    def test_empty_legacy_value_is_treated_as_unset(self) -> None:
        """An empty string legacy var is treated as unset; new name is consulted."""
        env = self._clean_env()
        env[self._LEGACY] = ""
        env[self._NEW] = "real-value"
        with patch.dict(os.environ, env, clear=True):
            result = config._read_env_strict(self._NEW, self._LEGACY)
        assert result == "real-value"

    def test_empty_new_value_with_no_legacy_returns_none(self) -> None:
        """Empty new-name var with no legacy returns None (empty is treated as unset)."""
        env = self._clean_env()
        env[self._NEW] = ""
        with patch.dict(os.environ, env, clear=True):
            result = config._read_env_strict(self._NEW, self._LEGACY)
        assert result is None

    @pytest.mark.parametrize(
        "new_val,legacy_val,expected_result,expect_raise",
        [
            # (a) new name only set -> returns value (AC-197-11 case 1)
            ("new-value", None, "new-value", False),
            # (b) legacy name only set -> raises RuntimeError naming both vars + devbench migrate-env (AC-197-2)
            (None, "old-value", None, True),
            # (c) both set -> same hard rejection as (b), legacy presence is the disqualifier (AC-197-3)
            ("new-value", "old-value", None, True),
            # (d) neither set -> returns None (AC-197-11 case 3)
            (None, None, None, False),
        ],
        ids=["new-only-returns-value", "legacy-only-raises", "both-set-raises", "neither-returns-none"],
    )
    def test_four_canonical_cases(
        self,
        new_val: str | None,
        legacy_val: str | None,
        expected_result: str | None,
        expect_raise: bool,
    ) -> None:
        """Parametrised over the four canonical env-var cases (AC-197-2, AC-197-3, AC-197-11).

        (a) New name only set -> returns value.
        (b) Legacy name only set -> raises RuntimeError naming both vars and 'devbench migrate-env'.
        (c) Both names set -> same hard rejection as (b): legacy presence is the disqualifier.
        (d) Neither set -> returns None.
        """
        env = self._clean_env()
        if new_val is not None:
            env[self._NEW] = new_val
        if legacy_val is not None:
            env[self._LEGACY] = legacy_val
        with patch.dict(os.environ, env, clear=True):
            if expect_raise:
                with pytest.raises(RuntimeError) as exc_info:
                    config._read_env_strict(self._NEW, self._LEGACY)
                msg = str(exc_info.value)
                assert self._LEGACY in msg, f"Legacy var name missing from error: {msg!r}"
                assert self._NEW in msg, f"New var name missing from error: {msg!r}"
                assert "devbench migrate-env" in msg, f"'devbench migrate-env' missing from error: {msg!r}"
            else:
                result = config._read_env_strict(self._NEW, self._LEGACY)
                assert result == expected_result, f"Expected {expected_result!r}, got {result!r}"


# ---------------------------------------------------------------------------
# AC-197-12: strict checker fires at earliest env-var read in process startup
# ---------------------------------------------------------------------------

_FIXTURES_DIR = Path(__file__).parent / "fixtures"
_TEST_YAML = _FIXTURES_DIR / "test_devbench.yaml"

# A representative legacy JUDGE_* -> DEVBENCH_* pair that is read at
# module level in config.py and must go through _read_env_strict (AC-197-12).
# This constant is the legacy name; the new name has the DEVBENCH_ prefix
# with the same suffix.
_AC197_12_LEGACY_VAR = "JUDGE_GH_ORG"
_AC197_12_NEW_VAR = "DEVBENCH_GH_ORG"


def _build_subprocess_env(legacy_var: str, legacy_val: str) -> dict[str, str]:
    """Build a clean env for subprocess tests: required devbench vars plus the legacy sentinel.

    Uses DEVBENCH_* names for the required env vars (AC-197-1) so that only
    the intentionally-injected legacy var triggers the strict checker.
    JUDGE_CONFIG_PATH and JUDGE_LOG_FILE remain because config_loader.py and
    log_setup.py have not yet been migrated to DEVBENCH_* names.
    """
    env: dict[str, str] = {
        "DEVBENCH_CLAUDE_MODEL": "test-model",
        "DEVBENCH_WORKSPACE_ROOT": "/tmp/test-workspace",
        "JUDGE_LOG_FILE": "/tmp/test-orchestrator.log",
        "JUDGE_CONFIG_PATH": str(_TEST_YAML),
        legacy_var: legacy_val,
    }
    # Propagate PATH and PYTHONPATH so the subprocess can find the package.
    for key in ("PATH", "PYTHONPATH", "VIRTUAL_ENV"):
        val = os.environ.get(key)
        if val:
            env[key] = val
    return env


@pytest.mark.unit
class TestStrictCheckerFiresAtEarliestStartup:
    """AC-197-12: regression test -- strict checker fires at earliest env-var read.

    The module-level code in config.py must call _read_env_strict for at least
    one JUDGE_X -> DEVBENCH_X pair before resolving WORKSPACE_ROOT (workspace
    path) and before reading JUDGE_CLAUDE_MODEL (LLM model).  This class pins
    the requirement by spawning a fresh Python process that imports devbench.config
    with a legacy JUDGE_* var set and asserts the process exits with a RuntimeError
    naming the legacy var BEFORE any side-effecting startup step completes.
    """

    def _run_import(self, env: dict[str, str]) -> subprocess.CompletedProcess[str]:
        """Spawn a subprocess that imports devbench.config and captures stderr."""
        script = "import devbench.config"
        return subprocess.run(
            [sys.executable, "-c", script],
            env=env,
            capture_output=True,
            text=True,
        )

    def test_legacy_var_causes_nonzero_exit_on_module_import(self) -> None:
        """When a legacy JUDGE_* var is set, importing config raises RuntimeError (exit != 0)."""
        env = _build_subprocess_env(_AC197_12_LEGACY_VAR, "legacy-org-value")
        result = self._run_import(env)
        assert result.returncode != 0, (
            f"Expected non-zero exit when {_AC197_12_LEGACY_VAR!r} is set, "
            f"but process exited {result.returncode}. stderr={result.stderr!r}"
        )

    def test_legacy_var_error_names_both_vars_and_migration_command(self) -> None:
        """The RuntimeError message from module import names the legacy var, new var, and devbench migrate-env."""
        env = _build_subprocess_env(_AC197_12_LEGACY_VAR, "legacy-org-value")
        result = self._run_import(env)
        combined = result.stderr + result.stdout
        assert _AC197_12_LEGACY_VAR in combined, (
            f"Legacy var name {_AC197_12_LEGACY_VAR!r} missing from process output. stderr={result.stderr!r}"
        )
        assert _AC197_12_NEW_VAR in combined, (
            f"New var name {_AC197_12_NEW_VAR!r} missing from process output. stderr={result.stderr!r}"
        )
        assert "devbench migrate-env" in combined, (
            f"'devbench migrate-env' missing from process output. stderr={result.stderr!r}"
        )

    def test_error_fires_before_workspace_root_is_resolved(self) -> None:
        """The strict-checker RuntimeError appears before any WORKSPACE_ROOT resolution message.

        Verifies the ordering invariant: _read_env_strict is wired at the earliest
        module-level read site, not deferred to after WORKSPACE_ROOT is consumed.
        """
        env = _build_subprocess_env(_AC197_12_LEGACY_VAR, "legacy-org-value")
        result = self._run_import(env)
        combined = result.stderr + result.stdout
        # The error must be the strict-checker RuntimeError, not a workspace-not-found error.
        assert "is no longer accepted" in combined, (
            f"Expected strict-checker message 'is no longer accepted' in output, but got: {combined!r}"
        )
        # The WORKSPACE_ROOT error must NOT appear -- it fires only when the
        # workspace is unset/empty, which is separate from the strict-checker error.
        # If strict checker fires first, we never reach the WORKSPACE_ROOT check.
        assert "DEVBENCH_WORKSPACE_ROOT environment variable is not set" not in combined, (
            f"WORKSPACE_ROOT error appeared before strict-checker fired. "
            f"The strict check must be wired before WORKSPACE_ROOT resolution. "
            f"output={combined!r}"
        )

    def test_error_fires_before_llm_model_is_read(self) -> None:
        """The strict-checker error appears before DEVBENCH_CLAUDE_MODEL is read.

        Verifies the strict checker fires before LLM client construction (AC-197-12).
        The test intentionally omits DEVBENCH_CLAUDE_MODEL from the env and sets
        the legacy var; the strict-checker must fire before the absent-model error.
        """
        env = _build_subprocess_env(_AC197_12_LEGACY_VAR, "legacy-org-value")
        # Remove the LLM model var so if the process somehow gets past the
        # strict check it would hit a different error (no-model RuntimeError).
        env.pop("DEVBENCH_CLAUDE_MODEL", None)
        result = self._run_import(env)
        combined = result.stderr + result.stdout
        assert "is no longer accepted" in combined, f"Expected strict-checker message in output but got: {combined!r}"
        # If the LLM model absence error appears, the strict check ran AFTER
        # the model-read -- a violation of the ordering invariant.
        assert "DEVBENCH_CLAUDE_MODEL environment variable is not set" not in combined, (
            f"CLAUDE_MODEL error appeared -- strict checker did not fire before LLM model read. output={combined!r}"
        )

    @pytest.mark.parametrize(
        "legacy_var,legacy_val",
        [
            (_AC197_12_LEGACY_VAR, "any-value"),
            ("JUDGE_MERGE_STRATEGY", "squash"),
        ],
        ids=["JUDGE_GH_ORG", "JUDGE_MERGE_STRATEGY"],
    )
    def test_various_legacy_vars_trigger_rejection_at_import(
        self,
        legacy_var: str,
        legacy_val: str,
    ) -> None:
        """Parametrised: multiple legacy vars all trigger rejection during module import.

        Each JUDGE_X var wired through _read_env_strict must cause the import to
        fail with a RuntimeError that names both the legacy and the new var name.
        """
        new_var = legacy_var.replace("JUDGE_", "DEVBENCH_", 1)
        env = _build_subprocess_env(legacy_var, legacy_val)
        result = self._run_import(env)
        assert result.returncode != 0, (
            f"Expected non-zero exit for legacy var {legacy_var!r}, "
            f"got exit {result.returncode}. stderr={result.stderr!r}"
        )
        combined = result.stderr + result.stdout
        assert legacy_var in combined, f"Legacy var {legacy_var!r} missing from error output: {combined!r}"
        assert new_var in combined, f"New var {new_var!r} missing from error output: {combined!r}"


# ---------------------------------------------------------------------------
# AC-197-1: every call site in config.py reads DEVBENCH_* via _read_env_strict
# AC-197-2: setting JUDGE_* causes hard rejection at module load
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestAC197CallSiteMigration:
    """AC-197-1 / AC-197-2: every config.py call site uses DEVBENCH_* via _read_env_strict.

    For each JUDGE_X -> DEVBENCH_X pair that was previously resolved via legacy
    helpers (now removed), each site now uses _strict_int / _strict_str /
    _strict_bool / _strict_float / _strict_optional_str / _strict_str_tuple
    which delegate to _read_env_strict. We verify:
      (a) DEVBENCH_X overrides the resolved constant correctly.
      (b) JUDGE_X set in env causes RuntimeError on config module reload.
    """

    @pytest.mark.parametrize(
        "legacy_var,new_var,test_value,attr_name",
        [
            ("JUDGE_MAX_RETRIES", "DEVBENCH_MAX_RETRIES", "77", "MAX_RETRY_ATTEMPTS"),
            ("JUDGE_GH_TIMEOUT", "DEVBENCH_GH_TIMEOUT", "999", "GITHUB_CHECK_TIMEOUT_SECONDS"),
            ("JUDGE_CI_FAILURE_LOG_BYTES", "DEVBENCH_CI_FAILURE_LOG_BYTES", "1024", "CI_FAILURE_LOG_BYTES"),
            ("JUDGE_STOP_MAX_BLOCKS", "DEVBENCH_STOP_MAX_BLOCKS", "9", "STOP_HOOK_MAX_BLOCKS"),
            ("JUDGE_STOP_WINDOW_SECONDS", "DEVBENCH_STOP_WINDOW_SECONDS", "77", "STOP_HOOK_WINDOW_SECONDS"),
            ("JUDGE_STOP_STALE_MINUTES", "DEVBENCH_STOP_STALE_MINUTES", "55", "STOP_HOOK_STALE_TASK_MINUTES"),
            ("JUDGE_GH_API_TIMEOUT", "DEVBENCH_GH_API_TIMEOUT", "45", "GH_API_TIMEOUT"),
            ("JUDGE_TEST_TIMEOUT", "DEVBENCH_TEST_TIMEOUT", "888", "TEST_TIMEOUT"),
            ("JUDGE_LLM_TIMEOUT", "DEVBENCH_LLM_TIMEOUT", "777", "LLM_TIMEOUT"),
            ("JUDGE_COMMAND_TIMEOUT", "DEVBENCH_COMMAND_TIMEOUT", "333", "COMMAND_TIMEOUT"),
            ("JUDGE_ALERT_SUMMARY_LIMIT", "DEVBENCH_ALERT_SUMMARY_LIMIT", "5", "ALERT_SUMMARY_LIMIT"),
            ("JUDGE_OUTPUT_TRUNCATION", "DEVBENCH_OUTPUT_TRUNCATION", "500", "OUTPUT_TRUNCATION_LIMIT"),
            ("JUDGE_LLM_EVIDENCE_TRUNCATION", "DEVBENCH_LLM_EVIDENCE_TRUNCATION", "9999", "LLM_EVIDENCE_TRUNCATION"),
            ("JUDGE_LLM_FILE_CONTEXT_LIMIT", "DEVBENCH_LLM_FILE_CONTEXT_LIMIT", "3", "LLM_FILE_CONTEXT_LIMIT"),
            ("JUDGE_LLM_FILE_PREVIEW_CHARS", "DEVBENCH_LLM_FILE_PREVIEW_CHARS", "111", "LLM_FILE_PREVIEW_CHARS"),
            (
                "JUDGE_ORCHESTRATOR_POLL_INTERVAL",
                "DEVBENCH_ORCHESTRATOR_POLL_INTERVAL",
                "99",
                "ORCHESTRATOR_POLL_INTERVAL",
            ),
            (
                "JUDGE_ORCHESTRATE_MAX_CASCADE_DEPTH",
                "DEVBENCH_ORCHESTRATE_MAX_CASCADE_DEPTH",
                "4",
                "MAX_CASCADE_DEPTH",
            ),
            (
                "JUDGE_CHECK_REGISTRATION_RETRIES",
                "DEVBENCH_CHECK_REGISTRATION_RETRIES",
                "3",
                "CHECK_REGISTRATION_RETRIES",
            ),
            (
                "JUDGE_CHECK_REGISTRATION_DELAY_SECONDS",
                "DEVBENCH_CHECK_REGISTRATION_DELAY_SECONDS",
                "2",
                "CHECK_REGISTRATION_DELAY_SECONDS",
            ),
            (
                "JUDGE_BLOCKED_RECOVERY_WINDOW_SECONDS",
                "DEVBENCH_BLOCKED_RECOVERY_WINDOW_SECONDS",
                "600",
                "BLOCKED_RECOVERY_WINDOW_SECONDS",
            ),
            ("JUDGE_PR_REVIEW_SETTLE_SECONDS", "DEVBENCH_PR_REVIEW_SETTLE_SECONDS", "30", "PR_REVIEW_SETTLE_SECONDS"),
            ("JUDGE_PR_REVIEW_POLL_INTERVAL", "DEVBENCH_PR_REVIEW_POLL_INTERVAL", "2", "PR_REVIEW_POLL_INTERVAL"),
            ("JUDGE_HOOK_TAIL_AGENT_WIDTH", "DEVBENCH_HOOK_TAIL_AGENT_WIDTH", "20", "HOOK_TAIL_AGENT_WIDTH"),
            ("JUDGE_HOOK_TAIL_TOOL_WIDTH", "DEVBENCH_HOOK_TAIL_TOOL_WIDTH", "15", "HOOK_TAIL_TOOL_WIDTH"),
            (
                "JUDGE_HOOK_TAIL_DESCRIPTION_MAX",
                "DEVBENCH_HOOK_TAIL_DESCRIPTION_MAX",
                "200",
                "HOOK_TAIL_DESCRIPTION_MAX",
            ),
            (
                "JUDGE_HOOK_TAIL_STDOUT_PREVIEW_MAX",
                "DEVBENCH_HOOK_TAIL_STDOUT_PREVIEW_MAX",
                "50",
                "HOOK_TAIL_STDOUT_PREVIEW_MAX",
            ),
            ("JUDGE_REPORT_RECENT_PACE_TASKS", "DEVBENCH_REPORT_RECENT_PACE_TASKS", "6", "RECENT_PACE_TASKS"),
            ("JUDGE_SECURITY_FETCH_TIMEOUT", "DEVBENCH_SECURITY_FETCH_TIMEOUT", "66", "SECURITY_FETCH_TIMEOUT"),
        ],
    )
    def test_devbench_int_var_overrides_constant(
        self,
        legacy_var: str,
        new_var: str,
        test_value: str,
        attr_name: str,
    ) -> None:
        """DEVBENCH_* int var overrides the resolved constant (AC-197-1)."""
        with patch.dict(os.environ, {new_var: test_value}, clear=False):
            importlib.reload(config)
            assert getattr(config, attr_name) == int(test_value), (
                f"{new_var}={test_value} expected to set {attr_name}={int(test_value)}, "
                f"got {getattr(config, attr_name)}"
            )
        importlib.reload(config)

    @pytest.mark.parametrize(
        "legacy_var,legacy_val",
        [
            ("JUDGE_MAX_RETRIES", "7"),
            ("JUDGE_GH_TIMEOUT", "120"),
            ("JUDGE_CI_FAILURE_LOG_BYTES", "1024"),
            ("JUDGE_STOP_MAX_BLOCKS", "3"),
            ("JUDGE_STOP_WINDOW_SECONDS", "60"),
            ("JUDGE_STOP_STALE_MINUTES", "30"),
            ("JUDGE_GH_API_TIMEOUT", "45"),
            ("JUDGE_TEST_TIMEOUT", "300"),
            ("JUDGE_LLM_TIMEOUT", "600"),
            ("JUDGE_COMMAND_TIMEOUT", "120"),
            ("JUDGE_ALERT_SUMMARY_LIMIT", "5"),
            ("JUDGE_OUTPUT_TRUNCATION", "500"),
            ("JUDGE_LLM_EVIDENCE_TRUNCATION", "9000"),
            ("JUDGE_LLM_FILE_CONTEXT_LIMIT", "3"),
            ("JUDGE_LLM_FILE_PREVIEW_CHARS", "1000"),
            ("JUDGE_ORCHESTRATOR_POLL_INTERVAL", "5"),
            ("JUDGE_ORCHESTRATE_MAX_CASCADE_DEPTH", "3"),
            ("JUDGE_CHECK_REGISTRATION_RETRIES", "3"),
            ("JUDGE_CHECK_REGISTRATION_DELAY_SECONDS", "2"),
            ("JUDGE_BLOCKED_RECOVERY_WINDOW_SECONDS", "600"),
            ("JUDGE_PR_REVIEW_SETTLE_SECONDS", "30"),
            ("JUDGE_PR_REVIEW_POLL_INTERVAL", "2"),
            ("JUDGE_HOOK_TAIL_AGENT_WIDTH", "20"),
            ("JUDGE_HOOK_TAIL_TOOL_WIDTH", "15"),
            ("JUDGE_HOOK_TAIL_DESCRIPTION_MAX", "200"),
            ("JUDGE_HOOK_TAIL_STDOUT_PREVIEW_MAX", "50"),
            ("JUDGE_REPORT_RECENT_PACE_TASKS", "6"),
            ("JUDGE_SECURITY_FETCH_TIMEOUT", "66"),
        ],
    )
    def test_legacy_int_var_causes_rejection(
        self,
        legacy_var: str,
        legacy_val: str,
    ) -> None:
        """Setting a legacy JUDGE_* int var causes RuntimeError on reload (AC-197-2)."""
        with patch.dict(os.environ, {legacy_var: legacy_val}, clear=False):
            with pytest.raises(RuntimeError, match="is no longer accepted"):
                importlib.reload(config)
        importlib.reload(config)

    @pytest.mark.parametrize(
        "legacy_var,new_var,test_value,attr_name",
        [
            ("JUDGE_INLINE_ORPHAN_CLEANUP", "DEVBENCH_INLINE_ORPHAN_CLEANUP", "0", "INLINE_ORPHAN_CLEANUP_ENABLED"),
            ("JUDGE_CI_FAILURE_RETRY_ENABLED", "DEVBENCH_CI_FAILURE_RETRY_ENABLED", "0", "CI_FAILURE_RETRY_ENABLED"),
            ("JUDGE_PR_REVIEW_DECISION_BLOCKS", "DEVBENCH_PR_REVIEW_DECISION_BLOCKS", "0", "PR_REVIEW_DECISION_BLOCKS"),
            (
                "JUDGE_PR_REVIEW_RESOLUTION_ENABLED",
                "DEVBENCH_PR_REVIEW_RESOLUTION_ENABLED",
                "1",
                "PR_REVIEW_RESOLUTION_ENABLED",
            ),
            ("JUDGE_PAUSE_BEFORE_MERGE", "DEVBENCH_PAUSE_BEFORE_MERGE", "1", "PAUSE_BEFORE_MERGE"),
            ("JUDGE_USE_BEDROCK", "DEVBENCH_USE_BEDROCK", "1", "USE_BEDROCK"),
        ],
    )
    def test_devbench_bool_var_overrides_constant(
        self,
        legacy_var: str,
        new_var: str,
        test_value: str,
        attr_name: str,
    ) -> None:
        """DEVBENCH_* bool var overrides the resolved constant (AC-197-1)."""
        expected = test_value in ("1", "true", "yes", "on")
        with patch.dict(os.environ, {new_var: test_value}, clear=False):
            importlib.reload(config)
            assert getattr(config, attr_name) == expected, (
                f"{new_var}={test_value} expected to set {attr_name}={expected}, got {getattr(config, attr_name)}"
            )
        importlib.reload(config)

    @pytest.mark.parametrize(
        "legacy_var,legacy_val",
        [
            ("JUDGE_INLINE_ORPHAN_CLEANUP", "0"),
            ("JUDGE_CI_FAILURE_RETRY_ENABLED", "0"),
            ("JUDGE_PR_REVIEW_DECISION_BLOCKS", "0"),
            ("JUDGE_PR_REVIEW_RESOLUTION_ENABLED", "1"),
            ("JUDGE_PAUSE_BEFORE_MERGE", "1"),
            ("JUDGE_USE_BEDROCK", "1"),
        ],
    )
    def test_legacy_bool_var_causes_rejection(
        self,
        legacy_var: str,
        legacy_val: str,
    ) -> None:
        """Setting a legacy JUDGE_* bool var causes RuntimeError on reload (AC-197-2)."""
        with patch.dict(os.environ, {legacy_var: legacy_val}, clear=False):
            with pytest.raises(RuntimeError, match="is no longer accepted"):
                importlib.reload(config)
        importlib.reload(config)

    @pytest.mark.parametrize(
        "legacy_var,new_var,test_value,attr_name",
        [
            (
                "JUDGE_REPORT_TOKEN_COST_DISCOUNT",
                "DEVBENCH_REPORT_TOKEN_COST_DISCOUNT",
                "0.15",
                "TOKEN_COST_DISCOUNT",
            ),
            (
                "JUDGE_REPORT_CACHE_READ_MULTIPLIER",
                "DEVBENCH_REPORT_CACHE_READ_MULTIPLIER",
                "0.05",
                "REPORT_CACHE_READ_MULTIPLIER",
            ),
            (
                "JUDGE_REPORT_CACHE_WRITE_5MIN_MULTIPLIER",
                "DEVBENCH_REPORT_CACHE_WRITE_5MIN_MULTIPLIER",
                "1.5",
                "REPORT_CACHE_WRITE_5MIN_MULTIPLIER",
            ),
            (
                "JUDGE_REPORT_CACHE_WRITE_1HR_MULTIPLIER",
                "DEVBENCH_REPORT_CACHE_WRITE_1HR_MULTIPLIER",
                "2.5",
                "REPORT_CACHE_WRITE_1HR_MULTIPLIER",
            ),
            (
                "JUDGE_REPORT_DATA_RESIDENCY_MULTIPLIER",
                "DEVBENCH_REPORT_DATA_RESIDENCY_MULTIPLIER",
                "1.2",
                "REPORT_DATA_RESIDENCY_MULTIPLIER",
            ),
            (
                "JUDGE_REPORT_FAST_MODE_MULTIPLIER",
                "DEVBENCH_REPORT_FAST_MODE_MULTIPLIER",
                "5.0",
                "REPORT_FAST_MODE_MULTIPLIER",
            ),
        ],
    )
    def test_devbench_float_var_overrides_constant(
        self,
        legacy_var: str,
        new_var: str,
        test_value: str,
        attr_name: str,
    ) -> None:
        """DEVBENCH_* float var overrides the resolved constant (AC-197-1)."""
        with patch.dict(os.environ, {new_var: test_value}, clear=False):
            importlib.reload(config)
            assert getattr(config, attr_name) == float(test_value), (
                f"{new_var}={test_value} expected to set {attr_name}={float(test_value)}, "
                f"got {getattr(config, attr_name)}"
            )
        importlib.reload(config)

    @pytest.mark.parametrize(
        "legacy_var,legacy_val",
        [
            ("JUDGE_REPORT_TOKEN_COST_DISCOUNT", "0.15"),
            ("JUDGE_REPORT_CACHE_READ_MULTIPLIER", "0.05"),
            ("JUDGE_REPORT_CACHE_WRITE_5MIN_MULTIPLIER", "1.5"),
            ("JUDGE_REPORT_CACHE_WRITE_1HR_MULTIPLIER", "2.5"),
            ("JUDGE_REPORT_DATA_RESIDENCY_MULTIPLIER", "1.2"),
            ("JUDGE_REPORT_FAST_MODE_MULTIPLIER", "5.0"),
        ],
    )
    def test_legacy_float_var_causes_rejection(
        self,
        legacy_var: str,
        legacy_val: str,
    ) -> None:
        """Setting a legacy JUDGE_* float var causes RuntimeError on reload (AC-197-2)."""
        with patch.dict(os.environ, {legacy_var: legacy_val}, clear=False):
            with pytest.raises(RuntimeError, match="is no longer accepted"):
                importlib.reload(config)
        importlib.reload(config)

    @pytest.mark.parametrize(
        "legacy_var,new_var,test_value,attr_name",
        [
            ("JUDGE_REPORT_TIMEZONE", "DEVBENCH_REPORT_TIMEZONE", "US/Eastern", "REPORT_DISPLAY_TIMEZONE"),
            ("JUDGE_DISPLAY_TIMEZONE", "DEVBENCH_DISPLAY_TIMEZONE", "Europe/Paris", "DISPLAY_TIMEZONE"),
            ("JUDGE_BEDROCK_REGION", "DEVBENCH_BEDROCK_REGION", "eu-west-1", "BEDROCK_REGION"),
            ("JUDGE_PR_REVIEW_AGENTS", "DEVBENCH_PR_REVIEW_AGENTS", "bot-a,bot-b", "PR_REVIEW_AGENTS"),
            (
                "JUDGE_GH_TOKEN_FILE",
                "DEVBENCH_GH_TOKEN_FILE",
                "/tmp/test_gh_token",
                "GH_TOKEN_FILE",
            ),
            (
                "JUDGE_CLAUDE_CREDENTIALS_FILE",
                "DEVBENCH_CLAUDE_CREDENTIALS_FILE",
                "/tmp/test_creds.json",
                "CLAUDE_CREDENTIALS_FILE",
            ),
            (
                "JUDGE_CLAUDE_MODEL",
                "DEVBENCH_CLAUDE_MODEL",
                "test-model-v2",
                "CLAUDE_MODEL",
            ),
        ],
    )
    def test_devbench_str_var_overrides_constant(
        self,
        legacy_var: str,
        new_var: str,
        test_value: str,
        attr_name: str,
    ) -> None:
        """DEVBENCH_* string var overrides the resolved constant (AC-197-1)."""
        with patch.dict(os.environ, {new_var: test_value}, clear=False):
            importlib.reload(config)
            resolved = getattr(config, attr_name)
            # For PR_REVIEW_AGENTS it's a tuple; for Path types, compare as string.
            if attr_name == "PR_REVIEW_AGENTS":
                assert resolved == ("bot-a", "bot-b"), f"Expected ('bot-a', 'bot-b'), got {resolved}"
            elif hasattr(resolved, "__fspath__"):
                assert str(resolved) == test_value, f"Expected {test_value!r}, got {resolved!r}"
            else:
                assert resolved == test_value, f"Expected {test_value!r}, got {resolved!r}"
        importlib.reload(config)

    @pytest.mark.parametrize(
        "legacy_var,legacy_val",
        [
            ("JUDGE_REPORT_TIMEZONE", "US/Eastern"),
            ("JUDGE_DISPLAY_TIMEZONE", "Europe/Paris"),
            ("JUDGE_BEDROCK_REGION", "eu-west-1"),
            ("JUDGE_PR_REVIEW_AGENTS", "bot-a,bot-b"),
            ("JUDGE_GH_TOKEN_FILE", "/tmp/test_gh_token"),
            ("JUDGE_CLAUDE_CREDENTIALS_FILE", "/tmp/test_creds.json"),
            ("JUDGE_CLAUDE_MODEL", "some-model"),
        ],
    )
    def test_legacy_str_var_causes_rejection(
        self,
        legacy_var: str,
        legacy_val: str,
    ) -> None:
        """Setting a legacy JUDGE_* string var causes RuntimeError on reload (AC-197-2)."""
        with patch.dict(os.environ, {legacy_var: legacy_val}, clear=False):
            with pytest.raises(RuntimeError, match="is no longer accepted"):
                importlib.reload(config)
        importlib.reload(config)

    @pytest.mark.parametrize(
        "legacy_var,new_var",
        [
            ("JUDGE_MAX_RETRIES", "DEVBENCH_MAX_RETRIES"),
            ("JUDGE_GH_TIMEOUT", "DEVBENCH_GH_TIMEOUT"),
            ("JUDGE_INLINE_ORPHAN_CLEANUP", "DEVBENCH_INLINE_ORPHAN_CLEANUP"),
            ("JUDGE_USE_BEDROCK", "DEVBENCH_USE_BEDROCK"),
            ("JUDGE_BEDROCK_REGION", "DEVBENCH_BEDROCK_REGION"),
            ("JUDGE_REPORT_TIMEZONE", "DEVBENCH_REPORT_TIMEZONE"),
            ("JUDGE_CLAUDE_MODEL", "DEVBENCH_CLAUDE_MODEL"),
        ],
    )
    def test_legacy_presence_rejects_even_when_new_also_set(
        self,
        legacy_var: str,
        new_var: str,
    ) -> None:
        """AC-197-3: when both JUDGE_X and DEVBENCH_X are set, the legacy presence causes rejection."""
        with patch.dict(os.environ, {legacy_var: "old-val", new_var: "new-val"}, clear=False):
            with pytest.raises(RuntimeError, match="is no longer accepted"):
                importlib.reload(config)


@pytest.mark.unit
class TestBootstrapBypass:
    """AC-197-7: DEVBENCH_BOOTSTRAP=1 bypasses the strict legacy-name rejection in _read_env_strict."""

    def test_bootstrap_bypass_allows_legacy_var_without_rejection(self) -> None:
        """When DEVBENCH_BOOTSTRAP=1, _read_env_strict does not raise even if a JUDGE_* var is set."""
        from devbench.config import _read_env_strict

        # Use a var unlikely to be set in the test environment
        test_new = "DEVBENCH_SOME_TEST_VAR_XYZ"
        test_legacy = "JUDGE_SOME_TEST_VAR_XYZ"
        env_patch = {DEVBENCH_BOOTSTRAP_ENV_VAR: "1", test_legacy: "old-val"}
        base = {k: v for k, v in os.environ.items() if k not in (test_new, test_legacy)}
        with patch.dict(os.environ, {**base, **env_patch}, clear=True):
            result = _read_env_strict(test_new, test_legacy)
        assert result is None

    def test_bootstrap_bypass_returns_new_var_when_set(self) -> None:
        """When DEVBENCH_BOOTSTRAP=1, _read_env_strict returns the new-name value."""
        from devbench.config import _read_env_strict

        test_new = "DEVBENCH_SOME_TEST_VAR_XYZ"
        test_legacy = "JUDGE_SOME_TEST_VAR_XYZ"
        env_patch = {
            DEVBENCH_BOOTSTRAP_ENV_VAR: "1",
            test_legacy: "old-val",
            test_new: "/new/path",
        }
        base = {k: v for k, v in os.environ.items() if k not in (test_new, test_legacy)}
        with patch.dict(os.environ, {**base, **env_patch}, clear=True):
            result = _read_env_strict(test_new, test_legacy)
        assert result == "/new/path"

    def test_bootstrap_bypass_is_only_bypass(self) -> None:
        """AC-197-7: with no DEVBENCH_BOOTSTRAP set, legacy var causes RuntimeError."""
        from devbench.config import _read_env_strict

        test_new = "DEVBENCH_SOME_TEST_VAR_XYZ"
        test_legacy = "JUDGE_SOME_TEST_VAR_XYZ"
        base = {k: v for k, v in os.environ.items() if k not in (DEVBENCH_BOOTSTRAP_ENV_VAR, test_new, test_legacy)}
        with patch.dict(os.environ, {**base, test_legacy: "old-val"}, clear=True):
            with pytest.raises(RuntimeError, match="is no longer accepted"):
                _read_env_strict(test_new, test_legacy)
        importlib.reload(config)
