# DevBench Plugin Architecture

This document describes the structure and design of the DevBench Claude Code plugin. For the wider system context (orchestration loop, judge tier, multi-PR vs single-PR mode), see the [architecture overview](architecture.md). For the rationale behind this architecture and the tradeoffs considered, see [adr/01-claude-agent-sdk-with-plugins.md](adr/01-claude-agent-sdk-with-plugins.md). For the per-step lifecycle, see [execution-modes.md](execution-modes.md).

## Table of contents

- [Plugin Directory Structure](#plugin-directory-structure)
- [Agent Definition Pattern](#agent-definition-pattern)
- [Evidence](#evidence)
- [Setup](#setup)
- [Model Per Role](#model-per-role)
- [Hook Safety Model](#hook-safety-model)
- [Python CLI: Thin Bridge, Not Orchestration](#python-cli-thin-bridge-not-orchestration)
- [Interactive vs Automated Gap](#interactive-vs-automated-gap)
- [SDK Bootstrap](#sdk-bootstrap)
- [Workspace Layout](#workspace-layout)
- [Unchanged Modules](#unchanged-modules)

---

## Plugin Directory Structure

After the issue #224 split, plugin artifacts live under two marketplaces in this repo. The orchestrate plugin (this section) lives at `plugin/devbench-orchestrate/`; the authoring plugin lives at `plugin-authoring/devbench-authoring/`:

```text
plugin/devbench-orchestrate/
├── .claude-plugin/
│   └── plugin.json              ← manifest: name, description, version, keywords, repository, license, homepage
├── agents/
│   ├── executor.md              ← dev agent: implements work units via TDD
│   ├── review-supervisor.md     ← non-spawning aggregator: reads the 4 judges' persisted verdicts
│   ├── security-reviewer.md     ← security review gate agent
│   ├── blocker-resolver.md      ← dependency blocker assessment agent + proposal emission after amendment reject
│   ├── manifest-amender.md      ← conditional judge for TDD GREEN manifest amendments
│   ├── task-factory.md          ← materialises blocker-resolver proposals into draft `proposed` work units
│   └── review_team/             ← review team agents, invoked directly by SKILL.md as first-level sub-agents (ADR-33)
│       ├── code-reviewer.md     ← SOLID, DRY, fail-fast, 12-factor review
│       ├── test-reviewer.md     ← TDD discipline, test quality, assertions
│       ├── doc-reviewer.md      ← accuracy, completeness, sync with code
│       └── changes-manifest.md  ← actual changes vs. declared manifest
├── skills/
│   └── orchestrate/
│       └── SKILL.md             ← main backlog execution loop
├── hooks/
│   └── hooks.json               ← hook registrations (PreToolUse, PostToolUse)
└── scripts/
    ├── hook-logger.sh           ← logs every tool call to the hook log
    ├── guard-bash.sh            ← blocks dangerous Bash commands
    ├── guard-backlog.sh         ← blocks direct Bash writes to backlog/ tracking files
    ├── guard-verdict-format.sh  ← validates log-verdict argument format
    ├── guard-git-stage.sh       ← blocks `git commit` with nothing staged AND `git add <path>` when path is outside the
    │                              active work unit's Changes Manifest. The active unit resolves from CURRENT_WORK_UNIT_FILE
    │                              when set (tests, operator pins), else from the `.devbench/active-work-unit[-<session>]`
    │                              marker `devbench claim` writes (issue #336); enforcement only while the resolved unit
    │                              is `## Status: in-progress`
    ├── guard-work-unit-write.sh ← blocks Write/Edit to work unit .md files
    ├── guard-destructive-git.sh ← blocks direct destructive git operations from non-git-ops agents
    ├── guard-review-supervisor-scope.sh
    │                            ← enforces read-only, non-spawning scope on the review-supervisor agent.
    │                              Blocks Bash mutations (git commit/push, rm, sed -i, > redirection, etc.)
    │                              AND blocks every Agent-tool invocation unconditionally -- no allowlist
    │                              (ADR-33: review-supervisor never spawns the judges itself). Issue #118
    │                              -- closes the loophole where the supervisor escalated to repo-mutation
    │                              rights via subagent spawn.
    ├── assert-tests-pass.sh     ← enforces test suite passes after Bash
    └── assert-shared-file-impact.sh
                                 ← enforces the shared-file full-suite regression gate
                                   (caylent-solutions/devbench-internal-backlog#13):
                                   blocks when `devbench check-shared-file-impact` reports a diff
                                   touched a `repos.<repo>.shared_file_patterns` match AND the
                                   full-suite run introduced failures not present in the stored
                                   baseline.
```

---

## Agent Definition Pattern

Agents use `!` dynamic injection to call CLI utilities. Evidence is resolved before Claude sees the
agent content:

```markdown
---
name: code-reviewer
description: Reviews staged code changes against SOLID, DRY, fail-fast, and 12-factor standards
model: opus
tools: Bash
disallowedTools: Write, Edit, Read, Glob, Grep
---

## Evidence

- Context: !`uv run devbench read-unit $ARGUMENTS`
- Diff:    !`uv run devbench get-diff $ARGUMENTS`

[system prompt content...]

Write verdict: `uv run devbench log-verdict code-review $ARGUMENTS <pass|fail> "<feedback>"`
```

The executor agent receives `repo_path` from `read-unit` and reads the target repo's CLAUDE.md
explicitly (since it cannot rely on cwd auto-loading):

```markdown
---
name: executor
description: Implements a work unit following TDD, SOLID, and all project standards
model: sonnet
tools: Read, Write, Edit, Bash, Glob, Grep
---

## Setup

Context: !`uv run devbench read-unit $ARGUMENTS`

The context above includes `repo_path`. Read `{repo_path}/CLAUDE.md` for project-specific
standards before starting. Use `repo_path` as your working root for all file operations.
```

---

## Model Per Role

Each agent specifies its own model in the frontmatter. Current shipped defaults
(`plugin/devbench-orchestrate/agents/**/*.md` frontmatter, ADR-25):

| Agent | Model | Reason |
|-------|-------|--------|
| `executor.md` | sonnet | Complex implementation work; sonnet balances capability and cost for the highest-volume agent |
| `code-reviewer.md`, `test-reviewer.md`, `doc-reviewer.md`, `changes-manifest.md` | opus | Judgment-heavy structured evidence evaluation; wrong verdicts cascade into rework, so accuracy outweighs the inference-cost savings of a smaller model |
| `security-reviewer.md` | opus | Security reasoning for a highly regulated environment requires full capability |
| `review-supervisor.md` | sonnet | Non-spawning aggregator (ADR-33): reads the four judges' already-persisted verdicts and reports a consolidated result; no spawning and no independent judgment call |
| `manifest-amender.md`, `blocker-resolver.md`, `task-factory.md` | opus | Judgment-heavy, fire only on unhappy paths; a wrong amendment/proposal decision costs more than the inference savings of a smaller model |

**Haiku is rejected at config-load time for every work agent
(`caylent-solutions/devbench#198`).** The Claude Agent SDK was repeatedly
observed silently dropping the `Agent` tool from Haiku's tool list mid-session,
breaking parallel sub-agent dispatch and forcing the orchestrator to classify
the work unit as `RUNTIME_DEGRADATION`. `validate_agent_model_value()` in
`src/devbench/config_loader.py` hard-rejects any `agents:` override value
containing `haiku` -- short name, full Anthropic API id, or Bedrock ARN --
at config-load, with no operator-facing override path. This ban is
unconditional and is not softened by cost-optimization goals; see
[ADR-25](adr/25-per-agent-model-overrides.md) for the full rationale and
reproduction evidence.

---

## Hook Safety Model

`hooks/hooks.json` registers Claude Code hooks that fire in both interactive and automated sessions. Guard hooks exit with code 2 to block the tool call; stderr is shown to the agent as feedback. Hook command strings use `${CLAUDE_PLUGIN_ROOT}`, which Claude Code interpolates at runtime to the absolute path of the loaded plugin directory (the value passed to `--plugin-dir`).

The current `hooks.json` registers ten hook event types. Below shows the structure for the events that gate tool calls (PreToolUse and PostToolUse); the catch-all logger entries for the remaining events (`PostToolUseFailure`, `UserPromptSubmit`, `Stop`, `SubagentStart`, `SubagentStop`, `PreCompact`, `PermissionRequest`, `Notification`) follow the same pattern.

```json
{
  "hooks": {
    "PreToolUse": [
      {
        "matcher": "Bash",
        "hooks": [
          {"type": "command", "command": "bash ${CLAUDE_PLUGIN_ROOT}/scripts/hook-logger.sh"},
          {"type": "command", "command": "bash ${CLAUDE_PLUGIN_ROOT}/scripts/guard-bash.sh"},
          {"type": "command", "command": "bash ${CLAUDE_PLUGIN_ROOT}/scripts/guard-verdict-format.sh"},
          {"type": "command", "command": "bash ${CLAUDE_PLUGIN_ROOT}/scripts/guard-comment-format.sh"},
          {"type": "command", "command": "bash ${CLAUDE_PLUGIN_ROOT}/scripts/guard-git-stage.sh"},
          {"type": "command", "command": "bash ${CLAUDE_PLUGIN_ROOT}/scripts/guard-destructive-git.sh"},
          {"type": "command", "command": "bash ${CLAUDE_PLUGIN_ROOT}/scripts/guard-review-supervisor-scope.sh"}
        ]
      },
      {
        "matcher": "Write",
        "hooks": [
          {"type": "command", "command": "bash ${CLAUDE_PLUGIN_ROOT}/scripts/guard-work-unit-write.sh"}
        ]
      },
      {
        "matcher": "Edit",
        "hooks": [
          {"type": "command", "command": "bash ${CLAUDE_PLUGIN_ROOT}/scripts/guard-work-unit-write.sh"}
        ]
      }
    ],
    "PostToolUse": [
      {
        "matcher": "Bash",
        "hooks": [
          {"type": "command", "command": "bash ${CLAUDE_PLUGIN_ROOT}/scripts/hook-logger.sh"},
          {"type": "command", "command": "bash ${CLAUDE_PLUGIN_ROOT}/scripts/assert-tests-pass.sh"}
        ]
      }
    ],
    "Stop": [
      {
        "hooks": [
          {"type": "command", "command": "bash ${CLAUDE_PLUGIN_ROOT}/scripts/continue-orchestration.sh"}
        ]
      }
    ]
  }
}
```

For the full hook table (all ten event types and their scripts), see [Hooks layer](architecture.md#9-hooks-layer) in the architecture doc.

### The Stop hook circuit breaker

`continue-orchestration.sh` is the headline reliability feature: it prevents Claude Code from stopping mid-loop after context compaction by injecting a continuation instruction with the current task ID, file path, last action, and recommended next step. A circuit breaker with configurable thresholds (`stop_hook.max_blocks`, `stop_hook.window_seconds` in `devbench.yaml`) prevents infinite block-stop loops. See [architecture.md → Hooks layer](architecture.md#9-hooks-layer) for the full design.

Hook exit codes:
- **Exit 0** -- allow the tool call to proceed (or, for Stop hooks, allow the stop)
- **Exit 2** -- block the tool call; stderr shown to the agent as feedback (Stop hooks emit a JSON `{"decision": "block", "reason": "..."}` envelope to the same effect)

---

## Python CLI: Thin Bridge, Not Orchestration

All LLM logic lives in the plugin. The Python CLI is a thin, deterministic bridge that agents call
for repo-specific operations and structured I/O. It knows `devbench.yaml` and resolves repo paths;
agents do not.

```text
Agent calls: uv run devbench read-unit E0-F1-S1-T1
  ↓
Python CLI reads work unit → extracts "Target Repository: org/repo"
  ↓
Resolves checkout path from devbench.yaml
  ↓
Returns JSON: { "content": "...", "repo_path": "/workspace/target-repo", "unit_id": "E0-F1-S1-T1" }
```

Agents never know how repo paths are resolved. Multi-repo routing is invisible to the plugin.

### CLI Commands Used by Plugin Agents

| Command | Purpose |
|---------|---------|
| `devbench next` | Find next actionable unit, mark in-progress, return JSON `{id, repo_path}` |
| `devbench claim <id>` | Claim a unit (set to in-progress) |
| `devbench read-unit <id>` | Return JSON: work unit content + resolved `repo_path` + `unit_id` |
| `devbench get-diff <id>` | Run `git diff` in correct repo cwd, return output |
| `devbench run-tests <id>` | Run test suite in correct repo cwd, return output |
| `devbench log-verdict <judge> <id> <pass\|fail> [msg]` | Append structured verdict to work unit Comments |
| `devbench log-comment <agent> <id> <message>` | Append agent comment to work unit Comments |
| `devbench log-tdd <id> <RED\|GREEN\|REFACTOR> <message>` | Append TDD phase entry to work unit. `VALID_TDD_PHASES` has two orchestrator-only phases: `RED_OBSERVED` (written by `write_red_observed_entry`) and `GREEN_GREEN_OBSERVED` (written by `devbench green-green-check`); an agent-facing invocation naming either is rejected with exit 1 |
| `devbench green-green-check <id> <test_node_id> [...]` | Orchestrator-only: machine-observe that the named test(s) PASS before and after a `refactor` task's production change; writes `GREEN_GREEN_OBSERVED` on success |
| `devbench mark-done <id>` | Done-gate verification + status update |
| `devbench git-ops <id>` | Deterministic: branch → commit → push → PR → CI wait → merge |
| `devbench validate-backlog` | Check backlog integrity before each cycle |

---

## Interactive vs Automated Gap

With the plugin model, both modes load the same filesystem artifacts. The only difference is the
session entry point:

| Concern | Interactive | Automated SDK | Gap |
|---------|-------------|---------------|-----|
| Plugin loading | `--plugin-dir plugin/devbench` | `plugins=[{"type":"local","path":"plugin/devbench"}]` | One config line (intentional SDK design) |
| Agent/skill/hook behavior | Loaded from same filesystem artifacts | Same | None |
| Repo context resolution | `uv run devbench` CLI via env vars | Same | None |
| Safety hooks | `hooks/hooks.json` fires automatically | Same file fires when plugin loaded | None |
| CLAUDE.md standards | Loaded via `settingSources` | Loaded via `settingSources` | None |
| Target repo CLAUDE.md | Executor agent reads it explicitly via `repo_path` | Same | None |

---

## SDK Bootstrap

`uv run devbench start` (or `make start`) runs the orchestrate skill non-interactively via the
Agent SDK:

```python
"""Thin SDK entry point for automated devbench execution."""
import asyncio
from pathlib import Path
from claude_agent_sdk import query, ClaudeAgentOptions

PLUGIN_PATH = Path(__file__).parent.parent.parent.parent / "plugin" / "devbench"

async def run() -> None:
    async for message in query(
        prompt="Run the devbench:orchestrate skill to process the backlog until complete",
        options=ClaudeAgentOptions(
            setting_sources=["project"],
            plugins=[{"type": "local", "path": str(PLUGIN_PATH)}],
        ),
    ):
        pass

if __name__ == "__main__":
    asyncio.run(run())
```

The orchestrate skill and executor agent handle the full backlog lifecycle; no separate
orchestrator or executor Python modules exist.

---

## Workspace Layout

`devbench.yaml` lives at `$DEVBENCH_WORKSPACE_ROOT/backlog/config/devbench.yaml` -- the workspace root,
one level above the devbench tool repo. It is workspace-specific configuration (target repos,
branches, merge strategy, timeouts). The plugin is config-agnostic.

```text
$DEVBENCH_WORKSPACE_ROOT/
├── BACKLOG.md
├── CLAUDE.md
├── backlog/
│   └── config/
│       └── devbench.yaml        ← workspace config, not part of plugin
└── devbench/                    ← this repo (plugin source lives here)
    └── plugin/devbench-orchestrate/         ← Claude Code plugin
```

---

## Unchanged Modules

| Module | Status |
|--------|--------|
| `src/devbench/config.py` / `config_loader.py` | Unchanged |
| `src/devbench/backlog/` | Unchanged |
| `src/devbench/github/git_ops.py` | Unchanged |
| `src/devbench/github/security.py` | Unchanged |
| `CLAUDE.md` | Unchanged |
| `backlog/config/devbench.yaml` | Unchanged |
