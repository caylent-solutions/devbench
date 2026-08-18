# ADR-34: Transport-error bounded restart (#331)

**Status:** Accepted; **amended 2026-08-18** -- decision D-3 (shared cap) is
superseded, and the alternative "A dedicated transport-restart cap / config
key" it rejected is now adopted. See [Amendment](#amendment-2026-08-18-dedicated-cap-and-backoff-d-3-superseded).
**Date:** 2026-08-13
**Issue:** #331

## Amendment (2026-08-18): dedicated cap and backoff, D-3 superseded

The shared-cap decision below was made on the assumption -- stated in the
original Consequences and in `constants.py` -- that a transport fault is rare
and self-throttling. Production disproved it.

A persistent transport fault produced ~1000 restarts in 39 minutes: the loop
retried with **no delay at all**, so the 1000-restart budget sized for quota
windows was consumed as fast as the SDK could reject a session. The cap was
exhausted, the run ended, and the daemon exited with no operator signal until
someone read the log three days later. The failure mode the bounded restart was
designed to survive is exactly what killed the run.

Two changes follow:

1. **Transport restarts get their own cap.** `MAX_TRANSPORT_RESTARTS`
   (`DEVBENCH_MAX_TRANSPORT_RESTARTS` / `orchestrate.max_transport_restarts`,
   default **10**) replaces the borrowed `_resolve_max_quota_resumes()`
   ceiling. It follows `DEFAULT_MAX_PREMATURE_TURN_END_RESTARTS`'s cost-guard
   reasoning: a fault that repeats ten times consecutively is not flapping, it
   is down, and the correct response is to fail fast and loudly.
2. **Restarts are paced by exponential backoff.**
   `base * 2 ** restarts_already_done`, clamped to a ceiling
   (`orchestrate.transport_restart_backoff_base_seconds` /
   `..._max_seconds`, defaults 1.0s and 60.0s). The chosen delay is recorded
   in the audit line as `backoff=<n>s`.

The premise that made the shared cap defensible -- that all three failure modes
self-throttle -- was simply false for transport. A quota window must elapse and
an inactivity restart costs a full timeout window; a transport fault costs
nothing and can recur immediately. The cap was never the safeguard; the absence
of a delay was the defect.

The counter-independence property from D-3 is retained unchanged: transport,
quota, and inactivity restarts still count separately and never consume each
other's budget. What changed is that the transport counter now has its own
ceiling and its own pacing.

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
run's report stays byte-identical to today. *(Amended 2026-08-18: the row now
counts per window and labels each count -- `<n> all-time / <n> session /
<n> this run` -- because a single lifetime total sitting above windowed
columns was read as a run-scoped number. The `n > 0` suppression rule is
unchanged.)*

**2. The cap is shared, not duplicated (D-3).** *(Superseded by the 2026-08-18 amendment above -- the cap is now dedicated and the restarts are paced. Retained for the record.)* `_should_restart_after_transport_error`
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
  overnight run: the loop restarts a fresh SDK session, bounded by its own
  cap and spaced by exponential backoff (amended 2026-08-18; originally the
  shared quota/inactivity cap with no delay).
- A permanent failure still fails fast: the cap is exhausted in a bounded
  number of attempts and `devbench start` exits non-zero with the verbatim
  final exception, exactly as an uncaught exception did before this ADR --
  no infinite retry loop, no swallowed defect.
- Operators gain visibility on two independent surfaces: the
  `orchestrator_stop` notification's `transport-error-restart-cap-exhausted`
  label, and `devbench report`'s conditional `Transport restarts` row, which
  counts per window (all-time / session / this run) and labels each count.
- ~~No new configuration surface~~ (amended 2026-08-18): the classification
  boundary remains a code-level invariant, but the cap and its backoff are now
  three optional `orchestrate.*` keys. The added surface is judged worth it --
  the field failure was not something an operator could tune their way out of,
  because the only knob was shared with quota resumes and moving it would have
  broken those. Every key is optional and resolves env > YAML > default.

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

Originally rejected (D-3); **adopted in the 2026-08-18 amendment.** The
original reasoning -- that one operator-visible cap is simpler than three
knobs, and that configuration surface can only be set wrong -- held only while
the shared cap was actually a safeguard for transport faults. It was not: with
no delay between attempts the ceiling was reachable in minutes, so the single
knob offered no protection and could not be lowered without also shortening
genuine quota recovery. Simplicity that does not bound the failure it names is
not simplicity.

## References

- Issue #331: SDK transport error termination.
- Upstream: anthropics/claude-agent-sdk-python#1203 (the contradictory
  `is_error=True` / `subtype="success"` / empty `errors` result frame).
- [ADR-24: Quota Wait-and-Resume](24-quota-wait-and-resume.md) -- the
  precedent bounded-restart shape this ADR mirrors.
- `spec/sdk-transport-resilience.md`
- `docs/cli-reference.md` -- `start`'s documented recovery paths and
  `report`'s `Transport restarts` row.
