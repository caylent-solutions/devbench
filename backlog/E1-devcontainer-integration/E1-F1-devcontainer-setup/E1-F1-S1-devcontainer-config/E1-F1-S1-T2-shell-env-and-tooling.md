# E1-F1-S1-T2: Add shell.env.example, update .gitignore and .tool-versions, update start scripts to source shell.env

## Status: in-queue

## Spec Reference

| Section | Lines | Description |
|---------|-------|-------------|
| Phase 1b | — | Create `shell.env.example`; add `shell.env` to `.gitignore`; add `uv` to `.tool-versions`; update start scripts to source `shell.env` |

## Description

This task creates `shell.env.example` as the canonical reference for all DevBench environment variables, adds `shell.env` to `.gitignore` to prevent accidental credential commits, adds a `uv` version entry to `.tool-versions`, and updates both start scripts to source `shell.env` from the project root if it exists. The `shell.env.example` content documents the complete configuration surface including all required and optional variables introduced in E0.

## Target Repository

- **Repo:** `caylent-solutions/devbench`
- **Local path:** `{JUDGE_WORKSPACE_ROOT}/devbench/`
- **Branch:** `feature/devcontainer-setup`

## Dependencies

| ID | Title | Status |
|----|-------|--------|
| E1-F1-S1-T1 | Add devcontainer.json, postcreate-wrapper.sh, devcontainer-functions.sh, project-setup.sh | in-queue |

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

- [❌] AC-1: `shell.env.example` exists at project root with exports for all required vars: `JUDGE_CLAUDE_MODEL`, `JUDGE_GH_ORG`, `JUDGE_ALLOWED_REPOS`, `JUDGE_WORKSPACE_ROOT`
- [❌] AC-2: `shell.env.example` also exports all optional vars: `JUDGE_MERGE_STRATEGY`, `JUDGE_MAX_RETRIES`, `JUDGE_GH_TIMEOUT`, `JUDGE_EXECUTOR_TIMEOUT`, `JUDGE_EXECUTOR_MAX_TURNS`, `JUDGE_USE_BEDROCK`, `JUDGE_BEDROCK_REGION`, `JUDGE_PROMPTS_DIR`
- [❌] AC-3: `shell.env` appears as an entry in `.gitignore`
- [❌] AC-4: `.tool-versions` has a line beginning with `uv ` specifying a version
- [❌] AC-5: `scripts/start.sh` sources `shell.env` from the project root before the required-var guard, using a conditional so it does not fail if the file is absent
- [❌] AC-6: `scripts/start-interactive.sh` has the same `shell.env` sourcing as `start.sh`
- [❌] AC-DOC-1: `shell.env.example` header explains its purpose, where to copy it, and that it is sourced automatically by both the devcontainer lifecycle and the start scripts

## Changes Manifest

| Action | File Path |
|--------|-----------|
| create | `shell.env.example` |
| modify | `.gitignore` |
| modify | `.tool-versions` |
| modify | `scripts/start.sh` |
| modify | `scripts/start-interactive.sh` |

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

- Source `shell.env` using: `SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"` then `[ -f "${SCRIPT_DIR}/../shell.env" ] && source "${SCRIPT_DIR}/../shell.env"`
- The source line must appear before any use of the environment variables
- Do not fail or exit if `shell.env` does not exist — its absence is valid

## Test Plan (Spec-Driven TDD)

### Contract Definition

`shell.env.example` is a shell script fragment — no executable semantics, just `export KEY="value"` lines. When sourced into a shell session, it sets all listed variables. Its content must be kept in sync with `config.py`'s environment variable references.

### Acceptance Tests (BDD-style)

# AC-1 & AC-2: shell.env.example covers all vars
Given `shell.env.example` is parsed for `export` statements
When all exported variable names are extracted
Then the set includes all vars in {JUDGE_CLAUDE_MODEL, JUDGE_GH_ORG, JUDGE_ALLOWED_REPOS, JUDGE_WORKSPACE_ROOT, JUDGE_MERGE_STRATEGY, JUDGE_MAX_RETRIES, JUDGE_GH_TIMEOUT, JUDGE_EXECUTOR_TIMEOUT, JUDGE_EXECUTOR_MAX_TURNS, JUDGE_USE_BEDROCK, JUDGE_BEDROCK_REGION, JUDGE_PROMPTS_DIR}

# AC-3: shell.env gitignored
Given `.gitignore` contents
When searched for "shell.env"
Then a matching non-commented line is found

# AC-5: start.sh sources shell.env
Given `shell.env` at project root with `export JUDGE_GH_ORG=myorg`
When `start.sh` is executed in a subshell up to the guard block
Then `$JUDGE_GH_ORG` is set to `myorg`

### Unit Tests

| Test Name | Spec Ref | Status |
|-----------|----------|--------|
| test_shell_env_example_has_all_required_vars | Phase 1b | ❌ |
| test_shell_env_example_has_all_optional_vars | Phase 1b | ❌ |
| test_shell_env_in_gitignore | Phase 1b | ❌ |
| test_tool_versions_has_uv_entry | Phase 1b | ❌ |
| test_start_sh_sources_shell_env_when_present | Phase 1b | ❌ |
| test_start_sh_does_not_fail_when_shell_env_absent | Phase 1b | ❌ |

### TDD Cycle Log

<!-- TDD cycle log will be filled in by agent -->

## Rollback Instructions

1. `git rm shell.env.example`
2. `git checkout main -- .gitignore .tool-versions scripts/start.sh scripts/start-interactive.sh`
3. Verify `make validate` passes

## Output Location

| Artifact | Path |
|----------|------|
| Shell env example | `{JUDGE_WORKSPACE_ROOT}/devbench/shell.env.example` |
| Updated gitignore | `{JUDGE_WORKSPACE_ROOT}/devbench/.gitignore` |
| Updated tool-versions | `{JUDGE_WORKSPACE_ROOT}/devbench/.tool-versions` |
| Updated start script | `{JUDGE_WORKSPACE_ROOT}/devbench/scripts/start.sh` |
| Updated interactive script | `{JUDGE_WORKSPACE_ROOT}/devbench/scripts/start-interactive.sh` |

## Comments / Agent Log

<!-- Agent log will be filled in during execution -->
