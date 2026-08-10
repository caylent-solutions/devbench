# Quota Wait-and-Resume

DevBench detects Anthropic subscription rate-limit errors and pauses the
orchestrate loop automatically, waiting for the quota window to reset rather
than exiting with a non-zero code. This behaviour is controlled by the
`quota_handling` block in `devbench.yaml` (ADR-24).

## Pause lifecycle

1. **Detection.** The inner SDK message loop in `cmd_start._run` calls
   `detect_quota_error` on every message from the Claude Agent SDK. A quota
   / rate-limit signal (HTTP 429, a verbatim CLI "You've hit your limit"
   line, an `AssistantMessage.error == "rate_limit"` field, or a Bedrock
   throttle code) raises a `_QuotaDetected` sentinel -- a `BaseException`
   subclass so it crosses the `asyncio.run` boundary without being caught by
   an intervening `except Exception` handler (decision D-4).
2. **Checkpoint.** Before anything else, `_handle_quota_pause` writes a
   `QuotaCheckpoint` to `<workspace>/.devbench/quota_pause.json` (decision
   D-6/D-9: this durability, not `asyncio.shield`, is what survives a
   SIGTERM mid-wait -- see "No shield" below).
3. **Wait.** `[QUOTA_WAITING]` is logged and (when configured)
   audit-commented onto the active work unit, then `wait_for_reset` polls
   for recovery. See "Markers" below for the full sequence, including the
   `[QUOTA_POLLING]` heartbeat that keeps a long wait visibly alive in the
   log.
4. **Recovery or timeout.** On recovery, `[QUOTA_RESUMED]` is logged, the
   checkpoint is removed, and the configured `resume_strategy` runs. On
   timeout (or a permanently unavailable recovery probe), the configured
   `on_exhaustion_timeout` disposition runs instead.
5. **In-process resume.** When the wait recovered, `cmd_start` does not
   exit -- it opens a brand-new Claude Agent SDK session on the remaining
   backlog (decision D-6: no SDK conversation resume; all continuity flows
   through the on-disk backlog, never through session state) and logs
   `[ORCHESTRATOR_QUOTA_RESUME] resume=<n> max=<cap>`. This is bounded by a
   fail-safe cap so an unattended overnight run can survive multiple quota
   windows without operator action -- see "Resume cap" below.

## No shield

`wait_for_reset` never wraps its wait in `asyncio.shield`. A SIGTERM
delivered mid-wait propagates naturally: the registered SIGTERM handler
force-transitions the in-flight work unit to `blocked` (with a
`[FORCED_BLOCKED_ON_STOP]` audit line) and the process exits promptly,
instead of finishing out the wait first. The checkpoint written in step 2
above is what makes this safe -- a restarted orchestrator (or an operator
running `devbench quota-watcher`) can see the pause was in progress even
though the process that owned it is gone. See ADR-24's "Alternatives
considered > asyncio.shield" for the full rationale.

## Markers

The first seven markers below (`[QUOTA_WAITING]` through
`[QUOTA_TIMEOUT_KEEP_WAITING]`) are the **structured markers** gated by
`quota_handling.log_structured_events`: with the flag at its default `true`
they log exactly as documented here; with `log_structured_events: false` none
of them is written to the log, and the "Gated?" column below records this.
Nothing else changes -- the wait timing, the recovery decision, and every
side effect other than the log line proceed identically either way.

Explicitly **excluded** from the gate (decision D-10, never suppressed by
`log_structured_events`): the Slack notifications fired on the wait/resume
transitions (their own `notifications.events.*` toggles), the
`[QUOTA_WAITING]` / `[QUOTA_RESUMED]` audit comments appended to the active
work unit (`audit_comment_on_wait` / `audit_comment_on_resume`), the on-disk
`quota_pause.json` checkpoint, and the two `[ORCHESTRATOR_QUOTA_*]` markers
at the bottom of the table -- those belong to the in-process resume loop, a
different marker family from the seven `[QUOTA_*]` structured markers this
flag controls.

| Marker | Format | Emitted when | Gated by `log_structured_events`? |
|--------|--------|-------------|:--:|
| `[QUOTA_WAITING]` | `[QUOTA_WAITING] reason=<r> reset_at=<ISO\|unknown>` | The pause begins, immediately after the checkpoint is written. | Yes |
| `[QUOTA_POLLING]` | `[QUOTA_POLLING] elapsed=<s> probe=<n> next_in=<s>` | Once per poll interval on every waiting path -- both the provider-stated `reset_at` wait and the recovery-probe loop -- so a long wait is visibly alive in the log. Best-effort: a logging failure can never break or delay the wait. | Yes |
| `[QUOTA_RESUMED]` | `[QUOTA_RESUMED] waited_seconds=<N>` | Recovery is confirmed (a known `reset_at` has elapsed, or the recovery probe succeeded). | Yes |
| `[QUOTA_PROBE_UNAVAILABLE]` | `[QUOTA_PROBE_UNAVAILABLE] reason=<r> detail=<msg>` | The recovery probe cannot run (no or invalid API credential) and no usable `reset_at` is known; the wait stops fast rather than polling for the full `max_wait_seconds`. | Yes |
| `[QUOTA_FAIL_FAST]` | `[QUOTA_FAIL_FAST] reason=<r>` | `on_exhaustion=fail` (at detection) or `on_exhaustion_timeout=fail` (at timeout) re-raises the quota error for a non-zero exit. | Yes |
| `[QUOTA_DRAIN_REQUESTED]` | `[QUOTA_DRAIN_REQUESTED] reason=<r> phase=<detection\|timeout>` | `on_exhaustion=drain` (at detection) or `on_exhaustion_timeout=drain` (at timeout) requests a graceful drain instead of waiting or resuming. | Yes |
| `[QUOTA_TIMEOUT_KEEP_WAITING]` | `[QUOTA_TIMEOUT_KEEP_WAITING] reason=<r>` | `on_exhaustion_timeout=keep_waiting` exits cleanly (rc 0) after the wait cap elapses, without draining or failing. | Yes |
| `[ORCHESTRATOR_QUOTA_RESUME]` | `[ORCHESTRATOR_QUOTA_RESUME] resume=<n> max=<cap>` | A recovered wait is followed by a fresh in-process SDK session, within the resume cap. | No -- always logged |
| `[ORCHESTRATOR_QUOTA_RESUMES_EXHAUSTED]` | `[ORCHESTRATOR_QUOTA_RESUMES_EXHAUSTED] max=<cap>` | A recovered wait's resume cap has already been used up for this `cmd_start` invocation; the run stops instead of resuming again. | No -- always logged |

## Resume cap

Unattended overnight runs can hit more than one quota window in a row.
Rather than cap the number of quota waits, DevBench bounds the number of
consecutive in-process SDK-session resumes: `DEVBENCH_MAX_QUOTA_RESUMES`
(default 1000, resolved by `_resolve_max_quota_resumes`). A missing, empty,
non-integer, or non-positive value falls back to the default rather than
disabling the cap or the resume loop -- a typo can never silently turn a
single quota window back into a run-ending event (fail-safe, not
fail-open). When the cap is reached, `[ORCHESTRATOR_QUOTA_RESUMES_EXHAUSTED]
max=<cap>` is logged and the run stops with the normal terminal
classification instead of resuming again.

## Drain preservation across a pause

When the configured disposition requests a drain (`on_exhaustion=drain` at
detection, or `on_exhaustion_timeout=drain` at timeout -- the default), the
drain signal must survive `cmd_start`'s own exit-path cleanup, which would
otherwise cancel any pending drain unconditionally. `cmd_start` routes both
of its exit-path drain cleanups through `_cancel_drain_unless_requested`
instead, so a drain the quota disposition deliberately asked for is still
in effect the next time `devbench start` runs (or a peer session acts on
it).

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
| `poll_interval_seconds` | `60` | Base cadence for the recovery-probe loop. Must be 30-3600. |
| `max_wait_seconds` | `18000` | Maximum total wait time in seconds (5 hours by default). Must be >= 1. |
| `on_exhaustion_timeout` | `drain` | Action when `max_wait_seconds` is exceeded or the recovery probe is permanently unavailable: `drain` requests a graceful drain that survives process exit; `fail` re-raises the quota error (non-zero exit); `keep_waiting` exits cleanly (rc 0) without draining or failing -- it does NOT block the process indefinitely. |
| `resume_strategy` | `continue_current_wu` | How the in-process resume re-enters the orchestrate loop after recovery -- see the table below. |
| `audit_comment_on_wait` | `true` | When `true`, a `[QUOTA_WAITING]` audit comment is written to the active work unit. |
| `audit_comment_on_resume` | `true` | When `true`, a `[QUOTA_RESUMED]` audit comment is written to the active work unit. |
| `log_structured_events` | `true` | When `true`, the seven structured `[QUOTA_*]` markers in the table above are logged (`[QUOTA_WAITING]`, `[QUOTA_POLLING]`, `[QUOTA_RESUMED]`, `[QUOTA_PROBE_UNAVAILABLE]`, `[QUOTA_FAIL_FAST]`, `[QUOTA_DRAIN_REQUESTED]`, `[QUOTA_TIMEOUT_KEEP_WAITING]`). When `false`, none of the seven is written to the log -- everything else (Slack notifications, the `audit_comment_on_wait`/`audit_comment_on_resume` comments, the on-disk checkpoint, and the `[ORCHESTRATOR_QUOTA_*]` markers) is unaffected. |

### `resume_strategy` values

| Value | Behaviour |
|-------|-----------|
| `continue_current_wu` | The in-process resume loop opens a fresh SDK session and continues where the backlog left off. The active work unit's status is unchanged. |
| `restart_wu` | Every `in-progress` work unit is force-transitioned back to `in-queue` before the fresh session starts, so the next orchestrator pass re-claims it from a clean state. |
| `drain_and_resume` | A drain is requested instead of resuming automatically; the run stops and must be restarted manually (or acted on by a peer session), since the Makefile auto-restart loop (`Makefile:117-123`) only fires on exit code 42, which a graceful drain does not produce. |

## Operator procedures

### Interrupting a paused orchestrator safely (journey J-2)

`devbench stop --session <name>` (or a direct `kill -TERM <pid>`) is safe to
run at any point during a quota wait, including mid-sleep. Because the wait
uses no `asyncio.shield`, the SIGTERM is handled immediately: the in-flight
work unit is force-transitioned to `blocked` with a
`[FORCED_BLOCKED_ON_STOP]` audit line, and the process exits. The quota
checkpoint on disk is unaffected by the interrupt -- a subsequent
`devbench quota-watcher` still reports the pause that was in progress, so
the operator knows a known state (not an ambiguous one) was left behind.
The interrupted work unit itself is left `blocked` and is **not**
auto-reclaimed: `BacklogParser.get_parallel_candidates` only treats
`in-queue` and `in-progress` units as actionable, so `blocked` is skipped.
Before `devbench start` will pick the unit back up, the operator must
first return it to `in-queue`, for example:

```
$ devbench set-status <unit-id> in-queue
$ devbench start
```

### Inspecting an in-progress pause (journey J-3)

Two commands report the same on-disk state without disturbing the running
orchestrator:

```
$ devbench quota-watcher
[QUOTA_WAITING] reason=anthropic-api reset_at=2026-01-01T16:10:00+00:00
```

When no checkpoint is present, `quota-watcher` exits with rc 1 and prints
nothing to stdout or stderr -- the absence of output is itself the "not
waiting" signal an operator or script should check the exit code for.

```
$ devbench status
...
Active work units:
  [In Progress] E1-F1-S1-T1 -- Example task (in-progress for 0:42:11)
```

The paused work unit's status is never changed by the wait itself (only a
SIGTERM force-blocks it), so it continues to appear in the "Active work
units" panel for the entire pause window -- this is what makes the pause
visible from a plain `devbench status` call without needing
`quota-watcher`.

## Disabling quota wait-and-resume

Set `enabled: false` to restore the pre-quota-handling behaviour (quota
errors propagate immediately, `devbench start` exits non-zero, no
checkpoint is written):

```yaml
quota_handling:
  enabled: false
```

## Related

- [ADR-24: Quota wait-and-resume](adr/24-quota-wait-and-resume.md)
