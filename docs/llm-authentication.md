# LLM Authentication

DevBench supports two LLM backends for judge evaluation. Choose one based on your environment.

## Table of contents

- [Option 1: Anthropic API via Claude Code OAuth (default)](#option-1-anthropic-api-via-claude-code-oauth-default)
- [Option 2: AWS Bedrock](#option-2-aws-bedrock)
- [Per-agent model overrides (quota management)](#per-agent-model-overrides-quota-management)

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
| `JUDGE_CLAUDE_CREDENTIALS_FILE` | `~/.claude/.credentials.json` | Path to the Claude Code credentials file |
| `JUDGE_CLAUDE_MODEL` | *(required)* | Model used for LLM evaluation (e.g. `claude-opus-4-7`) |
| `JUDGE_LLM_TIMEOUT` | `300` | Timeout for LLM API calls (seconds) |

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

When `JUDGE_USE_BEDROCK=1` is set, DevBench uses `anthropic.AnthropicBedrock` instead of `anthropic.Anthropic`. AWS credentials are resolved through the standard boto3 credential chain (IAM role, environment variables, AWS config file, etc.).

### Requirements

- **AWS credentials configured** -- via IAM role, `AWS_ACCESS_KEY_ID`/`AWS_SECRET_ACCESS_KEY`, or `~/.aws/credentials`
- **Bedrock model access enabled** -- the configured model must be enabled in your AWS account for the target region
- **No Claude Code login needed** -- authentication is handled entirely through AWS

### Configuration

| Variable | Default | Description |
|----------|---------|-------------|
| `JUDGE_USE_BEDROCK` | `false` | Set to `1`, `true`, or `yes` to enable Bedrock |
| `JUDGE_BEDROCK_REGION` | `us-east-1` | AWS region for Bedrock API calls (falls back to `AWS_REGION`) |
| `JUDGE_CLAUDE_MODEL` | *(required)* | Bedrock model ID (e.g. `us.anthropic.claude-opus-4-7-v1`) |
| `JUDGE_LLM_TIMEOUT` | `300` | Timeout for LLM API calls (seconds) |

### Usage

```bash
JUDGE_CLAUDE_MODEL=us.anthropic.claude-opus-4-7-v1 \
JUDGE_USE_BEDROCK=1 \
JUDGE_BEDROCK_REGION=us-east-1 \
make start
```

(Swap `make start` for `make start-interactive` only if you want the live-observation
mode -- non-interactive is the recommended default.)

### Verifying Authentication

```bash
JUDGE_USE_BEDROCK=1 JUDGE_CLAUDE_MODEL=us.anthropic.claude-opus-4-7-v1 \
python3 -c "
from devbench.config import USE_BEDROCK, BEDROCK_REGION
print(f'Bedrock enabled: {USE_BEDROCK}')
print(f'Region: {BEDROCK_REGION}')
"
```

### Troubleshooting

#### Bedrock credential resolution chain

When `JUDGE_USE_BEDROCK=1`, the Anthropic SDK delegates AWS auth to boto3, which checks credentials in this order (first match wins):

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

Ensure `JUDGE_BEDROCK_REGION` matches the region where you have Bedrock model access enabled. Cross-region inference model IDs use the `us.anthropic.*` prefix.

---

## Per-agent model overrides (quota management)

DevBench's ten work agents (executor, blocker-resolver, manifest-amender, security-reviewer, task-factory, review-supervisor, plus the four review_team judges) each declare a default model in their `.md` frontmatter. Operators whose per-model quota is uneven -- e.g. opus tokens left, sonnet exhausted, or vice versa -- can retarget any subset of agents to a different model without editing the canonical plugin. See [ADR-25](adr/25-per-agent-model-overrides.md) for the architectural details.

Add an `agents:` block to `backlog/config/devbench.yaml`. The shape below pins each agent to its **current frontmatter default** (nine agents on `sonnet`, `review_supervisor` on `haiku`) -- it is a no-op as written; flip individual fields when you need to retarget:

```yaml
agents:
  executor: sonnet
  blocker_resolver: sonnet
  manifest_amender: sonnet
  security_reviewer: sonnet
  task_factory: sonnet
  review_supervisor: haiku
  review_team:
    code_reviewer: sonnet
    test_reviewer: sonnet
    doc_reviewer: sonnet
    changes_manifest: sonnet
```

Every field defaults to `null` when absent (the agent runs on its frontmatter model). The values must match your authentication channel:

- `use_bedrock: false` -- short names (`opus` / `sonnet` / `haiku`) or full Anthropic API ids (`claude-opus-4-7`, `claude-sonnet-4-6`).
- `use_bedrock: true` -- full Bedrock ARNs (`us.anthropic.claude-opus-4-7-v1`, `us.anthropic.claude-sonnet-4-6-v1`).

Mismatches fail fast at config-load time with an actionable error message, rather than as a generic 401/404 on the first agent invocation.

Per-call env-var overrides take precedence over YAML (env > yaml > frontmatter):

```bash
JUDGE_AGENT_MODEL_EXECUTOR=opus
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
