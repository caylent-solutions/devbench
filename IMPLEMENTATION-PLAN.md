# Implementation Plan: `devbench supervise` (interactive screen orchestrator)

> Glyph note: this file uses ASCII double-hyphen `--` everywhere a dash is needed. The em-dash glyph (U+2014) does not appear in this file.

Spec: `spec/devbench-supervise-screen-orchestrator/devbench-supervise-screen-orchestrator.md`
(32 FRs, 34 ACs) + companion `QUOTA-VERIFICATION-TODO.md`.

This plan decomposes the spec into ordered phases, each sized for one agent. Every
phase is TDD (failing tests first), fail-fast, config/env-driven (no hardcoding), and
ends with a GREEN `env -u BASH_ENV -u DEVBENCH_WORKSPACE_ROOT make validate` (which
includes the 98% coverage floor). 100% coverage is the spec target for the NEW
`supervise.py` + `cmd_supervise` wiring (Section 10).

This plan IS the contract subsequent agents follow. A later phase MUST NOT begin until
the prior phase's `make validate` is green and committed.

---

## Baseline (established this run, before any change)

- `uv sync` clean; `env -u BASH_ENV -u DEVBENCH_WORKSPACE_ROOT make validate` GREEN.
- Test count: 7919 passed, 8 skipped. Coverage TOTAL: 98.02% (floor 98%).
- Branch: `feat/flatten-review-pipeline`. All work lands on this branch.

---

## Reuse map (DO NOT reinvent -- Section 3 of the spec)

These existing symbols are reused verbatim by later phases. Every phase below cites
which it touches.

- Dispatch: `cli._COMMANDS`, `cli._VARIADIC_COMMANDS`, `cli.main()` (`cli.py:12660,12996,13210`).
- Config: `config_loader.RuntimeConfig`/`load_runtime_config`/`jsonschema.validate(raw, _SCHEMA)`;
  `_parse_quota_handling_config` is the template for `_parse_supervise_config`
  (`config_loader.py:984`). Env precedence: `config.py` `_resolve_int/_resolve_str/_resolve_bool`.
- Model resolution: `cli._resolve_orchestrator_model` (`cli.py:7148`),
  `config_loader.validate_agent_model_value` (`config_loader.py:1455`),
  `constants.ALLOWED_AGENT_MODEL_SHORT_NAMES` (`constants.py:925`).
- Registry shape: `session.SessionRegistry` (atomic temp-rename, liveness via
  `os.kill(pid,0)`) (`session.py:155-364`). New `SuperviseRegistry` MIRRORS it but is
  SEPARATE (D-8).
- Scope: `scope.ScopeFilter.parse(include, exclude, backlog_ids)` (`scope.py:328`),
  `scope.ScopeFilter.to_file(workspace, path=...)` (`scope.py:397`),
  `scope.session_scope_file_path` (`scope.py:82`).
- Multi-session: `session.flock_backlog` (`session.py:372`),
  `session.detect_scope_overlap` (`session.py:437`), `session.ClaimRaceError`.
- Drain: `drain.request_drain/read_drain_state/consume_drain/cancel_drain/resolve_drain_signal_path`.
- Quota (REUSED, not reimplemented -- FR-15): `quota.wait_for_reset` (`quota.py:540`),
  `quota.detect_quota_error`/`_QUOTA_MARKERS`/`_RESET_AT_RE` (`quota.py:397,49,66`),
  `quota.QuotaCheckpoint` (`quota.py:674`), `cli._resolve_max_quota_resumes`
  (`cli.py:8324`), `constants.DEFAULT_MAX_QUOTA_RESUMES` (`constants.py:799`).
- Plugin: `plugin_shadow.shadow_plugin_path`/`materialise_shadow_plugin`,
  `cli._resolve_plugin_path` (`cli.py:7041`).
- Auth: `~/.claude/.credentials.json`, `config.get_anthropic_api_key` (`config.py:860`).
- Harness guard: `cli._check_harness_integrity`, `HARNESS_INTEGRITY_MARKER`.

---

## Phase 1 -- Foundations: deps, config block, registry, CLI skeleton

**Status: implemented this run.**

Files:
- `pyproject.toml` -- add `pexpect>=4.9.0` to `[project].dependencies` (FR-22); `uv sync`.
- `src/devbench/constants.py` -- add `SUPERVISE_BASE_DIR`, `SUPERVISE_REGISTRY_PATH`,
  `SUPERVISE_STATE_FILENAME`, `SUPERVISE_PTY_LOG_FILENAME`, `SUPERVISE_STOP_REQUEST_FILENAME`,
  `SUPERVISE_REGISTRY_TMP_SUFFIX`, `SUPERVISE_SCREEN_NAME_PREFIX_DEFAULT`,
  `SUPERVISE_EFFORT_DEFAULT`, the always-deny env-var set, the default detection-pattern
  set, the default injectable-command map (FR-17, Section 5.5).
- `src/devbench/config_loader.py` -- new `SuperviseConfig` (+ nested
  `SuperviseTimeoutsConfig`, `SuperviseRestartConfig`, `SuperviseQuotaConfig`,
  `SuperviseDetectionPatternsConfig`, `SuperviseLogTailConfig`, `SuperviseEnvConfig`,
  `SuperviseLoggingConfig`) dataclasses; `_parse_supervise_config` (modeled on
  `_parse_quota_handling_config`); wire `supervise` field into `RuntimeConfig` +
  `load_runtime_config`. Always-deny whitelist attempt is a Python-level fail-fast.
- `src/devbench/config-schema.json` -- add the `supervise` block to root `properties`
  (root has `additionalProperties:false`, so unknown `supervise.*` keys are rejected).
- `src/devbench/supervise.py` -- NEW module; Phase 1 lands `SuperviseRegistry` +
  `SuperviseSessionState` dataclass + state-dir/path helpers ONLY (mirrors `session.py`).
- `src/devbench/cli.py` -- register `supervise` in `_COMMANDS` + `_VARIADIC_COMMANDS`;
  add `cmd_supervise(*argv)` dispatcher over the six sub-verbs + the hidden `__run`;
  each sub-verb body is a thin stub that validates args (`--name` grammar, unknown
  sub-verb -> exit 2 with the documented usage) and fails fast with a clear
  NotImplemented-style error for the not-yet-built bodies. The arg parser
  (`_parse_supervise_args`) is full (it lands now so later phases only fill bodies).

Tests (failing first):
- `tests/test_supervise_config.py` -- AC-12: defaults, schema validate, unknown-key
  reject, always-deny whitelist fail-fast, env override precedence.
- `tests/test_supervise_registry.py` -- FR-17: save/load round-trip, atomic write,
  liveness, stale-reaping, per-session state-dir + path helpers, multi-session listing.
- `tests/test_supervise_cli_dispatch.py` -- FR-1/FR-2: verb registered; sub-verb
  dispatch; unknown sub-verb -> exit 2 + usage; `--name` grammar validation
  (`^[A-Za-z0-9][A-Za-z0-9_-]*$`, reject `..`); arg parser maps every flag; the
  not-yet-built bodies fail fast with a clear error (NOT silently).

FR/AC coverage: FR-1, FR-2 (partial -- parsing), FR-17, FR-19 (config parse + schema),
FR-22, FR-28 (registry of injectable commands lands in config). AC-12 fully; AC-22
fully; AC-2/AC-8/AC-11 partially seeded (state/types exist).

Gate: `make validate` GREEN. Commit + push.

Phase 1 deliberately does NOT launch claude/screen or run pexpect (that is P2/P5/P6).

---

## Phase 2 -- Launch + pexpect supervisor core

Files: `src/devbench/supervise.py` (grow), `src/devbench/cli.py` (`cmd_supervise start`
body + hidden `__run` body).

Implements (Section 4.0 decomposition):
- `EnvSanitizer` -- builds the minimized screen env: copy of os.environ minus the
  always-deny set + config `env.deny_vars`, plus the three scope-conveyance exports
  (`DEVBENCH_WORKSPACE_ROOT`, `DEVBENCH_SESSION_NAME`, `DEVBENCH_CLAUDE_MODEL`)
  (FR-21, Section 3.6.1, Section 5.6).
- `AuthVerifier` -- subscription-auth preflight (credentials file + `user:inference`
  scope) + API-key-present guard + non-root assert (FR-20, FR-21, Section 3.6.2).
- `PtyDriver` -- thin `pexpect.spawn` wrapper: launch, `expect(patterns, timeout)`,
  `sendline`, `before`/`after`, `terminate`, tee redacted PTY -> `pty.log` (0600).
- `CommandInjector` -- the injectable-command registry sender (FR-28); unknown name
  fail-fast.
- `SupervisorStateMachine` -- pure state machine, no I/O (Section 4.8, FR-27).
- `start` preflight pipeline (FR-23 screen-present, FR-25 claude path+version,
  FR-19 model/effort resolve, Section 3.6.2 non-root); scope expand + write
  session-routed scope.json via `ScopeFilter.to_file` (FR-8, Section 5.6, step 4a);
  `screen -dmS <prefix><name>` then `supervise __run`; `__run` spawns
  `claude --model --effort --dangerously-skip-permissions --plugin-dir <resolved>`,
  waits for ready (hybrid PTY+log), injects `/devbench-orchestrate:orchestrate`,
  transitions to `running`.

Tests (failing first): `test_supervise_env_guard.py` (AC-4), `test_supervise_preflight.py`
(AC-5/6/7), `test_supervise_model_resolution.py` (AC-8), `test_supervise_state_machine.py`
(AC-1), `test_supervise_detection.py` (AC-3), `test_supervise_inject.py` (AC-11),
`test_supervise_redaction.py` (AC-21), `test_supervise_scope_default.py` (AC-31).
Uses `FakePexpectChild` double (Section 10.0).

FR/AC: FR-3, FR-4, FR-5(launch), FR-6, FR-7, FR-8, FR-19, FR-20, FR-21, FR-23, FR-24,
FR-25, FR-27(core), FR-28, FR-29(patterns). AC-1,3,4,5,6,7,8,11,21,31.

Gate: `make validate` GREEN. Commit + push.

**Phase 2 should do X (handoff target):** start here next. The skeleton, config, and
registry from Phase 1 exist; Phase 2 fills the `start`/`__run` bodies and the supporting
classes. The `FakePexpectChild` double + stub-claude fixture are introduced here (the
stub-claude functional driver is fully exercised in P5).

---

## Phase 3 -- Quota wait-and-resume adapter (DRY) + restart + exit taxonomy

Files: `src/devbench/supervise.py` (grow), `cli.py` (`restart` body, exit-code propagation).

Implements:
- `QuotaWaiter` -- THIN adapter (FR-15, Section 4.9): delegates wait to
  `quota.wait_for_reset`, classification to `quota.detect_quota_error`, cap to
  `cli._resolve_max_quota_resumes`, checkpoint to `quota.QuotaCheckpoint`. New logic is
  ONLY the PTY-prompt detection + in-session-wait-vs-poll-restart branch. AC-32 asserts
  the same callables are invoked (no local copies).
- `LogTailDetector` -- tails orchestrator log for the Section 1.6 markers (hybrid).
- Restart loop (FR-12, Section 4.3): bounded by `supervise.restart.max_attempts`;
  relaunch with `--continue`/`--resume`. `cmd_supervise restart` body.
- Exit taxonomy (FR-13, Section 4.6): classify clean/fault/quota; quota does NOT exit.

Tests (failing first): `test_supervise_quota.py` (AC-9), `test_supervise_quota_reuse.py`
(AC-32), `test_supervise_restart.py` (AC-10), `test_supervise_exit_taxonomy.py` (AC-2),
plus the ADR-24 false-positive negative test (Section 10.2).

FR/AC: FR-12, FR-13, FR-14, FR-15, FR-16, FR-27(quota/restart transitions). AC-2,9,10,32.

Gate: `make validate` GREEN. Commit + push.

---

## Phase 4 -- status / info / attach (read-only) + stop

Files: `cli.py` (`status`/`info`/`attach`/`stop` bodies), `supervise.py` (read helpers).

Implements:
- `supervise status` (FR-9, FR-10): per-session + all-session; `quota-waiting` with
  `expected-resume` + `resumes-used`; `billing-channel: subscription`.
- `supervise info` (FR-11): join `screen -ls` with registry; orphan/stale reconcile;
  the exact `attach --name N` command column.
- `supervise stop` (FR-5, Section 4.2): graceful drain via `drain.request_drain` +
  `stop.request` control file; `--hard`; stale-screen reconcile.
- `supervise attach` (FR-26, Section 4.7): read-only redacted `pty.log` follow; stdin
  NEVER wired to the child; `--screen` fails fast (gated on DI-4, AC-33).

Tests (failing first): `test_supervise_attach_screen_gated.py` (AC-33); status/info/stop
unit tests. (The live-process functional attach/stop tests AC-18/19/20 land in P5.)

FR/AC: FR-5(stop), FR-9, FR-10, FR-11, FR-26. AC-33 (+ status/info/stop units).

Gate: `make validate` GREEN. Commit + push.

---

## Phase 5 -- Functional layer: stub `claude` + screen-less functional tests

Files: `tests/fixtures/supervise/stub-claude.py` (executable real CLI fixture),
`tests/functional/test_supervise_*.py`.

Implements the stub-claude (Section 10.0): prints a configurable ready prompt, accepts
slash commands on stdin, and (env/script-driven) emits `ALL_DONE`/`NO_ACTIONABLE`/crash/
quota-prompt+reset/exit-42-equivalent. Drives the start->/orchestrate->terminal paths
through the real pexpect supervisor (no real claude, no real subscription) -- screen is
stubbed or bypassed where it cannot run in CI.

Tests: AC-13 (clean), AC-14 (fault), AC-15 (quota-wait, test-shortened window), AC-16
(autorestart), AC-17 (multisession), AC-18 (attach read-only), AC-19 (stop), AC-20
(restart-resumes), AC-30 (scope conveyance end-to-end with `devbench next`).

FR/AC: exercises FR-3..FR-32 functionally. AC-13..20, AC-30.

Gate: `make validate` GREEN. Commit + push.

---

## Phase 6 -- Real integration on a dummy backlog + docs + ADR-31

Files: `tests/fixtures/supervise/dummy-backlog/`, `docs/supervise.md` (NEW),
`docs/adr/31-interactive-screen-supervisor.md` (NEW), edits to `docs/cli-reference.md`,
`docs/architecture.md`, `docs/execution-modes.md`, `docs/devbench-yaml-reference.md`,
`docs/llm-authentication.md`; optional `make supervise` target; `sample-config.yaml`.

Implements: the deferred-AC integration harness (AC-23..29, AC-34 are deferred -- they
require a live subscription/claude/screen/quota event; mark them deferred per the spec)
and ALL documentation (FR-31 -- the feature is "done" only when docs + ADR-31 ship in the
same change). Records DI outcomes (DI-1 effort-flag, DI-3 scope-conveyance, DI-4 screen
ACL gating, DI-5 quota prompt per QUOTA-VERIFICATION-TODO.md) for operator review.

FR/AC: FR-31. AC-23..29 + AC-34 (deferred markers); docs.

Gate: `make validate` GREEN. Commit + push. Final feature review.

---

## Cross-cutting standards (every phase)

- TDD: failing tests committed/observed before the implementation makes them pass.
- Fail-fast, no fallback, no silent failure (CLAUDE.md). Every error has an actionable
  stderr message + non-zero exit (FR-30).
- No hardcoded values: every operational value is a config field + `DEVBENCH_SUPERVISE_*`
  env override (Section 7.4), defaults in `constants.py`.
- No `# noqa`/`# type: ignore`/`# nosec`/`# pragma: no cover`; no `--no-verify`.
- No `time.sleep` for synchronization (pexpect `expect` timeouts / `quota.wait_for_reset`).
- No em-dash (U+2014); ASCII `--` only. No `Co-Authored-By`.
- Stage only files changed in the phase.

## Open spec ambiguities flagged for operator review

1. **DI-5 (quota prompt strings, HIGHEST RISK):** `detection_patterns.quota_limit` /
   `quota_wait_prompt` / `injectable_commands.quota_wait_choice` are PLACEHOLDERS until a
   real 5-hour-window event is captured (QUOTA-VERIFICATION-TODO.md). Phases ship the
   config-driven machinery with placeholder defaults; the in-session-wait path (4.9a) is
   best-effort until DI-5; poll-restart (4.9b) + log markers carry correctness meanwhile.
2. **DI-1 (`--effort xhigh` flag):** unverified that the installed CLI accepts
   `--effort xhigh` alongside `--dangerously-skip-permissions`. Config provides both the
   flag form and the `/effort xhigh` injection fallback (D-11); the flag is tried first.
3. **DI-4 (`--screen` attach ACL):** `--screen` stays fail-fast-disabled (AC-33) until a
   human verifies the write-removed ACL blocks all input on the target screen build.
4. **`__run` hidden sub-verb (D-10):** internal/undocumented; confirm acceptable vs a
   separate entrypoint module (the plan uses the hidden sub-verb).
