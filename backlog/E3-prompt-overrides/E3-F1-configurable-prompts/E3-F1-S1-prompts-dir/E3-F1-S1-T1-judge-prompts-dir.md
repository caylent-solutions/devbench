# E3-F1-S1-T1: Add JUDGE_PROMPTS_DIR to config.py, update load_prompt() to check it first, document in shell.env.example

## Status: in-queue

## Spec Reference

| Section | Lines | Description |
|---------|-------|-------------|
| Phase 5 | — | Add `JUDGE_PROMPTS_DIR` env var to `config.py`; update `load_prompt()` to check `JUDGE_PROMPTS_DIR` first, then fall back to `prompts/` directory; document in `shell.env.example` |

## Description

This task implements the full `JUDGE_PROMPTS_DIR` feature in three coordinated changes to three files. In `config.py` it adds `PROMPTS_DIR: Optional[Path]` which is `None` when the env var is absent. In the module that contains `load_prompt()` it updates the function to check `PROMPTS_DIR / name` first (if `PROMPTS_DIR` is set and the file exists there) before falling back to the default `prompts/` directory. In `shell.env.example` it adds the `JUDGE_PROMPTS_DIR` entry with a comment explaining the override behaviour.

## Target Repository

- **Repo:** `caylent-solutions/devbench`
- **Local path:** `{JUDGE_WORKSPACE_ROOT}/devbench/`
- **Branch:** `feature/configurable-prompts`

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

- [❌] AC-1: `PROMPTS_DIR: Optional[Path]` is added to `config.py`; `None` when `JUDGE_PROMPTS_DIR` is absent or empty
- [❌] AC-2: `PROMPTS_DIR` is a `Path` object when `JUDGE_PROMPTS_DIR` is set to a non-empty string
- [❌] AC-3: `load_prompt(name)` returns content from `PROMPTS_DIR / name` when that file exists
- [❌] AC-4: `load_prompt(name)` returns content from default `prompts/` when `PROMPTS_DIR` is `None`
- [❌] AC-5: `load_prompt(name)` returns content from default `prompts/` when `PROMPTS_DIR` is set but `PROMPTS_DIR / name` does not exist
- [❌] AC-6: `load_prompt(name)` raises `FileNotFoundError` with an actionable message when the prompt is not found in either location
- [❌] AC-7: `shell.env.example` contains `export JUDGE_PROMPTS_DIR=""` with a comment describing the override behaviour
- [❌] AC-DOC-1: `load_prompt()` docstring states the two-step lookup order and what happens on fallback

## Changes Manifest

| Action | File Path |
|--------|-----------|
| modify | `src/devbench/config.py` |
| modify | `src/devbench/` (module containing `load_prompt()`) |
| modify | `shell.env.example` |

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

### Tier 2: Contextual Rules — Python

- From spec: `PROMPTS_DIR: Path = Path(os.environ.get("JUDGE_PROMPTS_DIR", "")) or Path(__file__).parent.parent.parent / "prompts"` — but this task should keep it `Optional[Path]` set to `None` when unset, for cleaner fallback logic in `load_prompt()`
- `load_prompt()` error message on `FileNotFoundError`: `f"Prompt '{name}' not found in {PROMPTS_DIR} or default prompts/ directory"`
- Use `logger.debug("Loading prompt '%s' from %s", name, path)` on each load

## Test Plan (Spec-Driven TDD)

### Contract Definition

```python
# config.py addition:
_judge_prompts_dir = os.environ.get("JUDGE_PROMPTS_DIR", "").strip()
PROMPTS_DIR: Optional[Path] = Path(_judge_prompts_dir) if _judge_prompts_dir else None

# load_prompt() updated logic:
def load_prompt(name: str) -> str:
    """
    Load a prompt file by name.

    Lookup order:
    1. PROMPTS_DIR / name (if PROMPTS_DIR is set and file exists)
    2. Default prompts/ directory relative to package root

    Raises:
        FileNotFoundError: If not found in either location.
    """
    if PROMPTS_DIR is not None:
        override_path = PROMPTS_DIR / name
        if override_path.exists():
            logger.debug("Loading prompt '%s' from override: %s", name, override_path)
            return override_path.read_text()
        logger.debug("Prompt '%s' not in override dir, falling back to default", name)
    default_path = Path(__file__).parent.parent.parent / "prompts" / name
    if not default_path.exists():
        raise FileNotFoundError(
            f"Prompt '{name}' not found in "
            f"{PROMPTS_DIR or '(no override)'} or default {default_path.parent}"
        )
    logger.debug("Loading prompt '%s' from default: %s", name, default_path)
    return default_path.read_text()
```

### Acceptance Tests (BDD-style)

# AC-1: PROMPTS_DIR is None when env var not set
Given `JUDGE_PROMPTS_DIR` is not set
When `config` module imported
Then `config.PROMPTS_DIR is None`

# AC-3: override path used when file present
Given `JUDGE_PROMPTS_DIR=/tmp/prompts-override` and `/tmp/prompts-override/code_review.txt` = "override text"
When `load_prompt("code_review.txt")` called
Then result == "override text"

# AC-5: fallback when override file missing
Given `JUDGE_PROMPTS_DIR=/tmp/prompts-override` and no `doc_review.txt` in override dir
When `load_prompt("doc_review.txt")` called
Then result == content of default `prompts/doc_review.txt` (no exception)

# AC-6: error when not found anywhere
Given `JUDGE_PROMPTS_DIR=/tmp/prompts-override` and `nonexistent.txt` absent from both dirs
When `load_prompt("nonexistent.txt")` called
Then `FileNotFoundError` raised with message mentioning the prompt name

### Unit Tests

| Test Name | Spec Ref | Status |
|-----------|----------|--------|
| test_prompts_dir_is_none_when_env_var_absent | Phase 5 | ❌ |
| test_prompts_dir_is_path_when_env_var_present | Phase 5 | ❌ |
| test_load_prompt_returns_override_when_file_present | Phase 5 | ❌ |
| test_load_prompt_falls_back_when_override_file_missing | Phase 5 | ❌ |
| test_load_prompt_uses_default_when_prompts_dir_unset | Phase 5 | ❌ |
| test_load_prompt_raises_file_not_found_when_absent_everywhere | Phase 5 | ❌ |
| test_shell_env_example_has_judge_prompts_dir | Phase 5 | ❌ |

### TDD Cycle Log

<!-- TDD cycle log will be filled in by agent -->

## Rollback Instructions

1. `git checkout main -- src/devbench/config.py shell.env.example`
2. Revert changes to the `load_prompt()` containing module
3. Verify `make validate` passes

## Output Location

| Artifact | Path |
|----------|------|
| Config module | `{JUDGE_WORKSPACE_ROOT}/devbench/src/devbench/config.py` |
| Shell env example | `{JUDGE_WORKSPACE_ROOT}/devbench/shell.env.example` |

## Comments / Agent Log

<!-- Agent log will be filled in during execution -->
