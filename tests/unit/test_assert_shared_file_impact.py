"""Unit tests for the assert-shared-file-impact PostToolUse hook script.

Issue caylent-solutions/devbench-internal-backlog#13 (shared-file full-suite
regression gate): this hook enforces `devbench check-shared-file-impact`'s
verdict via the same PostToolUse-blocking mechanism `assert-tests-pass.sh`
already uses for `run-tests` / `pytest` / `make test` exit codes -- a
blocking verdict blocks silent progression rather than being an advisory
instruction an agent can skip under time pressure.

ROUND-5 REDESIGN (spec 4.6 finding 318-D13, code_review round-4 JUDGEMENT
comment on this work unit): four prior review rounds each replaced one
sed/jq heuristic for re-deriving this hook's verdict from the Claude Code
PostToolUse payload -- first from a nonexistent `tool_response.exit_code`
field, then from `tool_input.command` (a bare substring test, then a
quoted-region deletion matcher, then a tokenised match still defeated by
`bash -lc` style wrapper forms and an apostrophe-sandwich quoting edge
case) and `tool_response.stdout` (a tiered JSON-document scan defeated by
a decapitated block fragment coexisting with an unrelated complete
document). This test file replaces every one of the payload-parsing tests
those rounds accumulated: `cmd_check_shared_file_impact`
(`src/devbench/cli.py`, ref) now persists its own verdict to a small
plain-text record file (`_write_shared_file_impact_verdict`, ref) as the
very first thing it does, and this hook's entire job is reading that ONE
record back -- no `tool_input.command` or `tool_response` field is read or
needed any more (see the script's own module header, ref, for the full
contract). Every test below therefore exercises one of two independent
axes: (1) what the hook does with a given RECORD state, and (2) proof that
the PAYLOAD content on stdin has become irrelevant to that decision --
including replays of the exact wrapper-form / quoting-edge-case adversarial
payloads earlier rounds' matchers failed on, now inert by construction
because there is no command matcher left to defeat.

`TestAssertSharedFileImpactHookRegistration` (AC-6, spec Section 10) is
unaffected by the redesign and is carried over unchanged.

AC-5 interpretive note, restated here for this test file specifically (round-5
finding E2; full argument on the script's own module header, ref): AC-5's
literal wording ("a payload with a missing exit code, a missing command or
unparseable JSON makes the hook exit 2") and the Definition of Done's
"sources `_hook_lib.sh`" line both describe the PAYLOAD-PARSING mechanism this
redesign deliberately removes -- read literally, neither clause is satisfiable
by this design, and this file's own
`test_truncated_non_json_stdin_with_a_pass_record_still_allows` deliberately
asserts rc 0 for unparseable stdin given a `"pass"` record, the opposite of
AC-5's literal "exit 2" wording. This file (and every sibling class still
headed "AC-5") satisfies AC-5's INTENT instead -- fail closed whenever the
hook cannot determine a real pass/block verdict -- not its literal
payload-parsing text; see the script header's own intent argument for why no
implementation of this redesign can satisfy the literal clause.
"""

from __future__ import annotations

import json
import os
import subprocess
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

import pytest

from devbench import cli

_PLUGIN_ROOT = Path(__file__).parent.parent.parent / "plugin" / "devbench-orchestrate"
SCRIPT_PATH = _PLUGIN_ROOT / "scripts" / "assert-shared-file-impact.sh"
HOOKS_JSON_PATH = _PLUGIN_ROOT / "hooks" / "hooks.json"


def _clean_env(workspace_root: Path | None, *, session_name: str | None = None) -> dict[str, str]:
    """Return a process env with ambient session/workspace vars replaced deterministically.

    Strips whatever `DEVBENCH_WORKSPACE_ROOT` / `DEVBENCH_SESSION_NAME` /
    `DEVBENCH_LOG_FILE` the pytest host process happens to carry (this repo
    IS a live devbench workspace, so all three are typically set) before
    substituting the values the test actually wants -- otherwise a test
    asserting "no session" behaviour could accidentally inherit the real
    host session name and silently pass for the wrong reason.

    Args:
        workspace_root: Value to set `DEVBENCH_WORKSPACE_ROOT` to, or
            `None` to leave it UNSET entirely (the one narrow fail-open
            case the script's header documents).
        session_name: Value to set `DEVBENCH_SESSION_NAME` to, or `None`
            to leave it unset (no active session).
    """
    env = {
        k: v
        for k, v in os.environ.items()
        if k not in ("DEVBENCH_WORKSPACE_ROOT", "DEVBENCH_SESSION_NAME", "DEVBENCH_LOG_FILE")
    }
    if workspace_root is not None:
        env["DEVBENCH_WORKSPACE_ROOT"] = str(workspace_root)
    if session_name is not None:
        env["DEVBENCH_SESSION_NAME"] = session_name
    return env


def _run_hook(
    *,
    workspace_root: Path | None,
    session_name: str | None = None,
    stdin_text: str = "{}",
) -> subprocess.CompletedProcess:
    """Invoke the hook script once, with the given env and stdin.

    *stdin_text* defaults to a minimal, well-formed-but-empty JSON object:
    the redesigned hook never reads `tool_input`/`tool_response` fields
    from it at all (see the module docstring), so most tests never need to
    vary it -- the tests that DO vary it
    (`TestAssertSharedFileImpactHookIgnoresPayloadContent`) do so precisely
    to prove that variation has no effect on the outcome.
    """
    return subprocess.run(
        ["bash", str(SCRIPT_PATH)],
        input=stdin_text,
        capture_output=True,
        text=True,
        env=_clean_env(workspace_root, session_name=session_name),
    )


@contextmanager
def _temporary_session_name(session_name: str | None) -> Iterator[None]:
    """Temporarily set (and always restore) THIS PROCESS's own `DEVBENCH_SESSION_NAME`.

    Test-review round-6 warn W3: this save/set/restore idiom used to be hand-rolled
    three separate times in this file (in `_record_path` and in two
    `TestAssertSharedFileImpactHookSessionRoutingCrossLayerParity` tests) even
    though `monkeypatch.setenv`/`monkeypatch.delenv` already cover the identical
    need elsewhere in the suite; a plain `try`/`finally` context manager here
    (rather than `monkeypatch`, which is fixture-scoped and not available to the
    module-level `_record_path` helper) removes the duplication in one place.
    `None` means "no session active" (the variable is unset, mirroring
    `cli._session_state_file_path` treating an absent/whitespace-only value the
    same way), matching every call site's own `session_name is None` convention.
    """
    saved = os.environ.get("DEVBENCH_SESSION_NAME")
    try:
        if session_name is None:
            os.environ.pop("DEVBENCH_SESSION_NAME", None)
        else:
            os.environ["DEVBENCH_SESSION_NAME"] = session_name
        yield
    finally:
        if saved is None:
            os.environ.pop("DEVBENCH_SESSION_NAME", None)
        else:
            os.environ["DEVBENCH_SESSION_NAME"] = saved


def _record_path(workspace_root: Path, *, session_name: str | None = None) -> Path:
    """Return the on-disk verdict-record path for *workspace_root* / *session_name*.

    Delegates to the REAL `cli._shared_file_impact_verdict_path` -- the exact
    function `cmd_check_shared_file_impact` calls -- rather than
    reimplementing the `DEVBENCH_SESSION_NAME` routing/strip/`..`-guard rule a
    second time on the test side. A prior hand-rolled reimplementation here
    is precisely why the A2 shell-vs-Python whitespace-strip divergence and
    the A3 substring-vs-path-segment `..` divergence were both invisible to
    this file's own 35+ passing tests across four review rounds: this
    helper agreed with the (buggy) shell script's rule instead of the real
    Python one, so a real-world regression in the SHELL layer alone could
    never surface here.

    Temporarily sets (and always restores) `DEVBENCH_SESSION_NAME` in this
    process's own environment for the duration of the call, since
    `cli._shared_file_impact_verdict_path` reads it directly rather than
    accepting it as a parameter.
    """
    with _temporary_session_name(session_name):
        return cli._shared_file_impact_verdict_path(workspace_root)


def _write_record(
    workspace_root: Path,
    status: str,
    *,
    unit_id: str = "E0-F1-S1-T1",
    session_name: str | None = None,
    timestamp: str = "2026-01-01T00:00:00+00:00",
    extra_lines: tuple[str, ...] = (),
) -> Path:
    """Write a verdict record in the 3-line shape (status/unit_id/timestamp) the
    hook itself actually reads.

    `_write_shared_file_impact_verdict` now writes a 4th line (a per-invocation
    correlator, round-5 finding A1 family, only ever consumed by that same
    function's own non-clobbering guard -- see its docstring); this fixture
    deliberately does NOT reproduce that 4th line, since the hook this file tests
    only ever reads lines 1-2 and this 3-line shape remains valid input for every
    test in this file that only cares about the hook's own read-side behaviour
    (`TestAssertSharedFileImpactHookBlockThenPassNonClobbering` and
    `TestAssertSharedFileImpactHookPendingThenPassNonClobbering` use the REAL
    `cli._write_shared_file_impact_verdict` instead, precisely because they DO
    need the non-clobbering guard -- and therefore the real 4-line record -- to
    be exercised).

    Args:
        workspace_root: The workspace root the record is written under.
        status: Line 1 -- the status token (`"pending"`/`"pass"`/`"block"`,
            or a deliberately-invalid value for a fail-closed test case).
        unit_id: Line 2 -- diagnostics only.
        session_name: When given, writes to the `sessions/<name>/` subdirectory
            path instead of the workspace-root path.
        timestamp: Line 3 -- diagnostics only.
        extra_lines: Additional lines appended after the standard three,
            for a test that needs to prove trailing content is ignored.

    Returns:
        The path the record was written to.
    """
    path = _record_path(workspace_root, session_name=session_name)
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = [status, unit_id, timestamp, *extra_lines]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


# Real PostToolUse Bash `tool_input.command` values captured in this repo's
# own `hook-logs.jsonl` on 2026-08-26 -- genuine mentions of the gate's name
# that never invoke it -- with leading (`cd .../devbench && `) and/or
# trailing (e.g. a chained `; echo ---; grep ...` clause) boilerplate
# trimmed for fixture length; the substantive `grep` invocation each
# fixture is built from is otherwise unmodified. Used only by
# `TestAssertSharedFileImpactHookIgnoresPayloadContent` below to prove real
# command text no longer has any bearing on the hook's decision.
_REAL_LOGGED_MENTION_COMMANDS: tuple[str, ...] = (
    'grep -rn "check-shared-file-impact\\|check-fixture-consistency" src/devbench/cli.py | head -20',
    'grep -n "check-reachability\\|check-shared-file-impact\\|check-fixture-consistency" docs/cli-reference.md',
)


@pytest.mark.unit
class TestAssertSharedFileImpactHook:
    """Mandatory first test (spec Section 10): the script exists and is executable."""

    def test_script_exists_and_is_executable(self) -> None:
        assert SCRIPT_PATH.exists(), f"Script not found at {SCRIPT_PATH}"
        assert os.access(SCRIPT_PATH, os.X_OK), f"Script is not executable: {SCRIPT_PATH}"


@pytest.mark.unit
class TestAssertSharedFileImpactHookNoRecord:
    """No verdict record on disk -- this hook has no unresolved decision to make and
    allows, with no payload inspection of any kind (round-5 redesign)."""

    def test_no_devbench_directory_at_all_allows(self, tmp_path: Path) -> None:
        workspace = tmp_path / "workspace"
        workspace.mkdir()
        assert not (workspace / ".devbench").exists()
        result = _run_hook(workspace_root=workspace)
        assert result.returncode == 0
        assert result.stderr == ""

    def test_devbench_directory_present_but_no_record_file_allows(self, tmp_path: Path) -> None:
        workspace = tmp_path / "workspace"
        (workspace / ".devbench").mkdir(parents=True)
        result = _run_hook(workspace_root=workspace)
        assert result.returncode == 0

    def test_missing_devbench_workspace_root_allows(self) -> None:
        """The one narrow, documented fail-open exception (script header, ref): a
        completely absent `DEVBENCH_WORKSPACE_ROOT` is an environment-misconfiguration
        condition (mirrors `guard-git-stage.sh`'s existing "no context, skip
        enforcement" convention), never reachable in a real devbench-managed session
        (`config.py::_require_env` enforces the var at CLI import time for every
        `devbench` invocation)."""
        result = _run_hook(workspace_root=None)
        assert result.returncode == 0


@pytest.mark.unit
class TestAssertSharedFileImpactHookVerdictSemantics:
    """AC-5 (spec 3.5, 4.6, finding 318-D13): the hook's decision is a pure function of
    the verdict-record's own first line."""

    def test_pass_record_allows(self, tmp_path: Path) -> None:
        workspace = tmp_path / "workspace"
        _write_record(workspace, "pass")
        result = _run_hook(workspace_root=workspace)
        assert result.returncode == 0
        assert result.stderr == ""

    def test_block_record_blocks_and_names_the_unit(self, tmp_path: Path) -> None:
        workspace = tmp_path / "workspace"
        _write_record(workspace, "block", unit_id="E7-F2-S1-T3")
        result = _run_hook(workspace_root=workspace)
        assert result.returncode == 2
        assert "assert-shared-file-impact" in result.stderr
        assert "verdict: block" in result.stderr
        assert "E7-F2-S1-T3" in result.stderr

    def test_pending_record_blocks_fail_closed(self, tmp_path: Path) -> None:
        """The write-then-overwrite contract (`_write_shared_file_impact_verdict`, ref):
        every error-return path in `cmd_check_shared_file_impact` leaves the record at
        `"pending"` on purpose -- a run that started but whose verdict could not be
        determined (spec 3.5) blocks rather than being guessed as safe."""
        workspace = tmp_path / "workspace"
        _write_record(workspace, "pending")
        result = _run_hook(workspace_root=workspace)
        assert result.returncode == 2
        assert "never recorded a pass/block verdict" in result.stderr

    @pytest.mark.parametrize(
        "status",
        ["", "unknown", "PASS", "Block", "warn", "0", "null"],
        ids=[
            "empty",
            "unrecognised-word",
            "wrong-case-pass",
            "wrong-case-block",
            "warn",
            "numeric",
            "json-null-literal",
        ],
    )
    def test_unrecognised_status_value_blocks_fail_closed(self, tmp_path: Path, status: str) -> None:
        """Only the exact tokens `pass`/`block` allow/report a block; anything else --
        including a case variant of a real token, since the record is a devbench-owned
        format with no external producer that could ever legitimately vary case -- is
        treated the same as `"pending"`: fail closed, never silently allowed."""
        workspace = tmp_path / "workspace"
        _write_record(workspace, status)
        result = _run_hook(workspace_root=workspace)
        assert result.returncode == 2

    def test_record_missing_unit_id_line_still_blocks_with_a_placeholder(self, tmp_path: Path) -> None:
        """A record with only a status line (no line 2) -- reachable only if a future
        writer ever produced a malformed record -- must not crash this hook; it still
        fails closed for a `block`/unrecognised status, substituting a placeholder for
        the missing diagnostic unit id."""
        workspace = tmp_path / "workspace"
        record = _record_path(workspace)
        record.parent.mkdir(parents=True)
        record.write_text("block\n", encoding="utf-8")
        result = _run_hook(workspace_root=workspace)
        assert result.returncode == 2
        assert "<unknown unit>" in result.stderr

    def test_trailing_lines_after_the_first_three_are_ignored(self, tmp_path: Path) -> None:
        """Only line 1 (status) and line 2 (unit id, diagnostics-only) are read; extra
        trailing content (forward-compatibility: a future writer adding a 4th field)
        must never change today's verdict."""
        workspace = tmp_path / "workspace"
        _write_record(workspace, "pass", extra_lines=("some-future-field=value",))
        result = _run_hook(workspace_root=workspace)
        assert result.returncode == 0


@pytest.mark.unit
class TestAssertSharedFileImpactHookStalenessBound:
    """How a stale record is bounded from blocking forever (script header, ref, A4):
    this hook branches on the record's status FIRST, then consumes (reads and
    deletes) it as part of reporting that decision -- rather than unlinking it
    unconditionally before branching -- so a failed consume (e.g. a non-writable
    directory) can still fail closed with this hook's own controlled message
    instead of a bare `rm` error. Consuming still happens on every path, so a
    record still affects at most until the next Bash PostToolUse event this hook
    actually receives. "The next PostToolUse firing" is NOT the same event as
    "the next Bash call": hooks.json (ref) registers this script on `PostToolUse`
    for the `Bash` tool only, and a Bash call that exits non-zero emits
    `PostToolUseFailure` instead, which this hook is not registered for and never
    sees (measured directly against this repo's own hook-logs.jsonl: 24,181 Bash
    `PostToolUse` events versus 228 Bash `PostToolUseFailure` events) -- so an
    intervening non-zero-exit Bash call can push the consuming event later than
    the very next Bash call."""

    def test_block_record_is_consumed_and_a_second_firing_allows(self, tmp_path: Path) -> None:
        workspace = tmp_path / "workspace"
        record = _write_record(workspace, "block")
        first = _run_hook(workspace_root=workspace)
        assert first.returncode == 2
        assert not record.exists(), "the record must be deleted (consumed) by the firing that read it"

        second = _run_hook(workspace_root=workspace)
        assert second.returncode == 0, "with the record already consumed, a second firing has nothing left to block on"

    def test_pending_record_is_consumed_even_though_it_blocks(self, tmp_path: Path) -> None:
        workspace = tmp_path / "workspace"
        record = _write_record(workspace, "pending")
        first = _run_hook(workspace_root=workspace)
        assert first.returncode == 2
        assert not record.exists()
        second = _run_hook(workspace_root=workspace)
        assert second.returncode == 0

    def test_pass_record_is_also_consumed(self, tmp_path: Path) -> None:
        """Consumption is unconditional -- not only on the blocking branches -- so a
        `pass` record from a prior invocation can never be misread by a LATER,
        unrelated Bash call as if it were that later call's own fresh verdict."""
        workspace = tmp_path / "workspace"
        record = _write_record(workspace, "pass")
        _run_hook(workspace_root=workspace)
        assert not record.exists()


@pytest.mark.unit
class TestAssertSharedFileImpactHookSessionRouting:
    """Spec 4.4.4: `DEVBENCH_SESSION_NAME` routes the record path exactly the way
    `cli._session_state_file_path` routes `scope.json`, so two concurrent named
    sessions targeting the same workspace never share one verdict record."""

    def test_session_record_is_read_from_the_session_subdirectory(self, tmp_path: Path) -> None:
        workspace = tmp_path / "workspace"
        _write_record(workspace, "block", session_name="alpha")
        result = _run_hook(workspace_root=workspace, session_name="alpha")
        assert result.returncode == 2

    def test_workspace_root_record_is_not_seen_when_a_session_is_active(self, tmp_path: Path) -> None:
        """A record written WITHOUT a session (the workspace-root path) must not leak
        into a session-scoped hook invocation -- proves the two paths are genuinely
        isolated, not merely differently named."""
        workspace = tmp_path / "workspace"
        _write_record(workspace, "block")  # workspace-root path, no session
        result = _run_hook(workspace_root=workspace, session_name="alpha")
        assert result.returncode == 0, "a session-scoped hook must never read the workspace-root record"

    def test_session_record_is_not_seen_without_the_session_name(self, tmp_path: Path) -> None:
        workspace = tmp_path / "workspace"
        _write_record(workspace, "block", session_name="alpha")
        result = _run_hook(workspace_root=workspace, session_name=None)
        assert result.returncode == 0, "a no-session hook invocation must never read a session-scoped record"

    def test_path_traversal_session_name_blocks_fail_closed(self, tmp_path: Path) -> None:
        workspace = tmp_path / "workspace"
        result = _run_hook(workspace_root=workspace, session_name="../escape")
        assert result.returncode == 2
        assert "invalid" in result.stderr


@pytest.mark.unit
class TestAssertSharedFileImpactHookSessionRoutingCrossLayerParity:
    """Round-5 code_review/doc_review/test_review finding A2/A3: the shell script's
    `DEVBENCH_SESSION_NAME` routing must agree with `cli._shared_file_impact_verdict_path`
    on every ASCII-whitespace and `..`-path-segment case, not merely the one case
    (`../escape`) where a substring check and a path-segment check happen to give the
    same answer. Bounded, not universal (round-6 doc_review finding): Python's
    `str.strip()` also strips several non-ASCII whitespace code points (measured:
    U+001C, U+0085, U+00A0 among them) the shell guard's `[[:space:]]` class does not,
    so a `DEVBENCH_SESSION_NAME` padded with one of those specific code points is a
    real, narrow divergence NOT covered by (and not asserted by) the parametrizations
    below. These tests write the record via the REAL production write path
    (`cli._write_shared_file_impact_verdict`) and read it back via the REAL shell
    script (`_run_hook`), so a divergence between the two layers, for the cases these
    tests DO cover, shows up as the hook silently missing a record the Python layer
    wrote (or vice versa) rather than as a test-fixture assumption baked into both
    sides."""

    @pytest.mark.parametrize(
        "session_name",
        ["alpha", " alpha ", "  ", None],
        ids=["plain", "padded-with-whitespace", "whitespace-only", "unset"],
    )
    def test_python_write_and_shell_read_agree_on_the_record_path(
        self, tmp_path: Path, session_name: str | None
    ) -> None:
        """A2: `cli._session_state_file_path` strips `DEVBENCH_SESSION_NAME` with
        `str.strip()`; the shell script must strip it the same way, so a padded value
        (`' alpha '`) and an all-whitespace value (`'  '`, equivalent to unset) resolve
        to the SAME record path in both layers."""
        workspace = tmp_path / "workspace"
        with _temporary_session_name(session_name):
            cli._write_shared_file_impact_verdict(
                "block", workspace_root=workspace, unit_id="E0-F1-S1-T1", invocation_id="test-invocation"
            )

        result = _run_hook(workspace_root=workspace, session_name=session_name)
        assert result.returncode == 2, (
            f"session_name={session_name!r}: the shell script must find the record the real "
            "Python write path just wrote at the SAME resolved path"
        )
        assert "E0-F1-S1-T1" in result.stderr

    @pytest.mark.parametrize(
        "session_name",
        ["../escape", "a..b", "..", "x/../y"],
        ids=["leading-traversal", "embedded-dots-no-traversal", "bare-dots", "mid-path-traversal"],
    )
    def test_path_traversal_guard_agrees_with_python_on_every_case(self, tmp_path: Path, session_name: str) -> None:
        """A3: `cli._session_state_file_path` rejects only an exact `..` PATH SEGMENT
        (`".." in Path(session_name).parts`), not any `..` substring -- `a..b` is a
        valid (if unusual) session name, not a traversal attempt. The shell guard must
        implement the identical segment rule, not a substring rule, or the two layers
        disagree on which session names are legal."""
        workspace = tmp_path / "workspace"

        # `_shared_file_impact_verdict_path` reads `DEVBENCH_SESSION_NAME` from the
        # environment; set it exactly the way the hook script receives it.
        with _temporary_session_name(session_name):
            try:
                cli._shared_file_impact_verdict_path(workspace)
                python_rejects = False
            except ValueError:
                python_rejects = True

        result = _run_hook(workspace_root=workspace, session_name=session_name)
        shell_rejects = result.returncode == 2 and "invalid" in result.stderr

        assert shell_rejects == python_rejects, (
            f"session_name={session_name!r}: Python raises ValueError={python_rejects} but the "
            f"shell guard's fail-closed-with-'invalid' outcome={shell_rejects} -- the two layers "
            "must agree on every value, not just '../escape'"
        )


@pytest.mark.unit
class TestAssertSharedFileImpactHookBlockThenPassNonClobbering:
    """AC-5 (spec 3.5, 4.6): round-5 code_review/test_review finding A1, a REGRESSION
    against round 4. Two `check-shared-file-impact` invocations chained in a single Bash
    tool call (`unit-a ; unit-b`) fire only ONE PostToolUse event for the whole call, so
    a later PASSING unit's own `"pending"`/`"pass"` writes must never erase an earlier,
    unconsumed `"block"` verdict -- the hook must still block on that one shared
    PostToolUse event. Uses the REAL production write function
    (`cli._write_shared_file_impact_verdict`), not this file's own `_write_record` test
    fixture, since `_write_record` bypasses the non-clobbering guard entirely."""

    def test_block_then_pass_in_one_process_leaves_the_record_blocking(self, tmp_path: Path) -> None:
        workspace = tmp_path / "workspace"
        # Simulate `check-shared-file-impact <unit-a> ; check-shared-file-impact <unit-b>`
        # chained in one Bash call: unit A's invocation writes "pending" then "block";
        # unit B's invocation (a genuinely passing unit) then writes its own "pending"
        # then "pass" -- exactly what `cmd_check_shared_file_impact` does at the start
        # and end of each call.
        cli._write_shared_file_impact_verdict(
            "pending", workspace_root=workspace, unit_id="E9-F1-S1-T1", invocation_id="invocation-a"
        )
        cli._write_shared_file_impact_verdict(
            "block", workspace_root=workspace, unit_id="E9-F1-S1-T1", invocation_id="invocation-a"
        )
        cli._write_shared_file_impact_verdict(
            "pending", workspace_root=workspace, unit_id="E9-F2-S1-T2", invocation_id="invocation-b"
        )
        cli._write_shared_file_impact_verdict(
            "pass", workspace_root=workspace, unit_id="E9-F2-S1-T2", invocation_id="invocation-b"
        )

        record = workspace / ".devbench" / "shared-file-impact-verdict"
        assert record.read_text().splitlines()[0] == "block", (
            "an unconsumed 'block' verdict must never be clobbered by a later, unrelated unit's "
            "own 'pending'/'pass' writes within the same process -- only the hook consuming it "
            "(reading then deleting it) may clear it"
        )

        result = _run_hook(workspace_root=workspace)
        assert result.returncode == 2, "the real hook must still block on the surviving 'block' record"
        assert "E9-F1-S1-T1" in result.stderr, "the surviving block verdict must still name the unit that earned it"


@pytest.mark.unit
class TestAssertSharedFileImpactHookPendingThenPassNonClobbering:
    """AC-5 (spec 3.5, 4.6): round-5 finding A1 family, residual gap closed in this
    change. `_write_shared_file_impact_verdict`'s non-clobbering guard previously
    protected only an unconsumed `"block"`; an unconsumed `"pending"` -- left behind
    when an invocation crashes AFTER opening its own `"pending"` but BEFORE reaching a
    clean `"pass"`/`"block"` verdict, exactly the "started but the verdict cannot be
    determined" case spec 3.5 requires to fail closed -- was still freely overwritten
    by a DIFFERENT, later invocation's own clean `"pass"` write. Two
    `check-shared-file-impact` invocations chained in a single Bash tool call
    (`unit-a ; unit-b`) fire only ONE PostToolUse event for the whole call, so unit A
    crashing mid-run must still leave the hook fail-closed on that one shared event,
    exactly like the block case above. Uses the REAL production write function
    (`cli._write_shared_file_impact_verdict`) with explicit, DIFFERING
    `invocation_id` values representing two distinct OS processes, not this file's own
    `_write_record` test fixture, since `_write_record` bypasses the non-clobbering
    guard entirely and carries no invocation id at all."""

    def test_pending_then_pass_in_one_process_leaves_the_record_blocking(self, tmp_path: Path) -> None:
        workspace = tmp_path / "workspace"
        # Simulate `check-shared-file-impact <unit-a> ; check-shared-file-impact <unit-b>`
        # chained in one Bash call: unit A's invocation opens "pending" and then CRASHES
        # (killed, uncaught exception, argparse rejection deeper in the call graph --
        # never reaches its own "pass"/"block" write). Unit B's invocation (a genuinely
        # passing unit, a DIFFERENT invocation id) then runs its own full
        # "pending" -> "pass" transition, exactly what `cmd_check_shared_file_impact`
        # does at the start and end of every call.
        cli._write_shared_file_impact_verdict(
            "pending", workspace_root=workspace, unit_id="E9-F1-S1-T1", invocation_id="invocation-a"
        )
        # unit A crashes here -- no further write from invocation-a.
        cli._write_shared_file_impact_verdict(
            "pending", workspace_root=workspace, unit_id="E9-F2-S1-T2", invocation_id="invocation-b"
        )
        cli._write_shared_file_impact_verdict(
            "pass", workspace_root=workspace, unit_id="E9-F2-S1-T2", invocation_id="invocation-b"
        )

        record = workspace / ".devbench" / "shared-file-impact-verdict"
        assert record.read_text().splitlines()[0] == "pending", (
            "an unconsumed 'pending' verdict opened by one invocation must never be clobbered "
            "by a later, DIFFERENT invocation's own 'pending'/'pass' writes -- only the hook "
            "consuming it (reading then deleting it), or that SAME invocation's own follow-up "
            "write, may clear it"
        )
        assert "E9-F1-S1-T1" in record.read_text(), (
            "the surviving pending record must still name the unit that opened it"
        )

        result = _run_hook(workspace_root=workspace)
        assert result.returncode == 2, "the real hook must still fail closed on the surviving 'pending' record"
        assert "E9-F1-S1-T1" in result.stderr, "the surviving pending verdict must still name the unit that opened it"

    def test_own_invocation_pending_to_pass_still_allows(self, tmp_path: Path) -> None:
        """Regression guard for the fix above: an ORDINARY single passing invocation --
        one `invocation_id` writing its own `"pending"` and then its own `"pass"`,
        exactly what a real `cmd_check_shared_file_impact` run does -- must still end
        at `"pass"` and the real hook must still allow it. The non-clobbering guard
        must never mistake a SINGLE invocation's own transition for a foreign one."""
        workspace = tmp_path / "workspace"
        cli._write_shared_file_impact_verdict(
            "pending", workspace_root=workspace, unit_id="E1-F1-S1-T1", invocation_id="only-invocation"
        )
        cli._write_shared_file_impact_verdict(
            "pass", workspace_root=workspace, unit_id="E1-F1-S1-T1", invocation_id="only-invocation"
        )

        record = workspace / ".devbench" / "shared-file-impact-verdict"
        assert record.read_text().splitlines()[0] == "pass"

        result = _run_hook(workspace_root=workspace)
        assert result.returncode == 0, "an ordinary single passing invocation must still allow"


@pytest.mark.unit
class TestAssertSharedFileImpactHookConsumeFailureFailsClosed:
    """Round-5 code_review/test_review finding A4: `rm -f` unlinking the record requires
    write permission on its CONTAINING directory, not the record file itself. A
    non-writable `.devbench` (or session) directory makes `rm -f` fail; under
    `set -euo pipefail` that used to abort the script entirely (a non-blocking exit 1,
    the record left unconsumed, and the raw `rm` stderr leaked to the agent) instead of
    fail-closed exit 2 with this hook's own controlled message."""

    def test_read_only_record_directory_with_a_block_record_fails_closed(self, tmp_path: Path) -> None:
        workspace = tmp_path / "workspace"
        record = _write_record(workspace, "block", unit_id="E7-F1-S1-T1")
        devbench_dir = record.parent
        original_mode = devbench_dir.stat().st_mode
        devbench_dir.chmod(0o555)
        try:
            result = _run_hook(workspace_root=workspace)
        finally:
            # Restore write permission so pytest's own tmp_path cleanup can remove the
            # directory afterwards.
            devbench_dir.chmod(original_mode)

        assert result.returncode == 2, "a failed consume must fail closed (exit 2), never abort with a bare rm error"
        assert "assert-shared-file-impact" in result.stderr
        assert "rm:" not in result.stderr, "the raw OS rm error text must never leak to the agent"
        assert "Permission denied" not in result.stderr, "the raw OS rm error text must never leak to the agent"


@pytest.mark.unit
class TestAssertSharedFileImpactHookIgnoresPayloadContent:
    """Proof that the failure modes every prior review round found (command-string
    parsing, stdout parsing) are now structurally impossible: this hook's verdict does
    not change no matter what the PostToolUse payload's own `tool_input`/
    `tool_response` fields say. `_REAL_LOGGED_MENTION_COMMANDS` are replayed verbatim
    from this repo's own `hook-logs.jsonl`; the wrapper-form / quoting / heredoc /
    truncated-fragment payloads below are CONSTRUCTED -- they reproduce the exact
    shapes each of rounds 2 through 4's now-deleted command/stdout matchers were
    replay-proven to mishandle, to demonstrate none of them can affect the outcome any
    more."""

    @pytest.mark.parametrize("real_command", _REAL_LOGGED_MENTION_COMMANDS, ids=["real-grep-1", "real-grep-2"])
    def test_real_logged_mention_with_no_record_allows(self, tmp_path: Path, real_command: str) -> None:
        workspace = tmp_path / "workspace"
        payload = json.dumps(
            {"tool_name": "Bash", "tool_input": {"command": real_command}, "tool_response": {"stdout": ""}}
        )
        result = _run_hook(workspace_root=workspace, stdin_text=payload)
        assert result.returncode == 0

    @pytest.mark.parametrize(
        "constructed_command",
        [
            # Round-4's residual guard-2 fail-open: a shell wrapped in a form only the
            # literal `-c` token was unwrapped for.
            'bash -lc "uv run devbench check-shared-file-impact E1"',
            'bash --login -c "uv run devbench check-shared-file-impact E1"',
            'sh -ec "uv run devbench check-shared-file-impact E1"',
            # Round-4's apostrophe-sandwich: the single-word unquote pass creating a
            # spurious quote pairing across an unrelated later quoted region.
            'echo "don\'t" ; uv run devbench check-shared-file-impact E1 ; echo "won\'t"',
            # A variable-assembled command line -- never reachable by any word-level
            # command matcher regardless of how it is written.
            'CMD="uv run devbench check-shared-file-impact"; $CMD E1',
            # A heredoc body that merely CONTAINS the phrase as inert text.
            "cat > /tmp/probe.sh <<'SCRIPT_EOF'\nuv run devbench check-shared-file-impact E1\nSCRIPT_EOF",
        ],
        ids=["bash-lc", "bash-login-c", "sh-ec", "apostrophe-sandwich", "variable-assembled", "heredoc-body"],
    )
    def test_constructed_evasion_shaped_command_with_no_record_allows(
        self, tmp_path: Path, constructed_command: str
    ) -> None:
        workspace = tmp_path / "workspace"
        payload = json.dumps(
            {"tool_name": "Bash", "tool_input": {"command": constructed_command}, "tool_response": {"stdout": ""}}
        )
        result = _run_hook(workspace_root=workspace, stdin_text=payload)
        assert result.returncode == 0

    def test_unrelated_command_with_a_block_record_still_blocks(self, tmp_path: Path) -> None:
        """The mirror image of the above: this hook blocks this Bash call's own
        PostToolUse firing given an unconsumed `"block"` record left by a
        `check-shared-file-impact` invocation, EVEN THOUGH this call's own command
        text (`ls -la`) has nothing to do with the gate -- by design (see the
        module header's "staleness bound" paragraph): the record, not the command
        text, is authoritative. This is not necessarily the very next Bash call in
        wall-clock order (see `TestAssertSharedFileImpactHookStalenessBound`)."""
        workspace = tmp_path / "workspace"
        _write_record(workspace, "block")
        payload = json.dumps(
            {"tool_name": "Bash", "tool_input": {"command": "ls -la"}, "tool_response": {"stdout": "total 0"}}
        )
        result = _run_hook(workspace_root=workspace, stdin_text=payload)
        assert result.returncode == 2

    def test_truncated_non_json_stdin_with_a_pass_record_still_allows(self, tmp_path: Path) -> None:
        """A malformed/truncated PostToolUse payload (unparseable as JSON at all) used
        to be this hook's own fail-closed trigger (rounds 1-4); now the payload is
        never parsed, so a malformed payload has no bearing on the outcome either --
        only the record does."""
        workspace = tmp_path / "workspace"
        _write_record(workspace, "pass")
        result = _run_hook(workspace_root=workspace, stdin_text='{"tool_name": "Bash", "tool_inp')
        assert result.returncode == 0

    def test_empty_stdin_with_a_block_record_still_blocks(self, tmp_path: Path) -> None:
        """Pins a design decision made and rejected during this round: a defensive
        `hook_event_name`/`tool_name` sanity check (gating the record check on a
        successfully-extracted payload field) was tried and measured to reintroduce
        exactly the failure mode this redesign removes -- an empty/truncated stdin
        silently ALLOWED past a genuine `"block"` record, because the field
        extraction itself came back empty. This test fails if that check is ever
        reintroduced."""
        workspace = tmp_path / "workspace"
        _write_record(workspace, "block")
        result = _run_hook(workspace_root=workspace, stdin_text="")
        assert result.returncode == 2


@pytest.mark.unit
class TestAssertSharedFileImpactHookRegistration:
    """AC-6 (spec Section 10): the rewritten hook is registered exactly once on
    PostToolUse in hooks.json."""

    def test_registered_exactly_once_on_post_tool_use_bash(self) -> None:
        registration = json.loads(HOOKS_JSON_PATH.read_text(encoding="utf-8"))
        post_tool_use_bash_groups = [
            group for group in registration["hooks"]["PostToolUse"] if group.get("matcher") == "Bash"
        ]
        commands = [
            hook["command"]
            for group in post_tool_use_bash_groups
            for hook in group["hooks"]
            if "assert-shared-file-impact.sh" in hook.get("command", "")
        ]
        assert commands == ["bash ${CLAUDE_PLUGIN_ROOT}/scripts/assert-shared-file-impact.sh"]
