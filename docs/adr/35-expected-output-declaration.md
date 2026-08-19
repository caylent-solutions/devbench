# ADR-35: Per-work-unit `## Expected Output:` declaration

## Status

Accepted

## Context

`docs/backlog-contract.md` documents five Changes Manifest sentinels. Four of
them -- `<verification-only>`, `<decision-only>`, `<no changes>`, `<no-op>` --
are documented as "No source files are modified", with the task's evidence
recorded in `## Comments`. The fifth,
`<source-drift-fix-targets-determined-at-execution>`, is different in kind: its
concrete paths ARE enumerated mid-execution via `manifest_amendment`.

`git_ops._stage_for_commit` did not distinguish them. It rejected any Manifest
with no concrete path:

```
commit staging: the Changes Manifest contains no concrete file paths
(got ['<verification-only>']); every entry is an execution-time sentinel, so
there is no pathspec to scope the commit by. Resolve the sentinel to real paths
via a manifest amendment before committing.
```

That advice is correct for the deferred-resolution sentinel and unsatisfiable
for the other four: a verification task has no paths to resolve, by definition.
The result was that every verification, decision, or no-op task blocked
permanently at git-ops -- after its executor ran and all four review judges plus
security review had already passed. The implementation contradicted the
documented contract.

This is not a rare shape. A backlog that separates deployment from
post-deployment validation, as most operational backlogs do, is majority
verification tasks.

## Decision

Add an optional per-work-unit section:

```markdown
## Expected Output: none
```

- `commit` -- the default when the section is absent. git-ops commits, pushes,
  opens a PR, waits for CI, and merges. This is the pre-existing lifecycle.
- `none` -- git-ops completes the unit with no commit, push, PR, CI wait, or
  merge.

`validate-backlog` rule 28 cross-checks the declaration against the Manifest:
`none` requires a Manifest of only no-output sentinels, and rejects both a real
path and `<source-drift-fix-targets-determined-at-execution>`.

At execution time git-ops refuses a `none` unit that has **staged** changes, and
appends a `[GIT_OPS_NO_OUTPUT]` audit comment recording the declaration and the
working-tree state.

## Alternatives considered

**A `devbench.yaml` toggle (`git_ops.no_output_units: block | complete`).**
Rejected: whether a unit produces a commit is a property of the unit, and a
real backlog mixes both kinds. A backlog-level switch models the wrong thing,
and forces operators to configure something the work unit already knows.

**Infer the behaviour from the Manifest alone, with no declaration.** Rejected:
it silently reinterprets existing backlogs. A pre-existing unit whose Manifest
happens to be sentinel-only would change lifecycle on upgrade with no author
intent recorded. Requiring both the declaration and the sentinel Manifest keeps
the upgrade inert.

**Have each verification unit write an evidence file into the target repo** so
there is something to commit. Rejected: it pollutes the target repository with
one artefact per verification task, and in repositories that enforce a
one-release-unit-per-PR scope rule those artefacts collide with the rule. It
also contradicts the documented contract rather than implementing it.

## Consequences

- Backward compatible with zero migration. An absent section resolves to
  `commit`, so every existing backlog keeps its current lifecycle and no
  configuration key is introduced.
- Authoring mistakes fail at `validate-backlog` time rather than after four
  judges have run.
- A `none` unit produces no PR, so it does not appear in PR-based reporting. The
  `[GIT_OPS_NO_OUTPUT]` audit comment and log tag are the record that it ran.
- `none` units still require a non-gated `## Task Type:` under rule 21, since a
  sentinel-only Manifest cannot satisfy a gated type's production-source
  invariant.
