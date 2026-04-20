# ADR-08: Proposal-lifecycle observability, orchestration hygiene, and review-supervisor canonical judge names

**Status:** Accepted
**Date:** 2026-04-19

---

## Context

A 24-hour orchestration run against the kanon-migration backlog surfaced ten distinct defects, every one of them inside the same subsystem slice (proposal lifecycle + orchestration / review prompts + target-repo state management). They are recorded here because the fixes share helpers, reinforce each other, and are most coherent as a single PR.

1. **Un-materialised proposal JSONs are invisible in default reports.** `list-proposals` showed them. `devbench status` and `devbench report` did not. Five proposed tasks sat in `.devbench/proposals/*.json` with no backlog rows for hours. Operator could only discover them by running `list-proposals` explicitly.
2. **Un-materialised JSONs never auto-retry.** Task-factory's "skip when prior unresolved proposed tasks exist" safety guard raises `ProposalError` once; the JSON persists on disk; the operator is never prompted to re-invoke `materialise-proposal`.
3. **`list-proposals` conflates states.** It emits entries without per-task state labels. Un-materialised, proposed (draft exists), promoted, done, declined, and rejected-then-archived all render identically.
4. **No clean rejection path for un-materialised proposal JSONs.** `reject-proposal` was draft-level only (needed a task ID with a materialised `.md`). A JSON for which no drafts were ever created could only be disposed of by manual file deletion.
5. **Rejecting a promoted draft left a stale `[BLOCKED_PENDING_PROPOSAL]` marker on the source task.** The ADR-07 auto-requeue cascade treats unknown (archived / removed-from-index) IDs as non-terminal. Source has markers for `T3` + `T4`; operator rejects `T4`; source still has a marker pointing at an ID that no longer exists; cascade never fires; source stays `blocked` forever even after `T3` completes.
6. **Review-supervisor prompt used hyphenated judge names.** `plugin/devbench/agents/review-supervisor.md` instructed the supervisor to call `uv run devbench log-verdict <reviewer-name>` using the hyphenated agent frontmatter `name:` field (`code-reviewer`, `test-reviewer`, ...). `ALL_REQUIRED_JUDGE_NAMES` in `constants.py` expects underscored identifiers (`code_review`, `test_review`, `doc_review`, `changes_manifest`, `security_review`). Every task reaching `mark-done` failed the done-gate because `_last_round_all_passed` could not match the supervisor's verdict entries. Recurring defect that blocked every task.
7. **Task-factory auto-generated Description was too thin.** Materialisation blurred `suggested_approach` into the draft Description verbatim, so a one-paragraph RED/GREEN/VERIFY line landed as the entire Description and every Changes Manifest row read `TODO -- describe change`. Operators hand-edited every draft before promoting.
8. **Pre-existing target-repo pollution contaminated the next task.** A blocked task left files in the target working tree; the next task's executor either tried to stage them or tried to amend them into its scope. The git-ops safety rail caught commit-time pollution, but the waste (executor cycles + amendment rejections + extra blocked tasks) was paid upstream.
9. **Executor scope discipline under amendment-reject pressure was soft.** When a task's reviews fail and the executor retries, the executor sometimes tried to amend unrelated pre-existing bugs into scope. The amender correctly rejected, but the round cost real tokens.
10. **No operator guidance on retry-budget semantics.** When a task is blocked after exhausting `max_executor_retries`, the comment trail is correct but there was no FAQ entry explaining what that means or how to interpret it.

## Decision

Implement twelve small slices as one PR. Every slice has tests.

### Observability surfaces (slices A-D)

- Add `ProposalTaskState` enum + `classify_proposed_task(backlog_root, workspace_root, suggested_id)` helper in `src/devbench/backlog/proposal.py`. Single source of truth for the six states: `UNMATERIALISED`, `PROPOSED`, `PROMOTED`, `DONE`, `DECLINED`, `REJECTED`.
- `devbench status` adds a persistent `Un-materialised` row (rendered even at zero so regressions to zero stay visible).
- `devbench report` adds a `Proposal JSONs pending materialisation` panel rendered after the Proposed panel. Omitted when empty (same discipline as Proposed / Declined).
- `devbench list-proposals` gains a per-task `[state]` label column using the classifier output.

### Lifecycle cleanup (slices E-F)

- `devbench reject-proposal` grows a new form: `reject-proposal --unmaterialised <source-task-id> --reason "..."`. Refuses when any proposed task in the JSON already has a materialised draft (the operator must use per-draft reject for those first). Archives the JSON under `.devbench/rejected-proposals/<source-id>-unmaterialised-<timestamp>.json` and writes a `[PROPOSAL_JSON_REJECTED]` audit comment on the source task.
- Per-draft `reject-proposal` now strips any `[BLOCKED_PENDING_PROPOSAL] <rejected-id>` marker from the source's Comments section and invokes the ADR-07 cascade (`_auto_requeue_marker_dependents`). Rejecting a promoted draft can therefore auto-unblock the source if the remaining markers are all terminal.

### Orchestration hygiene (slices G-J)

- Review-supervisor prompt (`plugin/devbench/agents/review-supervisor.md`) replaces every hyphenated `<reviewer-name>` placeholder with the five canonical underscored names (`code_review`, `test_review`, `doc_review`, `changes_manifest`, `security_review`) plus a mapping table and a caution that the agent MUST NOT derive the judge name from the reviewer's frontmatter.
- Blocker-resolver prompt requires a four-section `suggested_approach` (Context / Scope / TDD approach / Verify) and a concrete example.
- Task-factory prompt documents two failure modes (thin `suggested_approach` and literal `TODO -- describe change` Changes Manifest rows) and states that materialise-proposal will refuse both.
- `src/devbench/backlog/proposal.py::materialise_proposal` enforces a minimum `suggested_approach` length (160 characters) and a no-`TODO -- describe change`-row contract, raising `ProposalError` with a clear message instead of writing thin drafts.
- Executor prompt adds a new pre-flight step 0: run `git status --porcelain=v1` in the target repo; restore / delete anything in the working tree that is NOT in the task's Changes Manifest. Amendment-scope discipline section forbids including pre-existing / unrelated dirty files in an amendment request.
- SKILL adds a step 0 (BEFORE `validate-backlog`) invoking `uv run devbench sweep-proposals`. The new CLI walks every pending proposal JSON and best-effort materialises those with UNMATERIALISED tasks, tolerating per-proposal `ProposalError` (skip + continue).

### Documentation (slices K + M)

- This ADR records the context and decision.
- `docs/faq.md` gains four Q&As: how to tell a proposal's state, how to throw away an un-materialised JSON, when a promoted-draft reject auto-unblocks the source, and retry-budget semantics.
- `docs/task-factory.md` extends the auto-requeue section for the reject-strips-marker behaviour, documents `--unmaterialised`, documents `sweep-proposals`, and documents the tightened `suggested_approach` requirement.
- `docs/architecture.md` adds two capability bullets: observability surfaces + orchestration-hygiene surfaces.

## Consequences

- **Un-materialised proposal JSONs are never invisible.** `status`, `report`, and `list-proposals` all surface them. `sweep-proposals` runs at the top of every orchestrate loop to best-effort materialise them. A JSON that remains pending after a sweep is genuinely blocked by the safety guard; the operator has the CLI to reject it cleanly.
- **The ADR-07 auto-requeue cascade is now correct after reject.** Rejecting a promoted draft strips its marker and re-evaluates the source, so a source whose other markers are terminal unblocks immediately. Rejecting a draft whose siblings are still non-terminal leaves the source blocked (same contract as before).
- **The review-supervisor defect that blocked every task is fixed** via prompt + regression test pin. A future drift that reintroduces `log-verdict code-reviewer` would fail `tests/test_plugin/test_agent_structure.py::TestReviewSupervisorCanonicalJudgeNames`.
- **Task-factory drafts arrive production-ready.** Thin `suggested_approach` is refused at materialise time; Changes Manifest rows are concrete. Operators no longer hand-edit drafts before promoting.
- **Target-repo pollution is cleaned by the executor before TDD RED.** Fewer amendment-reject cycles; reviewers see cleaner diffs.
- **Retry-budget semantics are now documented.** Operators know what `blocker: executor retry budget exhausted` means and how to recover.

## Alternatives considered and rejected

- **Four separate PRs (one per issue cluster).** Rejected. The fixes share helpers (`classify_proposed_task` is used by slices B / C / D; `reject_proposal` changes in E + F touch the same function body) and the same test batch covers them end-to-end. Splitting would create churn with no safety upside.
- **A new lifecycle state for "un-materialised".** Rejected. The state is already implicit in the JSON-vs-draft split; surfacing it with a classifier helper is simpler than teaching the status-enum machinery a new value.
- **A background timer-driven sweep of un-materialised JSONs.** Rejected. The operator-facing loop is already the natural tick; per-orchestrate-loop-iteration sweeps avoid hidden cron-like behaviour and keep the tool 12-factor.
- **Make the executor retry budget dynamically configurable.** Rejected. The budget lives in the SKILL prompt; operators can edit that one variable. No new config surface.

## Related files

### Python
- `src/devbench/backlog/proposal.py` -- `ProposalTaskState`, `classify_proposed_task`, `reject_proposal` extensions, `materialise_proposal` thin-approach refusal.
- `src/devbench/cli.py` -- `cmd_status`, `cmd_list_proposals`, `cmd_reject_proposal`, new `cmd_sweep_proposals`.
- `src/devbench/reporting/report.py` -- `_unmaterialised_proposals_listing`.

### Plugin prompts
- `plugin/devbench/agents/review-supervisor.md`
- `plugin/devbench/agents/blocker-resolver.md`
- `plugin/devbench/agents/task-factory.md`
- `plugin/devbench/agents/executor.md`
- `plugin/devbench/skills/orchestrate/SKILL.md`

### Tests
- `tests/test_backlog/test_proposal.py`
- `tests/test_cli.py`
- `tests/test_reporting/test_report.py`
- `tests/test_plugin/test_agent_structure.py`
- `tests/test_plugin/test_skill_structure.py`
- `tests/test_integration/test_task_factory_lifecycle.py`

### Docs
- `docs/adr/08-proposal-lifecycle-observability.md` (this file)
- `docs/task-factory.md`
- `docs/faq.md`
- `docs/architecture.md`
