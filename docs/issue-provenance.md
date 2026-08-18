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
that closure work, not decoration.

| Gate | Internal Issue | Source PR | Devbench Issues | Spec Section |
|------|-----------------|-----------|------------------|--------------|
| `reachability` | `caylent-solutions/devbench-internal-backlog#10` | `caylent-solutions/devbench#315` | none | `4.4` |
| `composition_root` | `caylent-solutions/devbench-internal-backlog#11` | `caylent-solutions/devbench#316` | none | `4.9` |
| `ancestry` | `caylent-solutions/devbench-internal-backlog#12` | `caylent-solutions/devbench#317` | none | `4.5` |
| `shared_file_impact` | `caylent-solutions/devbench-internal-backlog#13` | `caylent-solutions/devbench#318` | none | `4.6` |
| `layout_geometry` | `caylent-solutions/devbench-internal-backlog#14` | `caylent-solutions/devbench#319` | none | `4.9` |
| `newly_reachable_paths` | `caylent-solutions/devbench-internal-backlog#15` | `caylent-solutions/devbench#320` | none | `4.9` |
| `write_path_audit` | `caylent-solutions/devbench-internal-backlog#16` | `caylent-solutions/devbench#321` | none | `4.8` |
| `fixture_consistency` | `caylent-solutions/devbench-internal-backlog#17` | `caylent-solutions/devbench#322` | none | `4.7` |
| harness guard fixes landed on `feat/bug-closure` ahead of this campaign (D-12): `guard-bash.sh`'s `git checkout --theirs`/`--ours` permit (`#335`) and the `.devbench/active-work-unit` claim marker (`#336`) | none | none | `caylent-solutions/devbench#335`, `caylent-solutions/devbench#336` | `4.12` |
| assert-tests-pass.sh fail-open rework | TBD (filed at E11) | none | TBD (filed at E11) | `15` |
| guard-git-stage rule-1 cwd/-C quirks | TBD (filed at E11) | none | TBD (filed at E11) | `15` |
| real-browser layout machine-verification design | TBD (filed at E11) | none | TBD (filed at E11) | `15` |
| build-time generation of rubric bodies | TBD (filed at E11) | none | TBD (filed at E11) | `15` |
| auto-registry fan-in tuning telemetry | TBD (filed at E11) | none | TBD (filed at E11) | `15` |

Column notes:

- **Gate** -- one of the eight canonical gate names (`devbench.constants.GATE_NAMES`) for the first
  eight rows; a short descriptive label for every other row, since those rows track issues that are
  not tied to a single gate.
- **Internal Issue** -- the fully-qualified `caylent-solutions/devbench-internal-backlog#<N>` issue
  this row was requested by. `#10`-`#17` are the eight gate issues (spec sections 4.4-4.9); the five
  Section 15 follow-up rows have no issue number yet because spec section 15 defers filing them until
  E11.
- **Source PR** -- the fully-qualified `caylent-solutions/devbench#<N>` draft pull request the gate
  was hardened from. `#315`-`#322` is the set of eight source PRs; spec section 4.14 defines a
  different, non-ascending landing order for cherry-picking them (`#321` -> `#317` -> `#320` ->
  `#315` -> `#318` -> `#322` -> `#316` -> `#319`), not the order this column is listed in.
- **Devbench Issues** -- any `caylent-solutions/devbench`-repo issue tied to this row. `#335` is
  `guard-bash.sh`'s `git checkout --theirs`/`--ours` permit and `#336` is the
  `.devbench/active-work-unit` claim marker; both are harness guard fixes that landed on
  `feat/bug-closure` before this campaign's branch was cut (spec section 1.2, decision D-12); they
  are not tied to any single gate, so they carry their own row rather than being attached to one of
  the eight gate rows. `caylent-solutions/devbench-internal-backlog` (this workspace's separate
  internal-backlog repo) never appears in this column; that repo's issues live only in the Internal
  Issue column.
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
