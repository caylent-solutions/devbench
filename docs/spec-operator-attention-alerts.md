# SPEC: Operator-attention alerting surface

> **Status:** Future work. Design sketch only; no implementation.
> Tracked via the [open GitHub issues](https://github.com/caylent-solutions/devbench/issues) labelled `enhancement`. Referenced from [ADR-10](adr/10-multi-target-proposal-wiring.md).

This document captures the design context for a future devbench feature that proactively notifies a human when a decision-requiring item exists. It ships as a spec so the reasoning and trade-offs are preserved when an implementer eventually picks it up. Nothing in this file is a decision; every option enumerated below is still open.

## Problem

Devbench today surfaces two classes of items that require a human decision:

1. **Proposed drafts** -- work units at `## Status: proposed` that the operator must `promote-proposal` or `reject-proposal`. Task-factory materialises these from proposal JSONs; the orchestrator's `devbench next` intentionally skips them (they are inert by design).
2. **Blocked tasks classified as `NEEDS_OPERATOR_ATTENTION`** -- tasks in `## Status: blocked` whose `[BLOCKED_PENDING_PROPOSAL]` markers are absent, incomplete, or already terminal. See [ADR-10 slice G](adr/10-multi-target-proposal-wiring.md) for the `classify_blocked_task` contract.

The operator discovers both of these today by polling: running `devbench status`, `devbench report`, and `devbench list-proposals` on some cadence the operator chooses. A long-running orchestrate session can accumulate hours of latency before the operator glances at the terminal and realises something is waiting. The polling works, but there is no active signal telling the operator when it is worth looking.

## What counts as "operator attention needed"

A future implementation MUST treat these two sources as the complete definition (additions require a separate spec revision):

- Every work unit whose `## Status` is `proposed`.
- Every work unit whose `## Status` is `blocked` AND whose `classify_blocked_task` result is `BlockedTaskState.NEEDS_OPERATOR_ATTENTION`.

Both classifiers already exist as reusable helpers in `src/devbench/backlog/proposal.py`, so the implementation is a composition, not new logic.

## Design sketch: three reasonable surfaces

A future implementer picks one. The trade-offs matter more than the choice, so the spec lists all three with their strengths and weaknesses.

### Option A -- new CLI command `devbench check-attention`

```
uv run devbench check-attention [--since <ISO-8601>] [--format text|json]
```

Prints a structured summary of only the attention items. Pairs naturally with `watch -n 60 uv run devbench check-attention` in a second terminal.

**Pros.**
- No new runtime surface. Operators add a second terminal; no orchestrator change.
- Clean separation: everything the operator sees is on purpose.
- Easy to machine-parse with `--format json` for downstream tooling.

**Cons.**
- Polling model. Still requires the operator to either run the command or set up their own `watch` loop.
- Every poll re-reads the full backlog, which is cheap today but grows with backlog size.

### Option B -- alert file surface at `<workspace>/.devbench/attention.jsonl`

The orchestrate SKILL's step-0 tick writes one line per attention item at the end of every loop iteration. Operators use `inotifywait`, `fswatch`, or equivalent to fire a desktop notification / Slack message when the file mtime changes.

**Pros.**
- Event-driven. The notification fires the moment new items appear, not on a polling cadence.
- Works with any operator notification stack (tail the file to Slack, send a desktop toast, ping a web hook).
- State persists across orchestrator restarts.

**Cons.**
- Another file to maintain invariants on. The SKILL has to rewrite atomically to avoid readers seeing a half-written file.
- Crosses the line between "orchestrator business" and "operator UX"; the SKILL is not otherwise in the business of cross-process signalling.

### Option C -- `devbench watch` panel extension

Adds a new "Attention needed (N)" panel to the existing `devbench watch` dashboard (see [watch-activity.md](watch-activity.md)). The panel lists every attention item; when N is zero, the panel renders a short green "Nothing needs your attention" line.

**Pros.**
- Zero new commands.
- Operators already running `watch` see it without additional setup.
- Read-only, cannot race with the orchestrator's own writes.

**Cons.**
- Only helps operators who already run `watch`. Operators who monitor via `tail -f` on the orchestrator log see nothing.
- Mixes two concerns (current orchestration state + operator decision queue) in one surface.

## Thresholds and noise control

Regardless of the chosen surface, the implementation MUST support a "what is new since I last looked" filter so an operator who just triaged ten items is not paged about them again on the next poll. The open question below (see "Open questions") covers how to represent "last looked".

Every item needing attention is treated as equal priority. There is intentionally no "urgent / not urgent" subdivision because the operator is the only source of the decision; ranking items presupposes devbench knows something about the cost of waiting, which it does not.

## Integration with existing surfaces

Whatever surface lands, the existing surfaces stay:

- `devbench status` keeps its `Blocked (auto)` + `Blocked (attn)` rows and the always-visible `Un-materialised` row.
- `devbench report` keeps its three blocked panels (auto-clearing via proposal, auto-recovery in flight, needs operator attention) and the pending-materialisation panel. Issue #168 (3-panel surfacing) extended panel 3 to also include HOLD work units and BLOCKED tasks whose marker target is in HOLD; both classes are operator-must-act and route into the existing panel with sub-case-specific inline annotations (`[HOLD]`, `[HOLD: <id>]`, `[no marker]`, `[marker target unknown: <id>]`, `[marker targets all terminal]`).
- `devbench list-proposals` keeps its per-task `[state]` labels.

The attention-alert surface is ADDITIVE. Removing it does not lose operator-visible information; it only removes the proactive signal.

## Open questions for the implementation phase

These are not rhetorical. A future PR answering them concretely is how this spec ships.

1. **How does the surface differentiate "new since last check" from "still waiting"?** Options: a state file persisted per workspace; an operator-supplied `--since <timestamp>` flag; the SKILL's own session-boundary detection; the orchestrator log's last `Set X to` line; a per-item "first-seen" timestamp embedded in the backlog. Trade-offs: persistence complexity vs operator cognitive load.
2. **How does a multi-workspace operator keep alerts separate?** Options: include the workspace root in every output line; scope state to `$JUDGE_WORKSPACE_ROOT`; require `--workspace <path>` on every call; enforce one-workspace-per-terminal-session via env.
3. **Should `sweep-proposals` step 0 also update the alert surface?** If yes, un-materialised JSONs get signalled alongside proposed drafts. If no, the operator has to poll `list-proposals` separately. Trade-off: signal completeness vs surface complexity.
4. **Is there value in a `--format=json | --format=text` split on `check-attention` output?** JSON is natural for downstream tooling (Slack bots, CI dashboards); text is natural for terminal readers. Implementing both is cheap but adds a small invariant to maintain.
5. **Should the attention surface write an audit comment on items as it surfaces them?** If yes, every item picks up a `[ATTENTION_FLAGGED]` line the first time it is surfaced -- useful for audit, but clutters the Comments section for operators who prefer a clean file. If no, the surface is read-only (matches current surfaces like `watch` and `hook-tail`).

## Why not now

The attention-alert surface is orthogonal to the multi-target wiring that prompted this document. Shipping them together would conflate two reviewable diffs and spread the test burden across two unrelated systems. The spec file preserves the design context so a future implementer can start from the options above instead of rediscovering them.

## Related files (when this ships)

- `src/devbench/backlog/proposal.py::classify_proposed_task` + `::classify_blocked_task` -- the two helpers the surface composes.
- `plugin/devbench/skills/orchestrate/SKILL.md` -- option B (alert file) would add an end-of-tick write step.
- `src/devbench/activity.py` -- option C (watch panel) lives here.
- `docs/adr/XX-operator-attention-alerts.md` -- the ADR that records which option was chosen.
- `docs/cli-reference.md` -- new `check-attention` entry if option A lands.
