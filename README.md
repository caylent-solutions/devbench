# DevBench -- Autonomous Backlog Execution

An LLM-as-Judge orchestration system that processes a backlog of work units autonomously. Development agents write code; judge agents review it. All review decisions come from Claude LLM evaluation; there are no hard-coded pass/fail rules.

## 60-second overview

DevBench drives a structured backlog from claim to merged PR without human intervention between tasks.

**Input** -- a four-level work-unit hierarchy on disk:

| Level | Work unit |
|-------|-----------|
| 1 | Epic |
| 2 | Feature |
| 3 | Story |
| 4 | Task (the only level the orchestrator claims directly) |

**Pipeline** -- each task moves through four sequential stages:

| Stage | What runs |
|-------|-----------|
| 1 | TDD implement (RED -> GREEN -> REFACTOR) by the executor agent. |
| 2 | Parallel judge review -- four review judges run concurrently. |
| 3 | Security review -- one judge runs sequentially after stage 2 passes. |
| 4 | Git ops -- commit, push, create PR, wait for CI, merge. |

- **Autonomous SDLC pipeline.** One operator writes the spec; the orchestrator drives every task from claim to merged PR.
- **Real LLM review at every gate.** Every verdict is logged as an audit comment on the work unit.
- **Auditable by default.** Every agent action writes a timestamped comment on the work unit file. The orchestrator can resume from any point after a restart because state lives on disk, not in memory.

The judge / agent layer:

| # | Role | Agent | Runs when |
|---|------|-------|-----------|
| 1 | Review | `code-reviewer` | SOLID / DRY / fail-fast / 12-factor |
| 2 | Review | `test-reviewer` | TDD discipline + repo task-runner output |
| 3 | Review | `doc-reviewer` | Accuracy + sync with code |
| 4 | Review | `changes-manifest` | Actual diff vs declared Manifest |
| 5 | Security | `security-reviewer` | CodeQL / Dependabot / secret-scanning |
| 6 | Amender | `manifest-amender` | Judges `tdd_green_production_fix` amendments |
| 7 | Recovery | `blocker-resolver` | Decomposes amendment rejects into proposals |
| 8 | Recovery | `task-factory` | Materialises draft work units from proposals |

## Try it now

Going from a clean machine to a running orchestrator takes ten numbered steps. The walkthrough lives in [docs/zero-to-ready.md](docs/zero-to-ready.md) -- prerequisites verification, clone + install, Claude / Bedrock auth, workspace-root setup, YAML config, minimum-viable backlog, validation, and launch. Every command in that guide has been end-to-end execution-validated against the SHA stamped at the bottom of the doc.

### Where to go next

Pick the doc closest to your role.

| You are... | Start here |
|------------|-----------|
| **An operator** running devbench against a backlog | [CLI Reference](#cli-reference), [FAQ](docs/faq.md), [Interactive Mode](#interactive-mode), [Troubleshooting](#troubleshooting), live dashboards in [docs/watch-activity.md](docs/watch-activity.md) and [docs/hook-activity.md](docs/hook-activity.md) |
| **A developer** extending or modifying devbench | [docs/architecture.md](docs/architecture.md) for the end-to-end model, [docs/plugin-architecture.md](docs/plugin-architecture.md) for agents/hooks/skill, the ADRs under [docs/adr/](docs/adr/) for rationale, [open GitHub issues](https://github.com/caylent-solutions/devbench/issues) for in-queue work and technical debt, [docs/spec-operator-attention-alerts.md](docs/spec-operator-attention-alerts.md) for the attention-alerts future-work design sketch |
| **Authoring a new backlog** for devbench to execute | [docs/creating-specs-and-backlogs.md](docs/creating-specs-and-backlogs.md), [docs/backlog-contract.md](docs/backlog-contract.md), [docs/example-work-unit-template.md](docs/example-work-unit-template.md), [docs/authoring-manifests.md](docs/authoring-manifests.md) |
| **A decision-maker** assessing fit | [docs/architecture.md §2 Capabilities](docs/architecture.md#2-capabilities), then skim the ADR list under [docs/adr/](docs/adr/) |

## Table of contents

- [60-second overview](#60-second-overview)
- [How it works](#how-it-works)
- [CLI reference](#cli-reference)
- [Make targets](#make-targets)
- [Configuration](#configuration)
- [Workspace setup](#workspace-setup)
- [Real-world backlog examples](#real-world-backlog-examples)
- [Interactive mode](#interactive-mode)
- [Remote EC2 dev environments](#remote-ec2-dev-environments)
- [Troubleshooting](#troubleshooting)

## How it works

See [docs/execution-modes.md](docs/execution-modes.md) for the full step-by-step lifecycle (claim, implement, review, retry, security, git-ops, mark-done) and ownership rules.

```
Orchestrator (devbench:orchestrate SKILL / interactive Claude session)
  |
  |-- Step 0: sweep-proposals         -- materialise any pending proposal JSONs
  |-- Pre-flight: validate-backlog    -- abort if index / files are out of sync
  |-- Parse BACKLOG.md, find next actionable work unit (topological-depth order, issue #121)
  |-- Implement work unit via TDD (RED -> GREEN -> REFACTOR)  [executor agent]
  |     |-- Optional: manifest-amender judges a `tdd_green_production_fix`
  |     |   amendment when the executor needs to expand the Manifest mid-cycle
  |-- Run repo's task runners (make test, make validate)
  |-- Stage files, submit to judge review  [review-supervisor agent]
  |     |-- code-reviewer       -- SOLID, DRY, fail-fast, security, 12-factor
  |     |-- test-reviewer       -- TDD discipline, test quality, real assertions
  |     |-- doc-reviewer        -- accuracy, completeness, sync with code
  |     |-- changes-manifest    -- actual changes vs declared manifest
  |-- If review judges fail: read feedback, fix, resubmit
  |     |-- Per-judge or global retry budget (issue #122)
  |     |-- Prior feedback is injected into the next review to prevent contradictions
  |-- security-reviewer  -- CodeQL, Dependabot, secret-scanning alerts (sequential gate after the 4 review judges pass)
  |-- Git ops: commit, push, create PR, wait for CI, merge (or pause-before-merge per #101)
  |     |-- CI failure: rc=2 -> executor retry with the failing-job log (issue #115)
  |     |-- PR-bot review feedback: rc=3 -> executor retry with structured comment payload (issue #116)
  |     |-- Build/state orphan in working tree: chore commit before the task commit (inline orphan cleanup)
  |-- Update BACKLOG.md status to Done (parents auto-roll up when children complete)
  |     |-- Done-gate: mark-done verifies all 4 review judges logged REVIEW_PASS in the most recent round
  |-- Repeat until every actionable unit is done

Recovery cascade (fires when an amendment / executor retry exhausts):
  manifest-amender REJECT
    -> blocker-resolver writes a proposal JSON
    -> task-factory materialises 0..N draft work units
    -> validate-backlog wires deps so the source task unblocks when drafts complete
```

Five judges must pass before a work unit merges. The four review judges are tracked via `[REVIEW_PASS]` comments; the done-gate verifies all four passed in the most recent round. Security runs as a separate sequential gate after the four pass and before the git commit. A security failure writes `[SECURITY_FAIL]` followed by `[REVIEW_REJECTED]`; the `[REVIEW_REJECTED]` resets the done-gate window so the four review judges re-run after the security fix lands.

### Review feedback loop

Each judge review is an independent LLM call. Without memory, a judge might say "use `--deselect`" in round 1 then contradict itself with "use `xfail`" in round 2. The CLI parses the orchestrator log for prior feedback and injects it as a "Previous Review Feedback" section in the evidence payload:

> If the code has been updated to address this feedback, do not re-raise the same issues. Do NOT contradict prior feedback by requesting the opposite change.

First review: no history, the LLM's first feedback becomes the anchor. Subsequent reviews see their prior feedback and stay consistent.

### Test execution

The `test_review` judge runs the repo's own task runner:

1. If the target repo has a `Makefile` with a `test` target, it runs `make test`.
2. Otherwise it falls back to bare `pytest`.

This ensures the judge sees the same results the developer would, including env-var, exclusion, and flag settings from the Makefile.

### Evidence truncation

When sending file contents to the LLM, large inputs are truncated to fit context limits. Every truncation point includes an explicit marker so the LLM does not mistake a preview for a complete file:

```
[... TRUNCATED -- showing 3000 of 8500 chars. File is complete on disk.]
```

### Monitoring

- `tail -f $JUDGE_WORKSPACE_ROOT/logs/<session>-orchestrator.log` for the main log (path is declared in `backlog/config/devbench.yaml` under `log_file:`; the legacy `src/devbench/logs/orchestrator.log` path is no longer used).
- Every work unit `.md` has a `## Comments` section with timestamped entries from every judge and orchestrator action.
- `uv run devbench watch` prints a one-screen live dashboard (read-only). See [docs/watch-activity.md](docs/watch-activity.md).
- `uv run devbench hook-tail` pretty-tails the plugin hook event stream in real time (read-only). See [docs/hook-activity.md](docs/hook-activity.md).

## CLI reference

Full per-command details, flags, and examples live in [docs/cli-reference.md](docs/cli-reference.md). At a glance:

| Group | Commands |
|-------|----------|
| **Backlog read** | `status`, `next`, `report`, `watch`, `hook-tail`, `list-proposals`, `validate-backlog`, `read-unit` |
| **Backlog write** | `claim`, `set-status`, `mark-done`, `decline`, `start` |
| **Orchestrator helpers** | `log`, `log-verdict`, `log-comment`, `log-tdd`, `get-diff`, `run-tests`, `ensure-branch`, `git-ops`, `git-ops-finalize` |
| **Amendment workflow** | `request-amendment`, `apply-amendment`, `reject-amendment` |
| **Proposal workflow** | `write-proposal`, `materialise-proposal`, `sweep-proposals`, `promote-proposal`, `reject-proposal`, `add-dep` |

All commands run from the parent workspace root (the directory containing the `devbench` checkout):

```bash
uv run devbench <command> [args]
# or: python3 -m devbench <command> [args]
```

`devbench --help` prints the full command list with one-line descriptions. `devbench <command> --help` prints usage for a specific command.

## Make targets

```bash
make install              # Install runtime and dev dependencies
make start                # Launch the orchestrator via Agent SDK (non-interactive, recommended)
make plugin-install       # !! DEFAULT: DO NOT RUN. Only required for `make start-interactive`.
                          # NEVER needed for `make start` (the recommended non-interactive default).
                          # Globally installs Claude Code hooks that block EVERY Claude session
                          # on this machine from writing to backlog/** files, breaking the
                          # operator workflow of editing work units in a separate Claude session.
                          # If you must install (for interactive mode), plan to uninstall after.
make start-interactive    # Launch interactive Claude session with devbench plugin (observation only)
make validate             # Full validation: lint + type check + tests + coverage
make lint                 # ruff + bandit + no-duplicates guard
make format               # Auto-format with ruff
make typecheck            # mypy type checking
make test                 # All tests (unit + functional)
make report               # Show backlog progress report
make report-session       # Show progress since a timestamp (SINCE=<iso-ts>)
make clean                # Remove caches
```

## Configuration

Two environment variables MUST be set before any command runs (otherwise startup exits non-zero):

- `JUDGE_WORKSPACE_ROOT` -- absolute path to the workspace containing `BACKLOG.md` and `backlog/`.
- `JUDGE_CLAUDE_MODEL` -- model identifier (for example, `us.anthropic.claude-opus-4-7-v1`).

Everything else is optional. Per-repo settings, git-ops mode, stop-hook tuning, token pricing, and reporting timezone all live in `backlog/config/devbench.yaml` (relative to `JUDGE_WORKSPACE_ROOT`). Override the default lookup with the `--config <path>` CLI flag or `JUDGE_CONFIG_PATH` env var.

For the full annotated YAML, value-resolution precedence, and every config key, see [docs/architecture.md §8 Configuration model](docs/architecture.md#8-configuration-model). For per-model token pricing and cost-formula details, see [docs/model-pricing.md](docs/model-pricing.md).

### Common tuning

- **Single-branch mode** (one shared branch for the whole backlog, one PR at the end instead of one per work unit): set `git_ops.single_branch` and `git_ops.defer_pr` in `devbench.yaml`. See [architecture.md §6](docs/architecture.md#6-multi-pr-vs-single-pr-mode).
- **Local-only mode** (drive operational work -- AWS teardowns, evidence capture, audits -- against a sibling checkout that has no GitHub remote, never pushes, never produces a PR): set `git_ops.local_only: true` (alongside `git_ops.single_branch` + `git_ops.defer_pr: true`) in `devbench.yaml`. See [docs/operational-work.md](docs/operational-work.md) for the end-to-end pattern and [docs/git-ops-modes.md](docs/git-ops-modes.md) for the mode comparison.
- **Stop-hook circuit breaker** (prevents the orchestrator from stalling after context compaction; auto-allows stop after a configurable burst): tune `stop_hook.max_blocks`, `stop_hook.window_seconds`, `stop_hook.stale_task_minutes` in `devbench.yaml`, or override via `JUDGE_STOP_MAX_BLOCKS`, `JUDGE_STOP_WINDOW_SECONDS`, `JUDGE_STOP_STALE_MINUTES`. See [architecture.md §9 Hooks layer](docs/architecture.md#9-hooks-layer).
- **Pause-before-merge** (orchestrator pushes the PR + waits for green CI then transitions the task to `in-review` instead of merging; reconciles via `devbench check-merge` on the next loop iteration): set `git_ops.pause_before_merge: true` in `devbench.yaml`, or override via `JUDGE_PAUSE_BEFORE_MERGE`. Cannot be combined with `defer_pr` or `single_branch`. See [docs/git-ops-modes.md](docs/git-ops-modes.md) and [ADR-13](docs/adr/13-pause-before-merge.md).
- **CI-failure executor retry** (default-on; rc=2 from `git-ops` triggers an executor retry with the failing-job log as feedback under `.devbench/ci-failures/<id>-<n>.log`): toggle via `git_ops.ci_failure_retry` in `devbench.yaml` or `JUDGE_CI_FAILURE_RETRY_ENABLED`.
- **PR-bot review polling** (between CI-pass and merge, polls for unresolved Copilot / Q-Dev / internal-bot comments and re-invokes the executor with structured feedback): opt in via `git_ops.pr_review_resolution.enabled: true` plus the `agents:` allowlist.
- **Per-judge executor retry budgets** (different judges can flake at different rates; tune retries per failing judge instead of raising the global cap): set `max_executor_retries_per_judge:` map in `devbench.yaml`. Each entry falls back to `max_executor_retries` when absent.
- **Manifest amendments** (executors can request a `tdd_green_production_fix` to expand their Changes Manifest mid-cycle when TDD GREEN reveals required production fixes; the manifest-amender judges scope, approach-coherence, and standards): toggle via `manifest_amendment.enabled`. See [docs/manifest-amendments.md](docs/manifest-amendments.md) and [ADR-02](docs/adr/02-manifest-amendment-workflow.md).
- **Task-factory loop** (after manifest-amendment rejects, blocker-resolver decomposes the rejection and task-factory materialises draft work units the source task can depend on): toggle via `task_factory.enabled` and `task_factory.auto_accept_proposals`. See [docs/task-factory.md](docs/task-factory.md) and [ADR-03](docs/adr/03-task-factory.md).
- **HOLD lifecycle** (`devbench hold <id>` / `devbench unhold <id>`): tasks deliberately deferred without breaking dep-chain math.
- **Display timezone** in `devbench report` and `devbench hook-tail`: set `report.display_timezone` (IANA zone name) in `devbench.yaml`, or override per invocation via `JUDGE_REPORT_TIMEZONE`. See [model-pricing.md](docs/model-pricing.md#other-settings-under-report).
- **Per-model token pricing** (needed when you run anything other than Opus 4.7): drop the matching `report.token_cost_per_million_*` block from [model-pricing.md](docs/model-pricing.md) into `devbench.yaml`.
- **Cost premium multipliers**: `report.data_residency_multiplier` (default 1.10) and `report.fast_mode_multiplier` (default 6.0) are applied per-call to the residency-flagged / fast-mode token subsets. Composes with cache + base-rate multipliers, applies before the `report.token_cost_discount` (issue #124).
- **Hook-tail column caps**: tune `hook_tail.agent_width`, `hook_tail.tool_width`, `hook_tail.description_max` (default 120), `hook_tail.stdout_preview_max` in `devbench.yaml`, or override via `JUDGE_HOOK_TAIL_*` env vars (issue #134).

## Workspace setup

### Recommended: keep the backlog in its own git repo

The backlog (`BACKLOG.md`, `backlog/`, specs) should live in a dedicated local git repo, separate from the target repositories devbench modifies. This lets you track backlog progress with commits without mixing backlog changes into the target repos.

```
/workspaces/my-project/
  my-backlog/              <-- JUDGE_WORKSPACE_ROOT (its own git repo)
    BACKLOG.md
    backlog/
      config/devbench.yaml
      E0/...
    specs/
  target-repo/             <-- the repo devbench modifies (separate git repo)
```

Set `JUDGE_WORKSPACE_ROOT` to the backlog repo. The repo named in `checkout_directory` must be reachable at `<JUDGE_WORKSPACE_ROOT>/<checkout_directory>`. Two layouts are supported:

**Default (sibling directory)** -- clone the target repo *inside* the backlog repo, alongside `backlog/`:

```bash
cd /workspaces/my-project/my-backlog
git clone https://github.com/org/target-repo.git
```

```yaml
# backlog/config/devbench.yaml
repos:
  org/target-repo:
    default_branch: main
    checkout_directory: target-repo    # real directory under the backlog repo
```

Add `target-repo/` to `<JUDGE_WORKSPACE_ROOT>/.gitignore` so the target-repo working tree doesn't pollute backlog history.

**Alternative (symlink, for shared / pre-cloned repos)** -- clone the target repo elsewhere and symlink it into the backlog repo. Useful when one target repo serves multiple backlogs or already lives outside the workspace:

```bash
ln -s /workspaces/my-project/target-repo /workspaces/my-project/my-backlog/target-repo
```

```yaml
repos:
  org/target-repo:
    default_branch: main
    checkout_directory: target-repo    # relative; resolves via the symlink
```

The choice is purely an operator filesystem decision -- there is no YAML field that toggles symlink-awareness. `checkout_directory` always names a path under the backlog repo; whether that path is a real directory or a symlink is transparent to devbench. `checkout_directory` must be a relative path (absolute paths and `..` traversal are rejected). See [`docs/backlog-contract.md` § "Workspace layout"](docs/backlog-contract.md#workspace-layout-what-judge_workspace_root-points-at) for the full contract.

For multiple target repos, repeat either pattern per repo:

```bash
ln -s /workspaces/my-project/repo-a /workspaces/my-project/my-backlog/repo-a
ln -s /workspaces/my-project/repo-b /workspaces/my-project/my-backlog/repo-b
```

```yaml
repos:
  org/repo-a:
    default_branch: main
    checkout_directory: repo-a
  org/repo-b:
    default_branch: main
    checkout_directory: repo-b
```

### Minimal working-tree launch

```bash
# Shell 1: start interactive session
cd /path/to/devbench && \
  JUDGE_WORKSPACE_ROOT=/path/to/my-backlog \
  JUDGE_CLAUDE_MODEL=us.anthropic.claude-opus-4-7-v1 \
  claude --plugin-dir plugin/devbench

# Shell 2: watch progress
cd /path/to/devbench && watch -n 30 \
  'JUDGE_WORKSPACE_ROOT=/path/to/my-backlog \
   JUDGE_CLAUDE_MODEL=us.anthropic.claude-opus-4-7-v1 \
   uv run devbench status'
```

In the interactive session, set the model with `/model` if needed, then ask: `Run the devbench:orchestrate skill to process the backlog`.

### Starting devbench without make

The `make start-interactive` and `make start` targets are thin wrappers. If you need to invoke the underlying commands directly (for example, in CI scripts or remote shells where `make` is unavailable), use these equivalents.

**Interactive with `--dangerously-skip-permissions` (default `make start-interactive`)**

```bash
JUDGE_WORKSPACE_ROOT=/path/to/my-backlog \
JUDGE_CLAUDE_MODEL=us.anthropic.claude-opus-4-7-v1 \
claude --dangerously-skip-permissions --plugin-dir /path/to/devbench/plugin/devbench
```

**Interactive without `--dangerously-skip-permissions` (equivalent to `JUDGE_SAFE_PERMISSIONS=1 make start-interactive`)**

```bash
JUDGE_WORKSPACE_ROOT=/path/to/my-backlog \
JUDGE_CLAUDE_MODEL=us.anthropic.claude-opus-4-7-v1 \
claude --plugin-dir /path/to/devbench/plugin/devbench
```

Setting `JUDGE_SAFE_PERMISSIONS=1` when invoking `make start-interactive` selects the no-flag variant above. This is the safe-mode opt-out for environments that require explicit permission prompts.

**Non-interactive (equivalent to `make start`)**

```bash
JUDGE_WORKSPACE_ROOT=/path/to/my-backlog \
JUDGE_CLAUDE_MODEL=us.anthropic.claude-opus-4-7-v1 \
uv run python -m devbench.cli start
```

## Real-world backlog examples

Worked examples of real specs promoted into validated DevBench backlogs.
Each example ships the locked spec, the authored `backlog/` tree, the
`devbench.yaml` configuration, the operator launch commands, and a
step-by-step `how-it-was-made.md` describing the authoring journey. Each
example uses the same **before / after** layout: `before/` is the
validated, ready-to-run backlog; `after/` (when populated) is the
post-execution snapshot showing what DevBench actually produced.

| Example | Mode | Repos | Work units | Status |
|---|---|---|---|---|
| [`examples/backlogs/brownfield/multi-repo_single-pr_no-merge/`](examples/backlogs/brownfield/multi-repo_single-pr_no-merge/) | `single_branch` + `defer_pr` + `auto_finalize` + `auto_merge` + `ci_failure_retry` (no manual merge step) | 3 (caylent-solutions/kanon, caylent/caylent-private-kanon, caylent/kanon-claude-marketplaces) | 207 across 13 epics + 14 features | `before/` ready; `after/` Coming Soon |

More examples land here as backlogs are authored. Each example targets a
distinct DevBench mode (single-repo vs multi-repo, paused-merge vs
auto-merge, greenfield vs brownfield) so operators can pick the closest
match to their own setup and copy it as a starting point.

## Interactive mode

> **Non-interactive is now the recommended default** (`make start`) -- and in
> almost every situation it's also the only mode you need. DevBench is stable
> enough that the backlog itself is the right place to manage a run, not a
> live console.
>
> **You can get live observation in non-interactive mode**, side-by-side in a
> separate terminal:
>
> - `devbench hook-tail` -- pretty-streams every tool call, judge verdict,
>   and status transition as the orchestrator runs. Same firehose interactive
>   mode shows, without entering Claude Code.
> - `devbench report` -- live progress dashboard on TTY (epic counts, recent
>   transitions, judge pass/fail, CI status, cost). Refreshes continuously.
> - `devbench status` -- one-shot snapshot; pair with `watch -n 30 '...'`
>   for a low-frequency monitor.
>
> Between those three commands plus `git log` on your backlog repo, you see
> exactly what the orchestrator is doing in real time -- with no need to
> open Claude Code at all.
>
> **So when is interactive mode actually useful?** Almost never. The only
> thing it offers over the non-interactive + hook-tail combination is the
> ability to type natural-language instructions to the orchestrate skill
> mid-run -- which is exactly the behaviour we recommend AGAINST (live
> mid-claim interjection disturbs the executor's reasoning; corrections
> belong in the backlog, not the console).
>
> **Plugin install caveat (read before considering interactive).** Interactive
> mode requires the devbench Claude Code plugin to be loaded. The user-scope
> install (`make plugin-install`) registers the plugin's hooks **globally on
> this machine**, which **blocks every other Claude Code session you open
> from writing to `backlog/**` files** -- breaking the two-track operator
> workflow below. Prefer the per-session `--plugin-dir` approach
> (`claude --plugin-dir $DEVBENCH_DIR/plugin/devbench`) so the hooks load
> only for the observation session, OR uninstall (`claude plugin uninstall
> devbench --scope user && claude plugin marketplace remove devbench --scope
> user`) as soon as you're done observing. **For non-interactive runs the
> plugin is never needed** -- the Agent SDK loads it ad-hoc from the
> checkout. Skip `make plugin-install` entirely unless you have a specific
> reason to run interactive.
>
> **When you need to change something, stop the run and split the work
> across two tools:**
>
> - **`devbench` CLI** moves state and wires the graph: `decline`, `hold`,
>   `unhold`, `add-dep`, `set-status`, `log-comment`, `sync-blocked`,
>   `validate-backlog`. No file edits, just state mutations.
> - **Claude** (separate session) edits the work-unit `.md` content:
>   Approach, Manifest, Acceptance Criteria, or authoring a new work unit
>   entirely. The CLI doesn't edit prose; Claude does.
>
> Both tools point at the same workspace. See
> [`docs/zero-to-ready.md`](docs/zero-to-ready.md) Step 10 for the full
> two-track operator workflow, and
> [`examples/backlogs/brownfield/multi-repo_single-pr_no-merge/operator-interventions.md`](examples/backlogs/brownfield/multi-repo_single-pr_no-merge/operator-interventions.md)
> for a worked example.

```bash
make start-interactive
```

Restarting picks up where you left off: `done` units are skipped and `in-progress` units are resumed.

### LLM authentication

The judge layer uses your existing Claude Code OAuth credentials; no separate Anthropic API key is needed as long as you are logged into Claude Code (`claude` in terminal). See [docs/llm-authentication.md](docs/llm-authentication.md) for details. Alternatively set `JUDGE_USE_BEDROCK=1` to route LLM calls through AWS Bedrock.

### GitHub pre-configured token

If `GH_TOKEN` is already set, the start scripts skip the `gh auth` flow:

```bash
export GH_TOKEN="ghp_your_token_here"
make start-interactive
```

Both start modes authenticate with GitHub (or skip when `GH_TOKEN` is set), grant required scopes (repo, workflow, read:org, admin:repo_hook, security_events), and launch the orchestrator.

## Remote EC2 dev environments

For unattended runs, multi-operator workflows, or multiple parallel orchestrate sessions per operator, run the orchestrator on a remote EC2 dev box instead of in your local devcontainer. The provisioning stack (Terraform + Terragrunt + Ansible + a per-user multi-session launcher) is documented end-to-end in [`docs/remote-ec2-setup.md`](docs/remote-ec2-setup.md). It covers prerequisites, shared-infra provisioning, per-user instance stamping, Ansible bootstrap, the `devbench-session` launcher, environment configuration (including the E230 `JUDGE_ORCHESTRATOR_SESSION_ID` filter), and refresh / teardown workflows.

## Troubleshooting

### Backlog is out of sync with work unit files

Run `uv run devbench validate-backlog` to check for missing files, status mismatches, orphaned files, invalid dependency references, and Status Summary table count mismatches. Fix reported errors before running the orchestrator; it runs this check automatically at startup and aborts if any errors are found.

### Judge keeps failing the same unit

After `max_executor_retries` failures (default in the SKILL prompt), the unit is marked `blocked`. Read the Comments section of the work unit file for the feedback trail. See [the retry-budget FAQ](docs/faq.md) for recovery steps.

### `mark-done` fails with "not all required judges passed"

The done-gate found that not all four review judges (`code_review`, `test_review`, `doc_review`, `changes_manifest`) have a `[REVIEW_PASS]` entry after the most recent `[REVIEW_REJECTED]` line. Check the Comments section of the work unit file for the current verdicts, then re-run any failing agents via the orchestrate skill.

### Judge contradicts its previous feedback

This should not happen with the prior-feedback injection. If it does, check the orchestrator log for previous feedback entries (`grep "judge feedback for <unit-id>" $JUDGE_WORKSPACE_ROOT/logs/<session>-orchestrator.log`).

### GitHub token expired

```bash
unset GH_TOKEN
gh auth refresh -h github.com -s repo -s workflow -s read:org -s admin:repo_hook -s security_events
export GH_TOKEN="$(gh auth token)"
```

### Want to re-process a completed unit

Edit the work unit `.md` file, change `## Status: done` to `## Status: in-queue`, and update `BACKLOG.md` accordingly.

### I rejected a proposal and it came back

Fixed by [ADR-09](docs/adr/09-idempotent-materialise-proposal.md). If you see resurrection after ADR-09 shipped, report it as a regression (the resurrection-guard test in `tests/test_cli.py::TestCmdSweepProposalsResurrectionGuard` would have failed).
