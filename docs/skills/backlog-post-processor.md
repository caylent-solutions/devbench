# Backlog post-processor

The `devbench.plugin_helpers.backlog_post_processor` module provides deterministic post-processing passes the `spec-to-backlog` skill runs between Step 5 (task authoring) and Step 5d (`validate-backlog` invocation), so the backlog passes the validator on first try instead of requiring an operator-facing fix loop.

This module is **invoked by the skill, not by the orchestrator**. The orchestrator-facing `validate-backlog --fix` flag covers a different (smaller) set of rules (Rule 10 em-dash + Rule 11 checkout_directory). The post-processor extends the auto-fix surface for issues the LLM commonly produces during initial authoring.

## Why a separate module?

The LLM-driven `spec-to-backlog` skill cannot reliably apply mechanical transforms across N work-unit files (e.g., deduping a Manifest row that appears twice in the same file is trivial in Python but error-prone to do via Edit calls). The post-processor exposes those transforms as pure functions the skill calls via Bash.

## Available passes

Each pass takes a `backlog_dir: pathlib.Path` and returns an `int` (the count of files modified). All passes are **idempotent**: a second invocation on the same backlog returns 0.

### `sanitize_markdown_pipes_in_manifest(backlog_dir)`

Issue #221 A12. Escapes raw `|` characters that appear inside Changes Manifest annotation cells, which would otherwise cause `ManifestParseError: Manifest row must have exactly 2 columns`. Skills sometimes emit prose like `run cmd | grep -v debug` inside an annotation; this pass rewrites the inner pipe as `\|` so the parser sees a valid 2-column row.

### `dedupe_manifest_rows(backlog_dir)`

Issue #221 A13. Collapses identical Manifest rows down to one entry. The validator's intra-Task Manifest Conflict check fires when the same `(path, annotation)` pair appears twice in one Manifest; this pass removes the duplicate while preserving first-occurrence order.

### `suffix_ref_on_orphan_paths(backlog_dir)`

Issue #221 A11. Suffixes `(ref)` after backtick-quoted path tokens in Acceptance Criteria and Definition of Done sections that do not appear in the same Task's Changes Manifest. Validator Rule 20 (orphan path tokens) flags such tokens unless they are declared read-only references via the `(ref)` suffix; this pass adds the missing suffix where the path was clearly intended as a citation.

The pass accepts an optional `manifest_paths={file_path: {path1, path2, ...}}` mapping when the caller has already parsed Manifests in bulk and wants to avoid the per-file re-parse.

## Convenience entry point

```python
from pathlib import Path
from devbench.plugin_helpers import backlog_post_processor as bpp

result = bpp.run_all(Path("backlog"))
# result == {
#     "sanitize_markdown_pipes_in_manifest": 2,
#     "dedupe_manifest_rows": 1,
#     "suffix_ref_on_orphan_paths": 5,
# }
```

The `spec-to-backlog` skill invokes `run_all` via the Bash tool and emits each non-zero count as a `[POST_PROCESS] <pass_name>: <count> file(s)` audit line.

## Idempotency contract

Every pass MUST satisfy: running it twice on the same backlog produces the same on-disk content as running it once. Tests assert this for every implemented pass. New passes added to this module MUST include an idempotency test.

## Files excluded from the scan

- `BACKLOG.md` (the index file, not a work-unit file).
- Any file whose path contains a `config/` segment (operator config, not work-unit content).

All other `*.md` files under the backlog tree are scanned, including Epic, Feature, and Story files in addition to Task files.

## Future passes

The module scaffold supports additional passes from issue #221 (A8 serial-dep wiring for Manifest conflicts, A9 dep-row title population, A10 bug-discovery clause propagation, A14 BACKLOG.md regeneration, A16 N/A suffix on Markdown-only tasks). New helpers follow the established contract: pure function, `(backlog_dir: Path) -> int` signature, idempotent, with at least one happy-path test + one idempotency test + one no-op-on-clean-input test.
