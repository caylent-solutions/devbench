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
9. **Source task auto-unblocks** when every promoted dependency completes. The `BacklogManager._auto_requeue_marker_dependents` cascade (see the "Auto-requeue on proposal completion" section below) flips the source from `blocked` back to `in-queue` and writes an `[AUTO_UNBLOCKED]` audit comment. The orchestrator then re-claims the source naturally on the next `devbench next` iteration.

## Auto-requeue on proposal completion

Promoting a draft via `devbench promote-proposal <id>` (without `--no-dep-on-source`) does three writes on the source task:

1. Appends the draft ID to the source's `## Dependencies` table.
2. Writes a `[PROPOSAL_PROMOTED]` audit comment.
3. On the same comment line, writes a `[BLOCKED_PENDING_PROPOSAL] <draft-id>` state marker.

When the promoted draft later transitions to `done` via the standard lifecycle (`mark-done`), `BacklogManager._set_status` fires a reactive scan -- the sideways counterpart to the upward parent-rollup cascade. The scan finds every blocked task whose declared dependencies include the just-done task AND whose Comments section carries at least one `[BLOCKED_PENDING_PROPOSAL]` marker. If every marker ID is terminal (`done` or `declined`), the scan flips the source from `blocked` to `in-queue` and writes an `[AUTO_UNBLOCKED]` audit comment naming every marker ID.

The cascade is scoped narrowly by design:

| Source state | Marker present? | All marker IDs terminal? | Source auto-requeued? |
|--------------|-----------------|--------------------------|-----------------------|
| `blocked` | Yes | Yes | **Yes** |
| `blocked` | Yes | No (any marker is still open) | No (partial completion) |
| `blocked` | No | N/A | No (non-proposal block; operator must intervene) |
| non-`blocked` (in-queue / in-progress / in-review / done / declined) | Any | Any | No (scan only flips `blocked`) |

Unknown marker IDs (for example, a previously-promoted draft that was later archived via `reject-proposal`) would count as non-terminal if they remained on the source -- so per-draft `reject-proposal` strips the rejected draft's marker from the source before re-running the cascade. This closes a real regression: a reject that left the marker in place would have left the source `blocked` forever even after its siblings completed. See the "Rejecting a promoted draft strips the marker and re-invokes the cascade" subsection below and [ADR-08](adr/08-proposal-lifecycle-observability.md).

`--no-dep-on-source` skips the dependency wiring on the SOURCE task only. Every entry in `affected_task_ids` (see next subsection) still gets its marker + dep row. Use the flag when the promoted draft is independent of the source, not as a way to suppress peer-task wiring that an author explicitly listed.

See [ADR-07: Auto-requeue on proposal completion](adr/07-auto-requeue-on-proposal-completion.md) for rationale and the rejected alternatives.

### When to use `affected_task_ids` (ADR-10)

A single fix often unblocks more than one task. The canonical case is a pre-existing bug that breaks multiple work units simultaneously: fix the bug once, and every task that was waiting on it should unblock together. The proposal JSON carries an optional `affected_task_ids: list[str]` field for exactly this case:

```json
{
  "source_task_id": "E1-F1-S16-T1",
  "affected_task_ids": ["E1-F1-S15-T1"],
  "proposed_tasks": [
    { "suggested_id": "E1-F1-S16-T2", "title": "Fix 14 stale SystemExit test expectations", ... }
  ]
}
```

When `promote-proposal E1-F1-S16-T2` runs, the `[BLOCKED_PENDING_PROPOSAL] E1-F1-S16-T2` marker + Dependencies row is written on BOTH `E1-F1-S16-T1` (source) AND `E1-F1-S15-T1` (listed in `affected_task_ids`). The ADR-07 auto-requeue cascade then unblocks both of them when `E1-F1-S16-T2` reaches `done`.

Populate the field at authoring time only when you have direct evidence that the listed peer is waiting on the same bug (same failing test name, same production file in the blocker comment, same commit-hash as the root cause). Speculation produces spurious markers that the operator later has to unwind. When in doubt, leave the list empty; the operator can still wire additional targets post-promote via `devbench add-dep` (see [docs/cli-reference.md](cli-reference.md)).

Fail-fast: `promote-proposal` refuses to wire if ANY target in `[source_task_id] + affected_task_ids` is missing from the backlog index. The source is never left half-wired on a missing peer.

Backward-compatible: proposal JSONs without the field load as `affected_task_ids=[]` and follow the pre-ADR-10 1:1 wiring path.

See [ADR-10: Multi-target proposal wiring](adr/10-multi-target-proposal-wiring.md) for the full design.

### Rejecting a promoted draft strips the marker and re-invokes the cascade

Per-draft `reject-proposal <draft-id> --reason "..."` does the following, in order:

1. Archives the draft to `<workspace>/.devbench/rejected-proposals/<id>-<timestamp>.md`.
2. Removes the draft's row from `BACKLOG.md`.
3. Appends a `[PROPOSAL_REJECTED]` audit comment to the source task.
4. **Strips every `[BLOCKED_PENDING_PROPOSAL] <rejected-id>` line from the source's Comments section.** Without this, the cascade would see a marker pointing at an ID that no longer exists in the index, treat it as non-terminal (unknown), and never fire again.
5. **Invokes `_auto_requeue_marker_dependents`** with the rejected ID as the newly-terminal signal. The source is re-evaluated; if every remaining marker points at a terminal ID, the source auto-flips to `in-queue` with an `[AUTO_UNBLOCKED]` audit comment. If any remaining marker is still non-terminal, the source stays `blocked` (correct -- there is still pending work).

Net effect: the operator can safely reject one promoted draft out of many; the source does the right thing automatically. No manual marker editing, no manual `set-status in-queue`.

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

## Un-materialised proposal JSONs and the sweep

A proposal written via `write-proposal` sits in `.devbench/proposals/<source-id>.json` until `materialise-proposal` converts it into draft `.md` files. Two failure modes can leave a JSON in the "un-materialised" state:

- The safety guard above fired (prior proposed tasks still unresolved).
- The operator wrote the JSON by hand (for example, copying a blocker-resolver result) and never invoked `materialise-proposal`.

DevBench surfaces this state in three places:

- **`devbench status`** prints a persistent `Un-materialised  N` row (rendered at zero so regressions stay visible).
- **`devbench report`** renders a `Proposal JSONs pending materialisation (N)` panel listing one row per task in an un-materialised JSON. Omitted when zero.
- **`devbench list-proposals`** prefixes every entry with a `[state]` label -- `[unmaterialised]` / `[proposed]` / `[promoted]` / `[done]` / `[declined]` / `[rejected]` -- so every JSON entry is classified independently of the others.

### `devbench sweep-proposals`

At the top of every orchestrate loop iteration (SKILL step 0, before `validate-backlog`), the orchestrator runs `uv run devbench sweep-proposals`. The sweep walks every pending proposal JSON and best-effort materialises those whose proposed tasks are in un-materialised state. A proposal that remains blocked by the safety guard is skipped (not an error) so the sweep is idempotent.

Output per proposal:

- `sweep-proposals: materialised <source-id>: N task(s)` -- drafts successfully created.
- `sweep-proposals: skipped <source-id>: <reason>` -- safety guard or thin-approach refusal fired.
- `sweep-proposals: no-op <source-id>` -- every task already has a draft.

The operator can also run `devbench sweep-proposals` manually at any time.

### Auto-accepting proposals (ADR-11)

Some backlogs trust blocker-resolver + task-factory to produce sensible drafts every time and do not want a human in the loop on every promote. For those workspaces, opt in by setting `task_factory.auto_accept_proposals: true` in `backlog/config/devbench.yaml`:

```yaml
task_factory:
  enabled: true
  auto_accept_proposals: true
```

When the flag is `true`, `devbench sweep-proposals` calls `promote-proposal` automatically for every draft currently at `## Status: proposed`. Auto-promote runs on the standard SKILL step 0 tick, so no separate command or cron job is required. The flag is workspace-wide -- every proposal produced in this workspace gets auto-accepted, not individual proposals.

Behavioural details:

- **Default is `false`.** Omitting the key, or setting it explicitly to `false`, preserves the "human reviews every proposal" posture.
- **Idempotent.** A draft already past `PROPOSED` (promoted, done, declined, rejected) is skipped on the next sweep tick; no duplicate markers are written.
- **Legacy drafts get picked up too.** Flipping the flag on with PROPOSED drafts already waiting causes the next sweep tick to promote them. No extra operator action.
- **Audit signal.** Every auto-promoted draft's `[PROPOSAL_PROMOTED]` comment on the source task gains the suffix `(auto-accepted via task_factory.auto_accept_proposals=true)` between the description and the `[BLOCKED_PENDING_PROPOSAL]` marker. Reviewers of the work-unit file see at a glance that the tool, not a human, pressed the button.
- **Rejection still works.** An auto-promoted draft the operator later decides is wrong is rejected via the standard `reject-proposal <id> --reason "..."` flow; the ADR-07 marker-strip + cascade handle it exactly like any other reject.
- **Downstream behaviour unchanged.** ADR-07 auto-requeue, ADR-10 multi-target wiring on `affected_task_ids`, and the done-gate all continue to work exactly as they do under manual promote -- auto-accept shifts who calls `promote_proposal()`, not what the function does.

Sweep output gains an explicit auto-promote count when the flag is `true`:

```
sweep-proposals: materialised E1-F1-S2-T1: 2 new, 0 skipped (auto-promoted: 2)
```

When the flag is `false`, sweep output is byte-identical to today's.

**When NOT to use.** If your backlog's blocker-resolver sometimes produces drafts whose scope or wording needs operator review before they enter the queue, leave the flag off. Auto-accept removes the human-review step; it does not evaluate individual drafts for quality.

See [ADR-11](adr/11-auto-accept-proposals.md) for the full design and the alternatives that were rejected.

### Un-materialised form of `reject-proposal`

To discard an un-materialised JSON without ever producing drafts, use:

```
uv run devbench reject-proposal --unmaterialised <source-task-id> --reason "<message>"
```

The command archives the JSON to `.devbench/rejected-proposals/<source-id>-unmaterialised-<timestamp>.json` and writes a `[PROPOSAL_JSON_REJECTED]` audit comment on the source task. It refuses when any task in the JSON already has a materialised draft; in that case use per-draft reject for those drafts first.

### `materialise-proposal` is idempotent

Calling `materialise-proposal <source-id>` (or the sweep step 0 in the orchestrate SKILL) on the same JSON repeatedly is safe. The command classifies every `proposed_tasks[]` entry through `classify_proposed_task` before attempting any draft write:

| Task state | Action |
|------------|--------|
| `UNMATERIALISED` | Create draft `.md` and BACKLOG.md row. |
| `PROPOSED` / `PROMOTED` / `DONE` / `DECLINED` | Skip; leave existing draft untouched. |
| `REJECTED` (archive exists under `rejected-proposals/<id>-*.md`) | Skip; do NOT recreate the draft. The operator's rejection decision is preserved across sweeps, orchestrator restarts, and replay attempts. |

Practical consequences:

- **Rejected drafts do not resurrect.** A draft you reject today stays rejected on tomorrow's sweep tick, next orchestrator loop, or any future manual materialise call.
- **Retry is safe after a partial failure.** If a previous materialise run crashed halfway through (e.g. filesystem error on task 2 of 3), re-running materialise skips the already-materialised tasks and creates the rest.
- **Concurrent sweep + manual-materialise calls do not fight.** Whichever call creates a given draft first wins; the other classifies the same task as PROPOSED and skips.

CLI output distinguishes the two outcomes per task:
- `"materialised"` list contains paths that were just created on this call.
- `"skipped"` map associates every skipped task ID to its current classifier state.

See [ADR-09](adr/09-idempotent-materialise-proposal.md) for the rationale and rejected alternatives.

## Rejected proposals are archived, not deleted

`reject-proposal` moves the draft to `<workspace>/.devbench/rejected-proposals/<id>-<timestamp>.md` (or `<source-id>-unmaterialised-<timestamp>.json` for the un-materialised form). The BACKLOG.md row is removed (so `validate-backlog` stays clean), but the content is preserved for later review or recovery. Audit archived rejections directly with `ls <workspace>/.devbench/rejected-proposals/`.

## suggested_approach is a four-section contract

`blocker-resolver` produces the `suggested_approach` string for each proposed task; `task-factory` writes it verbatim as the draft's Description. To keep drafts production-ready at materialise time, `blocker-resolver` is required to emit AT LEAST four labelled sections:

1. **Context** (1-3 sentences): which source task, which production file, what the bug is, why the follow-up is needed.
2. **Scope**: exactly which files the follow-up will touch; whether production code, tests, or docs.
3. **TDD approach**: numbered RED / GREEN / REFACTOR steps with one sentence each.
4. **Verify**: the exact `make` commands the executor should run to confirm green.

`materialise_proposal` enforces a minimum length (160 characters) and refuses a proposal JSON whose `suggested_approach` is shorter -- the refusal names the source ID so the operator can re-run blocker-resolver with the tightened instructions. Every `Changes Manifest` row must have a concrete change description; a literal `TODO -- describe change` row also triggers refusal.

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

---

## When to use `--no-dep-on-source` (post-Backlog-A lesson)

`devbench promote-proposal <new-id>` defaults to wiring the source Task as a dependent on the new Task (i.e., `source.depends_on(new)`). This default fits the BLOCKER-resolver flow: when a Task hits a runtime block, the proposed new Task is the unconditional fix that must run first; the source then retries.

The default is WRONG when the proposed new Task is a TEST that validates the source Task's output. In that pattern, the source must run first (to produce the artifact), then the test verifies it. Wiring `source.depends_on(test)` creates a circular structural dependency: the source waits on the test, the test waits on the source's artifact to assert against.

### Pattern: test-validates-source

Symptoms:

- Proposed Task title starts with "Add tests/", "Verify", "Validate", "Assert", or similar.
- Proposed Task's `files_to_own` are all `tests/**` paths.
- Proposed Task's ACs assert observable state of an artifact owned by the source Task.

Action: pass `--no-dep-on-source` to `promote-proposal` AND wire the reverse dep:

```bash
devbench promote-proposal <new-test-id> --no-dep-on-source
devbench add-dep <new-test-id> <source-id>
```

Now the source runs first; the test runs after; no cycle.

### Worked example

Observed in production at `caylent-telemetry-spec/`: T8 owned `pyproject.toml` (build-backend + bandit config); T9 was proposed to add tests asserting `[tool.bandit].exclude_dirs` exists in pyproject.toml. Default `promote-proposal` wired `T8.depends_on(T9)`, which created a cycle (T9's test needed T8's edit; T8 was waiting on T9). Fix applied: removed T8's dep on T9; added T9's dep on T8 via `devbench add-dep E0-F1-S1-T9 E0-F1-S1-T8`. T8 ran first, applied the pyproject change; T9 ran after, asserted the change.

### Heuristic for `blocker-resolver` (proposal author)

When authoring a proposal where the new Task is test-validates-source, set a flag in the proposal JSON:

```json
"source_dep_direction": "test_validates_source"
```

`promote-proposal` honors the flag if present (auto-applies `--no-dep-on-source` and wires the reverse dep). When the flag is absent, the default direction is preserved (backward compatible). See [`plugin/devbench/agents/blocker-resolver.md`](../plugin/devbench/agents/blocker-resolver.md) for when the agent should set this flag.

## Spec-correction recovery tasks (issue #136)

When task-factory materialises a draft whose job is to remove or modify rows in another work-unit's Changes Manifest table, the draft's OWN Changes Manifest contains a **single row pointing at the work-unit markdown file being edited** -- e.g. `backlog/E2/E2-F3/E2-F3-S2/E2-F3-S2-T1.md`. Source files referenced inside that markdown's Manifest table (e.g. `pyproject.toml`, `Makefile`) are NOT listed in the draft's Manifest. Listing them re-introduces the very Manifest Conflict the recovery task was created to resolve.

The agent prompt (`plugin/devbench/agents/task-factory.md`) carries the rule + a self-correcting heuristic gated on Description / Approach verbs ("remove the row", "drop the entry", "correct the manifest table"). Regression coverage: `tests/test_integration/test_task_factory_spec_correction_scope.py`.

## Recovery-proposal dedup (issue #141)

When `blocker-resolver` would emit a recovery proposal,
`cmd_write_proposal` first computes a stable `fix_signature` -- a
SHA-256 hash over `(target_repo, sorted(files_to_own), normalised
intent_phrase)`. The intent phrase is extracted from the proposal's
Approach text via a regex table mapping verb patterns ("remove the
row", "untrack", "register marker", ...) to canonical tokens
(`remove-row`, `untrack`, `register-marker`, ...).

Before writing the JSON, `find_matching_pending_proposal` scans
`.devbench/proposals/*.json` for a non-terminal source task whose
proposal carries the same signature. On hit, `cmd_write_proposal`
calls `add-dep` to wire the new source task as an additional
dependency of the existing recovery task and emits a
`[RECOVERY_REUSED]` audit comment instead of writing a duplicate
JSON. On miss, the signature is stamped into the proposal JSON
before persistence so the next blocker that matches the same shape
hits the reuse path.

The dedup helpers live in
[`src/devbench/backlog/proposal.py`](../src/devbench/backlog/proposal.py).
Regression coverage:
[`tests/test_backlog/test_proposal_dedup.py`](../tests/test_backlog/test_proposal_dedup.py)
and
[`tests/test_backlog/test_proposal_scanner.py`](../tests/test_backlog/test_proposal_scanner.py).

## Cascade-depth limit (issue #144)

Recovery cascades (a proposal whose source task is itself the
materialisation of an earlier proposal) carry a `cascade_depth`
field equal to `parent_depth + 1`. The
`orchestrate.max_cascade_depth` YAML knob (default `3`, env override
`JUDGE_ORCHESTRATE_MAX_CASCADE_DEPTH`) caps recursion. When
`cmd_materialise_proposal` sees a proposal at the cap, it transitions
the source task to `NEEDS_OPERATOR_ATTENTION` instead of authoring
another draft. Default of 3 reflects observed cascade lengths in
production backlogs; raise per-workspace via YAML if your operator
loop genuinely needs deeper chains.

## Materialise-time placeholder rejection (issue #143)

`cmd_materialise_proposal` scans every proposal's
`proposed_tasks[*].suggested_approach` and rejects the materialisation
when any value is empty, whitespace-only, `TODO`, or `TBD`. The
rejection emits a structured error naming the offending tasks so the
operator (or the upstream `blocker-resolver` invocation) can supply a
real Approach before retrying. Concrete approach text -- even a single
sentence -- passes the gate.
