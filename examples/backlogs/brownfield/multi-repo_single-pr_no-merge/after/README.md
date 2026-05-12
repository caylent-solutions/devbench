# `after/` -- post-run snapshot (Coming Soon)

This folder will hold the post-execution snapshot of the `before/` backlog
once DevBench has been run end-to-end against the three real target repos
and every PR has been merged.

## What will land here

When the run completes, this folder will be populated with:

| Artefact | What it shows |
|---|---|
| `BACKLOG.md` (final) | Every row's Status column flipped to `done` (or `declined` / `materialised-as-proposal` where applicable). Status Summary counts at terminal. |
| `backlog/` (final state) | Each work-unit `.md` file as DevBench left it: TDD Cycle Log populated, judge verdicts logged, comments appended, Status promoted. |
| `kanon/`, `caylent-private-kanon/`, `kanon-claude-marketplaces/` | The post-merge commit SHA of each target repo, as a thin `git-bundle` or a `repo-state.md` file with `git log --oneline` and a link to the merged PR. (Full repo checkouts not included to keep the example folder lightweight.) |
| `run-report.md` | Final `devbench report` snapshot: wall-clock, retry counts per task, judge pass/fail per task, model token usage, dollar cost estimate. |
| `logs/orchestrator.log` (tail) | The closing N events from the orchestrator log -- final mark-done transitions, git-ops-finalize, CI watchers, merge events. |
| `proposals/` | Any task-factory proposals that materialised during the run (out-of-scope work the blocker-resolver decomposed into new draft tasks). |

## Why before + after

A static "validated backlog" is one thing; what readers actually want to know
is **what DevBench produced when it ran**. The before/after pair answers that
without making the reader pull and re-run anything:

- **Did every work unit finish?** Compare `before/BACKLOG.md` to
  `after/BACKLOG.md`.
- **What did the code changes look like?** `after/<repo>/repo-state.md`
  links to the merged PR diff.
- **How many retries per task? Which judges failed first?**
  `after/run-report.md` shows the per-task retry distribution and the most
  frequent rejection categories.
- **What did it cost?** `after/run-report.md` shows model token usage and a
  dollar estimate based on the configured `report.token_cost_per_million_*`
  multipliers.

The `how-it-was-made.md` doc in the parent folder captures the **authoring**
side; this folder will capture the **execution** side.

## When this will land

After the operator runs DevBench against `before/` end-to-end with real
target-repo clones, captures the artefacts above, and copies them here.
Status: pending the operator's first live run.
