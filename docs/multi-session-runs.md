# Multi-Session Runs: Operator Playbook

Run two or more devbench sessions simultaneously against a single backlog by giving
each session a disjoint scope. Each session operates under its own named identity
(`DEVBENCH_SESSION_NAME`), keeps its state in
`<workspace>/.devbench/sessions/<name>/`, and never races for work units belonging
to a different session's scope.

This document covers the full operator workflow: setup, launch, monitoring, draining
individual sessions, stopping a session immediately, and cleaning up stale state.
See [docs/concurrent-multi-workspace.md](concurrent-multi-workspace.md) for the
older two-clone pattern (separate workspace roots per session), which remains the
right choice when sessions run on different machines or in separate containers.

## Table of contents

- [Concepts](#concepts)
- [Prerequisites](#prerequisites)
- [Worked example: three sessions on a 30-epic backlog](#worked-example-three-sessions-on-a-30-epic-backlog)
  - [Step 1 -- Plan the partition](#step-1----plan-the-partition)
  - [Step 2 -- Verify disjointness](#step-2----verify-disjointness)
  - [Step 3 -- Launch the sessions](#step-3----launch-the-sessions)
  - [Step 4 -- Monitor all sessions at once](#step-4----monitor-all-sessions-at-once)
  - [Step 5 -- Monitor a single session](#step-5----monitor-a-single-session)
  - [Step 6 -- Drain one session gracefully](#step-6----drain-one-session-gracefully)
  - [Step 7 -- Stop a session immediately](#step-7----stop-a-session-immediately)
  - [Step 8 -- Clean up stale sessions](#step-8----clean-up-stale-sessions)
- [Overlap detection and --allow-overlap](#overlap-detection-and---allow-overlap)
- [Audit trail and claim stamps](#audit-trail-and-claim-stamps)
- [Per-session state layout](#per-session-state-layout)
- [Common error messages](#common-error-messages)
- [Cross-references](#cross-references)

---

## Concepts

A **session** is a named orchestrator process identified by `DEVBENCH_SESSION_NAME`.
When the env var is set:

- Devbench registers the session in
  `<workspace>/.devbench/sessions/registry.json`.
- Per-session state files live under `<workspace>/.devbench/sessions/<name>/`
  (see [Per-session state layout](#per-session-state-layout)).
- Every `[WU_CLAIMED]` audit comment includes `session=<name>` so the provenance
  of each claim is traceable.
- Scope, drain, and log files are isolated per session; two sessions can never
  overwrite each other's files.

When `DEVBENCH_SESSION_NAME` is not set, devbench uses the implicit name `default`
and behaves identically to pre-#192 single-session operation, reading and writing
the workspace-root `scope.json`, `drain.signal`, and `orchestrator.log` as before.

The **scope** of a session is the set of work-unit IDs it is allowed to claim,
expressed in [printer-pages syntax](cli-reference.md#scope-selectors-printer-pages-syntax)
(e.g., `"E1-E10"`, `"E11-E20, E25"`, `"E21-E30"`). Devbench expands the tokens
into a sorted list of IDs and checks for overlap against every currently active
session before starting. A new session whose scope intersects an existing session's
scope is rejected unless `--allow-overlap` is supplied.

**Claim arbitration** is serialised by an exclusive `fcntl.flock` on
`<workspace>/.devbench/BACKLOG.lock`. Even with `--allow-overlap`, exactly one
session claims each work unit; the other receives a `ClaimRaceError` and skips
to its next candidate. See [ADR-23](adr/23-named-sessions.md) section 5 for the
full protocol.

---

## Prerequisites

| Requirement | Notes |
|-------------|-------|
| Single shared workspace root | All sessions share one `DEVBENCH_WORKSPACE_ROOT`. Unlike the two-clone pattern, no duplication is needed. |
| Non-overlapping `--include` tokens | Plan your partition before launching. See [Step 2](#step-2----verify-disjointness). |
| Devbench installed | Follow [zero-to-ready.md](zero-to-ready.md) for initial setup. |
| `DEVBENCH_WORKSPACE_ROOT` and `DEVBENCH_CLAUDE_MODEL` set | Both vars must be exported in every shell that launches a session. |
| One terminal per session | Each `devbench start` call is blocking; open a dedicated terminal (or tmux pane) per session. |

---

## Worked example: three sessions on a 30-epic backlog

Suppose the backlog has 30 epics (`E1` through `E30`). Three sessions run in parallel,
each owning a ten-epic slice: **early** (`E1-E10`), **mid** (`E11-E20`), and **late**
(`E21-E30`).

### Step 1 -- Plan the partition

Choose scope tokens so the three slices are mutually disjoint and together cover the
full backlog. For a uniform split:

| Session | Scope tokens | Epics covered |
|---------|-------------|---------------|
| `early` | `"E1-E10"` | E1, E2, ..., E10 |
| `mid` | `"E11-E20"` | E11, E12, ..., E20 |
| `late` | `"E21-E30"` | E21, E22, ..., E30 |

Epic-level boundaries are the safest partition because every work unit belongs to
exactly one epic, so `E1-E10` and `E11-E20` can never overlap.

### Step 2 -- Verify disjointness

Before launching, preview each scope's expanded ID list to confirm there is no
collision:

```bash
# Preview the expanded IDs for the "early" session.
uv run devbench scope set --include "E1-E10"
uv run devbench scope show
# Expected output: lists all work-unit IDs under E1 through E10.

# Clear so the workspace-root scope.json is not left behind.
uv run devbench scope clear

# Repeat for "mid".
uv run devbench scope set --include "E11-E20"
uv run devbench scope show
uv run devbench scope clear

# Repeat for "late".
uv run devbench scope set --include "E21-E30"
uv run devbench scope show
uv run devbench scope clear
```

Each `scope show` output must be disjoint from the others. If any work-unit ID
appears in two outputs, adjust the token strings before proceeding.

### Step 3 -- Launch the sessions

Open three terminals (or three tmux panes). Each terminal exports a unique
`DEVBENCH_SESSION_NAME` and calls `devbench start` with the matching `--include`
scope.

**Terminal 1 -- session "early":**

```bash
export DEVBENCH_WORKSPACE_ROOT=~/my-workspace
export DEVBENCH_CLAUDE_MODEL=us.anthropic.claude-sonnet-4-7-v1
export DEVBENCH_SESSION_NAME=early

uv run devbench start --include "E1-E10"
```

**Terminal 2 -- session "mid":**

```bash
export DEVBENCH_WORKSPACE_ROOT=~/my-workspace
export DEVBENCH_CLAUDE_MODEL=us.anthropic.claude-sonnet-4-7-v1
export DEVBENCH_SESSION_NAME=mid

uv run devbench start --include "E11-E20"
```

**Terminal 3 -- session "late":**

```bash
export DEVBENCH_WORKSPACE_ROOT=~/my-workspace
export DEVBENCH_CLAUDE_MODEL=us.anthropic.claude-sonnet-4-7-v1
export DEVBENCH_SESSION_NAME=late

uv run devbench start --include "E21-E30"
```

Each `devbench start` call:

1. Validates that `DEVBENCH_SESSION_NAME` does not collide with an already-active
   session's scope (fails fast with a clear error if it does).
2. Writes `<workspace>/.devbench/sessions/<name>/pid`,
   `scope.json`, `started_at`, and `started_by`.
3. Registers the session in `<workspace>/.devbench/sessions/registry.json`.
4. Claims and executes work units from its scope slice only.

**AC-192-3** -- Two (or more) sessions run concurrently against disjoint scopes
without corruption. The flock-serialised claim arbitration ensures that even if
both sessions attempt to claim the same unit simultaneously (e.g., a boundary
task visible to two adjacent scopes), exactly one wins and the other skips cleanly.

### Step 4 -- Monitor all sessions at once

In a fourth terminal (no session env var needed):

```bash
# Aggregate status across all sessions.
uv run devbench status

# Aggregate live progress report.
uv run devbench report
```

Without `--session`, both commands aggregate across all active sessions, showing
counts and work-unit lists from all three slices combined.

```bash
# List all active sessions with name, PID, scope, start time, drain state, liveness.
uv run devbench sessions
```

Example `devbench sessions` output:

```
NAME     PID    SCOPE         STARTED_AT            DRAIN     LIVENESS
early    42100  E1-E10        2026-05-17T10:00Z     none      ACTIVE
mid      42201  E11-E20       2026-05-17T10:00Z     none      ACTIVE
late     42302  E21-E30       2026-05-17T10:00Z     none      ACTIVE
```

### Step 5 -- Monitor a single session

**AC-192-12** -- Session-filtered status and report work correctly.

```bash
# Show status for the "mid" session only.
uv run devbench status --session mid

# Show the progress report for the "early" session only.
uv run devbench report --session early
```

These commands filter the rendered output and the event-index queries to work
units claimed under `session=mid` (or `session=early`). Work units from other
sessions are excluded from the filtered view.

### Step 6 -- Drain one session gracefully

A **drain** asks a session to finish its current work unit and then exit cleanly.
The session detects the drain marker between work units and stops without
interrupting in-flight execution.

```bash
# Ask the "mid" session to drain after its current work unit completes.
uv run devbench drain --session mid

# Check drain state for "mid".
uv run devbench drain --session mid --status
# Output: pending | no drain pending

# Cancel the drain if you change your mind (the session continues).
uv run devbench drain --session mid --cancel
```

When `devbench drain --session mid` is called:

1. The drain marker is written to
   `<workspace>/.devbench/sessions/mid/drain.signal` (not the workspace-root
   `drain.signal`), so only the `mid` session is affected.
2. The `early` and `late` sessions are unaffected.
3. The `mid` orchestrator detects the marker between work units, logs
   `[ORCHESTRATOR_DRAIN]`, consumes the marker, and exits rc=0.
4. The `mid` session's PID file and registry entry are removed on clean exit.

To drain every active session at once:

```bash
uv run devbench drain --all
```

This writes a drain marker to every active session's state directory. Each session
exits after its current work unit completes.

### Step 7 -- Stop a session immediately

If a session must be stopped without waiting for the current work unit to finish,
use `devbench stop`:

```bash
# Send SIGTERM to the "late" session's orchestrator process.
uv run devbench stop --session late
```

What happens:

1. Devbench reads the PID from
   `<workspace>/.devbench/sessions/late/pid`.
2. SIGTERM is sent to that process.
3. The SIGTERM handler in `cmd_start` forces the in-flight work unit to `blocked`
   with a `[FORCED_BLOCKED_ON_STOP] session=late` audit comment.
4. The orchestrator exits rc=0.
5. The `late` session's state directory is cleaned up.

The interrupted work unit remains in `blocked` state. Run
`uv run devbench sync-blocked` after restarting to re-examine whether it can be
re-queued.

### Step 8 -- Clean up stale sessions

A session becomes **stale** when its orchestrator process has exited but its state
directory was not removed (e.g., after a crash or an unclean kill). Stale sessions
appear in `devbench sessions` output with `LIVENESS: STALE`.

```bash
# List all sessions, including stale ones.
uv run devbench sessions

# Remove state directories for sessions whose PID is confirmed dead.
uv run devbench sessions --cleanup
```

The cleanup command only removes STALE sessions -- those where `os.kill(pid, 0)`
returns `ProcessLookupError` (ESRCH). Sessions where the liveness check raises
`PermissionError` (EPERM) are treated as ACTIVE and left untouched.

---

## Overlap detection and --allow-overlap

By default, `devbench start` rejects a new session whose scope intersects any
existing active session's scope:

```
ERROR: scope overlap detected between session "mid" and existing session "early"
Conflicting work-unit IDs: E10-F3-S1-T1, E10-F3-S1-T2
To allow competition, re-run with --allow-overlap.
```

The `--allow-overlap` flag bypasses the check and allows two sessions to compete
for the same work units:

```bash
DEVBENCH_SESSION_NAME=mid uv run devbench start --include "E10-E20" --allow-overlap
```

With `--allow-overlap`, the claim arbitration flock still applies: exactly one
session claims each unit; the other raises `ClaimRaceError` and skips to its next
candidate. Use this mode when you intentionally want two sessions to race (e.g., a
"fast lane" session that picks up any available task across the full backlog while a
scoped background session drains a specific slice).

---

## Audit trail and claim stamps

Every work unit claimed under a named session records the session name in its
`## Comments` section:

```
[2026-05-17 10:05 UTC] [agent/orchestrator] [WU_CLAIMED] Set E11-F2-S1-T1 to 'in-progress' session=mid
```

When `DEVBENCH_SESSION_NAME` is not set (legacy single-session mode), the format
is unchanged:

```
[2026-05-17 10:05 UTC] [agent/orchestrator] [WU_CLAIMED] Set E11-F2-S1-T1 to 'in-progress'
```

The `--session` filter in `devbench status` and `devbench report` matches against
these audit stamps to build the per-session view.

---

## Per-session state layout

```
<workspace>/
  .devbench/
    sessions/
      registry.json          # JSON array of all registered Session objects
      early/
        pid                  # OS PID of the orchestrator process
        scope.json           # Session-scoped work-unit scope filter
        drain.signal         # Session-scoped drain request marker (if pending)
        orchestrator.log     # Session-scoped orchestration log
        report.json          # Session-scoped report cache
        started_at           # ISO 8601 UTC timestamp of session start
        started_by           # OS username of the session owner
      mid/
        pid
        scope.json
        orchestrator.log
        report.json
        started_at
        started_by
      late/
        pid
        scope.json
        orchestrator.log
        report.json
        started_at
        started_by
    BACKLOG.lock             # Advisory flock sentinel (never deleted)
  logs/
    orchestrator.log         # Aggregate log -- all sessions write here too
```

The per-session `scope.json` and `drain.signal` take precedence over the
workspace-root equivalents when `DEVBENCH_SESSION_NAME` is set.  For
`drain.signal` specifically, an empty per-session path falls through to
the workspace-root path so an operator-issued `devbench drain` from a
shell (which has no `DEVBENCH_SESSION_NAME` env var) is still observed by
the session-scoped orchestrator (issue #212).  The aggregate
`logs/orchestrator.log` receives events from all sessions (for backward
compatibility with operators who run `devbench status` without `--session`).

---

## Common error messages

### Scope overlap on start

```
ERROR: scope overlap detected between session "mid" and existing session "early"
Conflicting work-unit IDs: E10-F3-S1-T1, E10-F3-S1-T2
```

**Fix:** adjust the `--include` tokens so the scopes are disjoint, or pass
`--allow-overlap` if competition is intentional.

### Session name already active

```
ERROR: a session named "mid" is already active (PID 42201, ACTIVE)
```

**Fix:** choose a different `DEVBENCH_SESSION_NAME`, or stop the existing session
with `devbench stop --session mid`.

### PID file not found on stop

```
ERROR: no PID file for session "late" -- session may not be running or state is stale
Run: devbench sessions --cleanup
```

**Fix:** run `devbench sessions --cleanup` to remove stale state, then restart.

---

## Cross-references

- [docs/adr/23-named-sessions.md](adr/23-named-sessions.md) -- architectural
  decisions: session identity, per-session state, liveness detection, flock
  arbitration, scope overlap, audit format extension.
- [docs/cli-reference.md](cli-reference.md) -- full command reference for
  `devbench sessions`, `devbench stop`, `devbench drain`, `devbench status`,
  `devbench report`, `devbench scope`.
- [docs/concurrent-multi-workspace.md](concurrent-multi-workspace.md) -- older
  two-clone pattern using separate workspace roots per session. Still appropriate
  for cross-machine or cross-container scenarios.
- [docs/glossary.md](glossary.md) -- canonical definitions of `session`,
  `drain`, `scope`, and `audit comment`.
- Spec section 4.4 (`spec/devbench-self-improve.md`) -- full named-session
  behavioural requirements.
- Issue #192 -- named sessions feature specification.
