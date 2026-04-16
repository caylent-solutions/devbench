<!--
This is a template for a task work unit. Copy it when creating a new task and replace the
placeholders shown in {curly braces} with concrete values.

Placeholder legend:
  {ID}                       Uppercase work unit ID, e.g. E1-F1-S1-T1
  {id_lower}                 Lowercase form of the ID, e.g. e1-f1-s1-t1
  {Title}                    Human-readable title, sentence case
  {org}/{repo}               Fully-qualified GitHub repo name
  {repo_short}               Short name (the part after the slash)
  {downstream_id}            ID of a work unit that depends on this one (delete row if none)
  {test_file}, {source_file} File paths inside the target repo
  {function/class}, {doc_file} Names referenced in the Approach section
-->

# {ID}: {Title}

## Status: in-queue

## Target Repository

- **Repo:** `{org}/{repo}`
- **Branch:** `backlog/{id_lower}`

## Description

{Detailed description of what this work unit accomplishes. Be specific about the problem being solved, the approach to take, and the expected outcome. Include file paths, function names, and line numbers where relevant.}

### Definition of Ready

- [ ] All dependency work units are `done`
- [ ] Target repository is cloned and accessible at `{JUDGE_WORKSPACE_ROOT}/{repo_short}`
- [ ] All prerequisite tools are installed (`uv`, `ruff`, `pytest`)
- [ ] The branch `backlog/{id_lower}` does not already exist (or is from a prior attempt)
- [ ] The spec and acceptance criteria are unambiguous -- no open questions

### Depends On This

| ID | Title | Status |
|----|-------|--------|
| {downstream_id} | {downstream_title} | {downstream_status} |

### Approach

1. **TDD RED:** Write failing tests in `{test_file}` for {functionality}
2. **TDD GREEN:** Implement `{function/class}` in `{source_file}` to make tests pass
3. **TDD REFACTOR:** Clean up if needed
4. **Integration:** {Any integration steps}
5. **Docs:** Update `{doc_file}` with {changes}
6. **Verify:** Run full suite: `uv run pytest tests/ -v && uv run ruff check src/ tests/`

### Code Standards

All code in this work unit MUST comply with the following rules. These are checked by the LLM review judges (`code_review`, `test_review`, `doc_review`, `changes_manifest`) and trigger `REVIEW_FAIL` when violated. The work unit cannot reach `done` until every judge logs `REVIEW_PASS`.

#### Critical Rules (Violation = Automatic Rejection)

1. **NO FALLBACK LOGIC** -- If an operation can fail, it MUST fail loudly. Never catch an exception and silently continue. Never provide a default value when the real value is missing. Never write "if X fails, try Y instead."
2. **NO SILENT FAILURES** -- Every error must produce a clear, actionable error message sent to stderr. Every error must result in a non-zero exit code. Never swallow exceptions. Never log-and-continue when the operation was required to succeed.
3. **FAIL FAST** -- Detect errors at the earliest possible point. Validate inputs before processing. Check prerequisites before starting work. Exit immediately on the first error with a message that tells the user exactly what went wrong and what to do about it.
4. **NO HARD-CODED VALUES** -- No URLs, paths, timeouts, retry counts, port numbers, hostnames, credentials, feature flags, or environment-specific values in source code. All constants must live in a dedicated constants module (e.g., `constants.py`), never inline in source files. All configuration must come from environment variables, configuration files, or function parameters.
5. **NO TEMPORAL LOGIC** -- Never use `time.sleep()`, `asyncio.sleep()`, or any time-based delay as a synchronization mechanism. Use readiness detection, event-driven callbacks, or polling with configurable timeouts.
6. **ALL CODE MUST BE DYNAMIC AND INPUT-DRIVEN** -- No static data, no hard-coded test fixtures embedded in source, no magic numbers. All thresholds, limits, paths, and identifiers must be parameterized.
7. **NO BYPASS ANNOTATIONS** -- Never add `# noqa`, `# nosec`, `# type: ignore`, `# pragma: no cover`, or any annotation that suppresses a linter, type checker, or security scanner finding. Fix the finding instead.
8. **NO DASH-EMS IN CODE OR TESTS** -- Do not use the em-dash character (unicode U+2014) in any Python source file or test file. Use `--` (double hyphen) in comments and docstrings if a dash is needed.

#### Architecture Principles

- **SOLID** -- Single Responsibility, Open/Closed, Liskov Substitution, Interface Segregation, Dependency Inversion.
- **DRY** -- Extract shared logic into reusable functions. No duplicate code.
- **12-Factor App** -- Config from environment. Explicit dependencies. Logs to stdout/stderr.

#### Testing Rules

- **TDD MANDATORY** -- Write failing tests BEFORE implementation.
- **NO STUB TESTS** -- Every test must have assertions that can actually fail.
- **TEST ERROR PATHS** -- Every error condition must have a test.
- **PARAMETRIZE** -- Use `@pytest.mark.parametrize` for multiple scenarios.

#### Git Rules

- **STAGE ONLY** -- Use `git add` for relevant files. Do NOT commit, push, or create PRs.
- **NO --no-verify** -- Never bypass git hooks.
- **SELECTIVE STAGING** -- Only stage files in the Changes Manifest.

#### Security Rules

- **NO SECRETS** -- No API keys, tokens, passwords in source code.
- **NO eval()** -- Never execute dynamic code.

#### Error Handling Contract

- Raise specific exceptions (not generic `Exception`)
- Include context in error messages (file paths, variable names, expected vs actual)
- Never catch and discard exceptions
- Never call `sys.exit()` from library code -- only from CLI command handlers

### Related Specifications

- **Migration spec:** `specs/SPEC-repo-to-kanon-migration.md` -- Section {X}, Phase {Y}
- **Bug backlog:** `specs/BACKLOG-repo-bugs.md` -- Bug #{N} (if applicable)
- **Greenfield spec:** `specs/SPEC-repo-greenfield-refactor.md` -- Phase R{N} (if applicable)

## Dependencies

| ID | Title | Status |
|----|-------|--------|
| {dep_id} | {dep_title} | {dep_status} |

## Acceptance Criteria

- [ ] AC-FUNC-001 {Functional requirement -- what the code must do}
- [ ] AC-FUNC-002 {Second functional requirement}
- [ ] AC-TEST-001 All new code has unit tests written BEFORE implementation (TDD RED then GREEN)
- [ ] AC-TEST-002 All new tests pass: `uv run pytest tests/ -v`
- [ ] AC-TEST-003 Coverage: {target}% on modified modules (`--cov={module} --cov-report=term-missing`)
- [ ] AC-TEST-004 No test stubs -- every test has meaningful assertions that can fail
- [ ] AC-TEST-005 {Specific integration test requirement}
- [ ] AC-CYCLE-001 {Lifecycle cycle validation -- real end-to-end test proving the feature works}
- [ ] AC-DOC-001 All affected documentation updated in the same commit
- [ ] AC-LINT-001 `uv run ruff check src/ tests/` passes with zero findings
- [ ] AC-LINT-002 `uv run ruff format --check src/ tests/` passes
- [ ] AC-SEC-001 No secrets, credentials, or hard-coded configuration values
- [ ] AC-SEC-002 No security bypass annotations (`nosec`, `noqa`, `type: ignore`)

## Changes Manifest

| File | Change |
|------|--------|
| `src/{path}/{file}.py` | New/Updated -- description |
| `tests/{path}/{test_file}.py` | New -- unit tests |
| `docs/{doc_file}.md` | Updated -- description |

## Definition of Done

- [ ] All acceptance criteria checked
- [ ] `uv run pytest tests/ -v` passes
- [ ] `uv run ruff check src/ tests/` passes
- [ ] `uv run ruff format --check src/ tests/` passes
- [ ] Only files in Changes Manifest are staged with `git add`

## TDD Cycle Log

## Comments
