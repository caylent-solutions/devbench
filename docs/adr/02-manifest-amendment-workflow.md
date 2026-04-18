# ADR-02: Changes Manifest amendment workflow

**Status:** Accepted
**Date:** 2026-04-18

---

## Context

The `changes_manifest` judge enforces that the set of files staged by the executor matches the work unit's `## Changes Manifest` table exactly (AC-FINAL-015). This invariant is one of DevBench's strongest guarantees against silent scope creep: if a judge approves something outside the declared manifest, the author's contract is broken and future audits become untrustworthy. The invariant is also enforced structurally by `guard-work-unit-write.sh`, which blocks the executor from editing work-unit Markdown files directly.

In practice, a specific authoring pattern collides with this invariant. Many work units follow TDD (RED / GREEN / REFACTOR) and their Approach sections contain wording like "if the test exposes a bug that needs a production fix, implement the minimum change in the relevant source file". Backlog authors writing these tasks frequently declare a test-only Changes Manifest -- because the task name and primary deliverable are "a test" -- without also pre-declaring the production file the Approach authorises touching. The executor follows the Approach, stages the production fix, and the `changes_manifest` judge rejects on AC-FINAL-015. The task blocks. The guard hook prevents the executor from amending its own manifest, so recovery requires a human to edit the backlog file and restart the task, or to create a new unblocker task that owns the production fix.

Over the course of one operational run this collision produced five blocked tasks in the same shape. Each block cost two task executions (the original plus a hand-rolled unblocker), doubled the judge-invocation cost for affected work, and left an ad-hoc dependency pattern in the backlog that is itself a cognitive load.

Three remedies were considered:

1. **Re-author every affected manifest up front.** Expand manifests to include likely production files. This is bounded and cheap but requires the author to predict every possible fix at authoring time. For tasks whose production surface is genuinely unknowable until the test runs, this produces either over-broad manifests (weakening the scope-control guarantee) or Pattern-2-style "stop and escalate" Approaches that block on every discovery.
2. **Automate the hand-rolled unblocker workflow.** A command that, on block, generates an unblocker task owning the out-of-manifest files and re-opens the blocked task with a dependency. Cheap to build because it formalises what humans were doing, but it cements the collision as a feature, permanently carries two executions per affected block, and leaves the underlying contract broken.
3. **Runtime amendment workflow with an audited escape hatch.** The executor requests a manifest change during TDD GREEN; a dedicated judge reviews the request; on approval the CLI updates the manifest atomically with deterministic post-checks and rollback.

## Decision

DevBench adds a two-layer solution:

- **Authoring guidance (Part A).** New docs at `docs/authoring-manifests.md`, `docs/manifest-amendments.md`, and `docs/faq.md` describe three patterns (pre-declared, test-only with explicit escalate, amendment-based) and give backlog authors a decision tree. The work-unit template at `docs/example-work-unit-template.md` ships the pre-declared pattern as the default. No runtime behavior change.
- **Runtime amendment workflow (Part B).** A three-layer sandwich wraps a narrow LLM judge with deterministic pre- and post-checks:
  1. **Layer 1 (deterministic pre-filter)** -- `src/devbench/backlog/amendment.py::PreFilter` runs every structural check before the LLM is invoked: config enabled, request schema valid, task is in-progress, reason allowed, files appear in staged diff, files not already in manifest, linked ACs exist, rate limit not exceeded.
  2. **Layer 2 (narrow LLM judge)** -- `plugin/devbench/agents/manifest-amender.md` answers only the three genuinely-semantic questions: does the Approach authorise the kind of change, is the diff minimal and scoped to linked ACs, does the justification coherently describe the diff. Structural facts are given, not re-litigated.
  3. **Layer 3 (deterministic post-check + atomic rollback)** -- `apply-amendment` appends rows, writes an audit comment, commits via temp-file-plus-rename, and runs em-dash + `validate-backlog` post-checks. Any post-check failure reverts the work-unit file atomically and logs REVIEW_FAIL.

The feature is opt-in per backlog via `backlog/config/devbench.yaml::manifest_amendment.enabled: true` and defaults off. Existing backlogs see no change until they explicitly enable it.

## Consequences

**Positive.**

- AC-FINAL-015 remains in force: amendments are the only path to a manifest change, every amendment is audited, and the three-layer check catches every structural failure mode. The invariant is no weaker than before.
- The collision pattern no longer produces permanent blocks. A legitimate TDD GREEN fix routes through the amender and the task proceeds on the first orchestration pickup, without a hand-rolled unblocker.
- Authors have explicit guidance for three patterns with a decision tree, so new tasks can be written correctly up front. The workflow is the safety net, not the primary path.
- Non-determinism is confined to the narrow semantic judgment. If the LLM over-approves a bad amendment, Layer 3 catches structural damage and rolls back; the worst-case LLM failure mode is over-rejection (annoying, safe). Model drift cannot corrupt state -- deterministic layers always fire.
- The request / apply / reject commands share the same subprocess-writes-work-unit pattern that `log-verdict` has used since day one; no new guard-hook carve-out is introduced.

**Negative.**

- DevBench now ships with a sixth agent (`manifest-amender`) and a new orchestrator branch. Both are explicitly documented in `docs/plugin-architecture.md` and `plugin/devbench/skills/orchestrate/SKILL.md`, and the orchestrator branch is only taken when an amendment request file exists.
- An approved amendment costs an extra judge invocation plus a second `validate-backlog` run. This is the price of a controlled manifest mutation. Pre-declaring (Pattern 1) avoids the cost and is the recommended default.
- Tests must maintain 100% coverage on the two new modules (`manifest.py`, `amendment.py`), enforced by a new Makefile target `test-coverage-new` that is part of `make validate`. Maintainers must understand every branch rather than add defensive code that can't be triggered.

## Alternatives considered

- **Let the executor self-amend.** Rejected: would weaken the guard hook's structural guarantee and bypass judge review. Every manifest mutation must be audited.
- **Repair in the review-supervisor.** Rejected: review-supervisor is a parallel invoker; mutating shared state from inside a parallel judge would make post-amendment judge verdicts non-deterministic across runs.
- **Pure deterministic decision.** Rejected: Approach authorisation is expressed in natural language and varies across backlogs; a string-matching heuristic either false-negatives (rejecting legitimate amendments) or false-positives (approving anything with the word "fix" in it). The LLM handles prose; code handles structure.

## Related files

- Source: `src/devbench/backlog/manifest.py`, `src/devbench/backlog/amendment.py`, `src/devbench/cli.py` (`cmd_request_amendment`, `cmd_apply_amendment`, `cmd_reject_amendment`), `src/devbench/config_loader.py` (`AmendmentConfig`).
- Agent: `plugin/devbench/agents/manifest-amender.md`.
- Orchestrator: `plugin/devbench/skills/orchestrate/SKILL.md` (step 4b).
- Executor prompt: `plugin/devbench/agents/executor.md` (TDD GREEN amendment path).
- Config: `src/devbench/config-schema.json` (`manifest_amendment` section), `sample-config.yaml` (opt-in example).
- Guard: `plugin/devbench/scripts/guard-verdict-format.sh` (KNOWN_JUDGES entry for `manifest_amender`).
- Tests: `tests/test_backlog/test_manifest.py`, `tests/test_backlog/test_amendment.py`, `tests/test_backlog/test_amendment_prefilter.py`, `tests/test_integration/test_amendment_lifecycle.py`.
- Docs: `docs/authoring-manifests.md`, `docs/manifest-amendments.md`, `docs/faq.md`, `docs/architecture.md`, `docs/plugin-architecture.md`, `docs/example-work-unit-template.md`, `docs/creating-specs-and-backlogs.md`.
