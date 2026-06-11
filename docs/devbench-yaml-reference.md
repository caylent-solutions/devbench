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
  max_cascade_depth: 2  # recovery-of-recovery cascade depth cap
```

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
