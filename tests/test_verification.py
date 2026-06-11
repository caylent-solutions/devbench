"""Tests for the Acceptance-Criteria verification contract (verification module).

Covers:
- IaC tool detection across the full supported matrix (+ negatives)
- execution-verb detection used by the validator lint
- ``## Verification`` section detection + directive parsing (well-formed + malformed)
- VerificationItem.is_executable / is_infra classification
- unit_requires_iac_judge (deterministic iac_review applicability)
- evidence model (round-trip) and evidence_completeness gating
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from devbench.verification import (
    EXECUTABLE_TYPES,
    INFRA_TYPES,
    EvidenceRecord,
    VerificationItem,
    VerificationType,
    command_substitution_feeds_grep,
    deferred_items,
    deferred_reason_names_runnable_tool,
    detect_iac_tool,
    deterministic_gate_env,
    evidence_attempt_dir,
    evidence_completeness,
    evidence_root,
    executable_items,
    extract_command_paths,
    has_verification_section,
    latest_attempt_number,
    next_attempt_number,
    parse_verification_item,
    parse_verification_section,
    pytest_randomly_available,
    read_latest_evidence_ledger,
    sanitize_ac_label,
    text_has_execution_verb,
    trim_log,
    unit_requires_iac_judge,
    write_evidence_ledger,
)

# ---------------------------------------------------------------------------
# IaC tool detection
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("command", "expected"),
    [
        # Tool invoked with a lifecycle verb/subcommand -> detected (TDI-007).
        ("terraform apply -auto-approve", "terraform"),
        ("terraform validate", "terraform"),
        ("terraform init -backend=false", "terraform"),
        ("tofu plan", "opentofu"),
        ("terragrunt run-all apply", "terragrunt"),
        ("terragrunt apply", "terragrunt"),
        ("make tf-test UNIT=sandbox/000/data-lake/000", "terratest"),
        ("go test ./tests/...", "terratest"),
        ("cdktf deploy", "cdktf"),
        ("cdk deploy MyStack", "aws-cdk"),
        ("aws cloudformation deploy --template-file t.yaml", "cloudformation"),
        ("sam deploy --guided", "aws-sam"),
    ],
)
def test_detect_iac_tool_matrix(command: str, expected: str) -> None:
    assert detect_iac_tool(command) == expected


@pytest.mark.parametrize(
    "command",
    [
        "",
        None,
        "pytest -q",
        "make lint",
        "echo hello",
        # TDI-007: a tool name appearing only as a PATH OPERAND (no lifecycle
        # verb) must NOT be detected as an IaC invocation.
        "test -d terragrunt/common/sandbox/000",
        "jq -e . terragrunt/common/accounts.json",
        "grep -q region providers/aws/references/vpc-network/main.tf",
        "cat terraform.tfvars",
        "test -f providers/aws/primitives/spice-kms/variables.tf",
        # aws CLI without a CloudFormation provisioning verb is not IaC.
        "aws s3 ls",
        "aws ec2 describe-instances",
    ],
)
def test_detect_iac_tool_negative(command: str | None) -> None:
    assert detect_iac_tool(command) is None


def test_terragrunt_precedence_over_other_tools() -> None:
    # A terragrunt command that also mentions aws must classify as terragrunt.
    assert detect_iac_tool("terragrunt apply") == "terragrunt"


# ---------------------------------------------------------------------------
# Execution-verb detection (validator lint)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "text",
    [
        "A real terragrunt apply succeeds and the bucket exists",
        "make tf-test passes at >=90% coverage",
        "cdk deploy provisions the stack",
        "pytest exits zero",
        "The smoke test returns HTTP 200",
        "terraform validate passes",
    ],
)
def test_text_has_execution_verb_positive(text: str) -> None:
    assert text_has_execution_verb(text) is True


@pytest.mark.parametrize(
    "text",
    [
        "The module follows SOLID principles",
        "Variables are documented in the README",
        "",
        "Naming is idiomatic and consistent",
    ],
)
def test_text_has_execution_verb_negative(text: str) -> None:
    assert text_has_execution_verb(text) is False


# ---------------------------------------------------------------------------
# Command-path extraction + classification (TDI-001 / 004 / 005)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("command", "expected"),
    [
        (None, []),
        ("", []),
        # Slash paths are operands; the command word and flags are not.
        ("test -d terragrunt/common/sandbox/000", ["terragrunt/common/sandbox/000"]),
        ("grep -q x providers/aws/references/vpc/main.tf", ["providers/aws/references/vpc/main.tf"]),
        # Bare filename with a known extension counts; the command word does not.
        ("cat terraform.tfvars", ["terraform.tfvars"]),
        # Command substitutions are stripped, so their contents are not operands.
        ("grep -rnE PATTERN $(find providers -name '*.tf')", []),
        ("echo `cat providers/aws/x.tf`", []),
        # Excluded shapes: flags, key=value, var refs, globs, bare words.
        ("terraform validate -backend=false", []),
        ("jq -e . ${CONFIG}", []),
        ("ls providers/*.tf", []),
        ("make lint", []),
        # De-duplication, order preserved.
        ("diff a/x.tf a/x.tf", ["a/x.tf"]),
        # Unknown extension on a slash-less token is NOT a path operand.
        ("run something.zzz", []),
    ],
)
def test_extract_command_paths(command: str | None, expected: list[str]) -> None:
    assert extract_command_paths(command) == expected


@pytest.mark.parametrize(
    ("command", "expected"),
    [
        (None, False),
        ("", False),
        ("! grep -rnE PATTERN $(find providers/aws -name '*.tf')", True),
        ("grep -q x $(find . -name '*.py')", True),
        # A plain grep against explicit files does not feed from find.
        ("grep -q x providers/aws/main.tf", False),
        ("terraform validate", False),
    ],
)
def test_command_substitution_feeds_grep(command: str | None, expected: bool) -> None:
    assert command_substitution_feeds_grep(command) is expected


@pytest.mark.parametrize(
    ("reason", "expected_match"),
    [
        (None, None),
        ("", None),
        # Mis-classified: names a runnable tool with no live/operator signal.
        ("requires the Terraform toolchain the orchestrator runs at execution time", "Terraform"),
        ("runs terraform init -backend=false && terraform validate", "terraform"),
        ("pytest must run for this check", "pytest"),
        # Genuinely operator-only: vetoed even though it names a tool.
        ("real production terragrunt apply against a live account", None),
        ("prod apply is operator-only", None),
        ("requires AWS credentials the orchestrator must not hold", None),
        # No tool named at all.
        ("a human must visually inspect the dashboard", None),
    ],
)
def test_deferred_reason_names_runnable_tool(reason: str | None, expected_match: str | None) -> None:
    result = deferred_reason_names_runnable_tool(reason)
    if expected_match is None:
        assert result is None
    else:
        assert result is not None and result.lower() == expected_match.lower()


# ---------------------------------------------------------------------------
# Section detection + directive parsing
# ---------------------------------------------------------------------------

_WU_WITH_VERIFICATION = """# E1-F1-S1-T1: Example

## Verification
- VERIFY AC-3 | type=terratest | tool=terragrunt | cmd=`make tf-test UNIT=sandbox/000/x` | expect-exit=0
- VERIFY AC-7 | type=smoke | cmd=`bash -c "curl -sf $URL | grep ok"` | expect-exit=0
- VERIFY AC-9 | type=deferred | owner=operator | reason="prod apply is operator-only"
- VERIFY AC-2 | type=judge

## Comments
"""


def test_has_verification_section() -> None:
    assert has_verification_section(_WU_WITH_VERIFICATION) is True
    assert has_verification_section("# T1\n\n## Status: done\n") is False


def test_parse_verification_section_absent_returns_empty() -> None:
    assert parse_verification_section("# T1\n\n## Status: done\n") == []


def test_parse_verification_section_items() -> None:
    items = parse_verification_section(_WU_WITH_VERIFICATION)
    assert [i.vtype for i in items] == [
        VerificationType.TERRATEST,
        VerificationType.SMOKE,
        VerificationType.DEFERRED,
        VerificationType.JUDGE,
    ]
    terratest = items[0]
    assert terratest.ac_ids == ("AC-3",)
    assert terratest.tool == "terragrunt"
    assert terratest.command == "make tf-test UNIT=sandbox/000/x"
    assert terratest.expect_exit == 0
    # The smoke command contains a literal pipe inside backticks -- must survive splitting.
    assert items[1].command == 'bash -c "curl -sf $URL | grep ok"'
    # Deferred carries owner + reason (quotes stripped).
    assert items[2].owner == "operator"
    assert items[2].reason == "prod apply is operator-only"


def test_parse_item_multiple_ac_ids_and_inferred_tool() -> None:
    item = parse_verification_item(" AC-3, AC-5 | type=apply | cmd=`terraform apply`")
    assert item.ac_ids == ("AC-3", "AC-5")
    assert item.tool == "terraform"  # inferred from cmd since no explicit tool=
    assert item.expect_exit == 0


def test_parse_item_custom_expect_exit() -> None:
    item = parse_verification_item(" AC-1 | type=command | cmd=`./check.sh` | expect-exit=2")
    assert item.expect_exit == 2


def test_parse_item_missing_ac_raises() -> None:
    with pytest.raises(ValueError, match="names no AC id"):
        parse_verification_item(" | type=terratest | cmd=`x`")


def test_parse_item_missing_type_raises() -> None:
    with pytest.raises(ValueError, match="missing 'type='"):
        parse_verification_item(" AC-1 | cmd=`x`")


def test_parse_item_unknown_type_raises() -> None:
    with pytest.raises(ValueError, match="unknown type"):
        parse_verification_item(" AC-1 | type=bogus")


def test_parse_item_bad_expect_exit_raises() -> None:
    with pytest.raises(ValueError, match="non-integer expect-exit"):
        parse_verification_item(" AC-1 | type=command | cmd=`x` | expect-exit=abc")


# ---------------------------------------------------------------------------
# Classification
# ---------------------------------------------------------------------------


def test_executable_and_infra_type_sets() -> None:
    assert VerificationType.TERRATEST in EXECUTABLE_TYPES
    assert VerificationType.JUDGE not in EXECUTABLE_TYPES
    assert VerificationType.DEFERRED not in EXECUTABLE_TYPES
    assert VerificationType.SMOKE in INFRA_TYPES
    assert VerificationType.COMMAND not in INFRA_TYPES


def test_item_is_executable_and_is_infra() -> None:
    apply = VerificationItem(ac_ids=("AC-1",), vtype=VerificationType.APPLY, command="terraform apply")
    assert apply.is_executable() is True
    assert apply.is_infra() is True

    cmd_infra = VerificationItem(ac_ids=("AC-2",), vtype=VerificationType.COMMAND, command="cdk deploy S")
    assert cmd_infra.is_infra() is True  # via cmd pattern

    cmd_plain = VerificationItem(ac_ids=("AC-3",), vtype=VerificationType.COMMAND, command="pytest -q")
    assert cmd_plain.is_executable() is True
    assert cmd_plain.is_infra() is False

    judge = VerificationItem(ac_ids=("AC-4",), vtype=VerificationType.JUDGE)
    assert judge.is_executable() is False
    assert judge.is_infra() is False


def test_executable_and_deferred_filters() -> None:
    items = parse_verification_section(_WU_WITH_VERIFICATION)
    assert {i.vtype for i in executable_items(items)} == {VerificationType.TERRATEST, VerificationType.SMOKE}
    assert [i.vtype for i in deferred_items(items)] == [VerificationType.DEFERRED]


# ---------------------------------------------------------------------------
# iac_review applicability
# ---------------------------------------------------------------------------


def test_unit_requires_iac_judge_true_for_infra() -> None:
    assert unit_requires_iac_judge(_WU_WITH_VERIFICATION) is True


def test_unit_requires_iac_judge_false_when_no_infra() -> None:
    content = "# T1\n\n## Verification\n- VERIFY AC-1 | type=command | cmd=`pytest -q`\n- VERIFY AC-2 | type=judge\n"
    assert unit_requires_iac_judge(content) is False


def test_unit_requires_iac_judge_false_on_malformed() -> None:
    # A malformed directive must not raise here; the validator reports it separately.
    content = "# T1\n\n## Verification\n- VERIFY AC-1 | type=bogus\n"
    assert unit_requires_iac_judge(content) is False


def test_unit_requires_iac_judge_false_for_path_substring_only() -> None:
    # TDI-007 AC-1: type=command directives that only run jq/test/grep against
    # IaC-named paths (no lifecycle verb) must NOT require the iac_review judge.
    content = (
        "# T1\n\n## Verification\n"
        "- VERIFY AC-1 | type=command | cmd=`test -d terragrunt/common/sandbox/000`\n"
        "- VERIFY AC-2 | type=command | cmd=`jq -e . terragrunt/common/accounts.json`\n"
        "- VERIFY AC-3 | type=command | cmd=`grep -q region providers/aws/references/vpc-network/main.tf`\n"
    )
    assert unit_requires_iac_judge(content) is False


@pytest.mark.parametrize(
    "cmd",
    [
        "terraform validate",
        "terragrunt run-all plan",
        "go test ./tests/...",
    ],
)
def test_unit_requires_iac_judge_true_for_lifecycle_verb(cmd: str) -> None:
    # TDI-007 AC-2: a directive that actually invokes an IaC tool with a
    # lifecycle verb requires the iac_review judge.
    content = f"# T1\n\n## Verification\n- VERIFY AC-1 | type=command | cmd=`{cmd}`\n"
    assert unit_requires_iac_judge(content) is True


# ---------------------------------------------------------------------------
# Evidence model + completeness gate
# ---------------------------------------------------------------------------


def test_evidence_record_round_trip() -> None:
    rec = EvidenceRecord(
        ac_ids=["AC-3"],
        vtype="terratest",
        command="make tf-test",
        exit_code=0,
        tool="terragrunt",
        started_at="2026-06-08T00:00:00Z",
        finished_at="2026-06-08T00:01:00Z",
        artifact=".devbench/evidence/T1/1/AC-3.log",
        summary="ok",
    )
    restored = EvidenceRecord.from_dict(rec.to_dict())
    assert restored == rec


def test_evidence_record_from_dict_defaults() -> None:
    rec = EvidenceRecord.from_dict({"vtype": "smoke"})
    assert rec.ac_ids == []
    assert rec.exit_code == 1  # fail-closed default
    assert rec.command is None


def _items() -> list[VerificationItem]:
    return [
        VerificationItem(ac_ids=("AC-3",), vtype=VerificationType.TERRATEST, command="make tf-test", expect_exit=0),
        VerificationItem(ac_ids=("AC-7",), vtype=VerificationType.SMOKE, command="make smoke", expect_exit=0),
        VerificationItem(ac_ids=("AC-2",), vtype=VerificationType.JUDGE),
    ]


def test_evidence_complete_when_all_exit_zero() -> None:
    records = [
        EvidenceRecord(ac_ids=["AC-3"], vtype="terratest", command="make tf-test", exit_code=0),
        EvidenceRecord(ac_ids=["AC-7"], vtype="smoke", command="make smoke", exit_code=0),
    ]
    result = evidence_completeness(_items(), records)
    assert result.complete is True
    assert result.message() == ""


def test_evidence_missing_record() -> None:
    records = [EvidenceRecord(ac_ids=["AC-3"], vtype="terratest", command="make tf-test", exit_code=0)]
    result = evidence_completeness(_items(), records)
    assert result.complete is False
    assert result.missing == ["AC-7"]
    assert "no exit-0 evidence for: AC-7" in result.message()


def test_evidence_non_zero_exit_fails() -> None:
    records = [
        EvidenceRecord(ac_ids=["AC-3"], vtype="terratest", command="make tf-test", exit_code=1),
        EvidenceRecord(ac_ids=["AC-7"], vtype="smoke", command="make smoke", exit_code=0),
    ]
    result = evidence_completeness(_items(), records)
    assert result.complete is False
    assert result.failed == ["AC-3"]
    assert "non-zero exit recorded for: AC-3" in result.message()


def test_evidence_multiple_records_one_passing_satisfies() -> None:
    records = [
        EvidenceRecord(ac_ids=["AC-3"], vtype="terratest", command="make tf-test", exit_code=1),
        EvidenceRecord(ac_ids=["AC-3"], vtype="terratest", command="make tf-test", exit_code=0),
        EvidenceRecord(ac_ids=["AC-7"], vtype="smoke", command="make smoke", exit_code=0),
    ]
    assert evidence_completeness(_items(), records).complete is True


def test_deferred_blocks_by_default_and_allow_overrides() -> None:
    items = [VerificationItem(ac_ids=("AC-9",), vtype=VerificationType.DEFERRED, owner="operator")]
    blocked = evidence_completeness(items, [])
    assert blocked.complete is False
    assert blocked.deferred == ["AC-9"]
    assert "deferred (operator-only) ACs block done: AC-9" in blocked.message()

    allowed = evidence_completeness(items, [], allow_deferred=True)
    assert allowed.complete is True


# ---------------------------------------------------------------------------
# Evidence ledger persistence (the on-disk layout shared by runner + gate)
# ---------------------------------------------------------------------------

_TASK = "E1-F1-S1-T1"


def _record(ac: str, exit_code: int) -> EvidenceRecord:
    return EvidenceRecord(ac_ids=[ac], vtype="apply", command="make tf-apply", exit_code=exit_code, tool="terraform")


def test_sanitize_ac_label_joins_and_replaces() -> None:
    assert sanitize_ac_label(["AC-3", "AC-7"]) == "AC-3-AC-7"
    assert sanitize_ac_label(["AC-FINAL/001"]) == "AC-FINAL_001"


def test_sanitize_ac_label_empty_falls_back() -> None:
    assert sanitize_ac_label([]) == "unknown"
    # A non-empty id made entirely of disallowed chars collapses to a single
    # underscore -- still a stable, non-empty, directory-safe label.
    assert sanitize_ac_label(["//"]) == "_"


@pytest.mark.parametrize(
    ("text", "cap", "expected"),
    [
        ("short", 100, "short"),  # under cap -> unchanged
        ("abcdef", 3, "def"),  # tail-biased trim
        ("abcdef", 0, "abcdef"),  # non-positive cap disables trimming
        ("abcdef", -1, "abcdef"),
    ],
)
def test_trim_log(text: str, cap: int, expected: str) -> None:
    assert trim_log(text, cap) == expected


def test_next_attempt_number_starts_at_one(tmp_path: Path) -> None:
    assert next_attempt_number(tmp_path, _TASK) == 1


def test_attempt_numbering_and_latest_pointer(tmp_path: Path) -> None:
    a1 = next_attempt_number(tmp_path, _TASK)
    assert a1 == 1
    write_evidence_ledger(tmp_path, _TASK, a1, [_record("AC-1", 1)])
    assert latest_attempt_number(tmp_path, _TASK) == 1

    a2 = next_attempt_number(tmp_path, _TASK)
    assert a2 == 2
    write_evidence_ledger(tmp_path, _TASK, a2, [_record("AC-1", 0)])
    assert latest_attempt_number(tmp_path, _TASK) == 2


def test_next_attempt_ignores_non_numeric_children(tmp_path: Path) -> None:
    root = evidence_root(tmp_path, _TASK)
    root.mkdir(parents=True)
    (root / "1").mkdir()
    (root / "notanattempt").mkdir()
    (root / "latest.json").write_text("{}", encoding="utf-8")  # a file, not a dir
    assert next_attempt_number(tmp_path, _TASK) == 2


def test_write_and_read_latest_ledger_round_trip(tmp_path: Path) -> None:
    write_evidence_ledger(tmp_path, _TASK, 1, [_record("AC-1", 0), _record("AC-2", 3)])
    records = read_latest_evidence_ledger(tmp_path, _TASK)
    assert [r.ac_ids for r in records] == [["AC-1"], ["AC-2"]]
    assert [r.exit_code for r in records] == [0, 3]
    # The ledger file is human-readable JSON.
    ledger = evidence_attempt_dir(tmp_path, _TASK, 1) / "evidence.json"
    assert isinstance(json.loads(ledger.read_text(encoding="utf-8")), list)


def test_read_latest_ledger_no_pointer(tmp_path: Path) -> None:
    assert read_latest_evidence_ledger(tmp_path, _TASK) == []


def test_latest_attempt_number_absent(tmp_path: Path) -> None:
    assert latest_attempt_number(tmp_path, _TASK) is None


def test_latest_attempt_number_malformed_pointer(tmp_path: Path) -> None:
    root = evidence_root(tmp_path, _TASK)
    root.mkdir(parents=True)
    (root / "latest.json").write_text("not json", encoding="utf-8")
    assert latest_attempt_number(tmp_path, _TASK) is None


def test_latest_attempt_number_non_dict_payload(tmp_path: Path) -> None:
    root = evidence_root(tmp_path, _TASK)
    root.mkdir(parents=True)
    (root / "latest.json").write_text("[1, 2, 3]", encoding="utf-8")
    assert latest_attempt_number(tmp_path, _TASK) is None


def test_latest_attempt_number_non_int_attempt(tmp_path: Path) -> None:
    root = evidence_root(tmp_path, _TASK)
    root.mkdir(parents=True)
    (root / "latest.json").write_text(json.dumps({"attempt": "two"}), encoding="utf-8")
    assert latest_attempt_number(tmp_path, _TASK) is None


def test_read_latest_ledger_missing_ledger_file(tmp_path: Path) -> None:
    # Pointer claims attempt 5 but no ledger was written there.
    root = evidence_root(tmp_path, _TASK)
    root.mkdir(parents=True)
    (root / "latest.json").write_text(json.dumps({"attempt": 5}), encoding="utf-8")
    assert read_latest_evidence_ledger(tmp_path, _TASK) == []


def test_read_latest_ledger_malformed_json(tmp_path: Path) -> None:
    attempt_dir = evidence_attempt_dir(tmp_path, _TASK, 1)
    attempt_dir.mkdir(parents=True)
    (attempt_dir / "evidence.json").write_text("{ not json", encoding="utf-8")
    (evidence_root(tmp_path, _TASK) / "latest.json").write_text(json.dumps({"attempt": 1}), encoding="utf-8")
    assert read_latest_evidence_ledger(tmp_path, _TASK) == []


def test_read_latest_ledger_non_list_payload(tmp_path: Path) -> None:
    attempt_dir = evidence_attempt_dir(tmp_path, _TASK, 1)
    attempt_dir.mkdir(parents=True)
    (attempt_dir / "evidence.json").write_text(json.dumps({"ac_ids": ["AC-1"]}), encoding="utf-8")
    (evidence_root(tmp_path, _TASK) / "latest.json").write_text(json.dumps({"attempt": 1}), encoding="utf-8")
    assert read_latest_evidence_ledger(tmp_path, _TASK) == []


def test_read_latest_ledger_skips_non_dict_entries(tmp_path: Path) -> None:
    attempt_dir = evidence_attempt_dir(tmp_path, _TASK, 1)
    attempt_dir.mkdir(parents=True)
    payload = [_record("AC-1", 0).to_dict(), "garbage", 42]
    (attempt_dir / "evidence.json").write_text(json.dumps(payload), encoding="utf-8")
    (evidence_root(tmp_path, _TASK) / "latest.json").write_text(json.dumps({"attempt": 1}), encoding="utf-8")
    records = read_latest_evidence_ledger(tmp_path, _TASK)
    assert len(records) == 1
    assert records[0].ac_ids == ["AC-1"]


# ---------------------------------------------------------------------------
# Deterministic per-unit gate environment (TDI: AC-FINAL flaky-gate fix, item 1)
# ---------------------------------------------------------------------------


class TestDeterministicGateEnv:
    """``deterministic_gate_env`` pins the pytest ordering seed so a unit's
    completion gate is reproducible across runs (same input -> same verdict)."""

    def test_overlay_pins_pythonhashseed(self) -> None:
        overlay = deterministic_gate_env({}, seed=12345)
        assert overlay["PYTHONHASHSEED"] == "12345"

    def test_pythonhashseed_pinned_even_when_randomly_not_pinned(self) -> None:
        # The always-safe interpreter-level knob is set regardless of pin_randomly.
        overlay = deterministic_gate_env({}, seed=8, pin_randomly=False)
        assert overlay["PYTHONHASHSEED"] == "8"

    def test_overlay_pins_randomly_seed_in_pytest_addopts(self) -> None:
        overlay = deterministic_gate_env({}, seed=777)
        assert "--randomly-seed=777" in overlay["PYTEST_ADDOPTS"]

    def test_no_addopts_injected_when_pin_randomly_false(self) -> None:
        # A repo WITHOUT pytest-randomly would error on the unknown option, so
        # the seed flag must NOT be injected when pin_randomly is False.
        overlay = deterministic_gate_env({}, seed=5, pin_randomly=False)
        assert "PYTEST_ADDOPTS" not in overlay

    def test_pre_existing_addopts_untouched_when_pin_randomly_false(self) -> None:
        base = {"PYTEST_ADDOPTS": "-q"}
        overlay = deterministic_gate_env(base, seed=5, pin_randomly=False)
        assert overlay["PYTEST_ADDOPTS"] == "-q"

    def test_overlay_preserves_existing_pytest_addopts(self) -> None:
        base = {"PYTEST_ADDOPTS": "--maxfail=1 -q"}
        overlay = deterministic_gate_env(base, seed=5)
        addopts = overlay["PYTEST_ADDOPTS"]
        assert "--maxfail=1" in addopts
        assert "-q" in addopts
        assert "--randomly-seed=5" in addopts

    def test_overlay_does_not_duplicate_seed_when_already_present(self) -> None:
        base = {"PYTEST_ADDOPTS": "--randomly-seed=99"}
        overlay = deterministic_gate_env(base, seed=5)
        addopts = overlay["PYTEST_ADDOPTS"]
        # The pinned seed wins; the stale one must not survive as a duplicate.
        assert addopts.count("--randomly-seed") == 1
        assert "--randomly-seed=5" in addopts
        assert "--randomly-seed=99" not in addopts

    def test_overlay_is_deterministic_same_seed_same_result(self) -> None:
        assert deterministic_gate_env({}, seed=42) == deterministic_gate_env({}, seed=42)

    def test_overlay_does_not_mutate_input_mapping(self) -> None:
        base = {"PYTEST_ADDOPTS": "-q"}
        deterministic_gate_env(base, seed=1)
        assert base == {"PYTEST_ADDOPTS": "-q"}

    def test_overlay_carries_through_unrelated_keys(self) -> None:
        base = {"PATH": "/usr/bin", "HOME": "/home/x"}
        overlay = deterministic_gate_env(base, seed=3)
        assert overlay["PATH"] == "/usr/bin"
        assert overlay["HOME"] == "/home/x"

    def test_negative_seed_rejected(self) -> None:
        with pytest.raises(ValueError, match="seed"):
            deterministic_gate_env({}, seed=-1)


class TestPytestRandomlyAvailable:
    """``pytest_randomly_available`` probes the target repo, fail-safe to False."""

    def test_true_when_probe_exits_zero(self, tmp_path: Path) -> None:
        calls: list[tuple[list[str], Path]] = []

        def runner(cmd: list[str], cwd: Path) -> tuple[int, str, str]:
            calls.append((cmd, cwd))
            return 0, "", ""

        assert pytest_randomly_available(tmp_path, runner) is True
        # It probes by importing the plugin in the repo working dir.
        assert calls[0][0] == ["python", "-c", "import pytest_randomly"]
        assert calls[0][1] == tmp_path

    def test_false_when_probe_nonzero(self, tmp_path: Path) -> None:
        def runner(cmd: list[str], cwd: Path) -> tuple[int, str, str]:
            return 1, "", "ModuleNotFoundError"

        assert pytest_randomly_available(tmp_path, runner) is False

    def test_false_when_interpreter_missing(self, tmp_path: Path) -> None:
        # run_command returns 127 (never raises) when python is absent.
        def runner(cmd: list[str], cwd: Path) -> tuple[int, str, str]:
            return 127, "", "python: command not found"

        assert pytest_randomly_available(tmp_path, runner) is False
