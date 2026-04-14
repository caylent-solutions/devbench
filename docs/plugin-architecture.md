# DevBench Plugin Architecture

This document describes the structure and design of the DevBench Claude Code plugin. For the
rationale behind this architecture and the tradeoffs considered, see
[docs/adr/01-claude-agent-sdk-with-plugins.md](adr/01-claude-agent-sdk-with-plugins.md).

---

## Plugin Directory Structure

All plugin artifacts live under `plugin/devbench/` in the devbench repo:

```text
plugin/devbench/
├── .claude-plugin/
│   └── plugin.json              ← manifest: name, description, version
├── agents/
│   ├── executor.md              ← dev agent: implements work units via TDD
│   ├── review-supervisor.md     ← discovers and invokes all review_team agents in parallel
│   ├── security-reviewer.md     ← security review gate agent
│   ├── blocker-resolver.md      ← dependency blocker assessment agent
│   └── review_team/             ← review team agents invoked by review-supervisor
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
    ├── guard-git-stage.sh       ← blocks git commit when no files are staged
    ├── guard-work-unit-write.sh ← blocks Write/Edit to work unit .md files
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

`hooks/hooks.json` registers filesystem hooks that fire in both interactive and automated sessions
with identical behavior. Guard hooks exit with code 2 to block the tool call; stderr is shown to
the agent as feedback.

```json
{
  "hooks": {
    "PreToolUse": [
      {
        "matcher": "Bash",
        "hooks": [
          {"type": "command", "command": "${CLAUDE_SKILL_DIR}/../scripts/hook-logger.sh"},
          {"type": "command", "command": "${CLAUDE_SKILL_DIR}/../scripts/guard-bash.sh"},
          {"type": "command", "command": "${CLAUDE_SKILL_DIR}/../scripts/guard-verdict-format.sh"},
          {"type": "command", "command": "${CLAUDE_SKILL_DIR}/../scripts/guard-git-stage.sh"},
          {"type": "command", "command": "${CLAUDE_SKILL_DIR}/../scripts/guard-backlog.sh"}
        ]
      },
      {
        "matcher": "Write|Edit",
        "hooks": [
          {"type": "command", "command": "${CLAUDE_SKILL_DIR}/../scripts/guard-work-unit-write.sh"}
        ]
      }
    ],
    "PostToolUse": [
      {
        "matcher": "Bash",
        "hooks": [
          {"type": "command", "command": "${CLAUDE_SKILL_DIR}/../scripts/hook-logger.sh"},
          {"type": "command", "command": "${CLAUDE_SKILL_DIR}/../scripts/assert-tests-pass.sh"}
        ]
      }
    ]
  }
}
```

Hook exit codes:
- **Exit 0** — allow the tool call to proceed
- **Exit 2** — block the tool call; stderr shown to the agent as feedback

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

`devbench.yaml` lives at `$JUDGE_WORKSPACE_ROOT/backlog/config/devbench.yaml` — the workspace root,
one level above the devbench tool repo. It is workspace-specific configuration (target repos,
branches, merge strategy, timeouts). The plugin is config-agnostic.

```text
$JUDGE_WORKSPACE_ROOT/
├── BACKLOG.md
├── CLAUDE.md
├── backlog/
│   └── config/
│       └── devbench.yaml        ← workspace config, not part of plugin
└── devbench/                    ← this repo (plugin source lives here)
    └── plugin/devbench/         ← Claude Code plugin
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
