# devbench.yaml Reference

This document is the canonical reference for every field in `backlog/config/devbench.yaml`.

The YAML file is loaded from `<DEVBENCH_WORKSPACE_ROOT>/backlog/config/devbench.yaml` by default.
Override the lookup path with `--config <path>` (CLI flag) or the `DEVBENCH_CONFIG_PATH` environment
variable.

**Source of truth:** `src/devbench/config_loader.py` (module docstring + dataclass docstrings) and
`sample-config.yaml` (annotated with defaults). The JSON schema at `src/devbench/config-schema.json`
enforces unknown-key rejection at load time.

---

## Value resolution precedence

For every configurable parameter:

1. Environment variable override (applied by `src/devbench/config.py`, not by this module).
2. Value in `devbench.yaml` (applied by `config_loader.py`).
3. Code default in the relevant dataclass field.

---

## Top-level fields

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `merge_strategy` | `squash` \| `merge` \| `rebase` | `squash` | Default merge strategy for every repo unless overridden per-repo under `repos:`. Effective precedence at merge time: `DEVBENCH_MERGE_STRATEGY` env > per-repo > this top-level > `squash`. |
| `max_executor_retries` | integer | `10` | Shared retry budget across review-judge retries, CI-failure retries, and PR-bot-feedback retries. |
| `use_bedrock` | boolean | `false` | Route LLM calls via AWS Bedrock instead of the Anthropic API. |
| `bedrock_region` | string | `us-east-1` | AWS region for Bedrock when `use_bedrock: true`. |
| `allowed_orgs` | list of strings | `[]` | Hard allowlist of GitHub orgs devbench may operate against. Empty means every org listed under `repos:` is permitted. |
| `display_timezone` | IANA zone string | OS local | Timezone applied to every timestamp-rendering command (`report`, `hook-tail`, `watch`) and to work-unit audit comments. Comments default to UTC rather than OS local when the key is unset, because a work-unit file is committed and read on other machines. |
| `log_file` | string (relative path) | `logs/orchestrator.log` | Shared aggregate orchestrator log. Named sessions additionally write a per-session log at `.devbench/sessions/<name>/orchestrator.log` (read via `report --session <name>`). An explicit value (or `DEVBENCH_LOG_FILE`) overrides; relative values are workspace-relative. |

---

## `repos:` (required)

At least one entry is required. Each key must be in `org/repo` format.

```yaml
repos:
  caylent-solutions/devbench:
    default_branch: main          # optional -- omit to fall back to origin/HEAD
    checkout_directory: devbench  # optional -- relative to DEVBENCH_WORKSPACE_ROOT
    merge_strategy: squash        # optional -- overrides top-level merge_strategy
    branch_prefix: wg_004         # optional -- overrides top-level git_ops.branch_prefix
```

Per-repo shared/high-fan-in file registration moved under `gates.repos.<org/repo>
.shared_file_impact.patterns` -- see the `gates:` section below.

---

## `gates:` -- integration-reality gates (spec 4.1)

Unified opt-in configuration for the eight integration-reality gates (caylent-solutions/devbench-internal-backlog#10..#17):
`reachability`, `ancestry`, `shared_file_impact`, `fixture_consistency`, `write_path_audit`,
`newly_reachable_paths`, `composition_root`, and `layout_geometry`. Every gate is disabled by
default at the built-in level (D-17). `additionalProperties: false` applies at every level
(`gates:`, each per-gate block, and `gates.repos.*`), so an unrecognised key -- including a typo,
or a key from either retired pre-release surface below -- fails config-load with a `ValueError`
naming the offending key rather than being silently ignored (D-2).

```yaml
gates:
  reachability:
    enabled: false
    entry_points: []          # repo-relative paths; empty/absent uses the built-in stem-based default
  ancestry:
    enabled: false
  shared_file_impact:
    enabled: false
    auto_derive_registry: false
    fan_in_threshold: 3
  fixture_consistency:
    enabled: false
    canonical_sources: []
    scan: []
    extract_source_literals: false
  write_path_audit:
    enabled: false
  newly_reachable_paths:
    enabled: false
  composition_root:
    enabled: false
  layout_geometry:
    enabled: false
  repos:
    caylent-solutions/devbench:
      shared_file_impact:
        enabled: true
        patterns:
          - "src/app/Shell.tsx"
          - "src/hooks/useAuth.*"
```

### Value-resolution precedence (D-15)

Every gate field resolves through four layers, in this order (lowest to highest precedence):

1. **Built-in default** -- `constants.py`; every gate disabled, every boolean tunable at
   its documented default. Structural (non-boolean) tunables source their built-in
   default elsewhere: `gates.reachability.entry_points`, for example, defaults to
   `config_loader._REACHABILITY_ENTRY_POINTS_BUILTIN_DEFAULT`, itself derived from
   `source_classification.ENTRY_POINT_STEMS` rather than `constants.GATE_FIELD_DEFAULTS`.
2. **Project level** -- `gates.<gate>.*` in the workspace `devbench.yaml`.
3. **Per-repo override** -- `gates.repos.<org/repo>.<gate>.*`, field-wise merged OVER the
   project level, so a repo can flip `enabled` while inheriting every other tunable.
4. **Environment** -- `DEVBENCH_GATE_<NAME>_ENABLED`, workspace-wide, highest precedence.

**Status: resolver landed (E2-F1-S1-T2).** `config_loader.resolve_gate_config(gate, repo,
runtime_config, env_enabled_override=None) -> ResolvedGateConfig` is now the single read path for
this four-layer precedence chain: it merges built-in defaults, the project layer, the per-repo
override layer, and the caller-supplied env layer field-wise, and records per-field provenance
(`builtin` / `project` / `repo` / `env`) so a repo that flips `enabled` inherits every other
project-level tunable instead of resetting it. `config.resolve_gate_env_override(gate)` resolves
the `DEVBENCH_GATE_<NAME>_ENABLED` env layer through the existing `_resolve_bool` chain and is the
value callers thread into `resolve_gate_config`'s `env_enabled_override` parameter --
`config_loader.py` remains parse/validate-only and never reads environment variables itself. No
consumer other than `resolve_gate_config` may read a gate's resolver-managed fields (`enabled`,
`auto_derive_registry`, `fan_in_threshold`, `extract_source_literals`, `entry_points`) directly off
`RuntimeConfig.gates` (AC-27). `devbench gates` (E2-F1-S2-T1) is the first consumer: it renders one
row per declared gate with the resolved `enabled` status and per-field provenance for every row,
calling `resolve_gate_config` once per gate rather than reading `RuntimeConfig.gates` directly. Each
per-gate check command adopts the resolver as its own hardening epic lands: `check-reachability`
already reads both `enabled` and `entry_points` exclusively through `resolve_gate_config` (spec 4.4,
E3-F1-S1-T2); `check-shared-file-impact` reads `enabled`, `auto_derive_registry` and
`fan_in_threshold` the same way (spec 4.6, E5-F2-S1-T2); `check-fixture-consistency` and the ones
later gate epics add follow in their own tasks.

### Per-gate tunables

| Gate | Tunables (beyond `enabled`) | Per-repo override tunables |
|------|------------------------------|------------------------------|
| `reachability` | `entry_points` (issue #10 AC2) | `enabled` |
| `ancestry` | none | `enabled` |
| `shared_file_impact` | `auto_derive_registry`, `fan_in_threshold` | `enabled`, `patterns` |
| `fixture_consistency` | `canonical_sources`, `scan`, `extract_source_literals` (reserved, unused v1) | `enabled` |
| `write_path_audit` | none | `enabled` |
| `newly_reachable_paths` | none | `enabled` |
| `composition_root` | none | `enabled` |
| `layout_geometry` | none | `enabled` |

### Migration: retired pre-release keys (spec 4.1, Section 6)

Two keys that arrived on the branch ahead of any release are REMOVED by this same change, with
zero remaining references anywhere in the loader, schema, sample config, or reference docs (spec
Section 6: no released version ever carried these keys, so no migration path is owed):

| Retired pre-release surface | New location |
|-------------|---------------|
| PR #318's per-repo glob key (nested under `repos.<repo>`) | `gates.repos.<org/repo>.shared_file_impact.patterns` |
| PR #322's bare top-level opt-in block | `gates.fixture_consistency:` |

A config that still sets either retired key fails `load_runtime_config` with a schema-validation
`ValueError` (`additionalProperties: false` at the top level and on `repos.<repo>.*`). An absent
`gates:` block loads into the all-disabled built-in tree with no error and no warning, so a
0.4.0-era config with neither key keeps working unmodified (AC-4, Section 6).

### `gates.reachability.entry_points` -- transitive reachability walk (caylent-solutions/devbench-internal-backlog#10 AC2)

A list of repo-relative paths seeding the transitive reachability walk that `devbench
check-reachability` runs once the gate finds a candidate artifact's word-boundary referrer(s). A
referrer clears the candidate (`[OK]`) only when the referrer is itself reachable from this
entry-point set, walked with a cycle-safe visited set; when every referrer is itself unreachable,
the candidate is reported `[POTENTIALLY UNREACHABLE via orphan-chain]` instead of `[OK]` -- distinct
from the no-referrer-at-all `[POTENTIALLY UNREACHABLE]` shape, though both count toward the spec 5.2
status line's `findings` total.

Absent or an explicit empty list both mean "not overridden at the project level": the resolved
value falls back to the built-in default derived from `devbench.source_classification`'s
entry-point-stem convention (`main`, `app`, `index`, `__init__`, `setup`, `conftest`, `wsgi`,
`asgi`), matched case-insensitively against each candidate importer's own basename stem -- so
`src/App.tsx` or `cmd/main.go` are recognised as composition roots without any configuration at
all. An explicit `entry_points` list instead names literal repo-relative paths matched exactly;
each configured path must exist in the repo checkout, or `check-reachability` fails loudly with
`ERROR: gates.reachability.entry_points names a path that is not present in the repo: <path>`
before examining any candidate, rather than silently walking a graph with zero real roots. Every
element must actually BE repo-relative: an absolute path or a path containing a `..`
parent-traversal segment fails config load fast (naming `gates.reachability.entry_points` and the
offending value), enforced at two independent layers -- the JSON schema's `entry_points` item
`pattern`, and `_parse_reachability_entry_points`'s own validation -- because `repo_path /
entry_point` would otherwise silently discard `repo_path` for an absolute `entry_point` and let a
file outside the checkout satisfy the existence check above.

`entry_points` is read exclusively through `resolve_gate_config("reachability", repo)` (AC-27) --
no module reads `gates.reachability.entry_points` off `RuntimeConfig.gates` directly. There is no
per-repo override layer for this field today (this campaign configures a single target repo, spec
Section 9); the `enabled` field's four-layer precedence is unaffected.

### `gates.fixture_consistency` -- fixture-catalog cross-reference (caylent-solutions/devbench-internal-backlog#17)

Opt-in and project-specific: `devbench check-fixture-consistency` is a no-op unless
`gates.fixture_consistency.canonical_sources` is configured, since devbench cannot infer a
target repo's fixture/mock-file layout on its own. When configured, the test-reviewer agent runs
the check as review evidence and fails the review if a scanned fixture references an identifier
absent from its canonical source, or a canonical source's coverage falls short of an
`expected_count`. See `docs/backlog-contract.md`.

**The in-fixture `allow_missing` waiver marker (spec `integration-reality-gates-hardening.md`
4.7 bullet 5; PM-5's in-diff exception; E6-F1-S1-T2).** A scan target that intentionally models a
not-found/empty-state edge case is scoped out of `missing_key` findings by attaching a
structured marker directly to the waived record IN the scanned fixture file itself, not by a
workspace-config key:

```json
{"sku": "SKU-DOES-NOT-EXIST", "allow_missing": {"reason": "models an empty lookup response"}}
```

`reason` must be a non-empty string; a marker of any other shape (missing `reason`, an
empty-string `reason`, or a value that is not a `{"reason": "..."}` mapping) fails the check with
a `ValueError` naming the fixture path and the offending record's identifier value, rather than
silently suppressing anything. The marker is validated unconditionally wherever it appears, not
only on a dict that also resolves the configured `identifier_field`: a marker attached to a
record whose identifier field has no value in that same record (a typo'd or absent field name, or
a marker placed at the fixture's envelope level rather than on an individual record) fails the
same way, naming the fixture path and the record's own keys in place of an identifier value it
does not have -- a waiver that can never be matched to a record is dead configuration, not a
silent no-op. Every applied waiver is itself surfaced as a `waiver_applied`
finding in the check's own report, so the suppression is visible there too, not only in the
fixture's diff -- printed on both the `pass` and `fail` output, since a validly waived record is
informational, not a blocking problem: `check-fixture-consistency`'s `status` is computed from
the BLOCKING finding kinds only (`missing_key`, `coverage_shortfall`, `load_error`), so a run
whose only finding is an applied waiver still reports `status: "pass"` and exits 0. See
`docs/cli-reference.md`'s `check-fixture-consistency` entry for the exact status/exit-code rule.

**`gates.fixture_consistency.scan[].allow_missing` (the pre-E6-F1-S1-T2 workspace-config
allowlist) is a REMOVED key** -- a complete replacement, not an addition. It shipped only in an
unmerged draft PR (#322), so no migration path is owed (spec Section 6). A workspace config that
still sets it fails `load_runtime_config` with a `ValueError` naming the removed key and the
in-fixture marker above that replaced it, checked before JSON Schema validation runs so the
message can name the replacement (the schema's own `additionalProperties: false` rejection
cannot).

### `gates.shared_file_impact.auto_derive_registry` / `fan_in_threshold` -- auto-derived shared-file registry (caylent-solutions/devbench-internal-backlog#13 AC4)

The hand-maintained glob list below (`gates.repos.<org/repo>.shared_file_impact.patterns`) decays:
a module that becomes a composition root after the list was written is never added, so the gate
stops firing exactly where it matters most. `auto_derive_registry: true` (default `false`) closes
that gap by ADDITIONALLY computing a shared-file set from the import graph, unioned with the
hand-authored list below (see "Additive override" below -- this never replaces the hand list, and
the hand list remains the only way to name a shared file the scanner cannot derive, e.g. any file
whose extension is not classified as source):

- `check-shared-file-impact` walks every source file classified by
  `devbench.source_classification.is_source_extension` (pruning vendored/dependency directories
  during the walk, never scanning or voting from them -- the full, CLOSED set is `.git`, `.venv`,
  `venv`, `node_modules`, `__pycache__`, `.tox`, `.nox`, `.mypy_cache`, `.pytest_cache`,
  `.ruff_cache`, `.eggs`, `site-packages`, `dist`, `build`, `htmlcov`, `vendor` and `third_party`;
  because `dist` and `build` are pruned unconditionally, any first-party source that happens to
  live under a directory named `dist/` or `build/` is never scanned and never votes, exactly like
  a vendored file would be -- this is a real limitation, not merely an implication of the pruning
  behaviour above. This is a finite list, not an exhaustive denylist of every vendor/dependency convention, so an
  unlisted SUBDIRECTORY anywhere under the repo -- e.g. `bower_components/`, `.direnv/`,
  `target/` -- still has its own internals scanned and voted on; this applies only to
  subdirectories the walk descends into, not to the repo root itself, which is always scanned
  regardless of what it is named), extracts each file's import/require targets via
  `source_classification.extract_import_targets` (language-appropriate scanning dispatched on the
  extension the file already carries -- Python `import`/`from ... import`, the JS/TS family's
  `import`/`require`/dynamic `import()`/`export ... from`/`export * from`, Go's `import` blocks
  (every grouped block in the file, not only the first), Ruby's `require`/`require_relative`,
  Java/Kotlin's `import`, Swift's `import`, C#'s `using`, and PHP's `require`/`include`/`use`; a
  `.vue` file's imports live inside an embedded `<script>` block this scanner does not parse, so a
  `.vue` file always contributes zero import targets even though it is itself a valid, votable
  candidate), and RESOLVES each target -- never against a bare global basename index, which would
  credit an unrelated same-named file (e.g. a stdlib `import types` crediting an unrelated
  `mylib/types.py`). The resolution splits into three buckets by the target's first character,
  tested AFTER normalising any backslash separator to `/` -- relevant only to PHP's `use`
  namespace form (see bucket (ii) below); every other language's target already uses `/` as
  written. (i) A target starting with `.` resolves against the IMPORTING file's own directory --
  Python's leading-dot level semantics, or a `./`/`../`-style path for the JS/TS family, Ruby, and
  a relative PHP `require`/`include`. This bucket applies ONLY to Python, the JS/TS family, Ruby
  and PHP: Go, Java/Kotlin, C#, and Swift are ALWAYS resolved by bucket (iii) below regardless of
  the target's first character, a leading `.` included -- this is behaviourally inert for the
  JVM (Java/Kotlin)/C#/Swift languages, since no valid import in those languages can begin with a
  dot, but it means the partition below is by LANGUAGE FAMILY first and by leading character only
  within the families bucket (i) actually applies to. (ii) A target starting with `/` resolves
  against the repo ROOT ONLY, never a `src/` fallback -- the JS/TS family, Ruby, and PHP (Python's
  own extractor never emits a `/`-prefixed target, so this bucket does not arise for Python in
  practice). For PHP specifically, a `use` namespace written with a leading backslash (e.g.
  `use \Lib\Shared;`) normalises that backslash to `/` before the first-character test and so
  lands HERE, repo-root-only -- NOT in bucket (iii) below. (iii) Everything else -- a target with
  neither a leading `.` nor a leading `/` after normalisation -- resolves against the repo root
  and, when present, a top-level `src/` directory for Go's always-absolute import paths (always
  this bucket regardless of the target's own leading character, per bucket (i)'s note above),
  Python's absolute dotted targets, PHP's bare `require`/`include` targets and a `use` namespace
  with NO leading backslash (e.g. `use Lib\Shared;`), and the JVM (Java/Kotlin)/C#/Swift dotted
  forms (also always this bucket regardless of the target's own leading character); for the JS/TS
  family and Ruby, that same neither-`.`-nor-`/` shape (a bare or aliased specifier, e.g. a bare
  `import 'shared'` or `require 'shared'`) is deliberately never resolved and casts no fan-in
  vote, even when a same-named file exists elsewhere in the repo. A
  directory-form import (naming a package/barrel directly,
  e.g. `from mypkg import X` or `import {A} from './lib'`) resolves to that directory's entry file
  (`__init__.py` for Python, `index.<ext>` for the JS/TS family). A target resolving to MORE than
  one on-disk file is credited to NEITHER candidate (a `WARNING:` naming the ambiguity is printed
  to stderr rather than guessing); a target resolving to nothing in the repo (e.g. a stdlib or
  third-party import, or one of the JS/TS/Ruby bare targets described above) casts no vote at all.
  A directory-form target that normalises to the REPO ROOT ITSELF (e.g. `app/importer.js`
  importing `'..'`) never resolves to the root's own entry file even when one exists: the
  underlying matcher treats a prefix that normalises to the empty string or `.` (the repo root
  itself) as always yielding no match, so a directory-form target one level too shallow to name
  any real subdirectory is refused rather than credited to the root's entry file; this
  under-credits (never falsely credits) and only arises at that single degenerate depth -- the
  same import written one directory deeper resolves normally.
- A file whose resolved DISTINCT importer count is strictly greater than `fan_in_threshold`
  (default `3`, must be an integer `>= 1`) is included in the derived shared-file set.
- The derived set is printed in this command's JSON payload (`"derived_registry"`) on every
  invocation of an ENABLED gate that reaches a verdict (pass or block) with `auto_derive_registry`
  enabled, matched or not -- an invocation that raises before reaching a verdict (e.g. an
  unrecognised full-suite runner), and an invocation where `gates.shared_file_impact.enabled` is
  `false` (which writes its own PASS verdict record and returns before any payload is built,
  regardless of `auto_derive_registry`), both print no payload at all. It is ADDITIONALLY cached
  alongside the shared-file baseline
  record, as
  `<workspace>/.devbench/test-baselines/<repo>/<branch-point-sha>.derived-registry.json` (a
  sibling of the baseline record's own `<branch-point-sha>.json`), ONLY on a matched invocation
  (once a branch point/baseline is resolved) -- and as soon as that baseline is loaded, BEFORE the
  full-suite command is even resolved, so a matched invocation that later raises (an unrecognised
  runner, a baseline/runner mismatch) can still leave this cache written even though it aborts with
  no comparison and no printed payload. A no-match run resolves no branch point/baseline at all, so
  there is nothing for this cache to sit "alongside" on that path; the printed payload already
  covers what registry was in effect for a no-match run. This cache is write-only: no devbench
  command reads it back. It exists so an operator can inspect the file directly on disk during an
  investigation ("what did the derived set look like when this verdict was reached") -- not as a
  runtime dependency any command re-reads.

**Additive override, never a replacement (spec 4.6):** the hand-maintained `patterns` list below
is unioned with the auto-derived set, not superseded by it. A hand-listed file the scanner did not
derive still matches; a derived file matches even when the hand list is empty -- an operator can
always force a file into the shared-file set by hand-listing it, regardless of `auto_derive_registry`.
Because derivation only ever considers files `is_source_extension` classifies as source, a shared
NON-source file (e.g. a shared YAML/JSON config, a shell script outside that extension set) can
never be derived and must stay hand-listed regardless of `auto_derive_registry`.

`enabled`, `auto_derive_registry` and `fan_in_threshold` are all read exclusively through
`resolve_gate_config("shared_file_impact", repo)` (AC-27); a non-integer or `< 1` threshold, or an
unrecognised key inside the `gates.shared_file_impact` block, fails config load naming the
offending key (spec 4.1 AC-5).

### `gates.repos.<org/repo>.shared_file_impact.patterns` -- shared-file full-suite regression gate (caylent-solutions/devbench-internal-backlog#13)

Optional list of `fnmatch`-style glob patterns, matched against POSIX paths relative to the
repo root. Identifies "shared/high-fan-in" files for this repo -- app-level composition roots,
shared shell/container components, widely-consumed hooks -- where a change can silently break
already-passing code in unrelated features.

When a work unit's diff (resolved through the same ADR-12 mode-aware
`work_unit_scope.resolve_changed_files` helper `get-diff` and `check-manifest-scope` use, never
a raw working-tree scan) touches a path matching one of these patterns,
`devbench check-shared-file-impact <unit-id>` (invoked by the executor, enforced by the
`assert-shared-file-impact.sh` guard hook) runs the FULL test suite -- not the task's scoped
subset -- and diffs the resulting failing-test set against a pre-change baseline stored at
`<workspace>/.devbench/test-baselines/<repo>/<branch-point-sha>.json`, one file per merge-base
branch point the unit diverged from. The full-suite RESULT reported is always repo-wide, but
which of that run's newly-introduced failures actually **block** task completion is narrower:
only a failure whose failing node id is attributable to the unit's own Changes Manifest scope
(the `pytest` parser's `"<file>::<test>"` node ids are checked against that scope; `go test` and
jest node ids ordinarily carry no `::` at all and are attributable unconditionally -- but this is
not an absolute guarantee: the jest parser captures the raw description text after the failure
marker and the `go test` parser captures a bare non-whitespace token, so a test name or
description that happens to contain a literal `::` is still split on it by the same file-segment
check the `pytest` case uses, and the leading fragment -- not a real file path -- is then checked
against scope and can be silently excluded from `new_failures` into `unattributed_new_failures`)
blocks -- so a regression this task caused in a file outside its own Changes Manifest is reported in the JSON payload's
`unattributed_new_failures` list but does not block this task, and pre-existing/flaky failures
never stall an unrelated task either way. See `devbench check-shared-file-impact --help` and
`src/devbench/cli.py::cmd_check_shared_file_impact` for the full algorithm: the baseline is
captured once per branch point by running the full suite in an isolated `git worktree` checked
out AT that branch point (never from the current, already-changed tree), and a baseline that
exists but fails to parse, or whose stored `branch_point` disagrees with the resolved
merge-base, is a loud `ERROR` on stderr (exit 1) that leaves the file untouched -- there is no
silent re-bootstrap path.

The `assert-shared-file-impact.sh` guard hook does not parse the invoking Bash tool call's
command text or `tool_response.stdout` at all: `cmd_check_shared_file_impact` persists its own
verdict to a small record file (`<workspace>/.devbench/shared-file-impact-verdict`, or a
`<workspace>/.devbench/sessions/<DEVBENCH_SESSION_NAME>/` subdirectory when a named session is
active) as the very first thing it does -- `"pending"`, overwritten with `"pass"` or `"block"`
only on a clean exit -- and the hook's entire job is reading that ONE record back on the next
Bash call it receives, then consuming (deleting) it. An unconsumed `"block"` record is never
overwritten by a later, different invocation's own `"pending"`/`"pass"` writes, so it survives
until a Bash PostToolUse event actually consumes it. Almost the identical protection applies to
an unconsumed `"pending"` record: each invocation carries its own identity (a fresh PID+counter
value recorded on the record's 4th line), and a DIFFERENT, later invocation's own `"pending"`/
`"pass"` writes can never silently erase an earlier invocation's still-open `"pending"` -- the
case where that earlier invocation crashed before reaching its own clean verdict -- while that
SAME invocation's own subsequent `"pass"`/`"block"` write still transitions the record normally.
The one write this protection deliberately lets through is a DIFFERENT, later invocation's own
genuine `"block"`: it always escalates over an unconsumed `"pending"` rather than being refused,
since `"block"` is itself the strongest, sticky status this same paragraph's first sentence
protects, so nothing is lost by letting the escalation land -- refusing it would instead silently
discard the escalating invocation's own genuine failing verdict. A `"block"` record blocks (exit 2); a
`"pass"` record allows; a record still reading `"pending"` (every error path
`check-shared-file-impact` can exit through AFTER its initial write without reaching a clean
verdict -- an unrecognised unit id, no local repo path configured, the config file failed to
load, a scope-resolution error, the import-fan-in scan failed, `_evaluate_shared_file_gate`
raising `RuntimeError`/`UnknownTestRunnerError`/`TimeoutError`, the
due `[GATE_PASS shared_file_impact]` record write failing (the work-unit file cannot be
located, or the audit-marker append itself raises an `OSError`), or the process crashing or
being killed mid-run) fails CLOSED (blocks, exit 2). No record at all
allows, and is reached three ways, only two of which are the intended "nothing unresolved" case:
the gate was never invoked in this session; its prior record was already consumed; or (not
closable from inside this hook) `cmd_check_shared_file_impact` never reached its own first line
at all -- an unrecognised CLI subcommand, an import-time configuration failure, argparse
rejecting the invocation, `devbench` not on PATH, or the initial `"pending"` write itself raising
`OSError` -- none of which produce a record for the hook to find. Note also that a Bash tool call
exiting non-zero (exactly what a blocking `check-shared-file-impact` invocation itself does)
emits Claude Code's `PostToolUseFailure` event rather than `PostToolUse`, and this hook is
registered on `PostToolUse` only, so a block is observed on the next Bash call whose
`PostToolUse` event reaches this hook rather than necessarily on the gate's own call.

**Hand-maintained by default, auto-derivable on request (caylent-solutions/devbench-internal-backlog#13
AC4):** this registry is hand-maintained per repo unless `gates.shared_file_impact.auto_derive_registry`
is set to `true`, in which case the shared-file set is ADDITIONALLY computed from the repo's actual
import fan-in and unioned with this hand list, never replacing it (see
`gates.shared_file_impact.auto_derive_registry` / `fan_in_threshold` above). `auto_derive_registry`
defaults to `false`, so a repo that has not opted in still needs this list reviewed/regenerated by
hand as the codebase evolves -- nothing does that automatically for such a repo.

Omitting `patterns` entirely (or leaving it empty) makes `check-shared-file-impact` a permanent
no-op for that repo UNLESS `gates.shared_file_impact.auto_derive_registry` is also `true`, in which
case a derived file can still match and the gate can still block even with no hand-authored
patterns at all. Only when both `patterns` is empty AND `auto_derive_registry` is `false` (or
unset) is the gate a permanent no-op, identical to today's behavior before this feature existed.

---

## `backlog:` -- backlog lifecycle settings (issue #189)

All fields are optional. Omitting the entire `backlog:` section produces identical behaviour to
existing workspaces (backwards compatible -- AC-189-9).

```yaml
backlog:
  default_status_for_new_work_units: in-queue  # 'draft' or 'in-queue'
```

### `backlog.default_status_for_new_work_units`

| Property | Value |
|----------|-------|
| **Type** | string |
| **Accepted values** | `draft`, `in-queue` |
| **Default** | `in-queue` |
| **Invalid value behaviour** | `ValueError` raised at load time with an actionable message naming the accepted values |

**What it controls.** The `## Status:` line written into every newly created work-unit file by
`task_factory.materialise_proposal` and the blocker-resolver promote path. It does not retroactively
change the status of existing work units.

**`in-queue` (default).** New work units are immediately eligible for autonomous claim on the
next orchestrator sweep. This is the legacy behaviour; existing workspaces that do not set this
key see no change (AC-189-9).

**`draft`.** New work units are created in `draft` status -- a pre-`in-queue` gate. The
orchestrator never claims a `draft` task; `get_parallel_candidates` excludes them. An operator
must explicitly promote the task to `in-queue` before autonomous execution can begin. Use
`devbench promote <id>` for individual tasks or `--epic`, `--feature`, `--story`, `--all` for
bulk promotion. Each promoted unit receives a `[PROMOTED] draft -> in-queue` audit comment (AC-189-4).

**Error example.** Setting an invalid value raises a `ValueError` at process start:

```
ValueError: Config file 'backlog/config/devbench.yaml':
  backlog.default_status_for_new_work_units must be one of [draft, in-queue]; got 'staging'.
  Use 'draft' to require explicit promotion before execution,
  or 'in-queue' (the default) for the legacy behaviour.
```

**Example -- require human review of every generated task:**

```yaml
backlog:
  default_status_for_new_work_units: draft
```

After the orchestrator runs `task-factory` and materialises new tasks, the operator reviews
each task file, then runs:

```bash
# Promote a single task
uv run devbench promote E5-F2-S1-T3

# Promote everything under an epic in one transaction
uv run devbench promote --epic E5

# Promote all draft tasks (with confirmation prompt)
uv run devbench promote --all

# Promote all draft tasks without prompting (CI / scripted use)
uv run devbench promote --all --yes
```

---

## `timeouts:` -- all values in seconds

```yaml
timeouts:
  gh_api: 30
  test: 300
  security_fetch: 120
  llm: 300
  command: 120
  orchestrator_poll_interval: 10
  github_check: 600
  orchestrator_inactivity: 1800
```

Environment variable overrides are applied by `config.py` (not this module).

**`orchestrator_inactivity`** (integer, seconds, default `1800`) -- FR-17 (issues
db-262 / db-325). Bounds how long `devbench start`'s `_run` SDK message loop
will await the next message (`agen.__anext__()`) before treating the session
as hung. On expiry the loop raises `_OrchestrateInactivityTimeout`, which
`_drive_orchestrate_with_quota_resume` disposes as a bounded fresh-session
restart (reusing the same cap enforced by `DEVBENCH_MAX_QUOTA_RESUMES`) rather
than idling forever. Overridden at runtime by the
`DEVBENCH_ORCHESTRATOR_INACTIVITY_TIMEOUT` environment variable; the code
default lives in `DEFAULT_ORCHESTRATOR_INACTIVITY_SECONDS`
(`src/devbench/constants.py`).

---

## `limits:` -- threshold values

```yaml
limits:
  alert_summary: 10
  output_truncation: 2000
  llm_evidence_truncation: 15000
  llm_file_context: 5
  llm_file_preview_chars: 3000
  ci_failure_log_bytes: 32768
```

---

## `git_ops:` -- git workflow settings

```yaml
git_ops:
  update_submodule: false       # set true only for git-submodule repos
  # single_branch: feat/batch  # one branch for all WUs (single-PR mode)
  # branch_prefix: wg_004      # namespaces per-unit branches: backlog/<prefix>/<id-lower>
  defer_pr: false               # requires single_branch; commits stay local until git-ops-finalize
  pause_before_merge: false     # push + wait for CI, then transition to in-review
  inline_orphan_cleanup: true   # chore commit before task commit when orphans detected
  ci_failure_retry: true        # rc=2 on CI failure triggers executor retry with log feedback
  auto_finalize: false          # auto-run git-ops-finalize when all WUs terminal
  auto_merge: false             # auto-merge after CI green (requires auto_finalize + defer_pr)
  orphan_patterns: []           # replaces built-in orphan fnmatch list when non-empty
  # provenance_path: docs/release-notes/provenance-map.json  # PR-body provenance map (below)
                                # built-in list covers terraform state/plan, terragrunt
                                # cache, python caches/venv/egg-info, ansible *.retry, helm
                                # charts/*.tgz, node_modules, .DS_Store. LOCK FILES ARE EXCLUDED.
  pr_review_resolution:
    enabled: false
    agents: []
    decision_blocks: true
    settle_seconds: 60
    poll_interval: 5
```

### `git_ops.provenance_path` -- PR-body provenance map (spec 4.13; D-17)

Path to a JSON provenance map that `git-ops-finalize` reads to compose the batch PR body: the PR
title, one `###`-headed per-epic summary section, then a closing-keyword block with one
`Fixes ...` line per mapped issue (`Fixes <org>/<repo>#<n>` cross-repo, `Fixes #<n>` same-repo,
both rendered by the same code path). Without a resolved map, `git-ops-finalize` composes a plain
body that carries no closing-keyword block, so issues in the batch never auto-close on merge
(the gap observed on PR #334).

- **Default:** unset. With no `provenance_path` and no `--provenance` flag, the composed body is
  byte-identical to the plain body `git-ops-finalize` has always produced -- this key is a pure
  additive opt-in (spec Section 6).
- **Override:** `git-ops-finalize --provenance <path>` beats this config key for a single
  invocation; the config key alone is what lets an unattended `auto_finalize` run pick up the
  feature with no operator step. There is no `DEVBENCH_*` environment override for this key
  (YAML-only, the same as its sibling path/name settings `single_branch` and `branch_prefix`
  above).
- **Path resolution:** a relative value (from either the config key or the `--provenance` flag)
  resolves against the TARGET REPO working tree -- the local filesystem path that
  `repos.<org/repo>.checkout_directory` resolves to for the `<repo>` positional `git-ops-finalize`
  runs against -- never `DEVBENCH_WORKSPACE_ROOT` and never the devbench process's current working
  directory. An absolute path is used as-is. `git_ops` (including `provenance_path`) is a single
  GLOBAL config block, while `git-ops-finalize <repo>` runs per repo, so in a multi-repo workspace
  one relative `provenance_path` value resolves to a DIFFERENT file inside each repo's checkout.
  Either point the relative value at a path that exists identically in every target repo, use an
  absolute path, or override the value per repo with `--provenance` at invocation time.
- **Failure mode:** a configured or passed path that is missing, unreadable, not valid JSON, or
  parses to zero mapped issues fails the command loudly (exit 1, naming the path) before any push
  happens -- it never silently falls back to the plain body. This list is not exhaustive: any
  structurally malformed map fails the same way (loudly, naming the path, before any push) --
  including a payload that does not decode to a JSON object, a missing or non-list top-level
  `epics`, a non-object epic entry, an epic missing a non-empty `name` or `summary`, an epic whose
  `issues` is present but not a list, a non-object issue entry, an issue missing an integer
  `number`, and an issue `repo` that is not an `owner/name` string.

Provenance map shape (required fields marked):

```json
{
  "epics": [
    {
      "name": "E1: Cherry-pick integration",
      "summary": "One-line summary of what this epic delivered.",
      "issues": [
        {"repo": "org/other-repo", "number": 10},
        {"number": 335}
      ]
    }
  ]
}
```

Top-level `epics` is required and must be a list. Each epic requires a non-empty string `name`
and a non-empty string `summary`; an epic's `issues` is optional but, when present, must be a
list. Each `issues` entry needs an integer `number`; an omitted `repo` (or a `repo` equal to the
target repo) renders the same-repo `Fixes #<n>` form, any other `repo` (a string matching
`owner/name`) renders the cross-repo `Fixes <repo>#<n>` form.

---

## `stop_hook:` -- circuit breaker tuning

```yaml
stop_hook:
  max_blocks: 5
  window_seconds: 180
  stale_task_minutes: 120
```

**`DEVBENCH_STOP_HOOK_STATE_DIR`** (env var only, default `/tmp`) -- the
directory `continue-orchestration.sh` uses for its Stop-hook state file.
`/tmp` is shared machine-wide, so a test suite running alongside a live
orchestrator on the same host can collide on that file; set this env var to a
private directory in the test environment to isolate the two. Leaving it
unset preserves the previous `/tmp` behaviour exactly -- this is an optional
knob, not a required migration step.

---

## `hook_tail:` -- column caps for `devbench hook-tail`

```yaml
hook_tail:
  agent_width: 12
  tool_width: 8
  description_max: 120
  stdout_preview_max: 80
```

---

## `orchestrate:` -- orchestrator runtime tuning

```yaml
orchestrate:
  max_cascade_depth: 2                        # recovery-of-recovery cascade depth cap
  max_transport_restarts: 14                  # bounded restarts after an SDK transport error (~1h budget)
  transport_restart_backoff_base_seconds: 1.0 # first wait; doubles per restart
  transport_restart_backoff_max_seconds: 60.0 # ceiling on that doubling
  effort: high                                # reasoning effort for the orchestrator session
  max_thinking_tokens: 16000                  # ceiling on one turn's reasoning
```

All six keys are optional; each resolves **env > YAML > built-in default**.

| Key | Env override | Default |
| --- | --- | --- |
| `max_cascade_depth` | `DEVBENCH_ORCHESTRATE_MAX_CASCADE_DEPTH` | `2` |
| `max_transport_restarts` | `DEVBENCH_MAX_TRANSPORT_RESTARTS` | `14` |
| `transport_restart_backoff_base_seconds` | `DEVBENCH_TRANSPORT_RESTART_BACKOFF_BASE_SECONDS` | `1.0` |
| `transport_restart_backoff_max_seconds` | `DEVBENCH_TRANSPORT_RESTART_BACKOFF_MAX_SECONDS` | `60.0` |
| `effort` | `DEVBENCH_ORCHESTRATE_EFFORT` | `high` |
| `max_thinking_tokens` | `DEVBENCH_ORCHESTRATE_MAX_THINKING_TOKENS` | `16000` |

**Effort and the thinking budget.** `effort` accepts `low`, `medium`, `high`,
`xhigh` or `max`, and applies to the orchestrator SDK session and every agent
it spawns. devbench pins it rather than inheriting it: an unset value means the
session adopts whatever effort the ambient Claude Code configuration carries,
so an unattended run's cost profile is decided by the operator's last
interactive session.

`max_thinking_tokens` bounds how much one turn may reason, and the reason it
exists is not only cost. Prompt-cache entries have a limited lifetime. A turn
that reasons for longer than that lifetime returns to a cold cache, so the
whole prompt is re-uploaded and re-cached instead of being read back at cache
rates, and the run reaches its quota limit sooner. Quota exhaustion is what
interrupts units mid-flight, so an unbounded thinking budget is upstream of
the interruption-and-recovery machinery described in
[`git-ops-modes.md`](git-ops-modes.md).

Raise `effort` deliberately, and when you do, check that turns still finish
inside the cache window rather than assuming more reasoning is free.

**Transport restarts.** When the Claude Agent SDK fails at its transport
boundary, the orchestrator opens a fresh SDK session on the remaining backlog
rather than exiting. `max_transport_restarts` bounds how many times in a row it
will do that, and the two backoff keys space the attempts:

```text
delay = transport_restart_backoff_base_seconds * 2 ** restarts_already_done
        clamped to transport_restart_backoff_max_seconds
```

With the defaults the waits run 1s, 2s, 4s, 8s, 16s, 32s, then 60s thereafter.

`max_transport_restarts` is sized as a **time budget**, not an attempt count.
Each restart costs one full SDK session lifetime plus one backoff wait.
Measured against a live Anthropic 529 `overloaded` outage, an SDK session burns
its own internal retries and raises after ~199s, so the default 14 restarts
(15 sessions + ~9 min of backoff) gives **roughly one hour** of riding out a
provider outage before the run halts with
`transport-error-restart-cap-exhausted`.

That ~199s figure is a property of the SDK's retry schedule under one observed
failure mode, not a constant -- a fault that rejects instantly makes each cycle
much shorter and the same cap exhausts far sooner. Decide the wall-clock window
you want and re-derive the count; do not nudge the integer blindly.

This bound is deliberately separate from -- and far below --
`DEVBENCH_MAX_QUOTA_RESUMES` (default 1000), which bounds quota resumes and
inactivity restarts. A quota window must elapse and an inactivity restart costs
a full timeout window, so both self-throttle. A transport fault does not: it
recurs as fast as the SDK can reject a session. Sharing a 1000-restart budget
with no delay meant one persistent fault could spend the entire budget in
minutes and end the run unattended. Exhausting this smaller bound is the
intended signal that the transport is down rather than flapping.

Both backoff values must be greater than zero; the schema rejects zero or
negative values at load time, and the env-var path raises rather than degrading
into a busy retry loop. The ceiling also bounds how long an in-flight wait can
delay a `devbench stop`.

Each restart is recorded in the orchestrator log as
`[ORCHESTRATOR_TRANSPORT_RESTART] attempt=<n> max=<cap> backoff=<n>s` and
surfaced by the `Transport restarts` row in `devbench report`.

---

## `report:` -- cost estimation settings

```yaml
report:
  models:
    claude-opus-5:
      input: 5.0
      output: 25.0
  default_model:
    input: 5.0
    output: 25.0
  cache_read_multiplier: 0.10
  cache_write_5min_multiplier: 1.25
  cache_write_1hr_multiplier: 2.0
  data_residency_multiplier: 1.10
  fast_mode_multiplier: 6.0
  recent_pace_tasks: 10
  # display_timezone: America/New_York
```

The legacy scalar fields `token_cost_per_million_input`, `token_cost_per_million_output`, and
`token_cost_discount` were retired in issue #223; workspaces that still set them fail-fast at
config-load time. See [docs/model-pricing.md](model-pricing.md) for the full per-model pricing
table, the cost formula, and migration guidance.

---

## `manifest_amendment:` -- amendment workflow (on by default)

```yaml
manifest_amendment:
  enabled: true                    # default; set false to opt out
  allowed_reasons:
    - tdd_green_production_fix
    - doc_sync_review_fix
  max_requests_per_execution: 2    # default; one add + one row removal
```

`allowed_reasons` **narrows** the set of reasons devbench implements; it cannot widen it. A reason listed here that devbench does not implement is a config error and stays refused.

The narrowing is enforced at both gates -- when the request is written and again when it is applied. Before this was wired up, `PreFilter` was reachable from no CLI path and the apply path checked a module-level constant instead of the config, so a backlog that narrowed this list had the narrowing silently ignored and every reason was accepted. If your backlog narrows the list, expect requests using an excluded reason to now be refused at request time with a message naming the configured set.

`max_requests_per_execution` defaults to `2` so a unit correcting its Changes Manifest in both directions -- adding a file review demanded and dropping a row that went stale -- can satisfy [`AC-FINAL-015`](acceptance-criteria-canonical.md) within one execution. A limit of `1` made that combination impossible. See [`docs/manifest-amendments.md`](manifest-amendments.md) for the removal workflow and its no-diff precondition.

---

## `task_factory:` -- task-factory loop (on by default, ADR-32)

```yaml
task_factory:
  enabled: true                    # default; set false to opt out; requires manifest_amendment.enabled: true
  auto_accept_proposals: false     # default; governs two auto-promote paths (write-proposal's synchronous materialise+promote cascade, and sweep-proposals' orphan-`proposed`-draft promote); new drafts always use backlog.default_status_for_new_work_units regardless; only applies when enabled: true
```

---

## `validate:` -- validate-backlog rule toggles

```yaml
validate:
  check_orphan_path_tokens: true   # Rule 20; default on, set false to opt out
```

---

## `agents:` -- per-agent model overrides (ADR-25)

```yaml
agents:
  executor: sonnet
  blocker_resolver: opus
  manifest_amender: opus
  security_reviewer: opus
  task_factory: opus
  review_supervisor: sonnet
  review_team:
    code_reviewer: opus
    test_reviewer: opus
    doc_reviewer: opus
    changes_manifest: opus
```

All fields default to `null` (agent's `.md` frontmatter default). See
[docs/adr/25-per-agent-model-overrides.md](adr/25-per-agent-model-overrides.md).

---

## `quota_handling:` -- quota wait-and-resume configuration (issue #236, spec S5.2)

**Status: parsed, validated, and live.** This block is parsed, schema-checked and
range-checked at config-load time, `RuntimeConfig.quota_handling` is populated exactly as
documented below, and `cmd_start` reads `enabled` / `on_exhaustion` / `on_exhaustion_timeout` /
`resume_strategy` / `audit_comment_on_wait` / `audit_comment_on_resume` at runtime via
`_drive_orchestrate_with_quota_resume` -> `_dispatch_quota_detection` -> `_handle_quota_pause`
(`src/devbench/cli.py`; landed by E2-F4-S3-T1) -- see the per-field table below for exactly
when each is read. `log_structured_events` is read too: `_quota_structured_events_enabled()`
gates every one of the seven structured `[QUOTA_*]` markers on it (`src/devbench/cli.py`,
`src/devbench/quota.py`; landed by E9-F1-S2-T1) -- see its table row. `enabled: true`
(the default) makes the orchestrator pause and poll for reset instead of exiting non-zero;
`enabled: false` restores the legacy non-zero exit.

This block governs what the orchestrator does when the Claude CLI reports a quota-exhaustion
signal (HTTP 429 / CLI "You've hit your limit" message). The whole block is optional; omitting
it entirely yields the full default set below -- never a partial or `None` config object.

```yaml
quota_handling:
  enabled: true
  on_exhaustion: wait
  poll_interval_seconds: 60
  max_wait_seconds: 18000
  on_exhaustion_timeout: drain
  resume_strategy: continue_current_wu
  audit_comment_on_wait: true
  audit_comment_on_resume: true
  log_structured_events: true
```

| Field | Type | Accepted values / range | Default | What it controls |
|---|---|---|---|---|
| `enabled` | boolean | `true`, `false` | `true` | Master toggle. `false` restores the legacy non-zero exit on quota exhaustion (`#193` AC-4, spec AC-24) -- the escape hatch for operators who prefer the pre-#236 behaviour. |
| `on_exhaustion` | string (enum) | `wait`, `fail`, `drain` | `wait` | Action taken when a quota signal is detected. `wait` pauses and polls until reset; `fail` re-raises immediately (non-zero exit, same as `enabled: false`); `drain` triggers a graceful drain then exits. |
| `poll_interval_seconds` | integer | `[30, 3600]` | `60` | Cadence in seconds between recovery probes while waiting. |
| `max_wait_seconds` | integer | `>= 1` | `18000` (5 hours) | Cap on total wait time in seconds before `on_exhaustion_timeout` fires. |
| `on_exhaustion_timeout` | string (enum) | `drain`, `fail`, `keep_waiting` | `drain` | Action taken when `max_wait_seconds` elapses without recovery. `drain` triggers a graceful drain; `fail` re-raises the quota error; `keep_waiting` is terminal -- it logs `[QUOTA_TIMEOUT_KEEP_WAITING]` and ends the run (no drain request, no re-raise; see `_dispatch_quota_timeout` in `src/devbench/cli.py`). |
| `resume_strategy` | string (enum) | `continue_current_wu`, `restart_wu`, `drain_and_resume` | `continue_current_wu` | How the orchestrator re-enters the loop after recovery. `continue_current_wu` resumes where it left off; `restart_wu` forces the current work unit back to `in-queue`; `drain_and_resume` removes the quota checkpoint and requests a graceful drain -- the run stops and must be restarted manually, since the Makefile auto-restart loop (`Makefile:117-123`) only fires on exit code 42, which a graceful drain does not produce. |
| `audit_comment_on_wait` | boolean | `true`, `false` | `true` | Append a `[QUOTA_WAITING]` audit comment to the in-progress work unit when pausing. |
| `audit_comment_on_resume` | boolean | `true`, `false` | `true` | Append a `[QUOTA_RESUMED]` audit comment after recovery. |
| `log_structured_events` | boolean | `true`, `false` | `true` | Gates the seven structured `[QUOTA_*]` markers (`[QUOTA_WAITING]`, `[QUOTA_POLLING]`, `[QUOTA_RESUMED]`, `[QUOTA_PROBE_UNAVAILABLE]`, `[QUOTA_FAIL_FAST]`, `[QUOTA_DRAIN_REQUESTED]`, `[QUOTA_TIMEOUT_KEEP_WAITING]`); `false` suppresses all seven. Does not affect Slack notifications, the `audit_comment_on_wait`/`audit_comment_on_resume` comments, the on-disk checkpoint, or the `[ORCHESTRATOR_QUOTA_*]` markers -- see docs/quota-handling.md for the full breakdown. |

**Enum and range enforcement happens at config-load time, never at dispatch time** (spec FR-2.9):
an invalid `on_exhaustion` / `on_exhaustion_timeout` / `resume_strategy` value, or a
`poll_interval_seconds` / `max_wait_seconds` value outside its documented range, raises a
`ValueError` naming the config file path and the offending field before the orchestrator starts.
Unknown keys inside the block are rejected the same way (`additionalProperties: false`).

**What `enabled: false` restores.** The quota core (E2-F1), this config surface, and the
E2-F4 dispatcher that acts on it are all live: `quota_handling.enabled: false` is the
config-level equivalent of the pre-#236 behaviour -- the orchestrator propagates the quota
error and exits non-zero instead of pausing and polling. `enabled: true` (the default) makes
the orchestrator pause and poll for reset instead.

**The `notifications.events` keys `quota_waiting` and `quota_resumed` are live.** They are
declared in the `notifications.events` schema block below (single ownership of
`config-schema.json` avoids two tasks writing the same file) and are read on every
quota-exhaustion pause/recovery by `_handle_quota_pause` (`src/devbench/cli.py`); see
[`docs/slack-notifications.md`](slack-notifications.md) for the payload shape.

---

## `notifications:` -- operator-facing Slack / webhook pings

Per-event toggles for lifecycle notifications. Each toggle defaults to
`false` so the dispatcher is silent until the operator opts in. The
Slack webhook URL + user ID are credentials and should be provided via
the `DEVBENCH_NOTIFICATIONS_SLACK_WEBHOOK_URL` and
`DEVBENCH_NOTIFICATIONS_SLACK_USER_ID` env vars; the yaml fields below
are a fallback for non-secret cases. See
[`docs/slack-notifications.md`](slack-notifications.md) for the full
operator walkthrough.

```yaml
notifications:
  enabled: true                         # master switch; default false
  slack:
    webhook_url: null                   # https://hooks.slack.com/services/T.../B.../...
    user_id: null                       # Slack member id (U... or W...); enables <@mention>
  webhook_url: null                     # optional non-Slack generic webhook (raw JSON POST)
  timeout_seconds: 10                   # per-POST HTTP timeout
  events:
    work_unit_done: false
    work_unit_blocked_operator: false
    work_unit_materialised: false
    work_unit_promoted: false
    pr_opened: false
    pr_merged: false
    ci_failure: false
    ci_pass: false                       # issue #219 / Bundle C; fires on CIResult.GREEN
                                         # in the finalize path so operators under
                                         # auto_merge: false know the PR is ready for
                                         # manual merge.  Default false on upgrade.
    orchestrator_stop: false
    orchestrator_auto_restart: false
    quota_waiting: false                 # orchestrator hit a quota and started waiting; see `quota_handling:` above.
    quota_resumed: false                 # quota recovered and the run resumed; see `quota_handling:` above.
```

---

## `max_executor_retries_per_judge:` -- per-judge retry budget

```yaml
max_executor_retries_per_judge:
  code_review: 10
  test_review: 10
  doc_review: 10
  changes_manifest: 10
  security_review: 10
```

Each entry falls back to `max_executor_retries` when absent.

**Enforced in code (issue #122).** `devbench log-verdict <judge> <id> fail` counts that judge's prior `[REVIEW_FAIL]` rows in the work unit and, once the budget is spent, writes the `[BLOCKED] [RETRY_BUDGET_EXHAUSTED]` audit row, forces the unit to `blocked`, and notifies the operator. Enforcement counts per judge across the whole unit, so ANY single judge exhausting its own budget blocks the unit; only the five canonical reviewers charge a budget.

This bound previously lived only in orchestrate SKILL.md prose, which instructed the orchestrator to read the budget via `devbench config-resolve` -- a verb that did not exist. The budget was therefore unreadable at runtime and never enforced, so reviews could reject the same unit without limit. Inspect the resolved values with:

```
uv run devbench config-resolve max_executor_retries max_executor_retries_per_judge
```

Rounds spent against budget are also surfaced per task in the `devbench report` **Review rejections** row -- see [`docs/cli-reference.md`](cli-reference.md).

---

## `debug:` -- diagnostic-tuning knobs

```yaml
debug:
  check_registration_retries: 12
  check_registration_delay_seconds: 5
  blocked_recovery_window_seconds: 1800
```

Set only when investigating a specific cadence problem; production workspaces leave this section
absent.

---

## See also

- `sample-config.yaml` -- annotated copy of every field at its default value; copy and edit as a
  starting point.
- `src/devbench/config_loader.py` -- docstring contains the full YAML schema and dataclass
  definitions that are the source of truth for every field and its accepted values.
- `src/devbench/config-schema.json` -- JSON Schema that enforces structure at load time; unknown
  keys cause devbench to exit non-zero with an actionable error.
- [docs/architecture.md #8 Configuration model](architecture.md#8-configuration-model) -- covers
  value-resolution precedence and operational context for each section.
- [docs/model-pricing.md](model-pricing.md) -- per-model token pricing blocks for the `report:`
  section.
