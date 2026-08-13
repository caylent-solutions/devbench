# ADR-34: Transport-error bounded restart (#331)

**Status:** Accepted
**Date:** 2026-08-13
**Issue:** #331

## Context

`_drive_orchestrate_with_quota_resume` (`cli.py`) is the orchestrator's single
dispatch loop around every `cmd_start` iteration. Before this ADR it named
four outcomes: a clean SDK-loop exit, an operator-requested drain
(`_DrainRequested`), a quota / rate-limit signal (`_QuotaDetected`, ADR-24),
and an SDK message-loop stall (`_OrchestrateInactivityTimeout`, FR-17 /
db-262). Any OTHER exception raised across the SDK generator boundary -- an
upstream Claude Agent SDK defect, a transient transport failure, anything the
loop did not already have a name for -- propagated uncaught through
`asyncio.run` and terminated the daemon.

On 2026-08-12 this ended a multi-hour unattended run. The run had completed
Epic 13 in full and was mid-Epic-14 when the Claude Agent SDK's
`_internal/query.py` raised a bare
`Exception("Claude Code returned an error result: success")`
(upstream: anthropics/claude-agent-sdk-python#1203) -- a self-contradictory
result frame (`is_error=True`, `subtype="success"`, an empty `errors` list)
that devbench neither causes nor can fix. It was the second SDK-boundary
termination in twelve hours, which is what made the gap worth closing
structurally rather than case by case (issue #331).

## Decision

**1. Transport errors join the bounded-restart family alongside quota
resumes and inactivity restarts.** `_run`'s SDK message loop wraps ONLY the
`await asyncio.wait_for(agen.__anext__(), ...)` call: `StopAsyncIteration`
and `TimeoutError` keep their existing handling, and any other `Exception` is
re-raised as a new `_OrchestrateTransportError` -- a `BaseException`
subclass, not an `Exception` subclass, carrying the original as `__cause__`,
so it crosses the `asyncio.run` boundary uncaught the same way
`_QuotaDetected` and `_OrchestrateInactivityTimeout` already do.
`_drive_orchestrate_with_quota_resume` gains a matching
`except _OrchestrateTransportError` arm: it logs the verbatim upstream
exception at ERROR with the restart ordinal and the cap
(`[ORCHESTRATOR_TRANSPORT_ERROR] restart=<n> max=<cap>: <exc>`), then
consults `_should_restart_after_transport_error(restarts_used, max_resumes)`
-- the same shape as `_should_restart_after_inactivity_timeout`. That
predicate either logs `[ORCHESTRATOR_TRANSPORT_RESTART] attempt=<n> max=<cap>`
and restarts a fresh `run()` coroutine with no session state threaded between
iterations (mirroring the loop's existing no-state-threading contract for
quota and inactivity restarts), or logs
`[ORCHESTRATOR_TRANSPORT_RESTARTS_EXHAUSTED] max=<cap>` and re-raises,
preserving the original exception as `__cause__` for the legacy non-zero
exit. `_label_stop_reason` gains a `transport-error-restart-cap-exhausted`
class for the exhausted path, so the `orchestrator_stop` notification -- which
already fires unconditionally -- names the case instead of an unlabelled
crash. `devbench report` renders a `Transport restarts <n>` row
(`report.transport_restarts_line`, counting genuine
`[ORCHESTRATOR_TRANSPORT_RESTART]` audit lines) only when `n > 0`, so a clean
run's report stays byte-identical to today.

**2. The cap is shared, not duplicated (D-3).** `_should_restart_after_transport_error`
reuses `_resolve_max_quota_resumes()` -- the same `DEVBENCH_MAX_QUOTA_RESUMES`
resolver (default 1000) that already bounds quota resumes and inactivity
restarts -- rather than introducing a second config key. The transport
counter is tracked independently of the quota-resume and inactivity-restart
counters, so a transport restart never consumes either sibling's budget and
vice versa: three counters, one operator-tunable ceiling.

**3. Classification is structural, never message-based (D-4).** The observed
trigger's exception text was the literal string `success` -- upstream
rendered `subtype` as error text when its `errors` list was empty, producing
a message no sensible pattern would recognise as a failure. Deciding
retryability by parsing upstream exception text would be brittle exactly
when it matters most: a self-contradictory upstream frame is precisely the
case where the text lies about what happened. `_OrchestrateTransportError`
is raised because of *where* the exception was caught (the SDK generator
boundary), never because of *what* the exception says. This is recorded
explicitly because a message-matching fix is the obvious wrong turn for
whoever reads this next.

**4. Only the SDK generator boundary is wrapped, never the whole loop body
(D-5).** `_run`'s `except Exception` clause that raises
`_OrchestrateTransportError` sits immediately around
`agen.__anext__()` -- nothing else in the loop body is inside its `try`.
Wrapping the whole loop body would convert a genuine devbench defect (for
example a `TypeError` inside `_check_quota_and_drain`) into a silent restart
loop, trading a loud crash for a mystery. The narrow boundary keeps devbench
bugs loud and upstream hiccups survivable: a devbench-originated exception
raised elsewhere in the loop body is never wrapped and propagates unchanged.
`SystemExit`, `KeyboardInterrupt`, and `asyncio.CancelledError` are
`BaseException` subclasses that are not `Exception`, so the same `except
Exception` clause never matches them either -- they are never wrapped, and
SIGTERM / operator-interrupt behaviour is unchanged.

## Consequences

### Positive

- A transient upstream transport hiccup no longer ends an unattended
  overnight run: the loop restarts a fresh SDK session, bounded by the same
  cap operators already tune for quota and inactivity restarts.
- A permanent failure still fails fast: the cap is exhausted in a bounded
  number of attempts and `devbench start` exits non-zero with the verbatim
  final exception, exactly as an uncaught exception did before this ADR --
  no infinite retry loop, no swallowed defect.
- Operators gain visibility on two independent surfaces: the
  `orchestrator_stop` notification's `transport-error-restart-cap-exhausted`
  label, and `devbench report`'s conditional `Transport restarts <n>` row.
- No new configuration surface: the classification boundary is a code-level
  invariant and the cap is an existing resolver, so there is nothing new to
  set wrong.

### Negative

- A restart re-claims the interrupted work unit from the on-disk backlog
  rather than resuming the SDK conversation mid-turn; partial turn state is
  not recovered. This mirrors the existing quota- and inactivity-restart
  behaviour and is not a new limitation introduced here.
- The bounded restart cannot distinguish a genuinely transient upstream
  hiccup from a persistent upstream regression until the cap is exhausted --
  by design (D-4): message-based early detection was rejected as brittle.

## Alternatives considered

### Message-pattern matching on the upstream exception text

Rejected (D-4). The observed trigger's text was the literal word `success`;
no pattern-matching rule would have recognised it as an error, and a rule
broad enough to catch it would also catch genuine devbench defects that
happen to print similar text.

### Wrapping the entire `_run` loop body, not just the SDK boundary

Rejected (D-5). A defect inside devbench's own per-message handling (for
example `_check_quota_and_drain`) would be reclassified as a retryable
transport error and silently restarted instead of crashing loudly, hiding
genuine bugs behind a bounded-restart mask.

### A dedicated transport-restart cap / config key

Rejected (D-3). Three independent counters bounded by one operator-visible
cap (`DEVBENCH_MAX_QUOTA_RESUMES`) is simpler to reason about and operate
than three separate knobs, and the workspace standard discourages
configuration surface that can only be set wrong.

## References

- Issue #331: SDK transport error termination.
- Upstream: anthropics/claude-agent-sdk-python#1203 (the contradictory
  `is_error=True` / `subtype="success"` / empty `errors` result frame).
- [ADR-24: Quota Wait-and-Resume](24-quota-wait-and-resume.md) -- the
  precedent bounded-restart shape this ADR mirrors.
- `spec/sdk-transport-resilience.md`
- `docs/cli-reference.md` -- `start`'s documented recovery paths and
  `report`'s `Transport restarts` row.
