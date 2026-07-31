"""Machine-observed RED gate for gated TDD tasks (FR-4.2, issue #257).

An agent-written ``[RED]`` entry in a work unit's TDD Cycle Log is an
unverified claim: the executor asserts a test failed before the fix existed,
but nothing re-runs that claim. This module builds the observation itself.
The orchestrator -- never the executor -- proves that a gated task's named
test genuinely failed before the production fix existed, by:

1. Deriving ``prod_paths`` from the task's Changes Manifest, classified as
   production source by the single Rule 14 classifier
   (:meth:`devbench.backlog.manager.BacklogManager._is_production_source`).
   No independent classifier is implemented here.
2. Pre-flight: rejecting (fail-closed) if the working tree carries changes
   outside the manifest -- the gate never stashes work it does not own.
3. Path-scoping the stash to production source WITH ``-u``:
   ``git stash push -u -- <prod_paths>``. The ``-u`` flag is mandatory --
   measured empirically during spec authoring (spec FR-4.2, ``[V]``): a
   blanket ``git stash -u`` (no pathspec) removes the task's new *test* file
   too, so the named test cannot be found and pytest exits 4 -- a false RED
   an exit-code-only check would accept. ``git stash push -- <path>``
   *without* ``-u`` errors on an untracked file with "pathspec ... did not
   match any file(s) known to git", and new production files are the norm,
   not the edge case -- without ``-u`` a legitimate new-file fix is either
   blocked outright or (with a blanket no-pathspec stash) left in the tree,
   so the pre-change run still contains the fix and a genuine RED is missed.
4. Running the named test and applying the three-part assertion, all three
   required: the pytest exit code is EXACTLY 1 (tests ran, at least one
   failed); the named test was COLLECTED; and it failed as a **test
   failure** (assertion/behavioral), not a collection, import, or syntax
   error. Measured exit-code semantics (``[V]``): 1 = genuine RED candidate;
   4 (named test's file not found) and 2 (collection/import/syntax error) =
   false RED, rejected; 0 = no RED.
5. Restoring with ``git stash pop`` -- which MUST run even when the test
   step raises (fail-closed restore).

On success this module returns a :class:`RedObservation` (exit code, test
node id, failure-output digest). Writing that observation into the work
unit's TDD Cycle Log as the orchestrator-only ``RED_OBSERVED`` phase is the
caller's responsibility (see ``devbench.cli.cmd_tdd_gate`` /
``devbench.cli.write_red_observed_entry``) -- this module is a pure
observation engine with no work-unit-file-writing side effect, keeping the
git/pytest observation concern separate from the backlog-file-persistence
concern (single responsibility).
"""

from __future__ import annotations

import hashlib
import re
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path

from devbench.backlog.manager import BacklogManager
from devbench.config import TEST_TIMEOUT
from devbench.constants import TDD_CYCLE_LOG_SECTION_BODY_RE
from devbench.utils.process import run_command

# ---------------------------------------------------------------------------
# Measured pytest exit-code semantics (FR-4.2, spec-verified [V]).
# ---------------------------------------------------------------------------
PYTEST_EXIT_OK: int = 0
PYTEST_EXIT_TESTS_FAILED: int = 1
PYTEST_EXIT_INTERRUPTED: int = 2
PYTEST_EXIT_USAGE_ERROR: int = 4

_EXIT_CODE_REASONS: dict[int, str] = {
    PYTEST_EXIT_OK: "pytest reported all collected tests passed (no RED observed)",
    PYTEST_EXIT_TESTS_FAILED: "pytest reported at least one collected test failed",
    PYTEST_EXIT_INTERRUPTED: "pytest was interrupted by a collection, import, or syntax error",
    PYTEST_EXIT_USAGE_ERROR: "pytest reported a usage error (the named test's file or node id was not found)",
}

# Marker git prints on stdout when `git stash push` had nothing matching the
# given pathspec to stash -- distinguishing "nothing to stash" (exit 0, no
# stash entry created) from a genuine stash failure lets the caller decide
# whether `git stash pop` is safe to run afterward (popping with no stash
# entry created by this call would risk popping an unrelated stash entry
# from the same stack).
#
# PUBLIC (E4-F4-S1-T2, code_review FAIL round 4, DRY): this marker and the
# `stash_push_scoped`/`stash_pop` helpers below were module-private until
# `devbench.cli`'s green-green-check needed the identical scoped-stash
# behavior for its own before/after reconstruction and, unable to import a
# private name across modules, duplicated all three verbatim as `_gg_*`.
# Promoting them to public symbols here (and importing them in cli.py
# instead) is the single source of truth; the `_gg_*` duplicates are gone.
STASH_NO_LOCAL_CHANGES_MARKER: str = "No local changes to save"

# Matches a pytest node id embedded in an agent's free-text RED entry
# message, e.g. "tests/test_foo.py::test_baz" or
# "tests/test_foo.py::TestBar::test_baz". Requires a ".py::" anchor so a bare
# file path (no "::") never matches -- the gate needs a *specific* named
# test, not a whole module.
_TEST_NODE_ID_TOKEN_RE = re.compile(r"[\w./\-]+\.py::[\w:\[\]./\-]+")

# Trailing punctuation a sentence might attach to a node-id token (a
# terminating period, comma, or closing parenthesis) that is not part of the
# node id itself.
_NODE_ID_TRAILING_PUNCTUATION: str = ".,)"

# Matches an agent-written RED entry line -- never a RED_OBSERVED line,
# since "[RED_OBSERVED]" does not match the literal "[RED]" tag this pattern
# requires.
_RED_ENTRY_LINE_RE = re.compile(r"^-\s+\[RED\]\s+\S+\s+--\s+(?P<message>.+)$", re.MULTILINE)

# Matches a pytest `-rA` short-test-summary-info outcome line for a specific
# node, e.g. "FAILED tests/test_foo.py::test_baz - AssertionError: ...".
_OUTCOME_LINE_RE = re.compile(r"^(?P<outcome>PASSED|FAILED|ERROR)\s+(?P<node_id>\S+)", re.MULTILINE)

# FR-4.5: the three legitimate remedies every rejection message names.
REMEDY_1: str = (
    "Produce a genuine RED: write a test that reproduces the failure, confirm it fails "
    "pre-change, then fix production source."
)
REMEDY_2: str = (
    "Re-type the task: if it is genuinely docs, chore, refactor, or test-only, declare that "
    "type and satisfy its invariant instead."
)
REMEDY_3: str = (
    "Decline as already-satisfied: if a prior task already closed this behavior, verify it and "
    "route to decline with reason already-satisfied, citing the closing commit or task."
)


class TddGateRejectionError(Exception):
    """Raised when the RED gate rejects an observation (fail-closed, exit 1 at the CLI)."""


@dataclass(frozen=True)
class TestObservation:
    """The raw result of running one named test node id.

    Attributes:
        exit_code: The test runner's process exit code.
        node_outcome: The named node's own outcome as reported by the
            runner (``"PASSED"``, ``"FAILED"``, ``"ERROR"``), or ``None``
            when the named node does not appear in the runner's output at
            all (not collected -- e.g. its file was not found, or a
            collection/import error prevented the runner from reaching it).
        raw_output: The runner's combined stdout/stderr, used to compute the
            failure digest recorded on a successful observation.
    """

    exit_code: int
    node_outcome: str | None
    raw_output: str


@dataclass(frozen=True)
class RedObservation:
    """A successfully observed genuine RED, ready to record as RED_OBSERVED.

    Attributes:
        exit_code: The observed nonzero exit code (always
            ``PYTEST_EXIT_TESTS_FAILED`` for the pytest-backed runner).
        test_node_id: The pytest node id of the failing test.
        failure_digest: A SHA-256 hex digest of the failure output.
    """

    exit_code: int
    test_node_id: str
    failure_digest: str


TestRunner = Callable[[str, Path], TestObservation]


def _exit_code_reason(exit_code: int) -> str:
    """Return a human-readable explanation of a measured pytest exit code."""
    return _EXIT_CODE_REASONS.get(exit_code, f"pytest exited with an unexpected code ({exit_code})")


def _build_rejection_message(
    unit_id: str,
    test_node_id: str | None,
    exit_code: int | None,
    detail: str,
) -> str:
    """Build the standard RED-gate rejection message (spec FR-4.2/FR-4.5).

    Names the task, the test node id, the observed exit code, what was
    expected, and all three legitimate remedies, per the spec's error
    handling contract.
    """
    node_display = test_node_id if test_node_id else "(no named test found)"
    exit_display = str(exit_code) if exit_code is not None else "(not observed)"
    lines = [
        f"ERROR: RED gate rejected task '{unit_id}'.",
        f"  Test node id: {node_display}",
        f"  Observed exit code: {exit_display}",
        (
            f"  Expected: pytest exit code {PYTEST_EXIT_TESTS_FAILED} (tests failed) with the "
            "named test collected and reported as FAILED (not an error, and not a "
            "collection/import/syntax failure)."
        ),
        f"  Detail: {detail}",
        "  Remedies:",
        f"    1. {REMEDY_1}",
        f"    2. {REMEDY_2}",
        f"    3. {REMEDY_3}",
    ]
    return "\n".join(lines)


def classify_production_paths(manifest_paths: Sequence[str]) -> list[str]:
    """Return the subset of *manifest_paths* that are production source.

    Delegates entirely to
    :meth:`devbench.backlog.manager.BacklogManager._is_production_source`
    (Rule 14) -- this module implements no independent path classification
    (AC-E4-F3-S1-T2-8).
    """
    return [path for path in manifest_paths if BacklogManager._is_production_source(path)]


def find_named_test_node_id(work_unit_content: str) -> str | None:
    """Find the most recently named test node id in the TDD Cycle Log.

    Scans the agent-written ``[RED]`` entries inside the ``## TDD Cycle
    Log`` section (never ``[RED_OBSERVED]``, ``[GREEN]``, or ``[REFACTOR]``
    entries, and never text outside that section), from most recent to
    oldest, and returns the last ``<path>.py::<test>``-shaped token found in
    the first entry that contains one.

    Returns:
        The node id string, or ``None`` when no RED entry names one.
    """
    section_match = TDD_CYCLE_LOG_SECTION_BODY_RE.search(work_unit_content)
    if section_match is None:
        return None
    section_body = section_match.group(1)
    red_messages = [match.group("message") for match in _RED_ENTRY_LINE_RE.finditer(section_body)]
    for message in reversed(red_messages):
        tokens = _TEST_NODE_ID_TOKEN_RE.findall(message)
        if tokens:
            return tokens[-1].rstrip(_NODE_ID_TRAILING_PUNCTUATION)
    return None


def find_paths_outside_manifest(repo_path: Path, manifest_paths: Sequence[str]) -> list[str]:
    """Return working-tree paths with uncommitted changes not in the manifest.

    The gate never stashes work it does not own (spec AC-53): before
    touching the stash, the caller must confirm every changed/untracked
    path in the tree is accounted for by the task's own Changes Manifest.

    Raises:
        TddGateRejectionError: If ``git status`` itself fails.
    """
    manifest_set = set(manifest_paths)
    exit_code, stdout, stderr = run_command(["git", "status", "--porcelain=v1", "--untracked-files=all"], cwd=repo_path)
    if exit_code != 0:
        raise TddGateRejectionError(f"ERROR: 'git status' failed in {repo_path}: {stderr.strip() or stdout.strip()}")

    outside: list[str] = []
    seen: set[str] = set()
    for line in stdout.splitlines():
        path_part = line[3:]
        if " -> " in path_part:
            old_path, _, new_path = path_part.partition(" -> ")
            candidate_paths = [old_path, new_path]
        else:
            candidate_paths = [path_part]
        for raw_candidate in candidate_paths:
            candidate = raw_candidate.strip('"')
            if candidate in manifest_set or candidate in seen:
                continue
            seen.add(candidate)
            outside.append(candidate)
    return outside


def stash_push_scoped(repo_path: Path, prod_paths: Sequence[str]) -> tuple[bool, str | None]:
    """Run ``git stash push -u -- <prod_paths>``.

    PUBLIC: also the shared "before"-state reconstruction step used by
    ``devbench.cli``'s green-green-check (FR-4.6) -- see the module-level
    note above ``STASH_NO_LOCAL_CHANGES_MARKER``.

    Returns:
        A ``(pushed, error)`` pair. ``pushed`` is ``True`` only when a stash
        entry was actually created (so the caller knows whether ``git stash
        pop`` is later safe to run). ``error`` carries a description of a
        genuine failure, or ``None`` on success (whether or not anything
        was pushed).
    """
    exit_code, stdout, stderr = run_command(["git", "stash", "push", "-u", "--", *prod_paths], cwd=repo_path)
    if exit_code != 0:
        return False, (stderr.strip() or stdout.strip() or f"git stash push exited {exit_code}")
    if STASH_NO_LOCAL_CHANGES_MARKER in stdout:
        return False, None
    return True, None


def stash_pop(repo_path: Path) -> str | None:
    """Run ``git stash pop``. Returns an error description, or ``None`` on success.

    PUBLIC: also the shared "after"-state restore step used by
    ``devbench.cli``'s green-green-check (FR-4.6) -- see ``stash_push_scoped``.
    """
    exit_code, stdout, stderr = run_command(["git", "stash", "pop"], cwd=repo_path)
    if exit_code != 0:
        return stderr.strip() or stdout.strip() or f"git stash pop exited {exit_code}"
    return None


def _parse_node_outcome(output: str, test_node_id: str) -> str | None:
    """Return the named node's outcome from a pytest ``-rA`` summary, or ``None`` if absent."""
    for match in _OUTCOME_LINE_RE.finditer(output):
        if match.group("node_id") == test_node_id:
            return match.group("outcome")
    return None


def default_pytest_runner(test_node_id: str, repo_path: Path) -> TestObservation:
    """Run pytest, scoped to the named test's file, and capture its outcome.

    Invoked at file scope (not node-id scope) plus ``-rA`` (full short
    summary info) so a missing FILE reproduces the measured exit-4 usage
    error while a missing NODE within an existing file is still detected
    precisely by outcome-line matching, without depending on
    node-id-argument-specific pytest usage-error behavior.
    """
    file_part = test_node_id.split("::", 1)[0]
    exit_code, stdout, stderr = run_command(
        ["pytest", file_part, "--no-header", "-q", "-p", "no:cacheprovider", "-rA"],
        cwd=repo_path,
        timeout=TEST_TIMEOUT,
    )
    combined = "\n".join(part for part in (stdout, stderr) if part)
    node_outcome = _parse_node_outcome(combined, test_node_id)
    return TestObservation(exit_code=exit_code, node_outcome=node_outcome, raw_output=combined)


def observe_red(
    unit_id: str,
    repo_path: Path,
    manifest_paths: Sequence[str],
    work_unit_content: str,
    *,
    test_runner: TestRunner = default_pytest_runner,
) -> RedObservation:
    """Observe a genuine, machine-verified RED for a gated task (FR-4.2).

    Args:
        unit_id: The work unit id, named in every rejection message.
        repo_path: The target repository's working tree.
        manifest_paths: Every path listed in the task's Changes Manifest
            (both production and test rows -- used both to classify
            ``prod_paths`` and to scope the dirty-tree pre-flight check).
        work_unit_content: The full text of the work unit's markdown file,
            used to find the agent-named test node id.
        test_runner: The callable that executes the named test and reports
            its observation. Defaults to :func:`default_pytest_runner`.
            Injectable for tests that need to control or fail the test step
            deterministically (e.g. proving ``git stash pop`` runs even when
            this callable raises).

    Returns:
        A :class:`RedObservation` on a genuine RED.

    Raises:
        TddGateRejectionError: On every fail-closed rejection path: a dirty tree
            outside the manifest, no production-source rows in the
            manifest, no named test in the TDD Cycle Log, a stash push/pop
            failure, or a false/no RED (exit code, collection, or outcome
            mismatch).
        BaseException: Whatever *test_runner* itself raises (after the stash
            has been popped) is re-raised unchanged -- an internal
            test-runner failure is not silently reclassified as a RED-gate
            rejection. The restore runs for every exception type, including
            ``KeyboardInterrupt`` and ``SystemExit``, not merely subclasses
            of ``Exception``: an operator interrupting a long test run must
            never leave production source stashed out of the tree with no
            indication that ``git stash pop`` is needed.
    """
    outside_paths = find_paths_outside_manifest(repo_path, manifest_paths)
    if outside_paths:
        raise TddGateRejectionError(
            _build_rejection_message(
                unit_id,
                None,
                None,
                "the working tree carries changes outside the Changes Manifest: "
                f"{', '.join(outside_paths)}. The gate never stashes work it does not own; "
                "clear or commit these paths before retrying.",
            )
        )

    prod_paths = classify_production_paths(manifest_paths)
    if not prod_paths:
        raise TddGateRejectionError(
            _build_rejection_message(
                unit_id,
                None,
                None,
                "the Changes Manifest contains no production-source rows (Rule 14 classifier); "
                "a gated task must ship at least one production file.",
            )
        )

    test_node_id = find_named_test_node_id(work_unit_content)
    if test_node_id is None:
        raise TddGateRejectionError(
            _build_rejection_message(
                unit_id,
                None,
                None,
                "no named test node id (a '<path>.py::<test>' token) was found in the agent-written "
                "[RED] entries of the TDD Cycle Log.",
            )
        )

    pushed, push_error = stash_push_scoped(repo_path, prod_paths)
    if push_error is not None:
        raise TddGateRejectionError(
            _build_rejection_message(
                unit_id,
                test_node_id,
                None,
                f"'git stash push -u -- {' '.join(prod_paths)}' failed: {push_error}",
            )
        )

    test_exception: BaseException | None = None
    observation: TestObservation | None = None
    pop_error: str | None = None
    try:
        observation = test_runner(test_node_id, repo_path)
    except BaseException as caught:
        # Broad and intentional: the pop in the finally block below MUST
        # still run when the test step raises -- including
        # KeyboardInterrupt and SystemExit, not merely Exception subclasses
        # -- so the exception is captured here and re-raised only after the
        # stash has been popped (fail-closed restore). Never silently
        # swallowed.
        test_exception = caught
    finally:
        if pushed:
            pop_error = stash_pop(repo_path)

    if pushed and pop_error is not None:
        detail = f"'git stash pop' failed after observing the test run: {pop_error}."
        if test_exception is not None:
            detail += f" The test step also raised {test_exception!r}; both failures are reported together."
        raise TddGateRejectionError(_build_rejection_message(unit_id, test_node_id, None, detail)) from test_exception

    if test_exception is not None:
        raise test_exception

    # Invariant, not a runtime possibility to guard against: the try/except
    # above sets exactly one of `observation` or `test_exception`, and the
    # `test_exception is not None` branch immediately above always re-raises
    # (never falls through). By this point `test_exception` is `None`, so
    # `observation` was set by a successful `test_runner` call. This assert
    # exists purely for mypy narrowing (precedented at
    # `cli.py::cmd_start`'s `assert session_name is not None`), not as a
    # fail-closed check -- there is no `TddGateRejectionError` path here
    # because there is nothing to fail closed against.
    assert observation is not None, "unreachable: neither observation nor test_exception was set"

    if observation.exit_code != PYTEST_EXIT_TESTS_FAILED or observation.node_outcome != "FAILED":
        raise TddGateRejectionError(
            _build_rejection_message(
                unit_id,
                test_node_id,
                observation.exit_code,
                f"{_exit_code_reason(observation.exit_code)}; named test outcome was "
                f"{observation.node_outcome or 'not collected'}.",
            )
        )

    failure_digest = hashlib.sha256(observation.raw_output.encode("utf-8")).hexdigest()
    return RedObservation(exit_code=observation.exit_code, test_node_id=test_node_id, failure_digest=failure_digest)
