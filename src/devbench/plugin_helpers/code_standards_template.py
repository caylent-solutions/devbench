"""Canonical `### Code Standards` block emitter for spec-to-backlog (#230).

The ``spec-to-backlog`` skill authors a `### Code Standards` block in
every leaf-task file. Across a single backlog the block is byte-for-byte
identical modulo the `#### Error Handling Contract` subsection's
task-specific entries. Re-typing the ~50-line block per task burns
authoring token cost and risks per-task wording drift when sub-agents
paraphrase rather than copy verbatim.

This module ships the canonical block as a single source-of-truth
template and exposes ``emit_code_standards_block`` that returns the
rendered block with workspace-specific and task-specific substitutions
applied. The skill (Step 5a) is expected to call this helper instead
of re-typing the boilerplate.

Workspace override
==================

Workspaces that want a customised canonical body can place a file at
``<workspace_root>/code-standards-canonical.md`` -- if present it
overrides the devbench-shipped default. The override file uses the
same placeholder tokens as the shipped default.

Placeholder tokens
==================

The template carries three replacement tokens:

- ``<WORKSPACE_CLAUDE_MD>`` -- resolved to ``<workspace_root>/CLAUDE.md``
  so the rendered block points at the workspace's local engineering
  standards file.
- ``<TASK_SPECIFIC_ERROR_PATHS>`` -- replaced by the operator-supplied
  task-specific error-path bullets (or ``(none)`` when no paths are
  supplied).
- ``<REPO_CARVE_OUTS>`` -- replaced by a rendered list of repo-specific
  carve-outs (e.g., vendored directories that mypy / bandit skip), or
  ``(none)`` when no carve-outs are supplied.

Idempotency
===========

``emit_code_standards_block(...)`` returns the same string for the same
inputs every time. The helper has no filesystem side-effects beyond
reading the optional workspace override.
"""

from __future__ import annotations

from pathlib import Path

# The canonical body. Operators who want to customise per-workspace
# override this by placing a ``code-standards-canonical.md`` file at
# their workspace root. The body uses the same three placeholder tokens
# documented at module-level (``<WORKSPACE_CLAUDE_MD>``,
# ``<TASK_SPECIFIC_ERROR_PATHS>``, ``<REPO_CARVE_OUTS>``).
#
# The body is authored to align with the engineering standards at
# ``/workspaces/rpm-migration/CLAUDE.md`` (the operator's source-of-truth
# for kanon-deps-work). Other workspaces can replace this body wholesale
# by shipping their own ``code-standards-canonical.md`` override.
_CANONICAL_CODE_STANDARDS = (
    "### Code Standards\n"
    "\n"
    "All code in this work unit MUST comply with the following rules.\n"
    "These are checked by the LLM review judges (`code_review`,\n"
    "`test_review`, `doc_review`, `changes_manifest`, `security_review`)\n"
    "and trigger `REVIEW_FAIL` when violated. The work unit cannot reach\n"
    "`done` until every judge logs `REVIEW_PASS`. See\n"
    "`<WORKSPACE_CLAUDE_MD>` for the full engineering standards.\n"
    "\n"
    "#### Critical Rules (Violation = Automatic Rejection)\n"
    "\n"
    "1. **NO FALLBACK LOGIC** -- If an operation can fail, it MUST fail\n"
    "   loudly. Never catch an exception and silently continue. Never\n"
    "   provide a default value when the real value is missing.\n"
    "2. **NO SILENT FAILURES** -- Every error must produce a clear,\n"
    "   actionable error message sent to stderr. Every error must result\n"
    "   in a non-zero exit code. Never swallow exceptions. Never\n"
    "   log-and-continue when the operation was required to succeed.\n"
    "3. **FAIL FAST** -- Detect errors at the earliest possible point.\n"
    "   Validate inputs before processing. Check prerequisites before\n"
    "   starting work. Exit immediately on the first error with a\n"
    "   message that tells the user exactly what went wrong and what to\n"
    "   do about it.\n"
    "4. **NO HARD-CODED VALUES** -- No URLs, paths, timeouts, retry\n"
    "   counts, port numbers, hostnames, credentials, feature flags, or\n"
    "   environment-specific values in source code. All constants live\n"
    "   in the appropriate constants module; all configuration comes\n"
    "   from environment variables, configuration files, or function\n"
    "   parameters.\n"
    "5. **NO TEMPORAL LOGIC** -- Never use `time.sleep()`,\n"
    "   `asyncio.sleep()`, or any time-based delay as a synchronization\n"
    "   mechanism. Use readiness detection, event-driven callbacks, or\n"
    "   polling with configurable timeouts.\n"
    "6. **ALL CODE MUST BE DYNAMIC AND INPUT-DRIVEN** -- No static data,\n"
    "   no hard-coded test fixtures embedded in source, no magic\n"
    "   numbers. All thresholds, limits, paths, and identifiers must be\n"
    "   parameterised.\n"
    "7. **NO BYPASS ANNOTATIONS** -- Never add `# noqa`, `# nosec`,\n"
    "   `# type: ignore`, `# pragma: no cover`, or any annotation that\n"
    "   suppresses a linter, type checker, or security scanner finding.\n"
    "   Fix the finding instead.\n"
    "8. **NO EM-DASHES** -- Do not use the em-dash character (Unicode\n"
    "   U+2014) in any source file, test file, or work-unit `.md` file.\n"
    "   Use `--` (double hyphen).\n"
    "\n"
    "#### Architecture Principles\n"
    "\n"
    "- **SOLID** -- Single Responsibility, Open/Closed, Liskov\n"
    "  Substitution, Interface Segregation, Dependency Inversion.\n"
    "- **DRY** -- Extract shared logic into reusable helpers. No\n"
    "  duplicate code.\n"
    "- **12-Factor App** -- Config from environment or YAML. Explicit\n"
    "  dependencies declared. Logs to stdout/stderr.\n"
    "\n"
    "#### Testing Rules\n"
    "\n"
    "- **TDD MANDATORY** -- Write failing tests BEFORE implementation\n"
    "  when source code is added.\n"
    "- **NO STUB TESTS** -- Every test must have assertions that can\n"
    "  actually fail. No `assert True`, no TODO-marked test bodies.\n"
    "- **TEST ERROR PATHS** -- Every error condition must have a test.\n"
    "- **PARAMETRIZE** -- Use `@pytest.mark.parametrize` for multiple\n"
    "  scenarios.\n"
    "- **REAL TESTS ONLY** -- Integration tests run against real\n"
    "  fixtures.\n"
    "- **NO SKIPS / XFAILS / XPASSED** -- Skipped tests hide regressions.\n"
    "\n"
    "#### Git Rules\n"
    "\n"
    "- **STAGE ONLY** -- Use `git add` for explicit relevant files (only\n"
    "  those in this work unit's `## Changes Manifest`). Never\n"
    "  `git add -A` or `git add .`.\n"
    "- **NO --no-verify** -- Never bypass git hooks.\n"
    "- **SELECTIVE STAGING** -- Only stage files in the Changes Manifest.\n"
    "\n"
    "Repo-specific carve-outs:\n"
    "\n"
    "<REPO_CARVE_OUTS>\n"
    "\n"
    "#### Security Rules\n"
    "\n"
    "- **NO SECRETS** -- No API keys, tokens, passwords, GitHub PATs, or\n"
    "  SSH keys in source code, tests, or fixtures.\n"
    "- **NO eval() / exec()** -- Never execute dynamic code from\n"
    "  external input.\n"
    "- **NO HOOK BYPASS** -- Never bypass guard hooks installed by\n"
    "  `devbench-orchestrate` or the workspace.\n"
    "\n"
    "#### Error Handling Contract\n"
    "\n"
    "Raise specific exceptions (not generic `Exception`). Include\n"
    "context in error messages: file paths, variable names, expected vs\n"
    "actual values, the operator's likely next step. Never catch and\n"
    "discard exceptions. Never call `sys.exit()` from library code; only\n"
    "from CLI command handlers. Error messages follow the standard\n"
    "shape: `ERROR: <one-line summary>` then optional context lines\n"
    "(wrapped at 80 cols), then a remediation line when applicable.\n"
    "\n"
    "Task-specific error paths for this work unit:\n"
    "\n"
    "<TASK_SPECIFIC_ERROR_PATHS>\n"
)


def emit_code_standards_block(
    workspace_root: Path,
    task_specific_error_paths: list[str] | None = None,
    repo_specific_carve_outs: dict[str, str] | None = None,
) -> str:
    """Return the canonical `### Code Standards` block with substitutions applied.

    Args:
        workspace_root: Workspace directory. The helper resolves
            ``workspace_root / "code-standards-canonical.md"`` as an
            optional override; when absent the helper falls back to
            the devbench-shipped canonical body. The
            ``<WORKSPACE_CLAUDE_MD>`` placeholder also resolves
            against this directory.
        task_specific_error_paths: Optional list of bullet strings
            naming this task's unique error scenarios. Each entry
            becomes a Markdown bullet appended after the generic
            Error Handling Contract paragraph. ``None`` or an empty
            list renders ``(none)``.
        repo_specific_carve_outs: Optional ``{path: justification}``
            mapping for vendored directories the workspace has carved
            out of mypy / bandit / coverage. ``None`` or an empty
            dict renders ``(none)``.

    Returns:
        A Markdown string starting with ``### Code Standards`` and
        ending after the Error Handling Contract subsection.

    Raises:
        FileNotFoundError: when ``workspace_root`` does not exist or
            cannot be read. The caller's typo is a defect, not
            something the helper should silently absorb.
    """
    if not workspace_root.exists():
        raise FileNotFoundError(
            f"workspace_root does not exist: {workspace_root}. Pass the directory holding the workspace's BACKLOG.md."
        )

    override = workspace_root / "code-standards-canonical.md"
    template = override.read_text(encoding="utf-8") if override.is_file() else _CANONICAL_CODE_STANDARDS

    return (
        template.replace("<WORKSPACE_CLAUDE_MD>", str(workspace_root / "CLAUDE.md"))
        .replace("<TASK_SPECIFIC_ERROR_PATHS>", _format_error_paths(task_specific_error_paths))
        .replace("<REPO_CARVE_OUTS>", _format_carve_outs(repo_specific_carve_outs))
    )


def _format_error_paths(paths: list[str] | None) -> str:
    """Render task-specific error paths as a Markdown bullet list, or ``(none)``."""
    if not paths:
        return "(none)"
    return "\n".join(f"- {entry}" for entry in paths)


def _format_carve_outs(carve_outs: dict[str, str] | None) -> str:
    """Render repo-specific carve-outs as a Markdown bullet list, or ``(none)``."""
    if not carve_outs:
        return "(none)"
    return "\n".join(f"- `{path}` -- {justification}" for path, justification in carve_outs.items())


def canonical_body_excluding_error_contract() -> str:
    """Return the canonical block trimmed of its Error Handling Contract subsection.

    The Error Handling Contract subsection is intentionally task-specific
    -- every task appends its own error paths. The drift detector in
    ``backlog_post_processor.verify_code_standards_canonical`` compares
    task files' Code Standards blocks against this trimmed canonical
    body so per-task error paths do NOT register as drift.
    """
    body = _CANONICAL_CODE_STANDARDS
    marker = "#### Error Handling Contract"
    idx = body.find(marker)
    if idx < 0:
        return body
    return body[:idx].rstrip() + "\n"
