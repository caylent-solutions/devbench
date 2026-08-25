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
from collections.abc import Mapping

# ---------------------------------------------------------------------------
# Markdown section headers
# ---------------------------------------------------------------------------
COMMENTS_SECTION_HEADER: str = "## Comments"
STATUS_SECTION_PREFIX: str = "## Status:"
STATUS_SUMMARY_SECTION_HEADER: str = "## Status Summary"
STATUS_SUMMARY_TABLE_HEADER: str = (
    "| Epic | Title | Done | In Progress | In Queue | Blocked | Declined | Draft |\n"
    "|------|-------|------|-------------|----------|---------|----------|-------|\n"
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

# Backlog manager recognized status labels (title-case, as in markdown tables)
TABLE_STATUS_VALUES: frozenset[str] = frozenset(
    {"In Queue", "In Progress", "In Review", "Done", "Blocked", "Proposed", "Declined", "Hold"}
)

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

# Pad width applied to every label in the ``devbench status`` Backlog Status
# Summary so count values right-align to a single column regardless of label
# length.  The current longest label is ``Blocked (amendment-recovery)`` /
# ``Blocked (runtime-degradation)`` (29 chars); the chosen width keeps at
# least one space between every label and its count and leaves a few chars
# of headroom for future Blocked sub-bucket labels.  Used by ``cmd_status``
# in ``src/devbench/cli.py`` (issue #201).
STATUS_SUMMARY_LABEL_WIDTH: int = 32

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
STATUS_PROPOSED: str = "proposed"
STATUS_DECLINED: str = "declined"
# Held units are deliberately deferred (under debate, awaiting external
# decision). The orchestrator's next-query and parallel-candidate scan
# both skip held units the same way they skip declined ones, but unlike
# declined a held unit is non-terminal -- parent rollups do NOT count
# held children as complete; an operator must explicitly unhold to
# return the unit to the in-queue lifecycle.
STATUS_HOLD: str = "hold"
# Draft units are pre-queue planning artefacts: the spec is authored and
# reviewed but the unit has not yet been accepted into the execution queue.
# Unlike declined (terminal) or hold (deferred), draft is a pre-lifecycle
# state -- the orchestrator's parallel-candidate scan skips draft units.
STATUS_DRAFT: str = "draft"

# Ordered mapping from any accepted input form to the canonical write form.
# Used by BacklogManager._set_status() for validation and normalisation.
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

# Workflow agents that legitimately write audit-only verdicts via
# ``log-verdict``. They are NOT counted by the done-gate's
# ``_last_round_all_passed`` (only ``ALL_REQUIRED_JUDGE_NAMES`` is); the
# entries land in the work-unit Comments section as audit metadata.
# Mirrored in ``plugin/devbench-orchestrate/scripts/guard-verdict-format.sh``'s
# ``KNOWN_JUDGES`` array; both lists must stay in sync.
WORKFLOW_AGENT_JUDGE_NAMES: frozenset[str] = frozenset(
    {
        "executor",
        "blocker_resolver",
        "manifest_amender",
        "task_factory",
    }
)

# Full allowlist consumed by ``cmd_log_verdict``. Strictly broader than
# ``ALL_REQUIRED_JUDGE_NAMES`` -- the canonical 5 reviewers satisfy the
# done-gate; the workflow-agent names are audit-only.
KNOWN_JUDGE_NAMES: frozenset[str] = ALL_REQUIRED_JUDGE_NAMES | WORKFLOW_AGENT_JUDGE_NAMES

# ---------------------------------------------------------------------------
# Non-verdict agent comment format template
# ---------------------------------------------------------------------------
COMMENT_AGENT_TEMPLATE: str = "[{timestamp}] [agent/{name}] {message}\n"

# ---------------------------------------------------------------------------
# TDD Cycle Log section header and entry format template
# ---------------------------------------------------------------------------
TDD_CYCLE_LOG_SECTION_HEADER: str = "## TDD Cycle Log"
TDD_ENTRY_TEMPLATE: str = "- [{phase}] {timestamp} -- {message}\n"

TDD_PHASE_RED: str = "RED"
TDD_PHASE_GREEN: str = "GREEN"
TDD_PHASE_REFACTOR: str = "REFACTOR"

# RED_OBSERVED (FR-4.3 / E4-F3-S1-T1, issue #257) is a fourth TDD Cycle Log
# phase written exclusively by the orchestrator after it has independently
# run the test suite and observed a nonzero exit code. It is a *valid* phase
# (accepted by ``_append_tdd_entry``/parsed by ``red_gate_satisfied``) but it
# is not agent-writable -- ``cmd_log_tdd`` (the CLI verb agents call) must
# reject it outright. ``AGENT_WRITABLE_TDD_PHASES`` and
# ``ORCHESTRATOR_ONLY_TDD_PHASES`` partition ``VALID_TDD_PHASES`` as data
# (frozenset set-difference) rather than as scattered ``if phase == ...``
# conditionals, so the authorization boundary has one definition.
TDD_PHASE_RED_OBSERVED: str = "RED_OBSERVED"

# GREEN_GREEN_OBSERVED (FR-4.6 / E4-F4-S1-T2, issue #257) is a fifth TDD
# Cycle Log phase, mirroring RED_OBSERVED: written exclusively by the
# orchestrator (``devbench green-green-check``) after it has independently
# run a refactor task's named tests both before and after the change and
# observed both sides pass. It is a *valid* phase but not agent-writable.
# Registering it here (rather than as a private literal local to
# ``devbench.backlog.manager``, as it was through round 3 -- code_review
# FAIL round 4, SOLID/OCP) is load-bearing, not cosmetic:
# ``cli._reject_bracketed_phase_tag``'s bracketed-phase-tag security control
# (HIGH finding, E4-F3-S1-T1) is built directly from ``VALID_TDD_PHASES``, so
# a phase absent from this set is a phase agent free text can forge
# unrejected. Adding it here, and only here, closes that gap for every
# consumer of ``VALID_TDD_PHASES``/``ORCHESTRATOR_ONLY_TDD_PHASES`` at once.
TDD_PHASE_GREEN_GREEN_OBSERVED: str = "GREEN_GREEN_OBSERVED"

VALID_TDD_PHASES: frozenset[str] = frozenset(
    {
        TDD_PHASE_RED,
        TDD_PHASE_GREEN,
        TDD_PHASE_REFACTOR,
        TDD_PHASE_RED_OBSERVED,
        TDD_PHASE_GREEN_GREEN_OBSERVED,
    }
)
AGENT_WRITABLE_TDD_PHASES: frozenset[str] = frozenset({TDD_PHASE_RED, TDD_PHASE_GREEN, TDD_PHASE_REFACTOR})
ORCHESTRATOR_ONLY_TDD_PHASES: frozenset[str] = VALID_TDD_PHASES - AGENT_WRITABLE_TDD_PHASES

TDD_PHASE_ORCHESTRATOR_ONLY_MESSAGE_TEMPLATE: str = (
    "ERROR: TDD phase '{phase}' is orchestrator-only and cannot be written via "
    "log-tdd; agent-writable phases are: {agent_phases}."
)

# ---------------------------------------------------------------------------
# RED_OBSERVED record fields (E4-F3-S1-T1).
#
# A RED_OBSERVED entry's message body is not free text -- it is a fixed
# three-field record (``exit_code``, ``test_node_id``, ``failure_digest``)
# built by ``devbench.cli.build_red_observed_message`` and re-validated by
# ``devbench.cli.red_gate_satisfied`` on read. ``failure_digest`` is
# constrained to a hash-shaped value (never raw free text) so a failure
# message cannot leak secrets or filesystem paths into git history (LOW
# finding, E4-F3-S1-T1 security review).
# ---------------------------------------------------------------------------
RED_OBSERVED_FIELD_EXIT_CODE: str = "exit_code"
RED_OBSERVED_FIELD_TEST_NODE_ID: str = "test_node_id"
RED_OBSERVED_FIELD_FAILURE_DIGEST: str = "failure_digest"
RED_OBSERVED_RECORD_FIELDS: tuple[str, str, str] = (
    RED_OBSERVED_FIELD_EXIT_CODE,
    RED_OBSERVED_FIELD_TEST_NODE_ID,
    RED_OBSERVED_FIELD_FAILURE_DIGEST,
)

RED_OBSERVED_RECORD_MISSING_FIELD_TEMPLATE: str = "RED_OBSERVED record is missing required field '{field}'."
RED_OBSERVED_RECORD_ZERO_EXIT_CODE_MESSAGE: str = (
    "RED_OBSERVED requires a nonzero exit_code (a RED phase is, by definition, an observed failure); got exit_code=0."
)
RED_OBSERVED_RECORD_WHITESPACE_TEST_NODE_ID_TEMPLATE: str = (
    "RED_OBSERVED test_node_id must not contain whitespace (the read-side parser "
    "RED_OBSERVED_MESSAGE_FIELDS_RE requires a single non-whitespace token, so a "
    "space, tab or newline would build a record the gate can never match); got {test_node_id!r}."
)
RED_OBSERVED_RECORD_MALFORMED_DIGEST_TEMPLATE: str = (
    "RED_OBSERVED failure_digest must be a lowercase hex string of {min}-{max} characters; got {digest!r}."
)

FAILURE_DIGEST_MIN_LENGTH: int = 8
FAILURE_DIGEST_MAX_LENGTH: int = 64
FAILURE_DIGEST_RE = re.compile(rf"^[0-9a-f]{{{FAILURE_DIGEST_MIN_LENGTH},{FAILURE_DIGEST_MAX_LENGTH}}}$")

RED_OBSERVED_MESSAGE_TEMPLATE: str = "exit_code={exit_code} test_node_id={test_node_id} failure_digest={failure_digest}"

# Anchored (line-start) match for a RED_OBSERVED entry -- deliberately does
# NOT use a bare substring/"in" check. A phase tag embedded mid-message
# (e.g. an agent-written ``[RED]`` entry whose message body contains the
# literal text ``[RED_OBSERVED]``) must never satisfy this pattern, since
# that would let agent-controlled free text forge the gate (HIGH finding,
# E4-F3-S1-T1 security review).
RED_OBSERVED_ENTRY_LINE_RE = re.compile(
    r"^-\s+\[RED_OBSERVED\]\s+\S+\s+--\s+(?P<message>.+)$",
    re.MULTILINE,
)

# Parses the three RED_OBSERVED record fields out of an entry's message body.
# Requires all three fields, in the fixed order emitted by
# ``RED_OBSERVED_MESSAGE_TEMPLATE`` -- a message missing any field never
# matches (no partial-record fallback). ``failure_digest`` is captured
# permissively here (``\S+``); ``FAILURE_DIGEST_RE`` is the single source of
# truth for the hash-shape validation applied on top of this parse.
RED_OBSERVED_MESSAGE_FIELDS_RE = re.compile(
    r"^exit_code=(?P<exit_code>-?\d+)\s+"
    r"test_node_id=(?P<test_node_id>\S+)\s+"
    r"failure_digest=(?P<failure_digest>\S+)$"
)

# Captures the body of the ``## TDD Cycle Log`` section only -- from the
# header up to (but not including) the next ``## `` heading or end of
# string. Mirrors ``STRIP_SUMMARY_RE`` above. ``red_gate_satisfied`` scopes
# its search to this capture group so a RED_OBSERVED-shaped line planted in
# any other section (e.g. ``## Comments``) can never satisfy the gate (HIGH
# finding, E4-F3-S1-T1 security review).
TDD_CYCLE_LOG_SECTION_BODY_RE = re.compile(
    r"^## TDD Cycle Log\n(.*?)(?=^## |\Z)",
    re.MULTILINE | re.DOTALL,
)

# ---------------------------------------------------------------------------
# Six-type task taxonomy (FR-4.1 / E4-F2-S1-T1).
#
# ``## Task Type:`` is an optional work-unit section. When present it must
# name exactly one of the six values below; when absent, ``validate-backlog``
# defaults the task to the strictest type (``behavior-fix``) so that a task
# with no declared type is never accidentally exempted from the RED gate or
# the production-source Manifest invariant.
#
# ``GATED_TASK_TYPES`` are the types that require a RED-gated TDD cycle and
# at least one production-source row in the Changes Manifest.
# ---------------------------------------------------------------------------
TASK_TYPE_BEHAVIOR_FIX: str = "behavior-fix"
TASK_TYPE_FEATURE: str = "feature"
TASK_TYPE_TEST_ONLY: str = "test-only"
TASK_TYPE_REFACTOR: str = "refactor"
TASK_TYPE_DOCS: str = "docs"
TASK_TYPE_CHORE: str = "chore"

VALID_TASK_TYPES: frozenset[str] = frozenset(
    {
        TASK_TYPE_BEHAVIOR_FIX,
        TASK_TYPE_FEATURE,
        TASK_TYPE_TEST_ONLY,
        TASK_TYPE_REFACTOR,
        TASK_TYPE_DOCS,
        TASK_TYPE_CHORE,
    }
)

DEFAULT_TASK_TYPE: str = TASK_TYPE_BEHAVIOR_FIX

GATED_TASK_TYPES: frozenset[str] = frozenset({TASK_TYPE_BEHAVIOR_FIX, TASK_TYPE_FEATURE})

TASK_TYPE_SECTION_PREFIX: str = "## Task Type:"
TASK_TYPE_LINE_RE = re.compile(r"^(##\s*Task Type:\s*)(.+)$", re.MULTILINE)

# ---------------------------------------------------------------------------
# Expected-output taxonomy (per-work-unit commit declaration).
#
# A work unit declares whether executing it is expected to produce a commit.
# ``commit`` is the default when the section is absent, so every backlog
# authored before this section existed keeps its current lifecycle exactly.
# ``none`` names a unit that verifies, decides, or no-ops: it records its
# evidence in ``## Comments`` and git-ops completes it without a commit, push,
# PR, CI wait, or merge. See docs/backlog-contract.md and ADR-35.
# ---------------------------------------------------------------------------
EXPECTED_OUTPUT_COMMIT: str = "commit"
EXPECTED_OUTPUT_NONE: str = "none"
VALID_EXPECTED_OUTPUTS: frozenset[str] = frozenset({EXPECTED_OUTPUT_COMMIT, EXPECTED_OUTPUT_NONE})
DEFAULT_EXPECTED_OUTPUT: str = EXPECTED_OUTPUT_COMMIT
EXPECTED_OUTPUT_SECTION_PREFIX: str = "## Expected Output:"
EXPECTED_OUTPUT_LINE_RE = re.compile(r"^(##\s*Expected Output:\s*)(.+)$", re.MULTILINE)

# ---------------------------------------------------------------------------
# Orphan-path patterns: build/state artifacts that no production workflow
# commits. fnmatch-style globs matched against POSIX-relative repo paths;
# ``**/`` matches both repo-root and nested locations.
#
# Dependency LOCK files are deliberately absent. A lock file pins resolved
# dependency versions and belongs in version control -- devbench treats
# uv.lock, package-lock.json, poetry.lock, Cargo.lock and go.sum as ordinary
# tracked files, and .terraform.lock.hcl is the same category. Listing one
# here makes git-ops ``git rm --cached`` it as a build artifact, which is a
# reproducibility regression, not cleanup.
#
# Override per workspace with ``git_ops.orphan_patterns`` in devbench.yaml, or
# ``DEVBENCH_ORPHAN_IGNORE_PATTERNS`` (comma-separated) in the environment.
# Either replaces this list wholesale.
# ---------------------------------------------------------------------------
DEFAULT_ORPHAN_PATTERNS: tuple[str, ...] = (
    # Terraform state and module cache. ``**/`` prefix matches both
    # repo-root and nested locations.
    "**/*.tfstate",
    "**/*.tfstate.backup",
    "**/*.tfstate.lock.info",
    "**/.terraform/**",
    "**/.terragrunt-cache/**",
    # Python build / test caches
    "**/__pycache__/**",
    "**/*.pyc",
    "**/*.pyo",
    "**/.pytest_cache/**",
    "**/.mypy_cache/**",
    "**/.ruff_cache/**",
    # Coverage. ``**/.coverage*`` (no separator) is the catch-all that
    # covers ``.coverage``, ``.coverage.<ext>``, and the stray
    # ``.coverage (1)`` form pytest-cov writes when the canonical file
    # is locked. The narrower variants below stay for documentation but
    # are subsumed by the catch-all on this line.
    "**/.coverage*",
    "**/htmlcov/**",
    # Ansible: ansible-playbook writes a .retry file listing failed hosts.
    "**/*.retry",
    # Helm: `helm dependency update` vendors dependency charts as archives
    # under a chart's charts/ directory. Chart.lock pins their versions and
    # IS committed, so only the archives are listed here.
    "**/charts/*.tgz",
    # Terraform binary plan output (`terraform plan -out`).
    "**/*.tfplan",
    # Python virtualenv and build metadata
    "**/.venv/**",
    "**/*.egg-info/**",
    # Node
    "**/node_modules/**",
    # macOS
    "**/.DS_Store",
)

# ---------------------------------------------------------------------------
# Epic ID regex -- matches top-level epic IDs such as "E200", "E1", etc.
# A row is an epic row when its ID is exactly E<digits> with no hyphen suffix.
# ---------------------------------------------------------------------------
EPIC_ID_RE = re.compile(r"^E\d+$")

# ---------------------------------------------------------------------------
# Operational parameter defaults
# ---------------------------------------------------------------------------
DEFAULT_MAX_RETRY_ATTEMPTS: int = 10
DEFAULT_GITHUB_CHECK_TIMEOUT_SECONDS: int = 600
# Orchestrator inactivity net (issue db-262, FR-17). Bounds how long the
# `cmd_start._run` SDK message loop may wait for the NEXT SDK message
# (`asyncio.wait_for(agen.__anext__(), timeout=...)`) before treating the
# turn as hung and disposing it as a bounded fresh-session restart (see
# `_resolve_max_quota_resumes`). Conservative multi-minute default: it must
# exceed the longest legitimate turn, since a Task subagent invoked by the
# orchestrate skill can run many minutes without emitting an intermediate
# SDK message.
DEFAULT_ORCHESTRATOR_INACTIVITY_SECONDS: int = 1800
DEFAULT_STOP_HOOK_MAX_BLOCKS: int = 5
DEFAULT_STOP_HOOK_WINDOW_SECONDS: int = 180
DEFAULT_STOP_HOOK_STALE_TASK_MINUTES: int = 120
# Hook-tail column caps (issue #134). Operator-tunable via env vars
# (JUDGE_HOOK_TAIL_*) or YAML (`hook_tail.*` block). DESCRIPTION_MAX bumped
# from 100 -> 120 in the same release that introduces the configurability.
DEFAULT_HOOK_TAIL_AGENT_WIDTH: int = 12
DEFAULT_HOOK_TAIL_TOOL_WIDTH: int = 8
DEFAULT_HOOK_TAIL_DESCRIPTION_MAX: int = 120
DEFAULT_HOOK_TAIL_STDOUT_PREVIEW_MAX: int = 80
# Recovery-cascade depth cap (issue #144). Operator-tunable via
# `orchestrate.max_cascade_depth` YAML or
# `JUDGE_ORCHESTRATE_MAX_CASCADE_DEPTH` env var. When a proposal would
# land at depth >= this cap, the source task transitions to
# NEEDS_OPERATOR_ATTENTION instead of materialising another recovery
# layer. Default of 2 reflects the bounded cascade depth needed for
# typical recovery chains.
DEFAULT_MAX_CASCADE_DEPTH: int = 2
# Workflow-registration race defence (issue #114). When `gh pr checks`
# returns "no checks reported" right after a PR is created, devbench
# cannot tell "repo has no CI configured" apart from "GitHub Actions
# has not yet enqueued the workflow for this commit". The retry loop
# (12 retries x 5s = 60s default coverage) handles the race; the
# zero-workflow-files fast path skips the wait when the repo legitimately
# has no CI. Both knobs override via JUDGE_CHECK_REGISTRATION_RETRIES /
# JUDGE_CHECK_REGISTRATION_DELAY_SECONDS env vars.
DEFAULT_CHECK_REGISTRATION_RETRIES: int = 12
DEFAULT_CHECK_REGISTRATION_DELAY_SECONDS: int = 5
# Recency cap for the "recent recovery audit comment" heuristic in the
# 3-state blocked-task classifier (AWAITING_AUTO_RECOVERY signal #3).
# Tasks whose most recent [BLOCKED] audit-comment timestamp is older
# than this window fall through to NEEDS_OPERATOR_ATTENTION, even when
# the agent-tag and body-pattern would otherwise match. 30 minutes
# covers the gap between manifest-amender FAIL and blocker-resolver's
# proposal write under normal orchestrator iteration cadence; tune via
# JUDGE_BLOCKED_RECOVERY_WINDOW_SECONDS for slower / debugging runs.
DEFAULT_BLOCKED_RECOVERY_WINDOW_SECONDS: int = 1800
# When ``True``, ``cmd_git_ops`` runs ``cleanup_tracked_orphans`` inline as a
# devbench-authored chore commit instead of emitting a backlog cleanup task.
# Eliminates the orphan-cleanup-cascade pathology where multiple parent tasks
# emit duplicate cleanup proposals, the cleanup tasks themselves get blocked by
# the manifest amender on predecessor staging, and the orchestrator loops
# without converging. Operators can fall back to the legacy proposal flow via
# ``DEVBENCH_DISABLE_INLINE_ORPHAN_CLEANUP=1`` when an audit work-unit is
# required for compliance reporting; the legacy path still runs but with
# cross-task de-duplication so duplicate proposals cannot land.
DEFAULT_INLINE_ORPHAN_CLEANUP_ENABLED: bool = True
# Canonical commit message used by the inline orphan-cleanup path. Stable
# string so post-merge audit tooling can grep for the chore commits without
# parsing free-form prose. Keep in sync with the documented contract in
# ``docs/backlog-contract.md``'s orphan-cleanup section.
DEVBENCH_INLINE_CLEANUP_COMMIT_MESSAGE: str = (
    "chore(cleanup): untrack devbench-managed orphan paths and update .gitignore"
)
# Issue #115: CI-failure feedback log byte cap. ``cmd_git_ops`` writes the
# trimmed failing-job log to ``<workspace>/.devbench/ci-failures/<id>-<n>.log``
# so the executor retry loop has a stable filesystem path to read; the cap
# keeps the feedback payload bounded so a runaway-log job cannot consume the
# entire executor context window. Override via ``JUDGE_CI_FAILURE_LOG_BYTES``
# when a backlog's CI legitimately produces longer relevant tails.
DEFAULT_CI_FAILURE_LOG_BYTES: int = 32768
# Issue #115: CI-failure executor retry path. Default ``True`` so every
# backlog benefits without per-shell setup; set
# ``JUDGE_CI_FAILURE_RETRY_ENABLED=0`` (or ``false`` / ``no`` / ``off``) or
# ``git_ops.ci_failure_retry: false`` in YAML to opt out. ``cmd_git_ops``
# returns rc=2 on CI failure (signalling executor retry) until the shared
# retry budget (``MAX_RETRY_ATTEMPTS``) is exhausted, at which point rc=1
# BLOCKED applies. The retry budget is shared with the existing review-judge
# retry budget so total per-task work stays bounded.
DEFAULT_CI_FAILURE_RETRY_ENABLED: bool = True
# Issue #116: opt-in toggle for the PR review-comment polling path. Default
# False; both this AND a non-empty ``JUDGE_PR_REVIEW_AGENTS`` (or
# ``JUDGE_PR_REVIEW_DECISION_BLOCKS=1``) are required for the phase to
# activate. Allows the phase to ship without changing default merge cadence.
DEFAULT_PR_REVIEW_RESOLUTION_ENABLED: bool = False
# Issue #101: pause-before-merge mode. Default False so existing single-PR
# and multi-PR flows are unchanged. When True, ``cmd_git_ops`` pushes the PR
# and waits for green CI then transitions to ``in-review`` instead of
# merging; the orchestrator's loop reconciles ``in-review`` tasks via
# ``cmd_check_merge`` on the next iteration. Mutually exclusive with
# ``defer_pr: true`` and ``single_branch: <name>`` (validated at config load).
DEFAULT_PAUSE_BEFORE_MERGE: bool = False
# Issue #116: PR review-comment poll/settle window. After ``wait_for_checks``
# returns True, ``cmd_git_ops`` polls ``gh pr view`` for late-arriving
# REQUEST_CHANGES reviews and bot comments for up to this many seconds before
# merging. Event-driven loop (poll + condition), not a blanket sleep -- the
# helper exits early on the first signal. Override via
# ``JUDGE_PR_REVIEW_SETTLE_SECONDS``.
DEFAULT_PR_REVIEW_SETTLE_SECONDS: int = 60
# Issue #116: poll cadence inside the settle window. Smaller values reduce
# observed latency at the cost of more ``gh`` API calls; the default 5 s
# matches the ``CHECK_REGISTRATION_DELAY_SECONDS`` cadence already used by
# ``wait_for_checks`` so operators see one consistent rhythm. Override via
# ``JUDGE_PR_REVIEW_POLL_INTERVAL``.
DEFAULT_PR_REVIEW_POLL_INTERVAL: int = 5
# Issue #116: when ``True`` (the default), a PR with ``reviewDecision ==
# CHANGES_REQUESTED`` blocks the merge regardless of the bot allowlist. When
# ``False``, only allowlisted bot comments block the merge. Repos without
# review bots leave both signals empty and the entire phase is a no-op
# (zero regression for existing behaviour). Override via
# ``JUDGE_PR_REVIEW_DECISION_BLOCKS``.
DEFAULT_PR_REVIEW_DECISION_BLOCKS: bool = True
# Issue #116: comma-separated allowlist of GitHub login names whose review
# comments block the merge until resolved. Empty default means the phase is
# a no-op for repos without review bots. Override via
# ``JUDGE_PR_REVIEW_AGENTS`` (e.g. ``github-copilot[bot],amazon-q-developer[bot]``).
DEFAULT_PR_REVIEW_AGENTS: tuple[str, ...] = ()
# ---------------------------------------------------------------------------
# Per-model token pricing (issue #223). Each model id maps to a ``ModelRates``
# carrying the four scalar rates that devbench charges against. The cache
# multipliers + correction_factor are optional per-model overrides; when None
# the report falls back to the top-level ``ReportConfig`` multipliers.
#
# The legacy scalar fields (``DEFAULT_TOKEN_COST_PER_M_INPUT`` /
# ``DEFAULT_TOKEN_COST_PER_M_OUTPUT`` / ``DEFAULT_TOKEN_COST_DISCOUNT``) were
# removed in the same commit per CLAUDE.md "Complete Replacement of
# Superseded Code". Existing workspaces that set the old keys get a clear
# fail-fast error at config-load time pointing at the new ``report.models``
# block; see ``docs/model-pricing.md``.
# ---------------------------------------------------------------------------


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
        # **kwargs (rather than named ``input=``/``output=``) so the
        # attribute names match the YAML keys verbatim without shadowing
        # the builtin ``input()`` in this scope.  Callers always pass
        # keyword arguments (``ModelRates(input=5.0, output=25.0)``); the
        # init validates required keys and rejects unknown ones so type
        # safety is preserved at the boundary.  ``float | None`` covers
        # both the required scalar fields (always float) and the optional
        # multiplier fields (None when unset).
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
        # All four numeric inputs may already be float; the float(...) cast
        # ensures ints coming through JSON or YAML are normalised to the
        # arithmetic domain ``_compute_cost`` expects.  The conditional
        # ``None`` preserves the "unset" sentinel on the optional fields.
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


# Default per-model rates table -- single source of truth.  Lifted verbatim
# from ``docs/model-pricing.md`` Standard pricing table.  Operators who
# leave ``report.models`` absent get this table plus DEFAULT_FALLBACK_MODEL_RATES
# for any model id observed at runtime that isn't in the table (sentinel key
# ``"<unknown>"``).
#
# Keys match the literal ``model`` strings emitted by Claude Code in the
# transcript ``message.model`` field (e.g. ``claude-opus-4-7``, NOT
# ``us.anthropic.claude-opus-4-7-v1``).
#
# Current-lineup entries (issue #233, spec FR-3.1, section 5.3) added below
# the legacy rows. Source: https://platform.claude.com/docs/en/about-claude/pricing,
# captured 2026-07-28. All four are LIST rates per spec S5.3 -- workspaces
# wanting invoice-accurate introductory pricing override locally via
# ``report.models`` rather than getting a promotional rate baked into the
# shipped default.
DEFAULT_MODEL_RATES: dict[str, ModelRates] = {
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
    # Fable 5 list rate. Source: https://platform.claude.com/docs/en/about-claude/pricing,
    # captured 2026-07-28.
    "claude-fable-5": ModelRates(input=10.0, output=50.0),
    # Opus 5 list rate; current shipped default (issue #233 supersedes the
    # literal #254 request for Opus 4.8, per Decision D-2 -- Opus 5 shipped
    # after #254 was filed). Source:
    # https://platform.claude.com/docs/en/about-claude/pricing, captured 2026-07-28.
    "claude-opus-5": ModelRates(input=5.0, output=25.0),
    # Opus 4.8 list rate; selectable but no longer the default (see
    # claude-opus-5 above). Source:
    # https://platform.claude.com/docs/en/about-claude/pricing, captured 2026-07-28.
    "claude-opus-4-8": ModelRates(input=5.0, output=25.0),
    # Sonnet 5 LIST rate per spec S5.3 ($3/$15). NOTE: an introductory rate
    # of $2/$10 runs through 2026-08-31; that promotional rate is NOT the
    # shipped default -- workspaces wanting invoice-accurate introductory
    # pricing during the promo window override locally via `report.models`.
    # Source: https://platform.claude.com/docs/en/about-claude/pricing,
    # captured 2026-07-28.
    "claude-sonnet-5": ModelRates(input=3.0, output=15.0),
}

# Rates applied to the ``"<unknown>"`` aggregation bucket: any transcript
# message whose ``model`` field is missing, or any model id that does not
# appear in the loaded ``report.models`` table. Default mirrors Opus 5 list
# (issue #233; supersedes the prior Opus 4.7-list default) so devbench errs
# on the conservative (over-report) side -- under-reporting is the
# operator-pain failure mode #223 is filed against.
DEFAULT_FALLBACK_MODEL_RATES: ModelRates = ModelRates(input=5.0, output=25.0)

# Em-dash (U+2014). Prohibited in work-unit markdown files by the
# validate-backlog Check 10 (manager.py). Any CLI writer that accepts
# free-form agent text must reject em-dash at the input boundary so the
# validator can trust its own data.
EM_DASH: str = "\u2014"

# Minimum number of completed tasks required before per-window pace numbers
# (avg_minutes, est_hours) are considered statistically meaningful. Below this
# threshold the report renders "n/a (N=X samples)" rather than projecting from
# a single completion that could swing wildly with the next task. 3 is small
# enough to be responsive once a session has produced a few completions but
# large enough to avoid the N=1 fragility seen in production reports.
MIN_PACE_SAMPLES: int = 3

# Default number of most recently completed tasks to average for the "Recent
# pace" projection. Used when at least this many completions exist; otherwise
# the report falls back to All-time pace. Overridable via
# `report.recent_pace_tasks` YAML or `JUDGE_REPORT_RECENT_PACE_TASKS` env.
DEFAULT_RECENT_PACE_TASKS: int = 10

# Whitespace columns between the two side-by-side tables rendered by
# `devbench report` (Backlog state on left, Window stats on right). Pure
# rendering affordance -- kept as a constant so the gap is consistent and
# editable from one place.
SIDE_BY_SIDE_GAP_CHARS: int = 4

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
# ``%Z`` rather than a literal "UTC": comments are stamped in the workspace's
# ``display_timezone`` when one is set, so the header has to name the zone it
# actually used. Unset, the zone resolves to UTC and the rendered text is
# byte-identical to what earlier versions wrote.
COMMENT_TIMESTAMP_FORMAT: str = "%Y-%m-%d %H:%M %Z"

# ---------------------------------------------------------------------------
# Git message templates for finalize operations
# ---------------------------------------------------------------------------
FINALIZE_COMMIT_TEMPLATE: str = "finalize: {branch}"
FINALIZE_PR_TITLE_TEMPLATE: str = "feat: {branch}"

# ---------------------------------------------------------------------------
# Plugin path (relative to package root)
# ---------------------------------------------------------------------------
DEFAULT_PLUGIN_SUBPATH: str = "plugin/devbench-orchestrate"

# ---------------------------------------------------------------------------
# Subprocess error exit code (Unix convention for command-not-found / timeout)
# ---------------------------------------------------------------------------
SUBPROCESS_ERROR_EXIT_CODE: int = 127

# Exit code emitted by ``cmd_start`` when the orchestrator's SDK subprocess
# exited via ``NO_ACTIONABLE`` purely because every remaining blocker
# classifies as ``BlockedTaskState.RUNTIME_DEGRADATION`` (the SDK lost
# Agent-tool access mid-session, recoverable by a fresh subprocess) and
# there are zero IN_PROGRESS / IN_REVIEW tasks and zero
# OPERATOR_ACTION_REQUIRED blockers. The wrapping ``make start`` loop
# treats this code as "auto-restart" up to ``DEVBENCH_MAX_AUTO_RESTARTS``
# times. Any other exit (0 = clean, anything-else = real failure) is
# passed through unchanged.
ORCHESTRATOR_RESTART_EXIT_CODE: int = 42

# Audit-log template written to ``logs/orchestrator.log`` when
# ``cmd_start`` triggers an auto-restart. The token + task-id list let
# operators grep history for restart frequency without parsing
# free-form prose. Format: ``[ORCHESTRATOR_AUTO_RESTART]
# reason=runtime_degradation tasks=<id1,id2,...>``.
ORCHESTRATOR_AUTO_RESTART_AUDIT_PREFIX: str = "[ORCHESTRATOR_AUTO_RESTART] reason=runtime_degradation tasks="

# Audit-log prefix written once per stash entry when a task transitioning to
# ``blocked`` has its target-repo residue quarantined out of the shared
# checkout (``cli._clean_target_repo_on_block``). Blocking used to run
# ``git reset --hard`` + ``git clean -fd`` and destroy uncommitted work
# outright; it now reuses the same non-destructive quarantine claim-time uses,
# and this marker records where each entry went so an operator can recover it
# from ``git stash list``. Format: ``[BLOCK_QUARANTINE] <unit-id> owner=<id>
# paths=<n> stash=<message>``.
ORCHESTRATOR_BLOCK_QUARANTINE_AUDIT_PREFIX: str = "[BLOCK_QUARANTINE] "

# Audit-row tag written by ``cli.cmd_log_verdict`` at the moment a review
# judge's executor retry budget is spent, so the run stops re-litigating a
# work unit no further executor round can fix.
#
# The tag text is load-bearing and must appear verbatim (issue #248):
# ``backlog.proposal._RETRY_EXHAUSTED_TAG_RE`` matches it case-sensitively to
# classify the unit as ``OPERATOR_ACTION_REQUIRED`` instead of
# ``AWAITING_AMENDMENT_RECOVERY``, whose contract is "operator does nothing".
# Without the tag a spent budget reads as a recovery signal and the run
# stalls with no operator alert.
#
# Enforcement lives in code rather than in orchestrate SKILL.md prose because
# the prose contract was unenforceable: it told the orchestrator to read the
# budget via ``devbench config-resolve``, a verb that did not exist, so the
# per-judge budget in ``max_executor_retries_per_judge`` was parsed by
# ``config_loader`` and never consumed. Reviews could therefore repeat without
# bound; the audit trail this tag lands in is the same one the counter reads.
ORCHESTRATOR_RETRY_BUDGET_EXHAUSTED_AUDIT_TAG: str = "[RETRY_BUDGET_EXHAUSTED]"

# Bound on the number of consecutive in-process quota-recovery resumes
# ``_drive_orchestrate_with_quota_resume`` performs before stopping the
# orchestrator (spec FR-2.8, AC-22). Overridable via
# ``DEVBENCH_MAX_QUOTA_RESUMES``; resolved through
# ``_resolve_max_quota_resumes``'s fail-safe parse (non-integer or <= 0
# falls back to this default rather than raising or disabling resume, so a
# typo can never silently turn a single quota window into a run-ending
# event). Configuration precedence: env > yaml > built-in default
# (Section 7.3).
DEFAULT_MAX_QUOTA_RESUMES: int = 1000

# Bound on the number of consecutive in-process restarts
# ``_drive_orchestrate_with_quota_resume`` performs after a PREMATURE TURN END
# -- the SDK session ending while backlog work remains and no terminal
# sentinel (``ALL_DONE`` / ``NO_ACTIONABLE``) was ever observed. Overridable
# via ``DEVBENCH_MAX_PREMATURE_TURN_END_RESTARTS`` and resolved through
# ``_resolve_max_premature_turn_end_restarts``'s fail-safe parse, mirroring
# ``DEFAULT_MAX_QUOTA_RESUMES``.
#
# Deliberately far lower than the quota / inactivity cap: those two failure
# modes each self-throttle (a quota window must elapse, an inactivity restart
# costs a full timeout window), whereas a model that ends its turn immediately
# can do so again immediately. A transport fault does NOT self-throttle -- it
# can recur as fast as the SDK can fail -- so it carries its own bound
# (``DEFAULT_MAX_TRANSPORT_RESTARTS``) and its own backoff below, rather than
# sharing the 1000 ceiling.
# Sharing the 1000 ceiling would let one reproducible prompt-following failure
# burn a thousand consecutive sessions with no operator in the loop. This cap
# is a cost guard, not a correctness bound: exhausting it is itself the signal
# that the loop is not making progress and needs a human.
DEFAULT_MAX_PREMATURE_TURN_END_RESTARTS: int = 10

# Bound on the number of consecutive in-process restarts
# ``_drive_orchestrate_with_quota_resume`` performs after an SDK TRANSPORT
# error, and the exponential-backoff envelope applied between those restarts.
#
# Transport restarts previously borrowed ``DEFAULT_MAX_QUOTA_RESUMES`` (1000)
# and retried with no delay at all. That pairing is unsound: unlike a quota
# window or an inactivity timeout, a transport fault imposes no natural delay,
# so a persistently failing transport spends the entire 1000-restart budget as
# fast as the SDK can reject a session -- observed in the field as ~1000
# restarts inside 39 minutes, after which the run ended and the daemon exited
# with no operator signal until someone read the log.
#
# The cap is therefore separate from the quota ceiling, and sized as a TIME
# budget rather than a raw attempt count: a transport that is still failing
# after roughly an hour is down, not flapping, and the run must fail loudly
# rather than grind. Backoff spaces the attempts so a transient fault still
# recovers without burning the budget in seconds: delay =
# ``base * 2 ** restarts_used``, clamped to ``max``.
#
# Why 14 specifically. Each restart costs one full SDK session lifetime plus
# one backoff wait. Measured against a live Anthropic 529 'overloaded' outage,
# an SDK session burns its own ``max_retries`` and raises after ~199s, and the
# backoff ladder (1, 2, 4, 8, 16, 32, then 60s) adds ~9 min across 14
# restarts. 15 sessions x 199s + 9.1 min => ~59 min to cap exhaustion, i.e. a
# provider outage is ridden out for about an hour before the run halts.
#
# That ~199s session lifetime is a property of the SDK's own retry schedule
# under one observed failure mode, not a constant. A different fault (instant
# rejection, say) makes each cycle far shorter and the same cap exhausts much
# sooner. The hour is the intent; the number is the calibration. Operators who
# need a different window should set the wall-clock they want and re-derive,
# not nudge this integer blindly.
#
# All three are overridable, precedence env > yaml > built-in default:
#   ``DEVBENCH_MAX_TRANSPORT_RESTARTS``
#     / ``orchestrate.max_transport_restarts``
#   ``DEVBENCH_TRANSPORT_RESTART_BACKOFF_BASE_SECONDS``
#     / ``orchestrate.transport_restart_backoff_base_seconds``
#   ``DEVBENCH_TRANSPORT_RESTART_BACKOFF_MAX_SECONDS``
#     / ``orchestrate.transport_restart_backoff_max_seconds``
DEFAULT_MAX_TRANSPORT_RESTARTS: int = 14
DEFAULT_TRANSPORT_RESTART_BACKOFF_BASE_SECONDS: float = 1.0
DEFAULT_TRANSPORT_RESTART_BACKOFF_MAX_SECONDS: float = 60.0

# Reasoning effort and per-turn thinking budget for the orchestrator SDK
# session, and through it every agent the session spawns.
#
# Left unset, the session inherits whatever effort the ambient Claude Code
# configuration carries. That is how an unattended run ends up on ``xhigh``
# without anyone choosing it, and effort is not a free dial: a turn that
# reasons for longer than the prompt-cache lifetime returns to a cold cache,
# so the whole prompt is re-uploaded and re-cached on the next turn instead of
# being read back. The run then pays full price per turn and exhausts its
# quota far sooner, and quota exhaustion is what interrupts units mid-flight.
#
# ``DEFAULT_ORCHESTRATE_MAX_THINKING_TOKENS`` is the guard rail: it bounds one
# turn's reasoning so a turn cannot outlive the cache window. The value is a
# budget, not a target -- turns that need less use less.
#
# Override:
#   ``DEVBENCH_ORCHESTRATE_EFFORT`` / ``orchestrate.effort``
#   ``DEVBENCH_ORCHESTRATE_MAX_THINKING_TOKENS``
#     / ``orchestrate.max_thinking_tokens``
DEFAULT_ORCHESTRATE_EFFORT: str = "high"
DEFAULT_ORCHESTRATE_MAX_THINKING_TOKENS: int = 16000

# The effort levels the SDK accepts. An unrecognised value is rejected at
# config load rather than passed through to fail deep inside a session.
VALID_ORCHESTRATE_EFFORTS: tuple[str, ...] = ("low", "medium", "high", "xhigh", "max")

# Audit marker emitted by ``_should_resume_after_quota_recovery`` on each
# permitted in-process quota resume: ``[ORCHESTRATOR_QUOTA_RESUME]
# resume=<n> max=<cap>`` (spec FR-2.10).
ORCHESTRATOR_QUOTA_RESUME_AUDIT_PREFIX: str = "[ORCHESTRATOR_QUOTA_RESUME]"

# Audit marker emitted by ``_should_resume_after_quota_recovery`` when the
# in-process quota-resume cap above is reached:
# ``[ORCHESTRATOR_QUOTA_RESUMES_EXHAUSTED] max=<cap>`` (spec FR-2.10).
ORCHESTRATOR_QUOTA_RESUMES_EXHAUSTED_AUDIT_PREFIX: str = "[ORCHESTRATOR_QUOTA_RESUMES_EXHAUSTED]"

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
# Report window detection
# ---------------------------------------------------------------------------
# Gap (in minutes) between consecutive log entries that signals an orchestrator
# restart, used to identify the "current session" boundary in `devbench report`.
# 30 minutes is generous enough to span a long single task (which can take
# 17+ minutes) without misclassifying mid-task quiet as a session boundary.
DEFAULT_SESSION_GAP_MINUTES: int = 30

# Logger name to filter out of session-boundary detection. The log_setup
# logger fires once per CLI invocation including every `devbench report --watch`
# tick, which would otherwise look like noise that resets the session boundary.
LOG_NOISE_LOGGER_NAME: str = "judges.log_setup"

# ---------------------------------------------------------------------------
# Anthropic prompt-caching multipliers (relative to base input rate).
# Universal across Anthropic-served Claude models.
# Source: https://platform.claude.com/docs/en/about-claude/pricing (2026-04-16).
# Override per-deployment via `report.cache_*_multiplier` in devbench.yaml.
# ---------------------------------------------------------------------------
DEFAULT_CACHE_READ_MULTIPLIER: float = 0.10
DEFAULT_CACHE_WRITE_5MIN_MULTIPLIER: float = 1.25
DEFAULT_CACHE_WRITE_1HR_MULTIPLIER: float = 2.0
# Data-residency premium when usage.inference_geo is set (US-only inference;
# applies to Opus 4.7, Opus 4.6, Sonnet 4.6+).
DEFAULT_DATA_RESIDENCY_MULTIPLIER: float = 1.10
# Fast-mode premium when usage.speed == "fast" (Opus 5 and Opus 4.8 only at
# the time of this snapshot, issue #233). Source:
# https://platform.claude.com/docs/en/about-claude/pricing, captured 2026-07-28:
# fast mode runs $10/$50 on a $5/$25 base, i.e. a 2.0x multiplier. Applied
# per-call to the fast_* token subset (issue #124); see
# reporting.report._compute_cost_by_model.
DEFAULT_FAST_MODE_MULTIPLIER: float = 2.0

# ---------------------------------------------------------------------------
# Report table render widths (characters)
# ---------------------------------------------------------------------------
REPORT_METRIC_COLUMN_WIDTH: int = 60
REPORT_VALUE_COLUMN_WIDTH: int = 16

# ---------------------------------------------------------------------------
# Time unit conversions
# ---------------------------------------------------------------------------
MS_PER_SECOND: int = 1000
SECONDS_PER_MINUTE: int = 60
SECONDS_PER_HOUR: int = 3600
# Divisor for a minutes-valued quantity becoming hours -- distinct from
# SECONDS_PER_MINUTE (the divisor for a seconds-valued quantity becoming
# minutes). Both equal 60; using the wrong one is a latent semantic trap
# even though today's rendered output is unaffected (#329 FR-5).
MINUTES_PER_HOUR: int = 60
PERCENT_MULTIPLIER: int = 100

# ---------------------------------------------------------------------------
# Backlog index column count
# The BACKLOG.md table has 7 data columns. Splitting a pipe-delimited row
# by "|" produces 9 cells (empty leading cell + 7 data + empty trailing cell).
# ---------------------------------------------------------------------------
BACKLOG_INDEX_CELL_COUNT: int = 9

# ---------------------------------------------------------------------------
# Per-agent model overrides (Option A shadow-plugin-dir, ADR-25)
# ---------------------------------------------------------------------------
# Workspace-relative directory holding the materialised shadow plugin tree.
# When operators set ``agents.<name>: <model>`` in ``devbench.yaml`` the
# canonical marketplace plugin cannot be edited; instead, a shadow tree is
# built under ``<workspace>/<PLUGIN_SHADOW_DIR_NAME>/devbench/`` that mirrors
# the canonical via symlinks for every file except the overridden agent .md
# files. ``cmd_start`` and ``devbench prepare-plugin-shadow`` both point the
# Claude Agent SDK / ``claude --plugin-dir`` at this path so non-interactive
# and interactive modes share one mechanism.
PLUGIN_SHADOW_DIR_NAME: str = ".devbench/plugin-shadow"

# Filename of the PID sentinel written by ``cmd_start`` inside the shadow
# plugin tree at ``<workspace>/<PLUGIN_SHADOW_DIR_NAME>/devbench/<filename>``.
# The sentinel records the orchestrator PID that owns the materialised tree;
# ``clear_shadow_plugin`` refuses to delete the tree while the recorded PID
# is alive (raising ``RuntimeError``) so a concurrent ``devbench
# prepare-plugin-shadow`` invocation cannot clear a running orchestrator's
# plugin files out from under it. Lives inside the tree so ``rmtree`` of
# the tree removes the sentinel atomically when a clean rebuild is allowed.
SHADOW_PID_SENTINEL_FILENAME: str = ".pid"

# Short-name model aliases accepted in ``agents.*`` YAML values when
# ``use_bedrock: false``. Mirrors the convenience short forms the Anthropic
# SDK accepts. ``fable`` added by issue #233 (E3 model refresh) to alias
# ``claude-fable-5``.
ALLOWED_AGENT_MODEL_SHORT_NAMES: frozenset[str] = frozenset({"opus", "sonnet", "fable"})

# Full Anthropic model id pattern (``claude-opus-5``, ``claude-sonnet-5``,
# ``claude-fable-5``, ``claude-sonnet-4-6-20250514``). Accepted when
# ``use_bedrock: false``. Note: ids containing ``haiku`` are rejected by
# ``validate_agent_model_value()`` even though they would otherwise match
# this pattern. Verified (spec S1.7) to already match every current-lineup
# id added by issue #233 with zero pattern changes.
ANTHROPIC_AGENT_MODEL_PATTERN: re.Pattern[str] = re.compile(r"^claude-[a-z0-9]+(-[a-z0-9]+)+$")

# AWS Bedrock cross-region inference-profile id pattern
# (``us.anthropic.claude-opus-5``, ``us.anthropic.claude-sonnet-4-5-20250929-v1:0``).
# Accepted only when ``use_bedrock: true``; rejected otherwise.
#
# Deliberately does NOT require a version suffix (issue #342). The previous
# pattern was ``^us\.anthropic\.claude-[a-z0-9-]+-v[0-9]+$``, which demanded a
# trailing ``-v<N>``; AWS does not name profiles uniformly, so that rejected two
# whole shapes of real id: current-generation profiles carry no version segment
# at all (``us.anthropic.claude-opus-5``, ``...-opus-4-8``, ``...-sonnet-5``),
# and dated profiles end ``-v1:0`` whose ``:0`` failed the ``$`` anchor. Measured
# against ``aws bedrock list-inference-profiles``: of 12 ACTIVE non-haiku
# ``us.anthropic.claude*`` profiles the old pattern accepted 1, pinning Bedrock
# operators to ``us.anthropic.claude-opus-4-6-v1`` while every current model
# failed at config load.
#
# What remains enforced is the part devbench actually depends on: the ``us.``
# cross-region prefix (see docs/llm-authentication.md "Region mismatch") and the
# ``anthropic.claude`` family. ``.`` and ``:`` are admitted so dated ``-v1:0``
# ids parse. Haiku stays rejected by ``validate_agent_model_value``'s own
# substring check, which runs before this pattern.
#
# This validator cannot confirm a model is ENABLED in the caller's account --
# that needs an API call, and config load must not make one -- so the contract
# is deliberately "structurally a Bedrock Claude id". A genuinely unavailable
# model surfaces at first invocation, where AWS names the real failure, rather
# than being guessed at here.
BEDROCK_AGENT_MODEL_PATTERN: re.Pattern[str] = re.compile(r"^us\.anthropic\.claude-[a-z0-9.:-]+$")

# ---------------------------------------------------------------------------
# Quota recovery probe (FR-2.5, issue #236, E2-F1-S2-T2)
# ---------------------------------------------------------------------------
# Model id targeted by ``devbench.quota.recovery_probe``'s 1-token
# ``messages.create`` call used to confirm whether an exhausted quota has
# cleared. DELIBERATE DIVERGENCE from the source branch
# (``origin/feat/flatten-review-pipeline:constants.py:711``), which pinned
# ``"claude-opus-4-8"``: spec decision D-2 moves the default model lineup to
# Opus 5, so this constant lands as ``"claude-opus-5"`` instead of copying
# the branch value. Do not "fix" this back to ``claude-opus-4-8`` -- that
# would silently un-do D-2.
RECOVERY_PROBE_MODEL: str = "claude-opus-5"

# HTTP timeout, in seconds, for ``devbench.quota.recovery_probe``'s
# ``messages.create`` call (FR-2.5, issue #236). Consumed by the
# ``functools.partial(recovery_probe, ...)`` call built in
# ``_handle_quota_pause`` (``src/devbench/cli.py``) so the probe's timeout
# is not a hardcoded literal at the call site. Must be > 0 -- enforced by
# ``recovery_probe``'s own argument guard.
RECOVERY_PROBE_TIMEOUT_SECONDS: float = 30.0

# Input token count for the 1-token probe prompt sent by
# ``devbench.quota.recovery_probe`` (FR-2.5, issue #236). Consumed by the
# same ``functools.partial(recovery_probe, ...)`` call in
# ``_handle_quota_pause`` (``src/devbench/cli.py``). Must be >= 1 --
# enforced by ``recovery_probe``'s own argument guard.
RECOVERY_PROBE_REQUEST_SIZE_TOKENS: int = 1

# ---------------------------------------------------------------------------
# Session-management constants (spec 4.4.1 named sessions, issue #192)
# Consumed exclusively by ``src/devbench/session.py``; defined here so no
# literal values appear inline in that module.
# ---------------------------------------------------------------------------
# Workspace-relative path to the base directory holding all session state dirs.
# Full path is ``<workspace_root>/<SESSION_SESSIONS_BASE_DIR>``.
# Consumed by ``src/devbench/log_setup.py`` (per-session log routing) and
# ``src/devbench/session.py`` (registry and per-session state directories).
SESSION_SESSIONS_BASE_DIR: str = ".devbench/sessions"

# Workspace-relative path to the session registry JSON file.
# Full path is ``<workspace_root>/<SESSION_REGISTRY_PATH>``.
SESSION_REGISTRY_PATH: str = ".devbench/sessions/registry.json"

# Filename of the exclusive flock file created under ``<workspace_root>/.devbench/``.
# Full path is ``<workspace_root>/.devbench/<SESSION_BACKLOG_LOCK_NAME>``.
SESSION_BACKLOG_LOCK_NAME: str = "BACKLOG.lock"

# Default timeout in seconds for acquiring the BACKLOG.lock via ``fcntl.flock``.
# Raises ``TimeoutError`` when the lock cannot be acquired within this window.
SESSION_DEFAULT_FLOCK_TIMEOUT_SECONDS: int = 30

# Filename of the PID file written inside each session's state directory
# (``<workspace_root>/.devbench/sessions/<name>/<SESSION_PID_FILENAME>``).
SESSION_PID_FILENAME: str = "pid"

# Suffix appended to the registry JSON path for the intermediate temp file
# used during atomic registry writes (write-then-rename pattern).
SESSION_REGISTRY_TMP_SUFFIX: str = ".tmp"

# Default session name used when --name is not supplied to ``cmd_start``.
# Consumed by ``src/devbench/session.py`` and ``cmd_start``; defined here so
# no literal strings appear inline in those modules.
SESSION_DEFAULT_NAME: str = "default"

# Filename written inside a session's state directory to record the ISO-8601
# timestamp at which the session was started.
# Full path: ``<workspace_root>/.devbench/sessions/<name>/<SESSION_STARTED_AT_FILENAME>``.
SESSION_STARTED_AT_FILENAME: str = "started_at"

# Filename written inside a session's state directory to record the identity
# (username / process tag) of the agent that started the session.
# Full path: ``<workspace_root>/.devbench/sessions/<name>/<SESSION_STARTED_BY_FILENAME>``.
SESSION_STARTED_BY_FILENAME: str = "started_by"

# Poll interval (seconds) between non-blocking flock attempts in
# :func:`devbench.session.flock_backlog`.  A sub-second value keeps the
# effective wait latency low without busy-spinning.  Callers use
# ``min(SESSION_FLOCK_POLL_INTERVAL_SECONDS, remaining)`` so the deadline is
# never overshot.  Override via ``DEVBENCH_SESSION_FLOCK_POLL_INTERVAL``
# env var if the default 0.1 s is too coarse or too fine for a given deployment.
SESSION_FLOCK_POLL_INTERVAL_SECONDS: float = 0.1

# Filename of the drain signal file written inside a per-session state directory.
# Full path: ``<workspace_root>/.devbench/sessions/<name>/<SESSION_DRAIN_SIGNAL_FILENAME>``.
# Consumed by ``src/devbench/drain.py`` (resolve_drain_signal_path) and
# ``src/devbench/cli.py`` (_session_drain_state_str).
SESSION_DRAIN_SIGNAL_FILENAME: str = "drain.signal"

# Relative path (from workspace root) of the orchestrator restart marker
# file written by ``cmd_start`` on every startup.  Issue #215: bounds the
# audit-row scan window in
# ``devbench.backlog.proposal._has_runtime_degradation_signal`` so RUNTIME_DEGRADATION
# classification clears on operator-driven restart.  The file contains a
# single ISO 8601 UTC timestamp string.
LAST_RESTART_MARKER_PATH: str = ".devbench/last-restart"

# Relative path (from workspace root) of the active-work-unit marker written
# by ``cmd_claim`` under ``flock(BACKLOG.lock)`` on every successful claim.
# Issue #336: gives ``guard-git-stage.sh`` rule 2 (manifest-scope enforcement
# on ``git add``) a production activation path -- hook processes inherit the
# long-lived orchestrator environment, so a per-work-unit environment
# variable can never be pinned for them.  The file contains a single line:
# the absolute path of the claimed work unit's ``.md`` file.  Named sessions
# get their own marker (``<path>-<DEVBENCH_SESSION_NAME>``) so concurrent
# sessions in one workspace never read each other's claim.  The marker is
# never cleared: the hook validates that the referenced unit still declares
# ``## Status: in-progress`` before enforcing, so a stale marker is a
# designed skip, not a false block.
ACTIVE_WORK_UNIT_MARKER_PATH: str = ".devbench/active-work-unit"

# ---------------------------------------------------------------------------
# Bounded skill iterate-until-perfect mechanism (spec section 4.6.0, issue #204)
# The four onboarding skills (create-spec, spec-to-backlog, bootstrap-environment,
# configure-devbench) each run a bounded self-critique loop. Iteration state is
# persisted per skill so a max-iterations exhaustion is observable as an audit
# row rather than buried in skill prose. Consumed by ``src/devbench/skill_state.py``
# and the four SKILL.md files in ``plugin/devbench-orchestrate/skills/``.
# ---------------------------------------------------------------------------

# Maximum number of self-critique iterations a skill may run before emitting
# the [SKILL_MAX_ITERATIONS_REACHED] audit row and exiting non-zero.
SKILL_MAX_ITERATIONS: int = 5

# Quality threshold (count of unresolved items) below which the skill is
# considered converged. Zero means "no unresolved items".
SKILL_QUALITY_THRESHOLD: int = 0

# Workspace-relative path to the base directory holding per-skill checkpoint
# files. Full path: ``<workspace_root>/.devbench/<SKILL_STATE_DIR_NAME>/<skill>.json``.
SKILL_STATE_DIR_NAME: str = ".devbench/skill-state"

# Audit-row tag emitted when a skill exhausts its iteration budget without
# reaching SKILL_QUALITY_THRESHOLD. Operator-visible signal that the skill
# needs human attention.
SKILL_AUDIT_MAX_ITERATIONS_REACHED: str = "[SKILL_MAX_ITERATIONS_REACHED]"

# Audit-row tag emitted when a skill converges (unresolved count <= threshold).
SKILL_AUDIT_QUALITY_THRESHOLD_REACHED: str = "[SKILL_QUALITY_THRESHOLD_REACHED]"


# ---------------------------------------------------------------------------
# Integration-reality gates (spec integration-reality-gates-hardening.md
# section 4.1; caylent-solutions/devbench-internal-backlog#10..#17; D-2,
# D-15, D-17). Built-in defaults for the eight gates' resolver-managed
# fields, consumed exclusively by ``config_loader.resolve_gate_config`` --
# the single read path for gate configuration (AC-27).
# ``config_loader.GATE_NAMES`` (a frozenset, used for O(1) membership
# checks) derives from the ordered tuple below rather than repeating the
# literal gate-name list a second time.
# ---------------------------------------------------------------------------

# Ordered, canonical list of the eight integration-reality gate names.
GATE_NAMES: tuple[str, ...] = (
    "reachability",
    "ancestry",
    "shared_file_impact",
    "fixture_consistency",
    "write_path_audit",
    "newly_reachable_paths",
    "composition_root",
    "layout_geometry",
)

# D-17: every gate disabled by default; every implemented tunable at its
# documented, equally-disabled default. ``fan_in_threshold`` (spec 4.6
# hardening) is reserved for a future gate epic (E5-F2-S1) and has no
# dataclass field yet, so it is intentionally not modeled here.
GATE_ENABLED_DEFAULT: bool = False
GATE_AUTO_DERIVE_REGISTRY_DEFAULT: bool = False
GATE_EXTRACT_SOURCE_LITERALS_DEFAULT: bool = False

# Built-in per-gate default field values, keyed by gate name then field
# name. Every gate carries "enabled"; `shared_file_impact` additionally
# carries "auto_derive_registry" and `fixture_consistency` additionally
# carries "extract_source_literals" -- the only tunables with a
# project/built-in precedence relationship today (spec 4.1). Structural,
# list-valued config (`canonical_sources`, `scan`, `patterns`) is
# project/per-repo-only with no built-in default to merge against, so it
# is intentionally absent here.
GATE_FIELD_DEFAULTS: dict[str, dict[str, bool]] = {
    "reachability": {"enabled": GATE_ENABLED_DEFAULT},
    "ancestry": {"enabled": GATE_ENABLED_DEFAULT},
    "shared_file_impact": {
        "enabled": GATE_ENABLED_DEFAULT,
        "auto_derive_registry": GATE_AUTO_DERIVE_REGISTRY_DEFAULT,
    },
    "fixture_consistency": {
        "enabled": GATE_ENABLED_DEFAULT,
        "extract_source_literals": GATE_EXTRACT_SOURCE_LITERALS_DEFAULT,
    },
    "write_path_audit": {"enabled": GATE_ENABLED_DEFAULT},
    "newly_reachable_paths": {"enabled": GATE_ENABLED_DEFAULT},
    "composition_root": {"enabled": GATE_ENABLED_DEFAULT},
    "layout_geometry": {"enabled": GATE_ENABLED_DEFAULT},
}

# DEVBENCH_GATE_<NAME>_ENABLED env-var name components (spec Section 7):
# workspace-wide, highest-precedence layer, resolved by
# ``devbench.config.resolve_gate_env_override`` through the existing
# ``_resolve_bool`` chain.
GATE_ENV_VAR_PREFIX: str = "DEVBENCH_GATE_"
GATE_ENV_VAR_SUFFIX: str = "_ENABLED"

# Per-field provenance labels rendered by the `devbench gates` provenance
# column (spec 4.1, D-15; AC-27) -- which of the four precedence layers set
# a resolved field.
GATE_PROVENANCE_BUILTIN: str = "builtin"
GATE_PROVENANCE_PROJECT: str = "project"
GATE_PROVENANCE_REPO: str = "repo"
GATE_PROVENANCE_ENV: str = "env"

# ---------------------------------------------------------------------------
# Gate tier taxonomy (spec integration-reality-gates-hardening.md section
# 4.2, D-6; AC-E2-F2-S1-T1-4, AC-E2-F2-S1-T1-5). Three tiers describe how
# strongly a gate's outcome is trusted:
#   - machine-blocking: a passing `[GATE_PASS <gate>]` record
#     (`devbench.gate_records`) is REQUIRED before `mark_done` proceeds
#     (E2-F2-S1-T2's `_check_gate_pass_done_invariant`).
#   - judge-evidence: the gate's findings inform the review judges but do
#     not themselves block `mark_done`.
#   - advisory: informational only. No gate carries this tier today -- D-6
#     assigns only the two tiers above to the eight declared gates -- but
#     the symbol exists so a future gate can adopt the weakest tier without
#     inventing a fourth taxonomy value.
# PM-3: these are named importable symbols, not inline strings, because the
# `gates` CLI table, each gate command's output line, and the judge/docs
# vocabulary tests (E2-F2-S2-T1) all cross-reference them.
# ---------------------------------------------------------------------------
GATE_TIER_MACHINE_BLOCKING: str = "machine-blocking"
GATE_TIER_JUDGE_EVIDENCE: str = "judge-evidence"
GATE_TIER_ADVISORY: str = "advisory"

# D-6's machine-blocking set, declared once as the single input GATE_TIERS
# below is built from. Keeping this private and gate-name-only (rather than
# writing GATE_TIERS as an independent literal dict) means GATE_TIERS's keys
# can never drift from GATE_NAMES: a ninth gate added to GATE_NAMES without a
# tier decision here automatically lands in the weaker judge-evidence tier
# instead of being silently absent from GATE_TIERS.
_GATE_TIER_D6_MACHINE_BLOCKING_NAMES: frozenset[str] = frozenset(
    {"reachability", "ancestry", "shared_file_impact", "fixture_consistency"}
)

# Declares the tier of all eight gates (spec 4.2, D-6): reachability,
# ancestry, shared_file_impact and fixture_consistency are machine-blocking;
# write_path_audit, newly_reachable_paths, composition_root and
# layout_geometry are judge-evidence.
GATE_TIERS: Mapping[str, str] = {
    gate: (GATE_TIER_MACHINE_BLOCKING if gate in _GATE_TIER_D6_MACHINE_BLOCKING_NAMES else GATE_TIER_JUDGE_EVIDENCE)
    for gate in GATE_NAMES
}

# ---------------------------------------------------------------------------
# Gate status vocabulary (spec integration-reality-gates-hardening.md
# section 5.2, 4.1). Every gate command's spec 5.2 status line and spec 4.1
# disabled line report ``status`` as exactly one of these three values;
# declared once here, beside ``GATE_NAMES``/``GATE_TIERS``, so per-gate
# command implementations in ``cli.py`` (``cmd_check_ancestry``,
# ``cmd_check_reachability``, and future gate commands) share a single
# source instead of each declaring its own byte-identical
# ``_<GATE>_STATUS_*`` constants.
# ---------------------------------------------------------------------------
GATE_STATUS_DISABLED: str = "disabled"
GATE_STATUS_PASS: str = "pass"
GATE_STATUS_FAIL: str = "fail"

# ---------------------------------------------------------------------------
# GATE_WAIVER marker attribution vocabulary (spec 3.6, 4.9; D-6). A
# ``[GATE_WAIVER <gate>]`` marker's attribution field is exactly one of
# these two values: "operator" is the only waiver authority for a
# machine-blocking gate (``GATE_TIER_MACHINE_BLOCKING`` above) -- an
# "executor"-attributed marker alone never satisfies one, since executors
# do not self-certify a machine-blocking gate's outcome. Single-sourced
# here so ``devbench.backlog.manager`` (``compose_gate_waiver_record`` /
# ``parse_gate_waiver_record``'s grammar and the ``mark_done`` gate-record
# invariant) and ``devbench.cli`` (``log-waiver``'s ``--operator`` flag and
# each machine-blocking gate command's waiver-adoption filter, e.g.
# ``check-reachability``) share one vocabulary instead of each re-declaring
# the literal ``"operator"``/``"executor"`` strings.
# ---------------------------------------------------------------------------
GATE_WAIVER_ATTRIBUTION_OPERATOR: str = "operator"
GATE_WAIVER_ATTRIBUTION_EXECUTOR: str = "executor"
