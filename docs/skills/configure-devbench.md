# configure-devbench skill quickstart

The `configure-devbench` skill walks you through every `RuntimeConfig` section and
produces a valid `backlog/config/devbench.yaml`. Each value is round-tripped through
`RuntimeConfig` parsing immediately after entry; invalid values are rejected with the
parser's error message and the operator is re-prompted.

## What configure-devbench produces

- `backlog/config/devbench.yaml` -- a complete, validated devbench configuration
  file covering every `RuntimeConfig` section.
- A `[CONFIGURE_DEVBENCH_DONE]` summary message listing the configured values.

The produced file loads without `ConfigLoader` errors.

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

If `backlog/config/devbench.yaml` already exists, the skill reads it and pre-populates
defaults for every question. Enter a blank line to accept the shown default.

## What the skill does (step by step)

The skill walks through 16 sections, validating each before moving to the next:

1. **Read existing config** -- pre-populates defaults if `devbench.yaml` exists.
2. **repos section** -- target repositories. Required fields per entry:
   - `org/repo` key (e.g. `myorg/myrepo`)
   - `checkout_directory` (workspace-relative; no leading `/` or `..`)
   - `default_branch` (e.g. `main`)
   - `merge_strategy` per-repo (optional override)
3. **Top-level scalars** -- `merge_strategy`, `max_executor_retries`, `use_bedrock`,
   `bedrock_region`.
4. **timeouts section** -- per-operation timeout values in seconds.
5. **limits section** -- threshold and limit values.
6. **agents section** -- per-agent model overrides (executor, judges, workflow agents).
7. **git_ops section** -- `single_branch`, `defer_pr`, `auto_finalize`, `auto_merge`,
   `pause_before_merge`, `update_submodule`, `inline_orphan_cleanup`,
   `ci_failure_retry`, `local_only`.
8. **task_factory section** -- `enabled`, `auto_accept_proposals`.
9. **manifest_amendment section** -- `enabled`, `allowed_reasons`,
   `max_requests_per_execution`.
10. **validate section** -- `check_orphan_path_tokens`.
11. **stop_hook section** -- `max_blocks`, `window_seconds`, `stale_task_minutes`.
12. **hook_tail section** -- column-cap settings for `devbench hook-tail`.
13. **debug section** -- diagnostic knobs (leave blank for production workspaces).
14. **backlog section** -- `default_status_for_new_work_units` (`in-queue` or `draft`).
15. **notifications section** -- per-event Slack toggles under `notifications.events.*`
    plus the `notifications.slack` endpoint (PR #202).
16. **Final validation and write** -- assembles the YAML, runs the full
    `RuntimeConfig` round-trip, then writes `backlog/config/devbench.yaml`.

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
| Config file | `backlog/config/devbench.yaml` | Written after all 16 sections validate |
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
