from __future__ import annotations

from pathlib import Path

from puripuly_heart.config.prompts import load_prompt_for_provider


def test_load_prompt_for_qwen_matches_file() -> None:
    prompt = load_prompt_for_provider("qwen")
    raw = Path("prompts/qwen.txt").read_text(encoding="utf-8").strip()
    assert prompt == raw
    assert prompt
