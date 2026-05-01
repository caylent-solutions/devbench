# ADR-13: Pause-before-merge mode for per-PR human review

## Status

Accepted -- shipped in v-next.

## Context

DevBench's two existing git-ops modes left a gap that issue #101 surfaced:

- **Multi-PR mode (default)**: one PR per work unit, auto-merged on green CI.
  No human-in-the-loop gating. Judges + CI catch standards, scope, and
  correctness issues, but nothing pauses for an operator to skim each PR.
- **Single-branch + `defer_pr: true`**: every work unit's commits accumulate
  on one shared branch, with a single PR opened by `git-ops-finalize` after
  every unit is done. Per-task PR granularity is lost; the operator reviews
  one batch PR at the end.

Operators who want **per-task granularity** (each work unit is its own
reviewable, mergeable GitHub artifact) AND **per-task human gating**
(nothing merges until the human clicks) had no mode to express that.
Catching scope creep post-hoc in single-branch mode requires unwinding an
entire batch; auto-merge on multi-PR leaves no review surface.

## Decision

Introduce a third opt-in `git_ops` mode: `pause_before_merge: bool`. When
enabled, `cmd_git_ops` pushes the PR + waits for green CI then transitions
the work unit to `in-review` (instead of merging). The orchestrator's loop
reconciles in-review tasks via a new `cmd_check_merge` CLI on every
iteration:

- **PR merged externally**: promote to `done` via the existing done-gate
  (every required judge must have passed).
- **PR closed without merge**: transition to `blocked` with an audit comment
  naming the PR.
- **Still open**: no-op; the orchestrator moves on to other actionable
  units while the human reviews this one at their own pace.

The orchestrator does NOT block waiting for any single human merge; the
loop continues processing other actionable units, picking each in-review
task back up only when its state changes. Idle exit: when the only
remaining non-done units are in-review, the loop prints a status summary
naming "N tasks awaiting human merge" and exits 0.

### Configuration

```yaml
git_ops:
  pause_before_merge: true
```

Schema validation rejects two combinations as mutually exclusive:

- `pause_before_merge: true` + `defer_pr: true` -- defer_pr defers PR
  creation; pause_before_merge pauses after PR creation. Mutually
  exclusive by definition.
- `pause_before_merge: true` + `single_branch: <name>` -- single-branch
  mode puts every work unit's commits on one branch; there is no per-unit
  branch to create a PR from.

Override via env var: `JUDGE_PAUSE_BEFORE_MERGE=1` (or any
`_resolve_bool`-truthy value).

### State machine

This mode reuses the existing `WorkUnitStatus.IN_REVIEW` enum value
(previously defined but unused in the live orchestration flow):

```
in-progress --(pause_before_merge=true, git-ops pushed PR, CI green)--> in-review
in-review   --(PR merged externally, next loop iteration)-------------> done
in-review   --(PR closed without merge)------------------------------> blocked
```

### CLI surface

- **`cmd_git_ops <id>`** branches on the resolved
  `PAUSE_BEFORE_MERGE` config:
  - `False` (default): existing behaviour. Push, wait CI, merge.
  - `True`: push, wait CI, log `[PR_AWAITING_MERGE]` audit comment, set
    status to `in-review`, return rc=0 with JSON `{"status":
    "in-review", "pr_number": ..., "pr_url": ..., "mode":
    "pause-before-merge"}`.
- **`cmd_check_merge <id>`** queries `gh pr list --head <branch> --json
  number,state,merged,url` and dispatches per the rules above. Returns
  rc=0 in every normal case (merged / closed / open / no-pr-found); rc=1
  only on hard failure (gh API failure, malformed JSON, done-gate
  refusal).

### Orchestrator skill

`plugin/devbench/skills/orchestrate/SKILL.md` gains a new step 1b at the
top of each loop iteration that enumerates `in-review` tasks via
`devbench status --detail` and runs `devbench check-merge <id>` against
each. Skipped entirely when `pause_before_merge` is unset / false.

### Why `in-review` (not a new state)

The enum value `WorkUnitStatus.IN_REVIEW` already existed, valid in
`VALID_STATUSES`, but no live arc set it. Repurposing it for this mode
requires no enum change, no schema change, and no migration of existing
backlogs. It also reads naturally to a human glancing at a backlog: "in
review" = "the human is reviewing this PR".

## Consequences

**Positive**

- Per-task PR granularity retained.
- Per-PR human gating without blocking the orchestrator on any single
  human decision; other actionable units continue processing.
- No state-machine surgery; reuses an existing-but-unused enum value.
- Schema validation prevents the two illegal mode combinations at config
  load.
- Default off; existing flows are unchanged.

**Negative**

- Operators using this mode pay a per-loop polling cost on `gh pr list`
  for every in-review task. Bounded by however many in-review tasks
  exist at once, typically <10 in practice.
- The `IN_REVIEW` state is now load-bearing; any future change to the
  enum or status workflow must consider both this mode and the
  pre-existing review-supervisor flow that does not transition tasks to
  in-review.

**Out of scope**

- A `--wait-for-merge` flag on the orchestrate skill that polls GitHub
  indefinitely. Phase 2 enhancement; the current design exits cleanly
  when no actionable units remain.
- Webhook-driven merge notification (would require a listener service;
  much bigger change).
- Auto-reopening a PR closed without merge. The human decides whether to
  re-queue the work unit -- declining the auto-block keeps the operator
  in control of the recovery path.
