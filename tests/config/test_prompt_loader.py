from __future__ import annotations

import sys
from pathlib import Path

import puripuly_heart.config.prompts as prompts_module
from puripuly_heart.config.prompts import (
    get_default_prompt,
    get_prompts_dir,
    list_prompts,
    load_prompt,
    load_prompt_for_provider,
)


def test_load_prompt_for_qwen_matches_file() -> None:
    prompt = load_prompt_for_provider("qwen")
    raw = Path("prompts/qwen.md").read_text(encoding="utf-8").strip()
    assert prompt == raw
    assert prompt


def test_get_prompts_dir_prefers_env(tmp_path, monkeypatch) -> None:
    prompts_dir = tmp_path / "prompts"
    prompts_dir.mkdir()
    (prompts_dir / "default.txt").write_text("DEFAULT", encoding="utf-8")

    monkeypatch.setenv("PURIPULY_HEART_PROMPTS_DIR", str(prompts_dir))

    assert get_prompts_dir() == prompts_dir


def test_list_prompts_returns_sorted_names(tmp_path, monkeypatch) -> None:
    prompts_dir = tmp_path / "prompts"
    prompts_dir.mkdir()
    (prompts_dir / "b.md").write_text("B", encoding="utf-8")
    (prompts_dir / "a.md").write_text("A", encoding="utf-8")

    monkeypatch.setenv("PURIPULY_HEART_PROMPTS_DIR", str(prompts_dir))

    assert list_prompts() == ["a", "b"]


def test_load_prompt_falls_back_to_default(tmp_path, monkeypatch) -> None:
    prompts_dir = tmp_path / "prompts"
    prompts_dir.mkdir()
    (prompts_dir / "default.txt").write_text("DEFAULT", encoding="utf-8")

    monkeypatch.setenv("PURIPULY_HEART_PROMPTS_DIR", str(prompts_dir))

    assert load_prompt("missing") == "DEFAULT"


def test_load_prompt_returns_empty_when_missing(tmp_path, monkeypatch) -> None:
    prompts_dir = tmp_path / "prompts"
    prompts_dir.mkdir()

    monkeypatch.setenv("PURIPULY_HEART_PROMPTS_DIR", str(prompts_dir))

    assert load_prompt("missing") == ""


def test_load_prompt_for_provider_falls_back_to_default(tmp_path, monkeypatch) -> None:
    prompts_dir = tmp_path / "prompts"
    prompts_dir.mkdir()
    (prompts_dir / "default.txt").write_text("DEFAULT", encoding="utf-8")

    monkeypatch.setenv("PURIPULY_HEART_PROMPTS_DIR", str(prompts_dir))

    assert load_prompt_for_provider("gemini") == "DEFAULT"


def test_get_prompts_dir_uses_pyinstaller_meipass(tmp_path, monkeypatch) -> None:
    bundle_root = tmp_path / "bundle"
    prompts_dir = bundle_root / "prompts"
    prompts_dir.mkdir(parents=True)

    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "_MEIPASS", str(bundle_root), raising=False)

    assert get_prompts_dir() == prompts_dir


def test_list_prompts_returns_empty_when_missing(monkeypatch, tmp_path) -> None:
    missing_dir = tmp_path / "missing"
    monkeypatch.setattr(prompts_module, "get_prompts_dir", lambda: missing_dir)

    assert list_prompts() == []


def test_get_default_prompt_reads_default(tmp_path, monkeypatch) -> None:
    prompts_dir = tmp_path / "prompts"
    prompts_dir.mkdir()
    (prompts_dir / "default.txt").write_text("DEFAULT", encoding="utf-8")

    monkeypatch.setenv("PURIPULY_HEART_PROMPTS_DIR", str(prompts_dir))

    assert get_default_prompt() == "DEFAULT"


def test_get_prompts_dir_falls_back_to_cwd(tmp_path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("PURIPULY_HEART_PROMPTS_DIR", raising=False)
    monkeypatch.setattr(prompts_module, "__file__", str(tmp_path / "fake.py"))

    assert get_prompts_dir() == tmp_path / "prompts"
