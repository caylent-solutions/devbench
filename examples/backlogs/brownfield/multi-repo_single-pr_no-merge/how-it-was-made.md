# How this backlog was made

A complete record of the operator + Claude (Opus 4.7, 1M context) session that
produced this example. Every step is reproducible against any well-locked
spec; the choices below are notable wherever they generalise beyond this
specific backlog.

The work happened on **2026-05-11** in a single day. The spec was authored
between roughly **9 AM and 2 PM** (~5 hours). The backlog generation followed
in the same day's afternoon and evening. Final deliverable: **207 work units
across 13 epics + 14 features** authored from a single locked spec, passing
`devbench validate-backlog` clean.

The next section summarizes the morning's spec-authoring iteration.
Subsequent phases (0 through 7) document the backlog-generation work that
followed in the afternoon.

---

## How the spec itself was authored (summarised)

> **Note on this section.** What follows is a heavily cleaned-up and truncated
> summary of the spec-authoring journey. The real path was longer, noisier,
> and full of dead ends; the version here keeps only the load-bearing
> beats so readers can see *that* the iteration happened, even if every
> turn is not reproduced verbatim. If you are using this example to model
> your own spec-authoring cadence, expect your spec to take roughly the
> same shape -- many small loops, not one big bang.

### Why the spec needed to be locked before backlog generation

DevBench backlogs are deterministic promotions of a spec into work units.
Every acceptance criterion in every task traces back to a spec line. If the
spec is still drifting, the backlog drifts too -- and you discover the
drift only after authoring 200+ tasks. Locking the spec first is the
cheapest possible way to avoid that.

### Lead time

- **Wall-clock:** ~5 hours, roughly 9 AM to 2 PM on 2026-05-11.
- **Single sitting:** the spec was authored in one continuous session,
  not over multiple days. The 5 hours included sketching, expanding,
  re-reading, pushing back, re-cutting, and the final lock review.
- **Author pairing:** operator + Claude (Opus 4.7, 1M context). Neither
  side was a passive participant. The operator drove every section's
  intent; Claude expanded, cross-checked, and stress-tested the prose.
  Critically, **both parties had to re-read the spec at every iteration** --
  the operator to catch drift from intent, and Claude to catch cross-
  section inconsistencies that the previous turn's edits introduced.
  Within a 5-hour window the re-reading still mattered: changes in
  Section 11 routinely invalidated assumptions made in Section 4 an hour
  earlier, and the only way to catch that was for both sides to re-read
  the relevant context on the next turn.

### Iteration pattern (the loop that repeated, compressed into 5 hours)

Every spec section went through a variant of this six-step loop. With
the whole session in one sitting, the "park for a day" cadence of a
typical multi-day spec compressed into "work on a different section
for 20-30 minutes, then come back" -- but the re-read discipline was
the same.

1. **Operator sketches the intent** in 1-3 sentences ("I want `kanon list`
   to enumerate the catalog entries in the current `<catalog-source>`,
   with a `--format` flag for table / json output").
2. **Claude re-reads the relevant spec context** (existing sections,
   cross-referenced primitives, related open questions) before writing,
   so the new prose lines up with what is already in place.
3. **Claude expands** the sketch into a full spec section with: command
   signature, every flag, env var coupling, output examples, error cases,
   exit codes, and cross-references to other sections.
4. **Operator reads it cold**, pushes back on anything surprising ("why
   is `--format=json` the default?", "this flag overlaps with
   `--catalog-source` from Section 3"), and asks for tightening. The
   re-read is the work -- typing the response is the easy part.
5. **Claude re-cuts the section** with the corrections, re-reading
   neighbouring sections so the corrections do not contradict something
   set earlier in the same session. Open questions that surface here go
   into Section 13 explicitly rather than being hand-waved past.
6. **Move on, then come back.** Operator works on a different section,
   and when they return both sides re-read the whole spec end-to-end
   (not just the changed section) before deciding whether to accept or
   kick back to step 4.

The pattern is unglamorous. The wins come from **not committing to
ambiguous wording** and from **never assuming the previous read is
still valid**, even when the previous read was twenty minutes ago.
Every "wait, what does X actually mean here?" that either party caught
during a re-read became an explicit definition in the spec, which later
became a clear acceptance criterion in the backlog.

### Notable decision points the spec resolved before lock

These are the load-bearing decisions captured in the locked spec. Each
one shaped multiple backlog tasks downstream. The list quotes the
locked-state of each decision rather than reconstructing the path it
travelled to get there -- the cleaned-up summary cannot fairly
reproduce the dead ends that occurred inside the 5-hour window.

- **Resolver semantics for bare PEP 440 versions** (Section 4.0). The
  locked spec accepts full `packaging.version.Version` grammar
  (pre-releases like `1.0.0a1`, local-version segments like
  `1.0.0+local`, and so on) rather than the narrow `\d+\.\d+\.\d+`
  shape that today's `_normalize_bare_semver_to_tag` recognises.
  E1-F1-S1-T1 widens the regex.

- **Lockfile schema versioning** (Section 5). Locked spec carries an
  explicit `lockfile_version = 1` field plus forward-compatibility
  rules, plus the `kanon_hash` deterministic hash and an explicit
  enumeration of what is and is not part of the hash input.

- **Bootstrap deprecation policy** (Section 10). Locked version uses a
  deprecation shim that prints `DEPRECATED:` on stderr and exits 3
  with an actionable remediation line pointing at `kanon add`, giving
  downstream consumers a one-release migration window rather than a
  hard break.

- **Provider-agnosticism enforcement** (Section 3.6 + Section 10). The
  rule is paired with a CI requirement (Section 10 line 1023) and a
  Section 12 acceptance item. This was the gap E1-F5 filled in the
  final backlog audit (Phase 6 below).

- **Soft-spot rules 1-5 for catalog metadata** (Section 3.5). Locked as
  a numbered five-rule contract that the catalog-audit subcommand
  (E5-F2) enforces and the per-entry migration tasks (E11) satisfy.
  The numbering matters: downstream backlog tasks reference soft-spot
  rules by number.

- **Shell completion cache trust model** (Section 11.4 + Section 3.6).
  Locked spec mandates `${KANON_CACHE_DIR}` with mode 0700 directories
  and 0600 files, plus an output-sanitization step that rejects shell
  metacharacters from completion candidates. This drives the
  E7-F3-S1-T1 / E7-F3-S1-T4 implementation tasks.

### Section 13 ("Open questions") as a forcing function

The spec keeps an explicit "Open questions" section throughout authoring.
Every time the operator and Claude could not converge on a behaviour,
the question got written down in Section 13 instead of hand-waved past.
Lock criterion: **Section 13 must be drained (every item either resolved
into the relevant section or moved to Section 15 "Future work") before
the spec is considered locked.** The locked version of Section 13
contains only items deferred to Section 15.

### How the lock was called

The spec was considered locked when:

1. Section 13 was drained (every open question either resolved into the
   relevant section or moved to Section 15).
2. The operator could re-read the spec cold end-to-end without
   surprising themselves -- no "wait, that's not what I meant" moments.
3. Claude could re-read the spec cold end-to-end and produce a section-
   by-section paraphrase that matched the operator's mental model
   without prompting -- the practical test that the prose was
   self-consistent and not relying on shared verbal context.
4. Section 14 (CLI `--help` reference) compiled mentally: every command
   could be invoked with every flag, and the operator could predict the
   output shape without re-reading the rest of the spec.

At that point the spec was locked and the backlog-generation work
began. **Phase 0 below picks up from there:** even after the lock, one
final review pass surfaced six low-impact inconsistencies. Some drift
is unavoidable even inside a 5-hour authoring window, and the fix loop
is documented next.

---

## Phase 0 -- spec lock

**Starting point:** the operator had already authored
`spec/kanon-list-add-lock-features-spec.md` -- 15 sections covering resolver
semantics, every new command, the lockfile schema, env vars, error handling,
documentation inventory, catalog-repo migration, bootstrap deprecation, shell
completions, acceptance criteria, open questions, CLI `--help` reference, and
out-of-scope items.

**Pre-flight prompt:**

> "Give the spec a final review only for errors, consistency, logic gaps,
> cohesiveness, and completeness; ensure it describes a state that can be
> implemented successfully."

Claude read the spec, identified six minor inconsistencies (e.g., section
numbering drift between an introduced cross-reference and the actual section
position), and presented them as a punch list. The operator applied the fixes
and re-locked the spec. **Lesson:** a final spec review before backlog
authoring is cheap and catches errors that would otherwise propagate into 200+
work units.

---

## Phase 1 -- workspace layout

DevBench requires `JUDGE_WORKSPACE_ROOT` to be the **parent directory** that
holds both `BACKLOG.md` and the target repo clones as siblings. The kanon
backlog drives three target repos, so the layout is:

```
/workspaces/rpm-migration/
├── kanon/                              # repo 1 -- cloned separately
├── caylent-private-kanon/              # repo 2 -- cloned separately
├── kanon-claude-marketplaces/          # repo 3 -- cloned separately
└── kanon-deps-work/                    # JUDGE_WORKSPACE_ROOT
    ├── BACKLOG.md
    ├── backlog/
    ├── spec/
    ├── templates/
    ├── kanon -> ../kanon                              # SYMLINK
    ├── caylent-private-kanon -> ../caylent-private-kanon  # SYMLINK
    └── kanon-claude-marketplaces -> ../kanon-claude-marketplaces  # SYMLINK
```

**Symlink-versus-checkout choice:** DevBench docs describe a pattern where the
backlog lives in its own git repo and the target repos are siblings symlinked
in. The session used symlinks so the three target repos could be edited
independently (e.g., the user pulls latest in `kanon/` without touching
`kanon-deps-work/`).

```bash
cd kanon-deps-work
ln -s ../kanon kanon
ln -s ../caylent-private-kanon caylent-private-kanon
ln -s ../kanon-claude-marketplaces kanon-claude-marketplaces

git init    # the backlog itself is in git; the symlinks are .gitignored
```

`.gitignore` excludes the three symlinks, `.devbench/`, `logs/`, and Python
cruft so the backlog repo only tracks the authored content.

The operator corrected the commit author to `matthew.dresden@caylent.com` (not
the `userEmail` context default). One commit was already cut with the wrong
email; `git commit --amend --reset-author` was blocked by a `guard-destructive-git`
hook, so the legacy commit stayed and all subsequent commits used the correct
identity. **Lesson:** verify `git config user.email` before the first commit;
hooks may prevent reseating retroactively.

---

## Phase 2 -- devbench.yaml configuration

The operator's explicit directive:

> "The devbench YAML must explicitly set ALL features and toggles (no hidden
> defaults). Use single-PR mode that DOES make PRs, watches CI, and resolves
> CI issues. Turn ON all features that help: fix issues, propose work, fix
> blocks, cascading support. Anything turned OFF must be visible and
> explicitly toggled off."

Claude built `backlog/config/devbench.yaml` with every field in the
config-schema represented at its operator-chosen value. Each line that matches
a built-in default ends with the comment `(built-in default)` so reviewers can
distinguish authored values from defaults at a glance.

**Mode picked:** single-branch + `defer_pr` + `auto_finalize` + `auto_merge` +
`ci_failure_retry`. This is the canonical "single PR per repo, fully
automated" shape.

**Explicitly ON:**
- `git_ops.single_branch: feat/kanon-deps-work-2026-05`
- `git_ops.defer_pr: true`
- `git_ops.auto_finalize: true`
- `git_ops.auto_merge: true`
- `git_ops.ci_failure_retry: true`
- `git_ops.pr_review_resolution.enabled: true` + `decision_blocks: true`
- `manifest_amendment.enabled: true`
- `task_factory.enabled: true` + `auto_accept_proposals: true`
- `validate.check_orphan_path_tokens: true` (opt-in rule 20)

**Explicitly OFF (mutually exclusive with the chosen mode):**
- `git_ops.pause_before_merge: false` -- incompatible with `defer_pr`.
- `git_ops.local_only: false` -- we want a remote PR.
- `git_ops.update_submodule: false` -- target repos are not submodules.

The `allowed_orgs` list (`caylent-solutions`, `caylent`) is belt-and-suspenders
against a misconfigured `target_repo` pointing at the wrong org -- the config
loader refuses the run rather than silently accepting it.

---

## Phase 3 -- backlog architecture decisions

Before authoring tasks, two architecture decisions shaped everything that
followed.

### Decision 1: epic structure mirrors spec sections

The spec has natural section boundaries. The 13 epics map onto them:

| Epic | Title | Spec sections | Target repo |
|---|---|---|---|
| E1 | Resolver, URL canonicalization, @ splitter, shared CLI args | §3, §4.0, §4.5 | `caylent-solutions/kanon` |
| E2 | list / add / remove | §4.1-4.3 | `caylent-solutions/kanon` |
| E3 | Lockfile + install | §4.7, §5 | `caylent-solutions/kanon` |
| E4 | outdated / why | §4.4-4.5 | `caylent-solutions/kanon` |
| E5 | doctor / catalog audit / validate metadata | §4.6, §4.8, §4.9, §3.5 | `caylent-solutions/kanon` |
| E6 | Bootstrap deprecation | §10 | `caylent-solutions/kanon` |
| E7 | Shell completions | §11 | `caylent-solutions/kanon` |
| E8 | Doc set | §8 | `caylent-solutions/kanon` |
| E9 | --help snapshots | §14 | `caylent-solutions/kanon` |
| E10 | Caylent-private-kanon engineering + CI | §9a | `caylent/caylent-private-kanon` |
| E11 | Per-entry metadata migration | §9b Phase 1 | `caylent/caylent-private-kanon` |
| E12 | Delete legacy `catalog/` tree | §9b Phase 2 | `caylent/caylent-private-kanon` |
| E13 | kanon-claude-marketplaces audit + issue filing | §9c | `caylent/kanon-claude-marketplaces` |

Two epics (E1-F5, E1-F4-S1-T2) were added later during the final
spec-vs-backlog audit to fill gaps. See Phase 7 below.

### Decision 2: templates carry the repetitive rules

Two pieces of content appear verbatim in every work-unit file:

- `templates/code-standards-block.md` -- the 8 critical rules (no fallback
  logic, no silent failures, fail fast, no hard-coded values, no temporal
  logic, all code dynamic + input-driven, no bypass annotations, no
  em-dashes), plus architecture / testing / git / security rules. The
  operator's standing directive:

  > "I expect the most common rules to be repeated in each work unit, as
  > always, from CLAUDE.md."

  Repetition is intentional. Each work unit is an independent execution
  context; judges read the rules from the work unit itself, not from a
  central reference.

- `templates/ac-final-{python,markdown,yaml}.md` -- the AC-FINAL-001 through
  AC-FINAL-015 block, one variant per language tier. Python tasks get the
  full set; Markdown / YAML tasks get N/A suffixes on Python-tooling rows
  (e.g., `AC-FINAL-002 -- N/A for Markdown Tasks (no Python source
  authored)`).

  The N/A pattern lets the validator + judges treat language-tier
  differences uniformly without special-casing the acceptance harness.

---

## Phase 4 -- backlog generation strategy

Authoring 200+ work units in a single in-context pass would have blown the
context window. Two patterns enabled scale:

### Pattern A: parallel sub-agent dispatch per epic

Each epic was authored by its own sub-agent in a fresh context. The dispatch
prompt for every epic followed the same structure:

1. **Context paths**: spec + DevBench backlog-contract docs + example-template
   + AC-final templates + Code Standards block.
2. **Epic identity**: target repo, branch convention, spec section ownership.
3. **Decomposition diagram**: the on-disk file tree the agent must create
   (epic.md, per-feature dirs, per-story dirs, leaf task files).
4. **Per-task scope table**: one row per leaf task with title, spec
   section(s), source files (repo-relative), test files (paired per
   source-test-atomicity rule).
5. **Dependency wiring**: every leaf task's `## Dependencies` row, with
   semantic justification.
6. **Authoring conventions**: verbatim Code Standards + AC-FINAL,
   repo-relative paths, no em-dashes, AC-CYCLE-001 mandatory, etc.

13 epics, 13 sub-agents, dispatched in two waves (E1-E6 first, then E7-E13)
to keep memory pressure reasonable. Each sub-agent reported the files it
wrote + a conformance checklist (em-dash sweep, structural sections present,
manifest paths repo-relative).

**Lesson:** sub-agents that write to disk via the Write tool scale linearly;
their write-quality depends entirely on prompt specificity. Hand-off the
exact rows of a table, not a vague description.

### Pattern B: BACKLOG.md generated from disk

`BACKLOG.md` (the Status Summary + Full Work Unit Index) is a derived
artefact: it must list every work-unit file and reflect every dep edge. A
small Python generator (`gen_backlog_md.py`) walks the on-disk tree, parses
each `*.md`'s `## Status`, `## Target Repository`, `## Dependencies` sections,
and emits the canonical Markdown tables.

The generator's contract:
- Status Summary columns match the validator's 7-column format (Epic, Title,
  Done, In Progress, In Queue, Blocked, Declined).
- Full Work Unit Index rows include every Epic + Feature + Story + Task; the
  validator's status summary counts ALL descendants, not just Tasks.
- Em-dash sentinel: the generator hard-fails if any U+2014 leaks into the
  output (rule 10 enforcement).

After every disk edit (new task, dep wiring, manifest tweak), regenerate
`BACKLOG.md` before re-running `validate-backlog`.

---

## Phase 5 -- the validate-fix loop

After authoring, `devbench validate-backlog` was the iteration gate.
Each pass reduced the error count. The validator's autosuggested
`add-dep` commands and `(ref)` suffix directives were directly applied
via small Python scripts.

### Iteration 1: 339 errors

First validate pass surfaced five categories of issue:

| Category | Count | Root cause |
|---|---|---|
| Orphan path tokens | 100+ | AC/DoD prose referenced `docs/X.md` or `src/kanon_cli/Y.py` without listing them in the Manifest. |
| Manifest conflicts | 29 | Multiple tasks claimed the same file (e.g., `src/kanon_cli/cli.py` updated by 8 different command-registration tasks). |
| Status Summary mismatch | 13 | Generator emitted an extra "Repo" column the validator did not recognise. |
| Source-test atomicity violations | 48 | A task updates `src/kanon_cli/X.py` but no `tests/unit/test_X.py` in the same Manifest. |
| Template orphans | 4 | `backlog/templates/*.md` not in BACKLOG.md -> validator treated them as orphaned work units. |

### Fixes applied

1. **Templates moved** out of `backlog/` to `templates/` at workspace root.
   The validator only scans `backlog/**`, so relocation is enough.
2. **Generator updated** to emit the canonical 7-column Status Summary (no
   extra "Repo" column) and to count ALL descendants (Features + Stories +
   Tasks) per epic, not just Tasks.
3. **Orphan-path auto-fix script** (`fix_orphans.py`) read the validator's
   error list, located each offending `(uid, path, section)` tuple, and
   appended ` (ref)` to the path token inside the named section (AC or DoD).
   Idempotent: if the suffix is already present, the path is skipped. 553
   patches applied across 30+ files.
4. **Manifest-conflict serial chains** wired automatically. The validator's
   error message ships the exact `uv run devbench add-dep <blocked>
   <blocker>` commands. A bash loop fed every suggested chain back into
   `devbench add-dep`. 104 edges wired in the first pass.
5. **Source-test atomicity auto-fix** (`fix_atomicity.py`) appended
   `tests/unit/test_<basename>.py` rows to the Manifests of the 48 violating
   tasks, marking them as "New -- TDD-paired unit test covering the
   production change per docs/source-test-atomicity.md".

### Iteration 2: 4 errors

After the bulk fixes:

| Category | Count | Root cause |
|---|---|---|
| Dependency cycle | 3 | The autowired manifest-conflict chain on `cli.py` put `E7-F2-S1-T7` before `E7-F3-S1-T1`, but a semantic dep (`F2-T4` uses cache from `F3-T1`) closed the loop. |
| Manifest conflict | 1 | `docs/shell-completion.md` claimed by 15 tasks (one task per E7 completer + cache). |

### Fixes applied (round 2)

6. **Cycle broken** by removing the autowired `F3-T1 -> F2-T7` edge from
   `E7-F3-S1-T1.md`. The edge had been added by an earlier `add-dep` call
   that left a `[BLOCKED_PENDING_PROPOSAL]` audit-marker in the Comments
   section; the marker had to be deleted alongside the Dependencies-table
   row, because the marker also influences the dep graph.

7. **Manifest conflict on `docs/shell-completion.md`** resolved by **scope
   reduction** rather than chain expansion. The autowired chain would have
   serialized all 15 claimants into a single line, but doing so recreated
   the cycle. Instead, the doc's ownership was consolidated to four canonical
   owners (F1-T2 creates the draft section, F3-T1 adds the cache section,
   F4-T3 expands to the full operator guide, E8-F1-S1-T16 is the dedicated
   docs page), and the doc row was stripped from the 11 intermediate tasks
   that did not actually need to write to it. After the strip, the new
   chain wired cleanly: `F1-T2 -> F3-T1 -> F4-T3 -> E8-F1-S1-T16`.

   **Lesson:** when a manifest-conflict chain creates a cycle with semantic
   deps, the right fix is usually to narrow the manifest scope of the
   intermediate claimants, not to expand the chain.

### Iteration 3: 0 errors

`Backlog integrity check passed.` exit 0. Committed.

---

## Phase 6 -- final spec-vs-backlog audit

After validation passed, the operator requested:

> "Complete a final check between the spec and all the work units to make
> sure nothing was missed or changed; then validate the backlog with devbench
> one more time."

An Explore agent was dispatched with a comprehensive prompt listing every
spec section and the type of coverage to verify (CLI flag presence, error
message shape, env var documentation, edge cases, out-of-scope leakage,
acceptance criterion mapping). The agent walked the spec section-by-section
and grep'd the backlog for each requirement.

### Two real gaps surfaced

**Gap 1: global `--quiet` / `--verbose` / `--no-color` flags missing.**
Spec §7 (lines 735-736), §11.2 (line 1083), §14 (line 1349-1350) require
these as global flags on every command, with `--quiet` and `--verbose`
mutually exclusive (hard error if both passed). The backlog had zero coverage
of `--quiet` and `--verbose`. `--no-color` was mentioned per-command but no
shared implementation.

**Fix:** authored `E1-F4-S1-T2` adding `add_global_flags(parser)` to
`src/kanon_cli/core/cli_args.py` (the module introduced by `E1-F4-S1-T1`).
The factory uses `parser.add_mutually_exclusive_group()` for mutex; an
`_apply_global_flags(args)` helper plumbs the parsed values into Python
logging (`WARNING` / `INFO` / `DEBUG`) and a module-level `NO_COLOR_ACTIVE`
boolean.

Integration choice: rather than re-wire 10+ existing command tasks to call
the factory, the new T2 also updates `src/kanon_cli/cli.py` to call
`add_global_flags(root_parser)` on the **root** argparse parser. argparse
namespace propagation means every subcommand inherits `args.quiet`,
`args.verbose`, `args.no_color` automatically -- zero downstream task churn.
The trade-off is documented in the task's Description so the executor agent
understands why.

**Gap 2: provider-agnosticism CI grep test missing.** Spec §3.6, §10 (line
1023), §12 item 20 require a CI test that greps the source tree for
provider-specific CLI invocations (`gh`, `glab`, `bb`, `tea`, `aws codecommit`,
`az repos`) and provider-specific REST/GraphQL hostnames (`api.github.com`,
`gitlab.com/api`, `bitbucket.org/!api`, `dev.azure.com/_apis`), failing the
build on match. The Code Standards block in every task cited the rule, but no
task actually authored the test.

**Fix:** authored new feature `E1-F5-provider-agnosticism-ci-test` with one
leaf task `E1-F5-S1-T1`. The task ships:
- `tests/functional/test_provider_agnostic.py` -- enumerates tracked files
  via `git ls-files`, scans each for the forbidden patterns, fails with a
  clear remediation line on match.
- `tests/integration/provider_allowlist.txt` -- empty allowlist file with
  header comment explaining the `<path>:<justification-comment>` syntax for
  multi-provider parity tests.
- `docs/security-model.md` -- adds the "Provider-agnosticism CI test"
  subsection.

The new feature was added as a child of E1 by updating `backlog/E1-resolver/E1.md`'s
children list.

### Iteration 4: 13 new errors, then 7, then 0

Adding the two new tasks created new manifest conflicts on `cli.py` /
`constants.py` (both files updated by 16+ tasks now). The validator's
autosuggested chains re-wired the existing chains with the new tasks at the
head; 46 `add-dep` edges applied. Seven new orphan-path errors from the
two new tasks' Code Standards blocks were patched with the `(ref)` suffix
script. Final validate: **passed** (exit 0).

207 work units total. Committed.

---

## Phase 7 -- artefact summary

Final inventory:

| Artefact | Size | Notes |
|---|---|---|
| `BACKLOG.md` | 207 rows + 13-row status summary | Generated from disk; em-dash-free; columns match validator's 7-column shape. |
| `backlog/` | 207 work-unit files, 14 feature files, 13 epic files | Plus story files and the per-epic rollups. |
| `backlog/config/devbench.yaml` | ~270 lines | Every toggle explicit. |
| `spec/kanon-list-add-lock-features-spec.md` | ~50 KB locked spec | Source of truth for every AC. |
| `templates/` | 4 templates | Code Standards block + 3 AC-FINAL variants (Python / Markdown / YAML). |
| `devbench-commands.txt` | 5 commands | start (non-interactive), start (interactive), report, hook-tail, status. |

### Token-level statistics (approximate)

- 13 parallel sub-agent dispatches authored ~190 leaf task + rollup files in
  the first wave; 2 more in the gap-fill phase.
- 553 `(ref)` suffix patches across 30+ files via `fix_orphans.py`.
- 104 + 46 = 150 `add-dep` edges wired automatically from
  validator-suggested chains.
- 48 test-pairing rows added to manifests via `fix_atomicity.py`.
- 4 validate-backlog iterations (339 -> 4 -> 0 -> 13 -> 0).

---

## Reproducing this on a fresh spec

If you want to run the same playbook against your own locked spec:

1. **Lock the spec first.** Have Claude do a final-review pass for
   consistency, logic gaps, completeness. Apply the fixes. Stop editing.
2. **Identify epics from spec section boundaries.** One epic per major
   section, one feature per cohesive sub-area, one story per implementation
   unit, one task per testable change. Source-test atomicity dictates that
   every new `.py` source pairs with `tests/unit/test_<basename>.py` in the
   SAME task's Manifest.
3. **Author the Code Standards block and the AC-FINAL templates once.**
   Pin them under `templates/` outside the `backlog/` tree so the validator
   does not treat them as orphaned work units.
4. **Configure `backlog/config/devbench.yaml` with every toggle explicit.**
   Use the file in this example as a starting point; comment every value
   that matches a built-in default so the diff makes the choices auditable.
5. **Dispatch one sub-agent per epic.** Hand each agent the exact rows of
   the per-task scope table. Conformance checklist at the end of each
   dispatch (em-dash sweep, sections present, paths repo-relative).
6. **Run `devbench validate-backlog`** after every batch. Fix the categories
   in order:
   - Move templates out of `backlog/`.
   - Apply validator-suggested `add-dep` chains.
   - Apply `(ref)` suffixes for orphan paths.
   - Add source-test atomicity rows.
   - Resolve dependency cycles by narrowing manifest scope, not by
     expanding chains.
7. **Final audit: spec-vs-backlog gap pass.** Dispatch a read-only agent
   with the spec section list and the type of coverage to verify. Fill any
   gaps with new tasks authored to the same standard as the rest.
8. **Commit with the correct author identity.** `git config user.email`
   BEFORE the first commit; some hooks block `--amend --reset-author`.

The whole loop fits in one workday for a 200-task backlog if the spec is
well-locked. Most of the wall-clock time goes to validator iteration; the
fixes are mechanical once the patterns are scripted.

---

## Open improvements (future work for DevBench itself)

The session surfaced a few rough edges that would streamline future
backlog authoring:

- **`devbench remove-dep`** does not exist. Removing a wrongly-wired
  edge requires editing the work-unit file directly AND deleting the
  matching `[BLOCKED_PENDING_PROPOSAL]` audit marker in the Comments
  section. A symmetric remove command would be safer.

- **Manifest-conflict chain ordering** uses lexicographic task ID order,
  which can put conceptually "downstream" features before "upstream" ones
  (e.g., F2 completers before F3 cache infrastructure when the chain is
  on a file both touch). A heuristic that prefers feature-major ordering
  would have avoided one of the cycles in Phase 5.

- **Source-test atomicity violations** are easy to script-fix (append the
  test row to the Manifest), but the executor agent may end up authoring
  a stub test rather than a substantive one. A judge-side reminder when
  the test file is added "after the fact" by autofix would help.

- **`devbench validate-backlog --fix`** exists for rules 10/11 but not for
  the orphan-path or manifest-conflict fixes that this session scripted.
  Promoting the scripted fixes into the official `--fix` mode would shave
  most of Phase 5 off the next backlog.

These observations are not blockers; they are signal for the DevBench
roadmap.
