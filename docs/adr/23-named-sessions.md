# ADR-23: Named Sessions

**Status:** Accepted
**Date:** 2026-05-17

---

## Context

Devbench's orchestration loop has always assumed a single operator running a single
`devbench start` invocation against one backlog at a time. That model breaks down in
two real operator scenarios:

1. **Parallel work streams.** A large backlog with many independent work units benefits
   from two or more Claude orchestrators running simultaneously against disjoint scope
   slices -- halving wall-clock time without changing correctness guarantees.

2. **Mixed-priority processing.** An operator wants a "fast lane" session picking only
   high-priority tasks while a background session drains the rest. Single-session
   devbench cannot express this; the only workaround was multiple full workspaces,
   which duplicated state and made `devbench status` useless.

The pre-#192 design had no concept of session identity. Every `devbench start`
invocation read and wrote the same files (`scope.json`, `drain.signal`,
`orchestrator.log`, the BACKLOG.md claim audit) with no isolation between concurrent
callers. Two simultaneous sessions could:

- Race to claim the same work unit, producing corrupt duplicate `in-progress` stamps.
- Overwrite each other's `scope.json`, silently changing what each session claimed next.
- Interleave drain signals intended for one session so both sessions drained.
- Produce a single `orchestrator.log` with events from two sessions interspersed and
  unattributable.

Issue #192 specifies the full named-session capability. This ADR records the
architectural decisions made while implementing it.

---

## Decision

### 1. Session identity via DEVBENCH_SESSION_NAME environment variable

A session is named by setting `DEVBENCH_SESSION_NAME=<name>` before launching
`devbench start`. When absent, devbench falls back to the implicit name `default`,
preserving full backwards compatibility for single-session operators who never set
the variable.

The env-var approach was chosen over a `--name` CLI flag because:

- The session name must be visible inside every subprocess and hook that devbench
  spawns (the Claude Agent SDK subprocess, PreToolUse/PostToolUse shell hooks, the
  stop hook). A CLI flag passed to the top-level `devbench start` invocation would
  not propagate automatically to those subprocesses.
- The env-var is already the pattern used for other cross-process devbench
  configuration (`DEVBENCH_WORKSPACE_ROOT`, `DEVBENCH_CLAUDE_CREDENTIALS_FILE`, etc.).
- Operators can set `DEVBENCH_SESSION_NAME` in their shell profile or in the command
  prefix, and every devbench call in that shell inherits it without further plumbing.

Session names are validated to be non-empty alphanumeric strings with hyphens and
underscores allowed.

### 2. Per-session state directory

Each named session owns a state directory at
`<workspace>/.devbench/sessions/<name>/` containing:

| File | Content |
|------|---------|
| `pid` | OS process ID of the orchestrator process |
| `scope.json` | Session-scoped work-unit scope filter |
| `drain.signal` | Session-scoped drain request marker |
| `orchestrator.log` | Session-scoped orchestration log |
| `report.json` | Session-scoped report cache |
| `started_at` | ISO 8601 UTC timestamp of session start |
| `started_by` | OS username of the session owner |

The per-session `scope.json` and `drain.signal` take precedence over the workspace-root
equivalents when `DEVBENCH_SESSION_NAME` is set. The workspace-root files continue to
serve single-session operators unchanged.

The `orchestrator.log` is per-session and written in addition to (not instead of)
the aggregate `<workspace>/logs/orchestrator.log`, which remains for operators who
run `devbench status` without a `--session` filter.

### 3. Session registry

A JSON registry at `<workspace>/.devbench/sessions/registry.json` tracks all
registered sessions as an array of serialised `Session` objects. The registry enables:

- `devbench sessions` to enumerate active sessions without scanning the filesystem.
- Scope overlap detection before a new session starts.
- `devbench stop --session <name>` to locate the PID to signal.

Registry writes use a temp-then-rename pattern (`os.replace`) so readers never observe
a partial file. All registry mutations happen under the BACKLOG.lock (see section 5
below) to prevent concurrent `devbench start` invocations from creating duplicate
entries.

### 4. Liveness detection via os.kill(pid, 0)

Session liveness is determined by sending signal 0 to the recorded PID:

- Success: process is running -- session is ACTIVE.
- `ProcessLookupError` (ESRCH): process does not exist -- session is STALE.
- `PermissionError` (EPERM): process exists but we lack permission to signal it
  (cross-user) -- treated as ACTIVE to avoid false reaping.

The `sessions --cleanup` subcommand removes STALE session state directories and their
registry entries. The cleanup path removes only those dirs whose recorded PID is
confirmed dead; it never removes an ACTIVE or EPERM-protected session.

### 5. flock-serialised atomic claim arbitration

Every mutation of BACKLOG.md and the work-unit `.md` files is wrapped in an
exclusive `fcntl.flock` on `<workspace>/.devbench/BACKLOG.lock`. The claim sequence
under the lock is:

1. Acquire exclusive lock (`LOCK_EX | LOCK_NB` in a poll loop with a configurable
   timeout defaulting to 30 seconds).
2. Re-read the target work unit's current status under the lock.
3. If the status is no longer `in-queue` (or `in-progress` for a resume), raise
   `ClaimRaceError` and release the lock -- another session won the race; skip this
   unit and continue to the next candidate.
4. Write `in-progress` + stamp `owner_session` from `DEVBENCH_SESSION_NAME`.
5. Append the `[WU_CLAIMED] Set <id> to 'in-progress' session=<name>` audit comment.
6. Release the lock on context manager exit (normal or exceptional).

The `ClaimRaceError` exception is the designed signal that the claim was lost; the
orchestrator catches it, skips the unit silently, and re-invokes `get_parallel_candidates`
to find the next available unit.

The lock file is created lazily on first use and never deleted; it is a persistent
sentinel whose only purpose is to hold the advisory flock.

### 6. Scope overlap detection

Before registering a new session, devbench expands the new session's scope and
compares it against every active session's `scope` list. The `detect_scope_overlap`
function returns the sorted list of conflicting work-unit IDs.

Default behaviour (no `--allow-overlap` flag): fail fast with a clear error message
naming the conflicting IDs and the owning sessions. This prevents the most common
mistake of accidentally launching two sessions against the same tasks.

With `--allow-overlap`: emit a warning and proceed. The flock-serialised atomic claim
arbitration (section 5) handles the race deterministically -- exactly one session
claims each unit; the other raises `ClaimRaceError` and skips it. This mode is
useful for "best effort" session pairs where the operator intentionally allows
competition and wants the faster session to win.

### 7. Audit format extension

The `[WU_CLAIMED]` audit comment format defined in PR #187 is extended:

- Without `DEVBENCH_SESSION_NAME`: `[WU_CLAIMED] Set <id> to 'in-progress'` (unchanged).
- With `DEVBENCH_SESSION_NAME`: `[WU_CLAIMED] Set <id> to 'in-progress' session=<name>`.

The session suffix is appended in `cmd_claim` by reading `DEVBENCH_SESSION_NAME` at
call time rather than at import time, keeping the function testable with monkeypatching
and avoiding global state.

---

## Alternatives considered

### Alternative A: Multiple workspaces

Run two full copies of the workspace directory, each with its own BACKLOG.md and
work-unit files. Each `devbench start` invocation targets its own workspace.

**Rejected** because:

- Backlog state diverges: a task done in workspace A never reaches workspace B.
- `devbench status` shows only one workspace at a time; there is no aggregate view.
- The operator must manually partition tasks and keep both workspaces in sync after
  each session ends.
- Git branch management doubles: both workspaces commit to separate branches.

### Alternative B: Process-level locking with a simple lock file

Use a PID lock file (`<workspace>/.devbench/BACKLOG.pid`) checked at startup:
if another PID is present and alive, refuse to start.

**Rejected** because this is strictly single-session; it solves the corruption problem
but prevents the legitimate parallel-work-stream use case entirely.

### Alternative C: Database-backed claim table

Replace the BACKLOG.md + work-unit `.md` flat-file model with an SQLite table where
each row is a work unit and claims are row-level locks.

**Rejected** because:

- The BACKLOG.md flat-file format is a load-bearing user-visible artifact; operators
  read and edit it directly. Migrating to a DB-backed model would break the CLI,
  the human review workflow, and every existing `validate-backlog` check.
- `fcntl.flock` on a lock sentinel file achieves equivalent serialisation with no
  schema migration and no new runtime dependency.

### Alternative D: In-memory broker process

A long-running broker process serialises all claim requests through a queue.

**Rejected** because it requires a daemon that operators must start and manage
separately, and adds a single point of failure with no recovery path if the broker
crashes while sessions are running.

### Alternative E: File-based queue with atomic rename

Each session writes a "claim request" file; a separate process (or flock winner)
processes them in order.

**Rejected** because it adds latency (poll cycle) to the fast path and complicates
failure recovery. The `fcntl.flock` approach is simpler, well-understood, and has
no polling overhead on the happy path.

---

## Consequences

### Safety guarantees

- **No duplicate claims.** Two sessions racing to claim the same work unit resolve
  deterministically: the session that acquires the BACKLOG.lock first wins; the loser
  raises `ClaimRaceError` and skips to the next candidate. BACKLOG.md is never left
  in a state where the same unit appears `in-progress` under two sessions.
- **No scope file corruption.** Per-session `scope.json` and `drain.signal` paths
  are distinct; two sessions can never overwrite each other's scope or drain markers.
- **Drain isolation.** `devbench drain --session <name>` writes only to
  `<workspace>/.devbench/sessions/<name>/drain.signal`; other sessions are unaffected.
- **Log coherence.** Each session writes to its own `orchestrator.log`; the aggregate
  log still exists for operators not using `--session` filters.

### Operator playbook

See `docs/multi-session-runs.md` for the full operator walkthrough. Quick reference:

- Start two sessions: set `DEVBENCH_SESSION_NAME=alpha` and `DEVBENCH_SESSION_NAME=beta`
  in separate shells, each running `devbench start`.
- Check session liveness: `devbench sessions`.
- Remove stale sessions: `devbench sessions --cleanup`.
- Stop a session gracefully: `devbench stop --session alpha`.
- Drain a session: `devbench drain --session alpha`.

### What is out of scope

- **Cross-workspace sessions.** Sessions are workspace-scoped. Two workspaces cannot
  share a session.
- **Remote session coordination.** Named sessions are local-process only; there is no
  network protocol or distributed lock.
- **Window-stats and proposal lifecycle.** These remain workspace-shared (not
  per-session) per AC-192-16. The aggregate window-stats are the canonical
  performance record; per-session partitioning would make them meaningless.
- **Automatic PID reaping.** Sessions are only cleaned up when the operator explicitly
  calls `devbench sessions --cleanup`. Automatic reaping at startup would race with
  a session that starts just as another is classified as stale.

### Backwards compatibility

Single-session operators who never set `DEVBENCH_SESSION_NAME` see no behaviour
change: devbench uses the `default` session name implicitly, the per-session state
directory is `<workspace>/.devbench/sessions/default/`, and all workspace-root file
paths (`scope.json`, `drain.signal`, `orchestrator.log`) continue to be read and
written as before.

---

## References

- `src/devbench/session.py` -- `Session` dataclass, `SessionRegistry`, `flock_backlog`,
  `ClaimRaceError`, `detect_scope_overlap`.
- `src/devbench/cli.py` -- `cmd_claim` (flock + race check), `cmd_sessions`, `cmd_stop`,
  `cmd_start` (session registration, overlap detection).
- `src/devbench/scope.py` -- per-session `scope.json` path resolution.
- `src/devbench/drain.py` -- per-session `drain.signal` path resolution.
- `src/devbench/log_setup.py` -- dual-log routing (per-session + aggregate).
- `src/devbench/constants.py` -- all path constants and timeout defaults.
- `tests/test_session.py` -- unit tests with 100% line + branch coverage.
- `docs/multi-session-runs.md` -- operator playbook for running named sessions.
- Issue #192 -- named sessions feature specification.
- Spec section 4.4 -- detailed behavioural requirements.
- PR #187 -- original `[WU_CLAIMED]` audit format (extended by this feature).
- `docs/adr/24-quota-wait-and-resume.md` -- companion ADR for quota handling (shares the per-session state directory layout).
