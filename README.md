# DevBench -- Autonomous Backlog Execution

An LLM-as-Judge orchestration system that processes a backlog of work units autonomously. Development agents write code; judge agents review it. All review decisions come from Claude LLM evaluation; there are no hard-coded pass/fail rules.

## 60-second overview

DevBench takes a structured backlog of work units (epics, features, stories, tasks) and drives them through a TDD implement / parallel-judge-review / security-review / git-merge pipeline without human intervention between tasks.

- **Autonomous SDLC pipeline.** One operator writes the spec; the orchestrator drives every task from claim to merged PR.
- **Real LLM review at every gate.** Four review judges (code, test, docs, scope) run in parallel; a security judge runs after they pass. Every verdict is logged as an audit comment on the work unit.
- **Auditable by default.** Every agent action writes a timestamped comment on the work unit file. The orchestrator can resume from any point after a restart because state lives on disk, not in memory.

### Try it now (5 commands)

```bash
git clone <this-repo> && cd devbench
make install
make plugin-install
export JUDGE_WORKSPACE_ROOT=/path/to/your-backlog JUDGE_CLAUDE_MODEL=us.anthropic.claude-opus-4-7-v1
uv run devbench status
```

If `devbench status` prints a backlog summary, you are ready to launch an orchestration session (see [Interactive Mode](#interactive-mode)). If it fails, see [Troubleshooting](#troubleshooting) or the full install walkthrough in [Workspace Setup](#workspace-setup).

### Where to go next

Pick the doc closest to your role.

| You are... | Start here |
|------------|-----------|
| **An operator** running devbench against a backlog | [CLI Reference](#cli-reference), [FAQ](docs/faq.md), [Interactive Mode](#interactive-mode), [Troubleshooting](#troubleshooting), live dashboards in [docs/watch-activity.md](docs/watch-activity.md) and [docs/hook-activity.md](docs/hook-activity.md) |
| **A developer** extending or modifying devbench | [docs/architecture.md](docs/architecture.md) for the end-to-end model, [docs/plugin-architecture.md](docs/plugin-architecture.md) for agents/hooks/skill, the ADRs under [docs/adr/](docs/adr/) for rationale |
| **Authoring a new backlog** for devbench to execute | [docs/creating-specs-and-backlogs.md](docs/creating-specs-and-backlogs.md), [docs/backlog-contract.md](docs/backlog-contract.md), [docs/example-work-unit-template.md](docs/example-work-unit-template.md), [docs/authoring-manifests.md](docs/authoring-manifests.md) |
| **A decision-maker** assessing fit | [docs/architecture.md §2 Capabilities](docs/architecture.md#2-capabilities), then skim the ADR list under [docs/adr/](docs/adr/) |

## Table of contents

- [60-second overview](#60-second-overview)
- [How it works](#how-it-works)
- [CLI reference](#cli-reference)
- [Make targets](#make-targets)
- [Configuration](#configuration)
- [Workspace setup](#workspace-setup)
- [Interactive mode](#interactive-mode)
- [Troubleshooting](#troubleshooting)

## How it works

See [docs/execution-modes.md](docs/execution-modes.md) for the full step-by-step lifecycle (claim, implement, review, retry, security, git-ops, mark-done) and ownership rules.

```
Orchestrator (devbench:orchestrate SKILL / interactive Claude session)
  |
  |-- Step 0: sweep-proposals         -- materialise any pending proposal JSONs
  |-- Pre-flight: validate-backlog    -- abort if index / files are out of sync
  |-- Parse BACKLOG.md, find next actionable work unit
  |-- Implement work unit via TDD (RED -> GREEN -> REFACTOR)  [executor agent]
  |-- Run repo's task runners (make test, make validate)
  |-- Stage files, submit to judge review  [review-supervisor agent]
  |     |-- code-reviewer       -- SOLID, DRY, fail-fast, security, 12-factor
  |     |-- test-reviewer       -- TDD discipline, test quality, real assertions
  |     |-- doc-reviewer        -- accuracy, completeness, sync with code
  |     |-- changes-manifest    -- actual changes vs declared manifest
  |     |-- security-reviewer   -- CodeQL, Dependabot, secret-scanning alerts
  |-- If judges fail: read feedback, fix, resubmit (max retries configurable)
  |     |-- Prior feedback is injected into the next review to prevent contradictions
  |-- Git ops: commit, push, create PR, wait for CI, merge
  |-- Update BACKLOG.md status to Done (parents auto-roll up when children complete)
  |     |-- Done-gate: mark-done verifies all 4 review judges logged REVIEW_PASS
  |-- Repeat until every actionable unit is done
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

- `tail -f src/devbench/logs/orchestrator.log` for the main log.
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
| **Proposal workflow** | `write-proposal`, `materialise-proposal`, `sweep-proposals`, `promote-proposal`, `reject-proposal` |

All commands run from the parent workspace root (the directory containing the `devbench` checkout):

```bash
uv run devbench <command> [args]
# or: python3 -m devbench <command> [args]
```

`devbench --help` prints the full command list with one-line descriptions. `devbench <command> --help` prints usage for a specific command.

## Make targets

```bash
make install              # Install runtime and dev dependencies
make plugin-install       # Register the devbench plugin at user scope
make start-interactive    # Auth GitHub, launch interactive Claude session
make start                # Auth GitHub, launch orchestrator in background
make run-backlog          # Run orchestrator in foreground (assumes GH_TOKEN set)
make validate             # Full validation: lint + type check + tests + coverage
make lint                 # ruff + bandit
make format               # Auto-format with ruff
make check                # lint + mypy
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
- **Stop-hook circuit breaker** (prevents the orchestrator from stalling after context compaction; auto-allows stop after a configurable burst): tune `stop_hook.max_blocks`, `stop_hook.window_seconds`, `stop_hook.stale_task_minutes` in `devbench.yaml`, or override via `JUDGE_STOP_MAX_BLOCKS`, `JUDGE_STOP_WINDOW_SECONDS`, `JUDGE_STOP_STALE_MINUTES`. See [architecture.md §9 Hooks layer](docs/architecture.md#9-hooks-layer).
- **Display timezone** in `devbench report` and `devbench hook-tail`: set `report.display_timezone` (IANA zone name) in `devbench.yaml`, or override per invocation via `JUDGE_REPORT_TIMEZONE`. See [model-pricing.md](docs/model-pricing.md#other-settings-under-report).
- **Per-model token pricing** (needed when you run anything other than Opus 4.7): drop the matching `report.token_cost_per_million_*` block from [model-pricing.md](docs/model-pricing.md) into `devbench.yaml`.

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

Set `JUDGE_WORKSPACE_ROOT` to the backlog repo. Create symlinks inside it pointing to your target repos, and reference them as relative paths in `backlog/config/devbench.yaml`:

```bash
ln -s /workspaces/my-project/target-repo /workspaces/my-project/my-backlog/target-repo
```

```yaml
# backlog/config/devbench.yaml
repos:
  org/target-repo:
    default_branch: main
    checkout_directory: target-repo    # relative; resolves via the symlink
```

`checkout_directory` must be a relative path (absolute paths and `..` traversal are rejected). Symlinks bridge the backlog repo and the target repos cleanly.

For multiple target repos, create one symlink per repo:

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

## Interactive mode

```bash
make start-interactive
```

| Action | How |
|--------|-----|
| Pause | Press **Escape** |
| Give instructions | Type while paused, press Enter |
| Resume | Type `Continue` |
| Skip a unit | `Skip E1-F2-S3-T4, it's not needed` |
| Change priority | `Prioritize E2 work units next` |
| Check status | `What's the status?` |
| Stop | **Ctrl+C** (progress saved in BACKLOG.md) |

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

## Troubleshooting

### Backlog is out of sync with work unit files

Run `uv run devbench validate-backlog` to check for missing files, status mismatches, orphaned files, invalid dependency references, and Status Summary table count mismatches. Fix reported errors before running the orchestrator; it runs this check automatically at startup and aborts if any errors are found.

### Judge keeps failing the same unit

After `max_executor_retries` failures (default in the SKILL prompt), the unit is marked `blocked`. Read the Comments section of the work unit file for the feedback trail. See [the retry-budget FAQ](docs/faq.md) for recovery steps.

### `mark-done` fails with "not all required judges passed"

The done-gate found that not all four review judges (`code_review`, `test_review`, `doc_review`, `changes_manifest`) have a `[REVIEW_PASS]` entry after the most recent `[REVIEW_REJECTED]` line. Check the Comments section of the work unit file for the current verdicts, then re-run any failing agents via the orchestrate skill.

### Judge contradicts its previous feedback

This should not happen with the prior-feedback injection. If it does, check the orchestrator log for previous feedback entries (`grep "judge feedback for <unit-id>" src/devbench/logs/orchestrator.log`).

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
