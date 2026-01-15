"""Settings view - Bento grid layout with SegmentedButton providers."""

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
from puripuly_heart.ui.components.glow import GLOW_CARD, create_glow_stack
from puripuly_heart.ui.components.settings import ApiKeyField, AudioSettings, PromptEditor
from puripuly_heart.ui.fonts import font_for_language
from puripuly_heart.ui.i18n import (
    available_locales,
    get_locale,
    language_name,
    locale_label,
    provider_label,
    t,
)
from puripuly_heart.ui.theme import (
    COLOR_NEUTRAL,
    COLOR_NEUTRAL_DARK,
    COLOR_PRIMARY,
    COLOR_SURFACE,
    get_card_shadow,
)

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
    """Settings view with Bento grid layout."""

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

    # --- Card Wrapper (About page pattern) ---
    def _wrap_card(self, content: ft.Control, *, expand: bool = True) -> ft.Control:
        """Wrap content in a styled Bento card with glow effect."""
        content_with_glow = create_glow_stack(
            ft.Container(content=content, expand=True, padding=24),
            config=GLOW_CARD,
        )
        return ft.Container(
            content=content_with_glow,
            bgcolor=COLOR_SURFACE,
            border_radius=16,
            border=ft.border.all(1, ft.Colors.with_opacity(0.4, ft.Colors.WHITE)),
            expand=expand,
            clip_behavior=ft.ClipBehavior.HARD_EDGE,
            shadow=get_card_shadow(),
        )

    # --- SegmentedButton Builders ---
    def _build_stt_segmented(self) -> ft.SegmentedButton:
        """Build STT provider SegmentedButton."""
        return ft.SegmentedButton(
            selected={STTProviderName.DEEPGRAM.value},
            allow_empty_selection=False,
            show_selected_icon=False,
            segments=[
                ft.Segment(value=p.value, label=ft.Text(provider_label(p.value)))
                for p in STTProviderName
            ],
            on_change=self._on_stt_segmented_change,
        )

    def _build_llm_segmented(self) -> ft.SegmentedButton:
        """Build LLM provider SegmentedButton."""
        return ft.SegmentedButton(
            selected={LLMProviderName.GEMINI.value},
            allow_empty_selection=False,
            show_selected_icon=False,
            segments=[
                ft.Segment(value=p.value, label=ft.Text(provider_label(p.value)))
                for p in LLMProviderName
            ],
            on_change=self._on_llm_segmented_change,
        )

    def _build_qwen_region_segmented(self) -> ft.SegmentedButton:
        """Build Qwen region SegmentedButton."""
        return ft.SegmentedButton(
            selected={QwenRegion.BEIJING.value},
            allow_empty_selection=False,
            show_selected_icon=False,
            segments=[
                ft.Segment(value=QwenRegion.BEIJING.value, label=ft.Text(t("region.beijing"))),
                ft.Segment(value=QwenRegion.SINGAPORE.value, label=ft.Text(t("region.singapore"))),
            ],
            on_change=self._on_qwen_region_change,
        )

    def _build_ui(self) -> None:
        """Build the settings UI with Bento grid layout."""
        # === Row 1: STT (1x1) + Translation (1x1) ===
        self._stt_segmented = self._build_stt_segmented()
        self._stt_title = ft.Text(
            t("settings.section.stt"), size=24, weight=ft.FontWeight.BOLD, color=COLOR_NEUTRAL
        )
        stt_card = self._wrap_card(
            ft.Column([self._stt_title, ft.Container(height=16), self._stt_segmented], spacing=0)
        )

        self._llm_segmented = self._build_llm_segmented()
        self._trans_title = ft.Text(
            t("settings.section.translation"),
            size=24,
            weight=ft.FontWeight.BOLD,
            color=COLOR_NEUTRAL,
        )
        trans_card = self._wrap_card(
            ft.Column([self._trans_title, ft.Container(height=16), self._llm_segmented], spacing=0)
        )

        row1 = ft.Container(
            content=ft.Row([stt_card, trans_card], spacing=16, expand=True),
            height=280,
        )

        # === Row 2: API Keys (2x1) ===
        self._qwen_region_segmented = self._build_qwen_region_segmented()
        self._qwen_region_row = ft.Row(
            [
                ft.Text(
                    t("settings.qwen_region"),
                    size=20,
                    weight=ft.FontWeight.BOLD,
                    color=COLOR_NEUTRAL,
                ),
                self._qwen_region_segmented,
            ],
            alignment=ft.MainAxisAlignment.START,
            vertical_alignment=ft.CrossAxisAlignment.CENTER,
            spacing=16,
        )

        # API Key fields
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
        self._google_key = ApiKeyField(
            "settings.google_api_key",
            "google_api_key",
            "google",
            on_verify=self._verify_key,
            on_change=self._on_secret_change,
        )
        self._alibaba_key_beijing = ApiKeyField(
            "settings.alibaba_api_key_beijing",
            "alibaba_api_key_beijing",
            "alibaba_beijing",
            on_verify=self._verify_key,
            on_change=self._on_secret_change,
        )
        self._alibaba_key_singapore = ApiKeyField(
            "settings.alibaba_api_key_singapore",
            "alibaba_api_key_singapore",
            "alibaba_singapore",
            on_verify=self._verify_key,
            on_change=self._on_secret_change,
        )

        self._api_keys_column = ft.Column(
            [
                self._qwen_region_row,
                self._deepgram_key,
                self._soniox_key,
                self._google_key,
                self._alibaba_key_beijing,
                self._alibaba_key_singapore,
            ],
            spacing=12,
        )

        self._api_title = ft.Text(
            t("settings.section.api_keys"), size=24, weight=ft.FontWeight.BOLD, color=COLOR_NEUTRAL
        )
        api_card = self._wrap_card(
            ft.Column([self._api_title, ft.Container(height=16), self._api_keys_column], spacing=0)
        )
        row2 = api_card

        # === Row 3: UI (1x1) + Audio (1x1) ===
        self._ui_language = ft.Dropdown(
            label=t("settings.ui_language"),
            options=self._build_locale_options(),
            on_change=self._on_ui_language_change,
            border_radius=12,
            text_size=28,
            label_style=ft.TextStyle(size=20, weight=ft.FontWeight.BOLD),
            color=COLOR_NEUTRAL_DARK,
        )
        self._ui_title = ft.Text(
            t("settings.section.ui"), size=24, weight=ft.FontWeight.BOLD, color=COLOR_NEUTRAL
        )
        ui_card = self._wrap_card(
            ft.Column([self._ui_title, ft.Container(height=16), self._ui_language], spacing=0)
        )

        self._audio_settings = AudioSettings(on_change=self._on_audio_change)
        self._audio_title = ft.Text(
            t("settings.section.audio"), size=24, weight=ft.FontWeight.BOLD, color=COLOR_NEUTRAL
        )
        audio_card = self._wrap_card(
            ft.Column([self._audio_title, ft.Container(height=16), self._audio_settings], spacing=0)
        )

        row3 = ft.Container(
            content=ft.Row([ui_card, audio_card], spacing=16, expand=True),
            height=280,
        )

        # === Row 4-5: Persona (2x2) - Licenses style ===
        self._prompt_editor = PromptEditor(on_change=self._on_prompt_change)
        self._persona_title = ft.Text(
            t("settings.section.persona"), size=24, weight=ft.FontWeight.BOLD, color=COLOR_NEUTRAL
        )

        # Reset button (matches Persona title color, hover -> primary)
        self._reset_prompt_btn = ft.TextButton(
            text=t("settings.reset_prompt"),
            icon=ft.Icons.REFRESH_ROUNDED,
            style=ft.ButtonStyle(
                color={
                    ft.ControlState.HOVERED: COLOR_PRIMARY,
                    ft.ControlState.DEFAULT: COLOR_NEUTRAL,
                },
                icon_color={
                    ft.ControlState.HOVERED: COLOR_PRIMARY,
                    ft.ControlState.DEFAULT: COLOR_NEUTRAL,
                },
                text_style=ft.TextStyle(
                    size=20,
                    font_family=font_for_language(get_locale()),
                ),
                overlay_color=ft.Colors.TRANSPARENT,
                animation_duration=0,
            ),
            on_click=self._on_reset_prompt,
        )

        # Header row with title and reset button
        persona_header = ft.Row(
            controls=[self._persona_title, ft.Container(expand=True), self._reset_prompt_btn],
            alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
            vertical_alignment=ft.CrossAxisAlignment.CENTER,
        )

        # Simple container like Licenses (no border, no internal scroll)
        prompt_container = ft.Container(
            content=self._prompt_editor,
            width=float("inf"),
        )

        persona_card = self._wrap_card(
            ft.Column([persona_header, ft.Container(height=16), prompt_container], spacing=0),
        )
        row4 = persona_card

        self.controls = [row1, row2, row3, row4]

    def _populate_host_apis(self) -> None:
        """Legacy hook for tests; host APIs are handled by AudioSettings."""
        return None

    def _refresh_microphones(self) -> None:
        """Legacy hook for tests; microphone list is handled by AudioSettings."""
        return None

    def _build_locale_options(self) -> list[ft.dropdown.Option]:
        """Build locale dropdown options."""
        return [
            ft.dropdown.Option(key=code, text=locale_label(code)) for code in available_locales()
        ]

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
        self._stt_segmented.selected = {settings.provider.stt.value}
        self._update_api_visibility()

        # LLM Provider
        self._llm_segmented.selected = {settings.provider.llm.value}

        # Qwen Region
        self._qwen_region_segmented.selected = {settings.qwen.region.value}

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

        self._alibaba_key_beijing.value = beijing_key
        self._alibaba_key_singapore.value = singapore_key

    # --- Visibility Updates ---
    def _update_api_visibility(self) -> None:
        """Update API key field visibility based on selected providers."""
        if not self._settings:
            return

        stt = self._settings.provider.stt
        llm = self._settings.provider.llm

        # STT-related keys
        self._deepgram_key.visible = stt == STTProviderName.DEEPGRAM
        self._soniox_key.visible = stt == STTProviderName.SONIOX

        # LLM-related keys
        self._google_key.visible = llm == LLMProviderName.GEMINI

        # Qwen keys (visible if either uses Qwen)
        uses_qwen = stt == STTProviderName.QWEN_ASR or llm == LLMProviderName.QWEN
        self._qwen_region_row.visible = uses_qwen
        self._alibaba_key_beijing.visible = uses_qwen
        self._alibaba_key_singapore.visible = uses_qwen

    # --- Event Handlers ---
    def _on_stt_segmented_change(self, e) -> None:
        if not self._settings:
            return
        selected = list(e.control.selected)[0] if e.control.selected else None
        if not selected:
            return

        provider = STTProviderName(selected)
        old_provider = self._settings.provider.stt.value
        logger.info(f"[Settings] STT provider changed: {old_provider} -> {provider.value}")
        self._settings.provider.stt = provider
        self._update_api_visibility()
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
            self._api_keys_column.update()
        self._emit_settings_changed()

    def _on_llm_segmented_change(self, e) -> None:
        if not self._settings:
            return
        selected = list(e.control.selected)[0] if e.control.selected else None
        if not selected:
            return

        provider = LLMProviderName(selected)
        old_provider = self._settings.provider.llm
        logger.info(f"[Settings] LLM provider changed: {old_provider.value} -> {provider.value}")
        self._settings.provider.llm = provider
        self._update_api_visibility()
        self.has_provider_changes = True

        # Update prompt if provider changed
        if old_provider != provider:
            provider_name = "gemini" if provider == LLMProviderName.GEMINI else "qwen"
            self._prompt_editor.set_provider(provider_name)
            self._prompt_editor.load_default_prompt()
            self._settings.system_prompt = self._prompt_editor.value

        if self.page:
            self._api_keys_column.update()
        self._emit_settings_changed()

    def _on_qwen_region_change(self, e) -> None:
        if not self._settings:
            return
        selected = list(e.control.selected)[0] if e.control.selected else None
        if not selected:
            return

        old_region = self._settings.qwen.region.value
        logger.info(f"[Settings] Qwen region changed: {old_region} -> {selected}")
        self._settings.qwen.region = QwenRegion(selected)
        self.has_provider_changes = True
        self._emit_settings_changed()

    def _on_ui_language_change(self, e) -> None:
        if not self._settings:
            return
        old_locale = self._settings.ui.locale
        new_locale = self._ui_language.value or "en"
        logger.info(f"[Settings] Language changed: {old_locale} -> {new_locale}")
        self._settings.ui.locale = new_locale
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

    def _on_audio_change(self) -> None:
        if not self._settings:
            return

        new_host = self._audio_settings.host_api
        new_device = self._audio_settings.microphone
        new_vad = self._audio_settings.vad_sensitivity

        old_host = self._settings.audio.input_host_api
        old_device = self._settings.audio.input_device
        old_vad = self._settings.stt.vad_speech_threshold

        if old_host != new_host:
            logger.info(f"[Settings] Audio Host changed: {old_host} -> {new_host}")
        if old_device != new_device:
            logger.info(f"[Settings] Microphone changed: {old_device} -> {new_device}")
        if abs(old_vad - new_vad) > 0.001:
            logger.info(f"[Settings] VAD sensitivity changed: {old_vad:.2f} -> {new_vad:.2f}")

        self._settings.audio.input_host_api = new_host
        self._settings.audio.input_device = new_device
        self._settings.stt.vad_speech_threshold = new_vad
        self._emit_settings_changed()

    def _on_prompt_change(self, value: str) -> None:
        if not self._settings:
            return
        self._settings.system_prompt = value
        self._emit_settings_changed()

    def _on_reset_prompt(self, e) -> None:
        """Reset prompt to default for current provider."""
        self._prompt_editor.load_default_prompt()
        if self._settings:
            self._settings.system_prompt = self._prompt_editor.value
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
        # Section titles
        self._stt_title.value = t("settings.section.stt")
        self._trans_title.value = t("settings.section.translation")
        self._api_title.value = t("settings.section.api_keys")
        self._ui_title.value = t("settings.section.ui")
        self._audio_title.value = t("settings.section.audio")
        self._persona_title.value = t("settings.section.persona")

        # UI Language dropdown
        self._ui_language.label = t("settings.ui_language")
        self._ui_language.options = self._build_locale_options()

        # SegmentedButtons need rebuild for labels
        self._rebuild_segmented_buttons()

        # Components
        self._deepgram_key.apply_locale()
        self._soniox_key.apply_locale()
        self._google_key.apply_locale()
        self._alibaba_key_beijing.apply_locale()
        self._alibaba_key_singapore.apply_locale()
        self._audio_settings.apply_locale()
        self._prompt_editor.apply_locale()

        if self.page:
            self.update()

    def _rebuild_segmented_buttons(self) -> None:
        """Rebuild segmented button labels for locale change."""
        # STT
        for seg in self._stt_segmented.segments:
            seg.label = ft.Text(provider_label(seg.value))
        # LLM
        for seg in self._llm_segmented.segments:
            seg.label = ft.Text(provider_label(seg.value))
        # Qwen Region
        region_labels = {
            QwenRegion.BEIJING.value: t("region.beijing"),
            QwenRegion.SINGAPORE.value: t("region.singapore"),
        }
        for seg in self._qwen_region_segmented.segments:
            seg.label = ft.Text(region_labels.get(seg.value, seg.value))

    def refresh_prompt_if_empty(self) -> None:
        """Load default prompt if current is empty."""
        self._prompt_editor.load_default_if_empty()
