# Newly-Reachable Code Paths: the `behavior-fix` Verification Obligation

## The problem

A recurring pattern across multi-round remediation: a fix task clears its reported
blocker, but the reported blocker had been gating off an entire code path (a component
was never mounted, a button was permanently disabled, a page never stopped crashing
before this point). Once the fix lands, that previously-unreachable path becomes
reachable for the first time -- and immediately exposes a new, independently
pre-existing (and previously untestable) defect. A subset of instances go further: the
fix itself directly causes the new defect by reusing a shared, stateful primitive (a
z-index tier, a dirty-flag write, a close/dismiss callback) without accounting for that
primitive's other consumers.

Confirming that the originally-reported reproduction steps now pass proves the gate
opened. It says nothing about what was behind the gate. Left unaddressed, this produces
work groups cycling through many QA rounds where several rounds are not re-tests of the
same bug but successive layers of a previously-inaccessible code path being reached and
found broken one level at a time.

## The rule

**A `behavior-fix` task is not verified merely because the original repro passes.** It
additionally requires:

1. **Enumeration** -- an explicit list of the code paths this fix newly makes
   reachable (paths that could not execute, render, or receive user interaction while
   the defect was present, and now can).
2. **Live verification** -- a real, smoke-test-level check of each enumerated path,
   not a read-through of the code asserting it "should" work.

This is expressed as an **acceptance criterion**, never a Definition-of-Done checklist
item (spec `integration-reality-gates-hardening.md` Section 1.3 S1, findings 320-D04
and C-06): a Definition-of-Done checkbox is auto-ticked by the orchestrator on the
`done` transition, so it is a record of completion, not something any judge evaluates
before completion -- it can never function as a gate. An acceptance criterion is a
surface the review judges actually read and can reject against, so the requirement
lives there instead.

## What triggers this obligation: the `## Task Type:` taxonomy

The work unit's own `## Task Type: behavior-fix` declaration (the taxonomy
`devbench.backlog.manager.BacklogManager` validates on every leaf task,
`constants.VALID_TASK_TYPES`) keys the executor's, the code-reviewer's and the
blocker-resolver's obligations described below. It does NOT apply to `docs`, `chore`,
`test-only`, `refactor`, or `feature`-typed units. One surface is the exception:
`generate_draft_md`'s acceptance-criterion auto-append (first bullet below) keys off a
related but distinct field, `ProposedTask.task_type`, not the rendered `## Task Type:`
header -- see that bullet for how the two can diverge.

- **`generate_draft_md`'s acceptance-criterion auto-append** (`proposal.py`,
  E8-F1-S1-T1) appends the newly-reachable-paths AC line whenever the drafted task's
  `ProposedTask.task_type` resolves to `constants.TASK_TYPE_BEHAVIOR_FIX` -- the same
  default (`constants.DEFAULT_TASK_TYPE`) every proposal carries unless a caller
  overrides it.
- **The executor's and code-reviewer's BUG-FIX COMPLETENESS obligation** (see
  `executor.md` / `code-reviewer.md`) applies whenever the work unit being executed or
  reviewed declares `## Task Type: behavior-fix`.
- **`blocker-resolver.md`**'s proposal-authoring guidance uses the same field: a
  proposal's `task_type` defaults to `behavior-fix`, so an imperative `Fix ...` title
  documents intent for a human reader but is not itself what wires the requirement.

Prior to E8-F1-S1-T1/T2 these four surfaces each carried an independent "is this
bug-fix-shaped" title/description heuristic (`its title starts with "Fix"...`). That
heuristic is retired from the executor, the code-reviewer, the blocker-resolver and this
doc; `## Task Type: behavior-fix` is the single source of truth across all four. A fifth
surface, the authoring skill `plugin-authoring/devbench-authoring/skills/spec-to-backlog/SKILL.md`,
carried the same retired heuristic and the same retired Definition-of-Done carrier; the
follow-up task E8-F1-S1-T3 rewrote both spots (Step 1b item 13 now reads "Newly-reachable-paths
requirement is NOT a Definition of Done item" and keys off `## Task Type: behavior-fix`,
and the validation checklist item was renamed from "Bug-fix DoD completeness" to
"Newly-reachable-paths AC completeness"), so all five surfaces now agree.
When in doubt while drafting a task, declare `behavior-fix` rather than a weaker type --
the cost of an unnecessary "none, this fix is self-contained" marker is one CLI call; the
cost of skipping a genuinely gated-path fix is another multi-round remediation chain.

## What counts as adequate enumeration

This is inherently judgment-based. The bar is "meaningfully more than nothing," not
exhaustive transitive path coverage. At minimum, name the FIRST HOP downstream of the
gate the fix removed.

**Adequate:**

- "Fixed: modal now closes on Escape (previously threw on close). Newly reachable: the
  parent list's `onModalClose` refresh callback, which re-fetches and re-renders the
  row list."
- "Fixed: `/reports/export` no longer 500s before the auth check runs. Newly reachable:
  the CSV-generation branch and the S3-upload branch, both previously unreachable
  because the request never got past the auth check."
- "Fixed: form's Submit button was `disabled` unconditionally. Newly reachable: the
  submit handler, its validation branch, and the success/error toast it triggers."

**Inadequate (do not do this):**

- "Fixed the bug, repro no longer occurs." -- no enumeration at all.
- "This unlocks more of the app now." -- not specific enough for anyone to verify.

If, after genuine consideration, the fix unlocks no new code path (the defect was
fully self-contained -- e.g. a typo in a log message, an off-by-one in a value nothing
downstream reads), say so explicitly with a one-sentence justification rather than
omitting the step (see "When no path is newly reachable" below).

## What counts as live verification

Acceptable: running the app/service and exercising the path manually, an
integration/functional test that exercises it, or at minimum a targeted unit test
against the newly-reachable branch, with the command and result cited. Not acceptable:
reasoning from reading the code that the path "should" work.

If verification surfaces a new, independent defect in a newly-reachable path, the
executor does not silently mark the task done -- it is treated exactly like any other
bug discovered during TDD GREEN (fixed under the minimal-scope amendment path if in
scope, or escalated as a follow-up proposal if not). See `executor.md`'s "BUG-FIX
COMPLETENESS" section for the exact procedure.

## The audit trail: `[NEWLY_REACHABLE] <path> <method> <result>`

Before logging completion, the executor logs one structured marker per newly-reachable
path (spec Section 5.3):

```bash
uv run devbench log-newly-reachable <task-id> --path <p> --method <m> --result <r>
```

This writes `[NEWLY_REACHABLE] <path> <method> <result>` into the unit's
`## TDD Cycle Log` section -- the audit surface that survives every review judge's
`read-unit --strip-comments` Evidence fetch (the PM-6 evidence-horizon rule,
E2-F3-S1-T2). `## Comments` itself is stripped by that fetch; `log-newly-reachable`
never writes there. Call it once per newly-reachable path; each invocation appends
exactly one marker line.

- `<p>` (`--path`) -- the specific code path (file, route, component) made newly
  reachable. A single non-empty token with no whitespace.
- `<m>` (`--method`) -- how the path was verified: `manual`, `unit_test`,
  `integration_test`, or `functional_test` (`cli.NEWLY_REACHABLE_METHODS`).
- `<r>` (`--result`) -- the verification outcome: `verified` (the path behaves
  correctly) or `broken` (verification surfaced a new, independent defect)
  (`cli.NEWLY_REACHABLE_RESULTS`).

Example:

```
$ uv run devbench log-newly-reachable E9-F1-S1-T1 \
    --path src/ui/LegacyPanel.tsx --method manual --result verified
{"unit_id": "E9-F1-S1-T1", "path": "src/ui/LegacyPanel.tsx", "method": "manual", "result": "verified"}
```

Exit codes: `0` on success (the marker was written); `1` when the unit does not exist;
`2` (usage error, naming the offending argument) when a required field is missing or
empty, or `--method`/`--result` names an unknown value. A non-zero exit is a hard stop
for the executor -- see `executor.md`'s Main sequence. See `docs/cli-reference.md`'s
`log-newly-reachable` entry for the full argument list and exit-code contract.

**Migration note (superseded convention).** This document previously instructed the
executor to log free-text `[NEWLY_REACHABLE] ...` prose via `log-comment` into a work
unit's `## Comments` section. That convention is superseded by `log-newly-reachable`
(E2-F4-S1-T2) and must not be used going forward: `## Comments` is stripped by every
review judge's `read-unit --strip-comments` Evidence fetch, so a marker written there
was never actually visible to the judges `code-reviewer.md`'s BUG-FIX COMPLETENESS
rubric relies on (AC-21). The structured verb above writes into `## TDD Cycle Log`
instead, which survives that fetch.

## When no path is newly reachable

`log-newly-reachable` has no sentinel value for "nothing new was unlocked": `--path`
must be a real, non-empty, whitespace-free token, and `--method`/`--result` must each
be one of the enumerated values above -- neither vocabulary has an `n/a` or `none`
member, and an empty `--path` is rejected outright with exit `2`. Inventing a
placeholder token anyway (for example a literal path of `none`) would not signal
absence; it would record a fabricated path as `verified`/`broken`, which is worse than
not logging anything. Do not invoke `log-newly-reachable` for this case.

If, after genuine consideration (see "What counts as adequate enumeration" above), the
fix unlocks no new code path, record that explicitly through a supported
general-purpose channel instead: fold a one-sentence justification into the task's
GREEN `log-tdd` entry (already required for every task, and it also lands in
`## TDD Cycle Log`), or note it via `log-comment` if a GREEN entry is not a natural
fit. Either way this is plain prose, not a `[NEWLY_REACHABLE]` marker -- there is no
structured schema for the no-path case today (see "Known limitations" below).

## Where this is enforced

This is a two-sided control: a self-reported step alone (executor-only) is weaker than
having an independent check that the self-report actually happened and holds up.

- **`executor.md`** ("BUG-FIX COMPLETENESS: newly-reachable paths" section) -- the
  executing agent enumerates and live-verifies as part of doing the fix, then runs
  `log-newly-reachable` once per path (Main sequence), before logging completion.
- **`blocker-resolver.md`** (PROPOSAL EMISSION, `suggested_acs` guidance) -- when a
  proposed follow-up task is itself a fix that was gating off a code path, the proposal
  seeds an explicit AC naming `log-newly-reachable`, so the requirement travels with
  the task from creation. A missing or failing marker on a `behavior-fix` unit is
  escalated as a real finding -- never hand-written into a comment as a substitute, and
  never claimed in a proposal without having been observed.
- **`proposal.py` (`generate_draft_md`)** -- a drafted task whose `task_type` resolves
  to `constants.TASK_TYPE_BEHAVIOR_FIX` automatically gets an extra acceptance
  criterion requiring the enumeration + live-verification step, so materialised drafts
  carry the requirement even before a human edits them. This auto-append happens
  regardless of whether the `newly_reachable_paths` gate below is enabled for the
  target repo -- see "Gate config vs. the drafted AC" below for how the executor
  reconciles the two.
- **`code-reviewer.md`** ("BUG-FIX COMPLETENESS" rubric, rubric 53) -- the independent
  check, conditioned on the gate's resolved status for the repo. While the
  `newly_reachable_paths` row of the `uv run devbench gates` table resolves `status`
  `enabled`, a `behavior-fix` unit with no `[NEWLY_REACHABLE]` marker in its
  `## TDD Cycle Log` fails review (a GREEN `log-tdd` "nothing unlocked" entry is an
  acceptable substitute for the marker, since `log-newly-reachable` has no `none`
  sentinel); "the original repro now passes" alone is never accepted. When the
  resolved `status` instead reads `disabled` (`constants.GATE_ENABLED_DEFAULT` is
  `false` at every level, so this is the common case), the executor correctly skips
  the `log-newly-reachable` CLI call and rubric 53 does not fail for the missing
  marker -- it instead requires the GREEN `log-tdd` entry to record the
  enumeration/live-verification evidence directly. Either way, the executor's own
  claim alone is not sufficient for the task to close: rubric 53 is the independent
  check that the self-report actually happened.

`newly_reachable_paths` is declared a **judge-evidence** gate
(`constants.GATE_TIERS`, spec Section 4.2, D-6), not machine-blocking: the
`[NEWLY_REACHABLE]` marker(s) are evidence the code-reviewer weighs when forming its
verdict, not a condition `mark-done` itself checks. A disabled or unconfigured gate for
a repo is neither a pass nor a fail signal -- it means the repo has not opted in, so
the code-reviewer treats an absent marker on that repo's units as informational only.

## Gate config: `gates.newly_reachable_paths`

The cross-cutting-primitives registry (below) and the gate's `enabled` switch both live
in the unified gates config (`backlog/config/devbench.yaml`, spec 4.1, decision C-03),
resolved exclusively through `config_loader.resolve_gate_config` -- never read directly
off `RuntimeConfig.gates` by any caller other than the resolver itself (AC-27):

```yaml
gates:
  newly_reachable_paths:
    enabled: false             # judge-evidence tier; disabled by default at every level (D-17)
    paths: []                  # repo-relative paths; empty/absent means no registry configured
  repos:
    caylent-solutions/devbench:
      newly_reachable_paths:
        enabled: true
        paths:
          - "src/ui/zindex.ts"
```

- **`enabled`** -- whether the gate is opted into for a repo. Resolves through the
  standard four-layer precedence (built-in `false` -> project -> per-repo override ->
  `DEVBENCH_GATE_NEWLY_REACHABLE_PATHS_ENABLED` environment override), same as every
  other gate. See `docs/devbench-yaml-reference.md`'s `gates:` section for the full
  precedence model.
- **`paths`** -- repo-relative paths naming the shared, stateful primitives' defining
  file(s) (a z-index tier module, a shared dirty-flag write path, a shared
  close/dismiss callback) that a `behavior-fix` unit should be cross-checked against.
  This is the migrated, schema-validated home of the retired free-text
  `backlog/config/cross-cutting-primitives.md` convention: no such file exists in this
  workspace layout anymore, and none should be created. Unlike most gate tunables,
  `paths` also carries a real per-repo override -- a non-empty repo-level list replaces
  the project-level list wholesale for that repo (D-15 field-wise merge); an
  empty/absent repo-level list inherits the project-level list unchanged. Absolute
  paths and `..` traversal segments are rejected at config-load time
  (`config_loader._parse_repo_relative_path_list`).

When a diff touches a file named in the resolved `paths` list (or a file plausibly
implementing one of the named primitives), the executor and the code-reviewer both
treat it as a prompt to check the primitive's other consumers as part of the
enumeration/verification step above -- not just the consumer that was the subject of
the fix. To read the resolved value: run `uv run devbench gates` and check the
`newly_reachable_paths` row's `status`/`provenance` columns -- that row resolves against
whichever repo's override actually sets `enabled` (the first sorted repo carrying one),
which is NOT guaranteed to be this repo once more than one repo carries an override; when
in doubt confirm this repo's actual enabled/disabled value directly via
`gates.repos.<org/repo>.newly_reachable_paths.enabled` in `backlog/config/devbench.yaml`.
Then read the `gates.newly_reachable_paths.paths` and
`gates.repos.<org/repo>.newly_reachable_paths.paths` entries directly from
`backlog/config/devbench.yaml` for the configured path list -- `devbench gates` itself
renders status/provenance only, not the `paths` list.

## Gate config vs. the drafted AC

`generate_draft_md`'s acceptance-criterion auto-append (above) fires on every
`behavior-fix`-typed draft, independent of whether `newly_reachable_paths.enabled` is
`true` for the target repo -- the AC append is a Task-Type decision, not a gate-config
decision. This means an operator who has not opted into the gate for a repo can still
receive a drafted unit carrying the newly-reachable-paths AC.

The executor does not skip that AC on a disabled gate. Enumeration and live
verification (the two requirements under "The rule" above) are basic engineering
hygiene independent of gate configuration, so the executor still performs them and
records the evidence -- via the GREEN `log-tdd` entry when nothing new is unlocked, or
via `log-newly-reachable` when it is. Only the `log-newly-reachable` CLI call itself
is conditioned on `newly_reachable_paths.enabled` being `true` for the repo, because
that machine-readable marker exists specifically to feed the code-reviewer's
judge-evidence rubric, and invoking the gate-specific CLI verb for a repo that has not
opted into the gate would ask for tooling the review rubric never actually consults.

## Known limitations / follow-ups

- The `paths` registry is read as configuration, not diffed automatically: no CLI
  command compares a work unit's Changes Manifest against `gates.newly_reachable_paths.paths`
  and surfaces a structured finding. A follow-up could add a
  `cmd_check_cross_cutting_primitives` CLI helper that does a deterministic path-match
  and prints a `[CROSS_CUTTING_PRIMITIVE_TOUCHED]` audit line, removing the dependence
  on the agent noticing the overlap unprompted.
- The `[NEWLY_REACHABLE] <path> <method> <result>` marker is delivered, not
  hypothetical: `log-newly-reachable` (E2-F4-S1-T2) writes it as a structured,
  judge-visible record of the path, verification method, and outcome. It still carries
  no field for the verification *evidence* itself (the command run, the output
  observed): that evidence remains prose, cited alongside the marker in a
  `log-tdd`/`log-comment` entry, and the code-reviewer's BUG-FIX COMPLETENESS rubric
  judges that prose qualitatively, the same way `AC-FINAL` evidence is judged today
  (issue #156's `MISSING_AC_EVIDENCE` pattern). A stricter follow-up could add a fourth
  `--evidence` field to the marker's grammar to make the verification-evidence claim
  itself structured.
