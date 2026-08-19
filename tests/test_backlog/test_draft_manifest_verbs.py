"""The task factory must emit a real Changes Manifest change verb.

``generate_draft_md`` rendered every row as ``TODO -- describe change``. That
is not one of the three verbs the contract allows (``add`` / ``modify`` /
``delete``), so every factory-materialised task carried a placeholder an
operator had to hand-edit before the unit could be worked. With
``task_factory.auto_accept_proposals: true`` those drafts are promoted to
in-queue automatically, so the placeholder reached the executor unreviewed.

The verb is derived from the target repository itself -- a path already
tracked there is a ``modify``, a path that does not exist yet is an ``add`` --
so no backlog- or application-specific knowledge is required.
"""

from pathlib import Path

import pytest

from devbench.backlog.proposal import ProposedTask, generate_draft_md, manifest_change_verb


def _task(files: list[str]) -> ProposedTask:
    return ProposedTask(
        suggested_id="E9-F1-S1-T1",
        title="do the thing",
        suggested_approach="An approach with enough substance to pass the thinness check.",
        files_to_own=files,
        suggested_acs=["AC-1: something observable"],
        linked_scenarios=[],
    )


@pytest.mark.unit
class TestManifestChangeVerb:
    def test_existing_file_is_modify(self, tmp_path: Path, monkeypatch) -> None:
        (tmp_path / "scripts").mkdir()
        (tmp_path / "scripts" / "arc.py").write_text("x", encoding="utf-8")
        monkeypatch.setattr("devbench.config.REPO_LOCAL_PATHS", {"org/repo": tmp_path})
        assert manifest_change_verb("org/repo", "scripts/arc.py") == "modify"

    def test_absent_file_is_add(self, tmp_path: Path, monkeypatch) -> None:
        monkeypatch.setattr("devbench.config.REPO_LOCAL_PATHS", {"org/repo": tmp_path})
        assert manifest_change_verb("org/repo", "scripts/brand_new.py") == "add"

    def test_unresolvable_checkout_falls_back_to_modify(self, monkeypatch) -> None:
        """No checkout means no evidence; 'modify' is the conservative verb."""
        monkeypatch.setattr("devbench.config.REPO_LOCAL_PATHS", {})
        assert manifest_change_verb("org/unknown", "scripts/arc.py") == "modify"


@pytest.mark.unit
class TestGeneratedDraftManifest:
    def test_no_todo_placeholder_survives(self, tmp_path: Path, monkeypatch) -> None:
        (tmp_path / "scripts").mkdir()
        (tmp_path / "scripts" / "arc.py").write_text("x", encoding="utf-8")
        monkeypatch.setattr("devbench.config.REPO_LOCAL_PATHS", {"org/repo": tmp_path})

        md = generate_draft_md(
            _task(["scripts/arc.py", "scripts/new_thing.py"]),
            repo="org/repo",
            source_task_id="E9-F1-S1-T0",
            generated_at="2026-01-01T00:00:00Z",
        )

        assert "TODO -- describe change" not in md
        assert "| `scripts/arc.py` | modify |" in md
        assert "| `scripts/new_thing.py` | add |" in md

    def test_empty_file_list_uses_the_documented_sentinel(self, monkeypatch) -> None:
        """No known files means deferred resolution, not a file literally named TODO."""
        monkeypatch.setattr("devbench.config.REPO_LOCAL_PATHS", {})
        md = generate_draft_md(
            _task([]),
            repo="org/repo",
            source_task_id="E9-F1-S1-T0",
            generated_at="2026-01-01T00:00:00Z",
        )
        assert "TODO -- describe change" not in md
        assert "| `<source-drift-fix-targets-determined-at-execution>` | modify |" in md
