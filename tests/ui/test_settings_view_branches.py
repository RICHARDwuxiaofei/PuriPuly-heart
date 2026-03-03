from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

pytest.importorskip("flet")

from puripuly_heart.config.settings import (
    AppSettings,
    LLMProviderName,
    QwenLLMModel,
    QwenRegion,
    STTProviderName,
)
from puripuly_heart.ui.i18n import t
from puripuly_heart.ui.views import settings as settings_view


class DummySecretStore:
    def __init__(self, values: dict[str, str] | None = None) -> None:
        self.values = dict(values or {})
        self.set_calls: list[tuple[str, str]] = []
        self.delete_calls: list[str] = []

    def get(self, key: str) -> str | None:
        return self.values.get(key)

    def set(self, key: str, value: str) -> None:
        self.values[key] = value
        self.set_calls.append((key, value))

    def delete(self, key: str) -> None:
        self.values.pop(key, None)
        self.delete_calls.append(key)


def _make_settings_view(monkeypatch: pytest.MonkeyPatch, store: DummySecretStore | None = None):
    monkeypatch.setattr(settings_view.SettingsView, "_populate_host_apis", lambda self: None)
    monkeypatch.setattr(settings_view.SettingsView, "_refresh_microphones", lambda self: None)
    monkeypatch.setattr(settings_view.SettingsView, "update", lambda self: None)
    store = store or DummySecretStore()
    monkeypatch.setattr(settings_view, "create_secret_store", lambda *_args, **_kwargs: store)
    return settings_view.SettingsView(), store


def test_load_secret_value_prefers_existing_value() -> None:
    store = DummySecretStore({"new_key": "new", "old_key": "old"})

    value = settings_view._load_secret_value(store, "new_key", legacy_keys=("old_key",))

    assert value == "new"
    assert store.set_calls == []


def test_load_secret_value_migrates_legacy_value() -> None:
    store = DummySecretStore({"old_key": "legacy"})

    value = settings_view._load_secret_value(store, "new_key", legacy_keys=("old_key",))

    assert value == "legacy"
    assert store.set_calls == [("new_key", "legacy")]


def test_load_from_settings_uses_system_prompt_when_provider_prompt_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = AppSettings()
    settings.provider.llm = LLMProviderName.GEMINI
    settings.system_prompt = "LEGACY PROMPT"
    settings.system_prompts = {}

    view, _ = _make_settings_view(monkeypatch)
    view.load_from_settings(settings, config_path=Path("settings.json"))

    assert view._prompt_editor.value == "LEGACY PROMPT"
    assert settings.system_prompts["gemini"] == "LEGACY PROMPT"


def test_load_from_settings_uses_default_prompt_when_all_empty(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = AppSettings()
    settings.provider.llm = LLMProviderName.QWEN
    settings.system_prompt = ""
    settings.system_prompts = {}

    view, _ = _make_settings_view(monkeypatch)
    view.load_from_settings(settings, config_path=Path("settings.json"))

    assert bool(view._prompt_editor.value.strip())
    assert settings.system_prompt == view._prompt_editor.value
    assert settings.system_prompts["qwen"] == view._prompt_editor.value


def test_load_secrets_failure_is_ignored(monkeypatch: pytest.MonkeyPatch) -> None:
    settings = AppSettings()

    monkeypatch.setattr(settings_view.SettingsView, "_populate_host_apis", lambda self: None)
    monkeypatch.setattr(settings_view.SettingsView, "_refresh_microphones", lambda self: None)
    monkeypatch.setattr(settings_view.SettingsView, "update", lambda self: None)

    def raise_store(*_args, **_kwargs):
        raise RuntimeError("boom")

    monkeypatch.setattr(settings_view, "create_secret_store", raise_store)
    view = settings_view.SettingsView()
    view.load_from_settings(settings, config_path=Path("settings.json"))

    assert view._google_key.value == ""
    assert view._deepgram_key.value == ""
    assert view._soniox_key.value == ""


def test_restore_api_key_icons_sets_idle_success_error(monkeypatch: pytest.MonkeyPatch) -> None:
    settings = AppSettings()
    settings.api_key_verified.deepgram = True
    settings.api_key_verified.google = False

    view, _ = _make_settings_view(monkeypatch)
    view._deepgram_key.value = "deepgram-secret"
    view._google_key.value = "google-secret"
    view._soniox_key.value = ""
    view._alibaba_key_beijing.value = ""
    view._alibaba_key_singapore.value = ""

    view._restore_api_key_icons(settings)

    assert view._deepgram_key._current_status == "success"
    assert view._deepgram_key._last_verified_hash
    assert view._google_key._current_status == "error"
    assert view._soniox_key._current_status == "idle"


def test_update_api_visibility_tracks_provider_and_region(monkeypatch: pytest.MonkeyPatch) -> None:
    settings = AppSettings()
    settings.provider.stt = STTProviderName.QWEN_ASR
    settings.provider.llm = LLMProviderName.GEMINI
    settings.qwen.region = QwenRegion.BEIJING

    view, _ = _make_settings_view(monkeypatch)
    view._settings = settings
    view._update_api_visibility()

    assert view._qwen_region_btn.visible is True
    assert view._google_key.visible is True
    assert view._alibaba_key_beijing.visible is True
    assert view._alibaba_key_singapore.visible is False

    settings.qwen.region = QwenRegion.SINGAPORE
    settings.provider.llm = LLMProviderName.QWEN
    view._update_api_visibility()

    assert view._google_key.visible is False
    assert view._alibaba_key_beijing.visible is False
    assert view._alibaba_key_singapore.visible is True


def test_on_stt_selected_updates_provider_and_pipeline_flags(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = AppSettings()
    changed: list[AppSettings] = []
    view, _ = _make_settings_view(monkeypatch)
    view.load_from_settings(settings, config_path=Path("settings.json"))
    view.on_settings_changed = lambda incoming: changed.append(incoming)

    view._on_stt_selected(STTProviderName.SONIOX.value)

    assert settings.provider.stt == STTProviderName.SONIOX
    assert view.has_provider_changes is True
    assert view.provider_change_requires_pipeline is True
    assert changed == [settings]


def test_on_llm_selected_updates_model_and_prompt_state(monkeypatch: pytest.MonkeyPatch) -> None:
    settings = AppSettings()
    settings.provider.llm = LLMProviderName.GEMINI
    settings.system_prompts = {"gemini": "G", "qwen": "Q"}
    settings.system_prompt = "G"

    view, _ = _make_settings_view(monkeypatch)
    view.load_from_settings(settings, config_path=Path("settings.json"))
    view._on_llm_selected(QwenLLMModel.QWEN_35_PLUS.value)

    assert settings.provider.llm == LLMProviderName.QWEN
    assert settings.qwen.llm_model == QwenLLMModel.QWEN_35_PLUS
    assert view._prompt_editor.value == "Q"
    assert settings.system_prompt == "Q"

    view._on_llm_selected(QwenLLMModel.QWEN_35_PLUS.value)
    assert view.has_provider_changes is False


def test_on_ui_and_region_selection_emit_changes(monkeypatch: pytest.MonkeyPatch) -> None:
    settings = AppSettings()
    changed: list[AppSettings] = []
    view, _ = _make_settings_view(monkeypatch)
    view.load_from_settings(settings, config_path=Path("settings.json"))
    view.on_settings_changed = lambda incoming: changed.append(incoming)

    view._on_ui_selected("ko")
    view._on_qwen_region_selected(QwenRegion.SINGAPORE.value)

    assert settings.ui.locale == "ko"
    assert settings.qwen.region == QwenRegion.SINGAPORE
    assert view.has_provider_changes is True
    assert view.provider_change_requires_pipeline is True
    assert len(changed) == 2


def test_on_secret_change_saves_and_clears_keys(monkeypatch: pytest.MonkeyPatch) -> None:
    settings = AppSettings()
    store = DummySecretStore()
    cleared: list[str] = []
    view, _ = _make_settings_view(monkeypatch, store)
    view.load_from_settings(settings, config_path=Path("settings.json"))
    view.on_secret_cleared = lambda key: cleared.append(key)

    view._on_secret_change("google_api_key", "abc")
    view._on_secret_change("google_api_key", "")

    assert store.values.get("google_api_key") is None
    assert store.set_calls == [("google_api_key", "abc")]
    assert store.delete_calls == ["google_api_key"]
    assert cleared == ["google_api_key"]


def test_audio_vad_and_low_latency_handlers_update_state(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = AppSettings()
    view, _ = _make_settings_view(monkeypatch)
    view.load_from_settings(settings, config_path=Path("settings.json"))

    view._audio_settings.host_api = "MME"
    view._audio_settings.microphone = "Mic 2"
    view._on_audio_change()

    visual_event = SimpleNamespace(control=SimpleNamespace(value=0.72))
    monkeypatch.setattr(type(view._vad_slider), "update", lambda self: None)
    view._handle_vad_visual_change(visual_event)
    view._handle_vad_change(visual_event)
    view._on_low_latency_selected("on")

    assert settings.audio.input_host_api == "MME"
    assert settings.audio.input_device == "Mic 2"
    assert settings.stt.vad_speech_threshold == 0.72
    assert settings.stt.low_latency_mode is True
    assert view._low_latency_text.content.value == t("toggle.on")


@pytest.mark.asyncio
async def test_prompt_verify_and_emit_helpers(monkeypatch: pytest.MonkeyPatch) -> None:
    settings = AppSettings()
    changed: list[AppSettings] = []
    view, _ = _make_settings_view(monkeypatch)
    view.load_from_settings(settings, config_path=Path("settings.json"))
    view.on_settings_changed = lambda incoming: changed.append(incoming)

    view._on_prompt_change("custom prompt")
    assert settings.system_prompt == "custom prompt"
    assert settings.system_prompts[view._active_prompt_key()] == "custom prompt"

    view._on_reset_prompt(None)
    assert settings.system_prompt == view._prompt_editor.value
    assert changed

    unavailable = await view._verify_key("google", "abc")
    assert unavailable == (False, "Verification not available")

    async def fake_verify(provider: str, key: str) -> tuple[bool, str]:
        return provider == "google", key

    view.on_verify_api_key = fake_verify
    available = await view._verify_key("google", "abc")
    assert available == (True, "abc")


def test_apply_locale_and_refresh_prompt_if_empty(monkeypatch: pytest.MonkeyPatch) -> None:
    settings = AppSettings()
    view, _ = _make_settings_view(monkeypatch)
    view.load_from_settings(settings, config_path=Path("settings.json"))

    view._prompt_editor.value = ""
    view.apply_locale()
    view.refresh_prompt_if_empty()

    assert view._stt_title.value == t("settings.section.stt")
    assert view._reset_prompt_btn.text == t("settings.reset_prompt")
    assert bool(view._prompt_editor.value.strip())
