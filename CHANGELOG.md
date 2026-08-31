# Changelog

All notable changes to devbench are documented in this file. Format is
based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

## [Unreleased] -- v-next

### Added

- **Release-notes preamble reconciled to the filed Section 15 follow-ups,
  plus a pinned, fail-fast closing-keyword count invariant** (spec
  `integration-reality-gates-hardening.md` section 4.13, AC-24;
  E11-F1-S2-T3). `docs/release-notes/candidate-release-integration-reality-gates.md`
  no longer asserts the five Section 15 follow-up rows are `TBD (filed at
  E11)` or promises `E11-F1-S1-T3` will add their lines to the closing-keyword
  block -- both went stale once `E11-F1-S1-T3` filed them as
  `caylent-solutions/devbench#356` through `#360` -- and no longer
  misclassifies `#335`/`#336` as cross-repo issues while explaining why the
  five follow-ups stay excluded; the block still records exactly ten
  closing-keyword lines with no line added for any of the five, which stay
  deliberately OPEN. `tests/test_docs/test_issue_provenance.py` gains
  `TestClosingKeywordCountInvariant`, pinning that the block carries exactly
  one recognised GitHub closing-keyword line (the full case-insensitive
  `close(s/d)`/`fix(es/ed)`/`resolve(s/d)` set, not only exact-case `Fixes`)
  per numbered mapped issue with the OPEN follow-ups excluded, raising on any
  unrecognised line or code fence found under the heading instead of
  silently skipping it, with the Section 15 exclusion set derived from
  parsed map rows rather than transcribed. `extract_devbench_repo_issue_tokens`
  is wired into a real campaign-file walk resolving `caylent-solutions/devbench#<N>`
  citations against the map's Devbench Issues column unioned with its Source
  PR column (plus a small, named pre-campaign allowlist), closing a gap
  inherited from `E2-F7-S1-T2` where that citation form was never resolved
  against any real file at all.

- **All eight `caylent-solutions/devbench-internal-backlog` gate issues
  (`#10`-`#17`) closed with an auditable branch-note comment** (spec
  `integration-reality-gates-hardening.md` section 4.13, AC-23;
  E11-F1-S1-T1). Because these issues live in a different repository from
  the code that fixes them, no closing keyword in the combined
  `candidate-release/integration-reality-gates` PR can ever auto-close
  them (section 13, D-11); each was still OPEN at the time this unit ran,
  so each received `gh issue comment` with the Section 4.13 template
  naming the terminal work-unit ids that implemented its gate, then
  `gh issue close`. `docs/issue-provenance.md` gains a `## Closure log`
  section recording, per issue, the state observed before acting, the
  live comment URL and the closing timestamp, all transcribed from actual
  `gh` command output; a follow-up idempotency pass confirmed all eight
  issues already CLOSED and posted zero further comments.

- **The `caylent-solutions/devbench`-repo half of Section 4.13 closure lands,
  plus the release-notes PR-body closing-keyword block** (spec
  `integration-reality-gates-hardening.md` section 4.13, AC-23, AC-24;
  E11-F1-S1-T2). Live `gh issue view` calls found `caylent-solutions/devbench#335`
  and `#336` already CLOSED (fixed by commit `8ac9c07` on `feat/bug-closure`,
  inherited at this campaign's branch-cut per decision D-12), so neither needed
  a comment or a close call; `docs/issue-provenance.md`'s `## Closure log`
  gains a skip-reasoned row for each. New
  `docs/release-notes/candidate-release-integration-reality-gates.md` carries
  the PR title line, a per-epic summary and a closing-keyword block (one
  `Fixes caylent-solutions/devbench-internal-backlog#<n>` line per `#10`-`#17`,
  one bare `Fixes #<n>` line per `#335`/`#336`) for the operator to apply to
  the combined PR body at Phase 5 handoff, because the running harness
  predates the `git-ops-finalize --provenance` / `git_ops.provenance_path`
  product fix (section 6; `E2-F9-S1-T1`) that automates this for future runs.

- **Corrected the cross-repo auto-close overstatement in the `git-ops-finalize
  --provenance` CLI reference entry** (spec `integration-reality-gates-hardening.md`
  section 4.13, AC-DOC-001; E11-F1-S2-T1). `docs/cli-reference.md`'s
  `git-ops-finalize --provenance` entry previously concluded that the composed
  closing-keyword block makes the combined PR "auto-close every issue it fixes
  on merge" regardless of repository. That claim is false for cross-repository
  mapped issues: GitHub's closing-keyword auto-close mechanism only fires for
  an issue in the SAME repository as the merging pull request, and a
  cross-repository `Fixes owner/repo#n` line only creates a cross-reference on
  the target issue, never a state change, which is exactly why the eight
  `caylent-solutions/devbench-internal-backlog` issues in `E11-F1-S1-T1` had
  to be closed by hand. The entry now states both effects distinctly,
  consistent with `docs/release-notes/candidate-release-integration-reality-gates.md`'s
  "Closing keywords" section and the two bullets above. No production code
  changed; `GitOpsService.compose_finalize_pr_body` already rendered the
  correct closing-keyword lines.

- **Corrected the cross-repo auto-close overstatement in the
  `configure-devbench` authoring skill's `git_ops.provenance_path` guidance**
  (spec `integration-reality-gates-hardening.md` section 4.13, AC-DOC-001;
  E11-F1-S2-T2). The `git_ops.provenance_path` entry's Alternatives bullet in
  `plugin-authoring/devbench-authoring/skills/configure-devbench/SKILL.md`
  previously recommended the provenance map as "useful for unattended
  `auto_finalize` runs that need issues to auto-close on merge" with no
  same-repository qualifier, immediately after telling the reader that both
  cross-repo and same-repo `Fixes` forms come from a single rendering path.
  That is false for cross-repository mapped issues: GitHub's closing-keyword
  auto-close mechanism only fires for an issue in the SAME repository as the
  merging pull request, and a cross-repository `Fixes owner/repo#n` line only
  creates a cross-reference on the target issue, never a state change, which
  is exactly why the eight `caylent-solutions/devbench-internal-backlog`
  issues in `E11-F1-S1-T1` had to be closed by hand. The bullet now states
  both effects distinctly, consistent with `docs/cli-reference.md`'s
  `git-ops-finalize --provenance` entry and
  `docs/release-notes/candidate-release-integration-reality-gates.md`'s
  "Closing keywords" section. A sweep of `plugin-authoring/` and `plugin/`
  found no other instance of the claim class. No production code changed;
  `GitOpsService.compose_finalize_pr_body` already rendered the correct
  closing-keyword lines.

- **Filed the five spec Section 15 follow-up issues and folded them into the
  provenance map** (spec `integration-reality-gates-hardening.md` section 15,
  section 12, AC-FUNC-001 through AC-FUNC-004; E11-F1-S1-T3). One
  `caylent-solutions/devbench` issue now exists for each deferred item --
  `assert-tests-pass.sh` fail-open rework (`#356`), guard-git-stage rule-1
  cwd/-C quirks (`#357`), real-browser layout machine-verification design
  (`#358`), build-time generation of rubric bodies (`#359`) and auto-registry
  fan-in tuning telemetry (`#360`) -- each body naming the deferring spec
  section and the motivating finding or issue; a re-run of the filing pass
  matched all five titles to their existing issue and created zero new
  issues. `docs/issue-provenance.md` gains each number in the Devbench Issues
  column of its placeholder row (plus a cross-reference on the
  `layout_geometry`, `shared_file_impact` and harness-guard-fixes rows where
  Section 15 ties a follow-up to an existing row) and a new
  `## Follow-up issues` subsection recording item, issue, state OPEN and
  deferring spec section per row; every number was verified live against
  `gh issue view` before being recorded.

- **`[LAYOUT-AC]` tagging moves onto the `validate-backlog` AC-line grammar,
  with a named keyword constant** (spec `integration-reality-gates-hardening.md`
  section 4.9c, AC-22; issue `caylent-solutions/devbench-internal-backlog#14`
  319-D critical; E10-F1-S1-T1). PR #319 shipped `[LAYOUT-AC]` tagging as
  prompt-only prose that told authors to place the tag in a position
  `validate-backlog` never parsed, and shipped zero tests, so nothing in the
  toolchain could distinguish a correctly tagged unit from a silently
  ignored one. `src/devbench/constants.py` now declares `LAYOUT_AC_TAG` and
  `LAYOUT_GEOMETRY_KEYWORDS` (the geometry keyword heuristic, promoted from
  hand-copied prose to a single named constant); `src/devbench/backlog/manager.py`
  gains `_check_layout_ac_grammar` (Check 29 in `validate()`), which rejects
  a tagged AC line that names no keyword from the constant, rejects a
  `[LAYOUT-AC]` tag placed in `## Description` (including a nested
  `### Approach` subsection) or `## Definition of Done`, and rejects a tag
  found on a non-AC bullet line inside `## Acceptance Criteria` itself, each
  with an actionable message naming the work-unit id and the offending
  line/section. Required tag position on the AC bullet line: an
  UNBACKTICKED `[LAYOUT-AC]` tag is recognized anywhere on that line
  (immediately after the AC id, after a parenthetical spec reference, or
  mid-sentence); a BACKTICKED `` `[LAYOUT-AC]` `` tag is recognized only
  immediately after the AC id, so a backtick-wrapped mention elsewhere on
  the line is treated as prose discussing the tag rather than an
  application of it.
  `plugin/devbench-orchestrate/agents/review_team/test-reviewer.md`'s LAYOUT
  / VISUAL AC VERIFICATION rubric and the `spec-to-backlog` SKILL's Step 3a
  authoring instruction both render the keyword list from
  `LAYOUT_GEOMETRY_KEYWORDS` between a `<!-- generated:layout-ac-keywords -->`
  guard-marker pair, actually written by the new
  `devbench.backlog.manager.regenerate_layout_ac_keyword_surfaces` (run via
  `REGENERATE_LAYOUT_AC_KEYWORD_SURFACES_COMMAND`) and pinned byte-identical
  to the constant by
  `tests/test_constants.py::TestLayoutGeometryKeywordSurfacesMatchConstant`,
  so the list exists exactly once in the tree.

- **`gates.layout_geometry` documented as a first-class judge-evidence gate,
  with a structural drift pin on its documentation surfaces** (spec
  `integration-reality-gates-hardening.md` sections 4.1, 4.9c, 4.2, 4.9 PM-5;
  issue `caylent-solutions/devbench-internal-backlog#14`; E10-F1-S1-T2). The
  `gates.layout_geometry` node in the `GatesConfig` tree, `config-schema.json`
  and `sample-config.yaml` were already generically shipped for all eight
  gates by E2-F1-S1-T1/E2-F1-S1-T2; this task closes the two things E10-F1-S1-T1
  left open: `docs/devbench-yaml-reference.md` now carries a dedicated
  `gates.layout_geometry` subsection (purpose, default, judge-evidence tier,
  `DEVBENCH_GATE_LAYOUT_GEOMETRY_ENABLED` env override, and the `log-waiver`
  exception route -- a mandatory non-empty `--reason`, and `--operator` NOT
  required since a judge-evidence gate accepts either attribution, unlike a
  machine-blocking gate), mirrored into `GatesConfig`'s `layout_geometry`
  attribute docstring and pinned drift-free by
  `tests/test_config_loader.py::TestGatesConfigDocstringDocumentsLayoutGeometryWaiverRoute`.
  New `tests/test_plugin/test_layout_ac_pins.py` independently checks both
  shipped keyword surfaces' raw guard-block text against
  `LAYOUT_GEOMETRY_KEYWORDS` directly (complementing, not duplicating, the
  byte-identical generator pin `TestLayoutGeometryKeywordSurfacesMatchConstant`
  already provides) and asserts the yaml reference documents the gate and its
  waiver route -- every check asserts rather than skips when a pinned file is
  missing. `tests/test_config_loader.py` also gains named regression pins
  (`TestLayoutGeometryGateConfigNamedRegressionPins`) for the four-layer
  precedence, the unknown-key/wrong-type fail-fast paths, and the schema/
  sample-config coverage, explicitly by gate name for the first time.

- **`scaffold-store-factory` CLI verb: a composition-root store-factory test
  skeleton generator** (spec `integration-reality-gates-hardening.md`
  section 4.9(b), issue `caylent-solutions/devbench-internal-backlog#11`
  AC2 item 3; decision D-9; E9-F1-S1-T2). Root-cause closure of the
  store-factory convention PR `caylent-solutions/devbench#316` deferred:
  `uv run devbench scaffold-store-factory <unit-id> --out <path>` resolves
  the unit's changed files through the shared ADR-12 scope helper (spec
  4.3), detects the store shape (`redux` or `angular-di`) from those
  files' content, and writes a matching test skeleton to `--out`, refusing
  to overwrite an existing path (`--force` is absent by design). An
  undetectable store shape exits 1 naming the files scanned, never a
  placeholder skeleton. `docs/composition-root-testing.md` gains a v2
  "Store-factory convention" section documenting the generator and how the
  emitted skeleton relates to (but does not by itself satisfy) the
  composition-root acceptance criterion; `docs/cli-reference.md` gains the
  `### scaffold-store-factory` entry under `## Gates`, pinned against
  drift by `tests/test_docs/test_cli_reference_scaffold_store_factory.py`.

### Changed

- **The composition-root testing requirement is now keyed off the task
  `## Acceptance Criteria` line instead of the `## Definition of Done`**
  (spec `integration-reality-gates-hardening.md` section 4.9(b); decision
  D-13, finding S1; caylent-solutions/devbench-internal-backlog#11;
  E9-F1-S1-T1). `docs/composition-root-testing.md` now states normatively
  that an auto-ticked `## Definition of Done` checkbox is never accepted
  as satisfaction of the composition-root requirement, because devbench
  auto-ticks Definition of Done checkboxes on the done transition, making
  a DoD-based satisfaction record a false record; a sixteenth canonical
  task section was considered and rejected as the alternative (D-13). The
  `spec-to-backlog` SKILL's Step 1b item 13 sub-bullet and Step 5b item 15
  now instruct authors to draft the composition-root requirement as an
  `## Acceptance Criteria` item, and `test-reviewer`'s rubric item 57 now
  checks that AC line and the test behind it rather than a DoD checkbox.
  The smallest-real-ancestor exception remains documented in a task's
  `### Approach` section, which survives the judge Evidence fetch
  (`read-unit --strip-comments`, spec 4.3) -- `## Comments` is never an
  acceptable location.

- **The newly-reachable-paths requirement is now keyed off the `## Task Type:`
  taxonomy and emitted as an acceptance criterion, and its path registry
  moved into the unified gates config** (spec `integration-reality-gates-hardening.md`
  section 4.9(a), 4.1; decision D-8, C-03; E8-F1-S1-T1). `generate_draft_md`
  (`src/devbench/backlog/proposal.py`) no longer auto-appends a
  Definition-of-Done checkbox for a drafted `behavior-fix` task (spec 1.3 S1,
  findings 320-D04 and C-06: a DoD checkbox is auto-ticked on the done
  transition and is never a gate); instead it appends an acceptance
  criterion naming the `log-newly-reachable` verb, only for `behavior-fix`
  drafts, so a `docs`/`chore`/`test-only`/`refactor`/`feature` task never
  inherits a verification obligation it cannot satisfy. `_is_bug_fix_shaped`
  and the `"fix "` title heuristic (rejected at the E1 cherry-pick,
  spec 4.14 reject-list) were never carried over and remain absent. The
  cross-cutting-primitives path registry gains a config-backed home,
  `gates.newly_reachable_paths.paths` (`src/devbench/config_loader.py`,
  `src/devbench/config-schema.json`, `sample-config.yaml`), resolved
  exclusively through `resolve_gate_config` (AC-27) with a real per-repo
  override layer (`gates.repos.<org/repo>.newly_reachable_paths.paths`)
  field-wise merged over the project level (D-15) -- the migrated,
  schema-validated replacement for the free-text
  `backlog/config/cross-cutting-primitives.md` convention. See
  `docs/devbench-yaml-reference.md`'s `gates:` section for the full
  precedence model and tunable reference.

- **The executor, code-reviewer and blocker-resolver prompts, and
  `docs/newly-reachable-paths.md`, now key the newly-reachable-paths
  verification obligation off the `## Task Type: behavior-fix` taxonomy and
  read the structured `log-newly-reachable` marker instead of the retired
  title-heuristic and `## Comments`-based convention** (spec
  `integration-reality-gates-hardening.md` sections 1.3 S1/S2, 4.3, 4.9(a),
  5.3; AC-8, AC-10, AC-21; E8-F1-S1-T2). `plugin/devbench-orchestrate/agents/executor.md`
  gains a Main-sequence step that runs `uv run devbench log-newly-reachable
  <unit-id> --path <p> --method <m> --result <r>` once per newly-reachable
  path on a `behavior-fix` unit and treats a non-zero exit as a hard stop.
  `plugin/devbench-orchestrate/agents/review_team/code-reviewer.md` no
  longer directs the judge at `## Comments` (removed by `read-unit
  --strip-comments` before the Evidence fetch); it reads
  `[NEWLY_REACHABLE] <path> <method> <result>` markers from the
  `## TDD Cycle Log` audit section and raises `NEWLY_REACHABLE_PATH_UNVERIFIED`
  when a `behavior-fix` unit carries none. `plugin/devbench-orchestrate/agents/blocker-resolver.md`
  now requires escalation, never a hand-written substitute, when a marker is
  missing or `log-newly-reachable` fails. `docs/newly-reachable-paths.md` is
  rewritten as v2 around the spec 5.3 marker grammar, the
  `gates.newly_reachable_paths` config block and its precedence, the
  `## Task Type: behavior-fix` keying, the judge-evidence tier, and the rule
  that Definition-of-Done checkboxes are auto-ticked records, never gates.

- **`check-write-path` now attributes its itemized findings to the calling
  unit's own Changes-Manifest scope, while the verdict and status line stay
  repo-wide** (spec `integration-reality-gates-hardening.md` section 4.3,
  AC-9, AC-WP-025; E7-F2-S1-T3). `audit_write_path` in
  `src/devbench/plugin_helpers/permission_flag_writepath.py` gains a new
  keyword-only `scope` parameter, and the resulting `WritePathAudit` gains a
  new `attributed_sites` attribute holding only the assignment/setter sites
  that fall inside the calling unit's own resolved scope. The underlying
  scan stays repo-wide: `verdict`, `mentions`, `assignment_sites`, the
  `findings` count, and the spec 5.2 status line itself are all repo-wide
  RESULTS, computed exactly as before this scope dependency existed and
  never narrowed by scope -- only the itemized findings lines printed below
  the `[PERMISSION_FLAG_WRITE_PATH_AUDIT]` header are scope-limited BLAME,
  matching the pattern the machine-blocking gates already use to attribute
  their own findings. A live write outside that scope still drives the same
  repo-wide verdict a fully unscoped run would reach; it is simply never
  named in the printed findings. A new third rendering fallback line,
  `(no assignment/setter sites found within this unit's scope; N found
  outside scope)`, prints when the repo-wide scan finds real sites but none
  of them fall inside the calling unit's own scope. `devbench
  check-write-path` also gains a new zero-stdout exit-1 terminal: when the
  calling unit's own scope resolution fails (via the shared
  `_resolve_scope_or_report` helper, before `audit_write_path` is ever
  called), the run writes ZERO bytes to stdout and reports the error on
  stderr only, with no spec 5.2 status line at all.

- **Review-time re-run closes 321-D21: a delivered write-path task whose flag
  still classifies `default` now fails code review** (spec
  `integration-reality-gates-hardening.md` section 4.8, AC-20; E7-F2-S1-T1).
  Authoring-time detection in the `spec-to-backlog` skill's Step 3b already
  caught the copy-pattern clause, but nothing re-ran the audit at delivery
  time, so a write-path task could reach review with its flag still
  classifying `default`/`no_write_path`/`not_found` and no judge would ever
  see it. `code-reviewer.md`'s `## Evidence` section now instructs a
  conditional re-run: for a unit whose Acceptance Criteria name a permission
  or eligibility flag, run `uv run devbench check-write-path <unit-id>
  --flag <flag-name>` and treat a `default`, `no_write_path` or `not_found`
  verdict as a new `WRITE_PATH_UNVERIFIED` rejection. The code is registered
  in the `code_review` set in
  `src/devbench/backlog/review_feedback_vocabulary.py` (and no other
  judge's), with a membership test proving both halves of the ownership
  rule, and its docs-table row and inline prompt sentence are generated
  from `JUDGE_CATEGORIES` via `make generate-vocabulary` rather than
  hand-edited.

- **SECURITY: `WritePathAudit.render()` and `render_blocking_finding()` now
  also escape the audited `flag_name` and a `load_error`'s `<error>` text,
  the same way `relative_path` was already escaped, closing the same
  log-injection / evidence-forgery hole on two more untrusted fields**
  (doc_review round 7, E7-F1-S1-T2). `relative_path` was already routed
  through `_escape_untrusted_path_for_rendering` (round 4/5), but the
  `flag_name` interpolated into the `[PERMISSION_FLAG_WRITE_PATH_AUDIT]`
  header line and the `[BLOCKING_FINDING]` sentence, and the `<error>` text
  on a `load_error` line, still reached stdout raw: `flag_name` is
  spec-derived (the `spec-to-backlog` SKILL's Step 3b-ii lifts it out of
  spec prose as `<existing-flag-name>`), not purely operator-typed, and
  `cli._parse_unit_id_and_required_flag_argv` applies no control-character
  rejection to it, while a `load_error`'s `<error>` text can carry a
  locale-translated, non-ASCII `OSError.strerror`. `docs/cli-reference.md`'s
  `check-write-path` stdout enumeration is corrected to state this, pinned
  by a new `TestCheckWritePathRelativePathEscapingDocumented` case -- the
  previous wording attributed the stdout-wide "printable single-line ASCII"
  guarantee to `relative_path` escaping alone, which was false while these
  two fields stayed unescaped.

- **SECURITY: `render_blocking_finding()` now also escapes its
  `new_field_name` parameter, closing the same log-injection /
  evidence-forgery hole on the one remaining untrusted field this module
  rendered raw** (code_review round 8, E7-F1-S1-T2). `new_field_name` has
  identical provenance to `flag_name` above -- the `spec-to-backlog`
  SKILL's Step 3b-iii one-liner passes both `<new-field-name>` and
  `<existing-flag-name>` as placeholders lifted verbatim from spec prose in
  the same instruction -- but round 7's fix escaped `flag_name` and
  `relative_path` while leaving `new_field_name` interpolated raw in the
  same f-string, letting a hostile `new_field_name` forge a standalone
  second `[BLOCKING_FINDING] RESOLVED: ...` acknowledgement line.
  `flag_name`, `relative_path`, a `load_error`'s `<error>` text and
  `new_field_name` are now all routed through
  `_escape_untrusted_path_for_rendering`, making "one escaping contract for
  every untrusted value this module renders" true for every value this
  module interpolates into rendered output, not just three of the four.

- **SECURITY: every surface that renders an untrusted repo-sourced filename
  -- `WritePathAudit.render()`'s `load_error` and assignment-site lines, AND
  `render_blocking_finding()`'s assignment-site sentence -- now escapes it,
  closing a log-injection / evidence-forgery hole** (security_review HIGH,
  round 4; code_review + changes_manifest, round 5; E7-F1-S1-T2).
  `relative_path` on both `FlagAssignmentSite` and `FileLoadError` is derived
  from a filename INSIDE the audited repo -- the untrusted artefact this
  gate exists to examine, not agent- or operator-authored text -- and a
  POSIX filename may embed any byte except `/` and NUL. Rendered unescaped, a
  crafted filename could forge a second `[PERMISSION_FLAG_WRITE_PATH_AUDIT]`
  header line or a forged assignment-site line via an embedded newline,
  duplicate the machine-readable spec 5.2 status line JSON so a consumer
  that greps rather than parsing line 1 reads the forged line, emit `\r`
  plus ANSI erase-line/colour escape sequences that erase already-rendered
  evidence in a terminal, or -- on `render_blocking_finding()` specifically,
  the `spec-to-backlog` SKILL's Step 3b-iii blocking-finding line -- forge a
  second `[BLOCKING_FINDING] RESOLVED: ...` line claiming the operator had
  already acknowledged the finding; security_review, code_review and
  changes_manifest each reproduced one or more of these end to end. Round 4
  fixed `WritePathAudit.render()`'s two lines but left
  `render_blocking_finding()` -- a THIRD surface consuming the same
  `relative_path` field, reachable directly from
  `SKILL.md`'s own Step 3b-iii narrative -- interpolating it raw; round 5
  closes that remaining surface with the same helper. All three now pass
  `relative_path` through `_escape_untrusted_path_for_rendering`
  (`unicode_escape`, then decoded as ASCII) before rendering: every C0/C1
  control character, DEL, non-ASCII byte/character, and the Unicode
  line/paragraph separators U+2028/U+2029 some line-oriented consumers treat
  as a line break become a literal backslash-escape sequence, guaranteeing
  printable-ASCII, single-line output that can never forge structure --
  while the filename stays fully legible and recoverable for an operator to
  act on, and the finding is still reported (escaping, not
  `cli._reject_control_characters`-style rejection, so a hostile filename
  can never make its own finding silently disappear from the audit). The
  escaping contract is now also documented on the operator-facing
  `docs/cli-reference.md` `check-write-path` stdout enumeration, pinned by
  `TestCheckWritePathRelativePathEscapingDocumented` (doc_review +
  changes_manifest, round 5), not only in the source docstring.
  `VERDICT_DESCRIPTIONS[VERDICT_DEFAULT]`'s description is also corrected
  twice more: it previously read as requiring the default/constants path
  signal only on sites whose value could not be resolved, but
  `_classify_path_tiebreak` requires EVERY site -- including one already
  resolved to a literal `default` -- to carry the signal (doc_review, round
  4); and its "every site is a hardcoded literal" disjunct did not cover a
  site whose value is a CALL carrying a literal keyword-default argument
  (e.g. Django's `BooleanField(default=False)`), which also verdicts
  `default` with no default-signal path at all (doc_review, round 5). The
  generated `spec-to-backlog` SKILL Step 3b block is regenerated to match
  both corrections. `render_verdict_reference()` now raises `ValueError`
  naming `VERDICT_DESCRIPTIONS` (rather than a bare `IndexError`) if that
  mapping ever held no verdict other than `live` (code_review, round 4;
  unreachable against the shipped five-entry mapping), and also now rejects
  -- raising `devbench.vocabulary_generation.GuardMarkerError` naming the
  offending key -- any `VERDICT_DESCRIPTIONS` value that itself contains a
  guard-marker literal, closing a non-idempotent-regeneration hole that
  became reachable once descriptions started rendering into the generated
  block (code_review, round 5). `VERDICT_DESCRIPTIONS` is wrapped in
  `types.MappingProxyType` so it is genuinely immutable, not merely
  unrebindable (`Final` is a static-only annotation; code_review mutated it
  at runtime during review) (code_review, round 5). The `FileLoadError`
  docstring's description of `UnicodeDecodeError.__str__`'s shape is
  corrected a second time: the hex vs. byte-range form is determined by the
  offending byte SPAN (`exc.end - exc.start == 1`), not by the count of
  remaining unread bytes -- a real binary file (a PNG, a JPEG, or any
  content starting with an invalid UTF-8 lead byte, the modal `load_error`
  case) reports a single hex byte even with many bytes remaining, not a
  range (doc_review, round 5). The Step 3b guard-marker find/splice logic,
  previously a copy-paste fork of `devbench.vocabulary_generation`'s shared
  `_find_guard_block`/`replace_guarded_block` implementation, now delegates
  to that shared implementation (parameterised with this module's own
  marker literals, remediation command, and a `reject_duplicate` flag) --
  the module-local `SkillGuardMarkerError` class and
  `_locate_skill_guard_block` function are removed (code_review, round 6).

- **Write-path audit: unreadable files become `load_error` findings, the
  assertion-free `test_unreadable_binary_file_is_skipped_not_fatal` is rewritten
  to assert outcomes, and the `spec-to-backlog` SKILL's Step 3b verdict prose is
  now generated from the module's constants** (spec `integration-reality-gates-hardening.md`
  section 4.8, Section 7; issue #16; from #321; E7-F1-S1-T2). `audit_write_path` in
  `src/devbench/plugin_helpers/permission_flag_writepath.py` used to silently
  `continue` past a file it could not decode or read (`except (UnicodeDecodeError,
  OSError): continue`), so a repo with one unreadable source file produced a
  verdict computed from a silently truncated scan -- the fail-open shape spec
  Section 7 bans. Every unreadable file now becomes a `FileLoadError` finding
  naming the file's relative path and the underlying decode/read error, carried
  on `WritePathAudit.load_errors` and rendered alongside the assignment-site
  findings; the verdict itself is still computed from the readable files only.
  The recorded error text never carries an absolute filesystem path (an
  `OSError`'s `strerror` is used, never its `filename` attribute) and never
  echoes the surrounding, attacker-influenced byte content itself (it may
  report a position, and for a single offending byte its hex value, but
  never the byte content). The verdict vocabulary is now also
  exposed as a public, ordered `VERDICT_DESCRIPTIONS` mapping with a
  `render_verdict_reference()` renderer; the `spec-to-backlog` SKILL's Step 3b
  verdict sentence and a sample `audit_write_path(...).render()` output are
  generated from it inside `<!-- generated:write-path-verdicts -->` guard
  markers (the same grammar `devbench.vocabulary_generation` established), fixing
  the SKILL prose that still named the pre-rework verdict `default_only` after
  E7-F1-S1-T1's classifier rework retired that spelling in favour of `default`.
  `regenerate_skill_step_3b` regenerates the block in place; a hand-edit to the
  generated block now fails the pin test in
  `tests/test_plugin_helpers/test_permission_flag_writepath.py`, naming the
  regeneration command.

- **New CLI verb `check-write-path <id> --flag <name>` replaces the write-path audit's
  skill-invoked `python -c` one-liner, and the classifier it runs is rebuilt around
  assignment-context analysis** (spec `integration-reality-gates-hardening.md` section
  4.8; issue #16; from #321; `judge-evidence` tier; E7-F1-S1-T1). `_classify` in
  `src/devbench/plugin_helpers/permission_flag_writepath.py` used to decide `live`
  purely from path-name vocabulary, so a flag hardcoded in an `initialState` literal
  under a `store`/`slice`-named directory was reported `live` (321-D03, the flagship
  false-`live`) and any shape the vocabulary did not recognise auto-blocked, producing
  an every-repo-blocks defect. The classifier now decides primarily from the ASSIGNED
  VALUE: a write classifies `live` when the value is an attribute/subscript access on a
  request/action/payload/param-like identifier (`action`, `payload`, `request`, `req`,
  `event`, `args`, `kwargs`, `params`, `context`, `ctx`) or is a non-literal argument
  passed to a `set<Flag>(...)` setter call -- except a parenthesised or call-wrapped
  setter argument (`set_is_premium_eligible(bool(request.x))`), which is captured as a
  truncated fragment carrying an unmatched opening parenthesis, or a `)` character
  inside a QUOTED setter argument (`set_isPremiumEligible("a)b")`, security fix M-1),
  which truncates the capture into a fragment carrying an odd count of `"` or `'`
  characters -- both classify `indeterminate` rather than `live`, since a truncated
  fragment cannot be reliably classified either way, or a setter argument longer than
  512 characters (security fix,
  ReDoS: many unclosed `set<Flag>(` prefixes on one line previously cost time quadratic
  in the line's length), which no longer matches the setter shape at all and falls
  through to `no_write_path` (blocking) rather than `live` when it is the flag's only
  site; literal-only assignments classify `default`
  even inside live-named directories, with Rails and Django layouts in the parametrised
  matrix (321-D28), and literal recognition tolerates idiomatic noise around the value
  (wrapping parens, leading `!`/`!!` negation, a trailing line/block comment, a trailing
  `as <Type>`/`satisfies <Type>` assertion, a trailing comma). A shape expression
  analysis cannot resolve classifies `indeterminate` and falls back to a
  path-vocabulary tiebreak, consulted ONLY for these unresolved shapes; the tiebreak
  can still resolve one to `default` from the file's path but can NEVER resolve one to
  `live` -- the tiebreak's `live` branch was removed entirely, so it can no longer
  return `live` under any input, closing a fail-open path that would have reproduced 321-D03 for any shape
  expression analysis could not resolve. Every verdict is reported with its evidence
  lines and none of them auto-blocks except `default`, `no_write_path` and
  `not_found`; a site's matched source line is never printed, only its
  `relative_path:line_number` and `expression_verdict`, so a credential-shaped
  assignment is never echoed into gate output. `audit_write_path` stays importable for
  the `spec-to-backlog` skill's Step 3b narrative (AC-WP-001). The file enumerator also
  excludes any symlink whose resolved real location escapes the audited repo root.

- **`check-fixture-consistency` is wired into the `mark-done` done-path gate and proven
  end to end by a journey suite** (spec `integration-reality-gates-hardening.md` section
  4.2, 4.7 done-path sentence, 4.3 attribution rule; issue #17; E6-F2-S1-T2). A passing
  run now persists `[GATE_PASS fixture_consistency] <iso-utc> <scope-hash>` to the
  calling unit's audit trail from the command itself (never agent prose), closing the
  deadlock where `mark-done` already required a fresh `[GATE_PASS fixture_consistency]`
  record that no command could write, leaving an operator waiver as the only route to
  `done` for a unit whose repo resolves this gate enabled (a unit whose repo resolves it
  disabled reaches `done` without either); a failing or erroring run persists no record.
  `mark-done` already
  enforced this for every `constants.GATE_TIERS` machine-blocking gate generically
  (`BacklogManager._check_gate_pass_done_invariant`) -- `fixture_consistency` was
  declared machine-blocking since E2-F2 but had no writer until now, so this closes the
  invariant's last real gap without adding a gate-specific branch anywhere in that
  method. The persisted record's scope hash is computed over the calling unit's own
  Changes Manifest via the shared `work_unit_scope.resolve_changed_files` helper (the
  same one `check-reachability`/`check-shared-file-impact` use) so `mark-done`'s
  existing stale-record recompute (spec 4.2 AC-7) can validate it identically to every
  other non-`ancestry` gate, even though this gate's own catalog SCAN remains genuinely
  repo-wide and the JSON status line still carries no `scope_hash` field. Attribution
  (spec 4.3): a `missing_key` finding only blocks when the fixture/source file it names
  is a member of the calling unit's own scope -- a mismatch outside that scope is still
  reported (repo-wide, exactly as before) but never counted toward `status` or
  persisted-record eligibility; `coverage_shortfall`/`load_error` findings are unaffected
  by scope and always block, since neither describes a problem attributable to one
  particular file. The new hermetic journey suite,
  `tests/test_integration/test_gate_fixture_e2e.py`, drives the real CLI over scratch git
  fixture repos for the full AC-14 matrix (block, pass, disabled, waiver, stale-record,
  attribution) plus the four adversarial fixture shapes spec Section 10 names for this
  gate: a typo'd `identifier_field`, an empty canonical catalog, an in-fixture waiver
  visible in `git diff`, and a seeded source literal reported with `file:line`.

- **Fixed: an apostrophe (or any other special character) in a fixture's filename could
  defeat spec 4.3 attribution and let an inconsistent fixture reach `done`** (security
  fix; E6-F2-S1-T2). Attribution used to recover a `missing_key` finding's offending file
  path by re-parsing the finding's free-text `message` with a regex anchored on the
  message template's leading `Fixture '<location>'`/`Source file '<location>'` fragment.
  That message interpolates the path into a single-quoted slot with no escaping, so a
  scan target or classified source file legitimately named with an apostrophe (e.g.
  `o'brien.json`) truncated the regex capture at the first `'`, producing a path that was
  never a member of the calling unit's own resolved scope even when the real file was --
  silently misattributing an IN-SCOPE finding as out-of-scope, so it stopped blocking, a
  `[GATE_PASS fixture_consistency]` record was persisted, and `mark-done` reached `done`
  on an inconsistent fixture catalog. `fixture_consistency.FixtureFinding` now carries a
  structured `location` field, populated directly by the two `missing_key` producers
  (`_check_scan_targets`/`_check_source_literals`) at construction time -- there is no
  longer any free text for the attribution rule to parse, so no path shape (apostrophe,
  colon, newline, a `..` component, or any other legal character) can mis-split it. The
  two prior regex helpers (`cli._FIXTURE_MISSING_KEY_LOCATION_RE`,
  `_FIXTURE_LOCATION_LINE_SUFFIX_RE`) and the free-text parser they backed
  (`_fixture_finding_location_path`) are deleted outright, not kept as a fallback.
  `plugin/devbench-orchestrate/agents/review_team/test-reviewer.md` rubric item 54's
  fixture-catalog guidance is also tightened: a `[missing_key]` finding printed under the
  `OK:` banner is no longer dismissed unconditionally -- the reviewer must first cross-
  check the finding's quoted path against the unit's own Changes Manifest, since
  attribution is only as trustworthy as that self-attested Manifest and `mark-done` does
  not independently verify it against the real diff. The evidence header presented to
  that reviewer for this check now also documents that a passing run with a non-empty
  scope persists a `[GATE_PASS fixture_consistency]` record.

- **Fixed: an independently-spelled repo-relative path (a leading `./`, an internal
  `a/../` component, or a trailing `/`) could also defeat spec 4.3 attribution, reaching
  the same end state as the apostrophe bug above** (security fix; E6-F2-S1-T2). Attribution
  compared a scan target's configured `path` against the calling unit's resolved
  Changes-Manifest scope with NO canonicalisation on either side, so a scan target
  declared `./mock.json` or `sub/../mock.json` compared unequal to that SAME file's
  canonical `mock.json` Manifest spelling -- an IN-SCOPE finding was silently
  misattributed as out-of-scope, a `[GATE_PASS fixture_consistency]` record was
  persisted, and `mark-done` reached `done` on an inconsistent catalog. Both the
  finding's `location` and every Manifest scope path are now run through the new
  `fixture_consistency.normalize_repo_relative_path` helper before comparison -- a
  purely lexical operation (`posixpath.normpath`) that never touches the filesystem
  (so it cannot resolve a symlink and change which unit a finding is attributed to) and
  never case-folds (so a path differing only in case is never treated as equivalent to
  a genuine repo-relative Manifest entry). An absolute path or a path that escapes the
  repo root via `..` is likewise never treated as equivalent to a genuine repo-relative
  Manifest entry, because `posixpath.normpath` never fabricates a root-relative
  interpretation for either shape, not because of the case-folding behaviour above.

- **`check-fixture-consistency` gains a config-gated source-literal extraction mode**
  (spec `integration-reality-gates-hardening.md` section 4.7 bullet 4; issue #17
  AC-19; E6-F2-S1-T1). `gates.fixture_consistency.extract_source_literals`
  (default `false`) additionally scans the classified source files in the
  repo checkout -- enumerated via
  `devbench.source_classification.iter_classified_source_files`, the single
  owner of extension classification (PM-3), which prunes a fixed set of
  dependency/build/vendor directories during the walk -- for identifier
  literals whose key matches a configured `identifier_field`. A literal is
  resolved against the union of every canonical source sharing that
  `identifier_field` name (never cross-producted against an unrelated
  canonical source), flagging one absent from all of them with a
  `missing_key` finding carrying `file:line` (a 1-based line number). This
  catches catalog drift hiding in a hard-coded route table, enum-like
  constant list, or seed script that a reviewer would otherwise have to
  notice by eye. The mode is heuristic (a regex-based per-line scan, not a
  real parser) and defaults off for exactly that reason -- see
  `docs/devbench-yaml-reference.md`'s
  `gates.fixture_consistency.extract_source_literals` section for the full
  documented accuracy bounds, including that a single-line triple-quoted or
  genuinely empty string value is never matched rather than misreported with
  an empty literal value. Enabling the mode when the repo checkout resolves
  zero classified source files is a loud, pre-scan error naming the resolved
  scope and the config key (mirroring the existing empty-`scan`-list and
  zero-match-`identifier_field` loud-error shapes; this includes a repo whose
  only classified sources live entirely under a pruned directory); a
  directory that cannot be listed produces exactly one `load_error` finding
  naming the unreadable directory rather than silently skipping that subtree;
  a source file that raises `UnicodeDecodeError` or `OSError` while being
  read produces exactly one `load_error` finding naming the file, and every
  other classified source file is still scanned. There is no waiver
  mechanism for a source-literal finding -- the in-fixture `allow_missing`
  marker applies only to the structured scan-target cross-reference below.

- **`check-shared-file-impact`'s auto-derived-registry scan now shares its file
  enumeration with `check-fixture-consistency`'s new source-literal mode**
  (E6-F2-S1-T1 round-2 code_review Blocking 6 DRY finding). `cli.py`'s
  `_iter_shared_file_scan_candidates` delegates to
  `devbench.source_classification.iter_classified_source_files` instead of
  hand-copying that walk's body, so the two gates' pruned-directory set can
  never silently drift apart. That delegation also means an unreadable
  directory under the scanned repo -- previously silently skipped, returning
  a partial derived registry that looked identical to a clean, complete scan
  -- now raises `ERROR: import scan failed for directory <path>: <reason>`,
  the same loud error shape `_derive_shared_file_registry` already uses for
  an unreadable file, caught by `check-shared-file-impact`'s existing
  import-scan-failure handling rather than escaping as an unhandled
  traceback. Its own directory-not-found error message now names the
  unreadable directory repo-relatively (matching the file-level message
  next to it), rather than an absolute, tmp-path-prefixed form.

- **SECURITY: `iter_classified_source_files` no longer follows a symlink
  whose target resolves outside the walked root** (security_review round-3
  MEDIUM finding, E6-F2-S1-T1). A candidate file's repo-relative NAME
  previously determined whether it was enumerated, while a caller reading
  its content follows any symlink in the path to whatever it actually
  points at -- so a symlink committed inside a repo checkout, pointing
  outside it, was a read primitive for arbitrary filesystem content under a
  path that looked like it belonged to the scanned repo, shared by both
  `check-fixture-consistency`'s `extract_source_literals` mode and
  `check-shared-file-impact`'s auto-derived-registry scan. The boundary is
  now checked against the resolved real path (`os.path.realpath`), with
  both the candidate and the walked root resolved before comparing (so a
  root itself reached through a symlink, e.g. `/tmp` on macOS, is not
  spuriously treated as excluding everything under it). A symlink whose
  target ALSO resolves inside the root -- including a DANGLING one -- is
  still included, unchanged from prior behaviour; only a target resolving
  outside the root, live or dangling, is now excluded.

- **SECURITY: a `check-fixture-consistency` source-literal `missing_key`
  finding never echoes any part of an extracted value, regardless of
  length** (security_review AND code_review round-4, convergent findings;
  CLAUDE.md "Sensitive Data Handling"; E6-F2-S1-T1). A prior length
  threshold of 32 characters, below which a value was shown in full, plus a
  disclosed 4-character prefix on longer values, both leaked real
  credential shapes: a Stripe live secret key and a 32-character session
  identifier sat exactly on the old threshold and were echoed in full, and
  a 4-character prefix is exactly the length of common credential-type
  prefixes (`ghp_`, `AKIA`, `AIza`, `eyJh`), disclosing credential type and
  issuer with no review value `file:line` did not already provide.
  Redaction is now unconditional: the finding prints
  `<redacted, N chars total; see file:line above to inspect it directly>`
  naming only the value's original length, never any of its content,
  applied uniformly regardless of the value's shape or length. The finding
  still carries `file:line` and the matched field name. Documented
  alongside the mode's other accuracy bounds in
  `docs/devbench-yaml-reference.md` and mirrored in the `configure-devbench`
  skill's wizard entry.

- **The `allow_missing` fixture-catalog waiver moves into the fixture artifact**
  (spec `integration-reality-gates-hardening.md` section 4.7 bullet 5, PM-5's
  in-diff exception; issue #17, E6-F1-S1-T2). A waiver that scopes an
  intentional not-found/empty-state edge-case fixture used to live in
  workspace config (`gates.fixture_consistency.scan[].allow_missing`),
  invisible to a reviewer who never opens `devbench.yaml` for the unit under
  review. It now lives IN the scanned fixture file itself, as a structured
  `{"allow_missing": {"reason": "<non-empty reason>"}}` marker attached
  directly to the waived record -- visible in the same diff the reviewer is
  already looking at. Complete replacement, not an addition:
  `gates.fixture_consistency.scan[].allow_missing` is a removed config key
  (it shipped only in unmerged draft PR #322, so no migration path is owed
  per spec Section 6) -- `config_loader.py` fails config load fast on a
  residual key, naming the in-fixture replacement. A malformed marker (wrong
  shape, or a record missing a non-empty `reason`) raises rather than
  silently suppressing, and so does a well-formed marker attached to a
  record whose identifier field never resolves (a typo'd/absent field name,
  or a marker at the fixture's envelope level) -- validated unconditionally,
  never only on a dict that also happens to resolve an identifier, since a
  waiver that can never be matched to a record is dead configuration.
  Every applied waiver is itself surfaced as a
  `waiver_applied` finding in `check-fixture-consistency`'s own report --
  on BOTH the pass and the fail path, since visibility must not depend on
  whether the run also happens to contain an unrelated blocking finding.
  A validly waived record does not itself fail the gate:
  `cmd_check_fixture_consistency` computes `status`/exit code from the
  BLOCKING finding kinds only (`fixture_consistency.BLOCKING_FINDING_KINDS`
  -- `missing_key`, `coverage_shortfall`, `load_error`), so a scan whose
  only finding is `waiver_applied` still reports `status: "pass"` and
  exits 0.

- **`check-fixture-consistency` no longer degrades a misconfigured gate into a
  passing or misleading result** (spec `integration-reality-gates-hardening.md`
  section 4.7, register findings 322-D02/D03/D05; issue #17). At HEAD, two
  distinct degenerate-but-configured shapes each silently produced the wrong
  outcome. First: a typo'd `identifier_field` and a canonical source that is
  genuinely empty of that field were INDISTINGUISHABLE (both reduced to an
  empty resolved canonical identifier set), and both mass-false-positived
  every scanned reference as a `missing_key` finding (322-D02 typo, 322-D03
  genuinely-empty; an exit-1 failure, never a silent self-disable). Second,
  and separately: an enabled gate (non-empty `canonical_sources`) with a
  resolved `scan` list of zero targets DID silently self-disable, printing a
  passing result despite inspecting nothing (322-D05). Both shapes now raise
  before any misleading output is produced -- the empty-`scan` case before
  any file is even read -- and `cmd_check_fixture_consistency` catches the
  raise, exits 1, and prints the one-line diagnostic
  `ERROR: identifier field '<f>' matched zero records in <path>` or
  `ERROR: gate enabled but scan list is empty` to stderr (these name the
  misconfiguration, not a prescribed fix -- the field/file names in the
  message are themselves the actionable detail). Scan and canonical file
  parsing also moves off the old implicit-JSON-fallback: a
  `.json`/`.yaml`/`.yml` extension dispatches explicitly, and any other
  configured extension is now exactly one `load_error` finding naming the
  file, never a silent (and possibly wrong) JSON-parse attempt on unrelated
  content. The command now prints the spec 5.2 gate status line as the FIRST
  stdout line on every path that reaches gate resolution (an unresolvable
  unit id or a repo with no configured local path still write nothing to
  stdout, exactly as before this change):
  `{"gate": "fixture_consistency", "status": "disabled"}` when unconfigured,
  and otherwise `{"gate": "fixture_consistency", "tier": "machine-blocking",
  "status": "pass"|"fail"|"error", "findings": <int>}` -- `"error"` is new
  vocabulary for the two loud pre-flight failures above, since every prior
  gate command's status line only ever needed `"pass"`/`"fail"`; the shared
  `constants.GATE_STATUS_ERROR` constant backs it, alongside the existing
  `_DISABLED`/`_PASS`/`_FAIL` members. A correctly configured gate (non-empty
  `canonical_sources`, non-empty `scan`, a resolvable `identifier_field`) is
  unaffected: it reports the same findings as before this change. An
  empty-or-whitespace `<unit-id>` argument is also now a usage error caught
  before any other resolution step, printing
  `ERROR: check-fixture-consistency requires a non-empty <unit-id>` to
  stderr and exiting 2 -- at HEAD such a value fell through to the
  work-unit-lookup failure and exited 1.

- **`check-shared-file-impact` is wired into the done path and persists a
  `[GATE_PASS shared_file_impact]` machine record, making an already-enabled
  gate satisfiable for the first time** (spec
  `integration-reality-gates-hardening.md` sections 4.1, 4.2, 4.6, 5.2, 5.3;
  finding 318-D15). At HEAD, `constants.GATE_TIERS` already declared
  `shared_file_impact` machine-blocking and
  `BacklogManager._check_gate_pass_done_invariant` already required a fresh
  `[GATE_PASS shared_file_impact]` record for any repo with the gate enabled,
  but no code path could ever write that record (`compose_gate_pass_record`
  had exactly two call sites: ancestry and reachability). A repo that enabled
  the gate therefore had `mark-done` permanently deadlocked, satisfiable only
  by an operator `[GATE_WAIVER shared_file_impact]`. This change closes that
  deadlock by making `check-shared-file-impact` the command that produces the
  record the invariant already demanded. The command now prints the spec 5.2
  gate status line as the FIRST stdout line:
  `{"gate": "shared_file_impact", "status": "disabled"}` (the exact bytes
  `json.dumps`'s default separators emit) and exits 0 when the gate is
  disabled or unconfigured for the repo (spec 4.1, AC-4); otherwise
  `{"gate": "shared_file_impact", "tier": "machine-blocking",
  "status": "pass"|"fail", "findings": <int>, "scope_hash": "<sha256>"}` followed by
  the JSON findings payload, with `findings` counting the attributed
  `new_failures` on a blocking run and `0` on either passing shape (a no-match
  no-op, or a matched run with zero attributable new failures). A passing run
  with a non-empty Changes Manifest additionally appends
  `[GATE_PASS shared_file_impact] <iso-utc> <scope-hash>` to the unit's audit
  trail through `devbench.gate_records.compose_gate_pass_record` -- the sole
  authorized builder of that marker text, so the record is always written by
  the command, never by agent prose; a blocking run writes no `[GATE_PASS]`
  record. `mark-done`'s already-generic
  `BacklogManager._check_gate_pass_done_invariant` (spanning every
  `constants.GATE_TIERS` machine-blocking gate since E2-F2-S1-T2) is what
  enforces this: an enabled gate with no fresh record and no
  operator-attributed `[GATE_WAIVER shared_file_impact]` marker refuses
  naming the exact `uv run devbench check-shared-file-impact <unit-id>`
  remediation, and editing a Changes-Manifest file after the record was
  written re-derives a different scope hash and reads the record as stale.
  Unlike `check-reachability`, `check-shared-file-impact` itself never reads
  `[GATE_WAIVER shared_file_impact]` markers to clear individual findings --
  the whole-gate `mark-done` bypass is its only waiver interaction.
  `docs/cli-reference.md` gains a `check-shared-file-impact` entry (under
  [Orchestrator helpers](docs/cli-reference.md#orchestrator-helpers-invoked-by-agents),
  alongside `check-reachability` and `check-fixture-consistency`, per the
  [`## Gates`](docs/cli-reference.md#gates) section's own intro note that the
  per-gate check verbs continue to live under Orchestrator helpers until a
  follow-up unit relocates them -- `check-ancestry` itself in fact lives under
  `## Git operations`, not Orchestrator helpers) pinned by
  `tests/test_docs/test_cli_reference_shared_file_impact.py`, and
  `tests/test_integration/test_gate_shared_file_e2e.py` proves the wiring end
  to end over real, hermetic git fixture repos: block, pass, disabled, waiver,
  stale-record and attribution journeys, plus a pre-existing-vs-introduced
  failure pair, a corrupt-baseline loud failure, and an auto-derived registry
  that yields the expected shared set. The `docs/cli-reference.md` entry that
  spec Section 8 calls for (`#318`'s missing-entry gap) also lands in this
  change; 318-D15 itself is only the done-path requirement above.

- **`check-shared-file-impact` gains an auto-derived shared-file registry from
  import fan-in, with a tunable threshold** (spec
  `integration-reality-gates-hardening.md` section 4.6, D-9; issue #13 AC4).
  `gates.shared_file_impact.auto_derive_registry: true` (default `false`)
  computes the shared-file set as the files imported/required by more than
  `gates.shared_file_impact.fan_in_threshold` (default `3`, must be an
  integer `>= 1`) distinct modules, via language-appropriate import scanning
  (`devbench.source_classification.extract_import_targets`, dispatched on
  the module's existing extension sets: Python, the JS/TS family (including
  `export ... from`/`export * from`), Go (every grouped import block, not only
  the first), Ruby, Java/Kotlin, Swift, C#, and PHP) and
  `devbench.cli._derive_shared_file_registry`'s fan-in count, resolved
  language-appropriately: for Python, the JS/TS family, Ruby and PHP, against
  the importing file's own directory for a leading-`.` target and against the
  repo root ONLY (never a `src/` fallback) for a leading-`/` target (this
  leading-`/` bucket does not arise for Python in practice, since Python's own
  extractor never emits a `/`-prefixed target); for Go, Java/Kotlin, C#, and
  Swift, ALWAYS against the repo root and a top-level
  `src/` directory regardless of the target's own leading character (their
  import grammars have no relative form); and for a bare/absolute Python,
  PHP, Go, Java/Kotlin, C#, or Swift target with neither prefix, likewise
  against the repo root and a top-level `src/` directory -- never a bare
  global basename index, which would credit an
  unrelated same-named file (e.g. a stdlib `import types` crediting an
  unrelated `mylib/types.py`); a bare/aliased JS/TS or Ruby target with
  neither prefix is deliberately never resolved and casts no fan-in vote; a
  directory-form import (`from mypkg
  import X`, `import {A} from './lib'`) resolves to that package's entry file
  (`__init__.py`/`index.<ext>`); a target resolving to more than one candidate is
  credited to neither, with a `WARNING:` on stderr naming the ambiguity. The
  derived set is unioned ADDITIVELY with the hand-maintained
  `gates.repos.<org/repo>.shared_file_impact.patterns` glob list -- never a
  replacement -- and is printed in the JSON payload on every invocation of an
  ENABLED gate that reaches a verdict (pass or block), `auto_derive_registry`
  enabled, matched or not; an invocation that raises before reaching a verdict,
  and an invocation where `gates.shared_file_impact.enabled` is `false` (which
  writes its own PASS verdict record and returns before any payload is built),
  both print no payload at all. It is additionally cached alongside the baseline record, as
  `<branch-point-sha>.derived-registry.json` (a sibling of the baseline
  record's own `<branch-point-sha>.json`, never `<baseline_path>` literally
  suffixed, which would resolve to a nonexistent
  `<sha>.json.derived-registry.json`), on a MATCHED invocation (once a branch
  point/baseline is actually resolved) and as soon as that baseline is loaded,
  before the full-suite command is even resolved -- so a matched invocation
  that later raises can still leave this cache written with no comparison ever
  completed. This cache is write-only: no devbench command reads it back; it
  exists so an operator can recover what registry was in effect for a given
  verdict by inspecting the file directly on disk. `enabled`, `auto_derive_registry` and
  `fan_in_threshold` are all read exclusively through
  `resolve_gate_config("shared_file_impact", repo)` (spec 4.1 AC-27), via the
  same `_load_gate_config_or_report` helper `check-ancestry`/`check-reachability`
  use; a non-integer or `< 1` threshold, or an unrecognised key inside the
  `gates.shared_file_impact` block, fails config load naming the offending
  key. An unreadable source file (including a dangling symlink) encountered
  during the scan is a loud `ERROR: import scan failed for <path>: <reason>`
  (exit 1), never a partial derived set.

- **`check-ancestry` gains a squash-aware second probe, a fatal `git fetch`,
  and a configured tracking remote** (spec `integration-reality-gates-hardening.md`
  section 4.5, 317-D02; AC-17, AC-15; issue #12). A strict
  `git merge-base --is-ancestor` probe still runs first, but a "not an
  ancestor" answer (rc=1) no longer prints `BLOCKED` outright: a second
  probe searches for the dependency's merged PR via `gh pr list --search
  "<sha>" --state merged --base <default-branch>`, and a pass through it is
  recorded in the status line as `mode: "squash-pr"` -- a squash-merged,
  rebased, or fix-pack-landed dependency the strict probe can never see is
  no longer misreported as blocked. Both probes' outcomes are always
  printed together on every terminal decision. `git fetch` is now FATAL
  (`ERROR: git fetch '<remote>' failed: <stderr>`, exit 1) instead of the
  previous best-effort warning-and-continue, so a stale local view can
  never produce a false answer; the remote name is resolved from the
  repo's own `git config --get branch.<default-branch>.remote` rather than
  assumed to be the literal `origin`. The command is also brought onto the
  shared gate surface: gate enablement is read exclusively through
  `resolve_gate_config("ancestry", repo)`, printing
  `{"gate": "ancestry", "status": "disabled"}` and exiting 0 before any git
  call when disabled, or the spec 5.2 status line as the FIRST stdout line
  on an enabled run. An empty `dependency-ref` is now a usage error (exit
  2, was exit 1); `git merge-base --is-ancestor` returning rc>=2 is an
  evaluation failure, never reported as "not merged". `docs/cli-reference.md`
  and `docs/cross-backlog-dependencies.md` document the two-probe contract;
  no document routes a squash-merged dependency to the manual-blocker idiom
  as its only remediation any more.

- **`check-reachability` is wired into the done path and persists a
  `[GATE_PASS reachability]` record; `mark-done` now enforces it, and an
  operator-attributed `[GATE_WAIVER reachability]` marker is the escape
  valve** (spec `integration-reality-gates-hardening.md` sections 4.2 and
  4.4 final bullet, 4.9, Section 2 G4/G7; AC-6, AC-7, AC-15, AC-16; issue
  #10). A clean enabled run with at least one Manifest file appends exactly
  one `[GATE_PASS reachability] <iso-utc> <scope-hash>` line to the unit's
  audit section through the new `devbench.gate_records.compose_gate_pass_record`
  builder, `<scope-hash>` matching the status line's `scope_hash`
  (`devbench.gate_records.compute_scope_hash` over the sorted Manifest file
  list plus each file's git blob hash), so any later edit to an in-scope
  file invalidates the record; a failing run or a disabled gate writes no
  record. `mark-done` on a unit whose repo has `gates.reachability.enabled`
  true now refuses (exit 1, no status write) unless a fresh
  `[GATE_PASS reachability]` record exists, naming the exact remediation
  `uv run devbench check-reachability <unit-id>`; a record whose recomputed
  scope hash no longer matches is refused with `ERROR: gate 'reachability'
  record is stale (scope changed since it ran)`. `devbench.gate_records`
  gains `gate_waiver_records`/`gate_waiver_targets`, the sole scan-and-parse
  loop for the `[GATE_WAIVER <gate>]` marker family: `check-reachability`
  reads it before scanning, so a candidate with an OPERATOR-attributed
  waiver on file is reported `[WAIVED] <target> -- <reason>`, excluded from
  the blocking `findings` count, and the run exits 0 when every finding is
  waived this way; an executor-attributed waiver alone is scanned normally
  and never suppresses a finding or contributes to a `[GATE_PASS
  reachability]` write, since reachability is machine-blocking (spec
  Section 3.6/D-6) and an executor cannot self-certify. A malformed
  `[GATE_WAIVER reachability]` marker (missing target, missing or empty
  reason) is never silently treated as "no waiver": both `check-reachability`
  and `mark-done`'s generic gate-record invariant fail loud, naming the unit
  and the offending line. `docs/cli-reference.md`'s `check-reachability`
  entry documents the record shape, the `mark-done` requirement, the
  stale-record message and the `log-waiver` invocation that clears an
  artifact.

- **`check-reachability` gains transitive reachability and the
  `gates.reachability.entry_points` config tunable** (spec
  `integration-reality-gates-hardening.md` section 4.4 bullet 2; issue #10
  AC2). A referrer found by the word-boundary matcher now clears a
  candidate only when the referrer is itself reachable from a configured
  entry-point set, walked backward through `_search_reachability_importers`
  with a cycle-safe visited set (`_is_reachable_from_entry_points`); a
  candidate whose every referrer is itself unreachable is reported
  `[POTENTIALLY UNREACHABLE via orphan-chain]`, distinct from the
  no-referrer-at-all `[POTENTIALLY UNREACHABLE]` shape, and both count
  toward the spec 5.2 status line's `findings` total.
  `gates.reachability.entry_points`, a list of repo-relative paths, is
  parsed by `_parse_gates_config` into the new `GateReachabilityConfig`
  dataclass
  with fail-fast `ValueError` (naming `gates.reachability.entry_points`
  and the offending value) on a non-list value, a non-string element, an
  empty-string element, an absolute path, or a path containing a `..`
  segment (rejected at both the loader and the JSON schema's `entry_points`
  item `pattern`, since `repo_path / entry_point` would otherwise silently
  discard `repo_path` for an absolute `entry_point`); absent or empty
  falls back to a built-in default derived from
  `devbench.source_classification`'s entry-point-stem convention (`main`,
  `app`, `index`, `__init__`, ...) rather than an empty walk, resolved
  (with per-field provenance) exclusively through
  `resolve_gate_config("reachability", repo)`. An explicit,
  project-configured entry point that does not exist in the repo checkout
  fails the run loudly before any candidate is examined. A referrer met
  during the entry-point walk that itself cannot be read (permission
  failure or non-UTF-8 decode failure) no longer silently resolves to an
  "unreachable" verdict; it is rendered as a counted `[LOAD_ERROR]`
  finding naming that referrer, and the candidate under examination yields
  no `[OK]` / unreachable / orphan-chain verdict block in that run.
  `src/devbench/config-schema.json` and `sample-config.yaml` gain the new
  key with `additionalProperties: false` preserved at every level.

- **`check-reachability` reworked into the reachability gate: word-boundary
  matching, source-classified scope, loud `git grep` semantics, and
  `log-waiver` replacing the source-comment escape hatch** (spec
  `integration-reality-gates-hardening.md` section 4.4; machine-blocking,
  `constants.GATE_TIERS`; register findings 315-D01, 315-D02, and the
  rc-swallowing / `[SKIPPED]` / defer-marker rows). Five defects in the PR
  #315 cherry-pick made the command's verdict untrustworthy, all sharing the
  same code path and fixed together: (1) the substring `--fixed-strings`
  grep cleared an artifact exporting `Card` on any file containing
  `Cardinal` or `discardCards` -- replaced with a word-boundary
  `git grep --word-regexp --fixed-strings` search; (2) the grep ran
  repo-wide, so a mention in `CHANGELOG.md` or a design doc cleared an
  orphan -- matching now runs only over pathspecs derived from
  `devbench.source_classification.SOURCE_EXTENSIONS`; (3) every `git grep`
  rc besides 0 was treated as "no match" and swallowed by a `continue` --
  rc=1 is still no-match data, but rc>=2 now exits 1 with
  `ERROR: git grep failed: <stderr>` on stderr; (4) an unreadable candidate
  file printed a silent `[SKIPPED]` token and passed -- it is now a counted
  `[LOAD_ERROR]` finding that drives exit 1; (5) the `devbench-defer-reachability`
  source-comment escape hatch is deleted everywhere in `src/devbench/cli.py`,
  replaced by the structured `[GATE_WAIVER reachability]` marker `uv run
  devbench log-waiver <judge> <unit-id> --gate reachability --target <t>
  --reason <r> --operator` writes to the unit's audit trail. In the same
  change, scope now comes from the shared
  `devbench.work_unit_scope.resolve_changed_files` (the PR #315 near-copy
  `_collect_reachability_new_files` is deleted with zero remaining callers),
  a disabled/unconfigured gate prints `{"gate": "reachability", "status":
  "disabled"}` and exits 0, and an enabled run prints the spec 5.2 status
  line (`gate`, `tier`, `status`, `findings`, `scope_hash`) as the first
  stdout line before any human-readable findings.
  `plugin/devbench-orchestrate/agents/review_team/code-reviewer.md`'s
  REACHABILITY rubric (item 57 and sub-items 57c/57e) is rewritten to match:
  Manifest-scoped evidence, the `[GATE_WAIVER reachability]` waiver in place
  of the deleted `[DEFERRED]` token, and `[LOAD_ERROR]` documented as a
  blocking finding rather than an informational `[SKIPPED]`. Transitive
  reachability and the `gates.reachability.entry_points` tunable are
  deliberately left to a follow-up task on top of this corrected matcher.

- **`git-ops-finalize` composes a provenance-driven PR body with a closing-keyword
  block instead of a plain body** (spec `integration-reality-gates-hardening.md`
  section 4.13, D-17; AC-E2-F9-S1-T1-1 through -6; issue #334). A new persistent
  config key, `git_ops.provenance_path` (default absent, which preserves today's
  plain body), plus a per-invocation `--provenance <path>` flag override, points
  `git-ops-finalize` at a JSON provenance map. When a map resolves,
  `GitOpsService.compose_finalize_pr_body` in `src/devbench/github/git_ops.py`
  composes the PR title, a per-epic summary section, and one closing-keyword
  line per mapped issue (`Fixes <org>/<repo>#<n>` cross-repo, `Fixes #<n>`
  same-repo, both rendered by the same code path); an unattended
  `auto_finalize` run posts this composed body with no operator step, but
  only the same-repo `Fixes #<n>` lines auto-close on merge, because GitHub's
  closing-keyword auto-close mechanism only fires for an issue in the SAME
  repository as the merging pull request -- the cross-repo `Fixes
  <org>/<repo>#<n>` lines create a cross-reference on the target issue for
  traceability but never change its state -- except when a PR is already open
  on the branch, per issue #129, in which case the open PR is reused as-is
  and the freshly-composed body is computed but never posted. A missing, unreadable, invalid, or issue-empty
  map fails loudly (exit 1, naming the path) BEFORE any push happens -- it
  never silently falls back to the plain body. `--provenance` beats the
  config key; both beat the plain-body default; there is no `DEVBENCH_*`
  environment override for the config key
  (YAML-only, like its sibling `single_branch` and `branch_prefix` settings).
  `src/devbench/cli.py`'s `git-ops-finalize` command now parses the optional
  flag and resolves the effective path before composing the body.
  `src/devbench/config_loader.py` and `src/devbench/config-schema.json` carry
  the new key; `sample-config.yaml` and `docs/devbench-yaml-reference.md`
  document it; `docs/cli-reference.md` documents the flag under the `## Gates`
  section alongside the other gate-related verbs.

- **`bootstrap-environment` gains a Step 0 every-invocation interview over the
  environment decisions it owns: LLM credential source, model selection, and
  GitHub credential source** (spec `integration-reality-gates-hardening.md`
  section 4.15, D-16, G12; AC-E2-F8-S1-T2-1 through -5).
  `plugin-authoring/devbench-authoring/skills/bootstrap-environment/SKILL.md`
  now opens with a Step 0 that interviews the operator, one menu per variable,
  before the pre-existing clone / asdf / `make validate` bootstrap steps (which
  are unchanged): `DEVBENCH_USE_BEDROCK` and `DEVBENCH_BEDROCK_REGION` (LLM
  credential source, the env-var form of the `use_bedrock` / `bedrock_region`
  keys `configure-devbench` already interviews), `DEVBENCH_CLAUDE_CREDENTIALS_FILE`
  (Anthropic OAuth credentials file path, no YAML equivalent),
  `DEVBENCH_CLAUDE_MODEL` (the orchestrate skill's own required coordination-call
  model, distinct from the per-agent `agents:` block), and the `GH_TOKEN` /
  `DEVBENCH_GH_TOKEN_FILE` GitHub token source and `DEVBENCH_GH_ORG` single-org
  restriction (both pure env vars with no YAML equivalent). Each menu carries a
  recommended value marked as such, every alternative, a free-form entry path,
  and a full explanation of the setting and the consequence of each choice,
  matching the `configure-devbench` interview-block format (AC-E2-F8-S1-T1-3).
  The interview runs in full on every invocation: the
  current session's already-exported value is shown as the current value, but
  every question is still asked again -- there is no "skip because unchanged"
  path. A new self-verify step confirms the chosen credential sources actually
  work (`aws sts get-caller-identity` for Bedrock, the credentials file for the
  Anthropic API, the GitHub token source, and that `DEVBENCH_CLAUDE_MODEL` is
  non-empty), retrying the failing check once before escalating with a
  diagnostic and suggested fix, mirroring the file's existing per-repo
  retry-once/escalate idiom.

  `docs/skills/bootstrap-environment.md` documents the every-invocation
  contract, the new Step 0 entry, and the expanded output contract and
  troubleshooting table; `docs/zero-to-ready.md`'s "Two setup paths" table,
  Step 4 (Authenticate Claude / Bedrock), and Cross-references section now
  point at the skill-driven interview equivalent and link
  `docs/skills/bootstrap-environment.md` / `docs/skills/configure-devbench.md`
  directly, keeping the manual walkthrough and the skill-driven path in sync
  (`docs/zero-to-ready.md` was deferred from the `configure-devbench` rewrite
  to this unit). `docs/onboarding.md` Step 4 (intro, "What happens" list, and
  the worked-example walkthrough) now describes the same every-invocation
  Step 0 interview and its retry-once/escalate self-verify, matching how
  E2-F8-S1-T1 updated Step 3 for `configure-devbench`. `SKILL.md`'s
  `DEVBENCH_BEDROCK_REGION` block now documents the full four-layer
  precedence (env var over the YAML `bedrock_region` key over `AWS_REGION`
  over the built-in `us-east-1` default), matching `src/devbench/config.py`,
  and its session-value read loop uses bash indirect expansion (`${!v}`)
  instead of `eval`.

  `tests/test_plugin/test_bootstrap_environment_interview.py` is the new
  structural pin: for each of the six owned variables it parses the
  `#### \`VAR\`` interview block and asserts the Recommended/Alternatives/
  Free-form markers and a current-value line are present, asserts the
  every-invocation contract is stated in both the SKILL and its doc, asserts
  `docs/skills/bootstrap-environment.md` carries an "## Every-invocation
  contract" section and opens its step-by-step list with the Step 0
  interview, and asserts `docs/onboarding.md` Step 4 states the
  every-invocation phrase and references the Step 0 interview both when
  introducing it and in the self-verify description -- proven by mutation
  (deleting Step 0 from the real SKILL.md now fails the pin). The block
  parser and marker-completeness helpers are shared with
  `configure-devbench`'s pin via the new
  `tests/fixtures/interview_block_helpers.py` module rather than duplicated.

- **`src/devbench/source_classification.py` -- the single source/test-path/
  entry-point classification module** (spec
  `integration-reality-gates-hardening.md` section 4.3, D-3, PM-3;
  AC-E2-F6-S1-T1-1 through -5). "Which file extensions are source, which
  paths are tests, which filenames are entry points" used to have two
  independent answers: the CLI's own reachability-evidence classification
  and the write-path audit helper's scan vocabulary. New
  `SOURCE_EXTENSIONS`, `ENTRY_POINT_STEMS`, `TEST_PATH_MARKERS` and
  `TEST_FILENAME_MARKERS` frozensets, plus `is_source_extension`,
  `is_entry_point_stem`, `is_test_path` and `classify_extension`, are the
  one place that answers the reachability-evidence question now. `cli.py`'s
  `_is_reachability_candidate` consumes `is_source_extension`,
  `is_entry_point_stem` and (via `_is_reachability_test_path`)
  `is_test_path`, matching its pre-migration behaviour, which already
  lowercased the suffix and already used `SOURCE_EXTENSIONS`'s full
  15-extension union; `_is_reachability_test_path` itself delegates only
  to `is_test_path`. This half of the migration is behaviour-preserving,
  and `cli.py`'s local extension tuples are deleted, not left dormant.

  `plugin_helpers/permission_flag_writepath.py`'s write-path audit
  historically scanned a narrower 9-extension set, so rather than widen
  that scan to `SOURCE_EXTENSIONS`, this module keeps the audit's own
  scan scope as a *second* named set in the same single home,
  `WRITE_PATH_AUDIT_SCAN_EXTENSIONS`, consumed via the new
  `is_write_path_audit_extension` predicate. `_iter_source_files` now
  calls that predicate instead of declaring its own tuple, which matches
  exact-case (does not lowercase the suffix), preserving the audit's
  pre-migration case-sensitive scan byte-for-byte. One definition site
  (this module, AC-2/AC-3), two named scopes: `SOURCE_EXTENSIONS` answers
  "is this extension source code" for the reachability consumer;
  `WRITE_PATH_AUDIT_SCAN_EXTENSIONS` answers the narrower, audit-specific
  "is this one of the 9 extensions the write-path audit has always
  scanned." Both migrations are therefore behaviour-preserving
  (AC-E2-F6-S1-T1-5); broadening or narrowing the audit's scan set is
  left to the gate epic that actually needs it (spec 4.8), not this
  extraction. The pre-existing witness tests in `tests/test_cli.py` and
  `tests/test_plugin_helpers/test_permission_flag_writepath.py` pass
  unchanged before and after, recorded via `green-green-check`, and new
  tests pin each consumer's continued dependence on the shared module's
  symbols so a future reversal of the migration is caught rather than
  silently accepted.
- **A review-rejection loop had no bound in code, so one task could be rejected
  and reworked indefinitely while every health signal read green.** Issue #122
  shipped `max_executor_retries_per_judge` with a config field, a runtime
  validator, a JSON-schema entry and reference docs, but no code consumer: the
  only enforcement was orchestrate SKILL.md prose instructing the orchestrator
  to read the budget via `devbench config-resolve`, **a verb that did not
  exist**. The budget was therefore unreadable at runtime and never applied.
  `cmd_log_verdict` wrote `[REVIEW_FAIL]` and returned 0 unconditionally -- no
  counting, no cap, no escalation -- so `[RETRY_BUDGET_EXHAUSTED]` was never
  emitted and `backlog.proposal`'s classifier never saw the tag it needs to
  raise `OPERATOR_ACTION_REQUIRED`. Observed on a live run: one docs task spent
  4 review rounds across 4 claim cycles over ~5 hours against a configured
  budget of 10, with zero work units completed in the final window and no error
  logged. Three changes close it: the missing `config-resolve` verb now exists
  and prints resolved config as JSON (unknown field exits non-zero rather than
  returning a silent `null`); `cmd_log_verdict` counts the failing judge's prior
  `[REVIEW_FAIL]` rows in the work unit -- the audit trail is the counter, so
  there is no new state to drift -- and on exhaustion writes the verbatim
  `[BLOCKED] [RETRY_BUDGET_EXHAUSTED]` row, forces the unit to `blocked`, and
  sends the operator notification, mirroring `_handle_ci_failure`'s existing
  escalation shape; and `devbench report` gains a `Review rejections` row
  showing rounds spent per judge against budget for every non-terminal task, so
  a stalling task is no longer indistinguishable from a progressing one. Only
  the five canonical reviewers charge a budget -- audit-only workflow agents own
  no review gate, a boundary that matters because `manifest_amender` logged 3
  `REVIEW_FAIL`s against the same task. `log-verdict`'s JSON now carries
  `retry_budget_exhausted` so the orchestrator can tell a bounded rejection from
  a terminal one. Enforcement and display share one
  `backlog.manager.resolve_judge_retry_budget`, so the budget shown can never
  disagree with the budget applied. Below budget, behaviour is unchanged.

- **The amendment pre-filter was dead code from the CLI, so a backlog's
  configured `allowed_reasons` narrowing was silently ignored.**
  `amendment.PreFilter` implements 7 deterministic checks but was referenced
  nowhere in `cli.py`: `cmd_request_amendment` claimed in its own docstring to
  "fail fast on unknown reasons" while calling none of them, and
  `apply_amendment` validated against the module-level
  `ALLOWED_AMENDMENT_REASONS` (what devbench implements) instead of the
  per-backlog `AmendmentConfig.allowed_reasons` (what the backlog permits) --
  a fail-open bypass in which the config was loaded, schema-validated, and then
  disregarded at every gate. `request-amendment` now runs the full pre-filter
  before writing, so a request that cannot be approved never reaches disk or
  occupies the single pending-request slot, and both gates enforce the
  configured set. The set can only be narrowed, never widened: a configured
  reason devbench does not implement stays refused.

- **A judge could mandate a Changes Manifest correction that was impossible to
  perform.** `AC-FINAL-015` requires the Manifest to match the files git changed
  exactly -- "no extra, no missing" -- so a declared row whose file ends up with
  a zero-line diff (its work having landed under a sibling unit) is a real
  violation, and `changes_manifest` correctly fails the unit and prescribes an
  amendment. But `AmendmentRequest` was add-only: no `files_to_remove`,
  `files_to_drop`, or equivalent field existed, so the prescribed remedy could
  not be carried out. Adds `files_to_remove` (optional, defaults to empty so
  existing request JSON still parses) and a `manifest.remove_rows` counterpart
  to `append_rows` that reuses the same section regex, body parser and renderer
  so content outside the Changes Manifest stays byte-identical. Removal is
  gated on a deterministic safety property: a row may only be dropped once its
  file has **no staged, unstaged, or untracked changes**
  (`manifest.list_changed_files`), because the row is the only thing authorising
  a file to appear in the unit's commit -- permitting removal for a file with
  real changes would let work leave the unit's reviewed scope, the violation
  `assert_staged_matches_manifest` exists to stop. Removals ride the same atomic
  write and rollback envelope as additions, so a Layer 3 post-check failure
  restores the Manifest whole; the post-check needed no change, and its existing
  source-test atomicity rule still catches a removal that orphans a pair.
  Removing every row, removing an undeclared path, and adding plus removing the
  same path are each refused. The `[AMENDMENT_APPLIED]` audit row now names
  removals, so a dropped row is never invisible to a reviewer.
  `manifest_amendment.max_requests_per_execution` default rises from 1 to 2:
  a unit correcting its Manifest in both directions needs two amendments, which
  a limit of 1 made impossible to satisfy.

- **Wiring a `[BLOCKED_PENDING_PROPOSAL]` marker never wrote the status, so the
  ADR-07 auto-requeue cascade silently skipped the task forever.**
  `proposal.promote_proposal` wrote the marker into Comments and the row into
  the Dependencies table, but nothing in the promote path -- or in the sibling
  `add-dep` operator path, which writes the byte-identical marker -- set
  `## Status:` to `blocked`. A task blocked pending a proposal therefore kept
  whatever status it had. Observed with a work unit left `in-progress` after the
  manifest-amender failed it mid-execution: the report showed two tasks
  in-progress when only one was running.

  The mismatch is load-bearing, not cosmetic. Three consumers read marker and
  status independently: `_auto_requeue_marker_dependents` skips any candidate
  whose status is not `blocked`, so the task would never be requeued when its
  promoted dependency completed -- stranding permanently with a *satisfied*
  dependency, the exact outcome the cascade exists to prevent;
  `cli._should_auto_restart_after_no_actionable` refuses to restart while any
  task is `in-progress`, so a target wired mid-execution suppresses auto-restart
  indefinitely; and `BacklogParser.find_next_actionable` *prioritises*
  `in-progress` over `in-queue`, so a claim sweep could re-claim the task while
  its blocker was unresolved and re-run work that was deliberately halted.
  `classify_blocked_task` keys off marker presence rather than status, so it
  reported the task as auto-clearing while the status line disagreed -- neither
  view cross-checked the other, which is why this survived until an operator
  read both.

  Both writers now set the status through `BacklogManager.force_status` (so the
  `[STATUS]` audit row and the `BACKLOG.md` index row are written too), ordered
  marker-then-status: a crash between them leaves a marker with a stale status,
  which `sync-blocked` reconciles, rather than a `blocked` status with no marker,
  which `classify_blocked_task` buckets as `OPERATOR_ACTION_REQUIRED` and only a
  human can clear. New validate-backlog rule 27 makes the invariant
  unviolatable by any future path: a non-terminal Task carrying a marker whose
  target is itself non-terminal MUST be `blocked`. Markers whose targets are all
  terminal are exempt, since the cascade has legitimately requeued the task --
  demanding `blocked` there would flag correct state.

  Same shape as issue #332 (rollup fired its audit comment but not its status
  write), one path over: marker-writing and status-writing were separate steps
  with no invariant tying them together. Rule 27 is that invariant.

- **The Bedrock backend could not run any current-generation model** (issue
  #342). `BEDROCK_AGENT_MODEL_PATTERN` required every id to end in `-v<N>`
  (`^us\.anthropic\.claude-[a-z0-9-]+-v[0-9]+$`), a convention AWS does not
  follow. Two whole shapes of real inference-profile id were rejected: current
  generations carry no version segment (`us.anthropic.claude-opus-5`,
  `...-sonnet-5`, `...-opus-4-8`, `...-opus-4-7`), and dated profiles end
  `-v1:0` whose `:0` failed the `$` anchor. Measured against
  `aws bedrock list-inference-profiles`: of 12 ACTIVE non-haiku
  `us.anthropic.claude*` profiles the pattern accepted **1**, so
  `use_bedrock: true` plus any current model failed at config load with a
  `ValueError` and nothing started -- pinning Bedrock operators to
  `us.anthropic.claude-opus-4-6-v1`. The rejection message's own example,
  `us.anthropic.claude-opus-4-7-v1`, is not a real profile id (the real one
  has no `-v1`), so it steered operators toward a value AWS rejects at
  invocation.

  The pattern is now `^us\.anthropic\.claude-[a-z0-9.:-]+$`, keeping what
  devbench actually depends on (the `us.` cross-region prefix, the
  `anthropic.claude` family, the separately-enforced haiku ban) and dropping
  the false version-suffix assumption; `.` and `:` are admitted so dated ids
  parse. The message now names a real id and points at
  `aws bedrock list-inference-profiles`. A validator cannot confirm a model is
  *enabled* in the caller's account -- that needs an API call config load must
  not make -- so the contract is deliberately "structurally a Bedrock Claude
  id", with genuine access errors surfacing at first invocation where AWS names
  the real failure. New tests parametrize over real profile ids captured from
  the live API rather than synthesising `f"us.anthropic.{id}-v1"`, which is how
  the over-strict pattern survived until a real Bedrock run.

- **The test suite was unrunnable in a shell configured for Bedrock** (issue
  #342). `tests/conftest.py` forces every other backend-affecting variable but
  left `DEVBENCH_USE_BEDROCK` / `DEVBENCH_BEDROCK_REGION` inherited. Since env
  beats yaml, an operator who had legitimately exported them for their real
  workspace saw `use_bedrock` silently flip for the whole suite: five
  `tests/test_config.py` cases asserting the Anthropic path failed with Bedrock
  complaints. Both are now popped alongside `DEVBENCH_SESSION_NAME`, so the
  fixture YAML stays the single source of truth and cases that exercise either
  backend set the variable explicitly.

- **Blocking a task destroyed every uncommitted change in the target repo**
  (issue #340). `cli._clean_target_repo_on_block` ran `git reset --hard HEAD`
  plus `git clean -fd` against the target checkout on every transition to
  `blocked`. The executor stages production changes and leaves committing to
  `devbench git-ops`, so a task that blocked after finishing its work had that
  work annihilated -- unconditionally, irreversibly, and with no operator
  confirmation. The tell was the orchestrator routing around its own tool: an
  observed run deliberately chose `hold` over `blocked` to keep a complete
  task's verified work alive. Block-time cleanup now delegates to
  `git_quarantine.quarantine_paths`, the same non-destructive primitive
  claim-time quarantine (`_prepare_worktree_for_claim`) already used, so the
  shared checkout is still cleared for the next claim but the residue lands in
  recoverable `git stash` entries, one per owning unit, discoverable via
  `git stash list` and recorded with the new
  `[BLOCK_QUARANTINE] <unit-id> owner=<id> paths=<n> stash=<message>` audit
  line. The two "clear this shared checkout" paths now share one
  implementation instead of disagreeing about whether the work survives.

- **The RED gate rejected an out-of-band-committed production change by
  reporting only the symptom** (issue #341). When every production-source row
  is already committed, `git stash push -u -- <rows>` removes nothing, the
  named test necessarily passes, and `tdd_gate.observe_red` rejected with
  `named test outcome was PASSED` -- true but describing a consequence, so the
  operator had to reverse-engineer the cause. An observed run stranded a
  complete work unit exactly this way after an operator commit snapshotted its
  in-flight production file. The rejection now names the cause when nothing was
  stashed: that no production change was removed, that this normally means the
  rows were committed out of band, and how to re-derive an observable RED.
  Deliberately diagnostic-only -- the pass/fail decision is unchanged and no
  reconstruction is attempted. "Nothing to stash" remains legitimate on its own
  (test-first TDD, where a pinning test committed alongside still-broken
  production source genuinely fails; `TestJourneyJ8HonestBehaviorFix` pins
  that path), so the run still happens and the outcome still decides.

- **The orchestrator exited permanently, and reported a clean exit, when the
  model ended its own turn with the backlog unfinished** (issue #339). The
  orchestrate loop is designed to stop on exactly three conditions --
  `ALL_DONE`, `NO_ACTIONABLE`, or an operator drain -- but a fourth path
  ended it silently. Observed 2026-08-14T14:44 with 105 of 138 work units
  outstanding: the orchestrator attempted `git add` itself (out of scope; a
  guard hook correctly denied it), then ended its turn claiming "the executor
  agent is running in the background ... I've scheduled a fallback check-in
  ... I'll continue the orchestrate loop automatically". devbench has no
  background execution, no completion callback, and no scheduler; nothing
  resumed it and the daemon exited rc=0.

  Three defects, fixed together:

  1. `cmd_start._run` treated the SDK generator's `StopAsyncIteration` as a
     normal exit (a bare `break`), leaving the fastest-firing failure mode as
     the only one with no recovery, while a model going *silent* for the
     inactivity window -- a slower form of the same failure -- already earned
     a bounded fresh-session restart. It now raises the new
     `_OrchestratePrematureTurnEnd` sentinel (a `BaseException`, matching its
     quota / inactivity / transport siblings) carrying the model's last result
     text, and `_drive_orchestrate_with_quota_resume` disposes of it as a
     bounded restart on the remaining backlog. A genuine end-of-run never
     reaches this path: the loop returns as soon as a terminal sentinel is
     observed. The restart is bounded by its own
     `DEVBENCH_MAX_PREMATURE_TURN_END_RESTARTS` cap (new
     `DEFAULT_MAX_PREMATURE_TURN_END_RESTARTS`, default 10) rather than the
     shared 1000-resume ceiling: quota, inactivity, and transport faults each
     self-throttle, whereas an immediate turn end can repeat immediately, so
     sharing that ceiling would let one reproducible prompt-following failure
     burn a thousand consecutive sessions unattended.
  2. `_resolve_clean_stop_reason` promoted ANY non-empty
     `ResultMessage.result` text to `clean exit: <text>`, so the model's own
     narration was reported to the operator as a clean exit, indistinguishable
     from a finished backlog. This truthy check predates the
     premature-turn-end bucket (added later, by db-271) and made that bucket
     unreachable whenever the model said anything at all before stopping.
     Rule 1 now requires the text to actually carry a terminal sentinel
     (`_is_terminal_orchestrate_result`, the same helper the SDK loop uses),
     and the premature label retains the non-terminal text verbatim so the
     operator sees what the model claimed instead of losing the only
     diagnostic. Issue #217's intent -- surfacing
     `NO_ACTIONABLE -- 190/212 done, 11 blocked` in the ping -- is preserved
     unchanged for genuine end-of-run text.
  3. The orchestrate SKILL gained two rules: Agent tool calls are synchronous
     and no background task, completion notification, callback, scheduler,
     wakeup timer, or fallback check-in exists, so ending a turn on any such
     claim is a fabrication that kills the run; and the orchestrator never
     issues state-changing git commands (staging belongs to the executor,
     committing to `devbench git-ops`), with read-only inspection still
     permitted and a guard denial explicitly not a reason to end a turn.

- **`log-waiver` -- structured, judge-visible `[GATE_WAIVER]` waivers, a
  validate-backlog grammar rule, and the report waiver count** (spec
  `integration-reality-gates-hardening.md` section 4.9, 5.3, G7, D-5/PM-5;
  AC-E2-F4-S1-T1-1 through -7). New CLI verb `log-waiver <judge> <id> --gate
  <g> --target <t> --reason <r> [--operator]` writes a `[GATE_WAIVER <gate>]
  <iso-utc> <target> <operator|executor> <reason>` marker
  (`devbench.backlog.manager.compose_gate_waiver_record`, the sole authorized
  builder) into the unit's `## TDD Cycle Log` section -- the audit surface
  that survives every review judge's `read-unit --strip-comments` Evidence
  fetch (the PM-6 evidence-horizon rule, E2-F3-S1-T2), unlike `## Comments`
  which that fetch strips. Trust model enforced at the CLI boundary (spec
  Section 3.6): `<judge>` must be one of the five canonical review judges
  (`constants.ALL_REQUIRED_JUDGE_NAMES`), `--gate` one of the eight declared
  gates, `--reason` mandatory and validated by the existing
  `_validate_agent_free_text` em-dash / control-character / bracketed-tag
  boundary check, and a `machine-blocking` gate requires `--operator` --
  every usage failure exits 2 naming the offending argument; a missing unit
  exits 1. `validate-backlog` gains Check 27, rejecting a malformed
  `[GATE_WAIVER]` line via the same grammar authority
  (`parse_gate_waiver_record`) the marker is built from, naming the unit and
  the offending line. `devbench report`'s Backlog state table gains a "Gate
  waivers (operator / executor)" row (`count_gate_waiver_markers`), so an
  operator sees at a glance how much of the run is riding on waivers.
  `docs/cli-reference.md` documents the verb under `## Gates`, pinned by
  `tests/test_docs/test_cli_reference_log_waiver.py`.

- **`log-newly-reachable` -- structured, judge-visible `[NEWLY_REACHABLE]`
  path-verification markers** (spec `integration-reality-gates-hardening.md`
  section 4.9(a), 5.3, S1; AC-E2-F4-S1-T2-1 through -6; AC-21). New CLI verb
  `log-newly-reachable <id> --path <p> --method <m> --result <r>` writes a
  `[NEWLY_REACHABLE] <path> <method> <result>` marker
  (`devbench.cli.compose_newly_reachable_record`, the sole authorized
  builder) into the unit's `## TDD Cycle Log` section via the same
  `BacklogManager._append_audit_marker_before_comments` insertion point
  `log-waiver` uses -- the audit surface that survives every review judge's
  `read-unit --strip-comments` Evidence fetch (the PM-6 evidence-horizon
  rule, E2-F3-S1-T2). This replaces the free-text `[NEWLY_REACHABLE]`
  convention `docs/newly-reachable-paths.md` previously documented (written
  via `log-comment` into `## Comments`, which that fetch strips and which was
  therefore invisible to the judges spec 4.3 requires to weigh it); a
  Definition-of-Done checkbox is auto-ticked on the done transition (S1), so
  only a validated, judge-visible marker is auditable. `--method` (`manual`,
  `unit_test`, `integration_test`, `functional_test`) and `--result`
  (`verified`, `broken`) are validated against named importable
  `NEWLY_REACHABLE_METHODS`/`NEWLY_REACHABLE_RESULTS` constants rather than
  inline literals; an unknown or empty field exits 2 naming the offending
  argument and listing the accepted values, and a missing unit exits 1
  writing no marker -- the same 0/1/2 exit-code contract `log-waiver` uses.
  Flag scanning reuses the newly-generalised `_consume_gate_verb_flag_value`
  (renamed from `_consume_log_waiver_flag_value`) rather than duplicating
  flag-parsing between the two structured gate-marker verbs.
  `docs/cli-reference.md` documents the verb under `## Gates`, pinned by
  `tests/test_docs/test_cli_reference_log_newly_reachable.py`.
  `docs/newly-reachable-paths.md`'s audit-trail and enforcement sections
  still describe the superseded `log-comment`/`## Comments` convention;
  syncing that doc to the shipped verb is deferred to the named successor
  task E2-F4-S1-T4.

- **`make generate-vocabulary` -- generated vocabulary docs table and judge
  prompt sentences replace three hand-maintained copies with one** (spec
  `integration-reality-gates-hardening.md` section 4.10, 5.7, G5, D-4,
  Section 0.4; AC-E2-F5-S1-T1-1 through -6, AC-11 idempotence half). New
  module `devbench.vocabulary_generation` (run via `make generate-vocabulary`
  / `python -m devbench.vocabulary_generation`) renders the per-judge tables
  in `docs/review-feedback-vocabulary.md` and the per-judge vocabulary
  sentence in the five judge prompts (`review_team/code-reviewer.md`,
  `review_team/test-reviewer.md`, `review_team/doc-reviewer.md`,
  `review_team/changes-manifest.md`, `security-reviewer.md`) from
  `JUDGE_CATEGORIES` (`devbench.backlog.review_feedback_vocabulary`),
  writing only between `<!-- generated:vocabulary -->` /
  `<!-- /generated:vocabulary -->` guard markers so hand-written prose
  outside them is preserved byte for byte. Generation is idempotent (a
  second consecutive run produces zero diff); a target surface missing its
  guard markers, or carrying an unterminated pair, raises loudly naming the
  file (and, for an unterminated pair, the opening marker's line number)
  rather than being silently skipped. Behaviour change for operators:
  hand-edits inside a generated block are now overwritten by the next
  `make generate-vocabulary` run (Section 0.4); the `manifest_amender` table
  remains hand-maintained (different source of truth,
  `AMENDER_REJECTION_CATEGORIES`). The drift check that fails
  `make validate` on an un-regenerated surface is implemented as
  `make check-vocabulary-drift`, described in the following entry.

- **`make check-vocabulary-drift` -- `make validate` now fails on a
  hand-edited generated vocabulary block** (spec
  `integration-reality-gates-hardening.md` section 4.10, AC-11;
  AC-E2-F5-S1-T2-1 through -3, AC-E2-F5-S1-T3-1 through -7). The drift-check
  logic lives in `src/devbench/vocabulary_generation.py`, not the `Makefile`:
  `all_generated_relative_paths()` is the single enumeration of every
  guard-marked surface (the doc-surface constant plus the prompt-target
  mapping keys -- no second, hand-maintained copy of that list exists
  anywhere in production code; a literal fixture copy in
  `tests/test_vocabulary_generation.py` is deliberately pinned to these same
  constants by an equality assertion, so it cannot silently drift);
  `find_drifted_surfaces()` regenerates each surface into a scratch
  directory (never writing to the working tree it inspects) and diffs it
  against the six committed surfaces; `main` in check mode
  (`python -m devbench.vocabulary_generation --check`) reports the result,
  naming the offending file(s) and `make generate-vocabulary` as the fix.
  `check-vocabulary-drift` is now a single-line `Makefile` delegation to that
  check mode, carrying no copy of the surface list, and remains a `validate`
  prerequisite, so the pre-push hook catches a hand-edit before it is
  pushed; CI does not invoke `make validate` directly, so it catches the
  same hand-edit only indirectly, through `TestVocabularyDriftCheck` running
  inside `make test-coverage`.

- **Closed finding 322-D21: every `JUDGE_CATEGORIES` code is now accounted
  for by the `_LEGACY_CODES`/`_CAMPAIGN_CODES` partition, every campaign
  code carries an ownership plus non-membership assertion, `_LEGACY_CODES`
  is frozen by `TestLegacyCodesAreFrozen` so a new code cannot be parked
  there to dodge the gate, and `make validate` is proven to reach the
  module that enforces all three** (spec
  `integration-reality-gates-hardening.md` section 4.10;
  AC-E2-F5-S1-T2-4 through -6). The prior coverage was uneven, not
  uniformly absent: PR #322 shipped `FIXTURE_CATALOG_MISMATCH` proving only
  that it belonged to `test_review`, never that it was absent from every
  other judge's set, and `UNREACHABLE_ARTIFACT` and
  `NEWLY_REACHABLE_PATH_UNVERIFIED` shipped with that same one-sided gap.
  `LAYOUT_STUB_WITHOUT_LIVE_TEST` already had partial negative coverage
  (absence from `code_review` only, not from every other judge), and
  `COMPOSITION_ROOT_MISSING` already had full negative coverage via
  `test_composition_root_missing_not_valid_for_other_judges`, which looped
  over every judge but `test_review`. `tests/test_backlog/test_review_feedback_vocabulary.py`
  now partitions every code in `JUDGE_CATEGORIES` into `_LEGACY_CODES`
  (closed permanently, and pinned against an independently maintained
  `_LEGACY_CODES_SNAPSHOT` literal by the new `TestLegacyCodesAreFrozen`)
  and `_CAMPAIGN_CODES` (grows by one entry per new code, each proven by
  `TestCampaignCodeMembership`). Its positive half is parametrized off a
  literal `_CAMPAIGN_CODE_OWNERS` code-to-judge table, cross-checked against
  the published mapping in `docs/review-feedback-vocabulary.md`'s
  `code_review` and `test_review` tables (each code is documented once,
  under its owning judge's heading) and, for `FIXTURE_CATALOG_MISMATCH`
  specifically, `docs/cli-reference.md` line 1355, so reassigning a
  campaign code to a different judge's frozenset fails the test directly --
  deriving the "owning judge" from `JUDGE_CATEGORIES` itself, as an earlier
  revision of this test did, cannot detect that reassignment because the
  derivation and the assertion move together. `_owner`'s existing
  single-owner precondition is kept and cross-checked against the same
  literal table by `test_owner_matches_literal_table`. The negative half
  remains the non-membership cross-product against every other judge in
  `JUDGE_CATEGORIES`, parametrized so a judge added later cannot be silently
  skipped. The seven pre-existing ad-hoc single-code tests for these five
  codes are superseded: each pinned a literal judge mapping for one code
  with no negative coverage for three of them and partial negative coverage
  for a fourth (the historical detail above); `TestCampaignCodeMembership`
  now pins that same literal mapping for all five plus the full
  non-membership cross-product for all five uniformly.
  Together, `TestJudgeCategoryMembershipCoverage.test_every_code_is_accounted_for`
  (fails, naming the code, whenever a code lands in neither set) and
  `TestLegacyCodesAreFrozen.test_legacy_codes_matches_frozen_snapshot`
  (fails, naming the divergence, whenever `_LEGACY_CODES` is extended
  instead of `_CAMPAIGN_CODES`) mean a new code cannot ship without
  triggering `TestCampaignCodeMembership`'s ownership and non-membership
  assertion. `tests/test_integration/test_make_targets.py` adds
  `TestMembershipCoverageGateReachableFromValidate`, the direct analogue of
  `TestVocabularyDriftCheck.test_validate_runs_the_drift_check` above, with
  two assertions: `test_validate_pytest_invocation_collects_the_membership_module`
  extracts `test-coverage`'s real pytest invocation from `make -n` and runs
  it in `--collect-only` mode to prove the invocation actually collects the
  membership-coverage module, so a future marker filter or path list that
  narrows the invocation fails loudly instead of silently dropping this
  gate out of `test-coverage`; `test_test_coverage_is_a_validate_prerequisite`
  separately asserts that same invocation line appears in `make -n
  validate`'s dry-run output, so removing `test-coverage` from the
  `validate` prerequisite list would also fail loudly rather than leaving
  the first assertion green in isolation. `WRITE_PATH_UNVERIFIED`, added
  later by E7, is deliberately not asserted here: the completeness test is
  what obliges that unit to bring its own membership assertion in the same
  change that adds the code.

- **`src/devbench/work_unit_scope.py` -- the single ADR-12 mode-aware scope
  helper** (spec `integration-reality-gates-hardening.md` section 4.3, PM-6,
  AC-9; AC-E2-F3-S1-T1-1 through -6). New `resolve_changed_files(unit_id,
  repo_path, mode) -> ScopeResult` extracts the mode-aware scope-resolution
  logic that used to live inline in `cmd_get_diff` -- which files are in
  scope (the unit's real Changes Manifest paths), which ADR-12 mode applies,
  this unit's own commit sha(s) in `defer_pr` mode (resolved by
  commit-message subject, never `HEAD`), and the spec-4.2 `[GATE_PASS]` scope
  hash (`devbench.gate_records.compute_scope_hash`, so the value later
  gate-record freshness checks recompute can never drift from a second hash
  definition). `devbench get-diff` and `devbench check-manifest-scope` are
  migrated to call it in this same change, with their prior inline scope
  code (`_load_manifest_paths_or_report`, `_render_task_commit_hunks`)
  deleted, not left dormant. Error semantics: an unknown unit id or an
  invalid/non-work-tree repo path raise `ValueError` naming the offending
  value and (for the repo path) the config key to fix; git plumbing exiting
  >= 2 raises `RuntimeError` with stderr attached; the function never
  returns a partial `ScopeResult`. A grep-shaped pin
  (`TestScopeSingleImplementationPin` in `tests/test_cli.py`) asserts no
  other module reintroduces the commit-sha-by-subject resolution
  independently.

- **`tests/test_docs/test_gate_tier_vocabulary.py` -- the G3 blocking-vocabulary
  truthfulness pin, plus the disabled-status-line prose sweep** (spec
  `integration-reality-gates-hardening.md` section 4.2, G3, Section 0.2;
  AC-E2-F2-S2-T1-1 through -6, AC-8). New docs test walks
  `plugin/devbench-orchestrate/agents/` (the review-team prompts and the
  executor prompt), `plugin/devbench-orchestrate/skills/`, and `docs/` for
  blocking-vocabulary phrases (`blocks`/`blocking`/`blocked`,
  `enforces`/`enforced`/`enforcing`, `cannot be marked done` -- a named
  `BLOCKING_VOCABULARY_PATTERNS` constant, not an inline literal) that
  co-occur, on the same line, with one of the eight declared gate names. A
  violation is reported only when that gate's tier -- looked up directly
  from `constants.GATE_TIERS`, never a second hand-maintained list -- is not
  `machine-blocking`, so a machine-blocking gate may truthfully use blocking
  vocabulary (AC-E2-F2-S2-T1-3). The shared scanner
  (`scan_for_blocking_vocabulary_violations`) is exercised by a
  seeded-violation test, a machine-blocking-acceptance test, and a
  shipped-tree regression test that asserts zero violations across the real
  surface; a fourth test pins that every judge-evidence gate rubric in the
  three swept prompts states the Section 0.2 disabled-status-line semantics.
  `code-reviewer.md`'s BUG-FIX COMPLETENESS rubric, `test-reviewer.md`'s
  COMPOSITION-ROOT and LAYOUT rubrics, and `executor.md`'s BUG-FIX
  COMPLETENESS section each gain a sentence naming the judge-evidence gate
  they cover (`newly_reachable_paths`, `composition_root`,
  `layout_geometry`) and stating that a `{"gate":"<name>","status":"disabled"}`
  line means the gate is not configured -- neither a pass nor a fail signal,
  never a finding on its own.

- **Gate tier taxonomy and the `[GATE_PASS]` record module** (spec
  `integration-reality-gates-hardening.md` section 4.2, 5.3, D-6;
  AC-E2-F2-S1-T1-1 through -6). `constants.py` gains `GATE_TIER_MACHINE_BLOCKING`,
  `GATE_TIER_JUDGE_EVIDENCE`, `GATE_TIER_ADVISORY` and `GATE_TIERS: Mapping[str, str]`,
  declaring the tier of all eight gates (reachability, ancestry,
  shared_file_impact, fixture_consistency = machine-blocking; write_path_audit,
  newly_reachable_paths, composition_root, layout_geometry = judge-evidence),
  built as a dict comprehension over the existing `GATE_NAMES` tuple so the
  two collections can never drift. New module `gate_records.py` is the sole
  authority for the `[GATE_PASS <gate>] <iso-utc> <scope-hash>` marker
  grammar (Section 3.6: executors do not self-certify gate outcomes):
  `compose_gate_pass_record` builds the one-line marker from an
  already-resolved scope hash (rejecting undeclared gates, malformed scope
  hashes and naive timestamps); `parse_gate_pass_record` re-validates a
  marker on read, never returning a partial record on malformed input;
  `latest_gate_pass_record` locates the most recent record for a gate within
  arbitrary content (tolerating the marker being embedded inside a larger
  audit-comment line, since the grammar is additive to the audit-comment
  contract); `compute_scope_hash` is the SHA-256-over-sorted-file-list-plus-
  blob-hashes function backing the stale-record invalidation rule (AC-7),
  rejecting an empty scope. Mirroring `devbench.tdd_gate`, the module
  performs no work-unit-file or git I/O of its own -- gate commands persist
  the composed marker via the existing audit-append machinery, wired by the
  gate-specific tasks that consume this module (E2-F2-S1-T2 onward).

- **`devbench gates` -- read-only overview of every integration-reality
  gate's status, repo overrides and provenance** (spec
  `integration-reality-gates-hardening.md` G2, section 4.1; AC-4, AC-27).
  New zero-argument CLI verb registered in `_COMMANDS`
  (`"Show every gate's tier, status and repo overrides"`, matching the spec
  Section 14 `--help` snapshot) renders one row per declared gate
  (`constants.GATE_NAMES`), resolving each row exclusively through
  `config_loader.resolve_gate_config` -- never reading
  `RuntimeConfig.gates` fields directly (AC-27) -- so the table can never
  diverge from the four-layer precedence resolver. A fresh workspace with
  no `gates:` key renders all eight rows as `disabled` with the `-`
  no-override placeholder (D-17); a per-repo override or the
  `DEVBENCH_GATE_<NAME>_ENABLED` env var is reflected in both the `status`
  and `provenance` columns. Column widths are computed from the row data
  (`_format_gates_table`), not hard-coded, so the tier column a later unit
  adds needs no re-layout. Reloads `devbench.yaml` fresh from disk so a
  config load failure (missing file, invalid YAML/schema) is caught with
  the loader's own fail-fast message on stderr and exit 1, with no partial
  table printed (spec Section 7). Documented in `docs/cli-reference.md`'s
  new `## Gates` section, pinned by
  `tests/test_docs/test_cli_reference_gates.py` against the `_COMMANDS`
  description string so the doc can never drift from the registry.

- **`mark-done` wired to the machine-blocking gate-record invariant, and the
  `gates` table's `tier` column** (spec `integration-reality-gates-hardening.md`
  section 4.2, G2, G4; AC-E2-F2-S1-T2-1 through -6). `BacklogManager.mark_done`
  gains `_check_gate_pass_done_invariant`, mirroring the existing
  `_check_task_type_done_invariant` pattern so every caller (`cmd_mark_done`
  and `_check_merge_handle_merged` alike) inherits it identically: for each
  of the four machine-blocking gates (`constants.GATE_TIERS`) that resolves
  `enabled` for the unit's repo (`config_loader.resolve_gate_config`), the
  unit must carry a fresh `[GATE_PASS <gate>]` record or an
  operator-attributed `[GATE_WAIVER <gate>]` marker; an executor-attributed
  waiver is rejected as insufficient for a machine-blocking gate (spec
  Section 3.6), naming the missing operator attribution. Absent both, `mark-
  done` exits 1, writes no status, and names the exact remediation command,
  matching the spec G4 worked example verbatim in shape (`ERROR: done-gate:
  gate '<name>' is enabled for repo '<repo>' but has no [GATE_PASS <name>]
  record for <unit>. Run: uv run devbench check-<name> <unit>`). A
  `[GATE_PASS <gate>]` record's `scope_hash` is recomputed from the unit's
  current `## Changes Manifest` file list (`gate_records.compute_scope_hash`
  over each file's live `git hash-object` blob hash), so an edit to any
  in-scope file after the gate ran invalidates the record, refused with
  `ERROR: gate '<name>' record is stale (scope changed since it ran)`
  (AC-7). A disabled gate imposes nothing, preserving today's behaviour for
  every workspace that has not opted in. Separately, `devbench gates`
  (`_format_gates_table`) now renders the `tier` column (`machine-blocking`
  / `judge-evidence`, looked up from `constants.GATE_TIERS` by gate name)
  that E2-F1-S2-T1 deliberately deferred, completing the spec G2
  worked-example table shape. `docs/cli-reference.md`'s `## Gates` and
  `mark-done` sections document both changes with the recomputed example
  table and the G4 worked example.

- **`resolve_gate_config` -- the single four-layer precedence resolver for
  gate configuration** (spec `integration-reality-gates-hardening.md`
  section 4.1, D-15, D-17; AC-27). Adds `resolve_gate_config(gate, repo,
  runtime_config, env_enabled_override=None) -> ResolvedGateConfig` to
  `config_loader.py`: merges built-in defaults, project-level `gates.<gate>.*`,
  per-repo `gates.repos.<org/repo>.<gate>.*` overrides, and the
  `DEVBENCH_GATE_<NAME>_ENABLED` env layer field-wise, in that ascending
  precedence order, recording per-field provenance (`builtin` / `project` /
  `repo` / `env`) so a repo that flips `enabled` inherits every other
  project-level tunable instead of resetting it. Every built-in default
  (gate names, per-gate field defaults, the env-var prefix/suffix, and the
  provenance labels) is a named constant in `constants.py` -- no literal
  defaults inline in the resolver -- and the `GatesConfig` dataclass tree's
  own field defaults (`GateEnabledConfig`, `GateSharedFileImpactConfig`,
  `FixtureConsistencyConfig`) now reference the same constants instead of
  duplicated literals. Adds `config.resolve_gate_env_override(gate)`,
  deriving the env var name and resolving it through the existing
  `_resolve_bool` chain so the accepted boolean vocabulary and failure
  semantics never diverge from every other env-driven boolean in that
  module. Pins that `resolve_gate_config` is the ONLY module that reads a
  gate's resolver-managed fields (`enabled`, `auto_derive_registry`,
  `extract_source_literals`) directly off the raw config tree, so a later
  gate epic cannot quietly re-introduce a second, divergent interpretation.

- **Unified `gates:` config section for the eight integration-reality gates**
  (spec `integration-reality-gates-hardening.md` section 4.1;
  `caylent-solutions/devbench-internal-backlog#10`..`#17`). Replaces the
  ad-hoc per-PR opt-in surfaces the eight cherry-picked PRs shipped with ONE
  top-level `gates:` block covering `reachability`, `ancestry`,
  `shared_file_impact`, `fixture_consistency`, `write_path_audit`,
  `newly_reachable_paths`, `composition_root`, and `layout_geometry`, plus
  an optional `gates.repos.<org/repo>` per-repo override map. Adds the
  frozen `GatesConfig` dataclass tree and `RuntimeConfig.gates` field in
  `config_loader.py`, `_parse_gates_config` with fail-fast `ValueError` on
  an unknown gate name, a wrong-typed value, or a per-repo override naming
  a repo absent from `repos:`, and a matching JSON Schema block with
  `additionalProperties: false` at every level so a typo is a load-time
  error rather than a silently ignored key. Every gate is disabled by
  default (absent `gates:` behaves exactly as before this change); the
  four-layer precedence resolver (`resolve_gate_config`, adding per-repo
  and `DEVBENCH_GATE_<NAME>_ENABLED` env-override resolution) ships in a
  follow-up task. **Migration (complete replacement):** the pre-release
  keys that arrived on the branch ahead of any release -- a per-repo
  glob-pattern key nested under `repos:` and a bare top-level fixture-catalog
  opt-in block -- are REMOVED in this same change, with every consumer
  (`cli.py`'s `check-shared-file-impact` / `check-fixture-consistency`
  commands, `fixture_consistency.py`'s operator-facing messages, and the
  CLI/doc/plugin references below) updated to the new `gates.*` key paths
  and zero remaining references to either retired spelling.

- **Layout/CSS-geometry AC tagging and live-render verification gate**
  (`caylent-solutions/devbench-internal-backlog#14`). Standard jsdom-style
  unit-test environments have no real layout, paint, or cascade engine, so
  CSS-dependent runtime behaviour (sticky positioning, flex-shrink
  collapse, media-query cascade, grid autosize, overlap) has been shipping
  as "done" on jsdom-only tests -- including tests that stub the very
  primitive (`offsetHeight`, `getBoundingClientRect`, `ResizeObserver`)
  responsible for the bug, which structurally cannot fail even when the
  live defect persists. Adds a `spec-to-backlog` Step 3a keyword heuristic
  that tags layout/geometry-sensitive acceptance criteria `[LAYOUT-AC]`
  and requires a real-render/live-browser Definition of Done item for
  tagged tasks, a `test-reviewer` rubric item that flags a DOM-layout
  primitive stub for a `[LAYOUT-AC]`-tagged AC with no companion
  live-render test, and the new controlled rejection code
  `test_review:LAYOUT_STUB_WITHOUT_LIVE_TEST`. The vocabulary membership
  test (`tests/test_backlog/test_review_feedback_vocabulary.py`) was
  authored ahead of this pick because the source PR shipped zero tests.

- **Composition-root test verification for state-consuming UI tasks**
  (`caylent-solutions/devbench-internal-backlog#11`). Backlog tasks were
  repeatedly marked done with large green test suites that only ever
  rendered a component in isolation (hand-supplied props, a locally-built
  store, or a module-scope-mocked dependency) and never exercised the
  app's real composition root, letting components ship that were never
  wired into the running app, wired in wrong, or tested against a store
  shape that had silently diverged from production. Adds rubric items to
  `test-reviewer` requiring at least one test through the real
  composition root (or a documented smallest-real-ancestor exception) for
  any task touching a UI component that consumes shared/app-level state,
  a new controlled rejection code `test_review:COMPOSITION_ROOT_MISSING`,
  a `spec-to-backlog` Definition-of-Done requirement for tasks in that
  category, and `docs/composition-root-testing.md` defining the
  composition root, its scope, and acceptable exceptions.

- **Fixture-catalog cross-reference check**
  (`caylent-solutions/devbench-internal-backlog#17`). A feature's data-fetch
  logic can be correct while reading from a mock/fixture lookup table whose
  keys were fabricated, keyed in the wrong namespace, or left incomplete
  relative to the project's canonical shared fixture/demo dataset --
  functionally dead or crash-on-save for real records even though the
  underlying logic is sound, and invisible to the unit suite since each
  task's own fixtures are self-consistent. Adds an opt-in `gates.fixture_consistency:`
  block to `devbench.yaml` (`canonical_sources` designating authoritative
  fixture/dataset files and identifier fields, `scan` targets to
  cross-reference, and per-target `allow_missing` scoping for intentional
  edge-case fixtures) and `devbench check-fixture-consistency <id>`, a
  deliberate no-op unless the workspace configures `canonical_sources`. The
  check runs as `test-reviewer` review evidence and fails with
  `FIXTURE_CATALOG_MISMATCH` when a scanned fixture references a key absent
  from its canonical source, or a canonical source falls short of a
  declared `expected_count`. (E2-F1-S1-T1 re-nested this block under the
  unified `gates:` config section; see the "Unified `gates:` config section"
  entry below. The per-target `allow_missing` scoping described here was a
  workspace-config allowlist; E6-F1-S1-T2 superseded it with a structured
  in-fixture marker and removed the config key entirely -- see "The
  `allow_missing` fixture-catalog waiver moves into the fixture artifact"
  entry above.)

- **Shared-file full-suite regression gate**
  (`caylent-solutions/devbench-internal-backlog#13`). A task's regression
  verification is scoped to its own Changes Manifest even when the diff
  touches a shared/high-fan-in file (an app shell, a shared hook, a
  widely-consumed component) that many unrelated features depend on;
  previously such regressions surfaced only when an unrelated later task
  happened to run the full suite. Adds `gates.repos.<repo>.shared_file_impact.patterns`
  (a hand-maintained per-repo glob registry of shared composition-root
  files) to `devbench.yaml`, and `devbench check-shared-file-impact <id>`,
  a no-op unless the task's diff matches a registered pattern. On a match
  it runs the full suite, parses per-test failure identifiers, and diffs
  them against a stored baseline at `.devbench/test-baselines/<repo>.json`,
  blocking (exit 1) only on newly-introduced failures, bootstrapping on
  first run, and ratcheting the baseline down on a clean pass. A new
  `assert-shared-file-impact.sh` `PostToolUse` guard hook (mirroring
  `assert-tests-pass.sh`) makes the exit code load-bearing instead of
  advisory, and `executor.md`'s Definition of Done now requires running
  the gate before logging completion. (E5-F1-S1-T1 replaced the
  bootstrap-and-ratchet baseline below with a pre-change, per-branch-point
  baseline; see the "Shared-file baseline is now a pre-change,
  per-branch-point snapshot" entry below. E5-F2-S1-T1 replaced the
  `list_changed_files` working-tree scan below with the shared ADR-12 scope
  helper and rewrote the guard hook to fail closed; see the
  "`check-shared-file-impact` resolves its changed-file set through the
  shared ADR-12 scope helper..." entry below.)

- **Shared-file baseline is now a pre-change, per-branch-point snapshot,
  written atomically under a sibling `flock`, read under a shared `flock`,
  captured with fail-fast worktree cleanup, and a corrupt/mismatched/
  degraded baseline is a loud error instead of a silent re-bootstrap**
  (spec `integration-reality-gates-hardening.md` sections 3, 4.6 and 5.4;
  issue #13 AC2; source PR #318 findings 318-D2 and 318-D3). The previous
  post-change ratchet model wrote the baseline from a run of the
  already-changed tree, so a regression a task introduced itself was
  captured as "pre-existing" on the very first run and the gate passed
  forever after; a missing or corrupt baseline silently re-bootstrapped
  the same way, making baseline corruption an effective way to disable the
  gate unnoticed, and two racing gate runs on the same repo could lose one
  another's writes. `check-shared-file-impact` now resolves the work
  unit's branch point via `git merge-base HEAD origin/<default-branch>`
  and stores the baseline at
  `.devbench/test-baselines/<repo>/<branch-point-sha>.json` (one file per
  branch point, never overwritten by a later task diverging from a
  different commit) with the exact fields `schema_version`, `captured_at`,
  `branch_point`, `runner` and `failing`. When no baseline exists yet for
  that branch point it is captured by running the full suite in an
  isolated `git worktree` checked out AT the branch point -- never from
  the caller's own working tree -- so the baseline is always a true
  pre-change snapshot; a failure the unit's own diff introduces is no
  longer indistinguishable from one that predates the branch. Branch-point
  resolution and baseline validation now run BEFORE the (expensive) current-
  tree suite, so a corrupt or mismatched stored baseline never costs a
  full-suite run first; a degraded (unattributed) branch-point capture is
  still discovered only after the current-tree suite has already run,
  since the capture itself is a second full-suite run that only happens
  once the first one has completed. Baseline writes use `atomic_write_text`
  (temp-then-rename) held under an exclusive lock on a *sibling*
  `<baseline>.json.lock` file via a new generic
  `flock_path(lock_path, timeout_seconds, shared=...)` helper
  in `session.py` (which `flock_backlog` is now a thin wrapper over,
  rather than a separate re-implementation), bounded by
  `SESSION_DEFAULT_FLOCK_TIMEOUT_SECONDS` instead of blocking forever; a
  reader takes the same lock file in shared mode so it can never observe a
  write in progress. A baseline file that exists but fails to parse, or
  whose stored `branch_point` disagrees with the resolved merge-base, now
  exits 1 with `ERROR: shared-file baseline ... is corrupt and will not be
  rewritten` (or the branch-point-mismatch equivalent) on stderr and the
  file is left byte-for-byte untouched; the first-run bootstrap and
  post-change ratchet write paths are removed entirely, not gated behind a
  flag. The branch-point capture wraps the suite run and worktree removal
  in `try`/`finally` so removal is always attempted; a removal failure
  leaves the worktree directory in place (rather than deleting it out from
  under a still-registered worktree) and names `git worktree prune` as the
  remediation; and a capture run whose output cannot be attributed to
  per-test failures (including the runner not starting at all) is now a
  loud `ERROR` naming the runner and its stderr instead of silently
  persisting a synthetic "suite failed" marker as the permanent baseline.

- **Shared-file per-test failure parsing is now an explicit registry keyed by
  the repo's configured test command, and an unrecognized format is a loud
  pre-suite error instead of a degraded-but-passing marker** (spec
  `integration-reality-gates-hardening.md` sections 3.5 and 4.6; issue #13;
  source PR #318 finding 318-D4). As originally cherry-picked from PR #318 --
  before the capture-path hardening described above -- the parser tried a
  fixed list of regexes (pytest, `go test`, jest/mocha-style) against the
  suite output and, when a non-zero exit matched none of them, wrote a single
  synthetic `<suite-failed-no-per-test-detail-parsed>` marker into the
  baseline as if it were a real failing test -- so an unrecognized runner
  produced an opaque "failure" that compared equal on every later run, the
  gate reported a pass over a suite it could not actually read, and nothing
  told the operator that per-test attribution had been lost.
  `check-shared-file-impact` now resolves the repo's test command
  (`_select_test_command`) to one of three registered runner families -- a
  bare `pytest` invocation, a `go test` invocation, or the npm/yarn/npx-
  invoked jest form -- through a `_SHARED_FILE_RUNNER_PARSERS` registry. A
  `make test` command (the shape returned whenever the repo's Makefile has a
  `test` target -- the dominant shape for any Makefile-driven repo,
  including this one) is not a fourth runner family and not assumed to wrap
  pytest: each registry entry's invoker token is matched, by token
  containment, against the `shlex.split(..., comments=True)` tokens of its
  dry-run recipe (`make -n test`), comment-stripped so a trailing recipe
  comment is never mistaken for the recipe itself. A Makefile `test` target
  wrapping `go test`, jest, or an unregistered runner resolves (or errors)
  the same way a direct invocation of that runner would, and a recipe whose
  tokens contain more than one registered runner's invoker token (e.g. one
  invoking both `go test` and `pytest` -- not necessarily ambiguous to a
  human, e.g. a Python repo's recipe that installs JS dependencies before
  running pytest also trips this) is now also a loud `ERROR: cannot parse
  test output for runner '<cmd>'` naming the recipe and the matching
  candidates rather than a silent pick of whichever registry entry happens
  to iterate first. The no-match `ERROR` also now names the inspected
  recipe text, not just the bare `make test` command. Token containment is
  a narrower guarantee than "what the recipe actually invokes": a
  registered runner's token appearing anywhere
  in the recipe still selects that runner's parser even inside an uncovered
  wrapper script's own arguments (e.g. `npm ci && ./scripts/run-tests.sh`
  resolves to the npm/jest parser) or in a recipe that never runs that
  runner at all (e.g. `echo skipping pytest` resolves to the pytest parser)
  -- both are documented, known v1 limitations of the make-wrapped
  resolution path (see `cmd_check_shared_file_impact`'s docstring), not
  silently hidden. Each registry
  entry owns a dedicated parser function for that runner's own failure-line
  shape, a command-matching predicate for direct invocations, and a
  recipe-matching predicate for `make`-wrapped invocations, so
  `_resolve_runner_key` iterates the registry rather than restating the
  runner list in a hand-written if-chain -- onboarding a new runner family
  is exactly one new registry entry, never also an edit to
  `_resolve_runner_key` itself, for either resolution path. A command (or,
  for `make test`, a recipe) matching none of them is `ERROR: cannot parse
  test output for runner '<cmd>'` on stderr, exit 1, raised before that
  run's own suite subprocess is spawned -- for the current-tree evaluation
  run and for a branch-point baseline capture alike, via a dedicated
  `UnknownTestRunnerError`
  (a `ValueError` subclass) rather than the builtin, so an unrelated
  `ValueError` raised elsewhere in the gate is never misreported as an
  unrecognized-runner error -- so an unsupported runner never produces a
  partial or guessed result. The baseline record's `runner` field now stores
  the resolved registry key (spec 5.4) rather than the raw command, so a
  later run resolved to a different runner is detected as `ERROR: baseline
  was captured with runner '<stored>' but the repo is configured for
  '<cmd>'; failure sets are not comparable across runners` -- checked BEFORE
  the current-tree suite runs (costing zero full-suite runs on the mismatch
  path against an already-loaded baseline) rather than silently diffing
  incomparable failure sets; the same check also applies to a freshly
  captured baseline, since the branch-point worktree it is captured from may
  carry a different Makefile than the current tree. The current-tree
  evaluation run is now held to the same rule the branch-point capture run
  has always had: a current-tree suite that exits non-zero yet whose
  registered parser attributes zero failing node ids (e.g. a pytest
  collection/import error, which never prints a `FAILED <node-id>` line) is
  a loud `ERROR` rather than a `verdict: "pass"` reported over a suite that
  could not actually be read. The degraded-marker constant and the combined
  guess-every-format parser are deleted, not merely superseded. The
  make-wrapped recipe is now split with `shlex.split(..., comments=True)`
  rather than shlex's plain default: a trailing shell comment on the recipe
  line (an ordinary, valid Makefile annotation) is stripped before token
  matching instead of tokenised alongside the real recipe, and a recipe that
  is not shell-tokenizable at all (an unmatched quote or apostrophe outside
  of a comment) now raises the same dedicated `UnknownTestRunnerError` with
  a single formed `ERROR: ...` line naming the command, instead of an
  uncaught builtin `ValueError` traceback escaping the gate.

- **`check-shared-file-impact` resolves its changed-file set through the
  shared ADR-12 scope helper instead of a working-tree scan, attributes a
  new full-suite failure to the unit only when the failure is inside the
  unit's own scope, and its `PostToolUse` guard hook,
  `assert-shared-file-impact.sh`, now enforces a self-written verdict
  record instead of re-parsing the Claude Code payload**
  (spec `integration-reality-gates-hardening.md` sections 3.5, 4.3 and 4.6;
  AC-1 through AC-6, AC-9; source PR #318 findings 318-D7 and 318-D13).

  **Scope and attribution (unchanged since first shipped).**
  `cmd_check_shared_file_impact` previously computed its changed-file set via
  `list_changed_files`, a raw working-tree scan: a file another, unrelated
  task left dirty in the shared checkout could both trigger the gate and be
  blamed for a failure that had nothing to do with the unit under test. It
  now resolves scope exclusively through
  `work_unit_scope.resolve_changed_files(unit_id, repo_path, mode)` -- the
  same ADR-12 mode-aware helper `get-diff` and `check-manifest-scope`
  already use, shared via a `cmd_check_shared_file_impact` `_resolve_scope_or_report`
  call rather than a fourth inline `try`/`except` copy -- and matches
  `gates.repos.<repo>.shared_file_impact.patterns` against the resolved
  `ScopeResult.files`, never the working tree. A `git` plumbing failure, an
  unresolvable unit/repo path, or a work-unit file deleted in a
  same-process race (`FileNotFoundError`) surfaces as
  `ERROR: cannot resolve scope for unit <unit-id>: <message>` (exit 1). The
  full-suite RESULT the gate reports remains repo-wide (the suite itself is
  never scoped), but which of that run's NEW failures actually BLOCK the
  unit is narrower: a new failure is named in `new_failures` (and blocks)
  only when `_shared_file_gate_attributable` can attribute its failing node
  id's file to the unit's own `ScopeResult.files`; every other new failure
  is still visible in the payload's `unattributed_new_failures` list but
  never blocks.

  **Guard hook: shipped design (round-5 redesign, replacing four earlier
  review rounds' payload-parsing attempts described below).** Four review
  rounds each replaced one sed/jq heuristic for re-deriving this hook's
  verdict from the Claude Code PostToolUse payload -- first from a
  nonexistent `tool_response.exit_code` field, then from `tool_input.command`
  (a bare substring test, then progressively narrower quoted-region/token
  matchers still defeated by `bash -lc`-style wrapper forms and an
  apostrophe-sandwich quoting edge case) and `tool_response.stdout` (a
  tiered JSON-document scan defeated by a decapitated block fragment
  coexisting with an unrelated complete document). Every one of those
  defeats shared a root cause: the hook was re-parsing an agent-authored
  shell string or a composed stdout string it did not control, with no way
  to prove the parsed result was actually this gate's own verdict. The
  shipped design removes that entire re-parsing surface instead:
  `cmd_check_shared_file_impact` now persists its own verdict to a 4-line
  plain-text record file (`<workspace>/.devbench/shared-file-impact-verdict`,
  or `<workspace>/.devbench/sessions/<DEVBENCH_SESSION_NAME>/shared-file-impact-verdict`
  when a named session is active, spec 4.4.4) as the very first thing it
  does -- `"pending"`, overwritten with `"pass"` or `"block"` only on a
  clean exit path; every error-return path after that initial write leaves
  it at `"pending"` on purpose. The record's 4th line is a per-invocation
  correlator (`cli._shared_file_impact_invocation_id`, a fresh PID+counter
  value generated once per `check-shared-file-impact` invocation) that only
  `_write_shared_file_impact_verdict`'s own non-clobbering guard ever reads
  back -- see finding (1) below; the hook itself never reads or cares about
  this field. `assert-shared-file-impact.sh` no longer
  reads `tool_input.command` or `tool_response` at all (it drains stdin
  unread) and no longer sources `_hook_lib.sh` (there is no payload field
  left for it to extract; AC-4's "every extracted field goes through
  `_hook_lib.sh`" is satisfied vacuously). Its entire job is reading that
  ONE record back on the next Bash PostToolUse event it receives and then
  consuming it (deleting it after deciding what to do with it): a
  `"block"` record blocks (exit 2); a `"pass"` record allows (exit 0);
  `"pending"` (or any other unrecognised value) fails CLOSED (exit 2); no
  record at all allows (exit 0). `DEVBENCH_SESSION_NAME` routing (the `..`
  path-segment guard, and ASCII whitespace stripping) mirrors
  `cli._session_state_file_path` for every ASCII-whitespace and
  `..`-segment case, verified by a cross-layer test that writes via the
  real Python function and reads via the real script rather than two
  independent reimplementations of the same rule. Bounded, not universal
  (round-6 finding): Python's `str.strip()` also strips several non-ASCII
  whitespace code points (measured: U+001C, U+0085, U+00A0 among them) the
  shell script's `[[:space:]]` class does not, so a `DEVBENCH_SESSION_NAME`
  padded with one of those specific code points resolves to a different
  record path in each layer -- a narrow, real divergence outside the
  ASCII-whitespace cases covered above.

  **Round-5 defects found in review and fixed in this same change.**
  (1) *Block-then-pass clobber, a regression against round 4 (finding A1),
  and its residual gap (same A1 finding family, closed in a later change).*
  Two `check-shared-file-impact` invocations chained in a single Bash tool
  call fire only ONE PostToolUse event for the whole call;
  `_write_shared_file_impact_verdict` now refuses to overwrite an on-disk
  `"block"` status with anything, so a DIFFERENT, later unit's own
  `"pending"`/`"pass"` writes can never silently erase an earlier,
  unconsumed `"block"` -- verified with a real two-call repro
  (`pending`->`block` for one unit, then `pending`->`pass` for a second unit
  in the same process) that now still reads `"block"` and still makes the
  real hook exit 2. The same finding's residual gap: that guard protected
  only `"block"`, so an unconsumed `"pending"` -- left behind by an
  invocation that opened `"pending"` and then CRASHED before reaching its
  own clean verdict, exactly the "started but the verdict cannot be
  determined" case spec 3.5 requires to fail closed -- was still freely
  overwritten by a DIFFERENT, later invocation's own clean `"pass"` write.
  Every invocation now carries its own identity
  (`cli._shared_file_impact_invocation_id`), recorded as the record's 4th
  line; the guard now also refuses to overwrite an unconsumed `"pending"`
  whose recorded invocation id differs from the id being written under, while
  a SINGLE invocation's own `"pending"` -> `"pass"`/`"block"` transition
  (matching id on both writes) is unaffected -- verified with a real
  two-invocation repro (`pending` for invocation A, then A crashes; `pending`
  then `pass` for a DIFFERENT invocation B) that now still reads `"pending"`
  and still makes the real hook exit 2, alongside a regression test proving
  an ordinary single passing invocation still ends at `"pass"` and the real
  hook still exits 0.
  (2) *`DEVBENCH_SESSION_NAME` whitespace-strip divergence.* `cli._session_state_file_path`
  strips the env var with Python's `str.strip()`; the shell script now
  strips it identically before routing, so a padded value (`' alpha '`) and
  an all-whitespace value (`'  '`, equivalent to unset in Python) resolve to
  the SAME record path in both layers -- previously the shell layer alone
  left the padded/whitespace-only cases un-stripped, silently missing a
  record the Python layer had written.
  (3) *`..` guard divergence, a permanent false positive.* Python rejects
  only an exact `..` PATH SEGMENT (`".." in Path(session_name).parts`); the
  shell guard previously rejected any `..` SUBSTRING, so a devbench-accepted
  session name such as `a..b` made the hook fail closed on every Bash call
  for that session forever (the guard fired before the record was even
  checked). The shell guard now implements the identical segment rule
  (case-matching the wrapped name against `*/../*`), verified to agree with
  Python on `../escape`, `a..b`, `..` and `x/../y`.
  (4) *Consume-before-branch under `set -e`.* Unlinking the record requires
  write permission on its CONTAINING directory. The hook previously
  unlinked the record unconditionally before branching on its status; a
  non-writable directory made that `rm -f` fail, and under `set -euo pipefail`
  the whole script aborted with the OS's own stderr text and a
  non-blocking exit code (1) instead of this hook's fail-closed exit 2 --
  silently walking past a real `"block"` verdict. The hook now branches on
  the record's status first and consumes it as part of reporting that
  decision; a failed consume fails CLOSED (exit 2) with a controlled
  message instead of leaking raw `rm` stderr.

  **Round-6 correction (defect introduced by the round-5 residual-gap fix
  above).** The foreign-pending guard from (1)'s residual gap took no
  `status` parameter, so it refused EVERY foreign-invocation write over an
  unconsumed `"pending"`, including a genuine `"block"` -- reproduced end
  to end with NO concurrency on the exact `unit-a ; unit-b` chained shape
  the module comment above the verdict-record constants names as the
  threat model: unit A crashes leaving `"pending"`; unit B's gate
  genuinely fails and writes `"block"`, which was silently REFUSED; the
  record stayed `"pending"`/UNIT-A, and an agent following the hook's own
  prescribed remediation (re-run unit A, which then passes) ended at
  `"pass"` with the next Bash call ALLOWED, silently discarding unit B's
  real regression. `_shared_file_impact_verdict_write_is_blocked` now
  takes the incoming `status` and never refuses a `"block"` write over an
  unconsumed `"pending"`: escalating `"pending"` straight to `"block"`
  loses nothing, since `"block"` is itself the sticky, terminal status (1)
  above protects. Verified with a real two-invocation repro (`"pending"`
  for invocation A, then a DIFFERENT invocation B's genuine `"block"`)
  that now reads `"block"` and makes the real hook exit 2 naming the
  BLOCKING unit (B), not the crashed one (A). `invocation_id` is now a
  required keyword-only parameter of `_write_shared_file_impact_verdict`
  (previously defaulted to `""`, so two callers that both omitted it
  collided and silently defeated the guard against each other).

  **Residual, disclosed rather than fixed by redesign.**
  (B1) `hooks.json` registers this script on `PostToolUse` for the `Bash`
  tool only. Measured directly against this repo's own `hook-logs.jsonl`: a
  Bash tool call that exits NON-ZERO emits a `PostToolUseFailure` event, not
  `PostToolUse` (222 of 23,836 logged Bash tool-call completions, 0.93%, as
  of this measurement); this hook is not registered for `PostToolUseFailure`,
  so it never fires on a blocking `check-shared-file-impact` invocation's
  OWN call (which exits non-zero). Combined with fix (1) above, the block is
  not lost -- it is observed on the next Bash tool call whose PostToolUse
  event actually reaches this hook, which is not guaranteed to be the very
  next Bash call if intervening calls also exit non-zero. Registering this
  hook on `PostToolUseFailure` too is a closable follow-up, knowingly
  deferred with no tracking work unit filed yet (a prior draft,
  E5-F2-S1-T3, was filed via `write-proposal` and then withdrawn via
  `reject-proposal` after it deadlocked this unit as an auto-wired
  blocker). `hooks.json` is a DEFERRED file outside this unit's Manifest,
  marked ` (ref)` in this unit's own AC-6 and Definition of Done -- per
  `docs/backlog-contract.md`'s `(ref)` rule, a read-only reference excluded
  from the diff, which is the authority for it being out of scope here.
  (C2) The "no record at all" case is reached three ways, not two: the hook
  has never seen an invocation in this session; the prior record was
  already consumed; or (previously undocumented) `cmd_check_shared_file_impact`
  never reached its own first line at all (an unrecognised CLI subcommand,
  an import-time configuration failure, argparse rejecting the invocation,
  `devbench` not on PATH, or the initial `"pending"` write itself raising
  `OSError`) -- none of which produce a record, so this case allows and is
  not closable from inside this hook (there is nothing on disk to fail
  closed on), symmetric with the existing `DEVBENCH_WORKSPACE_ROOT`-unset
  exception.
  (E1) The record is keyed only by (workspace, session); several agent
  processes (the executor and every review judge) commonly share one
  `DEVBENCH_SESSION_NAME` within an orchestrator run. Investigated for a
  usable per-agent correlator visible to both the gate subprocess's
  environment and this hook's invocation: the PostToolUse payload's
  `session_id` and the gate subprocess's `CLAUDE_CODE_SESSION_ID` both
  identify the top-level orchestrator session, not the individual agent --
  measured directly against `hook-logs.jsonl`, multiple concurrently-running
  agent types share the identical `session_id`. No usable correlator was
  found; a verdict written by one agent's invocation can therefore be
  consumed by a different, concurrently-running agent's own next Bash
  PostToolUse event, bounded to agents sharing the same workspace and
  session name.
  (E2) AC-5's literal wording and the Definition of Done's `_hook_lib.sh`
  line both describe the payload-parsing mechanism this redesign removes,
  so neither is satisfiable verbatim by any implementation of this design;
  `devbench` offers no mechanism to revise shipped acceptance-criteria text.
  The shipped design instead satisfies AC-5's INTENT (spec 3.5, 4.6: fail
  closed rather than allow whenever the verdict cannot be determined), which
  every path through this hook still honours except the two narrow,
  explicitly reasoned exceptions (no record at all, `DEVBENCH_WORKSPACE_ROOT`
  unset).

- **Reachability check on the code-review gate**
  (`caylent-solutions/devbench-internal-backlog#10`). A task could
  previously build a component, hook, slice, or pure function as a
  self-contained deliverable, pass its own unit tests, clear `code-reviewer`,
  and be marked done without the separate step of wiring it into the real app
  (a route mount, a parent container's prop list, a shell's child
  composition) ever happening -- `code-reviewer`'s rubric never checked
  cross-file usage. A new `devbench check-reachability <id>` command greps
  the target repo, language-agnostically and restricted to source-classified
  files, for artifacts in the unit's own Changes Manifest scope with zero
  non-test references, and `code-reviewer` now surfaces that evidence and
  fails with `UNREACHABLE_ARTIFACT` when an artifact is genuinely orphaned
  rather than a grep false positive. A legitimate deferral (feature-flagged,
  Storybook-only, explicit follow-up task) is recorded with `uv run devbench
  log-waiver <judge> <unit-id> --gate reachability --target <t> --reason <r>
  --operator`, the only documented, audited way to clear a finding without
  fixing the wiring.

- **Added `devbench check-ancestry`, the canonical git-ancestry gate for
  declared work-group dependencies**
  (`caylent-solutions/devbench-internal-backlog#12`). Adds `devbench
  check-ancestry <id> <dependency-ref> [<target-ref>]`, which runs `git
  merge-base --is-ancestor` in the work unit's target repo to answer "has
  this declared prerequisite actually merged" with real git ancestry
  rather than a weaker proxy such as a local snapshot/report file. Wires
  this into `spec-to-backlog`: when a spec/operator declares a
  work-group dependency, the skill now auto-generates a mandatory,
  executable ancestry-gate task at `E0-F<N>-S1-T1` that every root of the
  intra-backlog dependency DAG depends on, so no other task in the
  backlog can be claimed until the gate passes. `docs/cli-reference.md`
  documents `devbench.check-ancestry` as the single canonical check for
  dependency deliverability, and `docs/cross-backlog-dependencies.md`
  gains a "producer is another devbench work group's branch" case
  distinct from the existing operator-verified manual-blocker idiom.

- **Added a copy-pattern permission/eligibility flag write-path audit helper**
  (`caylent-solutions/devbench-internal-backlog#16`). Specs that instruct an
  implementer to add a new permission/eligibility boolean by "following the
  exact pattern of" an existing flag could silently inherit that flag's
  missing write-path, since backlog generation never produced a task owning
  "wire this flag to real data." The new
  `devbench.plugin_helpers.permission_flag_writepath` module gives the
  `spec-to-backlog` skill's new Step 3b a heuristic, source-grep-based way to
  audit the referenced flag's write-path status and locate an existing
  placeholder/mock permission-provider seam before backlog generation
  proceeds, surfacing a `[BLOCKING_FINDING]` for operator acknowledgement
  when the referenced pattern is not actually live.

- **Added a newly-reachable-paths requirement to bug-fix tasks' Definition of
  Done** (`caylent-solutions/devbench-internal-backlog#15`). A fix that clears
  a reported repro often gates open a code path that was never reachable
  before; confirming the repro passes says nothing about what was behind the
  gate. `executor.md` gains a BUG-FIX COMPLETENESS section requiring the
  executor to enumerate and live-verify what the fix newly unlocks before
  completion, logged via `[NEWLY_REACHABLE]`; `code-reviewer.md` gains an
  independent BUG-FIX COMPLETENESS rubric (items 53-55) backed by the new
  `NEWLY_REACHABLE_PATH_UNVERIFIED` vocabulary code, and `blocker-resolver.md`
  seeds a matching AC on bug-fix-shaped follow-up proposals. `proposal.py`'s
  `generate_draft_md` auto-appends the matching Definition of Done item on
  materialised drafts: keyed on the `## Task Type:` taxonomy
  (`ProposedTask.task_type` resolving to `constants.TASK_TYPE_BEHAVIOR_FIX`),
  not a title heuristic, so the mechanical DoD append is exempt from the
  false positives/negatives a `"Fix "`-prefix title match would produce. See
  `docs/newly-reachable-paths.md` for the full rationale and worked examples.

- **`backlog_post_processor._find_section_bounds` matched heading text quoted
  in another section's prose** (issue #337). The unanchored
  `text.find(header)` let a Description that discusses "the task's
  `## Acceptance Criteria` line" hijack the section bounds, so
  `suffix_ref_on_orphan_paths` appended ` (ref)` to path tokens far outside
  the Acceptance Criteria / Definition of Done sections -- including inside
  `### Code Standards` blocks, which `verify_code_standards_canonical` then
  reported as permanent drift the two passes re-created on every `run_all`.
  The heading is now matched as a whole line (`^<header>$`, MULTILINE),
  mirroring the anchored `_NEXT_H2_RE` end bound and the validator's
  line-anchored `_extract_sections`, so the post-processor and
  validate-backlog Rule 20 agree about section membership.

- **`guard-bash.sh` over-blocked `git checkout --theirs` / `git checkout
  --ours`** (issue #335). The blocked pattern was the bare substring
  `git checkout --`, which matched conflict-side selection during a merge
  or cherry-pick as well as the destructive file-restore form it was
  aimed at -- and unlike `guard-destructive-git.sh` there is no override
  environment variable, so agent-driven conflict resolution was hard
  blocked. The pattern is now `git checkout -- ` (trailing space),
  matching the line `guard-destructive-git.sh` already draws, and the
  previously untested hook gained its missing
  `tests/unit/test_guard_bash.py` module.

- **`guard-git-stage.sh` rule 2 (manifest-scope enforcement on `git
  add`) was dead code in production** (issue #336). The rule gated on
  the `CURRENT_WORK_UNIT_FILE` environment variable, which nothing in
  the codebase ever set -- hook processes inherit the long-lived
  orchestrator environment, so a per-work-unit variable can never reach
  them, and the silent-skip branch hid the gap while the hook's own
  tests set the variable themselves. `devbench claim` now records the
  claimed unit's file path in `.devbench/active-work-unit[-<session>]`
  under the same `BACKLOG.lock` as the status write, and the hook
  resolves the active unit from that marker when the environment
  variable is absent, enforcing only while the resolved unit still
  declares `## Status: in-progress` (a stale marker is a designed skip,
  so no clear-on-terminal-transition wiring is needed).

- **Orchestrator inactivity net and cooperative SDK teardown** (FR-17,
  issues db-262 / db-325). `devbench start`'s `_run` SDK message loop
  no longer idles forever on a wound-down turn with no terminal
  sentinel (an observed hang ran 2h24m before this fix): the loop now
  awaits `agen.__anext__()` under a bounded `asyncio.wait_for`, and a
  stall raises a new `_OrchestrateInactivityTimeout` sentinel that
  `_drive_orchestrate_with_quota_resume` disposes as a bounded
  fresh-session restart, reusing the same cap as
  `DEVBENCH_MAX_QUOTA_RESUMES` -- never a stateful `ClaudeSDKClient`
  continuation. The wait window is configurable via the new
  `timeouts.orchestrator_inactivity` key in
  `backlog/config/devbench.yaml` and the
  `DEVBENCH_ORCHESTRATOR_INACTIVITY_TIMEOUT` environment variable,
  defaulting to the new `DEFAULT_ORCHESTRATOR_INACTIVITY_SECONDS`
  constant (1800 seconds). A `finally: await agen.aclose()` around the
  loop (db-325) guarantees cooperative teardown before a quota or
  inactivity sentinel unwinds, so `aclose()` is never invoked while
  the generator is still running and the CLI subprocess is never
  orphaned.

- **`Recent pace (last N tasks)` and `Average time per task` still
  understated the real execution window by roughly an order of magnitude
  even after issue #326's same-session gate** (issue #329). Two compounding
  defects. Defect A: the unanchored transition regex scanned the entire log
  line rather than requiring the emitting logger, so a `devbench.cli` line
  that echoed a prior transition -- for example inside an SDK
  `ToolResultBlock` payload reproducing an earlier audit comment -- was
  ingested as a genuine transition; on the reproduction log 85% of
  `in-progress` matches and 47% of `done` matches were echoes, not
  transitions. Defect B: even with Defect A fixed, a genuinely re-claimed
  task (claimed, bounced by review, re-claimed in the same session) was
  anchored to its LAST claim (`MAX(ts_epoch_us)`) rather than its first,
  discarding the review-bounce time as if it were idle. On the
  reproduction log (`logs/orchestrator.log`) the combined effect
  understated the median execution window by ~10.6x (32.1 min true median
  vs 3.0 min as sampled) and projected the remaining ETA at 0.5 h where the
  corrected figure is 5.3 h. `event_index.py`'s transition queries now bind
  `logger = 'devbench.backlog_manager'` -- the sole in-tree emitter of the
  quoted `Set <id> to '<status>'` record -- as a SQL predicate so an
  echoed line can never match, and `_execution_anchor` (`report.py`) now
  selects the EARLIEST same-session claim rather than the latest.
  Candidate `in-progress` rows rejected for failing the logger predicate
  are surfaced rather than silently dropped: the `Average time per task`
  and `Recent pace (last N tasks)` cells append a
  `(<k> non-transition rows rejected)` suffix, composed AFTER the #326
  `(<k> excluded: no execution window)` suffix, naming how many candidate
  rows were rejected. `docs/cli-reference.md`'s ETA-formula note documents
  the anchor contract and both suffixes.

- **Rubric item numbering in `test-reviewer.md`, `code-reviewer.md` and the
  `spec-to-backlog` SKILL renumbered once against the post-0.4.0 baseline,
  and pinned structurally** (spec `integration-reality-gates-hardening.md`
  section 4.11, PM-secondary-1; AC-12). The E1 cherry-pick tasks preserved
  each source PR's own rubric item numbers verbatim (section 4.14: E1
  preserves content, not numbering coherence), and since every PR was cut
  from a pre-0.4.0 base, several insertions collided with each other and
  with the shipped baseline: `test-reviewer.md`'s `COMPOSITION-ROOT /
  REAL-ENTRY-POINT VERIFICATION` (issue #11) and `LAYOUT / VISUAL AC
  VERIFICATION` (issue #14) sections both restarted at item 50, colliding
  with the pre-existing `RED-GATE EVIDENCE` block; `code-reviewer.md`'s
  `REACHABILITY` section (issue #10) restarted at item 53, colliding with
  the pre-existing `BUG-FIX COMPLETENESS` block; and the `spec-to-backlog`
  SKILL had duplicate items in Step 4b (issue #12 ancestry vs issue #16
  write-path-audit), Step 5b (issue #228 baseline, issue #15
  newly-reachable-paths, issue #16 write-path-audit, issue #11
  composition-root and issue #14 layout all competing for items 12/13),
  and its "Self-critique rubric for spec-to-backlog" reference section
  (issue #12 vs issue #16, item 12). Every duplicate is renumbered forward
  to the next free integer in file order; `test-reviewer.md`'s
  `FIXTURE-CATALOG CONSISTENCY` block (already correctly at items 54-56)
  and `code-reviewer.md`'s `BUG-FIX COMPLETENESS` block (already correctly
  at items 53-56) are left untouched, matching the allocation table's
  `test-reviewer items 54-56` / `code-reviewer items 53-55` anchors; the
  SKILL's Step 4b, Step 5b and Step 7/self-critique-rubric duplicates
  resolve to items 8-9, 13-15 (three of the five competing entries) and
  12-13 respectively. Every internal cross-reference to a renumbered item
  within these three files is updated in the same change; the external
  `docs/composition-root-testing.md` "Step 5b item" citation is a
  separate follow-up (E2-F7-S1-T3), since that file is outside this
  change's Changes Manifest. Exit conditions that stated a bare item
  count (`SKILL.md`'s "all 13 items scored PASS in Step 5b") now reference
  "every item" generically, so a future rubric append cannot silently
  leave a stale count behind. New structural test
  `tests/test_plugin/test_rubric_numbering.py` asserts every rubric list
  in the three files is unique, contiguous from 1 and monotonic in file
  order, asserts the allocation-table anchors land on the correct content
  (not just the correct number), asserts no exit condition states a bare
  item count, and includes seeded-violation cases proving the extractor
  genuinely detects a duplicate and a gap rather than passing by
  construction.

- **`docs/issue-provenance.md` -- the provenance map tying every integration-
  reality gate to its issue and PR history** (spec
  `integration-reality-gates-hardening.md` section 4.12, PM-secondary-2,
  section 4.13; AC-3, AC-23, AC-24; AC-E2-F7-S1-T2-1 through -5). A single
  five-column table (Gate, Internal Issue, Source PR, Devbench Issues, Spec
  Section) maps each of the eight gates to its
  `caylent-solutions/devbench-internal-backlog#10`-`#17` issue, its source
  pull request `caylent-solutions/devbench#315`-`#322`, and its defining spec
  section, plus rows for the `caylent-solutions/devbench#335`/`#336` harness
  guard fixes and the five Section 15 follow-ups still awaiting an E11-filed
  issue number. This table is the input E11's closure work units read to know
  which issues, in which repo, to close. New
  `tests/test_docs/test_issue_provenance.py` walks exactly six root/extension
  pairs -- `docs/*.md`, `plugin/*.md`, `plugin/*.sh`, `plugin-authoring/*.md`,
  `src/devbench/*.py` and `tests/*.py` -- plus `CHANGELOG.md` (a
  directory-walk discovery, not a hard-coded file list, so a file a later
  epic adds under one of those six pairs is covered automatically) for the
  fully-qualified `devbench-internal-backlog#<N>` citation form and the
  bare, zero-padded two-digit placeholder form the source PRs originally
  carried, and asserts every one resolves against a row in the map,
  confirming zero fabricated or unmapped internal-backlog citations remain
  in the walked file set (AC-3); a seeded fabricated bare zero-padded
  two-digit citation fails `find_unresolvable_citations`, and a seeded map
  row citing a nonexistent spec section fails the new
  `find_invalid_spec_sections` detector, proving both failure shapes the
  source PRs and a drifted map row could produce are actually caught rather
  than merely asserted. `parse_provenance_map` is the single annotated
  helper every test case in the module uses to read the table, and it
  raises naming the offending line when a data row is missing one of the
  five required columns. JSON config surfaces such as
  `src/devbench/config-schema.json` are outside the walked six pairs.

- **`configure-devbench` rewritten as a full-config, every-invocation
  interview with a schema-coverage pin** (spec
  `integration-reality-gates-hardening.md` section 4.15, D-16, G12; AC-28,
  AC-29; AC-E2-F8-S1-T1-1 through -6).
  `plugin-authoring/devbench-authoring/skills/configure-devbench/SKILL.md`
  now interviews the operator about EVERY setting in
  `src/devbench/config-schema.json` -- 130 static leaf settings, plus the
  dynamic per-entry maps collected through bounded free-text loops, across
  21 steps, each with its own recommended value marked as such, every alternative,
  and a free-form entry path, plus a full explanation of the setting and the
  consequence of each choice. The interview runs in full on every
  invocation; prior values are shown as the current value but never
  silently reused without being re-asked. The `gates:` block added by E2-F1
  (all eight gates, their tunables, `fixture_consistency.canonical_sources`
  / `.scan`, and the `gates.repos.<org/repo>` per-repo override map) is
  interviewed for the first time, alongside `orchestrate.max_cascade_depth`,
  the `quota_handling:` block, the `skills:` block,
  `git_ops.branch_prefix` / `.orphan_patterns` / `.pr_review_resolution`,
  `repos.<org/repo>.branch_prefix`, `allowed_orgs`, `display_timezone`
  (top-level and `report.display_timezone`), and
  `backlog.bulk_update_confirm_threshold` / `.bulk_update_audit_path` --
  every one of which the pre-rewrite skill either silently emitted at its
  built-in default without asking, or never emitted at all. The Step 21
  final-write step now validates the assembled yaml via
  `load_runtime_config` strictly before writing
  `backlog/config/devbench.yaml` and reporting `[CONFIGURE_DEVBENCH_DONE]`
  success (AC-29), structurally pinned by file-order rather than by
  inspection.

  New `tests/test_plugin/test_configure_devbench_schema_coverage.py` is the
  anti-drift mechanism: `walk_schema_settings` recursively walks
  `config-schema.json` (including every `gates.*` key) and
  `assert_skill_names_every_setting` fails naming any property the SKILL
  text does not name; a companion `assert_interview_blocks_complete` parses
  every `#### \`dotted.path\`` interview block and fails naming the setting
  and the missing element when the Recommended, Alternatives, or Free-form
  marker is absent. Both helpers, plus the AC-29 output-contract ordering
  check, carry seeded-mutation and seeded-omission controls over synthetic
  in-memory fixtures (never the real schema or SKILL file) proving every
  assertion is genuinely falsifiable rather than vacuously true. A future
  config key added without matching interview coverage now breaks this test
  immediately instead of surviving as a silent one-time gap.
  `docs/skills/configure-devbench.md` documents the every-invocation
  contract, the 21-step walkthrough, and the schema-coverage regression
  guard.

### Fixed

- **A single Claude Agent SDK transport hiccup ended a multi-hour unattended
  `devbench start` run with no retry** (issue #331). `_run`'s SDK message
  loop caught only `StopAsyncIteration` and `TimeoutError`; any other
  exception raised from `agen.__anext__()` -- for example the upstream
  `Exception: Claude Code returned an error result: success` frame reported
  at anthropics/claude-agent-sdk-python#1203 -- propagated uncaught through
  `asyncio.run` and killed the daemon; this happened twice in twelve hours.
  `_run` now re-raises any other SDK-generator-boundary exception as a new
  `_OrchestrateTransportError` (carrying the original as `__cause__`), and
  `_drive_orchestrate_with_quota_resume` gains an
  `except _OrchestrateTransportError` arm that logs the verbatim exception at
  ERROR with its restart ordinal and cap, then restarts a fresh SDK session
  -- bounded by the SAME `DEVBENCH_MAX_QUOTA_RESUMES` cap that already bounds
  quota resumes and inactivity restarts, tracked with its own independent
  counter -- or re-raises once the cap is exhausted, preserving the legacy
  non-zero exit and the verbatim final exception. Classification is
  structural (which call raised), never message-based: the observed
  trigger's exception text was the literal word `success`, so pattern-
  matching upstream text would be brittle exactly when it matters.
  `_label_stop_reason` gains the `transport-error-restart-cap-exhausted`
  class so the `orchestrator_stop` notification names the exhausted-cap case
  instead of an unlabelled crash, and `devbench report` renders a
  `Transport restarts <n>` row (only when `n > 0`) via the new
  `report.transport_restarts_line`, counting `[ORCHESTRATOR_TRANSPORT_RESTART]`
  audit lines. `docs/cli-reference.md` documents the recovery path and the
  report row; `docs/adr/34-orchestrator-transport-restart.md` records the
  design decisions, including why classification is structural rather than
  message-based.

- **`add-dep` reported `"wired": true` and exited 0 even when the printed
  Manifest-conflict remedy stayed inert** (issue #330 FR-1, FR-2). The
  Manifest Conflict Rule's dep-chain scan reads the `## Dependencies`
  table, but `add-dep` wrote only the `[BLOCKED_PENDING_PROPOSAL]`
  marker, whose ADR-07 cascade fires solely on `blocked` units -- for a
  non-blocked unit the two never met, yet the command still reported
  success. `cmd_add_dep` (`src/devbench/cli.py`) now writes a canonical
  `## Dependencies` row for the blocked task alongside the existing
  marker, with the Title and Status cells carrying the blocker's real,
  current values (not a placeholder), idempotently and under
  `flock_backlog`. `"wired": true` now means the blocked task's
  `## Dependencies` table carries a validator-visible row for the
  blocker as of that call (true whether newly written or already
  present on a repeat call); a request that cannot produce such a row
  reports `"wired": false`, exits non-zero, and populates `reason`,
  leaving no partial write. The status warning now names the
  consequence (the ADR-07 cascade will not fire until the blocked task
  is itself `blocked`) rather than implying the marker is merely
  deferred. `docs/cli-reference.md`, `docs/manual-blockers.md`, and
  `docs/cross-backlog-dependencies.md` document the corrected contract.

- **A full backlog run finished 101 done and 9 declined tasks -- zero remaining -- and never
  rolled its epics to `done` or opened a pull request** (issue #332). Two independent defects,
  either alone enough to break the finish line. First, `BacklogManager._rollup_parent_status`
  was invoked only from a fresh `STATUS_DONE` transition, so a Story/Feature/Epic whose last
  remaining child resolved via `decline` (not `mark-done`) was stranded in a non-terminal status
  forever, even though `_all_children_done` already treated `declined` as terminal; the rollup
  call now fires from any terminal transition (`done` **or** `declined`), and `devbench
  reconcile-cascade` gained a second pass (`_repair_stranded_containers`) that walks every
  non-terminal container, re-evaluates `_all_children_done` fresh, and promotes qualifying
  containers -- cascading upward exactly as a live rollup would -- reported in the command's JSON
  envelope as `rolled_up` and idempotent on a second run. Second, the finalize auth gap:
  `GitOpsService._git()` never carried `GH_TOKEN` in its subprocess environment the way `_gh()`
  already did, so every `git push` -- including the one `git-ops-finalize` depends on -- fell
  back to whatever ambient credential helper the launching shell had; with `defer_pr: true` that
  push happens at the very end of a multi-hour run, exactly when an inherited VS Code
  credential-helper socket is most likely stale, and the observed failure (`remote: No anonymous
  write access. fatal: Authentication failed`) occurred twice against a token that was valid
  throughout. `_git()` now builds the same `GH_TOKEN`-backed environment and inline
  `credential.helper` as `_gh()`, so a drained backlog's finalize push authenticates identically.
  A new integration test (`tests/test_integration/test_drained_backlog_finalize.py`) drives a
  backlog with a declined leaf to fully terminal and asserts both that the rollup reaches the
  epic and that `git-ops-finalize` is reached and attempts a push, pinning the whole "every task
  terminal" to "a PR exists" path so the defect cannot regress silently again. `docs/cli-
  reference.md` documents `reconcile-cascade`'s repair pass and summary-line format;
  `docs/backlog-contract.md` documents the terminal-rollup contract.

### Changed

- **Dependabot PRs #216 (`idna`) and #179 (`urllib3`) reconfirmed already
  superseded on current `main`; GitHub closure stays deferred to the
  batch-PR merge** (`spec/dep-remedy-and-dependency-currency.md` FR-4,
  Section 8). Both targets' floors (`idna` >= 3.15, `urllib3` >= 2.7.0)
  are already exceeded by the locked `idna` 3.18 and `urllib3` 2.7.0,
  landed in the 0.4.0 release by task E6-F1-S1-T2 (`uv lock
  --upgrade-package idna --upgrade-package urllib3`, commit
  `4338773`). `uv lock --dry-run` reports zero lockfile changes on
  current `main`, so re-running the bump would require hand-editing
  `uv.lock` or moving an unrelated pin, both forbidden by spec decision
  D-4; the task that would have re-landed the bump (E14-F2-S1-T1) is
  therefore declined as already-satisfied rather than re-doing a
  resolution with no unresolved constraint left to satisfy. `make
  validate` re-confirms the full suite is green at or above the 98%
  coverage floor on the current lock. Closing #216 and #179 on GitHub
  remains deferred until the single batch PR carrying `feat/bug-closure`
  merges to `main`, matching the posture already recorded for this
  identical PR pair.

- **`spec-to-backlog`'s generator-side ancestry-gate template now teaches
  the same four-outcome `check-ancestry` exit contract as `devbench
  check-ancestry` itself and `docs/cross-backlog-dependencies.md`,
  closing a disabled-gate fail-open in the generated `AC-DEP-001`**
  (`plugin-authoring/devbench-authoring/skills/spec-to-backlog/SKILL.md`
  "Authoring the ancestry-gate task" block; issue #12). Because gates
  default to disabled (D-17), a disabled ancestry gate's exit 0 was
  indistinguishable, under the old two-outcome template, from a merged
  dependency's exit 0 -- every ancestry-gate task generated into a
  backlog that had not opted into `gates.ancestry.enabled` would
  self-certify "dependency merged" with zero ancestry ever verified. The
  template's generated `## Acceptance Criteria` now requires the printed
  status line to carry `status: "pass"` together with `mode: "strict"` or
  `mode: "squash-pr"`, and states explicitly that a printed `status:
  "disabled"` line does not satisfy `AC-DEP-001`. The `### Approach`
  block documents all four outcomes (merged, disabled-gate, BLOCKED/
  evaluation error, usage error), the retired `"ancestor"`/`"not_ancestor"`
  tokens are gone, the `dependency_ref`/`target_ref` examples read
  `<remote>/<dependency-branch>` and `<remote>/<default-branch>` instead
  of a hard-coded `origin/`, and the cross-reference to
  `docs/cross-backlog-dependencies.md` now points at that document's
  "Squash-aware verification (317-D02)" section rather than a superseded
  "known limitation" paragraph.

- **New `wire-gate <gate-task-id> --blocks-roots` verb mechanises
  ancestry-gate fan-in, and `spec-to-backlog`-generated gate tasks are now
  typed `chore` with a real Manifest row so they can actually reach
  `done`** (spec `integration-reality-gates-hardening.md` section 4.5,
  317-D01/317-D23; issue #12). Generating a gate task previously left it
  untyped, which `validate-backlog` rule 21 defaults to the RED-gated
  `behavior-fix` -- a check-only task authors no code and can never
  produce the required RED evidence, so the generated task deadlocked
  permanently at the done transition. The `spec-to-backlog` template now
  authors `## Task Type: chore` with a `## Changes Manifest` row naming
  the gate task's own report file (`docs/gate-reports/<id>-ancestry.md`)
  instead of `(none)`, so the manifest is genuinely non-empty and
  classifiable. Fanning the gate into every root of the intra-backlog
  dependency DAG previously required hand-authoring an O(N) `##
  Dependencies` row per root, each edit a place a hand-typed row could
  silently drift from the canonical shape `validate-backlog` reads
  (317-D23). `devbench wire-gate <gate-task-id> --blocks-roots` computes
  the DAG roots itself and writes every edge through the same managed
  dependency path `add-dep` already owns, so every row lands in the exact
  canonical form; it validates every root BEFORE any write and fails
  loudly (exit 1, zero edges written) on an unknown gate-task id, a
  missing root file, or a root already wired to a different gate task. The
  root computation also excludes the gate task's own Epic/Feature/Story
  ancestors (present in every real generated tree) and any root already in
  a terminal `done` / `declined` status, so a re-run is idempotent without
  ever force-reverting a completed root's status back to `blocked`.
  `docs/cli-reference.md` documents the verb under `## Gates`, pinned by
  `tests/test_docs/test_cli_reference_wire_gate.py`.

- **`check-ancestry` is wired into the done path and persists a
  target-ref-aware `[GATE_PASS ancestry]` record; `mark-done` now enforces
  it and re-verifies on resume when the target branch has moved** (spec
  `integration-reality-gates-hardening.md` sections 4.2, 4.3, 4.5, 5.2;
  AC-6, AC-7, AC-16; internal issue #12 AC3). A passing enabled run appends
  exactly one `[GATE_PASS ancestry] <iso-utc> <scope-hash>` line to the
  unit's audit section (surviving `read-unit --strip-comments`) through
  `devbench.gate_records.compose_gate_pass_record`; a failing, error, or
  disabled run writes none. The spec 5.2 status line also gains a
  `scope_hash` field -- the same digest persisted in the record on a
  passing run, and the empty string on the BLOCKED (`status: "fail"`)
  line, which persists no record; this JSON field is printed as the
  first stdout line of an enabled run, copied verbatim into the
  generated gate task's report-file deliverable, and read by the
  review judges from there. `devbench.gate_records.compute_scope_hash` gains an
  explicit, named `target_ref_sha` parameter: the ancestry gate's scope
  hash folds in the resolved target ref's current commit sha alongside
  the unit's own Changes-Manifest file blob hashes, so an identical
  changed-file set with a different target ref sha now hashes
  differently, and the value is unchanged for every other gate's caller
  that omits the parameter. `mark-done` on a unit whose repo has
  `gates.ancestry.enabled` true now refuses (exit 1, no status write)
  unless a fresh `[GATE_PASS ancestry]` record exists (or an operator has
  filed a `[GATE_WAIVER ancestry]` -- the same whole-gate waiver bypass
  every other machine-blocking gate already honours), naming the exact
  remediation `uv run devbench check-ancestry <unit-id> <dependency-ref>`;
  a record whose recomputed hash no longer matches -- including when only
  the target branch has moved, with the Changes Manifest unchanged -- is
  refused with `ERROR: gate 'ancestry' record is stale (scope changed
  since it ran)`. Because `check-ancestry` accepts an OPTIONAL explicit
  `<target-ref>` override, `cmd_check_ancestry` also writes a
  `[GATE_ANCESTRY_TARGET_REF] <target-ref>` companion marker naming the
  EXACT ref the passing run probed against
  (`devbench.gate_records.compose_ancestry_target_ref_marker`), in the
  SAME atomic write as the `[GATE_PASS ancestry]` record so a write
  failure can never leave one without the other;
  `mark_done`'s freshness recompute reads it back
  (`BacklogManager._resolve_ancestry_target_ref`) instead of
  re-deriving the repo's default branch, so a record produced with an
  explicit override recomputes against the SAME ref rather than reading as
  permanently stale. A `[GATE_PASS ancestry]` record present WITHOUT its
  `[GATE_ANCESTRY_TARGET_REF]` companion (e.g. one written before this
  marker existed) now makes `mark-done` refuse with `ERROR: Cannot
  resolve ancestry gate target ref: no [GATE_ANCESTRY_TARGET_REF] marker
  recorded ...` naming the same `check-ancestry` remediation, rather than
  silently treating the record as unchanged.

## [0.4.0] -- 2026-08-12

### Changed (model defaults)

- **Shipped model rate table refreshed to the current lineup; default
  fallback model moves to Opus 5** (issue #233). `DEFAULT_MODEL_RATES`
  (`src/devbench/constants.py`) gains four entries, all LIST rates
  verified against the official Anthropic pricing page
  (https://platform.claude.com/docs/en/about-claude/pricing), captured
  2026-07-28:
  - `claude-fable-5`: $10 / $50 per million input / output tokens.
  - `claude-opus-5`: $5 / $25 -- the new shipped default (see
    `DEFAULT_FALLBACK_MODEL_RATES`).
  - `claude-opus-4-8`: $5 / $25 -- selectable, no longer the default.
  - `claude-sonnet-5`: $3 / $15 LIST rate (spec S5.3). An introductory rate
    of $2 / $10 runs through 2026-08-31; that promotional rate is **not**
    shipped as the default -- workspaces wanting invoice-accurate
    introductory pricing during the promo window override locally via
    `report.models`.

  Every pre-existing entry is retained, including the three Haiku pricing
  rows (Haiku remains priced for reporting even though it is banned for
  work agents, issue #198).

  **`DEFAULT_FALLBACK_MODEL_RATES`** moves from an Opus-4.7-list-rates
  label to an Opus-5-list-rates label (value unchanged at $5/$25 since
  Opus 5 and Opus 4.7 share the same list rate); no hard-coded Opus 4.7
  default reference remains in `constants.py`, `config_loader.py`,
  `config.py`, or `config-schema.json`.

  **`DEFAULT_FAST_MODE_MULTIPLIER`** corrects from `6.0` (stale Opus
  4.6-era value) to `2.0`: fast mode today runs $10/$50 on a $5/$25 base
  for Opus 5 and Opus 4.8, verified against the same pricing-page capture.

  **`fable` short name added.** `ALLOWED_AGENT_MODEL_SHORT_NAMES` now
  includes `fable` alongside `opus` and `sonnet`, aliasing `claude-fable-5`
  for `agents.*` YAML overrides when `use_bedrock: false`. `haiku` remains
  absent (issue #198); the `config-schema.json` `agents` description no
  longer advertises `haiku` as an accepted short name.

  **Issue #254 superseded.** #254 asked for Opus 4.8 as the new default;
  Opus 5 shipped after #254 was filed, so per Decision D-2 this work moves
  the default to Opus 5 instead and keeps Opus 4.8 as a selectable,
  non-default model. #254 closes with this note recording the honest
  supersede rather than the literal request.

  Mirrored comments updated in the same commit:
  `src/devbench/config_loader.py` (`ReportConfig.fast_mode_multiplier`
  docstring, `_parse_default_model_rates` docstring),
  `src/devbench/config.py` (`REPORT_DEFAULT_MODEL_RATES` comment), and
  `src/devbench/config-schema.json` (`default_model`,
  `fast_mode_multiplier`, `agents` descriptions). The `docs/model-pricing.md`
  and `sample-config.yaml` mirrors are updated by the follow-up task
  E3-F2-S1-T1.

### Changed

- **Flattened the review leg: the four review-team judges are now
  invoked directly by the orchestrate skill as first-level
  sub-agents; `review-supervisor` is reduced to a non-spawning
  aggregator** (ADR-33). A live reproduction (session
  `32862e10-7ede-4265-8892-e0637684bb3e`, `claude-agent-sdk 0.2.128`,
  recorded in `docs/adr/33-flatten-review-topology.md`) showed a
  second-level Agent-tool spawn from a sub-agent succeeding
  completely and reliably under that configuration -- it did **not**
  reproduce a hard SDK restriction on sub-agent-spawns-sub-agent. The
  flatten is adopted anyway, per spec S0 B-9a, as defense-in-depth
  against model-tier-dependent Agent-tool spawn reliability -- the
  same class of risk ADR-25's haiku-rejection guard already
  mitigates by pinning. Before this flatten, review-supervisor was
  invoked as a first-level sub-agent and itself declared
  `Agent(code-reviewer, test-reviewer, doc-reviewer, changes-manifest)`
  to fan out to the four judges; whenever that second-level spawn
  failed to run -- for whatever reason, model-tier-dependent or
  otherwise -- the fan-out silently no-opped, the work unit stalled as
  `RUNTIME_DEGRADATION`, and no restart could clear it, because the
  classifier could not distinguish a genuinely blocked spawn from a
  transient one. Removing the second-level spawn removes that whole
  failure class; `review-supervisor` is still invoked, but only
  afterward, to read the four judges' already-persisted verdicts and
  aggregate them -- it is never the one dispatching them.
  - `plugin/devbench-orchestrate/skills/orchestrate/SKILL.md` step 5
    now invokes `devbench-orchestrate:review_team:code-reviewer`,
    `test-reviewer`, `doc-reviewer`, and `changes-manifest` directly,
    in a single response, as first-level sub-agents. Each judge
    self-logs its own verdict before returning.
  - `plugin/devbench-orchestrate/agents/review-supervisor.md` no
    longer declares Agent-tool spawn capability in its frontmatter
    `tools:` field. It reads the four judges' already-persisted
    verdicts from the work unit's Comments section and reports a
    consolidated result.
  - **Missing-verdict hard failure**: SKILL.md step 5a documents
    that if any of the four required judges has no verdict logged in
    the current round, that is a hard failure naming the absent
    judge -- never an implicit pass. A judge that never logged is
    indistinguishable from a judge that never ran. (The underlying
    enforcement in `BacklogManager._last_round_all_passed` already
    treated a missing verdict as a hard failure; this change aligns
    the prompts with that existing contract.)
  - `plugin/devbench-orchestrate/scripts/guard-review-supervisor-scope.sh`
    now blocks every Agent-tool invocation from review-supervisor
    unconditionally -- the prior review_team allowlist branch is
    removed, since review-supervisor never spawns any subagent
    post-flatten.
  - `src/devbench/backlog/proposal.py`'s `_RUNTIME_DEGRADATION_BODY_RE`
    comment and `_has_runtime_degradation_signal` docstring now
    describe a match as a topology **regression** signal (a match
    should never occur in a healthy post-flatten run), not a
    transient degradation an operator restart routinely clears. The
    regex pattern itself is unchanged.
  - `docs/architecture.md`, `docs/plugin-architecture.md`,
    `docs/execution-modes.md`, `docs/faq.md`, `docs/cli-reference.md`,
    and `README.md` updated to describe the four judges as
    first-level sub-agents invoked directly by the orchestrate skill,
    with review-supervisor as a non-spawning aggregator.
  - Remaining consumers of the superseded second-level-spawn contract
    updated to match: `continue-orchestration.sh`'s Stop-hook
    `NEXT_STEP` guidance now names the four `review_team` judges as
    first-level invocations before review-supervisor aggregates;
    `docs/zero-to-ready.md`, `docs/llm-authentication.md`, and
    `plugin-authoring/devbench-authoring/skills/configure-devbench/
    SKILL.md` no longer call review-supervisor a "fan-out
    coordinator"; and `docs/watch-activity.md`'s troubleshooting
    table row and omitted-content bullet now describe
    `review-supervisor running` as meaning the four judges have
    already finished and self-logged, not that they are concurrently
    running.
- **`claude-agent-sdk` lock advanced 0.1.48 -> 0.2.128** (issue #231; epic
  driver #255). `pyproject.toml:18` already declared
  `claude-agent-sdk>=0.1.48` with no upper bound -- `uv.lock` was simply
  pinned to a stale 0.1.48 resolution. `uv lock --upgrade-package
  claude-agent-sdk` resolved cleanly to 0.2.128 with no `pyproject.toml`
  edit required. The nine sites in `tests/test_cli.py` that construct real
  `claude_agent_sdk.types` objects (`AssistantMessage`, `ToolUseBlock`,
  `ResultMessage`) pass unchanged against the upgraded SDK -- no
  constructor signature change was observed. A live probe (`query()`
  against a minimal prompt, run outside the orchestrator's own SDK
  session via `env -u CLAUDECODE`) shows the 0.2.x iterator now
  **terminates naturally** after a single `ResultMessage`
  (`ITERATOR TERMINATED NATURALLY` printed within ~4s total), in contrast
  to the 0.1.x behaviour documented above (#218) where the iterator never
  terminated on its own and re-emitted paid `ResultMessage` turns every
  ~5s. This is a cadence improvement, not a regression -- the
  `_TERMINAL_ORCHESTRATE_MARKERS` early-break workaround remains in place
  pending its own removal task. `make validate` passes unchanged after the
  advance (98.01% coverage, 5084 passed, 8 skipped -- identical to the
  pre-upgrade baseline).

- **`actionability_line` gains two active-run outcomes instead of collapsing
  to the stuck-state message while work is executing** (issue #309, spec
  Section 4 FR-1, E9-F1-S1-T1). `get_parallel_candidates` deliberately
  includes `IN_PROGRESS` units (issue #185, resume support) and the function
  then subtracted `active_ids`, so when the only candidate was the unit
  already running, the list emptied and `No actionable units. N blocked.`
  printed while work was actively executing -- in a serially-ordered backlog
  that is the steady state, so the operator-facing line cried wolf for the
  whole run and camouflaged the genuine deadlock case (issue #253). The
  three-outcome contract in `src/devbench/backlog/actionability.py` is now
  five: `Next actionable: <id> -- <title>` and `All work units are DONE.`
  keep their byte-identical strings; a new third branch renders `<id>
  active; nothing else can start yet. <tail>` for exactly one active id and
  `<N> units active; nothing else can start yet. <tail>` for several; the
  fall-through re-bases to `No actionable units. <tail>`; and `<tail>` is
  now `<B> blocked` or `<B> blocked, <H> on hold` when `H` (units with
  status `HOLD`) is greater than zero, computed directly from `units` via
  `WorkUnitStatus.HOLD` with no parser change. Both callers (`cli.py`
  `status`, `reporting/report.py` `report`) inherit the change untouched;
  `devbench next`'s own machine-token output is not modified.

### Added

- **Honest completion paths for the machine-observed RED gate: three
  named remedies, a cited already-satisfied decline, and a refactor
  green-green check** (FR-4.5/FR-4.6, E4-F4-S1-T2). The RED gate
  (`devbench.tdd_gate._build_rejection_message`, shipped by
  E4-F3-S1-T2) already named all three legitimate ways forward --
  produce a genuine RED, re-type the task, or decline it as
  already-satisfied -- in every rejection it raises. This task adds two
  new surfaces that reuse the same `tdd_gate.REMEDY_1`/`REMEDY_2`/
  `REMEDY_3` constants via a new
  `devbench.backlog.manager._build_remedies_rejection_message` helper so
  the same three remedies are named consistently there too: the
  gated-task-type block enforced by `BacklogManager.mark_done` (below),
  and `devbench decline`'s citation requirement. `devbench decline`
  gained a `--citation
  <commit-hash-or-task-id>` flag: declining a task with a reason naming
  "already-satisfied" now requires a valid citation (a 7-40 character
  lowercase hex commit hash or a task id, checked by the new
  `BacklogManager.is_valid_citation`); an uncited already-satisfied
  decline is rejected as unfalsifiable, and `validate-backlog` gained a
  matching static check (check 22) that flags any already-satisfied
  `[DECLINED]` comment persisted without one. The FR-4.5/FR-4.6
  task-type completion invariant now lives in
  `BacklogManager.mark_done` itself
  (`_check_task_type_done_invariant`), not in a CLI-layer wrapper, so
  every caller inherits it identically: both `devbench mark-done` and
  `devbench check-merge` (on a merged PR) now refuse a gated task
  (`behavior-fix` / `feature`, including the default when `## Task
  Type:` is omitted) that carries no `[RED_OBSERVED]` record in its TDD
  Cycle Log, so a behavior-fix whose test already passed before any
  change is routed to decline rather than silently claimed as done via
  either surface. A new `devbench green-green-check <id>
  <test_node_id> [...]` command gives `refactor` tasks -- exempt from
  the RED gate but not from their own invariant -- a way to prove the
  change is behavior-preserving: it confirms the named tests pass in
  the current ("after") tree, path-scoped stashes the Changes
  Manifest's production-source rows to reconstruct the pre-change
  ("before") state, confirms the same tests pass there too, and
  restores the stash unconditionally (including when the before-state
  run itself raises). If the stash push finds no uncommitted
  production-source change to save, the check rejects rather than
  silently comparing the tree to itself, so a refactor with nothing
  actually changed cannot false-pass. A collection failure on either
  side fails closed, never reported as a pass. On success, the check
  appends a machine-observed `[GREEN_GREEN_OBSERVED]` entry to the work
  unit's TDD Cycle Log naming the confirmed test node ids.
  `GREEN_GREEN_OBSERVED` is registered in `constants.VALID_TDD_PHASES`
  as orchestrator-only (not in `AGENT_WRITABLE_TDD_PHASES`), mirroring
  the `RED_OBSERVED` control: an agent cannot write it via `log-tdd`,
  and `cli._reject_bracketed_phase_tag`'s bracketed-phase-tag forgery
  check now also rejects a forged `[GREEN_GREEN_OBSERVED]` tag in
  `log-tdd`/`log-comment`/`log-verdict` free text. Because the
  same `BacklogManager.mark_done` invariant check backs both surfaces,
  both `devbench mark-done` and `devbench check-merge` now refuse a
  `refactor` task carrying no such record, so `green-green-check` is a
  gate a refactor task must pass through, not an optional, unconsumed
  command. Three end-to-end journeys in the new
  `tests/test_integration/test_tdd_red_gate_e2e.py` script the
  operator-facing scenarios against real git repositories: a false-fix
  attempt is judged REVIEW_FAIL with the exact FR-4.4 message pulled
  verbatim from the judge prompts; an honest behavior-fix (real RED
  observed, real fix applied, real GREEN) reaches done with the
  `[RED_OBSERVED]` record present; and all five required judge verdicts
  are independently attributable, with any one of the four review-team
  judges missing blocking done.

- **Quota wait-and-resume** (ADR-24, issue #236). `devbench start`
  detects Anthropic subscription rate-limit exhaustion mid-session
  (HTTP 429, the CLI's verbatim "You've hit your limit" text, or an
  `AssistantMessage.error == "rate_limit"` field) and pauses the
  orchestrate loop instead of exiting non-zero: it checkpoints the
  pause to `.devbench/quota_pause.json`, waits for the provider's
  `reset_at` (or polls a recovery probe when `reset_at` is unknown),
  then opens a fresh in-process SDK session and continues the backlog
  automatically once quota recovers. Configurable via the new
  `quota_handling` block in `backlog/config/devbench.yaml`
  (`enabled`, `on_exhaustion`, `poll_interval_seconds`,
  `max_wait_seconds`, `on_exhaustion_timeout`, `resume_strategy`,
  `audit_comment_on_wait`, `audit_comment_on_resume`,
  `log_structured_events`) -- default-on, waits on exhaustion, drains
  on timeout. The wait never uses `asyncio.shield`, so
  `devbench stop --session <name>` (or a direct SIGTERM) still
  interrupts a paused session promptly, force-blocking the in-flight
  work unit rather than leaving it in an ambiguous state. The new
  `devbench quota-watcher` command reports the current pause state
  (`reason`, `reset_at`) from the on-disk checkpoint without
  disturbing the running orchestrator; `devbench status` continues to
  show the paused work unit under "Active work units:" for the
  duration of the wait. In-process resumes are bounded by
  `DEVBENCH_MAX_QUOTA_RESUMES` (default 1000) so an unattended
  overnight run can survive multiple quota windows without exceeding
  a fail-safe cap. See `docs/quota-handling.md` and
  `docs/adr/24-quota-wait-and-resume.md`.

### Removed

- **`sdk_teardown_filter` workaround module removed** (issues #232, #231).
  The 185-line `src/devbench/sdk_teardown_filter.py` asyncio exception
  handler that downgraded the known `claude-agent-sdk` `Query.close()`
  cancel-scope `RuntimeError` teardown race to a `WARNING` is deleted,
  along with its 347-line test file `tests/test_sdk_teardown_filter.py`.
  `cmd_start`'s `_run` coroutine in `src/devbench/cli.py` no longer wraps
  the SDK `query()` loop in `async with _sdk_teardown_guard():`; the
  `async for` loop body is unchanged, only unindented one level. The
  operator-facing paragraph describing the workaround was removed from
  `docs/cli-reference.md`. The workaround is no longer needed now that
  `uv.lock` resolves `claude-agent-sdk` to `0.2.128`, above the `>=0.2.87`
  floor at which the cancel-scope teardown race is resolved upstream
  (verified in E1-F1-S1-T1); `pyproject.toml`'s declared floor remains
  unchanged at `>=0.1.48`, so a fresh resolve against the manifest alone
  is not guaranteed to select a fixed version -- the lock file is the
  operative evidence, not the manifest floor. Issue #232 (this workaround)
  and issue #231 (the upstream lock-advance tracking issue) are both
  closed as a result.

### Dependencies

- **Eight open dependabot PRs reconciled against the resolved lock; six
  closed unmerged, two bumped explicitly** (spec FR-6.1, FR-6.2, FR-6.3,
  D-14; AC-78, AC-79, AC-80, AC-81, AC-82, AC-83). Per decision D-14, E6
  reconciles the dependabot backlog against
  what `uv.lock` actually resolves rather than blind-merging every open
  branch, which would guarantee lock conflicts. `tools/check_dependabot_targets.py`
  is added as the reconciliation checker: it parses `uv.lock` with stdlib
  `tomllib`, compares each of the eight targets below against the locked
  version with a numeric version-tuple comparison, and prints one line per
  target in spec G-6's worked-example format. Resolved-version matrix after
  both E6-F1-S1-T3's mcp-family lock advance (commit
  `6ec06c7a02deeb1714fd5c8bb45230971f65b603`) and this task's idna/urllib3
  bump:

  | PR | Package | Locked | Target | Verdict |
  |---|---|---|---|---|
  | #287 | mcp | 1.29.0 | 1.28.1 | SATISFIED |
  | #278 | pydantic-settings | 2.14.2 | 2.14.2 | SATISFIED |
  | #277 | starlette | 1.3.1 | 1.3.1 | SATISFIED |
  | #276 | cryptography | 50.0.0 | 48.0.1 | SATISFIED |
  | #275 | python-multipart | 0.0.32 | 0.0.31 | SATISFIED |
  | #274 | pyjwt | 2.13.0 | 2.13.0 | SATISFIED |
  | #216 | idna | 3.18 | 3.15 | SATISFIED (E6-F1-S1-T2) |
  | #179 | urllib3 | 2.7.0 | 2.7.0 | SATISFIED (E6-F1-S1-T2) |

  **Starlette major-jump evidence (FR-6.2).** #277 moves starlette from
  `0.52.1` to `1.3.1`, a major-version jump reached transitively via `mcp`
  and `sse-starlette`; devbench imports no starlette symbol directly, so
  this is recorded evidence rather than an assumed zero blast radius.
  `mcp==1.29.0`'s own package metadata declares `starlette>=0.27` for
  `python_version < '3.14'` and `starlette>=0.48.0` for
  `python_version >= '3.14'`; `sse-starlette==3.3.2` (the lock's only other
  starlette parent) declares `starlette>=0.49.1`. The resolved `starlette
  1.3.1` satisfies every one of those lower bounds, confirming `mcp` accepts
  the resolved starlette rather than rejecting it -- the FR-6.2 BLOCK path
  (close #277 not-applicable with resolver evidence, never force-merge) was
  not triggered.

  **Six PRs closed unmerged with satisfying-commit evidence (AC-79).** Each
  of #287, #278, #277, #276, #275, and #274 is closed via `gh pr close`
  with a comment naming commit `6ec06c7a02deeb1714fd5c8bb45230971f65b603`
  as the satisfying lock advance; `gh pr view` confirms all six show
  `state=CLOSED`, `mergedAt=null`. None is merged, per FR-6.1's error
  handling: a dependabot branch whose target is already satisfied by the
  resolved lock is never merged.

  **Two targets bumped, independent of the mcp cascade (FR-6.3, AC-81,
  AC-82).** #216 (idna) and #179 (urllib3) needed an explicit bump because
  the mcp-family advance had no reason to move them, not because they sit
  outside the dependency graph the advance touched. `idna` in fact sits
  beneath `mcp` twice over: `mcp==1.29.0` depends on both `anyio` and
  `httpx`, and both of those depend on `idna`; `starlette` (one of the six
  targets moved by the mcp-family advance) also depends on `anyio` and so
  also reaches `idna`. `urllib3` reaches devbench only via `botocore`, which
  is not beneath `mcp`. In both cases the locked `idna 3.11` and
  `urllib3 2.6.3` already satisfied every lower bound declared anywhere in
  the resolved graph, so `uv lock --upgrade-package <mcp-family-target>`
  had no unsatisfied constraint to pull a newer `idna` or `urllib3` in; a
  targeted per-package upgrade of the mcp family simply never touches a
  transitive dependency whose currently-locked version already clears every
  floor. E6-F1-S1-T1's checker run captured both as
  `idna 3.11 < 3.15 NEEDS BUMP` and `urllib3 2.6.3 < 2.7.0 NEEDS BUMP`,
  reflecting the FR-6.3 target floor rather than a graph-resolution
  constraint. This task closes both with a single resolution:
  `uv lock --upgrade-package idna
  --upgrade-package urllib3` resolved cleanly on the first attempt (no
  per-target isolation was needed), moving `idna 3.11 -> 3.18` and `urllib3
  2.6.3 -> 2.7.0`. The `uv.lock` diff is exactly the two packages' version,
  sdist and wheel hash blocks -- no unrelated drift. The re-run checker shows
  all eight targets `SATISFIED`, closing the reconciliation E6-F1-S1-T1
  started, and `uv sync && make validate` exits 0 (6226 passed, 98.24%
  coverage), closing the dependency wave (AC-82). `.github/BATCH_PR_BODY.md`
  is added carrying the closing-keyword list for every issue this run
  resolves plus non-closing references to the six superseded dependabot PRs
  and the two bumped ones (#216, #179), for the operator to paste into the
  deferred single batch PR (spec S9, AC-83).

### Fixed

- **`devbench instances` still reported no running orchestrator for a daemon
  whose workspace lived outside `$HOME`** (`spec/devbench-observability-hardening.md`
  FR-D2/OAC-3, issue #270's companion defect D2, E7-F2-S1-T1).
  `_resolve_search_roots` (`src/devbench/instances.py:140-168`) defaulted to
  `[Path.home()]` alone whenever `DEVBENCH_INSTANCE_SEARCH_ROOTS` was unset, so
  a daemon started under a workspace outside `$HOME` (for example one checked
  out under `/workspaces`) was invisible to `devbench instances` unless the
  operator remembered to export the search-roots override by hand. The
  default now also includes the current `DEVBENCH_WORKSPACE_ROOT` (when set
  and not already under `$HOME`), so a workspace's own daemon is discoverable
  with no configuration; a workspace that already relies on
  `DEVBENCH_INSTANCE_SEARCH_ROOTS` sees byte-identical behavior, since that
  override still wins first and is returned verbatim. `docs/cli-reference.md`'s
  "Instances (per-host discovery)" section documents the three-tier
  resolution order.

- **`quota_handling.log_structured_events: false` had no effect: every
  `[QUOTA_*]` structured marker still emitted unconditionally** (spec
  Section 4 FR-2, AC-16, E9-F1-S2-T1). The config key was parsed, schema
  documented, and docstring-promised but consumed by zero call sites. A new
  `_quota_structured_events_enabled()` helper in `src/devbench/cli.py` reads
  `RUNTIME_CONFIG.quota_handling.log_structured_events` and now gates every
  structured-marker emission across `_handle_quota_pause`,
  `_dispatch_quota_detection`, `_dispatch_quota_timeout`, and the resume
  loop; `wait_for_reset` / `_wait_toward_reset` in `src/devbench/quota.py`
  gain a keyword-only `emit_structured_events: bool = True` gating the
  `[QUOTA_POLLING]` heartbeat, threaded from config at the `cli.py` call
  sites (`quota.py` still imports no config module). Slack notifications,
  audit comments, non-marker log lines, and checkpoint writes are explicitly
  NOT gated (decision D-10: markers only). Default `true` preserves
  byte-identical behavior for every workspace that has not set the flag.
  `docs/quota-handling.md` documents which markers are gated.

- **`Recent pace (last N tasks)`, `Average time per task`, and the ETA
  projection they feed sampled claim-to-done idle wall time as if it were
  execution time** (issue #326). A completion whose only `in-progress`
  anchor sat idle across an orchestrator session gap -- for example an
  operator `set-status <id> done` against a claim made in an earlier
  session -- was timed from that stale claim through to `done`, so the
  pace and average estimators (and the ETA/cost projections derived from
  them) could be skewed by wall-clock idle time that was never execution
  time. `_recent_pace_minutes` and `_compute_window_stats`
  (`src/devbench/reporting/report.py`) now accept a completion as a valid
  sample only when its `in-progress` anchor exists AND falls in the same
  orchestrator session as `done` (`_same_session`, session boundaries
  derived from the log's own non-noise timestamps): pace, average, and ETA
  no longer sample claim-to-done idle wall time. Both estimators now
  compute the MEDIAN of the resulting same-session execution-time samples
  instead of the arithmetic mean, so a single cross-session or outlier
  completion can no longer dominate the estimate. Completions dropped for
  having no execution window are no longer silently narrowed out of the
  sample count: the `Average time per task` and `Recent pace (last N
  tasks)` cells, and the trailing summary line when it drives the
  sentence, append `(<k> excluded: no execution window)` naming how many
  were dropped. `docs/cli-reference.md`'s ETA-formula note documents the
  median estimator and the exclusion suffix.

## [0.3.0] -- 2026-07-31

### Fixed

- **Every orchestrator start re-materialised a consumed proposal and recreated
  a duplicate work unit** (issue #302). `_find_draft_file` looked in exactly
  one directory, the story directory computed from the ID. A work unit living
  anywhere else read as absent, so `classify_proposed_task` reported it
  `UNMATERIALISED` and the orchestrate loop's opening `sweep-proposals`
  created it again in the canonical location, leaving two files and two index
  rows under one ID. Recovering cost roughly four minutes of orchestrator
  turns before any work unit was claimed, and it repeated on every start. A
  work-unit ID identifies one unit wherever its file sits, so the lookup now
  searches the backlog tree and refuses, rather than picking arbitrarily, when
  two files carry one ID. Separately, `sweep-proposals` now deletes a proposal
  once every task in it is resolved; leaving the JSON on disk also pinned the
  source task to `AWAITING_AMENDMENT_RECOVERY` indefinitely.

- **Quoting a `[BLOCKED_PENDING_PROPOSAL]` token inside a comment created a
  live marker** (issue #304). The scanner matched the token anywhere in the
  `## Comments` body, so an audit comment recording that a marker had been
  removed, quoting the removed line verbatim, silently re-blocked the unit on
  the quoted ID. The file read as correct to a human, because the only
  occurrence sat inside quotation marks, and agents write such narratives
  routinely. Both writers emit the marker as the final token of an audit row,
  so the pattern is now anchored there: writing *about* a marker no longer
  creates one.

- **An unscoped session wrote a `scope.json` its own readers reject** (issue
  #270; `spec/devbench-observability-hardening.md` FR-D1/OAC-1/OAC-2, defect
  D1). Session startup wrote a bare JSON array of IDs while
  `ScopeFilter.from_file` and `_read_scope_payload` require the canonical
  object. The two writers target the same path, because `resolve_scope_file_path`
  routes there whenever `DEVBENCH_SESSION_NAME` is set, so the array overwrote
  the object and every subsequent read raised
  `scope.json top-level payload must be an object, got 'list'`. Scoped
  sessions now write the canonical payload; unscoped sessions write no file,
  since absent is how every reader already expresses "no scope", and an empty
  scope would assert a filter matching nothing; a stale array file present at
  an unscoped session's start is still cleared at that point. Separately
  (E7-F1-S1-T1), a stale list-shaped file encountered at ANY other read --
  `ScopeFilter.from_file` and `_read_scope_banner_data` alike, so both
  `devbench next` and `devbench status` are covered -- is now migrated to the
  canonical object form in place (atomic rewrite, one INFO line naming the
  file) instead of raising; a second read of the same file proves the
  migration does not recur. Any other non-object shape still raises the
  pre-existing `ValueError` with its message text byte-preserved.

- **`devbench status` crashed with a traceback where `report` diagnosed**
  (issue #305). A missing work-unit file or a malformed index escaped `status`
  as a raw `FileNotFoundError` while `report` reported the same condition with
  an actionable message and a non-zero exit. An operator running both saw a
  crash and a clean error for one underlying state. The handler is now shared,
  so the two cannot drift apart, and it still distinguishes a missing file
  (possibly a transient writer-window race; re-run) from a malformed index
  (run `validate-backlog`).

- **The test suite wrote into the live workspace and orchestrator log**
  (issue #292; `spec/devbench-observability-hardening.md` FR-D4/OAC-5,
  defect D4). `tests/conftest.py` set `DEVBENCH_WORKSPACE_ROOT`,
  `DEVBENCH_PROJECT_ROOT`, `DEVBENCH_LOG_FILE` and `DEVBENCH_CONFIG_PATH`
  with `os.environ.setdefault`, so it INHERITED whatever the ambient shell
  already had. devbench is developed with devbench, so the executor runs the
  suite from inside a live workspace with those exported: fixture work-unit
  state landed in the real `.devbench/ci-failures/` and
  `.devbench/pr-bot-feedback/` under IDs that exist only in `tests/`, and
  fabricated lifecycle records were appended to the live log --
  `[ORCHESTRATOR_TERMINAL_EXIT]`, `[QUOTA_WAITING]`,
  `[ORCHESTRATOR_AUTO_RESTART]`, `Merged PR #42` -- for events that never
  happened. Those are exactly the markers the reporting layer parses, so a
  test run could drive an operator's `status` and `report` output. Assignment
  is now unconditional (force-assign, not `setdefault`), to a fresh per-run
  temporary workspace; an ambient value is the hazard, not a configuration to
  honour. Verified against a live workspace: the suite leaves a 180 MB
  orchestrator log byte-identical and the 197-file `.devbench/` tree
  unchanged. `tests/test_workspace_isolation.py` pins the isolation itself:
  a child pytest subprocess launched with a decoy live-like workspace
  exported proves the decoy log gains zero bytes and its `.devbench/`
  directory stays empty.

- **A spent executor retry budget was reported as "operator does nothing"**
  (issue #248). The orchestrate skill writes its retry-exhaustion `[BLOCKED]`
  row under `agent/orchestrator` naming the failing checks, which matched both
  the recovery agent-tag allowlist and the recovery body pattern (it includes
  `ALL_REVIEWS_FAILED` / `REVIEW_REJECTED`). The unit was therefore classified
  `AWAITING_AMENDMENT_RECOVERY`, whose contract is that the operator does
  nothing, while no further executor run was coming. Nothing cleared it, no
  operator notification fired, and the run stalled silently. The skill now
  emits an explicit `[RETRY_BUDGET_EXHAUSTED]` tag on genuine exhaustion and
  the classifier returns `OPERATOR_ACTION_REQUIRED` for it. A live
  `[BLOCKED_PENDING_PROPOSAL]` cascade still wins, because that genuinely will
  clear the unit.

- **The in-progress timer under-reported how long a work unit had been
  running** (issue #293). The pattern allowed `.*` between the log timestamp
  and the phrase `Set <id> to 'in-progress'`, so it matched lines that merely
  quoted the transition. The orchestrator logs whole SDK messages, and a tool
  result that read a work unit's `[WU_CLAIMED]` audit comment reproduces that
  phrase inside a line stamped with the time of the dump; being later, the
  echo won the `max()`. Observed: a unit claimed at 12:11 reported as claimed
  at 12:38, and the error grew with every further echo, so a long-running unit
  could keep resetting toward "just started". The pattern is now anchored to
  the emitting logger and level.

- **`validate-backlog` passed on duplicate work-unit IDs** (issue #291). One
  unit written into two directory trees yields two index rows under one ID.
  Every existing check passed -- both files exist, each matches its own row's
  status, and neither is orphaned because both are indexed -- while the rows
  disagreed about status (`done` in one tree, `declined` in the other) and a
  dependency on that ID resolved against whichever row was reached first. New
  check 21 reports every ID carrying more than one index row, naming each
  status and path. Status Summary rows, which repeat an ID without a File
  Path, are not index rows and are excluded.

- **`devbench report` did not say whether the run could proceed** (issue
  #251). `status` ended with `Next actionable` / `All work units are DONE.` /
  `No actionable units. N blocked.`; `report` ended with none of them, and its
  per-status counts cannot substitute -- a backlog can hold many `in-queue`
  units with nothing actionable, because only leaf Tasks execute and every one
  may be waiting on a dependency. Both commands now render the same line from
  one shared helper. `devbench next` keeps its machine tokens, which are a
  contract consumed by the orchestrate skill.

- **The orchestrator-alive banner reported ALIVE with no orchestrator
  running** (issue #250; `spec/devbench-observability-hardening.md`
  FR-D3/OAC-4, defect D3). Liveness was derived from log-activity recency
  alone, but a recent log line proves only that something wrote to the log,
  not that the writer still exists: a crashed or killed daemon read as ALIVE
  for the whole quiet window, and any other writer kept it ALIVE indefinitely.
  The banner now reads the workspace PID file and checks the process table,
  and reports five states (ALIVE, with an idle variant; STOPPED; STARTING;
  NOT RUNNING; UNKNOWN). `stop_hook.window_seconds` no longer decides
  liveness; it distinguishes a busy live orchestrator from an idle one, so a
  running-but-quiet orchestrator is never reported STOPPED. Per obs-spec
  decision OD-3, this fix owns the verdict only; the "last activity" recency
  line still reads whichever log `devbench report` resolved for the
  invocation -- the workspace's aggregate `logs/orchestrator.log` by
  default, or the named session's log when `--session <name>` is passed
  (E7-F3-S1-T1, declined as superseded: PR #295 already carried this fix
  before the task reached the queue).

- **`devbench start --help` omitted the scope-filter flags** (issue #249).
  `--include`, `--exclude`, `--name` and `--allow-overlap` were accepted by the
  parser and documented nowhere, so the only way to discover them was to read
  the parser. The registry description was also the single source for both the
  one-line command list and per-command help, leaving no room for flag
  documentation. A description may now span lines: the command list shows the
  first line, and `<cmd> --help` prints the whole text.

- **A blocked work unit's uncommitted changes contaminated every unit that
  claimed after it, and now get quarantined instead.** The single-branch modes run every work unit in one shared
  checkout. When a unit blocked before its work was committed, that work stayed
  in the tree, and nothing cleared it or reported it. The next unit to claim
  inherited it, with two distinct failure modes observed in one run: a unit's
  judges rejected it for staged files belonging to a blocked sibling, and a
  docs-only unit was blocked by a security review whose every finding cited a
  blocked sibling's unstaged source, which the docs-only unit neither owned nor
  was permitted to touch. Neither unit could act on the rejection, and the
  residue survived to contaminate the unit after that. `devbench claim` now
  refuses, before any executor or judge time is spent, when the target checkout
  holds uncommitted changes outside the claiming unit's Changes Manifest, naming
  every offending path so the residue is attributed to the unit that produced
  it. The check covers staged, unstaged, and untracked-but-not-gitignored paths;
  `assert_staged_matches_manifest` sees only the staged set, which is why a unit
  that blocked before staging read as a clean tree. Re-claiming an `in-progress`
  unit is unaffected: its own manifest files are never quarantined.

  devbench runs unattended, so the residue is moved rather than reported. Each
  foreign path is stashed under the ID of the unit whose Changes Manifest
  declares it, and the claim proceeds against a checkout holding only the
  claiming unit's scope. Stopping to ask an operator would turn one blocked
  unit into a stopped run. Quarantine is non-destructive: each entry is a
  normal git stash titled `devbench-quarantine:<owner-id>`, one per owning
  unit, recoverable with `git stash list` / `git stash apply`, and the owning
  unit receives a `[WORK_QUARANTINED]` audit comment naming it. Paths no unit
  declares are quarantined under an `unattributed` key, since they would
  corrupt the claiming unit's commit just the same. Nothing is restored
  automatically: a blocked unit re-executes from its Changes Manifest when it
  unblocks, and re-injecting a superseded attempt would recreate the
  contamination.

  **Behaviour change:** `devbench claim` exits non-zero only when the
  quarantine itself fails or leaves residue behind, which means the checkout
  was not actually cleared. When the unit's repo has no configured local
  checkout there is no shared tree to guard; the step logs that it was skipped
  rather than passing over it silently.

- **A work unit's commit could absorb another unit's unstaged changes.** Both
  commit paths ran `git add -A`, staging the entire working tree and committing
  it under the current unit's message, so any file another in-flight unit had
  left modified-but-unstaged was swept in. The guard meant to prevent exactly
  this could not see it: `assert_staged_matches_manifest` reads
  `git diff --cached`, which by definition excludes unstaged changes, so it
  verified the index, passed, and then `add -A` staged everything the check had
  just ignored. The victim task was then unrecoverable -- its declared files
  committed under another unit's name, failing `changes_manifest` permanently,
  with no remedy short of an operator override or rewriting published history on
  a shared branch. Both entry points now stage the Manifest paths the callers
  already parse for the scope check. There is no degraded mode: a caller that
  cannot resolve a Manifest, or whose Manifest holds only execution-time
  sentinels, is refused rather than silently given a whole-tree commit.
  `git-ops-finalize`, which batches many units and legitimately has no single
  Manifest, opts into whole-tree staging explicitly via `stage_all`.

  **Behaviour change:** `devbench git-ops` now exits non-zero when it cannot
  resolve the work unit's file. Previously it warned and committed anyway. The
  post-commit audit-comment write remains best-effort and never fails the run.

## [0.2.0] -- 2026-07-29

This release bundles the orchestrator self-healing work, the canonical
configuration refactor, the EC2 remote-dev provisioning stack, and the
work-unit lifecycle / authoring CLI improvements that have accumulated
since the last release. PR #119 carries every change.

### Fixed

- **Recovery tasks were materialised but never wired.** The `write-proposal`
  auto-cascade skipped `promote_proposal` for any draft not in `proposed` state.
  Under `backlog.default_status_for_new_work_units: in-queue` every fresh draft is
  born promoted, so the guard skipped it -- and with it the dependency row and the
  `[BLOCKED_PENDING_PROPOSAL]` marker the auto-unblock cascade reads. The source
  task stayed blocked indefinitely with nothing naming what it waited for. Drafts
  materialised by the current call are now always wired.
- **Consumed proposals were never deleted.** `delete_proposal` existed but had no
  caller. Because the recovery classifier tests for the proposal file's presence
  alone, a spent proposal pinned its source task to `AWAITING_AMENDMENT_RECOVERY`
  permanently -- reported as self-healing, so never surfaced as
  `OPERATOR_ACTION_REQUIRED` -- while `write_proposal` refused to emit a
  replacement. The proposal is now dropped once every proposed task has been
  resolved.
- **Materialised tasks landed in an orphan directory tree.** The story-directory
  helper derived its path from bare IDs (`backlog/E4/E4-F1/E4-F1-S1`), but
  `spec-to-backlog` names every level `<id>-<slug>`, so recovery tasks were written
  into a parallel tree with no Epic / Feature / Story work-unit files. That hid
  existing siblings from the story scan and could make ID allocation return an ID
  that already existed, overwriting a live task. An existing directory now always
  wins, preferring the one that holds the Story's own work-unit file.

### Changed (BREAKING)

- **devbench.yaml default changes.** Several built-in defaults changed; workspaces
  that omit these keys get the new behaviour:
  - `manifest_amendment.enabled` now defaults **true** (was `false`) -- the Changes
    Manifest amendment workflow is active unless explicitly disabled.
  - `validate.check_orphan_path_tokens` now defaults **true** (was `false`) --
    `validate-backlog` runs Rule 20 (AC/DoD path-coherence) by default. Set `false`
    to opt out if a pre-existing backlog is not yet compatible.
  - `task_factory.enabled` now defaults **true** (was `false`) and
    `task_factory.auto_accept_proposals` now defaults **false** (was `true`
    as shipped in released tag `0.1.0` via PR #202) -- both flipped together
    in the same commit per D-11 (issue #259, ADR-32) so the on-by-default
    loop never grants an unreviewed orphan-auto-promote path. Relative to
    `0.1.0` the user-visible change is that the loop now runs for every
    backlog by default; a freshly materialised draft's initial status was,
    and remains, `backlog.default_status_for_new_work_units` (default
    `in-queue`, AC-189-8) regardless of `auto_accept_proposals` -- that flag
    instead governs two auto-promote paths: `write-proposal` itself no
    longer synchronously materialises-and-promotes the proposal it just
    wrote inside the same invocation by default (that cascade only fires
    when `auto_accept_proposals` is explicitly `true`), so a freshly
    written proposal now waits for the next `sweep-proposals` tick to
    become actionable; and `sweep-proposals` no longer auto-promotes a
    draft explicitly left at `## Status: proposed` (a legacy/hand-edited-
    draft case, not something the normal materialise path produces) unless
    the flag is set. Existing backlogs that explicitly disabled
    `manifest_amendment` and never mentioned `task_factory` are unaffected:
    the defaulted-on `task_factory.enabled` downgrades to disabled rather
    than failing config-load in that combination (see ADR-32's interaction
    contract). See the migration note below for the exact keys to restore
    each pre-flip behavior.
  - `merge_strategy` default is now explicitly `squash` at the config layer.
  - `timeouts.executor` and `timeouts.executor_max_turns` were **removed** -- they
    were parsed but never consumed (dead config); removing them changes no behaviour.

- **Plugin split: `devbench@devbench` retired in favour of two
  marketplaces / two plugins** (issue #224). The single plugin had a
  structural conflict between the authoring audience
  (`spec-to-backlog`, which writes `backlog/*.md`) and the executor
  audience (the `guard-work-unit-write.sh` hook blocks any write to
  `backlog/*.md`). The split encodes the policy at the plugin-
  installation layer:
  - **`devbench-orchestrate`** (orchestrate marketplace, this repo's
    `plugin/`) -- ships the orchestrate skill + every agent + every
    PreToolUse guard hook. Install in a workspace where you run
    autonomous execution.
  - **`devbench-authoring`** (authoring marketplace, this repo's
    `plugin-authoring/`) -- ships `spec-to-backlog`, `create-spec`,
    `configure-devbench`, `bootstrap-environment`. No hooks; safe to
    enable in any authoring workspace.

  See `docs/migration-0.4.0.md` for the operator migration walkthrough.
  Note: prior CHANGELOG entries that reference `plugin/devbench/`
  paths describe the pre-split layout and are not rewritten.

  **Source-tree changes** (paths relative to repo root):
  - `plugin/devbench/` -> `plugin/devbench-orchestrate/` (renamed;
    four authoring skills removed from this side).
  - `plugin-authoring/devbench-authoring/` (new; receives the four
    authoring skills + a fresh `.claude-plugin/plugin.json`).
  - `plugin/.claude-plugin/marketplace.json` -- orchestrate
    marketplace, version `0.2.0` -> `0.3.0`; lists one plugin
    `devbench-orchestrate@0.4.0` at `./devbench-orchestrate/`.
  - `plugin-authoring/.claude-plugin/marketplace.json` (NEW) --
    authoring marketplace, version `0.1.0`; lists one plugin
    `devbench-authoring@0.1.0` at `./devbench-authoring/`.

  **Sub-agent invocation prefix changed** (every reference inside
  the orchestrate plugin): `devbench:executor` ->
  `devbench-orchestrate:executor`, same for `blocker-resolver`,
  `task-factory`, `manifest-amender`, `security-reviewer`,
  `review-supervisor`, `code_review`, `test_review`, `doc_review`,
  `changes_manifest`, `orchestrate`. The `guard-review-supervisor-
  scope.sh` and `guard-verdict-format.sh` scripts now check the
  new prefix.

  **Narrow Python source edits** (AC-12 deviation, called out for
  audit):
  - `src/devbench/constants.py`: `DEFAULT_PLUGIN_SUBPATH` flipped
    from `"plugin/devbench"` to `"plugin/devbench-orchestrate"`.
  - `src/devbench/cli.py`: the SDK launch prompt that invokes the
    orchestrate skill now reads `"Run the
    devbench-orchestrate:orchestrate skill..."`.

  These two are the minimum required for non-interactive
  `devbench start --daemon` runs to find the renamed plugin tree.
  No agent prompt, no hook script, and no other Python source is
  changed.

  **New regression tests** pin the orchestrate side's behaviour
  (`tests/test_plugin/test_orchestrate_isolation.py`,
  `test_executor_guard_unchanged.py`,
  `test_work_unit_write_block_message.py`). Any future drift in
  the PreToolUse hook list, the guard-work-unit-write stderr
  format, or the orchestrate plugin's self-containedness fails
  these tests locally before push.

### Added

- **`git_ops.branch_prefix` / per-repo `branch_prefix` task-branch
  namespacing** (issue #283). Task branches were always named
  `backlog/<unit-id-lower>` with no workspace identifier. When two
  independent devbench workspaces (each numbering its own backlog from
  `E1-F1-S1-T1`) target the same downstream repo, their task branches
  collide by name -- a run hit this live: workspace A's already-merged
  `backlog/e1-f1-s1-t1` PR blocked workspace B's unrelated `E1-F1-S1-T1`
  push with a non-fast-forward rejection, since it was a different task
  that happened to share an ID, not a real merge conflict. Setting
  `git_ops.branch_prefix` (optionally overridden per-repo via
  `repos.<org/repo>.branch_prefix`) namespaces per-unit branches as
  `backlog/<prefix>/<unit-id-lower>` and `single_branch` as
  `<prefix>/<single_branch>`, eliminating the collision in both modes.
  Unset by default -- existing single-workspace setups are unaffected.
  See `docs/devbench-yaml-reference.md`.

- **`spec-to-backlog` append-mode `BACKLOG.md` regeneration**
  (issue #225). Materialising a new spec on top of an existing
  populated backlog (E17+ on top of E1-E16) previously required the
  operator to merge `BACKLOG.md` by hand because Step 6 of the
  skill rewrote the file from scratch. The new
  `regenerate_backlog_index` post-processor pass appends new epic
  rows to the Status Summary table and new work-unit rows to the
  Full Work Unit Index; existing rows are byte-for-byte preserved.
  When a new epic ID collides with an existing index row at a
  different file path the pass raises
  `BacklogAppendCollisionError` (fail-fast). Greenfield invocations
  fall back to the skill's existing write path.

- **Code Standards block templater** (issue #230). The
  `spec-to-backlog` skill no longer needs to re-type the ~50-line
  `### Code Standards` block per task. A new
  `devbench.plugin_helpers.code_standards_template.emit_code_standards_block`
  helper emits the canonical block with three placeholder
  substitutions (`<WORKSPACE_CLAUDE_MD>`,
  `<TASK_SPECIFIC_ERROR_PATHS>`, `<REPO_CARVE_OUTS>`). Workspaces
  override the default body by placing
  `code-standards-canonical.md` at the workspace root. The
  companion `verify_code_standards_canonical` post-processor pass
  reports the count of tasks whose Code Standards block has drifted
  (check-only; does NOT mutate). See `docs/code-standards-canonical.md`.

- **`devbench report --by-role` per-role token/cost breakdown panel**
  (issue #206). Wires the data path landed in PR #202 (issue #123 via
  ``_parse_transcript_metrics_by_role``) into the rendered output of
  ``devbench report``. Off by default; without the flag the output is
  unchanged. With ``--by-role``, a Per-role cost breakdown panel
  renders beneath the existing aggregate Cost section, listing
  ``executor``, ``code_review``, ``test_review``, ``doc_review``,
  ``changes_manifest``, ``security_review``, ``blocker_resolver``,
  ``manifest_amender``, ``task_factory``, ``orchestrator`` rows
  (each present only when that role has activity in the window) with
  input/output/cache-read/cache-write tokens, message count, and
  est_cost.  The TOTAL row equals the sum of the per-role rows -- a
  render-time invariant pinned by the regression test.  Per-role and
  per-model (issue #223) are orthogonal axes available independently;
  the per-model rate table prices each role's tokens at the model
  that actually produced them.
- **Per-model token pricing in `devbench report`** (issue #223). The
  retired single-rate model is replaced by a per-model rate table under
  ``report.models`` in ``backlog/config/devbench.yaml``. Every key is a
  Claude model id (the literal ``message.model`` value Claude Code
  records on every ``assistant`` envelope, e.g. ``claude-opus-4-7``);
  every value is a ``{input, output, [cache_read_multiplier],
  [cache_write_5min_multiplier], [cache_write_1hr_multiplier],
  [correction_factor]}`` block. ``report.default_model`` is applied to
  any model id not present in ``report.models`` and to entries with no
  model attribution (NULL rows in the SQL cache aggregate under the
  ``"<unknown>"`` sentinel bucket). Schema enforces
  ``additionalProperties: false`` on each model entry while leaving the
  model-id KEY open (any string), so operators add new models without a
  code change. Default rate table in ``src/devbench/constants.py``
  mirrors the canonical Anthropic Standard pricing table verbatim;
  ``sample-config.yaml`` ships the same table as a starter block. New
  module-level constants ``REPORT_MODEL_RATES`` and
  ``REPORT_DEFAULT_MODEL_RATES`` expose the merged view at runtime.
  ``docs/model-pricing.md`` rewritten; new ``docs/cost-accuracy.md``
  documents the attribution chain (transcript -> SQL -> aggregator ->
  cost) and how to audit it.
- **Per-call model attribution end-to-end** (issue #223 phase 1).
  ``hook_entries`` and ``transcript_entries`` SQL tables gain a
  ``model TEXT`` column populated at parse time from
  ``tool_response.model`` and ``message.model`` respectively. NULL
  rows aggregate under the sentinel key ``"<unknown>"``. Schema bumped
  to v4; pre-v4 caches are dropped + rebuilt at next open via the
  existing version-mismatch handler. New aggregators
  ``aggregate_hook_window_by_model`` and
  ``aggregate_transcript_window_by_model`` return
  ``dict[str, dict[str, int]]`` (``model_id -> totals_dict``); their
  per-bucket totals roll up to the single-bucket aggregator's result
  so no tokens are silently dropped. New per-model dispatcher
  ``_compute_cost_by_model`` in ``reporting/report.py`` iterates
  ``{model_id -> HookLogTotals}``, prices each bucket against its own
  rates via the existing ``_compute_cost``, and composes the per-model
  ``correction_factor`` AFTER all other factors. Both call sites of
  ``_compute_cost`` (in-window cost and the ``_recent_per_task_cost``
  projection) now feed the new dispatcher.
- **`devbench cost-calibrate <actual-usd> [--window <ISO-8601>]`**
  (issue #223 phase 3). Operator-facing calibration command: sums
  devbench's reported per-model cost across the window, derives
  ``correction_factor = actual_usd / reported_total``, and writes the
  factor back to ``report.models.<id>.correction_factor`` in
  ``backlog/config/devbench.yaml`` for every model that contributed.
  Successive calibrations replace (not multiply) the prior factor so
  re-running is idempotent against a fixed actual-spend figure. Writes
  ``input`` and ``output`` from the canonical default rate table when
  the operator's yaml does not yet list a model the calibration
  observed (the resulting yaml is immediately schema-valid). YAML
  round-trip via ``yaml.safe_load`` + ``yaml.safe_dump``; atomic
  tmp+rename write so an interrupted calibrate does not corrupt the
  config. Reuses the per-model attribution from phase 1, so the
  command works against any ``feat/issues-188-193`` workspace whose
  cache has been refreshed.
- **Discovery-artifact coverage rubric for `spec-to-backlog`** (issue
  #221 A1). ``plugin/devbench/skills/spec-to-backlog/SKILL.md`` Step 2
  now accepts an optional second positional argument
  ``discovery_artifacts_dir`` pointing at a directory of discovery
  artefacts (typically ``spec/<run>/_workspace/``) that the spec was
  authored from. Recognised artefact filenames:
  ``verification_matrix.md``, ``ci_failures.md``,
  ``test_coverage_audit.md``, ``ambiguities.md``, ``scope_creep.md``.
  When supplied, Step 4b adds a new mandatory rubric item ("every row
  in every recognised artefact file must be covered by at least one
  leaf task") and Step 5b adds a per-task counterpart asserting the
  covering task explicitly cites the artefact row in its AC or
  Approach. Step 8 emits a new
  ``[DISCOVERY_COVERAGE] <covered>/<total>`` audit line so the audit
  trail records the coverage check having run. Skipped silently when
  ``discovery_artifacts_dir`` is absent -- no behavioural change for
  legacy invocations. The orthogonal spec-AC -> leaf-task rubric item
  (Step 4b item 4) is unchanged; this new rubric is the safety net for
  rows the spec author may have omitted an AC for.
- **Per-task checkpoint API for resumable spec-to-backlog runs** (issue
  #221 A3). New ``PerTaskCheckpoint`` dataclass plus
  ``read_per_task_checkpoint`` / ``write_per_task_checkpoint`` helpers
  in ``src/devbench/skill_state.py``. Persists the set of leaf-task IDs
  already authored to ``<workspace>/.devbench/skill-state/
  spec-to-backlog-tasks.json`` (alongside but separate from the
  iteration-counter file). When the skill is re-invoked after an
  interrupted run, it reads the checkpoint and resumes from the first
  un-completed task instead of regenerating every file. Atomic-write
  contract identical to the existing checkpoint API (tmp + rename, no
  partial reads). 100% coverage in ``tests/test_skill_state.py``.
- **Skill arg parsing for spec-to-backlog** (issue #221 A2).
  ``spec-to-backlog/SKILL.md`` Step 2 now accepts the spec path via
  ``args`` and only prompts when no arg was supplied -- the orchestrator
  can dispatch the skill non-interactively as part of a longer chain.
- **AC N/A-suffix grammar documented** (issue #221 D3). New section in
  ``docs/acceptance-criteria-canonical.md`` enumerates the
  case-insensitive accepted variants (mixed case OK; structure is what
  matters) and the things that must NOT change (``--`` double-dash
  sentinel, literal ``N/A`` token, literal ``Tasks`` plural, trailing
  parenthesised reason). Catches authors who write ``- N/A``,
  ``NA without slash``, or omit the parenthesised reason.

- **Backlog post-processor module for spec-to-backlog skill** (issue
  #221 A11, A12, A13). New ``src/devbench/plugin_helpers/`` package
  with ``backlog_post_processor.py`` exposing three deterministic
  passes the LLM-driven ``spec-to-backlog`` skill invokes between task
  authoring (Step 5) and validate-backlog (Step 5d):
  - ``sanitize_markdown_pipes_in_manifest`` escapes raw ``|`` inside
    Manifest annotation cells.
  - ``dedupe_manifest_rows`` collapses identical Manifest rows to one.
  - ``suffix_ref_on_orphan_paths`` adds ``(ref)`` after backtick-quoted
    path tokens in AC / DoD prose that are not in the Manifest.
  All passes are idempotent. The convenience entry point
  ``run_all(backlog_dir)`` returns a ``{pass_name: count}`` mapping
  the skill emits as ``[POST_PROCESS]`` audit rows. New docs page
  ``docs/skills/backlog-post-processor.md`` documents the module
  contract and the pattern for adding future passes. The skill's
  ``spec-to-backlog/SKILL.md`` Step 5d now invokes the post-processor
  before each ``validate-backlog`` call so the backlog lands green on
  first try instead of requiring an operator-facing fix loop.

- **Application-agnostic `spec-to-backlog` and `create-spec` skills**
  (issue #221 E1-E10). Both bundled SKILL.md files no longer hardcode
  the ``/workspaces/rpm-migration/kanon-deps-work/...`` exemplar path.
  Each skill now:
  - Resolves an optional workspace exemplar from a new ``skills:``
    YAML section (``skills.exemplar_backlog_path`` and
    ``skills.exemplar_spec_path``). Absent / non-existent paths cause
    the skill to skip the read entirely and rely on the canonical-
    section list embedded in the SKILL.md as the authoritative quality
    bar.
  - Enumerates the canonical structure (15 task-file sections for
    spec-to-backlog; 16 spec sections for create-spec) directly inside
    the SKILL.md so a workspace with no exemplar still produces a
    valid artefact.
  - Emits ``[QUALITY_REFERENCE] <resolved-path>`` on success when an
    exemplar was configured, or
    ``[QUALITY_REFERENCE] <embedded-canonical-sections>`` when one was
    not (the audit trail records what was consulted).
  - Reads ``skills.fan_out_threshold`` (default 10) and
    ``skills.max_iterations`` (default 5) for its parallel-authoring
    and convergence-budget knobs. New ``SkillsConfig`` dataclass +
    schema block + ``_parse_skills_config`` runtime validation +
    ``sample-config.yaml`` entry. New docs page
    ``docs/skills/exemplar-reference.md`` documents the
    resolution-order and provenance contract.

### Fixed

- **SDK teardown race downgraded to WARNING with tracking-issue link**
  (issue #232; tracks upstream #231). `claude-agent-sdk`'s
  `Query.close()` raises
  `RuntimeError: Attempted to exit cancel scope in a different task
  than it was entered in` on every successful session teardown.
  Surfaced as `[asyncio] ERROR Task exception was never retrieved`
  AFTER `[ORCHESTRATOR_TERMINAL_EXIT]`, the trace caused remote
  execution environments to mis-classify a successful orchestrator
  run as failed (any stderr `ERROR` line tripped their pipeline). A
  narrow asyncio exception-handler filter in the new
  `devbench.sdk_teardown_filter` module intercepts the EXACT known
  signature (RuntimeError class + verbatim marker substring + a
  traceback frame in `claude_agent_sdk/_internal/*`) and logs a single
  WARNING on the `devbench.sdk` logger that references devbench#231.
  Anything that does NOT match the signature falls through to the
  default handler and surfaces at ERROR. Full traceback is preserved
  at DEBUG. The filter is installed via an async context manager
  (`sdk_teardown_filter.guard()`) wrapped around the SDK query loop in
  `cmd_start`, so the previous handler is restored on every exit
  path. Filed upstream as
  [anthropics/claude-agent-sdk-python#983](https://github.com/anthropics/claude-agent-sdk-python/issues/983);
  the workaround is removed in the same commit that bumps the SDK
  pin once that lands (devbench#231 stays open until then).

- **`backlog_post_processor` scope + terminal-status guards**
  (issue #226). Materialising a new spec on top of an existing
  populated backlog could silently mutate already-done work units
  because every pass walked the full backlog tree. Every pass now
  accepts an optional `scope_paths=[...]` argument that confines
  the walk to the supplied epic directories, AND defaults to
  skipping any file with `## Status: done` or `## Status: declined`
  (terminal-status guard). The `force_terminal=True` override
  remains available for one-time mass migrations.

- **`spec-to-backlog` Changes Manifest column-format pinning +
  normaliser** (issue #227). The skill prompt now explicitly
  mandates the 2-column `| File | Change |` form (`parse_manifest`
  rejects any other column count). The new
  `normalize_manifest_column_count` post-processor pass collapses
  N-column variants (`| Repo | Path | Action |`,
  `| File | Change | Notes |`, 4-column variants) to the canonical
  form losslessly: when `header[0]` is `Repo`, columns 0+1 merge
  into the File cell as `repo -- path` and column 2+ joins into
  Change with ` -- `.

- **`spec-to-backlog` AC-FINAL N/A tier-suffix auto-fixup**
  (issue #228). The validator's Rule 13 requires Python-tooling
  AC-FINAL lines (002 ruff format, 003 ruff check, 004 mypy, 005
  pytest tier, 006 pytest other tier, 008 bandit, 014 coverage) to
  carry an explicit `-- N/A for <Tier> Tasks (no Python source
  authored)` suffix on tasks whose Changes Manifest contains zero
  `.py` paths. The skill prompt's Step 5b rubric now mandates this,
  and the new `suffix_na_on_non_python_tasks` post-processor pass
  appends the suffix deterministically when missing.

- **`spec-to-backlog` prompt drift from validator** (issue #229).
  Three sub-drifts resolved: Step 1b item 2 (Status) now explicitly
  pins the constraint that `draft` is only valid for Task work
  units (`_check_status_enum` rejects `draft` on Epic / Feature /
  Story); Step 5a now mandates the canonical dep-ID regex
  `E\d+(-F\d+)?(-S\d+)?(-T\d+)?` and forbids directory-slug forms
  (`E16-test-cleanup`); Step 6 Status Summary count semantics now
  match what the validator's `_compute_epic_counts` does (Features
  + Stories + Tasks under the Epic; the Epic file itself is NOT
  counted). The new `normalize_dep_ids` post-processor pass
  rewrites slug-form dep IDs to canonical form when found.

- **`devbench` exits cleanly when a required env var is missing**
  (issue #221 B7). The two import-time required env vars
  (``DEVBENCH_WORKSPACE_ROOT`` and ``DEVBENCH_CLAUDE_MODEL``) used to
  raise ``RuntimeError`` when absent, which fires before
  ``cli.py::main`` is reached and prints a multi-line Python traceback
  to stderr with empty stdout. Operators running ``devbench report``
  with stdout-only redirection (``devbench report > out.txt``) saw the
  empty output as "rc=0, no useful output" -- the exact symptom the
  issue is filed against. ``_require_env`` now writes a single
  actionable line to stderr (``devbench: DEVBENCH_WORKSPACE_ROOT
  environment variable is not set. Set it to the absolute path of your
  workspace root.``) and exits with code 2. Conftest sets both env
  vars, so the test suite is unaffected; the new contract is asserted
  directly in ``tests/test_config.py::TestRequireEnv``.
  ``docs/cli-reference.md`` calls out the required-env-vars contract
  under the ``report`` section so operators don't run into this again.
- **Classifier accepts hyphen-form recovery-agent tags in `[BLOCKED]`
  audit rows** (issue #211). ``_RECOVERY_AGENT_TAGS`` enumerates the
  canonical underscore form (``agent/manifest_amender``), but
  ``amendment.py::AMENDER_AGENT_ID`` and several other writers emit the
  hyphen form (``agent/manifest-amender``). Before the fix the hyphen
  form silently failed the frozenset membership check inside
  ``_recent_recovery_audit_comment``, so ``classify_blocked_task`` fell
  through to ``OPERATOR_ACTION_REQUIRED`` for rejected-amendment audits
  that should have classified as ``AWAITING_AMENDMENT_RECOVERY``. Adds
  a tiny one-way normaliser (``_normalize_agent_tag`` -- ``hyphen ->
  underscore`` inside the ``agent/`` namespace only) that runs before
  the frozenset lookup. New parametrised regression matrix in
  ``tests/test_backlog/test_proposal.py`` covers both forms for every
  recovery agent plus a negative case for non-recovery agents.
- **Manifest entries with glob patterns are rejected** (issue #221 B4).
  A Changes Manifest entry containing ``*`` or ``**`` (e.g.,
  ``src/devbench/**/*.py``) used to silently flow through to
  ``_check_source_test_pairs`` which then emitted a confusing error
  about a missing ``test_*.py`` pair for the glob literal. The
  validator now rejects globs at a dedicated pre-check
  (``_check_no_glob_in_manifest``) with a message pointing at the
  sentinel + ``manifest_amendment`` alternative.
- **Source-test atomicity Update-vs-Add docs** (issue #221 B5). Added
  worked example to ``docs/source-test-atomicity.md`` clarifying that
  an ``Update`` annotation on an existing ``test_*.py`` file satisfies
  Rule 14 the same way an ``Add`` does. The docstring of
  ``_check_source_test_pairs`` references the doc.
- **Status Summary count semantics documented** (issue #221 B6). The
  "In Queue" column counts ALL in-queue work units per epic, not just
  leaf tasks. ``docs/backlog-contract.md`` now explains the count
  formula and shows a worked example so authors building BACKLOG.md by
  hand don't miscount.
- **Manifest parser honours markdown-escaped pipes** (issue #221 B1).
  ``_parse_body`` now treats ``\|`` as a literal pipe inside Changes
  Manifest cells, so prose like ``run cmd output \| grep -v debug`` no
  longer triggers ``ManifestParseError: Manifest row must have exactly
  2 columns``. Genuine 3-column rows (no backslash) still raise.
- **Bare ``.md`` extension is no longer a path token** (issue #221 B2).
  Rule 20 (orphan path) used to flag prose like "only ``.md`` files
  modified" because the 3-char string ends in a known extension.
  ``_is_path_shaped`` now requires a filename stem or directory
  separator before treating a token as a path; bare extensions in
  prose pass through. Real paths (``docs/foo.md``) are still flagged.
- **Sentinel Manifest values are exempt from path-based rules** (issue
  #221 B3). New ``devbench.backlog.sentinels`` module enumerates the
  accepted Manifest sentinel values (``<verification-only>``,
  ``<decision-only>``, ``<no changes>``, ``<no-op>``,
  ``<source-drift-fix-targets-determined-at-execution>``) plus the
  ``<name>``-pattern that catches operator-defined variants. These
  values are no longer treated as "real paths", so multiple tasks
  declaring the same sentinel don't trigger spurious Manifest Conflict
  Rule violations, decision-only tasks don't trigger source-test
  atomicity, and AC prose mentioning ``<verification-only>`` doesn't
  trigger orphan-path detection.

### Breaking changes

- **Removed legacy single-rate token-cost fields** (issue #223).
  ``report.token_cost_per_million_input``,
  ``report.token_cost_per_million_output``, and
  ``report.token_cost_discount`` were retired in favour of per-model
  pricing under ``report.models`` (see Added above). Per CLAUDE.md
  "Complete Replacement of Superseded Code" there is no deprecation
  shim: workspaces that still set the legacy keys get an immediate
  ``ValueError`` at config-load time naming the offending fields and
  pointing at the new block syntax + ``docs/model-pricing.md``.
  Migration: replace the three scalar keys with a ``report.models``
  block (copy the starter block from ``sample-config.yaml`` or the
  Standard pricing table in ``docs/model-pricing.md``); express any
  non-zero ``token_cost_discount`` as a per-model
  ``correction_factor = 1.0 - <old_discount>``, OR run
  ``devbench cost-calibrate <actual-usd>`` once to derive the
  corrected factor from a real invoice. The package-level constants
  ``DEFAULT_TOKEN_COST_PER_M_INPUT``,
  ``DEFAULT_TOKEN_COST_PER_M_OUTPUT``, and
  ``DEFAULT_TOKEN_COST_DISCOUNT`` were removed from
  ``src/devbench/constants.py``; callers consume
  ``DEFAULT_MODEL_RATES`` + ``DEFAULT_FALLBACK_MODEL_RATES`` instead.
  Module-level constants ``TOKEN_COST_PER_M_INPUT``,
  ``TOKEN_COST_PER_M_OUTPUT``, and ``TOKEN_COST_DISCOUNT`` in
  ``src/devbench/config.py`` were removed; consumers read
  ``REPORT_MODEL_RATES`` and ``REPORT_DEFAULT_MODEL_RATES``.

### Added

- **Daemon mode + lifecycle CLI (`start --daemon`, `instances`, `stop`,
  `tail`, `restart`)** (issue #209). `devbench start --daemon` (or `-d`)
  double-forks into the background, redirects stdout/stderr to
  `<workspace>/logs/orchestrator.log`, writes a PID file at
  `<workspace>/.devbench/orchestrator.pid`, prints the instance id, and
  frees the operator's terminal.  Foreground `devbench start` is
  unchanged (still blocks).  Both modes write the PID file so the new
  lifecycle commands can target them by short instance id
  (`<workspace_name>-<pid-suffix>`) or raw PID:
  - `devbench instances [--json]` enumerates every live orchestrator on
    this host by walking `**/.devbench/orchestrator.pid` under operator-
    reachable roots (override via `DEVBENCH_INSTANCE_SEARCH_ROOTS`).
  - `devbench stop <id> [--timeout N] [--force]` sends SIGTERM, waits up
    to 30s by default, escalates to SIGKILL only with `--force`.
  - `devbench tail <id> [--follow|-f] [--lines|-n N]` resolves the
    instance's workspace via its PID file and tails
    `logs/orchestrator.log` (defaults: last 50 lines, no follow).
  - `devbench restart <id>` is a composite stop + start in the same mode
    (daemon vs foreground) and same session.

  New module `src/devbench/instances.py` owns PID-file IO, instance-id
  generation, liveness check (`os.kill(pid, 0)`), discovery, and the
  instance-id / PID resolver.  The PID file is removed on clean exit
  via the existing `try/finally` cleanup in `cmd_start`; stale entries
  (process dead) are filtered out at discovery time.  Pinned by 23
  cases in `tests/test_instances.py` (instance-id format, PID-file
  round-trip, corrupt-payload guards, liveness check, discovery walk,
  env-root override, resolver by id and PID, cleanup).

- **Per-class Slack toggles for every blocked classification + Backlog
  field on every payload** (issue #209). The previous notification
  surface fired only on transition into `OPERATOR_ACTION_REQUIRED`;
  the other six blocked classes (RUNTIME_DEGRADATION, HELD,
  BLOCKED_ON_HELD, AUTO_CLEARING_VIA_PROPOSAL, AWAITING_DEPENDENCY,
  AWAITING_AMENDMENT_RECOVERY) were silent. The
  `notifications.events` block now carries six new toggles --
  `work_unit_blocked_runtime_degradation`, `work_unit_blocked_held`,
  `work_unit_blocked_on_held`, `work_unit_blocked_auto_clearing`,
  `work_unit_blocked_awaiting_dependency`,
  `work_unit_blocked_amendment_recovery` -- each defaulting to
  `false`. Pings fire on transition INTO the matching class (initial
  entry or reclassification from a different bucket), idempotent per
  `(task × class)`. The cache is pruned on `cmd_sync_blocked` /
  `cmd_reconcile_cascade` for tasks that exit `blocked`, so re-entry
  into the same class produces a fresh ping rather than being
  suppressed by a stale entry. Every Slack payload also carries a new
  `Backlog` field naming the source workspace (operator request
  2026-05-19), so operators monitoring multiple workspaces can tell
  at a glance which backlog a ping refers to. Implementation lives in
  `src/devbench/notifications.py` (new `_EVENT_BY_CLASSIFICATION` +
  `_NOTIFY_FN_BY_CLASSIFICATION` mappings, six new `notify_*` helpers,
  generalised `notify_blocked_classification_transition`, new
  `prune_notification_state_for_unblocked`); pinned by 16 cases in
  `tests/test_notifications_transition.py` (parametrised across all
  seven classes) plus six new payload-shape tests in
  `tests/test_notifications.py`. Replaces the operator-only
  `notify_blocked_operator_transition` from #207 per CLAUDE.md
  "Complete Replacement of Superseded Code".

- **Operator-facing Slack notifications** (PR #202) — toggleable per-event
  Slack pings on every interesting lifecycle event: work-unit done,
  work-unit blocked-and-operator-action-required, work-unit materialised
  / promoted, PR opened / merged, CI failure, orchestrator stop (clean,
  drain, SIGTERM, or crash — always-fire on exit), orchestrator
  auto-restart. New `notifications:` yaml
  block with one independent boolean toggle per event; webhook URL +
  Slack user-id flow through `DEVBENCH_NOTIFICATIONS_SLACK_WEBHOOK_URL`
  and `DEVBENCH_NOTIFICATIONS_SLACK_USER_ID` env vars so credentials
  never touch tracked yaml. New `devbench notify-test --event <name>`
  CLI for smoke-testing setup. See `docs/slack-notifications.md` for
  the full operator walkthrough.

- **`make start` auto-restarts on SDK `RUNTIME_DEGRADATION`-only
  `NO_ACTIONABLE` exits** (issue #183 follow-up; pairs with the renderer +
  ETA fixes below). The orchestrate skill exits cleanly on `NO_ACTIONABLE`
  whenever no actionable work remains, and previously the Makefile's
  `start` target ran `uv run python -m devbench.cli start` exactly once --
  so a session ending purely because every remaining blocker classifies as
  `BlockedTaskState.RUNTIME_DEGRADATION` (the SDK subprocess lost
  Agent-tool access mid-session, recoverable by a fresh subprocess) would
  stop the orchestrator until an operator noticed and re-ran `make start`.
  `cmd_start` now inspects the backlog post-mortem after `asyncio.run`
  returns: when there is at least one `RUNTIME_DEGRADATION` blocker AND
  zero `IN_PROGRESS` / `IN_REVIEW` tasks AND zero
  `OPERATOR_ACTION_REQUIRED` blockers, it writes one
  `[ORCHESTRATOR_AUTO_RESTART] reason=runtime_degradation tasks=<ids>`
  audit line to `logs/orchestrator.log` and returns the new
  `ORCHESTRATOR_RESTART_EXIT_CODE` (42). The `start` target wraps the CLI
  call in a bounded while-loop: exit code 42 triggers a restart (printing
  `INFO: orchestrator auto-restart (attempt N/max)` to stderr), any other
  code is passed through unchanged, and the cap defaults to
  `DEVBENCH_MAX_AUTO_RESTARTS=3` (override via env). After the cap, the
  Makefile fails fast with rc=1 and an `ERROR: orchestrator hit
  RUNTIME_DEGRADATION restart cap` message naming the SDK subprocess
  Agent-tool loss as the thing to investigate. Pinned by
  `tests/test_cli.py::TestCmdStartAutoRestartPostMortem`,
  `TestShouldAutoRestartPostMortem` (7 cases covering each precondition),
  and the new `tests/test_integration/test_make_targets.py` Makefile-loop
  integration tests (cap-exhaustion, restart-then-succeed, non-42
  pass-through).

- **Per-agent model overrides via `agents:` block in `devbench.yaml`** (ADR-25).
  Each of the ten work agents (executor, blocker-resolver, manifest-amender,
  security-reviewer, task-factory, review-supervisor, plus the four
  review_team judges) can be retargeted to a different model independently of
  its `.md` frontmatter default. Lets operators with uneven per-model quota
  drive specific agents on opus while leaving the rest on sonnet, etc. A
  workspace-local shadow plugin tree is materialised at
  `<workspace>/.devbench/plugin-shadow/devbench/` on every launch -- agent
  files whose model is overridden are written as plain files with the
  `model:` frontmatter line rewritten; every other plugin file is symlinked
  back to the canonical install. New CLI command `devbench
  prepare-plugin-shadow` builds the same shadow standalone for interactive
  launchers (`claude --plugin-dir "$(devbench prepare-plugin-shadow)"`).
  `DEVBENCH_AGENT_MODEL_<NAME>` (or `JUDGE_AGENT_MODEL_<NAME>` for the five review judges + review-supervisor) env vars override the YAML block on a per-call
  basis (env > yaml > frontmatter). Defaults preserved: workspaces without
  an `agents:` block build no shadow and use the canonical plugin path,
  bit-identical to pre-feature behaviour. See `docs/adr/25-per-agent-model-overrides.md`.
- **`ScopeFilter` scope selector module** (issue #190). New
  `src/devbench/scope.py` dataclass providing a persistent allow/deny
  scope filter for work-unit execution. Public API: `ScopeFilter`
  dataclass with `parse(include_str: str, exclude_str: str, backlog_ids: list[str])`,
  `allows(unit_id: str) -> bool`, `to_file(workspace_root)`,
  `from_file(workspace_root)`, and `clear(workspace_root)` methods.
  `InvalidScopeError` raised on malformed scope expressions (e.g.
  reversed ranges). Scope state is persisted to
  `<workspace_root>/.devbench/scope.json` so the filter survives
  orchestrator restarts.
- **`git_ops.local_only` mode**. New config flag for target repos that
  have no `origin` git remote -- typical for operational workflows
  (AWS teardowns, evidence capture, audits) where devbench drives the
  work but no application code is being authored. When `local_only:
  true`, `ensure-branch` skips `git fetch origin` and creates the
  work-unit branch off the local default ref; `git-ops` commits
  locally only; remote-touching helpers (`commit_and_push`,
  `create_tag`, `checkout_default_branch`, `rebase_and_force_push`)
  are guarded with a clear `RuntimeError`; the `devbench check`
  pre-flight inverts its origin assertion (a present remote is now
  the error). Requires `defer_pr: true` and an explicit
  `default_branch:` per repo. See `docs/operational-work.md` and
  `docs/git-ops-modes.md`.
- **DRAFT lifecycle status** (E1, issue #189). New `WorkUnitStatus.DRAFT`
  enum value placed before `IN_QUEUE` in lifecycle order. Allows work
  units to be authored and held in `draft` state before entering the
  active queue. Full parser and CLI support follows in sibling tasks.
- **`validate-backlog` enforces Task-only draft status** (E1-F2-S2, issue #189, AC-189-10).
  `BacklogManager._check_status_enum` now rejects `draft` as a status for
  Epic, Feature, and Story work units with an explicit error message:
  `<id>: Status "draft" is only valid for Task work units; <id> is type <Epic|Feature|Story>.`
  Task-level work units (IDs containing `-T<digits>`) continue to accept `draft`
  without error (AC-189-2).
- **`BacklogConfig` dataclass + `backlog.default_status_for_new_work_units` config key** (E1-F5, issue #189, AC-189-8/9).
  New `BacklogConfig` frozen dataclass in `config_loader.py` with field
  `default_status_for_new_work_units` (accepted values: `'draft'` or
  `'in-queue'`). Parsed from the optional `backlog:` YAML section in
  `backlog/config/devbench.yaml` by `_parse_backlog_config()` and
  stored on `RuntimeConfig.backlog`. Defaults to `'in-queue'` when the
  section is absent, preserving backwards compatibility (AC-189-9).
  Set to `'draft'` to require explicit human promotion before the
  orchestrator picks up newly created work units (AC-189-8). Invalid
  values raise `ValueError` with an actionable error message listing
  the accepted choices.
- **HOLD lifecycle status** (E222, issue #104). New `WorkUnitStatus.HOLD`
  + `devbench hold <id>` / `devbench unhold <id>` CLI for tasks
  deliberately deferred without breaking dep-chain math.
- **`RepoConfig.resolved_checkout_path`** (E213, issue #105) and
  `validated_repo` enrichment so consumers stop re-resolving paths
  inline.
- **Dependency integrity + `devbench sync-blocked`** (E215, issue #107):
  recursive `_deps_satisfied` walk + bulk transition of in-progress
  tasks whose deps regressed.
- **Branch uniqueness validation** (E219, issue #108): validate-backlog
  rule rejects backlogs whose work-unit Branch fields collide.
- **`devbench status --detail` panels** (E220, issue #109): three panels
  (in-queue / blocked / held) for richer status overview.
- **`devbench new-task` scaffolder** (E223, issue #110) +
  `backlog/templates/{epic,feature,story,task}.md` template files.
- **3-state blocked-task classifier**: `AUTO_CLEARING_VIA_PROPOSAL` /
  `AWAITING_AUTO_RECOVERY` / `NEEDS_OPERATOR_ATTENTION`. Classifier
  signals: pending proposal JSON on disk, rejected-amendment archive,
  recent recovery audit comment.
- **Remote EC2 provisioning stack**: full Terraform + Terragrunt +
  Ansible suite for unattended / multi-operator orchestrate runs.
  `devbench-session` per-user multi-session launcher. End-to-end guide
  at `docs/remote-ec2-setup.md`.
- **E230 `DEVBENCH_ORCHESTRATOR_SESSION_ID` filter**: hook-tail
  `--orchestrator-only` flag isolates events per orchestrator session.
- **Inline orphan-cleanup chore commit (Phase 1)**: `cmd_git_ops` now
  runs `cleanup_tracked_orphans` programmatically and lands a chore
  commit (`chore(cleanup): untrack devbench-managed orphan paths and
  update .gitignore`) before the task's commit when build/state orphan
  paths are detected. Two commits land per `git-ops` invocation. The
  cleanup is no longer a backlog work unit.
- **CI-failure executor retry (issue #115)**: on CI failure,
  `cmd_git_ops` writes the trimmed failing-job log under
  `.devbench/ci-failures/<id>-<n>.log`, appends a `[CI_FAIL]` audit
  comment, and returns rc=2 to signal the orchestrator to re-invoke
  the executor with a `ci-fail` feedback payload. Bounded by
  `MAX_RETRY_ATTEMPTS`.
- **PR review-comment polling (issue #116)**: between CI-pass and
  merge, `cmd_git_ops` polls `gh pr view` and `gh api .../comments`
  for asynchronous bot review feedback (Copilot, Q-Dev, internal review
  bots). On signal, writes a `pr-bot` JSON feedback payload to
  `.devbench/pr-bot-feedback/<id>-<n>.json`, appends a `[PR_BOT_FAIL]`
  audit comment, and returns rc=3.
- **YAML `debug:` section**: diagnostic-tuning knobs
  (`check_registration_retries`, `check_registration_delay_seconds`,
  `blocked_recovery_window_seconds`) gain a stable workspace-level
  setting alongside their env vars.
- **YAML promotion of every PR-119 toggle**: every orchestrator
  feature flag now resolves env > YAML > default. Twelve toggles
  promoted: `git_ops.inline_orphan_cleanup`, `git_ops.ci_failure_retry`,
  `git_ops.pause_before_merge` (#101 schema only; impl is a follow-up),
  `git_ops.orphan_patterns`, `git_ops.pr_review_resolution.{enabled,
  agents, decision_blocks, settle_seconds, poll_interval}`,
  `limits.ci_failure_log_bytes`, and the three `debug.*` knobs.
- **Comprehensive `sample-config.yaml`**: every YAML field defined in
  `config-schema.json` now appears in the sample with its default
  value. Schema-completeness is enforced by
  `tests/test_config_loader.py::TestSampleConfigCompleteness`.
- **`_resolve_bool` helper**: env-var boolean parser with strict
  truthy / falsy validation. Misconfigurations fail fast at process
  start with a `ValueError`.
- **Recovery-proposal dedup** (issue #141). `cmd_write_proposal`
  computes a stable `fix_signature` over `(target_repo,
  sorted(files_to_own), normalised intent_phrase)` and scans pending
  proposals for a match. On hit, the new source task is auto-wired
  to the existing recovery via `add-dep` and the duplicate JSON is
  not written; on miss, the signature is stamped into the proposal
  before persisting. Pinned by `tests/test_backlog/test_proposal_dedup.py`
  and `tests/test_backlog/test_proposal_scanner.py`.
- **Manifest-amender auto-dep** (issue #142). When the conflict task
  is in a terminal state (`done` / `declined`), the amender now
  auto-invokes `uv run devbench add-dep <source-task-id>
  <conflict-task-id>` and emits `[CONFLICT_AUTODEP]` instead of
  recommending the operator wire it. Failure surfaces as
  `[CONFLICT_AUTODEP_FAILED]`. Pinned by
  `tests/test_integration/test_manifest_amender_pre_conflict.py`.
- **Materialise-time placeholder rejection** (issue #143).
  `cmd_materialise_proposal` rejects proposals whose
  `proposed_tasks[*].suggested_approach` is empty, whitespace-only,
  TODO, or TBD before any draft reaches the operator. Pinned by
  `tests/test_backlog/test_proposal_lifecycle_hardening.py` +
  `tests/test_cli.py::TestCmdMaterialiseProposalLifecycleGates`.
- **Bounded recovery-cascade depth** (issue #144). Proposals carry a
  `cascade_depth` field (`parent_depth + 1`); the new
  `orchestrate.max_cascade_depth` YAML knob (default 2, env override
  `DEVBENCH_ORCHESTRATE_MAX_CASCADE_DEPTH`) caps recursion. At cap, the
  source task transitions to `NEEDS_OPERATOR_ATTENTION` rather than
  materialising a deeper draft. Pinned by
  `tests/test_backlog/test_proposal_lifecycle_hardening.py::TestEnforceCascadeDepth`.
- **Backlog-repo recovery skip** (issue #146). `cmd_write_proposal`
  drops proposed-task entries whose `files_to_own` all live in the
  backlog repo (i.e., not in any configured target repo's
  `checkout_directory`). Backlog-repo edits (e.g. `spec/*.md`,
  `BACKLOG.md`, `backlog/**/*.md`, `docs/*.md`) are operator
  bookkeeping commits, not work-unit deliverables; the recovery
  cascade has no valid completion path for them. When every proposed
  task is skipped, the JSON envelope reports `recovery_skipped:
  true` and no proposal JSON is written. Mixed entries are pruned to
  their target-repo files. Logged as
  `[RECOVERY_SKIPPED_BACKLOG_REPO_FILES]`. Pinned by
  `tests/test_cli.py::TestCmdWriteProposalBacklogRepoSkip`.
- **`devbench reconcile-cascade` CLI command** (issue #150). Walks every
  blocked task, evaluates marker target states + regular dep states, and
  flips eligible tasks (markers all terminal AND deps satisfied) to
  in-queue with a `[CASCADE_RECONCILED]` audit comment. Returns a JSON
  envelope listing flips + skips with reasons. Operator-friendly
  recovery for blocked tasks the cascade missed (process crash, missing
  declared dep, etc.). Pinned by
  `tests/test_cli.py::TestCmdReconcileCascade`.
- **Manifest-amender rejection feedback persistence** (issue #154). Each
  `reject_amendment` call writes a structured JSON to
  `<workspace>/.devbench/amender-rejections/<task-id>-<n>.json` with
  `task_id`, `attempt`, `reason_category` (`SCOPE` /
  `APPROACH_AUTH` / `JUSTIFICATION_COHERENCE` / `PRE_FILTER` /
  `OTHER`), `reason_text`, and the original request payload. The
  blocker-resolver / executor-feedback collector ingests these on
  retry. Bounded by `MAX_RETRY_ATTEMPTS`; over-cap records still
  written but stamped `capped: true`. Pinned by
  `tests/test_backlog/test_amendment.py::TestAmenderRejectionPersistsFeedbackJson`.
- **Review-judge structured rejection feedback** (issue #156). New
  `devbench log-rejection-feedback <judge> <id> --json '<payload>'`
  CLI primitive validates the payload against
  `src/devbench/backlog/review-feedback-schema.json` (schema_version 1)
  + the per-judge controlled vocabulary in
  `src/devbench/backlog/review_feedback_vocabulary.py`, and persists
  to `.devbench/review-failures/<task-id>-<judge>-<n>.json`. The
  manifest-amender path migrates to the same shared directory; the
  legacy `.devbench/amender-rejections/` location remains as a
  forward-compat read path. The done-gate refuses
  `mark-done` until every prior rejection category is cleared via a
  `[REJECTION_FEEDBACK_RESOLVED] <judge>:<code>` audit OR escalated
  via `[NEEDS_DEP] <judge>:<code>`. `devbench status --detail`
  surfaces unresolved counts per blocked task. New per-judge
  vocabulary documented in `docs/review-feedback-vocabulary.md`.
- **In-progress attempt duration** (issue #158). `devbench status`,
  `devbench status --detail`, and `devbench report`'s in-progress
  panel now suffix every in-progress row with
  `(in-progress for 23m)` / `(in-progress for 1h 47m)` etc. When
  neither the structured log nor the work-unit audit comments yield
  a parseable timestamp the row reads `(in-progress, timer
  unavailable)` -- never silently omitted.
- **Orchestrator-alive status banner at top of `devbench report`**
  (issue #161). One-line banner derived from log-activity recency:
  `[ORCHESTRATOR ALIVE]` (green) when the last log line is within
  `stop_hook.window_seconds`, `[ORCHESTRATOR STOPPED]` (red) when
  past that window (with elapsed-since duration + last-seen
  timestamp), `[ORCHESTRATOR STARTING]` (yellow) when the log file
  is missing or empty. Includes the `DEVBENCH_ORCHESTRATOR_SESSION_ID`
  suffix when set so multi-session operators can tell which session
  the report monitors. ANSI colour only when stdout is a TTY; pipes
  / CI redirects receive plain text. Refreshes on every
  `--watch N` tick. Threshold reuses `stop_hook.window_seconds`
  rather than introducing a redundant config knob, which guarantees
  the banner stays aligned with the operator's already-tuned
  circuit-breaker quiet window (e.g., a 180s window tolerates a
  3-minute terraform-apply quiet stretch without flashing STOPPED).

### Added

- **`notifications.events.ci_pass` event toggle** (issue #219; Bundle C).
  When CI on the auto-finalize batch PR turns GREEN, `cmd_git_ops_finalize`
  -> `_handle_finalize_ci_result` now fires a `notify_ci_pass` Slack ping
  giving the operator an explicit "PR is ready for manual merge" signal
  -- previously the GREEN branch only wrote a `[CI_GREEN]` audit log line
  and was silent on Slack, leaving operators running `auto_merge: false`
  with no way to know CI passed except polling GitHub.  Default `false`
  so existing workspaces stay silent on upgrade.  New
  `notify_ci_pass(unit_id, repo, pr_url)` helper in
  `src/devbench/notifications.py` + `EVENT_CI_PASS` constant + field on
  `NotificationsEventsConfig` + JSON-schema property.  Configure-devbench
  skill template (`plugin/devbench/skills/configure-devbench/SKILL.md`)
  documents the new toggle; sample-config (`docs/devbench-yaml-reference.md`)
  shows the default-false rendering.  Pinned by
  `tests/test_notifications.py::TestSendTestNotification::test_every_event_has_a_sample`,
  `::TestSlackPayloads::test_ci_pass`,
  `::test_ci_pass_event_toggle_default_false_and_parse`, and
  `tests/test_cli.py::TestCmdGitOpsFinalizeNotifications::test_notify_ci_pass_fires_on_ci_green`.

### Fixed

- **Orchestrator burns ~$0.07/turn after orchestrate skill returns
  ALL_DONE / NO_ACTIONABLE because SDK loop never breaks** (issue #218).
  After the orchestrate skill prints one of its three terminal sentinels
  (`ALL_DONE` / `NO_ACTIONABLE` / `NO_ACTIONABLE_IN_SCOPE`) the Claude
  Agent SDK's `async for message in query(...)` iterator in `_run`
  (`cli.py:6562-6577`) keeps emitting `ResultMessage` events every ~5 s
  -- each a paid model turn carrying the same end-of-run summary --
  because the iterator has no internal natural-exit condition and only
  unwinds on quota errors, drain-on-claim, or subprocess EOF.  The
  kanon-deps-work run on 2026-05-20 burned **\$8.30 in 9.5 minutes**
  ($125.60 -> $133.90 cumulative `total_cost_usd`) before the operator
  killed the daemon; extrapolates to ~\$50/hr idle re-invocation.  New
  helper `_is_terminal_orchestrate_result(text)` matches the three
  sentinels; `_log_terminal_exit_if_applicable` writes an
  `[ORCHESTRATOR_TERMINAL_EXIT] reason=<text>` audit row and returns
  True, letting `_run`'s loop break early.  The existing #217 plumbing
  carries the captured result text into the `orchestrator_stop` Slack
  ping, so the operator sees the actual exit reason within seconds of
  the terminal marker.  Pinned by
  `tests/test_cli.py::TestIsTerminalOrchestrateResult` (7 cases) and
  `::TestCmdStartTerminalExit` (5 cases).

- **`cmd_git_ops_finalize` never fires `pr_opened` / `ci_failure`
  Slack pings; auto-finalize PR + CI lifecycle silent on Slack**
  (issue #219).  Operators running `git_ops.defer_pr: true` +
  `auto_finalize: true` (the recommended single-PR mode) got zero
  Slack pings about their batch PR's lifecycle: PR creation,
  CI failures, and CI green-readiness were all silent because
  the per-WU `cmd_git_ops` notification calls were never mirrored
  into the batch-PR code paths.  `cmd_git_ops_finalize` now calls
  `notify_pr_opened` after `ops.create_pr` returns;
  `_handle_finalize_ci_result` accepts a new `repo` kwarg and calls
  `notify_ci_failure` on FAILED_KNOWN_TASK + FAILED_UNKNOWN branches
  (attempt sentinel `1` -- the finalize path has no retry counter
  today).  When the finalize path has no in-flight WU to attribute
  the ping to (degenerate case), a symbolic `"finalize"` sentinel is
  used as the rep `unit_id`.  `pr_merged` deliberately not added --
  under `auto_merge: false` the operator merges by hand and GitHub
  does not signal devbench back; a follow-up issue should add an
  out-of-band poller if needed.  Pinned by
  `tests/test_cli.py::TestCmdGitOpsFinalizeNotifications` (5 cases
  covering pr_opened on create_pr, ci_failure on FAILED_KNOWN_TASK +
  FAILED_UNKNOWN, no-fire on TIMEOUT, and the Bundle C ci_pass
  case).

- **`orchestrator_stop` Slack ping says bare `"clean"` instead of the real
  exit reason** (issue #217).  When `cmd_start`'s SDK loop completes
  normally (e.g., the orchestrate skill returns `NO_ACTIONABLE -- 190/212
  done, 11 blocked` because of a deadlocked cascade), `_stop_reason`
  stayed at its initial value `"clean"`.  The Slack ping then read the
  same regardless of whether the backlog was complete (`ALL_DONE`) or
  blocked with work remaining -- the operator could not tell the
  difference at a glance.  `_run` now captures the latest
  `ResultMessage.result` text via a `nonlocal` closure variable;
  `cmd_start` promotes it to `_stop_reason = f"clean exit: {text}"`
  before firing the notification, so the operator sees the actual
  summary (status counts, NO_ACTIONABLE vs ALL_DONE, etc.) in the
  Slack ping.  When the SDK emits no `ResultMessage` (degenerate test
  scenario), the legacy `"clean"` reason is preserved -- behaviour is
  a strict superset of the pre-fix path.  Pinned by
  `tests/test_cli.py::TestCmdStartSlackPingResultText::test_slack_ping_includes_sdk_result_text_on_clean_exit`
  and `::test_slack_ping_falls_back_to_clean_when_no_result_message`.

- **Multi-column status table mis-aligned when one cell is much wider than
  others** (issue #214).  `_render_multi_column_table` /
  `_render_grouped_progress_table` shared a single `value_w` across every
  value column, derived from `max_cell` over every cell.  A single wide
  cell (e.g., the ETA breakdown All-time value with
  `+ blocked-runtime-degradation N` appended, ~107 chars vs Session's
  ~74) inflated all columns to that width, producing a table well past
  any reasonable terminal width.  Both renderers now compute per-column
  widths AND cap each column at `MAX_VALUE_COL_WIDTH` (50 chars); cells
  exceeding the cap wrap onto multiple physical lines via
  `_wrap_cell_value`, which prefers `` + `` boundaries first, falls back
  to whitespace word-wrap, and never breaks a single word
  mid-character (the long-word floor `_longest_word_len` keeps the column
  wide enough to fit unbreakable identifiers like
  `blocked-runtime-degradation`).  Spanning rows wrap to the joined-span
  width on the same rules.  Pinned by
  `tests/test_reporting/test_report.py::TestSpanningRows::test_multi_column_table_caps_columns_and_wraps_long_cells`,
  `::test_grouped_progress_table_caps_columns_and_wraps_long_cells`,
  `::test_wrap_cell_value_splits_on_plus_boundaries`, and
  `::test_wrap_cell_value_never_breaks_a_word`.

- **`RUNTIME_DEGRADATION` classification persists after orchestrator
  restart** (issue #215).  `_has_runtime_degradation_signal` scanned the
  work-unit's Comments section for `agent-tool-unavailable` audit rows
  within a 24h window.  Audit rows are append-only, so the same row kept
  triggering the classification for 24h regardless of how many times the
  operator restarted -- defeating both the renderer's "task remains
  blocked until the orchestrator restarts" hint and the auto-restart
  loop in `_should_auto_restart_after_no_actionable` (which then looped
  forever).  `cmd_start` now writes
  `<workspace>/.devbench/last-restart` (new constant
  `LAST_RESTART_MARKER_PATH`) on every startup; the classifier reads the
  marker via `_read_last_restart_marker` and filters out audit rows
  older than it before applying the 24h window.  With no marker
  (cold-boot / never-restarted workspace), behaviour is unchanged.
  Pinned by three new cases in
  `tests/test_backlog/test_proposal.py::TestClassifyBlockedTaskRuntimeDegradation`
  and `tests/test_cli.py::TestCmdStartWritesRestartMarker`.

- **`drain.signal` not cleared after orchestrator exit** (issue #212).
  When an operator ran `devbench drain` from a shell, the signal landed at
  `<workspace>/.devbench/drain.signal` (no `DEVBENCH_SESSION_NAME` in env),
  but `cmd_start` set `DEVBENCH_SESSION_NAME = parsed.name` (default
  `"default"`) before its drain loop ran and therefore looked at
  `<workspace>/.devbench/sessions/default/drain.signal` -- a different path.
  `consume_drain` never saw the operator's signal, the file persisted on
  disk after the orchestrator exited, and the next `devbench start` would
  read it via the same divergent path mismatch (or, with the per-session
  reader, would auto-drain on the next claim).  Operators had to run
  `devbench drain --cancel` manually after every drained shutdown.

  `drain.py::read_drain_state`, `consume_drain`, and `cancel_drain` now
  scan both candidate paths -- the per-session path (priority) and the
  workspace-root path (fallback).  The session-scoped reader observes an
  operator's workspace-root drain.signal; the unlink targets the path
  that actually held the signal.  `cmd_start`'s SDK-run `finally` clause
  additionally calls `cancel_drain(WORKSPACE_ROOT)` while
  `DEVBENCH_SESSION_NAME` is still set, so on every exit (clean,
  drain-enforced, crash) the signal is wiped from both paths -- the next
  start does not inherit a stale request.  Pinned by new cases in
  `tests/test_drain.py::TestPerSessionDrainHelpers` (cross-path
  read / consume / cancel) and
  `tests/test_cli.py::TestCmdStartCancelDrainOnExit` (finally-clause
  cleanup for both paths).

- **Clean orchestrator exits mis-labeled as crashes in `orchestrator_stop`
  Slack pings** (issue #213).  `cmd_start`'s outer
  `except BaseException as exc: _stop_reason = f"crash: {type(exc).__name__}: {exc}"`
  caught `SystemExit(0)` (raised by `sys.exit(0)` on NO_ACTIONABLE /
  ALL_DONE / SIGTERM-after-drain) and `KeyboardInterrupt` (operator Ctrl+C)
  and fired the Slack ping with text like `crash: SystemExit: 0`, mixing
  up "exited cleanly because there was nothing left to do" with "crashed
  unexpectedly".

  New helper `_label_stop_reason(exc)` buckets the reason: `SystemExit`
  with code `None` or `0` -> `"clean exit (SystemExit 0)"`;
  `KeyboardInterrupt` -> `"interrupted by operator (Ctrl+C / SIGINT)"`;
  anything else (including `SystemExit` with non-zero code) ->
  `"crash: <type>: <msg>"`.  Pinned by six cases in
  `tests/test_cli.py::TestLabelStopReason`.

### Changed (BREAKING)

- **`haiku` is rejected at config-load for every per-agent field** (#198).
  Any `agents:` block value in `devbench.yaml` (or `DEVBENCH_AGENT_MODEL_*`
  env var) that contains `haiku` -- whether the short name `"haiku"`, a full
  Anthropic API id such as `"claude-haiku-4-5-20251001"`, or a Bedrock ARN
  containing `haiku` -- now raises a `ValueError` at config-load and prevents
  the orchestrator from starting. There is no operator-facing override path.

  **Why:** Under load the Claude Agent SDK was repeatedly observed to silently
  drop the `Agent` tool from haiku's tool list mid-orchestration, causing
  `RUNTIME_DEGRADATION` audit events and forcing the orchestrator to classify
  work units as blocked. Two tasks (E1-F4-S1-T3 and E4-F1-S1-T5) hit the
  pattern across four separate restarts within one evening. The prior release
  promoted the `review-supervisor` frontmatter default from `haiku` to
  `sonnet` and added advisory warnings; this release converts the advisory
  into a hard config-load rejection so the failure mode cannot be reintroduced
  by an operator YAML change.

  **Migration:** Replace any `haiku` value in `agents:` or `review_team:` with
  `sonnet` (minimum recommended) or `opus`. The same applies to
  `DEVBENCH_AGENT_MODEL_*` env vars. `RECOVERY_PROBE_MODEL` in
  `constants.py` is not affected -- the probe uses haiku intentionally for
  its minimal 1-token latency check, not as a work agent.

- **Canonical environment-variable namespace is `DEVBENCH_*`** (#197).
  Every operational env var is named `DEVBENCH_<NAME>` and is read by
  `config.py` / `config_loader.py` / `log_setup.py` via simple
  `os.environ.get` calls. Missing required vars (workspace root, Claude
  model) fail fast at module-import time with a message that names the
  expected var. No alias or fallback layer.

  **Identifiers preserved under the LLM-as-judge concept** (kept as
  `JUDGE_*` because they refer to judges, not to the environment-variable
  namespace): `KNOWN_JUDGE_NAMES`, `REVIEW_JUDGE_NAMES`,
  `SECURITY_JUDGE_NAMES`, `ALL_REQUIRED_JUDGE_NAMES`,
  `WORKFLOW_AGENT_JUDGE_NAMES`, `JUDGE_CATEGORIES`, `JUDGE_SEVERITY_ORDER`,
  the per-judge model env vars
  (`JUDGE_AGENT_MODEL_{CODE_REVIEWER, TEST_REVIEWER, DOC_REVIEWER,
  SECURITY_REVIEWER, CHANGES_MANIFEST, REVIEW_SUPERVISOR}`), and the
  `[JUDGE_*_VERDICT]` audit-row tag format.

### Changed (model defaults)

- **`review-supervisor` frontmatter default is now `sonnet`** (was `haiku`).
  Empirical observation in the self-improve workspace: under load the
  Claude Agent SDK was repeatedly observed to silently drop the `Agent`
  tool from haiku's tool list mid-orchestration, leaving review-supervisor
  unable to dispatch the four review_team judges and forcing the
  orchestrator to classify the work-unit as `RUNTIME_DEGRADATION` (issue
  #183 follow-up). Promoting to `sonnet` closes the failure mode without
  the cost of `opus` (this is still pure fan-out coordination, not
  judgment-heavy work). ADR-25, the sample config, `docs/cli-reference.md`,
  `docs/zero-to-ready.md`, `docs/llm-authentication.md`, and the
  brownfield example launcher all updated to reflect the new default and
  to warn operators against pinning ANY work agent to `haiku`. The short
  name `haiku` is still accepted by the YAML parser so operators can
  experiment, but every documented role default avoids it.

### Added

- **Bounded skill iterate-until-perfect mechanism** (issue #204, spec
  section 4.6.0). The four onboarding skills (`create-spec`,
  `spec-to-backlog`, `bootstrap-environment`, `configure-devbench`)
  previously described their self-critique loops in `SKILL.md` prose only,
  with no observable iteration bound or escalation audit. The loop is now
  enforced by a small support module plus five new constants:

  - `src/devbench/constants.py` adds `SKILL_MAX_ITERATIONS`,
    `SKILL_QUALITY_THRESHOLD`, `SKILL_STATE_DIR_NAME`,
    `SKILL_AUDIT_MAX_ITERATIONS_REACHED`, and
    `SKILL_AUDIT_QUALITY_THRESHOLD_REACHED`.
  - `src/devbench/skill_state.py` (new module) provides `SkillState`
    dataclass plus `read_checkpoint`, `write_checkpoint` (atomic
    temp-then-rename), and `emit_audit` (structured audit row appended
    to the orchestrator log).
  - Each skill's `SKILL.md` and `docs/skills/*.md` now documents a
    `## Self-critique loop (bounded)` section that names the constants and
    explains the read-increment-write-audit-terminate sequence.
  - `tests/test_skill_state.py` (new) drives the helpers to 100% line +
    branch coverage; `tests/test_constants.py` asserts the new constants
    are present with the right shapes; `tests/test_plugin/test_skill_structure.py`
    asserts every onboarding `SKILL.md` documents the bounded loop and
    references the constant symbols.

  Operators see iteration exhaustion as `[SKILL_MAX_ITERATIONS_REACHED]`
  in the existing `devbench report` / `devbench hook-tail` streams; no new
  infrastructure is required.

### Fixed

- **Amendment-rejection no longer mis-fires the `work_unit_blocked_operator`
  Slack ping** (issue #210). `reject_amendment` in
  `src/devbench/backlog/amendment.py` previously called `mark_blocked`
  BEFORE writing the rejected-requests archive. `mark_blocked` runs
  `classify_blocked_task` inline; the classifier's
  `AWAITING_AMENDMENT_RECOVERY` signal is the presence of the archive
  on disk, so the classifier saw no recovery signal and fell through to
  `OPERATOR_ACTION_REQUIRED`. Pings fired against the wrong per-class
  toggle. The fix is a one-line reorder: write the archive +
  rejection-feedback JSON first, then `mark_blocked`. Pinned by
  `tests/test_backlog/test_amendment.py::TestRejectAmendment::test_rejected_archive_exists_before_mark_blocked_runs`,
  which monkey-patches `_set_status` and asserts the archive is on disk
  at the moment the blocked-write fires.

- **`work_unit_blocked_operator` Slack notification now fires on every
  transition into `OPERATOR_ACTION_REQUIRED`** (issue #207). Before this
  fix the ping was one-shot at `mark_blocked` time only; a task that
  entered `blocked` with classification `AWAITING_DEPENDENCY` (silent,
  correct) and later drifted into `OPERATOR_ACTION_REQUIRED` -- because
  a dep landed but the cascade missed it (#208) or a `[BLOCKED]` audit
  went stale -- left the operator silently uninformed.  A new
  per-workspace classification cache at
  `<workspace>/.devbench/notification-state.json` tracks each task's
  last-observed classification; the new
  `notify_blocked_operator_transition` helper in
  `src/devbench/notifications.py` fires exactly once per transition into
  the `OPERATOR_ACTION_REQUIRED` bucket and is wired into `mark_blocked`,
  `cmd_sync_blocked`, and `cmd_reconcile_cascade`.  Read-only renderers
  (`devbench status`, `devbench report`) still classify on every refresh
  but do NOT route through the notifier so they cannot duplicate pings.
  Cache failures (missing dir, corrupt JSON, non-object payload) are
  best-effort -- treated as empty cache, regenerated on next write,
  logged as `[WARN]` without aborting the orchestrator.  Pinned by
  `tests/test_notifications_transition.py` (9 cases: first-time fire,
  idempotent re-call, transition into / out of bucket, event-disabled
  short-circuit, corrupt-cache + non-dict-payload recovery, multi-task
  independent tracking).

- **Auto-requeue cascade now covers regular task-level dependencies, not
  only `promoted_proposals`** (issue #208, follow-up to #147). Before
  this fix, `mark_done(B)` fired the cascade only for tasks linked via
  `[BLOCKED_PENDING_PROPOSAL]` markers; tasks whose `Dependencies` table
  named B but carried no marker stayed in `blocked` indefinitely.  The
  new `BacklogManager._auto_requeue_regular_dep_dependents` helper in
  `src/devbench/backlog/manager.py` is called from `_set_status`
  alongside the existing marker cascade and unblocks blocked tasks whose
  regular `Dependencies` table entries are now all terminal AND that
  carry no `[BLOCKED_PENDING_PROPOSAL]` marker (the marker cascade owns
  those).  Each flip writes a `[UNBLOCKED] [CASCADE_RESOLVED] dependency
  '<dep_id>' now terminal` audit comment matching the supersession shape
  used by `cmd_sync_blocked` and the marker cascade (#153). Pinned by
  `tests/test_backlog/test_manager.py::TestAutoRequeueRegularDepDependents`
  (6 cases including mark_done-driven end-to-end integration).

- **`scope.json` non-object top-level payload now fails fast with an
  actionable error** (issue #205). Before this fix a top-level JSON
  list (`[]`, `[1, 2]`), bare string, integer, `null`, or `true` reached
  the per-field shape check at `src/devbench/scope.py:444-450` via
  `data[field_name]`, which raised the raw Python `TypeError: list
  indices must be integers or slices, not str` -- the operator saw the
  implementation detail and not the recovery step.  A new top-level
  `isinstance(data, dict)` guard rejects any non-object payload with a
  message naming the file path and the fix step (`remove and re-run
  'devbench start --include ...' to recreate`).  Mirrored in
  `_read_scope_banner_data` (`src/devbench/cli.py`) which previously
  silently coerced `[]` into `{}` via `dict(json.loads(...))`. Pinned
  by `tests/test_scope.py::test_from_file_non_object_top_level_raises`
  (6 parametrised shape cases).

- **`devbench status` Backlog Status Summary count column right-aligns to a
  single column across every row** (issue #201). Before this fix three
  different label-pad widths (`{label:<15}` for top-level rows, `{label:<28}`
  for Blocked sub-rows) put counts in two different columns ("In Queue" at
  col ~19 vs "Blocked (auto-clearing)" at col ~32) and the longest label
  (`Blocked (runtime-degradation)`, 29 chars) overran the 28-wide pad so
  the count was jammed against the label with no breathing space. The fix
  introduces `STATUS_SUMMARY_LABEL_WIDTH: int = 32` in
  `src/devbench/constants.py` and applies it uniformly across the five
  format-string sites in `cmd_status` (TOTAL, Draft, top-level, Blocked
  sub-rows, Un-materialised). A new
  `tests/test_cli.py::TestCmdStatusSummaryAlignment` class pins the
  contract: every count value lands at the same column index, the longest
  label is followed by at least one space, the separator spans the count
  column, and no hard-coded `:<15` / `:<28` format spec remains in
  `cmd_status` (regression guard for future Blocked sub-bucket additions).

### Fixed

- **Three-defect fix for blocked tasks with satisfied `[BLOCKED_PENDING_PROPOSAL]`
  markers not auto-clearing** (issue #200). Before this fix, a task that was
  blocked via a `[BLOCKED_PENDING_PROPOSAL] <child>` marker could remain
  stuck in `blocked` after `<child>` reached `done`, requiring manual
  operator intervention to flip it back to `in-queue`.

  Three root causes addressed:

  1. **Classifier fallthrough** (`src/devbench/backlog/proposal.py`
     `classify_blocked_task` / `_classify_with_markers`): when ALL
     `[BLOCKED_PENDING_PROPOSAL]` marker targets were terminal (`done` /
     `declined`), `_classify_with_markers` returned `None`, causing the
     task to fall through to `OPERATOR_ACTION_REQUIRED` instead of
     `AUTO_CLEARING_VIA_PROPOSAL`. The fix: when all markers are terminal
     AND no regular dep or recovery signal exists, the classifier now
     returns `AUTO_CLEARING_VIA_PROPOSAL` (AC-200-1).

  2. **Cascade trigger condition** (`src/devbench/backlog/manager.py`
     `_auto_requeue_marker_dependents`): the cascade required the
     newly-done task to be in the blocked task's Dependencies table. When
     task-factory wired the dep via a marker comment only (no
     Dependencies-table row), the cascade never fired. The fix: condition 2
     now accepts the newly-done task appearing EITHER in the Dependencies
     table OR as a `[BLOCKED_PENDING_PROPOSAL]` marker ID in the Comments
     section (AC-200-2).

  3. **Marker regex too broad** (`_BLOCKED_PENDING_PROPOSAL_RE` in
     `manager.py`): the original `\S+` capture group matched any
     non-whitespace word, including prose words like "Amendment" in lines
     such as `[BLOCKED_PENDING_PROPOSAL] Amendment rejected ...`. This
     injected fake non-terminal marker IDs that prevented the cascade from
     firing. The fix: the capture group now matches only canonical task IDs
     (`E\d+(?:-F\d+)?(?:-S\d+)?(?:-T\d+)?`) (AC-200-3).

  Additionally: **`[AMENDMENT_REJECTED]` structured-tag audits now trigger
  `AWAITING_AMENDMENT_RECOVERY`** (`src/devbench/backlog/proposal.py`
  `_REJECTION_TAG_RE`). Previously, manifest-amender's structured-tag audit
  `[AMENDMENT_REJECTED] tdd_green_production_fix; rejected: POST_CHECK: ...`
  was not matched by `_RECOVERY_BODY_RE` (which required prose like
  `amendment rejected` without brackets). A new `_REJECTION_TAG_RE` matcher
  recognises the structured-tag form explicitly (AC-200-4).

- **`devbench report` / `watch` no longer crash on transient WU md
  `FileNotFoundError`**. The user-reported scenario: `devbench watch`
  was running against an active orchestrator; one tick caught a WU md
  in the middle of a non-atomic SDK-driven `Write` / `Edit` tool
  call, the parser raised `FileNotFoundError`, and the watch session
  died with the misleading prefix `devbench report: cannot parse
  '/.../BACKLOG.md': [Errno 2] No such file or directory:
  '/.../E4-F1-S1-T5.md'` (the prefix named the index but the missing
  path was a WU md). Two surgical fixes: (1)
  `BacklogParser.parse_index` (`src/devbench/backlog/parser.py:189-200`)
  now performs a single-shot synchronous retry on
  `FileNotFoundError` from `parse_work_unit_file` -- zero sleep, zero
  temporal logic, just an immediate second attempt that closes the
  atomic-rename / writer-window race. Persistent FNF still
  propagates with the original missing path intact, preserving
  fail-fast. (2) `generate_report`'s exception wrapper
  (`src/devbench/reporting/report.py:2249-2278`) splits into two
  branches: `FileNotFoundError` surfaces the actual missing path
  (`exc.filename` when present, otherwise `str(exc)`) with a hint
  noting the writer-window race; `ValueError` keeps the original
  `validate-backlog` hint for genuine index corruption. The fixes
  benefit every `BacklogParser` caller -- `report`, `watch`,
  `status`, `cmd_check` -- transparently. Pinned by
  `tests/test_backlog/test_parser.py::TestParseIndexFNFRetry` (2
  cases: transient recovers, persistent propagates) plus the new
  WU-md-path-aware case in
  `tests/test_reporting/test_report.py::TestGenerateReportBacklogParseFailure::test_file_not_found_naming_wu_md_surfaces_wu_path`.

- **ETA denominator now includes `RUNTIME_DEGRADATION` tasks**
  (issue #183 follow-up, paired with the renderer-bucketing fix above).
  `_compute_window_stats` previously computed
  `eta_task_count = tasks_active + tasks_blocked_recovery + tasks_blocked_auto`,
  excluding `RUNTIME_DEGRADATION` from the auto-recoverable denominator
  even though an operator restart of `make start` clears it -- so the
  bucket is auto-recoverable in exactly the same sense as
  `AWAITING_AMENDMENT_RECOVERY`, `AWAITING_DEPENDENCY`, and
  `AUTO_CLEARING_VIA_PROPOSAL`. The trailing-summary `attn_blocked`
  formula had the matching gap and was treating runtime-degradation
  tasks as operator-attention work. `_compute_window_stats` gains a
  `tasks_blocked_runtime_degradation` parameter; `WindowStats` gains an
  `eta_blocked_runtime_degradation` field; `eta_task_count`,
  `eta_total`, and `attn_blocked` all grow the new term consistently;
  `_format_est_hours_display`'s breakdown suffix surfaces the new
  bucket. All three call sites in `_render_report` thread
  `backlog.tasks_blocked_runtime_degradation` through. Pinned by
  `tests/test_reporting/test_report.py::TestEtaIncludesBlockedRecoveryAndAuto::test_compute_window_stats_uses_combined_denominator`
  (extended) and the new
  `test_runtime_degradation_changes_eta_total`.

- **`devbench report` now buckets `RUNTIME_DEGRADATION` correctly**
  (issue #183 follow-up). The classifier `classify_blocked_task` already
  returned `BlockedTaskState.RUNTIME_DEGRADATION` for tasks whose
  Comments contained a recent `[BLOCKED] agent-tool-unavailable` audit
  from review-supervisor's Step 0 self-check, but the report renderer's
  two routing paths (`_classify_blocked_unit_into_buckets` and
  `_backlog_totals_from_units`) used an `if/elif/elif/elif/elif/else`
  chain that enumerated five other buckets and silently funnelled every
  other state -- including `RUNTIME_DEGRADATION` -- into
  `operator_rows / cnt_operator`. Result: `devbench report` mis-rendered
  RUNTIME_DEGRADATION tasks as "operator action required" while
  `devbench status --detail` (which has its own correct routing in
  `cli.py:316-327`) rendered them in the right bucket. Both renderer
  paths now handle every `BlockedTaskState` enum member explicitly and
  raise `RuntimeError` if a future enum addition is not wired in --
  per CLAUDE.md no-fallback-logic. The blocked-task display gains a
  dedicated "Blocked tasks (runtime-degradation)" panel with the
  resolution hint `"SDK lost Agent-tool access mid-session; `make
  start` auto-restarts to recover."`. `_BacklogTotals` exposes a new
  `tasks_blocked_runtime_degradation` field. Pinned by
  `tests/test_reporting/test_report.py::TestBacklogTotalsSixBlockedFields::test_unhandled_blocked_state_raises_in_counter_path`
  plus extended canonical-order panel test.

- **Atomic temp-then-rename for every work-unit md write** (commit B of
  the shadow-plugin / WU-write fix pair). Concurrent readers of a WU md
  file could previously observe a heading-less intermediate state during
  an in-place rewrite, causing `devbench report` /
  `devbench validate-backlog` / any external parser consumer to fail
  with `No top-level heading found in <path>`. Every WU md writer in
  `devbench.backlog.manager`, `devbench.backlog.proposal`,
  `devbench.backlog.amendment`, `devbench.backlog.work_unit`, and the
  WU-touching `cmd_*` entry points in `devbench.cli` now routes through
  the new shared helper `devbench.utils.io.atomic_write_text`, which
  writes to `<path>.tmp` then atomically renames over the target via
  `Path.replace`. A reader observing *path* sees either the prior
  complete content or the new complete content, never partial. Pinned
  by `tests/test_utils/test_io.py::TestAtomicWriteTextConcurrentReader`
  (50 rapid back-and-forth atomic rewrites under a tight-loop reader,
  zero partial-write observations across thousands of reads).
  The legacy `_atomic_write` helper inside
  `devbench.backlog.amendment` is removed in favour of the shared one
  so there is only ever one source of truth for the atomic-write
  pattern. `devbench.utils.io` is added to the
  `Makefile :: test-coverage-new` gate at 100% line + branch.

- **ETA formula now includes auto-recovering blocked tasks**
  (issue #157). `devbench report`'s `Est. time to complete remaining`
  multiplier was `tasks_active * recent_pace_minutes`; it now reads
  `(tasks_active + tasks_blocked_recovery + tasks_blocked_auto) *
  recent_pace_minutes`, since both blocked buckets resolve on devbench's
  own. The `Needs operator attention` bucket stays excluded (genuine
  halt -> unbounded ETA). The cell carries a comment-suffix
  (`~5.4 h (active 4 + blocked-recovery 60 + blocked-auto 27 at 5.6
  min/task)`) showing the breakdown. The cost projection uses the same
  denominator. ETA still falls back to `n/a` when fewer than the
  required pace samples have completed in the recent window.

### Fixed

- **Shadow plugin can no longer be cleared while an orchestrator is using it** (ADR-25 sentinel-protected lifecycle). `cmd_start` now writes its PID to `<workspace>/.devbench/plugin-shadow/devbench/.pid` immediately after materialising the shadow. `clear_shadow_plugin` reads the sentinel before deleting the tree: when the recorded PID is alive, it raises `RuntimeError` naming the owning PID and recommends stopping it first. Closes a production race where a stray `devbench prepare-plugin-shadow` (firing when the YAML `agents:` block matched frontmatter defaults, which caused `materialise_shadow_plugin` to clear the shadow and return None) deleted the shadow's hook scripts out from under a running orchestrator. The SDK kept the plugin cached in memory so tool calls continued, but each hook fires as a fresh shell-script subprocess; with the script files gone, hook telemetry silently stopped logging to `hook-logs.jsonl`. The sentinel converts that silent-corruption to a fail-fast error. Sentinel lives inside the shadow tree so a legitimate rebuild's `rmtree` cleans it atomically.

### Changed (model defaults)

- **Per-agent model defaults retuned by role**. The plugin agent
  `.md` frontmatter `model:` lines were flipped from a uniform
  `sonnet` to a role-aware split: `executor` stays on `sonnet`
  (writes code under TDD; fast happy-path); the five judges
  (`code-reviewer`, `test-reviewer`, `doc-reviewer`, `changes-manifest`,
  `security-reviewer`) and the three workflow-reasoning agents
  (`blocker-resolver`, `manifest-amender`, `task-factory`) move to
  `opus` (a bad verdict / proposal / amendment decision costs more
  than the inference savings and these agents fire only after a task
  finishes or on the unhappy paths, so cost is bounded);
  `review-supervisor` stays on `haiku` (pure fan-out coordinator).
  All eight upgraded agents pick up the new default automatically;
  operators with opus-budget pressure can pin individual agents back
  to `sonnet` via the `agents:` block (ADR-25). `sample-config.yaml`,
  `docs/zero-to-ready.md`, `docs/cli-reference.md`,
  `docs/llm-authentication.md`, `docs/adr/25-per-agent-model-overrides.md`,
  and `tests/test_plugin_shadow.py`'s synthetic fixture updated to
  reflect the new defaults.

### Removed

- **Dead `judge_model` / `executor_model` YAML fields** removed from
  `config-schema.json`, `RuntimeConfig`, `load_runtime_config`, the test
  fixtures, the parser tests, `sample-config.yaml`, and the example backlog
  config under `examples/backlogs/...`. These were holdover from the
  pre-plugin "main2" Python-orchestrator architecture (ADR-01); the
  plugin-based architecture replaced them with per-agent `.md` frontmatter
  models, leaving the YAML fields parsed but never read. ADR-25
  (`agents:` block) is the current control surface for per-agent model
  routing. `docs/architecture.md`, `docs/model-pricing.md`,
  `docs/llm-authentication.md`, `docs/cli-reference.md`, the example
  workspace's `README.md` and `devbench-commands.txt` updated to remove
  references to the dead fields and to clarify that `DEVBENCH_CLAUDE_MODEL` is
  the SDK caller's model (orchestrate skill coordination calls), not a
  global per-role pin.

### Changed

- **Cost-rate calibration guidance** added to `sample-config.yaml`
  `report:` block + new "Calibrating cost rates against actual billing"
  section in `docs/model-pricing.md`. Operators with non-default model /
  context-tier / contract-pricing combinations now have a documented
  worked example for deriving `token_cost_per_million_input` /
  `_output` from actual API billing
  (`correction_factor = actual_billing / reported_cost`; multiply both
  rates). The `token_cost_discount` field is documented as wrong-
  direction for under-reporting (it only decreases reported cost, so
  cannot fix cases where reported is below actual). No behaviour
  change in the cost-computation code.
- **Transitive dep chains accepted by Manifest Conflict validator**
  (issue #145). Previously the validator required `N*(N-1)/2` direct
  dep edges between N claimants of the same Manifest path; now any DAG
  that totally orders the set via transitive reachability is accepted,
  so a clean N-1 edge chain is sufficient. Operator overhead drops
  from quadratic to linear in claimant count. The conflict error
  message prints a suggested chain in lexical-sort order as an
  operator hint. Pinned by
  `tests/test_backlog/test_manager.py::TestValidateManifestConflictsTransitiveChain`.
- **CI-failure retry default flipped to ON** (issue #115). Default
  behaviour is rc=2 + executor retry; opt out via
  `git_ops.ci_failure_retry: false` in `devbench.yaml` or
  `DEVBENCH_CI_FAILURE_RETRY_ENABLED=0`.
- **Review-supervisor scope guard** (issue #118): the
  `guard-review-supervisor-scope.sh` PreToolUse hook now also blocks
  Agent-tool subagent spawns whose subagent_type is not in the
  read-only review_team allowlist, closing the Bash-only gap.
- **TBD Manifest placeholder rejection** (issue #117): new
  validate-backlog rule 19 rejects work-unit Manifest rows whose
  first cell starts with `TBD`. `cmd_claim` refuses on placeholder
  pre-flight.
- **`wait_for_checks` workflow-registration race defence** (issue
  #114): "no checks reported" is now disambiguated via a local
  `<repo>/.github/workflows/*.y[a]ml` glob. Repos with workflow
  files retry up to `DEVBENCH_CHECK_REGISTRATION_RETRIES` (default 12)
  attempts spaced by `DEVBENCH_CHECK_REGISTRATION_DELAY_SECONDS`
  (default 5). On retry exhaustion, devbench refuses the merge.
- **Workspace-layout doc rewritten** (issue #113): `DEVBENCH_WORKSPACE_ROOT`
  is the parent of `backlog/`, with target repos as siblings.
- **Operator YAML field surface**: every PR-119 env-only knob now has
  a YAML equivalent. Operators set workspace-stable behaviour in
  `backlog/config/devbench.yaml`; env vars remain available as
  per-launch overrides.
- **Auto-requeue cascade fires on every terminal transition** (issue
  #147). `BacklogManager._set_status` now invokes
  `_auto_requeue_marker_dependents` whenever the target status is
  terminal (`done` OR `declined`), not just `done`. Idempotent: a
  per-instance guard tracks `(backlog_index, unit_id)` pairs so a
  redundant `_set_status` call does not double-fire the scan.
  `[CASCADE_RESOLVED]` is now appended alongside `[AUTO_UNBLOCKED]`
  on every cascade-driven re-queue so the audit-supersession panel
  filter (#153) can hide stale `[BLOCKED]` rows.
- **`cmd_sweep_proposals` auto-promotes pre-existing proposed drafts**
  (issue #155). When `task_factory.auto_accept_proposals: true`, the
  sweep now runs a second pass over the full backlog index and
  promotes every `proposed` task whose proposal JSON has been deleted.
  Closes the gap where drafts authored under the toggle but
  materialised before it was flipped on were marooned in `proposed`
  state forever. Pinned by
  `tests/test_cli.py::TestCmdSweepProposalsAutoPromotesPreExisting`.

### Fixed

- **`sync-blocked` evaluates `[BLOCKED_PENDING_PROPOSAL]` marker target
  status** (issue #148). The legacy
  `_BLOCKED_PENDING_PROPOSAL_OPEN_RE` regex flagged every marker as
  "open" -- including markers whose target had already completed. The
  new `_has_open_proposal_marker(content, units_by_id)` helper resolves
  each marker target via the parsed index and returns `True` only when
  at least one target is non-terminal (anything other than `done` /
  `declined`). Unknown target IDs (rejected drafts whose backlog row
  was removed) stay conservative. Pinned by
  `tests/test_cli.py::TestSyncBlockedEvaluatesMarkerTargetState`.
- **`classify_blocked_task` considers regular task-level deps** (issue
  #149). When markers are closed/absent AND the task's declared
  regular dependencies are still in flight, the result is now
  `AWAITING_AUTO_RECOVERY` instead of incorrectly escalating to
  `NEEDS_OPERATOR_ATTENTION`. The orchestrator's next sweep cycle
  picks the task back up automatically; no operator action required.
  Pinned by
  `tests/test_backlog/test_proposal_lifecycle_hardening.py::TestClassifyBlockedConsidersRegularDeps`.
- **N-node dependency cycle detection in `validate-backlog`** (issue
  #151). New `BacklogManager._check_dep_cycles` runs DFS-with-
  recursion-stack over the dependency graph derived from the Full
  Work Unit Index. Catches 4-node, 5-node, and arbitrary-N cycles
  that the prior shallow check missed. Reports each cycle once,
  rotated to start at its lexicographically smallest ID. Pinned by
  `tests/test_backlog/test_manager.py::TestValidateDepCycle4Node`.
- **`_VARIADIC_COMMANDS` registration is auto-discovered**  (issue
  #152). New
  `tests/test_cli.py::TestVariadicCommandsCoverage` walks `_COMMANDS`,
  inspects each handler's source for `--reason` / `--reasoning` /
  `--message` flag-with-value patterns, and asserts every match is
  registered in `_VARIADIC_COMMANDS`. Adds the same dispatcher
  guard as a self-test so a future flag-bearing command cannot ship
  without correct registration.
- **Status panel filters stale `[BLOCKED]` audits superseded by
  `[UNBLOCKED]` / `[CASCADE_RESOLVED]`** (issue #153). The
  append-only Comments history in the file is unchanged; only the
  `status --detail` panel renderer hides rows that have been
  succeeded by a later positive transition. The cascade re-queue
  audit now reads `[AUTO_UNBLOCKED] [CASCADE_RESOLVED] ...` and
  sync-blocked writes `[UNBLOCKED] deps satisfied ...`. Pinned by
  `tests/test_cli.py::TestStatusPanelFiltersStaleBlockedAudits` and
  `tests/test_backlog/test_manager.py::TestSetStatusWritesUnblockedAudit`.
- **`_hook_lib.sh::decode_json_escapes` bash 4.3+ nameref breaks on
  macOS bash 3.2.57** (issue #120). Replaced `local -n` nameref with
  `${!1}` indirect read + `printf -v "$1"` write (bash 3.0+
  compatible).
- **Inline orphan cleanup ValueError on symlinked checkouts**
  (issue #125). `_run_inline_cleanup_steps` now resolves
  `repo_path` once at the function head so it lives in the same
  path-space as `cleanup_tracked_orphans`'s internally-resolved
  `OrphanReport.gitignore_path`. Every workspace following the
  documented symlinked-checkout layout (`docs/backlog-contract.md`
  Workspace layout) now runs the inline cleanup correctly. New
  regression test
  `tests/test_cli.py::TestInlineOrphanCleanup::test_inline_cleanup_handles_symlinked_repo_path`
  pins the fix.
- **Security reviewer no longer flags findings on files outside the
  task's staged diff** (issue #126). The
  `plugin/devbench/agents/security-reviewer.md` prompt now contains an
  explicit five-rule scope contract that captures the in-scope path
  set from `devbench get-diff` first, refuses to read out-of-scope
  files, and drops out-of-scope findings from the verdict. New
  regression test
  `tests/test_integration/test_security_review_scope.py` pins the
  scope-contract text by-content so the rule cannot be silently
  removed.
- **Stop-hook block decision now honoured across Claude Code 2.x
  (3-layer defence)** (issues #138, #139, #140). Three independent
  root-cause hypotheses each addressed in the same commit so the
  orchestrator self-termination class is closed regardless of which
  hypothesis was actually live:
  - `plugin/devbench/hooks/hooks.json` -- `hook-logger.sh` removed
    from the Stop event hook list (#138). The Stop event now has
    exactly one registered hook (`continue-orchestration.sh`),
    eliminating the empty-stdout-vote-vs-block-decision dispatcher
    ambiguity. Stop events are still logged because
    `continue-orchestration.sh` calls `uv run devbench log` directly.
  - `plugin/devbench/scripts/continue-orchestration.sh` -- BLOCK_JSON
    now emits both the legacy `{"decision":"block","reason":"..."}`
    shape AND the modern `{"hookSpecificOutput":{"hookEventName":"Stop","additionalContext":"..."}}`
    shape (#139). Forward-compat across Claude Code 2.x.
  - `plugin/devbench/skills/orchestrate/SKILL.md` -- new CRITICAL
    rule forbids ending a turn with prose narration of the next step
    (#140). Every turn MUST end with EITHER a tool call OR a
    `uv run devbench next` invocation; the recap-prose pattern
    ("Next: re-invoke executor") is forbidden because Claude Code
    interprets a turn-end-without-tool-call as agent-done before the
    Stop hook's block can re-prompt the model.
  Regression tests:
  `tests/unit/test_hooks_json_registration.py::TestStopEventSingleHookRegistration`,
  `tests/unit/test_continue_orchestration_hook.py::TestStopHookEnvelopeShape`,
  `tests/test_integration/test_orchestrate_skill_no_recap_anti_pattern.py`.
- **Recovery cascade no longer re-introduces Manifest Conflicts**
  (issue #136). `plugin/devbench/agents/task-factory.md` adds an
  explicit rule that spec-correction recovery tasks list ONLY the
  work-unit markdown file in their Changes Manifest -- never the
  source files referenced inside that markdown's Manifest table.
  Listing source files in a recovery task's Manifest re-introduced
  the very Manifest Conflict the recovery task was created to
  resolve. Live evidence: 2026-05-02 caylent-telemetry-spec
  E2-F3-S2-T5 was materialised to remove `pyproject.toml` +
  `Makefile` rows from E2-F3-S2-T1's Manifest table; the factory
  populated T5's own Manifest with those source files; the next
  validate-backlog reported the same Manifest Conflict on
  pyproject.toml (5 claimants including T5) and Makefile (T1 + T5).
  New regression test
  `tests/test_integration/test_task_factory_spec_correction_scope.py`
  pins the rule by-content.
- **manifest-amender now rejects amendments that would create a
  Manifest Conflict** (issue #137). New pre-filter rule scans every
  other work-unit's Manifest before approving an amendment that adds
  a file. If the file is already claimed: REJECT (or, when the
  conflict task is in a terminal state and the new row is `Modify`,
  ALLOW with a `[CONFLICT_AUTODEP]` audit comment recommending the
  operator add a dep edge). Prevents new conflicts from being
  authored in the first place, making the recovery cascade the
  exception rather than the norm. New regression test
  `tests/test_integration/test_manifest_amender_pre_conflict.py`
  pins the rule by-content.
- **`devbench hook-tail` description column no longer wraps onto a
  timestamp-less continuation line** (issue #133). The `_description`
  helper now collapses every run of whitespace -- including embedded
  `\n` / `\r\n` / `\t` and runs of spaces -- to a single space then
  strips. The previous implementation passed the raw agent-supplied
  string through verbatim, so a multi-line description (e.g.
  `"# Check ...\n# The actual command..."`) broke the per-event
  single-line invariant. New regression tests
  `tests/unit/test_hook_tail.py::TestDescriptionNormalisation` pin
  the contract: single-line stays unchanged; `\n` / `\r\n` / `\t` /
  runs-of-spaces collapse; every fallback branch (description /
  command / file_path / JSON) collapses; `format_entry()` output
  contains zero `\n` regardless of input.
- **Manifest amender no longer rejects amendments on the grounds that
  requested files are not yet in the Manifest** (issue #127). The
  `plugin/devbench/agents/manifest-amender.md` SCOPE rule now contains
  an explicit "Critical (issue #127)" sub-block forbidding the
  circular rejection -- adding files to the Manifest is the entire
  purpose of an amendment. New regression test
  `tests/test_integration/test_manifest_amender_scope.py` pins the
  protective fragments by-content.
- **Stop hook block decision no longer dropped when `python3` is
  asdf-shimmed without a configured version** (issue #130).
  `plugin/devbench/scripts/continue-orchestration.sh` now uses `jq`
  for every JSON serialisation (BLOCK_JSON construction, state-file
  read, diagnostic-capture write); the previous `python3 -c '...'`
  invocations exited 126 under an asdf shim with no python version,
  causing the script to fall back to a literal
  `"(reason serialisation failed)"` reason text and silently skip
  the diagnostic-capture file. Operators saw exactly one
  `Stop hook blocked (1/5)` log entry per session followed by an
  unwanted Claude Code self-termination. New regression tests
  `tests/unit/test_continue_orchestration_hook.py::TestBlockJsonSerialisationRobustness`
  pin the new contract: zero `python3` invocations in the script,
  the BLOCK_JSON reason field is real text under a minimal PATH,
  and the diagnostic-capture file is written on every block.
- **Stop hook now reports the active task, not the alphabetically-
  first stale `in-progress` row** (issue #131).
  `continue-orchestration.sh` reads `<workspace>/logs/*.log` for the
  most recent `Branch ready: ... on <task_id>` or
  `Set <task_id> to 'in-progress'` entry and uses that ID as the
  active task. When multiple tasks are in-progress simultaneously,
  the reason text now lists every in-progress ID with the active
  task named first and the rest enumerated as
  `(also in-progress: ID2, ID3)`. Falls back to the existing
  `head -1` BACKLOG row only when no log entry parses (fresh
  checkout, never-launched workspace). New regression tests
  `tests/unit/test_continue_orchestration_hook.py::TestActiveTaskSelection`
  pin all four scenarios (log-driven pick, multiple-in-progress
  surfacing, fresh-checkout fallback, both log-line shapes accepted).
- **`hook-tail` and other stream-rendering commands no longer emit a
  startup banner on stderr** (issue #132). The
  `judges.log_setup` "Logging to stderr and ..." informational line
  was demoted from INFO to DEBUG, so the default DEVBENCH_LOG_LEVEL=INFO
  run is silent. Operators who want the banner back can set
  DEVBENCH_LOG_LEVEL=DEBUG. New regression tests
  `tests/test_log_setup.py::TestStartupBannerDemoted` pin both the
  silent-at-INFO and visible-at-DEBUG behaviours.
- **Stop-hook block decision honored under asdf-shimmed workspaces**
  (issue #130 / #131 -- already shipped in v-next on 04457b2;
  see prior bullets). Follow-up clean-up included in this release.
- **`gh pr create` no longer fails with "a pull request already
  exists for this branch"** when git-ops runs a second time on the
  same branch (issue #129). New helper `GitOpsService.find_open_pr`
  queries `gh pr list --head <branch> --state open` first; if a PR
  exists, `create_pr` returns its URL and skips the create call.
  Triggers covered: REFACTOR cycle after REVIEW_PASS, executor fix
  triggered by `pr_review_resolution` bot feedback, and CI-failure
  retry replay. New regression tests
  `tests/test_github/test_git_ops.py::TestCreatePrExistingPrReuse`
  pin the find/reuse/fall-through paths plus defensive cases
  (gh failure, malformed JSON).
- **Executor no longer acts on the content of REVIEW_PASS verdicts**
  (issue #128). Prompt updates: SKILL.md step 7 now contains an
  explicit "CRITICAL (issue #128)" rule that REVIEW_PASS is
  terminal; executor.md gains a parallel "REVIEW_PASS verdicts are
  terminal" section listing the three (and only three) legitimate
  executor-invocation triggers. Informational content in PASS
  verdicts (MEDIUM-severity notes, refactor suggestions) MUST NOT
  trigger additional executor work cycles -- only REVIEW_FAIL or
  git-ops exit codes 2 / 3 do. New regression tests
  `tests/test_integration/test_executor_review_pass_terminality.py`
  pin both prompts by-content.

### Added

- **Topological sort for parallel-task candidates** (issue #121).
  `BacklogParser.get_parallel_candidates` now orders actionable
  tasks by their topological depth in the full dep-DAG (shallow
  first), with a stable lexicographic `id` tiebreaker within each
  depth band. A task with zero declared dependencies (depth 0)
  precedes a task with one transitive dependency (depth 1), which
  precedes a task with two (depth 2). The "build-order foundation
  first" intuition holds even when most ancestors are already
  ``done``. Cycle protection collapses self-loops to depth 0
  without recursing infinitely; unresolvable IDs add 1 depth band
  per declared dep but do not crash. New regression tests
  `tests/test_backlog/test_parser.py::TestGetParallelCandidatesTopologicalOrder`
  pin all five scenarios.
- **Per-judge executor retry budgets** (issue #122). New optional
  YAML map `max_executor_retries_per_judge` lets operators tune
  retries per failing judge (e.g., 20 retries for flakey
  test_review, 2 for stable doc_review) without raising the global
  cap. Schema validation rejects unknown judge names; runtime
  helper `_load_per_judge_retries` defends against schema-bypass.
  SKILL.md step 6.d documents the consumption rule (per-judge
  budget when listed, fall back to `max_executor_retries`).
  New regression tests
  `tests/test_config_loader.py::TestPerJudgeRetriesConfig` cover
  global-only, per-judge override, schema rejection, runtime helper
  rejection of malformed inputs.
- **Per-role cost breakdown helper for `devbench report`**
  (issue #123). New `_parse_transcript_metrics_by_role` returns a
  dict mapping role name -> `HookLogTotals` by reading each
  transcript message's `attributionAgent` field. Subagent
  attributions are normalised to the canonical judge names
  (`devbench:code-reviewer` -> `code_review`); messages with no
  attribution land in the `orchestrator` bucket. Aggregate-row
  contract holds: summed totals across all roles equal what
  `_parse_transcript_metrics` returns. New regression tests
  `tests/test_reporting/test_report.py::TestTranscriptParsing`
  cover bucketing, aggregate-row preservation, missing-dir
  handling, and the role-name normalisation table.
- **`hook_tail` column caps configurable** (issue #134). Four
  module-level constants in `src/devbench/hook_tail.py`
  (`AGENT_WIDTH`, `TOOL_WIDTH`, `DESCRIPTION_MAX`,
  `STDOUT_PREVIEW_MAX`) are now resolved env > YAML > default at
  module import. New top-level `hook_tail:` block in
  `backlog/config/devbench.yaml`; new `DEVBENCH_HOOK_TAIL_*` env-var
  overrides. **Default `DESCRIPTION_MAX` bumped from 100 to 120** so
  multi-word agent descriptions are less likely to truncate
  mid-clause; other three defaults unchanged (12 / 8 / 80).
  `EVENT_WIDTH` stays a `hook_tail.py`-local constant -- the arrow
  column is intrinsic to the format. New regression tests
  `tests/unit/test_hook_tail.py::TestHookTailColumnConfig`
  + `tests/test_config_loader.py::TestHookTailConfig` cover global-
  only / full-override / partial-override / schema rejection of
  non-positive caps and unknown keys.
- **Data-residency and fast-mode multipliers applied per-call**
  (issue #124). `_compute_cost` now accepts
  `data_residency_multiplier` (default 1.10 from
  `DEFAULT_DATA_RESIDENCY_MULTIPLIER`, configurable via YAML
  `report.data_residency_multiplier`) and `fast_mode_multiplier`
  (default 6.0 from `DEFAULT_FAST_MODE_MULTIPLIER`, new YAML field
  `report.fast_mode_multiplier`). Token volumes from entries with
  `usage.inference_geo` set are tracked separately
  (`us_only_*_tokens`) from baseline; same for
  `usage.speed == 'fast'` (`fast_*_tokens`). Multipliers compose
  with cache + base-rate multipliers (apply after cache scaling,
  before discount) per AC-FUNC-003. New regression tests
  `tests/test_reporting/test_report.py::TestAccurateCost::test_data_residency_multiplier_applies_to_us_only_subset`
  + the fast-mode + composition + default-no-boost siblings cover
  all four AC scenarios.

### Renamed / Removed

- **`DEVBENCH_DISABLE_INLINE_ORPHAN_CLEANUP` env var renamed** to
  `DEVBENCH_INLINE_ORPHAN_CLEANUP` (truthy/falsy, no longer opt-out).
  The asymmetric "DISABLE" form is removed; canonical naming aligns
  with every other toggle.

### Migration notes

Operators upgrading from before this release:

1. **Remove the old env-var name** (`DEVBENCH_DISABLE_INLINE_ORPHAN_CLEANUP`)
   from any orchestrator launch scripts and replace with
   `DEVBENCH_INLINE_ORPHAN_CLEANUP=0` if you want to disable inline
   cleanup. (Most operators leave it unset; the default is on.)
2. **Decide on CI-retry**: the new default is on. To opt out for a
   specific backlog, add `git_ops.ci_failure_retry: false` to that
   backlog's `backlog/config/devbench.yaml`.
3. **Optionally migrate env-var exports to YAML**: every toggle that
   was previously env-only in PR-119 now lives in
   `backlog/config/devbench.yaml`. Workspace-stable behaviour belongs
   there; env vars remain available as per-launch overrides. See
   `sample-config.yaml` for the canonical shape.
4. **Optional: enable PR review-comment polling (issue #116)** if your
   target repo has Copilot / Q-Dev / similar review bots. Set
   `git_ops.pr_review_resolution.enabled: true` and populate the
   `agents:` allowlist.
5. **Issue #101 (pause-before-merge)** lands the YAML schema
   (`git_ops.pause_before_merge`) in this release; the runtime
   implementation ships in a follow-up commit on this branch (or in
   the next release if the follow-up is deferred).
6. **Issue #259 (task-factory on by default, ADR-32)**: if your backlog
   omits `task_factory` from `backlog/config/devbench.yaml`, the loop now
   runs and materialises draft work-unit `.md` files after amendment
   rejects, where before it produced none. A freshly materialised
   draft's initial status is, and has always been, governed by
   `backlog.default_status_for_new_work_units` (default `in-queue`,
   AC-189-8) -- unaffected by this change -- so drafts remain immediately
   actionable unless you also set that key to `draft` for an explicit
   human-review gate. Relative to released tag `0.1.0`, TWO defaults
   changed: `task_factory.enabled` flipped `false` -> `true`, and
   `task_factory.auto_accept_proposals` flipped `true` -> `false`. To
   restore the pre-flip `enabled` behavior (loop off unless explicitly
   turned on), add `task_factory.enabled: false`. `auto_accept_proposals`
   going `true` -> `false` has two concrete effects an upgrading operator
   will notice: (a) `write-proposal` no longer synchronously
   materialises-and-promotes the proposal it just wrote inside the same
   invocation (`auto_cascade` in its output JSON now reads `"disabled"`),
   so a freshly written proposal now waits for the next `sweep-proposals`
   tick instead of being actionable immediately; and (b)
   `sweep-proposals` no longer auto-promotes a draft explicitly left at
   `## Status: proposed` (a legacy/hand-edited-draft case, not something
   the normal materialise path produces). To restore the
   `0.1.0`-released `auto_accept_proposals: true` behavior (both effects
   together), add `task_factory.auto_accept_proposals: true` (alongside
   `enabled: true`, its default) -- **warning:** this reintroduces the
   orphan-auto-promote path that `0.1.0` shipped and that D-11
   deliberately turns off by default; it was never a general
   auto-promote-everything behavior even at `0.1.0`, since freshly
   materialised drafts have always bypassed `proposed` status entirely.
   Only set it if you have already confirmed that skipping review of
   orphaned `proposed` drafts is acceptable. See
   `docs/adr/32-task-factory-default-on.md` for the full decision record
   and the `manifest_amendment`-interaction contract.

7. **Review topology changed (ADR-33).** The orchestrate skill now
   dispatches the four `review_team` judges directly as first-level
   sub-agents; `review-supervisor` is retained and is still invoked, but
   only afterward, as a non-spawning aggregator that reads the four
   judges' already-persisted verdicts and reports a consolidated result --
   it never dispatches them itself. No action is required for standard
   workspaces -- the `agents.review_supervisor` config key still parses
   and still selects the model used for that aggregation pass. If you
   maintain a **custom orchestrate skill or a forked plugin** that invokes
   `review-supervisor` expecting it to run the review fan-out itself, that
   path is now blocked by `guard-review-supervisor-scope.sh` (exit 2) and
   must be updated to dispatch the judges directly, letting
   `review-supervisor` aggregate afterward. A missing verdict from any
   required judge is now a hard review failure rather than an implicit
   pass, so a work unit that previously slipped through on a partial round
   will now fail review until every judge reports.
8. **Optional: isolate stop-hook state.** The Stop hook's state file
   defaults to `/tmp`, which is shared machine-wide. If you run the test
   suite on a host that also runs a live orchestrator, set
   `DEVBENCH_STOP_HOOK_STATE_DIR` to a private directory in the test
   environment. Leaving it unset preserves the previous `/tmp` paths
   exactly.

### Known follow-ups (this branch / next release)

- Implement `git_ops.pause_before_merge: true` runtime path (#101).
  Schema + validation already in place; runtime branch in
  `cmd_git_ops` + `cmd_check_merge` ships next.
- Live integration smoke covering all four exit codes (0/1/2/3) +
  inline cleanup chore-commit shape on a fresh tmp workspace.
