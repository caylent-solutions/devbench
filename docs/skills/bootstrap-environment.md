# bootstrap-environment skill quickstart

The `bootstrap-environment` skill prepares every target repository listed in
`backlog/config/devbench.yaml` so that `make validate` passes without manual
intervention beyond yes/no confirmations. It clones repos, installs asdf toolchains,
and runs the `make validate` baseline with a self-verify retry loop.

## What bootstrap-environment produces

- Each target repo cloned to its `checkout_directory` (if not already present).
- Toolchain installed from `.tool-versions` via asdf (if the file exists).
- `make validate` passing for each repo (green baseline).
- A final summary table showing clone, toolchain, and validate status per repo.

## Prerequisites

Before invoking bootstrap-environment:

1. `backlog/config/devbench.yaml` must exist with a `repos:` section listing at least
   one target repository. If the file is absent, the skill asks for repo information
   interactively.
2. Network access to `github.com` for cloning (or the repos must already be cloned).
3. asdf installed if any target repo has a `.tool-versions` file.
4. `make` available in the environment.
5. Claude Code CLI installed and authenticated.

## How to invoke

From any Claude Code session with the devbench plugin available:

```
claude run devbench:bootstrap-environment
```

Or per-session:

```bash
claude --dangerously-skip-permissions \
  --plugin-dir $DEVBENCH_DIR/plugin/devbench
```

Then within the session:

```
run devbench:bootstrap-environment
```

## What the skill does (step by step)

1. **Reads `backlog/config/devbench.yaml`** -- extracts the `repos:` section. Each
   entry must provide `repo` (org/name), `checkout_directory`, and `default_branch`.
   If the config is absent or the `repos:` key is empty, the skill prompts the
   operator interactively.

2. **Bootstraps each repo in sequence** -- for each repo:

   - **Step 2a -- Clone**: checks whether `checkout_directory/.git` exists. If
     missing, runs `git clone`. Self-verify: re-checks `.git` after clone. On first
     failure, logs `[RETRY_CLONE]` and retries once. On second failure, escalates with
     a clear diagnostic and asks whether to skip this repo.

   - **Step 2b -- Asdf toolchain**: checks for `.tool-versions`. If found, runs
     `asdf install <plugin> <version>` for each line, then `asdf install` inside
     the checkout. Self-verify: runs `asdf current` and confirms all plugins are
     listed. Retries once on failure; escalates on second failure.

   - **Step 2c -- make validate baseline**: runs `make validate` inside
     `checkout_directory`. On failure, logs `[RETRY_VALIDATE]` and retries once. On
     second failure, escalates and asks whether to skip this repo.

   - **Step 2d -- Per-repo status report**:
     ```
     [REPO_DONE] org/repo
       clone:         OK / SKIPPED (already present)
       asdf tools:    OK / SKIPPED (no .tool-versions) / ESCALATED
       make validate: PASS / ESCALATED
     ```

3. **Final summary table** -- printed after all repos are processed:

   ```
   Bootstrap-environment complete.

   Repo                  Clone     Toolchain  Validate
   --------------------  --------  ---------  --------
   org/repo-1            OK        OK         PASS
   org/repo-2            SKIPPED   OK         PASS
   ```

   If any repo was escalated, the skill reminds the operator to re-run after resolving
   the issue.

## Self-verify retry loop

Each step (clone, asdf install, make validate) runs a verification check immediately
after the operation. On the first verification failure the skill logs a `[RETRY_*]`
entry and re-runs the step once. On a second consecutive failure it pauses, presents
a `[ESCALATE]` message with a specific diagnostic and suggested fix, and asks the
operator whether to skip this repo and continue with the rest.

This loop ensures the skill does not silently leave repos in a broken state.

## Output contract

| Artefact | Condition |
|----------|-----------|
| Cloned repos | Each `checkout_directory` has a valid `.git` |
| Toolchains | `asdf current` lists every `.tool-versions` entry |
| Baseline | `make validate` exits 0 for each non-escalated repo |
| Summary table | Printed to stdout at skill exit |

## What to do if a repo is escalated

Read the `[ESCALATE]` message for the specific diagnostic. Common patterns:

| Symptom | Suggested fix |
|---------|---------------|
| Clone fails | Verify network access to `github.com` and that `checkout_directory` path is writable |
| asdf plugin missing | Run `asdf plugin add <plugin>` for each missing plugin, then re-run the skill |
| `make validate` fails | Resolve the failing sub-target (lint, typecheck, test) manually, then re-run the skill |

After resolving the issue, re-run the skill:

```
claude run devbench:bootstrap-environment
```

The skill is idempotent -- repos already cloned and validated are reported as
`SKIPPED / PASS` without repeating the work.

## Cross-references

- [`plugin-authoring/devbench-authoring/skills/bootstrap-environment/SKILL.md`](../../plugin-authoring/devbench-authoring/skills/bootstrap-environment/SKILL.md) -- full skill prompt
- [`docs/skills/configure-devbench.md`](configure-devbench.md) -- configure devbench.yaml before bootstrapping
- [`docs/zero-to-ready.md`](../zero-to-ready.md) -- manual step-by-step onboarding guide
- [`docs/onboarding.md`](../onboarding.md) -- chained-skill operator workflow
- [`sample-config.yaml`](../../sample-config.yaml) -- reference config with annotated repos: section

## Bounded self-critique loop

The make-validate retry loop is bounded by constants in
`src/devbench/constants.py`:

- `SKILL_MAX_ITERATIONS` -- maximum retries before the skill emits
  `[SKILL_MAX_ITERATIONS_REACHED]` and exits non-zero with the `[ESCALATE]`
  message.
- `SKILL_QUALITY_THRESHOLD` -- unresolved-repo count at which the skill
  emits `[SKILL_QUALITY_THRESHOLD_REACHED]` and exits success.

State persistence and audit emission are handled by
`src/devbench/skill_state.py` (`read_checkpoint`, `write_checkpoint`,
`emit_audit`). The checkpoint file lives at
`<workspace>/.devbench/skill-state/bootstrap-environment.json` between
iterations. The audit tags flow through `devbench report` and
`devbench hook-tail`.
