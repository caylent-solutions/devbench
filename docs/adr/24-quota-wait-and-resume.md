# ADR-24: Quota Wait-and-Resume Policy

**Status:** Accepted
**Date:** 2026-05-17

---

## Context

Devbench's orchestration loop runs `devbench start` and drives the Claude Agent SDK
in an automated loop, claiming work units, spawning executor agents, and collecting
results without human supervision. In practice this means:

1. **Long autonomous runs.** A backlog of 50--200 work units can take 4--12 hours of
   wall-clock time. Operators start a run before leaving the office or going to sleep,
   expecting it to complete by morning. The operator is not present to restart on
   failure.

2. **API rate limits are routine, not exceptional.** Claude Pro and Max subscriptions
   cap requests-per-minute and tokens-per-day. In a busy orchestration run, the cap is
   hit every few hours. Pre-#193, every quota hit crashed the run and left work units
   stuck `in-progress`.

3. **Multiple quota error types across providers.** Devbench supports three Anthropic
   access paths -- Claude Code OAuth (Pro/Max subscription), direct API key, and AWS
   Bedrock -- each with a distinct quota error signature:
   - HTTP 429 + `anthropic-ratelimit-*-reset` header: subscription rate limit.
   - HTTP 402 + `insufficient_quota`: API key credit balance depleted.
   - HTTP 402 + `billing_error`: API key account billing failure.
   - Bedrock `ThrottlingException`: provisioned throughput exhausted.

   A uniform detection layer was needed to handle all four without duplicating logic.

4. **Per-session quota state.** Issue #192 (ADR-23) introduced named sessions. When
   two sessions run in parallel, they may hit quota at different times. Session A
   hitting the rate limit must not prevent session B -- which has its own token budget
   -- from continuing.

Before #193, the operator workflow on quota exhaustion was:

1. Notice the orchestrator exited with a non-zero code.
2. Identify the stuck `in-progress` work unit.
3. Re-queue it manually with `devbench set-status <id> in-queue`.
4. Restart `devbench start` after the reset window passes.

This four-step manual loop is incompatible with unattended overnight runs. Issue #193
specifies the automated wait-and-resume feature. This ADR records the architectural
decisions made while implementing it.

---

## Decision

### 1. Unified exception hierarchy in quota.py

A new module `src/devbench/quota.py` owns all quota-related logic:

- **Exception hierarchy:** `QuotaExhaustedError` (base), with four concrete subclasses:
  `SubscriptionRateLimitError`, `SdkCreditExhaustedError`, `ApiBillingError`, `BedrockThrottleError`.
  Each subclass carries the parsed reset time (or `None` for credit exhaustion, where
  no automatic reset time exists).

- **`detect_quota_error(message_or_exception)`:** Single entry point for detection.
  Inspects HTTP status codes, response headers (`anthropic-ratelimit-requests-reset`,
  `Retry-After`), and error body shapes. Returns the appropriate
  `QuotaExhaustedError` subclass or `None` if not a quota error. Detection is
  controlled by the `detect_modes` config list; error classes omitted from the list
  propagate as fatal errors unchanged.

- **`parse_reset_time(headers)`:** Parses the `Retry-After` and
  `anthropic-ratelimit-*-reset` headers into a `datetime` (UTC). Returns `None` when
  no reset header is present (credit exhaustion; reset time must be determined by
  probing).

- **`wait_for_reset(reset_at, poll_interval, max_wait, probe_fn)`:** Sleeps until
  `reset_at`, then calls `probe_fn` to confirm recovery. On still-throttled: applies
  exponential backoff (up to `max_seconds`) with jitter (configurable multiplier and
  jitter fraction) and re-polls. Returns `True` on recovery, `False` when
  `max_wait` is exceeded.

  This is one of two explicitly-specified exceptions to the "no `time.sleep()`"
  rule in CLAUDE.md. The sleep here is the intended mechanism -- waiting for an
  externally-determined reset time. All other synchronisation in devbench uses
  readiness detection.

- **`recovery_probe(timeout_seconds)`:** Sends a 1-token completion request to
  confirm quota has genuinely recovered before the orchestrator restarts. This
  prevents a false-resume where the headers indicated recovery but the API still
  rejects the request. The probe is configurable: `request_size_tokens`,
  `timeout_seconds`, and the backoff parameters all come from `QuotaHandlingConfig`.

- **`save_checkpoint(session_dir, wu_id, phase, ...)`:** Writes `quota_pause.json`
  atomically (temp-then-rename via `os.replace`) to the session state directory.

- **`load_checkpoint(session_dir)`:** Reads `quota_pause.json` for resume;
  returns `None` if absent.

### 2. Config-driven wait/resume via QuotaHandlingConfig

The `quota_handling` section in `backlog/config/devbench.yaml` is optional. When
absent, devbench uses safe defaults equivalent to:

```yaml
quota_handling:
  enabled: true
  detect_modes:
    - subscription_rate_limit
    - sdk_credit_exhausted
    - api_billing_error
    - bedrock_throttle
  on_exhaustion: wait
  poll_interval_seconds: 60
  max_wait_seconds: 18000
  on_exhaustion_timeout: drain
  resume_strategy: continue_current_wu
  audit_comment_on_wait: true
  audit_comment_on_resume: true
  log_structured_events: true
  notify_on_pause:
    webhook_url: null
    slack_webhook_url: null
  notify_on_resume:
    webhook_url: null
    slack_webhook_url: null
  recovery_probe:
    enabled: true
    request_size_tokens: 1
    timeout_seconds: 10
    backoff:
      initial_seconds: 30
      max_seconds: 600
      multiplier: 2.0
      jitter: 0.2
```

Key design choices:

- **`enabled: false` is the escape hatch.** Setting `enabled: false` restores the
  pre-#193 raise-and-exit behaviour exactly. This satisfies AC-193-4 and ensures
  existing operator runbooks that handle quota failures externally are not broken.

- **`on_exhaustion: wait | fail | drain`** gives operators three policies: automatic
  wait (default), immediate exit, or finish-the-current-WU-then-exit. The `drain`
  variant reuses the drain protocol from E3 (#190), keeping the exit path cooperative.

- **`on_exhaustion_timeout: drain | fail | keep_waiting`** ensures a safety ceiling.
  The default `drain` exits cooperatively after `max_wait_seconds`; `fail` exits
  immediately; `keep_waiting` is for attended operator sessions and explicitly
  disables the ceiling.

- **`resume_strategy`** controls re-entry after quota recovers:
  - `continue_current_wu`: skip already-passed judges; resume from the last
    completed phase (default, lowest waste).
  - `restart_wu`: revert to `in-queue` and re-claim from scratch (safest for
    long-running phases that may have non-idempotent side effects).
  - `drain_and_resume`: finish the in-flight WU before resuming new claims.

### 3. quota_pause.json checkpoint file

When a quota wait begins, devbench writes `quota_pause.json` to the session state
directory:

```
<workspace>/.devbench/sessions/<session-name>/.devbench/quota_pause.json
```

For the default session:

```
<workspace>/.devbench/sessions/default/.devbench/quota_pause.json
```

The file contains the parsed reset time, the session ID, the work unit ID that was
in-flight, and the error class. It is removed atomically when the wait ends. The
file's existence is the signal used by `devbench status` to render the QUOTA WAIT
banner (AC-193-15) and by `devbench quota-watcher` to drive recovery polling.

Using a file rather than an in-memory flag is deliberate: if the orchestrator process
dies mid-wait, the pause file persists on disk. The operator can inspect it
(`quota_pause.json` is human-readable JSON), or run `devbench quota-watcher --once`
to advance the wait without relaunching the full orchestrator.

### 4. SDK wrapper retry decorator in cmd_start

The `cmd_start::_run` function wraps the `async for message in query(...)` loop with
a retry decorator that:

1. Catches `QuotaExhaustedError` (and detects quota errors embedded in SDK messages).
2. Writes `quota_pause.json` via `save_checkpoint`.
3. Appends `[QUOTA_WAITING] reason=... reset_at=...` to the in-flight work unit.
4. Calls `wait_for_reset` with the parsed reset time.
5. On recovery: removes `quota_pause.json`, appends `[QUOTA_RESUMED] waited_seconds=...`,
   and restarts the outer loop with a fresh SDK session.
6. On `max_wait_seconds` exceeded: fires `on_exhaustion_timeout` and exits.

The retry decorator does NOT retry on non-quota errors. Only `QuotaExhaustedError`
subclasses trigger the wait path; all other exceptions propagate immediately.

### 5. Audit comments

Two new audit comment phrases (spec section 4.5.7):

- `[QUOTA_WAITING] reason=<class> reset_at=<ISO-ts>` -- written to the in-flight
  work unit when the wait begins.
- `[QUOTA_RESUMED] waited_seconds=<N>` -- written when the wait ends.

Both use the existing `BacklogManager._append_agent_comment` helper (DRY). The
`audit_comment_on_wait` and `audit_comment_on_resume` config fields independently
toggle each.

### 6. Per-session quota isolation

Each named session (ADR-23) owns its own `quota_pause.json`. Session A hitting the
rate limit writes its pause file; session B, with its own token budget, is unaffected.
The `devbench status --session <name>` banner shows only the named session's state.

### 7. Notification webhooks

`notify_on_pause` and `notify_on_resume` accept `webhook_url` and
`slack_webhook_url`. Webhook delivery is best-effort: failures are logged at `[WARN]`
level but do NOT crash the orchestrator (spec section 4.5.6). This is the only
intentional "best-effort" path in devbench; the design decision is that a missed
notification is less bad than a crashed orchestrator that leaves work units stuck.

---

## Alternatives considered

### Alternative A: Operator-driven manual retry

Maintain the pre-#193 behaviour: crash on quota exhaustion, require the operator to
re-queue the stuck work unit and restart manually.

**Rejected** because:

- Incompatible with unattended overnight runs -- the entire motivation for autonomous
  operation is eliminated if human intervention is required for every quota hit.
- Every hit leaves a work unit stuck `in-progress`; the operator must know which
  unit to re-queue, which requires reading logs.
- During a busy run, quota hits may occur every 2--4 hours. Manual restart at every
  hit defeats the purpose of automation.

The `enabled: false` config flag preserves this behaviour for operators who explicitly
want it (e.g., CI pipelines that should fail fast rather than waiting hours).

### Alternative B: Fixed sleep

On quota detection, sleep for a hardcoded constant (e.g., 3600 seconds) and retry.

**Rejected** because:

- The Anthropic API returns the exact reset timestamp in the
  `anthropic-ratelimit-requests-reset` header. Ignoring it and using a fixed-sleep
  wastes time when the actual reset is shorter, or retries too early when the actual
  window is longer.
- Hard-coded sleep durations violate CLAUDE.md's "no hardcoded values" rule (all
  timeouts and delays must be configurable).
- A fixed sleep gives no feedback to the operator; the `quota_pause.json` + audit
  comment approach provides observable state that `devbench status` can surface.
- Fixed sleep cannot handle the credit-exhaustion case (no reset header), where
  probing is required to detect when the operator has replenished credit.

### Alternative C: Automatic provider failover

On quota exhaustion, automatically switch to a fallback API key or provider (e.g.,
Anthropic subscription -> direct API key -> Bedrock chain).

**Rejected** because:

- Devbench does not own the operator's credentials inventory; it cannot safely assume
  a fallback credential exists or that its quota state is independent.
- Auto-failover would silently change which model and provider generates results,
  potentially affecting output quality and cost without operator awareness.
- The spec explicitly excludes automatic provider failover and multi-account quota
  pooling from scope (spec sections 1.4 and 4.5 "out of scope" note).
- Implementing failover correctly requires a reliable provider-health check, credential
  rotation logic, and per-provider billing awareness -- a substantially larger surface
  than the wait-and-resume feature.

Per-agent model overrides (`docs/llm-authentication.md`) are the recommended mechanism
for operators who want to pre-configure a fallback credential; this is an explicit
operator decision, not an automatic one.

### Alternative D: Predictive quota throttling

Track token usage and slow down proactively before hitting the limit.

**Rejected** because:

- The Anthropic API does not expose a reliable real-time token budget endpoint;
  devbench would need to maintain its own estimate, which could drift.
- Predictive throttling adds latency to every task (artificial delay inserted to stay
  below an estimated limit) rather than only incurring cost on actual quota exhaustion.
- The spec explicitly excludes predictive quota throttling from scope.

### Alternative E: Per-session independent retry with no coordination

Each session retries independently with no shared pause file.

**Rejected** because the `quota_pause.json` file is the observable state used by
`devbench status` (QUOTA WAIT banner) and `devbench quota-watcher` (daemon-based
recovery). Without a shared file, the daemon cannot advance the wait, and the operator
cannot inspect pause state without reading logs. The per-session design (one file per
session directory) provides the necessary isolation without losing observability.

---

## Consequences

### Operator playbook

Full operator guidance lives in `docs/quota-handling.md`. Quick reference:

- Quota waits require zero operator intervention under default settings (`on_exhaustion: wait`).
- Check pause state: `devbench status` shows a QUOTA WAIT banner with a reset
  countdown when `quota_pause.json` exists for the current session.
- Advance the wait manually: `uv run devbench quota-watcher --once`.
- Run the watcher as a daemon: `uv run devbench quota-watcher --daemon`.
- Disable quota handling: set `quota_handling.enabled: false` in
  `backlog/config/devbench.yaml`.
- Recover from a `fail` exit: re-queue the stuck work unit with
  `devbench set-status <id> in-queue`, then restart `devbench start`.

### Safety bounds

The following safety properties hold:

- **No indefinite blocking by default.** `max_wait_seconds: 18000` (5 hours) is the
  ceiling. When exceeded, `on_exhaustion_timeout: drain` (default) exits cooperatively.
  The `keep_waiting` option must be explicitly configured by the operator.
- **No silent data loss.** `quota_pause.json` is written atomically before the wait
  begins. If the process dies mid-wait, the file persists and records the context.
- **Audit trail is always present.** `[QUOTA_WAITING]` and `[QUOTA_RESUMED]` comments
  are appended to the in-flight work unit, providing a timestamped record of every
  quota event in the backlog history.
- **No false resumes.** The recovery probe sends a 1-token request before the
  orchestrator restarts. If the probe returns another quota error, backoff is applied
  and the wait continues. The probe prevents the case where the reset header said
  "quota recovered" but the API still rejects requests.
- **Webhook failures are logged, not fatal.** Notification webhook delivery failures
  are logged at `[WARN]` level and do not affect the wait-and-resume cycle. The
  orchestrator's primary obligation is to resume correctly; notifications are
  informational.

### What is out of scope

- **Automatic provider failover.** Switching to a fallback API key or Bedrock on
  quota exhaustion is a separate decision that requires operator-configured credentials
  and explicit consent. Per-agent model overrides in `docs/llm-authentication.md` are
  the recommended opt-in mechanism.
- **Multi-account quota pooling.** Distributing requests across multiple Anthropic
  accounts to extend the effective quota budget is not in scope; it requires credential
  management and billing model awareness well beyond the feature boundary.
- **Predictive throttling.** Slowing down proactively before hitting the quota
  ceiling is excluded; the API does not expose a reliable real-time budget counter.
- **Cross-session quota sharing.** Sessions have independent `quota_pause.json` files.
  There is no mechanism to "lend" quota headroom from one session to another.
- **Recovery from non-quota API errors.** The `QuotaExhaustedError` hierarchy covers
  only the four specified error patterns. Other API errors (authentication failures,
  service outages, model unavailability) propagate as fatal errors unchanged.

### Backwards compatibility

- Operators who omit `quota_handling` from `devbench.yaml` receive the safe defaults
  (`enabled: true`, `on_exhaustion: wait`). No config change is required.
- Setting `enabled: false` restores pre-#193 behaviour exactly.
- All existing `devbench start` flags and exit semantics are unchanged.
- The `quota_pause.json` file is created lazily; it does not appear in workspaces that
  have never hit a quota limit.

---

## References

- `src/devbench/quota.py` -- `QuotaExhaustedError` hierarchy, `detect_quota_error`,
  `parse_reset_time`, `wait_for_reset`, `recovery_probe`, `save_checkpoint`,
  `load_checkpoint`.
- `src/devbench/config_loader.py` -- `QuotaHandlingConfig` dataclass and YAML parser.
- `src/devbench/cli.py` -- `cmd_start::_run` SDK wrapper retry decorator,
  `cmd_quota_watcher` daemon.
- `src/devbench/constants.py` -- default values for all quota_handling fields.
- `tests/test_quota.py` -- unit tests with 100% line + branch coverage.
- `plugin/devbench/scripts/guard-quota-aware.sh` -- PreToolUse hook that defers Bash
  invocations when a quota_pause.json pause is active.
- `plugin/devbench/scripts/continue-orchestration.sh` -- Stop hook extension that
  writes quota_pause.json when quota patterns appear in the transcript.
- `docs/quota-handling.md` -- operator playbook (configuration reference,
  troubleshooting, sample configs for Pro/Max/API-key/Bedrock).
- `docs/adr/23-named-sessions.md` -- ADR-23: per-session state directory layout that
  quota_pause.json shares; session isolation semantics.
- Spec section 4.5 -- authoritative behavioural specification for the quota
  wait-and-resume feature (#193).
- Issue #193 -- original feature request and discussion.
