# spec-to-backlog skill quickstart

The `spec-to-backlog` skill transforms a `spec/<project-name>.md` into a complete,
kanon-quality backlog: `BACKLOG.md` plus work-unit `.md` files under `backlog/` -- all
at the depth and rigour of the canonical quality reference.

## What spec-to-backlog produces

- `BACKLOG.md` -- Status Summary table + Full Work Unit Index (one row per leaf task).
- `backlog/<epic>/<feature>/<story>/<task>.md` -- one work-unit file per leaf task
  in the 4-level hierarchy (Epic -> Feature -> Story -> Task).
- All tasks default to `draft` status so operators can review before execution.
- A `[QUALITY_REFERENCE]` audit comment naming the kanon exemplar path consulted.

The generated backlog passes `devbench validate-backlog` with zero errors before the
skill exits.

## Quality bar

The canonical quality reference is the kanon backlog exemplar at
`/workspaces/rpm-migration/kanon-deps-work/`. The skill reads `BACKLOG.md` and a
representative leaf task file in Step 1. Target depth per task: ~50KB equivalent (the
kanon quality bar). Sub-30KB tasks are skeleton-only and the skill iterates until they
reach the quality bar.

## Prerequisites

Before invoking spec-to-backlog:

1. A spec file exists at `spec/<project-name>.md` (authored by `create-spec` or manually).
   The spec must include functional requirements and AC-N acceptance criteria.
2. `backlog/config/devbench.yaml` exists (even a minimal one) -- the skill reads
   `backlog.default_status_for_new_work_units` from it to determine whether new tasks
   land in `draft` or `in-queue`.
3. Claude Code CLI installed and authenticated.

## How to invoke

From any Claude Code session with the devbench plugin available:

```
claude run devbench:spec-to-backlog
```

Or per-session:

```bash
claude --dangerously-skip-permissions \
  --plugin-dir $DEVBENCH_DIR/plugin/devbench
```

Then within the session:

```
run devbench:spec-to-backlog
```

The skill asks: "Which spec file should I decompose into a backlog?"

Provide the path, e.g.: `spec/my-project.md`

## What the skill does (step by step)

1. **Reads the kanon backlog exemplar** -- internalises the 7-column BACKLOG.md
   format and the 15 canonical task-file sections.
2. **Asks for the input spec path** -- if not provided in the invocation message.
3. **Reads and internalises the spec** -- extracts functional requirements, AC-N
   identifiers, constraints, and the target repository/branch.
4. **Epic decomposition** (iterate-until-perfect granularity 1) -- decomposes every
   spec FR into a 4-level hierarchy with no skipped levels, balanced distribution
   (no epic holds more than 70% of tasks), and a validated DAG (no cycles).
5. **Authors task files one at a time** (iterate-until-perfect granularity 2) --
   writes each leaf task file with all 15 canonical sections and scores it against a
   10-item per-task rubric. Revises until the rubric score is zero.
6. **Runs `validate-backlog`** after each task file (iterate-until-perfect
   granularity 3) -- fixes any errors immediately before moving to the next task.
7. **Writes BACKLOG.md** -- Status Summary table + Full Work Unit Index with counts
   matching the generated task files.
8. **Final `validate-backlog` pass** -- all three exit conditions must pass:
   rc=0, every task passes the rubric, and BACKLOG.md totals match the Index row count.
9. **Emits `[QUALITY_REFERENCE]`** audit comment.
10. **Offers `devbench promote` guidance** -- shows how to promote draft tasks to
    `in-queue` when the operator is ready.

## Draft status and the promote workflow

All generated tasks default to `draft` status. This gives you a review gate before the
orchestrator can claim and execute any task.

After generation, inspect the draft tasks:

```bash
DEVBENCH_WORKSPACE_ROOT=~/my-workspace \
uv run --project $DEVBENCH_DIR devbench status
```

Promote tasks when ready:

```bash
# Promote a single task:
devbench promote E1-F1-S1-T1

# Promote all tasks under an epic:
devbench promote --epic E1

# Promote everything at once:
devbench promote --all --yes
```

To change the default from `draft` to `in-queue` (legacy behaviour), set in
`backlog/config/devbench.yaml`:

```yaml
backlog:
  default_status_for_new_work_units: in-queue
```

## Output contract

| Artefact | Location | Condition |
|----------|----------|-----------|
| Backlog index | `BACKLOG.md` | Written after all tasks pass the rubric |
| Task files | `backlog/<epic>/.../<task>.md` | One per leaf task |
| Audit comment | stdout | `[QUALITY_REFERENCE] <kanon-backlog-path>` on success |

## validate-backlog rc=0 guarantee

The skill does not exit until `devbench validate-backlog` returns rc=0. If the final
pass fails, the skill returns to the relevant fix step and re-runs validation. If
`SKILL_MAX_ITERATIONS` is exhausted without convergence, it emits a
`[SKILL_MAX_ITERATIONS_REACHED]` audit row listing the unresolved conditions and
exits non-zero instead of silently shipping a broken backlog.

## Bounded self-critique loop

The iterate-until-perfect loop is bounded by constants in
`src/devbench/constants.py`:

- `SKILL_MAX_ITERATIONS` -- maximum self-critique passes before the skill
  emits `[SKILL_MAX_ITERATIONS_REACHED]` and exits non-zero.
- `SKILL_QUALITY_THRESHOLD` -- unresolved-item count at which the skill
  emits `[SKILL_QUALITY_THRESHOLD_REACHED]` and exits success.

State persistence and audit emission are handled by
`src/devbench/skill_state.py` (`read_checkpoint`, `write_checkpoint`,
`emit_audit`). The checkpoint file lives at
`<workspace>/.devbench/skill-state/spec-to-backlog.json` between iterations.
The audit tags flow through the existing `devbench report` and
`devbench hook-tail` pipelines.

## Cross-references

- [`plugin/devbench/skills/spec-to-backlog/SKILL.md`](../../plugin/devbench/skills/spec-to-backlog/SKILL.md) -- full skill prompt
- [`docs/skills/create-spec.md`](create-spec.md) -- the upstream skill that authors the spec
- [`docs/skills/configure-devbench.md`](configure-devbench.md) -- configure devbench.yaml before running
- [`docs/creating-specs-and-backlogs.md`](../creating-specs-and-backlogs.md) -- manual backlog authoring guide
- [`docs/backlog-contract.md`](../backlog-contract.md) -- validate-backlog rule set (20 rules)
- [`docs/onboarding.md`](../onboarding.md) -- chained-skill operator workflow
