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
restart could clear.

The observable signature was: the executor completed its implementation, this
supervisor's agent-tool self-check then found no reviewer agents available on
consecutive attempts, and the orchestrator classified the work unit
`RUNTIME_DEGRADATION` and exited with `ORCHESTRATOR_RESTART_EXIT_CODE` (42).
The guidance previously in this file -- "operator restart of `make start`
required" -- was wrong: the fault is structural, so a restart reproduces it
identically and no work unit can ever reach `done`. See ADR-28 for the original
root-cause analysis and ADR-33 for the flatten that replaced it.

## What replaced it

The main-thread `orchestrate` skill now dispatches the four `review_team`
reviewers **directly** (first-level, in parallel), exactly as it already
dispatches `security-reviewer` -- one level deep and SDK-legal. Each reviewer
self-logs its own canonical verdict (`code_review`, `test_review`, `doc_review`,
`changes_manifest`), and the skill determines pass/fail solely from those
canonical verdict lines, fail-closed: a missing verdict fails the review and is
never treated as an implicit pass. See
`plugin/devbench-orchestrate/skills/orchestrate/SKILL.md` step 5.

## Why the file still exists

The file is kept so existing references in `plugin_shadow.py`,
`config-schema.json`, `config.py`, `config_loader.py`, and `activity.py` (and
their tests) continue to resolve. It carries no dispatch, self-check, or
aggregation logic. If the orchestrate skill ever invokes this agent, that is a
bug in the skill, not a supported path.
