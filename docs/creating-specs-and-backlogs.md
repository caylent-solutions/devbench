# Creating Specs and Backlogs for DevBench

How to produce a specification, break it into a backlog, and structure work units so DevBench can execute them reliably. This guide applies to any project type -- application development, infrastructure migration, cloud operations, or platform engineering.

## Table of contents

- [Overview](#overview)
- [Phase 1: Writing the Specification](#phase-1-writing-the-specification)
- [Phase 2: Backlog Structure](#phase-2-backlog-structure)
- [Phase 3: Work Unit Authoring](#phase-3-work-unit-authoring)
- [Phase 4: Code Standards Block](#phase-4-code-standards-block)
- [Phase 5: Lifecycle Journey Tests](#phase-5-lifecycle-journey-tests)
- [Phase 6: Git Strategy](#phase-6-git-strategy)
- [Phase 7: Validation](#phase-7-validation)
- [Authoring checklist](#authoring-checklist)

---

## Overview

DevBench executes work from a structured backlog. The quality of execution depends directly on the quality of the backlog. A vague work unit produces vague code. A precise work unit with clear acceptance criteria, lifecycle tests, and code standards produces reliable, reviewable output.

The process has seven phases (this guide walks through each):

1. **Spec** — Understand the problem, audit the codebase, document every detail
2. **Backlog structure** — Pick a hierarchy (epics → features → stories → tasks)
3. **Work unit authoring** — Write each task with the required sections
4. **Code Standards block** — Embed the rules every task must follow
5. **Lifecycle Journey Tests** — Add end-to-end cycle ACs
6. **Git Strategy** — Multi-PR (default) vs single-PR mode
7. **Validation** — Run `devbench validate-backlog` before execution

For the wider context (how the orchestrator consumes this backlog, multi-PR vs single-PR mode, judge architecture), see the [architecture overview](architecture.md).

---

## Phase 1: Writing the Specification

### Start with an audit, not a plan

Before writing any spec, deeply explore the codebase you're changing. Use agents to inventory:

- Every source file, its line count, classes, functions, and imports
- Every test file, its test count, and what it covers
- Every configuration file, dependency, and entry point
- Every cross-reference and integration point
- Every `__file__` path assumption, global state, dynamic import, or process-level side effect

The goal is to find every landmine before you step on it. The spec must address every issue found during the audit.

### Spec structure

A spec should contain:

1. **Context** -- Why this change is being made, what prompted it, the intended outcome
2. **Current state** -- Precise inventory of what exists today (file counts, line counts, test counts)
3. **Target state** -- Architecture after the change, with file tree showing every new/modified file
4. **Critical challenges** -- Problems discovered during audit that must be solved (not just listed)
5. **Migration/implementation phases** -- Ordered steps, each independently verifiable
6. **Automated test plan** -- Every deterministic test that will be written, organized by category
7. **Agent-executed test plan** -- Manual integration tests the agent runs against real repos
8. **Risk assessment** -- What can go wrong, severity, and mitigation for each
9. **Out of scope** -- What is explicitly not being done
10. **Definition of done** -- Checklist of every criterion that must be true before declaring complete

### Bug backlog

During the audit, document every bug, edge case, or fragile code pattern found. Create a separate bug backlog file prioritized by severity (Critical, High, Medium, Low). Each bug should have:

- File and line number
- Description of the problem
- Impact if not fixed
- Proposed fix

Reference the bug backlog from the spec. Plan when each bug gets fixed (as part of the migration, or separately).

### Critical review

After writing the spec, review it critically for:

- Missing files in the inventory
- Missing imports that need updating
- Dynamic code loading patterns (e.g., `__import__()`, `importlib`)
- `__file__` path assumptions that break when code moves
- Global state that leaks between calls
- Process-level operations (`os.execv()`, `sys.exit()`, signal handlers) that break when called as a library
- Non-Python runtime files (scripts, hooks, data files, docs read at runtime)
- Hard-coded organization-specific values

---

## Phase 2: Building the Backlog

### Hierarchy

DevBench uses a four-level hierarchy:

```
Epic (E0)           -- The entire initiative
  Feature (E0-F1)   -- A major capability area
    Story (E0-F1-S1) -- A commit-sized unit of work
      Task (E0-F1-S1-T1) -- A single TDD cycle (test + implement)
```

**Epics** scope the whole project. One epic per initiative.
**Features** group related stories. Each feature is a major subsystem (e.g., "Create Python API", "Fix all bugs", "Update documentation").
**Stories** produce commits. Every story is a commit. All tasks within a story must pass before the commit.
**Tasks** are the atomic work units that DevBench executes. Each task follows TDD: write failing test, implement, verify.

### Directory structure

```
backlog/
  config/
    devbench.yaml
  E0/
    E0.md
    E0-F1/
      E0-F1.md
      E0-F1-S1/
        E0-F1-S1.md
        E0-F1-S1-T1.md
        E0-F1-S1-T2.md
      E0-F1-S2/
        ...
    E0-F2/
      ...
```

Each level gets its own directory. Files are named by their ID. `BACKLOG.md` at the workspace root indexes everything.

### BACKLOG.md index

```markdown
## Full Work Unit Index

| ID | Title | Type | Status | Dependencies | Repo | File Path |
|-----|-------|------|--------|-------------|------|-----------|
| E0 | My Initiative | Epic | in-queue | None | org/repo | `backlog/E0/E0.md` |
| E0-F1 | Feature One | Feature | in-queue | None | org/repo | `backlog/E0/E0-F1/E0-F1.md` |
...

## Status Summary

| Epic | Title | Done | In Progress | In Queue | Blocked |
|------|-------|------|-------------|----------|---------|
| E0 | My Initiative | 0 | 0 | 42 | 0 |
```

The Status Summary uses per-epic rows with columns: Epic, Title, Done, In Progress, In Queue, Blocked. The count is descendants only (the epic row itself is not counted).

### Dependency chains

Dependencies control execution order. A task is actionable only when all its dependencies are `done`.

- Tasks within a story depend on each other sequentially (T1 before T2 before T3)
- Stories within a feature depend on the previous story (S2 depends on S1)
- Features can run in parallel if their dependency chains are independent
- Cross-feature dependencies are specified explicitly

Every task should list what it depends on AND what depends on it (the latter as `### Depends On This` inside the Description).

### TDD pairing

Tasks should come in pairs:

1. **Test task** (TDD RED): Write failing tests for the functionality
2. **Implementation task** (TDD GREEN): Implement to make tests pass

This ensures tests exist before code, and the agent cannot skip testing.

---

## Phase 3: Structuring Work Units

### Task file format (devbench contract)

```markdown
# {ID}: {Title}

## Status: in-queue

## Target Repository

- **Repo:** `org/repo`
- **Branch:** `backlog/{id_lower}`

## Description

{Detailed description.}

### Definition of Ready

- [ ] All dependency work units are `done`
- [ ] Target repository is accessible
- [ ] No open questions about the spec

### Depends On This

| ID | Title | Status |
|----|-------|--------|
| ... | ... | ... |

### Approach

1. **TDD RED:** Write failing tests
2. **TDD GREEN:** Implement
3. **TDD REFACTOR:** Clean up
4. **Verify:** Run full suite

### Code Standards

{Full code standards block — copy from [Phase 4: Code Standards Block](#phase-4-code-standards-block) below.}

### Related Specifications

- **Spec:** `specs/my-spec.md` -- Section X, Phase Y

## Dependencies

| ID | Title | Status |
|----|-------|--------|
| ... | ... | ... |

## Acceptance Criteria

- [ ] AC-FUNC-001 {Functional requirement}
- [ ] AC-TEST-001 {Test requirement}
- [ ] AC-CYCLE-001 {Lifecycle validation}
- [ ] AC-LINT-001 Lint passes
- [ ] AC-SEC-001 No secrets

## Changes Manifest

| File | Change |
|------|--------|
| `src/file.py` | New -- description |

## Definition of Done

- [ ] All acceptance criteria checked
- [ ] Tests pass
- [ ] Lint passes
- [ ] Only manifest files staged

## TDD Cycle Log

## Comments
```

### Key design principles

**1. Sections that devbench parses must be `##` headings:**
Status, Target Repository, Description, Dependencies, Acceptance Criteria, Changes Manifest, Definition of Done, TDD Cycle Log, Comments.

**2. Custom sections go inside `## Description` as `###` sub-headings:**
Definition of Ready, Depends On This, Approach, Code Standards, Related Specifications. These are visible to the executor agent as part of the parsed description.

**3. Code Standards are repeated in every task:**
Context windows compress over time. Having the rules fresh in every work unit prevents drift. This is intentional repetition, not DRY violation -- each work unit is an independent execution context.

**4. Acceptance criteria use typed prefixes:**
- `AC-FUNC-NNN` -- Functional requirements (what the code must do)
- `AC-TEST-NNN` -- Test requirements (what tests must exist)
- `AC-CYCLE-NNN` -- Lifecycle validation (real end-to-end cycle proving it works)
- `AC-DOC-NNN` -- Documentation requirements
- `AC-LINT-NNN` -- Lint/format requirements
- `AC-SEC-NNN` -- Security requirements

**5. Changes Manifest uses table format:**
`| File | Change |` with one row per file. This is what the changes_manifest judge validates against. See [docs/authoring-manifests.md](authoring-manifests.md) for the three patterns that avoid the most common authoring defect (test-only manifests on tasks whose Approach authorises TDD GREEN production fixes), and for the runtime amendment workflow that rescues cases the author could not anticipate.

**6. Every implementation task needs a lifecycle cycle AC:**
If a task implements behavior, it must have an `AC-CYCLE` that proves the behavior works in a real operation cycle -- not just that a unit test passes. Example: "After implementation, create a temp git repo, call the function, verify the output."

---

## Phase 4: Code Standards Block

Embed this in every task's `### Code Standards` section inside `## Description`. Adjust rules to your project's needs, but never remove the critical rules.

### Critical rules (violation = automatic rejection)

1. **NO FALLBACK LOGIC** -- Fail loudly. Never catch and silently continue.
2. **NO SILENT FAILURES** -- Every error to stderr with non-zero exit code.
3. **FAIL FAST** -- Detect errors early. Exit on first error with clear message.
4. **NO HARD-CODED VALUES** -- All constants in a dedicated constants module. All config from environment variables, config files, or function parameters.
5. **NO TEMPORAL LOGIC** -- No `sleep()`. Use readiness detection with configurable timeouts.
6. **ALL CODE DYNAMIC AND INPUT-DRIVEN** -- No static data, no magic numbers.
7. **NO BYPASS ANNOTATIONS** -- No `noqa`, `nosec`, `type: ignore`, `pragma: no cover`.
8. **NO UNICODE DASH-EMS** -- Use `--` (double hyphen), not the em-dash character (U+2014).

### Supporting rules

- **SOLID, DRY, 12-Factor** -- Standard architecture principles
- **TDD mandatory** -- Tests before implementation
- **No stub tests** -- Every assertion must be able to fail
- **Test error paths** -- Every error condition gets a test
- **Stage only** -- `git add` relevant files only. Never commit/push/create PRs.
- **No secrets** -- No credentials in source code
- **No eval()** -- Never execute dynamic code

---

## Phase 5: Lifecycle Journey Tests

The most important tests are lifecycle journeys -- they chain multiple operations into realistic user workflows. Every backlog should include journey tests that exercise the full cycle.

### Examples by project type

**Application development:**
```
bootstrap -> configure -> build -> test -> deploy -> verify -> teardown
```

**Infrastructure migration (e.g., VMware to EC2):**
```
discover inventory -> plan migration -> create target infra -> migrate workload -> validate connectivity -> cutover DNS -> verify application -> decommission source
```

**Cloud operations:**
```
provision resources -> configure security groups -> deploy application -> health check -> scale up -> scale down -> terminate
```

**Package manager (like kanon):**
```
bootstrap -> install -> verify packages -> validate manifests -> clean -> verify clean
install with marketplace -> verify plugins registered -> clean -> verify plugins removed
```

### Journey test structure

Each journey test should:
1. Set up real fixtures (git repos, config files, infrastructure state)
2. Execute the full chain of operations in order
3. Verify state at each step (not just the final state)
4. Clean up and verify cleanup is complete
5. Cover both the happy path and key error paths

---

## Phase 6: Git Strategy

DevBench supports two git workflow modes — choose one when planning the backlog:

- **Multi-PR (default)** — one branch and one PR per task. Best for independent work that can ship separately.
- **Single-PR (single-branch + defer_pr)** — all tasks commit to one shared branch; one PR for the batch via `devbench git-ops-finalize <repo>` after all units complete. Best for large migrations where the entire backlog ships as one reviewable PR.

Single-PR mode is enabled in `devbench.yaml`:

```yaml
git_ops:
  single_branch: feat/my-feature
  defer_pr: true
```

For the full lifecycle and trade-offs of each mode, see [Multi-PR vs single-PR mode](architecture.md#6-multi-pr-vs-single-pr-mode) in the architecture doc.

## Phase 7: Validation

Before executing the backlog, run:

```bash
devbench validate-backlog
```

This checks:
1. Every file path in the index exists on disk
2. Status consistency between index and work unit files
3. No orphaned files (recursive scan including nested directories)
4. All dependency IDs reference real work units
5. Status Summary counts match actual distribution
6. Task files have non-empty Description
7. Task files have Acceptance Criteria with AC- items
8. Task files have Changes Manifest entries
9. Task files have Definition of Done section
10. No em-dash characters in work unit files

Fix all errors before starting execution.

---

## Real Example

The `kanon-migration-backlog` demonstrates this process for migrating a 23,000-line multi-repo management tool into a CLI package:

```
specs/
  SPEC-repo-to-kanon-migration.md    -- 800+ line spec with 14 phases
  BACKLOG-repo-bugs.md               -- 20 prioritized bugs found during audit
  SPEC-repo-greenfield-refactor.md   -- Future refactoring plan (out of scope)
  CODE-STANDARDS-BLOCK.md            -- Reusable code standards template
  TEMPLATE-work-unit.md              -- Work unit template

BACKLOG.md                            -- 116 work units indexed

backlog/
  config/devbench.yaml
  E0/                                 -- 1 Epic
    E0-F1/ through E0-F9/            -- 9 Features
      E0-F1-S1/ through ...          -- 17 Stories (= 17 commits)
        E0-F1-S1-T1.md through ...   -- 89 Tasks
```

Key metrics:
- 116 work units (1 epic, 9 features, 17 stories, 89 tasks)
- 17 commits in 1 PR
- Every task has full Code Standards block (8 critical rules)
- 14 lifecycle AC-CYCLE injections across implementation tasks
- 4 dedicated journey test tasks (bootstrap, marketplace, repo commands, cross-cutting)
- 7 E2E verification tasks per story
- 20 bug fixes with TDD (48 unit tests + 23 integration tests)
- `devbench validate-backlog` passes with zero errors

---

## Checklist: Before You Start Execution

- [ ] Spec is complete with audit findings, target architecture, phases, test plans
- [ ] Bug backlog is prioritized and referenced from spec
- [ ] All work units follow the contract format (9 required `##` sections)
- [ ] Custom sections (Definition of Ready, Approach, Code Standards) are `###` inside Description
- [ ] Code Standards block is in every task file
- [ ] Every implementation task has an AC-CYCLE lifecycle validation
- [ ] Journey test tasks exist for every major user workflow
- [ ] Changes Manifest uses table format
- [ ] Definition of Done section exists in every task
- [ ] No em-dash characters anywhere
- [ ] No hard-coded organization-specific values in test code
- [ ] Dependencies form a valid DAG (no cycles, all IDs exist)
- [ ] Status Summary uses per-epic format with correct counts
- [ ] `devbench validate-backlog` passes with zero errors
