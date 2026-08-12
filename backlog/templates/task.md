# {{ID}}: {{TITLE}}

## Status: in-queue

## Task Type: behavior-fix

<!--
  Optional section. If omitted entirely, this task DEFAULTS to
  `behavior-fix` -- the strictest type -- under validate-backlog rule 21
  (FR-4.1); omitting the section is never an escape hatch from the RED
  gate. This scaffolding template always writes `behavior-fix` explicitly
  so the default and the explicit declaration agree for newly created
  tasks. Terminal tasks (`done` / `declined`) are skipped by rule 21
  regardless of whether this section is present.

  One of six values. Five carry a machine-checked Changes Manifest
  invariant enforced by `validate-backlog` (FR-4.1); `refactor`'s
  green-green requirement is NOT YET machine-checked (deferred to
  E4-F4-S1-T2) and is a TDD-cycle-log concern today, not a static
  Manifest-shape check:

  - `behavior-fix` -- RED-gated; Manifest needs >= 1 production-source row.
  - `feature`      -- RED-gated; Manifest needs >= 1 production-source row.
  - `test-only`    -- exempt from the RED gate; every Manifest row must be a test path.
  - `refactor`     -- exempt from the RED gate; requires green-green (tests pass before
                       AND after), no AC text change -- not yet machine-checked by
                       validate-backlog (see E4-F4-S1-T2).
  - `docs`         -- exempt; every Manifest row must be documentation/markdown.
  - `chore`        -- exempt; every Manifest row must be dependency/config/lockfile.

  See docs/backlog-contract.md "Task-Type Taxonomy" for the full invariant
  table, the default-when-absent rule, and the exact failure-message shapes.
-->

## Target Repository

- **Repo:** `{{REPO}}`
- **Branch:** `backlog/{{ID_LOWER}}` <!-- or `backlog/{{BRANCH_PREFIX}}/{{ID_LOWER}}` when git_ops.branch_prefix (or a per-repo override) is configured in backlog/config/devbench.yaml -- see spec-to-backlog SKILL.md "Branch naming" -->

## Description

{{DESCRIPTION}}

## Dependencies

| ID | Type | Reason |
|----|------|--------|
| none | | |

## Acceptance Criteria

- [ ] AC-FUNC-001: {{AC_FUNC}}
- [ ] AC-FINAL-001: `make lint` exits 0 on the target repo.
- [ ] AC-FINAL-002: `make typecheck` exits 0 on the target repo.
- [ ] AC-FINAL-003: `make test` exits 0 on the target repo.
- [ ] AC-FINAL-009: `devbench validate-backlog` exits 0.
- [ ] AC-FINAL-011: No bypass annotations introduced (no `noqa`, `nosec`, `type: ignore`, etc.).
- [ ] AC-FINAL-012: No em-dash characters introduced.
- [ ] AC-FINAL-015: Changes Manifest matches `git diff` exactly.

See [docs/acceptance-criteria-canonical.md](../../docs/acceptance-criteria-canonical.md) for the full AC-FINAL list and language-tier applicability rules.

## Changes Manifest

| File | Change |
|------|--------|
| `{{SOURCE_FILE}}` | new |
| `{{TEST_FILE}}` | new |

Every production source file in this Manifest MUST have a paired test file in the SAME Manifest. See [docs/source-test-atomicity.md](../../docs/source-test-atomicity.md).

## Definition of Done

- [ ] All acceptance criteria are checked.
- [ ] Review-supervisor PASS on `code_review`, `test_review`, `doc_review`, `changes_manifest`.
- [ ] Security-reviewer PASS.
- [ ] git-ops merged the work-unit branch (or recorded the deferred commit when single-PR mode is on).

## Comments

<!-- The orchestrator and reviewers append `[STATUS]` audit lines here. Operators may also paste manual rationale. -->

## TDD Cycle Log

<!-- The executor appends `[RED] ...` / `[GREEN] ...` / `[REFACTOR] ...` lines here per cycle. -->
