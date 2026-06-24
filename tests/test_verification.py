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


@pytest.mark.parametrize(
    ("command", "expected"),
    [
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
        "test -d terragrunt/common/sandbox/000",
        "jq -e . terragrunt/common/accounts.json",
        "grep -q region providers/aws/references/vpc-network/main.tf",
        "cat terraform.tfvars",
        "test -f providers/aws/primitives/spice-kms/variables.tf",
        "aws s3 ls",
        "aws ec2 describe-instances",
    ],
)
def test_detect_iac_tool_negative(command: str | None) -> None:
    assert detect_iac_tool(command) is None


def test_terragrunt_precedence_over_other_tools() -> None:
    assert detect_iac_tool("terragrunt apply") == "terragrunt"


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


@pytest.mark.parametrize(
    ("command", "expected"),
    [
        (None, []),
        ("", []),
        ("test -d terragrunt/common/sandbox/000", ["terragrunt/common/sandbox/000"]),
        ("grep -q x providers/aws/references/vpc/main.tf", ["providers/aws/references/vpc/main.tf"]),
        ("cat terraform.tfvars", ["terraform.tfvars"]),
        ("grep -rnE PATTERN $(find providers -name '*.tf')", []),
        ("echo `cat providers/aws/x.tf`", []),
        ("terraform validate -backend=false", []),
        ("jq -e . ${CONFIG}", []),
        ("ls providers/*.tf", []),
        ("make lint", []),
        ("diff a/x.tf a/x.tf", ["a/x.tf"]),
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
        ("requires the Terraform toolchain the orchestrator runs at execution time", "Terraform"),
        ("runs terraform init -backend=false && terraform validate", "terraform"),
        ("pytest must run for this check", "pytest"),
        ("real production terragrunt apply against a live account", None),
        ("prod apply is operator-only", None),
        ("requires AWS credentials the orchestrator must not hold", None),
        ("a human must visually inspect the dashboard", None),
    ],
)
def test_deferred_reason_names_runnable_tool(reason: str | None, expected_match: str | None) -> None:
    result = deferred_reason_names_runnable_tool(reason)
    if expected_match is None:
        assert result is None
    else:
        assert result is not None and result.lower() == expected_match.lower()


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
    assert items[1].command == 'bash -c "curl -sf $URL | grep ok"'
    assert items[2].owner == "operator"
    assert items[2].reason == "prod apply is operator-only"


def test_parse_item_multiple_ac_ids_and_inferred_tool() -> None:
    item = parse_verification_item(" AC-3, AC-5 | type=apply | cmd=`terraform apply`")
    assert item.ac_ids == ("AC-3", "AC-5")
    assert item.tool == "terraform"
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
    assert cmd_infra.is_infra() is True

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


def test_unit_requires_iac_judge_true_for_infra() -> None:
    assert unit_requires_iac_judge(_WU_WITH_VERIFICATION) is True


def test_unit_requires_iac_judge_false_when_no_infra() -> None:
    content = "# T1\n\n## Verification\n- VERIFY AC-1 | type=command | cmd=`pytest -q`\n- VERIFY AC-2 | type=judge\n"
    assert unit_requires_iac_judge(content) is False


def test_unit_requires_iac_judge_false_on_malformed() -> None:
    content = "# T1\n\n## Verification\n- VERIFY AC-1 | type=bogus\n"
    assert unit_requires_iac_judge(content) is False


def test_unit_requires_iac_judge_false_for_path_substring_only() -> None:
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
    content = f"# T1\n\n## Verification\n- VERIFY AC-1 | type=command | cmd=`{cmd}`\n"
    assert unit_requires_iac_judge(content) is True


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
    assert rec.exit_code == 1
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


_TASK = "E1-F1-S1-T1"


def _record(ac: str, exit_code: int) -> EvidenceRecord:
    return EvidenceRecord(ac_ids=[ac], vtype="apply", command="make tf-apply", exit_code=exit_code, tool="terraform")


def test_sanitize_ac_label_joins_and_replaces() -> None:
    assert sanitize_ac_label(["AC-3", "AC-7"]) == "AC-3-AC-7"
    assert sanitize_ac_label(["AC-FINAL/001"]) == "AC-FINAL_001"


def test_sanitize_ac_label_empty_falls_back() -> None:
    assert sanitize_ac_label([]) == "unknown"
    assert sanitize_ac_label(["//"]) == "_"


@pytest.mark.parametrize(
    ("text", "cap"),
    [
        ("short", 100),
        ("abcdef", 0),
        ("abcdef", -1),
        ("a" * 100, 100),
    ],
)
def test_trim_log_within_budget_or_disabled_is_verbatim(text: str, cap: int) -> None:
    """A log already within budget (or with trimming disabled) is returned as-is."""
    assert trim_log(text, cap) == text


def test_trim_log_no_sentinels_is_bounded_near_max_bytes() -> None:
    """A sentinel-free log over budget is bounded to roughly *max_bytes* (head + tail)."""
    text = "\n".join(f"plain line {i:04d} of filler output" for i in range(2000))
    cap = 600
    trimmed = trim_log(text, cap)
    assert trimmed != text
    assert len(trimmed) <= cap + len("[... 999999 bytes elided ...]")
    assert "plain line 0000" in trimmed
    assert "plain line 1999" in trimmed
    assert "bytes elided" in trimmed


def test_trim_log_retains_head_apply_destroy_and_tail_summary() -> None:
    """Apply/destroy/PASS sentinels at the HEAD survive even when the tail is plan-only.

    Mirrors a terratest package that runs its real apply/idempotency/destroy
    examples first and its plan-only negative tests last: the old tail-only
    slice discarded the apply proof; the sentinel-aware trim must keep it while
    still retaining the trailing package summary.
    """
    head = [
        "=== RUN   TestExampleApply",
        "    primitive_test.go:42: terraform apply",
        "Apply complete! Resources: 7 added, 0 changed, 0 destroyed.",
        "    primitive_test.go:58: idempotency re-plan",
        "No changes. Your infrastructure matches the configuration.",
        "    primitive_test.go:71: terraform destroy",
        "Destroy complete! Resources: 7 destroyed.",
        "--- PASS: TestExampleApply (93.43s)",
    ]
    middle = [f"    plan_only_test.go:{i}: negative validation assertion {i:04d}" for i in range(4000)]
    tail = [
        "--- PASS: TestEmptyNameValidationError (0.84s)",
        "PASS",
        "ok  \tgithub.com/example/module/tests/default\t93.430s",
    ]
    text = "\n".join(head + middle + tail)
    cap = 2048
    trimmed = trim_log(text, cap)

    assert "Apply complete! Resources: 7 added, 0 changed, 0 destroyed." in trimmed
    assert "No changes. Your infrastructure matches the configuration." in trimmed
    assert "Destroy complete! Resources: 7 destroyed." in trimmed
    assert "--- PASS: TestExampleApply (93.43s)" in trimmed
    assert "ok  \tgithub.com/example/module/tests/default\t93.430s" in trimmed
    assert "negative validation assertion 2000" not in trimmed
    assert "bytes elided" in trimmed


def test_trim_log_retains_fail_and_panic_sentinels() -> None:
    """Failure sentinels (--- FAIL:, panic:, FAIL package summary) survive a trim."""
    head = ["--- FAIL: TestSomething (1.20s)", "    panic: runtime error: index out of range"]
    middle = [f"verbose trace frame {i:05d}" for i in range(3000)]
    tail = ["FAIL\tgithub.com/example/module/tests/default\t1.230s", "exit status 1"]
    text = "\n".join(head + middle + tail)
    trimmed = trim_log(text, 1024)
    assert "--- FAIL: TestSomething (1.20s)" in trimmed
    assert "panic: runtime error: index out of range" in trimmed
    assert "FAIL\tgithub.com/example/module/tests/default\t1.230s" in trimmed
    assert "verbose trace frame 01500" not in trimmed


def test_trim_log_is_pure() -> None:
    """trim_log must not mutate its input."""
    text = "\n".join(f"Apply complete! run {i}" for i in range(500))
    before = text
    trim_log(text, 200)
    assert text == before


def test_trim_log_head_and_tail_windows_are_disjoint_no_double_count() -> None:
    """Head and tail windows never overlap, so no line is emitted twice.

    For an over-budget sentinel-free log the head and tail windows together
    stay within budget and a single elision marker bridges the dropped middle;
    no line appears more than once.
    """
    lines = [f"line{i:03d}-payload" for i in range(400)]
    text = "\n".join(lines)
    cap = 400
    trimmed = trim_log(text, cap)
    out_lines = trimmed.split("\n")
    payload_lines = [ln for ln in out_lines if ln.startswith("line")]
    assert len(payload_lines) == len(set(payload_lines))
    assert "line000-payload" in trimmed
    assert "line399-payload" in trimmed
    assert trimmed.count("bytes elided") == 1


def test_trim_log_skips_sentinel_that_exceeds_remaining_budget() -> None:
    """A sentinel line that does not fit the remaining budget is dropped, not forced.

    Exercises the sentinel-loop branch that skips an over-budget sentinel so the
    result never blows past *max_bytes* just to keep one more marker.
    """
    short_sentinel = "Apply complete!"
    long_sentinel = "Error: " + ("x" * 5000)
    middle = [f"filler {i:04d}" for i in range(2000)]
    tail = ["ok  \tgithub.com/example/m\t1.0s"]
    text = "\n".join([short_sentinel, *middle, long_sentinel, *middle, *tail])
    cap = 400
    trimmed = trim_log(text, cap)
    assert short_sentinel in trimmed
    assert "ok  \tgithub.com/example/m\t1.0s" in trimmed
    assert long_sentinel not in trimmed
    assert len(trimmed) <= cap + len(short_sentinel) + len("[... 9999999 bytes elided ...]\n") * 4


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
    (root / "latest.json").write_text("{}", encoding="utf-8")
    assert next_attempt_number(tmp_path, _TASK) == 2


def test_write_and_read_latest_ledger_round_trip(tmp_path: Path) -> None:
    write_evidence_ledger(tmp_path, _TASK, 1, [_record("AC-1", 0), _record("AC-2", 3)])
    records = read_latest_evidence_ledger(tmp_path, _TASK)
    assert [r.ac_ids for r in records] == [["AC-1"], ["AC-2"]]
    assert [r.exit_code for r in records] == [0, 3]
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


class TestDeterministicGateEnv:
    """``deterministic_gate_env`` pins the pytest ordering seed so a unit's
    completion gate is reproducible across runs (same input -> same verdict)."""

    def test_overlay_pins_pythonhashseed(self) -> None:
        overlay = deterministic_gate_env({}, seed=12345)
        assert overlay["PYTHONHASHSEED"] == "12345"

    def test_pythonhashseed_pinned_even_when_randomly_not_pinned(self) -> None:
        overlay = deterministic_gate_env({}, seed=8, pin_randomly=False)
        assert overlay["PYTHONHASHSEED"] == "8"

    def test_overlay_pins_randomly_seed_in_pytest_addopts(self) -> None:
        overlay = deterministic_gate_env({}, seed=777)
        assert "--randomly-seed=777" in overlay["PYTEST_ADDOPTS"]

    def test_no_addopts_injected_when_pin_randomly_false(self) -> None:
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
        assert calls[0][0] == ["python", "-c", "import pytest_randomly"]
        assert calls[0][1] == tmp_path

    def test_false_when_probe_nonzero(self, tmp_path: Path) -> None:
        def runner(cmd: list[str], cwd: Path) -> tuple[int, str, str]:
            return 1, "", "ModuleNotFoundError"

        assert pytest_randomly_available(tmp_path, runner) is False

    def test_false_when_interpreter_missing(self, tmp_path: Path) -> None:
        def runner(cmd: list[str], cwd: Path) -> tuple[int, str, str]:
            return 127, "", "python: command not found"

        assert pytest_randomly_available(tmp_path, runner) is False
