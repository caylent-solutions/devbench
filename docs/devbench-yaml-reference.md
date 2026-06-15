# devbench.yaml Reference

This document is the canonical reference for every field in `backlog/config/devbench.yaml`.

The YAML file is loaded from `<DEVBENCH_WORKSPACE_ROOT>/backlog/config/devbench.yaml` by default.
Override the lookup path with `--config <path>` (CLI flag) or the `DEVBENCH_CONFIG_PATH` environment
variable.

**Source of truth:** `src/devbench/config_loader.py` (module docstring + dataclass docstrings) and
`sample-config.yaml` (annotated with defaults). The JSON schema at `src/devbench/config-schema.json`
enforces unknown-key rejection at load time.

---

## Value resolution precedence

For every configurable parameter:

1. Environment variable override (applied by `src/devbench/config.py`, not by this module).
2. Value in `devbench.yaml` (applied by `config_loader.py`).
3. Code default in the relevant dataclass field.

---

## Top-level fields

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `merge_strategy` | `squash` \| `merge` \| `rebase` | `squash` | Default merge strategy for every repo unless overridden per-repo under `repos:`. Effective precedence at merge time: `DEVBENCH_MERGE_STRATEGY` env > per-repo > this top-level > `squash`. |
| `max_executor_retries` | integer | `10` | Shared retry budget across review-judge retries, CI-failure retries, and PR-bot-feedback retries. |
| `use_bedrock` | boolean | `false` | Route LLM calls via AWS Bedrock instead of the Anthropic API. |
| `bedrock_region` | string | `us-east-1` | AWS region for Bedrock when `use_bedrock: true`. |
| `allowed_orgs` | list of strings | `[]` | Hard allowlist of GitHub orgs devbench may operate against. Empty means every org listed under `repos:` is permitted. |
| `display_timezone` | IANA zone string | OS local | Timezone applied to every timestamp-rendering command (`report`, `hook-tail`, `watch`). |
| `log_file` | string (relative path) | `logs/orchestrator.log` | Shared aggregate orchestrator log. Named sessions additionally write a per-session log at `.devbench/sessions/<name>/orchestrator.log` (read via `report --session <name>`). An explicit value (or `DEVBENCH_LOG_FILE`) overrides; relative values are workspace-relative. |

---

## `repos:` (required)

At least one entry is required. Each key is either a two-segment `org/repo` identifier
(e.g. `caylent-solutions/devbench`) or a bare single-segment name (e.g. `workspace-local`)
for repos that have no GitHub remote. Three-segment keys (e.g. `org/repo/extra`) and
empty-segment keys (e.g. `org/`) are rejected at load time.

```yaml
repos:
  caylent-solutions/devbench:
    default_branch: main          # optional -- omit to fall back to origin/HEAD
    checkout_directory: devbench  # optional -- relative to DEVBENCH_WORKSPACE_ROOT
    merge_strategy: squash        # optional -- overrides top-level merge_strategy
    local_only: false             # optional -- see local_only below
```

### `repos.<key>.local_only`

| Property | Value |
|----------|-------|
| **Type** | boolean |
| **Default** | `false` |

When `true`, this repo has no `origin` remote and is never pushed:

- `ensure_branch` creates the work-unit branch off the local default branch without
  running `git fetch origin`.
- `git-ops-finalize` is a no-op and returns 0 immediately (logs
  `[AUTO_FINALIZE_SKIPPED] local_only=true`).

**Effective-value precedence.** The per-repo `local_only` key wins when explicitly set
(`true` or `false`); otherwise the repo inherits the top-level `git_ops.local_only` value.

**At-most-one constraint.** At most one repo may have an effective `local_only == true`
across the entire `repos:` map. Violating this constraint raises:

```
ValueError: Config file '...': at most one local_only repo is allowed; found: <ids>
```

**Requires `default_branch`.** Every repo whose effective `local_only` is `true` must
declare an explicit `default_branch`; there is no `origin/HEAD` to fall back to.
Omitting it raises:

```
ValueError: Config file '...': local_only repos require an explicit default_branch:.
Missing on: <repo-ids>. There is no origin to fall back to in local-only mode.
```

**Example -- workspace-local repo without a GitHub remote:**

```yaml
repos:
  workspace-local:
    default_branch: main
    local_only: true
git_ops:
  defer_pr: true
  single_branch: feat/work
```

---

## `backlog:` -- backlog lifecycle settings (issue #189)

All fields are optional. Omitting the entire `backlog:` section produces identical behaviour to
existing workspaces (backwards compatible -- AC-189-9).

```yaml
backlog:
  default_status_for_new_work_units: in-queue  # 'draft' or 'in-queue'
```

### `backlog.default_status_for_new_work_units`

| Property | Value |
|----------|-------|
| **Type** | string |
| **Accepted values** | `draft`, `in-queue` |
| **Default** | `in-queue` |
| **Invalid value behaviour** | `ValueError` raised at load time with an actionable message naming the accepted values |

**What it controls.** The `## Status:` line written into every newly created work-unit file by
`task_factory.materialise_proposal` and the blocker-resolver promote path. It does not retroactively
change the status of existing work units.

**`in-queue` (default).** New work units are immediately eligible for autonomous claim on the
next orchestrator sweep. This is the legacy behaviour; existing workspaces that do not set this
key see no change (AC-189-9).

**`draft`.** New work units are created in `draft` status -- a pre-`in-queue` gate. The
orchestrator never claims a `draft` task; `get_parallel_candidates` excludes them. An operator
must explicitly promote the task to `in-queue` before autonomous execution can begin. Use
`devbench promote <id>` for individual tasks or `--epic`, `--feature`, `--story`, `--all` for
bulk promotion. Each promoted unit receives a `[PROMOTED] draft -> in-queue` audit comment (AC-189-4).

**Error example.** Setting an invalid value raises a `ValueError` at process start:

```
ValueError: Config file 'backlog/config/devbench.yaml':
  backlog.default_status_for_new_work_units must be one of [draft, in-queue]; got 'staging'.
  Use 'draft' to require explicit promotion before execution,
  or 'in-queue' (the default) for the legacy behaviour.
```

**Example -- require human review of every generated task:**

```yaml
backlog:
  default_status_for_new_work_units: draft
```

After the orchestrator runs `task-factory` and materialises new tasks, the operator reviews
each task file, then runs:

```bash
# Promote a single task
uv run devbench promote E5-F2-S1-T3

# Promote everything under an epic in one transaction
uv run devbench promote --epic E5

# Promote all draft tasks (with confirmation prompt)
uv run devbench promote --all

# Promote all draft tasks without prompting (CI / scripted use)
uv run devbench promote --all --yes
```

---

## `timeouts:` -- all values in seconds

```yaml
timeouts:
  gh_api: 30
  test: 300
  security_fetch: 120
  llm: 300
  command: 120
  orchestrator_poll_interval: 10
  github_check: 600
```

Environment variable overrides are applied by `config.py` (not this module).

---

## `limits:` -- threshold values

```yaml
limits:
  alert_summary: 10
  output_truncation: 2000
  llm_evidence_truncation: 15000
  llm_file_context: 5
  llm_file_preview_chars: 3000
  ci_failure_log_bytes: 32768
```

---

## `git_ops:` -- git workflow settings

```yaml
git_ops:
  update_submodule: false       # set true only for git-submodule repos
  # single_branch: feat/batch  # one branch for all WUs (single-PR mode)
  defer_pr: false               # requires single_branch; commits stay local until git-ops-finalize
  pause_before_merge: false     # push + wait for CI, then transition to in-review
  inline_orphan_cleanup: true   # chore commit before task commit when orphans detected
  ci_failure_retry: true        # rc=2 on CI failure triggers executor retry with log feedback
  local_only: false             # top-level fallback; per-repo repos.<key>.local_only takes precedence
  auto_finalize: false          # auto-run git-ops-finalize when all WUs terminal (incompatible with local_only)
  auto_merge: false             # auto-merge after CI green (requires auto_finalize + defer_pr)
  orphan_patterns: []           # replaces built-in orphan fnmatch list when non-empty
  pr_review_resolution:
    enabled: false
    agents: []
    decision_blocks: true
    settle_seconds: 60
    poll_interval: 5
```

### `git_ops.local_only`

Top-level fallback for all repos in the `repos:` map. When `true`, every repo that does
not explicitly set its own `local_only` key inherits this value. Per-repo
`repos.<key>.local_only` takes precedence when explicitly set (overrides in both directions).

See `repos.<key>.local_only` above for the full semantics, constraints, and examples.

---

## `stop_hook:` -- circuit breaker tuning

```yaml
stop_hook:
  max_blocks: 5
  window_seconds: 180
  stale_task_minutes: 120
```

---

## `hook_tail:` -- column caps for `devbench hook-tail`

```yaml
hook_tail:
  agent_width: 12
  tool_width: 8
  description_max: 120
  stdout_preview_max: 80
```

---

## `orchestrate:` -- orchestrator runtime tuning

```yaml
orchestrate:
  max_cascade_depth: 2                 # recovery-of-recovery cascade depth cap
  model: claude-opus-4-8               # REQUIRED to launch the orchestrator; see below
  within_claim_convergence_check: true # block a claim that repeats the SAME failure
  max_within_claim_attempts: 4         # identical-failure recurrences before a block
  max_claim_wall_clock_seconds: 21600  # 6h backstop; 0 disables
  max_non_converging_claims: 3         # aggregate block-and-continue safety valve
```

**`model`** is the model the top-level orchestrate SDK session runs on when devbench launches it non-interactively (`devbench start` / `--daemon`). devbench passes it into `ClaudeAgentOptions(model=...)`, so the session is **pinned** to this value and can never inherit the interactive Claude Code (`~/.claude/settings.json`) model. It is **required** for `devbench start` and has **no fallback** (not to `DEVBENCH_CLAUDE_MODEL`, not to the CLI settings) -- the orchestrator-launch path fails fast with an actionable error when it is unset. Short name (`opus` | `sonnet`) or a full Anthropic id when `use_bedrock: false`; a Bedrock ARN when `true`. Haiku is rejected (#198).

Interactive vs non-interactive: this key governs ONLY the SDK-launched orchestrator. When an operator runs the `/devbench-orchestrate:orchestrate` slash command inside their own interactive Claude Code session, the skill runs on the **host session's selected model** -- devbench cannot and does not override the host session. (The work agents -- executor / judges / etc. -- get their model from each agent's plugin `.md` frontmatter, overridable via the `agents:` block.)

### Within-claim convergence bound + block-and-continue

A single in-progress claim that repeats the SAME unresolvable AC-verify / TDD-RED / live-test signature -- while staying "busy" so the inactivity budget keeps resetting -- is force-**BLOCKED** with a `[CLAIM_NOT_CONVERGING]` audit comment rather than churning for hours. The bound keys on the REPEATED IDENTICAL signature (never raw duration), so a genuinely-progressing long live run (a different signal each round) is never killed.

| Key | Env override | Default | Meaning |
|---|---|---|---|
| `within_claim_convergence_check` | `DEVBENCH_ORCHESTRATOR_WITHIN_CLAIM_CONVERGENCE_CHECK` | `true` | Master toggle for the bound. |
| `max_within_claim_attempts` | `DEVBENCH_ORCHESTRATOR_MAX_WITHIN_CLAIM_ATTEMPTS` | `4` | How many times the SAME failing signature may recur within one claim before it is blocked (>= 1). |
| `max_claim_wall_clock_seconds` | `DEVBENCH_ORCHESTRATOR_MAX_CLAIM_WALL_CLOCK_SECONDS` | `21600` | Secondary wall-clock backstop in seconds (>= 0; `0` disables it, the signature bound still applies). |
| `max_non_converging_claims` | `DEVBENCH_ORCHESTRATOR_MAX_NON_CONVERGING_CLAIMS` | `3` | Aggregate block-and-continue safety valve K (>= 1). See below. |

**Block-and-continue.** A non-converging claim is BLOCKED and the orchestrate session **continues to its next in-queue unit in scope** -- one bad module no longer abandons the rest of a session's scope, and a multi-session sweep no longer loses a whole session to its first defective module. The session keeps accumulating both completions and blocks in one pass and only stops when there is genuinely nothing actionable left (the normal `NO_ACTIONABLE` / `ALL_DONE` termination).

`max_non_converging_claims` is the aggregate safety valve so a systemically-broken run still halts for the operator: the session stops once **K distinct units** have each hit the convergence bound in the same session, emitting `[ORCHESTRATOR_STOP_REASON] reason=too many non-converging claims (K)`. (Resolution order for all four keys is env > YAML > the `constants.py` default.)

---

## `report:` -- cost estimation settings

```yaml
report:
  token_cost_per_million_input: 5.0
  token_cost_per_million_output: 25.0
  token_cost_discount: 0.0
  cache_read_multiplier: 0.10
  cache_write_5min_multiplier: 1.25
  cache_write_1hr_multiplier: 2.0
  data_residency_multiplier: 1.10
  fast_mode_multiplier: 6.0
  recent_pace_tasks: 10
  # display_timezone: America/New_York
```

See [docs/model-pricing.md](model-pricing.md) for per-model pricing blocks and the cost formula.

---

## `manifest_amendment:` -- amendment workflow (on by default)

```yaml
manifest_amendment:
  enabled: true                    # default; set false to opt out
  allowed_reasons:
    - tdd_green_production_fix
  max_requests_per_execution: 1
  # default true -- accept reason=verification_directive_defect so the pipeline can
  # repair an objectively-defective '## Verification' directive (stale assertion
  # superseded by a DONE unit, syntactic bug, or landed rename). Judge-gated via the
  # manifest-amender's dedicated rubric; deterministic guards forbid weakening (same
  # AC ids, same type=, same expect-exit; cited units must be done). Set false to
  # require an operator edit for every verification-directive fix.
  allow_verification_directive_amendments: true
```

---

## `task_factory:` -- task-factory loop

```yaml
task_factory:
  enabled: true                    # default true; requires manifest_amendment.enabled: true; set false to disable
  auto_accept_proposals: true      # default; only applies when enabled: true
```

---

## `validate:` -- validate-backlog rule toggles

```yaml
validate:
  check_orphan_path_tokens: true   # Rule 20; default on, set false to opt out
```

---

## `agents:` -- per-agent model overrides (ADR-25)

```yaml
agents:
  executor: sonnet
  blocker_resolver: opus
  manifest_amender: opus
  security_reviewer: opus
  task_factory: opus
  review_supervisor: sonnet
  iac_deploy_reviewer: opus     # optional iac_review judge; env: JUDGE_AGENT_MODEL_IAC_DEPLOY_REVIEWER
  review_team:
    code_reviewer: opus
    test_reviewer: opus
    doc_reviewer: opus
    changes_manifest: opus
```

All fields default to `null` (agent's `.md` frontmatter default). Every agent
`.md` file is materialised as a real file in the shadow tree so the Claude
Agent SDK registers it; non-agent files are symlinked. See
[docs/adr/25-per-agent-model-overrides.md](adr/25-per-agent-model-overrides.md).

---

## `notifications:` -- operator-facing Slack / webhook pings

Per-event toggles for lifecycle notifications. Each toggle defaults to
`false` so the dispatcher is silent until the operator opts in. The
Slack webhook URL + user ID are credentials and should be provided via
the `DEVBENCH_NOTIFICATIONS_SLACK_WEBHOOK_URL` and
`DEVBENCH_NOTIFICATIONS_SLACK_USER_ID` env vars; the yaml fields below
are a fallback for non-secret cases. See
[`docs/slack-notifications.md`](slack-notifications.md) for the full
operator walkthrough.

```yaml
notifications:
  enabled: true                         # master switch; default false
  slack:
    webhook_url: null                   # https://hooks.slack.com/services/T.../B.../...
    user_id: null                       # Slack member id (U... or W...); enables <@mention>
  webhook_url: null                     # optional non-Slack generic webhook (raw JSON POST)
  timeout_seconds: 10                   # per-POST HTTP timeout
  events:
    work_unit_done: false
    work_unit_blocked_operator: false
    work_unit_materialised: false
    work_unit_promoted: false
    pr_opened: false
    pr_merged: false
    ci_failure: false
    ci_pass: false                       # issue #219 / Bundle C; fires on CIResult.GREEN
                                         # in the finalize path so operators under
                                         # auto_merge: false know the PR is ready for
                                         # manual merge.  Default false on upgrade.
    orchestrator_stop: false
    orchestrator_auto_restart: false
    quota_waiting: false                 # quota hit; orchestrator started waiting
                                         # for the reset (payload: quota source/reason
                                         # + provider-stated reset time).  Default false.
    quota_resumed: false                 # quota recovered and the run resumed
                                         # (payload: total seconds waited).  Default false.
```

---

## `max_executor_retries_per_judge:` -- per-judge retry budget

```yaml
max_executor_retries_per_judge:
  code_review: 10
  test_review: 10
  doc_review: 10
  changes_manifest: 10
  security_review: 10
```

Each entry falls back to `max_executor_retries` when absent.

---

## `debug:` -- diagnostic-tuning knobs

```yaml
debug:
  check_registration_retries: 12
  check_registration_delay_seconds: 5
  blocked_recovery_window_seconds: 1800
```

Set only when investigating a specific cadence problem; production workspaces leave this section
absent.

---

## `supervise:` -- interactive subscription-billed orchestrator (ADR-31)

Config for `devbench supervise`, which launches the orchestrator as an interactive `claude` CLI
session under a detached `screen` daemon (billed against the Claude Code subscription's 5-hour
windows, not the API). Every field has a documented default and is overridable by a
`DEVBENCH_SUPERVISE_*` env var (env > yaml > default). The quota machinery REUSES the top-level
`quota_handling:` block, so it is not duplicated here. Full operator guide: [supervise.md](supervise.md).

```yaml
supervise:
  model: null                       # default model; null -> falls back to orchestrate.model -> fail-fast (D-3)
  effort: xhigh                      # low|medium|high|xhigh|max (xhigh default; max is session-only)
  screen_name_prefix: devbench-supervise-
  timeouts:
    ready_prompt_seconds: 120        # wait for the first interactive ready prompt
    idle_seconds: 1800               # max silence before treating the session as hung
    command_ack_seconds: 60          # wait for a slash command to be acknowledged
    quota_poll_interval_seconds: 60  # reuses quota_handling.poll_interval_seconds when null
    quota_max_wait_seconds: 18000    # reuses quota_handling.max_wait_seconds when null (5h)
    graceful_stop_seconds: 900       # graceful drain budget before escalating to a hard stop
    command_invocation_seconds: 30   # safety timeout bounding short screen subprocess shell-outs
  restart:
    max_attempts: 5                  # bounded auto-restart on the exit-42 restart signal
    resume_mode: continue            # continue | resume (resume uses the captured session id)
  quota:
    max_quota_resumes: null          # null -> DEFAULT_MAX_QUOTA_RESUMES (1000) / DEVBENCH_MAX_QUOTA_RESUMES
  detection_patterns:                # version-fragility hardening -- all PTY regexes centralized here
    ready_prompt: '(?m)^\s*(>|│\s*>)\s*$'
    working_prompt: '(?i)(esc to interrupt|tokens|thinking)'
    quota_limit: "(You’ve hit your limit|You've hit your limit|rate.?limit.*(exceeded|reached|resets))"
    quota_wait_prompt: '(?i)(wait.*reset|retry.*later|press.*to wait)'   # DI-5 placeholder (unverified)
    reset_at: 'resets\s+(\d{1,2}):(\d{2})(am|pm)\s+\(UTC\)'
    circuit_breaker: '\[CIRCUIT_BREAKER\]|cascade depth exceeded'
    harness_block: '\[HARNESS_INTEGRITY\]'
    crash: '(?i)(panic|fatal error|traceback \(most recent call last\))'
  log_tail:
    orchestrator_log_relpath: logs/orchestrator.log
    markers_clean: ["ALL_DONE", "NO_ACTIONABLE", "[ORCHESTRATOR_TERMINAL_EXIT]"]
    markers_quota: ["[QUOTA_WAITING]", "[QUOTA_POLLING]", "[ORCHESTRATOR_QUOTA_RESUME]"]
    markers_fault: ["[ORCHESTRATOR_STOP_REASON]", "[ORCHESTRATOR_FATAL_ERROR]", "[HARNESS_INTEGRITY]"]
    markers_restart: ["[ORCHESTRATOR_AUTO_RESTART]"]
  env:
    deny_vars: ["AWS_PROFILE", "AWS_SESSION_TOKEN"]    # ADDITIONAL deny vars; the always-deny set is non-removable
  logging:
    pty_log_relpath: pty.log         # under the per-session state dir; created mode 0600
    redact_patterns: ["sk-ant-[A-Za-z0-9_-]+", "AKIA[0-9A-Z]{16}", "(?i)aws_secret[^\\s]*", "Bearer\\s+[A-Za-z0-9._-]+"]
  injectable_commands:               # extensible registry -- new slash commands added here need NO code change
    orchestrate:    "/devbench-orchestrate:orchestrate"
    effort_xhigh:   "/effort xhigh"
    model_opus:     "/model opus"
    quota_wait_choice: "1"           # DI-5 placeholder: the keystroke that selects "wait" at the quota prompt
    drain_now:      "/exit"
```

Notes:

- **`model` / `effort` resolution** mirrors the no-fallback orchestrate contract: model resolves
  `--model` > `supervise.model` > `orchestrate.model`, fail-fast if all unset; `haiku` is rejected.
  `DEVBENCH_CLAUDE_MODEL` is deliberately NOT consulted (it is the API-caller model and must not leak
  into the subscription session).
- **`env.deny_vars`** lists ADDITIONAL vars to strip; the always-deny API/Bedrock-routing set
  (`ANTHROPIC_API_KEY`, `ANTHROPIC_AUTH_TOKEN`, `ANTHROPIC_API_URL`, `ANTHROPIC_BASE_URL`,
  `DEVBENCH_USE_BEDROCK`, `AWS_*`) is non-removable. A config that tries to whitelist one of those by
  negation fails fast at load.
- **`detection_patterns.quota_wait_prompt`** and **`injectable_commands.quota_wait_choice`** are
  PLACEHOLDERS until the real interactive usage-limit prompt is captured against a live quota event
  (DI-5; see `spec/devbench-supervise-screen-orchestrator/QUOTA-VERIFICATION-TODO.md`). The
  poll-and-restart path plus the stable log markers carry correctness meanwhile.

---

## See also

- `sample-config.yaml` -- annotated copy of every field at its default value; copy and edit as a
  starting point.
- `src/devbench/config_loader.py` -- docstring contains the full YAML schema and dataclass
  definitions that are the source of truth for every field and its accepted values.
- `src/devbench/config-schema.json` -- JSON Schema that enforces structure at load time; unknown
  keys cause devbench to exit non-zero with an actionable error.
- [docs/architecture.md #8 Configuration model](architecture.md#8-configuration-model) -- covers
  value-resolution precedence and operational context for each section.
- [docs/model-pricing.md](model-pricing.md) -- per-model token pricing blocks for the `report:`
  section.
