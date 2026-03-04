from __future__ import annotations

from pathlib import Path

import pytest

pytest.importorskip("flet")

from puripuly_heart.config.prompts import load_prompt_for_provider
from puripuly_heart.config.settings import (
    AppSettings,
    GeminiLLMModel,
    LLMProviderName,
    QwenLLMModel,
    STTProviderName,
)
from puripuly_heart.ui.i18n import t
from puripuly_heart.ui.views import settings as settings_view


class DummySecretStore:
    def get(self, _key: str) -> str | None:
        return None


def _make_settings_view(monkeypatch):
    monkeypatch.setattr(settings_view.SettingsView, "_populate_host_apis", lambda self: None)
    monkeypatch.setattr(settings_view.SettingsView, "_refresh_microphones", lambda self: None)
    monkeypatch.setattr(settings_view.SettingsView, "update", lambda self: None)
    monkeypatch.setattr(
        settings_view, "create_secret_store", lambda *args, **kwargs: DummySecretStore()
    )
    return settings_view.SettingsView()


def test_settings_view_loads_qwen_prompt(monkeypatch) -> None:
    settings = AppSettings()
    settings.provider.llm = LLMProviderName.QWEN

    view = _make_settings_view(monkeypatch)
    view.load_from_settings(settings, config_path=Path("settings.json"))

    assert view._prompt_editor.value == load_prompt_for_provider("qwen")
    assert settings.system_prompt == view._prompt_editor.value
    assert settings.system_prompts["qwen"] == view._prompt_editor.value


def test_settings_view_switches_prompt_on_llm_change(monkeypatch) -> None:
    settings = AppSettings()

    view = _make_settings_view(monkeypatch)
    view.load_from_settings(settings, config_path=Path("settings.json"))

    assert view._prompt_editor.value == load_prompt_for_provider("gemini")

    view._on_llm_selected(QwenLLMModel.QWEN_35_PLUS.value)

    assert view._prompt_editor.value == load_prompt_for_provider("qwen")
    assert settings.provider.llm == LLMProviderName.QWEN
    assert settings.qwen.llm_model == QwenLLMModel.QWEN_35_PLUS

    view._on_llm_selected(GeminiLLMModel.GEMINI_3_FLASH.value)

    assert view._prompt_editor.value == load_prompt_for_provider("gemini")
    assert settings.provider.llm == LLMProviderName.GEMINI


def test_settings_view_shows_qwen_model_label(monkeypatch) -> None:
    settings = AppSettings()
    settings.provider.llm = LLMProviderName.QWEN
    settings.qwen.llm_model = QwenLLMModel.QWEN_35_PLUS

    view = _make_settings_view(monkeypatch)
    view.load_from_settings(settings, config_path=Path("settings.json"))

    assert view._llm_text.content.value == t("provider.qwen35_plus")


def test_settings_view_preserves_provider_specific_prompts(monkeypatch) -> None:
    settings = AppSettings()
    settings.provider.llm = LLMProviderName.GEMINI
    settings.system_prompts = {
        "gemini": "GEMINI CUSTOM",
        "qwen": "QWEN CUSTOM",
    }
    settings.system_prompt = "GEMINI CUSTOM"

    view = _make_settings_view(monkeypatch)
    view.load_from_settings(settings, config_path=Path("settings.json"))

    assert view._prompt_editor.value == "GEMINI CUSTOM"

    view._on_llm_selected(QwenLLMModel.QWEN_35_FLASH.value)
    assert view._prompt_editor.value == "QWEN CUSTOM"
    assert settings.system_prompt == "QWEN CUSTOM"

    view._on_prompt_change("QWEN EDITED")
    assert settings.system_prompts["qwen"] == "QWEN EDITED"

    view._on_llm_selected(LLMProviderName.GEMINI.value)
    assert view._prompt_editor.value == "GEMINI CUSTOM"
    assert settings.system_prompt == "GEMINI CUSTOM"


def test_settings_view_llm_modal_orders_qwen_plus_before_flash(monkeypatch) -> None:
    settings = AppSettings()
    view = _make_settings_view(monkeypatch)
    view.load_from_settings(settings, config_path=Path("settings.json"))
    view.page = object()

    captured: dict[str, object] = {}

    class DummyModal:
        def __init__(self, _page, _title, options, _on_select, *, show_description=False):
            captured["options"] = options
            captured["show_description"] = show_description

        def open(self, current: str) -> None:
            captured["current"] = current

    monkeypatch.setattr(settings_view, "SettingsModal", DummyModal)

    view._on_llm_click(None)

    assert captured["show_description"] is True
    options = captured["options"]
    values = [option.value for option in options]
    assert values == [
        GeminiLLMModel.GEMINI_3_FLASH.value,
        GeminiLLMModel.GEMINI_31_FLASH_LITE.value,
        QwenLLMModel.QWEN_35_PLUS.value,
        QwenLLMModel.QWEN_35_FLASH.value,
    ]


def test_settings_view_updates_gemini_model_without_provider_switch(monkeypatch) -> None:
    settings = AppSettings()
    settings.provider.llm = LLMProviderName.GEMINI
    settings.system_prompts = {
        "gemini": "GEMINI CUSTOM",
        "qwen": "QWEN CUSTOM",
    }
    settings.system_prompt = "GEMINI CUSTOM"

    view = _make_settings_view(monkeypatch)
    view.load_from_settings(settings, config_path=Path("settings.json"))

    view._on_llm_selected(GeminiLLMModel.GEMINI_31_FLASH_LITE.value)

    assert settings.provider.llm == LLMProviderName.GEMINI
    assert settings.gemini.llm_model == GeminiLLMModel.GEMINI_31_FLASH_LITE
    assert settings.system_prompt == "GEMINI CUSTOM"
    assert view._prompt_editor.value == "GEMINI CUSTOM"


def test_settings_view_toggles_qwen_region_visibility_with_stt_provider(monkeypatch) -> None:
    settings = AppSettings()
    view = _make_settings_view(monkeypatch)
    view.load_from_settings(settings, config_path=Path("settings.json"))

    assert view._qwen_region_btn.visible is False

    view._on_stt_selected(STTProviderName.QWEN_ASR.value)
    assert view._qwen_region_btn.visible is True

    view._on_stt_selected(STTProviderName.DEEPGRAM.value)
    assert view._qwen_region_btn.visible is False
