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

At least one entry is required. Each key must be in `org/repo` format.

```yaml
repos:
  caylent-solutions/devbench:
    default_branch: main          # optional -- omit to fall back to origin/HEAD
    checkout_directory: devbench  # optional -- relative to DEVBENCH_WORKSPACE_ROOT
    merge_strategy: squash        # optional -- overrides top-level merge_strategy
    branch_prefix: wg_004         # optional -- overrides top-level git_ops.branch_prefix
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
  # branch_prefix: wg_004      # namespaces per-unit branches: backlog/<prefix>/<id-lower>
  defer_pr: false               # requires single_branch; commits stay local until git-ops-finalize
  pause_before_merge: false     # push + wait for CI, then transition to in-review
  inline_orphan_cleanup: true   # chore commit before task commit when orphans detected
  ci_failure_retry: true        # rc=2 on CI failure triggers executor retry with log feedback
  auto_finalize: false          # auto-run git-ops-finalize when all WUs terminal
  auto_merge: false             # auto-merge after CI green (requires auto_finalize + defer_pr)
  orphan_patterns: []           # replaces built-in orphan fnmatch list when non-empty
  pr_review_resolution:
    enabled: false
    agents: []
    decision_blocks: true
    settle_seconds: 60
    poll_interval: 5
```

---

## `stop_hook:` -- circuit breaker tuning

```yaml
stop_hook:
  max_blocks: 5
  window_seconds: 180
  stale_task_minutes: 120
```

**`DEVBENCH_STOP_HOOK_STATE_DIR`** (env var only, default `/tmp`) -- the
directory `continue-orchestration.sh` uses for its Stop-hook state file.
`/tmp` is shared machine-wide, so a test suite running alongside a live
orchestrator on the same host can collide on that file; set this env var to a
private directory in the test environment to isolate the two. Leaving it
unset preserves the previous `/tmp` behaviour exactly -- this is an optional
knob, not a required migration step.

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
  max_cascade_depth: 2  # recovery-of-recovery cascade depth cap
```

---

## `report:` -- cost estimation settings

```yaml
report:
  models:
    claude-opus-5:
      input: 5.0
      output: 25.0
  default_model:
    input: 5.0
    output: 25.0
  cache_read_multiplier: 0.10
  cache_write_5min_multiplier: 1.25
  cache_write_1hr_multiplier: 2.0
  data_residency_multiplier: 1.10
  fast_mode_multiplier: 6.0
  recent_pace_tasks: 10
  # display_timezone: America/New_York
```

The legacy scalar fields `token_cost_per_million_input`, `token_cost_per_million_output`, and
`token_cost_discount` were retired in issue #223; workspaces that still set them fail-fast at
config-load time. See [docs/model-pricing.md](model-pricing.md) for the full per-model pricing
table, the cost formula, and migration guidance.

---

## `manifest_amendment:` -- amendment workflow (on by default)

```yaml
manifest_amendment:
  enabled: true                    # default; set false to opt out
  allowed_reasons:
    - tdd_green_production_fix
  max_requests_per_execution: 1
```

---

## `task_factory:` -- task-factory loop (on by default, ADR-32)

```yaml
task_factory:
  enabled: true                    # default; set false to opt out; requires manifest_amendment.enabled: true
  auto_accept_proposals: false     # default; governs two auto-promote paths (write-proposal's synchronous materialise+promote cascade, and sweep-proposals' orphan-`proposed`-draft promote); new drafts always use backlog.default_status_for_new_work_units regardless; only applies when enabled: true
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
  review_team:
    code_reviewer: opus
    test_reviewer: opus
    doc_reviewer: opus
    changes_manifest: opus
```

All fields default to `null` (agent's `.md` frontmatter default). See
[docs/adr/25-per-agent-model-overrides.md](adr/25-per-agent-model-overrides.md).

---

## `quota_handling:` -- quota wait-and-resume configuration (issue #236, spec S5.2)

**Status: parsed, validated, and live.** This block is parsed, schema-checked and
range-checked at config-load time, `RuntimeConfig.quota_handling` is populated exactly as
documented below, and `cmd_start` reads `enabled` / `on_exhaustion` / `on_exhaustion_timeout` /
`resume_strategy` / `audit_comment_on_wait` / `audit_comment_on_resume` at runtime via
`_drive_orchestrate_with_quota_resume` -> `_dispatch_quota_detection` -> `_handle_quota_pause`
(`src/devbench/cli.py`; landed by E2-F4-S3-T1) -- see the per-field table below for exactly
when each is read. `log_structured_events` is the one exception: it is parsed and validated
like every other field, but has no runtime consumer today (see its table row). `enabled: true`
(the default) makes the orchestrator pause and poll for reset instead of exiting non-zero;
`enabled: false` restores the legacy non-zero exit.

This block governs what the orchestrator does when the Claude CLI reports a quota-exhaustion
signal (HTTP 429 / CLI "You've hit your limit" message). The whole block is optional; omitting
it entirely yields the full default set below -- never a partial or `None` config object.

```yaml
quota_handling:
  enabled: true
  on_exhaustion: wait
  poll_interval_seconds: 60
  max_wait_seconds: 18000
  on_exhaustion_timeout: drain
  resume_strategy: continue_current_wu
  audit_comment_on_wait: true
  audit_comment_on_resume: true
  log_structured_events: true
```

| Field | Type | Accepted values / range | Default | What it controls |
|---|---|---|---|---|
| `enabled` | boolean | `true`, `false` | `true` | Master toggle. `false` restores the legacy non-zero exit on quota exhaustion (`#193` AC-4, spec AC-24) -- the escape hatch for operators who prefer the pre-#236 behaviour. |
| `on_exhaustion` | string (enum) | `wait`, `fail`, `drain` | `wait` | Action taken when a quota signal is detected. `wait` pauses and polls until reset; `fail` re-raises immediately (non-zero exit, same as `enabled: false`); `drain` triggers a graceful drain then exits. |
| `poll_interval_seconds` | integer | `[30, 3600]` | `60` | Cadence in seconds between recovery probes while waiting. |
| `max_wait_seconds` | integer | `>= 1` | `18000` (5 hours) | Cap on total wait time in seconds before `on_exhaustion_timeout` fires. |
| `on_exhaustion_timeout` | string (enum) | `drain`, `fail`, `keep_waiting` | `drain` | Action taken when `max_wait_seconds` elapses without recovery. `drain` triggers a graceful drain; `fail` re-raises the quota error; `keep_waiting` is terminal -- it logs `[QUOTA_TIMEOUT_KEEP_WAITING]` and ends the run (no drain request, no re-raise; see `_dispatch_quota_timeout` in `src/devbench/cli.py`). |
| `resume_strategy` | string (enum) | `continue_current_wu`, `restart_wu`, `drain_and_resume` | `continue_current_wu` | How the orchestrator re-enters the loop after recovery. `continue_current_wu` resumes where it left off; `restart_wu` forces the current work unit back to `in-queue`; `drain_and_resume` removes the quota checkpoint and requests a graceful drain -- the run stops and must be restarted manually, since the Makefile auto-restart loop (`Makefile:117-123`) only fires on exit code 42, which a graceful drain does not produce. |
| `audit_comment_on_wait` | boolean | `true`, `false` | `true` | Append a `[QUOTA_WAITING]` audit comment to the in-progress work unit when pausing. |
| `audit_comment_on_resume` | boolean | `true`, `false` | `true` | Append a `[QUOTA_RESUMED]` audit comment after recovery. |
| `log_structured_events` | boolean | `true`, `false` | `true` | Parsed and validated; has no runtime consumer today -- quota transitions currently emit only the plain text markers (`[QUOTA_WAITING]`/`[QUOTA_RESUMED]`) via `logger.info`, not a JSON-structured event. |

**Enum and range enforcement happens at config-load time, never at dispatch time** (spec FR-2.9):
an invalid `on_exhaustion` / `on_exhaustion_timeout` / `resume_strategy` value, or a
`poll_interval_seconds` / `max_wait_seconds` value outside its documented range, raises a
`ValueError` naming the config file path and the offending field before the orchestrator starts.
Unknown keys inside the block are rejected the same way (`additionalProperties: false`).

**What `enabled: false` restores.** The quota core (E2-F1), this config surface, and the
E2-F4 dispatcher that acts on it are all live: `quota_handling.enabled: false` is the
config-level equivalent of the pre-#236 behaviour -- the orchestrator propagates the quota
error and exits non-zero instead of pausing and polling. `enabled: true` (the default) makes
the orchestrator pause and poll for reset instead.

**The `notifications.events` keys `quota_waiting` and `quota_resumed` are live.** They are
declared in the `notifications.events` schema block below (single ownership of
`config-schema.json` avoids two tasks writing the same file) and are read on every
quota-exhaustion pause/recovery by `_handle_quota_pause` (`src/devbench/cli.py`); see
[`docs/slack-notifications.md`](slack-notifications.md) for the payload shape.

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
    quota_waiting: false                 # orchestrator hit a quota and started waiting; see `quota_handling:` above.
    quota_resumed: false                 # quota recovered and the run resumed; see `quota_handling:` above.
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
