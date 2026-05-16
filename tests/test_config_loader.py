"""Tests for src/devbench/config_loader.py.

Covers: path resolution precedence, YAML loading, value parsing,
configured branch lookup, and PR base-branch wiring.
"""

from __future__ import annotations

import textwrap
from pathlib import Path

import jsonschema
import pytest

from devbench.config_loader import (
    DEFAULT_CONFIG_SUBPATH,
    BacklogConfig,
    LimitConfig,
    QuotaBackoffConfig,
    QuotaHandlingConfig,
    QuotaNotifyConfig,
    QuotaRecoveryProbeConfig,
    RepoConfig,
    RuntimeConfig,
    TimeoutConfig,
    get_configured_default_branch,
    get_repo_local_path,
    load_runtime_config,
    resolve_config_path,
)
from devbench.constants import STATUS_DRAFT, STATUS_IN_QUEUE

# ---------------------------------------------------------------------------
# resolve_config_path -- AC-2
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestResolveConfigPath:
    """AC-2: config path precedence is explicit > JUDGE_CONFIG_PATH > default."""

    def test_explicit_path_wins_over_env_and_default(self, tmp_path: Path) -> None:
        """
        Given: an explicit path, an env-var path, and a workspace root
        When: resolve_config_path is called with the explicit path
        Then: the explicit path is returned
        """
        explicit = tmp_path / "custom.yaml"
        env = {"JUDGE_CONFIG_PATH": str(tmp_path / "env.yaml")}
        result = resolve_config_path(str(explicit), env, tmp_path / "workspace")
        assert result == explicit, f"Expected explicit path {explicit}, got {result}"

    def test_judge_config_path_env_wins_over_default(self, tmp_path: Path) -> None:
        """
        Given: no explicit path but JUDGE_CONFIG_PATH set
        When: resolve_config_path is called
        Then: the env-var path is returned
        """
        env_yaml = tmp_path / "env_config.yaml"
        env = {"JUDGE_CONFIG_PATH": str(env_yaml)}
        result = resolve_config_path(None, env, tmp_path / "workspace")
        assert result == env_yaml, f"Expected env path {env_yaml}, got {result}"

    def test_default_path_when_no_override(self, tmp_path: Path) -> None:
        """
        Given: no explicit path and no JUDGE_CONFIG_PATH
        When: resolve_config_path is called
        Then: the default path under workspace_root is returned
        """
        workspace = tmp_path / "workspace"
        result = resolve_config_path(None, {}, workspace)
        assert result == workspace / DEFAULT_CONFIG_SUBPATH, (
            f"Expected default path {workspace / DEFAULT_CONFIG_SUBPATH}, got {result}"
        )

    def test_explicit_none_and_empty_judge_config_path_uses_default(self, tmp_path: Path) -> None:
        """
        Given: explicit path is None and JUDGE_CONFIG_PATH is empty string
        When: resolve_config_path is called
        Then: empty JUDGE_CONFIG_PATH is treated as unset and the default path is used
        """
        workspace = tmp_path / "ws"
        result = resolve_config_path(None, {"JUDGE_CONFIG_PATH": ""}, workspace)
        assert result == workspace / DEFAULT_CONFIG_SUBPATH, (
            f"Expected default path {workspace / DEFAULT_CONFIG_SUBPATH}, got {result}"
        )


# ---------------------------------------------------------------------------
# load_runtime_config -- AC-3, AC-4, AC-5
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestLoadRuntimeConfig:
    """AC-3: env > yaml > code defaults; AC-4: repos map; AC-5: allowed repos from YAML."""

    def _write_yaml(self, path: Path, content: str) -> Path:
        path.write_text(textwrap.dedent(content), encoding="utf-8")
        return path

    def test_raises_file_not_found_when_config_missing(self, tmp_path: Path) -> None:
        """
        Given: a path to a non-existent config file
        When: load_runtime_config is called
        Then: FileNotFoundError is raised with a message referencing the missing file
        """
        missing = tmp_path / "nonexistent.yaml"
        with pytest.raises(FileNotFoundError, match="DevBench config file not found"):
            load_runtime_config(missing, {})

    def test_raises_value_error_on_invalid_yaml(self, tmp_path: Path) -> None:
        """
        Given: a config file with malformed YAML
        When: load_runtime_config is called
        Then: ValueError is raised with 'Invalid YAML' in the message
        """
        bad = tmp_path / "bad.yaml"
        bad.write_text("key: [\ninvalid", encoding="utf-8")
        with pytest.raises(ValueError, match="Invalid YAML"):
            load_runtime_config(bad, {})

    def test_raises_value_error_when_repos_missing(self, tmp_path: Path) -> None:
        """
        Given: a config file with no 'repos' key
        When: load_runtime_config is called
        Then: ValueError is raised (schema requires repos)
        """
        cfg = self._write_yaml(tmp_path / "cfg.yaml", "env:\n  FOO: bar\n")
        with pytest.raises(ValueError):
            load_runtime_config(cfg, {})

    def test_raises_value_error_when_repos_empty(self, tmp_path: Path) -> None:
        """
        Given: a config file with an empty repos map
        When: load_runtime_config is called
        Then: ValueError is raised (schema requires at least one repo)
        """
        cfg = self._write_yaml(tmp_path / "cfg.yaml", "repos: {}\n")
        with pytest.raises(ValueError):
            load_runtime_config(cfg, {})

    def test_raises_value_error_on_invalid_repo_key_format(self, tmp_path: Path) -> None:
        """
        Given: a repo key without a slash (not 'org/repo' format)
        When: load_runtime_config is called
        Then: ValueError is raised referencing the invalid key
        """
        cfg = self._write_yaml(
            tmp_path / "cfg.yaml",
            "repos:\n  notavalidrepo:\n    default_branch: main\n",
        )
        with pytest.raises(ValueError, match="notavalidrepo"):
            load_runtime_config(cfg, {})

    def test_parses_single_repo_without_default_branch(self, tmp_path: Path) -> None:
        """
        Given: a repo entry with no default_branch field
        When: load_runtime_config is called
        Then: RepoConfig.default_branch is None
        """
        cfg = self._write_yaml(tmp_path / "cfg.yaml", "repos:\n  org/repo:\n")
        result = load_runtime_config(cfg, {})
        assert "org/repo" in result.repos, "Expected 'org/repo' in repos"
        assert result.repos["org/repo"].default_branch is None, (
            "Expected default_branch to be None when not specified in YAML"
        )

    def test_parses_repo_default_branch(self, tmp_path: Path) -> None:
        """
        Given: a repo entry with default_branch: main2
        When: load_runtime_config is called
        Then: RepoConfig.default_branch equals 'main2'
        """
        cfg = self._write_yaml(
            tmp_path / "cfg.yaml",
            "repos:\n  org/repo:\n    default_branch: main2\n",
        )
        result = load_runtime_config(cfg, {})
        assert result.repos["org/repo"].default_branch == "main2", (
            f"Expected default_branch='main2', got {result.repos['org/repo'].default_branch!r}"
        )

    def test_parses_multiple_repos(self, tmp_path: Path) -> None:
        """
        Given: a config with two repos
        When: load_runtime_config is called
        Then: both repos are present with correct default branches
        """
        cfg = self._write_yaml(
            tmp_path / "cfg.yaml",
            ("repos:\n  org/repo-a:\n    default_branch: main\n  org/repo-b:\n    default_branch: develop\n"),
        )
        result = load_runtime_config(cfg, {})
        assert set(result.repos) == {"org/repo-a", "org/repo-b"}, (
            f"Expected repos {{org/repo-a, org/repo-b}}, got {set(result.repos)}"
        )
        assert result.repos["org/repo-a"].default_branch == "main", (
            f"Expected 'main', got {result.repos['org/repo-a'].default_branch!r}"
        )
        assert result.repos["org/repo-b"].default_branch == "develop", (
            f"Expected 'develop', got {result.repos['org/repo-b'].default_branch!r}"
        )

    def test_returns_runtime_config_type(self, tmp_path: Path) -> None:
        """
        Given: a valid config file
        When: load_runtime_config is called
        Then: a RuntimeConfig instance is returned
        """
        cfg = self._write_yaml(tmp_path / "cfg.yaml", "repos:\n  org/r:\n")
        result = load_runtime_config(cfg, {})
        assert isinstance(result, RuntimeConfig), f"Expected RuntimeConfig instance, got {type(result).__name__}"

    def test_raises_value_error_on_non_string_default_branch(self, tmp_path: Path) -> None:
        """
        Given: default_branch value is an integer (not a string)
        When: load_runtime_config is called
        Then: ValueError is raised referencing the type mismatch
        """
        cfg = self._write_yaml(
            tmp_path / "cfg.yaml",
            "repos:\n  org/repo:\n    default_branch: 42\n",
        )
        with pytest.raises(ValueError, match="string"):
            load_runtime_config(cfg, {})


# ---------------------------------------------------------------------------
# get_configured_default_branch -- AC-6 (pure function)
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestGetConfiguredDefaultBranch:
    """AC-6: YAML default_branch returned when configured; None when absent."""

    def test_returns_configured_branch(self) -> None:
        """
        Given: a RuntimeConfig with a repo that has default_branch set
        When: get_configured_default_branch is called
        Then: the configured branch string is returned
        """
        config = RuntimeConfig(repos={"org/repo": RepoConfig(default_branch="main2")})
        result = get_configured_default_branch("org/repo", config)
        assert result == "main2", f"Expected 'main2', got {result!r}"

    def test_returns_none_for_repo_with_no_branch(self) -> None:
        """
        Given: a RuntimeConfig where the repo exists but has no default_branch
        When: get_configured_default_branch is called
        Then: None is returned
        """
        config = RuntimeConfig(repos={"org/repo": RepoConfig(default_branch=None)})
        result = get_configured_default_branch("org/repo", config)
        assert result is None, f"Expected None for repo with no branch, got {result!r}"

    def test_returns_none_for_unknown_repo(self) -> None:
        """
        Given: a RuntimeConfig with no repos
        When: get_configured_default_branch is called for an unknown repo
        Then: None is returned
        """
        config = RuntimeConfig(repos={})
        result = get_configured_default_branch("org/unknown", config)
        assert result is None, f"Expected None for unknown repo, got {result!r}"


# ---------------------------------------------------------------------------
# RuntimeConfig / RepoConfig dataclasses
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestDataclasses:
    """Structural tests for RuntimeConfig and RepoConfig."""

    def test_runtime_config_default_repos_is_empty_dict(self) -> None:
        """RuntimeConfig() initialises with an empty repos dict."""
        cfg = RuntimeConfig()
        assert cfg.repos == {}, f"Expected empty dict, got {cfg.repos!r}"

    def test_repo_config_default_branch_none(self) -> None:
        """RepoConfig() has default_branch=None."""
        rc = RepoConfig()
        assert rc.default_branch is None, f"Expected None, got {rc.default_branch!r}"

    def test_repo_config_checkout_directory_none_by_default(self) -> None:
        """RepoConfig() has checkout_directory=None by default."""
        rc = RepoConfig()
        assert rc.checkout_directory is None, f"Expected checkout_directory=None, got {rc.checkout_directory!r}"


# ---------------------------------------------------------------------------
# checkout_directory parsing -- AC-1, AC-3, AC-4
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestCheckoutDirectory:
    """Tests for checkout_directory YAML field parsing and validation."""

    @staticmethod
    def _write(path: Path, content: str) -> Path:
        path.write_text(textwrap.dedent(content))
        return path

    def test_parses_checkout_directory(self, tmp_path: Path) -> None:
        """
        Given: a repo with checkout_directory: my-checkout
        When: load_runtime_config is called
        Then: RepoConfig.checkout_directory equals 'my-checkout'
        """
        cfg = self._write(
            tmp_path / "cfg.yaml",
            """\
            repos:
              org/repo:
                checkout_directory: my-checkout
            """,
        )
        result = load_runtime_config(cfg, {})
        assert result.repos["org/repo"].checkout_directory == "my-checkout", (
            f"Expected 'my-checkout', got {result.repos['org/repo'].checkout_directory!r}"
        )

    def test_checkout_directory_omitted_is_none(self, tmp_path: Path) -> None:
        """
        Given: a repo with no checkout_directory field
        When: load_runtime_config is called
        Then: RepoConfig.checkout_directory is None
        """
        cfg = self._write(
            tmp_path / "cfg.yaml",
            "repos:\n  org/repo:\n    default_branch: main\n",
        )
        result = load_runtime_config(cfg, {})
        assert result.repos["org/repo"].checkout_directory is None, (
            "Expected checkout_directory=None when not specified"
        )

    @pytest.mark.parametrize(
        ("yaml_value", "match"),
        [
            ("/absolute/path", "absolute"),
            ("../escape", r"\.\.|traversal"),
            ("123", "string"),
        ],
    )
    def test_checkout_directory_rejects_invalid(self, tmp_path: Path, yaml_value: str, match: str) -> None:
        """
        Given: a checkout_directory value that is invalid (absolute, traversal, or non-string)
        When: load_runtime_config is called
        Then: ValueError is raised matching the expected pattern
        """
        cfg = self._write(
            tmp_path / "cfg.yaml",
            f"repos:\n  org/repo:\n    checkout_directory: {yaml_value}\n",
        )
        with pytest.raises(ValueError, match=match):
            load_runtime_config(cfg, {})


# ---------------------------------------------------------------------------
# get_repo_local_path -- AC-2, AC-5
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestGetRepoLocalPath:
    """AC-2 and AC-5: get_repo_local_path returns checkout_directory or falls back to short name."""

    def test_uses_checkout_directory_resolved_to_workspace(self, tmp_path: Path) -> None:
        """
        Given: a repo config with checkout_directory='custom-checkout'
        When: get_repo_local_path is called
        Then: the path is workspace_root / 'custom-checkout'
        """
        config = RuntimeConfig(repos={"org/my-repo": RepoConfig(checkout_directory="custom-checkout")})
        result = get_repo_local_path("org/my-repo", config, tmp_path)
        assert result == tmp_path / "custom-checkout", f"Expected {tmp_path / 'custom-checkout'}, got {result}"

    def test_falls_back_to_repo_short_name(self, tmp_path: Path) -> None:
        """
        Given: a repo config with no checkout_directory
        When: get_repo_local_path is called
        Then: the path is workspace_root / short-name
        """
        config = RuntimeConfig(repos={"org/my-repo": RepoConfig()})
        result = get_repo_local_path("org/my-repo", config, tmp_path)
        assert result == tmp_path / "my-repo", f"Expected {tmp_path / 'my-repo'}, got {result}"

    def test_falls_back_when_repo_not_in_config(self, tmp_path: Path) -> None:
        """
        Given: a RuntimeConfig with no repos
        When: get_repo_local_path is called for a repo not in config
        Then: the path falls back to workspace_root / short-name
        """
        config = RuntimeConfig(repos={})
        result = get_repo_local_path("org/unknown-repo", config, tmp_path)
        assert result == tmp_path / "unknown-repo", f"Expected {tmp_path / 'unknown-repo'}, got {result}"

    def test_checkout_directory_none_uses_short_name(self, tmp_path: Path) -> None:
        """
        Given: a repo config with explicit checkout_directory=None
        When: get_repo_local_path is called
        Then: the path falls back to workspace_root / short-name
        """
        config = RuntimeConfig(repos={"org/my-repo": RepoConfig(checkout_directory=None)})
        result = get_repo_local_path("org/my-repo", config, tmp_path)
        assert result == tmp_path / "my-repo", f"Expected {tmp_path / 'my-repo'}, got {result}"


# ---------------------------------------------------------------------------
# JSON Schema validation -- AC-1, AC-2, AC-3, AC-4, AC-5, AC-6
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestJsonSchemaValidation:
    """Tests verifying that load_runtime_config uses JSON Schema to reject invalid YAML."""

    @staticmethod
    def _write(path: Path, content: str) -> Path:
        path.write_text(textwrap.dedent(content), encoding="utf-8")
        return path

    def test_schema_rejects_unknown_top_level_key(self, tmp_path: Path) -> None:
        """
        Given: a YAML with an unrecognised top-level key
        When: load_runtime_config is called
        Then: ValueError is raised referencing the unknown key (AC-1, AC-5)
        """
        cfg = self._write(
            tmp_path / "cfg.yaml",
            """\
            repos:
              org/repo:
                default_branch: main
            typo_key: bad_value
            """,
        )
        with pytest.raises(ValueError, match="typo_key"):
            load_runtime_config(cfg, {})

    def test_schema_rejects_unknown_repo_field(self, tmp_path: Path) -> None:
        """
        Given: a YAML repo entry with an unrecognised field
        When: load_runtime_config is called
        Then: ValueError is raised referencing the unknown field (AC-1)
        """
        cfg = self._write(
            tmp_path / "cfg.yaml",
            """\
            repos:
              org/repo:
                default_branch: main
                unknown_field: oops
            """,
        )
        with pytest.raises(ValueError, match="unknown_field"):
            load_runtime_config(cfg, {})

    def test_schema_enforces_org_repo_key_format(self, tmp_path: Path) -> None:
        """
        Given: a repo key without a slash
        When: load_runtime_config is called
        Then: ValueError is raised referencing the invalid key (AC-2)
        """
        cfg = self._write(
            tmp_path / "cfg.yaml",
            """\
            repos:
              notavalidrepo:
                default_branch: main
            """,
        )
        with pytest.raises(ValueError, match="notavalidrepo"):
            load_runtime_config(cfg, {})

    def test_schema_rejects_invalid_merge_strategy(self, tmp_path: Path) -> None:
        """
        Given: a merge_strategy value not in the allowed enum
        When: load_runtime_config is called
        Then: ValueError is raised (AC-3)
        """
        cfg = self._write(
            tmp_path / "cfg.yaml",
            """\
            repos:
              org/repo:
                default_branch: main
            merge_strategy: cherry-pick
            """,
        )
        with pytest.raises(ValueError):
            load_runtime_config(cfg, {})

    def test_schema_rejects_non_integer_timeout(self, tmp_path: Path) -> None:
        """
        Given: a timeout value that is a string instead of integer
        When: load_runtime_config is called
        Then: ValueError is raised (AC-4)
        """
        cfg = self._write(
            tmp_path / "cfg.yaml",
            """\
            repos:
              org/repo:
                default_branch: main
            timeouts:
              gh_api: "thirty"
            """,
        )
        with pytest.raises(ValueError):
            load_runtime_config(cfg, {})

    def test_schema_rejects_zero_timeout(self, tmp_path: Path) -> None:
        """
        Given: a timeout value of zero (below minimum: 1)
        When: load_runtime_config is called
        Then: ValueError is raised (AC-4)
        """
        cfg = self._write(
            tmp_path / "cfg.yaml",
            """\
            repos:
              org/repo:
                default_branch: main
            timeouts:
              gh_api: 0
            """,
        )
        with pytest.raises(ValueError):
            load_runtime_config(cfg, {})

    def test_loader_raises_valueerror_with_jsonschema_message(self, tmp_path: Path) -> None:
        """
        Given: a YAML with a schema violation
        When: load_runtime_config is called
        Then: the ValueError message contains detail from jsonschema (AC-6)
        """
        cfg = self._write(
            tmp_path / "cfg.yaml",
            """\
            repos:
              org/repo:
                default_branch: main
            unknown_key: will_be_rejected
            """,
        )
        with pytest.raises(ValueError, match="unknown_key"):
            load_runtime_config(cfg, {})


# ---------------------------------------------------------------------------
# TimeoutConfig / LimitConfig dataclasses -- AC-9
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestTimeoutConfigDefaults:
    """AC-9: TimeoutConfig fields default to None when not specified in YAML."""

    def test_timeout_config_defaults_to_none(self) -> None:
        """
        Given: TimeoutConfig() with no arguments
        When: fields are inspected
        Then: all fields are None (YAML-absent fields yield None; config.py applies env var defaults)
        """
        tc = TimeoutConfig()
        assert tc.gh_api is None, f"Expected gh_api=None, got {tc.gh_api!r}"
        assert tc.test is None, f"Expected test=None, got {tc.test!r}"
        assert tc.security_fetch is None, f"Expected security_fetch=None, got {tc.security_fetch!r}"
        assert tc.llm is None, f"Expected llm=None, got {tc.llm!r}"
        assert tc.command is None, f"Expected command=None, got {tc.command!r}"
        assert tc.executor is None, f"Expected executor=None, got {tc.executor!r}"
        assert tc.executor_max_turns is None, f"Expected executor_max_turns=None, got {tc.executor_max_turns!r}"
        assert tc.orchestrator_poll_interval is None, (
            f"Expected orchestrator_poll_interval=None, got {tc.orchestrator_poll_interval!r}"
        )
        assert tc.github_check is None, f"Expected github_check=None, got {tc.github_check!r}"


@pytest.mark.unit
class TestLimitConfigDefaults:
    """AC-9: LimitConfig fields default to None when not specified in YAML."""

    def test_limit_config_defaults_to_none(self) -> None:
        """
        Given: LimitConfig() with no arguments
        When: fields are inspected
        Then: all fields are None (YAML-absent fields yield None; config.py applies env var defaults)
        """
        lc = LimitConfig()
        assert lc.alert_summary is None, f"Expected alert_summary=None, got {lc.alert_summary!r}"
        assert lc.output_truncation is None, f"Expected output_truncation=None, got {lc.output_truncation!r}"
        assert lc.llm_evidence_truncation is None, (
            f"Expected llm_evidence_truncation=None, got {lc.llm_evidence_truncation!r}"
        )
        assert lc.llm_file_context is None, f"Expected llm_file_context=None, got {lc.llm_file_context!r}"
        assert lc.llm_file_preview_chars is None, (
            f"Expected llm_file_preview_chars=None, got {lc.llm_file_preview_chars!r}"
        )


# ---------------------------------------------------------------------------
# RuntimeConfig population from YAML -- AC-9
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestRuntimeConfigPopulation:
    """AC-9: load_runtime_config populates timeouts and limits from YAML."""

    @staticmethod
    def _write(path: Path, content: str) -> Path:
        path.write_text(textwrap.dedent(content), encoding="utf-8")
        return path

    def test_runtime_config_populates_timeouts_from_yaml(self, tmp_path: Path) -> None:
        """
        Given: YAML with a timeouts block containing gh_api and test
        When: load_runtime_config is called
        Then: RuntimeConfig.timeouts reflects the YAML values; absent fields are None
        """
        cfg = self._write(
            tmp_path / "cfg.yaml",
            """\
            repos:
              org/repo:
                default_branch: main
            timeouts:
              gh_api: 45
              test: 600
            """,
        )
        result = load_runtime_config(cfg, {})
        assert result.timeouts.gh_api == 45, f"Expected gh_api=45, got {result.timeouts.gh_api!r}"
        assert result.timeouts.test == 600, f"Expected test=600, got {result.timeouts.test!r}"
        assert result.timeouts.llm is None, f"Expected unspecified field llm=None, got {result.timeouts.llm!r}"

    def test_runtime_config_populates_limits_from_yaml(self, tmp_path: Path) -> None:
        """
        Given: YAML with a limits block containing alert_summary and output_truncation
        When: load_runtime_config is called
        Then: RuntimeConfig.limits reflects the YAML values; absent fields are None
        """
        cfg = self._write(
            tmp_path / "cfg.yaml",
            """\
            repos:
              org/repo:
                default_branch: main
            limits:
              alert_summary: 20
              output_truncation: 4000
            """,
        )
        result = load_runtime_config(cfg, {})
        assert result.limits.alert_summary == 20, f"Expected alert_summary=20, got {result.limits.alert_summary!r}"
        assert result.limits.output_truncation == 4000, (
            f"Expected output_truncation=4000, got {result.limits.output_truncation!r}"
        )
        assert result.limits.llm_evidence_truncation is None, (
            f"Expected unspecified field llm_evidence_truncation=None, got {result.limits.llm_evidence_truncation!r}"
        )

    def test_runtime_config_top_level_fields_populated(self, tmp_path: Path) -> None:
        """
        Given: YAML with top-level optional fields set
        When: load_runtime_config is called
        Then: RuntimeConfig has those values populated
        """
        cfg = self._write(
            tmp_path / "cfg.yaml",
            """\
            repos:
              org/repo:
                default_branch: main
            merge_strategy: merge
            max_executor_retries: 5
            use_bedrock: true
            bedrock_region: us-west-2
            """,
        )
        result = load_runtime_config(cfg, {})
        assert result.merge_strategy == "merge", f"Expected merge_strategy='merge', got {result.merge_strategy!r}"
        assert result.max_executor_retries == 5, f"Expected max_executor_retries=5, got {result.max_executor_retries!r}"
        assert result.use_bedrock is True, f"Expected use_bedrock=True, got {result.use_bedrock!r}"
        assert result.bedrock_region == "us-west-2", (
            f"Expected bedrock_region='us-west-2', got {result.bedrock_region!r}"
        )

    def test_runtime_config_defaults_when_optional_fields_absent(self, tmp_path: Path) -> None:
        """
        Given: YAML with only repos (no optional top-level fields)
        When: load_runtime_config is called
        Then: optional RuntimeConfig fields are None (env var defaults applied by config.py)
        """
        cfg = self._write(
            tmp_path / "cfg.yaml",
            """\
            repos:
              org/repo:
                default_branch: main
            """,
        )
        result = load_runtime_config(cfg, {})
        assert result.merge_strategy is None, (
            f"Expected merge_strategy=None when absent from YAML, got {result.merge_strategy!r}"
        )
        assert result.max_executor_retries is None, (
            f"Expected max_executor_retries=None when absent from YAML, got {result.max_executor_retries!r}"
        )
        assert result.use_bedrock is False, (
            f"Expected use_bedrock=False (explicit bool default), got {result.use_bedrock!r}"
        )
        assert result.bedrock_region is None, (
            f"Expected bedrock_region=None when absent from YAML, got {result.bedrock_region!r}"
        )
        assert result.allowed_orgs == [], f"Expected allowed_orgs=[], got {result.allowed_orgs!r}"

    def test_repo_config_merge_strategy_populated(self, tmp_path: Path) -> None:
        """
        Given: YAML with per-repo merge_strategy: rebase
        When: load_runtime_config is called
        Then: RepoConfig.merge_strategy equals 'rebase'
        """
        cfg = self._write(
            tmp_path / "cfg.yaml",
            """\
            repos:
              org/repo:
                default_branch: main
                merge_strategy: rebase
            """,
        )
        result = load_runtime_config(cfg, {})
        assert result.repos["org/repo"].merge_strategy == "rebase", (
            f"Expected merge_strategy='rebase', got {result.repos['org/repo'].merge_strategy!r}"
        )

    def test_repo_config_merge_strategy_none_by_default(self, tmp_path: Path) -> None:
        """
        Given: YAML repo with no merge_strategy
        When: load_runtime_config is called
        Then: RepoConfig.merge_strategy is None
        """
        cfg = self._write(
            tmp_path / "cfg.yaml",
            """\
            repos:
              org/repo:
                default_branch: main
            """,
        )
        result = load_runtime_config(cfg, {})
        assert result.repos["org/repo"].merge_strategy is None, (
            f"Expected merge_strategy=None, got {result.repos['org/repo'].merge_strategy!r}"
        )


# ---------------------------------------------------------------------------
# AC-10: config_loader.py does not read env vars
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestConfigLoaderNoEnvVars:
    """AC-10: config_loader module does not read any environment variables."""

    def test_config_loader_does_not_read_env_vars(self) -> None:
        """
        Given: the config_loader module source
        When: the source is inspected for os.environ/os.getenv calls
        Then: no env-var read calls are found (AC-10)
        """
        import ast
        import inspect

        import devbench.config_loader as loader_module

        source = inspect.getsource(loader_module)
        tree = ast.parse(source)

        env_read_calls: list[str] = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Attribute) and (
                isinstance(node.value, ast.Attribute)
                and isinstance(node.value.value, ast.Name)
                and node.value.value.id == "os"
                and node.value.attr == "environ"
                and node.attr == "get"
            ):
                env_read_calls.append("os.environ.get")
            if isinstance(node, ast.Call) and (
                isinstance(node.func, ast.Attribute)
                and isinstance(node.func.value, ast.Name)
                and node.func.value.id == "os"
                and node.func.attr == "getenv"
            ):
                env_read_calls.append("os.getenv")

        assert env_read_calls == [], f"config_loader.py must not read env vars -- found: {env_read_calls}"


# ---------------------------------------------------------------------------
# AC-7: checkout_directory path safety enforced post-schema
# AC-8: allowed_orgs vs repos cross-validation enforced post-schema
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestPostSchemaValidation:
    """AC-7/AC-8: Python post-schema checks for path safety and org cross-validation."""

    @staticmethod
    def _write(path: Path, content: str) -> Path:
        path.write_text(textwrap.dedent(content), encoding="utf-8")
        return path

    @pytest.mark.parametrize(
        ("checkout_dir", "error_match"),
        [
            ("/absolute/path", "absolute"),
            ("../escape", r"\.\.|traversal"),
        ],
    )
    def test_ac7_checkout_directory_unsafe_path_rejected(
        self, tmp_path: Path, checkout_dir: str, error_match: str
    ) -> None:
        """
        Given: a checkout_directory that is either absolute or contains '..'
        When: load_runtime_config is called
        Then: ValueError is raised with an appropriate message (AC-7)
        """
        cfg = self._write(
            tmp_path / "cfg.yaml",
            f"""\
            repos:
              org/repo:
                checkout_directory: {checkout_dir}
            """,
        )
        with pytest.raises(ValueError, match=error_match):
            load_runtime_config(cfg, {})

    def test_ac8_repo_org_not_in_allowed_orgs_raises(self, tmp_path: Path) -> None:
        """
        Given: allowed_orgs contains only 'permitted-org' and a repo from 'other-org' is specified
        When: load_runtime_config is called
        Then: ValueError is raised referencing the disallowed org (AC-8)
        """
        cfg = self._write(
            tmp_path / "cfg.yaml",
            """\
            allowed_orgs:
              - permitted-org
            repos:
              other-org/repo:
                default_branch: main
            """,
        )
        with pytest.raises(ValueError, match="other-org"):
            load_runtime_config(cfg, {})

    def test_ac8_repo_org_in_allowed_orgs_accepted(self, tmp_path: Path) -> None:
        """
        Given: allowed_orgs contains 'permitted-org' and all repos belong to that org
        When: load_runtime_config is called
        Then: loading succeeds without error (AC-8)
        """
        cfg = self._write(
            tmp_path / "cfg.yaml",
            """\
            allowed_orgs:
              - permitted-org
            repos:
              permitted-org/repo:
                default_branch: main
            """,
        )
        result = load_runtime_config(cfg, {})
        assert "permitted-org/repo" in result.repos, f"Expected 'permitted-org/repo' in repos, got {set(result.repos)}"

    def test_ac8_empty_allowed_orgs_allows_any_org(self, tmp_path: Path) -> None:
        """
        Given: allowed_orgs is not specified (empty)
        When: load_runtime_config is called with a repo from any org
        Then: loading succeeds (any org is permitted when allowed_orgs is empty) (AC-8)
        """
        cfg = self._write(
            tmp_path / "cfg.yaml",
            """\
            repos:
              any-org/repo:
                default_branch: main
            """,
        )
        result = load_runtime_config(cfg, {})
        assert "any-org/repo" in result.repos, f"Expected 'any-org/repo' in repos, got {set(result.repos)}"


# ---------------------------------------------------------------------------
# GitOpsConfig -- T3 AC-3, AC-4, AC-5, AC-6
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestGitOpsConfig:
    """Tests for git_ops.update_submodule config flag (T3)."""

    def _write(self, path: Path, content: str) -> Path:
        path.write_text(textwrap.dedent(content), encoding="utf-8")
        return path

    def test_update_submodule_defaults_to_false_when_git_ops_absent(self, tmp_path: Path) -> None:
        """
        Given: a config file with no 'git_ops' section
        When: load_runtime_config is called
        Then: RuntimeConfig.git_ops.update_submodule is False (AC-3)
        """
        cfg = self._write(
            tmp_path / "cfg.yaml",
            """\
            repos:
              caylent-solutions/devbench:
                default_branch: main
            """,
        )
        result = load_runtime_config(cfg, {})
        assert result.git_ops.update_submodule is False, (
            f"Expected git_ops.update_submodule=False when git_ops absent, got {result.git_ops.update_submodule}"
        )

    def test_update_submodule_defaults_to_false_when_field_absent(self, tmp_path: Path) -> None:
        """
        Given: a config file with git_ops: {} (no update_submodule key)
        When: load_runtime_config is called
        Then: RuntimeConfig.git_ops.update_submodule is False (AC-3)
        """
        cfg = self._write(
            tmp_path / "cfg.yaml",
            """\
            repos:
              caylent-solutions/devbench:
                default_branch: main
            git_ops: {}
            """,
        )
        result = load_runtime_config(cfg, {})
        assert result.git_ops.update_submodule is False, (
            f"Expected git_ops.update_submodule=False when field absent, got {result.git_ops.update_submodule}"
        )

    def test_update_submodule_true_from_yaml(self, tmp_path: Path) -> None:
        """
        Given: a config file with git_ops.update_submodule: true
        When: load_runtime_config is called
        Then: RuntimeConfig.git_ops.update_submodule is True (AC-4)
        """
        cfg = self._write(
            tmp_path / "cfg.yaml",
            """\
            repos:
              caylent-solutions/devbench:
                default_branch: main
            git_ops:
              update_submodule: true
            """,
        )
        result = load_runtime_config(cfg, {})
        assert result.git_ops.update_submodule is True, (
            f"Expected git_ops.update_submodule=True, got {result.git_ops.update_submodule}"
        )

    def test_schema_rejects_non_boolean_update_submodule(self, tmp_path: Path) -> None:
        """
        Given: a config file with git_ops.update_submodule set to a string
        When: load_runtime_config is called
        Then: ValueError is raised (schema requires boolean) (AC-5)
        """
        cfg = self._write(
            tmp_path / "cfg.yaml",
            """\
            repos:
              caylent-solutions/devbench:
                default_branch: main
            git_ops:
              update_submodule: "yes"
            """,
        )
        with pytest.raises(ValueError, match="schema validation"):
            load_runtime_config(cfg, {})

    def test_single_branch_defaults_to_none(self, tmp_path: Path) -> None:
        cfg = self._write(
            tmp_path / "cfg.yaml",
            """\
            repos:
              caylent-solutions/devbench:
                default_branch: main
            """,
        )
        result = load_runtime_config(cfg, {})
        assert result.git_ops.single_branch is None
        assert result.git_ops.defer_pr is False

    def test_single_branch_parsed_from_yaml(self, tmp_path: Path) -> None:
        cfg = self._write(
            tmp_path / "cfg.yaml",
            """\
            repos:
              caylent-solutions/devbench:
                default_branch: main
            git_ops:
              single_branch: feat/my-feature
            """,
        )
        result = load_runtime_config(cfg, {})
        assert result.git_ops.single_branch == "feat/my-feature"
        assert result.git_ops.defer_pr is False

    def test_defer_pr_parsed_from_yaml(self, tmp_path: Path) -> None:
        cfg = self._write(
            tmp_path / "cfg.yaml",
            """\
            repos:
              caylent-solutions/devbench:
                default_branch: main
            git_ops:
              single_branch: feat/my-feature
              defer_pr: true
            """,
        )
        result = load_runtime_config(cfg, {})
        assert result.git_ops.single_branch == "feat/my-feature"
        assert result.git_ops.defer_pr is True

    def test_defer_pr_without_single_branch_raises(self, tmp_path: Path) -> None:
        cfg = self._write(
            tmp_path / "cfg.yaml",
            """\
            repos:
              caylent-solutions/devbench:
                default_branch: main
            git_ops:
              defer_pr: true
            """,
        )
        with pytest.raises(ValueError, match=r"defer_pr requires.*single_branch"):
            load_runtime_config(cfg, {})

    def test_token_cost_defaults(self, tmp_path: Path) -> None:
        cfg = self._write(
            tmp_path / "cfg.yaml",
            """\
            repos:
              caylent-solutions/devbench:
                default_branch: main
            """,
        )
        result = load_runtime_config(cfg, {})
        assert result.report.token_cost_per_million_input == 5.0
        assert result.report.token_cost_per_million_output == 25.0
        # Cache multipliers default to None in the parsed YAML layer; config.py
        # applies the constant defaults via _resolve_float (env > YAML > const).
        assert result.report.cache_read_multiplier is None
        assert result.report.cache_write_5min_multiplier is None
        assert result.report.cache_write_1hr_multiplier is None
        assert result.report.data_residency_multiplier is None

    def test_token_cost_overrides_from_yaml(self, tmp_path: Path) -> None:
        cfg = self._write(
            tmp_path / "cfg.yaml",
            """\
            repos:
              caylent-solutions/devbench:
                default_branch: main
            report:
              token_cost_per_million_input: 10.0
              token_cost_per_million_output: 50.0
              cache_read_multiplier: 0.05
              cache_write_5min_multiplier: 1.5
              cache_write_1hr_multiplier: 2.5
              data_residency_multiplier: 1.2
            """,
        )
        result = load_runtime_config(cfg, {})
        assert result.report.token_cost_per_million_input == 10.0
        assert result.report.token_cost_per_million_output == 50.0
        assert result.report.cache_read_multiplier == 0.05
        assert result.report.cache_write_5min_multiplier == 1.5
        assert result.report.cache_write_1hr_multiplier == 2.5
        assert result.report.data_residency_multiplier == 1.2

    def test_token_cost_partial_override(self, tmp_path: Path) -> None:
        cfg = self._write(
            tmp_path / "cfg.yaml",
            """\
            repos:
              caylent-solutions/devbench:
                default_branch: main
            report:
              token_cost_per_million_input: 8.0
            """,
        )
        result = load_runtime_config(cfg, {})
        assert result.report.token_cost_per_million_input == 8.0
        assert result.report.token_cost_per_million_output == 25.0
        # Unspecified multiplier fields stay None (meaning "fall back to constant default").
        assert result.report.cache_read_multiplier is None

    def test_schema_rejects_unknown_git_ops_keys(self, tmp_path: Path) -> None:
        """
        Given: a config file with an unknown key under git_ops
        When: load_runtime_config is called
        Then: ValueError is raised (additionalProperties: false) (AC-6)
        """
        cfg = self._write(
            tmp_path / "cfg.yaml",
            """\
            repos:
              caylent-solutions/devbench:
                default_branch: main
            git_ops:
              update_submodule: false
              unknown_key: true
            """,
        )
        with pytest.raises(ValueError, match="schema validation"):
            load_runtime_config(cfg, {})

    def test_raises_when_yaml_is_not_mapping(self, tmp_path: Path) -> None:
        """
        Line 353: raises ValueError when YAML top-level is not a dict (e.g. a list).
        """
        cfg = tmp_path / "cfg.yaml"
        cfg.write_text("- item1\n- item2\n", encoding="utf-8")
        with pytest.raises(ValueError, match="must be a YAML mapping"):
            load_runtime_config(cfg, {})

    def test_stop_hook_max_blocks_defaults(self, tmp_path: Path) -> None:
        """stop_hook_max_blocks defaults to DEFAULT_STOP_HOOK_MAX_BLOCKS when not in YAML."""
        cfg = self._write(
            tmp_path / "cfg.yaml",
            """\
            repos:
              caylent-solutions/devbench:
                default_branch: main
            """,
        )
        result = load_runtime_config(cfg, {})
        from devbench.constants import DEFAULT_STOP_HOOK_MAX_BLOCKS

        assert result.stop_hook.max_blocks == DEFAULT_STOP_HOOK_MAX_BLOCKS

    def test_stop_hook_max_blocks_from_yaml(self, tmp_path: Path) -> None:
        """max_blocks is read from YAML stop_hook section."""
        cfg = self._write(
            tmp_path / "cfg.yaml",
            """\
            repos:
              caylent-solutions/devbench:
                default_branch: main
            stop_hook:
              max_blocks: 3
            """,
        )
        result = load_runtime_config(cfg, {})
        assert result.stop_hook.max_blocks == 3

    def test_stop_hook_window_seconds_from_yaml(self, tmp_path: Path) -> None:
        """window_seconds is read from YAML stop_hook section."""
        cfg = self._write(
            tmp_path / "cfg.yaml",
            """\
            repos:
              caylent-solutions/devbench:
                default_branch: main
            stop_hook:
              window_seconds: 60
            """,
        )
        result = load_runtime_config(cfg, {})
        assert result.stop_hook.window_seconds == 60

    def test_stop_hook_stale_task_minutes_defaults(self, tmp_path: Path) -> None:
        """stale_task_minutes defaults to DEFAULT_STOP_HOOK_STALE_TASK_MINUTES when not in YAML."""
        cfg = self._write(
            tmp_path / "cfg.yaml",
            """\
            repos:
              caylent-solutions/devbench:
                default_branch: main
            """,
        )
        result = load_runtime_config(cfg, {})
        from devbench.constants import DEFAULT_STOP_HOOK_STALE_TASK_MINUTES

        assert result.stop_hook.stale_task_minutes == DEFAULT_STOP_HOOK_STALE_TASK_MINUTES

    def test_stop_hook_stale_task_minutes_from_yaml(self, tmp_path: Path) -> None:
        """stale_task_minutes is read from YAML stop_hook section."""
        cfg = self._write(
            tmp_path / "cfg.yaml",
            """\
            repos:
              caylent-solutions/devbench:
                default_branch: main
            stop_hook:
              stale_task_minutes: 30
            """,
        )
        result = load_runtime_config(cfg, {})
        assert result.stop_hook.stale_task_minutes == 30


class TestManifestAmendmentConfig:
    """YAML loader correctly parses the opt-in manifest_amendment section."""

    @staticmethod
    def _write(path: Path, content: str) -> Path:
        import textwrap

        path.write_text(textwrap.dedent(content))
        return path

    def test_defaults_when_section_absent(self, tmp_path: Path) -> None:
        """When the manifest_amendment section is omitted, the feature is disabled."""
        cfg = self._write(
            tmp_path / "cfg.yaml",
            """\
            repos:
              caylent-solutions/devbench:
                default_branch: main
            """,
        )
        result = load_runtime_config(cfg, {})
        assert result.manifest_amendment.enabled is False
        assert result.manifest_amendment.max_requests_per_execution == 1
        assert "tdd_green_production_fix" in result.manifest_amendment.allowed_reasons

    def test_enabled_from_yaml(self, tmp_path: Path) -> None:
        cfg = self._write(
            tmp_path / "cfg.yaml",
            """\
            repos:
              caylent-solutions/devbench:
                default_branch: main
            manifest_amendment:
              enabled: true
            """,
        )
        result = load_runtime_config(cfg, {})
        assert result.manifest_amendment.enabled is True

    def test_allowed_reasons_from_yaml(self, tmp_path: Path) -> None:
        cfg = self._write(
            tmp_path / "cfg.yaml",
            """\
            repos:
              caylent-solutions/devbench:
                default_branch: main
            manifest_amendment:
              enabled: true
              allowed_reasons:
                - tdd_green_production_fix
            """,
        )
        result = load_runtime_config(cfg, {})
        assert result.manifest_amendment.allowed_reasons == frozenset({"tdd_green_production_fix"})

    def test_max_requests_per_execution_from_yaml(self, tmp_path: Path) -> None:
        cfg = self._write(
            tmp_path / "cfg.yaml",
            """\
            repos:
              caylent-solutions/devbench:
                default_branch: main
            manifest_amendment:
              enabled: true
              max_requests_per_execution: 3
            """,
        )
        result = load_runtime_config(cfg, {})
        assert result.manifest_amendment.max_requests_per_execution == 3

    def test_rejects_disallowed_reason(self, tmp_path: Path) -> None:
        """Schema enumerates allowed_reasons values; unknown values fail validation."""
        cfg = self._write(
            tmp_path / "cfg.yaml",
            """\
            repos:
              caylent-solutions/devbench:
                default_branch: main
            manifest_amendment:
              enabled: true
              allowed_reasons:
                - unknown_reason
            """,
        )
        with pytest.raises(ValueError, match="schema validation"):
            load_runtime_config(cfg, {})

    def test_rejects_unknown_key(self, tmp_path: Path) -> None:
        cfg = self._write(
            tmp_path / "cfg.yaml",
            """\
            repos:
              caylent-solutions/devbench:
                default_branch: main
            manifest_amendment:
              unknown_field: true
            """,
        )
        with pytest.raises(ValueError, match="schema validation"):
            load_runtime_config(cfg, {})


class TestTaskFactoryConfig:
    """ADR-11: YAML loader correctly parses the opt-in task_factory section including auto_accept_proposals."""

    @staticmethod
    def _write(path: Path, content: str) -> Path:
        import textwrap

        path.write_text(textwrap.dedent(content))
        return path

    def test_defaults_when_section_absent(self, tmp_path: Path) -> None:
        cfg = self._write(
            tmp_path / "cfg.yaml",
            """\
            repos:
              caylent-solutions/devbench:
                default_branch: main
            """,
        )
        result = load_runtime_config(cfg, {})
        assert result.task_factory.enabled is False
        assert result.task_factory.auto_accept_proposals is False

    def test_auto_accept_defaults_false_when_key_omitted(self, tmp_path: Path) -> None:
        """Key omitted inside an enabled task_factory block -> default False (backward-compatible)."""
        cfg = self._write(
            tmp_path / "cfg.yaml",
            """\
            repos:
              caylent-solutions/devbench:
                default_branch: main
            manifest_amendment:
              enabled: true
            task_factory:
              enabled: true
            """,
        )
        result = load_runtime_config(cfg, {})
        assert result.task_factory.enabled is True
        assert result.task_factory.auto_accept_proposals is False

    def test_auto_accept_accepts_explicit_false(self, tmp_path: Path) -> None:
        cfg = self._write(
            tmp_path / "cfg.yaml",
            """\
            repos:
              caylent-solutions/devbench:
                default_branch: main
            manifest_amendment:
              enabled: true
            task_factory:
              enabled: true
              auto_accept_proposals: false
            """,
        )
        result = load_runtime_config(cfg, {})
        assert result.task_factory.auto_accept_proposals is False

    def test_auto_accept_accepts_explicit_true(self, tmp_path: Path) -> None:
        cfg = self._write(
            tmp_path / "cfg.yaml",
            """\
            repos:
              caylent-solutions/devbench:
                default_branch: main
            manifest_amendment:
              enabled: true
            task_factory:
              enabled: true
              auto_accept_proposals: true
            """,
        )
        result = load_runtime_config(cfg, {})
        assert result.task_factory.auto_accept_proposals is True

    def test_schema_rejects_auto_accept_non_boolean_string(self, tmp_path: Path) -> None:
        """String "true" is not a YAML boolean; schema validation must reject."""
        cfg = self._write(
            tmp_path / "cfg.yaml",
            """\
            repos:
              caylent-solutions/devbench:
                default_branch: main
            manifest_amendment:
              enabled: true
            task_factory:
              enabled: true
              auto_accept_proposals: "true"
            """,
        )
        with pytest.raises(ValueError, match="schema validation"):
            load_runtime_config(cfg, {})

    def test_schema_rejects_auto_accept_integer(self, tmp_path: Path) -> None:
        """Integer 1 is not a boolean for schema purposes."""
        cfg = self._write(
            tmp_path / "cfg.yaml",
            """\
            repos:
              caylent-solutions/devbench:
                default_branch: main
            manifest_amendment:
              enabled: true
            task_factory:
              enabled: true
              auto_accept_proposals: 1
            """,
        )
        with pytest.raises(ValueError, match="schema validation"):
            load_runtime_config(cfg, {})


@pytest.mark.unit
class TestReportConfigTokenCostDiscount:
    """F1: ``report.token_cost_discount`` -- contract discount off list price."""

    @staticmethod
    def _write(path: Path, content: str) -> Path:
        path.write_text(textwrap.dedent(content))
        return path

    def test_default_is_none_when_key_absent(self, tmp_path: Path) -> None:
        cfg = self._write(
            tmp_path / "cfg.yaml",
            """\
            repos:
              org/repo:
                default_branch: main
            """,
        )
        rt = load_runtime_config(cfg, {})
        assert rt.report.token_cost_discount is None

    def test_parsed_when_key_present(self, tmp_path: Path) -> None:
        cfg = self._write(
            tmp_path / "cfg.yaml",
            """\
            repos:
              org/repo:
                default_branch: main
            report:
              token_cost_discount: 0.40363636364
            """,
        )
        rt = load_runtime_config(cfg, {})
        assert rt.report.token_cost_discount == 0.40363636364

    def test_boundary_zero_accepted(self, tmp_path: Path) -> None:
        cfg = self._write(
            tmp_path / "cfg.yaml",
            """\
            repos:
              org/repo:
                default_branch: main
            report:
              token_cost_discount: 0.0
            """,
        )
        rt = load_runtime_config(cfg, {})
        assert rt.report.token_cost_discount == 0.0

    def test_boundary_one_accepted(self, tmp_path: Path) -> None:
        cfg = self._write(
            tmp_path / "cfg.yaml",
            """\
            repos:
              org/repo:
                default_branch: main
            report:
              token_cost_discount: 1.0
            """,
        )
        rt = load_runtime_config(cfg, {})
        assert rt.report.token_cost_discount == 1.0

    def test_negative_value_rejected_by_schema(self, tmp_path: Path) -> None:
        cfg = self._write(
            tmp_path / "cfg.yaml",
            """\
            repos:
              org/repo:
                default_branch: main
            report:
              token_cost_discount: -0.1
            """,
        )
        with pytest.raises(ValueError, match="schema validation"):
            load_runtime_config(cfg, {})

    def test_above_one_rejected_by_schema(self, tmp_path: Path) -> None:
        cfg = self._write(
            tmp_path / "cfg.yaml",
            """\
            repos:
              org/repo:
                default_branch: main
            report:
              token_cost_discount: 1.5
            """,
        )
        with pytest.raises(ValueError, match="schema validation"):
            load_runtime_config(cfg, {})


@pytest.mark.unit
class TestDisplayTimezoneTopLevel:
    """F2-A: top-level ``display_timezone`` yaml key (shared by every timestamp-rendering command)."""

    @staticmethod
    def _write(path: Path, content: str) -> Path:
        path.write_text(textwrap.dedent(content))
        return path

    def test_default_is_none_when_key_absent(self, tmp_path: Path) -> None:
        cfg = self._write(
            tmp_path / "cfg.yaml",
            """\
            repos:
              org/repo:
                default_branch: main
            """,
        )
        rt = load_runtime_config(cfg, {})
        assert rt.display_timezone is None

    def test_parsed_when_present_top_level(self, tmp_path: Path) -> None:
        cfg = self._write(
            tmp_path / "cfg.yaml",
            """\
            repos:
              org/repo:
                default_branch: main
            display_timezone: America/New_York
            """,
        )
        rt = load_runtime_config(cfg, {})
        assert rt.display_timezone == "America/New_York"

    def test_independent_of_report_display_timezone(self, tmp_path: Path) -> None:
        """Top-level and report-level are independent: both can be set, neither overrides the other at load time."""
        cfg = self._write(
            tmp_path / "cfg.yaml",
            """\
            repos:
              org/repo:
                default_branch: main
            display_timezone: America/Chicago
            report:
              display_timezone: Europe/London
            """,
        )
        rt = load_runtime_config(cfg, {})
        assert rt.display_timezone == "America/Chicago"
        assert rt.report.display_timezone == "Europe/London"


class TestRepoConfigRuntimeFields:
    """E213: RepoConfig is populated with validated_repo + resolved_checkout_path at load time."""

    @staticmethod
    def _write(path: Path, content: str) -> Path:
        path.write_text(textwrap.dedent(content))
        return path

    def test_validated_repo_set_to_yaml_key(self, tmp_path: Path) -> None:
        cfg = self._write(
            tmp_path / "cfg.yaml",
            """\
            repos:
              org/repo:
                default_branch: main
            """,
        )
        rt = load_runtime_config(cfg, {})
        assert rt.repos["org/repo"].validated_repo == "org/repo"

    def test_resolved_checkout_path_uses_explicit_directory(self, tmp_path: Path) -> None:
        cfg = self._write(
            tmp_path / "cfg.yaml",
            """\
            repos:
              org/repo:
                default_branch: main
                checkout_directory: my-checkout
            """,
        )
        rt = load_runtime_config(cfg, {"JUDGE_WORKSPACE_ROOT": str(tmp_path)})
        assert rt.repos["org/repo"].resolved_checkout_path == tmp_path / "my-checkout"

    def test_resolved_checkout_path_falls_back_to_short_name(self, tmp_path: Path) -> None:
        cfg = self._write(
            tmp_path / "cfg.yaml",
            """\
            repos:
              org/repo:
                default_branch: main
            """,
        )
        rt = load_runtime_config(cfg, {"JUDGE_WORKSPACE_ROOT": str(tmp_path)})
        assert rt.repos["org/repo"].resolved_checkout_path == tmp_path / "repo"

    def test_resolved_checkout_path_none_when_workspace_unset(self, tmp_path: Path) -> None:
        cfg = self._write(
            tmp_path / "cfg.yaml",
            """\
            repos:
              org/repo:
                default_branch: main
            """,
        )
        rt = load_runtime_config(cfg, {})
        assert rt.repos["org/repo"].resolved_checkout_path is None
        # validated_repo remains populated even without workspace_root.
        assert rt.repos["org/repo"].validated_repo == "org/repo"


@pytest.mark.unit
class TestVNextCanonicalConfig:
    """v-next release: every PR-119 toggle is now a YAML field; CI retry default-on; debug section."""

    def _write(self, path: Path, content: str) -> Path:
        path.write_text(textwrap.dedent(content), encoding="utf-8")
        return path

    def test_inline_orphan_cleanup_parses_from_yaml(self, tmp_path: Path) -> None:
        cfg = self._write(
            tmp_path / "cfg.yaml",
            """\
            repos:
              org/repo: {}
            git_ops:
              inline_orphan_cleanup: false
            """,
        )
        rt = load_runtime_config(cfg, {})
        assert rt.git_ops.inline_orphan_cleanup is False

    def test_ci_failure_retry_parses_from_yaml(self, tmp_path: Path) -> None:
        cfg = self._write(
            tmp_path / "cfg.yaml",
            """\
            repos:
              org/repo: {}
            git_ops:
              ci_failure_retry: false
            """,
        )
        rt = load_runtime_config(cfg, {})
        assert rt.git_ops.ci_failure_retry is False

    def test_pause_before_merge_parses_from_yaml(self, tmp_path: Path) -> None:
        cfg = self._write(
            tmp_path / "cfg.yaml",
            """\
            repos:
              org/repo: {}
            git_ops:
              pause_before_merge: true
            """,
        )
        rt = load_runtime_config(cfg, {})
        assert rt.git_ops.pause_before_merge is True

    def test_pause_before_merge_incompatible_with_defer_pr(self, tmp_path: Path) -> None:
        cfg = self._write(
            tmp_path / "cfg.yaml",
            """\
            repos:
              org/repo: {}
            git_ops:
              single_branch: my-branch
              defer_pr: true
              pause_before_merge: true
            """,
        )
        with pytest.raises(ValueError, match=r"incompatible with .*defer_pr"):
            load_runtime_config(cfg, {})

    def test_pause_before_merge_incompatible_with_single_branch(self, tmp_path: Path) -> None:
        cfg = self._write(
            tmp_path / "cfg.yaml",
            """\
            repos:
              org/repo: {}
            git_ops:
              single_branch: my-branch
              pause_before_merge: true
            """,
        )
        with pytest.raises(ValueError, match=r"incompatible with .*single_branch"):
            load_runtime_config(cfg, {})

    def test_local_only_defaults_to_false(self, tmp_path: Path) -> None:
        cfg = self._write(
            tmp_path / "cfg.yaml",
            """\
            repos:
              org/repo: {}
            git_ops:
              update_submodule: false
            """,
        )
        rt = load_runtime_config(cfg, {})
        assert rt.git_ops.local_only is False

    def test_local_only_parses_true_with_required_companions(self, tmp_path: Path) -> None:
        cfg = self._write(
            tmp_path / "cfg.yaml",
            """\
            repos:
              org/repo:
                default_branch: main
            git_ops:
              single_branch: my-branch
              defer_pr: true
              local_only: true
            """,
        )
        rt = load_runtime_config(cfg, {})
        assert rt.git_ops.local_only is True
        assert rt.git_ops.defer_pr is True
        assert rt.git_ops.single_branch == "my-branch"

    def test_local_only_requires_defer_pr(self, tmp_path: Path) -> None:
        cfg = self._write(
            tmp_path / "cfg.yaml",
            """\
            repos:
              org/repo:
                default_branch: main
            git_ops:
              single_branch: my-branch
              local_only: true
            """,
        )
        with pytest.raises(ValueError, match=r"local_only: true requires .*defer_pr: true"):
            load_runtime_config(cfg, {})

    def test_local_only_incompatible_with_pause_before_merge(self, tmp_path: Path) -> None:
        # pause_before_merge: true is itself mutually exclusive with defer_pr: true,
        # so the only way to combine local_only + pause_before_merge is without defer_pr
        # -- which trips the local_only-requires-defer_pr check first. The schema
        # validator reaches the pause_before_merge incompatibility branch only when
        # defer_pr is also present, which the existing pause-vs-defer rule already
        # rejects. We assert the intent: local_only + pause_before_merge can never
        # coexist successfully, regardless of which validator fires first.
        cfg = self._write(
            tmp_path / "cfg.yaml",
            """\
            repos:
              org/repo:
                default_branch: main
            git_ops:
              single_branch: my-branch
              defer_pr: true
              pause_before_merge: true
              local_only: true
            """,
        )
        with pytest.raises(ValueError):
            load_runtime_config(cfg, {})

    def test_local_only_requires_default_branch_on_every_repo(self, tmp_path: Path) -> None:
        cfg = self._write(
            tmp_path / "cfg.yaml",
            """\
            repos:
              org/repo: {}
            git_ops:
              single_branch: my-branch
              defer_pr: true
              local_only: true
            """,
        )
        with pytest.raises(ValueError, match=r"requires every entry in repos: to set an explicit default_branch"):
            load_runtime_config(cfg, {})

    def test_orphan_patterns_parse_from_yaml(self, tmp_path: Path) -> None:
        cfg = self._write(
            tmp_path / "cfg.yaml",
            """\
            repos:
              org/repo: {}
            git_ops:
              orphan_patterns:
                - "**/.coverage*"
                - "**/__pycache__/**"
            """,
        )
        rt = load_runtime_config(cfg, {})
        assert rt.git_ops.orphan_patterns == ["**/.coverage*", "**/__pycache__/**"]

    def test_pr_review_resolution_parses_full_block(self, tmp_path: Path) -> None:
        cfg = self._write(
            tmp_path / "cfg.yaml",
            """\
            repos:
              org/repo: {}
            git_ops:
              pr_review_resolution:
                enabled: true
                agents: ["github-copilot[bot]", "amazon-q-developer[bot]"]
                decision_blocks: false
                settle_seconds: 90
                poll_interval: 10
            """,
        )
        rt = load_runtime_config(cfg, {})
        assert rt.git_ops.pr_review_resolution.enabled is True
        assert rt.git_ops.pr_review_resolution.agents == [
            "github-copilot[bot]",
            "amazon-q-developer[bot]",
        ]
        assert rt.git_ops.pr_review_resolution.decision_blocks is False
        assert rt.git_ops.pr_review_resolution.settle_seconds == 90
        assert rt.git_ops.pr_review_resolution.poll_interval == 10

    def test_pr_review_resolution_absent_yields_none_defaults(self, tmp_path: Path) -> None:
        cfg = self._write(
            tmp_path / "cfg.yaml",
            """\
            repos:
              org/repo: {}
            """,
        )
        rt = load_runtime_config(cfg, {})
        assert rt.git_ops.pr_review_resolution.enabled is None
        assert rt.git_ops.pr_review_resolution.agents == []

    def test_ci_failure_log_bytes_parses_from_yaml(self, tmp_path: Path) -> None:
        cfg = self._write(
            tmp_path / "cfg.yaml",
            """\
            repos:
              org/repo: {}
            limits:
              ci_failure_log_bytes: 65536
            """,
        )
        rt = load_runtime_config(cfg, {})
        assert rt.limits.ci_failure_log_bytes == 65536

    def test_debug_section_parses_full_block(self, tmp_path: Path) -> None:
        cfg = self._write(
            tmp_path / "cfg.yaml",
            """\
            repos:
              org/repo: {}
            debug:
              check_registration_retries: 20
              check_registration_delay_seconds: 10
              blocked_recovery_window_seconds: 3600
            """,
        )
        rt = load_runtime_config(cfg, {})
        assert rt.debug.check_registration_retries == 20
        assert rt.debug.check_registration_delay_seconds == 10
        assert rt.debug.blocked_recovery_window_seconds == 3600

    def test_debug_section_absent_yields_none_defaults(self, tmp_path: Path) -> None:
        cfg = self._write(
            tmp_path / "cfg.yaml",
            """\
            repos:
              org/repo: {}
            """,
        )
        rt = load_runtime_config(cfg, {})
        assert rt.debug.check_registration_retries is None
        assert rt.debug.blocked_recovery_window_seconds is None

    def test_schema_rejects_unknown_git_ops_key(self, tmp_path: Path) -> None:
        cfg = self._write(
            tmp_path / "cfg.yaml",
            """\
            repos:
              org/repo: {}
            git_ops:
              not_a_real_field: true
            """,
        )
        with pytest.raises(ValueError, match="schema validation"):
            load_runtime_config(cfg, {})

    def test_schema_rejects_non_boolean_pause_before_merge(self, tmp_path: Path) -> None:
        cfg = self._write(
            tmp_path / "cfg.yaml",
            """\
            repos:
              org/repo: {}
            git_ops:
              pause_before_merge: "yes"
            """,
        )
        with pytest.raises(ValueError, match="schema validation"):
            load_runtime_config(cfg, {})

    def test_schema_rejects_unknown_debug_key(self, tmp_path: Path) -> None:
        cfg = self._write(
            tmp_path / "cfg.yaml",
            """\
            repos:
              org/repo: {}
            debug:
              not_a_real_debug_field: 99
            """,
        )
        with pytest.raises(ValueError, match="schema validation"):
            load_runtime_config(cfg, {})


@pytest.mark.unit
class TestSampleConfigCompleteness:
    """Verify ``sample-config.yaml`` documents every YAML field defined in the schema."""

    def test_sample_config_exists(self) -> None:
        """The sample-config.yaml file must exist at the repo root."""
        assert (Path(__file__).parent.parent / "sample-config.yaml").is_file()

    def test_sample_config_loads_cleanly(self, tmp_path: Path) -> None:
        """Parsing the sample-config.yaml must not raise.

        The fixture is the canonical reference -- if it fails schema
        validation or any of the loader's cross-field checks, operators
        copying it as a starting point would hit the same failure.
        """
        sample_path = Path(__file__).parent.parent / "sample-config.yaml"
        rt = load_runtime_config(sample_path, {})
        # Sanity: the v-next defaults that operators rely on are present.
        assert rt.git_ops.inline_orphan_cleanup is True
        assert rt.git_ops.ci_failure_retry is True
        assert rt.git_ops.pause_before_merge is False

    def test_sample_config_documents_every_top_level_schema_property(self) -> None:
        """Every top-level property in config-schema.json appears in sample-config.yaml.

        Walks the schema's `properties:` map and asserts each name appears
        somewhere in the sample (commented-out lines count -- the goal is
        documentation completeness, not active configuration). When a new
        schema field is added, the operator is forced to also update the
        sample so the canonical reference stays in sync.
        """
        import json as _json
        import re as _re

        schema_path = Path(__file__).parent.parent / "src" / "devbench" / "config-schema.json"
        with schema_path.open(encoding="utf-8") as fh:
            schema = _json.load(fh)
        sample_path = Path(__file__).parent.parent / "sample-config.yaml"
        sample_text = sample_path.read_text(encoding="utf-8")
        # Strip comment markers so commented-out fields still count as documented.
        decommented = _re.sub(r"^\s*#\s?", "", sample_text, flags=_re.MULTILINE)
        missing = [
            field_name
            for field_name in schema.get("properties", {})
            if not _re.search(rf"^{_re.escape(field_name)}:", decommented, _re.MULTILINE)
        ]
        assert not missing, (
            f"sample-config.yaml is missing top-level schema field(s): {missing}. "
            "Every schema-known field must appear in the sample-config so the "
            "canonical reference covers every operator-tunable knob."
        )


class TestPerJudgeRetriesConfig:
    """Issue #122 regression: per-judge executor retry budget overrides.

    YAML schema map ``max_executor_retries_per_judge`` lets operators tune
    per-judge retries (e.g., 5 retries for flakey test_review, 2 for stable
    doc_review) without raising the global cap. Schema validation rejects
    unknown judge names; runtime helper revalidates as defense-in-depth.
    """

    @staticmethod
    def _write(path: Path, content: str) -> Path:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(textwrap.dedent(content), encoding="utf-8")
        return path

    def test_only_global_when_per_judge_absent(self, tmp_path: Path) -> None:
        cfg = self._write(
            tmp_path / "cfg.yaml",
            """\
            repos:
              org/repo:
                default_branch: main
            max_executor_retries: 7
            """,
        )
        result = load_runtime_config(cfg, {})
        assert result.max_executor_retries == 7
        assert result.max_executor_retries_per_judge == {}

    def test_per_judge_override_loaded(self, tmp_path: Path) -> None:
        cfg = self._write(
            tmp_path / "cfg.yaml",
            """\
            repos:
              org/repo:
                default_branch: main
            max_executor_retries: 5
            max_executor_retries_per_judge:
              test_review: 20
              doc_review: 2
            """,
        )
        result = load_runtime_config(cfg, {})
        assert result.max_executor_retries == 5
        assert result.max_executor_retries_per_judge == {"test_review": 20, "doc_review": 2}

    def test_schema_rejects_unknown_judge_name(self, tmp_path: Path) -> None:
        """JSONSchema additionalProperties: false rejects unknown names; the
        loader wraps the schema error in ValueError with the original cause."""
        cfg = self._write(
            tmp_path / "cfg.yaml",
            """\
            repos:
              org/repo:
                default_branch: main
            max_executor_retries_per_judge:
              not_a_real_judge: 5
            """,
        )
        with pytest.raises(ValueError, match="failed schema validation") as exc_info:
            load_runtime_config(cfg, {})
        assert isinstance(exc_info.value.__cause__, jsonschema.ValidationError)

    def test_schema_rejects_zero_or_negative_retry_value(self, tmp_path: Path) -> None:
        cfg = self._write(
            tmp_path / "cfg.yaml",
            """\
            repos:
              org/repo:
                default_branch: main
            max_executor_retries_per_judge:
              test_review: 0
            """,
        )
        with pytest.raises(ValueError, match="failed schema validation"):
            load_runtime_config(cfg, {})

    def test_loader_helper_rejects_non_mapping(self) -> None:
        from devbench.config_loader import _load_per_judge_retries

        with pytest.raises(ValueError, match="must be a mapping"):
            _load_per_judge_retries(["not", "a", "dict"])

    def test_loader_helper_returns_empty_dict_when_none(self) -> None:
        from devbench.config_loader import _load_per_judge_retries

        assert _load_per_judge_retries(None) == {}

    def test_loader_helper_rejects_unknown_judge_at_runtime(self) -> None:
        """Defense-in-depth: even if the JSONSchema layer is bypassed, the
        runtime helper rejects unknown judge names with an actionable error."""
        from devbench.config_loader import _load_per_judge_retries

        with pytest.raises(ValueError, match="unknown judge"):
            _load_per_judge_retries({"not_a_real_judge": 5})

    def test_loader_helper_rejects_bool_as_int(self) -> None:
        """Python's bool is a subclass of int; the helper must reject it
        explicitly so a YAML ``true`` doesn't silently become budget=1."""
        from devbench.config_loader import _load_per_judge_retries

        with pytest.raises(ValueError, match="positive integer"):
            _load_per_judge_retries({"test_review": True})


class TestHookTailConfig:
    """Issue #134 regression: ``hook_tail:`` YAML block loads into
    ``RuntimeConfig.hook_tail`` with env > YAML > default precedence
    plumbed through ``config.py``.

    Mirrors the existing ``TestPerJudgeRetriesConfig`` pattern (env >
    YAML > default + schema rejection of non-positive caps +
    additionalProperties strictness for unknown keys).
    """

    @staticmethod
    def _write(path: Path, content: str) -> Path:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(textwrap.dedent(content), encoding="utf-8")
        return path

    def test_absent_block_yields_all_none(self, tmp_path: Path) -> None:
        cfg = self._write(
            tmp_path / "cfg.yaml",
            """\
            repos:
              org/repo:
                default_branch: main
            """,
        )
        result = load_runtime_config(cfg, {})
        assert result.hook_tail.agent_width is None
        assert result.hook_tail.tool_width is None
        assert result.hook_tail.description_max is None
        assert result.hook_tail.stdout_preview_max is None

    def test_full_override_loaded(self, tmp_path: Path) -> None:
        cfg = self._write(
            tmp_path / "cfg.yaml",
            """\
            repos:
              org/repo:
                default_branch: main
            hook_tail:
              agent_width: 16
              tool_width: 12
              description_max: 200
              stdout_preview_max: 100
            """,
        )
        result = load_runtime_config(cfg, {})
        assert result.hook_tail.agent_width == 16
        assert result.hook_tail.tool_width == 12
        assert result.hook_tail.description_max == 200
        assert result.hook_tail.stdout_preview_max == 100

    def test_partial_override_leaves_others_none(self, tmp_path: Path) -> None:
        """An operator who only wants to bump description_max should not
        have to redeclare the other three -- they stay None and config.py
        falls back to the constants."""
        cfg = self._write(
            tmp_path / "cfg.yaml",
            """\
            repos:
              org/repo:
                default_branch: main
            hook_tail:
              description_max: 200
            """,
        )
        result = load_runtime_config(cfg, {})
        assert result.hook_tail.agent_width is None
        assert result.hook_tail.tool_width is None
        assert result.hook_tail.description_max == 200
        assert result.hook_tail.stdout_preview_max is None

    def test_schema_rejects_zero_or_negative(self, tmp_path: Path) -> None:
        cfg = self._write(
            tmp_path / "cfg.yaml",
            """\
            repos:
              org/repo:
                default_branch: main
            hook_tail:
              description_max: 0
            """,
        )
        with pytest.raises(ValueError, match="failed schema validation"):
            load_runtime_config(cfg, {})

    def test_schema_rejects_unknown_keys(self, tmp_path: Path) -> None:
        cfg = self._write(
            tmp_path / "cfg.yaml",
            """\
            repos:
              org/repo:
                default_branch: main
            hook_tail:
              not_a_real_key: 1
            """,
        )
        with pytest.raises(ValueError, match="failed schema validation") as exc_info:
            load_runtime_config(cfg, {})
        assert isinstance(exc_info.value.__cause__, jsonschema.ValidationError)


# ---------------------------------------------------------------------------
# auto_finalize + auto_merge toggle tests (AC-FUNC-001..004)
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestAutoFinalizeAutoMergeConfig:
    """Schema and loader tests for git_ops.auto_finalize and git_ops.auto_merge toggles."""

    def _write(self, path: Path, content: str) -> Path:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(textwrap.dedent(content), encoding="utf-8")
        return path

    # AC-FUNC-001: both fields default to False when absent
    def test_auto_finalize_defaults_to_false(self, tmp_path: Path) -> None:
        cfg = self._write(
            tmp_path / "cfg.yaml",
            """\
            repos:
              org/repo:
                default_branch: main
            """,
        )
        rt = load_runtime_config(cfg, {})
        assert rt.git_ops.auto_finalize is False

    def test_auto_merge_defaults_to_false(self, tmp_path: Path) -> None:
        cfg = self._write(
            tmp_path / "cfg.yaml",
            """\
            repos:
              org/repo:
                default_branch: main
            """,
        )
        rt = load_runtime_config(cfg, {})
        assert rt.git_ops.auto_merge is False

    def test_auto_finalize_true_with_defer_pr_accepted(self, tmp_path: Path) -> None:
        cfg = self._write(
            tmp_path / "cfg.yaml",
            """\
            repos:
              org/repo:
                default_branch: main
            git_ops:
              single_branch: my-branch
              defer_pr: true
              auto_finalize: true
            """,
        )
        rt = load_runtime_config(cfg, {})
        assert rt.git_ops.auto_finalize is True
        assert rt.git_ops.defer_pr is True

    def test_auto_merge_true_with_auto_finalize_and_defer_pr_accepted(self, tmp_path: Path) -> None:
        cfg = self._write(
            tmp_path / "cfg.yaml",
            """\
            repos:
              org/repo:
                default_branch: main
            git_ops:
              single_branch: my-branch
              defer_pr: true
              auto_finalize: true
              auto_merge: true
            """,
        )
        rt = load_runtime_config(cfg, {})
        assert rt.git_ops.auto_merge is True
        assert rt.git_ops.auto_finalize is True
        assert rt.git_ops.defer_pr is True

    # AC-FUNC-002: auto_finalize: true without defer_pr: true must be rejected
    def test_auto_finalize_without_defer_pr_raises(self, tmp_path: Path) -> None:
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

    # AC-FUNC-003: auto_merge: true without auto_finalize: true must be rejected
    def test_auto_merge_without_auto_finalize_raises(self, tmp_path: Path) -> None:
        cfg = self._write(
            tmp_path / "cfg.yaml",
            """\
            repos:
              org/repo:
                default_branch: main
            git_ops:
              single_branch: my-branch
              defer_pr: true
              auto_merge: true
            """,
        )
        with pytest.raises(ValueError, match=r"auto_merge: true requires .*auto_finalize: true"):
            load_runtime_config(cfg, {})

    # AC-FUNC-004: auto_merge: true + local_only: true must be rejected
    def test_auto_merge_with_local_only_raises(self, tmp_path: Path) -> None:
        cfg = self._write(
            tmp_path / "cfg.yaml",
            """\
            repos:
              org/repo:
                default_branch: main
            git_ops:
              single_branch: my-branch
              defer_pr: true
              local_only: true
              auto_finalize: true
              auto_merge: true
            """,
        )
        # The config loader rejects any combination of auto_finalize/auto_merge
        # with local_only=true because local-only repos have no remote. The
        # auto_finalize + local_only check fires first (before auto_merge +
        # local_only), so we match on "local_only: true" which appears in both
        # error messages.
        with pytest.raises(ValueError, match=r"local_only: true"):
            load_runtime_config(cfg, {})


# ---------------------------------------------------------------------------
# BacklogConfig -- AC-189-8, AC-189-9
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestBacklogConfig:
    """AC-189-8/9: BacklogConfig dataclass parses from YAML backlog: section.

    AC-189-8: backlog.default_status_for_new_work_units is honored.
    AC-189-9: Default behavior unchanged -- absent config defaults to STATUS_IN_QUEUE.
    """

    def _write(self, path: Path, content: str) -> Path:
        path.write_text(textwrap.dedent(content), encoding="utf-8")
        return path

    def test_backlog_config_default_is_in_queue(self) -> None:
        """
        Given: BacklogConfig constructed with no arguments
        When: default_status_for_new_work_units is accessed
        Then: it equals STATUS_IN_QUEUE for backwards compatibility (AC-189-9)
        """
        cfg = BacklogConfig()
        assert cfg.default_status_for_new_work_units == STATUS_IN_QUEUE

    def test_backlog_config_accepts_draft(self) -> None:
        """
        Given: BacklogConfig constructed with draft status
        When: default_status_for_new_work_units is accessed
        Then: it equals STATUS_DRAFT (AC-189-8)
        """
        cfg = BacklogConfig(default_status_for_new_work_units=STATUS_DRAFT)
        assert cfg.default_status_for_new_work_units == STATUS_DRAFT

    def test_backlog_config_accepts_in_queue(self) -> None:
        """
        Given: BacklogConfig constructed with in-queue status explicitly
        When: default_status_for_new_work_units is accessed
        Then: it equals STATUS_IN_QUEUE
        """
        cfg = BacklogConfig(default_status_for_new_work_units=STATUS_IN_QUEUE)
        assert cfg.default_status_for_new_work_units == STATUS_IN_QUEUE

    def test_runtime_config_has_backlog_field(self) -> None:
        """
        Given: RuntimeConfig constructed with no arguments
        When: the backlog field is accessed
        Then: a BacklogConfig with default STATUS_IN_QUEUE is returned (AC-189-9)
        """
        rt = RuntimeConfig()
        assert isinstance(rt.backlog, BacklogConfig)
        assert rt.backlog.default_status_for_new_work_units == STATUS_IN_QUEUE

    def test_absent_backlog_section_defaults_to_in_queue(self, tmp_path: Path) -> None:
        """
        Given: a YAML config with no backlog: section
        When: load_runtime_config is called
        Then: RuntimeConfig.backlog.default_status_for_new_work_units == STATUS_IN_QUEUE (AC-189-9)
        """
        cfg = self._write(
            tmp_path / "cfg.yaml",
            """\
            repos:
              org/repo: {}
            """,
        )
        rt = load_runtime_config(cfg, {})
        assert rt.backlog.default_status_for_new_work_units == STATUS_IN_QUEUE

    def test_backlog_section_draft_parses(self, tmp_path: Path) -> None:
        """
        Given: a YAML config with backlog.default_status_for_new_work_units: draft
        When: load_runtime_config is called
        Then: RuntimeConfig.backlog.default_status_for_new_work_units == STATUS_DRAFT (AC-189-8)
        """
        cfg = self._write(
            tmp_path / "cfg.yaml",
            """\
            repos:
              org/repo: {}
            backlog:
              default_status_for_new_work_units: draft
            """,
        )
        rt = load_runtime_config(cfg, {})
        assert rt.backlog.default_status_for_new_work_units == STATUS_DRAFT

    def test_backlog_section_in_queue_parses(self, tmp_path: Path) -> None:
        """
        Given: a YAML config with backlog.default_status_for_new_work_units: in-queue
        When: load_runtime_config is called
        Then: RuntimeConfig.backlog.default_status_for_new_work_units == STATUS_IN_QUEUE
        """
        cfg = self._write(
            tmp_path / "cfg.yaml",
            """\
            repos:
              org/repo: {}
            backlog:
              default_status_for_new_work_units: in-queue
            """,
        )
        rt = load_runtime_config(cfg, {})
        assert rt.backlog.default_status_for_new_work_units == STATUS_IN_QUEUE

    def test_invalid_status_raises_value_error(self, tmp_path: Path) -> None:
        """
        Given: a YAML config with backlog.default_status_for_new_work_units: proposed
        When: load_runtime_config is called
        Then: ValueError is raised (the schema enum catches the invalid value and
              reports the exact invalid value in the error message)
        """
        cfg = self._write(
            tmp_path / "cfg.yaml",
            """\
            repos:
              org/repo: {}
            backlog:
              default_status_for_new_work_units: proposed
            """,
        )
        with pytest.raises(ValueError, match=r"proposed"):
            load_runtime_config(cfg, {})

    def test_invalid_status_message_names_valid_values(self, tmp_path: Path) -> None:
        """
        Given: an invalid value for default_status_for_new_work_units
        When: load_runtime_config raises ValueError
        Then: the error message references the schema-level enum rejection
              (which lists the valid values 'draft' and 'in-queue')
        """
        cfg = self._write(
            tmp_path / "cfg.yaml",
            """\
            repos:
              org/repo: {}
            backlog:
              default_status_for_new_work_units: in-progress
            """,
        )
        with pytest.raises(ValueError, match=r"draft.*in-queue|in-queue.*draft|is not one of"):
            load_runtime_config(cfg, {})

    def test_schema_rejects_unknown_backlog_key(self, tmp_path: Path) -> None:
        """
        Given: a YAML config with an unknown key in the backlog: section
        When: load_runtime_config is called
        Then: ValueError is raised identifying the unknown key (JSON Schema
              additionalProperties: false)
        """
        cfg = self._write(
            tmp_path / "cfg.yaml",
            """\
            repos:
              org/repo: {}
            backlog:
              unknown_key: foo
            """,
        )
        with pytest.raises(ValueError, match=r"Additional properties are not allowed.*unknown_key"):
            load_runtime_config(cfg, {})

    def test_parse_backlog_config_raises_on_invalid_status_direct(self, tmp_path: Path) -> None:
        """
        Given: _parse_backlog_config is called directly with a dict containing
               an invalid default_status_for_new_work_units value
        When: _parse_backlog_config is invoked (bypassing JSON schema validation)
        Then: ValueError is raised naming the invalid value and the valid options

        This test covers the runtime guard in _parse_backlog_config (lines 533-540)
        that protects against callers who bypass the schema-validation layer.
        """
        from devbench.config_loader import _parse_backlog_config

        fake_path = tmp_path / "cfg.yaml"
        with pytest.raises(
            ValueError,
            match=r"must be one of.*draft.*in-queue|must be one of.*in-queue.*draft",
        ):
            _parse_backlog_config(fake_path, {"default_status_for_new_work_units": "proposed"})


@pytest.mark.unit
class TestAgentModelsConfig:
    """ADR-25 per-agent model overrides parsed from the YAML ``agents:`` block."""

    def _write(self, path: Path, content: str) -> Path:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(textwrap.dedent(content), encoding="utf-8")
        return path

    def test_section_absent_yields_default_dataclass(self, tmp_path: Path) -> None:
        cfg = self._write(
            tmp_path / "cfg.yaml",
            """\
            repos:
              org/repo:
                default_branch: main
            """,
        )
        rt = load_runtime_config(cfg, {})
        assert rt.agent_models.executor is None
        assert rt.agent_models.review_team.code_reviewer is None

    def test_top_level_override_accepted(self, tmp_path: Path) -> None:
        cfg = self._write(
            tmp_path / "cfg.yaml",
            """\
            repos:
              org/repo:
                default_branch: main
            agents:
              executor: opus
              manifest_amender: claude-opus-4-7
            """,
        )
        rt = load_runtime_config(cfg, {})
        assert rt.agent_models.executor == "opus"
        assert rt.agent_models.manifest_amender == "claude-opus-4-7"

    def test_review_team_nested_override(self, tmp_path: Path) -> None:
        cfg = self._write(
            tmp_path / "cfg.yaml",
            """\
            repos:
              org/repo:
                default_branch: main
            agents:
              review_team:
                code_reviewer: opus
                test_reviewer: haiku
            """,
        )
        rt = load_runtime_config(cfg, {})
        assert rt.agent_models.review_team.code_reviewer == "opus"
        assert rt.agent_models.review_team.test_reviewer == "haiku"
        assert rt.agent_models.review_team.doc_reviewer is None

    def test_bedrock_id_accepted_when_use_bedrock_true(self, tmp_path: Path) -> None:
        cfg = self._write(
            tmp_path / "cfg.yaml",
            """\
            repos:
              org/repo:
                default_branch: main
            use_bedrock: true
            agents:
              executor: us.anthropic.claude-opus-4-7-v1
            """,
        )
        rt = load_runtime_config(cfg, {})
        assert rt.agent_models.executor == "us.anthropic.claude-opus-4-7-v1"

    def test_short_name_rejected_when_use_bedrock_true(self, tmp_path: Path) -> None:
        cfg = self._write(
            tmp_path / "cfg.yaml",
            """\
            repos:
              org/repo:
                default_branch: main
            use_bedrock: true
            agents:
              executor: opus
            """,
        )
        with pytest.raises(ValueError, match="not a valid Bedrock model id"):
            load_runtime_config(cfg, {})

    def test_bedrock_id_rejected_when_use_bedrock_false(self, tmp_path: Path) -> None:
        cfg = self._write(
            tmp_path / "cfg.yaml",
            """\
            repos:
              org/repo:
                default_branch: main
            use_bedrock: false
            agents:
              executor: us.anthropic.claude-opus-4-7-v1
            """,
        )
        with pytest.raises(ValueError, match="not a valid Anthropic API"):
            load_runtime_config(cfg, {})

    def test_unknown_field_rejected_by_schema(self, tmp_path: Path) -> None:
        cfg = self._write(
            tmp_path / "cfg.yaml",
            """\
            repos:
              org/repo:
                default_branch: main
            agents:
              not-a-real-agent: opus
            """,
        )
        with pytest.raises(ValueError, match="schema validation"):
            load_runtime_config(cfg, {})

    def test_review_team_unknown_field_rejected_by_schema(self, tmp_path: Path) -> None:
        cfg = self._write(
            tmp_path / "cfg.yaml",
            """\
            repos:
              org/repo:
                default_branch: main
            agents:
              review_team:
                not_a_judge: opus
            """,
        )
        with pytest.raises(ValueError, match="schema validation"):
            load_runtime_config(cfg, {})

    def test_validator_helper_rejects_garbage(self, tmp_path: Path) -> None:
        """validate_agent_model_value rejects values that are neither short, claude-, nor Bedrock."""
        from devbench.config_loader import validate_agent_model_value

        with pytest.raises(ValueError, match="not a valid Anthropic API"):
            validate_agent_model_value("env JUDGE_AGENT_MODEL_X", "executor", "garbage", False)
        with pytest.raises(ValueError, match="not a valid Bedrock"):
            validate_agent_model_value("env JUDGE_AGENT_MODEL_X", "executor", "opus", True)
        # Happy paths return None.
        validate_agent_model_value("yaml", "executor", "opus", False)
        validate_agent_model_value("yaml", "executor", "claude-opus-4-7", False)
        validate_agent_model_value("yaml", "executor", "us.anthropic.claude-opus-4-7-v1", True)


# ---------------------------------------------------------------------------
# QuotaHandlingConfig -- AC-193-19, AC-193-20
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestQuotaHandlingConfig:
    """AC-193-19: safe defaults when section absent; AC-193-20: schema rejects invalid values.

    Covers the QuotaHandlingConfig, QuotaNotifyConfig, QuotaRecoveryProbeConfig,
    and QuotaBackoffConfig dataclasses added to config_loader.py (spec section 4.5.6).
    """

    def _write(self, path: Path, content: str) -> Path:
        path.write_text(textwrap.dedent(content), encoding="utf-8")
        return path

    # ------------------------------------------------------------------
    # AC-193-19: safe defaults when quota_handling section is absent
    # ------------------------------------------------------------------

    def test_quota_handling_absent_yields_defaults(self, tmp_path: Path) -> None:
        """When quota_handling is absent, RuntimeConfig.quota_handling has all defaults."""
        cfg = self._write(
            tmp_path / "cfg.yaml",
            """\
            repos:
              org/repo:
                default_branch: main
            """,
        )
        rt = load_runtime_config(cfg, {})
        qh = rt.quota_handling
        assert qh.enabled is True
        assert qh.on_exhaustion == "wait"
        assert qh.poll_interval_seconds == 60
        assert qh.max_wait_seconds == 18000
        assert qh.on_exhaustion_timeout == "drain"
        assert qh.resume_strategy == "continue_current_wu"
        assert qh.audit_comment_on_wait is True
        assert qh.audit_comment_on_resume is True
        assert qh.log_structured_events is True

    def test_quota_handling_absent_detect_modes_defaults(self, tmp_path: Path) -> None:
        """When quota_handling is absent, detect_modes contains all four default modes."""
        cfg = self._write(
            tmp_path / "cfg.yaml",
            """\
            repos:
              org/repo:
                default_branch: main
            """,
        )
        rt = load_runtime_config(cfg, {})
        assert rt.quota_handling.detect_modes == [
            "subscription_rate_limit",
            "sdk_credit_exhausted",
            "api_billing_error",
            "bedrock_throttle",
        ]

    def test_quota_handling_absent_recovery_probe_defaults(self, tmp_path: Path) -> None:
        """When quota_handling is absent, recovery_probe uses all defaults."""
        cfg = self._write(
            tmp_path / "cfg.yaml",
            """\
            repos:
              org/repo:
                default_branch: main
            """,
        )
        rt = load_runtime_config(cfg, {})
        probe = rt.quota_handling.recovery_probe
        assert probe.enabled is True
        assert probe.request_size_tokens == 1
        assert probe.timeout_seconds == 10.0

    def test_quota_handling_absent_backoff_defaults(self, tmp_path: Path) -> None:
        """When quota_handling is absent, recovery_probe.backoff uses spec defaults."""
        cfg = self._write(
            tmp_path / "cfg.yaml",
            """\
            repos:
              org/repo:
                default_branch: main
            """,
        )
        rt = load_runtime_config(cfg, {})
        backoff = rt.quota_handling.recovery_probe.backoff
        assert backoff.initial_seconds == 30.0
        assert backoff.max_seconds == 600.0
        assert backoff.multiplier == 2.0
        assert backoff.jitter == 0.2

    def test_quota_handling_absent_notify_on_pause_none(self, tmp_path: Path) -> None:
        """When quota_handling is absent, notify_on_pause is None."""
        cfg = self._write(
            tmp_path / "cfg.yaml",
            """\
            repos:
              org/repo:
                default_branch: main
            """,
        )
        rt = load_runtime_config(cfg, {})
        assert rt.quota_handling.notify_on_pause is None

    def test_quota_handling_absent_notify_on_resume_none(self, tmp_path: Path) -> None:
        """When quota_handling is absent, notify_on_resume is None."""
        cfg = self._write(
            tmp_path / "cfg.yaml",
            """\
            repos:
              org/repo:
                default_branch: main
            """,
        )
        rt = load_runtime_config(cfg, {})
        assert rt.quota_handling.notify_on_resume is None

    # ------------------------------------------------------------------
    # AC-193-19: individual YAML fields override defaults
    # ------------------------------------------------------------------

    def test_quota_handling_enabled_false_overrides_default(self, tmp_path: Path) -> None:
        """Setting enabled: false is reflected on the dataclass."""
        cfg = self._write(
            tmp_path / "cfg.yaml",
            """\
            repos:
              org/repo:
                default_branch: main
            quota_handling:
              enabled: false
            """,
        )
        rt = load_runtime_config(cfg, {})
        assert rt.quota_handling.enabled is False

    def test_quota_handling_on_exhaustion_fail(self, tmp_path: Path) -> None:
        """on_exhaustion: fail is accepted and stored."""
        cfg = self._write(
            tmp_path / "cfg.yaml",
            """\
            repos:
              org/repo:
                default_branch: main
            quota_handling:
              on_exhaustion: fail
            """,
        )
        rt = load_runtime_config(cfg, {})
        assert rt.quota_handling.on_exhaustion == "fail"

    def test_quota_handling_on_exhaustion_drain(self, tmp_path: Path) -> None:
        """on_exhaustion: drain is accepted and stored."""
        cfg = self._write(
            tmp_path / "cfg.yaml",
            """\
            repos:
              org/repo:
                default_branch: main
            quota_handling:
              on_exhaustion: drain
            """,
        )
        rt = load_runtime_config(cfg, {})
        assert rt.quota_handling.on_exhaustion == "drain"

    def test_quota_handling_poll_interval_seconds_override(self, tmp_path: Path) -> None:
        """poll_interval_seconds YAML value overrides the default."""
        cfg = self._write(
            tmp_path / "cfg.yaml",
            """\
            repos:
              org/repo:
                default_branch: main
            quota_handling:
              poll_interval_seconds: 120
            """,
        )
        rt = load_runtime_config(cfg, {})
        assert rt.quota_handling.poll_interval_seconds == 120

    def test_quota_handling_max_wait_seconds_override(self, tmp_path: Path) -> None:
        """max_wait_seconds YAML value overrides the default."""
        cfg = self._write(
            tmp_path / "cfg.yaml",
            """\
            repos:
              org/repo:
                default_branch: main
            quota_handling:
              max_wait_seconds: 3600
            """,
        )
        rt = load_runtime_config(cfg, {})
        assert rt.quota_handling.max_wait_seconds == 3600

    def test_quota_handling_on_exhaustion_timeout_fail(self, tmp_path: Path) -> None:
        """on_exhaustion_timeout: fail is accepted and stored."""
        cfg = self._write(
            tmp_path / "cfg.yaml",
            """\
            repos:
              org/repo:
                default_branch: main
            quota_handling:
              on_exhaustion_timeout: fail
            """,
        )
        rt = load_runtime_config(cfg, {})
        assert rt.quota_handling.on_exhaustion_timeout == "fail"

    def test_quota_handling_on_exhaustion_timeout_keep_waiting(self, tmp_path: Path) -> None:
        """on_exhaustion_timeout: keep_waiting is accepted and stored."""
        cfg = self._write(
            tmp_path / "cfg.yaml",
            """\
            repos:
              org/repo:
                default_branch: main
            quota_handling:
              on_exhaustion_timeout: keep_waiting
            """,
        )
        rt = load_runtime_config(cfg, {})
        assert rt.quota_handling.on_exhaustion_timeout == "keep_waiting"

    def test_quota_handling_resume_strategy_restart_wu(self, tmp_path: Path) -> None:
        """resume_strategy: restart_wu is accepted and stored."""
        cfg = self._write(
            tmp_path / "cfg.yaml",
            """\
            repos:
              org/repo:
                default_branch: main
            quota_handling:
              resume_strategy: restart_wu
            """,
        )
        rt = load_runtime_config(cfg, {})
        assert rt.quota_handling.resume_strategy == "restart_wu"

    def test_quota_handling_resume_strategy_drain_and_resume(self, tmp_path: Path) -> None:
        """resume_strategy: drain_and_resume is accepted and stored."""
        cfg = self._write(
            tmp_path / "cfg.yaml",
            """\
            repos:
              org/repo:
                default_branch: main
            quota_handling:
              resume_strategy: drain_and_resume
            """,
        )
        rt = load_runtime_config(cfg, {})
        assert rt.quota_handling.resume_strategy == "drain_and_resume"

    def test_quota_handling_audit_comment_overrides(self, tmp_path: Path) -> None:
        """audit_comment_on_wait and audit_comment_on_resume accept false."""
        cfg = self._write(
            tmp_path / "cfg.yaml",
            """\
            repos:
              org/repo:
                default_branch: main
            quota_handling:
              audit_comment_on_wait: false
              audit_comment_on_resume: false
            """,
        )
        rt = load_runtime_config(cfg, {})
        assert rt.quota_handling.audit_comment_on_wait is False
        assert rt.quota_handling.audit_comment_on_resume is False

    def test_quota_handling_log_structured_events_false(self, tmp_path: Path) -> None:
        """log_structured_events: false is stored correctly."""
        cfg = self._write(
            tmp_path / "cfg.yaml",
            """\
            repos:
              org/repo:
                default_branch: main
            quota_handling:
              log_structured_events: false
            """,
        )
        rt = load_runtime_config(cfg, {})
        assert rt.quota_handling.log_structured_events is False

    def test_quota_handling_detect_modes_subset(self, tmp_path: Path) -> None:
        """A subset of detect_modes is accepted and stored."""
        cfg = self._write(
            tmp_path / "cfg.yaml",
            """\
            repos:
              org/repo:
                default_branch: main
            quota_handling:
              detect_modes:
                - subscription_rate_limit
                - sdk_credit_exhausted
            """,
        )
        rt = load_runtime_config(cfg, {})
        assert rt.quota_handling.detect_modes == ["subscription_rate_limit", "sdk_credit_exhausted"]

    def test_quota_handling_notify_on_pause_webhook(self, tmp_path: Path) -> None:
        """notify_on_pause with webhook_url stores the URL correctly."""
        cfg = self._write(
            tmp_path / "cfg.yaml",
            """\
            repos:
              org/repo:
                default_branch: main
            quota_handling:
              notify_on_pause:
                webhook_url: "https://example.com/hook"
            """,
        )
        rt = load_runtime_config(cfg, {})
        notify = rt.quota_handling.notify_on_pause
        assert notify is not None
        assert notify.webhook_url == "https://example.com/hook"
        assert notify.slack_webhook_url is None

    def test_quota_handling_notify_on_resume_slack(self, tmp_path: Path) -> None:
        """notify_on_resume with slack_webhook_url stores the URL correctly."""
        cfg = self._write(
            tmp_path / "cfg.yaml",
            """\
            repos:
              org/repo:
                default_branch: main
            quota_handling:
              notify_on_resume:
                slack_webhook_url: "https://hooks.slack.com/services/T/B/X"
            """,
        )
        rt = load_runtime_config(cfg, {})
        notify = rt.quota_handling.notify_on_resume
        assert notify is not None
        assert notify.slack_webhook_url == "https://hooks.slack.com/services/T/B/X"
        assert notify.webhook_url is None

    def test_quota_handling_recovery_probe_overrides(self, tmp_path: Path) -> None:
        """recovery_probe fields are stored from YAML correctly."""
        cfg = self._write(
            tmp_path / "cfg.yaml",
            """\
            repos:
              org/repo:
                default_branch: main
            quota_handling:
              recovery_probe:
                enabled: false
                request_size_tokens: 5
                timeout_seconds: 30.0
            """,
        )
        rt = load_runtime_config(cfg, {})
        probe = rt.quota_handling.recovery_probe
        assert probe.enabled is False
        assert probe.request_size_tokens == 5
        assert probe.timeout_seconds == 30.0

    def test_quota_handling_backoff_overrides(self, tmp_path: Path) -> None:
        """recovery_probe.backoff fields are stored from YAML correctly."""
        cfg = self._write(
            tmp_path / "cfg.yaml",
            """\
            repos:
              org/repo:
                default_branch: main
            quota_handling:
              recovery_probe:
                backoff:
                  initial_seconds: 15.0
                  max_seconds: 300.0
                  multiplier: 1.5
                  jitter: 0.1
            """,
        )
        rt = load_runtime_config(cfg, {})
        backoff = rt.quota_handling.recovery_probe.backoff
        assert backoff.initial_seconds == 15.0
        assert backoff.max_seconds == 300.0
        assert backoff.multiplier == 1.5
        assert backoff.jitter == 0.1

    # ------------------------------------------------------------------
    # AC-193-20: schema validation rejects invalid values
    # ------------------------------------------------------------------

    def test_quota_handling_invalid_on_exhaustion_rejected(self, tmp_path: Path) -> None:
        """on_exhaustion with an invalid value triggers a schema validation error."""
        cfg = self._write(
            tmp_path / "cfg.yaml",
            """\
            repos:
              org/repo:
                default_branch: main
            quota_handling:
              on_exhaustion: invalid_value
            """,
        )
        with pytest.raises(ValueError, match="schema validation"):
            load_runtime_config(cfg, {})

    def test_quota_handling_invalid_on_exhaustion_timeout_rejected(self, tmp_path: Path) -> None:
        """on_exhaustion_timeout with an invalid value triggers schema validation."""
        cfg = self._write(
            tmp_path / "cfg.yaml",
            """\
            repos:
              org/repo:
                default_branch: main
            quota_handling:
              on_exhaustion_timeout: invalid_value
            """,
        )
        with pytest.raises(ValueError, match="schema validation"):
            load_runtime_config(cfg, {})

    def test_quota_handling_invalid_resume_strategy_rejected(self, tmp_path: Path) -> None:
        """resume_strategy with an invalid value triggers schema validation."""
        cfg = self._write(
            tmp_path / "cfg.yaml",
            """\
            repos:
              org/repo:
                default_branch: main
            quota_handling:
              resume_strategy: invalid_strategy
            """,
        )
        with pytest.raises(ValueError, match="schema validation"):
            load_runtime_config(cfg, {})

    def test_quota_handling_poll_interval_below_minimum_rejected(self, tmp_path: Path) -> None:
        """poll_interval_seconds below 30 triggers schema validation."""
        cfg = self._write(
            tmp_path / "cfg.yaml",
            """\
            repos:
              org/repo:
                default_branch: main
            quota_handling:
              poll_interval_seconds: 29
            """,
        )
        with pytest.raises(ValueError, match="schema validation"):
            load_runtime_config(cfg, {})

    def test_quota_handling_poll_interval_above_maximum_rejected(self, tmp_path: Path) -> None:
        """poll_interval_seconds above 3600 triggers schema validation."""
        cfg = self._write(
            tmp_path / "cfg.yaml",
            """\
            repos:
              org/repo:
                default_branch: main
            quota_handling:
              poll_interval_seconds: 3601
            """,
        )
        with pytest.raises(ValueError, match="schema validation"):
            load_runtime_config(cfg, {})

    def test_quota_handling_max_wait_seconds_below_minimum_rejected(self, tmp_path: Path) -> None:
        """max_wait_seconds below 1 triggers schema validation."""
        cfg = self._write(
            tmp_path / "cfg.yaml",
            """\
            repos:
              org/repo:
                default_branch: main
            quota_handling:
              max_wait_seconds: 0
            """,
        )
        with pytest.raises(ValueError, match="schema validation"):
            load_runtime_config(cfg, {})

    def test_quota_handling_invalid_detect_mode_rejected(self, tmp_path: Path) -> None:
        """An unknown detect_mode value triggers schema validation."""
        cfg = self._write(
            tmp_path / "cfg.yaml",
            """\
            repos:
              org/repo:
                default_branch: main
            quota_handling:
              detect_modes:
                - not_a_real_mode
            """,
        )
        with pytest.raises(ValueError, match="schema validation"):
            load_runtime_config(cfg, {})

    def test_quota_handling_unknown_field_rejected(self, tmp_path: Path) -> None:
        """An extra unknown field in quota_handling triggers schema validation."""
        cfg = self._write(
            tmp_path / "cfg.yaml",
            """\
            repos:
              org/repo:
                default_branch: main
            quota_handling:
              not_a_real_field: true
            """,
        )
        with pytest.raises(ValueError, match="schema validation"):
            load_runtime_config(cfg, {})

    def test_quota_handling_recovery_probe_request_size_below_minimum_rejected(self, tmp_path: Path) -> None:
        """request_size_tokens below 1 triggers schema validation."""
        cfg = self._write(
            tmp_path / "cfg.yaml",
            """\
            repos:
              org/repo:
                default_branch: main
            quota_handling:
              recovery_probe:
                request_size_tokens: 0
            """,
        )
        with pytest.raises(ValueError, match="schema validation"):
            load_runtime_config(cfg, {})

    def test_quota_handling_recovery_probe_timeout_below_minimum_rejected(self, tmp_path: Path) -> None:
        """timeout_seconds below 1 triggers schema validation."""
        cfg = self._write(
            tmp_path / "cfg.yaml",
            """\
            repos:
              org/repo:
                default_branch: main
            quota_handling:
              recovery_probe:
                timeout_seconds: 0.5
            """,
        )
        with pytest.raises(ValueError, match="schema validation"):
            load_runtime_config(cfg, {})

    def test_quota_handling_backoff_jitter_above_maximum_rejected(self, tmp_path: Path) -> None:
        """backoff.jitter above 1.0 triggers schema validation."""
        cfg = self._write(
            tmp_path / "cfg.yaml",
            """\
            repos:
              org/repo:
                default_branch: main
            quota_handling:
              recovery_probe:
                backoff:
                  jitter: 1.5
            """,
        )
        with pytest.raises(ValueError, match="schema validation"):
            load_runtime_config(cfg, {})

    def test_quota_handling_backoff_multiplier_below_minimum_rejected(self, tmp_path: Path) -> None:
        """backoff.multiplier below 1.0 triggers schema validation."""
        cfg = self._write(
            tmp_path / "cfg.yaml",
            """\
            repos:
              org/repo:
                default_branch: main
            quota_handling:
              recovery_probe:
                backoff:
                  multiplier: 0.5
            """,
        )
        with pytest.raises(ValueError, match="schema validation"):
            load_runtime_config(cfg, {})

    # ------------------------------------------------------------------
    # AC-193-20: parametrized valid enum acceptance tests
    # ------------------------------------------------------------------

    @pytest.mark.parametrize("value", ["wait", "fail", "drain"])
    def test_quota_handling_on_exhaustion_valid_values_accepted(self, tmp_path: Path, value: str) -> None:
        """Each valid on_exhaustion value is accepted and stored verbatim."""
        cfg = self._write(
            tmp_path / f"cfg_{value}.yaml",
            f"""\
            repos:
              org/repo:
                default_branch: main
            quota_handling:
              on_exhaustion: {value}
            """,
        )
        rt = load_runtime_config(cfg, {})
        assert rt.quota_handling.on_exhaustion == value, (
            f"Expected on_exhaustion={value!r}, got {rt.quota_handling.on_exhaustion!r}"
        )

    @pytest.mark.parametrize(
        "value",
        ["continue_current_wu", "restart_wu", "drain_and_resume"],
    )
    def test_quota_handling_resume_strategy_valid_values_accepted(self, tmp_path: Path, value: str) -> None:
        """Each valid resume_strategy value is accepted and stored verbatim."""
        cfg = self._write(
            tmp_path / f"cfg_{value}.yaml",
            f"""\
            repos:
              org/repo:
                default_branch: main
            quota_handling:
              resume_strategy: {value}
            """,
        )
        rt = load_runtime_config(cfg, {})
        assert rt.quota_handling.resume_strategy == value, (
            f"Expected resume_strategy={value!r}, got {rt.quota_handling.resume_strategy!r}"
        )

    @pytest.mark.parametrize("value", ["drain", "fail", "keep_waiting"])
    def test_quota_handling_on_exhaustion_timeout_valid_values_accepted(self, tmp_path: Path, value: str) -> None:
        """Each valid on_exhaustion_timeout value is accepted and stored verbatim."""
        cfg = self._write(
            tmp_path / f"cfg_{value}.yaml",
            f"""\
            repos:
              org/repo:
                default_branch: main
            quota_handling:
              on_exhaustion_timeout: {value}
            """,
        )
        rt = load_runtime_config(cfg, {})
        assert rt.quota_handling.on_exhaustion_timeout == value, (
            f"Expected on_exhaustion_timeout={value!r}, got {rt.quota_handling.on_exhaustion_timeout!r}"
        )

    @pytest.mark.parametrize("value", [30, 60, 300, 3600])
    def test_quota_handling_poll_interval_boundary_values_accepted(self, tmp_path: Path, value: int) -> None:
        """poll_interval_seconds accepts the minimum (30), maximum (3600), and mid-range values."""
        cfg = self._write(
            tmp_path / f"cfg_{value}.yaml",
            f"""\
            repos:
              org/repo:
                default_branch: main
            quota_handling:
              poll_interval_seconds: {value}
            """,
        )
        rt = load_runtime_config(cfg, {})
        assert rt.quota_handling.poll_interval_seconds == value, (
            f"Expected poll_interval_seconds={value}, got {rt.quota_handling.poll_interval_seconds}"
        )

    # ------------------------------------------------------------------
    # AC-193-20: error messages name the valid options (actionable text)
    # ------------------------------------------------------------------

    @pytest.mark.parametrize(
        "bad_value,valid_option",
        [
            ("never", "wait"),
            ("pause", "fail"),
            ("stop", "drain"),
        ],
    )
    def test_quota_handling_on_exhaustion_error_names_valid_options(
        self, tmp_path: Path, bad_value: str, valid_option: str
    ) -> None:
        """Rejection message for bad on_exhaustion names at least one valid option."""
        cfg = self._write(
            tmp_path / f"cfg_{bad_value}.yaml",
            f"""\
            repos:
              org/repo:
                default_branch: main
            quota_handling:
              on_exhaustion: {bad_value}
            """,
        )
        with pytest.raises(ValueError, match=valid_option):
            load_runtime_config(cfg, {})

    @pytest.mark.parametrize(
        "bad_value,valid_option",
        [
            ("skip", "continue_current_wu"),
            ("noop", "restart_wu"),
            ("finish", "drain_and_resume"),
        ],
    )
    def test_quota_handling_resume_strategy_error_names_valid_options(
        self, tmp_path: Path, bad_value: str, valid_option: str
    ) -> None:
        """Rejection message for bad resume_strategy names at least one valid option."""
        cfg = self._write(
            tmp_path / f"cfg_{bad_value}.yaml",
            f"""\
            repos:
              org/repo:
                default_branch: main
            quota_handling:
              resume_strategy: {bad_value}
            """,
        )
        with pytest.raises(ValueError, match=valid_option):
            load_runtime_config(cfg, {})

    @pytest.mark.parametrize("bad_value", [29, 0, 3601, 10000])
    def test_quota_handling_poll_interval_error_names_valid_range(self, tmp_path: Path, bad_value: int) -> None:
        """Rejection message for out-of-range poll_interval_seconds mentions the boundary (30 or 3600)."""
        cfg = self._write(
            tmp_path / f"cfg_{bad_value}.yaml",
            f"""\
            repos:
              org/repo:
                default_branch: main
            quota_handling:
              poll_interval_seconds: {bad_value}
            """,
        )
        with pytest.raises(ValueError, match=r"30|3600"):
            load_runtime_config(cfg, {})

    @pytest.mark.parametrize(
        "bad_value",
        ["never", "pause", "stop", "quit", "ignore"],
    )
    def test_quota_handling_on_exhaustion_error_names_field_path(self, tmp_path: Path, bad_value: str) -> None:
        """Rejection message for bad on_exhaustion mentions the field name 'on_exhaustion'."""
        cfg = self._write(
            tmp_path / f"cfg_{bad_value}.yaml",
            f"""\
            repos:
              org/repo:
                default_branch: main
            quota_handling:
              on_exhaustion: {bad_value}
            """,
        )
        with pytest.raises(ValueError, match="on_exhaustion"):
            load_runtime_config(cfg, {})

    @pytest.mark.parametrize(
        "bad_value",
        ["skip", "noop", "finish", "retry", "abort"],
    )
    def test_quota_handling_resume_strategy_error_names_field_path(self, tmp_path: Path, bad_value: str) -> None:
        """Rejection message for bad resume_strategy mentions the field name 'resume_strategy'."""
        cfg = self._write(
            tmp_path / f"cfg_{bad_value}.yaml",
            f"""\
            repos:
              org/repo:
                default_branch: main
            quota_handling:
              resume_strategy: {bad_value}
            """,
        )
        with pytest.raises(ValueError, match="resume_strategy"):
            load_runtime_config(cfg, {})

    @pytest.mark.parametrize("bad_value", [29, 3601])
    def test_quota_handling_poll_interval_error_names_field_path(self, tmp_path: Path, bad_value: int) -> None:
        """Rejection message for out-of-range poll_interval_seconds mentions the field name."""
        cfg = self._write(
            tmp_path / f"cfg_{bad_value}.yaml",
            f"""\
            repos:
              org/repo:
                default_branch: main
            quota_handling:
              poll_interval_seconds: {bad_value}
            """,
        )
        with pytest.raises(ValueError, match="poll_interval_seconds"):
            load_runtime_config(cfg, {})

    # ------------------------------------------------------------------
    # Dataclass default-factory tests (pure unit -- no YAML load)
    # ------------------------------------------------------------------

    def test_quota_handling_config_dataclass_defaults(self) -> None:
        """QuotaHandlingConfig() with no args yields expected field defaults."""
        qh = QuotaHandlingConfig()
        assert qh.enabled is True
        assert qh.on_exhaustion == "wait"
        assert qh.poll_interval_seconds == 60
        assert qh.max_wait_seconds == 18000
        assert qh.on_exhaustion_timeout == "drain"
        assert qh.resume_strategy == "continue_current_wu"
        assert qh.audit_comment_on_wait is True
        assert qh.audit_comment_on_resume is True
        assert qh.log_structured_events is True
        assert qh.notify_on_pause is None
        assert qh.notify_on_resume is None

    def test_quota_backoff_config_dataclass_defaults(self) -> None:
        """QuotaBackoffConfig() with no args yields spec defaults."""
        b = QuotaBackoffConfig()
        assert b.initial_seconds == 30.0
        assert b.max_seconds == 600.0
        assert b.multiplier == 2.0
        assert b.jitter == 0.2

    def test_quota_recovery_probe_config_dataclass_defaults(self) -> None:
        """QuotaRecoveryProbeConfig() with no args yields spec defaults."""
        p = QuotaRecoveryProbeConfig()
        assert p.enabled is True
        assert p.request_size_tokens == 1
        assert p.timeout_seconds == 10.0
        assert isinstance(p.backoff, QuotaBackoffConfig)

    def test_quota_notify_config_stores_fields(self) -> None:
        """QuotaNotifyConfig accepts webhook_url and slack_webhook_url."""
        n = QuotaNotifyConfig(webhook_url="https://w.example.com", slack_webhook_url="https://s.example.com")
        assert n.webhook_url == "https://w.example.com"
        assert n.slack_webhook_url == "https://s.example.com"

    def test_quota_notify_config_default_nones(self) -> None:
        """QuotaNotifyConfig() with no args has both URLs as None."""
        n = QuotaNotifyConfig()
        assert n.webhook_url is None
        assert n.slack_webhook_url is None


# ---------------------------------------------------------------------------
# AC-193-4: quota_handling.enabled false preserves legacy behavior
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestQuotaHandlingEnabledFalse:
    """AC-193-4: enabled: false preserves legacy raise+exit behavior (backwards compat).

    When quota_handling.enabled is false, the orchestrator must NOT invoke
    quota detection, wait, or audit comment logic. The full QuotaHandlingConfig
    is still parsed and stored; the ``enabled`` field gates the caller's behavior.

    These tests verify:
    1. ``enabled: false`` in YAML is stored correctly.
    2. All other config fields retain their spec defaults when only enabled is
       set to false (no side-effects from disabling quota handling).
    3. ``enabled: false`` can coexist with explicit values for other fields,
       ensuring backwards-compatible configs remain valid.
    """

    def _write(self, path: Path, content: str) -> Path:
        path.write_text(textwrap.dedent(content), encoding="utf-8")
        return path

    def test_enabled_false_other_fields_at_defaults(self, tmp_path: Path) -> None:
        """When enabled: false and no other overrides, all other fields use spec defaults.

        Legacy operators who set enabled: false to opt out must not accidentally
        get different values for on_exhaustion, poll_interval_seconds, etc.
        """
        cfg = self._write(
            tmp_path / "cfg.yaml",
            """\
            repos:
              org/repo:
                default_branch: main
            quota_handling:
              enabled: false
            """,
        )
        rt = load_runtime_config(cfg, {})
        qh = rt.quota_handling
        assert qh.enabled is False
        assert qh.on_exhaustion == "wait"
        assert qh.poll_interval_seconds == 60
        assert qh.max_wait_seconds == 18000
        assert qh.on_exhaustion_timeout == "drain"
        assert qh.resume_strategy == "continue_current_wu"
        assert qh.audit_comment_on_wait is True
        assert qh.audit_comment_on_resume is True
        assert qh.log_structured_events is True

    def test_enabled_false_detection_modes_at_defaults(self, tmp_path: Path) -> None:
        """When enabled: false only, detect_modes still holds all four default modes.

        The detect_modes field must not be cleared when enabled is false; the
        config is a complete, valid representation that the caller gates with
        the enabled flag.
        """
        cfg = self._write(
            tmp_path / "cfg.yaml",
            """\
            repos:
              org/repo:
                default_branch: main
            quota_handling:
              enabled: false
            """,
        )
        rt = load_runtime_config(cfg, {})
        assert rt.quota_handling.detect_modes == [
            "subscription_rate_limit",
            "sdk_credit_exhausted",
            "api_billing_error",
            "bedrock_throttle",
        ]

    def test_enabled_false_recovery_probe_at_defaults(self, tmp_path: Path) -> None:
        """When enabled: false only, recovery_probe sub-config still has spec defaults."""
        cfg = self._write(
            tmp_path / "cfg.yaml",
            """\
            repos:
              org/repo:
                default_branch: main
            quota_handling:
              enabled: false
            """,
        )
        rt = load_runtime_config(cfg, {})
        probe = rt.quota_handling.recovery_probe
        assert probe.enabled is True
        assert probe.request_size_tokens == 1
        assert probe.timeout_seconds == 10.0
        assert probe.backoff.initial_seconds == 30.0
        assert probe.backoff.max_seconds == 600.0
        assert probe.backoff.multiplier == 2.0
        assert probe.backoff.jitter == 0.2

    def test_enabled_false_notify_fields_at_none(self, tmp_path: Path) -> None:
        """When enabled: false only, notify_on_pause and notify_on_resume remain None."""
        cfg = self._write(
            tmp_path / "cfg.yaml",
            """\
            repos:
              org/repo:
                default_branch: main
            quota_handling:
              enabled: false
            """,
        )
        rt = load_runtime_config(cfg, {})
        assert rt.quota_handling.notify_on_pause is None
        assert rt.quota_handling.notify_on_resume is None

    def test_enabled_false_with_on_exhaustion_fail(self, tmp_path: Path) -> None:
        """enabled: false and on_exhaustion: fail coexist without error.

        A config can set enabled: false alongside other fields to document
        what the settings would be if re-enabled; the parser must accept it.
        """
        cfg = self._write(
            tmp_path / "cfg.yaml",
            """\
            repos:
              org/repo:
                default_branch: main
            quota_handling:
              enabled: false
              on_exhaustion: fail
            """,
        )
        rt = load_runtime_config(cfg, {})
        assert rt.quota_handling.enabled is False
        assert rt.quota_handling.on_exhaustion == "fail"

    def test_enabled_false_with_max_wait_override(self, tmp_path: Path) -> None:
        """enabled: false alongside max_wait_seconds is accepted and stored correctly."""
        cfg = self._write(
            tmp_path / "cfg.yaml",
            """\
            repos:
              org/repo:
                default_branch: main
            quota_handling:
              enabled: false
              max_wait_seconds: 7200
            """,
        )
        rt = load_runtime_config(cfg, {})
        assert rt.quota_handling.enabled is False
        assert rt.quota_handling.max_wait_seconds == 7200

    def test_enabled_false_is_boolean_not_falsy_string(self, tmp_path: Path) -> None:
        """enabled: false is stored as the Python False bool, not the string 'false'."""
        cfg = self._write(
            tmp_path / "cfg.yaml",
            """\
            repos:
              org/repo:
                default_branch: main
            quota_handling:
              enabled: false
            """,
        )
        rt = load_runtime_config(cfg, {})
        assert rt.quota_handling.enabled is False
        assert isinstance(rt.quota_handling.enabled, bool)

    def test_enabled_true_is_default_without_section(self, tmp_path: Path) -> None:
        """When quota_handling section is absent, enabled is True (backwards compat default).

        Pre-existing devbench.yaml files that omit quota_handling get safe defaults
        including enabled: true -- they do not suddenly get quota handling disabled.
        """
        cfg = self._write(
            tmp_path / "cfg.yaml",
            """\
            repos:
              org/repo:
                default_branch: main
            """,
        )
        rt = load_runtime_config(cfg, {})
        assert rt.quota_handling.enabled is True
