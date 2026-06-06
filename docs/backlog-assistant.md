# devbench-backlog-assistant

The `devbench-backlog-assistant` plugin is an operator-phase diagnosis and remediation
assistant for devbench workspaces. It ships nine skills that help operators understand
why work units are blocked, what has drifted, and what to do next.

**Safety model**: every skill prints the mutating command and STOPS. No mutating verb
is ever run without an explicit operator CONFIRM. The plugin has no PreToolUse hooks.

## Installation

The plugin is co-located in the `plugin-authoring` marketplace:

```bash
claude --plugin-dir plugin-authoring/devbench-backlog-assistant
```

Or install via the authoring marketplace (which registers both `devbench-authoring` and
`devbench-backlog-assistant`):

```bash
claude --plugin-dir plugin-authoring
```

## The nine skills

### 1. triage-blocked-task

```
claude run devbench-backlog-assistant:triage-blocked-task <id>
```

Classifies a blocked work unit by calling `classify_blocked_task` (no reimplementation),
prints the audit tail and which signals fired, then prints the remediation command from
the seven-bucket matrix -- and STOPS.

Sub-cap 1a (composite, issue #248): if `RUNTIME_DEGRADATION`, re-classifies excluding
the degradation rung; if the result is `OPERATOR_ACTION_REQUIRED`, warns that a restart
will NOT resolve the co-existing structural blocker.

Sub-cap 1b (thrash, issue #248): counts `CASCADE_RECONCILED` re-queuing cycles; above
`skills.cascade_thrash_threshold` (default 3) warns of a thrash condition.

### 2. audit-backlog-impossibilities

```
claude run devbench-backlog-assistant:audit-backlog-impossibilities [--json]
```

Wraps `validate-backlog` (or calls `BacklogManager` check helpers directly when absent)
and groups findings by work unit, severity, and suggested fix. Guards against false positives
on clean snapshots. Writes a structured JSON to `/tmp/devbench-audit-findings.json`.

### 3. rewrite-impossibility

```
claude run devbench-backlog-assistant:rewrite-impossibility <id>
```

Classifies the structural impossibility, offers either an in-place operator-mode amendment
(assembles `/tmp/devbench-amendment-<id>.json` and suggests `request-amendment --operator-mode`)
or a decline-and-recreate command sequence. Verifies the payload parses and the new id is
unique before presenting. STOPS.

### 4. cascade-status

```
claude run devbench-backlog-assistant:cascade-status <id> <status> [--reason <text>]
```

Wraps `set-status --cascade` (#245) when present; otherwise computes a leaves-first
traversal order manually and emits the ordered per-WU commands. Excludes terminal and
invalid WUs from the traversal. STOPS.

### 5. backlog-health-check

```
claude run devbench-backlog-assistant:backlog-health-check [--auto-fix]
```

Classifies every blocked WU using the seven-bucket matrix, prints a bucket histogram
and the `OPERATOR_ACTION_REQUIRED` id list with per-bucket batch suggestions. Asserts
that bucket counts sum to the total blocked count. With `--auto-fix`, confirms per WU
before suggesting the individual fix. STOPS.

### 6. reconcile-backlog-md

```
claude run devbench-backlog-assistant:reconcile-backlog-md
```

Wraps `reconcile-backlog-md --check-only` (#243) when present; otherwise computes drift
manually by comparing BACKLOG.md rows to on-disk WU files. Prints per-row corrections
or a `--force` suggestion. Never silently rewrites BACKLOG.md. STOPS.

### 7. amend-manifest-offline

```
claude run devbench-backlog-assistant:amend-manifest-offline <id>
```

**#242 fallback only**: runs Layer-1 pre-filter checks offline and emits an amendment
proposal the blocker-resolver can consume. **Deprecated and removed once #242 ships**
(use `request-amendment --operator-mode` instead). STOPS.

### 8. refactor-target-repository

```
claude run devbench-backlog-assistant:refactor-target-repository <id> <new-repo>
```

Finds every old-repo reference in a WU (Target Repository, Manifest rows, AC/DoD path
tokens), assembles a `target_repository` patch amendment payload at
`/tmp/devbench-repo-refactor-<id>.json`, suggests `request-amendment --operator-mode`
and then `reconcile-backlog-md`. Verifies patched-count equals found-count before
presenting. STOPS.

### 9. diagnose-review-stuck

```
claude run devbench-backlog-assistant:diagnose-review-stuck <id>
```

Distinguishes *functionally-complete-but-misattributed* WUs (manifest files present under
a sibling commit -- suggests `decline <id> --reason "superseded by commit <SHA>"`, SHA
verified via `git show <SHA> -- <path>`) from *genuinely-incomplete* WUs (manifest files
absent -- re-queue or escalate). Works pre-#247 by reading `git log` directly. STOPS.

## Seven-bucket remediation matrix

The single source rendered by skills 1 and 5:

| Bucket | Remediation |
|--------|-------------|
| `HELD` | `uv run devbench unhold <id>` |
| `BLOCKED_ON_HELD` | `uv run devbench unhold <target>` |
| `AUTO_CLEARING_VIA_PROPOSAL` | No action (optional: `uv run devbench reconcile-cascade`) |
| `AWAITING_DEPENDENCY` | Wait, or: `uv run devbench set-status <dep> done && uv run devbench reconcile-cascade` |
| `AWAITING_AMENDMENT_RECOVERY` | Show pending proposal; `uv run devbench reconcile-cascade` if stalled; route rejected amendment to `rewrite-impossibility <id>` |
| `RUNTIME_DEGRADATION` | `make start` (see sub-cap 1a/1b warnings) |
| `OPERATOR_ACTION_REQUIRED` | Route by sub-cause: target-repo -> `refactor-target-repository`; structural -> `rewrite-impossibility`; review-stuck -> `diagnose-review-stuck`; out-of-scope -> `decline` |

## Safety model

**Read-only verbs** the skills MAY run: `read-unit`, `status`, `report`, `validate-backlog`,
`reconcile-backlog-md --check-only`, `list-proposals`, `get-diff`, `next`, library calls,
`git log/show`.

**Mutating verbs** are only printed, never run without CONFIRM: `set-status`, `mark-done`,
`hold`, `unhold`, `decline`, `promote`, `add-dep`, `new-task`, `request-amendment`,
`apply-amendment`, `reject-amendment`, `reconcile-cascade`, `reconcile-backlog-md --force`,
`--cascade`, `log-comment`.

No PreToolUse hooks are installed. The plugin is safe to enable in any operator workspace.

## Configuration knobs

These live under `skills.*` in `backlog/config/devbench.yaml`:

```yaml
skills:
  cascade_thrash_threshold: 3   # triage sub-cap 1b: cycles above this warn
  triage_audit_tail: 20         # triage: lines of audit tail to print
```

## Graceful degradation

Each skill documents what it does when a dependency issue (#242, #243, #244, #245,
#247, #248) is absent. Degraded paths are always explicit -- never silent.

## Related documentation

- `docs/block-types.md` -- BlockedTaskState bucket definitions
- `docs/manifest-amendments.md` -- amendment workflow
- `docs/creating-specs-and-backlogs.md` -- spec and backlog authoring
