# Dummy backlog -- `devbench supervise` Phase-6 integration fixture

> Glyph note: this file uses ASCII double-hyphen `--` everywhere a dash is needed.
> The em-dash glyph (U+2014) does not appear in this file.

A deliberately TINY, TRIVIAL, NON-AWS throwaway backlog used by the
`devbench supervise` integration layer (Section 10.0 of
`spec/devbench-supervise-screen-orchestrator/devbench-supervise-screen-orchestrator.md`).

## Why it exists

The supervise spec defines two test layers that touch a backlog:

- The in-CI integration tests (`tests/test_integration/test_supervise_dummy_backlog_integration.py`)
  parse this backlog with the REAL `BacklogParser`, expand its scope, and drive
  `supervise start -> __run -> /orchestrate -> ALL_DONE` against the REAL `pexpect`
  supervisor and the REAL `stub-claude` executable (no real `claude`, no
  subscription, no tokens, no `screen`).
- The DEFERRED live ACs (AC-23 cold-start, AC-34 scope/env conveyance with a real
  `claude`) use this same backlog when a human runs them against a live
  subscription session (those ACs cannot run in CI).

## Contents

| Path | Purpose |
|---|---|
| `BACKLOG.md` | The work-unit index (two trivial tasks). |
| `backlog/E1-F1-S1-T1.md` | Append a greeting line to `NOTES.md` (no deps). |
| `backlog/E1-F1-S1-T2.md` | Append a farewell line to `NOTES.md` (depends on T1). |
| `backlog/config/devbench.yaml` | Minimal config: one repo, `orchestrate.model`, a `supervise:` block. |
| `NOTES.md` | The single local file the trivial tasks edit. |

## Isolation guarantee

Every work unit is a pure local docs edit: NO cloud, NO AWS, NO
terraform/terragrunt, NO network, NO build tooling. A live run of this backlog
only appends lines to one local text file, so it can never collide with any
other workload on the host (including a concurrent terraform/terragrunt sweep).

## Running the DEFERRED live ACs (human operator, real subscription)

These require a real Claude Code subscription login and a live `claude` + `screen`
(they do NOT run in CI):

```bash
# 1. Make this fixture the workspace (copy it somewhere writable first so the
#    run's .devbench/ state does not mutate the committed fixture).
cp -r tests/fixtures/supervise/dummy-backlog /tmp/supervise-dummy-ws

# 2. Ensure subscription auth is present and NO API key is exported.
unset ANTHROPIC_API_KEY ANTHROPIC_AUTH_TOKEN
claude --version           # confirms a live CLI is on PATH

# 3. Cold-start the supervised session against the dummy backlog (AC-23).
DEVBENCH_WORKSPACE_ROOT=/tmp/supervise-dummy-ws \
  uv run devbench supervise start --name dummy

# 4. AC-24 / AC-34: confirm the live claude session is subscription-billed and
#    carries the conveyance env (read the child's /proc/<pid>/environ):
uv run devbench supervise status --name dummy   # billing-channel: subscription
```
