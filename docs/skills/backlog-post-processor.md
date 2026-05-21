# Backlog post-processor

The `devbench.plugin_helpers.backlog_post_processor` module provides deterministic post-processing passes the `spec-to-backlog` skill runs between Step 5 (task authoring) and Step 5d (`validate-backlog` invocation), so the backlog passes the validator on first try instead of requiring an operator-facing fix loop.

This module is **invoked by the skill, not by the orchestrator**. The orchestrator-facing `validate-backlog --fix` flag covers a different (smaller) set of rules (Rule 10 em-dash + Rule 11 checkout_directory). The post-processor extends the auto-fix surface for issues the LLM commonly produces during initial authoring.

## Why a separate module?

The LLM-driven `spec-to-backlog` skill cannot reliably apply mechanical transforms across N work-unit files (e.g., deduping a Manifest row that appears twice in the same file is trivial in Python but error-prone to do via Edit calls). The post-processor exposes those transforms as pure functions the skill calls via Bash.

## Scope and terminal-status guards (issue #226)

Every pass accepts two optional keyword-only arguments that bound what it touches:

- **`scope_paths`**: an iterable of `Path` objects naming the epic directories the pass may walk. When `None` (the default) the walk covers the full `backlog_dir` tree. When supplied, only files under one of the supplied directories are considered. Pass this whenever a fresh `spec-to-backlog` materialisation is adding new epics on top of an existing populated backlog -- the scope confines the walk to the new epic directories so no pre-existing work-unit file can be touched by accident. A non-existent scope path raises `FileNotFoundError` (fail-fast).
- **`force_terminal`**: when `False` (the default), files whose `## Status:` line is `done` or `declined` are skipped even when otherwise in scope. Set to `True` only for one-time mass-migrations of an old backlog under a new convention.

The two guards combine: a fresh materialisation should pass `scope_paths=[<new-epic-dirs>]`; the default terminal-status skip catches any stray done / declined file the scope happened to include. Without either guard, the legacy single-arg call form (`run_all(backlog_dir)`) still works, but terminal-status files are now skipped by default. This is the issue #226 fix: passes never mutate already-frozen work.

## Available passes

Each pass takes a `backlog_dir: pathlib.Path` (plus the two scope keyword arguments above) and returns an `int` (the count of files modified). All passes are **idempotent**: a second invocation on the same backlog returns 0.

### `normalize_manifest_column_count(backlog_dir, *, scope_paths=None, force_terminal=False)`

Issue #227. Collapses N-column Manifest tables (3+) to the canonical 2-column form (`| File | Change |`) losslessly. When the header row starts with `Repo`, the first two columns merge into the File cell as `repo -- path` and the remaining columns join into Change with ` -- `. Other N-column variants keep column 0 as File and join columns 1..N into Change. Already-canonical 2-column tables are skipped.

Runs first in `run_all` so downstream passes (pipe escape, dedupe, orphan-path suffix) see canonical 2-column shape.

### `sanitize_markdown_pipes_in_manifest(backlog_dir, *, scope_paths=None, force_terminal=False)`

Issue #221 A12. Escapes raw `|` characters that appear inside Changes Manifest annotation cells, which would otherwise cause `ManifestParseError: Manifest row must have exactly 2 columns`. Skills sometimes emit prose like `run cmd | grep -v debug` inside an annotation; this pass rewrites the inner pipe as `\|` so the parser sees a valid 2-column row.

### `dedupe_manifest_rows(backlog_dir, *, scope_paths=None, force_terminal=False)`

Issue #221 A13. Collapses identical Manifest rows down to one entry. The validator's intra-Task Manifest Conflict check fires when the same `(path, annotation)` pair appears twice in one Manifest; this pass removes the duplicate while preserving first-occurrence order.

### `suffix_na_on_non_python_tasks(backlog_dir, *, scope_paths=None, force_terminal=False)`

Issue #228. For Task work units whose Changes Manifest contains zero `.py` paths, appends the canonical suffix `-- N/A for <Tier> Tasks (no Python source authored)` to the Python-tooling AC-FINAL lines (`AC-FINAL-002`, `AC-FINAL-003`, `AC-FINAL-004`, `AC-FINAL-005`, `AC-FINAL-006`, `AC-FINAL-008`, `AC-FINAL-014`). Tier is derived from the task's Manifest paths via the same classifier the validator uses (`BacklogManager._classify_manifest_tier`). Python-tier, Mixed-tier, and empty-Manifest tasks are skipped. AC lines that already carry an `-- N/A` substring are left alone (idempotent). Cross-link: `docs/acceptance-criteria-canonical.md`.

### `suffix_ref_on_orphan_paths(backlog_dir, manifest_paths=None, *, scope_paths=None, force_terminal=False)`

Issue #221 A11. Suffixes `(ref)` after backtick-quoted path tokens in Acceptance Criteria and Definition of Done sections that do not appear in the same Task's Changes Manifest. Validator Rule 20 (orphan path tokens) flags such tokens unless they are declared read-only references via the `(ref)` suffix; this pass adds the missing suffix where the path was clearly intended as a citation.

The pass accepts an optional `manifest_paths={file_path: {path1, path2, ...}}` mapping when the caller has already parsed Manifests in bulk and wants to avoid the per-file re-parse.

## Convenience entry point

```python
from pathlib import Path
from devbench.plugin_helpers import backlog_post_processor as bpp

# Scoped to the freshly-authored epic directories (the recommended form).
result = bpp.run_all(
    Path("backlog"),
    scope_paths=[Path("backlog/E17-compat-ci-cpk"), Path("backlog/E18-compat-ci-marketplace")],
)
# result == {
#     "normalize_manifest_column_count": 0,
#     "sanitize_markdown_pipes_in_manifest": 2,
#     "dedupe_manifest_rows": 1,
#     "suffix_ref_on_orphan_paths": 5,
#     "suffix_na_on_non_python_tasks": 3,
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
