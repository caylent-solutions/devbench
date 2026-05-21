# Driving operational work with devbench

DevBench's executor + review-supervisor + git-ops loop is not just for
authoring application code. It can drive **operational work** --
sequences of well-defined, individually verifiable actions where the
"output" of each task is an evidence artefact rather than a code
change. Examples:

- AWS resource teardowns (one task per resource: destroy + verify +
  log).
- Periodic audits (one task per check: query + assert + log).
- Bulk administrative operations (one task per record: mutate +
  verify + log).
- Scheduled remediation runs (one task per finding: fix + verify +
  log).

The pattern: each task issues an external command, captures the
response, and writes a per-task evidence file to a sibling local
checkout that has no GitHub remote. Devbench drives every step --
claim, executor (run the command, write the file), review pipeline,
git commit -- without producing a PR.

## When to use this pattern

Use it when **all** of the following hold:

- The work is a sequence of independent or simply-ordered actions you
  could otherwise script as a runbook.
- Every action has a well-defined verify step (idempotency check,
  expected-state assertion).
- You want devbench-grade discipline (per-task review, audit log, halt
  on failure) but no PR gate makes sense because there is no
  application code to merge.
- A reviewer-readable history of what ran, when, and what the result
  was is itself the deliverable.

Do **not** use it when:

- You actually want PR review and CI gates -- use
  single-branch + defer-PR or multi-PR mode instead.
- Tasks need to coordinate state across machines in real time -- a
  sequential single-branch backlog is not a workflow engine.

## How to set it up

### 1. Provision the target "repo"

Create a sibling directory under your workspace root that is a git
repo with one branch (`main` is fine), no remotes, and a starting
commit:

```bash
mkdir -p ./teardown-records           # or audit-records, ops-evidence, ...
cd teardown-records
git init -b main
echo "# Operational evidence for <workspace>" > README.md
git add README.md && git commit -m "initial"
cd ..
```

DevBench's pre-flight check (`devbench check`) will REQUIRE that this
directory has no `origin` remote when `git_ops.local_only: true`.

### 2. Configure the workspace

In `backlog/config/devbench.yaml`:

```yaml
repos:
  caylent-solutions/<your-repo-name>:
    checkout_directory: teardown-records
    default_branch: main          # required under local_only

allowed_orgs:
  - caylent-solutions

git_ops:
  single_branch: feat/<workspace-name>
  defer_pr: true
  local_only: true
```

The `repos:` map still uses a fully-qualified `org/repo` name, but no
GitHub repo by that name needs to exist. Devbench treats it as the
workspace identifier; the actual on-disk target is the
`checkout_directory` sibling.

### 3. Author tasks

Each work unit's Approach should be:

1. The destructive / mutating command (with the AWS profile, region,
   target ID etc. inlined as the operator authored them).
2. The verification command and its expected result.
3. A directive to write a per-task evidence file under the
   `checkout_directory`, one file per task, named after the task ID
   (e.g. `destroy-log/E1-F1-S1-T1.md`). One-file-per-task avoids
   manifest conflicts and gives clean per-task git history.

The Changes Manifest names exactly the new evidence file. The
Acceptance Criteria spell out the expected verify-command output.

### 4. Run

```bash
/devbench:orchestrate
```

DevBench drives every task: claim, ensure-branch (creates the local
single-branch off `refs/heads/main`, no fetch), executor (runs the
external commands and writes the evidence file), review-supervisor,
security review, git-ops (commits locally), mark-done, next. All 31
or 100 or 500 tasks accumulate as commits on the shared local branch
in your sibling checkout. No push, no PR, no CI.

The commit history in the sibling checkout becomes the audit log.

## What you don't need to do

- No GitHub repo creation. No remote push. No PR review setup.
  No CI workflow. No branch-protection rules. No external reviewers.

## See also

- [`git-ops-modes.md`](git-ops-modes.md) -- the full list of git-ops
  modes; local-only is one of four.
- [`docs/cli-reference.md`](cli-reference.md) -- `devbench check`,
  `devbench ensure-branch`, `devbench git-ops`.
- [`plugin/devbench-orchestrate/skills/orchestrate/SKILL.md`](../plugin/devbench-orchestrate/skills/orchestrate/SKILL.md)
  -- the orchestrator loop the local-only path flows through.
