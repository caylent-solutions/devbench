# DevBench Backlog

<!-- Dependency DAG (epic-level)
E0 (Fix Critical Issues)
├── E1 (Devcontainer Integration)  — depends on E0
├── E2 (Reliability)               — depends on E0
├── E3 (Prompt Overrides)          — depends on E0
├── E4 (Backlog Manager)           — depends on E2
└── E5 (Backlog-Native Configuration) — depends on E0

E1, E2, E3 can run in parallel after E0 is done.
E4 starts after E2 is done.
E5 starts after E0 is done.
E2-F2 depends on E2-F1 completing first (within E2).
-->

<!-- Parallel Opportunities
After E0 is done:
- E1, E2, and E3 can all run in parallel (no inter-epic dependencies among them)
- E4 starts after E2 completes
- E5 can run in parallel with E1, E2, E3, and E4 once E0 is complete
- Within E2: E2-F1 and E2-F2 are partially ordered (E2-F2 depends on E2-F1-S2)
-->

## Status Summary

| Epic | Title | Features | Stories | Tasks | Total | Done | In Progress | In Review | In Queue |
|------|-------|----------|---------|-------|-------|------|-------------|-----------|----------|
| E0 | Fix Critical Issues | 1 | 2 | 4 | 7 | 0 | 0 | 0 | 7 |
| E1 | Devcontainer Integration | 1 | 1 | 2 | 4 | 0 | 0 | 0 | 4 |
| E2 | Reliability | 2 | 3 | 4 | 9 | 0 | 0 | 0 | 9 |
| E3 | Prompt Overrides | 1 | 1 | 1 | 3 | 0 | 0 | 0 | 3 |
| E4 | Backlog Manager | 1 | 1 | 1 | 4 | 1 | 0 | 0 | 3 |
| E5 | Backlog-Native Configuration | 1 | 1 | 1 | 4 | 0 | 0 | 0 | 4 |
| **Total** | | **7** | **9** | **13** | **31** | **1** | **0** | **0** | **30** |

---

## Full Work Unit Index

### E0: Fix Critical Issues

| ID | Title | Type | Status | Dependencies | Repo | File Path |
|----|-------|------|--------|--------------|------|-----------|
| E0-F1-S1-T1 | Move ALLOWED_REPOS to JUDGE_ALLOWED_REPOS env var | Task | in-queue | None | devbench | `backlog/E0-fix-critical-issues/E0-F1-config-hardening/E0-F1-S1-remove-hardcoded-config/E0-F1-S1-T1-allowed-repos-env-var.md` |
| E0-F1-S1-T2 | Remove hardcoded WORKSPACE_ROOT default (fail-fast) | Task | in-queue | E0-F1-S1-T1 | devbench | `backlog/E0-fix-critical-issues/E0-F1-config-hardening/E0-F1-S1-remove-hardcoded-config/E0-F1-S1-T2-workspace-root-env-var.md` |
| E0-F1-S1-T3 | Add JUDGE_MERGE_STRATEGY configurable env var | Task | in-queue | E0-F1-S1-T2 | devbench | `backlog/E0-fix-critical-issues/E0-F1-config-hardening/E0-F1-S1-remove-hardcoded-config/E0-F1-S1-T3-merge-strategy-env-var.md` |
| E0-F1-S1 | Remove Hardcoded Config Values | Story | in-queue | None | devbench | `backlog/E0-fix-critical-issues/E0-F1-config-hardening/E0-F1-S1-remove-hardcoded-config/E0-F1-S1.md` |
| E0-F1-S2-T1 | Remove hardcoded JUDGE_GH_ORG, add required var guards to both scripts | Task | in-queue | E0-F1-S1-T3 | devbench | `backlog/E0-fix-critical-issues/E0-F1-config-hardening/E0-F1-S2-harden-start-scripts/E0-F1-S2-T1-required-var-guards.md` |
| E0-F1-S2 | Harden Start Scripts | Story | in-queue | E0-F1-S1-T3 | devbench | `backlog/E0-fix-critical-issues/E0-F1-config-hardening/E0-F1-S2-harden-start-scripts/E0-F1-S2.md` |
| E0-F1 | Config Hardening | Feature | in-queue | None | devbench | `backlog/E0-fix-critical-issues/E0-F1-config-hardening/E0-F1.md` |
| E0 | Fix Critical Issues | Epic | in-queue | None | devbench | `backlog/E0-fix-critical-issues/E0.md` |

### E1: Devcontainer Integration

| ID | Title | Type | Status | Dependencies | Repo | File Path |
|----|-------|------|--------|--------------|------|-----------|
| E1-F1-S1-T1 | Add devcontainer.json, postcreate-wrapper.sh, devcontainer-functions.sh, project-setup.sh | Task | in-queue | E0 | devbench | `backlog/E1-devcontainer-integration/E1-F1-devcontainer-setup/E1-F1-S1-devcontainer-config/E1-F1-S1-T1-devcontainer-files.md` |
| E1-F1-S1-T2 | Add shell.env.example, update .gitignore and .tool-versions, update start scripts to source shell.env | Task | in-queue | E1-F1-S1-T1 | devbench | `backlog/E1-devcontainer-integration/E1-F1-devcontainer-setup/E1-F1-S1-devcontainer-config/E1-F1-S1-T2-shell-env-and-tooling.md` |
| E1-F1-S1 | Add devcontainer configuration | Story | in-queue | E0 | devbench | `backlog/E1-devcontainer-integration/E1-F1-devcontainer-setup/E1-F1-S1-devcontainer-config/E1-F1-S1.md` |
| E1-F1 | Devcontainer Setup | Feature | in-queue | E0 | devbench | `backlog/E1-devcontainer-integration/E1-F1-devcontainer-setup/E1-F1.md` |
| E1 | Devcontainer Integration | Epic | in-queue | E0 | devbench | `backlog/E1-devcontainer-integration/E1.md` |

### E2: Reliability

| ID | Title | Type | Status | Dependencies | Repo | File Path |
|----|-------|------|--------|--------------|------|-----------|
| E2-F1-S1-T1 | Add require_judge_approval guard in backlog/manager.py | Task | in-queue | E0 | devbench | `backlog/E2-reliability/E2-F1-backlog-integrity/E2-F1-S1-done-gate/E2-F1-S1-T1-judge-approval-guard.md` |
| E2-F1-S1-T2 | Consolidate all status writes through single set_status() in backlog/manager.py | Task | in-queue | E2-F1-S1-T1 | devbench | `backlog/E2-reliability/E2-F1-backlog-integrity/E2-F1-S1-done-gate/E2-F1-S1-T2-single-write-path.md` |
| E2-F1-S1 | Done-gate and single write path | Story | in-queue | E0 | devbench | `backlog/E2-reliability/E2-F1-backlog-integrity/E2-F1-S1-done-gate/E2-F1-S1.md` |
| E2-F1-S2-T1 | Add devbench validate-backlog command + wire as pre-flight in orchestrator.py | Task | in-queue | E2-F1-S1-T2 | devbench | `backlog/E2-reliability/E2-F1-backlog-integrity/E2-F1-S2-validate-backlog/E2-F1-S2-T1-validate-backlog-command.md` |
| E2-F1-S2 | validate-backlog CLI command | Story | in-queue | E2-F1-S1-T2 | devbench | `backlog/E2-reliability/E2-F1-backlog-integrity/E2-F1-S2-validate-backlog/E2-F1-S2.md` |
| E2-F1 | Backlog Integrity Enforcement | Feature | in-queue | E0 | devbench | `backlog/E2-reliability/E2-F1-backlog-integrity/E2-F1.md` |
| E2-F2-S1-T1 | Audit execution/orchestrator.py, fix any missing feedback pass-through, add regression test | Task | in-queue | E2-F1-S2-T1 | devbench | `backlog/E2-reliability/E2-F2-feedback-injection/E2-F2-S1-feedback-audit/E2-F2-S1-T1-feedback-audit.md` |
| E2-F2-S1 | Verify all retry paths pass feedback | Story | in-queue | E2-F1-S2-T1 | devbench | `backlog/E2-reliability/E2-F2-feedback-injection/E2-F2-S1-feedback-audit/E2-F2-S1.md` |
| E2-F2 | Prior Feedback Injection Audit | Feature | in-queue | E2-F1 | devbench | `backlog/E2-reliability/E2-F2-feedback-injection/E2-F2.md` |
| E2 | Reliability | Epic | in-queue | E0 | devbench | `backlog/E2-reliability/E2.md` |

### E3: Prompt Overrides

| ID | Title | Type | Status | Dependencies | Repo | File Path |
|----|-------|------|--------|--------------|------|-----------|
| E3-F1-S1-T1 | Add JUDGE_PROMPTS_DIR to config.py, update load_prompt() to check it first, document in shell.env.example | Task | in-queue | E0 | devbench | `backlog/E3-prompt-overrides/E3-F1-configurable-prompts/E3-F1-S1-prompts-dir/E3-F1-S1-T1-judge-prompts-dir.md` |
| E3-F1-S1 | JUDGE_PROMPTS_DIR support | Story | in-queue | E0 | devbench | `backlog/E3-prompt-overrides/E3-F1-configurable-prompts/E3-F1-S1-prompts-dir/E3-F1-S1.md` |
| E3-F1 | Configurable Prompts Directory | Feature | in-queue | E0 | devbench | `backlog/E3-prompt-overrides/E3-F1-configurable-prompts/E3-F1.md` |
| E3 | Prompt Overrides | Epic | in-queue | E0 | devbench | `backlog/E3-prompt-overrides/E3.md` |

### E4: Backlog Manager

| ID | Title | Type | Status | Dependencies | Repo | File Path |
|----|-------|------|--------|--------------|------|-----------|
| E4-F1-S1-T1 | Rename `BacklogManagerJudge` to `BacklogManager` and update all references | Task | done | E2 | devbench | `backlog/E4-backlog-manager/E4-F1-backlog-manager-rename/E4-F1-S1-backlog-manager-class/E4-F1-S1-T1-rename-backlog-manager.md` |
| E4-F1-S1 | Rename backlog manager class and update runtime references | Story | in-queue | E2 | devbench | `backlog/E4-backlog-manager/E4-F1-backlog-manager-rename/E4-F1-S1-backlog-manager-class/E4-F1-S1.md` |
| E4-F1 | Backlog Manager Class Rename and Decoupling | Feature | in-queue | E2 | devbench | `backlog/E4-backlog-manager/E4-F1-backlog-manager-rename/E4-F1.md` |
| E4 | Backlog Manager | Epic | in-queue | E2 | devbench | `backlog/E4-backlog-manager/E4.md` |

### E5: Backlog-Native Configuration

| ID | Title | Type | Status | Dependencies | Repo | File Path |
|----|-------|------|--------|--------------|------|-----------|
| E5-F1-S1-T1 | Load backlog YAML config with deterministic precedence and repo branch fallback | Task | in-queue | E0 | devbench | `backlog/E5-config-from-backlog/E5-F1-yaml-config-loader/E5-F1-S1-config-path-and-precedence/E5-F1-S1-T1-backlog-yaml-config-loader.md` |
| E5-F1-S1 | Backlog YAML config path and precedence | Story | in-queue | E0 | devbench | `backlog/E5-config-from-backlog/E5-F1-yaml-config-loader/E5-F1-S1-config-path-and-precedence/E5-F1-S1.md` |
| E5-F1 | YAML Config Loader and Precedence | Feature | in-queue | E0 | devbench | `backlog/E5-config-from-backlog/E5-F1-yaml-config-loader/E5-F1.md` |
| E5 | Backlog-Native Configuration | Epic | in-queue | E0 | devbench | `backlog/E5-config-from-backlog/E5.md` |
