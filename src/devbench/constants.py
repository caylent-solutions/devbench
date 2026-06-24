"""Centralized constants for the judges system.

All structural string constants, regex patterns, markdown format definitions,
section headers, and display limits live here. Source files import from this
module instead of embedding literals inline.

Operational parameters that vary by environment (timeouts, thresholds, paths)
live in ``config.py`` with environment-variable overrides.

Session-management constants (SESSION_*) are defined in the session-management
region below and consumed by ``src/devbench/session.py``.
"""

import re

COMMENTS_SECTION_HEADER: str = "## Comments"
STATUS_SECTION_PREFIX: str = "## Status:"
STATUS_SUMMARY_SECTION_HEADER: str = "## Status Summary"
STATUS_SUMMARY_TABLE_HEADER: str = (
    "| Epic | Title | Done | In Progress | In Queue | Blocked | Declined | Draft |\n"
    "|------|-------|------|-------------|----------|---------|----------|-------|\n"
)
STRIP_SUMMARY_RE = re.compile(
    r"## Status Summary\n.*?(?=\n## |\Z)",
    re.DOTALL,
)

STATUS_LINE_RE = re.compile(r"^(##\s*Status:\s*)(.+)$", re.MULTILINE)
TABLE_ROW_RE = re.compile(r"^\|([^|]*)\|", re.MULTILINE)

BACKLOG_STATUS_RE = re.compile(r"^##\s+Status:\s*(.+)$", re.MULTILINE)
BACKLOG_REPO_RE = re.compile(r"^-\s+\*?\*?Repo:?\*?\*?\s*`([^`]+)`", re.MULTILINE)
BACKLOG_LOCAL_PATH_RE = re.compile(r"^-\s+\*?\*?Local path:?\*?\*?\s*`([^`]+)`", re.MULTILINE)
BACKLOG_BRANCH_RE = re.compile(r"^-\s+\*?\*?Branch:?\*?\*?\s*`([^`]+)`", re.MULTILINE)
BACKLOG_AC_RE = re.compile(r"^-\s+\[[^\]]*\]\s+(AC-\S+.*)", re.MULTILINE)
BACKLOG_SECTION_RE = re.compile(r"^##\s+", re.MULTILINE)
BACKLOG_DEP_TABLE_ROW_RE = re.compile(
    r"^\|\s*([^|]+?)\s*\|\s*([^|]+?)\s*\|\s*([^|]+?)\s*\|$",
    re.MULTILINE,
)
BACKLOG_INDEX_TABLE_ROW_RE = re.compile(
    r"^\|\s*([^|]+?)\s*\|\s*([^|]+?)\s*\|\s*([^|]+?)\s*\|"
    r"\s*([^|]+?)\s*\|\s*([^|]+?)\s*\|\s*([^|]+?)\s*\|\s*([^|]+?)\s*\|$",
    re.MULTILINE,
)

SECURITY_ALERT_CATEGORIES: list[tuple[str, str]] = [
    ("code-scanning", "repos/{repo}/code-scanning/alerts?state=open"),
    ("dependabot", "repos/{repo}/dependabot/alerts?state=open"),
    ("secret-scanning", "repos/{repo}/secret-scanning/alerts?state=open"),
]

DISPLAY_STATUS_VALUES: list[str] = [
    "In Queue",
    "In Progress",
    "In Review",
    "Done",
    "Blocked",
    "Proposed",
    "Declined",
    "Hold",
]

TABLE_STATUS_VALUES: frozenset[str] = frozenset(
    {"In Queue", "In Progress", "In Review", "Done", "Blocked", "Proposed", "Declined", "Hold"}
)

TRACEABILITY_MATRIX_HEADER: str = "| Spec Ref | Test Ref | Verified At |\n| --- | --- | --- |\n"

COMMENT_ENTRY_TEMPLATE: str = "[{timestamp}] [{agent_id}] [{action}] {message}\n"

BRANCH_NAME_TEMPLATE: str = "backlog/{unit_id}"
PR_BODY_TEMPLATE: str = "Automated PR for work unit {unit_id}\n\n{description}"

ERROR_OUTPUT_PREVIEW_CHARS: int = 1000
RAW_RESPONSE_PREVIEW_CHARS: int = 500

LLM_RESPONSE_FORMAT_INSTRUCTIONS: str = (
    "# Response Format\n"
    "Respond with ONLY valid JSON (no markdown fences, no preamble):\n"
    '{"verdict": "pass" or "fail", "reasoning": "...", "feedback": "...", '
    '"evidence": ["item1", "item2"]}\n'
    "- verdict: pass or fail\n"
    "- reasoning: detailed explanation of your decision\n"
    "- feedback: if fail, specific actionable instructions to fix each issue. "
    "if pass, empty string.\n"
    "- evidence: list of specific items you checked\n"
)

STATUS_SEPARATOR_WIDTH: int = 40

STATUS_SUMMARY_LABEL_WIDTH: int = 32

DEPENDENCY_NONE_VALUE: str = "none"

STATUS_IN_QUEUE: str = "in-queue"
STATUS_IN_PROGRESS: str = "in-progress"
STATUS_IN_REVIEW: str = "in-review"
STATUS_DONE: str = "done"
STATUS_BLOCKED: str = "blocked"
STATUS_PROPOSED: str = "proposed"
STATUS_DECLINED: str = "declined"
STATUS_HOLD: str = "hold"
STATUS_DRAFT: str = "draft"

VALID_STATUSES: dict[str, str] = {
    STATUS_IN_QUEUE: STATUS_IN_QUEUE,
    STATUS_IN_PROGRESS: STATUS_IN_PROGRESS,
    STATUS_IN_REVIEW: STATUS_IN_REVIEW,
    STATUS_DONE: STATUS_DONE,
    STATUS_BLOCKED: STATUS_BLOCKED,
    STATUS_PROPOSED: STATUS_PROPOSED,
    STATUS_DECLINED: STATUS_DECLINED,
    STATUS_HOLD: STATUS_HOLD,
    STATUS_DRAFT: STATUS_DRAFT,
}

EPIC_PLACEHOLDER_ID: str = "--"

BACKLOG_SUBDIR: str = "backlog"

DEPENDENCY_NONE_VALUES: frozenset[str] = frozenset(
    {
        DEPENDENCY_NONE_VALUE,
        EPIC_PLACEHOLDER_ID,
        "---",
        "",
    }
)

REVIEW_JUDGE_NAMES: frozenset[str] = frozenset(
    {
        "code_review",
        "test_review",
        "doc_review",
        "changes_manifest",
    }
)

SECURITY_JUDGE_NAMES: frozenset[str] = frozenset({"security_review"})

ALL_REQUIRED_JUDGE_NAMES: frozenset[str] = REVIEW_JUDGE_NAMES | SECURITY_JUDGE_NAMES

WORKFLOW_AGENT_JUDGE_NAMES: frozenset[str] = frozenset(
    {
        "executor",
        "blocker_resolver",
        "manifest_amender",
        "task_factory",
    }
)

OPTIONAL_JUDGE_NAMES: frozenset[str] = frozenset({"iac_review"})

KNOWN_JUDGE_NAMES: frozenset[str] = ALL_REQUIRED_JUDGE_NAMES | WORKFLOW_AGENT_JUDGE_NAMES | OPTIONAL_JUDGE_NAMES

COMMENT_AGENT_TEMPLATE: str = "[{timestamp}] [agent/{name}] {message}\n"

TDD_CYCLE_LOG_SECTION_HEADER: str = "## TDD Cycle Log"
TDD_ENTRY_TEMPLATE: str = "- [{phase}] {timestamp} -- {message}\n"
VALID_TDD_PHASES: frozenset[str] = frozenset({"RED", "GREEN", "REFACTOR"})

EPIC_ID_RE = re.compile(r"^E\d+$")

DEFAULT_MAX_RETRY_ATTEMPTS: int = 10
DEFAULT_GITHUB_CHECK_TIMEOUT_SECONDS: int = 600
DEFAULT_STOP_HOOK_MAX_BLOCKS: int = 5
DEFAULT_STOP_HOOK_WINDOW_SECONDS: int = 180
DEFAULT_STOP_HOOK_STALE_TASK_MINUTES: int = 120
DEFAULT_HOOK_TAIL_AGENT_WIDTH: int = 12
DEFAULT_HOOK_TAIL_TOOL_WIDTH: int = 8
DEFAULT_HOOK_TAIL_DESCRIPTION_MAX: int = 120
DEFAULT_HOOK_TAIL_STDOUT_PREVIEW_MAX: int = 80
DEFAULT_MAX_CASCADE_DEPTH: int = 2
DEFAULT_CHECK_REGISTRATION_RETRIES: int = 12
DEFAULT_CHECK_REGISTRATION_DELAY_SECONDS: int = 5
DEFAULT_BLOCKED_RECOVERY_WINDOW_SECONDS: int = 1800
DEFAULT_INLINE_ORPHAN_CLEANUP_ENABLED: bool = True
DEVBENCH_INLINE_CLEANUP_COMMIT_MESSAGE: str = (
    "chore(cleanup): untrack devbench-managed orphan paths and update .gitignore"
)
DEFAULT_CI_FAILURE_LOG_BYTES: int = 32768
DEFAULT_CI_FAILURE_RETRY_ENABLED: bool = True
DEFAULT_PR_REVIEW_RESOLUTION_ENABLED: bool = False
DEFAULT_PAUSE_BEFORE_MERGE: bool = False
DEFAULT_PR_REVIEW_SETTLE_SECONDS: int = 60
DEFAULT_PR_REVIEW_POLL_INTERVAL: int = 5
DEFAULT_PR_REVIEW_DECISION_BLOCKS: bool = True
DEFAULT_PR_REVIEW_AGENTS: tuple[str, ...] = ()


def _opt_float(value: float | None) -> float | None:
    """Return ``float(value)`` when ``value`` is not None, else ``None``.

    Helper for ``ModelRates.__init__``: keeps the per-multiplier "unset"
    sentinel intact (so the runtime falls back to the top-level
    ``ReportConfig`` defaults) while still coercing JSON / YAML ints to
    float for the arithmetic path.
    """
    return None if value is None else float(value)


class ModelRates:
    """Per-model cost rates (issue #223).

    The four scalar fields are the canonical Anthropic-published rates
    expressed in USD per 1M tokens (input + output) and as multipliers
    relative to ``input`` (cache read / write 5min / write 1hr). All cache
    multiplier fields are optional -- when None the per-window report falls
    back to the top-level ``ReportConfig`` defaults so operators on
    standard Anthropic pricing only need to override the two scalar rates.

    ``correction_factor`` is the per-model contract correction; defaults to
    1.0 (no correction). Computed cost is multiplied by this value AFTER
    all other factors so operators can tune individual models without
    distorting cost for other models in a multi-model run.

    The class is a plain attribute container (not a frozen dataclass) so
    ``cli.py::cmd_cost_calibrate`` can construct mutated copies via
    ``replace``-style helpers without dataclass plumbing.
    """

    __slots__ = (
        "cache_read_multiplier",
        "cache_write_1hr_multiplier",
        "cache_write_5min_multiplier",
        "correction_factor",
        "input",
        "output",
    )

    def __init__(self, **kwargs: float | None) -> None:
        allowed = {
            "input",
            "output",
            "cache_read_multiplier",
            "cache_write_5min_multiplier",
            "cache_write_1hr_multiplier",
            "correction_factor",
        }
        unknown = set(kwargs) - allowed
        if unknown:
            raise TypeError(f"ModelRates got unexpected keyword argument(s): {sorted(unknown)}")
        for required in ("input", "output"):
            if kwargs.get(required) is None:
                raise TypeError(f"ModelRates requires keyword arguments 'input' and 'output'; missing {required!r}")
        self.input = float(kwargs["input"] or 0.0)
        self.output = float(kwargs["output"] or 0.0)
        self.cache_read_multiplier = _opt_float(kwargs.get("cache_read_multiplier"))
        self.cache_write_5min_multiplier = _opt_float(kwargs.get("cache_write_5min_multiplier"))
        self.cache_write_1hr_multiplier = _opt_float(kwargs.get("cache_write_1hr_multiplier"))
        self.correction_factor = float(kwargs.get("correction_factor") or 1.0)

    def __repr__(self) -> str:
        return (
            f"ModelRates(input={self.input}, output={self.output}, "
            f"cache_read_multiplier={self.cache_read_multiplier}, "
            f"cache_write_5min_multiplier={self.cache_write_5min_multiplier}, "
            f"cache_write_1hr_multiplier={self.cache_write_1hr_multiplier}, "
            f"correction_factor={self.correction_factor})"
        )

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, ModelRates):
            return NotImplemented
        return (
            self.input == other.input
            and self.output == other.output
            and self.cache_read_multiplier == other.cache_read_multiplier
            and self.cache_write_5min_multiplier == other.cache_write_5min_multiplier
            and self.cache_write_1hr_multiplier == other.cache_write_1hr_multiplier
            and self.correction_factor == other.correction_factor
        )

    def __hash__(self) -> int:
        return hash(
            (
                self.input,
                self.output,
                self.cache_read_multiplier,
                self.cache_write_5min_multiplier,
                self.cache_write_1hr_multiplier,
                self.correction_factor,
            )
        )


DEFAULT_MODEL_RATES: dict[str, ModelRates] = {
    "claude-opus-4-8": ModelRates(input=5.0, output=25.0),
    "claude-opus-4-7": ModelRates(input=5.0, output=25.0),
    "claude-opus-4-6": ModelRates(input=5.0, output=25.0),
    "claude-opus-4-5": ModelRates(input=5.0, output=25.0),
    "claude-opus-4-1": ModelRates(input=15.0, output=75.0),
    "claude-opus-4": ModelRates(input=15.0, output=75.0),
    "claude-sonnet-4-6": ModelRates(input=3.0, output=15.0),
    "claude-sonnet-4-5": ModelRates(input=3.0, output=15.0),
    "claude-sonnet-4": ModelRates(input=3.0, output=15.0),
    "claude-haiku-4-5": ModelRates(input=1.0, output=5.0),
    "claude-haiku-3-5": ModelRates(input=0.80, output=4.0),
    "claude-haiku-3": ModelRates(input=0.25, output=1.25),
}

DEFAULT_FALLBACK_MODEL_RATES: ModelRates = ModelRates(input=5.0, output=25.0)

BLOCKED_TARGET_REPO_UNRESOLVED_MARKER: str = "[BLOCKED_TARGET_REPO_UNRESOLVED]"

EM_DASH: str = "\u2014"

MIN_PACE_SAMPLES: int = 3

DEFAULT_RECENT_PACE_TASKS: int = 10

SIDE_BY_SIDE_GAP_CHARS: int = 4

DEFAULT_REPORT_STREAM_RENDER_BUDGET_SECONDS: float = 30.0
DEFAULT_REPORT_STREAM_MAX_POLL_INTERVAL: float = 5.0
DEFAULT_REPORT_STREAM_TAIL_BYTES: int = 1_048_576

DEFAULT_GH_API_TIMEOUT: int = 30
DEFAULT_TEST_TIMEOUT: int = 3600
DEFAULT_SECURITY_FETCH_TIMEOUT: int = 120
DEFAULT_LLM_TIMEOUT: int = 300
DEFAULT_COMMAND_TIMEOUT: int = 120
DEFAULT_ORCHESTRATOR_POLL_INTERVAL: int = 10

DEFAULT_ALERT_SUMMARY_LIMIT: int = 10
DEFAULT_OUTPUT_TRUNCATION_LIMIT: int = 2000
DEFAULT_LLM_EVIDENCE_TRUNCATION: int = 15000
DEFAULT_LLM_FILE_CONTEXT_LIMIT: int = 5
DEFAULT_LLM_FILE_PREVIEW_CHARS: int = 3000

LOG_FORMAT: str = "%(asctime)s [%(name)s] %(levelname)s %(message)s"
LOG_DATE_FORMAT: str = "%Y-%m-%dT%H:%M:%SZ"
DEFAULT_LOG_LEVEL: str = "INFO"
DEFAULT_LOG_SUBDIR: str = "logs"
DEFAULT_LOG_FILENAME: str = "orchestrator.log"

COMMENT_TIMESTAMP_FORMAT: str = "%Y-%m-%d %H:%M UTC"

FINALIZE_COMMIT_TEMPLATE: str = "finalize: {branch}"
FINALIZE_PR_TITLE_TEMPLATE: str = "feat: {branch}"

DEFAULT_PLUGIN_SUBPATH: str = "plugin/devbench-orchestrate"

SUBPROCESS_ERROR_EXIT_CODE: int = 127

DEFAULT_VERIFY_AC_PYTEST_SEED: int = 0

ORCHESTRATOR_RESTART_EXIT_CODE: int = 42

CLAIM_BLOCKED_PRECLAIM: int = 44
assert CLAIM_BLOCKED_PRECLAIM == 44, "CLAIM_BLOCKED_PRECLAIM must equal 44 (spec Section 5)"

GET_DIFF_NO_ATTRIBUTABLE: int = 45

CLAIM_DEFERRED_SERIALIZED: int = 47

ORCHESTRATOR_AUTO_RESTART_AUDIT_PREFIX: str = "[ORCHESTRATOR_AUTO_RESTART] reason=runtime_degradation tasks="

DEFAULT_ORCHESTRATOR_INACTIVITY_TIMEOUT_SECONDS: float = 300.0

ORCHESTRATOR_INACTIVITY_TIMEOUT_AUDIT_PREFIX: str = "[ORCHESTRATOR_INACTIVITY_TIMEOUT]"

DEFAULT_ORCHESTRATOR_MAX_TURN_END_CONTINUATIONS: int = 5

ORCHESTRATOR_TURN_END_CONTINUATIONS_EXHAUSTED_AUDIT_PREFIX: str = "[ORCHESTRATOR_TURN_END_CONTINUATIONS_EXHAUSTED]"

ORCHESTRATOR_TURN_END_CONTINUATIONS_EXHAUSTED_EXIT_CODE: int = 43

ORCHESTRATOR_FATAL_ERROR_EXIT_CODE: int = 46
ORCHESTRATOR_FATAL_ERROR_AUDIT_PREFIX: str = "[ORCHESTRATOR_FATAL_ERROR] reason="
FATAL_SDK_ERROR_CODES: frozenset[str] = frozenset(
    {
        "model_not_found",
        "authentication_error",
        "permission_error",
        "invalid_request_error",
        "not_found_error",
    }
)

DEFAULT_MAX_PARALLEL_IN_PROGRESS: int = 1

DEFAULT_MAX_WITHIN_CLAIM_ATTEMPTS: int = 4

DEFAULT_MAX_CLAIM_WALL_CLOCK_SECONDS: float = 21600.0

DEFAULT_MAX_NO_CLAIM_ACTIVITY_SECONDS: float = 600.0

DEFAULT_WITHIN_CLAIM_CONVERGENCE_CHECK: bool = True

CLAIM_NOT_CONVERGING_MARKER: str = "[CLAIM_NOT_CONVERGING]"

CLAIM_TEARDOWN_MARKER: str = "[CLAIM_EXECUTOR_TEARDOWN]"

DEFAULT_CLAIM_TEARDOWN_CLEANUP_HOOK: str = ""

TIMEOUT_RESULT_MARKERS: tuple[str, ...] = (
    "timed out after",
    "command timed out",
)

DEFAULT_MAX_NON_CONVERGING_CLAIMS: int = 3

DEFAULT_PRESYNC_ENVIRONMENT: bool = True

DEFAULT_PRESYNC_COMMAND: tuple[str, ...] = ("uv", "sync")

DEFAULT_PRESYNC_TIMEOUT_SECONDS: int = 900

DEFAULT_MAX_QUOTA_RESUMES: int = 1000

ORCHESTRATOR_QUOTA_RESUME_AUDIT_PREFIX: str = "[ORCHESTRATOR_QUOTA_RESUME]"

ORCHESTRATOR_QUOTA_RESUMES_EXHAUSTED_AUDIT_PREFIX: str = "[ORCHESTRATOR_QUOTA_RESUMES_EXHAUSTED]"

TOKENS_PER_MILLION: int = 1_000_000

DEFAULT_BEDROCK_REGION: str = "us-east-1"

CODEQL_STATE: str = "configured"
CODEQL_QUERY_SUITE: str = "default"
SECURITY_FEATURE_ENABLED: str = "enabled"

DEFAULT_REPORT_WATCH_INTERVAL: int = 3

DEFAULT_SESSION_GAP_MINUTES: int = 30

LOG_NOISE_LOGGER_NAME: str = "judges.log_setup"

DEFAULT_CACHE_READ_MULTIPLIER: float = 0.10
DEFAULT_CACHE_WRITE_5MIN_MULTIPLIER: float = 1.25
DEFAULT_CACHE_WRITE_1HR_MULTIPLIER: float = 2.0
DEFAULT_DATA_RESIDENCY_MULTIPLIER: float = 1.10
DEFAULT_FAST_MODE_MULTIPLIER: float = 6.0

REPORT_METRIC_COLUMN_WIDTH: int = 60
REPORT_VALUE_COLUMN_WIDTH: int = 16

MS_PER_SECOND: int = 1000
SECONDS_PER_MINUTE: int = 60
SECONDS_PER_HOUR: int = 3600
PERCENT_MULTIPLIER: int = 100

BACKLOG_INDEX_CELL_COUNT: int = 9

PLUGIN_SHADOW_DIR_NAME: str = ".devbench/plugin-shadow"

SHADOW_PID_SENTINEL_FILENAME: str = ".pid"

SHADOW_OVERRIDES_FINGERPRINT_FILENAME: str = ".overrides-fingerprint"

ALLOWED_AGENT_MODEL_SHORT_NAMES: frozenset[str] = frozenset({"opus", "sonnet"})

ANTHROPIC_AGENT_MODEL_PATTERN: re.Pattern[str] = re.compile(r"^claude-[a-z0-9]+(-[a-z0-9]+)+$")

BEDROCK_AGENT_MODEL_PATTERN: re.Pattern[str] = re.compile(r"^us\.anthropic\.claude-[a-z0-9-]+-v[0-9]+$")

SESSION_SESSIONS_BASE_DIR: str = ".devbench/sessions"

SESSION_REGISTRY_PATH: str = ".devbench/sessions/registry.json"

SESSION_BACKLOG_LOCK_NAME: str = "BACKLOG.lock"

SESSION_DEFAULT_FLOCK_TIMEOUT_SECONDS: int = 30

SESSION_PID_FILENAME: str = "pid"

SESSION_REGISTRY_TMP_SUFFIX: str = ".tmp"

SESSION_DEFAULT_NAME: str = "default"

SESSION_STARTED_AT_FILENAME: str = "started_at"

SESSION_STARTED_BY_FILENAME: str = "started_by"

SESSION_FLOCK_POLL_INTERVAL_SECONDS: float = 0.1

SESSION_DRAIN_SIGNAL_FILENAME: str = "drain.signal"

REQUEUED_AFTER_DEAD_SESSION_AUDIT_PREFIX: str = "[REQUEUED_AFTER_DEAD_SESSION] session="

LAST_RESTART_MARKER_PATH: str = ".devbench/last-restart"


SKILL_MAX_ITERATIONS: int = 5

SKILL_QUALITY_THRESHOLD: int = 0

SKILL_STATE_DIR_NAME: str = ".devbench/skill-state"


SUPERVISE_BASE_DIR: str = ".devbench/supervise"

SUPERVISE_REGISTRY_PATH: str = ".devbench/supervise/registry.json"

SUPERVISE_STATE_FILENAME: str = "state.json"

SUPERVISE_PTY_LOG_FILENAME: str = "pty.log"

SUPERVISE_STOP_REQUEST_FILENAME: str = "stop.request"

SUPERVISE_SUPERVISOR_LOG_FILENAME: str = "supervisor.log"

SUPERVISE_REGISTRY_TMP_SUFFIX: str = ".tmp"

SUPERVISE_REGISTRY_LOCK_SUFFIX: str = ".lock"
SUPERVISE_REGISTRY_LOCK_TIMEOUT_SECONDS: int = 30
SUPERVISE_REGISTRY_LOCK_POLL_SECONDS: float = 0.05

SUPERVISE_SCREEN_NAME_PREFIX_DEFAULT: str = "devbench-supervise-"

SUPERVISE_DEFAULT_NAME: str = "default"

SUPERVISE_EFFORT_DEFAULT: str = "xhigh"

SUPERVISE_VALID_EFFORT_LEVELS: frozenset[str] = frozenset({"low", "medium", "high", "xhigh", "max"})

SUPERVISE_VALID_RESUME_MODES: frozenset[str] = frozenset({"continue", "resume"})

SUPERVISE_BILLING_MODE_SUBSCRIPTION: str = "subscription"
SUPERVISE_BILLING_MODE_BEDROCK: str = "bedrock"
SUPERVISE_VALID_BILLING_MODES: frozenset[str] = frozenset(
    {SUPERVISE_BILLING_MODE_SUBSCRIPTION, SUPERVISE_BILLING_MODE_BEDROCK}
)
SUPERVISE_DEFAULT_BILLING_MODE: str = SUPERVISE_BILLING_MODE_SUBSCRIPTION
SUPERVISE_BILLING_MODE_ENV_VAR: str = "DEVBENCH_SUPERVISE_BILLING_MODE"
SUPERVISE_PROGRESS_STALL_SECONDS_ENV_VAR: str = "DEVBENCH_SUPERVISE_PROGRESS_STALL_SECONDS"
SUPERVISE_CLI_HANG_GUARD_ENV_VARS: dict[str, str] = {
    "DISABLE_AUTOUPDATER": "1",
    "CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC": "1",
}

SUPERVISE_BILLING_CHANNEL: str = SUPERVISE_BILLING_MODE_SUBSCRIPTION

SUPERVISE_BEDROCK_USE_FLAG_VAR: str = "CLAUDE_CODE_USE_BEDROCK"
SUPERVISE_BEDROCK_VERTEX_FLAG_VAR: str = "CLAUDE_CODE_USE_VERTEX"
SUPERVISE_BEDROCK_BASE_URL_VAR: str = "ANTHROPIC_BEDROCK_BASE_URL"
SUPERVISE_BEDROCK_MODEL_VAR: str = "ANTHROPIC_MODEL"
SUPERVISE_BEDROCK_SMALL_FAST_MODEL_VAR: str = "ANTHROPIC_SMALL_FAST_MODEL"
SUPERVISE_BEDROCK_BEARER_TOKEN_VAR: str = "AWS_BEARER_TOKEN_BEDROCK"
SUPERVISE_BEDROCK_REGION_VAR: str = "AWS_REGION"
SUPERVISE_BEDROCK_DEFAULT_REGION_VAR: str = "AWS_DEFAULT_REGION"

SUPERVISE_AWS_PASSTHROUGH_ENV_VARS: tuple[str, ...] = (
    "AWS_ACCESS_KEY_ID",
    "AWS_SECRET_ACCESS_KEY",
    "AWS_SESSION_TOKEN",
    "AWS_PROFILE",
    SUPERVISE_BEDROCK_REGION_VAR,
    SUPERVISE_BEDROCK_DEFAULT_REGION_VAR,
)

SUPERVISE_BASE_DENY_ENV_VARS: tuple[str, ...] = (
    "ANTHROPIC_API_KEY",
    "ANTHROPIC_AUTH_TOKEN",
    "ANTHROPIC_API_URL",
    "ANTHROPIC_BASE_URL",
)

SUPERVISE_SUBSCRIPTION_EXTRA_DENY_ENV_VARS: tuple[str, ...] = (
    "DEVBENCH_USE_BEDROCK",
    SUPERVISE_BEDROCK_USE_FLAG_VAR,
    SUPERVISE_BEDROCK_VERTEX_FLAG_VAR,
    SUPERVISE_BEDROCK_BASE_URL_VAR,
    SUPERVISE_BEDROCK_MODEL_VAR,
    SUPERVISE_BEDROCK_SMALL_FAST_MODEL_VAR,
    SUPERVISE_BEDROCK_BEARER_TOKEN_VAR,
)

SUPERVISE_ALWAYS_DENY_ENV_VARS: tuple[str, ...] = (
    *SUPERVISE_BASE_DENY_ENV_VARS,
    *SUPERVISE_SUBSCRIPTION_EXTRA_DENY_ENV_VARS,
)


def resolve_supervise_deny_vars(billing_mode: str) -> tuple[str, ...]:
    """Return the env-var deny tuple for *billing_mode* (Section 3.6.1, FR-21).

    DRY single source of the mode-resolved deny set (design point 5: one helper,
    not copy-paste). Both modes strip the direct-Anthropic-API base set;
    subscription mode additionally strips the Bedrock/Vertex routing vars so
    inference cannot route off-subscription. AWS workload creds + region are in
    NEITHER mode's deny set.

    Args:
        billing_mode: One of :data:`SUPERVISE_VALID_BILLING_MODES`.

    Returns:
        The ordered tuple of env-var names to strip for *billing_mode*.

    Raises:
        ValueError: *billing_mode* is not a recognized mode (fail-fast).
    """
    if billing_mode == SUPERVISE_BILLING_MODE_SUBSCRIPTION:
        return SUPERVISE_ALWAYS_DENY_ENV_VARS
    if billing_mode == SUPERVISE_BILLING_MODE_BEDROCK:
        return SUPERVISE_BASE_DENY_ENV_VARS
    valid = ", ".join(sorted(SUPERVISE_VALID_BILLING_MODES))
    raise ValueError(f"supervise billing_mode {billing_mode!r} is not one of [{valid}].")


SUPERVISE_TIMEOUT_READY_PROMPT_SECONDS_DEFAULT: int = 120
SUPERVISE_TIMEOUT_IDLE_SECONDS_DEFAULT: int = 1800
SUPERVISE_TIMEOUT_COMMAND_ACK_SECONDS_DEFAULT: int = 60
SUPERVISE_TIMEOUT_GRACEFUL_STOP_SECONDS_DEFAULT: int = 900
SUPERVISE_TIMEOUT_POLL_INTERVAL_SECONDS_DEFAULT: int = 2
SUPERVISE_TIMEOUT_COMMAND_INVOCATION_SECONDS_DEFAULT: int = 30
SUPERVISE_TIMEOUT_COMMAND_SUBMIT_QUIET_SECONDS_DEFAULT: int = 1
SUPERVISE_TIMEOUT_COMMAND_SUBMIT_SETTLE_SECONDS_DEFAULT: int = 8
SUPERVISE_TIMEOUT_PROGRESS_STALL_SECONDS_DEFAULT: int = 600
SUPERVISE_LONG_OP_HEARTBEAT_SECONDS_DEFAULT: int = 60
SUPERVISE_LONG_OP_HEARTBEAT_MARKER: str = "[LONG_OP_HEARTBEAT]"

SUPERVISE_RESTART_MAX_ATTEMPTS_DEFAULT: int = 5

SUPERVISE_RESUME_MODE_DEFAULT: str = "continue"

SUPERVISE_DETECTION_PATTERNS_DEFAULT: dict[str, str] = {
    "ready_prompt": "(?m)\u276f|^\\s*│?\\s*>(?:\\s|$)",
    "working_prompt": r"(?i)(esc to interrupt|tokens|thinking)",
    "idle_input_prompt": r"(?i)(how would you like to proceed|what would you like to do|awaiting your input)",
    "quota_limit": "(?i)(You(\u2019ve|'ve| have) hit your limit|rate.?limit.*(exceeded|reached|resets))",
    "quota_wait_prompt": r"(?i)(wait.*reset|retry.*later|press.*to wait)",
    "reset_at": r"resets\s+(\d{1,2}):(\d{2})(am|pm)\s+\(UTC\)",
    "circuit_breaker": r"\[CIRCUIT_BREAKER\]|cascade depth exceeded",
    "harness_block": r"\[HARNESS_INTEGRITY\]",
    "crash": r"(?i)(panic|fatal error|traceback \(most recent call last\))",
}

SUPERVISE_LOG_TAIL_ORCHESTRATOR_LOG_RELPATH_DEFAULT: str = "logs/orchestrator.log"
SUPERVISE_LOG_TAIL_MARKERS_CLEAN_DEFAULT: tuple[str, ...] = (
    "ALL_DONE",
    "NO_ACTIONABLE",
    "[ORCHESTRATOR_TERMINAL_EXIT]",
)
SUPERVISE_LOG_TAIL_MARKERS_QUOTA_DEFAULT: tuple[str, ...] = (
    "[QUOTA_WAITING]",
    "[QUOTA_POLLING]",
    "[ORCHESTRATOR_QUOTA_RESUME]",
)
SUPERVISE_LOG_TAIL_MARKERS_FAULT_DEFAULT: tuple[str, ...] = (
    "[ORCHESTRATOR_STOP_REASON]",
    "[ORCHESTRATOR_FATAL_ERROR]",
    "[HARNESS_INTEGRITY]",
)
SUPERVISE_LOG_TAIL_MARKERS_RESTART_DEFAULT: tuple[str, ...] = ("[ORCHESTRATOR_AUTO_RESTART]",)

SUPERVISE_LOG_PTY_LOG_RELPATH_DEFAULT: str = "pty.log"
SUPERVISE_LOG_REDACT_PATTERNS_DEFAULT: tuple[str, ...] = (
    r"sk-ant-[A-Za-z0-9_-]+",
    r"AKIA[0-9A-Z]{16}",
    r"(?i)aws_secret[^\s]*",
    r"Bearer\s+[A-Za-z0-9._-]+",
)

SUPERVISE_INJECTABLE_COMMANDS_DEFAULT: dict[str, str] = {
    "orchestrate": "/devbench-orchestrate:orchestrate",
    "effort_xhigh": "/effort xhigh",
    "model_opus": "/model opus",
    "quota_wait_choice": "1",
    "drain_now": "/exit",
    "loop_continuation": "/devbench-orchestrate:orchestrate",
}

SUPERVISE_STATE_STARTING: str = "starting"
SUPERVISE_STATE_RUNNING: str = "running"
SUPERVISE_STATE_QUOTA_WAITING: str = "quota-waiting"
SUPERVISE_STATE_QUOTA_RESUMED: str = "quota-resumed"
SUPERVISE_STATE_DRAINING: str = "draining"
SUPERVISE_STATE_COMPLETED_CLEAN: str = "completed-clean"
SUPERVISE_STATE_FAULTED: str = "faulted"
SUPERVISE_STATE_RESTARTING: str = "restarting"
SUPERVISE_STATE_STOPPED: str = "stopped"
SUPERVISE_VALID_STATES: frozenset[str] = frozenset(
    {
        SUPERVISE_STATE_STARTING,
        SUPERVISE_STATE_RUNNING,
        SUPERVISE_STATE_QUOTA_WAITING,
        SUPERVISE_STATE_QUOTA_RESUMED,
        SUPERVISE_STATE_DRAINING,
        SUPERVISE_STATE_COMPLETED_CLEAN,
        SUPERVISE_STATE_FAULTED,
        SUPERVISE_STATE_RESTARTING,
        SUPERVISE_STATE_STOPPED,
    }
)

SUPERVISE_INFO_STATE_UNKNOWN: str = "unknown"
SUPERVISE_INFO_STATE_STALE: str = "stale"

SUPERVISE_EXIT_REASON_GRACEFUL_STOP: str = "graceful-stop"
SUPERVISE_EXIT_REASON_HARD_STOP: str = "hard-stop"
SUPERVISE_EXIT_REASON_STALE_RECONCILED: str = "stale-screen-reconciled"

SUPERVISE_SUBVERBS: tuple[str, ...] = ("start", "stop", "restart", "status", "info", "attach")
SUPERVISE_INTERNAL_RUN_SUBVERB: str = "__run"

SUPERVISE_SESSION_NAME_PATTERN: str = r"^[A-Za-z0-9][A-Za-z0-9_-]*$"

SUPERVISE_FAULT_EXIT_CODE: int = ORCHESTRATOR_FATAL_ERROR_EXIT_CODE

SUPERVISE_STATE_AUDIT_PREFIX: str = "[SUPERVISE_STATE]"
SUPERVISE_FAULT_AUDIT_PREFIX: str = "[SUPERVISE_FAULT]"
SUPERVISE_RESTART_AUDIT_PREFIX: str = "[SUPERVISE_RESTART]"

SKILL_AUDIT_MAX_ITERATIONS_REACHED: str = "[SKILL_MAX_ITERATIONS_REACHED]"

SKILL_AUDIT_QUALITY_THRESHOLD_REACHED: str = "[SKILL_QUALITY_THRESHOLD_REACHED]"

RECOVERY_PROBE_MODEL: str = "claude-opus-4-8"

QUOTA_HANDLING_DEFAULT_ENABLED: bool = True

DEVBENCH_SKILLS_USE_WORKFLOW_ENV: str = "DEVBENCH_SKILLS_USE_WORKFLOW"

DEVBENCH_SKILLS_WORKFLOW_CHUNK_SIZE_ENV: str = "DEVBENCH_SKILLS_WORKFLOW_CHUNK_SIZE"

DEVBENCH_SKILLS_ADVERSARIAL_REVIEW_THRESHOLD_ENV: str = "DEVBENCH_SKILLS_ADVERSARIAL_REVIEW_THRESHOLD"

DEFAULT_SKILLS_USE_WORKFLOW: bool = False

DEFAULT_SKILLS_WORKFLOW_CHUNK_SIZE: int = 3

DEFAULT_SKILLS_ADVERSARIAL_REVIEW_THRESHOLD: int = 10

DEVBENCH_AUTO_RESOLVE_ENABLED_ENV: str = "DEVBENCH_AUTO_RESOLVE_ENABLED"

DEVBENCH_AUTO_RESOLVE_MAX_ATTEMPTS_ENV: str = "DEVBENCH_AUTO_RESOLVE_MAX_ATTEMPTS"

DEFAULT_AUTO_RESOLVE_ENABLED: bool = False

DEFAULT_AUTO_RESOLVE_MAX_ATTEMPTS: int = 3

AUTO_RESOLVE_AUDIT_STRING: str = "[AUTO_RESOLVED]"

AUTO_RESOLVE_ESCALATED_STRING: str = "[AUTO_RESOLVE_ESCALATED]"

AUTO_RESOLVE_WHITELIST: frozenset[str] = frozenset(
    {
        "re-queue",
        "set-status in-queue",
        "reconcile-cascade",
        "restart-signal",
    }
)

AUTO_RESOLVE_DESTRUCTIVE_VERBS: frozenset[str] = frozenset(
    {
        "decline",
        "mark-done",
        "force-status",
    }
)

CANONICAL_VERDICT_RE: re.Pattern[str] = re.compile(
    r"^\[\d{4}-\d{2}-\d{2} \d{2}:\d{2} UTC\] \[judge/(?P<judge>[^\]]+)\] \[(?P<action>[^\]]+)\]"
)
