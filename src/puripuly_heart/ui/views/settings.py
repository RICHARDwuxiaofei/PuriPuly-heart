"""Settings view - redesigned with component composition."""

from __future__ import annotations

import contextlib
import logging
from pathlib import Path
from typing import Callable

import flet as ft

from puripuly_heart.app.wiring import create_secret_store
from puripuly_heart.config.settings import (
    AppSettings,
    LLMProviderName,
    QwenRegion,
    STTProviderName,
)
from puripuly_heart.core.language import get_stt_compatibility_warning
from puripuly_heart.ui.components.settings import (
    ApiKeyField,
    AudioSettings,
    PromptEditor,
    ProviderSelector,
    SettingsSection,
)
from puripuly_heart.ui.i18n import available_locales, language_name, locale_label, t
from puripuly_heart.ui.theme import COLOR_NEUTRAL_DARK

logger = logging.getLogger(__name__)


def _load_secret_value(store, key: str, *, legacy_keys: tuple[str, ...] = ()) -> str:
    """Load secret value with legacy key fallback."""
    value = store.get(key) or ""
    if value or not legacy_keys:
        return value
    for legacy_key in legacy_keys:
        legacy_value = store.get(legacy_key) or ""
        if legacy_value:
            with contextlib.suppress(Exception):
                store.set(key, legacy_value)
            return legacy_value
    return ""


class SettingsView(ft.Column):
    """Redesigned settings view with component composition."""

    def __init__(self):
        super().__init__(expand=True, scroll=ft.ScrollMode.AUTO, spacing=16)

        # Callbacks (assigned by App)
        self.on_settings_changed: Callable[[AppSettings], None] | None = None
        self.on_providers_changed: Callable[[], None] | None = None
        self.on_verify_api_key: Callable[[str, str], object] | None = None

        # State
        self._settings: AppSettings | None = None
        self._config_path: Path | None = None
        self.has_provider_changes: bool = False

        # Build UI components
        self._build_ui()

    def _build_ui(self) -> None:
        """Build the settings UI with sections."""
        # --- UI Section ---
        self._ui_language = ft.Dropdown(
            label=t("settings.ui_language"),
            options=self._build_locale_options(),
            on_change=self._on_ui_language_change,
            border_radius=12,
            text_size=28,
            label_style=ft.TextStyle(size=20, weight=ft.FontWeight.BOLD),
            color=COLOR_NEUTRAL_DARK,
        )
        self._ui_section = SettingsSection(
            "settings.section.ui",
            content=self._ui_language,
        )

        # --- STT Section ---
        self._stt_provider = ProviderSelector(
            "settings.stt_provider",
            STTProviderName,
            on_change=self._on_stt_provider_change,
        )
        self._deepgram_key = ApiKeyField(
            "settings.deepgram_api_key",
            "deepgram_api_key",
            "deepgram",
            on_verify=self._verify_key,
            on_change=self._on_secret_change,
        )
        self._soniox_key = ApiKeyField(
            "settings.soniox_api_key",
            "soniox_api_key",
            "soniox",
            on_verify=self._verify_key,
            on_change=self._on_secret_change,
        )
        # Qwen fields for STT (shared reference)
        self._stt_qwen_region = self._build_qwen_region_dropdown()
        self._stt_qwen_key_beijing = ApiKeyField(
            "settings.alibaba_api_key_beijing",
            "alibaba_api_key_beijing",
            "alibaba_beijing",
            on_verify=self._verify_key,
            on_change=self._on_secret_change,
        )
        self._stt_qwen_key_singapore = ApiKeyField(
            "settings.alibaba_api_key_singapore",
            "alibaba_api_key_singapore",
            "alibaba_singapore",
            on_verify=self._verify_key,
            on_change=self._on_secret_change,
        )

        self._stt_fields = ft.Column(
            [
                self._deepgram_key,
                self._soniox_key,
                self._stt_qwen_region,
                self._stt_qwen_key_beijing,
                self._stt_qwen_key_singapore,
            ],
            spacing=12,
        )

        self._stt_section = SettingsSection(
            "settings.section.stt",
            content=ft.Column([self._stt_provider, self._stt_fields], spacing=16),
        )

        # --- Translation Section ---
        self._llm_provider = ProviderSelector(
            "settings.llm_provider",
            LLMProviderName,
            on_change=self._on_llm_provider_change,
        )
        self._google_key = ApiKeyField(
            "settings.google_api_key",
            "google_api_key",
            "google",
            on_verify=self._verify_key,
            on_change=self._on_secret_change,
        )
        # Qwen fields for Translation (shared reference)
        self._trans_qwen_region = self._build_qwen_region_dropdown()
        self._trans_qwen_key_beijing = ApiKeyField(
            "settings.alibaba_api_key_beijing",
            "alibaba_api_key_beijing",
            "alibaba_beijing",
            on_verify=self._verify_key,
            on_change=self._on_secret_change,
        )
        self._trans_qwen_key_singapore = ApiKeyField(
            "settings.alibaba_api_key_singapore",
            "alibaba_api_key_singapore",
            "alibaba_singapore",
            on_verify=self._verify_key,
            on_change=self._on_secret_change,
        )

        self._trans_fields = ft.Column(
            [
                self._google_key,
                self._trans_qwen_region,
                self._trans_qwen_key_beijing,
                self._trans_qwen_key_singapore,
            ],
            spacing=12,
        )

        self._translation_section = SettingsSection(
            "settings.section.translation",
            content=ft.Column([self._llm_provider, self._trans_fields], spacing=16),
        )

        # --- Audio Section ---
        self._audio_settings = AudioSettings(on_change=self._on_audio_change)
        self._audio_section = SettingsSection(
            "settings.section.audio",
            content=self._audio_settings,
        )

        # --- Persona Section ---
        self._prompt_editor = PromptEditor(on_change=self._on_prompt_change)
        self._persona_section = SettingsSection(
            "settings.section.persona",
            content=self._prompt_editor,
        )

        # Title
        self._title = ft.Text(
            t("settings.title"),
            size=28,
            weight=ft.FontWeight.BOLD,
            color=COLOR_NEUTRAL_DARK,
        )

        self.controls = [
            self._title,
            self._ui_section,
            self._stt_section,
            self._translation_section,
            self._audio_section,
            self._persona_section,
        ]

    def _build_locale_options(self) -> list[ft.dropdown.Option]:
        """Build locale dropdown options."""
        return [
            ft.dropdown.Option(key=code, text=locale_label(code)) for code in available_locales()
        ]

    def _build_qwen_region_dropdown(self) -> ft.Dropdown:
        """Build Qwen region dropdown."""
        return ft.Dropdown(
            label=t("settings.qwen_region"),
            options=[
                ft.dropdown.Option(key=QwenRegion.BEIJING.value, text=t("region.beijing")),
                ft.dropdown.Option(key=QwenRegion.SINGAPORE.value, text=t("region.singapore")),
            ],
            on_change=self._on_qwen_region_change,
            border_radius=12,
            text_size=28,
            label_style=ft.TextStyle(size=20, weight=ft.FontWeight.BOLD),
            color=COLOR_NEUTRAL_DARK,
        )

    # --- Load Settings ---
    def load_from_settings(self, settings: AppSettings, *, config_path: Path) -> None:
        """Load current settings into the UI."""
        self._settings = settings
        self._config_path = config_path
        self.has_provider_changes = False

        # UI Language
        self._ui_language.value = settings.ui.locale
        self._ui_language.options = self._build_locale_options()

        # STT Provider
        self._stt_provider.selected_provider = settings.provider.stt
        self._update_stt_visibility()

        # LLM Provider
        self._llm_provider.selected_provider = settings.provider.llm
        self._update_trans_visibility()

        # Qwen Region (sync both)
        region_value = settings.qwen.region.value
        self._stt_qwen_region.value = region_value
        self._trans_qwen_region.value = region_value

        # Audio Settings
        self._audio_settings.host_api = settings.audio.input_host_api
        self._audio_settings.microphone = settings.audio.input_device
        self._audio_settings.vad_sensitivity = settings.stt.vad_speech_threshold

        # Prompt
        provider_name = "gemini" if settings.provider.llm == LLMProviderName.GEMINI else "qwen"
        self._prompt_editor.set_provider(provider_name)
        if settings.system_prompt.strip():
            self._prompt_editor.value = settings.system_prompt
        else:
            self._prompt_editor.load_default_prompt()
            settings.system_prompt = self._prompt_editor.value

        # Load secrets
        self._load_secrets(settings, config_path)

        if self.page:
            self.update()

    def _load_secrets(self, settings: AppSettings, config_path: Path) -> None:
        """Load secret values into fields."""
        try:
            store = create_secret_store(settings.secrets, config_path=config_path)
        except Exception as exc:
            logger.warning("Failed to load secrets: %s", exc)
            return

        self._google_key.value = store.get("google_api_key") or ""
        self._deepgram_key.value = store.get("deepgram_api_key") or ""
        self._soniox_key.value = store.get("soniox_api_key") or ""

        # Alibaba keys with legacy fallback
        beijing_key = _load_secret_value(
            store, "alibaba_api_key_beijing", legacy_keys=("alibaba_api_key",)
        )
        singapore_key = _load_secret_value(
            store, "alibaba_api_key_singapore", legacy_keys=("alibaba_api_key",)
        )

        self._stt_qwen_key_beijing.value = beijing_key
        self._stt_qwen_key_singapore.value = singapore_key
        self._trans_qwen_key_beijing.value = beijing_key
        self._trans_qwen_key_singapore.value = singapore_key

    # --- Visibility Updates ---
    def _update_stt_visibility(self) -> None:
        """Update STT field visibility based on selected provider."""
        if not self._settings:
            return

        provider = self._settings.provider.stt
        is_deepgram = provider == STTProviderName.DEEPGRAM
        is_soniox = provider == STTProviderName.SONIOX
        is_qwen = provider == STTProviderName.QWEN_ASR

        self._deepgram_key.visible = is_deepgram
        self._soniox_key.visible = is_soniox
        self._stt_qwen_region.visible = is_qwen
        self._stt_qwen_key_beijing.visible = is_qwen
        self._stt_qwen_key_singapore.visible = is_qwen

    def _update_trans_visibility(self) -> None:
        """Update translation field visibility based on selected provider."""
        if not self._settings:
            return

        is_gemini = self._settings.provider.llm == LLMProviderName.GEMINI
        is_qwen = self._settings.provider.llm == LLMProviderName.QWEN

        self._google_key.visible = is_gemini
        self._trans_qwen_region.visible = is_qwen
        self._trans_qwen_key_beijing.visible = is_qwen
        self._trans_qwen_key_singapore.visible = is_qwen

    # --- Event Handlers ---
    def _on_ui_language_change(self, e) -> None:
        if not self._settings:
            return
        old_locale = self._settings.ui.locale
        new_locale = self._ui_language.value or "en"
        logger.info(f"[Settings] Language changed: {old_locale} -> {new_locale}")
        self._settings.ui.locale = new_locale
        self._emit_settings_changed()

    def _on_stt_provider_change(self, provider: STTProviderName) -> None:
        if not self._settings:
            return

        old_provider = self._settings.provider.stt.value
        logger.info(f"[Settings] STT provider changed: {old_provider} -> {provider.value}")
        self._settings.provider.stt = provider
        self._update_stt_visibility()
        self.has_provider_changes = True

        # Check compatibility warning
        source_lang = self._settings.languages.source_language
        warning = get_stt_compatibility_warning(source_lang, provider.value)
        if warning and self.page:
            self.page.open(
                ft.SnackBar(
                    ft.Text(t(warning.key, language=language_name(warning.language_code))),
                    bgcolor=ft.Colors.ORANGE_700,
                    duration=4000,
                )
            )

        if self.page:
            self._stt_fields.update()
        self._emit_settings_changed()

    def _on_llm_provider_change(self, provider: LLMProviderName) -> None:
        if not self._settings:
            return

        old_provider = self._settings.provider.llm
        logger.info(f"[Settings] LLM provider changed: {old_provider.value} -> {provider.value}")
        self._settings.provider.llm = provider
        self._update_trans_visibility()
        self.has_provider_changes = True

        # Update prompt if provider changed
        if old_provider != provider:
            provider_name = "gemini" if provider == LLMProviderName.GEMINI else "qwen"
            self._prompt_editor.set_provider(provider_name)
            self._prompt_editor.load_default_prompt()
            self._settings.system_prompt = self._prompt_editor.value

        if self.page:
            self._trans_fields.update()
        self._emit_settings_changed()

    def _on_qwen_region_change(self, e) -> None:
        if not self._settings:
            return

        region_value = e.control.value
        old_region = self._settings.qwen.region.value
        logger.info(f"[Settings] Qwen region changed: {old_region} -> {region_value}")
        self._settings.qwen.region = QwenRegion(region_value)

        # Sync both region dropdowns
        self._stt_qwen_region.value = region_value
        self._trans_qwen_region.value = region_value
        if self._stt_qwen_region.page:
            self._stt_qwen_region.update()
        if self._trans_qwen_region.page:
            self._trans_qwen_region.update()

        self.has_provider_changes = True
        self._emit_settings_changed()

    def _on_secret_change(self, key: str, value: str) -> None:
        if not self._settings or not self._config_path:
            return

        action = "updated" if value else "cleared"
        logger.info(f"[Settings] API key {action}: {key}")

        with contextlib.suppress(Exception):
            store = create_secret_store(self._settings.secrets, config_path=self._config_path)
            if value:
                store.set(key, value)
            else:
                store.delete(key)

        # Sync Alibaba keys between sections
        if key == "alibaba_api_key_beijing":
            self._stt_qwen_key_beijing.value = value
            self._trans_qwen_key_beijing.value = value
        elif key == "alibaba_api_key_singapore":
            self._stt_qwen_key_singapore.value = value
            self._trans_qwen_key_singapore.value = value

    def _on_audio_change(self) -> None:
        if not self._settings:
            return

        # New values
        new_host = self._audio_settings.host_api
        new_device = self._audio_settings.microphone
        new_vad = self._audio_settings.vad_sensitivity

        # Old values
        old_host = self._settings.audio.input_host_api
        old_device = self._settings.audio.input_device
        old_vad = self._settings.stt.vad_speech_threshold

        # Differential logging
        if old_host != new_host:
            logger.info(f"[Settings] Audio Host changed: {old_host} -> {new_host}")

        if old_device != new_device:
            logger.info(f"[Settings] Microphone changed: {old_device} -> {new_device}")

        if abs(old_vad - new_vad) > 0.001:  # Float comparison
            logger.info(f"[Settings] VAD sensitivity changed: {old_vad:.2f} -> {new_vad:.2f}")

        # Update settings
        self._settings.audio.input_host_api = new_host
        self._settings.audio.input_device = new_device
        self._settings.stt.vad_speech_threshold = new_vad
        self._emit_settings_changed()

    def _on_prompt_change(self, value: str) -> None:
        if not self._settings:
            return
        self._settings.system_prompt = value
        self._emit_settings_changed()

    async def _verify_key(self, provider: str, key: str) -> tuple[bool, str]:
        """Verify API key."""
        if self.on_verify_api_key:
            return await self.on_verify_api_key(provider, key)
        return False, "Verification not available"

    def _emit_settings_changed(self) -> None:
        if self._settings and self.on_settings_changed:
            self.on_settings_changed(self._settings)

    # --- Locale ---
    def apply_locale(self) -> None:
        """Update all labels when locale changes."""
        self._title.value = t("settings.title")
        self._ui_language.label = t("settings.ui_language")
        self._ui_language.options = self._build_locale_options()

        # Update section titles
        self._ui_section.apply_locale()
        self._stt_section.apply_locale()
        self._translation_section.apply_locale()
        self._audio_section.apply_locale()
        self._persona_section.apply_locale()

        # Update components
        self._stt_provider.apply_locale()
        self._llm_provider.apply_locale()
        self._deepgram_key.apply_locale()
        self._soniox_key.apply_locale()
        self._google_key.apply_locale()
        self._stt_qwen_key_beijing.apply_locale()
        self._stt_qwen_key_singapore.apply_locale()
        self._trans_qwen_key_beijing.apply_locale()
        self._trans_qwen_key_singapore.apply_locale()
        self._audio_settings.apply_locale()
        self._prompt_editor.apply_locale()

        # Update Qwen region dropdowns
        for dropdown in (self._stt_qwen_region, self._trans_qwen_region):
            dropdown.label = t("settings.qwen_region")
            dropdown.options = [
                ft.dropdown.Option(key=QwenRegion.BEIJING.value, text=t("region.beijing")),
                ft.dropdown.Option(key=QwenRegion.SINGAPORE.value, text=t("region.singapore")),
            ]

        if self.page:
            self.update()

    def refresh_prompt_if_empty(self) -> None:
        """Load default prompt if current is empty."""
        self._prompt_editor.load_default_if_empty()
