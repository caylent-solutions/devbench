"""Configuration module for the judges system.

Centralizes all configuration values, repo validation, and credential access.

Config file path resolution (first match wins):
1. ``--config`` CLI argument  (sets ``JUDGE_CONFIG_PATH`` before module import)
2. ``JUDGE_CONFIG_PATH`` environment variable  (config_loader.py not yet migrated)
3. ``<DEVBENCH_WORKSPACE_ROOT>/backlog/config/devbench.yaml``

Allowed repositories and per-repo settings are defined exclusively in the YAML
config file (``backlog/config/devbench.yaml`` relative to
``DEVBENCH_WORKSPACE_ROOT``).  The deprecated env vars ``JUDGE_ALLOWED_REPOS``,
``JUDGE_BACKLOG_ROOT``, and ``JUDGE_BACKLOG_INDEX`` are no longer read.
"""

import json
import logging
import os
from enum import StrEnum
from pathlib import Path

from devbench.config_loader import (
    RuntimeConfig,
    get_repo_local_path,
    load_runtime_config,
    resolve_config_path,
    validate_agent_model_value,
)
from devbench.constants import (
    BACKLOG_SUBDIR,
    DEFAULT_ALERT_SUMMARY_LIMIT,
    DEFAULT_BEDROCK_REGION,
    DEFAULT_BLOCKED_RECOVERY_WINDOW_SECONDS,
    DEFAULT_CACHE_READ_MULTIPLIER,
    DEFAULT_CACHE_WRITE_1HR_MULTIPLIER,
    DEFAULT_CACHE_WRITE_5MIN_MULTIPLIER,
    DEFAULT_CHECK_REGISTRATION_DELAY_SECONDS,
    DEFAULT_CHECK_REGISTRATION_RETRIES,
    DEFAULT_CI_FAILURE_LOG_BYTES,
    DEFAULT_CI_FAILURE_RETRY_ENABLED,
    DEFAULT_COMMAND_TIMEOUT,
    DEFAULT_DATA_RESIDENCY_MULTIPLIER,
    DEFAULT_FAST_MODE_MULTIPLIER,
    DEFAULT_GH_API_TIMEOUT,
    DEFAULT_GITHUB_CHECK_TIMEOUT_SECONDS,
    DEFAULT_HOOK_TAIL_AGENT_WIDTH,
    DEFAULT_HOOK_TAIL_DESCRIPTION_MAX,
    DEFAULT_HOOK_TAIL_STDOUT_PREVIEW_MAX,
    DEFAULT_HOOK_TAIL_TOOL_WIDTH,
    DEFAULT_INLINE_ORPHAN_CLEANUP_ENABLED,
    DEFAULT_LLM_EVIDENCE_TRUNCATION,
    DEFAULT_LLM_FILE_CONTEXT_LIMIT,
    DEFAULT_LLM_FILE_PREVIEW_CHARS,
    DEFAULT_LLM_TIMEOUT,
    DEFAULT_MAX_CASCADE_DEPTH,
    DEFAULT_MAX_RETRY_ATTEMPTS,
    DEFAULT_ORCHESTRATOR_POLL_INTERVAL,
    DEFAULT_OUTPUT_TRUNCATION_LIMIT,
    DEFAULT_PAUSE_BEFORE_MERGE,
    DEFAULT_PR_REVIEW_AGENTS,
    DEFAULT_PR_REVIEW_DECISION_BLOCKS,
    DEFAULT_PR_REVIEW_POLL_INTERVAL,
    DEFAULT_PR_REVIEW_RESOLUTION_ENABLED,
    DEFAULT_PR_REVIEW_SETTLE_SECONDS,
    DEFAULT_RECENT_PACE_TASKS,
    DEFAULT_SECURITY_FETCH_TIMEOUT,
    DEFAULT_STOP_HOOK_MAX_BLOCKS,
    DEFAULT_STOP_HOOK_STALE_TASK_MINUTES,
    DEFAULT_STOP_HOOK_WINDOW_SECONDS,
    DEFAULT_TEST_TIMEOUT,
    DEFAULT_TOKEN_COST_DISCOUNT,
    DEFAULT_TOKEN_COST_PER_M_INPUT,
    DEFAULT_TOKEN_COST_PER_M_OUTPUT,
    DEVBENCH_BOOTSTRAP_ENV_VAR,
)

_log = logging.getLogger("devbench.config")


def _resolve_float(env_var: str | None, yaml_value: float | None, default: float) -> float:
    """Resolve a float config value with explicit precedence.

    Precedence: environment variable > YAML value > default constant.
    When *env_var* is ``None``, skip env var lookup (YAML-only field).
    """
    if env_var is not None:
        env_val = os.environ.get(env_var)
        if env_val is not None:
            return float(env_val)
    if yaml_value is not None:
        return yaml_value
    return default


def _read_env_strict(new_name: str, legacy_name: str) -> str | None:
    """Read an env var by its new canonical name, hard-rejecting the legacy name.

    This is the single canonical reader for every env-var consumption site
    in config.py after the JUDGE_* -> DEVBENCH_* rename (issue #197).

    Behaviour:
    - If ``DEVBENCH_BOOTSTRAP=1`` is set in ``os.environ``, the legacy-name
      check is skipped entirely so that ``devbench migrate-env`` (the one
      subcommand that must work with legacy vars still present) can import
      config.py without triggering the rejection. This is the ONLY bypass
      (AC-197-7).
    - If the legacy name is set to a non-empty value (regardless of whether
      the new name is also set), raises ``RuntimeError`` with an actionable
      message naming both vars and directing the operator to
      ``devbench migrate-env`` (AC-197-2, AC-197-3).
    - Otherwise reads the new name from ``os.environ``; returns the value
      if set and non-empty, else returns ``None`` (AC-197-1).

    Args:
        new_name: the canonical ``DEVBENCH_*`` environment variable name.
        legacy_name: the deprecated ``JUDGE_*`` environment variable name.

    Returns:
        The value of ``os.environ[new_name]`` when set and non-empty, or
        ``None`` when absent / empty.

    Raises:
        RuntimeError: when ``os.environ[legacy_name]`` is set and non-empty
            and the bootstrap bypass is not active.
    """
    if os.environ.get(DEVBENCH_BOOTSTRAP_ENV_VAR, "") != "1":
        legacy_val = os.environ.get(legacy_name, "")
        if legacy_val:
            raise RuntimeError(
                f"{legacy_name} is no longer accepted; use {new_name}. "
                "Run `devbench migrate-env` to produce the migration shell-script."
            )
    new_val = os.environ.get(new_name, "")
    return new_val if new_val else None


def _strict_int(new_name: str, legacy_name: str, yaml_value: int | None, default: int) -> int:
    """Resolve an integer config value via _read_env_strict (new_name) with YAML fallback.

    Precedence: DEVBENCH_<NAME> env var (strict) > YAML value > default constant.
    Raises RuntimeError when the legacy JUDGE_<NAME> var is set (AC-197-2).
    """
    raw = _read_env_strict(new_name, legacy_name)
    if raw is not None:
        return int(raw)
    if yaml_value is not None:
        return yaml_value
    return default


def _strict_str(new_name: str, legacy_name: str, yaml_value: str | None, default: str) -> str:
    """Resolve a string config value via _read_env_strict (new_name) with YAML fallback.

    Precedence: DEVBENCH_<NAME> env var (strict) > YAML value > default constant.
    Raises RuntimeError when the legacy JUDGE_<NAME> var is set (AC-197-2).
    """
    raw = _read_env_strict(new_name, legacy_name)
    if raw is not None:
        return raw
    if yaml_value is not None:
        return yaml_value
    return default


def _strict_optional_str(new_name: str, legacy_name: str, yaml_value: str | None) -> str | None:
    """Resolve an optional string config value via _read_env_strict (new_name) with YAML fallback.

    Returns None when neither the env var nor the YAML value is set.
    Empty strings are treated as unset.
    Precedence: DEVBENCH_<NAME> env var (strict) > YAML value > None.
    Raises RuntimeError when the legacy JUDGE_<NAME> var is set (AC-197-2).
    """
    raw = _read_env_strict(new_name, legacy_name)
    if raw:
        return raw
    if yaml_value:
        return yaml_value
    return None


def _strict_bool(new_name: str, legacy_name: str, yaml_value: bool | None, default: bool) -> bool:
    """Resolve a boolean config value via _read_env_strict (new_name) with YAML fallback.

    Recognised values (case-insensitive):
      truthy: ``1``, ``true``, ``yes``, ``on``
      falsy:  ``0``, ``false``, ``no``, ``off``

    Precedence: DEVBENCH_<NAME> env var (strict) > YAML value > default constant.
    Raises RuntimeError when the legacy JUDGE_<NAME> var is set (AC-197-2).
    Raises ValueError for unrecognised boolean strings.
    """
    raw = _read_env_strict(new_name, legacy_name)
    if raw is not None:
        lower = raw.strip().lower()
        if lower in ("1", "true", "yes", "on"):
            return True
        if lower in ("0", "false", "no", "off"):
            return False
        if lower:
            raise ValueError(f"{new_name} must be one of 1/0/true/false/yes/no/on/off (case-insensitive); got {raw!r}")
    if yaml_value is not None:
        return yaml_value
    return default


def _strict_float(new_name: str, legacy_name: str, yaml_value: float | None, default: float) -> float:
    """Resolve a float config value via _read_env_strict (new_name) with YAML fallback.

    Precedence: DEVBENCH_<NAME> env var (strict) > YAML value > default constant.
    Raises RuntimeError when the legacy JUDGE_<NAME> var is set (AC-197-2).
    """
    raw = _read_env_strict(new_name, legacy_name)
    if raw is not None:
        return float(raw)
    if yaml_value is not None:
        return yaml_value
    return default


def _strict_str_tuple(new_name: str, legacy_name: str, default: tuple[str, ...]) -> tuple[str, ...]:
    """Resolve a tuple of strings from a comma-separated DEVBENCH_* env var.

    Empty / unset returns *default*. Whitespace around each item is stripped;
    empty items are dropped.
    Raises RuntimeError when the legacy JUDGE_<NAME> var is set (AC-197-2).
    """
    raw = _read_env_strict(new_name, legacy_name)
    if raw is not None and raw.strip():
        return tuple(item.strip() for item in raw.split(",") if item.strip())
    return default


# ---------------------------------------------------------------------------
# Repository allow-list
# ---------------------------------------------------------------------------
# When set, restricts all GitHub operations to this org only.
# Unset or empty to allow any org in the allow-list.
# _read_env_strict is called here first -- before WORKSPACE_ROOT, before
# CLAUDE_MODEL, before any git operation -- satisfying AC-197-12.
ALLOWED_GH_ORG: str = _read_env_strict("DEVBENCH_GH_ORG", "JUDGE_GH_ORG") or ""

# Absolute path to the workspace root directory containing all repo clones.
_workspace_root = _read_env_strict("DEVBENCH_WORKSPACE_ROOT", "JUDGE_WORKSPACE_ROOT") or ""
if not _workspace_root:
    raise RuntimeError(
        "DEVBENCH_WORKSPACE_ROOT environment variable is not set. Set it to the absolute path of your workspace root."
    )
WORKSPACE_ROOT: Path = Path(_workspace_root)

# ---------------------------------------------------------------------------
# YAML config loading
# ---------------------------------------------------------------------------
# Resolve config path and load YAML.  Fails fast if the file cannot be found.
_config_path: Path = resolve_config_path(None, os.environ, WORKSPACE_ROOT)
RUNTIME_CONFIG: RuntimeConfig = load_runtime_config(_config_path, os.environ)

# ---------------------------------------------------------------------------
# Allowed repos -- sourced exclusively from YAML config.
# ---------------------------------------------------------------------------
ALLOWED_REPOS: frozenset[str] = frozenset(RUNTIME_CONFIG.repos)

REPO_LOCAL_PATHS: dict[str, Path] = {
    repo: get_repo_local_path(repo, RUNTIME_CONFIG, WORKSPACE_ROOT) for repo in ALLOWED_REPOS
}

# Short name -> full name mapping for backlog compatibility.
# The backlog table uses short names (e.g., "git-repo") while the allow-list
# uses fully-qualified names (e.g., "caylent-solutions/git-repo").
REPO_SHORT_TO_FULL: dict[str, str] = {repo.split("/", maxsplit=1)[1]: repo for repo in ALLOWED_REPOS}


def resolve_repo(short_or_full: str) -> str:
    """Resolve a short repo name to its fully-qualified form.

    If the name is already fully-qualified, it is returned as-is.
    Raises ``ValueError`` if the name cannot be resolved.
    """
    if short_or_full in ALLOWED_REPOS:
        return short_or_full
    full = REPO_SHORT_TO_FULL.get(short_or_full)
    if full is not None:
        return full
    raise ValueError(
        f"Repository '{short_or_full}' is not recognised. "
        f"Allowed: {sorted(ALLOWED_REPOS)} or short names: {sorted(REPO_SHORT_TO_FULL)}"
    )


# ---------------------------------------------------------------------------
# Backlog paths -- derived from WORKSPACE_ROOT.
# ---------------------------------------------------------------------------
BACKLOG_ROOT: Path = WORKSPACE_ROOT / BACKLOG_SUBDIR
BACKLOG_INDEX: Path = WORKSPACE_ROOT / "BACKLOG.md"

# ---------------------------------------------------------------------------
# Operational parameters
# ---------------------------------------------------------------------------
MAX_RETRY_ATTEMPTS: int = _strict_int(
    "DEVBENCH_MAX_RETRIES", "JUDGE_MAX_RETRIES", RUNTIME_CONFIG.max_executor_retries, DEFAULT_MAX_RETRY_ATTEMPTS
)
GITHUB_CHECK_TIMEOUT_SECONDS: int = _strict_int(
    "DEVBENCH_GH_TIMEOUT",
    "JUDGE_GH_TIMEOUT",
    RUNTIME_CONFIG.timeouts.github_check,
    DEFAULT_GITHUB_CHECK_TIMEOUT_SECONDS,
)
# Issue #114: workflow-registration race defence. The retry loop runs
# `gh pr checks` up to CHECK_REGISTRATION_RETRIES times when the local
# `<repo>/.github/workflows/*.y[a]ml` glob proves CI exists but `gh`
# returns "no checks reported" (Actions has not yet enqueued the run).
# Lives under the YAML `debug:` section because operators only tune
# these when investigating an unusual orchestrator cadence; everyday
# workspaces leave them at the constant default.
CHECK_REGISTRATION_RETRIES: int = _strict_int(
    "DEVBENCH_CHECK_REGISTRATION_RETRIES",
    "JUDGE_CHECK_REGISTRATION_RETRIES",
    RUNTIME_CONFIG.debug.check_registration_retries,
    DEFAULT_CHECK_REGISTRATION_RETRIES,
)
CHECK_REGISTRATION_DELAY_SECONDS: int = _strict_int(
    "DEVBENCH_CHECK_REGISTRATION_DELAY_SECONDS",
    "JUDGE_CHECK_REGISTRATION_DELAY_SECONDS",
    RUNTIME_CONFIG.debug.check_registration_delay_seconds,
    DEFAULT_CHECK_REGISTRATION_DELAY_SECONDS,
)
# Recency cap for the AWAITING_AUTO_RECOVERY audit-comment heuristic in
# the 3-state blocked-task classifier. Lives under YAML `debug:` for
# the same reason as the registration knobs above.
BLOCKED_RECOVERY_WINDOW_SECONDS: int = _strict_int(
    "DEVBENCH_BLOCKED_RECOVERY_WINDOW_SECONDS",
    "JUDGE_BLOCKED_RECOVERY_WINDOW_SECONDS",
    RUNTIME_CONFIG.debug.blocked_recovery_window_seconds,
    DEFAULT_BLOCKED_RECOVERY_WINDOW_SECONDS,
)
# Phase 1: inline orphan-cleanup. Default-on; YAML
# `git_ops.inline_orphan_cleanup: false` or env
# `DEVBENCH_INLINE_ORPHAN_CLEANUP=0` (or `false` / `no` / `off`) opts out.
INLINE_ORPHAN_CLEANUP_ENABLED: bool = _strict_bool(
    "DEVBENCH_INLINE_ORPHAN_CLEANUP",
    "JUDGE_INLINE_ORPHAN_CLEANUP",
    RUNTIME_CONFIG.git_ops.inline_orphan_cleanup,
    DEFAULT_INLINE_ORPHAN_CLEANUP_ENABLED,
)
# Phase 2 (#115): CI-failure feedback log byte cap.
CI_FAILURE_LOG_BYTES: int = _strict_int(
    "DEVBENCH_CI_FAILURE_LOG_BYTES",
    "JUDGE_CI_FAILURE_LOG_BYTES",
    RUNTIME_CONFIG.limits.ci_failure_log_bytes,
    DEFAULT_CI_FAILURE_LOG_BYTES,
)
# Phase 2 (#115): CI-failure executor retry. Default-on (FLIPPED in the
# v-next release); YAML `git_ops.ci_failure_retry: false` or env
# `DEVBENCH_CI_FAILURE_RETRY_ENABLED=0` opts out.
CI_FAILURE_RETRY_ENABLED: bool = _strict_bool(
    "DEVBENCH_CI_FAILURE_RETRY_ENABLED",
    "JUDGE_CI_FAILURE_RETRY_ENABLED",
    RUNTIME_CONFIG.git_ops.ci_failure_retry,
    DEFAULT_CI_FAILURE_RETRY_ENABLED,
)
# Phase 3 (#116): PR review-comment polling. Default-off; YAML
# `git_ops.pr_review_resolution.enabled: true` + `agents: [...]` opts in.
PR_REVIEW_SETTLE_SECONDS: int = _strict_int(
    "DEVBENCH_PR_REVIEW_SETTLE_SECONDS",
    "JUDGE_PR_REVIEW_SETTLE_SECONDS",
    RUNTIME_CONFIG.git_ops.pr_review_resolution.settle_seconds,
    DEFAULT_PR_REVIEW_SETTLE_SECONDS,
)
PR_REVIEW_POLL_INTERVAL: int = _strict_int(
    "DEVBENCH_PR_REVIEW_POLL_INTERVAL",
    "JUDGE_PR_REVIEW_POLL_INTERVAL",
    RUNTIME_CONFIG.git_ops.pr_review_resolution.poll_interval,
    DEFAULT_PR_REVIEW_POLL_INTERVAL,
)
PR_REVIEW_DECISION_BLOCKS: bool = _strict_bool(
    "DEVBENCH_PR_REVIEW_DECISION_BLOCKS",
    "JUDGE_PR_REVIEW_DECISION_BLOCKS",
    RUNTIME_CONFIG.git_ops.pr_review_resolution.decision_blocks,
    DEFAULT_PR_REVIEW_DECISION_BLOCKS,
)
PR_REVIEW_AGENTS: tuple[str, ...] = _strict_str_tuple(
    "DEVBENCH_PR_REVIEW_AGENTS",
    "JUDGE_PR_REVIEW_AGENTS",
    tuple(RUNTIME_CONFIG.git_ops.pr_review_resolution.agents) or DEFAULT_PR_REVIEW_AGENTS,
)
PR_REVIEW_RESOLUTION_ENABLED: bool = _strict_bool(
    "DEVBENCH_PR_REVIEW_RESOLUTION_ENABLED",
    "JUDGE_PR_REVIEW_RESOLUTION_ENABLED",
    RUNTIME_CONFIG.git_ops.pr_review_resolution.enabled,
    DEFAULT_PR_REVIEW_RESOLUTION_ENABLED,
)
# Issue #101: pause-before-merge. Default-off; YAML
# `git_ops.pause_before_merge: true` or env `DEVBENCH_PAUSE_BEFORE_MERGE=1`
# opts in. Mutually exclusive with `defer_pr` and `single_branch`
# (validated at YAML load).
PAUSE_BEFORE_MERGE: bool = _strict_bool(
    "DEVBENCH_PAUSE_BEFORE_MERGE",
    "JUDGE_PAUSE_BEFORE_MERGE",
    RUNTIME_CONFIG.git_ops.pause_before_merge,
    DEFAULT_PAUSE_BEFORE_MERGE,
)
_claude_model = _read_env_strict("DEVBENCH_CLAUDE_MODEL", "JUDGE_CLAUDE_MODEL") or ""
if not _claude_model:
    raise RuntimeError(
        "DEVBENCH_CLAUDE_MODEL environment variable is not set. "
        "Set it to a valid model identifier (e.g. us.anthropic.claude-sonnet-4-6-v1)."
    )
CLAUDE_MODEL: str = _claude_model


class MergeStrategy(StrEnum):
    """Valid merge strategies for PR merges."""

    MERGE = "merge"
    SQUASH = "squash"
    REBASE = "rebase"

    @property
    def flag(self) -> str:
        """Return the ``gh pr merge`` flag for this strategy."""
        return f"--{self.value}"


# Merge strategy for PRs. Defaults to squash.
_merge_strategy = _read_env_strict("DEVBENCH_MERGE_STRATEGY", "JUDGE_MERGE_STRATEGY") or "squash"
try:
    MERGE_STRATEGY: MergeStrategy = MergeStrategy(_merge_strategy)
except ValueError:
    raise RuntimeError(
        f"DEVBENCH_MERGE_STRATEGY must be one of: {', '.join(s.value for s in MergeStrategy)}. Got: {_merge_strategy}"
    ) from None

UPDATE_SUBMODULE: bool = RUNTIME_CONFIG.git_ops.update_submodule
SINGLE_BRANCH: str | None = RUNTIME_CONFIG.git_ops.single_branch
DEFER_PR: bool = RUNTIME_CONFIG.git_ops.defer_pr
AUTO_FINALIZE: bool = RUNTIME_CONFIG.git_ops.auto_finalize
AUTO_MERGE: bool = RUNTIME_CONFIG.git_ops.auto_merge
MANIFEST_AMENDMENT_CONFIG = RUNTIME_CONFIG.manifest_amendment
TASK_FACTORY_CONFIG = RUNTIME_CONFIG.task_factory
TOKEN_COST_PER_M_INPUT: float = _resolve_float(
    None, RUNTIME_CONFIG.report.token_cost_per_million_input, DEFAULT_TOKEN_COST_PER_M_INPUT
)
TOKEN_COST_PER_M_OUTPUT: float = _resolve_float(
    None, RUNTIME_CONFIG.report.token_cost_per_million_output, DEFAULT_TOKEN_COST_PER_M_OUTPUT
)
# Contract discount off list-price token cost. Fraction in the inclusive
# range zero to one. Default is zero meaning no discount. See
# docs/model-pricing.md for the full semantic. Resolution precedence is
# env var, then YAML, then constant default.
TOKEN_COST_DISCOUNT: float = _strict_float(
    "DEVBENCH_REPORT_TOKEN_COST_DISCOUNT",
    "JUDGE_REPORT_TOKEN_COST_DISCOUNT",
    RUNTIME_CONFIG.report.token_cost_discount,
    DEFAULT_TOKEN_COST_DISCOUNT,
)
# IANA timezone name for displaying timestamps in `devbench report`.
# None means "use the host's system local timezone." Resolution: env > YAML > None.
REPORT_DISPLAY_TIMEZONE: str | None = _strict_optional_str(
    "DEVBENCH_REPORT_TIMEZONE", "JUDGE_REPORT_TIMEZONE", RUNTIME_CONFIG.report.display_timezone
)
# Global display timezone applied by every devbench command that renders
# timestamps (report, hook-tail, watch, any future command). IANA name.
# None means "use the OS local timezone". Resolution: env > YAML > None.
# Per-command surfaces may still override with their own CLI flag or
# command-specific env var (e.g. hook-tail --tz or DEVBENCH_REPORT_TIMEZONE).
DISPLAY_TIMEZONE: str | None = _strict_optional_str(
    "DEVBENCH_DISPLAY_TIMEZONE", "JUDGE_DISPLAY_TIMEZONE", RUNTIME_CONFIG.display_timezone
)
# Cost-calculation multipliers for `devbench report`. Defaults match Anthropic's
# published pricing structure (see constants.py for source).
REPORT_CACHE_READ_MULTIPLIER: float = _strict_float(
    "DEVBENCH_REPORT_CACHE_READ_MULTIPLIER",
    "JUDGE_REPORT_CACHE_READ_MULTIPLIER",
    RUNTIME_CONFIG.report.cache_read_multiplier,
    DEFAULT_CACHE_READ_MULTIPLIER,
)
REPORT_CACHE_WRITE_5MIN_MULTIPLIER: float = _strict_float(
    "DEVBENCH_REPORT_CACHE_WRITE_5MIN_MULTIPLIER",
    "JUDGE_REPORT_CACHE_WRITE_5MIN_MULTIPLIER",
    RUNTIME_CONFIG.report.cache_write_5min_multiplier,
    DEFAULT_CACHE_WRITE_5MIN_MULTIPLIER,
)
REPORT_CACHE_WRITE_1HR_MULTIPLIER: float = _strict_float(
    "DEVBENCH_REPORT_CACHE_WRITE_1HR_MULTIPLIER",
    "JUDGE_REPORT_CACHE_WRITE_1HR_MULTIPLIER",
    RUNTIME_CONFIG.report.cache_write_1hr_multiplier,
    DEFAULT_CACHE_WRITE_1HR_MULTIPLIER,
)
REPORT_DATA_RESIDENCY_MULTIPLIER: float = _strict_float(
    "DEVBENCH_REPORT_DATA_RESIDENCY_MULTIPLIER",
    "JUDGE_REPORT_DATA_RESIDENCY_MULTIPLIER",
    RUNTIME_CONFIG.report.data_residency_multiplier,
    DEFAULT_DATA_RESIDENCY_MULTIPLIER,
)
REPORT_FAST_MODE_MULTIPLIER: float = _strict_float(
    "DEVBENCH_REPORT_FAST_MODE_MULTIPLIER",
    "JUDGE_REPORT_FAST_MODE_MULTIPLIER",
    RUNTIME_CONFIG.report.fast_mode_multiplier,
    DEFAULT_FAST_MODE_MULTIPLIER,
)
# Number of most-recent task completions averaged for the "Recent pace"
# projection in `devbench report`. Resolution precedence: env > YAML > constant.
RECENT_PACE_TASKS: int = _strict_int(
    "DEVBENCH_REPORT_RECENT_PACE_TASKS",
    "JUDGE_REPORT_RECENT_PACE_TASKS",
    RUNTIME_CONFIG.report.recent_pace_tasks,
    DEFAULT_RECENT_PACE_TASKS,
)
# Hook-tail column caps (issue #134). Resolution precedence: env > YAML >
# constant. EVENT_WIDTH stays a hook_tail.py-local constant; the four below
# are the operator-tunable knobs.
HOOK_TAIL_AGENT_WIDTH: int = _strict_int(
    "DEVBENCH_HOOK_TAIL_AGENT_WIDTH",
    "JUDGE_HOOK_TAIL_AGENT_WIDTH",
    RUNTIME_CONFIG.hook_tail.agent_width,
    DEFAULT_HOOK_TAIL_AGENT_WIDTH,
)
HOOK_TAIL_TOOL_WIDTH: int = _strict_int(
    "DEVBENCH_HOOK_TAIL_TOOL_WIDTH",
    "JUDGE_HOOK_TAIL_TOOL_WIDTH",
    RUNTIME_CONFIG.hook_tail.tool_width,
    DEFAULT_HOOK_TAIL_TOOL_WIDTH,
)
HOOK_TAIL_DESCRIPTION_MAX: int = _strict_int(
    "DEVBENCH_HOOK_TAIL_DESCRIPTION_MAX",
    "JUDGE_HOOK_TAIL_DESCRIPTION_MAX",
    RUNTIME_CONFIG.hook_tail.description_max,
    DEFAULT_HOOK_TAIL_DESCRIPTION_MAX,
)
HOOK_TAIL_STDOUT_PREVIEW_MAX: int = _strict_int(
    "DEVBENCH_HOOK_TAIL_STDOUT_PREVIEW_MAX",
    "JUDGE_HOOK_TAIL_STDOUT_PREVIEW_MAX",
    RUNTIME_CONFIG.hook_tail.stdout_preview_max,
    DEFAULT_HOOK_TAIL_STDOUT_PREVIEW_MAX,
)
# Recovery-cascade depth cap (issue #144). env > YAML > default.
MAX_CASCADE_DEPTH: int = _strict_int(
    "DEVBENCH_ORCHESTRATE_MAX_CASCADE_DEPTH",
    "JUDGE_ORCHESTRATE_MAX_CASCADE_DEPTH",
    RUNTIME_CONFIG.orchestrate.max_cascade_depth,
    DEFAULT_MAX_CASCADE_DEPTH,
)
STOP_HOOK_MAX_BLOCKS: int = _strict_int(
    "DEVBENCH_STOP_MAX_BLOCKS",
    "JUDGE_STOP_MAX_BLOCKS",
    RUNTIME_CONFIG.stop_hook.max_blocks,
    DEFAULT_STOP_HOOK_MAX_BLOCKS,
)
STOP_HOOK_WINDOW_SECONDS: int = _strict_int(
    "DEVBENCH_STOP_WINDOW_SECONDS",
    "JUDGE_STOP_WINDOW_SECONDS",
    RUNTIME_CONFIG.stop_hook.window_seconds,
    DEFAULT_STOP_HOOK_WINDOW_SECONDS,
)
STOP_HOOK_STALE_TASK_MINUTES: int = _strict_int(
    "DEVBENCH_STOP_STALE_MINUTES",
    "JUDGE_STOP_STALE_MINUTES",
    RUNTIME_CONFIG.stop_hook.stale_task_minutes,
    DEFAULT_STOP_HOOK_STALE_TASK_MINUTES,
)
USE_BEDROCK: bool = _strict_bool(
    "DEVBENCH_USE_BEDROCK",
    "JUDGE_USE_BEDROCK",
    None,
    False,
)
BEDROCK_REGION: str = _strict_str(
    "DEVBENCH_BEDROCK_REGION",
    "JUDGE_BEDROCK_REGION",
    RUNTIME_CONFIG.bedrock_region,
    os.environ.get("AWS_REGION", DEFAULT_BEDROCK_REGION),
)

# ---------------------------------------------------------------------------
# Per-agent model overrides (ADR-25, Option A shadow-plugin-dir)
# ---------------------------------------------------------------------------
# Merge DEVBENCH_AGENT_MODEL_<NAME> env vars over the YAML-loaded
# RUNTIME_CONFIG.agent_models. Precedence: env > YAML > frontmatter (None).
#
# Each tuple: (new_devbench_var, legacy_judge_var, attr_path). The legacy var
# is passed to _read_env_strict so setting it causes a hard rejection.
_AGENT_MODEL_ENV_VARS: tuple[tuple[str, str, tuple[str, ...]], ...] = (
    ("DEVBENCH_AGENT_MODEL_EXECUTOR", "JUDGE_AGENT_MODEL_EXECUTOR", ("executor",)),
    ("DEVBENCH_AGENT_MODEL_BLOCKER_RESOLVER", "JUDGE_AGENT_MODEL_BLOCKER_RESOLVER", ("blocker_resolver",)),
    ("DEVBENCH_AGENT_MODEL_MANIFEST_AMENDER", "JUDGE_AGENT_MODEL_MANIFEST_AMENDER", ("manifest_amender",)),
    ("DEVBENCH_AGENT_MODEL_SECURITY_REVIEWER", "JUDGE_AGENT_MODEL_SECURITY_REVIEWER", ("security_reviewer",)),
    ("DEVBENCH_AGENT_MODEL_TASK_FACTORY", "JUDGE_AGENT_MODEL_TASK_FACTORY", ("task_factory",)),
    ("DEVBENCH_AGENT_MODEL_REVIEW_SUPERVISOR", "JUDGE_AGENT_MODEL_REVIEW_SUPERVISOR", ("review_supervisor",)),
    ("DEVBENCH_AGENT_MODEL_CODE_REVIEWER", "JUDGE_AGENT_MODEL_CODE_REVIEWER", ("review_team", "code_reviewer")),
    ("DEVBENCH_AGENT_MODEL_TEST_REVIEWER", "JUDGE_AGENT_MODEL_TEST_REVIEWER", ("review_team", "test_reviewer")),
    ("DEVBENCH_AGENT_MODEL_DOC_REVIEWER", "JUDGE_AGENT_MODEL_DOC_REVIEWER", ("review_team", "doc_reviewer")),
    (
        "DEVBENCH_AGENT_MODEL_CHANGES_MANIFEST",
        "JUDGE_AGENT_MODEL_CHANGES_MANIFEST",
        ("review_team", "changes_manifest"),
    ),
)


def _apply_agent_model_env_overrides() -> None:
    """Merge ``DEVBENCH_AGENT_MODEL_*`` env vars over the YAML agent_models block.

    Re-runs validation against the resolved ``USE_BEDROCK`` so an env-supplied
    value gets the same fail-fast treatment as a YAML value would. Validation
    of YAML values already ran in ``config_loader.load_runtime_config`` but
    used the YAML's ``use_bedrock`` flag; the env-driven re-validation here
    catches the case where ``DEVBENCH_USE_BEDROCK`` differs from the YAML setting.

    Setting a legacy ``JUDGE_AGENT_MODEL_*`` env var causes ``RuntimeError``
    via ``_read_env_strict`` (AC-197-2).
    """
    for new_var, legacy_var, attr_path in _AGENT_MODEL_ENV_VARS:
        value = _read_env_strict(new_var, legacy_var) or ""
        if not value:
            continue
        label = ".".join(attr_path)
        validate_agent_model_value(new_var, label, value, USE_BEDROCK)
        target: object = RUNTIME_CONFIG.agent_models
        for attr in attr_path[:-1]:
            target = getattr(target, attr)
        setattr(target, attr_path[-1], value)

    # Re-validate every still-present YAML value against the resolved
    # USE_BEDROCK. The loader already validated against the YAML flag; this
    # second pass catches the DEVBENCH_USE_BEDROCK-overrides-YAML case where the
    # operator flipped the Bedrock toggle via env without rewriting the YAML
    # model strings.
    for _, _, attr_path in _AGENT_MODEL_ENV_VARS:
        target = RUNTIME_CONFIG.agent_models
        for attr in attr_path:
            target = getattr(target, attr)
        if target is None:
            continue
        validate_agent_model_value(
            "DEVBENCH_USE_BEDROCK (env-resolved) vs agent_models",
            ".".join(attr_path),
            target,  # type: ignore[arg-type]
            USE_BEDROCK,
        )


_apply_agent_model_env_overrides()
AGENT_MODELS = RUNTIME_CONFIG.agent_models

# ---------------------------------------------------------------------------
# Timeouts -- all values in seconds
# ---------------------------------------------------------------------------
GH_API_TIMEOUT: int = _strict_int(
    "DEVBENCH_GH_API_TIMEOUT", "JUDGE_GH_API_TIMEOUT", RUNTIME_CONFIG.timeouts.gh_api, DEFAULT_GH_API_TIMEOUT
)
TEST_TIMEOUT: int = _strict_int(
    "DEVBENCH_TEST_TIMEOUT", "JUDGE_TEST_TIMEOUT", RUNTIME_CONFIG.timeouts.test, DEFAULT_TEST_TIMEOUT
)
SECURITY_FETCH_TIMEOUT: int = _strict_int(
    "DEVBENCH_SECURITY_FETCH_TIMEOUT",
    "JUDGE_SECURITY_FETCH_TIMEOUT",
    RUNTIME_CONFIG.timeouts.security_fetch,
    DEFAULT_SECURITY_FETCH_TIMEOUT,
)
LLM_TIMEOUT: int = _strict_int(
    "DEVBENCH_LLM_TIMEOUT", "JUDGE_LLM_TIMEOUT", RUNTIME_CONFIG.timeouts.llm, DEFAULT_LLM_TIMEOUT
)
COMMAND_TIMEOUT: int = _strict_int(
    "DEVBENCH_COMMAND_TIMEOUT", "JUDGE_COMMAND_TIMEOUT", RUNTIME_CONFIG.timeouts.command, DEFAULT_COMMAND_TIMEOUT
)

# ---------------------------------------------------------------------------
# Thresholds and limits
# ---------------------------------------------------------------------------
ALERT_SUMMARY_LIMIT: int = _strict_int(
    "DEVBENCH_ALERT_SUMMARY_LIMIT",
    "JUDGE_ALERT_SUMMARY_LIMIT",
    RUNTIME_CONFIG.limits.alert_summary,
    DEFAULT_ALERT_SUMMARY_LIMIT,
)
OUTPUT_TRUNCATION_LIMIT: int = _strict_int(
    "DEVBENCH_OUTPUT_TRUNCATION",
    "JUDGE_OUTPUT_TRUNCATION",
    RUNTIME_CONFIG.limits.output_truncation,
    DEFAULT_OUTPUT_TRUNCATION_LIMIT,
)
LLM_EVIDENCE_TRUNCATION: int = _strict_int(
    "DEVBENCH_LLM_EVIDENCE_TRUNCATION",
    "JUDGE_LLM_EVIDENCE_TRUNCATION",
    RUNTIME_CONFIG.limits.llm_evidence_truncation,
    DEFAULT_LLM_EVIDENCE_TRUNCATION,
)

# ---------------------------------------------------------------------------
# LLM context limits
# ---------------------------------------------------------------------------
LLM_FILE_CONTEXT_LIMIT: int = _strict_int(
    "DEVBENCH_LLM_FILE_CONTEXT_LIMIT",
    "JUDGE_LLM_FILE_CONTEXT_LIMIT",
    RUNTIME_CONFIG.limits.llm_file_context,
    DEFAULT_LLM_FILE_CONTEXT_LIMIT,
)
LLM_FILE_PREVIEW_CHARS: int = _strict_int(
    "DEVBENCH_LLM_FILE_PREVIEW_CHARS",
    "JUDGE_LLM_FILE_PREVIEW_CHARS",
    RUNTIME_CONFIG.limits.llm_file_preview_chars,
    DEFAULT_LLM_FILE_PREVIEW_CHARS,
)

# ---------------------------------------------------------------------------
# Orchestrator
# ---------------------------------------------------------------------------
ORCHESTRATOR_POLL_INTERVAL: int = _strict_int(
    "DEVBENCH_ORCHESTRATOR_POLL_INTERVAL",
    "JUDGE_ORCHESTRATOR_POLL_INTERVAL",
    RUNTIME_CONFIG.timeouts.orchestrator_poll_interval,
    DEFAULT_ORCHESTRATOR_POLL_INTERVAL,
)

# ---------------------------------------------------------------------------
# Credentials
# ---------------------------------------------------------------------------
GH_TOKEN_FILE: Path = Path(
    _read_env_strict("DEVBENCH_GH_TOKEN_FILE", "JUDGE_GH_TOKEN_FILE") or str(Path.home() / ".gh_token_env")
)
CLAUDE_CREDENTIALS_FILE: Path = Path(
    _read_env_strict("DEVBENCH_CLAUDE_CREDENTIALS_FILE", "JUDGE_CLAUDE_CREDENTIALS_FILE")
    or str(Path.home() / ".claude" / ".credentials.json")
)


def validate_repo(repo: str) -> None:
    """Raise ``ValueError`` if *repo* is not in the allow-list or wrong org.

    When ``DEVBENCH_GH_ORG`` is set, also validates that the repo belongs
    to the specified organization.
    """
    if ALLOWED_GH_ORG and "/" in repo:
        org = repo.split("/", maxsplit=1)[0]
        if org != ALLOWED_GH_ORG:
            raise ValueError(
                f"Repository '{repo}' belongs to org '{org}', "
                f"but DEVBENCH_GH_ORG restricts access to '{ALLOWED_GH_ORG}'."
            )
    if repo not in ALLOWED_REPOS:
        raise ValueError(f"Repository '{repo}' is not allowed. Allowed repositories: {sorted(ALLOWED_REPOS)}")


def get_anthropic_api_key() -> str:
    """Return an Anthropic API key for LLM judge evaluation.

    Reads the Claude Code OAuth token from the credentials file at
    ``CLAUDE_CREDENTIALS_FILE``.  The token has ``user:inference`` scope
    and is accepted by the Anthropic Python SDK as an ``api_key``.

    Raises ``RuntimeError`` if the credentials file is missing, unreadable,
    or does not contain a valid access token.
    """
    if not CLAUDE_CREDENTIALS_FILE.is_file():
        raise RuntimeError(
            f"Claude credentials file not found at '{CLAUDE_CREDENTIALS_FILE}'. "
            "Ensure Claude Code is authenticated (run 'claude' to login)."
        )

    try:
        raw = CLAUDE_CREDENTIALS_FILE.read_text(encoding="utf-8")
        data = json.loads(raw)
    except (OSError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"Failed to read Claude credentials from '{CLAUDE_CREDENTIALS_FILE}': {exc}") from exc

    oauth = data.get("claudeAiOauth")
    if not isinstance(oauth, dict):
        raise RuntimeError(
            f"Unexpected credentials structure in '{CLAUDE_CREDENTIALS_FILE}': missing 'claudeAiOauth' object."
        )

    token = oauth.get("accessToken", "").strip()
    if not token:
        raise RuntimeError(
            f"No access token found in '{CLAUDE_CREDENTIALS_FILE}'. "
            "Re-authenticate Claude Code (run 'claude' to login)."
        )

    return token


def get_gh_token() -> str:
    """Return the GitHub token, reading from file first then env var.

    Raises ``RuntimeError`` if the token cannot be obtained from either source.
    """
    if GH_TOKEN_FILE.is_file():
        token = GH_TOKEN_FILE.read_text().strip()
        if token:
            return token

    token = os.environ.get("GH_TOKEN", "").strip()
    if token:
        return token

    raise RuntimeError(
        f"GitHub token not found. Provide it via the file at '{GH_TOKEN_FILE}' or the GH_TOKEN environment variable."
    )
