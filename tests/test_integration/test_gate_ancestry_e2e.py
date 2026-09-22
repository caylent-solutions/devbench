"""Hermetic ancestry journey suite over squash-merged and fetch-failure fixtures (E4-F2-S1-T2).

The ancestry gate (`cmd_check_ancestry`, `cmd_mark_done`'s
`_check_gate_pass_done_invariant`, `cmd_log_waiver`) is already covered at
the unit level in `tests/test_cli.py::TestCmdCheckAncestry`,
`TestCmdCheckAncestrySquashProbe`, `TestCmdCheckAncestryFetchIsFatal`,
`TestCmdCheckAncestryRemoteResolution`, `TestCmdCheckAncestryDisabled`,
`TestCheckAncestryThenMarkDoneRealGitNonEmptyManifest` and
`TestCheckAncestryAbsentManifestFileRealGit`, most of which drive
`cmd_check_ancestry`/`cmd_mark_done` against a wholesale-stubbed
`run_command` (every subprocess call, git included, answered by a hand-
written fake). What this module adds is the `tests/test_integration`
placement spec Section 10 requires per gate: one dedicated journey class
per scenario, driven over REAL scratch git repositories on disk (no
stubbed git), with the fake boundary narrowed to the ONE call this gate
cannot exercise hermetically -- `gh pr list` -- and every other command
(fetch, merge-base, rev-parse, hash-object, remote config) running for
real. A prior unit in this epic shipped a bug that survived review
because a test stubbed `run_command` wholesale so `git hash-object`
always returned success; every fake in this module is a strict-allowlist
dispatcher that raises `AssertionError` on any invocation it does not
recognise, and every non-`gh` command is routed to the real,
module-captured `run_command` rather than answered by a canned value.

No production code is added by this task (Task Type: test-only): every
journey below asserts behaviour E4-F1-S1-T1/T2/T4 and E4-F2-S1-T1 already
shipped, so a journey that fails here is a genuine defect in the gate, not
a missing feature.

Fixture idiom: `_AncestryCmdFixtures` is imported from `tests/test_cli.py`
(not hand-copied) -- it already carries the config-fixture writer
(`_write_gate_config`/`_enable_gate`, a real `devbench.yaml` resolved
through `DEVBENCH_CONFIG_PATH`), the WU + BACKLOG.md seeder (`_seed_wu`,
which pre-seeds all five required judge `[REVIEW_PASS]` verdicts and a
`Task Type: chore` line so the RED gate never applies) and the
`check-ancestry`/`mark-done` patch-surface builders
(`_check_ancestry_patches`, `_mark_done_patches`) this module's journeys
call `cmd_check_ancestry`/`cmd_mark_done`/`cmd_log_waiver` through.
`_JourneyFixtures` below layers only the call-and-patch glue this module
needs on top of that shared base -- it does not re-derive
`_AncestryCmdFixtures`'s own fixture or config logic. The dependency-
topology git construction (`_build_dependency_fixture`) is new to this
module (no prior test needed a REAL merged/squashed/unmerged dependency
branch pair) and is built entirely from the generic scratch-git factory in
`tests/test_tdd_gate.py` (`init_scratch_repo`, `write_scratch_file`,
`commit_scratch_repo`, `run_scratch_git`), parametrised by topology so
every journey body below stays assertion-only.

"Real CLI" here means the actual, unmocked `devbench.cli.cmd_check_ancestry`,
`devbench.cli.cmd_mark_done` and `devbench.cli.cmd_log_waiver`
implementations -- the same functions the `devbench` executable dispatches
to. `_AncestryCmdFixtures._check_ancestry_patches`/`_mark_done_patches`
patch only `devbench.cli.BacklogParser`, `devbench.cli.BACKLOG_ROOT`,
`devbench.cli.WORKSPACE_ROOT`, `devbench.cli.BACKLOG_INDEX`,
`devbench.cli.REPO_LOCAL_PATHS`, `devbench.cli.get_configured_default_branch`
(check-ancestry path) and `devbench.config.RUNTIME_CONFIG` (mark-done
path) -- never `cmd_check_ancestry`, `_resolve_ancestry_not_ancestor`,
`_write_ancestry_gate_pass_record`, or any other gate-internal function.

D-17 hazard (spec 4.1): gates default to DISABLED, and a disabled gate
exits 0 -- identically to how a genuinely PASSING enabled gate exits 0. So
`TestJourneyGateDisabled` below never asserts on a bare exit code alone:
every disabled-path assertion is keyed on the status line's literal
`{"gate": "ancestry", "status": "disabled"}` payload. Every OTHER journey
in this module also refuses to rely on a bare exit code, but the
discriminator differs per journey rather than being one uniform
`status: "pass"`/`"fail"` check: `TestJourneySquashMergedDependencyPasses`,
`TestJourneyStrictAncestryPasses` and `TestJourneyUnmergedDependencyBlocks`
assert the status line's `status`/`mode` fields directly;
`TestJourneyFetchFailureIsFatal` asserts `rc == 1` together with empty
stdout and an `ERROR` naming the fetch failure on stderr, since a fetch
failure prints no status line at all; `TestJourneyMarkDoneBlockedWithoutRecord`
and `TestJourneyStaleRecordAfterTargetMoves` key their `mark-done`
assertions on the persisted `[GATE_PASS ancestry]` record (written only by
a genuinely enabled, passing `check-ancestry` run) rather than on
`check-ancestry`'s own exit code; and `TestJourneyOperatorWaiverUnblocksMarkDone`
keys its assertions on the persisted `[GATE_WAIVER ancestry]` record plus
the final `## Status: done` marker, with the env override explicitly
cleared via `_enable_gate` so the enabled gate's invariant check is the
thing actually exercised. So a disabled gate can never be mistaken for a
passing one in this suite's own assertions, even though each journey
proves it through the record or status field appropriate to what it is
testing rather than through one identical check.
"""

from __future__ import annotations

import contextlib
import json
import subprocess
from collections.abc import Callable
from pathlib import Path
from unittest.mock import patch

import pytest
from test_cli import _AncestryCmdFixtures
from test_tdd_gate import commit_scratch_repo, init_scratch_repo, run_scratch_git, write_scratch_file

from devbench import cli
from devbench.constants import GATE_STATUS_FAIL, GATE_STATUS_PASS, GATE_TIER_MACHINE_BLOCKING

# The unit id every `_AncestryCmdFixtures` patch helper hard-codes into its
# mocked `WorkUnit` (`_make_unit`'s default, and `_mark_done_patches`'s own
# literal `WorkUnit(id="E1-F1-S1-T1", ...)`) -- every journey below must use
# this exact id so `cmd_check_ancestry`/`cmd_mark_done`'s `_find_unit` call
# resolves to the same seeded work unit the patches point at.
_UNIT_ID = "E1-F1-S1-T1"

# The real, unpatched subprocess seam -- captured at import time, before any
# test patches `devbench.cli.run_command`. Every `gh`-stubbing fake below
# routes every OTHER command through this reference instead of
# re-implementing `run_command`, or (the D-17-adjacent hazard this task's
# own Approach names) answering every command with one canned value.
_REAL_RUN_COMMAND = cli.run_command


def _passthrough_except_gh(
    gh_response: tuple[int, str, str], expected_gh_cmd: list[str]
) -> Callable[..., tuple[int, str, str]]:
    """Build a strict-allowlist `run_command` fake that stubs ONLY `gh ...`.

    Every non-`gh` invocation (every real git call `cmd_check_ancestry`
    makes: fetch, merge-base, rev-parse, hash-object, remote config) is
    routed to the real, unpatched `run_command`, so the fixture stays
    hermetic without ever coercing an unrelated git call's result. A `gh`
    invocation that does not match *expected_gh_cmd* exactly raises
    `AssertionError` rather than being silently answered -- this is the
    strict-allowlist shape the whole gate suite is built around.
    """

    def fake_run_command(
        cmd: list[str],
        cwd: Path | None = None,
        timeout: int | None = None,
        env: dict[str, str] | None = None,
    ) -> tuple[int, str, str]:
        if cmd and cmd[0] == "gh":
            assert cmd == expected_gh_cmd, f"unexpected gh invocation: {cmd}"
            return gh_response
        return _REAL_RUN_COMMAND(cmd, cwd=cwd, timeout=timeout, env=env)

    return fake_run_command


def _no_commands_allowed(
    cmd: list[str],
    cwd: Path | None = None,
    timeout: int | None = None,
    env: dict[str, str] | None = None,
) -> tuple[int, str, str]:
    """A strict-allowlist fake that permits ZERO commands (spec 4.1, D-17).

    `TestJourneyGateDisabled` proves `cmd_check_ancestry` never reaches a
    single git/gh call before its early disabled-status return -- any
    invocation at all is a defect.
    """
    raise AssertionError(f"unexpected command reached the disabled-gate path: {cmd}")


def _build_dependency_fixture(tmp_path: Path, *, dir_prefix: str, topology: str) -> tuple[Path, Path, str]:
    """Build a real origin + checkout pair with the dependency landed per *topology*.

    *topology*:
      - ``"merged"``: `dep-branch` is real-git-merged (`--no-ff`) into
        `main` -- the strict `merge-base --is-ancestor` probe reports True.
      - ``"squashed"``: `dep-branch`'s tree change lands on `main` via a
        brand-new, unrelated commit (a squash-merged PR landing) -- no
        commit is shared between the two branches, so the strict probe
        reports False and only the `gh pr list` probe can answer "merged".
      - ``"unmerged"``: `dep-branch` exists but never lands on `main` at
        all -- neither probe can find it.

    Returns ``(origin, checkout, dep_head_sha)``. *dep_head_sha* is
    `dep-branch`'s HEAD commit sha in *origin*, needed by squash/unmerged
    callers to prime the `gh` stub with the exact sha
    `_probe_squash_merged_pr` will itself resolve via `git rev-parse`.
    """
    origin = init_scratch_repo(
        tmp_path,
        dir_name=f"{dir_prefix}-origin",
        author_email=f"{dir_prefix}@example.com",
        author_name="Ancestry Journey",
    )
    write_scratch_file(origin, "README.md", "baseline\n")
    commit_scratch_repo(origin, "baseline")
    run_scratch_git(["branch", "-M", "main"], origin)

    run_scratch_git(["checkout", "-b", "dep-branch"], origin)
    write_scratch_file(origin, "feature.txt", "dependency feature\n")
    commit_scratch_repo(origin, "implement the dependency feature")
    dep_head_sha = run_scratch_git(["rev-parse", "HEAD"], origin).stdout.strip()

    run_scratch_git(["checkout", "main"], origin)
    if topology == "merged":
        run_scratch_git(["merge", "--no-ff", "-m", "merge dep-branch", "dep-branch"], origin)
    elif topology == "squashed":
        write_scratch_file(origin, "feature.txt", "dependency feature\n")
        commit_scratch_repo(origin, "squash-merge dep-branch via PR")
    elif topology == "unmerged":
        pass
    else:
        raise AssertionError(f"unknown topology: {topology!r}")

    checkout = tmp_path / f"{dir_prefix}-checkout"
    run_scratch_git(["clone", "-q", str(origin), str(checkout)], tmp_path)
    run_scratch_git(["config", "user.email", f"{dir_prefix}@example.com"], checkout)
    run_scratch_git(["config", "user.name", "Ancestry Journey"], checkout)
    return origin, checkout, dep_head_sha


def _expected_gh_probe_cmd(sha: str, default_branch: str = "main") -> list[str]:
    """The exact `gh pr list` invocation `_probe_squash_merged_pr` issues for *sha*."""
    return [
        "gh",
        "pr",
        "list",
        "--search",
        sha,
        "--state",
        "merged",
        "--base",
        default_branch,
        "--json",
        "number,mergedAt,title",
    ]


class _JourneyFixtures(_AncestryCmdFixtures):
    """Call-and-patch glue this module's journeys share on top of `_AncestryCmdFixtures`.

    Adds `check-ancestry`/`mark-done`/`log-waiver` runners over the
    inherited `_check_ancestry_patches`/`_mark_done_patches` builders so a
    journey body only ever calls one of the three methods below and reads
    the returned exit code / captured output.
    """

    def _check_ancestry(
        self,
        tmp_path: Path,
        checkout: Path,
        backlog_root: Path,
        backlog_index: Path,
        dependency_ref: str,
        target_ref: str,
        *,
        fake_run_command: Callable[..., tuple[int, str, str]] | None = None,
    ) -> int:
        patches = self._check_ancestry_patches(tmp_path, checkout, backlog_root, backlog_index)
        with contextlib.ExitStack() as stack:
            for one_patch in patches:
                stack.enter_context(one_patch)
            if fake_run_command is not None:
                stack.enter_context(patch("devbench.cli.run_command", side_effect=fake_run_command))
            return cli.cmd_check_ancestry(_UNIT_ID, dependency_ref, target_ref)

    def _mark_done(self, checkout: Path, backlog_root: Path, backlog_index: Path) -> int:
        patches = self._mark_done_patches(checkout, backlog_root, backlog_index)
        with contextlib.ExitStack() as stack:
            for one_patch in patches:
                stack.enter_context(one_patch)
            return cli.cmd_mark_done(_UNIT_ID)

    def _log_waiver(self, checkout: Path, backlog_root: Path, backlog_index: Path, *, target: str, reason: str) -> int:
        patches = self._mark_done_patches(checkout, backlog_root, backlog_index)
        with contextlib.ExitStack() as stack:
            for one_patch in patches:
                stack.enter_context(one_patch)
            return cli.cmd_log_waiver(
                "code_review",
                _UNIT_ID,
                "--gate",
                "ancestry",
                "--target",
                target,
                "--reason",
                reason,
                "--operator",
            )


class TestJourneySquashMergedDependencyPasses(_JourneyFixtures):
    """AC-E2E-001 (spec 10, AC-17, finding 317-D02): a squash-merged
    dependency (no commit shared with the target ref) passes via the
    `gh pr list` probe and reports `mode: "squash-pr"`."""

    def test_squash_merged_dependency_passes_via_pr_probe(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        self._enable_gate(tmp_path, monkeypatch)
        origin, checkout, dep_sha = _build_dependency_fixture(tmp_path, dir_prefix="squash", topology="squashed")

        # Fixture precondition: the squash topology must NOT share a commit
        # between dep-branch and main -- if it did, this journey would be
        # exercising the strict-probe pass path, not the squash-PR path.
        precheck = subprocess.run(
            ["git", "merge-base", "--is-ancestor", dep_sha, "main"], cwd=origin, capture_output=True, text=True
        )
        assert precheck.returncode == 1, "fixture precondition: dep-branch must NOT be an ancestor of main"

        expected_gh_cmd = _expected_gh_probe_cmd(dep_sha)
        gh_payload = json.dumps([{"number": 42, "mergedAt": "2026-01-01T00:00:00Z", "title": "squash landed"}])
        fake_run_command = _passthrough_except_gh((0, gh_payload, ""), expected_gh_cmd)

        backlog_root, backlog_index, wu_file = self._seed_wu(tmp_path, _UNIT_ID, manifest_file="README.md")

        result = self._check_ancestry(
            tmp_path,
            checkout,
            backlog_root,
            backlog_index,
            "origin/dep-branch",
            "origin/main",
            fake_run_command=fake_run_command,
        )
        out = capsys.readouterr().out
        assert result == 0
        payload = json.loads(out.splitlines()[0])
        assert payload["status"] == GATE_STATUS_PASS
        assert payload["mode"] == "squash-pr"
        assert payload["tier"] == GATE_TIER_MACHINE_BLOCKING
        assert payload["dependency_ref"] == "origin/dep-branch"
        assert payload["target_ref"] == "origin/main"
        assert payload["scope_hash"], "a passing run must persist a real, non-empty scope hash"

        # Both probe outcomes are printed together, never a silent hand-off
        # from one probe to the other (spec 3.5 fallback ban, AC-ANC-002).
        assert "not an ancestor" in out
        assert "merged via PR #42" in out

        content = wu_file.read_text(encoding="utf-8")
        assert "[GATE_PASS ancestry]" in content
        assert "[GATE_ANCESTRY_TARGET_REF]" in content


class TestJourneyFetchFailureIsFatal(_JourneyFixtures):
    """AC-E2E-002 (spec 4.5, AC-17): an unreachable configured remote is
    FATAL -- exit 1 with an ERROR naming the fetch failure, and the merge
    question is never answered from stale local refs."""

    def test_unreachable_remote_hard_fails_without_answering_from_stale_refs(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        self._enable_gate(tmp_path, monkeypatch)
        origin, checkout, _dep_sha = _build_dependency_fixture(tmp_path, dir_prefix="fetch", topology="merged")
        backlog_root, backlog_index, wu_file = self._seed_wu(tmp_path, _UNIT_ID, manifest_file="README.md")

        # The clone already captured local refs that answer "ancestor" --
        # a regression that skipped the fetch (or silently proceeded past
        # its failure) would report a false PASS from exactly these stale
        # refs without ever touching the (now broken) remote.
        precheck = subprocess.run(
            ["git", "merge-base", "--is-ancestor", "origin/dep-branch", "origin/main"],
            cwd=checkout,
            capture_output=True,
            text=True,
        )
        assert precheck.returncode == 0, "fixture precondition: local stale refs must already answer ancestor=true"

        unreachable = tmp_path / "does-not-exist-on-disk.git"
        run_scratch_git(["remote", "set-url", "origin", str(unreachable)], checkout)

        result = self._check_ancestry(
            tmp_path, checkout, backlog_root, backlog_index, "origin/dep-branch", "origin/main"
        )
        captured = capsys.readouterr()
        assert result == 1
        assert captured.out == "", "no status line -- and no pass -- may be printed on a fetch failure"
        assert "ERROR" in captured.err
        assert "fetch" in captured.err
        assert "'origin'" in captured.err
        assert "[GATE_PASS ancestry]" not in wu_file.read_text(encoding="utf-8")


class TestJourneyStrictAncestryPasses(_JourneyFixtures):
    """AC-14 pass journey (spec 10): a genuinely merged dependency passes
    via the strict probe alone, and the squash-PR probe never runs."""

    def test_strict_merge_passes_without_squash_probe(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        self._enable_gate(tmp_path, monkeypatch)
        _origin, checkout, _dep_sha = _build_dependency_fixture(tmp_path, dir_prefix="strict", topology="merged")
        backlog_root, backlog_index, wu_file = self._seed_wu(tmp_path, _UNIT_ID, manifest_file="README.md")

        def fail_on_gh(
            cmd: list[str],
            cwd: Path | None = None,
            timeout: int | None = None,
            env: dict[str, str] | None = None,
        ) -> tuple[int, str, str]:
            assert cmd[0] != "gh", f"the squash-PR probe must not run when the strict probe already passed: {cmd}"
            return _REAL_RUN_COMMAND(cmd, cwd=cwd, timeout=timeout, env=env)

        result = self._check_ancestry(
            tmp_path,
            checkout,
            backlog_root,
            backlog_index,
            "origin/dep-branch",
            "origin/main",
            fake_run_command=fail_on_gh,
        )
        out = capsys.readouterr().out
        assert result == 0
        payload = json.loads(out.splitlines()[0])
        assert payload["status"] == GATE_STATUS_PASS
        assert payload["mode"] == "strict"
        assert "not run (strict probe already passed)" in out
        assert "[GATE_PASS ancestry]" in wu_file.read_text(encoding="utf-8")


class TestJourneyUnmergedDependencyBlocks(_JourneyFixtures):
    """AC-14 block journey (spec 10): a dependency that is neither a
    strict ancestor nor found via any merged PR is BLOCKED."""

    def test_unmerged_dependency_with_no_merged_pr_blocks(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        self._enable_gate(tmp_path, monkeypatch)
        _origin, checkout, dep_sha = _build_dependency_fixture(tmp_path, dir_prefix="block", topology="unmerged")
        backlog_root, backlog_index, wu_file = self._seed_wu(tmp_path, _UNIT_ID, manifest_file="README.md")

        expected_gh_cmd = _expected_gh_probe_cmd(dep_sha)
        fake_run_command = _passthrough_except_gh((0, "[]", ""), expected_gh_cmd)

        result = self._check_ancestry(
            tmp_path,
            checkout,
            backlog_root,
            backlog_index,
            "origin/dep-branch",
            "origin/main",
            fake_run_command=fake_run_command,
        )
        captured = capsys.readouterr()
        assert result == 1
        payload = json.loads(captured.out.splitlines()[0])
        assert payload["status"] == GATE_STATUS_FAIL
        assert payload["mode"] == "none"
        assert "not an ancestor" in captured.out
        assert "no merged PR found" in captured.out
        assert "BLOCKED" in captured.err
        assert "origin/dep-branch" in captured.err
        assert "origin/main" in captured.err
        assert "[GATE_PASS ancestry]" not in wu_file.read_text(encoding="utf-8")


class TestJourneyGateDisabled(_JourneyFixtures):
    """AC-14 disabled journey (spec 4.1, D-17, AC-ANC-006): with no
    `gates:` key at all, `check-ancestry` self-reports disabled, exits 0,
    and makes NO git/gh call before returning."""

    def test_disabled_gate_self_reports_and_makes_no_commands(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        cfg_path = self._write_gate_config(tmp_path, "")
        monkeypatch.setenv("DEVBENCH_CONFIG_PATH", str(cfg_path))
        monkeypatch.delenv("DEVBENCH_GATE_ANCESTRY_ENABLED", raising=False)

        backlog_root, backlog_index, wu_file = self._seed_wu(tmp_path, _UNIT_ID, manifest_file="README.md")
        unused_checkout = tmp_path / "unused-checkout"
        unused_checkout.mkdir()

        result = self._check_ancestry(
            tmp_path,
            unused_checkout,
            backlog_root,
            backlog_index,
            "origin/dep-branch",
            "origin/main",
            fake_run_command=_no_commands_allowed,
        )
        out = capsys.readouterr().out.strip()
        assert result == 0
        assert json.loads(out) == {"gate": "ancestry", "status": "disabled"}
        assert "[GATE_PASS ancestry]" not in wu_file.read_text(encoding="utf-8")


class TestJourneyOperatorWaiverUnblocksMarkDone(_JourneyFixtures):
    """AC-14 waiver journey (spec 4.9, 3.6): an operator `[GATE_WAIVER
    ancestry]` unblocks `mark-done` with no `[GATE_PASS ancestry]` record."""

    def test_operator_waiver_unblocks_mark_done_with_no_gate_pass_record(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        self._enable_gate(tmp_path, monkeypatch)
        _origin, checkout = self._make_origin_and_checkout(tmp_path)
        backlog_root, backlog_index, wu_file = self._seed_wu(tmp_path, _UNIT_ID, manifest_file="README.md")

        waived = self._log_waiver(
            checkout,
            backlog_root,
            backlog_index,
            target="origin/dep-branch",
            reason="operator manually confirmed the dependency shipped in release 42",
        )
        assert waived == 0
        content = wu_file.read_text(encoding="utf-8")
        assert "[GATE_WAIVER ancestry]" in content
        assert "[GATE_PASS ancestry]" not in content

        done = self._mark_done(checkout, backlog_root, backlog_index)
        assert done == 0
        final_content = wu_file.read_text(encoding="utf-8")
        assert "## Status: done" in final_content
        assert "[GATE_PASS ancestry]" not in final_content


class TestJourneyMarkDoneBlockedWithoutRecord(_JourneyFixtures):
    """AC-E2E-004 (spec 4.2, AC-16): `mark-done` is blocked for an
    enabled-gate unit with no `[GATE_PASS ancestry]` record, naming the
    `check-ancestry` remediation command, and proceeds once the record
    exists."""

    def test_mark_done_blocked_without_record_then_proceeds_once_recorded(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        self._enable_gate(tmp_path, monkeypatch)
        _origin, checkout, _dep_sha = _build_dependency_fixture(tmp_path, dir_prefix="norecord", topology="merged")
        backlog_root, backlog_index, wu_file = self._seed_wu(tmp_path, _UNIT_ID, manifest_file="README.md")

        blocked = self._mark_done(checkout, backlog_root, backlog_index)
        err = capsys.readouterr().err
        assert blocked == 1
        assert f"has no [GATE_PASS ancestry] record for {_UNIT_ID}" in err
        assert "uv run devbench check-ancestry" in err
        assert "<dependency-ref>" in err
        assert "## Status: done" not in wu_file.read_text(encoding="utf-8")

        checked = self._check_ancestry(
            tmp_path, checkout, backlog_root, backlog_index, "origin/dep-branch", "origin/main"
        )
        capsys.readouterr()
        assert checked == 0
        assert "[GATE_PASS ancestry]" in wu_file.read_text(encoding="utf-8")

        proceeded = self._mark_done(checkout, backlog_root, backlog_index)
        assert proceeded == 0
        assert "## Status: done" in wu_file.read_text(encoding="utf-8")


class TestJourneyStaleRecordAfterTargetMoves(_JourneyFixtures):
    """AC-E2E-005 (spec 4.5, AC-7, internal issue #12 AC3): advancing the
    target ref after a pass makes `mark-done` refuse the now-stale record."""

    def test_target_ref_advancing_after_pass_makes_mark_done_refuse_as_stale(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        self._enable_gate(tmp_path, monkeypatch)
        origin, checkout, _dep_sha = _build_dependency_fixture(tmp_path, dir_prefix="stale", topology="merged")
        backlog_root, backlog_index, wu_file = self._seed_wu(tmp_path, _UNIT_ID, manifest_file="README.md")

        checked = self._check_ancestry(
            tmp_path, checkout, backlog_root, backlog_index, "origin/dep-branch", "origin/main"
        )
        capsys.readouterr()
        assert checked == 0
        assert "[GATE_PASS ancestry]" in wu_file.read_text(encoding="utf-8")

        # The target ref moves AFTER the record was written -- the unit's
        # own Changes Manifest is untouched, but the recorded ref's commit
        # sha is no longer current.
        write_scratch_file(origin, "downstream.txt", "target advanced after the gate already ran\n")
        commit_scratch_repo(origin, "advance target after the gate already ran")
        run_scratch_git(["fetch", "origin"], checkout)

        stale = self._mark_done(checkout, backlog_root, backlog_index)
        err = capsys.readouterr().err
        assert stale == 1
        assert "gate 'ancestry' record is stale (scope changed since it ran)" in err
        assert "## Status: done" not in wu_file.read_text(encoding="utf-8")
