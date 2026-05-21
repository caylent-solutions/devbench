# Model Pricing Reference

Per-model token pricing for the Claude models supported by devbench, with the YAML snippet to drop into your `devbench.yaml` for accurate cost estimates in `devbench report`.

> **Pricing snapshot:** captured from <https://platform.claude.com/docs/en/about-claude/pricing> on **2026-04-16**. Rates and regional / platform-specific premiums change over time. Always verify against the canonical source before relying on these numbers for billing or budgeting decisions. This file is a captured reference, not a live feed.

---

## Table of contents

- [Why these values matter](#why-these-values-matter)
- [How cost is calculated](#how-cost-is-calculated)
- [Standard pricing (per 1M tokens, USD)](#standard-pricing-per-1m-tokens-usd)
- [Picking your defaults](#picking-your-defaults)
- [Caching multipliers and non-Anthropic platforms](#caching-multipliers-and-non-anthropic-platforms)
- [Long context and batch pricing](#long-context-and-batch-pricing)
- [Bedrock and other platforms](#bedrock-and-other-platforms)

---

## Why these values matter

`devbench report` shows estimated session and per-task costs. The estimate is computed per call, per token type, from real `usage` data in `hook-logs.jsonl` and the Claude Code transcript files. You only have to set your model's input/output rates:

```yaml
report:
  token_cost_per_million_input: <your model's input rate>
  token_cost_per_million_output: <your model's output rate>
```

If these don't match the model your orchestrator is actually running (set by `DEVBENCH_CLAUDE_MODEL`), the cost numbers will be off by the ratio of real-vs-configured rates. Pick the row in the table below that matches your model, then drop the snippet from [Picking your defaults](#picking-your-defaults) into your config.

---

## How cost is calculated

Every LLM call in the hook log and the outer-session transcript is costed individually from its own `usage` block:

```
call_cost =   usage.input_tokens        × input_rate
            + usage.output_tokens       × output_rate
            + usage.cache_read_tokens   × input_rate × 0.10
            + cache_write_5m_tokens     × input_rate × 1.25
            + cache_write_1h_tokens     × input_rate × 2.00
```

Cache-write tokens are read from the nested `usage.cache_creation.ephemeral_5m_input_tokens` / `ephemeral_1h_input_tokens` fields; older `cache_creation_input_tokens` values are counted as 5-minute writes.

**No blended-rate fallback.** If an entry lacks a `usage` block (a non-LLM tool call like Read/Bash, or a legacy record), it contributes zero cost. The duration is still counted for API-utilization metrics, but the token cost stays at zero rather than being filled in from a blended estimate. This is the fail-fast posture -- missing cost data surfaces as a visibly-low number rather than a masked estimate.

**No estimated input/output ratio.** The `Input / output share (measured)` row in the TOKENS section of the consolidated report is purely descriptive -- it shows what your actual workload ratio works out to from real data. It is never used as an input to the cost formula.

---

## Calibrating cost rates against actual billing

The reported cost should closely match actual API billing when the configured rates match your model and platform. If the report drifts noticeably from the actual invoice (model swap, 1M-context premium tier, contract pricing, region surcharge, or any other rate variant), run `devbench cost-calibrate`:

```bash
uv run devbench cost-calibrate <actual-usd> [--window <ISO-8601>]
```

The command sums devbench's reported cost across every model observed in the window, derives `correction_factor = actual_usd / reported_total`, and writes the factor back to `report.models.<id>.correction_factor` in `backlog/config/devbench.yaml` for every model that contributed to the window. The next `devbench report` reflects the corrected total without further operator action.

**Worked example.** A live workspace running Opus 4.7 with 1M-context observed actual API spend of `$83.66` against `devbench report` reading `$39.57` for the same window:

```bash
uv run devbench cost-calibrate 83.66 --window 2026-05-01T00:00:00Z
```

The command writes `correction_factor = 83.66 / 39.57 = 2.114` to `report.models.claude-opus-4-7.correction_factor` (and any other model that contributed). Re-run `devbench report` on the same window and the new value matches the invoice within rounding.

**When to recalibrate.** Re-run `devbench cost-calibrate` whenever any of these change: model routing in `agents:`, the workspace's context-tier (200k vs 1M), Anthropic's published list pricing, or your contract terms. Successive calibrations replace (not multiply) the prior `correction_factor` so re-running is idempotent against a fixed actual-spend figure.

---

## Per-model pricing config (issue #223)

`devbench` prices each transcript message at the rate of the model that produced it. The pricing table lives under `report.models` in `backlog/config/devbench.yaml`; each key is the literal model id Claude Code writes on every `assistant` message envelope. Operators add new model ids without code changes -- the schema's `additionalProperties` validation accepts any string at the model-id slot.

```yaml
report:
  models:
    claude-opus-4-7:
      input: 5.0
      output: 25.0
    claude-sonnet-4-6:
      input: 3.0
      output: 15.0
  default_model:    # applied to any model id NOT listed above
    input: 5.0
    output: 25.0
```

Per-model fields:

- `input` (required) -- cost per 1M input tokens, USD.
- `output` (required) -- cost per 1M output tokens, USD.
- `cache_read_multiplier` (optional) -- overrides the top-level `report.cache_read_multiplier` for this model only.
- `cache_write_5min_multiplier` (optional) -- ditto for 5-min cache write.
- `cache_write_1hr_multiplier` (optional) -- ditto for 1-hr cache write.
- `correction_factor` (optional) -- per-model contract correction; defaults to 1.0. Computed cost is multiplied by this value after every other factor. Set it via `devbench cost-calibrate <actual-usd>` rather than hand-editing.

### Calibrating against an Anthropic invoice (`devbench cost-calibrate`)

When the reported cost in `devbench report` drifts from the actual Anthropic invoice (model swap, 1M-context premium tier, non-list contract pricing), run:

```bash
uv run devbench cost-calibrate <actual-usd> [--window <ISO-8601>]
```

The command sums devbench's reported cost across every model observed in the window, derives `correction_factor = actual_usd / reported_total`, and writes the factor back to `report.models.<id>.correction_factor` in `backlog/config/devbench.yaml` for every model that contributed to the window. The next `devbench report` reflects the corrected total without further operator action.

If no `--window` is supplied the helper uses every event in the cache (`window_start = 1970-01-01`). Operators with a recent Anthropic invoice typically scope the window to the invoice period.

### Migration from the retired scalar fields

The legacy fields `report.token_cost_per_million_input`, `report.token_cost_per_million_output`, and `report.token_cost_discount` were retired in issue #223. Workspaces that still set them fail-fast at config-load time with a message naming the offending fields and pointing at this document. To migrate:

1. Replace the three scalar keys with a `report.models` block (use the table below as the canonical default).
2. If you had a non-zero `token_cost_discount`, express it as a per-model `correction_factor` via `report.models.<id>.correction_factor = 1.0 - <old_discount>`. Or run `devbench cost-calibrate <actual-usd>` once to write the corrected factor for every model observed in the current cache.

## Standard pricing (per 1M tokens, USD)

| Model            | Input | Output | Cache read | 5-min cache write | 1-hr cache write |
| ---------------- | ----- | ------ | ---------- | ----------------- | ---------------- |
| Claude Opus 4.7  | $5    | $25    | $0.50      | $6.25             | $10              |
| Claude Opus 4.6  | $5    | $25    | $0.50      | $6.25             | $10              |
| Claude Opus 4.5  | $5    | $25    | $0.50      | $6.25             | $10              |
| Claude Opus 4.1  | $15   | $75    | $1.50      | $18.75            | $30              |
| Claude Opus 4    | $15   | $75    | $1.50      | $18.75            | $30              |
| Claude Sonnet 4.6 | $3   | $15    | $0.30      | $3.75             | $6               |
| Claude Sonnet 4.5 | $3   | $15    | $0.30      | $3.75             | $6               |
| Claude Sonnet 4   | $3   | $15    | $0.30      | $3.75             | $6               |
| Claude Haiku 4.5  | $1   | $5     | $0.10      | $1.25             | $2               |
| Claude Haiku 3.5  | $0.80 | $4    | $0.08      | $1                | $1.60            |
| Claude Haiku 3    | $0.25 | $1.25 | $0.03      | $0.30             | $0.50            |

> Opus 4.7 introduced a new tokenizer that may use significantly more tokens for the same fixed text compared to earlier models -- factor this into cost projections when migrating between Opus generations.

The cache columns are derived from the input rate via the standard Anthropic multipliers (0.10x for reads, 1.25x for 5-min writes, 2.0x for 1-hr writes) and are applied automatically by `devbench report` -- you do not need to set them unless your deployment platform uses different multipliers.

---

## Picking your defaults

Drop the matching block into the `report:` section of `backlog/config/devbench.yaml`. Input and output rates are mandatory; cache multipliers default to Anthropic's published values and only need overriding on non-Anthropic platforms.

### Opus 4.7 / 4.6 / 4.5

```yaml
report:
  models:
    claude-opus-4-7:
      input: 5.0
      output: 25.0
    claude-opus-4-6:
      input: 5.0
      output: 25.0
    claude-opus-4-5:
      input: 5.0
      output: 25.0
```

### Opus 4.1 / 4

```yaml
report:
  models:
    claude-opus-4-1:
      input: 15.0
      output: 75.0
    claude-opus-4:
      input: 15.0
      output: 75.0
```

### Sonnet 4.6 / 4.5 / 4

```yaml
report:
  models:
    claude-sonnet-4-6:
      input: 3.0
      output: 15.0
```

### Haiku 4.5

```yaml
report:
  models:
    claude-haiku-4-5:
      input: 1.0
      output: 5.0
```

### Haiku 3.5

```yaml
report:
  models:
    claude-haiku-3-5:
      input: 0.80
      output: 4.0
```

### Mixed-model setups

If your orchestrator uses different models for different roles (for example, Opus for the executor and Sonnet for the judges via the `agents:` block in `devbench.yaml`; see [ADR-25](adr/25-per-agent-model-overrides.md)), populate `report.models` with rates for **every** model the workspace runs. `devbench report` prices each transcript message at the rate of its actual `message.model`, so a mixed-model run no longer requires picking a single representative rate -- the per-model attribution lands in the SQL index and rolls up to the aggregate cost row. The orthogonal per-role view (issue #206) is rendered behind `--by-role`; both axes (role and model) are now available independently.

---

## Caching multipliers and non-Anthropic platforms

All cost multipliers are overridable in the `report:` block. The defaults match Anthropic's published rates; override them only when running on a platform whose pricing differs:

```yaml
report:
  models:
    claude-opus-4-7:
      input: 5.0
      output: 25.0
  cache_read_multiplier: 0.10           # default: 0.10 (Anthropic)
  cache_write_5min_multiplier: 1.25     # default: 1.25 (Anthropic)
  cache_write_1hr_multiplier: 2.0       # default: 2.0  (Anthropic)
  data_residency_multiplier: 1.10       # default: 1.10 (US-only inference)
```

Each multiplier is defined in `src/devbench/constants.py` and can also be set per-invocation via environment variables:

| YAML key                      | Env var                                     | Default |
| ----------------------------- | ------------------------------------------- | ------- |
| `cache_read_multiplier`       | `DEVBENCH_REPORT_CACHE_READ_MULTIPLIER`        | 0.10    |
| `cache_write_5min_multiplier` | `DEVBENCH_REPORT_CACHE_WRITE_5MIN_MULTIPLIER`  | 1.25    |
| `cache_write_1hr_multiplier`  | `DEVBENCH_REPORT_CACHE_WRITE_1HR_MULTIPLIER`   | 2.0     |
| `data_residency_multiplier`   | `DEVBENCH_REPORT_DATA_RESIDENCY_MULTIPLIER`    | 1.10    |

Resolution order is env var > YAML value > constant default.

---

## Current code defaults

`src/devbench/constants.py` defines:

```python
DEFAULT_MODEL_RATES: dict[str, ModelRates] = { ... }  # per-model table; see Standard pricing above
DEFAULT_FALLBACK_MODEL_RATES: ModelRates = ModelRates(input=5.0, output=25.0)  # "<unknown>" bucket
DEFAULT_CACHE_READ_MULTIPLIER: float = 0.10
DEFAULT_CACHE_WRITE_5MIN_MULTIPLIER: float = 1.25
DEFAULT_CACHE_WRITE_1HR_MULTIPLIER: float = 2.0
DEFAULT_DATA_RESIDENCY_MULTIPLIER: float = 1.10
```

The per-model table is lifted verbatim from the Standard pricing block above. Operators do not need to override anything when running on standard Anthropic pricing; the `report.models` block is the place to override when running on Bedrock, a contract rate, or a newly released model that devbench does not yet know about.

### Other settings under `report:`

The `report:` section also accepts a `display_timezone` field (IANA zone name) to control which timezone the report renders timestamps in. **Prefer the top-level `display_timezone:` key** (see [Display timezone](#display-timezone) below) for consistency with `devbench hook-tail` and other timestamp-rendering commands. The report-specific `report.display_timezone:` is retained as a higher-priority override for the report command only.

```yaml
report:
  models:
    claude-opus-4-7:
      input: 5.0
      output: 25.0
  display_timezone: America/Denver   # optional report-specific override; defaults to top-level display_timezone, then system local TZ
```

When unset (or set to a name that isn't a valid IANA zone), the report falls back to the top-level `display_timezone`, then to the host's system local timezone. Override per-invocation via `DEVBENCH_REPORT_TIMEZONE=<zone>`.

---

## Discount / correction factor off list pricing

If your organisation negotiated a flat contract reduction on token pricing (AWS EDP, Azure EA, Anthropic enterprise, etc.), set `report.token_cost_discount` to the **fraction reduced from list price**. Every per-call calculated cost and every ETA-projected total-cost value in `devbench report` is reduced by that fraction before display, so the dollar figures match what finance reconciles.

The value is a discount (reduction), sometimes also called a correction factor. Semantics: `final_cost = raw_list_cost x (1 - token_cost_discount)`. Specify the **discount itself**, not the pay-rate. If you only know the fraction you pay, convert first: `discount = 1 - pay_rate`.

| Discount                    | Fraction paid     | Percent off list       |
|-----------------------------|-------------------|------------------------|
| `0.0` (default)             | `1.0`             | 0% (pay full list)     |
| `0.25`                      | `0.75`            | 25%                    |
| `0.40363636364`             | `0.59636363636`   | 40.3636364%            |
| `0.5`                       | `0.5`             | 50%                    |
| `1.0`                       | `0.0`             | 100% (free, rare)      |

```yaml
report:
  # 40.3636364% off list  (equivalently: pay 59.6363636% of list)
  token_cost_discount: 0.40363636364
```

Override via env: `DEVBENCH_REPORT_TOKEN_COST_DISCOUNT=0.40363636364`. Default: `0.0`.

Applies uniformly to input, output, cache reads, and cache writes (5-min and 1-hr). Cache multipliers stay as pure ratios; the discount is applied at the base input/output rate before cache multipliers evaluate.

---

## Display timezone

The top-level `display_timezone:` yaml key applies to **every devbench command that renders timestamps** -- `devbench report`, `devbench hook-tail`, `devbench watch`, and any future command. When unset, each command defaults to the host's OS local timezone.

```yaml
# Top level (preferred): applies to every timestamp-rendering command.
display_timezone: America/New_York
```

Override per-invocation via the `DEVBENCH_DISPLAY_TIMEZONE` env var. Per-command overrides still apply on top of this global:

- `devbench report` reads `report.display_timezone` (yaml) or `DEVBENCH_REPORT_TIMEZONE` (env) first, then falls back to `display_timezone` / `DEVBENCH_DISPLAY_TIMEZONE`.
- `devbench hook-tail` reads the CLI `--tz <zone>` flag first, then falls back to `display_timezone` / `DEVBENCH_DISPLAY_TIMEZONE`.

Resolution order (per command): CLI flag or command-specific override > `DEVBENCH_DISPLAY_TIMEZONE` env > top-level `display_timezone` yaml > OS local.

---

## Long context and batch pricing

Claude Opus 4.7, Opus 4.6, and Sonnet 4.6 support a **1M-token context window** at the same per-token rate as smaller requests -- there is no long-context premium for these models on the Claude API.

The **Batch API** offers 50% off input and output for asynchronous workloads (devbench does not currently use the Batch API; the orchestrator runs interactive sessions).

For full pricing details, multipliers, and platform-specific rates (Bedrock, Vertex AI, Foundry), see <https://platform.claude.com/docs/en/about-claude/pricing>.

---

## How `Estimated total cost at completion` is computed (issue #164)

The number is a **global** measure -- one finishing point for the backlog -- and renders as a single value spanning every column of the cost section, not one number per window.

Formula:

```
est_total_cost = cost.total_cost + recent_per_task_cost * eta_task_count
```

Where:

- `cost.total_cost` is the cumulative spend in the report window (per-window).
- `recent_per_task_cost` is the **global** average cost across the most-recent `RECENT_PACE_TASKS` (default 10) completions log-wide. Computed once per report invocation by walking the umbrella interval `[earliest_progress_of_recent_N, now]`, summing hook + transcript token costs across that interval, and dividing by N.
- `eta_task_count` is the same denominator the time-projection uses (active + auto-recovery + auto-clearing buckets).

When the log has fewer than `RECENT_PACE_TASKS` completions, `recent_per_task_cost` is `None` and the calculation falls back to the per-window average (`cost.total_cost / tasks_in_window`), matching the existing `recent_pace_minutes` fallback contract. When there are zero completions log-wide the projection equals the cumulative spend (no synthetic projection from no data).

Why the global rate. Earlier behaviour divided `cost.total_cost / tasks_in_window` (where `tasks_in_window` is the per-window completion count). Narrower windows had fewer completions, so the per-task cost spiked, so the projection inflated; on the same physical workspace the All-time / Session / This-run columns produced wildly different completion costs -- e.g. `~$13k` / `~$42k` / `~$8`. Using a single global rate produces one number that matches the operator's mental model: completion is a single global event with a single cost.

---

## Bedrock and other platforms

Pricing on AWS Bedrock, Google Vertex AI, and Microsoft Foundry is set by the platform vendor, not Anthropic. The standard Anthropic rates above are a reasonable starting estimate but verify against your platform's pricing page:

- AWS Bedrock -- <https://aws.amazon.com/bedrock/pricing/>
- Google Vertex AI -- <https://cloud.google.com/vertex-ai/generative-ai/pricing#claude-models>
- Microsoft Foundry -- <https://azure.microsoft.com/en-us/pricing/details/microsoft-foundry/>

Starting with Sonnet 4.5 and Haiku 4.5, regional and multi-region endpoints on Bedrock and Vertex AI carry a 10% premium over global endpoints. Override `report.data_residency_multiplier` in your YAML if you want the estimator to apply this premium.
