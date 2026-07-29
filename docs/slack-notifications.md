# Slack Notifications: Operator Setup Guide

devbench can send a Slack message on every interesting lifecycle event —
a work unit finishes, a task gets blocked, a PR is opened, the
orchestrator stops, and so on. Each event is toggled
independently in `devbench.yaml`, so you only get pinged on the things
you actually care about.

This document is the end-to-end operator walkthrough: how Slack
webhooks work, how to create one bound to your DMs (or to a shared
team channel), where the credentials live, and how to flip the toggles
you want.

## What you get

- A Slack message on every event you enable. Every payload starts
  with a literal `<!here>` mention, so the message drives a desktop +
  mobile push notification for every online member of the channel
  the webhook is bound to.
- The same payload works in two routings:
  - **DM-yourself pattern.** Bind the webhook to a private channel
    that has only you in it. `<!here>` notifies you only.
  - **Team-channel pattern.** Bind the webhook to a shared channel
    with multiple operators. `<!here>` notifies every online member
    of the team at once.
- Best-effort delivery: if Slack is down or the URL is wrong, the
  orchestrator logs a `[WARN]` to stderr and **keeps running**. A
  failed notification never crashes the orchestrator.
- One YAML toggle per event. Defaults are all `false` so devbench is
  silent until you opt in.
- Every payload carries a `Backlog` field naming the workspace the
  ping came from, so operators monitoring multiple workspaces can tell
  at a glance which backlog a ping refers to. The label is the
  basename of `DEVBENCH_WORKSPACE_ROOT`.

## How Slack incoming webhooks work

Slack's "Incoming Webhooks" feature gives you a URL that takes a JSON
payload and posts a message into one specific channel. The URL is
**channel-scoped**, not user-scoped — you can't DM a user directly
with it.

The trick to "DM yourself" is:

1. Create a **private channel** with only yourself as a member.
2. Bind the webhook to that channel.
3. The payload uses `<!here>` so Slack pushes a notification to every
   online member of the channel — that's just you in this routing.

Same payload, bound to a shared channel, pushes to all online
operators in that channel. One config, two routings.

## One-time setup (step by step)

### 1. Decide on the channel routing

Either of these works without changing devbench config:

- **DM-yourself.** Create a private channel `#devbench-<your-handle>`
  in your Slack workspace and make yourself the only member.
- **Team channel.** Use an existing shared channel where every
  operator who should get pinged is already a member.

Whichever you pick, the channel is where the webhook will post.

### 2. Create a Slack app + incoming webhook

Slack incoming webhooks live inside a Slack app. You'll create a
single-purpose app named `devbench-notify` and bind one webhook to
the channel from step 1.

1. Open <https://api.slack.com/apps>.
2. Click **Create New App** → **From scratch**.
3. Name it `devbench-notify`. Pick the Slack workspace your channel
   is in. Click **Create App**.
4. In the left sidebar, click **Incoming Webhooks**.
5. Flip **Activate Incoming Webhooks** to **On**.
6. Scroll down. Click **Add New Webhook to Workspace**.
7. Pick the channel from step 1. Click **Allow**.
8. Slack drops you back to the Incoming Webhooks page with a new row
   in the **Webhook URLs for Your Workspace** table. Copy the URL —
   it looks like:

   ```
   https://hooks.slack.com/services/T01ABCDEFGH/B02IJKLMNOP/qrstUVWXYZ123456789
   ```

   This URL is a credential. Treat it like a password. Anyone with
   the URL can post messages to the channel.

### 3. Drop the credential in your shell env

The recommended pattern keeps the Slack webhook URL out of any
checked-in YAML. Put it in `~/.devbench/shell.env` (or wherever you
keep your devbench operator secrets) so it gets sourced into the
environment of every devbench process you launch.

Add this line (substitute your real URL):

```bash
export DEVBENCH_NOTIFICATIONS_SLACK_WEBHOOK_URL='https://hooks.slack.com/services/T01ABCDEFGH/B02IJKLMNOP/qrstUVWXYZ123456789'
```

Then source the file:

```bash
source ~/.devbench/shell.env
```

devbench reads this env var at config-load and it takes precedence
over any matching yaml field. It never appears in any tracked file
and never shows up in the orchestrator's log output (the dispatcher
masks the URL whenever it logs a delivery failure).

### 4. Pick the events you want

Edit `backlog/config/devbench.yaml` in your workspace and add the
`notifications:` block. Flip on the events you want. Everything not
listed defaults to `false`.

Example: a typical operator wants to know when a task finishes, when
something needs human attention, and when the orchestrator stops:

```yaml
notifications:
  enabled: true
  events:
    work_unit_done: true
    work_unit_blocked_operator: true
    orchestrator_stop: true
  slack:
    enabled: true
    # webhook_url left unset; the env var supplies it.
```

The `slack.webhook_url` field is intentionally not set — the env var
from step 3 supplies it.

### 5. Smoke-test it

From the workspace root, run:

```bash
DEVBENCH_WORKSPACE_ROOT=$(pwd) \
DEVBENCH_CLAUDE_MODEL=us.anthropic.claude-opus-4-7-v1 \
uv run --project /path/to/devbench devbench notify-test --event work_unit_done
```

You should see a Slack message arrive in your channel within a
second or two, with the body:

> 🟢 *<!here> Work unit done: E0-F1-S1-T1*
> *Task* `E0-F1-S1-T1`
> *Title* Sample test task

If nothing arrives, see **Troubleshooting** below.

## Event reference

Every event toggle, when it fires, and what's in the payload:

| Toggle | Fires when | Payload fields |
|---|---|---|
| `work_unit_done` | A task transitions to `done`. | Task id, title. |
| `work_unit_blocked_operator` | A task transitions into the `OPERATOR_ACTION_REQUIRED` bucket (initial entry or reclassification from another bucket). Idempotent per (task × class): repeated classifications without a class change do not re-ping; exiting and re-entering the bucket re-fires (#207). | Task id, title, reason. |
| `work_unit_blocked_runtime_degradation` | A task transitions into the `RUNTIME_DEGRADATION` bucket (SDK Agent-tool loss; requires `make start` restart) (#209). | Task id, title, reason. |
| `work_unit_blocked_held` | A task transitions into the `HELD` bucket (status is `hold`; operator must resume manually) (#209). | Task id, title, reason. |
| `work_unit_blocked_on_held` | A task transitions into the `BLOCKED_ON_HELD` bucket (marker target is in `hold`; operator must resume the held target) (#209). | Task id, title, reason. |
| `work_unit_blocked_auto_clearing` | A task transitions into the `AUTO_CLEARING_VIA_PROPOSAL` bucket (ADR-07 cascade in flight; will auto-unblock) (#209). | Task id, title, reason. |
| `work_unit_blocked_awaiting_dependency` | A task transitions into the `AWAITING_DEPENDENCY` bucket (regular dep still in flight; will auto-unblock) (#209). | Task id, title, reason. |
| `work_unit_blocked_amendment_recovery` | A task transitions into the `AWAITING_AMENDMENT_RECOVERY` bucket (recovery signal on disk; resumes on next sweep) (#209). | Task id, title, reason. |
| `work_unit_materialised` | A draft WU file is written from a proposal. | New task id, title, source task id. |
| `work_unit_promoted` | A draft WU is promoted to `in-queue`. | Task id, title. |
| `pr_opened` | `gh pr create` succeeded — fires from both the per-WU `cmd_git_ops` path AND the auto-finalize batch-PR path `cmd_git_ops_finalize` (#219). | Task id (most-recent active task or symbolic `finalize`), repo, PR URL. |
| `pr_merged` | `gh pr merge` succeeded. **Not fired from auto-finalize** because that path leaves the PR open for manual merge under `auto_merge: false` (#219). | Task id, repo, PR URL. |
| `ci_failure` | A CI run on a WU PR is classified as failed — fires from both `cmd_git_ops` (per-WU) and `_handle_finalize_ci_result` FAILED_KNOWN_TASK / FAILED_UNKNOWN branches (#219). | Task id, repo, PR URL, attempt number (sentinel `1` on the finalize path). |
| `ci_pass` | CI on the auto-finalize batch PR turned GREEN — explicit signal that the PR is ready for manual merge under `auto_merge: false` (#219). **Default off** so existing workspaces stay silent on upgrade. | Task id (most-recent active task or symbolic `finalize`), repo, PR URL. |
| `orchestrator_stop` | The orchestrator loop exits — clean, drain, SIGTERM, terminal-marker (#218), or uncaught exception. **Always fires** when notifications.enabled and slack.enabled are true (best-effort try/finally at the top of `cmd_start`). | Reason (post-#217 includes the SDK's `ResultMessage.result` text; post-#218 fires within seconds of the terminal marker via the `[ORCHESTRATOR_TERMINAL_EXIT]` audit), in-flight WU id (when one was active). |
| `orchestrator_auto_restart` | The orchestrator exited with code 42 (RUNTIME_DEGRADATION-only NO_ACTIONABLE) and the Makefile loop is restarting. | List of blocked task ids (truncated at 5). |
| `quota_waiting` | The orchestrator detected a quota limit and began waiting for it to reset. The dispatcher is wired (`notify_quota_waiting`); the orchestrator call site that invokes it lands in E2-F4-S1-T1. | `reason`, `reset_at`. |
| `quota_resumed` | The quota recovered and the run resumed. The dispatcher is wired (`notify_quota_resumed`); the orchestrator call site that invokes it lands in E2-F4-S1-T1. | `waited_seconds`. |

## Authentication & secret hygiene

Webhook URLs are credentials. Anyone with the URL can post to your
channel. CLAUDE.md treats them as restricted data:

- **Never commit a webhook URL to a tracked yaml.** Use the env var.
- **Rotate** by deleting the webhook from
  <https://api.slack.com/apps> → your app → Incoming Webhooks
  (click the trash icon next to the URL) and creating a fresh one.
- **Revoke immediately** if a URL leaks. The fastest revoke is to
  delete the whole Slack app; you can rebuild it in two minutes from
  step 2 above.
- Devbench masks webhook URLs in its `[WARN]` delivery-failure logs,
  showing only the last 8 characters. You can correlate a failure
  with the URL you configured without leaking the secret.

## Troubleshooting

**No message arrives, no warning.** The most likely cause is one of
the three master switches being off. All three must be true for a
POST to happen: `notifications.enabled`, `notifications.slack.enabled`,
and the specific `notifications.events.<event_name>`. Re-run
`devbench notify-test --event work_unit_done` (the test command
forces the per-event toggle on temporarily so it bypasses the third
gate while still honoring the first two).

**Stderr shows `[WARN] webhook POST to '...SECRET01' failed: 404`.**
The webhook was deleted from Slack. Generate a new one from
<https://api.slack.com/apps> → Incoming Webhooks → Add New Webhook
to Workspace, and update the env var.

**Stderr shows `403` or `channel_not_found`.** The channel the
webhook is bound to was renamed or you got removed from it. Either
restore the channel or generate a new webhook bound to a new
channel.

**Message arrives but no push notification.** Check that the channel
has notifications enabled in your Slack client. `<!here>` only
notifies online members — if your Slack is "Away" status, you'll
see the message but not a push. Adjust Slack's notification settings
for the channel (gear icon → "Get notifications for All messages").

**Test the webhook bypass devbench.** Sanity-check the URL directly
with curl:

```bash
curl -X POST -H 'Content-Type: application/json' \
     -d '{"text":"manual sanity check"}' \
     "$DEVBENCH_NOTIFICATIONS_SLACK_WEBHOOK_URL"
```

A response of `ok` means the URL is good. Anything else (`no_team`,
`invalid_payload`, etc.) tells you what's wrong on the Slack side.

## Cross-references

- [docs/devbench-yaml-reference.md](devbench-yaml-reference.md) —
  full reference for every `notifications:` field.
