from __future__ import annotations

import logging
from pathlib import Path
from types import SimpleNamespace

import pytest

pytest.importorskip("flet")

from puripuly_heart.config.settings import (
    AppSettings,
    GeminiLLMModel,
    LLMProviderName,
    QwenLLMModel,
    QwenRegion,
    STTProviderName,
)
from puripuly_heart.ui import i18n as i18n_module
from puripuly_heart.ui.i18n import t
from puripuly_heart.ui.overlay_calibration import OverlayCalibration
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


def test_update_api_visibility_hides_secret_fields_for_local_qwen(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = AppSettings()
    settings.provider.stt = STTProviderName.LOCAL_QWEN
    settings.provider.llm = LLMProviderName.GEMINI

    view, _ = _make_settings_view(monkeypatch)
    view._settings = settings
    view._update_api_visibility()

    assert view._deepgram_key.visible is False
    assert view._soniox_key.visible is False
    assert view._qwen_region_btn.visible is False
    assert view._alibaba_key_beijing.visible is False
    assert view._alibaba_key_singapore.visible is False
    assert view._google_key.visible is True


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


def test_on_peer_stt_selected_updates_provider_and_pipeline_flags(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = AppSettings()
    changed: list[AppSettings] = []
    view, _ = _make_settings_view(monkeypatch)
    view.load_from_settings(settings, config_path=Path("settings.json"))
    view.on_settings_changed = lambda incoming: changed.append(incoming)

    view._on_peer_stt_selected(STTProviderName.SONIOX.value)

    assert settings.provider.peer_stt == STTProviderName.SONIOX
    assert view.has_provider_changes is True
    assert view.provider_change_requires_pipeline is True
    assert changed == [settings]


def test_on_overlay_selected_uses_dedicated_overlay_toggle_callback(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = AppSettings()
    overlay_calls: list[bool] = []
    settings_calls: list[AppSettings] = []

    view, _ = _make_settings_view(monkeypatch)
    view.load_from_settings(settings, config_path=Path("settings.json"))
    view.on_overlay_toggle = lambda enabled: overlay_calls.append(enabled)
    view.on_settings_changed = lambda incoming: settings_calls.append(incoming)

    view._on_overlay_selected("on")

    assert overlay_calls == [True]
    assert settings_calls == []
    assert settings.ui.overlay_enabled is True


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


def test_on_llm_selected_updates_gemini_model(monkeypatch: pytest.MonkeyPatch) -> None:
    settings = AppSettings()
    settings.provider.llm = LLMProviderName.GEMINI
    settings.gemini.llm_model = GeminiLLMModel.GEMINI_3_FLASH
    settings.system_prompts = {"gemini": "G", "qwen": "Q"}
    settings.system_prompt = "G"

    view, _ = _make_settings_view(monkeypatch)
    view.load_from_settings(settings, config_path=Path("settings.json"))
    view._on_llm_selected(GeminiLLMModel.GEMINI_31_FLASH_LITE.value)

    assert settings.provider.llm == LLMProviderName.GEMINI
    assert settings.gemini.llm_model == GeminiLLMModel.GEMINI_31_FLASH_LITE
    assert view._prompt_editor.value == "G"
    assert settings.system_prompt == "G"
    assert view.has_provider_changes is True


def test_on_llm_selected_logs_only_changed_fields_for_provider_switch(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    settings = AppSettings()
    settings.provider.llm = LLMProviderName.GEMINI
    settings.gemini.llm_model = GeminiLLMModel.GEMINI_31_FLASH_LITE
    settings.qwen.llm_model = QwenLLMModel.QWEN_35_PLUS

    view, _ = _make_settings_view(monkeypatch)
    view.load_from_settings(settings, config_path=Path("settings.json"))

    with caplog.at_level(logging.INFO, logger="puripuly_heart.ui.views.settings"):
        view._on_llm_selected(QwenLLMModel.QWEN_35_PLUS.value)

    message = caplog.messages[-1]
    assert "[Settings] LLM selection changed:" in message
    assert "provider=gemini->qwen" in message
    assert "gemini_model=" not in message
    assert "qwen_model=" not in message


def test_on_llm_selected_skips_log_when_selection_is_unchanged(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    settings = AppSettings()
    settings.provider.llm = LLMProviderName.QWEN
    settings.qwen.llm_model = QwenLLMModel.QWEN_35_PLUS

    view, _ = _make_settings_view(monkeypatch)
    view.load_from_settings(settings, config_path=Path("settings.json"))

    with caplog.at_level(logging.INFO, logger="puripuly_heart.ui.views.settings"):
        view._on_llm_selected(QwenLLMModel.QWEN_35_PLUS.value)

    assert not any("LLM selection changed" in message for message in caplog.messages)


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


def test_overlay_controls_gate_peer_translation_until_overlay_is_connected(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = AppSettings()
    view, _ = _make_settings_view(monkeypatch)
    view.load_from_settings(settings, config_path=Path("settings.json"))

    assert view._peer_translation_button.disabled is True
    assert view._peer_translation_hint.value == t(
        "settings.peer_translation.disabled.overlay_required"
    )

    view.set_overlay_runtime_state("connected")

    assert view._peer_translation_button.disabled is False
    assert view._peer_translation_hint.value == ""


def test_peer_qwen_region_control_is_visible_before_peer_translation_is_enabled(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = AppSettings()
    settings.provider.peer_stt = STTProviderName.QWEN_ASR
    settings.ui.peer_translation_enabled = False

    view, _ = _make_settings_view(monkeypatch)
    view.load_from_settings(settings, config_path=Path("settings.json"))

    assert view._peer_qwen_region_text.visible is True
    assert view._alibaba_key_beijing.visible is False
    assert view._alibaba_key_singapore.visible is False


def test_peer_qwen_region_override_can_be_cleared_back_to_inherited_none(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = AppSettings()
    settings.provider.peer_stt = STTProviderName.QWEN_ASR
    settings.peer_qwen_asr_stt.region = QwenRegion.BEIJING
    view, _ = _make_settings_view(monkeypatch)
    view.load_from_settings(settings, config_path=Path("settings.json"))
    view.page = object()

    region_updates: list[str] = []
    api_key_updates: list[str] = []
    monkeypatch.setattr(
        type(view._peer_qwen_region_text),
        "update",
        lambda self: region_updates.append("peer_qwen_region_text"),
    )
    monkeypatch.setattr(
        type(view._api_keys_column),
        "update",
        lambda self: api_key_updates.append("api_keys_column"),
    )

    view._on_peer_qwen_region_selected("")

    assert settings.peer_qwen_asr_stt.region is None
    assert view._peer_qwen_region_text.content.value == t("settings.peer_provider.follow_self")
    assert region_updates == ["peer_qwen_region_text"]
    assert api_key_updates == ["api_keys_column"]


def test_peer_soniox_model_override_can_be_cleared_back_to_inherited_none(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = AppSettings()
    settings.provider.peer_stt = STTProviderName.SONIOX
    settings.peer_soniox_stt.model = "stt-rt-v4"
    view, _ = _make_settings_view(monkeypatch)
    view.load_from_settings(settings, config_path=Path("settings.json"))
    view.page = object()

    model_updates: list[str] = []
    monkeypatch.setattr(
        type(view._peer_soniox_model_text),
        "update",
        lambda self: model_updates.append("peer_soniox_model_text"),
    )

    view._on_peer_soniox_model_selected("")

    assert settings.peer_soniox_stt.model is None
    assert view._peer_soniox_model_text.content.value == t("settings.peer_provider.follow_self")
    assert model_updates == ["peer_soniox_model_text"]


def test_update_api_visibility_includes_enabled_peer_provider(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = AppSettings()
    settings.provider.stt = STTProviderName.LOCAL_QWEN
    settings.provider.peer_stt = STTProviderName.DEEPGRAM
    settings.provider.llm = LLMProviderName.GEMINI
    settings.ui.peer_translation_enabled = True

    view, _ = _make_settings_view(monkeypatch)
    view._settings = settings
    view._update_api_visibility()

    assert view._deepgram_key.visible is True
    assert view._google_key.visible is True


def test_update_api_visibility_shows_both_qwen_region_keys_when_self_and_peer_differ(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = AppSettings()
    settings.provider.stt = STTProviderName.QWEN_ASR
    settings.provider.peer_stt = STTProviderName.QWEN_ASR
    settings.ui.peer_translation_enabled = True
    settings.qwen.region = QwenRegion.BEIJING
    settings.peer_qwen_asr_stt.region = QwenRegion.SINGAPORE

    view, _ = _make_settings_view(monkeypatch)
    view._settings = settings
    view._update_api_visibility()

    assert view._alibaba_key_beijing.visible is True
    assert view._alibaba_key_singapore.visible is True


def test_on_peer_stt_selected_refreshes_api_visibility_and_redraws_immediately(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = AppSettings()
    settings.provider.stt = STTProviderName.LOCAL_QWEN
    settings.provider.peer_stt = STTProviderName.DEEPGRAM
    settings.ui.peer_translation_enabled = True

    view, _ = _make_settings_view(monkeypatch)
    view.load_from_settings(settings, config_path=Path("settings.json"))
    view.page = object()

    api_key_updates: list[str] = []
    monkeypatch.setattr(
        type(view._peer_stt_text),
        "update",
        lambda self: api_key_updates.append("peer_stt_text"),
    )
    monkeypatch.setattr(
        type(view._api_keys_column),
        "update",
        lambda self: api_key_updates.append("api_keys_column"),
    )

    view._on_peer_stt_selected(STTProviderName.SONIOX.value)

    assert settings.provider.peer_stt == STTProviderName.SONIOX
    assert view._peer_stt_text.content.value == t("provider.soniox")
    assert api_key_updates == ["peer_stt_text", "api_keys_column"]


def test_on_peer_translation_selected_refreshes_api_visibility_when_enabled(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = AppSettings()
    settings.provider.stt = STTProviderName.LOCAL_QWEN
    settings.provider.peer_stt = STTProviderName.SONIOX
    settings.ui.peer_translation_enabled = False

    view, _ = _make_settings_view(monkeypatch)
    view.load_from_settings(settings, config_path=Path("settings.json"))
    view.set_overlay_runtime_state("connected")
    view.page = object()

    api_key_updates: list[str] = []
    monkeypatch.setattr(
        type(view._api_keys_column),
        "update",
        lambda self: api_key_updates.append("api_keys_column"),
    )

    view._on_peer_translation_selected("on")

    assert settings.ui.peer_translation_enabled is True
    assert view._soniox_key.visible is True
    assert api_key_updates == ["api_keys_column"]


def test_on_overlay_selected_refreshes_api_visibility_when_disabled(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = AppSettings()
    settings.provider.stt = STTProviderName.LOCAL_QWEN
    settings.provider.peer_stt = STTProviderName.DEEPGRAM
    settings.ui.overlay_enabled = True
    settings.ui.peer_translation_enabled = True

    view, _ = _make_settings_view(monkeypatch)
    view.load_from_settings(settings, config_path=Path("settings.json"))
    view.page = object()

    api_key_updates: list[str] = []
    monkeypatch.setattr(
        type(view._api_keys_column),
        "update",
        lambda self: api_key_updates.append("api_keys_column"),
    )

    view._on_overlay_selected("off")

    assert settings.ui.overlay_enabled is False
    assert settings.ui.peer_translation_enabled is False
    assert view._deepgram_key.visible is False
    assert api_key_updates == ["api_keys_column"]


def test_peer_provider_labels_are_backed_by_i18n(monkeypatch: pytest.MonkeyPatch) -> None:
    settings = AppSettings()
    view, _ = _make_settings_view(monkeypatch)
    view.load_from_settings(settings, config_path=Path("settings.json"))

    assert view._peer_stt_label.value == t("settings.peer_stt_provider")
    assert view._peer_qwen_region_label.value == t("settings.peer_qwen_region")


def test_overlay_display_toggles_update_persistent_settings(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = AppSettings()
    settings_calls: list[AppSettings] = []

    view, _ = _make_settings_view(monkeypatch)
    view.load_from_settings(settings, config_path=Path("settings.json"))
    view.on_settings_changed = lambda incoming: settings_calls.append(incoming)

    view._on_overlay_translation_selected("off")
    view._on_overlay_peer_original_selected("off")

    assert settings.ui.show_overlay_translation is False
    assert settings.ui.show_overlay_peer_original is False
    assert settings_calls == [settings, settings]


def test_first_peer_translation_enable_bootstraps_integrated_context_once(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = AppSettings()
    settings.ui.integrated_context_enabled = False
    settings.ui.integrated_context_bootstrapped = False

    view, _ = _make_settings_view(monkeypatch)
    view.load_from_settings(settings, config_path=Path("settings.json"))
    view.set_overlay_runtime_state("connected")

    view._on_peer_translation_selected("on")

    assert settings.ui.peer_translation_enabled is True
    assert settings.ui.integrated_context_enabled is True
    assert settings.ui.integrated_context_bootstrapped is True


def test_peer_translation_toggle_restores_saved_integrated_context_preference(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = AppSettings()

    view, _ = _make_settings_view(monkeypatch)
    view.load_from_settings(settings, config_path=Path("settings.json"))
    view.set_overlay_runtime_state("connected")

    view._on_peer_translation_selected("on")
    view._on_integrated_context_selected("off")
    assert settings.ui.integrated_context_enabled is False

    view._on_peer_translation_selected("off")
    assert view._integrated_context_button.disabled is True

    view._on_peer_translation_selected("on")

    assert settings.ui.peer_translation_enabled is True
    assert settings.ui.integrated_context_enabled is False


def test_audio_change_updates_desktop_loopback_controls(monkeypatch: pytest.MonkeyPatch) -> None:
    settings = AppSettings()
    changed: list[AppSettings] = []
    view, _ = _make_settings_view(monkeypatch)
    view.load_from_settings(settings, config_path=Path("settings.json"))
    view.on_settings_changed = lambda incoming: changed.append(incoming)

    view._audio_settings.desktop_output_device = "Speakers (Loopback)"
    view._audio_settings.desktop_vad_threshold = 0.72
    view._audio_settings.desktop_hangover_ms = 950
    view._audio_settings.desktop_pre_roll_ms = 420
    view._on_audio_change()

    assert settings.desktop_audio.output_device == "Speakers (Loopback)"
    assert settings.desktop_audio.vad_speech_threshold == 0.72
    assert settings.desktop_audio.vad_hangover_ms == 950
    assert settings.desktop_audio.vad_pre_roll_ms == 420
    assert changed == [settings]


@pytest.mark.parametrize("locale", ["en", "ko", "zh-CN"])
def test_overlay_failure_reason_keys_are_localized(locale: str) -> None:
    bundle = i18n_module._load_bundle(locale)

    assert bundle["settings.overlay.failure.missing_executable"]
    assert bundle["settings.overlay.failure.runtime_crashed"]
    assert bundle["settings.overlay.failure.stale_overlay_build"]
    assert bundle["settings.overlay.failure.steamvr_not_installed"]
    assert bundle["settings.overlay.failure.steamvr_not_running"]
    assert bundle["settings.overlay.failure.hmd_not_found"]
    assert bundle["settings.overlay.show_translation"]
    assert bundle["settings.overlay.show_peer_original"]


def test_overlay_calibration_controls_are_localized(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    view, _ = _make_settings_view(monkeypatch)
    view.load_from_settings(AppSettings(), config_path=Path("settings.json"))

    assert view._overlay_calibration_title.value == t("settings.overlay.calibration")
    assert view._overlay_anchor_label.value == t("settings.overlay.calibration.anchor")
    assert view._overlay_translation_label.value == t("settings.overlay.show_translation")
    assert view._overlay_peer_original_label.value == t("settings.overlay.show_peer_original")
    assert view._overlay_calibration_apply_button.text == t("settings.overlay.calibration.apply")
    assert view._overlay_calibration_cancel_button.text == t("settings.overlay.calibration.cancel")
    assert view._overlay_calibration_reset_button.text == t("settings.overlay.calibration.reset")


def test_overlay_calibration_controls_follow_local_apply_cancel_contract(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    view, _ = _make_settings_view(monkeypatch)
    view.load_from_settings(AppSettings(), config_path=Path("settings.json"))
    default_distance = view._format_overlay_calibration_number(OverlayCalibration().distance)

    view._overlay_distance_field.value = "1.20"
    view._on_overlay_calibration_numeric_blur(
        "distance",
        SimpleNamespace(control=view._overlay_distance_field),
    )
    view._on_overlay_calibration_cancel(None)

    assert view._overlay_distance_field.value == default_distance

    view._overlay_distance_field.value = "1.20"
    view._on_overlay_calibration_numeric_blur(
        "distance",
        SimpleNamespace(control=view._overlay_distance_field),
    )
    view._on_overlay_calibration_apply(None)

    assert view._overlay_distance_field.value == "1.20"


def test_overlay_calibration_apply_commits_current_field_values_without_blur(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    view, _ = _make_settings_view(monkeypatch)
    view.load_from_settings(AppSettings(), config_path=Path("settings.json"))

    view._overlay_distance_field.value = "1.20"
    view._on_overlay_calibration_apply(None)

    assert view._overlay_distance_field.value == "1.20"


def test_overlay_calibration_hides_background_alpha_control(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    view, _ = _make_settings_view(monkeypatch)
    view.load_from_settings(AppSettings(), config_path=Path("settings.json"))

    assert not hasattr(view, "_overlay_background_alpha_field")
    assert not hasattr(view, "_overlay_background_alpha_label")


def test_overlay_calibration_reset_restores_defaults_until_apply(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = AppSettings()
    settings.overlay_calibration.distance = 1.2
    settings.overlay_calibration.offset_y = 0.5
    view, _ = _make_settings_view(monkeypatch)
    view.load_from_settings(settings, config_path=Path("settings.json"))
    view.set_overlay_calibration(settings.overlay_calibration)

    defaults = OverlayCalibration()

    view._on_overlay_calibration_reset(None)

    assert view._overlay_distance_field.value == view._format_overlay_calibration_number(
        defaults.distance
    )
    assert view._overlay_offset_y_field.value == view._format_overlay_calibration_number(
        defaults.offset_y
    )

    view._on_overlay_calibration_cancel(None)

    assert view._overlay_distance_field.value == "1.20"
    assert view._overlay_offset_y_field.value == "0.50"

    view._on_overlay_calibration_reset(None)
    view._on_overlay_calibration_apply(None)

    assert view._overlay_distance_field.value == view._format_overlay_calibration_number(
        defaults.distance
    )
    assert view._overlay_offset_y_field.value == view._format_overlay_calibration_number(
        defaults.offset_y
    )


def test_overlay_calibration_section_uses_dedicated_row_card(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    view, _ = _make_settings_view(monkeypatch)

    assert len(view.controls) == 9

    row5 = view.controls[4]
    overlay_column = row5.content.controls[1].content.controls[1].content.content
    assert view._overlay_calibration_title not in overlay_column.controls

    row6 = view.controls[6]
    calibration_column = row6.content.controls[1].content.content
    assert calibration_column.controls[0] is view._overlay_calibration_title
    assert view._overlay_calibration_apply_button in calibration_column.controls[-1].controls
    assert view._overlay_calibration_cancel_button in calibration_column.controls[-1].controls
    assert view._overlay_calibration_reset_button in calibration_column.controls[-1].controls


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


def test_apply_locale_refreshes_peer_labels_and_inherit_texts(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = AppSettings()
    settings.ui.locale = "ko"
    settings.provider.peer_stt = STTProviderName.QWEN_ASR
    settings.peer_qwen_asr_stt.region = None
    settings.peer_qwen_asr_stt.model = None
    settings.peer_soniox_stt.model = None

    view, _ = _make_settings_view(monkeypatch)
    view.load_from_settings(settings, config_path=Path("settings.json"))

    old_locale = i18n_module.get_locale()
    expected_peer_stt_label = ""
    expected_peer_deepgram_model_label = ""
    expected_peer_qwen_region_label = ""
    expected_peer_qwen_model_label = ""
    expected_peer_soniox_model_label = ""
    expected_inherit_label = ""
    try:
        i18n_module.set_locale("ko")
        view._peer_stt_label.value = "stale"
        view._peer_deepgram_model_label.value = "stale"
        view._peer_qwen_region_label.value = "stale"
        view._peer_qwen_model_label.value = "stale"
        view._peer_soniox_model_label.value = "stale"
        view._peer_deepgram_model_text.content.value = "stale"
        view._peer_qwen_region_text.content.value = "stale"
        view._peer_qwen_model_text.content.value = "stale"
        view._peer_soniox_model_text.content.value = "stale"

        view.apply_locale()
        expected_peer_stt_label = t("settings.peer_stt_provider")
        expected_peer_deepgram_model_label = t("settings.peer_deepgram_model")
        expected_peer_qwen_region_label = t("settings.peer_qwen_region")
        expected_peer_qwen_model_label = t("settings.peer_qwen_model")
        expected_peer_soniox_model_label = t("settings.peer_soniox_model")
        expected_inherit_label = t("settings.peer_provider.follow_self")
    finally:
        i18n_module.set_locale(old_locale)

    assert view._peer_stt_label.value == expected_peer_stt_label
    assert view._peer_deepgram_model_label.value == expected_peer_deepgram_model_label
    assert view._peer_qwen_region_label.value == expected_peer_qwen_region_label
    assert view._peer_qwen_model_label.value == expected_peer_qwen_model_label
    assert view._peer_soniox_model_label.value == expected_peer_soniox_model_label
    assert view._peer_deepgram_model_text.content.value == expected_inherit_label
    assert view._peer_qwen_region_text.content.value == expected_inherit_label
    assert view._peer_qwen_model_text.content.value == expected_inherit_label
    assert view._peer_soniox_model_text.content.value == expected_inherit_label


def test_load_from_settings_updates_vrc_mic_toggle_label(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = AppSettings()
    view, _ = _make_settings_view(monkeypatch)

    settings.osc.vrc_mic_intercept = True
    view.load_from_settings(settings, config_path=Path("settings.json"))
    assert view._vrc_mic_text.content.value == t("settings.vrc_mic.on")

    settings.osc.vrc_mic_intercept = False
    view.load_from_settings(settings, config_path=Path("settings.json"))
    assert view._vrc_mic_text.content.value == t("settings.vrc_mic.off")


def test_on_vrc_mic_click_returns_when_page_is_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    settings = AppSettings()
    view, _ = _make_settings_view(monkeypatch)
    view.load_from_settings(settings, config_path=Path("settings.json"))
    modal_calls: list[str] = []

    class DummyModal:
        def __init__(self, *_args, **_kwargs) -> None:
            modal_calls.append("created")

    monkeypatch.setattr(settings_view, "SettingsModal", DummyModal)

    view._on_vrc_mic_click(None)

    assert modal_calls == []


def test_on_vrc_mic_click_opens_modal_with_current_selection(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = AppSettings()
    settings.osc.vrc_mic_intercept = True
    view, _ = _make_settings_view(monkeypatch)
    view.load_from_settings(settings, config_path=Path("settings.json"))
    view.page = object()

    captured: dict[str, object] = {}

    class DummyModal:
        def __init__(self, _page, title, options, _on_select, *, show_description=False):
            captured["title"] = title
            captured["options"] = options
            captured["show_description"] = show_description

        def open(self, current: str) -> None:
            captured["current"] = current

    monkeypatch.setattr(settings_view, "SettingsModal", DummyModal)

    view._on_vrc_mic_click(None)

    options = captured["options"]
    assert captured["title"] == t("settings.vrc_mic_intercept")
    assert captured["show_description"] is True
    assert [option.value for option in options] == ["on", "off"]
    assert [option.label for option in options] == [
        t("settings.vrc_mic.on"),
        t("settings.vrc_mic.off"),
    ]
    assert captured["current"] == "on"


def test_on_vrc_mic_selected_updates_setting_label_and_emits_change(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = AppSettings()
    changed: list[AppSettings] = []
    view, _ = _make_settings_view(monkeypatch)
    view.load_from_settings(settings, config_path=Path("settings.json"))
    view.on_settings_changed = lambda incoming: changed.append(incoming)
    view.page = object()
    monkeypatch.setattr(type(view._vrc_mic_text), "update", lambda self: None)

    view._on_vrc_mic_selected("on")

    assert settings.osc.vrc_mic_intercept is True
    assert view._vrc_mic_text.content.value == t("settings.vrc_mic.on")
    assert changed == [settings]


def test_on_vrc_mic_selected_without_settings_returns_early(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    changed: list[AppSettings] = []
    view, _ = _make_settings_view(monkeypatch)
    view.on_settings_changed = lambda incoming: changed.append(incoming)

    view._on_vrc_mic_selected("on")

    assert changed == []


def test_apply_locale_refreshes_vrc_mic_title_and_value(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = AppSettings()
    settings.osc.vrc_mic_intercept = True
    view, _ = _make_settings_view(monkeypatch)
    view.load_from_settings(settings, config_path=Path("settings.json"))
    view._vrc_mic_title.value = "stale-title"
    view._vrc_mic_text.content.value = "stale-value"

    view.apply_locale()

    assert view._vrc_mic_title.value == t("settings.vrc_mic_intercept")
    assert view._vrc_mic_text.content.value == t("settings.vrc_mic.on")


def test_custom_vocabulary_loads_current_source_language_bucket(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = AppSettings()
    settings.provider.stt = STTProviderName.SONIOX
    settings.languages.source_language = "ko"
    settings.stt.custom_vocabulary_enabled = True
    settings.stt.custom_terms = {"ko": ["Puripuly", "VRChat"], "en": ["Avatar"]}

    view, _ = _make_settings_view(monkeypatch)
    view.load_from_settings(settings, config_path=Path("settings.json"))

    assert view._custom_vocab_terms.value == "Puripuly\nVRChat"
    assert view._custom_vocab_terms.helper_text == ""
    assert view._custom_vocab_terms.shift_enter is False
    assert view._custom_vocab_terms.label is None
    assert view._custom_vocab_terms.border_color == settings_view.COLOR_DIVIDER


def test_custom_vocabulary_loads_seeded_settings_defaults_as_initial_value(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = AppSettings()
    settings.languages.source_language = "ko"

    view, _ = _make_settings_view(monkeypatch)
    view.load_from_settings(settings, config_path=Path("settings.json"))

    assert view._custom_vocab_terms.value == "아이리\n시나노"
    assert view._custom_vocab_terms.helper_text == ""


def test_custom_vocabulary_loads_seeded_settings_defaults_for_zh_cn(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = AppSettings()
    settings.languages.source_language = "zh-CN"

    view, _ = _make_settings_view(monkeypatch)
    view.load_from_settings(settings, config_path=Path("settings.json"))

    assert view._custom_vocab_terms.value == "airi\nshinano"
    assert view._custom_vocab_terms.helper_text == ""


def test_custom_vocabulary_info_icon_is_in_card_header(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    view, _ = _make_settings_view(monkeypatch)

    row7 = view.controls[-1]
    custom_vocab_column = row7.content.controls[1].content.content
    header = custom_vocab_column.controls[0]

    assert isinstance(header, settings_view.ft.Row)
    assert header.controls[0] is view._custom_vocab_title
    assert header.controls[-1] is view._custom_vocab_info_icon
    assert view._custom_vocab_info_icon.tooltip == t("settings.custom_vocabulary_tooltip")


def test_custom_vocabulary_switching_source_language_updates_editor_payload(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = AppSettings()
    settings.languages.source_language = "ko"
    settings.stt.custom_terms = {"ko": ["Puripuly"], "en": ["Avatar", "OSC"]}

    view, _ = _make_settings_view(monkeypatch)
    view.load_from_settings(settings, config_path=Path("settings.json"))
    assert view._custom_vocab_terms.value == "Puripuly"

    settings.languages.source_language = "en"
    view.load_from_settings(settings, config_path=Path("settings.json"))

    assert view._custom_vocab_terms.value == "Avatar\nOSC"


def test_custom_vocabulary_preserves_unsaved_drafts_across_source_language_reload(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = AppSettings()
    settings.languages.source_language = "ko"
    settings.stt.custom_terms = {"ko": ["Puripuly"], "en": ["Avatar"]}

    view, _ = _make_settings_view(monkeypatch)
    view.load_from_settings(settings, config_path=Path("settings.json"))

    view._custom_vocab_terms.value = "Puripuly\nVRChat"
    view._on_custom_vocabulary_terms_change(None)

    settings.languages.source_language = "en"
    view.load_from_settings(
        settings,
        config_path=Path("settings.json"),
        preserve_custom_vocab_draft=True,
    )
    assert view._custom_vocab_terms.value == "Avatar"

    settings.languages.source_language = "ko"
    view.load_from_settings(
        settings,
        config_path=Path("settings.json"),
        preserve_custom_vocab_draft=True,
    )

    assert view._custom_vocab_terms.value == "Puripuly\nVRChat"


def test_custom_vocabulary_default_load_refreshes_from_settings(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = AppSettings()
    settings.languages.source_language = "ko"
    settings.stt.custom_terms = {"ko": ["Puripuly"]}

    view, _ = _make_settings_view(monkeypatch)
    view.load_from_settings(settings, config_path=Path("settings.json"))

    view._custom_vocab_terms.value = "Puripuly\nVRChat"
    view._on_custom_vocabulary_terms_change(None)

    view.load_from_settings(settings, config_path=Path("settings.json"))

    assert view._custom_vocab_terms.value == "Puripuly"


def test_custom_vocabulary_apply_empty_terms_preserves_intentional_empty_bucket(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = AppSettings()
    settings.languages.source_language = "ko"
    settings.stt.custom_terms = {"ko": ["Puripuly"]}

    view, _ = _make_settings_view(monkeypatch)
    view.load_from_settings(settings, config_path=Path("settings.json"))

    view._custom_vocab_terms.value = ""
    view._on_custom_vocabulary_terms_change(None)
    view._on_custom_vocabulary_terms_blur(None)
    view.load_from_settings(settings, config_path=Path("settings.json"))

    assert settings.stt.custom_terms == {"ko": []}
    assert settings.stt.custom_vocabulary_enabled is False
    assert view._custom_vocab_terms.value == ""


def test_custom_vocabulary_typing_does_not_emit_or_persist(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = AppSettings()
    settings.languages.source_language = "ko"
    settings.stt.custom_terms = {"ko": ["Puripuly"], "en": ["Avatar"]}
    changed: list[AppSettings] = []

    view, _ = _make_settings_view(monkeypatch)
    view.load_from_settings(settings, config_path=Path("settings.json"))
    view.on_settings_changed = lambda incoming: changed.append(incoming)

    view._custom_vocab_terms.value = "Puripuly\nVRChat"
    view._on_custom_vocabulary_terms_change(None)

    assert changed == []
    assert settings.stt.custom_terms == {"ko": ["Puripuly"], "en": ["Avatar"]}
    assert view._custom_vocab_terms.value == "Puripuly\nVRChat"


def test_custom_vocabulary_blur_applies_updates_current_bucket_and_emits_once(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = AppSettings()
    settings.languages.source_language = "ko"
    settings.stt.custom_terms = {"ko": ["Puripuly"], "en": ["Avatar"]}
    changed: list[AppSettings] = []

    view, _ = _make_settings_view(monkeypatch)
    view.load_from_settings(settings, config_path=Path("settings.json"))
    view.on_settings_changed = lambda incoming: changed.append(incoming)

    view._custom_vocab_terms.value = " Puripuly \nVRChat\n\nPuripuly "
    view._on_custom_vocabulary_terms_change(None)
    view._on_custom_vocabulary_terms_blur(None)

    assert settings.stt.custom_vocabulary_enabled is True
    assert settings.stt.custom_terms == {
        "ko": ["Puripuly", "VRChat"],
        "en": ["Avatar"],
    }
    assert view._custom_vocab_terms.value == "Puripuly\nVRChat"
    assert changed == [settings]


def test_custom_vocabulary_blur_updates_only_current_bucket_and_emits_once(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = AppSettings()
    settings.languages.source_language = "ko"
    settings.stt.custom_terms = {"ko": ["Puripuly"], "en": ["Avatar"]}
    changed: list[AppSettings] = []

    view, _ = _make_settings_view(monkeypatch)
    view.load_from_settings(settings, config_path=Path("settings.json"))
    view.on_settings_changed = lambda incoming: changed.append(incoming)

    view._custom_vocab_terms.value = " Puripuly \nVRChat\n\nPuripuly "
    view._on_custom_vocabulary_terms_change(None)
    view._on_custom_vocabulary_terms_blur(None)

    assert settings.stt.custom_vocabulary_enabled is True
    assert settings.stt.custom_terms == {
        "ko": ["Puripuly", "VRChat"],
        "en": ["Avatar"],
    }
    assert changed == [settings]


def test_custom_vocabulary_caps_to_100_terms_and_shows_snackbar(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = AppSettings()
    settings.languages.source_language = "ko"
    changed: list[AppSettings] = []
    snackbars: list[tuple[str, str]] = []
    terms = [f"term-{i:03d}" for i in range(101)]

    view, _ = _make_settings_view(monkeypatch)
    view.load_from_settings(settings, config_path=Path("settings.json"))
    view.on_settings_changed = lambda incoming: changed.append(incoming)
    view.show_snackbar = lambda msg, bg: snackbars.append((msg, bg))

    view._custom_vocab_terms.value = "\n".join(terms)
    view._on_custom_vocabulary_terms_change(None)
    view._on_custom_vocabulary_terms_blur(None)

    assert settings.stt.custom_terms == {
        "ko": terms[:100],
        "en": ["airi", "shinano"],
        "zh-CN": ["airi", "shinano"],
    }
    assert settings.stt.custom_vocabulary_enabled is True
    assert view._custom_vocab_terms.value == "\n".join(terms[:100])
    assert changed == [settings]
    assert snackbars == [
        (t("snackbar.custom_vocabulary_limit", max_terms=100), settings_view.ft.Colors.ORANGE_700)
    ]


def test_custom_vocabulary_blur_logs_applied_state(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    settings = AppSettings()
    settings.languages.source_language = "ko"

    view, _ = _make_settings_view(monkeypatch)
    view.load_from_settings(settings, config_path=Path("settings.json"))
    view._custom_vocab_terms.value = "Puripuly\nVRChat"
    view._on_custom_vocabulary_terms_change(None)

    with caplog.at_level(logging.INFO, logger="puripuly_heart.ui.views.settings"):
        view._on_custom_vocabulary_terms_blur(None)

    assert "[Settings] Custom vocabulary applied: language=ko, terms=2" in caplog.messages


def test_apply_locale_refreshes_custom_vocabulary_text(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = AppSettings()
    settings.provider.stt = STTProviderName.DEEPGRAM
    settings.languages.source_language = "en"

    view, _ = _make_settings_view(monkeypatch)
    view.load_from_settings(settings, config_path=Path("settings.json"))
    view._custom_vocab_title.value = "stale-title"
    view._custom_vocab_terms.label = "stale-label"
    view._custom_vocab_terms.helper_text = "stale-helper"
    view._custom_vocab_info_icon.tooltip = "stale-tooltip"

    view.apply_locale()

    assert view._custom_vocab_title.value == t("settings.section.custom_vocabulary")
    assert view._custom_vocab_terms.label is None
    assert view._custom_vocab_terms.helper_text == ""
    assert view._custom_vocab_info_icon.tooltip == t("settings.custom_vocabulary_tooltip")
