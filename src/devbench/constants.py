"""Centralized constants for the judges system.

All structural string constants, regex patterns, markdown format definitions,
section headers, and display limits live here. Source files import from this
module instead of embedding literals inline.

Operational parameters that vary by environment (timeouts, thresholds, paths)
live in ``config.py`` with environment-variable overrides.
"""

import re

# ---------------------------------------------------------------------------
# Markdown section headers
# ---------------------------------------------------------------------------
COMMENTS_SECTION_HEADER: str = "## Comments"
STATUS_SECTION_PREFIX: str = "## Status:"
STATUS_SUMMARY_SECTION_HEADER: str = "## Status Summary"
STATUS_SUMMARY_TABLE_HEADER: str = (
    "| Epic | Title | Done | In Progress | In Queue | Blocked |\n"
    "|------|-------|------|-------------|----------|---------|\n"
)
# Pre-compiled pattern to strip the Status Summary section from BACKLOG.md content.
# Matches from the header up to (but not including) the next ## heading or end of string.
STRIP_SUMMARY_RE = re.compile(
    r"## Status Summary\n.*?(?=\n## |\Z)",
    re.DOTALL,
)

# ---------------------------------------------------------------------------
# Work-unit markdown format patterns
# ---------------------------------------------------------------------------
STATUS_LINE_RE = re.compile(r"^(##\s*Status:\s*)(.+)$", re.MULTILINE)
TABLE_ROW_RE = re.compile(r"^\|([^|]*)\|", re.MULTILINE)

# Backlog parser patterns
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

# ---------------------------------------------------------------------------
# Security review patterns
# ---------------------------------------------------------------------------
SECURITY_ALERT_CATEGORIES: list[tuple[str, str]] = [
    ("code-scanning", "repos/{repo}/code-scanning/alerts?state=open"),
    ("dependabot", "repos/{repo}/dependabot/alerts?state=open"),
    ("secret-scanning", "repos/{repo}/secret-scanning/alerts?state=open"),
]

# ---------------------------------------------------------------------------
# Backlog status display values (used in CLI status summaries)
# ---------------------------------------------------------------------------
DISPLAY_STATUS_VALUES: list[str] = ["In Queue", "In Progress", "In Review", "Done", "Blocked"]

# Backlog manager recognized status labels (title-case, as in markdown tables)
TABLE_STATUS_VALUES: frozenset[str] = frozenset({"In Queue", "In Progress", "In Review", "Done", "Blocked"})

# ---------------------------------------------------------------------------
# Traceability matrix format
# ---------------------------------------------------------------------------
TRACEABILITY_MATRIX_HEADER: str = "| Spec Ref | Test Ref | Verified At |\n| --- | --- | --- |\n"

# ---------------------------------------------------------------------------
# Comment entry format template
# ---------------------------------------------------------------------------
COMMENT_ENTRY_TEMPLATE: str = "[{timestamp}] [{agent_id}] [{action}] {message}\n"

# ---------------------------------------------------------------------------
# Orchestrator templates
# ---------------------------------------------------------------------------
BRANCH_NAME_TEMPLATE: str = "backlog/{unit_id}"
PR_BODY_TEMPLATE: str = "Automated PR for work unit {unit_id}\n\n{description}"

# ---------------------------------------------------------------------------
# Display / preview truncation limits
# ---------------------------------------------------------------------------
ERROR_OUTPUT_PREVIEW_CHARS: int = 1000
RAW_RESPONSE_PREVIEW_CHARS: int = 500

# ---------------------------------------------------------------------------
# LLM response format prompt
# ---------------------------------------------------------------------------
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

# ---------------------------------------------------------------------------
# Separator used in status display
# ---------------------------------------------------------------------------
STATUS_SEPARATOR_WIDTH: int = 40

# ---------------------------------------------------------------------------
# Dependency "none" sentinel
# ---------------------------------------------------------------------------
DEPENDENCY_NONE_VALUE: str = "none"

# ---------------------------------------------------------------------------
# Work-unit lifecycle status strings (canonical lowercase-hyphenated form).
# These are the values written into work-unit files and BACKLOG.md rows.
# The display / title-case variants live on WorkUnitStatus in work_unit.py.
# ---------------------------------------------------------------------------
STATUS_IN_QUEUE: str = "in-queue"
STATUS_IN_PROGRESS: str = "in-progress"
STATUS_IN_REVIEW: str = "in-review"
STATUS_DONE: str = "done"
STATUS_BLOCKED: str = "blocked"

# Ordered mapping from any accepted input form to the canonical write form.
# Used by BacklogManager._set_status() for validation and normalisation.
VALID_STATUSES: dict[str, str] = {
    STATUS_IN_QUEUE: STATUS_IN_QUEUE,
    STATUS_IN_PROGRESS: STATUS_IN_PROGRESS,
    STATUS_IN_REVIEW: STATUS_IN_REVIEW,
    STATUS_DONE: STATUS_DONE,
    STATUS_BLOCKED: STATUS_BLOCKED,
}

# ---------------------------------------------------------------------------
# Epic placeholder ID
# ---------------------------------------------------------------------------
EPIC_PLACEHOLDER_ID: str = "--"

# ---------------------------------------------------------------------------
# Subdirectory name under the backlog root where work-unit files live.
# ---------------------------------------------------------------------------
BACKLOG_SUBDIR: str = "backlog"

# All values that mean "no dependency" in the BACKLOG.md dependencies column.
# Includes DEPENDENCY_NONE_VALUE, EPIC_PLACEHOLDER_ID, the separator used in
# some tables, and empty string.
DEPENDENCY_NONE_VALUES: frozenset[str] = frozenset(
    {
        DEPENDENCY_NONE_VALUE,  # "none"
        EPIC_PLACEHOLDER_ID,  # "--"
        "---",
        "",
    }
)

# ---------------------------------------------------------------------------
# Required review judge names for the done-gate check (4.1)
# ---------------------------------------------------------------------------
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

# ---------------------------------------------------------------------------
# Non-verdict agent comment format template
# ---------------------------------------------------------------------------
COMMENT_AGENT_TEMPLATE: str = "[{timestamp}] [agent/{name}] {message}\n"

# ---------------------------------------------------------------------------
# TDD Cycle Log section header and entry format template
# ---------------------------------------------------------------------------
TDD_CYCLE_LOG_SECTION_HEADER: str = "## TDD Cycle Log"
TDD_ENTRY_TEMPLATE: str = "- [{phase}] {timestamp} \u2014 {message}\n"
VALID_TDD_PHASES: frozenset[str] = frozenset({"RED", "GREEN", "REFACTOR"})

# ---------------------------------------------------------------------------
# Epic ID regex — matches top-level epic IDs such as "E200", "E1", etc.
# A row is an epic row when its ID is exactly E<digits> with no hyphen suffix.
# ---------------------------------------------------------------------------
EPIC_ID_RE = re.compile(r"^E\d+$")

# ---------------------------------------------------------------------------
# Operational parameter defaults
# ---------------------------------------------------------------------------
DEFAULT_MAX_RETRY_ATTEMPTS: int = 10
DEFAULT_GITHUB_CHECK_TIMEOUT_SECONDS: int = 600
DEFAULT_STOP_HOOK_MAX_BLOCKS: int = 5
DEFAULT_STOP_HOOK_WINDOW_SECONDS: int = 180
DEFAULT_STOP_HOOK_STALE_TASK_MINUTES: int = 120
DEFAULT_TOKEN_COST_PER_M_INPUT: float = 15.0
DEFAULT_TOKEN_COST_PER_M_OUTPUT: float = 75.0
DEFAULT_TOKEN_COST_INPUT_RATIO: float = 0.80

# Timeout defaults (seconds)
DEFAULT_GH_API_TIMEOUT: int = 30
DEFAULT_TEST_TIMEOUT: int = 300
DEFAULT_SECURITY_FETCH_TIMEOUT: int = 120
DEFAULT_LLM_TIMEOUT: int = 300
DEFAULT_COMMAND_TIMEOUT: int = 120
DEFAULT_ORCHESTRATOR_POLL_INTERVAL: int = 10

# Threshold / limit defaults
DEFAULT_ALERT_SUMMARY_LIMIT: int = 10
DEFAULT_OUTPUT_TRUNCATION_LIMIT: int = 2000
DEFAULT_LLM_EVIDENCE_TRUNCATION: int = 15000
DEFAULT_LLM_FILE_CONTEXT_LIMIT: int = 5
DEFAULT_LLM_FILE_PREVIEW_CHARS: int = 3000

# ---------------------------------------------------------------------------
# Logging defaults
# ---------------------------------------------------------------------------
LOG_FORMAT: str = "%(asctime)s [%(name)s] %(levelname)s %(message)s"
LOG_DATE_FORMAT: str = "%Y-%m-%dT%H:%M:%SZ"
DEFAULT_LOG_LEVEL: str = "INFO"
DEFAULT_LOG_SUBDIR: str = "logs"
DEFAULT_LOG_FILENAME: str = "orchestrator.log"

# ---------------------------------------------------------------------------
# Comment timestamp format (used in work-unit Comments entries)
# ---------------------------------------------------------------------------
COMMENT_TIMESTAMP_FORMAT: str = "%Y-%m-%d %H:%M UTC"

# ---------------------------------------------------------------------------
# Git message templates for finalize operations
# ---------------------------------------------------------------------------
FINALIZE_COMMIT_TEMPLATE: str = "finalize: {branch}"
FINALIZE_PR_TITLE_TEMPLATE: str = "feat: {branch}"

# ---------------------------------------------------------------------------
# Plugin path (relative to package root)
# ---------------------------------------------------------------------------
DEFAULT_PLUGIN_SUBPATH: str = "plugin/devbench"

# ---------------------------------------------------------------------------
# Subprocess error exit code (Unix convention for command-not-found / timeout)
# ---------------------------------------------------------------------------
SUBPROCESS_ERROR_EXIT_CODE: int = 127

# ---------------------------------------------------------------------------
# Token arithmetic
# ---------------------------------------------------------------------------
TOKENS_PER_MILLION: int = 1_000_000

# ---------------------------------------------------------------------------
# AWS / Bedrock defaults
# ---------------------------------------------------------------------------
DEFAULT_BEDROCK_REGION: str = "us-east-1"

# ---------------------------------------------------------------------------
# GitHub security feature constants
# ---------------------------------------------------------------------------
CODEQL_STATE: str = "configured"
CODEQL_QUERY_SUITE: str = "default"
SECURITY_FEATURE_ENABLED: str = "enabled"

# ---------------------------------------------------------------------------
# Report watch interval default (seconds)
# ---------------------------------------------------------------------------
DEFAULT_REPORT_WATCH_INTERVAL: int = 3

# ---------------------------------------------------------------------------
# Backlog index column count
# The BACKLOG.md table has 7 data columns. Splitting a pipe-delimited row
# by "|" produces 9 cells (empty leading cell + 7 data + empty trailing cell).
# ---------------------------------------------------------------------------
BACKLOG_INDEX_CELL_COUNT: int = 9
