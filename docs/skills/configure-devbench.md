# configure-devbench skill quickstart

The `configure-devbench` skill interviews you about EVERY setting in
`src/devbench/config-schema.json` -- every existing `RuntimeConfig` section
and the `gates:` block alike (D-16, spec
`integration-reality-gates-hardening.md` section 4.15) -- and produces a
valid `backlog/config/devbench.yaml`. For each setting the skill shows one
interactive choice menu: the recommended value marked as such, every
alternative, and a free-form "enter your own" path, each with a full
explanation of what the setting does and the consequence of each choice.
Every value is round-tripped through `RuntimeConfig` parsing immediately
after entry; invalid values are rejected with the parser's error message and
the operator is re-prompted.

## Every-invocation contract

This interview runs in full on every invocation of the skill. It never
silently reuses a prior answer without asking: when
`backlog/config/devbench.yaml` already exists, its values are read and shown
as the current value in every menu, but every single question is still asked
again on every run. There is no "skip because unchanged" path.

## What configure-devbench produces

- `backlog/config/devbench.yaml` -- a complete, validated devbench configuration
  file: every `config-schema.json` setting is interviewed, including
  `quota_handling:` and `skills:`, and every answered value is written.
  `gates:` is interviewed the same way, but (per the Step 21 disabled
  sub-block trim below) it is omitted from the written file entirely when
  every gate resolves to its `false` recommended default.
- A `[CONFIGURE_DEVBENCH_DONE]` summary message listing the configured values,
  including the `report` and `gates` sections.

The produced file loads without `ConfigLoader` errors -- the skill validates
the authored yaml via `load_runtime_config` BEFORE it writes the file and
reports success (spec section 4.15, AC-29): it never hands the operator a
config that breaks at the next command.

### Full-default emission (issue #260, spec FR-3.6)

Every FR-3.6 tuning section -- `timeouts`, `limits`, `stop_hook`, `hook_tail`,
`orchestrate`, `report` (including `models`, `default_model`, and every cost
multiplier), `backlog`, `validate`, `skills`, `max_executor_retries`,
`max_executor_retries_per_judge`, and `log_file` -- is now interviewed in its
own step (unlike the pre-rewrite skill, which emitted several of these at
their built-in default without asking). The final-write step assembles the
value the operator chose (or accepted as recommended) for each field, and any
field genuinely left unanswered still falls back to its resolved built-in
default with the annotated comment copied verbatim from `sample-config.yaml`.
This makes the written config self-documenting: an operator who later wants
to tune a knob sees it in the file with its value and comment already
present, instead of discovering the knob only by reading `config_loader.py`.

A few inert blocks are still trimmed rather than emitted: `bedrock_region`
when `use_bedrock` is `false`, `agents` entries that are identical to their
frontmatter defaults, disabled sub-blocks, and `debug:` entirely when the
operator skipped the debug-configuration step.

Every emitted default is sourced from the shipped config surface
(`sample-config.yaml` and the `DEFAULT_*` constants in
`src/devbench/constants.py`) -- never restated from memory -- so an emitted
value that differs from the built-in default is treated as a defect.

### Round-trip equivalence check (FR-3.6 error handling)

`load_runtime_config` alone is not enough for this check: it only parses
literal YAML, so an absent field stays `None` on the raw `RuntimeConfig`
object and comparing two raw `RuntimeConfig` values with `!=` would always
report a spurious difference between an explicit full-default config and a
minimal one. Instead, before writing the file, the skill spawns one
subprocess per candidate config (via `DEVBENCH_CONFIG_PATH`) so
`devbench.config` is imported fresh and performs its real env-var-over-YAML-
over-built-in-default resolution in each subprocess, dumps the resolved
constants backing every FR-3.6 tuning section to JSON, and diffs the two
JSON blobs. Any difference fails the walkthrough loudly, naming the
differing field, so the full-default emission can never silently change
runtime behaviour relative to a minimal config.

## Prerequisites

Before invoking configure-devbench:

1. Claude Code CLI installed and authenticated.
2. A workspace root directory exists (the directory containing `backlog/`). The skill
   writes `backlog/config/devbench.yaml` relative to the current working directory.
3. Know the `org/repo` names and `checkout_directory` paths for your target repos
   (see Step 2 below).
4. Decide the merge strategy (`squash`, `merge`, or `rebase`) for your project.

## How to invoke

From any Claude Code session with the devbench plugin available:

```
claude run devbench:configure-devbench
```

Or per-session:

```bash
claude --dangerously-skip-permissions \
  --plugin-dir $DEVBENCH_DIR/plugin/devbench
```

Then within the session:

```
run devbench:configure-devbench
```

If `backlog/config/devbench.yaml` already exists, the skill reads it and shows
its values as the current value for every question -- but, per the every-
invocation contract above, still asks every question again. Enter a blank
line to accept the shown current/recommended value.

## What the skill does (step by step)

The skill walks through 21 steps, one interview menu per schema setting,
validating each before moving to the next:

1. **Read existing config** -- shows prior values as current if `devbench.yaml` exists.
2. **repos section** (dynamic per-repo map) -- target repositories. Required fields per entry:
   - `org/repo` key (e.g. `myorg/myrepo`)
   - `checkout_directory` (workspace-relative; no leading `/` or `..`)
   - `default_branch` (e.g. `main`)
   - `merge_strategy` per-repo (optional override)
   - `branch_prefix` per-repo (optional override of `git_ops.branch_prefix`)
3. **Top-level scalars** -- `merge_strategy`, `max_executor_retries`,
   `max_executor_retries_per_judge`, `use_bedrock`, `bedrock_region`,
   `allowed_orgs`, `display_timezone`, `log_file`.
4. **timeouts section** -- per-operation timeout values in seconds.
5. **limits section** -- threshold and limit values.
6. **agents section** -- per-agent model overrides (executor, judges, workflow agents).
7. **git_ops section** -- `single_branch`, `branch_prefix`, `defer_pr`, `auto_finalize`,
   `auto_merge`, `provenance_path`, `update_submodule`, `inline_orphan_cleanup`,
   `ci_failure_retry`, `orphan_patterns`, `local_only`, `pause_before_merge`,
   and the `pr_review_resolution` sub-block (`enabled`, `agents`,
   `decision_blocks`, `settle_seconds`, `poll_interval`).
8. **task_factory section** -- `enabled`, `auto_accept_proposals`.
9. **manifest_amendment section** -- `enabled`, `allowed_reasons`,
   `max_requests_per_execution`.
10. **validate section** -- `check_orphan_path_tokens`.
11. **stop_hook section** -- `max_blocks`, `window_seconds`, `stale_task_minutes`.
12. **hook_tail section** -- column-cap settings for `devbench hook-tail`.
13. **orchestrate section** -- `max_cascade_depth`.
14. **debug section** -- diagnostic knobs (leave the whole section absent for production workspaces).
15. **backlog section** -- `default_status_for_new_work_units` (`in-queue` or `draft`),
    `bulk_update_confirm_threshold`, `bulk_update_audit_path`.
16. **gates section** -- the eight integration-reality gates (`reachability`,
    `ancestry`, `shared_file_impact`, `fixture_consistency`, `write_path_audit`,
    `newly_reachable_paths`, `composition_root`, `layout_geometry`), each
    disabled by default, plus `fixture_consistency.canonical_sources` /
    `.scan` and the per-repo `gates.repos.<org/repo>` override map (spec
    section 4.1; caylent-solutions/devbench-internal-backlog#10-#17).
17. **skills section** -- `exemplar_backlog_path`, `exemplar_spec_path`,
    `fan_out_threshold`, `max_iterations`.
18. **notifications section** -- per-event Slack toggles under `notifications.events.*`
    plus the `notifications.slack` endpoint (PR #202).
19. **report section** -- the `report.models` per-model pricing table, `default_model`
    (fallback rate for unknown/missing model ids), `display_timezone`, and the
    cost multipliers (`cache_read_multiplier`, `cache_write_5min_multiplier`,
    `cache_write_1hr_multiplier`, `data_residency_multiplier`,
    `fast_mode_multiplier`, `recent_pace_tasks`). Every rate defaults to the
    cited value in `sample-config.yaml` (issue #260, spec FR-3.6).
20. **quota_handling section** -- `enabled`, `on_exhaustion`,
    `poll_interval_seconds`, `max_wait_seconds`, `on_exhaustion_timeout`,
    `resume_strategy`, `audit_comment_on_wait`, `audit_comment_on_resume`,
    `log_structured_events` (issue #236, spec S5.2).
21. **Final validation and write** -- assembles the YAML (emitting any
    remaining FR-3.6 tuning field at its resolved value, see
    "Full-default emission" above), validates via `load_runtime_config`, then
    runs the full `RuntimeConfig` round-trip equivalence check, then writes
    `backlog/config/devbench.yaml` and reports success.

### Schema-coverage regression guard

`tests/test_plugin/test_configure_devbench_schema_coverage.py` walks
`src/devbench/config-schema.json` recursively and fails naming any property
(including every `gates.*` key) the SKILL text does not name, plus a
companion check that every interview block carries its Recommended,
Alternatives, and Free-form markers. Any future config key added without
matching interview coverage breaks this test, so the 21-step coverage above
cannot silently drift out of date.

## Validation protocol

After collecting each section's values, the skill writes a temporary YAML snippet and
runs:

```bash
python -c "
from pathlib import Path
from devbench.config_loader import load_runtime_config
import os
load_runtime_config(Path('/tmp/devbench-validate-tmp.yaml'), os.environ)
print('OK')
"
```

If the command exits non-zero, the skill extracts the error message and re-prompts the
operator. The final `devbench.yaml` is written only after every section validates
successfully.

## Key decisions to make before running

### repos: required fields

`checkout_directory` must be a relative path (no leading `/`, no `..`). The skill
rejects absolute paths and `..` traversals immediately.

### git_ops mode

| You want... | Set |
|-------------|-----|
| One branch and PR per task (default) | `git_ops.single_branch` unset |
| All tasks in one shared branch | `git_ops.single_branch: feat/my-branch` |
| PR opened only at backlog completion | `git_ops.defer_pr: true` |
| Orchestrator auto-opens PR when all tasks are terminal | `git_ops.auto_finalize: true` (requires `defer_pr: true`) |

### default status for new work units

| You want... | Set |
|-------------|-----|
| Tasks eligible for autonomous claim immediately | `backlog.default_status_for_new_work_units: in-queue` (legacy default) |
| Tasks require explicit promotion before execution | `backlog.default_status_for_new_work_units: draft` |

`draft` gives you a review gate after `spec-to-backlog` generates the backlog. Use
`devbench promote` to transition tasks to `in-queue` when ready.

## Output contract

| Artefact | Location | Condition |
|----------|----------|-----------|
| Config file | `backlog/config/devbench.yaml` | Written after all 21 steps validate |
| Summary message | stdout | `[CONFIGURE_DEVBENCH_DONE]` with a section summary |

## Cross-references

- [`plugin-authoring/devbench-authoring/skills/configure-devbench/SKILL.md`](../../plugin-authoring/devbench-authoring/skills/configure-devbench/SKILL.md) -- full skill prompt
- [`docs/skills/bootstrap-environment.md`](bootstrap-environment.md) -- next step after configure-devbench
- [`docs/devbench-yaml-reference.md`](../devbench-yaml-reference.md) -- full annotated YAML reference
- [`sample-config.yaml`](../../sample-config.yaml) -- reference config with every possible key
- [`docs/zero-to-ready.md`](../zero-to-ready.md) -- Step 7 (manual config authoring alternative)
- [`docs/onboarding.md`](../onboarding.md) -- chained-skill operator workflow

## Bounded self-critique loop

The re-prompt loop that fires on invalid YAML values is bounded by constants
in `src/devbench/constants.py`:

- `SKILL_MAX_ITERATIONS` -- maximum re-prompts before the skill emits
  `[SKILL_MAX_ITERATIONS_REACHED]` and exits non-zero.
- `SKILL_QUALITY_THRESHOLD` -- unresolved-value count at which the skill
  emits `[SKILL_QUALITY_THRESHOLD_REACHED]` and exits success.

State persistence and audit emission are handled by
`src/devbench/skill_state.py` (`read_checkpoint`, `write_checkpoint`,
`emit_audit`). The checkpoint file lives at
`<workspace>/.devbench/skill-state/configure-devbench.json` between
iterations. The audit tags flow through `devbench report` and
`devbench hook-tail`.
