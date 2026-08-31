# feat: candidate-release/integration-reality-gates

This is the PR-body block for the combined `candidate-release/integration-reality-gates`
pull request (spec `integration-reality-gates-hardening.md` section 4.13, AC-24; section
5.6). The heading above is the literal PR title -- it matches
`devbench.constants.FINALIZE_PR_TITLE_TEMPLATE.format(branch="candidate-release/integration-reality-gates")`,
the same title the `git-ops-finalize` product feature composes once `git_ops.provenance_path`
is configured (see "Operator handoff" below). The three required parts -- this title, the
per-epic summary, and the closing-keyword block -- appear in that order (AC-DOC-001).

## Per-epic summary

**E1 -- Integration.** Landed the content of the eight draft pull requests
`caylent-solutions/devbench#315`-`#322` onto `candidate-release/integration-reality-gates`,
one work unit per PR, in the conflict-minimizing order verified from spec 1.1
(`#321` -> `#317` -> `#320` -> `#315` -> `#318` -> `#322` -> `#316` -> `#319`). Every task
followed the normative 7-step cherry-pick discipline of spec 4.14 verbatim: a genuine
pre-change failing test observed RED before any content landed, conflicts resolved with
`git restore --ours/--theirs` (never `git checkout --theirs`), the approved reject-list
deletions and provenance corrections applied in-pick, and `git-ops` owning every commit.
Content was preserved as shipped so the per-gate epics below had a genuine failing test to
drive their hardening.

**E2 -- Foundations.** Delivered every primitive the eight per-gate epics consume: the
unified `gates:` config surface and its single resolver, the enforcement tier taxonomy with
`[GATE_PASS]` done-path wiring, the shared scope helper and evidence-horizon pin, the
structured waiver and newly-reachable CLI verbs (`log-waiver`, `log-newly-reachable`),
build-time vocabulary generation and drift checking, the single rubric renumbering and the
issue provenance map (`docs/issue-provenance.md`), the `source_classification` single-source
module, the every-time setup-skill interviews, and the `git-ops-finalize --provenance` /
`git_ops.provenance_path` product fix this document's "Operator handoff" section describes.

**E3 -- Reachability gate (issue #10).** Turned the reachability gate from a grep-shaped
heuristic into a machine-blocking gate: word-boundary, language-aware reference matching
restricted to classified source extensions; orphan-chain attribution so a referencing file
only clears an artifact when the referencing file is itself reachable from the configured
entry points; loud failure on `git grep` plumbing errors instead of swallowed `continue`;
`load_error` findings for unreadable files; the structured `[GATE_WAIVER reachability]`
marker replacing the old source-comment defer mechanism; and done-path wiring requiring a
fresh `[GATE_PASS reachability]` record or an operator waiver.

**E4 -- Ancestry gate (issue #12).** Hardened `check-ancestry` so a passing answer is a
persisted machine record: squash-aware verification via `git merge-base --is-ancestor` or a
`gh pr list --search` probe with both outcomes printed; fatal (not warned) `git fetch`
failure with the remote name read from repo configuration; generated gate tasks typed
`## Task Type: chore` with the mechanical `wire-gate --blocks-roots` verb replacing O(N)
markdown fan-in wiring; and `[GATE_PASS ancestry]` records scoped with the resolved target
ref sha so a moved branch forces re-verification on resume.

**E5 -- Shared-file impact gate (issue #13).** Replaced a self-ratcheting, silently
re-bootstrapped baseline with one captured from the merge-base state under an exclusive
flock; a corrupt baseline is now a loud error instead of a silent rebuild; per-runner output
parsers are selected from an explicit registry with a loud error on an unrecognized format;
changed-file resolution goes through the shared `work_unit_scope` helper; the PostToolUse
hook fails closed instead of open; and the shared-file registry can be auto-derived from
import fan-in.

**E6 -- Fixture-consistency gate (issue #17).** Closed the 322-D01..D32 defect register:
every degenerate configuration (typo'd `identifier_field`, empty canonical set, empty scan
list, unknown extension) is now a loud exit-1 error instead of a silently-passing run; the
`allow_missing` waiver moved into the fixture artifact itself so it is visible in review
diffs; a config-gated source-literal extraction mode was added; and the gate is wired into
the `mark-done` done path.

**E7 -- Write-path audit gate (issue #16).** Replaced the unversioned `python -c` one-liner
interface with the `check-write-path <unit-id> --flag <name>` CLI verb (keeping
`audit_write_path` importable); replaced path-name-vocabulary classification with
assignment-context analysis, so a flag hardcoded in an `initialState` literal inside a
`store`/`slice`-named directory no longer misreports `live` (321-D03); replaced the bare
`except (UnicodeDecodeError, OSError): continue` with `load_error` findings; and closed
321-D21 with a review-time re-run (`WRITE_PATH_UNVERIFIED`) that catches a delivered
write-path task whose flag still classifies `default`.

**E8 -- Newly-reachable-paths gate (issue #15).** Deleted the Definition-of-Done auto-append
outright (an auto-ticked checkbox is a record, never a gate) and keyed the mechanism off
`## Task Type: behavior-fix` instead of a title heuristic. The `[NEWLY_REACHABLE]` record is
now written only by the structured `log-newly-reachable` CLI verb into the audit section that
survives the judge `read-unit --strip-comments` fetch, and the file-existence registry moved
into the unified `gates.newly_reachable_paths` config block.

**E9 -- Composition-root gate (issue #11).** Replaced Definition-of-Done-keyed satisfaction
(an auto-ticked DoD item was never real evidence) with a task
`## Acceptance Criteria` line that both `spec-to-backlog` and `test-reviewer` check for,
leaving zero DoD-keyed instructions behind. Shipped the store-factory convention as
`docs/composition-root-testing.md` v2 plus the `scaffold-store-factory` CLI verb, which emits
a repo-appropriate factory test skeleton and refuses to overwrite an existing output file.
Enforcement stays judge-evidence tier with no machine-blocking vocabulary.

**E10 -- Layout-geometry gate (issue #14).** Moved `[LAYOUT-AC]` tagging off prompt-only
prose and onto the AC-line grammar `validate-backlog` already walks, promoted the geometry
keyword list to a named constant consumed through the generated-surface mechanism, closed
the zero-test gap PR #319 shipped with tagging unit tests and prompt pins, added the
`gates.layout_geometry` config block, and routed exceptions through `log-waiver`. Real-browser
layout verification stays explicitly deferred (spec section 15) because it cannot be
machine-verified from inside devbench.

**E11 -- Issue closure and PR provenance.** In progress. `E11-F1-S1-T1` closed the eight
`caylent-solutions/devbench-internal-backlog` gate issues (`#10`-`#17`) with the Section
4.13 branch-note comment. This unit (`E11-F1-S1-T2`) closes the devbench-repo half of the
requirement and authors this release-notes file. `E11-F1-S1-T3` filed the five spec section 15
follow-up issues as `caylent-solutions/devbench#356` through `#360` and folded them into the
provenance map -- those follow-ups are not part of this block's closing-keyword line count
because they are deliberately OPEN tracked future work, not because they lack an issue
number.

**E12 -- Proof (operator-gated live smoke).** Not yet started (epic status: in-queue;
`E12-F1-S1-T1` blocked, `E12-F1-S1-T2` hold). E12 is the operator-gated live-smoke proof of
the gates framework described in spec section 4; nothing from it has landed at the time this
document was authored, so no CHANGELOG-backed claim is made for it here.

## Closing keywords

Only the two same-repo lines below (`Fixes #335`, `Fixes #336`) are genuine GitHub closing
keywords: GitHub's closing-keyword auto-close mechanism only fires for an issue in the same
repository as the merging pull request. A cross-repository `Fixes owner/repo#n` line creates a
cross-reference / mention on the target issue but never changes that issue's state (GitHub
Docs, "Linking a pull request to an issue" -- the syntax table documents the
`OWNER/REPOSITORY#ISSUE-NUMBER` form for referencing a different repository, but the auto-close
behaviour it describes is scoped to "its linked issue" in "a repository", not to a
cross-repository referent). The eight
`Fixes caylent-solutions/devbench-internal-backlog#<n>` lines for `#10`-`#17` are therefore
provenance / traceability annotations, not closing keywords: those eight issues were already
closed by hand with the Section 4.13 branch-note comment in `E11-F1-S1-T1`, which exists
precisely because no cross-repo keyword could have closed them on merge. AC-24 requires one
`Fixes ` line per mapped issue, not one auto-close guarantee per line, so the count below still
equals the number of mapped issues in `docs/issue-provenance.md`'s provenance table plus its
`#335`/`#336` row: the eight `caylent-solutions/devbench-internal-backlog` gate issues plus the
two devbench-repo harness-guard issues -- ten lines total. The five spec-section-15 follow-up
issues, filed by `E11-F1-S1-T3` as `caylent-solutions/devbench#356` through `#360`, contribute
no line here: they are deliberately OPEN tracked future work this campaign explicitly declined
to implement, not issues this campaign closes. Unlike the eight cross-repo `#10`-`#17` lines,
and like `#335`/`#336`, these five are same-repo issues, so a bare `Fixes #356` line WOULD
auto-close one of them on merge -- no line is added for any of `#356`-`#360`, by design, and
the count stays at ten.

### Closes

Fixes caylent-solutions/devbench-internal-backlog#10
Fixes caylent-solutions/devbench-internal-backlog#11
Fixes caylent-solutions/devbench-internal-backlog#12
Fixes caylent-solutions/devbench-internal-backlog#13
Fixes caylent-solutions/devbench-internal-backlog#14
Fixes caylent-solutions/devbench-internal-backlog#15
Fixes caylent-solutions/devbench-internal-backlog#16
Fixes caylent-solutions/devbench-internal-backlog#17
Fixes #335
Fixes #336

## Operator handoff (Phase 5)

The combined PR for `candidate-release/integration-reality-gates` is composed by the RUNNING
harness, which predates the `git-ops-finalize --provenance` product fix (spec section 6;
delivered by `E2-F9-S1-T1`). That fix teaches `git-ops-finalize` to compose a PR body with a
title, per-epic summary and closing-keyword block automatically from a JSON provenance map,
via the persistent `git_ops.provenance_path` config key (default absent, meaning today's
plain body) or a per-invocation `--provenance <path>` override -- but the harness instance
that ran this campaign was started before that config key existed, so it cannot pick it up
mid-run.

Because of that gap, the operator applies this document's content to the combined PR by hand
at Phase 5 finalize, mirroring exactly what `GitOpsService.compose_finalize_pr_body`
(`src/devbench/github/git_ops.py`) composes automatically once the config key is set:

- **PR title field:** the text on this document's line 1, after the leading `# `
  (`feat: candidate-release/integration-reality-gates`).
- **PR body field:** the "Per-epic summary" section's paragraphs, followed by the "Closing
  keywords" section's `### Closes` heading and the ten `Fixes ` lines under it, pasted as
  plain text with no surrounding triple-backtick fence. GitHub does not create an issue
  reference from text inside a fenced code block, so a fenced paste would drop the auto-close
  effect of the two genuine closing keywords (`Fixes #335`, `Fixes #336`); the ten lines are
  already unfenced above for this reason.

Applying the body this way means `Fixes #335` and `Fixes #336` auto-close those two issues if
either is still open (or gets re-opened) when the PR merges to `main`; the eight cross-repo
lines remain provenance annotations only, and do not depend on this PR merging because those
eight issues were already closed by hand in `E11-F1-S1-T1`. Every future campaign that
configures `git_ops.provenance_path` (or passes `--provenance`) gets this same body composed
automatically by `git-ops-finalize` with no operator step required.
