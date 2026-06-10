"""Tests for BacklogManager._check_no_committable_manifest_sentinel.

A Changes Manifest row may legitimately be a *sentinel* -- an angle-bracket
token like ``<verification-only>`` -- to document a unit that produces no
committable files, or whose file list is genuinely determined at execution
time (``<source-drift-fix-targets-determined-at-execution>``). Those stay
legal.

What is NOT legal is a *free-form* sentinel that stands in for committable
files it failed to enumerate (e.g.
``<providers/aws/primitives/waf-webacl/ example + aux template files,
determined at execution>``). The git-ops integrity gate
``assert_staged_matches_manifest`` does exact path-set membership and never
expands sentinels, so a unit carrying such a row can pass every judge yet
never commit -- its real staged files are rejected as out-of-manifest. This
check surfaces that latent block at authoring time: a WARNING by default
(back-compat) and an ERROR under ``--strict`` (which ``spec-to-backlog``
runs).

Covers:
- committable-file sentinel with a path separator -> warning (default) / error (strict)
- committable-file sentinel detected by file-creation keyword (no separator) -> warning/error
- recognised no-op sentinel (``<verification-only>``) -> clean
- recognised operator variant (``<verification-only:E1-F1-S1-T2>``) -> clean
- the undetermined-list sentinel (``<source-drift-fix-targets-determined-at-execution>``) -> clean
- a recognised-prefix variant that merely mentions a keyword (``<decision-only: chose a template engine>``) -> clean
- concrete add rows -> clean
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest

from devbench.backlog.manager import BacklogManager
from devbench.config_loader import RepoConfig, RuntimeConfig

pytestmark = pytest.mark.unit

_REPO = "caylent-solutions/devbench"
_FINDING_MARKER = "git-ops integrity gate"


def _make_index(tmp_path: Path, unit_id: str) -> Path:
    idx = tmp_path / "BACKLOG.md"
    idx.write_text(
        "# Backlog\n\n"
        "## Status Summary\n\n"
        "| Epic | Title | Done | In Progress | In Queue | Blocked |\n"
        "|------|-------|------|-------------|----------|---------|\n\n"
        "## Full Work Unit Index\n\n"
        "| ID | Title | Type | Status | Dependencies | Repo | File Path |\n"
        "|-----|-------|------|--------|-------------|------|-----------|\n"
        f"| {unit_id} | Task Title | Task | in-queue | none | {_REPO} | `backlog/{unit_id}.md` |\n",
        encoding="utf-8",
    )
    return idx


def _make_task(backlog_dir: Path, unit_id: str, *, manifest_rows: str) -> None:
    (backlog_dir / f"{unit_id}.md").write_text(
        f"# {unit_id}: Task Title\n\n"
        f"## Status: in-queue\n\n"
        f"## Target Repository\n\n- **Repo:** `{_REPO}`\n\n"
        f"## Description\n\nTest task.\n\n"
        f"## Dependencies\n\n| ID | Title | Status |\n|----|-------|--------|\n| none | | |\n\n"
        f"## Acceptance Criteria\n\n- [ ] AC-FUNC-001: behaviour holds.\n\n"
        f"## Changes Manifest\n\n| File | Change |\n|------|--------|\n{manifest_rows}\n"
        f"## Definition of Done\n\n- [ ] Done\n\n"
        f"## TDD Cycle Log\n\n## Comments\n",
        encoding="utf-8",
    )


def _validate(tmp_path: Path, *, strict: bool = False) -> tuple[list[str], list[str]]:
    idx = tmp_path / "BACKLOG.md"
    cfg = RuntimeConfig(repos={_REPO: RepoConfig()})
    with patch("devbench.config.RUNTIME_CONFIG", cfg):
        return BacklogManager().validate_with_warnings(idx, tmp_path, strict=strict)


def _findings(items: list[str]) -> list[str]:
    return [i for i in items if _FINDING_MARKER in i]


# ---------------------------------------------------------------------------
# Committable-file sentinels are flagged (warning -> error under strict)
# ---------------------------------------------------------------------------


def test_path_separator_sentinel_warns_then_errors_under_strict(tmp_path: Path, backlog_dir: Path) -> None:
    """A sentinel naming a path fragment is the exact E9-F1-S1-T5 incident."""
    _make_index(tmp_path, "E1-F1-S1-T1")
    _make_task(
        backlog_dir,
        "E1-F1-S1-T1",
        manifest_rows=(
            "| `<providers/aws/primitives/waf-webacl/ example + aux "
            "template files per sibling-primitive pattern, determined at execution>` | add |\n"
        ),
    )

    errors_default, warnings_default = _validate(tmp_path)
    assert _findings(warnings_default), "expected a default-mode WARNING for the committable sentinel"
    assert not _findings(errors_default), "default mode must not promote the committable sentinel to an error"
    assert "providers/aws/primitives/waf-webacl" in _findings(warnings_default)[0]

    errors_strict, warnings_strict = _validate(tmp_path, strict=True)
    assert _findings(errors_strict), "strict mode must promote the committable sentinel to an ERROR"
    assert not _findings(warnings_strict)


def test_file_keyword_sentinel_without_separator_is_flagged(tmp_path: Path, backlog_dir: Path) -> None:
    """No path separator, but the sentinel clearly stands in for files to create."""
    _make_index(tmp_path, "E1-F1-S1-T1")
    _make_task(
        backlog_dir,
        "E1-F1-S1-T1",
        manifest_rows="| `<auxiliary template files for the new primitive>` | add |\n",
    )
    errors, warnings = _validate(tmp_path, strict=True)
    assert _findings(errors), "a keyword-bearing committable sentinel must be flagged under strict"


# ---------------------------------------------------------------------------
# Recognised sentinels stay legal
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "sentinel",
    [
        "<verification-only>",
        "<decision-only>",
        "<no changes>",
        "<no-op>",
        "<source-drift-fix-targets-determined-at-execution>",
        "<verification-only:E1-F1-S1-T2>",
        "<decision-only: chose a template engine over hand-rolled files>",
    ],
)
def test_recognised_sentinels_are_clean(tmp_path: Path, backlog_dir: Path, sentinel: str) -> None:
    """No-op / undetermined sentinels and their operator variants are exempt -- even under strict."""
    _make_index(tmp_path, "E1-F1-S1-T1")
    _make_task(backlog_dir, "E1-F1-S1-T1", manifest_rows=f"| `{sentinel}` | add |\n")
    errors, warnings = _validate(tmp_path, strict=True)
    assert not _findings(errors), f"{sentinel!r} must not be flagged: {_findings(errors)}"
    assert not _findings(warnings)


def test_concrete_paths_are_clean(tmp_path: Path, backlog_dir: Path) -> None:
    """Enumerated concrete paths -- the desired authoring shape -- produce no finding."""
    _make_index(tmp_path, "E1-F1-S1-T1")
    _make_task(
        backlog_dir,
        "E1-F1-S1-T1",
        manifest_rows=(
            "| `providers/aws/primitives/waf-webacl/main.tf` | add |\n"
            "| `providers/aws/primitives/waf-webacl/variables.tf` | add |\n"
        ),
    )
    errors, warnings = _validate(tmp_path, strict=True)
    assert not _findings(errors)
    assert not _findings(warnings)
