# Code Standards canonical body (#230)

This is the operator-facing reference copy of the `### Code Standards` block the `spec-to-backlog` skill emits via `devbench.plugin_helpers.code_standards_template.emit_code_standards_block`. The Python helper carries the same content as a module-level constant so it has no filesystem dependency at runtime.

## Why this exists

Every leaf-task file in a devbench-managed backlog carries a `### Code Standards` block. Across a single backlog the block is byte-for-byte identical modulo the `#### Error Handling Contract` subsection's task-specific entries. Re-typing the ~50-line block per task during `spec-to-backlog` authoring burns LLM tokens and risks per-task wording drift when sub-agents paraphrase rather than copy.

The helper templates the block once with three placeholders:

- `<WORKSPACE_CLAUDE_MD>` -- resolved to the workspace's `CLAUDE.md` path so the rendered block points at the local engineering standards file.
- `<TASK_SPECIFIC_ERROR_PATHS>` -- resolved to a Markdown bullet list of operator-supplied error scenarios unique to the task.
- `<REPO_CARVE_OUTS>` -- resolved to a Markdown list of repo-specific carve-outs (vendored directories that mypy / bandit / coverage skip), or `(none)`.

## Workspace override

Workspaces that want a different canonical body place a file named `code-standards-canonical.md` at the workspace root (alongside `BACKLOG.md`). The helper reads that file first and falls back to the devbench-shipped body when absent. The override uses the same three placeholder tokens.

## Canonical body (default)

The default body, used when no workspace override exists, is the content below. It aligns with the engineering standards at `/workspaces/rpm-migration/CLAUDE.md` (the kanon-deps-work operator's source-of-truth). Other workspaces can fork this content.

```markdown
### Code Standards

All code in this work unit MUST comply with the following rules. These are checked by the LLM review judges (`code_review`, `test_review`, `doc_review`, `changes_manifest`, `security_review`) and trigger `REVIEW_FAIL` when violated. The work unit cannot reach `done` until every judge logs `REVIEW_PASS`. See `<WORKSPACE_CLAUDE_MD>` for the full engineering standards.

#### Critical Rules (Violation = Automatic Rejection)

1. **NO FALLBACK LOGIC** -- If an operation can fail, it MUST fail loudly. Never catch an exception and silently continue. Never provide a default value when the real value is missing.
2. **NO SILENT FAILURES** -- Every error must produce a clear, actionable error message sent to stderr. Every error must result in a non-zero exit code. Never swallow exceptions. Never log-and-continue when the operation was required to succeed.
3. **FAIL FAST** -- Detect errors at the earliest possible point. Validate inputs before processing. Check prerequisites before starting work. Exit immediately on the first error with a message that tells the user exactly what went wrong and what to do about it.
4. **NO HARD-CODED VALUES** -- No URLs, paths, timeouts, retry counts, port numbers, hostnames, credentials, feature flags, or environment-specific values in source code. All constants live in the appropriate constants module; all configuration comes from environment variables, configuration files, or function parameters.
5. **NO TEMPORAL LOGIC** -- Never use `time.sleep()`, `asyncio.sleep()`, or any time-based delay as a synchronization mechanism. Use readiness detection, event-driven callbacks, or polling with configurable timeouts.
6. **ALL CODE MUST BE DYNAMIC AND INPUT-DRIVEN** -- No static data, no hard-coded test fixtures embedded in source, no magic numbers. All thresholds, limits, paths, and identifiers must be parameterised.
7. **NO BYPASS ANNOTATIONS** -- Never add `# noqa`, `# nosec`, `# type: ignore`, `# pragma: no cover`, or any annotation that suppresses a linter, type checker, or security scanner finding. Fix the finding instead.
8. **NO EM-DASHES** -- Do not use the em-dash character (Unicode U+2014) in any source file, test file, or work-unit `.md` file. Use `--` (double hyphen).

#### Architecture Principles

- **SOLID** -- Single Responsibility, Open/Closed, Liskov Substitution, Interface Segregation, Dependency Inversion.
- **DRY** -- Extract shared logic into reusable helpers. No duplicate code.
- **12-Factor App** -- Config from environment or YAML. Explicit dependencies declared. Logs to stdout/stderr.

#### Testing Rules

- **TDD MANDATORY** -- Write failing tests BEFORE implementation when source code is added.
- **NO STUB TESTS** -- Every test must have assertions that can actually fail. No `assert True`, no TODO-marked test bodies.
- **TEST ERROR PATHS** -- Every error condition must have a test.
- **PARAMETRIZE** -- Use `@pytest.mark.parametrize` for multiple scenarios.
- **REAL TESTS ONLY** -- Integration tests run against real fixtures.
- **NO SKIPS / XFAILS / XPASSED** -- Skipped tests hide regressions.

#### Git Rules

- **STAGE ONLY** -- Use `git add` for explicit relevant files (only those in this work unit's `## Changes Manifest`). Never `git add -A` or `git add .`.
- **NO --no-verify** -- Never bypass git hooks.
- **SELECTIVE STAGING** -- Only stage files in the Changes Manifest.

Repo-specific carve-outs:

<REPO_CARVE_OUTS>

#### Security Rules

- **NO SECRETS** -- No API keys, tokens, passwords, GitHub PATs, or SSH keys in source code, tests, or fixtures.
- **NO eval() / exec()** -- Never execute dynamic code from external input.
- **NO HOOK BYPASS** -- Never bypass guard hooks installed by `devbench-orchestrate` or the workspace.

#### Error Handling Contract

Raise specific exceptions (not generic `Exception`). Include context in error messages: file paths, variable names, expected vs actual values, the operator's likely next step. Never catch and discard exceptions. Never call `sys.exit()` from library code; only from CLI command handlers. Error messages follow the standard shape: `ERROR: <one-line summary>` then optional context lines (wrapped at 80 cols), then a remediation line when applicable.

Task-specific error paths for this work unit:

<TASK_SPECIFIC_ERROR_PATHS>
```

## Drift detection

The `verify_code_standards_canonical` post-processor pass (see `docs/skills/backlog-post-processor.md`) walks every leaf-task file in the backlog and reports the count of tasks whose Code Standards block has drifted from the canonical body (excluding the `#### Error Handling Contract` subsection, which is intentionally task-specific). The pass is CHECK-ONLY -- it does NOT mutate task files; the operator decides whether to fix manually or via a future regenerate pass.
