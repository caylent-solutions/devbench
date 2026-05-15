# ADR-25: Per-agent model overrides via workspace-local shadow plugin

**Status:** Accepted
**Date:** 2026-05-15

---

## Context

DevBench ships ten work agents under `plugin/devbench/agents/`. Each agent's
`.md` file declares its model in YAML frontmatter:

```yaml
---
name: executor
model: sonnet
---
```

Today nine of those agents are pinned to `sonnet`; `review-supervisor` is
pinned to `haiku`. The top-level orchestrate skill inherits the SDK caller's
model (set by the launcher via `JUDGE_CLAUDE_MODEL`, typically opus). That
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

Materialise a workspace-local **shadow plugin tree** whose agent `.md` files
are copies (rewritten) of the canonical agent files, while every other file
is a symlink back to the canonical. Both modes load this shadow tree instead
of the canonical when any per-agent override is configured.

Configuration surface: a new top-level `agents:` block in
`backlog/config/devbench.yaml`. The example below pins each field to its
**current frontmatter default** (nine agents on `sonnet`, `review_supervisor`
on `haiku`) so the block as written is a no-op; flip individual fields when
quota pressure makes the default untenable for a specific agent:

```yaml
agents:
  executor: sonnet
  blocker_resolver: sonnet
  manifest_amender: sonnet
  security_reviewer: sonnet
  task_factory: sonnet
  review_supervisor: haiku
  review_team:
    code_reviewer: sonnet
    test_reviewer: sonnet
    doc_reviewer: sonnet
    changes_manifest: sonnet
```

Every field defaults to `null` when absent; a null (or absent) field leaves
the agent running on its frontmatter model. `JUDGE_AGENT_MODEL_<NAME>` env vars override the YAML on
a per-call basis (precedence: env > yaml > frontmatter).

The shadow tree lives at `<workspace>/.devbench/plugin-shadow/devbench/`.
It is rebuilt from scratch on every `devbench start` (cheap because it is
mostly symlinks) so it can never drift from the current config. When the
operator removes the `agents:` block, the next launch detects "no overrides"
and removes the shadow tree before falling back to the canonical plugin path.

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
sonnet quota is gone, or pin individual agents (e.g. keep `task-factory` on
haiku for cheap recovery loops). The override is auditable in
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

## Rollout

Shipped as a single non-pushed branch on the canonical install plus a
cherry-pick onto the orchestrator's working branch
(`feat/issues-188-193`). No autonomous backlog item; the feature exists
outside the orchestrator's queue because operators need to *use* it on
the same workspace that the orchestrator is processing.

## References

- `src/devbench/plugin_shadow.py` -- materialiser implementation.
- `src/devbench/config_loader.py` -- `AgentModelsConfig` dataclass and parser.
- `src/devbench/config.py` -- `JUDGE_AGENT_MODEL_*` env var merge.
- `src/devbench/cli.py` -- `cmd_start` pre-flight and `cmd_prepare_plugin_shadow`.
- `tests/test_plugin_shadow.py` -- 100% line + branch coverage gate.
