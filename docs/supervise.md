# `devbench supervise` -- Interactive Orchestrator under a Detached `screen` Daemon

> Glyph note: this guide uses ASCII double-hyphen `--` everywhere a dash is needed.
> The em-dash glyph (U+2014) does not appear in this file.

`devbench supervise` launches the orchestrator as an interactive `claude` CLI
session, wrapped in a detached `screen` daemon and driven by a Python `pexpect`
supervisor, so the run is unattended, survives terminal detach, and
self-heals across restarts and quota windows. Its single reason to exist is the
billing channel: an interactive subscription session draws from the Claude Code
Max subscription's rolling 5-hour usage windows instead of being metered at
per-token API/Bedrock rates.

This is a NEW, purely additive command group. It does not change, deprecate, or
remove any existing launch path (`devbench start`, `devbench start --daemon`,
`make start-interactive`). See [execution-modes.md](execution-modes.md) for how
it sits beside the SDK and foreground-interactive modes, and ADR-31
([adr/31-interactive-screen-supervisor.md](adr/31-interactive-screen-supervisor.md))
for the design rationale.

## Table of contents

- [Why subscription billing (the raison d'etre)](#why-subscription-billing-the-raison-detre)
- [The six verbs](#the-six-verbs)
- [Preflight requirements](#preflight-requirements)
- [Scope conveyance and multi-session](#scope-conveyance-and-multi-session)
- [Quota wait-and-resume](#quota-wait-and-resume)
- [Safe attach (read-only by default)](#safe-attach-read-only-by-default)
- [Auto-restart and exit taxonomy](#auto-restart-and-exit-taxonomy)
- [Configuration](#configuration)
- [Troubleshooting](#troubleshooting)
- [See also](#see-also)

## Why subscription billing (the raison d'etre)

The existing `devbench start` path drives a `ClaudeSDKClient`. Per
[llm-authentication.md](llm-authentication.md), that path authenticates by
handing the Claude Code OAuth `accessToken` to the Anthropic SDK as an
`api_key`, so inference is metered against the Anthropic API account (or AWS
Bedrock under `DEVBENCH_USE_BEDROCK=1`). Either way the orchestrator's tokens
bill at API/Bedrock per-token rates.

An interactive `claude` CLI session authenticated via the Claude Code Max
subscription login draws from the subscription's rolling 5-hour usage windows
instead. `devbench supervise` therefore launches the orchestrator as that
interactive session (NOT the SDK, and explicitly NOT `claude -p`/`--print`,
which is a non-interactive batch mode).

**Correctness requirement (the feature is pointless if violated):** an
interactive session whose environment contains `ANTHROPIC_API_KEY` (or
`ANTHROPIC_AUTH_TOKEN`, `ANTHROPIC_API_URL`, `ANTHROPIC_BASE_URL`, or the
`DEVBENCH_USE_BEDROCK`/`AWS_*` Bedrock-routing vars) silently routes inference
to API billing and defeats the entire purpose. The supervisor:

1. **Strips** every API/Bedrock-routing var from the environment handed to the
   `screen` session (the always-deny set is non-removable; a config that tries
   to whitelist one fails fast).
2. **Fails fast** at preflight (exit 2) if any always-deny var is even present
   in the operator's environment:
   `ERROR: ANTHROPIC_API_KEY is set; an interactive supervised session must bill
   against the Claude Code subscription, not the API. Unset it and retry.`
3. **Verifies subscription auth** before launch (`~/.claude/.credentials.json`
   carries a `claudeAiOauth.accessToken` with the `user:inference` scope).
4. **Surfaces the billing channel** in `status`/`info` as
   `billing-channel: subscription` so an operator can audit at a glance.

## The six verbs

```
$ uv run devbench supervise --help
usage: devbench supervise <start|stop|restart|status|info|attach> [options]
```

### `supervise start`

```
uv run devbench supervise start [--name N] [--include "<tokens>"] \
    [--exclude "<tokens>"] [--allow-overlap] [--model M] [--effort E]
```

Runs the preflight (screen present, non-root, subscription auth present, no
API-key env var, model resolvable), writes the per-session `scope.json`, creates
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
`claude-session`, `billing-channel: subscription`, and (when stopped/errored)
`exit-reason`. When `state=quota-waiting` it also shows `expected-resume` and
`resumes-used=<n>/<cap>`.

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

| Requirement | Failure message |
|---|---|
| `screen` is installed | `ERROR: 'screen' is not installed. Install it (devcontainer: 'apt-get install -y screen'; macOS: 'brew install screen') and retry.` |
| `claude` is on PATH | `ERROR: 'claude' not found on PATH.` |
| The process is non-root | `ERROR: refusing to launch claude --dangerously-skip-permissions as root.` |
| No API-key env var present | `ERROR: ANTHROPIC_API_KEY is set; ... bill against the subscription, not the API. Unset it and retry.` |
| Subscription auth present | `ERROR: Claude Code subscription auth not found. Run 'claude' and complete the browser login, then retry.` |
| A model resolves | `ERROR: no model: set --model, supervise.model, or orchestrate.model.` |

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

A 5-hour-window exhaustion is NOT an error: the supervisor transitions to
`quota-waiting` and never exits non-zero, mirroring the SDK semantics in ADR-24
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
quota timeouts fall through to the top-level `quota_handling` block when null. The
full field set, defaults, and env overrides are documented in
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
- **`ERROR: ANTHROPIC_API_KEY is set ...`** -- unset the API-key env var; the
  supervised session must bill against the subscription. Run
  `env | grep -E 'ANTHROPIC|AWS|BEDROCK'` to find the offending var.
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
