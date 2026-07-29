---
name: review-supervisor
description: NOT INVOKED (ADR-33). This agent has no role in the pipeline and MUST NOT be invoked. The file exists only so that per-agent model overrides keep resolving; see ADR-33 for why it was retained rather than deleted.
model: sonnet
tools: Bash
---

# NOT INVOKED -- no role in the pipeline (ADR-33)

Do nothing. Dispatch nothing. Write no verdicts. If you are reading this as an
invoked agent, the caller has a bug: return immediately and log nothing.

The orchestrate skill dispatches the four `review_team` judges directly as
first-level sub-agents and aggregates their verdicts itself, fail-closed. No
supervisor step exists between them.

This file is retained, rather than deleted, because `plugin_shadow.py` maps the
`agents.review_supervisor` config key to this path and fails fast if it is
missing -- so removing it would break every workspace whose `devbench.yaml`
pins a model for this agent. Deleting it is a config-deprecation cycle, not a
file removal. Rationale and the alternatives considered are recorded in ADR-33;
the original root-cause analysis of the spawn failure is in ADR-28.
