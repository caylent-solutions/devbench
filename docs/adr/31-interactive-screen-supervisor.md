# ADR-31: Interactive `screen` + `pexpect` Supervisor (Billing-Mode Orchestrator)

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
Some operators instead want the same interactive session billed via **AWS
Bedrock** (which is always-on and has no 5-hour windows). Either way the
foreground-interactive path is not unattended: it dies when the terminal
detaches and has no quota-wait, auto-restart, or multi-session arbitration.

We need an orchestrator launch that is (a) billed against the operator-selected
channel (subscription or Bedrock), (b) unattended and detach-surviving, and (c)
self-healing across quota windows and restarts -- without changing any existing
launch path.

Crucially, the supervised orchestrator runs **live AWS terratests**, so the AWS
workload credentials MUST reach the session in every mode. AWS creds do not route
Claude billing (only the Bedrock route flag does), so stripping them was a
mistake that forced subscription billing and broke the live AWS path.

## Decision

Add a new, purely additive `devbench supervise {start|stop|restart|status|info|attach}`
command group that launches the orchestrator as an **interactive `claude` CLI
session**, wrapped in a detached `screen` daemon, driven by a Python `pexpect`
supervisor.

Key decisions:

1. **Interactive CLI + operator-selected billing mode, over the SDK/API path.**
   The supervised session is a real interactive `claude` session whose billing
   channel is selected by `--billing-mode` (flag > `DEVBENCH_SUPERVISE_BILLING_MODE`
   env > `supervise.billing_mode` config > default `subscription`; invalid value
   fails fast). The deny set is a function of the mode (one DRY helper,
   `resolve_supervise_deny_vars`):
   - **`subscription`** strips the Claude-to-API/Bedrock ROUTING vars
     (`ANTHROPIC_API_KEY`, `ANTHROPIC_AUTH_TOKEN`, `ANTHROPIC_API_URL`,
     `ANTHROPIC_BASE_URL`, `DEVBENCH_USE_BEDROCK`, and the claude-CLI Bedrock/Vertex
     routing vars `CLAUDE_CODE_USE_BEDROCK`/`CLAUDE_CODE_USE_VERTEX`/`ANTHROPIC_BEDROCK_BASE_URL`/`ANTHROPIC_MODEL`/`ANTHROPIC_SMALL_FAST_MODEL`/`AWS_BEARER_TOKEN_BEDROCK`)
     so inference cannot route off-subscription, verifies the subscription OAuth
     credential (`user:inference` scope), and engages the 5-hour quota wait.
   - **`bedrock`** strips only the direct-Anthropic-API vars and EXPORTS the
     claude-CLI Bedrock route (`CLAUDE_CODE_USE_BEDROCK=1` + `AWS_REGION` +
     `ANTHROPIC_MODEL`), requires the AWS Bedrock prerequisites instead of
     subscription auth, and disables the 5-hour wait (Bedrock has no windows).

   The routing-var deny set (the union across modes) is non-removable -- a config
   that tries to whitelist one fails fast -- and the supervisor fails fast at
   preflight if a denied routing var is present in the operator environment.
   **The AWS workload creds (`AWS_ACCESS_KEY_ID`, `AWS_SECRET_ACCESS_KEY`,
   `AWS_SESSION_TOKEN`, `AWS_PROFILE`) and region (`AWS_REGION` /
   `AWS_DEFAULT_REGION`) are in NEITHER mode's deny set and ALWAYS pass through**
   (the orchestrator runs live AWS terratests). The non-root assertion applies in
   both modes.

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

- DevBench can run the orchestrator unattended and detach-surviving on either
  subscription or Bedrock billing (selected by `--billing-mode`), complementing
  (not replacing) the SDK and foreground-interactive paths. All three coexist;
  see [../execution-modes.md](../execution-modes.md).
- AWS workload creds always reach the supervised session, so the live AWS
  terratests run in both billing modes. The 5-hour quota wait is engaged in
  `subscription` mode and disabled in `bedrock` mode (which relies on the shared
  `quota.py` Bedrock-throttle handling).
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
