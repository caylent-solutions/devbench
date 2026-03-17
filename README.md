# DevBench — Autonomous Backlog Execution

An LLM-as-Judge orchestration system that processes a backlog of work units autonomously. Development agents write code; judge agents review it. All review decisions are made by Claude LLM evaluation — no hardcoded pass/fail rules.

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

Runs the orchestrator in the background. Monitor with `tail -f /tmp/backlog-run.log`.

### Pre-configured token (skip OAuth)

If `GH_TOKEN` is already set, both scripts skip the `gh auth` flow entirely:

```bash
export GH_TOKEN="ghp_your_token_here"
make start-interactive
```

### Both modes:
1. Authenticate with GitHub (or skip if `GH_TOKEN` is already set)
2. Grant required token scopes (repo, workflow, read:org, admin:repo_hook, security_events)
3. Start a background token refresher (every 4 hours, skipped if using pre-set token)
4. Launch the orchestrator

### LLM Authentication (no API key required)

The LLM judge layer uses your existing Claude Code OAuth credentials — no separate Anthropic API key needed. Just be logged into Claude Code (`claude` in terminal). See [docs/llm-authentication.md](docs/llm-authentication.md) for details.

Alternatively, set `JUDGE_USE_BEDROCK=1` to use AWS Bedrock for LLM calls instead of the Anthropic API directly.

## How It Works

See [docs/execution-modes.md](docs/execution-modes.md) for a full description of both execution modes, the step-by-step lifecycle, and ownership rules.

```
Orchestrator (execution/orchestrator.py / interactive Claude session)
  │
  ├── Pre-flight: validate-backlog — abort if index/files are out of sync
  ├── Parse BACKLOG.md → find next actionable work unit
  ├── Implement work unit via TDD (RED → GREEN → REFACTOR)
  ├── Run repo's task runners (make test, make validate)
  ├── Stage files and submit to judge review
  │     ├── code_review       — SOLID, DRY, fail-fast, security, 12-factor
  │     ├── test_review        — TDD discipline, test quality, assertions
  │     ├── doc_review         — accuracy, completeness, sync with code
  │     ├── changes_manifest   — actual changes vs. expected manifest
  │     └── security_review    — CodeQL, Dependabot, secret scanning alerts
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

| Command | Arguments | Description |
|---------|-----------|-------------|
| `status` | `[--detail]` | Show backlog summary (counts by status). With `--detail`: also lists in-queue Tasks in priority order and blocked Tasks with their unresolved dependency IDs. |
| `next` | `[--claim]` | Print next actionable work unit as JSON (read-only). With `--claim`: also sets unit status to `in-progress`. |
| `execute` | `<unit-id> [feedback]` | Spawn dev agent for a work unit |
| `review` | `<unit-id>` | Run all review judges, print JSON results |
| `security-review` | `<unit-id>` | Run security review judge |
| `set-status` | `<unit-id> <status>` | Force any status (no gate — use for recovery/lifecycle transitions) |
| `mark-done` | `<unit-id>` | Mark unit as Done (enforces done-gate: all judges must have passed) |
| `validate-backlog` | — | Check backlog integrity (file existence, status sync, orphans, deps) |
| `sync-blocked` | — | Scan in-queue units and mark those with unmet deps as blocked |
| `report` | `[since-timestamp]` | Print progress report with velocity stats |
| `log` | `<message>` | Append message to log file |

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
│   ├── constants.py               ← Centralized structural constants (regex, formats)
│   ├── log_setup.py               ← Dual logging (stdout + file)
│   ├── backlog/
│   │   ├── parser.py              ← Parses BACKLOG.md and work unit .md files
│   │   ├── work_unit.py           ← WorkUnit dataclass and status management
│   │   └── manager.py             ← Status sync, rollup, traceability
│   ├── execution/
│   │   ├── orchestrator.py        ← Main loop: parse backlog, dispatch, review, merge
│   │   └── executor.py            ← Spawns Claude Code dev agents
│   ├── github/
│   │   ├── git_ops.py             ← Commit, push, PR, merge, CI checks
│   │   └── security.py            ← GitHub security API integration
│   ├── judges/
│   │   ├── base.py                ← BaseJudge: LLM calls, prior feedback injection
│   │   ├── code_review.py         ← Git diff + work unit → LLM verdict
│   │   ├── test_review.py         ← make test / pytest + test files → LLM verdict
│   │   ├── doc_review.py          ← Doc diff + work unit → LLM verdict
│   │   ├── changes_manifest.py    ← Changed files vs. manifest → LLM verdict
│   │   ├── security_review.py     ← GitHub alerts + diff → LLM verdict
│   │   └── blocker_resolver.py    ← Dependency and blocker assessment
│   ├── utils/
│   │   └── process.py             ← run_command(): shared subprocess wrapper for running shell commands
│   ├── reporting/
│   │   └── report.py              ← Session progress report generator (velocity, ETA)
│   └── prompts/                   ← Prompt loader (reads from top-level prompts/)
├── prompts/                       ← External prompt files for each judge
│   ├── code_review.txt            ← 46 review rules (SOLID, DRY, 12-factor, security)
│   ├── test_review.txt            ← 42 test quality rules (TDD, stubs, markers)
│   ├── doc_review.txt             ← Documentation accuracy and completeness
│   ├── changes_manifest.txt       ← Scope control and manifest verification
│   ├── security_review.txt        ← Security alert evaluation
│   ├── blocker_resolver.txt       ← Dependency and blocker assessment
│   └── executor.txt               ← Dev agent execution prompt
├── tests/
│   ├── conftest.py                ← Shared fixtures
│   ├── testing.py                 ← Shared test utilities
│   ├── test_backlog/              ← parser, work_unit, manager tests
│   ├── test_execution/            ← executor, orchestrator tests
│   ├── test_github/               ← git_ops, security tests
│   ├── test_judges/               ← base, code_review, test_review, … tests
│   ├── test_reporting/            ← report tests
│   └── test_utils/                ← process.run_command tests
├── scripts/
│   ├── start.sh                   ← Background start script
│   └── start-interactive.sh       ← Interactive Claude session start script
├── pyproject.toml                 ← Build config, dependencies, ruff/mypy settings
├── Makefile                       ← lint, format, check, test, validate, install
└── README.md                      ← This file
```

## Configuration

Required variables (`JUDGE_WORKSPACE_ROOT`, `JUDGE_CLAUDE_MODEL`) raise `RuntimeError` at startup if unset. Allowed repositories and per-repo settings (default branch, checkout directory) are defined in `backlog/config/devbench.yaml` (relative to `JUDGE_WORKSPACE_ROOT`) — copy `sample-config.yaml` as a starting point. See [SYSTEM-OVERVIEW.md](SYSTEM-OVERVIEW.md#environment-variables) for the full variable reference and YAML schema.

The `--config <path>` CLI flag (or `JUDGE_CONFIG_PATH` env var) overrides the default config file location.

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
Run `devbench validate-backlog` to check for missing files, status mismatches, orphaned files, and invalid dependency references. Fix reported errors before running the orchestrator — it runs this check automatically at startup and aborts if any errors are found.

### Judge keeps failing the same unit
After `JUDGE_MAX_RETRIES` failures (default: 10), the unit is marked `blocked`. Check the Comments section of the work unit file for the feedback trail.

### `mark-done` fails with "not all required judges passed"
The done-gate check found that not all four review judges (`code_review`, `test_review`, `doc_review`, `changes_manifest`) have a `[REVIEW_PASS]` entry after the most recent `[REVIEW_REJECTED]` line. Run `devbench review <unit-id>` to get the current judge verdicts.

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
