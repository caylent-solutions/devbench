# Source/Test Atomicity in Changes Manifests

## The rule

Every Python source file authored by a Task MUST have its matching test file in the SAME Task's `## Changes Manifest`. Specifically:

- For every entry of the form `<path>/<basename>.py` in a Task's Manifest where the path indicates production source (e.g., `infra/scripts/`, `services/<name>/src/`, `src/`), the same Manifest MUST contain a matching test entry of the form `tests/unit/test_<basename>.py` (or the project's equivalent test path convention, e.g., `services/<name>/tests/unit/test_<basename>.py`).
- The two files MUST be authored, tested, and committed in a single TDD cycle within the same Task. RED phase writes the test against an absent or stub source; GREEN phase writes the source until the test passes.

This rule supersedes the older "Test task (RED) + Implementation task (GREEN) pair" advice. The split-task pattern is OBSOLETE for Python work because devbench's strict acceptance criteria (specifically `AC-FINAL-014` 100% coverage) cannot be satisfied during the Task that authors the source if its test is owned by a sibling Task.

## Why

`AC-FINAL-014` requires the new source file to have 100% line + branch coverage at the moment the source-authoring Task closes. If the test is owned by a sibling Task that has not yet run, coverage on the source file is 0% and the source-authoring Task fails its own AC. Two failure modes follow:

1. The orchestrator blocks the source Task indefinitely waiting on the (sibling) test Task.
2. If both Tasks are sequenced in the same dep graph and the orchestrator picks the source first, the cascade triggers a manifest amendment that the manifest-amender often rejects (the test file is outside the source Task's declared Manifest), which then triggers a `blocker-resolver` proposal for a follow-up Task to "add the test file to the source Task's Manifest" -- which is the test Task all over again, just renamed and re-scoped.

The atomic pattern eliminates the cascade by including the test in the source Task's Manifest from the start. The manifest-amender does not get involved because the Manifest already lists what the executor needs to write. `AC-FINAL-014` measures coverage of the source against the test that lands in the same commit; both pass together.

## What "atomic" means in practice

- A Task authoring `infra/scripts/merge_properties.py` MUST also list `services/shared/tests/unit/test_merge_properties.py` (or the project's equivalent test path) in its `## Changes Manifest`.
- A Task authoring `services/processor/src/event_handler.py` MUST also list `services/processor/tests/unit/test_event_handler.py`.
- A Task authoring N source files lists N matching test files; one Manifest, one Task, one git commit, one TDD cycle (RED-GREEN-REFACTOR per source/test pair).

## Counter-example observed in production

Caylent Telemetry's original Backlog A had:

- `E1-F1-S1-T3` (Implement merge_properties.py) -- Manifest: `infra/scripts/merge_properties.py`, `infra/scripts/__init__.py`.
- `E1-F1-S1-T4` (Unit tests for merge_properties.py) -- Manifest: `services/shared/tests/unit/test_merge_properties.py`, plus 2 `__init__.py` files.

When the orchestrator picked T3 first, T3's `AC-FINAL-014` failed (the test file did not exist in T3's Manifest, so it was not authored, so coverage was 0%). The fix applied: merge T3+T4 into a single Task (T3) that owns BOTH the source and the test files; mark T4 as `declined` with reason "merged-into-T3-source-test-pair-must-be-atomic-AC-FINAL-014". After the merge, T3 became claimable and self-closing.

## When the rule does NOT apply

- **Tests-only Tasks for code authored elsewhere**: rare but legitimate. Example: the Task adds an integration test that exercises code already shipped in a prior milestone. The test does not have a "source" pair within this Task; AC-FINAL-014 measures coverage of the test file's own assertions against existing code, not of new source. These Tasks are rare and each one needs an explicit `Description` justifying why the source-test pair is split.
- **Source-only generators**: e.g., a Task that authors a YAML config file used by Terragrunt. YAML is not Python, so AC-FINAL-014 is N/A (per `acceptance-criteria-canonical.md` language tiering). No test pairing required.
- **Migrating existing source**: a Task that moves a `.py` file from one path to another without changing behavior MAY rely on the existing test. Document the existing test's path in the Task's `Description` so reviewers can verify the test still covers the moved source.

## RED-GREEN-REFACTOR within an atomic Task

The TDD discipline is preserved by sequencing within the Task, not across Tasks:

1. **RED**: author the test file against an absent/stub source. Run `pytest tests/unit/test_<basename>.py`; expected exit 1 with `ImportError` or `AssertionError`.
2. **GREEN**: author the source file with the minimum implementation that makes the test pass. Re-run `pytest`; expected exit 0.
3. **REFACTOR**: clean up source and tests if needed; re-run `pytest`; expected exit 0.

The Task's `## TDD Cycle Log` records each phase with timestamps and exit codes, satisfying `AC-FINAL-001` (every AC-TEST/AC-CYCLE runs and passes) and `AC-FINAL-013` (no test skips).

## Authoring checklist

When drafting a Python-source Task's Manifest:

- [ ] Every `.py` file under `src/`, `services/<name>/src/`, or `infra/scripts/` has a matching `tests/unit/test_<basename>.py` entry in the same Manifest.
- [ ] If the project uses `services/<name>/tests/...` test paths (vs root `tests/...`), the test entries match the project's convention.
- [ ] `__init__.py` files for every new test package are listed in the Manifest (`tests/__init__.py`, `tests/unit/__init__.py`).
- [ ] `AC-FINAL-014` line is present without a `-- N/A` suffix (the Task IS Python-tier and coverage applies).
- [ ] AC-CYCLE-001 (at minimum) names the integration check that exercises the source via the test.

## Tooling support

`devbench validate-backlog` MAY emit a warning when a Task's Manifest contains a Python source file under a production-source path but no matching test file is listed. This rule is part of the post-Backlog-A Tier 3 tooling proposal; until it lands, authors are responsible for self-checking against the rule above.

## Authority

This document is the source of truth for source/test atomicity. `creating-specs-and-backlogs.md`'s historical "Test task + Implementation task pair" guidance is superseded for Python Tasks (the original split pattern remains acceptable for RED-only TDD demonstrations where the Task explicitly intends to land a failing test as the artifact). When the older pattern is referenced, this document is the override.
