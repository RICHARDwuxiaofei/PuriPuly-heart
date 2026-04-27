"""Settings view - Bento grid layout with SegmentedButton providers."""

from __future__ import annotations

import contextlib
import copy
import logging
from pathlib import Path
from typing import Callable

import flet as ft

from puripuly_heart.app.wiring import create_secret_store
from puripuly_heart.config.llm_profiles import (
    OPENROUTER_FALLBACK_SELECTION_ALIASES,
    OPENROUTER_MAIN_SELECTION_ALIASES,
    fallback_profile_for_alias,
    profile_for_alias,
)
from puripuly_heart.config.prompts import load_prompt_for_provider
from puripuly_heart.config.settings import (
    MAX_CUSTOM_VOCAB_TERMS,
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
from puripuly_heart.core.language import get_stt_compatibility_warning
from puripuly_heart.ui.components.managed_trial_usage_bar import ManagedTrialUsageBar
from puripuly_heart.ui.components.settings import (
    ApiKeyField,
    AudioSettings,
    OptionItem,
    PromptEditor,
    SettingsModal,
    SettingsUnitCard,
)
from puripuly_heart.ui.components.shared_card_wrapper import SharedCardWrapper
from puripuly_heart.ui.components.subtab_shell import TextSubtab, TextSubtabShell
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
from puripuly_heart.ui.overlay_peer_contract import OverlayPeerConsumerContract
from puripuly_heart.ui.theme import (
    COLOR_DIVIDER,
    COLOR_NEUTRAL,
    COLOR_NEUTRAL_DARK,
    COLOR_ON_BACKGROUND,
    COLOR_PRIMARY,
)

logger = logging.getLogger(__name__)

_CJK_START = 0x3000
_CENTER_ALIGNMENT = ft.alignment.Alignment(0, 0)
_CENTER_RIGHT_ALIGNMENT = ft.alignment.Alignment(1, 0)
_OPENROUTER_MANAGED_OPTION_VALUE = OpenRouterSelectionAlias.GEMMA4_MANAGED.value
_SETTINGS_SUBTAB_ORDER = ("api", "general", "prompt", "overlay")
_OVERLAY_DISTANCE_MIN = 0.5
_OVERLAY_DISTANCE_MAX = 2.0
_OVERLAY_DISTANCE_DIVISIONS = 30
_OVERLAY_OFFSET_STEP = 0.05
_OVERLAY_TEXT_SCALE_PRESETS = (
    ("large", 1.2),
    ("normal", 1.0),
    ("small", 0.8),
)


def _make_text_button(label: str, **kwargs) -> ft.TextButton:
    return ft.TextButton(text=label, **kwargs)


def _set_text_button_label(button: ft.TextButton, label: str) -> None:
    button.text = label


def _update_control_if_mounted(control: ft.Control) -> None:
    """Update a Flet control only while it is attached to a page."""
    if control.page is None:
        return
    try:
        control.update()
    except AssertionError as exc:
        if "Control must be added" not in str(exc):
            raise


def _make_overlay_anchor_dropdown(value: str, on_change) -> ft.Dropdown:
    return ft.Dropdown(
        value=value,
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
        on_change=on_change,
    )


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


def _weighted_len(text: str) -> int:
    return sum(2 if ord(char) >= _CJK_START else 1 for char in text)


def _setting_action_text_size(text: str) -> int:
    length = _weighted_len(text or "")
    if length <= 6:
        return 22
    if length <= 10:
        return 20
    if length <= 18:
        return 18
    return 16


def _derive_openrouter_selection_alias(
    llm_model: OpenRouterLLMModel,
    selected_source: OpenRouterCredentialSource,
) -> OpenRouterSelectionAlias:
    if llm_model == OpenRouterLLMModel.QWEN_35_FLASH_02_23:
        if selected_source == OpenRouterCredentialSource.MANAGED:
            return OpenRouterSelectionAlias.QWEN35_FLASH_MANAGED
        return OpenRouterSelectionAlias.QWEN35_FLASH_BYOK
    if selected_source == OpenRouterCredentialSource.MANAGED:
        return OpenRouterSelectionAlias.GEMMA4_MANAGED
    return OpenRouterSelectionAlias.GEMMA4_BYOK


class SettingsView(ft.Column):
    """Settings view with Bento grid layout."""

    def __init__(self):
        super().__init__(expand=True, spacing=16)

        # Callbacks (assigned by App)
        self.on_settings_changed: Callable[[AppSettings], None] | None = None
        self.on_prompt_apply_settings: Callable[[AppSettings], None] | None = None
        self.on_providers_changed: Callable[[], None] | None = None
        self.on_request_openrouter_pkce: Callable[[AppSettings], None] | None = None
        self.on_verify_api_key: Callable[[str, str], object] | None = None
        self.on_secret_cleared: Callable[[str], None] | None = None  # key name
        self.on_overlay_calibration_begin: Callable[[], OverlayCalibration] | None = None
        self.on_overlay_calibration_change: Callable[[str, object], OverlayCalibration] | None = (
            None
        )
        self.on_overlay_calibration_apply: Callable[[], OverlayCalibration] | None = None
        self.on_overlay_calibration_cancel: Callable[[], OverlayCalibration] | None = None
        self.show_snackbar: Callable[[str, str], None] | None = None
        self.runtime_log_basic: Callable[..., None] | None = None
        self.runtime_log_detailed: Callable[..., None] | None = None

        # State
        self._settings: AppSettings | None = None
        self._provider_settings_draft: AppSettings | None = None
        self._config_path: Path | None = None
        self.has_provider_changes: bool = False
        self.has_pending_prompt_changes: bool = False
        self._custom_vocab_draft_terms: dict[str, str] = {}
        self._overlay_state: str = "off"
        self._overlay_calibration = OverlayCalibration()
        self._overlay_calibration_draft = self._overlay_calibration.copy()
        self._overlay_calibration_session_active = False
        self._managed_trial_usage_visible = False
        self._managed_trial_usage_remaining_percent: int | None = None
        self._overlay_peer_contract: OverlayPeerConsumerContract | None = None

        # Build UI components
        self._build_ui()

    # --- Card Wrapper (About page pattern) ---
    def _wrap_card(
        self,
        content: ft.Control,
        *,
        expand: bool | None = None,
        height: float | int | None = SharedCardWrapper.DEFAULT_HEIGHT,
    ) -> SharedCardWrapper:
        """Wrap content in the shared card shell used across settings/about."""
        return SharedCardWrapper(
            content,
            expand=expand,
            height=height,
        )

    def _wrap_unit_card(
        self,
        *,
        title: ft.Control,
        value: ft.Control,
        extra_controls: tuple[ft.Control, ...] = (),
        height: float | int | None = SettingsUnitCard.DEFAULT_HEIGHT,
    ) -> SettingsUnitCard:
        return SettingsUnitCard(
            title=title,
            value=value,
            extra_controls=extra_controls,
            height=height,
        )

    def _wrap_empty_unit_card(
        self,
        *,
        height: float | int | None = SettingsUnitCard.DEFAULT_HEIGHT,
    ) -> SharedCardWrapper:
        return self._wrap_card(ft.Container(expand=True), expand=True, height=height)

    # --- Clickable Text Builders ---
    def _build_clickable_text(
        self,
        text: str,
        on_click,
        *,
        size: int = 28,
        text_align: ft.TextAlign = ft.TextAlign.CENTER,
        alignment=_CENTER_ALIGNMENT,
        no_wrap: bool = False,
        max_lines: int | None = None,
        overflow: ft.TextOverflow | None = None,
        width: float | int | None = None,
        height: float | int | None = None,
        expand: bool | int | None = True,
    ) -> ft.Container:
        """Build a clickable centered text with hover effect."""
        text_control = ft.Text(
            text,
            size=size,
            font_family=font_for_language(get_locale()),
            color=COLOR_ON_BACKGROUND,
            text_align=text_align,
            no_wrap=no_wrap,
            max_lines=max_lines,
            overflow=overflow,
        )
        return ft.Container(
            content=text_control,
            alignment=alignment,
            width=width,
            height=height,
            expand=expand,
            on_click=on_click,
            on_hover=self._on_text_hover,
        )

    def _build_setting_action_text(self, text: str, on_click) -> ft.Container:
        return self._build_clickable_text(
            text,
            on_click,
            size=_setting_action_text_size(text),
            text_align=ft.TextAlign.RIGHT,
            alignment=_CENTER_RIGHT_ALIGNMENT,
            no_wrap=True,
            max_lines=1,
            overflow=ft.TextOverflow.ELLIPSIS,
        )

    def _set_setting_action_text(self, control: ft.Container, text: str) -> None:
        text_control = control.content
        text_control.value = text
        text_control.size = _setting_action_text_size(text)

    def _set_unit_card_value_text(
        self, control: ft.Container, text: str, *, size: int = 28
    ) -> None:
        text_control = control.content
        text_control.value = text
        text_control.size = size

    def _iter_locale_sensitive_clickable_text_controls(self) -> tuple[ft.Container, ...]:
        return (
            self._integrated_context_button,
            self._stt_text,
            self._peer_stt_text,
            self._llm_text,
            self._ui_text,
            self._chatbox_source_text,
            self._vrc_mic_text,
            self._mic_audio_text,
            self._audio_host_api_text,
            self._loopback_audio_text,
            self._low_latency_text,
            self._overlay_translation_button,
            self._overlay_peer_original_button,
            self._overlay_anchor_button,
            self._overlay_text_scale_text,
            self._overlay_reset_button,
            self._openrouter_routing_text,
            self._openrouter_fallback_text,
        )

    def _sync_clickable_text_control_fonts(self, font_family: str | None) -> None:
        for control in self._iter_locale_sensitive_clickable_text_controls():
            if control:
                control.content.font_family = font_family

    def _sync_general_audio_card_texts(self) -> None:
        default_label = t("settings.default_option")
        self._set_unit_card_value_text(
            self._mic_audio_text,
            self._audio_settings.microphone or default_label,
        )
        self._set_unit_card_value_text(
            self._audio_host_api_text,
            self._audio_settings.host_api_display_label,
        )
        self._set_unit_card_value_text(
            self._loopback_audio_text,
            self._audio_settings.desktop_output_device or default_label,
        )

    def _on_text_hover(self, e: ft.ControlEvent) -> None:
        """Handle hover effect on clickable text."""
        container = e.control
        text_control = container.content
        next_color = COLOR_PRIMARY if e.data == "true" else COLOR_ON_BACKGROUND
        if text_control.color == next_color:
            return
        text_control.color = next_color
        container.update()

    def _make_overlay_step_hover_handler(self, text_control: ft.Text):
        def _on_hover(e: ft.ControlEvent) -> None:
            next_color = COLOR_PRIMARY if e.data == "true" else COLOR_ON_BACKGROUND
            if text_control.color == next_color:
                return
            text_control.color = next_color
            if text_control.page is not None:
                text_control.update()

        return _on_hover

    def _build_overlay_step_hit_lane(self, on_click, *, on_hover=None) -> ft.Container:
        return ft.Container(
            content=ft.Container(expand=True),
            expand=1,
            on_click=on_click,
            on_hover=on_hover,
        )

    def _build_overlay_step_visual_lane(
        self, text: str, *, alignment
    ) -> tuple[ft.Container, ft.Text]:
        text_control = ft.Text(
            text,
            size=22,
            font_family=font_for_language(get_locale()),
            color=COLOR_ON_BACKGROUND,
            text_align=ft.TextAlign.CENTER,
        )
        return (
            ft.Container(
                content=text_control,
                expand=1,
                alignment=alignment,
            ),
            text_control,
        )

    def _build_overlay_step_split_layout(
        self,
        *,
        title: ft.Text,
        value_text: ft.Text,
        decrease_text: str,
        increase_text: str,
        on_decrease,
        on_increase,
    ) -> tuple[ft.Stack, ft.Container, ft.Container, ft.Text, ft.Text]:
        decrease_visual, decrease_glyph = self._build_overlay_step_visual_lane(
            decrease_text,
            alignment=ft.alignment.center_right,
        )
        increase_visual, increase_glyph = self._build_overlay_step_visual_lane(
            increase_text,
            alignment=ft.alignment.center_left,
        )
        decrease_lane = self._build_overlay_step_hit_lane(
            on_decrease,
            on_hover=self._make_overlay_step_hover_handler(decrease_glyph),
        )
        increase_lane = self._build_overlay_step_hit_lane(
            on_increase,
            on_hover=self._make_overlay_step_hover_handler(increase_glyph),
        )
        visual_row = ft.Row(
            controls=[
                decrease_visual,
                ft.Container(
                    content=value_text,
                    width=84,
                    alignment=ft.alignment.center,
                ),
                increase_visual,
            ],
            spacing=4,
            expand=1,
            alignment=ft.MainAxisAlignment.CENTER,
            vertical_alignment=ft.CrossAxisAlignment.CENTER,
        )
        visual_column = ft.Column(
            controls=[
                title,
                ft.Container(
                    content=visual_row,
                    expand=True,
                    alignment=ft.alignment.center,
                ),
            ],
            spacing=0,
            expand=True,
        )
        stack = ft.Stack(
            controls=[
                ft.Row(
                    controls=[decrease_lane, increase_lane],
                    spacing=0,
                    expand=1,
                    vertical_alignment=ft.CrossAxisAlignment.STRETCH,
                ),
                ft.TransparentPointer(content=visual_column),
            ],
            fit=ft.StackFit.EXPAND,
            expand=True,
            alignment=ft.alignment.center,
        )
        return stack, decrease_lane, increase_lane, decrease_glyph, increase_glyph

    def _get_button_style(
        self,
        font_family: str,
        *,
        size: int = 20,
        default_color: str = COLOR_NEUTRAL,
        disabled_color: str | None = None,
    ) -> ft.ButtonStyle:
        """Create a complete ButtonStyle with the specified font."""
        color = {
            ft.ControlState.HOVERED: COLOR_PRIMARY,
            ft.ControlState.DEFAULT: default_color,
        }
        if disabled_color is not None:
            color[ft.ControlState.DISABLED] = disabled_color
        return ft.ButtonStyle(
            color=color,
            icon_color=color,
            text_style=ft.TextStyle(
                size=size,
                font_family=font_family,
            ),
            overlay_color=ft.Colors.TRANSPARENT,
            animation_duration=0,
        )

    def _settings_subtab_label(self, key: str) -> str:
        return t(f"settings.subtab.{key}")

    def _build_settings_subtab_shell(
        self, tab_rows: dict[str, list[ft.Control]]
    ) -> TextSubtabShell:
        return TextSubtabShell(
            tabs=[
                TextSubtab(key, self._settings_subtab_label(key), tuple(tab_rows[key]))
                for key in _SETTINGS_SUBTAB_ORDER
            ],
            font_family=font_for_language(get_locale()),
            initial_key=_SETTINGS_SUBTAB_ORDER[0],
            subtab_bar_position="bottom",
        )

    def _build_setting_action_row(self, label: ft.Text, action: ft.Control) -> ft.Row:
        return ft.Row(
            controls=[label, ft.Container(expand=True), action],
            alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
            vertical_alignment=ft.CrossAxisAlignment.CENTER,
        )

    def _emit_runtime_basic(self, message: str, *, level: int = logging.INFO) -> None:
        runtime_log_basic = getattr(self, "runtime_log_basic", None)
        if runtime_log_basic is not None:
            runtime_log_basic(message, level=level)
            return
        logger.log(level, message)

    def _emit_runtime_detailed(self, message: str, *, level: int = logging.INFO) -> None:
        runtime_log_detailed = getattr(self, "runtime_log_detailed", None)
        if runtime_log_detailed is not None:
            runtime_log_detailed(message, level=level)
            return
        logger.log(level, message)

    def _build_action_button(
        self,
        text: str,
        on_click,
        *,
        size: int = 20,
        default_color: str = COLOR_NEUTRAL,
        disabled_color: str | None = None,
        width: float | int | None = None,
        height: float | int | None = None,
    ) -> ft.TextButton:
        return _make_text_button(
            text,
            style=self._get_button_style(
                font_for_language(get_locale()),
                size=size,
                default_color=default_color,
                disabled_color=disabled_color,
            ),
            on_click=on_click,
            width=width,
            height=height,
        )

    def _build_integrated_context_unit_card(self) -> SettingsUnitCard:
        self._integrated_context_label = ft.Text(
            t("settings.integrated_context"),
            size=24,
            weight=ft.FontWeight.BOLD,
            color=COLOR_NEUTRAL,
        )
        self._integrated_context_button = self._build_clickable_text(
            t("settings.context.local"),
            self._on_integrated_context_click,
        )
        self._integrated_context_hint = ft.Text("", size=13, color=COLOR_NEUTRAL)

        self._integrated_context_card = self._wrap_unit_card(
            title=self._integrated_context_label,
            value=self._integrated_context_button,
        )
        return self._integrated_context_card

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

    def _build_numeric_setting_field(
        self,
        *,
        label: str,
        value: str,
        on_change_end,
    ) -> ft.TextField:
        return ft.TextField(
            label=label,
            value=value,
            dense=True,
            expand=True,
            text_align=ft.TextAlign.CENTER,
            border_radius=10,
            border_color=COLOR_DIVIDER,
            focused_border_color=COLOR_PRIMARY,
            on_blur=on_change_end,
            on_submit=on_change_end,
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

    def _overlay_anchor_label_for(self, anchor: str) -> str:
        return t(f"settings.overlay.calibration.anchor.{anchor}")

    def _overlay_text_scale_label_for(self, value: float) -> str:
        return t(
            f"settings.overlay.calibration.text_scale.{self._overlay_text_scale_preset_key_for(value)}"
        )

    def _overlay_text_scale_preset_key_for(self, value: float) -> str:
        return min(
            _OVERLAY_TEXT_SCALE_PRESETS,
            key=lambda preset: abs(preset[1] - value),
        )[0]

    def _overlay_text_scale_value_for(self, preset_key: str) -> float:
        for key, scale in _OVERLAY_TEXT_SCALE_PRESETS:
            if key == preset_key:
                return scale
        try:
            return float(preset_key)
        except (TypeError, ValueError):
            return 1.0

    def _parse_setting_float(
        self,
        raw_value: str,
        *,
        fallback: float,
        minimum: float,
        maximum: float | None = None,
    ) -> float:
        try:
            parsed = float(raw_value)
        except (TypeError, ValueError):
            parsed = fallback
        if parsed < minimum:
            parsed = minimum
        if maximum is not None and parsed > maximum:
            parsed = maximum
        return parsed

    def _parse_setting_int(
        self,
        raw_value: str,
        *,
        fallback: int,
        minimum: int,
    ) -> int:
        try:
            parsed = int(raw_value)
        except (TypeError, ValueError):
            parsed = fallback
        return max(minimum, parsed)

    def _build_ui(self) -> None:
        """Build the settings UI with Bento grid layout."""
        # === API provider surfaces: Self STT + Peer STT + Shared Translation ===
        self._stt_text = self._build_clickable_text(
            provider_label(STTProviderName.LOCAL_QWEN.value),
            self._on_stt_click,
        )
        self._stt_title = ft.Text(
            t("settings.section.stt"), size=24, weight=ft.FontWeight.BOLD, color=COLOR_NEUTRAL
        )
        self._stt_provider_label = ft.Text(
            t("settings.self_stt_provider"), size=16, color=COLOR_ON_BACKGROUND
        )
        stt_card = self._wrap_unit_card(
            title=self._stt_title,
            value=self._stt_text,
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
        self._translation_provider_label = ft.Text(
            t("settings.shared_translation_provider"), size=16, color=COLOR_ON_BACKGROUND
        )
        trans_card = self._wrap_unit_card(
            title=self._trans_title,
            value=self._llm_text,
        )

        # === Row 2: API Keys (2x1) ===
        # Qwen region selection button (in header)
        self._qwen_region_btn = _make_text_button(
            f"{t('settings.qwen_region')} {t('region.beijing')}",
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
        self._openrouter_key = ApiKeyField(
            "settings.openrouter_api_key",
            "openrouter_api_key",
            "openrouter",
            on_verify=self._verify_key,
            on_save=self._on_secret_change,
            show_snackbar=lambda msg, bg: (
                self.show_snackbar(msg, bg) if self.show_snackbar else None
            ),
        )
        self._openrouter_pkce_button = self._build_action_button(
            t("settings.openrouter_authenticate"),
            self._on_openrouter_pkce_click,
            size=20,
            default_color=COLOR_NEUTRAL_DARK,
            disabled_color=COLOR_NEUTRAL_DARK,
        )
        self._openrouter_pkce_button.disabled = False
        self._openrouter_pkce_button_row = ft.Row(
            controls=[self._openrouter_pkce_button],
            alignment=ft.MainAxisAlignment.END,
            vertical_alignment=ft.CrossAxisAlignment.CENTER,
        )
        self._managed_trial_usage_bar = ManagedTrialUsageBar()
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
                self._managed_trial_usage_bar,
                self._openrouter_key,
                self._openrouter_pkce_button_row,
                self._alibaba_key_beijing,
                self._alibaba_key_singapore,
            ],
            spacing=12,
        )

        self._api_title = ft.Text(
            t("settings.section.api_keys"), size=24, weight=ft.FontWeight.BOLD, color=COLOR_NEUTRAL
        )
        self._api_credentials_helper_text = ft.Text(
            t("settings.api_credentials_helper"),
            size=16,
            color=COLOR_NEUTRAL,
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
            ft.Column(
                [
                    api_header,
                    ft.Container(height=16),
                    self._api_keys_column,
                ],
                spacing=0,
            ),
            height=None,
        )
        api_keys_row = api_card

        # === General Tab Row 1: UI / Include Original / Integrated Context ===
        self._ui_text = self._build_clickable_text(
            locale_label(get_locale()),
            self._on_ui_click,
        )
        self._ui_title = ft.Text(
            t("settings.section.ui"), size=24, weight=ft.FontWeight.BOLD, color=COLOR_NEUTRAL
        )
        ui_card = self._wrap_unit_card(
            title=self._ui_title,
            value=self._ui_text,
        )

        self._audio_settings = AudioSettings(on_change=self._on_audio_change)
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
        chatbox_source_card = self._wrap_unit_card(
            title=self._chatbox_source_title,
            value=self._chatbox_source_text,
        )

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
        vrc_mic_card = self._wrap_unit_card(
            title=self._vrc_mic_title,
            value=self._vrc_mic_text,
        )

        integrated_context_card = self._build_integrated_context_unit_card()

        general_primary_row = ft.Container(
            content=ft.Row(
                [ui_card, chatbox_source_card, integrated_context_card],
                spacing=16,
                expand=True,
            ),
        )

        # === General Tab Row 2: Host API / Microphone Audio / Loopback Audio ===
        self._mic_audio_text = self._build_clickable_text(
            t("settings.default_option"),
            self._on_mic_audio_click,
        )
        self._audio_host_api_title = ft.Text(
            t("settings.audio_host_api"),
            size=24,
            weight=ft.FontWeight.BOLD,
            color=COLOR_NEUTRAL,
        )
        self._audio_host_api_text = self._build_clickable_text(
            t("settings.default_option"),
            self._on_mic_host_api_click,
        )
        host_api_card = self._wrap_unit_card(
            title=self._audio_host_api_title,
            value=self._audio_host_api_text,
        )
        self._mic_audio_title = ft.Text(
            t("settings.section.microphone_audio"),
            size=24,
            weight=ft.FontWeight.BOLD,
            color=COLOR_NEUTRAL,
        )
        mic_audio_card = self._wrap_unit_card(
            title=self._mic_audio_title,
            value=self._mic_audio_text,
        )

        self._loopback_audio_text = self._build_clickable_text(
            t("settings.default_option"),
            self._on_loopback_audio_click,
        )
        self._loopback_audio_title = ft.Text(
            t("settings.section.loopback_audio"),
            size=24,
            weight=ft.FontWeight.BOLD,
            color=COLOR_NEUTRAL,
        )
        loopback_audio_card = self._wrap_unit_card(
            title=self._loopback_audio_title,
            value=self._loopback_audio_text,
        )
        general_audio_row = ft.Container(
            content=ft.Row(
                [host_api_card, mic_audio_card, loopback_audio_card],
                spacing=16,
                expand=True,
            ),
        )

        # === API Tab Row 2: Response Mode / Routing / Fallback ===
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
        self._low_latency_card = self._wrap_unit_card(
            title=self._low_latency_title,
            value=self._low_latency_text,
        )

        # === General Tab Row 3: VRChat Mute Sync / Self VAD / Peer VAD ===
        self._self_vad_title = ft.Text(
            t("settings.section.self_vad_sensitivity"),
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
        self._self_vad_card = self._wrap_unit_card(
            title=self._self_vad_title,
            value=ft.Container(content=self._vad_slider, alignment=_CENTER_ALIGNMENT, expand=True),
        )

        self._peer_vad_title = ft.Text(
            t("settings.section.peer_vad_sensitivity"),
            size=24,
            weight=ft.FontWeight.BOLD,
            color=COLOR_NEUTRAL,
        )
        self._peer_vad_slider = ft.Slider(
            min=0.0,
            max=1.0,
            divisions=20,
            value=0.6,
            label="0.60",
            active_color=COLOR_PRIMARY,
            on_change=self._handle_peer_vad_visual_change,
            on_change_end=self._handle_peer_vad_change,
        )
        self._peer_vad_field = self._build_numeric_setting_field(
            label=t("settings.vad.peer"),
            value="0.60",
            on_change_end=self._on_peer_vad_threshold_change,
        )
        self._peer_hangover_field = self._build_numeric_setting_field(
            label=t("settings.vad.peer_hangover_ms"),
            value="700",
            on_change_end=self._on_peer_hangover_change,
        )
        self._peer_pre_roll_field = self._build_numeric_setting_field(
            label=t("settings.vad.peer_pre_roll_ms"),
            value="500",
            on_change_end=self._on_peer_pre_roll_change,
        )
        self._peer_vad_card = self._wrap_unit_card(
            title=self._peer_vad_title,
            value=ft.Container(
                content=self._peer_vad_slider,
                alignment=_CENTER_ALIGNMENT,
                expand=True,
            ),
        )
        general_vad_row = ft.Container(
            content=ft.Row(
                [vrc_mic_card, self._self_vad_card, self._peer_vad_card],
                spacing=16,
                expand=True,
            ),
        )

        # === Peer STT card ===
        self._peer_provider_title = ft.Text(
            t("settings.section.peer_stt"),
            size=24,
            weight=ft.FontWeight.BOLD,
            color=COLOR_NEUTRAL,
        )
        self._dashboard_language_redirect_text = ft.Text(
            t("settings.dashboard_language_redirect"),
            size=16,
            color=COLOR_NEUTRAL,
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
        peer_stt_card = self._wrap_unit_card(
            title=self._peer_provider_title,
            value=self._peer_stt_text,
        )
        row1 = ft.Container(
            content=ft.Row([stt_card, peer_stt_card, trans_card], spacing=16, expand=True),
        )

        self._overlay_translation_title = ft.Text(
            t("settings.overlay.show_translation"),
            size=24,
            weight=ft.FontWeight.BOLD,
            color=COLOR_NEUTRAL,
        )
        self._overlay_translation_button = self._build_clickable_text(
            t("settings.option.on"),
            self._on_overlay_translation_click,
        )
        self._overlay_translation_card = self._wrap_unit_card(
            title=self._overlay_translation_title,
            value=self._overlay_translation_button,
        )

        self._overlay_peer_original_title = ft.Text(
            t("settings.overlay.show_peer_original"),
            size=24,
            weight=ft.FontWeight.BOLD,
            color=COLOR_NEUTRAL,
        )
        self._overlay_peer_original_button = self._build_clickable_text(
            t("settings.option.on"),
            self._on_overlay_peer_original_click,
        )
        self._overlay_peer_original_card = self._wrap_unit_card(
            title=self._overlay_peer_original_title,
            value=self._overlay_peer_original_button,
        )

        self._overlay_anchor_title = ft.Text(
            t("settings.overlay.calibration.anchor"),
            size=24,
            weight=ft.FontWeight.BOLD,
            color=COLOR_NEUTRAL,
        )
        self._overlay_anchor_button = self._build_clickable_text(
            self._overlay_anchor_label_for(self._overlay_calibration.anchor),
            self._on_overlay_anchor_click,
        )
        self._overlay_anchor_card = self._wrap_unit_card(
            title=self._overlay_anchor_title,
            value=self._overlay_anchor_button,
        )

        self._overlay_distance_title = ft.Text(
            t("settings.overlay.calibration.distance"),
            size=24,
            weight=ft.FontWeight.BOLD,
            color=COLOR_NEUTRAL,
        )
        self._overlay_distance_value_text = ft.Text(
            self._format_overlay_calibration_number(self._overlay_calibration.distance),
            size=28,
            color=COLOR_ON_BACKGROUND,
            text_align=ft.TextAlign.CENTER,
        )
        (
            self._overlay_distance_card_content,
            self._overlay_distance_decrease_button,
            self._overlay_distance_increase_button,
            self._overlay_distance_decrease_glyph,
            self._overlay_distance_increase_glyph,
        ) = self._build_overlay_step_split_layout(
            title=self._overlay_distance_title,
            value_text=self._overlay_distance_value_text,
            decrease_text="－",
            increase_text="＋",
            on_decrease=lambda _e: self._on_overlay_distance_step(-_OVERLAY_OFFSET_STEP),
            on_increase=lambda _e: self._on_overlay_distance_step(_OVERLAY_OFFSET_STEP),
        )
        self._overlay_distance_card = self._wrap_card(
            self._overlay_distance_card_content,
            expand=True,
            height=SettingsUnitCard.DEFAULT_HEIGHT,
        )

        self._overlay_offset_x_title = ft.Text(
            t("settings.overlay.calibration.offset_x"),
            size=24,
            weight=ft.FontWeight.BOLD,
            color=COLOR_NEUTRAL,
        )
        self._overlay_offset_x_value_text = ft.Text(
            self._format_overlay_calibration_number(self._overlay_calibration.offset_x),
            size=28,
            color=COLOR_ON_BACKGROUND,
            text_align=ft.TextAlign.CENTER,
        )
        (
            self._overlay_offset_x_card_content,
            self._overlay_offset_x_decrease_button,
            self._overlay_offset_x_increase_button,
            self._overlay_offset_x_decrease_glyph,
            self._overlay_offset_x_increase_glyph,
        ) = self._build_overlay_step_split_layout(
            title=self._overlay_offset_x_title,
            value_text=self._overlay_offset_x_value_text,
            decrease_text="◀",
            increase_text="▶",
            on_decrease=lambda _e: self._on_overlay_offset_x_step(-_OVERLAY_OFFSET_STEP),
            on_increase=lambda _e: self._on_overlay_offset_x_step(_OVERLAY_OFFSET_STEP),
        )
        self._overlay_offset_x_card = self._wrap_card(
            self._overlay_offset_x_card_content,
            expand=True,
            height=SettingsUnitCard.DEFAULT_HEIGHT,
        )

        self._overlay_offset_y_title = ft.Text(
            t("settings.overlay.calibration.offset_y"),
            size=24,
            weight=ft.FontWeight.BOLD,
            color=COLOR_NEUTRAL,
        )
        self._overlay_offset_y_value_text = ft.Text(
            self._format_overlay_calibration_number(self._overlay_calibration.offset_y),
            size=28,
            color=COLOR_ON_BACKGROUND,
            text_align=ft.TextAlign.CENTER,
        )
        (
            self._overlay_offset_y_card_content,
            self._overlay_offset_y_decrease_button,
            self._overlay_offset_y_increase_button,
            self._overlay_offset_y_decrease_glyph,
            self._overlay_offset_y_increase_glyph,
        ) = self._build_overlay_step_split_layout(
            title=self._overlay_offset_y_title,
            value_text=self._overlay_offset_y_value_text,
            decrease_text="▲",
            increase_text="▼",
            on_decrease=lambda _e: self._on_overlay_offset_y_step(-_OVERLAY_OFFSET_STEP),
            on_increase=lambda _e: self._on_overlay_offset_y_step(_OVERLAY_OFFSET_STEP),
        )
        self._overlay_offset_y_card = self._wrap_card(
            self._overlay_offset_y_card_content,
            expand=True,
            height=SettingsUnitCard.DEFAULT_HEIGHT,
        )

        self._overlay_text_scale_title = ft.Text(
            t("settings.overlay.calibration.text_scale"),
            size=24,
            weight=ft.FontWeight.BOLD,
            color=COLOR_NEUTRAL,
        )
        self._overlay_text_scale_text = self._build_clickable_text(
            self._overlay_text_scale_label_for(self._overlay_calibration.text_scale),
            self._on_overlay_text_scale_click,
        )
        self._overlay_text_scale_card = self._wrap_unit_card(
            title=self._overlay_text_scale_title,
            value=self._overlay_text_scale_text,
        )

        self._overlay_reset_title = ft.Text(
            t("settings.overlay.position_reset"),
            size=24,
            weight=ft.FontWeight.BOLD,
            color=COLOR_NEUTRAL,
        )
        self._overlay_reset_button = self._build_clickable_text(
            t("settings.overlay.calibration.reset"),
            self._on_overlay_position_reset,
        )
        self._overlay_reset_card = self._wrap_unit_card(
            title=self._overlay_reset_title,
            value=self._overlay_reset_button,
        )
        self._overlay_empty_card = self._wrap_empty_unit_card()

        overlay_row1 = ft.Container(
            content=ft.Row(
                [
                    self._overlay_translation_card,
                    self._overlay_peer_original_card,
                    self._overlay_anchor_card,
                ],
                spacing=16,
                expand=True,
            ),
        )
        overlay_row2 = ft.Container(
            content=ft.Row(
                [
                    self._overlay_distance_card,
                    self._overlay_offset_x_card,
                    self._overlay_offset_y_card,
                ],
                spacing=16,
                expand=True,
            ),
        )
        overlay_row3 = ft.Container(
            content=ft.Row(
                [
                    self._overlay_text_scale_card,
                    self._overlay_reset_card,
                    self._overlay_empty_card,
                ],
                spacing=16,
                expand=True,
            ),
        )

        # === Row 7: OpenRouter Routing (1x1 + 1x1) ===
        self._openrouter_routing_title = ft.Text(
            t("settings.openrouter_routing"),
            size=24,
            weight=ft.FontWeight.BOLD,
            color=COLOR_NEUTRAL,
        )
        self._openrouter_routing_text = self._build_clickable_text(
            t("settings.openrouter_routing.latency"),
            self._on_openrouter_routing_click,
        )
        self._openrouter_routing_card = self._wrap_unit_card(
            title=self._openrouter_routing_title,
            value=self._openrouter_routing_text,
        )
        self._openrouter_fallback_title = ft.Text(
            t("settings.openrouter_fallback"),
            size=24,
            weight=ft.FontWeight.BOLD,
            color=COLOR_NEUTRAL,
        )
        self._openrouter_fallback_text = self._build_clickable_text(
            t("provider.gemini25_flash_lite"),
            self._on_openrouter_fallback_click,
        )
        self._openrouter_fallback_helper_text = ft.Text(
            t("settings.openrouter_fallback.inactive_helper"),
            size=16,
            color=COLOR_NEUTRAL,
        )
        self._openrouter_fallback_card = self._wrap_unit_card(
            title=self._openrouter_fallback_title,
            value=self._openrouter_fallback_text,
        )
        self._openrouter_routing_row = ft.Container(
            content=ft.Row(
                [
                    self._low_latency_card,
                    self._openrouter_routing_card,
                    self._openrouter_fallback_card,
                ],
                spacing=16,
                expand=True,
            ),
            visible=True,
        )

        # === Row 8: Persona (2x2) - Licenses style ===
        self._prompt_editor = PromptEditor(
            on_change=self._on_prompt_change,
            on_commit=self._on_prompt_commit,
        )
        self._persona_title = ft.Text(
            t("settings.section.persona"), size=24, weight=ft.FontWeight.BOLD, color=COLOR_NEUTRAL
        )
        self._prompt_for_text = ft.Text(
            self._prompt_provider_copy(),
            size=16,
            color=COLOR_NEUTRAL,
        )

        # Reset button (matches Persona title color, hover -> primary)
        self._reset_prompt_btn = _make_text_button(
            t("settings.reset_prompt"),
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

        persona_card = SharedCardWrapper(
            ft.Column(
                [
                    persona_header,
                    ft.Container(height=16),
                    prompt_container,
                ],
                spacing=0,
            ),
            height=None,
            expand=False,
        )
        # === Row 9: Custom Vocabulary (2x1) ===
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
        self._custom_vocab_helper_text = ft.Text(
            self._custom_vocabulary_helper_copy(),
            size=16,
            color=COLOR_NEUTRAL,
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
        row7 = SharedCardWrapper(
            ft.Column(
                [
                    custom_vocab_header,
                    ft.Container(height=16),
                    self._custom_vocab_terms,
                ],
                spacing=0,
            ),
            height=None,
            expand=False,
        )

        self._settings_subtab_shell = self._build_settings_subtab_shell(
            {
                "api": [row1, self._openrouter_routing_row, api_keys_row],
                "general": [general_primary_row, general_audio_row, general_vad_row],
                "prompt": [row7, persona_card],
                "overlay": [overlay_row1, overlay_row2, overlay_row3],
            }
        )
        self.controls = [self._settings_subtab_shell]

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
        if settings.provider.llm == LLMProviderName.OPENROUTER:
            return self._display_openrouter_selection_alias(settings).value
        return settings.qwen.llm_model.value

    def _stored_openrouter_selection_alias(
        self, settings: AppSettings
    ) -> OpenRouterSelectionAlias | None:
        if settings.openrouter.selection_alias is None:
            if settings.openrouter.selected_source == OpenRouterCredentialSource.NONE:
                return None
            return _derive_openrouter_selection_alias(
                settings.openrouter.llm_model,
                settings.openrouter.selected_source,
            )
        try:
            profile_for_alias(settings.openrouter.selection_alias.value)
            return settings.openrouter.selection_alias
        except KeyError:
            if settings.openrouter.selected_source == OpenRouterCredentialSource.NONE:
                return None
            return _derive_openrouter_selection_alias(
                settings.openrouter.llm_model,
                settings.openrouter.selected_source,
            )

    def _display_openrouter_selection_alias(
        self, settings: AppSettings
    ) -> OpenRouterSelectionAlias:
        stored_alias = self._stored_openrouter_selection_alias(settings)
        if stored_alias is not None:
            return stored_alias
        if settings.openrouter.llm_model == OpenRouterLLMModel.QWEN_35_FLASH_02_23:
            return OpenRouterSelectionAlias.QWEN35_FLASH_MANAGED
        return OpenRouterSelectionAlias.GEMMA4_MANAGED

    def _openrouter_selection_profile(self, settings: AppSettings | None):
        if settings is None:
            return None
        try:
            return profile_for_alias(self._display_openrouter_selection_alias(settings).value)
        except KeyError:
            return None

    def _openrouter_fallback_profile(self, settings: AppSettings | None):
        if settings is None:
            return None
        try:
            return fallback_profile_for_alias(settings.openrouter.fallback_selection_alias.value)
        except KeyError:
            return None

    def _openrouter_fallback_source(
        self, settings: AppSettings | None
    ) -> OpenRouterCredentialSource:
        if settings is None:
            return OpenRouterCredentialSource.NONE
        if settings.openrouter.fallback_selection_alias == OpenRouterFallbackSelectionAlias.NONE:
            return OpenRouterCredentialSource.NONE
        return settings.openrouter.selected_source

    def _openrouter_profile_display_label(self, profile) -> str:
        return t(profile.label_key)

    def _openrouter_profile_display_description(self, profile) -> str:
        return t(profile.description_key, default="")

    def _get_llm_display_label(self, settings: AppSettings) -> str:
        if settings.provider.llm == LLMProviderName.GEMINI:
            if settings.gemini.llm_model == GeminiLLMModel.GEMINI_31_FLASH_LITE:
                return t("provider.gemini31_flash_lite")
            return t("provider.gemini3_flash")
        if settings.provider.llm == LLMProviderName.OPENROUTER:
            profile = self._openrouter_selection_profile(settings)
            if profile is not None:
                return self._openrouter_profile_display_label(profile)
            return t("provider.gemma4_26b_a4b_it")
        if settings.qwen.llm_model == QwenLLMModel.QWEN_35_PLUS:
            return t("provider.qwen35_plus")
        return t("provider.qwen35_flash")

    def _get_openrouter_routing_display_label(self, settings: AppSettings | None) -> str:
        if settings is None:
            return t("settings.openrouter_routing.latency")
        return t(f"settings.openrouter_routing.{settings.openrouter.routing_mode.value}")

    def _set_openrouter_routing_text(self, text: str) -> None:
        text_control = self._openrouter_routing_text.content
        text_control.value = text
        text_control.size = 28

    def _get_openrouter_fallback_display_label(self, settings: AppSettings | None) -> str:
        profile = self._openrouter_fallback_profile(settings)
        if profile is None or profile.openrouter_model is None:
            return t("settings.openrouter_fallback.none")
        return t(profile.label_key)

    def _get_openrouter_fallback_helper_text(self, settings: AppSettings | None) -> str:
        if settings is None:
            return t("settings.openrouter_fallback.inactive_helper")
        if settings.openrouter.fallback_selection_alias == OpenRouterFallbackSelectionAlias.NONE:
            return t("settings.openrouter_fallback.none.description")
        if settings.provider.llm != LLMProviderName.OPENROUTER:
            return t("settings.openrouter_fallback.inactive_helper")
        return t("settings.openrouter_fallback.active_helper")

    def _set_openrouter_fallback_text(self, text: str) -> None:
        text_control = self._openrouter_fallback_text.content
        text_control.value = text
        text_control.size = 28

    def _sync_openrouter_fallback_card(self, settings: AppSettings | None = None) -> None:
        if settings is None:
            settings = self._build_settings_with_provider_draft()
        self._set_openrouter_fallback_text(self._get_openrouter_fallback_display_label(settings))
        self._openrouter_fallback_helper_text.value = self._get_openrouter_fallback_helper_text(
            settings
        )

    def _active_prompt_key_for_settings(self, settings: AppSettings | None) -> str:
        if settings is None:
            return "gemini"
        if settings.provider.llm == LLMProviderName.GEMINI:
            return "gemini"
        if settings.provider.llm == LLMProviderName.OPENROUTER:
            return "openrouter"
        return "qwen"

    def _active_prompt_key(self) -> str:
        return self._active_prompt_key_for_settings(self._build_settings_with_provider_draft())

    def _ensure_provider_prompt_value(self, settings: AppSettings, provider_name: str) -> str:
        prompt = settings.system_prompts.get(provider_name, "").strip()
        if prompt:
            return prompt
        prompt = load_prompt_for_provider(provider_name)
        settings.system_prompts[provider_name] = prompt
        return prompt

    def _current_source_language(self) -> str:
        if not self._settings:
            return "en"
        return self._settings.languages.source_language

    def _prompt_provider_copy(self) -> str:
        return t(
            "settings.prompt_for",
            provider=provider_label(self._active_prompt_key()),
        )

    def _custom_vocabulary_helper_copy(self) -> str:
        return t(
            "settings.custom_vocabulary_helper",
            language=language_name(self._current_source_language()),
        )

    def _sync_prompt_tab_copy(self) -> None:
        self._prompt_for_text.value = self._prompt_provider_copy()
        self._custom_vocab_helper_text.value = self._custom_vocabulary_helper_copy()
        if self.page:
            for control in (self._prompt_for_text, self._custom_vocab_helper_text):
                with contextlib.suppress(Exception):
                    control.update()

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

    @property
    def managed_trial_usage_state(self) -> dict[str, object]:
        return {
            "visible": self._managed_trial_usage_visible,
            "remaining_percent": self._managed_trial_usage_remaining_percent,
        }

    def set_managed_trial_usage_state(
        self, *, visible: bool, remaining_percent: int | None = None
    ) -> None:
        self._managed_trial_usage_visible = bool(visible)
        if self._managed_trial_usage_visible and remaining_percent is not None:
            self._managed_trial_usage_remaining_percent = max(0, min(100, int(remaining_percent)))
        else:
            self._managed_trial_usage_remaining_percent = None
        self._sync_managed_trial_usage_bar()
        if self.page:
            with contextlib.suppress(Exception):
                self._managed_trial_usage_bar.update()

    def _copy_provider_draft_fields(self, source: AppSettings, target: AppSettings) -> None:
        target.provider.stt = source.provider.stt
        target.provider.peer_stt = source.provider.peer_stt
        target.provider.llm = source.provider.llm
        target.gemini.llm_model = source.gemini.llm_model
        target.openrouter.llm_model = source.openrouter.llm_model
        target.openrouter.routing_mode = source.openrouter.routing_mode
        target.openrouter.selected_source = source.openrouter.selected_source
        target.openrouter.selection_alias = source.openrouter.selection_alias
        target.openrouter.fallback_selection_alias = source.openrouter.fallback_selection_alias
        target.qwen.llm_model = source.qwen.llm_model
        target.qwen.region = source.qwen.region
        if source.openrouter.selected_source == OpenRouterCredentialSource.MANAGED:
            target.managed_identity.verified_hardware_hash = (
                source.managed_identity.verified_hardware_hash
            )
            target.managed_identity.verified_hardware_hash_salt_version = (
                source.managed_identity.verified_hardware_hash_salt_version
            )
        target.system_prompt = source.system_prompt
        target.system_prompts = copy.deepcopy(source.system_prompts)

    def _build_settings_with_provider_draft(self) -> AppSettings | None:
        if self._settings is None:
            return None
        if self._provider_settings_draft is None:
            return self._settings
        merged = copy.deepcopy(self._settings)
        self._copy_provider_draft_fields(self._provider_settings_draft, merged)
        return merged

    def _ensure_provider_settings_draft(self) -> AppSettings:
        assert self._settings is not None
        if self._provider_settings_draft is None:
            self._provider_settings_draft = copy.deepcopy(self._settings)
        return self._provider_settings_draft

    def _normalized_peer_stt_provider(self, provider: STTProviderName) -> STTProviderName:
        return provider

    def _effective_peer_stt_provider(self, settings: AppSettings | None) -> STTProviderName:
        if settings is None:
            return STTProviderName.DEEPGRAM
        return self._normalized_peer_stt_provider(settings.provider.peer_stt)

    def _peer_stt_option_item(self, provider: STTProviderName) -> OptionItem:
        return OptionItem(
            value=provider.value,
            label=provider_label(provider.value),
            description=t(f"provider.{provider.value}.description", default=""),
        )

    def _sanitize_provider_apply_settings(self, settings: AppSettings | None) -> AppSettings | None:
        return settings

    def _stage_prompt_draft(self, value: str) -> None:
        if not self._settings:
            return
        committed_prompt = self._committed_prompt_value()
        prompt_key = self._active_prompt_key()
        draft = self._ensure_provider_settings_draft()
        draft.system_prompt = value
        draft.system_prompts[prompt_key] = value
        self.has_pending_prompt_changes = value != committed_prompt
        if not self.has_pending_prompt_changes and not self.has_provider_changes:
            self._provider_settings_draft = None

    def _committed_prompt_value(self) -> str:
        if not self._settings:
            return ""
        prompt_key = self._active_prompt_key_for_settings(self._settings)
        return self._settings.system_prompts.get(prompt_key, self._settings.system_prompt)

    def build_provider_apply_settings(self) -> AppSettings | None:
        return self._sanitize_provider_apply_settings(self._build_settings_with_provider_draft())

    def consume_provider_apply_settings(self) -> AppSettings | None:
        settings = self.build_provider_apply_settings()
        if settings is None:
            return None
        self._settings = settings
        self._provider_settings_draft = None
        self.has_provider_changes = False
        self.has_pending_prompt_changes = False
        return settings

    def consume_prompt_apply_settings(self) -> AppSettings | None:
        if not self.has_pending_prompt_changes:
            return None
        settings = self._sanitize_provider_apply_settings(
            self._build_settings_with_provider_draft()
        )
        if settings is None:
            return None
        self._settings = settings
        self.has_pending_prompt_changes = False
        if not self.has_provider_changes:
            self._provider_settings_draft = None
        return settings

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
        self._provider_settings_draft = None
        self._config_path = config_path
        self.has_provider_changes = False
        self.has_pending_prompt_changes = False
        self._sync_clickable_text_control_fonts(font_for_language(settings.ui.locale))

        # UI Language
        self._ui_text.content.value = locale_label(settings.ui.locale)

        # STT Provider
        self._set_unit_card_value_text(
            self._stt_text,
            provider_label(settings.provider.stt.value),
        )
        self._set_unit_card_value_text(
            self._peer_stt_text,
            provider_label(self._effective_peer_stt_provider(settings).value),
        )
        self._update_api_visibility()

        # LLM Provider
        self._set_unit_card_value_text(
            self._llm_text,
            self._get_llm_display_label(settings),
        )
        self._set_openrouter_routing_text(
            self._get_openrouter_routing_display_label(settings),
        )
        self._sync_openrouter_fallback_card(settings)

        # Qwen Region
        region_label = t(f"region.{settings.qwen.region.value}")
        _set_text_button_label(self._qwen_region_btn, f"{t('settings.qwen_region')} {region_label}")

        # Audio Settings
        self._audio_settings.host_api = settings.audio.input_host_api
        self._audio_settings.microphone = settings.audio.input_device
        self._audio_settings.desktop_output_device = settings.desktop_audio.output_device
        self._sync_general_audio_card_texts()

        # VAD
        self._vad_slider.value = settings.stt.vad_speech_threshold
        self._vad_slider.label = f"{settings.stt.vad_speech_threshold:.2f}"
        self._peer_vad_slider.value = settings.desktop_audio.vad_speech_threshold
        self._peer_vad_slider.label = f"{settings.desktop_audio.vad_speech_threshold:.2f}"
        self._peer_vad_field.value = f"{settings.desktop_audio.vad_speech_threshold:.2f}"
        self._peer_hangover_field.value = str(settings.desktop_audio.vad_hangover_ms)
        self._peer_pre_roll_field.value = str(settings.desktop_audio.vad_pre_roll_ms)
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
        # Prompt
        provider_name = self._active_prompt_key()
        self._prompt_editor.set_provider(provider_name)
        stored_prompt = settings.system_prompts.get(provider_name, "").strip()
        if stored_prompt:
            self._prompt_editor.value = stored_prompt
            settings.system_prompt = stored_prompt
        elif settings.system_prompt.strip():
            self._prompt_editor.value = settings.system_prompt
            settings.system_prompts[provider_name] = settings.system_prompt
        else:
            self._prompt_editor.load_default_prompt(emit_change=False)
            settings.system_prompt = self._prompt_editor.value
            settings.system_prompts[provider_name] = settings.system_prompt

        self._set_custom_vocabulary_draft_from_settings(
            preserve_existing=preserve_custom_vocab_draft
        )
        self._sync_prompt_tab_copy()
        self._custom_vocab_terms.helper_text = ""
        self._overlay_peer_contract = None
        self._sync_overlay_controls()
        self.set_overlay_calibration(
            settings.overlay.calibration,
            preserve_draft=self._overlay_calibration_session_active,
        )

        # Load secrets
        self._load_secrets(settings, config_path)

        if self.page:
            self.update()

    def refresh_after_openrouter_pkce_success(
        self,
        settings: AppSettings,
        *,
        config_path: Path,
    ) -> None:
        self._settings = settings
        self._provider_settings_draft = None
        self._config_path = config_path
        self.has_provider_changes = False
        self.has_pending_prompt_changes = False

        self._set_unit_card_value_text(
            self._llm_text,
            self._get_llm_display_label(settings),
        )
        self._set_openrouter_routing_text(
            self._get_openrouter_routing_display_label(settings),
        )
        self._sync_openrouter_fallback_card(settings)
        self._update_api_visibility()

        provider_name = self._active_prompt_key()
        self._prompt_editor.set_provider(provider_name)
        stored_prompt = settings.system_prompts.get(provider_name, "").strip()
        if stored_prompt:
            self._prompt_editor.value = stored_prompt
            settings.system_prompt = stored_prompt
        elif settings.system_prompt.strip():
            self._prompt_editor.value = settings.system_prompt
            settings.system_prompts[provider_name] = settings.system_prompt
        else:
            self._prompt_editor.load_default_prompt(emit_change=False)
            settings.system_prompt = self._prompt_editor.value
            settings.system_prompts[provider_name] = settings.system_prompt
        self._sync_prompt_tab_copy()

        try:
            store = create_secret_store(settings.secrets, config_path=config_path)
        except Exception as exc:
            self._emit_runtime_basic(f"Failed to load secrets: {exc}", level=logging.WARNING)
        else:
            self._openrouter_key.value = store.get("openrouter_api_key") or ""
            self._restore_api_key_icons(settings)

        if self.page:
            self.update()

    def _load_secrets(self, settings: AppSettings, config_path: Path) -> None:
        """Load secret values into fields."""
        try:
            store = create_secret_store(settings.secrets, config_path=config_path)
        except Exception as exc:
            self._emit_runtime_basic(f"Failed to load secrets: {exc}", level=logging.WARNING)
            return

        self._google_key.value = store.get("google_api_key") or ""
        self._openrouter_key.value = store.get("openrouter_api_key") or ""
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
            (self._openrouter_key, self._openrouter_key.value, verified.openrouter),
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
        self._sync_openrouter_pkce_button_state(settings)

    def _sync_openrouter_pkce_button_state(self, settings: AppSettings | None = None) -> None:
        if settings is None:
            settings = self._build_settings_with_provider_draft()
        authenticated = bool(
            settings is not None
            and settings.api_key_verified.openrouter
            and self._openrouter_key.value
        )
        _set_text_button_label(
            self._openrouter_pkce_button,
            t(
                "settings.openrouter_authenticated"
                if authenticated
                else "settings.openrouter_authenticate"
            ),
        )
        self._openrouter_pkce_button.disabled = authenticated
        self._openrouter_pkce_button.style = self._get_button_style(
            font_for_language(get_locale()),
            default_color=COLOR_NEUTRAL_DARK,
            disabled_color=COLOR_NEUTRAL_DARK,
        )
        if getattr(self._openrouter_pkce_button, "page", None):
            self._openrouter_pkce_button.update()

    # --- Visibility Updates ---
    def _sync_managed_trial_usage_bar(self) -> None:
        settings = self._build_settings_with_provider_draft()
        managed_selected = bool(
            settings is not None
            and settings.provider.llm == LLMProviderName.OPENROUTER
            and settings.openrouter.selected_source == OpenRouterCredentialSource.MANAGED
        )
        self._managed_trial_usage_bar.visible = managed_selected
        self._managed_trial_usage_bar.set_percent(
            self._managed_trial_usage_remaining_percent
            if managed_selected and self._managed_trial_usage_visible
            else None
        )

    def _update_api_visibility(self) -> None:
        """Update API key field visibility based on selected providers."""
        settings = self._build_settings_with_provider_draft()
        if settings is None:
            return

        stt = settings.provider.stt
        llm = settings.provider.llm
        peer_stt = self._effective_peer_stt_provider(settings)
        fallback_source = self._openrouter_fallback_source(settings)
        active_stt_providers = {stt, peer_stt}
        self._deepgram_key.visible = STTProviderName.DEEPGRAM in active_stt_providers
        self._soniox_key.visible = STTProviderName.SONIOX in active_stt_providers

        self._google_key.visible = llm == LLMProviderName.GEMINI
        self._sync_managed_trial_usage_bar()
        openrouter_byok_selected = bool(
            llm == LLMProviderName.OPENROUTER
            and settings.openrouter.selected_source == OpenRouterCredentialSource.BYOK
        )
        self._openrouter_key.visible = bool(
            openrouter_byok_selected or fallback_source == OpenRouterCredentialSource.BYOK
        )
        self._openrouter_pkce_button_row.visible = openrouter_byok_selected
        self._sync_openrouter_pkce_button_state(settings)
        self._openrouter_routing_row.visible = True
        self._sync_openrouter_fallback_card(settings)

        qwen_regions: set[QwenRegion] = set()
        if (
            stt == STTProviderName.QWEN_ASR
            or llm == LLMProviderName.QWEN
            or peer_stt == STTProviderName.QWEN_ASR
        ):
            qwen_regions.add(settings.qwen.region)

        self._qwen_region_btn.visible = (
            stt == STTProviderName.QWEN_ASR or llm == LLMProviderName.QWEN
        )
        self._alibaba_key_beijing.visible = QwenRegion.BEIJING in qwen_regions
        self._alibaba_key_singapore.visible = QwenRegion.SINGAPORE in qwen_regions

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
        display_settings = self._build_settings_with_provider_draft()
        current = (
            display_settings.provider.stt.value
            if display_settings is not None
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
        current_settings = self._build_settings_with_provider_draft()
        assert current_settings is not None
        provider = STTProviderName(value)
        old_provider = current_settings.provider.stt.value
        if old_provider == provider.value:
            return
        self._emit_runtime_basic(
            f"[Settings] STT provider changed: {old_provider} -> {provider.value}"
        )
        draft = self._ensure_provider_settings_draft()
        draft.provider.stt = provider
        self._update_api_visibility()
        self.has_provider_changes = True

        # Update text
        self._set_unit_card_value_text(self._stt_text, provider_label(provider.value))

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

    def _on_peer_stt_click(self, e) -> None:
        if not self.page:
            return
        options = [self._peer_stt_option_item(provider) for provider in STTProviderName]
        display_settings = self._build_settings_with_provider_draft()
        current_provider = (
            display_settings.provider.peer_stt
            if display_settings is not None
            else STTProviderName.DEEPGRAM
        )
        current = self._normalized_peer_stt_provider(current_provider).value
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
        current_settings = self._build_settings_with_provider_draft()
        assert current_settings is not None
        provider = STTProviderName(value)
        if current_settings.provider.peer_stt == provider:
            return
        draft = self._ensure_provider_settings_draft()
        draft.provider.peer_stt = provider
        self._set_unit_card_value_text(self._peer_stt_text, provider_label(value))
        self._update_api_visibility()
        if self.page:
            self._peer_stt_text.update()
            self._api_keys_column.update()
        self.has_provider_changes = True

    def _on_llm_click(self, e) -> None:
        """Open LLM provider selection modal."""
        if not self.page:
            return
        managed_openrouter_options: list[OptionItem] = []
        byok_openrouter_options: list[OptionItem] = []
        for alias in OPENROUTER_MAIN_SELECTION_ALIASES:
            profile = profile_for_alias(alias)
            option = OptionItem(
                value=profile.alias,
                label=self._openrouter_profile_display_label(profile),
                description=self._openrouter_profile_display_description(profile),
            )
            if profile.openrouter_source == OpenRouterCredentialSource.MANAGED.value:
                managed_openrouter_options.append(option)
            else:
                byok_openrouter_options.append(option)
        options = [
            *managed_openrouter_options,
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
            *byok_openrouter_options,
            OptionItem(
                value=QwenLLMModel.QWEN_35_PLUS.value,
                label=t("provider.qwen35_plus"),
                description=t("provider.qwen35_plus.description", default=""),
            ),
        ]
        display_settings = self._build_settings_with_provider_draft()
        current = (
            self._get_llm_modal_value(display_settings)
            if display_settings is not None
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
        current_settings = self._build_settings_with_provider_draft()
        assert current_settings is not None
        old_provider = current_settings.provider.llm
        old_gemini_model = current_settings.gemini.llm_model
        old_openrouter_model = current_settings.openrouter.llm_model
        old_openrouter_selected_source = current_settings.openrouter.selected_source
        old_openrouter_selection_alias = self._stored_openrouter_selection_alias(current_settings)
        old_qwen_model = current_settings.qwen.llm_model
        try:
            openrouter_profile = profile_for_alias(value)
        except KeyError:
            openrouter_profile = None
        if (
            openrouter_profile is not None
            and openrouter_profile.provider != LLMProviderName.OPENROUTER.value
        ):
            openrouter_profile = None

        if value == LLMProviderName.GEMINI.value:
            provider = LLMProviderName.GEMINI
            gemini_model = GeminiLLMModel.GEMINI_3_FLASH
            openrouter_model = old_openrouter_model
            qwen_model = old_qwen_model
            openrouter_selected_source = old_openrouter_selected_source
            openrouter_selection_alias = old_openrouter_selection_alias
        elif value == GeminiLLMModel.GEMINI_3_FLASH.value:
            provider = LLMProviderName.GEMINI
            gemini_model = GeminiLLMModel.GEMINI_3_FLASH
            openrouter_model = old_openrouter_model
            qwen_model = old_qwen_model
            openrouter_selected_source = old_openrouter_selected_source
            openrouter_selection_alias = old_openrouter_selection_alias
        elif value == GeminiLLMModel.GEMINI_31_FLASH_LITE.value:
            provider = LLMProviderName.GEMINI
            gemini_model = GeminiLLMModel.GEMINI_31_FLASH_LITE
            openrouter_model = old_openrouter_model
            qwen_model = old_qwen_model
            openrouter_selected_source = old_openrouter_selected_source
            openrouter_selection_alias = old_openrouter_selection_alias
        elif value == LLMProviderName.OPENROUTER.value:
            provider = LLMProviderName.OPENROUTER
            gemini_model = old_gemini_model
            qwen_model = old_qwen_model
            openrouter_selection_alias = OpenRouterSelectionAlias(_OPENROUTER_MANAGED_OPTION_VALUE)
            openrouter_profile = profile_for_alias(openrouter_selection_alias.value)
            assert openrouter_profile.openrouter_model is not None
            openrouter_model = OpenRouterLLMModel(openrouter_profile.openrouter_model)
            openrouter_selected_source = OpenRouterCredentialSource(
                openrouter_profile.openrouter_source
            )
        elif openrouter_profile is not None and (
            openrouter_profile.openrouter_source == OpenRouterCredentialSource.BYOK.value
        ):
            provider = LLMProviderName.OPENROUTER
            gemini_model = old_gemini_model
            qwen_model = old_qwen_model
            openrouter_selection_alias = OpenRouterSelectionAlias(openrouter_profile.alias)
            assert openrouter_profile.openrouter_model is not None
            openrouter_model = OpenRouterLLMModel(openrouter_profile.openrouter_model)
            openrouter_selected_source = OpenRouterCredentialSource.BYOK
        elif openrouter_profile is not None:
            provider = LLMProviderName.OPENROUTER
            gemini_model = old_gemini_model
            qwen_model = old_qwen_model
            openrouter_selection_alias = OpenRouterSelectionAlias(openrouter_profile.alias)
            assert openrouter_profile.openrouter_model is not None
            openrouter_model = OpenRouterLLMModel(openrouter_profile.openrouter_model)
            openrouter_selected_source = OpenRouterCredentialSource(
                openrouter_profile.openrouter_source
            )
        elif value == OpenRouterLLMModel.GEMMA_4_26B_A4B_IT.value:
            provider = LLMProviderName.OPENROUTER
            gemini_model = old_gemini_model
            openrouter_model = OpenRouterLLMModel.GEMMA_4_26B_A4B_IT
            qwen_model = old_qwen_model
            openrouter_selected_source = OpenRouterCredentialSource.BYOK
            openrouter_selection_alias = _derive_openrouter_selection_alias(
                openrouter_model,
                openrouter_selected_source,
            )
        elif value == OpenRouterLLMModel.QWEN_35_FLASH_02_23.value:
            provider = LLMProviderName.OPENROUTER
            gemini_model = old_gemini_model
            openrouter_model = OpenRouterLLMModel.QWEN_35_FLASH_02_23
            qwen_model = old_qwen_model
            openrouter_selected_source = OpenRouterCredentialSource.BYOK
            openrouter_selection_alias = _derive_openrouter_selection_alias(
                openrouter_model,
                openrouter_selected_source,
            )
        elif value == QwenLLMModel.QWEN_35_PLUS.value:
            provider = LLMProviderName.QWEN
            gemini_model = old_gemini_model
            openrouter_model = old_openrouter_model
            qwen_model = QwenLLMModel.QWEN_35_PLUS
            openrouter_selected_source = old_openrouter_selected_source
            openrouter_selection_alias = old_openrouter_selection_alias
        else:
            provider = LLMProviderName.QWEN
            gemini_model = old_gemini_model
            openrouter_model = old_openrouter_model
            qwen_model = QwenLLMModel.QWEN_35_FLASH
            openrouter_selected_source = old_openrouter_selected_source
            openrouter_selection_alias = old_openrouter_selection_alias

        changes: list[str] = []
        if old_provider != provider:
            changes.append(f"provider={old_provider.value}->{provider.value}")
        if old_gemini_model != gemini_model:
            changes.append(f"gemini_model={old_gemini_model.value}->{gemini_model.value}")
        if old_openrouter_model != openrouter_model:
            changes.append(
                f"openrouter_model={old_openrouter_model.value}->{openrouter_model.value}"
            )
        if old_openrouter_selected_source != openrouter_selected_source:
            changes.append(
                "openrouter_source="
                f"{old_openrouter_selected_source.value}->{openrouter_selected_source.value}"
            )
        if old_openrouter_selection_alias != openrouter_selection_alias:
            changes.append(
                "openrouter_alias="
                f"{old_openrouter_selection_alias.value if old_openrouter_selection_alias is not None else 'none'}"
                f"->{openrouter_selection_alias.value if openrouter_selection_alias is not None else 'none'}"
            )
        if old_qwen_model != qwen_model:
            changes.append(f"qwen_model={old_qwen_model.value}->{qwen_model.value}")
        if not changes:
            return
        if old_provider != provider:
            self._emit_runtime_basic(
                f"[Settings] LLM provider changed: {old_provider.value} -> {provider.value}"
            )
        self._emit_runtime_detailed(f"[Settings] LLM selection changed: {', '.join(changes)}")

        draft = self._ensure_provider_settings_draft()
        draft.provider.llm = provider
        draft.openrouter.llm_model = openrouter_model
        draft.openrouter.selected_source = openrouter_selected_source
        draft.openrouter.selection_alias = openrouter_selection_alias
        if provider == LLMProviderName.QWEN:
            draft.qwen.llm_model = qwen_model
        else:
            draft.gemini.llm_model = gemini_model
        self._update_api_visibility()
        self.has_provider_changes = True

        # Update text
        display_settings = self._build_settings_with_provider_draft()
        assert display_settings is not None
        self._set_unit_card_value_text(
            self._llm_text,
            self._get_llm_display_label(display_settings),
        )
        self._set_openrouter_routing_text(
            self._get_openrouter_routing_display_label(display_settings),
        )
        self._sync_openrouter_fallback_card(display_settings)

        # Update prompt if provider changed
        if old_provider != provider:
            provider_name = self._active_prompt_key()
            self._prompt_editor.set_provider(provider_name)
            next_prompt = self._ensure_provider_prompt_value(draft, provider_name)
            self._prompt_editor.value = next_prompt
            draft.system_prompt = next_prompt
        self._sync_prompt_tab_copy()

        if self.page:
            self._qwen_region_btn.update()
            self._api_keys_column.update()
            self._llm_text.update()
            self._openrouter_routing_row.update()

    def _on_openrouter_fallback_click(self, e) -> None:
        if not self.page:
            return
        options: list[OptionItem] = []
        for alias in OPENROUTER_FALLBACK_SELECTION_ALIASES:
            if alias == OpenRouterFallbackSelectionAlias.NONE.value:
                options.append(
                    OptionItem(
                        value=alias,
                        label=t("settings.openrouter_fallback.none"),
                        description=t("settings.openrouter_fallback.none.description", default=""),
                    )
                )
                continue
            profile = fallback_profile_for_alias(alias)
            options.append(
                OptionItem(
                    value=alias,
                    label=self._openrouter_profile_display_label(profile),
                    description=self._openrouter_profile_display_description(profile),
                )
            )
        display_settings = self._build_settings_with_provider_draft()
        current = (
            display_settings.openrouter.fallback_selection_alias.value
            if display_settings is not None
            else OpenRouterFallbackSelectionAlias.GEMINI25_FLASH_LITE.value
        )
        modal = SettingsModal(
            self.page,
            t("settings.openrouter_fallback"),
            options,
            self._on_openrouter_fallback_selected,
            show_description=True,
        )
        modal.open(current)

    def _on_openrouter_fallback_selected(self, value: str) -> None:
        if not self._settings:
            return

        current_settings = self._build_settings_with_provider_draft()
        assert current_settings is not None
        try:
            new_value = OpenRouterFallbackSelectionAlias(value)
        except ValueError:
            new_value = OpenRouterFallbackSelectionAlias.GEMINI25_FLASH_LITE

        old_value = current_settings.openrouter.fallback_selection_alias
        if old_value == new_value:
            return

        self._emit_runtime_detailed(
            "[Settings] OpenRouter fallback selection changed: "
            f"{old_value.value}->{new_value.value}"
        )
        draft = self._ensure_provider_settings_draft()
        draft.openrouter.fallback_selection_alias = new_value
        self.has_provider_changes = True
        self._update_api_visibility()

        display_settings = self._build_settings_with_provider_draft()
        self._sync_openrouter_fallback_card(display_settings)
        if self.page:
            self._api_keys_column.update()
            self._openrouter_routing_row.update()

    def _on_openrouter_routing_click(self, e) -> None:
        """Open OpenRouter routing selection modal."""
        if not self.page:
            return
        options = [
            OptionItem(
                value=OpenRouterRoutingMode.LATENCY.value,
                label=t("settings.openrouter_routing.latency"),
                description=t("settings.openrouter_routing.latency.description", default=""),
            ),
        ]
        display_settings = self._build_settings_with_provider_draft()
        current = (
            display_settings.openrouter.routing_mode.value
            if display_settings is not None
            else OpenRouterRoutingMode.LATENCY.value
        )
        modal = SettingsModal(
            self.page,
            t("settings.openrouter_routing"),
            options,
            self._on_openrouter_routing_selected,
            show_description=True,
        )
        modal.open(current)

    def _on_openrouter_routing_selected(self, value: str) -> None:
        if not self._settings:
            return

        current_settings = self._build_settings_with_provider_draft()
        assert current_settings is not None
        old_value = current_settings.openrouter.routing_mode
        try:
            new_value = OpenRouterRoutingMode(value)
        except ValueError:
            new_value = OpenRouterRoutingMode.LATENCY

        if old_value == new_value:
            return

        self._emit_runtime_detailed(
            f"[Settings] OpenRouter routing mode changed: {old_value} -> {new_value}"
        )
        draft = self._ensure_provider_settings_draft()
        draft.openrouter.routing_mode = new_value
        self.has_provider_changes = True
        self._set_openrouter_routing_text(
            self._get_openrouter_routing_display_label(self._build_settings_with_provider_draft()),
        )
        if self.page:
            self._openrouter_routing_text.update()

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
        self._emit_runtime_basic(f"[Settings] Language changed: {old_locale} -> {value}")
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
        display_settings = self._build_settings_with_provider_draft()
        current = (
            display_settings.qwen.region.value
            if display_settings is not None
            else QwenRegion.BEIJING.value
        )
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

        current_settings = self._build_settings_with_provider_draft()
        assert current_settings is not None
        old_region = current_settings.qwen.region.value
        if old_region == value:
            return
        self._emit_runtime_detailed(f"[Settings] Qwen region changed: {old_region} -> {value}")
        draft = self._ensure_provider_settings_draft()
        draft.qwen.region = QwenRegion(value)
        self.has_provider_changes = True

        # Update text
        _set_text_button_label(
            self._qwen_region_btn,
            f"{t('settings.qwen_region')} {t(f'region.{value}')}",
        )
        if self.page:
            self._qwen_region_btn.update()

        self._update_api_visibility()
        if self.page:
            self._api_keys_column.update()

    def _on_openrouter_pkce_click(self, _e) -> None:
        settings = self._build_settings_with_provider_draft()
        if settings is None or self.on_request_openrouter_pkce is None:
            return
        if settings.api_key_verified.openrouter and self._openrouter_key.value:
            return
        if settings.provider.llm != LLMProviderName.OPENROUTER:
            return
        if settings.openrouter.selected_source != OpenRouterCredentialSource.BYOK:
            return
        profile = self._openrouter_selection_profile(settings)
        if profile is None or profile.openrouter_source != OpenRouterCredentialSource.BYOK.value:
            return

        target = copy.deepcopy(settings)
        target.provider.llm = LLMProviderName.OPENROUTER
        target.openrouter.selection_alias = OpenRouterSelectionAlias(profile.alias)
        target.openrouter.selected_source = OpenRouterCredentialSource.BYOK
        assert profile.openrouter_model is not None
        target.openrouter.llm_model = OpenRouterLLMModel(profile.openrouter_model)
        target.system_prompt = self._ensure_provider_prompt_value(target, "openrouter")
        self.on_request_openrouter_pkce(target)

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
            if key == "openrouter_api_key":
                self._sync_openrouter_pkce_button_state()

    def _on_audio_change(self) -> None:
        if not self._settings:
            return

        new_host = self._audio_settings.host_api
        new_device = self._audio_settings.microphone
        new_desktop_output = self._audio_settings.desktop_output_device
        old_host = self._settings.audio.input_host_api
        old_device = self._settings.audio.input_device
        old_desktop_output = self._settings.desktop_audio.output_device

        if old_host != new_host:
            self._emit_runtime_basic(f"[Settings] Audio Host changed: {old_host} -> {new_host}")
        if old_device != new_device:
            self._emit_runtime_basic(f"[Settings] Microphone changed: {old_device} -> {new_device}")
        if old_desktop_output != new_desktop_output:
            self._emit_runtime_basic(
                f"[Settings] Desktop loopback output changed: {old_desktop_output} -> {new_desktop_output}"
            )

        self._settings.audio.input_host_api = new_host
        self._settings.audio.input_device = new_device
        self._settings.desktop_audio.output_device = new_desktop_output
        self._emit_settings_changed()

    def _on_mic_host_api_click(self, e) -> None:
        if not self.page:
            return
        options = self._audio_settings._get_host_api_options()
        modal = SettingsModal(
            self.page,
            t("settings.audio_host_api"),
            options,
            self._on_mic_host_api_selected,
            show_description=False,
        )
        modal.open(self._audio_settings.host_api)

    def _on_mic_host_api_selected(self, value: str) -> None:
        self._audio_settings.host_api = value
        self._audio_settings.microphone = ""
        self._sync_general_audio_card_texts()
        if self.page:
            self._mic_audio_text.update()
            self._audio_host_api_text.update()
        self._on_audio_change()

    def _on_mic_audio_click(self, e) -> None:
        if not self.page:
            return
        options = self._audio_settings._get_microphone_options()
        modal = SettingsModal(
            self.page,
            t("settings.section.microphone_audio"),
            options,
            self._on_mic_audio_selected,
            show_description=False,
        )
        modal.open(self._audio_settings.microphone)

    def _on_mic_audio_selected(self, value: str) -> None:
        self._audio_settings.microphone = value
        self._sync_general_audio_card_texts()
        if self.page:
            self._mic_audio_text.update()
        self._on_audio_change()

    def _on_loopback_audio_click(self, e) -> None:
        if not self.page:
            return
        options = self._audio_settings._get_desktop_output_options()
        modal = SettingsModal(
            self.page,
            t("settings.section.loopback_audio"),
            options,
            self._on_loopback_audio_selected,
            show_description=False,
        )
        modal.open(self._audio_settings.desktop_output_device)

    def _on_loopback_audio_selected(self, value: str) -> None:
        self._audio_settings.desktop_output_device = value
        self._sync_general_audio_card_texts()
        if self.page:
            self._loopback_audio_text.update()
        self._on_audio_change()

    def set_overlay_calibration(
        self,
        calibration: OverlayCalibration,
        *,
        preserve_draft: bool = False,
    ) -> None:
        calibration.validate()
        self._overlay_calibration = calibration.copy()

        if preserve_draft and self._overlay_calibration_session_active:
            self._sync_overlay_calibration_controls(self._overlay_calibration_draft)
            return

        self._overlay_calibration_draft = calibration.copy()
        self._overlay_calibration_session_active = False
        self._sync_overlay_calibration_controls(self._overlay_calibration)

    def _sync_overlay_calibration_controls(
        self,
        calibration: OverlayCalibration | None = None,
    ) -> None:
        current = (calibration or self._overlay_calibration).copy()
        self._set_unit_card_value_text(
            self._overlay_anchor_button,
            self._overlay_anchor_label_for(current.anchor),
        )
        self._overlay_distance_value_text.value = self._format_overlay_calibration_number(
            current.distance
        )
        self._overlay_offset_x_value_text.value = self._format_overlay_calibration_number(
            current.offset_x
        )
        self._overlay_offset_y_value_text.value = self._format_overlay_calibration_number(
            current.offset_y
        )
        self._overlay_text_scale_text.content.value = self._overlay_text_scale_label_for(
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

    def _commit_overlay_calibration_draft(self) -> OverlayCalibration:
        if self.on_overlay_calibration_apply:
            calibration = self.on_overlay_calibration_apply()
            calibration.validate()
        else:
            if not self._overlay_calibration_session_active:
                self._begin_overlay_calibration_session()
            calibration = self._overlay_calibration_draft.copy()

        self._overlay_calibration = calibration.copy()
        self._overlay_calibration_draft = calibration.copy()
        self._overlay_calibration_session_active = False
        if self._settings is not None:
            self._settings.overlay.calibration = calibration.copy()
        self._sync_overlay_calibration_controls(self._overlay_calibration)

        if self.page:
            self.update()

        if self.on_overlay_calibration_apply is None:
            self._emit_settings_changed()

        return calibration.copy()

    def _apply_overlay_calibration_field_immediately(
        self,
        field_name: str,
        value: object,
    ) -> OverlayCalibration | None:
        try:
            self._update_overlay_calibration_draft(field_name, value)
        except ValueError:
            self._sync_overlay_calibration_controls(self._overlay_calibration)
            return None

        return self._commit_overlay_calibration_draft()

    def _on_overlay_distance_step(self, delta: float) -> None:
        current = self._overlay_calibration.distance
        next_value = max(_OVERLAY_DISTANCE_MIN, min(_OVERLAY_DISTANCE_MAX, current + delta))
        self._apply_overlay_calibration_field_immediately("distance", round(next_value, 2))

    def _on_overlay_anchor_click(self, e) -> None:
        if not self.page or not self._settings:
            return
        options = [
            OptionItem(value=anchor, label=t(f"settings.overlay.calibration.anchor.{anchor}"))
            for anchor in OVERLAY_CALIBRATION_ANCHORS
        ]
        modal = SettingsModal(
            self.page,
            t("settings.overlay.calibration.anchor"),
            options,
            self._on_overlay_anchor_selected,
            show_description=False,
        )
        modal.open(self._overlay_calibration.anchor)

    def _on_overlay_anchor_selected(self, value: str) -> None:
        self._apply_overlay_calibration_field_immediately("anchor", value)

    def _on_overlay_offset_x_step(self, delta: float) -> None:
        current = self._overlay_calibration.offset_x
        self._apply_overlay_calibration_field_immediately("offset_x", current + delta)

    def _on_overlay_offset_y_step(self, delta: float) -> None:
        current = self._overlay_calibration.offset_y
        self._apply_overlay_calibration_field_immediately("offset_y", current + delta)

    def _on_overlay_text_scale_click(self, e) -> None:
        if not self.page or not self._settings:
            return
        options = [
            OptionItem(
                value=key,
                label=t(f"settings.overlay.calibration.text_scale.{key}"),
            )
            for key, _scale in _OVERLAY_TEXT_SCALE_PRESETS
        ]
        modal = SettingsModal(
            self.page,
            t("settings.overlay.calibration.text_scale"),
            options,
            self._on_overlay_text_scale_selected,
            show_description=False,
        )
        modal.open(self._overlay_text_scale_preset_key_for(self._overlay_calibration.text_scale))

    def _on_overlay_text_scale_selected(self, value: str) -> None:
        self._apply_overlay_calibration_field_immediately(
            "text_scale", self._overlay_text_scale_value_for(value)
        )

    def _on_overlay_position_reset(self, e) -> None:
        _ = e
        defaults = OverlayCalibration()
        for field_name in OverlayCalibration.__dataclass_fields__:
            self._update_overlay_calibration_draft(field_name, getattr(defaults, field_name))
        self._commit_overlay_calibration_draft()

    def set_overlay_peer_contract(self, contract: OverlayPeerConsumerContract) -> None:
        self._overlay_peer_contract = contract
        if self._settings is not None:
            self._settings.ui.overlay_enabled = contract.overlay.intent_enabled
            self._settings.ui.peer_translation_enabled = contract.peer.intent_enabled
            self._update_api_visibility()
            if self.page:
                self._api_keys_column.update()
        self._sync_overlay_controls()

    def _sync_overlay_controls(self) -> None:
        overlay_translation_enabled = bool(
            self._settings and self._settings.overlay.show_translation
        )
        overlay_peer_original_enabled = bool(
            self._settings and self._settings.overlay.show_peer_original
        )
        integrated_context_enabled = bool(
            self._settings and self._settings.ui.integrated_context_enabled
        )

        self._set_unit_card_value_text(
            self._overlay_translation_button,
            t("settings.option.on" if overlay_translation_enabled else "settings.option.off"),
        )
        self._set_unit_card_value_text(
            self._overlay_peer_original_button,
            t("settings.option.on" if overlay_peer_original_enabled else "settings.option.off"),
        )
        self._set_unit_card_value_text(
            self._integrated_context_button,
            t(
                "settings.context.integrated"
                if integrated_context_enabled
                else "settings.context.local"
            ),
        )

        self._overlay_translation_button.disabled = self._settings is None
        self._overlay_peer_original_button.disabled = self._settings is None
        self._overlay_anchor_button.disabled = self._settings is None
        self._overlay_distance_decrease_button.disabled = self._settings is None
        self._overlay_distance_increase_button.disabled = self._settings is None
        self._overlay_offset_x_decrease_button.disabled = self._settings is None
        self._overlay_offset_x_increase_button.disabled = self._settings is None
        self._overlay_offset_y_decrease_button.disabled = self._settings is None
        self._overlay_offset_y_increase_button.disabled = self._settings is None
        self._overlay_reset_button.disabled = self._settings is None
        self._integrated_context_button.disabled = self._settings is None
        self._integrated_context_hint.value = ""

        if self.page:
            self.update()

    def set_overlay_runtime_state(
        self,
        state: str,
        *,
        failure_reason: str | None = None,
    ) -> None:
        _ = failure_reason
        self._overlay_state = state
        self._sync_overlay_controls()

    def _on_overlay_calibration_reset(self, e) -> None:
        _ = e
        self._begin_overlay_calibration_session()
        self._overlay_calibration_draft = OverlayCalibration()
        self._sync_overlay_calibration_controls(self._overlay_calibration_draft)

        if self.page:
            self.update()

    def _on_overlay_translation_click(self, e) -> None:
        if not self._settings or self._overlay_translation_button.disabled:
            return
        next_value = "off" if self._settings.overlay.show_translation else "on"
        self._on_overlay_translation_selected(next_value)

    def _on_overlay_translation_selected(self, value: str) -> None:
        if not self._settings:
            return
        self._settings.overlay.show_translation = value == "on"
        self._sync_overlay_controls()
        self._emit_settings_changed()

    def _on_overlay_peer_original_click(self, e) -> None:
        if not self._settings or self._overlay_peer_original_button.disabled:
            return
        next_value = "off" if self._settings.overlay.show_peer_original else "on"
        self._on_overlay_peer_original_selected(next_value)

    def _on_overlay_peer_original_selected(self, value: str) -> None:
        if not self._settings:
            return
        self._settings.overlay.show_peer_original = value == "on"
        self._sync_overlay_controls()
        self._emit_settings_changed()

    def _on_integrated_context_click(self, e) -> None:
        if not self.page or not self._settings:
            return
        options = [
            OptionItem(value="off", label=t("settings.context.local")),
            OptionItem(
                value="on",
                label=t("settings.context.integrated"),
                description=t("settings.context.integrated_modal_helper"),
            ),
        ]
        modal = SettingsModal(
            self.page,
            t("settings.integrated_context"),
            options,
            self._on_integrated_context_selected,
            show_description=True,
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
        _update_control_if_mounted(self._vad_slider)

    def _handle_vad_change(self, e) -> None:
        if not self._settings:
            return

        new_vad = float(e.control.value)
        old_vad = self._settings.stt.vad_speech_threshold

        if abs(old_vad - new_vad) > 0.001:
            self._emit_runtime_detailed(
                f"[Settings] VAD sensitivity changed: {old_vad:.2f} -> {new_vad:.2f}"
            )

        self._settings.stt.vad_speech_threshold = new_vad
        self._emit_settings_changed()

    def _handle_peer_vad_visual_change(self, e) -> None:
        self._peer_vad_slider.label = f"{float(e.control.value):.2f}"
        _update_control_if_mounted(self._peer_vad_slider)

    def _handle_peer_vad_change(self, e) -> None:
        if not self._settings:
            return

        new_vad = float(e.control.value)
        old_vad = self._settings.desktop_audio.vad_speech_threshold

        if abs(old_vad - new_vad) > 0.001:
            self._emit_runtime_detailed(
                f"[Settings] Peer VAD threshold changed: {old_vad:.2f} -> {new_vad:.2f}"
            )

        self._settings.desktop_audio.vad_speech_threshold = new_vad
        self._peer_vad_field.value = f"{new_vad:.2f}"
        self._peer_vad_slider.label = f"{new_vad:.2f}"
        _update_control_if_mounted(self._peer_vad_field)
        _update_control_if_mounted(self._peer_vad_slider)
        self._emit_settings_changed()

    def _on_peer_vad_threshold_change(self, e) -> None:
        if not self._settings:
            return

        old_value = self._settings.desktop_audio.vad_speech_threshold
        new_value = self._parse_setting_float(
            e.control.value,
            fallback=old_value,
            minimum=0.0,
            maximum=1.0,
        )
        if abs(old_value - new_value) > 0.001:
            self._emit_runtime_detailed(
                f"[Settings] Peer VAD threshold changed: {old_value:.2f} -> {new_value:.2f}"
            )

        self._settings.desktop_audio.vad_speech_threshold = new_value
        self._peer_vad_field.value = f"{new_value:.2f}"
        _update_control_if_mounted(self._peer_vad_field)
        self._emit_settings_changed()

    def _on_peer_hangover_change(self, e) -> None:
        if not self._settings:
            return

        old_value = self._settings.desktop_audio.vad_hangover_ms
        new_value = self._parse_setting_int(
            e.control.value,
            fallback=old_value,
            minimum=0,
        )
        if old_value != new_value:
            self._emit_runtime_detailed(
                f"[Settings] Peer hangover changed: {old_value} -> {new_value}"
            )

        self._settings.desktop_audio.vad_hangover_ms = new_value
        self._peer_hangover_field.value = str(new_value)
        _update_control_if_mounted(self._peer_hangover_field)
        self._emit_settings_changed()

    def _on_peer_pre_roll_change(self, e) -> None:
        if not self._settings:
            return

        old_value = self._settings.desktop_audio.vad_pre_roll_ms
        new_value = self._parse_setting_int(
            e.control.value,
            fallback=old_value,
            minimum=0,
        )
        if old_value != new_value:
            self._emit_runtime_detailed(
                f"[Settings] Peer pre-roll changed: {old_value} -> {new_value}"
            )

        self._settings.desktop_audio.vad_pre_roll_ms = new_value
        self._peer_pre_roll_field.value = str(new_value)
        _update_control_if_mounted(self._peer_pre_roll_field)
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
        self._emit_runtime_basic(f"[Settings] VRC mic intercept toggled: {new_value}")
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
        self._emit_runtime_basic(f"[Settings] Chatbox include source toggled: {new_value}")
        self._settings.osc.chatbox_include_source = new_value

        self._chatbox_source_text.content.value = t(
            "settings.chatbox_source.on" if new_value else "settings.chatbox_source.off"
        )
        if self.page:
            self._chatbox_source_text.update()
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
            self._emit_runtime_detailed(
                f"[Settings] Low latency mode changed: {old_value} -> {new_value}"
            )
        self._settings.stt.low_latency_mode = new_value

        # Update text
        self._low_latency_text.content.value = t("toggle.on" if new_value else "toggle.off")
        if self.page:
            self._low_latency_text.update()
        self._emit_settings_changed()

    def _on_prompt_change(self, value: str) -> None:
        self._stage_prompt_draft(value)

    def _on_prompt_commit(self, value: str) -> None:
        if not self.has_pending_prompt_changes and value == self._committed_prompt_value():
            return
        self._stage_prompt_draft(value)
        if self.has_provider_changes:
            return
        pending = self.consume_prompt_apply_settings()
        if pending is None:
            return
        self._emit_prompt_apply_settings(pending)

    def _on_reset_prompt(self, e) -> None:
        """Reset prompt to default for current provider."""
        self._prompt_editor.load_default_prompt()
        self._on_prompt_commit(self._prompt_editor.value)

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
            self._emit_runtime_detailed(
                "[Settings] Custom vocabulary capped: "
                f"language={source_language}, requested={unique_count}, applied={MAX_CUSTOM_VOCAB_TERMS}"
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
        self._emit_runtime_detailed(
            f"[Settings] Custom vocabulary applied: language={source_language}, terms={len(parsed_terms)}"
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
            result = await self.on_verify_api_key(provider, key)
            if provider == "openrouter":
                self._sync_openrouter_pkce_button_state()
            return result
        return False, "Verification not available"

    def _emit_settings_changed(self) -> None:
        if self._settings and self.on_settings_changed:
            self.on_settings_changed(self._sanitize_provider_apply_settings(self._settings))

    def _emit_prompt_apply_settings(self, settings: AppSettings) -> None:
        sanitized = self._sanitize_provider_apply_settings(settings)
        if sanitized is None:
            return
        if self.on_prompt_apply_settings:
            self.on_prompt_apply_settings(sanitized)
            return
        if self.on_settings_changed:
            self.on_settings_changed(sanitized)

    # --- Locale ---
    def apply_locale(self) -> None:
        """Update all labels when locale changes."""
        self._settings_subtab_shell.set_font_family(font_for_language(get_locale()))
        for key in _SETTINGS_SUBTAB_ORDER:
            self._settings_subtab_shell.set_tab_label(key, self._settings_subtab_label(key))

        # Section titles
        self._stt_title.value = t("settings.section.stt")
        self._trans_title.value = t("settings.section.translation")
        self._api_title.value = t("settings.section.api_keys")
        self._stt_provider_label.value = t("settings.self_stt_provider")
        self._translation_provider_label.value = t("settings.shared_translation_provider")
        self._api_credentials_helper_text.value = t("settings.api_credentials_helper")
        self._ui_title.value = t("settings.section.ui")
        self._audio_host_api_title.value = t("settings.audio_host_api")
        self._mic_audio_title.value = t("settings.section.microphone_audio")
        self._loopback_audio_title.value = t("settings.section.loopback_audio")
        self._self_vad_title.value = t("settings.section.self_vad_sensitivity")
        self._peer_vad_title.value = t("settings.section.peer_vad_sensitivity")
        self._peer_vad_field.label = t("settings.vad.peer")
        self._peer_hangover_field.label = t("settings.vad.peer_hangover_ms")
        self._peer_pre_roll_field.label = t("settings.vad.peer_pre_roll_ms")
        self._low_latency_title.value = t("settings.low_latency_mode")
        self._openrouter_routing_title.value = t("settings.openrouter_routing")
        self._openrouter_fallback_title.value = t("settings.openrouter_fallback")
        self._persona_title.value = t("settings.section.persona")
        self._custom_vocab_title.value = t("settings.section.custom_vocabulary")
        self._custom_vocab_info_icon.tooltip = t("settings.custom_vocabulary_tooltip")
        self._vrc_mic_title.value = t("settings.vrc_mic_intercept")
        self._chatbox_source_title.value = t("settings.chatbox_include_source")
        self._peer_provider_title.value = t("settings.section.peer_stt")
        self._dashboard_language_redirect_text.value = t("settings.dashboard_language_redirect")
        self._peer_stt_label.value = t("settings.peer_stt_provider")
        self._overlay_translation_title.value = t("settings.overlay.show_translation")
        self._overlay_peer_original_title.value = t("settings.overlay.show_peer_original")
        self._integrated_context_label.value = t("settings.integrated_context")
        self._audio_settings.apply_locale()
        self._sync_general_audio_card_texts()
        self._overlay_anchor_title.value = t("settings.overlay.calibration.anchor")
        self._overlay_distance_title.value = t("settings.overlay.calibration.distance")
        self._overlay_offset_x_title.value = t("settings.overlay.calibration.offset_x")
        self._overlay_offset_y_title.value = t("settings.overlay.calibration.offset_y")
        self._overlay_text_scale_title.value = t("settings.overlay.calibration.text_scale")
        self._overlay_reset_title.value = t("settings.overlay.position_reset")
        self._set_unit_card_value_text(
            self._overlay_reset_button, t("settings.overlay.calibration.reset")
        )
        _set_text_button_label(self._reset_prompt_btn, t("settings.reset_prompt"))
        self._sync_prompt_tab_copy()
        self._custom_vocab_terms.label = None
        self._custom_vocab_terms.helper_text = ""

        # Update dynamic buttons by replacing the entire style object
        ui_font = font_for_language(get_locale())
        display_settings = self._build_settings_with_provider_draft()

        if self._reset_prompt_btn:
            self._reset_prompt_btn.style = self._get_button_style(ui_font)

        if self._qwen_region_btn:
            self._qwen_region_btn.style = self._get_button_style(ui_font)
        if self._openrouter_pkce_button:
            self._sync_openrouter_pkce_button_state(display_settings)
        self._sync_clickable_text_control_fonts(ui_font)
        for glyph_text in (
            getattr(self, "_overlay_distance_decrease_glyph", None),
            getattr(self, "_overlay_distance_increase_glyph", None),
            getattr(self, "_overlay_offset_x_decrease_glyph", None),
            getattr(self, "_overlay_offset_x_increase_glyph", None),
            getattr(self, "_overlay_offset_y_decrease_glyph", None),
            getattr(self, "_overlay_offset_y_increase_glyph", None),
        ):
            if glyph_text:
                glyph_text.font_family = ui_font
                glyph_text.size = 22
        # Update text controls with current selection labels
        if display_settings:
            self._set_unit_card_value_text(
                self._stt_text,
                provider_label(display_settings.provider.stt.value),
            )
            self._set_unit_card_value_text(
                self._peer_stt_text,
                provider_label(self._effective_peer_stt_provider(display_settings).value),
            )
            self._set_unit_card_value_text(
                self._llm_text,
                self._get_llm_display_label(display_settings),
            )
            self._set_openrouter_routing_text(
                self._get_openrouter_routing_display_label(display_settings),
            )
            self._sync_openrouter_fallback_card(display_settings)
            self._ui_text.content.value = locale_label(display_settings.ui.locale)
            self._low_latency_text.content.value = t(
                "toggle.on" if display_settings.stt.low_latency_mode else "toggle.off"
            )
            self._vrc_mic_text.content.value = t(
                "settings.vrc_mic.on"
                if display_settings.osc.vrc_mic_intercept
                else "settings.vrc_mic.off"
            )
            self._chatbox_source_text.content.value = t(
                "settings.chatbox_source.on"
                if display_settings.osc.chatbox_include_source
                else "settings.chatbox_source.off"
            )
            self._sync_overlay_controls()
            self._sync_overlay_calibration_controls()

        # Qwen Region label
        if display_settings:
            region_val = display_settings.qwen.region.value
            _set_text_button_label(
                self._qwen_region_btn,
                f"{t('settings.qwen_region')} {t(f'region.{region_val}')}",
            )

        # Components
        self._deepgram_key.apply_locale()
        self._soniox_key.apply_locale()
        self._google_key.apply_locale()
        self._managed_trial_usage_bar.apply_locale()
        self._openrouter_key.apply_locale()
        self._alibaba_key_beijing.apply_locale()
        self._alibaba_key_singapore.apply_locale()
        self._audio_settings.apply_locale()
        self._prompt_editor.apply_locale()

        if self.page:
            self.update()

    def refresh_prompt_if_empty(self) -> None:
        """Load default prompt if current is empty."""
        was_empty = not self._prompt_editor.value.strip()
        self._prompt_editor.load_default_if_empty()
        if was_empty and self._prompt_editor.value.strip():
            if self._prompt_editor.value != self._committed_prompt_value():
                self._stage_prompt_draft(self._prompt_editor.value)
