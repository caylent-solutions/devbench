# Git-ops execution modes

DevBench's `cmd_git_ops` supports four mutually-exclusive execution
modes. Pick the mode that matches the operator's review posture and
PR-granularity needs.

For the orchestrator's interactive vs non-interactive run-time modes,
see [`docs/execution-modes.md`](execution-modes.md). This doc is
specifically about the per-task git workflow after reviews pass.

## At a glance

| Mode | YAML knobs | Per-task PR? | Auto-merge? | Use when... |
|------|------------|--------------|-------------|-------------|
| Multi-PR (default) | _none_ | Yes | Yes (on green CI) | You trust judges + CI to gate; no human review needed per PR. |
| Single-branch + defer-PR | `git_ops.single_branch: <branch>` + `git_ops.defer_pr: true` | No (one batch PR) | No by default; opt-in via `git_ops.auto_merge: true` (requires `auto_finalize: true`; CI watcher must report GREEN before merge fires) | You want one PR for the whole batch (e.g., dependent tasks must land atomically). |
| Pause-before-merge (#101) | `git_ops.pause_before_merge: true` | Yes | No -- waits for human merge | You want per-task granularity AND per-PR human gating without blocking the orchestrator. |
| Local-only | `git_ops.single_branch: <branch>` + `git_ops.defer_pr: true` + `git_ops.local_only: true` | No (no PR ever) | N/A (no remote) | Operational workflows -- AWS teardowns, evidence capture, scheduled audits -- where devbench drives work but the target "repo" has no GitHub remote and never pushes. See [`operational-work.md`](operational-work.md). |

The schema rejects the illegal combinations at config load:

- `pause_before_merge: true` + `defer_pr: true` -- mutually exclusive.
- `pause_before_merge: true` + `single_branch: <name>` -- mutually
  exclusive (single-branch has no per-unit branch to PR from).
- `local_only: true` without `defer_pr: true` -- a local-only repo has
  no remote to push to; PR creation is meaningless.
- `local_only: true` + `pause_before_merge: true` -- there is no PR to
  pause before merging.
- `local_only: true` + any `repos:` entry without an explicit
  `default_branch:` -- there is no `origin/HEAD` to fall back to.

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

### Post-finalize CI watching and failure resolution

After `git-ops-finalize` creates the batch PR it calls
`GitOpsService.wait_for_checks_and_classify(pr_url, repo_path)`, which
wraps `gh pr checks --watch` and classifies the outcome into one of four
`CIResult` values:

- `CIResult.GREEN` -- every check passed. `git-ops-finalize` logs
  `[CI_GREEN]` and returns rc=0. **The PR is left open for human merge
  by default.** When `git_ops.auto_merge: true` is set (requires
  `git_ops.auto_finalize: true`), the orchestrator's auto-merge check
  (SKILL.md step 11c) invokes `gh pr merge` automatically once the CI
  watcher reports GREEN.

- `CIResult.FAILED_KNOWN_TASK(task_id)` -- the failing job's log
  contains exactly one `[E<n>-...-T<n>]` commit marker that traces the
  failure to a single task. `git-ops-finalize` writes a recovery-proposal
  JSON at `<workspace>/.devbench/proposals/<task_id>.json`, transitions
  the named task to `blocked` with a `[CI_FAILED_BATCH_PR] <log_path>`
  audit comment, and returns rc=2. Cascade-cap interaction: if the
  offending task is already at cascade depth N-1 (controlled by
  `orchestrate.max_cascade_depth`), the recovery proposal is skipped and
  the task transitions to blocked with a `[CI_FAILED_CASCADE_CAPPED]` audit
  comment (which the blocked-task classifier resolves to
  `OPERATOR_ACTION_REQUIRED`).

- `CIResult.FAILED_UNKNOWN` -- a failure is detected but cannot be
  attributed to a single task (no marker present, multiple distinct
  markers found, or the job log was unreachable). `git-ops-finalize`
  transitions the most-recent in-review/done task to `blocked` with the
  same `[CI_FAILED_BATCH_PR]` audit comment shape and returns rc=2.

- `CIResult.TIMEOUT` -- `gh pr checks --watch` exceeded the configured
  timeout. `git-ops-finalize` logs `[CI_WATCH_TIMEOUT]` and returns rc=2
  without changing any task statuses; the operator decides the next step.

### Auto-finalize and auto-merge toggles

Two optional boolean knobs control whether the orchestrate skill invokes
`git-ops-finalize` automatically and whether it merges the batch PR after
CI turns green.

| `auto_finalize` | `auto_merge` | Behaviour |
|-----------------|--------------|-----------|
| `false` (default) | `false` (default) | Manual flow preserved: operator runs `devbench git-ops-finalize` by hand; merge is a human decision. |
| `true` | `false` | Orchestrate skill invokes `git-ops-finalize` once every actionable unit reaches a terminal state. PR is created and CI is watched (see [Post-finalize CI watching](#post-finalize-ci-watching-and-failure-resolution)); **not merged** -- merging stays a human decision. |
| `true` | `true` | Same as above; after the CI watcher reports GREEN, the skill invokes `gh pr merge` using the top-level `merge_strategy` config. Emits `[BATCH_PR_MERGED] <pr_url>` on the orchestrator log. |
| `false` | `true` | **Rejected at config-load time.** `auto_merge: true` requires `auto_finalize: true`. |

Re-entry guards prevent duplicate operations: a marker file at
`<workspace>/.devbench/auto-finalize-fired-<repo>.marker` is written
after a successful finalize; `<workspace>/.devbench/auto-merge-fired-<repo>.marker`
is written after a successful merge. Subsequent orchestrate iterations
short-circuit when the marker is present.

#### Schema-validation rules

The schema rejects the following combinations at config-load time:

- `auto_finalize: true` without `defer_pr: true` -- `auto_finalize` is
  meaningful only in single-PR mode. Validator emits a clear error.
- `auto_finalize: true` + `pause_before_merge: true` -- already mutually
  exclusive via `defer_pr`; the validator enforces this directly.
- `auto_finalize: true` + `local_only: true` -- rejected at config-load
  time; local-only repos have no remote to push to, so git-ops-finalize
  cannot create a PR.
- `auto_merge: true` without `auto_finalize: true` -- rejected; the
  combination is meaningless (no PR would be created to merge).
- `auto_merge: true` + `local_only: true` -- rejected; no remote means
  no PR means nothing to merge.

#### Dependency on the CI watcher (E7)

`auto_merge: true` depends on the CI watcher reporting GREEN before the
merge fires. See [Post-finalize CI watching and failure resolution](#post-finalize-ci-watching-and-failure-resolution)
for the four `CIResult` values and how each affects the merge decision.
When the CI watcher is absent (E7 not yet deployed), `auto_merge: true`
short-circuits with a `[AUTO_MERGE_SKIPPED] no_ci_watcher` audit row and
the operator must merge manually.

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

## Local-only mode

When the target "repo" has no `origin` remote configured -- typically
because it is a sibling checkout used to capture per-task operational
evidence (AWS teardown logs, audit artefacts, scheduled-job output)
rather than to host application source code -- set:

```yaml
git_ops:
  single_branch: feat/<workspace-name>
  defer_pr: true
  local_only: true
repos:
  org/repo:
    default_branch: main   # required: no origin/HEAD fallback exists
    checkout_directory: <local-folder>
```

What changes vs. single-branch + defer-PR mode:

- `ensure-branch` does **not** call `git fetch origin`. The work-unit
  branch is created off the **local** default branch
  (`refs/heads/<default_branch>`).
- `git-ops` commits locally only -- no push, no PR, no CI wait, no
  `git-ops-finalize` step.
- `commit_and_push`, `create_tag`, `checkout_default_branch`, and
  `rebase_and_force_push` are guarded -- calling any of them under
  `local_only: true` raises a clear `RuntimeError`. Defense-in-depth
  against future refactors.
- The pre-flight `devbench check` inverts its origin assertion: it
  REQUIRES the absence of an `origin` remote and flags any target repo
  that has one as misconfigured.

When to choose this mode:

- The work is operational, not application-code authoring -- AWS
  resource teardowns, evidence-capture audits, scheduled
  administrative ops.
- The output is a per-task artefact file committed to a local
  history; nobody reviews PRs because there are none.
- See [`operational-work.md`](operational-work.md) for an end-to-end
  walkthrough of structuring such a backlog.

### Configuration overrides

| Layer | Knob |
|-------|------|
| YAML | `git_ops.local_only: bool` |

There is no environment-variable override for `local_only`; the flag
declares an intent about the workspace's target repo, not a per-run
behavior.

## See also

- [ADR-13](adr/13-pause-before-merge.md) -- pause-before-merge design
  decision + consequences.
- [`operational-work.md`](operational-work.md) -- end-to-end pattern for
  using devbench to drive non-code operational work under
  `local_only: true`.
- [`plugin/devbench/skills/orchestrate/SKILL.md`](../plugin/devbench/skills/orchestrate/SKILL.md) -- step 1b reconciliation
  loop and step 8 mode dispatch.
- [`docs/cli-reference.md`](cli-reference.md) -- the `git-ops` and
  `check-merge` command references.
