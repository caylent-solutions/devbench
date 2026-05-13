# ADR-22: Unified `devbench upgrade` Command

**Status:** Superseded (2026-05-13)
**Date:** 2026-05-04

> **Superseded by post-v1.0 cleanup (2026-05-13, issue #167).**
>
> The `devbench upgrade` and `devbench migrate-log-shards` commands were
> deleted as part of the v1.0 release cleanup. Issue #162's migration
> phases are now historical: every new workspace ships with the post-#162
> layout from day one; existing workspaces had a one-release window to
> migrate before v1.0 and any holdouts can pin to a pre-cleanup tag.
>
> This ADR is preserved as the historical record of why the unified
> upgrade command existed; the implementation details below no longer
> reflect shipped code.

---

## Context

Issue #162's full rollup (Phases 1+4 cache, Phase 2 window indices,
Phase 3 sharded log + migration, Phase 6 snapshots, Phase 7 Parquet
archive) introduces several new on-disk artefacts. Operators pulling
this update on existing workspaces need a clear, single-command path
to migrate without consulting per-phase documentation.

Without a unified command, an operator pulling the update would have
to: (a) know that the cache self-heals, (b) run
`devbench rebuild-window-stats` separately, (c) decide whether to run
the destructive Phase 3 log migration, (d) understand that Phase 6
snapshots write themselves on next iteration, (e) opt in to Phase 7
Parquet archive separately. Five steps, several with operator
decisions.

## Decision

Ship a single `devbench upgrade` subcommand that orchestrates every
migration. Default behaviour runs the safe migrations automatically
and skips the destructive Phase 3 step unless `--migrate-log-shards`
is passed. Self-tests at the end via the `BacklogParser` load so the
operator sees pass/fail before walking away.

### Per-phase behaviour

| Phase | Default action | Operator action |
|---|---|---|
| 1+4 (cache) | Detect + report state | None; cache self-heals on next read |
| 2 (window indices) | `rebuild_from_log(...)` -- idempotent | None |
| 3 (sharded log) | Print warning + skip | Pass `--migrate-log-shards` to opt in |
| 6 (snapshot) | Detect + report state | None; snapshot self-heals on next iteration |
| 7 (Parquet) | Detect `pyarrow` import; print install hint | `pip install devbench[archive]` if wanted |
| Self-tests | Run `BacklogParser.parse_index()` | None |

Idempotent. Re-running `devbench upgrade` is safe at any cadence.
Every phase's underlying migration is itself idempotent (`rebuild_from_log`
overwrites; `migrate_flat_to_sharded` appends; cache + snapshot self-heal).

Default-deny on the destructive phase. Per CLAUDE.md "execute with
care," the Phase 3 log migration requires explicit
`--migrate-log-shards` confirmation. Without the flag, the upgrade
prints the warning verbatim and continues to subsequent phases.

Self-test failure is non-fatal. The upgrade command's exit code is
0 even when `BacklogParser.parse_index()` fails -- the migrations
themselves succeeded; the parser failure is a structured warning so
the operator knows their backlog needs attention. Returning non-zero
on parser failure would break operators who run `devbench upgrade`
on a workspace whose `BACKLOG.md` is mid-edit.

## Alternatives considered

- **Per-phase commands only.** Already shipped (`rebuild-window-stats`,
  `migrate-log-shards`, etc.). The unified command does NOT replace
  these; it composes them. Operators who want fine-grained control
  still have it.
- **Auto-confirm Phase 3 in upgrade.** Bypasses the destructive-flag
  default-deny. Wrong default; matches CLAUDE.md fail-fast / execute-
  with-care posture.
- **Make upgrade fail fast on self-test failure.** Punishes the common
  case of "operator ran upgrade on a workspace whose backlog has a
  validate-backlog finding pending"; that's a separate user error
  unrelated to migration correctness.

## Consequences

- New CLI subcommand `devbench upgrade [--migrate-log-shards]`.
- Documented in `docs/upgrade-guide.md` (also new in this rollup) as
  the canonical migration entry point.
- The `cmd_upgrade` body is composed from 6 helper functions
  (`_upgrade_report_cache_phase`, etc.) so each phase's behaviour
  reads + tests cleanly.
- Test surface covers: idempotency, default-deny destructive flag,
  destructive-flag-with-flag-runs-migration, self-test pass + fail
  paths, every phase's "absent" and "present" message branches,
  pyarrow-installed and pyarrow-not-installed branches.

## Future cleanup

After devbench's first major release ships, the entire upgrade tool
+ upgrade-guide doc + readme cross-link become legacy infrastructure
(new operators post-1.0 will have no legacy state to migrate). A
post-release tracking issue captures the removal task:

- Drop `cmd_upgrade` + the 6 helper functions
- Drop `cmd_rebuild_window_stats`, `cmd_migrate_log_shards` (no longer
  needed; the layouts are the only ones that ever existed for a
  post-1.0 user)
- Drop `docs/upgrade-guide.md`
- Drop the README cross-link
- Keep `cmd_archive_session` (Phase 7 archive remains useful)

## References

- Issue #162 (parent roadmap)
- ADR-16 (cache), ADR-17 (window indices), ADR-18 (sharded log),
  ADR-19 (SQLite index), ADR-20 (snapshots), ADR-21 (Parquet archive)
  -- the layers this command composes
- `src/devbench/cli.py::cmd_upgrade` + helper functions
- `tests/test_cli.py::TestCmdUpgrade`
- `docs/upgrade-guide.md` (operator-facing complement)
