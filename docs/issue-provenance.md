# Issue Provenance Map

Spec `integration-reality-gates-hardening.md` section 4.12 (PM-secondary-2) requires a single map
tying every gate this campaign hardens to the internal-backlog issue that requested it, the source
pull request it was hardened from, any `caylent-solutions/devbench`-repo issue it is tied to, and the
spec section that defines it. The map exists because the eight source pull requests carried
fabricated `#01`-`#08` placeholder citations authored before the real internal-backlog issues
existed (section 4.12); `tests/test_docs/test_issue_provenance.py` walks exactly six root/extension
pairs -- `docs/*.md`, `plugin/*.md`, `plugin/*.sh`, `plugin-authoring/*.md`, `src/devbench/*.py` and
`tests/*.py` -- plus `CHANGELOG.md`, for the fully-qualified
`caylent-solutions/devbench-internal-backlog#<N>` citation form and the fabricated zero-padded
`#01`-`#08` form, and asserts every one resolves against a row in this table -- proving none of the
placeholders survived the Epic 1 cherry-pick within that walked surface (AC-3).

E11's closure work units (spec section 4.13) read this table verbatim to know which issues, in
which repo, to close, and in what order the closing PR body cites them -- this table is the input to
that closure work, not decoration. The five Section 15 follow-up rows (`#356`-`#360`) are
excluded from that closure input set: they are deliberately OPEN by design and must NEVER be
closed by E11 closure work units -- see the `## Follow-up issues` subsection below for the
full record of why each stays open.

| Gate | Internal Issue | Source PR | Devbench Issues | Spec Section |
|------|-----------------|-----------|------------------|--------------|
| `reachability` | `caylent-solutions/devbench-internal-backlog#10` | `caylent-solutions/devbench#315` | none | `4.4` |
| `composition_root` | `caylent-solutions/devbench-internal-backlog#11` | `caylent-solutions/devbench#316` | none | `4.9` |
| `ancestry` | `caylent-solutions/devbench-internal-backlog#12` | `caylent-solutions/devbench#317` | none | `4.5` |
| `shared_file_impact` | `caylent-solutions/devbench-internal-backlog#13` | `caylent-solutions/devbench#318` | `caylent-solutions/devbench#360` | `4.6` |
| `layout_geometry` | `caylent-solutions/devbench-internal-backlog#14` | `caylent-solutions/devbench#319` | `caylent-solutions/devbench#358` | `4.9` |
| `newly_reachable_paths` | `caylent-solutions/devbench-internal-backlog#15` | `caylent-solutions/devbench#320` | none | `4.9` |
| `write_path_audit` | `caylent-solutions/devbench-internal-backlog#16` | `caylent-solutions/devbench#321` | none | `4.8` |
| `fixture_consistency` | `caylent-solutions/devbench-internal-backlog#17` | `caylent-solutions/devbench#322` | none | `4.7` |
| harness guard fixes landed on `feat/bug-closure` ahead of this campaign (D-12): `guard-bash.sh`'s `git checkout --theirs`/`--ours` permit (`#335`) and the `.devbench/active-work-unit` claim marker (`#336`) | none | none | `caylent-solutions/devbench#335`, `caylent-solutions/devbench#336`, `caylent-solutions/devbench#357` | `4.12` |
| assert-tests-pass.sh fail-open rework | none | none | `caylent-solutions/devbench#356` | `15` |
| guard-git-stage rule-1 cwd/-C quirks | none | none | `caylent-solutions/devbench#357` | `15` |
| real-browser layout machine-verification design | none | none | `caylent-solutions/devbench#358` | `15` |
| build-time generation of rubric bodies | none | none | `caylent-solutions/devbench#359` | `15` |
| auto-registry fan-in tuning telemetry | none | none | `caylent-solutions/devbench#360` | `15` |

Column notes:

- **Gate** -- one of the eight canonical gate names (`devbench.constants.GATE_NAMES`) for the first
  eight rows; a short descriptive label for every other row, since those rows track issues that are
  not tied to a single gate.
- **Internal Issue** -- the fully-qualified `caylent-solutions/devbench-internal-backlog#<N>` issue
  this row was requested by. `#10`-`#17` are the eight gate issues (spec sections 4.4-4.9); the five
  Section 15 follow-up rows carry `none` here, not a number, because they were never requested by an
  internal-backlog issue -- E11-F1-S1-T3 filed each directly as a `caylent-solutions/devbench`-repo
  issue (see the Devbench Issues column), the same shape as the harness-guard-fixes row above them.
- **Source PR** -- the fully-qualified `caylent-solutions/devbench#<N>` draft pull request the gate
  was hardened from. `#315`-`#322` is the set of eight source PRs; spec section 4.14 defines a
  different, non-ascending landing order for cherry-picking them (`#321` -> `#317` -> `#320` ->
  `#315` -> `#318` -> `#322` -> `#316` -> `#319`), not the order this column is listed in.
- **Devbench Issues** -- any `caylent-solutions/devbench`-repo issue tied to this row. `#335` is
  `guard-bash.sh`'s `git checkout --theirs`/`--ours` permit and `#336` is the
  `.devbench/active-work-unit` claim marker; both are harness guard fixes that landed on
  `feat/bug-closure` before this campaign's branch was cut (spec section 1.2, decision D-12); they
  are not tied to any single gate, so they carry their own row rather than being attached to one of
  the eight gate rows. `#357` (`guard-git-stage rule-1 cwd/-C quirks`) is cross-referenced onto this
  same row because Section 12 documents that follow-up as a secondary finding of `#336`. `#358`
  (`real-browser layout machine-verification design`) and `#360` (`auto-registry fan-in tuning
  telemetry`) are additionally cross-referenced onto the `layout_geometry` and `shared_file_impact`
  gate rows respectively, because Section 15's follow-up list ties each to that specific gate; every
  filed follow-up also keeps its own dedicated row below (`#356`, `#357`, `#358`, `#359`, `#360`),
  each OPEN by design (spec Section 15 files these as tracked future work, not work this campaign
  closes) -- see the `## Follow-up issues` subsection for the full record.
  `caylent-solutions/devbench-internal-backlog` (this workspace's separate internal-backlog repo)
  never appears in this column; that repo's issues live only in the Internal Issue column.
- **Spec Section** -- the `spec/integration-reality-gates-hardening.md` heading that defines this
  row's requirement.

The eight source pull requests are `caylent-solutions/devbench#315`-`#322`. Each was drafted against
a placeholder internal-backlog issue that did not exist yet, expressed as a bare, zero-padded
two-digit citation (`#01`-`#08`). Those placeholders were corrected to the real
`caylent-solutions/devbench-internal-backlog#10`-`#17` citations during the Epic 1 cherry-pick
procedure (spec section 4.14); `tests/test_docs/test_issue_provenance.py` is the mechanical proof
that none of the fabricated forms survived, walking exactly six root/extension pairs -- `docs/*.md`,
`plugin/*.md`, `plugin/*.sh`, `plugin-authoring/*.md`, `src/devbench/*.py` and `tests/*.py` -- plus
`CHANGELOG.md` (excluding this map and its own test module), for the fully-qualified internal-backlog
citation form and the fabricated zero-padded form, and asserting every one resolves against a row in
this table. Devbench-repo issue citations (e.g. `caylent-solutions/devbench#228`), any file outside
those six root/extension pairs and `CHANGELOG.md` -- including Markdown files under
`tests/fixtures/`, shell scripts outside `plugin/`, and JSON config files such as
`src/devbench/config-schema.json` -- are outside this walk's scope.

## Closure log

Spec section 4.13 (AC-23) closure evidence for the eight
`caylent-solutions/devbench-internal-backlog` gate issues, produced by E11-F1-S1-T1.
Each row below is transcribed directly from live `gh` command output captured while
this unit executed against `caylent-solutions/devbench-internal-backlog`, not
reconstructed or assumed. The state-before value is what `gh issue view <n>` reported
immediately before this unit's first action on that issue; the comment URL is the
exact URL `gh issue comment` returned on success; the closed-at value is the
`closedAt` field `gh issue view <n> --json state,closedAt` reported immediately after
the close call. This is a plain list, not a fifth pipe-table, so the resolvability
parser above (which treats every `|`-prefixed line after the header as another data
row of the single provenance table) never mistakes it for additional provenance rows.

- Issue `caylent-solutions/devbench-internal-backlog#10` (`reachability`, E3): state
  before = OPEN; comment =
  https://github.com/caylent-solutions/devbench-internal-backlog/issues/10#issuecomment-5473216108;
  closed at = 2026-08-31T03:11:28Z.
- Issue `caylent-solutions/devbench-internal-backlog#11` (`composition_root`, E9):
  state before = OPEN; comment =
  https://github.com/caylent-solutions/devbench-internal-backlog/issues/11#issuecomment-5473216872;
  closed at = 2026-08-31T03:11:34Z.
- Issue `caylent-solutions/devbench-internal-backlog#12` (`ancestry`, E4): state
  before = OPEN; comment =
  https://github.com/caylent-solutions/devbench-internal-backlog/issues/12#issuecomment-5473217644;
  closed at = 2026-08-31T03:11:41Z.
- Issue `caylent-solutions/devbench-internal-backlog#13` (`shared_file_impact`, E5):
  state before = OPEN; comment =
  https://github.com/caylent-solutions/devbench-internal-backlog/issues/13#issuecomment-5473218436;
  closed at = 2026-08-31T03:11:49Z.
- Issue `caylent-solutions/devbench-internal-backlog#14` (`layout_geometry`, E10):
  state before = OPEN; comment =
  https://github.com/caylent-solutions/devbench-internal-backlog/issues/14#issuecomment-5473219203;
  closed at = 2026-08-31T03:11:56Z.
- Issue `caylent-solutions/devbench-internal-backlog#15` (`newly_reachable_paths`,
  E8): state before = OPEN; comment =
  https://github.com/caylent-solutions/devbench-internal-backlog/issues/15#issuecomment-5473219990;
  closed at = 2026-08-31T03:12:03Z.
- Issue `caylent-solutions/devbench-internal-backlog#16` (`write_path_audit`, E7):
  state before = OPEN; comment =
  https://github.com/caylent-solutions/devbench-internal-backlog/issues/16#issuecomment-5473220853;
  closed at = 2026-08-31T03:12:10Z.
- Issue `caylent-solutions/devbench-internal-backlog#17` (`fixture_consistency`,
  E6): state before = OPEN; comment =
  https://github.com/caylent-solutions/devbench-internal-backlog/issues/17#issuecomment-5473221570;
  closed at = 2026-08-31T03:12:17Z.

No mapped issue was observed already CLOSED before this unit acted, so no row above
carries a skip reason; all eight received the comment-then-close sequence. Every
comment body matches the spec 4.13 template verbatim (re-verified against a live
`gh api repos/caylent-solutions/devbench-internal-backlog/issues/10/comments` read):

```
Fixed by <unit-ids> on branch `candidate-release/integration-reality-gates` (combined PR: opens at finalize). The fix goes live when that branch merges to main.
```

with `<unit-ids>` populated from the terminal (`done`) work-unit ids under the
mapped epic (E3, E9, E4, E5, E10, E8, E7, E6 respectively; declined siblings
within those epics, which were never implemented, are excluded from each
list). The combined PR for
`candidate-release/integration-reality-gates` does not exist yet at the time this
unit ran (`git_ops.defer_pr: true`, `auto_finalize: true`; it is created once at
finalize), so every comment uses the DoR-approved literal substitute `opens at
finalize` for the PR field rather than a fabricated PR number or URL.

An idempotency pass was run after the eight closures above: `gh issue list
--repo caylent-solutions/devbench-internal-backlog --state all --json
number,state,title --limit 100` was re-queried for issues `#10`-`#17` and every one
reported `state: CLOSED`, so the re-run posted zero comments and issued zero close
calls (AC-FUNC-002). A follow-up `gh issue view <n> --repo
caylent-solutions/devbench-internal-backlog --json state,comments` for each of the
eight issues confirmed `state: CLOSED` and exactly one comment whose body matches the
template above; each issue additionally carries one earlier, pre-existing comment
(a bare source-PR link, e.g. `https://github.com/caylent-solutions/devbench/pull/315`
on `#10`) authored before this unit ran, which does not match the Section 4.13
template and is therefore not counted against AC-FUNC-001's "exactly one comment
matching the Section 4.13 template" requirement.

Spec section 4.13 (AC-23) closure evidence for the two `caylent-solutions/devbench`-repo
issues in this table's "Devbench Issues" column (`#335`, `#336`), produced by
E11-F1-S1-T2. Both were observed via a live `gh issue view <n> --repo
caylent-solutions/devbench --json number,state,title,comments` call immediately before this
unit's first action; both already reported `state: CLOSED` with zero comments, so neither
received the Section 4.13 branch-note comment or a `gh issue close` call -- each is recorded
below with its skip reason instead (AC-FUNC-004), matching Approach step 3's "issues observed
CLOSED are skipped with an audit note."

- Issue `caylent-solutions/devbench#335` (`guard-bash.sh` over-blocked `git checkout
  --theirs`/`--ours` via the bare `git checkout --` substring pattern): state before =
  CLOSED; skip reason = already closed before this unit ran. The fix landed in commit
  `8ac9c07` on `feat/bug-closure` (spec section 1.2, decision D-12) and was inherited by
  `candidate-release/integration-reality-gates` when this campaign's branch was cut from
  `feat/bug-closure` tip `8ac9c07` -- the fix therefore predates any campaign work-unit id, so
  no `<unit-ids>` value exists to cite in a comment; no comment posted, no close call issued.
- Issue `caylent-solutions/devbench#336` (`guard-git-stage.sh` rule 2 manifest-scope
  enforcement was dead code because `CURRENT_WORK_UNIT_FILE` was never set in production):
  state before = CLOSED; skip reason = same as `#335` -- fixed by the same commit `8ac9c07`
  on `feat/bug-closure`, already CLOSED at branch-cut; no comment posted, no close call
  issued.

An idempotency pass was run after the observation above: `gh issue view 335 --repo
caylent-solutions/devbench --json number,state,comments` and the equivalent call for `#336`
were re-queried and both still report `state: CLOSED` with zero comments, so the re-run
posted zero further comments and issued zero close calls (AC-FUNC-004).

The Section 4.13 release-notes PR-body block this unit authored is
`docs/release-notes/candidate-release-integration-reality-gates.md` (spec 5.6): it carries the
PR title line, a per-epic summary and a closing-keyword block with one `Fixes
caylent-solutions/devbench-internal-backlog#<n>` line for each of `#10`-`#17` above and one
bare `Fixes #<n>` line for each of `#335`, `#336` above -- ten lines total, matching this
table's mapped-issue count exactly (AC-24). The five Section 15 follow-up rows
(`#356`-`#360`) are excluded from that mapped-issue count: they are deliberately OPEN
tracked future work this campaign explicitly declined to implement, not issues this
campaign closes, so no `Fixes` line exists for any of them anywhere in that file (see
the `## Follow-up issues` subsection below). That file also names the Phase 5 operator handoff
step and the `git_ops.provenance_path` config key that supersedes it for future runs (spec
section 6).

## Follow-up issues

Spec Section 15 ("Future work (explicitly deferred)") and Section 12 ("Out of
scope (this spec)") names five pieces of deferred work and requires each to be
FILED as its own `caylent-solutions/devbench` issue during E11
(E11-F1-S1-T3). Each row below is transcribed directly from live `gh issue
view <n> --repo caylent-solutions/devbench --json number,title,state,url`
output captured immediately after this unit's `gh issue create` call, not
reconstructed or assumed. Every issue is deliberately left OPEN: unlike the
mapped gate issues E11-F1-S1-T1 and E11-F1-S1-T2 closed, these are tracked
future work items this campaign explicitly declined to implement, so closing
them here would misrepresent the campaign as having done the deferred work.
This is a plain list, not a sixth pipe-table, so the resolvability parser
above (which treats every `|`-prefixed line after the header as another data
row of the single provenance table) never mistakes it for additional
provenance rows.

- Item: assert-tests-pass.sh fail-open rework. Issue:
  `caylent-solutions/devbench#356`
  (https://github.com/caylent-solutions/devbench/issues/356). State: OPEN.
  Deferring spec section: `15`.
- Item: guard-git-stage rule-1 cwd/-C quirks. Issue:
  `caylent-solutions/devbench#357`
  (https://github.com/caylent-solutions/devbench/issues/357). State: OPEN.
  Deferring spec section: `15`.
- Item: real-browser layout machine-verification design. Issue:
  `caylent-solutions/devbench#358`
  (https://github.com/caylent-solutions/devbench/issues/358). State: OPEN.
  Deferring spec section: `15`.
- Item: build-time generation of rubric bodies. Issue:
  `caylent-solutions/devbench#359`
  (https://github.com/caylent-solutions/devbench/issues/359). State: OPEN.
  Deferring spec section: `15`.
- Item: auto-registry fan-in tuning telemetry. Issue:
  `caylent-solutions/devbench#360`
  (https://github.com/caylent-solutions/devbench/issues/360). State: OPEN.
  Deferring spec section: `15`.

Each issue body names the deferring spec section (Section 15, cross-referenced
from Section 12 for the four items Section 12 also lists by name -- the
fail-open rework, the guard-git-stage rule-1 quirks, the real-browser
machine-verification design and the build-time-generation-beyond-vocabulary
item; the auto-registry fan-in telemetry item is Section-15-only and carries
no Section 12 cross-reference), the
motivating finding or issue (`L-claude-md-24` for the fail-open rework;
`caylent-solutions/devbench#336` for the guard-git-stage rule-1 quirks;
`caylent-solutions/devbench-internal-backlog#14` for the layout
machine-verification design; the E2 vocabulary-only generation scope for the
rubric-bodies item; `caylent-solutions/devbench-internal-backlog#13` and
decision D-9 for the auto-registry fan-in telemetry item), and the reason the
work was excluded from this campaign (AC-FUNC-001).

An idempotency pass was run after filing: `gh issue list --repo
caylent-solutions/devbench --state all --search "\"<title>\" in:title" --json
number,title,state` was re-queried for each of the five titles above and each
returned exactly the one issue number already filed (`#356`-`#360`), so a
second filing pass creates zero issues and reuses the existing numbers
(AC-FUNC-003).
