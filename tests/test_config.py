"""Tests for judges.config module."""

from __future__ import annotations

import importlib
import json
import logging
import os
from pathlib import Path
from unittest.mock import patch

import pytest

from devbench import config
from devbench.config import ALLOWED_REPOS, validate_repo
from devbench.config_loader import get_schema_default

# ---------------------------------------------------------------------------
# Test constants derived from the test fixture (tests/fixtures/test_devbench.yaml)
# so that test data is not embedded as inline deployment-configuration literals.
# The fixture defines repos under "caylent-solutions"; these constants mirror that.
# ---------------------------------------------------------------------------
_FIXTURE_ORG = "caylent-solutions"
_ALLOWED_REPO_IN_FIXTURE = f"{_FIXTURE_ORG}/git-repo"
# Repo absent from ALLOWED_REPOS but from an org that IS in allowed_orgs,
# so the "not allowed" (repo-level) error is raised rather than the org-level error.
_UNKNOWN_REPO = f"{_FIXTURE_ORG}/unknown-sentinel-repo"
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
                f"Expected ALLOWED_REPOS to be a frozenset after reload, "
                f"got {type(config.ALLOWED_REPOS).__name__}"
            )
            assert len(config.ALLOWED_REPOS) > 0, (
                "Expected ALLOWED_REPOS to be non-empty (sourced from YAML fixture)"
            )

        importlib.reload(config)

    def test_judge_allowed_repos_env_var_has_no_effect(self) -> None:
        """JUDGE_ALLOWED_REPOS env var is ignored — repos come from YAML only."""
        # Capture the baseline ALLOWED_REPOS before patching.
        baseline = frozenset(config.ALLOWED_REPOS)
        assert len(baseline) > 0, (
            "Baseline ALLOWED_REPOS must be non-empty for this test to be meaningful"
        )

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
        assert repo in ALLOWED_REPOS, (
            f"Precondition failed: '{repo}' should be in ALLOWED_REPOS"
        )
        validate_repo(repo)
        assert repo in ALLOWED_REPOS, (
            f"Post-condition failed: validate_repo must not modify ALLOWED_REPOS; "
            f"'{repo}' was removed after the call"
        )

    def test_validate_repo_raises_for_unknown_repo(self) -> None:
        with pytest.raises(ValueError, match="not allowed"):
            validate_repo(_UNKNOWN_REPO)

    def test_validate_repo_rejects_wrong_org_when_allowed_gh_orgs_set(self) -> None:
        with patch.object(config, "ALLOWED_GH_ORGS", [_FIXTURE_ORG]):
            with pytest.raises(ValueError, match="allowed_orgs"):
                config.validate_repo(_WRONG_ORG_REPO)

    def test_validate_repo_skips_org_check_when_allowed_gh_orgs_empty(self) -> None:
        with patch.object(config, "ALLOWED_GH_ORGS", []):
            with pytest.raises(ValueError, match="not allowed"):
                config.validate_repo("other-org/some-repo")


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


# ---------------------------------------------------------------------------
# AC-1, AC-2, AC-3: allowed_orgs and validate_repo list semantics
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestAllowedOrgs:
    """AC-1/2/3: allowed_orgs from YAML populates ALLOWED_GH_ORGS; validate_repo list check."""

    def test_allowed_orgs_from_yaml_populates_allowed_gh_orgs(self) -> None:
        """
        AC-1: allowed_orgs in YAML populates RUNTIME_CONFIG.allowed_orgs and ALLOWED_GH_ORGS.
        Given: fixture YAML does not specify allowed_orgs (empty list)
        Then: ALLOWED_GH_ORGS is a list (may be empty)
        """
        assert isinstance(config.ALLOWED_GH_ORGS, list), (
            f"Expected ALLOWED_GH_ORGS to be a list, got {type(config.ALLOWED_GH_ORGS).__name__}"
        )

    def test_validate_repo_passes_when_org_in_allowed_orgs(self, tmp_path: Path) -> None:
        """
        AC-2: validate_repo passes when repo org is in allowed_orgs list.
        Given: ALLOWED_GH_ORGS = ['permitted-org']
        When: validate_repo is called with 'permitted-org/some-repo' in ALLOWED_REPOS
        Then: no exception is raised
        """
        with (
            patch.object(config, "ALLOWED_GH_ORGS", ["permitted-org"]),
            patch.object(config, "ALLOWED_REPOS", frozenset(["permitted-org/some-repo"])),
        ):
            config.validate_repo("permitted-org/some-repo")

    def test_validate_repo_raises_when_org_not_in_allowed_orgs(self, tmp_path: Path) -> None:
        """
        AC-3: validate_repo raises when repo org is not in allowed_orgs (when list is non-empty).
        Given: ALLOWED_GH_ORGS = ['permitted-org']
        When: validate_repo is called with a repo from 'other-org'
        Then: ValueError is raised with an org-restriction message
        """
        with (
            patch.object(config, "ALLOWED_GH_ORGS", ["permitted-org"]),
            patch.object(config, "ALLOWED_REPOS", frozenset(["other-org/some-repo"])),
        ):
            with pytest.raises(ValueError, match="allowed_orgs"):
                config.validate_repo("other-org/some-repo")

    def test_validate_repo_skips_org_check_when_allowed_orgs_empty(self) -> None:
        """
        AC-3: when allowed_orgs is empty, org check is skipped (any org in ALLOWED_REPOS passes).
        """
        with (
            patch.object(config, "ALLOWED_GH_ORGS", []),
            patch.object(config, "ALLOWED_REPOS", frozenset(["any-org/repo"])),
        ):
            config.validate_repo("any-org/repo")

    def test_validate_repo_raises_for_unknown_repo_regardless_of_orgs(self) -> None:
        """
        validate_repo still raises for repos not in ALLOWED_REPOS even when org is permitted.
        """
        with (
            patch.object(config, "ALLOWED_GH_ORGS", []),
            patch.object(config, "ALLOWED_REPOS", frozenset(["org/allowed"])),
        ):
            with pytest.raises(ValueError, match="not allowed"):
                config.validate_repo("org/not-in-list")


# ---------------------------------------------------------------------------
# AC-4: JUDGE_GH_ORG deprecated env var
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestJudgeGhOrgDeprecation:
    """AC-4: JUDGE_GH_ORG env var still restricts access but emits WARNING."""

    def test_judge_gh_org_env_var_warns_deprecated(self, caplog: pytest.LogCaptureFixture) -> None:
        """
        AC-4: When JUDGE_GH_ORG is set, a WARNING-level log message is emitted.
        """
        env_without_gh_org = {k: v for k, v in os.environ.items() if k != "JUDGE_GH_ORG"}
        with caplog.at_level(logging.WARNING, logger="devbench.config"):
            with patch.dict(os.environ, {**env_without_gh_org, "JUDGE_GH_ORG": "some-org"}, clear=True):
                importlib.reload(config)

        assert any("JUDGE_GH_ORG" in r.message and "deprecated" in r.message.lower() for r in caplog.records), (
            f"Expected a deprecation WARNING for JUDGE_GH_ORG. Log records: {[r.message for r in caplog.records]}"
        )
        importlib.reload(config)

    def test_judge_gh_org_merged_into_allowed_gh_orgs(self) -> None:
        """
        AC-4: When JUDGE_GH_ORG is set, it is included in ALLOWED_GH_ORGS.
        """
        env_without_gh_org = {k: v for k, v in os.environ.items() if k != "JUDGE_GH_ORG"}
        with patch.dict(os.environ, {**env_without_gh_org, "JUDGE_GH_ORG": "legacy-org"}, clear=True):
            importlib.reload(config)
            assert "legacy-org" in config.ALLOWED_GH_ORGS, (
                f"Expected 'legacy-org' in ALLOWED_GH_ORGS after JUDGE_GH_ORG set, "
                f"got {config.ALLOWED_GH_ORGS}"
            )

        importlib.reload(config)


# ---------------------------------------------------------------------------
# AC-5, AC-6, AC-7, AC-8, AC-9, AC-10: model configuration
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestModelConfig:
    """AC-5 through AC-10: CLAUDE_MODEL / EXECUTOR_MODEL configuration."""

    def test_judge_model_from_yaml_sets_claude_model(self) -> None:
        """
        AC-5: judge_model in YAML sets CLAUDE_MODEL when ANTHROPIC_MODEL absent.
        The test fixture sets judge_model: test-judge-model.
        """
        env_without_overrides = {
            k: v for k, v in os.environ.items()
            if k not in ("ANTHROPIC_MODEL", "JUDGE_CLAUDE_MODEL")
        }
        with patch.dict(os.environ, env_without_overrides, clear=True):
            importlib.reload(config)
            assert config.CLAUDE_MODEL == "test-judge-model", (
                f"Expected CLAUDE_MODEL='test-judge-model' from YAML, got {config.CLAUDE_MODEL!r}"
            )

        importlib.reload(config)

    def test_executor_model_from_yaml_sets_executor_model(self) -> None:
        """
        AC-6: executor_model in YAML sets EXECUTOR_MODEL when ANTHROPIC_MODEL absent.
        The test fixture sets executor_model: test-executor-model.
        """
        env_without_overrides = {
            k: v for k, v in os.environ.items()
            if k not in ("ANTHROPIC_MODEL", "JUDGE_CLAUDE_MODEL")
        }
        with patch.dict(os.environ, env_without_overrides, clear=True):
            importlib.reload(config)
            assert config.EXECUTOR_MODEL == "test-executor-model", (
                f"Expected EXECUTOR_MODEL='test-executor-model' from YAML, got {config.EXECUTOR_MODEL!r}"
            )

        importlib.reload(config)

    def test_anthropic_model_env_overrides_both_silently(self) -> None:
        """
        AC-7: ANTHROPIC_MODEL env var silently overrides both CLAUDE_MODEL and EXECUTOR_MODEL.
        """
        env_with_anthropic = {
            k: v for k, v in os.environ.items()
            if k not in ("ANTHROPIC_MODEL", "JUDGE_CLAUDE_MODEL")
        }
        env_with_anthropic["ANTHROPIC_MODEL"] = "override-model"
        with patch.dict(os.environ, env_with_anthropic, clear=True):
            importlib.reload(config)
            assert config.CLAUDE_MODEL == "override-model", (
                f"Expected CLAUDE_MODEL='override-model' from ANTHROPIC_MODEL, got {config.CLAUDE_MODEL!r}"
            )
            assert config.EXECUTOR_MODEL == "override-model", (
                f"Expected EXECUTOR_MODEL='override-model' from ANTHROPIC_MODEL, got {config.EXECUTOR_MODEL!r}"
            )

        importlib.reload(config)

    def test_judge_claude_model_env_warns_deprecated(self, caplog: pytest.LogCaptureFixture) -> None:
        """
        AC-8: JUDGE_CLAUDE_MODEL env var populates both constants but emits deprecation WARNING.
        """
        env = {
            k: v for k, v in os.environ.items()
            if k not in ("ANTHROPIC_MODEL", "JUDGE_CLAUDE_MODEL")
        }
        env["JUDGE_CLAUDE_MODEL"] = "legacy-model"
        with caplog.at_level(logging.WARNING, logger="devbench.config"):
            with patch.dict(os.environ, env, clear=True):
                importlib.reload(config)

        assert any(
            "JUDGE_CLAUDE_MODEL" in r.message and "deprecated" in r.message.lower()
            for r in caplog.records
        ), (
            f"Expected a deprecation WARNING for JUDGE_CLAUDE_MODEL. "
            f"Log records: {[r.message for r in caplog.records]}"
        )
        importlib.reload(config)

    def test_judge_claude_model_env_populates_both_constants(self) -> None:
        """
        AC-8: JUDGE_CLAUDE_MODEL populates both CLAUDE_MODEL and EXECUTOR_MODEL as fallback.
        """
        env = {
            k: v for k, v in os.environ.items()
            if k not in ("ANTHROPIC_MODEL", "JUDGE_CLAUDE_MODEL")
        }
        env["JUDGE_CLAUDE_MODEL"] = "legacy-model"
        with patch.dict(os.environ, env, clear=True):
            importlib.reload(config)
            assert config.CLAUDE_MODEL == "legacy-model", (
                f"Expected CLAUDE_MODEL='legacy-model' from JUDGE_CLAUDE_MODEL, got {config.CLAUDE_MODEL!r}"
            )
            assert config.EXECUTOR_MODEL == "legacy-model", (
                f"Expected EXECUTOR_MODEL='legacy-model' from JUDGE_CLAUDE_MODEL, got {config.EXECUTOR_MODEL!r}"
            )

        importlib.reload(config)

    def test_model_defaults_to_direct_api_id_when_no_bedrock(self, tmp_path: Path) -> None:
        """
        AC-9/AC-10: When no model is in YAML/env and use_bedrock=false,
        JUDGE_DEFAULT_MODEL_DIRECT env var is used as the auth-dependent default.
        """
        cfg = tmp_path / "minimal.yaml"
        cfg.write_text(
            "repos:\n  caylent-solutions/git-repo:\n    default_branch: main\n"
            "use_bedrock: false\n",
            encoding="utf-8",
        )
        env = {
            k: v for k, v in os.environ.items()
            if k not in ("ANTHROPIC_MODEL", "JUDGE_CLAUDE_MODEL", "JUDGE_USE_BEDROCK",
                         "JUDGE_DEFAULT_MODEL_DIRECT", "JUDGE_DEFAULT_MODEL_BEDROCK")
        }
        env["JUDGE_CONFIG_PATH"] = str(cfg)
        env["JUDGE_DEFAULT_MODEL_DIRECT"] = "env-direct-default-model"
        with patch.dict(os.environ, env, clear=True):
            importlib.reload(config)
            assert config.CLAUDE_MODEL == "env-direct-default-model", (
                f"Expected CLAUDE_MODEL='env-direct-default-model' from JUDGE_DEFAULT_MODEL_DIRECT, "
                f"got {config.CLAUDE_MODEL!r}"
            )
            assert config.EXECUTOR_MODEL == "env-direct-default-model", (
                f"Expected EXECUTOR_MODEL='env-direct-default-model' from JUDGE_DEFAULT_MODEL_DIRECT, "
                f"got {config.EXECUTOR_MODEL!r}"
            )

        importlib.reload(config)

    def test_model_defaults_to_bedrock_id_when_use_bedrock_true(self, tmp_path: Path) -> None:
        """
        AC-10: use_bedrock: true in YAML → JUDGE_DEFAULT_MODEL_BEDROCK env var used as default.
        bedrock_region is required when use_bedrock=true (fail-fast).
        """
        cfg = tmp_path / "bedrock.yaml"
        cfg.write_text(
            "repos:\n  caylent-solutions/git-repo:\n    default_branch: main\n"
            "use_bedrock: true\n"
            "bedrock_region: us-west-2\n",
            encoding="utf-8",
        )
        env = {
            k: v for k, v in os.environ.items()
            if k not in ("ANTHROPIC_MODEL", "JUDGE_CLAUDE_MODEL", "JUDGE_USE_BEDROCK",
                         "JUDGE_DEFAULT_MODEL_DIRECT", "JUDGE_DEFAULT_MODEL_BEDROCK")
        }
        env["JUDGE_CONFIG_PATH"] = str(cfg)
        env["JUDGE_DEFAULT_MODEL_BEDROCK"] = "env-bedrock-default-model"
        with patch.dict(os.environ, env, clear=True):
            importlib.reload(config)
            assert config.CLAUDE_MODEL == "env-bedrock-default-model", (
                f"Expected CLAUDE_MODEL='env-bedrock-default-model' from JUDGE_DEFAULT_MODEL_BEDROCK, "
                f"got {config.CLAUDE_MODEL!r}"
            )
            assert config.EXECUTOR_MODEL == "env-bedrock-default-model", (
                f"Expected EXECUTOR_MODEL='env-bedrock-default-model' from JUDGE_DEFAULT_MODEL_BEDROCK, "
                f"got {config.EXECUTOR_MODEL!r}"
            )

        importlib.reload(config)

    def test_bedrock_region_required_when_use_bedrock_true(self, tmp_path: Path) -> None:
        """
        BEDROCK_REGION is required when use_bedrock=true.
        If no region is available from JUDGE_BEDROCK_REGION, AWS_REGION, or YAML,
        a RuntimeError is raised at config load time (fail-fast).
        """
        cfg = tmp_path / "bedrock_no_region.yaml"
        cfg.write_text(
            "repos:\n  caylent-solutions/git-repo:\n    default_branch: main\n"
            "use_bedrock: true\n",
            encoding="utf-8",
        )
        env = {
            k: v for k, v in os.environ.items()
            if k not in ("ANTHROPIC_MODEL", "JUDGE_CLAUDE_MODEL", "JUDGE_USE_BEDROCK",
                         "JUDGE_BEDROCK_REGION", "AWS_REGION")
        }
        env["JUDGE_CONFIG_PATH"] = str(cfg)
        with patch.dict(os.environ, env, clear=True):
            with pytest.raises(RuntimeError, match="BEDROCK_REGION is required"):
                importlib.reload(config)

        importlib.reload(config)

    def test_judge_use_bedrock_env_overrides_yaml(self, tmp_path: Path) -> None:
        """
        AC-13: JUDGE_USE_BEDROCK=1 env var silently overrides YAML value.
        JUDGE_BEDROCK_REGION is provided because use_bedrock=true requires a region.
        JUDGE_DEFAULT_MODEL_BEDROCK is provided because no model is set in YAML.
        """
        cfg = tmp_path / "no_bedrock.yaml"
        cfg.write_text(
            "repos:\n  caylent-solutions/git-repo:\n    default_branch: main\n"
            "use_bedrock: false\n",
            encoding="utf-8",
        )
        env = {
            k: v for k, v in os.environ.items()
            if k not in ("ANTHROPIC_MODEL", "JUDGE_CLAUDE_MODEL", "JUDGE_USE_BEDROCK",
                         "JUDGE_BEDROCK_REGION", "AWS_REGION",
                         "JUDGE_DEFAULT_MODEL_DIRECT", "JUDGE_DEFAULT_MODEL_BEDROCK")
        }
        env["JUDGE_CONFIG_PATH"] = str(cfg)
        env["JUDGE_USE_BEDROCK"] = "1"
        env["JUDGE_BEDROCK_REGION"] = "us-east-1"
        env["JUDGE_DEFAULT_MODEL_BEDROCK"] = "env-bedrock-override-model"
        with patch.dict(os.environ, env, clear=True):
            importlib.reload(config)
            assert config.USE_BEDROCK is True, (
                f"Expected USE_BEDROCK=True when JUDGE_USE_BEDROCK=1 overrides YAML, "
                f"got {config.USE_BEDROCK!r}"
            )
            assert config.CLAUDE_MODEL == "env-bedrock-override-model", (
                f"Expected CLAUDE_MODEL='env-bedrock-override-model' when bedrock overridden via env, "
                f"got {config.CLAUDE_MODEL!r}"
            )

        importlib.reload(config)

    def test_model_default_raises_when_no_default_env_var_set(self, tmp_path: Path) -> None:
        """
        AC-9: When no model is in YAML/env AND JUDGE_DEFAULT_MODEL_DIRECT is unset,
        RuntimeError is raised at config load time (fail-fast).
        """
        cfg = tmp_path / "no_model.yaml"
        cfg.write_text(
            "repos:\n  caylent-solutions/git-repo:\n    default_branch: main\n"
            "use_bedrock: false\n",
            encoding="utf-8",
        )
        env = {
            k: v for k, v in os.environ.items()
            if k not in ("ANTHROPIC_MODEL", "JUDGE_CLAUDE_MODEL", "JUDGE_USE_BEDROCK",
                         "JUDGE_DEFAULT_MODEL_DIRECT", "JUDGE_DEFAULT_MODEL_BEDROCK")
        }
        env["JUDGE_CONFIG_PATH"] = str(cfg)
        # Do NOT set JUDGE_DEFAULT_MODEL_DIRECT → should raise
        with patch.dict(os.environ, env, clear=True):
            with pytest.raises(RuntimeError, match="JUDGE_DEFAULT_MODEL_DIRECT"):
                importlib.reload(config)

        importlib.reload(config)


# ---------------------------------------------------------------------------
# Helper: build a minimal YAML with specific timeouts/limits sections.
# ---------------------------------------------------------------------------

def _minimal_yaml_with_overrides(
    tmp_path: Path,
    *,
    timeouts: dict | None = None,
    limits: dict | None = None,
    max_retries: int | None = None,
) -> Path:
    """Return path to a minimal valid config YAML written to *tmp_path*.

    The YAML includes the test fixture's required repos/model/orgs fields and
    optionally injects a ``timeouts:`` and/or ``limits:`` block.
    """
    lines = [
        "repos:",
        "  caylent-solutions/git-repo:",
        "    default_branch: main",
        "allowed_orgs:",
        "  - caylent-solutions",
        "judge_model: test-judge-model",
        "executor_model: test-executor-model",
        "use_bedrock: false",
        "bedrock_region: us-east-1",
    ]
    if max_retries is not None:
        lines.append(f"max_retries: {max_retries}")
    if timeouts is not None:
        lines.append("timeouts:")
        for key, val in timeouts.items():
            lines.append(f"  {key}: {val}")
    if limits is not None:
        lines.append("limits:")
        for key, val in limits.items():
            lines.append(f"  {key}: {val}")
    cfg = tmp_path / "test.yaml"
    cfg.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return cfg


def _reload_with_config(path: Path, extra_env: dict | None = None) -> None:
    """Reload ``config`` module using *path* as ``JUDGE_CONFIG_PATH``.

    Strips timeout/limit env var overrides so that YAML values are visible.
    The caller is responsible for calling ``importlib.reload(config)`` to
    restore the original module state after the assertion.
    """
    _timeout_env_vars = {
        "JUDGE_GH_TIMEOUT",
        "JUDGE_GH_API_TIMEOUT",
        "JUDGE_TEST_TIMEOUT",
        "JUDGE_SECURITY_FETCH_TIMEOUT",
        "JUDGE_LLM_TIMEOUT",
        "JUDGE_COMMAND_TIMEOUT",
        "JUDGE_EXECUTOR_TIMEOUT",
        "JUDGE_EXECUTOR_MAX_TURNS",
        "JUDGE_ORCHESTRATOR_POLL_INTERVAL",
        "JUDGE_ALERT_SUMMARY_LIMIT",
        "JUDGE_OUTPUT_TRUNCATION",
        "JUDGE_LLM_EVIDENCE_TRUNCATION",
        "JUDGE_LLM_FILE_CONTEXT_LIMIT",
        "JUDGE_LLM_FILE_PREVIEW_CHARS",
        "JUDGE_MAX_RETRIES",
    }
    env = {k: v for k, v in os.environ.items() if k not in _timeout_env_vars}
    env["JUDGE_CONFIG_PATH"] = str(path)
    if extra_env:
        env.update(extra_env)
    importlib.reload(config)


# ---------------------------------------------------------------------------
# Parametrized data: (constant_name, yaml_timeout_key, env_var, yaml_value)
# Schema defaults are read via get_schema_default() — no hardcoded values here.
# ---------------------------------------------------------------------------

_TIMEOUT_CASES = [
    ("GITHUB_CHECK_TIMEOUT_SECONDS", "github_check", "JUDGE_GH_TIMEOUT", 77),
    ("GH_API_TIMEOUT", "gh_api", "JUDGE_GH_API_TIMEOUT", 77),
    ("TEST_TIMEOUT", "test", "JUDGE_TEST_TIMEOUT", 77),
    ("SECURITY_FETCH_TIMEOUT", "security_fetch", "JUDGE_SECURITY_FETCH_TIMEOUT", 77),
    ("LLM_TIMEOUT", "llm", "JUDGE_LLM_TIMEOUT", 77),
    ("COMMAND_TIMEOUT", "command", "JUDGE_COMMAND_TIMEOUT", 77),
    ("EXECUTOR_TIMEOUT", "executor", "JUDGE_EXECUTOR_TIMEOUT", 77),
    ("EXECUTOR_MAX_TURNS", "executor_max_turns", "JUDGE_EXECUTOR_MAX_TURNS", 77),
    ("ORCHESTRATOR_POLL_INTERVAL", "orchestrator_poll_interval", "JUDGE_ORCHESTRATOR_POLL_INTERVAL", 77),
]

_LIMIT_CASES = [
    ("ALERT_SUMMARY_LIMIT", "alert_summary", "JUDGE_ALERT_SUMMARY_LIMIT", 77),
    ("OUTPUT_TRUNCATION_LIMIT", "output_truncation", "JUDGE_OUTPUT_TRUNCATION", 77),
    ("LLM_EVIDENCE_TRUNCATION", "llm_evidence_truncation", "JUDGE_LLM_EVIDENCE_TRUNCATION", 77),
    ("LLM_FILE_CONTEXT_LIMIT", "llm_file_context", "JUDGE_LLM_FILE_CONTEXT_LIMIT", 77),
    ("LLM_FILE_PREVIEW_CHARS", "llm_file_preview_chars", "JUDGE_LLM_FILE_PREVIEW_CHARS", 77),
]


@pytest.mark.unit
class TestTimeoutYamlPattern:
    """AC-1/AC-2/AC-3/AC-4: yaml-with-env-override pattern for timeout constants."""

    @pytest.mark.parametrize(
        "constant_name,yaml_key,env_var,yaml_value",
        _TIMEOUT_CASES,
        ids=[c[0] for c in _TIMEOUT_CASES],
    )
    def test_timeout_from_yaml_when_env_absent(
        self,
        tmp_path: Path,
        constant_name: str,
        yaml_key: str,
        env_var: str,
        yaml_value: int,
    ) -> None:
        """
        AC-1/AC-4: YAML value sets constant when env var is absent.
        Given: timeouts.<yaml_key>: <yaml_value> in YAML, env var absent
        When: config module is loaded
        Then: config.<constant_name> == yaml_value
        """
        cfg = _minimal_yaml_with_overrides(tmp_path, timeouts={yaml_key: yaml_value})
        env = {k: v for k, v in os.environ.items() if k != env_var}
        env["JUDGE_CONFIG_PATH"] = str(cfg)
        with patch.dict(os.environ, env, clear=True):
            importlib.reload(config)
            actual = getattr(config, constant_name)
            assert actual == yaml_value, (
                f"{constant_name}: expected {yaml_value} from YAML, got {actual}"
            )
        importlib.reload(config)

    @pytest.mark.parametrize(
        "constant_name,yaml_key,env_var,yaml_value",
        _TIMEOUT_CASES,
        ids=[c[0] for c in _TIMEOUT_CASES],
    )
    def test_timeout_env_overrides_yaml_silently(
        self,
        tmp_path: Path,
        caplog: pytest.LogCaptureFixture,
        constant_name: str,
        yaml_key: str,
        env_var: str,
        yaml_value: int,
    ) -> None:
        """
        AC-2/AC-4: Env var silently overrides YAML value (no warning logged).
        Given: YAML sets timeout to yaml_value, env var set to 999
        When: config module is loaded
        Then: config.<constant_name> == 999 and no WARNING emitted
        """
        cfg = _minimal_yaml_with_overrides(tmp_path, timeouts={yaml_key: yaml_value})
        env = dict(os.environ)
        env["JUDGE_CONFIG_PATH"] = str(cfg)
        env[env_var] = "999"
        with caplog.at_level(logging.WARNING, logger="devbench.config"):
            with patch.dict(os.environ, env, clear=True):
                importlib.reload(config)
                actual = getattr(config, constant_name)
        assert actual == 999, (
            f"{constant_name}: expected 999 from env var override, got {actual}"
        )
        warning_messages = [r.message for r in caplog.records if r.levelno >= logging.WARNING]
        timeout_warnings = [m for m in warning_messages if env_var in m]
        assert not timeout_warnings, (
            f"{constant_name}: expected silent env override but got warnings: {timeout_warnings}"
        )
        importlib.reload(config)

    @pytest.mark.parametrize(
        "constant_name,yaml_key,env_var,yaml_value",
        _TIMEOUT_CASES,
        ids=[c[0] for c in _TIMEOUT_CASES],
    )
    def test_timeout_empty_env_var_raises(
        self,
        tmp_path: Path,
        constant_name: str,
        yaml_key: str,
        env_var: str,
        yaml_value: int,
    ) -> None:
        """
        Rule 11: Env var set to empty string raises ValueError (fail-fast, not silent fallback).
        Given: YAML sets timeout to yaml_value, env var set to ""
        When: config module is loaded
        Then: ValueError is raised with a message identifying the env var
        """
        cfg = _minimal_yaml_with_overrides(tmp_path, timeouts={yaml_key: yaml_value})
        env = dict(os.environ)
        env["JUDGE_CONFIG_PATH"] = str(cfg)
        env[env_var] = ""
        with patch.dict(os.environ, env, clear=True):
            with pytest.raises(ValueError, match=env_var):
                importlib.reload(config)
        importlib.reload(config)

    def test_timeouts_schema_default_when_yaml_section_omitted(self, tmp_path: Path) -> None:
        """
        AC-3: When timeouts: section is absent from YAML, schema defaults are used.
        Given: YAML has no timeouts: block, no timeout env vars set
        When: config module is loaded
        Then: all timeout constants equal their schema-defined defaults
        """
        cfg = _minimal_yaml_with_overrides(tmp_path, timeouts=None)
        timeout_env_vars = {c[2] for c in _TIMEOUT_CASES}
        env = {k: v for k, v in os.environ.items() if k not in timeout_env_vars}
        env["JUDGE_CONFIG_PATH"] = str(cfg)
        with patch.dict(os.environ, env, clear=True):
            importlib.reload(config)
            for constant_name, yaml_key, _env_var, _yaml_value in _TIMEOUT_CASES:
                schema_default = get_schema_default("timeouts", yaml_key)
                actual = getattr(config, constant_name)
                assert actual == schema_default, (
                    f"{constant_name}: expected schema default {schema_default} "
                    f"when timeouts: absent from YAML, got {actual}"
                )
        importlib.reload(config)


@pytest.mark.unit
class TestLimitYamlPattern:
    """AC-4: yaml-with-env-override pattern for limit constants."""

    @pytest.mark.parametrize(
        "constant_name,yaml_key,env_var,yaml_value",
        _LIMIT_CASES,
        ids=[c[0] for c in _LIMIT_CASES],
    )
    def test_limit_from_yaml_when_env_absent(
        self,
        tmp_path: Path,
        constant_name: str,
        yaml_key: str,
        env_var: str,
        yaml_value: int,
    ) -> None:
        """
        AC-4: YAML value sets limit constant when env var is absent.
        Given: limits.<yaml_key>: <yaml_value> in YAML, env var absent
        When: config module is loaded
        Then: config.<constant_name> == yaml_value
        """
        cfg = _minimal_yaml_with_overrides(tmp_path, limits={yaml_key: yaml_value})
        env = {k: v for k, v in os.environ.items() if k != env_var}
        env["JUDGE_CONFIG_PATH"] = str(cfg)
        with patch.dict(os.environ, env, clear=True):
            importlib.reload(config)
            actual = getattr(config, constant_name)
            assert actual == yaml_value, (
                f"{constant_name}: expected {yaml_value} from YAML, got {actual}"
            )
        importlib.reload(config)

    @pytest.mark.parametrize(
        "constant_name,yaml_key,env_var,yaml_value",
        _LIMIT_CASES,
        ids=[c[0] for c in _LIMIT_CASES],
    )
    def test_limit_env_overrides_yaml_silently(
        self,
        tmp_path: Path,
        caplog: pytest.LogCaptureFixture,
        constant_name: str,
        yaml_key: str,
        env_var: str,
        yaml_value: int,
    ) -> None:
        """
        AC-4: Env var silently overrides YAML limit value (no warning logged).
        Given: YAML sets limit to yaml_value, env var set to 888
        When: config module is loaded
        Then: config.<constant_name> == 888 and no WARNING emitted
        """
        cfg = _minimal_yaml_with_overrides(tmp_path, limits={yaml_key: yaml_value})
        env = dict(os.environ)
        env["JUDGE_CONFIG_PATH"] = str(cfg)
        env[env_var] = "888"
        with caplog.at_level(logging.WARNING, logger="devbench.config"):
            with patch.dict(os.environ, env, clear=True):
                importlib.reload(config)
                actual = getattr(config, constant_name)
        assert actual == 888, (
            f"{constant_name}: expected 888 from env var override, got {actual}"
        )
        warning_messages = [r.message for r in caplog.records if r.levelno >= logging.WARNING]
        limit_warnings = [m for m in warning_messages if env_var in m]
        assert not limit_warnings, (
            f"{constant_name}: expected silent env override but got warnings: {limit_warnings}"
        )
        importlib.reload(config)

    @pytest.mark.parametrize(
        "constant_name,yaml_key,env_var,yaml_value",
        _LIMIT_CASES,
        ids=[c[0] for c in _LIMIT_CASES],
    )
    def test_limit_empty_env_var_raises(
        self,
        tmp_path: Path,
        constant_name: str,
        yaml_key: str,
        env_var: str,
        yaml_value: int,
    ) -> None:
        """
        Rule 11: Env var set to empty string raises ValueError (fail-fast, not silent fallback).
        Given: YAML sets limit to yaml_value, env var set to ""
        When: config module is loaded
        Then: ValueError is raised with a message identifying the env var
        """
        cfg = _minimal_yaml_with_overrides(tmp_path, limits={yaml_key: yaml_value})
        env = dict(os.environ)
        env["JUDGE_CONFIG_PATH"] = str(cfg)
        env[env_var] = ""
        with patch.dict(os.environ, env, clear=True):
            with pytest.raises(ValueError, match=env_var):
                importlib.reload(config)
        importlib.reload(config)

    def test_limits_schema_default_when_yaml_section_omitted(self, tmp_path: Path) -> None:
        """
        AC-3/AC-4: When limits: section is absent from YAML, schema defaults are used.
        Given: YAML has no limits: block, no limit env vars set
        When: config module is loaded
        Then: all limit constants equal their schema-defined defaults
        """
        cfg = _minimal_yaml_with_overrides(tmp_path, limits=None)
        limit_env_vars = {c[2] for c in _LIMIT_CASES}
        env = {k: v for k, v in os.environ.items() if k not in limit_env_vars}
        env["JUDGE_CONFIG_PATH"] = str(cfg)
        with patch.dict(os.environ, env, clear=True):
            importlib.reload(config)
            for constant_name, yaml_key, _env_var, _yaml_value in _LIMIT_CASES:
                schema_default = get_schema_default("limits", yaml_key)
                actual = getattr(config, constant_name)
                assert actual == schema_default, (
                    f"{constant_name}: expected schema default {schema_default} "
                    f"when limits: absent from YAML, got {actual}"
                )
        importlib.reload(config)


@pytest.mark.unit
class TestMaxRetriesYamlPattern:
    """AC-5: yaml-with-env-override pattern for MAX_RETRY_ATTEMPTS."""

    def test_max_retries_from_yaml(self, tmp_path: Path) -> None:
        """
        AC-5: max_retries in YAML sets MAX_RETRY_ATTEMPTS when JUDGE_MAX_RETRIES absent.
        Given: max_retries: 5 in YAML, JUDGE_MAX_RETRIES not set
        When: config module is loaded
        Then: config.MAX_RETRY_ATTEMPTS == 5
        """
        cfg = _minimal_yaml_with_overrides(tmp_path, max_retries=5)
        env = {k: v for k, v in os.environ.items() if k != "JUDGE_MAX_RETRIES"}
        env["JUDGE_CONFIG_PATH"] = str(cfg)
        with patch.dict(os.environ, env, clear=True):
            importlib.reload(config)
            assert config.MAX_RETRY_ATTEMPTS == 5, (
                f"Expected MAX_RETRY_ATTEMPTS=5 from YAML, got {config.MAX_RETRY_ATTEMPTS}"
            )
        importlib.reload(config)

    def test_max_retries_env_overrides_yaml_silently(
        self, tmp_path: Path, caplog: pytest.LogCaptureFixture
    ) -> None:
        """
        AC-5: JUDGE_MAX_RETRIES silently overrides YAML max_retries (no warning logged).
        Given: max_retries: 5 in YAML, JUDGE_MAX_RETRIES=20
        When: config module is loaded
        Then: config.MAX_RETRY_ATTEMPTS == 20 and no WARNING emitted
        """
        cfg = _minimal_yaml_with_overrides(tmp_path, max_retries=5)
        env = dict(os.environ)
        env["JUDGE_CONFIG_PATH"] = str(cfg)
        env["JUDGE_MAX_RETRIES"] = "20"
        with caplog.at_level(logging.WARNING, logger="devbench.config"):
            with patch.dict(os.environ, env, clear=True):
                importlib.reload(config)
                actual = config.MAX_RETRY_ATTEMPTS
        assert actual == 20, (
            f"Expected MAX_RETRY_ATTEMPTS=20 from env override, got {actual}"
        )
        warning_messages = [r.message for r in caplog.records if r.levelno >= logging.WARNING]
        retries_warnings = [m for m in warning_messages if "JUDGE_MAX_RETRIES" in m]
        assert not retries_warnings, (
            f"Expected silent env override but got warnings: {retries_warnings}"
        )
        importlib.reload(config)

    def test_max_retries_empty_env_var_raises(self, tmp_path: Path) -> None:
        """
        Rule 11: JUDGE_MAX_RETRIES set to empty string raises ValueError (fail-fast).
        Given: JUDGE_MAX_RETRIES="" (explicitly empty)
        When: config module is loaded
        Then: ValueError is raised identifying the env var
        """
        cfg = _minimal_yaml_with_overrides(tmp_path, max_retries=5)
        env = dict(os.environ)
        env["JUDGE_CONFIG_PATH"] = str(cfg)
        env["JUDGE_MAX_RETRIES"] = ""
        with patch.dict(os.environ, env, clear=True):
            with pytest.raises(ValueError, match="JUDGE_MAX_RETRIES"):
                importlib.reload(config)
        importlib.reload(config)

    def test_max_retries_schema_default_when_yaml_absent(self, tmp_path: Path) -> None:
        """
        AC-3/AC-5: When max_retries absent from YAML and JUDGE_MAX_RETRIES not set,
        MAX_RETRY_ATTEMPTS equals the schema-defined default.
        """
        cfg = _minimal_yaml_with_overrides(tmp_path, max_retries=None)
        env = {k: v for k, v in os.environ.items() if k != "JUDGE_MAX_RETRIES"}
        env["JUDGE_CONFIG_PATH"] = str(cfg)
        schema_default = get_schema_default("", "max_retries")
        with patch.dict(os.environ, env, clear=True):
            importlib.reload(config)
            actual = config.MAX_RETRY_ATTEMPTS
            assert actual == schema_default, (
                f"Expected MAX_RETRY_ATTEMPTS={schema_default} (schema default) when YAML absent, "
                f"got {actual}"
            )
        importlib.reload(config)
