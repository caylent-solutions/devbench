# Acceptance Criteria -- Canonical AC-FINAL Set

This document defines the canonical AC-FINAL-001..015 acceptance-criteria set that every Backlog Task SHOULD include in its `## Acceptance Criteria` section. Each AC has an explicit **Applicability** field listing the language tiers it applies to. Tasks whose Changes Manifest does NOT include source files of an AC's applicable language MUST append a `-- N/A for <tier> Tasks (no <X> source authored)` suffix to that AC line so reviewers do not enforce the inapplicable check.

## Why a canonical set

Before this document existed, AC-FINAL was referenced by ID (e.g., "AC-FINAL-008", "AC-FINAL-015") across `authoring-manifests.md`, `manifest-amendments.md`, and several agent prompts, but the IDs were never defined as a stable set with semantics. Backlog generators copied AC-FINAL blocks from one example task to the next without knowing which ACs actually applied to the new task's language. The result observed in Backlog A's first orchestration: 44 non-Python tasks (HCL, YAML, JSON) failed AC-FINAL checks that required `pytest`, `mypy`, `ruff`, or `bandit` to run against `src/` and `tests/` directories that the task did not author. Each failure cascaded into a follow-up proposal task; aggregate human-review cost was non-trivial. This document removes the ambiguity by canonicalizing the AC set and tagging each AC with its applicable language tier.

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
| AC-FINAL-005 | `pytest tests/unit -v` exits zero (full unit suite green). | `Python`, `Mixed` (Python subset) |
| AC-FINAL-006 | `pytest tests/integration -v` exits zero (full integration suite green). | `Python`, `Mixed` (Python subset) |
| AC-FINAL-007 | `pytest tests/functional -v` exits zero (full functional suite green). | `Python`, `Mixed` (Python subset) |
| AC-FINAL-008 | `bandit -r src -ll` exits zero. | `Python`, `Mixed` (Python subset) |
| AC-FINAL-009 | `JUDGE_CLAUDE_MODEL=<model> JUDGE_WORKSPACE_ROOT=<workspace> uv run --project <devbench> devbench validate-backlog` exits zero. | All tiers |
| AC-FINAL-010 | The code under test is functionally verified end-to-end (the AC-CYCLE-* evidence above). | All tiers |
| AC-FINAL-011 | No bypass annotations: no `# noqa`, `# nosec`, `# type: ignore`, `@SuppressWarnings`, `# pragma: no cover`, `--no-verify`, no raised lint thresholds, no added exclusions to linter configs. | All tiers |
| AC-FINAL-012 | No em-dash characters (U+2014) introduced anywhere. | All tiers |
| AC-FINAL-013 | No new test skips, xfails, or `pytest.mark.skip*` annotations. | All tiers |
| AC-FINAL-014 | Coverage: `pytest --cov=src --cov-fail-under=100` exits zero for new files; baseline not regressed for modified files. | `Python`, `Mixed` (Python subset) |
| AC-FINAL-015 | The Task's Changes Manifest matches exactly the files changed by git (no extra, no missing). Paths are repo-relative (NOT prefixed with checkout_directory). | All tiers |

## The N/A suffix

For each AC whose Applicability does NOT include the Task's manifest tier, the author MUST append this suffix verbatim to the AC line:

```
 -- N/A for <tier> Tasks (no <language> source authored)
```

Where `<tier>` is the Task's manifest tier (e.g., `HCL`, `YAML`, `JSON`, `Mixed (HCL+JSON)`) and `<language>` is the source language the AC requires (e.g., `Python` for AC-FINAL-002..008, AC-FINAL-014).

### Example: a pure-HCL Task

A Task whose Changes Manifest contains only `infra/terragrunt/.../terragrunt.hcl` files has tier `HCL`. Its AC-FINAL block looks like:

```markdown
- [ ] AC-FINAL-001 Every AC-TEST-* and AC-CYCLE-* test defined above runs and PASSES (not merely exists). No skipped, no xfail, no xpassed, no stubs.
- [ ] AC-FINAL-002 `ruff check src tests` exits zero in the relevant service directory -- N/A for HCL Tasks (no Python source authored)
- [ ] AC-FINAL-003 `ruff format --check src tests` exits zero -- N/A for HCL Tasks (no Python source authored)
- [ ] AC-FINAL-004 `mypy src` exits zero -- N/A for HCL Tasks (no Python source authored)
- [ ] AC-FINAL-005 `pytest tests/unit -v` exits zero (full unit suite green) -- N/A for HCL Tasks (no Python source authored)
- [ ] AC-FINAL-006 `pytest tests/integration -v` exits zero (full integration suite green) -- N/A for HCL Tasks (no Python source authored)
- [ ] AC-FINAL-007 `pytest tests/functional -v` exits zero (full functional suite green) -- N/A for HCL Tasks (no Python source authored)
- [ ] AC-FINAL-008 `bandit -r src -ll` exits zero -- N/A for HCL Tasks (no Python source authored)
- [ ] AC-FINAL-009 `JUDGE_CLAUDE_MODEL=... JUDGE_WORKSPACE_ROOT=... uv run --project ... devbench validate-backlog` exits zero.
- [ ] AC-FINAL-010 The code under test is functionally verified end-to-end (the AC-CYCLE-* evidence above).
- [ ] AC-FINAL-011 No bypass annotations: no `# noqa`, `# nosec`, `# type: ignore`, `@SuppressWarnings`, `# pragma: no cover`, `--no-verify`, no raised lint thresholds, no added exclusions to linter configs.
- [ ] AC-FINAL-012 No em-dash characters (U+2014) introduced anywhere.
- [ ] AC-FINAL-013 No new test skips, xfails, or `pytest.mark.skip*` annotations.
- [ ] AC-FINAL-014 Coverage: `pytest --cov=src --cov-fail-under=100` exits zero for new files; baseline not regressed for modified files -- N/A for HCL Tasks (no Python source authored)
- [ ] AC-FINAL-015 The Task's Changes Manifest matches exactly the files changed by git (no extra, no missing). Paths are repo-relative (NOT prefixed with checkout_directory).
```

### Example: a pure-Python Task

A Task whose Changes Manifest contains only `*.py` files has tier `Python`. Every AC line stays as-is from the table above; no N/A suffixes are appended. AC-FINAL-014 (100% coverage) requires the Manifest to contain BOTH the source `.py` file AND its matching `tests/unit/test_<basename>.py` (see `source-test-atomicity.md` for the rule).

### Example: a Mixed Task

A Task with both `*.py` source and `*.yaml` config in its Manifest has tier `Mixed (Python+YAML)`. The Python-tier ACs apply to the `.py` portion of the Manifest; the YAML files do not block AC-FINAL-005 etc. Authors do NOT append the N/A suffix in Mixed Tasks because at least one Python file is in scope.

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
