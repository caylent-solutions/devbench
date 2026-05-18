# Changelog

All notable changes to devbench are documented in this file. Format is
based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

## [Unreleased] -- v-next

This release bundles the orchestrator self-healing work, the canonical
configuration refactor, the EC2 remote-dev provisioning stack, and the
work-unit lifecycle / authoring CLI improvements that have accumulated
since the last release. PR #119 carries every change.

### Added

- **Operator-facing Slack notifications** (PR #202) — toggleable per-event
  Slack pings on every interesting lifecycle event: work-unit done,
  work-unit blocked-and-operator-action-required, work-unit materialised
  / promoted, PR opened / merged, CI failure, orchestrator stop (clean,
  drain, SIGTERM, or crash — always-fire on exit), orchestrator
  auto-restart, quota pause, quota resume. New `notifications:` yaml
  block with one independent boolean toggle per event; webhook URL +
  Slack user-id flow through `DEVBENCH_NOTIFICATIONS_SLACK_WEBHOOK_URL`
  and `DEVBENCH_NOTIFICATIONS_SLACK_USER_ID` env vars so credentials
  never touch tracked yaml. New `devbench notify-test --event <name>`
  CLI for smoke-testing setup. See `docs/slack-notifications.md` for
  the full operator walkthrough.

- **`make start` auto-restarts on SDK `RUNTIME_DEGRADATION`-only
  `NO_ACTIONABLE` exits** (issue #183 follow-up; pairs with the renderer +
  ETA fixes below). The orchestrate skill exits cleanly on `NO_ACTIONABLE`
  whenever no actionable work remains, and previously the Makefile's
  `start` target ran `uv run python -m devbench.cli start` exactly once --
  so a session ending purely because every remaining blocker classifies as
  `BlockedTaskState.RUNTIME_DEGRADATION` (the SDK subprocess lost
  Agent-tool access mid-session, recoverable by a fresh subprocess) would
  stop the orchestrator until an operator noticed and re-ran `make start`.
  `cmd_start` now inspects the backlog post-mortem after `asyncio.run`
  returns: when there is at least one `RUNTIME_DEGRADATION` blocker AND
  zero `IN_PROGRESS` / `IN_REVIEW` tasks AND zero
  `OPERATOR_ACTION_REQUIRED` blockers, it writes one
  `[ORCHESTRATOR_AUTO_RESTART] reason=runtime_degradation tasks=<ids>`
  audit line to `logs/orchestrator.log` and returns the new
  `ORCHESTRATOR_RESTART_EXIT_CODE` (42). The `start` target wraps the CLI
  call in a bounded while-loop: exit code 42 triggers a restart (printing
  `INFO: orchestrator auto-restart (attempt N/max)` to stderr), any other
  code is passed through unchanged, and the cap defaults to
  `DEVBENCH_MAX_AUTO_RESTARTS=3` (override via env). After the cap, the
  Makefile fails fast with rc=1 and an `ERROR: orchestrator hit
  RUNTIME_DEGRADATION restart cap` message naming the SDK subprocess
  Agent-tool loss as the thing to investigate. Pinned by
  `tests/test_cli.py::TestCmdStartAutoRestartPostMortem`,
  `TestShouldAutoRestartPostMortem` (7 cases covering each precondition),
  and the new `tests/test_integration/test_make_targets.py` Makefile-loop
  integration tests (cap-exhaustion, restart-then-succeed, non-42
  pass-through).

- **Per-agent model overrides via `agents:` block in `devbench.yaml`** (ADR-25).
  Each of the ten work agents (executor, blocker-resolver, manifest-amender,
  security-reviewer, task-factory, review-supervisor, plus the four
  review_team judges) can be retargeted to a different model independently of
  its `.md` frontmatter default. Lets operators with uneven per-model quota
  drive specific agents on opus while leaving the rest on sonnet, etc. A
  workspace-local shadow plugin tree is materialised at
  `<workspace>/.devbench/plugin-shadow/devbench/` on every launch -- agent
  files whose model is overridden are written as plain files with the
  `model:` frontmatter line rewritten; every other plugin file is symlinked
  back to the canonical install. New CLI command `devbench
  prepare-plugin-shadow` builds the same shadow standalone for interactive
  launchers (`claude --plugin-dir "$(devbench prepare-plugin-shadow)"`).
  `DEVBENCH_AGENT_MODEL_<NAME>` (or `JUDGE_AGENT_MODEL_<NAME>` for the five review judges + review-supervisor) env vars override the YAML block on a per-call
  basis (env > yaml > frontmatter). Defaults preserved: workspaces without
  an `agents:` block build no shadow and use the canonical plugin path,
  bit-identical to pre-feature behaviour. See `docs/adr/25-per-agent-model-overrides.md`.
- **`ScopeFilter` scope selector module** (issue #190). New
  `src/devbench/scope.py` dataclass providing a persistent allow/deny
  scope filter for work-unit execution. Public API: `ScopeFilter`
  dataclass with `parse(include_str: str, exclude_str: str, backlog_ids: list[str])`,
  `allows(unit_id: str) -> bool`, `to_file(workspace_root)`,
  `from_file(workspace_root)`, and `clear(workspace_root)` methods.
  `InvalidScopeError` raised on malformed scope expressions (e.g.
  reversed ranges). Scope state is persisted to
  `<workspace_root>/.devbench/scope.json` so the filter survives
  orchestrator restarts.
- **`git_ops.local_only` mode**. New config flag for target repos that
  have no `origin` git remote -- typical for operational workflows
  (AWS teardowns, evidence capture, audits) where devbench drives the
  work but no application code is being authored. When `local_only:
  true`, `ensure-branch` skips `git fetch origin` and creates the
  work-unit branch off the local default ref; `git-ops` commits
  locally only; remote-touching helpers (`commit_and_push`,
  `create_tag`, `checkout_default_branch`, `rebase_and_force_push`)
  are guarded with a clear `RuntimeError`; the `devbench check`
  pre-flight inverts its origin assertion (a present remote is now
  the error). Requires `defer_pr: true` and an explicit
  `default_branch:` per repo. See `docs/operational-work.md` and
  `docs/git-ops-modes.md`.
- **DRAFT lifecycle status** (E1, issue #189). New `WorkUnitStatus.DRAFT`
  enum value placed before `IN_QUEUE` in lifecycle order. Allows work
  units to be authored and held in `draft` state before entering the
  active queue. Full parser and CLI support follows in sibling tasks.
- **`validate-backlog` enforces Task-only draft status** (E1-F2-S2, issue #189, AC-189-10).
  `BacklogManager._check_status_enum` now rejects `draft` as a status for
  Epic, Feature, and Story work units with an explicit error message:
  `<id>: Status "draft" is only valid for Task work units; <id> is type <Epic|Feature|Story>.`
  Task-level work units (IDs containing `-T<digits>`) continue to accept `draft`
  without error (AC-189-2).
- **`BacklogConfig` dataclass + `backlog.default_status_for_new_work_units` config key** (E1-F5, issue #189, AC-189-8/9).
  New `BacklogConfig` frozen dataclass in `config_loader.py` with field
  `default_status_for_new_work_units` (accepted values: `'draft'` or
  `'in-queue'`). Parsed from the optional `backlog:` YAML section in
  `backlog/config/devbench.yaml` by `_parse_backlog_config()` and
  stored on `RuntimeConfig.backlog`. Defaults to `'in-queue'` when the
  section is absent, preserving backwards compatibility (AC-189-9).
  Set to `'draft'` to require explicit human promotion before the
  orchestrator picks up newly created work units (AC-189-8). Invalid
  values raise `ValueError` with an actionable error message listing
  the accepted choices.
- **HOLD lifecycle status** (E222, issue #104). New `WorkUnitStatus.HOLD`
  + `devbench hold <id>` / `devbench unhold <id>` CLI for tasks
  deliberately deferred without breaking dep-chain math.
- **`RepoConfig.resolved_checkout_path`** (E213, issue #105) and
  `validated_repo` enrichment so consumers stop re-resolving paths
  inline.
- **Dependency integrity + `devbench sync-blocked`** (E215, issue #107):
  recursive `_deps_satisfied` walk + bulk transition of in-progress
  tasks whose deps regressed.
- **Branch uniqueness validation** (E219, issue #108): validate-backlog
  rule rejects backlogs whose work-unit Branch fields collide.
- **`devbench status --detail` panels** (E220, issue #109): three panels
  (in-queue / blocked / held) for richer status overview.
- **`devbench new-task` scaffolder** (E223, issue #110) +
  `backlog/templates/{epic,feature,story,task}.md` template files.
- **3-state blocked-task classifier**: `AUTO_CLEARING_VIA_PROPOSAL` /
  `AWAITING_AUTO_RECOVERY` / `NEEDS_OPERATOR_ATTENTION`. Classifier
  signals: pending proposal JSON on disk, rejected-amendment archive,
  recent recovery audit comment.
- **Remote EC2 provisioning stack**: full Terraform + Terragrunt +
  Ansible suite for unattended / multi-operator orchestrate runs.
  `devbench-session` per-user multi-session launcher. End-to-end guide
  at `docs/remote-ec2-setup.md`.
- **E230 `DEVBENCH_ORCHESTRATOR_SESSION_ID` filter**: hook-tail
  `--orchestrator-only` flag isolates events per orchestrator session.
- **Inline orphan-cleanup chore commit (Phase 1)**: `cmd_git_ops` now
  runs `cleanup_tracked_orphans` programmatically and lands a chore
  commit (`chore(cleanup): untrack devbench-managed orphan paths and
  update .gitignore`) before the task's commit when build/state orphan
  paths are detected. Two commits land per `git-ops` invocation. The
  cleanup is no longer a backlog work unit.
- **CI-failure executor retry (issue #115)**: on CI failure,
  `cmd_git_ops` writes the trimmed failing-job log under
  `.devbench/ci-failures/<id>-<n>.log`, appends a `[CI_FAIL]` audit
  comment, and returns rc=2 to signal the orchestrator to re-invoke
  the executor with a `ci-fail` feedback payload. Bounded by
  `MAX_RETRY_ATTEMPTS`.
- **PR review-comment polling (issue #116)**: between CI-pass and
  merge, `cmd_git_ops` polls `gh pr view` and `gh api .../comments`
  for asynchronous bot review feedback (Copilot, Q-Dev, internal review
  bots). On signal, writes a `pr-bot` JSON feedback payload to
  `.devbench/pr-bot-feedback/<id>-<n>.json`, appends a `[PR_BOT_FAIL]`
  audit comment, and returns rc=3.
- **YAML `debug:` section**: diagnostic-tuning knobs
  (`check_registration_retries`, `check_registration_delay_seconds`,
  `blocked_recovery_window_seconds`) gain a stable workspace-level
  setting alongside their env vars.
- **YAML promotion of every PR-119 toggle**: every orchestrator
  feature flag now resolves env > YAML > default. Twelve toggles
  promoted: `git_ops.inline_orphan_cleanup`, `git_ops.ci_failure_retry`,
  `git_ops.pause_before_merge` (#101 schema only; impl is a follow-up),
  `git_ops.orphan_patterns`, `git_ops.pr_review_resolution.{enabled,
  agents, decision_blocks, settle_seconds, poll_interval}`,
  `limits.ci_failure_log_bytes`, and the three `debug.*` knobs.
- **Comprehensive `sample-config.yaml`**: every YAML field defined in
  `config-schema.json` now appears in the sample with its default
  value. Schema-completeness is enforced by
  `tests/test_config_loader.py::TestSampleConfigCompleteness`.
- **`_resolve_bool` helper**: env-var boolean parser with strict
  truthy / falsy validation. Misconfigurations fail fast at process
  start with a `ValueError`.
- **Recovery-proposal dedup** (issue #141). `cmd_write_proposal`
  computes a stable `fix_signature` over `(target_repo,
  sorted(files_to_own), normalised intent_phrase)` and scans pending
  proposals for a match. On hit, the new source task is auto-wired
  to the existing recovery via `add-dep` and the duplicate JSON is
  not written; on miss, the signature is stamped into the proposal
  before persisting. Pinned by `tests/test_backlog/test_proposal_dedup.py`
  and `tests/test_backlog/test_proposal_scanner.py`.
- **Manifest-amender auto-dep** (issue #142). When the conflict task
  is in a terminal state (`done` / `declined`), the amender now
  auto-invokes `uv run devbench add-dep <source-task-id>
  <conflict-task-id>` and emits `[CONFLICT_AUTODEP]` instead of
  recommending the operator wire it. Failure surfaces as
  `[CONFLICT_AUTODEP_FAILED]`. Pinned by
  `tests/test_integration/test_manifest_amender_pre_conflict.py`.
- **Materialise-time placeholder rejection** (issue #143).
  `cmd_materialise_proposal` rejects proposals whose
  `proposed_tasks[*].suggested_approach` is empty, whitespace-only,
  TODO, or TBD before any draft reaches the operator. Pinned by
  `tests/test_backlog/test_proposal_lifecycle_hardening.py` +
  `tests/test_cli.py::TestCmdMaterialiseProposalLifecycleGates`.
- **Bounded recovery-cascade depth** (issue #144). Proposals carry a
  `cascade_depth` field (`parent_depth + 1`); the new
  `orchestrate.max_cascade_depth` YAML knob (default 2, env override
  `DEVBENCH_ORCHESTRATE_MAX_CASCADE_DEPTH`) caps recursion. At cap, the
  source task transitions to `NEEDS_OPERATOR_ATTENTION` rather than
  materialising a deeper draft. Pinned by
  `tests/test_backlog/test_proposal_lifecycle_hardening.py::TestEnforceCascadeDepth`.
- **Backlog-repo recovery skip** (issue #146). `cmd_write_proposal`
  drops proposed-task entries whose `files_to_own` all live in the
  backlog repo (i.e., not in any configured target repo's
  `checkout_directory`). Backlog-repo edits (e.g. `spec/*.md`,
  `BACKLOG.md`, `backlog/**/*.md`, `docs/*.md`) are operator
  bookkeeping commits, not work-unit deliverables; the recovery
  cascade has no valid completion path for them. When every proposed
  task is skipped, the JSON envelope reports `recovery_skipped:
  true` and no proposal JSON is written. Mixed entries are pruned to
  their target-repo files. Logged as
  `[RECOVERY_SKIPPED_BACKLOG_REPO_FILES]`. Pinned by
  `tests/test_cli.py::TestCmdWriteProposalBacklogRepoSkip`.
- **`devbench reconcile-cascade` CLI command** (issue #150). Walks every
  blocked task, evaluates marker target states + regular dep states, and
  flips eligible tasks (markers all terminal AND deps satisfied) to
  in-queue with a `[CASCADE_RECONCILED]` audit comment. Returns a JSON
  envelope listing flips + skips with reasons. Operator-friendly
  recovery for blocked tasks the cascade missed (process crash, missing
  declared dep, etc.). Pinned by
  `tests/test_cli.py::TestCmdReconcileCascade`.
- **Manifest-amender rejection feedback persistence** (issue #154). Each
  `reject_amendment` call writes a structured JSON to
  `<workspace>/.devbench/amender-rejections/<task-id>-<n>.json` with
  `task_id`, `attempt`, `reason_category` (`SCOPE` /
  `APPROACH_AUTH` / `JUSTIFICATION_COHERENCE` / `PRE_FILTER` /
  `OTHER`), `reason_text`, and the original request payload. The
  blocker-resolver / executor-feedback collector ingests these on
  retry. Bounded by `MAX_RETRY_ATTEMPTS`; over-cap records still
  written but stamped `capped: true`. Pinned by
  `tests/test_backlog/test_amendment.py::TestAmenderRejectionPersistsFeedbackJson`.
- **Review-judge structured rejection feedback** (issue #156). New
  `devbench log-rejection-feedback <judge> <id> --json '<payload>'`
  CLI primitive validates the payload against
  `src/devbench/backlog/review-feedback-schema.json` (schema_version 1)
  + the per-judge controlled vocabulary in
  `src/devbench/backlog/review_feedback_vocabulary.py`, and persists
  to `.devbench/review-failures/<task-id>-<judge>-<n>.json`. The
  manifest-amender path migrates to the same shared directory; the
  legacy `.devbench/amender-rejections/` location remains as a
  forward-compat read path. The done-gate refuses
  `mark-done` until every prior rejection category is cleared via a
  `[REJECTION_FEEDBACK_RESOLVED] <judge>:<code>` audit OR escalated
  via `[NEEDS_DEP] <judge>:<code>`. `devbench status --detail`
  surfaces unresolved counts per blocked task. New per-judge
  vocabulary documented in `docs/review-feedback-vocabulary.md`.
- **In-progress attempt duration** (issue #158). `devbench status`,
  `devbench status --detail`, and `devbench report`'s in-progress
  panel now suffix every in-progress row with
  `(in-progress for 23m)` / `(in-progress for 1h 47m)` etc. When
  neither the structured log nor the work-unit audit comments yield
  a parseable timestamp the row reads `(in-progress, timer
  unavailable)` -- never silently omitted.
- **Orchestrator-alive status banner at top of `devbench report`**
  (issue #161). One-line banner derived from log-activity recency:
  `[ORCHESTRATOR ALIVE]` (green) when the last log line is within
  `stop_hook.window_seconds`, `[ORCHESTRATOR STOPPED]` (red) when
  past that window (with elapsed-since duration + last-seen
  timestamp), `[ORCHESTRATOR STARTING]` (yellow) when the log file
  is missing or empty. Includes the `DEVBENCH_ORCHESTRATOR_SESSION_ID`
  suffix when set so multi-session operators can tell which session
  the report monitors. ANSI colour only when stdout is a TTY; pipes
  / CI redirects receive plain text. Refreshes on every
  `--watch N` tick. Threshold reuses `stop_hook.window_seconds`
  rather than introducing a redundant config knob, which guarantees
  the banner stays aligned with the operator's already-tuned
  circuit-breaker quiet window (e.g., a 180s window tolerates a
  3-minute terraform-apply quiet stretch without flashing STOPPED).

### Removed

- **`quota_handling.notify_on_pause` / `quota_handling.notify_on_resume`
  yaml fields** (PR #202) — superseded by the unified `notifications:`
  block above. The dispatcher, `QuotaNotifyConfig` dataclass, and
  `deliver_notifications` helper were also removed (per CLAUDE.md
  "Complete Replacement of Superseded Code"). Set
  `notifications.events.quota_pause: true` and
  `notifications.events.quota_resume: true` to receive Slack pings on
  those events; webhook URL and Slack user-id flow through the new
  env-var pair documented in `docs/slack-notifications.md`.

### Changed (BREAKING)

- **`haiku` is rejected at config-load for every per-agent field** (#198).
  Any `agents:` block value in `devbench.yaml` (or `DEVBENCH_AGENT_MODEL_*`
  env var) that contains `haiku` -- whether the short name `"haiku"`, a full
  Anthropic API id such as `"claude-haiku-4-5-20251001"`, or a Bedrock ARN
  containing `haiku` -- now raises a `ValueError` at config-load and prevents
  the orchestrator from starting. There is no operator-facing override path.

  **Why:** Under load the Claude Agent SDK was repeatedly observed to silently
  drop the `Agent` tool from haiku's tool list mid-orchestration, causing
  `RUNTIME_DEGRADATION` audit events and forcing the orchestrator to classify
  work units as blocked. Two tasks (E1-F4-S1-T3 and E4-F1-S1-T5) hit the
  pattern across four separate restarts within one evening. The prior release
  promoted the `review-supervisor` frontmatter default from `haiku` to
  `sonnet` and added advisory warnings; this release converts the advisory
  into a hard config-load rejection so the failure mode cannot be reintroduced
  by an operator YAML change.

  **Migration:** Replace any `haiku` value in `agents:` or `review_team:` with
  `sonnet` (minimum recommended) or `opus`. The same applies to
  `DEVBENCH_AGENT_MODEL_*` env vars. `RECOVERY_PROBE_MODEL` in
  `constants.py` is not affected -- the probe uses haiku intentionally for
  its minimal 1-token latency check, not as a work agent.

- **Canonical environment-variable namespace is `DEVBENCH_*`** (#197).
  Every operational env var is named `DEVBENCH_<NAME>` and is read by
  `config.py` / `config_loader.py` / `log_setup.py` via simple
  `os.environ.get` calls. Missing required vars (workspace root, Claude
  model) fail fast at module-import time with a message that names the
  expected var. No alias or fallback layer.

  **Identifiers preserved under the LLM-as-judge concept** (kept as
  `JUDGE_*` because they refer to judges, not to the environment-variable
  namespace): `KNOWN_JUDGE_NAMES`, `REVIEW_JUDGE_NAMES`,
  `SECURITY_JUDGE_NAMES`, `ALL_REQUIRED_JUDGE_NAMES`,
  `WORKFLOW_AGENT_JUDGE_NAMES`, `JUDGE_CATEGORIES`, `JUDGE_SEVERITY_ORDER`,
  the per-judge model env vars
  (`JUDGE_AGENT_MODEL_{CODE_REVIEWER, TEST_REVIEWER, DOC_REVIEWER,
  SECURITY_REVIEWER, CHANGES_MANIFEST, REVIEW_SUPERVISOR}`), and the
  `[JUDGE_*_VERDICT]` audit-row tag format.

### Changed (model defaults)

- **`review-supervisor` frontmatter default is now `sonnet`** (was `haiku`).
  Empirical observation in the self-improve workspace: under load the
  Claude Agent SDK was repeatedly observed to silently drop the `Agent`
  tool from haiku's tool list mid-orchestration, leaving review-supervisor
  unable to dispatch the four review_team judges and forcing the
  orchestrator to classify the work-unit as `RUNTIME_DEGRADATION` (issue
  #183 follow-up). Promoting to `sonnet` closes the failure mode without
  the cost of `opus` (this is still pure fan-out coordination, not
  judgment-heavy work). ADR-25, the sample config, `docs/cli-reference.md`,
  `docs/zero-to-ready.md`, `docs/llm-authentication.md`, and the
  brownfield example launcher all updated to reflect the new default and
  to warn operators against pinning ANY work agent to `haiku`. The short
  name `haiku` is still accepted by the YAML parser so operators can
  experiment, but every documented role default avoids it.

### Added

- **Bounded skill iterate-until-perfect mechanism** (issue #204, spec
  section 4.6.0). The four onboarding skills (`create-spec`,
  `spec-to-backlog`, `bootstrap-environment`, `configure-devbench`)
  previously described their self-critique loops in `SKILL.md` prose only,
  with no observable iteration bound or escalation audit. The loop is now
  enforced by a small support module plus five new constants:

  - `src/devbench/constants.py` adds `SKILL_MAX_ITERATIONS`,
    `SKILL_QUALITY_THRESHOLD`, `SKILL_STATE_DIR_NAME`,
    `SKILL_AUDIT_MAX_ITERATIONS_REACHED`, and
    `SKILL_AUDIT_QUALITY_THRESHOLD_REACHED`.
  - `src/devbench/skill_state.py` (new module) provides `SkillState`
    dataclass plus `read_checkpoint`, `write_checkpoint` (atomic
    temp-then-rename), and `emit_audit` (structured audit row appended
    to the orchestrator log).
  - Each skill's `SKILL.md` and `docs/skills/*.md` now documents a
    `## Self-critique loop (bounded)` section that names the constants and
    explains the read-increment-write-audit-terminate sequence.
  - `tests/test_skill_state.py` (new) drives the helpers to 100% line +
    branch coverage; `tests/test_constants.py` asserts the new constants
    are present with the right shapes; `tests/test_plugin/test_skill_structure.py`
    asserts every onboarding `SKILL.md` documents the bounded loop and
    references the constant symbols.

  Operators see iteration exhaustion as `[SKILL_MAX_ITERATIONS_REACHED]`
  in the existing `devbench report` / `devbench hook-tail` streams; no new
  infrastructure is required.

### Fixed

- **`devbench status` Backlog Status Summary count column right-aligns to a
  single column across every row** (issue #201). Before this fix three
  different label-pad widths (`{label:<15}` for top-level rows, `{label:<28}`
  for Blocked sub-rows) put counts in two different columns ("In Queue" at
  col ~19 vs "Blocked (auto-clearing)" at col ~32) and the longest label
  (`Blocked (runtime-degradation)`, 29 chars) overran the 28-wide pad so
  the count was jammed against the label with no breathing space. The fix
  introduces `STATUS_SUMMARY_LABEL_WIDTH: int = 32` in
  `src/devbench/constants.py` and applies it uniformly across the five
  format-string sites in `cmd_status` (TOTAL, Draft, top-level, Blocked
  sub-rows, Un-materialised). A new
  `tests/test_cli.py::TestCmdStatusSummaryAlignment` class pins the
  contract: every count value lands at the same column index, the longest
  label is followed by at least one space, the separator spans the count
  column, and no hard-coded `:<15` / `:<28` format spec remains in
  `cmd_status` (regression guard for future Blocked sub-bucket additions).

### Tests

- **Direct coverage for `quota._http_post`** (issue #203). `_http_post` is
  the network-level helper that `post_webhook` delegates to (HTTPS / HTTP
  branch, path + query construction, `try` / `finally` close). Earlier
  tests stubbed `_http_post` itself rather than driving it; the new
  `TestHttpPostInternals` class in `tests/test_quota.py` patches
  `http.client.HTTPSConnection` and `HTTPConnection` with `MagicMock`
  factories and exercises every branch end-to-end without real network
  I/O. Closes the only remaining coverage gap and brings
  `src/devbench/quota.py` to 100% line + branch coverage.

### Fixed

- **Three-defect fix for blocked tasks with satisfied `[BLOCKED_PENDING_PROPOSAL]`
  markers not auto-clearing** (issue #200). Before this fix, a task that was
  blocked via a `[BLOCKED_PENDING_PROPOSAL] <child>` marker could remain
  stuck in `blocked` after `<child>` reached `done`, requiring manual
  operator intervention to flip it back to `in-queue`.

  Three root causes addressed:

  1. **Classifier fallthrough** (`src/devbench/backlog/proposal.py`
     `classify_blocked_task` / `_classify_with_markers`): when ALL
     `[BLOCKED_PENDING_PROPOSAL]` marker targets were terminal (`done` /
     `declined`), `_classify_with_markers` returned `None`, causing the
     task to fall through to `OPERATOR_ACTION_REQUIRED` instead of
     `AUTO_CLEARING_VIA_PROPOSAL`. The fix: when all markers are terminal
     AND no regular dep or recovery signal exists, the classifier now
     returns `AUTO_CLEARING_VIA_PROPOSAL` (AC-200-1).

  2. **Cascade trigger condition** (`src/devbench/backlog/manager.py`
     `_auto_requeue_marker_dependents`): the cascade required the
     newly-done task to be in the blocked task's Dependencies table. When
     task-factory wired the dep via a marker comment only (no
     Dependencies-table row), the cascade never fired. The fix: condition 2
     now accepts the newly-done task appearing EITHER in the Dependencies
     table OR as a `[BLOCKED_PENDING_PROPOSAL]` marker ID in the Comments
     section (AC-200-2).

  3. **Marker regex too broad** (`_BLOCKED_PENDING_PROPOSAL_RE` in
     `manager.py`): the original `\S+` capture group matched any
     non-whitespace word, including prose words like "Amendment" in lines
     such as `[BLOCKED_PENDING_PROPOSAL] Amendment rejected ...`. This
     injected fake non-terminal marker IDs that prevented the cascade from
     firing. The fix: the capture group now matches only canonical task IDs
     (`E\d+(?:-F\d+)?(?:-S\d+)?(?:-T\d+)?`) (AC-200-3).

  Additionally: **`[AMENDMENT_REJECTED]` structured-tag audits now trigger
  `AWAITING_AMENDMENT_RECOVERY`** (`src/devbench/backlog/proposal.py`
  `_REJECTION_TAG_RE`). Previously, manifest-amender's structured-tag audit
  `[AMENDMENT_REJECTED] tdd_green_production_fix; rejected: POST_CHECK: ...`
  was not matched by `_RECOVERY_BODY_RE` (which required prose like
  `amendment rejected` without brackets). A new `_REJECTION_TAG_RE` matcher
  recognises the structured-tag form explicitly (AC-200-4).

- **`devbench report` / `watch` no longer crash on transient WU md
  `FileNotFoundError`**. The user-reported scenario: `devbench watch`
  was running against an active orchestrator; one tick caught a WU md
  in the middle of a non-atomic SDK-driven `Write` / `Edit` tool
  call, the parser raised `FileNotFoundError`, and the watch session
  died with the misleading prefix `devbench report: cannot parse
  '/.../BACKLOG.md': [Errno 2] No such file or directory:
  '/.../E4-F1-S1-T5.md'` (the prefix named the index but the missing
  path was a WU md). Two surgical fixes: (1)
  `BacklogParser.parse_index` (`src/devbench/backlog/parser.py:189-200`)
  now performs a single-shot synchronous retry on
  `FileNotFoundError` from `parse_work_unit_file` -- zero sleep, zero
  temporal logic, just an immediate second attempt that closes the
  atomic-rename / writer-window race. Persistent FNF still
  propagates with the original missing path intact, preserving
  fail-fast. (2) `generate_report`'s exception wrapper
  (`src/devbench/reporting/report.py:2249-2278`) splits into two
  branches: `FileNotFoundError` surfaces the actual missing path
  (`exc.filename` when present, otherwise `str(exc)`) with a hint
  noting the writer-window race; `ValueError` keeps the original
  `validate-backlog` hint for genuine index corruption. The fixes
  benefit every `BacklogParser` caller -- `report`, `watch`,
  `status`, `cmd_check` -- transparently. Pinned by
  `tests/test_backlog/test_parser.py::TestParseIndexFNFRetry` (2
  cases: transient recovers, persistent propagates) plus the new
  WU-md-path-aware case in
  `tests/test_reporting/test_report.py::TestGenerateReportBacklogParseFailure::test_file_not_found_naming_wu_md_surfaces_wu_path`.

- **ETA denominator now includes `RUNTIME_DEGRADATION` tasks**
  (issue #183 follow-up, paired with the renderer-bucketing fix above).
  `_compute_window_stats` previously computed
  `eta_task_count = tasks_active + tasks_blocked_recovery + tasks_blocked_auto`,
  excluding `RUNTIME_DEGRADATION` from the auto-recoverable denominator
  even though an operator restart of `make start` clears it -- so the
  bucket is auto-recoverable in exactly the same sense as
  `AWAITING_AMENDMENT_RECOVERY`, `AWAITING_DEPENDENCY`, and
  `AUTO_CLEARING_VIA_PROPOSAL`. The trailing-summary `attn_blocked`
  formula had the matching gap and was treating runtime-degradation
  tasks as operator-attention work. `_compute_window_stats` gains a
  `tasks_blocked_runtime_degradation` parameter; `WindowStats` gains an
  `eta_blocked_runtime_degradation` field; `eta_task_count`,
  `eta_total`, and `attn_blocked` all grow the new term consistently;
  `_format_est_hours_display`'s breakdown suffix surfaces the new
  bucket. All three call sites in `_render_report` thread
  `backlog.tasks_blocked_runtime_degradation` through. Pinned by
  `tests/test_reporting/test_report.py::TestEtaIncludesBlockedRecoveryAndAuto::test_compute_window_stats_uses_combined_denominator`
  (extended) and the new
  `test_runtime_degradation_changes_eta_total`.

- **`devbench report` now buckets `RUNTIME_DEGRADATION` correctly**
  (issue #183 follow-up). The classifier `classify_blocked_task` already
  returned `BlockedTaskState.RUNTIME_DEGRADATION` for tasks whose
  Comments contained a recent `[BLOCKED] agent-tool-unavailable` audit
  from review-supervisor's Step 0 self-check, but the report renderer's
  two routing paths (`_classify_blocked_unit_into_buckets` and
  `_backlog_totals_from_units`) used an `if/elif/elif/elif/elif/else`
  chain that enumerated five other buckets and silently funnelled every
  other state -- including `RUNTIME_DEGRADATION` -- into
  `operator_rows / cnt_operator`. Result: `devbench report` mis-rendered
  RUNTIME_DEGRADATION tasks as "operator action required" while
  `devbench status --detail` (which has its own correct routing in
  `cli.py:316-327`) rendered them in the right bucket. Both renderer
  paths now handle every `BlockedTaskState` enum member explicitly and
  raise `RuntimeError` if a future enum addition is not wired in --
  per CLAUDE.md no-fallback-logic. The blocked-task display gains a
  dedicated "Blocked tasks (runtime-degradation)" panel with the
  resolution hint `"SDK lost Agent-tool access mid-session; `make
  start` auto-restarts to recover."`. `_BacklogTotals` exposes a new
  `tasks_blocked_runtime_degradation` field. Pinned by
  `tests/test_reporting/test_report.py::TestBacklogTotalsSixBlockedFields::test_unhandled_blocked_state_raises_in_counter_path`
  plus extended canonical-order panel test.

- **Atomic temp-then-rename for every work-unit md write** (commit B of
  the shadow-plugin / WU-write fix pair). Concurrent readers of a WU md
  file could previously observe a heading-less intermediate state during
  an in-place rewrite, causing `devbench report` /
  `devbench validate-backlog` / any external parser consumer to fail
  with `No top-level heading found in <path>`. Every WU md writer in
  `devbench.backlog.manager`, `devbench.backlog.proposal`,
  `devbench.backlog.amendment`, `devbench.backlog.work_unit`, and the
  WU-touching `cmd_*` entry points in `devbench.cli` now routes through
  the new shared helper `devbench.utils.io.atomic_write_text`, which
  writes to `<path>.tmp` then atomically renames over the target via
  `Path.replace`. A reader observing *path* sees either the prior
  complete content or the new complete content, never partial. Pinned
  by `tests/test_utils/test_io.py::TestAtomicWriteTextConcurrentReader`
  (50 rapid back-and-forth atomic rewrites under a tight-loop reader,
  zero partial-write observations across thousands of reads).
  The legacy `_atomic_write` helper inside
  `devbench.backlog.amendment` is removed in favour of the shared one
  so there is only ever one source of truth for the atomic-write
  pattern. `devbench.utils.io` is added to the
  `Makefile :: test-coverage-new` gate at 100% line + branch.

- **ETA formula now includes auto-recovering blocked tasks**
  (issue #157). `devbench report`'s `Est. time to complete remaining`
  multiplier was `tasks_active * recent_pace_minutes`; it now reads
  `(tasks_active + tasks_blocked_recovery + tasks_blocked_auto) *
  recent_pace_minutes`, since both blocked buckets resolve on devbench's
  own. The `Needs operator attention` bucket stays excluded (genuine
  halt -> unbounded ETA). The cell carries a comment-suffix
  (`~5.4 h (active 4 + blocked-recovery 60 + blocked-auto 27 at 5.6
  min/task)`) showing the breakdown. The cost projection uses the same
  denominator. ETA still falls back to `n/a` when fewer than the
  required pace samples have completed in the recent window.

### Fixed

- **Shadow plugin can no longer be cleared while an orchestrator is using it** (ADR-25 sentinel-protected lifecycle). `cmd_start` now writes its PID to `<workspace>/.devbench/plugin-shadow/devbench/.pid` immediately after materialising the shadow. `clear_shadow_plugin` reads the sentinel before deleting the tree: when the recorded PID is alive, it raises `RuntimeError` naming the owning PID and recommends stopping it first. Closes a production race where a stray `devbench prepare-plugin-shadow` (firing when the YAML `agents:` block matched frontmatter defaults, which caused `materialise_shadow_plugin` to clear the shadow and return None) deleted the shadow's hook scripts out from under a running orchestrator. The SDK kept the plugin cached in memory so tool calls continued, but each hook fires as a fresh shell-script subprocess; with the script files gone, hook telemetry silently stopped logging to `hook-logs.jsonl`. The sentinel converts that silent-corruption to a fail-fast error. Sentinel lives inside the shadow tree so a legitimate rebuild's `rmtree` cleans it atomically.

### Changed (model defaults)

- **Per-agent model defaults retuned by role**. The plugin agent
  `.md` frontmatter `model:` lines were flipped from a uniform
  `sonnet` to a role-aware split: `executor` stays on `sonnet`
  (writes code under TDD; fast happy-path); the five judges
  (`code-reviewer`, `test-reviewer`, `doc-reviewer`, `changes-manifest`,
  `security-reviewer`) and the three workflow-reasoning agents
  (`blocker-resolver`, `manifest-amender`, `task-factory`) move to
  `opus` (a bad verdict / proposal / amendment decision costs more
  than the inference savings and these agents fire only after a task
  finishes or on the unhappy paths, so cost is bounded);
  `review-supervisor` stays on `haiku` (pure fan-out coordinator).
  All eight upgraded agents pick up the new default automatically;
  operators with opus-budget pressure can pin individual agents back
  to `sonnet` via the `agents:` block (ADR-25). `sample-config.yaml`,
  `docs/zero-to-ready.md`, `docs/cli-reference.md`,
  `docs/llm-authentication.md`, `docs/adr/25-per-agent-model-overrides.md`,
  and `tests/test_plugin_shadow.py`'s synthetic fixture updated to
  reflect the new defaults.

### Removed

- **Dead `judge_model` / `executor_model` YAML fields** removed from
  `config-schema.json`, `RuntimeConfig`, `load_runtime_config`, the test
  fixtures, the parser tests, `sample-config.yaml`, and the example backlog
  config under `examples/backlogs/...`. These were holdover from the
  pre-plugin "main2" Python-orchestrator architecture (ADR-01); the
  plugin-based architecture replaced them with per-agent `.md` frontmatter
  models, leaving the YAML fields parsed but never read. ADR-25
  (`agents:` block) is the current control surface for per-agent model
  routing. `docs/architecture.md`, `docs/model-pricing.md`,
  `docs/llm-authentication.md`, `docs/cli-reference.md`, the example
  workspace's `README.md` and `devbench-commands.txt` updated to remove
  references to the dead fields and to clarify that `DEVBENCH_CLAUDE_MODEL` is
  the SDK caller's model (orchestrate skill coordination calls), not a
  global per-role pin.

### Changed

- **Cost-rate calibration guidance** added to `sample-config.yaml`
  `report:` block + new "Calibrating cost rates against actual billing"
  section in `docs/model-pricing.md`. Operators with non-default model /
  context-tier / contract-pricing combinations now have a documented
  worked example for deriving `token_cost_per_million_input` /
  `_output` from actual API billing
  (`correction_factor = actual_billing / reported_cost`; multiply both
  rates). The `token_cost_discount` field is documented as wrong-
  direction for under-reporting (it only decreases reported cost, so
  cannot fix cases where reported is below actual). No behaviour
  change in the cost-computation code.
- **Transitive dep chains accepted by Manifest Conflict validator**
  (issue #145). Previously the validator required `N*(N-1)/2` direct
  dep edges between N claimants of the same Manifest path; now any DAG
  that totally orders the set via transitive reachability is accepted,
  so a clean N-1 edge chain is sufficient. Operator overhead drops
  from quadratic to linear in claimant count. The conflict error
  message prints a suggested chain in lexical-sort order as an
  operator hint. Pinned by
  `tests/test_backlog/test_manager.py::TestValidateManifestConflictsTransitiveChain`.
- **CI-failure retry default flipped to ON** (issue #115). Default
  behaviour is rc=2 + executor retry; opt out via
  `git_ops.ci_failure_retry: false` in `devbench.yaml` or
  `DEVBENCH_CI_FAILURE_RETRY_ENABLED=0`.
- **Review-supervisor scope guard** (issue #118): the
  `guard-review-supervisor-scope.sh` PreToolUse hook now also blocks
  Agent-tool subagent spawns whose subagent_type is not in the
  read-only review_team allowlist, closing the Bash-only gap.
- **TBD Manifest placeholder rejection** (issue #117): new
  validate-backlog rule 19 rejects work-unit Manifest rows whose
  first cell starts with `TBD`. `cmd_claim` refuses on placeholder
  pre-flight.
- **`wait_for_checks` workflow-registration race defence** (issue
  #114): "no checks reported" is now disambiguated via a local
  `<repo>/.github/workflows/*.y[a]ml` glob. Repos with workflow
  files retry up to `DEVBENCH_CHECK_REGISTRATION_RETRIES` (default 12)
  attempts spaced by `DEVBENCH_CHECK_REGISTRATION_DELAY_SECONDS`
  (default 5). On retry exhaustion, devbench refuses the merge.
- **Workspace-layout doc rewritten** (issue #113): `DEVBENCH_WORKSPACE_ROOT`
  is the parent of `backlog/`, with target repos as siblings.
- **Operator YAML field surface**: every PR-119 env-only knob now has
  a YAML equivalent. Operators set workspace-stable behaviour in
  `backlog/config/devbench.yaml`; env vars remain available as
  per-launch overrides.
- **Auto-requeue cascade fires on every terminal transition** (issue
  #147). `BacklogManager._set_status` now invokes
  `_auto_requeue_marker_dependents` whenever the target status is
  terminal (`done` OR `declined`), not just `done`. Idempotent: a
  per-instance guard tracks `(backlog_index, unit_id)` pairs so a
  redundant `_set_status` call does not double-fire the scan.
  `[CASCADE_RESOLVED]` is now appended alongside `[AUTO_UNBLOCKED]`
  on every cascade-driven re-queue so the audit-supersession panel
  filter (#153) can hide stale `[BLOCKED]` rows.
- **`cmd_sweep_proposals` auto-promotes pre-existing proposed drafts**
  (issue #155). When `task_factory.auto_accept_proposals: true`, the
  sweep now runs a second pass over the full backlog index and
  promotes every `proposed` task whose proposal JSON has been deleted.
  Closes the gap where drafts authored under the toggle but
  materialised before it was flipped on were marooned in `proposed`
  state forever. Pinned by
  `tests/test_cli.py::TestCmdSweepProposalsAutoPromotesPreExisting`.

### Fixed

- **`sync-blocked` evaluates `[BLOCKED_PENDING_PROPOSAL]` marker target
  status** (issue #148). The legacy
  `_BLOCKED_PENDING_PROPOSAL_OPEN_RE` regex flagged every marker as
  "open" -- including markers whose target had already completed. The
  new `_has_open_proposal_marker(content, units_by_id)` helper resolves
  each marker target via the parsed index and returns `True` only when
  at least one target is non-terminal (anything other than `done` /
  `declined`). Unknown target IDs (rejected drafts whose backlog row
  was removed) stay conservative. Pinned by
  `tests/test_cli.py::TestSyncBlockedEvaluatesMarkerTargetState`.
- **`classify_blocked_task` considers regular task-level deps** (issue
  #149). When markers are closed/absent AND the task's declared
  regular dependencies are still in flight, the result is now
  `AWAITING_AUTO_RECOVERY` instead of incorrectly escalating to
  `NEEDS_OPERATOR_ATTENTION`. The orchestrator's next sweep cycle
  picks the task back up automatically; no operator action required.
  Pinned by
  `tests/test_backlog/test_proposal_lifecycle_hardening.py::TestClassifyBlockedConsidersRegularDeps`.
- **N-node dependency cycle detection in `validate-backlog`** (issue
  #151). New `BacklogManager._check_dep_cycles` runs DFS-with-
  recursion-stack over the dependency graph derived from the Full
  Work Unit Index. Catches 4-node, 5-node, and arbitrary-N cycles
  that the prior shallow check missed. Reports each cycle once,
  rotated to start at its lexicographically smallest ID. Pinned by
  `tests/test_backlog/test_manager.py::TestValidateDepCycle4Node`.
- **`_VARIADIC_COMMANDS` registration is auto-discovered**  (issue
  #152). New
  `tests/test_cli.py::TestVariadicCommandsCoverage` walks `_COMMANDS`,
  inspects each handler's source for `--reason` / `--reasoning` /
  `--message` flag-with-value patterns, and asserts every match is
  registered in `_VARIADIC_COMMANDS`. Adds the same dispatcher
  guard as a self-test so a future flag-bearing command cannot ship
  without correct registration.
- **Status panel filters stale `[BLOCKED]` audits superseded by
  `[UNBLOCKED]` / `[CASCADE_RESOLVED]`** (issue #153). The
  append-only Comments history in the file is unchanged; only the
  `status --detail` panel renderer hides rows that have been
  succeeded by a later positive transition. The cascade re-queue
  audit now reads `[AUTO_UNBLOCKED] [CASCADE_RESOLVED] ...` and
  sync-blocked writes `[UNBLOCKED] deps satisfied ...`. Pinned by
  `tests/test_cli.py::TestStatusPanelFiltersStaleBlockedAudits` and
  `tests/test_backlog/test_manager.py::TestSetStatusWritesUnblockedAudit`.
- **`_hook_lib.sh::decode_json_escapes` bash 4.3+ nameref breaks on
  macOS bash 3.2.57** (issue #120). Replaced `local -n` nameref with
  `${!1}` indirect read + `printf -v "$1"` write (bash 3.0+
  compatible).
- **Inline orphan cleanup ValueError on symlinked checkouts**
  (issue #125). `_run_inline_cleanup_steps` now resolves
  `repo_path` once at the function head so it lives in the same
  path-space as `cleanup_tracked_orphans`'s internally-resolved
  `OrphanReport.gitignore_path`. Every workspace following the
  documented symlinked-checkout layout (`docs/backlog-contract.md`
  Workspace layout) now runs the inline cleanup correctly. New
  regression test
  `tests/test_cli.py::TestInlineOrphanCleanup::test_inline_cleanup_handles_symlinked_repo_path`
  pins the fix.
- **Security reviewer no longer flags findings on files outside the
  task's staged diff** (issue #126). The
  `plugin/devbench/agents/security-reviewer.md` prompt now contains an
  explicit five-rule scope contract that captures the in-scope path
  set from `devbench get-diff` first, refuses to read out-of-scope
  files, and drops out-of-scope findings from the verdict. New
  regression test
  `tests/test_integration/test_security_review_scope.py` pins the
  scope-contract text by-content so the rule cannot be silently
  removed.
- **Stop-hook block decision now honoured across Claude Code 2.x
  (3-layer defence)** (issues #138, #139, #140). Three independent
  root-cause hypotheses each addressed in the same commit so the
  orchestrator self-termination class is closed regardless of which
  hypothesis was actually live:
  - `plugin/devbench/hooks/hooks.json` -- `hook-logger.sh` removed
    from the Stop event hook list (#138). The Stop event now has
    exactly one registered hook (`continue-orchestration.sh`),
    eliminating the empty-stdout-vote-vs-block-decision dispatcher
    ambiguity. Stop events are still logged because
    `continue-orchestration.sh` calls `uv run devbench log` directly.
  - `plugin/devbench/scripts/continue-orchestration.sh` -- BLOCK_JSON
    now emits both the legacy `{"decision":"block","reason":"..."}`
    shape AND the modern `{"hookSpecificOutput":{"hookEventName":"Stop","additionalContext":"..."}}`
    shape (#139). Forward-compat across Claude Code 2.x.
  - `plugin/devbench/skills/orchestrate/SKILL.md` -- new CRITICAL
    rule forbids ending a turn with prose narration of the next step
    (#140). Every turn MUST end with EITHER a tool call OR a
    `uv run devbench next` invocation; the recap-prose pattern
    ("Next: re-invoke executor") is forbidden because Claude Code
    interprets a turn-end-without-tool-call as agent-done before the
    Stop hook's block can re-prompt the model.
  Regression tests:
  `tests/unit/test_hooks_json_registration.py::TestStopEventSingleHookRegistration`,
  `tests/unit/test_continue_orchestration_hook.py::TestStopHookEnvelopeShape`,
  `tests/test_integration/test_orchestrate_skill_no_recap_anti_pattern.py`.
- **Recovery cascade no longer re-introduces Manifest Conflicts**
  (issue #136). `plugin/devbench/agents/task-factory.md` adds an
  explicit rule that spec-correction recovery tasks list ONLY the
  work-unit markdown file in their Changes Manifest -- never the
  source files referenced inside that markdown's Manifest table.
  Listing source files in a recovery task's Manifest re-introduced
  the very Manifest Conflict the recovery task was created to
  resolve. Live evidence: 2026-05-02 caylent-telemetry-spec
  E2-F3-S2-T5 was materialised to remove `pyproject.toml` +
  `Makefile` rows from E2-F3-S2-T1's Manifest table; the factory
  populated T5's own Manifest with those source files; the next
  validate-backlog reported the same Manifest Conflict on
  pyproject.toml (5 claimants including T5) and Makefile (T1 + T5).
  New regression test
  `tests/test_integration/test_task_factory_spec_correction_scope.py`
  pins the rule by-content.
- **manifest-amender now rejects amendments that would create a
  Manifest Conflict** (issue #137). New pre-filter rule scans every
  other work-unit's Manifest before approving an amendment that adds
  a file. If the file is already claimed: REJECT (or, when the
  conflict task is in a terminal state and the new row is `Modify`,
  ALLOW with a `[CONFLICT_AUTODEP]` audit comment recommending the
  operator add a dep edge). Prevents new conflicts from being
  authored in the first place, making the recovery cascade the
  exception rather than the norm. New regression test
  `tests/test_integration/test_manifest_amender_pre_conflict.py`
  pins the rule by-content.
- **`devbench hook-tail` description column no longer wraps onto a
  timestamp-less continuation line** (issue #133). The `_description`
  helper now collapses every run of whitespace -- including embedded
  `\n` / `\r\n` / `\t` and runs of spaces -- to a single space then
  strips. The previous implementation passed the raw agent-supplied
  string through verbatim, so a multi-line description (e.g.
  `"# Check ...\n# The actual command..."`) broke the per-event
  single-line invariant. New regression tests
  `tests/unit/test_hook_tail.py::TestDescriptionNormalisation` pin
  the contract: single-line stays unchanged; `\n` / `\r\n` / `\t` /
  runs-of-spaces collapse; every fallback branch (description /
  command / file_path / JSON) collapses; `format_entry()` output
  contains zero `\n` regardless of input.
- **Manifest amender no longer rejects amendments on the grounds that
  requested files are not yet in the Manifest** (issue #127). The
  `plugin/devbench/agents/manifest-amender.md` SCOPE rule now contains
  an explicit "Critical (issue #127)" sub-block forbidding the
  circular rejection -- adding files to the Manifest is the entire
  purpose of an amendment. New regression test
  `tests/test_integration/test_manifest_amender_scope.py` pins the
  protective fragments by-content.
- **Stop hook block decision no longer dropped when `python3` is
  asdf-shimmed without a configured version** (issue #130).
  `plugin/devbench/scripts/continue-orchestration.sh` now uses `jq`
  for every JSON serialisation (BLOCK_JSON construction, state-file
  read, diagnostic-capture write); the previous `python3 -c '...'`
  invocations exited 126 under an asdf shim with no python version,
  causing the script to fall back to a literal
  `"(reason serialisation failed)"` reason text and silently skip
  the diagnostic-capture file. Operators saw exactly one
  `Stop hook blocked (1/5)` log entry per session followed by an
  unwanted Claude Code self-termination. New regression tests
  `tests/unit/test_continue_orchestration_hook.py::TestBlockJsonSerialisationRobustness`
  pin the new contract: zero `python3` invocations in the script,
  the BLOCK_JSON reason field is real text under a minimal PATH,
  and the diagnostic-capture file is written on every block.
- **Stop hook now reports the active task, not the alphabetically-
  first stale `in-progress` row** (issue #131).
  `continue-orchestration.sh` reads `<workspace>/logs/*.log` for the
  most recent `Branch ready: ... on <task_id>` or
  `Set <task_id> to 'in-progress'` entry and uses that ID as the
  active task. When multiple tasks are in-progress simultaneously,
  the reason text now lists every in-progress ID with the active
  task named first and the rest enumerated as
  `(also in-progress: ID2, ID3)`. Falls back to the existing
  `head -1` BACKLOG row only when no log entry parses (fresh
  checkout, never-launched workspace). New regression tests
  `tests/unit/test_continue_orchestration_hook.py::TestActiveTaskSelection`
  pin all four scenarios (log-driven pick, multiple-in-progress
  surfacing, fresh-checkout fallback, both log-line shapes accepted).
- **`hook-tail` and other stream-rendering commands no longer emit a
  startup banner on stderr** (issue #132). The
  `judges.log_setup` "Logging to stderr and ..." informational line
  was demoted from INFO to DEBUG, so the default DEVBENCH_LOG_LEVEL=INFO
  run is silent. Operators who want the banner back can set
  DEVBENCH_LOG_LEVEL=DEBUG. New regression tests
  `tests/test_log_setup.py::TestStartupBannerDemoted` pin both the
  silent-at-INFO and visible-at-DEBUG behaviours.
- **Stop-hook block decision honored under asdf-shimmed workspaces**
  (issue #130 / #131 -- already shipped in v-next on 04457b2;
  see prior bullets). Follow-up clean-up included in this release.
- **`gh pr create` no longer fails with "a pull request already
  exists for this branch"** when git-ops runs a second time on the
  same branch (issue #129). New helper `GitOpsService.find_open_pr`
  queries `gh pr list --head <branch> --state open` first; if a PR
  exists, `create_pr` returns its URL and skips the create call.
  Triggers covered: REFACTOR cycle after REVIEW_PASS, executor fix
  triggered by `pr_review_resolution` bot feedback, and CI-failure
  retry replay. New regression tests
  `tests/test_github/test_git_ops.py::TestCreatePrExistingPrReuse`
  pin the find/reuse/fall-through paths plus defensive cases
  (gh failure, malformed JSON).
- **Executor no longer acts on the content of REVIEW_PASS verdicts**
  (issue #128). Prompt updates: SKILL.md step 7 now contains an
  explicit "CRITICAL (issue #128)" rule that REVIEW_PASS is
  terminal; executor.md gains a parallel "REVIEW_PASS verdicts are
  terminal" section listing the three (and only three) legitimate
  executor-invocation triggers. Informational content in PASS
  verdicts (MEDIUM-severity notes, refactor suggestions) MUST NOT
  trigger additional executor work cycles -- only REVIEW_FAIL or
  git-ops exit codes 2 / 3 do. New regression tests
  `tests/test_integration/test_executor_review_pass_terminality.py`
  pin both prompts by-content.

### Added

- **Topological sort for parallel-task candidates** (issue #121).
  `BacklogParser.get_parallel_candidates` now orders actionable
  tasks by their topological depth in the full dep-DAG (shallow
  first), with a stable lexicographic `id` tiebreaker within each
  depth band. A task with zero declared dependencies (depth 0)
  precedes a task with one transitive dependency (depth 1), which
  precedes a task with two (depth 2). The "build-order foundation
  first" intuition holds even when most ancestors are already
  ``done``. Cycle protection collapses self-loops to depth 0
  without recursing infinitely; unresolvable IDs add 1 depth band
  per declared dep but do not crash. New regression tests
  `tests/test_backlog/test_parser.py::TestGetParallelCandidatesTopologicalOrder`
  pin all five scenarios.
- **Per-judge executor retry budgets** (issue #122). New optional
  YAML map `max_executor_retries_per_judge` lets operators tune
  retries per failing judge (e.g., 20 retries for flakey
  test_review, 2 for stable doc_review) without raising the global
  cap. Schema validation rejects unknown judge names; runtime
  helper `_load_per_judge_retries` defends against schema-bypass.
  SKILL.md step 6.d documents the consumption rule (per-judge
  budget when listed, fall back to `max_executor_retries`).
  New regression tests
  `tests/test_config_loader.py::TestPerJudgeRetriesConfig` cover
  global-only, per-judge override, schema rejection, runtime helper
  rejection of malformed inputs.
- **Per-role cost breakdown helper for `devbench report`**
  (issue #123). New `_parse_transcript_metrics_by_role` returns a
  dict mapping role name -> `HookLogTotals` by reading each
  transcript message's `attributionAgent` field. Subagent
  attributions are normalised to the canonical judge names
  (`devbench:code-reviewer` -> `code_review`); messages with no
  attribution land in the `orchestrator` bucket. Aggregate-row
  contract holds: summed totals across all roles equal what
  `_parse_transcript_metrics` returns. New regression tests
  `tests/test_reporting/test_report.py::TestTranscriptParsing`
  cover bucketing, aggregate-row preservation, missing-dir
  handling, and the role-name normalisation table.
- **`hook_tail` column caps configurable** (issue #134). Four
  module-level constants in `src/devbench/hook_tail.py`
  (`AGENT_WIDTH`, `TOOL_WIDTH`, `DESCRIPTION_MAX`,
  `STDOUT_PREVIEW_MAX`) are now resolved env > YAML > default at
  module import. New top-level `hook_tail:` block in
  `backlog/config/devbench.yaml`; new `DEVBENCH_HOOK_TAIL_*` env-var
  overrides. **Default `DESCRIPTION_MAX` bumped from 100 to 120** so
  multi-word agent descriptions are less likely to truncate
  mid-clause; other three defaults unchanged (12 / 8 / 80).
  `EVENT_WIDTH` stays a `hook_tail.py`-local constant -- the arrow
  column is intrinsic to the format. New regression tests
  `tests/unit/test_hook_tail.py::TestHookTailColumnConfig`
  + `tests/test_config_loader.py::TestHookTailConfig` cover global-
  only / full-override / partial-override / schema rejection of
  non-positive caps and unknown keys.
- **Data-residency and fast-mode multipliers applied per-call**
  (issue #124). `_compute_cost` now accepts
  `data_residency_multiplier` (default 1.10 from
  `DEFAULT_DATA_RESIDENCY_MULTIPLIER`, configurable via YAML
  `report.data_residency_multiplier`) and `fast_mode_multiplier`
  (default 6.0 from `DEFAULT_FAST_MODE_MULTIPLIER`, new YAML field
  `report.fast_mode_multiplier`). Token volumes from entries with
  `usage.inference_geo` set are tracked separately
  (`us_only_*_tokens`) from baseline; same for
  `usage.speed == 'fast'` (`fast_*_tokens`). Multipliers compose
  with cache + base-rate multipliers (apply after cache scaling,
  before discount) per AC-FUNC-003. New regression tests
  `tests/test_reporting/test_report.py::TestAccurateCost::test_data_residency_multiplier_applies_to_us_only_subset`
  + the fast-mode + composition + default-no-boost siblings cover
  all four AC scenarios.

### Renamed / Removed

- **`DEVBENCH_DISABLE_INLINE_ORPHAN_CLEANUP` env var renamed** to
  `DEVBENCH_INLINE_ORPHAN_CLEANUP` (truthy/falsy, no longer opt-out).
  The asymmetric "DISABLE" form is removed; canonical naming aligns
  with every other toggle.

### Migration notes

Operators upgrading from before this release:

1. **Remove the old env-var name** (`DEVBENCH_DISABLE_INLINE_ORPHAN_CLEANUP`)
   from any orchestrator launch scripts and replace with
   `DEVBENCH_INLINE_ORPHAN_CLEANUP=0` if you want to disable inline
   cleanup. (Most operators leave it unset; the default is on.)
2. **Decide on CI-retry**: the new default is on. To opt out for a
   specific backlog, add `git_ops.ci_failure_retry: false` to that
   backlog's `backlog/config/devbench.yaml`.
3. **Optionally migrate env-var exports to YAML**: every toggle that
   was previously env-only in PR-119 now lives in
   `backlog/config/devbench.yaml`. Workspace-stable behaviour belongs
   there; env vars remain available as per-launch overrides. See
   `sample-config.yaml` for the canonical shape.
4. **Optional: enable PR review-comment polling (issue #116)** if your
   target repo has Copilot / Q-Dev / similar review bots. Set
   `git_ops.pr_review_resolution.enabled: true` and populate the
   `agents:` allowlist.
5. **Issue #101 (pause-before-merge)** lands the YAML schema
   (`git_ops.pause_before_merge`) in this release; the runtime
   implementation ships in a follow-up commit on this branch (or in
   the next release if the follow-up is deferred).

### Known follow-ups (this branch / next release)

- Implement `git_ops.pause_before_merge: true` runtime path (#101).
  Schema + validation already in place; runtime branch in
  `cmd_git_ops` + `cmd_check_merge` ships next.
- Live integration smoke covering all four exit codes (0/1/2/3) +
  inline cleanup chore-commit shape on a fresh tmp workspace.
