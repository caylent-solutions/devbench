---
name: review-supervisor
description: DEPRECATED (ADR-28). The review fan-out is now performed directly by the orchestrate skill; this agent dispatches nothing and MUST NOT be invoked. Retained only for config / plugin-shadow / activity back-compatibility.
model: sonnet
tools: Bash
---

# DEPRECATED -- do not invoke (ADR-28)

This agent is **deprecated** and intentionally inert. It dispatches nothing,
reviews nothing, and writes no verdicts.

## Why it was retired

The review pipeline used to route the four `review_team` reviewers
(`code-reviewer`, `test-reviewer`, `doc-reviewer`, `changes-manifest`) through
this supervisor, which was itself a first-level sub-agent that then tried to
spawn those four via the Agent tool. The Claude Agent SDK forbids a sub-agent
from spawning its own sub-agents: a nested `Agent(...)` call silently no-ops, so
the fan-out never ran and the task stalled as a runtime degradation that no
restart could clear. See ADR-28 for the full root-cause analysis.

## What replaced it

The main-thread `orchestrate` skill now dispatches the four `review_team`
reviewers **directly** (first-level, in parallel), exactly as it already
dispatches `security-reviewer` and `iac-deploy-reviewer` -- one level deep and
SDK-legal. Each reviewer self-logs its own canonical verdict (`code_review`,
`test_review`, `doc_review`, `changes_manifest`) under the round-scoped token
file the skill writes via `devbench review-token new <id>` (ADR-29); the skill
determines pass/fail solely from those canonical verdict lines (fail-closed). See
`plugin/devbench-orchestrate/skills/orchestrate/SKILL.md` step 5.

## Why the file still exists

The file is kept so existing references in `plugin_shadow.py`,
`config-schema.json`, `config.py`, `config_loader.py`, and `activity.py` (and
their tests) continue to resolve. It carries no dispatch, self-check, or
aggregation logic. If the orchestrate skill ever invokes this agent, that is a
bug in the skill, not a supported path.
