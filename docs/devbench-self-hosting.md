# Self-hosting devbench (issue #301)

devbench can drive its own backlog: a workspace where the "target repo" devbench edits is
`caylent-solutions/devbench` itself. This document states the two-checkout split that setup
requires, which checkout actually executes the orchestrator, and the canonical procedure for
keeping them in sync. It exists because nothing wrote this down before issue #301: an operator had
no documented way to know the split existed, and on 2026-08-12 the harness install ran 30+ commits
behind the target for over five hours before the resulting stale-code crash was noticed.

## The two-checkout split

Self-hosting requires **two separate git clones** of `caylent-solutions/devbench`, at different
paths under the same `DEVBENCH_WORKSPACE_ROOT`:

| Install | Typical path | Role |
|---|---|---|
| **Harness** | `harness/devbench` (or wherever `DEVBENCH_PROJECT_ROOT` / your `uv run --project` points) | The code that actually executes. **Never** listed under `repos:` in `backlog/config/devbench.yaml`. |
| **Target** | `devbench/` (a sibling of `backlog/`) | The checkout devbench edits and commits into, like any other target repo. Listed under `repos:` with a `checkout_directory` pointing at it. |

`backlog/config/devbench.yaml` documents this split for the workspace at the top of the file, for
example:

```yaml
# Workspace root : /path/to/my-workspace
# Tool checkout  : harness/devbench   (drives the run; NOT a target repo)
# Target repo    : devbench/          (what the orchestrator edits)

repos:
  caylent-solutions/devbench:
    default_branch: main
    checkout_directory: devbench
```

Both clones point at the same `origin` remote. `src/devbench/install_parity.py`'s
`resolve_install_parity` is what lets devbench tell them apart: it resolves the harness install's
own identity (path, revision, branch, `origin` URL) from `install_parity.py`'s own package
location, then walks every repo configured under `repos:` looking for one whose checkout's
`origin` canonically matches the harness's `origin` (scheme, credentials, `.git` suffix, and host
letter case are all ignored for that comparison). A match means the workspace is self-hosting; the
matching repo's checkout is the target.

## Which checkout executes

**The harness.** Launching the orchestrator (`make start`, `uv run devbench start`, or
`uv run --project harness/devbench devbench start`) runs whichever `src/devbench` package Python
resolved from -- the checkout you invoked `uv run` / `make` from, or the one named by
`DEVBENCH_PROJECT_ROOT` / `--project`. That is the harness install, and it is the code that
actually drives the run: parses the backlog, launches the executor / review / git-ops agents, and
decides what to do next.

The target checkout is passive during a run: it only receives the commits the orchestrator lands
on behalf of completed work units, exactly like any other target repo devbench operates on. It
never executes orchestrator code itself.

This is why divergence is dangerous and easy to miss: because the target lands a commit per
completed self-hosting task, it moves strictly ahead of the harness during ordinary self-hosting
operation. A harness that is never resynced runs progressively staler code with every task it
completes, entirely silently, until issue #301's install-parity gate and report/status row (below)
made the gap visible.

## Verifying parity

Two commands surface whether the two installs currently agree:

- **`devbench start`** refuses to run when self-hosting is detected and the harness is missing
  commits touching `src/devbench/` that the target already has -- exit code 1, before any PID
  file, SDK session, or backlog claim. See [`docs/cli-reference.md`'s `start` section](cli-reference.md#start)
  for the exact refusal message and the resync commands it prints.
- **`devbench report`** and **`devbench status`** render an `Install parity` row -- `IN SYNC` or
  `BEHIND by <N> commit(s) touching src/devbench/` -- whenever self-hosting is detected, and
  `unavailable: <reason>` if the comparison itself fails. See
  [`docs/cli-reference.md`'s `report`](cli-reference.md#report) and
  [`status`](cli-reference.md#status) sections for the exact row formats.

Both surfaces call the identical resolver (`resolve_install_parity`), so `start`'s refusal and the
`report` / `status` row can never disagree about whether the harness is behind.

There is deliberately no flag, environment variable, or config key to bypass the `start` gate: the
only supported way past a refused start is to resync.

## The canonical resync procedure

Run these three commands against the **harness** checkout (not the target) to bring it back in
sync with the target's branch:

```bash
git -C <harness-path> fetch origin <branch>
git -C <harness-path> checkout -B <branch> origin/<branch>
uv sync --project <harness-path>
```

- `<harness-path>` is the harness install's own path (for example `harness/devbench`), exactly as
  named in the `start` refusal message.
- `<branch>` is the target checkout's current branch (also named in the refusal message);
  fast-forwarding the harness onto the target's branch tip is what closes the gap.
- `uv sync` re-installs dependencies for the resynced revision so the harness's virtualenv matches
  its new `uv.lock`, not just its source tree.

After resyncing, re-run `devbench start`; the gate proceeds once the harness's revision reaches or
passes the target's.

## See also

- [`docs/cli-reference.md`](cli-reference.md) -- `start`'s install-parity gate, and the `report` /
  `status` install-parity row.
- [`docs/backlog-contract.md`](backlog-contract.md#workspace-layout-what-devbench_workspace_root-points-at)
  -- the general (non-self-hosting) workspace layout every other target repo follows.
- `CHANGELOG.md` -- the `[Unreleased]` entry citing issue #301.
