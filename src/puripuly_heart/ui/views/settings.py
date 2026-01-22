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
from puripuly_heart.ui.components.settings import (
    ApiKeyField,
    AudioSettings,
    OptionItem,
    PromptEditor,
    SettingsModal,
)
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
    COLOR_ON_BACKGROUND,
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
        self.on_secret_cleared: Callable[[str], None] | None = None  # key name
        self.show_snackbar: Callable[[str, str], None] | None = None

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

    # --- Clickable Text Builders ---
    def _build_clickable_text(self, text: str, on_click) -> ft.Container:
        """Build a clickable centered text with hover effect."""
        text_control = ft.Text(
            text,
            size=28,
            color=COLOR_ON_BACKGROUND,
            text_align=ft.TextAlign.CENTER,
        )
        return ft.Container(
            content=text_control,
            alignment=ft.alignment.center,
            expand=True,
            on_click=on_click,
            on_hover=self._on_text_hover,
        )

    def _on_text_hover(self, e: ft.ControlEvent) -> None:
        """Handle hover effect on clickable text."""
        container = e.control
        text_control = container.content
        if e.data == "true":
            text_control.color = COLOR_PRIMARY
        else:
            text_control.color = COLOR_ON_BACKGROUND
        container.update()

        container.update()

    def _get_button_style(self, font_family: str) -> ft.ButtonStyle:
        """Create a complete ButtonStyle with the specified font."""
        return ft.ButtonStyle(
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
                font_family=font_family,
            ),
            overlay_color=ft.Colors.TRANSPARENT,
            animation_duration=0,
        )

    def _build_ui(self) -> None:
        """Build the settings UI with Bento grid layout."""
        # === Row 1: STT (1x1) + Translation (1x1) ===
        self._stt_text = self._build_clickable_text(
            provider_label(STTProviderName.DEEPGRAM.value),
            self._on_stt_click,
        )
        self._stt_title = ft.Text(
            t("settings.section.stt"), size=24, weight=ft.FontWeight.BOLD, color=COLOR_NEUTRAL
        )
        stt_card = self._wrap_card(
            ft.Column([self._stt_title, self._stt_text], spacing=0, expand=True)
        )

        self._llm_text = self._build_clickable_text(
            provider_label(LLMProviderName.GEMINI.value),
            self._on_llm_click,
        )
        self._trans_title = ft.Text(
            t("settings.section.translation"),
            size=24,
            weight=ft.FontWeight.BOLD,
            color=COLOR_NEUTRAL,
        )
        trans_card = self._wrap_card(
            ft.Column([self._trans_title, self._llm_text], spacing=0, expand=True)
        )

        row1 = ft.Container(
            content=ft.Row([stt_card, trans_card], spacing=16, expand=True),
            height=280,
        )

        # === Row 2: API Keys (2x1) ===
        # Qwen region selection button (in header)
        self._qwen_region_btn = ft.TextButton(
            text=f"{t('settings.qwen_region')} {t('region.beijing')}",
            style=ft.ButtonStyle(
                color={
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
            on_click=self._on_qwen_region_click,
            visible=False,  # Hidden by default, updated by visibility logic
        )

        # API Key fields
        self._deepgram_key = ApiKeyField(
            "settings.deepgram_api_key",
            "deepgram_api_key",
            "deepgram",
            on_verify=self._verify_key,
            on_save=self._on_secret_change,
            show_snackbar=lambda msg, bg: (
                self.show_snackbar(msg, bg) if self.show_snackbar else None
            ),
        )
        self._soniox_key = ApiKeyField(
            "settings.soniox_api_key",
            "soniox_api_key",
            "soniox",
            on_verify=self._verify_key,
            on_save=self._on_secret_change,
            show_snackbar=lambda msg, bg: (
                self.show_snackbar(msg, bg) if self.show_snackbar else None
            ),
        )
        self._google_key = ApiKeyField(
            "settings.google_api_key",
            "google_api_key",
            "google",
            on_verify=self._verify_key,
            on_save=self._on_secret_change,
            show_snackbar=lambda msg, bg: (
                self.show_snackbar(msg, bg) if self.show_snackbar else None
            ),
        )
        self._alibaba_key_beijing = ApiKeyField(
            "settings.alibaba_api_key_beijing",
            "alibaba_api_key_beijing",
            "alibaba_beijing",
            on_verify=self._verify_key,
            on_save=self._on_secret_change,
            show_snackbar=lambda msg, bg: (
                self.show_snackbar(msg, bg) if self.show_snackbar else None
            ),
        )
        self._alibaba_key_singapore = ApiKeyField(
            "settings.alibaba_api_key_singapore",
            "alibaba_api_key_singapore",
            "alibaba_singapore",
            on_verify=self._verify_key,
            on_save=self._on_secret_change,
            show_snackbar=lambda msg, bg: (
                self.show_snackbar(msg, bg) if self.show_snackbar else None
            ),
        )

        self._api_keys_column = ft.Column(
            [
                # self._qwen_region_row removed
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
        # Header row with title and region button
        api_header = ft.Row(
            controls=[
                self._api_title,
                ft.Container(expand=True),
                self._qwen_region_btn,
            ],
            alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
            vertical_alignment=ft.CrossAxisAlignment.CENTER,
        )

        api_card = self._wrap_card(
            ft.Column([api_header, ft.Container(height=16), self._api_keys_column], spacing=0)
        )
        row2 = api_card

        # === Row 3: UI (1x1) + Audio (1x1) ===
        self._ui_text = self._build_clickable_text(
            locale_label(get_locale()),
            self._on_ui_click,
        )
        self._ui_title = ft.Text(
            t("settings.section.ui"), size=24, weight=ft.FontWeight.BOLD, color=COLOR_NEUTRAL
        )
        ui_card = self._wrap_card(
            ft.Column([self._ui_title, self._ui_text], spacing=0, expand=True)
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

        # === Row 4: Low Latency (1x1) + VAD (1x1) ===
        self._low_latency_text = self._build_clickable_text(
            t("toggle.off"),
            self._on_low_latency_click,
        )
        self._low_latency_title = ft.Text(
            t("settings.low_latency_mode"),
            size=24,
            weight=ft.FontWeight.BOLD,
            color=COLOR_NEUTRAL,
        )
        low_latency_card = self._wrap_card(
            ft.Column([self._low_latency_title, self._low_latency_text], spacing=0, expand=True)
        )

        # VAD Box
        self._vad_title = ft.Text(
            t("settings.vad_sensitivity"),
            size=24,
            weight=ft.FontWeight.BOLD,
            color=COLOR_NEUTRAL,
        )
        self._vad_slider = ft.Slider(
            min=0.0,
            max=1.0,
            divisions=20,
            value=0.5,
            label="0.50",
            active_color=COLOR_PRIMARY,
            on_change=self._handle_vad_visual_change,
            on_change_end=self._handle_vad_change,
        )
        vad_card = self._wrap_card(
            ft.Column(
                [
                    self._vad_title,
                    ft.Container(
                        content=self._vad_slider,
                        alignment=ft.alignment.center,
                        expand=True,
                    ),
                ],
                spacing=0,
                expand=True,
            )
        )

        row4 = ft.Container(
            content=ft.Row([low_latency_card, vad_card], spacing=16, expand=True),
            height=280,
        )

        # === Row 5: Persona (2x2) - Licenses style ===
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
        row5 = persona_card

        self.controls = [row1, row2, row3, row4, row5]

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
        self._ui_text.content.value = locale_label(settings.ui.locale)

        # STT Provider
        self._stt_text.content.value = provider_label(settings.provider.stt.value)
        self._update_api_visibility()

        # LLM Provider
        self._llm_text.content.value = provider_label(settings.provider.llm.value)

        # Qwen Region
        region_label = t(f"region.{settings.qwen.region.value}")
        self._qwen_region_btn.text = f"{t('settings.qwen_region')} {region_label}"

        # Audio Settings
        self._audio_settings.host_api = settings.audio.input_host_api
        self._audio_settings.microphone = settings.audio.input_device

        # VAD
        self._vad_slider.value = settings.stt.vad_speech_threshold
        self._vad_slider.label = f"{settings.stt.vad_speech_threshold:.2f}"
        self._low_latency_text.content.value = t(
            "toggle.on" if settings.stt.low_latency_mode else "toggle.off"
        )

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

        # Restore verification status icons from saved settings
        self._restore_api_key_icons(settings)

    def _restore_api_key_icons(self, settings: AppSettings) -> None:
        """Restore API key field icons based on saved verification status."""
        verified = settings.api_key_verified

        # Map field -> (has_key, is_verified)
        field_map = [
            (self._deepgram_key, self._deepgram_key.value, verified.deepgram),
            (self._soniox_key, self._soniox_key.value, verified.soniox),
            (self._google_key, self._google_key.value, verified.google),
            (self._alibaba_key_beijing, self._alibaba_key_beijing.value, verified.alibaba_beijing),
            (
                self._alibaba_key_singapore,
                self._alibaba_key_singapore.value,
                verified.alibaba_singapore,
            ),
        ]

        for field, has_key, is_verified in field_map:
            if not has_key:
                field._set_status("idle")
                field._last_verified_hash = ""
            elif is_verified:
                field._set_status("success")
                # Restore hash to prevent re-verification on blur
                field._last_verified_hash = field._get_key_hash(has_key)
            else:
                field._set_status("error")
                field._last_verified_hash = ""

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

        # Qwen keys (visible if either uses Qwen, based on selected region)
        uses_qwen = stt == STTProviderName.QWEN_ASR or llm == LLMProviderName.QWEN
        self._qwen_region_btn.visible = uses_qwen
        qwen_region = self._settings.qwen.region
        self._alibaba_key_beijing.visible = uses_qwen and qwen_region == QwenRegion.BEIJING
        self._alibaba_key_singapore.visible = uses_qwen and qwen_region == QwenRegion.SINGAPORE

    # --- Event Handlers ---
    def _on_stt_click(self, e) -> None:
        """Open STT provider selection modal."""
        if not self.page:
            return
        options = [
            OptionItem(
                value=p.value,
                label=provider_label(p.value),
                description=t(f"provider.{p.value}.description", default=""),
            )
            for p in STTProviderName
        ]
        current = (
            self._settings.provider.stt.value if self._settings else STTProviderName.DEEPGRAM.value
        )
        modal = SettingsModal(
            self.page,
            t("settings.section.stt"),
            options,
            self._on_stt_selected,
            show_description=True,
        )
        modal.open(current)

    def _on_stt_selected(self, value: str) -> None:
        """Handle STT provider selection from modal."""
        if not self._settings:
            return
        provider = STTProviderName(value)
        old_provider = self._settings.provider.stt.value
        logger.info(f"[Settings] STT provider changed: {old_provider} -> {provider.value}")
        self._settings.provider.stt = provider
        self._update_api_visibility()
        self.has_provider_changes = True

        # Update text
        self._stt_text.content.value = provider_label(provider.value)

        # Check compatibility warning
        source_lang = self._settings.languages.source_language
        warning = get_stt_compatibility_warning(source_lang, provider.value)
        if warning and self.page:
            self.page.open(
                ft.SnackBar(
                    ft.Text(
                        t(warning.key, language=language_name(warning.language_code)),
                        color=ft.Colors.WHITE,
                    ),
                    bgcolor=ft.Colors.ORANGE_700,
                    duration=4000,
                    behavior=ft.SnackBarBehavior.FLOATING,
                    margin=ft.margin.only(bottom=90),
                    padding=20,
                )
            )

        if self.page:
            self._qwen_region_btn.update()
            self._api_keys_column.update()
            self._stt_text.update()
        self._emit_settings_changed()

    def _on_llm_click(self, e) -> None:
        """Open LLM provider selection modal."""
        if not self.page:
            return
        options = [
            OptionItem(
                value=p.value,
                label=provider_label(p.value),
                description=t(f"provider.{p.value}.description", default=""),
            )
            for p in LLMProviderName
        ]
        current = (
            self._settings.provider.llm.value if self._settings else LLMProviderName.GEMINI.value
        )
        modal = SettingsModal(
            self.page,
            t("settings.section.translation"),
            options,
            self._on_llm_selected,
            show_description=True,
        )
        modal.open(current)

    def _on_llm_selected(self, value: str) -> None:
        """Handle LLM provider selection from modal."""
        if not self._settings:
            return
        provider = LLMProviderName(value)
        old_provider = self._settings.provider.llm
        logger.info(f"[Settings] LLM provider changed: {old_provider.value} -> {provider.value}")
        self._settings.provider.llm = provider
        self._update_api_visibility()
        self.has_provider_changes = True

        # Update text
        self._llm_text.content.value = provider_label(provider.value)

        # Update prompt if provider changed
        if old_provider != provider:
            provider_name = "gemini" if provider == LLMProviderName.GEMINI else "qwen"
            self._prompt_editor.set_provider(provider_name)
            self._prompt_editor.load_default_prompt()
            self._settings.system_prompt = self._prompt_editor.value

        if self.page:
            self._qwen_region_btn.update()
            self._api_keys_column.update()
            self._llm_text.update()
        self._emit_settings_changed()

    def _on_ui_click(self, e) -> None:
        """Open UI language selection modal."""
        if not self.page:
            return
        options = [OptionItem(value=code, label=locale_label(code)) for code in available_locales()]
        current = self._settings.ui.locale if self._settings else "en"
        modal = SettingsModal(
            self.page,
            t("settings.section.ui"),
            options,
            self._on_ui_selected,
            show_description=False,
        )
        modal.open(current)

    def _on_ui_selected(self, value: str) -> None:
        """Handle UI language selection from modal."""
        if not self._settings:
            return
        old_locale = self._settings.ui.locale
        logger.info(f"[Settings] Language changed: {old_locale} -> {value}")
        self._settings.ui.locale = value

        # Update text
        self._ui_text.content.value = locale_label(value)
        if self.page:
            self._ui_text.update()
        self._emit_settings_changed()

    def _on_qwen_region_click(self, e) -> None:
        """Open Qwen region selection modal."""
        if not self.page:
            return
        options = [OptionItem(value=r.value, label=t(f"region.{r.value}")) for r in QwenRegion]
        current = self._settings.qwen.region.value if self._settings else QwenRegion.BEIJING.value
        modal = SettingsModal(
            self.page,
            t("settings.qwen_region"),
            options,
            self._on_qwen_region_selected,
            show_description=False,
        )
        modal.open(current)

    def _on_qwen_region_selected(self, value: str) -> None:
        if not self._settings:
            return

        old_region = self._settings.qwen.region.value
        logger.info(f"[Settings] Qwen region changed: {old_region} -> {value}")
        self._settings.qwen.region = QwenRegion(value)
        self.has_provider_changes = True

        # Update text
        self._qwen_region_btn.text = f"{t('settings.qwen_region')} {t(f'region.{value}')}"
        if self.page:
            self._qwen_region_btn.update()

        self._update_api_visibility()
        if self.page:
            self._api_keys_column.update()
        self._emit_settings_changed()

    def _on_secret_change(self, key: str, value: str) -> None:
        if not self._settings or not self._config_path:
            return

        with contextlib.suppress(Exception):
            store = create_secret_store(self._settings.secrets, config_path=self._config_path)
            if value:
                store.set(key, value)
            else:
                store.delete(key)
                # Notify app to reset verification status
                if self.on_secret_cleared:
                    self.on_secret_cleared(key)

    def _on_audio_change(self) -> None:
        if not self._settings:
            return

        new_host = self._audio_settings.host_api
        new_device = self._audio_settings.microphone
        old_host = self._settings.audio.input_host_api
        old_device = self._settings.audio.input_device

        if old_host != new_host:
            logger.info(f"[Settings] Audio Host changed: {old_host} -> {new_host}")
        if old_device != new_device:
            logger.info(f"[Settings] Microphone changed: {old_device} -> {new_device}")

        self._settings.audio.input_host_api = new_host
        self._settings.audio.input_device = new_device
        self._emit_settings_changed()

    def _handle_vad_visual_change(self, e) -> None:
        self._vad_slider.label = f"{float(e.control.value):.2f}"
        self._vad_slider.update()

    def _handle_vad_change(self, e) -> None:
        if not self._settings:
            return

        new_vad = float(e.control.value)
        old_vad = self._settings.stt.vad_speech_threshold

        if abs(old_vad - new_vad) > 0.001:
            logger.info(f"[Settings] VAD sensitivity changed: {old_vad:.2f} -> {new_vad:.2f}")

        self._settings.stt.vad_speech_threshold = new_vad
        self._emit_settings_changed()

    def _on_low_latency_click(self, e) -> None:
        """Open low latency mode selection modal."""
        if not self.page:
            return
        options = [
            OptionItem(
                value="on",
                label=t("toggle.on"),
                description=t("toggle.on.description", default=""),
            ),
            OptionItem(
                value="off",
                label=t("toggle.off"),
                description=t("toggle.off.description", default=""),
            ),
        ]
        current = "on" if self._settings.stt.low_latency_mode else "off"
        modal = SettingsModal(
            self.page,
            t("settings.low_latency_mode"),
            options,
            self._on_low_latency_selected,
            show_description=True,
        )
        modal.open(current)

    def _on_low_latency_selected(self, value: str) -> None:
        """Handle low latency mode selection from modal."""
        if not self._settings:
            return
        new_value = value == "on"
        old_value = self._settings.stt.low_latency_mode
        if new_value != old_value:
            logger.info(f"[Settings] Low latency mode changed: {old_value} -> {new_value}")
        self._settings.stt.low_latency_mode = new_value

        # Update text
        self._low_latency_text.content.value = t("toggle.on" if new_value else "toggle.off")
        if self.page:
            self._low_latency_text.update()
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
        self._vad_title.value = t("settings.vad_sensitivity")
        self._low_latency_title.value = t("settings.low_latency_mode")
        self._persona_title.value = t("settings.section.persona")
        self._reset_prompt_btn.text = t("settings.reset_prompt")

        # Update dynamic buttons by replacing the entire style object
        ui_font = font_for_language(get_locale())

        if self._reset_prompt_btn:
            self._reset_prompt_btn.style = self._get_button_style(ui_font)

        if self._qwen_region_btn:
            self._qwen_region_btn.style = self._get_button_style(ui_font)

        # Update text controls with current selection labels

        # Update text controls with current selection labels
        if self._settings:
            self._stt_text.content.value = provider_label(self._settings.provider.stt.value)
            self._llm_text.content.value = provider_label(self._settings.provider.llm.value)
            self._ui_text.content.value = locale_label(self._settings.ui.locale)
            self._low_latency_text.content.value = t(
                "toggle.on" if self._settings.stt.low_latency_mode else "toggle.off"
            )

        # Qwen Region label
        if self._settings:
            region_val = self._settings.qwen.region.value
            self._qwen_region_btn.text = f"{t('settings.qwen_region')} {t(f'region.{region_val}')}"

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

    def refresh_prompt_if_empty(self) -> None:
        """Load default prompt if current is empty."""
        self._prompt_editor.load_default_if_empty()
