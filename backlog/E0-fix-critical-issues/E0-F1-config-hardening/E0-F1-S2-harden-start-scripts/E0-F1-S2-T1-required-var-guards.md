# E0-F1-S2-T1: Remove hardcoded JUDGE_GH_ORG, add required var guards to both scripts

## Status: done

## Spec Reference

| Section | Lines | Description |
|---------|-------|-------------|
| Phase 3 §3.4 | — | Remove `JUDGE_GH_ORG=caylent-solutions`; add required-var guard loop checking `JUDGE_CLAUDE_MODEL`, `JUDGE_GH_ORG`, `JUDGE_ALLOWED_REPOS`, `JUDGE_WORKSPACE_ROOT` |

## Description

This task edits both `scripts/start.sh` and `scripts/start-interactive.sh`. It removes the line that hardcodes `JUDGE_GH_ORG=caylent-solutions` and inserts a guard block at the top of each script that iterates over the four required environment variables and exits with code 1 if any are unset, printing a descriptive error to stderr. The guard must appear before any substantive logic so that the failure is always immediate.

## Target Repository

- **Repo:** `caylent-solutions/devbench`
- **Local path:** `{JUDGE_WORKSPACE_ROOT}/devbench/`
- **Branch:** `feature/harden-start-scripts`

## Dependencies

| ID | Title | Status |
|----|-------|--------|
| E0-F1-S1-T3 | Add JUDGE_MERGE_STRATEGY configurable env var | in-queue |

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

- [❌] AC-1: Neither `scripts/start.sh` nor `scripts/start-interactive.sh` contains `JUDGE_GH_ORG=caylent-solutions`
- [❌] AC-2: Both scripts exit 1 with a stderr message naming the variable when `JUDGE_GH_ORG` is missing
- [❌] AC-3: Both scripts exit 1 with a stderr message naming the variable when `JUDGE_ALLOWED_REPOS` is missing
- [❌] AC-4: Both scripts exit 1 with a stderr message naming the variable when `JUDGE_WORKSPACE_ROOT` is missing
- [❌] AC-5: Both scripts exit 1 with a stderr message naming the variable when `JUDGE_CLAUDE_MODEL` is missing
- [❌] AC-6: Guard is placed before any logic that uses the variables
- [❌] AC-DOC-1: Each script has a comment block above the guard listing required variables and a brief description of each

## Changes Manifest

| Action | File Path |
|--------|-----------|
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

- Use `set -euo pipefail` — already present in both scripts; verify it stays at the top
- Use bash array syntax for the list of required variable names
- `${!var:-}` indirect expansion to dereference the variable name stored in `$var`
- Print error to stderr: `echo "Required environment variable $var is not set. Export it before running this script." >&2`
- Use `exit 1` exclusively; avoid `exit 2` or other codes

## Test Plan (Spec-Driven TDD)

### Contract Definition

Both scripts will contain a guard block of this form, appearing after the `set -euo pipefail` line and before any other logic:

```bash
# Required environment variables — set these before running
# JUDGE_CLAUDE_MODEL: Claude model identifier (e.g. us.anthropic.claude-sonnet-4-6-v1)
# JUDGE_GH_ORG: GitHub organization name (e.g. my-github-org)
# JUDGE_ALLOWED_REPOS: Comma-separated list of allowed repos (e.g. org/repo1,org/repo2)
# JUDGE_WORKSPACE_ROOT: Absolute path to workspace root (e.g. /workspaces/my-env)
required_vars=(JUDGE_CLAUDE_MODEL JUDGE_GH_ORG JUDGE_ALLOWED_REPOS JUDGE_WORKSPACE_ROOT)
for var in "${required_vars[@]}"; do
  if [ -z "${!var:-}" ]; then
    echo "Required environment variable $var is not set. Export it before running this script." >&2
    exit 1
  fi
done
```

Neither script contains `JUDGE_GH_ORG=caylent-solutions`.

### Acceptance Tests (BDD-style)

# AC-2: start.sh exits on missing JUDGE_GH_ORG
Given all required vars are unset except `JUDGE_GH_ORG`
When `bash scripts/start.sh` is executed
Then exit code is 1 and stderr line contains "JUDGE_GH_ORG"

# AC-1: Hardcoded org removed from start.sh
Given the file contents of `scripts/start.sh`
When the file is searched for "caylent-solutions"
Then no match is found

# AC-6: Guard is first logic block
Given `scripts/start.sh` with all required vars exported
When the guard section completes without error
Then remaining script logic is reached (guard did not prematurely exit)

### Unit Tests

| Test Name | Spec Ref | Status |
|-----------|----------|--------|
| test_start_sh_exits_on_missing_judge_claude_model | Phase 3 §3.4 | ❌ |
| test_start_sh_exits_on_missing_judge_gh_org | Phase 3 §3.4 | ❌ |
| test_start_sh_exits_on_missing_judge_allowed_repos | Phase 3 §3.4 | ❌ |
| test_start_sh_exits_on_missing_judge_workspace_root | Phase 3 §3.4 | ❌ |
| test_start_interactive_exits_on_missing_required_var | Phase 3 §3.4 | ❌ |
| test_start_sh_no_hardcoded_caylent_solutions | Phase 3 §3.4 | ❌ |

### TDD Cycle Log

<!-- TDD cycle log will be filled in by agent -->

## Rollback Instructions

1. `git checkout main -- scripts/start.sh scripts/start-interactive.sh`
2. Verify `make validate` passes
3. Note: E1-F1-S1-T2 modifies these same scripts to add `shell.env` sourcing; coordinate carefully

## Output Location

| Artifact | Path |
|----------|------|
| Start script | `{JUDGE_WORKSPACE_ROOT}/devbench/scripts/start.sh` |
| Interactive start script | `{JUDGE_WORKSPACE_ROOT}/devbench/scripts/start-interactive.sh` |

## Comments / Agent Log

<!-- Agent log will be filled in during execution -->
