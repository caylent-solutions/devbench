# DevBench — System Overview

An LLM-powered software development system that takes a specification-driven backlog and autonomously implements it: writing code, running tests, reviewing changes, creating PRs, and merging. The system runs unattended with LLM judges enforcing quality at every gate. A human can pause and intervene at any point during execution.

## How It Works

A Claude Code agent reads work units from a structured backlog, implements each one following TDD, submits the work to five LLM judge agents for review, and upon approval creates a PR, waits for CI, merges, and moves to the next unit.

```
┌─────────────────────────────────────────────────────────────────┐
│                        BACKLOG.md                               │
│  Work units organized as Epics > Features > Stories > Tasks     │
│  Dependency DAG controls execution order                        │
└────────────────────────────────┬────────────────────────────────┘
                                 │
                                 ▼
┌─────────────────────────────────────────────────────────────────┐
│                    ORCHESTRATOR LOOP                            │
│                                                                 │
│  0. Pre-flight: validate backlog integrity — abort on errors    │
│  1. Find next actionable work unit (deps satisfied)             │
│  2. Read work unit spec, ACs, and CLAUDE.md standards           │
│  3. Create feature branch in target repo                        │
│  4. Implement via TDD (RED → GREEN → REFACTOR)                  │
│  5. Run the repo's task runners to validate changes             │
│  6. Stage files and submit to judge review                      │
│  7. If judges reject → inject prior feedback, fix, resubmit     │
│  8. If judges approve → commit, push, create PR                 │
│  9. Wait for GitHub CI checks to pass                           │
│ 10. Merge PR, update submodule ref, mark done (done-gate)       │
│ 11. Loop back to step 1                                         │
│                                                                 │
│  Human can pause (Escape), give instructions, resume (Continue) │
└────────────────────────────────┬────────────────────────────────┘
                                 │
                                 ▼
┌─────────────────────────────────────────────────────────────────┐
│                     FIVE LLM JUDGES                             │
│                                                                 │
│  Each judge gathers evidence, sends it to the configured Claude │
│  model, and the LLM makes the pass/fail decision.              │
│                                                                 │
│  On re-review, prior feedback is injected to prevent the LLM    │
│  from contradicting its previous findings.                      │
│                                                                 │
│  ┌──────────────┐  ┌───────────────┐  ┌───────────────┐         │
│  │ code_review  │  │ test_review   │  │  doc_review   │         │
│  │              │  │               │  │               │         │
│  │ Evidence:    │  │ Evidence:     │  │ Evidence:     │         │
│  │ • git diff   │  │ • make test   │  │ • git diff    │         │
│  │ • work unit  │  │   output      │  │ • doc files   │         │
│  │ • prior      │  │ • test files  │  │ • work unit   │         │
│  │   feedback   │  │ • work unit   │  │ • prior       │         │
│  │              │  │ • prior       │  │   feedback    │         │
│  │ Evaluates:   │  │   feedback    │  │               │         │
│  │ • AC coverage│  │               │  │ Evaluates:    │         │
│  │ • SOLID/DRY  │  │ Evaluates:    │  │ • Accuracy    │         │
│  │ • Fail-fast  │  │ • TDD cycle   │  │ • Completeness│         │
│  │ • Security   │  │ • Test quality│  │ • Sync w/code │         │
│  │ • Prohibited │  │ • Assertions  │  │ • Stale refs  │         │
│  │   patterns   │  │ • Markers     │  │               │         │
│  │ • 12-factor  │  │ • Task runner │  │               │         │
│  │ • Task runner│  │   validation  │  │               │         │
│  └──────┬───────┘  └──────┬────────┘  └──────┬────────┘         │
│         │                 │                  │                  │
│  ┌──────┴─────────────────┴──────────────────┴────────┐         │
│  │              changes_manifest                      │         │
│  │                                                    │         │
│  │  Evidence: changed file list, diff summary         │         │
│  │  Evaluates: actual changes vs. expected manifest   │         │
│  └────────────────────────┬───────────────────────────┘         │
│                           │                                     │
│           ┌───────────────┴───────────────────┐                 │
│           │         security_review           │                 │
│           │                                   │                 │
│           │  Evidence: GitHub CodeQL,          │                 │
│           │  Dependabot, secret scanning      │                 │
│           │  alerts, git diff                 │                 │
│           │  Evaluates: open alerts,          │                 │
│           │  security anti-patterns           │                 │
│           │                                   │                 │
│           │  Runs AFTER the other 4 pass —    │                 │
│           │  final security gate before merge │                 │
│           └───────────────────────────────────┘                 │
│                                                                 │
│  ALL FIVE must pass. Any failure → feedback to dev agent.       │
└─────────────────────────────────────────────────────────────────┘
```

## Full SDLC Process

Every work unit goes through the complete software development lifecycle.

### 1. Planning and Requirements

- Work units are pre-decomposed from a specification document into Epics, Features, Stories, and Tasks
- Each task has acceptance criteria, a changes manifest, dependency list, and spec references
- The backlog parser respects the dependency DAG — a task only becomes actionable when all its dependencies are done

### 2. Branch and Environment Setup

- Agent creates a feature branch (`backlog/<unit-id>`) in the target submodule repo
- Agent reads CLAUDE.md standards, AGENT-INSTRUCTIONS.md, and the full work unit spec before writing any code

### 3. Test-Driven Development

- **RED**: Agent writes failing tests first, runs them, confirms they fail
- **GREEN**: Agent writes minimal code to make tests pass
- **REFACTOR**: Agent cleans up while tests stay green
- Each phase is logged in the work unit's TDD Cycle Log with timestamps

### 4. Task Runner Validation

- Agent runs the repo's task runners and logs the output
- The test_review judge runs `make test` (if the repo has a Makefile with a test target) or falls back to bare `pytest`
- This ensures the judge sees the same results as the developer, including environment variables, flags, and exclusions configured in the Makefile
- The LLM judges verify task runner correctness by reading the config from the diff and checking the agent log for execution evidence

### 5. Documentation Synchronization

- Documentation is updated in the same change as code (CLAUDE.md requirement)
- Agent updates README, contributing guides, and inline help comments

### 6. LLM Judge Review

- Agent stages files and runs `judges.cli review <unit-id>`
- Four independent LLM judges evaluate the work (code_review, test_review, doc_review, changes_manifest), each calling the configured Claude model
- Each judge gathers its own evidence (diffs, test output, file contents, changed file lists)
- The LLM makes every pass/fail decision — no hardcoded rules
- Judges verify task runner correctness by reading the config in the diff and checking the agent log

### 7. Review Feedback Loop with Prior Feedback Injection

When judges fail, the agent reads the feedback and fixes the issues. On re-review, the system injects the prior feedback to maintain consistency:

```
┌──────────────────────────────────────────────────────┐
│                 FEEDBACK LOOP                        │
│                                                      │
│  Round 1: Fresh review → judge gives feedback        │
│           "Use xfail marker instead of --deselect"   │
│                    │                                 │
│                    ▼                                 │
│  Agent fixes code based on feedback                  │
│                    │                                 │
│                    ▼                                 │
│  Round 2: Re-review with prior feedback injected     │
│           LLM sees: "Previous feedback said use      │
│           xfail. Code now has xfail. ✓ Addressed."   │
│                    │                                 │
│           Without this → LLM might flip-flop:        │
│           "Use --deselect instead of xfail" ✗        │
│                                                      │
│  Prior feedback is parsed from orchestrator.log      │
│  and injected as an evidence section per judge.      │
└──────────────────────────────────────────────────────┘
```

The prompt instructs:
- If the code addresses prior feedback, do not re-raise the same issues
- Do NOT contradict prior feedback by requesting the opposite change
- If the code has NOT addressed the feedback, flag it again

Up to 10 retry attempts before marking the unit as blocked.

### 8. Security Review

- After the first 4 judges pass, the security review judge queries GitHub CodeQL, Dependabot, and secret scanning alerts via the GitHub API
- The security judge evaluates whether open alerts are related to the current changes or pre-existing upstream issues

### 9. Git Operations and PR

- Agent commits to the feature branch with selective `git add` (never `git add -A`)
- Pushes and creates a PR using `gh pr create --repo <owner/name>` (explicit repo targeting to avoid fork confusion)
- All `gh pr` commands use `--repo` to target the correct fork

### 10. CI/CD Gate

- Agent runs `gh pr checks --watch` and waits for all GitHub Actions checks to pass
- Target repos have branch protection on `main` requiring checks to pass

### 11. Merge and Status Update

- Agent merges the PR via `gh pr merge --delete-branch` using the strategy set by `JUDGE_MERGE_STRATEGY` (default: `squash`)
- Updates the parent repo's submodule reference
- Marks the work unit as Done via `mark_done()` — enforces the done-gate before writing
- The done-gate verifies all four required review judges (`code_review`, `test_review`, `doc_review`, `changes_manifest`) have a `[REVIEW_PASS]` entry in the most recent round; raises `RuntimeError` otherwise
- All writes go through the private `_set_status()` workhorse which atomically updates both the work unit file and BACKLOG.md
- When all child tasks of a Story/Feature/Epic are Done, the parent is automatically rolled up to Done via `_set_status()` (cascading upward; gate bypassed since rollup is structurally correct)

### 12. Session Recovery

- If the agent session is interrupted, the next session recovers in-progress work
- Agent detects uncommitted changes on existing branches and continues from where it left off

## Environment Variables

All configuration is via environment variables. Required variables raise `RuntimeError` at startup if unset.

### Required

| Variable | Description |
|----------|-------------|
| `JUDGE_WORKSPACE_ROOT` | Absolute path to workspace root containing all repo clones |

### YAML Configuration File

Repos, model identifiers, and per-repo settings are defined in `backlog/config/devbench.yaml` (relative to `JUDGE_WORKSPACE_ROOT`). Copy `sample-config.yaml` from the repo root as a starting point.

**Config file path resolution** (first match wins):
1. `--config <path>` CLI argument
2. `JUDGE_CONFIG_PATH` environment variable
3. `<JUDGE_WORKSPACE_ROOT>/backlog/config/devbench.yaml` (default)

The config file must exist at the resolved path — a missing file raises `RuntimeError` with an actionable message.

**YAML schema:**

```yaml
repos:
  org/repo:                          # key must be "org/repo" format; at least one required
    default_branch: main2            # optional — omit to fall back to origin/HEAD
    checkout_directory: my-checkout  # optional — relative to JUDGE_WORKSPACE_ROOT
                                     # omit to use repo short-name (e.g. "my-repo")

allowed_orgs:                        # optional — restrict repos to these GitHub orgs
  - caylent-solutions

judge_model: claude-sonnet-4-6-20251001     # optional — model for LLM judge agents
executor_model: claude-sonnet-4-6-20251001  # optional — model for executor subprocess

use_bedrock: false                   # optional — route LLM calls via AWS Bedrock
bedrock_region: us-east-1            # required when use_bedrock: true — AWS region (env var override: JUDGE_BEDROCK_REGION)

git_ops:                             # optional — git workflow behaviour settings
  update_submodule: false            # set true only when repos are git submodules of a parent repo
```

**Model configuration precedence** (first match wins, applied to both `CLAUDE_MODEL` and `EXECUTOR_MODEL`):
1. `ANTHROPIC_MODEL` env var — silently overrides both models
2. `JUDGE_CLAUDE_MODEL` env var — **deprecated**; populates both models with a WARNING
3. `judge_model` / `executor_model` in YAML — sets each model independently
4. Auth-dependent default env var: `JUDGE_DEFAULT_MODEL_DIRECT` (when `use_bedrock=false`) or `JUDGE_DEFAULT_MODEL_BEDROCK` (when `use_bedrock=true`). A `RuntimeError` is raised at startup if neither YAML field nor the applicable default env var is set.

**Auth (USE_BEDROCK) precedence:**
1. `JUDGE_USE_BEDROCK` env var — overrides YAML value
2. `use_bedrock` in YAML — default `false`

When `use_bedrock` is `true`, the executor subprocess receives `CLAUDE_CODE_USE_BEDROCK=1` in its environment. When `false`, `CLAUDE_CODE_USE_BEDROCK` is explicitly removed from the subprocess environment.

**Timeout, limit, and max_retries precedence** (first match wins, silent — no warning emitted):
1. Environment variable override
2. YAML field (`timeouts.*`, `limits.*`, or `max_retries`)
3. Schema default (defined in `config-schema.json`; no hardcoded values in Python)

Setting a timeout/limit env var to an empty string (`VAR=`) is a misconfiguration and raises `ValueError` at startup. Unset the variable or provide a valid integer.

| Variable | YAML Field | Schema Default | Description |
|----------|------------|--------------|-------------|
| `JUDGE_CONFIG_PATH` | — | *(see above)* | Override YAML config file path |
| `JUDGE_MERGE_STRATEGY` | — | `squash` | PR merge strategy: `merge`, `squash`, or `rebase` |
| `ANTHROPIC_MODEL` | — | *(YAML or default)* | Silently overrides both `CLAUDE_MODEL` and `EXECUTOR_MODEL` |
| `JUDGE_DEFAULT_MODEL_DIRECT` | — | *(required when no YAML model and not using Bedrock)* | Default model ID for direct Anthropic API path. RuntimeError at startup if unset and no YAML model or `ANTHROPIC_MODEL` is configured. |
| `JUDGE_DEFAULT_MODEL_BEDROCK` | — | *(required when no YAML model and using Bedrock)* | Default model ID for AWS Bedrock path. RuntimeError at startup if unset and no YAML model or `ANTHROPIC_MODEL` is configured. |
| `JUDGE_CLAUDE_MODEL` | — | *(deprecated)* | **Deprecated.** Use `judge_model`/`executor_model` in YAML or `ANTHROPIC_MODEL` instead |
| `JUDGE_GH_ORG` | — | *(deprecated)* | **Deprecated.** Use `allowed_orgs` in YAML instead |
| `JUDGE_MAX_RETRIES` | `max_retries` | `10` | Max retry attempts per work unit before marking blocked |
| `JUDGE_USE_BEDROCK` | `use_bedrock` | *(YAML value)* | Override `use_bedrock` from YAML. `1`/`true`/`yes` enables Bedrock |
| `JUDGE_BEDROCK_REGION` | `bedrock_region` | *(required when use_bedrock=true)* | AWS region for Bedrock; falls back to `AWS_REGION`, then `bedrock_region` in YAML. RuntimeError if all absent and `use_bedrock=true`. |
| `JUDGE_GH_TOKEN_FILE` | — | `~/.gh_token_env` | GitHub token file path |
| `JUDGE_GH_TIMEOUT` | `timeouts.github_check` | `600` (schema) | GitHub check wait timeout (seconds) |
| `JUDGE_GH_API_TIMEOUT` | `timeouts.gh_api` | `30` (schema) | GitHub API call timeout (seconds) |
| `JUDGE_TEST_TIMEOUT` | `timeouts.test` | `300` (schema) | Test execution timeout (seconds) |
| `JUDGE_LLM_TIMEOUT` | `timeouts.llm` | `300` (schema) | LLM evaluation timeout (seconds) |
| `JUDGE_COMMAND_TIMEOUT` | `timeouts.command` | `120` (schema) | Maximum seconds to wait for a subprocess command before timing out. Used by `run_command()` in `utils/process.py` and all judge subprocess calls. |
| `JUDGE_EXECUTOR_TIMEOUT` | `timeouts.executor` | `1800` (schema) | Dev agent execution timeout (seconds) |
| `JUDGE_EXECUTOR_MAX_TURNS` | `timeouts.executor_max_turns` | `50` (schema) | Max turns for dev agent execution |
| `JUDGE_ORCHESTRATOR_POLL_INTERVAL` | `timeouts.orchestrator_poll_interval` | `10` (schema) | Seconds between orchestrator poll cycles |
| `JUDGE_SECURITY_FETCH_TIMEOUT` | `timeouts.security_fetch` | `120` (schema) | Security advisory fetch timeout (seconds) |
| `JUDGE_OUTPUT_TRUNCATION` | `limits.output_truncation` | `2000` (schema) | Output truncation limit (chars) |
| `JUDGE_LLM_EVIDENCE_TRUNCATION` | `limits.llm_evidence_truncation` | `15000` (schema) | LLM evidence truncation (chars) |
| `JUDGE_LLM_FILE_CONTEXT_LIMIT` | `limits.llm_file_context` | `5` (schema) | Max files sent to LLM context |
| `JUDGE_LLM_FILE_PREVIEW_CHARS` | `limits.llm_file_preview_chars` | `3000` (schema) | Per-file preview truncation (chars) |
| `JUDGE_ALERT_SUMMARY_LIMIT` | `limits.alert_summary` | `10` (schema) | Max security alerts included in judge evidence |
| `JUDGE_CLAUDE_CREDENTIALS_FILE` | — | `~/.claude/.credentials.json` | Claude Code OAuth credentials file path |

## Key Design Decisions

| Decision | Rationale |
|----------|-----------|
| LLM-only verdicts | No hardcoded pass/fail rules. The LLM evaluates context, intent, and quality. |
| Evidence gathering is deterministic | Git diffs, test output, file lists collected reliably. Only the judgment is LLM. |
| Prior feedback injection | Re-reviews include previous feedback to prevent contradictory findings across rounds. Parsed from orchestrator log per judge per unit. |
| Task runner integration | test_review runs `make test` when available, respecting repo-configured env vars and flags. Falls back to bare `pytest`. |
| Evidence truncation markers | Truncated content includes `[... TRUNCATED — showing N of M chars]` so the LLM knows it's seeing a preview, not an incomplete file. |
| Five independent judges | Separation of concerns: code quality, test quality, documentation, scope control, and security evaluated independently. |
| Fail-fast feedback loop | Up to 10 retry attempts with specific feedback. Agent fixes real issues, not noise. |
| Private write path | `_set_status()` is the single private workhorse: always writes both the work unit file and BACKLOG.md atomically. No public caller can write to just one file. |
| Public API separation | `force_status()` — any status, no gate (lifecycle transitions and recovery). `mark_done()` — gated completion only. `mark_blocked()` — blocked with reason. Rollups call `_set_status()` directly (structurally correct, no judge review required). |
| Done-gate enforcement | `mark_done()` checks the work unit's comment history before writing `done`. All four review judges must have a `[REVIEW_PASS]` entry in the most recent round — `[REVIEW_REJECTED]` resets the window. Works across process restarts since the work unit file is the source of truth. |
| Backlog integrity check | `validate-backlog` (CLI) and orchestrator pre-flight check detect missing files, status mismatches, orphaned work unit files, and invalid dependency references before any work begins. |
| Automatic status rollup | When all children of a Story/Feature/Epic are Done, parent auto-rolls to Done via `_set_status()`. Cascades upward. |
| Exact ID matching | Status updates match the ID cell exactly, not as a substring. |
| Case-insensitive status matching | Recognizes both `in-queue` (lowercase) and `In Queue` (title-case). Writes lowercase. |
| Explicit `--repo` on all `gh` commands | Prevents PRs from being created against upstream parent repos in fork workflows. |
| Dynamic default branch resolution | `git rev-parse --abbrev-ref origin/HEAD` — works with main, master, or any default. |
| Graceful timeout handling | `_run_command()` catches `TimeoutExpired` and returns it as evidence instead of crashing. |
| No commit before judge approval | Agents stage files but don't commit until all judges pass. |
| Dual LLM backend support | Anthropic API (Claude Code OAuth) or AWS Bedrock — configurable via environment variables. |

## Evidence Gathering

Each judge collects evidence from the repo and sends it to the LLM. The evidence sources cover all git states.

| Evidence Source | Git Command | What It Captures |
|----------------|-------------|------------------|
| Staged changes | `git diff --cached` | Files added with `git add` but not committed |
| Unstaged changes | `git diff` | Modified tracked files not yet staged |
| Untracked files | `git ls-files --others --exclude-standard` | New files not yet added |
| Branch changes | `git diff <default-branch>` | All commits on the feature branch vs. default |
| Prior feedback | Orchestrator log parsing | Most recent feedback per judge for this unit |

The default branch is resolved dynamically — no hardcoded `main` or `master`.

## GitHub Security Configuration

Target repos have security features enabled and branch protection configured:

| Feature | Description |
|---------|-------------|
| Dependabot vulnerability alerts | Automated dependency vulnerability detection |
| Automated security fixes | Auto-generated PRs for vulnerable dependencies |
| CodeQL scanning | Static analysis for security vulnerabilities |
| Secret scanning | Detection of committed secrets and credentials |
| Push protection | Blocks pushes containing detected secrets |
| Branch protection on main | PRs required, no direct push, no force push |
| Required CI checks | All checks must pass before merge |
