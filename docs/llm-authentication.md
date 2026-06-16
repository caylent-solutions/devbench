# LLM Authentication

DevBench supports two LLM backends for judge evaluation. Choose one based on your environment.

## Table of contents

- [Option 1: Anthropic API via Claude Code OAuth (default)](#option-1-anthropic-api-via-claude-code-oauth-default)
- [Option 2: AWS Bedrock](#option-2-aws-bedrock)
- [Per-agent model overrides (quota management)](#per-agent-model-overrides-quota-management)
- [The supervise path: billing-mode interactive orchestrator](#the-supervise-path-billing-mode-interactive-orchestrator)

## Option 1: Anthropic API via Claude Code OAuth (default)

Uses your existing Claude Code OAuth credentials -- no separate Anthropic API key needed. Requires a Claude Pro or Enterprise subscription.

### How It Works

When you authenticate with Claude Code (by running `claude` and logging in), Claude Code stores an OAuth token at:

```
~/.claude/.credentials.json
```

This file contains a `claudeAiOauth` object with an `accessToken` that has the `user:inference` scope. The `user:inference` scope grants permission to make LLM inference calls (Messages API) but not administrative operations like managing API keys or billing. The Anthropic Python SDK accepts this token as an `api_key`, so DevBench reads it directly.

### Authentication Flow

```
1. User logs into Claude Code (one-time: `claude` → browser OAuth)
2. Claude Code writes ~/.claude/.credentials.json
3. DevBench reads accessToken from that file
4. Anthropic SDK uses the token for API calls (model inference)
5. Token auto-refreshes when Claude Code is running
```

### Requirements

- **Claude Code authenticated** -- run `claude` at least once and complete the browser login
- **Claude Pro or Enterprise subscription** -- the OAuth token requires a valid subscription with `user:inference` scope
- **No ANTHROPIC_API_KEY needed** -- the system reads credentials from the file, not from environment variables

### Configuration

| Variable | Default | Description |
|----------|---------|-------------|
| `DEVBENCH_CLAUDE_CREDENTIALS_FILE` | `~/.claude/.credentials.json` | Path to the Claude Code credentials file |
| `DEVBENCH_CLAUDE_MODEL` | *(required)* | SDK caller's model -- governs the orchestrate skill's coordination calls (e.g. `claude-opus-4-8`). Per-agent work models live in the `agents:` block of `devbench.yaml` ([ADR-25](adr/25-per-agent-model-overrides.md)) and default to each agent's `.md` frontmatter. |
| `DEVBENCH_LLM_TIMEOUT` | `300` | Timeout for LLM API calls (seconds) |

### Verifying Authentication

Check that the credentials file exists and contains a valid token:

```bash
python3 -c "
from devbench.config import get_anthropic_api_key
token = get_anthropic_api_key()
print(f'Token found: {token[:15]}...')
"
```

### Troubleshooting

#### "Claude credentials file not found"

Run `claude` in your terminal and complete the OAuth login flow. This creates `~/.claude/.credentials.json`.

#### "No access token found"

The credentials file exists but the token is empty or missing. Re-authenticate:

```bash
claude
# Complete the browser login when prompted
```

#### "Unexpected credentials structure"

The credentials file format may have changed. Check the file structure:

```bash
python3 - <<'PY'
import json, os
path = os.path.expanduser("~/.claude/.credentials.json")
with open(path) as f:
    print(json.dumps(json.load(f), indent=2, default=str))
PY
```

The expected structure is:
```json
{
  "claudeAiOauth": {
    "accessToken": "sk-ant-oat01-...",
    "scopes": ["user:inference", ...],
    ...
  }
}
```

#### Token expiration

Claude Code OAuth tokens have an expiration timestamp embedded in `~/.claude/.credentials.json`. When you launch a Claude Code interactive session (`claude` in the terminal), the Claude Code CLI itself manages token refresh as long as the session is active.

DevBench's `cmd_start()` (the SDK entry point used by `make start`) does **not** run a background token refresher -- it reads the credential file at startup and trusts that whoever launched the orchestrator has a valid token. If the token expires mid-run, the next LLM call fails with an API error and the orchestrator stops.

To recover: re-authenticate Claude Code (`claude` → complete the browser flow) and restart the orchestrator.

## Option 2: AWS Bedrock

Uses the Anthropic Bedrock SDK with your AWS credentials. No Claude Code subscription required -- billing goes through your AWS account.

### How It Works

When `DEVBENCH_USE_BEDROCK=1` is set, DevBench uses `anthropic.AnthropicBedrock` instead of `anthropic.Anthropic`. AWS credentials are resolved through the standard boto3 credential chain (IAM role, environment variables, AWS config file, etc.).

### Requirements

- **AWS credentials configured** -- via IAM role, `AWS_ACCESS_KEY_ID`/`AWS_SECRET_ACCESS_KEY`, or `~/.aws/credentials`
- **Bedrock model access enabled** -- the configured model must be enabled in your AWS account for the target region
- **No Claude Code login needed** -- authentication is handled entirely through AWS

### Configuration

| Variable | Default | Description |
|----------|---------|-------------|
| `DEVBENCH_USE_BEDROCK` | `false` | Set to `1`, `true`, or `yes` to enable Bedrock |
| `DEVBENCH_BEDROCK_REGION` | `us-east-1` | AWS region for Bedrock API calls (falls back to `AWS_REGION`) |
| `DEVBENCH_CLAUDE_MODEL` | *(required)* | SDK caller's Bedrock model ID -- governs the orchestrate skill's coordination calls (e.g. `us.anthropic.claude-opus-4-8-v1`). Per-agent work models live in the `agents:` block of `devbench.yaml` ([ADR-25](adr/25-per-agent-model-overrides.md)). |
| `DEVBENCH_LLM_TIMEOUT` | `300` | Timeout for LLM API calls (seconds) |

### Usage

```bash
DEVBENCH_CLAUDE_MODEL=us.anthropic.claude-opus-4-8-v1 \
DEVBENCH_USE_BEDROCK=1 \
DEVBENCH_BEDROCK_REGION=us-east-1 \
make start
```

(Swap `make start` for `make start-interactive` only if you want the live-observation
mode -- non-interactive is the recommended default.)

### Verifying Authentication

```bash
DEVBENCH_USE_BEDROCK=1 DEVBENCH_CLAUDE_MODEL=us.anthropic.claude-opus-4-8-v1 \
python3 -c "
from devbench.config import USE_BEDROCK, BEDROCK_REGION
print(f'Bedrock enabled: {USE_BEDROCK}')
print(f'Region: {BEDROCK_REGION}')
"
```

### Troubleshooting

#### Bedrock credential resolution chain

When `DEVBENCH_USE_BEDROCK=1`, the Anthropic SDK delegates AWS auth to boto3, which checks credentials in this order (first match wins):

1. Explicit env vars: `AWS_ACCESS_KEY_ID` + `AWS_SECRET_ACCESS_KEY` (+ optional `AWS_SESSION_TOKEN`)
2. Shared credentials file: `~/.aws/credentials` (profile selected by `AWS_PROFILE`, default `default`)
3. AWS config file: `~/.aws/config`
4. Container credentials (ECS task role)
5. Instance metadata (EC2 IAM role)

If you get "Could not resolve credentials," start by running `aws sts get-caller-identity` in the same shell -- it uses the same chain and will show you where boto3 is looking.

#### "Could not resolve credentials"

AWS credentials are not configured. Set `AWS_ACCESS_KEY_ID` and `AWS_SECRET_ACCESS_KEY`, or configure an IAM role.

#### "Access denied" or "Model not found"

The Bedrock model is not enabled in your AWS account for the configured region. Enable it in the AWS Bedrock console.

#### Region mismatch

Ensure `DEVBENCH_BEDROCK_REGION` matches the region where you have Bedrock model access enabled. Cross-region inference model IDs use the `us.anthropic.*` prefix.

---

## Per-agent model overrides (quota management)

DevBench's ten work agents (executor, blocker-resolver, manifest-amender, security-reviewer, task-factory, review-supervisor, plus the four review_team judges) each declare a default model in their `.md` frontmatter. Operators whose per-model quota is uneven -- e.g. opus tokens left, sonnet exhausted, or vice versa -- can retarget any subset of agents to a different model without editing the canonical plugin. See [ADR-25](adr/25-per-agent-model-overrides.md) for the architectural details.

> **Haiku is rejected at config-load time (caylent-solutions/devbench#198).** Under load the Claude Agent SDK was repeatedly observed to silently drop the `Agent` tool (and other multi-call tools) from haiku's tool list mid-orchestration, breaking parallel sub-agent dispatch and forcing the orchestrator to classify work-units as `RUNTIME_DEGRADATION`. Any `agents:` block value containing `haiku` -- short name, full Anthropic id, or Bedrock ARN -- raises a `ValueError` at config-load. Use `sonnet` or `opus` for every work agent.

Add an `agents:` block to `backlog/config/devbench.yaml`. The shape below pins each agent to its **current frontmatter default**: `executor` on `sonnet` (writes code under TDD; fast happy path); the five judges plus the three workflow-reasoning agents (`blocker_resolver`, `manifest_amender`, `task_factory`) on `opus` (judgment work where wrong calls cost more than inference); `review_supervisor` on `sonnet` (fan-out coordinator). The block as written is a no-op; flip individual fields when you need to retarget (e.g., drop the judges to `sonnet` when opus quota is exhausted):

```yaml
agents:
  executor: sonnet
  blocker_resolver: opus
  manifest_amender: opus
  security_reviewer: opus
  task_factory: opus
  review_supervisor: sonnet
  review_team:
    code_reviewer: opus
    test_reviewer: opus
    doc_reviewer: opus
    changes_manifest: opus
```

Every field defaults to `null` when absent (the agent runs on its frontmatter model). The values must match your authentication channel:

- `use_bedrock: false` -- short names (`opus` / `sonnet`) or full Anthropic API ids (`claude-opus-4-8`, `claude-sonnet-4-6`).
- `use_bedrock: true` -- full Bedrock ARNs (`us.anthropic.claude-opus-4-8-v1`, `us.anthropic.claude-sonnet-4-6-v1`).

Mismatches fail fast at config-load time with an actionable error message, rather than as a generic 401/404 on the first agent invocation.

Per-call env-var overrides take precedence over YAML (env > yaml > frontmatter):

```bash
DEVBENCH_AGENT_MODEL_EXECUTOR=opus
JUDGE_AGENT_MODEL_CODE_REVIEWER=opus
JUDGE_AGENT_MODEL_CHANGES_MANIFEST=opus
```

Both modes apply the override the same way:

- **Non-interactive** (`devbench start`): a workspace-local shadow plugin tree is materialised at `<workspace>/.devbench/plugin-shadow/devbench/` automatically.
- **Interactive** (`claude --plugin-dir ...`): run `uv run devbench prepare-plugin-shadow` first to materialise the same shadow tree, then pass its path to `--plugin-dir`:

```bash
claude --plugin-dir "$(uv run devbench prepare-plugin-shadow)"
```

Workspaces without an `agents:` block build no shadow and use the canonical plugin path -- behaviour is bit-identical to pre-feature releases.

## The supervise path: billing-mode interactive orchestrator

Both options above bill the orchestrator's inference at **API rates**: the SDK path (`devbench start`) hands the Claude Code OAuth `accessToken` to the Anthropic SDK as an `api_key` (Option 1), or routes to AWS Bedrock (Option 2). Either way, tokens are metered per-token against the API/Bedrock account.

`devbench supervise` launches the orchestrator as an interactive `claude` CLI session (under a detached `screen` daemon driven by a `pexpect` supervisor). Its `--billing-mode` flag (default `subscription`; precedence `--billing-mode` > `DEVBENCH_SUPERVISE_BILLING_MODE` env > `supervise.billing_mode` config > default) selects the channel. See [supervise.md](supervise.md) and ADR-31 ([adr/31-interactive-screen-supervisor.md](adr/31-interactive-screen-supervisor.md)).

### `subscription` mode (default)

Authenticated via the Claude Code Max subscription login, so the session draws from the subscription's rolling 5-hour usage windows instead of per-token API/Bedrock billing.

**No-routing-var requirement (correctness, not a preference).** An interactive `claude` session whose environment carries `ANTHROPIC_API_KEY` (or `ANTHROPIC_AUTH_TOKEN`, `ANTHROPIC_API_URL`, `ANTHROPIC_BASE_URL`, `DEVBENCH_USE_BEDROCK`, or the claude-CLI Bedrock/Vertex routing vars `CLAUDE_CODE_USE_BEDROCK`/`CLAUDE_CODE_USE_VERTEX`/`ANTHROPIC_BEDROCK_BASE_URL`/`ANTHROPIC_MODEL`/`ANTHROPIC_SMALL_FAST_MODEL`/`AWS_BEARER_TOKEN_BEDROCK`) silently routes inference off-subscription and defeats the entire purpose. In `subscription` mode the supervisor strips every one of those routing vars from the session environment (the routing deny set is non-removable) and FAILS FAST at preflight (exit 2) if any is present in the operator's environment:

```
ERROR: ANTHROPIC_API_KEY is set; an interactive supervised session in
subscription mode must bill via the Claude Code subscription, not the direct
API. Unset it and retry.
```

It also verifies the same `~/.claude/.credentials.json` `user:inference` OAuth credential Option 1 documents (the supervisor verifies, but never manages, the login), and surfaces `billing-channel: subscription` in `supervise status`/`info`.

### `bedrock` mode

The same interactive session routes inference through AWS Bedrock. The supervisor strips only the direct-Anthropic-API vars (`ANTHROPIC_API_KEY`, `ANTHROPIC_AUTH_TOKEN`, `ANTHROPIC_API_URL`, `ANTHROPIC_BASE_URL`) and EXPORTS the claude-CLI Bedrock route the CLI needs (`CLAUDE_CODE_USE_BEDROCK=1`, `AWS_REGION`, and the resolved `ANTHROPIC_MODEL` Bedrock model id), consistent with the SDK Bedrock handling in Option 2. It does NOT require subscription auth; it FAILS FAST at preflight if the AWS Bedrock prerequisites are absent (no AWS credential among `AWS_ACCESS_KEY_ID`/`AWS_PROFILE`/`AWS_BEARER_TOKEN_BEDROCK`, or no `AWS_REGION`/`AWS_DEFAULT_REGION`). It surfaces `billing-channel: bedrock` in `supervise status`/`info`. Because Bedrock has no 5-hour subscription windows, the supervisor's 5-hour quota wait is disabled (throttling is handled by the shared `quota.py` path).

### AWS workload creds pass through in both modes

The AWS workload credentials (`AWS_ACCESS_KEY_ID`, `AWS_SECRET_ACCESS_KEY`, `AWS_SESSION_TOKEN`, `AWS_PROFILE`) and region (`AWS_REGION` / `AWS_DEFAULT_REGION`) are in NEITHER mode's deny set: AWS creds do NOT route Claude billing (only the Bedrock route flag does), and the supervised orchestrator runs live AWS terratests that cannot work without them. They pass through unchanged and are never treated as a billing-routing violation. The non-root preflight assertion applies in both modes.

### CLI-hang guards are always set (not billing-routing vars)

In BOTH billing modes the supervise launch env additionally sets `DISABLE_AUTOUPDATER=1` and `CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC=1` unconditionally, so the interactive `claude` child cannot hang on the CLI auto-updater ("Checking for updates") -- a hang that the PTY-silence idle timer cannot catch (the spinner keeps the PTY busy). These are NOT billing-routing vars: they are absent from every deny set and are never stripped. They work alongside the progress watchdog (`supervise.timeouts.progress_stall_seconds`), which auto-restarts a session whose orchestrator log stops growing; see [supervise.md](supervise.md).
