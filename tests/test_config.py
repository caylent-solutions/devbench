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
from devbench.config_loader import RepoConfig, RuntimeConfig

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
        env = {k: v for k, v in os.environ.items() if k != "DEVBENCH_ALLOWED_REPOS"}
        with patch.dict(os.environ, env, clear=True):
            importlib.reload(config)
            assert isinstance(config.ALLOWED_REPOS, frozenset), (
                f"Expected ALLOWED_REPOS to be a frozenset after reload, got {type(config.ALLOWED_REPOS).__name__}"
            )
            assert len(config.ALLOWED_REPOS) > 0, "Expected ALLOWED_REPOS to be non-empty (sourced from YAML fixture)"

        importlib.reload(config)

    def test_judge_allowed_repos_env_var_has_no_effect(self) -> None:
        """DEVBENCH_ALLOWED_REPOS env var is ignored -- repos come from YAML only."""
        # Capture the baseline ALLOWED_REPOS before patching.
        baseline = frozenset(config.ALLOWED_REPOS)
        assert len(baseline) > 0, "Baseline ALLOWED_REPOS must be non-empty for this test to be meaningful"

        with patch.dict(os.environ, {"DEVBENCH_ALLOWED_REPOS": "org/repo-a,org/repo-b"}, clear=False):
            importlib.reload(config)
            # The env var must not alter ALLOWED_REPOS -- it must remain the same as baseline.
            assert baseline == config.ALLOWED_REPOS, (
                f"ALLOWED_REPOS changed after setting DEVBENCH_ALLOWED_REPOS -- "
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
class TestResolveMergeStrategy:
    """#237: resolve_merge_strategy precedence env > per-repo > top-level > squash.

    Patches the module inputs (_read_env / MERGE_STRATEGY / RUNTIME_CONFIG) directly
    rather than reloading the module, so each precedence layer is isolated.
    """

    def _cfg(self, *, top: str | None = None, per_repo: str | None = None) -> RuntimeConfig:
        repo = RepoConfig(merge_strategy=per_repo) if per_repo is not None else RepoConfig()
        return RuntimeConfig(repos={"org/repo": repo}, merge_strategy=top)

    def test_env_override_wins_over_yaml(self) -> None:
        # env set -> returns the (validated, import-time) MERGE_STRATEGY regardless of YAML.
        with (
            patch.object(config, "_read_env", return_value="merge"),
            patch.object(config, "MERGE_STRATEGY", config.MergeStrategy.MERGE),
            patch.object(config, "RUNTIME_CONFIG", self._cfg(top="squash", per_repo="rebase")),
        ):
            assert config.resolve_merge_strategy("org/repo") is config.MergeStrategy.MERGE

    def test_per_repo_yaml_when_env_unset(self) -> None:
        with (
            patch.object(config, "_read_env", return_value=None),
            patch.object(config, "MERGE_STRATEGY", config.MergeStrategy.SQUASH),
            patch.object(config, "RUNTIME_CONFIG", self._cfg(top="squash", per_repo="rebase")),
        ):
            assert config.resolve_merge_strategy("org/repo") is config.MergeStrategy.REBASE

    def test_top_level_yaml_when_no_per_repo(self) -> None:
        with (
            patch.object(config, "_read_env", return_value=None),
            patch.object(config, "MERGE_STRATEGY", config.MergeStrategy.SQUASH),
            patch.object(config, "RUNTIME_CONFIG", self._cfg(top="merge")),
        ):
            assert config.resolve_merge_strategy("org/repo") is config.MergeStrategy.MERGE

    def test_squash_default_when_env_and_yaml_unset(self) -> None:
        with (
            patch.object(config, "_read_env", return_value=None),
            patch.object(config, "MERGE_STRATEGY", config.MergeStrategy.SQUASH),
            patch.object(config, "RUNTIME_CONFIG", self._cfg()),
        ):
            assert config.resolve_merge_strategy("org/repo") is config.MergeStrategy.SQUASH


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

        env_copy = {k: v for k, v in os.environ.items() if k not in ("DEVBENCH_BACKLOG_ROOT",)}
        with patch.dict(os.environ, env_copy, clear=True):
            importlib.reload(config)
            expected = Path(os.environ["DEVBENCH_WORKSPACE_ROOT"]) / BACKLOG_SUBDIR
            assert expected == config.BACKLOG_ROOT

        importlib.reload(config)

    def test_backlog_index_derived_from_workspace_root(self) -> None:
        """BACKLOG_INDEX is always derived from DEVBENCH_WORKSPACE_ROOT, not from env."""
        env_copy = {k: v for k, v in os.environ.items() if k not in ("DEVBENCH_BACKLOG_INDEX",)}
        with patch.dict(os.environ, env_copy, clear=True):
            importlib.reload(config)
            expected = Path(os.environ["DEVBENCH_WORKSPACE_ROOT"]) / "BACKLOG.md"
            assert expected == config.BACKLOG_INDEX

        importlib.reload(config)

    def test_judge_backlog_root_env_var_has_no_effect(self, tmp_path: Path) -> None:
        """DEVBENCH_BACKLOG_ROOT env var is ignored -- path derived from DEVBENCH_WORKSPACE_ROOT."""
        from devbench.constants import BACKLOG_SUBDIR

        custom_root = tmp_path / "custom-backlog"
        with patch.dict(os.environ, {"DEVBENCH_BACKLOG_ROOT": str(custom_root)}, clear=False):
            importlib.reload(config)
            expected = Path(os.environ["DEVBENCH_WORKSPACE_ROOT"]) / BACKLOG_SUBDIR
            assert expected == config.BACKLOG_ROOT
            assert custom_root != config.BACKLOG_ROOT

        importlib.reload(config)

    def test_judge_backlog_index_env_var_has_no_effect(self, tmp_path: Path) -> None:
        """DEVBENCH_BACKLOG_INDEX env var is ignored -- path derived from DEVBENCH_WORKSPACE_ROOT."""
        custom_index = tmp_path / "CUSTOM_BACKLOG.md"
        with patch.dict(os.environ, {"DEVBENCH_BACKLOG_INDEX": str(custom_index)}, clear=False):
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

    # ---- _resolve_str -----------------------------------------------------

    def test_resolve_str_env_var_wins(self) -> None:
        with patch.dict(os.environ, {"TEST_STR_X": "from-env"}, clear=False):
            assert config._resolve_str("TEST_STR_X", "yaml", "default") == "from-env"

    def test_resolve_str_yaml_when_env_absent(self) -> None:
        env_copy = {k: v for k, v in os.environ.items() if k != "TEST_STR_X"}
        with patch.dict(os.environ, env_copy, clear=True):
            assert config._resolve_str("TEST_STR_X", "yaml-val", "default") == "yaml-val"

    def test_resolve_str_default_when_both_absent(self) -> None:
        env_copy = {k: v for k, v in os.environ.items() if k != "TEST_STR_X"}
        with patch.dict(os.environ, env_copy, clear=True):
            assert config._resolve_str("TEST_STR_X", None, "default-val") == "default-val"

    # ---- _resolve_optional_str -------------------------------------------

    def test_resolve_optional_str_env_var_wins(self) -> None:
        with patch.dict(os.environ, {"TEST_OPT": "envv"}, clear=False):
            assert config._resolve_optional_str("TEST_OPT", "yaml") == "envv"

    def test_resolve_optional_str_yaml_when_env_empty(self) -> None:
        env_copy = {k: v for k, v in os.environ.items() if k != "TEST_OPT"}
        with patch.dict(os.environ, env_copy, clear=True):
            assert config._resolve_optional_str("TEST_OPT", "yaml-val") == "yaml-val"

    def test_resolve_optional_str_none_when_both_empty(self) -> None:
        env_copy = {k: v for k, v in os.environ.items() if k != "TEST_OPT"}
        with patch.dict(os.environ, env_copy, clear=True):
            assert config._resolve_optional_str("TEST_OPT", None) is None

    # ---- _resolve_bool ----------------------------------------------------

    @pytest.mark.parametrize(
        ("raw", "expected"),
        [
            ("1", True),
            ("true", True),
            ("TRUE", True),
            ("yes", True),
            ("on", True),
            ("0", False),
            ("false", False),
            ("FALSE", False),
            ("no", False),
            ("off", False),
        ],
    )
    def test_resolve_bool_truthy_falsy_env_values(self, raw: str, expected: bool) -> None:
        with patch.dict(os.environ, {"TEST_BOOL_X": raw}, clear=False):
            assert config._resolve_bool("TEST_BOOL_X", None, not expected) is expected

    def test_resolve_bool_invalid_env_value_raises(self) -> None:
        with patch.dict(os.environ, {"TEST_BOOL_X": "maybe"}, clear=False):
            with pytest.raises(ValueError, match=r"TEST_BOOL_X must be one of"):
                config._resolve_bool("TEST_BOOL_X", None, False)

    def test_resolve_bool_yaml_when_env_absent(self) -> None:
        env_copy = {k: v for k, v in os.environ.items() if k != "TEST_BOOL_X"}
        with patch.dict(os.environ, env_copy, clear=True):
            assert config._resolve_bool("TEST_BOOL_X", True, False) is True

    # ---- _resolve_str_tuple ----------------------------------------------

    def test_resolve_str_tuple_env_overrides_default(self) -> None:
        with patch.dict(os.environ, {"TEST_TUPLE": "a, b ,c"}, clear=False):
            assert config._resolve_str_tuple("TEST_TUPLE", ("x",)) == ("a", "b", "c")

    def test_resolve_str_tuple_default_when_env_empty(self) -> None:
        with patch.dict(os.environ, {"TEST_TUPLE": "   "}, clear=False):
            assert config._resolve_str_tuple("TEST_TUPLE", ("x", "y")) == ("x", "y")

    def test_resolve_str_tuple_default_when_env_absent(self) -> None:
        env_copy = {k: v for k, v in os.environ.items() if k != "TEST_TUPLE"}
        with patch.dict(os.environ, env_copy, clear=True):
            assert config._resolve_str_tuple("TEST_TUPLE", ("x", "y")) == ("x", "y")


class TestRequireEnv:
    """The required-env-var contract: ``_require_env`` is the single source of
    truth for the import-time required env vars (``DEVBENCH_WORKSPACE_ROOT``
    and ``DEVBENCH_CLAUDE_MODEL``).

    Issue #221 B7: the helper now prints an actionable one-line error to
    stderr and exits with code 2 instead of raising ``RuntimeError``.
    The previous traceback path produced "empty stdout, traceback to
    stderr" which operators saw as silent failure on stdout-only consumers
    (``devbench report > out.txt``).
    """

    def test_returns_value_when_env_var_set(self) -> None:
        with patch.dict(os.environ, {"REQ_TEST_VAR": "some-value"}, clear=False):
            assert config._require_env("REQ_TEST_VAR", "hint") == "some-value"

    def test_exits_with_actionable_message_when_env_var_absent(self, capsys: pytest.CaptureFixture[str]) -> None:
        env_copy = {k: v for k, v in os.environ.items() if k != "REQ_TEST_VAR"}
        with patch.dict(os.environ, env_copy, clear=True):
            with pytest.raises(SystemExit) as excinfo:
                config._require_env("REQ_TEST_VAR", "set-it-properly")
        assert excinfo.value.code == 2
        captured = capsys.readouterr()
        # Actionable message on stderr; stdout stays empty so log scrapers
        # don't false-positive on a missing-env-var line.
        assert captured.out == ""
        assert "REQ_TEST_VAR environment variable is not set. set-it-properly" in captured.err
        # The "devbench:" prefix marks the line as coming from devbench's
        # fail-fast layer, not from an unrelated upstream component.
        assert captured.err.startswith("devbench: ")

    def test_exits_when_env_var_empty(self, capsys: pytest.CaptureFixture[str]) -> None:
        with patch.dict(os.environ, {"REQ_TEST_VAR": ""}, clear=False):
            with pytest.raises(SystemExit) as excinfo:
                config._require_env("REQ_TEST_VAR", "hint")
        assert excinfo.value.code == 2
        assert "REQ_TEST_VAR environment variable is not set. hint" in capsys.readouterr().err


class TestAgentModelEnvOverrideTypeError:
    """``_apply_agent_model_env_overrides`` raises TypeError when an agent_models
    field resolves to a non-string in the second-pass validation loop.  This
    guards against future schema regressions where the field shape changes.
    """

    def test_typeerror_for_non_string_agent_model_value(self) -> None:
        # Build a namespace that mirrors every (var, attr_path) entry in
        # _AGENT_MODEL_ENV_VARS.  Every leaf is None except the one we are
        # exercising, which is a non-string sentinel.
        from types import SimpleNamespace

        review_team = SimpleNamespace(
            code_reviewer=12345,  # the non-string we want the loop to trip on
            test_reviewer=None,
            doc_reviewer=None,
            changes_manifest=None,
        )
        fake = SimpleNamespace(
            executor=None,
            blocker_resolver=None,
            manifest_amender=None,
            security_reviewer=None,
            task_factory=None,
            review_supervisor=None,
            review_team=review_team,
        )
        with patch.object(config.RUNTIME_CONFIG, "agent_models", fake):
            with pytest.raises(TypeError, match=r"resolved to non-string"):
                config._apply_agent_model_env_overrides()


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
                "JUDGE_AGENT_MODEL_CODE_REVIEWER": "opus",
                "JUDGE_AGENT_MODEL_CHANGES_MANIFEST": "opus",
            },
            clear=False,
        ):
            importlib.reload(config)
            assert config.AGENT_MODELS.review_team.code_reviewer == "opus"
            assert config.AGENT_MODELS.review_team.changes_manifest == "opus"
        importlib.reload(config)

    def test_haiku_env_value_rejected_at_load(self) -> None:
        """AC-198-4: haiku env rejected at config.py import (caylent-solutions/devbench#198)."""
        with patch.dict(os.environ, {"JUDGE_AGENT_MODEL_CODE_REVIEWER": "haiku"}, clear=False):
            with pytest.raises(ValueError, match="#198"):
                importlib.reload(config)
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
            "JUDGE_AGENT_MODEL_SECURITY_REVIEWER": "opus",
            "DEVBENCH_AGENT_MODEL_TASK_FACTORY": "opus",
            "JUDGE_AGENT_MODEL_REVIEW_SUPERVISOR": "opus",
            "JUDGE_AGENT_MODEL_CODE_REVIEWER": "opus",
            "JUDGE_AGENT_MODEL_TEST_REVIEWER": "opus",
            "JUDGE_AGENT_MODEL_DOC_REVIEWER": "opus",
            "JUDGE_AGENT_MODEL_CHANGES_MANIFEST": "opus",
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
class TestNotificationsSlackEnvOverride:
    """Coverage helper: exercise the env-var override path for the slack
    webhook URL.  Pre-existing uncovered line (config.py:568) brought
    over the 98% gate by issue #223's test additions; pinning the
    contract independently of the slack notifications feature.
    """

    def test_env_override_replaces_yaml_webhook_url(self) -> None:
        import importlib

        from devbench import config

        original = config.RUNTIME_CONFIG.notifications.slack.webhook_url
        with patch.dict(
            os.environ,
            {"DEVBENCH_NOTIFICATIONS_SLACK_WEBHOOK_URL": "https://hooks.slack.com/services/TEST/HOOK/URL"},
            clear=False,
        ):
            importlib.reload(config)
            assert config.RUNTIME_CONFIG.notifications.slack.webhook_url == (
                "https://hooks.slack.com/services/TEST/HOOK/URL"
            )
        importlib.reload(config)
        # The pre-test value is restored after reload-without-env.
        assert config.RUNTIME_CONFIG.notifications.slack.webhook_url == original


@pytest.mark.unit
class TestOrchestratorInactivityTimeoutConfig:
    """Issue #262 (E10-F2-S1): ORCHESTRATOR_INACTIVITY_TIMEOUT_SECONDS config resolution.

    AC-2: env var DEVBENCH_ORCHESTRATOR_INACTIVITY_TIMEOUT_SECONDS takes precedence
          over YAML; YAML takes precedence over the unset-safe default; a missing env
          var and absent YAML key yield the constant default.
    """

    _ENV_VAR: str = "DEVBENCH_ORCHESTRATOR_INACTIVITY_TIMEOUT_SECONDS"

    def test_inactivity_timeout_exported_from_config(self) -> None:
        """ORCHESTRATOR_INACTIVITY_TIMEOUT_SECONDS is exported from devbench.config."""
        from devbench import config as _cfg

        assert hasattr(_cfg, "ORCHESTRATOR_INACTIVITY_TIMEOUT_SECONDS"), (
            "devbench.config must export ORCHESTRATOR_INACTIVITY_TIMEOUT_SECONDS"
        )

    def test_inactivity_timeout_is_float(self) -> None:
        """ORCHESTRATOR_INACTIVITY_TIMEOUT_SECONDS is a float."""
        from devbench.config import ORCHESTRATOR_INACTIVITY_TIMEOUT_SECONDS

        assert isinstance(ORCHESTRATOR_INACTIVITY_TIMEOUT_SECONDS, float), (
            f"Expected float; got {type(ORCHESTRATOR_INACTIVITY_TIMEOUT_SECONDS).__name__}"
        )

    def test_env_var_wins_over_default(self) -> None:
        """Env var DEVBENCH_ORCHESTRATOR_INACTIVITY_TIMEOUT_SECONDS overrides the default."""
        with patch.dict(os.environ, {self._ENV_VAR: "999.9"}, clear=False):
            result = config._resolve_float(self._ENV_VAR, None, 300.0)
        assert result == 999.9, f"Expected 999.9 from env; got {result}"

    def test_yaml_wins_over_default_when_env_absent(self) -> None:
        """YAML value takes precedence over the constant default when env is absent."""
        env_copy = {k: v for k, v in os.environ.items() if k != self._ENV_VAR}
        with patch.dict(os.environ, env_copy, clear=True):
            result = config._resolve_float(self._ENV_VAR, 77.5, 300.0)
        assert result == 77.5, f"Expected 77.5 from yaml; got {result}"

    def test_default_used_when_both_absent(self) -> None:
        """Unset-safe default is returned when env var is absent and YAML key is None."""
        env_copy = {k: v for k, v in os.environ.items() if k != self._ENV_VAR}
        with patch.dict(os.environ, env_copy, clear=True):
            result = config._resolve_float(self._ENV_VAR, None, 300.0)
        assert result == 300.0, f"Expected 300.0 default; got {result}"

    def test_zero_value_disables_timeout(self) -> None:
        """A value <= 0 resolves correctly and can be used to disable the timeout."""
        with patch.dict(os.environ, {self._ENV_VAR: "0"}, clear=False):
            result = config._resolve_float(self._ENV_VAR, None, 300.0)
        assert result == 0.0, f"Expected 0.0 (disable sentinel); got {result}"

    def test_negative_value_disables_timeout(self) -> None:
        """A negative value resolves correctly and signals timeout disabled."""
        with patch.dict(os.environ, {self._ENV_VAR: "-1.0"}, clear=False):
            result = config._resolve_float(self._ENV_VAR, None, 300.0)
        assert result == -1.0, f"Expected -1.0 (disable sentinel); got {result}"


@pytest.mark.unit
class TestAutoResolveEnabledConfig:
    """E11-F1-S1-T1: AUTO_RESOLVE_ENABLED resolved via _resolve_bool with env precedence."""

    _ENV_VAR = "DEVBENCH_AUTO_RESOLVE_ENABLED"

    def test_env_true_overrides_yaml_false(self) -> None:
        from devbench.constants import DEFAULT_AUTO_RESOLVE_ENABLED

        with patch.dict(os.environ, {self._ENV_VAR: "true"}, clear=False):
            result = config._resolve_bool(self._ENV_VAR, False, DEFAULT_AUTO_RESOLVE_ENABLED)
        assert result is True

    def test_env_false_overrides_yaml_true(self) -> None:
        from devbench.constants import DEFAULT_AUTO_RESOLVE_ENABLED

        with patch.dict(os.environ, {self._ENV_VAR: "false"}, clear=False):
            result = config._resolve_bool(self._ENV_VAR, True, DEFAULT_AUTO_RESOLVE_ENABLED)
        assert result is False

    def test_yaml_true_used_when_env_absent(self) -> None:
        from devbench.constants import DEFAULT_AUTO_RESOLVE_ENABLED

        env_copy = {k: v for k, v in os.environ.items() if k != self._ENV_VAR}
        with patch.dict(os.environ, env_copy, clear=True):
            result = config._resolve_bool(self._ENV_VAR, True, DEFAULT_AUTO_RESOLVE_ENABLED)
        assert result is True

    def test_default_false_when_both_absent(self) -> None:
        """Unset-safe default is False when env var is absent and YAML value is None."""
        from devbench.constants import DEFAULT_AUTO_RESOLVE_ENABLED

        env_copy = {k: v for k, v in os.environ.items() if k != self._ENV_VAR}
        with patch.dict(os.environ, env_copy, clear=True):
            result = config._resolve_bool(self._ENV_VAR, None, DEFAULT_AUTO_RESOLVE_ENABLED)
        assert result is False


@pytest.mark.unit
class TestAutoResolveMaxAttemptsConfig:
    """E11-F1-S1-T1: AUTO_RESOLVE_MAX_ATTEMPTS resolved via DEVBENCH_AUTO_RESOLVE_MAX_ATTEMPTS_ENV constant."""

    def test_env_var_name_constant_used_for_max_attempts(self) -> None:
        """config.py must use DEVBENCH_AUTO_RESOLVE_MAX_ATTEMPTS_ENV constant, not a hardcoded string."""
        from devbench.constants import (
            DEFAULT_AUTO_RESOLVE_MAX_ATTEMPTS,
            DEVBENCH_AUTO_RESOLVE_MAX_ATTEMPTS_ENV,
        )

        env_copy = {k: v for k, v in os.environ.items() if k != DEVBENCH_AUTO_RESOLVE_MAX_ATTEMPTS_ENV}
        with patch.dict(os.environ, env_copy, clear=True):
            result = config._resolve_int(
                DEVBENCH_AUTO_RESOLVE_MAX_ATTEMPTS_ENV, None, DEFAULT_AUTO_RESOLVE_MAX_ATTEMPTS
            )
        assert result == DEFAULT_AUTO_RESOLVE_MAX_ATTEMPTS

    def test_env_override_max_attempts(self) -> None:
        """Env var overrides the default max_attempts when set."""
        from devbench.constants import (
            DEFAULT_AUTO_RESOLVE_MAX_ATTEMPTS,
            DEVBENCH_AUTO_RESOLVE_MAX_ATTEMPTS_ENV,
        )

        with patch.dict(os.environ, {DEVBENCH_AUTO_RESOLVE_MAX_ATTEMPTS_ENV: "7"}, clear=False):
            result = config._resolve_int(
                DEVBENCH_AUTO_RESOLVE_MAX_ATTEMPTS_ENV, None, DEFAULT_AUTO_RESOLVE_MAX_ATTEMPTS
            )
        assert result == 7


# ---------------------------------------------------------------------------
# Skills workflow config resolution -- E12-F1-S1-T1
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestSkillsUseWorkflowConfig:
    """E12-F1-S1-T1: SKILLS_USE_WORKFLOW resolved via _resolve_bool with env precedence."""

    def test_env_true_overrides_yaml_false(self) -> None:
        from devbench.constants import DEFAULT_SKILLS_USE_WORKFLOW, DEVBENCH_SKILLS_USE_WORKFLOW_ENV

        with patch.dict(os.environ, {DEVBENCH_SKILLS_USE_WORKFLOW_ENV: "true"}, clear=False):
            result = config._resolve_bool(DEVBENCH_SKILLS_USE_WORKFLOW_ENV, False, DEFAULT_SKILLS_USE_WORKFLOW)
        assert result is True

    def test_env_false_overrides_yaml_true(self) -> None:
        from devbench.constants import DEFAULT_SKILLS_USE_WORKFLOW, DEVBENCH_SKILLS_USE_WORKFLOW_ENV

        with patch.dict(os.environ, {DEVBENCH_SKILLS_USE_WORKFLOW_ENV: "false"}, clear=False):
            result = config._resolve_bool(DEVBENCH_SKILLS_USE_WORKFLOW_ENV, True, DEFAULT_SKILLS_USE_WORKFLOW)
        assert result is False

    def test_default_false_when_both_absent(self) -> None:
        """Unset-safe default is False when env is absent and YAML value is None."""
        from devbench.constants import DEFAULT_SKILLS_USE_WORKFLOW, DEVBENCH_SKILLS_USE_WORKFLOW_ENV

        env_copy = {k: v for k, v in os.environ.items() if k != DEVBENCH_SKILLS_USE_WORKFLOW_ENV}
        with patch.dict(os.environ, env_copy, clear=True):
            result = config._resolve_bool(DEVBENCH_SKILLS_USE_WORKFLOW_ENV, None, DEFAULT_SKILLS_USE_WORKFLOW)
        assert result is False


@pytest.mark.unit
class TestSkillsWorkflowChunkSizeConfig:
    """E12-F1-S1-T1: SKILLS_WORKFLOW_CHUNK_SIZE resolved via _resolve_int with env precedence."""

    def test_env_overrides_default(self) -> None:
        from devbench.constants import (
            DEFAULT_SKILLS_WORKFLOW_CHUNK_SIZE,
            DEVBENCH_SKILLS_WORKFLOW_CHUNK_SIZE_ENV,
        )

        with patch.dict(os.environ, {DEVBENCH_SKILLS_WORKFLOW_CHUNK_SIZE_ENV: "2"}, clear=False):
            result = config._resolve_int(
                DEVBENCH_SKILLS_WORKFLOW_CHUNK_SIZE_ENV, None, DEFAULT_SKILLS_WORKFLOW_CHUNK_SIZE
            )
        assert result == 2

    def test_default_used_when_env_absent(self) -> None:
        from devbench.constants import (
            DEFAULT_SKILLS_WORKFLOW_CHUNK_SIZE,
            DEVBENCH_SKILLS_WORKFLOW_CHUNK_SIZE_ENV,
        )

        env_copy = {k: v for k, v in os.environ.items() if k != DEVBENCH_SKILLS_WORKFLOW_CHUNK_SIZE_ENV}
        with patch.dict(os.environ, env_copy, clear=True):
            result = config._resolve_int(
                DEVBENCH_SKILLS_WORKFLOW_CHUNK_SIZE_ENV, None, DEFAULT_SKILLS_WORKFLOW_CHUNK_SIZE
            )
        assert result == DEFAULT_SKILLS_WORKFLOW_CHUNK_SIZE


@pytest.mark.unit
class TestSkillsAdversarialReviewThresholdConfig:
    """E12-F1-S1-T1: SKILLS_ADVERSARIAL_REVIEW_THRESHOLD resolved via _resolve_int."""

    def test_env_overrides_default(self) -> None:
        from devbench.constants import (
            DEFAULT_SKILLS_ADVERSARIAL_REVIEW_THRESHOLD,
            DEVBENCH_SKILLS_ADVERSARIAL_REVIEW_THRESHOLD_ENV,
        )

        with patch.dict(os.environ, {DEVBENCH_SKILLS_ADVERSARIAL_REVIEW_THRESHOLD_ENV: "20"}, clear=False):
            result = config._resolve_int(
                DEVBENCH_SKILLS_ADVERSARIAL_REVIEW_THRESHOLD_ENV,
                None,
                DEFAULT_SKILLS_ADVERSARIAL_REVIEW_THRESHOLD,
            )
        assert result == 20

    def test_default_used_when_env_absent(self) -> None:
        from devbench.constants import (
            DEFAULT_SKILLS_ADVERSARIAL_REVIEW_THRESHOLD,
            DEVBENCH_SKILLS_ADVERSARIAL_REVIEW_THRESHOLD_ENV,
        )

        env_copy = {k: v for k, v in os.environ.items() if k != DEVBENCH_SKILLS_ADVERSARIAL_REVIEW_THRESHOLD_ENV}
        with patch.dict(os.environ, env_copy, clear=True):
            result = config._resolve_int(
                DEVBENCH_SKILLS_ADVERSARIAL_REVIEW_THRESHOLD_ENV,
                None,
                DEFAULT_SKILLS_ADVERSARIAL_REVIEW_THRESHOLD,
            )
        assert result == DEFAULT_SKILLS_ADVERSARIAL_REVIEW_THRESHOLD


# ---------------------------------------------------------------------------
# E14-F2-S1-T1: Configurable stop-class to mention-level mapping
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestOrchestratorStopMentionMapConfig:
    """ORCHESTRATOR_STOP_MENTION_MAP resolved via env vars, unset-safe with a noise-reducing default."""

    def test_map_exported_from_config(self) -> None:
        """config.py must export ORCHESTRATOR_STOP_MENTION_MAP."""
        from devbench import config as _cfg
        from devbench import notifications

        assert hasattr(_cfg, "ORCHESTRATOR_STOP_MENTION_MAP"), (
            "devbench.config must export ORCHESTRATOR_STOP_MENTION_MAP"
        )
        m = _cfg.ORCHESTRATOR_STOP_MENTION_MAP
        assert isinstance(m, dict)
        for cls in notifications.ALL_STOP_CLASSES:
            assert cls in m, f"stop class {cls!r} missing from ORCHESTRATOR_STOP_MENTION_MAP"

    def test_unset_env_yields_noise_reducing_default(self) -> None:
        """When all env vars are absent, the default map reduces noise (completion/drain -> none)."""
        from devbench import notifications

        env_keys = [f"DEVBENCH_STOP_MENTION_{cls.upper().replace('-', '_')}" for cls in notifications.ALL_STOP_CLASSES]
        env_copy = {k: v for k, v in os.environ.items() if k not in env_keys}
        with patch.dict(os.environ, env_copy, clear=True):
            m = config.resolve_orchestrator_stop_mention_map(None)
        assert m[notifications.STOP_CLASS_COMPLETION] == notifications.MENTION_LEVEL_NONE
        assert m[notifications.STOP_CLASS_DRAIN] == notifications.MENTION_LEVEL_NONE
        assert m[notifications.STOP_CLASS_PREMATURE_TURN_END] == notifications.MENTION_LEVEL_HERE
        assert m[notifications.STOP_CLASS_CRASH] == notifications.MENTION_LEVEL_HERE
        assert m[notifications.STOP_CLASS_OPERATOR_INTERRUPT] == notifications.MENTION_LEVEL_HERE
        assert m[notifications.STOP_CLASS_QUOTA_EXHAUSTED] == notifications.MENTION_LEVEL_HERE

    def test_env_var_overrides_default_for_stop_class(self) -> None:
        """An env var for a specific stop class overrides the default for that class only."""
        from devbench import notifications

        env_var = f"DEVBENCH_STOP_MENTION_{notifications.STOP_CLASS_CRASH.upper().replace('-', '_')}"
        with patch.dict(os.environ, {env_var: notifications.MENTION_LEVEL_NONE}, clear=False):
            m = config.resolve_orchestrator_stop_mention_map(None)
        assert m[notifications.STOP_CLASS_CRASH] == notifications.MENTION_LEVEL_NONE

    def test_yaml_map_overrides_default(self) -> None:
        """A yaml_map dict overrides the default for the keys it supplies."""
        from devbench import notifications

        env_keys = [f"DEVBENCH_STOP_MENTION_{cls.upper().replace('-', '_')}" for cls in notifications.ALL_STOP_CLASSES]
        env_copy = {k: v for k, v in os.environ.items() if k not in env_keys}
        with patch.dict(os.environ, env_copy, clear=True):
            m = config.resolve_orchestrator_stop_mention_map(
                {notifications.STOP_CLASS_COMPLETION: notifications.MENTION_LEVEL_HERE}
            )
        assert m[notifications.STOP_CLASS_COMPLETION] == notifications.MENTION_LEVEL_HERE
        # Other classes still default.
        assert m[notifications.STOP_CLASS_DRAIN] == notifications.MENTION_LEVEL_NONE

    def test_env_wins_over_yaml(self) -> None:
        """Env var takes precedence over yaml_map value."""
        from devbench import notifications

        cls = notifications.STOP_CLASS_COMPLETION
        env_var = f"DEVBENCH_STOP_MENTION_{cls.upper().replace('-', '_')}"
        with patch.dict(os.environ, {env_var: notifications.MENTION_LEVEL_HERE}, clear=False):
            m = config.resolve_orchestrator_stop_mention_map({cls: notifications.MENTION_LEVEL_NONE})
        assert m[cls] == notifications.MENTION_LEVEL_HERE

    def test_invalid_mention_level_in_env_fails_fast(self) -> None:
        """An invalid mention level in an env var raises ValueError at resolution time."""
        from devbench import notifications

        env_var = f"DEVBENCH_STOP_MENTION_{notifications.STOP_CLASS_CRASH.upper().replace('-', '_')}"
        with patch.dict(os.environ, {env_var: "loud"}, clear=False):
            with pytest.raises(ValueError, match="mention level"):
                config.resolve_orchestrator_stop_mention_map(None)

    def test_invalid_stop_class_key_in_yaml_map_fails_fast(self) -> None:
        """An unknown stop-class key in the yaml map raises ValueError at resolution time."""
        with pytest.raises(ValueError, match="stop-class"):
            config.resolve_orchestrator_stop_mention_map({"not-a-class": "here"})

    def test_invalid_mention_level_in_yaml_map_fails_fast(self) -> None:
        """An invalid mention level in a yaml map value raises ValueError."""
        from devbench import notifications

        with pytest.raises(ValueError, match="mention level"):
            config.resolve_orchestrator_stop_mention_map({notifications.STOP_CLASS_COMPLETION: "loud"})
