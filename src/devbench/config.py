"""Configuration module for the judges system.

Centralizes all configuration values, repo validation, and credential access.

Config file path resolution (first match wins):
1. ``--config`` CLI argument  (sets ``DEVBENCH_CONFIG_PATH`` before module import)
2. ``DEVBENCH_CONFIG_PATH`` environment variable
3. ``<DEVBENCH_WORKSPACE_ROOT>/backlog/config/devbench.yaml``

Allowed repositories and per-repo settings are defined exclusively in the YAML
config file (``backlog/config/devbench.yaml`` relative to
``DEVBENCH_WORKSPACE_ROOT``).  No environment variables override the repo
allow-list -- the YAML repos section is the only source.
"""

import json
import logging
import os
import sys
from enum import StrEnum
from pathlib import Path

from devbench.config_loader import (
    RuntimeConfig,
    get_effective_merge_strategy,
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
    DEFAULT_MODEL_RATES,
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
    ModelRates,
)

_log = logging.getLogger("devbench.config")


def _read_env(name: str) -> str | None:
    """Read an env var by name; return its value when set and non-empty, else ``None``."""
    val = os.environ.get(name, "")
    return val if val else None


def _resolve_int(name: str, yaml_value: int | None, default: int) -> int:
    """Resolve an integer config value with precedence: env var > YAML > default."""
    raw = _read_env(name)
    if raw is not None:
        return int(raw)
    if yaml_value is not None:
        return yaml_value
    return default


def _resolve_str(name: str, yaml_value: str | None, default: str) -> str:
    """Resolve a string config value with precedence: env var > YAML > default."""
    raw = _read_env(name)
    if raw is not None:
        return raw
    if yaml_value is not None:
        return yaml_value
    return default


def _resolve_optional_str(name: str, yaml_value: str | None) -> str | None:
    """Resolve an optional string config value. Returns None when neither env nor YAML is set."""
    raw = _read_env(name)
    if raw:
        return raw
    if yaml_value:
        return yaml_value
    return None


def _resolve_bool(name: str, yaml_value: bool | None, default: bool) -> bool:
    """Resolve a boolean config value (case-insensitive 1/0/true/false/yes/no/on/off)."""
    raw = _read_env(name)
    if raw is not None:
        lower = raw.strip().lower()
        if lower in ("1", "true", "yes", "on"):
            return True
        if lower in ("0", "false", "no", "off"):
            return False
        if lower:
            raise ValueError(f"{name} must be one of 1/0/true/false/yes/no/on/off (case-insensitive); got {raw!r}")
    if yaml_value is not None:
        return yaml_value
    return default


def _resolve_float(name: str, yaml_value: float | None, default: float) -> float:
    """Resolve a float config value with precedence: env var > YAML > default."""
    raw = _read_env(name)
    if raw is not None:
        return float(raw)
    if yaml_value is not None:
        return yaml_value
    return default


def _resolve_str_tuple(name: str, default: tuple[str, ...]) -> tuple[str, ...]:
    """Resolve a tuple of strings from a comma-separated env var; whitespace stripped, empty items dropped."""
    raw = _read_env(name)
    if raw is not None and raw.strip():
        return tuple(item.strip() for item in raw.split(",") if item.strip())
    return default


# ---------------------------------------------------------------------------
# Repository allow-list
# ---------------------------------------------------------------------------
# When set, restricts all GitHub operations to this org only.
# Unset or empty to allow any org in the allow-list.
# Read before WORKSPACE_ROOT, before CLAUDE_MODEL, before any git operation,
# so a missing repo allow-list surfaces at import-time rather than from the
# first command that touches GitHub.
ALLOWED_GH_ORG: str = _read_env("DEVBENCH_GH_ORG") or ""


def _require_env(name: str, hint: str) -> str:
    """Return the value of a required env var or exit with a clean error.

    Used for the two import-time required env vars (``DEVBENCH_WORKSPACE_ROOT``
    and ``DEVBENCH_CLAUDE_MODEL``).  Keeps the failure messages adjacent to
    the variable name so operators see a single actionable error.

    Issue #221 B7: previously raised ``RuntimeError``.  Because this check
    fires at module-import time (before ``cli.py::main`` is reached), a
    plain ``raise`` produced a Python traceback to stderr and empty stdout
    -- the operator-visible symptom that the issue is filed against.
    Now writes the same actionable hint to stderr and exits non-zero so
    the operator sees a one-line error instead of a traceback.  Tests are
    unaffected: ``tests/conftest.py`` sets both env vars before the first
    import of ``devbench.config``.
    """
    value = _read_env(name) or ""
    if not value:
        # Print + sys.exit(2) so the operator gets a clean, single-line
        # error.  Fail-fast (CLAUDE.md): no fallback, no silent default.
        print(f"devbench: {name} environment variable is not set. {hint}", file=sys.stderr)
        sys.exit(2)
    return value


# Absolute path to the workspace root directory containing all repo clones.
WORKSPACE_ROOT: Path = Path(
    _require_env(
        "DEVBENCH_WORKSPACE_ROOT",
        "Set it to the absolute path of your workspace root.",
    )
)

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
MAX_RETRY_ATTEMPTS: int = _resolve_int(
    "DEVBENCH_MAX_RETRIES", RUNTIME_CONFIG.max_executor_retries, DEFAULT_MAX_RETRY_ATTEMPTS
)
GITHUB_CHECK_TIMEOUT_SECONDS: int = _resolve_int(
    "DEVBENCH_GH_TIMEOUT",
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
CHECK_REGISTRATION_RETRIES: int = _resolve_int(
    "DEVBENCH_CHECK_REGISTRATION_RETRIES",
    RUNTIME_CONFIG.debug.check_registration_retries,
    DEFAULT_CHECK_REGISTRATION_RETRIES,
)
CHECK_REGISTRATION_DELAY_SECONDS: int = _resolve_int(
    "DEVBENCH_CHECK_REGISTRATION_DELAY_SECONDS",
    RUNTIME_CONFIG.debug.check_registration_delay_seconds,
    DEFAULT_CHECK_REGISTRATION_DELAY_SECONDS,
)
# Recency cap for the AWAITING_AUTO_RECOVERY audit-comment heuristic in
# the 3-state blocked-task classifier. Lives under YAML `debug:` for
# the same reason as the registration knobs above.
BLOCKED_RECOVERY_WINDOW_SECONDS: int = _resolve_int(
    "DEVBENCH_BLOCKED_RECOVERY_WINDOW_SECONDS",
    RUNTIME_CONFIG.debug.blocked_recovery_window_seconds,
    DEFAULT_BLOCKED_RECOVERY_WINDOW_SECONDS,
)
# Phase 1: inline orphan-cleanup. Default-on; YAML
# `git_ops.inline_orphan_cleanup: false` or env
# `DEVBENCH_INLINE_ORPHAN_CLEANUP=0` (or `false` / `no` / `off`) opts out.
INLINE_ORPHAN_CLEANUP_ENABLED: bool = _resolve_bool(
    "DEVBENCH_INLINE_ORPHAN_CLEANUP",
    RUNTIME_CONFIG.git_ops.inline_orphan_cleanup,
    DEFAULT_INLINE_ORPHAN_CLEANUP_ENABLED,
)
# Phase 2 (#115): CI-failure feedback log byte cap.
CI_FAILURE_LOG_BYTES: int = _resolve_int(
    "DEVBENCH_CI_FAILURE_LOG_BYTES",
    RUNTIME_CONFIG.limits.ci_failure_log_bytes,
    DEFAULT_CI_FAILURE_LOG_BYTES,
)
# Phase 2 (#115): CI-failure executor retry. Default-on (FLIPPED in the
# v-next release); YAML `git_ops.ci_failure_retry: false` or env
# `DEVBENCH_CI_FAILURE_RETRY_ENABLED=0` opts out.
CI_FAILURE_RETRY_ENABLED: bool = _resolve_bool(
    "DEVBENCH_CI_FAILURE_RETRY_ENABLED",
    RUNTIME_CONFIG.git_ops.ci_failure_retry,
    DEFAULT_CI_FAILURE_RETRY_ENABLED,
)
# Phase 3 (#116): PR review-comment polling. Default-off; YAML
# `git_ops.pr_review_resolution.enabled: true` + `agents: [...]` opts in.
PR_REVIEW_SETTLE_SECONDS: int = _resolve_int(
    "DEVBENCH_PR_REVIEW_SETTLE_SECONDS",
    RUNTIME_CONFIG.git_ops.pr_review_resolution.settle_seconds,
    DEFAULT_PR_REVIEW_SETTLE_SECONDS,
)
PR_REVIEW_POLL_INTERVAL: int = _resolve_int(
    "DEVBENCH_PR_REVIEW_POLL_INTERVAL",
    RUNTIME_CONFIG.git_ops.pr_review_resolution.poll_interval,
    DEFAULT_PR_REVIEW_POLL_INTERVAL,
)
PR_REVIEW_DECISION_BLOCKS: bool = _resolve_bool(
    "DEVBENCH_PR_REVIEW_DECISION_BLOCKS",
    RUNTIME_CONFIG.git_ops.pr_review_resolution.decision_blocks,
    DEFAULT_PR_REVIEW_DECISION_BLOCKS,
)
PR_REVIEW_AGENTS: tuple[str, ...] = _resolve_str_tuple(
    "DEVBENCH_PR_REVIEW_AGENTS",
    tuple(RUNTIME_CONFIG.git_ops.pr_review_resolution.agents) or DEFAULT_PR_REVIEW_AGENTS,
)
PR_REVIEW_RESOLUTION_ENABLED: bool = _resolve_bool(
    "DEVBENCH_PR_REVIEW_RESOLUTION_ENABLED",
    RUNTIME_CONFIG.git_ops.pr_review_resolution.enabled,
    DEFAULT_PR_REVIEW_RESOLUTION_ENABLED,
)
# Issue #101: pause-before-merge. Default-off; YAML
# `git_ops.pause_before_merge: true` or env `DEVBENCH_PAUSE_BEFORE_MERGE=1`
# opts in. Mutually exclusive with `defer_pr` and `single_branch`
# (validated at YAML load).
PAUSE_BEFORE_MERGE: bool = _resolve_bool(
    "DEVBENCH_PAUSE_BEFORE_MERGE",
    RUNTIME_CONFIG.git_ops.pause_before_merge,
    DEFAULT_PAUSE_BEFORE_MERGE,
)
CLAUDE_MODEL: str = _require_env(
    "DEVBENCH_CLAUDE_MODEL",
    "Set it to a valid model identifier (e.g. us.anthropic.claude-sonnet-4-6-v1).",
)


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
_merge_strategy = _read_env("DEVBENCH_MERGE_STRATEGY") or "squash"
try:
    MERGE_STRATEGY: MergeStrategy = MergeStrategy(_merge_strategy)
except ValueError:
    raise RuntimeError(
        f"DEVBENCH_MERGE_STRATEGY must be one of: {', '.join(s.value for s in MergeStrategy)}. Got: {_merge_strategy}"
    ) from None


def resolve_merge_strategy(repo: str) -> MergeStrategy:
    """Return the effective ``MergeStrategy`` for *repo*.

    Precedence: ``DEVBENCH_MERGE_STRATEGY`` env var > per-repo
    ``repos.<org/repo>.merge_strategy`` > top-level ``merge_strategy`` >
    ``"squash"`` default.  The env override (and the squash default) are already
    captured in ``MERGE_STRATEGY``; the YAML layers are resolved here per-repo.

    Args:
        repo: Fully-qualified repository name (e.g. ``'org/repo'``).

    Returns:
        The resolved :class:`MergeStrategy`.

    Raises:
        RuntimeError: If a YAML-configured value is not a valid strategy.
    """
    if _read_env("DEVBENCH_MERGE_STRATEGY") is not None:
        return MERGE_STRATEGY
    configured = get_effective_merge_strategy(repo, RUNTIME_CONFIG)
    if configured is None:
        return MERGE_STRATEGY
    try:
        return MergeStrategy(configured)
    except ValueError:
        raise RuntimeError(
            f"merge_strategy {configured!r} for repo {repo!r} must be one of: "
            f"{', '.join(s.value for s in MergeStrategy)}."
        ) from None


UPDATE_SUBMODULE: bool = RUNTIME_CONFIG.git_ops.update_submodule
SINGLE_BRANCH: str | None = RUNTIME_CONFIG.git_ops.single_branch
DEFER_PR: bool = RUNTIME_CONFIG.git_ops.defer_pr
AUTO_FINALIZE: bool = RUNTIME_CONFIG.git_ops.auto_finalize
AUTO_MERGE: bool = RUNTIME_CONFIG.git_ops.auto_merge
MANIFEST_AMENDMENT_CONFIG = RUNTIME_CONFIG.manifest_amendment
TASK_FACTORY_CONFIG = RUNTIME_CONFIG.task_factory
# Per-model token-pricing table (issue #223).  Operators set per-model rates
# in ``backlog/config/devbench.yaml`` under ``report.models``; we merge that
# with ``DEFAULT_MODEL_RATES`` so the operator's overrides win for the model
# ids they list and the canonical Anthropic defaults apply for everything
# else.  The retired scalar token-cost constants are no longer exposed --
# callers consume ``REPORT_MODEL_RATES`` instead.  Removed per CLAUDE.md
# "Complete Replacement of Superseded Code"; no deprecation shim.
REPORT_MODEL_RATES: dict[str, ModelRates] = {
    **DEFAULT_MODEL_RATES,
    **RUNTIME_CONFIG.report.models,
}
# Rates applied to the ``"<unknown>"`` aggregation bucket (transcript
# messages with no ``model`` field, or model ids not present in
# REPORT_MODEL_RATES).  Operator override via ``report.default_model``;
# falls back to ``DEFAULT_FALLBACK_MODEL_RATES`` (Opus 4.7 list rates).
REPORT_DEFAULT_MODEL_RATES: ModelRates = RUNTIME_CONFIG.report.default_model
# IANA timezone name for displaying timestamps in `devbench report`.
# None means "use the host's system local timezone." Resolution: env > YAML > None.
REPORT_DISPLAY_TIMEZONE: str | None = _resolve_optional_str(
    "DEVBENCH_REPORT_TIMEZONE", RUNTIME_CONFIG.report.display_timezone
)
# Global display timezone applied by every devbench command that renders
# timestamps (report, hook-tail, watch, any future command). IANA name.
# None means "use the OS local timezone". Resolution: env > YAML > None.
# Per-command surfaces may still override with their own CLI flag or
# command-specific env var (e.g. hook-tail --tz or DEVBENCH_REPORT_TIMEZONE).
DISPLAY_TIMEZONE: str | None = _resolve_optional_str("DEVBENCH_DISPLAY_TIMEZONE", RUNTIME_CONFIG.display_timezone)
# Cost-calculation multipliers for `devbench report`. Defaults match Anthropic's
# published pricing structure (see constants.py for source).
REPORT_CACHE_READ_MULTIPLIER: float = _resolve_float(
    "DEVBENCH_REPORT_CACHE_READ_MULTIPLIER",
    RUNTIME_CONFIG.report.cache_read_multiplier,
    DEFAULT_CACHE_READ_MULTIPLIER,
)
REPORT_CACHE_WRITE_5MIN_MULTIPLIER: float = _resolve_float(
    "DEVBENCH_REPORT_CACHE_WRITE_5MIN_MULTIPLIER",
    RUNTIME_CONFIG.report.cache_write_5min_multiplier,
    DEFAULT_CACHE_WRITE_5MIN_MULTIPLIER,
)
REPORT_CACHE_WRITE_1HR_MULTIPLIER: float = _resolve_float(
    "DEVBENCH_REPORT_CACHE_WRITE_1HR_MULTIPLIER",
    RUNTIME_CONFIG.report.cache_write_1hr_multiplier,
    DEFAULT_CACHE_WRITE_1HR_MULTIPLIER,
)
REPORT_DATA_RESIDENCY_MULTIPLIER: float = _resolve_float(
    "DEVBENCH_REPORT_DATA_RESIDENCY_MULTIPLIER",
    RUNTIME_CONFIG.report.data_residency_multiplier,
    DEFAULT_DATA_RESIDENCY_MULTIPLIER,
)
REPORT_FAST_MODE_MULTIPLIER: float = _resolve_float(
    "DEVBENCH_REPORT_FAST_MODE_MULTIPLIER",
    RUNTIME_CONFIG.report.fast_mode_multiplier,
    DEFAULT_FAST_MODE_MULTIPLIER,
)
# Number of most-recent task completions averaged for the "Recent pace"
# projection in `devbench report`. Resolution precedence: env > YAML > constant.
RECENT_PACE_TASKS: int = _resolve_int(
    "DEVBENCH_REPORT_RECENT_PACE_TASKS",
    RUNTIME_CONFIG.report.recent_pace_tasks,
    DEFAULT_RECENT_PACE_TASKS,
)
# Hook-tail column caps (issue #134). Resolution precedence: env > YAML >
# constant. EVENT_WIDTH stays a hook_tail.py-local constant; the four below
# are the operator-tunable knobs.
HOOK_TAIL_AGENT_WIDTH: int = _resolve_int(
    "DEVBENCH_HOOK_TAIL_AGENT_WIDTH",
    RUNTIME_CONFIG.hook_tail.agent_width,
    DEFAULT_HOOK_TAIL_AGENT_WIDTH,
)
HOOK_TAIL_TOOL_WIDTH: int = _resolve_int(
    "DEVBENCH_HOOK_TAIL_TOOL_WIDTH",
    RUNTIME_CONFIG.hook_tail.tool_width,
    DEFAULT_HOOK_TAIL_TOOL_WIDTH,
)
HOOK_TAIL_DESCRIPTION_MAX: int = _resolve_int(
    "DEVBENCH_HOOK_TAIL_DESCRIPTION_MAX",
    RUNTIME_CONFIG.hook_tail.description_max,
    DEFAULT_HOOK_TAIL_DESCRIPTION_MAX,
)
HOOK_TAIL_STDOUT_PREVIEW_MAX: int = _resolve_int(
    "DEVBENCH_HOOK_TAIL_STDOUT_PREVIEW_MAX",
    RUNTIME_CONFIG.hook_tail.stdout_preview_max,
    DEFAULT_HOOK_TAIL_STDOUT_PREVIEW_MAX,
)
# Recovery-cascade depth cap (issue #144). env > YAML > default.
MAX_CASCADE_DEPTH: int = _resolve_int(
    "DEVBENCH_ORCHESTRATE_MAX_CASCADE_DEPTH",
    RUNTIME_CONFIG.orchestrate.max_cascade_depth,
    DEFAULT_MAX_CASCADE_DEPTH,
)
STOP_HOOK_MAX_BLOCKS: int = _resolve_int(
    "DEVBENCH_STOP_MAX_BLOCKS",
    RUNTIME_CONFIG.stop_hook.max_blocks,
    DEFAULT_STOP_HOOK_MAX_BLOCKS,
)
STOP_HOOK_WINDOW_SECONDS: int = _resolve_int(
    "DEVBENCH_STOP_WINDOW_SECONDS",
    RUNTIME_CONFIG.stop_hook.window_seconds,
    DEFAULT_STOP_HOOK_WINDOW_SECONDS,
)
STOP_HOOK_STALE_TASK_MINUTES: int = _resolve_int(
    "DEVBENCH_STOP_STALE_MINUTES",
    RUNTIME_CONFIG.stop_hook.stale_task_minutes,
    DEFAULT_STOP_HOOK_STALE_TASK_MINUTES,
)
USE_BEDROCK: bool = _resolve_bool(
    "DEVBENCH_USE_BEDROCK",
    None,
    False,
)
BEDROCK_REGION: str = _resolve_str(
    "DEVBENCH_BEDROCK_REGION",
    RUNTIME_CONFIG.bedrock_region,
    os.environ.get("AWS_REGION", DEFAULT_BEDROCK_REGION),
)

# ---------------------------------------------------------------------------
# Per-agent model overrides (ADR-25, Option A shadow-plugin-dir)
# ---------------------------------------------------------------------------
# Merge DEVBENCH_AGENT_MODEL_<NAME> env vars over the YAML-loaded
# RUNTIME_CONFIG.agent_models. Precedence: env > YAML > frontmatter (None).
#
# Each tuple: (env_var, attr_path).
_AGENT_MODEL_ENV_VARS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("DEVBENCH_AGENT_MODEL_EXECUTOR", ("executor",)),
    ("DEVBENCH_AGENT_MODEL_BLOCKER_RESOLVER", ("blocker_resolver",)),
    ("DEVBENCH_AGENT_MODEL_MANIFEST_AMENDER", ("manifest_amender",)),
    ("JUDGE_AGENT_MODEL_SECURITY_REVIEWER", ("security_reviewer",)),
    ("DEVBENCH_AGENT_MODEL_TASK_FACTORY", ("task_factory",)),
    ("JUDGE_AGENT_MODEL_REVIEW_SUPERVISOR", ("review_supervisor",)),
    ("JUDGE_AGENT_MODEL_CODE_REVIEWER", ("review_team", "code_reviewer")),
    ("JUDGE_AGENT_MODEL_TEST_REVIEWER", ("review_team", "test_reviewer")),
    ("JUDGE_AGENT_MODEL_DOC_REVIEWER", ("review_team", "doc_reviewer")),
    ("JUDGE_AGENT_MODEL_CHANGES_MANIFEST", ("review_team", "changes_manifest")),
)


def _apply_agent_model_env_overrides() -> None:
    """Merge ``DEVBENCH_AGENT_MODEL_*`` env vars over the YAML agent_models block.

    Re-runs validation against the resolved ``USE_BEDROCK`` so an env-supplied
    value gets the same fail-fast treatment as a YAML value would. Validation
    of YAML values already ran in ``config_loader.load_runtime_config`` but
    used the YAML's ``use_bedrock`` flag; the env-driven re-validation here
    catches the case where ``DEVBENCH_USE_BEDROCK`` differs from the YAML setting.

    """
    for new_var, attr_path in _AGENT_MODEL_ENV_VARS:
        value = _read_env(new_var) or ""
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
    for _, attr_path in _AGENT_MODEL_ENV_VARS:
        target = RUNTIME_CONFIG.agent_models
        for attr in attr_path:
            target = getattr(target, attr)
        if target is None:
            continue
        if not isinstance(target, str):
            msg = (
                f"agent_models.{'.'.join(attr_path)} resolved to non-string {type(target).__name__}; "
                "expected a model name string or None"
            )
            raise TypeError(msg)
        validate_agent_model_value(
            "DEVBENCH_USE_BEDROCK (env-resolved) vs agent_models",
            ".".join(attr_path),
            target,
            USE_BEDROCK,
        )


_apply_agent_model_env_overrides()
AGENT_MODELS = RUNTIME_CONFIG.agent_models


def _apply_notifications_env_overrides() -> None:
    """Override ``notifications.slack.webhook_url`` from env var.

    Slack incoming-webhook URLs are credentials; the recommended
    operator workflow keeps them in ``~/.devbench/shell.env`` and out
    of any tracked yaml.  The env-var value takes precedence over the
    yaml ``notifications.slack.webhook_url`` field when both are
    present.

    Empty-string values are ignored (treated as "not set"), so a
    boilerplate ``export DEVBENCH_NOTIFICATIONS_SLACK_WEBHOOK_URL=``
    in a shell-init script does not clobber a yaml-supplied value.
    """
    slack_url = _read_env("DEVBENCH_NOTIFICATIONS_SLACK_WEBHOOK_URL")
    if slack_url:
        RUNTIME_CONFIG.notifications.slack.webhook_url = slack_url


_apply_notifications_env_overrides()
NOTIFICATIONS = RUNTIME_CONFIG.notifications

# ---------------------------------------------------------------------------
# Timeouts -- all values in seconds
# ---------------------------------------------------------------------------
GH_API_TIMEOUT: int = _resolve_int("DEVBENCH_GH_API_TIMEOUT", RUNTIME_CONFIG.timeouts.gh_api, DEFAULT_GH_API_TIMEOUT)
TEST_TIMEOUT: int = _resolve_int("DEVBENCH_TEST_TIMEOUT", RUNTIME_CONFIG.timeouts.test, DEFAULT_TEST_TIMEOUT)
SECURITY_FETCH_TIMEOUT: int = _resolve_int(
    "DEVBENCH_SECURITY_FETCH_TIMEOUT",
    RUNTIME_CONFIG.timeouts.security_fetch,
    DEFAULT_SECURITY_FETCH_TIMEOUT,
)
LLM_TIMEOUT: int = _resolve_int("DEVBENCH_LLM_TIMEOUT", RUNTIME_CONFIG.timeouts.llm, DEFAULT_LLM_TIMEOUT)
COMMAND_TIMEOUT: int = _resolve_int(
    "DEVBENCH_COMMAND_TIMEOUT", RUNTIME_CONFIG.timeouts.command, DEFAULT_COMMAND_TIMEOUT
)

# ---------------------------------------------------------------------------
# Thresholds and limits
# ---------------------------------------------------------------------------
ALERT_SUMMARY_LIMIT: int = _resolve_int(
    "DEVBENCH_ALERT_SUMMARY_LIMIT",
    RUNTIME_CONFIG.limits.alert_summary,
    DEFAULT_ALERT_SUMMARY_LIMIT,
)
OUTPUT_TRUNCATION_LIMIT: int = _resolve_int(
    "DEVBENCH_OUTPUT_TRUNCATION",
    RUNTIME_CONFIG.limits.output_truncation,
    DEFAULT_OUTPUT_TRUNCATION_LIMIT,
)
LLM_EVIDENCE_TRUNCATION: int = _resolve_int(
    "DEVBENCH_LLM_EVIDENCE_TRUNCATION",
    RUNTIME_CONFIG.limits.llm_evidence_truncation,
    DEFAULT_LLM_EVIDENCE_TRUNCATION,
)

# ---------------------------------------------------------------------------
# LLM context limits
# ---------------------------------------------------------------------------
LLM_FILE_CONTEXT_LIMIT: int = _resolve_int(
    "DEVBENCH_LLM_FILE_CONTEXT_LIMIT",
    RUNTIME_CONFIG.limits.llm_file_context,
    DEFAULT_LLM_FILE_CONTEXT_LIMIT,
)
LLM_FILE_PREVIEW_CHARS: int = _resolve_int(
    "DEVBENCH_LLM_FILE_PREVIEW_CHARS",
    RUNTIME_CONFIG.limits.llm_file_preview_chars,
    DEFAULT_LLM_FILE_PREVIEW_CHARS,
)

# ---------------------------------------------------------------------------
# Orchestrator
# ---------------------------------------------------------------------------
ORCHESTRATOR_POLL_INTERVAL: int = _resolve_int(
    "DEVBENCH_ORCHESTRATOR_POLL_INTERVAL",
    RUNTIME_CONFIG.timeouts.orchestrator_poll_interval,
    DEFAULT_ORCHESTRATOR_POLL_INTERVAL,
)

# ---------------------------------------------------------------------------
# Credentials
# ---------------------------------------------------------------------------
GH_TOKEN_FILE: Path = Path(_read_env("DEVBENCH_GH_TOKEN_FILE") or str(Path.home() / ".gh_token_env"))
CLAUDE_CREDENTIALS_FILE: Path = Path(
    _read_env("DEVBENCH_CLAUDE_CREDENTIALS_FILE") or str(Path.home() / ".claude" / ".credentials.json")
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
