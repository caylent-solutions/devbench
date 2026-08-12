# ADR-33: Flatten review topology -- reproduction findings and design record

**Status:** Accepted (decision recorded; downstream necessity flagged for
re-evaluation -- see "Reconciliation" below)
**Date:** 2026-07-28

---

## Context

`plugin/devbench-orchestrate/agents/review-supervisor.md` declares, on `main`
at the time of this reproduction:

```yaml
---
name: review-supervisor
description: Discovers and invokes all review_team agents in parallel, aggregates verdicts, returns consolidated pass/fail. Invoke with a work unit ID (e.g. E0-F1-S1-T1).
model: sonnet
tools: Bash, Agent(code-reviewer, test-reviewer, doc-reviewer, changes-manifest)
---
```

`plugin/devbench-orchestrate/skills/orchestrate/SKILL.md` line 83 confirms
review-supervisor is itself invoked as a first-level sub-agent by the
orchestrate skill (`5. Invoke \`review-supervisor\` with the unit ID.`),
and line 134 makes it the done-gate ("Never bypass the done-gate --
review-supervisor must pass before git-ops."). The Claude Agent SDK's own
sub-agent documentation states that a sub-agent cannot spawn further
sub-agents and that an `Agent(...)` entry in a sub-agent's frontmatter has
no effect. If that restriction held here, `review-supervisor`'s own
`Agent(...)` declaration would be inert: the four `review_team` judges
would never run, and the done-gate would pass on the strength of
`review-supervisor`'s own unverified aggregation, not on four independent
judge executions.

This was not a theoretical concern. ADR-28 on
`origin/feat/flatten-review-pipeline` (read at pre-strip commit
`4fd906c74c1ccc5372b3d4b3ba26bf35705b0b5c`, dated 2026-06-10T18:55:29Z, per
decision D-16 -- this commit predates `6188aab`, "Remove all explanatory
Python comments," which strips rationale but does not touch
`docs/adr/28-flatten-review-pipeline.md` at all, confirmed via
`git merge-base --is-ancestor 4fd906c74c1ccc5372b3d4b3ba26bf35705b0b5c 6188aab`)
diagnosed a real incident: work unit `E9-F1-S1-T5` became an unclearable
`RUNTIME_DEGRADATION` that no restart could clear. ADR-28 attributes the
incident structurally, to the SDK's sub-agent nesting restriction, and
contains no mention of `haiku` and no model-tier attribution for that run
at all. ADR-28 cites the SDK's own subagent documentation and three
upstream issue trackers (`anthropics/claude-code#19077`, `#61993`,
`#31977`) against SDK version `claude-agent-sdk 0.2.91`.

Spec S1.0's weakest-links table marks the judge failure mode `[I]`
(inference, not verified): does the topology silently skip the four
judges, fail loudly naming the unavailable `Agent` tool, or surface via the
`review-supervisor[^\n]*only\s+Bash` regex branch of
`_RUNTIME_DEGRADATION_BODY_RE` in `src/devbench/backlog/proposal.py`? This
ADR resolves that marker against a live orchestrate session, per FR-4.0's
mandate that no rewrite (E4-F1-S1-T2) proceed on an unreproduced mode.

A second, corroborating fact narrows the likely original cause: ADR-25
("Per-agent model overrides via workspace-local shadow plugin", accepted
2026-05-15 -- i.e. *before* the E9-F1-S1-T5 incident ADR-28 diagnosed on
2026-06-10) records: "Haiku was tried for `review-supervisor` and dropped:
under load the SDK silently removed the Agent tool from haiku's tool list,
breaking parallel review_team dispatch," and ADR-25 subsequently made
`haiku` rejected at config-load time for every work agent
(`caylent-solutions/devbench#198`). `review-supervisor.md`'s current
frontmatter on `main` pins `model: sonnet`, not `haiku`. This reproduction
was run against that current, post-ADR-25 configuration.

## Reproduction protocol

Exact steps performed, in order:

1. **Fetch and read the pre-strip ADR-28.**
   ```
   git fetch origin feat/flatten-review-pipeline
   git ls-tree -r origin/feat/flatten-review-pipeline docs/adr | grep -i flatten
   git show -s --format='%H %cI %s' 4fd906c74c1ccc5372b3d4b3ba26bf35705b0b5c
   git merge-base --is-ancestor 4fd906c74c1ccc5372b3d4b3ba26bf35705b0b5c 6188aab && echo ancestor-confirmed
   git show 4fd906c74c1ccc5372b3d4b3ba26bf35705b0b5c:docs/adr/28-flatten-review-pipeline.md
   ```
   Result: `4fd906c74c1ccc5372b3d4b3ba26bf35705b0b5c 2026-06-10T18:55:29Z fix(review): flatten review pipeline + unit-scoped round token (ADR-28)`,
   confirmed an ancestor of the comment-strip commit `6188aab`.

2. **Confirm the misconfiguration on the working branch.**
   ```
   sed -n '1,6p' plugin/devbench-orchestrate/agents/review-supervisor.md
   grep -n "review-supervisor" plugin/devbench-orchestrate/skills/orchestrate/SKILL.md
   ```
   Confirmed: frontmatter line 5 is
   `tools: Bash, Agent(code-reviewer, test-reviewer, doc-reviewer, changes-manifest)`;
   `model: sonnet` (line 4); SKILL.md line 83 invokes `review-supervisor` as
   a first-level sub-agent, line 134 makes it the done-gate.

3. **Provisioned a throwaway scratch workspace** at
   `/tmp/e4f1s1t1-repro/workspace/`, entirely outside this repo's checkout,
   containing:
   - `dummy-repo/` -- a standalone git repository (`scratch-org/dummy-repo`)
     with a `src/dummy/calc.py` module (`add(a, b)`), a matching
     `tests/test_calc.py` (pytest, `@pytest.mark.unit`), a `pyproject.toml`
     declaring the `unit` marker and a `src`-layout package, and a
     `Makefile` with `test-unit` and `validate` targets.
   - `backlog/config/devbench.yaml` -- minimal valid config: one repo entry
     (`scratch-org/dummy-repo`), `allowed_orgs: []`, `max_executor_retries: 1`,
     `git_ops: {single_branch: scratch-repro, local_only: true, defer_pr: true,
     auto_finalize: false, auto_merge: false, pause_before_merge: false}`
     (local-only mode: no GitHub remote, no CI, no PR -- isolates the
     observation to the Agent-tool spawn topology alone),
     `manifest_amendment.enabled: false`, `task_factory.enabled: false`,
     `notifications.enabled: false`.
   - `CLAUDE.md` -- a two-line TDD/standards note scoped to the scratch task.
   - `BACKLOG.md` and `backlog/EX-F1-S1-T1.md` -- one dummy Task ("Add a
     multiply function to the dummy calc module"), Changes Manifest
     `src/dummy/calc.py` (modify) + `tests/test_calc.py` (modify).
   `uv run devbench validate-backlog` (run with
   `DEVBENCH_WORKSPACE_ROOT=/tmp/e4f1s1t1-repro/workspace`) confirmed:
   "Backlog integrity check passed."

4. **Ran a live orchestrate session** over the dummy work unit, without
   intervention:
   ```
   DEVBENCH_WORKSPACE_ROOT=/tmp/e4f1s1t1-repro/workspace \
   DEVBENCH_CLAUDE_MODEL=claude-opus-5 \
   timeout 900 uv run --project /workspaces/general-dev/repos/devbench-updates/devbench \
     python3 -m devbench.cli start
   ```
   Session id `32862e10-7ede-4265-8892-e0637684bb3e`; `claude_code_version`
   `2.1.220`; locked `claude-agent-sdk` version `0.2.128` (per this repo's
   `uv.lock`, materially newer than the `0.2.91` ADR-28 cited). Top-level
   session and `security-reviewer` ran on `model='claude-opus-5'`.
   `review-supervisor`'s own spawning turn -- the `AssistantMessage` whose
   own `tool_use_id` (`toolu_01FCCoDBH6W7KarFskr5kd1S`) is the
   `parent_tool_use_id` of the four judge `Agent` calls below -- ran on
   `model='claude-sonnet-5'`, matching its own `model: sonnet` frontmatter.
   The captured stream carries **no separate model attribution for the
   judges' own execution turns**: the only `AssistantMessage` bearing each
   judge's `tool_use_id` is `review-supervisor`'s spawning turn itself, not
   a turn belonging to the judge. All four judges' frontmatter declares
   `model: opus`; this reproduction can neither confirm nor deny per-judge
   model routing from the artifacts retained, and the `claude-sonnet-5`
   value belongs to `review-supervisor`'s turn only.

5. **Observed the review leg (SKILL.md step 5)** by reading the session's
   structured SDK message stream (`TaskStartedMessage` / `TaskProgressMessage`
   / `TaskUpdatedMessage` / `AssistantMessage` with `ToolUseBlock`), the
   scratch work unit's persisted Comments section, and the final
   `devbench drain --status` summary -- see Transcript evidence below.

6. **Captured the transcript excerpts** proving the classification, quoted
   verbatim below with session id and timestamps.

**Note on the `harness/devbench` checkout.** This workspace also contains a
separate git checkout at `harness/devbench` (the devbench-orchestrate
plugin harness used to run the orchestrator process itself), which
independently carries uncommitted modifications on branch
`fix/review-spawn-topology`. Those modifications belong to a different
repository outside this task's checkout -- this task's `repo_path` is this
repo, not `harness/devbench` -- and are not part of this task's Changes
Manifest; `git status --porcelain` in this checkout confirms this task's
own change set is the single ADR file added here. More directly relevant
to reproduction validity: the live orchestrate session in step 4 invoked
the `Agent` tool against **this checkout's own, unflattened**
`plugin/devbench-orchestrate/agents/review-supervisor.md` (confirmed
still declaring `tools: Bash, Agent(...)` at the time of the session, per
step 2 above), never against `harness/devbench`'s copy. `harness/devbench`
was not on the `PYTHONPATH`, plugin search path, or `--project` argument
used to start the session (step 4's command targets this checkout via
`--project /workspaces/general-dev/repos/devbench-updates/devbench`).
Whatever state that separate checkout was in, and whenever any
modification was made there, is therefore irrelevant to the validity of
this reproduction: the session could not have exercised a plugin
definition it never referenced.

## Transcript evidence

The top-level session's `AssistantMessage` invoked `review-supervisor` as a
first-level sub-agent via the `Agent` tool:

```
2026-07-28T19:52:05Z AssistantMessage(content=[ToolUseBlock(
  id='toolu_01FCCoDBH6W7KarFskr5kd1S', name='Agent',
  input={'description': 'Review EX-F1-S1-T1',
         'subagent_type': 'devbench-orchestrate:review-supervisor',
         'run_in_background': False, 'prompt': 'EX-F1-S1-T1'})],
  model='claude-opus-5', parent_tool_use_id=None,
  session_id='32862e10-7ede-4265-8892-e0637684bb3e')
```

**Within that same sub-agent's own turn** (`parent_tool_use_id` equal to
`review-supervisor`'s own `tool_use_id`, `toolu_01FCCoDBH6W7KarFskr5kd1S`,
and `model='claude-sonnet-5'` matching its frontmatter), `review-supervisor`
issued four further `Agent` tool calls -- a second-level spawn:

```
2026-07-28T19:52:16Z ToolUseBlock(id='toolu_01SJ9aXEins32MnGKC2pVrZP', name='Agent',
  input={'subagent_type': 'devbench-orchestrate:review_team:code-reviewer', ...},
  parent_tool_use_id='toolu_01FCCoDBH6W7KarFskr5kd1S')
2026-07-28T19:52:16Z ToolUseBlock(id='toolu_01DdrW559MTRS4V8zDf6jwKx', name='Agent',
  input={'subagent_type': 'devbench-orchestrate:review_team:test-reviewer', ...},
  parent_tool_use_id='toolu_01FCCoDBH6W7KarFskr5kd1S')
2026-07-28T19:52:17Z ToolUseBlock(id='toolu_01Ca8oi4LM5a1bM3eWaESTPp', name='Agent',
  input={'subagent_type': 'devbench-orchestrate:review_team:doc-reviewer', ...},
  parent_tool_use_id='toolu_01FCCoDBH6W7KarFskr5kd1S')
2026-07-28T19:52:18Z ToolUseBlock(id='toolu_016x6mnRvh4TW8xAEk8PDt7x', name='Agent',
  input={'subagent_type': 'devbench-orchestrate:review_team:changes-manifest', ...},
  parent_tool_use_id='toolu_01FCCoDBH6W7KarFskr5kd1S')
```

All four ran to completion with real, independent, multi-step work, counted
by distinct `task_progress` events per judge, each with its own measured
window: `code-reviewer` 20 tool uses (`19:52:19Z`--`19:53:37Z`),
`test-reviewer` 26 (`19:52:19Z`--`19:54:39Z`), `doc-reviewer` 13
(`19:52:21Z`--`19:53:21Z`), `changes-manifest` 15
(`19:52:24Z`--`19:53:41Z`). Sampled activity descriptions
include "Get authoritative diff for work unit", "Run make test-unit in
target repo", "Mutation-check that test_multiply fails when implementation
is broken", "Commit-attribution check against main", "Log final pass
verdict" -- genuine tool-backed investigation, not templated pass-through.

Each judge self-logged a real, persisted `[REVIEW_PASS]` verdict via
`uv run devbench log-verdict`, confirmed present in the scratch work unit's
Comments section (`/tmp/e4f1s1t1-repro/workspace/backlog/EX-F1-S1-T1.md`):

```
[2026-07-28 19:53 UTC] [judge/doc_review] [REVIEW_PASS] PASS - verified DOC_SYNC, STALE_REFERENCES, ...
[2026-07-28 19:53 UTC] [judge/code_review] [REVIEW_PASS] PASS - all 3 AC met and independently verified ...
[2026-07-28 19:53 UTC] [judge/changes_manifest] [REVIEW_PASS] PASS: SCOPE, STAGING, COMMIT-ATTRIBUTION, ...
[2026-07-28 19:54 UTC] [judge/test_review] [REVIEW_PASS] PASS: verified REAL_TESTS (mutation-proven failing assertion), TDD ...
[2026-07-28 19:56 UTC] [agent/review-supervisor] All review_team members passed (code_review, test_review, doc_review, changes_manifest each self-logged [REVIEW_PASS] verdicts, verified present in current round with correct canonical judge names, no intervening REVIEW_REJECTED boundary)
```

The orchestrate skill then proceeded (per SKILL.md step 8, no re-invocation
of `review-supervisor`) directly to `security-reviewer`, which fetched the
real diff and logged its own `[REVIEW_PASS]`:

```
2026-07-28T19:57:05Z ToolUseBlock(name='Bash', input={'command':
  'uv run devbench log-verdict security_review EX-F1-S1-T1 pass "PASS: ..."'})
[2026-07-28 19:57 UTC] [judge/security_review] [REVIEW_PASS] PASS: in-scope diff (src/dummy/calc.py, tests/test_calc.py) adds a pure arithmetic function and its unit test; no secrets, injection, crypto, authn/authz, container, dependency, or bypass-annotation findings at any severity
```

git-ops and mark-done followed with no retry, no amendment, no
degradation:

```
[2026-07-28 19:57 UTC] [agent/git_ops] [COMMIT_DEFERRED] EX-F1-S1-T1: Add a multiply function to the dummy calc module
[2026-07-28 19:57 UTC] [agent/orchestrator] [DONE] Work unit EX-F1-S1-T1 completed
```

The final backlog status summary (`devbench drain --status`, verbatim)
confirms the loop terminated cleanly with zero degraded units:

```
Backlog Status Summary
========================================
  TOTAL                               1
  ...
  Done                                1
  Blocked (auto-clearing)             0
  ...
  Blocked (runtime-degradation)       0
  ...
All work units are DONE.
```

No occurrence of `agent-tool-unavailable`, `Self-check`, or
`review-supervisor[^\n]*only\s+Bash` appears anywhere in the session's
message stream: `review-supervisor`'s pre-existing "Step 0: Self-check
Agent tool availability (issue #183)" block never had cause to fire,
because the `Agent` tool genuinely was present and functional in its tool
list.

## Observed failure mode

**The anticipated failure mode did not reproduce.** Live evidence rules out
all three anticipated classifications:

- **Not silent skip.** All four judges have independent `task_progress`
  event streams (20, 26, 13, 15 tool uses respectively), each with distinct
  investigative activity and each ending in a self-logged, persisted
  `[REVIEW_PASS]` verdict that `review-supervisor`'s own aggregation
  message explicitly confirms it found "in current round with correct
  canonical judge names."
- **Not loud failure.** No error, exception, or `[BLOCKED]
  agent-tool-unavailable` comment was logged. `review-supervisor`'s Step-0
  self-check (which exists specifically to convert an unavailable-Agent-tool
  condition into a loud, attributable failure) never triggered its failure
  branch, because there was no unavailable-tool condition to detect.
- **Not `RUNTIME_DEGRADATION`.** The final drain summary shows zero units
  in the `runtime-degradation` bucket, and no comment anywhere in the
  session matches `_RUNTIME_DEGRADATION_BODY_RE`
  (`agent-tool-unavailable|review-supervisor[^\n]*only\s+Bash`).

This **resolves the S1.0 `[I]` marker to Verified**, but not to either of
the two modes the marker anticipated: under the current pinned
configuration (`review-supervisor` on `model: sonnet` per its frontmatter,
`claude-agent-sdk` 0.2.128, `claude_code_version` 2.1.220),
`review-supervisor`'s second-level `Agent(...)` spawn of the four
`review_team` judges succeeds completely and reliably, with genuinely
attributable, independently-verified per-judge verdicts satisfying spec
AC-64's evidence baseline.

## Reconciliation: reproduction versus the original incident

This reproduction's outcome does not contradict ADR-28's account of
`E9-F1-S1-T5`; it narrows the likely cause. ADR-25 (accepted 2026-05-15,
*before* the `E9-F1-S1-T5` incident ADR-28 diagnosed on 2026-06-10) records
in its own words: "Haiku was tried for `review-supervisor` and dropped:
under load the SDK silently removed the Agent tool from haiku's tool list,
breaking parallel review_team dispatch." `review-supervisor.md`'s current
frontmatter pins `model: sonnet`, and ADR-25 additionally rejects `haiku`
for every work agent at config-load time. It is plausible, and consistent
with all available evidence, that the specific mechanism behind the
`E9-F1-S1-T5` `RUNTIME_DEGRADATION` was `review-supervisor` running on
`haiku` at the time (a model-tier-specific SDK behaviour, not a universal
"sub-agents can never spawn sub-agents" restriction), and that ADR-25's
model-pinning fix already closed that specific hole independently of any
topology change. The generic Claude Agent SDK subagent documentation ADR-28
cites ("a sub-agent cannot spawn sub-agents") did not hold in this live
reproduction against `claude-agent-sdk` 0.2.128 with `review-supervisor` on
`sonnet`.

**This ADR still records the flatten design below**, per this task's
Acceptance Criteria and Definition of Done, because the design was already
adopted at the spec level (S0 B-9a) as the intended direction for FR-4.0
independent of this task's specific finding, and `E4-F1-S1-T2` (which
depends on this task) is queued to implement it. The finding above is
recorded so that `E4-F1-S1-T2` and any operator reviewing it can weigh the
flatten as **defense-in-depth against a model-tier-dependent SDK behaviour
that ADR-25 already mitigates by pinning**, rather than as a fix for an
actively-reproducing bug in the current, post-ADR-25 configuration. No
downstream task status was changed by this finding; it is recorded here as
evidence for the next reviewer of `E4-F1-S1-T2`.

## Decision

Flatten the review topology: the four `review_team` judges
(`code-reviewer`, `test-reviewer`, `doc-reviewer`, `changes-manifest`)
become first-level sub-agents invoked directly from the orchestrate skill
(`SKILL.md` step 5), each receiving the unit ID independently.
`review-supervisor` is reduced to (or replaced by) non-spawning
aggregation: it no longer declares `Agent(...)` in its `tools:` frontmatter
and no longer dispatches the judges itself; it (or its replacement) reads
the four judges' independently-persisted `[REVIEW_PASS]` /
`[REVIEW_FAIL]` verdicts from the work unit's Comments section and reports
the consolidated result. A missing verdict from any required judge is a
hard failure, not a silent pass, per spec S0 B-9a. This removes any
dependency on second-level `Agent(...)` spawn behaviour succeeding at all,
regardless of which model tier any given agent is pinned to.

## ADR-28 citation

ADR-28 ("Flatten the code-review pipeline (no nested sub-agents)"), read at
commit `4fd906c74c1ccc5372b3d4b3ba26bf35705b0b5c` (2026-06-10T18:55:29Z) on
`origin/feat/flatten-review-pipeline`, the pre-strip commit per decision
D-16 (this commit is a confirmed ancestor of the later comment-strip
commit `6188aab`, "Remove all explanatory Python comments," which does not
touch `docs/adr/28-flatten-review-pipeline.md`). ADR-28 diagnosed the
`E9-F1-S1-T5` `RUNTIME_DEGRADATION` and proposed the same flatten shape
this ADR adopts: no sub-agent spawns sub-agents.

## Scope boundary

This task, and the whole of E4-F1, does **not** adopt the rest of the
`feat/flatten-review-pipeline` branch. Explicitly out of scope, per spec
Section 12 item 2:

- `src/devbench/review_token.py`
- ADR-29's file-based round token
- `guard-verdict-format.sh` (the branch's version; the canonical hook of
  the same name on `main` is unaffected)
- The judge-prompt rewrites on that branch

This task changes exactly one file (this ADR). No `plugin/`, `src/`, or
`tests/` file was touched. The rewrite implementing the flatten decision
recorded above belongs to `E4-F1-S1-T2`.
