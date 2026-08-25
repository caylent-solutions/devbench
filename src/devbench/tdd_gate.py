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
   required: the runner's exit code is EXACTLY its ``failed_exit_code`` (1
   for both supported frameworks -- tests ran, at least one failed); the
   named test was COLLECTED; and it failed as a **test failure**
   (assertion/behavioral), not a collection, import, or syntax error.
   Measured pytest exit-code semantics (``[V]``): 1 = genuine RED
   candidate; 4 (named test's file not found) and 2 (collection/import/
   syntax error) = false RED, rejected; 0 = no RED.
5. Restoring with ``git stash pop`` -- which MUST run even when the test
   step raises (fail-closed restore).

On success this module returns a :class:`RedObservation` (exit code, test
node id, failure-output digest). Writing that observation into the work
unit's TDD Cycle Log as the orchestrator-only ``RED_OBSERVED`` phase is the
caller's responsibility (see ``devbench.cli.cmd_tdd_gate`` /
``devbench.cli.write_red_observed_entry``) -- this module is a pure
observation engine with no work-unit-file-writing side effect, keeping the
git/test-run observation concern separate from the backlog-file-persistence
concern (single responsibility).

Test frameworks
---------------
Which test runner a target repo uses is an operator decision, not something
this module sniffs: ``validate.test_runner`` in ``backlog/config/devbench.yaml``
(per-repo overridable at ``repos.<org/repo>.test_runner``) selects one, and
leaving it unset keeps the original pytest-only behaviour exactly. Nothing
is auto-detected -- a repo that carries both a ``package.json`` and a
``pyproject.toml`` would make detection a coin flip, and a gate that silently
picks the wrong runner rejects honest work with a message about the wrong
tool.

Everything that varies between frameworks -- the node-id shape searched for
in ``[RED]`` entries, the exit codes asserted on, the exit-code
explanations quoted in rejections, the runner itself -- is bundled into
:class:`TestFramework`; :data:`PYTEST_FRAMEWORK` and :data:`NODE_FRAMEWORK`
are the two instances. The gate's own logic (pre-flight, scoped stash,
fail-closed restore, three-part assertion) is framework-agnostic and is not
duplicated per framework.
"""

from __future__ import annotations

import hashlib
import re
from collections.abc import Callable, Mapping, Sequence
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

# ---------------------------------------------------------------------------
# Measured `node --test` exit-code semantics (Node 22), the node counterpart
# of the pytest table above.
#
# Node deliberately offers FEWER distinguishable codes than pytest: an
# ordinary assertion failure, a syntax error in the test file, and a test
# file that does not exist at all ALL exit 1. There is no equivalent of
# pytest's exit 4 (usage error) or exit 2 (collection/import error).
#
# That does not weaken the gate, because the gate has never leaned on the
# exit code alone. `default_pytest_runner`'s own docstring records that it
# runs at FILE scope plus `-rA` precisely so a missing NODE is "detected
# precisely by outcome-line matching, without depending on
# node-id-argument-specific pytest usage-error behavior". Outcome-line
# matching is the load-bearing check for both frameworks and the exit code
# is corroboration; for node it is simply the ONLY check. A run that
# produced no `ok`/`not ok` line for the named test is rejected as
# not-collected exactly as an exit-4 pytest run is, and
# `_node_missing_file_diagnostic` recovers from the output the detail the
# exit code no longer carries.
# ---------------------------------------------------------------------------
NODE_EXIT_OK: int = 0
NODE_EXIT_TESTS_FAILED: int = 1

_NODE_EXIT_CODE_REASONS: dict[int, str] = {
    NODE_EXIT_OK: "node --test reported all tests passed (no RED observed)",
    NODE_EXIT_TESTS_FAILED: (
        "node --test exited 1, which it does for ANY failure -- an assertion failure, a syntax "
        "error in the test file, or a test file that does not exist; the named test's own TAP "
        "outcome line is what tells these apart"
    ),
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

# Matches the `node:test` node-id shape inside an agent's free-text RED
# entry: `<path>::"<test name>"`, e.g. tests/greeter.test.js::"greets by name".
#
# The quotes are load-bearing, not decoration. A pytest test name is a
# Python identifier and so can never contain a space, which is why the
# pattern above can get away with a bare unquoted token; a `node:test` name
# is an arbitrary string and routinely does contain spaces ("greets by
# name"). Free text offers no other way to know where such a name ends, so
# the agent writing the [RED] entry has to delimit it -- and
# `TestFramework.node_id_shape` is quoted verbatim in every rejection
# message so the agent is told the shape rather than left to guess it.
#
# A useful consequence of requiring the quote: `::"` can never occur in a
# pytest node id and `.py::` can never occur in a quoted one, so the two
# patterns are mutually exclusive. A node id written in the wrong shape for
# the configured framework is never half-matched into something plausible;
# it is simply not found, and the rejection names the shape that was
# expected.
_NODE_TEST_NODE_ID_TOKEN_RE = re.compile(r'[\w./\-]+::"[^"\n]+"')

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

# Matches one `node --test` TAP outcome line: `ok 1 - greets by name` or
# `not ok 2 - greets by name`, the node counterpart of `_OUTCOME_LINE_RE`.
#
# Leading whitespace is allowed because TAP indents a subtest's lines under
# its parent, and a `describe`/`it` suite names its individual cases at
# that inner level -- anchoring hard to column zero would see only the
# suite names and never the tests an agent actually names.
#
# The trailing directive alternatives are spelled out as the literal SKIP
# and TODO rather than accepted as any word after a `#`. Node does escape a
# `#` occurring inside a test name (see `_TAP_NAME_ESCAPE_RE`), so the
# unescaped `#` that opens a directive is already unambiguous -- but a
# permissive directive pattern would make that escaping load-bearing, and
# if it ever failed the non-greedy name group would stop at the name's own
# `#`, silently truncate the name, and a genuine RED would be rejected as
# uncollected. TAP defines no directives beyond these two, so naming them
# costs nothing and removes the dependency.
_TAP_OUTCOME_LINE_RE = re.compile(
    r"^\s*(?P<status>not ok|ok)\s+\d+\s+-\s+(?P<name>.*?)\s*(?:#\s*(?P<directive>(?i:SKIP|TODO))\b.*)?$",
    re.MULTILINE,
)

# Node prints this, followed by the path it could not open, when
# `node --test <file>` is handed a path that does not exist. The gate
# surfaces the whole line in its rejection message because node's exit code
# for that case (1) is the same one an ordinary assertion failure produces:
# unlike pytest's exit 4, the code alone cannot tell an operator that the
# file is simply missing, and "the named test was not collected" without
# that line sends them hunting for a collection error that does not exist.
_NODE_MISSING_FILE_MARKER: str = "Could not find"

# Undoes node's TAP escaping of a test NAME, which backslash-escapes exactly
# two characters on its way out: `\` becomes `\\` and `#` becomes `\#`
# (measured, Node 22 -- a test named `hash # and back\slash` is reported as
# `hash \# and back\\slash`). The agent writes the node id with the name as
# the source declares it, so the parsed line must be un-escaped before it is
# compared or a name containing either character never matches its own
# outcome line -- rejecting a genuine RED as uncollected.
#
# Deliberately narrow: it un-escapes only those two characters rather than
# any `\X` pair, so an unrecognised escape is left alone instead of being
# silently mangled into a different name that might match the wrong test.
_TAP_NAME_ESCAPE_RE = re.compile(r"\\([\\#])")

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
        exit_code: The observed nonzero exit code (always the configured
            framework's ``failed_exit_code`` -- 1 for both pytest and node).
        test_node_id: The node id of the failing test, in the configured
            framework's shape.
        failure_digest: A SHA-256 hex digest of the failure output.
    """

    exit_code: int
    test_node_id: str
    failure_digest: str


TestRunner = Callable[[str, Path], TestObservation]


def classify_production_paths(manifest_paths: Sequence[str]) -> list[str]:
    """Return the subset of *manifest_paths* that are production source.

    Delegates entirely to
    :meth:`devbench.backlog.manager.BacklogManager._is_production_source`
    (Rule 14) -- this module implements no independent path classification
    (AC-E4-F3-S1-T2-8).
    """
    return [path for path in manifest_paths if BacklogManager._is_production_source(path)]


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


def _no_diagnostic(_raw_output: str) -> str | None:
    """Recover nothing: a pytest exit code already names its own cause.

    The pytest table distinguishes "the file or node id was not found"
    (exit 4) from "a collection, import, or syntax error" (exit 2) from an
    ordinary test failure (exit 1), so :func:`_exit_code_reason` alone
    already tells an operator what happened and there is no second source
    of detail to fall back on. Node collapses all three into exit 1, which
    is why :func:`_node_missing_file_diagnostic` exists and this deliberately
    does not. Present so that :class:`TestFramework` can require a
    diagnostic hook unconditionally rather than making the field optional
    and re-testing it for ``None`` at the one call site.
    """
    return None


def _parse_tap_node_outcome(output: str, test_node_id: str) -> str | None:
    """Return the named node's outcome from `node --test` TAP output, or ``None`` if absent.

    The node counterpart of :func:`_parse_node_outcome`. The node id's
    quoted half is the TAP test name to look for; the path half selected
    the file that was run and is not repeated in the output. Each candidate
    line's name is un-escaped before comparison (see
    ``_TAP_NAME_ESCAPE_RE``) so it is matched as the source declares it.

    A ``# SKIP`` or ``# TODO`` directive maps to ``"ERROR"`` -- neither
    ``"PASSED"`` nor ``"FAILED"`` -- because neither is a verdict about the
    behaviour under test. TAP reports ``ok ... # SKIP`` for a test that
    never executed, so calling it PASSED would let a skipped test satisfy
    green-green's "passed before and after": the exact "could not run
    reported as passed" hole FR-4.6 forbids. It reports ``not ok ... #
    TODO`` for a test whose failure is *declared expected*, so calling that
    FAILED would let the RED gate accept a test that never reproduced the
    defect. ``"ERROR"`` is accepted by neither gate, which is the correct,
    fail-closed answer for both.

    Like :func:`_parse_node_outcome`, the first matching line wins. Two
    tests sharing one name are indistinguishable in TAP output, so the
    ambiguity is resolved the same way for both frameworks rather than
    differently in each.

    Returns:
        ``"PASSED"``, ``"FAILED"``, ``"ERROR"``, or ``None`` when the named
        test produced no outcome line at all (its file was missing, or a
        syntax error stopped node before it ran).
    """
    _, _, quoted_name = test_node_id.partition("::")
    wanted = quoted_name.strip('"')
    for match in _TAP_OUTCOME_LINE_RE.finditer(output):
        if _TAP_NAME_ESCAPE_RE.sub(r"\1", match.group("name")) != wanted:
            continue
        if match.group("directive") is not None:
            return "ERROR"
        return "PASSED" if match.group("status") == "ok" else "FAILED"
    return None


def _node_missing_file_diagnostic(raw_output: str) -> str | None:
    """Return node's ``Could not find '<path>'`` line, or ``None`` when absent.

    See ``_NODE_MISSING_FILE_MARKER`` for why node needs this and pytest
    does not.
    """
    for line in raw_output.splitlines():
        if _NODE_MISSING_FILE_MARKER in line:
            return line.strip()
    return None


def default_node_test_runner(test_node_id: str, repo_path: Path) -> TestObservation:
    """Run ``node --test``, scoped to the named test's file, and capture its outcome.

    Mirrors :func:`default_pytest_runner` in the property that matters:
    invoked at FILE scope, never filtered down to the single named test.
    Node does offer ``--test-name-pattern``, but filtering there would make
    a pattern that matches nothing indistinguishable from a file that ran
    and passed -- exit 0, no outcome line -- which is precisely the false
    GREEN the file-scope-plus-outcome-matching discipline exists to
    prevent. Running the whole file always emits one line per test, so the
    named test is either present in the output or provably absent from it.

    ``--test-reporter=tap`` is passed explicitly rather than relied upon.
    Node already prefers TAP over its ``spec`` reporter when stdout is not
    a TTY, which it never is here since ``run_command`` captures pipes, so
    the flag changes no behaviour today; it pins the format this module
    parses to something that does not depend on how the process happens to
    be attached.
    """
    file_part = test_node_id.split("::", 1)[0]
    exit_code, stdout, stderr = run_command(
        ["node", "--test", "--test-reporter=tap", file_part],
        cwd=repo_path,
        timeout=TEST_TIMEOUT,
    )
    combined = "\n".join(part for part in (stdout, stderr) if part)
    node_outcome = _parse_tap_node_outcome(combined, test_node_id)
    return TestObservation(exit_code=exit_code, node_outcome=node_outcome, raw_output=combined)


@dataclass(frozen=True)
class TestFramework:
    """Everything the RED and green-green gates need that differs per test framework.

    The gates' actual logic -- the outside-the-manifest pre-flight, the
    path-scoped stash, the fail-closed restore, the three-part assertion --
    is framework-agnostic and stays in :func:`observe_red`. Only the values
    below vary, so they travel as one value the CLI resolves once from
    configuration and threads through, rather than as six loose parameters
    or (worse) a framework-name branch re-taken at every site that needs
    one of them.

    Attributes:
        name: The ``test_runner`` config value that selects this framework.
        node_id_shape: The node-id shape, quoted verbatim into every
            rejection message. The agent that writes the ``[RED]`` entry is
            the one that has to produce an id of this shape, so a rejection
            that does not state it leaves that agent guessing -- which for
            the node shape (quotes required, spaces allowed) it would
            reliably guess wrong.
        node_id_pattern: Matches this framework's node id inside an agent's
            free-text ``[RED]`` entry.
        failed_exit_code: The code this framework returns when tests ran and
            at least one failed -- the only code the RED gate accepts.
        ok_exit_code: The code this framework returns when every test
            passed -- the only code the green-green check accepts.
        exit_code_reasons: Human-readable explanation per measured exit
            code, consumed by :func:`_exit_code_reason`.
        runner: Runs one named node id in a repo and reports the observation.
        diagnostic: Recovers detail from a run's raw output that the exit
            code does not carry, returning ``None`` when there is none.
    """

    name: str
    node_id_shape: str
    node_id_pattern: re.Pattern[str]
    failed_exit_code: int
    ok_exit_code: int
    exit_code_reasons: Mapping[int, str]
    runner: TestRunner
    diagnostic: Callable[[str], str | None]


PYTEST_FRAMEWORK: TestFramework = TestFramework(
    name="pytest",
    node_id_shape="'<path>.py::<test>'",
    node_id_pattern=_TEST_NODE_ID_TOKEN_RE,
    failed_exit_code=PYTEST_EXIT_TESTS_FAILED,
    ok_exit_code=PYTEST_EXIT_OK,
    exit_code_reasons=_EXIT_CODE_REASONS,
    runner=default_pytest_runner,
    diagnostic=_no_diagnostic,
)

NODE_FRAMEWORK: TestFramework = TestFramework(
    name="node",
    node_id_shape="'<path>::\"<test name>\"' (the test name in double quotes)",
    node_id_pattern=_NODE_TEST_NODE_ID_TOKEN_RE,
    failed_exit_code=NODE_EXIT_TESTS_FAILED,
    ok_exit_code=NODE_EXIT_OK,
    exit_code_reasons=_NODE_EXIT_CODE_REASONS,
    runner=default_node_test_runner,
    diagnostic=_node_missing_file_diagnostic,
)

TEST_FRAMEWORKS: dict[str, TestFramework] = {
    PYTEST_FRAMEWORK.name: PYTEST_FRAMEWORK,
    NODE_FRAMEWORK.name: NODE_FRAMEWORK,
}


def resolve_test_framework(name: str | None) -> TestFramework:
    """Map a configured ``test_runner`` value to its :class:`TestFramework`.

    ``None`` -- what ``config_loader.get_effective_test_runner`` returns for
    a workspace that never declares the key -- resolves to pytest, so every
    config written before this key existed behaves exactly as it did.

    Args:
        name: The effective ``test_runner`` value, or ``None`` when unset.

    Returns:
        The selected framework.

    Raises:
        TddGateRejectionError: On an unrecognized name. ``config-schema.json``
            already rejects one at config-load time, so reaching this branch
            means a caller built a ``RuntimeConfig`` in process instead of
            loading it from YAML. Failing closed keeps that path from
            quietly falling back to pytest and then rejecting every node
            test as uncollected -- a symptom that reads as a broken gate
            rather than as the misconfiguration it is.
    """
    if name is None:
        return PYTEST_FRAMEWORK
    framework = TEST_FRAMEWORKS.get(name)
    if framework is None:
        raise TddGateRejectionError(
            f"ERROR: unknown test_runner '{name}'. Set validate.test_runner (or the per-repo "
            f"repos.<org/repo>.test_runner override) to one of: {', '.join(sorted(TEST_FRAMEWORKS))}."
        )
    return framework


def _exit_code_reason(exit_code: int, framework: TestFramework = PYTEST_FRAMEWORK) -> str:
    """Return a human-readable explanation of a measured runner exit code.

    *framework* defaults to pytest so the two-argument form reads the same
    as the original one-argument one for every existing caller and test.
    """
    reason = framework.exit_code_reasons.get(exit_code)
    if reason is None:
        return f"{framework.name} exited with an unexpected code ({exit_code})"
    return reason


def _build_rejection_message(
    unit_id: str,
    test_node_id: str | None,
    exit_code: int | None,
    detail: str,
    framework: TestFramework = PYTEST_FRAMEWORK,
) -> str:
    """Build the standard RED-gate rejection message (spec FR-4.2/FR-4.5).

    Names the task, the test node id, the observed exit code, what was
    expected, and all three legitimate remedies, per the spec's error
    handling contract.

    The "Expected" line names *framework* and its own failure exit code
    rather than pytest's, because the whole point of the message is to tell
    an agent working in a node repo what a passing run would have looked
    like there; a message that says "pytest exit code 1" to an agent that
    never invoked pytest is worse than no message.
    """
    node_display = test_node_id if test_node_id else "(no named test found)"
    exit_display = str(exit_code) if exit_code is not None else "(not observed)"
    lines = [
        f"ERROR: RED gate rejected task '{unit_id}'.",
        f"  Test node id: {node_display}",
        f"  Observed exit code: {exit_display}",
        (
            f"  Expected: {framework.name} exit code {framework.failed_exit_code} (tests failed) with the "
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


def find_named_test_node_id(work_unit_content: str, framework: TestFramework = PYTEST_FRAMEWORK) -> str | None:
    """Find the most recently named test node id in the TDD Cycle Log.

    Scans the agent-written ``[RED]`` entries inside the ``## TDD Cycle
    Log`` section (never ``[RED_OBSERVED]``, ``[GREEN]``, or ``[REFACTOR]``
    entries, and never text outside that section), from most recent to
    oldest, and returns the last *framework*-shaped token found in the first
    entry that contains one -- ``<path>.py::<test>`` for pytest,
    ``<path>::"<test name>"`` for node.

    Args:
        work_unit_content: The full text of the work unit's markdown file.
        framework: The configured test framework, whose ``node_id_pattern``
            defines the shape searched for. Defaults to pytest.

    Returns:
        The node id string, or ``None`` when no RED entry names one.
    """
    section_match = TDD_CYCLE_LOG_SECTION_BODY_RE.search(work_unit_content)
    if section_match is None:
        return None
    section_body = section_match.group(1)
    red_messages = [match.group("message") for match in _RED_ENTRY_LINE_RE.finditer(section_body)]
    for message in reversed(red_messages):
        tokens = framework.node_id_pattern.findall(message)
        if tokens:
            return tokens[-1].rstrip(_NODE_ID_TRAILING_PUNCTUATION)
    return None


def _build_failure_detail(
    observation: TestObservation,
    framework: TestFramework,
    pushed: bool,
    prod_paths: Sequence[str],
    repo_path: Path,
) -> str:
    """Explain a rejected observation: why the run was not a genuine RED.

    Split out of :func:`observe_red` because it is pure string assembly over
    an already-made decision -- the caller has established that the exit code
    or the named test's outcome disqualifies the run, and this only says so
    legibly. Keeping it here leaves ``observe_red`` reading as the gate's
    control flow and nothing else.
    """
    detail = (
        f"{_exit_code_reason(observation.exit_code, framework)}; named test outcome was "
        f"{observation.node_outcome or 'not collected'}."
    )

    # Detail the exit code itself cannot carry. Only the node framework has
    # any (see ``_NODE_MISSING_FILE_MARKER``); for pytest this is always
    # None, because its distinct exit codes 2 and 4 already say what a
    # diagnostic line would.
    diagnostic = framework.diagnostic(observation.raw_output)
    if diagnostic is not None:
        detail += f" Runner diagnostic: {diagnostic}"

    if not pushed:
        # Nothing was stashed, so the run above observed the tree exactly as
        # found. That is legitimate when the committed baseline is itself the
        # "before" state (test-first TDD: a pinning test committed alongside
        # still-broken production source genuinely fails here). But when the
        # named test PASSES in that situation, the cause is specifically that
        # no production change was removed -- the fix is already in the
        # committed baseline -- and the bare outcome above reports only the
        # symptom, leaving the operator to reverse-engineer why. Name the
        # cause. Diagnostic only: the pass/fail decision is unchanged, and no
        # reconstruction is attempted or implied.
        detail += (
            f" Note: 'git stash push -u -- {' '.join(prod_paths)}' removed nothing, so this run "
            f"observed the working tree as found. Every production-source row is already committed "
            f"or absent in {repo_path}, which is why the test could not fail. The executor stages "
            f"production changes and leaves committing to 'devbench git-ops', so this usually means "
            f"the rows were committed out of band -- for example an operator commit that snapshotted "
            f"this task's in-flight files. To re-derive an observable RED, commit the removal of the "
            f"production change so its content returns to a staged, uncommitted state, then re-run."
        )
    return detail


def observe_red(
    unit_id: str,
    repo_path: Path,
    manifest_paths: Sequence[str],
    work_unit_content: str,
    *,
    framework: TestFramework = PYTEST_FRAMEWORK,
    test_runner: TestRunner | None = None,
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
        framework: The test framework this repo is configured for, supplying
            the node-id shape to search for, the exit codes to assert on,
            the exit-code explanations quoted in rejection messages, and
            the runner to invoke. Defaults to :data:`PYTEST_FRAMEWORK`, so a
            caller that passes neither keyword behaves exactly as this
            function did before node support existed. Resolved from
            ``validate.test_runner`` by the CLI (see
            :func:`resolve_test_framework`).
        test_runner: Overrides ``framework.runner`` when given. The two
            knobs are separate because they answer different questions:
            *framework* is the operator's configured choice and carries the
            message and assertion vocabulary that goes with it, while
            *test_runner* exists purely as a test seam for controlling or
            failing the test step deterministically (e.g. proving ``git
            stash pop`` runs even when this callable raises). Collapsing
            them would force such a test to hand-build a whole
            :class:`TestFramework` just to swap one callable. ``None`` --
            the default -- means "use the framework's own runner".

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
    runner = framework.runner if test_runner is None else test_runner

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
                framework,
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
                framework,
            )
        )

    test_node_id = find_named_test_node_id(work_unit_content, framework)
    if test_node_id is None:
        raise TddGateRejectionError(
            _build_rejection_message(
                unit_id,
                None,
                None,
                f"no named test node id (a {framework.node_id_shape} token) was found in the "
                f"agent-written [RED] entries of the TDD Cycle Log, which is the shape the "
                f"configured '{framework.name}' test runner requires.",
                framework,
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
                framework,
            )
        )

    test_exception: BaseException | None = None
    observation: TestObservation | None = None
    pop_error: str | None = None
    try:
        observation = runner(test_node_id, repo_path)
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
        raise TddGateRejectionError(
            _build_rejection_message(unit_id, test_node_id, None, detail, framework)
        ) from test_exception

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

    if observation.exit_code != framework.failed_exit_code or observation.node_outcome != "FAILED":
        detail = _build_failure_detail(observation, framework, pushed, prod_paths, repo_path)
        raise TddGateRejectionError(
            _build_rejection_message(unit_id, test_node_id, observation.exit_code, detail, framework)
        )

    failure_digest = hashlib.sha256(observation.raw_output.encode("utf-8")).hexdigest()
    return RedObservation(exit_code=observation.exit_code, test_node_id=test_node_id, failure_digest=failure_digest)
