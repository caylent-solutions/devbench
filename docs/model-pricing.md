# Model Pricing Reference

Per-model token pricing for the Claude models supported by devbench, with the YAML snippet to drop into your `devbench.yaml` for accurate cost estimates in `devbench report`.

> **Pricing snapshot:** captured from <https://platform.claude.com/docs/en/about-claude/pricing> on **2026-04-16**. Rates change. Verify against the canonical source before relying on cost estimates for budgeting.

---

## Table of contents

- [Why these values matter](#why-these-values-matter)
- [Standard pricing (per 1M tokens, USD)](#standard-pricing-per-1m-tokens-usd)
- [Picking your defaults](#picking-your-defaults)
- [What `token_cost_input_ratio` means](#what-token_cost_input_ratio-means)
- [Current code defaults vs published rates](#current-code-defaults-vs-published-rates)
- [Long context, batch, and cache pricing](#long-context-batch-and-cache-pricing)
- [Bedrock and other platforms](#bedrock-and-other-platforms)

---

## Why these values matter

`devbench report` shows estimated session and per-task costs. The estimate is a blended rate computed from three values in the `report:` section of `backlog/config/devbench.yaml`:

```yaml
report:
  token_cost_per_million_input: <your model's input rate>
  token_cost_per_million_output: <your model's output rate>
  token_cost_input_ratio: 0.80   # assumed share of tokens that are input
```

If these values don't match the model your orchestrator is actually running (set by `JUDGE_CLAUDE_MODEL`), the cost numbers are wrong. Pick the row in the table below that matches your model, then drop the snippet from [Picking your defaults](#picking-your-defaults) into your config.

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

> Opus 4.7 introduced a new tokenizer that may use up to ~35% more tokens for the same fixed text compared to earlier models — factor this into cost projections when migrating between Opus generations.

---

## Picking your defaults

Drop the matching block into the `report:` section of `backlog/config/devbench.yaml`. Only the input and output rates are used by the cost estimator today; cache and batch rates are documented here for budgeting reference but not yet plumbed into `devbench report`.

### Opus 4.7 / 4.6 / 4.5

```yaml
report:
  token_cost_per_million_input: 5.0
  token_cost_per_million_output: 25.0
  token_cost_input_ratio: 0.80
```

### Opus 4.1 / 4

```yaml
report:
  token_cost_per_million_input: 15.0
  token_cost_per_million_output: 75.0
  token_cost_input_ratio: 0.80
```

### Sonnet 4.6 / 4.5 / 4

```yaml
report:
  token_cost_per_million_input: 3.0
  token_cost_per_million_output: 15.0
  token_cost_input_ratio: 0.80
```

### Haiku 4.5

```yaml
report:
  token_cost_per_million_input: 1.0
  token_cost_per_million_output: 5.0
  token_cost_input_ratio: 0.80
```

### Haiku 3.5

```yaml
report:
  token_cost_per_million_input: 0.80
  token_cost_per_million_output: 4.0
  token_cost_input_ratio: 0.80
```

### Mixed-model setups

If your orchestrator uses different models for different roles (for example, Opus for executor and Sonnet for judges via `executor_model` / `judge_model` in `devbench.yaml`), pick the rate of the model that consumes the most tokens — usually the executor — for the most accurate single-figure estimate. There is no per-role cost split in `devbench report` yet (see [Current gaps](architecture.md#section-10--current-gaps-known-limitations) in the architecture doc).

---

## What `token_cost_input_ratio` means

The orchestrator's hook log (`hook-logs.jsonl`) records a single `totalTokens` value per tool call — it does not distinguish input from output. `devbench report` therefore computes a **blended rate** from your input and output prices, weighted by the input/output ratio you specify:

```
blended_per_M = (input_rate * input_ratio) + (output_rate * (1 - input_ratio))
```

For the default `token_cost_input_ratio: 0.80` and Opus 4.7 prices ($5/$25):

```
blended_per_M = (5.0 * 0.80) + (25.0 * 0.20) = $9.00 / 1M tokens
```

**Why 0.80 is a reasonable default** for SDLC workloads: the executor reads work units, repo files, prior judge feedback, and TDD cycle logs (input-heavy) and writes test/code diffs and TDD log entries (output-light). Reviewer agents are even more input-heavy because they consume the full diff plus prior comments and emit only short JSON envelopes. If you observe your sessions skewing more output-heavy (for example, the executor is writing a lot of new prose docs), drop the ratio toward 0.70 or 0.60.

---

## Current code defaults

`src/devbench/constants.py` defines:

```python
DEFAULT_TOKEN_COST_PER_M_INPUT: float = 5.0
DEFAULT_TOKEN_COST_PER_M_OUTPUT: float = 25.0
DEFAULT_TOKEN_COST_INPUT_RATIO: float = 0.80
```

These reflect current **Opus 4.7** pricing. If you run a different model — Sonnet, Haiku, or any older Opus generation — set the `report:` values explicitly in your `devbench.yaml` using the table above so `devbench report` produces accurate cost estimates for your model.

### Other settings under `report:`

The `report:` section also accepts a `display_timezone` field (IANA zone name) to control which timezone the report renders timestamps in:

```yaml
report:
  token_cost_per_million_input: 5.0
  token_cost_per_million_output: 25.0
  token_cost_input_ratio: 0.80
  display_timezone: America/Denver   # optional; defaults to system local TZ
```

When unset (or set to a name that isn't a valid IANA zone), the report falls back to the host's system local timezone. Override per-invocation via `JUDGE_REPORT_TIMEZONE=<zone>`. This setting is useful when running devbench inside a devcontainer or VM whose system clock is UTC but you want to read timestamps in your actual location's TZ.

---

## Long context, batch, and cache pricing

Claude Opus 4.7, Opus 4.6, and Sonnet 4.6 support a **1M-token context window** at the same per-token rate as smaller requests — there is no long-context premium for these models on the Claude API.

The **Batch API** offers 50% off input and output for asynchronous workloads (devbench does not currently use the Batch API; the orchestrator runs interactive sessions).

**Prompt caching** charges 1.25x the input rate for 5-minute writes and 0.10x for cache hits. Devbench's executor and reviewer agents benefit from caching automatically when the SDK reuses cached system prompts, but the cost estimator does not yet model cache savings. Real spend may be lower than the report's estimate when caching is active.

For full pricing details, multipliers, and platform-specific rates (Bedrock, Vertex AI, Foundry), see <https://platform.claude.com/docs/en/about-claude/pricing>.

---

## Bedrock and other platforms

Pricing on AWS Bedrock, Google Vertex AI, and Microsoft Foundry is set by the platform vendor, not Anthropic. The standard Anthropic rates above are a reasonable starting estimate but verify against your platform's pricing page:

- AWS Bedrock — <https://aws.amazon.com/bedrock/pricing/>
- Google Vertex AI — <https://cloud.google.com/vertex-ai/generative-ai/pricing#claude-models>
- Microsoft Foundry — <https://azure.microsoft.com/en-us/pricing/details/microsoft-foundry/>

Starting with Sonnet 4.5 and Haiku 4.5, regional and multi-region endpoints on Bedrock and Vertex AI carry a 10% premium over global endpoints.
