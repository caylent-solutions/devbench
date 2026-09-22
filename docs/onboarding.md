# DevBench Onboarding: Chained-Skill Operator Workflow

This guide walks you through the full skill chain that takes a project idea from zero to
a running autonomous backlog:

```
create-spec -> spec-to-backlog -> configure-devbench -> bootstrap-environment -> make start
```

Each step is a Claude Code marketplace skill. The skills chain together: the output of
one step is the input to the next. By the end of this walkthrough, DevBench is
processing your backlog autonomously.

For deep-dive reference material on any individual skill, see the per-skill quickstart
docs under [`docs/skills/`](skills/).

---

## Prerequisites

Before running any skill in the chain, verify the following are in place:

1. **Claude Code CLI** -- installed and authenticated. Verify with `claude --version`.
   See [`docs/zero-to-ready.md` Step 4](zero-to-ready.md#step-4-authenticate-claude--bedrock)
   for auth options (Anthropic API or AWS Bedrock).

2. **devbench cloned** -- clone the repository and export `DEVBENCH_DIR`:

   ```bash
   git clone https://github.com/caylent-solutions/devbench.git ~/devbench
   export DEVBENCH_DIR=~/devbench
   make -C $DEVBENCH_DIR install
   ```

3. **DevBench plugin available** -- the four onboarding skills are part of the devbench
   marketplace plugin. Load the plugin per-session (recommended) or install globally:

   ```bash
   # Per-session (recommended -- avoids hook interference with other Claude sessions):
   claude --dangerously-skip-permissions \
     --plugin-dir $DEVBENCH_DIR/plugin/devbench

   # Or globally (read the warning in zero-to-ready.md Step 3 first):
   make -C $DEVBENCH_DIR plugin-install
   ```

4. **Workspace root directory** -- create the directory that will hold your backlog,
   config, and cloned target repos:

   ```bash
   mkdir -p ~/my-workspace/backlog/config
   cd ~/my-workspace && git init
   ```

---

## Step 1: create-spec -- author a rigorous engineering spec

The `create-spec` skill guides you through a structured Q&A and produces a
`spec/<project-name>.md` file that meets the kanon quality bar (1000+ lines for
non-trivial programs, 16 top-level sections, numbered and testable acceptance criteria).

**Invoke:**

```
claude run devbench:create-spec
```

Or from within a Claude Code session loaded with the plugin:

```
run devbench:create-spec
```

**What happens:**

1. The skill reads the kanon spec exemplar to internalise the 16-section structural
   skeleton and the quality bar.
2. It asks a structured question block covering: problem statement, scope, non-goals,
   functional requirements, NFRs, acceptance criteria, and resolved design decisions.
3. It authors the spec one section at a time and runs a bounded self-critique
   loop until the rubric score is zero (`SKILL_QUALITY_THRESHOLD_REACHED`
   audit) or `SKILL_MAX_ITERATIONS` is reached
   (`SKILL_MAX_ITERATIONS_REACHED` audit). The iteration counter is persisted
   in `<workspace>/.devbench/skill-state/create-spec.json` between passes; see
   `src/devbench/skill_state.py`.
4. It asks for your final sign-off, then writes `spec/<project-name>.md`.
5. It offers to invoke `spec-to-backlog` directly as the next step.

**Output:** `spec/<project-name>.md` in the current working directory.

**Reference:** [`docs/skills/create-spec.md`](skills/create-spec.md)

---

## Step 2: spec-to-backlog -- decompose the spec into a backlog

The `spec-to-backlog` skill reads `spec/<project-name>.md` and produces a complete,
validated backlog: `BACKLOG.md` plus work-unit `.md` files under `backlog/` in the
4-level hierarchy (Epic -> Feature -> Story -> Task).

**Invoke:**

```
claude run devbench:spec-to-backlog
```

The skill asks: "Which spec file should I decompose into a backlog?" Provide the path
(e.g., `spec/my-project.md`).

**What happens:**

1. The skill reads the kanon backlog exemplar to internalise the 7-column format and
   the ~50KB-per-task quality bar.
2. It decomposes every functional requirement into the 4-level hierarchy and validates
   the DAG (no cycles, no skipped levels).
3. It authors each leaf task file with all canonical sections and scores each against a
   per-task rubric, iterating until the rubric score is zero.
4. After every task file, it runs `devbench validate-backlog`. Any errors are fixed
   immediately before moving on.
5. It writes `BACKLOG.md` and runs a final `validate-backlog` pass.
6. All generated tasks default to `draft` status -- the orchestrator cannot claim them
   until you promote them with `devbench promote`.

**Output:** `BACKLOG.md` and `backlog/<epic>/.../<task>.md` work-unit files.

**Promote tasks when ready:**

```bash
# Promote a single task:
devbench promote E1-F1-S1-T1

# Promote all tasks under an epic:
devbench promote --epic E1

# Promote everything at once (no confirmation prompt):
devbench promote --all --yes
```

**Reference:** [`docs/skills/spec-to-backlog.md`](skills/spec-to-backlog.md)

---

## Step 3: configure-devbench -- author backlog/config/devbench.yaml

The `configure-devbench` skill interviews you about EVERY setting in
`src/devbench/config-schema.json` -- every existing section and the `gates:` block
alike -- and produces a valid `backlog/config/devbench.yaml`. This interview
runs in full on every invocation: it never silently reuses a prior answer.
When `backlog/config/devbench.yaml` already exists, its values are read and
shown as the CURRENT VALUE in every menu below, but every single question is
still asked again -- there is no skip-because-unchanged path anywhere in
this skill.

**Invoke:**

```
claude run devbench:configure-devbench
```

**What happens:**

1. The skill walks through 21 steps: reading the existing config (if
   present), then one interview per schema section -- `repos`, top-level
   scalars, `timeouts`, `limits`, `agents`, `git_ops`, `task_factory`,
   `manifest_amendment`, `validate`, `stop_hook`, `hook_tail`, `orchestrate`,
   `debug`, `backlog`, `gates`, `skills`, `notifications`, `report`,
   `quota_handling` -- and a final validation-and-write step. The `gates:` section
   (the eight integration-reality gates), `skills:`, `quota_handling:`, and
   `orchestrate.max_cascade_depth` are interviewed alongside every pre-existing
   section -- none of them is silently emitted at a built-in default without asking.
2. Each leaf setting's menu shows the recommended value marked as such, every
   alternative with its own consequence, and a free-form entry path, plus a full
   explanation of what the setting does; entering a blank line accepts the
   recommended (or, if the config already exists, the current) value.
3. Each section validates against `RuntimeConfig` before moving to the next.
4. In the final step, the assembled yaml (including every remaining tuning section
   at its resolved value) is validated by `load_runtime_config` -- the skill fails
   fast and returns you to the relevant step if validation fails, rather than
   writing a file that would break at the next command -- and only then runs a
   round-trip equivalence check against a minimal config before
   `backlog/config/devbench.yaml` is written and success is reported (issue #260,
   spec FR-3.6, spec section 4.15, AC-29).

**Minimum required input:** the `repos:` section -- `org/repo` key,
`checkout_directory` (workspace-relative), and `default_branch`.

**Output:** `backlog/config/devbench.yaml` that loads without `ConfigLoader` errors.

**Reference:** [`docs/skills/configure-devbench.md`](skills/configure-devbench.md)

---

## Step 4: bootstrap-environment -- clone repos and run make validate

The `bootstrap-environment` skill first interviews you about every environment
decision it owns -- the LLM credential source and Bedrock region, the Anthropic
OAuth credentials file path, the model the orchestrate skill's own coordination
calls run on, and the GitHub token source and org restriction -- then prepares
every target repository listed in `backlog/config/devbench.yaml` so that
`make validate` passes without manual intervention beyond yes/no confirmations.
This Step 0 interview runs in full on every invocation: it never silently
reuses a prior answer. The current session's already-exported value for each
variable is shown as the current value in every menu, but every single
question is still asked again -- there is no skip-because-unchanged path
anywhere in this skill.

**Invoke:**

```
claude run devbench:bootstrap-environment
```

**What happens:**

1. Step 0 interviews you about every environment decision the skill owns
   (`DEVBENCH_USE_BEDROCK`, `DEVBENCH_BEDROCK_REGION`,
   `DEVBENCH_CLAUDE_CREDENTIALS_FILE`, `DEVBENCH_CLAUDE_MODEL`, the `GH_TOKEN` /
   `DEVBENCH_GH_TOKEN_FILE` token source, and `DEVBENCH_GH_ORG`), each with a
   recommended value marked as such, every alternative, and a free-form entry
   path.
2. Reads the `repos:` section from `backlog/config/devbench.yaml`. If the config is
   absent, the skill asks interactively.
3. For each repo: clone to `checkout_directory` if not already present; install the
   asdf toolchain from `.tool-versions` if the file exists; run `make validate` as a
   baseline check.
4. Each step (Step 0's environment verification, clone, asdf install, make
   validate) self-verifies immediately after the operation. On first failure the
   skill logs `[RETRY_*]` and retries once. On a second failure it escalates with a
   clear diagnostic and asks whether to skip this repo (or, for Step 0, whether to
   continue without full environment verification).
5. Prints a final summary table showing clone, toolchain, and validate status per repo.

**Output:** a verified LLM credential source and GitHub token source, each
`checkout_directory` has a valid `.git`, the toolchain is installed, and
`make validate` exits 0 for every non-escalated repo.

**Reference:** [`docs/skills/bootstrap-environment.md`](skills/bootstrap-environment.md)

---

## Step 5: make start -- launch the orchestrator

Once the backlog is generated, config is written, repos are bootstrapped, and draft
tasks are promoted to `in-queue`, launch DevBench:

```bash
DEVBENCH_WORKSPACE_ROOT=~/my-workspace \
DEVBENCH_CLAUDE_MODEL=us.anthropic.claude-opus-4-7-v1 \
make -C $DEVBENCH_DIR start
```

DevBench claims work units in dependency order, runs the TDD cycle, submits each result
to the review judges, and lands every passing task as a git commit (and optionally a PR).

**What `make start` does automatically:**

- Claims the next eligible `in-queue` work unit.
- Invokes the executor (implements the task under TDD).
- Runs the five review judges (code, test, doc, changes-manifest, security).
- On REVIEW_PASS: runs git-ops (commit + PR or single-branch commit).
- On REVIEW_FAIL: feeds the judge feedback back to the executor and retries.
- Between work units: checks for a drain request (`devbench drain`) and exits cleanly
  if one is found.

---

## Worked example: onboarding a new Go service

This example shows the chain applied to a real project.

**Setup:**

```bash
mkdir -p ~/payment-service-ws/backlog/config
cd ~/payment-service-ws
git init
export DEVBENCH_WORKSPACE_ROOT=~/payment-service-ws
```

**Step 1 -- create-spec:**

```bash
# Open Claude Code with the plugin loaded:
claude --dangerously-skip-permissions \
  --plugin-dir $DEVBENCH_DIR/plugin/devbench

# Within the session:
run devbench:create-spec
```

Answer the Q&A blocks; the skill produces `spec/payment-service.md`.

**Step 2 -- spec-to-backlog:**

```
run devbench:spec-to-backlog
```

Provide `spec/payment-service.md` when prompted. The skill produces `BACKLOG.md` and
work-unit files. All tasks land in `draft` status.

**Step 3 -- configure-devbench:**

```
run devbench:configure-devbench
```

Enter: `org/repo` = `myorg/payment-service`, `checkout_directory` = `payment-service`,
`default_branch` = `main`. The skill writes `backlog/config/devbench.yaml`.

**Step 4 -- bootstrap-environment:**

```
run devbench:bootstrap-environment
```

Step 0 interviews you about the environment: accept the recommended
`DEVBENCH_USE_BEDROCK` (unset/false, Anthropic API) and `DEVBENCH_CLAUDE_MODEL`
(e.g. `claude-opus-4-7`, the Anthropic API id form -- see
`docs/llm-authentication.md`), then confirm the GitHub token source.
The skill then clones `github.com/myorg/payment-service` to
`~/payment-service-ws/payment-service`, installs the Go toolchain, and runs
`make validate`. Reports `PASS` when done.

**Review and promote:**

```bash
# Inspect generated tasks:
uv run --project $DEVBENCH_DIR devbench status

# Promote the first epic for autonomous execution:
uv run --project $DEVBENCH_DIR devbench promote --epic E1
```

**Step 5 -- launch:**

```bash
DEVBENCH_WORKSPACE_ROOT=~/payment-service-ws \
DEVBENCH_CLAUDE_MODEL=us.anthropic.claude-opus-4-7-v1 \
make -C $DEVBENCH_DIR start
```

DevBench begins processing tasks autonomously.

---

## Key decisions in the chained workflow

### draft vs in-queue (Step 2)

All tasks generated by `spec-to-backlog` default to `draft` status. Draft tasks are
invisible to the orchestrator -- they cannot be claimed until promoted. This gives you a
review gate between generation and execution: inspect every generated task, tighten
scope, verify Manifests, then release the ones you approve.

To skip the review gate and release all tasks immediately:

```bash
devbench promote --all --yes
```

### Single-PR vs multi-PR (Step 3)

The default mode creates one branch and PR per task. To batch all tasks into one shared
branch, set `git_ops.single_branch` in `devbench.yaml` during Step 3.

### Scoping the run (Step 5)

To process only a subset of the backlog:

```bash
DEVBENCH_WORKSPACE_ROOT=~/payment-service-ws \
DEVBENCH_CLAUDE_MODEL=us.anthropic.claude-opus-4-7-v1 \
uv run --project $DEVBENCH_DIR devbench start --include "E1-E3"
```

See [`docs/zero-to-ready.md` -- Scoping a run](zero-to-ready.md#scoping-a-run) for the
full printer-pages token syntax.

### Stopping cleanly between tasks

```bash
# Request a graceful stop after the current task completes:
uv run --project $DEVBENCH_DIR devbench drain --reason "reviewing E2 tasks"
```

The orchestrator finishes the in-flight task, detects the drain marker between tasks,
and exits cleanly. See [`docs/zero-to-ready.md` -- Stopping a run cleanly](zero-to-ready.md#stopping-a-run-cleanly).

---

## Troubleshooting the chained workflow

| Symptom | Step | Fix |
|---------|------|-----|
| Skill not found in Claude Code | 1-4 | Run `claude plugin list`; if `devbench` is missing, re-run `make -C $DEVBENCH_DIR plugin-install` or use `--plugin-dir` |
| `validate-backlog` fails after Step 2 | 2 | Check the error message; common causes: em-dash in a work-unit file, orphaned file not in BACKLOG.md, dep cycle |
| `ConfigLoader` error after Step 3 | 3 | Re-run `configure-devbench`; the skill re-prompts for invalid values |
| `make validate` fails for a repo in Step 4 | 4 | Resolve the failing sub-target (lint, typecheck, test) manually, then re-run `bootstrap-environment` |
| `DEVBENCH_WORKSPACE_ROOT not set` at Step 5 | 5 | Export the variable: `export DEVBENCH_WORKSPACE_ROOT=~/my-workspace` |
| No tasks eligible after `make start` | 5 | Check `devbench status` -- tasks may still be in `draft`; run `devbench promote` |

---

## Cross-references

- [`docs/skills/create-spec.md`](skills/create-spec.md) -- per-skill quickstart for create-spec
- [`docs/skills/spec-to-backlog.md`](skills/spec-to-backlog.md) -- per-skill quickstart for spec-to-backlog
- [`docs/skills/configure-devbench.md`](skills/configure-devbench.md) -- per-skill quickstart for configure-devbench
- [`docs/skills/bootstrap-environment.md`](skills/bootstrap-environment.md) -- per-skill quickstart for bootstrap-environment
- [`docs/zero-to-ready.md`](zero-to-ready.md) -- manual step-by-step alternative (no skills required)
- [`docs/creating-specs-and-backlogs.md`](creating-specs-and-backlogs.md) -- manual spec and backlog authoring guide
- [`docs/cli-reference.md`](cli-reference.md) -- full CLI command reference
- [`docs/backlog-contract.md`](backlog-contract.md) -- validate-backlog rule set (22 rules)
