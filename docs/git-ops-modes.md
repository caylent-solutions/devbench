# Git-ops execution modes

DevBench's `cmd_git_ops` supports three mutually-exclusive execution
modes. Pick the mode that matches the operator's review posture and
PR-granularity needs.

For the orchestrator's interactive vs non-interactive run-time modes,
see [`docs/execution-modes.md`](execution-modes.md). This doc is
specifically about the per-task git workflow after reviews pass.

## At a glance

| Mode | YAML knobs | Per-task PR? | Auto-merge? | Use when... |
|------|------------|--------------|-------------|-------------|
| Multi-PR (default) | _none_ | Yes | Yes (on green CI) | You trust judges + CI to gate; no human review needed per PR. |
| Single-branch + defer-PR | `git_ops.single_branch: <branch>` + `git_ops.defer_pr: true` | No (one batch PR) | Yes (via `git-ops-finalize`) | You want one PR for the whole batch (e.g., dependent tasks must land atomically). |
| Pause-before-merge (#101) | `git_ops.pause_before_merge: true` | Yes | No -- waits for human merge | You want per-task granularity AND per-PR human gating without blocking the orchestrator. |

The schema rejects the two illegal combinations at config load:

- `pause_before_merge: true` + `defer_pr: true` -- mutually exclusive.
- `pause_before_merge: true` + `single_branch: <name>` -- mutually
  exclusive (single-branch has no per-unit branch to PR from).

## Decision tree

```
                        +-----------------------+
                        | Need a PR per task?   |
                        +-----------------------+
                          |                  |
                       Yes|                  |No
                          v                  v
            +-------------------------+   +----------------+
            | Need human review per   |   | single-branch +|
            | PR before merge?        |   | defer_pr: true |
            +-------------------------+   +----------------+
              |                |
           Yes|                |No
              v                v
   +-----------------+   +-------------+
   | pause_before_   |   | Multi-PR    |
   | merge: true     |   | (default -- |
   | (#101)          |   | no YAML)    |
   +-----------------+   +-------------+
```

## Multi-PR mode (default)

No YAML required. Each work unit's `git-ops` invocation:

1. Commits to `backlog/<id-lower>` (per-unit branch).
2. Pushes + creates a PR.
3. Waits for green CI.
4. (Optional) polls for PR-bot review feedback per `pr_review_resolution`.
5. Merges via `gh pr merge`.
6. Updates the parent submodule pointer if `update_submodule: true`.

The work unit transitions `in-progress` -> `done` in one shot.

## Single-branch + defer-PR mode

```yaml
git_ops:
  single_branch: feat/my-batch-branch
  defer_pr: true
```

Every work unit commits to the same branch via `commit_local()` (no push,
no PR). After the last work unit completes, run
`devbench git-ops-finalize <repo>` to push the branch and create the
single batch PR for review.

Use when:
- The whole batch must land atomically (interlocking changes).
- One review for the whole epic / feature is more efficient than per-task
  review.
- Per-PR review noise is not worth the bookkeeping cost.

## Pause-before-merge mode (#101)

```yaml
git_ops:
  pause_before_merge: true
```

Each work unit's `git-ops` invocation:

1. Commits to `backlog/<id-lower>` (per-unit branch).
2. Pushes + creates a PR.
3. Waits for green CI.
4. (Optional) polls for PR-bot review feedback per `pr_review_resolution`.
5. **Stops here.** Logs `[PR_AWAITING_MERGE]` audit comment, transitions
   the work unit to `in-review`, returns rc=0.

The orchestrator's loop reconciles `in-review` tasks at the top of each
iteration via `devbench check-merge <id>`:

- **PR merged externally** -> promote to `done` via the done-gate.
- **PR closed without merge** -> transition to `blocked` with an audit
  comment.
- **Still open** -> no-op; orchestrator moves on to other actionable
  units.

The orchestrator does NOT block waiting for any single human merge; the
loop keeps processing other actionable units. When the only remaining
non-done units are in-review, the loop prints a status summary naming
"N tasks awaiting human merge" and exits 0.

Use when:
- Per-task PR granularity is needed (each work unit is its own
  reviewable, mergeable GitHub artifact).
- Per-task human review gating is needed (judges + CI alone are
  insufficient; a human must skim the PR before merge).
- Multi-PR mode's auto-merge is too eager; single-branch's batch PR is
  too coarse.

### Configuration overrides

| Layer | Knob |
|-------|------|
| YAML | `git_ops.pause_before_merge: bool` |
| Env | `JUDGE_PAUSE_BEFORE_MERGE` (truthy / falsy) |

Both layers compose with the standard env > YAML > default precedence.

## See also

- [ADR-13](adr/13-pause-before-merge.md) -- pause-before-merge design
  decision + consequences.
- [`plugin/devbench/skills/orchestrate/SKILL.md`](../plugin/devbench/skills/orchestrate/SKILL.md) -- step 1b reconciliation
  loop and step 8 mode dispatch.
- [`docs/cli-reference.md`](cli-reference.md) -- the `git-ops` and
  `check-merge` command references.
