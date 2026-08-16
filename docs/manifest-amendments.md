# The Changes Manifest amendment workflow

This doc describes the runtime workflow (on by default; set `manifest_amendment.enabled: false` to opt out) that lets an executor update a work unit's `## Changes Manifest` during execution, when TDD GREEN exposes a production fix that was not pre-declared, or when a `doc_review` REVIEW_FAIL demands an out-of-Manifest documentation sync. For guidance on when to rely on this workflow versus pre-declaring files, see [docs/authoring-manifests.md](authoring-manifests.md).

## Opt in

Add to your backlog's `backlog/config/devbench.yaml`:

```yaml
manifest_amendment:
  enabled: true
  max_requests_per_execution: 2   # built-in default: one add + one row removal per run
  allowed_reasons:
    - tdd_green_production_fix
    - doc_sync_review_fix
```

`max_requests_per_execution` defaults to `2` so a unit correcting its Changes Manifest in both directions -- adding a file a review demanded and dropping a row that went stale -- can satisfy `AC-FINAL-015` within one executor run; see [docs/devbench-yaml-reference.md](devbench-yaml-reference.md).

Two amendment reasons are sanctioned by default:

- `tdd_green_production_fix` -- a production fix that TDD GREEN exposed, not pre-declared in the Changes Manifest. Unrestricted paths.
- `doc_sync_review_fix` (FR-11, db-327) -- an out-of-Manifest documentation sync mandated by a current-round `doc_review` REVIEW_FAIL. Restricted to documentation (`.md`) or documentation-pinning test paths only; the deterministic path guard rejects any other path with `Amendment reason 'doc_sync_review_fix' only permits documentation (.md) or documentation-pinning test paths, but these are not: <bad_paths>`.

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
- Every path in `files_to_remove` is currently declared in the Changes Manifest, and no path is in both `files_to_add` and `files_to_remove` (self-contradictory).
- **No path in `files_to_remove` has any change in the target repo** -- not staged, not unstaged, not untracked. See "Removing a stale row" below.

`PreFilter` runs from `request-amendment` **before the request is written**, so a request that cannot be approved never reaches disk and never occupies the single pending-request slot. Before this was wired up the class was reachable from no CLI path at all: every check above was dead code, and a backlog that narrowed `allowed_reasons` had the narrowing silently ignored.

### Layer 2 -- LLM semantic judge

Implemented as `plugin/devbench-orchestrate/agents/manifest-amender.md`. Only invoked when Layer 1 passes. Answers three questions:

1. **Approach authorisation.** Does the work unit's Approach text authorise the *kind* of change the request describes? (Natural-language variation across backlogs makes this hard to encode deterministically.)
2. **Scope minimality.** Is the diff for each file in `files_to_add` minimal and scoped to the linked ACs, or does it sprawl into unrelated work?
3. **Justification coherence.** Does the `justification` accurately describe what the diff actually does?

If the answer to any question is unclear or negative, the judge rejects. It does not attempt to repair the request.

### Layer 3 -- deterministic post-check + atomic rollback

After the judge invokes `devbench apply-amendment`, the CLI captures a `baseline_errors` snapshot from `devbench validate-backlog` BEFORE writing the amended file, then appends the rows to the manifest, writes an audit comment, and performs the write atomically via temp-file-plus-rename. Immediately afterward the post-check runs, baseline-relative (FR-10, db-312):

- No em-dash (U+2014) introduced in the updated work-unit file. This check is absolute, not baseline-relative: an amendment-introduced U+2014 always rolls back independent of `baseline_errors` (spec AC-22).
- `devbench validate-backlog` is compared against the pre-write `baseline_errors` snapshot. Only errors the amendment itself INTRODUCED (`errors - baseline_errors`) roll back the apply. Errors that already existed before the amendment and survive unchanged (`errors & baseline_errors`) are logged as a WARNING and never silently dropped, but they do NOT block the apply -- an unrelated pre-existing backlog error cannot be used to veto an otherwise-clean amendment.

If either post-check fails, the atomic rename is reversed and the work-unit file is restored to its pre-amendment content byte-for-byte. The task is left as it was before the amendment attempt, the request file is preserved so the caller (the amender agent) can log a REVIEW_FAIL verdict, and the orchestrator blocks the task.

## Flow

1. Executor hits TDD GREEN and discovers a production fix not in the Changes Manifest, OR a current-round `doc_review` REVIEW_FAIL demands an out-of-Manifest documentation sync.
2. Executor stages the fix (or doc sync) in git and invokes `uv run devbench request-amendment <task-id>` with a JSON payload on stdin, selecting `reason: "tdd_green_production_fix"` for a production fix or `reason: "doc_sync_review_fix"` for a `doc_review`-mandated documentation-only sync.
3. `request-amendment` runs the Layer 1 schema checks and persists the request to `$DEVBENCH_WORKSPACE_ROOT/.devbench/amendments/<task-id>.json`.
4. The orchestrator detects the pending request file after the executor returns and invokes the `manifest-amender` agent.
5. The agent reads the work unit, the staged diff, and the request JSON; decides `apply` or `reject`.
6. On `apply`: the agent runs `uv run devbench apply-amendment <task-id>`. CLI appends rows, writes audit, atomically commits to disk, runs Layer 3 post-check. On success the request file is deleted; on failure the write is rolled back and the agent logs REVIEW_FAIL.
7. On `reject`: the agent (a) first reverts every file listed in the pending request from the target repo (`git restore --staged`, `checkout --`, `clean -f --`) so stale staged edits do not leak into subsequent tasks, then (b) runs `uv run devbench reject-amendment <task-id> "<reason>"`. CLI writes a rejection audit comment, transitions the task to `blocked`, and **archives** the pending request to `<workspace>/.devbench/rejected-requests/<task-id>-<timestamp>.json` so `blocker-resolver` + `task-factory` (on by default, see [ADR-03](adr/03-task-factory.md) and [ADR-32](adr/32-task-factory-default-on.md)) can read it afterwards. The agent MUST verify the archive exists on disk before logging its verdict -- the manifest-amender prompt has a numbered execute-and-verify recipe with a final `test -f .devbench/rejected-requests/...` assertion that aborts the verdict if the side-effect did not land.
8. On post-Layer-3 success the standard review-supervisor runs against the updated manifest; the rest of the pipeline is unchanged.

## Amendment request JSON schema

The executor writes JSON with these fields. `request-amendment` fills in `task_id` and `requested_at` so the executor only provides the semantic parts. `reason` is one of the backlog's `allowed_reasons` -- by default `tdd_green_production_fix` (unrestricted paths) or `doc_sync_review_fix` (restricted to `.md` / documentation-pinning test paths; see the path guard in "The three-layer decision architecture" above).

```json
{
  "reason": "tdd_green_production_fix",
  "justification": "<one or two sentences describing what the test exposed and why the minimum change is necessary>",
  "files_to_add": [
    {"path": "<staged-file-path-relative-to-repo-root>", "change": "<one-line description>"}
  ],
  "files_to_remove": ["<declared-path-with-no-diff>"],
  "linked_acs": ["<AC-ID-1>", "<AC-ID-2>"]
}
```

`files_to_remove` is optional and defaults to empty, so requests written before it existed still parse. At least one of `files_to_add` / `files_to_remove` must be non-empty -- a request that changes nothing is rejected as a no-op.

### Removing a stale row

[`AC-FINAL-015`](acceptance-criteria-canonical.md) requires the Changes Manifest to match the files git changed *exactly* -- "no extra, no missing". A declared row whose file ends up with a zero-line diff is therefore a real violation, and the usual cause is benign: the work that row was written for landed under a sibling unit instead. `changes_manifest` fails the unit with `MANIFEST_MISMATCH` and prescribes an amendment; `files_to_remove` is how the unit complies. Until it existed the prescribed remedy was unimplementable, because a request could only ever add.

**The safety property: a row may only be dropped once its file has no changes of any kind.** The Manifest row is the only thing authorising a file to appear in the unit's commit, so if removal were permitted for a file with real changes, the work could leave the unit's reviewed scope entirely -- precisely the violation `assert_staged_matches_manifest` exists to stop. The check unions `git diff --cached`, `git diff`, and untracked files (`manifest.list_changed_files`), so an unstaged edit or a brand-new untracked file blocks removal just as a staged change does. A dirty path is refused with an error naming it.

Removals and additions apply inside the **same** atomic write and rollback envelope, so a Layer 3 post-check failure restores the Manifest whole rather than leaving it half-amended. The post-check itself needs no special casing for removals: it re-runs whole-backlog validation, so a removal that orphaned a source/test pair is caught by the existing source-test atomicity rule.

Removing every row is refused -- a unit that declares no files has nothing to verify its staged changes against, which is a manifest to rewrite by hand rather than arrive at by amendment. A path that is not declared is likewise an error rather than a silent no-op, so a typo surfaces instead of reporting success while the real stale row keeps failing the gate.

## Audit trail

Every amendment action leaves a timestamped entry in the work-unit `## Comments` section:

- On apply: `[YYYY-MM-DD HH:MM UTC] [agent/manifest-amender] [AMENDMENT_APPLIED] <reason>; added N file(s); justification: <...>`
- On apply with removals, the same row also names them: `... added N file(s); removed M row(s): <paths>; justification: <...>`. A dropped row changes what the unit is allowed to commit, so it is never invisible in the audit trail.
- On reject: `[YYYY-MM-DD HH:MM UTC] [agent/manifest-amender] [AMENDMENT_REJECTED] <reason>; rejected: <...>`

The amender also logs a final `REVIEW_PASS` or `REVIEW_FAIL` verdict via `log-verdict manifest_amender` so the done-gate and review history are coherent.

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
- **It does not let a removal carry work out of scope.** `files_to_remove` only drops rows whose files have no staged, unstaged, or untracked changes, so removal can never be used to move real work outside the unit's reviewed Manifest.
- **It does not retry.** One pending request per task at a time; `max_requests_per_execution` caps the total per executor run. If the amender rejects, the task blocks for human review.
- **It does not cover validation-gate tasks.** Validation gates (empty Changes Manifest / Approach that forbids production-code changes) never stage a fix, so they never produce an amendment request for the amender to review. When a validation gate surfaces an out-of-scope production bug, the executor uses a separate path -- the BUG ESCALATION FOR VALIDATION GATES procedure in `plugin/devbench-orchestrate/agents/executor.md`, which writes a proposal JSON directly so task-factory can materialise follow-up work units. See [ADR-06: Validation-gate bug escalation](adr/06-validation-gate-bug-escalation.md) and [docs/task-factory.md](task-factory.md) for the full flow.

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
- `plugin/devbench-orchestrate/agents/manifest-amender.md` -- Layer 2 judge prompt.
- `plugin/devbench-orchestrate/skills/orchestrate/SKILL.md` -- step 4b of the main loop.
- `tests/test_backlog/test_manifest.py`, `tests/test_backlog/test_amendment.py`, `tests/test_backlog/test_amendment_prefilter.py`, `tests/test_integration/test_amendment_lifecycle.py` -- unit + integration coverage.

## Pre-conflict check (issue #137)

Before approving an amendment that adds a file to a work-unit's Changes Manifest, the manifest-amender scans every other work-unit's Manifest table for the same file path. The validator's `_check_manifest_conflicts` helper exposes this map (operationally: `uv run devbench validate-backlog 2>&1 | grep "Manifest conflict on '<file>'"`).

- **No conflict**: ALLOW.
- **Conflict task in terminal state (`done` / `declined`) AND new row is `Modify`**: ALLOW + auto-wire the dep edge by invoking `uv run devbench add-dep <source-task-id> <conflict-task-id>` (issue #142) before emitting the `apply` verdict; then log `[CONFLICT_AUTODEP]` naming the wired pair. If the `add-dep` invocation fails, emit `[CONFLICT_AUTODEP_FAILED]` with the underlying error -- the amendment still applies, but the operator is paged to wire the dep manually.
- **Otherwise**: REJECT with a structured reason naming the conflict task. The blocker-resolver / task-factory cascade then materialises a recovery task whose own Manifest is markdown-only per issue #136.

This pre-filter prevents new conflicts from being authored in the first place, which makes the recovery cascade an exception rather than the norm. Regression coverage: `tests/test_integration/test_manifest_amender_pre_conflict.py`.
