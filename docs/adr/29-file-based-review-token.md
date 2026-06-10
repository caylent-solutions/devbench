# ADR-29: File-based per-round review token (replaces the env-var transport)

**Status:** Accepted
**Date:** 2026-06-10

---

## Context

The H3 default-deny verdict guard (`guard-verdict-format.sh`) requires a
second factor for every canonical reviewer verdict: a per-round, unit-scoped
token. [ADR-28](28-flatten-review-pipeline.md) hardened the *value* of that
token (it must now be scoped to the unit under review, prefix `<unit-id>-`) but
left its *transport* on the `DEVBENCH_REVIEW_ROUND_TOKEN` environment variable
carried via `shell.env` and `BASH_ENV`.

That transport was never actually implemented in code. No devbench module wrote
the variable; each orchestrator run improvised the injection. It failed twice in
production:

- **Incident 1 -- stale value masked a missing injection.** A leftover token
  sat in `shell.env`. Because `BASH_ENV` sources `shell.env` into every
  non-interactive bash subprocess, the guard always saw a non-empty value and
  could not tell a fresh per-round token from a stale leftover. The round-aware
  unit-id scoping (ADR-28) narrowed but did not close this, because the leftover
  could still belong to the same unit across rounds.
- **Incident 2 -- written where the hook never read it.** A later run wrote the
  token into `.claude/settings.local.json`. The guard reads the process
  environment, not the settings file, so the token was present-but-invisible and
  every canonical verdict was blocked (the done-gate became unsatisfiable).

The shared root cause is that the env-var transport depended on `BASH_ENV`
sourcing a shell profile on every subprocess startup -- a fragile, action-at-a-
distance mechanism with no single deterministic write point the guard could
trust.

---

## Decision

**Move the per-round token off the environment variable entirely and onto a
file under the workspace's `.devbench/` directory.** A new CLI verb manages it:

1. **`devbench review-token new <unit-id>`** writes a fresh
   `<unit-id>-r<n>-<rand>` token to `<workspace>/.devbench/review-round-token`
   (mode `0600`) and prints it. The per-unit round number `<n>` comes from a
   monotonic counter persisted in `<workspace>/.devbench/review-round-counters.json`
   (incremented on each `new` call for that unit); `<rand>` is
   `secrets.token_hex(6)`. The workspace root comes from the CLI's resolved
   `WORKSPACE_ROOT`. It fails fast (rc=1) on a missing or empty unit id.

2. **`devbench review-token clear`** removes the token file (and reports whether
   a file was present).

3. **`guard-verdict-format.sh` reads the file**, not an env var. For any
   canonical reviewer judge it resolves the workspace from the stable
   `DEVBENCH_WORKSPACE_ROOT` (set once by `devbench start`, not per round),
   reads `<workspace>/.devbench/review-round-token`, and requires that the file:
   exists, is non-empty, and is **unit-scoped** (begins with `<unit-id>-`). Any
   of those failing is an exit-2 block. `DEVBENCH_WORKSPACE_ROOT` unset is also a
   fail-closed block (the guard cannot locate the file).

4. **The orchestrate skill** calls `review-token new <unit-id>` at the start of a
   review round (step 5a) and `review-token clear` at the end (step 5d). The same
   round token covers the four `review_team` reviewers (step 5), the step-7
   security reviewer, and the step-7b iac reviewer -- they all run inside one
   round and share one token.

The CLI verb is implemented in `src/devbench/review_token.py` (the
`new_token` / `clear_token` / `read_token` helpers and the `token_path` accessor)
and wired through `cmd_review_token` in `src/devbench/cli.py`.

### Security equivalence

The second factor is unchanged in substance: it is still a per-round,
unit-scoped token that a rogue subagent cannot forge without the orchestrator's
cooperation, and a stale token from a different unit's round still cannot satisfy
this unit's verdict (the unit-id prefix check is preserved). The only thing that
changed is the **transport**: environment variable (sourced via `BASH_ENV`) to a
file under `.devbench/` written at a single deterministic point. This removes the
`BASH_ENV` fragility that caused both incidents -- there is no shell profile to go
stale and no settings file for the value to land in where the guard never looks.

---

## Why this is the right fix

The env-var transport had no deterministic write point: it relied on `BASH_ENV`
re-sourcing `shell.env` on every subprocess, so a stale value and a mis-located
value both looked identical to a fresh, correct one. A file written by an
explicit CLI call has exactly one writer (`review-token new`) and one reader (the
guard), both pinned to the same path, and is cleared at a known point
(`review-token clear`). The mechanism is workspace-relative and backlog-agnostic,
so it works for any devbench workspace without hardcoded paths.

## Rejected alternatives

- **Keep the environment-variable transport.** This is the mechanism that caused
  both production incidents. There is no robust way to distinguish a fresh
  injection from a stale `shell.env` leftover when the value arrives via
  `BASH_ENV`, and nothing prevents a future run from writing the value to a
  location (such as `.claude/settings.local.json`) the guard never reads.
  Rejected as fundamentally fragile.

- **HMAC-signed token scheme.** A cryptographically signed token (the scheme
  ADR-25 deferred) is heavier than the threat requires. The file-based unit-scoped
  token is the proportionate second factor; signing can be revisited if the trust
  boundary changes.
