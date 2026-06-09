# ADR-27: Acceptance-Criteria Evidence Gate and Optional IaC Judge

**Status:** Accepted
**Date:** 2026-06-08

---

## Context

A real devbench run (the tools-telemetry IaC backlog) marked many work units
`done` whose Acceptance Criteria / Definition-of-Done demanded a real
`terragrunt apply` / `make tf-test`, while only static checks ran and nothing
was provisioned. ADR-25 (done-integrity hardening) made the `done` write path
non-forgeable, but the gate still proved only that the five core review judges
logged `[REVIEW_PASS]`: nothing parsed the requirement list, ran it, and blocked
on the captured exit code. Soft "flag this" prose in the judges could not
reliably catch a false `[x]`.

Two further constraints shaped the design:

1. **Certainty must anchor on Acceptance Criteria (AC).** The Definition of Done
   stays a process checklist; "DoD/AC agree" means the DoD may not assert any
   verifiable/executable outcome that is not also an AC.
2. **The five core review judges (`code_review`, `test_review`, `doc_review`,
   `changes_manifest`, `security_review`) are always-on and non-disableable.**
   Adding specialty review without weakening the core required a separate,
   opt-in judge tier.

---

## Decision

### 1. `## Verification` contract + deterministic evidence

Each work unit may declare a machine-checkable `## Verification` section that
maps each executable AC to a command whose **real** exit code is captured by
`devbench verify-ac` (never self-reported). The supported IaC tool matrix
(Terraform, OpenTofu, Terragrunt, Terratest, CDKTF, AWS CDK, AWS CLI,
CloudFormation, AWS SAM, plus generic deploy/smoke) is a single maintained,
extensible constant (`IAC_TOOL_PATTERNS` in `src/devbench/verification.py`) so a
new tool is a one-line addition. The applicability of the optional IaC judge is
derived deterministically from this contract via
`verification.unit_requires_iac_judge` -- never authored by hand, never
self-judged.

### 2. Optional specialty judges are toggleable; the core five are not

`devbench.constants.OPTIONAL_JUDGE_NAMES` (today `{"iac_review"}`) is the set of
optional specialty judges. `KNOWN_JUDGE_NAMES` is the union of the core five
(`ALL_REQUIRED_JUDGE_NAMES`), the audit-only workflow agents
(`WORKFLOW_AGENT_JUDGE_NAMES`), and `OPTIONAL_JUDGE_NAMES`. The core five are
**not** members of `OPTIONAL_JUDGE_NAMES` and remain mandatory.

Enablement is operator-controlled and **off by default**:

- `optional_judges.iac_review: false` (default) -- never dispatched, never
  required. Override via `DEVBENCH_JUDGE_IAC_REVIEW_ENABLED`.
- `done_gate.allow_deferred_evidence: false` (default) -- a `type=deferred`
  (operator-only) Verification item blocks `mark-done` and is surfaced loudly.
  Override via `DEVBENCH_DONE_GATE_ALLOW_DEFERRED_EVIDENCE`.

### 3. Per-unit required judge set

`BacklogManager._required_judge_set(content)` computes a unit's required judge
set as the always-on core five **union** any enabled optional specialty judge
applicable to that unit. `iac_review` is applicable iff
`optional_judges.iac_review` is `true` AND `unit_requires_iac_judge(content)` is
`true`. When no optional judge is enabled+applicable, the required set is exactly
the core five and the done-gate behaves identically to before this feature. The
gate (`_last_round_all_passed`, called from `mark_done`) is pure code with no
LLM in the loop.

### 4. Judge-sync triad stays atomic

`iac_review` is a canonical (done-gate-satisfying) reviewer verdict, written by
the `devbench-orchestrate:iac-deploy-reviewer` agent. To keep the
default-deny verdict path intact, three lists are kept in sync (enforced by
`infra/scripts/release_acceptance.py` condition (e)):

- `constants.KNOWN_JUDGE_NAMES`
- `guard-verdict-format.sh` `KNOWN_JUDGES` (`iac_review` added) and
  `CANONICAL_REVIEWER_JUDGES` (`iac_review` added)
- the guard's reviewer agent-type allowlist (`iac-deploy-reviewer` added)

---

## Consequences

- A unit cannot reach `done` until every executable AC has tool-captured exit-0
  proof, and -- on AWS/IaC projects that opt in -- until the evidence-verifying
  `iac_review` judge has passed.
- Existing backlogs are unaffected on upgrade: every new config key is optional
  with a default, the optional judge defaults off, and a unit with no enabled
  optional judge is gated exactly as before.
- New tools enter the matrix with a one-line `IAC_TOOL_PATTERNS` addition; the
  judge and applicability detection pick them up automatically.

---

## Related

- ADR-25 (Done-Integrity Hardening) -- the non-forgeable `done` write path this
  gate builds on.
- `docs/backlog-contract.md` -- the `## Verification` contract and done-gate
  description.
