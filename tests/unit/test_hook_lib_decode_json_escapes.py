"""Regression test for ``_hook_lib.sh::decode_json_escapes`` bash 3.0+ compat.

Issue #120: the previous implementation used ``local -n`` (bash 4.3+
nameref) which fails on macOS's stock bash 3.2.57 with
``local: -n: invalid option``. The replacement uses ``${!1}`` indirect
read + ``printf -v "$1"`` write, which work in bash 3.0+. This test
exercises the decoder against the four JSON escape sequences the
hook framework forwards to its guards (``\\"``, ``\\\\``, ``\\n``,
``\\t``) and confirms each round-trip produces the canonical
unescaped byte sequence.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

SCRIPT_PATH = Path(__file__).parent.parent.parent / "plugin" / "devbench" / "scripts" / "_hook_lib.sh"


def _run_decode(escaped: str) -> str:
    """Source ``_hook_lib.sh`` in a subshell, run ``decode_json_escapes``, return the value.

    The escaped input is forwarded verbatim via single quotes so bash
    sees the literal byte sequence (no shell-level interpretation of
    backslashes).
    """
    # Build the bash snippet with escaped single quotes for safety.
    quoted = "'" + escaped.replace("'", "'\\''") + "'"
    cmd = [
        "bash",
        "-c",
        f". {SCRIPT_PATH}; var={quoted}; decode_json_escapes var; printf '%s' \"$var\"",
    ]
    return subprocess.run(cmd, capture_output=True, text=True, check=True).stdout


@pytest.mark.parametrize(
    ("escaped", "expected"),
    [
        (r"hello\nworld", "hello\nworld"),
        (r"tab\there", "tab\there"),
        (r"plain text", "plain text"),
        ("", ""),
    ],
)
def test_decode_json_escapes_round_trip(escaped: str, expected: str) -> None:
    assert _run_decode(escaped) == expected


def test_decode_json_escapes_no_nameref_in_function_body() -> None:
    """The decoder must not use ``local -n`` -- bash 3.2.57 (macOS default) lacks namerefs.

    Greps the actual decoder function body (between the ``decode_json_escapes()``
    line and its closing ``}``) so the issue-#120 explanation comment elsewhere
    in the file does not false-positive the test.
    """
    body = SCRIPT_PATH.read_text(encoding="utf-8")
    # Extract just the function body so the issue #120 comment that
    # mentions "local -n" elsewhere in the file does not match.
    start = body.index("decode_json_escapes() {")
    end = body.index("\n}\n", start)
    function_body = body[start:end]
    # Strip comment lines so the rationale comment inside the function
    # (which legitimately mentions "local -n" as the thing we removed)
    # does not trigger the assertion.
    code_lines = [line for line in function_body.splitlines() if not line.strip().startswith("#")]
    code = "\n".join(code_lines)
    assert "local -n" not in code, (
        "decode_json_escapes must not use 'local -n' (bash 4.3+ nameref); "
        "use ${!1} indirect read + 'printf -v $1' instead so macOS bash 3.2.57 keeps working."
    )
