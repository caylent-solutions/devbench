# ADR-12: mode-aware `devbench get-diff`

**Status:** Accepted
**Date:** 2026-04-21

---

## Context

`devbench get-diff <unit-id>` is the CLI entry point every review judge uses to read "what this work unit changed". All five review judges (`code-reviewer`, `test-reviewer`, `doc-reviewer`, `changes-manifest`, and `security-reviewer`) invoke it at the top of their prompt via `!`uv run devbench get-diff $ARGUMENTS`` and feed the output to the LLM. The output therefore defines the scope the judges evaluate against.

Until this ADR, `cmd_get_diff` concatenated four hunks unconditionally:

1. `git diff --cached` (staged).
2. `git diff` (unstaged).
3. `git diff origin/<default_branch>` (branch vs default).
4. Untracked files as synthetic diff hunks.

Hunk 3 is correct in the devbench-default per-task-branch workflow, where each work unit runs on its own branch cut from `main`. In that workflow `git diff origin/main` IS the task's diff.

It is wrong in the opt-in `git_ops.single_branch: <branch>` + `git_ops.defer_pr: true` mode, which ships tasks by committing to a shared branch without pushing until the operator invokes `git-ops-finalize`. After N completed tasks on the shared branch, hunk 3 covers N tasks' worth of work. A judge reading the output sees "this work unit staged 362 files outside its manifest" when the current task actually staged one.

Two incidents on 2026-04-20 against the kanon `feat/embed-repo-tool` branch traced back to this directly:

- **E1-F1-S16-T3** (two README files, staged correctly). Tier-1 judges passed on the staged diff, but `security-reviewer` flagged HIGH-severity findings against `.github/workflows/pr-validation.yml`, `main-validation.yml`, and ASDF installer scripts: files T3 never touched. The orchestrator verified via `git diff --staged --name-only` that only the two READMEs were staged and noted the security reviewer had read the branch-vs-main accumulation.
- **E1-F2-S12-T6** (one ruff-format fix, one staged file). All four Tier-1 judges failed with "362 files staged outside the Changes Manifest". The orchestrator re-verified the staged set was exactly the one file named by the manifest and noted the same root cause.

The LLM judges are not the defect. They are reading exactly what `get-diff` gave them. The CLI is misrepresenting scope in `defer_pr` mode.

## Decision

Make `cmd_get_diff` mode-aware, gated on the existing `git_ops.defer_pr` config surface (no new yaml keys, no new env vars).

**Per-task-branch mode** (`defer_pr: false`, default): byte-identical to today. Emit staged + unstaged + branch-vs-default + untracked.

**defer_pr mode** (`defer_pr: true`):

1. Emit staged and unstaged.
2. If BOTH are empty the executor has just committed; perform a task-attributed commit lookup via `git log --grep "^<unit-id>:"` and emit those commit diffs so the post-commit security review still sees this task's actual changes. If no task-attributed commit is found, exit `GET_DIFF_NO_ATTRIBUTABLE` (45) with a verbatim diagnostic on stderr.
3. Emit untracked synthetic hunks unchanged.
4. DO NOT emit `git diff origin/<default>`. That hunk is the source of the misread and has no correct interpretation in this mode.

The `get_configured_default_branch` lookup is skipped entirely in defer_pr mode, since no branch-vs-default diff is produced.

All five review judge prompts additionally carry a defensive "Scope contract" line that states `devbench get-diff` is the authoritative scope source and forbids raw `git diff origin/main` / `git diff main...HEAD` as a workaround. This is belt-and-suspenders: the CLI fix alone resolves the bug, but the prompt guard prevents future drift.

## Consequences

- **Kanon (and every future `defer_pr: true` backlog) sees scope-correct review output.** The T3 / T6 failure mode does not recur.
- **No behaviour change in the default posture.** Per-task-branch workflows keep the four-hunk output byte-identically.
- **Post-commit reviews work.** When the executor has committed and the working tree is clean, `get-diff` performs a task-attributed commit lookup via `git log --grep "^<unit-id>:"` so security-review (which typically runs after commit) still sees the task's actual changes. If no task-attributed commit is found, `get-diff` exits `GET_DIFF_NO_ATTRIBUTABLE` (45) with a verbatim diagnostic on stderr so operators can diagnose the missing commit attribution.
- **Regression surface shrinks.** Three plugin-structure regression pins (`TestReviewJudgesUseGetDiffForScope`) now fail any PR that drops the "use get-diff for scope" contract or reintroduces the `git diff origin/main` anti-pattern in a judge prompt.
- **No new configuration to maintain.** The fix reuses `devbench.config.DEFER_PR`, already populated by `config_loader` from `git_ops.defer_pr`. Operators do not learn a new flag.

> **Related (concurrent-claim variant).** This ADR fixes scope misreads caused by *branch accumulation* in `defer_pr` mode. A second source of the same "files staged outside the manifest" misread -- two work units `in-progress` on the SAME shared checkout leaking each other's uncommitted files into `get-diff` -- is addressed by serializing claims in [ADR-33](33-serialize-claims-and-scoped-convergence.md) (`orchestrate.max_parallel_in_progress`, default 1).

## Alternatives considered and rejected

**Rewrite every judge prompt to compute its own staged-diff via raw git.** Rejected: duplicates git logic across five agents, each of which would re-learn the staged/unstaged/post-commit discriminator separately. Brittle to model drift and to future changes in the git-ops flow.

**Cut a new branch every N completed tasks to reset the diff baseline.** Rejected: pushes operator toil to paper over a CLI bug. Leaves the underlying defect intact; every subsequent accumulation re-surfaces the same misread.

**Switch kanon (and future defer_pr consumers) out of `defer_pr` mode.** Rejected: `defer_pr` is a legitimate workflow choice (one PR per batch rather than one per task) and the tool must support it correctly. Forcing every consumer onto per-task branches would invalidate the reason `defer_pr` exists.

**Make get-diff return only staged + unstaged in all modes (drop branch-vs-default universally).** Rejected: changes behaviour in the default per-task-branch mode and would break consumers who rely on the current four-hunk output. The mode switch keeps the default path byte-identical.

**Add a new `git_ops.review_scope: commit_local | branch` yaml key.** Rejected: a new key for a property that is already fully determined by `defer_pr`. The mode discriminator is not an independent degree of freedom.

## Related files

### Python
- `src/devbench/cli.py::cmd_get_diff` -- mode-aware branch on `DEFER_PR`, task-attributed commit lookup via `git log --grep "^<unit-id>:"` when working tree is empty in `defer_pr` mode (exits `GET_DIFF_NO_ATTRIBUTABLE` (45) when no attributable commit is found), `git diff origin/<default>` skipped entirely in `defer_pr` mode.

### Plugin prompts
- `plugin/devbench/agents/review_team/code-reviewer.md` -- scope-contract line added after get-diff invocation.
- `plugin/devbench/agents/review_team/test-reviewer.md` -- same.
- `plugin/devbench/agents/review_team/doc-reviewer.md` -- same.
- `plugin/devbench/agents/review_team/changes-manifest.md` -- same.
- `plugin/devbench/agents/security-reviewer.md` -- same.

### Tests
- `tests/test_cli.py::TestCmdGetDiffModeAware` -- 7 unit cases (non-defer_pr back-compat pin, defer_pr excludes branch-vs-main, pre-commit returns staged+unstaged, post-commit performs task-attributed commit lookup, accumulated prior commits do not leak, untracked still rendered, all-empty returns "(no changes)").
- `tests/test_plugin/test_agent_structure.py::TestReviewJudgesUseGetDiffForScope` -- 3 regression pins parametrized across all 5 judges.
- `tests/test_integration/test_get_diff_defer_pr_mode.py` -- 2 end-to-end cases (real git repo with 3 prior commits on a shared branch; defer_pr returns task-local scope only, non-defer_pr keeps branch-vs-default hunk).

### Docs
- `docs/adr/12-mode-aware-get-diff.md` (this file).
- `docs/cli-reference.md` -- `get-diff` entry updated with the mode-aware contract.
- `docs/faq.md` -- new Q&A on "judges report wrong staged file count".
- `docs/architecture.md` -- one bullet noting `get-diff` is the authoritative scope source and is mode-aware per ADR-12.
- `docs/execution-modes.md` -- SINGLE-BRANCH MODE section notes the get-diff contract.
