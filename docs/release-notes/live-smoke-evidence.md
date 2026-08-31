# Live Smoke Evidence: Integration-Reality Gates

Spec `integration-reality-gates-hardening.md` Section 10, "Live smoke
(operator-gated)" bullet, and AC-25. This document is the operator's checklist
for a short real `devbench` run against a seeded fixture workspace with the
`reachability` (spec 4.4) and `shared_file_impact` (spec 4.6) gates enabled,
and it is the evidence artifact that run's output gets pasted into.

## Operator-gated: automation must not touch this run

This checklist is executed by a human operator, never by the orchestrator.
The companion task that fills in the evidence column below,
`E12-F1-S1-T2`, is released with `## Status: hold` and stays there:

- The orchestrator's `devbench next` never surfaces a `hold` unit as
  actionable, so `E12-F1-S1-T2` is never claimed.
- No automated cascade or amendment path resumes a `hold` unit. The
  REQUIRED release path for this checklist is `uv run devbench unhold
  E12-F1-S1-T2 --reason "<reason>"`, documented in
  [docs/block-types.md](../block-types.md) (ref): it only accepts a unit
  whose current status is `hold` and records `<reason>` in the unit's audit
  trail before moving it to `in-queue`. Use this command, and only this
  command, to release `E12-F1-S1-T2`. A second command, `uv run devbench
  set-status E12-F1-S1-T2 in-queue`, exists in the codebase and is
  documented in the same reference, but it is named here strictly as a
  threat-surface fact, not as an available option: it writes the status
  directly, bypasses every gate check with no hold-status guard and no
  reason requirement, and is a strictly less audited path than `unhold`.
  Do NOT use `set-status` to release this checklist's fixture task; a SOX
  and SOC 2 audit trail depends on `unhold`'s reason-recording guard. Both
  commands require a human to run them; neither is automatic.
- Because it is never claimed, it is never auto-completed. `mark-done`
  against `E12-F1-S1-T2` only ever runs after an operator has pasted real
  command output into every row of the [evidence table](#evidence-table)
  below.

If you are an agent reading this file: do not run the checklist below and do
not edit the evidence table. This document exists so a human operator has a
copy-pasteable script; it is not a task for you to execute.

## Preconditions

All five of the following must hold before starting. A failed precondition
aborts the smoke run -- do not attempt to work around a missing precondition
by skipping a step or substituting a different fixture; fix the precondition
and restart the checklist from step 1.

1. **A throwaway seeded fixture workspace.** A `DEVBENCH_WORKSPACE_ROOT`
   directory that is NOT the workspace used for real backlog work, containing
   its own `backlog/config/devbench.yaml` and a minimal `backlog/` tree (at
   least one Epic/Feature/Story ancestor chain so `new-task` in
   [step 1](#the-checklist) has a `--target` parent directory to write into).
   See [docs/zero-to-ready.md](../zero-to-ready.md) (ref) Steps 5-8 for how to
   stand one up from scratch.
2. **A throwaway target-repo clone.** A local clone of a disposable
   `org/repo` (never `caylent-solutions/devbench` itself) registered under
   `repos:` in that fixture workspace's `devbench.yaml`, with a default
   branch and at least one committed file so the reachability and
   shared-file-impact scans have something to scan.
3. **A green `uv run devbench validate-backlog` against the fixture
   workspace.** Run it with `DEVBENCH_WORKSPACE_ROOT` pointed at the fixture
   workspace from precondition 1 and confirm it exits 0 before continuing --
   see [docs/zero-to-ready.md](../zero-to-ready.md) (ref) Step 9 for the exact
   invocation shape.
4. **Working LLM and `gh` credentials.** A Claude Code subscription or AWS
   Bedrock credentials (see [docs/llm-authentication.md](../llm-authentication.md)
   (ref)), and an authenticated `gh auth status` against the throwaway
   target-repo's GitHub org, both present in the shell that runs the
   checklist.
5. **`git_ops.single_branch` and `git_ops.defer_pr: true` configured.** Both
   are top-level keys under `git_ops:` in the fixture workspace's
   `devbench.yaml`, a sibling of `repos:` and `gates:` (see
   [Gate enablement](#gate-enablement) below). `uv run devbench
   git-ops-finalize` (checklist step 12) fails fast before doing anything
   when either key is unset (`cmd_git_ops_finalize`); `uv run devbench
   git-ops` (checklist step 10) uses the same two keys to decide whether to
   commit locally without pushing.

## Gate enablement

Add the following fragment to the fixture workspace's
`backlog/config/devbench.yaml`. `gates:` is a **top-level key, a sibling of
`repos:`** -- do NOT nest it under the throwaway target repo's entry.
`repos.<org/repo>` declares `additionalProperties: false` and permits only
`default_branch`, `checkout_directory`, `merge_strategy`, `branch_prefix`
(`src/devbench/config-schema.json`); nesting `gates:` inside a `repos:` entry
fails schema validation at load time with `ValueError: ... repos.<org/repo>:
Additional properties are not allowed ('gates' was unexpected)` before the
checklist can even start. A per-repo override, if you want one instead of a
project-wide default, uses the schema-supported
`gates.repos.<org/repo>.<gate>.enabled` path -- not a `gates:` block nested
inside `repos:`.

```yaml
gates:
  reachability:
    enabled: true
  shared_file_impact:
    enabled: true
    auto_derive_registry: true
```

Verify both gates resolved `enabled` before starting the checklist:

```bash
uv run devbench gates
```

Expected: the `reachability` and `shared_file_impact` rows both read
`status=enabled`, `tier=machine-blocking`, `provenance=project` (or `repo` if
enabled instead under a `gates.repos.<org/repo>` override). Every other gate
row stays `disabled` / `builtin` -- the smoke run enables exactly the two
gates named in spec Section 10, no more.

## The checklist

Run every command from the throwaway target-repo clone's working directory
unless noted otherwise. Replace `<FIXTURE_ID>` with the work-unit id you
choose in step 1 and `<FIXTURE_REPO>` with the throwaway `org/repo` from
precondition 2 -- both are operator-supplied, never hard-coded by this
checklist. Steps 2-12 invoke `devbench` subcommands that resolve the unit
through `DEVBENCH_WORKSPACE_ROOT` and the parsed backlog index, not through
the shell's working directory, so they behave the same regardless of where
you run them.

1. **Seed the fixture work unit.**

   ```bash
   uv run devbench new-task --id <FIXTURE_ID> \
     --title "Live smoke fixture task" \
     --target "$DEVBENCH_WORKSPACE_ROOT/backlog/<path-to-epic>/<path-to-story>/<FIXTURE_ID>.md" \
     --repo <FIXTURE_REPO>
   ```

   `--target` must be an absolute path (`cmd_new_task`'s own docstring: "the
   absolute path where the new .md file will be written"); anchoring it at
   `$DEVBENCH_WORKSPACE_ROOT` means the command resolves correctly no matter
   which directory the shell is in, unlike a workspace-relative
   `backlog/...` path passed while sitting in the target-repo clone.

   Then hand-edit the new file:

   - `## Changes Manifest` to name one real, already-tracked file in the
     throwaway target-repo clone. Prefer a config/lockfile-style extension
     (`.yaml`, `.json`, `.toml`) -- `BacklogManager._is_chore_path` accepts
     those for the `chore` task type set below (a `.md` manifest path is
     also accepted for `chore`, but through the OR-list's other classifier,
     `_is_documentation_path`; `_is_chore_path` itself deliberately excludes
     Markdown), so a later `validate-backlog` run against the fixture
     workspace stays type-consistent, though nothing later in this
     checklist re-runs it.
   - `## Status:` to `in-queue`.
   - `## Task Type:` to `chore`. The template's default, `behavior-fix`, is
     in `constants.GATED_TASK_TYPES` together with `feature`; a gated type
     requires a machine-observed `RED_OBSERVED` TDD Cycle Log entry before
     `BacklogManager.mark_done`'s `_check_task_type_done_invariant` will even
     let execution reach the gate check in step 3 -- and `RED_OBSERVED` is
     deliberately impossible to hand-seed (`devbench log-tdd` always rejects
     that phase; see its own docstring). `chore` is not in
     `GATED_TASK_TYPES`, so step 3's refusal is genuinely the machine-blocking
     gate refusal this checklist demonstrates, not a RED-gate refusal that
     masks it.

   Then add the matching rows to the fixture workspace's `BACKLOG.md`.
   `cmd_new_task` only writes the `.md` file and never touches `BACKLOG.md`;
   every later command in this checklist resolves `<FIXTURE_ID>` through
   `BACKLOG.md`'s parsed index (`BacklogParser.parse_index`), so skipping
   this sub-step leaves every step from 2 through 12 that names
   `<FIXTURE_ID>` failing immediately. The exact wording varies by verb:
   `check-reachability`, `check-shared-file-impact`, `log-verdict`,
   `git-ops` and `git-ops-finalize` all share `_resolve_unit_repo_and_path`
   / `_resolve_git_ops_context` and print `ERROR: Work unit '<FIXTURE_ID>'
   not found in backlog`; `mark-done` resolves the unit separately and
   prints the same message without the `in backlog` suffix: `ERROR: Work
   unit '<FIXTURE_ID>' not found`. Follow the shape in
   [docs/zero-to-ready.md](../zero-to-ready.md) (ref) Step 8:

   - One row in the `## Full Work Unit Index` table:
     `| <FIXTURE_ID> | Live smoke fixture task | Task | in-queue | None | <FIXTURE_REPO> | \`backlog/<path-to-epic>/<path-to-story>/<FIXTURE_ID>.md\` |`
   - The `## Status Summary` row for the fixture unit's epic, with its
     `In Queue` column incremented by one (add a new epic row, all other
     columns `0`, if the fixture epic has no row yet).

2. **Verify gate enablement.**

   ```bash
   uv run devbench gates
   ```

   Expected: as described in [Gate enablement](#gate-enablement) above --
   both rows `enabled`.

3. **Attempt `mark-done` before either gate has run.**

   ```bash
   uv run devbench mark-done <FIXTURE_ID>
   ```

   Expected: exit 1. Because `<FIXTURE_ID>` carries a non-gated `## Task
   Type: chore` (step 1) and both required judge records do not exist yet
   (step 6 seeds those later), the first invariant `mark_done` reaches that
   is actually unmet is the gate-pass check: the refusal names the first
   unmet machine-blocking gate (`reachability` or `shared_file_impact`,
   whichever `constants.GATE_TIERS` orders first -- `reachability`, per
   `constants.GATE_NAMES`'s declared order) and prints the exact remediation
   command to run, in the shape `ERROR: done-gate: gate '<name>' is enabled
   for repo '<FIXTURE_REPO>' but has no [GATE_PASS <name>] record for
   <FIXTURE_ID>. Run: uv run devbench check-<name-with-hyphens> <FIXTURE_ID>`.
   The remediation verb hyphenates the gate name
   (`BacklogManager._gate_check_command`): `reachability` has no underscore
   so its verb is `check-reachability` unchanged, but `shared_file_impact`'s
   verb is `check-shared-file-impact`, not `check-shared_file_impact`.

4. **Satisfy the reachability gate.**

   ```bash
   uv run devbench check-reachability <FIXTURE_ID>
   ```

   Expected: a `{"gate": "reachability", "tier": "machine-blocking", "status":
   "pass", "findings": 0, "scope_hash": "<sha256>"}` status line, and a fresh
   `[GATE_PASS reachability] <iso-utc> <scope-hash>` line appended to
   `<FIXTURE_ID>`'s audit trail.

5. **Satisfy the shared-file-impact gate.**

   ```bash
   uv run devbench check-shared-file-impact <FIXTURE_ID>
   ```

   Expected: a `{"gate": "shared_file_impact", "tier": "machine-blocking",
   "status": "pass", ...}` status line, and a fresh `[GATE_PASS
   shared_file_impact]` line appended to `<FIXTURE_ID>`'s audit trail.

6. **Record the required judge review-pass verdicts.**

   **Warning: run this loop only against `<FIXTURE_ID>`, the throwaway unit
   in the throwaway fixture workspace named in precondition 1. Never run it
   against a real work unit in a real backlog workspace. If it were, it
   would manufacture five `[REVIEW_PASS]` records for code that no judge
   ever actually reviewed, satisfying `_last_round_all_passed` and letting
   `mark-done` succeed on unreviewed, unaudited work -- exactly the review
   forgery this checklist's fixture isolation exists to prevent.**

   ```bash
   for judge in code_review test_review doc_review changes_manifest security_review; do
     uv run devbench log-verdict "$judge" <FIXTURE_ID> pass \
       "live-smoke fixture: hand-seeded pass for $judge"
   done
   ```

   Expected: each invocation appends a `[judge/<judge>] [REVIEW_PASS]
   live-smoke fixture: hand-seeded pass for <judge>` line to `<FIXTURE_ID>`'s
   Comments section. This step exists because satisfying the two
   machine-blocking gates (steps 4-5) is necessary but not sufficient for
   `mark-done` to succeed: `BacklogManager.mark_done` also calls
   `_last_round_all_passed`, which requires a `[REVIEW_PASS]` record from
   every name in `constants.ALL_REQUIRED_JUDGE_NAMES` (`code_review`,
   `test_review`, `doc_review`, `changes_manifest`, `security_review`) in the
   most recent review round. A hand-seeded fixture unit has no organic
   review round, so this checklist seeds the five records directly with
   `log-verdict` rather than routing the fixture through a full orchestrator
   review cycle.

7. **Capture the baseline report evidence, before completing the fixture.**

   ```bash
   uv run devbench report --once
   ```

   Expected: the report's BACKLOG STATE section shows a `Tasks completed`
   row reading `<N> of <M> (<pct>%)` with `<FIXTURE_ID>` not yet counted in
   `<N>` (`<FIXTURE_ID>` is still `in-queue` at this point in the checklist:
   `_backlog_state_rows`, `src/devbench/reporting/report.py:2677`, renders
   this row as the absolute string `f"{tasks_done} of {tasks_total}
   ({task_pct}%)"`, never a delta). Record the exact `<N> of <M>` string
   from this run -- step 9 compares its own `Tasks completed` row against
   this baseline.

8. **Re-run `mark-done` after both gates and all five judges have passed.**

   ```bash
   uv run devbench mark-done <FIXTURE_ID>
   ```

   Expected: exit 0. `<FIXTURE_ID>`'s `## Status:` becomes `done` in both the
   work-unit file and `BACKLOG.md`.

9. **Capture the report evidence.**

   ```bash
   uv run devbench report --once
   ```

   Expected: the report's BACKLOG STATE section shows the `Tasks completed`
   row's `<N>` incremented by exactly one relative to the baseline captured
   in step 7 (`<N> of <M> (<pct>%)` -> `<N+1> of <M> (<pct'>%)`), reflecting
   `<FIXTURE_ID>`'s completion. `<FIXTURE_ID>` moves directly from `in-queue`
   to `done` in this checklist -- no step ever sets its status to
   `in-progress` -- so it was never listed under the report's `In-progress
   tasks:` heading either before or after this step, and this checklist
   makes no claim about that listing (`_in_progress_listing`,
   `src/devbench/reporting/report.py:2767`, filters strictly on
   `WorkUnitStatus.IN_PROGRESS` and renders nothing for an id that never
   held that status). `devbench report` has no per-unit "done" listing of
   its own and renders no "Status Summary" table at all -- the `| Epic |
   Title | Done | In Progress | In Queue | Blocked | Declined |` Status
   Summary table lives in `BACKLOG.md`, and its `Done` column carries a
   per-epic count, not work-unit ids.

10. **Commit the fixture change to the single branch.**

    First make a real content change to the file named in
    `<FIXTURE_ID>`'s `## Changes Manifest` (step 1), in the target-repo
    clone -- for example append a comment line or bump a value. `git add`
    of an unmodified, already-tracked file stages nothing:

    ```bash
    echo "# live-smoke fixture edit $(date -u +%FT%TZ)" \
      >> <path-to-fixture-repo-clone>/<manifest-file-from-step-1>
    git -C <path-to-fixture-repo-clone> add <manifest-file-from-step-1>
    uv run devbench git-ops <FIXTURE_ID>
    ```

    Expected: exit 0, AND a real commit lands. Confirm the commit landed
    (exit 0 alone does not prove it) with:

    ```bash
    git -C <path-to-fixture-repo-clone> log -1 --stat
    ```

    and paste that output into the evidence cell. Skipping the content
    change is a silent no-op, not a failure: `git add` of an unmodified
    tracked file leaves `git status --porcelain` empty,
    `GitOpsService.commit_local` (`src/devbench/github/git_ops.py:708-711`)
    sees nothing staged, logs `Nothing to commit ... skipping` and returns
    without committing, and `_git_ops_deferred`
    (`src/devbench/cli.py:10664-10668`) still prints `mode=deferred` and
    exits 0 -- there is no error and no warning, only a missing commit. In
    `defer_pr: true` mode `devbench git-ops` commits the staged Changes
    Manifest files locally to the `git_ops.single_branch` branch
    (creating/switching to it if needed) and does NOT push or open a PR --
    that happens in step 12. This is the commit `git-ops-finalize` pushes;
    without a real commit here, step 12 has nothing accumulated to push.

11. **Author the provenance map.**

    Create a JSON file at the path you will pass as `--provenance` in step
    12, shaped per `GitOpsService.compose_finalize_pr_body`'s documented
    contract:

    ```json
    {
      "epics": [
        {
          "name": "Live smoke fixture epic",
          "summary": "One-line summary of the fixture change.",
          "issues": [
            {"number": <FIXTURE_ISSUE_NUMBER>}
          ]
        }
      ]
    }
    ```

    At least one epic with at least one `issues` entry is required:
    `compose_finalize_pr_body` fails loudly (exit 1, naming the path, before
    any push happens) on a provenance map that resolves to zero mapped
    issues. `<FIXTURE_ISSUE_NUMBER>` must be an integer; use a real open
    issue number in the throwaway `<FIXTURE_REPO>` if you want step 12's PR
    to actually close something, or any placeholder integer if you do not
    (see the closure-guarantee note in step 12).

12. **Capture the finalize PR-body evidence.**

    ```bash
    uv run devbench git-ops-finalize <FIXTURE_REPO> \
      --provenance <path-to-a-fixture-provenance-map.json>
    ```

    A relative `--provenance` value resolves against the **target repo's
    working tree** (the `repos.<FIXTURE_REPO>` checkout), never against the
    devbench process's current working directory or the workspace root; use
    an absolute path, or a path already relative to the target-repo clone,
    to avoid ambiguity.

    Expected: exit 0 (a PR is created, or an already-open PR on the branch
    is reused, and the post-PR CI watcher reports GREEN). Paste the composed
    PR body -- title, per-epic summary, closing-keyword block -- into the
    corresponding evidence row. The closing-keyword block's guarantee is
    asymmetric ([docs/cli-reference.md](../cli-reference.md) (ref),
    `--provenance` entry): a same-repository `Fixes #<n>` line auto-closes
    that issue on merge, because GitHub's auto-close mechanism only fires
    for an issue in the same repository as the merging PR; a
    cross-repository `Fixes owner/repo#<n>` line only creates a
    cross-reference on the target issue for traceability and never changes
    that issue's state.

## Evidence table

One row per numbered checklist step above. The **Operator evidence** column
starts empty; `E12-F1-S1-T2` is the task that fills it in with real,
pasted command output. A row left with an empty **Operator evidence** cell
means the live smoke run did not pass -- an empty cell is never treated as an
implicit pass, and this checklist is not complete until every row carries
real output.

Redact absolute local filesystem paths (home directories, usernames, machine
hostnames) from pasted command output before committing; replace them with a
placeholder such as `<local-path>`.

| Step | Command | Expected observation | Operator evidence |
|------|---------|----------------------|--------------------|
| 1 | `uv run devbench new-task --id <FIXTURE_ID> --title "Live smoke fixture task" --target <absolute-path> --repo <FIXTURE_REPO>` (plus the `## Task Type: chore` hand-edit and the `BACKLOG.md` rows) | New work-unit file scaffolded from the canonical template at the given target path; `BACKLOG.md` carries a matching Full Work Unit Index row and updated epic Status Summary row. |  |
| 2 | `uv run devbench gates` | `reachability` and `shared_file_impact` rows both `status=enabled`, `tier=machine-blocking`; every other row stays `disabled`. |  |
| 3 | `uv run devbench mark-done <FIXTURE_ID>` | Exit 1; refusal names the unmet gate (`reachability` first) and its hyphenated remediation command. |  |
| 4 | `uv run devbench check-reachability <FIXTURE_ID>` | Status line reports `status: pass`, `findings: 0`; `[GATE_PASS reachability]` record appended. |  |
| 5 | `uv run devbench check-shared-file-impact <FIXTURE_ID>` | Status line reports `status: pass`; `[GATE_PASS shared_file_impact]` record appended. |  |
| 6 | `uv run devbench log-verdict <judge> <FIXTURE_ID> pass "<message>"` for each of the five required judges | Five `[judge/<judge>] [REVIEW_PASS]` lines appended to `<FIXTURE_ID>`'s Comments section. |  |
| 7 | `uv run devbench report --once` (baseline, before mark-done) | `Tasks completed` row reads `<N> of <M> (<pct>%)` with `<FIXTURE_ID>` not yet counted; recorded as the baseline for step 9. |  |
| 8 | `uv run devbench mark-done <FIXTURE_ID>` | Exit 0; `<FIXTURE_ID>` status becomes `done`. |  |
| 9 | `uv run devbench report --once` | `Tasks completed` row's `<N>` incremented by exactly one relative to step 7's baseline; `<FIXTURE_ID>` never appears under `In-progress tasks:` (it was never `in-progress`). |  |
| 10 | Modify `<manifest-file>` in the clone, `git add <manifest-file>`, then `uv run devbench git-ops <FIXTURE_ID>`, then `git -C <clone> log -1 --stat` | Exit 0; the `log -1 --stat` output confirms a real commit landed on the `git_ops.single_branch` branch in the target-repo clone (no push, no PR). |  |
| 11 | Author `<path-to-a-fixture-provenance-map.json>` | File exists, decodes as JSON, and has at least one epic with at least one mapped issue. |  |
| 12 | `uv run devbench git-ops-finalize <FIXTURE_REPO> --provenance <path>` | Exit 0; composed PR body (title, per-epic summary, closing-keyword block) printed/observable via `gh pr view`. |  |

## Cross-references

- [`docs/zero-to-ready.md`](../zero-to-ready.md) (ref) -- operator onboarding
  and handoff path; Steps 5-9 cover standing up the fixture workspace this
  checklist requires.
- [`docs/cli-reference.md`](../cli-reference.md) (ref) -- full command
  reference for every verb named in this checklist.
- [`docs/block-types.md`](../block-types.md) (ref) -- the `HELD` block-type
  reference that documents `hold` status semantics referenced above.
