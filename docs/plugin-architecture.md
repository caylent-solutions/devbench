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
│   ├── review-supervisor.md     ← DEPRECATED (ADR-28): inert stub; the orchestrate skill now dispatches the review_team agents directly
│   ├── security-reviewer.md     ← security review gate agent
│   ├── blocker-resolver.md      ← dependency blocker assessment agent + proposal emission after amendment reject
│   ├── manifest-amender.md      ← conditional judge for TDD GREEN manifest amendments
│   ├── task-factory.md          ← materialises blocker-resolver proposals into draft `proposed` work units
│   └── review_team/             ← review team agents dispatched directly by the orchestrate skill (ADR-28)
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
    ├── guard-git-stage.sh       ← blocks `git commit` with nothing staged AND `git add <path>` when path is outside the work unit's Changes Manifest
    ├── guard-work-unit-write.sh ← blocks Write/Edit to work unit .md files
    ├── guard-destructive-git.sh ← blocks direct destructive git operations from non-git-ops agents
    ├── guard-plugin-write.sh    ← "guard the guards": PreToolUse on Write/Edit; hard-denies (no role
    │                              bypass) writes to any plugin scripts/hooks dir, the
    │                              .devbench/plugin-shadow/ tree, .claude/settings*.json, and the
    │                              $BASH_ENV-named file. The guard layer must not be self-modifiable.
    ├── guard-review-supervisor-scope.sh
    │                            ← enforces read-only scope on the review-supervisor agent.
    │                              Blocks Bash mutations (git commit/push, rm, sed -i, > redirection, etc.)
    │                              AND blocks Agent-tool spawn of any subagent_type outside the
    │                              review_team allowlist (devbench:code_review, test_review, doc_review,
    │                              changes_manifest). Issue #118 -- closes the loophole where the
    │                              supervisor escalated to repo-mutation rights via subagent spawn.
    │                              Dormant after ADR-28: the supervisor is deprecated and never invoked.
    └── assert-tests-pass.sh     ← enforces test suite passes after Bash
```

---

## Agent Definition Pattern

Agents use `!` dynamic injection to call CLI utilities. Evidence is resolved before Claude sees the
agent content:

```markdown
---
name: code-reviewer
description: Reviews staged code changes against SOLID, DRY, fail-fast, and 12-factor standards
model: haiku
tools: Read, Bash, Glob, Grep
disallowedTools: Write, Edit
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
model: opus
tools: Read, Write, Edit, Bash, Glob, Grep
---

## Setup

Context: !`uv run devbench read-unit $ARGUMENTS`

The context above includes `repo_path`. Read `{repo_path}/CLAUDE.md` for project-specific
standards before starting. Use `repo_path` as your working root for all file operations.
```

---

## Model Per Role

Each agent specifies its own model in the frontmatter:

| Agent | Model | Reason |
|-------|-------|--------|
| `executor.md` | opus | Complex implementation work requiring full capability |
| `code-reviewer.md`, `test-reviewer.md`, `doc-reviewer.md`, `changes-manifest.md` | haiku | Structured evidence evaluation with constrained output |
| `security-reviewer.md` | sonnet | Security reasoning requires more capability than haiku |

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
          {"type": "command", "command": "bash ${CLAUDE_PLUGIN_ROOT}/scripts/guard-plugin-write.sh"},
          {"type": "command", "command": "bash ${CLAUDE_PLUGIN_ROOT}/scripts/guard-work-unit-write.sh"}
        ]
      },
      {
        "matcher": "Edit",
        "hooks": [
          {"type": "command", "command": "bash ${CLAUDE_PLUGIN_ROOT}/scripts/guard-plugin-write.sh"},
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

The Write/Edit matchers run `guard-plugin-write.sh` first -- the "guard the guards" hook. It hard-denies (exit 2, no role bypass) any Write/Edit whose target is a plugin `scripts/` or `hooks/` file, anything under `.devbench/plugin-shadow/`, a `.claude/settings*.json` file, or the file named by `$BASH_ENV`. The guard layer must not be editable by the agents it constrains, so unlike `guard-work-unit-write.sh` it ignores `DEVBENCH_AGENT_ROLE` entirely. See [architecture.md → Guard-the-guards](architecture.md#9-hooks-layer) for the protected-category table.

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
| `devbench log-tdd <id> <RED\|GREEN\|REFACTOR> <message>` | Append TDD phase entry to work unit |
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
├── .devbench/                   ← per-workspace runtime state (not part of plugin)
│   ├── review-round-token       ← file-based per-round review token (ADR-29); written by
│   │                              `devbench review-token new <id>`, removed by `... clear`,
│   │                              read by guard-verdict-format.sh as the H3 second factor
│   └── review-round-counters.json ← per-unit monotonic round counter backing the token's `r<n>`
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
