"""Answers-file parser for the headless ``create-spec`` skill.

Issue #256. The ``create-spec`` skill accepts ``--answers-file <path>`` to
run headlessly without prompting the operator. The file is a YAML mapping
keyed by Block letter A-G where each value is the pre-collected answer for
that block of questions.

Schema (Appendix D-6):
    A: <str>   # Block A -- Problem and context
    B: <str>   # Block B -- Goals, non-goals, and scope
    C: <str>   # Block C -- Functional requirements and command surface
    D: <str>   # Block D -- Data formats and integration points
    E: <str>   # Block E -- NFRs, error handling, and configuration
    F: <str>   # Block F -- Testing and documentation
    G: <str>   # Block G -- Acceptance criteria, decisions, and future work

All seven blocks are required. A missing block causes the headless skill to
fail fast with the verbatim ``[BLOCKED]`` message and a non-zero exit.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

__all__ = [
    "BLOCKED_MESSAGE_TEMPLATE",
    "REQUIRED_BLOCKS",
    "MalformedAnswersError",
    "MissingBlockError",
    "load_answers_file",
    "validate_answers",
]


REQUIRED_BLOCKS: list[str] = ["A", "B", "C", "D", "E", "F", "G"]
"""Ordered list of block keys that MUST be present in the answers file."""

BLOCKED_MESSAGE_TEMPLATE: str = "[BLOCKED] create-spec headless: missing answer for Block {block}"
"""Verbatim message template emitted (and raised) when a required block is
absent. Format with ``block=<letter>`` to produce the exact spec string."""


class MalformedAnswersError(ValueError):
    """Raised when the answers YAML cannot be parsed or is not a mapping."""


class MissingBlockError(ValueError):
    """Raised when a required block key is absent from the answers mapping.

    ``str(exc)`` returns the verbatim ``[BLOCKED]`` message required by
    spec AC-256-1 so callers can emit it directly to stderr.
    """


def load_answers_file(path: Path) -> dict[str, Any]:
    """Parse *path* as a YAML answers file and return the mapping.

    Raises:
        FileNotFoundError: if *path* does not exist.
        MalformedAnswersError: if the file cannot be parsed as YAML or
            the top-level value is not a mapping.
    """
    if not path.exists():
        raise FileNotFoundError(f"answers file not found: {path}")

    raw = path.read_text(encoding="utf-8")
    try:
        data = yaml.safe_load(raw)
    except yaml.YAMLError as exc:
        raise MalformedAnswersError(f"answers file {path} could not be parsed as YAML: {exc}") from exc

    if not isinstance(data, dict):
        raise MalformedAnswersError(f"answers file {path} must be a YAML mapping keyed A-G; got {type(data).__name__}")

    return data


def validate_answers(answers: dict[str, Any]) -> None:
    """Assert that every required block is present in *answers*.

    Returns ``None`` on success. Fails fast on the first missing block.

    Raises:
        MissingBlockError: with the verbatim ``[BLOCKED]`` message for
            the first missing required block, in alphabetical order
            (A before B before C, ...).
    """
    for block in REQUIRED_BLOCKS:
        if block not in answers:
            msg = BLOCKED_MESSAGE_TEMPLATE.format(block=block)
            raise MissingBlockError(msg)
