# `devbench supervise` -- Interactive Orchestrator under a Detached `screen` Daemon

> Glyph note: this guide uses ASCII double-hyphen `--` everywhere a dash is needed.
> The em-dash glyph (U+2014) does not appear in this file.

`devbench supervise` launches the orchestrator as an interactive `claude` CLI
session, wrapped in a detached `screen` daemon and driven by a Python `pexpect`
supervisor, so the run is unattended, survives terminal detach, and
self-heals across restarts and quota windows. Its reason to exist is the billing
channel, selected by `--billing-mode` (default `subscription`):

- **`subscription`** (default) -- an interactive subscription session draws from
  the Claude Code Max subscription's rolling 5-hour usage windows instead of the
  direct Anthropic API.
- **`bedrock`** -- the same interactive session routes inference through AWS
  Bedrock (always-on; there are NO 5-hour windows for Bedrock).

In BOTH modes the AWS workload credentials (`AWS_ACCESS_KEY_ID`,
`AWS_SECRET_ACCESS_KEY`, `AWS_SESSION_TOKEN`, `AWS_PROFILE`) and region
(`AWS_REGION` / `AWS_DEFAULT_REGION`) pass through to the session unchanged: AWS
creds do NOT route Claude billing (only the Bedrock route flag does), and the
supervised orchestrator runs live AWS terratests that cannot work without them.

This is a NEW, purely additive command group. It does not change, deprecate, or
remove any existing launch path (`devbench start`, `devbench start --daemon`,
`make start-interactive`). See [execution-modes.md](execution-modes.md) for how
it sits beside the SDK and foreground-interactive modes, and ADR-31
([adr/31-interactive-screen-supervisor.md](adr/31-interactive-screen-supervisor.md))
for the design rationale.

## Table of contents

- [Billing modes (the raison d'etre)](#billing-modes-the-raison-detre)
- [The six verbs](#the-six-verbs)
- [Preflight requirements](#preflight-requirements)
- [Scope conveyance and multi-session](#scope-conveyance-and-multi-session)
- [Quota wait-and-resume](#quota-wait-and-resume)
- [Safe attach (read-only by default)](#safe-attach-read-only-by-default)
- [Auto-restart and exit taxonomy](#auto-restart-and-exit-taxonomy)
- [Configuration](#configuration)
- [Troubleshooting](#troubleshooting)
- [See also](#see-also)

## Billing modes (the raison d'etre)

The existing `devbench start` path drives a `ClaudeSDKClient`. Per
[llm-authentication.md](llm-authentication.md), that path authenticates by
handing the Claude Code OAuth `accessToken` to the Anthropic SDK as an
`api_key`, so inference is metered against the Anthropic API account (or AWS
Bedrock under `DEVBENCH_USE_BEDROCK=1`). Either way the orchestrator's tokens
bill at API/Bedrock per-token rates.

`devbench supervise` launches the orchestrator as an interactive `claude` CLI
session instead (NOT the SDK, and explicitly NOT `claude -p`/`--print`, which is
a non-interactive batch mode). The session's billing channel is selected by
`--billing-mode` (precedence: `--billing-mode` flag > `DEVBENCH_SUPERVISE_BILLING_MODE`
env > `supervise.billing_mode` config > default `subscription`; an invalid value
fails fast):

### `subscription` mode (default)

An interactive `claude` session authenticated via the Claude Code Max
subscription login draws from the subscription's rolling 5-hour usage windows.
The supervisor:

1. **Strips** the Claude-to-API/Bedrock ROUTING vars from the environment handed
   to the `screen` session -- `ANTHROPIC_API_KEY`, `ANTHROPIC_AUTH_TOKEN`,
   `ANTHROPIC_API_URL`, `ANTHROPIC_BASE_URL`, `DEVBENCH_USE_BEDROCK`, and the
   claude-CLI Bedrock/Vertex routing vars (`CLAUDE_CODE_USE_BEDROCK`,
   `CLAUDE_CODE_USE_VERTEX`, `ANTHROPIC_BEDROCK_BASE_URL`, `ANTHROPIC_MODEL`,
   `ANTHROPIC_SMALL_FAST_MODEL`, `AWS_BEARER_TOKEN_BEDROCK`) -- so inference
   cannot route off-subscription. This routing-var deny set is non-removable; a
   config that tries to whitelist one fails fast.
2. **Fails fast** at preflight (exit 2) if any of those routing vars is even
   present in the operator's environment:
   `ERROR: ANTHROPIC_API_KEY is set; an interactive supervised session in
   subscription mode must bill via the Claude Code subscription, not the direct
   API. Unset it and retry.`
3. **Verifies subscription auth** before launch (`~/.claude/.credentials.json`
   carries a `claudeAiOauth.accessToken` with the `user:inference` scope).
4. **Engages the 5-hour-window quota wait-and-resume** (see
   [Quota wait-and-resume](#quota-wait-and-resume)).
5. **Surfaces the billing channel** in `status`/`info` as
   `billing-channel: subscription` so an operator can audit at a glance.

### `bedrock` mode

The same interactive session routes inference through AWS Bedrock. The supervisor:

1. **Strips** only the direct-Anthropic-API vars (`ANTHROPIC_API_KEY`,
   `ANTHROPIC_AUTH_TOKEN`, `ANTHROPIC_API_URL`, `ANTHROPIC_BASE_URL`) and
   **exports** the claude-CLI Bedrock route the CLI needs:
   `CLAUDE_CODE_USE_BEDROCK=1`, `AWS_REGION` (from the operator's `AWS_REGION` /
   `AWS_DEFAULT_REGION`), and `ANTHROPIC_MODEL` (the resolved Bedrock model id).
2. Does NOT require subscription auth. Instead it **fails fast** at preflight if
   the AWS Bedrock prerequisites are absent (no AWS credential among
   `AWS_ACCESS_KEY_ID` / `AWS_PROFILE` / `AWS_BEARER_TOKEN_BEDROCK`, or no
   `AWS_REGION` / `AWS_DEFAULT_REGION`).
3. **Disables the 5-hour-window quota wait** -- Bedrock has no subscription
   windows; Bedrock throttling is handled by the shared `quota.py` path in the
   orchestrator subprocess (see [Quota wait-and-resume](#quota-wait-and-resume)).
4. **Surfaces the billing channel** in `status`/`info` as
   `billing-channel: bedrock`.

### AWS workload creds pass through in both modes

The AWS workload credentials (`AWS_ACCESS_KEY_ID`, `AWS_SECRET_ACCESS_KEY`,
`AWS_SESSION_TOKEN`, `AWS_PROFILE`) and region (`AWS_REGION` /
`AWS_DEFAULT_REGION`) are in NEITHER mode's deny set: they pass through to the
session unchanged. AWS creds do NOT route Claude billing (only the Bedrock route
flag does), and the supervised orchestrator runs live AWS terratests that cannot
work without them. The non-root preflight assertion applies in both modes.

## The six verbs

```
$ uv run devbench supervise --help
usage: devbench supervise <start|stop|restart|status|info|attach> [options]
```

### `supervise start`

```
uv run devbench supervise start [--name N] [--include "<tokens>"] \
    [--exclude "<tokens>"] [--allow-overlap] [--model M] [--effort E] \
    [--billing-mode {subscription,bedrock}]
```

`--billing-mode` selects the billing channel (default `subscription`); see
[Billing modes](#billing-modes-the-raison-detre). Runs the mode-aware preflight
(screen present, non-root, model resolvable, plus -- in `subscription` mode --
subscription auth present and no routing-var env, or -- in `bedrock` mode --
AWS Bedrock prerequisites present), writes the per-session `scope.json`, creates
the detached `screen` (`devbench-supervise-<name>`), launches `claude` inside it,
waits for the ready prompt, injects `/devbench-orchestrate:orchestrate`, and
transitions the session to `running`. Exits 0 only once the session reaches
`state=running`; exit 2 on a preflight/argument failure; a non-zero classified
code on a launch fault.

```bash
$ uv run devbench supervise start --name nightly
[supervise] preflight: screen 4.09.01 found
[supervise] preflight: subscription auth OK (~/.claude/.credentials.json, scope user:inference)
[supervise] preflight: no API-key env vars present (ANTHROPIC_API_KEY unset)
[supervise] launching screen 'devbench-supervise-nightly'
[supervise] claude ready; injecting /devbench-orchestrate:orchestrate (scope: all)
[supervise] state=running  pid=44310  screen=devbench-supervise-nightly
```

### `supervise stop`

```
uv run devbench supervise stop [--name N] [--hard]
```

Graceful (default): writes the per-session `drain.signal`, lets the in-flight
work unit finish, captures the `claude` session id for resume, sends the drain
command, and quits the screen. `--hard`: terminates the `claude` child and quits
the screen immediately. Exit 0 on stop; exit 2 if no such session.

### `supervise restart`

```
uv run devbench supervise restart [--name N]
```

Performs a graceful `stop` (capturing the session id) then relaunches with the
resume flags (`--continue`, or `--resume <id>` when an id was captured),
preserving orchestration context. Bounded by `supervise.restart.max_attempts`.

### `supervise status`

```
uv run devbench supervise status [--name N]
```

With `--name`, prints one session; without, prints all supervise sessions.
Fields: `name`, `state` (`starting|running|quota-waiting|draining|stopped|errored|restarting`),
`in-progress` (current claimed work unit), `last-activity`, `screen`,
`claude-session`, `billing-channel` (`subscription` or `bedrock`, mirroring the
active billing mode), and (when stopped/errored) `exit-reason`. When
`state=quota-waiting` it also shows `expected-resume` and `resumes-used=<n>/<cap>`
(subscription mode only -- bedrock mode never enters `quota-waiting`).

### `supervise info`

```
uv run devbench supervise info
```

Joins `screen -ls` with the registry and lists every supervise screen with its
SCREEN name, NAME, STATE, PID, CLAUDE-SESSION, BILLING channel, and the exact
`supervise attach --name N` command to observe it.

### `supervise attach`

```
uv run devbench supervise attach [--name N] [--screen]
```

Read-only observation. The default follows the redacted PTY transcript; the
attaching process's stdin is NEVER wired to the `claude` TTY, so an observer
cannot inject input or steal the PTY. See
[Safe attach](#safe-attach-read-only-by-default).

## Preflight requirements

`supervise start` fails fast (exit 2) with an actionable message unless ALL of:

| Requirement | Mode | Failure message |
|---|---|---|
| `screen` is installed | both | `ERROR: 'screen' is not installed. Install it (devcontainer: 'apt-get install -y screen'; macOS: 'brew install screen') and retry.` |
| `claude` is on PATH | both | `ERROR: 'claude' not found on PATH.` |
| The process is non-root | both | `ERROR: refusing to launch claude --dangerously-skip-permissions as root.` |
| A model resolves | both | `ERROR: no model: set --model, supervise.model, or orchestrate.model.` |
| No routing-var env present | subscription | `ERROR: ANTHROPIC_API_KEY is set; an interactive supervised session in subscription mode must bill via the Claude Code subscription, not the direct API. Unset it and retry.` |
| Subscription auth present | subscription | `ERROR: Claude Code subscription auth not found. Run 'claude' and complete the browser login, then retry.` |
| AWS Bedrock prerequisites present | bedrock | `bedrock billing mode requires AWS credentials (AWS_ACCESS_KEY_ID, AWS_PROFILE, or AWS_BEARER_TOKEN_BEDROCK); none are set. Configure AWS access and retry.` / `bedrock billing mode requires an AWS region: set AWS_REGION or AWS_DEFAULT_REGION ...` |

AWS workload creds (`AWS_ACCESS_KEY_ID`, `AWS_SECRET_ACCESS_KEY`,
`AWS_SESSION_TOKEN`, `AWS_PROFILE`) and region are NEVER treated as a routing
violation: they pass through in both modes.

The supervised session launches `claude --dangerously-skip-permissions`. This
is safe only inside the recognized devcontainer sandbox, non-root; the
supervisor asserts non-root as defense in depth (even though `claude` itself
also refuses to run that flag as root).

## Scope conveyance and multi-session

Scope is conveyed DETERMINISTICALLY before the orchestrate skill claims
anything, by reusing the existing scope plumbing (no new scope code):

- `--include "<tokens>"` / `--exclude "<tokens>"` expand via the same
  `ScopeFilter` the SDK path uses; an empty `--include` means the ENTIRE backlog
  (the deterministic default, not a fallback).
- `supervise start` writes the expanded scope to
  `<workspace>/.devbench/sessions/<name>/scope.json` (the same path the SDK path
  and `devbench next` read).
- Three env vars are exported into the screen session the `claude` child
  inherits: `DEVBENCH_WORKSPACE_ROOT` (which backlog + where the config is),
  `DEVBENCH_SESSION_NAME` (per-session scope/drain routing), and
  `DEVBENCH_CLAUDE_MODEL` (the import-time model the in-session `devbench`
  subprocesses need; NOT the interactive billing model, and NOT an API-key var).

Multiple disjoint-scope sessions run in parallel via distinct `--name`s. Scope
is expanded and overlap-checked under the shared backlog flock against both the
supervise registry and the SDK session registry, so a supervise session and an
SDK session cannot claim the same work unit. See
[multi-session-runs.md](multi-session-runs.md) for the shared arbitration model.

```bash
$ uv run devbench supervise start --name fast --include "priority:high"
$ uv run devbench supervise start --name bulk --exclude "priority:high"
$ uv run devbench supervise info
SCREEN                          NAME    STATE     PID     ATTACH
devbench-supervise-fast         fast    running   44310   supervise attach --name fast
devbench-supervise-bulk         bulk    running   44755   supervise attach --name bulk
```

## Quota wait-and-resume

This section applies to `subscription` mode. In `bedrock` mode there are NO
5-hour subscription windows, so the 5-hour wait is DISABLED: a subscription
usage-limit prompt is anomalous and faults fast with
`exit-reason=quota-wait-disabled-bedrock`. Bedrock throttling is instead handled
by the shared `quota.py` path (the `_BEDROCK_THROTTLE_CODES` handling) in the
orchestrator subprocess; the supervisor's subscription quota markers and the
5-hour window-reset logic do not fire in bedrock mode.

In `subscription` mode, a 5-hour-window exhaustion is NOT an error: the
supervisor transitions to `quota-waiting` and never exits non-zero, mirroring the
SDK semantics in ADR-24
([adr/24-quota-wait-and-resume.md](adr/24-quota-wait-and-resume.md)). The wait
REUSES the shared quota primitives (`quota.wait_for_reset`, the
`quota_handling` config, the resume cap, and the `quota_pause.json` checkpoint);
the only new logic is detecting the usage-limit prompt in the PTY and choosing
in-session-wait vs poll-and-restart. Detection is HYBRID: it also tails the
orchestrator's own quota log markers (`[QUOTA_WAITING]`, `[QUOTA_POLLING]`,
`[ORCHESTRATOR_QUOTA_RESUME]`), which are stable across CLI versions.

```bash
$ uv run devbench supervise status --name nightly
name=nightly  state=quota-waiting  expected-resume=2026-06-15T08:00:00Z  resumes-used=2/1000
```

> The EXACT interactive usage-limit prompt strings are version-fragile and are
> still being verified against a real quota event (DI-5). See
> [Troubleshooting](#troubleshooting) and
> `spec/devbench-supervise-screen-orchestrator/QUOTA-VERIFICATION-TODO.md`. Until
> the real strings are captured, the poll-and-restart path plus the stable log
> markers carry correctness; the in-session-wait path is best-effort.

## Safe attach (read-only by default)

Observation is a hard read-only requirement, not a preference:

- `supervise attach` (no flags) follows the supervisor-written, redacted
  `pty.log`. It is a pure read; the attaching process's stdin is never connected
  to the `claude` TTY, so it is structurally impossible for an observer to inject
  input or steal the PTY from the supervisor. Ctrl-C stops watching and does NOT
  stop the orchestration.
- Input-capable native sharing (`--screen`, via `screen -x`) is STRICTLY opt-in
  and is GATED: it stays disabled and fails fast
  (`ERROR: --screen attach is not enabled ...`) until a human verifies that the
  write-removed multiuser ACL cannot inject any keystroke into the `claude`
  window on the target `screen` build (DI-4).

The `pty.log` is created mode `0600` and is run through a configurable redaction
pass (`sk-ant-*`, `AKIA*`, `aws_secret`, `Bearer ` tokens by default) before any
chunk is written, so a secret the model happened to print is not persisted.

## Auto-restart and exit taxonomy

The supervisor owns its own bounded relaunch loop. When the orchestrator emits
the restart signal (the exit-42 RUNTIME_DEGRADATION equivalent), the supervisor
relaunches `claude` with `--continue` (bounded by `supervise.restart.max_attempts`).

| Outcome | Result |
|---|---|
| `ALL_DONE` / operator-gated `NO_ACTIONABLE` | clean completion, exit 0 |
| crash, prompt-timeout, harness-self-edit block, non-clean stop-reason | faulted, non-zero classified exit |
| 5-hour-window exhaustion | `quota-waiting`, NO exit (waits and resumes) |
| restart cap exhausted | faulted, `exit-reason=restart-cap-exhausted` |
| quota resume cap exhausted | faulted, `exit-reason=quota-resume-cap-exhausted` |

## Configuration

Every operational value is a `supervise:` config field with a documented default,
each overridable by a `DEVBENCH_SUPERVISE_*` env var (env > yaml > default). The
billing channel is `supervise.billing_mode` (`subscription` | `bedrock`, default
`subscription`), overridable by `--billing-mode` (flag wins) or
`DEVBENCH_SUPERVISE_BILLING_MODE` (env > config). The quota timeouts fall through
to the top-level `quota_handling` block when null. The full field set, defaults,
and env overrides are documented in
[devbench-yaml-reference.md](devbench-yaml-reference.md) under the `supervise:`
block. New injectable operator commands (future slash commands) are added via the
`supervise.injectable_commands` config map with NO supervisor code change.

## Troubleshooting

- **`ERROR: 'screen' is not installed`** -- install `screen`
  (`apt-get install -y screen` in the devcontainer; `brew install screen` on
  macOS) and retry.
- **`ERROR: Claude Code subscription auth not found`** -- run `claude` and
  complete the browser login once, then retry. The supervisor verifies but never
  manages or automates the login.
- **`ERROR: ANTHROPIC_API_KEY is set ...`** -- in `subscription` mode, unset the
  Claude-to-API/Bedrock routing var; the supervised session must bill against the
  subscription. Run `env | grep -E 'ANTHROPIC|CLAUDE_CODE_USE|DEVBENCH_USE_BEDROCK'`
  to find the offending routing var. (AWS workload creds are NOT routing vars and
  are never flagged -- they pass through in both modes.)
- **`bedrock billing mode requires AWS credentials ...` / `... an AWS region ...`**
  -- you launched with `--billing-mode bedrock` (or `supervise.billing_mode: bedrock`)
  but the AWS Bedrock prerequisites are missing. Set an AWS credential
  (`AWS_ACCESS_KEY_ID` / `AWS_PROFILE` / `AWS_BEARER_TOKEN_BEDROCK`) and a region
  (`AWS_REGION` / `AWS_DEFAULT_REGION`), then retry.
- **The session faults with `exit-reason=ready-prompt-timeout`** -- the `claude`
  CLI prompt-detection is version-fragile (the interactive ready/working prompt
  text changes between CLI versions). All prompt-detection regexes are
  centralized in `supervise.detection_patterns` so you can update them for your
  installed CLI version without a code change; the readiness detection is also
  hybrid (it accepts the first orchestrate log marker as readiness). Record your
  `claude --version` when reporting a detection mismatch.
- **The quota prompt is not detected** -- the EXACT interactive usage-limit
  prompt strings are unverified (DI-5). The poll-and-restart path and the stable
  log markers carry correctness meanwhile; capture the real strings via
  `spec/devbench-supervise-screen-orchestrator/QUOTA-VERIFICATION-TODO.md` and
  set `supervise.detection_patterns.quota_wait_prompt` /
  `supervise.injectable_commands.quota_wait_choice` to match.

## See also

- [execution-modes.md](execution-modes.md) -- supervise as the third execution mode
- [llm-authentication.md](llm-authentication.md) -- subscription vs API/Bedrock billing
- [devbench-yaml-reference.md](devbench-yaml-reference.md) -- the `supervise:` config block
- [cli-reference.md](cli-reference.md) -- the supervise verb reference
- [architecture.md](architecture.md) -- the interactive-screen launch path
- [adr/31-interactive-screen-supervisor.md](adr/31-interactive-screen-supervisor.md) -- design rationale
- [quota-handling.md](quota-handling.md) -- the reused quota wait-and-resume model
- [multi-session-runs.md](multi-session-runs.md) -- multi-session scope arbitration
