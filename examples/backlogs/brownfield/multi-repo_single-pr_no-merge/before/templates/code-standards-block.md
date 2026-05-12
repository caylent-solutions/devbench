<!--
Canonical Code Standards block. Copy verbatim into the `### Code Standards`
subsection (inside `## Description`) of EVERY task file in this backlog.

This duplication is intentional per docs/creating-specs-and-backlogs.md
Phase 4: each work unit is an independent execution context, and judges read
these rules from the work unit itself.
-->

### Code Standards

All code in this work unit MUST comply with the following rules. These are checked by the LLM review judges (`code_review`, `test_review`, `doc_review`, `changes_manifest`, `security_review`) and trigger `REVIEW_FAIL` when violated. The work unit cannot reach `done` until every judge logs `REVIEW_PASS`.

#### Critical Rules (Violation = Automatic Rejection)

1. **NO FALLBACK LOGIC** -- If an operation can fail, it MUST fail loudly. Never catch an exception and silently continue. Never provide a default value when the real value is missing. Exceptions: the resolver's documented lockfile-catalog-source fallback for `kanon install` / `kanon doctor`, and the existing `git ls-remote` retry policy (`KANON_GIT_RETRY_COUNT` / `KANON_GIT_RETRY_DELAY`) -- both in `spec/kanon-list-add-lock-features-spec.md` Section 4 header and Section 7.
2. **NO SILENT FAILURES** -- Every error must produce a clear, actionable error message sent to stderr. Every error must result in a non-zero exit code. Never swallow exceptions. Never log-and-continue when the operation was required to succeed.
3. **FAIL FAST** -- Detect errors at the earliest possible point. Validate inputs before processing. Check prerequisites before starting work. Exit immediately on the first error with a message that tells the user exactly what went wrong and what to do about it.
4. **NO HARD-CODED VALUES** -- No URLs, paths, timeouts, retry counts, port numbers, hostnames, credentials, feature flags, or environment-specific values in source code. All constants live in `src/kanon_cli/constants.py`, never inline in source files. All configuration comes from environment variables, configuration files, or function parameters.
5. **NO TEMPORAL LOGIC** -- Never use `time.sleep()`, `asyncio.sleep()`, or any time-based delay as a synchronization mechanism. The existing `KANON_GIT_RETRY_DELAY` (wait between known-failed `git ls-remote` retries) is the ONLY allowed sleep-based wait in this codebase. Use readiness detection, event-driven callbacks, or polling with configurable timeouts everywhere else.
6. **ALL CODE MUST BE DYNAMIC AND INPUT-DRIVEN** -- No static data, no hard-coded test fixtures embedded in source, no magic numbers. All thresholds, limits, paths, and identifiers must be parameterized.
7. **NO BYPASS ANNOTATIONS** -- Never add `# noqa`, `# nosec`, `# type: ignore`, `# pragma: no cover`, or any annotation that suppresses a linter, type checker, or security scanner finding. Fix the finding instead. The kanon repo has a documented vendored carve-out for `src/kanon_cli/repo/` (the embedded repo-tool fork): mypy and bandit scope to non-vendored code only. This carve-out is a scope demarcation, NOT a bypass annotation; do not extend it to new paths.
8. **NO DASH-EMS IN CODE OR TESTS** -- Do not use the em-dash character (Unicode U+2014) in any source file, test file, or work-unit `.md` file. Use `--` (double hyphen) in comments and docstrings if a dash is needed. `devbench validate-backlog` rule 10 enforces this on work-unit files.

#### Architecture Principles

- **SOLID** -- Single Responsibility, Open/Closed, Liskov Substitution, Interface Segregation, Dependency Inversion.
- **DRY** -- Extract shared logic into reusable helpers under `src/kanon_cli/utils/`, `src/kanon_cli/core/`, or `src/kanon_cli/commands/` as appropriate. No duplicate code.
- **12-Factor App** -- Config from environment or YAML. Explicit dependencies declared in `pyproject.toml`. Logs to stdout/stderr.

#### Testing Rules

- **TDD MANDATORY** -- Write failing tests BEFORE implementation. Source/test atomicity (`devbench/docs/source-test-atomicity.md`): every new `.py` source file under `src/kanon_cli/` has its matching `tests/unit/test_<basename>.py` in the SAME work unit's Changes Manifest.
- **NO STUB TESTS** -- Every test must have assertions that can actually fail. No `assert True`, no `assert obj is not None` as the only assertion, no TODO-marked test bodies.
- **TEST ERROR PATHS** -- Every error condition must have a test. Network failures, malformed inputs, missing files, permission errors, timeouts, signal interruption.
- **PARAMETRIZE** -- Use `@pytest.mark.parametrize` for multiple scenarios. One test function per logical assertion.
- **REAL TESTS ONLY** -- Integration tests run against a real fixture git server (or mocks matching `git ls-remote --refs` output verbatim). No `MagicMock` over network APIs without a real-server parity test.
- **NO SKIPS / XFAILS / XPASSED** -- AC-FINAL-013. Skipped tests hide regressions.

#### Git Rules

- **STAGE ONLY** -- Use `git add` for relevant files (only those in this work unit's `## Changes Manifest`). Do NOT commit, push, or create PRs from inside the executor. The orchestrator owns the git-ops lifecycle.
- **NO --no-verify** -- Never bypass git hooks.
- **SELECTIVE STAGING** -- Only stage files in the Changes Manifest. `git add -A` is forbidden; use explicit `git add <path>` per file.

#### Security Rules

- **NO SECRETS** -- No API keys, tokens, passwords, GitHub PATs, or SSH keys in source code, tests, or fixtures.
- **NO eval() / exec()** -- Never execute dynamic code from external input.
- **NO PROVIDER-SPECIFIC API CALLS** -- Per spec Section 3.6, kanon NEVER calls provider HTTP APIs (`api.github.com`, `gitlab.com/api`, etc.) and NEVER shells out to provider CLIs (`gh`, `glab`, `bb`, `tea`, `aws codecommit`, `az repos`). Every git interaction is via the `git` binary only.
- **NO AUTH HANDLING** -- kanon NEVER prompts for credentials, NEVER caches them, NEVER reads them from anywhere except by delegating to the operator's git client. Auth-error patterns are detected in stderr for retry-policy purposes only (`GIT_AUTH_ERROR_PATTERNS` in `constants.py`).

#### Error Handling Contract

- Raise specific exceptions (not generic `Exception`).
- Include context in error messages: file paths, variable names, expected vs actual values, the operator's likely next step.
- Never catch and discard exceptions.
- Never call `sys.exit()` from library code; only from CLI command handlers in `src/kanon_cli/commands/` or `src/kanon_cli/cli.py`.
- Error messages follow the standard shape per spec Section 4: `ERROR: <one-line summary>` then optional context lines (wrapped at 80 cols), then a remediation line when applicable.
