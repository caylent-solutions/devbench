# ADR-31: Interactive `screen` + `pexpect` Supervisor (Subscription-Billed Orchestrator)

**Status:** Accepted

**Date:** 2026-06-15

> Glyph note: this ADR uses ASCII double-hyphen `--` everywhere a dash is needed.
> The em-dash glyph (U+2014) does not appear in this file.

## Context

DevBench has two pre-existing launch paths for the orchestrator:

1. **Agent-SDK, non-interactive** (`devbench start` / `make start`, plus the
   double-forked `--daemon` variant). This drives a `ClaudeSDKClient`.
2. **Foreground interactive** (`make start-interactive`), which runs
   `claude --plugin-dir plugin/devbench` directly in the operator's terminal.

The SDK path bills inference at API rates. Per
[../llm-authentication.md](../llm-authentication.md), it authenticates by reading
the Claude Code OAuth `accessToken` and handing it to the Anthropic SDK as an
`api_key`, so the orchestrator's tokens are metered against the Anthropic API
account (or AWS Bedrock under `DEVBENCH_USE_BEDROCK=1`) at per-token rates.

An operator running on a Claude Code **Max subscription** wants the
orchestrator's token consumption to draw from the subscription's rolling 5-hour
usage windows instead of being metered as per-token API spend. An interactive
`claude` CLI session authenticated via the subscription login does exactly that.
But the foreground-interactive path is not unattended: it dies when the terminal
detaches and has no quota-wait, auto-restart, or multi-session arbitration.

We need an orchestrator launch that is (a) billed against the subscription, (b)
unattended and detach-surviving, and (c) self-healing across quota windows and
restarts -- without changing any existing launch path.

## Decision

Add a new, purely additive `devbench supervise {start|stop|restart|status|info|attach}`
command group that launches the orchestrator as an **interactive `claude` CLI
session**, wrapped in a detached `screen` daemon, driven by a Python `pexpect`
supervisor.

Key decisions:

1. **Interactive CLI + subscription billing, over the SDK/API path.** The
   supervised session is a real interactive `claude` session billed against the
   subscription's 5-hour windows, not the API. The correctness guard is
   environment minimization: the supervisor strips `ANTHROPIC_API_KEY`,
   `ANTHROPIC_AUTH_TOKEN`, `ANTHROPIC_API_URL`, `ANTHROPIC_BASE_URL`,
   `DEVBENCH_USE_BEDROCK`, and the `AWS_*` Bedrock-routing vars from the session
   environment (the always-deny set is non-removable), and fails fast at
   preflight if any is present in the operator environment. It also verifies the
   subscription OAuth credential (`user:inference` scope) before launch.

2. **NOT `claude -p` / `--print`.** `-p`/`--print` is a non-interactive batch
   mode. It would reintroduce the wrong UX and is excluded by operator
   requirement. The supervisor drives a genuinely interactive session over the
   PTY that `screen -dmS` allocates.

3. **`screen` as the daemon backend** (not tmux/nohup). `screen -dmS` allocates a
   PTY the `claude` CLI can drive (it detects a TTY via `isatty()`), survives
   terminal hangup, and offers a multiuser ACL for the (gated) opt-in attach.

4. **`pexpect` as the PTY driver.** A thin `pexpect.spawn` wrapper launches
   `claude`, waits for the ready prompt, injects
   `/devbench-orchestrate:orchestrate`, and reads the transcript. `pexpect` is
   added as a hard Python dependency; `screen` is a system dependency the
   supervisor probes for and fails fast on when absent.

5. **Reuse, do not reinvent.** Quota wait-and-resume reuses the ADR-24
   primitives verbatim (`quota.wait_for_reset`, the `quota_handling` config, the
   resume cap, the `quota_pause.json` checkpoint); scope conveyance reuses the
   existing `ScopeFilter` + per-session `scope.json` + `DEVBENCH_SESSION_NAME`
   routing; multi-session arbitration reuses `flock_backlog` +
   `detect_scope_overlap`. The supervise registry mirrors the session registry's
   file shape but is intentionally kept separate so `status`/`info` keep the
   subscription-billed and API-billed channels distinct.

## Version-fragility tradeoff and the hybrid log-tail mitigation

Driving an interactive `claude` CLI over a PTY is NOT officially recommended and
is version-fragile: the on-screen ready prompt, working prompt, and usage-limit
prompt text change between CLI versions, so screen-scraping regexes can break on
an upgrade. We accept this tradeoff (it is the only way to get subscription
billing) and engineer robustness around it:

- **All prompt-detection regexes are centralized in config**
  (`supervise.detection_patterns`) so an operator updates them for their
  installed CLI version with no code change.
- **Detection is HYBRID.** Beyond screen-scraping the PTY, the supervisor tails
  the orchestrator's own structured log markers (`ALL_DONE`, `NO_ACTIONABLE`,
  `[ORCHESTRATOR_TERMINAL_EXIT]`, `[QUOTA_WAITING]`, `[QUOTA_POLLING]`,
  `[ORCHESTRATOR_QUOTA_RESUME]`, `[ORCHESTRATOR_AUTO_RESTART]`,
  `[ORCHESTRATOR_FATAL_ERROR]`, `[HARNESS_INTEGRITY]`). These markers are stable
  across CLI versions and carry correctness even when the on-screen prompt
  wording drifts.
- **Generous, configurable timeouts.** Readiness and idle detection use
  configurable `expect()` timeouts (event-driven, no `sleep`), and readiness is
  also satisfied by the first orchestrate log marker.

The EXACT interactive usage-limit prompt strings are the highest-risk unknown
(DI-5). They remain placeholders seeded from the SDK-surface markers until a
real 5-hour-window event is captured (see
`spec/devbench-supervise-screen-orchestrator/QUOTA-VERIFICATION-TODO.md`); the
poll-and-restart path plus the stable log markers carry correctness meanwhile.

## Consequences

- DevBench can run the orchestrator unattended and detach-surviving on
  subscription billing, complementing (not replacing) the SDK and
  foreground-interactive paths. All three coexist; see
  [../execution-modes.md](../execution-modes.md).
- A new hard dependency (`pexpect`) and a new system dependency (`screen`) are
  introduced. Missing `screen` fails fast with an install message.
- Observation is read-only by default (a redacted PTY-log follow that cannot
  inject input); input-capable `screen -x` attach is gated off until a human
  verifies its ACL (DI-4).
- The interactive PTY path is version-fragile; the hybrid log-tail and
  config-driven regexes mitigate it, but a `claude` CLI upgrade may require
  updating `supervise.detection_patterns`.
- The full operator guide is [../supervise.md](../supervise.md); the config block
  is documented in [../devbench-yaml-reference.md](../devbench-yaml-reference.md).

## References

- Spec: `spec/devbench-supervise-screen-orchestrator/devbench-supervise-screen-orchestrator.md`
- ADR-23 (named sessions), ADR-24 (quota wait-and-resume), ADR-25 (per-agent
  model overrides), ADR-30 (harness self-edit guard).
- [../llm-authentication.md](../llm-authentication.md) -- subscription vs API/Bedrock billing.
