# The Changes Manifest amendment workflow

This doc describes the opt-in runtime workflow that lets an executor update a work unit's `## Changes Manifest` during execution, when TDD GREEN exposes a production fix that was not pre-declared. For guidance on when to rely on this workflow versus pre-declaring files, see [docs/authoring-manifests.md](authoring-manifests.md).

## Opt in

Add to your backlog's `backlog/config/devbench.yaml`:

```yaml
manifest_amendment:
  enabled: true
  max_requests_per_execution: 1
  allowed_reasons:
    - tdd_green_production_fix
```

With `enabled: false` (the default, and the behavior of any backlog that has never configured this section), the workflow is inert: the executor does not emit amendment requests, the amender agent is never invoked, and the existing review pipeline runs exactly as before.

## The three-layer decision architecture

Amendments are processed through a three-layer sandwich. Structural invariants are enforced by deterministic code before and after the LLM judge runs; the judge's scope is confined to semantic questions that code cannot reliably answer.

### Layer 1 -- deterministic pre-filter

Implemented in `src/devbench/backlog/amendment.py::PreFilter`. Every rule below is unit-tested one-to-one. If any check fails the amendment is rejected with a specific reproducible error and the LLM is never invoked.

- The backlog config has `manifest_amendment.enabled: true`.
- The request JSON parses and matches the amendment schema (see below).
- The task ID exists in the backlog and its current status is `in-progress`.
- The request's `reason` appears in the backlog's configured `allowed_reasons`.
- Every path in `files_to_add` appears in the task branch's staged diff against base (no unrelated files smuggled in).
- No path in `files_to_add` is already present in the Changes Manifest (no silent duplicates).
- Every entry in `linked_acs` appears verbatim in the task's `## Acceptance Criteria` section.
- The count of amendments applied to this task in the current executor run is below `max_requests_per_execution`.

### Layer 2 -- LLM semantic judge

Implemented as `plugin/devbench/agents/manifest-amender.md`. Only invoked when Layer 1 passes. Answers three questions:

1. **Approach authorisation.** Does the work unit's Approach text authorise the *kind* of change the request describes? (Natural-language variation across backlogs makes this hard to encode deterministically.)
2. **Scope minimality.** Is the diff for each file in `files_to_add` minimal and scoped to the linked ACs, or does it sprawl into unrelated work?
3. **Justification coherence.** Does the `justification` accurately describe what the diff actually does?

If the answer to any question is unclear or negative, the judge rejects. It does not attempt to repair the request.

### Layer 3 -- deterministic post-check + atomic rollback

After the judge invokes `devbench apply-amendment`, the CLI appends the rows to the manifest, writes an audit comment, and performs the write atomically via temp-file-plus-rename. Immediately afterward the post-check runs:

- No em-dash (U+2014) introduced in the updated work-unit file.
- `devbench validate-backlog` still returns zero errors against the full backlog (catches BACKLOG.md drift, orphan references, status-summary count mismatches, and every other existing integrity rule).

If any post-check fails, the atomic rename is reversed and the work-unit file is restored to its pre-amendment content byte-for-byte. The task is left as it was before the amendment attempt, the request file is preserved so the caller (the amender agent) can log a REVIEW_FAIL verdict, and the orchestrator blocks the task.

## Flow

1. Executor hits TDD GREEN and discovers a production fix not in the Changes Manifest.
2. Executor stages the fix in git and invokes `uv run devbench request-amendment <task-id>` with a JSON payload on stdin.
3. `request-amendment` runs the Layer 1 schema checks and persists the request to `$JUDGE_WORKSPACE_ROOT/.devbench/amendments/<task-id>.json`.
4. The orchestrator detects the pending request file after the executor returns and invokes the `manifest-amender` agent.
5. The agent reads the work unit, the staged diff, and the request JSON; decides `apply` or `reject`.
6. On `apply`: the agent runs `uv run devbench apply-amendment <task-id>`. CLI appends rows, writes audit, atomically commits to disk, runs Layer 3 post-check. On success the request file is deleted; on failure the write is rolled back and the agent logs REVIEW_FAIL.
7. On `reject`: the agent (a) first reverts every file listed in the pending request from the target repo (`git restore --staged`, `checkout --`, `clean -f --`) so stale staged edits do not leak into subsequent tasks, then (b) runs `uv run devbench reject-amendment <task-id> "<reason>"`. CLI writes a rejection audit comment, transitions the task to `blocked`, and **archives** the pending request to `<workspace>/.devbench/rejected-requests/<task-id>-<timestamp>.json` so `blocker-resolver` + `task-factory` (opt-in, see [ADR-03](adr/03-task-factory.md)) can read it afterwards. The agent MUST verify the archive exists on disk before logging its verdict -- the manifest-amender prompt has a numbered execute-and-verify recipe with a final `test -f .devbench/rejected-requests/...` assertion that aborts the verdict if the side-effect did not land.
8. On post-Layer-3 success the standard review-supervisor runs against the updated manifest; the rest of the pipeline is unchanged.

## Amendment request JSON schema

The executor writes JSON with these fields. `request-amendment` fills in `task_id` and `requested_at` so the executor only provides the semantic parts.

```json
{
  "reason": "tdd_green_production_fix",
  "justification": "<one or two sentences describing what the test exposed and why the minimum change is necessary>",
  "files_to_add": [
    {"path": "<staged-file-path-relative-to-repo-root>", "change": "<one-line description>"}
  ],
  "linked_acs": ["<AC-ID-1>", "<AC-ID-2>"]
}
```

## Audit trail

Every amendment action leaves a timestamped entry in the work-unit `## Comments` section:

- On apply: `[YYYY-MM-DD HH:MM UTC] [agent/manifest-amender] [AMENDMENT_APPLIED] <reason>; added N file(s); justification: <...>`
- On reject: `[YYYY-MM-DD HH:MM UTC] [agent/manifest-amender] [AMENDMENT_REJECTED] <reason>; rejected: <...>`

The amender also logs a final `REVIEW_PASS` or `REVIEW_FAIL` verdict via `log-verdict manifest_amender` so the done-gate and review history are coherent.

## What the amendment workflow does NOT do

- **It does not weaken `AC-FINAL-015`.** The Changes Manifest mismatch rule still fires; amendments are the only path to a manifest change, and every amendment is audited.
- **It does not let the executor edit work-unit files directly.** The guard hook `guard-work-unit-write.sh` continues to block Edit/Write on `backlog/**/*.md`. The CLI writes via subprocess, bypassing the hook the same way `log-verdict` has always worked.
- **It does not allow amendments outside the staged diff.** Every file in `files_to_add` must be in the staged diff against base; the pre-filter rejects attempts to include unrelated files.
- **It does not retry.** One pending request per task at a time; `max_requests_per_execution` caps the total per executor run. If the amender rejects, the task blocks for human review.
- **It does not cover validation-gate tasks.** Validation gates (empty Changes Manifest / Approach that forbids production-code changes) never stage a fix, so they never produce an amendment request for the amender to review. When a validation gate surfaces an out-of-scope production bug, the executor uses a separate path -- the BUG ESCALATION FOR VALIDATION GATES procedure in `plugin/devbench/agents/executor.md`, which writes a proposal JSON directly so task-factory can materialise follow-up work units. See [ADR-06: Validation-gate bug escalation](adr/06-validation-gate-bug-escalation.md) and [docs/task-factory.md](task-factory.md) for the full flow.

### Amendments vs. bug-escalation -- which applies?

| Scenario | Use this path |
|----------|---------------|
| The task's Approach authorises production fixes, and TDD exposed a bug in a file already in the Changes Manifest | Standard TDD: stage the fix, proceed to review. No amendment needed. |
| The task's Approach authorises production fixes, and TDD exposed a bug in a file NOT in the Changes Manifest | Amendment workflow: stage the fix, call `request-amendment`, let the amender decide. |
| The task's Approach forbids production fixes (validation gate), and verifications surfaced confirmed production bugs | Bug-escalation workflow: do NOT stage; call `write-proposal` with a decomposed proposal JSON and log NEEDS_ESCALATION. Task-factory materialises drafts independently. |

## Related code and tests

- `src/devbench/backlog/manifest.py` -- Changes Manifest parser and writer (Layer 3 mechanics).
- `src/devbench/backlog/amendment.py` -- amendment request schema, PreFilter, apply/reject lifecycle, post-check.
- `src/devbench/cli.py` -- `cmd_request_amendment`, `cmd_apply_amendment`, `cmd_reject_amendment`.
- `plugin/devbench/agents/manifest-amender.md` -- Layer 2 judge prompt.
- `plugin/devbench/skills/orchestrate/SKILL.md` -- step 4b of the main loop.
- `tests/test_backlog/test_manifest.py`, `tests/test_backlog/test_amendment.py`, `tests/test_backlog/test_amendment_prefilter.py`, `tests/test_integration/test_amendment_lifecycle.py` -- unit + integration coverage.
