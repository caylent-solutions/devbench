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
3. Calls `wait_for_reset`, which sleeps until `reset_at` (if known). A known,
   elapsed `reset_at` is the **authoritative** readiness signal: the wait
   resumes the moment it passes, **without** consulting the recovery probe
   (TDI-003a). The probe is **best-effort** and only consulted while `reset_at`
   is unknown -- it issues a direct Anthropic **API** call, which tests a
   different auth channel than the Claude Code CLI/SDK **subscription** channel
   the orchestrator runs on, so a probe success does not prove the exhausted
   subscription quota cleared (and on subscription auth the probe can never
   succeed). When `reset_at` is unknown the loop polls the probe with jittered
   exponential backoff; if the probe is permanently unavailable (no/invalid API
   credential) it emits `[QUOTA_PROBE_UNAVAILABLE]` and stops fast rather than
   polling for the full `max_wait_seconds`. While polling, `wait_for_reset`
   emits a `[QUOTA_POLLING] elapsed=<s> probe=<n> next_in=<s>` heartbeat once
   per poll so a long wait is visibly alive in the log rather than looking dead
   between `[QUOTA_WAITING]` and `[QUOTA_RESUMED]`.
4. On recovery, emits `[QUOTA_RESUMED] waited_seconds=<N>` and applies the
   configured `resume_strategy` before returning `rc=0`.

The wait-start (`[QUOTA_WAITING]`) and recovery (`[QUOTA_RESUMED]`) points also
fire the opt-in `quota_waiting` / `quota_resumed` Slack notification events when
configured (best-effort -- a notify failure never breaks or delays the wait or
resume). See `docs/slack-notifications.md`.

Text detection is deliberately channel-specific so arbitrary tool output never
trips a false pause:

- **Tool-result / result content** (a sub-agent `ToolResultBlock`, or a
  `ResultMessage.result`) is matched **only** when the result is an explicit
  error (`is_error is True`) **and** the content contains a **verbatim** CLI
  limit line (e.g. "You've hit your limit"). The broad "rate limit + verb"
  regex is **not** applied to tool content. Rationale: tool content is
  arbitrary data the agent read or grepped -- including devbench's own source
  (e.g. `amendment.py` emits the literal "Amendment rate limit exceeded: ..."
  and `quota.py`/its tests contain "You've hit your limit" and "rate limit"
  strings). A **successful** Read/Grep/Glob result carries `is_error=None` and
  a successful Bash result carries `is_error=False`; neither is scanned, so a
  passing tool whose output mentions a limit phrase cannot trip a pause. A
  genuine sub-agent subscription limit surfaces as an *error* result and is
  still caught.
- **Exception messages** (`str(exc)`) are matched with the full set: the
  verbatim CLI lines OR "rate limit" immediately followed by an exhaustion verb
  ("exceeded", "reached", "exhausted", ...). An exception message is an
  authoritative signal, not arbitrary content, so the broad regex is retained
  there.
- **Structured signals** (HTTP 429/402, `error == 'rate_limit'`, Bedrock
  throttle codes) are matched directly and are unaffected by the text rules.

Benign sub-agent prose that merely mentions rate limiting (for example a
reviewer noting "API endpoints implement rate limiting") is never misclassified
as a limit.

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
| `on_exhaustion_timeout` | `drain` | Action when `max_wait_seconds` is exceeded **or** the recovery probe is permanently unavailable: `drain` requests a graceful drain (the signal survives process exit); `fail` re-raises the quota error (non-zero exit); `keep_waiting` exits cleanly (rc 0) **without** draining or failing — it does NOT block the process indefinitely. With `keep_waiting`, the `make start` restart loop re-invokes `devbench start`, which re-detects the quota signal and re-enters the wait; run directly (not via `make start`) it simply stops. |
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
| `[QUOTA_POLLING]` | `[QUOTA_POLLING] elapsed=<s> probe=<n> next_in=<s>` | One heartbeat per recovery-probe poll while waiting (visible liveness) |
| `[QUOTA_RESUMED]` | `[QUOTA_RESUMED] waited_seconds=<N>` | Recovery confirmed |
| `[QUOTA_PROBE_UNAVAILABLE]` | `[QUOTA_PROBE_UNAVAILABLE] reason=<r> detail=<msg>` | Probe cannot run (no/invalid credential) and no reset time is known; routed through `on_exhaustion_timeout` |
| `[QUOTA_FAIL_FAST]` | `[QUOTA_FAIL_FAST] reason=<source>` | `on_exhaustion=fail` (detection) or `on_exhaustion_timeout=fail` (timeout) aborts with a non-zero exit |
| `[QUOTA_DRAIN_REQUESTED]` | `[QUOTA_DRAIN_REQUESTED] reason=<source> phase=<detection\|timeout>` | `on_exhaustion=drain` (detection) or `on_exhaustion_timeout=drain` (timeout) requests a graceful drain |
| `[QUOTA_TIMEOUT_KEEP_WAITING]` | `[QUOTA_TIMEOUT_KEEP_WAITING] reason=<source>` | `on_exhaustion_timeout=keep_waiting` exits cleanly after the wait cap (restart loop re-enters) |

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
