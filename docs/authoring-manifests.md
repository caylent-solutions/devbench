# Authoring the Changes Manifest

The `## Changes Manifest` section in each work-unit file is the declarative contract between the backlog author and the executor. The `changes_manifest` judge enforces that the staged file set matches this manifest exactly -- declared files must be present and undeclared files must be absent.

This doc explains how to write manifests that do not collide with the execution standards DevBench enforces, and it describes the three patterns that avoid the most common authoring defect.

## The TDD-with-production-fix collision

Most tasks that follow TDD (RED / GREEN / REFACTOR) can in principle require a production change during GREEN to make the failing test pass. If the Approach section authorises such a change ("if the test exposes a bug that needs a production fix, implement the minimum change"), but the Changes Manifest lists only test files, the two statements contradict. The executor follows the Approach and stages the fix; the `changes_manifest` judge rejects on `AC-FINAL-015` (staged files must match the manifest exactly); the task blocks. The executor cannot repair its own manifest because the guard hook prevents it from editing work-unit files.

Three patterns resolve the collision. Pick the one that matches the task's intent.

## Pattern 1 -- pre-declared manifest (preferred when the fix is likely)

Declare every file the executor could plausibly need to touch. This is the default for tasks where the author can predict the production fix, and it is the pattern `docs/example-work-unit-template.md` ships.

```markdown
## Changes Manifest

| File | Change |
|------|--------|
| `tests/test_example.py` | new unit tests for the edge case |
| `src/example/service.py` | minimum production fix for the edge case |
```

With both files declared, TDD can proceed to GREEN and stage either or both. The `changes_manifest` judge passes on the first run.

## Pattern 2 -- test-only with explicit escalate (preferred when the test should pass as-is)

Use this when the test is expected to codify existing correct behavior. Make the Approach explicit that no production change is authorised:

```markdown
## Description

### Approach

1. Write the failing test listed in AC-TEST-*.
2. If TDD RED does not pass once the test is written, stop and escalate -- do not make production changes in this task.
3. Otherwise, clean up if refactoring is needed.

## Changes Manifest

| File | Change |
|------|--------|
| `tests/test_example.py` | new unit tests |
```

Under this pattern, the executor will not stage production files. If the test unexpectedly exposes a bug, the task blocks for human review rather than silently expanding scope.

## Pattern 3 -- rely on the amendment workflow

If the backlog has opted into the amendment workflow (`manifest_amendment.enabled: true` in `backlog/config/devbench.yaml`), the executor can emit an amendment request during TDD GREEN when it discovers a required production fix that was not pre-declared. The `manifest-amender` judge reviews the request and, on approval, updates the Changes Manifest atomically before the standard review judges run.

This is the safety net for TDD discoveries that the author genuinely could not anticipate. It is valid, but Pattern 1 is preferred for cases where the author can predict the fix. Every amendment costs an additional judge invocation and leaves an audit trail that must be reviewed; pre-declaring is cheaper and keeps the task deterministic.

See [docs/manifest-amendments.md](manifest-amendments.md) for the full amendment workflow, the three-layer decision architecture (deterministic pre-filter, narrow LLM judge, deterministic post-check with rollback), and the opt-in configuration.

## Decision tree

1. Does the work unit primarily test existing behavior that should already work? -> **Pattern 2**.
2. Is the production fix predictable from the AC text? -> **Pattern 1**.
3. Is the production fix genuinely unpredictable (e.g., the test will probe a wide surface)? -> **Pattern 3**, if the backlog has opted into amendments. Otherwise **Pattern 2**; any surprise blocks for human review.

## Manifest row rules

Every row must:

- Use exactly two columns: `File` and `Change`.
- Wrap the file path in backticks. The path is relative to the repo root.
- Avoid em-dash (U+2014) anywhere in either cell. Use `--` (two hyphens) if a dash is needed.
- Contain non-empty, non-whitespace content in both cells.

The parser and writer live in `src/devbench/backlog/manifest.py`; malformed tables raise `ManifestParseError` with a specific message identifying the offending row.

## Why this matters

`AC-FINAL-015` -- "the Task's Changes Manifest matches exactly the files changed by git (no extra, no missing)" -- is one of the strongest invariants DevBench enforces. The pre-declared pattern keeps the judge's job mechanical (set comparison); the amendment pattern provides an audited escape hatch for cases the author could not foresee. Neither pattern weakens the invariant; they differ in when the declaration is made.

## When a declared row goes stale

`AC-FINAL-015` cuts both ways: "no extra" means a row the Task declares but never changes is as much a violation as an undeclared file that appears in the diff. The usual cause is benign -- the work that row was written for landed under a sibling unit first, leaving this Task's copy with a zero-line diff.

`changes_manifest` fails the unit with `MANIFEST_MISMATCH` and prescribes an amendment. The remedy is `files_to_remove` on the amendment request, which drops the stale row. It is gated: a row may only be dropped once its file has **no staged, unstaged, or untracked changes**, so a removal can never quietly carry real work outside the reviewed Manifest. See [docs/manifest-amendments.md](manifest-amendments.md#removing-a-stale-row).

Pre-declaring still beats correcting after the fact. If two sibling Tasks could each plausibly land the same file, decide which one owns it while authoring rather than leaving the loser to amend its way out.
