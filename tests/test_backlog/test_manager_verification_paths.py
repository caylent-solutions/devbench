"""Tests for the verify-ac path-contract and AC referential-integrity lints.

Covers:
- TDI-001: a ``type=command`` path operand prefixed with the unit's own
  checkout-directory name (workspace-prefix smell) -> warning / strict-error.
- TDI-001: a ``type=command`` whose grep takes operands from ``$(find ...)``.
- TDI-001 AC-3 / TDI-005: a ``type=command`` literal path that does not resolve
  against the present checkout, is not created by any task, and is not external.
- TDI-004: a ``type=deferred`` directive whose reason names a runnable tool.
- TDI-005: an Acceptance Criterion asserting a path must exist that is neither
  present, created-by-a-task, nor marked external.
- Clean cases: repo-root-relative paths, present paths, created-by-task paths,
  external carve-outs, and genuinely operator-only deferred directives.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest

from devbench.backlog.manager import BacklogManager
from devbench.config_loader import RepoConfig, RuntimeConfig

pytestmark = pytest.mark.unit

_REPO = "caylent-solutions/devbench"
_CHECKOUT = "repo"


def _make_index(tmp_path: Path, unit_ids: list[str]) -> Path:
    rows = "".join(
        f"| {uid} | Task Title | Task | in-queue | none | {_REPO} | `backlog/{uid}.md` |\n" for uid in unit_ids
    )
    (tmp_path / "BACKLOG.md").write_text(
        "# Backlog\n\n"
        "## Status Summary\n\n"
        "| Epic | Title | Done | In Progress | In Queue | Blocked |\n"
        "|------|-------|------|-------------|----------|---------|\n\n"
        "## Full Work Unit Index\n\n"
        "| ID | Title | Type | Status | Dependencies | Repo | File Path |\n"
        "|-----|-------|------|--------|-------------|------|-----------|\n"
        f"{rows}",
        encoding="utf-8",
    )
    return tmp_path / "BACKLOG.md"


def _make_task(
    backlog_dir: Path,
    unit_id: str,
    *,
    ac_block: str = "- [ ] AC-1: the module is documented",
    manifest_rows: str = "| `src/f.py` | modify |",
    verification_block: str = "",
) -> None:
    verification = f"## Verification\n\n{verification_block}\n\n" if verification_block else ""
    (backlog_dir / f"{unit_id}.md").write_text(
        f"# {unit_id}: Task Title\n\n"
        f"## Status: in-queue\n\n"
        f"## Target Repository\n\n- **Repo:** `{_REPO}`\n\n"
        f"## Description\n\nTest task.\n\n"
        f"## Dependencies\n\n| ID | Title | Status |\n|----|-------|--------|\n| none | | |\n\n"
        f"## Acceptance Criteria\n\n{ac_block}\n\n"
        f"## Changes Manifest\n\n| File | Change |\n|------|--------|\n{manifest_rows}\n\n"
        f"## Definition of Done\n\n- [ ] All ACs checked\n\n"
        f"{verification}"
        f"## TDD Cycle Log\n\n## Comments\n",
        encoding="utf-8",
    )


def _validate(
    tmp_path: Path,
    *,
    strict: bool = False,
    checkout_present: bool = False,
) -> tuple[list[str], list[str]]:
    idx = tmp_path / "BACKLOG.md"
    resolved = None
    if checkout_present:
        resolved = tmp_path / _CHECKOUT
        resolved.mkdir(exist_ok=True)
    repo_cfg = RepoConfig(checkout_directory=_CHECKOUT, resolved_checkout_path=resolved)
    cfg = RuntimeConfig(repos={_REPO: repo_cfg})
    with patch("devbench.config.RUNTIME_CONFIG", cfg):
        return BacklogManager().validate_with_warnings(idx, tmp_path, strict=strict)


def _path_findings(items: list[str]) -> list[str]:
    return [i for i in items if "Verification Contract" in i or "AC Referential Integrity" in i]


# ---------------------------------------------------------------------------
# TDI-001: workspace-prefix smell
# ---------------------------------------------------------------------------


def test_command_path_with_checkout_prefix_warns_then_errors(tmp_path: Path, backlog_dir: Path) -> None:
    _make_index(tmp_path, ["E1-F1-S1-T1"])
    _make_task(
        backlog_dir,
        "E1-F1-S1-T1",
        verification_block="- VERIFY AC-1 | type=command | cmd=`test -d repo/providers/aws/x` | expect-exit=0",
    )
    _, warnings = _validate(tmp_path)
    assert any("begins with the target-repo checkout-directory name" in w for w in warnings)

    errors_s, _ = _validate(tmp_path, strict=True)
    assert any("begins with the target-repo checkout-directory name" in e for e in errors_s)


def test_command_repo_relative_path_is_clean(tmp_path: Path, backlog_dir: Path) -> None:
    _make_index(tmp_path, ["E1-F1-S1-T1"])
    _make_task(
        backlog_dir,
        "E1-F1-S1-T1",
        verification_block="- VERIFY AC-1 | type=command | cmd=`test -d providers/aws/x` | expect-exit=0",
    )
    _, warnings = _validate(tmp_path)
    assert not any("checkout-directory name" in w for w in warnings)


# ---------------------------------------------------------------------------
# TDI-001: unbounded $(find ...) -> grep smell
# ---------------------------------------------------------------------------


def test_find_feeds_grep_is_flagged(tmp_path: Path, backlog_dir: Path) -> None:
    _make_index(tmp_path, ["E1-F1-S1-T1"])
    _make_task(
        backlog_dir,
        "E1-F1-S1-T1",
        verification_block=(
            "- VERIFY AC-1 | type=command | cmd=`! grep -rnE PATTERN $(find providers -name '*.tf')` | expect-exit=0"
        ),
    )
    _, warnings = _validate(tmp_path)
    assert any("feeds a grep from $(find ...)" in w for w in warnings)


# ---------------------------------------------------------------------------
# TDI-004: mis-classified deferred
# ---------------------------------------------------------------------------


def test_deferred_naming_runnable_tool_is_flagged(tmp_path: Path, backlog_dir: Path) -> None:
    _make_index(tmp_path, ["E1-F1-S1-T1"])
    _make_task(
        backlog_dir,
        "E1-F1-S1-T1",
        ac_block="- [ ] AC-1: terraform validate passes",
        verification_block=(
            "- VERIFY AC-1 | type=deferred | owner=operator | "
            'reason="requires the Terraform toolchain the orchestrator runs at execution time"'
        ),
    )
    _, warnings = _validate(tmp_path)
    assert any("type=deferred but its reason names a runnable tool" in w for w in warnings)


def test_deferred_operator_only_is_clean(tmp_path: Path, backlog_dir: Path) -> None:
    _make_index(tmp_path, ["E1-F1-S1-T1"])
    _make_task(
        backlog_dir,
        "E1-F1-S1-T1",
        ac_block="- [ ] AC-1: the production apply completes",
        verification_block=(
            "- VERIFY AC-1 | type=deferred | owner=operator | "
            'reason="real production terragrunt apply against a live account"'
        ),
    )
    _, warnings = _validate(tmp_path)
    assert not any("names a runnable tool" in w for w in warnings)


# ---------------------------------------------------------------------------
# TDI-001 AC-3 / TDI-005: referential integrity (checkout present)
# ---------------------------------------------------------------------------


def test_command_path_absent_from_checkout_is_flagged(tmp_path: Path, backlog_dir: Path) -> None:
    _make_index(tmp_path, ["E1-F1-S1-T1"])
    _make_task(
        backlog_dir,
        "E1-F1-S1-T1",
        verification_block="- VERIFY AC-1 | type=command | cmd=`test -d providers/aws/missing` | expect-exit=0",
    )
    _, warnings = _validate(tmp_path, checkout_present=True)
    assert any("AC Referential Integrity" in w and "providers/aws/missing" in w for w in warnings)


def test_command_path_present_in_checkout_is_clean(tmp_path: Path, backlog_dir: Path) -> None:
    _make_index(tmp_path, ["E1-F1-S1-T1"])
    _make_task(
        backlog_dir,
        "E1-F1-S1-T1",
        verification_block="- VERIFY AC-1 | type=command | cmd=`test -d providers/aws/present` | expect-exit=0",
    )
    # Create the path inside the checkout so it resolves.
    (tmp_path / _CHECKOUT / "providers" / "aws" / "present").mkdir(parents=True, exist_ok=True)
    _, warnings = _validate(tmp_path, checkout_present=True)
    assert not _path_findings(warnings)


def test_required_path_created_by_sibling_task_is_clean(tmp_path: Path, backlog_dir: Path) -> None:
    _make_index(tmp_path, ["E1-F1-S1-T1", "E1-F1-S1-T2"])
    # T1 asserts providers/aws/new/main.tf must exist; T2 adds it.
    _make_task(
        backlog_dir,
        "E1-F1-S1-T1",
        verification_block="- VERIFY AC-1 | type=command | cmd=`test -f providers/aws/new/main.tf` | expect-exit=0",
    )
    _make_task(
        backlog_dir,
        "E1-F1-S1-T2",
        manifest_rows="| `providers/aws/new/main.tf` | add |",
    )
    _, warnings = _validate(tmp_path, checkout_present=True)
    assert not any("providers/aws/new/main.tf" in w and "AC Referential Integrity" in w for w in warnings)


def test_ac_existence_assertion_unbacked_is_flagged(tmp_path: Path, backlog_dir: Path) -> None:
    _make_index(tmp_path, ["E1-F1-S1-T1"])
    _make_task(
        backlog_dir,
        "E1-F1-S1-T1",
        ac_block="- [ ] AC-1: the default `providers/aws/primitives/waf-webacl` must point to an existing directory",
    )
    _, warnings = _validate(tmp_path, checkout_present=True)
    assert any("AC Referential Integrity" in w and "waf-webacl" in w for w in warnings)


def test_ac_existence_assertion_external_carveout_is_clean(tmp_path: Path, backlog_dir: Path) -> None:
    _make_index(tmp_path, ["E1-F1-S1-T1"])
    _make_task(
        backlog_dir,
        "E1-F1-S1-T1",
        ac_block="- [ ] AC-1: the pinned external `vendor/third-party/mod` source resolves to a published module",
    )
    _, warnings = _validate(tmp_path, checkout_present=True)
    assert not _path_findings(warnings)


def test_referential_skips_non_command_and_prefix_operands(tmp_path: Path, backlog_dir: Path) -> None:
    # A judge item (non-command) is skipped by the referential loop, and a
    # command operand that begins with the checkout prefix is left to the
    # path-contract check (not double-reported by referential integrity).
    _make_index(tmp_path, ["E1-F1-S1-T1"])
    _make_task(
        backlog_dir,
        "E1-F1-S1-T1",
        verification_block=(
            "- VERIFY AC-1 | type=judge\n"
            "- VERIFY AC-2 | type=command | cmd=`test -d repo/providers/aws/x` | expect-exit=0"
        ),
    )
    _, warnings = _validate(tmp_path, checkout_present=True)
    # The prefixed operand is reported once (path-contract), not also as a
    # referential-integrity finding.
    assert any("checkout-directory name" in w for w in warnings)
    assert not any("AC Referential Integrity" in w for w in warnings)


def test_referential_handles_malformed_verification(tmp_path: Path, backlog_dir: Path) -> None:
    # A malformed directive yields no parsed items in the referential check
    # (the contract check reports the malformed error separately).
    _make_index(tmp_path, ["E1-F1-S1-T1"])
    _make_task(
        backlog_dir,
        "E1-F1-S1-T1",
        ac_block="- [ ] AC-1: the module is documented",
        verification_block="- VERIFY AC-1 | type=bogus",
    )
    errors, _ = _validate(tmp_path, checkout_present=True)
    assert any("malformed '## Verification' directive" in e for e in errors)


def test_ac_existence_assertion_non_path_token_is_ignored(tmp_path: Path, backlog_dir: Path) -> None:
    # An existence-asserting AC whose backtick token is not a path (no '/')
    # produces no referential finding.
    _make_index(tmp_path, ["E1-F1-S1-T1"])
    _make_task(
        backlog_dir,
        "E1-F1-S1-T1",
        ac_block="- [ ] AC-1: the `sandbox` environment already exists in the config",
    )
    _, warnings = _validate(tmp_path, checkout_present=True)
    assert not _path_findings(warnings)


def test_referential_integrity_skipped_without_checkout(tmp_path: Path, backlog_dir: Path) -> None:
    _make_index(tmp_path, ["E1-F1-S1-T1"])
    _make_task(
        backlog_dir,
        "E1-F1-S1-T1",
        verification_block="- VERIFY AC-1 | type=command | cmd=`test -d providers/aws/missing` | expect-exit=0",
    )
    # No checkout on disk -> absence cannot be asserted -> no referential finding.
    _, warnings = _validate(tmp_path, checkout_present=False)
    assert not any("AC Referential Integrity" in w for w in warnings)
