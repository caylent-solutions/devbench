# Newly-Reachable Code Paths: Bug-Fix Definition of Done

## The problem

A recurring pattern across multi-round remediation: a fix task clears its reported
blocker, but the reported blocker had been gating off an entire code path (a component
was never mounted, a button was permanently disabled, a page never stopped crashing
before this point). Once the fix lands, that previously-unreachable path becomes
reachable for the first time -- and immediately exposes a new, independently
pre-existing (and previously untestable) defect. A subset of instances go further: the
fix itself directly causes the new defect by reusing a shared, stateful primitive (a
z-index tier, a dirty-flag write, a close/dismiss callback) without accounting for that
primitive's other consumers.

Confirming that the originally-reported reproduction steps now pass proves the gate
opened. It says nothing about what was behind the gate. Left unaddressed, this produces
work groups cycling through many QA rounds where several rounds are not re-tests of the
same bug but successive layers of a previously-inaccessible code path being reached and
found broken one level at a time.

## The rule

**A bug-fix task's Definition of Done is not satisfied by the original repro passing
alone.** It additionally requires:

1. **Enumeration** -- an explicit list of the code paths this fix newly makes
   reachable (paths that could not execute, render, or receive user interaction while
   the defect was present, and now can).
2. **Live verification** -- a real, smoke-test-level check of each enumerated path,
   not a read-through of the code asserting it "should" work.

## What counts as a "bug-fix task"

Two related mechanisms decide this today, on different signals (see "Known
limitations" below for why they are not yet unified):

- **`generate_draft_md`'s Definition-of-Done auto-append** (`proposal.py`) is keyed
  on the drafted task's Task Type: it appends the newly-reachable-paths item whenever
  `ProposedTask.task_type` resolves to `constants.TASK_TYPE_BEHAVIOR_FIX` -- the same
  `## Task Type:` taxonomy `manager.py` already validates on every leaf task.
- **The executor's and code-reviewer's BUG-FIX COMPLETENESS obligation** (see
  `executor.md` / `code-reviewer.md`) applies whenever a work unit's title starts with
  "Fix" (the imperative-title convention `blocker-resolver.md` already requires for
  defect-correction proposals), or its Description / Approach explicitly frames the
  work as correcting a defect: a crash, a permanently-disabled control, an exception
  that was silently short-circuiting downstream logic, a component that never
  mounted, a condition that always took the early-return branch.

It does NOT apply to greenfield feature work, refactors with no reported defect, or
documentation-only tasks. Judgment call: when in doubt, err toward treating a task as
bug-fix-shaped rather than skipping the step -- the cost of an unnecessary "none, this
fix is self-contained" line is one sentence; the cost of skipping a genuinely
gated-path fix is another multi-round remediation chain.

## What counts as adequate enumeration

This is inherently judgment-based. The bar is "meaningfully more than nothing," not
exhaustive transitive path coverage. At minimum, name the FIRST HOP downstream of the
gate the fix removed.

**Adequate:**

- "Fixed: modal now closes on Escape (previously threw on close). Newly reachable: the
  parent list's `onModalClose` refresh callback, which re-fetches and re-renders the
  row list."
- "Fixed: `/reports/export` no longer 500s before the auth check runs. Newly reachable:
  the CSV-generation branch and the S3-upload branch, both previously unreachable
  because the request never got past the auth check."
- "Fixed: form's Submit button was `disabled` unconditionally. Newly reachable: the
  submit handler, its validation branch, and the success/error toast it triggers."

**Inadequate (do not do this):**

- "Fixed the bug, repro no longer occurs." -- no enumeration at all.
- "This unlocks more of the app now." -- not specific enough for anyone to verify.

If, after genuine consideration, the fix unlocks no new code path (the defect was
fully self-contained -- e.g. a typo in a log message, an off-by-one in a value nothing
downstream reads), say so explicitly with a one-sentence justification rather than
omitting the step.

## What counts as live verification

Acceptable: running the app/service and exercising the path manually, an
integration/functional test that exercises it, or at minimum a targeted unit test
against the newly-reachable branch, with the command and result cited. Not acceptable:
reasoning from reading the code that the path "should" work.

If verification surfaces a new, independent defect in a newly-reachable path, the
executor does not silently mark the task done -- it is treated exactly like any other
bug discovered during TDD GREEN (fixed under the minimal-scope amendment path if in
scope, or escalated as a follow-up proposal if not). See `executor.md`'s "BUG-FIX
COMPLETENESS" section for the exact procedure.

## The audit trail: `[NEWLY_REACHABLE]`

Before logging completion, the executor logs one audit line:

```bash
uv run devbench log-comment executor <task-id> "[NEWLY_REACHABLE] <path 1>: <what was verified and how>; <path 2>: ..."
```

or, when nothing new is unlocked:

```bash
uv run devbench log-comment executor <task-id> "[NEWLY_REACHABLE] none -- <one-sentence justification>"
```

## Where this is enforced

This is a two-sided control: a self-reported step alone (executor-only) is weaker than
having an independent check that the self-report actually happened and holds up.

- **`executor.md`** ("BUG-FIX COMPLETENESS" section) -- the executing agent enumerates
  and live-verifies as part of doing the fix, before logging completion.
- **`blocker-resolver.md`** (STEP 5, `suggested_acs` guidance) -- when a proposed
  follow-up task is itself a bug fix, the proposal seeds an explicit AC requiring the
  eventual executor to do this, so the requirement travels with the task from creation.
- **`proposal.py` (`generate_draft_md`)** -- proposals whose `task_type` resolves to
  `constants.TASK_TYPE_BEHAVIOR_FIX` automatically get an extra Definition of Done
  checklist item requiring the enumeration + live-verification step, so materialised
  drafts carry the requirement even before a human edits them.
- **`code-reviewer.md`** ("BUG-FIX COMPLETENESS" rubric) -- the independent check.
  A bug-fix-shaped task with no `[NEWLY_REACHABLE]` entry in its Comments/Agent Log
  fails review, even if the original repro now passes and every other rubric item is
  clean. This is the gate that makes the requirement more than self-reported: the
  executor's own claim is not sufficient for the task to close.

## Optional: the cross-cutting-primitives registry

A subset of "newly-reachable path" failures are not just newly-exposed pre-existing
bugs -- the fix itself introduces the break by reusing a shared, stateful primitive
(a shared z-index tier, a shared dirty-flag/`setField` write path, a shared
close/dismiss callback) without accounting for that primitive's other consumers.

Workspaces that want a lightweight reminder for this can create an optional registry
file at `backlog/config/cross-cutting-primitives.md` (workspace-relative, next to
`backlog/config/devbench.yaml`). There is no schema validation or CLI enforcement for
this file today -- it is a plain markdown convention that `executor.md` and
`code-reviewer.md` read (via `cat`, when present) and cross-reference against the
diff's changed files. Format:

```markdown
# Cross-Cutting Primitives

| Primitive | Defining file(s) | Known consumers |
|-----------|-------------------|------------------|
| Modal z-index tier | `src/ui/zindex.ts` | `Modal`, `Toast`, `Tooltip`, `CommandPalette` |
| Form dirty-flag write path | `src/forms/useDirtyField.ts` | every screen under `src/forms/screens/` |
| Shared close/dismiss callback | `src/ui/useDismissable.ts` | `Modal`, `Drawer`, `Popover` |
```

When a diff touches a defining file (or a file plausibly implementing one of the
listed primitives), the executor and the code-reviewer both treat it as a prompt to
check the primitive's other named consumers as part of the enumeration/verification
step above -- not just the consumer that was the subject of the fix.

**This is intentionally a v1, not a static-analysis tool.** No devbench code parses or
diffs against this file automatically; it is plain text a workspace maintains by hand
and two agents read as extra evidence. See "Known limitations" below for what a fuller
version would need.

## Known limitations / follow-ups

- `generate_draft_md`'s auto-append is keyed on the `## Task Type:` taxonomy
  (`constants.TASK_TYPE_BEHAVIOR_FIX`) rather than a title heuristic. The broader
  "is this task bug-fix-shaped" judgment used by `executor.md`'s BUG-FIX COMPLETENESS
  section, `blocker-resolver.md`'s AC-seeding, and `code-reviewer.md`'s rubric scoping
  still relies on the title/description heuristic described above. Unifying all of
  these onto the Task-Type taxonomy, plus merging PR #320's file-existence registry
  into the gates config, is E8-F1-S1-T1's scope (spec 4.9a, C-03).
- The cross-cutting-primitives registry is read as free text by two prompt-driven
  agents; it is not schema-validated, not wired into `backlog/config/devbench.yaml`,
  and no CLI command diffs a work unit's Changes Manifest against it automatically. A
  follow-up could add a `cmd_check_cross_cutting_primitives` CLI helper that does a
  deterministic path-match and surfaces a `[CROSS_CUTTING_PRIMITIVE_TOUCHED]` audit
  line, removing the dependence on the agent noticing the overlap unprompted.
- Live verification is judged by the code-reviewer reading the `[NEWLY_REACHABLE]`
  entry's prose, the same way `AC-FINAL` evidence is judged today (issue #156's
  `MISSING_AC_EVIDENCE` pattern) -- there is no structured schema for "verification
  evidence" the way there is for, say, TDD Cycle Log entries. This is consistent with
  how the rest of the review rubric works, but a stricter follow-up could require a
  structured `{path, method, command, result}` JSON blob instead of prose.
