# Live Activity Dashboard (`devbench watch`)

`devbench watch` prints a one-screen snapshot of the currently-active
orchestration. It answers three questions at once: *is the orchestrator
working right now? what is it doing? should I intervene or wait?*

The command is strictly read-only. It inspects files on disk -- the
orchestrator log, the Claude Code session transcripts, the hook log, the
target repo's git status, and any pending amendment request -- and never
signals, writes, or otherwise mutates devbench state. It is safe to run
concurrently with an active `/devbench:orchestrate` session.

## Usage

```bash
# One-shot snapshot; prints and exits.
devbench watch

# Live refresh every N seconds. Ctrl+C to stop.
devbench watch --watch 5

# Makefile shortcuts.
make watch                       # one-shot
make watch-live                  # refresh every 5s
make watch-live INTERVAL=2       # override refresh cadence
```

The default cadence balance is the same as `devbench report --watch`.
Match the interval to what you want to see: 2--3 seconds tracks tool
calls as they happen; 30 seconds or more is better when you just want
to know whether the orchestrator is alive.

## What the dashboard shows

```
DevBench activity -- 2026-04-18T03:05:12Z

Mode: single-branch + defer_pr  (branch: feat/embed-repo-tool)
Active task: E0-F9-S2-T4  'Run agent manual integration tests'
             claimed 39m ago -- In Progress

Phase: executor subagent active  (last activity 30s ago)

Latest agent thinking (most recent text from the active subagent):
  RS-05, KI-04, and BV-07 all sync a project with revision="~=1.0.0". They
  are all failing with "Cannot initialize work tree". This is clearly related
  to my changes. Let me check if _InitWorkTree() got broken by the
  _ResolveVersionConstraint refactor I added.

Recent tool calls (most recent 3):
  03:01:09  Bash   git stash pop && echo "Popped"
  03:00:59  Bash   git stash && echo "Stashed"
  03:00:56  Bash   python3 -c "<debug REPL>"

Recent devbench CLI calls (last 2):
  02:50:27  TDD RED logged for E0-F9-S2-T4  (6 failures, 40 passes)
  02:26:00  Claimed E0-F9-S2-T4 (set to in-progress)

Target repo state (kanon):
  M  src/kanon_cli/repo/project.py               (unstaged)
  ?? src/kanon_cli/repo/subcmds/version.py       (untracked)
  Staged count: 0  Unstaged: 1  Untracked: 1

Pending amendment request: no

Idle for 30s.  (Ctrl+C to stop; `devbench watch --watch N` for live tail.)
```

### Panel-by-panel

| Panel | What it tells you |
|-------|-------------------|
| `Mode` | Git-ops mode: `standard multi-PR`, `single-branch + defer_pr (branch: X)`, or future `multi-PR with pause-before-merge`. Answers "what does 'done' mean for this task?" |
| `Active task` | The one task currently `in-progress` (or, if none, the most recent `in-review` / `blocked`). `claimed Nm ago` tells you whether the task is freshly picked up or has been stuck. |
| `Phase` | Coarse state: `executor subagent active`, `review-supervisor running`, `security-reviewer running`, `git-ops running`, `blocker-resolver running`, `manifest-amender running`, `task-factory running`, or `idle`. `last activity Ns ago` separates alive-but-thinking from genuinely hung. |
| `Latest agent thinking` | Most recent `text` content from the active subagent transcript. Truncated to 500 characters with a continuation marker if longer. This is the single biggest signal of what the LLM is reasoning about at the moment it paused. |
| `Recent tool calls` | The last tool invocations by the active subagent. Shows working memory -- which files and commands the agent is touching. |
| `Recent devbench CLI calls` | Last `devbench.cli` log entries. Confirms whether the skill is making orchestration progress (claimed, TDD logged, verdict logged) rather than the subagent spinning inside itself. |
| `Target repo state` | `git status --porcelain=v1` inside the task's repo. Answers "has the executor committed to a diff yet, or is it still in debug mode?" |
| `Pending amendment request` | Whether `<workspace>/.devbench/amendments/<task-id>.json` exists. When `yes`, the amender is about to run. |
| `Idle for Ns` | Seconds since any log source produced new output. High N means probably stuck; low N means actively working. |

## Diagnosing common patterns

| Symptom | Interpretation |
|---------|----------------|
| `Phase: idle` + high `Idle for Ns` (minutes) | Orchestrator is between tasks or genuinely hung. Check `devbench status` for actionable work. |
| `Phase: executor subagent active` but no recent tool calls and `last activity > 60s` | LLM is reasoning. Normal for large context windows; wait a minute before intervening. |
| `Pending amendment request: yes` + `Phase: manifest-amender running` | The amender is reviewing a TDD GREEN production-fix request. Outcome lands as a verdict on the task in the next few seconds. |
| Repo has staged files but `Phase: review-supervisor running` | Executor finished, judges are running. Normal pre-merge state. |
| `Active task: (none)` + `Phase: idle` | No task is currently claimed. Either the backlog is done or `devbench next` returned `NO_ACTIONABLE`. |

## What the dashboard intentionally omits

- **Full JSONL dumps.** Use `tail -f <workspace>/hook-logs.jsonl` or the subagent transcript for raw bytes.
- **Every PreToolUse / PostToolUse pair.** One row per tool call is enough; the pairs are implicit.
- **Historical analytics.** Use `devbench report` for cross-session velocity, cost, and cache stats.
- **Full judge verdicts.** When review-supervisor runs, the phase label and the recent CLI panel show progress; open the work-unit `.md` for full verdict evidence.
- **Token / cost metrics.** Covered by `devbench report`.

## Safety guarantees

- No subprocess call mutates anything. Every `git` invocation is `git -C <repo> status --porcelain=v1` or `git -C <repo> rev-parse HEAD`.
- Every file is opened read-only.
- Every log parse tolerates malformed lines (partial flushes are invisible).
- A file-read timeout derived from `RUNTIME_CONFIG.timeouts.command` bounds every git call.

## Related files

- Source: `src/devbench/activity.py` (module), `src/devbench/cli.py::cmd_watch`.
- Tests: `tests/test_activity.py`, `tests/test_integration/test_watch_against_live_log.py`.
- ADR: [ADR-04: Live activity dashboard](adr/04-watch-dashboard.md).
