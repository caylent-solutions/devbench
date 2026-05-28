"""End-to-end tests for the validation-gate bug-escalation lifecycle (ADR-06).

Covers the second trigger for task-factory: an executor working on a validation-gate
task (empty Changes Manifest, Approach that forbids production-code changes) surfaces
out-of-scope production bugs, emits a proposal JSON directly via `write_proposal`
(bypassing the amendment pipeline), and the SKILL-equivalent call path materialises
drafts at the config-driven default status (in-queue by default; AC-189-8). Key invariants verified here:

- No amendment pending-request file is produced (validation-gate path does not go
  through `request-amendment` -> `manifest-amender` -> `reject-amendment`).
- No rejected-requests archive is produced (no amendment to reject).
- The proposal JSON lands on disk and is schema-valid.
- Materialisation creates one draft `.md` per proposed task with the config-driven
  default status (in-queue by default) and a matching row in `BACKLOG.md`.
- The source validation-gate task is NOT automatically blocked; its status is
  unchanged by the escalation (the source's own review pipeline remains in control).
- `list_proposals` surfaces the pending proposal to the operator.

The functions under test (`write_proposal`, `materialise_proposal`, `list_proposals`)
are flow-agnostic -- they behave identically regardless of whether the caller is the
blocker-resolver agent (amendment-reject trigger) or the executor agent (validation-
gate trigger). This test exercises the second entry point end-to-end.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from devbench.backlog import proposal as proposal_mod
from devbench.backlog.proposal import (
    Proposal,
    ProposedTask,
    list_proposals,
    materialise_proposal,
    write_proposal,
)

_VALIDATION_GATE_ROW = (
    "| E0-F9-S2-T4 | Validation Gate Task | Task | in-progress | None "
    "| caylent-solutions/example | `backlog/E0/E0-F9/E0-F9-S2/E0-F9-S2-T4.md` |"
)

_BACKLOG_TEMPLATE = (
    "# Backlog\n\n"
    "## Status Summary\n\n"
    "| Epic | Title | Done | In Progress | In Queue | Blocked | Declined |\n"
    "|------|-------|------|-------------|----------|---------|----------|\n"
    "| E0 | Example Epic | 0 | 1 | 0 | 0 | 0 |\n\n"
    "## Full Work Unit Index\n\n"
    "| ID | Title | Type | Status | Dependencies | Repo | File Path |\n"
    "|----|-------|------|--------|--------------|------|-----------|\n"
    f"{_VALIDATION_GATE_ROW}\n"
)

_VALIDATION_GATE_SOURCE = """\
# E0-F9-S2-T4: Validation Gate Task

## Status: in-progress

## Target Repository

- **Repo:** `caylent-solutions/example`

## Description

Run the existing verification suite and report pass / fail. Do NOT change production
code. This task is a validation gate per ADR-06; any out-of-scope production bugs
surfaced MUST be emitted via `write-proposal` rather than fixed in-scope.

## Dependencies

| ID | Title | Status |
|----|-------|--------|
| none | | |

## Acceptance Criteria

- [ ] AC-TEST-001 Manual integration scenarios pass

## Changes Manifest

| File | Change |
|------|--------|
| none | | |

## Definition of Done

- [ ] AC complete
"""


def _workspace(tmp_path: Path) -> Path:
    """Build a tmp workspace with one validation-gate source task in-progress."""
    (tmp_path / "BACKLOG.md").write_text(_BACKLOG_TEMPLATE)
    story_dir = tmp_path / "backlog" / "E0" / "E0-F9" / "E0-F9-S2"
    story_dir.mkdir(parents=True)
    (story_dir / "E0-F9-S2-T4.md").write_text(_VALIDATION_GATE_SOURCE)
    return tmp_path


def _validation_gate_proposal() -> Proposal:
    """A proposal emitted by the executor (not blocker-resolver) for a validation gate.

    Shape is identical to an amendment-reject proposal: same schema, same CLI, same
    task-factory consumption. The only difference is the upstream trigger.
    """
    return Proposal(
        source_task_id="E0-F9-S2-T4",
        generated_at="2026-04-18T18:30:00Z",
        rejection_reason=(
            "Validation gate surfaced 2 out-of-scope production bugs; "
            "source Approach forbids production-code changes in-scope."
        ),
        proposed_tasks=[
            ProposedTask(
                suggested_id="E0-F9-S2-T6",
                title="fix deprecation warning visibility in subprocess context",
                files_to_own=["src/example_cli/commands/install.py"],
                linked_scenarios=["SC-01"],
                suggested_acs=[
                    "AC-TEST-001 deprecation message appears on stderr via subprocess",
                    "AC-CODE-001 warnings.warn call preserved for pytest inspection",
                ],
                suggested_approach=(
                    "Context: DeprecationWarning emitted via warnings.warn is invisible in "
                    "subprocess context because Python's default filter suppresses it. "
                    "Scope: src/example_cli/commands/install.py. "
                    "TDD approach: 1. RED -- subprocess-level test asserts stderr contains the "
                    "deprecation text. 2. GREEN -- augment the warn call with print to stderr. "
                    "3. REFACTOR -- extract the message into a module-level constant. "
                    "Verify: make lint && make test-unit all exit zero."
                ),
            ),
            ProposedTask(
                suggested_id="E0-F9-S2-T7",
                title="add missing repo version subcommand",
                files_to_own=["src/example_cli/repo/subcmds/version.py"],
                linked_scenarios=["SC-02"],
                suggested_acs=[
                    "AC-TEST-001 example repo version exits 0 and prints version",
                    "AC-CODE-001 version sourced from package metadata, not hard-coded",
                ],
                suggested_approach=(
                    "Context: example repo version subcommand is missing entirely; invocation "
                    "exits 1 with 'version is not a repo command'. "
                    "Scope: src/example_cli/repo/subcmds/version.py plus dispatcher registration. "
                    "TDD approach: 1. RED -- integration test invokes subprocess and asserts exit 0. "
                    "2. GREEN -- create version.py and register in the dispatcher. "
                    "3. REFACTOR -- none expected. "
                    "Verify: make lint && make test-integration all exit zero."
                ),
            ),
        ],
    )


class TestValidationGateEscalationHappyPath:
    """ADR-06: executor emits proposal directly; materialisation creates drafts."""

    def test_executor_emission_lands_proposal_on_disk(self, tmp_path: Path) -> None:
        """Step 4a in SKILL.md branches on this file's existence; emission must persist it."""
        workspace = _workspace(tmp_path)
        proposal = _validation_gate_proposal()

        write_proposal(workspace, proposal)

        proposal_path = workspace / ".devbench" / "proposals" / "E0-F9-S2-T4.json"
        assert proposal_path.is_file(), (
            f"Proposal JSON must land at {proposal_path}. SKILL step 4a branches on this "
            f"exact file; a missing file silently suppresses task-factory."
        )

    def test_no_amendment_artifacts_produced_by_validation_gate_path(self, tmp_path: Path) -> None:
        """Validation-gate path bypasses request-amendment / reject-amendment entirely.

        No `.devbench/amendments/<id>.json` pending file, no `.devbench/rejected-requests/`
        archive is produced when the executor emits a proposal directly.
        """
        workspace = _workspace(tmp_path)
        write_proposal(workspace, _validation_gate_proposal())

        amendments_file = workspace / ".devbench" / "amendments" / "E0-F9-S2-T4.json"
        rejected_dir = workspace / ".devbench" / "rejected-requests"

        assert not amendments_file.exists(), (
            "Validation-gate path MUST NOT produce an amendment pending-request file. "
            "If this exists, the SKILL step 4a guard will defer to step 4b and the "
            "direct escalation flow regresses into the amendment flow."
        )
        assert not rejected_dir.exists() or not any(rejected_dir.iterdir()), (
            "Validation-gate path MUST NOT produce a rejected-requests archive. "
            "No amendment happened, so no rejection archive should exist."
        )

    def test_materialise_creates_drafts_with_config_driven_status(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """task-factory materialises drafts identically regardless of upstream trigger.

        Since AC-189-8, materialise_proposal writes the configured default status
        (in-queue by default) rather than the hard-coded 'proposed' sentinel. This
        test patches _get_runtime_config to use 'in-queue', verifying that the
        draft file's ## Status: line reflects the configuration.
        """
        from devbench.config_loader import BacklogConfig, RuntimeConfig

        fake_cfg = RuntimeConfig.__new__(RuntimeConfig)
        object.__setattr__(fake_cfg, "backlog", BacklogConfig(default_status_for_new_work_units="in-queue"))
        monkeypatch.setattr(proposal_mod, "_get_runtime_config", lambda: fake_cfg)

        workspace = _workspace(tmp_path)
        proposal = _validation_gate_proposal()
        write_proposal(workspace, proposal)

        drafts = materialise_proposal(
            workspace_root=workspace,
            backlog_root=workspace / "backlog",
            backlog_index=workspace / "BACKLOG.md",
            proposal=proposal,
            repo="caylent-solutions/example",
        )

        assert len(drafts) == 2, "Each proposed task must produce one draft .md file."
        for draft_path in drafts:
            assert draft_path.is_file(), f"Draft must land on disk: {draft_path}"
            draft_content = draft_path.read_text()
            assert "## Status: in-queue" in draft_content, (
                f"Draft {draft_path} must carry the configured default status 'in-queue'."
            )

    def test_materialise_adds_backlog_rows(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """Every proposed task must get a BACKLOG.md row with the configured default status."""
        from devbench.config_loader import BacklogConfig, RuntimeConfig

        fake_cfg = RuntimeConfig.__new__(RuntimeConfig)
        object.__setattr__(fake_cfg, "backlog", BacklogConfig(default_status_for_new_work_units="in-queue"))
        monkeypatch.setattr(proposal_mod, "_get_runtime_config", lambda: fake_cfg)

        workspace = _workspace(tmp_path)
        proposal = _validation_gate_proposal()
        write_proposal(workspace, proposal)
        materialise_proposal(
            workspace_root=workspace,
            backlog_root=workspace / "backlog",
            backlog_index=workspace / "BACKLOG.md",
            proposal=proposal,
            repo="caylent-solutions/example",
        )

        backlog_text = (workspace / "BACKLOG.md").read_text()
        assert "E0-F9-S2-T6" in backlog_text
        assert "E0-F9-S2-T7" in backlog_text
        assert "in-queue" in backlog_text

    def test_list_proposals_surfaces_pending_to_operator(self, tmp_path: Path) -> None:
        """`devbench list-proposals` (the CLI) calls this under the hood."""
        workspace = _workspace(tmp_path)
        proposal = _validation_gate_proposal()
        write_proposal(workspace, proposal)
        materialise_proposal(
            workspace_root=workspace,
            backlog_root=workspace / "backlog",
            backlog_index=workspace / "BACKLOG.md",
            proposal=proposal,
            repo="caylent-solutions/example",
        )

        pending = list_proposals(workspace)
        assert pending, (
            "Pending proposals must be visible to the operator via list_proposals; "
            "the operator's review cadence depends on this."
        )

    def test_source_task_not_auto_blocked_by_escalation(self, tmp_path: Path) -> None:
        """ADR-06 invariant: validation-gate escalation does NOT auto-block the source.

        Unlike the amendment-reject path (which always leaves the source blocked),
        the validation-gate path leaves the source task's status unchanged. Its own
        review pipeline at SKILL step 5 determines whether it passes or blocks.
        """
        workspace = _workspace(tmp_path)
        source_md = workspace / "backlog" / "E0" / "E0-F9" / "E0-F9-S2" / "E0-F9-S2-T4.md"
        status_before = [line for line in source_md.read_text().splitlines() if line.startswith("## Status:")]

        proposal = _validation_gate_proposal()
        write_proposal(workspace, proposal)
        materialise_proposal(
            workspace_root=workspace,
            backlog_root=workspace / "backlog",
            backlog_index=workspace / "BACKLOG.md",
            proposal=proposal,
            repo="caylent-solutions/example",
        )

        status_after = [line for line in source_md.read_text().splitlines() if line.startswith("## Status:")]
        assert status_before == status_after, (
            "Validation-gate bug-escalation must NOT flip the source task to `blocked`. "
            "The source's own review pipeline controls its status; escalation only "
            "materialises independent follow-up drafts. Regression path: "
            "materialise_proposal silently mutating the source's Status header."
        )


class TestValidationGateEscalationIndependentOfAmendmentPath:
    """The two task-factory triggers produce identical downstream artifacts."""

    def test_same_proposal_schema_from_both_triggers(self, tmp_path: Path) -> None:
        """The proposal JSON schema does not depend on the upstream trigger.

        blocker-resolver (amendment-reject trigger) and the executor (validation-gate
        trigger) both emit the same JSON envelope through the same `write-proposal`
        CLI. The downstream task-factory code path is trigger-agnostic.
        """
        workspace = _workspace(tmp_path)
        proposal = _validation_gate_proposal()
        write_proposal(workspace, proposal)

        proposal_path = workspace / ".devbench" / "proposals" / "E0-F9-S2-T4.json"
        assert proposal_path.is_file()
        proposal_json = proposal_path.read_text()

        # Schema fields required for task-factory consumption:
        assert "source_task_id" in proposal_json
        assert "generated_at" in proposal_json
        assert "rejection_reason" in proposal_json
        assert "proposed_tasks" in proposal_json
        assert "suggested_id" in proposal_json
        assert "files_to_own" in proposal_json
        assert "linked_scenarios" in proposal_json
        assert "suggested_acs" in proposal_json
        assert "suggested_approach" in proposal_json
