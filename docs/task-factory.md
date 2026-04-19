# Task Factory (Proposed Work Units)

The task-factory feature automates the "an operator must author new work units" step that follows a legitimate amendment rejection. When the `manifest-amender` correctly rejects an amendment whose changes describe real production fixes that fall outside the source task's scope, the orchestrator invokes the `blocker-resolver` + `task-factory` agents to generate draft work-unit `.md` files with status `proposed`. The human reviews, edits, and promotes each draft; the orchestrator continues on other actionable tasks until promotion.

See [ADR-03: Task factory](adr/03-task-factory.md) for the design rationale and alternatives considered.

## When task-factory runs

Two independent triggers fire task-factory. Both land a proposal JSON at `<workspace>/.devbench/proposals/<source-id>.json`; the SKILL branches on file existence, not on which trigger emitted the file.

**Trigger 1 -- amendment-reject path:** an executor requested an amendment during TDD GREEN, the amender rejected because the change belongs outside the source task's scope, and `blocker-resolver` decomposed the rejection into a proposal JSON. See [ADR-03: Task factory](adr/03-task-factory.md) for details.

**Trigger 2 -- validation-gate bug escalation:** the executor itself wrote the proposal JSON via `uv run devbench write-proposal` because the task is a validation gate (empty Changes Manifest / Approach forbids production fixes) that surfaced confirmed out-of-scope bugs. The executor bypasses blocker-resolver because it already has the bug diagnosis in hand; no amendment is ever requested. See [ADR-06: Validation-gate bug escalation](adr/06-validation-gate-bug-escalation.md) for details.

| Condition | Outcome |
|-----------|---------|
| Amendment request + approved | No factory run; the manifest is updated and reviews continue. |
| Amendment request + rejected + `task_factory.enabled: false` | No factory run; source task stays blocked as before. |
| Amendment request + rejected + `task_factory.enabled: true` | Blocker-resolver writes a proposal JSON; task-factory materialises drafts; source stays blocked pending promotion. |
| No amendment request + executor emits proposal JSON + `task_factory.enabled: false` | No factory run; audit comment notes the pending proposal for operator review. |
| No amendment request + executor emits proposal JSON + `task_factory.enabled: true` | Task-factory materialises drafts directly; source task's own reviews continue (it is NOT auto-blocked -- validation gates can still pass their own ACs even when they surface out-of-scope bugs). |

The feature is opt-in per backlog via `backlog/config/devbench.yaml`:

```yaml
manifest_amendment:
  enabled: true           # prerequisite

task_factory:
  enabled: true           # runs after amender rejects
```

`task_factory.enabled: true` requires `manifest_amendment.enabled: true`. Config validation fails loud otherwise.

**Trigger is file-based, not verdict-word-based.** The orchestrate skill branches on `test -f $JUDGE_WORKSPACE_ROOT/.devbench/proposals/<source-id>.json` to decide whether to invoke task-factory. Agent verdict words (`proposed` / `resolved` / `escalated` from blocker-resolver; `NEEDS_ESCALATION` from the executor) are audit-only. This is deliberate: the file existing proves a proposal was emitted; verdict words are summaries that cannot override disk state. If no proposal file exists after the executor or blocker-resolver runs, task-factory does NOT fire -- by design. Both triggers use the same file, so only one can fire per source task per run.

If the operator decides that some proposed drafts should never be promoted, they can run `devbench decline <id> --reason "<msg>"` instead of `reject-proposal`. `decline` flips the draft's status to `declined` (preserves the file and the audit trail), whereas `reject-proposal` archives the draft and removes the BACKLOG.md row. Use `decline` when you want the draft visible in the backlog's historical record as a considered-and-rejected candidate; use `reject-proposal` when the draft was clearly a misgeneration.

## The end-to-end flow

1. **Executor runs**, discovers a production bug during TDD, stages the fix, and emits an amendment request.
2. **Amender rejects** because the fix is outside the source task's scope (diagnosed on Approach authorisation, scope minimality, or justification coherence).
3. **Amender runs the git cleanup recipe** to revert the staged files (see [docs/manifest-amendments.md](manifest-amendments.md#reject-path-cleanup)) and invokes `uv run devbench reject-amendment`. The request is archived at `<workspace>/.devbench/rejected-requests/<id>-<timestamp>.json`.
4. **Orchestrator invokes `devbench:blocker-resolver`**. The agent reads the archived request + rejection rationale and decomposes the out-of-scope fixes into a structured proposal JSON. It calls `uv run devbench write-proposal <id>` with the JSON on stdin; the file lands at `<workspace>/.devbench/proposals/<id>.json`.
5. **Orchestrator invokes `devbench:task-factory`**. The agent calls `uv run devbench materialise-proposal <id>`, which:
   - Reads the proposal JSON.
   - Writes one draft `.md` per proposed task under `backlog/<epic>/<feature>/<story>/<task-id>.md`, each with `## Status: proposed` and an auto-generated header marker naming the source task.
   - Inserts a matching row in `BACKLOG.md` with the same `proposed` status.
   - Refreshes the Status Summary table.
6. **Orchestrator returns to step 1** (`devbench next`). Proposed tasks are NOT in the actionable set, so the loop picks another task; the source task stays blocked.
7. **Operator reviews proposals** at their convenience:
   - `devbench list-proposals` prints every pending proposal.
   - `devbench status` shows a `Proposed: N` count.
   - `devbench report` surfaces the Proposed panel at the bottom.
   - Editing the draft `.md` files directly tightens titles, acceptance criteria, or approach text before promotion.
8. **Operator promotes or rejects each draft**:
   - `devbench promote-proposal <id>` flips status to `in-queue`, appends the promoted task as a dependency on the source task, and adds an audit comment.
   - `devbench promote-proposal --all-from <source-id>` promotes every draft from a single proposal.
   - `devbench promote-proposal --no-dep-on-source <id>` skips the automatic dependency wiring.
   - `devbench reject-proposal <id> --reason "..."` archives the draft to `<workspace>/.devbench/rejected-proposals/<id>-<timestamp>.md`, removes the BACKLOG.md row, and appends a `[PROPOSAL_REJECTED]` audit comment to the source task.
9. **Source task auto-unblocks** when every promoted dependency completes. Orchestrator re-claims the source, the production bugs are fixed, and the source task's test suite now passes.

## Validation-gate bug escalation (direct executor emission)

Trigger 2 follows a shorter flow because the executor itself emits the proposal JSON:

1. **Executor identifies the task as a validation gate.** The Changes Manifest is empty (or absent) and the Approach explicitly forbids production-code changes (wording such as "run and report", "validation gate", "verify only"). The executor runs the prescribed verifications.
2. **Verifications surface out-of-scope bugs.** The executor confirms bugs that fall outside the source task's scope. Per the executor prompt's BUG ESCALATION FOR VALIDATION GATES section, it does NOT stage a fix and does NOT request an amendment.
3. **Executor writes the proposal JSON directly.** It allocates `suggested_id` values by scanning sibling task files in the target Story directory, builds the same JSON envelope blocker-resolver would (see the schema below), and pipes it into `uv run devbench write-proposal <source-id>` on stdin.
4. **Executor verifies the file landed.** `test -f $JUDGE_WORKSPACE_ROOT/.devbench/proposals/<source-id>.json` is the load-bearing check; the orchestrate skill branches on it at step 4a.
5. **Executor logs NEEDS_ESCALATION** naming the proposal path and the proposed task titles. The source task's review pipeline then runs normally at step 5 -- validation-gate escalation does NOT auto-block the source; its own ACs may still pass.
6. **Orchestrator detects the proposal at step 4a** and invokes `devbench:task-factory` directly, skipping blocker-resolver (the executor's proposal is already authoritative).
7. **Task-factory materialises the drafts** exactly as it does on the amendment-reject path: one `.md` per proposed task with `## Status: proposed`, one BACKLOG.md row per draft, Status Summary refreshed.
8. **Operator reviews and promotes** on their own cadence using the same `promote-proposal` / `reject-proposal` / `decline` commands as Trigger 1.

The key behavioural difference from Trigger 1 is that the source validation-gate task can still complete (`done`) if its own acceptance criteria passed -- for example, a gate whose AC is "43/46 scenarios pass with a documented diagnosis of the remaining 3" can ship while the three proposed fix tasks queue up as independent follow-ups. Trigger 1, by contrast, always leaves the source blocked because the amender rejected the in-scope implementation.

## Proposal JSON schema

Written by blocker-resolver, consumed by task-factory:

```json
{
  "source_task_id": "E0-F9-S2-T4",
  "generated_at": "2026-04-18T03:25:00Z",
  "rejection_reason": "Approach is validation-only; 4 unrelated production fixes out of scope",
  "proposed_tasks": [
    {
      "suggested_id": "E0-F1-S1-T6",
      "title": "guard against null gitdir + cache manifest config lookups",
      "files_to_own": ["src/example_app/core/project.py"],
      "linked_scenarios": ["SC-01", "SC-02", "SC-03", "SC-04"],
      "suggested_acs": [
        "AC-TEST-001 Reproduce SC-01 failure with gitdir=None and assert clean error.",
        "AC-CODE-001 Guard against gitdir=None in the env-building helper."
      ],
      "suggested_approach": "TDD GREEN: ..."
    }
  ]
}
```

Every field is required. Field-level validation runs on write (stdin → `write-proposal`) and on read (`materialise-proposal`).

## Concurrency-safe ID allocation

`src/devbench/backlog/proposal.py::allocate_next_ids` acquires an exclusive POSIX file lock on `<workspace>/.devbench/task-factory.lock` via `fcntl.flock` before scanning the Story directory and returning the next N free task IDs. Two concurrent factory runs cannot produce colliding IDs.

POSIX-only (Linux + macOS). DevBench's `.devcontainer` and CI targets are Linux; no Windows support is claimed.

## Skipping when prior proposals are unresolved

If the source task blocks a second time while previous proposals are still in `proposed` status, `materialise_proposal` raises `ProposalError("Skipped proposal generation -- unresolved proposed tasks already exist")`. This prevents duplicate proposals from piling up and forces the operator to engage with the existing drafts before new ones appear.

## Rejected proposals are archived, not deleted

`reject-proposal` moves the draft to `<workspace>/.devbench/rejected-proposals/<id>-<timestamp>.md`. The BACKLOG.md row is removed (so `validate-backlog` stays clean), but the draft content is preserved for later review or recovery. A future `devbench list-rejected-proposals` command can surface these without a migration.

## Safety properties

- **`proposed` drafts are inert.** `devbench next` filters actionable tasks to `in-queue` / `in-progress` only; proposed drafts never execute. A factory mistake cannot silently poison the active queue.
- **Proposals persist on disk.** Losing the terminal mid-flow leaves the proposal JSON and drafts intact; the next `devbench:orchestrate` resume picks up where it left off.
- **Dependencies wire automatically on promote.** The source task cannot re-run until every promoted dependency completes. No hand-wiring required.
- **Rejects are audited.** A `[PROPOSAL_REJECTED]` comment lands on the source task with the operator's reason so future readers understand why.
- **The orchestration loop is never blocked.** Source tasks stay blocked silently; the rest of the backlog continues. The operator decides their own review cadence.

## Related files

- Source: `src/devbench/backlog/proposal.py`, `src/devbench/cli.py` (`cmd_list_proposals`, `cmd_promote_proposal`, `cmd_reject_proposal`, `cmd_materialise_proposal`, `cmd_write_proposal`), `src/devbench/config_loader.py::TaskFactoryConfig`.
- Agents: `plugin/devbench/agents/blocker-resolver.md`, `plugin/devbench/agents/task-factory.md`.
- Orchestrator: `plugin/devbench/skills/orchestrate/SKILL.md` (step 4c).
- Tests: `tests/test_backlog/test_proposal.py`, `tests/test_integration/test_task_factory_lifecycle.py`.
- ADR: [ADR-03: Task factory](adr/03-task-factory.md).
