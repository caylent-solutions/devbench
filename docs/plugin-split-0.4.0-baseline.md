# Plugin-split 0.4.0 baseline (issue #224)

Captured BEFORE any source-tree rename. Reviewer / regression-test anchor.

## SHA256 of every file under plugin/devbench/

```
7e8b9e00b053b7c0b64cb13ef46fa7e2dcec8406d65b24b6d76c4c1dd1db3a02  plugin/devbench/agents/blocker-resolver.md
533e8ef14f8b2731e95d3e8e69a3b06eee21d5c6f2eb33ecd1a0a75507eeabd2  plugin/devbench/agents/executor.md
def8481144e9929d030565728653933194554b1f0ef778a1067b7d93945101af  plugin/devbench/agents/manifest-amender.md
cdbffaac45443a1b8a633d308764f376b43a963b6167435dbb95f56a6ff78ce3  plugin/devbench/agents/review-supervisor.md
0a6fed22a6f8b5cd619a2c880fe3b2b3382bd244878bd2df5e83a738e544e069  plugin/devbench/agents/review_team/changes-manifest.md
8fab4a480dc6d5b3d828d4ba2e9756273169442f04ab5d0be7f5282efa1133e9  plugin/devbench/agents/review_team/code-reviewer.md
727970a99d51a1c19b56a488d75245aff73cf344ec22e7b3f82d7982b4bb801b  plugin/devbench/agents/review_team/doc-reviewer.md
ed0aa34aa97087b7dcb25ea8d6bebe0d07c219ab7629664bc86fb386b89b7322  plugin/devbench/agents/review_team/test-reviewer.md
00292939d5a204eb5af6834e2a3b7dd6e564d65317d8bec8b9ea281732662cd5  plugin/devbench/agents/security-reviewer.md
37d9ea87a7b0585a031c19de3b8c482b4fe0d9d9cb0184392f446c056e52d1f0  plugin/devbench/agents/task-factory.md
699f72c266dffa065e92be74e943beb99b0d2270c0e036cda4c63fcddb4625b3  plugin/devbench/.claude-plugin/plugin.json
bbd3af20583e1942565ec8acf8ef413ca7db49f0519975b6a179ebcf7033da76  plugin/devbench/hooks/hooks.json
a88e919bf68b700a438285254cd4e7b613bcc6f5440b17153858aa402d222946  plugin/devbench/scripts/assert-tests-pass.sh
f157b4b544e8f480d2e170925ab6bc7e3bb8950cf1b586c3f80f51e4bdbe7e62  plugin/devbench/scripts/continue-orchestration.sh
0a7f6dbe2e5fedd7c09a4c426bc3f5887753548074841ed10db30dd4d430df90  plugin/devbench/scripts/guard-backlog.sh
556135b393db01c5530c300ae78e07eef4efb0fea605ed6444626dda52b7b39a  plugin/devbench/scripts/guard-bash.sh
55fa187366d9f08a24f9b37981782e3f95b42dee820a5172263499f49e347f29  plugin/devbench/scripts/guard-comment-format.sh
c13e2808706d6388d03c9561f852adc3fe82c06c4156bdd17f7722af8027c973  plugin/devbench/scripts/guard-destructive-git.sh
00530d7dc3ef6e101b529c3998957895ed893ac9e5bf21bd45e1edf506c5431a  plugin/devbench/scripts/guard-git-stage.sh
e0eb9b1468f3a465f111329ffbfddb631fe8704878e9b2a2d49d0452f03ff7ce  plugin/devbench/scripts/guard-quota-aware.sh
82ae6f455487bbe566603ae7b809c97e210c36341a330fa42fa27c7d50ee3507  plugin/devbench/scripts/guard-review-supervisor-scope.sh
09c32e84b00e34922d46e7f179ec59a64cf7f18421733453ed6d1004741e47c9  plugin/devbench/scripts/guard-verdict-format.sh
4163f9d36f202a96a92113fde34ef02155f7a015a005c54cb784faa1c22d5ca2  plugin/devbench/scripts/guard-work-unit-write.sh
8d5472b70581cc808e2a6147b8ca5e1554d16c14ded808cb0bcf899f15bbaf01  plugin/devbench/scripts/_hook_lib.sh
7bbef195547e5a0e405934077b234cba7bb6e017f18074a27ff94088e342f429  plugin/devbench/scripts/hook-logger.sh
34d4a9022b8c98b8ece6231899da1a8fc051ea3ea2374d228287cf2480ba2822  plugin/devbench/skills/bootstrap-environment/SKILL.md
deb20dc8e1373f783d421f2a6646f93428a6c03c8da08a92afe1ce0601c09bc7  plugin/devbench/skills/configure-devbench/SKILL.md
ec8b536cfd0c83cb6a215f73df289487af0e805ef8190d00fa389d1974582452  plugin/devbench/skills/create-spec/SKILL.md
9d54ead82b26bd17f0cad516a766bd8641299610a24ed2117b26b71b1dd880e9  plugin/devbench/skills/orchestrate/SKILL.md
d5a3968317622d20ef6a6ddfd44fe42a5aa1c49d95ef1be7969bffd5338a7492  plugin/devbench/skills/spec-to-backlog/SKILL.md
```

## Verbatim hooks/hooks.json (PreToolUse hook list source of truth)

```json
{
  "hooks": {
    "PreToolUse": [
      {
        "matcher": "Bash",
        "hooks": [
          {
            "type": "command",
            "command": "bash ${CLAUDE_PLUGIN_ROOT}/scripts/hook-logger.sh"
          },
          {
            "type": "command",
            "command": "bash ${CLAUDE_PLUGIN_ROOT}/scripts/guard-bash.sh"
          },
          {
            "type": "command",
            "command": "bash ${CLAUDE_PLUGIN_ROOT}/scripts/guard-verdict-format.sh"
          },
          {
            "type": "command",
            "command": "bash ${CLAUDE_PLUGIN_ROOT}/scripts/guard-comment-format.sh"
          },
          {
            "type": "command",
            "command": "bash ${CLAUDE_PLUGIN_ROOT}/scripts/guard-git-stage.sh"
          },
          {
            "type": "command",
            "command": "bash ${CLAUDE_PLUGIN_ROOT}/scripts/guard-destructive-git.sh"
          },
          {
            "type": "command",
            "command": "bash ${CLAUDE_PLUGIN_ROOT}/scripts/guard-review-supervisor-scope.sh"
          },
          {
            "type": "command",
            "command": "bash ${CLAUDE_PLUGIN_ROOT}/scripts/guard-quota-aware.sh"
          }
        ]
      },
      {
        "matcher": "Write",
        "hooks": [
          {
            "type": "command",
            "command": "bash ${CLAUDE_PLUGIN_ROOT}/scripts/guard-work-unit-write.sh"
          }
        ]
      },
      {
        "matcher": "Edit",
        "hooks": [
          {
            "type": "command",
            "command": "bash ${CLAUDE_PLUGIN_ROOT}/scripts/guard-work-unit-write.sh"
          }
        ]
      },
      {
        "matcher": ".*",
        "hooks": [
          {
            "type": "command",
            "command": "bash ${CLAUDE_PLUGIN_ROOT}/scripts/hook-logger.sh"
          }
        ]
      }
    ],
    "PostToolUse": [
      {
        "matcher": "Bash",
        "hooks": [
          {
            "type": "command",
            "command": "bash ${CLAUDE_PLUGIN_ROOT}/scripts/hook-logger.sh"
          },
          {
            "type": "command",
            "command": "bash ${CLAUDE_PLUGIN_ROOT}/scripts/assert-tests-pass.sh"
          }
        ]
      },
      {
        "matcher": ".*",
        "hooks": [
          {
            "type": "command",
            "command": "bash ${CLAUDE_PLUGIN_ROOT}/scripts/hook-logger.sh"
          }
        ]
      }
    ],
    "PostToolUseFailure": [
      {
        "matcher": ".*",
        "hooks": [
          {
            "type": "command",
            "command": "bash ${CLAUDE_PLUGIN_ROOT}/scripts/hook-logger.sh"
          }
        ]
      }
    ],
    "UserPromptSubmit": [
      {
        "hooks": [
          {
            "type": "command",
            "command": "bash ${CLAUDE_PLUGIN_ROOT}/scripts/hook-logger.sh"
          }
        ]
      }
    ],
    "Stop": [
      {
        "hooks": [
          {
            "type": "command",
            "command": "bash ${CLAUDE_PLUGIN_ROOT}/scripts/continue-orchestration.sh"
          }
        ]
      }
    ],
    "SubagentStart": [
      {
        "hooks": [
          {
            "type": "command",
            "command": "bash ${CLAUDE_PLUGIN_ROOT}/scripts/hook-logger.sh"
          }
        ]
      }
    ],
    "SubagentStop": [
      {
        "hooks": [
          {
            "type": "command",
            "command": "bash ${CLAUDE_PLUGIN_ROOT}/scripts/hook-logger.sh"
          }
        ]
      }
    ],
    "PreCompact": [
      {
        "hooks": [
          {
            "type": "command",
            "command": "bash ${CLAUDE_PLUGIN_ROOT}/scripts/hook-logger.sh"
          }
        ]
      }
    ],
    "PermissionRequest": [
      {
        "hooks": [
          {
            "type": "command",
            "command": "bash ${CLAUDE_PLUGIN_ROOT}/scripts/hook-logger.sh"
          }
        ]
      }
    ],
    "Notification": [
      {
        "hooks": [
          {
            "type": "command",
            "command": "bash ${CLAUDE_PLUGIN_ROOT}/scripts/hook-logger.sh"
          }
        ]
      }
    ]
  }
}
```

## guard-work-unit-write.sh stderr format (#224 AC-11)

```bash

  echo "guard-work-unit-write: blocked write to work unit file: ${FILE_PATH}" >&2
  echo "Fix: work unit .md files under backlog/ are managed exclusively by the orchestrate skill." >&2
  echo "Executors must not modify work unit files directly." >&2
  echo "(Issue #160: set DEVBENCH_AGENT_ROLE=orchestrator in the calling env to bypass for orchestrator-tier corrective edits.)" >&2
```

## Agent .md frontmatter (model + allowed-tools)

### plugin/devbench/agents/blocker-resolver.md
```yaml
---
name: blocker-resolver
description: Analyzes blockers in a work unit and proposes compliant resolutions or escalation paths. Invoke with a work unit ID (e.g. E0-F1-S1-T1).
model: opus
tools: Bash
disallowedTools: Write, Edit, Read, Glob, Grep
---
---

You are a blocker resolution analyst for a project held to the standards of highly regulated financial services.
Evaluate whether blockers listed in the work unit can be resolved or need escalation. All proposed resolutions must comply with project standards.

## BLOCKER CLASSIFICATION
1. Is the blocker correctly categorized (dependency vs. technical vs. external)?
2. Is the blocker description specific enough to take action on?
```

### plugin/devbench/agents/executor.md
```yaml
---
name: executor
description: Executes a work unit from the project backlog following TDD, SOLID, fail-fast, and 12-factor standards. Invoke with a work unit ID (e.g. E0-F1-S1-T1).
model: sonnet
tools: Bash, Read, Write, Edit, Glob, Grep
---
---

You are executing a work unit from the project backlog for a project held to the standards of highly regulated financial services.

The `uv run devbench read-unit` output above contains:
- `repo_path`: your working directory for all code changes -- use this as `cwd` for all repo operations
- `work_unit_path`: path to the work unit specification file
- `content`: full work unit content including acceptance criteria

```

### plugin/devbench/agents/manifest-amender.md
```yaml
---
name: manifest-amender
description: Reviews a pending amendment request for an in-progress work unit and either applies it to the Changes Manifest (after Layer 3 post-check) or rejects it and blocks the task. Invoke with a work unit ID (e.g. E0-F1-S1-T1).
model: opus
tools: Bash
disallowedTools: Write, Edit, Read, Glob, Grep
---
---

You are a Layer 2 semantic reviewer of amendment requests. Your role is narrow: a deterministic pre-filter has already verified the request's structural invariants (valid JSON schema, task is in-progress, allowed reason, linked ACs exist in the work unit, files are in the staged diff, rate limit not exceeded). Do NOT re-check those facts -- they are given.

Your job is to answer the genuinely semantic questions that deterministic code cannot reliably answer:

1. **Approach authorisation.** Read the work unit's `## Description` and specifically its Approach section. Does the text authorise the *kind* of change the request describes? The canonical authorising pattern is TDD GREEN wording like "if the test exposes a bug that needs a production fix, implement the minimum change", but backlog authors phrase this differently. Decide whether the current work unit's Approach contemplates the change in the amendment request.

```

### plugin/devbench/agents/review-supervisor.md
```yaml
---
name: review-supervisor
description: Discovers and invokes all review_team agents in parallel, aggregates verdicts, returns consolidated pass/fail. Invoke with a work unit ID (e.g. E0-F1-S1-T1).
model: sonnet
tools: Bash, Agent(code-reviewer, test-reviewer, doc-reviewer, changes-manifest)
---
---

You are the review supervisor. Your job is to discover all review_team members, invoke them in parallel, collect their verdicts, and return a consolidated result.

## Scope (read-only aggregator -- issue #118)

Your role is **read-only aggregation**. You MUST NOT:

- Mutate the worktree, index, or filesystem state. The `guard-review-supervisor-scope.sh` hook blocks `git commit / push / pull / merge / rebase / checkout / rm / stash / clean / apply / tag`, output redirection (`>` / `>>`), `tee`, `sed -i`, `find -exec/-delete`, and similar Bash mutations.
```

### plugin/devbench/agents/security-reviewer.md
```yaml
---
name: security-reviewer
description: Reviews security posture against SOC 2, PCI DSS, FINRA, SEC, GDPR, CCPA, and SOX standards. Invoke with a work unit ID (e.g. E0-F1-S1-T1).
model: opus
tools: Bash
disallowedTools: Write, Edit, Read, Glob, Grep
---
---

You are a strict security reviewer for a project held to the standards of highly regulated financial services.
This project must comply with SOC 2, PCI DSS, FINRA, SEC regulations, GDPR, CCPA, and SOX.

Evaluate the security posture based on the provided evidence.

## SECRETS AND CREDENTIALS
```

### plugin/devbench/agents/task-factory.md
```yaml
---
name: task-factory
description: Reads a pending blocker-resolver proposal JSON and materialises each proposed task as a draft work-unit .md file with status `proposed` plus a matching row in BACKLOG.md. Invoke with a source work unit ID (e.g. E0-F1-S1-T1).
model: opus
tools: Bash
disallowedTools: Write, Edit, Read, Glob, Grep
---
---

You are the task-factory agent. Your ONLY job is to call `uv run devbench materialise-proposal $ARGUMENTS`, which reads the pending proposal JSON and writes one draft `.md` per proposed task plus matching rows in `BACKLOG.md`. The CLI does every validation and mutation; you are here to run it and surface any non-zero exit code.

Do NOT:
- Re-author the proposal content. It came from the blocker-resolver; your semantic review happened there.
- Write or edit any backlog files directly (you have no Write/Edit tools).
- Skip the materialise call or substitute a different command.
```

### plugin/devbench/agents/review_team/changes-manifest.md
```yaml
---
name: changes-manifest
description: Reviews whether actual file changes match the planned Changes Manifest and comply with project scope and standards. Invoke with a work unit ID (e.g. E0-F1-S1-T1).
model: opus
tools: Bash
disallowedTools: Write, Edit, Read, Glob, Grep
---
---

You are a strict change scope reviewer for a project held to the standards of highly regulated financial services.
Evaluate whether the actual file changes match what was planned in the Changes Manifest and comply with project standards.

## REVIEW LIFECYCLE CONTEXT
You run BEFORE the orchestrator commits. The executor stages files (git add) but does not commit.
- "Staged (ready for review)" = executor correctly prepared these files. This is the primary evidence.
```

### plugin/devbench/agents/review_team/code-reviewer.md
```yaml
---
name: code-reviewer
description: Reviews staged code changes against SOLID, DRY, fail-fast, 12-factor, security, and project standards. Invoke with a work unit ID (e.g. E0-F1-S1-T1).
model: opus
tools: Bash
disallowedTools: Write, Edit, Read, Glob, Grep
---
---

You are a strict code reviewer for a project held to the standards of highly regulated financial services.
Evaluate the code diff against the acceptance criteria and CLAUDE.md standards.

## ACCEPTANCE CRITERIA
1. Each acceptance criterion is meaningfully addressed (not just keyword matches).
2. All consumers of superseded code are updated in the same change.
```

### plugin/devbench/agents/review_team/doc-reviewer.md
```yaml
---
name: doc-reviewer
description: Reviews documentation completeness, accuracy, and synchronization with code changes. Invoke with a work unit ID (e.g. E0-F1-S1-T1).
model: opus
tools: Bash
disallowedTools: Write, Edit, Read, Glob, Grep
---
---

You are a strict documentation reviewer for a project held to the standards of highly regulated financial services.
Evaluate whether documentation is complete, accurate, and synchronized with code changes.

## DOCUMENTATION SYNCHRONIZATION
1. Documentation is updated in the same change as the code changes it describes.
2. No stale references to removed or renamed code, APIs, classes, methods, or configuration.
```

### plugin/devbench/agents/review_team/test-reviewer.md
```yaml
---
name: test-reviewer
description: Reviews test quality against TDD discipline, real-tests-only, and coverage standards. Invoke with a work unit ID (e.g. E0-F1-S1-T1).
model: opus
tools: Bash
disallowedTools: Write, Edit, Read, Glob, Grep
---
---

You are a strict test quality reviewer for a project held to the standards of highly regulated financial services.
Evaluate the test code and TDD adherence against these standards.

## REAL TESTS ONLY (NO STUBS)
1. No stub tests: no assert(true), assertTrue(true), assert(1 == 1), or any assertion that always passes.
2. No empty test bodies or tests with only TODO/FIXME comments.
```


## tests/test_plugin/ collection (pinned test surface)

```
tests/test_plugin/test_agent_structure.py::TestReviewTeamDirectory::test_review_team_dir_exists
tests/test_plugin/test_agent_structure.py::TestReviewTeamDirectory::test_review_team_dir_contains_exactly_four_agents
tests/test_plugin/test_agent_structure.py::TestReviewSupervisorFrontmatter::test_review_supervisor_file_exists
tests/test_plugin/test_agent_structure.py::TestReviewSupervisorFrontmatter::test_review_supervisor_frontmatter_valid
tests/test_plugin/test_agent_structure.py::TestReviewSupervisorStep0SelfCheck::test_supervisor_contains_step_0_self_check
tests/test_plugin/test_agent_structure.py::TestReviewSupervisorStep0SelfCheck::test_supervisor_emits_structured_runtime_degradation_payload
tests/test_plugin/test_agent_structure.py::TestSecurityReviewerNotInReviewTeam::test_security_reviewer_not_in_review_team
tests/test_plugin/test_agent_structure.py::TestSecurityReviewerNotInReviewTeam::test_security_reviewer_at_agents_root
tests/test_plugin/test_agent_structure.py::TestNoStaleFlatAgentPaths::test_no_stale_flat_agent_paths_in_plugin[code-reviewer.md]
tests/test_plugin/test_agent_structure.py::TestNoStaleFlatAgentPaths::test_no_stale_flat_agent_paths_in_plugin[test-reviewer.md]
tests/test_plugin/test_agent_structure.py::TestNoStaleFlatAgentPaths::test_no_stale_flat_agent_paths_in_plugin[doc-reviewer.md]
tests/test_plugin/test_agent_structure.py::TestNoStaleFlatAgentPaths::test_no_stale_flat_agent_paths_in_plugin[changes-manifest.md]
tests/test_plugin/test_agent_structure.py::TestReviewTeamModelDefault::test_review_team_agent_uses_opus_model[code-reviewer.md]
tests/test_plugin/test_agent_structure.py::TestReviewTeamModelDefault::test_review_team_agent_uses_opus_model[test-reviewer.md]
tests/test_plugin/test_agent_structure.py::TestReviewTeamModelDefault::test_review_team_agent_uses_opus_model[doc-reviewer.md]
tests/test_plugin/test_agent_structure.py::TestReviewTeamModelDefault::test_review_team_agent_uses_opus_model[changes-manifest.md]
tests/test_plugin/test_agent_structure.py::TestReviewSupervisorVerdictFormat::test_supervisor_no_review_fail_token
tests/test_plugin/test_agent_structure.py::TestReviewSupervisorVerdictFormat::test_supervisor_no_review_pass_token
tests/test_plugin/test_agent_structure.py::TestReviewSupervisorVerdictFormat::test_supervisor_fail_branch_uses_lowercase_fail
tests/test_plugin/test_agent_structure.py::TestReviewSupervisorVerdictFormat::test_supervisor_pass_branch_uses_lowercase_pass
tests/test_plugin/test_agent_structure.py::TestReviewerLogCommentBeforeLogVerdict::test_reviewer_instructs_log_comment_before_log_verdict[code-reviewer.md]
tests/test_plugin/test_agent_structure.py::TestReviewerLogCommentBeforeLogVerdict::test_reviewer_instructs_log_comment_before_log_verdict[test-reviewer.md]
tests/test_plugin/test_agent_structure.py::TestReviewerLogCommentBeforeLogVerdict::test_reviewer_instructs_log_comment_before_log_verdict[doc-reviewer.md]
tests/test_plugin/test_agent_structure.py::TestReviewerLogCommentBeforeLogVerdict::test_reviewer_instructs_log_comment_before_log_verdict[changes-manifest.md]
tests/test_plugin/test_agent_structure.py::TestReviewerJsonEnvelope::test_reviewer_requires_json_envelope[code-reviewer.md]
tests/test_plugin/test_agent_structure.py::TestReviewerJsonEnvelope::test_reviewer_requires_json_envelope[test-reviewer.md]
tests/test_plugin/test_agent_structure.py::TestReviewerJsonEnvelope::test_reviewer_requires_json_envelope[doc-reviewer.md]
tests/test_plugin/test_agent_structure.py::TestReviewerJsonEnvelope::test_reviewer_requires_json_envelope[changes-manifest.md]
tests/test_plugin/test_agent_structure.py::TestReviewerJsonEnvelope::test_reviewer_json_envelope_is_last_output[code-reviewer.md]
tests/test_plugin/test_agent_structure.py::TestReviewerJsonEnvelope::test_reviewer_json_envelope_is_last_output[test-reviewer.md]
tests/test_plugin/test_agent_structure.py::TestReviewerJsonEnvelope::test_reviewer_json_envelope_is_last_output[doc-reviewer.md]
tests/test_plugin/test_agent_structure.py::TestReviewerJsonEnvelope::test_reviewer_json_envelope_is_last_output[changes-manifest.md]
tests/test_plugin/test_agent_structure.py::TestReviewSupervisorUsesJsonEnvelope::test_supervisor_no_hardcoded_passed_strings
tests/test_plugin/test_agent_structure.py::TestReviewSupervisorUsesJsonEnvelope::test_supervisor_references_json_envelope
tests/test_plugin/test_agent_structure.py::TestReviewSupervisorUsesJsonEnvelope::test_supervisor_fail_branch_logs_findings_as_comments
tests/test_plugin/test_agent_structure.py::TestExecutorValidationGateEscalation::test_executor_has_bug_escalation_heading
tests/test_plugin/test_agent_structure.py::TestExecutorValidationGateEscalation::test_executor_bug_escalation_names_write_proposal
tests/test_plugin/test_agent_structure.py::TestExecutorValidationGateEscalation::test_executor_bug_escalation_verifies_proposal_file
tests/test_plugin/test_agent_structure.py::TestSkillValidationGateEscalationBranch::test_skill_file_exists
tests/test_plugin/test_agent_structure.py::TestSkillValidationGateEscalationBranch::test_skill_has_validation_gate_branch
tests/test_plugin/test_agent_structure.py::TestSkillValidationGateEscalationBranch::test_skill_step_4a_branches_on_proposal_file
tests/test_plugin/test_agent_structure.py::TestSkillValidationGateEscalationBranch::test_skill_step_4a_short_circuits_on_amendment_file
tests/test_plugin/test_agent_structure.py::TestSkillSubagentTextIsDiagnostic::test_skill_declares_subagent_text_is_diagnostic
tests/test_plugin/test_agent_structure.py::TestSkillSubagentTextIsDiagnostic::test_skill_lists_control_language_patterns
tests/test_plugin/test_agent_structure.py::TestSkillSubagentTextIsDiagnostic::test_skill_names_guard_comment_format_backstop
tests/test_plugin/test_agent_structure.py::TestSkillSubagentTextIsDiagnostic::test_skill_states_only_halt_triggers
tests/test_plugin/test_agent_structure.py::TestExecutorCommentLanguageDiscipline::test_executor_has_comment_language_discipline_heading
tests/test_plugin/test_agent_structure.py::TestExecutorCommentLanguageDiscipline::test_executor_enumerates_forbidden_phrases
tests/test_plugin/test_agent_structure.py::TestExecutorCommentLanguageDiscipline::test_executor_points_at_guard_comment_format
tests/test_plugin/test_agent_structure.py::TestExecutorCommentLanguageDiscipline::test_executor_gives_good_and_bad_example
```
