# Cross-Backlog Dependencies

## Purpose

A devbench Backlog scopes its work to one set of target repos defined in its `devbench.yaml`. When that work consumes outputs produced by a DIFFERENT Backlog driven by a separate orchestrator (e.g., released artifacts, tagged Terraform module versions, generated SDK bindings), the dependency is "cross-backlog" and cannot be expressed via the in-backlog `## Dependencies` table alone -- the dependent's `## Dependencies` cannot reference task IDs that don't exist in this backlog's `BACKLOG.md` index.

This document is the canonical pattern for representing cross-backlog dependencies: the **manual blocker idiom** (see `manual-blockers.md`), anchored in the dependent backlog and wired into every dependent Task.

## When you have a cross-backlog dependency

You have one if either of these is true:

- A Task in this backlog references modules / packages / images / secrets / DNS records produced by Tasks in a different backlog AND the producer backlog has its own `BACKLOG.md` that this backlog cannot validate against.
- A Task in this backlog reads a tag / release / artifact whose creation is owned by a separate orchestrator that runs against a different `DEVBENCH_WORKSPACE_ROOT`.

Examples observed in production:

- Backlog A (`caylent-telemetry-spec/backlog/`) consumes Backlog B (`tf-modules-backlog/`) released terraform-modules tags. 16 Backlog A Tasks reference `caylent-solutions/terraform-modules` modules by tag. Backlog B has its own orchestrator and `BACKLOG.md`.
- Backlog A consumes a kanon integration-test fix being authored by another agent in the kanon repo. The fix's completion is verified externally by the operator running kanon's test suite locally.

## Special case: the producer is another devbench work group's branch

The most common cross-backlog dependency in practice is one devbench work group's backlog declaring "Task X in this backlog must not start until work group Y's branch has merged into our shared target branch." Unlike the general external-producer case above (an artifact tag, a human sign-off, a fix authored by someone outside devbench), **this specific case is git-verifiable** -- devbench itself can answer "has Y merged" by checking real ancestry, so it does not need to fall back to an operator-verified manual blocker.

`spec-to-backlog` auto-generates this as an **ancestry-gate task** rather than a manual blocker whenever a spec declares a work-group dependency (see the skill's Step 2/4a). The gate task is a normal, executable Task -- not a `DO NOT CLAIM` anchor -- placed at `E0-F<N>-S1-T1` by the same convention as a manual blocker (Workspace Bootstrap epic, new Feature per declared dependency), and every other Task in the tree lists it in `## Dependencies` so no work can be claimed until it passes:

````markdown
### Approach

Run the canonical dependency-deliverability check and report its result;
do not attempt to satisfy this task's AC any other way:

```bash
devbench check-ancestry <this-task-id> <remote>/<dependency-branch>
```

`<remote>` is the producer repo's configured tracking remote (resolved from
`git config --get branch.<default-branch>.remote`), not necessarily `origin`.

- Exit 0 with `mode: "strict"` or `mode: "squash-pr"` in the status line:
  the dependency has merged. Mark AC-DEP-001 met.
- Exit 0 with `{"gate": "ancestry", "status": "disabled"}` on stdout: the
  ancestry gate is NOT enabled for this repo (see `resolve_gate_config`
  and the D-17 built-in default of disabled). This is not an answer to
  "has the dependency merged" -- it means the question was never asked.
  Do NOT mark AC-DEP-001 met on this output; enable
  `gates.ancestry.enabled` for the repo and re-run, or treat the gate
  task as blocked pending that configuration.
- Exit 1 (a BLOCKED result, or an evaluation error): the dependency has
  NOT merged (or ancestry could not be determined). Do not mark
  AC-DEP-001 met. This task -- and every Task depending on it -- must
  remain unclaimed until a re-run of the same command exits 0.
- Exit 2: a usage error (for example an empty dependency ref). Fix the
  invocation and re-run; this is not a verdict on the dependency.
````

`devbench check-ancestry` (see [`cli-reference.md`](cli-reference.md#check-ancestry)) is **the one canonical command** for this question across the whole pipeline: it runs a strict `git merge-base --is-ancestor <dependency-ref> <target-ref>` probe against the real target repo, not a proxy such as checking for a local snapshot/report file. Every tool in this pipeline that needs to answer "is this prerequisite actually available" -- `spec-to-backlog`-generated gate tasks, `init-workgroup`-style pre-flight checks, merge-forecast/merge-resolve tooling -- should shell out to this command rather than reinvent the check.

Because the gate is an ordinary Task, it is re-evaluated every time it is (re-)attempted -- including after the backlog is paused and resumed -- rather than being checked once at generation time and forgotten: the ADR-07 dependency mechanism keeps every dependent Task unclaimable as long as the gate has not reached a terminal-satisfying status, and a rejected/blocked gate task naturally gets re-attempted (re-running the same `check-ancestry` command) the next time the orchestrator or an operator revisits it.

**Squash-aware verification (317-D02).** A strict `git merge-base --is-ancestor` probe is, by itself, a commit-graph-only check: it reports "not merged" for a dependency that is logically satisfied but was squash-merged, rebased, or landed via a fix-pack branch that doesn't carry the original branch's commit hashes. `devbench check-ancestry` does not stop there -- when the strict probe reports "not an ancestor", it runs a second probe that searches for the dependency's merged PR via `gh pr list --search "<sha>" --state merged --base <default-branch>`, independent of which commits survived the merge. A pass through this second probe is recorded in the status line as `mode: "squash-pr"`; both probes' outcomes are always printed together so an operator can see which probe answered what. There is no squash-merged, rebased, or fix-pack-landed topology that requires falling back to the manual-blocker idiom below -- only a producer that is not a devbench-tracked branch merge at all does.

Use a plain manual blocker (not an ancestry gate) when the producer is NOT a devbench-tracked branch merge -- see the table in [`manual-blockers.md`](manual-blockers.md#when-to-use-a-manual-blocker-vs-a-regular-dependency).

## The pattern: anchor a manual blocker in this backlog

Create a manual blocker (per `manual-blockers.md`) representing the external dependency. Wire every dependent Task in this backlog to it via `devbench add-dep`.

Convention for placement: the manual blocker lives in `E0-F<N>` (Workspace Bootstrap epic, a new Feature for each external dependency). This keeps cross-backlog gates visible at the top of `BACKLOG.md`'s index, alongside other workspace-level prereqs (symlinks, AWS profiles, etc.).

### Step 1 -- Create the manual blocker

Follow the canonical Story + Task templates from `manual-blockers.md`. The Story's description MUST name the external producer backlog explicitly (e.g., "Backlog B at `tf-modules-backlog/`") and the deliverables this backlog consumes (e.g., "all 14 modules tagged on `caylent-solutions/terraform-modules` main"). The Task's `## Acceptance Criteria` MUST include a `AC-MANUAL-001` that is verifiable by an external command (e.g., `gh api repos/<org>/<producer-repo>/tags --jq '.[].name' | sort`).

### Step 2 -- Wire every dependent Task

For each Task in this backlog that consumes the external producer's output:

```bash
DEVBENCH_WORKSPACE_ROOT=... DEVBENCH_CLAUDE_MODEL=... \
  uv run --project ... devbench add-dep <dependent-task-id> E0-F<N>-S1-T1
```

`add-dep` writes the manual blocker into the dependent's `## Dependencies` table carrying the manual blocker's real, current title and status (not a `(auto)` / `proposed` placeholder), alongside a `[BLOCKED_PENDING_PROPOSAL]` audit marker. When `devbench report` runs, the dependent appears under "Blocked tasks (auto-clearing via proposal)" rather than "needs operator attention" because the cascade-classifier keys off the `[BLOCKED_PENDING_PROPOSAL]` marker -- not the Dependencies row's status cell -- as the signal that the blocker is resolvable. Once the operator flips the manual blocker to `done`, every dependent that has only this blocker becomes claimable.

### Step 3 -- Document the unblock procedure

The manual-blocker Task's description MUST list the exact verification commands the operator runs to confirm the external work is complete, plus the `devbench set-status <id> done` invocation that clears the gate. See the example in `manual-blockers.md`.

## Why not represent cross-backlog deps another way

Three alternatives are NOT recommended:

- **In-line `## Dependencies` rows referencing a task ID from another backlog**: `devbench validate-backlog` cannot resolve such IDs (they don't exist in this backlog's index) and will fail validation.
- **Untracked operator memory ("Backlog B should be done before launching A")**: this is what we had in `caylent-telemetry-spec/BACKLOG.md` preamble; it's enforced at the epic level by prose but NOT at the task level. Result: the orchestrator picks up Backlog B-dependent Tasks before Backlog B has run, fails them, and the operator must intervene per task. The manual-blocker idiom enforces the dependency at task-claim time.
- **Custom "EXTERNAL-" task IDs**: tempting but breaks devbench's ID format regex (`E\d+-F\d+-S\d+-T\d+`) and the parser. If devbench ever grows native cross-backlog awareness, the EXTERNAL prefix could be added as a first-class concept; until then, the manual-blocker idiom is the workaround.

## Worked example: Backlog B gate in caylent-telemetry-spec

Authoritative implementation in production at `caylent-telemetry-spec/backlog/E0/E0-F4/`:

- `E0-F4.md` -- Feature description: "Backlog B (terraform-modules) gate"
- `E0-F4-S1/E0-F4-S1.md` -- Story: "Verify Backlog B terraform-modules tags released"
- `E0-F4-S1/E0-F4-S1-T1.md` -- Task: "Backlog B terraform-modules gate -- DO NOT CLAIM"

Wired dependents (16 Tasks across E1 + E2 + E3):

- E1-F5-S1-T1, T2, T3 (DNS zones using route53-record primitive)
- E1-F5-S2-T1 (ACM cert tasks)
- E1-F5-S3-T1 (per-env DNS records)
- E1-F6-S{1..5}-T2, T3 (per-env Terragrunt deploys using telemetry-api / telemetry-storage / telemetry-observability collections)
- E2-F2-S3-T1 (HMAC authorizer Lambda using collection module)
- E3-F3-S1-T1 (Managed Grafana workspace using primitive)

Once Backlog B's 14 modules are tagged on terraform-modules main, the operator runs:

```bash
DEVBENCH_WORKSPACE_ROOT=/workspaces/rpm-migration/caylent-telemetry-spec \
DEVBENCH_CLAUDE_MODEL=claude-opus-4-7 \
uv run --project /workspaces/rpm-migration/devbench \
  devbench set-status E0-F4-S1-T1 done
uv run --project /workspaces/rpm-migration/devbench \
  devbench set-status E0-F4-S1 done
```

The 16 dependents auto-unblock; the orchestrator's next iteration claims them in dep-graph order.

## Multiple cross-backlog dependencies

Each external producer gets its own manual blocker. Do NOT combine unrelated external deps under one Feature. Caylent Telemetry currently has two:

- `E0-F3-S2-T1: External-agent placeholder -- DO NOT CLAIM` -- gates kanon-fork Tasks waiting on a kanon int-test fix being authored elsewhere.
- `E0-F4-S1-T1: Backlog B terraform-modules gate -- DO NOT CLAIM` -- gates terraform-modules-consuming Tasks waiting on Backlog B.

A given dependent Task MAY have multiple manual blockers in its Dependencies table if it consumes multiple external producers (e.g., a Task that uses both kanon and a Backlog B module). All blockers must clear before the Task is claimable.

## Authority

This document is the source of truth for cross-backlog dependency representation. `manual-blockers.md` defines the underlying idiom; this document layers on the cross-backlog usage convention.
