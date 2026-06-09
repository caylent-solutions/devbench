# ADR-24: Quota Wait-and-Resume

**Status:** Accepted
**Date:** 2026-06-06
**Issue:** #236

**Refinement (2026-06-09, bug-fix):** Decision items 1, 3, and 4 below were
refined after a false-positive pause in production -- benign sub-agent reviewer
prose ("API endpoints implement rate limiting") tripped a bare `"rate limit"`
substring marker, and the recovery probe (which cannot authenticate under
Claude-Code CLI subscription auth) then polled for the full `max_wait_seconds`.
Detection is now precise and gated on tool-result error state, and the probe
fails fast / defers to the provider-supplied reset time when it cannot run.

**Refinement (2026-06-09, config wiring):** The `on_exhaustion` and
`on_exhaustion_timeout` config fields were previously parsed and validated but
never consumed (only ever `wait`-then-silently-return-0 happened). They are now
honored: `on_exhaustion` is applied at detection time in
`_dispatch_quota_detection` (`fail` re-raises for a non-zero exit; `drain`
requests a graceful drain and stops without waiting; `wait` pauses and polls),
and `on_exhaustion_timeout` is applied in `_dispatch_quota_timeout` when the
wait cap elapses **or** the recovery probe is unavailable (`drain` default,
`fail` re-raises, `keep_waiting` exits cleanly and lets the restart loop
re-enter — it does NOT block forever). A drain requested this way is preserved
across `cmd_start`'s exit (its `cancel_drain` cleanup is skipped) so the restart
loop / a peer session acts on it. New audit markers: `[QUOTA_FAIL_FAST]`,
`[QUOTA_DRAIN_REQUESTED] phase=<detection|timeout>`,
`[QUOTA_TIMEOUT_KEEP_WAITING]`.

## Context

Anthropic subscription accounts have token-rate limits that reset on a fixed
UTC schedule (typically every six hours). When the orchestrate loop hits this
limit mid-session, the Claude Agent SDK surfaces an HTTP 429 with a
`reset_at` timestamp in the response body (or a CLI-level "You've hit your
limit" text line). Before this ADR, the error propagated directly, causing
`devbench start` to exit non-zero and requiring manual operator intervention
to restart after the window reset.

This ADR formalises the decision to detect these errors and pause the
orchestrate loop automatically, waiting for the window to reset before
resuming -- rather than exiting and requiring a manual restart.

## Decision

1. **Detect per-message.** The inner SDK message loop in `cmd_start._run`
   calls `detect_quota_error(message)` for every SDK-emitted message. On a
   non-None result, `_run` raises a `_QuotaDetected(quota_exc)` sentinel
   (a `BaseException` subclass so it crosses the `asyncio.run` boundary
   without being caught by generic `except Exception` handlers).

   **1a. Precise text matching (refinement).** Because every message --
   including sub-agent transcripts surfaced as `UserMessage`/`ToolResultBlock`
   content -- passes through `detect_quota_error`, text matching must not fire
   on prose that merely mentions rate limiting. CLI text detection therefore
   matches the verbatim limit markers (e.g. "You've hit your limit") plus a
   bounded regex requiring an exhaustion verb adjacent to "rate limit"
   (`exceeded | reached | hit | exhausted | resets | try again`). In addition,
   a `ToolResultBlock` is only scanned when its `is_error` field is `True` or
   unset -- a successful tool result (`is_error is False`) carries benign prose
   and never triggers a pause. Structured signals (HTTP 429, the
   `AssistantMessage.error == 'rate_limit'` field) are unaffected.

2. **Checkpoint before sleeping.** Before waiting, `_handle_quota_pause`
   writes a `QuotaCheckpoint` to
   `<workspace>/.devbench/quota_pause.json`. This allows:
   - `devbench quota-watcher --once` to report current pause state to the
     operator.
   - An interrupted wait (SIGTERM) to leave evidence on disk rather than
     silently disappearing.

3. **Wait with jittered exponential backoff (no shield).** `wait_for_reset`
   performs an initial sleep until `reset_at` (if known), then polls with
   jittered exponential backoff. `asyncio.shield` is deliberately NOT used:
   a SIGTERM during the wait propagates naturally, allowing the SIGTERM
   handler to force-block the in-flight work unit and exit cleanly.

   **3a. Probe-unavailable handling (refinement).** The recovery probe issues
   a minimal Anthropic API call. That channel can be permanently unavailable
   (no/invalid credential -- common under Claude-Code CLI subscription auth),
   in which case polling can never confirm recovery. `recovery_probe` now
   distinguishes permanent failures (authentication / permission / missing
   credential) from transient ones (network, 429) and raises
   `RecoveryProbeUnavailableError` for the permanent case. `wait_for_reset`
   resumes on the provider-supplied `reset_at` when it has elapsed (the reset
   time is the readiness signal), and otherwise propagates so the handler
   stops fast rather than polling for the full `max_wait_seconds`.

4. **Emit structured audit markers.** Three markers are emitted to the
   orchestrator log:
   - `[QUOTA_WAITING] reason=<source> reset_at=<ISO|unknown>` when the wait
     begins.
   - `[QUOTA_RESUMED] waited_seconds=<N>` when recovery is confirmed.
   - `[QUOTA_PROBE_UNAVAILABLE] reason=<source> detail=<msg>` when the probe
     cannot run and no reset time is known (the wait stops fast).

5. **Configurable via `quota_handling:` block.** All behaviour is opt-in at
   the config level. The `enabled` flag defaults to `true` (D-Q-1:
   default-on opt-out); set `enabled: false` to restore the legacy non-zero
   exit.

6. **Config schema validated at load time.** Enum fields (`on_exhaustion`,
   `on_exhaustion_timeout`, `resume_strategy`) and range fields
   (`poll_interval_seconds`, `max_wait_seconds`) are validated at
   `load_runtime_config` time with a clear `ValueError` on violation -- not
   at first use.

## Consequences

### Positive

- Subscription-tier users no longer need to monitor and manually restart the
  orchestrator after quota exhaustion -- the loop resumes automatically.
- The checkpoint provides operator-visible state via `quota-watcher --once`.
- The `enabled: false` escape hatch preserves full backward compatibility for
  operators who prefer the explicit exit.
- The structured markers integrate cleanly with the existing audit-comment
  and log infrastructure.

### Negative

- A long pause (up to `max_wait_seconds`, default 5 hours) may not be
  apparent to operators who do not monitor logs. The `audit_comment_on_wait`
  flag (default `true`) mitigates this by writing to the active work unit's
  Comments section.
- `wait_for_reset` uses `asyncio.sleep`, which means the process is alive
  but idle during the wait. The PID file and the checkpoint together make this
  state observable, but an operator who kills the process expecting a clean
  stop will find the work unit in `in-progress` (the SIGTERM handler
  force-blocks it before exit, so this is recoverable).

## Alternatives considered

### asyncio.shield

Using `asyncio.shield(wait_for_reset(...))` would prevent a SIGTERM from
interrupting the wait. This was rejected because it would make the process
unresponsive to `devbench stop --session <name>` during a long quota wait,
and because the checkpoint already provides the durable state needed for a
clean restart.

### External watcher process

Spinning off a separate background process to monitor quota state was
considered but rejected as over-engineered for the common case (single
orchestrator session). The `quota-watcher --once` advisory command covers
the operator's inspection need without adding process-lifecycle complexity.

### Retry via Makefile loop only

Relying entirely on the existing `make start` retry loop (which re-invokes
`devbench start` on exit code 42) was rejected because:
- It requires the operator to have configured the retry loop.
- The retry loop does not know about `reset_at` and would re-attempt
  immediately, hitting the same 429 repeatedly until the window resets.
- The in-process wait avoids the overhead of re-initialising the SDK,
  re-reading the backlog, and re-establishing session state.

## References

- Issue #236: Quota wait-and-resume
- Issue #234: Quota detection module (`detect_quota_error`)
- Spec Section 4 E1.F2 / E1.F3
- Appendix A QW-1..QW-10
- `docs/quota-handling.md`
