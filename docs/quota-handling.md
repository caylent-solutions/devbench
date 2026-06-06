# Quota Wait-and-Resume

DevBench can detect Anthropic subscription rate-limit errors and pause the
orchestrate loop automatically, waiting for the quota window to reset rather
than exiting with a non-zero code. This behaviour is controlled by the
`quota_handling` block in `devbench.yaml`.

## How it works

When the Claude Agent SDK emits a message that `detect_quota_error` classifies
as a quota-exhaustion signal (HTTP 429, CLI "You've hit your limit" text, or
the equivalent Bedrock throttle codes), `cmd_start` raises a `_QuotaDetected`
sentinel from the inner SDK message loop. The outer handler:

1. Saves a `QuotaCheckpoint` to `<workspace>/.devbench/quota_pause.json` so
   a SIGTERM during the wait does not lose the pause state.
2. Emits a structured audit marker to the orchestrator log:
   `[QUOTA_WAITING] reason=<source> reset_at=<ISO|unknown>`.
3. Calls `wait_for_reset`, which sleeps until `reset_at` (if known) then
   polls with jittered exponential backoff until a probe confirms recovery.
4. On recovery, emits `[QUOTA_RESUMED] waited_seconds=<N>` and applies the
   configured `resume_strategy` before returning `rc=0`.

No `asyncio.shield` is used -- a SIGTERM during the wait propagates naturally,
allowing the SIGTERM handler to force-block the in-flight work unit and exit
cleanly (the checkpoint on disk allows an operator to inspect the pause state
after restart).

## Configuration

Add the `quota_handling` block to `backlog/config/devbench.yaml`. All fields
are optional -- the defaults shown here implement the policy recommended by
D-Q-1 (enabled by default, wait on exhaustion, drain on timeout):

```yaml
quota_handling:
  enabled: true                     # false restores legacy non-zero exit
  on_exhaustion: wait               # wait | fail | drain
  poll_interval_seconds: 60         # 30-3600
  max_wait_seconds: 18000           # 1+; 18000 = 5 hours
  on_exhaustion_timeout: drain      # drain | fail | keep_waiting
  resume_strategy: continue_current_wu  # continue_current_wu | restart_wu | drain_and_resume
  audit_comment_on_wait: true
  audit_comment_on_resume: true
  log_structured_events: true
```

### Field reference

| Field | Default | Description |
|-------|---------|-------------|
| `enabled` | `true` | When `false`, quota errors propagate as-is (legacy non-zero exit, no checkpoint written). |
| `on_exhaustion` | `wait` | Action taken when a quota error is detected: `wait` pauses and polls; `fail` re-raises immediately; `drain` triggers a graceful drain. |
| `poll_interval_seconds` | `60` | Base cadence for the recovery probe loop. Must be 30-3600. |
| `max_wait_seconds` | `18000` | Maximum total wait time in seconds (5 hours by default). Must be >= 1. |
| `on_exhaustion_timeout` | `drain` | Action when `max_wait_seconds` is exceeded: `drain` triggers a graceful drain; `fail` re-raises the quota error; `keep_waiting` ignores the cap. |
| `resume_strategy` | `continue_current_wu` | How to re-enter the orchestrate loop after recovery. |
| `audit_comment_on_wait` | `true` | When `true`, a `[QUOTA_WAITING]` audit comment is written to the active work unit. |
| `audit_comment_on_resume` | `true` | When `true`, a `[QUOTA_RESUMED]` audit comment is written to the active work unit. |
| `log_structured_events` | `true` | When `true`, JSON-structured events are emitted alongside text markers. |

### `resume_strategy` values

| Value | Behaviour |
|-------|-----------|
| `continue_current_wu` | The orchestrate loop continues where it left off. The active work unit's state is unchanged. |
| `restart_wu` | The current work unit is force-transitioned back to `in-queue` so the next loop picks it up fresh. |
| `drain_and_resume` | A drain request is written so the Makefile restart loop re-invokes `devbench start`. |

## Audit markers

The orchestrator log and the active work unit's Comments section receive the
following structured markers:

| Marker | Format | Emitted when |
|--------|--------|-------------|
| `[QUOTA_WAITING]` | `[QUOTA_WAITING] reason=<r> reset_at=<ISO|unknown>` | Pause begins |
| `[QUOTA_RESUMED]` | `[QUOTA_RESUMED] waited_seconds=<N>` | Recovery confirmed |

## Inspecting the checkpoint

Use `devbench quota-watcher --once` to print the current pause state:

```
$ devbench quota-watcher --once
[QUOTA_WAITING] reason=anthropic-api reset_at=2026-01-01T16:10:00+00:00 (saved 2026-01-01T10:05:00+00:00)
```

When no checkpoint is present, the command exits with a non-zero code and
prints a message indicating the orchestrator is not waiting.

## Disabling quota wait-and-resume

Set `enabled: false` to restore the pre-Issue-#236 behaviour (quota errors
propagate immediately, `devbench start` exits non-zero, no checkpoint is
written):

```yaml
quota_handling:
  enabled: false
```

## Related

- [ADR-24: Quota wait-and-resume](adr/24-quota-wait-and-resume.md)
