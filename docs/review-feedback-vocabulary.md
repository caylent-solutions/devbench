# Review-Feedback Vocabulary

Issue #156 introduces structured rejection-feedback persistence for every
review judge AND the manifest amender. Each rejection JSON written to
`<workspace>/.devbench/review-failures/<task-id>-<judge>-<n>.json` declares
one or more **categories**, each tagged with a code drawn from a fixed
per-judge vocabulary. The literal strings are part of the public
contract -- agents emit them, the executor resolves them, the done-gate
keys off them, and the report aggregates them. Renaming or removing a
code is a breaking change.

The canonical source of truth is
`src/devbench/backlog/review_feedback_vocabulary.py`
(`JUDGE_CATEGORIES` constant). This document mirrors that table with
example remediations.

## `code_review`

| Code | Meaning | Example remediation |
|------|---------|---------------------|
| `MAKE_VALIDATE_FAILURE` | `make validate` returned non-zero in the staged diff | Run `make validate` locally, fix the named failure, re-stage. |
| `HARDCODED_URL` | Hardcoded URL / hostname / endpoint | Read from environment variable; document the env var in the config docs. |
| `MISSING_AC_EVIDENCE` | Diff does not satisfy a required Acceptance Criterion | Add the missing implementation; reference the AC ID in the TDD log. |
| `SOLID_VIOLATION` | Single-responsibility / open-closed / etc. violation | Refactor to comply with the named SOLID principle. |
| `SECURITY_BYPASS_ANNOTATION` | `# noqa` / `# nosec` / equivalent suppression | Remove the suppression and fix the underlying finding. |
| `SCOPE_VIOLATION` | Diff touches files outside the Changes Manifest | Either revert the out-of-scope change OR file an amendment request. |
| `MANIFEST_TODO_UNFILLED` | Manifest still has a `TBD` placeholder row | Replace placeholder with real file/change rows before claim. |
| `AGENT_LOG_CONTRADICTS_DIFF` | TDD log claims work that does not appear in the diff | Reconcile log + diff; re-stage if work was lost, or trim the log claim. |
| `NEWLY_REACHABLE_PATH_UNVERIFIED` | Bug-fix-shaped task has no `[NEWLY_REACHABLE]` entry, or an entry with unverified paths | Enumerate the paths the fix newly unlocks and live-verify each at smoke-test level; see `docs/newly-reachable-paths.md`. |
| `UNREACHABLE_ARTIFACT` | New component/hook/slice/function has zero non-test importers per `devbench check-reachability` evidence | Import and wire the artifact into its real composition root (route table, parent container, shell), or add a `devbench-defer-reachability: <reason>` comment if intentionally deferred. |

## `test_review`

| Code | Meaning | Example remediation |
|------|---------|---------------------|
| `GIT_COMPLETENESS` | Test files exist on disk but are not staged | `git add` the test files. |
| `STUB_TEST` | Placeholder test (`assert True`, TODO body, etc.) | Replace with a real test that can fail when the code regresses. |
| `COVERAGE_REGRESSION` | Coverage on the gated modules dropped below 100% | Add tests that exercise every modified branch. |
| `TDD_CYCLE_MISSING` | No `[RED]` / `[GREEN]` / `[REFACTOR]` audit entries | Re-run the TDD cycle and log the phases via `devbench log-tdd`. |
| `DRY_VIOLATION` | Duplicated test logic that should be parameterised | Extract a helper or use `pytest.mark.parametrize`. |
| `FIXTURE_CATALOG_MISMATCH` | `devbench check-fixture-consistency` reported a `FAIL:` finding -- a mock/fixture file references an identifier absent from its designated canonical dataset, or a canonical source's coverage fell short of a declared `expected_count` | Fix the fixture to reference a real canonical key, add the value to `fixture_consistency.scan[].allow_missing` if it is an intentional edge case, or complete the backfill to satisfy `expected_count`. |
| `COMPOSITION_ROOT_MISSING` | Only coverage for a state-consuming UI component is an isolated render with hand-supplied props/mocked store/DI container (caylent-solutions/devbench-internal-backlog#11) | Add a test that renders/exercises the component through the app's real composition root, or a documented smallest-real-ancestor exception -- see `docs/composition-root-testing.md`. |
| `LAYOUT_STUB_WITHOUT_LIVE_TEST` | Diff stubs a DOM-layout/rendering primitive (`offsetHeight`, `getBoundingClientRect`, `ResizeObserver`, etc.) for a `[LAYOUT-AC]`-tagged AC with no companion real-render test for the same AC | Add a companion real-render/live-browser test (e.g. Playwright) at the viewport/breakpoint the AC names; the stub alone does not prove the fix. |

## `doc_review`

| Code | Meaning | Example remediation |
|------|---------|---------------------|
| `README_SYNC` | README out of sync with code change | Update the README in the same commit as the code. |
| `CHANGELOG_SYNC` | CHANGELOG missing the matching entry | Add a bullet under the v-next block. |
| `API_DOCS_STALE` | Docstring / API doc lags behind the implementation | Update the docstring; verify any generated docs. |
| `EVIDENCE_BASED_CLAIM` | Speculative quantitative claim ("30% faster" without data) | Restate qualitatively or cite the measurement. |
| `CONFIG_DOCS` | New env var / config field undocumented | Document the new variable in `docs/cli-reference.md` / `sample-config.yaml`. |

## `changes_manifest`

| Code | Meaning | Example remediation |
|------|---------|---------------------|
| `SCOPE_GAP` | Manifest declares files not in the diff | Either implement the missing change OR remove the row. |
| `MANIFEST_MISMATCH` | Diff vs. manifest row disagreement | Update the row's `Change` cell to match the actual edit. |
| `STAGING_GAP` | Diff has files outside the manifest, no amendment filed | File an amendment OR revert the out-of-scope file. |
| `OUT_OF_SCOPE_FILES` | Files clearly belonging to another task | File a proposal for a follow-up task; revert here. |

## `security_review`

| Code | Meaning | Example remediation |
|------|---------|---------------------|
| `SECRET_LEAK` | Credential / token / key materialised in code or logs | Rotate the secret; move to AWS Secrets Manager / Parameter Store. |
| `UNAUTHORIZED_DEP` | Dependency added without security review | Open a dependency-vetting ticket; remove the dep or wait for review. |
| `SCOPE_VIOLATION` | Security-relevant change outside the manifest | File an amendment with a security justification. |

## `manifest_amender`

The amender uses the same vocabulary it has carried since issue #154.
Mirrored here so all six judges share a single registry. See
`AMENDER_REJECTION_CATEGORIES` in `devbench.backlog.amendment` for the
authoritative set.

| Code | Meaning |
|------|---------|
| `SCOPE` | Amendment is for files unrelated to the in-progress task. |
| `APPROACH_AUTH` | Approach forbids the kind of fix the amendment proposes. |
| `JUSTIFICATION_COHERENCE` | Justification does not clearly tie back to a linked AC. |
| `PRE_FILTER` | Layer 1 deterministic check failed (rate limit, dup row, etc.). |
| `OTHER` | Catch-all when none of the above match. |

## Severity ordering

When the executor-feedback collector injects multiple rejection
payloads on retry, it orders them by severity descending then by
attempt descending. The severity ordinals live alongside the
vocabulary in `JUDGE_SEVERITY_ORDER`:

```
security_review (60) > code_review (50) > test_review (40)
                     > changes_manifest (30) > doc_review (20)
                     > manifest_amender (10)
```

The list is truncated to `MAX_RETRY_ATTEMPTS` rounds so the executor
never receives more context than the retry budget can act on.

## Resolution protocol

For every category surfaced in a `review-judge-fail` payload, the
executor MUST do exactly one of:

1. Fix the issue, re-stage, and log
   `[REJECTION_FEEDBACK_RESOLVED] <judge>:<code>` once the next
   review iteration confirms the category no longer flags.
2. Determine that the fix belongs upstream, log
   `[NEEDS_DEP] <judge>:<code>`, and propose a dep wire via
   `devbench add-dep`.

Until every rejection-feedback file for a task is cleared via either
mechanism, the done-gate refuses `mark-done` and emits a
`[REJECTION_FEEDBACK_OUTSTANDING]` audit naming the unresolved
`<judge>:<code>` pairs.
