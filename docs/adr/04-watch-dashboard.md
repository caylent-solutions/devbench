# ADR-04: Live activity dashboard (`devbench watch`)

**Status:** Accepted
**Date:** 2026-04-18

---

## Context

An operator running `/devbench:orchestrate` watches the parent Claude Code window freeze for 10+ minutes at a time while a subagent executes. The parent is blocked on an Agent tool call whose internals -- LLM thinking, tool calls, test runs -- are not surfaced in the parent session. To answer "is the orchestrator alive? what is it working on? do I need to intervene?" the operator has to manually correlate four sources: `orchestrator.log`, `hook-logs.jsonl`, the most-recently-modified subagent transcript under `~/.claude/projects/<slug>/subagents/`, and the target repo's git state.

Existing observability surfaces address different questions:

- `devbench report` covers **cross-session history and economics**: velocity, cost, cache hit rate, projected remaining time.
- `devbench status` covers **backlog shape**: how many tasks are in each status.
- Neither one answers **"what is the orchestrator doing *right now*?"**.

## Decision

Add a new read-only CLI command, `devbench watch`, that:

1. **Auto-discovers** the current Claude Code session directory from the most recent `input.transcript_path` in `hook-logs.jsonl`, then picks the newest `subagents/agent-*.jsonl` by mtime.
2. **Parses** five signals -- the active task from `BACKLOG.md`, the latest `text` + recent `tool_use` blocks from the active subagent transcript, recent `devbench.cli` log lines, `git status --porcelain=v1` for the task's repo, and any pending amendment request -- into a typed `ActivitySnapshot` dataclass.
3. **Renders** a one-screen (~40 line) dashboard as a pure function of the snapshot.
4. **Supports `--watch N`** for live refresh, matching the `devbench report --watch N` UX exactly. Without `--watch`, the command prints once and exits.

The collector and renderer live in `src/devbench/activity.py` as pure, independently-testable functions. The CLI entry `src/devbench/cli.py::cmd_watch` is the only part that performs I/O beyond reading local files; the watch loop uses the same `_TERMINAL_CLEAR_CMD` + `time.sleep(interval)` pattern `cmd_report` established.

## Consequences

**Positive.**

- One command replaces a four-source stitch-together. Operators can answer the three active questions from a single terminal invocation.
- Strictly read-only: every subprocess call is a fixed `git` argv with a bounded timeout; every file is opened in read mode; no hook signalling. Concurrent-safe with live orchestrations.
- 100% coverage enforced on `devbench.activity` by extending `test-coverage-new`; every parser and the renderer have unit tests, plus an end-to-end fixture test that subprocess-invokes the CLI.
- The renderer is a pure function of an `ActivitySnapshot`, so new display fields are added by (a) extending the dataclass, (b) adding a parser, (c) adding a renderer panel -- all three are trivially testable.
- Graceful degradation: a missing hook log, missing subagent transcript, non-git repo, or missing amendment file each produces a blank or "no" panel, never a crash.

**Negative.**

- The dashboard is opinionated: the panel set is fixed at ship time. Adding a new signal requires code + tests + docs.
- The subagent-type detection depends on Claude Code's transcript format, which can change across releases. The parser reads multiple alias fields (`subagent_type`, `agent_type`, `agentType`) and tolerates absence, but a major Claude Code format change would require a parser update.
- The `Phase` label is heuristic (`{executor, review-supervisor, ...}` inferred from subagent type with a CLI-message fallback). When the heuristic misses, the phase reads `idle`; the panels above (Latest agent thinking, Recent tool calls) still carry the real signal, so the miss is a cosmetic issue, not a functional one.

## Alternatives considered

- **A shell script that tails four log files.** Rejected: the parsing is non-trivial (transcript JSON, hook JSONL, orchestrator log format), the parsers would drift from Python's `devbench.reporting.report._discover_transcript_dir`, and the test story for bash is weaker. A Python module is DRY with the report module and unit-testable.
- **Embed in `devbench report`**. Rejected: `report` is about history and economics; `watch` is about *now*. Mixing them confuses the reader and bloats both code paths. Two small commands beat one large one.
- **Push-based updates (tail files with inotify)**. Rejected for v1: polling at 3--5s is plenty for the "is it alive" question, and inotify adds a runtime dependency that does not pull its weight for a local-only developer tool.
- **Parse and render full JSONL transcripts**. Rejected: the compact text/tool-use extraction captures the most actionable signal at a fraction of the screen real-estate. Operators who want raw JSONL have `tail -f` and their editor.

## Related files

- Source: `src/devbench/activity.py`, `src/devbench/cli.py` (`cmd_watch`, `_extract_watch_flag`, `_dispatch_watch_commands`).
- Tests: `tests/test_activity.py`, `tests/test_integration/test_watch_against_live_log.py`, `tests/fixtures/activity/*`.
- Docs: `docs/watch-activity.md`.
- Makefile: `watch`, `watch-live` targets; `test-coverage-new` extended to include `devbench.activity`.
