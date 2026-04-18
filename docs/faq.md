# DevBench FAQ

Short answers to questions that come up repeatedly. For broader design context, see [docs/architecture.md](architecture.md).

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

## Task factory (proposed work units)

### My task got blocked with proposals -- what do I do?

The `task-factory` generated one or more draft work units for the production fixes the amender flagged as out-of-scope. Each draft has `## Status: proposed` and a matching row in `BACKLOG.md`; the drafts are inert until you promote them. Run `devbench list-proposals` to see what was proposed, then for each draft you accept:

1. Open `backlog/<epic>/<feature>/<story>/<id>.md` and tighten the auto-generated Approach, acceptance criteria, or Changes Manifest.
2. Run `devbench promote-proposal <id>` (flips to `in-queue`, wires as a dependency of the source task automatically) or `devbench reject-proposal <id> --reason "..."` (archives the draft, removes the row, audits the source task).
3. `devbench promote-proposal --all-from <source-id>` promotes every draft from a single proposal in one step.

The source task stays blocked until every promoted dependency completes. Once those land, the source task becomes actionable again; the orchestrator re-runs it. See [docs/task-factory.md](task-factory.md).

### I don't see "Lifetime X" rows in the report anymore, where did they go?

They were renamed to "All-time" and folded into the consolidated grouped table. The report now renders ONE table with sections BACKLOG STATE / THROUGHPUT / API USAGE / TOKENS / COST and three windowed columns (All-time / Session / This run). The TOKENS section carries the breakdown the old "Lifetime tokens consumed" rows used to show -- the numbers are identical, only the label and location changed. The BACKLOG STATE section populates only the All-time column (these are instantaneous snapshots, not windowed metrics) while the other sections populate every window column.

## Live activity dashboard

### The orchestrator has been silent for 10 minutes -- how do I tell if it's stuck?

Run `devbench watch` in another terminal. It prints a one-screen snapshot of the current session: which task is active, which subagent is running, the latest `text` content from the subagent's transcript, the last 3-5 tool calls, and a git-status summary for the target repo. If the `Idle for Ns` footer shows a high number and neither the subagent text nor tool calls have changed, the run is likely hung; otherwise, the agent is probably thinking (LLM inference on a big context can take minutes on its own). See [docs/watch-activity.md](watch-activity.md).

### Is `devbench watch` safe to run while an orchestration is active?

Yes. The command is strictly read-only. It invokes only `git -C <repo> status --porcelain=v1` and `git -C <repo> rev-parse HEAD` (both read-only), opens every file in read mode, and never signals or writes back to the running process. Safe to run continuously via `make watch-live INTERVAL=2`.
