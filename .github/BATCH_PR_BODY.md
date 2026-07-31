# Batch PR body

This file is the body the operator pastes into the single batch PR when the
deferred single-branch run opens it (spec S9: one target repo, one branch,
one deferred PR covering the whole E1-E6/E8 dependency-and-cleanup run).

Paste everything below the `---` separator verbatim into the PR description
field, then open the PR.

---

## Summary

This PR closes the E1-E6 run staged on the single deferred branch (spec S9),
one clause per epic:

- **E1 -- SDK upgrade.** Advances the `claude-agent-sdk` lock past the
  cancel-scope teardown race floor and removes the now-dead
  `sdk_teardown_filter` workaround.
- **E2 -- quota wait-and-resume (ADR-24).** Adds `devbench start` mid-session
  quota-exhaustion detection, an on-disk pause checkpoint, the
  `quota_handling` config block, the `devbench quota-watcher` command, and
  the Slack pause/resume events so an unattended overnight run survives an
  Anthropic quota window instead of exiting.
- **E3 -- model refresh.** Applies the current Sonnet/Opus lineup, moves the
  default fallback model to Opus 5, and records issue #254 (Opus 4.8) as
  superseded by that change.
- **E4 -- genuinely-observed TDD RED gate (#257).** Adds the orchestrator-only
  `RED_OBSERVED` evidence record so a RED can no longer be an unverified
  agent claim, adds judge-side detection of false fixes and missing
  evidence, and flattens the review-team spawn topology so all four
  review-team judges are attributable rather than possibly never executed.
- **E5 -- truthful reporting.** Makes `devbench report`, `status`, and
  `--help` say what they mean: an actionability banner instead of a bare
  count, process-authoritative liveness instead of log-recency guessing,
  and the scope-filter flags documented in `--help`.
- **E6 -- dependabot reconciliation (this task).** Reconciles the full
  dependabot backlog against the resolved lock: six targets already
  satisfied by the `mcp`-family advance have their dependabot PRs closed
  unmerged; the two remaining independent targets (`idna`, `urllib3`) are
  bumped explicitly.

Every issue closed by E1-E6 is listed below with a GitHub closing keyword so
merging this PR closes them automatically. E7 (#270, observability
hardening) targets the same `feat/updates` branch and is queued behind E6,
but its work units are not yet done as of this PR body's authoring, so no
E7 issue carries a closing keyword here; E7 will land in a later commit on
this same branch, either folded into this PR before it is opened or as a
follow-up PR, and its issues will be closed at that point instead.

## Closes

Closes #255
Closes #231
Closes #232
Closes #235
Closes #236
Closes #234
Closes #257
Closes #254
Closes #260
Closes #259
Closes #251
Closes #250
Closes #252
Closes #249

## References

The following dependabot PRs are referenced for traceability only; they
carry no closing keyword because they are pull requests to be closed by the
dependabot flow (superseded, closed unmerged) or resolved through this PR's
lock change (bumped), not issues this PR closes.

- Superseded, closed unmerged (satisfied by the `mcp`-family lock advance):
  #287, #278, #277, #276, #275, #274.
- Resolved via explicit `uv lock --upgrade-package` bump in this run: #216
  (idna), #179 (urllib3).
