# Task Factory (Proposed Work Units)

The task-factory feature automates the "an operator must author new work units" step that follows a legitimate amendment rejection. When the `manifest-amender` correctly rejects an amendment whose changes describe real production fixes that fall outside the source task's scope, the orchestrator invokes the `blocker-resolver` + `task-factory` agents to generate draft work-unit `.md` files with status `proposed`. The human reviews, edits, and promotes each draft; the orchestrator continues on other actionable tasks until promotion.

See [ADR-03: Task factory](adr/03-task-factory.md) for the design rationale and alternatives considered.

## When task-factory runs

| Condition | Outcome |
|-----------|---------|
| Amendment request + approved | No factory run; the manifest is updated and reviews continue. |
| Amendment request + rejected + `task_factory.enabled: false` | No factory run; source task stays blocked as before. |
| Amendment request + rejected + `task_factory.enabled: true` | Blocker-resolver writes a proposal JSON; task-factory materialises drafts; source stays blocked pending promotion. |

The feature is opt-in per backlog via `backlog/config/devbench.yaml`:

```yaml
manifest_amendment:
  enabled: true           # prerequisite

task_factory:
  enabled: true           # runs after amender rejects
```

`task_factory.enabled: true` requires `manifest_amendment.enabled: true`. Config validation fails loud otherwise.

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

## Proposal JSON schema

Written by blocker-resolver, consumed by task-factory:

```json
{
  "source_task_id": "E0-F9-S2-T4",
  "generated_at": "2026-04-18T03:25:00Z",
  "rejection_reason": "Approach is validation-only; 4 unrelated production fixes out of scope",
  "proposed_tasks": [
    {
      "suggested_id": "E0-F9-S2-T6",
      "title": "project.py gitdir guard + manifest-config caching",
      "files_to_own": ["src/kanon_cli/repo/project.py"],
      "linked_scenarios": ["RI-05", "RS-05", "KI-04", "BV-07"],
      "suggested_acs": [
        "AC-TEST-001 Reproduce RI-05 failure with gitdir=None and assert clean error.",
        "AC-CODE-001 GitCommand guards against gitdir=None in _build_env."
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
