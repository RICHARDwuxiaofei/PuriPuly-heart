"""Settings view - Bento grid layout with SegmentedButton providers."""

from __future__ import annotations

import contextlib
import logging
from pathlib import Path
from typing import Callable

import flet as ft

from puripuly_heart.app.wiring import create_secret_store
from puripuly_heart.config.settings import (
    MAX_CUSTOM_VOCAB_TERMS,
    AppSettings,
    GeminiLLMModel,
    LLMProviderName,
    QwenLLMModel,
    QwenRegion,
    STTProviderName,
)
from puripuly_heart.core.language import get_all_language_options, get_stt_compatibility_warning
from puripuly_heart.ui.components.glow import GLOW_CARD, create_glow_stack
from puripuly_heart.ui.components.language_modal import LanguageModal
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
from puripuly_heart.ui.overlay_calibration import (
    OVERLAY_CALIBRATION_ANCHORS,
    OverlayCalibration,
)
from puripuly_heart.ui.theme import (
    COLOR_DIVIDER,
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
        self.on_overlay_toggle: Callable[[bool], None] | None = None
        self.on_providers_changed: Callable[[], None] | None = None
        self.on_verify_api_key: Callable[[str, str], object] | None = None
        self.on_secret_cleared: Callable[[str], None] | None = None  # key name
        self.on_overlay_calibration_begin: Callable[[], OverlayCalibration] | None = None
        self.on_overlay_calibration_change: Callable[[str, object], OverlayCalibration] | None = (
            None
        )
        self.on_overlay_calibration_apply: Callable[[], OverlayCalibration] | None = None
        self.on_overlay_calibration_cancel: Callable[[], OverlayCalibration] | None = None
        self.show_snackbar: Callable[[str, str], None] | None = None

        # State
        self._settings: AppSettings | None = None
        self._config_path: Path | None = None
        self.has_provider_changes: bool = False
        self.provider_change_requires_pipeline: bool = False
        self._custom_vocab_draft_terms: dict[str, str] = {}
        self._overlay_state: str = "off"
        self._overlay_failure_reason: str | None = None
        self._overlay_calibration = OverlayCalibration()
        self._overlay_calibration_draft = self._overlay_calibration.copy()
        self._overlay_calibration_session_active = False

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

    def _build_setting_action_row(self, label: ft.Text, action: ft.Control) -> ft.Row:
        return ft.Row(
            controls=[label, ft.Container(expand=True), action],
            alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
            vertical_alignment=ft.CrossAxisAlignment.CENTER,
        )

    def _build_action_button(self, text: str, on_click) -> ft.TextButton:
        return ft.TextButton(
            text=text,
            style=self._get_button_style(font_for_language(get_locale())),
            on_click=on_click,
        )

    def _build_overlay_calibration_field(
        self,
        *,
        value: float,
        on_blur,
    ) -> ft.TextField:
        return ft.TextField(
            value=self._format_overlay_calibration_number(value),
            text_size=14,
            width=120,
            border_radius=10,
            border_color=COLOR_DIVIDER,
            focused_border_color=COLOR_PRIMARY,
            on_blur=on_blur,
        )

    def _build_overlay_calibration_column(
        self,
        *,
        label: ft.Text,
        control: ft.Control,
    ) -> ft.Column:
        return ft.Column(
            controls=[label, control],
            spacing=6,
            expand=True,
        )

    def _format_overlay_calibration_number(self, value: float) -> str:
        return f"{value:.2f}"

    def _build_ui(self) -> None:
        """Build the settings UI with Bento grid layout."""
        # === Row 1: STT (1x1) + Translation (1x1) ===
        self._stt_text = self._build_clickable_text(
            provider_label(STTProviderName.LOCAL_QWEN.value),
            self._on_stt_click,
        )
        self._stt_title = ft.Text(
            t("settings.section.stt"), size=24, weight=ft.FontWeight.BOLD, color=COLOR_NEUTRAL
        )
        stt_card = self._wrap_card(
            ft.Column([self._stt_title, self._stt_text], spacing=0, expand=True)
        )

        self._llm_text = self._build_clickable_text(
            t("provider.gemini3_flash"),
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
            height=420,
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

        # === Row 5: VRChat Mic Sync (1x1) + Overlay (1x1) ===
        self._vrc_mic_text = self._build_clickable_text(
            t("settings.vrc_mic.on"),
            self._on_vrc_mic_click,
        )
        self._vrc_mic_title = ft.Text(
            t("settings.vrc_mic_intercept"),
            size=24,
            weight=ft.FontWeight.BOLD,
            color=COLOR_NEUTRAL,
        )
        vrc_mic_card = self._wrap_card(
            ft.Column([self._vrc_mic_title, self._vrc_mic_text], spacing=0, expand=True)
        )

        # === Chatbox source toggle card ===
        self._chatbox_source_text = self._build_clickable_text(
            t("settings.chatbox_source.on"),
            self._on_chatbox_source_click,
        )
        self._chatbox_source_title = ft.Text(
            t("settings.chatbox_include_source"),
            size=24,
            weight=ft.FontWeight.BOLD,
            color=COLOR_NEUTRAL,
        )
        chatbox_source_card = self._wrap_card(
            ft.Column(
                [self._chatbox_source_title, self._chatbox_source_text],
                spacing=0,
                expand=True,
            )
        )
        # === Peer language card ===
        self._peer_lang_title = ft.Text(
            t("settings.peer_language"),
            size=24,
            weight=ft.FontWeight.BOLD,
            color=COLOR_NEUTRAL,
        )
        self._peer_source_text = self._build_clickable_text(
            t("settings.peer_language.follow"),
            self._on_peer_source_click,
        )
        self._peer_target_text = self._build_clickable_text(
            t("settings.peer_language.follow"),
            self._on_peer_target_click,
        )
        self._peer_source_label = ft.Text(
            t("settings.peer_language.source"),
            size=16,
            color=COLOR_ON_BACKGROUND,
        )
        self._peer_target_label = ft.Text(
            t("settings.peer_language.target"),
            size=16,
            color=COLOR_ON_BACKGROUND,
        )
        self._peer_stt_text = self._build_clickable_text(
            provider_label(STTProviderName.DEEPGRAM.value),
            self._on_peer_stt_click,
        )
        self._peer_stt_label = ft.Text(
            t("settings.peer_stt_provider"),
            size=16,
            color=COLOR_ON_BACKGROUND,
        )
        self._peer_deepgram_model_text = self._build_clickable_text(
            self._inherit_label(),
            self._on_peer_deepgram_model_click,
        )
        self._peer_deepgram_model_label = ft.Text(
            t("settings.peer_deepgram_model"),
            size=16,
            color=COLOR_ON_BACKGROUND,
        )
        self._peer_qwen_region_text = self._build_clickable_text(
            self._inherit_label(),
            self._on_peer_qwen_region_click,
        )
        self._peer_qwen_region_label = ft.Text(
            t("settings.peer_qwen_region"),
            size=16,
            color=COLOR_ON_BACKGROUND,
        )
        self._peer_qwen_model_text = self._build_clickable_text(
            self._inherit_label(),
            self._on_peer_qwen_model_click,
        )
        self._peer_qwen_model_label = ft.Text(
            t("settings.peer_qwen_model"),
            size=16,
            color=COLOR_ON_BACKGROUND,
        )
        self._peer_soniox_model_text = self._build_clickable_text(
            self._inherit_label(),
            self._on_peer_soniox_model_click,
        )
        self._peer_soniox_model_label = ft.Text(
            t("settings.peer_soniox_model"),
            size=16,
            color=COLOR_ON_BACKGROUND,
        )
        peer_lang_card = self._wrap_card(
            ft.Column(
                [
                    self._peer_lang_title,
                    ft.Container(height=12),
                    self._build_setting_action_row(
                        self._peer_stt_label,
                        self._peer_stt_text,
                    ),
                    self._build_setting_action_row(
                        self._peer_source_label,
                        self._peer_source_text,
                    ),
                    self._build_setting_action_row(
                        self._peer_target_label,
                        self._peer_target_text,
                    ),
                    self._build_setting_action_row(
                        self._peer_deepgram_model_label,
                        self._peer_deepgram_model_text,
                    ),
                    self._build_setting_action_row(
                        self._peer_qwen_region_label,
                        self._peer_qwen_region_text,
                    ),
                    self._build_setting_action_row(
                        self._peer_qwen_model_label,
                        self._peer_qwen_model_text,
                    ),
                    self._build_setting_action_row(
                        self._peer_soniox_model_label,
                        self._peer_soniox_model_text,
                    ),
                ],
                spacing=6,
                expand=True,
            )
        )
        row_chatbox_source = ft.Container(
            content=ft.Row([chatbox_source_card, peer_lang_card], spacing=16, expand=True),
            height=280,
        )

        self._overlay_title = ft.Text(
            t("settings.section.overlay"),
            size=24,
            weight=ft.FontWeight.BOLD,
            color=COLOR_NEUTRAL,
        )
        self._overlay_enabled_label = ft.Text(
            t("settings.overlay.enabled"),
            size=16,
            color=COLOR_ON_BACKGROUND,
        )
        self._peer_translation_label = ft.Text(
            t("settings.peer_translation"),
            size=16,
            color=COLOR_ON_BACKGROUND,
        )
        self._overlay_translation_label = ft.Text(
            t("settings.overlay.show_translation"),
            size=16,
            color=COLOR_ON_BACKGROUND,
        )
        self._overlay_peer_original_label = ft.Text(
            t("settings.overlay.show_peer_original"),
            size=16,
            color=COLOR_ON_BACKGROUND,
        )
        self._integrated_context_label = ft.Text(
            t("settings.integrated_context"),
            size=16,
            color=COLOR_ON_BACKGROUND,
        )
        self._overlay_enabled_button = self._build_action_button(
            t("settings.option.off"),
            self._on_overlay_click,
        )
        self._peer_translation_button = self._build_action_button(
            t("settings.option.off"),
            self._on_peer_translation_click,
        )
        self._overlay_translation_button = self._build_action_button(
            t("settings.option.on"),
            self._on_overlay_translation_click,
        )
        self._overlay_peer_original_button = self._build_action_button(
            t("settings.option.on"),
            self._on_overlay_peer_original_click,
        )
        self._integrated_context_button = self._build_action_button(
            t("settings.context.local"),
            self._on_integrated_context_click,
        )
        self._peer_translation_hint = ft.Text("", size=13, color=COLOR_NEUTRAL)
        self._integrated_context_hint = ft.Text("", size=13, color=COLOR_NEUTRAL)
        self._overlay_status_text = ft.Text(
            "",
            size=14,
            color=COLOR_NEUTRAL,
        )
        self._overlay_calibration_title = ft.Text(
            t("settings.overlay.calibration"),
            size=16,
            weight=ft.FontWeight.BOLD,
            color=COLOR_NEUTRAL,
        )
        self._overlay_anchor_label = ft.Text(
            t("settings.overlay.calibration.anchor"),
            size=14,
            color=COLOR_NEUTRAL,
        )
        self._overlay_offset_x_label = ft.Text(
            t("settings.overlay.calibration.offset_x"),
            size=14,
            color=COLOR_NEUTRAL,
        )
        self._overlay_offset_y_label = ft.Text(
            t("settings.overlay.calibration.offset_y"),
            size=14,
            color=COLOR_NEUTRAL,
        )
        self._overlay_distance_label = ft.Text(
            t("settings.overlay.calibration.distance"),
            size=14,
            color=COLOR_NEUTRAL,
        )
        self._overlay_text_scale_label = ft.Text(
            t("settings.overlay.calibration.text_scale"),
            size=14,
            color=COLOR_NEUTRAL,
        )
        self._overlay_anchor_dropdown = ft.Dropdown(
            value=self._overlay_calibration.anchor,
            options=[
                ft.dropdown.Option(
                    key=anchor,
                    text=t(f"settings.overlay.calibration.anchor.{anchor}"),
                )
                for anchor in OVERLAY_CALIBRATION_ANCHORS
            ],
            text_size=14,
            border_radius=10,
            border_color=COLOR_DIVIDER,
            focused_border_color=COLOR_PRIMARY,
            on_change=self._on_overlay_anchor_change,
        )
        self._overlay_offset_x_field = self._build_overlay_calibration_field(
            value=self._overlay_calibration.offset_x,
            on_blur=lambda e: self._on_overlay_calibration_numeric_blur("offset_x", e),
        )
        self._overlay_offset_y_field = self._build_overlay_calibration_field(
            value=self._overlay_calibration.offset_y,
            on_blur=lambda e: self._on_overlay_calibration_numeric_blur("offset_y", e),
        )
        self._overlay_distance_field = self._build_overlay_calibration_field(
            value=self._overlay_calibration.distance,
            on_blur=lambda e: self._on_overlay_calibration_numeric_blur("distance", e),
        )
        self._overlay_text_scale_field = self._build_overlay_calibration_field(
            value=self._overlay_calibration.text_scale,
            on_blur=lambda e: self._on_overlay_calibration_numeric_blur("text_scale", e),
        )
        self._overlay_calibration_apply_button = self._build_action_button(
            t("settings.overlay.calibration.apply"),
            self._on_overlay_calibration_apply,
        )
        self._overlay_calibration_cancel_button = self._build_action_button(
            t("settings.overlay.calibration.cancel"),
            self._on_overlay_calibration_cancel,
        )
        self._overlay_calibration_reset_button = self._build_action_button(
            t("settings.overlay.calibration.reset"),
            self._on_overlay_calibration_reset,
        )
        overlay_card = self._wrap_card(
            ft.Column(
                [
                    self._overlay_title,
                    ft.Container(height=12),
                    self._build_setting_action_row(
                        self._overlay_enabled_label,
                        self._overlay_enabled_button,
                    ),
                    self._build_setting_action_row(
                        self._peer_translation_label,
                        self._peer_translation_button,
                    ),
                    self._peer_translation_hint,
                    self._build_setting_action_row(
                        self._overlay_translation_label,
                        self._overlay_translation_button,
                    ),
                    self._build_setting_action_row(
                        self._overlay_peer_original_label,
                        self._overlay_peer_original_button,
                    ),
                    self._build_setting_action_row(
                        self._integrated_context_label,
                        self._integrated_context_button,
                    ),
                    self._integrated_context_hint,
                    ft.Container(height=12),
                    self._overlay_status_text,
                ],
                spacing=6,
                expand=True,
            )
        )
        row5 = ft.Container(
            content=ft.Row([vrc_mic_card, overlay_card], spacing=16, expand=True),
            height=380,
        )

        # === Row 6: Overlay Calibration (2x1) ===
        overlay_calibration_card = self._wrap_card(
            ft.Column(
                [
                    self._overlay_calibration_title,
                    ft.Container(height=12),
                    ft.Row(
                        controls=[
                            self._build_overlay_calibration_column(
                                label=self._overlay_anchor_label,
                                control=self._overlay_anchor_dropdown,
                            ),
                            self._build_overlay_calibration_column(
                                label=self._overlay_distance_label,
                                control=self._overlay_distance_field,
                            ),
                        ],
                        spacing=12,
                    ),
                    ft.Row(
                        controls=[
                            self._build_overlay_calibration_column(
                                label=self._overlay_offset_x_label,
                                control=self._overlay_offset_x_field,
                            ),
                            self._build_overlay_calibration_column(
                                label=self._overlay_offset_y_label,
                                control=self._overlay_offset_y_field,
                            ),
                        ],
                        spacing=12,
                    ),
                    ft.Row(
                        controls=[
                            self._build_overlay_calibration_column(
                                label=self._overlay_text_scale_label,
                                control=self._overlay_text_scale_field,
                            ),
                        ],
                        spacing=12,
                    ),
                    ft.Row(
                        controls=[
                            self._overlay_calibration_apply_button,
                            self._overlay_calibration_cancel_button,
                            self._overlay_calibration_reset_button,
                        ],
                        spacing=12,
                    ),
                ],
                spacing=6,
            ),
            expand=False,
        )

        # === Row 7: Persona (2x2) - Licenses style ===
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
            ft.Column(
                [
                    persona_header,
                    ft.Container(height=16),
                    prompt_container,
                ],
                spacing=0,
            ),
        )

        # === Row 8: Custom Vocabulary (2x1) ===
        self._custom_vocab_title = ft.Text(
            t("settings.section.custom_vocabulary"),
            size=24,
            weight=ft.FontWeight.BOLD,
            color=COLOR_NEUTRAL,
        )
        self._custom_vocab_info_icon = ft.Icon(
            name=ft.Icons.INFO_OUTLINE,
            color=COLOR_NEUTRAL,
            size=24,
            tooltip=t("settings.custom_vocabulary_tooltip"),
        )
        custom_vocab_header = ft.Row(
            controls=[
                self._custom_vocab_title,
                ft.Container(expand=True),
                self._custom_vocab_info_icon,
            ],
            alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
            vertical_alignment=ft.CrossAxisAlignment.CENTER,
        )
        self._custom_vocab_terms = ft.TextField(
            multiline=True,
            min_lines=5,
            helper_text="",
            border_radius=12,
            border_color=COLOR_DIVIDER,
            focused_border_color=COLOR_PRIMARY,
            text_size=16,
            color=COLOR_ON_BACKGROUND,
            on_change=self._on_custom_vocabulary_terms_change,
            on_blur=self._on_custom_vocabulary_terms_blur,
        )
        row7 = self._wrap_card(
            ft.Column(
                [
                    custom_vocab_header,
                    ft.Container(height=16),
                    self._custom_vocab_terms,
                ],
                spacing=0,
            ),
            expand=False,
        )

        self.controls = [
            row1,
            row2,
            row3,
            row4,
            row5,
            row_chatbox_source,
            overlay_calibration_card,
            persona_card,
            row7,
        ]

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

    def _get_llm_modal_value(self, settings: AppSettings) -> str:
        if settings.provider.llm == LLMProviderName.GEMINI:
            return settings.gemini.llm_model.value
        return settings.qwen.llm_model.value

    def _get_llm_display_label(self, settings: AppSettings) -> str:
        if settings.provider.llm == LLMProviderName.GEMINI:
            if settings.gemini.llm_model == GeminiLLMModel.GEMINI_31_FLASH_LITE:
                return t("provider.gemini31_flash_lite")
            return t("provider.gemini3_flash")
        if settings.qwen.llm_model == QwenLLMModel.QWEN_35_PLUS:
            return t("provider.qwen35_plus")
        return t("provider.qwen35_flash")

    def _active_prompt_key(self) -> str:
        if not self._settings or self._settings.provider.llm == LLMProviderName.GEMINI:
            return "gemini"
        return "qwen"

    def _current_source_language(self) -> str:
        if not self._settings:
            return "en"
        return self._settings.languages.source_language

    def _set_custom_vocabulary_draft_from_settings(self, *, preserve_existing: bool) -> None:
        if not self._settings:
            self._custom_vocab_draft_terms = {}
            self._custom_vocab_terms.value = ""
            return

        source_language = self._current_source_language()
        if not preserve_existing:
            self._custom_vocab_draft_terms = {
                language: "\n".join(terms)
                for language, terms in self._settings.stt.custom_terms.items()
            }
        current_value = self._custom_vocab_draft_terms.get(
            source_language,
            "\n".join(self._settings.stt.custom_terms.get(source_language, [])),
        )
        self._custom_vocab_draft_terms[source_language] = current_value
        self._custom_vocab_terms.value = current_value

    def _parse_custom_vocabulary_terms(self) -> tuple[list[str], int]:
        terms: list[str] = []
        seen_terms: set[str] = set()
        unique_count = 0
        for line in (self._custom_vocab_terms.value or "").splitlines():
            normalized = line.strip()
            if not normalized or normalized in seen_terms:
                continue
            seen_terms.add(normalized)
            unique_count += 1
            if len(terms) >= MAX_CUSTOM_VOCAB_TERMS:
                continue
            terms.append(normalized)
        return terms, unique_count

    def _inherit_label(self) -> str:
        return t("settings.peer_provider.follow_self")

    def _peer_deepgram_model_label_for(self, settings: AppSettings | None) -> str:
        if settings is None or settings.peer_deepgram_stt.model is None:
            return self._inherit_label()
        return settings.peer_deepgram_stt.model

    def _peer_qwen_region_label_for(self, settings: AppSettings | None) -> str:
        if settings is None or settings.peer_qwen_asr_stt.region is None:
            return self._inherit_label()
        return t(f"region.{settings.peer_qwen_asr_stt.region.value}")

    def _peer_qwen_model_label_for(self, settings: AppSettings | None) -> str:
        if settings is None or settings.peer_qwen_asr_stt.model is None:
            return self._inherit_label()
        return settings.peer_qwen_asr_stt.model

    def _peer_soniox_model_label_for(self, settings: AppSettings | None) -> str:
        if settings is None or settings.peer_soniox_stt.model is None:
            return self._inherit_label()
        return settings.peer_soniox_stt.model

    # --- Load Settings ---
    def load_from_settings(
        self,
        settings: AppSettings,
        *,
        config_path: Path,
        preserve_custom_vocab_draft: bool = False,
    ) -> None:
        """Load current settings into the UI."""
        self._settings = settings
        self._config_path = config_path
        self.has_provider_changes = False
        self.provider_change_requires_pipeline = False

        # UI Language
        self._ui_text.content.value = locale_label(settings.ui.locale)

        # STT Provider
        self._stt_text.content.value = provider_label(settings.provider.stt.value)
        self._peer_stt_text.content.value = provider_label(settings.provider.peer_stt.value)
        self._peer_deepgram_model_text.content.value = self._peer_deepgram_model_label_for(
            settings
        )
        self._peer_qwen_region_text.content.value = self._peer_qwen_region_label_for(settings)
        self._peer_qwen_model_text.content.value = self._peer_qwen_model_label_for(settings)
        self._peer_soniox_model_text.content.value = self._peer_soniox_model_label_for(settings)
        self._update_api_visibility()

        # LLM Provider
        self._llm_text.content.value = self._get_llm_display_label(settings)

        # Qwen Region
        region_label = t(f"region.{settings.qwen.region.value}")
        self._qwen_region_btn.text = f"{t('settings.qwen_region')} {region_label}"

        # Audio Settings
        self._audio_settings.host_api = settings.audio.input_host_api
        self._audio_settings.microphone = settings.audio.input_device
        self._audio_settings.desktop_output_device = settings.desktop_audio.output_device
        self._audio_settings.desktop_vad_threshold = settings.desktop_audio.vad_speech_threshold
        self._audio_settings.desktop_hangover_ms = settings.desktop_audio.vad_hangover_ms
        self._audio_settings.desktop_pre_roll_ms = settings.desktop_audio.vad_pre_roll_ms

        # VAD
        self._vad_slider.value = settings.stt.vad_speech_threshold
        self._vad_slider.label = f"{settings.stt.vad_speech_threshold:.2f}"
        self._low_latency_text.content.value = t(
            "toggle.on" if settings.stt.low_latency_mode else "toggle.off"
        )
        # --- 新增：读取 VRChat 同步开关状态 ---
        self._vrc_mic_text.content.value = t(
            "settings.vrc_mic.on" if settings.osc.vrc_mic_intercept else "settings.vrc_mic.off"
        )
        self._chatbox_source_text.content.value = t(
            "settings.chatbox_source.on"
            if settings.osc.chatbox_include_source
            else "settings.chatbox_source.off"
        )
        self._peer_source_text.content.value = self._peer_lang_display(
            settings.languages.peer_source_language
        )
        self._peer_target_text.content.value = self._peer_lang_display(
            settings.languages.peer_target_language
        )

        # Prompt
        provider_name = "gemini" if settings.provider.llm == LLMProviderName.GEMINI else "qwen"
        self._prompt_editor.set_provider(provider_name)
        stored_prompt = settings.system_prompts.get(provider_name, "").strip()
        if stored_prompt:
            self._prompt_editor.value = stored_prompt
            settings.system_prompt = stored_prompt
        elif settings.system_prompt.strip():
            self._prompt_editor.value = settings.system_prompt
            settings.system_prompts[provider_name] = settings.system_prompt
        else:
            self._prompt_editor.load_default_prompt()
            settings.system_prompt = self._prompt_editor.value
            settings.system_prompts[provider_name] = settings.system_prompt

        self._set_custom_vocabulary_draft_from_settings(
            preserve_existing=preserve_custom_vocab_draft
        )
        self._custom_vocab_terms.helper_text = ""
        self._sync_overlay_controls()
        self._sync_overlay_calibration_controls()

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
        peer_stt = self._settings.provider.peer_stt
        peer_enabled = bool(self._settings.ui.peer_translation_enabled)

        active_stt_providers = {stt}
        if peer_enabled:
            active_stt_providers.add(peer_stt)
        self._deepgram_key.visible = STTProviderName.DEEPGRAM in active_stt_providers
        self._soniox_key.visible = STTProviderName.SONIOX in active_stt_providers

        self._google_key.visible = llm == LLMProviderName.GEMINI

        qwen_regions: set[QwenRegion] = set()
        if stt == STTProviderName.QWEN_ASR or llm == LLMProviderName.QWEN:
            qwen_regions.add(self._settings.qwen.region)
        if peer_enabled and peer_stt == STTProviderName.QWEN_ASR:
            qwen_regions.add(self._settings.peer_qwen_asr_stt.region or self._settings.qwen.region)

        self._qwen_region_btn.visible = stt == STTProviderName.QWEN_ASR or llm == LLMProviderName.QWEN
        self._alibaba_key_beijing.visible = QwenRegion.BEIJING in qwen_regions
        self._alibaba_key_singapore.visible = QwenRegion.SINGAPORE in qwen_regions
        self._update_peer_provider_visibility()

    def _update_peer_provider_visibility(self) -> None:
        if not self._settings:
            return

        peer_stt = self._settings.provider.peer_stt
        show_deepgram = peer_stt == STTProviderName.DEEPGRAM
        show_qwen = peer_stt == STTProviderName.QWEN_ASR
        show_soniox = peer_stt == STTProviderName.SONIOX

        self._peer_deepgram_model_label.visible = show_deepgram
        self._peer_deepgram_model_text.visible = show_deepgram
        self._peer_qwen_region_label.visible = show_qwen
        self._peer_qwen_region_text.visible = show_qwen
        self._peer_qwen_model_label.visible = show_qwen
        self._peer_qwen_model_text.visible = show_qwen
        self._peer_soniox_model_label.visible = show_soniox
        self._peer_soniox_model_text.visible = show_soniox

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
            self._settings.provider.stt.value
            if self._settings
            else STTProviderName.LOCAL_QWEN.value
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
        self.provider_change_requires_pipeline = True

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

    def _on_peer_stt_click(self, e) -> None:
        if not self.page:
            return
        options = [
            OptionItem(
                value=provider.value,
                label=provider_label(provider.value),
                description=t(f"provider.{provider.value}.description", default=""),
            )
            for provider in STTProviderName
        ]
        current = (
            self._settings.provider.peer_stt.value
            if self._settings
            else STTProviderName.DEEPGRAM.value
        )
        SettingsModal(
            self.page,
            t("settings.peer_stt_provider"),
            options,
            self._on_peer_stt_selected,
            show_description=True,
        ).open(current)

    def _on_peer_stt_selected(self, value: str) -> None:
        if not self._settings:
            return
        self._settings.provider.peer_stt = STTProviderName(value)
        self._peer_stt_text.content.value = provider_label(value)
        self._update_api_visibility()
        if self.page:
            self._peer_stt_text.update()
            self._api_keys_column.update()
        self.has_provider_changes = True
        self.provider_change_requires_pipeline = True
        self._emit_settings_changed()

    def _on_peer_deepgram_model_click(self, e) -> None:
        if not self.page or not self._settings:
            return
        options = [
            OptionItem(value="", label=self._inherit_label()),
            OptionItem(value="nova-3", label="nova-3"),
            OptionItem(value="nova-3-general", label="nova-3-general"),
        ]
        SettingsModal(
            self.page,
            t("settings.peer_deepgram_model"),
            options,
            self._on_peer_deepgram_model_selected,
            show_description=False,
        ).open(self._settings.peer_deepgram_stt.model or "")

    def _on_peer_deepgram_model_selected(self, value: str) -> None:
        if not self._settings:
            return
        self._settings.peer_deepgram_stt.model = value or None
        self._peer_deepgram_model_text.content.value = self._peer_deepgram_model_label_for(
            self._settings
        )
        if self.page:
            self._peer_deepgram_model_text.update()
        self.has_provider_changes = True
        self.provider_change_requires_pipeline = True
        self._emit_settings_changed()

    def _on_peer_qwen_region_click(self, e) -> None:
        if not self.page or not self._settings:
            return
        options = [
            OptionItem(value="", label=self._inherit_label()),
            OptionItem(value=QwenRegion.BEIJING.value, label=t("region.beijing")),
            OptionItem(value=QwenRegion.SINGAPORE.value, label=t("region.singapore")),
        ]
        SettingsModal(
            self.page,
            t("settings.peer_qwen_region"),
            options,
            self._on_peer_qwen_region_selected,
            show_description=False,
        ).open(self._settings.peer_qwen_asr_stt.region.value if self._settings.peer_qwen_asr_stt.region else "")

    def _on_peer_qwen_region_selected(self, value: str) -> None:
        if not self._settings:
            return
        self._settings.peer_qwen_asr_stt.region = QwenRegion(value) if value else None
        self._peer_qwen_region_text.content.value = self._peer_qwen_region_label_for(
            self._settings
        )
        self._update_api_visibility()
        if self.page:
            self._peer_qwen_region_text.update()
            self._api_keys_column.update()
        self.has_provider_changes = True
        self.provider_change_requires_pipeline = True
        self._emit_settings_changed()

    def _on_peer_qwen_model_click(self, e) -> None:
        if not self.page or not self._settings:
            return
        options = [
            OptionItem(value="", label=self._inherit_label()),
            OptionItem(value="qwen3-asr-flash-realtime", label="qwen3-asr-flash-realtime"),
        ]
        SettingsModal(
            self.page,
            t("settings.peer_qwen_model"),
            options,
            self._on_peer_qwen_model_selected,
            show_description=False,
        ).open(self._settings.peer_qwen_asr_stt.model or "")

    def _on_peer_qwen_model_selected(self, value: str) -> None:
        if not self._settings:
            return
        self._settings.peer_qwen_asr_stt.model = value or None
        self._peer_qwen_model_text.content.value = self._peer_qwen_model_label_for(self._settings)
        if self.page:
            self._peer_qwen_model_text.update()
        self.has_provider_changes = True
        self.provider_change_requires_pipeline = True
        self._emit_settings_changed()

    def _on_peer_soniox_model_click(self, e) -> None:
        if not self.page or not self._settings:
            return
        options = [
            OptionItem(value="", label=self._inherit_label()),
            OptionItem(value="stt-rt-v4", label="stt-rt-v4"),
        ]
        SettingsModal(
            self.page,
            t("settings.peer_soniox_model"),
            options,
            self._on_peer_soniox_model_selected,
            show_description=False,
        ).open(self._settings.peer_soniox_stt.model or "")

    def _on_peer_soniox_model_selected(self, value: str) -> None:
        if not self._settings:
            return
        self._settings.peer_soniox_stt.model = value or None
        self._peer_soniox_model_text.content.value = self._peer_soniox_model_label_for(
            self._settings
        )
        if self.page:
            self._peer_soniox_model_text.update()
        self.has_provider_changes = True
        self.provider_change_requires_pipeline = True
        self._emit_settings_changed()

    def _on_llm_click(self, e) -> None:
        """Open LLM provider selection modal."""
        if not self.page:
            return
        options = [
            OptionItem(
                value=GeminiLLMModel.GEMINI_3_FLASH.value,
                label=t("provider.gemini3_flash"),
                description=t("provider.gemini3_flash.description", default=""),
            ),
            OptionItem(
                value=GeminiLLMModel.GEMINI_31_FLASH_LITE.value,
                label=t("provider.gemini31_flash_lite"),
                description=t("provider.gemini31_flash_lite.description", default=""),
            ),
            OptionItem(
                value=QwenLLMModel.QWEN_35_PLUS.value,
                label=t("provider.qwen35_plus"),
                description=t("provider.qwen35_plus.description", default=""),
            ),
            OptionItem(
                value=QwenLLMModel.QWEN_35_FLASH.value,
                label=t("provider.qwen35_flash"),
                description=t("provider.qwen35_flash.description", default=""),
            ),
        ]
        current = (
            self._get_llm_modal_value(self._settings)
            if self._settings
            else GeminiLLMModel.GEMINI_3_FLASH.value
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
        old_provider = self._settings.provider.llm
        old_gemini_model = self._settings.gemini.llm_model
        old_qwen_model = self._settings.qwen.llm_model

        if value == LLMProviderName.GEMINI.value:
            provider = LLMProviderName.GEMINI
            gemini_model = GeminiLLMModel.GEMINI_3_FLASH
            qwen_model = old_qwen_model
        elif value == GeminiLLMModel.GEMINI_3_FLASH.value:
            provider = LLMProviderName.GEMINI
            gemini_model = GeminiLLMModel.GEMINI_3_FLASH
            qwen_model = old_qwen_model
        elif value == GeminiLLMModel.GEMINI_31_FLASH_LITE.value:
            provider = LLMProviderName.GEMINI
            gemini_model = GeminiLLMModel.GEMINI_31_FLASH_LITE
            qwen_model = old_qwen_model
        elif value == QwenLLMModel.QWEN_35_PLUS.value:
            provider = LLMProviderName.QWEN
            gemini_model = old_gemini_model
            qwen_model = QwenLLMModel.QWEN_35_PLUS
        else:
            provider = LLMProviderName.QWEN
            gemini_model = old_gemini_model
            qwen_model = QwenLLMModel.QWEN_35_FLASH

        changes: list[str] = []
        if old_provider != provider:
            changes.append(f"provider={old_provider.value}->{provider.value}")
        if old_gemini_model != gemini_model:
            changes.append(f"gemini_model={old_gemini_model.value}->{gemini_model.value}")
        if old_qwen_model != qwen_model:
            changes.append(f"qwen_model={old_qwen_model.value}->{qwen_model.value}")
        if changes:
            logger.info("[Settings] LLM selection changed: %s", ", ".join(changes))

        self._settings.provider.llm = provider
        if provider == LLMProviderName.QWEN:
            self._settings.qwen.llm_model = qwen_model
        else:
            self._settings.gemini.llm_model = gemini_model
        llm_changed = (
            old_provider != provider
            or (
                provider == LLMProviderName.QWEN and old_qwen_model != self._settings.qwen.llm_model
            )
            or (
                provider == LLMProviderName.GEMINI
                and old_gemini_model != self._settings.gemini.llm_model
            )
        )
        self._update_api_visibility()
        self.has_provider_changes = llm_changed

        # Update text
        self._llm_text.content.value = self._get_llm_display_label(self._settings)

        # Update prompt if provider changed
        if old_provider != provider:
            provider_name = "gemini" if provider == LLMProviderName.GEMINI else "qwen"
            self._prompt_editor.set_provider(provider_name)
            next_prompt = self._settings.system_prompts.get(provider_name, "").strip()
            if next_prompt:
                self._prompt_editor.value = next_prompt
            else:
                self._prompt_editor.load_default_prompt()
                next_prompt = self._prompt_editor.value
                self._settings.system_prompts[provider_name] = next_prompt
            self._settings.system_prompt = next_prompt

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
        self.provider_change_requires_pipeline = True

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
        new_desktop_output = self._audio_settings.desktop_output_device
        new_desktop_vad = self._audio_settings.desktop_vad_threshold
        new_desktop_hangover = self._audio_settings.desktop_hangover_ms
        new_desktop_pre_roll = self._audio_settings.desktop_pre_roll_ms
        old_host = self._settings.audio.input_host_api
        old_device = self._settings.audio.input_device
        old_desktop_output = self._settings.desktop_audio.output_device
        old_desktop_vad = self._settings.desktop_audio.vad_speech_threshold
        old_desktop_hangover = self._settings.desktop_audio.vad_hangover_ms
        old_desktop_pre_roll = self._settings.desktop_audio.vad_pre_roll_ms

        if old_host != new_host:
            logger.info(f"[Settings] Audio Host changed: {old_host} -> {new_host}")
        if old_device != new_device:
            logger.info(f"[Settings] Microphone changed: {old_device} -> {new_device}")
        if old_desktop_output != new_desktop_output:
            logger.info(
                "[Settings] Desktop loopback output changed: %s -> %s",
                old_desktop_output,
                new_desktop_output,
            )
        if abs(old_desktop_vad - new_desktop_vad) > 0.001:
            logger.info(
                "[Settings] Desktop loopback VAD threshold changed: %.2f -> %.2f",
                old_desktop_vad,
                new_desktop_vad,
            )
        if old_desktop_hangover != new_desktop_hangover:
            logger.info(
                "[Settings] Desktop loopback hangover changed: %s -> %s",
                old_desktop_hangover,
                new_desktop_hangover,
            )
        if old_desktop_pre_roll != new_desktop_pre_roll:
            logger.info(
                "[Settings] Desktop loopback pre-roll changed: %s -> %s",
                old_desktop_pre_roll,
                new_desktop_pre_roll,
            )

        self._settings.audio.input_host_api = new_host
        self._settings.audio.input_device = new_device
        self._settings.desktop_audio.output_device = new_desktop_output
        self._settings.desktop_audio.vad_speech_threshold = new_desktop_vad
        self._settings.desktop_audio.vad_hangover_ms = new_desktop_hangover
        self._settings.desktop_audio.vad_pre_roll_ms = new_desktop_pre_roll
        self._emit_settings_changed()

    def set_overlay_calibration(self, calibration: OverlayCalibration) -> None:
        calibration.validate()
        self._overlay_calibration = calibration.copy()
        self._overlay_calibration_draft = calibration.copy()
        self._overlay_calibration_session_active = False
        self._sync_overlay_calibration_controls(self._overlay_calibration)

    def _sync_overlay_calibration_controls(
        self,
        calibration: OverlayCalibration | None = None,
    ) -> None:
        current = (calibration or self._overlay_calibration).copy()
        self._overlay_anchor_dropdown.value = current.anchor
        self._overlay_offset_x_field.value = self._format_overlay_calibration_number(
            current.offset_x
        )
        self._overlay_offset_y_field.value = self._format_overlay_calibration_number(
            current.offset_y
        )
        self._overlay_distance_field.value = self._format_overlay_calibration_number(
            current.distance
        )
        self._overlay_text_scale_field.value = self._format_overlay_calibration_number(
            current.text_scale
        )

    def _begin_overlay_calibration_session(self) -> OverlayCalibration:
        if self._overlay_calibration_session_active:
            return self._overlay_calibration_draft.copy()

        if self.on_overlay_calibration_begin:
            calibration = self.on_overlay_calibration_begin()
        else:
            calibration = self._overlay_calibration.copy()

        calibration.validate()
        self._overlay_calibration_draft = calibration.copy()
        self._overlay_calibration_session_active = True
        self._sync_overlay_calibration_controls(self._overlay_calibration_draft)
        return self._overlay_calibration_draft.copy()

    def _update_overlay_calibration_draft(
        self,
        field_name: str,
        value: object,
    ) -> OverlayCalibration:
        self._begin_overlay_calibration_session()

        if self.on_overlay_calibration_change:
            calibration = self.on_overlay_calibration_change(field_name, value)
            calibration.validate()
            self._overlay_calibration_draft = calibration.copy()
        else:
            if field_name == "anchor":
                setattr(self._overlay_calibration_draft, field_name, str(value))
            else:
                setattr(self._overlay_calibration_draft, field_name, float(value))
            self._overlay_calibration_draft.validate()

        self._sync_overlay_calibration_controls(self._overlay_calibration_draft)
        return self._overlay_calibration_draft.copy()

    def _on_overlay_anchor_change(self, e) -> None:
        if getattr(e.control, "value", None) is None:
            self._sync_overlay_calibration_controls(
                self._overlay_calibration_draft
                if self._overlay_calibration_session_active
                else self._overlay_calibration
            )
            return
        self._update_overlay_calibration_draft("anchor", e.control.value)

    def _on_overlay_calibration_numeric_blur(self, field_name: str, e) -> None:
        raw_value = str(getattr(e.control, "value", "")).strip()
        try:
            value = float(raw_value)
        except (TypeError, ValueError):
            self._sync_overlay_calibration_controls(
                self._overlay_calibration_draft
                if self._overlay_calibration_session_active
                else self._overlay_calibration
            )
            return

        try:
            self._update_overlay_calibration_draft(field_name, value)
        except ValueError:
            self._sync_overlay_calibration_controls(
                self._overlay_calibration_draft
                if self._overlay_calibration_session_active
                else self._overlay_calibration
            )

    def _commit_overlay_calibration_form_values(self) -> bool:
        current = (
            self._overlay_calibration_draft
            if self._overlay_calibration_session_active
            else self._overlay_calibration
        )
        anchor_value = self._overlay_anchor_dropdown.value or current.anchor
        offset_x_raw = (self._overlay_offset_x_field.value or "").strip()
        offset_y_raw = (self._overlay_offset_y_field.value or "").strip()
        distance_raw = (self._overlay_distance_field.value or "").strip()
        text_scale_raw = (self._overlay_text_scale_field.value or "").strip()
        try:
            self._update_overlay_calibration_draft("anchor", anchor_value)
            self._update_overlay_calibration_draft("offset_x", float(offset_x_raw))
            self._update_overlay_calibration_draft("offset_y", float(offset_y_raw))
            self._update_overlay_calibration_draft("distance", float(distance_raw))
            self._update_overlay_calibration_draft("text_scale", float(text_scale_raw))
        except (TypeError, ValueError):
            self._sync_overlay_calibration_controls(current)
            return False

        return True

    def _on_overlay_calibration_apply(self, e) -> None:
        _ = e
        if not self._commit_overlay_calibration_form_values():
            return
        if self.on_overlay_calibration_apply:
            calibration = self.on_overlay_calibration_apply()
        else:
            if not self._overlay_calibration_session_active:
                self._begin_overlay_calibration_session()
            calibration = self._overlay_calibration_draft.copy()

        calibration.validate()
        self._overlay_calibration = calibration.copy()
        self._overlay_calibration_draft = calibration.copy()
        self._overlay_calibration_session_active = False
        self._sync_overlay_calibration_controls(self._overlay_calibration)

        if self.page:
            self.update()

    def _on_overlay_calibration_cancel(self, e) -> None:
        _ = e
        if self.on_overlay_calibration_cancel:
            calibration = self.on_overlay_calibration_cancel()
            calibration.validate()
            self._overlay_calibration = calibration.copy()

        self._overlay_calibration_draft = self._overlay_calibration.copy()
        self._overlay_calibration_session_active = False
        self._sync_overlay_calibration_controls(self._overlay_calibration)

        if self.page:
            self.update()

    def _overlay_connected(self) -> bool:
        return self._overlay_state == "connected"

    def _compose_overlay_status_text(self) -> str:
        state_label = t(
            f"settings.overlay.status.{self._overlay_state}", default=self._overlay_state
        )
        if self._overlay_state == "failed" and self._overlay_failure_reason:
            reason_label = t(
                f"settings.overlay.failure.{self._overlay_failure_reason}",
                default=self._overlay_failure_reason,
            )
            return t(
                "settings.overlay.status.failed_with_reason",
                status=state_label,
                reason=reason_label,
                default=f"{state_label}: {reason_label}",
            )
        return state_label

    def _sync_overlay_controls(self) -> None:
        overlay_enabled = bool(self._settings and self._settings.ui.overlay_enabled)
        overlay_translation_enabled = bool(
            self._settings and self._settings.ui.show_overlay_translation
        )
        overlay_peer_original_enabled = bool(
            self._settings and self._settings.ui.show_overlay_peer_original
        )
        peer_translation_enabled = bool(
            self._settings and self._settings.ui.peer_translation_enabled
        )
        integrated_context_enabled = bool(
            self._settings and self._settings.ui.integrated_context_enabled
        )

        self._overlay_enabled_button.text = t(
            "settings.option.on" if overlay_enabled else "settings.option.off"
        )
        self._peer_translation_button.text = t(
            "settings.option.on" if peer_translation_enabled else "settings.option.off"
        )
        self._overlay_translation_button.text = t(
            "settings.option.on" if overlay_translation_enabled else "settings.option.off"
        )
        self._overlay_peer_original_button.text = t(
            "settings.option.on" if overlay_peer_original_enabled else "settings.option.off"
        )
        self._integrated_context_button.text = t(
            "settings.context.integrated"
            if integrated_context_enabled
            else "settings.context.local"
        )

        peer_translation_available = self._overlay_connected()
        integrated_context_available = peer_translation_available and peer_translation_enabled

        self._overlay_enabled_button.disabled = self._settings is None
        self._overlay_translation_button.disabled = self._settings is None
        self._overlay_peer_original_button.disabled = self._settings is None
        self._peer_translation_button.disabled = not peer_translation_available
        self._integrated_context_button.disabled = not integrated_context_available

        self._peer_translation_hint.value = (
            ""
            if peer_translation_available
            else t("settings.peer_translation.disabled.overlay_required")
        )
        if integrated_context_available:
            self._integrated_context_hint.value = ""
        elif not peer_translation_available:
            self._integrated_context_hint.value = t(
                "settings.integrated_context.disabled.overlay_required"
            )
        else:
            self._integrated_context_hint.value = t(
                "settings.integrated_context.disabled.peer_translation_required"
            )

        self._overlay_status_text.value = self._compose_overlay_status_text()

        if self.page:
            self.update()

    def set_overlay_runtime_state(
        self,
        state: str,
        *,
        failure_reason: str | None = None,
    ) -> None:
        self._overlay_state = state
        self._overlay_failure_reason = failure_reason
        self._sync_overlay_controls()

    def _on_overlay_calibration_reset(self, e) -> None:
        _ = e
        self._begin_overlay_calibration_session()
        self._overlay_calibration_draft = OverlayCalibration()
        self._sync_overlay_calibration_controls(self._overlay_calibration_draft)

        if self.page:
            self.update()

    def _on_overlay_click(self, e) -> None:
        if not self.page or not self._settings:
            return
        options = [
            OptionItem(value="on", label=t("settings.option.on")),
            OptionItem(value="off", label=t("settings.option.off")),
        ]
        modal = SettingsModal(
            self.page,
            t("settings.overlay.enabled"),
            options,
            self._on_overlay_selected,
            show_description=False,
        )
        modal.open("on" if self._settings.ui.overlay_enabled else "off")

    def _on_overlay_selected(self, value: str) -> None:
        if not self._settings:
            return
        enabled = value == "on"
        self._settings.ui.overlay_enabled = enabled
        if not enabled:
            self._settings.ui.peer_translation_enabled = False
        self._sync_overlay_controls()
        self._update_api_visibility()
        if self.page:
            self._api_keys_column.update()
        if self.on_overlay_toggle is not None:
            self.on_overlay_toggle(enabled)
            return
        self._emit_settings_changed()

    def _on_peer_translation_click(self, e) -> None:
        if not self.page or not self._settings or self._peer_translation_button.disabled:
            return
        options = [
            OptionItem(value="on", label=t("settings.option.on")),
            OptionItem(value="off", label=t("settings.option.off")),
        ]
        modal = SettingsModal(
            self.page,
            t("settings.peer_translation"),
            options,
            self._on_peer_translation_selected,
            show_description=False,
        )
        modal.open("on" if self._settings.ui.peer_translation_enabled else "off")

    def _on_peer_translation_selected(self, value: str) -> None:
        if not self._settings:
            return
        enabled = value == "on"
        self._settings.ui.peer_translation_enabled = enabled
        if enabled and not self._settings.ui.integrated_context_bootstrapped:
            self._settings.ui.integrated_context_enabled = True
            self._settings.ui.integrated_context_bootstrapped = True
        self._sync_overlay_controls()
        self._update_api_visibility()
        if self.page:
            self._api_keys_column.update()
        self._emit_settings_changed()

    def _on_overlay_translation_click(self, e) -> None:
        if not self.page or not self._settings or self._overlay_translation_button.disabled:
            return
        options = [
            OptionItem(value="on", label=t("settings.option.on")),
            OptionItem(value="off", label=t("settings.option.off")),
        ]
        modal = SettingsModal(
            self.page,
            t("settings.overlay.show_translation"),
            options,
            self._on_overlay_translation_selected,
            show_description=False,
        )
        modal.open("on" if self._settings.ui.show_overlay_translation else "off")

    def _on_overlay_translation_selected(self, value: str) -> None:
        if not self._settings:
            return
        self._settings.ui.show_overlay_translation = value == "on"
        self._sync_overlay_controls()
        self._emit_settings_changed()

    def _on_overlay_peer_original_click(self, e) -> None:
        if not self.page or not self._settings or self._overlay_peer_original_button.disabled:
            return
        options = [
            OptionItem(value="on", label=t("settings.option.on")),
            OptionItem(value="off", label=t("settings.option.off")),
        ]
        modal = SettingsModal(
            self.page,
            t("settings.overlay.show_peer_original"),
            options,
            self._on_overlay_peer_original_selected,
            show_description=False,
        )
        modal.open("on" if self._settings.ui.show_overlay_peer_original else "off")

    def _on_overlay_peer_original_selected(self, value: str) -> None:
        if not self._settings:
            return
        self._settings.ui.show_overlay_peer_original = value == "on"
        self._sync_overlay_controls()
        self._emit_settings_changed()

    def _on_integrated_context_click(self, e) -> None:
        if not self.page or not self._settings or self._integrated_context_button.disabled:
            return
        options = [
            OptionItem(value="on", label=t("settings.context.integrated")),
            OptionItem(value="off", label=t("settings.context.local")),
        ]
        modal = SettingsModal(
            self.page,
            t("settings.integrated_context"),
            options,
            self._on_integrated_context_selected,
            show_description=False,
        )
        modal.open("on" if self._settings.ui.integrated_context_enabled else "off")

    def _on_integrated_context_selected(self, value: str) -> None:
        if not self._settings:
            return
        self._settings.ui.integrated_context_enabled = value == "on"
        self._sync_overlay_controls()
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

    def _on_vrc_mic_click(self, e) -> None:
        """打开 VRC 闭麦同步选项框

        Open VRC mic intercept selection modal.
        """
        if not self.page:
            return
        options = [
            OptionItem(
                value="on",
                label=t("settings.vrc_mic.on"),
                description=t("settings.vrc_mic.on.description", default=""),
            ),
            OptionItem(value="off", label=t("settings.vrc_mic.off")),
        ]
        current = "on" if self._settings.osc.vrc_mic_intercept else "off"
        modal = SettingsModal(
            self.page,
            t("settings.vrc_mic_intercept"),
            options,
            self._on_vrc_mic_selected,
            show_description=True,
        )
        modal.open(current)

    def _on_vrc_mic_selected(self, value: str) -> None:
        """处理选项卡的选择结果

        Handle VRC mic intercept selection result.
        """
        if not self._settings:
            return
        new_value = value == "on"
        logger.info(f"[Settings] VRC mic intercept toggled: {new_value}")
        self._settings.osc.vrc_mic_intercept = new_value

        self._vrc_mic_text.content.value = t(
            "settings.vrc_mic.on" if new_value else "settings.vrc_mic.off"
        )
        if self.page:
            self._vrc_mic_text.update()
        self._emit_settings_changed()

    def _on_chatbox_source_click(self, e) -> None:
        """Open chatbox source inclusion selection modal."""
        if not self.page:
            return
        options = [
            OptionItem(value="on", label=t("settings.chatbox_source.on")),
            OptionItem(value="off", label=t("settings.chatbox_source.off")),
        ]
        current = "on" if self._settings.osc.chatbox_include_source else "off"
        modal = SettingsModal(
            self.page,
            t("settings.chatbox_include_source"),
            options,
            self._on_chatbox_source_selected,
            show_description=False,
        )
        modal.open(current)

    def _on_chatbox_source_selected(self, value: str) -> None:
        """Handle chatbox source inclusion selection result."""
        if not self._settings:
            return
        new_value = value == "on"
        logger.info(f"[Settings] Chatbox include source toggled: {new_value}")
        self._settings.osc.chatbox_include_source = new_value

        self._chatbox_source_text.content.value = t(
            "settings.chatbox_source.on" if new_value else "settings.chatbox_source.off"
        )
        if self.page:
            self._chatbox_source_text.update()
        self._emit_settings_changed()

    def _peer_lang_display(self, code: str) -> str:
        """Return display text for peer language, showing 'follow' if empty."""
        if not code:
            return t("settings.peer_language.follow")
        return language_name(code)

    def _on_peer_source_click(self, e) -> None:
        if not self.page or not self._settings:
            return
        modal = LanguageModal(
            page=self.page,
            languages=get_all_language_options(),
            on_select=self._on_peer_source_selected,
        )
        current = (
            self._settings.languages.peer_source_language
            or self._settings.languages.source_language
        )
        modal.open(current=current, recent=self._settings.languages.recent_source_languages)

    def _on_peer_source_selected(self, lang_code: str) -> None:
        if not self._settings:
            return
        if lang_code == self._settings.languages.source_language:
            self._settings.languages.peer_source_language = ""
        else:
            self._settings.languages.peer_source_language = lang_code
        self._peer_source_text.content.value = self._peer_lang_display(
            self._settings.languages.peer_source_language
        )
        if self.page:
            self._peer_source_text.update()
        self._emit_settings_changed()

    def _on_peer_target_click(self, e) -> None:
        if not self.page or not self._settings:
            return
        modal = LanguageModal(
            page=self.page,
            languages=get_all_language_options(),
            on_select=self._on_peer_target_selected,
        )
        current = (
            self._settings.languages.peer_target_language
            or self._settings.languages.target_language
        )
        modal.open(current=current, recent=self._settings.languages.recent_target_languages)

    def _on_peer_target_selected(self, lang_code: str) -> None:
        if not self._settings:
            return
        if lang_code == self._settings.languages.target_language:
            self._settings.languages.peer_target_language = ""
        else:
            self._settings.languages.peer_target_language = lang_code
        self._peer_target_text.content.value = self._peer_lang_display(
            self._settings.languages.peer_target_language
        )
        if self.page:
            self._peer_target_text.update()
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
        self._settings.system_prompts[self._active_prompt_key()] = value
        self._emit_settings_changed()

    def _on_reset_prompt(self, e) -> None:
        """Reset prompt to default for current provider."""
        self._prompt_editor.load_default_prompt()
        if self._settings:
            self._settings.system_prompt = self._prompt_editor.value
            self._settings.system_prompts[self._active_prompt_key()] = self._prompt_editor.value
            self._emit_settings_changed()

    def _apply_custom_vocabulary(self) -> None:
        if not self._settings:
            return

        source_language = self._current_source_language()
        updated_terms = dict(self._settings.stt.custom_terms)
        current_terms = list(updated_terms.get(source_language, []))
        parsed_terms, unique_count = self._parse_custom_vocabulary_terms()
        normalized_text = "\n".join(parsed_terms)
        if self._custom_vocab_terms.value != normalized_text:
            self._custom_vocab_terms.value = normalized_text
            if self._custom_vocab_terms.page:
                self._custom_vocab_terms.update()
        updated_terms[source_language] = parsed_terms
        next_enabled = any(bool(terms) for terms in updated_terms.values())
        self._custom_vocab_draft_terms[source_language] = normalized_text

        if unique_count > MAX_CUSTOM_VOCAB_TERMS:
            logger.info(
                "[Settings] Custom vocabulary capped: language=%s, requested=%d, applied=%d",
                source_language,
                unique_count,
                MAX_CUSTOM_VOCAB_TERMS,
            )
            if self.show_snackbar:
                self.show_snackbar(
                    t(
                        "snackbar.custom_vocabulary_limit",
                        max_terms=MAX_CUSTOM_VOCAB_TERMS,
                    ),
                    ft.Colors.ORANGE_700,
                )

        if (
            current_terms == parsed_terms
            and self._settings.stt.custom_vocabulary_enabled == next_enabled
        ):
            return

        self._settings.stt.custom_terms = updated_terms
        self._settings.stt.custom_vocabulary_enabled = next_enabled
        logger.info(
            "[Settings] Custom vocabulary applied: language=%s, terms=%d",
            source_language,
            len(parsed_terms),
        )
        self._emit_settings_changed()

    def _on_apply_custom_vocabulary(self, e) -> None:
        _ = e
        self._apply_custom_vocabulary()

    def _on_custom_vocabulary_terms_change(self, e) -> None:
        _ = e
        self._custom_vocab_draft_terms[self._current_source_language()] = (
            self._custom_vocab_terms.value or ""
        )

    def _on_custom_vocabulary_terms_blur(self, e) -> None:
        _ = e
        self._apply_custom_vocabulary()

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
        self._custom_vocab_title.value = t("settings.section.custom_vocabulary")
        self._custom_vocab_info_icon.tooltip = t("settings.custom_vocabulary_tooltip")
        self._vrc_mic_title.value = t("settings.vrc_mic_intercept")
        self._chatbox_source_title.value = t("settings.chatbox_include_source")
        self._peer_lang_title.value = t("settings.peer_language")
        self._peer_stt_label.value = t("settings.peer_stt_provider")
        self._peer_source_label.value = t("settings.peer_language.source")
        self._peer_target_label.value = t("settings.peer_language.target")
        self._peer_deepgram_model_label.value = t("settings.peer_deepgram_model")
        self._peer_qwen_region_label.value = t("settings.peer_qwen_region")
        self._peer_qwen_model_label.value = t("settings.peer_qwen_model")
        self._peer_soniox_model_label.value = t("settings.peer_soniox_model")
        self._overlay_title.value = t("settings.section.overlay")
        self._overlay_enabled_label.value = t("settings.overlay.enabled")
        self._overlay_translation_label.value = t("settings.overlay.show_translation")
        self._overlay_peer_original_label.value = t("settings.overlay.show_peer_original")
        self._peer_translation_label.value = t("settings.peer_translation")
        self._integrated_context_label.value = t("settings.integrated_context")
        self._overlay_calibration_title.value = t("settings.overlay.calibration")
        self._overlay_anchor_label.value = t("settings.overlay.calibration.anchor")
        self._overlay_offset_x_label.value = t("settings.overlay.calibration.offset_x")
        self._overlay_offset_y_label.value = t("settings.overlay.calibration.offset_y")
        self._overlay_distance_label.value = t("settings.overlay.calibration.distance")
        self._overlay_text_scale_label.value = t("settings.overlay.calibration.text_scale")
        self._reset_prompt_btn.text = t("settings.reset_prompt")
        self._custom_vocab_terms.label = None
        self._custom_vocab_terms.helper_text = ""

        # Update dynamic buttons by replacing the entire style object
        ui_font = font_for_language(get_locale())

        if self._reset_prompt_btn:
            self._reset_prompt_btn.style = self._get_button_style(ui_font)

        if self._qwen_region_btn:
            self._qwen_region_btn.style = self._get_button_style(ui_font)
        if self._overlay_enabled_button:
            self._overlay_enabled_button.style = self._get_button_style(ui_font)
        if self._overlay_translation_button:
            self._overlay_translation_button.style = self._get_button_style(ui_font)
        if self._overlay_peer_original_button:
            self._overlay_peer_original_button.style = self._get_button_style(ui_font)
        if self._peer_translation_button:
            self._peer_translation_button.style = self._get_button_style(ui_font)
        if self._integrated_context_button:
            self._integrated_context_button.style = self._get_button_style(ui_font)
        if self._overlay_calibration_apply_button:
            self._overlay_calibration_apply_button.style = self._get_button_style(ui_font)
        if self._overlay_calibration_cancel_button:
            self._overlay_calibration_cancel_button.style = self._get_button_style(ui_font)
        if self._overlay_calibration_reset_button:
            self._overlay_calibration_reset_button.style = self._get_button_style(ui_font)

        self._overlay_calibration_apply_button.text = t("settings.overlay.calibration.apply")
        self._overlay_calibration_cancel_button.text = t("settings.overlay.calibration.cancel")
        self._overlay_calibration_reset_button.text = t("settings.overlay.calibration.reset")
        self._overlay_anchor_dropdown.options = [
            ft.dropdown.Option(
                key=anchor,
                text=t(f"settings.overlay.calibration.anchor.{anchor}"),
            )
            for anchor in OVERLAY_CALIBRATION_ANCHORS
        ]

        # Update text controls with current selection labels

        # Update text controls with current selection labels
        if self._settings:
            self._stt_text.content.value = provider_label(self._settings.provider.stt.value)
            self._peer_stt_text.content.value = provider_label(self._settings.provider.peer_stt.value)
            self._llm_text.content.value = self._get_llm_display_label(self._settings)
            self._ui_text.content.value = locale_label(self._settings.ui.locale)
            self._low_latency_text.content.value = t(
                "toggle.on" if self._settings.stt.low_latency_mode else "toggle.off"
            )
            self._vrc_mic_text.content.value = t(
                "settings.vrc_mic.on"
                if self._settings.osc.vrc_mic_intercept
                else "settings.vrc_mic.off"
            )
            self._chatbox_source_text.content.value = t(
                "settings.chatbox_source.on"
                if self._settings.osc.chatbox_include_source
                else "settings.chatbox_source.off"
            )
            self._peer_source_text.content.value = self._peer_lang_display(
                self._settings.languages.peer_source_language
            )
            self._peer_target_text.content.value = self._peer_lang_display(
                self._settings.languages.peer_target_language
            )
            self._peer_deepgram_model_text.content.value = self._peer_deepgram_model_label_for(
                self._settings
            )
            self._peer_qwen_region_text.content.value = self._peer_qwen_region_label_for(
                self._settings
            )
            self._peer_qwen_model_text.content.value = self._peer_qwen_model_label_for(
                self._settings
            )
            self._peer_soniox_model_text.content.value = self._peer_soniox_model_label_for(
                self._settings
            )
            self._sync_overlay_controls()
            self._sync_overlay_calibration_controls()
            self._update_peer_provider_visibility()

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
