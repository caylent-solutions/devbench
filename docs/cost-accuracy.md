# Cost accuracy: per-model attribution chain

Issue #223 introduced per-model cost attribution end-to-end. This page
documents the pipeline so operators can audit why a particular dollar
figure appears in `devbench report` and how to validate it.

## Pipeline

```
Claude Code transcripts (~/.claude/projects/<slug>/*.jsonl)
                |
                | message.model captured at parse time
                v
SQLite cache (.devbench/report-cache/events.sqlite)
  hook_entries.model       TEXT  -- per-invocation model id
  transcript_entries.model TEXT  -- per-message model id
                |
                | aggregate_*_window_by_model() groups by model
                v
report.py::_compute_cost_by_model(totals_by_model)
                |
                | per-model rates from report.models in devbench.yaml
                | fallback to report.default_model for "<unknown>" bucket
                v
CostBreakdown (renderer-facing)
```

## Where to look

- **Raw model ids in the cache** -- query the SQLite directly:
  ```bash
  sqlite3 .devbench/report-cache/events.sqlite \
    "SELECT model, COUNT(*) FROM hook_entries GROUP BY model;"
  sqlite3 .devbench/report-cache/events.sqlite \
    "SELECT model, COUNT(*) FROM transcript_entries GROUP BY model;"
  ```
  Any NULL row aggregates under the sentinel key `"<unknown>"` and is
  priced against `report.default_model`.
- **Per-model rates** -- inspect `backlog/config/devbench.yaml` under
  `report.models`. The runtime view (`devbench.config.REPORT_MODEL_RATES`)
  merges the operator's overrides over `DEFAULT_MODEL_RATES` from
  `src/devbench/constants.py`.
- **Aggregator output** -- run a Python REPL with the workspace env vars
  set and inspect
  `EventIndex.aggregate_hook_window_by_model(hook_log, window_start)`.

## What to do if numbers look wrong

1. Sanity-check the model attribution: `SELECT DISTINCT model FROM
   transcript_entries` should produce the model ids the workspace
   actually ran. If a model is missing, the transcripts may not have
   refreshed yet -- run `devbench report --once` to trigger a
   rebuild-refresh cycle.
2. Confirm the rate table: every observed model id should have a row in
   `report.models`. Anything else falls into `"<unknown>"` priced
   against `report.default_model` (Opus 4.7 list by default, which
   over-reports for cheaper models).
3. If the totals are off by a uniform factor across a window, run
   `devbench cost-calibrate <actual-usd>` to derive per-model
   `correction_factor` values from a real Anthropic invoice. See
   [model-pricing.md](model-pricing.md) for the full workflow.

## Migration from the retired scalar rate config

The legacy `report.token_cost_per_million_input` /
`report.token_cost_per_million_output` / `report.token_cost_discount`
fields were removed in issue #223 (complete-replacement per CLAUDE.md;
no deprecation shim). Workspaces that still set the old keys fail-fast
at config-load time. Migrate using the table in
[model-pricing.md](model-pricing.md#standard-pricing-per-1m-tokens-usd).
