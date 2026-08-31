"""Issue #137 regression: the manifest-amender Step B.reject recipe must not
prescribe git revert forms that guard-destructive-git.sh blocks, and must not
swallow a blocked (or otherwise failing) revert behind ``|| true``.

While rejecting E3-F2-S1-T1's amendment, the manifest_amender logged a
recipe-deviation escalation: two of the three revert calls prescribed by
Step B.reject in ``plugin/devbench-orchestrate/agents/manifest-amender.md``
(``git checkout -- <path>`` and ``git clean -f -- <path>``) are refused by the
guard-destructive-git PreToolUse hook, so the amender could not follow its own
documented recipe. Worse, all three revert calls were suffixed with
``2>/dev/null || true``, so the hook's refusal (and any genuine git failure)
was silently swallowed and the recipe reported success while leaving the
rejected request's production edits in the worktree -- precisely the leak
into subsequent tasks that Step B.reject exists to prevent, and a violation
of the project fail-fast standard forbidding silent failure.

This test extracts the Step B.reject fenced bash block directly out of the
shipped prompt (never a hardcoded restatement of the recipe body, so prompt
drift is caught rather than duplicated) and asserts:

  AC-FUNC-001 no path-scoped worktree-overwriting checkout and no forced-clean
  AC-FUNC-002 only the guard's own named replacement forms are used
  AC-FUNC-003 tracked-vs-untracked is decided with 'git ls-files --error-unmatch'
  AC-FUNC-004 fail-fast: 'set -euo pipefail', no '|| true', no stderr-to-null
  AC-FUNC-005 the recipe forbids DEVBENCH_ALLOW_DESTRUCTIVE_GIT and names
              log-comment escalation instead
  AC-TEST-002 every extracted git command line is one the guard permits

Note (E3-F2-S1-T9): the shipped-prompt path constant, the '**Step B.reject**'
fence-extraction regex, the module-scoped file-text fixture and the
file-existence assertion are shared with the sibling doc-sync suites
(``test_orchestrate_skill_reject_recipe_sync.py``,
``test_manifest_amendments_doc_reject_recipe_sync.py``) via
``tests/test_integration/conftest.py`` -- ``MANIFEST_AMENDER_PATH``,
``STEP_B_REJECT_FENCE_PATTERN``, the ``manifest_amender_text`` fixture and
``assert_manifest_amender_file_exists``. This module consumes those shared
pieces rather than hand-copying them, and defines only what those sibling
suites do not need: the detailed content assertions over the recipe body
(AC-FUNC-001 through AC-FUNC-005) and the guard-subprocess conformance
check (AC-TEST-002).
"""

from __future__ import annotations

import json
import os
import re
import subprocess
from pathlib import Path

import pytest
from test_integration.conftest import (
    MANIFEST_AMENDER_PATH,
    STEP_B_REJECT_FENCE_PATTERN,
    assert_manifest_amender_file_exists,
)

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
GUARD_PATH = REPO_ROOT / "plugin" / "devbench-orchestrate" / "scripts" / "guard-destructive-git.sh"


@pytest.fixture(scope="module")
def reject_recipe(manifest_amender_text: str) -> str:
    """The extracted Step B.reject fenced bash block body (never a
    hardcoded copy -- pulled fresh from the shipped prompt every run via
    the shared ``STEP_B_REJECT_FENCE_PATTERN``).
    """
    match = STEP_B_REJECT_FENCE_PATTERN.search(manifest_amender_text)
    assert match is not None, (
        "Could not locate the Step B.reject fenced bash block in "
        f"{MANIFEST_AMENDER_PATH}. The '**Step B.reject**' heading immediately "
        "followed by a ```bash ... ``` fence is the extraction contract this "
        "test relies on."
    )
    return match.group("code")


def _clean_env() -> dict[str, str]:
    """Env with legacy DEVBENCH_WORKSPACE_ROOT / DEVBENCH_LOG_FILE stripped,
    matching the convention in tests/test_plugin/test_guard_verdict_format.py
    for driving a hook script via subprocess with crafted stdin.

    DEVBENCH_ALLOW_DESTRUCTIVE_GIT is force-set to '0' (never merely
    stripped) so an ambient override exported in the invoking shell can
    never neuter the guard-conformance assertions below: guard-destructive-
    git.sh treats an unset variable and '0' identically, but forcing the
    value here makes the hermetic contract explicit and immune to whatever
    the surrounding environment happens to export.
    """
    env = {k: v for k, v in os.environ.items() if k not in ("DEVBENCH_WORKSPACE_ROOT", "DEVBENCH_LOG_FILE")}
    env["DEVBENCH_ALLOW_DESTRUCTIVE_GIT"] = "0"
    return env


def _run_guard(command: str) -> subprocess.CompletedProcess[str]:
    stdin = json.dumps({"tool_name": "Bash", "tool_input": {"command": command}})
    return subprocess.run(
        ["bash", str(GUARD_PATH)],
        input=stdin,
        capture_output=True,
        text=True,
        check=False,
        env=_clean_env(),
    )


def _git_command_lines(block: str) -> list[str]:
    """Every non-comment line in *block* that contains a `git` token,
    i.e. the actual git invocations the guard is meant to police.
    """
    lines = []
    for line in block.splitlines():
        stripped = line.strip()
        if stripped.startswith("#"):
            continue
        if re.search(r"\bgit\b", stripped):
            lines.append(stripped)
    return lines


@pytest.mark.integration
class TestStepBRejectRecipeExists:
    def test_prompt_file_exists(self) -> None:
        assert_manifest_amender_file_exists()

    def test_guard_script_exists(self) -> None:
        assert GUARD_PATH.is_file()

    def test_recipe_extracted_and_non_empty(self, reject_recipe: str) -> None:
        assert reject_recipe.strip(), "extracted Step B.reject recipe body was empty"


@pytest.mark.integration
class TestNoGuardBlockedRevertForms:
    """AC-FUNC-001: neither a path-scoped worktree-overwriting checkout nor
    any forced-clean invocation may appear in the recipe.
    """

    def test_no_checkout_dashdash_revert(self, reject_recipe: str) -> None:
        assert not re.search(r"git\s+(-C\s+\S+\s+)?checkout\s+--\s+", reject_recipe), (
            "Step B.reject still contains a 'git checkout -- <path>' revert, which "
            "guard-destructive-git.sh blocks (issue #137). Use 'git restore "
            "--staged --worktree <path>' for a tracked path instead."
        )

    def test_no_forced_clean(self, reject_recipe: str) -> None:
        assert not re.search(r"git\s+(-C\s+\S+\s+)?clean\s+-[a-zA-Z]*f", reject_recipe), (
            "Step B.reject still contains a 'git clean -f' invocation, which "
            "guard-destructive-git.sh blocks (issue #137). Use an enumerated "
            "'rm <path>' for an untracked path instead."
        )


@pytest.mark.integration
class TestOnlyGuardNamedReplacementForms:
    """AC-FUNC-002: the revert loop must use only the replacement forms
    guard-destructive-git.sh names in its own remediation strings.
    """

    def test_tracked_path_uses_staged_and_worktree_restore(self, reject_recipe: str) -> None:
        assert re.search(r"git\s+-C\s+\S+\s+restore\s+--staged\s+--worktree\s+", reject_recipe), (
            "Step B.reject must revert a tracked path via 'git restore --staged "
            "--worktree <path>' -- guard-destructive-git.sh's own named "
            "path-scoped revert remediation."
        )

    def test_untracked_path_uses_enumerated_rm(self, reject_recipe: str) -> None:
        assert re.search(r"(^|\n)\s*rm\s+", reject_recipe), (
            "Step B.reject must remove an untracked, newly-created path via an "
            "enumerated 'rm <path>' -- the exact replacement guard-destructive-git.sh's "
            "clean -f rule names in its remediation string ('enumerate untracked "
            "files and rm <path> the ones you authored')."
        )


@pytest.mark.integration
class TestTrackedVsUntrackedDecidedExplicitly:
    """AC-FUNC-003: tracked-vs-untracked must be decided with
    'git ls-files --error-unmatch <path>' before either revert branch runs.
    """

    def test_ls_files_error_unmatch_present(self, reject_recipe: str) -> None:
        assert re.search(r"git\s+-C\s+\S+\s+ls-files\s+--error-unmatch\s+", reject_recipe), (
            "Step B.reject must decide tracked-vs-untracked with "
            "'git ls-files --error-unmatch <path>' before branching, so a "
            "tracked file is never removed outright and an untracked file is "
            "never handed to the restore branch."
        )

    def test_ls_files_check_precedes_both_revert_branches(self, reject_recipe: str) -> None:
        ls_files_match = re.search(r"git\s+-C\s+\S+\s+ls-files\s+--error-unmatch\s+", reject_recipe)
        restore_match = re.search(r"git\s+-C\s+\S+\s+restore\s+--staged\s+--worktree\s+", reject_recipe)
        rm_match = re.search(r"(^|\n)\s*rm\s+", reject_recipe)
        assert ls_files_match and restore_match and rm_match
        assert ls_files_match.start() < restore_match.start(), "ls-files check must precede the tracked-path restore"
        assert ls_files_match.start() < rm_match.start(), "ls-files check must precede the untracked-path rm"


@pytest.mark.integration
class TestFailFastNoSilentSwallowing:
    """AC-FUNC-004: no command discards a non-zero exit status."""

    def test_opens_with_set_euo_pipefail(self, reject_recipe: str) -> None:
        first_line = next(line for line in reject_recipe.splitlines() if line.strip())
        assert first_line.strip() == "set -euo pipefail", (
            f"Step B.reject recipe must open with 'set -euo pipefail'; found {first_line!r}."
        )

    def test_no_or_true_swallowing(self, reject_recipe: str) -> None:
        assert "|| true" not in reject_recipe, (
            "Step B.reject still contains '|| true', which discards a non-zero "
            "exit status (including a guard refusal) and reports false success "
            "-- violates the project fail-fast standard (issue #137)."
        )

    def test_no_stderr_to_null_redirection(self, reject_recipe: str) -> None:
        assert "2>/dev/null" not in reject_recipe, (
            "Step B.reject still redirects stderr to /dev/null, which can hide "
            "the guard's actionable refusal message -- violates fail-fast."
        )
        assert "2>&1" not in reject_recipe, (
            "Step B.reject still merges stderr into a redirected stdout stream, "
            "which can hide diagnostic output -- violates fail-fast."
        )

    def test_failed_tracked_revert_names_the_path_and_exits_nonzero(self, reject_recipe: str) -> None:
        assert re.search(r"could not revert tracked path.*\$f.*exit 1", reject_recipe, re.DOTALL), (
            "a failed tracked-path restore must abort the recipe with a "
            "non-zero exit and a message naming the path that could not be reverted."
        )

    def test_failed_untracked_removal_names_the_path_and_exits_nonzero(self, reject_recipe: str) -> None:
        assert re.search(r"could not remove untracked path.*\$f.*exit 1", reject_recipe, re.DOTALL), (
            "a failed untracked-path removal must abort the recipe with a "
            "non-zero exit and a message naming the path that could not be removed."
        )

    def test_no_unchecked_for_loop_command_substitution(self, reject_recipe: str) -> None:
        """'set -e' does NOT apply to a command substitution used directly as
        a 'for ... in $(...)' list word: 'set -euo pipefail; for f in $(python3
        -c "import sys; sys.exit(3)"); do echo x; done; echo REACHED' prints
        REACHED with rc=0 even under strict mode. If the path enumeration is a
        bare 'for f in $(cmd ...); do', a missing/schema-drifted request file
        makes the enumerator fail, the loop silently iterates zero times, and
        the recipe falls through to the archive step reporting success while
        the rejected request's production edits remain in the worktree --
        exactly the leak Step B.reject exists to prevent. The enumeration must
        instead be captured into a variable with its own explicit failure
        branch (e.g. 'FILES=$(cmd ...) || { ...; exit 1; }') before the 'for'
        loop iterates over that variable.
        """
        assert not re.search(r"for\s+\w+\s+in\s+\$\(", reject_recipe), (
            "Step B.reject contains a 'for <var> in $(...)' loop whose list is "
            "a command substitution evaluated directly: 'set -e' does not cover "
            "a failing command substitution in for-list position, so a failure "
            "there silently no-ops the loop instead of aborting the recipe. "
            "Capture the enumeration into a variable with an explicit "
            "'|| { ...; exit 1; }' failure branch first, then iterate over that "
            "variable."
        )


@pytest.mark.integration
class TestNoDestructiveGitOverrideEscalateInstead:
    """AC-FUNC-005: the recipe must forbid DEVBENCH_ALLOW_DESTRUCTIVE_GIT and
    name log-comment escalation instead, matching the guard's own wording.
    """

    def test_never_set_override_env_var(self, reject_recipe: str) -> None:
        assert "NEVER set DEVBENCH_ALLOW_DESTRUCTIVE_GIT" in reject_recipe, (
            "Step B.reject must explicitly state that an agent must NEVER set "
            "DEVBENCH_ALLOW_DESTRUCTIVE_GIT, matching the guard script's own "
            "'Operators (not agents) set this' wording and the no-hook-bypass rule."
        )

    def test_names_log_comment_escalation(self, reject_recipe: str) -> None:
        assert "escalate via log-comment" in reject_recipe, (
            "Step B.reject must name log-comment escalation as the path when a "
            "revert genuinely cannot complete, matching the guard's 'Agents must "
            "escalate via log-comment' remediation wording."
        )


@pytest.mark.integration
class TestEveryGitCommandLineGuardPermitted:
    """AC-TEST-002: drive every extracted git command line through
    guard-destructive-git.sh as a subprocess and require exit zero, so the
    prompt and the guard can never silently disagree again.
    """

    def test_git_command_lines_are_non_empty(self, reject_recipe: str) -> None:
        lines = _git_command_lines(reject_recipe)
        assert lines, "expected at least one git command line in the Step B.reject recipe"

    def test_every_git_command_line_passes_the_guard(self, reject_recipe: str) -> None:
        lines = _git_command_lines(reject_recipe)
        assert lines
        for line in lines:
            result = _run_guard(line)
            assert result.returncode == 0, (
                f"guard-destructive-git.sh blocked a Step B.reject command line: {line!r}\nstderr: {result.stderr}"
            )
