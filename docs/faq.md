# DevBench FAQ

Short answers to questions that come up repeatedly. For broader design context, see [docs/architecture.md](architecture.md).

## Contents

- [Changes Manifest and amendments](#changes-manifest-and-amendments)
- [Judges and reviews](#judges-and-reviews)
- [Lifecycle statuses](#lifecycle-statuses)
- [Task factory (proposed work units)](#task-factory-proposed-work-units)
- [Live activity dashboard](#live-activity-dashboard)

## Changes Manifest and amendments

### Why did my task block with "Changes Manifest mismatch"?

The `changes_manifest` judge rejects when the staged file set does not exactly match the manifest declared in the work unit. The most common cause is a task whose Approach authorises a TDD GREEN production fix but whose Changes Manifest only lists test files. The executor correctly staged the fix and correctly got rejected for staging a file outside the manifest.

Two resolutions:

1. **Re-author the work unit** so the manifest includes the production file up front, or so the Approach says "stop and escalate if a production fix is needed" rather than authorising the fix. See [docs/authoring-manifests.md](authoring-manifests.md) for the three patterns and when to pick each.
2. **Opt into the amendment workflow** for the backlog. When enabled, the executor can request a runtime amendment to the manifest during TDD GREEN; a dedicated judge reviews the request and, on approval, updates the manifest atomically before the standard review pipeline runs. See [docs/manifest-amendments.md](manifest-amendments.md).

### My executor needed a production fix during TDD -- what do I do?

Check whether the backlog has opted into the amendment workflow (`manifest_amendment.enabled: true` in `backlog/config/devbench.yaml`). If yes, the executor's prompt teaches it to stage the fix and invoke `uv run devbench request-amendment <task-id>` with a JSON payload on stdin; the orchestrator handles the rest. If no, the executor logs a `NEEDS_ESCALATION` comment and stops, so a human can choose whether to broaden the manifest, change the Approach, or opt into amendments.

### Can I edit the Changes Manifest after the task starts?

Not directly. The PreToolUse hook `guard-work-unit-write.sh` blocks every Edit/Write call to `backlog/**/*.md`. The only paths to a manifest change are:

- **For authors**, editing the work-unit file before the task is claimed (no hook interference, since the file is edited outside the orchestration flow).
- **For executors at runtime**, the amendment workflow described in [docs/manifest-amendments.md](manifest-amendments.md). The workflow goes through the `manifest-amender` judge and the `apply-amendment` CLI, both of which are audited; direct edits from the executor's tool calls remain blocked.

The guard and the amendment workflow together preserve `AC-FINAL-015` (the manifest-match invariant) while giving a controlled, reviewed path to a manifest change.

## Judges and reviews

### What judges run on every task?

Four review-team judges run in parallel via `review-supervisor` after the executor stages its files: `code_review`, `test_review`, `doc_review`, and `changes_manifest`. If they all pass, `security_review` runs sequentially. The `manifest-amender` judge runs conditionally before `review-supervisor` when an amendment request file is pending; if the backlog has not opted in, it is never invoked. The done-gate (`mark-done`) requires the four review judges plus security to have all passed in the most recent round.

### Why does my task keep failing with the same judge finding?

The executor retries up to `max_executor_retries` times on `REVIEW_FAIL`. Each retry re-runs the executor with the prior judge comments in its context. If the same finding appears across retries, either the executor is not reading the finding correctly (very rare; usually a prompt-alignment issue) or the finding reflects a real structural gap that the work unit cannot satisfy. In the second case the task blocks after the retry budget is exhausted, which is the correct outcome -- the work unit needs a human edit, not more executor attempts.

### My task blocked on reviewers saying N files are staged but `git diff --staged` shows only 1 -- why?

This was the 2026-04-20 kanon misread, fixed by ADR-12. If your backlog runs in `git_ops.single_branch: <branch>` + `git_ops.defer_pr: true` mode, versions of devbench before ADR-12 concatenated `git diff origin/<default>` into the output of `devbench get-diff`, which every review judge consumes. After N prior completed tasks have committed to the shared branch, that hunk contains N tasks' accumulated changes, and every judge correctly (by its prompt) reports "files staged outside the Changes Manifest" on subsequent tasks.

The fix is structural: `devbench get-diff` is now mode-aware. In defer_pr mode it emits only the current task's staged + unstaged changes (plus `git show HEAD` when the working tree is clean post-commit), never the accumulated branch-vs-default diff. Upgrade devbench to a version that carries ADR-12 and the misread stops occurring. Tasks already blocked on this misread can be unblocked with `devbench set-status <id> done` once you have verified via `git diff --staged --name-only` that the actually-staged set matches the Changes Manifest.

### My task blocked at git-ops with "staged files do not match Manifest declarations" -- what do I do?

The git-ops safety rail (`src/devbench/backlog/manifest.py::assert_staged_matches_manifest`) compares your Changes Manifest entries against `git diff --name-only` output, which is always **repo-relative**. The most common cause of this block is a **`checkout_directory` path-prefix** on one or more manifest rows. If your `backlog/config/devbench.yaml` sets `checkout_directory: <dir>` for the repo, manifest entries MUST NOT begin with `<dir>/`. Example: for a repo with `checkout_directory: example-repo`, `| \`example-repo/README.md\` | ... |` is wrong; `| \`README.md\` | ... |` is correct.

Fix: edit the task's `## Changes Manifest` section, drop the prefix from every offending row, then `devbench set-status <id> in-queue` to re-queue. The orchestrator will re-run the task cleanly.

Prevention: `devbench validate-backlog` now includes a path-prefix check (check 11) that surfaces this defect at authoring / startup time, before the executor does the work. Run `validate-backlog` after any bulk edit to the backlog. See [backlog-contract.md](backlog-contract.md) for the full rule.

## Lifecycle statuses

### What's the difference between Blocked and Declined?

- **Blocked**: the work is waiting on something external (a dependency, infra repair, human judgement) and WILL eventually progress once the blocker is resolved. The stop-hook circuit breaker has specific behaviour around blocked tasks.
- **Declined**: the work has been determined to NEVER be done -- scope rewritten, functionality being deleted instead, different task delivered the same outcome, or the risk / cost is no longer justified. Terminal state; a deliberate human decision. Captured via `devbench decline <id> --reason "<message>"`.

Declined children count as "terminal-complete" for parent (Story/Feature/Epic) auto-rollup -- a parent rolls to `done` once every child is either `done` or `declined`. Declined tasks are excluded from `tasks_remaining` and from projection ETAs, and surface in a dedicated `Declined (N):` panel in `devbench report`. See [docs/adr/05-declined-status.md](adr/05-declined-status.md).

### What happens when my validation-gate task finds bugs it can't fix?

A validation-gate task is one whose Changes Manifest is empty and whose Approach explicitly forbids production-code changes -- its job is to run existing verifications (test suite, lint, coverage, manual integration scenarios) and report. When such a gate surfaces a confirmed production bug that falls outside its scope, the executor prompt's BUG ESCALATION FOR VALIDATION GATES section instructs it to emit a proposal JSON directly via `uv run devbench write-proposal <source-id>` rather than staging any fix.

The orchestrate skill detects the proposal file at step 4a and invokes `devbench:task-factory` straight away (no blocker-resolver hop, because the executor already did the decomposition). Task-factory materialises one draft `.md` per proposed task with `## Status: proposed` and writes the matching BACKLOG.md rows. The source validation-gate task then proceeds to its own review pipeline at step 5 and may complete normally -- validation-gate escalation does NOT auto-block the source; the source's own acceptance criteria govern whether it passes.

This requires `task_factory.enabled: true` in `backlog/config/devbench.yaml`. If disabled, the executor still logs `NEEDS_ESCALATION` but no drafts are materialised; the orchestrator logs an audit comment naming the pending proposal for operator review. See [ADR-06: Validation-gate bug escalation](adr/06-validation-gate-bug-escalation.md) and [docs/task-factory.md](task-factory.md) for the full flow.

### Do blocked tasks auto-unblock when their promoted proposals complete?

Yes -- specifically, blocks caused by a `devbench promote-proposal` wiring auto-unblock when every promoted dep completes. The cascade is narrow by design: the scan only fires for tasks carrying a `[BLOCKED_PENDING_PROPOSAL]` marker comment (written by `promote-proposal` at wiring time). Blocks from other causes -- review failures, git-ops errors, operator intervention -- stay manual and need `devbench set-status <id> in-queue` from the operator.

The sequence: operator runs `devbench promote-proposal <draft>`. The command appends the draft ID to the source's Dependencies table and writes `[BLOCKED_PENDING_PROPOSAL] <draft>` on the source. Later, when the draft passes reviews and `mark-done` runs, `BacklogManager._set_status` fires a reactive scan: for every blocked task whose declared deps include the just-done task AND whose markers are all pointing at terminal IDs (`done` or `declined`), the source auto-flips to `in-queue` with an `[AUTO_UNBLOCKED]` audit comment. See [ADR-07](adr/07-auto-requeue-on-proposal-completion.md) and [docs/task-factory.md](task-factory.md) for the full mechanism.

Partial completion (one of two promoted drafts is done, the other is still in-queue) keeps the source blocked. Unknown marker IDs (drafts that were later rejected and removed from the index) count as non-terminal, so a stray rejected-proposal marker can never trigger a spurious auto-requeue.

### Why didn't task-factory fire after my amendment was rejected?

Three possible causes:

1. **The amender never executed `reject-amendment`.** The old prompt treated the CLI block as reference; the current prompt requires the amender to run it AND verify the archive file exists before logging the verdict. If you see a rejection comment on the work unit but no file at `<workspace>/.devbench/rejected-requests/<id>-*.json`, the amender hit this pre-tightening bug. Manually recreate the archive from the rejection comment's content, then re-run `devbench:blocker-resolver` for that task.
2. **The blocker-resolver classified the issue as `escalated` instead of `proposed`.** Step 4c in the orchestrate SKILL now branches on `.devbench/proposals/<id>.json` FILE EXISTENCE (not the verdict word), and the blocker-resolver prompt mandates emitting a proposal JSON whenever a rejected-requests archive exists. If the proposal file is missing, task-factory doesn't fire -- by design. Check whether an archive existed when the resolver ran.
3. **`task_factory.enabled` is false.** The whole loop is opt-in. Confirm `backlog/config/devbench.yaml` has `task_factory.enabled: true` AND `manifest_amendment.enabled: true`.

### Why did the orchestrator halt instead of continuing?

The orchestrator halts ONLY when (a) `devbench next` returns `ALL_DONE` / `NO_ACTIONABLE` or (b) the stop-hook circuit breaker trips. Per-task failures (git-ops orphan branch, manifest-scope violation, push rejection, executor retry budget exhausted, amendment rejected without a proposal) MUST mark the task `blocked` and return to step 2 -- they never halt the whole loop. If your orchestrator stopped on a per-task failure, the agent was running an older skill prompt; reinstall the plugin (`claude plugin install devbench --scope user`) to pick up the tightened halt-discipline rules.

### Why did my `log-comment` call get rejected with "forbidden control-language phrase"?

The `guard-comment-format.sh` PreToolUse hook rejects `uv run devbench log-comment` calls whose message body contains imperatives directed at the orchestrator's loop: `halt orchestration`, `halting orchestration`, `halt the loop`, `halt loop`, `stop the loop`, `stop orchestration`, `abort orchestration`, `operator action required`, `resume orchestration once`, `emergency halt`, `do not continue` (case-insensitive substring match).

The rule exists because subagent prose is diagnostic narration, not loop control. The orchestrator's loop is owned exclusively by `uv run devbench next` and the stop-hook circuit breaker; any text in a subagent log-comment that reads as a halt directive is a prompt-injection vector that can make the orchestrator stop even when the SKILL halt-discipline rule says to continue. Prior incident: a downstream executor logged a comment opening with "Halting orchestration: ..." and the orchestrator LLM obeyed the prose instead of continuing the loop, leaving the orchestration idle for hours.

Fix: rewrite the message describing the condition factually. Keep the diagnostic content; drop the imperatives. Example replacements:

- `Halting orchestration: <X>` -> `<X> detected: ...`
- `Operator action required: <Y>` -> `Recommended fix: <Y>`
- `Resume orchestration once <Z>` -> `Source task remains blocked until <Z>`

Do NOT add `# noqa`-style bypass annotations or attempt to evade the hook -- that violates the prohibited-bypass rule in CLAUDE.md. The hook is one of three defenses in depth; the other two are the SKILL halt-discipline section and the executor prompt's `COMMENT LANGUAGE DISCIPLINE` section.

## Task factory (proposed work units)

### My task got blocked with proposals -- what do I do?

The `task-factory` generated one or more draft work units for the production fixes the amender flagged as out-of-scope. Each draft has `## Status: proposed` and a matching row in `BACKLOG.md`; the drafts are inert until you promote them. Run `devbench list-proposals` to see what was proposed, then for each draft you accept:

1. Open `backlog/<epic>/<feature>/<story>/<id>.md` and tighten the auto-generated Approach, acceptance criteria, or Changes Manifest.
2. Run `devbench promote-proposal <id>` (flips to `in-queue`, wires as a dependency of the source task automatically) or `devbench reject-proposal <id> --reason "..."` (archives the draft, removes the row, audits the source task).
3. `devbench promote-proposal --all-from <source-id>` promotes every draft from a single proposal in one step.

The source task stays blocked until every promoted dependency completes. Once those land, the source task becomes actionable again; the orchestrator re-runs it. See [docs/task-factory.md](task-factory.md).

### How do I tell what state a pending proposal is in?

`devbench list-proposals` prints one line per proposed task with a `[state]` label prefix: `[unmaterialised]` (JSON names this id but no draft `.md` exists), `[proposed]` (draft exists with `## Status: proposed`), `[promoted]` (draft flipped to in-queue / in-progress / in-review / blocked), `[done]`, `[declined]`, `[rejected]` (draft archived via `reject-proposal`). `devbench status` prints a persistent `Un-materialised` count row so you can see at a glance if any JSONs are waiting to become drafts, and `devbench report` renders a `Proposal JSONs pending materialisation` panel listing each entry (omitted when empty). See [ADR-08](adr/08-proposal-lifecycle-observability.md).

### A proposal JSON exists but no draft -- how do I throw it away?

Use the un-materialised form of `reject-proposal`:

```
uv run devbench reject-proposal --unmaterialised <source-task-id> --reason "<message>"
```

This archives the JSON to `.devbench/rejected-proposals/<source-id>-unmaterialised-<timestamp>.json` and writes a `[PROPOSAL_JSON_REJECTED]` audit comment on the source task. The command refuses when any proposed task in the JSON already has a materialised draft; in that case use per-draft `reject-proposal <draft-id> --reason "..."` for each draft first. Also note that `devbench sweep-proposals` runs automatically at the top of every orchestrate loop iteration, so an un-materialised JSON whose safety guard clears will become drafts on its own -- you only need the manual reject path for JSONs you know you want to discard.

### I rejected a promoted draft; will the source task auto-unblock?

Yes, if the source's remaining `[BLOCKED_PENDING_PROPOSAL]` markers are all terminal (`done` or `declined`). Per-draft `reject-proposal` strips the rejected draft's marker from the source's Comments section and re-invokes the ADR-07 auto-requeue cascade. If the remaining markers are all terminal the source flips to `in-queue` with an `[AUTO_UNBLOCKED]` audit comment. If any remaining marker is still non-terminal (another in-queue / in-progress / blocked draft), the source stays `blocked` until that one resolves -- same contract as the ADR-07 cascade on the happy path. See [ADR-07](adr/07-auto-requeue-on-proposal-completion.md) and [ADR-08](adr/08-proposal-lifecycle-observability.md).

### The orchestrator promoted a fix, but a sibling task that shares the same blocker did not unblock -- why?

The proposal's `affected_task_ids` list was empty. By default `promote-proposal` wires the `[BLOCKED_PENDING_PROPOSAL]` marker on the proposal's `source_task_id` only -- not on any peer task that happens to share the same root-cause bug. The ADR-07 auto-requeue cascade only fires on tasks that carry a marker, so a peer without one never unblocks from the fix.

Two fixes, depending on where you are in the lifecycle:

1. **Before promote.** Populate the proposal's `affected_task_ids: list[str]` field with the peer task IDs. The `blocker-resolver` prompt now instructs the agent to populate this field when a shared-root-cause signature is evident (same failing test name, same production file in both blocker comments, etc.). If the agent missed a peer, you can also edit the JSON on disk at `.devbench/proposals/<source-id>.json` before `materialise-proposal` runs.
2. **After promote.** Run `uv run devbench add-dep <peer-task-id> <promoted-task-id> --reason "..."`. This wires the marker + Dependencies row on the peer task idempotently; the ADR-07 cascade picks the peer up when the promoted fix completes. See the [cli-reference entry](cli-reference.md#add-dep).

See [ADR-10: Multi-target proposal wiring](adr/10-multi-target-proposal-wiring.md) for the full design and the alternatives that were rejected.

### I rejected a proposal but it came back on the next loop iteration -- why?

Pre-ADR-09 behaviour: `reject-proposal <draft-id>` archived the draft and removed its BACKLOG.md row, but left the source proposal JSON unchanged. The next `sweep-proposals` call (SKILL step 0) or any manual `materialise-proposal` call walked the JSON's `proposed_tasks[]`, saw no live `.md` for the rejected task, and re-created the draft. Classic case of two layers disagreeing: the classifier (via the rejection archive) knew the task was `REJECTED`, but the materialiser did not ask.

ADR-09 fixed this: `materialise_proposal` now classifies every task before deciding to create a draft and only materialises `UNMATERIALISED` tasks. Rejected drafts stay rejected across any number of sweeps, orchestrator restarts, and replay attempts. If you see a resurrected draft after ADR-09 ships, report it as a regression -- the test pin at `tests/test_cli.py::TestCmdSweepAutoAccept::test_rejected_draft_not_recreated_by_sweep` would have failed.

See [ADR-09](adr/09-idempotent-materialise-proposal.md) for the rationale and [docs/task-factory.md](task-factory.md)'s "materialise-proposal is idempotent" section for the full skip contract.

### How do I make devbench auto-promote every proposal?

Set `task_factory.auto_accept_proposals: true` in `backlog/config/devbench.yaml`:

```yaml
task_factory:
  enabled: true
  auto_accept_proposals: true
```

Once the flag is on, `devbench sweep-proposals` (which runs as SKILL step 0 of every orchestrate loop tick) auto-calls `promote-proposal` for every draft currently at `## Status: proposed`. Drafts land at `in-queue` without any operator action; the ADR-07 auto-requeue cascade and ADR-10 multi-target wiring continue to work exactly as under manual promote.

Every auto-promoted draft's `[PROPOSAL_PROMOTED]` comment on the source task carries the suffix `(auto-accepted via task_factory.auto_accept_proposals=true)` so the audit trail reflects the tool, not a human, pressed the button.

Default is `false`. Omitting the key or leaving it `false` preserves the "human reviews every proposal" default. See [ADR-11](adr/11-auto-accept-proposals.md) and the "Auto-accepting proposals" section of [task-factory.md](task-factory.md).

**Reverting an auto-accepted draft:** use the standard `uv run devbench reject-proposal <id> --reason "..."` flow. Marker-strip + cascade handle it exactly like any other reject.

### My task says `blocker: executor retry budget exhausted`. What does that mean?

The executor is allowed a bounded number of retry rounds on `REVIEW_FAIL`. When that budget is exhausted the SKILL marks the task `blocked` and returns to the loop. The block is not a judgement about the work; it's a cost ceiling that prevents a task that cannot converge from burning unbounded tokens. Recovery path:

1. Open the work unit's Comments and read the last `[REVIEW_REJECTED]` block. The reviewer's finding is the actionable signal.
2. Either fix the underlying issue manually (tighten an AC, split scope, correct a Changes Manifest row) or `uv run devbench set-status <id> in-queue` to retry from scratch if you believe the failure was transient.
3. The retry budget is a prompt variable inside `plugin/devbench/skills/orchestrate/SKILL.md` (search for `max_executor_retries`). Adjust downward for cost control or upward if your task class is genuinely slow to converge; the default is documented inline in the SKILL.

### I don't see "Lifetime X" rows in the report anymore, where did they go?

They were renamed to "All-time" and folded into the consolidated grouped table. The report now renders ONE table with sections BACKLOG STATE / THROUGHPUT / API USAGE / TOKENS / COST and three windowed columns (All-time / Session / This run). The TOKENS section carries the breakdown the old "Lifetime tokens consumed" rows used to show -- the numbers are identical, only the label and location changed. The BACKLOG STATE section populates only the All-time column (these are instantaneous snapshots, not windowed metrics) while the other sections populate every window column.

## Live activity dashboard

### The orchestrator has been silent for 10 minutes -- how do I tell if it's stuck?

Run `devbench watch` in another terminal. It prints a one-screen snapshot of the current session: which task is active, which subagent is running, the latest `text` content from the subagent's transcript, the last 3-5 tool calls, and a git-status summary for the target repo. If the `Idle for Ns` footer shows a high number and neither the subagent text nor tool calls have changed, the run is likely hung; otherwise, the agent is probably thinking (LLM inference on a big context can take minutes on its own). See [docs/watch-activity.md](watch-activity.md).

### Is `devbench watch` safe to run while an orchestration is active?

Yes. The command is strictly read-only. It invokes only `git -C <repo> status --porcelain=v1` and `git -C <repo> rev-parse HEAD` (both read-only), opens every file in read mode, and never signals or writes back to the running process. Safe to run continuously via `make watch-live INTERVAL=2`.

### How do I watch agent hook activity live (instead of a snapshot)?

Run `devbench hook-tail` in another terminal. It pretty-prints every PreToolUse / PostToolUse / subagent / stop / user-prompt event from the plugin's `hook-logs.jsonl` as a one-line colorized summary, in real time:

```
23:57:45 -> review-super Bash     List all tests collected
23:58:16 <- review-super Bash     Check test coverage  |  ===== 21 passed in 27.93s =====
```

Timestamps default to the OS local timezone; override with `--tz America/New_York` (or any IANA zoneinfo name). Complementary to `devbench watch`: watch shows *current state* refreshed every N seconds; hook-tail shows *events as they happen*, append-only. Also read-only. See [docs/hook-activity.md](hook-activity.md).
