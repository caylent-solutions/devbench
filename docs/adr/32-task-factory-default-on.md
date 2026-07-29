# ADR-32: `task_factory.enabled` defaults on, `auto_accept_proposals` restored to `false` (supersedes the PR #202 shipped posture, not ADR-11)

**Status:** Accepted
**Date:** 2026-07-29

---

## Context

Issue #259 asks whether the task-factory loop -- blocker-resolver +
task-factory materialising draft work-unit `.md` files for the
out-of-scope production fixes an amendment reject surfaces -- should run
by default. `spec/devbench-modernization.md` Section 0 flags this as
behavior-change register item **B-8**, the single most invasive default
flip in the modernization: today `TaskFactoryConfig.enabled` defaults to
`False` (opt-in) while `TaskFactoryConfig.auto_accept_proposals` defaults
to `True` (shipped by PR #202, not by ADR-11 -- see below). A materialised
draft's initial `## Status:` is unrelated to either flag -- it is always
`backlog.default_status_for_new_work_units` (default `in-queue`, AC-189-8),
so drafts are immediately actionable by the next orchestrator sweep with
no built-in review gate regardless of `auto_accept_proposals`.
`auto_accept_proposals` instead governs two things: (1) whether
`write-proposal` itself synchronously materialises (and promotes any
legacy `proposed`-status draft) inside the same invocation rather than
waiting for the next `sweep-proposals` tick, and (2) whether
`sweep-proposals` separately auto-promotes a draft that is explicitly
sitting at `## Status: proposed` (a status the normal materialise path
has not written since AC-189-8 shipped -- it only exists today for
legacy or hand-edited drafts). If a future change flipped `enabled`
alone -- without also reconsidering `auto_accept_proposals` -- every
backlog that upgrades devbench and omits the `task_factory` key would
silently inherit an *active* version of both paths: `write-proposal`
calls from `blocker-resolver` or a validation-gate escalation would
immediately materialise-and-promote with no human involvement, and any
`proposed` draft left over from a legacy backlog, or created by a future
config change, would be auto-promoted by `sweep-proposals` -- whereas
today both paths are configured but inert because the loop that feeds
them never runs.

ADR-11 itself chose `auto_accept_proposals` to default `false`
("matching today's behaviour byte-for-byte, so every existing backlog
keeps working without change" -- ADR-11 Context; "Default `false`" --
ADR-11 Decision item 1). The `true` default did not come from ADR-11:
it was introduced afterward, in commit `0ec8db8` (PR #202,
`feat/issues-188-193`, merged 2026-05-28, released in tag `0.1.0`),
without an accompanying ADR update. By the time this task started, the
shipped default an operator observed was `true`, and the opt-in
`enabled` framing made that auto-promote-by-default posture look
low-risk, since an operator had to explicitly turn the loop on first to
be affected by it. That framing no longer holds once `enabled` flips to
on-by-default, so per spec Section 13 **D-17** this decision needs its
own ADR that explicitly records what is being superseded -- the PR #202
auto-promote-by-default posture as shipped, not ADR-11's own written
decision -- rather than silently drifting the default again in a
config_loader.py diff with no paper trail.

## Decision

Per spec Section 13 **D-11**, flip both `TaskFactoryConfig` defaults
**simultaneously in one commit**:

1. `enabled` defaults to `True` (was `False`). The task-factory loop runs
   for every backlog unless the operator explicitly sets
   `task_factory.enabled: false`.
2. `auto_accept_proposals` defaults to `False` (was `True`). This does
   NOT change where a freshly materialised draft lands -- that has always
   been, and remains, `backlog.default_status_for_new_work_units`
   (default `in-queue`, AC-189-8), regardless of this flag. What the flag
   controls is two auto-promote paths: `write-proposal` synchronously
   materialising (and promoting any legacy `proposed`-status draft)
   inside the same invocation instead of waiting for the next
   `sweep-proposals` tick, and `sweep-proposals` separately auto-
   promoting a draft that is explicitly sitting at `## Status: proposed`
   (the legacy/hand-edited-draft case described in Context). With the
   default `False`, `write-proposal` only persists the JSON
   (materialisation happens later, via `sweep-proposals` or a manual
   `materialise-proposal` call) and `sweep-proposals` leaves orphaned
   `proposed` drafts alone for the operator to `promote-proposal` or
   `reject-proposal` explicitly; neither path changes the initial status
   of a freshly materialised draft, which lands at
   `backlog.default_status_for_new_work_units` regardless of this flag.

**This ADR supersedes the PR #202 auto-promote-by-default posture, not
ADR-11's written decision.** ADR-11's decision record (the
`auto_accept_proposals` flag itself, its plumbing through
`cmd_sweep_proposals`, the `[PROPOSAL_PROMOTED] (auto-accepted via
task_factory.auto_accept_proposals=true)` audit suffix, and its own
default of `false`) remains mechanically correct and factually accurate
as written. What this ADR supersedes is the *shipped* default that PR
#202 introduced after ADR-11 without recording a decision for it: this
ADR restores `auto_accept_proposals` to `false` -- coincidentally the
same value ADR-11 always specified -- and does so as a fresh, reasoned
decision (D-11) rather than a reversion, because the `enabled` default
is flipping in the same commit and the two fields' interaction is what
actually needs a new record. ADR-11 is not deleted and its own text
needs no correction; this ADR answers "what does an operator get if
they touch nothing" for the current, post-PR-#202 codebase.

There is no commit-level intermediate state where `enabled` is `True`
while `auto_accept_proposals` is still `True`: both dataclass field
defaults change in the same diff in `src/devbench/config_loader.py`, so
no released version of devbench ever shipped the unreviewed-auto-promote
hazard B-8 warns about.

### Defaults-versus-amendment interaction contract

`task_factory.enabled: true` has always required
`manifest_amendment.enabled: true` -- the loop runs from the
amendment-reject path, so it has nothing to do when the amendment
workflow itself is off. That cross-field check is a config-load
`ValueError` today. Flipping `task_factory.enabled`'s default to `True`
interacts with that check in a way that needs an explicit rule, or the
defaults flip alone would brick any existing backlog that has
`manifest_amendment.enabled: false` in its YAML and never mentions
`task_factory` (a config that was perfectly valid before this ADR and
did nothing wrong).

The rule:

- **Explicit contradiction still fails fast.** A config that explicitly
  writes `task_factory.enabled: true` while `manifest_amendment.enabled`
  resolves to `false` (whether that `false` is explicit or -- after some
  future default change -- itself defaulted) raises the existing
  `ValueError` at config load. The operator asked for a loop that cannot
  run; that is a real, actionable mistake and must fail loudly per
  `CLAUDE.md`'s fail-fast rule.
- **A defaulted-on `enabled` downgrades silently instead of bricking.**
  A config that **omits** the `task_factory` block entirely -- so
  `enabled` resolves via the new on-by-default value, not because the
  operator asked for it -- combined with an **explicit**
  `manifest_amendment.enabled: false`, is the B-8 migration case: an
  existing backlog from before this release that made a deliberate,
  valid choice about the amendment workflow and never opted into
  task-factory. `load_runtime_config` resolves `task_factory.enabled` to
  `False` for that combination instead of raising. The loop has nothing
  to do without the amendment workflow it runs from either way, so
  disabling it is behaviorally identical to what the operator's config
  already implied; the only alternative (raising) would turn a passive,
  pre-existing config into a hard upgrade blocker for a section the
  operator never touched, which spec Section 0 B-8 explicitly forbids.

This distinction is implemented by tracking whether the YAML's
`task_factory` mapping contained an explicit `enabled` key (not merely
whether the resolved boolean is `True`), and is proven by
`tests/test_config_loader.py::TestTaskFactoryConfig` (the explicit-true
case still raises; the omitted-key-plus-disabled-amendment case does
not, and resolves `enabled=False`).

## Consequences

- **Existing backlogs get task-factory drafts without human config
  changes.** Those drafts land at `backlog.default_status_for_new_work_units`
  (default `in-queue`) and are actionable by the next orchestrator sweep
  immediately -- this was already true before this ADR and is unchanged
  by either default flip. Operators who want an actual human-review gate
  on task-factory drafts must set `backlog.default_status_for_new_work_units:
  draft` explicitly; `auto_accept_proposals` does not provide that gate.
- **No unreviewed *orphan-promote* hazard ships in any commit.** Both
  fields flip together; there is no window where `enabled=true,
  auto_accept_proposals=true` is the shipped default, so no released
  commit auto-promotes legacy/hand-edited drafts sitting at
  `## Status: proposed` without an operator decision.
- **Backlogs that explicitly disabled the amendment workflow keep
  working unchanged.** `manifest_amendment.enabled: false` plus an
  omitted `task_factory` block continues to parse and run exactly as
  before this ADR (task-factory inert), matching B-8's "no silent
  brick" requirement.
- **Relative to released tag `0.1.0`, this ADR changes two defaults**:
  `task_factory.enabled` (`false` -> `true`) and
  `task_factory.auto_accept_proposals` (`true` -> `false`). Operators who
  want the pre-ADR-32 opt-in `enabled` behavior restore it by setting
  `task_factory.enabled: false` explicitly. Operators who want the
  `0.1.0`-released `auto_accept_proposals: true` behavior restore it by
  setting `task_factory.auto_accept_proposals: true` explicitly -- **this
  reintroduces both `0.1.0` effects**: (1) `write-proposal` synchronously
  materialising-and-promoting the proposal it just wrote inside the same
  invocation, instead of waiting for the next `sweep-proposals` tick, and
  (2) the orphan-auto-promote path for drafts already sitting at
  `## Status: proposed` -- both of which D-11 deliberately turns off by
  default. Neither effect auto-promotes freshly materialised drafts past
  their initial status (those have always landed at
  `backlog.default_status_for_new_work_units`, default `in-queue`, per
  AC-189-8) -- so restoring the flag does not by itself produce a general
  unreviewed auto-promote-everything loop; set it only after confirming
  that skipping the sweep-tick delay and review of orphaned `proposed`
  drafts is acceptable. Both keys are independent; either or both can be
  set. See the CHANGELOG migration note for the exact keys.
- **Every self-documenting config surface was updated in the same
  change**: the `TaskFactoryConfig` dataclass docstring,
  `src/devbench/config-schema.json`'s three `task_factory` descriptions,
  `sample-config.yaml`'s block and comments, and
  `plugin-authoring/devbench-authoring/skills/configure-devbench/SKILL.md`
  Step 8's prompted defaults. No surface still describes `enabled` as
  opt-in or `auto_accept_proposals` as on-by-default.

## Alternatives considered and rejected

**Flip `enabled` alone, leave `auto_accept_proposals` untouched.**
Rejected: this is exactly the hazard B-8 names -- every backlog upgrading
devbench would silently start auto-promoting task-factory drafts with no
human review, because the pre-existing `auto_accept_proposals: True`
default was only ever safe under the assumption that `enabled` itself
required an explicit opt-in.

**Raise `ValueError` unconditionally whenever the resolved
`task_factory.enabled` is `True` and `manifest_amendment.enabled` is
`False`, regardless of whether `enabled` came from an explicit key or
the new default.** Rejected: this bricks every existing backlog that
disabled the amendment workflow and never mentioned `task_factory` --
config-load failure for a section the operator never touched, which
directly violates the B-8 migration requirement ("a defaults-only flip
that bricks existing configs would violate the migration requirement").

**Silently force `manifest_amendment.enabled` to `True` whenever
`task_factory.enabled` resolves `True` by default, instead of
downgrading `task_factory.enabled`.** Rejected: flipping a *different*
section's value to satisfy a cross-field constraint is a larger, more
surprising behavior change than downgrading the section whose default
just moved. Downgrading `task_factory.enabled` keeps the blast radius of
this ADR confined to the one section D-11 is about; the operator's
explicit `manifest_amendment.enabled: false` choice is never overridden.

**Bump a config schema version to force operators to re-review every
key on upgrade.** Rejected: heavier migration mechanism than the actual
scope of this change warrants; every other default flip in this
modernization (see `CHANGELOG.md`'s "devbench.yaml default changes"
entry) is handled with a plain default-value change plus a migration
note, and this one is no different in kind.

## Related files

### Python
- `src/devbench/config_loader.py::TaskFactoryConfig` -- `enabled` default
  `False` -> `True`; `auto_accept_proposals` default `True` -> `False`;
  docstring rewritten.
- `src/devbench/config_loader.py::load_runtime_config` -- cross-field
  validation between `task_factory.enabled` and `manifest_amendment.enabled`
  now distinguishes an explicit `enabled` key from the defaulted value.
- `src/devbench/config-schema.json` -- `task_factory` block description,
  `enabled` description, `auto_accept_proposals` description.

### Config surfaces
- `sample-config.yaml` -- `task_factory.enabled: true`,
  `task_factory.auto_accept_proposals: false`, updated comments.
- `plugin-authoring/devbench-authoring/skills/configure-devbench/SKILL.md`
  Step 8 -- prompted defaults updated.

### Tests
- `tests/test_config_loader.py::TestTaskFactoryConfig` -- default-pinning
  tests updated to the new defaults; new test for the omitted-key +
  disabled-amendment downgrade contract; the explicit-contradiction test
  (`task_factory.enabled: true` with `manifest_amendment.enabled: false`
  raises) kept passing unchanged.

### Docs
- `docs/adr/32-task-factory-default-on.md` (this file).
- `CHANGELOG.md` -- migration note naming the exact restore-old-behavior
  keys.
- `docs/adr/11-auto-accept-proposals.md` -- unchanged and factually
  accurate as written (it specified `auto_accept_proposals` default
  `false`); this ADR restores that value after PR #202 shipped `true`
  without an ADR, and supersedes the PR #202 posture, not ADR-11's
  stated default.
