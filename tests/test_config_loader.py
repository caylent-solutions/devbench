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
    LimitConfig,
    RepoConfig,
    RuntimeConfig,
    TimeoutConfig,
    get_configured_default_branch,
    get_repo_local_path,
    load_runtime_config,
    resolve_config_path,
)

# ---------------------------------------------------------------------------
# resolve_config_path — AC-2
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
# load_runtime_config — AC-3, AC-4, AC-5
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
            (
                "repos:\n"
                "  org/repo-a:\n"
                "    default_branch: main\n"
                "  org/repo-b:\n"
                "    default_branch: develop\n"
            ),
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
        assert isinstance(result, RuntimeConfig), (
            f"Expected RuntimeConfig instance, got {type(result).__name__}"
        )

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
# get_configured_default_branch — AC-6 (pure function)
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
        assert rc.checkout_directory is None, (
            f"Expected checkout_directory=None, got {rc.checkout_directory!r}"
        )


# ---------------------------------------------------------------------------
# checkout_directory parsing — AC-1, AC-3, AC-4
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
# get_repo_local_path — AC-2, AC-5
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
        config = RuntimeConfig(
            repos={"org/my-repo": RepoConfig(checkout_directory="custom-checkout")}
        )
        result = get_repo_local_path("org/my-repo", config, tmp_path)
        assert result == tmp_path / "custom-checkout", (
            f"Expected {tmp_path / 'custom-checkout'}, got {result}"
        )

    def test_falls_back_to_repo_short_name(self, tmp_path: Path) -> None:
        """
        Given: a repo config with no checkout_directory
        When: get_repo_local_path is called
        Then: the path is workspace_root / short-name
        """
        config = RuntimeConfig(repos={"org/my-repo": RepoConfig()})
        result = get_repo_local_path("org/my-repo", config, tmp_path)
        assert result == tmp_path / "my-repo", (
            f"Expected {tmp_path / 'my-repo'}, got {result}"
        )

    def test_falls_back_when_repo_not_in_config(self, tmp_path: Path) -> None:
        """
        Given: a RuntimeConfig with no repos
        When: get_repo_local_path is called for a repo not in config
        Then: the path falls back to workspace_root / short-name
        """
        config = RuntimeConfig(repos={})
        result = get_repo_local_path("org/unknown-repo", config, tmp_path)
        assert result == tmp_path / "unknown-repo", (
            f"Expected {tmp_path / 'unknown-repo'}, got {result}"
        )

    def test_checkout_directory_none_uses_short_name(self, tmp_path: Path) -> None:
        """
        Given: a repo config with explicit checkout_directory=None
        When: get_repo_local_path is called
        Then: the path falls back to workspace_root / short-name
        """
        config = RuntimeConfig(
            repos={"org/my-repo": RepoConfig(checkout_directory=None)}
        )
        result = get_repo_local_path("org/my-repo", config, tmp_path)
        assert result == tmp_path / "my-repo", (
            f"Expected {tmp_path / 'my-repo'}, got {result}"
        )


# ---------------------------------------------------------------------------
# JSON Schema validation — AC-1, AC-2, AC-3, AC-4, AC-5, AC-6
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
# TimeoutConfig / LimitConfig dataclasses — AC-9
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
# RuntimeConfig population from YAML — AC-9
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
        assert result.timeouts.llm is None, (
            f"Expected unspecified field llm=None, got {result.timeouts.llm!r}"
        )

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
        assert result.limits.alert_summary == 20, (
            f"Expected alert_summary=20, got {result.limits.alert_summary!r}"
        )
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
            max_retries: 5
            use_bedrock: true
            bedrock_region: us-west-2
            """,
        )
        result = load_runtime_config(cfg, {})
        assert result.merge_strategy == "merge", (
            f"Expected merge_strategy='merge', got {result.merge_strategy!r}"
        )
        assert result.max_retries == 5, f"Expected max_retries=5, got {result.max_retries!r}"
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
        assert result.max_retries is None, (
            f"Expected max_retries=None when absent from YAML, got {result.max_retries!r}"
        )
        assert result.use_bedrock is False, (
            f"Expected use_bedrock=False (explicit bool default), got {result.use_bedrock!r}"
        )
        assert result.bedrock_region is None, (
            f"Expected bedrock_region=None when absent from YAML, got {result.bedrock_region!r}"
        )
        assert result.allowed_orgs == [], (
            f"Expected allowed_orgs=[], got {result.allowed_orgs!r}"
        )
        assert result.judge_model is None, (
            f"Expected judge_model=None when absent from YAML, got {result.judge_model!r}"
        )
        assert result.executor_model is None, (
            f"Expected executor_model=None when absent from YAML, got {result.executor_model!r}"
        )

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

        assert env_read_calls == [], (
            f"config_loader.py must not read env vars — found: {env_read_calls}"
        )


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
        assert "permitted-org/repo" in result.repos, (
            f"Expected 'permitted-org/repo' in repos, got {set(result.repos)}"
        )

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
        assert "any-org/repo" in result.repos, (
            f"Expected 'any-org/repo' in repos, got {set(result.repos)}"
        )
