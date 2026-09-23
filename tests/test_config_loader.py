"""Tests for src/devbench/config_loader.py.

Covers: path resolution precedence, YAML loading, value parsing,
configured branch lookup, and PR base-branch wiring.
"""

from __future__ import annotations

import dataclasses
import json
import re
import textwrap
from pathlib import Path

import jsonschema
import pytest
import yaml

from devbench.config_loader import (
    DEFAULT_CONFIG_SUBPATH,
    BacklogConfig,
    GitOpsConfig,
    LimitConfig,
    RepoConfig,
    RuntimeConfig,
    SkillsConfig,
    TaskFactoryConfig,
    TimeoutConfig,
    extract_ticket_id,
    format_branch_name,
    format_commit_subject,
    format_single_branch_name,
    get_configured_default_branch,
    get_effective_branch_prefix,
    get_effective_commit_subject_template,
    get_effective_merge_strategy,
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
    """AC-2: config path precedence is explicit > DEVBENCH_CONFIG_PATH > default."""

    def test_explicit_path_wins_over_env_and_default(self, tmp_path: Path) -> None:
        """
        Given: an explicit path, an env-var path, and a workspace root
        When: resolve_config_path is called with the explicit path
        Then: the explicit path is returned
        """
        explicit = tmp_path / "custom.yaml"
        env = {"DEVBENCH_CONFIG_PATH": str(tmp_path / "env.yaml")}
        result = resolve_config_path(str(explicit), env, tmp_path / "workspace")
        assert result == explicit, f"Expected explicit path {explicit}, got {result}"

    def test_judge_config_path_env_wins_over_default(self, tmp_path: Path) -> None:
        """
        Given: no explicit path but DEVBENCH_CONFIG_PATH set
        When: resolve_config_path is called
        Then: the env-var path is returned
        """
        env_yaml = tmp_path / "env_config.yaml"
        env = {"DEVBENCH_CONFIG_PATH": str(env_yaml)}
        result = resolve_config_path(None, env, tmp_path / "workspace")
        assert result == env_yaml, f"Expected env path {env_yaml}, got {result}"

    def test_default_path_when_no_override(self, tmp_path: Path) -> None:
        """
        Given: no explicit path and no DEVBENCH_CONFIG_PATH
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
        Given: explicit path is None and DEVBENCH_CONFIG_PATH is empty string
        When: resolve_config_path is called
        Then: empty DEVBENCH_CONFIG_PATH is treated as unset and the default path is used
        """
        workspace = tmp_path / "ws"
        result = resolve_config_path(None, {"DEVBENCH_CONFIG_PATH": ""}, workspace)
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


class TestGetEffectiveMergeStrategy:
    """#237: per-repo merge_strategy overrides top-level; None when neither set."""

    def test_per_repo_override_wins(self) -> None:
        config = RuntimeConfig(
            repos={"org/repo": RepoConfig(merge_strategy="rebase")},
            merge_strategy="merge",
        )
        assert get_effective_merge_strategy("org/repo", config) == "rebase"

    def test_top_level_fallback_when_no_per_repo(self) -> None:
        config = RuntimeConfig(
            repos={"org/repo": RepoConfig(merge_strategy=None)},
            merge_strategy="merge",
        )
        assert get_effective_merge_strategy("org/repo", config) == "merge"

    def test_top_level_applies_to_unknown_repo(self) -> None:
        config = RuntimeConfig(repos={}, merge_strategy="merge")
        assert get_effective_merge_strategy("org/unknown", config) == "merge"

    def test_none_when_neither_set(self) -> None:
        config = RuntimeConfig(repos={"org/repo": RepoConfig(merge_strategy=None)}, merge_strategy=None)
        assert get_effective_merge_strategy("org/repo", config) is None


class TestGetEffectiveBranchPrefix:
    """Per-repo branch_prefix overrides the top-level git_ops.branch_prefix; None when neither set.

    Mirrors TestGetEffectiveMergeStrategy -- same three-tier precedence
    (per-repo -> top-level -> None), added to prevent task-branch
    collisions when multiple devbench workspaces share one downstream repo.
    """

    def test_per_repo_override_wins(self) -> None:
        config = RuntimeConfig(
            repos={"org/repo": RepoConfig(branch_prefix="wg_004")},
            git_ops=GitOpsConfig(branch_prefix="wg_global"),
        )
        assert get_effective_branch_prefix("org/repo", config) == "wg_004"

    def test_top_level_fallback_when_no_per_repo(self) -> None:
        config = RuntimeConfig(
            repos={"org/repo": RepoConfig(branch_prefix=None)},
            git_ops=GitOpsConfig(branch_prefix="wg_global"),
        )
        assert get_effective_branch_prefix("org/repo", config) == "wg_global"

    def test_top_level_applies_to_unknown_repo(self) -> None:
        config = RuntimeConfig(repos={}, git_ops=GitOpsConfig(branch_prefix="wg_global"))
        assert get_effective_branch_prefix("org/unknown", config) == "wg_global"

    def test_none_when_neither_set(self) -> None:
        config = RuntimeConfig(repos={"org/repo": RepoConfig(branch_prefix=None)}, git_ops=GitOpsConfig())
        assert get_effective_branch_prefix("org/repo", config) is None


class TestFormatBranchName:
    """format_branch_name: backlog/<id> unchanged when unset; namespaced when a prefix is given."""

    def test_no_prefix_matches_original_template(self) -> None:
        assert format_branch_name("E1-F1-S1-T1") == "backlog/e1-f1-s1-t1"

    def test_no_prefix_explicit_none_matches_original_template(self) -> None:
        assert format_branch_name("E1-F1-S1-T1", None) == "backlog/e1-f1-s1-t1"

    def test_prefix_inserted_between_backlog_and_unit_id(self) -> None:
        assert format_branch_name("E1-F1-S1-T1", "wg_004") == "backlog/wg_004/e1-f1-s1-t1"

    def test_empty_string_prefix_treated_as_unset(self) -> None:
        assert format_branch_name("E1-F1-S1-T1", "") == "backlog/e1-f1-s1-t1"


@pytest.mark.unit
class TestGetEffectiveCommitSubjectTemplate:
    """Per-repo commit_subject_template overrides the top-level one; None when neither set.

    Same three-tier precedence as TestGetEffectiveBranchPrefix. Exists because
    a target repo's commit-msg hook can enforce a subject shape the multi-dash
    work-unit ID cannot satisfy, and that is a per-repo fact.
    """

    def test_per_repo_override_wins(self) -> None:
        config = RuntimeConfig(
            repos={"org/repo": RepoConfig(commit_subject_template="{ticket}: {title}")},
            git_ops=GitOpsConfig(commit_subject_template="{unit_id}: {title}"),
        )
        assert get_effective_commit_subject_template("org/repo", config) == "{ticket}: {title}"

    def test_top_level_fallback_when_no_per_repo(self) -> None:
        config = RuntimeConfig(
            repos={"org/repo": RepoConfig(commit_subject_template=None)},
            git_ops=GitOpsConfig(commit_subject_template="{unit_id}: {title}"),
        )
        assert get_effective_commit_subject_template("org/repo", config) == "{unit_id}: {title}"

    def test_top_level_applies_to_unknown_repo(self) -> None:
        config = RuntimeConfig(repos={}, git_ops=GitOpsConfig(commit_subject_template="{ticket}: {title}"))
        assert get_effective_commit_subject_template("org/unknown", config) == "{ticket}: {title}"

    def test_none_when_neither_set(self) -> None:
        config = RuntimeConfig(repos={"org/repo": RepoConfig()}, git_ops=GitOpsConfig())
        assert get_effective_commit_subject_template("org/repo", config) is None


@pytest.mark.unit
class TestExtractTicketId:
    """extract_ticket_id: the tracker id a work unit's branch name leads with."""

    def test_reads_the_ticket_a_branch_leads_with(self) -> None:
        assert extract_ticket_id("sfb-229-vendoring-pipeline") == "SFB-229"

    def test_upper_cases_a_lowercase_branch_convention(self) -> None:
        assert extract_ticket_id("abc-7-do-a-thing") == "ABC-7"

    def test_strips_the_backticks_a_work_unit_field_wraps_it_in(self) -> None:
        assert extract_ticket_id("`sfb-229-vendoring-pipeline`") == "SFB-229"

    def test_none_for_a_branch_carrying_no_ticket(self) -> None:
        assert extract_ticket_id("backlog/e1-f1-s1-t1") is None

    def test_none_for_a_ticket_with_no_slug_after_it(self) -> None:
        # The trailing hyphen is required: without it a bare "sfb-229" could
        # equally be the first two segments of some other naming scheme.
        assert extract_ticket_id("sfb-229") is None

    def test_none_for_empty_and_none(self) -> None:
        assert extract_ticket_id("") is None
        assert extract_ticket_id(None) is None


@pytest.mark.unit
class TestFormatCommitSubject:
    """format_commit_subject: unchanged default, ticket substitution, and loud refusals."""

    def test_no_template_matches_original_subject(self) -> None:
        assert format_commit_subject("E1-F1-S1-T1", "Do the thing", "sfb-229-slug", None) == "E1-F1-S1-T1: Do the thing"

    def test_ticket_template_names_the_ticket_not_the_unit_id(self) -> None:
        subject = format_commit_subject(
            "E1-F2-S1-T1", "Write the vendoring ADR", "sfb-229-vendoring-pipeline", "{ticket}: {title}"
        )
        assert subject == "SFB-229: Write the vendoring ADR"

    def test_template_may_still_name_the_unit_id(self) -> None:
        subject = format_commit_subject("E1-F1-S1-T1", "Do the thing", "sfb-229-slug", "{ticket}: {title} ({unit_id})")
        assert subject == "SFB-229: Do the thing (E1-F1-S1-T1)"

    def test_refuses_a_ticket_template_when_the_branch_carries_none(self) -> None:
        with pytest.raises(ValueError, match="carries no tracker id"):
            format_commit_subject("E1-F1-S1-T1", "Do the thing", "backlog/e1-f1-s1-t1", "{ticket}: {title}")

    def test_refuses_a_ticket_template_when_the_branch_is_missing(self) -> None:
        with pytest.raises(ValueError, match="carries no tracker id"):
            format_commit_subject("E1-F1-S1-T1", "Do the thing", None, "{ticket}: {title}")

    def test_refuses_an_unknown_placeholder(self) -> None:
        with pytest.raises(ValueError, match="unknown placeholder"):
            format_commit_subject("E1-F1-S1-T1", "Do the thing", "sfb-229-slug", "{epic}: {title}")

    def test_a_ticketless_template_needs_no_ticket(self) -> None:
        assert format_commit_subject("E1-F1-S1-T1", "Do the thing", None, "chore: {title}") == "chore: Do the thing"


class TestFormatSingleBranchName:
    """format_single_branch_name: single_branch unchanged when unset; namespaced when a prefix is given.

    Single-branch (accumulator) mode has the same cross-workspace collision
    risk as per-unit branches -- two devbench workspaces could configure the
    same single_branch name against the same shared repo (issue #283 AC-5).
    """

    def test_no_prefix_matches_configured_single_branch(self) -> None:
        assert format_single_branch_name("feat/batch") == "feat/batch"

    def test_no_prefix_explicit_none_matches_configured_single_branch(self) -> None:
        assert format_single_branch_name("feat/batch", None) == "feat/batch"

    def test_prefix_prepended_to_single_branch(self) -> None:
        assert format_single_branch_name("feat/batch", "wg_004") == "wg_004/feat/batch"

    def test_empty_string_prefix_treated_as_unset(self) -> None:
        assert format_single_branch_name("feat/batch", "") == "feat/batch"


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

    def test_schema_accepts_orchestrator_inactivity_timeout(self, tmp_path: Path) -> None:
        """
        Given: a YAML timeouts block with an orchestrator_inactivity integer
        When: load_runtime_config is called
        Then: no ValueError is raised and the value is loaded (spec FR-17, db-262)
        """
        cfg = self._write(
            tmp_path / "cfg.yaml",
            """\
            repos:
              org/repo:
                default_branch: main
            timeouts:
              orchestrator_inactivity: 900
            """,
        )
        result = load_runtime_config(cfg, {})
        assert result.timeouts.orchestrator_inactivity == 900, (
            f"Expected orchestrator_inactivity=900, got {result.timeouts.orchestrator_inactivity!r}"
        )

    def test_schema_rejects_zero_orchestrator_inactivity_timeout(self, tmp_path: Path) -> None:
        """
        Given: an orchestrator_inactivity value of zero (below minimum: 1)
        When: load_runtime_config is called
        Then: ValueError is raised (spec FR-17, db-262, mirrors AC-4 for gh_api)
        """
        cfg = self._write(
            tmp_path / "cfg.yaml",
            """\
            repos:
              org/repo:
                default_branch: main
            timeouts:
              orchestrator_inactivity: 0
            """,
        )
        with pytest.raises(ValueError):
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
        assert tc.orchestrator_poll_interval is None, (
            f"Expected orchestrator_poll_interval=None, got {tc.orchestrator_poll_interval!r}"
        )
        assert tc.github_check is None, f"Expected github_check=None, got {tc.github_check!r}"
        assert tc.orchestrator_inactivity is None, (
            f"Expected orchestrator_inactivity=None, got {tc.orchestrator_inactivity!r}"
        )


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

    def test_runtime_config_populates_orchestrator_inactivity_from_yaml(self, tmp_path: Path) -> None:
        """
        Given: YAML with timeouts.orchestrator_inactivity set
        When: load_runtime_config is called
        Then: RuntimeConfig.timeouts.orchestrator_inactivity reflects the YAML value (spec FR-17, db-262)
        """
        cfg = self._write(
            tmp_path / "cfg.yaml",
            """\
            repos:
              org/repo:
                default_branch: main
            timeouts:
              orchestrator_inactivity: 1200
            """,
        )
        result = load_runtime_config(cfg, {})
        assert result.timeouts.orchestrator_inactivity == 1200, (
            f"Expected orchestrator_inactivity=1200, got {result.timeouts.orchestrator_inactivity!r}"
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
        assert result.merge_strategy == "squash", (
            f"Expected merge_strategy='squash' (default) when absent from YAML, got {result.merge_strategy!r}"
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
# orchestrate: transport-restart bound and backoff envelope
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestOrchestrateTransportRestartConfig:
    """The ``orchestrate.*`` transport-restart knobs load from YAML.

    These are optional: a workspace that never sets them must keep ``None`` so
    ``config.py``'s env > YAML > default chain still reaches the built-in
    default rather than being pinned by a stray zero.
    """

    @staticmethod
    def _write(path: Path, content: str) -> Path:
        path.write_text(textwrap.dedent(content), encoding="utf-8")
        return path

    def test_absent_orchestrate_block_leaves_every_field_none(self, tmp_path: Path) -> None:
        cfg = self._write(
            tmp_path / "cfg.yaml",
            """\
            repos:
              org/repo:
                default_branch: main
            """,
        )

        result = load_runtime_config(cfg, {})

        assert result.orchestrate.max_transport_restarts is None
        assert result.orchestrate.transport_restart_backoff_base_seconds is None
        assert result.orchestrate.transport_restart_backoff_max_seconds is None

    def test_values_are_read_from_the_orchestrate_block(self, tmp_path: Path) -> None:
        cfg = self._write(
            tmp_path / "cfg.yaml",
            """\
            repos:
              org/repo:
                default_branch: main
            orchestrate:
              max_transport_restarts: 4
              transport_restart_backoff_base_seconds: 0.5
              transport_restart_backoff_max_seconds: 30
            """,
        )

        result = load_runtime_config(cfg, {})

        assert result.orchestrate.max_transport_restarts == 4
        assert result.orchestrate.transport_restart_backoff_base_seconds == 0.5
        # An int in YAML must still surface as a float for the arithmetic.
        assert result.orchestrate.transport_restart_backoff_max_seconds == 30.0
        assert isinstance(result.orchestrate.transport_restart_backoff_max_seconds, float)

    def test_partial_block_leaves_the_unset_siblings_none(self, tmp_path: Path) -> None:
        """Setting one knob must not silently pin the other two."""
        cfg = self._write(
            tmp_path / "cfg.yaml",
            """\
            repos:
              org/repo:
                default_branch: main
            orchestrate:
              max_transport_restarts: 7
            """,
        )

        result = load_runtime_config(cfg, {})

        assert result.orchestrate.max_transport_restarts == 7
        assert result.orchestrate.transport_restart_backoff_base_seconds is None
        assert result.orchestrate.transport_restart_backoff_max_seconds is None

    @pytest.mark.parametrize(
        ("key", "bad_value"),
        [
            ("max_transport_restarts", 0),
            ("transport_restart_backoff_base_seconds", 0),
            ("transport_restart_backoff_max_seconds", -1),
        ],
    )
    def test_schema_rejects_non_positive_values(self, tmp_path: Path, key: str, bad_value: object) -> None:
        """Fail fast at load time: a zero or negative delay is the busy-loop
        defect the backoff exists to prevent."""
        cfg = self._write(
            tmp_path / "cfg.yaml",
            f"""\
            repos:
              org/repo:
                default_branch: main
            orchestrate:
              {key}: {bad_value}
            """,
        )

        with pytest.raises((ValueError, jsonschema.ValidationError)):
            load_runtime_config(cfg, {})

    def test_schema_rejects_unknown_orchestrate_key(self, tmp_path: Path) -> None:
        """``additionalProperties: false`` -- a typo must be loud, not ignored."""
        cfg = self._write(
            tmp_path / "cfg.yaml",
            """\
            repos:
              org/repo:
                default_branch: main
            orchestrate:
              max_transport_restart: 4
            """,
        )

        with pytest.raises((ValueError, jsonschema.ValidationError)):
            load_runtime_config(cfg, {})


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

    def test_report_models_empty_when_absent(self, tmp_path: Path) -> None:
        """Issue #223: with no ``report.models`` block, the parsed mapping is
        empty.  ``devbench.config`` then folds in the per-package
        ``DEFAULT_MODEL_RATES`` so the runtime view still prices every
        canonical model id.
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
        assert result.report.models == {}
        # default_model falls back to the package constant when YAML
        # leaves it unset.
        from devbench.constants import DEFAULT_FALLBACK_MODEL_RATES

        assert result.report.default_model == DEFAULT_FALLBACK_MODEL_RATES
        # Cache multipliers default to None in the parsed YAML layer;
        # config.py applies the constant defaults via _resolve_float
        # (env > YAML > const).
        assert result.report.cache_read_multiplier is None
        assert result.report.cache_write_5min_multiplier is None
        assert result.report.cache_write_1hr_multiplier is None
        assert result.report.data_residency_multiplier is None

    def test_report_models_parsed_from_yaml(self, tmp_path: Path) -> None:
        cfg = self._write(
            tmp_path / "cfg.yaml",
            """\
            repos:
              caylent-solutions/devbench:
                default_branch: main
            report:
              models:
                claude-sonnet-4-6:
                  input: 3.0
                  output: 15.0
                claude-opus-4-7:
                  input: 5.0
                  output: 25.0
                  correction_factor: 1.05
              default_model:
                input: 5.0
                output: 25.0
              cache_read_multiplier: 0.05
              cache_write_5min_multiplier: 1.5
              cache_write_1hr_multiplier: 2.5
              data_residency_multiplier: 1.2
            """,
        )
        result = load_runtime_config(cfg, {})
        assert set(result.report.models.keys()) == {"claude-sonnet-4-6", "claude-opus-4-7"}
        sonnet = result.report.models["claude-sonnet-4-6"]
        assert (sonnet.input, sonnet.output, sonnet.correction_factor) == (3.0, 15.0, 1.0)
        opus = result.report.models["claude-opus-4-7"]
        assert (opus.input, opus.output, opus.correction_factor) == (5.0, 25.0, 1.05)
        assert result.report.default_model.input == 5.0
        assert result.report.default_model.output == 25.0
        assert result.report.cache_read_multiplier == 0.05
        assert result.report.cache_write_5min_multiplier == 1.5
        assert result.report.cache_write_1hr_multiplier == 2.5
        assert result.report.data_residency_multiplier == 1.2

    def test_legacy_token_cost_keys_rejected_with_actionable_message(self, tmp_path: Path) -> None:
        """Issue #223: complete-replacement per CLAUDE.md.  Workspaces that
        still set the retired scalar fields get a fail-fast error that
        names the field AND points at the new ``report.models`` block.
        """
        cfg = self._write(
            tmp_path / "cfg.yaml",
            """\
            repos:
              caylent-solutions/devbench:
                default_branch: main
            report:
              token_cost_per_million_input: 5.0
              token_cost_per_million_output: 25.0
              token_cost_discount: 0.0
            """,
        )
        with pytest.raises(ValueError) as exc:
            load_runtime_config(cfg, {})
        msg = str(exc.value)
        assert "token_cost_per_million_input" in msg
        assert "token_cost_per_million_output" in msg
        assert "token_cost_discount" in msg
        assert "report.models" in msg
        assert "docs/model-pricing.md" in msg

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


@pytest.mark.unit
class TestBranchPrefixConfig:
    """git_ops.branch_prefix and repos.<org/repo>.branch_prefix: parsing and validation.

    Namespaces task branches (backlog/<prefix>/<id>) so multiple devbench
    workspaces sharing one downstream repo cannot collide on branch names.
    """

    def _write(self, path: Path, content: str) -> Path:
        path.write_text(textwrap.dedent(content), encoding="utf-8")
        return path

    def test_branch_prefix_defaults_to_none(self, tmp_path: Path) -> None:
        cfg = self._write(
            tmp_path / "cfg.yaml",
            """\
            repos:
              caylent-solutions/devbench:
                default_branch: main
            """,
        )
        result = load_runtime_config(cfg, {})
        assert result.git_ops.branch_prefix is None
        assert result.repos["caylent-solutions/devbench"].branch_prefix is None

    def test_top_level_branch_prefix_from_yaml(self, tmp_path: Path) -> None:
        cfg = self._write(
            tmp_path / "cfg.yaml",
            """\
            repos:
              caylent-solutions/devbench:
                default_branch: main
            git_ops:
              branch_prefix: wg_004
            """,
        )
        result = load_runtime_config(cfg, {})
        assert result.git_ops.branch_prefix == "wg_004"

    def test_per_repo_branch_prefix_from_yaml(self, tmp_path: Path) -> None:
        cfg = self._write(
            tmp_path / "cfg.yaml",
            """\
            repos:
              caylent-solutions/devbench:
                default_branch: main
                branch_prefix: wg_004
            """,
        )
        result = load_runtime_config(cfg, {})
        assert result.repos["caylent-solutions/devbench"].branch_prefix == "wg_004"

    def test_rejects_empty_branch_prefix(self, tmp_path: Path) -> None:
        cfg = self._write(
            tmp_path / "cfg.yaml",
            """\
            repos:
              caylent-solutions/devbench:
                default_branch: main
            git_ops:
              branch_prefix: ""
            """,
        )
        result = load_runtime_config(cfg, {})
        assert result.git_ops.branch_prefix is None

    def test_rejects_leading_slash_branch_prefix(self, tmp_path: Path) -> None:
        cfg = self._write(
            tmp_path / "cfg.yaml",
            """\
            repos:
              caylent-solutions/devbench:
                default_branch: main
            git_ops:
              branch_prefix: /wg_004
            """,
        )
        with pytest.raises(ValueError, match="leading or trailing"):
            load_runtime_config(cfg, {})

    def test_rejects_trailing_slash_repo_branch_prefix(self, tmp_path: Path) -> None:
        cfg = self._write(
            tmp_path / "cfg.yaml",
            """\
            repos:
              caylent-solutions/devbench:
                default_branch: main
                branch_prefix: wg_004/
            """,
        )
        with pytest.raises(ValueError, match="leading or trailing"):
            load_runtime_config(cfg, {})

    def test_rejects_parent_traversal_branch_prefix(self, tmp_path: Path) -> None:
        cfg = self._write(
            tmp_path / "cfg.yaml",
            """\
            repos:
              caylent-solutions/devbench:
                default_branch: main
            git_ops:
              branch_prefix: "../escape"
            """,
        )
        with pytest.raises(ValueError, match="parent traversal"):
            load_runtime_config(cfg, {})


class TestManifestAmendmentConfig:
    """YAML loader correctly parses the opt-in manifest_amendment section."""

    @staticmethod
    def _write(path: Path, content: str) -> Path:
        import textwrap

        path.write_text(textwrap.dedent(content))
        return path

    def test_defaults_when_section_absent(self, tmp_path: Path) -> None:
        """When the manifest_amendment section is omitted, the feature defaults on."""
        cfg = self._write(
            tmp_path / "cfg.yaml",
            """\
            repos:
              caylent-solutions/devbench:
                default_branch: main
            """,
        )
        result = load_runtime_config(cfg, {})
        assert result.manifest_amendment.enabled is True
        # 2 admits one addition plus one row removal in a single execution, the
        # combination AC-FINAL-015 can require when a declared row goes stale.
        assert result.manifest_amendment.max_requests_per_execution == 2
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

    def test_amendment_allowed_reasons_default_includes_doc_sync(self, tmp_path: Path) -> None:
        """FR-11 Leg A1: the default allowed_reasons set carries both sanctioned reasons."""
        cfg = self._write(
            tmp_path / "cfg.yaml",
            """\
            repos:
              caylent-solutions/devbench:
                default_branch: main
            """,
        )
        result = load_runtime_config(cfg, {})
        assert result.manifest_amendment.allowed_reasons == frozenset(
            {"tdd_green_production_fix", "doc_sync_review_fix"}
        )

    def test_config_schema_allowed_reasons_enum_includes_doc_sync(self) -> None:
        """The config-schema.json enum must accept 'doc_sync_review_fix' for schema validation to pass."""
        schema_path = Path(__file__).parent.parent / "src" / "devbench" / "config-schema.json"
        with schema_path.open(encoding="utf-8") as fh:
            schema = json.load(fh)
        enum = schema["properties"]["manifest_amendment"]["properties"]["allowed_reasons"]["items"]["enum"]
        assert set(enum) == {"tdd_green_production_fix", "doc_sync_review_fix"}, (
            f"config-schema.json ({schema_path}) manifest_amendment.allowed_reasons.items.enum "
            f"must list exactly the two sanctioned reasons; got {enum}"
        )

    def test_production_source_paths_from_yaml(self, tmp_path: Path) -> None:
        """validate.production_source_paths is parsed into a tuple on ValidateConfig."""
        cfg = tmp_path / "devbench.yaml"
        cfg.write_text(
            "repos:\n  org/repo:\n    checkout_directory: repo\n"
            "validate:\n  production_source_paths:\n    - scripts/\n    - tools/\n"
        )
        result = load_runtime_config(cfg, {})
        assert result.validate.production_source_paths == ("scripts/", "tools/")

    def test_production_source_paths_absent_defaults_to_none(self, tmp_path: Path) -> None:
        """Absent means built-in behaviour, expressed as None rather than an empty tuple."""
        cfg = tmp_path / "devbench.yaml"
        cfg.write_text("repos:\n  org/repo:\n    checkout_directory: repo\n")
        assert load_runtime_config(cfg, {}).validate.production_source_paths is None

    def test_production_source_extensions_supports_extensionless_names(self, tmp_path: Path) -> None:
        """An entry is a filename SUFFIX, so an extensionless source file can be declared.
        Inferring a leading dot would make `Makefile` unmatchable."""
        cfg = tmp_path / "devbench.yaml"
        cfg.write_text(
            "repos:\n  org/repo:\n    checkout_directory: repo\n"
            "validate:\n  production_source_extensions:\n    - .py\n    - Makefile\n"
        )
        assert load_runtime_config(cfg, {}).validate.production_source_extensions == (".py", "makefile")

    def test_production_source_paths_rejects_non_list(self, tmp_path: Path) -> None:
        """A scalar is a config error, not silently coerced."""
        cfg = tmp_path / "devbench.yaml"
        cfg.write_text(
            "repos:\n  org/repo:\n    checkout_directory: repo\nvalidate:\n  production_source_paths: scripts/\n"
        )
        with pytest.raises(ValueError, match="production_source_paths"):
            load_runtime_config(cfg, {})

    def test_production_source_paths_rejects_empty_entry(self, tmp_path: Path) -> None:
        """An empty entry would match every path; reject it loudly."""
        cfg = tmp_path / "devbench.yaml"
        cfg.write_text(
            "repos:\n  org/repo:\n    checkout_directory: repo\n"
            "validate:\n  production_source_paths:\n    - scripts/\n    - '  '\n"
        )
        with pytest.raises(ValueError, match="must not contain an empty entry"):
            load_runtime_config(cfg, {})

    def test_audit_trail_paths_from_yaml(self, tmp_path: Path) -> None:
        """validate.audit_trail_paths is parsed into a tuple on ValidateConfig."""
        cfg = tmp_path / "devbench.yaml"
        cfg.write_text(
            "repos:\n  org/repo:\n    checkout_directory: repo\n"
            "validate:\n  audit_trail_paths:\n    - .ai-sdlc/\n    - .audit/\n"
        )
        result = load_runtime_config(cfg, {})
        assert result.validate.audit_trail_paths == (".ai-sdlc/", ".audit/")

    def test_audit_trail_paths_absent_defaults_to_none(self, tmp_path: Path) -> None:
        """Absent means no path is exempt from the task-type invariant, expressed as None."""
        cfg = tmp_path / "devbench.yaml"
        cfg.write_text("repos:\n  org/repo:\n    checkout_directory: repo\n")
        assert load_runtime_config(cfg, {}).validate.audit_trail_paths is None

    def test_audit_trail_paths_strips_surrounding_whitespace(self, tmp_path: Path) -> None:
        """A prefix is compared against a path, so stray YAML whitespace must not defeat the match."""
        cfg = tmp_path / "devbench.yaml"
        cfg.write_text(
            "repos:\n  org/repo:\n    checkout_directory: repo\n"
            "validate:\n  audit_trail_paths:\n    - '  .ai-sdlc/  '\n"
        )
        assert load_runtime_config(cfg, {}).validate.audit_trail_paths == (".ai-sdlc/",)

    def test_audit_trail_paths_rejects_non_list(self, tmp_path: Path) -> None:
        """A scalar is a config error, not silently coerced. Caught by schema validation."""
        cfg = tmp_path / "devbench.yaml"
        cfg.write_text("repos:\n  org/repo:\n    checkout_directory: repo\nvalidate:\n  audit_trail_paths: .ai-sdlc/\n")
        with pytest.raises(ValueError, match="audit_trail_paths"):
            load_runtime_config(cfg, {})

    def test_audit_trail_paths_rejects_non_string_entry(self, tmp_path: Path) -> None:
        """A non-string entry cannot be prefix-matched against a path. Caught by schema validation."""
        cfg = tmp_path / "devbench.yaml"
        cfg.write_text(
            "repos:\n  org/repo:\n    checkout_directory: repo\n"
            "validate:\n  audit_trail_paths:\n    - .ai-sdlc/\n    - 7\n"
        )
        with pytest.raises(ValueError, match="audit_trail_paths"):
            load_runtime_config(cfg, {})

    def test_audit_trail_paths_rejects_empty_entry(self, tmp_path: Path) -> None:
        """An empty prefix would exempt every path in the repo; reject it loudly."""
        cfg = tmp_path / "devbench.yaml"
        cfg.write_text(
            "repos:\n  org/repo:\n    checkout_directory: repo\n"
            "validate:\n  audit_trail_paths:\n    - .ai-sdlc/\n    - '  '\n"
        )
        with pytest.raises(ValueError, match="must not contain an empty entry"):
            load_runtime_config(cfg, {})

    def test_parse_audit_trail_paths_rejects_non_list_directly(self) -> None:
        """The parser's own type guard, independent of the schema.

        Schema validation fires first on the ``load_runtime_config`` path, so
        this guard is belt-and-suspenders for an in-memory raw dict fed
        directly. Pinned here the same way ``_parse_model_rates`` is.
        """
        from devbench.config_loader import _parse_audit_trail_paths

        with pytest.raises(ValueError, match="must be a list of path-prefix strings"):
            _parse_audit_trail_paths(Path("test.yaml"), {"audit_trail_paths": ".ai-sdlc/"})

    def test_parse_audit_trail_paths_rejects_non_string_entry_directly(self) -> None:
        """Same guard, reached via a list whose entries are not all strings."""
        from devbench.config_loader import _parse_audit_trail_paths

        with pytest.raises(ValueError, match="must be a list of path-prefix strings"):
            _parse_audit_trail_paths(Path("test.yaml"), {"audit_trail_paths": [".ai-sdlc/", 7]})

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
    """ADR-32: YAML loader correctly parses the task_factory section (on-by-default
    per D-11, superseding the PR #202 shipped auto-promote-by-default posture, not
    ADR-11) including auto_accept_proposals."""

    @staticmethod
    def _write(path: Path, content: str) -> Path:
        import textwrap

        path.write_text(textwrap.dedent(content))
        return path

    def test_defaults_when_section_absent(self, tmp_path: Path) -> None:
        """D-11: a config omitting task_factory entirely resolves enabled=True,
        auto_accept_proposals=False (spec AC-41)."""
        cfg = self._write(
            tmp_path / "cfg.yaml",
            """\
            repos:
              caylent-solutions/devbench:
                default_branch: main
            """,
        )
        result = load_runtime_config(cfg, {})
        assert result.task_factory.enabled is True
        assert result.task_factory.auto_accept_proposals is False

    def test_task_factory_enabled_without_manifest_amendment_raises(self, tmp_path: Path) -> None:
        """An EXPLICIT task_factory.enabled=true still requires an explicit or
        defaulted manifest_amendment.enabled=true; the contradiction fails fast
        with the existing ValueError (ADR-32 interaction contract, AC-5)."""
        cfg = self._write(
            tmp_path / "cfg.yaml",
            """\
            repos:
              org/repo:
                default_branch: main
            manifest_amendment:
              enabled: false
            task_factory:
              enabled: true
            """,
        )
        with pytest.raises(ValueError, match=r"task_factory\.enabled: true requires manifest_amendment\.enabled: true"):
            load_runtime_config(cfg, {})

    @pytest.mark.parametrize(
        "task_factory_block",
        [
            pytest.param("", id="task_factory_block_absent"),
            pytest.param(
                "task_factory:\n              auto_accept_proposals: false\n",
                id="task_factory_block_present_without_enabled_key",
            ),
        ],
    )
    def test_defaulted_enabled_downgrades_when_manifest_amendment_disabled(
        self, tmp_path: Path, task_factory_block: str
    ) -> None:
        """ADR-32 interaction contract: a config where task_factory.enabled
        resolves via the on-by-default value -- because the key was never
        explicitly set to `true` -- but manifest_amendment is explicitly
        disabled, must NOT brick at config-load (spec Section 0 B-8 migration
        requirement). The defaulted-on task_factory downgrades to disabled
        instead of raising, since the loop has nothing to do without the
        amendment workflow it runs from.

        Parametrized over two shapes that must both downgrade rather than
        raise: (1) the task_factory block is absent entirely, and (2) the
        block is present but only sets an unrelated key
        (auto_accept_proposals), omitting `enabled`. Shape (2) pins that the
        "was `enabled` explicitly set" check inspects the `enabled` KEY
        specifically -- not merely whether the `task_factory` mapping is
        non-empty/truthy. A mutant that computed explicitness from
        truthiness of the whole block (e.g. `bool(task_factory_raw)`) would
        pass shape (1) but incorrectly treat shape (2) as an explicit
        `enabled: true`, wrongly raising ValueError instead of downgrading.
        """
        cfg = self._write(
            tmp_path / "cfg.yaml",
            f"""\
            repos:
              caylent-solutions/devbench:
                default_branch: main
            manifest_amendment:
              enabled: false
            {task_factory_block}""",
        )
        result = load_runtime_config(cfg, {})
        assert result.task_factory.enabled is False, (
            "ADR-32 B-8 migration contract: a task_factory.enabled that resolves via "
            "the on-by-default value (not an explicit `enabled: true` key) against an "
            "explicitly disabled manifest_amendment must downgrade enabled to False "
            "rather than raising, so a pre-existing backlog is never bricked by the "
            "on-by-default flip"
        )
        assert result.task_factory.auto_accept_proposals is False, (
            "ADR-32 B-8 migration contract: the downgraded, defaulted-off task_factory "
            "must also resolve auto_accept_proposals to its unreviewed-promotion-safe "
            "False default -- an unreviewed auto-promote path must never be granted by "
            "a defaults-only resolution the operator never explicitly requested"
        )

    def test_auto_accept_defaults_false_when_key_omitted(self, tmp_path: Path) -> None:
        """Key omitted inside an explicitly-enabled task_factory block -> default False (D-11)."""
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


_TASK_FACTORY_DRIFT_FIELDS = [field.name for field in dataclasses.fields(TaskFactoryConfig)]


@pytest.mark.unit
class TestTaskFactoryDefaultsDriftGuard:
    """Parameterized guard against the task_factory defaults (D-11, ADR-32:
    ``enabled`` True, ``auto_accept_proposals`` False) silently drifting
    across ``TaskFactoryConfig``, ``sample-config.yaml``,
    ``src/devbench/config-schema.json``, the configure-devbench
    ``SKILL.md`` Step 8 prompt, and the ``docs/block-types.md`` config-knobs
    table.

    Modeled on
    ``tests/test_plugin/test_skill_structure.py::TestConfigureDevbenchSkillReportStepDriftGuard``
    (test_review REVIEW_FAIL, round 3: 'the two task_factory defaults are
    hand-restated in sample-config.yaml, SKILL.md Step 8, config-schema.json
    and docs/block-types.md with no drift guard tying them to
    TaskFactoryConfig, and the task's own round-3 log proves nine surfaces
    already drifted under the manual-grep control'). Every comparison below
    treats the ``TaskFactoryConfig`` dataclass default as the single source
    of truth so a future default change is caught the moment any restated
    surface goes stale, instead of relying on a one-time manual grep.

    ``_TASK_FACTORY_DRIFT_FIELDS`` above is derived from
    ``dataclasses.fields(TaskFactoryConfig)`` rather than hand-maintained,
    so a future third ``TaskFactoryConfig`` field is automatically covered
    by every parametrized check in this class instead of silently escaping
    them (test_review REVIEW_FAIL, round 6).

    ``docs/block-types.md``'s config-knobs table only restates
    ``task_factory.enabled`` (it has no ``auto_accept_proposals`` row), so
    that surface is guarded by a single, non-parametrized test rather than
    being folded into the parametrized checks above. The table's adjacent
    ``manifest_amendment.enabled`` row is guarded by ``AmendmentConfig``,
    not ``TaskFactoryConfig``, and is out of scope for this drift guard
    (test_review round-6 remediation item 2 is deliberately not
    implemented here; it belongs to whichever unit owns
    ``AmendmentConfig`` defaults).
    """

    @staticmethod
    def _dataclass_defaults() -> dict[str, bool]:
        defaults: dict[str, bool] = {}
        for field in dataclasses.fields(TaskFactoryConfig):
            assert isinstance(field.default, bool), (
                f"TaskFactoryConfig.{field.name} must declare a bool default for this "
                "drift guard to compare against; got "
                f"{type(field.default).__name__}"
            )
            defaults[field.name] = field.default
        return defaults

    @staticmethod
    def _sample_config_task_factory_section() -> dict[str, object]:
        sample_path = Path(__file__).parent.parent / "sample-config.yaml"
        loaded = yaml.safe_load(sample_path.read_text(encoding="utf-8"))
        section = loaded.get("task_factory") if isinstance(loaded, dict) else None
        assert isinstance(section, dict), (
            f"sample-config.yaml ({sample_path}) must have a top-level 'task_factory:' mapping"
        )
        return section

    @staticmethod
    def _config_schema_task_factory_properties() -> dict[str, dict[str, object]]:
        schema_path = Path(__file__).parent.parent / "src" / "devbench" / "config-schema.json"
        with schema_path.open(encoding="utf-8") as fh:
            schema = json.load(fh)
        properties = schema.get("properties", {}).get("task_factory", {}).get("properties", {})
        assert isinstance(properties, dict) and properties, (
            f"config-schema.json ({schema_path}) must define properties.task_factory.properties"
        )
        return properties

    @staticmethod
    def _skill_step_8_text() -> str:
        skill_path = (
            Path(__file__).parent.parent
            / "plugin-authoring"
            / "devbench-authoring"
            / "skills"
            / "configure-devbench"
            / "SKILL.md"
        )
        content = skill_path.read_text(encoding="utf-8")
        match = re.search(
            r"^## Step \d+ -- task_factory section\n(.*?)(?=^## Step \d+ --)",
            content,
            re.MULTILINE | re.DOTALL,
        )
        assert match is not None, (
            f"configure-devbench/SKILL.md ({skill_path}) must contain a "
            "'## Step N -- task_factory section' walkthrough step"
        )
        return match.group(1)

    @staticmethod
    def _block_types_task_factory_enabled_default() -> bool:
        block_types_path = Path(__file__).parent.parent / "docs" / "block-types.md"
        content = block_types_path.read_text(encoding="utf-8")
        match = re.search(
            r"^\|\s*`task_factory\.enabled`\s*\|\s*`?(true|false)`?[^|]*\|",
            content,
            re.MULTILINE,
        )
        assert match is not None, (
            f"docs/block-types.md ({block_types_path}) must have a config-knobs table "
            "row for '`task_factory.enabled`' with the default in the Default column, "
            "e.g. '| `task_factory.enabled` | `true` (ADR-32) | ... |'"
        )
        return match.group(1) == "true"

    @pytest.mark.parametrize("field_name", _TASK_FACTORY_DRIFT_FIELDS)
    def test_sample_config_matches_dataclass_default(self, field_name: str) -> None:
        dataclass_default = self._dataclass_defaults()[field_name]
        section = self._sample_config_task_factory_section()
        assert field_name in section, (
            f"sample-config.yaml's task_factory block must set '{field_name}' explicitly "
            "so the shipped example never silently relies on an unstated default"
        )
        assert section[field_name] is dataclass_default, (
            f"sample-config.yaml's task_factory.{field_name} is '{section[field_name]}' but "
            f"TaskFactoryConfig.{field_name} defaults to '{dataclass_default}' in "
            "src/devbench/config_loader.py. Update sample-config.yaml to match the "
            "dataclass default (D-11, ADR-32)."
        )

    @pytest.mark.parametrize("field_name", _TASK_FACTORY_DRIFT_FIELDS)
    def test_schema_description_matches_dataclass_default(self, field_name: str) -> None:
        dataclass_default = self._dataclass_defaults()[field_name]
        properties = self._config_schema_task_factory_properties()
        assert field_name in properties, (
            f"src/devbench/config-schema.json must define properties.task_factory.properties.{field_name}"
        )
        description = properties[field_name]["description"]
        assert isinstance(description, str), (
            f"src/devbench/config-schema.json's task_factory.{field_name} description must "
            f"be a string; got {type(description).__name__}"
        )
        match = re.search(r"Default (true|false)", description)
        assert match is not None, (
            f"src/devbench/config-schema.json's task_factory.{field_name} description must "
            "state 'Default true' or 'Default false' so this drift guard can verify it"
        )
        stated_default = match.group(1) == "true"
        assert stated_default == dataclass_default, (
            f"src/devbench/config-schema.json states task_factory.{field_name}'s default as "
            f"'{match.group(1)}' but TaskFactoryConfig.{field_name} defaults to "
            f"'{dataclass_default}' in src/devbench/config_loader.py. Update the schema "
            "description to match the dataclass default (D-11, ADR-32)."
        )

    @pytest.mark.parametrize("field_name", _TASK_FACTORY_DRIFT_FIELDS)
    def test_skill_step_matches_dataclass_default(self, field_name: str) -> None:
        dataclass_default = self._dataclass_defaults()[field_name]
        step_text = self._skill_step_8_text()
        match = re.search(
            rf"{re.escape(field_name)}\s.*?\[true/false,\s*default:\s*(true|false)\]",
            step_text,
            re.DOTALL,
        )
        assert match is not None, (
            f"configure-devbench/SKILL.md Step 8 must state '{field_name}' followed by a "
            "'[true/false, default: <bool>]' annotation on its own bullet"
        )
        stated_default = match.group(1) == "true"
        assert stated_default == dataclass_default, (
            f"configure-devbench/SKILL.md Step 8 states task_factory.{field_name}'s default "
            f"as '{match.group(1)}' but TaskFactoryConfig.{field_name} defaults to "
            f"'{dataclass_default}' in src/devbench/config_loader.py. Update the SKILL.md "
            "prompt to match the dataclass default (D-11, ADR-32)."
        )

    def test_block_types_config_knobs_matches_dataclass_default(self) -> None:
        """docs/block-types.md's config-knobs table restates
        task_factory.enabled's Default column (test_review REVIEW_FAIL,
        round 6: this was the fourth restated surface the round-3 finding
        named that the drift guard added in round 4 left unguarded). This
        checks only 'enabled': the table has no 'auto_accept_proposals'
        row, so there is nothing to guard there. The adjacent
        'manifest_amendment.enabled' row is out of scope (AmendmentConfig,
        not TaskFactoryConfig) and is deliberately left unguarded and
        unmodified here.
        """
        dataclass_default = self._dataclass_defaults()["enabled"]
        stated_default = self._block_types_task_factory_enabled_default()
        assert stated_default == dataclass_default, (
            f"docs/block-types.md's config-knobs table states task_factory.enabled's "
            f"default as '{stated_default}' but TaskFactoryConfig.enabled defaults to "
            f"'{dataclass_default}' in src/devbench/config_loader.py. Update the "
            "Default column to match the dataclass default (D-11, ADR-32)."
        )


@pytest.mark.unit
class TestReportModelsBlock:
    """Issue #223: per-model rate table replaces the retired scalar
    ``token_cost_per_million_*`` / ``token_cost_discount`` fields.

    Asserts the schema validation contract (AC-8): unknown keys under
    ``report.models.<id>`` are rejected; model-id keys themselves are
    open (any string accepted) so operators can register new model ids
    without code changes.
    """

    @staticmethod
    def _write(path: Path, content: str) -> Path:
        path.write_text(textwrap.dedent(content))
        return path

    def test_unknown_per_model_field_rejected(self, tmp_path: Path) -> None:
        """AC-8: ``additionalProperties: false`` on each model entry."""
        cfg = self._write(
            tmp_path / "cfg.yaml",
            """\
            repos:
              org/repo:
                default_branch: main
            report:
              models:
                claude-opus-4-7:
                  input: 5.0
                  output: 25.0
                  typo_field: 1.0
            """,
        )
        with pytest.raises(ValueError, match="schema validation"):
            load_runtime_config(cfg, {})

    def test_arbitrary_model_id_accepted(self, tmp_path: Path) -> None:
        """AC-8: the model-id KEY is open (any string).  Operators add
        new model ids by listing them under ``report.models`` -- no code
        change required.
        """
        cfg = self._write(
            tmp_path / "cfg.yaml",
            """\
            repos:
              org/repo:
                default_branch: main
            report:
              models:
                future-model-9000:
                  input: 0.5
                  output: 2.5
            """,
        )
        rt = load_runtime_config(cfg, {})
        assert "future-model-9000" in rt.report.models
        assert rt.report.models["future-model-9000"].input == 0.5

    def test_missing_required_field_in_model_rejected(self, tmp_path: Path) -> None:
        """``input`` and ``output`` are both mandatory per model entry."""
        cfg = self._write(
            tmp_path / "cfg.yaml",
            """\
            repos:
              org/repo:
                default_branch: main
            report:
              models:
                claude-sonnet-4-6:
                  input: 3.0
            """,
        )
        with pytest.raises(ValueError, match="schema validation"):
            load_runtime_config(cfg, {})

    def test_negative_input_rejected(self, tmp_path: Path) -> None:
        cfg = self._write(
            tmp_path / "cfg.yaml",
            """\
            repos:
              org/repo:
                default_branch: main
            report:
              models:
                claude-opus-4-7:
                  input: -1.0
                  output: 25.0
            """,
        )
        with pytest.raises(ValueError, match="schema validation"):
            load_runtime_config(cfg, {})

    def test_correction_factor_zero_rejected(self, tmp_path: Path) -> None:
        """``correction_factor`` must be strictly positive; 0 makes cost
        identically zero and would silence the cost panel."""
        cfg = self._write(
            tmp_path / "cfg.yaml",
            """\
            repos:
              org/repo:
                default_branch: main
            report:
              models:
                claude-opus-4-7:
                  input: 5.0
                  output: 25.0
                  correction_factor: 0.0
            """,
        )
        with pytest.raises(ValueError, match="schema validation"):
            load_runtime_config(cfg, {})


@pytest.mark.unit
class TestParseModelRatesRuntime:
    """Issue #223: direct unit tests on ``_parse_model_rates`` /
    ``_parse_report_models`` / ``_parse_default_model_rates``.

    Schema validation fires BEFORE these runtime checks on the
    ``load_runtime_config`` happy path, so the runtime checks are
    belt-and-suspenders -- triggered when an in-memory raw dict is fed
    directly (e.g. a JSON-derived config tree).  These tests pin the
    runtime contract independently of the schema.
    """

    def test_parse_model_rates_rejects_non_mapping(self) -> None:
        from devbench.config_loader import _parse_model_rates

        with pytest.raises(ValueError, match="must be a mapping"):
            _parse_model_rates("claude-opus-4-7", "not-a-dict", "test.yaml")

    def test_parse_model_rates_rejects_missing_required_field(self) -> None:
        from devbench.config_loader import _parse_model_rates

        with pytest.raises(ValueError, match="missing required field"):
            _parse_model_rates("claude-opus-4-7", {"input": 5.0}, "test.yaml")

    def test_parse_model_rates_rejects_negative_rate(self) -> None:
        from devbench.config_loader import _parse_model_rates

        with pytest.raises(ValueError, match="must be non-negative"):
            _parse_model_rates("claude-opus-4-7", {"input": -1.0, "output": 25.0}, "test.yaml")

    def test_parse_model_rates_rejects_non_positive_correction_factor(self) -> None:
        from devbench.config_loader import _parse_model_rates

        with pytest.raises(ValueError, match="correction_factor must be > 0"):
            _parse_model_rates(
                "claude-opus-4-7",
                {"input": 5.0, "output": 25.0, "correction_factor": 0.0},
                "test.yaml",
            )

    def test_parse_report_models_returns_empty_for_none(self) -> None:
        from devbench.config_loader import _parse_report_models

        assert _parse_report_models(None, "test.yaml") == {}

    def test_parse_report_models_rejects_non_mapping(self) -> None:
        from devbench.config_loader import _parse_report_models

        with pytest.raises(ValueError, match="must be a mapping of"):
            _parse_report_models(["not", "a", "mapping"], "test.yaml")

    def test_parse_default_model_rates_falls_back_when_none(self) -> None:
        from devbench.config_loader import _parse_default_model_rates
        from devbench.constants import DEFAULT_FALLBACK_MODEL_RATES

        result = _parse_default_model_rates(None, "test.yaml")
        assert result == DEFAULT_FALLBACK_MODEL_RATES


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
        rt = load_runtime_config(cfg, {"DEVBENCH_WORKSPACE_ROOT": str(tmp_path)})
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
        rt = load_runtime_config(cfg, {"DEVBENCH_WORKSPACE_ROOT": str(tmp_path)})
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

    def test_get_repo_local_path_uses_resolved_checkout_path_when_set(self, tmp_path: Path) -> None:
        """get_repo_local_path returns repo_config.resolved_checkout_path directly when populated."""
        from devbench.config_loader import get_repo_local_path

        cfg = self._write(
            tmp_path / "cfg.yaml",
            """\
            repos:
              org/repo:
                default_branch: main
                checkout_directory: explicit-dir
            """,
        )
        rt = load_runtime_config(cfg, {"DEVBENCH_WORKSPACE_ROOT": str(tmp_path)})
        assert rt.repos["org/repo"].resolved_checkout_path == tmp_path / "explicit-dir"
        # workspace_root argument is ignored because resolved_checkout_path is set.
        result = get_repo_local_path("org/repo", rt, Path("/different/ws"))
        assert result == tmp_path / "explicit-dir"


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

    # -- AC-194-4 / AC-194-7: bulk_update_confirm_threshold and bulk_update_audit_path fields --

    def test_backlog_config_default_bulk_update_confirm_threshold(self) -> None:
        """
        Given: BacklogConfig constructed with no arguments
        When: bulk_update_confirm_threshold is accessed
        Then: it equals 10 (the spec default from section 4.7.3, AC-194-4)
        """
        cfg = BacklogConfig()
        assert cfg.bulk_update_confirm_threshold == 10

    def test_backlog_config_default_bulk_update_audit_path(self) -> None:
        """
        Given: BacklogConfig constructed with no arguments
        When: bulk_update_audit_path is accessed
        Then: it equals 'logs/bulk-updates.log' (the spec default from section 4.7.3, AC-194-7)
        """
        cfg = BacklogConfig()
        assert cfg.bulk_update_audit_path == "logs/bulk-updates.log"

    def test_backlog_config_accepts_custom_threshold(self) -> None:
        """
        Given: BacklogConfig constructed with bulk_update_confirm_threshold=5
        When: the field is accessed
        Then: it returns 5
        """
        cfg = BacklogConfig(bulk_update_confirm_threshold=5)
        assert cfg.bulk_update_confirm_threshold == 5

    def test_backlog_config_accepts_zero_threshold(self) -> None:
        """
        Given: BacklogConfig constructed with bulk_update_confirm_threshold=0
        When: the field is accessed
        Then: it returns 0 (zero is the boundary -- always prompt)
        """
        cfg = BacklogConfig(bulk_update_confirm_threshold=0)
        assert cfg.bulk_update_confirm_threshold == 0

    def test_backlog_config_accepts_custom_audit_path(self) -> None:
        """
        Given: BacklogConfig constructed with bulk_update_audit_path='logs/custom.log'
        When: the field is accessed
        Then: it returns the custom path
        """
        cfg = BacklogConfig(bulk_update_audit_path="logs/custom.log")
        assert cfg.bulk_update_audit_path == "logs/custom.log"

    def test_parse_backlog_config_threshold_from_yaml(self, tmp_path: Path) -> None:
        """
        Given: a YAML config with backlog.bulk_update_confirm_threshold: 25
        When: load_runtime_config is called
        Then: RuntimeConfig.backlog.bulk_update_confirm_threshold == 25 (AC-194-4)
        """
        cfg = self._write(
            tmp_path / "cfg.yaml",
            """\
            repos:
              org/repo: {}
            backlog:
              bulk_update_confirm_threshold: 25
            """,
        )
        rt = load_runtime_config(cfg, {})
        assert rt.backlog.bulk_update_confirm_threshold == 25

    def test_parse_backlog_config_audit_path_from_yaml(self, tmp_path: Path) -> None:
        """
        Given: a YAML config with backlog.bulk_update_audit_path: 'logs/ops.log'
        When: load_runtime_config is called
        Then: RuntimeConfig.backlog.bulk_update_audit_path == 'logs/ops.log' (AC-194-7)
        """
        cfg = self._write(
            tmp_path / "cfg.yaml",
            """\
            repos:
              org/repo: {}
            backlog:
              bulk_update_audit_path: logs/ops.log
            """,
        )
        rt = load_runtime_config(cfg, {})
        assert rt.backlog.bulk_update_audit_path == "logs/ops.log"

    def test_parse_backlog_config_absent_fields_use_defaults(self, tmp_path: Path) -> None:
        """
        Given: a YAML config with a backlog: section but no bulk_update_* keys
        When: load_runtime_config is called
        Then: defaults (threshold=10, audit_path='logs/bulk-updates.log') are applied
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
        assert rt.backlog.bulk_update_confirm_threshold == 10
        assert rt.backlog.bulk_update_audit_path == "logs/bulk-updates.log"

    def test_parse_backlog_config_both_new_fields_from_yaml(self, tmp_path: Path) -> None:
        """
        Given: a YAML config with both new backlog fields set
        When: load_runtime_config is called
        Then: both fields are parsed correctly
        """
        cfg = self._write(
            tmp_path / "cfg.yaml",
            """\
            repos:
              org/repo: {}
            backlog:
              default_status_for_new_work_units: draft
              bulk_update_confirm_threshold: 50
              bulk_update_audit_path: audit/bulk-ops.log
            """,
        )
        rt = load_runtime_config(cfg, {})
        assert rt.backlog.default_status_for_new_work_units == "draft"
        assert rt.backlog.bulk_update_confirm_threshold == 50
        assert rt.backlog.bulk_update_audit_path == "audit/bulk-ops.log"

    def test_parse_backlog_config_negative_threshold_raises(self, tmp_path: Path) -> None:
        """
        Given: _parse_backlog_config is called with bulk_update_confirm_threshold: -1
        When: _parse_backlog_config is invoked
        Then: ValueError is raised with a clear message naming the invalid value (AC-194-4)
        """
        from devbench.config_loader import _parse_backlog_config

        fake_path = tmp_path / "cfg.yaml"
        with pytest.raises(
            ValueError,
            match=r"bulk_update_confirm_threshold.*must be >= 0",
        ):
            _parse_backlog_config(fake_path, {"bulk_update_confirm_threshold": -1})

    @pytest.mark.parametrize("threshold", [-1, -10, -100])
    def test_parse_backlog_config_negative_threshold_parametrized(self, tmp_path: Path, threshold: int) -> None:
        """
        Given: _parse_backlog_config is called with a negative threshold value
        When: _parse_backlog_config is invoked
        Then: ValueError is raised for every negative value (AC-194-4)
        """
        from devbench.config_loader import _parse_backlog_config

        fake_path = tmp_path / "cfg.yaml"
        with pytest.raises(ValueError, match=r"bulk_update_confirm_threshold"):
            _parse_backlog_config(fake_path, {"bulk_update_confirm_threshold": threshold})

    def test_parse_backlog_config_direct_threshold_and_path(self, tmp_path: Path) -> None:
        """
        Given: _parse_backlog_config is called directly with both new fields
        When: _parse_backlog_config is invoked
        Then: a BacklogConfig with the supplied values is returned
        """
        from devbench.config_loader import _parse_backlog_config

        fake_path = tmp_path / "cfg.yaml"
        result = _parse_backlog_config(
            fake_path,
            {
                "bulk_update_confirm_threshold": 20,
                "bulk_update_audit_path": "custom/path.log",
            },
        )
        assert result.bulk_update_confirm_threshold == 20
        assert result.bulk_update_audit_path == "custom/path.log"


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
                test_reviewer: sonnet
            """,
        )
        rt = load_runtime_config(cfg, {})
        assert rt.agent_models.review_team.code_reviewer == "opus"
        assert rt.agent_models.review_team.test_reviewer == "sonnet"
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
            validate_agent_model_value("env DEVBENCH_AGENT_MODEL_X", "executor", "garbage", False)
        with pytest.raises(ValueError, match="not a valid Bedrock"):
            validate_agent_model_value("env DEVBENCH_AGENT_MODEL_X", "executor", "opus", True)
        # Happy paths return None.
        validate_agent_model_value("yaml", "executor", "opus", False)
        validate_agent_model_value("yaml", "executor", "claude-opus-4-7", False)
        validate_agent_model_value("yaml", "executor", "us.anthropic.claude-opus-4-7-v1", True)


# ---------------------------------------------------------------------------
# New-lineup model ids -- AC-E3-F1-S1-T1-6/7 (issue #233, spec AC-37, S1.7)
# ---------------------------------------------------------------------------

_NEW_LINEUP_MODEL_IDS = (
    "claude-fable-5",
    "claude-opus-5",
    "claude-opus-4-8",
    "claude-sonnet-5",
)


#: Real ``us.anthropic.claude*`` inference-profile ids, taken verbatim from
#: ``aws bedrock list-inference-profiles --region us-east-1`` (all reported
#: ACTIVE, 2026-08-14). Three distinct shapes appear in AWS's own naming, and
#: issue #342's pattern accepted only the first:
#:   * ``-v<N>``            (older, e.g. opus-4-6-v1)
#:   * no version segment   (current generation, e.g. opus-5)
#:   * dated ``-v<N>:<M>``  (e.g. sonnet-4-5-20250929-v1:0)
#: Pinned as data rather than synthesised (``f"us.anthropic.{id}-v1"``) because
#: a synthesised id cannot catch a naming convention AWS does not follow -- that
#: is exactly how the over-strict pattern survived until a real Bedrock run.
_REAL_BEDROCK_PROFILE_IDS: tuple[str, ...] = (
    "us.anthropic.claude-opus-4-6-v1",
    "us.anthropic.claude-opus-5",
    "us.anthropic.claude-sonnet-5",
    "us.anthropic.claude-opus-4-8",
    "us.anthropic.claude-opus-4-7",
    "us.anthropic.claude-sonnet-4-6",
    "us.anthropic.claude-fable-5",
    "us.anthropic.claude-sonnet-4-5-20250929-v1:0",
    "us.anthropic.claude-opus-4-5-20251101-v1:0",
    "us.anthropic.claude-opus-4-1-20250805-v1:0",
    "us.anthropic.claude-sonnet-4-20250514-v1:0",
)


@pytest.mark.unit
class TestRealBedrockProfileIdsAccepted:
    """Issue #342: every real ACTIVE profile id must load under use_bedrock.

    The previous pattern required a trailing ``-v<N>``, accepting 1 of the 12
    ACTIVE non-haiku profiles in a live account and failing config load for
    every current-generation model, so the Bedrock backend could only run on
    ``us.anthropic.claude-opus-4-6-v1``.
    """

    @pytest.mark.parametrize("model_id", _REAL_BEDROCK_PROFILE_IDS)
    def test_real_profile_id_accepted(self, model_id: str) -> None:
        from devbench.config_loader import validate_agent_model_value

        validate_agent_model_value("yaml", "executor", model_id, True)

    @pytest.mark.parametrize("model_id", _REAL_BEDROCK_PROFILE_IDS)
    def test_real_profile_id_matches_the_pattern_directly(self, model_id: str) -> None:
        from devbench.constants import BEDROCK_AGENT_MODEL_PATTERN

        assert BEDROCK_AGENT_MODEL_PATTERN.match(model_id), (
            f"{model_id!r} is a real ACTIVE Bedrock inference profile and must validate"
        )

    @pytest.mark.parametrize(
        "model_id",
        [
            "us.anthropic.claude-opus-5",  # no version segment
            "us.anthropic.claude-sonnet-4-5-20250929-v1:0",  # dated, ':' in id
        ],
    )
    def test_both_previously_rejected_shapes_round_trip_via_yaml(self, tmp_path: Path, model_id: str) -> None:
        """A full config-load round trip, not just the validator in isolation."""
        cfg = self._write_bedrock_cfg(tmp_path / f"cfg-{abs(hash(model_id))}.yaml", model_id)

        rt = load_runtime_config(cfg, {})

        assert rt.agent_models.executor == model_id

    @staticmethod
    def _write_bedrock_cfg(path: Path, model_id: str) -> Path:
        path.write_text(
            textwrap.dedent(
                f"""\
                repos:
                  org/repo:
                    default_branch: main
                use_bedrock: true
                agents:
                  executor: {model_id}
                """
            ),
            encoding="utf-8",
        )
        return path

    @pytest.mark.parametrize(
        "model_id",
        [
            "anthropic.claude-opus-5",  # missing the us. cross-region prefix
            "eu.anthropic.claude-opus-5",  # wrong region prefix
            "us.anthropic.titan-text-v1",  # wrong model family
            "us.amazon.claude-opus-5",  # wrong vendor
            "claude-opus-5",  # first-party id, not a Bedrock profile
            "opus",  # short name
        ],
    )
    def test_non_bedrock_shapes_still_rejected(self, model_id: str) -> None:
        """Relaxing the version suffix must not turn the check into a no-op."""
        from devbench.config_loader import validate_agent_model_value

        with pytest.raises(ValueError, match="not a valid Bedrock model id"):
            validate_agent_model_value("yaml", "executor", model_id, True)

    def test_haiku_profile_id_still_rejected(self) -> None:
        """The haiku ban (issue #198) runs before the pattern check, so a
        structurally valid haiku profile id must still fail -- and fail with the
        haiku rationale, not a pattern complaint."""
        from devbench.config_loader import validate_agent_model_value

        with pytest.raises(ValueError, match="devbench#198"):
            validate_agent_model_value("yaml", "executor", "us.anthropic.claude-haiku-4-5-20251001-v1:0", True)

    def test_rejection_message_names_a_real_profile_id(self) -> None:
        """The old message's example, 'us.anthropic.claude-opus-4-7-v1', is not a
        real profile id (the real one has no '-v1'), so it pointed operators at a
        value AWS rejects at invocation."""
        from devbench.config_loader import validate_agent_model_value

        with pytest.raises(ValueError) as excinfo:
            validate_agent_model_value("yaml", "executor", "opus", True)
        message = str(excinfo.value)

        assert "us.anthropic.claude-opus-4-7-v1" not in message
        assert "us.anthropic.claude-opus-5" in message
        assert "aws bedrock list-inference-profiles" in message


@pytest.mark.unit
class TestNewLineupModelIdsAccepted:
    """AC-E3-F1-S1-T1-6: validate_agent_model_value accepts every new-lineup
    model id, both as an Anthropic API id (use_bedrock=False) and as its
    Bedrock ARN form (use_bedrock=True), with zero regex changes (spec S1.7:
    the existing ANTHROPIC_AGENT_MODEL_PATTERN / BEDROCK_AGENT_MODEL_PATTERN
    already match these ids).
    """

    @pytest.mark.parametrize("model_id", _NEW_LINEUP_MODEL_IDS)
    def test_anthropic_id_accepted_when_use_bedrock_false(self, model_id: str) -> None:
        from devbench.config_loader import validate_agent_model_value

        validate_agent_model_value("yaml", "executor", model_id, False)

    @pytest.mark.parametrize("model_id", _NEW_LINEUP_MODEL_IDS)
    def test_bedrock_id_accepted_when_use_bedrock_true(self, model_id: str) -> None:
        from devbench.config_loader import validate_agent_model_value

        bedrock_id = f"us.anthropic.{model_id}-v1"
        validate_agent_model_value("yaml", "executor", bedrock_id, True)

    def test_fable_short_name_accepted_when_use_bedrock_false(self) -> None:
        """AC-E3-F1-S1-T1-6: the 'fable' short name is accepted exactly like
        'opus'/'sonnet' when use_bedrock is False."""
        from devbench.config_loader import validate_agent_model_value

        validate_agent_model_value("yaml", "executor", "fable", False)

    def test_fable_short_name_rejected_when_use_bedrock_true(self) -> None:
        """AC-E3-F1-S1-T1-7: 'fable' alone is rejected under use_bedrock=True
        exactly as 'opus'/'sonnet' short names are today (FR-3.3 error
        handling)."""
        from devbench.config_loader import validate_agent_model_value

        with pytest.raises(ValueError, match="not a valid Bedrock model id"):
            validate_agent_model_value("yaml", "executor", "fable", True)

    def test_new_lineup_ids_round_trip_via_yaml(self, tmp_path: Path) -> None:
        """AC-E3-F1-S1-T1-6: a full config-load round trip accepts the new
        default model id via the top-level `agents.executor` field."""
        cfg = tmp_path / "cfg.yaml"
        cfg.parent.mkdir(parents=True, exist_ok=True)
        cfg.write_text(
            textwrap.dedent(
                """\
                repos:
                  org/repo:
                    default_branch: main
                agents:
                  executor: claude-opus-5
                """
            ),
            encoding="utf-8",
        )
        rt = load_runtime_config(cfg, {})
        assert rt.agent_models.executor == "claude-opus-5"

    def test_haiku_still_rejected_after_fable_addition(self) -> None:
        """AC-E3-F1-S1-T1-8: the haiku rejection (with its #198 citation) is
        untouched by the fable short-name addition."""
        from devbench.config_loader import validate_agent_model_value

        with pytest.raises(ValueError, match="caylent-solutions/devbench#198"):
            validate_agent_model_value("yaml", "executor", "haiku", False)


# ---------------------------------------------------------------------------
# Haiku rejection -- AC-198-2, AC-198-3
# ---------------------------------------------------------------------------

_ALL_TOP_LEVEL_AGENT_FIELDS = (
    "executor",
    "blocker_resolver",
    "manifest_amender",
    "security_reviewer",
    "task_factory",
    "review_supervisor",
)

_ALL_REVIEW_TEAM_FIELDS = (
    "code_reviewer",
    "test_reviewer",
    "doc_reviewer",
    "changes_manifest",
)

# All haiku input shapes that must be rejected (use_bedrock=False paths)
_HAIKU_ANTHROPIC_INPUTS = (
    "haiku",
    "claude-haiku-4-5-20251001",
    "Haiku",
    "HAIKU",
)

# Haiku Bedrock ARN shape rejected when use_bedrock=True
_HAIKU_BEDROCK_INPUTS = ("us.anthropic.claude-haiku-4-5-v1",)


@pytest.mark.unit
class TestHaikuRejectionTopLevelFields:
    """AC-198-2: every top-level per-agent field rejects haiku values at config-load time.

    The validator must raise ValueError naming the offending field, the
    rejected value, and referencing caylent-solutions/devbench#198.
    """

    @staticmethod
    def _write(path: Path, content: str) -> Path:
        import textwrap

        path.write_text(textwrap.dedent(content), encoding="utf-8")
        return path

    @pytest.mark.parametrize("field_name", _ALL_TOP_LEVEL_AGENT_FIELDS)
    @pytest.mark.parametrize("haiku_value", _HAIKU_ANTHROPIC_INPUTS)
    def test_top_level_field_rejects_haiku_anthropic_input(
        self, tmp_path: Path, field_name: str, haiku_value: str
    ) -> None:
        """AC-198-2: top-level agents field raises ValueError for any haiku value (use_bedrock=false)."""
        cfg = self._write(
            tmp_path / "cfg.yaml",
            f"""\
            repos:
              org/repo:
                default_branch: main
            use_bedrock: false
            agents:
              {field_name}: "{haiku_value}"
            """,
        )
        with pytest.raises(ValueError, match="#198"):
            load_runtime_config(cfg, {})

    @pytest.mark.parametrize("field_name", _ALL_TOP_LEVEL_AGENT_FIELDS)
    @pytest.mark.parametrize("haiku_value", _HAIKU_BEDROCK_INPUTS)
    def test_top_level_field_rejects_haiku_bedrock_arn(self, tmp_path: Path, field_name: str, haiku_value: str) -> None:
        """AC-198-3: top-level agents field raises ValueError for haiku Bedrock ARN (use_bedrock=true)."""
        cfg = self._write(
            tmp_path / "cfg.yaml",
            f"""\
            repos:
              org/repo:
                default_branch: main
            use_bedrock: true
            agents:
              {field_name}: "{haiku_value}"
            """,
        )
        with pytest.raises(ValueError, match="#198"):
            load_runtime_config(cfg, {})


@pytest.mark.unit
class TestHaikuRejectionReviewTeamFields:
    """AC-198-2: every review_team per-agent field rejects haiku values at config-load time."""

    @staticmethod
    def _write(path: Path, content: str) -> Path:
        import textwrap

        path.write_text(textwrap.dedent(content), encoding="utf-8")
        return path

    @pytest.mark.parametrize("field_name", _ALL_REVIEW_TEAM_FIELDS)
    @pytest.mark.parametrize("haiku_value", _HAIKU_ANTHROPIC_INPUTS)
    def test_review_team_field_rejects_haiku_anthropic_input(
        self, tmp_path: Path, field_name: str, haiku_value: str
    ) -> None:
        """AC-198-2: review_team agents field raises ValueError for any haiku value (use_bedrock=false)."""
        cfg = self._write(
            tmp_path / "cfg.yaml",
            f"""\
            repos:
              org/repo:
                default_branch: main
            use_bedrock: false
            agents:
              review_team:
                {field_name}: "{haiku_value}"
            """,
        )
        with pytest.raises(ValueError, match="#198"):
            load_runtime_config(cfg, {})

    @pytest.mark.parametrize("field_name", _ALL_REVIEW_TEAM_FIELDS)
    @pytest.mark.parametrize("haiku_value", _HAIKU_BEDROCK_INPUTS)
    def test_review_team_field_rejects_haiku_bedrock_arn(
        self, tmp_path: Path, field_name: str, haiku_value: str
    ) -> None:
        """AC-198-3: review_team agents field raises ValueError for haiku Bedrock ARN (use_bedrock=true)."""
        cfg = self._write(
            tmp_path / "cfg.yaml",
            f"""\
            repos:
              org/repo:
                default_branch: main
            use_bedrock: true
            agents:
              review_team:
                {field_name}: "{haiku_value}"
            """,
        )
        with pytest.raises(ValueError, match="#198"):
            load_runtime_config(cfg, {})


@pytest.mark.unit
class TestHaikuRejectionValidatorHelper:
    """AC-198-2: validate_agent_model_value itself rejects haiku across all input shapes."""

    @pytest.mark.parametrize("haiku_value", _HAIKU_ANTHROPIC_INPUTS)
    def test_validator_rejects_haiku_short_and_full_anthropic(self, haiku_value: str) -> None:
        """validate_agent_model_value raises ValueError for haiku values when use_bedrock=False."""
        from devbench.config_loader import validate_agent_model_value

        with pytest.raises(ValueError, match="#198"):
            validate_agent_model_value("yaml", "executor", haiku_value, False)

    @pytest.mark.parametrize("haiku_value", _HAIKU_BEDROCK_INPUTS)
    def test_validator_rejects_haiku_bedrock_arn(self, haiku_value: str) -> None:
        """validate_agent_model_value raises ValueError for haiku Bedrock ARN when use_bedrock=True."""
        from devbench.config_loader import validate_agent_model_value

        with pytest.raises(ValueError, match="#198"):
            validate_agent_model_value("yaml", "executor", haiku_value, True)

    def test_validator_error_message_names_field(self) -> None:
        """ValueError message must name the offending field (AC-198-2 contract)."""
        from devbench.config_loader import validate_agent_model_value

        with pytest.raises(ValueError, match="executor"):
            validate_agent_model_value("yaml", "executor", "haiku", False)

    def test_validator_error_message_names_rejected_value(self) -> None:
        """ValueError message must name the rejected value (AC-198-2 contract)."""
        from devbench.config_loader import validate_agent_model_value

        with pytest.raises(ValueError, match="haiku"):
            validate_agent_model_value("yaml", "executor", "haiku", False)

    def test_non_haiku_values_still_accepted(self) -> None:
        """Non-haiku short names (opus, sonnet) must still pass validation."""
        from devbench.config_loader import validate_agent_model_value

        # These must NOT raise.
        validate_agent_model_value("yaml", "executor", "opus", False)
        validate_agent_model_value("yaml", "executor", "sonnet", False)
        validate_agent_model_value("yaml", "executor", "claude-opus-4-7", False)
        validate_agent_model_value("yaml", "executor", "claude-sonnet-4-6", False)


# ---------------------------------------------------------------------------
# SkillsConfig -- issue #221 E1-E10 (application-agnostic SKILL.md + config)
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestSkillsConfig:
    """SkillsConfig dataclass and ``skills:`` YAML section parsing.

    issue #221 E1-E10: the bundled spec-to-backlog and create-spec skills
    are application-agnostic; operators point them at workspace exemplars
    via this config block. All fields are optional.
    """

    def _write(self, path: Path, content: str) -> Path:
        path.write_text(textwrap.dedent(content), encoding="utf-8")
        return path

    def test_skills_config_defaults(self) -> None:
        """Given no args, SkillsConfig holds None paths + 10 fan-out + 5 iterations."""
        cfg = SkillsConfig()
        assert cfg.exemplar_backlog_path is None
        assert cfg.exemplar_spec_path is None
        assert cfg.fan_out_threshold == 10
        assert cfg.max_iterations == 5

    def test_runtime_config_has_skills_field(self) -> None:
        """RuntimeConfig exposes ``skills`` populated with SkillsConfig defaults."""
        rt = RuntimeConfig()
        assert isinstance(rt.skills, SkillsConfig)
        assert rt.skills.exemplar_backlog_path is None
        assert rt.skills.fan_out_threshold == 10

    def test_absent_skills_section_falls_back_to_defaults(self, tmp_path: Path) -> None:
        """A YAML config without a ``skills:`` section yields default SkillsConfig values."""
        cfg = self._write(
            tmp_path / "cfg.yaml",
            """\
            repos:
              org/repo: {}
            """,
        )
        rt = load_runtime_config(cfg, {})
        assert rt.skills.exemplar_backlog_path is None
        assert rt.skills.exemplar_spec_path is None
        assert rt.skills.fan_out_threshold == 10
        assert rt.skills.max_iterations == 5

    def test_skills_section_parses_all_fields(self, tmp_path: Path) -> None:
        """All four ``skills:`` keys round-trip into SkillsConfig fields."""
        cfg = self._write(
            tmp_path / "cfg.yaml",
            """\
            repos:
              org/repo: {}
            skills:
              exemplar_backlog_path: backlog/_exemplars/representative/BACKLOG.md
              exemplar_spec_path: spec/_exemplars/representative.md
              fan_out_threshold: 25
              max_iterations: 8
            """,
        )
        rt = load_runtime_config(cfg, {})
        assert rt.skills.exemplar_backlog_path == "backlog/_exemplars/representative/BACKLOG.md"
        assert rt.skills.exemplar_spec_path == "spec/_exemplars/representative.md"
        assert rt.skills.fan_out_threshold == 25
        assert rt.skills.max_iterations == 8

    def test_skills_section_partial_yaml_keeps_defaults(self, tmp_path: Path) -> None:
        """Only one field set; the others retain their defaults."""
        cfg = self._write(
            tmp_path / "cfg.yaml",
            """\
            repos:
              org/repo: {}
            skills:
              fan_out_threshold: 3
            """,
        )
        rt = load_runtime_config(cfg, {})
        assert rt.skills.fan_out_threshold == 3
        assert rt.skills.exemplar_backlog_path is None
        assert rt.skills.exemplar_spec_path is None
        assert rt.skills.max_iterations == 5

    def test_schema_rejects_unknown_skills_key(self, tmp_path: Path) -> None:
        """JSON Schema ``additionalProperties: false`` rejects unknown ``skills:`` keys."""
        cfg = self._write(
            tmp_path / "cfg.yaml",
            """\
            repos:
              org/repo: {}
            skills:
              unknown_field: foo
            """,
        )
        with pytest.raises(ValueError, match=r"Additional properties are not allowed.*unknown_field"):
            load_runtime_config(cfg, {})

    def test_schema_rejects_fan_out_threshold_below_minimum(self, tmp_path: Path) -> None:
        """JSON Schema ``minimum: 1`` rejects fan_out_threshold: 0."""
        cfg = self._write(
            tmp_path / "cfg.yaml",
            """\
            repos:
              org/repo: {}
            skills:
              fan_out_threshold: 0
            """,
        )
        with pytest.raises(ValueError):
            load_runtime_config(cfg, {})

    def test_schema_rejects_max_iterations_below_minimum(self, tmp_path: Path) -> None:
        """JSON Schema ``minimum: 1`` rejects max_iterations: 0."""
        cfg = self._write(
            tmp_path / "cfg.yaml",
            """\
            repos:
              org/repo: {}
            skills:
              max_iterations: 0
            """,
        )
        with pytest.raises(ValueError):
            load_runtime_config(cfg, {})

    def test_parse_skills_config_raises_on_negative_fan_out_direct(self, tmp_path: Path) -> None:
        """_parse_skills_config defensive guard fires when the schema layer is bypassed."""
        from devbench.config_loader import _parse_skills_config

        fake_path = tmp_path / "cfg.yaml"
        with pytest.raises(ValueError, match=r"skills.fan_out_threshold must be >= 1"):
            _parse_skills_config(fake_path, {"fan_out_threshold": 0})

    def test_parse_skills_config_raises_on_negative_max_iterations_direct(self, tmp_path: Path) -> None:
        """_parse_skills_config defensive guard fires for max_iterations < 1."""
        from devbench.config_loader import _parse_skills_config

        fake_path = tmp_path / "cfg.yaml"
        with pytest.raises(ValueError, match=r"skills.max_iterations must be >= 1"):
            _parse_skills_config(fake_path, {"max_iterations": 0})

    def test_empty_string_exemplar_paths_normalise_to_none(self, tmp_path: Path) -> None:
        """An empty-string exemplar path normalises to None (treated as unset)."""
        cfg = self._write(
            tmp_path / "cfg.yaml",
            """\
            repos:
              org/repo: {}
            skills:
              exemplar_backlog_path: ""
              exemplar_spec_path: ""
            """,
        )
        rt = load_runtime_config(cfg, {})
        assert rt.skills.exemplar_backlog_path is None
        assert rt.skills.exemplar_spec_path is None


# ---------------------------------------------------------------------------
# orchestrate: reasoning effort and per-turn thinking budget
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestOrchestrateEffortAndThinkingBudget:
    """The ``orchestrate.effort`` / ``max_thinking_tokens`` knobs load from YAML.

    Left unset the SDK session adopts the ambient Claude Code effort, so an
    unattended run's cost profile is decided by whatever the operator's last
    interactive session happened to use. Both must stay ``None`` when absent
    so ``config.py``'s env > YAML > default chain still reaches the built-in
    default rather than being pinned by a stray zero.
    """

    @staticmethod
    def _write(path: Path, content: str) -> Path:
        path.write_text(textwrap.dedent(content), encoding="utf-8")
        return path

    def test_absent_keys_leave_both_fields_none(self, tmp_path: Path) -> None:
        cfg = self._write(
            tmp_path / "cfg.yaml",
            """\
            repos:
              org/repo:
                default_branch: main
            orchestrate:
              max_cascade_depth: 3
            """,
        )

        result = load_runtime_config(cfg, {})

        assert result.orchestrate.effort is None
        assert result.orchestrate.max_thinking_tokens is None

    def test_values_are_read_from_the_orchestrate_block(self, tmp_path: Path) -> None:
        cfg = self._write(
            tmp_path / "cfg.yaml",
            """\
            repos:
              org/repo:
                default_branch: main
            orchestrate:
              effort: medium
              max_thinking_tokens: 8000
            """,
        )

        result = load_runtime_config(cfg, {})

        assert result.orchestrate.effort == "medium"
        assert result.orchestrate.max_thinking_tokens == 8000

    def test_the_new_keys_coexist_with_the_transport_restart_knobs(self, tmp_path: Path) -> None:
        cfg = self._write(
            tmp_path / "cfg.yaml",
            """\
            repos:
              org/repo:
                default_branch: main
            orchestrate:
              effort: low
              max_thinking_tokens: 4096
              max_transport_restarts: 7
            """,
        )

        result = load_runtime_config(cfg, {})

        assert result.orchestrate.effort == "low"
        assert result.orchestrate.max_thinking_tokens == 4096
        assert result.orchestrate.max_transport_restarts == 7

    def test_setting_one_key_leaves_its_sibling_none(self, tmp_path: Path) -> None:
        """Pinning effort must not silently pin the thinking budget too."""
        cfg = self._write(
            tmp_path / "cfg.yaml",
            """\
            repos:
              org/repo:
                default_branch: main
            orchestrate:
              effort: xhigh
            """,
        )

        result = load_runtime_config(cfg, {})

        assert result.orchestrate.effort == "xhigh"
        assert result.orchestrate.max_thinking_tokens is None


# ---------------------------------------------------------------------------
# git_ops: per-unit worktree isolation
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestGitOpsIsolateWorktrees:
    """``git_ops.isolate_worktrees`` opts into per-unit checkouts.

    The combination with ``single_branch`` is rejected at load rather than at
    the first claim: git allows a branch to be checked out in exactly one
    worktree at a time, so the pair would otherwise surface as an opaque git
    error partway through an unattended run.
    """

    @staticmethod
    def _write(path: Path, content: str) -> Path:
        path.write_text(textwrap.dedent(content), encoding="utf-8")
        return path

    def test_defaults_to_false_when_absent(self, tmp_path: Path) -> None:
        cfg = self._write(
            tmp_path / "cfg.yaml",
            """\
            repos:
              org/repo:
                default_branch: main
            """,
        )

        result = load_runtime_config(cfg, {})

        assert result.git_ops.isolate_worktrees is False

    def test_opting_in_is_read_from_yaml(self, tmp_path: Path) -> None:
        cfg = self._write(
            tmp_path / "cfg.yaml",
            """\
            repos:
              org/repo:
                default_branch: main
            git_ops:
              isolate_worktrees: true
            """,
        )

        result = load_runtime_config(cfg, {})

        assert result.git_ops.isolate_worktrees is True

    def test_combining_with_single_branch_is_rejected(self, tmp_path: Path) -> None:
        cfg = self._write(
            tmp_path / "cfg.yaml",
            """\
            repos:
              org/repo:
                default_branch: main
            git_ops:
              isolate_worktrees: true
              single_branch: feat/everything
              defer_pr: true
            """,
        )

        with pytest.raises(ValueError, match=re.escape("mutually exclusive with git_ops.single_branch")):
            load_runtime_config(cfg, {})

    def test_single_branch_alone_still_loads(self, tmp_path: Path) -> None:
        """The guard must not reject the single-branch mode it coexists with."""
        cfg = self._write(
            tmp_path / "cfg.yaml",
            """\
            repos:
              org/repo:
                default_branch: main
            git_ops:
              single_branch: feat/everything
              defer_pr: true
            """,
        )

        result = load_runtime_config(cfg, {})

        assert result.git_ops.single_branch == "feat/everything"
        assert result.git_ops.isolate_worktrees is False
