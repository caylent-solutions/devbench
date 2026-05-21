# create-spec skill quickstart

The `create-spec` skill helps you author a rigorous engineering specification that
matches the kanon quality bar. After gathering answers to structured questions about
your project, the skill produces a `spec/<project-name>.md` file that the
`spec-to-backlog` skill can consume directly.

## What create-spec produces

- `spec/<project-name>.md` -- a 1000+ line spec (for non-trivial programs) covering
  all 16 top-level sections from the kanon exemplar skeleton (Sections 0-15), or with
  explicit N/A justification for sections not applicable to the project.
- A `[QUALITY_REFERENCE]` audit comment naming the kanon exemplar path consulted.

## Quality bar

The canonical quality reference is the kanon spec exemplar at
`/workspaces/rpm-migration/kanon-deps-work/spec/kanon-list-add-lock-features-spec.md`.
The skill reads this file in Step 1 before authoring anything, internalises the
16-section structural skeleton, then applies that skeleton to your project.

Target depth: 1000+ lines for non-trivial programs. A 200-line spec is appropriate for
a single-feature change; a 50-line spec is never sufficient for a new subsystem.

## Prerequisites

Before invoking create-spec, have the following ready:

1. Access to the codebase you are specifying (local clone or readable paths).
2. A one-sentence problem statement and project name.
3. A rough list of functional requirements (even informal notes are fine -- the skill
   will structure them through a Q&A block).
4. Claude Code CLI installed and authenticated (Anthropic API or AWS Bedrock).

## How to invoke

From any Claude Code session with the devbench plugin available:

```
claude run devbench:create-spec
```

Or, if running without a global plugin install, load the plugin directory per-session:

```bash
claude --dangerously-skip-permissions \
  --plugin-dir $DEVBENCH_DIR/plugin/devbench
```

Then from within the Claude Code session:

```
run devbench:create-spec
```

## What the skill does (step by step)

1. **Reads the kanon exemplar** -- internalises the 16-section structural skeleton
   and the quality bar before authoring anything.
2. **Asks a structured question block** -- Block A (context), Block B (goals/scope),
   Block C (command surface), Block D (data formats), Block E (NFRs), Block F (testing
   and docs), Block G (ACs, decisions, future work). The skill waits for your answers
   before drafting.
3. **Authors the spec one section at a time** -- works through Sections 0-15 in
   order, presenting each section for operator spot-check before continuing.
4. **Runs the iterate-until-perfect self-critique loop** -- scores each draft against
   an 8-item rubric (structure, functional requirements, acceptance criteria, design
   record). Revises and re-scores until the rubric score is zero or `max_iterations`
   is reached.
5. **Presents for final operator review** -- shows total line count, section count,
   and AC count. Asks "Does this look good to write?"
6. **Writes `spec/<project-name>.md`** and reads back the first 20 lines to confirm.
7. **Emits `[QUALITY_REFERENCE]`** audit comment naming the kanon exemplar path.
8. **Offers spec-to-backlog handoff** -- asks whether to invoke the `spec-to-backlog`
   skill immediately.

If `max_iterations` is reached without converging, the skill emits a `[BLOCKED]` comment
listing the unresolved rubric items and asks the operator to clarify the ambiguous areas.
It does NOT silently ship a sub-quality spec.

## Iterate-until-perfect self-critique rubric

The skill scores the draft against 8 items after every revision:

1. **16 sections** -- all present or explicitly marked N/A.
2. **Worked examples per goal** -- every goal has a concrete command + expected output.
3. **Error handling per FR** -- every functional requirement states error message and
   exit code.
4. **Non-goals stated** -- Section 12 names every plausible adjacent ask not covered.
5. **Numbered and testable ACs** -- every AC is `AC-N` format, cites its spec section,
   and is testable from the spec text alone.
6. **Cross-references to primitives** -- every reused function, class, or env var is
   cited in Section 3 before being mentioned in Section 4+.
7. **Resolved decisions** -- Section 13 records every design call made during authoring
   with the rationale and alternatives considered.
8. **Out-of-scope section** -- Section 12 names at least as many adjacent asks as were
   discussed in the operator Q&A block.

## Output contract

| Artefact | Location | Condition |
|----------|----------|-----------|
| Spec file | `spec/<project-name>.md` | Written after operator approves the draft |
| Audit comment | stdout | `[QUALITY_REFERENCE] <kanon-spec-path>` emitted on success |

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
`<workspace>/.devbench/skill-state/create-spec.json` between iterations.
The audit tags flow through the existing `devbench report` and
`devbench hook-tail` pipelines without any new infrastructure.

## Cross-references

- [`plugin-authoring/devbench-authoring/skills/create-spec/SKILL.md`](../../plugin-authoring/devbench-authoring/skills/create-spec/SKILL.md) -- full skill prompt
- [`docs/skills/spec-to-backlog.md`](spec-to-backlog.md) -- the downstream skill that consumes this spec
- [`docs/creating-specs-and-backlogs.md`](../creating-specs-and-backlogs.md) -- manual spec authoring guide
- [`docs/onboarding.md`](../onboarding.md) -- chained-skill operator workflow
