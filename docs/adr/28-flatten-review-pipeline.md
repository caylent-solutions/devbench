# ADR-28: Flatten the code-review pipeline (no nested sub-agents)

**Status:** Accepted
**Date:** 2026-06-10

---

## Context

A real devbench run (the tools-telemetry IaC backlog) surfaced unit
`E9-F1-S1-T5` as a `RUNTIME_DEGRADATION` that no restart could clear. A grounded
root-cause analysis (code plus the official Claude Agent SDK docs) found a
structural defect, not transient flakiness.

The review pipeline was two levels deep on the review leg:

```
main-thread orchestrate skill
  |- executor                         (first-level sub-agent -- legal)
  |- review-supervisor                (first-level sub-agent -- legal)
  |     `- code/test/doc/changes      (SECOND-LEVEL spawn -- SDK-FORBIDDEN)
  |- security-reviewer                (first-level -- legal)
  `- iac-deploy-reviewer              (first-level -- legal)
```

`review-supervisor` is itself a first-level sub-agent, yet its frontmatter
declared `tools: Bash, Agent(code-reviewer, test-reviewer, doc-reviewer,
changes-manifest)` and it was instructed to spawn those four via the Agent tool.

The Claude Agent SDK is explicit that this cannot work:

> "Subagents cannot spawn their own subagents. Don't include `Agent` in a
> subagent's `tools` array."
>
> "`Agent(...)` has no effect in subagent definitions."

(code.claude.com/docs/en/agent-sdk/subagents; .../sub-agents;
anthropics/claude-code #19077, #61993, #31977. SDK version
`claude-agent-sdk 0.2.91`.)

So the supervisor's fan-out silently no-ops. The supervisor's own Step 0
self-check detected the missing Agent tool and emitted an
`agent-tool-unavailable` audit comment, which the classifier buckets as
`RUNTIME_DEGRADATION` (priority-0) and the `make start` exit-42 loop treats as
transient -- but a restart can never fix a structural nesting violation.

Two further failure modes rode on top of the same root cause:

- **Fabricated PASS (integrity hole).** Step 0 was supposed to fail-closed.
  Instead the model improvised an undocumented "orchestrator-dispatched,
  verdicts persisted in log-only mode" path (a phrase that exists nowhere in
  devbench) and declared all reviewers PASS. A degraded review could emit fake
  passes.
- **Per-round token gap.** The H3 guard only checked that
  `DEVBENCH_REVIEW_ROUND_TOKEN` was non-empty. Its transport is `shell.env` via
  `BASH_ENV`. A stale leftover token in `shell.env` masked a missing fresh
  injection (the guard could not tell fresh from stale).

---

## Decision

**Flatten the review pipeline so no sub-agent spawns sub-agents.** The
main-thread `orchestrate` skill dispatches the four `review_team` reviewers
**directly** (first-level, in parallel), exactly as it already dispatches
`security-reviewer` and `iac-deploy-reviewer`:

```
main-thread orchestrate skill
  |- executor
  |- code-reviewer / test-reviewer / doc-reviewer / changes-manifest  (parallel, first-level)
  |- security-reviewer
  `- iac-deploy-reviewer
```

Concrete changes:

1. **`orchestrate/SKILL.md` step 5** dispatches the four
   `devbench-orchestrate:code-reviewer`, `:test-reviewer`, `:doc-reviewer`,
   `:changes-manifest` agents in a single response (parallel), injecting the
   per-round token into each. The retry loop (step 6) re-dispatches the four,
   not the supervisor.

2. **Fail-closed pass determination.** With no aggregator to improvise a PASS,
   the skill determines pass/fail **solely from the canonical verdict lines for
   the current round** (`[judge/<name>] [REVIEW_PASS]`, read via `read-unit`),
   never from reviewer prose or the JSON envelope. A missing required verdict is
   a REVIEW_FAIL, never an inferred pass: a reviewer that did not run leaves no
   verdict line.

3. **`guard-verdict-format.sh` allowlist extended.** The four reviewers now
   present their own `agent_type` (`devbench-orchestrate:code-reviewer`, etc.),
   so each is added to `ALLOWED_REVIEWER_AGENT_TYPES`. The H3 two-factor
   (allowed agent_type + token) is preserved.

4. **Round-aware token (closes the masking).** The skill writes
   `DEVBENCH_REVIEW_ROUND_TOKEN=<unit-id>-r<n>-<rand>` before each round and
   clears it after; the guard now requires the token to be **scoped to the unit
   under review** (prefix `<unit-id>-`), so a stale leftover token from a
   different unit's round can never satisfy this unit's verdict.

5. **`review-supervisor` demoted.** Its frontmatter drops the `Agent(...)` tool
   (the literal SDK fix) and its body becomes an inert deprecation notice. The
   file is retained so existing references in `plugin_shadow.py`,
   `config-schema.json`, `config.py`, `config_loader.py`, and `activity.py` (and
   their tests) keep resolving. It dispatches nothing and MUST NOT be invoked.

6. **The four `review_team` agents** each gained the H3 "Token requirement"
   section (they are now the direct token consumers) and their closing line
   reads "the orchestrate skill reads this JSON" rather than "the supervisor".

**The done-gate is unchanged.** `_CANONICAL_VERDICT_RE` matches
`[judge/<name>]` lines and `_last_round_all_passed` counts
`[judge/<name>] [REVIEW_PASS]` regardless of *which* agent called `log-verdict`.
The four reviewers already self-logged their canonical verdicts before this
change, so flattening required no done-gate change -- only the guard allowlist,
the skill dispatch, and the supervisor demotion.

---

## Why this is the right fix

The degradation is not transient SDK flakiness a restart clears -- it is the
deterministic, documented consequence of an Agent fan-out placed one level too
deep. The only durable fix is to remove the nesting. Flattening also closes the
fabricated-PASS hole (no aggregator remains to improvise a verdict) and, paired
with the round-aware token, the staleness that masked the incident.

## Rejected alternatives

- **Main-thread logging of reviewer verdicts.** Have the orchestrate skill parse
  each reviewer's JSON envelope and call `log-verdict` itself. Rejected: the
  reviewers already self-log (this was never the gap), it would re-introduce an
  aggregation step that can drift from the reviewers' actual findings, and it
  would re-create the fabricated-PASS surface this ADR removes.
- **Keep the supervisor and "fix" the nesting at runtime.** There is no runtime
  fix -- the SDK forbids the nesting by design; a sub-agent's `Agent` tool is
  inert.

## Deferred (not in this change)

Moving the per-round token off `shell.env`/`BASH_ENV` onto a per-round
`.devbench/` sidecar file, or the HMAC token scheme ADR-25 deferred. The
round-aware unit-id scoping is the proportionate hardening for now; the sidecar
move is a larger transport change tracked separately.
