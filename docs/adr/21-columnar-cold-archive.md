# ADR-21: Columnar Cold-Archive (Parquet, Opt-In)

**Status:** Accepted
**Date:** 2026-05-04

---

## Context

Issue #162 Phase 7. Long-running orchestrations accumulate large flat
JSONL logs (50+ MB). Operators who want to retain ended sessions for
audit / forensics / cross-session reporting need a denser format
than the live JSONL the orchestrator appends to.

This phase adds a per-session columnar archive at
`<workspace>/logs/legacy/<session-id>.parquet`, generated on demand
via `devbench archive-session <session-id>`. The active session keeps
its hot JSONL file; ended sessions can be archived for cold storage.

## Decision

`pyarrow` is the only new dependency. It is **opt-in** -- mainline
`pip install devbench` carries zero new mandatory dependencies.
Operators install via:

    pip install devbench[archive]

When `pyarrow` is missing, every public function in
`devbench.reporting.archive` raises a structured
`ArchiveDependencyMissing` error whose message names the install
command verbatim. Per CLAUDE.md fail-fast: no silent fallback.

The Parquet schema is intentionally minimal:

| Column | Type | Notes |
|---|---|---|
| `raw_line` | string | The original JSONL line, byte-faithful |
| `parsed_json` | string | The parsed JSON payload, or "" for non-JSON lines |

Storing the raw line guarantees round-trip parity. Storing the parsed
payload separately lets future queries skip the re-parse step. The
JSONL log remains authoritative; the archive is a derived view that
can be deleted at any time without losing data (the source line is
still in the live or rotated log).

## Alternatives considered

- **Make `pyarrow` mandatory.** Operators who never archive would pay
  the install cost (~50 MB) for no benefit. Opt-in is cleaner.
- **Use `gzip`-compressed JSONL.** Lighter dependency footprint
  (stdlib) but loses the columnar query benefit; readers still
  iterate line-by-line.
- **Replace the JSONL log entirely with Parquet.** Loses the audit-
  friendly append-only contract that downstream tools (`tail -f`,
  `grep`, etc.) depend on. Append-only JSONL stays the source of
  truth.

## Consequences

- Operators who care about long-term retention install one extra and
  run `devbench archive-session <id>` per ended session. No automatic
  archiving; opt-in by command invocation.
- New disk artefact `<workspace>/logs/legacy/<session-id>.parquet`.
  Deletion is always safe -- the source JSONL still holds the events.
- New module `devbench.reporting.archive` joins the coverage gate at
  100% line + branch.
- The `[archive]` extra in `pyproject.toml` is the only declarative
  signal an operator needs to opt in. The dev-group install pulls
  pyarrow in so the test suite can pin parity contracts.

## References

- Issue #162 (parent roadmap)
- `src/devbench/reporting/archive.py`
- `src/devbench/cli.py::cmd_archive_session`
- `tests/test_reporting/test_archive.py`
- `pyproject.toml` `[project.optional-dependencies] archive`
