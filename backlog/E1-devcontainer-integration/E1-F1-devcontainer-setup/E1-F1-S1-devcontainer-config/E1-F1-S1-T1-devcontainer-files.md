# E1-F1-S1-T1: Add devcontainer.json, postcreate-wrapper.sh, devcontainer-functions.sh, project-setup.sh

## Status: in-queue

## Spec Reference

| Section | Lines | Description |
|---------|-------|-------------|
| Phase 1b | — | Copy lifecycle scripts from `caylent-solutions/devcontainer`; write project-specific `project-setup.sh` |

## Description

This task creates the `.devcontainer/` directory and its four files. `devcontainer.json` configures the container name and references the `postcreate-wrapper.sh` hook. `postcreate-wrapper.sh` and `devcontainer-functions.sh` are copied verbatim from the `caylent-solutions/devcontainer` shared platform. `project-setup.sh` is written specifically for DevBench: it checks for `uv`, installs it via the official installer if absent, then runs `uv sync --all-extras`.

## Target Repository

- **Repo:** `caylent-solutions/devbench`
- **Local path:** `{JUDGE_WORKSPACE_ROOT}/devbench/`
- **Branch:** `feature/devcontainer-setup`

## Dependencies

| ID | Title | Status |
|----|-------|--------|
| E0 | Fix Critical Issues | in-queue |

## Blocked By

No blockers.

## Definition of Ready

- [❌] All dependencies listed above are `done` (verified in BACKLOG.md)
- [❌] All spec sections in the Spec Reference table have been read by the agent
- [❌] Agent has read CLAUDE.md in the repository root
- [❌] Agent has read backlog/AGENT-INSTRUCTIONS.md
- [❌] Agent has completed the pre-flight checklist
- [❌] No other agent has this work unit `in-progress`

## Definition of Done

- [❌] All acceptance criteria met (every AC item below shows a green checkmark)
- [❌] All tests pass — unit tests AND functional tests
- [❌] `make validate` passes in the target repo with zero errors
- [❌] TDD Cycle Log shows red-green-refactor cycle for each test written
- [❌] Documentation created or updated per acceptance criteria
- [❌] All code compliant with CLAUDE.md standards
- [❌] Changes manifest verified — only the files listed below were modified
- [❌] Judge agent system has been notified and approval is pending

## Acceptance Criteria

- [❌] AC-1: `.devcontainer/devcontainer.json` is valid JSON with `"name": "devbench"` and a `postCreateCommand` referencing `postcreate-wrapper.sh`
- [❌] AC-2: `.devcontainer/postcreate-wrapper.sh` and `.devcontainer/devcontainer-functions.sh` are present and executable
- [❌] AC-3: `.devcontainer/project-setup.sh` is executable and contains a `command -v uv` check followed by the official `uv` install command
- [❌] AC-4: `.devcontainer/project-setup.sh` calls `uv sync --all-extras`
- [❌] AC-5: `project-setup.sh` uses `log_info` calls at start and end (provided by `devcontainer-functions.sh`)
- [❌] AC-DOC-1: `project-setup.sh` has a comment header block describing what it does and when it is called

## Changes Manifest

| Action | File Path |
|--------|-----------|
| create | `.devcontainer/devcontainer.json` |
| create | `.devcontainer/postcreate-wrapper.sh` |
| create | `.devcontainer/devcontainer-functions.sh` |
| create | `.devcontainer/project-setup.sh` |

## Code Standards and Requirements

### Tier 1: Universal Rules

1. Follow SOLID principles — single responsibility, open/closed, Liskov substitution, interface segregation, dependency inversion
2. DRY — do not duplicate logic; extract shared code into helpers
3. Fail-Fast — validate inputs at the earliest possible point; raise immediately on bad state
4. 12-Factor App — configuration via environment variables, no hardcoded values
5. Security — never log secrets; never commit credentials; validate all external inputs
6. No time-based waits — do not use `sleep()` or `time.sleep()` to wait for external state
7. No bypass annotations — do not use `# noqa`, `# type: ignore`, or `# nosec` without a documented reason
8. No `--no-verify` — never skip git hooks
9. No hardcoded config — all configuration comes from environment variables or explicit parameters
10. Explicit over implicit — prefer explicit parameter passing over globals and implicit state
11. Single source of truth — each piece of configuration lives in exactly one place
12. No silent failures — every error must be surfaced; never swallow exceptions without logging
13. Immutable data — prefer immutable structures (`frozenset`, `tuple`, `NamedTuple`) for configuration
14. Type annotations — all public functions and methods must have complete type annotations
15. Docstrings — all public modules, classes, and functions must have docstrings
16. Test coverage — every new function must have at least one unit test
17. Small functions — functions should do one thing; aim for under 30 lines
18. No global mutable state — module-level variables must be constants (immutable)
19. Dependency injection — pass dependencies explicitly rather than importing them inside functions
20. Error messages must be actionable — tell the user what to do, not just what went wrong
21. Log at appropriate levels — DEBUG for tracing, INFO for milestones, WARNING for recoverable issues, ERROR for failures
22. No print statements in library code — use the logging module
23. Backwards compatibility — do not remove or rename public interfaces without a deprecation path

### Tier 2: Contextual Rules — Shell/Bash

- `set -euo pipefail` in all shell scripts
- `#!/usr/bin/env bash` shebang line
- All shell scripts must be made executable (`chmod +x`)
- Install `uv` using the canonical: `curl -LsSf https://astral.sh/uv/install.sh | sh`
- Add `$HOME/.local/bin` to `PATH` after install

### Tier 2: Contextual Rules — JSON/YAML config

- `devcontainer.json` must be valid JSON; validate with `python3 -m json.tool`
- No trailing commas in JSON

## Test Plan (Spec-Driven TDD)

### Contract Definition

`project-setup.sh` is an idempotent bash script. Input: system state (uv present or absent). Output: zero exit code, `uv` on PATH, project dependencies installed. The script sources `devcontainer-functions.sh` for `log_info`.

### Acceptance Tests (BDD-style)

# AC-1: devcontainer.json is valid JSON with correct name
Given `.devcontainer/devcontainer.json` exists
When parsed with `python3 -m json.tool`
Then no parse error and the `name` field equals `"devbench"`

# AC-3: project-setup.sh checks for uv before installing
Given `uv` is already on PATH
When `project-setup.sh` is run
Then the `curl` install command is not executed

# AC-4: project-setup.sh runs uv sync
Given `uv` is available
When `project-setup.sh` is run
Then `uv sync --all-extras` is called

### Unit Tests

| Test Name | Spec Ref | Status |
|-----------|----------|--------|
| test_devcontainer_json_valid_json | Phase 1b | ❌ |
| test_devcontainer_json_name_field | Phase 1b | ❌ |
| test_project_setup_calls_uv_sync | Phase 1b | ❌ |
| test_project_setup_skips_install_when_uv_present | Phase 1b | ❌ |
| test_devcontainer_scripts_are_executable | Phase 1b | ❌ |

### TDD Cycle Log

<!-- TDD cycle log will be filled in by agent -->

## Rollback Instructions

1. `git rm -r .devcontainer/`
2. Verify `make validate` passes
3. Note: T2 (shell.env and tooling) depends on this task; rolling back blocks T2

## Output Location

| Artifact | Path |
|----------|------|
| Devcontainer JSON | `{JUDGE_WORKSPACE_ROOT}/devbench/.devcontainer/devcontainer.json` |
| Postcreate wrapper | `{JUDGE_WORKSPACE_ROOT}/devbench/.devcontainer/postcreate-wrapper.sh` |
| Devcontainer functions | `{JUDGE_WORKSPACE_ROOT}/devbench/.devcontainer/devcontainer-functions.sh` |
| Project setup | `{JUDGE_WORKSPACE_ROOT}/devbench/.devcontainer/project-setup.sh` |

## Comments / Agent Log

<!-- Agent log will be filled in during execution -->
