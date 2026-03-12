"""Base judge module providing abstract class and shared types for all judges.

All concrete judge implementations must inherit from ``BaseJudge`` and implement
the ``evaluate`` method.  Each judge gathers evidence (diffs, test output,
security alerts, etc.) and delegates the pass/fail decision to the LLM.
"""

import abc
import json
import logging
import subprocess
from dataclasses import dataclass
from enum import Enum
from pathlib import Path

import anthropic

from devbench.config import (
    BEDROCK_REGION,
    CLAUDE_MODEL,
    COMMAND_TIMEOUT,
    LLM_EVIDENCE_TRUNCATION,
    LLM_TIMEOUT,
    RUNTIME_CONFIG,
    USE_BEDROCK,
    get_anthropic_api_key,
)
from devbench.config_loader import get_configured_default_branch
from devbench.constants import ERROR_OUTPUT_PREVIEW_CHARS, LLM_RESPONSE_FORMAT_INSTRUCTIONS, RAW_RESPONSE_PREVIEW_CHARS


class Verdict(Enum):
    """Outcome of a judge evaluation."""

    PASS = "pass"
    FAIL = "fail"


@dataclass
class JudgeResult:
    """Structured result returned by every judge evaluation."""

    judge_name: str
    verdict: Verdict
    reasoning: str
    feedback: str  # actionable feedback if FAIL
    evidence: list[str]  # list of evidence items checked


class BaseJudge(abc.ABC):
    """Abstract base class for all judge implementations.

    Provides shared utilities for file reading, command execution,
    and LLM-powered evaluation via Claude.
    Subclasses must implement ``evaluate``.
    """

    def __init__(self, name: str) -> None:
        self.name = name
        self.logger = logging.getLogger(f"judge.{name}")
        self.previous_feedback: str = ""

    @abc.abstractmethod
    def evaluate(self, work_unit_path: Path, repo_path: Path, **kwargs: object) -> JudgeResult:
        """Evaluate a work unit. Returns JudgeResult with verdict and reasoning."""
        ...

    def _get_default_branch(self, repo_path: Path, repo: str = "") -> str:
        """Return the default branch name for the repo (e.g. ``main2``, ``main``).

        Resolution order:
        1. YAML ``repos.<repo>.default_branch`` when *repo* is provided and configured.
        2. ``git rev-parse --abbrev-ref origin/HEAD`` fallback.

        Args:
            repo_path: Local filesystem path to the repository.
            repo: Fully-qualified repo name (e.g. ``'org/repo'``).  When
                provided, the YAML config is consulted first.

        Raises:
            RuntimeError: If no YAML branch is configured and the git fallback
                cannot determine the default branch.
        """
        if repo:
            configured = get_configured_default_branch(repo, RUNTIME_CONFIG)
            if configured:
                return configured

        rc, stdout, _ = self._run_command(
            ["git", "rev-parse", "--abbrev-ref", "origin/HEAD"], cwd=repo_path,
        )
        if rc != 0 or not stdout.strip():
            raise RuntimeError(
                f"Cannot determine default branch in {repo_path}. "
                "Run 'git remote set-head origin --auto' to configure it."
            )
        # stdout is e.g. "origin/main" — strip the remote prefix
        return stdout.strip().removeprefix("origin/")

    def _read_file(self, path: Path) -> str:
        """Read a file and return its contents as a string.

        Raises ``FileNotFoundError`` if the file does not exist.
        """
        if not path.exists():
            raise FileNotFoundError(f"Judge {self.name}: file not found: {path}")
        return path.read_text(encoding="utf-8")

    def _run_command(
        self,
        cmd: list[str],
        cwd: Path | None = None,
        timeout: int | None = None,
    ) -> tuple[int, str, str]:
        """Run a shell command and return (returncode, stdout, stderr).

        Returns ``(127, "", "<error>")`` when the executable is not found
        or the command times out, avoiding crashes when a tool is missing
        or a task runner hangs.
        """
        effective_timeout = timeout if timeout is not None else COMMAND_TIMEOUT
        try:
            result = subprocess.run(
                cmd,
                cwd=cwd,
                capture_output=True,
                text=True,
                timeout=effective_timeout,
            )
        except FileNotFoundError:
            return 127, "", f"{cmd[0]}: command not found"
        except subprocess.TimeoutExpired:
            return 127, "", f"{' '.join(cmd)}: timed out after {effective_timeout}s"
        return result.returncode, result.stdout, result.stderr

    def _llm_evaluate(
        self,
        system_prompt: str,
        evidence_sections: dict[str, str],
        cwd: Path | None = None,
        timeout: int | None = None,
    ) -> JudgeResult:
        """Use Claude via the Anthropic API to evaluate evidence.

        Args:
            system_prompt: Instructions telling Claude what to evaluate and how.
                Must instruct Claude to respond in JSON with keys:
                verdict ("pass" or "fail"), reasoning, feedback, evidence.
            evidence_sections: Dict of section_name -> content to include in the prompt.
            cwd: Unused (kept for interface compatibility).
            timeout: Max seconds for the API call.

        Returns:
            JudgeResult parsed from Claude's JSON response.

        Raises:
            RuntimeError: If Claude credentials cannot be read.
        """
        evidence_text = ""
        for section_name, content in evidence_sections.items():
            if len(content) > LLM_EVIDENCE_TRUNCATION:
                truncated = (
                    content[:LLM_EVIDENCE_TRUNCATION]
                    + f"\n\n[... TRUNCATED — showing {LLM_EVIDENCE_TRUNCATION} of "
                    f"{len(content)} chars total.]"
                )
            else:
                truncated = content
            evidence_text += f"\n## {section_name}\n```\n{truncated}\n```\n"

        if self.previous_feedback:
            evidence_text += (
                "\n## Previous Review Feedback\n"
                "The following feedback was given in a prior review of this same work unit. "
                "If the code has been updated to address this feedback, do not re-raise the "
                "same issues. If the code has NOT addressed the feedback, flag it again. "
                "Do NOT contradict prior feedback by requesting the opposite change.\n"
                f"```\n{self.previous_feedback}\n```\n"
            )

        user_prompt = (
            f"# Evidence\n{evidence_text}\n\n"
            f"{LLM_RESPONSE_FORMAT_INSTRUCTIONS}"
        )

        effective_timeout = timeout if timeout is not None else LLM_TIMEOUT

        client: anthropic.AnthropicBedrock | anthropic.Anthropic
        if USE_BEDROCK:
            self.logger.info(
                "Calling Claude (%s) for %s evaluation via Bedrock (%s)",
                CLAUDE_MODEL, self.name, BEDROCK_REGION,
            )
            client = anthropic.AnthropicBedrock(aws_region=BEDROCK_REGION, timeout=effective_timeout)
        else:
            api_key = get_anthropic_api_key()
            self.logger.info("Calling Claude (%s) for %s evaluation via API", CLAUDE_MODEL, self.name)
            client = anthropic.Anthropic(api_key=api_key, timeout=effective_timeout)

        self.logger.debug(
            "LLM request — judge=%s model=%s system_prompt=%r user_prompt=%r",
            self.name, CLAUDE_MODEL, system_prompt, user_prompt,
        )

        try:
            message = client.messages.create(
                model=CLAUDE_MODEL,
                max_tokens=4096,
                system=system_prompt,
                messages=[{"role": "user", "content": user_prompt}],
            )
        except anthropic.APIError as exc:
            self.logger.warning("Anthropic API error for %s judge: %s", self.name, exc)
            return JudgeResult(
                judge_name=self.name,
                verdict=Verdict.FAIL,
                reasoning=f"LLM API call failed: {exc}",
                feedback=str(exc)[:ERROR_OUTPUT_PREVIEW_CHARS],
                evidence=["LLM API call failed"],
            )

        raw_output = ""
        if message.content:
            first_block = message.content[0]
            if hasattr(first_block, "text"):
                raw_output = first_block.text

        self.logger.debug(
            "LLM response — judge=%s stop_reason=%s usage=%s raw_output=%r",
            self.name, message.stop_reason, message.usage, raw_output,
        )

        return self._parse_llm_response(raw_output)

    def _parse_llm_response(self, raw_output: str) -> JudgeResult:
        """Parse Claude's JSON response into a JudgeResult.

        Handles common formatting issues (markdown fences, preamble text).
        """
        # Strip markdown code fences if present
        cleaned = raw_output.strip()
        if cleaned.startswith("```"):
            # Remove opening fence (with optional language tag)
            first_newline = cleaned.index("\n")
            cleaned = cleaned[first_newline + 1 :]
        if cleaned.endswith("```"):
            cleaned = cleaned[:-3]
        cleaned = cleaned.strip()

        # Try to find JSON object in the output
        json_start = cleaned.find("{")
        json_end = cleaned.rfind("}") + 1
        if json_start == -1 or json_end == 0:
            self.logger.warning("No JSON found in LLM response for %s judge", self.name)
            return JudgeResult(
                judge_name=self.name,
                verdict=Verdict.FAIL,
                reasoning="LLM response did not contain valid JSON",
                feedback=f"Raw response: {raw_output[:RAW_RESPONSE_PREVIEW_CHARS]}",
                evidence=["Failed to parse LLM response"],
            )

        json_str = cleaned[json_start:json_end]

        try:
            data = json.loads(json_str)
        except json.JSONDecodeError as exc:
            self.logger.warning("Failed to parse LLM JSON for %s: %s", self.name, exc)
            return JudgeResult(
                judge_name=self.name,
                verdict=Verdict.FAIL,
                reasoning=f"Failed to parse LLM JSON response: {exc}",
                feedback=f"Raw JSON: {json_str[:RAW_RESPONSE_PREVIEW_CHARS]}",
                evidence=["JSON parse error"],
            )

        verdict_str = str(data.get("verdict", "fail")).lower()
        verdict = Verdict.PASS if verdict_str == "pass" else Verdict.FAIL

        return JudgeResult(
            judge_name=self.name,
            verdict=verdict,
            reasoning=str(data.get("reasoning", "")),
            feedback=str(data.get("feedback", "")),
            evidence=[str(e) for e in data.get("evidence", [])],
        )
