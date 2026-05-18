# Quota Handling: Operator Playbook

DevBench automatically detects Anthropic API quota exhaustion and waits for the
quota window to reset before resuming orchestration. This document covers when
quota waits fire, how to configure the behavior, and what to do when things go
wrong.

**Spec source:** `spec/devbench-self-improve.md` section 4.5. Issue: #193.

---

## Table of contents

- [Overview](#overview)
- [When Quota Waits Fire](#when-quota-waits-fire)
- [Configuration](#configuration)
  - [Full schema](#full-schema)
  - [Sample configurations](#sample-configurations)
- [What Happens When max_wait_seconds Is Exceeded](#what-happens-when-max_wait_seconds-is-exceeded)
- [Status Banner (devbench status)](#status-banner-devbench-status)
- [quota-watcher Daemon](#quota-watcher-daemon)
- [Audit Trail](#audit-trail)
- [Multi-session Awareness](#multi-session-awareness)
- [Troubleshooting](#troubleshooting)
- [Cross-references](#cross-references)

---

## Overview

When the Anthropic API returns a quota-exhaustion response (HTTP 429 rate limit,
HTTP 402 credit exhaustion, or an AWS Bedrock throttle error), DevBench writes a
`quota_pause.json` checkpoint file and pauses the orchestration loop. Once the
reset window passes -- confirmed by a lightweight recovery probe -- DevBench
removes `quota_pause.json` and resumes from where it left off.

The full wait-and-resume cycle requires zero operator intervention under default
settings. The operator can observe progress via `devbench status`, which shows a
**QUOTA WAIT** banner with a reset countdown.

This behavior satisfies AC-193-13 (non-interactive end-to-end wait-and-resume)
and AC-193-15 (status banner with countdown).

---

## When Quota Waits Fire

Quota waits are triggered by four distinct error patterns, each mapped to an
exception subclass in `src/devbench/quota.py`:

| Error pattern | Exception class | Typical cause |
|---------------|----------------|--------------|
| HTTP 429 + `anthropic-ratelimit-*-reset` header | `SubscriptionRateLimit` | Claude Pro or Max subscription rate limit |
| HTTP 402 `insufficient_quota` | `SdkCreditExhausted` | API key credit balance depleted |
| HTTP 402 `billing_error` | `ApiBillingError` | Billing failure on API key account |
| Bedrock throttle error shape | `BedrockThrottle` | AWS Bedrock provisioned throughput exhausted |

### Which Anthropic plans are affected

**Claude Pro (subscription via Claude Code OAuth):** subject to
`SubscriptionRateLimit`. The reset time is read from the
`anthropic-ratelimit-requests-reset` or `anthropic-ratelimit-tokens-reset`
response header. DevBench waits until that timestamp, then probes before
resuming.

**Claude Max (subscription via Claude Code OAuth):** also subject to
`SubscriptionRateLimit` when the per-window request/token cap is hit. Behavior
is identical to Pro.

**API key (direct Anthropic API access):** subject to `SdkCreditExhausted` when
prepaid credit runs out, or `ApiBillingError` on payment failure. Unlike
subscription rate limits, these have no automatic reset time. The wait applies
`poll_interval_seconds` probing until credit is replenished or
`max_wait_seconds` is exceeded.

**AWS Bedrock:** subject to `BedrockThrottle`. The reset time is parsed from the
Bedrock error shape. Backoff and probing work the same way regardless of
provider.

### Detection modes configuration

The `detect_modes` field controls which error patterns DevBench actively watches
for:

```yaml
quota_handling:
  detect_modes:
    - subscription_rate_limit   # HTTP 429 from Anthropic
    - sdk_credit_exhausted      # HTTP 402 insufficient_quota
    - api_billing_error         # HTTP 402 billing_error
    - bedrock_throttle          # Bedrock ThrottlingException
```

Remove any mode to ignore that error class and let it propagate as a fatal error
instead.

---

## Configuration

The `quota_handling` section in `backlog/config/devbench.yaml` is optional. When
absent, DevBench uses the safe defaults shown below. All fields are optional and
independently overridable.

### Full schema

```yaml
quota_handling:
  enabled: true                        # false -> legacy raise+exit behavior
  detect_modes:
    - subscription_rate_limit
    - sdk_credit_exhausted
    - api_billing_error
    - bedrock_throttle
  on_exhaustion: wait                  # wait | fail | drain
  poll_interval_seconds: 60            # min 30, max 3600
  max_wait_seconds: 18000              # 5 hours; 0 = wait forever
  on_exhaustion_timeout: drain         # drain | fail | keep_waiting
  resume_strategy: continue_current_wu # continue_current_wu | restart_wu | drain_and_resume
  audit_comment_on_wait: true
  audit_comment_on_resume: true
  log_structured_events: true
  # Pause / resume notifications moved to the unified ``notifications:``
  # block in PR #202.  Set ``notifications.events.quota_pause: true`` and
  # ``notifications.events.quota_resume: true`` to get a Slack ping on
  # each event.  See docs/slack-notifications.md for the full setup.
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

**`enabled`:** Set to `false` to disable all quota-wait logic and restore the
pre-#193 behavior (raise and exit on any quota error). Satisfies AC-193-4 and
AC-193-19.

**`on_exhaustion`:** What to do when quota is detected:
- `wait` (default) -- pause and wait for reset; resume automatically.
- `fail` -- raise and exit immediately (legacy behavior with `enabled: true`).
- `drain` -- finish the currently-running work unit, then pause and wait.

**`poll_interval_seconds`:** How often (in seconds) to probe for quota recovery
after the expected reset time has passed. Minimum 30, maximum 3600.

**`max_wait_seconds`:** Upper bound on total wait time in seconds. Defaults to
18000 (5 hours). When this ceiling is reached, `on_exhaustion_timeout` fires.

**`on_exhaustion_timeout`:** What to do when `max_wait_seconds` is exceeded:
- `drain` (default) -- attempt to finish any in-flight work unit, then exit.
- `fail` -- exit immediately with non-zero status.
- `keep_waiting` -- ignore the ceiling and continue probing indefinitely.

**`resume_strategy`:** How to restart after quota recovers:
- `continue_current_wu` (default) -- resume the in-flight work unit from the
  last completed phase; already-passed judges are skipped.
- `restart_wu` -- revert the work unit to `in-queue` and re-claim from scratch.
- `drain_and_resume` -- finish any in-flight work unit's remaining phases, then
  resume claiming new work units.

**`recovery_probe`:** A lightweight 1-token completion probe sent before
resuming, to confirm quota has genuinely recovered. Backoff is applied if the
probe returns another quota error, with jitter to prevent thundering-herd on
multi-session workspaces.

### Sample configurations

#### Claude Pro / Max -- subscription rate limit

Subscribers are rate-limited per window. The reset time comes from the API
response header. This configuration waits up to the configured ceiling, then
drains gracefully:

```yaml
quota_handling:
  enabled: true
  detect_modes:
    - subscription_rate_limit
  on_exhaustion: wait
  poll_interval_seconds: 60
  max_wait_seconds: 7200              # 2-hour ceiling for Pro window resets
  on_exhaustion_timeout: drain
  resume_strategy: continue_current_wu
  audit_comment_on_wait: true
  audit_comment_on_resume: true
```

#### API key -- credit / billing exhaustion

API key accounts exhaust when prepaid credit runs out. There is no automatic
reset time; the operator must manually replenish credit. This configuration uses
a longer poll interval and a 24-hour ceiling:

```yaml
quota_handling:
  enabled: true
  detect_modes:
    - sdk_credit_exhausted
    - api_billing_error
  on_exhaustion: wait
  poll_interval_seconds: 300          # probe every 5 minutes
  max_wait_seconds: 86400             # 24-hour ceiling
  on_exhaustion_timeout: fail         # exit after 24h so CI pipelines do not hang
  resume_strategy: restart_wu         # restart cleanly after credit is restored
```

#### AWS Bedrock -- provisioned throughput throttle

Bedrock returns throttle errors when throughput limits are hit. Resets typically
occur within minutes. This configuration uses aggressive probing and a short
ceiling:

```yaml
quota_handling:
  enabled: true
  detect_modes:
    - bedrock_throttle
  on_exhaustion: wait
  poll_interval_seconds: 30           # probe every 30 seconds
  max_wait_seconds: 3600              # 1-hour ceiling
  on_exhaustion_timeout: fail
  resume_strategy: continue_current_wu
  recovery_probe:
    enabled: true
    request_size_tokens: 1
    timeout_seconds: 10
    backoff:
      initial_seconds: 30
      max_seconds: 300
      multiplier: 2.0
      jitter: 0.25                    # extra jitter for multi-region deployments
```

#### Minimal (disable quota handling)

To restore legacy behavior -- raise and exit on any quota error -- set
`enabled: false`:

```yaml
quota_handling:
  enabled: false
```

---

## What Happens When max_wait_seconds Is Exceeded

When the total elapsed wait exceeds `max_wait_seconds`, DevBench consults
`on_exhaustion_timeout`:

1. **`drain` (default):** The orchestrator signals the in-flight executor to
   finish its current work unit. Once the work unit transitions out of
   `in-progress`, DevBench writes a `[QUOTA_TIMEOUT] waited_seconds=<N>` audit
   comment to the work unit and exits with a non-zero status code.

2. **`fail`:** DevBench exits immediately with a non-zero status code and logs
   `[QUOTA_TIMEOUT] waited_seconds=<N> action=fail` to the orchestrator log.
   Any in-flight work unit is left in `in-progress` status; the operator must
   manually re-queue it with `devbench set-status <id> in-queue`.

3. **`keep_waiting`:** DevBench ignores the `max_wait_seconds` ceiling and
   continues probing indefinitely. Use this only in attended operator sessions
   where the operator can cancel manually.

### Recovering after a fail exit

If DevBench exited via `on_exhaustion_timeout: fail`, check for stuck
`in-progress` work units:

```bash
# List all in-progress work units
uv run devbench status

# Re-queue the stuck unit manually
uv run devbench set-status <work-unit-id> in-queue
```

Then restart devbench with sufficient quota:

```bash
uv run devbench start
```

---

## Status Banner (devbench status)

When quota is exhausted and DevBench is waiting, `devbench status` shows a
**QUOTA WAIT** banner above the work-unit table:

```
+-------------------------------------------------------------+
|  QUOTA WAIT                                                 |
|  Reason:      subscription_rate_limit                       |
|  Reset at:    2026-05-17T15:30:00Z                          |
|  Time left:   47 minutes                                    |
|  Session:     default                                       |
+-------------------------------------------------------------+
```

The banner includes:

- **Reason** -- the detected quota error class (e.g., `subscription_rate_limit`).
- **Reset at** -- the ISO 8601 UTC timestamp when quota is expected to recover.
- **Time left** -- live countdown to the reset timestamp.
- **Session** -- the named session whose quota is paused (`default` for
  single-session runs).

The banner is rendered whenever `quota_pause.json` exists for the active session.
Once DevBench resumes, the banner disappears automatically on the next
`devbench status` invocation.

This satisfies AC-193-15 (`devbench status` shows QUOTA WAIT banner with reset
countdown).

---

## quota-watcher Daemon

The `devbench quota-watcher` command polls `quota_pause.json` files and
advances the orchestration loop when quota recovers. It is useful in supervised
environments where a separate process manages the wait loop independently of the
main orchestrator.

### Single-tick mode

Run one poll cycle and exit:

```bash
uv run devbench quota-watcher --once
```

### Daemon mode

Run continuously, polling all sessions at `poll_interval_seconds` intervals:

```bash
uv run devbench quota-watcher --daemon
```

When a `quota_pause.json` reset time passes, the daemon:

1. Sends a recovery probe (`recovery_probe.request_size_tokens` tokens).
2. On success: removes `quota_pause.json` and (for interactive sessions)
   re-prompts the Claude Code session via `claude --resume <session-id>`.
3. On still-throttled: applies the configured backoff and retries.
4. Logs every state transition to the session's `orchestrator.log`.

### Running the daemon as a background process

In a long-running operator scenario, start the daemon in the background before
launching devbench:

```bash
uv run devbench quota-watcher --daemon &
DEVBENCH_SESSION_NAME=my-session uv run devbench start
```

The daemon exits cleanly when all `quota_pause.json` files are cleared. It does
not need to run if `on_exhaustion: wait` is configured in DevBench itself --
the main orchestration loop handles the wait internally.

---

## Audit Trail

DevBench appends structured audit comments to the in-flight work unit when quota
waits begin and end. These comments appear in the work unit's `## Comments`
section and are visible in `devbench status --verbose`:

### [QUOTA_WAITING]

Written when the wait begins:

```
[2026-05-17 13:00:00 UTC] [agent/orchestrator] [QUOTA_WAITING]
reason=subscription_rate_limit reset_at=2026-05-17T15:30:00Z
waited=0s session=my-session
```

### [QUOTA_RESUMED]

Written when quota recovers and orchestration resumes:

```
[2026-05-17 15:32:45 UTC] [agent/orchestrator] [QUOTA_RESUMED]
waited_seconds=9165 session=my-session
```

Both comments use the existing `BacklogManager._append_agent_comment` helper
(spec section 4.5.7). `audit_comment_on_wait` and `audit_comment_on_resume`
config fields toggle each independently.

---

## Multi-session Awareness

Each named session maintains its own `quota_pause.json` at:

```
<workspace>/.devbench/sessions/<session-name>/quota_pause.json
```

For the default (single) session:

```
<workspace>/.devbench/sessions/default/quota_pause.json
```

Two sessions can be in different quota states simultaneously -- one waiting, one
active. The `devbench status` banner applies only to the session whose
`quota_pause.json` exists. With `--session <name>`, the banner is filtered to
the named session.

See `docs/multi-session-runs.md` for the full per-session state directory layout
and the `docs/adr/23-named-sessions.md` ADR for the architectural rationale. The
quota handling feature shares the per-session state directory introduced in #192.

This satisfies AC-193-16 (multi-session aware, per-session `quota_pause.json`).

---

## Troubleshooting

### Quota wait is not triggering

**Symptom:** DevBench exits immediately on a quota error instead of waiting.

**Diagnosis:**

1. Check that `quota_handling.enabled` is `true` in `backlog/config/devbench.yaml`
   (or confirm the section is absent -- the default is `true`).
2. Check that the error class is listed in `detect_modes`. An error class not in
   `detect_modes` propagates as a fatal error.
3. Check `on_exhaustion` -- if set to `fail`, DevBench will exit immediately by
   design.

```bash
# Inspect the current config
uv run devbench status --verbose | grep -A5 "quota"
```

### quota_pause.json is not being cleared

**Symptom:** DevBench remains paused after the expected reset time has passed.

**Diagnosis:**

1. Inspect the `quota_pause.json` file to see the stored reset timestamp:

   ```bash
   cat <workspace>/.devbench/sessions/<session-name>/quota_pause.json
   ```

2. If the reset time has passed, the recovery probe may be failing. Check the
   session orchestrator log:

   ```bash
   cat <workspace>/.devbench/sessions/<session-name>/orchestrator.log | tail -30
   ```

3. If the probe returns another quota error, the backoff is extending the wait.
   Verify your credential/token configuration in `docs/llm-authentication.md`
   to confirm quota has genuinely recovered.

4. If the file is stale (DevBench process died mid-wait), remove it manually:

   ```bash
   rm <workspace>/.devbench/sessions/<session-name>/quota_pause.json
   ```

   Then restart DevBench. It will re-detect quota exhaustion if still hit.

### max_wait_seconds exceeded: work unit stuck in-progress

**Symptom:** DevBench exited with `on_exhaustion_timeout: fail` and a work unit
is stuck in `in-progress`.

**Fix:**

```bash
# Identify the stuck work unit
uv run devbench status

# Re-queue it for retry after credit/quota is restored
uv run devbench set-status <work-unit-id> in-queue

# Restart the orchestrator
uv run devbench start
```

### Recovery probe returns 401 or 403

**Symptom:** Log shows `[QUOTA_PROBE_FAIL] status=401` or `status=403` after
the reset time has passed.

**Diagnosis:** The credentials are invalid or expired, not a quota issue. Verify
authentication:

- **Claude Code OAuth:** Re-run `claude` in the terminal and complete the browser
  login flow to refresh the token. See `docs/llm-authentication.md` for the full
  token refresh procedure.
- **API key:** Verify `DEVBENCH_CLAUDE_CREDENTIALS_FILE` points to a file with a
  valid key.
- **Bedrock:** Run `aws sts get-caller-identity` to confirm IAM credentials are
  valid.

### Webhook notifications are not firing

**Symptom:** quota pause / resume Slack pings don't arrive.

**Diagnosis:** Quota notifications now flow through the unified
`notifications:` block (PR #202). The legacy
`quota_handling.notify_on_pause` / `notify_on_resume` fields were
removed. See [docs/slack-notifications.md](slack-notifications.md)
for the operator walkthrough; the relevant toggles are
`notifications.events.quota_pause` and
`notifications.events.quota_resume`.

### devbench status does not show QUOTA WAIT banner

**Symptom:** quota_pause.json exists but the banner is absent.

**Diagnosis:** Check whether you are running `devbench status` with a
`--session` filter that does not match the session that wrote `quota_pause.json`:

```bash
# Show all sessions and their quota state
uv run devbench status

# Show only a specific session
uv run devbench status --session <session-name>
```

If `quota_pause.json` was written by session `alpha` and you run
`devbench status --session beta`, the banner will not appear.

---

## Cross-references

- **`docs/adr/24-quota-wait-and-resume.md`** -- ADR-24: architectural decisions
  for the quota wait policy (design rationale, alternatives considered).
- **`docs/llm-authentication.md`** -- LLM authentication setup for all four
  provider types; per-agent model overrides as a quota management strategy.
- **`docs/multi-session-runs.md`** -- operator playbook for named sessions; the
  per-session state directory layout that quota_pause.json shares.
- **`docs/cli-reference.md`** -- full reference for `devbench quota-watcher`,
  `devbench status`, and `devbench set-status`.
- **[`docs/slack-notifications.md`](slack-notifications.md)** -- the unified
  Slack / webhook notification system (PR #202); covers the
  `notifications.events.quota_pause` and
  `notifications.events.quota_resume` toggles.
- **`docs/glossary.md`** -- canonical definitions for "quota wait", "session",
  "audit comment", and "drain".
- **Spec section 4.5** -- authoritative behavioral specification for the
  quota wait-and-resume feature (#193).
- **Issue #193** -- original feature request and discussion.
