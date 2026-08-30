# Composition-Root Testing (caylent-solutions/devbench-internal-backlog#11)

This document defines what counts as a **composition-root test** and when
one is required. It is the shared reference for two enforcement points:

- `test-reviewer`'s rubric (`plugin/devbench-orchestrate/agents/review_team/test-reviewer.md`,
  "COMPOSITION-ROOT / REAL-ENTRY-POINT VERIFICATION" section).
- The `spec-to-backlog` skill's canonical task-file skeleton
  (`plugin-authoring/devbench-authoring/skills/spec-to-backlog/SKILL.md`, Step 1b
  item 13, `## Acceptance Criteria`).

devbench is stack-agnostic -- it is pointed at many different target
repos across many different frameworks and languages. Everything below is
phrased in framework-neutral terms first, with a React + Redux example
given only as an illustration, because that is the stack the source
investigation's evidence happened to come from.

## Why this exists

QA review of prior work groups repeatedly found large, fully-green test
suites (hundreds to thousands of passing tests) for components that were
either never mounted anywhere in the real application, mounted but missing
props/callbacks their own logic depended on, or tested against a
hand-built store/mock shape that had silently diverged from the real
runtime shape. In every case the test suite passed because it rendered the
component **in isolation** -- hand-supplied props, a locally constructed
store, or a module-scope-mocked dependency -- and never exercised the
actual composition root that assembles the real app. A green suite built
this way cannot detect "never wired in" or "wired in wrong."

## Definition: composition root

The **composition root** of an application is the place where the real
runtime object graph is assembled -- the application's actual entry point,
or the top of whatever provider/store/dependency-injection/router nesting
production code goes through before a component ever renders. Examples
across stacks:

- React + Redux: the top-level `<App>` (or the outermost real
  `<Provider store={realStore}>` + router tree) as opposed to a bare
  `render(<MyComponent {...props} />)`.
- Angular: the real root `NgModule` / route tree with its actual DI
  providers, as opposed to a `TestBed` configured with hand-picked
  stand-ins for every dependency.
- A server-side MVC framework: the real request-dispatch pipeline
  (middleware, DI container, session/auth context) as opposed to calling
  a controller method directly with hand-built arguments.

## When a composition-root test is required

A task's `## Acceptance Criteria` MUST include a composition-root
verification item, and `test-reviewer` checks the test behind that item,
when the task **adds or modifies a UI component (or equivalent
presentation-layer unit) that consumes shared or application-level
state** -- a global store, a dependency-injection container, routing
context, or any shared provider/composition tree the real app assembles
at startup.

An auto-ticked `## Definition of Done` checkbox is never accepted as
satisfaction of this requirement (spec `integration-reality-gates-hardening.md`
section 4.9(b), decision D-13, finding S1): devbench auto-ticks
Definition of Done checkboxes on the done transition, so a DoD-based
satisfaction record is a false record. A sixteenth canonical task
section was considered and rejected as the alternative (decision D-13);
the requirement is drafted, and checked, exclusively against the task's
`## Acceptance Criteria` line and the test behind it.

It is NOT required, and must not be flagged, for units that are
genuinely self-contained:

- Pure functions / pure utility logic with no external dependencies.
- Presentational components that take only primitive/local props and
  read no shared state, context, store, or DI-resolved dependency.
- Components already covered by an existing composition-root test in the
  same work unit (a second isolated-render test alongside it is fine and
  often desirable for fast, focused assertions -- it just cannot be the
  *only* coverage).

The rule keys off "consumes shared/app state," not "has any test at
all." A stateless helper function does not need to be mounted through the
app shell to be meaningfully tested.

## What counts as an acceptable composition-root test

Either of the following satisfies the requirement:

1. **The literal real entry point.** The test renders/exercises the
   component by going through the application's actual top-level entry
   (e.g., the real `App` component, the real bootstrap/router, the real
   request-dispatch pipeline) with the real store/DI container/provider
   tree the production build uses -- not a copy or reduced stand-in built
   solely for the test.

2. **A documented smallest-real-ancestor exception.** When mounting the
   literal top-level entry point is impractical (e.g., it pulls in
   unrelated network boot sequences), the test may mount the smallest
   *real* ancestor that still reproduces production's actual
   provider/store/DI/router nesting for this component -- i.e., the real
   feature container, real tab panel, or real routed page the component
   is actually rendered under in production, using the app's real
   store/provider setup (not a hand-rolled substitute). This exception
   MUST be documented in the task's `### Approach` section (never
   `## Comments`, which `read-unit --strip-comments` removes before a
   judge's Evidence fetch ever sees it -- spec 4.3) with a one-line
   justification for why the literal entry point was impractical and why
   the chosen ancestor still reproduces the real nesting.

What does **not** satisfy the requirement, regardless of test count or
pass rate:

- Rendering the component directly with hand-supplied props and no
  provider/store tree at all.
- Rendering through a locally constructed store/DI container/provider
  built specifically for the test (e.g., ad hoc `configureStore()` with
  a hand-picked partial state shape) instead of the app's real one.
- Rendering through a version of a dependency that is mocked at module
  scope such that the dependency's real logic never runs (e.g.,
  module-scope-mocking a data-grid library so its real row-validation
  logic is bypassed).

## Recommended companion convention (optional, not enforced by devbench)

Where practical, target repos are encouraged to expose a single,
shared, production-derived test-store/test-provider factory (built from
the app's real root reducer/provider tree) that component tests use
instead of ad hoc local store/mock-provider helpers, so test
infrastructure cannot silently diverge from the real runtime shape. This
is a target-repo-side testing convention, not a devbench mechanism --
devbench does not generate or enforce this factory, but a task's
`### Approach` may propose introducing or reusing one when the target
repo already has (or would benefit from) such a factory. See
caylent-solutions/devbench-internal-backlog#11 proposed-change item 3
for the source rationale; this is deliberately
left as guidance rather than a hard requirement because introducing a
shared test-double factory is itself a nontrivial refactor of existing
test infrastructure.

## Enforcement summary

- `test-reviewer` rubric item (see `test-reviewer.md`) is a judge-evidence
  check (`constants.GATE_TIERS['composition_root']`) that flags a task
  whose only coverage for a state-consuming UI component is an isolated
  render with hand-supplied props/mocked store/DI container, and emits
  the structured rejection code `test_review:COMPOSITION_ROOT_MISSING`
  (see `docs/review-feedback-vocabulary.md`).
- `spec-to-backlog` (Step 1b item 13 and Step 5b item 15) requires the
  generated task's `## Acceptance Criteria` to include an explicit
  composition-root item whenever the task's Changes Manifest adds or
  modifies a state-consuming UI component, so `test-reviewer` is
  checking against an AC line the task already committed to rather than
  inferring the requirement after the fact. An auto-ticked
  `## Definition of Done` item is never accepted in its place (spec
  4.9(b), decision D-13, finding S1).

## Related

- Companion to caylent-solutions/devbench-internal-backlog#10 (static
  reachability check): caylent-solutions/devbench-internal-backlog#10
  is a cheap, static "was this ever imported into a rendered tree"
  check; this is a real render test that additionally catches
  "imported but not functionally wired" (missing props, wrong store
  shape) that static analysis alone would miss.
