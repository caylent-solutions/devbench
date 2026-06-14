# The Changes Manifest amendment workflow

This doc describes the runtime workflow (on by default; set `manifest_amendment.enabled: false` to opt out) that lets an executor update a work unit's `## Changes Manifest` during execution, when TDD GREEN exposes a production fix that was not pre-declared. For guidance on when to rely on this workflow versus pre-declaring files, see [docs/authoring-manifests.md](authoring-manifests.md).

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
- The task ID exists in the backlog and its current status is `in-progress`. **Skipped in operator mode** (`--operator-mode`) so the operator can amend tasks in any status.
- The request's `reason` appears in the backlog's configured `allowed_reasons`.
- Every path in `files_to_add` appears in the task branch's staged diff against base (no unrelated files smuggled in).
- No path in `files_to_add` is already present in the Changes Manifest (no silent duplicates). **Skipped in operator mode.**
- Every entry in `linked_acs` appears verbatim in the task's `## Acceptance Criteria` section. **Skipped in operator mode.**
- The count of amendments applied to this task in the current executor run is below `max_requests_per_execution`.

### Layer 2 -- LLM semantic judge

Implemented as `plugin/devbench-orchestrate/agents/manifest-amender.md`. Only invoked when Layer 1 passes. Answers three questions:

1. **Approach authorisation.** Does the work unit's Approach text authorise the *kind* of change the request describes? (Natural-language variation across backlogs makes this hard to encode deterministically.)
2. **Scope minimality.** Is the diff for each file in `files_to_add` minimal and scoped to the linked ACs, or does it sprawl into unrelated work?
3. **Justification coherence.** Does the `justification` accurately describe what the diff actually does?

If the answer to any question is unclear or negative, the judge rejects. It does not attempt to repair the request.

### Layer 3 -- deterministic post-check + atomic rollback

After the judge invokes `devbench apply-amendment`, the CLI removes any `files_to_remove` rows (fail-fast on an absent path), appends the `files_to_add` rows to the manifest, writes an audit comment, and performs the write atomically via temp-file-plus-rename. Immediately afterward the post-check runs:

- No em-dash (U+2014) introduced in the updated work-unit file.
- `devbench validate-backlog` still returns zero errors against the full backlog (catches BACKLOG.md drift, orphan references, status-summary count mismatches, and every other existing integrity rule).

If any post-check fails, the atomic rename is reversed and the work-unit file is restored to its pre-amendment content byte-for-byte. The task is left as it was before the amendment attempt, the request file is preserved so the caller (the amender agent) can log a REVIEW_FAIL verdict, and the orchestrator blocks the task.

## Flow

### Standard (executor) flow

1. Executor hits TDD GREEN and discovers a production fix not in the Changes Manifest.
2. Executor stages the fix in git and invokes `uv run devbench request-amendment <task-id>` with a JSON payload on stdin.
3. `request-amendment` runs the Layer 1 schema checks and persists the request to `$DEVBENCH_WORKSPACE_ROOT/.devbench/amendments/<task-id>.json`.
4. The orchestrator detects the pending request file after the executor returns and invokes the `manifest-amender` agent.
5. The agent reads the work unit, the staged diff, and the request JSON; decides `apply` or `reject`.
6. On `apply`: the agent runs `uv run devbench apply-amendment <task-id>`. CLI appends rows, writes audit, atomically commits to disk, runs Layer 3 post-check. On success the request file is deleted; on failure the write is rolled back and the agent logs REVIEW_FAIL.
7. On `reject`: the agent (a) first reverts every file listed in the pending request from the target repo (`git restore --staged`, `checkout --`, `clean -f --`) so stale staged edits do not leak into subsequent tasks, then (b) runs `uv run devbench reject-amendment <task-id> "<reason>"`. CLI writes a rejection audit comment, transitions the task to `blocked`, and **archives** the pending request to `<workspace>/.devbench/rejected-requests/<task-id>-<timestamp>.json` so `blocker-resolver` + `task-factory` (enabled by default, see [ADR-03](adr/03-task-factory.md)) can read it afterwards. The agent MUST verify the archive exists on disk before logging its verdict -- the manifest-amender prompt has a numbered execute-and-verify recipe with a final `test -f .devbench/rejected-requests/...` assertion that aborts the verdict if the side-effect did not land.
8. On post-Layer-3 success the standard review-supervisor runs against the updated manifest; the rest of the pipeline is unchanged.

### Operator flow (issue #242)

Use `request-amendment <task-id> --operator-mode` when a human operator needs to amend a work unit directly, bypassing the in-progress gate and the LLM judge. This is the recommended path for the `devbench-backlog-assistant` skills (rewrite-impossibility, refactor-target-repository, etc.).

1. Operator invokes `uv run devbench request-amendment <task-id> --operator-mode` with the full payload JSON (including `"operator_mode": true`) on stdin.
2. The CLI validates the payload schema (all eight operator-mode fields; see Appendix D-7 table above).
3. `request-amendment` applies the amendment **synchronously**: removes any `files_to_remove` rows, appends `files_to_add` rows, writes the operator-amendment audit entry, runs Layer-3 post-check. A `files_to_remove` path that is absent from the Changes Manifest fails fast with an actionable error before any write (no partial removal).
4. On Layer-3 success: the work-unit file is updated and the audit `[OPERATOR_AMENDMENT] applied; layer3=validate-backlog rc=0` appears in `## Comments`. No pending request file is written.
5. On Layer-3 failure: the work-unit file is restored to its pre-amendment content and the command exits with rc=1.

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

### Verification-directive amendments (`reason: verification_directive_defect`)

A second request shape repairs an objectively-defective `## Verification` directive without an operator stop-window. Accepted only when `manifest_amendment.allow_verification_directive_amendments` is true (the default); carries `verification_patches` instead of manifest rows (`files_to_add` must be empty):

```json
{
  "reason": "verification_directive_defect",
  "justification": "<which directive is defective and why>",
  "files_to_add": [],
  "linked_acs": ["<AC-ID of the directive>"],
  "verification_patches": [
    {
      "before": "<the EXACT defective directive line, verbatim>",
      "after": "<the corrected directive line>",
      "cited_done_units": ["<done unit id justifying the edit, when applicable>"],
      "evidence": "<tool-captured proof: the failing check + the ground-truth fact>"
    }
  ]
}
```

The three legitimate repair classes (the manifest-amender's VERIFICATION-DIRECTIVE RUBRIC rejects everything else): **(a) stale-assertion removal** -- the directive asserts repo state a DONE unit's landed change removed (citation required); **(b) syntactic defect fix** -- regex/quoting/path bug with intent preserved (e.g. a character class that cannot match a spec-required identifier); **(c) landed-rename alignment** -- an identifier renamed to match what a DONE sibling actually landed (citation required).

Deterministic guards in `apply-amendment` make weakening impossible regardless of what the judge approves: the `after` line must keep the **same AC ids, same `type=`, same `expect-exit`** as `before` (so a `command` can never become `deferred` and the gate semantics never loosen); the `before` line must exist verbatim in `## Verification`; every cited unit must be status `done`; the standard Layer 3 post-check (em-dash scan + full `validate-backlog`) runs with atomic rollback. The applied edit writes a `[VERIFICATION_AMENDMENT]` audit comment quoting before/after, citations, and evidence.

### Manifest-row-superseded amendments (`reason: manifest_row_superseded`)

A third request shape lets the **executor** self-remove a `## Changes Manifest` row whose file a DONE sibling renamed or deleted -- a row that otherwise deterministically re-fails git-ops staging (the file is absent on disk) and previously required an operator stop-window edit. Accepted only when `manifest_amendment.allow_manifest_row_superseded_amendments` is true (the default); carries `manifest_row_superseded_claims` instead of `files_to_add` (which must be empty):

```json
{
  "reason": "manifest_row_superseded",
  "justification": "<which row is stale and which DONE sibling superseded it>",
  "files_to_add": [],
  "linked_acs": ["<AC-ID this row served>"],
  "manifest_row_superseded_claims": [
    {
      "row_path": "<the EXACT ManifestRow.file value to remove>",
      "cited_done_units": ["<done unit id whose landed rename/delete explains the absence>"],
      "evidence": "<tool-captured proof: e.g. the git log line for the rename + the file-absent check>"
    }
  ]
}
```

Deterministic guards in `apply-amendment` (the CLI resolves the unit's target repo and staged-file set generically and threads both in) require, before any row is removed: **(a)** the row's file is **absent on disk** in the target repo (a row whose file still exists is never removed); **(b)** every cited unit is status `done` in the backlog index; **(c)** the staged diff **does not touch** the removed path (a path the executor is actively staging is not "superseded"). On success the row is dropped via `remove_rows`, the standard Layer 3 post-check (em-dash scan + full `validate-backlog`) runs with atomic rollback, and a `[MANIFEST_ROW_REMOVED]` audit comment naming the removed row(s), citations, and evidence is written. This mirrors the operator-path `files_to_remove` (below) but is gated by deterministic, never-weakening guards so the executor can apply it without an operator edit.

### Operator-mode fields (issue #242 / Appendix D-7)

When `request-amendment` is invoked with `--operator-mode`, the JSON payload may include seven additional optional patch fields. All fields default to their zero value when absent and are validated by `AmendmentRequest.from_dict`.

```json
{
  "reason": "tdd_green_production_fix",
  "justification": "<operator rationale>",
  "files_to_add": [],
  "linked_acs": [],
  "operator_mode": true,
  "files_to_remove": ["path/to/stale/file.py"],
  "target_repository": "new-org/new-repo",
  "description_patch": "<replacement description section text>",
  "approach_patch": "<replacement approach section text>",
  "title_patch": "New Task Title",
  "dod_patch": "<replacement definition-of-done text>",
  "section_patches": {
    "## Related Specifications": "<replacement body>"
  }
}
```

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `operator_mode` | `bool` | `false` | When `true`, bypasses the in-progress status gate and the LLM judge; applies synchronously. |
| `files_to_remove` | `list[str]` | `[]` | File paths to remove from the Changes Manifest table. Applied by both `apply-amendment` (approved-request path) and the operator path. Each path must already be a row in the manifest; an absent path fails fast with `AmendmentError` (no partial removal). Use this to drop a row whose file a DONE sibling renamed/deleted. |
| `target_repository` | `str` | `""` | When non-empty, replaces the work-unit's Target Repository value. |
| `description_patch` | `str` | `""` | When non-empty, replaces the entire Description section body. |
| `approach_patch` | `str` | `""` | When non-empty, replaces the entire Approach section body. |
| `title_patch` | `str` | `""` | When non-empty, replaces the work-unit title (the H1 heading). |
| `dod_patch` | `str` | `""` | When non-empty, replaces the entire Definition of Done section. |
| `section_patches` | `dict[str, str]` | `{}` | Maps arbitrary section headers to replacement body text. |

## Audit trail

Every amendment action leaves a timestamped entry in the work-unit `## Comments` section:

- On apply: `[YYYY-MM-DD HH:MM UTC] [agent/manifest-amender] [AMENDMENT_APPLIED] <reason>; added N file(s); justification: <...>`
- On reject: `[YYYY-MM-DD HH:MM UTC] [agent/manifest-amender] [AMENDMENT_REJECTED] <reason>; rejected: <...>`
- On operator-mode apply: `[YYYY-MM-DD HH:MM UTC] [operator] [OPERATOR_AMENDMENT] applied; layer3=validate-backlog rc=<n>; reason=<...>; justification: <...>`
- When an amendment removes rows, the audit entry (apply or operator-mode) carries a trailing `; [MANIFEST_ROW_REMOVED] <path>[, <path>...]` fragment naming every removed row.

The amender also logs a final `REVIEW_PASS` or `REVIEW_FAIL` verdict via `log-verdict manifest_amender` so the done-gate and review history are coherent. Operator-mode amendments do not go through the amender agent and therefore do not log a judge verdict.

### Rejection feedback persistence (issue #154)

Every rejection also writes a structured feedback JSON to `<workspace>/.devbench/amender-rejections/<task-id>-<n>.json` so the executor-feedback collector can ingest the rejection on the next retry. Schema:

```json
{
  "task_id": "EX-F1-S1-T1",
  "attempt": 1,
  "reason_category": "SCOPE",
  "reason_text": "amendment is out of scope for this task",
  "request": { /* original AmendmentRequest dict */ },
  "capped": false,
  "recorded_at": "2026-05-02T12:34:56Z"
}
```

`reason_category` is one of `SCOPE` / `APPROACH_AUTH` / `JUSTIFICATION_COHERENCE` / `PRE_FILTER` / `OTHER`. Rejection reasons that include any of the canonical category tokens are auto-classified by substring match; everything else falls back to `OTHER`. The amender prompt instructs the LLM to surface the canonical token inline so consumers always see a known category.

The directory layout mirrors `<workspace>/.devbench/ci-failures/` (used by `_handle_ci_failure`) and `<workspace>/.devbench/pr-bot-feedback/` (used by `_handle_pr_review_resolution`). The blocker-resolver / executor-feedback consumer reads all three paths via the same retry pipeline so every kind of late-stage rejection feeds the next executor invocation. The per-task attempt counter is bounded by `MAX_RETRY_ATTEMPTS`; once the cap is exceeded the file is still written but stamped `"capped": true` so consumers can detect budget exhaustion rather than silently dropping the record.

## What the amendment workflow does NOT do

- **It does not weaken `AC-FINAL-015`.** The Changes Manifest mismatch rule still fires; amendments are the only path to a manifest change, and every amendment is audited.
- **It does not let the executor edit work-unit files directly.** The guard hook `guard-work-unit-write.sh` continues to block Edit/Write on `backlog/**/*.md`. The CLI writes via subprocess, bypassing the hook the same way `log-verdict` has always worked.
- **It does not allow amendments outside the staged diff.** Every file in `files_to_add` must be in the staged diff against base; the pre-filter rejects attempts to include unrelated files.
- **It does not retry.** One pending request per task at a time; `max_requests_per_execution` caps the total per executor run. If the amender rejects, the task blocks for human review.
- **It does not cover validation-gate tasks.** Validation gates (empty Changes Manifest / Approach that forbids production-code changes) never stage a fix, so they never produce an amendment request for the amender to review. When a validation gate surfaces an out-of-scope production bug, the executor uses a separate path -- the BUG ESCALATION FOR VALIDATION GATES procedure in `plugin/devbench-orchestrate/agents/executor.md`, which writes a proposal JSON directly so task-factory can materialise follow-up work units. See [ADR-06: Validation-gate bug escalation](adr/06-validation-gate-bug-escalation.md) and [docs/task-factory.md](task-factory.md) for the full flow.

### Amendments vs. bug-escalation -- which applies?

| Scenario | Use this path |
|----------|---------------|
| The task's Approach authorises production fixes, and TDD exposed a bug in a file already in the Changes Manifest | Standard TDD: stage the fix, proceed to review. No amendment needed. |
| The task's Approach authorises production fixes, and TDD exposed a bug in a file NOT in the Changes Manifest | Amendment workflow: stage the fix, call `request-amendment`, let the amender decide. |
| The task's Approach forbids production fixes (validation gate), and verifications surfaced confirmed production bugs | Bug-escalation workflow: do NOT stage; call `write-proposal` with a decomposed proposal JSON and log NEEDS_ESCALATION. Task-factory materialises drafts independently. |

## Related code and tests

- `src/devbench/backlog/manifest.py` -- Changes Manifest parser and writer (Layer 3 mechanics); `append_rows` / `remove_rows` splice rows into / out of the manifest.
- `src/devbench/backlog/amendment.py` -- amendment request schema, PreFilter, apply/reject/operator lifecycle, post-check.
- `src/devbench/cli.py` -- `cmd_request_amendment` (variadic, `--operator-mode`), `cmd_apply_amendment`, `cmd_reject_amendment`.
- `plugin/devbench-orchestrate/agents/manifest-amender.md` -- Layer 2 judge prompt.
- `plugin/devbench-orchestrate/skills/orchestrate/SKILL.md` -- step 4b of the main loop.
- `tests/test_backlog/test_manifest.py`, `tests/test_backlog/test_amendment.py`, `tests/test_backlog/test_amendment_prefilter.py`, `tests/test_backlog/test_amendment_operator_mode.py`, `tests/test_cli_request_amendment.py`, `tests/test_integration/test_amendment_lifecycle.py` -- unit + integration coverage.

## Pre-conflict check (issue #137)

Before approving an amendment that adds a file to a work-unit's Changes Manifest, the manifest-amender scans every other work-unit's Manifest table for the same file path. The validator's `_check_manifest_conflicts` helper exposes this map (operationally: `uv run devbench validate-backlog 2>&1 | grep "Manifest conflict on '<file>'"`).

- **No conflict**: ALLOW.
- **Conflict task in terminal state (`done` / `declined`) AND new row is `Modify`**: ALLOW + auto-wire the dep edge by invoking `uv run devbench add-dep <source-task-id> <conflict-task-id>` (issue #142) before emitting the `apply` verdict; then log `[CONFLICT_AUTODEP]` naming the wired pair. If the `add-dep` invocation fails, emit `[CONFLICT_AUTODEP_FAILED]` with the underlying error -- the amendment still applies, but the operator is paged to wire the dep manually.
- **Otherwise**: REJECT with a structured reason naming the conflict task. The blocker-resolver / task-factory cascade then materialises a recovery task whose own Manifest is markdown-only per issue #136.

This pre-filter prevents new conflicts from being authored in the first place, which makes the recovery cascade an exception rather than the norm. Regression coverage: `tests/test_integration/test_manifest_amender_pre_conflict.py`.
