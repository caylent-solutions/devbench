# Gap Fill Multi-File Test Spec

## Section 1 -- Purpose

This fixture spec is used by `test_spec_to_backlog_gap_fill.py` to verify that the
spec-to-backlog gap-fill loop in SKILL.md Step 7c:

- Routes NEW TASK gaps through the existing Step-5 authoring path.
- Routes ENHANCE gaps through file-partitioned fan-out.
- Loops re-running the post-processor and validate-backlog until zero confirmed gaps.
- Declares success only at zero confirmed gaps AND validate-backlog rc=0.
- Falls back to single-agent FR/AC citation rubric when Workflow is unavailable.

## Section 2 -- Functional Requirements

FR-1: The system must author new task files for NEW TASK gaps using the 15-section Step-5 path.
FR-2: The system must enhance existing task files for ENHANCE gaps via file-partitioned fan-out.
FR-3: After each gap-fill round the system must re-run the index regeneration, post-processor, and validate-backlog.
FR-4: The gap-fill loop must re-audit after each validate-backlog pass and repeat until zero confirmed gaps.
FR-5: The system must emit a [BLOCKED] escalation listing unresolved gaps when skills.max_iterations is reached.
FR-6: Success must be declared only when both zero confirmed gaps remain AND validate-backlog returns rc=0.
FR-7: When the Workflow tool is unavailable the single-agent FR/AC citation rubric must run unchanged.
FR-8: All thresholds and round counts must be config-driven via skills.max_iterations in devbench.yaml.

<!-- AC-SECTION-START -->

## Section 6 -- Acceptance Criteria

AC-1: NEW TASK gaps are authored via the existing Step-5 path with all 15 canonical sections.
AC-2: ENHANCE gaps use file-partitioned fan-out, one agent per task file, adding missing ACs/Approach/Manifest/DoD.
AC-3: After each gap-fill round the loop regenerates the index, runs the post-processor and validate-backlog, then re-audits.
AC-4: The loop repeats until zero confirmed gaps or skills.max_iterations is reached (then [BLOCKED]).
AC-5: Success is declared only at zero confirmed gaps AND validate-backlog rc=0.
AC-6: When Workflow is unavailable the single-agent FR/AC citation rubric runs unchanged; thresholds are config-driven.
