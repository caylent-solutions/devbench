# DevBench Glossary

This document defines the canonical terms used across DevBench documentation,
source code, and backlog files. The definitions below are the authoritative
spellings from spec section 1.2. Any divergence from these spellings in other
docs is a terminology drift violation (spec section 5.2).

For the broader set of architectural terms (work unit, task, epic, judge, etc.)
see the [Glossary section in docs/architecture.md](architecture.md#12-glossary).

---

## Canonical Terms (spec section 1.2)

| Term | Canonical spelling | Meaning |
|------|--------------------|---------|
| `draft` | lower-case, code-style | Pre-`in-queue` work-unit status. Operator-staged; not yet approved for autonomous claim. The orchestrator's `get_parallel_candidates` skips draft work units entirely. Promoted to `in-queue` via `devbench promote`. |
| `in-queue` | lower-case, hyphenated | Approved and ready for autonomous claim by the orchestrator. The default status for newly created work units unless `backlog.default_status_for_new_work_units: draft` is configured. |
| `scope` | lower-case | A subset of the backlog selected via printer-pages-style `--include` / `--exclude` tokens (e.g., `E1,E3-E5`). Persisted across commands in `<workspace>/.devbench/sessions/<name>/scope.json`. |
| `drain` | lower-case | An operator-initiated graceful stop request. The orchestrator finishes the current work unit, detects the drain marker between work units, and exits cleanly. Written by `devbench drain`; consumed on orchestrator exit. |
| `session` | lower-case | A named orchestrator process with its own scope, drain marker, log file, report cache, and PID file under `<workspace>/.devbench/sessions/<name>/`. The implicit session name when `--name` is omitted is `default`. |
| `marketplace plugin` | two words, lower-case | The `devbench` Claude Code plugin published with a manifest sufficient for discovery via `claude plugin marketplace`. Hosts the four onboarding skills. |
| `skill` | lower-case | A single conversational capability inside the marketplace plugin (e.g., `create-spec`, `spec-to-backlog`, `bootstrap-environment`, `configure-devbench`). |
| `audit comment` | two words, lower-case | A timestamped row appended to a work-unit file's `## Comments` section. Used by classifiers, reports, and the done-gate. Format: `[YYYY-MM-DD HH:MM UTC] [author] [TAG] message`. |

---

## Usage notes

### draft vs. Draft

The status value is always written in lower-case as `draft` (or as a code token
`` `draft` ``). Title-case `Draft` appears only as a column heading in status
tables (e.g., the `Draft` column in `BACKLOG.md` status tables). When referring
to the status in prose, use `draft` or `` `draft` ``.

Correct:
```
The work unit has `draft` status.
Promote it from draft to in-queue with: devbench promote E1-F1-S1-T1
```

Incorrect:
```
The work unit has Draft status.   # Title-case outside a table heading
```

### session vs. session name vs. session id

The canonical term is **session** or **named session**. The session is identified
by a **name** (a short string like `backlog-a-orchestrator`). The term
"session id" is reserved for the `DEVBENCH_ORCHESTRATOR_SESSION_ID` environment
variable (a unique string set at orchestrator launch). Do not use "session name"
and "session id" interchangeably -- they refer to different things:

- **session name** (`--name <value>`) -- the human-readable label used in
  `devbench sessions`, `devbench drain`, and the filesystem layout under
  `<workspace>/.devbench/sessions/<name>/`.
- **session id** (`DEVBENCH_ORCHESTRATOR_SESSION_ID`) -- the runtime
  identifier stamped into every audit log event, used by `hook-tail` for
  filtering and by reports for per-session aggregation.

---

## See also

- [docs/architecture.md -- Section 12: Glossary](architecture.md#12-glossary) -- broader architectural term definitions
- [docs/zero-to-ready.md](zero-to-ready.md) -- operator walkthrough covering draft, scope, drain, and session
- [docs/cli-reference.md](cli-reference.md) -- full CLI reference with all commands and flags
- [docs/onboarding.md](onboarding.md) -- chained-skill workflow using marketplace plugin and skills
- [docs/devbench-yaml-reference.md](devbench-yaml-reference.md) -- configuration reference including `backlog.default_status_for_new_work_units`
