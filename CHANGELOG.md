# Changelog

All notable changes to devbench are documented in this file. Format is
based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

## [Unreleased] -- v-next

This release bundles the orchestrator self-healing work, the canonical
configuration refactor, the EC2 remote-dev provisioning stack, and the
work-unit lifecycle / authoring CLI improvements that have accumulated
since the last release. PR #119 carries every change.

### Added

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
- **E230 `JUDGE_ORCHESTRATOR_SESSION_ID` filter**: hook-tail
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
  `JUDGE_ORCHESTRATE_MAX_CASCADE_DEPTH`) caps recursion. At cap, the
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
  is missing or empty. Includes the `JUDGE_ORCHESTRATOR_SESSION_ID`
  suffix when set so multi-session operators can tell which session
  the report monitors. ANSI colour only when stdout is a TTY; pipes
  / CI redirects receive plain text. Refreshes on every
  `--watch N` tick. Threshold reuses `stop_hook.window_seconds`
  rather than introducing a redundant config knob, which guarantees
  the banner stays aligned with the operator's already-tuned
  circuit-breaker quiet window (e.g., a 180s window tolerates a
  3-minute terraform-apply quiet stretch without flashing STOPPED).

### Fixed

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
  `JUDGE_CI_FAILURE_RETRY_ENABLED=0`.
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
  files retry up to `JUDGE_CHECK_REGISTRATION_RETRIES` (default 12)
  attempts spaced by `JUDGE_CHECK_REGISTRATION_DELAY_SECONDS`
  (default 5). On retry exhaustion, devbench refuses the merge.
- **Workspace-layout doc rewritten** (issue #113): `JUDGE_WORKSPACE_ROOT`
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
  was demoted from INFO to DEBUG, so the default JUDGE_LOG_LEVEL=INFO
  run is silent. Operators who want the banner back can set
  JUDGE_LOG_LEVEL=DEBUG. New regression tests
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
  `backlog/config/devbench.yaml`; new `JUDGE_HOOK_TAIL_*` env-var
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
  `JUDGE_INLINE_ORPHAN_CLEANUP` (truthy/falsy, no longer opt-out).
  The asymmetric "DISABLE" form is removed; canonical naming aligns
  with every other toggle.

### Migration notes

Operators upgrading from before this release:

1. **Remove the old env-var name** (`DEVBENCH_DISABLE_INLINE_ORPHAN_CLEANUP`)
   from any orchestrator launch scripts and replace with
   `JUDGE_INLINE_ORPHAN_CLEANUP=0` if you want to disable inline
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
