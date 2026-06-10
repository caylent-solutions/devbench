"""Issue #224 AC-11: the executor PreToolUse hook chain is unchanged post-split.

Pins the EXACT ordered list of guard scripts that fire on each PreToolUse
matcher in the orchestrate plugin's ``hooks/hooks.json``.  Captured from
the pre-split baseline (``docs/plugin-split-0.4.0-baseline.md``) and
asserted verbatim here.

If a future refactor drops, silently adds, or re-orders any guard, this
test fails before the orchestrator behaviour can be silently weakened.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).parent.parent.parent
HOOKS_JSON = REPO_ROOT / "plugin" / "devbench-orchestrate" / "hooks" / "hooks.json"

# Exact ordered list of guard scripts that must fire on each matcher.
# Captured pre-split from plugin/devbench/hooks/hooks.json and pinned
# here as the orchestrate-plugin contract (issue #224 amendment 2).
EXPECTED_PRE_TOOL_USE: dict[str, list[str]] = {
    "Bash": [
        "hook-logger.sh",
        "guard-bash.sh",
        "guard-verdict-format.sh",
        "guard-comment-format.sh",
        "guard-git-stage.sh",
        "guard-destructive-git.sh",
        "guard-review-supervisor-scope.sh",
    ],
    # "Guard the guards": guard-plugin-write.sh runs BEFORE guard-work-unit-write.sh
    # on Write/Edit. It hard-denies (exit 2, no role bypass) Write/Edit to the
    # plugin's own scripts/hooks, the workspace shadow plugin, .claude/settings*
    # files, and the $BASH_ENV target -- closing the self-modification gap that
    # let an orchestrator session edit guard-verdict-format.sh.
    "Write": ["guard-plugin-write.sh", "guard-work-unit-write.sh"],
    "Edit": ["guard-plugin-write.sh", "guard-work-unit-write.sh"],
    ".*": ["hook-logger.sh"],
}


def _script_names_for_matcher(hooks_doc: dict, matcher: str) -> list[str]:
    """Extract just the script basenames (e.g. ``guard-bash.sh``) for the
    given PreToolUse matcher, preserving order.
    """
    for block in hooks_doc["hooks"].get("PreToolUse", []):
        if block.get("matcher") == matcher:
            return [Path(hook["command"].split()[-1]).name for hook in block.get("hooks", [])]
    return []


@pytest.mark.unit
class TestExecutorPreToolUseGuardListUnchanged:
    """Issue #224 AC-11: the ordered guard list per matcher is the
    contract; any drift fails fast.
    """

    @pytest.mark.parametrize(("matcher", "expected"), list(EXPECTED_PRE_TOOL_USE.items()))
    def test_matcher_has_exact_expected_guard_list(self, matcher: str, expected: list[str]) -> None:
        hooks_doc = json.loads(HOOKS_JSON.read_text(encoding="utf-8"))
        actual = _script_names_for_matcher(hooks_doc, matcher)
        assert actual == expected, (
            f"PreToolUse matcher {matcher!r} guard list drifted from the "
            f"issue #224 baseline. Expected (in order): {expected!r}; "
            f"got: {actual!r}. The orchestrate plugin's guard contract is "
            f"pinned -- any deliberate change must update this test in the same commit."
        )

    def test_no_unexpected_pretooluse_matchers(self) -> None:
        """Catch any silently-added PreToolUse matcher (e.g., a future
        guard that hooks NotebookEdit without the operator noticing).
        """
        hooks_doc = json.loads(HOOKS_JSON.read_text(encoding="utf-8"))
        pre_tool_use_matchers = {block.get("matcher") for block in hooks_doc["hooks"].get("PreToolUse", [])}
        assert pre_tool_use_matchers == set(EXPECTED_PRE_TOOL_USE.keys()), (
            f"Issue #224 pins the PreToolUse matchers to {set(EXPECTED_PRE_TOOL_USE.keys())!r}; "
            f"hooks.json declares {pre_tool_use_matchers!r}. "
            "Any addition / removal of a matcher must update this test in the same commit."
        )
