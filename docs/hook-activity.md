# Live Hook Stream (`devbench hook-tail`)

`devbench hook-tail` is a pretty-printed, real-time tail of the plugin's
`hook-logs.jsonl` stream. Every PreToolUse / PostToolUse / UserPromptSubmit /
Stop / SubagentStart / SubagentStop / PreCompact / PermissionRequest /
Notification event that the plugin emits lands in that file, one JSON record
per line. This command converts those records into a compact colorized
summary so an operator can watch an orchestration run event-by-event without
tailing the raw JSON.

Complementary to `devbench watch` (which renders a *snapshot* of current
state every N seconds). `hook-tail` is append-only -- each new event prints
one line at the bottom; history never redraws.

## Usage

```bash
# Tail the default workspace hook log ($JUDGE_WORKSPACE_ROOT/hook-logs.jsonl).
devbench hook-tail

# Tail a specific file.
devbench hook-tail /path/to/other-hook-logs.jsonl

# Override the display timezone (defaults to the OS local timezone).
devbench hook-tail --tz America/New_York
devbench hook-tail --tz UTC
devbench hook-tail --tz Europe/London

# Print existing entries before starting to follow (default seeks to EOF).
devbench hook-tail --from-start

# Format existing entries then exit; no follow. Useful for piping.
devbench hook-tail --no-follow --from-start | grep executor
```

`NO_COLOR=1` in the environment disables ANSI color. Color is also
automatically disabled when stdout is not a TTY, so pipes stay clean.

## Output format

```
# devbench hook-tail: /workspace/hook-logs.jsonl
# timestamps rendered in America/New_York (EDT); raw log stores UTC
23:57:41 <- review-super Bash     Count tests in integration test file  |  11
23:57:43 -> review-super Bash     Count tests in functional test file
23:57:43 <- review-super Bash     Count tests in functional test file   |  14
23:58:16 <- review-super Bash     Check test coverage                   |  ========= 21 passed in 27.93s =========
```

Columns left-to-right:

| Column | Width | Content |
|--------|------:|---------|
| timestamp | 8 | `HH:MM:SS` in the display timezone |
| event | 2 | two-char event glyph (see legend below) |
| agent | 12 | `devbench:` prefix stripped; padded / truncated |
| tool | 8 | `Bash`, `Read`, `Edit`, `Write`, `Skill`, `Agent`, etc. |
| description | ≤100 | `tool_input.description` > `.command` > `.file_path` > JSON-encoded |
| stdout preview | ≤80 | last non-empty line of `tool_response.stdout` (PostToolUse only) |

The stdout preview column is rendered only when the entry has a non-empty
`tool_response.stdout`; PreToolUse / UserPromptSubmit / Stop rows omit it
entirely.

### Event legend

| Glyph | Event | Meaning |
|-------|-------|---------|
| `->` | PreToolUse | Tool call starting |
| `<-` | PostToolUse | Tool call finished |
| `!!` | PostToolUseFailure | Tool call raised |
| `U>` | UserPromptSubmit | User (or orchestrator) sent a prompt |
| `\|\|` | Stop | Agent turn ended |
| `+s` | SubagentStart | A subagent (executor / reviewer / etc.) began |
| `-s` | SubagentStop | A subagent returned |
| `Cp` | PreCompact | Context compaction about to happen |
| `P?` | PermissionRequest | Permission prompt shown |
| `No` | Notification | Plugin notification |

Unknown events render the first two characters of the event name.

## Timezones

The raw log always stores UTC (the hook-logger writes timestamps with a `Z`
suffix). `hook-tail` converts at display time.

- **Default:** OS local timezone, resolved via `datetime.now().astimezone()`.
- **Workspace-level override:** set `display_timezone:` at the top level of
  `backlog/config/devbench.yaml` (IANA name), or export
  `JUDGE_DISPLAY_TIMEZONE=<iana-name>`. Applies to every timestamp-rendering
  command (`report`, `hook-tail`, `watch`, future commands).
- **Per-invocation override:** `--tz <iana-name>` on the `hook-tail` command
  wins over both of the above. Any IANA zoneinfo name works, e.g.
  `America/Los_Angeles`, `Europe/London`, `Asia/Tokyo`, `UTC`.
- **Invalid zone:** exits 2 with a stderr message naming the offending zone.

The header line always names the active zone so a shared terminal log is
self-describing:

```
# timestamps rendered in America/New_York (EDT); raw log stores UTC
```

DST abbreviations (EDT vs EST, PDT vs PST, BST vs GMT, etc.) are resolved
from the current moment, so runs straddling a DST boundary show the right
label for each rendering moment.

## Example scenarios

**Watching an orchestration live in the local timezone.** No flags:

```bash
devbench hook-tail
```

**Piping to grep for a specific agent or tool.** Color auto-disables on
non-TTY stdout so the pipe stays clean:

```bash
devbench hook-tail | grep executor
devbench hook-tail | grep -E "^[0-9:]+ (->|<-) review-super"
```

**Replaying the whole run.** `--from-start` includes history; `--no-follow`
exits at end-of-file instead of blocking:

```bash
devbench hook-tail --no-follow --from-start > run.log
```

**Parallel to `devbench watch`.** Open two terminals:

- Terminal A: `devbench watch --watch 5` -- current state snapshot every 5s.
- Terminal B: `devbench hook-tail` -- append-only event stream.

The two answer different questions: "what is the orchestrator doing
*right now*?" versus "what happened *as it happened*?".

## What the command intentionally does NOT do

- **No mutations.** Read-only by construction. Safe to run concurrently
  with `/devbench:orchestrate` or any other devbench command.
- **No filtering flags.** Pipe to `grep` or `jq` for filtering. A `--filter`
  flag can land in a follow-up if the operator reaches for it often.
- **No snapshot mode.** For current-state snapshots use `devbench watch`.
  `hook-tail` is specifically an append-only event stream.
- **No log rotation / archival.** That's the plugin hook-logger's job.
  `hook-tail` does detect rotation (inode change) and reopens automatically.

## Diagnosing common patterns

| Symptom | Likely cause | Where to look |
|---------|--------------|---------------|
| No rows ever appear | File hasn't been written to since the command started; default is seek-to-EOF | Add `--from-start` to include history |
| Row says `!? bad-json` with a raw prefix | One JSONL line is malformed (truncated write, disk full) | `grep -n 'bad-json'` the raw log; the line number matches the source log |
| `--:--:--` sentinel in the timestamp column | The `timestamp` field was missing or non-parsable | Re-check the hook-logger's timestamp format |
| Command exits 1 with "file not found" | `--no-follow` mode and the target path does not exist | Without `--no-follow`, the command polls until the file appears |
| Command exits 2 with "unknown timezone" | The `--tz` value is not a valid IANA zoneinfo name | `timedatectl list-timezones` to list valid names |

## Related files

- Source: `src/devbench/hook_tail.py`, `src/devbench/cli.py::cmd_hook_tail`.
- Tests: `tests/unit/test_hook_tail.py`, `tests/test_integration/test_hook_tail_lifecycle.py`, `tests/test_cli.py::TestCmdHookTail`.
- Complementary commands: [`devbench watch`](watch-activity.md), [`devbench report`](architecture.md).
- Hook-logger that writes the stream: `plugin/devbench/scripts/hook-logger.sh`.
- Stop-hook block diagnostics: `<workspace>/.devbench/stop-hook-diag/<ts>-<task-id>.json` -- one file per `continue-orchestration.sh` block event, capturing the exact JSON payload emitted to Claude Code plus circuit-breaker counters. Read these after a hang to confirm the hook's block response was well-formed.
