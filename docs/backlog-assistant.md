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

### Enabling takes effect in the next session (TDI-006)

`claude plugin enable devbench-backlog-assistant` registers the plugin's skills at the
**next** session start, not in the session the command was run in (skills are discovered
when a session begins). If a skill is not invocable immediately after enabling, start a new
session (or restart the active one) -- do not assume the skill is live mid-session.

### `triage-blocked-task` routes (TDI-006)

Beyond the structural buckets, the matrix routes three operationally common states:

- **Done-gate deferred-evidence hold** -- a fully-implemented unit `HELD` solely because an
  executable AC is `type=deferred` and `done_gate.allow_deferred_evidence` is `false`. When the
  deferred AC names a runnable tool, the remedy is to reclassify it to `type=command` (TDI-004)
  and re-queue, NOT to flip the secure-default policy.
- **`INTERRUPTED_ON_STOP`** -- force-blocked by the SIGTERM shutdown safeguard with no structural
  blocker; re-queued automatically on the next sweep (TDI-002).
- **Pending proposal with alternatives** -- when `.devbench/proposals/<id>.json` carries
  `proposed_tasks`, the skill lists the draft resolution-path ids + titles and instructs the
  operator to promote one (or fold the fix into the source unit).

## Auto-resolve engine (issue #263, opt-in)

The auto-resolve engine lets the `triage-blocked-task` skill apply a
whitelisted, non-destructive remediation without operator intervention.
It is **opt-in**: the default is advise-only mode, preserving the existing
safety model.

### Opt-in toggle

Enable auto-resolve by setting `auto_resolve.enabled: true` in
`backlog/config/devbench.yaml`:

```yaml
auto_resolve:
  enabled: true        # default: false (advise-only)
  max_attempts: 3      # default: 3 (per task+signature budget)
```

You may also override the toggle at runtime via the
`DEVBENCH_AUTO_RESOLVE_ENABLED` environment variable (any truthy value
enables; precedence: env var > YAML > default).

When `auto_resolve.enabled` is `false` (the default), the triage skill
skips the engine entirely and returns the advise-only payload byte-for-byte
unchanged.

### Non-destructive whitelist

Only the following remediation verbs may ever be auto-applied:

- `re-queue`
- `set-status in-queue`
- `reconcile-cascade`
- `restart-signal`

These verbs are non-destructive: they re-submit or restart a work unit
without altering its specification, manifest, or status history.

Destructive verbs (`decline`, `mark-done`, `force-status`) are
**hard-excluded** regardless of configuration. Attempting to auto-apply one
raises `ValueError` before the enabled check -- the guard fires even when
`auto_resolve.enabled` is `true`.

### Per-(task, signature) budget

The engine enforces a per-`(task_id, signature)` attempt budget:

- Default: `max_attempts: 3` (override via `DEVBENCH_AUTO_RESOLVE_MAX_ATTEMPTS`
  env var or `auto_resolve.max_attempts` in YAML).
- When the budget is not yet exhausted, the engine auto-applies the
  remediation and writes the verbatim audit string
  `[AUTO_RESOLVED] task_id=<id> signature=<sig> remediation=<verb>` to
  stderr.
- When the budget is exhausted, the engine writes
  `[AUTO_RESOLVE_ESCALATED]` to stderr and returns the advise-only payload
  unchanged -- the operator must intervene manually.

The budget is keyed on `(task_id, signature)`, not on the bucket name, so
repeated structural changes to the same work unit each get their own fresh
counter.

### Learning catalog

The engine consults and updates the agnostic resolution catalog at:

```
<workspace>/.devbench/operator-resolution-catalog.json
```

Each entry is keyed by `<classification>:<normalized_signature>` and
records:

- The classification bucket (e.g. `RUNTIME_DEGRADATION`).
- The normalized signature (stripped of task-id and app-specific content).
- The remediation verb that was applied.
- Success count, failure count, and last-applied timestamp (UTC ISO-8601).

The catalog is schema-versioned (`CATALOG_SCHEMA_VERSION = 1`). A malformed
or legacy catalog (wrong schema version, invalid JSON, unexpected structure)
is treated as empty and self-heals -- load never raises a fatal error.
Writes are atomic: the engine writes to `operator-resolution-catalog.json.tmp`
and renames it into place.

**Novel signatures**: when a normalized signature has no prior catalog entry,
the engine records it with outcome `"novel"` and returns advise-only without
consuming budget. The operator must confirm the pattern (by running the
suggested command once manually) before the engine auto-applies future
occurrences.

### Escalation behavior

The engine follows this decision order (in priority sequence):

1. Destructive-verb guard: raises `ValueError` unconditionally.
2. Disabled gate: when `auto_resolve.enabled` is `false`, returns advise-only unchanged.
3. Composite-block gate: when the classification is `RUNTIME_DEGRADATION`
   and a structural co-blocker also exists, the engine returns advise-only
   without consuming budget (a restart alone cannot clear the structural blocker).
4. Whitelist gate: a non-destructive but unlisted verb stays advisory.
5. Novel-signature gate: unrecognized signature is recorded for operator review;
   advise-only returned.
6. Budget gate: when the per-`(task_id, signature)` count equals
   `max_attempts`, emits `[AUTO_RESOLVE_ESCALATED]` and returns advise-only.
7. Apply path: emits `[AUTO_RESOLVED]` to stderr, records `"applied"` in the
   catalog, returns advise-only payload unchanged as the printed output.

The triage skill delegates this entire decision tree to `apply_auto_resolve`
and never reimplements it inline.

### Integration with triage-blocked-task

When `auto_resolve.enabled` is `true`, the `triage-blocked-task` skill adds
Step 4a after generating the advise-only payload:

1. Reads `cfg.auto_resolve.enabled` from `devbench.yaml`.
2. Derives a normalized blocker signature from the audit tail.
3. Calls `apply_auto_resolve` with the task id, signature, remediation verb,
   advise-only payload, config, bucket classification, and workspace path.
4. Prints the result (which is the advise-only payload regardless of path).
5. STOPS -- the operator may CONFIRM the printed command.

The `[AUTO_RESOLVED]` or `[AUTO_RESOLVE_ESCALATED]` audit strings appear on
stderr (visible in the Claude session log) but do not change the printed
operator-facing output.

## Configuration knobs

These live under `skills.*` in `backlog/config/devbench.yaml`:

```yaml
skills:
  cascade_thrash_threshold: 3   # triage sub-cap 1b: cycles above this warn
  triage_audit_tail: 20         # triage: lines of audit tail to print
```

Auto-resolve knobs live under `auto_resolve.*`:

```yaml
auto_resolve:
  enabled: false       # master toggle; default false (advise-only)
  max_attempts: 3      # per-(task, signature) budget before escalation
```

Environment-variable overrides (both follow env var > YAML > default
precedence):

| Variable | Effect |
|----------|--------|
| `DEVBENCH_AUTO_RESOLVE_ENABLED` | Sets `auto_resolve.enabled` |
| `DEVBENCH_AUTO_RESOLVE_MAX_ATTEMPTS` | Sets `auto_resolve.max_attempts` |

## Graceful degradation

Each skill documents what it does when a dependency issue (#242, #243, #244, #245,
#247, #248) is absent. Degraded paths are always explicit -- never silent.

## Related documentation

- `docs/block-types.md` -- BlockedTaskState bucket definitions
- `docs/manifest-amendments.md` -- amendment workflow
- `docs/creating-specs-and-backlogs.md` -- spec and backlog authoring
