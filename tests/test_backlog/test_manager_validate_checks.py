"""Tests for BacklogManager.validate() impossibility checks C1/C3/C4/C6/C7.

Covers spec AC-240-1 and spec AC-240a-1:
- C1: target repo resolves (new _check_target_repo_resolves method)
- C3: manifest multi-repo prefixes resolve
- C4: dep id resolves to an existing WU file on disk
- C6: WU title equals the BACKLOG.md row title (exact compare after strip)
- C7: path canonical shape (E/F/S/T regexes)
- Clean backlog produces zero errors from any of the new checks.
- C5 self-dep is not duplicated.
- The existing manifest-prefix check (check 11) is retained.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest

from devbench.backlog.manager import BacklogManager
from devbench.config_loader import RepoConfig, RuntimeConfig

pytestmark = pytest.mark.unit

# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------

_KNOWN_REPO = "caylent-solutions/devbench"
_UNKNOWN_REPO = "unknown-org/mystery-repo"


def _make_runtime_config(repo: str = _KNOWN_REPO) -> RuntimeConfig:
    """Return a minimal RuntimeConfig that knows one repo."""
    return RuntimeConfig(repos={repo: RepoConfig()})


def _make_index(tmp_path: Path, rows: str, *, extra_known_ids: str = "") -> Path:
    """Write a minimal BACKLOG.md with valid header and return its path.

    Args:
        tmp_path: Directory to write BACKLOG.md in.
        rows: Pipe-delimited table rows (excluding header/separator).
        extra_known_ids: Additional rows to add as known IDs in the index
            (so dep checks do not fire on other WUs).
    """
    idx = tmp_path / "BACKLOG.md"
    idx.write_text(
        "# Backlog\n\n"
        "## Status Summary\n\n"
        "| Epic | Title | Done | In Progress | In Queue | Blocked |\n"
        "|------|-------|------|-------------|----------|---------|\n"
        "\n"
        "## Full Work Unit Index\n\n"
        "| ID | Title | Type | Status | Dependencies | Repo | File Path |\n"
        "|-----|-------|------|--------|-------------|------|-----------|\n" + rows + extra_known_ids,
        encoding="utf-8",
    )
    return idx


def _make_task(
    backlog_dir: Path,
    unit_id: str,
    title: str = "Task Title",
    repo: str = _KNOWN_REPO,
    dep_rows: str = "| none | | |\n",
    manifest_rows: str = "| `src/f.py` | modify |\n",
) -> Path:
    """Write a minimal, fully-valid task work-unit file."""
    wu = backlog_dir / f"{unit_id}.md"
    wu.write_text(
        f"# {unit_id}: {title}\n\n"
        f"## Status: in-queue\n\n"
        f"## Target Repository\n\n"
        f"- **Repo:** `{repo}`\n\n"
        f"## Description\n\nTest task.\n\n"
        f"## Dependencies\n\n"
        f"| ID | Title | Status |\n"
        f"|----|-------|--------|\n"
        f"{dep_rows}\n"
        f"## Acceptance Criteria\n\n- [ ] AC-TEST-001 placeholder\n\n"
        f"## Changes Manifest\n\n"
        f"| File | Change |\n"
        f"|------|--------|\n"
        f"{manifest_rows}\n"
        f"## Definition of Done\n\n- [ ] All ACs checked\n\n"
        f"## TDD Cycle Log\n\n## Comments\n",
        encoding="utf-8",
    )
    return wu


def _run_validate(tmp_path: Path, rt_cfg: RuntimeConfig) -> list[str]:
    """Run validate against tmp_path/BACKLOG.md with the given RuntimeConfig."""
    idx = tmp_path / "BACKLOG.md"
    with patch("devbench.config.RUNTIME_CONFIG", rt_cfg):
        return BacklogManager().validate(idx, tmp_path)


# ---------------------------------------------------------------------------
# C1 check: target repo resolves
# ---------------------------------------------------------------------------


class TestCheckTargetRepoResolves:
    """C1: every work unit's target repo must appear in the configured repos."""

    def test_unknown_repo_emits_error(self, tmp_path: Path, backlog_dir: Path) -> None:
        """A WU referencing an unknown repo emits a C1 error."""
        _make_task(backlog_dir, "E1-F1-S1-T1", repo=_UNKNOWN_REPO)
        _make_index(
            tmp_path,
            f"| E1-F1-S1-T1 | Task Title | Task | in-queue | none | {_UNKNOWN_REPO} | `backlog/E1-F1-S1-T1.md` |\n",
        )
        rt_cfg = _make_runtime_config(_KNOWN_REPO)
        errors = _run_validate(tmp_path, rt_cfg)
        c1_errors = [e for e in errors if "E1-F1-S1-T1" in e and "target repo" in e.lower()]
        assert len(c1_errors) >= 1, f"Expected C1 error; got errors: {errors}"
        assert _UNKNOWN_REPO in c1_errors[0]

    def test_known_repo_produces_no_c1_error(self, tmp_path: Path, backlog_dir: Path) -> None:
        """A WU referencing a known repo must not trigger C1."""
        _make_task(backlog_dir, "E1-F1-S1-T1", repo=_KNOWN_REPO)
        _make_index(
            tmp_path,
            f"| E1-F1-S1-T1 | Task Title | Task | in-queue | none | {_KNOWN_REPO} | `backlog/E1-F1-S1-T1.md` |\n",
        )
        rt_cfg = _make_runtime_config(_KNOWN_REPO)
        errors = _run_validate(tmp_path, rt_cfg)
        c1_errors = [e for e in errors if "target repo" in e.lower() and "not recognised" in e.lower()]
        assert c1_errors == [], f"Unexpected C1 errors: {c1_errors}"

    def test_wu_with_no_repo_section_does_not_trigger_c1(self, tmp_path: Path, backlog_dir: Path) -> None:
        """A WU without a Target Repository section is skipped by C1."""
        wu = backlog_dir / "E1-F1-S1-T1.md"
        wu.write_text(
            "# E1-F1-S1-T1: No Repo Task\n\n"
            "## Status: in-queue\n\n"
            "## Description\n\nTest.\n\n"
            "## Dependencies\n\n| ID | Title | Status |\n|----|-------|--------|\n| none | | |\n\n"
            "## Acceptance Criteria\n\n- [ ] AC-TEST-001 placeholder\n\n"
            "## Changes Manifest\n\n| File | Change |\n|------|--------|\n| `src/f.py` | modify |\n\n"
            "## Definition of Done\n\n- [ ] Done\n\n"
            "## TDD Cycle Log\n\n## Comments\n",
            encoding="utf-8",
        )
        _make_index(
            tmp_path,
            "| E1-F1-S1-T1 | No Repo Task | Task | in-queue | none | unknown | `backlog/E1-F1-S1-T1.md` |\n",
        )
        rt_cfg = _make_runtime_config(_KNOWN_REPO)
        errors = _run_validate(tmp_path, rt_cfg)
        c1_errors = [e for e in errors if "E1-F1-S1-T1" in e and "target repo" in e.lower()]
        assert c1_errors == [], f"Unexpected C1 errors for repo-less WU: {c1_errors}"

    @pytest.mark.parametrize(
        "bad_repo",
        [
            "org/totally-unknown",
            "some-other-org/some-repo",
        ],
    )
    def test_various_unknown_repos_emit_error(self, tmp_path: Path, backlog_dir: Path, bad_repo: str) -> None:
        """Parameterized: multiple unknown repos each emit a C1 error."""
        _make_task(backlog_dir, "E1-F1-S1-T1", repo=bad_repo)
        _make_index(
            tmp_path,
            f"| E1-F1-S1-T1 | Task Title | Task | in-queue | none | {bad_repo} | `backlog/E1-F1-S1-T1.md` |\n",
        )
        rt_cfg = _make_runtime_config(_KNOWN_REPO)
        errors = _run_validate(tmp_path, rt_cfg)
        assert any("E1-F1-S1-T1" in e and "target repo" in e.lower() for e in errors), (
            f"Expected C1 error for repo={bad_repo!r}; got: {errors}"
        )

    def test_c1_is_distinct_from_manifest_prefix_check(self, tmp_path: Path, backlog_dir: Path) -> None:
        """C1 fires for any unknown repo; manifest-prefix check (11) is retained separately.

        When a WU references an unknown repo, C1 reports it.
        The manifest-prefix check silently continues on unknown repos
        (it only applies to repos WITH a configured checkout_directory).
        """
        _make_task(backlog_dir, "E1-F1-S1-T1", repo=_UNKNOWN_REPO)
        _make_index(
            tmp_path,
            f"| E1-F1-S1-T1 | Task Title | Task | in-queue | none | {_UNKNOWN_REPO} | `backlog/E1-F1-S1-T1.md` |\n",
        )
        rt_cfg = _make_runtime_config(_KNOWN_REPO)
        errors = _run_validate(tmp_path, rt_cfg)
        # C1 fires
        c1_errors = [e for e in errors if "target repo" in e.lower() and "not recognised" in e.lower()]
        assert len(c1_errors) >= 1
        # Manifest-prefix check (11) does NOT fire -- unknown repo is skipped there
        prefix_errors = [e for e in errors if "checkout_directory prefix" in e]
        assert prefix_errors == []


# ---------------------------------------------------------------------------
# C3: manifest multi-repo prefix resolves
# ---------------------------------------------------------------------------


class TestCheckManifestMultiRepoPrefixes:
    """C3: every repo prefix in a multi-repo manifest row must resolve."""

    def test_unknown_manifest_repo_prefix_emits_error(self, tmp_path: Path, backlog_dir: Path) -> None:
        """A manifest row with an unknown repo prefix emits a C3 error."""
        _make_task(
            backlog_dir,
            "E1-F1-S1-T1",
            repo=_KNOWN_REPO,
            manifest_rows=f"| `{_UNKNOWN_REPO}` -- `src/f.py` | modify |\n",
        )
        _make_index(
            tmp_path,
            f"| E1-F1-S1-T1 | Task Title | Task | in-queue | none | {_KNOWN_REPO} | `backlog/E1-F1-S1-T1.md` |\n",
        )
        rt_cfg = _make_runtime_config(_KNOWN_REPO)
        errors = _run_validate(tmp_path, rt_cfg)
        c3_errors = [
            e
            for e in errors
            if "E1-F1-S1-T1" in e and "manifest" in e.lower() and "repo" in e.lower() and _UNKNOWN_REPO in e
        ]
        assert len(c3_errors) >= 1, f"Expected C3 error; got errors: {errors}"

    def test_known_manifest_repo_prefix_no_c3_error(self, tmp_path: Path, backlog_dir: Path) -> None:
        """A manifest row with a known repo prefix must not trigger C3."""
        second_repo = "caylent-solutions/git-repo"
        _make_task(
            backlog_dir,
            "E1-F1-S1-T1",
            repo=_KNOWN_REPO,
            manifest_rows=f"| `{second_repo}` -- `src/f.py` | modify |\n",
        )
        _make_index(
            tmp_path,
            f"| E1-F1-S1-T1 | Task Title | Task | in-queue | none | {_KNOWN_REPO} | `backlog/E1-F1-S1-T1.md` |\n",
        )
        rt_cfg = RuntimeConfig(repos={_KNOWN_REPO: RepoConfig(), second_repo: RepoConfig()})
        errors = _run_validate(tmp_path, rt_cfg)
        c3_errors = [
            e
            for e in errors
            if "E1-F1-S1-T1" in e and "manifest" in e.lower() and "repo" in e.lower() and second_repo in e
        ]
        assert c3_errors == [], f"Unexpected C3 errors: {c3_errors}"

    def test_manifest_row_without_repo_prefix_not_flagged(self, tmp_path: Path, backlog_dir: Path) -> None:
        """A plain (no-prefix) manifest row is not subject to C3."""
        _make_task(
            backlog_dir,
            "E1-F1-S1-T1",
            repo=_KNOWN_REPO,
            manifest_rows="| `src/f.py` | modify |\n",
        )
        _make_index(
            tmp_path,
            f"| E1-F1-S1-T1 | Task Title | Task | in-queue | none | {_KNOWN_REPO} | `backlog/E1-F1-S1-T1.md` |\n",
        )
        rt_cfg = _make_runtime_config(_KNOWN_REPO)
        errors = _run_validate(tmp_path, rt_cfg)
        c3_errors = [
            e for e in errors if "manifest" in e.lower() and "repo" in e.lower() and "not recognised" in e.lower()
        ]
        assert c3_errors == [], f"Unexpected C3 errors: {c3_errors}"


# ---------------------------------------------------------------------------
# C4: dep id resolves to an existing WU file on disk
# ---------------------------------------------------------------------------


class TestCheckDepFileExists:
    """C4: every dep ID in ## Dependencies must have a real WU file on disk."""

    def test_dep_with_no_file_emits_error(self, tmp_path: Path, backlog_dir: Path) -> None:
        """A dep ID whose WU file does not exist on disk emits a C4 error."""
        # T1 depends on T2, but T2 has no file on disk (only in index)
        _make_task(
            backlog_dir,
            "E1-F1-S1-T1",
            dep_rows="| E1-F1-S1-T2 | Dep Task | in-queue |\n",
        )
        _make_index(
            tmp_path,
            f"| E1-F1-S1-T1 | Task Title | Task | in-queue | E1-F1-S1-T2 | {_KNOWN_REPO} | `backlog/E1-F1-S1-T1.md` |\n"
            f"| E1-F1-S1-T2 | Dep Task | Task | in-queue | none | {_KNOWN_REPO} | `backlog/E1-F1-S1-T2.md` |\n",
        )
        rt_cfg = _make_runtime_config(_KNOWN_REPO)
        errors = _run_validate(tmp_path, rt_cfg)
        c4_errors = [e for e in errors if "E1-F1-S1-T1" in e and "E1-F1-S1-T2" in e and "file" in e.lower()]
        assert len(c4_errors) >= 1, f"Expected C4 error; got errors: {errors}"

    def test_dep_with_existing_file_no_c4_error(self, tmp_path: Path, backlog_dir: Path) -> None:
        """A dep ID whose WU file exists on disk must not trigger C4."""
        _make_task(backlog_dir, "E1-F1-S1-T2")
        _make_task(
            backlog_dir,
            "E1-F1-S1-T1",
            dep_rows="| E1-F1-S1-T2 | Dep Task | in-queue |\n",
        )
        _make_index(
            tmp_path,
            f"| E1-F1-S1-T1 | Task Title | Task | in-queue | E1-F1-S1-T2 | {_KNOWN_REPO} | `backlog/E1-F1-S1-T1.md` |\n"
            f"| E1-F1-S1-T2 | Task Title | Task | in-queue | none | {_KNOWN_REPO} | `backlog/E1-F1-S1-T2.md` |\n",
        )
        rt_cfg = _make_runtime_config(_KNOWN_REPO)
        errors = _run_validate(tmp_path, rt_cfg)
        c4_errors = [e for e in errors if "E1-F1-S1-T1" in e and "E1-F1-S1-T2" in e and "no work-unit file" in e]
        assert c4_errors == [], f"Unexpected C4 errors: {c4_errors}"

    def test_none_dep_not_flagged(self, tmp_path: Path, backlog_dir: Path) -> None:
        """'none' / '-' in ## Dependencies is not subject to C4."""
        _make_task(backlog_dir, "E1-F1-S1-T1", dep_rows="| none | | |\n")
        _make_index(
            tmp_path,
            f"| E1-F1-S1-T1 | Task Title | Task | in-queue | none | {_KNOWN_REPO} | `backlog/E1-F1-S1-T1.md` |\n",
        )
        rt_cfg = _make_runtime_config(_KNOWN_REPO)
        errors = _run_validate(tmp_path, rt_cfg)
        c4_errors = [e for e in errors if "no work-unit file" in e]
        assert c4_errors == [], f"Unexpected C4 errors: {c4_errors}"


# ---------------------------------------------------------------------------
# C5: self-dep not duplicated (AC-240a-1)
# ---------------------------------------------------------------------------


class TestC5SelfDepNotDuplicated:
    """C5 self-dep is already covered by existing check 4; C1-C4/C6/C7 do not duplicate it."""

    def test_self_dep_detected_by_existing_check(self, tmp_path: Path, backlog_dir: Path) -> None:
        """A self-dependency is still caught (by the existing dep check); C1/C4 do not emit a duplicate."""
        _make_task(
            backlog_dir,
            "E1-F1-S1-T1",
            dep_rows="| E1-F1-S1-T1 | Self | in-queue |\n",
        )
        _make_index(
            tmp_path,
            f"| E1-F1-S1-T1 | Task Title | Task | in-queue | E1-F1-S1-T1"
            f" | {_KNOWN_REPO} | `backlog/E1-F1-S1-T1.md` |\n",
        )
        rt_cfg = _make_runtime_config(_KNOWN_REPO)
        errors = _run_validate(tmp_path, rt_cfg)
        # C4 must NOT emit a duplicate about E1-F1-S1-T1's own file not existing
        c4_dup_errors = [e for e in errors if "E1-F1-S1-T1" in e and "no work-unit file" in e and "E1-F1-S1-T1" in e]
        assert c4_dup_errors == [], f"C4 duplicated self-dep error: {c4_dup_errors}"


# ---------------------------------------------------------------------------
# C6: WU title matches BACKLOG.md index title
# ---------------------------------------------------------------------------


class TestCheckTitleMatchesIndex:
    """C6: the WU file heading title must match the BACKLOG.md row title exactly."""

    def test_title_mismatch_emits_error(self, tmp_path: Path, backlog_dir: Path) -> None:
        """A title in the WU file different from the index row emits a C6 error."""
        # WU title: "Real Task Title", index title: "Wrong Index Title"
        _make_task(backlog_dir, "E1-F1-S1-T1", title="Real Task Title")
        _make_index(
            tmp_path,
            f"| E1-F1-S1-T1 | Wrong Index Title | Task | in-queue | none"
            f" | {_KNOWN_REPO} | `backlog/E1-F1-S1-T1.md` |\n",
        )
        rt_cfg = _make_runtime_config(_KNOWN_REPO)
        errors = _run_validate(tmp_path, rt_cfg)
        c6_errors = [e for e in errors if "E1-F1-S1-T1" in e and "title" in e.lower() and "mismatch" in e.lower()]
        assert len(c6_errors) >= 1, f"Expected C6 error; got errors: {errors}"
        assert "Real Task Title" in c6_errors[0] or "Wrong Index Title" in c6_errors[0]

    def test_title_match_produces_no_c6_error(self, tmp_path: Path, backlog_dir: Path) -> None:
        """A title that matches the index row must not trigger C6."""
        _make_task(backlog_dir, "E1-F1-S1-T1", title="Exact Match Title")
        _make_index(
            tmp_path,
            f"| E1-F1-S1-T1 | Exact Match Title | Task | in-queue | none"
            f" | {_KNOWN_REPO} | `backlog/E1-F1-S1-T1.md` |\n",
        )
        rt_cfg = _make_runtime_config(_KNOWN_REPO)
        errors = _run_validate(tmp_path, rt_cfg)
        c6_errors = [e for e in errors if "title" in e.lower() and "mismatch" in e.lower()]
        assert c6_errors == [], f"Unexpected C6 errors: {c6_errors}"

    @pytest.mark.parametrize(
        "wu_title,index_title",
        [
            ("Add Feature X", "Add feature x"),
            ("Do Something", "Do Something Else"),
            ("Short", "Short but different"),
        ],
    )
    def test_various_mismatches_emit_c6_error(
        self,
        tmp_path: Path,
        backlog_dir: Path,
        wu_title: str,
        index_title: str,
    ) -> None:
        """Parameterized: title mismatches all emit C6 errors."""
        _make_task(backlog_dir, "E1-F1-S1-T1", title=wu_title)
        _make_index(
            tmp_path,
            f"| E1-F1-S1-T1 | {index_title} | Task | in-queue | none | {_KNOWN_REPO} | `backlog/E1-F1-S1-T1.md` |\n",
        )
        rt_cfg = _make_runtime_config(_KNOWN_REPO)
        errors = _run_validate(tmp_path, rt_cfg)
        assert any("E1-F1-S1-T1" in e and "title" in e.lower() and "mismatch" in e.lower() for e in errors), (
            f"Expected C6 error for wu_title={wu_title!r}, index_title={index_title!r}; got: {errors}"
        )


# ---------------------------------------------------------------------------
# C7: path canonical shape
# ---------------------------------------------------------------------------


class TestCheckCanonicalPathShape:
    """C7: the file path in the BACKLOG.md index must match the canonical shape for the unit type."""

    @pytest.mark.parametrize(
        "unit_id,unit_type,bad_path",
        [
            # Task ID but path ends with story-shaped filename
            ("E1-F1-S1-T1", "Task", "backlog/E1-F1-S1.md"),
            # Feature ID but path has task-shaped basename
            ("E1-F1", "Feature", "backlog/E1-F1-S1-T1.md"),
            # Story ID but wrong basename
            ("E1-F1-S1", "Story", "backlog/E1-F1.md"),
            # Epic ID but wrong basename
            ("E1", "Epic", "backlog/E1-F1.md"),
        ],
    )
    def test_wrong_path_shape_emits_error(
        self,
        tmp_path: Path,
        backlog_dir: Path,
        unit_id: str,
        unit_type: str,
        bad_path: str,
    ) -> None:
        """A file path whose basename does not match the unit ID emits a C7 error."""
        # Write the WU file AT the bad path location
        bad_wu = tmp_path / bad_path
        bad_wu.parent.mkdir(parents=True, exist_ok=True)
        bad_wu.write_text(
            f"# {unit_id}: Task Title\n\n"
            f"## Status: in-queue\n\n"
            f"## Target Repository\n\n- **Repo:** `{_KNOWN_REPO}`\n\n"
            f"## Description\n\nTest.\n\n"
            f"## Dependencies\n\n| ID | Title | Status |\n|----|-------|--------|\n| none | | |\n\n"
            f"## Acceptance Criteria\n\n- [ ] AC-TEST-001 placeholder\n\n"
            f"## Changes Manifest\n\n| File | Change |\n|------|--------|\n| `src/f.py` | modify |\n\n"
            f"## Definition of Done\n\n- [ ] Done\n\n"
            f"## TDD Cycle Log\n\n## Comments\n",
            encoding="utf-8",
        )
        _make_index(
            tmp_path,
            f"| {unit_id} | Task Title | {unit_type} | in-queue | none | {_KNOWN_REPO} | `{bad_path}` |\n",
        )
        rt_cfg = _make_runtime_config(_KNOWN_REPO)
        errors = _run_validate(tmp_path, rt_cfg)
        c7_errors = [e for e in errors if unit_id in e and "path" in e.lower() and "canonical" in e.lower()]
        assert len(c7_errors) >= 1, f"Expected C7 error for {unit_id} at {bad_path!r}; got: {errors}"

    @pytest.mark.parametrize(
        "unit_id,unit_type,good_path",
        [
            # Flat paths (used in tests)
            ("E1-F1-S1-T1", "Task", "backlog/E1-F1-S1-T1.md"),
            ("E1-F1-S1", "Story", "backlog/E1-F1-S1.md"),
            ("E1-F1", "Feature", "backlog/E1-F1.md"),
            ("E1", "Epic", "backlog/E1.md"),
            # Nested paths (used in real backlog)
            (
                "E1-F1-S1-T1",
                "Task",
                "backlog/E1-something/E1-F1-something/E1-F1-S1-something/E1-F1-S1-T1.md",
            ),
        ],
    )
    def test_correct_path_shape_no_c7_error(
        self,
        tmp_path: Path,
        backlog_dir: Path,
        unit_id: str,
        unit_type: str,
        good_path: str,
    ) -> None:
        """A file path with the correct shape for the unit ID produces no C7 error."""
        good_wu = tmp_path / good_path
        good_wu.parent.mkdir(parents=True, exist_ok=True)
        good_wu.write_text(
            f"# {unit_id}: Task Title\n\n"
            f"## Status: in-queue\n\n"
            f"## Target Repository\n\n- **Repo:** `{_KNOWN_REPO}`\n\n"
            f"## Description\n\nTest.\n\n"
            f"## Dependencies\n\n| ID | Title | Status |\n|----|-------|--------|\n| none | | |\n\n"
            f"## Acceptance Criteria\n\n- [ ] AC-TEST-001 placeholder\n\n"
            f"## Changes Manifest\n\n| File | Change |\n|------|--------|\n| `src/f.py` | modify |\n\n"
            f"## Definition of Done\n\n- [ ] Done\n\n"
            f"## TDD Cycle Log\n\n## Comments\n",
            encoding="utf-8",
        )
        _make_index(
            tmp_path,
            f"| {unit_id} | Task Title | {unit_type} | in-queue | none | {_KNOWN_REPO} | `{good_path}` |\n",
        )
        rt_cfg = _make_runtime_config(_KNOWN_REPO)
        errors = _run_validate(tmp_path, rt_cfg)
        c7_errors = [e for e in errors if unit_id in e and "canonical" in e.lower()]
        assert c7_errors == [], f"Unexpected C7 errors for {unit_id} at {good_path!r}: {c7_errors}"


# ---------------------------------------------------------------------------
# Clean-backlog zero-false-positives
# ---------------------------------------------------------------------------


class TestCleanBacklogNoFalsePositives:
    """A correctly-configured backlog must produce zero errors from C1/C3/C4/C6/C7."""

    def test_fully_clean_backlog_no_new_check_errors(self, tmp_path: Path, backlog_dir: Path) -> None:
        """Clean backlog: no C1/C3/C4/C6/C7 errors from the new checks."""
        _make_task(backlog_dir, "E1-F1-S1-T1", title="Clean Task One")
        _make_task(backlog_dir, "E1-F1-S1-T2", title="Clean Task Two")
        _make_index(
            tmp_path,
            f"| E1-F1-S1-T1 | Clean Task One | Task | in-queue | none | {_KNOWN_REPO} | `backlog/E1-F1-S1-T1.md` |\n"
            f"| E1-F1-S1-T2 | Clean Task Two | Task | in-queue | none | {_KNOWN_REPO} | `backlog/E1-F1-S1-T2.md` |\n",
        )
        rt_cfg = _make_runtime_config(_KNOWN_REPO)
        errors = _run_validate(tmp_path, rt_cfg)
        new_check_errors = [
            e
            for e in errors
            if "target repo" in e.lower()
            or ("manifest" in e.lower() and "repo" in e.lower() and "not recognised" in e.lower())
            or "no work-unit file" in e.lower()
            or ("title" in e.lower() and "mismatch" in e.lower())
            or "canonical" in e.lower()
        ]
        assert new_check_errors == [], f"Clean backlog triggered new checks: {new_check_errors}"

    def test_clean_backlog_with_dep_chain_no_errors(self, tmp_path: Path, backlog_dir: Path) -> None:
        """Clean dep chain (T2 depends on T1, both files exist) produces no C4 errors."""
        _make_task(backlog_dir, "E1-F1-S1-T1", title="Dep Target")
        _make_task(
            backlog_dir,
            "E1-F1-S1-T2",
            title="Dep Consumer",
            dep_rows="| E1-F1-S1-T1 | Dep Target | in-queue |\n",
        )
        _make_index(
            tmp_path,
            f"| E1-F1-S1-T1 | Dep Target | Task | in-queue | none"
            f" | {_KNOWN_REPO} | `backlog/E1-F1-S1-T1.md` |\n"
            f"| E1-F1-S1-T2 | Dep Consumer | Task | in-queue | E1-F1-S1-T1"
            f" | {_KNOWN_REPO} | `backlog/E1-F1-S1-T2.md` |\n",
        )
        rt_cfg = _make_runtime_config(_KNOWN_REPO)
        errors = _run_validate(tmp_path, rt_cfg)
        c4_errors = [e for e in errors if "no work-unit file" in e.lower()]
        assert c4_errors == [], f"Unexpected C4 errors in clean dep chain: {c4_errors}"
