# Slack Notifications: Operator Setup Guide

devbench can send a Slack message on every interesting lifecycle event —
a work unit finishes, a task gets blocked, a PR is opened, the
orchestrator stops, quota recovers, and so on. Each event is toggled
independently in `devbench.yaml`, so you only get pinged on the things
you actually care about.

This document is the end-to-end operator walkthrough: how Slack
webhooks work, how to create one bound to your DMs, where the
credentials live, and how to flip the toggles you want.

## What you get

- A Slack message on every event you enable, with a `<@you>` mention
  that drives a desktop + mobile push notification (even when the
  message lands in a channel).
- Best-effort delivery: if Slack is down or the URL is wrong, the
  orchestrator logs a `[WARN]` to stderr and **keeps running**. A
  failed notification never crashes the orchestrator.
- One YAML toggle per event. Defaults are all `false` so devbench is
  silent until you opt in.

## How Slack incoming webhooks work

Slack's "Incoming Webhooks" feature gives you a URL that takes a JSON
payload and posts a message into one specific channel. The URL is
**channel-scoped**, not user-scoped — you can't DM a user directly
with it.

The trick to "DM yourself" is:

1. Create a **private channel** with only yourself as a member.
2. Bind the webhook to that channel.
3. Include a `<@your_user_id>` mention in every payload so Slack
   pushes a notification to your phone / desktop the same way it
   would for a real DM.

That's exactly what devbench does. You give it the webhook URL and
your user ID; it posts to the channel (where only you are) with the
mention, and you get a push.

## One-time setup (step by step)

### 1. Create a Slack channel for the notifications

In your Slack workspace:

1. Click the `+` next to **Channels** in the left sidebar.
2. Create a **private** channel named `#devbench-<your-handle>`
   (e.g. `#devbench-alice`).
3. Do **not** invite anyone. You should be the only member.

### 2. Create a Slack app + incoming webhook

Slack incoming webhooks live inside a Slack app. You'll create a
single-purpose app named `devbench-notify` and bind one webhook to
your private channel.

1. Open <https://api.slack.com/apps>.
2. Click **Create New App** → **From scratch**.
3. Name it `devbench-notify`. Pick the Slack workspace you just
   created the channel in. Click **Create App**.
4. In the left sidebar, click **Incoming Webhooks**.
5. Flip **Activate Incoming Webhooks** to **On**.
6. Scroll down. Click **Add New Webhook to Workspace**.
7. Pick the `#devbench-<your-handle>` channel you created. Click
   **Allow**.
8. Slack drops you back to the Incoming Webhooks page with a new row
   in the **Webhook URLs for Your Workspace** table. Copy the URL —
   it looks like:

   ```
   https://hooks.slack.com/services/T01ABCDEFGH/B02IJKLMNOP/qrstUVWXYZ123456789
   ```

   This URL is a credential. Treat it like a password. Anyone with
   the URL can post messages to your channel.

### 3. Get your Slack user ID

The user ID is the `U...` (or `W...` for Slack Connect / Enterprise
Grid) string that identifies you to Slack's API.

1. In Slack desktop, click your avatar in the top-right.
2. Click **Profile**.
3. Click the `⋮` menu in the profile pane.
4. Click **Copy member ID**.

You'll get a string like `U07ABCDEFGH`. Save it.

### 4. Drop the credentials in your shell env

The recommended pattern keeps Slack credentials out of any
checked-in YAML. Put them in `~/.devbench/shell.env` (or wherever
you keep your devbench operator secrets) so they get sourced into
the environment of every devbench process you launch.

Add these two lines (substitute your real URL + user ID):

```bash
export DEVBENCH_NOTIFICATIONS_SLACK_WEBHOOK_URL='https://hooks.slack.com/services/T01ABCDEFGH/B02IJKLMNOP/qrstUVWXYZ123456789'
export DEVBENCH_NOTIFICATIONS_SLACK_USER_ID='U07ABCDEFGH'
```

Then source the file:

```bash
source ~/.devbench/shell.env
```

devbench reads these env vars at config-load and they take precedence
over any matching yaml field. They never appear in any tracked file
and never show up in the orchestrator's log output (the dispatcher
masks the URL whenever it logs a delivery failure).

### 5. Pick the events you want

Edit `backlog/config/devbench.yaml` in your workspace and add the
`notifications:` block. Flip on the events you want. Everything not
listed defaults to `false`.

Example: a typical operator wants to know when a task finishes, when
something needs human attention, when the orchestrator stops, and
when quota pauses or resumes:

```yaml
notifications:
  enabled: true
  events:
    work_unit_done: true
    work_unit_blocked_operator: true
    orchestrator_stop: true
    quota_pause: true
    quota_resume: true
```

The `slack:` block is intentionally not included — the env vars from
step 4 supply the URL + user ID.

### 6. Smoke-test it

From the workspace root, run:

```bash
DEVBENCH_WORKSPACE_ROOT=$(pwd) \
DEVBENCH_CLAUDE_MODEL=us.anthropic.claude-opus-4-7-v1 \
uv run --project /path/to/devbench devbench notify-test --event work_unit_done
```

You should see a Slack message arrive in your private channel within
a second or two, with the body:

> 🟢 *<@U07ABCDEFGH> Work unit done: E0-F1-S1-T1*
> *Task* `E0-F1-S1-T1`
> *Title* Sample test task

If nothing arrives, see **Troubleshooting** below.

## Event reference

Every event toggle, when it fires, and what's in the payload:

| Toggle | Fires when | Payload fields |
|---|---|---|
| `work_unit_done` | A task transitions to `done`. | Task id, title. |
| `work_unit_blocked_operator` | A task is blocked AND the classifier flags it as `OPERATOR_ACTION_REQUIRED` (auto-clearing blocks do **not** fire). | Task id, title, reason. |
| `work_unit_materialised` | A draft WU file is written from a proposal. | New task id, title, source task id. |
| `work_unit_promoted` | A draft WU is promoted to `in-queue`. | Task id, title. |
| `pr_opened` | `gh pr create` succeeded for a work unit. | Task id, repo, PR URL. |
| `pr_merged` | `gh pr merge` succeeded. | Task id, repo, PR URL. |
| `ci_failure` | A CI run on a WU PR is classified as failed. | Task id, repo, PR URL, attempt number. |
| `orchestrator_stop` | The orchestrator loop exits — clean, drain, SIGTERM, or uncaught exception. **Always fires** when notifications.enabled is true (best-effort try/finally at the top of `cmd_start`). | Reason, in-flight WU id (when one was active). |
| `orchestrator_auto_restart` | The orchestrator exited with code 42 (RUNTIME_DEGRADATION-only NO_ACTIONABLE) and the Makefile loop is restarting. | List of blocked task ids (truncated at 5). |
| `quota_pause` | The orchestrator detects a quota signal and starts the wait. | Reason, reset_at timestamp, paused_at timestamp. |
| `quota_resume` | The recovery probe succeeded; the orchestrator is resuming. | Resumed_at, waited_seconds. |

## Authentication & secret hygiene

Webhook URLs are credentials. Anyone with the URL can post to your
channel. CLAUDE.md treats them as restricted data:

- **Never commit a webhook URL to a tracked yaml.** Use env vars.
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

**No message arrives, no warning.** The most likely cause is
`notifications.enabled: false` or the specific event toggle being
off. Re-run `devbench notify-test --event work_unit_done` (the test
command forces the toggle on temporarily so it bypasses
per-event gating).

**Stderr shows `[WARN] webhook POST to '...SECRET01' failed: 404`.**
The webhook was deleted from Slack. Generate a new one from
<https://api.slack.com/apps> → Incoming Webhooks → Add New Webhook
to Workspace, and update the env var.

**Stderr shows `403` or `channel_not_found`.** The private channel
the webhook is bound to was renamed or you got removed from it.
Either restore the channel or generate a new webhook bound to a
new channel.

**Message arrives but no push notification.** Check that
`DEVBENCH_NOTIFICATIONS_SLACK_USER_ID` is your actual Slack member
ID (`U...` or `W...`). Without it, the message has no `<@you>`
mention and Slack treats it as a regular channel message — silent
unless you have channel-level mentions enabled.

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

- [docs/quota-handling.md](quota-handling.md) — the quota pause /
  resume lifecycle the `quota_pause` / `quota_resume` events fire
  on top of.
- [docs/devbench-yaml-reference.md](devbench-yaml-reference.md) —
  full reference for every `notifications:` field.
- ADR-24 (`docs/adr/24-quota-wait-and-resume.md`) — the original
  pause/resume design that introduced the notification webhooks
  (PR #202 unified the dispatch path across every lifecycle event).
