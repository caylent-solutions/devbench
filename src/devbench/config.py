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
    AutoResolveConfig,
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
    DEFAULT_AUTO_RESOLVE_ENABLED,
    DEFAULT_AUTO_RESOLVE_MAX_ATTEMPTS,
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
    DEFAULT_ORCHESTRATOR_INACTIVITY_TIMEOUT_SECONDS,
    DEFAULT_ORCHESTRATOR_POLL_INTERVAL,
    DEFAULT_OUTPUT_TRUNCATION_LIMIT,
    DEFAULT_PAUSE_BEFORE_MERGE,
    DEFAULT_PR_REVIEW_AGENTS,
    DEFAULT_PR_REVIEW_DECISION_BLOCKS,
    DEFAULT_PR_REVIEW_POLL_INTERVAL,
    DEFAULT_PR_REVIEW_RESOLUTION_ENABLED,
    DEFAULT_PR_REVIEW_SETTLE_SECONDS,
    DEFAULT_RECENT_PACE_TASKS,
    DEFAULT_REPORT_STREAM_MAX_POLL_INTERVAL,
    DEFAULT_REPORT_STREAM_RENDER_BUDGET_SECONDS,
    DEFAULT_REPORT_STREAM_TAIL_BYTES,
    DEFAULT_SECURITY_FETCH_TIMEOUT,
    DEFAULT_SKILLS_ADVERSARIAL_REVIEW_THRESHOLD,
    DEFAULT_SKILLS_USE_WORKFLOW,
    DEFAULT_SKILLS_WORKFLOW_CHUNK_SIZE,
    DEFAULT_STOP_HOOK_MAX_BLOCKS,
    DEFAULT_STOP_HOOK_STALE_TASK_MINUTES,
    DEFAULT_STOP_HOOK_WINDOW_SECONDS,
    DEFAULT_TEST_TIMEOUT,
    DEFAULT_VERIFY_AC_PYTEST_SEED,
    DEVBENCH_AUTO_RESOLVE_ENABLED_ENV,
    DEVBENCH_AUTO_RESOLVE_MAX_ATTEMPTS_ENV,
    DEVBENCH_SKILLS_ADVERSARIAL_REVIEW_THRESHOLD_ENV,
    DEVBENCH_SKILLS_USE_WORKFLOW_ENV,
    DEVBENCH_SKILLS_WORKFLOW_CHUNK_SIZE_ENV,
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
        print(f"devbench: {name} environment variable is not set. {hint}", file=sys.stderr)
        sys.exit(2)
    return value


WORKSPACE_ROOT: Path = Path(
    _require_env(
        "DEVBENCH_WORKSPACE_ROOT",
        "Set it to the absolute path of your workspace root.",
    )
)

_config_path: Path = resolve_config_path(None, os.environ, WORKSPACE_ROOT)
RUNTIME_CONFIG: RuntimeConfig = load_runtime_config(_config_path, os.environ)

ALLOWED_REPOS: frozenset[str] = frozenset(RUNTIME_CONFIG.repos)

REPO_LOCAL_PATHS: dict[str, Path] = {
    repo: get_repo_local_path(repo, RUNTIME_CONFIG, WORKSPACE_ROOT) for repo in ALLOWED_REPOS
}

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


BACKLOG_ROOT: Path = WORKSPACE_ROOT / BACKLOG_SUBDIR
BACKLOG_INDEX: Path = WORKSPACE_ROOT / "BACKLOG.md"

MAX_RETRY_ATTEMPTS: int = _resolve_int(
    "DEVBENCH_MAX_RETRIES", RUNTIME_CONFIG.max_executor_retries, DEFAULT_MAX_RETRY_ATTEMPTS
)
GITHUB_CHECK_TIMEOUT_SECONDS: int = _resolve_int(
    "DEVBENCH_GH_TIMEOUT",
    RUNTIME_CONFIG.timeouts.github_check,
    DEFAULT_GITHUB_CHECK_TIMEOUT_SECONDS,
)
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
BLOCKED_RECOVERY_WINDOW_SECONDS: int = _resolve_int(
    "DEVBENCH_BLOCKED_RECOVERY_WINDOW_SECONDS",
    RUNTIME_CONFIG.debug.blocked_recovery_window_seconds,
    DEFAULT_BLOCKED_RECOVERY_WINDOW_SECONDS,
)
INLINE_ORPHAN_CLEANUP_ENABLED: bool = _resolve_bool(
    "DEVBENCH_INLINE_ORPHAN_CLEANUP",
    RUNTIME_CONFIG.git_ops.inline_orphan_cleanup,
    DEFAULT_INLINE_ORPHAN_CLEANUP_ENABLED,
)
CI_FAILURE_LOG_BYTES: int = _resolve_int(
    "DEVBENCH_CI_FAILURE_LOG_BYTES",
    RUNTIME_CONFIG.limits.ci_failure_log_bytes,
    DEFAULT_CI_FAILURE_LOG_BYTES,
)
CI_FAILURE_RETRY_ENABLED: bool = _resolve_bool(
    "DEVBENCH_CI_FAILURE_RETRY_ENABLED",
    RUNTIME_CONFIG.git_ops.ci_failure_retry,
    DEFAULT_CI_FAILURE_RETRY_ENABLED,
)
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
REPORT_MODEL_RATES: dict[str, ModelRates] = {
    **DEFAULT_MODEL_RATES,
    **RUNTIME_CONFIG.report.models,
}
REPORT_DEFAULT_MODEL_RATES: ModelRates = RUNTIME_CONFIG.report.default_model
REPORT_DISPLAY_TIMEZONE: str | None = _resolve_optional_str(
    "DEVBENCH_REPORT_TIMEZONE", RUNTIME_CONFIG.report.display_timezone
)
DISPLAY_TIMEZONE: str | None = _resolve_optional_str("DEVBENCH_DISPLAY_TIMEZONE", RUNTIME_CONFIG.display_timezone)
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
RECENT_PACE_TASKS: int = _resolve_int(
    "DEVBENCH_REPORT_RECENT_PACE_TASKS",
    RUNTIME_CONFIG.report.recent_pace_tasks,
    DEFAULT_RECENT_PACE_TASKS,
)
REPORT_STREAM_RENDER_BUDGET_SECONDS: float = _resolve_float(
    "DEVBENCH_REPORT_STREAM_RENDER_BUDGET_SECONDS",
    None,
    DEFAULT_REPORT_STREAM_RENDER_BUDGET_SECONDS,
)
REPORT_STREAM_MAX_POLL_INTERVAL: float = _resolve_float(
    "DEVBENCH_REPORT_STREAM_MAX_POLL_INTERVAL",
    None,
    DEFAULT_REPORT_STREAM_MAX_POLL_INTERVAL,
)
REPORT_STREAM_TAIL_BYTES: int = _resolve_int(
    "DEVBENCH_REPORT_STREAM_TAIL_BYTES",
    None,
    DEFAULT_REPORT_STREAM_TAIL_BYTES,
)
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

_AGENT_MODEL_ENV_VARS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("DEVBENCH_AGENT_MODEL_EXECUTOR", ("executor",)),
    ("DEVBENCH_AGENT_MODEL_BLOCKER_RESOLVER", ("blocker_resolver",)),
    ("DEVBENCH_AGENT_MODEL_MANIFEST_AMENDER", ("manifest_amender",)),
    ("JUDGE_AGENT_MODEL_SECURITY_REVIEWER", ("security_reviewer",)),
    ("DEVBENCH_AGENT_MODEL_TASK_FACTORY", ("task_factory",)),
    ("JUDGE_AGENT_MODEL_REVIEW_SUPERVISOR", ("review_supervisor",)),
    ("JUDGE_AGENT_MODEL_IAC_DEPLOY_REVIEWER", ("iac_deploy_reviewer",)),
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


_OPTIONAL_JUDGE_ENV_VARS: tuple[tuple[str, str], ...] = (("DEVBENCH_JUDGE_IAC_REVIEW_ENABLED", "iac_review"),)


def _apply_optional_judge_env_overrides() -> None:
    """Merge ``DEVBENCH_JUDGE_<NAME>_ENABLED`` env vars over the YAML optional_judges block.

    Each env var, when set, overrides the YAML-loaded toggle for its optional
    judge. Empty-string values are ignored (treated as "not set") so a
    boilerplate ``export DEVBENCH_JUDGE_IAC_REVIEW_ENABLED=`` in a shell-init
    script does not clobber a YAML-supplied value.
    """
    for env_var, judge_name in _OPTIONAL_JUDGE_ENV_VARS:
        raw = _read_env(env_var)
        if raw is None or not raw.strip():
            continue
        RUNTIME_CONFIG.optional_judges[judge_name] = _resolve_bool(
            env_var,
            RUNTIME_CONFIG.optional_judges.get(judge_name, False),
            False,
        )


def _apply_done_gate_env_overrides() -> None:
    """Merge the done-gate env vars over the YAML done_gate block.

    ``DEVBENCH_DONE_GATE_ALLOW_DEFERRED_EVIDENCE``, when set, overrides the
    YAML-loaded ``done_gate.allow_deferred_evidence`` toggle. Empty-string
    values are ignored (treated as "not set").
    """
    raw = _read_env("DEVBENCH_DONE_GATE_ALLOW_DEFERRED_EVIDENCE")
    if raw is None or not raw.strip():
        return
    RUNTIME_CONFIG.done_gate.allow_deferred_evidence = _resolve_bool(
        "DEVBENCH_DONE_GATE_ALLOW_DEFERRED_EVIDENCE",
        RUNTIME_CONFIG.done_gate.allow_deferred_evidence,
        False,
    )


_apply_optional_judge_env_overrides()
_apply_done_gate_env_overrides()
OPTIONAL_JUDGES = RUNTIME_CONFIG.optional_judges
DONE_GATE = RUNTIME_CONFIG.done_gate


def resolve_orchestrator_stop_mention_map(yaml_map: dict[str, str] | None) -> dict[str, str]:
    """Resolve the stop-class to mention-level mapping.

    Precedence per stop class: env var > *yaml_map* value > default.

    The default (``DEFAULT_ORCHESTRATOR_STOP_MENTION_MAP`` in
    ``devbench.notifications``) is noise-reducing: attention-worthy stops
    (premature turn end, operator interrupt, crash, quota exhausted) map to
    ``"here"``; clean completion and drain map to ``"none"``.

    Env var naming: ``DEVBENCH_STOP_MENTION_<CLASS_UPPER>`` where
    ``<CLASS_UPPER>`` is the stop-class token uppercased with hyphens replaced
    by underscores (e.g. ``DEVBENCH_STOP_MENTION_PREMATURE_TURN_END``).

    Validation:
        - Unknown stop-class keys in *yaml_map* raise ``ValueError`` immediately
          (fail-fast at config-load time).
        - Invalid mention-level values -- whether from an env var or *yaml_map*
          -- raise ``ValueError`` naming the offending key and the allowed levels.

    Args:
        yaml_map: Mapping from the ``notifications.orchestrator_stop_mention_map``
            YAML block, or ``None`` when the block is absent.

    Returns:
        A complete ``dict[stop_class, mention_level]`` covering all
        ``ALL_STOP_CLASSES`` entries.

    Raises:
        ValueError: When any key in *yaml_map* is not a recognised stop class,
            or when any resolved mention level is not a recognised level.
    """
    from devbench.notifications import (
        ALL_MENTION_LEVELS,
        ALL_STOP_CLASSES,
        DEFAULT_ORCHESTRATOR_STOP_MENTION_MAP,
    )

    if yaml_map:
        for cls in yaml_map:
            if cls not in ALL_STOP_CLASSES:
                raise ValueError(
                    f"unknown stop-class key {cls!r} in orchestrator_stop_mention_map; "
                    f"allowed stop-class keys: {sorted(ALL_STOP_CLASSES)}"
                )

    resolved: dict[str, str] = {}
    for cls in ALL_STOP_CLASSES:
        env_var = f"DEVBENCH_STOP_MENTION_{cls.upper().replace('-', '_')}"
        env_val = _read_env(env_var)
        if env_val is not None:
            if env_val not in ALL_MENTION_LEVELS:
                raise ValueError(
                    f"{env_var}: invalid mention level {env_val!r}; "
                    f"allowed mention levels: {sorted(ALL_MENTION_LEVELS)}"
                )
            resolved[cls] = env_val
        elif yaml_map and cls in yaml_map:
            yaml_val = yaml_map[cls]
            if yaml_val not in ALL_MENTION_LEVELS:
                raise ValueError(
                    f"orchestrator_stop_mention_map.{cls}: invalid mention level {yaml_val!r}; "
                    f"allowed mention levels: {sorted(ALL_MENTION_LEVELS)}"
                )
            resolved[cls] = yaml_val
        else:
            resolved[cls] = DEFAULT_ORCHESTRATOR_STOP_MENTION_MAP[cls]
    return resolved


ORCHESTRATOR_STOP_MENTION_MAP: dict[str, str] = resolve_orchestrator_stop_mention_map(None)


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

VERIFY_AC_PYTEST_SEED: int = _resolve_int("DEVBENCH_VERIFY_AC_PYTEST_SEED", None, DEFAULT_VERIFY_AC_PYTEST_SEED)

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

ORCHESTRATOR_POLL_INTERVAL: int = _resolve_int(
    "DEVBENCH_ORCHESTRATOR_POLL_INTERVAL",
    RUNTIME_CONFIG.timeouts.orchestrator_poll_interval,
    DEFAULT_ORCHESTRATOR_POLL_INTERVAL,
)

ORCHESTRATOR_INACTIVITY_TIMEOUT_SECONDS: float = _resolve_float(
    "DEVBENCH_ORCHESTRATOR_INACTIVITY_TIMEOUT_SECONDS",
    RUNTIME_CONFIG.timeouts.orchestrator_inactivity_timeout,
    DEFAULT_ORCHESTRATOR_INACTIVITY_TIMEOUT_SECONDS,
)

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


AUTO_RESOLVE_ENABLED: bool = _resolve_bool(
    DEVBENCH_AUTO_RESOLVE_ENABLED_ENV,
    RUNTIME_CONFIG.auto_resolve.enabled,
    DEFAULT_AUTO_RESOLVE_ENABLED,
)
AUTO_RESOLVE_MAX_ATTEMPTS: int = _resolve_int(
    DEVBENCH_AUTO_RESOLVE_MAX_ATTEMPTS_ENV,
    RUNTIME_CONFIG.auto_resolve.max_attempts,
    DEFAULT_AUTO_RESOLVE_MAX_ATTEMPTS,
)
AUTO_RESOLVE_CONFIG: AutoResolveConfig = AutoResolveConfig(
    enabled=AUTO_RESOLVE_ENABLED,
    max_attempts=AUTO_RESOLVE_MAX_ATTEMPTS,
)

SKILLS_USE_WORKFLOW: bool = _resolve_bool(
    DEVBENCH_SKILLS_USE_WORKFLOW_ENV,
    RUNTIME_CONFIG.skills.use_workflow,
    DEFAULT_SKILLS_USE_WORKFLOW,
)
SKILLS_WORKFLOW_CHUNK_SIZE: int = _resolve_int(
    DEVBENCH_SKILLS_WORKFLOW_CHUNK_SIZE_ENV,
    RUNTIME_CONFIG.skills.workflow_chunk_size,
    DEFAULT_SKILLS_WORKFLOW_CHUNK_SIZE,
)
SKILLS_ADVERSARIAL_REVIEW_THRESHOLD: int = _resolve_int(
    DEVBENCH_SKILLS_ADVERSARIAL_REVIEW_THRESHOLD_ENV,
    RUNTIME_CONFIG.skills.adversarial_review_threshold,
    DEFAULT_SKILLS_ADVERSARIAL_REVIEW_THRESHOLD,
)


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
