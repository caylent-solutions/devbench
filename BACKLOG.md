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
E6 starts after E0 is done (independent of E1–E5).
E7 starts after E5 is done.
E2-F2 depends on E2-F1 completing first (within E2).
-->

<!-- Parallel Opportunities
After E0 is done:
- E1, E2, E3, E5, and E6 can all run in parallel (no inter-epic dependencies among them)
- E4 starts after E2 completes
- Within E2: E2-F1 and E2-F2 are partially ordered (E2-F2 depends on E2-F1-S2)
- Within E6: T1 and T2 are independent and can run in parallel
-->

## Status Summary

| Epic | Title | Features | Stories | Tasks | Total | Done | In Progress | In Review | In Queue |
|------|-------|----------|---------|-------|-------|------|-------------|-----------|----------|
| E0 | Fix Critical Issues | 1 | 2 | 4 | 7 | 7 | 0 | 0 | 0 |
| E1 | Devcontainer Integration | 1 | 1 | 2 | 4 | 0 | 0 | 0 | 4 |
| E2 | Reliability | 2 | 3 | 4 | 9 | 9 | 0 | 0 | 0 |
| E3 | Prompt Overrides | 1 | 1 | 1 | 3 | 0 | 0 | 0 | 3 |
| E4 | Backlog Manager | 1 | 1 | 1 | 4 | 4 | 0 | 0 | 0 |
| E5 | Backlog-Native Configuration | 1 | 1 | 2 | 5 | 2 | 3 | 0 | 0 |
| E6 | Orchestrator Runtime Fixes | 1 | 1 | 2 | 5 | 0 | 0 | 0 | 5 |
| E7 | Deprecation Removal | 1 | 1 | 1 | 4 | 0 | 0 | 0 | 4 |
| **Total** | | **9** | **11** | **17** | **41** | **22** | **3** | **0** | **16** |

---

## Full Work Unit Index

### E0: Fix Critical Issues

| ID | Title | Type | Status | Dependencies | Repo | File Path |
|----|-------|------|--------|--------------|------|-----------|
| E0-F1-S1-T1 | Move ALLOWED_REPOS to JUDGE_ALLOWED_REPOS env var | Task | done | None | devbench | `backlog/E0-fix-critical-issues/E0-F1-config-hardening/E0-F1-S1-remove-hardcoded-config/E0-F1-S1-T1-allowed-repos-env-var.md` |
| E0-F1-S1-T2 | Remove hardcoded WORKSPACE_ROOT default (fail-fast) | Task | done | E0-F1-S1-T1 | devbench | `backlog/E0-fix-critical-issues/E0-F1-config-hardening/E0-F1-S1-remove-hardcoded-config/E0-F1-S1-T2-workspace-root-env-var.md` |
| E0-F1-S1-T3 | Add JUDGE_MERGE_STRATEGY configurable env var | Task | done | E0-F1-S1-T2 | devbench | `backlog/E0-fix-critical-issues/E0-F1-config-hardening/E0-F1-S1-remove-hardcoded-config/E0-F1-S1-T3-merge-strategy-env-var.md` |
| E0-F1-S1 | Remove Hardcoded Config Values | Story | done | None | devbench | `backlog/E0-fix-critical-issues/E0-F1-config-hardening/E0-F1-S1-remove-hardcoded-config/E0-F1-S1.md` |
| E0-F1-S2-T1 | Remove hardcoded JUDGE_GH_ORG, add required var guards to both scripts | Task | done | E0-F1-S1-T3 | devbench | `backlog/E0-fix-critical-issues/E0-F1-config-hardening/E0-F1-S2-harden-start-scripts/E0-F1-S2-T1-required-var-guards.md` |
| E0-F1-S2 | Harden Start Scripts | Story | done | E0-F1-S1-T3 | devbench | `backlog/E0-fix-critical-issues/E0-F1-config-hardening/E0-F1-S2-harden-start-scripts/E0-F1-S2.md` |
| E0-F1 | Config Hardening | Feature | done | None | devbench | `backlog/E0-fix-critical-issues/E0-F1-config-hardening/E0-F1.md` |
| E0 | Fix Critical Issues | Epic | done | None | devbench | `backlog/E0-fix-critical-issues/E0.md` |

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
| E2-F1-S1-T1 | Add require_judge_approval guard in backlog/manager.py | Task | done | E0 | devbench | `backlog/E2-reliability/E2-F1-backlog-integrity/E2-F1-S1-done-gate/E2-F1-S1-T1-judge-approval-guard.md` |
| E2-F1-S1-T2 | Consolidate all status writes through single set_status() in backlog/manager.py | Task | done | E2-F1-S1-T1 | devbench | `backlog/E2-reliability/E2-F1-backlog-integrity/E2-F1-S1-done-gate/E2-F1-S1-T2-single-write-path.md` |
| E2-F1-S1 | Done-gate and single write path | Story | done | E0 | devbench | `backlog/E2-reliability/E2-F1-backlog-integrity/E2-F1-S1-done-gate/E2-F1-S1.md` |
| E2-F1-S2-T1 | Add devbench validate-backlog command + wire as pre-flight in orchestrator.py | Task | done | E2-F1-S1-T2 | devbench | `backlog/E2-reliability/E2-F1-backlog-integrity/E2-F1-S2-validate-backlog/E2-F1-S2-T1-validate-backlog-command.md` |
| E2-F1-S2 | validate-backlog CLI command | Story | done | E2-F1-S1-T2 | devbench | `backlog/E2-reliability/E2-F1-backlog-integrity/E2-F1-S2-validate-backlog/E2-F1-S2.md` |
| E2-F1 | Backlog Integrity Enforcement | Feature | done | E0 | devbench | `backlog/E2-reliability/E2-F1-backlog-integrity/E2-F1.md` |
| E2-F2-S1-T1 | Audit execution/orchestrator.py, fix any missing feedback pass-through, add regression test | Task | done | E2-F1-S2-T1 | devbench | `backlog/E2-reliability/E2-F2-feedback-injection/E2-F2-S1-feedback-audit/E2-F2-S1-T1-feedback-audit.md` |
| E2-F2-S1 | Verify all retry paths pass feedback | Story | done | E2-F1-S2-T1 | devbench | `backlog/E2-reliability/E2-F2-feedback-injection/E2-F2-S1-feedback-audit/E2-F2-S1.md` |
| E2-F2 | Prior Feedback Injection Audit | Feature | done | E2-F1 | devbench | `backlog/E2-reliability/E2-F2-feedback-injection/E2-F2.md` |
| E2 | Reliability | Epic | done | E0 | devbench | `backlog/E2-reliability/E2.md` |

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
| E4-F1-S1 | Rename backlog manager class and update runtime references | Story | done | E2 | devbench | `backlog/E4-backlog-manager/E4-F1-backlog-manager-rename/E4-F1-S1-backlog-manager-class/E4-F1-S1.md` |
| E4-F1 | Backlog Manager Class Rename and Decoupling | Feature | done | E2 | devbench | `backlog/E4-backlog-manager/E4-F1-backlog-manager-rename/E4-F1.md` |
| E4 | Backlog Manager | Epic | done | E2 | devbench | `backlog/E4-backlog-manager/E4.md` |

### E5: Backlog-Native Configuration

| ID | Title | Type | Status | Dependencies | Repo | File Path |
|----|-------|------|--------|--------------|------|-----------|
| E5-F1-S1-T1 | Load backlog YAML config with deterministic precedence and repo branch fallback | Task | done | E0 | devbench | `backlog/E5-config-from-backlog/E5-F1-yaml-config-loader/E5-F1-S1-config-path-and-precedence/E5-F1-S1-T1-backlog-yaml-config-loader.md` |
| E5-F1-S1-T2 | Add checkout_directory mapping and simplify bootstrap path contract | Task | done | E5-F1-S1-T1 | devbench | `backlog/E5-config-from-backlog/E5-F1-yaml-config-loader/E5-F1-S1-config-path-and-precedence/E5-F1-S1-T2-checkout-directory-and-bootstrap.md` |
| E5-F1-S1 | Backlog YAML config path and precedence | Story | in-progress | E0 | devbench | `backlog/E5-config-from-backlog/E5-F1-yaml-config-loader/E5-F1-S1-config-path-and-precedence/E5-F1-S1.md` |
| E5-F1 | YAML Config Loader and Precedence | Feature | in-progress | E0 | devbench | `backlog/E5-config-from-backlog/E5-F1-yaml-config-loader/E5-F1.md` |
| E5 | Backlog-Native Configuration | Epic | in-progress | E0 | devbench | `backlog/E5-config-from-backlog/E5.md` |

### E6: Orchestrator Runtime Fixes

| ID | Title | Type | Status | Dependencies | Repo | File Path |
|----|-------|------|--------|--------------|------|-----------|
| E6-F1-S1-T1 | Pass repo= kwarg to SecurityReviewJudge.evaluate() | Task | in-queue | None | devbench | `backlog/E6-orchestrator-runtime-fixes/E6-F1-security-and-git-fixes/E6-F1-S1-fix-orchestrator-runtime-bugs/E6-F1-S1-T1-security-judge-repo-kwarg.md` |
| E6-F1-S1-T2 | Create local branch before commit in commit_and_push() | Task | in-queue | None | devbench | `backlog/E6-orchestrator-runtime-fixes/E6-F1-security-and-git-fixes/E6-F1-S1-fix-orchestrator-runtime-bugs/E6-F1-S1-T2-branch-checkout-before-push.md` |
| E6-F1-S1 | Fix Orchestrator Runtime Bugs | Story | in-queue | E0 | devbench | `backlog/E6-orchestrator-runtime-fixes/E6-F1-security-and-git-fixes/E6-F1-S1-fix-orchestrator-runtime-bugs/E6-F1-S1.md` |
| E6-F1 | Security and Git Flow Bug Fixes | Feature | in-queue | E0 | devbench | `backlog/E6-orchestrator-runtime-fixes/E6-F1-security-and-git-fixes/E6-F1.md` |
| E6 | Orchestrator Runtime Fixes | Epic | in-queue | E0 | devbench | `backlog/E6-orchestrator-runtime-fixes/E6.md` |

### E7: Deprecation Removal

| ID | Title | Type | Status | Dependencies | Repo | File Path |
|----|-------|------|--------|--------------|------|-----------|
| E7-F1-S1-T1 | Remove JUDGE_ALLOWED_REPOS, JUDGE_BACKLOG_ROOT, JUDGE_BACKLOG_INDEX compat shims | Task | in-queue | E5 | devbench | `backlog/E7-deprecation-removal/E7-F1-remove-deprecated-env-vars/E7-F1-S1-remove-compat-shims/E7-F1-S1-T1-remove-deprecated-env-vars.md` |
| E7-F1-S1 | Remove backward-compat code and docs for deprecated env vars | Story | in-queue | E5 | devbench | `backlog/E7-deprecation-removal/E7-F1-remove-deprecated-env-vars/E7-F1-S1-remove-compat-shims/E7-F1-S1.md` |
| E7-F1 | Remove Deprecated Env Var Compat Shims | Feature | in-queue | E5 | devbench | `backlog/E7-deprecation-removal/E7-F1-remove-deprecated-env-vars/E7-F1.md` |
| E7 | Deprecation Removal | Epic | in-queue | E5 | devbench | `backlog/E7-deprecation-removal/E7.md` |
