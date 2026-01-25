"""Prompt file loader utility.

Loads system prompts from files in the prompts/ directory.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path


def get_prompts_dir() -> Path:
    """Get the prompts directory path."""
    env_dir = os.getenv("PURIPULY_HEART_PROMPTS_DIR")
    if env_dir:
        env_path = Path(env_dir)
        if env_path.exists():
            return env_path

    # PyInstaller frozen app: use _MEIPASS
    if getattr(sys, "frozen", False) and hasattr(sys, "_MEIPASS"):
        meipass_prompts = Path(sys._MEIPASS) / "prompts"
        if meipass_prompts.exists():
            return meipass_prompts

    # Try relative to the project root first
    candidates = [
        Path(__file__).parent.parent.parent.parent
        / "prompts",  # src/puripuly_heart.../config -> project root
        Path.cwd() / "prompts",
        Path(__file__).parent / "prompts",
    ]

    for path in candidates:
        if path.exists():
            return path

    # Walk up from cwd to find project root (pyproject.toml) with prompts/
    for parent in [Path.cwd().resolve(), *Path.cwd().resolve().parents]:
        candidate = parent / "prompts"
        if (parent / "pyproject.toml").exists() and candidate.exists():
            return candidate

    # Walk up from cwd to find any prompts/ directory (e.g., when running from .venv)
    for parent in [Path.cwd().resolve(), *Path.cwd().resolve().parents]:
        candidate = parent / "prompts"
        if candidate.exists():
            return candidate

    # Default: relative to cwd
    return Path.cwd() / "prompts"


def list_prompts() -> list[str]:
    """List available prompt file names (without extension)."""
    prompts_dir = get_prompts_dir()
    if not prompts_dir.exists():
        return []

    return sorted([f.stem for f in prompts_dir.glob("*.md")])


def load_prompt(name: str = "default") -> str:
    """Load a prompt from file.

    Args:
        name: Prompt file name (without .txt extension)

    Returns:
        Prompt content, or empty string if not found
    """
    prompts_dir = get_prompts_dir()

    # Try .md first
    prompt_file = prompts_dir / f"{name}.md"
    if prompt_file.exists():
        return prompt_file.read_text(encoding="utf-8").strip()

    # Fallback to .txt
    prompt_file = prompts_dir / f"{name}.txt"
    if prompt_file.exists():
        return prompt_file.read_text(encoding="utf-8").strip()

    # Fallback to default
    default_file = prompts_dir / "default.md"
    if default_file.exists():
        return default_file.read_text(encoding="utf-8").strip()

    # Legacy default.txt
    default_file = prompts_dir / "default.txt"
    if default_file.exists():
        return default_file.read_text(encoding="utf-8").strip()

    return ""


def get_default_prompt() -> str:
    """Load the default prompt."""
    return load_prompt("default")


def load_prompt_for_provider(provider: str) -> str:
    """Load the prompt for a specific LLM provider.

    Args:
        provider: Provider name ('gemini' or 'qwen')

    Returns:
        Prompt content for the provider, or default if not found
    """
    provider_lower = provider.lower()
    prompts_dir = get_prompts_dir()

    provider_lower = provider.lower()
    prompts_dir = get_prompts_dir()

    # Try provider-specific prompt first (.md)
    prompt_file = prompts_dir / f"{provider_lower}.md"
    if prompt_file.exists():
        return prompt_file.read_text(encoding="utf-8").strip()

    # Try provider-specific prompt first (.txt)
    prompt_file = prompts_dir / f"{provider_lower}.txt"
    if prompt_file.exists():
        return prompt_file.read_text(encoding="utf-8").strip()

    # Fallback to default
    return load_prompt("default")


def load_qwen_few_shot() -> list[dict[str, str]]:
    """Load Qwen few-shot examples from Qwen_few_shots.json.

    Returns:
        List of dictionaries with 'source' and 'target' keys.
        Returns empty list if file not found or invalid.
    """
    import json

    prompts_dir = get_prompts_dir()
    fs_file = prompts_dir / "qwen_few_shots.json"

    if not fs_file.exists():
        return []

    try:
        data = json.loads(fs_file.read_text(encoding="utf-8"))
        if isinstance(data, list):
            # Validate basic structure
            valid: list[dict[str, str]] = []
            for item in data:
                if isinstance(item, dict) and "source" in item and "target" in item:
                    valid.append({"source": str(item["source"]), "target": str(item["target"])})
            return valid
        return []
    except Exception:
        return []
