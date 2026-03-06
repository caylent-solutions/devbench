"""Prompt loader for judge system prompts.

All prompts are stored as text files in this directory and loaded at import time.
This centralizes prompt management and avoids hardcoded strings in source code.
"""

from pathlib import Path

_PROMPTS_DIR = Path(__file__).parent


def load_prompt(name: str) -> str:
    """Load a prompt file by name (without extension).

    Args:
        name: The prompt file name without the ``.txt`` extension.

    Returns:
        The prompt text content.

    Raises:
        FileNotFoundError: If the prompt file does not exist.
    """
    prompt_path = _PROMPTS_DIR / f"{name}.txt"
    if not prompt_path.exists():
        raise FileNotFoundError(f"Prompt file not found: {prompt_path}")
    return prompt_path.read_text(encoding="utf-8").strip()
