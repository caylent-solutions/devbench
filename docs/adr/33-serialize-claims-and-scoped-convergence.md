# ADR-33: serialize claims (shared-checkout safety) + scoped within-claim convergence

**Status:** Accepted
**Date:** 2026-06-17

---

## Context

Two unattended-run defects shared a single underlying assumption that the harness
never made explicit: that the orchestrator works on ONE target-repo checkout at a
time and that a work unit's verification is scoped to that unit.

**Tracked-issue 002 -- shared-checkout cross-contamination.** Every claim
operates on the SAME target-repo checkout (`REPO_LOCAL_PATHS[<repo>]`). Nothing
in the loop prevented two work units from being `in-progress` concurrently. When
that happened, the second unit's uncommitted (staged + working-tree) files leaked
into the first unit's `devbench get-diff` / staged-index reads. The downstream
effects observed live:

- A review judge evaluated files the unit never touched (the other unit's WIP),
  producing false "files staged outside the Changes Manifest" findings -- the same
  failure class [ADR-12](12-mode-aware-get-diff.md) fixed for branch accumulation,
  but here caused by a concurrent claim rather than a shared branch.
- A COMPLETED unit could be RE-OPENED when a concurrent claim's tree state was
  attributed to it.

The on-claim foreign-WIP eviction (TDI-006) mitigates a *prior interrupted* unit's
orphaned WIP, but it cannot help two *live* concurrent claims racing on the same
tree.

**Tracked-issue 004 -- whole-suite failures trip CLAIM_NOT_CONVERGING.** The
`ClaimConvergenceTracker` (the within-claim repeated-identical-failure bound) keys
on a failure signature `marker::target`. The executor sometimes runs the FULL repo
test suite inside a claim (e.g. `make test` / a bare `pytest`). A whole-suite
failure can be caused by an OUT-OF-SCOPE / other-unit defect even when the unit's
OWN scoped `verify-ac` is green. Re-running the full suite then produced the SAME
whole-suite signature every round and tripped `CLAIM_NOT_CONVERGING`, BLOCKing a
unit whose own acceptance criteria verifiably passed. A leaf unit was held hostage
to another unit's tests.

## Decision

**Serialize claims behind a configurable cap.** Add
`orchestrate.max_parallel_in_progress` (env
`DEVBENCH_ORCHESTRATOR_MAX_PARALLEL_IN_PROGRESS`, default **1**, rejected `< 1`),
wired exactly like the existing `max_within_claim_attempts` plumbing
(constants default -> `OrchestrateConfig` field -> `_parse_orchestrate_config`
validation -> `_resolve_max_parallel_in_progress()` env > YAML > default ->
JSON schema). Enforced at two layers:

1. **`devbench next`** -- after `get_parallel_candidates`, count units with status
   `IN_PROGRESS`; when that count is at the cap, drop `IN_QUEUE` candidates so a
   NEW unit is never offered. If filtering empties the candidate set while an
   in-progress unit exists, print `NO_ACTIONABLE` with a distinct
   `IN_PROGRESS_AT_CAPACITY:` reason naming the in-progress id, so the loop can
   distinguish "serialized, busy" from "genuinely stalled". The scope-filter
   (`NO_ACTIONABLE_IN_SCOPE`) and `ALL_DONE` paths are preserved.

2. **`devbench claim`** -- a hard backstop BEFORE the claim is written: if the
   target unit is NOT already `IN_PROGRESS` and the number of OTHER `IN_PROGRESS`
   units is at the cap, refuse with a new dedicated exit code
   `CLAIM_DEFERRED_SERIALIZED` (47, distinct from 42/43/44/45/46/127). This is a
   DEFERRAL, not a unit failure: nothing is written, the unit stays `in-queue`,
   and the message says to retry after the in-progress unit completes.
   Re-claiming an ALREADY `in-progress` unit stays idempotent (rc 0) -- it owns
   the shared checkout already.

The default of `1` serializes claims so the shared checkout is owned exclusively
by one claim at a time. An operator who gives each claim its OWN isolated checkout
(e.g. a worktree-per-claim setup) may raise the cap, where cross-contamination
cannot occur.

**Scope the within-claim convergence bound.** Classify each failure signature with
a pure helper `_is_whole_suite_target(marker, target_token, repo_roots) -> bool`:

- A `devbench verify-ac` failure (the AUTHORITATIVE per-unit gate) is NEVER
  whole-suite -- it always counts.
- A path-scoped test-runner (`pytest` / `make test` / `go test`) failure is
  whole-suite -- and is NOT counted -- when its target token is empty, a bare
  directory (`tests` / `tests/unit`), or an absolute path equal to / under a
  configured target-repo checkout root. A target naming a SPECIFIC test file or
  node id still counts.
- A `KEY=value`-parameterised runner (`tf-test` / `terratest`) always names a
  SPECIFIC module, never a whole suite, so it always counts.

The helper is wired into `ClaimConvergenceTracker.observe` with the smallest
change: a whole-suite signature short-circuits before the per-signature
increment, clears the pending marker, and emits a one-line audit note explaining
why it was not counted. The verify-ac counting path is unchanged. The tracker
takes its checkout roots from `REPO_LOCAL_PATHS` at construction; under test they
are injected via the new optional `repo_roots` parameter.

The executor agent prompt (`agents/executor.md`) additionally instructs that the
within-claim test loop must run ONLY the unit's own scoped tests (the Changes
Manifest's test files, or `verify-ac`), never the full repo suite -- the
full-suite / global-coverage gate belongs to an epic-capstone unit or CI.

## Consequences

- **A unit's get-diff / review never sees another live claim's WIP.** The
  false-positive "files staged outside manifest" and re-open failure classes from
  concurrent claims do not recur under the default serialized posture.
- **A leaf unit is no longer blocked by another unit's tests.** A whole-suite
  failure does not accrue toward `CLAIM_NOT_CONVERGING`; the authoritative,
  per-unit `verify-ac` gate still converges normally, and a scoped test failure
  still converges, so genuine non-convergence detection is preserved.
- **The deferral is transient and self-clearing.** `CLAIM_DEFERRED_SERIALIZED`
  (47) is distinct from `CLAIM_BLOCKED_PRECLAIM` (44) so the wrapping loop never
  mistakes a serialized deferral for a structural block; the unit stays `in-queue`
  and is claimable as soon as the in-progress unit completes.
- **Backlog-agnostic.** No tools-telemetry / app-specific coupling; the cap and
  the checkout-root classification derive from generic config + `REPO_LOCAL_PATHS`.

## Alternatives considered and rejected

**Give each claim its own git worktree instead of serializing.** A larger
architectural change (worktree lifecycle, cleanup, disk). Serialization is the
minimal correct default; the cap lets a worktree-per-claim setup opt into
parallelism later without re-litigating this decision.

**Mark the second claim BLOCKED instead of deferring.** Rejected: a saturated cap
is a transient, self-clearing condition, not a defect in the unit. Blocking would
pollute the backlog with spurious blocked units and require operator unblocking.

**Drop whole-suite test runs from the convergence signature entirely (count
nothing from `pytest` / `make test`).** Rejected: a SCOPED test failure naming a
specific file is a legitimate per-unit non-convergence signal and must still
count. Only the WHOLE-SUITE shape is exempt.

## Related files

### Python
- `src/devbench/constants.py` -- `DEFAULT_MAX_PARALLEL_IN_PROGRESS = 1`,
  `CLAIM_DEFERRED_SERIALIZED = 47`.
- `src/devbench/config_loader.py` -- `OrchestrateConfig.max_parallel_in_progress`
  field + `_parse_orchestrate_config` validation (`>= 1`).
- `src/devbench/config-schema.json` -- `orchestrate.max_parallel_in_progress`.
- `src/devbench/cli.py` -- `_resolve_max_parallel_in_progress`; `cmd_next`
  serialize filter + `IN_PROGRESS_AT_CAPACITY` reason; `cmd_claim` deferral
  backstop; `_is_whole_suite_target` + `_looks_like_test_file` helpers;
  `ClaimConvergenceTracker(repo_roots=...)` + `_is_whole_suite_signature` wiring;
  `REPO_LOCAL_PATHS`-sourced `repo_roots` at the production instantiation.

### Plugin prompts
- `plugin/devbench-orchestrate/agents/executor.md` -- within-claim test loop must
  run only the unit's own scoped tests / `verify-ac`, never the full repo suite.

### Tests
- `tests/test_cli_next.py::TestCmdNextSerializeInProgressCap` /
  `::TestResolveMaxParallelInProgress`.
- `tests/test_cli_claim.py::TestCmdClaimSerializeBackstop`.
- `tests/test_config_loader_presync.py::TestMaxParallelInProgressConfig`.
- `tests/test_cli_claim_convergence.py::TestIsWholeSuiteTarget` /
  `::TestWholeSuiteFailureDoesNotConverge`.

### Docs
- `docs/adr/33-serialize-claims-and-scoped-convergence.md` (this file).
- `docs/cli-reference.md` -- exit code 47, `next` serialize filter, `claim`
  serialize backstop.
- `docs/devbench-yaml-reference.md` -- serialize-claims section, scoped
  convergence note, sample-config key.
- `CHANGELOG.md` -- both fixes.
- `examples/backlogs/brownfield/multi-repo_single-pr_no-merge/before/backlog/config/devbench.yaml`
  -- `max_parallel_in_progress` with a comment.
