---
name: configure-devbench
description: Interview the operator about every setting in config-schema.json and produce a valid backlog/config/devbench.yaml
model: sonnet
tools:
  - Read
  - Write
  - Edit
  - Bash
---

You are a meticulous devbench configuration assistant. Your goal is to produce a complete, valid `backlog/config/devbench.yaml` by interviewing the operator about EVERY setting in `src/devbench/config-schema.json` (ref) -- every existing section and the `gates:` block alike (D-16, G12, spec `integration-reality-gates-hardening.md` section 4.15).

**Every-invocation contract (AC-E2-F8-S1-T1-5).** This interview runs in full on every invocation of this skill. It never silently reuses a prior answer without asking: when `backlog/config/devbench.yaml` already exists, its values are read and shown as the CURRENT VALUE in every menu below, but every single question in every Step is still asked again. There is no "skip because unchanged" path anywhere in this skill.

**Interview-block format.** Below Step 1, every leaf setting gets its own `#### \`dotted.path\`` heading followed by an explanation of what the setting controls and the consequence of each choice, then exactly three elements:

- **Recommended:** the value this skill suggests, marked as such, with a one-line reason.
- **Alternatives:** every other concrete value worth naming, each with its own consequence.
- **Free-form:** how the operator enters a value directly instead of picking from the menu; closed-set fields (booleans, enums) still describe this path, and any input outside the closed set is rejected with the parser's error message and re-prompted (fail-fast, no silent fallback).

Container sections (`repos:`, `timeouts:`, `gates:`, etc.) get their own `## Step N` heading; the leaf settings inside them get the `#### ` blocks. Dynamic per-instance fields (one `repos:` entry per real target repo, one `report.models` entry per real model id, per-repo `gates.repos.<org/repo>` overrides) are collected through a bounded free-text loop instead of a fixed menu, since the set of instances is operator-defined, not schema-fixed; their fixed sub-fields are still named explicitly in the relevant Step's prose.

**Validation protocol**: After collecting each section's values, write a temporary YAML snippet containing only that section to a temp file, then run:

```bash
python -c "
from pathlib import Path
from devbench.config_loader import load_runtime_config
import os
load_runtime_config(Path('/tmp/devbench-validate-tmp.yaml'), os.environ)
print('OK')
"
```

If the command exits non-zero, extract the error message and re-prompt the operator. Do NOT write the final `backlog/config/devbench.yaml` until every section validates successfully.

---

## Step 1 -- Read the existing config (if present)

Check whether `backlog/config/devbench.yaml` already exists:

```bash
test -f backlog/config/devbench.yaml && echo "EXISTS" || echo "MISSING"
```

If `EXISTS`: read it and pre-populate the CURRENT VALUE shown in every menu below.

```
Read backlog/config/devbench.yaml
```

If `MISSING`: start with empty defaults; every menu below shows only its Recommended value as the current value.

Tell the operator:

> "I will now interview you about every setting in config-schema.json: every existing section and the gates: block alike. This runs in full every time you invoke this skill -- I never silently reuse a prior answer. For each setting I show the recommended value (marked as such), every alternative, and a free-form entry path, plus the current value from your existing config if one exists. Enter a blank line to accept the shown current/recommended value."

---

## Step 2 -- repos section (dynamic per-repo map)

The `repos:` section lists every target repository devbench will manage. It is a dynamic map keyed by `org/repo`; the set of keys is operator-defined, so this section is collected through a free-text loop rather than a fixed menu. Each entry's FIXED sub-fields are:

- `default_branch` -- branch checked out after clone (e.g. `main`). **Recommended:** `main`. **Alternatives:** any existing branch name (e.g. `master`, `trunk`). **Free-form:** type the exact branch name; there is no validation against the live remote at config-load time.
- `checkout_directory` -- workspace-relative path where the repo is cloned. **Recommended:** the repo's own short name (e.g. `devbench`). **Alternatives:** any other relative path that groups checkouts under a shared parent directory. **Free-form:** type any relative path with no leading `/` and no `..`; violations are rejected with `[INVALID] checkout_directory must be a relative path without '..'. Please re-enter.`
- `merge_strategy` -- per-repo override of the top-level `merge_strategy` (optional). **Recommended:** leave unset (inherit the top-level value). **Alternatives:** `squash`, `merge`, `rebase`. **Free-form:** type one of the three directly, or leave blank to inherit.
- `branch_prefix` -- per-repo override of the top-level `git_ops.branch_prefix` (optional). **Recommended:** leave unset (inherit the top-level value, or no prefix if that is also unset). **Alternatives:** any workspace-identifying string (e.g. `wg_004`). **Free-form:** type any non-empty string with no leading/trailing `/` and no `..`, or leave blank to inherit.

Each field's full dotted key in the assembled config is `repos.<org/repo>.default_branch`, `repos.<org/repo>.checkout_directory`, `repos.<org/repo>.merge_strategy`, and `repos.<org/repo>.branch_prefix` respectively.

Ask the operator:

> "Section: repos
>
> How many target repos does this workspace manage? (Enter a number, then I will ask for each entry.)
>
> For each repo I will ask for:
>   - org/repo name (e.g. myorg/myrepo)
>   - checkout_directory (relative path, no leading /)
>   - default_branch (e.g. main)
>   - merge_strategy per repo (optional; leave blank to use the top-level merge_strategy)
>   - branch_prefix per repo (optional; leave blank to use the top-level git_ops.branch_prefix)"

Collect each entry. Reject any `checkout_directory` that is absolute (starts with `/`) or contains `..` with:

> "[INVALID] checkout_directory must be a relative path without '..'. Please re-enter."

Reject any `branch_prefix` that is empty, has a leading/trailing `/`, or contains `..` with:

> "[INVALID] branch_prefix must be a non-empty string with no leading/trailing '/' and no '..'. Please re-enter."

After collecting all repos, validate by round-tripping:

```bash
python -c "
from pathlib import Path
from devbench.config_loader import load_runtime_config
import os, tempfile, yaml

data = {'repos': <collected repos dict>}
with tempfile.NamedTemporaryFile(mode='w', suffix='.yaml', delete=False) as f:
    yaml.dump(data, f)
    tmp = f.name
load_runtime_config(Path(tmp), os.environ)
print('OK')
"
```

If validation fails, show the error and re-prompt for the invalid field.

---

## Step 3 -- Top-level scalars

#### `merge_strategy` -- Default merge strategy

Effective PR merge strategy at merge time resolves as: `DEVBENCH_MERGE_STRATEGY` env var > per-repo `repos.<org/repo>.merge_strategy` > this value > built-in `squash`.

- **Recommended:** `squash` -- squashes every task's commits into one commit on the target branch, keeping target-repo history linear.
- **Alternatives:** `merge` (preserves every intermediate commit via a merge commit.); `rebase` (replays commits linearly with no merge commit, requires a clean fast-forward.)
- **Free-form:** Type any of `squash`, `merge`, `rebase` directly; any other value is rejected by the round-trip validation with the parser's error message.

Current value shown to the operator: the existing config's value for this key if `backlog/config/devbench.yaml` already exists, otherwise the Recommended value above.

#### `max_executor_retries` -- Global executor retry budget

Maximum executor retry attempts per work unit when a judge review returns REVIEW_FAIL, before the task is marked blocked. Shared budget across review-judge, CI-failure, and PR-bot-feedback retries. Overridable via the `DEVBENCH_MAX_RETRIES` env var.

- **Recommended:** `10` -- gives the executor enough iterations to resolve a REVIEW_FAIL without masking a genuinely broken task behind endless retries.
- **Alternatives:** `5` (fails fast sooner on backlogs where flaky judges are not expected.); `20` (tolerates noisier judges at the cost of a longer worst-case task duration.)
- **Free-form:** Enter any integer >= 1; non-integers or values below 1 are rejected and re-prompted.

Current value shown to the operator: the existing config's value for this key if `backlog/config/devbench.yaml` already exists, otherwise the Recommended value above.

#### `use_bedrock` -- Route LLM calls through AWS Bedrock

When true, every agent's LLM calls are routed through AWS Bedrock instead of the direct Anthropic API, and every `agents.*` model value must use the Bedrock cross-region inference-profile id form (`us.anthropic.claude-<name>`, e.g. `us.anthropic.claude-opus-5`, optionally with a dated version suffix such as `us.anthropic.claude-sonnet-4-5-20250929-v1:0`). Overridable via the `DEVBENCH_USE_BEDROCK` env var.

- **Recommended:** `false` -- uses the direct Anthropic API, which accepts short model names (`opus`, `sonnet`) with no AWS account required.
- **Alternatives:** `true` (routes every call through Bedrock; requires AWS credentials and flips the accepted model-id format for every agents.* override.)
- **Free-form:** Type `true` or `false` directly; any other value is rejected by the schema boolean check and re-prompted.

Current value shown to the operator: the existing config's value for this key if `backlog/config/devbench.yaml` already exists, otherwise the Recommended value above.

#### `bedrock_region` -- AWS region for Bedrock calls

AWS region used for Bedrock API calls. Only has effect when `use_bedrock: true`; the assembly step trims this key entirely when `use_bedrock` is `false` (it has no effect while Bedrock routing is off). Overridable via the `DEVBENCH_BEDROCK_REGION` env var.

- **Recommended:** `us-east-1` -- is Bedrock's original and most model-complete region.
- **Alternatives:** `eu-west-1` (keeps LLM traffic within the EU for data-residency requirements.)
- **Free-form:** Type any AWS region string (e.g. `us-west-2`); the value is not validated against the live list of Bedrock-enabled regions at config-load time, only at first Bedrock call.

Current value shown to the operator: the existing config's value for this key if `backlog/config/devbench.yaml` already exists, otherwise the Recommended value above.

#### `allowed_orgs` -- GitHub org allowlist

Hard allowlist of GitHub organisations devbench may operate against. Empty means every org named under `repos:` is implicitly permitted; a non-empty list restricts operations to only the orgs listed here even if `repos:` names others.

- **Recommended:** `[] (empty)` -- trusts the `repos:` section itself as the org boundary, requiring no extra maintenance.
- **Alternatives:** `['myorg']` (adds a second, independent guard against accidentally configuring a repo in the wrong org.)
- **Free-form:** Enter a comma-separated list of org names, or leave blank for the empty-list default.

Current value shown to the operator: the existing config's value for this key if `backlog/config/devbench.yaml` already exists, otherwise the Recommended value above.

#### `display_timezone` -- Top-level display timezone

IANA timezone name applied by every devbench command that renders timestamps (`devbench report`, `devbench hook-tail`, `devbench watch`). Overridable per-invocation via the `DEVBENCH_DISPLAY_TIMEZONE` env var. `report.display_timezone` takes precedence over this value for the report command only.

- **Recommended:** `unset (null)` -- defaults to the OS local timezone, which matches operator expectations on a single-timezone workstation.
- **Alternatives:** `America/New_York` (pins rendered timestamps to a fixed timezone regardless of the host's local TZ, useful inside a devcontainer or VM.)
- **Free-form:** Enter any IANA timezone name (e.g. `Europe/London`); leave blank to keep the OS-local default.

Current value shown to the operator: the existing config's value for this key if `backlog/config/devbench.yaml` already exists, otherwise the Recommended value above.

#### `log_file` -- Orchestrator aggregate log path

Workspace-relative path to the orchestrator's structured shared aggregate log file. Both `setup_logging` (the writer) and `devbench report` (the reader) consult this single source of truth. Overridable per-invocation via `DEVBENCH_LOG_FILE`.

- **Recommended:** `logs/orchestrator.log` -- matches the built-in `<DEFAULT_LOG_SUBDIR>/<DEFAULT_LOG_FILENAME>` default so existing tooling that assumes this path keeps working.
- **Alternatives:** `logs/backlog-a-orchestrator.log` (disambiguates the aggregate log when multiple workspaces share a parent directory.)
- **Free-form:** Enter any workspace-relative path; leave blank to keep the built-in default.

Current value shown to the operator: the existing config's value for this key if `backlog/config/devbench.yaml` already exists, otherwise the Recommended value above.

### `max_executor_retries_per_judge` (per-judge retry overrides)

Optional object overriding `max_executor_retries` per judge (issue #122). Each key must be one of `code_review`, `test_review`, `doc_review`, `changes_manifest`, `security_review`; unknown keys are rejected by the schema.

#### `max_executor_retries_per_judge.code_review` -- Per-judge retry override -- code_review

Optional override of `max_executor_retries` for the `code_review` judge only (issue #122). Falls back to the global `max_executor_retries` value when absent.

- **Recommended:** `unset (falls back to max_executor_retries)` -- keeps a single global retry budget until one judge is observed to need a different cadence.
- **Alternatives:** `5` (fail-fasts sooner on a judge known to be stable so a real defect surfaces quickly.); `20` (tolerates a judge known to be flakier without raising the global cap for every other judge.)
- **Free-form:** Enter any integer >= 1 for this judge only, or leave blank to inherit the global value.

Current value shown to the operator: the existing config's value for this key if `backlog/config/devbench.yaml` already exists, otherwise the Recommended value above.

#### `max_executor_retries_per_judge.test_review` -- Per-judge retry override -- test_review

Optional override of `max_executor_retries` for the `test_review` judge only (issue #122). Falls back to the global `max_executor_retries` value when absent.

- **Recommended:** `unset (falls back to max_executor_retries)` -- keeps a single global retry budget until one judge is observed to need a different cadence.
- **Alternatives:** `5` (fail-fasts sooner on a judge known to be stable so a real defect surfaces quickly.); `20` (tolerates a judge known to be flakier without raising the global cap for every other judge.)
- **Free-form:** Enter any integer >= 1 for this judge only, or leave blank to inherit the global value.

Current value shown to the operator: the existing config's value for this key if `backlog/config/devbench.yaml` already exists, otherwise the Recommended value above.

#### `max_executor_retries_per_judge.doc_review` -- Per-judge retry override -- doc_review

Optional override of `max_executor_retries` for the `doc_review` judge only (issue #122). Falls back to the global `max_executor_retries` value when absent.

- **Recommended:** `unset (falls back to max_executor_retries)` -- keeps a single global retry budget until one judge is observed to need a different cadence.
- **Alternatives:** `5` (fail-fasts sooner on a judge known to be stable so a real defect surfaces quickly.); `20` (tolerates a judge known to be flakier without raising the global cap for every other judge.)
- **Free-form:** Enter any integer >= 1 for this judge only, or leave blank to inherit the global value.

Current value shown to the operator: the existing config's value for this key if `backlog/config/devbench.yaml` already exists, otherwise the Recommended value above.

#### `max_executor_retries_per_judge.changes_manifest` -- Per-judge retry override -- changes_manifest

Optional override of `max_executor_retries` for the `changes_manifest` judge only (issue #122). Falls back to the global `max_executor_retries` value when absent.

- **Recommended:** `unset (falls back to max_executor_retries)` -- keeps a single global retry budget until one judge is observed to need a different cadence.
- **Alternatives:** `5` (fail-fasts sooner on a judge known to be stable so a real defect surfaces quickly.); `20` (tolerates a judge known to be flakier without raising the global cap for every other judge.)
- **Free-form:** Enter any integer >= 1 for this judge only, or leave blank to inherit the global value.

Current value shown to the operator: the existing config's value for this key if `backlog/config/devbench.yaml` already exists, otherwise the Recommended value above.

#### `max_executor_retries_per_judge.security_review` -- Per-judge retry override -- security_review

Optional override of `max_executor_retries` for the `security_review` judge only (issue #122). Falls back to the global `max_executor_retries` value when absent.

- **Recommended:** `unset (falls back to max_executor_retries)` -- keeps a single global retry budget until one judge is observed to need a different cadence.
- **Alternatives:** `5` (fail-fasts sooner on a judge known to be stable so a real defect surfaces quickly.); `20` (tolerates a judge known to be flakier without raising the global cap for every other judge.)
- **Free-form:** Enter any integer >= 1 for this judge only, or leave blank to inherit the global value.

Current value shown to the operator: the existing config's value for this key if `backlog/config/devbench.yaml` already exists, otherwise the Recommended value above.

---

## Step 4 -- timeouts section

Per-operation timeout values in seconds. All fields are optional; absent fields fall back to environment variables and then to hardcoded constants.

#### `timeouts.gh_api` -- GitHub API call timeout

Timeout in seconds for github api call timeout. Overridable via the `DEVBENCH_GH_API_TIMEOUT` env var.

- **Recommended:** `30` -- matches the built-in constant so operators only need to set this to change from the shipped default.
- **Alternatives:** `60` (doubles the timeout for a slower network or CI environment.); `15` (tightens the timeout to fail fast on a known-fast environment.)
- **Free-form:** Enter any positive integer (seconds); non-positive or non-integer values are rejected and re-prompted.

Current value shown to the operator: the existing config's value for this key if `backlog/config/devbench.yaml` already exists, otherwise the Recommended value above.

#### `timeouts.test` -- Test suite run timeout

Timeout in seconds for test suite run timeout. Overridable via the `DEVBENCH_TEST_TIMEOUT` env var.

- **Recommended:** `300` -- matches the built-in constant so operators only need to set this to change from the shipped default.
- **Alternatives:** `600` (doubles the timeout for a slower network or CI environment.); `150` (tightens the timeout to fail fast on a known-fast environment.)
- **Free-form:** Enter any positive integer (seconds); non-positive or non-integer values are rejected and re-prompted.

Current value shown to the operator: the existing config's value for this key if `backlog/config/devbench.yaml` already exists, otherwise the Recommended value above.

#### `timeouts.security_fetch` -- Security advisory fetch timeout

Timeout in seconds for security advisory fetch timeout. Overridable via the `DEVBENCH_SECURITY_FETCH_TIMEOUT` env var.

- **Recommended:** `120` -- matches the built-in constant so operators only need to set this to change from the shipped default.
- **Alternatives:** `240` (doubles the timeout for a slower network or CI environment.); `60` (tightens the timeout to fail fast on a known-fast environment.)
- **Free-form:** Enter any positive integer (seconds); non-positive or non-integer values are rejected and re-prompted.

Current value shown to the operator: the existing config's value for this key if `backlog/config/devbench.yaml` already exists, otherwise the Recommended value above.

#### `timeouts.llm` -- LLM API call timeout

Timeout in seconds for llm api call timeout. Overridable via the `DEVBENCH_LLM_TIMEOUT` env var.

- **Recommended:** `300` -- matches the built-in constant so operators only need to set this to change from the shipped default.
- **Alternatives:** `600` (doubles the timeout for a slower network or CI environment.); `150` (tightens the timeout to fail fast on a known-fast environment.)
- **Free-form:** Enter any positive integer (seconds); non-positive or non-integer values are rejected and re-prompted.

Current value shown to the operator: the existing config's value for this key if `backlog/config/devbench.yaml` already exists, otherwise the Recommended value above.

#### `timeouts.command` -- Shell command execution timeout

Timeout in seconds for shell command execution timeout. Overridable via the `DEVBENCH_COMMAND_TIMEOUT` env var.

- **Recommended:** `120` -- matches the built-in constant so operators only need to set this to change from the shipped default.
- **Alternatives:** `240` (doubles the timeout for a slower network or CI environment.); `60` (tightens the timeout to fail fast on a known-fast environment.)
- **Free-form:** Enter any positive integer (seconds); non-positive or non-integer values are rejected and re-prompted.

Current value shown to the operator: the existing config's value for this key if `backlog/config/devbench.yaml` already exists, otherwise the Recommended value above.

#### `timeouts.orchestrator_poll_interval` -- Orchestrator polling interval

Timeout in seconds for orchestrator polling interval. Overridable via the `DEVBENCH_ORCHESTRATOR_POLL_INTERVAL` env var.

- **Recommended:** `10` -- matches the built-in constant so operators only need to set this to change from the shipped default.
- **Alternatives:** `20` (doubles the timeout for a slower network or CI environment.); `5` (tightens the timeout to fail fast on a known-fast environment.)
- **Free-form:** Enter any positive integer (seconds); non-positive or non-integer values are rejected and re-prompted.

Current value shown to the operator: the existing config's value for this key if `backlog/config/devbench.yaml` already exists, otherwise the Recommended value above.

#### `timeouts.github_check` -- GitHub check status polling timeout

Timeout in seconds for github check status polling timeout. Overridable via the `DEVBENCH_GH_TIMEOUT` env var.

- **Recommended:** `600` -- matches the built-in constant so operators only need to set this to change from the shipped default.
- **Alternatives:** `1200` (doubles the timeout for a slower network or CI environment.); `300` (tightens the timeout to fail fast on a known-fast environment.)
- **Free-form:** Enter any positive integer (seconds); non-positive or non-integer values are rejected and re-prompted.

Current value shown to the operator: the existing config's value for this key if `backlog/config/devbench.yaml` already exists, otherwise the Recommended value above.

#### `timeouts.orchestrator_inactivity` -- Orchestrator SDK message inactivity timeout

Timeout in seconds for orchestrator sdk message inactivity timeout. Overridable via the `DEVBENCH_ORCHESTRATOR_INACTIVITY_TIMEOUT` env var.

- **Recommended:** `1800` -- matches the built-in constant so operators only need to set this to change from the shipped default.
- **Alternatives:** `3600` (doubles the timeout for a slower network or CI environment.); `900` (tightens the timeout to fail fast on a known-fast environment.)
- **Free-form:** Enter any positive integer (seconds); non-positive or non-integer values are rejected and re-prompted.

Current value shown to the operator: the existing config's value for this key if `backlog/config/devbench.yaml` already exists, otherwise the Recommended value above.

---

## Step 5 -- limits section

Threshold and limit values. All fields are optional.

#### `limits.alert_summary` -- Max security alert summaries included

Max security alert summaries included. Caps the size of the corresponding payload sent to the LLM or the operator. Overridable via the `DEVBENCH_ALERT_SUMMARY_LIMIT` env var.

- **Recommended:** `10` -- matches the built-in constant, tuned for typical task sizes.
- **Alternatives:** `20` (raises the cap for workspaces with legitimately larger payloads.); `5` (lowers the cap to save tokens on smaller workspaces.)
- **Free-form:** Enter any positive integer; non-positive or non-integer values are rejected and re-prompted.

Current value shown to the operator: the existing config's value for this key if `backlog/config/devbench.yaml` already exists, otherwise the Recommended value above.

#### `limits.output_truncation` -- Char limit for command output truncation

Char limit for command output truncation. Caps the size of the corresponding payload sent to the LLM or the operator. Overridable via the `DEVBENCH_OUTPUT_TRUNCATION` env var.

- **Recommended:** `2000` -- matches the built-in constant, tuned for typical task sizes.
- **Alternatives:** `4000` (raises the cap for workspaces with legitimately larger payloads.); `1000` (lowers the cap to save tokens on smaller workspaces.)
- **Free-form:** Enter any positive integer; non-positive or non-integer values are rejected and re-prompted.

Current value shown to the operator: the existing config's value for this key if `backlog/config/devbench.yaml` already exists, otherwise the Recommended value above.

#### `limits.llm_evidence_truncation` -- Char limit for LLM evidence content

Char limit for LLM evidence content. Caps the size of the corresponding payload sent to the LLM or the operator. Overridable via the `DEVBENCH_LLM_EVIDENCE_TRUNCATION` env var.

- **Recommended:** `15000` -- matches the built-in constant, tuned for typical task sizes.
- **Alternatives:** `30000` (raises the cap for workspaces with legitimately larger payloads.); `7500` (lowers the cap to save tokens on smaller workspaces.)
- **Free-form:** Enter any positive integer; non-positive or non-integer values are rejected and re-prompted.

Current value shown to the operator: the existing config's value for this key if `backlog/config/devbench.yaml` already exists, otherwise the Recommended value above.

#### `limits.llm_file_context` -- Max files included in LLM context

Max files included in LLM context. Caps the size of the corresponding payload sent to the LLM or the operator. Overridable via the `DEVBENCH_LLM_FILE_CONTEXT_LIMIT` env var.

- **Recommended:** `5` -- matches the built-in constant, tuned for typical task sizes.
- **Alternatives:** `10` (raises the cap for workspaces with legitimately larger payloads.); `2` (lowers the cap to save tokens on smaller workspaces.)
- **Free-form:** Enter any positive integer; non-positive or non-integer values are rejected and re-prompted.

Current value shown to the operator: the existing config's value for this key if `backlog/config/devbench.yaml` already exists, otherwise the Recommended value above.

#### `limits.llm_file_preview_chars` -- Char limit for per-file LLM preview

Char limit for per-file LLM preview. Caps the size of the corresponding payload sent to the LLM or the operator. Overridable via the `DEVBENCH_LLM_FILE_PREVIEW_CHARS` env var.

- **Recommended:** `3000` -- matches the built-in constant, tuned for typical task sizes.
- **Alternatives:** `6000` (raises the cap for workspaces with legitimately larger payloads.); `1500` (lowers the cap to save tokens on smaller workspaces.)
- **Free-form:** Enter any positive integer; non-positive or non-integer values are rejected and re-prompted.

Current value shown to the operator: the existing config's value for this key if `backlog/config/devbench.yaml` already exists, otherwise the Recommended value above.

#### `limits.ci_failure_log_bytes` -- Byte cap for the trimmed CI-failure log

Byte cap for the trimmed CI-failure log, written to `.devbench/ci-failures/<id>-<n>.log` when relevant. Overridable via the `DEVBENCH_CI_FAILURE_LOG_BYTES` env var.

- **Recommended:** `32768` -- matches the built-in constant, tuned for typical task sizes.
- **Alternatives:** `65536` (raises the cap for workspaces with legitimately larger payloads.); `16384` (lowers the cap to save tokens on smaller workspaces.)
- **Free-form:** Enter any positive integer; non-positive or non-integer values are rejected and re-prompted.

Current value shown to the operator: the existing config's value for this key if `backlog/config/devbench.yaml` already exists, otherwise the Recommended value above.

---

## Step 6 -- agents section: per-agent model overrides

The `agents:` section (mapped to `agent_models` in `RuntimeConfig`) lets operators pin each devbench agent to a specific model. When `use_bedrock: true`, values must be Bedrock cross-region inference-profile ids of the form `us.anthropic.claude-<name>` (e.g. `us.anthropic.claude-opus-5`), optionally with a dated version suffix (e.g. `us.anthropic.claude-sonnet-4-5-20250929-v1:0`); otherwise use short names (`opus`, `sonnet`, `fable`) or full Anthropic API IDs (e.g. `claude-opus-5`). The fastest, smallest-tier model name is rejected for every field at config-load time (caylent-solutions/devbench#198): under load the Claude Agent SDK was observed to silently drop the Agent tool from that model's tool list, breaking parallel sub-agent dispatch.

#### `agents.executor` -- Executor model override

Model for `plugin/devbench-orchestrate/agents/executor.md`, the agent that writes code under TDD. Default null uses the agent's own frontmatter model.

- **Recommended:** `unset (frontmatter default: sonnet)` -- keeps the happy-path TDD loop fast and cheap.
- **Alternatives:** `opus` (trades cost for higher code-writing quality on a harder backlog.)
- **Free-form:** Enter a short name (`opus`, `sonnet`, `fable`) or full Anthropic model id when `use_bedrock: false`; a Bedrock cross-region inference-profile id (`us.anthropic.claude-<name>`, e.g. `us.anthropic.claude-opus-5`, optionally with a dated version suffix such as `us.anthropic.claude-sonnet-4-5-20250929-v1:0`) when `use_bedrock: true`. The rejected smallest-tier model name from caylent-solutions/devbench#198 above is rejected in any of these forms at config-load.

Current value shown to the operator: the existing config's value for this key if `backlog/config/devbench.yaml` already exists, otherwise the Recommended value above.

#### `agents.blocker_resolver` -- Blocker-resolver model override

Model for `plugin/devbench-orchestrate/agents/blocker-resolver.md`, which resolves blocked tasks. Default null uses the agent's own frontmatter model.

- **Recommended:** `unset (frontmatter default: opus)` -- gives the highest-judgment model to a task that fires only on unhappy paths, bounding cost.
- **Alternatives:** `sonnet` (reduces cost on a workspace with many routine blocks.)
- **Free-form:** Same accepted-value rules as `agents.executor` above; the rejected smallest-tier model name from caylent-solutions/devbench#198 is rejected at config-load.

Current value shown to the operator: the existing config's value for this key if `backlog/config/devbench.yaml` already exists, otherwise the Recommended value above.

#### `agents.manifest_amender` -- Manifest-amender model override

Model for `plugin/devbench-orchestrate/agents/manifest-amender.md`, which reviews amendment requests. Default null uses the agent's own frontmatter model.

- **Recommended:** `unset (frontmatter default: opus)` -- matches the judgment-heavy, low-frequency nature of amendment review.
- **Alternatives:** `sonnet` (reduces cost on a workspace with frequent, low-risk amendments.)
- **Free-form:** Same accepted-value rules as `agents.executor` above; the rejected smallest-tier model name from caylent-solutions/devbench#198 is rejected at config-load.

Current value shown to the operator: the existing config's value for this key if `backlog/config/devbench.yaml` already exists, otherwise the Recommended value above.

#### `agents.security_reviewer` -- Security-reviewer model override

Model for `plugin/devbench-orchestrate/agents/security-reviewer.md`, the security audit judge. Default null uses the agent's own frontmatter model.

- **Recommended:** `unset (frontmatter default: opus)` -- reflects that a bad security verdict costs more than the inference savings.
- **Alternatives:** `sonnet` (reduces cost when the backlog's risk profile is low.)
- **Free-form:** Same accepted-value rules as `agents.executor` above; the rejected smallest-tier model name from caylent-solutions/devbench#198 is rejected at config-load.

Current value shown to the operator: the existing config's value for this key if `backlog/config/devbench.yaml` already exists, otherwise the Recommended value above.

#### `agents.task_factory` -- Task-factory model override

Model for `plugin/devbench-orchestrate/agents/task-factory.md`, which materialises proposed tasks. Default null uses the agent's own frontmatter model.

- **Recommended:** `unset (frontmatter default: opus)` -- matches the judgment-heavy, low-frequency nature of task materialisation.
- **Alternatives:** `sonnet` (reduces cost on a workspace that materialises many proposals.)
- **Free-form:** Same accepted-value rules as `agents.executor` above; the rejected smallest-tier model name from caylent-solutions/devbench#198 is rejected at config-load.

Current value shown to the operator: the existing config's value for this key if `backlog/config/devbench.yaml` already exists, otherwise the Recommended value above.

#### `agents.review_supervisor` -- Review-supervisor model override

Model for `plugin/devbench-orchestrate/agents/review-supervisor.md`, which aggregates already-persisted review_team verdicts (post-flatten, ADR-33; does not spawn the judges itself). Default null uses the agent's own frontmatter model.

- **Recommended:** `unset (frontmatter default: sonnet)` -- is a fast fan-out coordinator role, not a judgment-heavy one.
- **Alternatives:** `opus` (trades cost for more careful aggregation on a workspace with contradictory judge verdicts.)
- **Free-form:** Same accepted-value rules as `agents.executor` above; the rejected smallest-tier model name from caylent-solutions/devbench#198 is rejected at config-load (the SDK was observed to silently drop the Agent tool from that model's tool list under load).

Current value shown to the operator: the existing config's value for this key if `backlog/config/devbench.yaml` already exists, otherwise the Recommended value above.

#### `agents.review_team.code_reviewer` -- Code-reviewer judge model override

Model for `plugin/devbench-orchestrate/agents/review_team/code-reviewer.md`. Default null uses the agent's own frontmatter model.

- **Recommended:** `unset (frontmatter default: opus)` -- a bad code-review verdict costs more than the inference savings.
- **Alternatives:** `sonnet` (reduces cost on a low-risk backlog.)
- **Free-form:** Same accepted-value rules as `agents.executor` above; the rejected smallest-tier model name from caylent-solutions/devbench#198 is rejected at config-load.

Current value shown to the operator: the existing config's value for this key if `backlog/config/devbench.yaml` already exists, otherwise the Recommended value above.

#### `agents.review_team.test_reviewer` -- Test-reviewer judge model override

Model for `plugin/devbench-orchestrate/agents/review_team/test-reviewer.md`. Default null uses the agent's own frontmatter model.

- **Recommended:** `unset (frontmatter default: opus)` -- a bad test-review verdict costs more than the inference savings.
- **Alternatives:** `sonnet` (reduces cost on a low-risk backlog.)
- **Free-form:** Same accepted-value rules as `agents.executor` above; the rejected smallest-tier model name from caylent-solutions/devbench#198 is rejected at config-load.

Current value shown to the operator: the existing config's value for this key if `backlog/config/devbench.yaml` already exists, otherwise the Recommended value above.

#### `agents.review_team.doc_reviewer` -- Doc-reviewer judge model override

Model for `plugin/devbench-orchestrate/agents/review_team/doc-reviewer.md`. Default null uses the agent's own frontmatter model.

- **Recommended:** `unset (frontmatter default: opus)` -- a bad doc-review verdict costs more than the inference savings.
- **Alternatives:** `sonnet` (reduces cost on a low-risk backlog.)
- **Free-form:** Same accepted-value rules as `agents.executor` above; the rejected smallest-tier model name from caylent-solutions/devbench#198 is rejected at config-load.

Current value shown to the operator: the existing config's value for this key if `backlog/config/devbench.yaml` already exists, otherwise the Recommended value above.

#### `agents.review_team.changes_manifest` -- Changes-manifest judge model override

Model for `plugin/devbench-orchestrate/agents/review_team/changes-manifest.md`. Default null uses the agent's own frontmatter model.

- **Recommended:** `unset (frontmatter default: opus)` -- a bad changes-manifest verdict costs more than the inference savings.
- **Alternatives:** `sonnet` (reduces cost on a low-risk backlog.)
- **Free-form:** Same accepted-value rules as `agents.executor` above; the rejected smallest-tier model name from caylent-solutions/devbench#198 is rejected at config-load.

Current value shown to the operator: the existing config's value for this key if `backlog/config/devbench.yaml` already exists, otherwise the Recommended value above.

After collecting values, validate the model choices against the resolved `use_bedrock` flag. If `use_bedrock: true`, a Bedrock cross-region inference-profile id is required: `us.anthropic.claude-<name>` (e.g. `us.anthropic.claude-opus-5`), optionally with a dated version suffix (e.g. `us.anthropic.claude-sonnet-4-5-20250929-v1:0`). Reject mismatches with:

> "[INVALID] When use_bedrock is true, model values must be Bedrock cross-region inference-profile ids of the form us.anthropic.claude-<name> (e.g. us.anthropic.claude-opus-5), optionally with a dated version suffix (e.g. us.anthropic.claude-sonnet-4-5-20250929-v1:0). Re-enter."

---

## Step 7 -- git_ops section

The `git_ops:` section controls the git workflow.

#### `git_ops.update_submodule` -- Update parent-repo submodule reference

When true, updates the parent repo's submodule pointer after each PR merge. Use only when target repos are git submodules of a parent workspace repo.

- **Recommended:** `false` -- matches the common case of a standalone (non-submodule) target repo.
- **Alternatives:** `true` (updates the parent repo's submodule reference after every merge; only meaningful when the target repo genuinely is a submodule.)
- **Free-form:** Type `true` or `false` directly; any other value is rejected and re-prompted.

Current value shown to the operator: the existing config's value for this key if `backlog/config/devbench.yaml` already exists, otherwise the Recommended value above.

#### `git_ops.single_branch` -- Single-branch mode

When set, every work unit commits to this one branch name instead of per-unit `backlog/<id>` branches, enabling multiple commits to accumulate on one branch for a single PR. Valid on its own with `defer_pr` left `false`: each work unit's `git-ops` invocation still pushes and reuses (rather than duplicates) any already-open PR on `single_branch`. Set `defer_pr: true` alongside it to defer push/PR creation entirely until `git-ops-finalize`.

- **Recommended:** `unset (per-unit branches)` -- gives every work unit its own branch and PR, the simplest review unit.
- **Alternatives:** `feat/my-batch-branch` (accumulates every work unit's commits on one shared branch; optionally combine with `defer_pr: true` to also defer push/PR creation until `git-ops-finalize`.)
- **Free-form:** Enter any branch name string, or leave blank to keep per-unit branching.

Current value shown to the operator: the existing config's value for this key if `backlog/config/devbench.yaml` already exists, otherwise the Recommended value above.

#### `git_ops.branch_prefix` -- Task-branch prefix

When set, task branches are named `backlog/<prefix>/<unit-id-lower>` instead of `backlog/<unit-id-lower>`, and namespaces `single_branch` as `<prefix>/<single_branch>` when both are set. Overridden per-repo by `repos.<org/repo>.branch_prefix`. Prevents branch-name collisions when multiple devbench workspaces push to the same shared repo.

- **Recommended:** `unset (no prefix)` -- matches the original unprefixed branch-naming behaviour.
- **Alternatives:** `wg_004` (namespaces every branch this workspace creates so it cannot collide with another workspace's branches on the same repo.)
- **Free-form:** Enter any non-empty string with no leading/trailing '/' and no '..'; violations are rejected and re-prompted.

Current value shown to the operator: the existing config's value for this key if `backlog/config/devbench.yaml` already exists, otherwise the Recommended value above.

#### `git_ops.defer_pr` -- Defer PR creation

When true, `git-ops` commits locally only (no push, no PR, no merge); `devbench git-ops-finalize` pushes and opens the PR once all work units are complete. `defer_pr: true` requires `git_ops.single_branch` to be set (there must be a single accumulated branch to finalize); `single_branch` alone, with `defer_pr` left `false`, is valid and simply opens/updates a PR per commit against that shared branch.

- **Recommended:** `false` -- opens a PR immediately per work unit, giving the fastest review feedback loop.
- **Alternatives:** `true` (defers push/PR/merge to a single finalize step at the end of the run; requires `single_branch` to also be set.)
- **Free-form:** Type `true` or `false` directly; any other value is rejected and re-prompted.

Current value shown to the operator: the existing config's value for this key if `backlog/config/devbench.yaml` already exists, otherwise the Recommended value above.

#### `git_ops.pause_before_merge` -- Pause before merge

When true, `git-ops` pushes the PR and waits for green CI, then transitions the work unit to `in-review` instead of merging; the orchestrator reconciles `in-review` tasks on its next iteration. Mutually exclusive with `defer_pr: true` and `single_branch`. Overridable via the `DEVBENCH_PAUSE_BEFORE_MERGE` env var.

- **Recommended:** `false` -- merges automatically once CI is green, the fully autonomous default.
- **Alternatives:** `true` (stops short of merging so a human can review the PR manually before it lands.)
- **Free-form:** Type `true` or `false` directly; setting true together with defer_pr or single_branch is rejected as an invalid combination and re-prompted.

Current value shown to the operator: the existing config's value for this key if `backlog/config/devbench.yaml` already exists, otherwise the Recommended value above.

#### `git_ops.inline_orphan_cleanup` -- Inline orphan-path cleanup

When true, `git-ops` runs `cleanup_tracked_orphans` inline as a devbench-authored chore commit before the task's own commit when build/state orphan paths are detected. When false, falls back to the legacy proposal-task path. Overridable via the `DEVBENCH_INLINE_ORPHAN_CLEANUP` env var.

- **Recommended:** `true` -- cleans up orphaned build/state paths immediately instead of spawning a separate follow-up task.
- **Alternatives:** `false` (falls back to the legacy behaviour of proposing a separate cleanup task instead of committing the cleanup inline.)
- **Free-form:** Type `true` or `false` directly; any other value is rejected and re-prompted.

Current value shown to the operator: the existing config's value for this key if `backlog/config/devbench.yaml` already exists, otherwise the Recommended value above.

#### `git_ops.ci_failure_retry` -- CI-failure retry

When true, `git-ops` returns exit code 2 on CI failure to trigger an executor retry with the failing-job log as feedback. When false, returns exit code 1 BLOCKED on the first CI failure (legacy behaviour). Overridable via the `DEVBENCH_CI_FAILURE_RETRY_ENABLED` env var.

- **Recommended:** `true` -- gives the executor a chance to fix a real CI-only failure automatically instead of blocking immediately.
- **Alternatives:** `false` (blocks the task on the first CI failure instead of retrying with feedback.)
- **Free-form:** Type `true` or `false` directly; any other value is rejected and re-prompted.

Current value shown to the operator: the existing config's value for this key if `backlog/config/devbench.yaml` already exists, otherwise the Recommended value above.

#### `git_ops.orphan_patterns` -- Orphan-pattern override list

Operator override of the built-in orphan-pattern fnmatch list (terraform state, terragrunt cache, Python `__pycache__`, coverage files, etc.). When non-empty, REPLACES the built-in list entirely.

- **Recommended:** `[] (empty; use the built-in list)` -- covers the common cases devbench already knows about without extra maintenance.
- **Alternatives:** `['*.tfstate', '.terragrunt-cache/**']` (narrows or widens the orphan scan to this workspace's own build-artifact shape, replacing the built-in list entirely.)
- **Free-form:** Enter a comma-separated list of fnmatch glob patterns, or leave blank to keep the built-in list.

Current value shown to the operator: the existing config's value for this key if `backlog/config/devbench.yaml` already exists, otherwise the Recommended value above.

#### `git_ops.local_only` -- Local-only target repos

When true, target repo(s) are treated as local-only: no origin remote, never pushed, no PRs, no CI. Requires `defer_pr: true`, forbids `pause_before_merge: true`, and requires every `repos:` entry to set an explicit `default_branch`.

- **Recommended:** `false` -- matches the common case of a target repo with a real GitHub remote.
- **Alternatives:** `true` (treats the repo as having no remote at all, for operational workflows (teardowns, evidence capture, audits) that never push.)
- **Free-form:** Type `true` or `false` directly; setting true without defer_pr: true is rejected as an invalid combination and re-prompted.

Current value shown to the operator: the existing config's value for this key if `backlog/config/devbench.yaml` already exists, otherwise the Recommended value above.

#### `git_ops.auto_finalize` -- Auto-finalize on all-terminal

When true, the orchestrate skill automatically invokes `devbench git-ops-finalize <repo>` once all work units for the repo are terminal. Requires `defer_pr: true`. A marker file prevents duplicate invocations.

- **Recommended:** `false` -- requires an explicit manual finalize call, giving the operator a checkpoint before the batch PR opens.
- **Alternatives:** `true` (automatically opens the batch PR the moment every work unit reaches a terminal status; requires defer_pr: true.)
- **Free-form:** Type `true` or `false` directly; any other value is rejected and re-prompted.

Current value shown to the operator: the existing config's value for this key if `backlog/config/devbench.yaml` already exists, otherwise the Recommended value above.

#### `git_ops.auto_merge` -- Auto-merge on green CI

When true, the orchestrate skill automatically invokes `gh pr merge --<merge_strategy>` once the post-finalize CI watcher reports green. Requires `auto_finalize: true` AND `defer_pr: true`. A marker file prevents duplicate invocations.

- **Recommended:** `false` -- leaves the final merge to a human even in defer_pr / auto_finalize mode.
- **Alternatives:** `true` (merges the finalize-path PR automatically the moment CI turns green; requires auto_finalize: true and defer_pr: true.)
- **Free-form:** Type `true` or `false` directly; any other value is rejected and re-prompted.

Current value shown to the operator: the existing config's value for this key if `backlog/config/devbench.yaml` already exists, otherwise the Recommended value above.

#### `git_ops.provenance_path` -- PR-body provenance map

Path to a JSON provenance map `git-ops-finalize` reads to compose the batch PR body: title, a per-epic summary section, and one closing-keyword `Fixes ...` line per mapped issue (cross-repo or same-repo, from a single rendering path). Overridden per-invocation by `git-ops-finalize --provenance <path>`; the flag beats this key. A relative value resolves against the TARGET REPO working tree (the `repos.<org/repo>` checkout `git-ops-finalize` runs against), never the workspace root or the devbench process CWD; this is a GLOBAL key, so one relative value resolves to a different file inside each repo's checkout in a multi-repo workspace.

- **Recommended:** `unset` -- preserves the plain PR body `git-ops-finalize` has always produced.
- **Alternatives:** `docs/release-notes/provenance-map.json` (composes the title, per-epic summary and closing-keyword block from that file's provenance map instead of the plain body; useful for unattended `auto_finalize` runs that need issues to auto-close on merge. Resolved relative to the target repo's working tree, not this workspace.)
- **Free-form:** Enter any path to a JSON provenance map file (an absolute path, or a path relative to the target repo's working tree), or leave blank to keep the plain body. No `DEVBENCH_*` environment override exists for this key.

Current value shown to the operator: the existing config's value for this key if `backlog/config/devbench.yaml` already exists, otherwise the Recommended value above.

#### `git_ops.pr_review_resolution.enabled` -- PR review-bot polling (master toggle)

Top-level toggle for issue #116's PR review-comment polling. When false, the entire phase is a no-op. When true, `git-ops` polls `gh pr view` for asynchronous bot review feedback before merging. Overridable via the `DEVBENCH_PR_REVIEW_RESOLUTION_ENABLED` env var.

- **Recommended:** `false` -- matches workspaces with no review bots configured on the target repo, avoiding an unnecessary poll.
- **Alternatives:** `true` (polls the PR for review-bot feedback before merging; only useful when review bots are actually configured on the target repo.)
- **Free-form:** Type `true` or `false` directly; any other value is rejected and re-prompted.

Current value shown to the operator: the existing config's value for this key if `backlog/config/devbench.yaml` already exists, otherwise the Recommended value above.

#### `git_ops.pr_review_resolution.agents` -- PR review-bot allowlist

Allowlist of GitHub login names whose unresolved review comments block the merge. Empty by default; populate when review bots are configured on the target repo. Overridable via the `DEVBENCH_PR_REVIEW_AGENTS` env var.

- **Recommended:** `[] (empty)` -- matches the disabled default of the master toggle above.
- **Alternatives:** `['github-copilot[bot]', 'amazon-q-developer[bot]']` (blocks the merge on unresolved comments from exactly these bot logins.)
- **Free-form:** Enter a comma-separated list of GitHub login names, or leave blank.

Current value shown to the operator: the existing config's value for this key if `backlog/config/devbench.yaml` already exists, otherwise the Recommended value above.

#### `git_ops.pr_review_resolution.decision_blocks` -- Review-decision hard block

When true, GitHub's `reviewDecision == CHANGES_REQUESTED` hard-blocks the merge regardless of the bot allowlist above. Overridable via the `DEVBENCH_PR_REVIEW_DECISION_BLOCKS` env var.

- **Recommended:** `true` -- treats a formal CHANGES_REQUESTED review as an unconditional merge blocker, matching standard GitHub review semantics.
- **Alternatives:** `false` (allows a merge to proceed even with an open CHANGES_REQUESTED review, relying only on the named-bot allowlist.)
- **Free-form:** Type `true` or `false` directly; any other value is rejected and re-prompted.

Current value shown to the operator: the existing config's value for this key if `backlog/config/devbench.yaml` already exists, otherwise the Recommended value above.

#### `git_ops.pr_review_resolution.settle_seconds` -- Review poll settle window

Total settle-window length in seconds for the PR review poll loop, giving asynchronous bots time to post their comments before `git-ops` proceeds. Overridable via the `DEVBENCH_PR_REVIEW_SETTLE_SECONDS` env var.

- **Recommended:** `60` -- gives most review bots enough time to post without meaningfully slowing down the merge.
- **Alternatives:** `120` (widens the window for slower review bots.); `30` (shortens the window when the configured bots respond quickly.)
- **Free-form:** Enter any positive integer (seconds); non-positive or non-integer values are rejected and re-prompted.

Current value shown to the operator: the existing config's value for this key if `backlog/config/devbench.yaml` already exists, otherwise the Recommended value above.

#### `git_ops.pr_review_resolution.poll_interval` -- Review poll cadence

Per-poll cadence in seconds inside the settle window above. Overridable via the `DEVBENCH_PR_REVIEW_POLL_INTERVAL` env var.

- **Recommended:** `5` -- polls frequently enough to catch a bot comment without excessive API calls.
- **Alternatives:** `10` (reduces API call volume at the cost of slightly coarser polling.)
- **Free-form:** Enter any positive integer (seconds); non-positive or non-integer values are rejected and re-prompted.

Current value shown to the operator: the existing config's value for this key if `backlog/config/devbench.yaml` already exists, otherwise the Recommended value above.

#### `git_ops.isolate_worktrees` -- Per-unit git worktrees

Claim each work unit into its own git worktree beside the primary checkout instead of sharing one working tree. Two units that never share a tree never collide, so an interrupted unit's uncommitted work is not something the next claim has to displace into quarantine. Mutually exclusive with `single_branch`.

- **Recommended:** `false` -- matches the built-in default and the shared-tree model most workspaces expect.
- **Alternatives:** `true` (isolates every claim, at the cost of one worktree per in-flight unit; do not combine with `single_branch`.)
- **Free-form:** Enter `true` or `false`; any other value is rejected and re-prompted.

Current value shown to the operator: the existing config's value for this key if `backlog/config/devbench.yaml` already exists, otherwise the Recommended value above.

> "Section: git_ops
>
>   single_branch      -- Use one branch for all work units (blank = per-unit backlog/<id> branches).
>                         Example: feat/my-feature
>   defer_pr           -- When true, git-ops commits locally only; PR opens via git-ops-finalize.
>                         Required when single_branch is set. [true/false, default: false]
>   auto_finalize      -- When true, orchestrate skill pushes the branch + opens PR automatically
>                         once all work units are terminal. Requires defer_pr: true. [true/false, default: false]
>   auto_merge         -- When true, orchestrate skill merges the PR once CI is green.
>                         Requires auto_finalize: true AND defer_pr: true. [true/false, default: false]
>   pause_before_merge -- When true, work units transition to 'in-review' after CI passes;
>                         the operator manually merges. Mutually exclusive with defer_pr. [true/false, default: false]
>   update_submodule   -- Update parent repo's submodule reference after each PR merge.
>                         Use only when target repos are git submodules. [true/false, default: false]
>   inline_orphan_cleanup -- Run orphan-path cleanup as a chore commit before the task commit.
>                            [true/false, default: true]
>   orphan_patterns    -- fnmatch globs identifying build/state artifacts to untrack.
>                         REPLACES the built-in list wholesale when non-empty (the env var
>                         DEVBENCH_ORPHAN_IGNORE_PATTERNS wins over it), so a workspace that
>                         sets it owns the complete set and a devbench upgrade cannot
>                         reintroduce a pattern it removed on purpose. Dependency LOCK files
>                         (uv.lock, package-lock.json, .terraform.lock.hcl, Chart.lock) are
>                         deliberately absent from the built-in list -- they pin versions and
>                         belong in git. [list of globs, default: [] = built-in list]
>   ci_failure_retry   -- Return rc=2 on CI failure to trigger an executor retry. [true/false, default: true]
>   local_only         -- Target repos have no origin remote; never push or create PRs.
>                         Requires defer_pr: true. [true/false, default: false]"

Validate incompatible combinations:
- `defer_pr: true` requires `single_branch` to be set (`single_branch` alone, without `defer_pr`, is valid). Reject with:
  > "[INVALID] git_ops.defer_pr requires git_ops.single_branch to be set. Re-enter."
- `pause_before_merge: true` is incompatible with `defer_pr: true` and `single_branch`. Reject with:
  > "[INVALID] pause_before_merge: true is mutually exclusive with defer_pr: true and single_branch. Re-enter."
- `auto_finalize: true` requires `defer_pr: true`. Reject with:
  > "[INVALID] auto_finalize: true requires defer_pr: true. Re-enter."
- `auto_finalize: true` is incompatible with `local_only: true` (local-only repos have no remote to push to; `git-ops-finalize` cannot create a PR). Reject with:
  > "[INVALID] auto_finalize: true is incompatible with local_only: true. Re-enter."
- `auto_merge: true` requires `auto_finalize: true`. Reject with:
  > "[INVALID] auto_merge: true requires auto_finalize: true AND defer_pr: true. Re-enter."
- `local_only: true` requires `defer_pr: true`. Reject with:
  > "[INVALID] local_only: true requires defer_pr: true. Re-enter."
- `local_only: true` requires every `repos:` entry to set an explicit `default_branch` (there is no `origin` to fall back to in local-only mode). Reject with:
  > "[INVALID] local_only: true requires every entry in repos: to set an explicit default_branch. Re-enter."

---

## Step 8 -- task_factory section

On by default (ADR-32). Ask the operator:

> "Section: task_factory (on by default, ADR-32)
>
>   enabled               -- Run the blocker-resolver + task-factory loop after amendment rejects.
>                            Requires manifest_amendment.enabled: true. [true/false, default: true]
>   auto_accept_proposals -- Two auto-promote paths. (1) write-proposal: synchronously
>                            materialises (and promotes any legacy 'proposed'-status draft)
>                            in the same call, instead of waiting for the next sweep-proposals
>                            tick. (2) sweep-proposals: also auto-promotes any orphaned draft
>                            explicitly left at status 'proposed' (legacy/hand-edited drafts
>                            only -- freshly materialised drafts always use
>                            backlog.default_status_for_new_work_units, default 'in-queue',
>                            regardless of this flag). [true/false, default: false]; false
>                            defers both to an explicit promote-proposal/reject-proposal
>                            decision. Only takes effect when enabled: true."

#### `task_factory.enabled` -- Task-factory loop toggle

Whether the blocker-resolver + task-factory loop runs after an amendment reject to materialise new draft work-unit files for out-of-scope production fixes. Requires `manifest_amendment.enabled: true` (ADR-32, on by default).

- **Recommended:** `true` -- keeps the self-healing backlog loop on by default, matching ADR-32.
- **Alternatives:** `false` (opts the workspace out of automatic task materialisation after an amendment reject; out-of-scope fixes then require manual follow-up.)
- **Free-form:** Type `true` or `false` directly; any other value is rejected and re-prompted.

Current value shown to the operator: the existing config's value for this key if `backlog/config/devbench.yaml` already exists, otherwise the Recommended value above.

#### `task_factory.auto_accept_proposals` -- Auto-promote proposals

Governs two auto-promote paths: (1) `write-proposal` synchronously materialises drafts in the same call instead of waiting for the next `sweep-proposals` tick; (2) `sweep-proposals` also auto-promotes any draft explicitly left at legacy status `proposed`. Only takes effect when `task_factory.enabled: true`.

- **Recommended:** `false` -- defers both paths to an explicit `promote-proposal`/`reject-proposal` human decision (ADR-32).
- **Alternatives:** `true` (auto-promotes newly materialised and legacy-proposed drafts without a human review step.)
- **Free-form:** Type `true` or `false` directly; any other value is rejected and re-prompted.

Current value shown to the operator: the existing config's value for this key if `backlog/config/devbench.yaml` already exists, otherwise the Recommended value above.

---

## Step 9 -- manifest_amendment section

#### `manifest_amendment.enabled` -- Amendment workflow toggle

Whether the Changes Manifest amendment workflow is active for this backlog. When enabled, an executor that discovers a required production fix during TDD GREEN can request an amendment reviewed by a dedicated judge.

- **Recommended:** `true` -- lets the executor request a reviewed, audited manifest expansion instead of being blocked outright.
- **Alternatives:** `false` (disables the amendment workflow entirely; any out-of-manifest fix the executor finds must be escalated instead of requested.)
- **Free-form:** Type `true` or `false` directly; any other value is rejected and re-prompted.

Current value shown to the operator: the existing config's value for this key if `backlog/config/devbench.yaml` already exists, otherwise the Recommended value above.

#### `manifest_amendment.allowed_reasons` -- Accepted amendment reasons

List of amendment reasons this backlog accepts. Amendment requests whose reason is not in this list are rejected by the Layer 1 pre-filter. `doc_sync_review_fix` is restricted to documentation (`.md`) or documentation-pinning test paths.

- **Recommended:** `[tdd_green_production_fix, doc_sync_review_fix]` -- covers both the TDD-discovered production-fix path and the doc-sync review-feedback path this repo actually uses.
- **Alternatives:** `[tdd_green_production_fix]` (narrows the workflow to only TDD-discovered production fixes, rejecting doc-sync amendment requests.)
- **Free-form:** Enter a comma-separated list drawn from `tdd_green_production_fix` and `doc_sync_review_fix`, or leave blank for the default.

Current value shown to the operator: the existing config's value for this key if `backlog/config/devbench.yaml` already exists, otherwise the Recommended value above.

#### `manifest_amendment.max_requests_per_execution` -- Amendment request cap

Maximum amendments applied to one task during one executor run. Prevents amendment loops.

- **Recommended:** `1` -- bounds a single executor run to one manifest expansion, which is enough for the common case and prevents runaway loops.
- **Alternatives:** `2` (allows a second amendment in the same run for a task that legitimately needs two separate out-of-manifest fixes.)
- **Free-form:** Enter any integer >= 1; non-integers or values below 1 are rejected and re-prompted.

Current value shown to the operator: the existing config's value for this key if `backlog/config/devbench.yaml` already exists, otherwise the Recommended value above.
> "Section: manifest_amendment
>
>   enabled                    -- Enable the Changes Manifest amendment workflow. [true/false, default: true]
>   allowed_reasons            -- List of amendment reasons accepted by the pre-filter.
>                                 Default: [tdd_green_production_fix]
>                                 (Enter comma-separated values, or leave blank for the default.)
>   max_requests_per_execution -- Max amendments applied to one task per executor run. [integer >= 1, default: 2 -- one addition plus one row removal so a unit can satisfy AC-FINAL-015 in both directions within a single run]"

---

## Step 10 -- validate section

Toggles for additional validate-backlog rules. Existing rules (1-19) run unconditionally; the rule listed here is individually toggleable.

#### `validate.check_orphan_path_tokens` -- Rule 20: orphan path-token scan

When true, cross-checks Acceptance Criteria and Definition of Done sections of every Task against the Changes Manifest; backtick-quoted path-shaped tokens absent from the Manifest (and not marked `(ref)`) emit an integrity error.

- **Recommended:** `true` -- catches spec drift where AC/DoD prose restates a path that disagrees with the Manifest.
- **Alternatives:** `false` (opts a pre-existing backlog out of Rule 20 when its AC/DoD prose is not yet compatible with the rule.)
- **Free-form:** Type `true` or `false` directly; any other value is rejected and re-prompted.

Current value shown to the operator: the existing config's value for this key if `backlog/config/devbench.yaml` already exists, otherwise the Recommended value above.

#### `validate.production_source_paths` -- Production-source path prefixes

Path prefixes this workspace treats as production source for the task-type invariant (rule 21) and source-test atomicity (rule 14). Absent preserves the built-in prefixes `src/` and `infra/scripts/` plus any nested `/src/` segment. Set this when a repository keeps tested production modules elsewhere.

- **Recommended:** Leave absent -- the built-in prefixes cover the conventional layout.
- **Alternatives:** `['src/', 'lib/']` (declares an additional top-level production tree.)
- **Free-form:** Enter a YAML list of path-prefix strings, or `skip` to leave the key absent and keep the built-in prefixes.

Current value shown to the operator: the existing config's value for this key if `backlog/config/devbench.yaml` already exists, otherwise the Recommended value above.

#### `validate.production_source_extensions` -- Production-source file extensions

File extensions this workspace treats as production source, consumed together with `production_source_paths` by the task-type invariant (rule 21) and source-test atomicity (rule 14). Absent keeps the built-in Python-only behaviour. Declare it when behaviour lives in non-Python artefacts.

- **Recommended:** Leave absent -- the built-in Python-only behaviour matches a Python workspace.
- **Alternatives:** `['.py', '.tf']` (treats Terraform as production source alongside Python.)
- **Free-form:** Enter a YAML list of extension strings including the leading dot, or `skip` to leave the key absent.

Current value shown to the operator: the existing config's value for this key if `backlog/config/devbench.yaml` already exists, otherwise the Recommended value above.


---

## Step 11 -- stop_hook section

Circuit breaker settings.

#### `stop_hook.max_blocks` -- Circuit-breaker block cap

Maximum consecutive stop-hook blocks before the circuit breaker trips and allows the stop, preventing an infinite block loop. Overridable via the `DEVBENCH_STOP_MAX_BLOCKS` env var.

- **Recommended:** `5` -- matches the built-in default, tuned to distinguish a genuinely stuck session from normal back-and-forth.
- **Alternatives:** `10` (tolerates more consecutive blocks before tripping, for a workspace with legitimately longer back-and-forth sessions.)
- **Free-form:** Enter any integer >= 1; non-integers or values below 1 are rejected and re-prompted.

Current value shown to the operator: the existing config's value for this key if `backlog/config/devbench.yaml` already exists, otherwise the Recommended value above.

#### `stop_hook.window_seconds` -- Circuit-breaker time window

Time window in seconds for counting stop-hook blocks; the counter resets after this period. Also doubles as the threshold for `devbench report`'s orchestrator-alive banner: a log line within this many seconds renders ALIVE, past it renders STOPPED. Overridable via the `DEVBENCH_STOP_WINDOW_SECONDS` env var.

- **Recommended:** `180` -- matches the built-in default and keeps the report banner aligned with the circuit breaker automatically.
- **Alternatives:** `300` (widens the quiet-window tolerance for a workload with longer natural pauses between log lines.)
- **Free-form:** Enter any integer >= 10; smaller values or non-integers are rejected and re-prompted.

Current value shown to the operator: the existing config's value for this key if `backlog/config/devbench.yaml` already exists, otherwise the Recommended value above.

#### `stop_hook.stale_task_minutes` -- Stale in-progress threshold

Minutes before an in-progress task is considered stale; the hook warns instead of blindly blocking once a task crosses this threshold. Overridable via the `DEVBENCH_STOP_STALE_MINUTES` env var.

- **Recommended:** `120` -- matches the built-in default, tuned for typical task durations.
- **Alternatives:** `60` (flags staleness sooner for a workspace with shorter expected task durations.)
- **Free-form:** Enter any integer >= 1; non-integers or values below 1 are rejected and re-prompted.

Current value shown to the operator: the existing config's value for this key if `backlog/config/devbench.yaml` already exists, otherwise the Recommended value above.

---

## Step 12 -- hook_tail section

`devbench hook-tail` column-cap settings (issue #134).

#### `hook_tail.agent_width` -- Agent-name column width

Column width for the agent name in `devbench hook-tail` output. Overridable via the `DEVBENCH_HOOK_TAIL_AGENT_WIDTH` env var.

- **Recommended:** `12` -- fits the longest built-in agent names without excessive padding.
- **Alternatives:** `16` (widens the column for a workspace with longer custom agent names.)
- **Free-form:** Enter any integer >= 1; non-positive or non-integer values are rejected and re-prompted.

Current value shown to the operator: the existing config's value for this key if `backlog/config/devbench.yaml` already exists, otherwise the Recommended value above.

#### `hook_tail.tool_width` -- Tool-name column width

Column width for the tool name in `devbench hook-tail` output. Overridable via the `DEVBENCH_HOOK_TAIL_TOOL_WIDTH` env var.

- **Recommended:** `8` -- fits the longest built-in tool names without excessive padding.
- **Alternatives:** `10` (widens the column for longer custom tool names.)
- **Free-form:** Enter any integer >= 1; non-positive or non-integer values are rejected and re-prompted.

Current value shown to the operator: the existing config's value for this key if `backlog/config/devbench.yaml` already exists, otherwise the Recommended value above.

#### `hook_tail.description_max` -- Description column max chars

Max characters for the description column in `devbench hook-tail` output. Overridable via the `DEVBENCH_HOOK_TAIL_DESCRIPTION_MAX` env var.

- **Recommended:** `120` -- balances readability against terminal width for the common case.
- **Alternatives:** `200` (shows more of each description on a wide terminal.)
- **Free-form:** Enter any integer >= 1; non-positive or non-integer values are rejected and re-prompted.

Current value shown to the operator: the existing config's value for this key if `backlog/config/devbench.yaml` already exists, otherwise the Recommended value above.

#### `hook_tail.stdout_preview_max` -- Result-preview column max chars

Max characters for the result-preview column after the pipe in `devbench hook-tail` output. Overridable via the `DEVBENCH_HOOK_TAIL_STDOUT_PREVIEW_MAX` env var.

- **Recommended:** `80` -- keeps the preview readable on a standard terminal width.
- **Alternatives:** `120` (shows more of each result preview on a wide terminal.)
- **Free-form:** Enter any integer >= 1; non-positive or non-integer values are rejected and re-prompted.

Current value shown to the operator: the existing config's value for this key if `backlog/config/devbench.yaml` already exists, otherwise the Recommended value above.

---

## Step 13 -- orchestrate section

Orchestrator runtime tuning (issue #144).

#### `orchestrate.max_cascade_depth` -- Recovery-cascade depth cap

Cap on recovery-of-a-recovery cascade depth (issue #144). When a proposal would land at depth >= this cap, the source task transitions to `NEEDS_OPERATOR_ATTENTION` instead of materialising another recovery layer. Overridable via the `DEVBENCH_ORCHESTRATE_MAX_CASCADE_DEPTH` env var.

- **Recommended:** `2` -- matches the built-in default, tuned to catch a genuinely runaway recovery cascade without flagging normal one-level recoveries.
- **Alternatives:** `3` (tolerates one additional recovery layer before escalating to a human.)
- **Free-form:** Enter any integer >= 1; non-integers or values below 1 are rejected and re-prompted.

Current value shown to the operator: the existing config's value for this key if `backlog/config/devbench.yaml` already exists, otherwise the Recommended value above.

#### `orchestrate.max_transport_restarts` -- SDK transport-restart cap

Cap on consecutive in-process restarts after an SDK transport error. Unlike a quota window or an inactivity timeout, a transport fault imposes no natural delay, so this bound is deliberately low and separate from the quota ceiling: exhausting it means the transport is down rather than flapping. Sized as a time budget -- with the backoff defaults below, 14 restarts is roughly an hour before the run halts. Overridable via the `DEVBENCH_MAX_TRANSPORT_RESTARTS` env var.

- **Recommended:** `14` -- matches the built-in default, giving about an hour of riding out a provider outage.
- **Alternatives:** `20` (extends the window for a provider known to have long outages.)
- **Free-form:** Enter any integer >= 1; non-integers or values below 1 are rejected and re-prompted.

Current value shown to the operator: the existing config's value for this key if `backlog/config/devbench.yaml` already exists, otherwise the Recommended value above.

#### `orchestrate.transport_restart_backoff_base_seconds` -- First transport-restart delay

First delay, in seconds, before the initial transport restart. Each subsequent restart doubles it (`base * 2 ** restarts_used`) up to the ceiling below. Without this envelope the restart cap above is spent as fast as the SDK can reject a session. Overridable via the `DEVBENCH_TRANSPORT_RESTART_BACKOFF_BASE_SECONDS` env var.

- **Recommended:** `1.0` -- matches the built-in default; recovers from a momentary fault without a perceptible stall.
- **Alternatives:** `2.0` (backs off faster on a link known to be flaky.)
- **Free-form:** Enter any number greater than 0 (seconds); zero or negative values are rejected and re-prompted.

Current value shown to the operator: the existing config's value for this key if `backlog/config/devbench.yaml` already exists, otherwise the Recommended value above.

#### `orchestrate.transport_restart_backoff_max_seconds` -- Transport-restart delay ceiling

Ceiling, in seconds, on the exponential transport-restart delay. Must be >= `transport_restart_backoff_base_seconds`. The ceiling also bounds how long an in-flight wait can delay a `devbench stop`. Overridable via the `DEVBENCH_TRANSPORT_RESTART_BACKOFF_MAX_SECONDS` env var.

- **Recommended:** `60.0` -- matches the built-in default; settles a long outage into a steady one-minute retry cadence.
- **Alternatives:** `120.0` (halves the retry volume during a long outage, at the cost of a less responsive stop.)
- **Free-form:** Enter any number greater than 0 (seconds) that is at least the base above; smaller or non-positive values are rejected and re-prompted.

Current value shown to the operator: the existing config's value for this key if `backlog/config/devbench.yaml` already exists, otherwise the Recommended value above.

#### `orchestrate.effort` -- Reasoning effort

Reasoning effort for the orchestrator SDK session and every agent it spawns. Left unset the session inherits the ambient Claude Code effort, so an unattended run's cost profile depends on whatever the operator's last interactive session happened to use. Pinning it makes the run reproducible.

- **Recommended:** `high` -- matches the built-in default and the effort the judge prompts were written against.
- **Alternatives:** `medium` (cheaper per turn, at the cost of shallower review reasoning); `xhigh` or `max` (deeper reasoning, materially higher cost per turn.)
- **Free-form:** Enter one of `low`, `medium`, `high`, `xhigh`, `max`; any other value is rejected and re-prompted.

Current value shown to the operator: the existing config's value for this key if `backlog/config/devbench.yaml` already exists, otherwise the Recommended value above.

#### `orchestrate.max_thinking_tokens` -- Per-turn thinking budget

Ceiling on how many tokens one turn may spend reasoning. A turn that reasons for longer than the prompt-cache lifetime returns to a cold cache, so the whole prompt is re-uploaded and re-cached rather than read back, and the run reaches its quota limit sooner.

- **Recommended:** `16000` -- matches the built-in default, tuned to stay inside the prompt-cache lifetime.
- **Alternatives:** `32000` (allows deeper single-turn reasoning; expect more cold-cache re-uploads.)
- **Free-form:** Enter any integer >= 1; non-integers or values below 1 are rejected and re-prompted.

Current value shown to the operator: the existing config's value for this key if `backlog/config/devbench.yaml` already exists, otherwise the Recommended value above.


---

## Step 14 -- debug section

Diagnostic-tuning knobs. Set these only when investigating a specific orchestrator-cadence problem; production workspaces leave this whole section absent. Ask the operator first:

> "Section: debug (diagnostic knobs; leave the whole section absent for production workspaces -- enter 'skip' to omit it entirely)"

If the operator skips, omit the `debug:` key entirely rather than writing it with default values.

#### `debug.check_registration_retries` -- Check-registration retry count

Number of times `wait_for_checks` retries `gh pr checks` when 'no checks reported' contradicts the local workflow-file glob (issue #114 race-defence). Set only when investigating a specific check-registration race; leave the whole `debug:` section absent for production workspaces. Overridable via the `DEVBENCH_CHECK_REGISTRATION_RETRIES` env var.

- **Recommended:** `12` -- matches the built-in default, tuned for typical GitHub Actions check-registration latency.
- **Alternatives:** `20` (tolerates a slower-registering CI provider.)
- **Free-form:** Enter any integer >= 1; non-integers or values below 1 are rejected and re-prompted.

Current value shown to the operator: the existing config's value for this key if `backlog/config/devbench.yaml` already exists, otherwise the Recommended value above.

#### `debug.check_registration_delay_seconds` -- Check-registration retry delay

Delay in seconds between check-registration retries above. Overridable via the `DEVBENCH_CHECK_REGISTRATION_DELAY_SECONDS` env var.

- **Recommended:** `5` -- matches the built-in default.
- **Alternatives:** `10` (widens the delay for a slower-registering CI provider.)
- **Free-form:** Enter any integer >= 1; non-integers or values below 1 are rejected and re-prompted.

Current value shown to the operator: the existing config's value for this key if `backlog/config/devbench.yaml` already exists, otherwise the Recommended value above.

#### `debug.blocked_recovery_window_seconds` -- Blocked-recovery recency window

Recency cap for the AWAITING_AUTO_RECOVERY signal in the 3-state blocked-task classifier. Tasks whose most recent `[BLOCKED]` audit-comment timestamp is older than this window fall through to `NEEDS_OPERATOR_ATTENTION`. Overridable via the `DEVBENCH_BLOCKED_RECOVERY_WINDOW_SECONDS` env var.

- **Recommended:** `1800` -- matches the built-in default, tuned to distinguish an in-flight auto-recovery from a genuinely stuck task.
- **Alternatives:** `3600` (widens the window for a workload with slower recovery cascades.)
- **Free-form:** Enter any integer >= 1; non-integers or values below 1 are rejected and re-prompted.

Current value shown to the operator: the existing config's value for this key if `backlog/config/devbench.yaml` already exists, otherwise the Recommended value above.

---

## Step 15 -- backlog section (issue #189, issue #194)

Backlog lifecycle settings.

#### `backlog.default_status_for_new_work_units` -- New work-unit default status

Lifecycle status written into the `## Status:` line of every newly created work-unit file (issue #189).

- **Recommended:** `in-queue` -- matches legacy behaviour: new tasks are picked up by the orchestrator immediately.
- **Alternatives:** `draft` (requires explicit human promotion (`devbench promote`) before the orchestrator picks up a newly generated task.)
- **Free-form:** Type `in-queue` or `draft` directly; any other value is rejected and re-prompted.

Current value shown to the operator: the existing config's value for this key if `backlog/config/devbench.yaml` already exists, otherwise the Recommended value above.

#### `backlog.bulk_update_confirm_threshold` -- Bulk-update confirmation threshold

Number of work units above which `devbench set-status` with selector flags prompts for confirmation before applying a bulk status change (issue #194). Zero means always prompt.

- **Recommended:** `10` -- lets small, deliberate bulk updates proceed without friction while still guarding larger ones.
- **Alternatives:** `0` (always prompts for confirmation, regardless of how many work units are affected.)
- **Free-form:** Enter any non-negative integer; negative or non-integer values are rejected and re-prompted.

Current value shown to the operator: the existing config's value for this key if `backlog/config/devbench.yaml` already exists, otherwise the Recommended value above.

#### `backlog.bulk_update_audit_path` -- Bulk-update audit log path

Workspace-relative path to the file where bulk-update audit rows are appended (issue #194).

- **Recommended:** `logs/bulk-updates.log` -- matches the built-in default location alongside the other logs.
- **Alternatives:** `logs/backlog-a-bulk-updates.log` (disambiguates the audit log when multiple workspaces share a parent directory.)
- **Free-form:** Enter any workspace-relative path, or leave blank to keep the built-in default.

Current value shown to the operator: the existing config's value for this key if `backlog/config/devbench.yaml` already exists, otherwise the Recommended value above.

Validate `default_status_for_new_work_units` is exactly `in-queue` or `draft`. Reject anything else with:

> "[INVALID] default_status_for_new_work_units must be 'in-queue' or 'draft'. Re-enter."

---

## Step 16 -- gates section (spec section 4.1; caylent-solutions/devbench-internal-backlog#10..#17)

Unified opt-in configuration for the eight integration-reality gates. Every gate is disabled by default at the built-in level (D-17); each block below is individually enabled and carries its own tunables, each with a built-in default. `additionalProperties: false` at every level means an unrecognised key is a load-time error rather than a silently ignored key (D-2).

Ask the operator once, up front:

> "Section: gates (integration-reality gates; every gate is OFF by default -- enter 'skip' to omit the whole gates: block, or walk through each gate below to opt in)"

#### `gates.reachability.enabled` -- Enable the check-reachability gate

Enables the check-reachability gate for this workspace (caylent-solutions/devbench-internal-backlog#10). Every gate is disabled by default at the built-in level (D-17); this is the per-gate opt-in.

Effective value at gate-check time resolves as: `DEVBENCH_GATE_REACHABILITY_ENABLED` env var > per-repo `gates.repos.<org/repo>.reachability.enabled` > this value > built-in `false`.

- **Recommended:** `false` -- matches D-17's every-gate-disabled-by-default posture until the workspace is ready to adopt the gate.
- **Alternatives:** `true` (enables the reachability gate for every repo unless overridden per-repo under gates.repos.<org/repo>.reachability.enabled.)
- **Free-form:** Type `true` or `false` directly; any other value is rejected by the schema boolean check and re-prompted.

Current value shown to the operator: the existing config's value for this key if `backlog/config/devbench.yaml` already exists, otherwise the Recommended value above.

#### `gates.reachability.entry_points` -- Reachability walk entry points

Repo-relative paths seeding the transitive reachability walk (caylent-solutions/devbench-internal-backlog#10 AC2): a referrer only clears an orphan candidate when the referrer is itself reachable from one of these entry points. Absent or empty defaults to the built-in `source_classification`-derived entry-point stem set (`main`, `app`, `index`, `__init__`, `setup`, `conftest`, `wsgi`, `asgi`), matched against each candidate importer's own basename stem -- so leaving this unset still walks a non-empty, repo-agnostic graph instead of an always-empty one.

- **Recommended:** leave unset (empty) -- inherits the built-in entry-point-stem default, which recognises conventional composition roots (`main.py`, `App.tsx`, `index.ts`, ...) by name across any target repo.
- **Alternatives:** a list of explicit repo-relative paths (e.g. `["src/index.ts", "cmd/server/main.go"]`) to seed the walk from this workspace's actual composition roots instead of the generic stem convention; each configured path must exist in the repo checkout or `check-reachability` fails loudly naming the missing path.
- **Free-form:** enter a comma-separated list of repo-relative paths, or leave blank to keep the built-in default. Each entry must be a non-empty string that is genuinely repo-relative; the schema and loader reject a scalar value, a non-string element, an empty-string element, an absolute path, or a path containing a `..` segment.

Current value shown to the operator: the existing config's value for this key if `backlog/config/devbench.yaml` already exists, otherwise the Recommended value above.

#### `gates.ancestry.enabled` -- Enable the check-ancestry gate

Enables the check-ancestry gate for this workspace (caylent-solutions/devbench-internal-backlog#12). Every gate is disabled by default at the built-in level (D-17); this is the per-gate opt-in.

Effective value at gate-check time resolves as: `DEVBENCH_GATE_ANCESTRY_ENABLED` env var > per-repo `gates.repos.<org/repo>.ancestry.enabled` > this value > built-in `false`.

- **Recommended:** `false` -- matches D-17's every-gate-disabled-by-default posture until the workspace is ready to adopt the gate.
- **Alternatives:** `true` (enables the ancestry gate for every repo unless overridden per-repo under gates.repos.<org/repo>.ancestry.enabled.)
- **Free-form:** Type `true` or `false` directly; any other value is rejected by the schema boolean check and re-prompted.

Current value shown to the operator: the existing config's value for this key if `backlog/config/devbench.yaml` already exists, otherwise the Recommended value above.

#### `gates.shared_file_impact.enabled` -- Enable the check-shared-file-impact gate

Enables the check-shared-file-impact gate for this workspace (caylent-solutions/devbench-internal-backlog#13). Every gate is disabled by default at the built-in level (D-17); this is the per-gate opt-in.

Effective value at gate-check time resolves as: `DEVBENCH_GATE_SHARED_FILE_IMPACT_ENABLED` env var > per-repo `gates.repos.<org/repo>.shared_file_impact.enabled` > this value > built-in `false`.

- **Recommended:** `false` -- matches D-17's every-gate-disabled-by-default posture until the workspace is ready to adopt the gate.
- **Alternatives:** `true` (enables the shared_file_impact gate for every repo unless overridden per-repo under gates.repos.<org/repo>.shared_file_impact.enabled.)
- **Free-form:** Type `true` or `false` directly; any other value is rejected by the schema boolean check and re-prompted.

Current value shown to the operator: the existing config's value for this key if `backlog/config/devbench.yaml` already exists, otherwise the Recommended value above.

#### `gates.fixture_consistency.enabled` -- Enable the check-fixture-consistency gate

Enables the check-fixture-consistency gate for this workspace (caylent-solutions/devbench-internal-backlog#17). Every gate is disabled by default at the built-in level (D-17); this is the per-gate opt-in.

Effective value at gate-check time resolves as: `DEVBENCH_GATE_FIXTURE_CONSISTENCY_ENABLED` env var > per-repo `gates.repos.<org/repo>.fixture_consistency.enabled` > this value > built-in `false`.

- **Recommended:** `false` -- matches D-17's every-gate-disabled-by-default posture until the workspace is ready to adopt the gate.
- **Alternatives:** `true` (enables the fixture_consistency gate for every repo unless overridden per-repo under gates.repos.<org/repo>.fixture_consistency.enabled.)
- **Free-form:** Type `true` or `false` directly; any other value is rejected by the schema boolean check and re-prompted.

Current value shown to the operator: the existing config's value for this key if `backlog/config/devbench.yaml` already exists, otherwise the Recommended value above.

#### `gates.write_path_audit.enabled` -- Enable the write-path-audit gate

Enables the write-path-audit gate for this workspace (caylent-solutions/devbench-internal-backlog#16). Every gate is disabled by default at the built-in level (D-17); this is the per-gate opt-in.

Effective value at gate-check time resolves as: `DEVBENCH_GATE_WRITE_PATH_AUDIT_ENABLED` env var > per-repo `gates.repos.<org/repo>.write_path_audit.enabled` > this value > built-in `false`.

- **Recommended:** `false` -- matches D-17's every-gate-disabled-by-default posture until the workspace is ready to adopt the gate.
- **Alternatives:** `true` (enables the write_path_audit gate for every repo unless overridden per-repo under gates.repos.<org/repo>.write_path_audit.enabled.)
- **Free-form:** Type `true` or `false` directly; any other value is rejected by the schema boolean check and re-prompted.

Current value shown to the operator: the existing config's value for this key if `backlog/config/devbench.yaml` already exists, otherwise the Recommended value above.

#### `gates.newly_reachable_paths.enabled` -- Enable the newly-reachable-paths gate

Enables the newly-reachable-paths gate for this workspace (caylent-solutions/devbench-internal-backlog#15). Every gate is disabled by default at the built-in level (D-17); this is the per-gate opt-in.

Effective value at gate-check time resolves as: `DEVBENCH_GATE_NEWLY_REACHABLE_PATHS_ENABLED` env var > per-repo `gates.repos.<org/repo>.newly_reachable_paths.enabled` > this value > built-in `false`.

- **Recommended:** `false` -- matches D-17's every-gate-disabled-by-default posture until the workspace is ready to adopt the gate.
- **Alternatives:** `true` (enables the newly_reachable_paths gate for every repo unless overridden per-repo under gates.repos.<org/repo>.newly_reachable_paths.enabled.)
- **Free-form:** Type `true` or `false` directly; any other value is rejected by the schema boolean check and re-prompted.

Current value shown to the operator: the existing config's value for this key if `backlog/config/devbench.yaml` already exists, otherwise the Recommended value above.

#### `gates.composition_root.enabled` -- Enable the composition-root gate

Enables the composition-root gate for this workspace (caylent-solutions/devbench-internal-backlog#11). Every gate is disabled by default at the built-in level (D-17); this is the per-gate opt-in.

Effective value at gate-check time resolves as: `DEVBENCH_GATE_COMPOSITION_ROOT_ENABLED` env var > per-repo `gates.repos.<org/repo>.composition_root.enabled` > this value > built-in `false`.

- **Recommended:** `false` -- matches D-17's every-gate-disabled-by-default posture until the workspace is ready to adopt the gate.
- **Alternatives:** `true` (enables the composition_root gate for every repo unless overridden per-repo under gates.repos.<org/repo>.composition_root.enabled.)
- **Free-form:** Type `true` or `false` directly; any other value is rejected by the schema boolean check and re-prompted.

Current value shown to the operator: the existing config's value for this key if `backlog/config/devbench.yaml` already exists, otherwise the Recommended value above.

#### `gates.layout_geometry.enabled` -- Enable the layout-geometry gate

Enables the layout-geometry gate for this workspace (caylent-solutions/devbench-internal-backlog#14). Every gate is disabled by default at the built-in level (D-17); this is the per-gate opt-in.

Effective value at gate-check time resolves as: `DEVBENCH_GATE_LAYOUT_GEOMETRY_ENABLED` env var > per-repo `gates.repos.<org/repo>.layout_geometry.enabled` > this value > built-in `false`.

- **Recommended:** `false` -- matches D-17's every-gate-disabled-by-default posture until the workspace is ready to adopt the gate.
- **Alternatives:** `true` (enables the layout_geometry gate for every repo unless overridden per-repo under gates.repos.<org/repo>.layout_geometry.enabled.)
- **Free-form:** Type `true` or `false` directly; any other value is rejected by the schema boolean check and re-prompted.

Current value shown to the operator: the existing config's value for this key if `backlog/config/devbench.yaml` already exists, otherwise the Recommended value above.

#### `gates.shared_file_impact.auto_derive_registry` -- Auto-derive fan-in registry

When true, computes the shared-file set as the files imported/required by more than `fan_in_threshold` distinct modules (spec 4.6, issue #13 AC4), via language-appropriate import scanning. Unioned ADDITIVELY with the hand-maintained per-repo glob list (`gates.repos.<org/repo>.shared_file_impact.patterns`) -- auto-derivation never replaces the hand list.

- **Recommended:** `false` -- D-17 covers both every gate being disabled at the built-in level (`gates.shared_file_impact.enabled`, above) AND the accepted tunable defaults, naming this exact key with this exact value (`auto_derive_registry=false`); this default is D-17's operator selection, not this feature's own choice.
- **Alternatives:** `true` (enables auto-derivation for every repo; combine with `fan_in_threshold` below.)
- **Free-form:** Type `true` or `false` directly; any other value is rejected and re-prompted.

Current value shown to the operator: the existing config's value for this key if `backlog/config/devbench.yaml` already exists, otherwise the Recommended value above.

#### `gates.shared_file_impact.fan_in_threshold` -- Auto-derive fan-in threshold

The distinct-importer fan-in count a file must exceed (strictly greater than, not "at least") for `auto_derive_registry` to include it in the shared-file set. Only consumed when `auto_derive_registry` is true.

- **Recommended:** `3` -- the built-in default, D-17's accepted `fan_in_threshold=3` operator selection.
- **Alternatives:** any integer `>= 1` (a lower value derives more files; a higher value derives fewer.)
- **Free-form:** Type an integer `>= 1` directly; any other value (non-integer, or `< 1`) is rejected by the schema check and re-prompted.

Current value shown to the operator: the existing config's value for this key if `backlog/config/devbench.yaml` already exists, otherwise the Recommended value above.

#### `gates.fixture_consistency.extract_source_literals` -- Literal-extraction scan mode

A second, heuristic scan mode for the fixture-consistency gate (spec 4.7 bullet 4; issue #17
AC-19): when true, `check-fixture-consistency` additionally scans the classified source files
in the repo checkout (enumerated via `source_classification.iter_classified_source_files`, which
prunes a fixed set of dependency/build/vendor directories during the walk, and also excludes any
FILE symlink whose resolved real path falls outside the walked root -- live or dangling; a
symlinked DIRECTORY is never descended into at all) for
an assignment whose key matches a configured `identifier_field`, and resolves a matched literal
against the UNION of every canonical source sharing that `identifier_field` name (canonical
sources declaring the same `identifier_field` are one combined identifier namespace, never
cross-producted against each other): a literal present in ANY member of that group passes, and
one absent from all of them is a `missing_key` finding naming the whole group with a
comma-joined path list and carrying `file:line`. Every extracted value is redacted
UNCONDITIONALLY, regardless of length: the finding prints
`<redacted, N chars total; see file:line above to inspect it directly>` rather than any part of
the value (SECURITY: gate output flows into CI logs and review comments), applied uniformly
regardless of the value's shape. It is a regex-based heuristic, not a parser --
it has no notion of comments
or reachability, does not resolve string interpolation/concatenation (a concatenated or
cross-line-continued value can be flagged on its first quoted chunk, a partial-literal false
positive), and never matches a value spread across more than one physical line via a
triple-quoted string (including a triple-quoted value that fits on one line, and a genuinely
empty string, both of which are simply unmatched rather than misreported) -- so a workspace
enabling it should expect occasional false positives, and has no waiver mechanism available for
a source-literal finding (the in-fixture `allow_missing` marker applies only to the structured
scan-target cross-reference); see `docs/devbench-yaml-reference.md`'s
`gates.fixture_consistency.extract_source_literals` section for the full documented accuracy
bounds.

- **Recommended:** `false` -- matches the shipped default (`constants.GATE_EXTRACT_SOURCE_LITERALS_DEFAULT`); the heuristic can produce false positives, so it stays opt-in.
- **Alternatives:** `true` (enables the source-literal scan mode in addition to the structured JSON/YAML cross-reference mode above.)
- **Free-form:** Type `true` or `false` directly; any other value is rejected and re-prompted.

Current value shown to the operator: the existing config's value for this key if `backlog/config/devbench.yaml` already exists, otherwise the Recommended value above.

### `gates.fixture_consistency.canonical_sources` and `gates.fixture_consistency.scan` (dynamic arrays)

These two array fields are collected through a free-text loop, not a fixed menu, since their length and content are workspace-specific. Devbench cannot infer a target repo's fixture-file layout on its own, so an absent `canonical_sources` makes the check a no-op even when `enabled: true`. Each field's full dotted key template in the assembled config, using the array's positional (not operator-named) `<item>` placeholder, is given alongside its bullet below.

For each `canonical_sources` entry, ask for:
- `path` (full dotted key template: `gates.fixture_consistency.canonical_sources.<item>.path`) -- repo-relative path to the canonical fixture/dataset file (JSON or YAML). **Free-form:** any repo-relative path.
- `identifier_field` (full dotted key template: `gates.fixture_consistency.canonical_sources.<item>.identifier_field`) -- key name within each canonical record whose values other fixtures must reference (e.g. `sku`). **Free-form:** any string key name present in the canonical file's records.
- `expected_count` (optional; full dotted key template: `gates.fixture_consistency.canonical_sources.<item>.expected_count`) -- exact expected number of distinct `identifier_field` values, for asserting full backfill coverage. **Free-form:** any positive integer, or leave blank to skip this check.

For each `scan` entry, ask for:
- `path` (full dotted key template: `gates.fixture_consistency.scan.<item>.path`) -- repo-relative path to the mock/fixture file to scan. **Free-form:** any repo-relative path.
- `identifier_field` (full dotted key template: `gates.fixture_consistency.scan.<item>.identifier_field`) -- key name within each scanned record holding the identifier literal(s). **Free-form:** any string key name.
- `canonical_source` (full dotted key template: `gates.fixture_consistency.scan.<item>.canonical_source`) -- the `path` of the `canonical_sources` entry this scan target checks against. **Free-form:** inferred automatically when exactly one `canonical_sources` entry is configured; required and validated against the configured paths when more than one is configured.

There is no `allow_missing` field to collect here. A deliberate not-found/empty-state edge-case
fixture is waived by attaching a structured `{"allow_missing": {"reason": "<non-empty reason>"}}`
marker directly to the waived record IN the scanned fixture file itself (spec
`integration-reality-gates-hardening.md` 4.7 bullet 5, PM-5's in-diff exception; E6-F1-S1-T2), not
by a config key under `gates.fixture_consistency.scan.<item>`. `gates.fixture_consistency.scan[].allow_missing`
is a REMOVED workspace-config key: a stale value there fails `load_runtime_config` naming the
removed key and the in-fixture replacement above. Do not offer it as a configurable field; tell
the operator to edit the scanned fixture file directly instead. See
`docs/devbench-yaml-reference.md`'s `gates.fixture_consistency` section for the marker's exact
shape.

### `gates.repos` (dynamic per-repo override map)

Optional per-repo override map, field-wise merged OVER the project-level `gates.*` values above (D-15 precedence). Keys must be `org/repo` and must already be present in the top-level `repos:` mapping -- an override naming an unconfigured repo is a load-time error. Ask the operator, per repo already collected in Step 2:

> "Does repo <org/repo> need a gate override that differs from the project-level gates: settings above? If so, which gate(s) (reachability, ancestry, shared_file_impact, fixture_consistency, write_path_audit, newly_reachable_paths, composition_root, layout_geometry) and what enabled value? For shared_file_impact you may also set patterns: a list of fnmatch-style glob patterns (matched against POSIX paths relative to the repo root) identifying shared/high-fan-in composition-root files for this repo -- this is the migrated home of the retired per-repo glob key."

Leave `gates.repos` entirely absent when no repo needs an override.

---

## Step 17 -- skills section

Plugin-skill (spec-to-backlog, create-spec) operator-facing configuration. All fields are optional; absent values fall through to skill-side defaults (issue #221).

#### `skills.exemplar_backlog_path` -- spec-to-backlog exemplar path

Workspace-relative or absolute path to a representative BACKLOG.md the spec-to-backlog skill consults to internalise the project's quality bar. When absent, the skill falls back to the canonical-section list embedded in its prompt.

- **Recommended:** `unset` -- uses the embedded canonical-section list, requiring no extra file to maintain.
- **Alternatives:** `backlog/_exemplars/representative/BACKLOG.md` (points the skill at a richer in-workspace exemplar than the embedded section list.)
- **Free-form:** Enter any workspace-relative or absolute path, or leave blank to use the embedded list.

Current value shown to the operator: the existing config's value for this key if `backlog/config/devbench.yaml` already exists, otherwise the Recommended value above.

#### `skills.exemplar_spec_path` -- create-spec exemplar path

Workspace-relative or absolute path to a representative spec file the create-spec skill consults to internalise the project's quality bar. When absent, the skill falls back to the 16-section structural skeleton embedded in its prompt.

- **Recommended:** `unset` -- uses the embedded 16-section skeleton, requiring no extra file to maintain.
- **Alternatives:** `spec/_exemplars/representative.md` (points the skill at a richer in-workspace exemplar than the embedded skeleton.)
- **Free-form:** Enter any workspace-relative or absolute path, or leave blank to use the embedded skeleton.

Current value shown to the operator: the existing config's value for this key if `backlog/config/devbench.yaml` already exists, otherwise the Recommended value above.

#### `skills.fan_out_threshold` -- spec-to-backlog fan-out threshold

When the Epic decomposition yields more than this many leaf tasks, spec-to-backlog fans the per-task authoring out across one Agent invocation per Feature instead of writing tasks serially.

- **Recommended:** `10` -- matches the built-in default, tuned to balance serial simplicity against fan-out speed.
- **Alternatives:** `20` (keeps authoring serial on a larger workspace before fanning out.); `5` (fans out more aggressively on a workspace that generates many small Features.)
- **Free-form:** Enter any integer >= 1; non-integers or values below 1 are rejected and re-prompted.

Current value shown to the operator: the existing config's value for this key if `backlog/config/devbench.yaml` already exists, otherwise the Recommended value above.

#### `skills.max_iterations` -- Skill self-critique iteration cap

Maximum self-critique iterations per skill invocation. When exceeded, the skill emits a `[SKILL_MAX_ITERATIONS_REACHED]` audit comment with the unresolved rubric items instead of silently shipping a sub-quality artefact.

- **Recommended:** `5` -- matches the built-in default, tuned to converge most rubrics without an unbounded loop.
- **Alternatives:** `8` (allows more self-critique passes for a harder-to-satisfy rubric.)
- **Free-form:** Enter any integer >= 1; non-integers or values below 1 are rejected and re-prompted.

Current value shown to the operator: the existing config's value for this key if `backlog/config/devbench.yaml` already exists, otherwise the Recommended value above.

---

## Step 18 -- notifications section (PR #202)

Operator-facing Slack / webhook pings on lifecycle events. The entire section may be omitted; defaults are all "off". Ask the operator first:

> "Section: notifications (operator lifecycle pings; leave blank to keep them all off)"

#### `notifications.enabled` -- Notifications master switch

Master switch for every operator-facing lifecycle notification. When false, no event fires regardless of the per-event toggles below.

- **Recommended:** `false` -- keeps notifications off until the operator has configured a real Slack webhook.
- **Alternatives:** `true` (enables the notification dispatcher; individual events still need their own toggle set to true to actually fire.)
- **Free-form:** Type `true` or `false` directly; any other value is rejected and re-prompted.

Current value shown to the operator: the existing config's value for this key if `backlog/config/devbench.yaml` already exists, otherwise the Recommended value above.

#### `notifications.timeout_seconds` -- Notification POST timeout

Per-POST HTTP timeout in seconds for every notification endpoint call.

- **Recommended:** `10` -- matches the built-in default, tuned for a typical webhook response time.
- **Alternatives:** `20` (tolerates a slower webhook endpoint.)
- **Free-form:** Enter any number >= 1; non-positive values are rejected and re-prompted.

Current value shown to the operator: the existing config's value for this key if `backlog/config/devbench.yaml` already exists, otherwise the Recommended value above.

#### `notifications.events.work_unit_done` -- Event toggle -- work_unit_done

Fires when a work unit transitions to done. Every event defaults to false so the dispatcher is silent until the operator opts in.

- **Recommended:** `false` -- keeps this event silent until the operator deliberately opts in.
- **Alternatives:** `true` (fires a Slack notification when a work unit transitions to done (subject to the notifications.enabled master switch and notifications.slack.enabled endpoint toggle also being true).)
- **Free-form:** Type `true` or `false` directly; any other value is rejected and re-prompted.

Current value shown to the operator: the existing config's value for this key if `backlog/config/devbench.yaml` already exists, otherwise the Recommended value above.

#### `notifications.events.work_unit_blocked_operator` -- Event toggle -- work_unit_blocked_operator

Fires when the classifier flags a blocked WU as OPERATOR_ACTION_REQUIRED. Every event defaults to false so the dispatcher is silent until the operator opts in.

- **Recommended:** `false` -- keeps this event silent until the operator deliberately opts in.
- **Alternatives:** `true` (fires a Slack notification when the classifier flags a blocked WU as OPERATOR_ACTION_REQUIRED (subject to the notifications.enabled master switch and notifications.slack.enabled endpoint toggle also being true).)
- **Free-form:** Type `true` or `false` directly; any other value is rejected and re-prompted.

Current value shown to the operator: the existing config's value for this key if `backlog/config/devbench.yaml` already exists, otherwise the Recommended value above.

#### `notifications.events.work_unit_blocked_runtime_degradation` -- Event toggle -- work_unit_blocked_runtime_degradation

Fires when the classifier flags a blocked WU as RUNTIME_DEGRADATION (SDK Agent-tool loss). Every event defaults to false so the dispatcher is silent until the operator opts in.

- **Recommended:** `false` -- keeps this event silent until the operator deliberately opts in.
- **Alternatives:** `true` (fires a Slack notification when the classifier flags a blocked WU as RUNTIME_DEGRADATION (SDK Agent-tool loss) (subject to the notifications.enabled master switch and notifications.slack.enabled endpoint toggle also being true).)
- **Free-form:** Type `true` or `false` directly; any other value is rejected and re-prompted.

Current value shown to the operator: the existing config's value for this key if `backlog/config/devbench.yaml` already exists, otherwise the Recommended value above.

#### `notifications.events.work_unit_blocked_held` -- Event toggle -- work_unit_blocked_held

Fires when the classifier flags a blocked WU as HELD (status is hold). Every event defaults to false so the dispatcher is silent until the operator opts in.

- **Recommended:** `false` -- keeps this event silent until the operator deliberately opts in.
- **Alternatives:** `true` (fires a Slack notification when the classifier flags a blocked WU as HELD (status is hold) (subject to the notifications.enabled master switch and notifications.slack.enabled endpoint toggle also being true).)
- **Free-form:** Type `true` or `false` directly; any other value is rejected and re-prompted.

Current value shown to the operator: the existing config's value for this key if `backlog/config/devbench.yaml` already exists, otherwise the Recommended value above.

#### `notifications.events.work_unit_blocked_on_held` -- Event toggle -- work_unit_blocked_on_held

Fires when the classifier flags a blocked WU as BLOCKED_ON_HELD (marker target is hold). Every event defaults to false so the dispatcher is silent until the operator opts in.

- **Recommended:** `false` -- keeps this event silent until the operator deliberately opts in.
- **Alternatives:** `true` (fires a Slack notification when the classifier flags a blocked WU as BLOCKED_ON_HELD (marker target is hold) (subject to the notifications.enabled master switch and notifications.slack.enabled endpoint toggle also being true).)
- **Free-form:** Type `true` or `false` directly; any other value is rejected and re-prompted.

Current value shown to the operator: the existing config's value for this key if `backlog/config/devbench.yaml` already exists, otherwise the Recommended value above.

#### `notifications.events.work_unit_blocked_auto_clearing` -- Event toggle -- work_unit_blocked_auto_clearing

Fires when the classifier flags a blocked WU as AUTO_CLEARING_VIA_PROPOSAL (cascade in flight). Every event defaults to false so the dispatcher is silent until the operator opts in.

- **Recommended:** `false` -- keeps this event silent until the operator deliberately opts in.
- **Alternatives:** `true` (fires a Slack notification when the classifier flags a blocked WU as AUTO_CLEARING_VIA_PROPOSAL (cascade in flight) (subject to the notifications.enabled master switch and notifications.slack.enabled endpoint toggle also being true).)
- **Free-form:** Type `true` or `false` directly; any other value is rejected and re-prompted.

Current value shown to the operator: the existing config's value for this key if `backlog/config/devbench.yaml` already exists, otherwise the Recommended value above.

#### `notifications.events.work_unit_blocked_awaiting_dependency` -- Event toggle -- work_unit_blocked_awaiting_dependency

Fires when the classifier flags a blocked WU as AWAITING_DEPENDENCY (regular dep in flight). Every event defaults to false so the dispatcher is silent until the operator opts in.

- **Recommended:** `false` -- keeps this event silent until the operator deliberately opts in.
- **Alternatives:** `true` (fires a Slack notification when the classifier flags a blocked WU as AWAITING_DEPENDENCY (regular dep in flight) (subject to the notifications.enabled master switch and notifications.slack.enabled endpoint toggle also being true).)
- **Free-form:** Type `true` or `false` directly; any other value is rejected and re-prompted.

Current value shown to the operator: the existing config's value for this key if `backlog/config/devbench.yaml` already exists, otherwise the Recommended value above.

#### `notifications.events.work_unit_blocked_amendment_recovery` -- Event toggle -- work_unit_blocked_amendment_recovery

Fires when the classifier flags a blocked WU as AWAITING_AMENDMENT_RECOVERY (recovery signal on disk). Every event defaults to false so the dispatcher is silent until the operator opts in.

- **Recommended:** `false` -- keeps this event silent until the operator deliberately opts in.
- **Alternatives:** `true` (fires a Slack notification when the classifier flags a blocked WU as AWAITING_AMENDMENT_RECOVERY (recovery signal on disk) (subject to the notifications.enabled master switch and notifications.slack.enabled endpoint toggle also being true).)
- **Free-form:** Type `true` or `false` directly; any other value is rejected and re-prompted.

Current value shown to the operator: the existing config's value for this key if `backlog/config/devbench.yaml` already exists, otherwise the Recommended value above.

#### `notifications.events.work_unit_materialised` -- Event toggle -- work_unit_materialised

Fires when a draft WU file is materialised from a proposal. Every event defaults to false so the dispatcher is silent until the operator opts in.

- **Recommended:** `false` -- keeps this event silent until the operator deliberately opts in.
- **Alternatives:** `true` (fires a Slack notification when a draft WU file is materialised from a proposal (subject to the notifications.enabled master switch and notifications.slack.enabled endpoint toggle also being true).)
- **Free-form:** Type `true` or `false` directly; any other value is rejected and re-prompted.

Current value shown to the operator: the existing config's value for this key if `backlog/config/devbench.yaml` already exists, otherwise the Recommended value above.

#### `notifications.events.work_unit_promoted` -- Event toggle -- work_unit_promoted

Fires when a draft WU is promoted to in-queue. Every event defaults to false so the dispatcher is silent until the operator opts in.

- **Recommended:** `false` -- keeps this event silent until the operator deliberately opts in.
- **Alternatives:** `true` (fires a Slack notification when a draft WU is promoted to in-queue (subject to the notifications.enabled master switch and notifications.slack.enabled endpoint toggle also being true).)
- **Free-form:** Type `true` or `false` directly; any other value is rejected and re-prompted.

Current value shown to the operator: the existing config's value for this key if `backlog/config/devbench.yaml` already exists, otherwise the Recommended value above.

#### `notifications.events.pr_opened` -- Event toggle -- pr_opened

Fires when `gh pr create` succeeds. Every event defaults to false so the dispatcher is silent until the operator opts in.

- **Recommended:** `false` -- keeps this event silent until the operator deliberately opts in.
- **Alternatives:** `true` (fires a Slack notification when `gh pr create` succeeds (subject to the notifications.enabled master switch and notifications.slack.enabled endpoint toggle also being true).)
- **Free-form:** Type `true` or `false` directly; any other value is rejected and re-prompted.

Current value shown to the operator: the existing config's value for this key if `backlog/config/devbench.yaml` already exists, otherwise the Recommended value above.

#### `notifications.events.pr_merged` -- Event toggle -- pr_merged

Fires when `gh pr merge` succeeds. Every event defaults to false so the dispatcher is silent until the operator opts in.

- **Recommended:** `false` -- keeps this event silent until the operator deliberately opts in.
- **Alternatives:** `true` (fires a Slack notification when `gh pr merge` succeeds (subject to the notifications.enabled master switch and notifications.slack.enabled endpoint toggle also being true).)
- **Free-form:** Type `true` or `false` directly; any other value is rejected and re-prompted.

Current value shown to the operator: the existing config's value for this key if `backlog/config/devbench.yaml` already exists, otherwise the Recommended value above.

#### `notifications.events.ci_failure` -- Event toggle -- ci_failure

Fires when CI checks on a WU PR fail. Every event defaults to false so the dispatcher is silent until the operator opts in.

- **Recommended:** `false` -- keeps this event silent until the operator deliberately opts in.
- **Alternatives:** `true` (fires a Slack notification when CI checks on a WU PR fail (subject to the notifications.enabled master switch and notifications.slack.enabled endpoint toggle also being true).)
- **Free-form:** Type `true` or `false` directly; any other value is rejected and re-prompted.

Current value shown to the operator: the existing config's value for this key if `backlog/config/devbench.yaml` already exists, otherwise the Recommended value above.

#### `notifications.events.ci_pass` -- Event toggle -- ci_pass

Fires when CI checks on the finalize-path batch PR turn green (fires under auto_merge: false so the operator knows the PR is ready for manual merge). Every event defaults to false so the dispatcher is silent until the operator opts in.

- **Recommended:** `false` -- keeps this event silent until the operator deliberately opts in.
- **Alternatives:** `true` (fires a Slack notification when CI checks on the finalize-path batch PR turn green (fires under auto_merge: false so the operator knows the PR is ready for manual merge) (subject to the notifications.enabled master switch and notifications.slack.enabled endpoint toggle also being true).)
- **Free-form:** Type `true` or `false` directly; any other value is rejected and re-prompted.

Current value shown to the operator: the existing config's value for this key if `backlog/config/devbench.yaml` already exists, otherwise the Recommended value above.

#### `notifications.events.orchestrator_stop` -- Event toggle -- orchestrator_stop

Fires when the orchestrator loop exits (clean, drain, SIGTERM, or crash). Every event defaults to false so the dispatcher is silent until the operator opts in.

- **Recommended:** `false` -- keeps this event silent until the operator deliberately opts in.
- **Alternatives:** `true` (fires a Slack notification when the orchestrator loop exits (clean, drain, SIGTERM, or crash) (subject to the notifications.enabled master switch and notifications.slack.enabled endpoint toggle also being true).)
- **Free-form:** Type `true` or `false` directly; any other value is rejected and re-prompted.

Current value shown to the operator: the existing config's value for this key if `backlog/config/devbench.yaml` already exists, otherwise the Recommended value above.

#### `notifications.events.orchestrator_auto_restart` -- Event toggle -- orchestrator_auto_restart

Fires when the orchestrator exits 42 (RUNTIME_DEGRADATION) and the Makefile loop restarts. Every event defaults to false so the dispatcher is silent until the operator opts in.

- **Recommended:** `false` -- keeps this event silent until the operator deliberately opts in.
- **Alternatives:** `true` (fires a Slack notification when the orchestrator exits 42 (RUNTIME_DEGRADATION) and the Makefile loop restarts (subject to the notifications.enabled master switch and notifications.slack.enabled endpoint toggle also being true).)
- **Free-form:** Type `true` or `false` directly; any other value is rejected and re-prompted.

Current value shown to the operator: the existing config's value for this key if `backlog/config/devbench.yaml` already exists, otherwise the Recommended value above.

#### `notifications.events.quota_waiting` -- Event toggle -- quota_waiting

Fires when the orchestrator hits a quota and starts waiting for it to reset. Every event defaults to false so the dispatcher is silent until the operator opts in.

- **Recommended:** `false` -- keeps this event silent until the operator deliberately opts in.
- **Alternatives:** `true` (fires a Slack notification when the orchestrator hits a quota and starts waiting for it to reset (subject to the notifications.enabled master switch and notifications.slack.enabled endpoint toggle also being true).)
- **Free-form:** Type `true` or `false` directly; any other value is rejected and re-prompted.

Current value shown to the operator: the existing config's value for this key if `backlog/config/devbench.yaml` already exists, otherwise the Recommended value above.

#### `notifications.events.quota_resumed` -- Event toggle -- quota_resumed

Fires when the quota recovers and the run resumes. Every event defaults to false so the dispatcher is silent until the operator opts in.

- **Recommended:** `false` -- keeps this event silent until the operator deliberately opts in.
- **Alternatives:** `true` (fires a Slack notification when the quota recovers and the run resumes (subject to the notifications.enabled master switch and notifications.slack.enabled endpoint toggle also being true).)
- **Free-form:** Type `true` or `false` directly; any other value is rejected and re-prompted.

Current value shown to the operator: the existing config's value for this key if `backlog/config/devbench.yaml` already exists, otherwise the Recommended value above.

#### `notifications.slack.enabled` -- Slack endpoint toggle

Endpoint-level toggle for the Slack transport. When false, no Slack POST fires regardless of the master switch or per-event toggles.

- **Recommended:** `false` -- matches the disabled default until a real webhook URL is configured.
- **Alternatives:** `true` (enables the Slack transport; still gated by notifications.enabled and each event's own toggle.)
- **Free-form:** Type `true` or `false` directly; any other value is rejected and re-prompted.

Current value shown to the operator: the existing config's value for this key if `backlog/config/devbench.yaml` already exists, otherwise the Recommended value above.

#### `notifications.slack.webhook_url` -- Slack webhook URL

Slack incoming webhook URL. The payload carries an `<!here>` mention so the same webhook works whether it is bound to a one-person DM channel or a shared team channel.

- **Recommended:** `unset (null)` -- avoids committing a credential to the config file.
- **Alternatives:** `set via DEVBENCH_NOTIFICATIONS_SLACK_WEBHOOK_URL env var` (keeps the webhook URL out of version control entirely, the recommended pattern.)
- **Free-form:** Enter the full `https://hooks.slack.com/services/...` URL directly only for a local, non-committed config; the recommended free-form path is to leave this null here and set the env var instead.

Current value shown to the operator: the existing config's value for this key if `backlog/config/devbench.yaml` already exists, otherwise the Recommended value above.

Every payload also carries a Backlog field naming the source workspace (basename of `DEVBENCH_WORKSPACE_ROOT`) so operators monitoring multiple workspaces can tell at a glance which backlog a ping refers to. Full operator walkthrough (Slack app creation, webhook URL, channel routing): `docs/slack-notifications.md`.

Validate `slack.webhook_url` (when present) starts with `https://`. Validate every `events.*` value is a boolean. Reject any invalid value and re-prompt.

---

## Step 19 -- report section

Issue #260, spec FR-3.6, S5.3.

Per-model token pricing for `devbench report`'s cost-estimation and pace projections. Every rate carries a source URL and capture-date citation in `sample-config.yaml` (ref); this step copies those cited values verbatim and never guesses a new rate.

Ask the operator:

> "Section: report (cost-estimation pricing; leave blank to keep every shipped default)
>
> report.models -- per-model token pricing table (USD per 1M tokens). Each key is a Claude
>   model id (the literal message.model value Claude Code records on every assistant message
>   envelope, e.g. claude-opus-4-7). The full current lineup and its source-plus-capture-date
>   citation live in sample-config.yaml's report.models block; copy that block verbatim into
>   the assembled config unless the operator wants to override a specific model's rate.
>
> default_model -- fallback rate applied to any transcript message whose model field is
>   missing OR not present in report.models above. [default: Opus 5 list rate,
>   input: 5.0, output: 25.0]
>
> Cost multipliers (Anthropic's published cache / fast-mode / data-residency pricing):
>   cache_read_multiplier         -- cache-read token discount off the base input rate
>                                     [default: 0.10]
>   cache_write_5min_multiplier   -- 5-minute prompt-cache write premium [default: 1.25]
>   cache_write_1hr_multiplier    -- 1-hour prompt-cache write premium [default: 2.0]
>   data_residency_multiplier     -- applied when usage.inference_geo is set (US-only
>                                     inference) [default: 1.10]
>   fast_mode_multiplier          -- Opus 5 / Opus 4.8 fast-mode premium [default: 2.0]
>   recent_pace_tasks             -- number of most-recently completed tasks averaged for
>                                     the 'Recent pace' cost projection [default: 10]
>
> Optional per-model override fields (set only when one model's actual invoice drifts from
> its list rate): cache_read_multiplier, cache_write_5min_multiplier,
> cache_write_1hr_multiplier, correction_factor. Use `devbench cost-calibrate <actual-usd>`
> to compute and write correction_factor instead of hand-editing this file."

The individual `#### ` interview blocks below give each multiplier and each `default_model` sub-field its own Recommended/Alternatives/Free-form menu; the quoted prompt above is what the skill actually says to the operator in one pass, and the two are required to state matching values.

### `report.models` (dynamic per-model map)

Per-model token pricing table (USD per 1M tokens). Each key is a Claude model id (the literal `message.model` value Claude Code records on every assistant message envelope, e.g. `claude-opus-4-7`). Ask the operator:

> "report.models -- per-model token pricing table. The full current lineup and its source-plus-capture-date citation live in sample-config.yaml's report.models block; copy that block verbatim into the assembled config unless you want to override a specific model's rate."

Each entry's fixed sub-fields (`input`, `output` required; `cache_read_multiplier`, `cache_write_5min_multiplier`, `cache_write_1hr_multiplier`, `correction_factor` optional per-model overrides) mirror `report.default_model` below. Each field's full dotted key in the assembled config is `report.models.<id>.input`, `report.models.<id>.output`, `report.models.<id>.cache_read_multiplier`, `report.models.<id>.cache_write_5min_multiplier`, `report.models.<id>.cache_write_1hr_multiplier`, and `report.models.<id>.correction_factor` respectively.

#### `report.cache_read_multiplier` -- Cache-read cost multiplier

Cost multiplier for cache-read tokens, relative to the base input rate. Overridable via the `DEVBENCH_REPORT_CACHE_READ_MULTIPLIER` env var.

- **Recommended:** `0.10` -- matches Anthropic's published rate.
- **Alternatives:** `0.20` (overrides for a platform (e.g. some Bedrock configurations) with different cache-read pricing.)
- **Free-form:** Enter any non-negative number; negative values are rejected and re-prompted.

Current value shown to the operator: the existing config's value for this key if `backlog/config/devbench.yaml` already exists, otherwise the Recommended value above.

#### `report.cache_write_5min_multiplier` -- 5-minute cache-write cost multiplier

Cost multiplier for 5-minute prompt-cache write tokens, relative to the base input rate. Overridable via the `DEVBENCH_REPORT_CACHE_WRITE_5MIN_MULTIPLIER` env var.

- **Recommended:** `1.25` -- matches Anthropic's published rate.
- **Alternatives:** `1.5` (overrides for a platform with different 5-minute cache-write pricing.)
- **Free-form:** Enter any non-negative number; negative values are rejected and re-prompted.

Current value shown to the operator: the existing config's value for this key if `backlog/config/devbench.yaml` already exists, otherwise the Recommended value above.

#### `report.cache_write_1hr_multiplier` -- 1-hour cache-write cost multiplier

Cost multiplier for 1-hour prompt-cache write tokens, relative to the base input rate. Overridable via the `DEVBENCH_REPORT_CACHE_WRITE_1HR_MULTIPLIER` env var.

- **Recommended:** `2.0` -- matches Anthropic's published rate.
- **Alternatives:** `2.5` (overrides for a platform with different 1-hour cache-write pricing.)
- **Free-form:** Enter any non-negative number; negative values are rejected and re-prompted.

Current value shown to the operator: the existing config's value for this key if `backlog/config/devbench.yaml` already exists, otherwise the Recommended value above.

#### `report.data_residency_multiplier` -- Data-residency cost multiplier

Cost multiplier applied per-call to `usage.inference_geo`-flagged tokens (US-only inference). Composes with cache + base-rate multipliers and applies before any per-model correction factor. Overridable via the `DEVBENCH_REPORT_DATA_RESIDENCY_MULTIPLIER` env var.

- **Recommended:** `1.10` -- matches Anthropic's published rate.
- **Alternatives:** `1.0` (removes the data-residency premium for a workspace that never sets inference_geo.)
- **Free-form:** Enter any non-negative number; negative values are rejected and re-prompted.

Current value shown to the operator: the existing config's value for this key if `backlog/config/devbench.yaml` already exists, otherwise the Recommended value above.

#### `report.fast_mode_multiplier` -- Fast-mode cost multiplier

Cost multiplier applied per-call to `usage.speed=='fast'` tokens (Opus 5 / Opus 4.8 fast-mode premium). Composes with cache + base-rate multipliers. Overridable via the `DEVBENCH_REPORT_FAST_MODE_MULTIPLIER` env var.

- **Recommended:** `2.0` -- matches Anthropic's published rate captured 2026-07-28.
- **Alternatives:** `1.0` (removes the fast-mode premium if the platform's fast-mode pricing changes.)
- **Free-form:** Enter any non-negative number; negative values are rejected and re-prompted.

Current value shown to the operator: the existing config's value for this key if `backlog/config/devbench.yaml` already exists, otherwise the Recommended value above.

#### `report.recent_pace_tasks` -- Recent-pace window size

Number of most-recently-completed tasks averaged for the 'Recent pace' cost projection in `devbench report`. Overridable via the `DEVBENCH_REPORT_RECENT_PACE_TASKS` env var.

- **Recommended:** `10` -- balances responsiveness against stability for the projection.
- **Alternatives:** `20` (smooths the projection further for a noisier task-duration distribution.)
- **Free-form:** Enter any integer >= 1; non-integers or values below 1 are rejected and re-prompted.

Current value shown to the operator: the existing config's value for this key if `backlog/config/devbench.yaml` already exists, otherwise the Recommended value above.

#### `report.display_timezone` -- Report-specific display timezone

IANA timezone name for displaying timestamps in `devbench report` specifically. Takes precedence over the top-level `display_timezone` for the report command only. Overridable via `DEVBENCH_REPORT_TIMEZONE`.

- **Recommended:** `unset (falls back to top-level display_timezone, then OS local)` -- avoids a second timezone to maintain when the top-level setting already covers the operator's needs.
- **Alternatives:** `America/Denver` (pins the report command's timestamps to a fixed timezone independent of the top-level display_timezone.)
- **Free-form:** Enter any IANA timezone name, or leave blank to inherit the top-level setting.

Current value shown to the operator: the existing config's value for this key if `backlog/config/devbench.yaml` already exists, otherwise the Recommended value above.

#### `report.default_model.input` -- Default-model input rate

USD per million input tokens applied to any transcript message whose `model` field is missing or not present in `report.models`.

- **Recommended:** `5.0` -- matches the Opus 5 list input rate, erring toward over-reporting cost for an unknown model.
- **Alternatives:** `a positive number in USD per 1M tokens` (overrides the shipped default with a value matching your actual Anthropic contract.)
- **Free-form:** Use `devbench cost-calibrate <actual-usd>` to compute and write `correction_factor` instead of hand-editing; other fields accept direct numeric entry.

Current value shown to the operator: the existing config's value for this key if `backlog/config/devbench.yaml` already exists, otherwise the Recommended value above.

#### `report.default_model.output` -- Default-model output rate

USD per million output tokens applied to any transcript message whose `model` field is missing or not present in `report.models`.

- **Recommended:** `25.0` -- matches the Opus 5 list output rate, erring toward over-reporting cost for an unknown model.
- **Alternatives:** `a positive number in USD per 1M tokens` (overrides the shipped default with a value matching your actual Anthropic contract.)
- **Free-form:** Use `devbench cost-calibrate <actual-usd>` to compute and write `correction_factor` instead of hand-editing; other fields accept direct numeric entry.

Current value shown to the operator: the existing config's value for this key if `backlog/config/devbench.yaml` already exists, otherwise the Recommended value above.

#### `report.default_model.cache_read_multiplier` -- Default-model cache-read override

Optional per-model override of `report.cache_read_multiplier`, applied only to the `<unknown>` bucket.

- **Recommended:** `unset (inherits report.cache_read_multiplier)` -- avoids a redundant override when the top-level multiplier already applies.
- **Alternatives:** `a positive number` (overrides the shipped default with a value matching your actual Anthropic contract.)
- **Free-form:** Use `devbench cost-calibrate <actual-usd>` to compute and write `correction_factor` instead of hand-editing; other fields accept direct numeric entry.

Current value shown to the operator: the existing config's value for this key if `backlog/config/devbench.yaml` already exists, otherwise the Recommended value above.

#### `report.default_model.cache_write_5min_multiplier` -- Default-model 5-min cache-write override

Optional per-model override of `report.cache_write_5min_multiplier`, applied only to the `<unknown>` bucket.

- **Recommended:** `unset (inherits report.cache_write_5min_multiplier)` -- avoids a redundant override when the top-level multiplier already applies.
- **Alternatives:** `a positive number` (overrides the shipped default with a value matching your actual Anthropic contract.)
- **Free-form:** Use `devbench cost-calibrate <actual-usd>` to compute and write `correction_factor` instead of hand-editing; other fields accept direct numeric entry.

Current value shown to the operator: the existing config's value for this key if `backlog/config/devbench.yaml` already exists, otherwise the Recommended value above.

#### `report.default_model.cache_write_1hr_multiplier` -- Default-model 1-hr cache-write override

Optional per-model override of `report.cache_write_1hr_multiplier`, applied only to the `<unknown>` bucket.

- **Recommended:** `unset (inherits report.cache_write_1hr_multiplier)` -- avoids a redundant override when the top-level multiplier already applies.
- **Alternatives:** `a positive number` (overrides the shipped default with a value matching your actual Anthropic contract.)
- **Free-form:** Use `devbench cost-calibrate <actual-usd>` to compute and write `correction_factor` instead of hand-editing; other fields accept direct numeric entry.

Current value shown to the operator: the existing config's value for this key if `backlog/config/devbench.yaml` already exists, otherwise the Recommended value above.

#### `report.default_model.correction_factor` -- Default-model correction factor

Per-model contract correction factor applied to the `<unknown>` bucket's computed cost, after every other multiplier.

- **Recommended:** `unset (1.0, no correction)` -- avoids a correction until an actual invoice comparison justifies one.
- **Alternatives:** `a positive number` (overrides the shipped default with a value matching your actual Anthropic contract.)
- **Free-form:** Use `devbench cost-calibrate <actual-usd>` to compute and write `correction_factor` instead of hand-editing; other fields accept direct numeric entry.

Current value shown to the operator: the existing config's value for this key if `backlog/config/devbench.yaml` already exists, otherwise the Recommended value above.

Validate each provided `report.models.<id>` entry requires both `input` and `output`, each a non-negative number (USD per 1M tokens; 0 is allowed). Validate every optional multiplier field (`cache_read_multiplier`, `cache_write_5min_multiplier`, `cache_write_1hr_multiplier`) is also non-negative. `correction_factor` is the one field that must be strictly positive (> 0). Reject with:

> "[INVALID] report.models.<id> requires both 'input' and 'output' as non-negative numbers (USD per 1M tokens). Re-enter."

> "[INVALID] report.models.<id>.correction_factor must be > 0. Re-enter."

---

## Step 20 -- quota_handling section (issue #236, spec S5.2)

Quota wait-and-resume configuration. Governs what the orchestrator does when the Claude CLI reports a quota-exhaustion signal (HTTP 429 / CLI "You've hit your limit" message).

#### `quota_handling.enabled` -- Quota wait-and-resume master toggle

Master toggle for the quota wait-and-resume dispatcher (issue #236). When true, the orchestrator pauses and waits for the quota window to reset instead of exiting non-zero on a quota-exhaustion signal.

- **Recommended:** `true` -- keeps a long-running orchestrator alive across a routine quota reset instead of requiring a manual restart.
- **Alternatives:** `false` (restores the pre-#236 behaviour: propagate the quota error and exit non-zero immediately.)
- **Free-form:** Type `true` or `false` directly; any other value is rejected and re-prompted.

Current value shown to the operator: the existing config's value for this key if `backlog/config/devbench.yaml` already exists, otherwise the Recommended value above.

#### `quota_handling.on_exhaustion` -- Action on quota-exhaustion signal

Action taken when a quota-exhaustion signal (HTTP 429 / CLI limit message) is detected.

- **Recommended:** `wait` -- pauses and polls until the quota resets, the fully autonomous default.
- **Alternatives:** `fail` (re-raises immediately, same as enabled: false.); `drain` (triggers a graceful drain then exits.)
- **Free-form:** Type `wait`, `fail`, or `drain` directly; any other value is rejected and re-prompted.

Current value shown to the operator: the existing config's value for this key if `backlog/config/devbench.yaml` already exists, otherwise the Recommended value above.

#### `quota_handling.poll_interval_seconds` -- Quota recovery poll cadence

Base cadence in seconds for the recovery-probe loop while waiting for quota to reset.

- **Recommended:** `60` -- balances prompt recovery detection against excessive probe traffic.
- **Alternatives:** `120` (reduces probe frequency for a quota window known to be long.)
- **Free-form:** Enter any integer between 30 and 3600; out-of-range or non-integer values are rejected and re-prompted.

Current value shown to the operator: the existing config's value for this key if `backlog/config/devbench.yaml` already exists, otherwise the Recommended value above.

#### `quota_handling.max_wait_seconds` -- Maximum quota wait cap

Maximum total wait time in seconds before `on_exhaustion_timeout` fires.

- **Recommended:** `18000 (5 hours)` -- covers most provider-stated quota reset windows without waiting indefinitely.
- **Alternatives:** `7200 (2 hours)` (fails or drains sooner for a workspace that prefers a shorter unattended wait.)
- **Free-form:** Enter any integer >= 1; non-integers or values below 1 are rejected and re-prompted.

Current value shown to the operator: the existing config's value for this key if `backlog/config/devbench.yaml` already exists, otherwise the Recommended value above.

#### `quota_handling.on_exhaustion_timeout` -- Action when max_wait_seconds elapses

Action taken when `max_wait_seconds` elapses without a confirmed quota recovery.

- **Recommended:** `drain` -- triggers a graceful drain, preserving in-flight work over an abrupt failure.
- **Alternatives:** `fail` (re-raises the quota error instead of draining.); `keep_waiting` (logs [QUOTA_TIMEOUT_KEEP_WAITING] and ends the run with no drain and no re-raise.)
- **Free-form:** Type `drain`, `fail`, or `keep_waiting` directly; any other value is rejected and re-prompted.

Current value shown to the operator: the existing config's value for this key if `backlog/config/devbench.yaml` already exists, otherwise the Recommended value above.

#### `quota_handling.resume_strategy` -- Resume strategy after recovery

How the orchestrator re-enters the orchestrate loop after a confirmed quota recovery.

- **Recommended:** `continue_current_wu` -- resumes exactly where the run left off, the least disruptive default.
- **Alternatives:** `restart_wu` (forces the current work unit back to in-queue before resuming.); `drain_and_resume` (removes the quota checkpoint and requests a graceful drain; the run then must be restarted manually.)
- **Free-form:** Type `continue_current_wu`, `restart_wu`, or `drain_and_resume` directly; any other value is rejected and re-prompted.

Current value shown to the operator: the existing config's value for this key if `backlog/config/devbench.yaml` already exists, otherwise the Recommended value above.

#### `quota_handling.audit_comment_on_wait` -- Audit comment on quota wait

Whether to append a `[QUOTA_WAITING]` audit comment to the in-progress work unit when a quota pause begins.

- **Recommended:** `true` -- leaves an audit trail explaining why the work unit paused.
- **Alternatives:** `false` (suppresses the audit comment when the wait begins; the wait itself still happens.)
- **Free-form:** Type `true` or `false` directly; any other value is rejected and re-prompted.

Current value shown to the operator: the existing config's value for this key if `backlog/config/devbench.yaml` already exists, otherwise the Recommended value above.

#### `quota_handling.audit_comment_on_resume` -- Audit comment on quota resume

Whether to append a `[QUOTA_RESUMED]` audit comment after a confirmed quota recovery.

- **Recommended:** `true` -- leaves an audit trail explaining when the run resumed.
- **Alternatives:** `false` (suppresses the audit comment when the run resumes; the resume itself still happens.)
- **Free-form:** Type `true` or `false` directly; any other value is rejected and re-prompted.

Current value shown to the operator: the existing config's value for this key if `backlog/config/devbench.yaml` already exists, otherwise the Recommended value above.

#### `quota_handling.log_structured_events` -- Structured quota event logging

Gates the seven structured `[QUOTA_*]` log markers. Does not affect Slack notifications, the audit comments above, the on-disk checkpoint, or the `[ORCHESTRATOR_QUOTA_*]` markers.

- **Recommended:** `true` -- keeps the structured markers available for log-based tooling and dashboards.
- **Alternatives:** `false` (suppresses all seven structured [QUOTA_*] markers; the underlying wait/resume behaviour is unaffected.)
- **Free-form:** Type `true` or `false` directly; any other value is rejected and re-prompted.

Current value shown to the operator: the existing config's value for this key if `backlog/config/devbench.yaml` already exists, otherwise the Recommended value above.

---

## Step 21 -- Final validation and write

Assemble the complete YAML from all collected sections. In addition to the operator-supplied sections above, the assembled YAML must also emit every remaining FR-3.6 tuning section at its resolved built-in default, so a freshly configured workspace is self-documenting (issue #260, spec FR-3.6, AC-40, Journey J-7): `timeouts`, `limits`, `stop_hook`, `hook_tail`, `orchestrate`, `report` (including `models`, `default_model`, and every multiplier field), `backlog`, `validate`, `skills`, `max_executor_retries`, `max_executor_retries_per_judge`, and `log_file`. Unlike the pre-rewrite skill, every one of those sections is now also interactively interviewed in Steps 3-20 above (including the new `gates` and `quota_handling` sections), so "emit at resolved default" now means "emit the value the operator actually chose (or accepted as the recommended default) in its own Step," not a value the operator was never asked about. An operator who later wants to tune a knob sees it in the file with its resolved value and annotated comment already present, instead of discovering the knob only by reading `config_loader.py`.
> **`orchestrate.*` transport-restart knobs -- what to tell the operator.**
> These are emitted at their built-in defaults like every other FR-3.6 tuning
> section, and most workspaces should leave them alone. Raise them only if the
> operator explicitly asks, and explain the trade-off rather than just setting
> the number:
>
> - `max_transport_restarts` (default `14`) bounds consecutive restarts after
>   an SDK **transport** failure. It is deliberately NOT the quota ceiling
>   (`max_quota_resumes`, default `1000`). Those two must not be conflated: a
>   quota window must elapse before a resume can succeed, so quota resumes
>   self-throttle, whereas a transport fault recurs as fast as the SDK can
>   reject a session. Pairing a four-figure budget with transport faults is
>   what previously let a single persistent fault burn ~1000 restarts in 39
>   minutes and end an unattended run.
> - `transport_restart_backoff_base_seconds` (default `1.0`) and
>   `transport_restart_backoff_max_seconds` (default `60.0`) space those
>   restarts as `base * 2 ** restarts_already_done`, clamped to the ceiling.
>   Both must be `> 0`; the schema rejects zero or negative at load time.
> - Operational caveat worth stating out loud: the ceiling also bounds how
>   long an in-flight backoff wait can delay a `devbench stop`. An operator who
>   raises the ceiling to many minutes is also making shutdown that much less
>   responsive.
> - If the operator is running unattended (`--daemon`) and wants to survive a
>   longer upstream outage, the right lever is usually a **higher ceiling**
>   (fewer, more spaced attempts), not a much higher cap -- a high cap with a
>   low ceiling just retries a dead transport more often.

Assemble the complete YAML from all collected sections. In addition to the operator-supplied sections above, the assembled YAML must also emit every remaining FR-3.6 tuning section at its resolved built-in default, so a freshly configured workspace is self-documenting (issue #260, spec FR-3.6, AC-40, Journey J-7): `timeouts`, `limits`, `stop_hook`, `hook_tail`, `orchestrate`, `report` (including `models`, `default_model`, and every multiplier field), `backlog`, `validate`, `skills`, `max_executor_retries`, `max_executor_retries_per_judge`, and `log_file`. An operator who later wants to tune a knob sees it in the file with its default value and annotated comment already present, instead of discovering the knob only by reading `config_loader.py`.

Source every emitted default value and its comment from `sample-config.yaml` (ref) -- copy the value and comment verbatim; never restate a number by hand from memory. Written values must equal built-in defaults exactly; any drift between an emitted value and the corresponding built-in default in `src/devbench/constants.py` / `config_loader.py` is a defect (FR-3.6 error handling).

Trim the following inert blocks from the emission even though they are technically part of the full-default surface:
- `bedrock_region` -- trim when `use_bedrock` is `false` (the field has no effect while Bedrock routing is off).
- `agents:` entries -- trim any entry whose value equals that agent's frontmatter default; keep only entries the operator actually overrode away from frontmatter.
- Disabled sub-block trim: trim any sub-block whose own `enabled` toggle (or equivalent master switch) resolves to `false` -- for example `git_ops.pr_review_resolution` when `pr_review_resolution.enabled: false`, `gates.<gate>` when that gate's `enabled: false`, and `notifications` entirely when `notifications.enabled: false`. A sub-block that defaults to enabled (e.g. `quota_handling`) is NOT trimmed by this rule.
- `debug:` -- trim entirely when the operator skipped Step 14.

**Output contract (AC-E2-F8-S1-T1-4, spec section 4.15, AC-29). Every authored `devbench.yaml` MUST be validated by `load_runtime_config` before this skill reports success.** Run the full validation round-trip:

```bash
python -c "
import sys, tempfile, yaml, os
from pathlib import Path
from devbench.config_loader import load_runtime_config

data = <assembled config dict>
with tempfile.NamedTemporaryFile(mode='w', suffix='.yaml', delete=False) as f:
    yaml.dump(data, f, default_flow_style=False, sort_keys=False)
    tmp = f.name

try:
    load_runtime_config(Path(tmp), os.environ)
    print('VALID')
except Exception as e:
    print(f'INVALID: {e}', file=sys.stderr)
    sys.exit(1)
"
```

If the validation exits non-zero, identify the section responsible, show the error message verbatim, and return the operator to the relevant step. Do NOT write `backlog/config/devbench.yaml` and do NOT report success until this `load_runtime_config` validation exits zero -- the skill must fail fast on its own output rather than hand the operator a file that breaks at the next command.

**Round-trip equivalence check** (FR-3.6 error handling, spec AC-44, Journey J-7). `load_runtime_config` only parses literal YAML; it leaves an absent field as `None` on the raw `RuntimeConfig` dataclass and does NOT apply the env-var-over-YAML-over-built-in-default resolution chain -- that resolution happens downstream, in `src/devbench/config.py`'s module-level constants (e.g. `REPORT_FAST_MODE_MULTIPLIER`, `STOP_HOOK_MAX_BLOCKS`, `MAX_RETRY_ATTEMPTS`). Comparing two raw `RuntimeConfig` objects with `!=` therefore ALWAYS reports a spurious difference between an explicit full-default config and a minimal one, even when both resolve to identical runtime behavior -- do not use that comparison. Instead, spawn one subprocess per candidate config with `DEVBENCH_CONFIG_PATH` pointed at that config's temp file so `devbench.config` is imported fresh and performs real resolution in each subprocess, dump the resolved constants that back every FR-3.6 tuning section to JSON, and diff the two JSON blobs in the parent process. `max_executor_retries_per_judge` is the one FR-3.6 field the dump does NOT compare as a raw dict: its built-in default is `{}` on `RuntimeConfig`, so a full-default config that names every judge explicitly will always differ from a minimal config's empty dict at the raw level even though both behave identically once the documented per-judge fallback to `max_executor_retries` (`plugin/devbench-orchestrate/skills/orchestrate/SKILL.md`) is applied; the dump script reproduces that fallback itself and compares the RESOLVED per-judge effective retry budget instead. When substituting `<assembled full-default config dict>` / `<minimal config dict>`, write dict keys and string values with single quotes -- the surrounding `python -c "..."` wrapper is itself a double-quoted shell string, so unescaped double quotes inside the substituted dict literal terminate that string early and corrupt the command:

```bash
python -c "
import json, os, subprocess, sys, tempfile, yaml
from pathlib import Path

full_default_data = <assembled full-default config dict>
minimal_data = <minimal config dict: only operator-changed values, plus repos>

def _write(data):
    with tempfile.NamedTemporaryFile(mode='w', suffix='.yaml', delete=False) as f:
        yaml.dump(data, f, default_flow_style=False, sort_keys=False)
        return f.name

DUMP_SCRIPT = r'''
import json, sys
from devbench import config
from devbench.constants import ALL_REQUIRED_JUDGE_NAMES

# One constant per FR-3.6-resolved section (timeouts, limits, stop_hook,
# hook_tail, orchestrate, report, max_executor_retries). backlog, validate
# and skills carry concrete (non-None) defaults directly on RuntimeConfig
# with no downstream config.py resolution layer, so they are compared
# directly off RUNTIME_CONFIG below. log_file is compared via the same
# _resolve_log_file() helper setup_logging/report use.
#
# max_executor_retries_per_judge is deliberately NOT compared as a raw
# dict: its built-in RuntimeConfig default is {} (config_loader.py's
# max_executor_retries_per_judge field(default_factory=dict)), while a
# full-default emission writes every judge name explicitly, so the raw
# dicts always differ between a full-default and a minimal config even
# when both resolve to identical effective behavior. The per-judge
# fallback to max_executor_retries when a judge is absent is documented
# in plugin/devbench-orchestrate/skills/orchestrate/SKILL.md and applied
# at consumption time, not inside devbench/config.py, so this check
# reproduces that same fallback and compares the RESOLVED per-judge
# effective retry budget instead of the raw dict.
_MODULE_CONSTANTS = [
    \"GH_API_TIMEOUT\", \"TEST_TIMEOUT\", \"SECURITY_FETCH_TIMEOUT\", \"LLM_TIMEOUT\",
    \"COMMAND_TIMEOUT\", \"ORCHESTRATOR_POLL_INTERVAL\", \"GITHUB_CHECK_TIMEOUT_SECONDS\",
    \"ALERT_SUMMARY_LIMIT\", \"OUTPUT_TRUNCATION_LIMIT\", \"LLM_EVIDENCE_TRUNCATION\",
    \"LLM_FILE_CONTEXT_LIMIT\", \"LLM_FILE_PREVIEW_CHARS\", \"CI_FAILURE_LOG_BYTES\",
    \"STOP_HOOK_MAX_BLOCKS\", \"STOP_HOOK_WINDOW_SECONDS\", \"STOP_HOOK_STALE_TASK_MINUTES\",
    \"HOOK_TAIL_AGENT_WIDTH\", \"HOOK_TAIL_TOOL_WIDTH\", \"HOOK_TAIL_DESCRIPTION_MAX\",
    \"HOOK_TAIL_STDOUT_PREVIEW_MAX\", \"MAX_CASCADE_DEPTH\",
    \"MAX_TRANSPORT_RESTARTS\", \"TRANSPORT_RESTART_BACKOFF_BASE_SECONDS\",
    \"TRANSPORT_RESTART_BACKOFF_MAX_SECONDS\",
    \"REPORT_MODEL_RATES\", \"REPORT_DEFAULT_MODEL_RATES\", \"REPORT_CACHE_READ_MULTIPLIER\",
    \"REPORT_CACHE_WRITE_5MIN_MULTIPLIER\", \"REPORT_CACHE_WRITE_1HR_MULTIPLIER\",
    \"REPORT_DATA_RESIDENCY_MULTIPLIER\", \"REPORT_FAST_MODE_MULTIPLIER\",
    \"RECENT_PACE_TASKS\", \"MAX_RETRY_ATTEMPTS\",
]

def _ser(obj):
    if hasattr(obj, \"__dataclass_fields__\"):
        return {k: _ser(getattr(obj, k)) for k in obj.__dataclass_fields__}
    if isinstance(obj, dict):
        return {str(k): _ser(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple, frozenset, set)):
        return sorted(_ser(v) for v in obj) if isinstance(obj, (frozenset, set)) else [_ser(v) for v in obj]
    if isinstance(obj, (str, int, float, bool)) or obj is None:
        return obj
    return repr(obj)

out = {name: _ser(getattr(config, name)) for name in _MODULE_CONSTANTS}
for name in (\"backlog\", \"validate\", \"skills\"):
    out[f\"RUNTIME_CONFIG.{name}\"] = _ser(getattr(config.RUNTIME_CONFIG, name))
per_judge = config.RUNTIME_CONFIG.max_executor_retries_per_judge
for judge in sorted(ALL_REQUIRED_JUDGE_NAMES):
    out[f\"RESOLVED_MAX_RETRIES.{judge}\"] = per_judge.get(judge, config.MAX_RETRY_ATTEMPTS)
from devbench.log_setup import _resolve_log_file
out[\"RESOLVED_LOG_FILE\"] = str(_resolve_log_file())
json.dump(out, sys.stdout)
'''

def _dump(config_path):
    env = dict(os.environ)
    env['DEVBENCH_CONFIG_PATH'] = config_path
    result = subprocess.run([sys.executable, '-c', DUMP_SCRIPT], env=env, capture_output=True, text=True)
    if result.returncode != 0:
        print(result.stderr, file=sys.stderr)
        sys.exit(1)
    return json.loads(result.stdout)

full_dump = _dump(_write(full_default_data))
minimal_dump = _dump(_write(minimal_data))

mismatches = [
    (k, full_dump.get(k, '<MISSING>'), minimal_dump.get(k, '<MISSING>'))
    for k in sorted(set(full_dump) | set(minimal_dump))
    if full_dump.get(k, '<MISSING>') != minimal_dump.get(k, '<MISSING>')
]
if mismatches:
    for k, a, b in mismatches:
        print(f'MISMATCH: differing field {k!r}: full-default={a!r} minimal={b!r}', file=sys.stderr)
    sys.exit(1)
print('EQUIVALENT')
"
```

If this exits non-zero, the emitted full-default config resolves to different runtime behavior than a minimal config -- this is a defect, never an acceptable output. Fail loudly, report every `MISMATCH: differing field ...` line verbatim to the operator, fix the emission instructions for that field's default value, and re-run this check before writing the file.

On `VALID` and `EQUIVALENT`, write the assembled config to `backlog/config/devbench.yaml`:

```
Write backlog/config/devbench.yaml
```

Report:

> "[CONFIGURE_DEVBENCH_DONE] backlog/config/devbench.yaml written and validated successfully.
>
> Summary of configured sections:
>   repos:              <N repos configured>
>   merge_strategy:     <value>
>   use_bedrock:        <value>
>   git_ops:            single_branch=<value>, defer_pr=<value>, auto_finalize=<value>, auto_merge=<value>
>   task_factory:       enabled=<value>, auto_accept_proposals=<value>
>   manifest_amendment: enabled=<value>
>   backlog:            default_status_for_new_work_units=<value>
>   gates:               <comma-separated list of enabled gates, or 'none'>
>   quota_handling:      enabled=<value>, on_exhaustion=<value>
>   notifications:      enabled=<value>, events=<comma-separated list of enabled events>
>   stop_hook:          max_blocks=<value>, window_seconds=<value>
>   report:             default_model=input <value>/output <value>, N models priced, fast_mode_multiplier=<value>
>
> Every tuning section not listed above (timeouts, limits, hook_tail, orchestrate, validate, skills, max_executor_retries, max_executor_retries_per_judge, log_file) was also interviewed and written at its resolved value; see backlog/config/devbench.yaml directly for its value.
>
> Next step: run 'claude run devbench-authoring:bootstrap-environment' to clone target repos and verify make validate baselines."

After writing the yaml, also produce or refresh the workspace's
`devbench-commands.txt` launcher file with both the standard launch
commands (1-5) AND the daemon-mode lifecycle commands (6-10) below. The
operator chooses between foreground (command 1) and daemon mode (command
6) per run; daemon mode is recommended for production / long-running
sessions because it frees the terminal and supports targeted
`stop` / `tail` / `restart` lookups by instance id (#209).

The lifecycle commands all read `<workspace>/.devbench/orchestrator.pid`
(written by daemon-mode start) plus walk PID files under
`DEVBENCH_INSTANCE_SEARCH_ROOTS` (default: `$HOME` plus the current
`DEVBENCH_WORKSPACE_ROOT`), so they work without the operator needing to
`cd` into the target workspace.

Template (substitute `/path/to/devbench` + `/path/to/kanon-deps-work`):

```text
# 6. Non-interactive start (DAEMON -- recommended)
DEVBENCH_WORKSPACE_ROOT=/path/to/<workspace> \
DEVBENCH_CLAUDE_MODEL=<model> \
uv run --project /path/to/devbench python -m devbench.cli start --daemon

# 7. List running orchestrators on this host
uv run --project /path/to/devbench devbench instances

# 8. Stop by instance id (SIGTERM; SIGKILL only with --force)
uv run --project /path/to/devbench devbench stop-instance <instance_id>

# 9. Tail an orchestrator's log
uv run --project /path/to/devbench devbench tail <instance_id> --follow

# 10. Restart (stop + start in same mode)
uv run --project /path/to/devbench devbench restart <instance_id>
```

---

## Self-critique loop (bounded)

The re-prompt loop that fires on invalid YAML values must terminate -- either
when the round-trip parse via `ConfigLoader` succeeds for every section
(success) or when the iteration budget is exhausted (escalation). Use the
helpers in `src/devbench/skill_state.py`:

- On each pass call `read_checkpoint("configure-devbench", workspace_root)`
  to load the previous counter (returns `None` on the first pass).
- When `ConfigLoader.load_runtime_config(...)` succeeds without raising
  (`unresolved_count <= SKILL_QUALITY_THRESHOLD`), call
  `emit_audit("configure-devbench", SKILL_AUDIT_QUALITY_THRESHOLD_REACHED, {...}, workspace_root)`
  and exit success.
- Otherwise increment the checkpoint via `write_checkpoint(...)` and re-prompt.
- When the iteration reaches `SKILL_MAX_ITERATIONS` (defined in
  `src/devbench/constants.py`), call
  `emit_audit("configure-devbench", SKILL_AUDIT_MAX_ITERATIONS_REACHED, {"unresolved": ...}, workspace_root)`
  and exit non-zero so the operator can resolve the rejected value manually.

The audit tags `[SKILL_MAX_ITERATIONS_REACHED]` and
`[SKILL_QUALITY_THRESHOLD_REACHED]` flow through the existing report and
hook-tail pipelines without any new infrastructure.
