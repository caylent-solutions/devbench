# ADR-01: Claude Agent SDK with Plugin-Based Orchestration

**Status:** Accepted  
**Date:** 2026-03-09

---

## Context

DevBench originally used a Python-heavy orchestration model (referred to internally as main2) where
behavior was split across two parallel representations per judge:

- `prompts/*.txt` -- the LLM system prompt
- `judges/*.py` -- the Python class that gathered evidence and called the LLM

And two separate orchestration paths:

- `execution/orchestrator.py` -- for unattended (SDK) execution
- `orchestrator-prompt.md` -- loaded interactively via Claude Code session

This created compounding maintenance problems:

1. **Dual code paths.** Interactive and automated modes were implemented separately. Any behavior
   change required updates in both paths and manual verification that they stayed in sync.

2. **Dual representations per judge.** Each of the five judges existed as both a prompt file and a
   Python class. Adding a new evaluation dimension, changing evidence gathering, or updating a
   judge's instructions required touching both representations.

3. **No runtime safety interception.** Dangerous commands (e.g., `git push --force`, `rm -rf`)
   were blocked only by prompt instruction. If the LLM deviated from the instruction, nothing
   stopped the destructive command from executing. Hooks did not exist in this model.

4. **Limited observability on the executor.** The executor ran as a subprocess with restricted
   access to Claude's internal decision-making. Agent-level logging, tool call interception, and
   structured feedback injection were not possible.

5. **Hard-coded model per run.** All judges used a single globally configured model. There was no
   way to assign a cheaper or more capable model based on the complexity of each role.

---

## Decision

Restructure DevBench as a Claude Code plugin where all orchestration behavior lives in filesystem
artifacts -- agents (`.md`), skills (`SKILL.md`), and hooks (`hooks.json`) -- that are loaded
identically by both interactive (`claude --plugin-dir`) and automated (Agent SDK `query()` with
`plugins=`) sessions.

Python becomes a thin, stateless CLI bridge that agents call for repo-specific operations and
structured I/O. No LLM logic remains in Python.

**Key structural changes:**

| Old (main2) | New |
|-------------|-----|
| `judges/*.py` (5 Python judge classes) | `plugin/devbench/agents/review_team/*.md` (agent definitions) |
| `prompts/*.txt` (5 LLM system prompts) | Merged into each agent's `.md` file |
| `execution/orchestrator.py` | `plugin/devbench/skills/orchestrate/SKILL.md` |
| `execution/executor.py` | `plugin/devbench/agents/executor.md` |
| `orchestrator-prompt.md` | Deleted -- same `SKILL.md` serves both modes |
| No hooks | `plugin/devbench/hooks/hooks.json` + 8 guard scripts |
| One global model config | Per-agent `model:` frontmatter |
| `devbench review <id>` CLI command | Removed -- agents invoke directly |
| `devbench execute <id>` CLI command | Removed -- executor agent invoked by orchestrate skill |

---

## Rationale

### Single source of truth for both execution modes

With the plugin model, interactive and automated sessions load the same `SKILL.md`, the same agent
`.md` files, and the same `hooks.json`. There is no synchronization problem because there is nothing
to synchronize. The gap between the two modes reduces to a single config line in the SDK bootstrap:

```python
# Automated mode
ClaudeAgentOptions(plugins=[{"type": "local", "path": "plugin/devbench"}])
```

```bash
# Interactive mode
claude --plugin-dir plugin/devbench
```

### Deterministic safety at the tool-call layer

Hooks intercept tool calls before and after execution, independent of what the LLM was instructed
to do. `guard-bash.sh` blocks destructive commands at the OS level. `guard-work-unit-write.sh`
prevents agents from directly modifying work unit files, enforcing the invariant that only the CLI
can write structured state. This protection propagates into subagents automatically.

### Separation of evidence gathering from judgment

Evidence gathering (running `git diff`, executing `make test`, listing changed files) is
deterministic and belongs in the CLI. Judgment (pass/fail based on that evidence) belongs in the
LLM. The `!` dynamic injection in agent definitions makes this boundary explicit: evidence is
resolved before Claude sees the agent content.

### Per-role model assignment

Each agent file declares its model in YAML frontmatter:

```markdown
---
name: code-reviewer
model: sonnet
tools: Bash
---
```

Claude Code reads the `model:` field when invoking the agent and routes the inference call to that model. There is no per-role wiring in the Python code -- the routing is data-driven by the agent file itself.

This was not possible when a single global `JUDGE_CLAUDE_MODEL` applied to all roles. With the per-agent model field, the executor can use Opus (long context, complex implementation), the four review judges can use Sonnet or Haiku (shorter, structured evaluation), and the security reviewer can use Sonnet (security reasoning) -- all configured independently and changeable without code changes.

Agent files live at `plugin/devbench/agents/` (top-level agents: executor, review-supervisor, security-reviewer, blocker-resolver, manifest-amender, task-factory) and `plugin/devbench/agents/review_team/` (the four parallel review judges).

---

## Consequences

### Positive

- Interactive and automated modes share identical behavior without manual synchronization.
- Safety hooks propagate into subagents without additional configuration.
- Adding a new review dimension requires one new `.md` agent file -- no Python class, no prompt
  file, no CLI command.
- Per-agent model selection optimizes cost and capability by role.
- The orchestrate skill, executor agent, and review agents are readable and editable by anyone
  familiar with Claude Code, without Python knowledge.

### Negative

- The plugin model requires Claude Code to be installed. Environments that previously ran DevBench
  with only Python and the Anthropic SDK now also require Claude Code.
- The `!` dynamic injection syntax in agent frontmatter is a Claude Code-specific feature with no
  standard equivalent in other agent frameworks.
- Agent context windows are finite. Complex work units with large diffs can exceed what a single
  agent invocation handles, requiring evidence truncation (marked with `[... TRUNCATED]` markers).

### Neutral

- `src/devbench/config.py`, `config_loader.py`, `backlog/`, and `github/` are unchanged. The
  Python package remains the authoritative source for structured state management, config
  resolution, and git operations.
- `devbench.yaml` workspace configuration is unchanged and continues to live at
  `$JUDGE_WORKSPACE_ROOT/backlog/config/devbench.yaml`, outside the plugin.

---

## See also

- [architecture.md](../architecture.md) -- End-to-end system architecture, diagrams, and current gaps
- [plugin-architecture.md](../plugin-architecture.md) -- Implementation details of the plugin layer this ADR introduced
- [execution-modes.md](../execution-modes.md) -- Per-step lifecycle for both interactive and automated modes
