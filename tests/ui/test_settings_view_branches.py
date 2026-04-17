from __future__ import annotations

import logging
from pathlib import Path
from types import SimpleNamespace

import flet as ft
import pytest

pytest.importorskip("flet")

from puripuly_heart.config.settings import (
    AppSettings,
    GeminiLLMModel,
    LLMProviderName,
    OpenRouterCredentialSource,
    OpenRouterFallbackSelectionAlias,
    OpenRouterLLMModel,
    OpenRouterRoutingMode,
    OpenRouterSelectionAlias,
    QwenLLMModel,
    QwenRegion,
    STTProviderName,
)
from puripuly_heart.ui import i18n as i18n_module
from puripuly_heart.ui.components import subtab_shell as subtab_shell_module
from puripuly_heart.ui.components.bottom_nav import BottomNavBar
from puripuly_heart.ui.i18n import language_name, provider_label, t
from puripuly_heart.ui.overlay_calibration import OverlayCalibration
from puripuly_heart.ui.overlay_peer_contract import build_overlay_peer_consumer_contract
from puripuly_heart.ui.views import settings as settings_view
from tests.helpers.flet_page import attach_dummy_page


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


def _make_llm_selection_view(
    monkeypatch: pytest.MonkeyPatch,
    settings: AppSettings,
) -> settings_view.SettingsView:
    monkeypatch.setattr(settings_view.SettingsView, "page", property(lambda self: None))
    view = settings_view.SettingsView.__new__(settings_view.SettingsView)
    view._settings = settings
    view._provider_settings_draft = None
    view._config_path = Path("settings.json")
    view.has_provider_changes = False
    view.has_pending_prompt_changes = False
    view._managed_trial_usage_visible = False
    view._managed_trial_usage_remaining_percent = None
    view._llm_text = SimpleNamespace(content=SimpleNamespace(value=""), update=lambda: None)
    view._openrouter_routing_text = SimpleNamespace(
        content=SimpleNamespace(value="", size=None),
        update=lambda: None,
    )
    view._openrouter_fallback_text = SimpleNamespace(
        content=SimpleNamespace(value="", size=None),
        update=lambda: None,
    )
    view._openrouter_fallback_helper_text = SimpleNamespace(value="", update=lambda: None)
    view._openrouter_routing_row = SimpleNamespace(visible=False, update=lambda: None)
    view._managed_trial_usage_bar = SimpleNamespace(
        visible=False, percent=None, update=lambda: None
    )
    view._managed_trial_usage_bar.set_percent = lambda percent: setattr(
        view._managed_trial_usage_bar, "percent", percent
    )
    view._qwen_region_btn = SimpleNamespace(visible=False, update=lambda: None)
    view._api_keys_column = SimpleNamespace(update=lambda: None)
    view._deepgram_key = SimpleNamespace(visible=False)
    view._soniox_key = SimpleNamespace(visible=False)
    view._google_key = SimpleNamespace(visible=False)
    view._openrouter_key = SimpleNamespace(visible=False)
    view._alibaba_key_beijing = SimpleNamespace(visible=False)
    view._alibaba_key_singapore = SimpleNamespace(visible=False)
    view._prompt_editor = SimpleNamespace(
        value=settings.system_prompts.get("gemini", settings.system_prompt),
        provider=None,
    )
    view._prompt_for_text = SimpleNamespace(value="")
    view._custom_vocab_helper_text = SimpleNamespace(value="")
    view._prompt_editor.set_provider = lambda provider: setattr(
        view._prompt_editor, "provider", provider
    )
    view._prompt_editor.load_default_prompt = lambda emit_change=False: setattr(
        view._prompt_editor,
        "value",
        "DEFAULT PROMPT",
    )
    view._update_peer_provider_visibility = lambda: None
    return view


def _row_cards(container: ft.Container) -> list[ft.Control]:
    return list(container.content.controls)


def _subtab_controls(view: settings_view.SettingsView, key: str) -> list[ft.Control]:
    return list(view._settings_subtab_shell.body_by_key[key].controls)


def _layout_cards(control: ft.Control) -> list[ft.Control]:
    content = getattr(control, "content", None)
    if isinstance(content, ft.Row):
        return list(content.controls)
    if _card_title(control) is not None:
        return [control]
    return []


def _prompt_tab_cards(view: settings_view.SettingsView) -> list[ft.Control]:
    return list(_subtab_controls(view, "prompt"))


def _overlay_tab_cards(view: settings_view.SettingsView) -> list[ft.Control]:
    cards: list[ft.Control] = []
    for control in _subtab_controls(view, "overlay"):
        for card in _layout_cards(control):
            try:
                title = _card_title(card)
            except Exception:
                continue
            if title is not None:
                cards.append(card)
    return cards


def _wrapped_card_column(card: ft.Control) -> ft.Control:
    return card.content.controls[1].content.content


def _card_title(card: ft.Control) -> str | None:
    column = _wrapped_card_column(card)
    controls = getattr(column, "controls", None)
    if not controls:
        return None
    title = column.controls[0]
    if isinstance(title, ft.Text):
        return title.value
    if isinstance(title, ft.Row):
        for child in title.controls:
            if isinstance(child, ft.Text) and child.value:
                return child.value
    return None


def _general_tab_card_titles(view: settings_view.SettingsView) -> list[str]:
    titles: list[str] = []
    for row in _subtab_controls(view, "general"):
        titles.extend(
            title for card in _layout_cards(row) if (title := _card_title(card)) is not None
        )
    return titles


def _api_tab_card_titles(view: settings_view.SettingsView) -> list[str]:
    titles: list[str] = []
    for row in _subtab_controls(view, "api"):
        titles.extend(
            title for card in _layout_cards(row) if (title := _card_title(card)) is not None
        )
    return titles


def _prompt_tab_card_titles(view: settings_view.SettingsView) -> list[str]:
    titles: list[str] = []
    for card in _prompt_tab_cards(view):
        if (title := _card_title(card)) is not None:
            titles.append(title)
    return titles


def _general_tab_card(view: settings_view.SettingsView, title: str) -> ft.Control:
    for row in _subtab_controls(view, "general"):
        for card in _layout_cards(row):
            if _card_title(card) == title:
                return card
    raise AssertionError(f"General tab card not found: {title}")


def _api_tab_card(view: settings_view.SettingsView, title: str) -> ft.Control:
    for row in _subtab_controls(view, "api"):
        for card in _layout_cards(row):
            if _card_title(card) == title:
                return card
    raise AssertionError(f"API tab card not found: {title}")


def _row_card_titles(control: ft.Control) -> list[str]:
    return [title for card in _layout_cards(control) if (title := _card_title(card)) is not None]


def _prompt_tab_card(view: settings_view.SettingsView, title: str) -> ft.Control:
    for card in _prompt_tab_cards(view):
        if _card_title(card) == title:
            return card
    raise AssertionError(f"prompt tab card not found: {title}")


def _overlay_tab_card_titles(view: settings_view.SettingsView) -> list[str]:
    titles: list[str] = []
    for card in _overlay_tab_cards(view):
        if (title := _card_title(card)) is not None:
            titles.append(title)
    return titles


def _overlay_tab_card(view: settings_view.SettingsView, title: str) -> ft.Control:
    for card in _overlay_tab_cards(view):
        if _card_title(card) == title:
            return card
    raise AssertionError(f"overlay tab card not found: {title}")


def _iter_control_tree(control: ft.Control):
    yield control
    content = getattr(control, "content", None)
    if content is not None:
        yield from _iter_control_tree(content)
    controls = getattr(control, "controls", None) or []
    for child in controls:
        yield from _iter_control_tree(child)


def _control_labels(control: ft.Control) -> list[str]:
    labels: list[str] = []
    for node in _iter_control_tree(control):
        if isinstance(node, ft.Text) and node.value:
            labels.append(node.value)
        elif isinstance(node, ft.TextField) and node.label:
            labels.append(node.label)
        elif isinstance(node, ft.TextButton) and node.text:
            labels.append(node.text)
    return labels


def _tree_contains_control(root: ft.Control, target: ft.Control) -> bool:
    return any(node is target for node in _iter_control_tree(root))


def _button_style_value(
    button: ft.TextButton,
    attribute: str,
    state: ft.ControlState = ft.ControlState.DEFAULT,
):
    return getattr(button.style, attribute)[state]


def _subtab_label(button: ft.Control) -> ft.Text:
    if isinstance(button, ft.Container) and isinstance(button.content, ft.Text):
        return button.content
    raise AssertionError(f"Expected subtab container label, got {type(button)!r}")


def _subtab_text_value(button: ft.Control) -> str:
    if isinstance(button, ft.TextButton):
        return button.text
    return _subtab_label(button).value


def _subtab_text_color(button: ft.Control) -> str | None:
    if isinstance(button, ft.TextButton):
        return _button_style_value(button, "color")
    return _subtab_label(button).color


def _subtab_text_size(button: ft.Control) -> int | None:
    if isinstance(button, ft.TextButton):
        raise AssertionError("Expected bottom-docked container-backed subtab")
    return _subtab_label(button).size


def _container_text_size(control: ft.Container) -> int | None:
    if not isinstance(control.content, ft.Text):
        raise AssertionError(
            f"Expected container-backed text control, got {type(control.content)!r}"
        )
    return control.content.size


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


def test_setting_action_text_size_shrinks_for_long_values() -> None:
    assert settings_view._setting_action_text_size("영어") == 22
    assert settings_view._setting_action_text_size("Deepgram") == 20
    assert settings_view._setting_action_text_size("qwen3-asr-flash-realtime") == 16


def test_peer_language_card_removed_from_general_tab(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    view, _ = _make_settings_view(monkeypatch)
    general_titles = _general_tab_card_titles(view)
    api_titles = _api_tab_card_titles(view)
    general_labels: list[str] = []
    api_labels: list[str] = []
    for row in _subtab_controls(view, "general"):
        general_labels.extend(_control_labels(row))
    for row in _subtab_controls(view, "api"):
        api_labels.extend(_control_labels(row))

    assert t("settings.peer_language") not in general_titles
    assert t("settings.section.peer_stt") not in general_titles
    assert t("settings.section.peer_stt") in api_titles
    assert t("settings.peer_language.source") not in general_labels
    assert t("settings.peer_language.target") not in general_labels
    assert t("settings.dashboard_language_redirect") not in general_labels
    assert t("settings.dashboard_language_redirect") not in api_labels
    assert not hasattr(view, "_peer_source_text")
    assert not hasattr(view, "_peer_target_text")


def test_load_from_settings_resizes_long_peer_model_value(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = AppSettings()
    settings.peer_qwen_asr_stt.model = "qwen3-asr-flash-realtime"

    view, _ = _make_settings_view(monkeypatch)
    view.load_from_settings(settings, config_path=Path("settings.json"))

    assert view._peer_qwen_model_text.content.value == "qwen3-asr-flash-realtime"
    assert view._peer_qwen_model_text.content.size == settings_view._setting_action_text_size(
        "qwen3-asr-flash-realtime"
    )


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
    basic_messages: list[str] = []

    monkeypatch.setattr(settings_view.SettingsView, "_populate_host_apis", lambda self: None)
    monkeypatch.setattr(settings_view.SettingsView, "_refresh_microphones", lambda self: None)
    monkeypatch.setattr(settings_view.SettingsView, "update", lambda self: None)

    def raise_store(*_args, **_kwargs):
        raise RuntimeError("boom")

    monkeypatch.setattr(settings_view, "create_secret_store", raise_store)
    view = settings_view.SettingsView()
    view.runtime_log_basic = lambda message, *, level=logging.INFO: basic_messages.append(message)
    view.load_from_settings(settings, config_path=Path("settings.json"))

    assert view._google_key.value == ""
    assert view._deepgram_key.value == ""
    assert view._soniox_key.value == ""
    assert basic_messages == ["Failed to load secrets: boom"]


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


def test_update_api_visibility_shows_openrouter_key(monkeypatch: pytest.MonkeyPatch) -> None:
    settings = AppSettings()
    settings.provider.llm = LLMProviderName.OPENROUTER
    settings.openrouter.selected_source = OpenRouterCredentialSource.BYOK
    settings.openrouter.selection_alias = OpenRouterSelectionAlias.GEMMA4_BYOK

    view = _make_llm_selection_view(monkeypatch, settings)
    view._update_api_visibility()

    assert view._google_key.visible is False
    assert view._alibaba_key_beijing.visible is False
    assert view._alibaba_key_singapore.visible is False
    assert view._openrouter_key.visible is True
    assert view._openrouter_routing_row.visible is True


def test_update_api_visibility_hides_openrouter_key_for_managed_trial(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = AppSettings()
    settings.provider.llm = LLMProviderName.OPENROUTER
    settings.openrouter.selected_source = OpenRouterCredentialSource.MANAGED

    view = _make_llm_selection_view(monkeypatch, settings)
    view._update_api_visibility()

    assert view._openrouter_key.visible is False
    assert view._managed_trial_usage_bar.visible is True
    assert view._openrouter_routing_row.visible is True


def test_load_from_settings_shows_managed_usage_bar_in_api_keys_column(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = AppSettings()
    settings.provider.llm = LLMProviderName.OPENROUTER
    settings.openrouter.selected_source = OpenRouterCredentialSource.MANAGED

    view, _ = _make_settings_view(monkeypatch)
    view.load_from_settings(settings, config_path=Path("settings.json"))

    assert view._managed_trial_usage_bar in view._api_keys_column.controls
    assert view._managed_trial_usage_bar.visible is True
    assert view._openrouter_key.visible is False


def test_set_managed_trial_usage_state_tracks_visible_and_remaining_percent(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = AppSettings()
    settings.provider.llm = LLMProviderName.OPENROUTER
    settings.openrouter.selected_source = OpenRouterCredentialSource.MANAGED

    view, _ = _make_settings_view(monkeypatch)
    view.load_from_settings(settings, config_path=Path("settings.json"))

    view.set_managed_trial_usage_state(visible=True, remaining_percent=71)

    assert view.managed_trial_usage_state == {
        "visible": True,
        "remaining_percent": 71,
    }
    assert view._managed_trial_usage_bar.visible is True
    assert view._managed_trial_usage_bar.percent == 71

    view.set_managed_trial_usage_state(visible=False, remaining_percent=12)

    assert view.managed_trial_usage_state == {
        "visible": False,
        "remaining_percent": None,
    }
    assert view._managed_trial_usage_bar.visible is True
    assert view._managed_trial_usage_bar.percent is None


def test_update_api_visibility_keeps_openrouter_cards_visible_for_inactive_fallback_copy(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = AppSettings()
    settings.provider.llm = LLMProviderName.GEMINI

    view, _ = _make_settings_view(monkeypatch)
    view._settings = settings
    view._update_api_visibility()

    assert view._openrouter_routing_row.visible is True
    assert view._openrouter_fallback_helper_text.value == t(
        "settings.openrouter_fallback.inactive_helper"
    )


def test_update_api_visibility_treats_peer_local_qwen_as_local_provider(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = AppSettings()
    settings.provider.stt = STTProviderName.LOCAL_QWEN
    settings.provider.peer_stt = STTProviderName.LOCAL_QWEN
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

    pending = view.build_provider_apply_settings()

    assert settings.provider.stt == STTProviderName.LOCAL_QWEN
    assert pending is not None
    assert pending.provider.stt == STTProviderName.SONIOX
    assert view.has_provider_changes is True
    assert changed == []


def test_on_peer_stt_selected_updates_provider_and_pipeline_flags(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = AppSettings()
    changed: list[AppSettings] = []
    view, _ = _make_settings_view(monkeypatch)
    view.load_from_settings(settings, config_path=Path("settings.json"))
    view.on_settings_changed = lambda incoming: changed.append(incoming)

    view._on_peer_stt_selected(STTProviderName.SONIOX.value)

    pending = view.build_provider_apply_settings()

    assert settings.provider.peer_stt == STTProviderName.DEEPGRAM
    assert pending is not None
    assert pending.provider.peer_stt == STTProviderName.SONIOX
    assert view.has_provider_changes is True
    assert changed == []


def test_peer_stt_local_qwen_option_is_selectable_with_provider_description(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = AppSettings()
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

    view._on_peer_stt_click(None)

    options = captured["options"]
    local_qwen_option = next(
        option for option in options if option.value == STTProviderName.LOCAL_QWEN.value
    )

    assert captured["title"] == t("settings.peer_stt_provider")
    assert captured["show_description"] is True
    assert local_qwen_option.label == "Qwen ASR 0.6B (Local)"
    assert local_qwen_option.disabled is False
    assert local_qwen_option.description == t("provider.local_qwen.description")
    assert all(not option.disabled for option in options)


def test_peer_stt_local_qwen_choice_can_be_persisted(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = AppSettings()
    view, _ = _make_settings_view(monkeypatch)
    view.load_from_settings(settings, config_path=Path("settings.json"))

    view._on_peer_stt_selected(STTProviderName.LOCAL_QWEN.value)

    pending = view.build_provider_apply_settings()

    assert settings.provider.peer_stt == STTProviderName.DEEPGRAM
    assert pending is not None
    assert pending.provider.peer_stt == STTProviderName.LOCAL_QWEN
    assert view.has_provider_changes is True

    settings.provider.peer_stt = STTProviderName.LOCAL_QWEN
    view.load_from_settings(settings, config_path=Path("settings.json"))

    normalized_pending = view.build_provider_apply_settings()

    assert normalized_pending is not None
    assert normalized_pending.provider.peer_stt == STTProviderName.LOCAL_QWEN


def test_settings_view_omits_legacy_overlay_peer_toggle_api(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    view, _ = _make_settings_view(monkeypatch)

    assert not hasattr(view, "on_overlay_toggle")
    assert not hasattr(view, "on_peer_translation_toggle")
    assert not hasattr(view, "_overlay_enabled_label")
    assert not hasattr(view, "_overlay_enabled_button")
    assert not hasattr(view, "_peer_translation_label")
    assert not hasattr(view, "_peer_translation_button")
    assert not hasattr(view, "_peer_translation_status_text")
    assert not hasattr(view, "_peer_translation_hint")
    assert not hasattr(view, "_overlay_status_text")
    assert not hasattr(settings_view.SettingsView, "_on_overlay_click")
    assert not hasattr(settings_view.SettingsView, "_on_overlay_selected")
    assert not hasattr(settings_view.SettingsView, "_on_peer_translation_click")
    assert not hasattr(settings_view.SettingsView, "_on_peer_translation_selected")


def test_on_llm_selected_updates_model_and_prompt_state(monkeypatch: pytest.MonkeyPatch) -> None:
    settings = AppSettings()
    settings.provider.llm = LLMProviderName.GEMINI
    settings.system_prompts = {"gemini": "G", "qwen": "Q"}
    settings.system_prompt = "G"

    view, _ = _make_settings_view(monkeypatch)
    view.load_from_settings(settings, config_path=Path("settings.json"))
    view._on_llm_selected(QwenLLMModel.QWEN_35_PLUS.value)

    pending = view.build_provider_apply_settings()

    assert settings.provider.llm == LLMProviderName.GEMINI
    assert pending is not None
    assert pending.provider.llm == LLMProviderName.QWEN
    assert pending.qwen.llm_model == QwenLLMModel.QWEN_35_PLUS
    assert view._prompt_editor.value == "Q"
    assert settings.system_prompt == "G"

    view._on_llm_selected(QwenLLMModel.QWEN_35_PLUS.value)
    assert view.has_provider_changes is True


def test_on_llm_selected_updates_openrouter_model_and_prompt_state(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = AppSettings()
    settings.provider.llm = LLMProviderName.GEMINI
    settings.system_prompts = {
        "gemini": "G",
        "openrouter": "O",
        "qwen": "Q",
    }
    settings.system_prompt = "G"

    view, _ = _make_settings_view(monkeypatch)
    view.load_from_settings(settings, config_path=Path("settings.json"))
    view._on_llm_selected(OpenRouterSelectionAlias.GEMMA4_BYOK.value)

    pending = view.build_provider_apply_settings()

    assert settings.provider.llm == LLMProviderName.GEMINI
    assert pending is not None
    assert pending.provider.llm == LLMProviderName.OPENROUTER
    assert pending.openrouter.llm_model == OpenRouterLLMModel.GEMMA_4_26B_A4B_IT
    assert pending.openrouter.selected_source == OpenRouterCredentialSource.BYOK
    assert pending.openrouter.selection_alias == OpenRouterSelectionAlias.GEMMA4_BYOK
    assert view._prompt_editor.value == "O"
    assert settings.system_prompt == "G"
    assert view._openrouter_routing_row.visible is True


def test_on_llm_selected_updates_managed_openrouter_label_and_source(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = AppSettings()
    settings.provider.llm = LLMProviderName.GEMINI
    settings.system_prompts = {
        "gemini": "G",
        "openrouter": "O",
        "qwen": "Q",
    }
    settings.system_prompt = "G"

    view = _make_llm_selection_view(monkeypatch, settings)
    view._on_llm_selected(settings_view._OPENROUTER_MANAGED_OPTION_VALUE)

    pending = view.build_provider_apply_settings()

    assert pending is not None
    assert pending.provider.llm == LLMProviderName.OPENROUTER
    assert pending.openrouter.llm_model == OpenRouterLLMModel.GEMMA_4_26B_A4B_IT
    assert pending.openrouter.selected_source == OpenRouterCredentialSource.MANAGED
    assert pending.openrouter.selection_alias == OpenRouterSelectionAlias.GEMMA4_MANAGED
    assert view._llm_text.content.value == t("provider.gemma4_managed")
    assert view._openrouter_key.visible is False
    assert view._prompt_editor.value == "O"


def test_on_llm_selected_openrouter_provider_value_defaults_to_gemma_managed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = AppSettings()
    settings.provider.llm = LLMProviderName.GEMINI

    view = _make_llm_selection_view(monkeypatch, settings)
    view._on_llm_selected(LLMProviderName.OPENROUTER.value)

    pending = view.build_provider_apply_settings()

    assert pending is not None
    assert pending.provider.llm == LLMProviderName.OPENROUTER
    assert pending.openrouter.selected_source == OpenRouterCredentialSource.MANAGED
    assert pending.openrouter.selection_alias == OpenRouterSelectionAlias.GEMMA4_MANAGED
    assert view._llm_text.content.value == t("provider.gemma4_managed")


def test_on_llm_selected_sets_qwen_managed_alias_and_label(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = AppSettings()
    settings.provider.llm = LLMProviderName.GEMINI
    settings.system_prompts = {
        "gemini": "G",
        "openrouter": "O",
        "qwen": "Q",
    }
    settings.system_prompt = "G"

    view = _make_llm_selection_view(monkeypatch, settings)
    view._on_llm_selected(OpenRouterSelectionAlias.QWEN35_FLASH_MANAGED.value)

    pending = view.build_provider_apply_settings()

    assert pending is not None
    assert pending.provider.llm == LLMProviderName.OPENROUTER
    assert pending.openrouter.selection_alias == OpenRouterSelectionAlias.QWEN35_FLASH_MANAGED
    assert pending.openrouter.llm_model == OpenRouterLLMModel.QWEN_35_FLASH_02_23
    assert pending.openrouter.selected_source == OpenRouterCredentialSource.MANAGED
    assert view._llm_text.content.value == t("provider.qwen35_flash_managed")
    assert view._prompt_editor.value == "O"


def test_on_llm_selected_updates_prompt_helper_copy_live_when_mounted(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = AppSettings()
    settings.provider.llm = LLMProviderName.GEMINI
    settings.system_prompts = {"gemini": "G", "qwen": "Q"}
    settings.system_prompt = "G"

    view = _make_llm_selection_view(monkeypatch, settings)
    monkeypatch.setattr(settings_view.SettingsView, "page", property(lambda self: object()))
    prompt_copy_updates: list[str] = []
    view._prompt_for_text = SimpleNamespace(
        value="stale",
        update=lambda: prompt_copy_updates.append(view._prompt_for_text.value),
    )

    view._on_llm_selected(QwenLLMModel.QWEN_35_PLUS.value)

    assert view._prompt_for_text.value == t(
        "settings.prompt_for",
        provider=provider_label(LLMProviderName.QWEN.value),
    )
    assert prompt_copy_updates == [
        t(
            "settings.prompt_for",
            provider=provider_label(LLMProviderName.QWEN.value),
        )
    ]


def test_on_llm_selected_leaving_managed_mode_clears_verified_hardware_hash_fields(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = AppSettings()
    settings.provider.llm = LLMProviderName.OPENROUTER
    settings.openrouter.selected_source = OpenRouterCredentialSource.MANAGED
    settings.managed_identity.verified_hardware_hash = "hardware-hash"
    settings.managed_identity.verified_hardware_hash_salt_version = 7

    view, _ = _make_settings_view(monkeypatch)
    view.load_from_settings(settings, config_path=Path("settings.json"))

    view._on_llm_selected(OpenRouterSelectionAlias.GEMMA4_BYOK.value)

    pending = view.build_provider_apply_settings()

    assert pending is not None
    assert pending.openrouter.selected_source == OpenRouterCredentialSource.BYOK
    assert pending.managed_identity.verified_hardware_hash is None
    assert pending.managed_identity.verified_hardware_hash_salt_version is None


def test_on_llm_selected_round_trips_back_to_managed_without_dropping_verified_snapshot(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = AppSettings()
    settings.provider.llm = LLMProviderName.OPENROUTER
    settings.openrouter.selected_source = OpenRouterCredentialSource.MANAGED
    settings.managed_identity.verified_hardware_hash = "hardware-hash"
    settings.managed_identity.verified_hardware_hash_salt_version = 7

    view, _ = _make_settings_view(monkeypatch)
    view.load_from_settings(settings, config_path=Path("settings.json"))

    view._on_llm_selected(OpenRouterSelectionAlias.GEMMA4_BYOK.value)
    view._on_llm_selected(settings_view._OPENROUTER_MANAGED_OPTION_VALUE)

    pending = view.build_provider_apply_settings()

    assert pending is not None
    assert pending.openrouter.selected_source == OpenRouterCredentialSource.MANAGED
    assert pending.managed_identity.verified_hardware_hash == "hardware-hash"
    assert pending.managed_identity.verified_hardware_hash_salt_version == 7


def test_on_llm_selected_switching_away_from_openrouter_preserves_saved_selection_alias(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = AppSettings()
    settings.provider.llm = LLMProviderName.OPENROUTER
    settings.openrouter.selected_source = OpenRouterCredentialSource.BYOK
    settings.openrouter.selection_alias = OpenRouterSelectionAlias.GEMMA4_BYOK
    settings.system_prompts = {"gemini": "G", "openrouter": "O", "qwen": "Q"}
    settings.system_prompt = "O"

    view, _ = _make_settings_view(monkeypatch)
    view.load_from_settings(settings, config_path=Path("settings.json"))

    view._on_llm_selected(QwenLLMModel.QWEN_35_PLUS.value)

    pending = view.build_provider_apply_settings()

    assert pending is not None
    assert pending.provider.llm == LLMProviderName.QWEN
    assert pending.openrouter.selected_source == OpenRouterCredentialSource.BYOK
    assert pending.openrouter.selection_alias == OpenRouterSelectionAlias.GEMMA4_BYOK


def test_on_llm_selected_preserves_default_openrouter_managed_selection_during_gemini_and_qwen_changes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = AppSettings()

    view, _ = _make_settings_view(monkeypatch)
    view.load_from_settings(settings, config_path=Path("settings.json"))

    view._on_llm_selected(GeminiLLMModel.GEMINI_31_FLASH_LITE.value)
    pending = view.build_provider_apply_settings()

    assert pending is not None
    assert pending.provider.llm == LLMProviderName.GEMINI
    assert pending.openrouter.selected_source == OpenRouterCredentialSource.MANAGED
    assert pending.openrouter.selection_alias == OpenRouterSelectionAlias.GEMMA4_MANAGED

    view._on_llm_selected(QwenLLMModel.QWEN_35_PLUS.value)
    pending = view.build_provider_apply_settings()

    assert pending is not None
    assert pending.provider.llm == LLMProviderName.QWEN
    assert pending.openrouter.selected_source == OpenRouterCredentialSource.MANAGED
    assert pending.openrouter.selection_alias == OpenRouterSelectionAlias.GEMMA4_MANAGED


def test_load_from_settings_shows_openrouter_routing_label(monkeypatch: pytest.MonkeyPatch) -> None:
    settings = AppSettings()
    settings.provider.llm = LLMProviderName.OPENROUTER
    settings.openrouter.routing_mode = OpenRouterRoutingMode.NOVITA_FIRST

    view, _ = _make_settings_view(monkeypatch)
    view.load_from_settings(settings, config_path=Path("settings.json"))

    assert view._openrouter_routing_row.visible is True
    assert view._openrouter_routing_text.content.value == t(
        "settings.openrouter_routing.novita_first"
    )


def test_on_openrouter_routing_selected_updates_settings_and_flags(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = AppSettings()
    settings.provider.llm = LLMProviderName.OPENROUTER
    changed: list[AppSettings] = []

    view, _ = _make_settings_view(monkeypatch)
    view.load_from_settings(settings, config_path=Path("settings.json"))
    view.on_settings_changed = lambda incoming: changed.append(incoming)

    view._on_openrouter_routing_selected(OpenRouterRoutingMode.PARASAIL_FIRST.value)

    pending = view.build_provider_apply_settings()

    assert settings.openrouter.routing_mode == OpenRouterRoutingMode.LATENCY
    assert pending is not None
    assert pending.openrouter.routing_mode == OpenRouterRoutingMode.PARASAIL_FIRST
    assert view._openrouter_routing_text.content.value == t(
        "settings.openrouter_routing.parasail_first"
    )
    assert view.has_provider_changes is True
    assert changed == []


def test_on_openrouter_fallback_selected_updates_draft_and_helper_copy(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = AppSettings()
    settings.provider.llm = LLMProviderName.GEMINI

    view, _ = _make_settings_view(monkeypatch)
    view.load_from_settings(settings, config_path=Path("settings.json"))

    view._on_openrouter_fallback_selected(
        OpenRouterFallbackSelectionAlias.GEMINI25_FLASH_LITE.value
    )

    pending = view.build_provider_apply_settings()

    assert pending is not None
    assert (
        pending.openrouter.fallback_selection_alias
        == OpenRouterFallbackSelectionAlias.GEMINI25_FLASH_LITE
    )
    assert view._openrouter_fallback_text.content.value == t("provider.gemini25_flash_lite")
    assert view._openrouter_fallback_helper_text.value == t(
        "settings.openrouter_fallback.inactive_helper"
    )

    view._on_llm_selected(OpenRouterSelectionAlias.GEMMA4_MANAGED.value)

    assert view._openrouter_fallback_helper_text.value == t(
        "settings.openrouter_fallback.active_helper"
    )


def test_fallback_card_stays_visible_when_non_openrouter_active(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = AppSettings()
    settings.provider.llm = LLMProviderName.GEMINI

    view, _ = _make_settings_view(monkeypatch)
    view.load_from_settings(settings, config_path=Path("settings.json"))

    assert view._openrouter_routing_row.visible is True
    assert t("settings.openrouter_fallback") in _api_tab_card_titles(view)


def test_update_api_visibility_keeps_openrouter_key_for_openrouter_gemini_fallback(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = AppSettings()
    settings.provider.llm = LLMProviderName.OPENROUTER
    settings.openrouter.selected_source = OpenRouterCredentialSource.BYOK
    settings.openrouter.selection_alias = OpenRouterSelectionAlias.GEMMA4_BYOK
    settings.openrouter.fallback_selection_alias = (
        OpenRouterFallbackSelectionAlias.GEMINI25_FLASH_LITE
    )

    view = _make_llm_selection_view(monkeypatch, settings)
    view._update_api_visibility()

    assert view._google_key.visible is False
    assert view._openrouter_key.visible is True
    assert view._alibaba_key_beijing.visible is False
    assert view._alibaba_key_singapore.visible is False


def test_update_api_visibility_keeps_openrouter_key_for_inactive_byok_fallback(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = AppSettings()
    settings.provider.llm = LLMProviderName.GEMINI
    settings.openrouter.selected_source = OpenRouterCredentialSource.BYOK
    settings.openrouter.selection_alias = OpenRouterSelectionAlias.GEMMA4_BYOK
    settings.openrouter.fallback_selection_alias = (
        OpenRouterFallbackSelectionAlias.GEMINI25_FLASH_LITE
    )

    view = _make_llm_selection_view(monkeypatch, settings)
    view._update_api_visibility()

    assert view._google_key.visible is True
    assert view._openrouter_key.visible is True


def test_update_api_visibility_shows_openrouter_key_for_byok_fallback_when_main_provider_is_gemini(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = AppSettings()
    settings.provider.llm = LLMProviderName.GEMINI
    settings.openrouter.selected_source = OpenRouterCredentialSource.BYOK
    settings.openrouter.selection_alias = OpenRouterSelectionAlias.GEMMA4_BYOK
    settings.openrouter.fallback_selection_alias = OpenRouterFallbackSelectionAlias.QWEN35_FLASH

    view = _make_llm_selection_view(monkeypatch, settings)
    view._update_api_visibility()

    assert view._google_key.visible is True
    assert view._openrouter_key.visible is True


def test_openrouter_fallback_modal_only_lists_none_gemini25_and_qwen35(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = AppSettings()
    settings.provider.llm = LLMProviderName.OPENROUTER

    view = _make_settings_view(monkeypatch)[0]
    view.load_from_settings(settings, config_path=Path("settings.json"))
    attach_dummy_page(monkeypatch, view)

    captured: dict[str, object] = {}

    class DummyModal:
        def __init__(self, _page, _title, options, _on_select, *, show_description=False):
            captured["options"] = options
            captured["show_description"] = show_description

        def open(self, current: str) -> None:
            captured["current"] = current

    monkeypatch.setattr(settings_view, "SettingsModal", DummyModal)

    view._on_openrouter_fallback_click(None)

    assert captured["show_description"] is True
    options = captured["options"]
    assert [option.value for option in options] == [
        OpenRouterFallbackSelectionAlias.NONE.value,
        OpenRouterFallbackSelectionAlias.GEMINI25_FLASH_LITE.value,
        OpenRouterFallbackSelectionAlias.QWEN35_FLASH.value,
    ]
    assert [option.label for option in options] == [
        t("settings.openrouter_fallback.none"),
        t("provider.gemini25_flash_lite"),
        t("provider.qwen35_flash_fallback"),
    ]


def test_llm_modal_omits_openrouter_descriptions_and_direct_qwen_flash_option(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = AppSettings()
    settings.provider.llm = LLMProviderName.GEMINI

    view, _ = _make_settings_view(monkeypatch)
    view.load_from_settings(settings, config_path=Path("settings.json"))
    attach_dummy_page(monkeypatch, view)

    captured: dict[str, object] = {}

    class DummyModal:
        def __init__(self, _page, title, options, _on_select, *, show_description=False):
            captured["title"] = title
            captured["options"] = options
            captured["show_description"] = show_description

        def open(self, current: str) -> None:
            captured["current"] = current

    monkeypatch.setattr(settings_view, "SettingsModal", DummyModal)

    view._on_llm_click(None)

    options = captured["options"]
    option_by_value = {option.value: option for option in options}

    assert captured["title"] == t("settings.section.translation")
    assert captured["show_description"] is True
    assert [option.value for option in options] == [
        OpenRouterSelectionAlias.GEMMA4_MANAGED.value,
        OpenRouterSelectionAlias.QWEN35_FLASH_MANAGED.value,
        GeminiLLMModel.GEMINI_3_FLASH.value,
        GeminiLLMModel.GEMINI_31_FLASH_LITE.value,
        OpenRouterSelectionAlias.GEMMA4_BYOK.value,
        OpenRouterSelectionAlias.QWEN35_FLASH_BYOK.value,
        QwenLLMModel.QWEN_35_PLUS.value,
    ]
    assert QwenLLMModel.QWEN_35_FLASH.value not in option_by_value
    assert option_by_value[OpenRouterSelectionAlias.GEMMA4_MANAGED.value].description == ""
    assert option_by_value[OpenRouterSelectionAlias.QWEN35_FLASH_MANAGED.value].description == ""
    assert option_by_value[OpenRouterSelectionAlias.GEMMA4_BYOK.value].description == ""
    assert option_by_value[OpenRouterSelectionAlias.QWEN35_FLASH_BYOK.value].description == ""
    assert option_by_value[OpenRouterSelectionAlias.QWEN35_FLASH_BYOK.value].label == t(
        "provider.qwen35_flash_openrouter"
    )


def test_openrouter_routing_modal_only_lists_latency_option(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = AppSettings()
    settings.provider.llm = LLMProviderName.OPENROUTER

    view, _ = _make_settings_view(monkeypatch)
    view.load_from_settings(settings, config_path=Path("settings.json"))
    attach_dummy_page(monkeypatch, view)

    captured: dict[str, object] = {}

    class DummyModal:
        def __init__(self, _page, _title, options, _on_select, *, show_description=False):
            captured["options"] = options
            captured["show_description"] = show_description

        def open(self, current: str) -> None:
            captured["current"] = current

    monkeypatch.setattr(settings_view, "SettingsModal", DummyModal)

    view._on_openrouter_routing_click(None)

    assert captured["show_description"] is True
    options = captured["options"]
    assert [option.value for option in options] == [OpenRouterRoutingMode.LATENCY.value]
    assert [option.label for option in options] == [t("settings.openrouter_routing.latency")]


def test_openrouter_fallback_modal_hides_provider_descriptions_for_active_options(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = AppSettings()
    settings.provider.llm = LLMProviderName.OPENROUTER

    view = _make_settings_view(monkeypatch)[0]
    view.load_from_settings(settings, config_path=Path("settings.json"))
    attach_dummy_page(monkeypatch, view)

    captured: dict[str, object] = {}

    class DummyModal:
        def __init__(self, _page, _title, options, _on_select, *, show_description=False):
            captured["options"] = options
            captured["show_description"] = show_description

        def open(self, current: str) -> None:
            captured["current"] = current

    monkeypatch.setattr(settings_view, "SettingsModal", DummyModal)

    view._on_openrouter_fallback_click(None)

    options = {option.value: option for option in captured["options"]}
    assert captured["show_description"] is True
    assert options[OpenRouterFallbackSelectionAlias.NONE.value].description == t(
        "settings.openrouter_fallback.none.description"
    )
    assert options[OpenRouterFallbackSelectionAlias.GEMINI25_FLASH_LITE.value].description == ""
    assert options[OpenRouterFallbackSelectionAlias.QWEN35_FLASH.value].description == ""


def test_openrouter_fallback_off_does_not_show_active_helper_copy(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = AppSettings()
    settings.provider.llm = LLMProviderName.OPENROUTER
    settings.openrouter.fallback_selection_alias = OpenRouterFallbackSelectionAlias.NONE

    view = _make_llm_selection_view(monkeypatch, settings)
    view._update_api_visibility()

    assert view._openrouter_fallback_helper_text.value == t(
        "settings.openrouter_fallback.none.description"
    )


def test_openrouter_fallback_off_shows_off_description_when_main_provider_is_inactive(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = AppSettings()
    settings.provider.llm = LLMProviderName.GEMINI
    settings.openrouter.fallback_selection_alias = OpenRouterFallbackSelectionAlias.NONE

    view = _make_llm_selection_view(monkeypatch, settings)
    view._update_api_visibility()

    assert view._openrouter_fallback_helper_text.value == t(
        "settings.openrouter_fallback.none.description"
    )


def test_on_llm_selected_updates_gemini_model(monkeypatch: pytest.MonkeyPatch) -> None:
    settings = AppSettings()
    settings.provider.llm = LLMProviderName.GEMINI
    settings.gemini.llm_model = GeminiLLMModel.GEMINI_3_FLASH
    settings.system_prompts = {"gemini": "G", "qwen": "Q"}
    settings.system_prompt = "G"

    view, _ = _make_settings_view(monkeypatch)
    view.load_from_settings(settings, config_path=Path("settings.json"))
    view._on_llm_selected(GeminiLLMModel.GEMINI_31_FLASH_LITE.value)

    pending = view.build_provider_apply_settings()

    assert settings.provider.llm == LLMProviderName.GEMINI
    assert settings.gemini.llm_model == GeminiLLMModel.GEMINI_3_FLASH
    assert pending is not None
    assert pending.gemini.llm_model == GeminiLLMModel.GEMINI_31_FLASH_LITE
    assert view._prompt_editor.value == "G"
    assert settings.system_prompt == "G"
    assert view.has_provider_changes is True


def test_on_llm_selected_logs_only_changed_fields_for_provider_switch(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = AppSettings()
    settings.provider.llm = LLMProviderName.GEMINI
    settings.gemini.llm_model = GeminiLLMModel.GEMINI_31_FLASH_LITE
    settings.qwen.llm_model = QwenLLMModel.QWEN_35_PLUS
    basic_messages: list[str] = []
    detailed_messages: list[str] = []

    view, _ = _make_settings_view(monkeypatch)
    view.load_from_settings(settings, config_path=Path("settings.json"))
    view.runtime_log_basic = lambda message, *, level=logging.INFO: basic_messages.append(message)
    view.runtime_log_detailed = lambda message, *, level=logging.INFO: detailed_messages.append(
        message
    )

    view._on_llm_selected(QwenLLMModel.QWEN_35_PLUS.value)

    assert basic_messages == ["[Settings] LLM provider changed: gemini -> qwen"]
    assert detailed_messages == ["[Settings] LLM selection changed: provider=gemini->qwen"]


def test_on_llm_selected_skips_log_when_selection_is_unchanged(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = AppSettings()
    settings.provider.llm = LLMProviderName.QWEN
    settings.qwen.llm_model = QwenLLMModel.QWEN_35_PLUS
    basic_messages: list[str] = []
    detailed_messages: list[str] = []

    view, _ = _make_settings_view(monkeypatch)
    view.load_from_settings(settings, config_path=Path("settings.json"))
    view.runtime_log_basic = lambda message, *, level=logging.INFO: basic_messages.append(message)
    view.runtime_log_detailed = lambda message, *, level=logging.INFO: detailed_messages.append(
        message
    )

    view._on_llm_selected(QwenLLMModel.QWEN_35_PLUS.value)

    assert basic_messages == []
    assert detailed_messages == []


def test_on_ui_and_region_selection_emit_changes(monkeypatch: pytest.MonkeyPatch) -> None:
    settings = AppSettings()
    changed: list[AppSettings] = []
    view, _ = _make_settings_view(monkeypatch)
    view.load_from_settings(settings, config_path=Path("settings.json"))
    view.on_settings_changed = lambda incoming: changed.append(incoming)

    view._on_ui_selected("ko")
    view._on_qwen_region_selected(QwenRegion.SINGAPORE.value)

    assert settings.ui.locale == "ko"
    assert settings.qwen.region == QwenRegion.BEIJING
    pending = view.build_provider_apply_settings()
    assert pending is not None
    assert pending.qwen.region == QwenRegion.SINGAPORE
    assert view.has_provider_changes is True
    assert len(changed) == 1
    assert changed[0].qwen.region == QwenRegion.BEIJING


def test_provider_draft_does_not_leak_into_immediate_settings_apply(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = AppSettings()
    changed: list[AppSettings] = []
    view, _ = _make_settings_view(monkeypatch)
    view.load_from_settings(settings, config_path=Path("settings.json"))
    view.on_settings_changed = lambda incoming: changed.append(incoming)

    view._on_stt_selected(STTProviderName.SONIOX.value)
    view._on_ui_selected("ko")

    pending = view.build_provider_apply_settings()

    assert len(changed) == 1
    assert changed[0].ui.locale == "ko"
    assert changed[0].provider.stt == STTProviderName.LOCAL_QWEN
    assert pending is not None
    assert pending.provider.stt == STTProviderName.SONIOX


def test_provider_selection_equality_guards_skip_noop_draft_changes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = AppSettings()
    changed: list[AppSettings] = []
    view, _ = _make_settings_view(monkeypatch)
    view.load_from_settings(settings, config_path=Path("settings.json"))
    view.on_settings_changed = lambda incoming: changed.append(incoming)

    view._on_stt_selected(STTProviderName.LOCAL_QWEN.value)
    view._on_peer_stt_selected(STTProviderName.DEEPGRAM.value)
    view._on_openrouter_routing_selected(OpenRouterRoutingMode.LATENCY.value)
    view._on_qwen_region_selected(QwenRegion.BEIJING.value)

    assert view.has_provider_changes is False
    assert changed == []


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
    view._peer_vad_field.value = "0.61"
    view._on_peer_vad_threshold_change(SimpleNamespace(control=view._peer_vad_field))
    view._on_low_latency_selected("on")

    assert settings.audio.input_host_api == "MME"
    assert settings.audio.input_device == "Mic 2"
    assert settings.stt.vad_speech_threshold == 0.72
    assert settings.desktop_audio.vad_speech_threshold == 0.61
    assert settings.stt.low_latency_mode is True
    assert view._low_latency_text.content.value == t("toggle.on")


def test_immediate_settings_emit_preserves_peer_local_qwen(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = AppSettings()
    settings.provider.peer_stt = STTProviderName.LOCAL_QWEN
    changed: list[AppSettings] = []
    view, _ = _make_settings_view(monkeypatch)
    view.load_from_settings(settings, config_path=Path("settings.json"))
    view.on_settings_changed = lambda incoming: changed.append(incoming)

    view._on_low_latency_selected("off")

    assert changed
    assert changed[-1].provider.peer_stt == STTProviderName.LOCAL_QWEN
    assert settings.provider.peer_stt == STTProviderName.LOCAL_QWEN
    assert settings.stt.low_latency_mode is False


def test_overlay_controls_gate_integrated_context_until_peer_translation_is_effective(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = AppSettings()
    settings.ui.peer_translation_enabled = True
    settings.ui.integrated_context_enabled = True
    view, _ = _make_settings_view(monkeypatch)
    view.load_from_settings(settings, config_path=Path("settings.json"))

    assert view._integrated_context_button.disabled is False
    assert view._integrated_context_hint.value == ""

    view.set_overlay_peer_contract(
        build_overlay_peer_consumer_contract(
            overlay_intent_enabled=True,
            overlay_state="connected",
            overlay_failure_reason=None,
            peer_intent_enabled=True,
            peer_effective_enabled=True,
        )
    )

    assert view._integrated_context_button.disabled is False
    assert view._integrated_context_hint.value == ""


@pytest.mark.parametrize("locale", ["en", "ko", "zh-CN"])
def test_overlay_failure_contract_drives_integrated_context_copy_from_i18n(
    monkeypatch: pytest.MonkeyPatch,
    locale: str,
) -> None:
    old_locale = i18n_module.get_locale()
    try:
        i18n_module.set_locale(locale)
        settings = AppSettings()
        settings.ui.locale = locale
        settings.ui.overlay_enabled = True
        settings.ui.peer_translation_enabled = True

        view, _ = _make_settings_view(monkeypatch)
        view.load_from_settings(settings, config_path=Path("settings.json"))
        view.set_overlay_peer_contract(
            build_overlay_peer_consumer_contract(
                overlay_intent_enabled=True,
                overlay_state="failed",
                overlay_failure_reason="runtime_crashed",
                peer_intent_enabled=True,
                peer_effective_enabled=False,
            )
        )

        assert view._integrated_context_hint.value == ""
        assert view._integrated_context_button.disabled is False
    finally:
        i18n_module.set_locale(old_locale)


def test_runtime_unavailable_contract_drives_integrated_context_hint(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = AppSettings()
    settings.ui.overlay_enabled = True
    settings.ui.peer_translation_enabled = True
    settings.ui.integrated_context_enabled = True

    view, _ = _make_settings_view(monkeypatch)
    view.load_from_settings(settings, config_path=Path("settings.json"))
    view.set_overlay_peer_contract(
        build_overlay_peer_consumer_contract(
            overlay_intent_enabled=True,
            overlay_state="connected",
            overlay_failure_reason=None,
            peer_intent_enabled=True,
            peer_effective_enabled=False,
            peer_warning_reason="runtime_unavailable",
        )
    )

    assert view._integrated_context_hint.value == ""
    assert view._integrated_context_button.disabled is False


def test_overlay_stopping_contract_drives_integrated_context_hint(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = AppSettings()
    settings.ui.overlay_enabled = True
    settings.ui.peer_translation_enabled = True
    settings.ui.integrated_context_enabled = True

    view, _ = _make_settings_view(monkeypatch)
    view.load_from_settings(settings, config_path=Path("settings.json"))
    view.set_overlay_peer_contract(
        build_overlay_peer_consumer_contract(
            overlay_intent_enabled=True,
            overlay_state="stopping",
            overlay_failure_reason=None,
            peer_intent_enabled=True,
            peer_effective_enabled=False,
        )
    )

    assert view._integrated_context_hint.value == ""
    assert view._integrated_context_button.disabled is False


@pytest.mark.parametrize(
    ("locale", "expected_hint"),
    [
        ("en", "Turn on peer translation from Dashboard first."),
        ("ko", "먼저 대시보드에서 상대 번역을 켜주세요."),
        ("zh-CN", "请先在仪表板打开对方翻译。"),
    ],
)
def test_integrated_context_peer_disabled_hint_redirects_to_dashboard(
    monkeypatch: pytest.MonkeyPatch,
    locale: str,
    expected_hint: str,
) -> None:
    old_locale = i18n_module.get_locale()
    try:
        i18n_module.set_locale(locale)
        settings = AppSettings()
        settings.ui.locale = locale

        view, _ = _make_settings_view(monkeypatch)
        view.load_from_settings(settings, config_path=Path("settings.json"))
        view.set_overlay_runtime_state("connected")

        assert view._integrated_context_hint.value == ""
        assert view._integrated_context_button.disabled is False
    finally:
        i18n_module.set_locale(old_locale)


def test_peer_qwen_region_control_is_visible_before_peer_translation_is_enabled(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = AppSettings()
    settings.provider.peer_stt = STTProviderName.QWEN_ASR
    settings.ui.peer_translation_enabled = False

    view, _ = _make_settings_view(monkeypatch)
    view.load_from_settings(settings, config_path=Path("settings.json"))

    assert view._peer_qwen_region_label.visible is True
    assert view._peer_qwen_region_text.visible is True


def test_update_api_visibility_keeps_peer_auth_controls_visible_when_peer_disabled(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = AppSettings()
    settings.provider.stt = STTProviderName.LOCAL_QWEN
    settings.provider.peer_stt = STTProviderName.DEEPGRAM
    settings.provider.llm = LLMProviderName.GEMINI
    settings.ui.peer_translation_enabled = False

    view, _ = _make_settings_view(monkeypatch)
    view._settings = settings
    view._update_api_visibility()

    assert view._deepgram_key.visible is True
    assert view._soniox_key.visible is False
    assert view._google_key.visible is True


def test_update_api_visibility_keeps_peer_qwen_credentials_visible_when_peer_disabled(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = AppSettings()
    settings.provider.stt = STTProviderName.LOCAL_QWEN
    settings.provider.peer_stt = STTProviderName.QWEN_ASR
    settings.provider.llm = LLMProviderName.GEMINI
    settings.ui.peer_translation_enabled = False
    settings.peer_qwen_asr_stt.region = QwenRegion.SINGAPORE

    view, _ = _make_settings_view(monkeypatch)
    view._settings = settings
    view._update_api_visibility()

    assert view._peer_qwen_region_text.visible is True
    assert view._alibaba_key_beijing.visible is False
    assert view._alibaba_key_singapore.visible is True


def test_peer_qwen_region_override_can_be_cleared_back_to_inherited_none(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = AppSettings()
    settings.provider.peer_stt = STTProviderName.QWEN_ASR
    settings.peer_qwen_asr_stt.region = QwenRegion.BEIJING
    view, _ = _make_settings_view(monkeypatch)
    view.load_from_settings(settings, config_path=Path("settings.json"))
    attach_dummy_page(monkeypatch, view)

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

    pending = view.build_provider_apply_settings()

    assert settings.peer_qwen_asr_stt.region == QwenRegion.BEIJING
    assert pending is not None
    assert pending.peer_qwen_asr_stt.region is None
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
    attach_dummy_page(monkeypatch, view)

    model_updates: list[str] = []
    monkeypatch.setattr(
        type(view._peer_soniox_model_text),
        "update",
        lambda self: model_updates.append("peer_soniox_model_text"),
    )

    view._on_peer_soniox_model_selected("")

    pending = view.build_provider_apply_settings()

    assert settings.peer_soniox_stt.model == "stt-rt-v4"
    assert pending is not None
    assert pending.peer_soniox_stt.model is None
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
    attach_dummy_page(monkeypatch, view)

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

    pending = view.build_provider_apply_settings()

    assert settings.provider.peer_stt == STTProviderName.DEEPGRAM
    assert pending is not None
    assert pending.provider.peer_stt == STTProviderName.SONIOX
    assert view._peer_stt_text.content.value == t("provider.soniox")
    assert api_key_updates == ["peer_stt_text", "api_keys_column"]


def test_peer_provider_labels_are_backed_by_i18n(monkeypatch: pytest.MonkeyPatch) -> None:
    settings = AppSettings()
    view, _ = _make_settings_view(monkeypatch)
    view.load_from_settings(settings, config_path=Path("settings.json"))

    assert view._peer_stt_label.value == t("settings.peer_stt_provider")
    assert view._peer_qwen_region_label.value == t("settings.peer_qwen_region")


@pytest.mark.parametrize("locale", ["en", "ko", "zh-CN"])
def test_peer_stt_local_qwen_explanatory_copy_renders_from_i18n(
    monkeypatch: pytest.MonkeyPatch,
    locale: str,
) -> None:
    old_locale = i18n_module.get_locale()
    try:
        settings = AppSettings()
        settings.ui.locale = locale
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

        i18n_module.set_locale(locale)
        view.apply_locale()
        view._on_peer_stt_click(None)

        options = captured["options"]
        local_qwen_option = next(
            option for option in options if option.value == STTProviderName.LOCAL_QWEN.value
        )

        assert captured["title"] == t("settings.peer_stt_provider")
        assert local_qwen_option.description == t("provider.local_qwen.description")
    finally:
        i18n_module.set_locale(old_locale)


def test_peer_local_qwen_load_preserves_display_and_modal_current(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = AppSettings()
    settings.provider.peer_stt = STTProviderName.LOCAL_QWEN
    view, _ = _make_settings_view(monkeypatch)
    view.load_from_settings(settings, config_path=Path("settings.json"))

    assert settings.provider.peer_stt == STTProviderName.LOCAL_QWEN
    assert view._peer_stt_text.content.value == t("provider.local_qwen")

    view._peer_stt_text.content.value = "stale"
    view.apply_locale()

    assert view._peer_stt_text.content.value == t("provider.local_qwen")

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

    view._on_peer_stt_click(None)

    assert captured["title"] == t("settings.peer_stt_provider")
    assert captured["current"] == STTProviderName.LOCAL_QWEN.value


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

    assert settings.overlay.show_translation is False
    assert settings.overlay.show_peer_original is False
    assert settings_calls == [settings, settings]


def test_audio_change_updates_desktop_loopback_controls(monkeypatch: pytest.MonkeyPatch) -> None:
    settings = AppSettings()
    changed: list[AppSettings] = []
    view, _ = _make_settings_view(monkeypatch)
    view.load_from_settings(settings, config_path=Path("settings.json"))
    view.on_settings_changed = lambda incoming: changed.append(incoming)

    view._audio_settings.desktop_output_device = "Speakers (Loopback)"
    view._on_audio_change()
    view._peer_vad_field.value = "0.72"
    view._on_peer_vad_threshold_change(SimpleNamespace(control=view._peer_vad_field))
    view._peer_hangover_field.value = "950"
    view._on_peer_hangover_change(SimpleNamespace(control=view._peer_hangover_field))
    view._peer_pre_roll_field.value = "420"
    view._on_peer_pre_roll_change(SimpleNamespace(control=view._peer_pre_roll_field))

    assert settings.desktop_audio.output_device == "Speakers (Loopback)"
    assert settings.desktop_audio.vad_speech_threshold == 0.72
    assert settings.desktop_audio.vad_hangover_ms == 950
    assert settings.desktop_audio.vad_pre_roll_ms == 420
    assert changed == [settings, settings, settings, settings]


def test_general_tab_uses_three_row_layout_with_split_audio_and_vad_cards(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    view, _ = _make_settings_view(monkeypatch)
    general_controls = _subtab_controls(view, "general")

    assert len(general_controls) == 3
    assert len(general_controls[0].content.controls) == 3
    assert len(general_controls[1].content.controls) == 3
    assert len(general_controls[2].content.controls) == 3
    assert _row_card_titles(general_controls[0]) == [
        t("settings.section.ui"),
        t("settings.chatbox_include_source"),
        t("settings.integrated_context"),
    ]
    assert _row_card_titles(general_controls[1]) == [
        t("settings.audio_host_api"),
        t("settings.section.microphone_audio"),
        t("settings.section.loopback_audio"),
    ]
    assert _row_card_titles(general_controls[2]) == [
        t("settings.vrc_mic_intercept"),
        t("settings.section.self_vad_sensitivity"),
        t("settings.section.peer_vad_sensitivity"),
    ]


def test_general_tab_excludes_prompt_and_overlay_controls(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    view, _ = _make_settings_view(monkeypatch)

    general_labels: list[str] = []
    for row in _subtab_controls(view, "general"):
        general_labels.extend(_control_labels(row))

    assert t("settings.section.persona") not in general_labels
    assert t("settings.section.custom_vocabulary") not in general_labels
    assert t("settings.section.overlay") not in general_labels
    assert t("settings.overlay.enabled") not in general_labels
    assert t("settings.integrated_context") in general_labels
    assert t("settings.overlay.calibration") not in general_labels


def test_integrated_context_general_tab_uses_dedicated_unit_card(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    view, _ = _make_settings_view(monkeypatch)

    prompt_titles = _prompt_tab_card_titles(view)
    prompt_labels: list[str] = []
    for control in _subtab_controls(view, "prompt"):
        prompt_labels.extend(_control_labels(control))
    general_card = _general_tab_card(view, t("settings.integrated_context"))

    assert prompt_titles == [
        t("settings.section.custom_vocabulary"),
        t("settings.section.persona"),
    ]
    assert t("settings.integrated_context") not in prompt_labels
    assert _tree_contains_control(general_card, view._integrated_context_button)
    assert not _tree_contains_control(general_card, view._integrated_context_hint)


def test_api_tab_uses_three_row_layout_with_response_mode_and_api_keys(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    view, _ = _make_settings_view(monkeypatch)
    api_controls = _subtab_controls(view, "api")

    assert len(api_controls) == 3
    assert _row_card_titles(api_controls[0]) == [
        t("settings.section.stt"),
        t("settings.section.peer_stt"),
        t("settings.section.translation"),
    ]
    assert _row_card_titles(api_controls[1]) == [
        t("settings.low_latency_mode"),
        t("settings.openrouter_routing"),
        t("settings.openrouter_fallback"),
    ]
    assert _row_card_titles(api_controls[2]) == [t("settings.section.api_keys")]


def test_api_tab_primary_value_typography_is_consistent_across_rows(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = AppSettings()
    view, _ = _make_settings_view(monkeypatch)
    view.load_from_settings(settings, config_path=Path("settings.json"))

    assert {
        _container_text_size(view._stt_text),
        _container_text_size(view._peer_stt_text),
        _container_text_size(view._llm_text),
        _container_text_size(view._low_latency_text),
        _container_text_size(view._openrouter_routing_text),
        _container_text_size(view._openrouter_fallback_text),
    } == {28}


@pytest.mark.parametrize("locale", ["en", "ko", "zh-CN"])
def test_api_tab_single_value_cards_do_not_render_helper_copy(
    monkeypatch: pytest.MonkeyPatch,
    locale: str,
) -> None:
    old_locale = i18n_module.get_locale()
    try:
        settings = AppSettings()
        settings.ui.locale = locale
        view, _ = _make_settings_view(monkeypatch)
        view.load_from_settings(settings, config_path=Path("settings.json"))

        i18n_module.set_locale(locale)
        view.apply_locale()

        api_labels: list[str] = []
        for row in _subtab_controls(view, "api"):
            api_labels.extend(_control_labels(row))

        assert view._stt_provider_label.value == t("settings.self_stt_provider")
        assert view._translation_provider_label.value == t("settings.shared_translation_provider")
        assert view._dashboard_language_redirect_text.value == t(
            "settings.dashboard_language_redirect"
        )
        assert view._openrouter_fallback_helper_text.value in {
            t("settings.openrouter_fallback.inactive_helper"),
            t("settings.openrouter_fallback.active_helper"),
            t("settings.openrouter_fallback.none.description"),
        }
        assert t("settings.self_stt_provider") not in api_labels
        assert t("settings.shared_translation_provider") not in api_labels
        assert t("settings.peer_stt_provider") not in api_labels
        assert t("settings.dashboard_language_redirect") not in api_labels
        assert t("settings.openrouter_fallback.inactive_helper") not in api_labels
        assert t("settings.openrouter_fallback.active_helper") not in api_labels
    finally:
        i18n_module.set_locale(old_locale)


def test_general_tab_host_api_card_exposes_host_api_only(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    view, _ = _make_settings_view(monkeypatch)
    host_api_card = _general_tab_card(view, t("settings.audio_host_api"))
    host_api_labels = _control_labels(host_api_card)

    assert t("settings.desktop_audio.output_device") not in host_api_labels
    assert _tree_contains_control(host_api_card, view._audio_host_api_text)


def test_general_tab_microphone_audio_card_exposes_microphone_only(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    view, _ = _make_settings_view(monkeypatch)
    mic_audio_card = _general_tab_card(view, t("settings.section.microphone_audio"))
    mic_audio_labels = _control_labels(mic_audio_card)

    assert t("settings.audio_host_api") not in mic_audio_labels
    assert t("settings.desktop_audio.output_device") not in mic_audio_labels
    assert _tree_contains_control(mic_audio_card, view._mic_audio_text)
    assert not _tree_contains_control(mic_audio_card, view._audio_host_api_text)


def test_general_tab_loopback_audio_card_exposes_loopback_device_only(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    view, _ = _make_settings_view(monkeypatch)
    loopback_audio_card = _general_tab_card(view, t("settings.section.loopback_audio"))
    loopback_audio_labels = _control_labels(loopback_audio_card)

    assert t("settings.audio_host_api") not in loopback_audio_labels
    assert _tree_contains_control(loopback_audio_card, view._loopback_audio_text)


def test_general_tab_self_vad_card_contains_only_self_vad_slider(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    view, _ = _make_settings_view(monkeypatch)
    self_vad_card = _general_tab_card(view, t("settings.section.self_vad_sensitivity"))

    assert _tree_contains_control(self_vad_card, view._vad_slider)
    assert not _tree_contains_control(self_vad_card, view._peer_vad_field)
    assert not _tree_contains_control(self_vad_card, view._peer_hangover_field)
    assert not _tree_contains_control(self_vad_card, view._peer_pre_roll_field)


def test_general_tab_peer_vad_card_contains_peer_fields_only(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    view, _ = _make_settings_view(monkeypatch)
    peer_vad_card = _general_tab_card(view, t("settings.section.peer_vad_sensitivity"))
    peer_vad_labels = _control_labels(peer_vad_card)

    assert t("settings.vad.peer") not in peer_vad_labels
    assert t("settings.vad.peer_hangover_ms") not in peer_vad_labels
    assert t("settings.vad.peer_pre_roll_ms") not in peer_vad_labels
    assert _tree_contains_control(peer_vad_card, view._peer_vad_slider)
    assert not _tree_contains_control(peer_vad_card, view._peer_vad_field)
    assert not _tree_contains_control(peer_vad_card, view._peer_hangover_field)
    assert not _tree_contains_control(peer_vad_card, view._peer_pre_roll_field)
    assert not _tree_contains_control(peer_vad_card, view._vad_slider)


@pytest.mark.parametrize("locale", ["en", "ko", "zh-CN"])
def test_general_tab_labels_and_section_headings_render_from_i18n(
    monkeypatch: pytest.MonkeyPatch,
    locale: str,
) -> None:
    settings = AppSettings()
    settings.ui.locale = locale
    view, _ = _make_settings_view(monkeypatch)
    view.load_from_settings(settings, config_path=Path("settings.json"))

    old_locale = i18n_module.get_locale()
    try:
        i18n_module.set_locale(locale)
        view.apply_locale()

        assert view._ui_title.value == t("settings.section.ui")
        assert view._audio_host_api_title.value == t("settings.audio_host_api")
        assert view._mic_audio_title.value == t("settings.section.microphone_audio")
        assert view._loopback_audio_title.value == t("settings.section.loopback_audio")
        assert view._integrated_context_label.value == t("settings.integrated_context")
        assert view._low_latency_title.value == t("settings.low_latency_mode")
        assert view._self_vad_title.value == t("settings.section.self_vad_sensitivity")
        assert view._peer_vad_title.value == t("settings.section.peer_vad_sensitivity")
        assert view._peer_vad_field.label == t("settings.vad.peer")
        assert view._peer_hangover_field.label == t("settings.vad.peer_hangover_ms")
        assert view._peer_pre_roll_field.label == t("settings.vad.peer_pre_roll_ms")
        assert view._vrc_mic_title.value == t("settings.vrc_mic_intercept")
        assert view._chatbox_source_title.value == t("settings.chatbox_include_source")
    finally:
        i18n_module.set_locale(old_locale)


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
    assert bundle["settings.peer_translation.status.warning"]
    assert bundle["settings.peer_translation.warning.overlay_failed"]


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


@pytest.mark.parametrize("locale", ["en", "ko", "zh-CN"])
def test_overlay_apply_save_labels_render_from_i18n(
    monkeypatch: pytest.MonkeyPatch,
    locale: str,
) -> None:
    settings = AppSettings()
    settings.ui.locale = locale
    view, _ = _make_settings_view(monkeypatch)
    view.load_from_settings(settings, config_path=Path("settings.json"))

    old_locale = i18n_module.get_locale()
    try:
        i18n_module.set_locale(locale)
        view._overlay_display_options_title.value = "stale"
        view._overlay_translation_label.value = "stale"
        view._overlay_peer_original_label.value = "stale"
        view._overlay_calibration_title.value = "stale"
        view._overlay_calibration_apply_button.text = "stale"
        view._overlay_calibration_cancel_button.text = "stale"
        view._overlay_calibration_reset_button.text = "stale"

        view.apply_locale()

        display_card = _overlay_tab_card(view, t("settings.overlay.display_options"))
        calibration_card = _overlay_tab_card(view, t("settings.overlay.calibration"))
        display_labels = _control_labels(display_card)
        calibration_labels = _control_labels(calibration_card)

        assert view._overlay_display_options_title.value == t("settings.overlay.display_options")
        assert view._overlay_translation_label.value == t("settings.overlay.show_translation")
        assert view._overlay_peer_original_label.value == t("settings.overlay.show_peer_original")
        assert view._overlay_calibration_title.value == t("settings.overlay.calibration")
        assert view._overlay_calibration_apply_button.text == t(
            "settings.overlay.calibration.apply"
        )
        assert view._overlay_calibration_cancel_button.text == t(
            "settings.overlay.calibration.cancel"
        )
        assert view._overlay_calibration_reset_button.text == t(
            "settings.overlay.calibration.reset"
        )
        assert t("settings.overlay.show_translation") in display_labels
        assert t("settings.overlay.show_peer_original") in display_labels
        assert t("settings.overlay.calibration.apply") in calibration_labels
        assert t("settings.overlay.calibration.cancel") in calibration_labels
        assert t("settings.overlay.calibration.reset") in calibration_labels
    finally:
        i18n_module.set_locale(old_locale)


def test_overlay_display_options_card_contains_visibility_controls_only(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    view, _ = _make_settings_view(monkeypatch)

    overlay_titles = _overlay_tab_card_titles(view)
    display_card = _overlay_tab_card(view, t("settings.overlay.display_options"))
    display_labels = _control_labels(display_card)

    assert overlay_titles == [
        t("settings.overlay.display_options"),
        t("settings.overlay.calibration"),
    ]
    assert t("settings.overlay.enabled") not in display_labels
    assert t("settings.peer_translation") not in display_labels
    assert t("settings.overlay.show_translation") in display_labels
    assert t("settings.overlay.show_peer_original") in display_labels


def test_legacy_vr_overlay_shell_removed_from_settings_subtabs(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    view, _ = _make_settings_view(monkeypatch)

    prompt_titles = _prompt_tab_card_titles(view)
    overlay_titles = _overlay_tab_card_titles(view)
    prompt_labels: list[str] = []
    overlay_labels: list[str] = []
    for control in _subtab_controls(view, "prompt"):
        prompt_labels.extend(_control_labels(control))
    for control in _subtab_controls(view, "overlay"):
        overlay_labels.extend(_control_labels(control))

    assert prompt_titles == [
        t("settings.section.custom_vocabulary"),
        t("settings.section.persona"),
    ]
    assert overlay_titles == [
        t("settings.overlay.display_options"),
        t("settings.overlay.calibration"),
    ]
    assert t("settings.section.overlay") not in prompt_labels
    assert t("settings.section.overlay") not in overlay_labels
    assert t("settings.overlay.enabled") not in prompt_labels
    assert t("settings.overlay.enabled") not in overlay_labels
    assert t("settings.peer_translation") not in prompt_labels
    assert t("settings.peer_translation") not in overlay_labels


def test_migrated_overlay_copy_cleanup_keeps_prompt_and_overlay_context_separate(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    view, _ = _make_settings_view(monkeypatch)

    general_card = _general_tab_card(view, t("settings.integrated_context"))
    display_card = _overlay_tab_card(view, t("settings.overlay.display_options"))
    calibration_card = _overlay_tab_card(view, t("settings.overlay.calibration"))
    general_labels = _control_labels(general_card)
    display_labels = _control_labels(display_card)
    calibration_labels = _control_labels(calibration_card)

    assert t("settings.integrated_context") in general_labels
    assert t("settings.integrated_context") not in display_labels
    assert t("settings.integrated_context") not in calibration_labels
    assert t("settings.context.integrated_modal_helper") not in general_labels
    assert t("settings.overlay.show_translation") not in general_labels
    assert t("settings.overlay.show_peer_original") not in general_labels
    assert t("settings.overlay.show_translation") in display_labels
    assert t("settings.overlay.show_peer_original") in display_labels
    assert t("settings.overlay.calibration") not in general_labels
    assert t("settings.overlay.calibration") not in display_labels
    assert t("settings.overlay.calibration") in calibration_labels


def test_legacy_overlay_cleanup_copy_renders_from_i18n(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    previous_locale = i18n_module.get_locale()

    try:
        for locale in ("en", "ko", "zh-CN"):
            settings = AppSettings()
            settings.ui.locale = locale

            view, _ = _make_settings_view(monkeypatch)
            view.load_from_settings(settings, config_path=Path("settings.json"))

            i18n_module.set_locale(locale)
            view._integrated_context_label.value = "stale"
            view._integrated_context_button.text = "stale"
            view._integrated_context_hint.value = "stale"
            view._overlay_display_options_title.value = "stale"
            view._overlay_translation_label.value = "stale"
            view._overlay_peer_original_label.value = "stale"
            view._overlay_calibration_title.value = "stale"

            view.apply_locale()

            general_card = _general_tab_card(view, t("settings.integrated_context"))
            display_card = _overlay_tab_card(view, t("settings.overlay.display_options"))
            calibration_card = _overlay_tab_card(view, t("settings.overlay.calibration"))
            general_labels = _control_labels(general_card)
            display_labels = _control_labels(display_card)
            calibration_labels = _control_labels(calibration_card)

            assert view._integrated_context_label.value == t("settings.integrated_context")
            assert view._integrated_context_button.text == t("settings.context.local")
            assert view._integrated_context_hint.value == ""
            assert view._overlay_display_options_title.value == t(
                "settings.overlay.display_options"
            )
            assert view._overlay_translation_label.value == t("settings.overlay.show_translation")
            assert view._overlay_peer_original_label.value == t(
                "settings.overlay.show_peer_original"
            )
            assert view._overlay_calibration_title.value == t("settings.overlay.calibration")
            assert t("settings.integrated_context") in general_labels
            assert t("settings.context.local") in general_labels
            assert t("settings.context.integrated_modal_helper") not in general_labels
            assert t("settings.overlay.display_options") in display_labels
            assert t("settings.overlay.show_translation") in display_labels
            assert t("settings.overlay.show_peer_original") in display_labels
            assert t("settings.overlay.calibration") in calibration_labels
            assert t("settings.section.overlay") not in general_labels
            assert t("settings.section.overlay") not in display_labels
            assert t("settings.overlay.enabled") not in general_labels
            assert t("settings.overlay.enabled") not in display_labels
            assert t("settings.peer_translation") not in general_labels
            assert t("settings.peer_translation") not in display_labels
    finally:
        i18n_module.set_locale(previous_locale)


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
    settings.overlay.calibration.distance = 1.2
    settings.overlay.calibration.offset_y = 0.5
    view, _ = _make_settings_view(monkeypatch)
    view.load_from_settings(settings, config_path=Path("settings.json"))
    view.set_overlay_calibration(settings.overlay.calibration)

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


def test_overlay_calibration_reload_preserves_active_draft_until_cancel(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = AppSettings()
    settings.overlay.calibration.distance = 0.9
    view, _ = _make_settings_view(monkeypatch)
    view.load_from_settings(settings, config_path=Path("settings.json"))

    view._overlay_distance_field.value = "1.20"
    view._on_overlay_calibration_numeric_blur(
        "distance",
        SimpleNamespace(control=view._overlay_distance_field),
    )

    reloaded = AppSettings()
    reloaded.languages.source_language = "ja"
    reloaded.overlay.calibration.distance = 0.8

    view.load_from_settings(reloaded, config_path=Path("settings.json"))

    assert view._overlay_calibration_session_active is True
    assert view._overlay_distance_field.value == "1.20"
    assert view._overlay_calibration.distance == 0.8

    view._on_overlay_calibration_cancel(None)

    assert view._overlay_distance_field.value == view._format_overlay_calibration_number(0.8)


def test_overlay_calibration_reload_preserves_active_draft_until_apply(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = AppSettings()
    settings.overlay.calibration.distance = 0.9
    view, _ = _make_settings_view(monkeypatch)
    view.load_from_settings(settings, config_path=Path("settings.json"))

    view._overlay_distance_field.value = "1.20"
    view._on_overlay_calibration_numeric_blur(
        "distance",
        SimpleNamespace(control=view._overlay_distance_field),
    )

    reloaded = AppSettings()
    reloaded.languages.source_language = "ja"
    reloaded.overlay.calibration.distance = 0.8

    view.load_from_settings(reloaded, config_path=Path("settings.json"))
    view._on_overlay_calibration_apply(None)

    assert view._overlay_calibration_session_active is False
    assert view._overlay_distance_field.value == "1.20"
    assert view._overlay_calibration.distance == 1.2


def test_overlay_calibration_section_uses_dedicated_row_card(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    view, _ = _make_settings_view(monkeypatch)

    overlay_cards = _overlay_tab_cards(view)
    api_controls = _subtab_controls(view, "api")

    assert overlay_cards == [view._overlay_display_options_card, view._overlay_calibration_card]
    assert isinstance(view._overlay_display_options_card, settings_view.SharedCardWrapper)
    assert isinstance(view._overlay_calibration_card, settings_view.SharedCardWrapper)
    assert view._overlay_display_options_card.height is None
    assert view._overlay_calibration_card.height is None
    assert view._overlay_display_options_card.expand is False
    assert view._overlay_calibration_card.expand is False

    display_column = _wrapped_card_column(view._overlay_display_options_card)
    assert view._overlay_calibration_title not in display_column.controls

    calibration_column = _wrapped_card_column(view._overlay_calibration_card)
    assert calibration_column.controls[0] is view._overlay_calibration_title
    assert view._overlay_calibration_apply_button in calibration_column.controls[-1].controls
    assert view._overlay_calibration_cancel_button in calibration_column.controls[-1].controls
    assert view._overlay_calibration_reset_button in calibration_column.controls[-1].controls

    row2 = api_controls[1]
    assert row2 is view._openrouter_routing_row
    assert row2.content.controls[0] is view._low_latency_card
    assert row2.content.controls[1] is view._openrouter_routing_card
    assert row2.content.controls[2] is view._openrouter_fallback_card
    openrouter_column = row2.content.controls[1].content.controls[1].content.content
    assert openrouter_column.controls[0] is view._openrouter_routing_title
    assert openrouter_column.controls[1].content is view._openrouter_routing_text
    fallback_column = row2.content.controls[2].content.controls[1].content.content
    assert fallback_column.controls[0] is view._openrouter_fallback_title
    assert fallback_column.controls[1].content is view._openrouter_fallback_text


def test_translation_card_no_longer_contains_openrouter_routing_row(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    view, _ = _make_settings_view(monkeypatch)

    translation_card = _api_tab_card(view, t("settings.section.translation"))
    translation_column = translation_card.content.controls[1].content.content

    assert view._openrouter_routing_row not in translation_column.controls


@pytest.mark.parametrize("locale", ["en", "ko", "zh-CN"])
def test_overlay_tab_labels_and_headings_render_from_i18n(
    monkeypatch: pytest.MonkeyPatch,
    locale: str,
) -> None:
    settings = AppSettings()
    settings.ui.locale = locale
    view, _ = _make_settings_view(monkeypatch)
    view.load_from_settings(settings, config_path=Path("settings.json"))

    old_locale = i18n_module.get_locale()
    try:
        i18n_module.set_locale(locale)
        view.apply_locale()

        overlay_labels: list[str] = []
        for card in _overlay_tab_cards(view):
            overlay_labels.extend(_control_labels(card))

        assert view._overlay_display_options_title.value == t("settings.overlay.display_options")
        assert view._overlay_calibration_title.value == t("settings.overlay.calibration")
        assert view._overlay_translation_label.value == t("settings.overlay.show_translation")
        assert view._overlay_peer_original_label.value == t("settings.overlay.show_peer_original")
        assert t("settings.overlay.display_options") in overlay_labels
        assert t("settings.overlay.calibration") in overlay_labels
    finally:
        i18n_module.set_locale(old_locale)


@pytest.mark.asyncio
async def test_prompt_verify_and_emit_helpers(monkeypatch: pytest.MonkeyPatch) -> None:
    settings = AppSettings()
    changed: list[AppSettings] = []
    view, _ = _make_settings_view(monkeypatch)
    view.load_from_settings(settings, config_path=Path("settings.json"))
    view.on_settings_changed = lambda incoming: changed.append(incoming)

    view._on_prompt_change("custom prompt")
    assert settings.system_prompt != "custom prompt"
    assert view.has_pending_prompt_changes is True

    view._on_prompt_commit("custom prompt")
    assert changed[-1].system_prompt == "custom prompt"

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


def test_prompt_change_only_updates_draft_until_commit(monkeypatch: pytest.MonkeyPatch) -> None:
    settings = AppSettings()
    changed: list[AppSettings] = []
    view, _ = _make_settings_view(monkeypatch)
    view.load_from_settings(settings, config_path=Path("settings.json"))
    view.on_settings_changed = lambda incoming: changed.append(incoming)

    original_prompt = settings.system_prompt
    original_provider_prompt = settings.system_prompts[view._active_prompt_key()]

    view._on_prompt_change("custom prompt")

    pending = view.build_provider_apply_settings()

    assert settings.system_prompt == original_prompt
    assert settings.system_prompts[view._active_prompt_key()] == original_provider_prompt
    assert view.has_pending_prompt_changes is True
    assert pending is not None
    assert pending.system_prompt == "custom prompt"
    assert pending.system_prompts[view._active_prompt_key()] == "custom prompt"
    assert changed == []


def test_prompt_commit_emits_once_when_no_provider_changes(monkeypatch: pytest.MonkeyPatch) -> None:
    settings = AppSettings()
    changed: list[AppSettings] = []
    view, _ = _make_settings_view(monkeypatch)
    view.load_from_settings(settings, config_path=Path("settings.json"))
    view.on_settings_changed = lambda incoming: changed.append(incoming)

    view._on_prompt_change("custom prompt")
    view._on_prompt_commit("custom prompt")

    assert view.has_pending_prompt_changes is False
    assert changed
    assert changed[-1].system_prompt == "custom prompt"
    assert changed[-1].system_prompts[view._active_prompt_key()] == "custom prompt"


def test_prompt_commit_preserves_peer_local_qwen_before_emit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = AppSettings()
    settings.provider.peer_stt = STTProviderName.LOCAL_QWEN
    changed: list[AppSettings] = []
    view, _ = _make_settings_view(monkeypatch)
    view.load_from_settings(settings, config_path=Path("settings.json"))
    view.on_settings_changed = lambda incoming: changed.append(incoming)

    view._on_prompt_change("custom prompt")
    view._on_prompt_commit("custom prompt")

    assert changed
    assert changed[-1] is not settings
    assert changed[-1].provider.peer_stt == STTProviderName.LOCAL_QWEN
    assert settings.provider.peer_stt == STTProviderName.LOCAL_QWEN
    assert changed[-1].system_prompt == "custom prompt"


def test_prompt_commit_noops_when_value_is_unchanged(monkeypatch: pytest.MonkeyPatch) -> None:
    settings = AppSettings()
    changed: list[AppSettings] = []
    view, _ = _make_settings_view(monkeypatch)
    view.load_from_settings(settings, config_path=Path("settings.json"))
    view.on_settings_changed = lambda incoming: changed.append(incoming)

    current_prompt = view._prompt_editor.value
    view._on_prompt_commit(current_prompt)

    assert changed == []
    assert view.has_pending_prompt_changes is False


def test_prompt_reverting_to_committed_value_clears_pending_state(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = AppSettings()
    changed: list[AppSettings] = []
    view, _ = _make_settings_view(monkeypatch)
    view.load_from_settings(settings, config_path=Path("settings.json"))
    view.on_settings_changed = lambda incoming: changed.append(incoming)

    original_prompt = view._prompt_editor.value

    view._on_prompt_change("temporary prompt")
    assert view.has_pending_prompt_changes is True

    view._on_prompt_change(original_prompt)
    view._on_prompt_commit(original_prompt)

    assert view.has_pending_prompt_changes is False
    assert changed == []


def test_refresh_prompt_if_empty_stages_default_for_apply(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = AppSettings()
    view, _ = _make_settings_view(monkeypatch)
    view.load_from_settings(settings, config_path=Path("settings.json"))

    prompt_key = view._active_prompt_key()
    view._settings.system_prompt = ""
    view._settings.system_prompts[prompt_key] = ""
    view._provider_settings_draft = None
    view.has_provider_changes = False
    view.has_pending_prompt_changes = False
    view._prompt_editor.value = ""

    view.refresh_prompt_if_empty()
    pending = view.build_provider_apply_settings()

    assert bool(view._prompt_editor.value.strip())
    assert view.has_pending_prompt_changes is True
    assert pending is not None
    assert pending.system_prompt == view._prompt_editor.value
    assert pending.system_prompts[prompt_key] == view._prompt_editor.value


def test_on_text_hover_updates_container_once(monkeypatch: pytest.MonkeyPatch) -> None:
    view, _ = _make_settings_view(monkeypatch)
    updates: list[str] = []
    text_control = SimpleNamespace(color=settings_view.COLOR_ON_BACKGROUND)
    container = SimpleNamespace(
        content=text_control,
        update=lambda: updates.append(text_control.color),
    )

    view._on_text_hover(SimpleNamespace(control=container, data="true"))

    assert text_control.color == settings_view.COLOR_PRIMARY
    assert len(updates) == 1


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
    assert view._openrouter_routing_title.value == t("settings.openrouter_routing")


def test_low_latency_card_title_uses_response_mode_copy_in_korean(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    old_locale = i18n_module.get_locale()
    try:
        i18n_module.set_locale("ko")
        settings = AppSettings()
        settings.ui.locale = "ko"
        view, _ = _make_settings_view(monkeypatch)
        view.load_from_settings(settings, config_path=Path("settings.json"))
        view.apply_locale()

        assert view._low_latency_title.value == "응답 방식"
    finally:
        i18n_module.set_locale(old_locale)


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
    expected_peer_qwen_region_label = ""
    expected_peer_qwen_model_label = ""
    expected_peer_soniox_model_label = ""
    expected_inherit_label = ""
    try:
        i18n_module.set_locale("ko")
        view._peer_stt_label.value = "stale"
        view._peer_qwen_region_label.value = "stale"
        view._peer_qwen_model_label.value = "stale"
        view._peer_soniox_model_label.value = "stale"
        view._peer_qwen_region_text.content.value = "stale"
        view._peer_qwen_model_text.content.value = "stale"
        view._peer_soniox_model_text.content.value = "stale"

        view.apply_locale()
        expected_peer_stt_label = t("settings.peer_stt_provider")
        expected_peer_qwen_region_label = t("settings.peer_qwen_region")
        expected_peer_qwen_model_label = t("settings.peer_qwen_model")
        expected_peer_soniox_model_label = t("settings.peer_soniox_model")
        expected_inherit_label = t("settings.peer_provider.follow_self")
    finally:
        i18n_module.set_locale(old_locale)

    assert view._peer_stt_label.value == expected_peer_stt_label
    assert view._peer_qwen_region_label.value == expected_peer_qwen_region_label
    assert view._peer_qwen_model_label.value == expected_peer_qwen_model_label
    assert view._peer_soniox_model_label.value == expected_peer_soniox_model_label
    assert view._peer_qwen_region_text.content.value == expected_inherit_label
    assert view._peer_qwen_model_text.content.value == expected_inherit_label
    assert view._peer_soniox_model_text.content.value == expected_inherit_label


@pytest.mark.parametrize(
    ("locale", "expected_title", "expected_redirect"),
    [
        (
            "en",
            "Peer Speech Recognition",
            "Change self and peer language pairs from the Dashboard language card.",
        ),
        (
            "ko",
            "상대 음성 인식",
            "셀프와 상대 언어 조합은 대시보드 언어 카드에서 바꿔주세요.",
        ),
        (
            "zh-CN",
            "对方语音识别",
            "请在仪表板的语言卡片中修改自己与对方的语言组合。",
        ),
    ],
)
def test_peer_language_migration_copy_renders_from_i18n(
    monkeypatch: pytest.MonkeyPatch,
    locale: str,
    expected_title: str,
    expected_redirect: str,
) -> None:
    old_locale = i18n_module.get_locale()
    try:
        settings = AppSettings()
        settings.ui.locale = locale
        view, _ = _make_settings_view(monkeypatch)
        view.load_from_settings(settings, config_path=Path("settings.json"))

        i18n_module.set_locale(locale)
        view.apply_locale()

        assert view._peer_provider_title.value == expected_title
        assert view._dashboard_language_redirect_text.value == expected_redirect

        if locale != "en":
            assert view._peer_provider_title.value != "Peer Speech Recognition"
            assert (
                view._dashboard_language_redirect_text.value
                != "Change self and peer language pairs from the Dashboard language card."
            )
    finally:
        i18n_module.set_locale(old_locale)


def test_settings_view_does_not_create_peer_deepgram_model_controls(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    view, _ = _make_settings_view(monkeypatch)

    assert not hasattr(view, "_peer_deepgram_model_label")
    assert not hasattr(view, "_peer_deepgram_model_text")


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
    attach_dummy_page(monkeypatch, view)

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
    attach_dummy_page(monkeypatch, view)
    monkeypatch.setattr(type(view._vrc_mic_text), "update", lambda self: None)

    view._on_vrc_mic_selected("on")

    assert settings.osc.vrc_mic_intercept is True
    assert view._vrc_mic_text.content.value == t("settings.vrc_mic.on")
    assert changed == [settings]


def test_on_integrated_context_click_opens_modal_with_current_selection_and_helper_copy(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = AppSettings()
    settings.ui.integrated_context_enabled = True
    view, _ = _make_settings_view(monkeypatch)
    view.load_from_settings(settings, config_path=Path("settings.json"))
    attach_dummy_page(monkeypatch, view)

    captured: dict[str, object] = {}

    class DummyModal:
        def __init__(self, _page, title, options, _on_select, *, show_description=False):
            captured["title"] = title
            captured["options"] = options
            captured["show_description"] = show_description

        def open(self, current: str) -> None:
            captured["current"] = current

    monkeypatch.setattr(settings_view, "SettingsModal", DummyModal)

    view._on_integrated_context_click(None)

    options = captured["options"]
    assert captured["title"] == t("settings.integrated_context")
    assert captured["show_description"] is True
    assert [option.value for option in options] == ["off", "on"]
    assert [option.label for option in options] == [
        t("settings.context.local"),
        t("settings.context.integrated"),
    ]
    assert options[0].description == ""
    assert options[1].description == t("settings.context.integrated_modal_helper")
    assert captured["current"] == "on"


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

    custom_vocab_card = _prompt_tab_card(view, t("settings.section.custom_vocabulary"))
    custom_vocab_column = _wrapped_card_column(custom_vocab_card)
    header = custom_vocab_column.controls[0]

    assert isinstance(header, settings_view.ft.Row)
    assert header.controls[0] is view._custom_vocab_title
    assert header.controls[-1] is view._custom_vocab_info_icon
    assert view._custom_vocab_info_icon.tooltip == t("settings.custom_vocabulary_tooltip")


def test_prompt_tab_uses_shared_full_width_cards(monkeypatch: pytest.MonkeyPatch) -> None:
    from puripuly_heart.ui.components.shared_card_wrapper import SharedCardWrapper

    view, _ = _make_settings_view(monkeypatch)

    prompt_cards = _subtab_controls(view, "prompt")

    assert len(prompt_cards) == 2
    assert all(isinstance(card, SharedCardWrapper) for card in prompt_cards)
    assert all(card.height is None for card in prompt_cards)
    assert all(card.expand is False for card in prompt_cards)


def test_prompt_tab_hides_prompt_provider_copy_and_language_helper_text(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = AppSettings()
    settings.ui.locale = "ko"
    settings.languages.source_language = "zh-CN"
    settings.provider.llm = LLMProviderName.GEMINI

    old_locale = i18n_module.get_locale()
    try:
        view, _ = _make_settings_view(monkeypatch)
        view.load_from_settings(settings, config_path=Path("settings.json"))

        i18n_module.set_locale("ko")
        view.apply_locale()

        prompt_card = _prompt_tab_card(view, t("settings.section.persona"))
        custom_vocab_card = _prompt_tab_card(view, t("settings.section.custom_vocabulary"))

        assert t(
            "settings.prompt_for",
            provider=provider_label(LLMProviderName.GEMINI.value),
        ) not in _control_labels(prompt_card)
        assert t(
            "settings.custom_vocabulary_helper",
            language=language_name("zh-CN"),
        ) not in _control_labels(custom_vocab_card)
    finally:
        i18n_module.set_locale(old_locale)


@pytest.mark.parametrize(
    ("locale", "expected_tooltip"),
    [
        (
            "ko",
            "자신의 말하는 음성에만 적용이 되어요\n또한 Deepgram과 Soniox 사용 시에만 설정이 쓰여요",
        ),
        ("en", "Only applies to your speech.\nOnly applies to Deepgram and Soniox."),
        ("zh-CN", "仅适用于你的语音。\n仅适用于 Deepgram 和 Soniox。"),
    ],
)
def test_custom_vocabulary_tooltip_copy_matches_new_provider_scope(
    monkeypatch: pytest.MonkeyPatch,
    locale: str,
    expected_tooltip: str,
) -> None:
    settings = AppSettings()
    settings.ui.locale = locale

    old_locale = i18n_module.get_locale()
    try:
        view, _ = _make_settings_view(monkeypatch)
        view.load_from_settings(settings, config_path=Path("settings.json"))

        i18n_module.set_locale(locale)
        view.apply_locale()

        assert view._custom_vocab_info_icon.tooltip == expected_tooltip
    finally:
        i18n_module.set_locale(old_locale)


@pytest.mark.parametrize(
    ("locale", "expected_title"),
    [
        ("ko", "음성 인식 힌트"),
        ("en", "Speech Recognition Hints"),
        ("zh-CN", "语音识别提示"),
    ],
)
def test_custom_vocabulary_card_title_uses_generic_hint_wording(
    monkeypatch: pytest.MonkeyPatch,
    locale: str,
    expected_title: str,
) -> None:
    settings = AppSettings()
    settings.ui.locale = locale

    old_locale = i18n_module.get_locale()
    try:
        view, _ = _make_settings_view(monkeypatch)
        view.load_from_settings(settings, config_path=Path("settings.json"))

        i18n_module.set_locale(locale)
        view.apply_locale()

        assert view._custom_vocab_title.value == expected_title
    finally:
        i18n_module.set_locale(old_locale)


def test_settings_api_unit_cards_use_settings_unit_card_defaults(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from puripuly_heart.ui.components.settings.settings_unit_card import SettingsUnitCard

    view, _ = _make_settings_view(monkeypatch)

    unit_cards = [
        _api_tab_card(view, t("settings.section.stt")),
        _api_tab_card(view, t("settings.section.peer_stt")),
        _api_tab_card(view, t("settings.section.translation")),
        _api_tab_card(view, t("settings.low_latency_mode")),
        view._openrouter_routing_card,
        view._openrouter_fallback_card,
    ]

    assert all(isinstance(card, SettingsUnitCard) for card in unit_cards)
    assert {card.height for card in unit_cards} == {SettingsUnitCard.DEFAULT_HEIGHT}
    assert all(card.expand is True for card in unit_cards)


def test_general_cards_use_settings_unit_card_defaults(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from puripuly_heart.ui.components.settings.settings_unit_card import SettingsUnitCard

    view, _ = _make_settings_view(monkeypatch)

    general_cards = [
        _general_tab_card(view, t("settings.section.ui")),
        _general_tab_card(view, t("settings.chatbox_include_source")),
        _general_tab_card(view, t("settings.integrated_context")),
        _general_tab_card(view, t("settings.vrc_mic_intercept")),
        _general_tab_card(view, t("settings.audio_host_api")),
        _general_tab_card(view, t("settings.section.microphone_audio")),
        _general_tab_card(view, t("settings.section.loopback_audio")),
        _general_tab_card(view, t("settings.section.self_vad_sensitivity")),
        _general_tab_card(view, t("settings.section.peer_vad_sensitivity")),
    ]

    assert all(isinstance(card, SettingsUnitCard) for card in general_cards)
    assert {card.height for card in general_cards} == {SettingsUnitCard.DEFAULT_HEIGHT}
    assert all(card.expand is True for card in general_cards)
    assert all(getattr(row, "height", None) is None for row in _subtab_controls(view, "general"))
    text_style = view._integrated_context_button.style.text_style[ft.ControlState.DEFAULT]
    assert text_style.size == 28
    assert view._openrouter_routing_row.height is None


def test_api_keys_card_uses_shared_full_width_auto_height(monkeypatch: pytest.MonkeyPatch) -> None:
    from puripuly_heart.ui.components.shared_card_wrapper import SharedCardWrapper

    view, _ = _make_settings_view(monkeypatch)

    api_card = _api_tab_card(view, t("settings.section.api_keys"))

    assert isinstance(api_card, SharedCardWrapper)
    assert api_card.height is None
    assert api_card.expand is False


def test_api_keys_card_omits_helper_copy_and_keeps_qwen_region_button_in_header(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = AppSettings()
    settings.provider.llm = LLMProviderName.QWEN
    settings.qwen.region = QwenRegion.SINGAPORE

    view, _ = _make_settings_view(monkeypatch)
    view.load_from_settings(settings, config_path=Path("settings.json"))

    api_card = _api_tab_card(view, t("settings.section.api_keys"))
    api_column = _wrapped_card_column(api_card)
    api_header = api_column.controls[0]

    assert view._qwen_region_btn.visible is True
    assert isinstance(api_header, ft.Row)
    assert api_header.controls[0] is view._api_title
    assert api_header.controls[2] is view._qwen_region_btn
    assert view._api_credentials_helper_text not in api_column.controls
    assert t("settings.api_credentials_helper") not in _control_labels(api_card)


def test_api_provider_row_does_not_override_shared_card_height(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    view, _ = _make_settings_view(monkeypatch)

    api_provider_row = _subtab_controls(view, "api")[0]

    assert api_provider_row.height is None


def test_integrated_context_controls_are_removed_from_overlay_tab(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    view, _ = _make_settings_view(monkeypatch)

    overlay_labels: list[str] = []
    for control in _subtab_controls(view, "overlay"):
        overlay_labels.extend(_control_labels(control))

    assert t("settings.integrated_context") not in overlay_labels
    assert not any(
        _tree_contains_control(control, view._integrated_context_button)
        or _tree_contains_control(control, view._integrated_context_hint)
        for control in _subtab_controls(view, "overlay")
    )


def test_integrated_context_general_card_labels_render_from_i18n(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = AppSettings()
    settings.ui.locale = "ko"

    view, _ = _make_settings_view(monkeypatch)
    view.load_from_settings(settings, config_path=Path("settings.json"))

    old_locale = i18n_module.get_locale()
    try:
        i18n_module.set_locale("ko")
        view.apply_locale()

        general_card = _general_tab_card(view, t("settings.integrated_context"))
        general_labels = _control_labels(general_card)

        assert view._integrated_context_label.value == t("settings.integrated_context")
        assert view._integrated_context_button.text == t("settings.context.local")
        assert view._integrated_context_hint.value == ""
        assert t("settings.integrated_context") in general_labels
        assert t("settings.context.local") in general_labels
        assert t("settings.context.integrated_modal_helper") not in general_labels
    finally:
        i18n_module.set_locale(old_locale)


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
    assert view._custom_vocab_helper_text.value == (
        f"One term per line for {language_name('en')}. Changes save when you leave this field."
    )


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
) -> None:
    settings = AppSettings()
    settings.languages.source_language = "ko"
    detailed_messages: list[str] = []

    view, _ = _make_settings_view(monkeypatch)
    view.load_from_settings(settings, config_path=Path("settings.json"))
    view.runtime_log_detailed = lambda message, *, level=logging.INFO: detailed_messages.append(
        message
    )
    view._custom_vocab_terms.value = "Puripuly\nVRChat"
    view._on_custom_vocabulary_terms_change(None)

    view._on_custom_vocabulary_terms_blur(None)

    assert detailed_messages == ["[Settings] Custom vocabulary applied: language=ko, terms=2"]


def test_on_qwen_region_selected_uses_detailed_runtime_log(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = AppSettings()
    detailed_messages: list[str] = []

    view, _ = _make_settings_view(monkeypatch)
    view.load_from_settings(settings, config_path=Path("settings.json"))
    view.runtime_log_detailed = lambda message, *, level=logging.INFO: detailed_messages.append(
        message
    )

    view._on_qwen_region_selected(QwenRegion.SINGAPORE.value)

    assert detailed_messages == ["[Settings] Qwen region changed: beijing -> singapore"]


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


def test_settings_view_uses_generic_subtab_shell(monkeypatch: pytest.MonkeyPatch) -> None:
    from puripuly_heart.ui.components.subtab_shell import TextSubtabShell

    view, _ = _make_settings_view(monkeypatch)

    assert view.scroll is None
    assert view.controls == [view._settings_subtab_shell]
    assert isinstance(view._settings_subtab_shell, TextSubtabShell)
    assert isinstance(view._settings_subtab_shell.body_host, ft.Stack)
    assert view._settings_subtab_shell.title_region is None
    assert isinstance(view._settings_subtab_shell.body_region, ft.Container)
    assert view._settings_subtab_shell.body_region.content is view._settings_subtab_shell.body_host
    assert view._settings_subtab_shell.body_region.padding == ft.padding.only(
        left=16, top=16, right=16
    )
    assert view._settings_subtab_shell.controls == [
        view._settings_subtab_shell.body_region,
        view._settings_subtab_shell.subtab_bar,
    ]


def test_settings_subtab_shell_preserves_per_tab_scroll_state(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    view, _ = _make_settings_view(monkeypatch)
    shell = view._settings_subtab_shell

    api_body = shell.body_by_key["api"]
    general_body = shell.body_by_key["general"]

    shell.record_scroll("api", SimpleNamespace(pixels=144.0))
    shell.select_tab("general")
    shell.record_scroll("general", SimpleNamespace(pixels=320.0))
    shell.select_tab("api")

    assert shell.active_key == "api"
    assert api_body.scroll == ft.ScrollMode.AUTO
    assert general_body.scroll == ft.ScrollMode.AUTO
    assert shell.scroll_offsets["api"] == 144.0
    assert shell.scroll_offsets["general"] == 320.0
    assert api_body.visible is True
    assert general_body.visible is False


def test_settings_subtab_shell_restores_scroll_on_tab_switch_for_mounted_body(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    view, _ = _make_settings_view(monkeypatch)
    shell = view._settings_subtab_shell
    api_body = shell.body_by_key["api"]
    scroll_calls: list[tuple[float, int]] = []

    monkeypatch.setattr(type(api_body), "page", property(lambda self: object()))
    monkeypatch.setattr(
        api_body,
        "scroll_to",
        lambda **kwargs: scroll_calls.append((kwargs["offset"], kwargs["duration"])),
    )

    shell.record_scroll("api", SimpleNamespace(pixels=144.0))
    shell.select_tab("general")
    shell.select_tab("api")

    assert scroll_calls == [(144.0, 0)]


def test_settings_subtab_bar_matches_bottom_nav_family_structure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    view, _ = _make_settings_view(monkeypatch)
    shell = view._settings_subtab_shell
    main_nav = BottomNavBar(on_change=lambda _idx: None)

    buttons = [shell.button_by_key[key] for key in settings_view._SETTINGS_SUBTAB_ORDER]
    dividers = [
        control for control in shell.subtab_row.controls if isinstance(control, ft.VerticalDivider)
    ]
    border = shell.subtab_bar.border
    nav_row = main_nav.content
    nav_dividers = [
        control for control in nav_row.controls if isinstance(control, ft.VerticalDivider)
    ]

    assert isinstance(shell.subtab_bar.content, ft.Row)
    assert shell.controls[-1] is shell.subtab_bar
    assert shell.spacing == 0
    assert shell.subtab_row.expand is True
    assert shell.subtab_row.wrap is False
    assert shell.subtab_row.scroll is None
    assert shell.subtab_row.spacing == 0
    assert all(isinstance(button, ft.Container) for button in buttons)
    assert all(button.expand is True for button in buttons)
    assert all(button.alignment == ft.alignment.center for button in buttons)
    assert all(callable(button.on_click) for button in buttons)
    assert all(callable(button.on_hover) for button in buttons)
    assert all(isinstance(button.content, ft.Text) for button in buttons)
    assert len(dividers) == len(settings_view._SETTINGS_SUBTAB_ORDER) - 1
    assert shell.subtab_bar.bgcolor == main_nav.bgcolor
    assert shell.subtab_bar.height == int(main_nav.height * 0.8)
    assert shell.subtab_bar.border_radius is None
    assert border.top.width == main_nav.border.top.width
    assert border.top.color == main_nav.border.top.color
    assert border.left is None
    assert border.right is None
    assert border.bottom is None
    assert shell.subtab_bar.padding is None
    assert len(nav_dividers) == len(dividers)
    assert all(divider.width == nav_dividers[0].width for divider in dividers)
    assert all(divider.thickness == nav_dividers[0].thickness for divider in dividers)
    assert all(divider.color == nav_dividers[0].color for divider in dividers)
    assert all(_subtab_text_size(button) == 20 for button in buttons)
    assert _subtab_text_color(buttons[0]) == subtab_shell_module.COLOR_PRIMARY
    assert _subtab_text_color(buttons[1]) == subtab_shell_module.COLOR_NEUTRAL

    buttons[1].on_hover(SimpleNamespace(data="true"))
    assert _subtab_text_color(buttons[1]) == subtab_shell_module.COLOR_PRIMARY

    buttons[1].on_hover(SimpleNamespace(data="false"))
    assert _subtab_text_color(buttons[1]) == subtab_shell_module.COLOR_NEUTRAL

    buttons[1].on_click(SimpleNamespace())
    assert shell.active_key == "general"
    assert _subtab_text_color(buttons[0]) == subtab_shell_module.COLOR_NEUTRAL
    assert _subtab_text_color(buttons[1]) == subtab_shell_module.COLOR_PRIMARY


def test_text_subtab_shell_keeps_floating_treatment_when_bar_is_top() -> None:
    from puripuly_heart.ui.components.subtab_shell import TextSubtab, TextSubtabShell

    shell = TextSubtabShell(
        title=ft.Text("Settings"),
        tabs=[
            TextSubtab("api", "API", (ft.Text("One"),)),
            TextSubtab("general", "General", (ft.Text("Two"),)),
        ],
    )

    assert shell.controls == [shell.title_region, shell.subtab_bar, shell.body_host]
    assert shell.spacing == 16
    assert shell.subtab_row.scroll == ft.ScrollMode.AUTO
    assert shell.subtab_row.spacing == 8
    assert shell.subtab_bar.bgcolor == subtab_shell_module.COLOR_SURFACE
    assert shell.subtab_bar.border_radius == 24
    assert _button_style_value(shell.button_by_key["api"], "bgcolor") == (
        subtab_shell_module.COLOR_PRIMARY_CONTAINER
    )
    assert _button_style_value(shell.button_by_key["general"], "bgcolor") == (ft.Colors.TRANSPARENT)


def test_settings_subtab_labels_render_from_i18n(monkeypatch: pytest.MonkeyPatch) -> None:
    view, _ = _make_settings_view(monkeypatch)
    previous_locale = i18n_module.get_locale()

    try:
        i18n_module.set_locale("ko")
        view.apply_locale()

        assert [
            _subtab_text_value(view._settings_subtab_shell.button_by_key[key])
            for key in settings_view._SETTINGS_SUBTAB_ORDER
        ] == [
            t("settings.subtab.api"),
            t("settings.subtab.general"),
            t("settings.subtab.prompt"),
            t("settings.subtab.overlay"),
        ]
    finally:
        i18n_module.set_locale(previous_locale)


def test_text_subtab_shell_rejects_duplicate_keys() -> None:
    from puripuly_heart.ui.components.subtab_shell import TextSubtab, TextSubtabShell

    with pytest.raises(ValueError, match="unique tab keys"):
        TextSubtabShell(
            title=ft.Text("Settings"),
            tabs=[
                TextSubtab("api", "API", (ft.Text("One"),)),
                TextSubtab("api", "Again", (ft.Text("Two"),)),
            ],
        )


def test_text_subtab_shell_rejects_unknown_initial_key() -> None:
    from puripuly_heart.ui.components.subtab_shell import TextSubtab, TextSubtabShell

    with pytest.raises(ValueError, match="Unknown initial tab key"):
        TextSubtabShell(
            title=ft.Text("Settings"),
            tabs=[
                TextSubtab("api", "API", (ft.Text("One"),)),
                TextSubtab("general", "General", (ft.Text("Two"),)),
            ],
            initial_key="overlay",
        )


def test_text_subtab_shell_can_render_without_title_and_pin_subtab_bar_to_bottom() -> None:
    from puripuly_heart.ui.components.subtab_shell import TextSubtab, TextSubtabShell

    shell = TextSubtabShell(
        title=None,
        tabs=[
            TextSubtab("api", "API", (ft.Text("One"),)),
            TextSubtab("general", "General", (ft.Text("Two"),)),
        ],
        subtab_bar_position="bottom",
    )

    assert shell.title_region is None
    assert isinstance(shell.body_region, ft.Container)
    assert shell.body_region.content is shell.body_host
    assert shell.controls == [shell.body_region, shell.subtab_bar]
