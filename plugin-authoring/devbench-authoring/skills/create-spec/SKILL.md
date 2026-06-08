---
name: create-spec
description: Author a rigorous engineering specification at the depth `spec-to-backlog` requires, matching the operator's workspace exemplar (when configured) or the embedded 16-section skeleton otherwise; then offer spec-to-backlog handoff
model: opus
tools:
  - Read
  - Write
  - Edit
  - Bash
---

You are a meticulous specification author. Your goal is to produce a `spec/<project-name>.md` deep enough that a developer who has never seen the codebase can implement every feature from the spec alone, and a QA engineer can derive every test case from the spec's acceptance criteria.

**Quality bar (two-source resolution)**: The 16-section structural skeleton below (Sections 0-15) is the authoritative quality bar. When the workspace points the skill at an in-workspace exemplar via `skills.exemplar_spec_path` in `backlog/config/devbench.yaml`, also internalise that exemplar's depth as a reference. Target: 1000+ lines for non-trivial programs (smaller for minor features); every spec MUST cover all 16 sections or explicitly mark each absent section "N/A -- reason".

**Iterate-until-perfect loop**: Self-critique against the rubric below after every draft. Revise. Re-score. Repeat until the score is zero unresolved items, or until `skills.max_iterations` in `backlog/config/devbench.yaml` (default 5) is reached. If `max_iterations` is reached without converging, emit a [BLOCKED] audit comment listing the unresolved rubric items and ask the operator to clarify the ambiguous or under-specified areas. Do NOT silently ship a sub-quality artefact.

---

## Headless mode (--answers-file)

Issue #256. When the operator invokes the skill with `--answers-file <path>`, the skill runs headlessly: no prompts are presented, Steps 2 and 5 are auto-satisfied, and any failure exits immediately with a non-zero code.

**Detection**: At skill startup, check whether `--answers-file <path>` was supplied as an argument. If it was, enter headless mode for the entire session.

**Loading and validation**: Use `devbench.plugin_helpers.create_spec_answers.load_answers_file` and `validate_answers` to parse and validate the file. The file must be a YAML mapping keyed by Block letter A-G (Appendix D-6). If a required block is absent, emit the verbatim message to stderr and exit non-zero:

```
[BLOCKED] create-spec headless: missing answer for Block <X>
```

If the file cannot be parsed as YAML or is not a mapping, emit `ERROR: answers file <path> could not be parsed as YAML: <detail>` to stderr and exit non-zero.

**Step 2 (operator questions) -- auto-satisfied**: When answers are present, skip all operator prompts. Use the pre-loaded block answers as the inputs to Step 3 directly. Do NOT ask the operator any questions.

**Step 5 (final operator review) -- auto-satisfied**: When the rubric score is zero after the self-critique loop, proceed directly to Step 6 (write the spec) without presenting the spec to the operator for approval.

**Non-zero rubric after max_iterations in headless mode**: If the iterate-until-perfect loop reaches `skills.max_iterations` without converging to a zero rubric score, emit the `[BLOCKED]` comment (same format as interactive mode) to stderr and exit non-zero:

```
[BLOCKED] create-spec headless: iterate-until-perfect loop reached max_iterations=<N> without converging.
Unresolved rubric items:
- <item number>: <detail> -- <suggested-fix>
...
Re-run the skill with an updated answers file addressing the above items.
```

Do NOT write a partial spec when headless mode exits with `[BLOCKED]`.

### Appendix D-6 -- Answers file YAML schema

The answers file is a YAML mapping where each key is one of the Block letters A through G and each value is a string containing the operator's pre-collected answers for that block's questions. All seven blocks are required.

```yaml
# answers.yaml -- headless create-spec answers file (Appendix D-6)
A: |
  1. Project name: <name>. Problem: <one-sentence problem statement>.
  2. Current codebase state: <primitives, commands, existing behavior>.
  3. Behavior changes: <list old vs new, or "None">.
B: |
  4. Goals: <goal 1 with worked example>, <goal 2 with worked example>, ...
  5. Non-goals: <list of explicitly out-of-scope asks>.
  6. Multi-repo scope: <description, or "None">.
C: |
  7. New commands: <exact CLI syntax, semantics, error handling, edge cases,
     worked example -- one sub-section per command>.
D: |
  8. File format changes: <schema description, or "None">.
  9. Existing primitives to reuse: <function/class/constant names and modules>.
E: |
  10. NFRs: <performance, security, compatibility constraints>.
  11. Error handling and logging conventions: <description>.
F: |
  12. Testing requirements: <coverage targets, integration scenarios>.
  13. Documentation updates: <list of docs to create or modify>.
G: |
  14. Acceptance criteria: <AC-1: ..., AC-2: ..., ...>.
  15. Resolved decisions: <decision + rationale + alternatives considered>.
  16. Out of scope: <list of adjacent asks this spec does NOT cover>.
  17. Future work: <explicitly deferred items>.
```

Block values may be multi-line strings. Empty string values are structurally valid (present but blank); they satisfy the "block present" check but may produce a lower-quality spec section.

---

## Step 1 -- Internalise the canonical spec structure (and the workspace exemplar when configured)

This skill is application-agnostic: it does NOT depend on any specific workspace having a particular exemplar file.

**Step 1a -- Resolve the exemplar path (optional)**

Read `backlog/config/devbench.yaml` and look for `skills.exemplar_spec_path`. If the key is present and the file at that path exists, read it:

```
Read <skills.exemplar_spec_path value>
```

If `skills.exemplar_spec_path` is absent OR the file does not exist, skip the file read entirely. Do NOT default to any hardcoded path. The 16-section skeleton below is sufficient by itself to author a passing spec.

**Step 1b -- The 16 canonical spec sections (authoritative quality bar)**

Every spec MUST cover these 16 top-level sections (or mark each absent section "N/A -- reason"):

- **Section 0** -- Items that change existing user-facing behavior (prefaced review)
- **Section 1** -- Context (current verified state of the codebase)
- **Section 2** -- Goals (with worked operator examples)
- **Section 3** -- Existing primitives to reuse (DO NOT reinvent)
- **Section 3.5** -- Standards audit
- **Section 3.6** -- Trust model
- **Section 4** -- New command surface (sub-sections per command, resolver semantics, edge cases, error handling, examples)
- **Section 5** -- Data format (lockfile or equivalent)
- **Section 6** -- Version / interoperability semantics
- **Section 7** -- Error handling, logging, configuration
- **Section 8** -- Documentation updates
- **Section 9** -- Parallel or multi-repo scope
- **Section 10** -- Testing requirements (100% coverage)
- **Section 11** -- Completions / integrations matrix
- **Section 12** -- Out of scope (this spec)
- **Section 13** -- Resolved decisions (interview record)
- **Section 14** -- CLI `--help` reference / snapshot
- **Section 15** -- Future work (explicitly deferred)

---

## Step 2 -- Ask the operator structured questions

Ask the operator one block of questions covering every section of the canonical skeleton. Do not proceed to authoring until you have answers (or explicit "N/A / skip" for optional items). Structure your questions as follows:

**Optional discovery-artifact mode** (issue #221 C1): if the operator invokes the skill with a `discovery_artifacts_dir` argument naming a directory of pre-collected research artifacts (architecture notes, oncall postmortems, existing READMEs), walk that directory first via `Read` on each artifact and use the contents as the evidence base for Sections 1, 3, 3.5, 3.6, and 4 instead of asking blank-slate questions. The operator question block then narrows to the *gaps* the artifacts do not cover.

**Block A -- Problem and context**
1. What is the project name and one-sentence problem statement?
2. What is the current verified state of the codebase that this spec builds on? (What primitives already exist? What commands already work?)
3. Are there any items that change existing user-facing behavior? If so, list them with old vs. new behavior so reviewers can flag policy concerns before implementation begins.

**Block B -- Goals, non-goals, and scope**
4. What are the specific goals of this spec? For each goal, provide a worked operator example (command + expected output or behavior).
5. What are the non-goals? List every plausible adjacent ask that this spec deliberately does NOT cover.
6. Is there a multi-repo or parallel scope (e.g., open-source repo + private catalog repo)? If yes, describe it.

**Block C -- Functional requirements and command surface**
7. What new commands or subcommands does this spec introduce? For each:
   - Exact CLI syntax (`command <required-arg> [--optional-flag <value>]`)
   - Semantics: what does it do step by step?
   - Error handling: what errors can occur and how are they reported?
   - Edge cases: what happens on empty input, missing files, permission errors, concurrent invocations?
   - Worked operator example (show the exact command and expected output)

**Block D -- Data formats and integration points**
8. Does this spec introduce or modify any on-disk file format (lockfile, manifest, config)? If so, describe the schema.
9. Which existing primitives (functions, classes, constants, env vars) MUST be reused rather than reinvented?

**Block E -- NFRs, error handling, and configuration**
10. Non-functional requirements: performance, security, compatibility, portability constraints.
11. What error handling, logging, and configuration conventions apply (beyond the language/framework defaults)?

**Block F -- Testing and documentation**
12. What testing requirements apply? (Coverage targets, integration test scenarios, property-based tests?)
13. What documentation must be updated or created as part of this spec?

**Block G -- Acceptance criteria, decisions, and future work**
14. What are the acceptance criteria (AC-N format, testable from the spec text alone)?
15. What design decisions have already been resolved? For each: decision taken + rationale + alternatives considered.
16. What is explicitly out of scope for this spec? (Name every plausible adjacent ask not covered.)
17. What future work is explicitly deferred?

---

## Step 3 -- Author the spec one section at a time

Using the operator's answers, author `spec/<project-name>.md` following the canonical structural skeleton from Step 1b. Work through one section at a time:

1. Write Section 0 (behavior-change prefaced items, if any) -- present it to the operator for spot-check before continuing.
2. Write Section 1 (Context) -- verify with the operator that the current codebase state is accurate.
3. Write Section 2 (Goals) -- ensure each goal has a worked operator example.
4. Continue through Sections 3-15, applying the canonical depth at each section.

**Per-FR discipline**: for every functional requirement, the spec must state:
- The happy-path behavior
- The error-handling semantics (what error, what message, what exit code or exception)
- At least one worked operator example

Every functional requirement MUST be written as a numbered `FR-N:` line (e.g., `FR-1: The system shall ...`). These lines form the machine-readable FR list consumed by `spec-to-backlog` and the backlog-readiness self-check.

**Per-AC discipline**: acceptance criteria are numbered (`AC-N`), reference the spec section that justifies them, and are testable from the spec text alone without asking the implementer to infer intent.

**AC-N section marker**: the AC-N list MUST be preceded by the stable machine-locatable marker line:

```
<!-- AC-SECTION-START -->
```

Place this marker on its own line immediately before the first `AC-N` entry (or before the heading that introduces the AC list). This marker allows `spec-to-backlog` to locate the AC section deterministically without relying on section numbers that change across specs.

**Target Repository block**: every spec MUST include a `## Target Repository` section with exactly these two fields:

```markdown
## Target Repository

- **Repo:** `<org/repo>`
- **Branch:** `<target-branch>`
```

**Unit Inventory (multi-unit specs only)**: when a spec covers more than one work unit, add a `## Unit Inventory` section that lists each unit:

```markdown
## Unit Inventory

- unit-1: <title>
- unit-2: <title>
```

Single-unit specs do NOT require a Unit Inventory section.

---

## Step 4 -- Run the iterate-until-perfect self-critique loop

After drafting the full spec, run the self-critique rubric below (single-agent self-critique -- the Step-4 fallback path, which runs unchanged when the Workflow tool is absent). Generate a finding list (each finding: criteria-group + detail + suggested-fix). Then revise one finding at a time. Re-run the full rubric after each revision batch. Repeat until the rubric score is zero unresolved items or `skills.max_iterations` is reached.

### Self-critique rubric for create-spec

Score each item as PASS or FAIL. A FAIL is an unresolved item.

**Structure (items 1-2)**
1. **16 sections**: All 16 top-level sections from the canonical skeleton (Step 1b) are present (Sections 0-15) or each absent section has an explicit "N/A -- reason" statement. FAIL if any section is missing without justification.
2. **Worked examples per goal**: Every goal in Section 2 has at least one worked operator example (concrete command + expected output). FAIL if any goal lacks a worked example.

**Functional requirements (items 3-4)**
3. **Error handling per FR**: Every functional requirement (new command, new behavior) has explicit error-handling semantics: what error, what message, what exit code or exception. FAIL if any FR is silent on error handling.
4. **Non-goals stated**: Every plausible adjacent ask the spec does NOT cover is named in Section 12 (Out of scope). FAIL if the out-of-scope section is absent or empty.

**Acceptance criteria (items 5-6)**
5. **Numbered and testable ACs**: Every acceptance criterion is numbered (AC-N), cites the spec section that justifies it, and is testable from the spec text alone. FAIL if any AC is unnumbered, ambiguous, or requires inferring intent.
6. **Cross-references to primitives**: Every reused existing primitive (function, class, constant, env var) is cited by name in Section 3. FAIL if a reused primitive is mentioned in Section 4+ without a Section 3 cross-reference.

**Design record (items 7-8)**
7. **Resolved decisions**: Section 13 (Resolved decisions / interview record) captures every "we decided X because Y" call made during spec authoring, with alternatives considered. FAIL if any design call made during authoring is not recorded.
8. **Out-of-scope section**: Section 12 (Out of scope) names every plausible adjacent ask not covered by this spec. FAIL if the section is absent or names fewer adjacent asks than were discussed during the operator question block.

**Backlog contract (items 9-12)**
9. **FR-N lines present**: every functional requirement is written as a numbered `FR-N:` line. FAIL if any FR is stated in prose without a `FR-N:` prefix.
10. **AC-SECTION-START marker**: the literal line `<!-- AC-SECTION-START -->` appears immediately before the AC-N list. FAIL if the marker is absent.
11. **Target Repository block**: a `## Target Repository` section with `Repo:` and `Branch:` fields is present. FAIL if either field is missing.
12. **Unit Inventory (multi-unit)**: when the spec covers more than one work unit, a `## Unit Inventory` section listing each unit is present. FAIL if the spec is multi-unit and the inventory is absent. (Single-unit specs are exempt.)

**Convergence protocol**: If the rubric score after revision is still > 0 and the loop count equals `skills.max_iterations`, emit a [BLOCKED] comment:

```
[BLOCKED] create-spec iterate-until-perfect loop reached max_iterations=<N> without converging.
Unresolved rubric items:
- <item number>: <detail> -- <suggested-fix>
...
Please clarify the above items and re-run the skill.
```

---

## Step 4b -- Adversarial review phase (Workflow mode only)

This phase runs **only** when both conditions are met:

1. The Workflow tool is available (i.e., `skills.use_workflow` is `true` in
   `backlog/config/devbench.yaml` and the Workflow tool is present).
2. The spec exceeds `skills.adversarial_review_threshold` (the word count or
   section count configured in `backlog/config/devbench.yaml`).

When the Workflow tool is absent, skip this step entirely. The single-agent
Step-4 self-critique runs unchanged as the sole review pass. This preserves
the existing behavior without regression.

### Dimension fan-out

Using the dimension fan-out pattern from `docs/workflow-authoring-patterns.md`
(Pattern 1), spawn one independent reviewer agent per generic dimension. The
five generic dimensions are fixed; finer-grained checks within each dimension
are derived from the spec's own content, never from a pre-baked domain
taxonomy:

1. **Implementability** -- Can every FR be implemented given the primitives
   cited in Section 3? Are any FRs under-specified to the point where a
   developer cannot proceed without guessing?
2. **Internal consistency** -- Do the ACs, FRs, and Sections 0-15 agree with
   each other? Do resolved decisions (Section 13) align with Section 4
   functional requirements?
3. **Completeness/gaps** -- Are there implied behaviors that the spec leaves
   unspecified? Does each FR have error-handling semantics?
4. **Claims-grounding** -- Are there unmeasured performance claims (e.g.,
   "reduces latency by X%")? Any claim with a specific number but no cited
   measurement is flagged by this dimension.
5. **Citation/standards verification** -- Every external module, flag, version,
   or standard the spec cites must be checked against its named source. Any
   cited external item that cannot be verified against the named source is
   flagged by this dimension.

Each reviewer agent writes its findings to a separate output file (Pattern 6:
file-based agent output from `docs/workflow-authoring-patterns.md`). No
dimension-specific findings are restated here; see the shared patterns doc for
the generic fan-out and file-output contracts.

### Per-finding skeptic re-verification

After collecting all per-dimension findings, apply Pattern 2 (per-finding
adversarial verification, default-reject) from
`docs/workflow-authoring-patterns.md`:

For each finding, an independent skeptic agent attempts to falsify it. The
skeptic returns exactly one of three verdicts:

- **CONFIRMED** -- the finding is verifiable; it must be addressed before
  the spec is written.
- **REJECTED** -- the finding is not verifiable or is a false positive; it
  does not drive an edit.
- **severity-adjusted** -- the finding is real but less severe than the
  reviewer claimed; the adjusted severity governs what action (if any) is
  taken.

Findings that the skeptic cannot verify default to **REJECTED**. An
unverifiable finding must never silently drive an edit; it must be surfaced to
the operator as unverified and left for manual review.

Only CONFIRMED and severity-adjusted (where the adjusted severity still
requires action) findings advance to the revision step. REJECTED findings are
reported in a summary but do not trigger spec edits.

### Citation dimension error contract

A cited external module, flag, or version that cannot be checked against its
named source MUST be flagged by the citation dimension, never assumed valid.
The skeptic agent that re-checks a citation finding must attempt to verify the
cited item against the source the spec names. If the source is not accessible
or does not contain the cited item, the skeptic returns CONFIRMED for the
citation finding (i.e., the un-verifiable citation is a confirmed problem, not
a false positive).

### Resolved-decisions ledger (Step 4b contract)

During the adversarial hardening loop, every confirmed cross-section or
cross-file contradiction that requires a resolution MUST be recorded in the
companion artifact ``spec/<name>-resolved-decisions.md`` as a ``D<N>`` entry.

**Maintaining the ledger**

Use ``devbench.plugin_helpers.resolved_decisions_ledger.append_decision`` to
append each new resolution:

```python
from devbench.plugin_helpers.resolved_decisions_ledger import (
    DecisionEntry,
    DuplicateResolutionError,
    append_decision,
    read_ledger,
)

decision = DecisionEntry(
    index=0,  # assigned automatically
    contradiction="<description of the cross-section conflict>",
    resolution="<the chosen resolution, verbatim>",
    rationale="<why this resolution was preferred>",
)
try:
    entry = append_decision(ledger_path, decision)
    # entry.index holds the assigned D<N> integer
except DuplicateResolutionError as err:
    # The contradiction was already recorded -- defer to the existing entry.
    # Do NOT append a conflicting second entry.
    pass
```

**Deferring in later rounds**

Before resolving any contradiction in a subsequent review round, call
``read_ledger(ledger_path)`` and check whether the contradiction is already
recorded.  When a match is found, use the existing ``**Resolution:**`` text
verbatim rather than re-litigating the decision.  ``append_decision`` enforces
this contract automatically by raising ``DuplicateResolutionError`` on an
attempt to re-record an existing contradiction.

**Emitting the ledger as a companion artifact**

The ledger file is written alongside the spec file and consumed by
``spec-to-backlog`` as the contradiction tie-breaker (wired in E12-F3-S2).
When the spec is written in Step 6, confirm that the ledger file exists at
``spec/<name>-resolved-decisions.md``; if no contradictions were encountered
during the adversarial loop, the ledger file may be absent or empty -- that
is acceptable.

---

## Step 5 -- Final operator review before writing

Present the full spec to the operator with a summary of:
- Total line count
- Number of sections (and any that were marked N/A)
- Number of ACs

Ask: "Does this look good to write to `spec/<project-name>.md`? Or do you have feedback to incorporate?"

- **On "looks good"**: write the spec to `spec/<project-name>.md` using the Write tool.
- **On feedback**: add the operator's note as an additional rubric item and re-enter the iterate loop (Step 4) with the feedback as the highest-priority unresolved item. Revise and re-present.

---

## Step 6 -- Write the spec and offer handoff

Once the operator approves, run the backlog-readiness self-check against the
approved spec text **before writing the file**, so decomposition failures
surface here rather than inside `spec-to-backlog`.

When the Workflow tool is available (i.e., ``skills.use_workflow`` is ``true``
in ``backlog/config/devbench.yaml`` and the Workflow tool is present), run
the self-check as a single decomposability-audit agent invocation:

```python
from devbench.plugin_helpers.spec_backlog_contract import check_backlog_readiness
spec_text = <the full approved spec text as a string>
is_multi = <True when the spec covers more than one work unit, False otherwise>
check_backlog_readiness(spec_text, is_multi_unit=is_multi)
```

When the Workflow tool is absent, run the same call as a direct single-agent
self-check (no sub-agent spawning required; the call is synchronous and pure).

If ``check_backlog_readiness`` raises ``ReadinessError``, emit the error
message verbatim to the operator, do NOT write the spec, and exit non-zero.
The error message names the missing element and the required fix.

Once the self-check passes (returns without raising), write the spec:

```
Write spec/<project-name>.md
<full spec content>
```

Confirm the write succeeded by reading back the first 20 lines:

```
Read spec/<project-name>.md
```

Emit the provenance audit comment naming the exemplar consulted in Step 1a. When `skills.exemplar_spec_path` was set and resolved, emit the resolved path; when it was absent, emit the literal token `<embedded-canonical-sections>` so the audit trail records that no external exemplar was consulted:

```
[QUALITY_REFERENCE] <resolved-exemplar-path-or-embedded-canonical-sections>
```

This audit line is mandatory -- it records what quality reference (workspace exemplar or embedded section list) was consulted so the orchestrator's audit trail captures provenance for every skill invocation that authors a spec.

Then offer the spec-to-backlog handoff:

> Spec written to `spec/<project-name>.md` (<line count> lines).
>
> Would you like me to invoke the `spec-to-backlog` skill now to decompose this spec into a BACKLOG.md + work-unit files? The skill will read this spec as input and produce a 4-level backlog hierarchy (Epic -> Feature -> Story -> Task).

---

## Output contract

- **Output file**: `spec/<project-name>.md`
- **Target size**: 1000+ lines for non-trivial programs; smaller for minor features (a 200-line spec is appropriate for a single-feature change; a 50-line spec is always insufficient for a new subsystem)
- **Quality gate**: rubric score must be zero unresolved items before the spec is written
- **FR list**: at least one `FR-N:` line is present in the spec
- **AC-N marker**: the literal line `<!-- AC-SECTION-START -->` appears immediately before the AC-N list
- **Target Repository**: `Repo:` and `Branch:` fields are present
- **Unit Inventory**: present for multi-unit specs; absent is acceptable for single-unit specs
- **Readiness self-check**: `check_backlog_readiness` from `devbench.plugin_helpers.spec_backlog_contract` returns without raising before the spec is written
- **Handoff**: on operator consent, invoke the `spec-to-backlog` skill with this spec as input
- **Provenance**: `[QUALITY_REFERENCE]` audit comment emitted on completion naming either the resolved workspace exemplar path or the literal `<embedded-canonical-sections>` token

---

## Self-critique loop (bounded)

The self-critique loop must terminate -- either when the rubric reports
zero unresolved items (success) or when the iteration budget is exhausted
(escalation). The bound is enforced by the helpers in
`src/devbench/skill_state.py`:

- Before scoring the spec, call `read_checkpoint("create-spec", workspace_root)`
  to load the previous iteration counter (returns `None` on the first pass).
- After scoring, if `unresolved_count <= SKILL_QUALITY_THRESHOLD` (the constant
  defined in `src/devbench/constants.py`), call
  `emit_audit("create-spec", SKILL_AUDIT_QUALITY_THRESHOLD_REACHED, {...}, workspace_root)`
  and exit success.
- Otherwise increment the iteration in the checkpoint via
  `write_checkpoint("create-spec", state, workspace_root)` and continue.
- When the iteration reaches `skills.max_iterations` (from
  `backlog/config/devbench.yaml`, falling back to `SKILL_MAX_ITERATIONS`),
  call
  `emit_audit("create-spec", SKILL_AUDIT_MAX_ITERATIONS_REACHED, {"unresolved": ...}, workspace_root)`
  and exit non-zero so the orchestrator surfaces the unresolved items for
  operator review.

The audit tags `[SKILL_MAX_ITERATIONS_REACHED]` and
`[SKILL_QUALITY_THRESHOLD_REACHED]` flow through the existing report and
hook-tail pipelines without any new infrastructure.

---

## Reusable Workflow Authoring Patterns

For Workflow-mode invocations that apply multi-round authoring with fan-out,
adversarial verification, decisions-ledger tie-breaking, deterministic gates,
file-partitioned parallel repair, or file-based agent output, consult the
shared patterns reference rather than implementing the patterns inline:

`docs/workflow-authoring-patterns.md`

Each pattern is defined once in that document with a generic form that applies
to any spec or backlog domain. Do not restate pattern bodies in this SKILL.md.
