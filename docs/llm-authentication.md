# LLM Authentication

DevBench supports two LLM backends for judge evaluation. Choose one based on your environment.

## Table of contents

- [Option 1: Anthropic API via Claude Code OAuth (default)](#option-1-anthropic-api-via-claude-code-oauth-default)
- [Option 2: AWS Bedrock](#option-2-aws-bedrock)

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
make start-interactive
```

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
