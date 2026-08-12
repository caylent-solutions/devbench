# Changelog

All notable changes to devbench are documented in this file. Format is
based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

## [Unreleased] -- v-next

- **Orchestrator inactivity net and cooperative SDK teardown** (FR-17,
  issues db-262 / db-325). `devbench start`'s `_run` SDK message loop
  no longer idles forever on a wound-down turn with no terminal
  sentinel (an observed hang ran 2h24m before this fix): the loop now
  awaits `agen.__anext__()` under a bounded `asyncio.wait_for`, and a
  stall raises a new `_OrchestrateInactivityTimeout` sentinel that
  `_drive_orchestrate_with_quota_resume` disposes as a bounded
  fresh-session restart, reusing the same cap as
  `DEVBENCH_MAX_QUOTA_RESUMES` -- never a stateful `ClaudeSDKClient`
  continuation. The wait window is configurable via the new
  `timeouts.orchestrator_inactivity` key in
  `backlog/config/devbench.yaml` and the
  `DEVBENCH_ORCHESTRATOR_INACTIVITY_TIMEOUT` environment variable,
  defaulting to the new `DEFAULT_ORCHESTRATOR_INACTIVITY_SECONDS`
  constant (1800 seconds). A `finally: await agen.aclose()` around the
  loop (db-325) guarantees cooperative teardown before a quota or
  inactivity sentinel unwinds, so `aclose()` is never invoked while
  the generator is still running and the CLI subprocess is never
  orphaned.

- **`Recent pace (last N tasks)` and `Average time per task` still
  understated the real execution window by roughly an order of magnitude
  even after issue #326's same-session gate** (issue #329). Two compounding
  defects. Defect A: the unanchored transition regex scanned the entire log
  line rather than requiring the emitting logger, so a `devbench.cli` line
  that echoed a prior transition -- for example inside an SDK
  `ToolResultBlock` payload reproducing an earlier audit comment -- was
  ingested as a genuine transition; on the reproduction log 85% of
  `in-progress` matches and 47% of `done` matches were echoes, not
  transitions. Defect B: even with Defect A fixed, a genuinely re-claimed
  task (claimed, bounced by review, re-claimed in the same session) was
  anchored to its LAST claim (`MAX(ts_epoch_us)`) rather than its first,
  discarding the review-bounce time as if it were idle. On the
  reproduction log (`logs/orchestrator.log`) the combined effect
  understated the median execution window by ~10.6x (32.1 min true median
  vs 3.0 min as sampled) and projected the remaining ETA at 0.5 h where the
  corrected figure is 5.3 h. `event_index.py`'s transition queries now bind
  `logger = 'devbench.backlog_manager'` -- the sole in-tree emitter of the
  quoted `Set <id> to '<status>'` record -- as a SQL predicate so an
  echoed line can never match, and `_execution_anchor` (`report.py`) now
  selects the EARLIEST same-session claim rather than the latest.
  Candidate `in-progress` rows rejected for failing the logger predicate
  are surfaced rather than silently dropped: the `Average time per task`
  and `Recent pace (last N tasks)` cells append a
  `(<k> non-transition rows rejected)` suffix, composed AFTER the #326
  `(<k> excluded: no execution window)` suffix, naming how many candidate
  rows were rejected. `docs/cli-reference.md`'s ETA-formula note documents
  the anchor contract and both suffixes.


## [0.4.0] -- 2026-08-12

### Changed (model defaults)

- **Shipped model rate table refreshed to the current lineup; default
  fallback model moves to Opus 5** (issue #233). `DEFAULT_MODEL_RATES`
  (`src/devbench/constants.py`) gains four entries, all LIST rates
  verified against the official Anthropic pricing page
  (https://platform.claude.com/docs/en/about-claude/pricing), captured
  2026-07-28:
  - `claude-fable-5`: $10 / $50 per million input / output tokens.
  - `claude-opus-5`: $5 / $25 -- the new shipped default (see
    `DEFAULT_FALLBACK_MODEL_RATES`).
  - `claude-opus-4-8`: $5 / $25 -- selectable, no longer the default.
  - `claude-sonnet-5`: $3 / $15 LIST rate (spec S5.3). An introductory rate
    of $2 / $10 runs through 2026-08-31; that promotional rate is **not**
    shipped as the default -- workspaces wanting invoice-accurate
    introductory pricing during the promo window override locally via
    `report.models`.

  Every pre-existing entry is retained, including the three Haiku pricing
  rows (Haiku remains priced for reporting even though it is banned for
  work agents, issue #198).

  **`DEFAULT_FALLBACK_MODEL_RATES`** moves from an Opus-4.7-list-rates
  label to an Opus-5-list-rates label (value unchanged at $5/$25 since
  Opus 5 and Opus 4.7 share the same list rate); no hard-coded Opus 4.7
  default reference remains in `constants.py`, `config_loader.py`,
  `config.py`, or `config-schema.json`.

  **`DEFAULT_FAST_MODE_MULTIPLIER`** corrects from `6.0` (stale Opus
  4.6-era value) to `2.0`: fast mode today runs $10/$50 on a $5/$25 base
  for Opus 5 and Opus 4.8, verified against the same pricing-page capture.

  **`fable` short name added.** `ALLOWED_AGENT_MODEL_SHORT_NAMES` now
  includes `fable` alongside `opus` and `sonnet`, aliasing `claude-fable-5`
  for `agents.*` YAML overrides when `use_bedrock: false`. `haiku` remains
  absent (issue #198); the `config-schema.json` `agents` description no
  longer advertises `haiku` as an accepted short name.

  **Issue #254 superseded.** #254 asked for Opus 4.8 as the new default;
  Opus 5 shipped after #254 was filed, so per Decision D-2 this work moves
  the default to Opus 5 instead and keeps Opus 4.8 as a selectable,
  non-default model. #254 closes with this note recording the honest
  supersede rather than the literal request.

  Mirrored comments updated in the same commit:
  `src/devbench/config_loader.py` (`ReportConfig.fast_mode_multiplier`
  docstring, `_parse_default_model_rates` docstring),
  `src/devbench/config.py` (`REPORT_DEFAULT_MODEL_RATES` comment), and
  `src/devbench/config-schema.json` (`default_model`,
  `fast_mode_multiplier`, `agents` descriptions). The `docs/model-pricing.md`
  and `sample-config.yaml` mirrors are updated by the follow-up task
  E3-F2-S1-T1.

### Changed

- **Flattened the review leg: the four review-team judges are now
  invoked directly by the orchestrate skill as first-level
  sub-agents; `review-supervisor` is reduced to a non-spawning
  aggregator** (ADR-33). A live reproduction (session
  `32862e10-7ede-4265-8892-e0637684bb3e`, `claude-agent-sdk 0.2.128`,
  recorded in `docs/adr/33-flatten-review-topology.md`) showed a
  second-level Agent-tool spawn from a sub-agent succeeding
  completely and reliably under that configuration -- it did **not**
  reproduce a hard SDK restriction on sub-agent-spawns-sub-agent. The
  flatten is adopted anyway, per spec S0 B-9a, as defense-in-depth
  against model-tier-dependent Agent-tool spawn reliability -- the
  same class of risk ADR-25's haiku-rejection guard already
  mitigates by pinning. Before this flatten, review-supervisor was
  invoked as a first-level sub-agent and itself declared
  `Agent(code-reviewer, test-reviewer, doc-reviewer, changes-manifest)`
  to fan out to the four judges; whenever that second-level spawn
  failed to run -- for whatever reason, model-tier-dependent or
  otherwise -- the fan-out silently no-opped, the work unit stalled as
  `RUNTIME_DEGRADATION`, and no restart could clear it, because the
  classifier could not distinguish a genuinely blocked spawn from a
  transient one. Removing the second-level spawn removes that whole
  failure class; `review-supervisor` is still invoked, but only
  afterward, to read the four judges' already-persisted verdicts and
  aggregate them -- it is never the one dispatching them.
  - `plugin/devbench-orchestrate/skills/orchestrate/SKILL.md` step 5
    now invokes `devbench-orchestrate:review_team:code-reviewer`,
    `test-reviewer`, `doc-reviewer`, and `changes-manifest` directly,
    in a single response, as first-level sub-agents. Each judge
    self-logs its own verdict before returning.
  - `plugin/devbench-orchestrate/agents/review-supervisor.md` no
    longer declares Agent-tool spawn capability in its frontmatter
    `tools:` field. It reads the four judges' already-persisted
    verdicts from the work unit's Comments section and reports a
    consolidated result.
  - **Missing-verdict hard failure**: SKILL.md step 5a documents
    that if any of the four required judges has no verdict logged in
    the current round, that is a hard failure naming the absent
    judge -- never an implicit pass. A judge that never logged is
    indistinguishable from a judge that never ran. (The underlying
    enforcement in `BacklogManager._last_round_all_passed` already
    treated a missing verdict as a hard failure; this change aligns
    the prompts with that existing contract.)
  - `plugin/devbench-orchestrate/scripts/guard-review-supervisor-scope.sh`
    now blocks every Agent-tool invocation from review-supervisor
    unconditionally -- the prior review_team allowlist branch is
    removed, since review-supervisor never spawns any subagent
    post-flatten.
  - `src/devbench/backlog/proposal.py`'s `_RUNTIME_DEGRADATION_BODY_RE`
    comment and `_has_runtime_degradation_signal` docstring now
    describe a match as a topology **regression** signal (a match
    should never occur in a healthy post-flatten run), not a
    transient degradation an operator restart routinely clears. The
    regex pattern itself is unchanged.
  - `docs/architecture.md`, `docs/plugin-architecture.md`,
    `docs/execution-modes.md`, `docs/faq.md`, `docs/cli-reference.md`,
    and `README.md` updated to describe the four judges as
    first-level sub-agents invoked directly by the orchestrate skill,
    with review-supervisor as a non-spawning aggregator.
  - Remaining consumers of the superseded second-level-spawn contract
    updated to match: `continue-orchestration.sh`'s Stop-hook
    `NEXT_STEP` guidance now names the four `review_team` judges as
    first-level invocations before review-supervisor aggregates;
    `docs/zero-to-ready.md`, `docs/llm-authentication.md`, and
    `plugin-authoring/devbench-authoring/skills/configure-devbench/
    SKILL.md` no longer call review-supervisor a "fan-out
    coordinator"; and `docs/watch-activity.md`'s troubleshooting
    table row and omitted-content bullet now describe
    `review-supervisor running` as meaning the four judges have
    already finished and self-logged, not that they are concurrently
    running.
- **`claude-agent-sdk` lock advanced 0.1.48 -> 0.2.128** (issue #231; epic
  driver #255). `pyproject.toml:18` already declared
  `claude-agent-sdk>=0.1.48` with no upper bound -- `uv.lock` was simply
  pinned to a stale 0.1.48 resolution. `uv lock --upgrade-package
  claude-agent-sdk` resolved cleanly to 0.2.128 with no `pyproject.toml`
  edit required. The nine sites in `tests/test_cli.py` that construct real
  `claude_agent_sdk.types` objects (`AssistantMessage`, `ToolUseBlock`,
  `ResultMessage`) pass unchanged against the upgraded SDK -- no
  constructor signature change was observed. A live probe (`query()`
  against a minimal prompt, run outside the orchestrator's own SDK
  session via `env -u CLAUDECODE`) shows the 0.2.x iterator now
  **terminates naturally** after a single `ResultMessage`
  (`ITERATOR TERMINATED NATURALLY` printed within ~4s total), in contrast
  to the 0.1.x behaviour documented above (#218) where the iterator never
  terminated on its own and re-emitted paid `ResultMessage` turns every
  ~5s. This is a cadence improvement, not a regression -- the
  `_TERMINAL_ORCHESTRATE_MARKERS` early-break workaround remains in place
  pending its own removal task. `make validate` passes unchanged after the
  advance (98.01% coverage, 5084 passed, 8 skipped -- identical to the
  pre-upgrade baseline).

- **`actionability_line` gains two active-run outcomes instead of collapsing
  to the stuck-state message while work is executing** (issue #309, spec
  Section 4 FR-1, E9-F1-S1-T1). `get_parallel_candidates` deliberately
  includes `IN_PROGRESS` units (issue #185, resume support) and the function
  then subtracted `active_ids`, so when the only candidate was the unit
  already running, the list emptied and `No actionable units. N blocked.`
  printed while work was actively executing -- in a serially-ordered backlog
  that is the steady state, so the operator-facing line cried wolf for the
  whole run and camouflaged the genuine deadlock case (issue #253). The
  three-outcome contract in `src/devbench/backlog/actionability.py` is now
  five: `Next actionable: <id> -- <title>` and `All work units are DONE.`
  keep their byte-identical strings; a new third branch renders `<id>
  active; nothing else can start yet. <tail>` for exactly one active id and
  `<N> units active; nothing else can start yet. <tail>` for several; the
  fall-through re-bases to `No actionable units. <tail>`; and `<tail>` is
  now `<B> blocked` or `<B> blocked, <H> on hold` when `H` (units with
  status `HOLD`) is greater than zero, computed directly from `units` via
  `WorkUnitStatus.HOLD` with no parser change. Both callers (`cli.py`
  `status`, `reporting/report.py` `report`) inherit the change untouched;
  `devbench next`'s own machine-token output is not modified.

### Added

- **Honest completion paths for the machine-observed RED gate: three
  named remedies, a cited already-satisfied decline, and a refactor
  green-green check** (FR-4.5/FR-4.6, E4-F4-S1-T2). The RED gate
  (`devbench.tdd_gate._build_rejection_message`, shipped by
  E4-F3-S1-T2) already named all three legitimate ways forward --
  produce a genuine RED, re-type the task, or decline it as
  already-satisfied -- in every rejection it raises. This task adds two
  new surfaces that reuse the same `tdd_gate.REMEDY_1`/`REMEDY_2`/
  `REMEDY_3` constants via a new
  `devbench.backlog.manager._build_remedies_rejection_message` helper so
  the same three remedies are named consistently there too: the
  gated-task-type block enforced by `BacklogManager.mark_done` (below),
  and `devbench decline`'s citation requirement. `devbench decline`
  gained a `--citation
  <commit-hash-or-task-id>` flag: declining a task with a reason naming
  "already-satisfied" now requires a valid citation (a 7-40 character
  lowercase hex commit hash or a task id, checked by the new
  `BacklogManager.is_valid_citation`); an uncited already-satisfied
  decline is rejected as unfalsifiable, and `validate-backlog` gained a
  matching static check (check 22) that flags any already-satisfied
  `[DECLINED]` comment persisted without one. The FR-4.5/FR-4.6
  task-type completion invariant now lives in
  `BacklogManager.mark_done` itself
  (`_check_task_type_done_invariant`), not in a CLI-layer wrapper, so
  every caller inherits it identically: both `devbench mark-done` and
  `devbench check-merge` (on a merged PR) now refuse a gated task
  (`behavior-fix` / `feature`, including the default when `## Task
  Type:` is omitted) that carries no `[RED_OBSERVED]` record in its TDD
  Cycle Log, so a behavior-fix whose test already passed before any
  change is routed to decline rather than silently claimed as done via
  either surface. A new `devbench green-green-check <id>
  <test_node_id> [...]` command gives `refactor` tasks -- exempt from
  the RED gate but not from their own invariant -- a way to prove the
  change is behavior-preserving: it confirms the named tests pass in
  the current ("after") tree, path-scoped stashes the Changes
  Manifest's production-source rows to reconstruct the pre-change
  ("before") state, confirms the same tests pass there too, and
  restores the stash unconditionally (including when the before-state
  run itself raises). If the stash push finds no uncommitted
  production-source change to save, the check rejects rather than
  silently comparing the tree to itself, so a refactor with nothing
  actually changed cannot false-pass. A collection failure on either
  side fails closed, never reported as a pass. On success, the check
  appends a machine-observed `[GREEN_GREEN_OBSERVED]` entry to the work
  unit's TDD Cycle Log naming the confirmed test node ids.
  `GREEN_GREEN_OBSERVED` is registered in `constants.VALID_TDD_PHASES`
  as orchestrator-only (not in `AGENT_WRITABLE_TDD_PHASES`), mirroring
  the `RED_OBSERVED` control: an agent cannot write it via `log-tdd`,
  and `cli._reject_bracketed_phase_tag`'s bracketed-phase-tag forgery
  check now also rejects a forged `[GREEN_GREEN_OBSERVED]` tag in
  `log-tdd`/`log-comment`/`log-verdict` free text. Because the
  same `BacklogManager.mark_done` invariant check backs both surfaces,
  both `devbench mark-done` and `devbench check-merge` now refuse a
  `refactor` task carrying no such record, so `green-green-check` is a
  gate a refactor task must pass through, not an optional, unconsumed
  command. Three end-to-end journeys in the new
  `tests/test_integration/test_tdd_red_gate_e2e.py` script the
  operator-facing scenarios against real git repositories: a false-fix
  attempt is judged REVIEW_FAIL with the exact FR-4.4 message pulled
  verbatim from the judge prompts; an honest behavior-fix (real RED
  observed, real fix applied, real GREEN) reaches done with the
  `[RED_OBSERVED]` record present; and all five required judge verdicts
  are independently attributable, with any one of the four review-team
  judges missing blocking done.

- **Quota wait-and-resume** (ADR-24, issue #236). `devbench start`
  detects Anthropic subscription rate-limit exhaustion mid-session
  (HTTP 429, the CLI's verbatim "You've hit your limit" text, or an
  `AssistantMessage.error == "rate_limit"` field) and pauses the
  orchestrate loop instead of exiting non-zero: it checkpoints the
  pause to `.devbench/quota_pause.json`, waits for the provider's
  `reset_at` (or polls a recovery probe when `reset_at` is unknown),
  then opens a fresh in-process SDK session and continues the backlog
  automatically once quota recovers. Configurable via the new
  `quota_handling` block in `backlog/config/devbench.yaml`
  (`enabled`, `on_exhaustion`, `poll_interval_seconds`,
  `max_wait_seconds`, `on_exhaustion_timeout`, `resume_strategy`,
  `audit_comment_on_wait`, `audit_comment_on_resume`,
  `log_structured_events`) -- default-on, waits on exhaustion, drains
  on timeout. The wait never uses `asyncio.shield`, so
  `devbench stop --session <name>` (or a direct SIGTERM) still
  interrupts a paused session promptly, force-blocking the in-flight
  work unit rather than leaving it in an ambiguous state. The new
  `devbench quota-watcher` command reports the current pause state
  (`reason`, `reset_at`) from the on-disk checkpoint without
  disturbing the running orchestrator; `devbench status` continues to
  show the paused work unit under "Active work units:" for the
  duration of the wait. In-process resumes are bounded by
  `DEVBENCH_MAX_QUOTA_RESUMES` (default 1000) so an unattended
  overnight run can survive multiple quota windows without exceeding
  a fail-safe cap. See `docs/quota-handling.md` and
  `docs/adr/24-quota-wait-and-resume.md`.

### Removed

- **`sdk_teardown_filter` workaround module removed** (issues #232, #231).
  The 185-line `src/devbench/sdk_teardown_filter.py` asyncio exception
  handler that downgraded the known `claude-agent-sdk` `Query.close()`
  cancel-scope `RuntimeError` teardown race to a `WARNING` is deleted,
  along with its 347-line test file `tests/test_sdk_teardown_filter.py`.
  `cmd_start`'s `_run` coroutine in `src/devbench/cli.py` no longer wraps
  the SDK `query()` loop in `async with _sdk_teardown_guard():`; the
  `async for` loop body is unchanged, only unindented one level. The
  operator-facing paragraph describing the workaround was removed from
  `docs/cli-reference.md`. The workaround is no longer needed now that
  `uv.lock` resolves `claude-agent-sdk` to `0.2.128`, above the `>=0.2.87`
  floor at which the cancel-scope teardown race is resolved upstream
  (verified in E1-F1-S1-T1); `pyproject.toml`'s declared floor remains
  unchanged at `>=0.1.48`, so a fresh resolve against the manifest alone
  is not guaranteed to select a fixed version -- the lock file is the
  operative evidence, not the manifest floor. Issue #232 (this workaround)
  and issue #231 (the upstream lock-advance tracking issue) are both
  closed as a result.

### Dependencies

- **Eight open dependabot PRs reconciled against the resolved lock; six
  closed unmerged, two bumped explicitly** (spec FR-6.1, FR-6.2, FR-6.3,
  D-14; AC-78, AC-79, AC-80, AC-81, AC-82, AC-83). Per decision D-14, E6
  reconciles the dependabot backlog against
  what `uv.lock` actually resolves rather than blind-merging every open
  branch, which would guarantee lock conflicts. `tools/check_dependabot_targets.py`
  is added as the reconciliation checker: it parses `uv.lock` with stdlib
  `tomllib`, compares each of the eight targets below against the locked
  version with a numeric version-tuple comparison, and prints one line per
  target in spec G-6's worked-example format. Resolved-version matrix after
  both E6-F1-S1-T3's mcp-family lock advance (commit
  `6ec06c7a02deeb1714fd5c8bb45230971f65b603`) and this task's idna/urllib3
  bump:

  | PR | Package | Locked | Target | Verdict |
  |---|---|---|---|---|
  | #287 | mcp | 1.29.0 | 1.28.1 | SATISFIED |
  | #278 | pydantic-settings | 2.14.2 | 2.14.2 | SATISFIED |
  | #277 | starlette | 1.3.1 | 1.3.1 | SATISFIED |
  | #276 | cryptography | 50.0.0 | 48.0.1 | SATISFIED |
  | #275 | python-multipart | 0.0.32 | 0.0.31 | SATISFIED |
  | #274 | pyjwt | 2.13.0 | 2.13.0 | SATISFIED |
  | #216 | idna | 3.18 | 3.15 | SATISFIED (E6-F1-S1-T2) |
  | #179 | urllib3 | 2.7.0 | 2.7.0 | SATISFIED (E6-F1-S1-T2) |

  **Starlette major-jump evidence (FR-6.2).** #277 moves starlette from
  `0.52.1` to `1.3.1`, a major-version jump reached transitively via `mcp`
  and `sse-starlette`; devbench imports no starlette symbol directly, so
  this is recorded evidence rather than an assumed zero blast radius.
  `mcp==1.29.0`'s own package metadata declares `starlette>=0.27` for
  `python_version < '3.14'` and `starlette>=0.48.0` for
  `python_version >= '3.14'`; `sse-starlette==3.3.2` (the lock's only other
  starlette parent) declares `starlette>=0.49.1`. The resolved `starlette
  1.3.1` satisfies every one of those lower bounds, confirming `mcp` accepts
  the resolved starlette rather than rejecting it -- the FR-6.2 BLOCK path
  (close #277 not-applicable with resolver evidence, never force-merge) was
  not triggered.

  **Six PRs closed unmerged with satisfying-commit evidence (AC-79).** Each
  of #287, #278, #277, #276, #275, and #274 is closed via `gh pr close`
  with a comment naming commit `6ec06c7a02deeb1714fd5c8bb45230971f65b603`
  as the satisfying lock advance; `gh pr view` confirms all six show
  `state=CLOSED`, `mergedAt=null`. None is merged, per FR-6.1's error
  handling: a dependabot branch whose target is already satisfied by the
  resolved lock is never merged.

  **Two targets bumped, independent of the mcp cascade (FR-6.3, AC-81,
  AC-82).** #216 (idna) and #179 (urllib3) needed an explicit bump because
  the mcp-family advance had no reason to move them, not because they sit
  outside the dependency graph the advance touched. `idna` in fact sits
  beneath `mcp` twice over: `mcp==1.29.0` depends on both `anyio` and
  `httpx`, and both of those depend on `idna`; `starlette` (one of the six
  targets moved by the mcp-family advance) also depends on `anyio` and so
  also reaches `idna`. `urllib3` reaches devbench only via `botocore`, which
  is not beneath `mcp`. In both cases the locked `idna 3.11` and
  `urllib3 2.6.3` already satisfied every lower bound declared anywhere in
  the resolved graph, so `uv lock --upgrade-package <mcp-family-target>`
  had no unsatisfied constraint to pull a newer `idna` or `urllib3` in; a
  targeted per-package upgrade of the mcp family simply never touches a
  transitive dependency whose currently-locked version already clears every
  floor. E6-F1-S1-T1's checker run captured both as
  `idna 3.11 < 3.15 NEEDS BUMP` and `urllib3 2.6.3 < 2.7.0 NEEDS BUMP`,
  reflecting the FR-6.3 target floor rather than a graph-resolution
  constraint. This task closes both with a single resolution:
  `uv lock --upgrade-package idna
  --upgrade-package urllib3` resolved cleanly on the first attempt (no
  per-target isolation was needed), moving `idna 3.11 -> 3.18` and `urllib3
  2.6.3 -> 2.7.0`. The `uv.lock` diff is exactly the two packages' version,
  sdist and wheel hash blocks -- no unrelated drift. The re-run checker shows
  all eight targets `SATISFIED`, closing the reconciliation E6-F1-S1-T1
  started, and `uv sync && make validate` exits 0 (6226 passed, 98.24%
  coverage), closing the dependency wave (AC-82). `.github/BATCH_PR_BODY.md`
  is added carrying the closing-keyword list for every issue this run
  resolves plus non-closing references to the six superseded dependabot PRs
  and the two bumped ones (#216, #179), for the operator to paste into the
  deferred single batch PR (spec S9, AC-83).

### Fixed

- **`devbench instances` still reported no running orchestrator for a daemon
  whose workspace lived outside `$HOME`** (`spec/devbench-observability-hardening.md`
  FR-D2/OAC-3, issue #270's companion defect D2, E7-F2-S1-T1).
  `_resolve_search_roots` (`src/devbench/instances.py:140-168`) defaulted to
  `[Path.home()]` alone whenever `DEVBENCH_INSTANCE_SEARCH_ROOTS` was unset, so
  a daemon started under a workspace outside `$HOME` (for example one checked
  out under `/workspaces`) was invisible to `devbench instances` unless the
  operator remembered to export the search-roots override by hand. The
  default now also includes the current `DEVBENCH_WORKSPACE_ROOT` (when set
  and not already under `$HOME`), so a workspace's own daemon is discoverable
  with no configuration; a workspace that already relies on
  `DEVBENCH_INSTANCE_SEARCH_ROOTS` sees byte-identical behavior, since that
  override still wins first and is returned verbatim. `docs/cli-reference.md`'s
  "Instances (per-host discovery)" section documents the three-tier
  resolution order.

- **`quota_handling.log_structured_events: false` had no effect: every
  `[QUOTA_*]` structured marker still emitted unconditionally** (spec
  Section 4 FR-2, AC-16, E9-F1-S2-T1). The config key was parsed, schema
  documented, and docstring-promised but consumed by zero call sites. A new
  `_quota_structured_events_enabled()` helper in `src/devbench/cli.py` reads
  `RUNTIME_CONFIG.quota_handling.log_structured_events` and now gates every
  structured-marker emission across `_handle_quota_pause`,
  `_dispatch_quota_detection`, `_dispatch_quota_timeout`, and the resume
  loop; `wait_for_reset` / `_wait_toward_reset` in `src/devbench/quota.py`
  gain a keyword-only `emit_structured_events: bool = True` gating the
  `[QUOTA_POLLING]` heartbeat, threaded from config at the `cli.py` call
  sites (`quota.py` still imports no config module). Slack notifications,
  audit comments, non-marker log lines, and checkpoint writes are explicitly
  NOT gated (decision D-10: markers only). Default `true` preserves
  byte-identical behavior for every workspace that has not set the flag.
  `docs/quota-handling.md` documents which markers are gated.

- **`Recent pace (last N tasks)`, `Average time per task`, and the ETA
  projection they feed sampled claim-to-done idle wall time as if it were
  execution time** (issue #326). A completion whose only `in-progress`
  anchor sat idle across an orchestrator session gap -- for example an
  operator `set-status <id> done` against a claim made in an earlier
  session -- was timed from that stale claim through to `done`, so the
  pace and average estimators (and the ETA/cost projections derived from
  them) could be skewed by wall-clock idle time that was never execution
  time. `_recent_pace_minutes` and `_compute_window_stats`
  (`src/devbench/reporting/report.py`) now accept a completion as a valid
  sample only when its `in-progress` anchor exists AND falls in the same
  orchestrator session as `done` (`_same_session`, session boundaries
  derived from the log's own non-noise timestamps): pace, average, and ETA
  no longer sample claim-to-done idle wall time. Both estimators now
  compute the MEDIAN of the resulting same-session execution-time samples
  instead of the arithmetic mean, so a single cross-session or outlier
  completion can no longer dominate the estimate. Completions dropped for
  having no execution window are no longer silently narrowed out of the
  sample count: the `Average time per task` and `Recent pace (last N
  tasks)` cells, and the trailing summary line when it drives the
  sentence, append `(<k> excluded: no execution window)` naming how many
  were dropped. `docs/cli-reference.md`'s ETA-formula note documents the
  median estimator and the exclusion suffix.

## [0.3.0] -- 2026-07-31

### Fixed

- **Every orchestrator start re-materialised a consumed proposal and recreated
  a duplicate work unit** (issue #302). `_find_draft_file` looked in exactly
  one directory, the story directory computed from the ID. A work unit living
  anywhere else read as absent, so `classify_proposed_task` reported it
  `UNMATERIALISED` and the orchestrate loop's opening `sweep-proposals`
  created it again in the canonical location, leaving two files and two index
  rows under one ID. Recovering cost roughly four minutes of orchestrator
  turns before any work unit was claimed, and it repeated on every start. A
  work-unit ID identifies one unit wherever its file sits, so the lookup now
  searches the backlog tree and refuses, rather than picking arbitrarily, when
  two files carry one ID. Separately, `sweep-proposals` now deletes a proposal
  once every task in it is resolved; leaving the JSON on disk also pinned the
  source task to `AWAITING_AMENDMENT_RECOVERY` indefinitely.

- **Quoting a `[BLOCKED_PENDING_PROPOSAL]` token inside a comment created a
  live marker** (issue #304). The scanner matched the token anywhere in the
  `## Comments` body, so an audit comment recording that a marker had been
  removed, quoting the removed line verbatim, silently re-blocked the unit on
  the quoted ID. The file read as correct to a human, because the only
  occurrence sat inside quotation marks, and agents write such narratives
  routinely. Both writers emit the marker as the final token of an audit row,
  so the pattern is now anchored there: writing *about* a marker no longer
  creates one.

- **An unscoped session wrote a `scope.json` its own readers reject** (issue
  #270; `spec/devbench-observability-hardening.md` FR-D1/OAC-1/OAC-2, defect
  D1). Session startup wrote a bare JSON array of IDs while
  `ScopeFilter.from_file` and `_read_scope_payload` require the canonical
  object. The two writers target the same path, because `resolve_scope_file_path`
  routes there whenever `DEVBENCH_SESSION_NAME` is set, so the array overwrote
  the object and every subsequent read raised
  `scope.json top-level payload must be an object, got 'list'`. Scoped
  sessions now write the canonical payload; unscoped sessions write no file,
  since absent is how every reader already expresses "no scope", and an empty
  scope would assert a filter matching nothing; a stale array file present at
  an unscoped session's start is still cleared at that point. Separately
  (E7-F1-S1-T1), a stale list-shaped file encountered at ANY other read --
  `ScopeFilter.from_file` and `_read_scope_banner_data` alike, so both
  `devbench next` and `devbench status` are covered -- is now migrated to the
  canonical object form in place (atomic rewrite, one INFO line naming the
  file) instead of raising; a second read of the same file proves the
  migration does not recur. Any other non-object shape still raises the
  pre-existing `ValueError` with its message text byte-preserved.

- **`devbench status` crashed with a traceback where `report` diagnosed**
  (issue #305). A missing work-unit file or a malformed index escaped `status`
  as a raw `FileNotFoundError` while `report` reported the same condition with
  an actionable message and a non-zero exit. An operator running both saw a
  crash and a clean error for one underlying state. The handler is now shared,
  so the two cannot drift apart, and it still distinguishes a missing file
  (possibly a transient writer-window race; re-run) from a malformed index
  (run `validate-backlog`).

- **The test suite wrote into the live workspace and orchestrator log**
  (issue #292; `spec/devbench-observability-hardening.md` FR-D4/OAC-5,
  defect D4). `tests/conftest.py` set `DEVBENCH_WORKSPACE_ROOT`,
  `DEVBENCH_PROJECT_ROOT`, `DEVBENCH_LOG_FILE` and `DEVBENCH_CONFIG_PATH`
  with `os.environ.setdefault`, so it INHERITED whatever the ambient shell
  already had. devbench is developed with devbench, so the executor runs the
  suite from inside a live workspace with those exported: fixture work-unit
  state landed in the real `.devbench/ci-failures/` and
  `.devbench/pr-bot-feedback/` under IDs that exist only in `tests/`, and
  fabricated lifecycle records were appended to the live log --
  `[ORCHESTRATOR_TERMINAL_EXIT]`, `[QUOTA_WAITING]`,
  `[ORCHESTRATOR_AUTO_RESTART]`, `Merged PR #42` -- for events that never
  happened. Those are exactly the markers the reporting layer parses, so a
  test run could drive an operator's `status` and `report` output. Assignment
  is now unconditional (force-assign, not `setdefault`), to a fresh per-run
  temporary workspace; an ambient value is the hazard, not a configuration to
  honour. Verified against a live workspace: the suite leaves a 180 MB
  orchestrator log byte-identical and the 197-file `.devbench/` tree
  unchanged. `tests/test_workspace_isolation.py` pins the isolation itself:
  a child pytest subprocess launched with a decoy live-like workspace
  exported proves the decoy log gains zero bytes and its `.devbench/`
  directory stays empty.

- **A spent executor retry budget was reported as "operator does nothing"**
  (issue #248). The orchestrate skill writes its retry-exhaustion `[BLOCKED]`
  row under `agent/orchestrator` naming the failing checks, which matched both
  the recovery agent-tag allowlist and the recovery body pattern (it includes
  `ALL_REVIEWS_FAILED` / `REVIEW_REJECTED`). The unit was therefore classified
  `AWAITING_AMENDMENT_RECOVERY`, whose contract is that the operator does
  nothing, while no further executor run was coming. Nothing cleared it, no
  operator notification fired, and the run stalled silently. The skill now
  emits an explicit `[RETRY_BUDGET_EXHAUSTED]` tag on genuine exhaustion and
  the classifier returns `OPERATOR_ACTION_REQUIRED` for it. A live
  `[BLOCKED_PENDING_PROPOSAL]` cascade still wins, because that genuinely will
  clear the unit.

- **The in-progress timer under-reported how long a work unit had been
  running** (issue #293). The pattern allowed `.*` between the log timestamp
  and the phrase `Set <id> to 'in-progress'`, so it matched lines that merely
  quoted the transition. The orchestrator logs whole SDK messages, and a tool
  result that read a work unit's `[WU_CLAIMED]` audit comment reproduces that
  phrase inside a line stamped with the time of the dump; being later, the
  echo won the `max()`. Observed: a unit claimed at 12:11 reported as claimed
  at 12:38, and the error grew with every further echo, so a long-running unit
  could keep resetting toward "just started". The pattern is now anchored to
  the emitting logger and level.

- **`validate-backlog` passed on duplicate work-unit IDs** (issue #291). One
  unit written into two directory trees yields two index rows under one ID.
  Every existing check passed -- both files exist, each matches its own row's
  status, and neither is orphaned because both are indexed -- while the rows
  disagreed about status (`done` in one tree, `declined` in the other) and a
  dependency on that ID resolved against whichever row was reached first. New
  check 21 reports every ID carrying more than one index row, naming each
  status and path. Status Summary rows, which repeat an ID without a File
  Path, are not index rows and are excluded.

- **`devbench report` did not say whether the run could proceed** (issue
  #251). `status` ended with `Next actionable` / `All work units are DONE.` /
  `No actionable units. N blocked.`; `report` ended with none of them, and its
  per-status counts cannot substitute -- a backlog can hold many `in-queue`
  units with nothing actionable, because only leaf Tasks execute and every one
  may be waiting on a dependency. Both commands now render the same line from
  one shared helper. `devbench next` keeps its machine tokens, which are a
  contract consumed by the orchestrate skill.

- **The orchestrator-alive banner reported ALIVE with no orchestrator
  running** (issue #250; `spec/devbench-observability-hardening.md`
  FR-D3/OAC-4, defect D3). Liveness was derived from log-activity recency
  alone, but a recent log line proves only that something wrote to the log,
  not that the writer still exists: a crashed or killed daemon read as ALIVE
  for the whole quiet window, and any other writer kept it ALIVE indefinitely.
  The banner now reads the workspace PID file and checks the process table,
  and reports five states (ALIVE, with an idle variant; STOPPED; STARTING;
  NOT RUNNING; UNKNOWN). `stop_hook.window_seconds` no longer decides
  liveness; it distinguishes a busy live orchestrator from an idle one, so a
  running-but-quiet orchestrator is never reported STOPPED. Per obs-spec
  decision OD-3, this fix owns the verdict only; the "last activity" recency
  line still reads whichever log `devbench report` resolved for the
  invocation -- the workspace's aggregate `logs/orchestrator.log` by
  default, or the named session's log when `--session <name>` is passed
  (E7-F3-S1-T1, declined as superseded: PR #295 already carried this fix
  before the task reached the queue).

- **`devbench start --help` omitted the scope-filter flags** (issue #249).
  `--include`, `--exclude`, `--name` and `--allow-overlap` were accepted by the
  parser and documented nowhere, so the only way to discover them was to read
  the parser. The registry description was also the single source for both the
  one-line command list and per-command help, leaving no room for flag
  documentation. A description may now span lines: the command list shows the
  first line, and `<cmd> --help` prints the whole text.

- **A blocked work unit's uncommitted changes contaminated every unit that
  claimed after it, and now get quarantined instead.** The single-branch modes run every work unit in one shared
  checkout. When a unit blocked before its work was committed, that work stayed
  in the tree, and nothing cleared it or reported it. The next unit to claim
  inherited it, with two distinct failure modes observed in one run: a unit's
  judges rejected it for staged files belonging to a blocked sibling, and a
  docs-only unit was blocked by a security review whose every finding cited a
  blocked sibling's unstaged source, which the docs-only unit neither owned nor
  was permitted to touch. Neither unit could act on the rejection, and the
  residue survived to contaminate the unit after that. `devbench claim` now
  refuses, before any executor or judge time is spent, when the target checkout
  holds uncommitted changes outside the claiming unit's Changes Manifest, naming
  every offending path so the residue is attributed to the unit that produced
  it. The check covers staged, unstaged, and untracked-but-not-gitignored paths;
  `assert_staged_matches_manifest` sees only the staged set, which is why a unit
  that blocked before staging read as a clean tree. Re-claiming an `in-progress`
  unit is unaffected: its own manifest files are never quarantined.

  devbench runs unattended, so the residue is moved rather than reported. Each
  foreign path is stashed under the ID of the unit whose Changes Manifest
  declares it, and the claim proceeds against a checkout holding only the
  claiming unit's scope. Stopping to ask an operator would turn one blocked
  unit into a stopped run. Quarantine is non-destructive: each entry is a
  normal git stash titled `devbench-quarantine:<owner-id>`, one per owning
  unit, recoverable with `git stash list` / `git stash apply`, and the owning
  unit receives a `[WORK_QUARANTINED]` audit comment naming it. Paths no unit
  declares are quarantined under an `unattributed` key, since they would
  corrupt the claiming unit's commit just the same. Nothing is restored
  automatically: a blocked unit re-executes from its Changes Manifest when it
  unblocks, and re-injecting a superseded attempt would recreate the
  contamination.

  **Behaviour change:** `devbench claim` exits non-zero only when the
  quarantine itself fails or leaves residue behind, which means the checkout
  was not actually cleared. When the unit's repo has no configured local
  checkout there is no shared tree to guard; the step logs that it was skipped
  rather than passing over it silently.

- **A work unit's commit could absorb another unit's unstaged changes.** Both
  commit paths ran `git add -A`, staging the entire working tree and committing
  it under the current unit's message, so any file another in-flight unit had
  left modified-but-unstaged was swept in. The guard meant to prevent exactly
  this could not see it: `assert_staged_matches_manifest` reads
  `git diff --cached`, which by definition excludes unstaged changes, so it
  verified the index, passed, and then `add -A` staged everything the check had
  just ignored. The victim task was then unrecoverable -- its declared files
  committed under another unit's name, failing `changes_manifest` permanently,
  with no remedy short of an operator override or rewriting published history on
  a shared branch. Both entry points now stage the Manifest paths the callers
  already parse for the scope check. There is no degraded mode: a caller that
  cannot resolve a Manifest, or whose Manifest holds only execution-time
  sentinels, is refused rather than silently given a whole-tree commit.
  `git-ops-finalize`, which batches many units and legitimately has no single
  Manifest, opts into whole-tree staging explicitly via `stage_all`.

  **Behaviour change:** `devbench git-ops` now exits non-zero when it cannot
  resolve the work unit's file. Previously it warned and committed anyway. The
  post-commit audit-comment write remains best-effort and never fails the run.

## [0.2.0] -- 2026-07-29

This release bundles the orchestrator self-healing work, the canonical
configuration refactor, the EC2 remote-dev provisioning stack, and the
work-unit lifecycle / authoring CLI improvements that have accumulated
since the last release. PR #119 carries every change.

### Fixed

- **Recovery tasks were materialised but never wired.** The `write-proposal`
  auto-cascade skipped `promote_proposal` for any draft not in `proposed` state.
  Under `backlog.default_status_for_new_work_units: in-queue` every fresh draft is
  born promoted, so the guard skipped it -- and with it the dependency row and the
  `[BLOCKED_PENDING_PROPOSAL]` marker the auto-unblock cascade reads. The source
  task stayed blocked indefinitely with nothing naming what it waited for. Drafts
  materialised by the current call are now always wired.
- **Consumed proposals were never deleted.** `delete_proposal` existed but had no
  caller. Because the recovery classifier tests for the proposal file's presence
  alone, a spent proposal pinned its source task to `AWAITING_AMENDMENT_RECOVERY`
  permanently -- reported as self-healing, so never surfaced as
  `OPERATOR_ACTION_REQUIRED` -- while `write_proposal` refused to emit a
  replacement. The proposal is now dropped once every proposed task has been
  resolved.
- **Materialised tasks landed in an orphan directory tree.** The story-directory
  helper derived its path from bare IDs (`backlog/E4/E4-F1/E4-F1-S1`), but
  `spec-to-backlog` names every level `<id>-<slug>`, so recovery tasks were written
  into a parallel tree with no Epic / Feature / Story work-unit files. That hid
  existing siblings from the story scan and could make ID allocation return an ID
  that already existed, overwriting a live task. An existing directory now always
  wins, preferring the one that holds the Story's own work-unit file.

### Changed (BREAKING)

- **devbench.yaml default changes.** Several built-in defaults changed; workspaces
  that omit these keys get the new behaviour:
  - `manifest_amendment.enabled` now defaults **true** (was `false`) -- the Changes
    Manifest amendment workflow is active unless explicitly disabled.
  - `validate.check_orphan_path_tokens` now defaults **true** (was `false`) --
    `validate-backlog` runs Rule 20 (AC/DoD path-coherence) by default. Set `false`
    to opt out if a pre-existing backlog is not yet compatible.
  - `task_factory.enabled` now defaults **true** (was `false`) and
    `task_factory.auto_accept_proposals` now defaults **false** (was `true`
    as shipped in released tag `0.1.0` via PR #202) -- both flipped together
    in the same commit per D-11 (issue #259, ADR-32) so the on-by-default
    loop never grants an unreviewed orphan-auto-promote path. Relative to
    `0.1.0` the user-visible change is that the loop now runs for every
    backlog by default; a freshly materialised draft's initial status was,
    and remains, `backlog.default_status_for_new_work_units` (default
    `in-queue`, AC-189-8) regardless of `auto_accept_proposals` -- that flag
    instead governs two auto-promote paths: `write-proposal` itself no
    longer synchronously materialises-and-promotes the proposal it just
    wrote inside the same invocation by default (that cascade only fires
    when `auto_accept_proposals` is explicitly `true`), so a freshly
    written proposal now waits for the next `sweep-proposals` tick to
    become actionable; and `sweep-proposals` no longer auto-promotes a
    draft explicitly left at `## Status: proposed` (a legacy/hand-edited-
    draft case, not something the normal materialise path produces) unless
    the flag is set. Existing backlogs that explicitly disabled
    `manifest_amendment` and never mentioned `task_factory` are unaffected:
    the defaulted-on `task_factory.enabled` downgrades to disabled rather
    than failing config-load in that combination (see ADR-32's interaction
    contract). See the migration note below for the exact keys to restore
    each pre-flip behavior.
  - `merge_strategy` default is now explicitly `squash` at the config layer.
  - `timeouts.executor` and `timeouts.executor_max_turns` were **removed** -- they
    were parsed but never consumed (dead config); removing them changes no behaviour.

- **Plugin split: `devbench@devbench` retired in favour of two
  marketplaces / two plugins** (issue #224). The single plugin had a
  structural conflict between the authoring audience
  (`spec-to-backlog`, which writes `backlog/*.md`) and the executor
  audience (the `guard-work-unit-write.sh` hook blocks any write to
  `backlog/*.md`). The split encodes the policy at the plugin-
  installation layer:
  - **`devbench-orchestrate`** (orchestrate marketplace, this repo's
    `plugin/`) -- ships the orchestrate skill + every agent + every
    PreToolUse guard hook. Install in a workspace where you run
    autonomous execution.
  - **`devbench-authoring`** (authoring marketplace, this repo's
    `plugin-authoring/`) -- ships `spec-to-backlog`, `create-spec`,
    `configure-devbench`, `bootstrap-environment`. No hooks; safe to
    enable in any authoring workspace.

  See `docs/migration-0.4.0.md` for the operator migration walkthrough.
  Note: prior CHANGELOG entries that reference `plugin/devbench/`
  paths describe the pre-split layout and are not rewritten.

  **Source-tree changes** (paths relative to repo root):
  - `plugin/devbench/` -> `plugin/devbench-orchestrate/` (renamed;
    four authoring skills removed from this side).
  - `plugin-authoring/devbench-authoring/` (new; receives the four
    authoring skills + a fresh `.claude-plugin/plugin.json`).
  - `plugin/.claude-plugin/marketplace.json` -- orchestrate
    marketplace, version `0.2.0` -> `0.3.0`; lists one plugin
    `devbench-orchestrate@0.4.0` at `./devbench-orchestrate/`.
  - `plugin-authoring/.claude-plugin/marketplace.json` (NEW) --
    authoring marketplace, version `0.1.0`; lists one plugin
    `devbench-authoring@0.1.0` at `./devbench-authoring/`.

  **Sub-agent invocation prefix changed** (every reference inside
  the orchestrate plugin): `devbench:executor` ->
  `devbench-orchestrate:executor`, same for `blocker-resolver`,
  `task-factory`, `manifest-amender`, `security-reviewer`,
  `review-supervisor`, `code_review`, `test_review`, `doc_review`,
  `changes_manifest`, `orchestrate`. The `guard-review-supervisor-
  scope.sh` and `guard-verdict-format.sh` scripts now check the
  new prefix.

  **Narrow Python source edits** (AC-12 deviation, called out for
  audit):
  - `src/devbench/constants.py`: `DEFAULT_PLUGIN_SUBPATH` flipped
    from `"plugin/devbench"` to `"plugin/devbench-orchestrate"`.
  - `src/devbench/cli.py`: the SDK launch prompt that invokes the
    orchestrate skill now reads `"Run the
    devbench-orchestrate:orchestrate skill..."`.

  These two are the minimum required for non-interactive
  `devbench start --daemon` runs to find the renamed plugin tree.
  No agent prompt, no hook script, and no other Python source is
  changed.

  **New regression tests** pin the orchestrate side's behaviour
  (`tests/test_plugin/test_orchestrate_isolation.py`,
  `test_executor_guard_unchanged.py`,
  `test_work_unit_write_block_message.py`). Any future drift in
  the PreToolUse hook list, the guard-work-unit-write stderr
  format, or the orchestrate plugin's self-containedness fails
  these tests locally before push.

### Added

- **`git_ops.branch_prefix` / per-repo `branch_prefix` task-branch
  namespacing** (issue #283). Task branches were always named
  `backlog/<unit-id-lower>` with no workspace identifier. When two
  independent devbench workspaces (each numbering its own backlog from
  `E1-F1-S1-T1`) target the same downstream repo, their task branches
  collide by name -- a run hit this live: workspace A's already-merged
  `backlog/e1-f1-s1-t1` PR blocked workspace B's unrelated `E1-F1-S1-T1`
  push with a non-fast-forward rejection, since it was a different task
  that happened to share an ID, not a real merge conflict. Setting
  `git_ops.branch_prefix` (optionally overridden per-repo via
  `repos.<org/repo>.branch_prefix`) namespaces per-unit branches as
  `backlog/<prefix>/<unit-id-lower>` and `single_branch` as
  `<prefix>/<single_branch>`, eliminating the collision in both modes.
  Unset by default -- existing single-workspace setups are unaffected.
  See `docs/devbench-yaml-reference.md`.

- **`spec-to-backlog` append-mode `BACKLOG.md` regeneration**
  (issue #225). Materialising a new spec on top of an existing
  populated backlog (E17+ on top of E1-E16) previously required the
  operator to merge `BACKLOG.md` by hand because Step 6 of the
  skill rewrote the file from scratch. The new
  `regenerate_backlog_index` post-processor pass appends new epic
  rows to the Status Summary table and new work-unit rows to the
  Full Work Unit Index; existing rows are byte-for-byte preserved.
  When a new epic ID collides with an existing index row at a
  different file path the pass raises
  `BacklogAppendCollisionError` (fail-fast). Greenfield invocations
  fall back to the skill's existing write path.

- **Code Standards block templater** (issue #230). The
  `spec-to-backlog` skill no longer needs to re-type the ~50-line
  `### Code Standards` block per task. A new
  `devbench.plugin_helpers.code_standards_template.emit_code_standards_block`
  helper emits the canonical block with three placeholder
  substitutions (`<WORKSPACE_CLAUDE_MD>`,
  `<TASK_SPECIFIC_ERROR_PATHS>`, `<REPO_CARVE_OUTS>`). Workspaces
  override the default body by placing
  `code-standards-canonical.md` at the workspace root. The
  companion `verify_code_standards_canonical` post-processor pass
  reports the count of tasks whose Code Standards block has drifted
  (check-only; does NOT mutate). See `docs/code-standards-canonical.md`.

- **`devbench report --by-role` per-role token/cost breakdown panel**
  (issue #206). Wires the data path landed in PR #202 (issue #123 via
  ``_parse_transcript_metrics_by_role``) into the rendered output of
  ``devbench report``. Off by default; without the flag the output is
  unchanged. With ``--by-role``, a Per-role cost breakdown panel
  renders beneath the existing aggregate Cost section, listing
  ``executor``, ``code_review``, ``test_review``, ``doc_review``,
  ``changes_manifest``, ``security_review``, ``blocker_resolver``,
  ``manifest_amender``, ``task_factory``, ``orchestrator`` rows
  (each present only when that role has activity in the window) with
  input/output/cache-read/cache-write tokens, message count, and
  est_cost.  The TOTAL row equals the sum of the per-role rows -- a
  render-time invariant pinned by the regression test.  Per-role and
  per-model (issue #223) are orthogonal axes available independently;
  the per-model rate table prices each role's tokens at the model
  that actually produced them.
- **Per-model token pricing in `devbench report`** (issue #223). The
  retired single-rate model is replaced by a per-model rate table under
  ``report.models`` in ``backlog/config/devbench.yaml``. Every key is a
  Claude model id (the literal ``message.model`` value Claude Code
  records on every ``assistant`` envelope, e.g. ``claude-opus-4-7``);
  every value is a ``{input, output, [cache_read_multiplier],
  [cache_write_5min_multiplier], [cache_write_1hr_multiplier],
  [correction_factor]}`` block. ``report.default_model`` is applied to
  any model id not present in ``report.models`` and to entries with no
  model attribution (NULL rows in the SQL cache aggregate under the
  ``"<unknown>"`` sentinel bucket). Schema enforces
  ``additionalProperties: false`` on each model entry while leaving the
  model-id KEY open (any string), so operators add new models without a
  code change. Default rate table in ``src/devbench/constants.py``
  mirrors the canonical Anthropic Standard pricing table verbatim;
  ``sample-config.yaml`` ships the same table as a starter block. New
  module-level constants ``REPORT_MODEL_RATES`` and
  ``REPORT_DEFAULT_MODEL_RATES`` expose the merged view at runtime.
  ``docs/model-pricing.md`` rewritten; new ``docs/cost-accuracy.md``
  documents the attribution chain (transcript -> SQL -> aggregator ->
  cost) and how to audit it.
- **Per-call model attribution end-to-end** (issue #223 phase 1).
  ``hook_entries`` and ``transcript_entries`` SQL tables gain a
  ``model TEXT`` column populated at parse time from
  ``tool_response.model`` and ``message.model`` respectively. NULL
  rows aggregate under the sentinel key ``"<unknown>"``. Schema bumped
  to v4; pre-v4 caches are dropped + rebuilt at next open via the
  existing version-mismatch handler. New aggregators
  ``aggregate_hook_window_by_model`` and
  ``aggregate_transcript_window_by_model`` return
  ``dict[str, dict[str, int]]`` (``model_id -> totals_dict``); their
  per-bucket totals roll up to the single-bucket aggregator's result
  so no tokens are silently dropped. New per-model dispatcher
  ``_compute_cost_by_model`` in ``reporting/report.py`` iterates
  ``{model_id -> HookLogTotals}``, prices each bucket against its own
  rates via the existing ``_compute_cost``, and composes the per-model
  ``correction_factor`` AFTER all other factors. Both call sites of
  ``_compute_cost`` (in-window cost and the ``_recent_per_task_cost``
  projection) now feed the new dispatcher.
- **`devbench cost-calibrate <actual-usd> [--window <ISO-8601>]`**
  (issue #223 phase 3). Operator-facing calibration command: sums
  devbench's reported per-model cost across the window, derives
  ``correction_factor = actual_usd / reported_total``, and writes the
  factor back to ``report.models.<id>.correction_factor`` in
  ``backlog/config/devbench.yaml`` for every model that contributed.
  Successive calibrations replace (not multiply) the prior factor so
  re-running is idempotent against a fixed actual-spend figure. Writes
  ``input`` and ``output`` from the canonical default rate table when
  the operator's yaml does not yet list a model the calibration
  observed (the resulting yaml is immediately schema-valid). YAML
  round-trip via ``yaml.safe_load`` + ``yaml.safe_dump``; atomic
  tmp+rename write so an interrupted calibrate does not corrupt the
  config. Reuses the per-model attribution from phase 1, so the
  command works against any ``feat/issues-188-193`` workspace whose
  cache has been refreshed.
- **Discovery-artifact coverage rubric for `spec-to-backlog`** (issue
  #221 A1). ``plugin/devbench/skills/spec-to-backlog/SKILL.md`` Step 2
  now accepts an optional second positional argument
  ``discovery_artifacts_dir`` pointing at a directory of discovery
  artefacts (typically ``spec/<run>/_workspace/``) that the spec was
  authored from. Recognised artefact filenames:
  ``verification_matrix.md``, ``ci_failures.md``,
  ``test_coverage_audit.md``, ``ambiguities.md``, ``scope_creep.md``.
  When supplied, Step 4b adds a new mandatory rubric item ("every row
  in every recognised artefact file must be covered by at least one
  leaf task") and Step 5b adds a per-task counterpart asserting the
  covering task explicitly cites the artefact row in its AC or
  Approach. Step 8 emits a new
  ``[DISCOVERY_COVERAGE] <covered>/<total>`` audit line so the audit
  trail records the coverage check having run. Skipped silently when
  ``discovery_artifacts_dir`` is absent -- no behavioural change for
  legacy invocations. The orthogonal spec-AC -> leaf-task rubric item
  (Step 4b item 4) is unchanged; this new rubric is the safety net for
  rows the spec author may have omitted an AC for.
- **Per-task checkpoint API for resumable spec-to-backlog runs** (issue
  #221 A3). New ``PerTaskCheckpoint`` dataclass plus
  ``read_per_task_checkpoint`` / ``write_per_task_checkpoint`` helpers
  in ``src/devbench/skill_state.py``. Persists the set of leaf-task IDs
  already authored to ``<workspace>/.devbench/skill-state/
  spec-to-backlog-tasks.json`` (alongside but separate from the
  iteration-counter file). When the skill is re-invoked after an
  interrupted run, it reads the checkpoint and resumes from the first
  un-completed task instead of regenerating every file. Atomic-write
  contract identical to the existing checkpoint API (tmp + rename, no
  partial reads). 100% coverage in ``tests/test_skill_state.py``.
- **Skill arg parsing for spec-to-backlog** (issue #221 A2).
  ``spec-to-backlog/SKILL.md`` Step 2 now accepts the spec path via
  ``args`` and only prompts when no arg was supplied -- the orchestrator
  can dispatch the skill non-interactively as part of a longer chain.
- **AC N/A-suffix grammar documented** (issue #221 D3). New section in
  ``docs/acceptance-criteria-canonical.md`` enumerates the
  case-insensitive accepted variants (mixed case OK; structure is what
  matters) and the things that must NOT change (``--`` double-dash
  sentinel, literal ``N/A`` token, literal ``Tasks`` plural, trailing
  parenthesised reason). Catches authors who write ``- N/A``,
  ``NA without slash``, or omit the parenthesised reason.

- **Backlog post-processor module for spec-to-backlog skill** (issue
  #221 A11, A12, A13). New ``src/devbench/plugin_helpers/`` package
  with ``backlog_post_processor.py`` exposing three deterministic
  passes the LLM-driven ``spec-to-backlog`` skill invokes between task
  authoring (Step 5) and validate-backlog (Step 5d):
  - ``sanitize_markdown_pipes_in_manifest`` escapes raw ``|`` inside
    Manifest annotation cells.
  - ``dedupe_manifest_rows`` collapses identical Manifest rows to one.
  - ``suffix_ref_on_orphan_paths`` adds ``(ref)`` after backtick-quoted
    path tokens in AC / DoD prose that are not in the Manifest.
  All passes are idempotent. The convenience entry point
  ``run_all(backlog_dir)`` returns a ``{pass_name: count}`` mapping
  the skill emits as ``[POST_PROCESS]`` audit rows. New docs page
  ``docs/skills/backlog-post-processor.md`` documents the module
  contract and the pattern for adding future passes. The skill's
  ``spec-to-backlog/SKILL.md`` Step 5d now invokes the post-processor
  before each ``validate-backlog`` call so the backlog lands green on
  first try instead of requiring an operator-facing fix loop.

- **Application-agnostic `spec-to-backlog` and `create-spec` skills**
  (issue #221 E1-E10). Both bundled SKILL.md files no longer hardcode
  the ``/workspaces/rpm-migration/kanon-deps-work/...`` exemplar path.
  Each skill now:
  - Resolves an optional workspace exemplar from a new ``skills:``
    YAML section (``skills.exemplar_backlog_path`` and
    ``skills.exemplar_spec_path``). Absent / non-existent paths cause
    the skill to skip the read entirely and rely on the canonical-
    section list embedded in the SKILL.md as the authoritative quality
    bar.
  - Enumerates the canonical structure (15 task-file sections for
    spec-to-backlog; 16 spec sections for create-spec) directly inside
    the SKILL.md so a workspace with no exemplar still produces a
    valid artefact.
  - Emits ``[QUALITY_REFERENCE] <resolved-path>`` on success when an
    exemplar was configured, or
    ``[QUALITY_REFERENCE] <embedded-canonical-sections>`` when one was
    not (the audit trail records what was consulted).
  - Reads ``skills.fan_out_threshold`` (default 10) and
    ``skills.max_iterations`` (default 5) for its parallel-authoring
    and convergence-budget knobs. New ``SkillsConfig`` dataclass +
    schema block + ``_parse_skills_config`` runtime validation +
    ``sample-config.yaml`` entry. New docs page
    ``docs/skills/exemplar-reference.md`` documents the
    resolution-order and provenance contract.

### Fixed

- **SDK teardown race downgraded to WARNING with tracking-issue link**
  (issue #232; tracks upstream #231). `claude-agent-sdk`'s
  `Query.close()` raises
  `RuntimeError: Attempted to exit cancel scope in a different task
  than it was entered in` on every successful session teardown.
  Surfaced as `[asyncio] ERROR Task exception was never retrieved`
  AFTER `[ORCHESTRATOR_TERMINAL_EXIT]`, the trace caused remote
  execution environments to mis-classify a successful orchestrator
  run as failed (any stderr `ERROR` line tripped their pipeline). A
  narrow asyncio exception-handler filter in the new
  `devbench.sdk_teardown_filter` module intercepts the EXACT known
  signature (RuntimeError class + verbatim marker substring + a
  traceback frame in `claude_agent_sdk/_internal/*`) and logs a single
  WARNING on the `devbench.sdk` logger that references devbench#231.
  Anything that does NOT match the signature falls through to the
  default handler and surfaces at ERROR. Full traceback is preserved
  at DEBUG. The filter is installed via an async context manager
  (`sdk_teardown_filter.guard()`) wrapped around the SDK query loop in
  `cmd_start`, so the previous handler is restored on every exit
  path. Filed upstream as
  [anthropics/claude-agent-sdk-python#983](https://github.com/anthropics/claude-agent-sdk-python/issues/983);
  the workaround is removed in the same commit that bumps the SDK
  pin once that lands (devbench#231 stays open until then).

- **`backlog_post_processor` scope + terminal-status guards**
  (issue #226). Materialising a new spec on top of an existing
  populated backlog could silently mutate already-done work units
  because every pass walked the full backlog tree. Every pass now
  accepts an optional `scope_paths=[...]` argument that confines
  the walk to the supplied epic directories, AND defaults to
  skipping any file with `## Status: done` or `## Status: declined`
  (terminal-status guard). The `force_terminal=True` override
  remains available for one-time mass migrations.

- **`spec-to-backlog` Changes Manifest column-format pinning +
  normaliser** (issue #227). The skill prompt now explicitly
  mandates the 2-column `| File | Change |` form (`parse_manifest`
  rejects any other column count). The new
  `normalize_manifest_column_count` post-processor pass collapses
  N-column variants (`| Repo | Path | Action |`,
  `| File | Change | Notes |`, 4-column variants) to the canonical
  form losslessly: when `header[0]` is `Repo`, columns 0+1 merge
  into the File cell as `repo -- path` and column 2+ joins into
  Change with ` -- `.

- **`spec-to-backlog` AC-FINAL N/A tier-suffix auto-fixup**
  (issue #228). The validator's Rule 13 requires Python-tooling
  AC-FINAL lines (002 ruff format, 003 ruff check, 004 mypy, 005
  pytest tier, 006 pytest other tier, 008 bandit, 014 coverage) to
  carry an explicit `-- N/A for <Tier> Tasks (no Python source
  authored)` suffix on tasks whose Changes Manifest contains zero
  `.py` paths. The skill prompt's Step 5b rubric now mandates this,
  and the new `suffix_na_on_non_python_tasks` post-processor pass
  appends the suffix deterministically when missing.

- **`spec-to-backlog` prompt drift from validator** (issue #229).
  Three sub-drifts resolved: Step 1b item 2 (Status) now explicitly
  pins the constraint that `draft` is only valid for Task work
  units (`_check_status_enum` rejects `draft` on Epic / Feature /
  Story); Step 5a now mandates the canonical dep-ID regex
  `E\d+(-F\d+)?(-S\d+)?(-T\d+)?` and forbids directory-slug forms
  (`E16-test-cleanup`); Step 6 Status Summary count semantics now
  match what the validator's `_compute_epic_counts` does (Features
  + Stories + Tasks under the Epic; the Epic file itself is NOT
  counted). The new `normalize_dep_ids` post-processor pass
  rewrites slug-form dep IDs to canonical form when found.

- **`devbench` exits cleanly when a required env var is missing**
  (issue #221 B7). The two import-time required env vars
  (``DEVBENCH_WORKSPACE_ROOT`` and ``DEVBENCH_CLAUDE_MODEL``) used to
  raise ``RuntimeError`` when absent, which fires before
  ``cli.py::main`` is reached and prints a multi-line Python traceback
  to stderr with empty stdout. Operators running ``devbench report``
  with stdout-only redirection (``devbench report > out.txt``) saw the
  empty output as "rc=0, no useful output" -- the exact symptom the
  issue is filed against. ``_require_env`` now writes a single
  actionable line to stderr (``devbench: DEVBENCH_WORKSPACE_ROOT
  environment variable is not set. Set it to the absolute path of your
  workspace root.``) and exits with code 2. Conftest sets both env
  vars, so the test suite is unaffected; the new contract is asserted
  directly in ``tests/test_config.py::TestRequireEnv``.
  ``docs/cli-reference.md`` calls out the required-env-vars contract
  under the ``report`` section so operators don't run into this again.
- **Classifier accepts hyphen-form recovery-agent tags in `[BLOCKED]`
  audit rows** (issue #211). ``_RECOVERY_AGENT_TAGS`` enumerates the
  canonical underscore form (``agent/manifest_amender``), but
  ``amendment.py::AMENDER_AGENT_ID`` and several other writers emit the
  hyphen form (``agent/manifest-amender``). Before the fix the hyphen
  form silently failed the frozenset membership check inside
  ``_recent_recovery_audit_comment``, so ``classify_blocked_task`` fell
  through to ``OPERATOR_ACTION_REQUIRED`` for rejected-amendment audits
  that should have classified as ``AWAITING_AMENDMENT_RECOVERY``. Adds
  a tiny one-way normaliser (``_normalize_agent_tag`` -- ``hyphen ->
  underscore`` inside the ``agent/`` namespace only) that runs before
  the frozenset lookup. New parametrised regression matrix in
  ``tests/test_backlog/test_proposal.py`` covers both forms for every
  recovery agent plus a negative case for non-recovery agents.
- **Manifest entries with glob patterns are rejected** (issue #221 B4).
  A Changes Manifest entry containing ``*`` or ``**`` (e.g.,
  ``src/devbench/**/*.py``) used to silently flow through to
  ``_check_source_test_pairs`` which then emitted a confusing error
  about a missing ``test_*.py`` pair for the glob literal. The
  validator now rejects globs at a dedicated pre-check
  (``_check_no_glob_in_manifest``) with a message pointing at the
  sentinel + ``manifest_amendment`` alternative.
- **Source-test atomicity Update-vs-Add docs** (issue #221 B5). Added
  worked example to ``docs/source-test-atomicity.md`` clarifying that
  an ``Update`` annotation on an existing ``test_*.py`` file satisfies
  Rule 14 the same way an ``Add`` does. The docstring of
  ``_check_source_test_pairs`` references the doc.
- **Status Summary count semantics documented** (issue #221 B6). The
  "In Queue" column counts ALL in-queue work units per epic, not just
  leaf tasks. ``docs/backlog-contract.md`` now explains the count
  formula and shows a worked example so authors building BACKLOG.md by
  hand don't miscount.
- **Manifest parser honours markdown-escaped pipes** (issue #221 B1).
  ``_parse_body`` now treats ``\|`` as a literal pipe inside Changes
  Manifest cells, so prose like ``run cmd output \| grep -v debug`` no
  longer triggers ``ManifestParseError: Manifest row must have exactly
  2 columns``. Genuine 3-column rows (no backslash) still raise.
- **Bare ``.md`` extension is no longer a path token** (issue #221 B2).
  Rule 20 (orphan path) used to flag prose like "only ``.md`` files
  modified" because the 3-char string ends in a known extension.
  ``_is_path_shaped`` now requires a filename stem or directory
  separator before treating a token as a path; bare extensions in
  prose pass through. Real paths (``docs/foo.md``) are still flagged.
- **Sentinel Manifest values are exempt from path-based rules** (issue
  #221 B3). New ``devbench.backlog.sentinels`` module enumerates the
  accepted Manifest sentinel values (``<verification-only>``,
  ``<decision-only>``, ``<no changes>``, ``<no-op>``,
  ``<source-drift-fix-targets-determined-at-execution>``) plus the
  ``<name>``-pattern that catches operator-defined variants. These
  values are no longer treated as "real paths", so multiple tasks
  declaring the same sentinel don't trigger spurious Manifest Conflict
  Rule violations, decision-only tasks don't trigger source-test
  atomicity, and AC prose mentioning ``<verification-only>`` doesn't
  trigger orphan-path detection.

### Breaking changes

- **Removed legacy single-rate token-cost fields** (issue #223).
  ``report.token_cost_per_million_input``,
  ``report.token_cost_per_million_output``, and
  ``report.token_cost_discount`` were retired in favour of per-model
  pricing under ``report.models`` (see Added above). Per CLAUDE.md
  "Complete Replacement of Superseded Code" there is no deprecation
  shim: workspaces that still set the legacy keys get an immediate
  ``ValueError`` at config-load time naming the offending fields and
  pointing at the new block syntax + ``docs/model-pricing.md``.
  Migration: replace the three scalar keys with a ``report.models``
  block (copy the starter block from ``sample-config.yaml`` or the
  Standard pricing table in ``docs/model-pricing.md``); express any
  non-zero ``token_cost_discount`` as a per-model
  ``correction_factor = 1.0 - <old_discount>``, OR run
  ``devbench cost-calibrate <actual-usd>`` once to derive the
  corrected factor from a real invoice. The package-level constants
  ``DEFAULT_TOKEN_COST_PER_M_INPUT``,
  ``DEFAULT_TOKEN_COST_PER_M_OUTPUT``, and
  ``DEFAULT_TOKEN_COST_DISCOUNT`` were removed from
  ``src/devbench/constants.py``; callers consume
  ``DEFAULT_MODEL_RATES`` + ``DEFAULT_FALLBACK_MODEL_RATES`` instead.
  Module-level constants ``TOKEN_COST_PER_M_INPUT``,
  ``TOKEN_COST_PER_M_OUTPUT``, and ``TOKEN_COST_DISCOUNT`` in
  ``src/devbench/config.py`` were removed; consumers read
  ``REPORT_MODEL_RATES`` and ``REPORT_DEFAULT_MODEL_RATES``.

### Added

- **Daemon mode + lifecycle CLI (`start --daemon`, `instances`, `stop`,
  `tail`, `restart`)** (issue #209). `devbench start --daemon` (or `-d`)
  double-forks into the background, redirects stdout/stderr to
  `<workspace>/logs/orchestrator.log`, writes a PID file at
  `<workspace>/.devbench/orchestrator.pid`, prints the instance id, and
  frees the operator's terminal.  Foreground `devbench start` is
  unchanged (still blocks).  Both modes write the PID file so the new
  lifecycle commands can target them by short instance id
  (`<workspace_name>-<pid-suffix>`) or raw PID:
  - `devbench instances [--json]` enumerates every live orchestrator on
    this host by walking `**/.devbench/orchestrator.pid` under operator-
    reachable roots (override via `DEVBENCH_INSTANCE_SEARCH_ROOTS`).
  - `devbench stop <id> [--timeout N] [--force]` sends SIGTERM, waits up
    to 30s by default, escalates to SIGKILL only with `--force`.
  - `devbench tail <id> [--follow|-f] [--lines|-n N]` resolves the
    instance's workspace via its PID file and tails
    `logs/orchestrator.log` (defaults: last 50 lines, no follow).
  - `devbench restart <id>` is a composite stop + start in the same mode
    (daemon vs foreground) and same session.

  New module `src/devbench/instances.py` owns PID-file IO, instance-id
  generation, liveness check (`os.kill(pid, 0)`), discovery, and the
  instance-id / PID resolver.  The PID file is removed on clean exit
  via the existing `try/finally` cleanup in `cmd_start`; stale entries
  (process dead) are filtered out at discovery time.  Pinned by 23
  cases in `tests/test_instances.py` (instance-id format, PID-file
  round-trip, corrupt-payload guards, liveness check, discovery walk,
  env-root override, resolver by id and PID, cleanup).

- **Per-class Slack toggles for every blocked classification + Backlog
  field on every payload** (issue #209). The previous notification
  surface fired only on transition into `OPERATOR_ACTION_REQUIRED`;
  the other six blocked classes (RUNTIME_DEGRADATION, HELD,
  BLOCKED_ON_HELD, AUTO_CLEARING_VIA_PROPOSAL, AWAITING_DEPENDENCY,
  AWAITING_AMENDMENT_RECOVERY) were silent. The
  `notifications.events` block now carries six new toggles --
  `work_unit_blocked_runtime_degradation`, `work_unit_blocked_held`,
  `work_unit_blocked_on_held`, `work_unit_blocked_auto_clearing`,
  `work_unit_blocked_awaiting_dependency`,
  `work_unit_blocked_amendment_recovery` -- each defaulting to
  `false`. Pings fire on transition INTO the matching class (initial
  entry or reclassification from a different bucket), idempotent per
  `(task × class)`. The cache is pruned on `cmd_sync_blocked` /
  `cmd_reconcile_cascade` for tasks that exit `blocked`, so re-entry
  into the same class produces a fresh ping rather than being
  suppressed by a stale entry. Every Slack payload also carries a new
  `Backlog` field naming the source workspace (operator request
  2026-05-19), so operators monitoring multiple workspaces can tell
  at a glance which backlog a ping refers to. Implementation lives in
  `src/devbench/notifications.py` (new `_EVENT_BY_CLASSIFICATION` +
  `_NOTIFY_FN_BY_CLASSIFICATION` mappings, six new `notify_*` helpers,
  generalised `notify_blocked_classification_transition`, new
  `prune_notification_state_for_unblocked`); pinned by 16 cases in
  `tests/test_notifications_transition.py` (parametrised across all
  seven classes) plus six new payload-shape tests in
  `tests/test_notifications.py`. Replaces the operator-only
  `notify_blocked_operator_transition` from #207 per CLAUDE.md
  "Complete Replacement of Superseded Code".

- **Operator-facing Slack notifications** (PR #202) — toggleable per-event
  Slack pings on every interesting lifecycle event: work-unit done,
  work-unit blocked-and-operator-action-required, work-unit materialised
  / promoted, PR opened / merged, CI failure, orchestrator stop (clean,
  drain, SIGTERM, or crash — always-fire on exit), orchestrator
  auto-restart. New `notifications:` yaml
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

### Added

- **`notifications.events.ci_pass` event toggle** (issue #219; Bundle C).
  When CI on the auto-finalize batch PR turns GREEN, `cmd_git_ops_finalize`
  -> `_handle_finalize_ci_result` now fires a `notify_ci_pass` Slack ping
  giving the operator an explicit "PR is ready for manual merge" signal
  -- previously the GREEN branch only wrote a `[CI_GREEN]` audit log line
  and was silent on Slack, leaving operators running `auto_merge: false`
  with no way to know CI passed except polling GitHub.  Default `false`
  so existing workspaces stay silent on upgrade.  New
  `notify_ci_pass(unit_id, repo, pr_url)` helper in
  `src/devbench/notifications.py` + `EVENT_CI_PASS` constant + field on
  `NotificationsEventsConfig` + JSON-schema property.  Configure-devbench
  skill template (`plugin/devbench/skills/configure-devbench/SKILL.md`)
  documents the new toggle; sample-config (`docs/devbench-yaml-reference.md`)
  shows the default-false rendering.  Pinned by
  `tests/test_notifications.py::TestSendTestNotification::test_every_event_has_a_sample`,
  `::TestSlackPayloads::test_ci_pass`,
  `::test_ci_pass_event_toggle_default_false_and_parse`, and
  `tests/test_cli.py::TestCmdGitOpsFinalizeNotifications::test_notify_ci_pass_fires_on_ci_green`.

### Fixed

- **Orchestrator burns ~$0.07/turn after orchestrate skill returns
  ALL_DONE / NO_ACTIONABLE because SDK loop never breaks** (issue #218).
  After the orchestrate skill prints one of its three terminal sentinels
  (`ALL_DONE` / `NO_ACTIONABLE` / `NO_ACTIONABLE_IN_SCOPE`) the Claude
  Agent SDK's `async for message in query(...)` iterator in `_run`
  (`cli.py:6562-6577`) keeps emitting `ResultMessage` events every ~5 s
  -- each a paid model turn carrying the same end-of-run summary --
  because the iterator has no internal natural-exit condition and only
  unwinds on quota errors, drain-on-claim, or subprocess EOF.  The
  kanon-deps-work run on 2026-05-20 burned **\$8.30 in 9.5 minutes**
  ($125.60 -> $133.90 cumulative `total_cost_usd`) before the operator
  killed the daemon; extrapolates to ~\$50/hr idle re-invocation.  New
  helper `_is_terminal_orchestrate_result(text)` matches the three
  sentinels; `_log_terminal_exit_if_applicable` writes an
  `[ORCHESTRATOR_TERMINAL_EXIT] reason=<text>` audit row and returns
  True, letting `_run`'s loop break early.  The existing #217 plumbing
  carries the captured result text into the `orchestrator_stop` Slack
  ping, so the operator sees the actual exit reason within seconds of
  the terminal marker.  Pinned by
  `tests/test_cli.py::TestIsTerminalOrchestrateResult` (7 cases) and
  `::TestCmdStartTerminalExit` (5 cases).

- **`cmd_git_ops_finalize` never fires `pr_opened` / `ci_failure`
  Slack pings; auto-finalize PR + CI lifecycle silent on Slack**
  (issue #219).  Operators running `git_ops.defer_pr: true` +
  `auto_finalize: true` (the recommended single-PR mode) got zero
  Slack pings about their batch PR's lifecycle: PR creation,
  CI failures, and CI green-readiness were all silent because
  the per-WU `cmd_git_ops` notification calls were never mirrored
  into the batch-PR code paths.  `cmd_git_ops_finalize` now calls
  `notify_pr_opened` after `ops.create_pr` returns;
  `_handle_finalize_ci_result` accepts a new `repo` kwarg and calls
  `notify_ci_failure` on FAILED_KNOWN_TASK + FAILED_UNKNOWN branches
  (attempt sentinel `1` -- the finalize path has no retry counter
  today).  When the finalize path has no in-flight WU to attribute
  the ping to (degenerate case), a symbolic `"finalize"` sentinel is
  used as the rep `unit_id`.  `pr_merged` deliberately not added --
  under `auto_merge: false` the operator merges by hand and GitHub
  does not signal devbench back; a follow-up issue should add an
  out-of-band poller if needed.  Pinned by
  `tests/test_cli.py::TestCmdGitOpsFinalizeNotifications` (5 cases
  covering pr_opened on create_pr, ci_failure on FAILED_KNOWN_TASK +
  FAILED_UNKNOWN, no-fire on TIMEOUT, and the Bundle C ci_pass
  case).

- **`orchestrator_stop` Slack ping says bare `"clean"` instead of the real
  exit reason** (issue #217).  When `cmd_start`'s SDK loop completes
  normally (e.g., the orchestrate skill returns `NO_ACTIONABLE -- 190/212
  done, 11 blocked` because of a deadlocked cascade), `_stop_reason`
  stayed at its initial value `"clean"`.  The Slack ping then read the
  same regardless of whether the backlog was complete (`ALL_DONE`) or
  blocked with work remaining -- the operator could not tell the
  difference at a glance.  `_run` now captures the latest
  `ResultMessage.result` text via a `nonlocal` closure variable;
  `cmd_start` promotes it to `_stop_reason = f"clean exit: {text}"`
  before firing the notification, so the operator sees the actual
  summary (status counts, NO_ACTIONABLE vs ALL_DONE, etc.) in the
  Slack ping.  When the SDK emits no `ResultMessage` (degenerate test
  scenario), the legacy `"clean"` reason is preserved -- behaviour is
  a strict superset of the pre-fix path.  Pinned by
  `tests/test_cli.py::TestCmdStartSlackPingResultText::test_slack_ping_includes_sdk_result_text_on_clean_exit`
  and `::test_slack_ping_falls_back_to_clean_when_no_result_message`.

- **Multi-column status table mis-aligned when one cell is much wider than
  others** (issue #214).  `_render_multi_column_table` /
  `_render_grouped_progress_table` shared a single `value_w` across every
  value column, derived from `max_cell` over every cell.  A single wide
  cell (e.g., the ETA breakdown All-time value with
  `+ blocked-runtime-degradation N` appended, ~107 chars vs Session's
  ~74) inflated all columns to that width, producing a table well past
  any reasonable terminal width.  Both renderers now compute per-column
  widths AND cap each column at `MAX_VALUE_COL_WIDTH` (50 chars); cells
  exceeding the cap wrap onto multiple physical lines via
  `_wrap_cell_value`, which prefers `` + `` boundaries first, falls back
  to whitespace word-wrap, and never breaks a single word
  mid-character (the long-word floor `_longest_word_len` keeps the column
  wide enough to fit unbreakable identifiers like
  `blocked-runtime-degradation`).  Spanning rows wrap to the joined-span
  width on the same rules.  Pinned by
  `tests/test_reporting/test_report.py::TestSpanningRows::test_multi_column_table_caps_columns_and_wraps_long_cells`,
  `::test_grouped_progress_table_caps_columns_and_wraps_long_cells`,
  `::test_wrap_cell_value_splits_on_plus_boundaries`, and
  `::test_wrap_cell_value_never_breaks_a_word`.

- **`RUNTIME_DEGRADATION` classification persists after orchestrator
  restart** (issue #215).  `_has_runtime_degradation_signal` scanned the
  work-unit's Comments section for `agent-tool-unavailable` audit rows
  within a 24h window.  Audit rows are append-only, so the same row kept
  triggering the classification for 24h regardless of how many times the
  operator restarted -- defeating both the renderer's "task remains
  blocked until the orchestrator restarts" hint and the auto-restart
  loop in `_should_auto_restart_after_no_actionable` (which then looped
  forever).  `cmd_start` now writes
  `<workspace>/.devbench/last-restart` (new constant
  `LAST_RESTART_MARKER_PATH`) on every startup; the classifier reads the
  marker via `_read_last_restart_marker` and filters out audit rows
  older than it before applying the 24h window.  With no marker
  (cold-boot / never-restarted workspace), behaviour is unchanged.
  Pinned by three new cases in
  `tests/test_backlog/test_proposal.py::TestClassifyBlockedTaskRuntimeDegradation`
  and `tests/test_cli.py::TestCmdStartWritesRestartMarker`.

- **`drain.signal` not cleared after orchestrator exit** (issue #212).
  When an operator ran `devbench drain` from a shell, the signal landed at
  `<workspace>/.devbench/drain.signal` (no `DEVBENCH_SESSION_NAME` in env),
  but `cmd_start` set `DEVBENCH_SESSION_NAME = parsed.name` (default
  `"default"`) before its drain loop ran and therefore looked at
  `<workspace>/.devbench/sessions/default/drain.signal` -- a different path.
  `consume_drain` never saw the operator's signal, the file persisted on
  disk after the orchestrator exited, and the next `devbench start` would
  read it via the same divergent path mismatch (or, with the per-session
  reader, would auto-drain on the next claim).  Operators had to run
  `devbench drain --cancel` manually after every drained shutdown.

  `drain.py::read_drain_state`, `consume_drain`, and `cancel_drain` now
  scan both candidate paths -- the per-session path (priority) and the
  workspace-root path (fallback).  The session-scoped reader observes an
  operator's workspace-root drain.signal; the unlink targets the path
  that actually held the signal.  `cmd_start`'s SDK-run `finally` clause
  additionally calls `cancel_drain(WORKSPACE_ROOT)` while
  `DEVBENCH_SESSION_NAME` is still set, so on every exit (clean,
  drain-enforced, crash) the signal is wiped from both paths -- the next
  start does not inherit a stale request.  Pinned by new cases in
  `tests/test_drain.py::TestPerSessionDrainHelpers` (cross-path
  read / consume / cancel) and
  `tests/test_cli.py::TestCmdStartCancelDrainOnExit` (finally-clause
  cleanup for both paths).

- **Clean orchestrator exits mis-labeled as crashes in `orchestrator_stop`
  Slack pings** (issue #213).  `cmd_start`'s outer
  `except BaseException as exc: _stop_reason = f"crash: {type(exc).__name__}: {exc}"`
  caught `SystemExit(0)` (raised by `sys.exit(0)` on NO_ACTIONABLE /
  ALL_DONE / SIGTERM-after-drain) and `KeyboardInterrupt` (operator Ctrl+C)
  and fired the Slack ping with text like `crash: SystemExit: 0`, mixing
  up "exited cleanly because there was nothing left to do" with "crashed
  unexpectedly".

  New helper `_label_stop_reason(exc)` buckets the reason: `SystemExit`
  with code `None` or `0` -> `"clean exit (SystemExit 0)"`;
  `KeyboardInterrupt` -> `"interrupted by operator (Ctrl+C / SIGINT)"`;
  anything else (including `SystemExit` with non-zero code) ->
  `"crash: <type>: <msg>"`.  Pinned by six cases in
  `tests/test_cli.py::TestLabelStopReason`.

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

- **Amendment-rejection no longer mis-fires the `work_unit_blocked_operator`
  Slack ping** (issue #210). `reject_amendment` in
  `src/devbench/backlog/amendment.py` previously called `mark_blocked`
  BEFORE writing the rejected-requests archive. `mark_blocked` runs
  `classify_blocked_task` inline; the classifier's
  `AWAITING_AMENDMENT_RECOVERY` signal is the presence of the archive
  on disk, so the classifier saw no recovery signal and fell through to
  `OPERATOR_ACTION_REQUIRED`. Pings fired against the wrong per-class
  toggle. The fix is a one-line reorder: write the archive +
  rejection-feedback JSON first, then `mark_blocked`. Pinned by
  `tests/test_backlog/test_amendment.py::TestRejectAmendment::test_rejected_archive_exists_before_mark_blocked_runs`,
  which monkey-patches `_set_status` and asserts the archive is on disk
  at the moment the blocked-write fires.

- **`work_unit_blocked_operator` Slack notification now fires on every
  transition into `OPERATOR_ACTION_REQUIRED`** (issue #207). Before this
  fix the ping was one-shot at `mark_blocked` time only; a task that
  entered `blocked` with classification `AWAITING_DEPENDENCY` (silent,
  correct) and later drifted into `OPERATOR_ACTION_REQUIRED` -- because
  a dep landed but the cascade missed it (#208) or a `[BLOCKED]` audit
  went stale -- left the operator silently uninformed.  A new
  per-workspace classification cache at
  `<workspace>/.devbench/notification-state.json` tracks each task's
  last-observed classification; the new
  `notify_blocked_operator_transition` helper in
  `src/devbench/notifications.py` fires exactly once per transition into
  the `OPERATOR_ACTION_REQUIRED` bucket and is wired into `mark_blocked`,
  `cmd_sync_blocked`, and `cmd_reconcile_cascade`.  Read-only renderers
  (`devbench status`, `devbench report`) still classify on every refresh
  but do NOT route through the notifier so they cannot duplicate pings.
  Cache failures (missing dir, corrupt JSON, non-object payload) are
  best-effort -- treated as empty cache, regenerated on next write,
  logged as `[WARN]` without aborting the orchestrator.  Pinned by
  `tests/test_notifications_transition.py` (9 cases: first-time fire,
  idempotent re-call, transition into / out of bucket, event-disabled
  short-circuit, corrupt-cache + non-dict-payload recovery, multi-task
  independent tracking).

- **Auto-requeue cascade now covers regular task-level dependencies, not
  only `promoted_proposals`** (issue #208, follow-up to #147). Before
  this fix, `mark_done(B)` fired the cascade only for tasks linked via
  `[BLOCKED_PENDING_PROPOSAL]` markers; tasks whose `Dependencies` table
  named B but carried no marker stayed in `blocked` indefinitely.  The
  new `BacklogManager._auto_requeue_regular_dep_dependents` helper in
  `src/devbench/backlog/manager.py` is called from `_set_status`
  alongside the existing marker cascade and unblocks blocked tasks whose
  regular `Dependencies` table entries are now all terminal AND that
  carry no `[BLOCKED_PENDING_PROPOSAL]` marker (the marker cascade owns
  those).  Each flip writes a `[UNBLOCKED] [CASCADE_RESOLVED] dependency
  '<dep_id>' now terminal` audit comment matching the supersession shape
  used by `cmd_sync_blocked` and the marker cascade (#153). Pinned by
  `tests/test_backlog/test_manager.py::TestAutoRequeueRegularDepDependents`
  (6 cases including mark_done-driven end-to-end integration).

- **`scope.json` non-object top-level payload now fails fast with an
  actionable error** (issue #205). Before this fix a top-level JSON
  list (`[]`, `[1, 2]`), bare string, integer, `null`, or `true` reached
  the per-field shape check at `src/devbench/scope.py:444-450` via
  `data[field_name]`, which raised the raw Python `TypeError: list
  indices must be integers or slices, not str` -- the operator saw the
  implementation detail and not the recovery step.  A new top-level
  `isinstance(data, dict)` guard rejects any non-object payload with a
  message naming the file path and the fix step (`remove and re-run
  'devbench start --include ...' to recreate`).  Mirrored in
  `_read_scope_banner_data` (`src/devbench/cli.py`) which previously
  silently coerced `[]` into `{}` via `dict(json.loads(...))`. Pinned
  by `tests/test_scope.py::test_from_file_non_object_top_level_raises`
  (6 parametrised shape cases).

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
6. **Issue #259 (task-factory on by default, ADR-32)**: if your backlog
   omits `task_factory` from `backlog/config/devbench.yaml`, the loop now
   runs and materialises draft work-unit `.md` files after amendment
   rejects, where before it produced none. A freshly materialised
   draft's initial status is, and has always been, governed by
   `backlog.default_status_for_new_work_units` (default `in-queue`,
   AC-189-8) -- unaffected by this change -- so drafts remain immediately
   actionable unless you also set that key to `draft` for an explicit
   human-review gate. Relative to released tag `0.1.0`, TWO defaults
   changed: `task_factory.enabled` flipped `false` -> `true`, and
   `task_factory.auto_accept_proposals` flipped `true` -> `false`. To
   restore the pre-flip `enabled` behavior (loop off unless explicitly
   turned on), add `task_factory.enabled: false`. `auto_accept_proposals`
   going `true` -> `false` has two concrete effects an upgrading operator
   will notice: (a) `write-proposal` no longer synchronously
   materialises-and-promotes the proposal it just wrote inside the same
   invocation (`auto_cascade` in its output JSON now reads `"disabled"`),
   so a freshly written proposal now waits for the next `sweep-proposals`
   tick instead of being actionable immediately; and (b)
   `sweep-proposals` no longer auto-promotes a draft explicitly left at
   `## Status: proposed` (a legacy/hand-edited-draft case, not something
   the normal materialise path produces). To restore the
   `0.1.0`-released `auto_accept_proposals: true` behavior (both effects
   together), add `task_factory.auto_accept_proposals: true` (alongside
   `enabled: true`, its default) -- **warning:** this reintroduces the
   orphan-auto-promote path that `0.1.0` shipped and that D-11
   deliberately turns off by default; it was never a general
   auto-promote-everything behavior even at `0.1.0`, since freshly
   materialised drafts have always bypassed `proposed` status entirely.
   Only set it if you have already confirmed that skipping review of
   orphaned `proposed` drafts is acceptable. See
   `docs/adr/32-task-factory-default-on.md` for the full decision record
   and the `manifest_amendment`-interaction contract.

7. **Review topology changed (ADR-33).** The orchestrate skill now
   dispatches the four `review_team` judges directly as first-level
   sub-agents; `review-supervisor` is retained and is still invoked, but
   only afterward, as a non-spawning aggregator that reads the four
   judges' already-persisted verdicts and reports a consolidated result --
   it never dispatches them itself. No action is required for standard
   workspaces -- the `agents.review_supervisor` config key still parses
   and still selects the model used for that aggregation pass. If you
   maintain a **custom orchestrate skill or a forked plugin** that invokes
   `review-supervisor` expecting it to run the review fan-out itself, that
   path is now blocked by `guard-review-supervisor-scope.sh` (exit 2) and
   must be updated to dispatch the judges directly, letting
   `review-supervisor` aggregate afterward. A missing verdict from any
   required judge is now a hard review failure rather than an implicit
   pass, so a work unit that previously slipped through on a partial round
   will now fail review until every judge reports.
8. **Optional: isolate stop-hook state.** The Stop hook's state file
   defaults to `/tmp`, which is shared machine-wide. If you run the test
   suite on a host that also runs a live orchestrator, set
   `DEVBENCH_STOP_HOOK_STATE_DIR` to a private directory in the test
   environment. Leaving it unset preserves the previous `/tmp` paths
   exactly.

### Known follow-ups (this branch / next release)

- Implement `git_ops.pause_before_merge: true` runtime path (#101).
  Schema + validation already in place; runtime branch in
  `cmd_git_ops` + `cmd_check_merge` ships next.
- Live integration smoke covering all four exit codes (0/1/2/3) +
  inline cleanup chore-commit shape on a fresh tmp workspace.
