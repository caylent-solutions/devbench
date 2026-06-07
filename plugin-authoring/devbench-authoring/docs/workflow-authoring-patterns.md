# Workflow Authoring Patterns

This document defines the reusable, application-agnostic Workflow authoring
patterns that are shared across the `create-spec` and `spec-to-backlog` skills.
Each pattern is a generic property of any specification or backlog decomposition
process; none of the patterns assume a particular domain or taxonomy.

Reference this document from skill instruction files rather than restating
pattern bodies inline. Adding a new pattern: append a `##`-level section below
and update the cross-references in both SKILL.md files.

---

## Pattern 1: Dimension Fan-Out

**Summary.** When a single high-level concern must be evaluated against multiple
independent dimensions (e.g., correctness, security, performance, coverage), the
skill fans the concern out to one parallel assessment per dimension rather than
processing all dimensions in a single serial pass.

**Generic form.**
1. Enumerate the set of dimensions from configuration or context; do not
   hardcode dimension lists.
2. Spawn one independent analysis thread per dimension. Threads share no
   mutable state and write their findings to separate output files.
3. Collect all per-dimension files after the fan-out completes and merge
   results deterministically before proceeding to the next step.

**Why it matters.** Serial evaluation allows early findings in one dimension to
bias later dimensions. Fan-out removes that ordering artifact and makes the
evaluation width configurable without changing the core loop.

---

## Pattern 2: Per-Finding Adversarial Verification (Default-Reject)

**Summary.** Every finding produced by a generative step is treated as
unverified until an independent verification pass either confirms it or rejects
it. When verification is inconclusive or impossible to perform, the finding is
rejected by default rather than passed through.

**Generic form.**
1. The authoring pass produces a list of candidate findings (claims, assertions,
   acceptance criteria, decisions).
2. For each finding, an adversarial verification agent attempts to falsify it:
   produce a counter-example, locate a missing prerequisite, or identify an
   unresolvable ambiguity.
3. A finding survives only if the adversarial agent explicitly confirms it as
   verifiable. Findings that the agent cannot confirm are marked rejected and
   the authoring pass must either remove them or revise them into a verifiable
   form.
4. The total verification outcome is a two-list split: verified findings and
   rejected findings. Rejected findings are surfaced to the operator rather than
   silently dropped.

**Why it matters.** LLMs readily produce plausible but unverifiable claims.
Default-reject forces every output to carry a verification chain; the skill
never ships a finding it cannot substantiate.

---

## Pattern 3: Decisions-Ledger as Cross-Round Tie-Breaker

**Summary.** All design decisions made during a multi-round authoring or
review process are recorded in a persistent decisions ledger. When two
consecutive rounds disagree on a question that was previously resolved, the
ledger entry for that question is the authoritative tie-breaker and the later
round's conflicting conclusion is overridden.

**Generic form.**
1. The ledger is a list of entries with the shape:
   `{question, decision, rationale, alternatives_considered, round_number}`.
2. At the start of every new round, the authoring agent reads the ledger to
   restore prior context.
3. When the current round produces a conclusion that conflicts with a ledger
   entry, the agent logs the conflict and applies the ledger's decision rather
   than the round's conclusion.
4. New decisions (ones with no prior ledger entry) are appended to the ledger
   at the end of the round.

**Why it matters.** Without a ledger, successive LLM rounds tend to relitigate
resolved questions, producing inconsistent output. The ledger makes the
decision space convergent.

---

## Pattern 4: Deterministic Gates Between LLM Rounds

**Summary.** Between every pair of LLM authoring rounds a deterministic
(non-LLM) gate checks a fixed set of structural invariants. A round whose
output fails any gate check does not advance; the skill re-runs the failing
round rather than propagating a structurally invalid intermediate state.

**Generic form.**
1. Define one gate function per invariant class (e.g., required sections
   present, no circular dependencies, IDs match canonical regex).
2. After each LLM round emits its output, run all gate functions against the
   output.
3. If every gate passes (rc=0), advance to the next round.
4. If any gate fails, surface the failing assertions to the authoring agent and
   re-run the current round with the gate output appended as a correction
   prompt.
5. The number of re-run attempts per round is bounded by the skill's
   `max_iterations` budget.

**Why it matters.** LLMs can produce structurally plausible but semantically
invalid output. Running gates deterministically -- rather than asking the LLM
to self-check structure -- eliminates entire classes of structural drift between
rounds.

---

## Pattern 5: File-Partitioned Parallel Repair (One Agent Per File)

**Summary.** When a set of files must be repaired (corrected, reformatted, or
brought into conformance), the repair work is partitioned by file: one
independent agent handles exactly one file and writes its corrected output to
disk. Agents operate concurrently without coordinating with each other.

**Generic form.**
1. Identify the set of files that need repair from a pre-computed findings list.
2. For each file, spawn one repair agent with the file path, the list of
   findings specific to that file, and the correction rules.
3. Each agent reads its file, applies the corrections, and writes the result
   back to the same path. The agent returns a short confirmation message (not
   the entire corrected file content).
4. After all repair agents complete, run the deterministic gate (Pattern 4) to
   confirm all files now pass.

**Why it matters.** Repairing multiple files in a single LLM call requires the
agent to hold all file contents in context simultaneously, which increases
context pressure and error rate. File-partitioned repair keeps each agent's
context small and makes partial failures easy to diagnose.

---

## Pattern 6: File-Based Agent Output

**Summary.** When an agent produces a finding, correction, or analysis result,
the output is written to a named file on disk. The calling skill reads that file
to consume the result rather than relying on structured return values embedded
in the agent's response message.

**Generic form.**
1. Before spawning an agent, the skill designates an output file path for that
   agent's results.
2. The agent writes its findings or output to the designated file using the
   Write tool.
3. The agent returns a short confirmation to the calling skill (e.g.,
   `"findings written to <path>"`) rather than embedding all findings in the
   return message.
4. The calling skill reads the designated file to consume the agent's output.

**Why it matters.** Large structured returns from many concurrent agents
frequently fail due to context length limits and response parsing issues.
File-based output decouples agent result size from the inter-agent message
channel, making concurrent multi-agent workflows reliable at scale (see spec
Section 1 robustness finding).

---

## Usage in Skills

Both `create-spec` and `spec-to-backlog` reference these patterns where
applicable. See:

- `skills/create-spec/SKILL.md` -- references this doc for Workflow mode
- `skills/spec-to-backlog/SKILL.md` -- references this doc for fan-out and
  parallel repair steps

Do not copy pattern body text into SKILL.md files. Instead, cite the relevant
pattern by number and name, and link back to this file.
