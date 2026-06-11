# Acceptance Criteria -- Canonical AC-FINAL Set

This document defines the canonical AC-FINAL-001..015 acceptance-criteria set that every Backlog Task SHOULD include in its `## Acceptance Criteria` section. Each AC has an explicit **Applicability** field listing the language tiers it applies to. Tasks whose Changes Manifest does NOT include source files of an AC's applicable language MUST append a `-- N/A for <tier> Tasks (no <X> source authored)` suffix to that AC line so reviewers do not enforce the inapplicable check.

## Why a canonical set

Before this document existed, AC-FINAL was referenced by ID (e.g., "AC-FINAL-008", "AC-FINAL-015") across `authoring-manifests.md`, `manifest-amendments.md`, and several agent prompts, but the IDs were never defined as a stable set with semantics. Backlog generators copied AC-FINAL blocks from one example task to the next without knowing which ACs actually applied to the new task's language. The result observed in Backlog A's first orchestration: a significant number of non-Python tasks (HCL, YAML, JSON) failed AC-FINAL checks that required `pytest`, `mypy`, `ruff`, or `bandit` to run against `src/` and `tests/` directories that the task did not author. Each failure cascaded into a follow-up proposal task; aggregate human-review cost was non-trivial. This document removes the ambiguity by canonicalizing the AC set and tagging each AC with its applicable language tier.

## Language tiers

| Tier | Includes file extensions |
|---|---|
| `Python` | `.py` |
| `HCL` | `.hcl`, `.tf`, `.tfvars` |
| `YAML` | `.yaml`, `.yml` |
| `JSON` | `.json` |
| `XML` | `.xml` |
| `TOML` | `.toml` |
| `Markdown` | `.md` |
| `Mixed` | Manifest contains files from two or more tiers above |

A Task's "manifest tier" is determined by the file extensions in its `## Changes Manifest`. If at least one file is `.py`, the tier is `Python` (regardless of other files); otherwise the tier is whichever non-Python tier matches all files (or `Mixed` if more than one).

## The canonical AC-FINAL-001..015 set

Each AC line below is the verbatim text the Task author should paste into the `## Acceptance Criteria` section. The **Applicability** column lists the tiers the AC applies to; for Tasks whose tier is NOT listed, the author MUST append the `-- N/A` suffix shown below the table.

| ID | AC text | Applicability |
|---|---|---|
| AC-FINAL-001 | Every AC-TEST-* and AC-CYCLE-* test defined above runs and PASSES (not merely exists). No skipped, no xfail, no xpassed, no stubs. | All tiers |
| AC-FINAL-002 | `ruff check src tests` exits zero in the relevant service directory. | `Python`, `Mixed` (Python subset) |
| AC-FINAL-003 | `ruff format --check src tests` exits zero. | `Python`, `Mixed` (Python subset) |
| AC-FINAL-004 | `mypy src` exits zero. | `Python`, `Mixed` (Python subset) |
| AC-FINAL-005 | `pytest <the unit's OWN test files> -v` exits zero (the unit's own unit tests, scoped to the `tests/unit/test_*.py` paths in this Task's Changes Manifest). | `Python`, `Mixed` (Python subset) |
| AC-FINAL-006 | `pytest <the unit's OWN integration test files> -v` exits zero (the unit's own integration tests in its Manifest). | `Python`, `Mixed` (Python subset) |
| AC-FINAL-007 | `pytest <the unit's OWN functional test files> -v` exits zero (the unit's own functional tests in its Manifest). | `Python`, `Mixed` (Python subset) |
| AC-FINAL-008 | `bandit -r src -ll` exits zero. | `Python`, `Mixed` (Python subset) |
| AC-FINAL-009 | `DEVBENCH_CLAUDE_MODEL=<model> DEVBENCH_WORKSPACE_ROOT=<workspace> uv run --project <devbench> devbench validate-backlog` exits zero. | All tiers |
| AC-FINAL-010 | The code under test is functionally verified end-to-end (the AC-CYCLE-* evidence above). | All tiers |
| AC-FINAL-011 | No bypass annotations: no `# noqa`, `# nosec`, `# type: ignore`, `@SuppressWarnings`, `# pragma: no cover`, `--no-verify`, no raised lint thresholds, no added exclusions to linter configs. | All tiers |
| AC-FINAL-012 | No em-dash characters (U+2014) introduced anywhere. | All tiers |
| AC-FINAL-013 | No new test skips, xfails, or `pytest.mark.skip*` annotations. | All tiers |
| AC-FINAL-014 | Coverage: the unit's OWN new source modules reach 100% line + branch coverage from the unit's OWN tests (`pytest --cov=<the unit's source module(s)> --cov-fail-under=100 <the unit's OWN test files>`); baseline not regressed for modified files. Scoped to the unit's own modules, NOT global `--cov=src`. | `Python`, `Mixed` (Python subset) |
| AC-FINAL-015 | The Task's Changes Manifest matches exactly the files changed by git (no extra, no missing). Paths are repo-relative (NOT prefixed with checkout_directory). | All tiers |
| AC-FINAL-016 | Epic-capstone / CI gate: `pytest tests/unit tests/integration tests/functional -v` exits zero (the FULL suite is green). Owned by the epic's capstone Task (or CI), NOT by every leaf Task. | Epic capstone (Python epics) |
| AC-FINAL-017 | Epic-capstone / CI gate: global coverage (`pytest --cov=src --cov-fail-under=<project threshold>`) is not regressed across the whole tree. Owned by the epic's capstone Task (or CI), NOT by every leaf Task. | Epic capstone (Python epics) |

## Per-unit gate vs. epic-capstone / CI gate (scoping AC-FINAL-005/006/007/014)

A leaf Task's completion gate MUST only depend on artifacts the Task owns. AC-FINAL-005, AC-FINAL-006, AC-FINAL-007, and AC-FINAL-014 are therefore **scoped to the unit's OWN tests and modules** -- the `tests/**/test_*.py` paths and source modules listed in that Task's `## Changes Manifest`. A leaf Task is `done` when its own tests pass and its own new modules are fully covered.

The **FULL-suite green** check (every test in `tests/unit` + `tests/integration` + `tests/functional`) and the **global-coverage** check are split out to AC-FINAL-016 and AC-FINAL-017, which are owned by the **epic's capstone Task** (a final integration/gate Task per Python epic) or by **CI**, never by every leaf Task.

Why the split (issue: AC-FINAL full-suite gate fragile under flaky / order-dependent tests):

- When the target repo uses `pytest-randomly` (random test order each run), a sibling-owned test with an order-dependence / shared-state bug passes in isolation and under a fixed order but intermittently fails under some random seeds. If every leaf Task gated on the WHOLE `tests/unit` suite, that one flaky sibling test would block an otherwise-complete, **unrelated** unit **non-deterministically** -- the same unit passes on one seed and blocks on the next. A unit cannot be responsible for the whole suite's state.
- Scoping AC-FINAL-005/006/007/014 to the unit's OWN tests means a unit is never hostage to another unit's tests. The full-suite green + global-coverage signal is still enforced -- but once, at the epic capstone / in CI, where it is the right place to catch cross-unit order-dependence as its OWN signal rather than as a random per-leaf block.
- The per-unit `verify-ac` gate additionally pins the `pytest-randomly` seed (deterministic ordering) so a unit's verdict is reproducible run-to-run (`DEVBENCH_VERIFY_AC_PYTEST_SEED`); see the verify-ac deterministic-gate behaviour in `docs/cli-reference.md`.

Deferred (not implemented): an automatic "flaky-vs-real" discriminator that, on a per-unit gate failure, re-runs the failing tests in isolation and reclassifies them as a pre-existing order-dependent flake when they pass alone. With the deterministic seed (eliminates the non-deterministic per-leaf block) and own-test scoping (a unit no longer runs sibling tests in its gate at all) in place, the attribution problem is already removed at the per-unit level. An automatic re-run-in-isolation reclassifier was judged too invasive for the critical `verify-ac` path -- it risks masking a genuine failure as "non-attributable" and contradicts fail-fast -- so it is intentionally NOT shipped. The orthogonal full-suite order-dependence signal is surfaced by AC-FINAL-016 at the epic capstone / in CI, where a flaky/order-dependent failure is the correct thing to investigate and fix in the owning unit (see the target-repo test-isolation follow-up).

Authoring guidance:

- On every leaf Python Task, author AC-FINAL-005/006/007 against the unit's OWN test files (the Manifest test paths) and AC-FINAL-014 against the unit's OWN source modules. Do NOT use `pytest tests/unit` or global `--cov=src` as a leaf-Task gate.
- Add AC-FINAL-016 and AC-FINAL-017 ONLY to the epic's capstone Task (or rely on CI). They carry the `-- N/A` suffix (or are simply omitted) on ordinary leaf Tasks; the `Epic capstone` applicability means they do not block a non-capstone leaf.

## The N/A suffix

For each AC whose Applicability does NOT include the Task's manifest tier, the author MUST append this suffix verbatim to the AC line:

```
 -- N/A for <tier> Tasks (no <language> source authored)
```

Where `<tier>` is the Task's manifest tier (e.g., `HCL`, `YAML`, `JSON`, `Mixed (HCL+JSON)`) and `<language>` is the source language the AC requires (e.g., `Python` for AC-FINAL-002..008, AC-FINAL-014).

### Case sensitivity and accepted variants (issue #221 D3)

The suffix is parsed by ``BacklogManager`` when it computes which AC lines apply to a Task. The match is **case-insensitive**, so authors may write any of these equivalent forms:

- `-- N/A for HCL Tasks (no Python source authored)` (canonical)
- `-- n/a for hcl tasks (no python source authored)` (lower-case)
- `-- N/A FOR HCL TASKS (NO PYTHON SOURCE AUTHORED)` (upper-case)

What MUST be preserved verbatim regardless of case:

1. The double-dash sentinel ``--`` (two ASCII hyphens). An em-dash (U+2014) is forbidden by AC-FINAL-012 and would be flagged as a separate violation.
2. The literal token ``N/A`` (with the slash). ``NA`` without the slash is NOT recognised.
3. The literal token ``Tasks`` (plural). ``Task`` (singular) is NOT recognised.
4. The trailing parenthesised reason. The validator strips leading/trailing whitespace from the reason but does not parse its contents; any descriptive text inside the parentheses is acceptable.

What is NOT accepted (these will trip Rule 19 -- canonical-AC drift):

- Replacing the double-dash with a single dash (``- N/A``).
- Omitting the parenthesised reason (``-- N/A for HCL Tasks``).
- Reordering the suffix elements (``-- (no Python source authored) N/A``).
- Translating to a non-English locale.

The strict structure exists so the validator can deterministically identify Tasks that opt out of a canonical AC line; loose pattern matching would silently accept malformed suffixes that fail later when the orchestrator's executor parses the same AC at runtime.

### Example: a pure-HCL Task

A Task whose Changes Manifest contains only `infra/terragrunt/.../terragrunt.hcl` files has tier `HCL`. Its AC-FINAL block looks like:

```markdown
- [ ] AC-FINAL-001 Every AC-TEST-* and AC-CYCLE-* test defined above runs and PASSES (not merely exists). No skipped, no xfail, no xpassed, no stubs.
- [ ] AC-FINAL-002 `ruff check src tests` exits zero in the relevant service directory -- N/A for HCL Tasks (no Python source authored)
- [ ] AC-FINAL-003 `ruff format --check src tests` exits zero -- N/A for HCL Tasks (no Python source authored)
- [ ] AC-FINAL-004 `mypy src` exits zero -- N/A for HCL Tasks (no Python source authored)
- [ ] AC-FINAL-005 `pytest <the unit's OWN test files> -v` exits zero (the unit's own unit tests) -- N/A for HCL Tasks (no Python source authored)
- [ ] AC-FINAL-006 `pytest <the unit's OWN integration test files> -v` exits zero (the unit's own integration tests) -- N/A for HCL Tasks (no Python source authored)
- [ ] AC-FINAL-007 `pytest <the unit's OWN functional test files> -v` exits zero (the unit's own functional tests) -- N/A for HCL Tasks (no Python source authored)
- [ ] AC-FINAL-008 `bandit -r src -ll` exits zero -- N/A for HCL Tasks (no Python source authored)
- [ ] AC-FINAL-009 `DEVBENCH_CLAUDE_MODEL=... DEVBENCH_WORKSPACE_ROOT=... uv run --project ... devbench validate-backlog` exits zero.
- [ ] AC-FINAL-010 The code under test is functionally verified end-to-end (the AC-CYCLE-* evidence above).
- [ ] AC-FINAL-011 No bypass annotations: no `# noqa`, `# nosec`, `# type: ignore`, `@SuppressWarnings`, `# pragma: no cover`, `--no-verify`, no raised lint thresholds, no added exclusions to linter configs.
- [ ] AC-FINAL-012 No em-dash characters (U+2014) introduced anywhere.
- [ ] AC-FINAL-013 No new test skips, xfails, or `pytest.mark.skip*` annotations.
- [ ] AC-FINAL-014 Coverage: the unit's OWN new source modules reach 100% line + branch coverage from the unit's OWN tests; baseline not regressed for modified files -- N/A for HCL Tasks (no Python source authored)
- [ ] AC-FINAL-015 The Task's Changes Manifest matches exactly the files changed by git (no extra, no missing). Paths are repo-relative (NOT prefixed with checkout_directory).
```

(AC-FINAL-016 and AC-FINAL-017 are epic-capstone / CI gates and are NOT authored on an ordinary leaf Task -- HCL or otherwise. They live only on the epic's capstone Task. See "Per-unit gate vs. epic-capstone / CI gate" above.)

### Example: a pure-Python Task

A Task whose Changes Manifest contains only `*.py` files has tier `Python`. Every leaf-Task AC line (AC-FINAL-001..015) stays as-is from the table above; no N/A suffixes are appended. AC-FINAL-005/006/007 run the unit's OWN test files (the `tests/**/test_*.py` paths in this Task's Manifest), not the full suite. AC-FINAL-014 (100% coverage) requires the Manifest to contain BOTH the source `.py` file AND its matching `tests/unit/test_<basename>.py` (see `source-test-atomicity.md` for the rule) and measures coverage of the unit's OWN module(s) from those tests. AC-FINAL-016/017 (full-suite green + global coverage) are NOT authored here -- they belong on the epic's capstone Task.

### Example: a Mixed Task

A Task with both `*.py` source and `*.yaml` config in its Manifest has tier `Mixed (Python+YAML)`. The Python-tier ACs apply to the `.py` portion of the Manifest; the YAML files do not block AC-FINAL-005 etc. Authors do NOT append the N/A suffix in Mixed Tasks because at least one Python file is in scope. As with a pure-Python Task, AC-FINAL-005/006/007/014 are scoped to the unit's OWN tests and modules; the full-suite AC-FINAL-016/017 capstone gates are not authored on this leaf.

## Vendored code carve-out (AC-FINAL-004, AC-FINAL-008)

When a task's source repo contains a vendored third-party tree (code
the team did not author and does not own -- e.g. AOSP-derived
`src/kanon_cli/repo/` in caylent-solutions/kanon), the mypy and
bandit gates MUST scope to non-vendored code only.

Acceptable AC wording:

> AC-FINAL-004: mypy passes 100% on all owned code. Vendored trees
> excluded via `[mypy-<vendored.module>.*] ignore_errors = True` in
> the project's mypy config; the carve-out path and rationale are
> documented in the repo's CLAUDE.md.

> AC-FINAL-008: bandit passes 100% on all owned code. Vendored
> trees excluded via `bandit -x <path>` or the bandit config's
> `exclude_dirs` list; the carve-out path and rationale are
> documented in the repo's CLAUDE.md.

Vendored carve-outs are NOT bypass annotations (no `# noqa`,
`# nosec`); they are scope demarcations between owned and
unowned code at the build-config layer. They require:
- The carved-out path is committed in version control (we own
  the relationship to the upstream).
- The rationale is documented in repo's CLAUDE.md (so a future
  reviewer understands why those files are excluded).
- A separate work-item exists in the backlog (or an upstream
  PR is open) tracking eventual remediation if appropriate.

## Authority and lifecycle

- This document is the SOURCE OF TRUTH for the AC-FINAL set. If a backlog generator emits AC-FINAL ACs that diverge from the text in the table above, the divergence is a defect in the generator.
- New AC-FINAL IDs (016+) MAY be added to this document with their applicability declared up front; existing IDs MUST NOT be repurposed or renumbered (downstream Tasks reference them by ID).
- The N/A suffix wording is exact; reviewers match on the literal string `-- N/A for <tier> Tasks (no <language> source authored)`. Variations are flagged as AC text drift.
- `devbench validate-backlog` MAY emit a warning when a Task's AC-FINAL line lacks the N/A suffix despite the manifest tier requiring it (see Tier 3 tooling proposal in the post-Backlog-A lessons-learned plan).
