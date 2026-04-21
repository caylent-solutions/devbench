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

If these don't match the model your orchestrator is actually running (set by `JUDGE_CLAUDE_MODEL`), the cost numbers will be off by the ratio of real-vs-configured rates. Pick the row in the table below that matches your model, then drop the snippet from [Picking your defaults](#picking-your-defaults) into your config.

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

> Opus 4.7 introduced a new tokenizer that may use up to ~35% more tokens for the same fixed text compared to earlier models -- factor this into cost projections when migrating between Opus generations.

The cache columns are derived from the input rate via the standard Anthropic multipliers (0.10x for reads, 1.25x for 5-min writes, 2.0x for 1-hr writes) and are applied automatically by `devbench report` -- you do not need to set them unless your deployment platform uses different multipliers.

---

## Picking your defaults

Drop the matching block into the `report:` section of `backlog/config/devbench.yaml`. Input and output rates are mandatory; cache multipliers default to Anthropic's published values and only need overriding on non-Anthropic platforms.

### Opus 4.7 / 4.6 / 4.5

```yaml
report:
  token_cost_per_million_input: 5.0
  token_cost_per_million_output: 25.0
```

### Opus 4.1 / 4

```yaml
report:
  token_cost_per_million_input: 15.0
  token_cost_per_million_output: 75.0
```

### Sonnet 4.6 / 4.5 / 4

```yaml
report:
  token_cost_per_million_input: 3.0
  token_cost_per_million_output: 15.0
```

### Haiku 4.5

```yaml
report:
  token_cost_per_million_input: 1.0
  token_cost_per_million_output: 5.0
```

### Haiku 3.5

```yaml
report:
  token_cost_per_million_input: 0.80
  token_cost_per_million_output: 4.0
```

### Mixed-model setups

If your orchestrator uses different models for different roles (for example, Opus for executor and Sonnet for judges via `executor_model` / `judge_model` in `devbench.yaml`), pick the rate of the model that consumes the most tokens -- usually the executor -- for the most accurate single-figure estimate. There is no per-role cost split in `devbench report` yet (see [Current gaps](architecture.md#section-10--current-gaps-known-limitations) in the architecture doc).

---

## Caching multipliers and non-Anthropic platforms

All cost multipliers are overridable in the `report:` block. The defaults match Anthropic's published rates; override them only when running on a platform whose pricing differs:

```yaml
report:
  token_cost_per_million_input: 5.0
  token_cost_per_million_output: 25.0
  cache_read_multiplier: 0.10           # default: 0.10 (Anthropic)
  cache_write_5min_multiplier: 1.25     # default: 1.25 (Anthropic)
  cache_write_1hr_multiplier: 2.0       # default: 2.0  (Anthropic)
  data_residency_multiplier: 1.10       # default: 1.10 (US-only inference)
```

Each multiplier is defined in `src/devbench/constants.py` and can also be set per-invocation via environment variables:

| YAML key                      | Env var                                     | Default |
| ----------------------------- | ------------------------------------------- | ------- |
| `cache_read_multiplier`       | `JUDGE_REPORT_CACHE_READ_MULTIPLIER`        | 0.10    |
| `cache_write_5min_multiplier` | `JUDGE_REPORT_CACHE_WRITE_5MIN_MULTIPLIER`  | 1.25    |
| `cache_write_1hr_multiplier`  | `JUDGE_REPORT_CACHE_WRITE_1HR_MULTIPLIER`   | 2.0     |
| `data_residency_multiplier`   | `JUDGE_REPORT_DATA_RESIDENCY_MULTIPLIER`    | 1.10    |

Resolution order is env var > YAML value > constant default.

---

## Current code defaults

`src/devbench/constants.py` defines:

```python
DEFAULT_TOKEN_COST_PER_M_INPUT: float = 5.0
DEFAULT_TOKEN_COST_PER_M_OUTPUT: float = 25.0
DEFAULT_CACHE_READ_MULTIPLIER: float = 0.10
DEFAULT_CACHE_WRITE_5MIN_MULTIPLIER: float = 1.25
DEFAULT_CACHE_WRITE_1HR_MULTIPLIER: float = 2.0
DEFAULT_DATA_RESIDENCY_MULTIPLIER: float = 1.10
```

These reflect current **Opus 4.7** pricing and Anthropic's published cache/data-residency multipliers. If you run a different model -- Sonnet, Haiku, or any older Opus generation -- set the `report:` values explicitly in your `devbench.yaml` using the table above so `devbench report` produces accurate cost estimates for your model.

### Other settings under `report:`

The `report:` section also accepts a `display_timezone` field (IANA zone name) to control which timezone the report renders timestamps in:

```yaml
report:
  token_cost_per_million_input: 5.0
  token_cost_per_million_output: 25.0
  display_timezone: America/Denver   # optional; defaults to system local TZ
```

When unset (or set to a name that isn't a valid IANA zone), the report falls back to the host's system local timezone. Override per-invocation via `JUDGE_REPORT_TIMEZONE=<zone>`. This setting is useful when running devbench inside a devcontainer or VM whose system clock is UTC but you want to read timestamps in your actual location's TZ.

---

## Long context and batch pricing

Claude Opus 4.7, Opus 4.6, and Sonnet 4.6 support a **1M-token context window** at the same per-token rate as smaller requests -- there is no long-context premium for these models on the Claude API.

The **Batch API** offers 50% off input and output for asynchronous workloads (devbench does not currently use the Batch API; the orchestrator runs interactive sessions).

For full pricing details, multipliers, and platform-specific rates (Bedrock, Vertex AI, Foundry), see <https://platform.claude.com/docs/en/about-claude/pricing>.

---

## Bedrock and other platforms

Pricing on AWS Bedrock, Google Vertex AI, and Microsoft Foundry is set by the platform vendor, not Anthropic. The standard Anthropic rates above are a reasonable starting estimate but verify against your platform's pricing page:

- AWS Bedrock -- <https://aws.amazon.com/bedrock/pricing/>
- Google Vertex AI -- <https://cloud.google.com/vertex-ai/generative-ai/pricing#claude-models>
- Microsoft Foundry -- <https://azure.microsoft.com/en-us/pricing/details/microsoft-foundry/>

Starting with Sonnet 4.5 and Haiku 4.5, regional and multi-region endpoints on Bedrock and Vertex AI carry a 10% premium over global endpoints. Override `report.data_residency_multiplier` in your YAML if you want the estimator to apply this premium.
