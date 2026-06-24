# ADR-25: Per-agent model overrides via workspace-local shadow plugin

**Status:** Accepted
**Date:** 2026-05-15

---

## Context

DevBench ships eleven work agents under `plugin/devbench-orchestrate/agents/`
(the six core agents, the four `review_team` judges, and the optional
`iac-deploy-reviewer` IaC judge). Each agent's `.md` file declares its model in
YAML frontmatter:

```yaml
---
name: executor
model: sonnet
---
```

Today every work agent is pinned to `sonnet` by frontmatter default (haiku
was tried for `review-supervisor` and dropped: under load the SDK silently
removed the Agent tool from haiku's tool list, breaking parallel review_team
dispatch -- see CHANGELOG entry for the role-default refresh). The top-level
orchestrate skill inherits the SDK caller's
model (set by the launcher via `DEVBENCH_CLAUDE_MODEL`, typically opus). That
caller-supplied model **only governs the orchestrate skill's own coordination
calls**: every `Agent(...)` invocation that fires a work agent uses the
agent's own frontmatter model, not the orchestrate skill's.

This becomes a problem the moment an operator's per-model quota is uneven --
e.g. opus tokens left, sonnet exhausted. The orchestrator burns opus on
coordination while every work-agent invocation fails for lack of sonnet quota.
Operators have a real opus budget that the system cannot spend.

A second motivating consideration: the same operator may run devbench
**interactively** (`claude --plugin-dir <path>` plus a manual orchestrate
prompt) or **non-interactively** (`devbench start`, which calls the Claude
Agent SDK directly). Any override mechanism that only works in one mode
forces the operator to keep two parallel configurations.

## Decision

Materialise a workspace-local **shadow plugin tree** in which **every** agent
`.md` file is a real file -- the canonical content copied verbatim, with the
`model:` frontmatter line rewritten only for agents whose model is overridden
-- while every **non-agent** file is a symlink back to the canonical. Both
modes load this shadow tree instead of the canonical when any per-agent
override is configured.

Agent files must be real files (never symlinks) because the Claude Agent SDK
discovers subagents by walking the plugin tree on disk: a symlinked agent
`.md` is not registered as a dispatchable agent type and silently disappears
from the session. An earlier design symlinked every non-overridden agent and
copied only the overridden ones; that left agents absent from the override
maps (notably `iac-deploy-reviewer`) as symlinks, so the optional `iac_review`
judge failed to dispatch and the done-gate blocked every infrastructure unit.
Materialising the full agent roster as real files -- regardless of which
models are overridden -- guarantees the complete roster registers and is
robust to agents added to the canonical plugin in future without touching the
shadow module.

Configuration surface: a new top-level `agents:` block in
`backlog/config/devbench.yaml`. The example below pins each field to its
**current frontmatter default**. The defaults are tuned by the role each
agent plays:

* `executor` (writes code under TDD) on `sonnet` -- keeps the happy-path
  loop fast.
* The five judges (`code_reviewer`, `test_reviewer`, `doc_reviewer`,
  `changes_manifest`, `security_reviewer`) on `opus` -- they fire only
  after the executor finishes a task, and a bad verdict costs more than
  the inference savings.
* The three workflow-reasoning agents (`blocker_resolver`,
  `manifest_amender`, `task_factory`) on `opus` -- judgment-heavy and
  fire only on unhappy paths, so cost is bounded; a wrong proposal /
  wrong amendment decision / poor draft creates downstream cascade cost
  larger than the inference savings.
* `review_supervisor` on `sonnet` -- fan-out coordinator that spawns
  the five judges in parallel and merges their JSON verdicts. Haiku was
  tried and dropped: under load the SDK silently removed the Agent tool
  from haiku's tool list, breaking parallel review_team dispatch. Opus here
  would be waste.

### Haiku is rejected at config-load for every work agent

Empirical observation in this codebase: when `review_supervisor` was pinned to
`haiku`, the Claude Agent SDK was repeatedly observed to drop the `Agent` tool
from the running session's tool list mid-orchestration, leaving the agent
unable to dispatch sub-agents and forcing the orchestrator to classify the
work-unit as `RUNTIME_DEGRADATION` (issue #183 follow-up). The same risk
applies to every other work agent that uses the `Agent` tool or any
multi-tool-call pattern (executor, blocker-resolver, manifest-amender,
task-factory, the five judges).

**Haiku is rejected at config-load time (caylent-solutions/devbench#198).**
Any `agents:` block value that contains `haiku` -- whether the short name
`"haiku"`, a full Anthropic API id like `"claude-haiku-4-5-20251001"`, or a
Bedrock ARN containing `haiku` -- raises a `ValueError` at config-load and
prevents the orchestrator from starting. There is no operator-facing override
path; re-enabling haiku requires both removing the haiku guard in
`validate_agent_model_value()` in `src/devbench/config_loader.py` AND
re-adding `"haiku"` to `ALLOWED_AGENT_MODEL_SHORT_NAMES` in
`src/devbench/constants.py`. Stick with the frontmatter defaults (`sonnet` or `opus`), or override
deliberately to a different mid-tier / large model when cost shaping is needed.

The block as written is a no-op; flip individual fields when quota
pressure makes the default untenable (e.g., drop the judges to `sonnet`
when opus quota is exhausted):

```yaml
agents:
  executor: sonnet
  blocker_resolver: opus
  manifest_amender: opus
  security_reviewer: opus
  task_factory: opus
  review_supervisor: sonnet
  iac_deploy_reviewer: opus
  review_team:
    code_reviewer: opus
    test_reviewer: opus
    doc_reviewer: opus
    changes_manifest: opus
```

Every field defaults to `null` when absent; a null (or absent) field leaves
the agent running on its frontmatter model. `DEVBENCH_AGENT_MODEL_<NAME>` (or
`JUDGE_AGENT_MODEL_<NAME>` for the four review judges, `review-supervisor`, and
`iac-deploy-reviewer`) env vars override the YAML on a per-call basis
(precedence: env > yaml > frontmatter). The optional IaC judge's env var is
`JUDGE_AGENT_MODEL_IAC_DEPLOY_REVIEWER`.

The shadow tree lives at `<workspace>/.devbench/plugin-shadow/devbench/`.
It is rebuilt from scratch on every `devbench start` (cheap: the agent `.md`
files are small real-file copies and every other file is a symlink) so it can
never drift from the current config. When the operator removes the `agents:`
block, the next launch detects "no overrides" and removes the shadow tree
before falling back to the canonical plugin path.

### Mode symmetry

The same module materialises the same path in both modes:

| Mode            | Entry point                       | Plugin path consumed by                          |
| --------------- | --------------------------------- | ------------------------------------------------ |
| Non-interactive | `devbench start`                  | `ClaudeAgentOptions(plugins=[{"path": <shadow>}])` |
| Interactive     | `devbench prepare-plugin-shadow`  | `claude --plugin-dir $(devbench prepare-plugin-shadow)` |

`devbench start` builds the shadow as a pre-flight; `prepare-plugin-shadow`
builds the same shadow and prints its path so the operator can pass it to
`claude --plugin-dir`. The shared implementation guarantees identical
behaviour across modes.

## Alternatives considered

### Option B: SDK option

Pass a `model_per_agent` mapping into the Claude Agent SDK at orchestrate-skill
invocation. **Rejected** because it works only in the non-interactive path
(the SDK constructor); the interactive `claude --plugin-dir` flow has no
matching option. Mode-asymmetric.

### Option C: per-dispatch wrapper

Intercept every `Agent(...)` call inside the orchestrate skill and swap the
model. **Rejected** because there is no stable per-call SDK hook; the
orchestrate skill is a plugin that the SDK runs, not a Python class we can
subclass. Same mode asymmetry as B.

### Option D: edit the canonical plugin

**Rejected** because the canonical install is shared with other workspaces
(marketplace install is treated as immutable) and a per-workspace YAML must
not mutate it. The shadow-plugin approach is the workspace-local variant of
this idea.

## Consequences

**Cost transparency.** Operators can opt every work agent up to opus when
sonnet quota is gone, or pin individual agents to sonnet or opus as cost
shaping requires. The override is auditable in
`backlog/config/devbench.yaml` -- it lives alongside the rest of the workspace
config.

**Marketplace immutability preserved.** The canonical install is never
mutated; only the workspace-local shadow tree changes. Multiple workspaces
running against the same canonical install can each have different overrides.

**Idempotent and safe under crash.** The shadow tree is rebuilt every launch
via temp-then-rename writes. A partial materialisation cannot leak into the
next launch because the directory is cleared first.

**Validation is fail-fast.** YAML load and env-var merge both re-validate
the supplied value against `use_bedrock`. Short names + Anthropic API ids
are accepted when `use_bedrock: false`; full Bedrock ARNs are required when
`use_bedrock: true`. A mismatch surfaces immediately at config-load time
rather than as a generic SDK error on first agent invocation.

**No backwards-compatibility hazard.** Workspaces without an `agents:` block
build no shadow and use the canonical plugin path -- bit-identical to
pre-feature behaviour.

**Sentinel-protected lifecycle.** Each `cmd_start` invocation that materialises
a shadow writes its own PID to `<workspace>/.devbench/plugin-shadow/devbench/.pid`.
`clear_shadow_plugin` reads the sentinel before deleting the tree: when the
recorded PID is alive, it raises `RuntimeError` naming the owning PID and
recommends stopping it first. This closes a real production race: under the
pre-sentinel design, a stray `devbench prepare-plugin-shadow` invocation
(triggered when the YAML's `agents:` block matched frontmatter defaults, so
`materialise_shadow_plugin` decided to clear the shadow and return None) could
delete the shadow files out from under a running orchestrator. The SDK
subprocess kept the plugin cached in memory, so tool execution continued, but
each hook fires as a fresh shell-script subprocess that reads `hook-logger.sh`
from disk -- after the shadow tree was gone, those scripts could not be
found and hook telemetry silently stopped. The sentinel makes the clear
operation fail-fast instead of silently corrupting state. Sentinel lives
inside the shadow tree so `rmtree` cleans it atomically on legitimate rebuilds.

## Rollout

Shipped as a single non-pushed branch on the canonical install plus a
cherry-pick onto the orchestrator's working branch
(`feat/issues-188-193`). No autonomous backlog item; the feature exists
outside the orchestrator's queue because operators need to *use* it on
the same workspace that the orchestrator is processing.

## References

* `src/devbench/plugin_shadow.py` -- materialiser implementation.
* `src/devbench/config_loader.py` -- `AgentModelsConfig` dataclass and parser.
* `src/devbench/config.py` -- `DEVBENCH_AGENT_MODEL_* (non-judge agents) / JUDGE_AGENT_MODEL_* (judge agents)` env var merge.
* `src/devbench/cli.py` -- `cmd_start` pre-flight and `cmd_prepare_plugin_shadow`.
* `tests/test_plugin_shadow.py` -- 100% line + branch coverage gate.
