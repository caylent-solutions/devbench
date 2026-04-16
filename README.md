# DevBench — Autonomous Backlog Execution

An LLM-as-Judge orchestration system that processes a backlog of work units autonomously. Development agents write code; judge agents review it. All review decisions are made by Claude LLM evaluation — no hardcoded pass/fail rules.

> **New here?** Start with the [Architecture overview](docs/architecture.md) for the end-to-end picture (diagrams, capabilities, gaps), then come back here for install + commands.

## Table of contents

- [Quick Start](#quick-start)
- [How It Works](#how-it-works)
- [Evidence Truncation](#evidence-truncation)
- [Monitoring](#monitoring)
- [CLI Reference](#cli-reference)
- [Make Targets](#make-targets)
- [Architecture](#architecture)
- [Configuration](#configuration)
  - [Single-branch mode](#single-branch-mode)
  - [Token cost estimates](#token-cost-estimates)
  - [Stop hook (circuit breaker)](#stop-hook-circuit-breaker)
- [Workspace Setup](#workspace-setup)
- [Interactive Mode](#interactive-mode)
- [Troubleshooting](#troubleshooting)

## Quick Start

### Interactive (recommended)

```bash
cd <workspace>/devbench
make start-interactive
```

This launches an interactive Claude Code session where you can:
- **Escape** to pause at any time
- Type instructions while paused (skip units, change priorities, ask questions)
- Type `Continue` to resume
- **Ctrl+C** to stop (progress saved in BACKLOG.md)

### Background (unattended)

```bash
cd <workspace>/devbench
make start
```

Runs the orchestrator in the background. Monitor with `tail -f src/devbench/logs/orchestrator.log`.

The orchestrator is hardened against context-compaction stalls by a [Stop hook circuit breaker](#stop-hook-circuit-breaker) — if Claude tries to stop while a task is still in-progress, the hook injects a continuation instruction and re-enters the loop.

### Pre-configured token (skip OAuth)

If `GH_TOKEN` is already set, both scripts skip the `gh auth` flow entirely:

```bash
export GH_TOKEN="ghp_your_token_here"
make start-interactive
```

### Both modes:
1. Authenticate with GitHub (or skip if `GH_TOKEN` is already set)
2. Grant required token scopes (repo, workflow, read:org, admin:repo_hook, security_events)
3. Launch the orchestrator

### LLM Authentication (no API key required)

The LLM judge layer uses your existing Claude Code OAuth credentials — no separate Anthropic API key needed. Just be logged into Claude Code (`claude` in terminal). See [docs/llm-authentication.md](docs/llm-authentication.md) for details.

Alternatively, set `JUDGE_USE_BEDROCK=1` to use AWS Bedrock for LLM calls instead of the Anthropic API directly.

## How It Works

See [docs/execution-modes.md](docs/execution-modes.md) for a full description of both execution modes, the step-by-step lifecycle, and ownership rules.

```
Orchestrator (orchestrate SKILL.md / interactive Claude session)
  │
  ├── Pre-flight: validate-backlog — abort if index/files are out of sync
  ├── Parse BACKLOG.md → find next actionable work unit
  ├── Implement work unit via TDD (RED → GREEN → REFACTOR)  [devbench:executor agent]
  ├── Run repo's task runners (make test, make validate)
  ├── Stage files and submit to judge review  [devbench:review-supervisor agent]
  │     ├── code-reviewer       — SOLID, DRY, fail-fast, security, 12-factor
  │     ├── test-reviewer       — TDD discipline, test quality, assertions
  │     ├── doc-reviewer        — accuracy, completeness, sync with code
  │     ├── changes-manifest    — actual changes vs. expected manifest
  │     └── security-reviewer   — CodeQL, Dependabot, secret scanning alerts
  ├── If judges fail → read feedback, fix, resubmit (≤10 tries)
  │     └── Prior feedback injected into re-review to prevent contradictions
  ├── Git ops: commit, push, create PR, wait for CI, merge
  ├── Update BACKLOG.md status to Done (with automatic parent rollup)
  │     └── Done-gate: mark_done() verifies all 4 review judges passed
  └── Repeat until all work units are done
```

All five judges must pass before a work unit can be merged. Four judges (`code_review`, `test_review`, `doc_review`, `changes_manifest`) are tracked via `[REVIEW_PASS]` comments in the work unit file; the done-gate in `mark_done()` verifies all four passed in the most recent round. The security judge runs as a separate sequential gate after the four pass and before the git commit — a security failure writes `[SECURITY_FAIL]` and then `[REVIEW_REJECTED]` to the work-unit comment history — the `[SECURITY_FAIL]` records the rejection reason; the `[REVIEW_REJECTED]` resets the done-gate window, ensuring the four judges re-run after any security fix.

## Review Feedback Loop

A key feature is the **prior feedback injection** that prevents judges from contradicting themselves across review rounds.

**The problem**: Each judge review is an independent LLM call. Without context, the LLM might say "use `--deselect`" in round 1, then "use `xfail` instead of `--deselect`" in round 2 — creating an infinite loop where the agent can never satisfy both.

**The solution**: When re-reviewing a work unit, the CLI parses the orchestrator log for the most recent feedback from each judge and injects it as a "Previous Review Feedback" evidence section. The LLM prompt instructs:

> If the code has been updated to address this feedback, do not re-raise the same issues. Do NOT contradict prior feedback by requesting the opposite change.

This means:
- **First review**: No history — the LLM gives fresh feedback, which becomes the anchor
- **Subsequent reviews**: The LLM sees its prior feedback and stays consistent. If the agent addressed the feedback, the issue is resolved. If not, it's flagged again.

## Test Execution

The test_review judge runs tests through the repo's own task runner when available:

1. If the repo has a `Makefile` with a `test` target → runs `make test`
2. Otherwise → falls back to bare `pytest`

This ensures the judge sees the same test results the developer would see, including environment variables, exclusions, and flags configured in the Makefile.

## Evidence Truncation

When sending file contents and evidence to the LLM, the system truncates large inputs to fit context limits. All truncation points include explicit markers:

```
[... TRUNCATED — showing 3000 of 8500 chars. File is complete on disk.]
```

This prevents the LLM from mistaking system-truncated previews for incomplete source files.

## Monitoring

### Log file

```bash
tail -f src/devbench/logs/orchestrator.log
```

### Work unit audit trail

Every work unit `.md` file has a `## Comments` section with timestamped entries from every judge and orchestrator action.

## CLI Reference

All commands run from the parent workspace root (the directory containing the `devbench` checkout):

```bash
devbench <command> [args]
# or: python3 -m devbench <command> [args]
```

### Backlog navigation and status

`status` — show backlog summary (counts by status). No arguments.

`next` — print the next actionable work unit as JSON. No arguments.

`claim <unit-id>` — claim a work unit (set status to `in-progress`).

`set-status <unit-id> <status>` — force any status (no gate; use for recovery or lifecycle transitions).

`mark-done <unit-id>` — mark unit as `done`. Enforces the done-gate: all four review judges must have logged `REVIEW_PASS` in the most recent round.

`validate-backlog` — check backlog integrity (file existence, status sync, orphans, dep references, summary table counts, content rules). No arguments.

### Reading work unit context (for agents)

`read-unit <unit-id>` — print the work unit spec as markdown (for agent context).

`get-diff <unit-id>` — print git diff vs default branch (for review agents).

`run-tests <unit-id>` — run the test suite in the work unit's target repo.

### Logging

`log-verdict <judge> <unit-id> <pass|fail> [msg]` — record a judge verdict in the work unit Comments.

`log-comment <agent> <unit-id> <message>` — append a non-verdict agent comment to the work unit Comments.

`log-tdd <unit-id> <RED|GREEN|REFACTOR> <message>` — append a TDD phase entry to the work unit TDD Cycle Log.

`log <message>` — append a free-form message to the orchestrator log file.

### Git operations

`ensure-branch <unit-id>` — create or switch to the work unit branch before the executor runs.

`git-ops <unit-id>` — commit, push, create PR, wait for CI, merge. In single-branch + defer-PR mode, commits locally only.

`git-ops-finalize <repo>` — single-branch mode only: push the shared branch and create one PR for all accumulated commits.

### Orchestration and reporting

`start` — run the orchestrate skill non-interactively via the Agent SDK. No arguments.

`report [--watch N] [since-timestamp]` — print the progress report with velocity, token consumption, and cost estimates. With `--watch N`, refreshes every N seconds (Ctrl+C to exit). Optional `since-timestamp` (ISO-8601) limits to events after that time.

## Make Targets

```bash
make install              # Install runtime and dev dependencies
make start-interactive    # Auth GitHub + launch interactive Claude session
make start                # Auth GitHub + launch orchestrator in background
make run-backlog          # Run orchestrator in foreground (assumes GH_TOKEN set)
make validate             # Full validation: lint + type check + tests
make lint                 # ruff + bandit
make format               # Auto-format with ruff
make check                # lint + mypy
make test                 # All tests (unit + functional)
make report               # Show backlog progress report (full session)
make report-session       # Show progress since a timestamp (SINCE=<iso-ts>)
make clean                # Remove caches
```

## Architecture

```
devbench/
├── src/devbench/                  ← Installable package (pip install -e .)
│   ├── cli.py                     ← CLI entry point (devbench <command>)
│   ├── config.py                  ← Environment-driven configuration (all env vars)
│   ├── config_loader.py           ← YAML config parser and schema validator (parse/validate only — no env var access)
│   ├── config-schema.json         ← JSON schema for devbench.yaml validation
│   ├── constants.py               ← Centralized structural constants (regex, formats)
│   ├── log_setup.py               ← Dual logging (stdout + file)
│   ├── backlog/
│   │   ├── parser.py              ← Parses BACKLOG.md and work unit .md files
│   │   ├── work_unit.py           ← WorkUnit dataclass and status management
│   │   └── manager.py             ← Status sync, rollup, traceability
│   ├── github/
│   │   ├── git_ops.py             ← Commit, push, PR, merge, CI checks
│   │   └── security.py            ← GitHub security API integration
│   ├── utils/
│   │   ├── greeting.py            ← get_greeting(name): greeting utility for POC pipeline verification
│   │   └── process.py             ← run_command(): shared subprocess wrapper for running shell commands
│   ├── reporting/
│   │   └── report.py              ← Session progress report generator (velocity, ETA)
│   └── prompts/                   ← Prompt loader (reads agent prompt files)
├── plugin/                        ← Claude Code plugin (agents, hooks, skills)
│   └── devbench/
│       ├── agents/                ← Agent definitions invoked by orchestrate skill
│       │   ├── executor.md        ← Dev agent: implements work units via TDD
│       │   ├── review-supervisor.md ← Discovers and invokes all review_team agents in parallel
│       │   ├── security-reviewer.md ← Security review judge agent
│       │   ├── blocker-resolver.md  ← Dependency blocker assessment agent
│       │   └── review_team/       ← Review team agents invoked by review-supervisor
│       │       ├── code-reviewer.md   ← Code review judge agent
│       │       ├── test-reviewer.md   ← Test quality judge agent
│       │       ├── doc-reviewer.md    ← Documentation review judge agent
│       │       └── changes-manifest.md ← Scope/manifest review judge agent
│       ├── skills/
│       │   └── orchestrate/
│       │       └── SKILL.md       ← Orchestrate skill: main backlog execution loop
│       ├── hooks/
│       │   └── hooks.json         ← Hook registrations (PreToolUse, PostToolUse, …)
│       └── scripts/               ← Hook scripts invoked by hooks.json
│           ├── hook-logger.sh     ← Logs every tool call to the hook log
│           ├── guard-bash.sh      ← PreToolUse: blocks dangerous Bash commands
│           ├── guard-backlog.sh   ← PreToolUse: prevents direct writes to backlog/ tracking files (Bash)
│           ├── guard-verdict-format.sh ← PreToolUse: validates log-verdict argument format
│           ├── guard-git-stage.sh      ← PreToolUse: blocks git commit when no files are staged
│           ├── guard-work-unit-write.sh ← PreToolUse: blocks Write/Edit to work unit .md files under backlog/
│           └── assert-tests-pass.sh    ← PostToolUse: enforces test suite passes after Bash
├── tests/
│   ├── conftest.py                ← Shared fixtures
│   ├── testing.py                 ← Shared test utilities
│   ├── fixtures/                  ← Shared test fixture files
│   ├── functional/                ← Functional tests
│   ├── test_backlog/              ← parser, work_unit, manager tests
│   ├── test_execution/            ← execution path tests
│   ├── test_github/               ← git_ops, security tests
│   ├── test_judges/               ← judge-related tests
│   ├── test_plugin/               ← plugin integration tests
│   ├── test_prompts/              ← prompt loader tests
│   ├── test_reporting/            ← report tests
│   ├── test_utils/                ← process.run_command tests
│   └── unit/                      ← Unit tests for plugin hook scripts
│       ├── test_guard_verdict_format.py  ← Tests for guard-verdict-format.sh
│       ├── test_guard_git_stage.py       ← Tests for guard-git-stage.sh
│       ├── test_guard_work_unit_write.py ← Tests for guard-work-unit-write.sh
│       └── test_assert_tests_pass.py     ← Tests for assert-tests-pass.sh
├── scripts/
│   ├── start.sh                   ← Background start script
│   └── start-interactive.sh       ← Interactive Claude session start script
├── pyproject.toml                 ← Build config, dependencies, ruff/mypy settings
├── Makefile                       ← lint, format, check, test, validate, install
└── README.md                      ← This file
```

## Plugin Hooks

The `plugin/devbench/` directory is a Claude Code plugin that registers deterministic guards on Bash, Write, and Edit tool calls. Hooks are configured in `plugin/devbench/hooks/hooks.json`.

### PreToolUse hooks (Bash)

These hooks fire before every Bash tool call and can block execution by exiting with code 2:

| Script | Purpose |
|--------|---------|
| `hook-logger.sh` | Logs the tool call (tool name, command) to the hook log for audit |
| `guard-bash.sh` | Blocks Bash commands that are destructive or prohibited (e.g. `rm -rf /`) |
| `guard-verdict-format.sh` | Validates `uv run devbench log-verdict` calls: verdict must be `pass` or `fail`, judge name must be a known identifier, and feedback must be non-empty when verdict is `fail` |
| `guard-git-stage.sh` | Blocks `git commit` when no files are staged; runs `git diff --cached --quiet` and exits 2 with guidance to run `git add` if the index is empty |

### PreToolUse hooks (Write and Edit)

These hooks fire before every Write and Edit tool call and can block execution by exiting with code 2:

| Script | Purpose |
|--------|---------|
| `guard-work-unit-write.sh` | Blocks direct Write/Edit to work unit `.md` files under `backlog/` (excludes `BACKLOG.md` and files under `backlog/config/`); work unit files are managed exclusively by the orchestrate skill |

### PostToolUse hooks (Bash)

These hooks fire after every Bash tool call:

| Script | Purpose |
|--------|---------|
| `hook-logger.sh` | Logs the tool result |
| `assert-tests-pass.sh` | Validates that explicit test-runner commands (`pytest`, `make test`, `make test-unit`, `make test-functional`, `make validate`, `uv run pytest`) succeed; blocks if a test command exits with non-zero |

### Hook exit codes

- **Exit 0** — allow the tool call to proceed (or acknowledge post-use)
- **Exit 2** — block the tool call; stderr message is shown to the agent as feedback

## Configuration

Required variables (`JUDGE_WORKSPACE_ROOT`, `JUDGE_CLAUDE_MODEL`) raise `RuntimeError` at startup if unset. Allowed repositories and per-repo settings (default branch, checkout directory) are defined in `backlog/config/devbench.yaml` (relative to `JUDGE_WORKSPACE_ROOT`). See the [Configuration model](docs/architecture.md#8-configuration-model) section of the architecture doc for the full annotated YAML and value-resolution precedence.

The `--config <path>` CLI flag (or `JUDGE_CONFIG_PATH` env var) overrides the default config file location.

### Single-branch mode

By default, DevBench creates a separate branch per work unit and merges each independently. For projects that need all changes on one branch with one PR at the end, enable single-branch mode:

```yaml
# backlog/config/devbench.yaml
git_ops:
  single_branch: feat/my-feature
  defer_pr: true
```

In this mode:
- `ensure-branch` creates/checks out `feat/my-feature` for every task (instead of `backlog/<id>`)
- `git-ops` commits locally only (no push, no PR, no merge) -- one commit per task
- After all work units are done, run `devbench git-ops-finalize <repo>` to push the branch and create the PR

This produces a single branch with one commit per completed task, resulting in one PR for review.

`defer_pr` requires `single_branch` to be set (raises an error otherwise).

### Token cost estimates

The `report` command shows token consumption and estimated cost. Pricing is configurable per model — see [docs/model-pricing.md](docs/model-pricing.md) for the published rates of every Claude 4.x model and the YAML snippet to drop in for your specific model:

```yaml
report:
  token_cost_per_million_input: 5.0     # Opus 4.7 (default constant: 15.0)
  token_cost_per_million_output: 25.0   # Opus 4.7 (default constant: 75.0)
  token_cost_input_ratio: 0.80          # default: 0.80 (assumed input/output mix)
```

> The current code defaults (`15.0` / `75.0`) reflect Opus 4.1 pricing. If you are running Opus 4.7 or newer, set the values above explicitly to avoid overstating cost by ~3x. See the [model pricing doc](docs/model-pricing.md) for the full rate table.

Token data is read from `hook-logs.jsonl` in the workspace root (written by Claude Code hooks). Cost is a blended estimate based on the input/output ratio — actual billing depends on your account terms and active prompt caching.

### Stop hook (circuit breaker)

The orchestrator registers a Claude Code Stop hook (`continue-orchestration.sh`) that prevents the agent from stopping mid-loop when a task is in-progress. After context compaction, Claude may attempt to stop; the hook blocks the stop and injects the current task ID, file path, last action, and a specific next step so the agent can resume.

A circuit breaker prevents infinite stop-block loops: after `max_blocks` blocks within `window_seconds`, the hook allows the stop and logs a `[CIRCUIT_BREAKER]` comment to the work unit.

```yaml
stop_hook:
  max_blocks: 5              # default: 5
  window_seconds: 180        # default: 180
  stale_task_minutes: 120    # default: 120
```

Environment variable overrides: `JUDGE_STOP_MAX_BLOCKS`, `JUDGE_STOP_WINDOW_SECONDS`, `JUDGE_STOP_STALE_MINUTES`.

## Workspace Setup

### Recommended: keep the backlog in its own git repo

The backlog (`BACKLOG.md`, `backlog/`, specs) should live in a dedicated local git repo, separate from the target repositories DevBench modifies. This lets you track backlog progress with commits without mixing backlog changes into the target repos.

```
/workspaces/my-project/
  my-backlog/              <-- JUDGE_WORKSPACE_ROOT (its own git repo)
    BACKLOG.md
    backlog/
      config/devbench.yaml
      E0/...
    specs/
  target-repo/             <-- the repo DevBench modifies (separate git repo)
```

Set `JUDGE_WORKSPACE_ROOT` to the backlog repo directory. Then create symlinks inside it pointing to your target repos, and reference them as relative paths in `backlog/config/devbench.yaml`:

```bash
# Create symlinks to target repos inside the backlog repo
ln -s /workspaces/my-project/target-repo /workspaces/my-project/my-backlog/target-repo
```

```yaml
# backlog/config/devbench.yaml
repos:
  org/target-repo:
    default_branch: main
    checkout_directory: target-repo    # relative -- resolves via symlink
```

The `checkout_directory` must be a relative path (devbench rejects absolute paths and `..` traversal). Symlinks bridge the gap between the backlog repo and the target repos cleanly.

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

### Quick start

```bash
# Shell 1: start interactive session
cd /path/to/devbench && \
  JUDGE_WORKSPACE_ROOT=/path/to/my-backlog \
  JUDGE_CLAUDE_MODEL=claude-opus-4-6 \
  claude --plugin-dir plugin/devbench

# Shell 2: watch progress
cd /path/to/devbench && watch -n 30 \
  'JUDGE_WORKSPACE_ROOT=/path/to/my-backlog \
   JUDGE_CLAUDE_MODEL=claude-opus-4-6 \
   uv run devbench status'
```

In the interactive session, set your model with `/model`, then type: `Run the devbench:orchestrate skill to process the backlog`

## Interactive Mode

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

Restarting picks up where you left off — `done` units are skipped, and `in-progress` units are resumed.

## Troubleshooting

### Backlog is out of sync with work unit files
Run `devbench validate-backlog` to check for missing files, status mismatches, orphaned files, invalid dependency references, and Status Summary table count mismatches. Fix reported errors before running the orchestrator — it runs this check automatically at startup and aborts if any errors are found.

### Judge keeps failing the same unit
After `max_executor_retries` failures (default: 10, configurable in `devbench.yaml` or via `JUDGE_MAX_RETRIES` env var), the unit is marked `blocked`. Check the Comments section of the work unit file for the feedback trail.

### `mark-done` fails with "not all required judges passed"
The done-gate check found that not all four review judges (`code_review`, `test_review`, `doc_review`, `changes_manifest`) have a `[REVIEW_PASS]` entry after the most recent `[REVIEW_REJECTED]` line. Check the Comments section of the work unit file for the current judge verdicts, then re-run any failing agents via the orchestrate skill.

### Judge contradicts its previous feedback
This should not happen with the prior feedback injection. If it does, check whether the orchestrator log has the previous feedback entries (`grep "judge feedback for <unit-id>" src/devbench/logs/orchestrator.log`).

### GitHub token expired
```bash
unset GH_TOKEN
gh auth refresh -h github.com -s repo -s workflow -s read:org -s admin:repo_hook -s security_events
export GH_TOKEN="$(gh auth token)"
```

### Want to re-process a completed unit
Edit the work unit `.md` file, change `## Status: done` to `## Status: in-queue`, and update `BACKLOG.md` accordingly.
